"""CLI entry point: ``python -m matrix_auto_cutter.repeat.cli``. No interaction."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from matrix_auto_cutter.repeat.asr import DEFAULT_MAX_SEGMENT_LEN, DEFAULT_THREADS, run_whisper
from matrix_auto_cutter.repeat.audio import extract_audio, probe_duration_ms
from matrix_auto_cutter.repeat.boundary import (
    DEFAULT_MAX_WINDOW_WORDS,
    DEFAULT_MIN_WINDOW_WORDS,
    DEFAULT_SCORE_THRESHOLD,
    BoundaryDetectionParams,
)
from matrix_auto_cutter.repeat.detect import DetectionParams
from matrix_auto_cutter.repeat.diagnostics import build_diagnostics, write_diagnostics
from matrix_auto_cutter.repeat.errors import (
    BinaryNotFoundError,
    FfmpegError,
    FfprobeError,
    ModelNotFoundError,
    ProcessTimeoutError,
    RawOutputEmptyError,
    RawOutputMissingError,
    RepeatContractError,
    SourceNotFoundError,
    WhisperError,
)
from matrix_auto_cutter.repeat.process import NativeProcessRunner
from matrix_auto_cutter.repeat.review import ReviewEntry, build_review_html
from matrix_auto_cutter.repeat.snippets import build_snippets, write_snippet_manifest
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument, load_transcript
from matrix_auto_cutter.repeat.whisper_json import convert_whisper_output

_PROBE_TIMEOUT_MS = 30_000
_AUDIO_STREAM_SPECIFIER = "0:a:0"

_EXIT_CODES: tuple[tuple[type[RepeatContractError], int], ...] = (
    (BinaryNotFoundError, 2),
    (ModelNotFoundError, 3),
    (SourceNotFoundError, 4),
    (FfprobeError, 5),
    (FfmpegError, 6),
    (WhisperError, 7),
    (ProcessTimeoutError, 8),
    (RawOutputMissingError, 9),
    (RawOutputEmptyError, 10),
)


def _exit_code_for(exc: RepeatContractError) -> int:
    for exc_type, code in _EXIT_CODES:
        if isinstance(exc, exc_type):
            return code
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m matrix_auto_cutter.repeat.cli",
        description=(
            "Erkenne benachbarte Wiederholungen und Selbstkorrekturen in einem Transkript."
        ),
    )
    parser.add_argument("--transcript", help="Pfad zur repeat_transcript/1.0-Datei")
    parser.add_argument(
        "--source",
        help=(
            "Pfad zur Audio-/Videoquelle. Ohne --transcript: wird transkribiert "
            "(whisper läuft). Zusammen mit --transcript: dient ausschließlich als "
            "Audioquelle für --snippet-dir/--emit-review, whisper läuft NICHT."
        ),
    )
    parser.add_argument("--out", required=True, help="Zielpfad der repeat_diagnostics/1.0-Datei")
    parser.add_argument(
        "--emit-transcript", help="Schreibt das konvertierte Transkript zusätzlich an diesen Pfad"
    )
    parser.add_argument("--whisper-binary", help="Pfad zu whisper-cli.exe (mit --source Pflicht)")
    parser.add_argument("--whisper-model", help="Pfad zum whisper-Modell (mit --source Pflicht)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg-Binärname oder -Pfad")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe-Binärname oder -Pfad")
    parser.add_argument("--work-dir", help="Arbeitsverzeichnis für WAV/JSON (mit --source Pflicht)")
    parser.add_argument("--window-start-ms", type=int, default=None)
    parser.add_argument("--window-end-ms", type=int, default=None)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument(
        "--max-segment-len",
        type=int,
        default=DEFAULT_MAX_SEGMENT_LEN,
        help="whisper-cli -ml (mit --source Pflicht); <= 0 unterdrückt das Flag",
    )
    parser.add_argument(
        "--boundary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Zweiter Detektor für kurze Echos an der Äußerungsgrenze (repeat_diagnostics/1.1)",
    )
    parser.add_argument(
        "--boundary-threshold",
        type=float,
        default=DEFAULT_SCORE_THRESHOLD,
        help="Score-Schwelle des Boundary-Detektors",
    )
    parser.add_argument(
        "--boundary-min-words",
        type=int,
        default=DEFAULT_MIN_WINDOW_WORDS,
        help="Kleinste Fensterbreite (Wörter) des Boundary-Detektors",
    )
    parser.add_argument(
        "--boundary-max-words",
        type=int,
        default=DEFAULT_MAX_WINDOW_WORDS,
        help="Größte Fensterbreite (Wörter) des Boundary-Detektors",
    )
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument(
        "--initial-prompt", help="Vokabular-Hinweis, wörtlich an whisper-cli übergeben"
    )
    prompt_group.add_argument(
        "--initial-prompt-file", help="Datei (UTF-8) mit dem Vokabular-Hinweis"
    )
    parser.add_argument(
        "--snippet-dir",
        help=(
            "Verzeichnis für Audio-Schnipsel (m4a) je Kandidat plus "
            "snippets.json (erfordert --source)"
        ),
    )
    parser.add_argument(
        "--emit-review",
        help=(
            "Schreibt eine eigenständige review.html mit eingebettetem "
            "Audio (erfordert --snippet-dir)"
        ),
    )
    return parser


def _resolve_initial_prompt(args: argparse.Namespace) -> str | None:
    if args.initial_prompt is not None:
        return str(args.initial_prompt)
    if args.initial_prompt_file is not None:
        text = Path(args.initial_prompt_file).read_text(encoding="utf-8").strip()
        return text if text else None
    return None


def _require_source_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    missing = [
        name
        for name, value in (
            ("--whisper-binary", args.whisper_binary),
            ("--whisper-model", args.whisper_model),
            ("--work-dir", args.work_dir),
        )
        if value is None
    ]
    if missing:
        parser.error(f"--source erfordert: {', '.join(missing)}")


def _validate_transcript_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Route transcript vs. source: whisper only runs when there is no transcript to reuse.

    whisper-cli is the expensive step (minutes per recording); reusing an
    already-transcribed ``repeat_transcript/1.0`` file is the normal case,
    not the exception. When ``--transcript`` is given, it always supplies the
    transcript and whisper is never invoked -- ``--source``, if also given,
    then serves only as the audio source for ``--snippet-dir``/``--emit-review``
    (ffmpeg slicing), not for transcription. ``--whisper-binary``/``--whisper-model``
    are meaningless in that combination and are ignored with a stderr warning
    rather than rejected, so an operator's leftover flags from a source-only
    invocation don't turn into a hard error.
    """
    if args.transcript is None and args.source is None:
        parser.error("einer von --transcript oder --source ist erforderlich")
    if args.transcript is None:
        _require_source_args(parser, args)
    elif args.source is not None and (
        args.whisper_binary is not None or args.whisper_model is not None
    ):
        print(
            "Warnung: --whisper-binary/--whisper-model werden ignoriert, "
            "da --transcript angegeben ist (kein whisper-Lauf).",
            file=sys.stderr,
        )


