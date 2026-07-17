from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from tests.phase2.finalizer.conftest import RUN_ID, SESSION_ID, add_validated_file, journal_bytes
from tests.phase2.source_confirmation.conftest import make_case

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationCancelled,
    FinalizationConflict,
    FinalizationRejected,
    FinalizationRequest,
    Finalized,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    RecoveryRequest,
    finalize,
    recover,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    finalization_key,
    parse_finalization_receipt_bytes,
    parse_intent_bytes,
    parse_state_bytes,
)
from matrix_auto_cutter.phase2.locks import LockAcquired, acquire_project_lock, acquire_target_lock
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.source_confirmation import SourceConfirmed, confirm_source


def _context(*, checkpoint=lambda name: None):
    case = make_case()
    confirmed = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(confirmed, SourceConfirmed)
    journal = add_validated_file(
        case.port,
        r"C:\Input\recording.ndjson",
        journal_bytes(),
    )
    ports = FinalizerPorts(
        case.port,
        lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        uuid4,
        checkpoint,
    )
    request = FinalizationRequest(
        case.project,
        RUN_ID,
        JournalInputProfile.LEGACY,
        JournalInputPaths(journal),
        confirmed.confirmed_source,
        SESSION_ID,
    )
    return case, confirmed, ports, request


def _recovery(request, sidecar_path: str, **changes) -> RecoveryRequest:
    values = {
        "project": request.project,
        "target_path": sidecar_path,
        "expected_input_profile": request.input_profile,
        "finalizer_run_id": request.finalizer_run_id,
        "expected_recording_id": request.expected_recording_id,
        "journal_inputs": request.inputs,
        "confirmed_source": request.confirmed_source,
    }
    values.update(changes)
    return RecoveryRequest(**values)


def test_recovery_retries_persisted_intent_without_promoting_temp() -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == "after_intent":
            token.cancel()

    case, _, ports, request = _context(checkpoint=cancel)
    try:
        first = finalize(ports, request, token)
        assert isinstance(first, FinalizationCancelled)
        target = r"C:\Sources\source.obs-events.json"
        assert case.port._key(target) not in case.port.nodes
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        result = recover(
            clean,
            _recovery(request, target),
            CancellationToken(),
        )
        assert isinstance(result, Finalized)
        assert case.port._key(target) in case.port.nodes
        assert not any(".TMP." in key for key in case.port.nodes)
    finally:
        case.close()


@pytest.mark.parametrize("crash_phase", ["during_write", "after_flush"])
def test_recovery_discards_only_its_bound_crash_temp(monkeypatch, crash_phase) -> None:
    case, _, ports, request = _context()
    original_write = case.port.write_file
    original_flush = case.port.flush_file
    calls = 0
    own_temp = rf"C:\Sources\.source.obs-events.json.tmp.{RUN_ID}"
    own_temp_key = case.port._key(own_temp)

    def crashing_write(handle, data):
        nonlocal calls
        key, _ = case.port.handles[handle.value]
        if key != own_temp_key:
            return original_write(handle, data)
        calls += 1
        if calls == 2:
            raise SystemExit("crash during temp write")
        previous = case.port.partial_write
        case.port.partial_write = 2
        try:
            return original_write(handle, data)
        finally:
            case.port.partial_write = previous

    def crashing_flush(handle):
        key, _ = case.port.handles[handle.value]
        if key != own_temp_key:
            return original_flush(handle)
        original_flush(handle)
        raise SystemExit("crash after temp flush")

    if crash_phase == "during_write":
        monkeypatch.setattr(case.port, "write_file", crashing_write)
    else:
        monkeypatch.setattr(case.port, "flush_file", crashing_flush)
    foreign_temp = r"C:\Sources\.source.obs-events.json.tmp.99999999-9999-4999-8999-999999999999"
    try:
        with pytest.raises(SystemExit):
            finalize(ports, request, CancellationToken())
        assert case.port._key(own_temp) in case.port.nodes
        case.port.add_file(foreign_temp, b"foreign")
        monkeypatch.setattr(case.port, "write_file", original_write)
        monkeypatch.setattr(case.port, "flush_file", original_flush)
        case.port.partial_write = None
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        recovered = recover(
            clean,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized)
        assert case.port._key(own_temp) not in case.port.nodes
        assert case.port._key(foreign_temp) in case.port.nodes
    finally:
        case.close()


