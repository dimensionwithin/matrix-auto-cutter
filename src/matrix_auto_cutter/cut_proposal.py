"""Conservative, source-bound silence cut proposals without media output."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)

from matrix_auto_cutter.intro import (
    INTRO_FLOW_PROTECTED_FRAMES,
    IntroCandidateEvidence,
    IntroResolutionEvidence,
    IntroResolvedStatus,
    is_resolved,
    resolve_intro,
)
from matrix_auto_cutter.models import (
    CanonicalModel,
    MaterializedFrameRange,
    Sha256,
    SourceIdentity,
)
from matrix_auto_cutter.outro import (
    OutroCandidateEvidence,
    OutroResolutionEvidence,
    default_binding_path,
    load_binding,
    resolve_outro,
)
from matrix_auto_cutter.phase2.source_confirmation.identity import source_identity_digest
from matrix_auto_cutter.protection import materialize_protection_with_outro
from matrix_auto_cutter.sidecar import ValidatedObsEventSidecar, validate_sidecar
from matrix_auto_cutter.timebase import Frame, FrameRange

ANALYSIS_VERSION: Literal["silence_dead_air/1.0"] = "silence_dead_air/1.0"
PROPOSAL_SCHEMA_VERSION: Literal["1.1"] = "1.1"
PROPOSAL_FILE_NAME = "cut-proposal.json"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FFMPEG_LOG_BYTES = 2 * 1024 * 1024
_DIGEST_DOMAIN_V10 = b"matrix-auto-cutter/cut-proposal/1.0\0"
_DIGEST_DOMAIN_V11 = b"matrix-auto-cutter/cut-proposal/1.1\0"
_SILENCE_START = re.compile(rb"silence_start:\s*(-?[0-9]+(?:\.[0-9]+)?)")
_SILENCE_END = re.compile(
    rb"silence_end:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*"
    rb"(-?[0-9]+(?:\.[0-9]+)?)"
)


class AnalysisParameters(CanonicalModel):
    """Materialized conservative rule set for one proposal generation."""

    silence_threshold_db: int = Field(default=-35, ge=-90, le=-20)
    minimum_silence_ms: int = Field(default=1200, ge=500, le=60_000)
    handle_before_ms: int = Field(default=350, ge=0, le=5_000)
    handle_after_ms: int = Field(default=350, ge=0, le=5_000)
    minimum_cut_ms: int = Field(default=500, ge=100, le=60_000)
    minimum_keep_island_ms: int = Field(default=500, ge=0, le=60_000)
    fps_num: Literal[60] = 60
    fps_den: Literal[1] = 1
    cut_rounding: Literal["inward"] = "inward"
    protection_rounding: Literal["existing_sidecar_outward"] = "existing_sidecar_outward"


class FfmpegIdentity(CanonicalModel):
    """Exact executable evidence used by the silence analysis."""

    absolute_path: str = Field(min_length=1)
    file_name: Literal["ffmpeg.exe"]
    size_bytes: int = Field(ge=1)
    sha256: Sha256
    version_line: str = Field(min_length=1, max_length=1000)


class AudioEvidence(CanonicalModel):
    """Measured FFmpeg silence evidence behind one candidate."""

    detector: Literal["ffmpeg_silencedetect"] = "ffmpeg_silencedetect"
    raw_silence_start_ms: int = Field(ge=0)
    raw_silence_end_ms: int = Field(gt=0)
    raw_silence_duration_ms: int = Field(ge=0)
    threshold_db: int
    minimum_detector_duration_ms: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered(self) -> AudioEvidence:
        """Reject non-positive or inconsistent measured intervals."""
        if self.raw_silence_start_ms >= self.raw_silence_end_ms:
            raise ValueError("audio evidence requires start < end")
        return self


class AppliedHandles(CanonicalModel):
    """Silence retained around audible content."""

    before_ms: int = Field(ge=0)
    after_ms: int = Field(ge=0)


class ProposedCut(CanonicalModel):
    """One non-empty half-open cut interval on the existing 60 FPS timeline."""

    candidate_id: str = Field(pattern=r"^candidate-[0-9a-f]{24}$")
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    start_timecode: str = Field(pattern=r"^[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}$")
    end_timecode: str = Field(pattern=r"^[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}$")
    duration_ms: int = Field(gt=0)
    reason: Literal["conservative_silence_dead_air", "outro_excess_tail", "intro_lead_in"]
    audio_evidence: AudioEvidence | None = None
    outro_evidence: OutroCandidateEvidence | None = None
    intro_evidence: IntroCandidateEvidence | None = None
    applied_handles: AppliedHandles | None = None
    protection_result: Literal[
        "clear_no_blocking_overlap",
        "outro_tail_after_hard_protection",
        "intro_lead_in_overrides_protection",
    ]

    @model_validator(mode="after")
    def nonempty(self) -> ProposedCut:
        """Keep the artifact interval contract explicit."""
        if any(
            name in self.model_fields_set and getattr(self, name) is None
            for name in ("audio_evidence", "outro_evidence", "intro_evidence", "applied_handles")
        ):
            raise ValueError("candidate evidence fields may not be explicit null")
        if self.start_frame >= self.end_frame:
            raise ValueError("proposed cut requires start_frame < end_frame")
        if self.reason == "conservative_silence_dead_air" and (
            self.audio_evidence is None
            or self.applied_handles is None
            or self.outro_evidence is not None
            or self.intro_evidence is not None
            or self.protection_result != "clear_no_blocking_overlap"
        ):
            raise ValueError("silence cut evidence is incomplete")
        if self.reason == "outro_excess_tail" and (
            self.outro_evidence is None
            or self.audio_evidence is not None
            or self.intro_evidence is not None
            or self.applied_handles is not None
            or self.protection_result != "outro_tail_after_hard_protection"
            or self.start_frame != self.outro_evidence.tail_start_frame
            or self.end_frame != self.outro_evidence.total_source_frames
        ):
            raise ValueError("outro tail evidence is incomplete")
        if self.reason == "intro_lead_in" and (
            self.intro_evidence is None
            or self.audio_evidence is not None
            or self.outro_evidence is not None
            or self.applied_handles is not None
            or self.protection_result != "intro_lead_in_overrides_protection"
            or self.start_frame != 0
            or self.end_frame != self.intro_evidence.intro_start_frame
            or self.duration_ms != self.intro_evidence.removed_ms
        ):
            raise ValueError("intro lead-in evidence is incomplete")
        return self

    @model_serializer(mode="wrap")
    def omit_missing_evidence(self, handler: SerializerFunctionWrapHandler) -> object:
        """Keep legacy silence bytes unchanged by omitting absent typed evidence."""
        serialized: dict[str, object] = handler(self)
        for name in ("audio_evidence", "outro_evidence", "intro_evidence", "applied_handles"):
            if getattr(self, name) is None:
                serialized.pop(name, None)
        return serialized


RejectionReason = Literal[
    "below_minimum_silence",
    "below_minimum_cut_after_handles",
    "hard_protection_overlap",
    "soft_or_uncertain_protection_overlap",
    "minimum_keep_island",
    "outside_source_timeline",
    "superseded_by_outro_tail",
    "superseded_by_intro_lead_in",
    "intro_flow_protected",
]


class RejectedCandidate(CanonicalModel):
    """Bounded transparent explanation for a measured but unused candidate."""

    candidate_id: str = Field(pattern=r"^candidate-[0-9a-f]{24}$")
    raw_silence_start_ms: int = Field(ge=0)
    raw_silence_end_ms: int = Field(ge=0)
    reason: RejectionReason
    protection_ids: tuple[str, ...] = ()


class RejectionCount(CanonicalModel):
    """Stable aggregate count for review and automation."""

    reason: RejectionReason
    count: int = Field(ge=1)


class CutProposalContent(CanonicalModel):
    """Digest input: every immutable proposal field except the digest itself."""

    artifact_type: Literal["matrix_auto_cutter_cut_proposal"]
    schema_version: Literal["1.0", "1.1"]
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    recording_id: str = Field(min_length=1, max_length=100)
    source_path: str = Field(min_length=1)
    source_identity: SourceIdentity
    source_identity_digest: Sha256
    sidecar_path: str = Field(min_length=1)
    sidecar_sha256: Sha256
    analysis_version: Literal["silence_dead_air/1.0"]
    ffmpeg: FfmpegIdentity
    analysis_parameters: AnalysisParameters
    source_duration_ms: int = Field(ge=1)
    source_frame_count: int = Field(ge=1)
    status: Literal["ready", "no_cuts"]
    proposed_cuts: tuple[ProposedCut, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]
    rejection_counts: tuple[RejectionCount, ...]
    total_proposed_cuts: int = Field(ge=0)
    total_proposed_savings_ms: int = Field(ge=0)
    generated_at: AwareDatetime
    outro_resolution: OutroResolutionEvidence | None = None
    intro_resolution: IntroResolutionEvidence | None = None

    @model_validator(mode="after")
    def internally_consistent(self) -> CutProposalContent:
        """Reject reordered, overlapping, miscounted, or cross-source content."""
        if self.recording_id != str(self.recording_id):
            raise ValueError("recording_id is invalid")
        if self.source_identity_digest != source_identity_digest(self.source_identity):
            raise ValueError("source identity digest mismatch")
        if self.source_duration_ms != self.source_identity.duration_ms:
            raise ValueError("source duration mismatch")
        if self.source_frame_count != self.source_identity.video_frame_count:
            raise ValueError("source frame count mismatch")
        if self.total_proposed_cuts != len(self.proposed_cuts):
            raise ValueError("proposed cut count mismatch")
        if self.total_proposed_savings_ms != sum(item.duration_ms for item in self.proposed_cuts):
            raise ValueError("proposed savings mismatch")
        if self.status == "ready" and not self.proposed_cuts:
            raise ValueError("ready proposal requires at least one cut")
        if self.status == "no_cuts" and self.proposed_cuts:
            raise ValueError("no_cuts proposal cannot contain cuts")
        if self.schema_version == "1.0" and (
            self.outro_resolution is not None or self.intro_resolution is not None
        ):
            raise ValueError("proposal-1.0 cannot contain outro or intro resolution")
        # ``intro_resolution`` was added to Proposal-1.1 in place instead of via a
        # new schema version, so 1.1 bytes published before the field existed must
        # keep loading; their canonical bytes and digest omit it either way.
        if self.schema_version == "1.1" and self.outro_resolution is None:
            raise ValueError("proposal-1.1 requires typed outro resolution")
        if "outro_resolution" in self.model_fields_set and self.outro_resolution is None:
            raise ValueError("outro_resolution may not be explicit null")
        if "intro_resolution" in self.model_fields_set and self.intro_resolution is None:
            raise ValueError("intro_resolution may not be explicit null")
        previous_end = -1
        for item in self.proposed_cuts:
            if item.start_frame < previous_end or item.end_frame > self.source_frame_count:
                raise ValueError("proposal cuts must be sorted, disjoint, and in bounds")
            previous_end = item.end_frame
        expected_counts = Counter(item.reason for item in self.rejected_candidates)
        observed_counts = Counter({item.reason: item.count for item in self.rejection_counts})
        if expected_counts != observed_counts:
            raise ValueError("rejection counts mismatch")
        return self

    @model_serializer(mode="wrap")
    def omit_missing_outro_resolution(self, handler: SerializerFunctionWrapHandler) -> object:
        """Preserve canonical Proposal-1.0 bytes when the fields are absent."""
        serialized: dict[str, object] = handler(self)
        if self.outro_resolution is None:
            serialized.pop("outro_resolution", None)
        if self.intro_resolution is None:
            serialized.pop("intro_resolution", None)
        return serialized


class CutProposal(CutProposalContent):
    """Strict immutable cut-proposal artifact including its domain digest."""

    proposal_digest: Sha256

    @model_validator(mode="after")
    def digest_matches(self) -> CutProposal:
        """Bind the embedded digest to every other canonical field."""
        content = _content_from_proposal(self)
        if self.proposal_digest != proposal_content_digest(content):
            raise ValueError("proposal digest mismatch")
        return self


@dataclass(frozen=True, slots=True)
class SilenceInterval:
    """Exact seconds emitted by FFmpeg silencedetect."""

    start_seconds: Decimal
    end_seconds: Decimal
    measured_duration_seconds: Decimal


@dataclass(frozen=True, slots=True)
class FfmpegProcessResult:
    """Bounded process outcome used by the analyzer and tests."""

    exit_code: int
    stderr: bytes
    timed_out: bool = False
    output_truncated: bool = False


@dataclass(frozen=True, slots=True)
class ProposalReady:
    """Published or strictly reused immutable proposal generation."""

    proposal: CutProposal
    proposal_path: Path
    proposal_sha256: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ProposalFailed:
    """Stable fail-closed analysis result containing no cut authorization."""

    code: str
    message_de: str


type ProposalResult = ProposalReady | ProposalFailed
type ProcessRunner = Callable[[Sequence[str], int], FfmpegProcessResult]


def _content_from_proposal(proposal: CutProposal) -> CutProposalContent:
    return CutProposalContent.model_validate_json(
        proposal.model_dump_json(exclude={"proposal_digest"})
    )


def proposal_content_digest(content: CutProposalContent) -> str:
    """Digest every canonical immutable content field with domain separation."""
    domain = _DIGEST_DOMAIN_V10 if content.schema_version == "1.0" else _DIGEST_DOMAIN_V11
    return hashlib.sha256(domain + content.model_dump_json().encode("utf-8")).hexdigest()


def _proposal_from_content(content: CutProposalContent) -> CutProposal:
    payload = json.loads(content.model_dump_json())
    payload["proposal_digest"] = proposal_content_digest(content)
    return CutProposal.model_validate_json(json.dumps(payload, ensure_ascii=False))


def proposal_bytes(proposal: CutProposal) -> bytes:
    """Return the sole canonical on-disk representation."""
    return (proposal.model_dump_json() + "\n").encode("utf-8")


def proposal_file_sha256(proposal: CutProposal) -> str:
    """Hash the exact canonical bytes used for approval binding."""
    return hashlib.sha256(proposal_bytes(proposal)).hexdigest()


def load_proposal(path: Path) -> ProposalReady | ProposalFailed:
    """Strictly load canonical bytes, validate the digest, and reject foreign encoding."""
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_JSON_BYTES:
            raise ValueError("proposal size is outside the contract")
        proposal = CutProposal.model_validate_json(data)
        canonical = proposal_bytes(proposal)
        if data != canonical:
            raise ValueError("proposal bytes are not canonical")
        return ProposalReady(proposal, path, hashlib.sha256(data).hexdigest(), True)
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        return ProposalFailed("E_PROPOSAL_INVALID", f"Proposal ist ungültig: {exc}")


def _atomic_create(path: Path, data: bytes) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{id(data)}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.rename(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def discover_ffmpeg(explicit_path: str | None = None) -> Path | None:
    """Resolve exactly one existing absolute ffmpeg.exe candidate without downloading."""
    candidate = explicit_path or shutil.which("ffmpeg")
    if not candidate:
        return None
    path = Path(candidate)
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if (
        not resolved.is_absolute()
        or not resolved.is_file()
        or resolved.name.casefold() != "ffmpeg.exe"
    ):
        return None
    return resolved


def _bounded_process(arguments: Sequence[str], timeout_seconds: int) -> FfmpegProcessResult:
    """Run one argument-vector process while retaining only a bounded diagnostic log."""
    with tempfile.TemporaryFile(mode="w+b") as log:
        try:
            process = subprocess.Popen(
                list(arguments),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                shell=False,
            )
            try:
                exit_code = process.wait(timeout=timeout_seconds)
                timed_out = False
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                exit_code = process.returncode
                timed_out = True
        except OSError as exc:
            return FfmpegProcessResult(-1, str(exc).encode("utf-8", errors="replace"))
        size = log.tell()
        truncated = size > MAX_FFMPEG_LOG_BYTES
        log.seek(max(0, size - MAX_FFMPEG_LOG_BYTES))
        output = log.read(MAX_FFMPEG_LOG_BYTES)
    return FfmpegProcessResult(exit_code, output, timed_out, truncated)


def validate_ffmpeg(
    path: Path,
    *,
    process_runner: ProcessRunner = _bounded_process,
) -> FfmpegIdentity | ProposalFailed:
    """Validate name/path/file, full binary digest, and a bounded version invocation."""
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_absolute() or not resolved.is_file():
            raise ValueError("FFmpeg-Pfad ist keine existierende reguläre Datei")
        if resolved.name.casefold() != "ffmpeg.exe":
            raise ValueError("erwartete ausführbare Datei heißt ffmpeg.exe")
        stat_before = resolved.stat()
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        stat_after = resolved.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns) != (
            stat_after.st_size,
            stat_after.st_mtime_ns,
        ):
            raise ValueError("FFmpeg-Binary änderte sich während der Validierung")
        version = process_runner((str(resolved), "-version"), 15)
        if version.timed_out or version.exit_code != 0 or version.output_truncated:
            raise ValueError("FFmpeg-Version konnte nicht zuverlässig abgerufen werden")
        lines = version.stderr.splitlines()
        if not lines:
            raise ValueError("FFmpeg-Versionausgabe ist leer")
        first = lines[0].decode("utf-8", errors="replace")[:1000]
        if not first.casefold().startswith("ffmpeg version "):
            raise ValueError("unerwartete FFmpeg-Versionausgabe")
        return FfmpegIdentity(
            absolute_path=str(resolved),
            file_name="ffmpeg.exe",
            size_bytes=stat_after.st_size,
            sha256=digest.hexdigest(),
            version_line=first,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return ProposalFailed("E_FFMPEG_UNAVAILABLE", f"FFmpeg ist nicht verwendbar: {exc}")


def _load_valid_sidecar(
    source_path: Path, sidecar_path: Path
) -> tuple[ValidatedObsEventSidecar, str, str] | ProposalFailed:
    try:
        sidecar_data = sidecar_path.read_bytes()
        if not sidecar_data or len(sidecar_data) > MAX_JSON_BYTES:
            raise ValueError("Sidecar-Größe liegt außerhalb des Vertrags")
        raw = json.loads(sidecar_data, parse_float=Decimal)
        if not isinstance(raw, Mapping):
            raise ValueError("Sidecar-Wurzel ist kein JSON-Objekt")
        source_raw = raw.get("source")
        if not isinstance(source_raw, Mapping):
            raise ValueError("Sidecar enthält keine SourceIdentity")
        expected = SourceIdentity.model_validate_json(json.dumps(source_raw))
        validated = validate_sidecar(raw, expected)
        if (
            validated.mode not in {"validated_sidecar_1_1", "validated_sidecar_1_2"}
            or validated.sidecar is None
        ):
            reasons = ", ".join(reason.code.value for reason in validated.reasons)
            raise ValueError(f"Sidecar-Validierung fehlgeschlagen: {reasons}")
        sidecar = validated.sidecar
        stat_before = source_path.stat()
        if not source_path.is_file() or source_path.name != sidecar.source.file_name:
            raise ValueError("Sourcepfad stimmt nicht mit der Sidecar-Identität überein")
        if stat_before.st_size != sidecar.source.size_bytes:
            raise ValueError("Sourcegröße stimmt nicht mit der Sidecar-Identität überein")
        digest = hashlib.sha256()
        with source_path.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        stat_after = source_path.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns) != (
            stat_after.st_size,
            stat_after.st_mtime_ns,
        ):
            raise ValueError("Source änderte sich während der read-only Identitätsprüfung")
        if digest.hexdigest() != sidecar.source.sha256:
            raise ValueError("Source-SHA-256 stimmt nicht mit dem Sidecar überein")
        return (
            sidecar,
            hashlib.sha256(sidecar_data).hexdigest(),
            source_identity_digest(sidecar.source),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        return ProposalFailed(
            "E_SIDECAR_SOURCE_BINDING",
            f"Kein zeitentfernender Vorschlag: Sidecar oder Source-Bindung ungültig: {exc}",
        )


def _parse_silences(output: bytes, source_duration_ms: int) -> tuple[SilenceInterval, ...]:
    result: list[SilenceInterval] = []
    pending_start: Decimal | None = None
    for line in output.splitlines():
        start_match = _SILENCE_START.search(line)
        if start_match is not None:
            pending_start = Decimal(start_match.group(1).decode("ascii"))
        end_match = _SILENCE_END.search(line)
        if end_match is not None and pending_start is not None:
            end = Decimal(end_match.group(1).decode("ascii"))
            duration = Decimal(end_match.group(2).decode("ascii"))
            maximum = Decimal(source_duration_ms) / Decimal(1000)
            start = max(Decimal(0), min(pending_start, maximum))
            end = max(Decimal(0), min(end, maximum))
            if start < end:
                result.append(SilenceInterval(start, end, duration))
            pending_start = None
    return tuple(result)


def _run_silence_analysis(
    source_path: Path,
    ffmpeg: FfmpegIdentity,
    parameters: AnalysisParameters,
    timeout_seconds: int,
    process_runner: ProcessRunner,
) -> tuple[SilenceInterval, ...] | ProposalFailed:
    filter_value = (
        f"silencedetect=noise={parameters.silence_threshold_db}dB:"
        f"d={Decimal(parameters.minimum_silence_ms) / Decimal(1000)}"
    )
    arguments = (
        ffmpeg.absolute_path,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-v",
        "info",
        "-i",
        str(source_path),
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        filter_value,
        "-f",
        "null",
        "NUL",
    )
    result = process_runner(arguments, timeout_seconds)
    if result.timed_out:
        return ProposalFailed("E_FFMPEG_TIMEOUT", "FFmpeg-Audioanalyse überschritt das Zeitlimit.")
    if result.output_truncated:
        return ProposalFailed(
            "E_FFMPEG_OUTPUT_LIMIT",
            "FFmpeg-Ausgabe überschritt das Sicherheitslimit; kein Vorschlag wurde erzeugt.",
        )
    if result.exit_code != 0:
        detail = result.stderr[-1000:].decode("utf-8", errors="replace")
        return ProposalFailed(
            "E_FFMPEG_ANALYSIS",
            f"FFmpeg-Audioanalyse scheiterte mit Exitcode {result.exit_code}: {detail}",
        )
    return _parse_silences(result.stderr, 2**63 - 1)


def _milliseconds(seconds: Decimal) -> int:
    return int((seconds * Decimal(1000)).to_integral_value(rounding=ROUND_HALF_UP))


def _ceil_frame(seconds: Decimal) -> int:
    return int((seconds * Decimal(60)).to_integral_value(rounding=ROUND_CEILING))


def _floor_frame(seconds: Decimal) -> int:
    return int((seconds * Decimal(60)).to_integral_value(rounding=ROUND_FLOOR))


def _duration_ms(frames: int) -> int:
    return int((Decimal(frames) * Decimal(1000) / Decimal(60)).to_integral_value())


def _timecode(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _candidate_id(seed: str, start_ms: int, end_ms: int) -> str:
    value = hashlib.sha256(f"{seed}\0{start_ms}\0{end_ms}".encode()).hexdigest()[:24]
    return f"candidate-{value}"


def _normalize_silences(values: Sequence[SilenceInterval]) -> tuple[SilenceInterval, ...]:
    ordered = sorted(values, key=lambda item: (item.start_seconds, item.end_seconds))
    normalized: list[SilenceInterval] = []
    for item in ordered:
        if item.start_seconds >= item.end_seconds:
            continue
        if normalized and item.start_seconds <= normalized[-1].end_seconds:
            previous = normalized.pop()
            end = max(previous.end_seconds, item.end_seconds)
            normalized.append(
                SilenceInterval(
                    previous.start_seconds,
                    end,
                    end - previous.start_seconds,
                )
            )
        else:
            normalized.append(item)
    return tuple(normalized)


def build_cut_candidates(
    silences: Sequence[SilenceInterval],
    source: SourceIdentity,
    protection_ranges: tuple[MaterializedFrameRange, ...],
    parameters: AnalysisParameters,
    candidate_seed: str,
) -> tuple[tuple[ProposedCut, ...], tuple[RejectedCandidate, ...]]:
    """Map measured silence inward to frames and conservatively apply protection."""
    proposed: list[ProposedCut] = []
    rejected: list[RejectedCandidate] = []
    handle_before = Decimal(parameters.handle_before_ms) / Decimal(1000)
    handle_after = Decimal(parameters.handle_after_ms) / Decimal(1000)
    minimum_silence = Decimal(parameters.minimum_silence_ms) / Decimal(1000)
    minimum_cut_frames = math.ceil(parameters.minimum_cut_ms * 60 / 1000)
    minimum_island_frames = math.ceil(parameters.minimum_keep_island_ms * 60 / 1000)
    for silence in _normalize_silences(silences):
        raw_start_ms = max(0, _milliseconds(silence.start_seconds))
        raw_end_ms = max(raw_start_ms, _milliseconds(silence.end_seconds))
        candidate_id = _candidate_id(candidate_seed, raw_start_ms, raw_end_ms)
        if silence.end_seconds - silence.start_seconds < minimum_silence:
            rejected.append(
                RejectedCandidate(
                    candidate_id=candidate_id,
                    raw_silence_start_ms=raw_start_ms,
                    raw_silence_end_ms=raw_end_ms,
                    reason="below_minimum_silence",
                )
            )
            continue
        start_frame = max(0, _ceil_frame(silence.start_seconds + handle_before))
        end_frame = min(source.video_frame_count, _floor_frame(silence.end_seconds - handle_after))
        if start_frame >= source.video_frame_count or end_frame <= 0:
            reason: RejectionReason = "outside_source_timeline"
        elif end_frame - start_frame < minimum_cut_frames:
            reason = "below_minimum_cut_after_handles"
        else:
            reason = "outside_source_timeline"  # replaced below when accepted
            candidate_range = FrameRange(Frame(start_frame), Frame(end_frame))
            conflicts = tuple(
                item
                for item in protection_ranges
                if item.policy.blocks_time_edits
                and candidate_range.intersects(
                    FrameRange(Frame(item.source_start_frame), Frame(item.source_end_frame))
                )
            )
            if conflicts:
                hard = any(item.level.value == "hard" for item in conflicts)
                rejected.append(
                    RejectedCandidate(
                        candidate_id=candidate_id,
                        raw_silence_start_ms=raw_start_ms,
                        raw_silence_end_ms=raw_end_ms,
                        reason=(
                            "hard_protection_overlap"
                            if hard
                            else "soft_or_uncertain_protection_overlap"
                        ),
                        protection_ids=tuple(item.protection_id for item in conflicts),
                    )
                )
                continue
            if proposed and start_frame - proposed[-1].end_frame < minimum_island_frames:
                rejected.append(
                    RejectedCandidate(
                        candidate_id=candidate_id,
                        raw_silence_start_ms=raw_start_ms,
                        raw_silence_end_ms=raw_end_ms,
                        reason="minimum_keep_island",
                    )
                )
                continue
            start_ms = _duration_ms(start_frame)
            end_ms = _duration_ms(end_frame)
            proposed.append(
                ProposedCut(
                    candidate_id=candidate_id,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    start_timecode=_timecode(start_ms),
                    end_timecode=_timecode(end_ms),
                    duration_ms=_duration_ms(end_frame - start_frame),
                    reason="conservative_silence_dead_air",
                    audio_evidence=AudioEvidence(
                        raw_silence_start_ms=raw_start_ms,
                        raw_silence_end_ms=raw_end_ms,
                        raw_silence_duration_ms=max(
                            0, _milliseconds(silence.measured_duration_seconds)
                        ),
                        threshold_db=parameters.silence_threshold_db,
                        minimum_detector_duration_ms=parameters.minimum_silence_ms,
                    ),
                    applied_handles=AppliedHandles(
                        before_ms=parameters.handle_before_ms,
                        after_ms=parameters.handle_after_ms,
                    ),
                    protection_result="clear_no_blocking_overlap",
                )
            )
            continue
        rejected.append(
            RejectedCandidate(
                candidate_id=candidate_id,
                raw_silence_start_ms=raw_start_ms,
                raw_silence_end_ms=raw_end_ms,
                reason=reason,
            )
        )
    return tuple(proposed), tuple(rejected)


def _proposal_id(
    source_digest: str,
    sidecar_sha256: str,
    ffmpeg: FfmpegIdentity,
    parameters: AnalysisParameters,
    outro_resolution: OutroResolutionEvidence,
    intro_resolution: IntroResolutionEvidence,
) -> str:
    payload = "\0".join(
        (
            ANALYSIS_VERSION,
            source_digest,
            sidecar_sha256,
            ffmpeg.sha256,
            parameters.model_dump_json(),
            outro_resolution.model_dump_json(),
            intro_resolution.model_dump_json(),
        )
    ).encode("utf-8")
    return f"proposal-{hashlib.sha256(payload).hexdigest()[:32]}"


def generate_proposal(
    source_path: Path,
    sidecar_path: Path,
    recording_id: str,
    artifacts_root: Path,
    ffmpeg_path: Path,
    *,
    parameters: AnalysisParameters | None = None,
    timeout_seconds: int = 600,
    process_runner: ProcessRunner = _bounded_process,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    outro_binding_path: Path | None = None,
    obs_scene_collections_root: Path | None = None,
) -> ProposalResult:
    """Validate all bindings, analyze read-only, and atomically publish one generation."""
    rules = parameters or AnalysisParameters()
    loaded = _load_valid_sidecar(source_path, sidecar_path)
    if isinstance(loaded, ProposalFailed):
        return loaded
    sidecar, sidecar_sha256, source_digest = loaded
    if str(sidecar.recording_session_id) != recording_id:
        return ProposalFailed(
            "E_RECORDING_BINDING",
            "Sidecar-Recording-ID stimmt nicht mit der Runner-Session überein.",
        )
    binary = validate_ffmpeg(ffmpeg_path, process_runner=process_runner)
    if isinstance(binary, ProposalFailed):
        return binary
    binding_path = outro_binding_path or default_binding_path()
    binding = load_binding(binding_path)
    collections_root = obs_scene_collections_root or (
        Path(os.environ.get("APPDATA", "")) / "obs-studio" / "basic" / "scenes"
    )
    collection_file = (
        collections_root / f"{binding.scene_collection_name}.json"
        if binding is not None
        else collections_root / "__binding_unavailable__.json"
    )
    outro_resolution = resolve_outro(
        sidecar,
        sidecar_sha256=sidecar_sha256,
        binding_path=binding_path,
        collection_file=collection_file,
    )
    intro_resolution = resolve_intro(
        sidecar,
        sidecar_sha256=sidecar_sha256,
        outro_tail_start_frame=outro_resolution.tail_start_frame,
    )
    proposal_id = _proposal_id(
        source_digest, sidecar_sha256, binary, rules, outro_resolution, intro_resolution
    )
    target = artifacts_root / recording_id / "proposals" / proposal_id / PROPOSAL_FILE_NAME
    if target.exists():
        reused = load_proposal(target)
        if isinstance(reused, ProposalFailed):
            return reused
        proposal = reused.proposal
        expected = (
            proposal.proposal_id == proposal_id
            and proposal.recording_id == recording_id
            and proposal.source_identity == sidecar.source
            and proposal.source_identity_digest == source_digest
            and proposal.sidecar_sha256 == sidecar_sha256
            and proposal.ffmpeg == binary
            and proposal.analysis_parameters == rules
            and proposal.outro_resolution == outro_resolution
            and proposal.intro_resolution == intro_resolution
        )
        if not expected:
            return ProposalFailed(
                "E_PROPOSAL_REUSE_BINDING",
                "Vorhandenes Proposal passt nicht vollständig zu Source, Sidecar und Analyse.",
            )
        return reused
    protection = materialize_protection_with_outro(sidecar, outro_resolution)
    if protection.status != "materialized":
        return ProposalFailed(
            "E_PROTECTION",
            "Sidecar-Protection konnte nicht materialisiert werden; kein Schnitt vorgeschlagen.",
        )
    analysis = _run_silence_analysis(
        source_path,
        binary,
        rules,
        timeout_seconds,
        process_runner,
    )
    if isinstance(analysis, ProposalFailed):
        return analysis
    # Clamp parsed output only after the authoritative sidecar duration is known.
    maximum_seconds = Decimal(sidecar.source.duration_ms) / Decimal(1000)
    clamped = tuple(
        SilenceInterval(
            max(Decimal(0), min(item.start_seconds, maximum_seconds)),
            max(Decimal(0), min(item.end_seconds, maximum_seconds)),
            item.measured_duration_seconds,
        )
        for item in analysis
        if max(Decimal(0), min(item.start_seconds, maximum_seconds))
        < max(Decimal(0), min(item.end_seconds, maximum_seconds))
    )
    proposed, rejected = build_cut_candidates(
        clamped,
        sidecar.source,
        protection.ranges,
        rules,
        proposal_id,
    )
    if outro_resolution.status == "resolved" and outro_resolution.tail_start_frame is not None:
        assert outro_resolution.binding_digest is not None
        assert outro_resolution.binding_file_sha256 is not None
        assert outro_resolution.scene_event_id is not None
        assert outro_resolution.scene_uuid is not None
        assert outro_resolution.scene_name is not None
        assert outro_resolution.outro_start_frame is not None
        assert outro_resolution.protected_start_frame is not None
        assert outro_resolution.protected_end_frame is not None
        tail = ProposedCut(
            candidate_id=_candidate_id(
                proposal_id + "\0outro_excess_tail",
                outro_resolution.tail_start_frame,
                sidecar.source.video_frame_count,
            ),
            start_frame=outro_resolution.tail_start_frame,
            end_frame=sidecar.source.video_frame_count,
            start_timecode=_timecode(_duration_ms(outro_resolution.tail_start_frame)),
            end_timecode=_timecode(_duration_ms(sidecar.source.video_frame_count)),
            duration_ms=_duration_ms(
                sidecar.source.video_frame_count - outro_resolution.tail_start_frame
            ),
            reason="outro_excess_tail",
            outro_evidence=OutroCandidateEvidence(
                binding_digest=outro_resolution.binding_digest,
                binding_file_sha256=outro_resolution.binding_file_sha256,
                sidecar_sha256=sidecar_sha256,
                scene_event_id=outro_resolution.scene_event_id,
                scene_uuid=outro_resolution.scene_uuid,
                scene_name=outro_resolution.scene_name,
                outro_start_frame=outro_resolution.outro_start_frame,
                protected_start_frame=outro_resolution.protected_start_frame,
                protected_end_frame=outro_resolution.protected_end_frame,
                tail_start_frame=outro_resolution.tail_start_frame,
                total_source_frames=sidecar.source.video_frame_count,
            ),
            protection_result="outro_tail_after_hard_protection",
        )
        kept: list[ProposedCut] = []
        for item in proposed:
            if item.start_frame < tail.end_frame and tail.start_frame < item.end_frame:
                assert item.audio_evidence is not None
                rejected = (*rejected,
                    RejectedCandidate(
                        candidate_id=item.candidate_id,
                        raw_silence_start_ms=item.audio_evidence.raw_silence_start_ms,
                        raw_silence_end_ms=item.audio_evidence.raw_silence_end_ms,
                        reason="superseded_by_outro_tail",
                    ),
                )
            else:
                kept.append(item)
        proposed = (*kept, tail)
    if is_resolved(intro_resolution):
        assert intro_resolution.binding_basis is not None
        assert intro_resolution.scene_event_id is not None
        assert intro_resolution.intro_start_frame is not None
        assert intro_resolution.removed_frames is not None
        assert intro_resolution.removed_ms is not None
        resolved_status: IntroResolvedStatus = (
            "resolved_first_of_multiple"
            if intro_resolution.status == "resolved_first_of_multiple"
            else "resolved"
        )
        lead_in = ProposedCut(
            candidate_id=_candidate_id(
                proposal_id + "\0intro_lead_in",
                0,
                intro_resolution.intro_start_frame,
            ),
            start_frame=0,
            end_frame=intro_resolution.intro_start_frame,
            start_timecode=_timecode(0),
            end_timecode=_timecode(_duration_ms(intro_resolution.intro_start_frame)),
            duration_ms=_duration_ms(intro_resolution.intro_start_frame),
            reason="intro_lead_in",
            intro_evidence=IntroCandidateEvidence(
                sidecar_sha256=sidecar_sha256,
                binding_basis=intro_resolution.binding_basis,
                scene_event_id=intro_resolution.scene_event_id,
                scene_uuid=intro_resolution.scene_uuid,
                scene_name=intro_resolution.scene_name,
                intro_start_frame=intro_resolution.intro_start_frame,
                removed_frames=intro_resolution.removed_frames,
                removed_ms=intro_resolution.removed_ms,
                matching_scene_event_count=intro_resolution.matching_scene_event_count,
                total_source_frames=sidecar.source.video_frame_count,
                resolution_status=resolved_status,
            ),
            protection_result="intro_lead_in_overrides_protection",
        )
        # The lead-in introduces a new cut boundary that ``build_cut_candidates``
        # never saw, so the existing minimum-keep-island rule is re-applied across
        # it.  Without this the renderer would refuse the whole proposal over a
        # micro segment between the lead-in and the first surviving silence.
        minimum_island_frames = max(1, math.ceil(rules.minimum_keep_island_ms * 60 / 1000))
        # Halboffene Einstiegszone ``[lead_in.end_frame, flow_end)``: ein
        # Kandidat, der davor beginnt, fällt ganz weg, auch wenn er weit darüber
        # hinausreicht — gekürzt wird nicht, sonst fiele der Schnitt doch noch
        # in eine bewusst gesetzte Pause.  Ein Kandidat, der genau auf
        # ``flow_end`` beginnt, liegt schon außerhalb und bleibt.
        flow_end = lead_in.end_frame + INTRO_FLOW_PROTECTED_FRAMES
        retained: list[ProposedCut] = []
        for item in proposed:
            if item.start_frame < lead_in.end_frame and lead_in.start_frame < item.end_frame:
                assert item.audio_evidence is not None
                rejected = (*rejected,
                    RejectedCandidate(
                        candidate_id=item.candidate_id,
                        raw_silence_start_ms=item.audio_evidence.raw_silence_start_ms,
                        raw_silence_end_ms=item.audio_evidence.raw_silence_end_ms,
                        reason="superseded_by_intro_lead_in",
                    ),
                )
            elif item.audio_evidence is not None and item.start_frame < flow_end:
                # Beginnt in der Einstiegszone: der gestaltete Einstieg bleibt
                # am Stück. Ab ``flow_end`` arbeitet der Cutter unverändert.
                rejected = (
                    *rejected,
                    RejectedCandidate(
                        candidate_id=item.candidate_id,
                        raw_silence_start_ms=item.audio_evidence.raw_silence_start_ms,
                        raw_silence_end_ms=item.audio_evidence.raw_silence_end_ms,
                        reason="intro_flow_protected",
                    ),
                )
            elif (
                item.audio_evidence is not None
                and not retained
                and item.start_frame - lead_in.end_frame < minimum_island_frames
            ):
                rejected = (*rejected,
                    RejectedCandidate(
                        candidate_id=item.candidate_id,
                        raw_silence_start_ms=item.audio_evidence.raw_silence_start_ms,
                        raw_silence_end_ms=item.audio_evidence.raw_silence_end_ms,
                        reason="minimum_keep_island",
                    ),
                )
            else:
                retained.append(item)
        proposed = (lead_in, *retained)
    counts = Counter(item.reason for item in rejected)
    content = CutProposalContent(
        artifact_type="matrix_auto_cutter_cut_proposal",
        schema_version=PROPOSAL_SCHEMA_VERSION,
        proposal_id=proposal_id,
        recording_id=recording_id,
        source_path=str(source_path.resolve(strict=True)),
        source_identity=sidecar.source,
        source_identity_digest=source_digest,
        sidecar_path=str(sidecar_path.resolve(strict=True)),
        sidecar_sha256=sidecar_sha256,
        analysis_version=ANALYSIS_VERSION,
        ffmpeg=binary,
        analysis_parameters=rules,
        source_duration_ms=sidecar.source.duration_ms,
        source_frame_count=sidecar.source.video_frame_count,
        status="ready" if proposed else "no_cuts",
        proposed_cuts=proposed,
        rejected_candidates=rejected,
        rejection_counts=tuple(
            RejectionCount(reason=reason, count=counts[reason]) for reason in sorted(counts)
        ),
        total_proposed_cuts=len(proposed),
        total_proposed_savings_ms=sum(item.duration_ms for item in proposed),
        generated_at=now(),
        outro_resolution=outro_resolution,
        intro_resolution=intro_resolution,
    )
    proposal = _proposal_from_content(content)
    data = proposal_bytes(proposal)
    if not _atomic_create(target, data):
        concurrent = load_proposal(target)
        if isinstance(concurrent, ProposalFailed) or concurrent.proposal != proposal:
            return ProposalFailed(
                "E_PROPOSAL_PUBLISH_RACE",
                "Gleichzeitige Proposal-Veröffentlichung war nicht identisch.",
            )
        return concurrent
    return ProposalReady(proposal, target, hashlib.sha256(data).hexdigest(), False)