def _require_snippet_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.emit_review is not None and args.snippet_dir is None:
        parser.error("--emit-review erfordert --snippet-dir")
    if (args.snippet_dir is not None or args.emit_review is not None) and args.source is None:
        parser.error("--snippet-dir/--emit-review erfordern --source")


def _candidate_detectors(candidate: object) -> tuple[str, ...]:
    detector = getattr(candidate, "detector", None)
    if detector is None:
        return ("utterance",)
    if isinstance(detector, str):
        return (detector,)
    return tuple(detector)


def _candidate_scores(candidate: object) -> tuple[float | None, float | None]:
    utterance_score_obj = getattr(candidate, "utterance_score", None) or getattr(
        candidate, "scores", None
    )
    utterance_score = utterance_score_obj.total if utterance_score_obj is not None else None
    boundary_score = getattr(candidate, "boundary_score", None)
    return utterance_score, boundary_score


def _build_review_entries(
    candidates: tuple[object, ...],
    stem: str,
    source: str,
    manifest_entries: list,
) -> list[ReviewEntry]:
    manifest_by_nr = {entry.nr: entry for entry in manifest_entries}
    entries: list[ReviewEntry] = []
    for nr, candidate in enumerate(candidates, start=1):
        manifest_entry = manifest_by_nr.get(nr)
        audio_bytes: bytes | None = None
        audio_error: str | None = None
        if manifest_entry is not None and manifest_entry.path is not None:
            audio_bytes = Path(manifest_entry.path).read_bytes()
        elif manifest_entry is not None:
            audio_error = manifest_entry.error
        else:
            audio_error = "kein Schnipsel erzeugt"
        utterance_score, boundary_score = _candidate_scores(candidate)
        entries.append(
            ReviewEntry(
                stem=stem,
                nr=nr,
                source=source,
                first_text=candidate.first.text,
                first_start_ms=candidate.first.start_ms,
                first_end_ms=candidate.first.end_ms,
                second_text=candidate.second.text,
                second_start_ms=candidate.second.start_ms,
                second_end_ms=candidate.second.end_ms,
                detectors=_candidate_detectors(candidate),
                utterance_score=utterance_score,
                boundary_score=boundary_score,
                window_words=getattr(candidate, "window_words", None),
                first_window_text=getattr(candidate, "first_window_text", None),
                second_window_text=getattr(candidate, "second_window_text", None),
                audio_bytes=audio_bytes,
                audio_error=audio_error,
            )
        )
    return entries