def test_crash_after_commit_reconstructs_receipt_and_state() -> None:
    def crash(name: str) -> None:
        if name == "after_commit":
            raise SystemExit("crash")

    case, _, ports, request = _context(checkpoint=crash)
    try:
        with pytest.raises(SystemExit):
            finalize(ports, request, CancellationToken())
        target = r"C:\Sources\source.obs-events.json"
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        result = recover(clean, _recovery(request, target), CancellationToken())
        assert isinstance(result, Finalized)
        assert result.receipt is not None
        assert result.state is not None
    finally:
        case.close()


def test_valid_phase1_sidecar_without_intent_remains_commit() -> None:
    case, _, ports, request = _context()
    result = finalize(ports, request, CancellationToken())
    assert isinstance(result, Finalized)
    intent_key = case.port._key(result.intent.canonical_path)
    receipt_key = case.port._key(result.receipt.canonical_path)
    state_key = case.port._key(result.state.canonical_path)
    del case.port.nodes[intent_key]
    del case.port.nodes[receipt_key]
    del case.port.nodes[state_key]
    case.request.lease.close()
    recovered = recover(
        ports,
        _recovery(
            request,
            result.sidecar.canonical_path,
            confirmed_source=None,
            journal_inputs=None,
        ),
        CancellationToken(),
    )
    assert isinstance(recovered, Finalized)
    assert recovered.intent is None
    assert recovered.receipt is None
    assert recovered.evidence_status == "not_reconstructable"
    case.close()


def test_recovery_sidecar_missing_or_corrupt_is_not_commit() -> None:
    case, _, ports, request = _context()
    try:
        missing = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(missing, FinalizationConflict)

        journal_node = case.port.nodes[case.port._key(request.inputs.journal.canonical_dos_path)]
        original_journal = bytes(journal_node.data)
        journal_node.data[:] = b"bad\n"
        invalid_journal = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(invalid_journal, FinalizationConflict)
        journal_node.data[:] = original_journal

        case.port.add_file(r"C:\Sources\source.obs-events.json", b"bad\n")
        corrupt = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(corrupt, FinalizationConflict)
    finally:
        case.close()


def test_recovery_binding_conflicts_keep_committed_sidecar() -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        before = bytes(case.port.nodes[case.port._key(committed.sidecar.canonical_path)].data)
        wrong_run = recover(
            ports,
            _recovery(
                request,
                committed.sidecar.canonical_path,
                finalizer_run_id="99999999-9999-4999-8999-999999999999",
            ),
            CancellationToken(),
        )
        assert isinstance(wrong_run, FinalizationConflict)
        wrong_recording = recover(
            ports,
            _recovery(
                request,
                committed.sidecar.canonical_path,
                expected_recording_id="99999999-9999-4999-8999-999999999999",
                journal_inputs=None,
            ),
            CancellationToken(),
        )
        assert isinstance(wrong_recording, FinalizationConflict)
        assert (
            bytes(case.port.nodes[case.port._key(committed.sidecar.canonical_path)].data) == before
        )
    finally:
        case.close()


def test_recovery_rejects_a_target_not_bound_to_the_current_source() -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        data = bytes(case.port.nodes[case.port._key(committed.sidecar.canonical_path)].data)
        other = r"C:\Sources\other.obs-events.json"
        case.port.add_file(other, data)
        result = recover(ports, _recovery(request, other), CancellationToken())
        assert isinstance(result, FinalizationConflict)
        assert result.error.phase == "recovery.target_binding"
        assert bytes(case.port.nodes[case.port._key(other)].data) == data
    finally:
        case.close()


