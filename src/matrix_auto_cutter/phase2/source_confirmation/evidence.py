"""Strict canonical package-2E persistent evidence models."""

from __future__ import annotations

import hashlib
from typing import Annotated, Final, Literal, Self

from pydantic import Field, model_validator

from matrix_auto_cutter.models import CanonicalModel, SourceIdentity
from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, CanonicalUuidText
from matrix_auto_cutter.phase2.probe import (
    ExactTime,
    FinalizedStreamSelection,
    FormatProfile,
    MediaStream,
    ProbeDiagnosticProfile,
    ProbeOk,
    ProgramProfile,
    Rational,
    StreamType,
    ValidatedFfprobeBinary,
    canonical_stream_evidence_bytes,
)
from matrix_auto_cutter.phase2.snapshots import FileSnapshot, FileTime
from matrix_auto_cutter.phase2.source_hash import HashReceipt, hash_receipt_bytes

MAX_MEDIA_PROBE_BYTES = 4 * 1024 * 1024
MAX_STREAM_ASSIGNMENT_BYTES = 1024 * 1024
MAX_SOURCE_IDENTITY_EVIDENCE_BYTES = 4 * 1024 * 1024

MEDIA_PROBE_CONTRACT_VERSION: Final[Literal["media_probe/1.0"]] = "media_probe/1.0"
STREAM_ASSIGNMENT_CONTRACT_VERSION: Final[Literal["stream_assignment/1.0"]] = (
    "stream_assignment/1.0"
)
SOURCE_IDENTITY_EVIDENCE_CONTRACT_VERSION: Final[Literal["source_identity_evidence/1.0"]] = (
    "source_identity_evidence/1.0"
)
NORMALIZED_PROFILE_VERSION: Final[Literal["normalized_media_profile/1.0"]] = (
    "normalized_media_profile/1.0"
)
PROBE_PARSER_VERSION: Final[Literal["probe_json_parser/1.0"]] = "probe_json_parser/1.0"

Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
VolumeIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$")]
FileIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class ArtifactReference(CanonicalModel):
    """Digest-bound reference to one immutable project artifact."""

    artifact_type: Literal[
        "media_probe", "stream_assignment", "source_hash_receipt", "source_identity_evidence"
    ]
    schema_version: Literal["1.0"] = "1.0"
    artifact_id: str = Field(min_length=1, max_length=128)
    artifact_digest: Sha256Hex
    canonical_path: str = Field(min_length=3, max_length=32767)


class SnapshotEvidence(CanonicalModel):
    """Complete canonical projection of one comparable file snapshot."""

    evidence_version: Literal["file_snapshot/1.0"] = "file_snapshot/1.0"
    source_path: str = Field(min_length=3, max_length=32767)
    snapshot_key: Sha256Hex
    size_bytes: int = Field(ge=0)
    last_write_time_100ns: int = Field(ge=0)
    creation_time_100ns: int | None = Field(default=None, ge=0)
    change_time_100ns: int | None = Field(default=None, ge=0)
    attributes: int = Field(ge=0)
    volume_id: VolumeIdHex
    volume_id_scheme: Literal["ntfs_volume_serial"] = "ntfs_volume_serial"
    file_id: FileIdHex
    file_id_scheme: Literal["file_id_128"] = "file_id_128"

    @classmethod
    def from_snapshot(cls, snapshot: FileSnapshot) -> SnapshotEvidence:
        """Project one validated comparable snapshot without trusting its path alone."""
        if not isinstance(snapshot.volume_id, AvailableIdentity) or not isinstance(
            snapshot.file_id, AvailableIdentity
        ):
            raise ValueError("snapshot lacks comparable volume/file identity")

        def optional_time(value: object) -> int | None:
            return value.value if isinstance(value, FileTime) else None

        return cls(
            source_path=snapshot.path_ref.canonical_dos_path,
            snapshot_key=snapshot.snapshot_key,
            size_bytes=snapshot.size_bytes,
            last_write_time_100ns=snapshot.last_write_time.value,
            creation_time_100ns=optional_time(snapshot.creation_time),
            change_time_100ns=optional_time(snapshot.change_time),
            attributes=snapshot.attributes,
            volume_id=snapshot.volume_id.value,
            file_id=snapshot.file_id.value,
        )


