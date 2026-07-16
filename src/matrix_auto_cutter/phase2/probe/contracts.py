"""Immutable package-2B runtime contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from math import gcd
from typing import Final, Literal, SupportsIndex

from matrix_auto_cutter.phase2.artifacts import IdentityEvidence
from matrix_auto_cutter.phase2.pathing import PathRole, ValidatedPath
from matrix_auto_cutter.phase2.probe.errors import ProbeError
from matrix_auto_cutter.phase2.probe.numeric_limits import (
    validate_bounded_integer,
    validate_derived_integer,
)
from matrix_auto_cutter.phase2.probe.supported_versions import SemanticVersion
from matrix_auto_cutter.phase2.snapshots import FileSnapshot, FileTime

PROBE_CONTRACT_VERSION: Final[Literal["probe_core/1.0"]] = "probe_core/1.0"
BINARY_VALIDATION_CONTRACT_VERSION: Final[Literal["ffprobe_binary_validation/1.0"]] = (
    "ffprobe_binary_validation/1.0"
)
STREAM_SELECTION_POLICY_VERSION: Final[Literal["stream_selection/1.0"]] = "stream_selection/1.0"


def _ascii_fold_name(value: str) -> str:
    """Fold only ASCII A-Z for contract-defined field and tag comparisons."""
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character for character in value
    )


@dataclass(frozen=True, slots=True)
class Rational:
    """Normalized exact rational number."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        """Normalize sign and reduce without using floating point."""
        if self.denominator == 0:
            raise ValueError("rational denominator must not be zero")
        validate_derived_integer(self.numerator)
        validate_derived_integer(self.denominator)
        sign = -1 if self.denominator < 0 else 1
        numerator = self.numerator * sign
        denominator = self.denominator * sign
        divisor = gcd(abs(numerator), denominator)
        object.__setattr__(self, "numerator", numerator // divisor)
        object.__setattr__(self, "denominator", denominator // divisor)

    @property
    def positive(self) -> bool:
        """Return whether the value is strictly positive."""
        return self.numerator > 0

    def compare(self, other: Rational) -> int:
        """Compare exactly and return -1, 0 or 1."""
        left = validate_derived_integer(self.numerator * other.denominator)
        right = validate_derived_integer(other.numerator * self.denominator)
        delta = validate_derived_integer(left - right)
        return (delta > 0) - (delta < 0)


@dataclass(frozen=True, slots=True)
class ExactTime:
    """An exact decimal time lexeme retained as ``Decimal``."""

    value: Decimal
    lexeme: str


@dataclass(frozen=True, slots=True)
class LibraryVersion:
    """One typed ffprobe library version line."""

    name: str
    compiled: tuple[int, int, int]
    runtime: tuple[int, int, int]
    raw_line: str


@dataclass(frozen=True, slots=True)
class FfprobeVersion:
    """Fully parsed ffprobe version report."""

    semantic_version: SemanticVersion
    build_suffix: str
    first_line: str
    compiler_line: str
    configuration_line: str
    libraries: tuple[LibraryVersion, ...]
    raw_output: str


@dataclass(frozen=True, slots=True)
class FfprobeCandidate:
    """An untrusted configured candidate path."""

    path: str


@dataclass(frozen=True, slots=True)
class BinaryEvidence:
    """Handle-derived binary file facts and full content digest."""

    volume_id: IdentityEvidence
    file_id: IdentityEvidence
    size_bytes: int
    creation_time: FileTime
    last_write_time: FileTime
    change_time: FileTime
    sha256: str
    snapshot: FileSnapshot


class ValidatedFfprobeBinary:
    """Sealed, immutable ffprobe capability issued only by the binary validator.

    The ordinary constructor, copying and pickle-style reconstruction are deliberately
    unavailable.  The trusted validator allocates an instance without invoking this
    constructor and binds every slot to an authenticated seal checked at prelaunch.
    """

    __slots__ = (
        "_seal",
        "canonical_dos_path",
        "change_time",
        "creation_time",
        "file_id",
        "last_write_time",
        "long_path",
        "original_snapshot",
        "path",
        "raw_version_output",
        "sha256",
        "size_bytes",
        "support_policy_digest",
        "support_policy_revision",
        "support_policy_type",
        "validated_at_utc",
        "validation_contract_version",
        "version",
        "version_stderr_output",
        "volume_id",
    )

    path: ValidatedPath
    canonical_dos_path: str
    long_path: str
    volume_id: IdentityEvidence
    file_id: IdentityEvidence
    size_bytes: int
    creation_time: FileTime
    last_write_time: FileTime
    change_time: FileTime
    sha256: str
    raw_version_output: str
    version: FfprobeVersion
    version_stderr_output: bytes
    support_policy_revision: Literal["1.0"]
    support_policy_type: Literal["minimum_semantic_version"]
    support_policy_digest: str
    validation_contract_version: Literal["ffprobe_binary_validation/1.0"]
    validated_at_utc: str
    original_snapshot: FileSnapshot
    _seal: bytes

    def __new__(cls, *_args: object, **_kwargs: object) -> ValidatedFfprobeBinary:
        """Reject public construction; only the trusted validator may allocate one."""
        raise TypeError("ValidatedFfprobeBinary can only be issued by validate_ffprobe_binary")

    def __setattr__(self, _name: str, _value: object) -> None:
        """Reject mutation through the normal object protocol."""
        raise TypeError("ValidatedFfprobeBinary is immutable")

    def __copy__(self) -> ValidatedFfprobeBinary:
        """Forbid producing a second capability through shallow copy."""
        raise TypeError("ValidatedFfprobeBinary capabilities cannot be copied")

    def __deepcopy__(self, _memo: dict[int, object]) -> ValidatedFfprobeBinary:
        """Forbid producing a second capability through deep copy."""
        raise TypeError("ValidatedFfprobeBinary capabilities cannot be copied")

    def __reduce_ex__(self, _protocol: SupportsIndex) -> str | tuple[object, ...]:
        """Forbid serialization from becoming an alternate constructor."""
        raise TypeError("ValidatedFfprobeBinary capabilities cannot be serialized")

    def __repr__(self) -> str:
        """Render bounded handle-free diagnostic identity without the authentication seal."""
        return (
            "ValidatedFfprobeBinary("
            f"path={self.canonical_dos_path!r}, version={self.version.semantic_version!s}, "
            f"sha256={self.sha256!r}, validated_at_utc={self.validated_at_utc!r})"
        )


class StreamType(StrEnum):
    """Normalized stream kinds; unknown values remain visible."""

    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    DATA = "data"
    ATTACHMENT = "attachment"
    UNKNOWN = "unknown"


class TagProjectionStatus(StrEnum):
    """Diagnostic availability of one ASCII-case-insensitive tag projection."""

    NOT_AVAILABLE = "not_available"
    VALUE = "value"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class TagProjection:
    """One non-authoritative language/title convenience projection."""

    status: TagProjectionStatus
    value: str | None = None


@dataclass(frozen=True, slots=True)
class CanonicalJsonArray:
    """Type-preserving canonical JSON array evidence."""

    items: tuple[CanonicalJsonValue, ...]


@dataclass(frozen=True, slots=True)
class CanonicalJsonObject:
    """Type-preserving canonical JSON object evidence with sorted keys."""

    items: tuple[tuple[str, CanonicalJsonValue], ...]


type CanonicalJsonValue = (
    None | bool | int | Decimal | str | CanonicalJsonArray | CanonicalJsonObject
)


class VideoSelectionReason(StrEnum):
    """Closed video reason-code enum for policy 1.0."""

    SINGLE_ELIGIBLE = "video_single_eligible"
    UNIQUE_DEFAULT = "video_unique_default"
    UNIQUE_RESOLUTION_MAXIMUM = "video_unique_resolution_maximum"


class AudioSelectionReason(StrEnum):
    """Closed audio reason-code enum for policy 1.0."""

    SINGLE_ELIGIBLE = "audio_single_eligible"
    UNIQUE_DEFAULT = "audio_unique_default"
    UNIQUE_HIGHEST_SUPPORTED_LAYOUT = "audio_unique_highest_supported_layout"


@dataclass(frozen=True, slots=True)
class Disposition:
    """Closed ffprobe disposition flags with critical absence retained."""

    default: bool | None
    attached_pic: bool | None
    flags: tuple[tuple[str, bool], ...]


@dataclass(frozen=True, slots=True)
class RotationEvidence:
    """Explicit and display-matrix rotation kept separate."""

    explicit_degrees: int | None
    display_matrix_degrees: int | None
    display_matrix_raw: str | None


@dataclass(frozen=True, slots=True)
class SideData:
    """Bounded side-data with known rotation evidence and untrusted fields."""

    side_data_type: str
    rotation: int | None
    display_matrix: str | None
    extra_fields: tuple[tuple[str, CanonicalJsonValue], ...]

    @property
    def untrusted_fields(self) -> tuple[tuple[str, CanonicalJsonValue], ...]:
        """Compatibility accessor for the now type-preserving extra evidence."""
        return self.extra_fields


@dataclass(frozen=True, slots=True)
class MediaStream:
    """Fully normalized stream while preserving unsupported kinds."""

    index: int
    codec_name: str | None
    profile: str | None
    pix_fmt: str | None
    codec_type_raw: str
    stream_type: StreamType
    disposition: Disposition
    time_base: Rational | None
    r_frame_rate: Rational | None
    avg_frame_rate: Rational | None
    start_time: ExactTime | None
    duration: ExactTime | None
    nb_frames: int | None
    width: int | None
    height: int | None
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    tags: tuple[tuple[str, str], ...]
    side_data: tuple[SideData, ...]
    rotation: RotationEvidence
    extra_fields: tuple[tuple[str, CanonicalJsonValue], ...] = ()
    cfr_status: Literal["not_established"] = field(default="not_established", init=False)

    def _tag_projection(self, name: str) -> TagProjection:
        """Project one diagnostic tag using only ASCII case-insensitive equality."""
        folded = _ascii_fold_name(name)
        matches = tuple(value for key, value in self.tags if _ascii_fold_name(key) == folded)
        if not matches:
            return TagProjection(TagProjectionStatus.NOT_AVAILABLE)
        if len(matches) > 1:
            return TagProjection(TagProjectionStatus.AMBIGUOUS)
        return TagProjection(TagProjectionStatus.VALUE, matches[0])

    @property
    def language_projection(self) -> TagProjection:
        """Return the non-authoritative language projection."""
        return self._tag_projection("language")

    @property
    def title_projection(self) -> TagProjection:
        """Return the non-authoritative title projection."""
        return self._tag_projection("title")

    @property
    def language(self) -> str | None:
        """Return the unique projected language, otherwise ``None``."""
        return self.language_projection.value

    @property
    def title(self) -> str | None:
        """Return the unique projected title, otherwise ``None``."""
        return self.title_projection.value


@dataclass(frozen=True, slots=True)
class FormatProfile:
    """Normalized container-level ffprobe evidence."""

    filename: str
    format_name: str
    format_long_name: str | None
    start_time: ExactTime | None
    duration: ExactTime | None
    size_bytes: int | None
    bit_rate: int | None
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ProgramProfile:
    """Normalized program with stream indexes and bounded untrusted tags."""

    program_id: int
    stream_indexes: tuple[int, ...]
    tags: tuple[tuple[str, str], ...]


def _rational_evidence(value: Rational | None) -> tuple[int, int] | None:
    if value is None:
        return None
    return value.numerator, value.denominator


def _time_evidence(value: ExactTime | None) -> tuple[str, str] | None:
    if value is None:
        return None
    return str(value.value), value.lexeme


def _decimal_evidence(value: Decimal) -> str:
    """Render one finite exact decimal using the existing canonical zero rule."""
    return "0" if value == 0 else format(value, "f")


def _json_value_evidence(value: CanonicalJsonValue) -> dict[str, object]:
    """Encode opaque JSON evidence with explicit scalar/container type tags."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean", "value": value}
    if isinstance(value, int):
        return {"type": "integer", "value": value}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": _decimal_evidence(value)}
    if isinstance(value, str):
        return {"type": "string", "value": value}
    if isinstance(value, CanonicalJsonArray):
        return {
            "type": "array",
            "value": tuple(_json_value_evidence(item) for item in value.items),
        }
    return {
        "type": "object",
        "value": tuple((key, _json_value_evidence(item)) for key, item in value.items),
    }