def test_recovery_target_binding_rejects_adapter_and_path_derivation(monkeypatch) -> None:
    from tests.phase2.conftest import FakePort

    import matrix_auto_cutter.phase2.finalizer.recovery as module
    from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
    from matrix_auto_cutter.phase2.errors import failure as detail
    from matrix_auto_cutter.phase2.pathing import PathRejected

    case, _, ports, request = _context()
    try:
        other = FakePort()
        other.make_tree(r"C:\Sources")
        mismatched_ports = FinalizerPorts(other, ports.now, uuid4)
        result = recover(
            mismatched_ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(result, FinalizationConflict)
        assert result.error.phase == "recovery.target_binding"

        rejected = PathRejected(
            detail(ErrorCode.PATH_INPUT_FORM, ErrorCategory.INPUT, "path", "rejected")
        )
        monkeypatch.setattr(module, "derive_external_target", lambda *args: rejected)
        result = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(result, FinalizationConflict)
        assert result.error.phase == "recovery.target_binding"
    finally:
        case.close()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_identity_evidence_id", "f" * 64),
        ("source_identity_evidence_digest", "f" * 64),
        ("probe_artifact_id", "foreign-probe"),
        ("hash_artifact_id", "99999999-9999-4999-8999-999999999999"),
        ("assignment_artifact_id", "foreign-assignment"),
    ],
)
def test_recovery_cross_checks_all_current_source_evidence(field, replacement) -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        node = case.port.nodes[case.port._key(committed.intent.canonical_path)]
        intent = parse_intent_bytes(bytes(node.data))
        values = intent.model_dump()
        values["source_identity"] = intent.source_identity
        values["bundle_binding"] = intent.bundle_binding
        values[field] = replacement
        provisional = type(intent).model_construct(**values)
        values["finalization_key"] = finalization_key(provisional)
        node.data[:] = canonical_bytes(type(intent).model_validate(values))
        result = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(result, FinalizationConflict)
        assert result.committed_sidecar == committed.sidecar
    finally:
        case.close()


def test_corrupt_intent_and_receipt_report_committed_conflict() -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        intent_node = case.port.nodes[case.port._key(committed.intent.canonical_path)]
        original_intent = bytes(intent_node.data)
        intent_node.data[:] = b"bad\n"
        conflict = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(conflict, FinalizationConflict)
        assert conflict.committed_sidecar == committed.sidecar
        intent_node.data[:] = original_intent

        receipt_node = case.port.nodes[case.port._key(committed.receipt.canonical_path)]
        receipt_node.data[:] = b"bad\n"
        conflict = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(conflict, FinalizationConflict)
        assert conflict.committed_sidecar == committed.sidecar
    finally:
        case.close()


def test_receipt_binding_and_state_binding_conflicts() -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        receipt_node = case.port.nodes[case.port._key(committed.receipt.canonical_path)]
        receipt = parse_finalization_receipt_bytes(bytes(receipt_node.data))
        receipt_node.data[:] = canonical_bytes(
            receipt.model_copy(update={"sidecar_sha256": "f" * 64})
        )
        conflict = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(conflict, FinalizationConflict)

        receipt_node.data[:] = canonical_bytes(receipt)
        state_node = case.port.nodes[case.port._key(committed.state.canonical_path)]
        state = parse_state_bytes(bytes(state_node.data))
        state_node.data[:] = canonical_bytes(
            state.model_copy(update={"recording_id": "99999999-9999-4999-8999-999999999999"})
        )
        conflict = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(conflict, FinalizationConflict)
    finally:
        case.close()


def test_corrupt_state_is_diagnostic_and_sidecar_stays_valid() -> None:
    case, _, ports, request = _context()
    try:
        committed = finalize(ports, request, CancellationToken())
        assert isinstance(committed, Finalized)
        state_node = case.port.nodes[case.port._key(committed.state.canonical_path)]
        state_node.data[:] = b"bad\n"
        recovered = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized)
        assert any(item.phase == "recovery.state_parse" for item in recovered.diagnostics)
    finally:
        case.close()


def test_recovery_cancellation_closed_source_and_invalid_target() -> None:
    case, _, ports, request = _context()
    token = CancellationToken()
    token.cancel()
    assert isinstance(
        recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            token,
        ),
        FinalizationCancelled,
    )
    case.request.lease.close()
    assert isinstance(
        recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        ),
        FinalizationConflict,
    )
    invalid = recover(
        ports,
        _recovery(request, r"relative\sidecar.json", confirmed_source=None),
        CancellationToken(),
    )
    assert isinstance(invalid, FinalizationConflict | Finalized) is False
    case.close()


