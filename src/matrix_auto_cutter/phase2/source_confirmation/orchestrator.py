"""Linear package-2E orchestration across probe, lease, hash, and identity evidence."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from matrix_auto_cutter.phase2.artifacts import canonical_bytes, is_canonical_uuid4
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateLease,
    RecheckCancelled,
    RecheckClosed,
    RecheckOk,
    RecheckUnstable,
)
from matrix_auto_cutter.phase2.close_gate.lease import (
    _LeaseUsageSession,
    _LeaseUsageUnavailable,
    _run_lease_usage,
)
from matrix_auto_cutter.phase2.pathing import ValidatedPath
from matrix_auto_cutter.phase2.probe import (
    PROBE_CONTRACT_VERSION,
    STREAM_SELECTION_POLICY_VERSION,
    ProbeDiagnosticProfile,
    ProbeError,
    ProbeErrorCode,
    ProbeFailed,
    ProbeOk,
    ProbeRequest,
    canonical_stream_evidence_bytes,
    run_probe,
    selection_semantically_matches,
    stream_selection_evidence_digest,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    FileSnapshot,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    SnapshotResult,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.source_confirmation.assignment import (
    ValidatedExplicitSelection,
    validate_stream_assignment,
)
from matrix_auto_cutter.phase2.source_confirmation.capability import _issue_confirmed_source
from matrix_auto_cutter.phase2.source_confirmation.contracts import (
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
    ConfirmationPorts,
    EvidenceReferences,
    PrimaryFailure,
    SourceAssignmentRequired,
    SourceConfirmationCancelled,
    SourceConfirmationFailed,
    SourceConfirmationRequest,
    SourceConfirmationResult,
    SourceConfirmed,
    SourceDisappeared,
    SourceInvalidated,
    SourceUnsupported,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    MAX_MEDIA_PROBE_BYTES,
    MAX_SOURCE_IDENTITY_EVIDENCE_BYTES,
    ArtifactReference,
    AutomaticSelectionEvidence,
    BinaryIdentityEvidence,
    MediaProbe,
    SnapshotEvidence,
    SourceIdentityEvidence,
    StreamEvidence,
    normalized_profile_from_probe,
    parse_media_probe_bytes,
    parse_source_identity_evidence_bytes,
    semantic_profile_digest,
)
from matrix_auto_cutter.phase2.source_confirmation.identity import (
    build_source_identity,
    source_identity_digest,
)
from matrix_auto_cutter.phase2.source_confirmation.path_revalidation import (
    PathDisappeared,
    PathInstanceChanged,
    PathRevalidated,
    revalidate_lease_path,
)
from matrix_auto_cutter.phase2.source_confirmation.persistence import (
    ArtifactConflict,
    ArtifactIoFailure,
    ArtifactPublishCancelled,
    ArtifactPublished,
    ArtifactPublishResult,
    artifact_target,
    publish_artifact,
)
from matrix_auto_cutter.phase2.source_confirmation.state import SourceState, SourceStateMachine
from matrix_auto_cutter.phase2.source_hash import (
    HashCancelled,
    HashCompleted,
    HashIoError,
    HashReceiptConflict,
    HashReceiptPublishCancelled,
    HashReceiptPublished,
    HashUnexpectedEof,
    SourceChanged,
    hash_lease_source,
    hash_receipt_bytes,
    publish_hash_receipt,
    receipt_from_completed,
)


@dataclass(frozen=True, slots=True)
class _BoundSelection:
    video: StreamEvidence
    audio: StreamEvidence
    video_reason_code: str
    audio_reason_code: str
    selection_identity: str
    mode: str
    assignment: ArtifactReference | None


def _failure(
    code: ConfirmationErrorCode,
    category: ConfirmationErrorCategory,
    phase: str,
    message: str,
    *,
    underlying: object | None = None,
    retryable: bool = False,
) -> ConfirmationFailure:
    return ConfirmationFailure(
        code,
        category,
        phase,
        message[:512],
        underlying=underlying,
        retryable=retryable,
    )


def _cancelled_error(phase: str, underlying: object | None = None) -> ConfirmationFailure:
    return _failure(
        ConfirmationErrorCode.CANCELLED,
        ConfirmationErrorCategory.CANCELLED,
        phase,
        "source confirmation cancelled",
        underlying=underlying,
        retryable=True,
    )


def _cancelled(
    state: SourceStateMachine,
    error: PrimaryFailure | None = None,
) -> SourceConfirmationCancelled:
    state.transition(SourceState.CANCELLED)
    return SourceConfirmationCancelled(
        error or _cancelled_error("confirmation.cancel"),
        SourceState.CANCELLED,
        state.history,
    )


def _failed(
    state: SourceStateMachine,
    error: PrimaryFailure,
    media: ArtifactReference | None = None,
) -> SourceConfirmationFailed:
    state.transition(SourceState.FAILED)
    return SourceConfirmationFailed(error, media, SourceState.FAILED, state.history)


def _invalidated(
    state: SourceStateMachine,
    error: PrimaryFailure,
) -> SourceInvalidated:
    state.transition(SourceState.INVALIDATED)
    return SourceInvalidated(error, SourceState.INVALIDATED, state.history)


def _disappeared(
    state: SourceStateMachine,
    error: PrimaryFailure,
) -> SourceDisappeared:
    state.transition(SourceState.DISAPPEARED)
    return SourceDisappeared(error, SourceState.DISAPPEARED, state.history)


def _unsupported(
    state: SourceStateMachine,
    error: PrimaryFailure,
    media: ArtifactReference | None = None,
) -> SourceUnsupported:
    state.transition(SourceState.UNSUPPORTED)
    return SourceUnsupported(error, media, SourceState.UNSUPPORTED, state.history)


def _snapshots_match_s0(lease: CloseGateLease, *snapshots: object) -> bool:
    for snapshot in snapshots:
        try:
            comparison = compare_snapshots(lease.s0, snapshot)  # type: ignore[arg-type]
        except (AttributeError, TypeError, ValueError):
            return False
        if not isinstance(comparison, SameInstanceUnchanged):
            return False
    return True


def _validate_probe_profile(
    request: SourceConfirmationRequest,
    profile: ProbeOk | ProbeDiagnosticProfile,
) -> ConfirmationFailure | None:
    value = profile.profile if isinstance(profile, ProbeOk) else profile
    if (
        value.probe_contract_version != PROBE_CONTRACT_VERSION
        or value.binary is not request.binary
        or value.source.canonical_dos_path != request.lease.source_path.canonical_dos_path
        or value.source.long_path != request.lease.source_path.long_path
        or value.expected_snapshot_key != request.lease.s0.snapshot_key
        or not _snapshots_match_s0(
            request.lease,
            value.snapshot_before,
            value.snapshot_after,
        )
        or canonical_stream_evidence_bytes(value.streams).decode("utf-8") == ""
    ):
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "probe.validation",
            "probe result does not match the current lease, binary, source, or snapshots",
        )
    expected_digest = stream_selection_evidence_digest(value.streams)
    if isinstance(profile, ProbeDiagnosticProfile):
        actual_digest = profile.stream_selection_evidence_digest
    else:
        selection = profile.profile.selection
        if not selection_semantically_matches(selection, profile.profile.streams):
            return _failure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                "probe.selection",
                "FinalizedStreamSelection failed authoritative semantic revalidation",
            )
        actual_digest = selection.stream_selection_evidence_digest
    if actual_digest != expected_digest:
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "probe.stream_evidence",
            "probe stream-selection evidence digest is inconsistent",
        )
    return None


def _media_probe(
    request: SourceConfirmationRequest,
    pre_path: PathRevalidated,
    s3: object,
    probe_result: ProbeOk | ProbeFailed,
) -> MediaProbe:
    diagnostic = probe_result.profile if isinstance(probe_result, ProbeFailed) else None
    if isinstance(probe_result, ProbeOk):
        bound: ProbeOk | ProbeDiagnosticProfile = probe_result
    else:
        assert diagnostic is not None
        bound = diagnostic
    profile = normalized_profile_from_probe(bound)
    runtime_profile = bound.profile if isinstance(bound, ProbeOk) else bound
    if isinstance(probe_result, ProbeOk):
        outcome = "selected"
        automatic = AutomaticSelectionEvidence.from_selection(probe_result.profile.selection)
        error_code = error_phase = error_detail = None
    else:
        outcome = (
            "ambiguous"
            if probe_result.error.code is ProbeErrorCode.AMBIGUOUS_STREAMS
            else "unsupported"
        )
        automatic = None
        error_code = probe_result.error.code.value
        error_phase = probe_result.error.phase
        error_detail = (
            probe_result.error.detail.value if probe_result.error.detail is not None else None
        )
    return MediaProbe(
        artifact_id=request.probe_id,
        project_id=request.project.document.project_id,
        probe_id=request.probe_id,
        probe_run_id=request.probe_run_id,
        lease_id=str(request.lease.lease_id),
        lease_epoch=str(request.lease.validation_epoch),
        volume_id=request.lease.volume_id,
        file_id=request.lease.file_id,
        source_path=request.lease.source_path.canonical_dos_path,
        s0=SnapshotEvidence.from_snapshot(request.lease.s0),
        s1=SnapshotEvidence.from_snapshot(request.lease.s1),
        s2=SnapshotEvidence.from_snapshot(request.lease.s2),
        s3=SnapshotEvidence.from_snapshot(s3),  # type: ignore[arg-type]
        pre_probe_path_revalidation=pre_path.evidence,
        binary=BinaryIdentityEvidence.from_binary(request.binary),
        probe_core_contract_version=runtime_profile.probe_contract_version,
        expected_snapshot_key=runtime_profile.expected_snapshot_key,
        probe_snapshot_before=SnapshotEvidence.from_snapshot(runtime_profile.snapshot_before),
        probe_snapshot_after=SnapshotEvidence.from_snapshot(runtime_profile.snapshot_after),
        profile=profile,
        semantic_profile_digest=semantic_profile_digest(profile),
        stream_selection_evidence_digest=stream_selection_evidence_digest(runtime_profile.streams),
        outcome=outcome,  # type: ignore[arg-type]
        automatic_selection=automatic,
        error_code=error_code,
        error_phase=error_phase,
        error_detail_code=error_detail,
    )


def _publish_media(
    request: SourceConfirmationRequest,
    ports: ConfirmationPorts,
    media: MediaProbe,
    cancellation: CancellationToken,
) -> ArtifactPublishResult:
    target = artifact_target(
        ports.win32,
        request.project,
        ("probe", request.probe_id),
        "media-probe.json",
    )
    if isinstance(target, ConfirmationFailure):
        return ArtifactIoFailure(target)
    return publish_artifact(
        ports.win32,
        target,
        media,
        MAX_MEDIA_PROBE_BYTES,
        parse_media_probe_bytes,
        cancellation,
        artifact_name="media-probe",
        artifact_id=request.probe_id,
        artifact_type="media_probe",
    )


def _recheck(
    session: _LeaseUsageSession,
    request: SourceConfirmationRequest,
    cancellation: CancellationToken,
    phase: str,
) -> FileSnapshot | ConfirmationFailure:
    result = session.recheck(cancellation)
    if isinstance(result, RecheckOk):
        comparison = compare_snapshots(request.lease.s0, result.snapshot)
        if isinstance(comparison, SameInstanceUnchanged):
            return result.snapshot
        if isinstance(comparison, SameInstanceChanged | DifferentInstance):
            return _failure(
                ConfirmationErrorCode.SOURCE_CHANGED,
                ConfirmationErrorCategory.INTEGRITY,
                phase,
                f"{phase.upper()} proves changed source evidence",
                underlying=comparison,
                retryable=True,
            )
        assert isinstance(comparison, NotComparable | ComparisonFailed)
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            phase,
            f"{phase.upper()} cannot prove unchanged S0 evidence",
            underlying=comparison,
        )
    if isinstance(result, RecheckUnstable):
        return _failure(
            ConfirmationErrorCode.SOURCE_CHANGED,
            ConfirmationErrorCategory.INTEGRITY,
            phase,
            f"{phase.upper()} proves the source changed",
            underlying=result.error,
            retryable=True,
        )
    if isinstance(result, RecheckCancelled):
        return _cancelled_error(phase, result.error)
    if isinstance(result, RecheckClosed):
        return _failure(
            ConfirmationErrorCode.LEASE_INVALID,
            ConfirmationErrorCategory.INTEGRITY,
            phase,
            "lease close linearized before recheck publication",
            underlying=result.error,
        )
    error = result.error
    return ConfirmationFailure(
        ConfirmationErrorCode.IO,
        ConfirmationErrorCategory.IO,
        phase,
        error.message,
        win32_code=error.win32_code,
        cause=error.cause,
        underlying=error,
    )


def _selection_from_media(
    request: SourceConfirmationRequest,
    ports: ConfirmationPorts,
    media: MediaProbe,
    media_reference: ArtifactReference,
    probe_result: ProbeOk | ProbeFailed,
) -> _BoundSelection | ConfirmationFailure:
    streams = {item.index: item for item in media.profile.streams}
    if isinstance(probe_result, ProbeOk):
        selection = probe_result.profile.selection
        if not selection_semantically_matches(selection, probe_result.profile.streams):
            return _failure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                "selection.automatic",
                "automatic selection changed before stream binding",
            )
        if request.assignment is not None:
            return _failure(
                ConfirmationErrorCode.ASSIGNMENT_INVALID,
                ConfirmationErrorCategory.INPUT,
                "selection.automatic",
                "explicit assignment cannot override an unambiguous automatic selection",
            )
        return _BoundSelection(
            streams[selection.video_index],
            streams[selection.audio_index],
            selection.video_reason_code.value,
            selection.audio_reason_code.value,
            selection.selection_identity,
            "automatic_unique",
            None,
        )
    if probe_result.error.code is not ProbeErrorCode.AMBIGUOUS_STREAMS:
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "selection.nonautomatic",
            "only genuine stream ambiguity can be resolved by assignment",
            underlying=probe_result.error,
        )
    if request.assignment is None:
        return _failure(
            ConfirmationErrorCode.ASSIGNMENT_INVALID,
            ConfirmationErrorCategory.INPUT,
            "selection.assignment",
            "an explicit stream assignment is required",
        )
    explicit = validate_stream_assignment(
        ports.win32,
        request.project,
        request.assignment,
        media,
    )
    if isinstance(explicit, ConfirmationFailure):
        return explicit
    assert isinstance(explicit, ValidatedExplicitSelection)
    return _BoundSelection(
        streams[explicit.video_index],
        streams[explicit.audio_index],
        explicit.video_reason_code,
        explicit.audio_reason_code,
        explicit.selection_identity,
        "explicit_assignment",
        explicit.reference,
    )


def _validate_completed(
    request: SourceConfirmationRequest,
    completed: HashCompleted,
) -> ConfirmationFailure | None:
    try:
        receipt = receipt_from_completed(completed)
    except TypeError as exc:
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "hash.completed",
            "hash result is not an authentic HashCompleted value",
            underlying=exc,
        )
    comparison = compare_snapshots(request.lease.s0, completed.s4)
    if isinstance(comparison, SameInstanceChanged | DifferentInstance):
        return _failure(
            ConfirmationErrorCode.SOURCE_CHANGED,
            ConfirmationErrorCategory.INTEGRITY,
            "hash.s4",
            "S4 differs from the current lease S0",
            underlying=comparison,
            retryable=True,
        )
    if not isinstance(comparison, SameInstanceUnchanged):
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "hash.s4",
            "S4 is not comparable to the current lease S0",
            underlying=comparison,
        )
    if (
        completed.s0 is not request.lease.s0
        or receipt.project_id != request.project.document.project_id
        or receipt.hash_run_id != request.hash_run_id
        or receipt.lease_id != str(request.lease.lease_id)
        or receipt.validation_epoch != str(request.lease.validation_epoch)
        or receipt.s0_snapshot_key != request.lease.s0.snapshot_key
        or receipt.s4_snapshot_key != completed.s4.snapshot_key
        or receipt.volume_id != request.lease.volume_id
        or receipt.file_id != request.lease.file_id
        or receipt.bytes_read != request.lease.s0.size_bytes
        or completed.sha256 != receipt.sha256
    ):
        return _failure(
            ConfirmationErrorCode.SOURCE_CHANGED,
            ConfirmationErrorCategory.INTEGRITY,
            "hash.bindings",
            "HashCompleted crosses the current lease, S0, project, or run binding",
            underlying=receipt,
            retryable=True,
        )
    return None


def _validate_identity_evidence_chain(
    request: SourceConfirmationRequest,
    evidence: SourceIdentityEvidence,
    media: MediaProbe,
    media_reference: ArtifactReference,
    selection: _BoundSelection,
    completed: HashCompleted,
    receipt_reference: ArtifactReference,
    pre_path: PathRevalidated,
    final_path: PathRevalidated,
    s5: FileSnapshot,
) -> ConfirmationFailure | None:
    """Revalidate every current runtime input before durable identity publication."""
    rebuilt = build_source_identity(
        request.lease.source_path.canonical_dos_path,
        completed,
        media.profile.format,
        selection.video,
        selection.audio,
        request.binding,
    )
    if isinstance(rebuilt, ConfirmationFailure):
        return rebuilt
    streams = {item.index: item for item in media.profile.streams}
    checks = (
        media_reference.artifact_type == "media_probe",
        media_reference.artifact_id == request.probe_id,
        media_reference.artifact_digest == hashlib.sha256(canonical_bytes(media)).hexdigest(),
        evidence.media_probe == media_reference,
        evidence.project_id == request.project.document.project_id == media.project_id,
        evidence.identity_run_id == request.identity_run_id,
        evidence.lease_id == str(request.lease.lease_id) == media.lease_id,
        evidence.lease_epoch == str(request.lease.validation_epoch) == media.lease_epoch,
        evidence.source_path == request.lease.source_path.canonical_dos_path == media.source_path,
        (evidence.s0, evidence.s1, evidence.s2, evidence.s3)
        == (media.s0, media.s1, media.s2, media.s3),
        evidence.s4 == SnapshotEvidence.from_snapshot(completed.s4),
        evidence.s5 == SnapshotEvidence.from_snapshot(s5),
        evidence.pre_probe_path_revalidation == pre_path.evidence,
        evidence.pre_commit_path_revalidation == final_path.evidence,
        evidence.probe_core_contract_version == media.probe_core_contract_version,
        evidence.parser_version == media.parser_version,
        evidence.profile_version == media.profile_version,
        evidence.binary_sha256 == media.binary.sha256 == request.binary.sha256,
        evidence.binary_version == media.binary.semantic_version,
        evidence.stream_selection_policy == STREAM_SELECTION_POLICY_VERSION,
        evidence.stream_selection_evidence_digest == media.stream_selection_evidence_digest,
        evidence.video_index == selection.video.index,
        evidence.audio_index == selection.audio.index,
        streams.get(evidence.video_index) == selection.video,
        streams.get(evidence.audio_index) == selection.audio,
        evidence.video_reason_code == selection.video_reason_code,
        evidence.audio_reason_code == selection.audio_reason_code,
        evidence.selection_identity == selection.selection_identity,
        evidence.selection_mode == selection.mode,
        evidence.assignment == selection.assignment,
        evidence.source_identity == rebuilt,
        evidence.source_identity_digest == source_identity_digest(rebuilt),
        evidence.hash_run_id == request.hash_run_id == completed.hash_run_id,
        evidence.hash_receipt == receipt_from_completed(completed),
        evidence.hash_receipt_digest == receipt_reference.artifact_digest,
        receipt_reference.artifact_type == "source_hash_receipt",
        receipt_reference.artifact_id == request.hash_run_id,
        evidence.sha256 == completed.sha256,
        evidence.bytes_read == completed.bytes_read,
        evidence.binding_mode == request.binding.value,
    )
    if not all(checks):
        return _failure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "identity.evidence_validation",
            "SourceIdentityEvidence differs from the current probe, selection, hash, or lease",
        )
    return None


def _probe_failure_result(
    state: SourceStateMachine,
    error: ProbeError,
) -> SourceConfirmationResult:
    if error.code is ProbeErrorCode.CANCELLED:
        return _cancelled(state, error)
    if error.code is ProbeErrorCode.SOURCE_CHANGED:
        return _invalidated(state, error)
    if error.code in {ProbeErrorCode.UNSUPPORTED_VERSION, ProbeErrorCode.UNSUPPORTED_MEDIA}:
        return _unsupported(state, error)
    return _failed(state, error)


def _confirm_in_usage(
    session: _LeaseUsageSession,
    ports: ConfirmationPorts,
    request: SourceConfirmationRequest,
    cancellation: CancellationToken,
    state: SourceStateMachine,
) -> SourceConfirmationResult:
    if cancellation.is_cancelled:
        return _cancelled(state)
    pre_path = revalidate_lease_path(ports.win32, request.lease, "before_probe")
    if isinstance(pre_path, PathDisappeared):
        return _disappeared(state, pre_path.error)
    if isinstance(pre_path, PathInstanceChanged):
        return _invalidated(state, pre_path.error)
    if not isinstance(pre_path, PathRevalidated):
        return _failed(state, pre_path.error)
    if cancellation.is_cancelled:
        return _cancelled(state)

    state.transition(SourceState.PROBING)
    if cancellation.is_cancelled:
        return _cancelled(state)

    def current_snapshot(path: object) -> SnapshotResult:
        if not isinstance(path, ValidatedPath):
            raise TypeError("probe snapshotter requires a ValidatedPath")
        return snapshot_file(ports.win32, path)

    probe_result = run_probe(
        ProbeRequest(
            request.binary,
            request.lease.source_path,
            pre_path.evidence.snapshot.snapshot_key,
            request.probe_timeout_seconds,
        ),
        ports.binary_trust,
        ports.probe_process,
        current_snapshot,
        cancellation,
    )
    if cancellation.is_cancelled and not (
        isinstance(probe_result, ProbeFailed)
        and probe_result.error.code is not ProbeErrorCode.CANCELLED
    ):
        return _cancelled(
            state,
            probe_result.error if isinstance(probe_result, ProbeFailed) else None,
        )
    if isinstance(probe_result, ProbeFailed) and probe_result.profile is None:
        return _probe_failure_result(state, probe_result.error)

    if isinstance(probe_result, ProbeOk):
        bound_profile: ProbeOk | ProbeDiagnosticProfile = probe_result
    else:
        assert probe_result.profile is not None
        bound_profile = probe_result.profile
    validation_error = _validate_probe_profile(request, bound_profile)
    if validation_error is not None:
        return _failed(state, validation_error)
    if cancellation.is_cancelled:
        return _cancelled(state)

    s3 = _recheck(session, request, cancellation, "s3")
    if isinstance(s3, ConfirmationFailure):
        if s3.code is ConfirmationErrorCode.CANCELLED:
            return _cancelled(state, s3)
        if s3.code is ConfirmationErrorCode.SOURCE_CHANGED:
            return _invalidated(state, s3)
        return _failed(state, s3)
    if cancellation.is_cancelled:
        return _cancelled(state)

    try:
        media = _media_probe(request, pre_path, s3, probe_result)
    except (TypeError, ValueError) as exc:
        return _failed(
            state,
            _failure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                "media_probe.build",
                str(exc),
                underlying=exc,
            ),
        )
    published_media = _publish_media(request, ports, media, cancellation)
    if isinstance(published_media, ArtifactPublishCancelled):
        return _cancelled(state, published_media.error)
    if isinstance(published_media, ArtifactConflict | ArtifactIoFailure):
        return _failed(state, published_media.error)
    assert isinstance(published_media, ArtifactPublished)
    media_reference = published_media.reference

    if (
        isinstance(probe_result, ProbeFailed)
        and probe_result.error.code is not ProbeErrorCode.AMBIGUOUS_STREAMS
    ):
        return _unsupported(state, probe_result.error, media_reference)
    if (
        isinstance(probe_result, ProbeFailed)
        and request.assignment is None
        and probe_result.error.code is ProbeErrorCode.AMBIGUOUS_STREAMS
    ):
        state.transition(SourceState.UNSUPPORTED)
        return SourceAssignmentRequired(
            probe_result.error,
            media_reference,
            SourceState.UNSUPPORTED,
            state.history,
        )
    if cancellation.is_cancelled:
        return _cancelled(state)
    selection = _selection_from_media(
        request,
        ports,
        media,
        media_reference,
        probe_result,
    )
    if isinstance(selection, ConfirmationFailure):
        return _failed(state, selection, media_reference)
    if cancellation.is_cancelled:
        return _cancelled(state)
    state.transition(SourceState.PROBED)

    if cancellation.is_cancelled:
        return _cancelled(state)
    state.transition(SourceState.HASHING)
    hash_result = hash_lease_source(
        request.lease,
        cancellation,
        request.project.document.project_id,
        request.hash_run_id,
    )
    if isinstance(hash_result, HashCancelled):
        return _cancelled(state, hash_result.error)
    if isinstance(hash_result, SourceChanged):
        return _invalidated(state, hash_result.error)
    if isinstance(hash_result, HashIoError | HashUnexpectedEof):
        return _failed(state, hash_result.error, media_reference)
    assert isinstance(hash_result, HashCompleted)
    if cancellation.is_cancelled:
        return _cancelled(state)
    completed_error = _validate_completed(request, hash_result)
    if completed_error is not None:
        if completed_error.code is ConfirmationErrorCode.SOURCE_CHANGED:
            return _invalidated(state, completed_error)
        return _failed(state, completed_error, media_reference)
    state.transition(SourceState.HASH_COMPLETED)

    identity = build_source_identity(
        request.lease.source_path.canonical_dos_path,
        hash_result,
        media.profile.format,
        selection.video,
        selection.audio,
        request.binding,
    )
    if isinstance(identity, ConfirmationFailure):
        return _failed(state, identity, media_reference)
    identity_id = source_identity_digest(identity)
    receipt_target = artifact_target(
        ports.win32,
        request.project,
        ("identity", identity_id),
        "hash-receipt.json",
    )
    if isinstance(receipt_target, ConfirmationFailure):
        return _failed(state, receipt_target, media_reference)
    if cancellation.is_cancelled:
        return _cancelled(state)
    receipt_publish = publish_hash_receipt(
        ports.win32,
        receipt_target,
        hash_result,
        cancellation,
    )
    if isinstance(receipt_publish, HashReceiptPublishCancelled):
        return _cancelled(state, receipt_publish.error)
    if isinstance(receipt_publish, HashReceiptConflict):
        return _failed(state, receipt_publish.error, media_reference)
    if not isinstance(receipt_publish, HashReceiptPublished):
        return _failed(state, receipt_publish.error, media_reference)
    receipt = receipt_from_completed(hash_result)
    receipt_data = hash_receipt_bytes(receipt)
    receipt_reference = ArtifactReference(
        artifact_type="source_hash_receipt",
        artifact_id=request.hash_run_id,
        artifact_digest=hashlib.sha256(receipt_data).hexdigest(),
        canonical_path=receipt_target.canonical_dos_path,
    )
    state.transition(SourceState.CONFIRMING_IDENTITY)

    if cancellation.is_cancelled:
        return _cancelled(state)
    s5 = _recheck(session, request, cancellation, "s5")
    if isinstance(s5, ConfirmationFailure):
        if s5.code is ConfirmationErrorCode.CANCELLED:
            return _cancelled(state, s5)
        if s5.code is ConfirmationErrorCode.SOURCE_CHANGED:
            return _invalidated(state, s5)
        return _failed(state, s5, media_reference)
    if cancellation.is_cancelled:
        return _cancelled(state)
    final_path = revalidate_lease_path(
        ports.win32,
        request.lease,
        "before_identity_commit",
    )
    if isinstance(final_path, PathDisappeared):
        return _disappeared(state, final_path.error)
    if isinstance(final_path, PathInstanceChanged):
        return _invalidated(state, final_path.error)
    if not isinstance(final_path, PathRevalidated):
        return _failed(state, final_path.error, media_reference)
    if cancellation.is_cancelled:
        return _cancelled(state)

    evidence = SourceIdentityEvidence(
        project_id=request.project.document.project_id,
        identity_run_id=request.identity_run_id,
        evidence_id=identity_id,
        source_identity=identity,
        source_identity_digest=identity_id,
        lease_id=str(request.lease.lease_id),
        lease_epoch=str(request.lease.validation_epoch),
        s0=SnapshotEvidence.from_snapshot(request.lease.s0),
        s1=SnapshotEvidence.from_snapshot(request.lease.s1),
        s2=SnapshotEvidence.from_snapshot(request.lease.s2),
        s3=media.s3,
        s4=SnapshotEvidence.from_snapshot(hash_result.s4),
        s5=SnapshotEvidence.from_snapshot(s5),
        volume_id=request.lease.volume_id,
        file_id=request.lease.file_id,
        source_path=request.lease.source_path.canonical_dos_path,
        pre_probe_path_revalidation=pre_path.evidence,
        pre_commit_path_revalidation=final_path.evidence,
        media_probe=media_reference,
        probe_core_contract_version=media.probe_core_contract_version,
        parser_version=media.parser_version,
        profile_version=media.profile_version,
        binary_sha256=media.binary.sha256,
        binary_version=media.binary.semantic_version,
        stream_selection_policy=STREAM_SELECTION_POLICY_VERSION,
        stream_selection_evidence_digest=media.stream_selection_evidence_digest,
        video_index=selection.video.index,
        audio_index=selection.audio.index,
        video_reason_code=selection.video_reason_code,
        audio_reason_code=selection.audio_reason_code,
        selection_identity=selection.selection_identity,
        selection_mode=selection.mode,  # type: ignore[arg-type]
        assignment=selection.assignment,
        hash_run_id=request.hash_run_id,
        hash_receipt=receipt,
        hash_receipt_digest=receipt_reference.artifact_digest,
        sha256=hash_result.sha256,
        bytes_read=hash_result.bytes_read,
        hash_contract_version=receipt.hash_contract_version,
        binding_mode=identity.binding.value,
    )
    evidence_error = _validate_identity_evidence_chain(
        request,
        evidence,
        media,
        media_reference,
        selection,
        hash_result,
        receipt_reference,
        pre_path,
        final_path,
        s5,
    )
    if evidence_error is not None:
        return _failed(state, evidence_error, media_reference)
    evidence_target = artifact_target(
        ports.win32,
        request.project,
        ("identity", identity_id),
        "source-identity-evidence.json",
    )
    if isinstance(evidence_target, ConfirmationFailure):
        return _failed(state, evidence_target, media_reference)
    if cancellation.is_cancelled:
        return _cancelled(state)
    evidence_publish = publish_artifact(
        ports.win32,
        evidence_target,
        evidence,
        MAX_SOURCE_IDENTITY_EVIDENCE_BYTES,
        parse_source_identity_evidence_bytes,
        cancellation,
        artifact_name="source-identity-evidence",
        artifact_id=identity_id,
        artifact_type="source_identity_evidence",
    )
    if isinstance(evidence_publish, ArtifactPublishCancelled):
        return _cancelled(state, evidence_publish.error)
    if isinstance(evidence_publish, ArtifactConflict | ArtifactIoFailure):
        return _failed(state, evidence_publish.error, media_reference)
    assert isinstance(evidence_publish, ArtifactPublished)
    if cancellation.is_cancelled:
        return _cancelled(state)
    if not session.commit(cancellation):
        if cancellation.is_cancelled:
            return _cancelled(state)
        return _failed(
            state,
            _failure(
                ConfirmationErrorCode.LEASE_INVALID,
                ConfirmationErrorCategory.INTEGRITY,
                "confirmation.commit",
                "lease close linearized before ConfirmedSource commit",
            ),
            media_reference,
        )
    confirmed = _issue_confirmed_source(
        identity,
        evidence,
        request.project.document.project_id,
        request.identity_run_id,
        request.lease,
    )
    state.transition(SourceState.CONFIRMED)
    return SourceConfirmed(
        confirmed,
        identity,
        EvidenceReferences(
            media_reference,
            receipt_reference,
            evidence_target.canonical_dos_path,
            evidence_publish.reference.artifact_digest,
        ),
        SourceState.CONFIRMED,
        state.history,
    )


def _validate_request(request: SourceConfirmationRequest) -> ConfirmationFailure | None:
    if (
        not request.project.trusted
        or not all(
            is_canonical_uuid4(value)
            for value in (
                request.identity_run_id,
                request.probe_id,
                request.probe_run_id,
                request.hash_run_id,
            )
        )
        or not isinstance(request.lease, CloseGateLease)
        or request.lease.closed
        or request.lease.lease_id != request.lease.validation_epoch
        or not 1 <= request.probe_timeout_seconds <= 600
    ):
        return _failure(
            ConfirmationErrorCode.INVALID_INPUT,
            ConfirmationErrorCategory.INPUT,
            "confirmation.input",
            "project, run IDs, lease, binding, or timeout is invalid",
        )
    return None


def confirm_source(
    ports: ConfirmationPorts,
    request: SourceConfirmationRequest,
    cancellation: CancellationToken,
) -> SourceConfirmationResult:
    """Run a fresh probe and hash under one continuous authentic lease epoch."""
    state = SourceStateMachine()
    if cancellation.is_cancelled:
        return _cancelled(state)
    invalid = _validate_request(request)
    if invalid is not None:
        return _failed(state, invalid)
    if cancellation.is_cancelled:
        return _cancelled(state)
    result = _run_lease_usage(
        request.lease,
        request.project.document.project_id,
        cancellation,
        lambda session: _confirm_in_usage(
            session,
            ports,
            request,
            cancellation,
            state,
        ),
    )
    if isinstance(result, _LeaseUsageUnavailable):
        if result.reason == "cancelled":
            return _cancelled(state)
        return _failed(
            state,
            _failure(
                ConfirmationErrorCode.LEASE_INVALID,
                ConfirmationErrorCategory.INTEGRITY,
                "confirmation.lease",
                f"lease integration usage unavailable: {result.reason}",
            ),
        )
    return result
