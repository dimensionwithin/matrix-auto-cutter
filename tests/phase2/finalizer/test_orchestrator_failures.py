from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from tests.phase2.finalizer.conftest import (
    RUN_ID,
    SESSION_ID,
    add_validated_file,
    journal_bytes,
)
from tests.phase2.source_confirmation.conftest import make_case

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
from matrix_auto_cutter.phase2.errors import failure as phase2_failure
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationCancelled,
    FinalizationConflict,
    FinalizationRejected,
    FinalizationRequest,
    Finalized,
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    finalize,
)
from matrix_auto_cutter.phase2.finalizer.errors import failure
from matrix_auto_cutter.phase2.finalizer.orchestrator import (
    _journal_matches_source,
    _lock_failure,
    _new_uuid,
    _target_digest,
)
from matrix_auto_cutter.phase2.finalizer.persistence import ArtifactStoreFailed, StateStoreFailed
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.source_confirmation import SourceConfirmed, confirm_source


def _setup(*, checkpoint=lambda name: None):
    case = make_case()
    confirmed = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(confirmed, SourceConfirmed)
    path = add_validated_file(
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
        JournalInputPaths(path),
        confirmed.confirmed_source,
        SESSION_ID,
    )
    return case, confirmed, ports, request


def test_public_input_loader_and_cancellation_failures() -> None:
    case, _, ports, request = _setup()
    try:
        invalid = finalize(
            ports,
            FinalizationRequest(
                request.project,
                "not-a-uuid",
                request.input_profile,
                request.inputs,
                request.confirmed_source,
            ),
            CancellationToken(),
        )
        assert isinstance(invalid, FinalizationRejected)
        token = CancellationToken()
        token.cancel()
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)

        journal_node = case.port.nodes[case.port._key(request.inputs.journal.canonical_dos_path)]
        journal_node.data[:] = b"bad\n"
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()


@pytest.mark.parametrize(
    "checkpoint",
    ["before_intent", "after_intent", "after_sidecar_construction"],
)
def test_cancellation_checkpoints_before_commit(checkpoint) -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == checkpoint:
            token.cancel()

    case, _, ports, request = _setup(checkpoint=cancel)
    try:
        result = finalize(ports, request, token)
        assert isinstance(result, FinalizationCancelled)
        assert case.port._key(r"C:\Sources\source.obs-events.json") not in case.port.nodes
    finally:
        case.close()


@pytest.mark.parametrize("checkpoint", ["after_commit", "before_receipt", "before_final_state"])
def test_late_cancel_never_revokes_committed_sidecar(checkpoint) -> None:
    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == checkpoint:
            token.cancel()

    case, _, ports, request = _setup(checkpoint=cancel)
    try:
        result = finalize(ports, request, token)
        assert isinstance(result, Finalized)
        assert case.port._key(result.sidecar.canonical_path) in case.port.nodes
    finally:
        case.close()


def test_closed_confirmed_source_is_not_disk_authority() -> None:
    case, confirmed, ports, request = _setup()
    case.request.lease.close()
    result = finalize(ports, request, CancellationToken())
    assert isinstance(result, FinalizationRejected)
    assert result.error.code is FinalizerErrorCode.SOURCE_UNAUTHORIZED
    assert not confirmed.confirmed_source.authorized


def test_journal_source_mismatch_and_ordinal_fail_closed() -> None:
    case, _, ports, request = _setup()
    try:
        records = journal_bytes().replace(
            b"C:\\\\Sources\\\\source.mp4", b"C:\\\\Other\\\\source.mp4"
        )
        if records == journal_bytes():
            records = journal_bytes().replace(b"C:\\Sources\\source.mp4", b"C:\\Other\\source.mp4")
        case.port.nodes[case.port._key(request.inputs.journal.canonical_dos_path)].data[:] = records
        result = finalize(ports, request, CancellationToken())
        assert isinstance(result, FinalizationRejected)
        assert result.error.code is FinalizerErrorCode.JOURNAL_SOURCE_MISMATCH
    finally:
        case.close()


