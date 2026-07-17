"""Explicit ambiguous-stream assignment creation and current-probe revalidation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from matrix_auto_cutter.phase2.artifacts import is_canonical_uuid4
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.probe import ProbeErrorCode
from matrix_auto_cutter.phase2.source_confirmation.contracts import (
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
    StreamAssignmentCancelled,
    StreamAssignmentConflict,
    StreamAssignmentCreated,
    StreamAssignmentFailed,
    StreamAssignmentRequest,
    StreamAssignmentResult,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    MAX_MEDIA_PROBE_BYTES,
    MAX_STREAM_ASSIGNMENT_BYTES,
    ArtifactReference,
    MediaProbe,
    StreamAssignment,
    StreamEvidence,
    parse_media_probe_bytes,
    parse_stream_assignment_bytes,
)
from matrix_auto_cutter.phase2.source_confirmation.persistence import (
    ArtifactConflict,
    ArtifactIoFailure,
    ArtifactPublishCancelled,
    ArtifactPublished,
    artifact_target,
    publish_artifact,
    read_artifact,
)
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.phase2.workspace import ProjectCapability


@dataclass(frozen=True, slots=True)
class ValidatedExplicitSelection:
    """Current-probe indexes and durable evidence for an explicit assignment."""

    video_index: int
    audio_index: int
    video_reason_code: str
    audio_reason_code: str
    selection_identity: str
    assignment: StreamAssignment
    reference: ArtifactReference


def _invalid(phase: str, message: str) -> ConfirmationFailure:
    return ConfirmationFailure(
        ConfirmationErrorCode.ASSIGNMENT_INVALID,
        ConfirmationErrorCategory.INPUT,
        phase,
        message,
    )


def _stale(phase: str, message: str) -> ConfirmationFailure:
    return ConfirmationFailure(
        ConfirmationErrorCode.ASSIGNMENT_STALE,
        ConfirmationErrorCategory.INTEGRITY,
        phase,
        message,
    )


def _codec_available(value: str | None) -> bool:
    return (
        value is not None
        and bool(value.strip())
        and value.casefold()
        not in {
            "unknown",
            "none",
            "n/a",
        }
    )


def _technically_assignable(video: StreamEvidence, audio: StreamEvidence) -> bool:
    """Check technical completeness only; this is not an automatic ranking policy."""
    return (
        video.stream_type == "video"
        and video.disposition.attached_pic is False
        and video.disposition.default is not None
        and _codec_available(video.codec_name)
        and video.width is not None
        and video.width > 0
        and video.height is not None
        and video.height > 0
        and audio.stream_type == "audio"
        and audio.disposition.default is not None
        and _codec_available(audio.codec_name)
        and audio.sample_rate is not None
        and audio.sample_rate > 0
        and audio.channels is not None
        and audio.channels > 0
        and audio.channel_layout is not None
        and bool(audio.channel_layout.strip())
        and audio.duration is not None
        and Decimal(audio.duration.value) > 0
    )


def _indexed_pair(
    media: MediaProbe, video_index: int, audio_index: int
) -> tuple[StreamEvidence, StreamEvidence] | None:
    streams = {item.index: item for item in media.profile.streams}
    video = streams.get(video_index)
    audio = streams.get(audio_index)
    if video is None or audio is None or not _technically_assignable(video, audio):
        return None
    return video, audio


def create_stream_assignment(
    port: Win32Port,
    request: StreamAssignmentRequest,
    cancellation: CancellationToken,
) -> StreamAssignmentResult:
    """Create an immutable explicit choice from genuine ambiguous probe evidence."""
    project_id = request.project.document.project_id
    if (
        not request.project.trusted
        or not is_canonical_uuid4(request.assignment_run_id)
        or request.media_probe.artifact_type != "media_probe"
        or isinstance(request.video_index, bool)
        or not isinstance(request.video_index, int)
        or request.video_index < 0
        or isinstance(request.audio_index, bool)
        or not isinstance(request.audio_index, int)
        or request.audio_index < 0
    ):
        return StreamAssignmentFailed(_invalid("assignment.input", "invalid assignment input"))
    if cancellation.is_cancelled:
        return StreamAssignmentCancelled(
            ConfirmationFailure(
                ConfirmationErrorCode.CANCELLED,
                ConfirmationErrorCategory.CANCELLED,
                "assignment.input",
                "stream assignment cancelled",
                retryable=True,
            )
        )
    original = read_artifact(
        port,
        request.project,
        request.media_probe,
        MAX_MEDIA_PROBE_BYTES,
        parse_media_probe_bytes,
    )
    if isinstance(original, ConfirmationFailure):
        return StreamAssignmentFailed(original)
    if (
        original.project_id != project_id
        or original.probe_id != request.media_probe.artifact_id
        or original.outcome != "ambiguous"
        or original.error_code != ProbeErrorCode.AMBIGUOUS_STREAMS.value
    ):
        return StreamAssignmentFailed(
            _invalid(
                "assignment.original_probe",
                "assignment requires a matching genuine ambiguous media probe",
            )
        )
    selected = _indexed_pair(original, request.video_index, request.audio_index)
    if selected is None:
        return StreamAssignmentFailed(
            _invalid(
                "assignment.streams",
                "selected indexes are absent or technically incomplete",
            )
        )
    video, audio = selected
    assignment = StreamAssignment(
        assignment_id=request.assignment_run_id,
        project_id=project_id,
        assignment_run_id=request.assignment_run_id,
        original_probe_id=original.probe_id,
        original_media_probe=request.media_probe,
        original_semantic_profile_digest=original.semantic_profile_digest,
        stream_selection_evidence_digest=original.stream_selection_evidence_digest,
        source_snapshot_key=original.s0.snapshot_key,
        video=video,
        audio=audio,
        diagnostic_note=request.diagnostic_note,
    )
    target = artifact_target(
        port,
        request.project,
        ("probe", original.probe_id),
        "stream-assignment.json",
    )
    if isinstance(target, ConfirmationFailure):
        return StreamAssignmentFailed(target)
    published = publish_artifact(
        port,
        target,
        assignment,
        MAX_STREAM_ASSIGNMENT_BYTES,
        parse_stream_assignment_bytes,
        cancellation,
        artifact_name="stream-assignment",
        artifact_id=assignment.assignment_id,
        artifact_type="stream_assignment",
    )
    if isinstance(published, ArtifactPublished):
        return StreamAssignmentCreated(
            published.status,
            assignment,
            published.reference,
        )
    if isinstance(published, ArtifactPublishCancelled):
        return StreamAssignmentCancelled(published.error)
    if isinstance(published, ArtifactConflict):
        return StreamAssignmentConflict(published.error)
    assert isinstance(published, ArtifactIoFailure)
    return StreamAssignmentFailed(published.error)


def validate_stream_assignment(
    port: Win32Port,
    project: ProjectCapability,
    reference: ArtifactReference,
    current_media: MediaProbe,
) -> ValidatedExplicitSelection | ConfirmationFailure:
    """Revalidate an old explicit assignment against this run's new lease probe."""
    if reference.artifact_type != "stream_assignment":
        return _invalid("assignment.reference", "reference is not a stream assignment")
    assignment = read_artifact(
        port,
        project,
        reference,
        MAX_STREAM_ASSIGNMENT_BYTES,
        parse_stream_assignment_bytes,
    )
    if isinstance(assignment, ConfirmationFailure):
        return assignment
    original = read_artifact(
        port,
        project,
        assignment.original_media_probe,
        MAX_MEDIA_PROBE_BYTES,
        parse_media_probe_bytes,
    )
    if isinstance(original, ConfirmationFailure):
        return original
    if (
        assignment.assignment_id != reference.artifact_id
        or assignment.project_id != project.document.project_id
        or original.project_id != assignment.project_id
        or original.probe_id != assignment.original_probe_id
        or original.outcome != "ambiguous"
        or original.error_code != ProbeErrorCode.AMBIGUOUS_STREAMS.value
        or original.semantic_profile_digest != assignment.original_semantic_profile_digest
        or original.stream_selection_evidence_digest != assignment.stream_selection_evidence_digest
        or original.s0.snapshot_key != assignment.source_snapshot_key
    ):
        return _stale("assignment.original_probe", "original assignment evidence is stale")
    if (
        current_media.project_id != assignment.project_id
        or current_media.outcome != "ambiguous"
        or current_media.error_code != ProbeErrorCode.AMBIGUOUS_STREAMS.value
        or current_media.s0.snapshot_key != assignment.source_snapshot_key
        or current_media.semantic_profile_digest != assignment.original_semantic_profile_digest
        or current_media.stream_selection_evidence_digest
        != assignment.stream_selection_evidence_digest
    ):
        return _stale("assignment.current_probe", "assignment does not match the new lease probe")
    selected = _indexed_pair(current_media, assignment.video.index, assignment.audio.index)
    if selected is None or selected != (assignment.video, assignment.audio):
        return _stale(
            "assignment.current_streams",
            "selected indexes or technical stream characteristics changed",
        )
    payload = json.dumps(
        {
            "assignment_digest": reference.artifact_digest,
            "audio_index": assignment.audio.index,
            "policy_id": "stream_selection/1.0",
            "stream_selection_evidence_digest": current_media.stream_selection_evidence_digest,
            "video_index": assignment.video.index,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return ValidatedExplicitSelection(
        assignment.video.index,
        assignment.audio.index,
        "explicit_video_assignment",
        "explicit_audio_assignment",
        hashlib.sha256(b"matrix-explicit-stream-selection/1.0\0" + payload).hexdigest(),
        assignment,
        reference,
    )