def _stream_evidence(stream: MediaStream) -> dict[str, object]:
    """Build the complete bounded normalized evidence shape for one stream."""
    return {
        "avg_frame_rate": _rational_evidence(stream.avg_frame_rate),
        "channel_layout": stream.channel_layout,
        "channels": stream.channels,
        "codec_name": stream.codec_name,
        "codec_type_raw": stream.codec_type_raw,
        "disposition": {
            "attached_pic": stream.disposition.attached_pic,
            "default": stream.disposition.default,
            "flags": stream.disposition.flags,
        },
        "duration": _time_evidence(stream.duration),
        "height": stream.height,
        "index": stream.index,
        "nb_frames": stream.nb_frames,
        "pix_fmt": stream.pix_fmt,
        "profile": stream.profile,
        "r_frame_rate": _rational_evidence(stream.r_frame_rate),
        "rotation": {
            "display_matrix_degrees": stream.rotation.display_matrix_degrees,
            "display_matrix_raw": stream.rotation.display_matrix_raw,
        },
        "sample_rate": stream.sample_rate,
        "side_data": tuple(
            {
                "display_matrix": item.display_matrix,
                "rotation": item.rotation,
                "side_data_type": item.side_data_type,
                "extra_fields": tuple(
                    (key, _json_value_evidence(value)) for key, value in item.extra_fields
                ),
            }
            for item in stream.side_data
        ),
        "start_time": _time_evidence(stream.start_time),
        "stream_type": stream.stream_type.value,
        "tags": stream.tags,
        "time_base": _rational_evidence(stream.time_base),
        "width": stream.width,
        "cfr_status": stream.cfr_status,
        "extra_fields": tuple(
            (key, _json_value_evidence(value)) for key, value in stream.extra_fields
        ),
    }


