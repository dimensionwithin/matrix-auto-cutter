from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread
from uuid import UUID

import pytest
from tests.phase2.close_gate.conftest import FakeWaitClock, gate

import matrix_auto_cutter.phase2.close_gate.gate as gate_module
import matrix_auto_cutter.phase2.close_gate.lease as lease_module
import matrix_auto_cutter.phase2.close_gate.ownership as ownership_module
import matrix_auto_cutter.phase2.close_gate.snapshot as snapshot_module
import matrix_auto_cutter.phase2.close_gate.win32_native as native_module
from matrix_auto_cutter.phase2.artifacts import UnavailableIdentity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateBusy,
    CloseGateCancelled,
    CloseGateClosed,
    CloseGateDeletePending,
    CloseGateDisappeared,
    CloseGateErrorCategory,
    CloseGateErrorCode,
    CloseGateFailure,
    CloseGateInaccessible,
    CloseGateUnknownWin32Error,
    CloseGateUnsupported,
    RecheckCancelled,
    RecheckClosed,
    RecheckUnknownWin32Error,
    RecheckUnsupported,
)
from matrix_auto_cutter.phase2.close_gate.classification import classify_phase2
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, failure
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
)
from matrix_auto_cutter.phase2.pathing import PathRejected
from matrix_auto_cutter.phase2.snapshots import ComparisonFailed, NotComparable
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_ACCESS_DENIED,
    OwnedHandle,
    Win32Err,
    Win32Failure,
    Win32Ok,
)


def _detail(code: ErrorCode, native: int | None = None):
    return failure(code, ErrorCategory.IO, "phase2", "detail", win32_code=native)


def test_phase2_and_lock_classification_edges() -> None:
    assert isinstance(classify_phase2(_detail(ErrorCode.PROJECT_LOCK_BUSY)), CloseGateBusy)
    assert isinstance(
        classify_phase2(_detail(ErrorCode.SNAPSHOT_NOT_FOUND)),
        CloseGateDisappeared,
    )
    assert isinstance(classify_phase2(_detail(ErrorCode.CANCELLED)), CloseGateCancelled)
    assert isinstance(classify_phase2(_detail(ErrorCode.LOCK_IO)), CloseGateUnknownWin32Error)
    assert isinstance(
        classify_phase2(_detail(ErrorCode.PATH_OS_ERROR, 303), source_operation=True),
        CloseGateDeletePending,
    )
    assert isinstance(
        classify_phase2(_detail(ErrorCode.PATH_OS_ERROR, 32), source_operation=True),
        CloseGateBusy,
    )
    assert isinstance(
        classify_phase2(_detail(ErrorCode.PATH_OS_ERROR, 32)), CloseGateUnknownWin32Error
    )
    assert isinstance(
        gate_module._lock_failure(LockBusy(_detail(ErrorCode.PATH_LOCK_BUSY))), CloseGateBusy
    )
    assert isinstance(
        gate_module._lock_failure(LockTimedOut(_detail(ErrorCode.LOCK_TIMEOUT))),
        CloseGateBusy,
    )
    assert isinstance(
        gate_module._lock_failure(LockAccessDenied(_detail(ErrorCode.LOCK_ACCESS_DENIED))),
        CloseGateInaccessible,
    )
    assert isinstance(
        gate_module._lock_failure(LockIoError(_detail(ErrorCode.LOCK_IO))),
        CloseGateUnknownWin32Error,
    )
    assert isinstance(
        gate_module._lock_failure(LockCancelled(_detail(ErrorCode.CANCELLED))),
        CloseGateCancelled,
    )
    with pytest.raises(TypeError):
        gate_module._lock_failure(object())


