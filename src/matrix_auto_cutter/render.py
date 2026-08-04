"""Approval-gated, source-preserving rendering of one cut proposal."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
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

from matrix_auto_cutter.approval import (
    ApprovalArtifact,
    SelectiveProposalApproval,
    check_render_authorization,
)
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
RENDER_ATTEMPTS_FILE_NAME = "render-attempts.json"
NVENC_CAPABILITY_FILE_NAME = "nvenc-capability.json"
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


class SelectiveApprovalBinding(ApprovalBinding):
    """Additional immutable binding emitted only for selective approvals."""

    selection_sha256: Sha256
    selection_digest: Sha256
    active_candidate_ids: tuple[str, ...]
    active_cut_count: int = Field(ge=1)
    selected_savings_ms: int = Field(ge=1)


type ApprovalBindingModel = ApprovalBinding | SelectiveApprovalBinding


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


class RenderRequestV11(CanonicalModel):
    """Selective request evidence; 1.0 requests retain their exact bytes."""

    artifact_type: Literal["matrix_auto_cutter_render_request"]
    schema_version: Literal["1.1"]
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
    selection_sha256: Sha256
    selection_digest: Sha256
    active_candidate_ids: tuple[str, ...]
    active_cut_count: int = Field(ge=1)
    selected_savings_ms: int = Field(ge=1)


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


class RenderStatusV11(CanonicalModel):
    """Extended polling status; kept separate so canonical 1.0 bytes remain valid."""

    artifact_type: Literal["matrix_auto_cutter_render_status"]
    schema_version: Literal["1.1"]
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
    phase: RenderState
    preferred_encoder: Literal["h264_nvenc", "libx264"] | None = None
    active_encoder: Literal["h264_nvenc", "libx264"] | None = None
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None
    encoder_attempt: int | None = Field(default=None, ge=1, le=2)
    max_encoder_attempts: Literal[2] = 2
    fallback_used: bool = False
    fallback_reason: str | None = Field(default=None, max_length=4000)
    ffmpeg_output_time_us: int | None = Field(default=None, ge=0)
    elapsed_total_ms: int | None = Field(default=None, ge=0)
    elapsed_attempt_ms: int | None = Field(default=None, ge=0)
    eta_ms: int | None = Field(default=None, ge=0)
    speed_x: float | None = Field(default=None, ge=0)
    frame: int | None = Field(default=None, ge=0)
    total_size_bytes: int | None = Field(default=None, ge=0)
    verification_status: Literal["not_run", "running", "failed", "passed"] = "not_run"

    @model_validator(mode="after")
    def progress_consistent(self) -> RenderStatusV11:
        """Keep final completion distinct from an in-progress FFmpeg measurement."""
        if self.state == "render_running" and self.progress_percent == 100:
            raise ValueError("encoding progress may not be 100")
        if self.fallback_used != (self.fallback_reason is not None):
            raise ValueError("fallback reason must match fallback flag")
        return self


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
    approval: ApprovalBindingModel
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


class RenderResultV11(CanonicalModel):
    """Terminal result with direct, canonical encoder-attempt evidence."""

    artifact_type: Literal["matrix_auto_cutter_render_result"]
    schema_version: Literal["1.1"]
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
    preferred_encoder: Literal["h264_nvenc", "libx264"] | None = None
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None
    fallback_used: bool = False
    fallback_reason: str | None = Field(default=None, max_length=4000)
    encoder_attempts: tuple[EncoderAttempt, ...] = ()
    encoder_attempts_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def encoder_evidence_consistent(self) -> RenderResultV11:
        """Bind the ordered attempts directly into the final terminal artifact."""
        if self.fallback_used != (self.fallback_reason is not None):
            raise ValueError("fallback reason must match fallback flag")
        if self.encoder_attempts:
            digest = _text_digest(
                "\n".join(item.model_dump_json() for item in self.encoder_attempts)
            )
            if self.encoder_attempts_sha256 != digest:
                raise ValueError("encoder attempts digest mismatch")
            if (
                self.final_encoder is not None
                and self.encoder_attempts[-1].encoder != self.final_encoder
            ):
                raise ValueError("final encoder must match final attempt")
        elif self.encoder_attempts_sha256 is not None:
            raise ValueError("attempts digest requires attempts")
        return self


@dataclass(frozen=True, slots=True)
class RenderAccepted:
    """A deliberate request was written atomically."""

    request: RenderRequestModel
    request_path: Path


@dataclass(frozen=True, slots=True)
class RenderFailed:
    """Stable failure that never represents a published output."""

    code: str
    message_de: str
    result: RenderResult | RenderResultV11 | None = None
    result_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RenderSucceeded:
    """Verified final output and its persistent evidence."""

    plan: RenderPlan
    result: RenderResult | RenderResultV11
    plan_path: Path
    result_path: Path
    reused: bool = False


type RenderRequestModel = RenderRequest | RenderRequestV11
type RequestResult = RenderAccepted | RenderFailed
type RenderExecution = RenderSucceeded | RenderFailed
type RenderStatusModel = RenderStatus | RenderStatusV11
type StatusCallback = Callable[[RenderStatusModel], None]


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """One bounded FFmpeg progress observation, emitted while the process is alive."""

    frame: int | None = None
    fps: float | None = None
    out_time_us: int | None = None
    speed: float | None = None
    total_size: int | None = None
    ended: bool = False


ProgressCallback = Callable[[ProgressSnapshot], None]


class _ProgressParser:
    """Tolerate partial/unknown FFmpeg progress keys and emit complete blocks only."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def feed(self, line: bytes) -> ProgressSnapshot | None:
        try:
            key, value = line.decode("utf-8", errors="replace").strip().split("=", 1)
        except ValueError:
            return None
        self.values[key] = value
        if key != "progress":
            return None

        def integer(name: str) -> int | None:
            try:
                return int(self.values[name])
            except (KeyError, ValueError):
                return None

        def number(name: str) -> float | None:
            try:
                value = self.values[name].rstrip("x")
                parsed = float(value)
                return parsed if parsed >= 0 else None
            except (KeyError, ValueError):
                return None

        output_us = integer("out_time_us")
        if output_us is None:
            milliseconds = integer("out_time_ms")
            output_us = milliseconds * 1000 if milliseconds is not None else None
        snapshot = ProgressSnapshot(
            frame=integer("frame"),
            fps=number("fps"),
            out_time_us=output_us,
            speed=number("speed"),
            total_size=integer("total_size"),
            ended=value == "end",
        )
        self.values = {}
        return snapshot


