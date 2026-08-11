r"""Stufe 1: die Avatardatei anhand des Bildschirm-Proposals frameexakt nachschneiden.

Nimmt eine ``shorts-job.json`` (Stufe 0) entgegen, lädt und prüft das
referenzierte Proposal über :mod:`matrix_auto_cutter.cut_proposal` und
:mod:`matrix_auto_cutter.approval` (keine zweite Kopie der Schemalogik hier),
misst den Tonabgleich-Lag ``L`` zwischen Bildschirm- und Avataraufnahme
selbst (:mod:`matrix_auto_cutter.shorts.avatar_lag`, Auftrag 11 Eingriff 1),
verschiebt die freigegebenen Schnitte um ``|L|`` Frames auf die Avatarachse
(siehe :mod:`matrix_auto_cutter.shorts.avatar_axis`) und schneidet die
Avatardatei per ffmpeg neu zusammen - frameexakt, neu kodiert, 60 fps
erhalten.

``--lag-ms`` bleibt als Übersteuerung der Messung erhalten - für Tests und
für Läufe, deren Messung scheitert.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from matrix_auto_cutter.approval import (
    ApprovalGateResult,
    SelectiveProposalApproval,
    check_render_authorization,
)
from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.cut_proposal import CutProposal, discover_ffmpeg
from matrix_auto_cutter.shorts.avatar_axis import (
    RenderedAxisCoverage,
    TrailingEdgeFinding,
    rendered_axis_coverage,
    shift_intervals_to_avatar_axis,
    trailing_edge_finding,
)
from matrix_auto_cutter.shorts.avatar_lag import LagMeasurementFailed, measure_lag
from matrix_auto_cutter.shorts.frame_map import (
    KeepSegment,
    effective_cuts,
    keep_segments_from_intervals,
)
from matrix_auto_cutter.shorts.inventory import discover_ffprobe

AVATAR_CUT_VIDEO_NAME = "avatar-cut.mp4"
AVATAR_CUT_REPORT_NAME = "avatar-cut.json"
AVATAR_CUT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded ffmpeg/ffprobe-Prozessausgang - eigenständig, analog zu ``cut_proposal``."""

    exit_code: int
    stderr: bytes


ProcessRunner = Callable[[Sequence[str], int], ProcessResult]


def _default_process_runner(arguments: Sequence[str], timeout_seconds: int) -> ProcessResult:
    """Führe ein Kommando mit begrenzter Diagnoseausgabe aus - der reale Standardweg."""
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessResult(-1, str(exc).encode("utf-8", errors="replace"))
    return ProcessResult(result.returncode, result.stdout or b"")


@dataclass(frozen=True, slots=True)
class LagInput:
    """Der für die Planung verwendete Tonabgleich-Lag, samt Herkunft.

    ``lag_ms`` folgt der Konvention aus ``audio_offset_check.py``/
    :mod:`matrix_auto_cutter.shorts.avatar_lag`: negativ, weil die
    Avataraufnahme in jedem bisherigen Lauf später beginnt als die
    Bildschirmaufnahme. ``method`` unterscheidet eine selbst gemessene
    Auskunft (``"measured"``) von einer per ``--lag-ms`` übersteuerten
    (``"override"``) - beide landen in ``avatar-cut.json``, damit
    nachvollziehbar bleibt, welcher Weg es war. ``peak_ratio`` ist nur bei
    ``"measured"`` gesetzt.
    """

    lag_ms: float
    source: str
    method: Literal["measured", "override"] = "override"
    peak_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class LagMeasurementUnavailable:
    """Fail-closed Auskunft, wenn die Selbstmessung des Lags scheiterte."""

    reason: str


def measure_lag_input(
    *,
    ffmpeg_path: Path,
    screen_path: Path,
    avatar_path: Path,
) -> LagInput | LagMeasurementUnavailable:
    """Miss den Lag selbst (Auftrag 11, Eingriff 1) - kein Rückfall auf null.

    Scheitert die Messung (kein Ton, zu kurzes Fenster, kein eindeutiger
    Spitzenwert), wird das gemeldet statt eines geschätzten Werts.
    """
    result = measure_lag(ffmpeg_path, screen_path, avatar_path)
    if isinstance(result, LagMeasurementFailed):
        return LagMeasurementUnavailable(result.reason)
    return LagInput(
        lag_ms=result.lag_ms,
        source=f"gemessen: {screen_path.name} <-> {avatar_path.name}",
        method="measured",
        peak_ratio=result.peak_ratio,
    )