def test_measurement_and_wait_helper_edges(monkeypatch, close_port, source_path) -> None:
    path_failure = snapshot_module.SnapshotMeasurementFailed(
        "measure",
        "path",
        path_error=_detail(ErrorCode.PATH_REPARSE),
    )
    assert isinstance(gate_module._measurement_failure(path_failure), CloseGateUnsupported)
    cause = RuntimeError("construction")
    cause_failure = snapshot_module.SnapshotMeasurementFailed(
        "measure", "construction", cause=cause
    )
    assert isinstance(gate_module._measurement_failure(cause_failure), CloseGateUnknownWin32Error)

    token = CancellationToken()
    token.cancel()
    assert isinstance(
        gate_module._wait_interval(FakeWaitClock(), token, "wait"), CloseGateCancelled
    )

    class RaisingClock(FakeWaitClock):
        def monotonic(self):
            raise RuntimeError("clock")

    assert isinstance(
        gate_module._wait_interval(RaisingClock(), CancellationToken(), "wait"),
        CloseGateUnknownWin32Error,
    )
    nonfinite = FakeWaitClock()
    nonfinite.now = float("nan")
    assert isinstance(
        gate_module._wait_interval(nonfinite, CancellationToken(), "wait"),
        CloseGateUnknownWin32Error,
    )

    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    unavailable = replace(result.lease.s0, file_id=UnavailableIdentity())
    assert isinstance(
        gate_module._compare_window(unavailable, unavailable, unavailable),
        CloseGateUnsupported,
    )
    tampered = replace(result.lease.s0)
    object.__setattr__(tampered, "snapshot_key", "f" * 64)
    assert isinstance(
        gate_module._compare_window(tampered, tampered, tampered),
        CloseGateUnknownWin32Error,
    )
    result.lease.close()


class NthCheckToken(CancellationToken):
    def __init__(self, target: int) -> None:
        super().__init__()
        self.target = target
        self.checks = 0

    @property
    def is_cancelled(self) -> bool:
        self.checks += 1
        if self.checks == self.target:
            self.cancel()
        return super().is_cancelled


@pytest.mark.parametrize("target", range(1, 32))
def test_every_gate_cancellation_checkpoint_cleans_up(close_port, source_path, target: int) -> None:
    token = NthCheckToken(target)
    result = gate(close_port, source_path, token=token)
    if isinstance(result, CloseGateClosed):
        result.lease.close()
    else:
        assert isinstance(result, CloseGateCancelled)
    assert not close_port.handles


def test_project_path_and_source_lock_failures(monkeypatch, close_port, source_path) -> None:
    original_project = gate_module.acquire_project_lock
    monkeypatch.setattr(
        gate_module,
        "acquire_project_lock",
        lambda *args, **kwargs: LockBusy(_detail(ErrorCode.PROJECT_LOCK_BUSY, 32)),
    )
    assert isinstance(gate(close_port, source_path), CloseGateBusy)
    monkeypatch.setattr(gate_module, "acquire_project_lock", original_project)

    original = gate_module.acquire_path_lock
    monkeypatch.setattr(
        gate_module,
        "acquire_path_lock",
        lambda *args, **kwargs: LockAccessDenied(_detail(ErrorCode.LOCK_ACCESS_DENIED)),
    )
    assert isinstance(gate(close_port, source_path), CloseGateInaccessible)
    monkeypatch.setattr(gate_module, "acquire_path_lock", original)

    close_port.delete_pending_error = Win32Failure(777, "query", "failed")
    assert isinstance(gate(close_port, source_path), CloseGateUnknownWin32Error)


def test_unsupported_identity_scheme_and_bad_lease_id(monkeypatch, close_port, source_path) -> None:
    original = gate_module.measure_snapshot

    def wrong_scheme(*args, **kwargs):
        measured = original(*args, **kwargs)
        if hasattr(measured, "snapshot"):
            object.__setattr__(
                measured.snapshot,
                "volume_id",
                measured.snapshot.volume_id.model_copy(update={"scheme": "other"}),
            )
        return measured

    monkeypatch.setattr(gate_module, "measure_snapshot", wrong_scheme)
    assert isinstance(gate(close_port, source_path), CloseGateUnsupported)
    monkeypatch.setattr(gate_module, "measure_snapshot", original)
    with pytest.raises(ValueError):
        gate_module.run_close_gate(
            close_port,
            "550e8400-e29b-41d4-a716-446655440000",
            source_path,
            CancellationToken(),
            wait_clock=FakeWaitClock(),
            lease_id_factory=lambda: UUID(int=0),
        )
    assert not close_port.handles


