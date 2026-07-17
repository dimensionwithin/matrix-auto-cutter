"""Package-2F finalization orchestration over Phase 1 and packages 2A-2E."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PureWindowsPath
from uuid import UUID, uuid4

from matrix_auto_cutter.models import SourceBinding
from matrix_auto_cutter.paths import expected_sidecar_path
from matrix_auto_cutter.phase2.artifacts import canonical_bytes, is_canonical_uuid4
from matrix_auto_cutter.phase2.atomic_project import cleanup_external_owned_temp
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
    MAX_INTENT_BYTES,
    BundleBinding,
    FinalizationIntent,
    FinalizationReceipt,
    FinalizerState,
    FinalizerStateName,
    JournalInputProfile,
    finalization_key,
    parse_finalization_receipt_bytes,
    parse_intent_bytes,
    strict_artifact_bytes,
)
from matrix_auto_cutter.phase2.finalizer.persistence import (
    ArtifactStored,
    ArtifactStoreFailed,
    StateStored,
    StateStoreFailed,
    project_artifact_path,
    store_immutable,
    store_state,
)
from matrix_auto_cutter.phase2.finalizer.publisher import (
    SidecarPublishFailed,
    TargetInvalid,
    TargetMissing,
    TargetValid,
    publish_sidecar,
    validate_target,
)
from matrix_auto_cutter.phase2.finalizer.sidecar_builder import build_sidecar
from matrix_auto_cutter.phase2.finalizer.state_machine import FinalizerStateMachine
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockAcquired,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
    TargetLockLease,
    acquire_target_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    SecureReadFailed,
    ValidatedPath,
    derive_external_target,
    secure_read_file,
)
from matrix_auto_cutter.phase2.source_confirmation import ConfirmedSource
from matrix_auto_cutter.phase2.source_confirmation.capability import (
    _ConfirmedSourceUsage,
    _ConfirmedSourceUsageUnavailable,
    _run_confirmed_source_usage,
)
from matrix_auto_cutter.phase2.source_confirmation.identity import source_identity_digest
from matrix_auto_cutter.phase2.win32_port import Win32Err, Win32Port
from matrix_auto_cutter.phase2.workspace import ProjectCapability

FINALIZER_VERSION = "phase2f/1.0"


@dataclass(frozen=True, slots=True)
class FinalizerPorts:
    """Injectable OS, time, identifier, and crash-checkpoint boundaries."""

    win32: Win32Port
    now: Callable[[], datetime]
    uuid_factory: Callable[[], UUID] = uuid4
    checkpoint: Callable[[str], None] = lambda _name: None


@dataclass(frozen=True, slots=True)
class FinalizationRequest:
    """Explicit request bound to one project, profile, and live confirmed source."""

    project: ProjectCapability
    finalizer_run_id: str
    input_profile: JournalInputProfile
    inputs: JournalInputPaths
    confirmed_source: ConfirmedSource
    expected_recording_id: str | None = None


def _cancelled(phase: str) -> FinalizationCancelled:
    return FinalizationCancelled(
        failure(
            FinalizerErrorCode.CANCELLED,
            FinalizerErrorCategory.CANCELLED,
            phase,
            "finalization cancelled",
            retryable=True,
        )
    )


def _target_digest(port: Win32Port, target: ValidatedPath) -> str | FinalizationRejected:
    key = port.ordinal_case_key(target.canonical_dos_path)
    if isinstance(key, Win32Err):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.IO,
                "target.digest",
                key.error.detail,
                win32_code=key.error.code,
            )
        )
    return hashlib.sha256(
        b"matrix-auto-cutter/sidecar-target/1.0\0" + key.value.encode("utf-16-le")
    ).hexdigest()


def _journal_matches_source(
    port: Win32Port,
    journal: LoadedJournal,
    source_path: str,
    binding: SourceBinding,
) -> bool:
    stop = journal.records[-1]
    recorded = str(stop.get("last_recording_path", ""))
    actual = PureWindowsPath(source_path)
    candidate = PureWindowsPath(recorded)
    if binding in {SourceBinding.DIRECT_MP4, SourceBinding.OBS_AUTO_REMUX}:
        expected = actual if binding is SourceBinding.DIRECT_MP4 else actual.with_suffix(".mkv")
        candidate_key = port.ordinal_case_key(str(candidate))
        expected_key = port.ordinal_case_key(str(expected))
        return (
            not isinstance(candidate_key, Win32Err)
            and not isinstance(expected_key, Win32Err)
            and candidate_key.value == expected_key.value
        )
    return True


def _new_uuid(factory: Callable[[], UUID], field: str) -> str:
    value = factory()
    if value.version != 4 or not is_canonical_uuid4(str(value)):
        raise ValueError(f"{field} factory value is not canonical UUIDv4")
    return str(value)


def _build_intent(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    usage: _ConfirmedSourceUsage,
    journal: LoadedJournal,
    target_digest: str,
) -> FinalizationIntent:
    evidence = usage.evidence
    identity = usage.source_identity
    provisional = FinalizationIntent.model_construct(
        finalizer_run_id=request.finalizer_run_id,
        finalized_at=ports.now(),
        project_id=usage.project_id,
        input_profile=request.input_profile,
        recording_id=journal.recording_id,
        journal_sha256=journal.sha256,
        journal_size_bytes=journal.size_bytes,
        bundle_binding=journal.bundle_binding,
        source_identity=identity,
        source_identity_digest=source_identity_digest(identity),
        source_identity_evidence_id=evidence.evidence_id,
        source_identity_evidence_digest=hashlib.sha256(canonical_bytes(evidence)).hexdigest(),
        source_volume_id=usage.volume_id,
        source_file_id=usage.file_id,
        probe_artifact_id=evidence.media_probe.artifact_id,
        hash_artifact_id=evidence.hash_run_id,
        assignment_artifact_id=(
            evidence.assignment.artifact_id if evidence.assignment is not None else "not_available"
        ),
        bundle_schema_version=(
            "1.0" if isinstance(journal.bundle_binding, BundleBinding) else "not_available"
        ),
        target_path_digest=target_digest,
        target_generation=_new_uuid(ports.uuid_factory, "target generation"),
        synthetic_stop_event_id=_new_uuid(ports.uuid_factory, "stop event"),
        finalization_key="0" * 64,
    )
    payload = provisional.model_dump()
    payload["finalization_key"] = finalization_key(provisional)
    return FinalizationIntent.model_validate(payload)


def _intent_bindings_match(
    intent: FinalizationIntent,
    request: FinalizationRequest,
    usage: _ConfirmedSourceUsage,
    journal: LoadedJournal,
    target_digest: str,
) -> bool:
    evidence = usage.evidence
    return (
        intent.finalizer_run_id == request.finalizer_run_id
        and intent.project_id == request.project.document.project_id == usage.project_id
        and intent.input_profile is request.input_profile
        and intent.recording_id == journal.recording_id
        and intent.journal_sha256 == journal.sha256
        and intent.journal_size_bytes == journal.size_bytes
        and intent.bundle_binding == journal.bundle_binding
        and intent.source_identity == usage.source_identity
        and intent.source_identity_digest == source_identity_digest(usage.source_identity)
        and intent.source_identity_evidence_id == evidence.evidence_id
        and intent.source_identity_evidence_digest
        == hashlib.sha256(canonical_bytes(evidence)).hexdigest()
        and intent.source_volume_id == usage.volume_id
        and intent.source_file_id == usage.file_id
        and intent.probe_artifact_id == evidence.media_probe.artifact_id
        and intent.hash_artifact_id == evidence.hash_run_id
        and intent.assignment_artifact_id
        == (evidence.assignment.artifact_id if evidence.assignment is not None else "not_available")
        and intent.target_path_digest == target_digest
    )


def _load_or_create_intent(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    usage: _ConfirmedSourceUsage,
    journal: LoadedJournal,
    target_digest: str,
    target: ValidatedPath,
    cancellation: CancellationToken,
) -> tuple[FinalizationIntent, bytes, ArtifactLocation, bool] | FinalizationResult:
    read = secure_read_file(ports.win32, target, MAX_INTENT_BYTES)
    if not isinstance(read, SecureReadFailed):
        try:
            intent = parse_intent_bytes(read.data)
        except (UnicodeError, ValueError) as exc:
            return FinalizationConflict(
                failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.INTEGRITY,
                    "intent.parse",
                    str(exc),
                    cause=exc,
                )
            )
        if not _intent_bindings_match(intent, request, usage, journal, target_digest):
            return FinalizationConflict(
                failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.INTEGRITY,
                    "intent.binding",
                    "existing finalization intent differs from the requested generation",
                )
            )
        data = strict_artifact_bytes(intent, MAX_INTENT_BYTES)
        location = ArtifactLocation(
            target.canonical_dos_path,
            hashlib.sha256(data).hexdigest(),
            len(data),
        )
        return intent, data, location, True
    if read.error.win32_code not in {2, 3}:
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.ATOMIC_PUBLISH_FAILED,
                FinalizerErrorCategory.IO,
                "intent.read",
                read.error.message,
                win32_code=read.error.win32_code,
                underlying=read.error,
            )
        )
    if cancellation.is_cancelled:
        return _cancelled("before_intent")
    try:
        intent = _build_intent(ports, request, usage, journal, target_digest)
        data = strict_artifact_bytes(intent, MAX_INTENT_BYTES)
    except (ArithmeticError, TypeError, ValueError) as exc:
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.INTERNAL,
                "intent.construct",
                str(exc),
                cause=exc,
            )
        )
    stored = store_immutable(
        ports.win32,
        target,
        data,
        parse_intent_bytes,
        cancellation,
        artifact="finalization-intent",
        operation_id=UUID(request.finalizer_run_id),
    )
    if isinstance(stored, ArtifactStoreFailed):
        if stored.error.code is FinalizerErrorCode.CANCELLED:
            return FinalizationCancelled(stored.error)
        return FinalizationConflict(stored.error)
    return intent, data, stored.location, False


def _state_value(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    journal: LoadedJournal,
    machine: FinalizerStateMachine,
    intent: FinalizationIntent | None,
    sidecar_sha256: str,
    *,
    recovery_status: str = "normal",
) -> FinalizerState:
    return FinalizerState(
        project_id=request.project.document.project_id,
        finalizer_run_id=request.finalizer_run_id,
        revision=0,
        current_state=machine.state,
        input_profile=request.input_profile,
        recording_id=journal.recording_id,
        intent_id=intent.finalization_key if intent is not None else "not_available",
        target_generation=intent.target_generation if intent is not None else "not_available",
        journal_sha256=journal.sha256,
        source_identity_digest=(
            intent.source_identity_digest if intent is not None else "not_available"
        ),
        sidecar_sha256=sidecar_sha256,
        last_safe_transition="->".join(item.value for item in machine.history[-2:]),
        error_or_cancel_reference="not_available",
        observed_at=ports.now(),
        recovery_status=recovery_status,  # type: ignore[arg-type]
    )


def _persist_state(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    usage: _ConfirmedSourceUsage,
    state_path: ValidatedPath,
    journal: LoadedJournal,
    machine: FinalizerStateMachine,
    intent: FinalizationIntent | None,
    sidecar_sha256: str,
    cancellation: CancellationToken,
) -> StateStored | StateStoreFailed:
    desired = _state_value(ports, request, journal, machine, intent, sidecar_sha256)
    return usage.run_project_locked(
        lambda lock: store_state(
            ports.win32,
            state_path,
            desired,
            cancellation,
            lock,
            operation_id=ports.uuid_factory(),
        )
    )


def _lock_failure(
    result: LockBusy | LockTimedOut | LockCancelled | LockAccessDenied | LockIoError,
) -> FinalizationResult:
    error = result.error
    if isinstance(result, LockCancelled):
        return _cancelled("target_lock")
    if isinstance(result, LockBusy | LockTimedOut):
        code = FinalizerErrorCode.FINALIZER_CONCURRENT
        category = FinalizerErrorCategory.CONCURRENCY
    else:
        assert isinstance(result, LockAccessDenied | LockIoError)
        code = FinalizerErrorCode.FINALIZER_INTERNAL
        category = FinalizerErrorCategory.IO
    return FinalizationRejected(
        failure(
            code,
            category,
            "target_lock",
            error.message,
            win32_code=error.win32_code,
            underlying=error,
            retryable=error.retryable,
        )
    )


def _in_usage(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    journal: LoadedJournal,
    cancellation: CancellationToken,
    machine: FinalizerStateMachine,
    usage: _ConfirmedSourceUsage,
) -> FinalizationResult:
    if (
        usage.project_id != request.project.document.project_id
        or not request.project.trusted
        or not usage.matches_port(ports.win32)
    ):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.SOURCE_UNAUTHORIZED,
                FinalizerErrorCategory.INTEGRITY,
                "source.project_binding",
                "ConfirmedSource does not authorize the requested live project",
            )
        )
    if not _journal_matches_source(
        ports.win32,
        journal,
        usage.source_path.canonical_dos_path,
        usage.source_identity.binding,
    ):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.JOURNAL_SOURCE_MISMATCH,
                FinalizerErrorCategory.INTEGRITY,
                "journal.source_binding",
                "journal output path does not bind the confirmed source",
            )
        )
    for state in (
        FinalizerStateName.AWAITING_CLOSE,
        FinalizerStateName.PROBING,
        FinalizerStateName.HASHING,
        FinalizerStateName.CONFIRMING_IDENTITY,
    ):
        machine.transition(state)
    target_name = PureWindowsPath(expected_sidecar_path(usage.source_path.canonical_dos_path)).name
    target_result = derive_external_target(ports.win32, usage.source_path, target_name)
    temp_result = derive_external_target(
        ports.win32,
        usage.source_path,
        f".{target_name}.tmp.{request.finalizer_run_id}",
    )
    if isinstance(target_result, PathRejected) or isinstance(temp_result, PathRejected):
        rejected = target_result if isinstance(target_result, PathRejected) else temp_result
        assert isinstance(rejected, PathRejected)
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.INTEGRITY,
                "target.path",
                rejected.error.message,
                underlying=rejected.error,
            )
        )
    target = target_result.path
    temp = temp_result.path
    digest = _target_digest(ports.win32, target)
    if isinstance(digest, FinalizationRejected):
        return digest
    lock_result = acquire_target_lock(
        ports.win32,
        target,
        cancellation,
        run_id=UUID(request.finalizer_run_id),
    )
    if not isinstance(lock_result, LockAcquired):
        return _lock_failure(lock_result)
    assert isinstance(lock_result.lease, TargetLockLease)
    target_lock = lock_result.lease
    result: FinalizationResult
    cleanup: FinalizerDiagnostic | None = None
    try:
        machine.transition(FinalizerStateName.PREPARING_INTENT)
        state_path = project_artifact_path(
            ports.win32,
            request.project,
            ("runs", request.finalizer_run_id, "state"),
            "finalizer-state.json",
        )
        intent_path = project_artifact_path(
            ports.win32,
            request.project,
            ("runs", request.finalizer_run_id),
            "finalization-intent.json",
        )
        if not isinstance(state_path, ValidatedPath) or not isinstance(intent_path, ValidatedPath):
            error = state_path if not isinstance(state_path, ValidatedPath) else intent_path
            assert not isinstance(error, ValidatedPath)
            return FinalizationRejected(error)
        _persist_state(
            ports,
            request,
            usage,
            state_path,
            journal,
            machine,
            None,
            "not_available",
            cancellation,
        )
        ports.checkpoint("before_intent")
        prepared = _load_or_create_intent(
            ports,
            request,
            usage,
            journal,
            digest,
            intent_path,
            cancellation,
        )
        if isinstance(
            prepared,
            Finalized | FinalizationCancelled | FinalizationRejected | FinalizationConflict,
        ):
            return prepared
        intent, intent_data, intent_location, retrying = prepared
        ports.checkpoint("after_intent")
        if cancellation.is_cancelled:
            return _cancelled("after_intent")
        if retrying:
            temp_cleanup = cleanup_external_owned_temp(
                ports.win32,
                temp,
                owned_suffix=f".tmp.{request.finalizer_run_id}",
            )
            if temp_cleanup:
                primary = temp_cleanup[0]
                return FinalizationRejected(
                    failure(
                        FinalizerErrorCode.ATOMIC_PUBLISH_FAILED,
                        FinalizerErrorCategory.IO,
                        "temp.retry_cleanup",
                        primary.message,
                        win32_code=primary.win32_code,
                        underlying=temp_cleanup,
                    )
                )
        _persist_state(
            ports,
            request,
            usage,
            state_path,
            journal,
            machine,
            intent,
            "not_available",
            cancellation,
        )
        machine.transition(FinalizerStateName.CONSTRUCTING_SIDECAR)
        _persist_state(
            ports,
            request,
            usage,
            state_path,
            journal,
            machine,
            intent,
            "not_available",
            cancellation,
        )
        if cancellation.is_cancelled:
            return _cancelled("before_sidecar_construction")
        sidecar = build_sidecar(journal, intent, cancellation)
        if isinstance(sidecar, FinalizerFailure):
            if sidecar.code is FinalizerErrorCode.CANCELLED:
                return FinalizationCancelled(sidecar)
            return FinalizationRejected(sidecar)
        ports.checkpoint("after_sidecar_construction")
        observed = validate_target(ports.win32, target, intent, sidecar)
        if isinstance(observed, TargetInvalid):
            return FinalizationConflict(observed.error)
        machine.transition(FinalizerStateName.COMMITTING_SIDECAR)
        _persist_state(
            ports,
            request,
            usage,
            state_path,
            journal,
            machine,
            intent,
            "not_available",
            cancellation,
        )
        if isinstance(observed, TargetValid):
            published_location = observed.location
            idempotent = True
        else:
            assert isinstance(observed, TargetMissing)
            ports.checkpoint("before_temp")
            published = publish_sidecar(
                ports.win32,
                target,
                temp,
                intent_path,
                intent_data,
                intent,
                sidecar,
                usage,
                cancellation,
            )
            if isinstance(published, SidecarPublishFailed):
                if published.error.code is FinalizerErrorCode.CANCELLED:
                    return FinalizationCancelled(published.error)
                return FinalizationConflict(published.error)
            published_location = published.location
            idempotent = published.idempotent
        ports.checkpoint("after_commit")
        intent_digest = hashlib.sha256(intent_data).hexdigest()
        receipt = FinalizationReceipt(
            project_id=request.project.document.project_id,
            intent_run_id=request.finalizer_run_id,
            target_generation=intent.target_generation,
            recording_id=journal.recording_id,
            source_identity=intent.source_identity,
            source_identity_digest=intent.source_identity_digest,
            sidecar_path_digest=intent.target_path_digest,
            sidecar_sha256=published_location.sha256,
            sidecar_size_bytes=published_location.size_bytes,
            finalizer_run_id=intent.finalizer_run_id,
            finalized_at=intent.finalized_at,
            intent_id=intent.finalization_key,
            intent_digest=intent_digest,
        )
        receipt_data = canonical_bytes(receipt)
        receipt_path = project_artifact_path(
            ports.win32,
            request.project,
            ("sidecar", "receipts"),
            f"{journal.recording_id}.json",
        )
        diagnostics: list[FinalizerDiagnostic] = []
        receipt_location = None
        state_location = None
        ports.checkpoint("before_receipt")
        if not cancellation.is_cancelled and isinstance(receipt_path, ValidatedPath):
            stored_receipt = store_immutable(
                ports.win32,
                receipt_path,
                receipt_data,
                parse_finalization_receipt_bytes,
                cancellation,
                artifact="finalization-receipt",
                operation_id=ports.uuid_factory(),
            )
            if isinstance(stored_receipt, ArtifactStored):
                receipt_location = stored_receipt.location
            else:
                diagnostics.append(
                    FinalizerDiagnostic("receipt.publish", stored_receipt.error.message)
                )
        elif not isinstance(receipt_path, ValidatedPath):
            diagnostics.append(FinalizerDiagnostic("receipt.path", receipt_path.message))
        machine.transition(FinalizerStateName.FINALIZED)
        ports.checkpoint("before_final_state")
        if not cancellation.is_cancelled:
            stored_state = _persist_state(
                ports,
                request,
                usage,
                state_path,
                journal,
                machine,
                intent,
                published_location.sha256,
                cancellation,
            )
            if isinstance(stored_state, StateStored):
                state_location = stored_state.location
            else:
                diagnostics.append(FinalizerDiagnostic("state.publish", stored_state.error.message))
        ports.checkpoint("after_final_state")
        result = Finalized(
            published_location,
            intent_location,
            receipt_location,
            state_location,
            journal.recording_id,
            intent.source_identity,
            intent.target_generation,
            idempotent,
            "complete" if receipt_location is not None else "committed_evidence_incomplete",
            tuple(diagnostics),
        )
    finally:
        released = target_lock.release()
        if released is not None:
            cleanup = FinalizerDiagnostic(
                "target_lock.release",
                released.message,
                released.win32_code,
                released.cause,
            )
    if cleanup is not None and isinstance(result, Finalized):
        return Finalized(
            result.sidecar,
            result.intent,
            result.receipt,
            result.state,
            result.recording_id,
            result.source_identity,
            result.target_generation,
            result.idempotent,
            result.evidence_status,
            (*result.diagnostics, cleanup),
        )
    return result


def finalize(
    ports: FinalizerPorts,
    request: FinalizationRequest,
    cancellation: CancellationToken,
) -> FinalizationResult:
    """Finalize exactly one explicit input profile under authentic 2E authority."""
    machine = FinalizerStateMachine()
    if (
        not is_canonical_uuid4(request.finalizer_run_id)
        or not isinstance(request.input_profile, JournalInputProfile)
        or request.project.document.project_id != request.confirmed_source.project_id
    ):
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.SOURCE_UNAUTHORIZED,
                FinalizerErrorCategory.INPUT,
                "finalizer.input",
                "run, profile, project, or ConfirmedSource binding is invalid",
            )
        )
    if cancellation.is_cancelled:
        return _cancelled("before_loader")
    machine.transition(FinalizerStateName.VALIDATING_INPUT)
    loaded = load_journal(
        ports.win32,
        request.input_profile,
        request.inputs,
        expected_recording_id=request.expected_recording_id,
    )
    if isinstance(loaded, JournalLoadFailed):
        return FinalizationRejected(loaded.error)
    if cancellation.is_cancelled:
        return _cancelled("after_loader")
    machine.transition(FinalizerStateName.RESOLVING_SOURCE)
    result = _run_confirmed_source_usage(
        request.confirmed_source,
        cancellation,
        lambda usage: _in_usage(ports, request, loaded, cancellation, machine, usage),
    )
    if isinstance(result, _ConfirmedSourceUsageUnavailable):
        if cancellation.is_cancelled:
            return _cancelled("source_usage")
        return FinalizationRejected(
            failure(
                FinalizerErrorCode.SOURCE_UNAUTHORIZED,
                FinalizerErrorCategory.INTEGRITY,
                "source_usage",
                f"authenticated ConfirmedSource usage unavailable: {result.reason}",
            )
        )
    return result