def lag_frames_from_ms(lag_ms: float, *, fps_num: int, fps_den: int) -> int:
    """Rechne den gemessenen Lag in eine nichtnegative Framezahl |L| um.

    Erzwingt die Vorzeichenkonvention: ``lag_ms`` muss <= 0 sein (Avatar
    beginnt später oder zeitgleich). Ein positiver Wert ist ein Befund gegen
    die dokumentierte Konvention und wird nicht stillschweigend übernommen.
    """
    if lag_ms > 0:
        raise ValueError(
            "lag_ms ist positiv - das widerspricht der dokumentierten Konvention "
            "(Avatar beginnt stets später oder zeitgleich); Messung prüfen statt anwenden"
        )
    frames = round(-lag_ms * fps_num / (fps_den * 1000.0))
    return int(frames)


@dataclass(frozen=True, slots=True)
class AvatarCutPlan:
    """Vollständig berechneter Schneideplan, bevor irgendein ffmpeg läuft."""

    lag_ms: float
    lag_frames: int
    lag_method: Literal["measured", "override"]
    lag_peak_ratio: float | None
    fps_num: int
    fps_den: int
    source_frame_count: int
    avatar_frame_count: int
    applied_cut_count: int
    avatar_cut_intervals: tuple[tuple[int, int], ...]
    avatar_keep_segments: tuple[KeepSegment, ...]
    expected_output_frame_count: int
    coverage: RenderedAxisCoverage
    trailing_edge: TrailingEdgeFinding


@dataclass(frozen=True, slots=True)
class PlanFailed:
    """Fail-closed Auskunft, warum kein Plan gebaut werden konnte."""

    code: str
    message_de: str


def build_avatar_cut_plan(
    proposal: CutProposal,
    *,
    active_candidate_ids: Sequence[str] | None,
    lag: LagInput,
    avatar_frame_count: int,
) -> AvatarCutPlan | PlanFailed:
    """Reine Planung: aus Proposal + Lag + Avatar-Framezahl den Schneideplan bauen."""
    if avatar_frame_count <= 0:
        return PlanFailed("invalid_avatar_frame_count", "Avatar-Framezahl muss positiv sein")
    if proposal.status != "ready" or not proposal.proposed_cuts:
        return PlanFailed(
            "no_cuts_in_proposal", "Proposal hat keine Schnitte - nichts zum Verschieben"
        )
    fps_num = proposal.analysis_parameters.fps_num
    fps_den = proposal.analysis_parameters.fps_den
    try:
        lag_frames = lag_frames_from_ms(lag.lag_ms, fps_num=fps_num, fps_den=fps_den)
    except ValueError as exc:
        return PlanFailed("lag_sign_violation", str(exc))

    cuts = effective_cuts(proposal.proposed_cuts, active_candidate_ids)
    if not cuts:
        return PlanFailed("no_active_cuts", "Keine der freigegebenen Schnittkandidaten ist aktiv")
    screen_intervals = tuple((cut.start_frame, cut.end_frame) for cut in cuts)
    try:
        screen_keep = keep_segments_from_intervals(screen_intervals, proposal.source_frame_count)
    except ValueError as exc:
        return PlanFailed("invalid_screen_intervals", str(exc))

    avatar_intervals = shift_intervals_to_avatar_axis(
        screen_intervals, lag_frames=lag_frames, avatar_frame_count=avatar_frame_count
    )
    avatar_keep: tuple[KeepSegment, ...]
    if not avatar_intervals:
        avatar_keep = (KeepSegment(0, avatar_frame_count),)
    else:
        avatar_keep = keep_segments_from_intervals(avatar_intervals, avatar_frame_count)
    if not avatar_keep:
        return PlanFailed(
            "avatar_fully_cut", "Alle Avatarframes liegen in einem Schnitt - nichts bliebe übrig"
        )

    trailing = trailing_edge_finding(
        source_frame_count=proposal.source_frame_count,
        lag_frames=lag_frames,
        avatar_frame_count=avatar_frame_count,
    )
    try:
        coverage = rendered_axis_coverage(
            screen_keep,
            lag_frames=lag_frames,
            avatar_frame_count=avatar_frame_count,
        )
    except ValueError as exc:
        return PlanFailed("coverage_inconsistent", str(exc))
    return AvatarCutPlan(
        lag_ms=lag.lag_ms,
        lag_frames=lag_frames,
        lag_method=lag.method,
        lag_peak_ratio=lag.peak_ratio,
        fps_num=fps_num,
        fps_den=fps_den,
        source_frame_count=proposal.source_frame_count,
        avatar_frame_count=avatar_frame_count,
        applied_cut_count=len(avatar_intervals),
        avatar_cut_intervals=avatar_intervals,
        avatar_keep_segments=avatar_keep,
        expected_output_frame_count=sum(segment.length for segment in avatar_keep),
        coverage=coverage,
        trailing_edge=trailing,
    )