def test_source_ownership_key_root_cancel_and_cleanup_edges(close_port) -> None:
    with pytest.raises(ValueError):
        ownership_module.source_ownership_key("1", "2")
    with pytest.raises(ValueError):
        ownership_module.source_ownership_key("z" * 16, "f" * 32)
    invalid = ownership_module.acquire_source_ownership(
        close_port, "z" * 16, "f" * 32, CancellationToken()
    )
    assert isinstance(invalid, ownership_module.SourceOwnershipRejected)
    token = CancellationToken()
    token.cancel()
    assert isinstance(
        ownership_module.acquire_source_ownership(close_port, "0" * 16, "f" * 32, token),
        ownership_module.SourceOwnershipRejected,
    )
    close_port.failures["SHGetKnownFolderPath"] = [ERROR_ACCESS_DENIED]
    denied = ownership_module.acquire_source_ownership(
        close_port, "0" * 16, "f" * 32, CancellationToken()
    )
    assert isinstance(denied, ownership_module.SourceOwnershipRejected)
    assert isinstance(denied.result, CloseGateInaccessible)
    close_port.local_path = r"\\server\share"
    bad_root = ownership_module.acquire_source_ownership(
        close_port, "0" * 16, "f" * 32, CancellationToken()
    )
    assert isinstance(bad_root, ownership_module.SourceOwnershipRejected)


