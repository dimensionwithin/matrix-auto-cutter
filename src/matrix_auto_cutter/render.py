"""Approval-gated, source-preserving rendering of one cut proposal."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from matrix_auto_cutter.approval import ProposalApproval, check_render_authorization
from matrix_auto_cutter.cut_proposal import (
    CutProposal,
    FfmpegIdentity,
    ProposalFailed,
    load_proposal,
    validate_ffmpeg,
)
from matrix_auto_cutter.models import CanonicalModel, Sha256

RENDER_REQUEST_FILE_NAME = "render-request.json"
RENDER_STATUS_FILE_NAME = "render-status.json"
RENDER_PLAN_FILE_NAME = "render-plan.json"
RENDER_RESULT_FILE_NAME = "render-result.json"
RENDER_SCHEMA_VERSION: Literal["1.0"] = "1.0"
DEFAULT_RENDER_DIRECTORY = Path(r"F:\MatrixMarketAutoEdit\Rendered")
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_PROCESS_OUTPUT_BYTES = 2 * 1024 * 1024
RENDER_TIMEOUT_SECONDS = 6 * 60 * 60
VERIFY_TIMEOUT_SECONDS = 30 * 60

RenderState = Literal[
    "render_not_authorized",
    "render_ready",
    "render_running",
    "render_verifying",
    "render_succeeded",
    "render_failed",
]


class KeepSegment(CanonicalModel):
    """One positive half-open frame range retained in the output."""

    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)

    @model_validator(mode="after")
    def positive(self) -> KeepSegment:
        """Reject zero or negative segments."""
        if self.start_frame >= self.end_frame:
            raise ValueError("keep segment requires start_frame < end_frame")
        return self


class StreamSelection(CanonicalModel):
    """The one unambiguous primary video/audio pair used by this renderer."""

    video_index: int = Field(ge=0)
    audio_index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps_num: Literal[60]
    fps_den: Literal[1]
    audio_sample_rate: int = Field(ge=1)


class OutputMediaProfile(CanonicalModel):
    """Observed technical output properties after rendering."""

    video_index: int = Field(ge=0)
    audio_index: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    fps_num: int = Field(ge=1)
    fps_den: int = Field(ge=1)
    audio_sample_rate: int = Field(ge=1)
    duration_ms: int = Field(ge=1)


class ApprovalBinding(CanonicalModel):
    """Materialized approval evidence used by one immutable plan."""

    proposal_id: str
    proposal_sha256: Sha256
    proposal_digest: Sha256
    source_identity_digest: Sha256
    recording_id: str
    sidecar_sha256: Sha256
    decision: Literal["approved"]
    approval_sha256: Sha256
    decided_at: AwareDatetime


class RenderRequest(CanonicalModel):
    """One deliberate UI request, not itself an authorization."""

    artifact_type: Literal["matrix_auto_cutter_render_request"]
    schema_version: Literal["1.0"]
    attempt_id: str = Field(pattern=r"^render-attempt-[0-9a-f]{32}$")
    recording_id: str
    proposal_id: str
    proposal_digest: Sha256
    proposal_sha256: Sha256
    source_identity_digest: Sha256
    sidecar_sha256: Sha256
    approval_sha256: Sha256
    approval_decided_at: AwareDatetime
    target_path: str = Field(min_length=1)
    requested_at: AwareDatetime


class RenderStatus(CanonicalModel):
    """Small polling artifact shared by runner and local review UI."""

    artifact_type: Literal["matrix_auto_cutter_render_status"]
    schema_version: Literal["1.0"]
    proposal_id: str
    state: RenderState
    message_de: str = Field(min_length=1, max_length=2000)
    updated_at: AwareDatetime
    attempt_id: str | None = None
    render_id: str | None = None
    target_path: str | None = None
    result_path: str | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    error_code: str | None = None


class RenderPlan(CanonicalModel):
    """Immutable, exact composition inputs and FFmpeg invocation."""

    artifact_type: Literal["matrix_auto_cutter_render_plan"]
    schema_version: Literal["1.0"]
    render_id: str = Field(pattern=r"^render-[0-9a-f]{32}$")
    attempt_id: str = Field(pattern=r"^render-attempt-[0-9a-f]{32}$")
    recording_id: str
    source_identity_digest: Sha256
    source_path: str
    proposal_id: str
    proposal_digest: Sha256
    proposal_sha256: Sha256
    sidecar_sha256: Sha256
    approval: ApprovalBinding
    keep_segments: tuple[KeepSegment, ...]
    source_frame_count: int = Field(ge=1)
    cut_frame_count: int = Field(ge=1)
    output_frame_count: int = Field(ge=1)
    expected_source_duration_ms: int = Field(ge=1)
    expected_cut_duration_ms: int = Field(ge=1)
    expected_output_duration_ms: int = Field(ge=1)
    ffmpeg: FfmpegIdentity
    encoder: Literal["h264_nvenc", "libx264"]
    streams: StreamSelection
    filtergraph: str = Field(min_length=1)
    filtergraph_sha256: Sha256
    arguments: tuple[str, ...]
    arguments_sha256: Sha256
    target_path: str
    partial_path: str
    created_at: AwareDatetime

    @model_validator(mode="after")
    def consistent(self) -> RenderPlan:
        """Reject hand-edited plans before they can be consumed."""
        if self.cut_frame_count + self.output_frame_count != self.source_frame_count:
            raise ValueError("render frame totals do not match")
        if self.filtergraph_sha256 != _text_digest(self.filtergraph):
            raise ValueError("filtergraph digest mismatch")
        if self.arguments_sha256 != _arguments_digest(self.arguments):
            raise ValueError("argument digest mismatch")
        if not self.keep_segments:
            raise ValueError("render plan requires keep segments")
        return self


class RenderResult(CanonicalModel):
    """Terminal, plan-bound outcome of exactly one render attempt."""

    artifact_type: Literal["matrix_auto_cutter_render_result"]
    schema_version: Literal["1.0"]
    render_id: str
    attempt_id: str
    plan_sha256: Sha256 | None = None
    status: Literal["succeeded", "failed", "interrupted"]
    started_at: AwareDatetime
    ended_at: AwareDatetime
    exit_code: int | None = None
    target_path: str
    output_sha256: Sha256 | None = None
    output_size_bytes: int | None = Field(default=None, ge=1)
    output_media_profile: OutputMediaProfile | None = None
    actual_duration_ms: int | None = Field(default=None, ge=1)
    expected_duration_ms: int | None = Field(default=None, ge=1)
    verification_status: Literal["not_run", "failed", "passed"]
    error_phase: (
        Literal["authorization", "planning", "render", "verification", "publication", "recovery"]
        | None
    ) = None
    error_code: str | None = None
    message_de: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True, slots=True)
class RenderAccepted:
    """A deliberate request was written atomically."""

    request: RenderRequest
    request_path: Path


@dataclass(frozen=True, slots=True)
class RenderFailed:
    """Stable failure that never represents a published output."""

    code: str
    message_de: str
    result: RenderResult | None = None
    result_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RenderSucceeded:
    """Verified final output and its persistent evidence."""

    plan: RenderPlan
    result: RenderResult
    plan_path: Path
    result_path: Path
    reused: bool = False


type RequestResult = RenderAccepted | RenderFailed
type RenderExecution = RenderSucceeded | RenderFailed
type StatusCallback = Callable[[RenderStatus], None]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded child-process result."""

    exit_code: int | None
    stdout: bytes = b""
    stderr: bytes = b""
    timed_out: bool = False
    cancelled: bool = False


