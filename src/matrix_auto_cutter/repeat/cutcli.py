"""Standalone CLI: ``python -m matrix_auto_cutter.repeat.cutcli``.

Re-encodes a finished, already-rendered source file with the "versprecher"
passages from an ``urteile.json`` cut out, in a single ffmpeg run
(``filter_complex`` trim/atrim + concat). This module is deliberately NOT
wired into ``cli.py`` -- it is a separate entry point for a separate stage
that runs on rendered output, not on raw recordings.

Encoder defaults below are read from (not imported from) the product
renderer's libx264 branch:
  src/matrix_auto_cutter/render.py:1271-1291 (video: codec/preset/crf/profile/pix_fmt)
  src/matrix_auto_cutter/render.py:1296-1298 (audio: codec/sample rate)
The renderer never passes ``-b:a`` for its aac audio stream, so the default
here is likewise "omit the flag" (aac's own default bitrate applies) unless
``--audio-bitrate`` is given explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from matrix_auto_cutter.repeat.audio import probe_duration_ms
from matrix_auto_cutter.repeat.cut import (
    CutIntegrityError,
    CutPlan,
    EmptyResultError,
    KeptSegment,
    SchnittWert,
    compute_cut_plan,
    resolve_schnitt,
)
from matrix_auto_cutter.repeat.errors import (
    FfmpegError,
    FfprobeError,
    ProcessTimeoutError,
    SourceNotFoundError,
)
from matrix_auto_cutter.repeat.process import NativeProcessRunner, ProcessRunner
from matrix_auto_cutter.repeat.snap import (
    DEFAULT_MIN_SILENCE_MS,
    DEFAULT_NOISE_DB,
    DEFAULT_WINDOW_MS,
    SilencePeriod,
    SnapResult,
    detect_silence,
    snap_point,
)

_PROBE_TIMEOUT_MS = 30_000
_ORPHAN_CHECK_TIMEOUT_MS = 10_000
_MIN_ENCODE_TIMEOUT_MS = 60_000
_ENCODE_TIMEOUT_FACTOR = 5
_DURATION_MISMATCH_WARNING_MS = 500
_MIN_SILENCE_DETECT_TIMEOUT_MS = 30_000
_SILENCE_DETECT_TIMEOUT_FACTOR = 2

_DEFAULT_VIDEO_CODEC = "libx264"
_DEFAULT_PRESET = "slow"
_DEFAULT_CRF = 18
_DEFAULT_PROFILE = "high"
_DEFAULT_PIX_FMT = "yuv420p"
_DEFAULT_AUDIO_CODEC = "aac"
_DEFAULT_AUDIO_SAMPLE_RATE = "48000"
_DEFAULT_AUDIO_BITRATE: str | None = None

_EXIT_OUT_EQUALS_SOURCE = 2
_EXIT_OUT_EXISTS = 3
_EXIT_SOURCE_NOT_FOUND = 4
_EXIT_FFPROBE_ERROR = 5
_EXIT_FFMPEG_ERROR = 6
_EXIT_PROCESS_TIMEOUT = 7
_EXIT_CUT_PLAN_ERROR = 8


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m matrix_auto_cutter.repeat.cutcli",
        description=(
            "Schneidet die per urteile.json als 'versprecher' markierten Passagen "
            "aus einer bereits fertig gerenderten Datei heraus."
        ),
    )
    parser.add_argument("--source", required=True, help="Die fertig gerenderte Quelldatei")
    parser.add_argument("--urteile", required=True, help="Pfad zur urteile.json")
    parser.add_argument("--out", required=True, help="Zielpfad der geschnittenen Datei")
    parser.add_argument(
        "--dry-run", action="store_true", help="Nur rechnen und berichten, nichts kodieren"
    )
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg-Binärname oder -Pfad")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe-Binärname oder -Pfad")
    parser.add_argument("--video-codec", default=_DEFAULT_VIDEO_CODEC)
    parser.add_argument("--crf", type=int, default=_DEFAULT_CRF)
    parser.add_argument("--preset", default=_DEFAULT_PRESET)
    parser.add_argument("--audio-bitrate", default=_DEFAULT_AUDIO_BITRATE)
    parser.add_argument(
        "--snap",
        dest="snap",
        action="store_true",
        help="Schnittpunkte an gemessene Stille heranruecken (Vorgabe: an)",
    )
    parser.add_argument(
        "--no-snap",
        dest="snap",
        action="store_false",
        help="Schnittpunkte nicht heranruecken -- Urteile unveraendert verwenden",
    )
    parser.set_defaults(snap=True)
    parser.add_argument("--snap-window-ms", type=int, default=DEFAULT_WINDOW_MS)
    parser.add_argument("--snap-noise-db", type=float, default=DEFAULT_NOISE_DB)
    parser.add_argument("--snap-min-silence-ms", type=int, default=DEFAULT_MIN_SILENCE_MS)
    return parser


def _seconds_arg(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def build_filtergraph(kept_segments: Sequence[KeptSegment]) -> str:
    """Build one ``filter_complex`` graph: per-segment trim/atrim, then a single concat."""
    parts: list[str] = []
    labels: list[str] = []
    for i, segment in enumerate(kept_segments):
        start = _seconds_arg(segment.start_ms)
        end = _seconds_arg(segment.end_ms)
        parts.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS[v{i}]")
        parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    concat = "".join(labels) + f"concat=n={len(kept_segments)}:v=1:a=1[vout][aout]"
    return ";".join([*parts, concat])


def build_ffmpeg_argv(
    ffmpeg_path: str,
    source: Path,
    out: Path,
    kept_segments: Sequence[KeptSegment],
    video_codec: str,
    crf: int,
    preset: str,
    audio_bitrate: str | None,
) -> list[str]:
    """Build the single-pass ffmpeg argv that trims, concats, and re-encodes in one run."""
    filtergraph = build_filtergraph(kept_segments)
    argv = [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-n",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        video_codec,
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-profile:v",
        _DEFAULT_PROFILE,
        "-pix_fmt",
        _DEFAULT_PIX_FMT,
        "-c:a",
        _DEFAULT_AUDIO_CODEC,
        "-ar",
        _DEFAULT_AUDIO_SAMPLE_RATE,
    ]
    if audio_bitrate is not None:
        argv += ["-b:a", audio_bitrate]
    argv += ["-movflags", "+faststart", str(out)]
    return argv


def _check_orphaned_ffmpeg(runner: ProcessRunner) -> list[str]:
    """Query already-running ffmpeg processes (Windows) over the same process seam."""
    argv = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-Process ffmpeg -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id",
    ]
    result = runner(argv, _ORPHAN_CHECK_TIMEOUT_MS)
    if result.exit_code != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _encode_timeout_ms(plan: CutPlan) -> int:
    return max(_MIN_ENCODE_TIMEOUT_MS, plan.duration_after_ms * _ENCODE_TIMEOUT_FACTOR)


def _silence_detect_timeout_ms(duration_ms: int) -> int:
    return max(_MIN_SILENCE_DETECT_TIMEOUT_MS, duration_ms * _SILENCE_DETECT_TIMEOUT_FACTOR)


def _format_hms(milliseconds: int) -> str:
    sign = "-" if milliseconds < 0 else ""
    total_ms = abs(milliseconds)
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    seconds, millis = divmod(rem_ms, 1000)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _resolve_snap_or_discard(
    start_result: SnapResult, end_result: SnapResult
) -> tuple[SnapResult, SnapResult, bool]:
    """Discard both snaps for one removal if they would cross (start >= end).

    A removal whose snapped start lands at or past its snapped end would
    have negative or zero duration, breaking the "kept + removed ==
    total" invariant compute_cut_plan enforces. The original boundary
    values are always valid (they came from the judgment itself), so
    falling back to them is always safe -- no partial snap, no swapping
    the boundaries.
    """
    if start_result.new_ms >= end_result.new_ms:
        discarded_start = SnapResult(
            start_result.original_ms, start_result.original_ms, 0, False, crossing_discarded=True
        )
        discarded_end = SnapResult(
            end_result.original_ms, end_result.original_ms, 0, False, crossing_discarded=True
        )
        return discarded_start, discarded_end, True
    return start_result, end_result, False


def snap_urteile(
    urteile: list[dict],
    periods: list[SilencePeriod],
    duration_ms: int,
    window_ms: int,
) -> tuple[list[dict], list[SnapResult]]:
    """Snap both removal boundaries of every "versprecher" urteil onto silence.

    Removals are built from the judgments exactly as ``cut.py`` builds them
    (via the public ``resolve_schnitt``); only the two removal-boundary
    fields are rewritten with their snapped values, so the unmodified
    ``compute_cut_plan`` merges the (now snapped) removals afterwards, never
    before -- snapping can push two previously separate removals into
    overlap, and merging first would have produced a duplicate cut.

    If snapping would cross a single removal's own boundaries, that
    removal's snap is discarded entirely (see ``_resolve_snap_or_discard``)
    and its original, unsnapped boundaries are kept.
    """
    adjusted: list[dict] = []
    results: list[SnapResult] = []
    for urteil in urteile:
        schnitt: SchnittWert | None = resolve_schnitt(urteil)
        if schnitt is None:
            adjusted.append(urteil)
            continue
        erste = dict(urteil["erste_passage"])
        zweite = dict(urteil["zweite_passage"])
        if schnitt == "erste":
            start_result = snap_point(erste["start_ms"], periods, duration_ms, window_ms)
            end_result = snap_point(erste["end_ms"], periods, duration_ms, window_ms)
            start_result, end_result, crossed = _resolve_snap_or_discard(start_result, end_result)
            if not crossed:
                erste["start_ms"] = start_result.new_ms
                erste["end_ms"] = end_result.new_ms
        elif schnitt == "zweite":
            start_result = snap_point(zweite["start_ms"], periods, duration_ms, window_ms)
            end_result = snap_point(zweite["end_ms"], periods, duration_ms, window_ms)
            start_result, end_result, crossed = _resolve_snap_or_discard(start_result, end_result)
            if not crossed:
                zweite["start_ms"] = start_result.new_ms
                zweite["end_ms"] = end_result.new_ms
        else:
            start_result = snap_point(erste["start_ms"], periods, duration_ms, window_ms)
            end_result = snap_point(zweite["end_ms"], periods, duration_ms, window_ms)
            start_result, end_result, crossed = _resolve_snap_or_discard(start_result, end_result)
            if not crossed:
                erste["start_ms"] = start_result.new_ms
                zweite["end_ms"] = end_result.new_ms
        results.append(start_result)
        results.append(end_result)
        new_urteil = dict(urteil)
        new_urteil["erste_passage"] = erste
        new_urteil["zweite_passage"] = zweite
        adjusted.append(new_urteil)
    return adjusted, results


def _report_snap_results(results: list[SnapResult]) -> None:
    snapped_count = 0
    crossing_discarded_count = 0
    max_shift_ms = 0
    for result in results:
        if result.crossing_discarded:
            crossing_discarded_count += 1
            print(
                f"Original {_format_hms(result.original_ms)} Heranruecken verworfen (Ueberkreuzung)"
            )
        elif result.snapped:
            snapped_count += 1
            max_shift_ms = max(max_shift_ms, abs(result.shift_ms))
            print(
                f"Original {_format_hms(result.original_ms)} -> "
                f"Neu {_format_hms(result.new_ms)}, "
                f"Verschiebung {result.shift_ms:+d} ms"
            )
        else:
            print(
                f"Original {_format_hms(result.original_ms)} "
                "nicht herangerueckt (keine Stille im Fenster)"
            )
    not_snapped_count = len(results) - snapped_count - crossing_discarded_count
    print(
        f"Herangerueckt: {snapped_count}, nicht herangerueckt: {not_snapped_count}, "
        f"verworfen (Ueberkreuzung): {crossing_discarded_count}, "
        f"groesste Verschiebung: {max_shift_ms} ms"
    )


def _report(plan: CutPlan, argv_ffmpeg: Sequence[str], args: argparse.Namespace) -> None:
    audio_bitrate = args.audio_bitrate if args.audio_bitrate is not None else "(Encoder-Default)"
    print(f"Anzahl Stellen (behaltene Segmente): {len(plan.kept_segments)}")
    print(f"Anzahl Schnitte vor Zusammenfuehrung: {plan.cut_count_before_merge}")
    print(f"Anzahl Schnitte nach Zusammenfuehrung: {plan.cut_count}")
    print(f"Entfernte Dauer: {plan.removed_duration_ms} ms")
    print(f"Dauer vorher: {plan.duration_before_ms} ms")
    print(f"Dauer nachher: {plan.duration_after_ms} ms")
    print(
        "Encoder-Parameter: "
        f"video-codec={args.video_codec} preset={args.preset} crf={args.crf} "
        f"profile={_DEFAULT_PROFILE} pix_fmt={_DEFAULT_PIX_FMT} "
        f"audio-codec={_DEFAULT_AUDIO_CODEC} audio-rate={_DEFAULT_AUDIO_SAMPLE_RATE} "
        f"audio-bitrate={audio_bitrate}"
    )
    print("ffmpeg-Befehlszeile: " + " ".join(argv_ffmpeg))


def main(argv: Sequence[str] | None = None) -> int:
    """Compute the cut plan, report it, and -- unless ``--dry-run`` -- encode it."""
    parser = _parser()
    args = parser.parse_args(argv)

    source = Path(args.source).resolve()
    out = Path(args.out).resolve()
    if out == source:
        print("Fehler: --out darf nicht auf --source zeigen.", file=sys.stderr)
        return _EXIT_OUT_EQUALS_SOURCE
    if out.exists():
        print(f"Fehler: --out existiert bereits: {out}", file=sys.stderr)
        return _EXIT_OUT_EXISTS

    runner = NativeProcessRunner()
    try:
        duration_ms = probe_duration_ms(str(source), args.ffprobe, runner, _PROBE_TIMEOUT_MS)
    except SourceNotFoundError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_SOURCE_NOT_FOUND
    except FfprobeError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_FFPROBE_ERROR
    except ProcessTimeoutError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_PROCESS_TIMEOUT

    urteile = json.loads(Path(args.urteile).read_text(encoding="utf-8"))

    snap_results: list[SnapResult] = []
    if args.snap:
        # source's existence was already confirmed above by probe_duration_ms,
        # so detect_silence's own SourceNotFoundError check cannot trigger here.
        try:
            periods = detect_silence(
                source,
                args.ffmpeg,
                runner,
                _silence_detect_timeout_ms(duration_ms),
                duration_ms,
                args.snap_noise_db,
                args.snap_min_silence_ms,
            )
        except FfmpegError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return _EXIT_FFMPEG_ERROR
        except ProcessTimeoutError as exc:
            print(f"Fehler: {exc}", file=sys.stderr)
            return _EXIT_PROCESS_TIMEOUT
        urteile, snap_results = snap_urteile(urteile, periods, duration_ms, args.snap_window_ms)

    try:
        plan = compute_cut_plan(urteile, duration_ms)
    except (EmptyResultError, CutIntegrityError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_CUT_PLAN_ERROR

    argv_ffmpeg = build_ffmpeg_argv(
        args.ffmpeg,
        source,
        out,
        plan.kept_segments,
        args.video_codec,
        args.crf,
        args.preset,
        args.audio_bitrate,
    )
    if args.snap:
        _report_snap_results(snap_results)
    _report(plan, argv_ffmpeg, args)

    if args.dry_run:
        return 0

    orphans = _check_orphaned_ffmpeg(runner)
    if orphans:
        print(
            f"Warnung: verwaiste ffmpeg-Prozesse gefunden (PID {', '.join(orphans)}).",
            file=sys.stderr,
        )

    result = runner(argv_ffmpeg, _encode_timeout_ms(plan))
    if result.timed_out:
        exc = ProcessTimeoutError(
            "ffmpeg", _encode_timeout_ms(plan), result.exit_code, result.stderr
        )
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_PROCESS_TIMEOUT
    if result.exit_code != 0:
        exc = FfmpegError(result.exit_code, result.stderr)
        print(f"Fehler: {exc}", file=sys.stderr)
        return _EXIT_FFMPEG_ERROR

    try:
        actual_duration_ms = probe_duration_ms(str(out), args.ffprobe, runner, _PROBE_TIMEOUT_MS)
    except (FfprobeError, SourceNotFoundError, ProcessTimeoutError) as exc:
        print(f"Warnung: Nachpruefung der Zieldatei fehlgeschlagen: {exc}", file=sys.stderr)
        return 0
    diff_ms = abs(actual_duration_ms - plan.duration_after_ms)
    if diff_ms > _DURATION_MISMATCH_WARNING_MS:
        print(
            f"Warnung: Zieldauer weicht um {diff_ms} ms von der berechneten Dauer ab "
            f"(erwartet {plan.duration_after_ms} ms, gemessen {actual_duration_ms} ms).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