class EncoderAttempt(CanonicalModel):
    """Immutable evidence for one actual encoder process invocation."""

    sequence: Literal[1, 2]
    encoder: Literal["h264_nvenc", "libx264"]
    started_at: AwareDatetime
    ended_at: AwareDatetime
    arguments_sha256: Sha256
    partial_path: str
    exit_code: int | None = None
    outcome: Literal["succeeded", "failed", "cancelled", "timed_out"]
    timed_out: bool = False
    cancelled: bool = False
    error_code: str | None = None
    diagnostic: str = Field(max_length=4000)

    @model_validator(mode="after")
    def terminal_consistent(self) -> EncoderAttempt:
        """Make timeout/cancellation explicit and non-contradictory."""
        if self.timed_out != (self.outcome == "timed_out"):
            raise ValueError("timeout flag does not match attempt outcome")
        if self.cancelled != (self.outcome == "cancelled"):
            raise ValueError("cancellation flag does not match attempt outcome")
        return self


class RenderAttempts(CanonicalModel):
    """Additive 1.0 side artifact; it never changes existing plan/result bytes."""

    artifact_type: Literal["matrix_auto_cutter_render_attempts"]
    schema_version: Literal["1.0"]
    render_id: str
    attempt_id: str
    preferred_encoder: Literal["h264_nvenc", "libx264"]
    attempts: tuple[EncoderAttempt, ...]
    fallback_reason: str | None = Field(default=None, max_length=4000)
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None

    @model_validator(mode="after")
    def consistent(self) -> RenderAttempts:
        """Keep the bounded policy and final evidence tamper-evident."""
        if not self.attempts or len(self.attempts) > 2:
            raise ValueError("render attempts require one or two entries")
        if tuple(item.sequence for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("attempt sequences must be canonical")
        if self.attempts[0].encoder != self.preferred_encoder:
            raise ValueError("first attempt must use preferred encoder")
        if len(self.attempts) == 2 and (
            self.preferred_encoder != "h264_nvenc" or self.attempts[1].encoder != "libx264"
        ):
            raise ValueError("only one NVENC to libx264 fallback is allowed")
        if self.final_encoder is not None and self.attempts[-1].encoder != self.final_encoder:
            raise ValueError("final encoder must be the final attempted encoder")
        return self


class EncoderCapability(CanonicalModel):
    """Structured, product-binary-bound NVENC capability evidence."""

    artifact_type: Literal["matrix_auto_cutter_nvenc_capability"]
    schema_version: Literal["1.0"]
    encoder: Literal["h264_nvenc"]
    ffmpeg_path: str
    ffmpeg_sha256: Sha256
    tested_at: AwareDatetime
    arguments_sha256: Sha256
    output_path: str
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    output_created: bool = False
    ffprobe_verified: bool = False
    decode_verified: bool = False
    reason: str = Field(min_length=1, max_length=4000)
    diagnostic: str = Field(max_length=4000)


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
        return self.run(arguments, timeout_seconds, cancellation)

    def run(
        self,
        arguments: Sequence[str],
        timeout_seconds: int,
        cancellation: threading.Event,
        progress_callback: ProgressCallback | None = None,
    ) -> ProcessResult:
        """Drain both pipes incrementally, avoiding FFmpeg pipe deadlocks on Windows."""
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
        stdout = bytearray()
        stderr = bytearray()
        completed = threading.Event()
        parser = _ProgressParser()

        def append_bounded(target: bytearray, data: bytes) -> None:
            target.extend(data)
            overflow = len(target) - MAX_PROCESS_OUTPUT_BYTES
            if overflow > 0:
                del target[:overflow]

        def drain(stream: object, target: bytearray, parse_progress: bool) -> None:
            assert hasattr(stream, "readline")
            while True:
                line = stream.readline()
                if not line:
                    break
                append_bounded(target, line)
                if parse_progress and progress_callback is not None:
                    snapshot = parser.feed(line)
                    if snapshot is not None:
                        progress_callback(snapshot)
            completed.set()

        assert process.stdout is not None and process.stderr is not None
        stdout_thread = threading.Thread(
            target=drain,
            args=(process.stdout, stdout, True),
            name="matrix-ffmpeg-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=drain,
            args=(process.stderr, stderr, False),
            name="matrix-ffmpeg-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        cancelled = False
        try:
            while process.poll() is None:
                if cancellation.is_set():
                    cancelled = True
                    process.kill()
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    process.kill()
                    break
                completed.wait(0.05)
            process.wait(timeout=10)
            stdout_thread.join(timeout=10)
            stderr_thread.join(timeout=10)
            return ProcessResult(
                process.returncode,
                bytes(stdout),
                bytes(stderr),
                timed_out=timed_out,
                cancelled=cancelled or cancellation.is_set(),
            )
        finally:
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
            with suppress(subprocess.SubprocessError):
                process.wait(timeout=10)
            stdout_thread.join(timeout=10)
            stderr_thread.join(timeout=10)
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


def load_render_request(proposal_path: Path) -> RenderRequestModel | None:
    """Strictly load the current deliberate request."""
    loaded = _load_versioned(render_request_path(proposal_path), RenderRequest, RenderRequestV11)
    return loaded if isinstance(loaded, RenderRequest | RenderRequestV11) else None


def _load_versioned(
    path: Path, legacy: type[CanonicalModel], current: type[CanonicalModel]
) -> CanonicalModel | None:
    """Select a strict canonical reader from the explicit schema marker only."""
    try:
        document = json.loads(path.read_bytes())
        version = document.get("schema_version") if isinstance(document, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if version == "1.0":
        return _load_model(path, legacy)
    if version == "1.1":
        return _load_model(path, current)
    return None


def load_render_status(proposal_path: Path) -> RenderStatusModel | None:
    """Strictly load the current UI status."""
    loaded = _load_versioned(render_status_path(proposal_path), RenderStatus, RenderStatusV11)
    return loaded if isinstance(loaded, RenderStatus | RenderStatusV11) else None


def write_render_status(proposal_path: Path, status: RenderStatusModel) -> None:
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
    if isinstance(gate.approval, SelectiveProposalApproval) and gate.selection is not None:
        request: RenderRequestModel = RenderRequestV11(
            artifact_type="matrix_auto_cutter_render_request",
            schema_version="1.1",
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
            selection_sha256=gate.approval.selection_sha256,
            selection_digest=gate.approval.selection_digest,
            active_candidate_ids=gate.approval.active_candidate_ids,
            active_cut_count=gate.approval.active_cut_count,
            selected_savings_ms=gate.approval.selected_savings_ms,
        )
    else:
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


def build_keep_segments(
    proposal: CutProposal, active_candidate_ids: Sequence[str] | None = None
) -> tuple[KeepSegment, ...]:
    """Return the exact complement of validated sorted half-open cuts."""
    if proposal.status != "ready" or not proposal.proposed_cuts:
        raise ValueError("approved proposal requires at least one cut")
    keeps: list[KeepSegment] = []
    cursor = 0
    minimum = max(1, (proposal.analysis_parameters.minimum_keep_island_ms * 60 + 999) // 1000)
    active = set(active_candidate_ids) if active_candidate_ids is not None else None
    if active is not None and (
        not active or not active.issubset({cut.candidate_id for cut in proposal.proposed_cuts})
    ):
        raise ValueError("active cut selection is empty or contains an unknown candidate")
    for cut in proposal.proposed_cuts:
        if active is not None and cut.candidate_id not in active:
            continue
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


def _bounded_diagnostic(result: ProcessResult) -> str:
    """Return a short human-readable tail without retaining FFmpeg's full output."""
    return result.stderr[-4000:].decode("utf-8", errors="replace").strip()


def _owned_capability_output(path: Path, directory: Path) -> bool:
    """Only delete the UUID-named test MP4 created below this render attempt directory."""
    try:
        return (
            path.parent.resolve(strict=True) == directory.resolve(strict=True)
            and path.name.startswith("nvenc-capability-")
            and path.suffix == ".mp4"
        )
    except OSError:
        return False


def run_nvenc_capability_test(
    ffmpeg: FfmpegIdentity,
    ffprobe_path: Path,
    directory: Path,
    process_runner: ProcessRunner,
    cancellation: threading.Event,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    uuid_factory: Callable[[], UUID] = uuid4,
) -> EncoderCapability:
    """Encode, probe and fully decode a short real NVENC MP4 with the product binary."""
    directory.mkdir(parents=True, exist_ok=True)
    token = uuid_factory()
    output = directory / f"nvenc-capability-{token.hex}.mp4"
    arguments = (
        ffmpeg.absolute_path,
        "-hide_banner",
        "-nostdin",
        "-n",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=60:duration=3",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:sample_rate=48000:duration=3",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p5",
        "-rc",
        "vbr",
        "-cq",
        "19",
        "-b:v",
        "0",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "60",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output),
    )
    rendered = process_runner(arguments, 90, cancellation)
    created = output.is_file() and output.stat().st_size > 0
    profile = _probe(ffprobe_path, output, process_runner, cancellation) if created else None
    probed = (
        profile is not None
        and (profile.width, profile.height) == (640, 360)
        and (profile.fps_num, profile.fps_den, profile.audio_sample_rate) == (60, 1, 48_000)
    )
    decode = ProcessResult(None)
    if probed:
        decode = process_runner(
            (
                ffmpeg.absolute_path,
                "-v",
                "error",
                "-nostdin",
                "-i",
                str(output),
                "-map",
                "0:v:0",
                "-map",
                "0:a:0",
                "-f",
                "null",
                "-",
            ),
            VERIFY_TIMEOUT_SECONDS,
            cancellation,
        )
    success = (
        rendered.exit_code == 0
        and not rendered.timed_out
        and not rendered.cancelled
        and created
        and probed
        and decode.exit_code == 0
        and not decode.timed_out
        and not decode.cancelled
    )
    reason = (
        "ok"
        if success
        else (
            "cancelled"
            if rendered.cancelled
            else "timeout"
            if rendered.timed_out
            else "encode_failed"
            if rendered.exit_code != 0
            else "probe_failed"
            if not probed
            else "decode_failed"
        )
    )
    capability = EncoderCapability(
        artifact_type="matrix_auto_cutter_nvenc_capability",
        schema_version="1.0",
        encoder="h264_nvenc",
        ffmpeg_path=ffmpeg.absolute_path,
        ffmpeg_sha256=ffmpeg.sha256,
        tested_at=now(),
        arguments_sha256=_arguments_digest(arguments),
        output_path=str(output),
        exit_code=rendered.exit_code,
        timed_out=rendered.timed_out,
        cancelled=rendered.cancelled,
        output_created=created,
        ffprobe_verified=probed,
        decode_verified=decode.exit_code == 0 and not decode.timed_out and not decode.cancelled,
        reason=reason,
        diagnostic=_bounded_diagnostic(rendered),
    )
    _atomic_write(
        directory / NVENC_CAPABILITY_FILE_NAME, _canonical_bytes(capability), replace=True
    )
    if _owned_capability_output(output, directory):
        with suppress(OSError):
            output.unlink()
    return capability


def _select_encoder(
    ffmpeg_path: Path,
    process_runner: ProcessRunner,
    cancellation: threading.Event,
) -> Literal["h264_nvenc", "libx264"] | None:
    if _encoder_available(ffmpeg_path, "libx264", process_runner, cancellation):
        return "libx264"
    return None


def _approval_binding(approval: ApprovalArtifact, approval_path: Path) -> ApprovalBindingModel:
    if isinstance(approval, SelectiveProposalApproval):
        return SelectiveApprovalBinding(
            proposal_id=approval.proposal_id,
            proposal_sha256=approval.proposal_sha256,
            proposal_digest=approval.proposal_digest,
            source_identity_digest=approval.source_identity_digest,
            recording_id=approval.recording_id,
            sidecar_sha256=approval.sidecar_sha256,
            decision="approved",
            approval_sha256=_sha256(approval_path),
            decided_at=approval.decided_at,
            selection_sha256=approval.selection_sha256,
            selection_digest=approval.selection_digest,
            active_candidate_ids=approval.active_candidate_ids,
            active_cut_count=approval.active_cut_count,
            selected_savings_ms=approval.selected_savings_ms,
        )
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
    request: RenderRequestModel,
    proposal: CutProposal,
    proposal_sha256: str,
    approval: ApprovalArtifact,
    approval_path: Path,
) -> bool:
    common = (
        request.recording_id == proposal.recording_id
        and request.proposal_id == proposal.proposal_id
        and request.proposal_digest == proposal.proposal_digest
        and request.proposal_sha256 == proposal_sha256
        and request.source_identity_digest == proposal.source_identity_digest
        and request.sidecar_sha256 == proposal.sidecar_sha256
        and request.approval_sha256 == _sha256(approval_path)
        and request.approval_decided_at == approval.decided_at
    )
    if not common:
        return False
    if isinstance(approval, SelectiveProposalApproval):
        return (
            isinstance(request, RenderRequestV11)
            and request.selection_sha256 == approval.selection_sha256
            and request.selection_digest == approval.selection_digest
            and request.active_candidate_ids == approval.active_candidate_ids
            and request.active_cut_count == approval.active_cut_count
            and request.selected_savings_ms == approval.selected_savings_ms
        )
    return not isinstance(request, RenderRequestV11)


def _render_arguments(
    ffmpeg_path: Path,
    proposal: CutProposal,
    streams: StreamSelection,
    filtergraph: str,
    encoder: Literal["h264_nvenc", "libx264"],
    partial: Path,
) -> tuple[str, ...]:
    common = (
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
    )
    video = (
        (
            "-preset",
            "p5",
            "-rc",
            "vbr",
            "-cq",
            "19",
            "-b:v",
            "0",
            "-profile:v",
            "high",
            "-pix_fmt",
            "yuv420p",
        )
        if encoder == "h264_nvenc"
        else ("-preset", "slow", "-crf", "18", "-profile:v", "high", "-pix_fmt", "yuv420p")
    )
    return (
        common
        + video
        + (
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
    )


def _status(
    proposal: CutProposal,
    state: RenderState,
    message: str,
    now: Callable[[], datetime],
    *,
    request: RenderRequestModel | None = None,
    render_id: str | None = None,
    result_path: Path | None = None,
    error_code: str | None = None,
    preferred_encoder: Literal["h264_nvenc", "libx264"] | None = None,
    active_encoder: Literal["h264_nvenc", "libx264"] | None = None,
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None,
    encoder_attempt: int | None = None,
    fallback_reason: str | None = None,
    progress_percent: int | None = None,
    ffmpeg_output_time_us: int | None = None,
    elapsed_total_ms: int | None = None,
    elapsed_attempt_ms: int | None = None,
    eta_ms: int | None = None,
    speed_x: float | None = None,
    frame: int | None = None,
    total_size_bytes: int | None = None,
    verification_status: Literal["not_run", "running", "failed", "passed"] = "not_run",
) -> RenderStatusV11:
    return RenderStatusV11(
        artifact_type="matrix_auto_cutter_render_status",
        schema_version="1.1",
        proposal_id=proposal.proposal_id,
        state=state,
        message_de=message,
        updated_at=now(),
        attempt_id=request.attempt_id if request is not None else None,
        render_id=render_id,
        target_path=request.target_path if request is not None else None,
        result_path=str(result_path) if result_path is not None else None,
        error_code=error_code,
        phase=state,
        preferred_encoder=preferred_encoder,
        active_encoder=active_encoder,
        final_encoder=final_encoder,
        encoder_attempt=encoder_attempt,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        progress_percent=progress_percent,
        ffmpeg_output_time_us=ffmpeg_output_time_us,
        elapsed_total_ms=elapsed_total_ms,
        elapsed_attempt_ms=elapsed_attempt_ms,
        eta_ms=eta_ms,
        speed_x=speed_x,
        frame=frame,
        total_size_bytes=total_size_bytes,
        verification_status=verification_status,
    )


def _publish_status(
    proposal_path: Path,
    status: RenderStatusModel,
    callback: StatusCallback | None,
) -> None:
    write_render_status(proposal_path, status)
    if callback is not None:
        callback(status)


def _failure_result(
    *,
    render_id: str,
    request: RenderRequestModel,
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
    preferred_encoder: Literal["h264_nvenc", "libx264"] | None = None,
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None,
    fallback_reason: str | None = None,
    encoder_attempts: Sequence[EncoderAttempt] = (),
) -> RenderResultV11:
    attempts = tuple(encoder_attempts)
    return RenderResultV11(
        artifact_type="matrix_auto_cutter_render_result",
        schema_version="1.1",
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
        preferred_encoder=preferred_encoder,
        final_encoder=final_encoder,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        encoder_attempts=attempts,
        encoder_attempts_sha256=(
            _text_digest("\n".join(item.model_dump_json() for item in attempts))
            if attempts
            else None
        ),
    )


def _persist_failure(
    proposal_path: Path,
    attempt_directory: Path,
    proposal: CutProposal,
    request: RenderRequestModel,
    result: RenderResult | RenderResultV11,
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
            preferred_encoder=(
                result.preferred_encoder if isinstance(result, RenderResultV11) else None
            ),
            active_encoder=(result.final_encoder if isinstance(result, RenderResultV11) else None),
            final_encoder=(result.final_encoder if isinstance(result, RenderResultV11) else None),
            encoder_attempt=(
                len(result.encoder_attempts)
                if isinstance(result, RenderResultV11) and result.encoder_attempts
                else None
            ),
            fallback_reason=(
                result.fallback_reason if isinstance(result, RenderResultV11) else None
            ),
            progress_percent=(99 if result.error_phase in {"render", "verification"} else None),
            verification_status=(
                "failed" if result.error_phase in {"verification", "publication"} else "not_run"
            ),
        ),
        callback,
    )
    return RenderFailed(
        result.error_code or "E_RENDER_FAILED", result.message_de, result, result_path
    )


def _write_attempt_evidence(
    directory: Path,
    *,
    render_id: str,
    request: RenderRequestModel,
    preferred: Literal["h264_nvenc", "libx264"],
    attempts: Sequence[EncoderAttempt],
    fallback_reason: str | None = None,
    final_encoder: Literal["h264_nvenc", "libx264"] | None = None,
) -> None:
    """Persist additive evidence without changing historical plan/result schemas."""
    evidence = RenderAttempts(
        artifact_type="matrix_auto_cutter_render_attempts",
        schema_version="1.0",
        render_id=render_id,
        attempt_id=request.attempt_id,
        preferred_encoder=preferred,
        attempts=tuple(attempts),
        fallback_reason=fallback_reason,
        final_encoder=final_encoder,
    )
    _atomic_write(directory / RENDER_ATTEMPTS_FILE_NAME, _canonical_bytes(evidence), replace=True)


def _format_seconds(value: float | None) -> str:
    """Render a neutral duration only when it is finite and non-negative."""
    if value is None or value < 0 or value == float("inf"):
        return "-"
    total = round(value)
    return f"{total // 60:02d}:{total % 60:02d}"


def _find_reusable_success(
    proposal_path: Path, request: RenderRequestModel
) -> RenderSucceeded | RenderFailed | None:
    renders = proposal_path.parent / "renders"
    if not renders.is_dir():
        return None
    for result_path in sorted(renders.glob(f"*/{RENDER_RESULT_FILE_NAME}")):
        loaded_result = _load_versioned(result_path, RenderResult, RenderResultV11)
        if (
            not isinstance(loaded_result, RenderResult | RenderResultV11)
            or loaded_result.status != "succeeded"
        ):
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
    request: RenderRequestModel,
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
        keep_segments = build_keep_segments(
            proposal,
            (
                initial_gate.approval.active_candidate_ids
                if isinstance(initial_gate.approval, SelectiveProposalApproval)
                else None
            ),
        )
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
    capability = run_nvenc_capability_test(
        ffmpeg, ffprobe_path, attempt_directory, runner, stopped, now=now
    )
    if capability.cancelled or stopped.is_set():
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="render",
            code="E_RENDER_CANCELLED",
            message="NVENC-Capability wurde abgebrochen.",
            interrupted=True,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    if capability.ffmpeg_path != ffmpeg.absolute_path or capability.ffmpeg_sha256 != ffmpeg.sha256:
        return RenderFailed(
            "E_RENDER_CAPABILITY_BINDING", "NVENC-Capability ist nicht an FFmpeg gebunden."
        )
    encoder: Literal["h264_nvenc", "libx264"] | None = (
        "h264_nvenc" if capability.reason == "ok" else None
    )
    if encoder is None and _encoder_available(ffmpeg_path, "libx264", runner, stopped):
        encoder = "libx264"
    if encoder is None:
        result = _failure_result(
            render_id=render_id,
            request=request,
            started_at=started_at,
            ended_at=now(),
            target=target,
            phase="planning",
            code="E_RENDER_ENCODER",
            message="NVENC-Capability und libx264-Verfügbarkeit sind fehlgeschlagen.",
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.stem}.{request.attempt_id}.{encoder}.partial.mp4")
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
    preferred_encoder = encoder
    encoder_attempts: list[EncoderAttempt] = []
    fallback_reason: str | None = (
        None if capability.reason == "ok" else f"NVENC-Capability: {capability.reason}"
    )
    output_frames = sum(item.end_frame - item.start_frame for item in keep_segments)
    cut_frames = proposal.source_frame_count - output_frames
    expected_output_ms = round(output_frames * 1000 / 60)
    expected_cut_ms = round(cut_frames * 1000 / 60)
    gate = check_render_authorization(proposal_path)
    if (
        not gate.authorized
        or gate.approval is None
        or not _request_matches(
            request,
            proposal,
            loaded.proposal_sha256,
            gate.approval,
            proposal_path.with_name("approval.json"),
        )
    ):
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
            preferred_encoder=preferred_encoder,
            active_encoder=encoder,
            encoder_attempt=1,
            progress_percent=0,
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
            preferred_encoder=preferred_encoder,
            final_encoder=encoder,
            fallback_reason=fallback_reason,
            encoder_attempts=encoder_attempts,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    attempt_started = now()
    total_progress_started = time.monotonic()
    progress_started = time.monotonic()
    last_progress_percent = 0
    last_progress_write = 0.0

    def publish_progress(snapshot: ProgressSnapshot) -> None:
        nonlocal last_progress_percent, last_progress_write
        if snapshot.out_time_us is None or expected_output_ms <= 0:
            return
        percent = min(
            99, max(last_progress_percent, int(snapshot.out_time_us / (expected_output_ms * 10)))
        )
        elapsed = max(0.0, time.monotonic() - progress_started)
        elapsed_total = max(0.0, time.monotonic() - total_progress_started)
        eta: float | None = None
        if snapshot.speed is not None and snapshot.speed > 0 and snapshot.out_time_us > 0:
            eta = max(
                0.0,
                (expected_output_ms * 1_000 - snapshot.out_time_us) / 1_000_000 / snapshot.speed,
            )
        current = time.monotonic()
        if (
            percent == last_progress_percent
            and current - last_progress_write < 0.5
            and not snapshot.ended
        ):
            return
        last_progress_percent, last_progress_write = percent, current
        speed = (
            f"{snapshot.speed:.2f}x" if snapshot.speed is not None and snapshot.speed > 0 else "-"
        )
        _publish_status(
            proposal_path,
            _status(
                proposal,
                "render_running",
                f"Encoder: {'NVIDIA NVENC' if encoder == 'h264_nvenc' else 'CPU / libx264'} · "
                f"Fallback: {'nein' if fallback_reason is None else fallback_reason} · "
                f"Versuch {len(encoder_attempts) + 1} · {percent}% · vergangen "
                f"{_format_seconds(elapsed)} · ETA {_format_seconds(eta)} · "
                f"Geschwindigkeit {speed}",
                now,
                request=request,
                render_id=render_id,
                preferred_encoder=preferred_encoder,
                active_encoder=encoder,
                encoder_attempt=len(encoder_attempts) + 1,
                fallback_reason=fallback_reason,
                progress_percent=percent,
                ffmpeg_output_time_us=snapshot.out_time_us,
                elapsed_total_ms=round(elapsed_total * 1000),
                elapsed_attempt_ms=round(elapsed * 1000),
                eta_ms=round(eta * 1000) if eta is not None else None,
                speed_x=snapshot.speed,
                frame=snapshot.frame,
                total_size_bytes=snapshot.total_size,
            ),
            status_callback,
        )

    rendered = (
        runner.run(arguments, RENDER_TIMEOUT_SECONDS, stopped, publish_progress)
        if isinstance(runner, NativeProcessRunner)
        else runner(arguments, RENDER_TIMEOUT_SECONDS, stopped)
    )
    attempt_ended = now()
    first_outcome: Literal["succeeded", "failed", "cancelled", "timed_out"] = (
        "cancelled"
        if rendered.cancelled
        else "timed_out"
        if rendered.timed_out
        else "succeeded"
        if rendered.exit_code == 0
        else "failed"
    )
    encoder_attempts.append(
        EncoderAttempt(
            sequence=1,
            encoder=encoder,
            started_at=attempt_started,
            ended_at=attempt_ended,
            arguments_sha256=_arguments_digest(arguments),
            partial_path=str(partial),
            exit_code=rendered.exit_code,
            outcome=first_outcome,
            timed_out=rendered.timed_out,
            cancelled=rendered.cancelled,
            error_code=(
                "E_RENDER_TIMEOUT"
                if rendered.timed_out
                else "E_RENDER_CANCELLED"
                if rendered.cancelled
                else "E_RENDER_FFMPEG"
                if rendered.exit_code != 0
                else None
            ),
            diagnostic=_bounded_diagnostic(rendered),
        )
    )
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
        # A single CPU retry is allowed only for a real NVENC render failure, never for
        # cancellation, timeout, or any authorization/binding/target/verification phase.
        can_fallback = (
            encoder == "h264_nvenc"
            and not rendered.timed_out
            and not rendered.cancelled
            and rendered.exit_code != 0
            and capability.reason == "ok"
        )
        if can_fallback:
            retry_gate = check_render_authorization(proposal_path)
            retry_loaded = load_proposal(proposal_path)
            retry_allowed = (
                retry_gate.authorized
                and retry_gate.approval is not None
                and isinstance(retry_loaded, type(loaded))
                and not isinstance(retry_loaded, ProposalFailed)
                and _request_matches(
                    request,
                    retry_loaded.proposal,
                    retry_loaded.proposal_sha256,
                    retry_gate.approval,
                    proposal_path.with_name("approval.json"),
                )
                and not target.exists()
            )
            if retry_allowed:
                fallback_reason = (
                    f"NVENC-Vollrender fehlgeschlagen (Exitcode {rendered.exit_code})."
                )
                fallback_partial = target.with_name(
                    f"{target.stem}.{request.attempt_id}.libx264.partial.mp4"
                )
                if not fallback_partial.exists():
                    fallback_arguments = _render_arguments(
                        ffmpeg_path, proposal, streams, filtergraph, "libx264", fallback_partial
                    )
                    _publish_status(
                        proposal_path,
                        _status(
                            proposal,
                            "render_running",
                            "NVENC fehlgeschlagen; kontrollierter CPU-Fallback startet.",
                            now,
                            request=request,
                            render_id=render_id,
                        ),
                        status_callback,
                    )
                    immediate_retry_gate = check_render_authorization(proposal_path)
                    immediate_retry_loaded = load_proposal(proposal_path)
                    if (
                        immediate_retry_gate.authorized
                        and immediate_retry_gate.approval is not None
                        and not isinstance(immediate_retry_loaded, ProposalFailed)
                        and _request_matches(
                            request,
                            immediate_retry_loaded.proposal,
                            immediate_retry_loaded.proposal_sha256,
                            immediate_retry_gate.approval,
                            proposal_path.with_name("approval.json"),
                        )
                    ):
                        encoder = "libx264"
                        partial = fallback_partial
                        arguments = fallback_arguments
                        progress_started = time.monotonic()
                        last_progress_percent = 0
                        retry_started = now()
                        rendered = (
                            runner.run(arguments, RENDER_TIMEOUT_SECONDS, stopped, publish_progress)
                            if isinstance(runner, NativeProcessRunner)
                            else runner(arguments, RENDER_TIMEOUT_SECONDS, stopped)
                        )
                        retry_ended = now()
                        retry_outcome: Literal["succeeded", "failed", "cancelled", "timed_out"] = (
                            "cancelled"
                            if rendered.cancelled
                            else "timed_out"
                            if rendered.timed_out
                            else "succeeded"
                            if rendered.exit_code == 0
                            else "failed"
                        )
                        encoder_attempts.append(
                            EncoderAttempt(
                                sequence=2,
                                encoder="libx264",
                                started_at=retry_started,
                                ended_at=retry_ended,
                                arguments_sha256=_arguments_digest(arguments),
                                partial_path=str(partial),
                                exit_code=rendered.exit_code,
                                outcome=retry_outcome,
                                timed_out=rendered.timed_out,
                                cancelled=rendered.cancelled,
                                error_code=(
                                    "E_RENDER_TIMEOUT"
                                    if rendered.timed_out
                                    else "E_RENDER_CANCELLED"
                                    if rendered.cancelled
                                    else "E_RENDER_FFMPEG"
                                    if rendered.exit_code != 0
                                    else None
                                ),
                                diagnostic=_bounded_diagnostic(rendered),
                            )
                        )
        if rendered.exit_code != 0 or rendered.timed_out or rendered.cancelled:
            _write_attempt_evidence(
                attempt_directory,
                render_id=render_id,
                request=request,
                preferred=preferred_encoder,
                attempts=encoder_attempts,
                fallback_reason=fallback_reason,
            )
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
                preferred_encoder=preferred_encoder,
                final_encoder=encoder,
                fallback_reason=fallback_reason,
                encoder_attempts=encoder_attempts,
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
            preferred_encoder=preferred_encoder,
            active_encoder=encoder,
            encoder_attempt=len(encoder_attempts),
            fallback_reason=fallback_reason,
            progress_percent=99,
            elapsed_total_ms=round((time.monotonic() - total_progress_started) * 1000),
            elapsed_attempt_ms=round((time.monotonic() - progress_started) * 1000),
            verification_status="running",
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
        _write_attempt_evidence(
            attempt_directory,
            render_id=render_id,
            request=request,
            preferred=preferred_encoder,
            attempts=encoder_attempts,
            fallback_reason=fallback_reason,
        )
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
            preferred_encoder=preferred_encoder,
            final_encoder=encoder,
            fallback_reason=fallback_reason,
            encoder_attempts=encoder_attempts,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    assert profile is not None
    final_gate = check_render_authorization(proposal_path)
    if not final_gate.authorized:
        _write_attempt_evidence(
            attempt_directory,
            render_id=render_id,
            request=request,
            preferred=preferred_encoder,
            attempts=encoder_attempts,
            fallback_reason=fallback_reason,
        )
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
            preferred_encoder=preferred_encoder,
            final_encoder=encoder,
            fallback_reason=fallback_reason,
            encoder_attempts=encoder_attempts,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    output_sha256 = _sha256(partial)
    if output_sha256 == proposal.source_identity.sha256:
        _write_attempt_evidence(
            attempt_directory,
            render_id=render_id,
            request=request,
            preferred=preferred_encoder,
            attempts=encoder_attempts,
            fallback_reason=fallback_reason,
        )
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
            preferred_encoder=preferred_encoder,
            final_encoder=encoder,
            fallback_reason=fallback_reason,
            encoder_attempts=encoder_attempts,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    try:
        os.link(partial, target)
        partial.unlink()
    except OSError as exc:
        _write_attempt_evidence(
            attempt_directory,
            render_id=render_id,
            request=request,
            preferred=preferred_encoder,
            attempts=encoder_attempts,
            fallback_reason=fallback_reason,
        )
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
            preferred_encoder=preferred_encoder,
            final_encoder=encoder,
            fallback_reason=fallback_reason,
            encoder_attempts=encoder_attempts,
        )
        return _persist_failure(
            proposal_path, attempt_directory, proposal, request, result, now, status_callback
        )
    _write_attempt_evidence(
        attempt_directory,
        render_id=render_id,
        request=request,
        preferred=preferred_encoder,
        attempts=encoder_attempts,
        fallback_reason=fallback_reason,
        final_encoder=encoder,
    )
    result = RenderResultV11(
        artifact_type="matrix_auto_cutter_render_result",
        schema_version="1.1",
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
        preferred_encoder=preferred_encoder,
        final_encoder=encoder,
        fallback_used=fallback_reason is not None,
        fallback_reason=fallback_reason,
        encoder_attempts=tuple(encoder_attempts),
        encoder_attempts_sha256=_text_digest(
            "\n".join(item.model_dump_json() for item in encoder_attempts)
        ),
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
            f"Encoder: {'NVIDIA NVENC' if encoder == 'h264_nvenc' else 'CPU / libx264'} · "
            f"Fallback: {'nein' if fallback_reason is None else fallback_reason} · "
            f"100% · {result.message_de}",
            now,
            request=request,
            render_id=render_id,
            result_path=result_path,
            preferred_encoder=preferred_encoder,
            active_encoder=encoder,
            final_encoder=encoder,
            encoder_attempt=len(encoder_attempts),
            fallback_reason=fallback_reason,
            progress_percent=100,
            elapsed_total_ms=round((time.monotonic() - total_progress_started) * 1000),
            elapsed_attempt_ms=round((time.monotonic() - progress_started) * 1000),
            verification_status="passed",
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