class NativeProcessRunner:
    """Own and stop at most one child FFmpeg/ffprobe process at a time."""

    def __init__(self) -> None:
        """Initialize empty ownership state."""
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None

    def __call__(
        self,
        arguments: Sequence[str],
        timeout_seconds: int,
        cancellation: threading.Event,
    ) -> ProcessResult:
        """Execute an argument list without a shell and bound captured output."""
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = subprocess.Popen(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            creationflags=creation_flags,
            shell=False,
        )
        with self._lock:
            self._process = process
        try:
            if cancellation.is_set():
                process.kill()
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate(timeout=10)
                return ProcessResult(
                    process.returncode,
                    stdout[-MAX_PROCESS_OUTPUT_BYTES:],
                    stderr[-MAX_PROCESS_OUTPUT_BYTES:],
                    timed_out=True,
                )
            return ProcessResult(
                process.returncode,
                stdout[-MAX_PROCESS_OUTPUT_BYTES:],
                stderr[-MAX_PROCESS_OUTPUT_BYTES:],
                cancelled=cancellation.is_set(),
            )
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None

    def cancel(self) -> None:
        """Terminate only the currently owned process, if any."""
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            with suppress(OSError):
                process.kill()


type ProcessRunner = Callable[[Sequence[str], int, threading.Event], ProcessResult]


def render_request_path(proposal_path: Path) -> Path:
    """Return the sole request file for one proposal generation."""
    _require_proposal_name(proposal_path)
    return proposal_path.with_name(RENDER_REQUEST_FILE_NAME)