def test_source_ownership_target_validation_failure(monkeypatch, close_port) -> None:
    original = ownership_module.validate_path
    calls = 0

    def reject_target(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return PathRejected(_detail(ErrorCode.PATH_UNC))
        return original(*args, **kwargs)

    monkeypatch.setattr(ownership_module, "validate_path", reject_target)
    result = ownership_module.acquire_source_ownership(
        close_port, "0" * 16, "f" * 32, CancellationToken()
    )
    assert isinstance(result, ownership_module.SourceOwnershipRejected)


class ToggleAfterOpenToken(CancellationToken):
    def __init__(self) -> None:
        super().__init__()
        self.checks = 0

    @property
    def is_cancelled(self) -> bool:
        self.checks += 1
        if self.checks == 5:
            self.cancel()
        return super().is_cancelled


def test_source_ownership_cancel_after_open_preserves_close_failure(close_port) -> None:
    token = ToggleAfterOpenToken()
    original = close_port.open_file

    def close_failing_open(*args, **kwargs):
        opened = original(*args, **kwargs)
        if not isinstance(opened, Win32Err) and "\\sources\\" in args[0].casefold():
            return Win32Ok(
                OwnedHandle(
                    opened.value.value,
                    lambda raw: Win32Err(Win32Failure(919, "CloseHandle", str(raw))),
                )
            )
        return opened

    close_port.open_file = close_failing_open
    result = ownership_module.acquire_source_ownership(close_port, "0" * 16, "f" * 32, token)
    assert isinstance(result, ownership_module.SourceOwnershipRejected)
    assert result.cleanup_diagnostics[0].win32_code == 919


def test_snapshot_measurement_defensive_edges(monkeypatch, close_port, source_path) -> None:
    opened = close_port.open_file(source_path.long_path, 0x80000000, 1, 3, 0x00200000)
    assert not isinstance(opened, Win32Err)
    handle = opened.value
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    node.attributes |= 0x400
    measured = snapshot_module.measure_snapshot(close_port, source_path, handle, "measure")
    assert isinstance(measured, snapshot_module.SnapshotMeasurementFailed)
    node.attributes &= ~0x400

    original_query = close_port.query_file_info

    def invalid_info(_handle):
        result = original_query(_handle)
        assert isinstance(result, Win32Ok)
        return Win32Ok(replace(result.value, size_bytes=-1, creation_time_100ns=None))

    close_port.query_file_info = invalid_info
    measured = snapshot_module.measure_snapshot(close_port, source_path, handle, "measure")
    assert isinstance(measured, snapshot_module.SnapshotMeasurementFailed)
    close_port.query_file_info = original_query
    assert isinstance(snapshot_module._optional_time(None), UnavailableIdentity)

    class RaisingSnapshot:
        def __init__(self, *args, **kwargs):
            del args, kwargs
            raise ValueError("snapshot")

    monkeypatch.setattr(snapshot_module, "FileSnapshot", RaisingSnapshot)
    measured = snapshot_module.measure_snapshot(close_port, source_path, handle, "measure")
    assert isinstance(measured, snapshot_module.SnapshotMeasurementFailed)
    handle.close()


def test_native_delete_pending_failure_edge(monkeypatch) -> None:
    port = object.__new__(native_module.NativeCloseGateWin32Port)
    monkeypatch.setattr(port, "_query", lambda *args: False)
    monkeypatch.setattr(
        native_module,
        "_failure",
        lambda operation: Win32Err(Win32Failure(888, operation, "failed")),
    )
    handle = OwnedHandle(1, lambda raw: Win32Ok(None))
    result = port.query_delete_pending(handle)
    assert isinstance(result, Win32Err)
    handle.close()


class DummyLease:
    def __init__(self, *, error=None, raises: BaseException | None = None) -> None:
        self.held = True
        self.error = error
        self.raises = raises

    def release(self):
        if self.raises is not None:
            raise self.raises
        self.held = False
        return self.error


def _owned_handle(code: int | None = None, raises: BaseException | None = None) -> OwnedHandle:
    def close(raw):
        if raises is not None:
            raise raises
        if code is not None:
            return Win32Err(Win32Failure(code, "CloseHandle", str(raw)))
        return Win32Ok(None)

    return OwnedHandle(123 + (code or 0), close)


def test_resource_cleanup_all_secondary_paths() -> None:
    detail = _detail(ErrorCode.LOCK_IO, 700)
    resources = lease_module._OwnedGateResources(
        project_lock=DummyLease(error=detail),  # type: ignore[arg-type]
        path_lock=DummyLease(raises=RuntimeError("path")),  # type: ignore[arg-type]
        source_handle=_owned_handle(702),
        source_ownership=ownership_module.SourceOwnership(
            "key",
            object(),
            _owned_handle(701),  # type: ignore[arg-type]
        ),
    )
    diagnostics = resources.close()
    assert {item.phase for item in diagnostics} == {
        "close_source_lock",
        "close_source_handle",
        "release_path_lock",
        "release_project_lock",
    }
    assert resources.close() == ()

    path_error = lease_module._OwnedGateResources(
        path_lock=DummyLease(error=detail),  # type: ignore[arg-type]
    )
    assert path_error.close()[0].phase == "release_path_lock"

    exceptional = lease_module._OwnedGateResources(
        project_lock=DummyLease(raises=RuntimeError("project")),  # type: ignore[arg-type]
        source_handle=_owned_handle(raises=KeyboardInterrupt("source")),
        source_ownership=ownership_module.SourceOwnership(
            "key",
            object(),
            _owned_handle(raises=KeyboardInterrupt("lock")),  # type: ignore[arg-type]
        ),
    )
    assert len(exceptional.close()) == 3


def test_lease_private_authority_and_recheck_edges(monkeypatch, close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    lease = result.lease
    record = lease_module._LEASE_AUTHORITY._record(lease)
    assert record is not None

    error = CloseGateFailure(
        CloseGateErrorCode.WIN32_UNKNOWN,
        CloseGateErrorCategory.IO,
        "recheck",
        "failure",
    )
    assert isinstance(
        lease_module._recheck_failure(CloseGateDeletePending(error)),
        lease_module.RecheckDeletePending,
    )
    assert isinstance(lease_module._recheck_failure(CloseGateCancelled(error)), RecheckCancelled)

    class OtherFailure:
        pass

    other = OtherFailure()
    other.error = error
    assert isinstance(lease_module._recheck_failure(other), RecheckUnknownWin32Error)

    record.state = "closing"
    assert isinstance(lease.recheck(), RecheckClosed)
    record.state = "open"

    close_port.delete_pending_error = Win32Failure(777, "query", "failed")
    assert isinstance(lease.recheck(), RecheckUnknownWin32Error)

    token_after_pending = CancellationToken()
    original_pending = close_port.query_delete_pending

    def cancel_after_pending(handle):
        result = original_pending(handle)
        token_after_pending.cancel()
        return result

    close_port.query_delete_pending = cancel_after_pending
    assert isinstance(lease.recheck(token_after_pending), RecheckCancelled)
    close_port.query_delete_pending = original_pending

    close_port.snapshot_errors[close_port.snapshot_query_count + 1] = Win32Failure(
        778, "query", "snapshot"
    )
    assert isinstance(lease.recheck(), RecheckUnknownWin32Error)
    close_port.snapshot_errors.clear()

    original_measure = lease_module.measure_snapshot
    monkeypatch.setattr(
        lease_module,
        "measure_snapshot",
        lambda *args, **kwargs: snapshot_module.SnapshotMeasurementFailed(
            "recheck", "construction", cause=RuntimeError("construction")
        ),
    )
    assert isinstance(lease.recheck(), RecheckUnknownWin32Error)
    monkeypatch.setattr(lease_module, "measure_snapshot", original_measure)

    token_after_snapshot = CancellationToken()

    def cancel_after_snapshot(*args, **kwargs):
        measured = original_measure(*args, **kwargs)
        token_after_snapshot.cancel()
        return measured

    monkeypatch.setattr(lease_module, "measure_snapshot", cancel_after_snapshot)
    assert isinstance(lease.recheck(token_after_snapshot), RecheckCancelled)
    monkeypatch.setattr(lease_module, "measure_snapshot", original_measure)

    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    node.attributes |= 0x400
    assert isinstance(lease.recheck(), RecheckUnsupported)
    node.attributes &= ~0x400

    monkeypatch.setattr(lease_module, "compare_snapshots", lambda *args: NotComparable())
    assert isinstance(lease.recheck(), RecheckUnsupported)
    monkeypatch.setattr(lease_module, "compare_snapshots", lambda *args: ComparisonFailed("bad"))
    assert isinstance(lease.recheck(), RecheckUnknownWin32Error)

    class CommitCancel(CancellationToken):
        def begin_irreversible_commit(self):
            self.cancel()
            return None

    monkeypatch.setattr(
        lease_module,
        "compare_snapshots",
        lambda *args: lease_module.SameInstanceUnchanged(),
    )
    assert isinstance(lease.recheck(CommitCancel()), RecheckCancelled)

    record.resources.source_handle = None
    assert isinstance(lease.recheck(), RecheckClosed)
    lease.close()

    forged = object.__new__(lease_module.CloseGateLease)
    assert lease_module._LEASE_AUTHORITY._record(forged) is None
    assert isinstance(
        lease_module._LEASE_AUTHORITY.recheck(forged, CancellationToken()), RecheckClosed
    )
    with pytest.raises(TypeError):
        lease_module._LEASE_AUTHORITY.close(forged)


def test_lease_initialization_context_and_issue_guards(close_port, source_path) -> None:
    forged = object.__new__(lease_module.CloseGateLease)
    with pytest.raises(TypeError):
        forged._initialize(
            object(),
            source_path,
            "0" * 16,
            "f" * 32,
            "file_id_128",
            object(),
            object(),
            object(),
            UUID("2e157a84-2e31-49d9-b64e-494c24f8f612"),
            _seal=object(),
        )
    with pytest.raises(AttributeError):
        del forged._token

    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    unavailable = replace(result.lease.s0, file_id=UnavailableIdentity())
    with pytest.raises(ValueError):
        lease_module._LeaseAuthorityRegistry().issue(
            close_port,
            lease_module._OwnedGateResources(),
            source_path,
            unavailable,
            unavailable,
            unavailable,
        )
    with result.lease as entered:
        assert entered is result.lease
    with pytest.raises(RuntimeError):
        result.lease.__enter__()


def test_baseexception_attaches_cleanup_note(close_port, source_path) -> None:
    close_port.source_handle_close_error = Win32Failure(990, "CloseHandle", "cleanup")

    class InterruptingClock(FakeWaitClock):
        def wait(self, cancellation, seconds):
            del cancellation, seconds
            raise KeyboardInterrupt("primary")

    with pytest.raises(KeyboardInterrupt) as captured:
        gate(close_port, source_path, clock=InterruptingClock())
    assert any("cleanup" in note for note in captured.value.__notes__)


def test_concurrent_idempotent_close_waiters(monkeypatch, close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    record = lease_module._LEASE_AUTHORITY._record(result.lease)
    assert record is not None
    entered = Event()
    proceed = Event()
    original_close = lease_module._OwnedGateResources.close

    def blocking_close(resources):
        entered.set()
        assert proceed.wait(2)
        return original_close(resources)

    monkeypatch.setattr(lease_module._OwnedGateResources, "close", blocking_close)
    outputs = []
    first = Thread(target=lambda: outputs.append(result.lease.close()))
    second = Thread(target=lambda: outputs.append(result.lease.close()))
    first.start()
    assert entered.wait(2)
    second.start()
    proceed.set()
    first.join(2)
    second.join(2)
    assert outputs == [(), ()]