def test_recovery_project_and_target_lock_concurrency() -> None:
    case, _, ports, request = _context()
    case.request.lease.close()
    target_path = r"C:\Sources\source.obs-events.json"
    project = acquire_project_lock(
        case.port,
        request.project.document.project_id,
        CancellationToken(),
        run_id=uuid4(),
    )
    assert isinstance(project, LockAcquired)
    try:
        busy = recover(
            ports,
            _recovery(request, target_path, confirmed_source=None),
            CancellationToken(),
        )
        assert not isinstance(busy, Finalized)
    finally:
        project.lease.release()

    validated = validate_path(
        case.port,
        target_path,
        PathRole.EXTERNAL_TARGET_CREATE_ONLY,
    )
    assert isinstance(validated, PathValidated)
    target = acquire_target_lock(
        case.port,
        validated.path,
        CancellationToken(),
        run_id=uuid4(),
    )
    assert isinstance(target, LockAcquired)
    try:
        busy = recover(
            ports,
            _recovery(request, target_path, confirmed_source=None),
            CancellationToken(),
        )
        assert not isinstance(busy, Finalized)
    finally:
        target.lease.release()
    case.close()


def test_recovery_invalid_profile_lock_cancel_and_after_target_cancel(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.recovery as module
    from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
    from matrix_auto_cutter.phase2.errors import failure as detail
    from matrix_auto_cutter.phase2.locks import LockCancelled

    case, _, ports, request = _context()
    invalid = RecoveryRequest(
        request.project,
        r"C:\Sources\source.obs-events.json",
        "legacy",  # type: ignore[arg-type]
    )
    assert isinstance(recover(ports, invalid, CancellationToken()), FinalizationRejected)
    lock_error = detail(ErrorCode.CANCELLED, ErrorCategory.CANCELLED, "lock", "cancel")
    assert isinstance(
        module._lock_failure(LockCancelled(lock_error), "lock"), FinalizationCancelled
    )

    token = CancellationToken()
    original = module.acquire_target_lock

    def acquire(*args, **kwargs):
        value = original(*args, **kwargs)
        token.cancel()
        return value

    monkeypatch.setattr(module, "acquire_target_lock", acquire)
    try:
        result = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            token,
        )
        assert isinstance(result, FinalizationCancelled)
    finally:
        case.close()


def test_recovery_missing_target_intent_edges(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.recovery as module

    case, _, ports, request = _context()
    try:
        no_run = recover(
            ports,
            _recovery(
                request,
                r"C:\Sources\source.obs-events.json",
                finalizer_run_id=None,
            ),
            CancellationToken(),
        )
        assert isinstance(no_run, FinalizationConflict)

        original_path = module._artifact_path

        def fail_intent(*args, **kwargs):
            if args[3] == "intent":
                from matrix_auto_cutter.phase2.finalizer.errors import (
                    FinalizerErrorCategory,
                    FinalizerErrorCode,
                    failure,
                )

                return failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.IO,
                    "intent.path",
                    "failed",
                )
            return original_path(*args, **kwargs)

        monkeypatch.setattr(module, "_artifact_path", fail_intent)
        assert isinstance(
            recover(
                ports,
                _recovery(request, r"C:\Sources\source.obs-events.json"),
                CancellationToken(),
            ),
            FinalizationConflict,
        )
    finally:
        case.close()


def test_recovery_corrupt_uncommitted_intent_and_missing_authority() -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == "after_intent":
            token.cancel()

    case, _, ports, request = _context(checkpoint=cancel)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
        intent_path = (
            request.project.project_directory.canonical_dos_path
            + rf"\runs\{RUN_ID}\finalization-intent.json"
        )
        intent_node = case.port.nodes[case.port._key(intent_path)]
        original = bytes(intent_node.data)
        intent_node.data[:] = b"bad\n"
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        assert isinstance(
            recover(
                clean,
                _recovery(request, r"C:\Sources\source.obs-events.json"),
                CancellationToken(),
            ),
            FinalizationConflict,
        )
        intent_node.data[:] = original
        case.request.lease.close()
        assert isinstance(
            recover(
                clean,
                _recovery(
                    request,
                    r"C:\Sources\source.obs-events.json",
                    confirmed_source=None,
                ),
                CancellationToken(),
            ),
            FinalizationConflict,
        )
    finally:
        case.close()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("target_path_digest", "f" * 64),
        ("probe_artifact_id", "foreign-probe"),
        ("hash_artifact_id", "99999999-9999-4999-8999-999999999999"),
        ("assignment_artifact_id", "foreign-assignment"),
    ],
)
def test_recovery_never_retries_an_uncommitted_foreign_intent(field, replacement) -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == "after_intent":
            token.cancel()

    case, _, ports, request = _context(checkpoint=cancel)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
        intent_path = (
            request.project.project_directory.canonical_dos_path
            + rf"\runs\{RUN_ID}\finalization-intent.json"
        )
        node = case.port.nodes[case.port._key(intent_path)]
        intent = parse_intent_bytes(bytes(node.data))
        values = intent.model_dump()
        values["source_identity"] = intent.source_identity
        values["bundle_binding"] = intent.bundle_binding
        values[field] = replacement
        provisional = type(intent).model_construct(**values)
        values["finalization_key"] = finalization_key(provisional)
        node.data[:] = canonical_bytes(type(intent).model_validate(values))
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        result = recover(
            clean,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            CancellationToken(),
        )
        assert isinstance(result, FinalizationConflict)
        assert result.error.phase == "recovery.retry_intent_binding"
        assert case.port._key(r"C:\Sources\source.obs-events.json") not in case.port.nodes
    finally:
        case.close()