def test_target_lock_and_move_failures_are_structured() -> None:
    case, _, ports, request = _setup()
    try:
        target = validate_path(
            case.port,
            r"C:\Sources\source.obs-events.json",
            PathRole.EXTERNAL_TARGET_CREATE_ONLY,
        )
        assert isinstance(target, PathValidated)
        from matrix_auto_cutter.phase2.locks import LockAcquired, acquire_target_lock

        held = acquire_target_lock(
            case.port,
            target.path,
            CancellationToken(),
            run_id=uuid4(),
        )
        assert isinstance(held, LockAcquired)
        try:
            busy = finalize(ports, request, CancellationToken())
            assert isinstance(busy, FinalizationRejected)
            assert busy.error.code is FinalizerErrorCode.FINALIZER_CONCURRENT
        finally:
            held.lease.release()

        def fail_sidecar(name: str) -> None:
            if name == "before_temp":
                case.port.failures["MoveFileExW"] = [5]

        fail_ports = FinalizerPorts(
            case.port,
            ports.now,
            uuid4,
            fail_sidecar,
        )
        failed = finalize(fail_ports, request, CancellationToken())
        assert isinstance(failed, FinalizationConflict)
        assert failed.error.code is FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
    finally:
        case.close()


def test_existing_foreign_target_and_corrupt_intent_remain_unchanged() -> None:
    case, _, ports, request = _setup()
    try:
        target = r"C:\Sources\source.obs-events.json"
        case.port.add_file(target, b"foreign\n")
        before = bytes(case.port.nodes[case.port._key(target)].data)
        result = finalize(ports, request, CancellationToken())
        assert isinstance(result, FinalizationConflict)
        assert bytes(case.port.nodes[case.port._key(target)].data) == before

        del case.port.nodes[case.port._key(target)]
        intent_path = (
            request.project.project_directory.canonical_dos_path
            + rf"\runs\{RUN_ID}\finalization-intent.json"
        )
        case.port.make_tree(str(intent_path.rsplit("\\", 1)[0]))
        case.port.add_file(intent_path, b"bad\n")
        result = finalize(ports, request, CancellationToken())
        assert isinstance(result, FinalizationConflict)
    finally:
        case.close()


def test_downstream_receipt_and_state_failure_preserve_commit(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    case, _, ports, request = _setup()
    original_store_immutable = module.store_immutable
    original_persist_state = module._persist_state

    def store(*args, **kwargs):
        if kwargs.get("artifact") == "finalization-receipt":
            return ArtifactStoreFailed(
                failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.INTEGRITY,
                    "receipt",
                    "injected",
                )
            )
        return original_store_immutable(*args, **kwargs)

    calls = 0

    def state(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls >= 5:
            return StateStoreFailed(
                failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.INTEGRITY,
                    "state",
                    "injected",
                )
            )
        return original_persist_state(*args, **kwargs)

    monkeypatch.setattr(module, "store_immutable", store)
    monkeypatch.setattr(module, "_persist_state", state)
    try:
        result = finalize(ports, request, CancellationToken())
        assert isinstance(result, Finalized)
        assert result.receipt is None
        assert result.diagnostics
    finally:
        case.close()


def test_baseexception_releases_target_and_source_usage() -> None:
    def crash(name: str) -> None:
        if name == "before_temp":
            raise SystemExit("crash")

    case, _, ports, request = _setup(checkpoint=crash)
    try:
        with pytest.raises(SystemExit):
            finalize(ports, request, CancellationToken())
        clean_ports = FinalizerPorts(
            case.port,
            lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
            uuid4,
        )
        assert isinstance(finalize(clean_ports, request, CancellationToken()), Finalized)
    finally:
        case.close()


def test_small_private_helper_branches(fake_port) -> None:
    with pytest.raises(ValueError):
        _new_uuid(lambda: UUID(int=0), "test")
    path = validate_path(
        fake_port,
        r"C:\target.json",
        PathRole.EXTERNAL_TARGET_CREATE_ONLY,
    )
    assert isinstance(path, PathValidated)
    fake_port.failures["LCMapStringEx"] = [5]
    assert isinstance(_target_digest(fake_port, path.path), FinalizationRejected)

    detail = phase2_failure(
        ErrorCode.PATH_LOCK_BUSY,
        ErrorCategory.CONCURRENCY,
        "lock",
        "busy",
    )
    for value in (LockBusy(detail), LockTimedOut(detail)):
        assert isinstance(_lock_failure(value), FinalizationRejected)
    assert isinstance(_lock_failure(LockCancelled(detail)), FinalizationCancelled)
    for value in (LockAccessDenied(detail), LockIoError(detail)):
        assert isinstance(_lock_failure(value), FinalizationRejected)

    journal = type("Journal", (), {"records": ({}, {"last_recording_path": "x"})})()
    assert _journal_matches_source(
        fake_port,
        journal,
        r"C:\source.mp4",
        __import__(
            "matrix_auto_cutter.models", fromlist=["SourceBinding"]
        ).SourceBinding.MANUAL_REMUX,
    )