class PathRevalidationEvidence(CanonicalModel):
    """One successful path-to-held-instance proof."""

    phase: Literal["before_probe", "before_identity_commit"]
    source_path: str = Field(min_length=3, max_length=32767)
    lease_volume_id: VolumeIdHex
    lease_file_id: FileIdHex
    same_instance: Literal[True] = True
    snapshot: SnapshotEvidence

    @model_validator(mode="after")
    def binding_matches(self) -> Self:
        """Require the measured path object to equal the held lease instance."""
        if (
            self.snapshot.source_path != self.source_path
            or self.snapshot.volume_id != self.lease_volume_id
            or self.snapshot.file_id != self.lease_file_id
        ):
            raise ValueError("path revalidation does not match the lease instance")
        return self


class RationalEvidence(CanonicalModel):
    """Exact normalized rational evidence."""

    numerator: int
    denominator: int

    @classmethod
    def from_value(cls, value: Rational | None) -> RationalEvidence | None:
        """Project an optional normalized rational."""
        return (
            None if value is None else cls(numerator=value.numerator, denominator=value.denominator)
        )


class TimeEvidence(CanonicalModel):
    """Exact decimal time with its original ffprobe lexeme."""

    value: str = Field(min_length=1, max_length=4096)
    lexeme: str = Field(min_length=1, max_length=4096)


class DispositionEvidence(CanonicalModel):
    """Closed disposition projection used by assignments."""

    default: bool | None
    attached_pic: bool | None
    flags: tuple[tuple[str, bool], ...]


class StreamEvidence(CanonicalModel):
    """Strict technical stream projection plus the full 2B evidence binding."""

    index: int = Field(ge=0)
    stream_type: Literal["video", "audio", "subtitle", "data", "attachment", "unknown"]
    codec_type_raw: str = Field(max_length=4096)
    codec_name: str | None = Field(default=None, max_length=4096)
    profile: str | None = Field(default=None, max_length=4096)
    pix_fmt: str | None = Field(default=None, max_length=4096)
    disposition: DispositionEvidence
    time_base: RationalEvidence | None
    r_frame_rate: RationalEvidence | None
    avg_frame_rate: RationalEvidence | None
    start_time: TimeEvidence | None
    duration: TimeEvidence | None
    nb_frames: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    sample_rate: int | None = Field(default=None, ge=0)
    channels: int | None = Field(default=None, ge=0)
    channel_layout: str | None = Field(default=None, max_length=4096)

    @classmethod
    def from_stream(cls, stream: MediaStream) -> StreamEvidence:
        """Project all authority-relevant technical values from one 2B stream."""

        def time(value: ExactTime | None) -> TimeEvidence | None:
            if value is None:
                return None
            return TimeEvidence(value=str(value.value), lexeme=value.lexeme)

        return cls(
            index=stream.index,
            stream_type=stream.stream_type.value,
            codec_type_raw=stream.codec_type_raw,
            codec_name=stream.codec_name,
            profile=stream.profile,
            pix_fmt=stream.pix_fmt,
            disposition=DispositionEvidence(
                default=stream.disposition.default,
                attached_pic=stream.disposition.attached_pic,
                flags=stream.disposition.flags,
            ),
            time_base=RationalEvidence.from_value(stream.time_base),
            r_frame_rate=RationalEvidence.from_value(stream.r_frame_rate),
            avg_frame_rate=RationalEvidence.from_value(stream.avg_frame_rate),
            start_time=time(stream.start_time),
            duration=time(stream.duration),
            nb_frames=stream.nb_frames,
            width=stream.width,
            height=stream.height,
            sample_rate=stream.sample_rate,
            channels=stream.channels,
            channel_layout=stream.channel_layout,
        )


class FormatEvidence(CanonicalModel):
    """Normalized container evidence required by the Phase-1 identity builder."""

    filename: str = Field(min_length=1, max_length=32767)
    format_name: str = Field(min_length=1, max_length=4096)
    format_long_name: str | None = Field(default=None, max_length=4096)
    start_time: TimeEvidence | None
    duration: TimeEvidence | None
    size_bytes: int | None = Field(default=None, ge=0)
    bit_rate: int | None = Field(default=None, ge=0)
    tags: tuple[tuple[str, str], ...]

    @classmethod
    def from_format(cls, value: FormatProfile) -> FormatEvidence:
        """Project one normalized 2B format profile."""

        def time(item: ExactTime | None) -> TimeEvidence | None:
            if item is None:
                return None
            return TimeEvidence(value=str(item.value), lexeme=item.lexeme)

        return cls(
            filename=value.filename,
            format_name=value.format_name,
            format_long_name=value.format_long_name,
            start_time=time(value.start_time),
            duration=time(value.duration),
            size_bytes=value.size_bytes,
            bit_rate=value.bit_rate,
            tags=value.tags,
        )