def test_recovery_cancellation_after_inspection_prevents_uncommitted_retry() -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == "recovery.inspect_target":
            token.cancel()

    case, _, ports, request = _context(checkpoint=cancel)
    try:
        result = recover(
            ports,
            _recovery(request, r"C:\Sources\source.obs-events.json"),
            token,
        )
        assert isinstance(result, FinalizationCancelled)
        assert case.port._key(r"C:\Sources\source.obs-events.json") not in case.port.nodes
    finally:
        case.close()


def test_recovery_evidence_publish_and_read_failures_are_secondary(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.recovery as module
    from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
    from matrix_auto_cutter.phase2.errors import failure as detail
    from matrix_auto_cutter.phase2.finalizer.errors import (
        FinalizerErrorCategory,
        FinalizerErrorCode,
        failure,
    )
    from matrix_auto_cutter.phase2.finalizer.persistence import (
        ArtifactStoreFailed,
        StateStoreFailed,
    )
    from matrix_auto_cutter.phase2.pathing import SecureReadFailed

    case, _, ports, request = _context()
    committed = finalize(ports, request, CancellationToken())
    assert isinstance(committed, Finalized)
    del case.port.nodes[case.port._key(committed.receipt.canonical_path)]
    del case.port.nodes[case.port._key(committed.state.canonical_path)]
    original_store = module.store_immutable

    monkeypatch.setattr(
        module,
        "store_immutable",
        lambda *args, **kwargs: ArtifactStoreFailed(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "receipt",
                "failed",
            )
        ),
    )
    monkeypatch.setattr(
        module,
        "store_state",
        lambda *args, **kwargs: StateStoreFailed(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "state",
                "failed",
            )
        ),
    )
    try:
        recovered = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized)
        assert recovered.evidence_status == "committed_evidence_incomplete"
        assert len(recovered.diagnostics) >= 2
    finally:
        monkeypatch.setattr(module, "store_immutable", original_store)

    original_read = module.secure_read_file

    def fail_evidence_read(port, path, maximum):
        if path.canonical_dos_path.endswith("finalizer-state.json") or "receipts" in (
            path.canonical_dos_path
        ):
            return SecureReadFailed(
                detail(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "read", "failed", win32_code=5)
            )
        return original_read(port, path, maximum)

    monkeypatch.setattr(module, "secure_read_file", fail_evidence_read)
    try:
        recovered = recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized)
        assert any(item.phase == "recovery.receipt_read" for item in recovered.diagnostics)
        assert any(item.phase == "recovery.state_read" for item in recovered.diagnostics)
    finally:
        case.close()