def _emit_snippets_and_review(args: argparse.Namespace, document: object) -> None:
    stem = Path(args.source).stem
    manifest_entries: list = []
    if args.snippet_dir is not None:
        runner = NativeProcessRunner()
        source_duration_ms = probe_duration_ms(args.source, args.ffprobe, runner, _PROBE_TIMEOUT_MS)
        manifest_entries = build_snippets(
            candidates=list(document.candidates),
            stem=stem,
            source_path=args.source,
            source_duration_ms=source_duration_ms,
            ffmpeg_path=args.ffmpeg,
            snippet_dir=args.snippet_dir,
            runner=runner,
        )
        write_snippet_manifest(args.snippet_dir, manifest_entries)
    if args.emit_review is not None:
        entries = _build_review_entries(
            tuple(document.candidates), stem, str(args.source), manifest_entries
        )
        html_text = build_review_html(entries)
        Path(args.emit_review).write_text(html_text, encoding="utf-8")


def _transcribe_source(args: argparse.Namespace) -> RepeatTranscriptDocument:
    runner = NativeProcessRunner()
    source_duration_ms = probe_duration_ms(args.source, args.ffprobe, runner, _PROBE_TIMEOUT_MS)
    window_duration_ms = source_duration_ms
    if args.window_start_ms is not None and args.window_end_ms is not None:
        window_duration_ms = args.window_end_ms - args.window_start_ms
    ffmpeg_timeout_ms = max(_PROBE_TIMEOUT_MS, window_duration_ms * 2)
    wav_path = extract_audio(
        args.source,
        args.ffmpeg,
        args.work_dir,
        runner,
        ffmpeg_timeout_ms,
        window_start_ms=args.window_start_ms,
        window_end_ms=args.window_end_ms,
    )
    whisper_result = run_whisper(
        wav_path,
        args.whisper_binary,
        args.whisper_model,
        runner,
        audio_duration_ms=window_duration_ms,
        threads=args.threads,
        timeout_ms=args.timeout_ms,
        max_segment_len=args.max_segment_len,
        initial_prompt=_resolve_initial_prompt(args),
    )
    window_offset_ms = args.window_start_ms if args.window_start_ms is not None else 0
    return convert_whisper_output(
        whisper_result.raw_json,
        source_duration_ms=source_duration_ms,
        audio_stream_specifier=_AUDIO_STREAM_SPECIFIER,
        window_offset_ms=window_offset_ms,
    )


def _emit_transcript(path: str, transcript: RepeatTranscriptDocument) -> None:
    Path(path).write_text(transcript.model_dump_json(indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate/transcribe a source, detect adjacent repeats, and write diagnostics atomically."""
    parser = _parser()
    args = parser.parse_args(argv)
    _validate_transcript_source(args, parser)
    _require_snippet_args(parser, args)
    try:
        transcript = (
            load_transcript(args.transcript)
            if args.transcript is not None
            else _transcribe_source(args)
        )
        if args.emit_transcript is not None:
            # Written before diagnostics/boundary detection run: if a later step
            # raises, the already-converted transcript stays on disk regardless.
            _emit_transcript(args.emit_transcript, transcript)
        boundary_params = (
            BoundaryDetectionParams(
                score_threshold=args.boundary_threshold,
                min_window_words=args.boundary_min_words,
                max_window_words=args.boundary_max_words,
            )
            if args.boundary
            else None
        )
        document = build_diagnostics(transcript, DetectionParams(), boundary_params)
        result = write_diagnostics(args.out, document)
        if args.snippet_dir is not None or args.emit_review is not None:
            _emit_snippets_and_review(args, document)
    except RepeatContractError as exc:
        print(f"Vertragsfehler: {exc}", file=sys.stderr)
        return _exit_code_for(exc)
    if result.status != "written":
        print(f"Fehler beim Schreiben: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