def test_cancel_after_loader_and_cancelled_unavailable_usage(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    case, _, ports, request = _setup()
    token = CancellationToken()
    original_load = module.load_journal

    def load(*args, **kwargs):
        value = original_load(*args, **kwargs)
        token.cancel()
        return value

    monkeypatch.setattr(module, "load_journal", load)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
    finally:
        case.close()

    case, _, ports, request = _setup()
    token = CancellationToken()

    def unavailable(*args, **kwargs):
        del args, kwargs
        token.cancel()
        return module._ConfirmedSourceUsageUnavailable("cancelled")

    monkeypatch.setattr(module, "load_journal", original_load)
    monkeypatch.setattr(module, "_run_confirmed_source_usage", unavailable)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
    finally:
        case.close()


def test_confirmed_source_usage_rejects_a_different_os_adapter(monkeypatch) -> None:
    from tests.phase2.conftest import FakePort

    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    case, _, ports, request = _setup()
    loaded = module.load_journal(
        case.port,
        request.input_profile,
        request.inputs,
        expected_recording_id=request.expected_recording_id,
    )
    monkeypatch.setattr(module, "load_journal", lambda *args, **kwargs: loaded)
    mismatched = FinalizerPorts(FakePort(), ports.now, uuid4)
    try:
        result = finalize(mismatched, request, CancellationToken())
        assert isinstance(result, FinalizationRejected)
        assert result.error.code is FinalizerErrorCode.SOURCE_UNAUTHORIZED
    finally:
        case.close()


def test_retry_temp_cleanup_failure_is_primary_and_never_commits(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    token = CancellationToken()

    def cancel(name: str) -> None:
        if name == "after_intent":
            token.cancel()

    case, _, ports, request = _setup(checkpoint=cancel)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
        temp = rf"C:\Sources\.source.obs-events.json.tmp.{RUN_ID}"
        case.port.add_file(temp, b"partial")
        detail = phase2_failure(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            ErrorCategory.IO,
            "delete_temp",
            "failed",
            win32_code=5,
        )
        monkeypatch.setattr(
            module, "cleanup_external_owned_temp", lambda *args, **kwargs: (detail,)
        )
        clean = FinalizerPorts(case.port, ports.now, uuid4)
        result = finalize(clean, request, CancellationToken())
        assert isinstance(result, FinalizationRejected)
        assert result.error.code is FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
        assert case.port._key(temp) in case.port.nodes
        assert case.port._key(r"C:\Sources\source.obs-events.json") not in case.port.nodes
    finally:
        case.close()


def test_untrusted_project_path_digest_and_artifact_path_failures(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module
    from matrix_auto_cutter.phase2.pathing import PathRejected

    case, _, ports, request = _setup()
    request.project._invalidate_trust()
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()

    case, _, ports, request = _setup()
    monkeypatch.setattr(
        module,
        "derive_external_target",
        lambda *args, **kwargs: PathRejected(
            phase2_failure(
                ErrorCode.PATH_INPUT_FORM,
                ErrorCategory.INPUT,
                "path",
                "rejected",
            )
        ),
    )
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()

    case, _, ports, request = _setup()
    monkeypatch.setattr(
        module,
        "derive_external_target",
        __import__(
            "matrix_auto_cutter.phase2.pathing", fromlist=["derive_external_target"]
        ).derive_external_target,
    )
    monkeypatch.setattr(
        module,
        "_target_digest",
        lambda *args: FinalizationRejected(
            failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.IO,
                "digest",
                "failed",
            )
        ),
    )
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()

    case, _, ports, request = _setup()
    monkeypatch.setattr(module, "_target_digest", _target_digest)
    monkeypatch.setattr(
        module,
        "project_artifact_path",
        lambda *args, **kwargs: failure(
            FinalizerErrorCode.FINALIZER_INTERNAL,
            FinalizerErrorCategory.IO,
            "artifact",
            "failed",
        ),
    )
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()


def test_intent_io_construct_store_and_binding_failures(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    case, _, ports, request = _setup()

    def fail_read(name: str) -> None:
        if name == "before_intent":
            case.port.failures["CreateFileW"] = [5]

    try:
        result = finalize(
            FinalizerPorts(case.port, ports.now, uuid4, fail_read),
            request,
            CancellationToken(),
        )
        assert isinstance(result, FinalizationRejected)
    finally:
        case.close()

    case, _, ports, request = _setup()
    try:
        result = finalize(
            FinalizerPorts(case.port, ports.now, lambda: UUID(int=0)),
            request,
            CancellationToken(),
        )
        assert isinstance(result, FinalizationRejected)
    finally:
        case.close()

    for code, expected in (
        (FinalizerErrorCode.CANCELLED, FinalizationCancelled),
        (FinalizerErrorCode.RECOVERY_CONFLICT, FinalizationConflict),
    ):
        case, _, ports, request = _setup()
        original = module.store_immutable

        def fail_store(*args, _code=code, _original=original, **kwargs):
            if kwargs.get("artifact") == "finalization-intent":
                return ArtifactStoreFailed(
                    failure(_code, FinalizerErrorCategory.INTEGRITY, "intent", "failed")
                )
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, "store_immutable", fail_store)
        try:
            assert isinstance(finalize(ports, request, CancellationToken()), expected)
        finally:
            case.close()
        monkeypatch.setattr(module, "store_immutable", original)

    case, _, ports, request = _setup()
    try:
        first = finalize(ports, request, CancellationToken())
        assert isinstance(first, Finalized)
        del case.port.nodes[case.port._key(first.sidecar.canonical_path)]
        intent_node = case.port.nodes[case.port._key(first.intent.canonical_path)]
        from matrix_auto_cutter.phase2.artifacts import canonical_bytes
        from matrix_auto_cutter.phase2.finalizer.models import finalization_key, parse_intent_bytes

        intent = parse_intent_bytes(bytes(intent_node.data))
        values = intent.model_dump()
        values["source_identity"] = intent.source_identity
        values["bundle_binding"] = intent.bundle_binding
        values["target_path_digest"] = "f" * 64
        provisional = type(intent).model_construct(**values)
        values["finalization_key"] = finalization_key(provisional)
        intent_node.data[:] = canonical_bytes(type(intent).model_validate(values))
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationConflict)
    finally:
        case.close()


def test_construct_publish_and_receipt_path_failure_branches(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.orchestrator as module

    case, _, ports, request = _setup()
    token = CancellationToken()
    original_state = module._persist_state

    def cancel_construct(*args, **kwargs):
        value = original_state(*args, **kwargs)
        machine = args[5]
        if machine.state.value == "constructing_sidecar":
            token.cancel()
        return value

    monkeypatch.setattr(module, "_persist_state", cancel_construct)
    try:
        assert isinstance(finalize(ports, request, token), FinalizationCancelled)
    finally:
        case.close()

    case, _, ports, request = _setup()
    monkeypatch.setattr(module, "_persist_state", original_state)
    monkeypatch.setattr(
        module,
        "build_sidecar",
        lambda *args: failure(
            FinalizerErrorCode.CANCELLED,
            FinalizerErrorCategory.CANCELLED,
            "build",
            "cancelled",
        ),
    )
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationCancelled)
    finally:
        case.close()

    case, _, ports, request = _setup()
    monkeypatch.setattr(
        module,
        "build_sidecar",
        lambda *args: failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "build",
            "failed",
        ),
    )
    try:
        assert isinstance(finalize(ports, request, CancellationToken()), FinalizationRejected)
    finally:
        case.close()

    case, _, ports, request = _setup()
    from matrix_auto_cutter.phase2.finalizer.sidecar_builder import build_sidecar

    monkeypatch.setattr(module, "build_sidecar", build_sidecar)
    original_path = module.project_artifact_path

    def receipt_path(*args, **kwargs):
        if args[2] == ("sidecar", "receipts"):
            return failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.IO,
                "receipt.path",
                "failed",
            )
        return original_path(*args, **kwargs)

    monkeypatch.setattr(module, "project_artifact_path", receipt_path)
    try:
        result = finalize(ports, request, CancellationToken())
        assert isinstance(result, Finalized)
        assert any(item.phase == "receipt.path" for item in result.diagnostics)
    finally:
        case.close()


def test_target_lock_release_failure_is_secondary() -> None:
    case, _, ports, request = _setup()

    def fail_release(name: str) -> None:
        if name == "after_final_state":
            key = next(key for key in case.port.exclusive if "OWNERSHIP\\TARGETS" in key)
            case.port.close_results[key] = [5]

    try:
        result = finalize(
            FinalizerPorts(case.port, ports.now, uuid4, fail_release),
            request,
            CancellationToken(),
        )
        assert isinstance(result, Finalized)
        assert any(item.phase == "target_lock.release" for item in result.diagnostics)
    finally:
        case.port.close_results.clear()
        case.close()
