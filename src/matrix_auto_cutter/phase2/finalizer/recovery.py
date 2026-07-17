"""Explicit sidecar-authoritative package-2F recovery."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PureWindowsPath
from uuid import UUID

from matrix_auto_cutter.paths import expected_sidecar_path
from matrix_auto_cutter.phase2.artifacts import canonical_bytes, is_canonical_uuid4
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.finalizer.errors import (
    ArtifactLocation,
    FinalizationCancelled,
    FinalizationConflict,
    FinalizationRejected,
    FinalizationResult,
    Finalized,
    FinalizerDiagnostic,
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerFailure,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.loader import (
    JournalInputPaths,
    JournalLoadFailed,
    LoadedJournal,
    load_journal,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    MAX_FINALIZATION_RECEIPT_BYTES,
    MAX_INTENT_BYTES,
    MAX_STATE_BYTES,
    FinalizationIntent,
    FinalizationReceipt,
    FinalizerState,
    FinalizerStateName,
    JournalInputProfile,
    parse_finalization_receipt_bytes,
    parse_intent_bytes,
    parse_state_bytes,
)
from matrix_auto_cutter.phase2.finalizer.orchestrator import (
    FinalizationRequest,
    FinalizerPorts,
    _intent_bindings_match,
    _target_digest,
    finalize,
)
from matrix_auto_cutter.phase2.finalizer.persistence import (
    ArtifactStored,
    ArtifactStoreFailed,
    StateStored,
    project_artifact_path,
    store_immutable,
    store_state,
)
from matrix_auto_cutter.phase2.finalizer.publisher import (
    TargetInvalid,
    TargetMissing,
    TargetValid,
    read_committed_sidecar,
    validate_target,
)
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockAcquired,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
    ProjectLockLease,
    TargetLockLease,
    acquire_project_lock,
    acquire_target_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureReadFailed,
    ValidatedPath,
    derive_external_target,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.source_confirmation import ConfirmedSource
from matrix_auto_cutter.phase2.source_confirmation.capability import (
    _ConfirmedSourceUsage,
    _ConfirmedSourceUsageUnavailable,
    _run_confirmed_source_usage,
)
from matrix_auto_cutter.phase2.workspace import ProjectCapability


@dataclass(frozen=True, slots=True)
class RecoveryRequest:
    """Inputs to one explicit recovery operation."""

    project: ProjectCapability
    target_path: str
    expected_input_profile: JournalInputProfile
    finalizer_run_id: str | None = None
    expected_recording_id: str | None = None
    journal_inputs: JournalInputPaths | None = None
    confirmed_source: ConfirmedSource | None = None


@dataclass(frozen=True, slots=True)
class _RetryIntent:
    run_id: str


def _cancelled(phase: str) -> FinalizationCancelled:
    return FinalizationCancelled(
        failure(
            FinalizerErrorCode.CANCELLED,
            FinalizerErrorCategory.CANCELLED,
            phase,
            "recovery cancelled",
            retryable=True,
        )
    )


def _conflict(
    phase: str,
    message: str,
    *,
    committed: ArtifactLocation | None = None,
    underlying: object | None = None,
) -> FinalizationConflict:
    return FinalizationConflict(
        failure(
            FinalizerErrorCode.RECOVERY_CONFLICT,
            FinalizerErrorCategory.INTEGRITY,
            phase,
            message,
            underlying=underlying,
        ),
        committed,
    )


def _lock_failure(
    value: LockBusy | LockTimedOut | LockCancelled | LockAccessDenied | LockIoError,
    phase: str,
) -> FinalizationResult:
    if isinstance(value, LockCancelled):
        return _cancelled(phase)
    concurrent = isinstance(value, LockBusy | LockTimedOut)
    return FinalizationRejected(
        failure(
            (
                FinalizerErrorCode.FINALIZER_CONCURRENT
                if concurrent
                else FinalizerErrorCode.FINALIZER_INTERNAL
            ),
            (FinalizerErrorCategory.CONCURRENCY if concurrent else FinalizerErrorCategory.IO),
            phase,
            value.error.message,
            win32_code=value.error.win32_code,
            underlying=value.error,
            retryable=value.error.retryable,
        )
    )


def _artifact_path(
    ports: FinalizerPorts,
    request: RecoveryRequest,
    run_id: str,
    kind: str,
    recording_id: str | None = None,
) -> ValidatedPath | FinalizerFailure:
    if kind == "intent":
        return project_artifact_path(
            ports.win32,
            request.project,
            ("runs", run_id),
            "finalization-intent.json",
        )
    if kind == "state":
        return project_artifact_path(
            ports.win32,
            request.project,
            ("runs", run_id, "state"),
            "finalizer-state.json",
        )
    assert recording_id is not None
    return project_artifact_path(
        ports.win32,
        request.project,
        ("sidecar", "receipts"),
        f"{recording_id}.json",
    )


def _read_intent(
    ports: FinalizerPorts,
    path: ValidatedPath,
) -> tuple[FinalizationIntent, bytes, ArtifactLocation] | None | FinalizationConflict:
    read = secure_read_file(ports.win32, path, MAX_INTENT_BYTES)
    if isinstance(read, SecureReadFailed):
        if read.error.win32_code in {2, 3}:
            return None
        return _conflict("recovery.intent_read", read.error.message, underlying=read.error)
    try:
        intent = parse_intent_bytes(read.data)
    except (UnicodeError, ValueError) as exc:
        return _conflict("recovery.intent_parse", str(exc), underlying=exc)
    return (
        intent,
        read.data,
        ArtifactLocation(
            path.canonical_dos_path,
            hashlib.sha256(read.data).hexdigest(),
            len(read.data),
        ),
    )


def _load_optional_journal(
    ports: FinalizerPorts,
    request: RecoveryRequest,
) -> LoadedJournal | JournalLoadFailed | None:
    if request.journal_inputs is None:
        return None
    return load_journal(
        ports.win32,
        request.expected_input_profile,
        request.journal_inputs,
        expected_recording_id=request.expected_recording_id,
    )


def _intent_matches_commit(
    intent: FinalizationIntent,
    target: TargetValid,
    request: RecoveryRequest,
    target_digest: str,
    journal: LoadedJournal | None,
    usage: _ConfirmedSourceUsage | None,
) -> bool:
    sidecar = target.sidecar
    return (
        intent.project_id == request.project.document.project_id
        and intent.input_profile is request.expected_input_profile
        and intent.recording_id == str(sidecar.recording_session_id)
        and intent.finalizer_run_id == str(sidecar.lifecycle.finalizer_run_id)
        and intent.finalized_at == sidecar.lifecycle.finalized_at
        and intent.source_identity == sidecar.source
        and intent.target_path_digest == target_digest
        and (request.expected_recording_id in {None, intent.recording_id})
        and (
            journal is None
            or (
                journal.recording_id == intent.recording_id
                and journal.sha256 == intent.journal_sha256
                and journal.size_bytes == intent.journal_size_bytes
                and journal.bundle_binding == intent.bundle_binding
            )
        )
        and (
            usage is None
            or (
                usage.project_id == intent.project_id
                and usage.source_identity == intent.source_identity
                and usage.evidence.evidence_id == intent.source_identity_evidence_id
                and hashlib.sha256(canonical_bytes(usage.evidence)).hexdigest()
                == intent.source_identity_evidence_digest
                and usage.volume_id == intent.source_volume_id
                and usage.file_id == intent.source_file_id
                and usage.evidence.media_probe.artifact_id == intent.probe_artifact_id
                and usage.evidence.hash_run_id == intent.hash_artifact_id
                and (
                    usage.evidence.assignment.artifact_id
                    if usage.evidence.assignment is not None
                    else "not_available"
                )
                == intent.assignment_artifact_id
            )
        )
    )


def _target_matches_usage(
    ports: FinalizerPorts,
    target: ValidatedPath,
    usage: _ConfirmedSourceUsage,
    target_digest: str,
) -> bool:
    if not usage.matches_port(ports.win32):
        return False
    target_name = PureWindowsPath(expected_sidecar_path(usage.source_path.canonical_dos_path)).name
    expected = derive_external_target(ports.win32, usage.source_path, target_name)
    if isinstance(expected, PathRejected):
        return False
    digest = _target_digest(ports.win32, expected.path)
    return isinstance(digest, str) and digest == target_digest


def _receipt_for(
    request: RecoveryRequest,
    intent: FinalizationIntent,
    intent_data: bytes,
    target: TargetValid,
) -> FinalizationReceipt:
    return FinalizationReceipt(
        project_id=request.project.document.project_id,
        intent_run_id=intent.finalizer_run_id,
        target_generation=intent.target_generation,
        recording_id=intent.recording_id,
        source_identity=intent.source_identity,
        source_identity_digest=intent.source_identity_digest,
        sidecar_path_digest=intent.target_path_digest,
        sidecar_sha256=target.location.sha256,
        sidecar_size_bytes=target.location.size_bytes,
        finalizer_run_id=intent.finalizer_run_id,
        finalized_at=intent.finalized_at,
        intent_id=intent.finalization_key,
        intent_digest=hashlib.sha256(intent_data).hexdigest(),
    )


def _state_for(
    ports: FinalizerPorts,
    request: RecoveryRequest,
    target: TargetValid,
    run_id: str,
    intent: FinalizationIntent | None,
    journal: LoadedJournal | None,
) -> FinalizerState:
    return FinalizerState(
        project_id=request.project.document.project_id,
        finalizer_run_id=run_id,
        revision=0,
        current_state=FinalizerStateName.FINALIZED,
        input_profile=request.expected_input_profile,
        recording_id=str(target.sidecar.recording_session_id),
        intent_id=intent.finalization_key if intent is not None else "not_available",
        target_generation=intent.target_generation if intent is not None else "not_available",
        journal_sha256=journal.sha256 if journal is not None else "not_available",
        source_identity_digest=(
            intent.source_identity_digest if intent is not None else "not_available"
        ),
        sidecar_sha256=target.location.sha256,
        last_safe_transition="committing_sidecar->finalized",
        error_or_cancel_reference="not_available",
        observed_at=ports.now(),
        recovery_status="reconstructed",
    )


def _state_matches_generation(existing: FinalizerState, desired: FinalizerState) -> bool:
    return (
        existing.project_id == desired.project_id
        and existing.finalizer_run_id == desired.finalizer_run_id
        and existing.input_profile is desired.input_profile
        and existing.recording_id == desired.recording_id
        and existing.journal_sha256 == desired.journal_sha256
        and existing.intent_id in {desired.intent_id, "not_available"}
        and existing.target_generation in {desired.target_generation, "not_available"}
        and existing.source_identity_digest in {desired.source_identity_digest, "not_available"}
        and existing.sidecar_sha256 in {desired.sidecar_sha256, "not_available"}
    )


def _reconstruct_evidence(
    ports: FinalizerPorts,
    request: RecoveryRequest,
    project_lock: ProjectLockLease,
    target: TargetValid,
    intent_value: tuple[FinalizationIntent, bytes, ArtifactLocation] | None,
    journal: LoadedJournal | None,
    cancellation: CancellationToken,
) -> FinalizationResult:
    diagnostics: list[FinalizerDiagnostic] = []
    receipt_location: ArtifactLocation | None = None
    intent_location: ArtifactLocation | None = None
    intent = None
    if intent_value is not None:
        intent, intent_data, intent_location = intent_value
        receipt = _receipt_for(request, intent, intent_data, target)
        receipt_data = canonical_bytes(receipt)
        receipt_path = _artifact_path(
            ports,
            request,
            intent.finalizer_run_id,
            "receipt",
            intent.recording_id,
        )
        if isinstance(receipt_path, FinalizerFailure):
            diagnostics.append(FinalizerDiagnostic("recovery.receipt_path", receipt_path.message))
        else:
            ports.checkpoint("recovery.before_receipt")
            existing = secure_read_file(
                ports.win32,
                receipt_path,
                MAX_FINALIZATION_RECEIPT_BYTES,
            )
            if not isinstance(existing, SecureReadFailed):
                try:
                    parsed = parse_finalization_receipt_bytes(existing.data)
                except (UnicodeError, ValueError) as exc:
                    return _conflict(
                        "recovery.receipt_parse",
                        str(exc),
                        committed=target.location,
                        underlying=exc,
                    )
                if parsed != receipt or existing.data != receipt_data:
                    return _conflict(
                        "recovery.receipt_binding",
                        "existing receipt conflicts with the committed sidecar generation",
                        committed=target.location,
                    )
                receipt_location = ArtifactLocation(
                    receipt_path.canonical_dos_path,
                    hashlib.sha256(existing.data).hexdigest(),
                    len(existing.data),
                )
            elif existing.error.win32_code in {2, 3} and not cancellation.is_cancelled:
                stored = store_immutable(
                    ports.win32,
                    receipt_path,
                    receipt_data,
                    parse_finalization_receipt_bytes,
                    cancellation,
                    artifact="finalization-receipt",
                    operation_id=ports.uuid_factory(),
                )
                if isinstance(stored, ArtifactStored):
                    receipt_location = stored.location
                else:
                    assert isinstance(stored, ArtifactStoreFailed)
                    diagnostics.append(
                        FinalizerDiagnostic("recovery.receipt_publish", stored.error.message)
                    )
            else:
                diagnostics.append(
                    FinalizerDiagnostic("recovery.receipt_read", existing.error.message)
                )
    run_id = (
        intent.finalizer_run_id
        if intent is not None
        else str(target.sidecar.lifecycle.finalizer_run_id)
    )
    state_location: ArtifactLocation | None = None
    state_path = _artifact_path(ports, request, run_id, "state")
    if isinstance(state_path, FinalizerFailure):
        diagnostics.append(FinalizerDiagnostic("recovery.state_path", state_path.message))
    else:
        ports.checkpoint("recovery.before_state")
    if isinstance(state_path, ValidatedPath) and not cancellation.is_cancelled:
        desired = _state_for(ports, request, target, run_id, intent, journal)
        existing_state = secure_read_file(ports.win32, state_path, MAX_STATE_BYTES)
        if not isinstance(existing_state, SecureReadFailed):
            try:
                parsed_state = parse_state_bytes(existing_state.data)
            except (UnicodeError, ValueError) as exc:
                diagnostics.append(FinalizerDiagnostic("recovery.state_parse", str(exc), cause=exc))
            else:
                if not _state_matches_generation(parsed_state, desired):
                    return _conflict(
                        "recovery.state_binding",
                        "existing finalizer state belongs to another generation",
                        committed=target.location,
                    )
                stored_state = store_state(
                    ports.win32,
                    state_path,
                    desired,
                    cancellation,
                    project_lock,
                    operation_id=ports.uuid_factory(),
                )
                if isinstance(stored_state, StateStored):
                    state_location = stored_state.location
                else:
                    diagnostics.append(
                        FinalizerDiagnostic(
                            "recovery.state_publish",
                            stored_state.error.message,
                        )
                    )
        elif existing_state.error.win32_code in {2, 3}:
            stored_state = store_state(
                ports.win32,
                state_path,
                desired,
                cancellation,
                project_lock,
                operation_id=ports.uuid_factory(),
            )
            if isinstance(stored_state, StateStored):
                state_location = stored_state.location
            else:
                diagnostics.append(
                    FinalizerDiagnostic("recovery.state_publish", stored_state.error.message)
                )
        else:
            diagnostics.append(
                FinalizerDiagnostic("recovery.state_read", existing_state.error.message)
            )
    evidence_status = "not_reconstructable" if intent is None else "complete"
    if intent is not None and (receipt_location is None or state_location is None):
        evidence_status = "committed_evidence_incomplete"
    return Finalized(
        target.location,
        intent_location,
        receipt_location,
        state_location,
        str(target.sidecar.recording_session_id),
        target.sidecar.source,
        intent.target_generation if intent is not None else "not_reconstructable",
        True,
        evidence_status,
        tuple(diagnostics),
    )


def _recover_under_project(
    ports: FinalizerPorts,
    request: RecoveryRequest,
    target_path: ValidatedPath,
    project_lock: ProjectLockLease,
    cancellation: CancellationToken,
    usage: _ConfirmedSourceUsage | None,
) -> FinalizationResult | _RetryIntent:
    run_uuid = (
        UUID(request.finalizer_run_id)
        if request.finalizer_run_id is not None and is_canonical_uuid4(request.finalizer_run_id)
        else ports.uuid_factory()
    )
    acquired = acquire_target_lock(
        ports.win32,
        target_path,
        cancellation,
        run_id=run_uuid,
    )
    if not isinstance(acquired, LockAcquired):
        return _lock_failure(acquired, "recovery.target_lock")
    assert isinstance(acquired.lease, TargetLockLease)
    target_lock = acquired.lease
    try:
        if cancellation.is_cancelled:
            return _cancelled("recovery.after_target_lock")
        digest = _target_digest(ports.win32, target_path)
        if isinstance(digest, FinalizationRejected):
            return digest
        if usage is not None and not _target_matches_usage(ports, target_path, usage, digest):
            return _conflict(
                "recovery.target_binding",
                "recovery target is not the confirmed source's expected sidecar path",
            )
        ports.checkpoint("recovery.inspect_target")
        observed = read_committed_sidecar(
            ports.win32,
            target_path,
            usage.source_identity if usage is not None else None,
        )
        journal_value = _load_optional_journal(ports, request)
        if isinstance(journal_value, JournalLoadFailed):
            return FinalizationConflict(journal_value.error)
        journal = journal_value
        if cancellation.is_cancelled and not isinstance(observed, TargetValid):
            return _cancelled("recovery.after_inspection")
        if isinstance(observed, TargetInvalid):
            return _conflict(
                "recovery.target",
                observed.error.message,
                underlying=observed.error,
            )
        if isinstance(observed, TargetMissing):
            if request.finalizer_run_id is None or not is_canonical_uuid4(request.finalizer_run_id):
                return _conflict(
                    "recovery.uncommitted",
                    "no sidecar exists and no canonical finalizer run identifies an intent",
                )
            intent_path = _artifact_path(
                ports,
                request,
                request.finalizer_run_id,
                "intent",
            )
            if isinstance(intent_path, FinalizerFailure):
                return FinalizationConflict(intent_path)
            intent_value = _read_intent(ports, intent_path)
            if isinstance(intent_value, FinalizationConflict):
                return intent_value
            if intent_value is None:
                return _conflict(
                    "recovery.uncommitted",
                    "neither committed sidecar nor persisted intent exists",
                )
            if journal is None or usage is None:
                return _conflict(
                    "recovery.retry_inputs",
                    "uncommitted intent requires journal inputs and current ConfirmedSource",
                )
            assert request.finalizer_run_id is not None
            assert request.confirmed_source is not None
            assert request.journal_inputs is not None
            retry_request = FinalizationRequest(
                request.project,
                request.finalizer_run_id,
                request.expected_input_profile,
                request.journal_inputs,
                request.confirmed_source,
                request.expected_recording_id,
            )
            if not _intent_bindings_match(
                intent_value[0],
                retry_request,
                usage,
                journal,
                digest,
            ):
                return _conflict(
                    "recovery.retry_intent_binding",
                    "persisted uncommitted intent differs from current recovery authority",
                )
            return _RetryIntent(request.finalizer_run_id)
        assert isinstance(observed, TargetValid)
        run_id = str(observed.sidecar.lifecycle.finalizer_run_id)
        if request.finalizer_run_id is not None and request.finalizer_run_id != run_id:
            return _conflict(
                "recovery.run_binding",
                "requested run differs from the committed sidecar lifecycle",
                committed=observed.location,
            )
        if request.expected_recording_id is not None and request.expected_recording_id != str(
            observed.sidecar.recording_session_id
        ):
            return _conflict(
                "recovery.recording_binding",
                "requested recording differs from the committed sidecar",
                committed=observed.location,
            )
        intent_path = _artifact_path(ports, request, run_id, "intent")
        if isinstance(intent_path, FinalizerFailure):
            return _conflict(
                "recovery.intent_path",
                intent_path.message,
                committed=observed.location,
                underlying=intent_path,
            )
        read_intent = _read_intent(ports, intent_path)
        if isinstance(read_intent, FinalizationConflict):
            return FinalizationConflict(read_intent.error, observed.location)
        intent_value = read_intent
        if intent_value is not None:
            intent = intent_value[0]
            complete = validate_target(ports.win32, target_path, intent, None)
            if not isinstance(complete, TargetValid) or not _intent_matches_commit(
                intent,
                observed,
                request,
                digest,
                journal,
                usage,
            ):
                return _conflict(
                    "recovery.intent_binding",
                    "intent, sidecar, source, journal, or target binding differs",
                    committed=observed.location,
                    underlying=complete,
                )
        return _reconstruct_evidence(
            ports,
            request,
            project_lock,
            observed,
            intent_value,
            journal,
            cancellation,
        )
    finally:
        target_lock.release()


def _target(
    ports: FinalizerPorts, request: RecoveryRequest
) -> ValidatedPath | FinalizationRejected:
    validated = validate_path(
        ports.win32,
        request.target_path,
        PathRole.EXTERNAL_TARGET_CREATE_ONLY,
    )
    if isinstance(validated, PathRejected):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.INTEGRITY,
                "recovery.target_path",
                validated.error.message,
                underlying=validated.error,
            )
        )
    return validated.path


def recover(
    ports: FinalizerPorts,
    request: RecoveryRequest,
    cancellation: CancellationToken,
) -> FinalizationResult:
    """Recover explicitly; only a visible valid sidecar establishes commit."""
    if not isinstance(request.expected_input_profile, JournalInputProfile):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.INPUT,
                "recovery.profile",
                "recovery requires one explicit supported journal profile",
            )
        )
    if cancellation.is_cancelled:
        return _cancelled("recovery.before_locks")
    target = _target(ports, request)
    if isinstance(target, FinalizationRejected):
        return target

    def retry(value: FinalizationResult | _RetryIntent) -> FinalizationResult:
        if not isinstance(value, _RetryIntent):
            return value
        assert request.confirmed_source is not None
        assert request.journal_inputs is not None
        return finalize(
            ports,
            FinalizationRequest(
                request.project,
                value.run_id,
                request.expected_input_profile,
                request.journal_inputs,
                request.confirmed_source,
                request.expected_recording_id,
            ),
            cancellation,
        )

    if request.confirmed_source is not None:
        recovered = _run_confirmed_source_usage(
            request.confirmed_source,
            cancellation,
            lambda usage: usage.run_project_locked(
                lambda project_lock: _recover_under_project(
                    ports,
                    request,
                    target,
                    project_lock,
                    cancellation,
                    usage,
                )
            ),
        )
        if isinstance(recovered, _ConfirmedSourceUsageUnavailable):
            return _conflict(
                "recovery.source_authority",
                f"current ConfirmedSource usage unavailable: {recovered.reason}",
            )
        return retry(recovered)
    acquired = acquire_project_lock(
        ports.win32,
        request.project.document.project_id,
        cancellation,
        run_id=ports.uuid_factory(),
    )
    if not isinstance(acquired, LockAcquired):
        return _lock_failure(acquired, "recovery.project_lock")
    assert isinstance(acquired.lease, ProjectLockLease)
    project_lock = acquired.lease
    try:
        result = _recover_under_project(
            ports,
            request,
            target,
            project_lock,
            cancellation,
            None,
        )
    finally:
        project_lock.release()
    assert not isinstance(result, _RetryIntent)
    return result