def authorize_and_load_proposal(proposal_path: Path) -> ApprovalGateResult:
    """Lade und prüfe ein Proposal ausschließlich über die vorhandene Freigabeprüfung."""
    return check_render_authorization(proposal_path)


def _active_candidate_ids(gate: ApprovalGateResult) -> Sequence[str] | None:
    """Nur bei ``selected_cuts_approved`` gibt es eine echte Teilmenge - sonst gilt alles."""
    if isinstance(gate.approval, SelectiveProposalApproval):
        return gate.approval.active_candidate_ids
    return None


def build_ffmpeg_filter_complex(
    segments: Sequence[KeepSegment], *, fps_num: int, fps_den: int
) -> tuple[str, str, str]:
    """Baue den ``-filter_complex``-Ausdruck und die Ausgabelabels für den Schnitt.

    Frameexakt für Video (``trim=start_frame:end_frame``), sekundengenau
    berechnet aus der Framezahl für Audio (``atrim``) - Audio kennt keine
    Frames. Neu kodiert, nicht kopiert: nur so lässt sich an beliebigen
    Stellen schneiden, nicht nur an Keyframes.
    """
    if not segments:
        raise ValueError("mindestens ein Keep-Segment wird benötigt")
    fps = fps_num / fps_den
    video_parts: list[str] = []
    audio_parts: list[str] = []
    for index, segment in enumerate(segments):
        start_s = segment.start_frame / fps
        end_s = segment.end_frame / fps
        video_parts.append(
            f"[0:v]trim=start_frame={segment.start_frame}:end_frame={segment.end_frame},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
        audio_parts.append(
            f"[0:a]atrim=start={start_s:.9f}:end={end_s:.9f},asetpts=PTS-STARTPTS[a{index}]"
        )
    if len(segments) == 1:
        filter_complex = ";".join([video_parts[0], audio_parts[0]])
        return filter_complex, "[v0]", "[a0]"
    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    concat = f"{concat_inputs}concat=n={len(segments)}:v=1:a=1[outv][outa]"
    filter_complex = ";".join([*video_parts, *audio_parts, concat])
    return filter_complex, "[outv]", "[outa]"


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    avatar_path: Path,
    output_path: Path,
    plan: AvatarCutPlan,
) -> list[str]:
    """Vollständiges ffmpeg-Kommando für den Avatar-Nachschnitt."""
    filter_complex, video_label, audio_label = build_ffmpeg_filter_complex(
        plan.avatar_keep_segments, fps_num=plan.fps_num, fps_den=plan.fps_den
    )
    fps = plan.fps_num / plan.fps_den
    return [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(avatar_path),
        "-filter_complex",
        filter_complex,
        "-map",
        video_label,
        "-map",
        audio_label,
        "-r",
        f"{fps:g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]


def probe_frame_count(
    video_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 120,
) -> int | None:
    """Zähle die Videoframes exakt (``-count_frames``); ``None`` bei Fehlern."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="replace").strip()
    try:
        return int(text)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class AvatarCutResult:
    """Ergebnis eines abgeschlossenen Laufs, wie es auch in ``avatar-cut.json`` steht."""

    status: Literal["written", "failed"]
    plan: AvatarCutPlan | None
    output_video_path: str | None
    output_report_path: str
    actual_output_frame_count: int | None
    error: str | None = None


def write_avatar_cut_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe ``avatar-cut.json`` atomar - dasselbe Muster wie ``shorts-job.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.tmp.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, path, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def plan_report_payload(
    plan: AvatarCutPlan, *, actual_output_frame_count: int | None
) -> dict[str, object]:
    """Baue den JSON-Inhalt von ``avatar-cut.json`` aus einem abgeschlossenen Plan."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_avatar_cut",
        "schema_version": AVATAR_CUT_SCHEMA_VERSION,
        "lag": {
            "ms": plan.lag_ms,
            "frames": plan.lag_frames,
            "method": plan.lag_method,
            "peak_ratio": plan.lag_peak_ratio,
        },
        "applied_cut_count": plan.applied_cut_count,
        "source_frame_count": plan.source_frame_count,
        "avatar_frame_count_before": plan.avatar_frame_count,
        "expected_output_frame_count": plan.expected_output_frame_count,
        "actual_output_frame_count": actual_output_frame_count,
        "coverage": {
            "first_rendered_frame": plan.coverage.first_rendered_frame,
            "last_rendered_frame": plan.coverage.last_rendered_frame,
            "missing_frames_front": plan.coverage.missing_frames_front,
            "missing_frames_back": plan.coverage.missing_frames_back,
        },
        "trailing_edge": {
            "missing_frames": plan.trailing_edge.missing_frames,
        },
    }