def render_status_path(proposal_path: Path) -> Path:
    """Return the one UI polling state for one proposal generation."""
    _require_proposal_name(proposal_path)
    return proposal_path.with_name(RENDER_STATUS_FILE_NAME)


def _require_proposal_name(path: Path) -> None:
    if path.name != "cut-proposal.json":
        raise ValueError("proposal path must end in cut-proposal.json")


def _canonical_bytes(model: CanonicalModel) -> bytes:
    return (model.model_dump_json() + "\n").encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _arguments_digest(arguments: Sequence[str]) -> str:
    return hashlib.sha256("\0".join(arguments).encode("utf-8")).hexdigest()


def _atomic_write(path: Path, data: bytes, *, replace: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
            return True
        try:
            os.rename(temporary, path)
            return True
        except FileExistsError:
            return False
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _load_model(path: Path, model_type: type[CanonicalModel]) -> CanonicalModel | None:
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_JSON_BYTES:
            return None
        model = model_type.model_validate_json(data)
        if data != _canonical_bytes(model):
            return None
        return model
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None


def load_render_request(proposal_path: Path) -> RenderRequest | None:
    """Strictly load the current deliberate request."""
    loaded = _load_model(render_request_path(proposal_path), RenderRequest)
    return loaded if isinstance(loaded, RenderRequest) else None


def load_render_status(proposal_path: Path) -> RenderStatus | None:
    """Strictly load the current UI status."""
    loaded = _load_model(render_status_path(proposal_path), RenderStatus)
    return loaded if isinstance(loaded, RenderStatus) else None


def write_render_status(proposal_path: Path, status: RenderStatus) -> None:
    """Atomically replace the UI status only on a material transition."""
    existing = load_render_status(proposal_path)
    if existing is not None and existing.model_dump(exclude={"updated_at"}) == status.model_dump(
        exclude={"updated_at"}
    ):
        return
    _atomic_write(render_status_path(proposal_path), _canonical_bytes(status), replace=True)


def target_path_for(proposal: CutProposal, target_directory: Path) -> Path:
    """Build the deterministic final name in a separate export directory."""
    return target_directory / f"{Path(proposal.source_path).stem}.matrix-cut.mp4"


def submit_render_request(
    proposal_path: Path,
    target_directory: Path = DEFAULT_RENDER_DIRECTORY,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    uuid_factory: Callable[[], UUID] = uuid4,
) -> RequestResult:
    """Create one deliberate request; authorization is checked again by the runner."""
    gate = check_render_authorization(proposal_path)
    if not gate.authorized or gate.proposal is None or gate.approval is None:
        return RenderFailed("E_RENDER_NOT_AUTHORIZED", gate.reason)
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return RenderFailed(loaded.code, loaded.message_de)
    proposal = loaded.proposal
    target = target_path_for(proposal, target_directory).resolve(strict=False)
    source = Path(proposal.source_path).resolve(strict=True)
    if os.path.normcase(str(target)) == os.path.normcase(str(source)):
        return RenderFailed("E_RENDER_TARGET_SOURCE", "Finales Ziel darf niemals die Source sein.")
    identifier = uuid_factory()
    if identifier.version != 4:
        return RenderFailed("E_RENDER_ATTEMPT_ID", "Render-Attempt-ID muss UUIDv4 sein.")
    request = RenderRequest(
        artifact_type="matrix_auto_cutter_render_request",
        schema_version=RENDER_SCHEMA_VERSION,
        attempt_id=f"render-attempt-{identifier.hex}",
        recording_id=proposal.recording_id,
        proposal_id=proposal.proposal_id,
        proposal_digest=proposal.proposal_digest,
        proposal_sha256=loaded.proposal_sha256,
        source_identity_digest=proposal.source_identity_digest,
        sidecar_sha256=proposal.sidecar_sha256,
        approval_sha256=_sha256(proposal_path.with_name("approval.json")),
        approval_decided_at=gate.approval.decided_at,
        target_path=str(target),
        requested_at=now(),
    )
    path = render_request_path(proposal_path)
    _atomic_write(path, _canonical_bytes(request), replace=True)
    observed = load_render_request(proposal_path)
    if observed != request:
        return RenderFailed(
            "E_RENDER_REQUEST_WRITE", "Renderauftrag wurde nicht identisch gespeichert."
        )
    return RenderAccepted(request, path)


def build_keep_segments(proposal: CutProposal) -> tuple[KeepSegment, ...]:
    """Return the exact complement of validated sorted half-open cuts."""
    if proposal.status != "ready" or not proposal.proposed_cuts:
        raise ValueError("approved proposal requires at least one cut")
    keeps: list[KeepSegment] = []
    cursor = 0
    minimum = max(1, (proposal.analysis_parameters.minimum_keep_island_ms * 60 + 999) // 1000)
    for cut in proposal.proposed_cuts:
        if cut.start_frame < cursor or cut.end_frame > proposal.source_frame_count:
            raise ValueError("cuts must be sorted, disjoint, and in bounds")
        if cut.start_frame > cursor:
            segment = KeepSegment(start_frame=cursor, end_frame=cut.start_frame)
            if segment.end_frame - segment.start_frame < minimum:
                raise ValueError("proposal would create an unintended micro keep segment")
            keeps.append(segment)
        cursor = cut.end_frame
    if cursor < proposal.source_frame_count:
        segment = KeepSegment(start_frame=cursor, end_frame=proposal.source_frame_count)
        if segment.end_frame - segment.start_frame < minimum:
            raise ValueError("proposal would create an unintended micro keep segment")
        keeps.append(segment)
    if not keeps:
        raise ValueError("proposal removes the complete source")
    return tuple(keeps)


def build_filtergraph(
    segments: Sequence[KeepSegment],
    *,
    video_index: int = 0,
    audio_index: int = 1,
) -> str:
    """Build one deterministic video/audio trim and concat graph."""
    if not segments:
        raise ValueError("at least one keep segment is required")
    chains: list[str] = []
    inputs: list[str] = []
    for index, segment in enumerate(segments):
        start = Decimal(segment.start_frame) / Decimal(60)
        end = Decimal(segment.end_frame) / Decimal(60)
        start_text = format(start.quantize(Decimal("0.000000001")), "f")
        end_text = format(end.quantize(Decimal("0.000000001")), "f")
        chains.append(
            f"[0:{video_index}]trim=start={start_text}:end={end_text},setpts=PTS-STARTPTS[v{index}]"
        )
        chains.append(
            f"[0:{audio_index}]atrim=start={start_text}:end={end_text},"
            f"asetpts=PTS-STARTPTS[a{index}]"
        )
        inputs.append(f"[v{index}][a{index}]")
    chains.append(f"{''.join(inputs)}concat=n={len(segments)}:v=1:a=1[vout][aout]")
    return ";".join(chains)


def _parse_rate(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    if rate <= 0:
        return None
    return rate.numerator, rate.denominator


def _probe_profile(payload: bytes) -> OutputMediaProfile | None:
    try:
        document = json.loads(payload)
        streams = document["streams"]
        duration = document["format"]["duration"]
        if not isinstance(streams, list):
            return None
        videos = [item for item in streams if item.get("codec_type") == "video"]
        audios = [item for item in streams if item.get("codec_type") == "audio"]
        if len(videos) != 1 or len(audios) != 1:
            return None
        video, audio = videos[0], audios[0]
        rate = _parse_rate(video.get("avg_frame_rate"))
        if rate is None:
            return None
        return OutputMediaProfile(
            video_index=int(video["index"]),
            audio_index=int(audio["index"]),
            width=int(video["width"]),
            height=int(video["height"]),
            fps_num=rate[0],
            fps_den=rate[1],
            audio_sample_rate=int(audio["sample_rate"]),
            duration_ms=round(float(duration) * 1000),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError):
        return None


def _probe(
    ffprobe_path: Path,
    media_path: Path,
    process_runner: ProcessRunner,
    cancellation: threading.Event,
) -> OutputMediaProfile | None:
    result = process_runner(
        [
            str(ffprobe_path),
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,width,height,avg_frame_rate,sample_rate:format=duration",
            "-of",
            "json",
            str(media_path),
        ],
        120,
        cancellation,
    )
    if result.exit_code != 0 or result.timed_out or result.cancelled:
        return None
    return _probe_profile(result.stdout)


def _source_streams(profile: OutputMediaProfile) -> StreamSelection | None:
    if (profile.fps_num, profile.fps_den) != (60, 1):
        return None
    return StreamSelection(
        video_index=profile.video_index,
        audio_index=profile.audio_index,
        width=profile.width,
        height=profile.height,
        fps_num=60,
        fps_den=1,
        audio_sample_rate=profile.audio_sample_rate,
    )


def _encoder_available(
    ffmpeg_path: Path,
    encoder: Literal["h264_nvenc", "libx264"],
    process_runner: ProcessRunner,
    cancellation: threading.Event,
) -> bool:
    result = process_runner(
        [
            str(ffmpeg_path),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=size=32x32:rate=60:duration=0.05",
            "-frames:v",
            "1",
            "-c:v",
            encoder,
            "-f",
            "null",
            "-",
        ],
        30,
        cancellation,
    )
    return result.exit_code == 0 and not result.timed_out and not result.cancelled


def _select_encoder(
    ffmpeg_path: Path,
    process_runner: ProcessRunner,
    cancellation: threading.Event,
) -> Literal["h264_nvenc", "libx264"] | None:
    if _encoder_available(ffmpeg_path, "h264_nvenc", process_runner, cancellation):
        return "h264_nvenc"
    if _encoder_available(ffmpeg_path, "libx264", process_runner, cancellation):
        return "libx264"
    return None


def _approval_binding(approval: ProposalApproval, approval_path: Path) -> ApprovalBinding:
    return ApprovalBinding(
        proposal_id=approval.proposal_id,
        proposal_sha256=approval.proposal_sha256,
        proposal_digest=approval.proposal_digest,
        source_identity_digest=approval.source_identity_digest,
        recording_id=approval.recording_id,
        sidecar_sha256=approval.sidecar_sha256,
        decision="approved",
        approval_sha256=_sha256(approval_path),
        decided_at=approval.decided_at,
    )


def _request_matches(
    request: RenderRequest,
    proposal: CutProposal,
    proposal_sha256: str,
    approval: ProposalApproval,
    approval_path: Path,
) -> bool:
    return (
        request.recording_id == proposal.recording_id
        and request.proposal_id == proposal.proposal_id
        and request.proposal_digest == proposal.proposal_digest
        and request.proposal_sha256 == proposal_sha256
        and request.source_identity_digest == proposal.source_identity_digest
        and request.sidecar_sha256 == proposal.sidecar_sha256
        and request.approval_sha256 == _sha256(approval_path)
        and request.approval_decided_at == approval.decided_at
    )


def _render_arguments(
    ffmpeg_path: Path,
    proposal: CutProposal,
    streams: StreamSelection,
    filtergraph: str,
    encoder: Literal["h264_nvenc", "libx264"],
    partial: Path,
) -> tuple[str, ...]:
    return (
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-n",
        "-loglevel",
        "error",
        "-progress",
        "pipe:1",
        "-i",
        proposal.source_path,
        "-filter_complex",
        filtergraph,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        encoder,
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-sn",
        "-dn",
        "-fps_mode",
        "passthrough",
        str(partial),
    )


def _status(
    proposal: CutProposal,
    state: RenderState,
    message: str,
    now: Callable[[], datetime],
    *,
    request: RenderRequest | None = None,
    render_id: str | None = None,
    result_path: Path | None = None,
    error_code: str | None = None,
) -> RenderStatus:
    return RenderStatus(
        artifact_type="matrix_auto_cutter_render_status",
        schema_version=RENDER_SCHEMA_VERSION,
        proposal_id=proposal.proposal_id,
        state=state,
        message_de=message,
        updated_at=now(),
        attempt_id=request.attempt_id if request is not None else None,
        render_id=render_id,
        target_path=request.target_path if request is not None else None,
        result_path=str(result_path) if result_path is not None else None,
        error_code=error_code,
    )


def _publish_status(
    proposal_path: Path,
    status: RenderStatus,
    callback: StatusCallback | None,
) -> None:
    write_render_status(proposal_path, status)
    if callback is not None:
        callback(status)


def _failure_result(
    *,
    render_id: str,
    request: RenderRequest,
    started_at: datetime,
    ended_at: datetime,
    target: Path,
    phase: Literal[
        "authorization", "planning", "render", "verification", "publication", "recovery"
    ],
    code: str,
    message: str,
    exit_code: int | None = None,
    plan_sha256: str | None = None,
    expected_duration_ms: int | None = None,
    interrupted: bool = False,
) -> RenderResult:
    return RenderResult(
        artifact_type="matrix_auto_cutter_render_result",
        schema_version=RENDER_SCHEMA_VERSION,
        render_id=render_id,
        attempt_id=request.attempt_id,
        plan_sha256=plan_sha256,
        status="interrupted" if interrupted else "failed",
        started_at=started_at,
        ended_at=ended_at,
        exit_code=exit_code,
        target_path=str(target),
        expected_duration_ms=expected_duration_ms,
        verification_status="not_run"
        if phase in {"authorization", "planning", "render", "recovery"}
        else "failed",
        error_phase=phase,
        error_code=code,
        message_de=message,
    )


def _persist_failure(
    proposal_path: Path,
    attempt_directory: Path,
    proposal: CutProposal,
    request: RenderRequest,
    result: RenderResult,
    now: Callable[[], datetime],
    callback: StatusCallback | None,
) -> RenderFailed:
    result_path = attempt_directory / RENDER_RESULT_FILE_NAME
    _atomic_write(result_path, _canonical_bytes(result), replace=True)
    _publish_status(
        proposal_path,
        _status(
            proposal,
            "render_failed",
            result.message_de,
            now,
            request=request,
            render_id=result.render_id,
            result_path=result_path,
            error_code=result.error_code,
        ),
        callback,
    )
    return RenderFailed(
        result.error_code or "E_RENDER_FAILED", result.message_de, result, result_path
    )


def _find_reusable_success(
    proposal_path: Path, request: RenderRequest
) -> RenderSucceeded | RenderFailed | None:
    renders = proposal_path.parent / "renders"
    if not renders.is_dir():
        return None
    for result_path in sorted(renders.glob(f"*/{RENDER_RESULT_FILE_NAME}")):
        loaded_result = _load_model(result_path, RenderResult)
        if not isinstance(loaded_result, RenderResult) or loaded_result.status != "succeeded":
            continue
        plan_path = result_path.with_name(RENDER_PLAN_FILE_NAME)
        loaded_plan = _load_model(plan_path, RenderPlan)
        if not isinstance(loaded_plan, RenderPlan):
            continue
        target = Path(loaded_result.target_path)
        bindings_match = (
            loaded_plan.recording_id == request.recording_id
            and loaded_plan.proposal_id == request.proposal_id
            and loaded_plan.proposal_digest == request.proposal_digest
            and loaded_plan.proposal_sha256 == request.proposal_sha256
            and loaded_plan.source_identity_digest == request.source_identity_digest
            and loaded_plan.sidecar_sha256 == request.sidecar_sha256
            and loaded_plan.approval.approval_sha256 == request.approval_sha256
            and loaded_plan.approval.decided_at == request.approval_decided_at
            and os.path.normcase(str(target)) == os.path.normcase(request.target_path)
        )
        if not bindings_match:
            continue
        try:
            valid_output = (
                target.is_file()
                and loaded_result.output_size_bytes == target.stat().st_size
                and loaded_result.output_sha256 == _sha256(target)
                and loaded_result.plan_sha256 == _sha256(plan_path)
            )
        except OSError:
            valid_output = False
        if not valid_output:
            return RenderFailed(
                "E_RENDER_REUSE_INVALID",
                "Früheres Erfolgsergebnis oder finale Ausgabe ist nicht mehr digestgleich.",
            )
        return RenderSucceeded(loaded_plan, loaded_result, plan_path, result_path, True)
    return None


def execute_approved_render(
    proposal_path: Path,
    request: RenderRequest,
    ffmpeg_path: Path,
    ffprobe_path: Path,
    *,
    process_runner: ProcessRunner | None = None,
    cancellation: threading.Event | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    uuid_factory: Callable[[], UUID] = uuid4,
    status_callback: StatusCallback | None = None,
) -> RenderExecution:
    """Reauthorize, render a partial, verify it, and publish it create-only."""
    runner = process_runner or NativeProcessRunner()
    stopped = cancellation or threading.Event()
    started_at = now()
    render_uuid = uuid_factory()
    if render_uuid.version != 4:
        return RenderFailed("E_RENDER_ID", "Render-ID muss UUIDv4 sein.")
    render_id = f"render-{render_uuid.hex}"
    target = Path(request.target_path)
    attempt_directory = proposal_path.parent / "renders" / request.attempt_id
    attempt_directory.mkdir(parents=True, exist_ok=True)

    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return RenderFailed(loaded.code, loaded.message_de)
    proposal = loaded.proposal
    initial_gate = check_render_authorization(proposal_path)
    if not initial_gate.authorized or initial_gate.approval is None:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="authorization",
            code="E_RENDER_NOT_AUTHORIZED",
            message=initial_gate.reason,
        )
        return _persist_failure(
            proposal_path,
            attempt_directory,
            proposal,
            request,
            result,
            now,
            status_callback,
        )
    reusable = _find_reusable_success(proposal_path, request)
    if reusable is not None:
        return reusable
    if not _request_matches(
        request,
        proposal,
        loaded.proposal_sha256,
        initial_gate.approval,
        proposal_path.with_name("approval.json"),
    ):
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="authorization",
            code="E_RENDER_REQUEST_BINDING",
            message="Renderauftrag passt nicht exakt zum aktuellen Proposal.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    if os.path.normcase(str(target.resolve(strict=False))) == os.path.normcase(
        str(Path(proposal.source_path).resolve(strict=True))
    ):
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_TARGET_SOURCE",
            message="Finales Ziel darf niemals die Source sein.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    if target.exists():
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_TARGET_CONFLICT",
            message="Zieldatei existiert bereits und wird nicht überschrieben.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )

    try:
        keep_segments = build_keep_segments(proposal)
    except ValueError as exc:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_KEEP_PLAN",
            message=f"Ungültige Keep-Segment-Planung: {exc}",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    ffmpeg = validate_ffmpeg(ffmpeg_path)
    if isinstance(ffmpeg, ProposalFailed):
        return RenderFailed(ffmpeg.code, ffmpeg.message_de)
    source_profile = _probe(ffprobe_path, Path(proposal.source_path), runner, stopped)
    streams = _source_streams(source_profile) if source_profile is not None else None
    if streams is None or streams.audio_sample_rate <= 0:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_STREAMS",
            message="Source besitzt nicht genau einen bestätigten 60/1-Video- und Audiostream.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    encoder = _select_encoder(ffmpeg_path, runner, stopped)
    if encoder is None:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_ENCODER",
            message="Weder h264_nvenc noch libx264 konnte real gestartet werden.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.stem}.{request.attempt_id}.partial.mp4")
    if partial.exists():
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_PARTIAL_CONFLICT",
            message="Attempt-eigene Partialdatei existiert bereits; Ownership ist nicht eindeutig.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    filtergraph = build_filtergraph(
        keep_segments,
        video_index=streams.video_index,
        audio_index=streams.audio_index,
    )
    arguments = _render_arguments(ffmpeg_path, proposal, streams, filtergraph, encoder, partial)
    output_frames = sum(item.end_frame - item.start_frame for item in keep_segments)
    cut_frames = proposal.source_frame_count - output_frames
    expected_output_ms = round(output_frames * 1000 / 60)
    expected_cut_ms = round(cut_frames * 1000 / 60)
    gate = check_render_authorization(proposal_path)
    if not gate.authorized or gate.approval is None:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="authorization",
            code="E_RENDER_NOT_AUTHORIZED",
            message=gate.reason,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    plan = RenderPlan(
        artifact_type="matrix_auto_cutter_render_plan",
        schema_version=RENDER_SCHEMA_VERSION,
        render_id=render_id,
        attempt_id=request.attempt_id,
        recording_id=proposal.recording_id,
        source_identity_digest=proposal.source_identity_digest,
        source_path=proposal.source_path,
        proposal_id=proposal.proposal_id,
        proposal_digest=proposal.proposal_digest,
        proposal_sha256=loaded.proposal_sha256,
        sidecar_sha256=proposal.sidecar_sha256,
        approval=_approval_binding(gate.approval, proposal_path.with_name("approval.json")),
        keep_segments=keep_segments,
        source_frame_count=proposal.source_frame_count,
        cut_frame_count=cut_frames,
        output_frame_count=output_frames,
        expected_source_duration_ms=proposal.source_duration_ms,
        expected_cut_duration_ms=expected_cut_ms,
        expected_output_duration_ms=expected_output_ms,
        ffmpeg=ffmpeg,
        encoder=encoder,
        streams=streams,
        filtergraph=filtergraph,
        filtergraph_sha256=_text_digest(filtergraph),
        arguments=arguments,
        arguments_sha256=_arguments_digest(arguments),
        target_path=str(target),
        partial_path=str(partial),
        created_at=now(),
    )
    plan_path = attempt_directory / RENDER_PLAN_FILE_NAME
    if not _atomic_write(plan_path, _canonical_bytes(plan), replace=False):
        return RenderFailed("E_RENDER_PLAN_CONFLICT", "Renderplan existiert bereits.")
    plan_sha256 = _sha256(plan_path)
    _publish_status(
        proposal_path,
        _status(
            proposal,
            "render_running",
            "FFmpeg rendert die freigegebenen Keep-Segmente.",
            now,
            request=request,
            render_id=render_id,
        ),
        status_callback,
    )
    # Mandatory immediate re-read: no cached approval result can authorize process start.
    immediate_gate = check_render_authorization(proposal_path)
    if not immediate_gate.authorized:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="authorization",
            code="E_RENDER_NOT_AUTHORIZED_AT_START",
            message=immediate_gate.reason,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    rendered = runner(arguments, RENDER_TIMEOUT_SECONDS, stopped)
    _atomic_write(
        attempt_directory / "ffmpeg-progress.log",
        rendered.stdout[-MAX_PROCESS_OUTPUT_BYTES:],
        replace=True,
    )
    _atomic_write(
        attempt_directory / "ffmpeg-stderr.log",
        rendered.stderr[-MAX_PROCESS_OUTPUT_BYTES:],
        replace=True,
    )
    if rendered.exit_code != 0 or rendered.timed_out or rendered.cancelled:
        code = (
            "E_RENDER_TIMEOUT"
            if rendered.timed_out
            else "E_RENDER_CANCELLED"
            if rendered.cancelled
            else "E_RENDER_FFMPEG"
        )
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="render",
            code=code,
            message="FFmpeg-Render wurde abgebrochen oder ist fehlgeschlagen.",
            exit_code=rendered.exit_code,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
            interrupted=rendered.cancelled,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    _publish_status(
        proposal_path,
        _status(
            proposal,
            "render_verifying",
            "Partialausgabe wird vollständig technisch verifiziert.",
            now,
            request=request,
            render_id=render_id,
        ),
        status_callback,
    )
    try:
        partial_ok = partial.is_file() and partial.stat().st_size > 0
    except OSError:
        partial_ok = False
    profile = _probe(ffprobe_path, partial, runner, stopped) if partial_ok else None
    tolerance_ms = 50
    profile_ok = (
        profile is not None
        and (profile.width, profile.height) == (streams.width, streams.height)
        and (profile.fps_num, profile.fps_den) == (60, 1)
        and profile.audio_sample_rate == 48_000
        and abs(profile.duration_ms - expected_output_ms) <= tolerance_ms
        and profile.duration_ms < proposal.source_duration_ms
    )
    decode = ProcessResult(None)
    if profile_ok:
        decode = runner(
            [
                str(ffmpeg_path),
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(partial),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ],
            VERIFY_TIMEOUT_SECONDS,
            stopped,
        )
    if not profile_ok or decode.exit_code != 0 or decode.timed_out or decode.cancelled:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="verification",
            code="E_RENDER_VERIFY",
            message=(
                "Partialausgabe erfüllt Medienprofil, Dauer oder vollständigen Decode-Test nicht."
            ),
            exit_code=rendered.exit_code,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    assert profile is not None
    final_gate = check_render_authorization(proposal_path)
    if not final_gate.authorized:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="authorization",
            code="E_RENDER_AUTHORIZATION_CHANGED",
            message=final_gate.reason,
            exit_code=rendered.exit_code,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    output_sha256 = _sha256(partial)
    if output_sha256 == proposal.source_identity.sha256:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="verification",
            code="E_RENDER_UNCHANGED_OUTPUT",
            message="Output darf nicht als unveränderte Sourceidentität erscheinen.",
            exit_code=rendered.exit_code,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    try:
        os.link(partial, target)
        partial.unlink()
    except OSError as exc:
        with suppress(OSError):
            if target.is_file() and _sha256(target) == output_sha256:
                target.unlink()
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="publication",
            code="E_RENDER_PUBLISH",
            message=f"Finale Ausgabe konnte nicht create-only veröffentlicht werden: {exc}",
            exit_code=rendered.exit_code,
            plan_sha256=plan_sha256,
            expected_duration_ms=expected_output_ms,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    result = RenderResult(
        artifact_type="matrix_auto_cutter_render_result",
        schema_version=RENDER_SCHEMA_VERSION,
        render_id=render_id,
        attempt_id=request.attempt_id,
        plan_sha256=plan_sha256,
        status="succeeded",
        started_at=started_at,
        ended_at=now(),
        exit_code=rendered.exit_code,
        target_path=str(target),
        output_sha256=output_sha256,
        output_size_bytes=target.stat().st_size,
        output_media_profile=profile,
        actual_duration_ms=profile.duration_ms,
        expected_duration_ms=expected_output_ms,
        verification_status="passed",
        message_de="Finale MP4 wurde verifiziert und create-only veröffentlicht.",
    )
    result_path = attempt_directory / RENDER_RESULT_FILE_NAME
    if not _atomic_write(result_path, _canonical_bytes(result), replace=False):
        with suppress(OSError):
            if target.is_file() and _sha256(target) == output_sha256:
                target.unlink()
        return RenderFailed(
            "E_RENDER_RESULT_CONFLICT",
            "Erfolgsergebnis konnte nicht create-only persistiert werden.",
        )
    _publish_status(
        proposal_path,
        _status(
            proposal,
            "render_succeeded",
            result.message_de,
            now,
            request=request,
            render_id=render_id,
            result_path=result_path,
        ),
        status_callback,
    )
    return RenderSucceeded(plan, result, plan_path, result_path)


def discover_ffprobe(ffmpeg_path: Path, explicit_path: str | None = None) -> Path | None:
    """Resolve ffprobe explicitly, beside FFmpeg, or from PATH."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates.append(ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
    discovered = shutil.which("ffprobe")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            if resolved.is_file():
                return resolved
        except OSError:
            continue
    return None