def test_recovery_artifact_paths_digest_and_intent_binding(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.recovery as module
    from matrix_auto_cutter.phase2.artifacts import canonical_bytes
    from matrix_auto_cutter.phase2.finalizer.errors import (
        FinalizerErrorCategory,
        FinalizerErrorCode,
        failure,
    )
    from matrix_auto_cutter.phase2.finalizer.models import finalization_key, parse_intent_bytes

    case, _, ports, request = _context()
    committed = finalize(ports, request, CancellationToken())
    assert isinstance(committed, Finalized)
    original_path = module._artifact_path

    def fail_receipt_state(*args, **kwargs):
        if args[3] in {"receipt", "state"}:
            return failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "artifact.path",
                "failed",
            )
        return original_path(*args, **kwargs)

    monkeypatch.setattr(module, "_artifact_path", fail_receipt_state)
    recovered = recover(
        ports,
        _recovery(request, committed.sidecar.canonical_path),
        CancellationToken(),
    )
    assert isinstance(recovered, Finalized)
    assert len(recovered.diagnostics) >= 2
    monkeypatch.setattr(module, "_artifact_path", original_path)

    def fail_intent(*args, **kwargs):
        if args[3] == "intent":
            return failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "intent.path",
                "failed",
            )
        return original_path(*args, **kwargs)

    monkeypatch.setattr(module, "_artifact_path", fail_intent)
    conflict = recover(
        ports,
        _recovery(request, committed.sidecar.canonical_path),
        CancellationToken(),
    )
    assert isinstance(conflict, FinalizationConflict)
    assert conflict.committed_sidecar == committed.sidecar
    monkeypatch.setattr(module, "_artifact_path", original_path)

    monkeypatch.setattr(
        module,
        "_target_digest",
        lambda *args: FinalizationRejected(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "digest",
                "failed",
            )
        ),
    )
    assert isinstance(
        recover(
            ports,
            _recovery(request, committed.sidecar.canonical_path),
            CancellationToken(),
        ),
        FinalizationRejected,
    )
    monkeypatch.setattr(
        module,
        "_target_digest",
        __import__(
            "matrix_auto_cutter.phase2.finalizer.orchestrator", fromlist=["_target_digest"]
        )._target_digest,
    )

    intent_node = case.port.nodes[case.port._key(committed.intent.canonical_path)]
    intent = parse_intent_bytes(bytes(intent_node.data))
    values = intent.model_dump()
    values["source_identity"] = intent.source_identity
    values["bundle_binding"] = intent.bundle_binding
    values["target_path_digest"] = "f" * 64
    provisional = type(intent).model_construct(**values)
    values["finalization_key"] = finalization_key(provisional)
    intent_node.data[:] = canonical_bytes(type(intent).model_validate(values))
    try:
        assert isinstance(
            recover(
                ports,
                _recovery(request, committed.sidecar.canonical_path),
                CancellationToken(),
            ),
            FinalizationConflict,
        )
    finally:
        case.close()


def test_recovery_intent_io_state_publish_and_cancel_before_state(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.recovery as module
    from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
    from matrix_auto_cutter.phase2.errors import failure as detail
    from matrix_auto_cutter.phase2.finalizer.errors import (
        FinalizerErrorCategory,
        FinalizerErrorCode,
        failure,
    )
    from matrix_auto_cutter.phase2.finalizer.persistence import StateStoreFailed
    from matrix_auto_cutter.phase2.pathing import SecureReadFailed

    case, _, ports, request = _context()
    committed = finalize(ports, request, CancellationToken())
    assert isinstance(committed, Finalized)
    original_read = module.secure_read_file

    def fail_intent_read(port, path, maximum):
        if path.canonical_dos_path.endswith("finalization-intent.json"):
            return SecureReadFailed(
                detail(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "read", "failed", win32_code=5)
            )
        return original_read(port, path, maximum)

    monkeypatch.setattr(module, "secure_read_file", fail_intent_read)
    conflict = recover(
        ports,
        _recovery(request, committed.sidecar.canonical_path),
        CancellationToken(),
    )
    assert isinstance(conflict, FinalizationConflict)
    assert conflict.committed_sidecar == committed.sidecar
    monkeypatch.setattr(module, "secure_read_file", original_read)

    monkeypatch.setattr(
        module,
        "store_state",
        lambda *args, **kwargs: StateStoreFailed(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.IO,
                "state",
                "failed",
            )
        ),
    )
    recovered = recover(
        ports,
        _recovery(request, committed.sidecar.canonical_path),
        CancellationToken(),
    )
    assert isinstance(recovered, Finalized)
    assert any(item.phase == "recovery.state_publish" for item in recovered.diagnostics)

    token = CancellationToken()
    del case.port.nodes[case.port._key(committed.state.canonical_path)]

    def cancel_before_state(name: str) -> None:
        if name == "recovery.before_state":
            token.cancel()

    cancel_ports = FinalizerPorts(case.port, ports.now, uuid4, cancel_before_state)
    recovered = recover(
        cancel_ports,
        _recovery(request, committed.sidecar.canonical_path),
        token,
    )
    assert isinstance(recovered, Finalized)
    assert recovered.state is None
    assert recovered.evidence_status == "committed_evidence_incomplete"
    case.close()