class ProgramEvidence(CanonicalModel):
    """Normalized ffprobe program evidence."""

    program_id: int = Field(ge=0)
    stream_indexes: tuple[int, ...]
    tags: tuple[tuple[str, str], ...]

    @classmethod
    def from_program(cls, value: ProgramProfile) -> ProgramEvidence:
        """Project one normalized 2B program."""
        return cls(
            program_id=value.program_id,
            stream_indexes=value.stream_indexes,
            tags=value.tags,
        )


class NormalizedProfileEvidence(CanonicalModel):
    """Bounded normalized profile and complete canonical 2B stream evidence."""

    profile_version: Literal["normalized_media_profile/1.0"] = NORMALIZED_PROFILE_VERSION
    format: FormatEvidence
    streams: tuple[StreamEvidence, ...]
    programs: tuple[ProgramEvidence, ...]
    canonical_stream_evidence_json: str = Field(min_length=2, max_length=4 * 1024 * 1024)

    @model_validator(mode="after")
    def stream_indexes_are_unique_and_ordered(self) -> Self:
        """Keep the structured projection aligned with 2B's canonical order."""
        indexes = tuple(item.index for item in self.streams)
        if indexes != tuple(sorted(indexes)) or len(indexes) != len(set(indexes)):
            raise ValueError("profile stream indexes must be unique and ordered")
        return self


class BinaryIdentityEvidence(CanonicalModel):
    """Complete durable identity of the validated ffprobe capability."""

    canonical_path: str = Field(min_length=3, max_length=32767)
    volume_id: VolumeIdHex
    file_id: FileIdHex
    file_id_scheme: Literal["file_id_128"] = "file_id_128"
    size_bytes: int = Field(ge=1)
    sha256: Sha256Hex
    semantic_version: str = Field(min_length=5, max_length=128)
    version_report: str = Field(min_length=1, max_length=1024 * 1024)
    version_stderr_sha256: Sha256Hex
    versions_matrix_revision: str = Field(min_length=1, max_length=64)
    support_policy_type: Literal["minimum_semantic_version"]
    support_policy_digest: Sha256Hex
    validation_contract_version: Literal["ffprobe_binary_validation/1.0"]
    validated_at_utc: str = Field(min_length=1, max_length=128)

    @classmethod
    def from_binary(cls, binary: ValidatedFfprobeBinary) -> BinaryIdentityEvidence:
        """Project an already authenticated 2B binary capability."""
        if not isinstance(binary.volume_id, AvailableIdentity) or not isinstance(
            binary.file_id, AvailableIdentity
        ):
            raise ValueError("validated ffprobe binary lacks file identity")
        return cls(
            canonical_path=binary.canonical_dos_path,
            volume_id=binary.volume_id.value,
            file_id=binary.file_id.value,
            size_bytes=binary.size_bytes,
            sha256=binary.sha256,
            semantic_version=str(binary.version.semantic_version),
            version_report=binary.raw_version_output,
            version_stderr_sha256=hashlib.sha256(binary.version_stderr_output).hexdigest(),
            versions_matrix_revision=binary.support_policy_revision,
            support_policy_type=binary.support_policy_type,
            support_policy_digest=binary.support_policy_digest,
            validation_contract_version=binary.validation_contract_version,
            validated_at_utc=binary.validated_at_utc,
        )


class AutomaticSelectionEvidence(CanonicalModel):
    """All authority-bearing fields of one semantic 2B selection."""

    policy_id: Literal["stream_selection/1.0"]
    stream_selection_evidence_digest: Sha256Hex
    video_index: int = Field(ge=0)
    audio_index: int = Field(ge=0)
    video_reason_code: Literal[
        "video_single_eligible", "video_unique_default", "video_unique_resolution_maximum"
    ]
    audio_reason_code: Literal[
        "audio_single_eligible", "audio_unique_default", "audio_unique_highest_supported_layout"
    ]
    selection_identity: Sha256Hex

    @classmethod
    def from_selection(cls, value: FinalizedStreamSelection) -> AutomaticSelectionEvidence:
        """Project a semantically revalidated 2B selection."""
        return cls(
            policy_id=value.policy_id,
            stream_selection_evidence_digest=value.stream_selection_evidence_digest,
            video_index=value.video_index,
            audio_index=value.audio_index,
            video_reason_code=value.video_reason_code.value,
            audio_reason_code=value.audio_reason_code.value,
            selection_identity=value.selection_identity,
        )