def run_avatar_cut(
    *,
    avatar_path: Path,
    output_path: Path,
    plan: AvatarCutPlan,
    ffmpeg_path: Path,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult:
    """Führe den geplanten Schnitt tatsächlich per ffmpeg aus."""
    arguments = build_ffmpeg_arguments(ffmpeg_path, avatar_path, output_path, plan)
    return process_runner(arguments, timeout_seconds)


def execute_avatar_cut(
    *,
    avatar_path: Path,
    output_video_path: Path,
    output_report_path: Path,
    plan: AvatarCutPlan,
    ffmpeg_path: Path,
    process_runner: ProcessRunner = _default_process_runner,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 1800,
) -> AvatarCutResult:
    """Schneide, prüfe die tatsächliche Framezahl, schreibe ``avatar-cut.json`` atomar.

    Schreibt den Bericht auch bei einem gescheiterten ffmpeg-Lauf, damit
    fehlgeschlagene Läufe nachvollziehbar bleiben. Legt das Zielverzeichnis
    an, bevor ffmpeg startet (Auftrag 12, Punkt 2) - vorher scheiterte ein
    Lauf in ein noch nicht existierendes Verzeichnis mit "No such file or
    directory".
    """
    output_video_path.parent.mkdir(parents=True, exist_ok=True)
    process_result = run_avatar_cut(
        avatar_path=avatar_path,
        output_path=output_video_path,
        plan=plan,
        ffmpeg_path=ffmpeg_path,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    if process_result.exit_code != 0:
        payload = plan_report_payload(plan, actual_output_frame_count=None)
        payload["error"] = process_result.stderr.decode("utf-8", errors="replace")[-2000:]
        write_avatar_cut_report(output_report_path, payload)
        return AvatarCutResult(
            status="failed",
            plan=plan,
            output_video_path=None,
            output_report_path=str(output_report_path),
            actual_output_frame_count=None,
            error=payload["error"],  # type: ignore[arg-type]
        )
    actual_frames = probe_frame_count(
        output_video_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    payload = plan_report_payload(plan, actual_output_frame_count=actual_frames)
    write_avatar_cut_report(output_report_path, payload)
    return AvatarCutResult(
        status="written",
        plan=plan,
        output_video_path=str(output_video_path),
        output_report_path=str(output_report_path),
        actual_output_frame_count=actual_frames,
    )


@dataclass(frozen=True, slots=True)
class Stage1Failed:
    """Fail-closed Auskunft für den ganzen Stufe-1-Lauf, vor jedem ffmpeg-Aufruf."""

    code: str
    message_de: str


def run_stage1_for_job(
    job_path: Path,
    *,
    lag: LagInput | None = None,
    output_root: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> AvatarCutResult | Stage1Failed:
    """Ende-zu-Ende: ``shorts-job.json`` lesen, Proposal prüfen, Avatardatei schneiden.

    Lädt und prüft das Proposal ausschließlich über
    :func:`authorize_and_load_proposal` (``approval.check_render_authorization``)
    - dieselbe Freigabeprüfung, die auch der Renderer verwendet. Nur die
    tatsächlich freigegebene Teilmenge der Schnitte wird angewendet.

    Ist ``lag`` ``None`` (Normalfall, Auftrag 11 Eingriff 1), wird der Lag
    selbst gemessen (Bildschirmspur aus dem Proposal-``source_path``, gegen
    die Avatardatei) statt einen Wert entgegenzunehmen. ``--lag-ms`` bleibt
    als Übersteuerung möglich - dann wird nicht gemessen.
    """
    try:
        job_payload = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Stage1Failed("job_unreadable", str(exc))
    proposal_path_str = job_payload.get("proposal", {}).get("path")
    if not proposal_path_str:
        return Stage1Failed("no_proposal", "shorts-job.json referenziert kein Proposal")
    avatar_path_str = job_payload.get("avatar", {}).get("path")
    if not avatar_path_str:
        return Stage1Failed("no_avatar", "shorts-job.json referenziert keine Avatardatei")
    avatar_path = Path(avatar_path_str)
    if not avatar_path.is_file():
        return Stage1Failed("avatar_missing", f"Avatardatei fehlt: {avatar_path}")

    gate = authorize_and_load_proposal(Path(proposal_path_str))
    if not gate.authorized or gate.proposal is None:
        return Stage1Failed("not_authorized", gate.reason)

    if lag is None:
        measured = measure_lag_input(
            ffmpeg_path=ffmpeg_path,
            screen_path=Path(gate.proposal.source_path),
            avatar_path=avatar_path,
        )
        if isinstance(measured, LagMeasurementUnavailable):
            return Stage1Failed("lag_measurement_failed", measured.reason)
        lag = measured

    avatar_frame_count = probe_frame_count(
        avatar_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if avatar_frame_count is None:
        return Stage1Failed(
            "avatar_frame_count_unknown", "ffprobe konnte die Avatar-Framezahl nicht ermitteln"
        )

    plan = build_avatar_cut_plan(
        gate.proposal,
        active_candidate_ids=_active_candidate_ids(gate),
        lag=lag,
        avatar_frame_count=avatar_frame_count,
    )
    if isinstance(plan, PlanFailed):
        return Stage1Failed(plan.code, plan.message_de)

    video_name = job_payload.get("video_name")
    target_dir = output_root / str(video_name)
    return execute_avatar_cut(
        avatar_path=avatar_path,
        output_video_path=target_dir / AVATAR_CUT_VIDEO_NAME,
        output_report_path=target_dir / AVATAR_CUT_REPORT_NAME,
        plan=plan,
        ffmpeg_path=ffmpeg_path,
        process_runner=process_runner,
        ffprobe_path=ffprobe_path,
        timeout_seconds=timeout_seconds,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    process_runner: ProcessRunner | None = None,
) -> int:
    """CLI für einen einzelnen Stufe-1-Lauf: ``shorts-job.json``, Lag optional.

    Ohne ``--lag-ms`` misst der Lauf den Lag selbst (Auftrag 11, Eingriff 1).
    ``--lag-ms`` bleibt als Übersteuerung für Tests und für Läufe, deren
    Messung scheitert. ``process_runner`` ist nur für Tests gedacht - ohne
    ihn läuft echtes ffmpeg über :func:`_default_process_runner`.
    """
    import argparse

    from matrix_auto_cutter.shorts.app import JOBS_ROOT

    parser = argparse.ArgumentParser(description="Avatardatei anhand eines Proposals nachschneiden")
    parser.add_argument("job_path", type=Path)
    parser.add_argument("--lag-ms", type=float, default=None)
    parser.add_argument("--lag-source", type=str, default="cli")
    parser.add_argument("--output-root", type=Path, default=JOBS_ROOT)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    lag = (
        LagInput(lag_ms=args.lag_ms, source=args.lag_source, method="override")
        if args.lag_ms is not None
        else None
    )
    result = run_stage1_for_job(
        args.job_path,
        lag=lag,
        output_root=args.output_root,
        ffmpeg_path=ffmpeg_path,
        process_runner=process_runner or _default_process_runner,
    )
    if isinstance(result, Stage1Failed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.status == "failed":
        print(f"ffmpeg fehlgeschlagen: {result.error}")
        return 1
    print(f"geschrieben: {result.output_video_path}")
    print(f"bericht: {result.output_report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