def canonical_stream_evidence_bytes(streams: tuple[MediaStream, ...]) -> bytes:
    """Return canonical bytes for the full stream set sorted strictly by index."""
    ordered = tuple(sorted(streams, key=lambda stream: stream.index))
    indexes = tuple(stream.index for stream in ordered)
    if len(indexes) != len(set(indexes)):
        raise ValueError("stream evidence contains duplicate indexes")
    payload = json.dumps(
        tuple(_stream_evidence(stream) for stream in ordered),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return payload


def stream_selection_evidence_digest(streams: tuple[MediaStream, ...]) -> str:
    """Digest the full canonical stream set under the policy-1.0 domain."""
    return hashlib.sha256(
        b"matrix-stream-selection-evidence/1.0\0" + canonical_stream_evidence_bytes(streams)
    ).hexdigest()


def canonical_selection_identity_payload_bytes(
    evidence_digest: str,
    video_index: int,
    audio_index: int,
    video_reason_code: VideoSelectionReason,
    audio_reason_code: AudioSelectionReason,
) -> bytes:
    """Build the exact six-field identity payload in its normative key order."""
    if (
        len(evidence_digest) != 64
        or any(character not in "0123456789abcdef" for character in evidence_digest)
        or isinstance(video_index, bool)
        or isinstance(audio_index, bool)
        or video_index < 0
        or audio_index < 0
        or not isinstance(video_reason_code, VideoSelectionReason)
        or not isinstance(audio_reason_code, AudioSelectionReason)
    ):
        raise ValueError("invalid stream-selection identity field")
    validate_bounded_integer(video_index)
    validate_bounded_integer(audio_index)
    payload = json.dumps(
        {
            "audio_index": audio_index,
            "audio_reason_code": audio_reason_code.value,
            "policy_id": STREAM_SELECTION_POLICY_VERSION,
            "stream_selection_evidence_digest": evidence_digest,
            "video_index": video_index,
            "video_reason_code": video_reason_code.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return payload


def validate_selection_identity_payload(value: object) -> bool:
    """Validate the exact closed six-field external identity payload shape."""
    if not isinstance(value, dict) or set(value) != {
        "policy_id",
        "stream_selection_evidence_digest",
        "video_index",
        "audio_index",
        "video_reason_code",
        "audio_reason_code",
    }:
        return False
    if value["policy_id"] != STREAM_SELECTION_POLICY_VERSION:
        return False
    digest = value["stream_selection_evidence_digest"]
    video_index = value["video_index"]
    audio_index = value["audio_index"]
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
        or isinstance(video_index, bool)
        or not isinstance(video_index, int)
        or video_index < 0
        or isinstance(audio_index, bool)
        or not isinstance(audio_index, int)
        or audio_index < 0
        or value["video_reason_code"] not in {reason.value for reason in VideoSelectionReason}
        or value["audio_reason_code"] not in {reason.value for reason in AudioSelectionReason}
    ):
        return False
    try:
        validate_bounded_integer(video_index)
        validate_bounded_integer(audio_index)
    except ValueError:
        return False
    return True


def _selection_identity(
    evidence_digest: str,
    video_index: int,
    audio_index: int,
    video_reason_code: VideoSelectionReason,
    audio_reason_code: AudioSelectionReason,
) -> str:
    payload = canonical_selection_identity_payload_bytes(
        evidence_digest,
        video_index,
        audio_index,
        video_reason_code,
        audio_reason_code,
    )
    return hashlib.sha256(b"matrix-stream-selection-identity/1.0\0" + payload).hexdigest()


@dataclass(frozen=True, slots=True)
class FinalizedStreamSelection:
    """Non-authoritative data value for one policy-1.0 stream decision."""

    video: MediaStream
    audio: MediaStream
    stream_evidence: tuple[MediaStream, ...]
    video_reason_code: VideoSelectionReason
    audio_reason_code: AudioSelectionReason
    policy_id: Literal["stream_selection/1.0"] = field(
        default=STREAM_SELECTION_POLICY_VERSION, init=False
    )
    stream_selection_evidence_digest: str = field(init=False)
    selection_identity: str = field(init=False)

    def _validate_shape(self) -> tuple[MediaStream, ...]:
        """Validate structural binding without running selection heuristics again."""
        ordered = tuple(sorted(self.stream_evidence, key=lambda stream: stream.index))
        if self.stream_evidence != ordered:
            raise ValueError("finalized stream evidence must be sorted by stream index")
        indexes = tuple(stream.index for stream in ordered)
        if len(indexes) != len(set(indexes)):
            raise ValueError("finalized stream evidence contains duplicate indexes")
        if (
            self.video.stream_type is not StreamType.VIDEO
            or self.video.disposition.attached_pic is not False
        ):
            raise ValueError("finalized video must be a non-attached video stream")
        if self.audio.stream_type is not StreamType.AUDIO:
            raise ValueError("finalized audio must be an audio stream")
        if self.video not in ordered or self.audio not in ordered:
            raise ValueError("selected streams must come from the bound stream evidence")
        if not isinstance(self.video_reason_code, VideoSelectionReason):
            raise ValueError("finalized selection has an unknown video reason code")
        if not isinstance(self.audio_reason_code, AudioSelectionReason):
            raise ValueError("finalized selection has an unknown audio reason code")
        return ordered

    def __post_init__(self) -> None:
        """Reject incomplete, reordered or cross-stream final states."""
        ordered = self._validate_shape()
        evidence_digest = stream_selection_evidence_digest(ordered)
        object.__setattr__(self, "stream_selection_evidence_digest", evidence_digest)
        object.__setattr__(
            self,
            "selection_identity",
            _selection_identity(
                evidence_digest,
                self.video.index,
                self.audio.index,
                self.video_reason_code,
                self.audio_reason_code,
            ),
        )

    def integrity_valid(self) -> bool:
        """Recheck immutable metadata/digests without performing stream selection again."""
        try:
            ordered = self._validate_shape()
            evidence_digest = stream_selection_evidence_digest(ordered)
            selection_identity = _selection_identity(
                evidence_digest,
                self.video.index,
                self.audio.index,
                self.video_reason_code,
                self.audio_reason_code,
            )
        except (AttributeError, TypeError, ValueError):
            return False
        return (
            self.policy_id == STREAM_SELECTION_POLICY_VERSION
            and self.stream_selection_evidence_digest == evidence_digest
            and self.selection_identity == selection_identity
        )

    @property
    def policy_version(self) -> Literal["stream_selection/1.0"]:
        """Compatibility alias for the fixed policy identifier."""
        return self.policy_id

    @property
    def evidence_digest(self) -> str:
        """Compatibility alias for the named stream-selection evidence digest."""
        return self.stream_selection_evidence_digest

    @property
    def rationale(self) -> tuple[str, str]:
        """Diagnostic compatibility view of the two closed reason codes."""
        return self.video_reason_code.value, self.audio_reason_code.value

    @property
    def video_index(self) -> int:
        """Return the selected original ffprobe video index."""
        return self.video.index

    @property
    def audio_index(self) -> int:
        """Return the selected original ffprobe audio index."""
        return self.audio.index


# Compatibility name for the original uncommitted package-2B API.
StreamSelectionStatus = FinalizedStreamSelection


@dataclass(frozen=True, slots=True)
class MediaProfile:
    """Lease-free probe evidence; deliberately contains no finality capability."""

    probe_contract_version: Literal["probe_core/1.0"]
    binary: ValidatedFfprobeBinary
    source: ValidatedPath
    expected_snapshot_key: str
    snapshot_before: FileSnapshot
    snapshot_after: FileSnapshot
    format: FormatProfile
    streams: tuple[MediaStream, ...]
    programs: tuple[ProgramProfile, ...]
    selection: FinalizedStreamSelection


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    """One bounded, lease-free probe request."""

    binary: ValidatedFfprobeBinary
    source: ValidatedPath
    expected_snapshot_key: str
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        """Enforce the public timeout contract."""
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("timeout_seconds must be between 1 and 600")
        if self.source.role is not PathRole.EXTERNAL_SOURCE_READ_ONLY:
            raise ValueError("probe source must have the external read-only path role")
        if len(self.expected_snapshot_key) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_snapshot_key
        ):
            raise ValueError("expected_snapshot_key must be a complete lower-case SHA-256 key")


@dataclass(frozen=True, slots=True)
class ProbeOk:
    """Successful profile without lease/finality semantics."""

    profile: MediaProfile


@dataclass(frozen=True, slots=True)
class ProbeDiagnosticProfile:
    """Bounded normalized post-parse evidence retained when selection fails."""

    probe_contract_version: Literal["probe_core/1.0"]
    binary: ValidatedFfprobeBinary
    source: ValidatedPath
    expected_snapshot_key: str
    snapshot_before: FileSnapshot
    snapshot_after: FileSnapshot
    format: FormatProfile
    streams: tuple[MediaStream, ...]
    programs: tuple[ProgramProfile, ...]
    stream_selection_evidence_digest: str


@dataclass(frozen=True, slots=True)
class ProbeFailed:
    """Structured terminal probe-core failure."""

    error: ProbeError
    profile: ProbeDiagnosticProfile | None = None


type ProbeCoreResult = ProbeOk | ProbeFailed


@dataclass(frozen=True, slots=True)
class StreamsSelected:
    """Unique policy selection."""

    selection: FinalizedStreamSelection

    @property
    def status(self) -> FinalizedStreamSelection:
        """Retain the original read-only accessor for package-2B callers."""
        return self.selection


@dataclass(frozen=True, slots=True)
class ProbeAmbiguousStreams:
    """No safe tie-break exists."""

    error: ProbeError
    stream_evidence: tuple[MediaStream, ...]
    stream_selection_evidence_digest: str


@dataclass(frozen=True, slots=True)
class ProbeUnsupportedMedia:
    """Required media kind or valid candidate is absent."""

    error: ProbeError
    stream_evidence: tuple[MediaStream, ...]
    stream_selection_evidence_digest: str


type StreamSelectionResult = StreamsSelected | ProbeAmbiguousStreams | ProbeUnsupportedMedia


@dataclass(frozen=True, slots=True)
class StreamAssignment:
    """Immutable, non-authoritative future 2E assignment shape."""

    video_index: int | None
    audio_index: int | None
    bound_probe_digest: str
    bound_snapshot_key: str
    schema_version: Literal["1.0"] = "1.0"
    authoritative_in_2b: Literal[False] = field(default=False, init=False)