class MediaProbe(CanonicalModel):
    """Persistent immutable media_probe 1.0 evidence."""

    artifact_type: Literal["media_probe"] = "media_probe"
    schema_version: Literal["1.0"] = "1.0"
    artifact_id: CanonicalUuidText
    media_probe_contract_version: Literal["media_probe/1.0"] = MEDIA_PROBE_CONTRACT_VERSION
    project_id: CanonicalUuidText
    probe_id: CanonicalUuidText
    probe_run_id: CanonicalUuidText
    lease_id: CanonicalUuidText
    lease_epoch: CanonicalUuidText
    volume_id: VolumeIdHex
    file_id: FileIdHex
    file_id_scheme: Literal["file_id_128"] = "file_id_128"
    source_path: str = Field(min_length=3, max_length=32767)
    s0: SnapshotEvidence
    s1: SnapshotEvidence
    s2: SnapshotEvidence
    s3: SnapshotEvidence
    pre_probe_path_revalidation: PathRevalidationEvidence
    binary: BinaryIdentityEvidence
    probe_core_contract_version: Literal["probe_core/1.0"]
    parser_version: Literal["probe_json_parser/1.0"] = PROBE_PARSER_VERSION
    profile_version: Literal["normalized_media_profile/1.0"] = NORMALIZED_PROFILE_VERSION
    expected_snapshot_key: Sha256Hex
    probe_snapshot_before: SnapshotEvidence
    probe_snapshot_after: SnapshotEvidence
    profile: NormalizedProfileEvidence
    semantic_profile_digest: Sha256Hex
    stream_selection_evidence_digest: Sha256Hex
    outcome: Literal["selected", "ambiguous", "unsupported"]
    automatic_selection: AutomaticSelectionEvidence | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    error_phase: str | None = Field(default=None, min_length=1, max_length=256)
    error_detail_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def all_bindings_match(self) -> Self:
        """Reject mixed epochs, instances, digests, or success/error shapes."""
        if self.artifact_id != self.probe_id or self.lease_id != self.lease_epoch:
            raise ValueError("media probe ID or lease epoch mismatch")
        snapshots = (self.s0, self.s1, self.s2, self.s3)
        if any(
            item.volume_id != self.volume_id
            or item.file_id != self.file_id
            or item.source_path != self.source_path
            for item in snapshots
        ):
            raise ValueError("media probe snapshots cross source instances")
        if len({item.snapshot_key for item in snapshots}) != 1:
            raise ValueError("media probe snapshots are not unchanged")
        if (
            self.expected_snapshot_key != self.s0.snapshot_key
            or self.probe_snapshot_before.snapshot_key != self.s0.snapshot_key
            or self.probe_snapshot_after.snapshot_key != self.s0.snapshot_key
            or self.pre_probe_path_revalidation.snapshot.snapshot_key != self.s0.snapshot_key
        ):
            raise ValueError("probe snapshot binding differs from S0")
        stream_bytes = self.profile.canonical_stream_evidence_json.encode("utf-8")
        expected_stream_digest = hashlib.sha256(
            b"matrix-stream-selection-evidence/1.0\0" + stream_bytes
        ).hexdigest()
        if self.stream_selection_evidence_digest != expected_stream_digest:
            raise ValueError("stream-selection evidence digest mismatch")
        expected_profile_digest = hashlib.sha256(
            b"matrix-media-profile-evidence/1.0\0" + self.profile.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.semantic_profile_digest != expected_profile_digest:
            raise ValueError("semantic profile digest mismatch")
        if self.outcome == "selected":
            if (
                self.automatic_selection is None
                or self.error_code is not None
                or self.error_phase is not None
                or self.error_detail_code is not None
                or self.automatic_selection.stream_selection_evidence_digest
                != self.stream_selection_evidence_digest
            ):
                raise ValueError("selected media probe has an invalid selection shape")
        elif (
            self.automatic_selection is not None
            or self.error_code is None
            or self.error_phase is None
        ):
            raise ValueError("non-selected media probe lacks bounded failure evidence")
        return self


class StreamAssignment(CanonicalModel):
    """Persistent explicit stream_assignment 1.0 decision evidence."""

    artifact_type: Literal["stream_assignment"] = "stream_assignment"
    schema_version: Literal["1.0"] = "1.0"
    assignment_contract_version: Literal["stream_assignment/1.0"] = (
        STREAM_ASSIGNMENT_CONTRACT_VERSION
    )
    assignment_id: CanonicalUuidText
    project_id: CanonicalUuidText
    assignment_run_id: CanonicalUuidText
    original_probe_id: CanonicalUuidText
    original_media_probe: ArtifactReference
    original_semantic_profile_digest: Sha256Hex
    stream_selection_evidence_digest: Sha256Hex
    source_snapshot_key: Sha256Hex
    video: StreamEvidence
    audio: StreamEvidence
    diagnostic_note: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def explicit_shape_is_bound(self) -> Self:
        """Require one video and audio without pretending an automatic result."""
        if (
            self.assignment_id != self.assignment_run_id
            or self.original_media_probe.artifact_type != "media_probe"
            or self.original_media_probe.artifact_id != self.original_probe_id
            or self.video.stream_type != StreamType.VIDEO.value
            or self.audio.stream_type != StreamType.AUDIO.value
            or self.video.index == self.audio.index
        ):
            raise ValueError("stream assignment bindings are inconsistent")
        return self


class SourceIdentityEvidence(CanonicalModel):
    """Persistent cross-validated SourceIdentityEvidence 1.0."""

    artifact_type: Literal["source_identity_evidence"] = "source_identity_evidence"
    schema_version: Literal["1.0"] = "1.0"
    evidence_contract_version: Literal["source_identity_evidence/1.0"] = (
        SOURCE_IDENTITY_EVIDENCE_CONTRACT_VERSION
    )
    project_id: CanonicalUuidText
    identity_run_id: CanonicalUuidText
    evidence_id: Sha256Hex
    source_identity: SourceIdentity
    source_identity_digest: Sha256Hex
    lease_id: CanonicalUuidText
    lease_epoch: CanonicalUuidText
    s0: SnapshotEvidence
    s1: SnapshotEvidence
    s2: SnapshotEvidence
    s3: SnapshotEvidence
    s4: SnapshotEvidence
    s5: SnapshotEvidence
    volume_id: VolumeIdHex
    file_id: FileIdHex
    file_id_scheme: Literal["file_id_128"] = "file_id_128"
    source_path: str = Field(min_length=3, max_length=32767)
    pre_probe_path_revalidation: PathRevalidationEvidence
    pre_commit_path_revalidation: PathRevalidationEvidence
    media_probe: ArtifactReference
    probe_core_contract_version: Literal["probe_core/1.0"]
    parser_version: Literal["probe_json_parser/1.0"]
    profile_version: Literal["normalized_media_profile/1.0"]
    binary_sha256: Sha256Hex
    binary_version: str = Field(min_length=5, max_length=128)
    stream_selection_policy: Literal["stream_selection/1.0"]
    stream_selection_evidence_digest: Sha256Hex
    video_index: int = Field(ge=0)
    audio_index: int = Field(ge=0)
    video_reason_code: str = Field(min_length=1, max_length=128)
    audio_reason_code: str = Field(min_length=1, max_length=128)
    selection_identity: Sha256Hex
    selection_mode: Literal["automatic_unique", "explicit_assignment"]
    assignment: ArtifactReference | None = None
    hash_run_id: CanonicalUuidText
    hash_receipt: HashReceipt
    hash_receipt_digest: Sha256Hex
    sha256: Sha256Hex
    bytes_read: int = Field(ge=0)
    hash_contract_version: Literal["lease_bound_source_hash/1.0"]
    binding_mode: Literal["direct_mp4", "obs_auto_remux", "manual_remux", "renamed_rebind"]

    @model_validator(mode="after")
    def evidence_chain_is_closed(self) -> Self:
        """Cross-check every persisted authority-bearing package binding."""
        identity_bytes = self.source_identity.model_dump_json().encode("utf-8")
        identity_digest = hashlib.sha256(
            b"matrix-auto-cutter/source-identity/1.0\0" + identity_bytes
        ).hexdigest()
        if (
            self.evidence_id != self.source_identity_digest
            or self.source_identity_digest != identity_digest
            or self.lease_id != self.lease_epoch
        ):
            raise ValueError("identity digest or lease epoch mismatch")
        snapshots = (self.s0, self.s1, self.s2, self.s3, self.s4, self.s5)
        if (
            any(
                item.volume_id != self.volume_id
                or item.file_id != self.file_id
                or item.source_path != self.source_path
                for item in snapshots
            )
            or len({item.snapshot_key for item in snapshots}) != 1
        ):
            raise ValueError("S0-S5 do not prove one unchanged source instance")
        if (
            self.pre_probe_path_revalidation.snapshot.snapshot_key != self.s0.snapshot_key
            or self.pre_commit_path_revalidation.snapshot.snapshot_key != self.s0.snapshot_key
        ):
            raise ValueError("path revalidations do not match S0")
        receipt = self.hash_receipt
        if (
            receipt.project_id != self.project_id
            or receipt.hash_run_id != self.hash_run_id
            or receipt.lease_id != self.lease_id
            or receipt.validation_epoch != self.lease_epoch
            or receipt.s0_snapshot_key != self.s0.snapshot_key
            or receipt.s4_snapshot_key != self.s4.snapshot_key
            or receipt.volume_id != self.volume_id
            or receipt.file_id != self.file_id
            or receipt.sha256 != self.sha256
            or receipt.bytes_read != self.bytes_read
            or self.hash_receipt_digest != hashlib.sha256(hash_receipt_bytes(receipt)).hexdigest()
        ):
            raise ValueError("hash receipt is inconsistent with identity evidence")
        if (
            self.source_identity.sha256 != self.sha256
            or self.source_identity.size_bytes != self.bytes_read
            or self.source_identity.binding.value != self.binding_mode
        ):
            raise ValueError("Phase-1 SourceIdentity differs from confirmed evidence")
        if (self.selection_mode == "explicit_assignment") != (self.assignment is not None):
            raise ValueError("assignment reference does not match selection mode")
        return self


def normalized_profile_from_probe(
    result: ProbeOk | ProbeDiagnosticProfile,
) -> NormalizedProfileEvidence:
    """Create the one persistent profile projection from current 2B evidence."""
    profile = result.profile if isinstance(result, ProbeOk) else result
    stream_json = canonical_stream_evidence_bytes(profile.streams).decode("utf-8")
    return NormalizedProfileEvidence(
        format=FormatEvidence.from_format(profile.format),
        streams=tuple(StreamEvidence.from_stream(item) for item in profile.streams),
        programs=tuple(ProgramEvidence.from_program(item) for item in profile.programs),
        canonical_stream_evidence_json=stream_json,
    )


def semantic_profile_digest(profile: NormalizedProfileEvidence) -> str:
    """Digest the exact normalized profile under the 2E evidence domain."""
    return hashlib.sha256(
        b"matrix-media-profile-evidence/1.0\0" + profile.model_dump_json().encode("utf-8")
    ).hexdigest()


def _parse_canonical[ModelT: CanonicalModel](
    data: bytes,
    maximum_bytes: int,
    model_type: type[ModelT],
) -> ModelT:
    if len(data) > maximum_bytes:
        raise ValueError("artifact exceeds its bounded size contract")
    if data.startswith(b"\xef\xbb\xbf") or not data.endswith(b"\n"):
        raise ValueError("artifact is not canonical UTF-8")
    model = model_type.model_validate_json(data.decode("utf-8", errors="strict"))
    from matrix_auto_cutter.phase2.artifacts import canonical_bytes

    if canonical_bytes(model) != data:
        raise ValueError("artifact bytes are not canonical")
    return model


def parse_media_probe_bytes(data: bytes) -> MediaProbe:
    """Strictly parse bounded canonical media_probe 1.0 bytes."""
    return _parse_canonical(data, MAX_MEDIA_PROBE_BYTES, MediaProbe)


def parse_stream_assignment_bytes(data: bytes) -> StreamAssignment:
    """Strictly parse bounded canonical stream_assignment 1.0 bytes."""
    return _parse_canonical(data, MAX_STREAM_ASSIGNMENT_BYTES, StreamAssignment)


def parse_source_identity_evidence_bytes(data: bytes) -> SourceIdentityEvidence:
    """Strictly parse bounded canonical source_identity_evidence 1.0 bytes."""
    return _parse_canonical(
        data,
        MAX_SOURCE_IDENTITY_EVIDENCE_BYTES,
        SourceIdentityEvidence,
    )
