from __future__ import annotations

from dataclasses import replace

import pytest
from tests.phase2.source_hash.conftest import HASH_RUN_ID, PROJECT_ID, make_hash_case
from tests.phase2.source_hash.test_receipt import _completed, _target

import matrix_auto_cutter.phase2.close_gate.lease as lease_module
import matrix_auto_cutter.phase2.close_gate.win32_native as close_native
import matrix_auto_cutter.phase2.source_hash.hashing as hashing_module
import matrix_auto_cutter.phase2.source_hash.publish as publish_module
import matrix_auto_cutter.phase2.source_hash.receipt as receipt_module
from matrix_auto_cutter.phase2.atomic_project import AtomicPublishIntegrity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import RecheckOk
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, failure
from matrix_auto_cutter.phase2.snapshots import ComparisonFailed
from matrix_auto_cutter.phase2.source_hash import (
    HashCancelled,
    HashCompleted,
    HashDiagnostic,
    HashErrorCategory,
    HashErrorCode,
    HashFailure,
    HashIoError,
    HashReceipt,
    HashReceiptPublishIoError,
    SourceChanged,
    hash_lease_source,
    parse_hash_receipt_bytes,
    publish_hash_receipt,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Failure, Win32Ok


def test_completed_properties_failure_cleanup_and_private_guards() -> None:
    case, completed = _completed()
    assert completed.s0 is case.lease.s0
    assert completed.s4.snapshot_key == completed.s0.snapshot_key
    assert completed.lease_id == completed.validation_epoch == case.lease.lease_id
    assert completed.project_id == PROJECT_ID
    assert completed.hash_run_id == HASH_RUN_ID
    assert completed.hash_algorithm == "sha256"
    assert completed.hash_algorithm_version == "1.0"
    assert completed.block_size_bytes == 2
    diagnostic = HashDiagnostic("cleanup", "secondary")
    primary = HashFailure(HashErrorCode.IO, HashErrorCategory.IO, "hash.read", "primary")
    assert primary.with_cleanup((diagnostic,)).cleanup_diagnostics == (diagnostic,)
    forged = object.__new__(HashCompleted)
    with pytest.raises(TypeError):
        forged._initialize(
            completed.receipt,
            completed.s0,
            completed.s4,
            object(),
            _seal=object(),
        )
    with pytest.raises(AttributeError):
        del completed._token
    case.lease.close()


def test_internal_lease_io_session_defensive_edges() -> None:
    case = make_hash_case(b"abcd")
    token = CancellationToken()
    leaked = lease_module._run_lease_io(case.lease, token, lambda session: session)
    assert isinstance(leaked, lease_module._LeaseIoSession)
    with pytest.raises(RuntimeError):
        leaked.read(1)

    record = lease_module._LEASE_AUTHORITY._record(case.lease)
    assert record is not None
    source = record.resources.source_handle

    def unavailable_inside(session):
        record.resources.source_handle = None
        try:
            with pytest.raises(RuntimeError):
                session.read(1)
        finally:
            record.resources.source_handle = source

    lease_module._run_lease_io(case.lease, CancellationToken(), unavailable_inside)

    def closing_commit(session):
        record.state = "closing"
        try:
            assert not session.commit(CancellationToken())
        finally:
            record.state = "open"

    lease_module._run_lease_io(case.lease, CancellationToken(), closing_commit)
    record.state = "closing"
    rejected = lease_module._run_lease_io(case.lease, CancellationToken(), lambda session: None)
    assert rejected == lease_module._LeaseIoUnavailable("lease_closed")
    record.state = "open"
    record.resources.source_handle = None
    rejected = lease_module._run_lease_io(case.lease, CancellationToken(), lambda session: None)
    assert rejected == lease_module._LeaseIoUnavailable("source_handle_unavailable")
    record.resources.source_handle = source
    case.lease.close()


def test_native_set_file_pointer_failure(monkeypatch) -> None:
    class Kernel:
        @staticmethod
        def SetFilePointerEx(*args):
            return False

    port = object.__new__(close_native.NativeCloseGateWin32Port)
    port._kernel32 = Kernel()
    monkeypatch.setattr(
        close_native,
        "_failure",
        lambda operation: hashing_module.Win32Err(Win32Failure(977, operation, "failed")),
    )
    handle = OwnedHandle(1, lambda raw: Win32Ok(None))
    result = port.set_file_offset(handle, 0)
    assert isinstance(result, hashing_module.Win32Err)
    handle.close()


def test_hash_binding_position_read_and_s4_edges(monkeypatch) -> None:
    invalid_ids = make_hash_case(b"abcd")
    assert isinstance(
        invalid_ids.run(project_id="not-a-uuid"),
        HashIoError,
    )
    invalid_ids.lease.close()

    identity = make_hash_case(b"abcd")
    object.__setattr__(identity.lease, "_volume_id", "f" * 16)
    assert isinstance(identity.run(), HashIoError)
    identity.lease.close()

    self_invalid = make_hash_case(b"abcd")
    monkeypatch.setattr(
        hashing_module,
        "compare_snapshots",
        lambda left, right: ComparisonFailed("invalid")
        if left is right
        else hashing_module.SameInstanceUnchanged(),
    )
    assert isinstance(self_invalid.run(), HashIoError)
    self_invalid.lease.close()
    monkeypatch.undo()

    wrong_position = make_hash_case(b"abcd")
    wrong_position.port.set_file_offset = lambda handle, offset: Win32Ok(1)
    assert isinstance(wrong_position.run(), HashIoError)
    wrong_position.lease.close()

    nonbytes = make_hash_case(b"abcd")
    original_read = nonbytes.port.read_file
    nonbytes.port.read_file = lambda handle, maximum: Win32Ok("bad")  # type: ignore[assignment]
    assert isinstance(nonbytes.run(), HashIoError)
    nonbytes.port.read_file = original_read
    nonbytes.lease.close()

    malformed_eof = make_hash_case(b"")
    malformed_eof.port.read_file = lambda handle, maximum: Win32Ok(b"xx")  # type: ignore[assignment]
    assert isinstance(malformed_eof.run(), HashIoError)
    malformed_eof.lease.close()


class DelayedCancellation(CancellationToken):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_once = False

    @property
    def is_cancelled(self) -> bool:
        actual = super().is_cancelled
        if actual and not self.hidden_once:
            self.hidden_once = True
            return False
        return actual


def test_precise_pre_s4_post_s4_and_direct_s4_change_edges(monkeypatch) -> None:
    pre_s4 = make_hash_case(b"abcd")
    token = DelayedCancellation()
    pre_s4.port.after_reads[2] = token.cancel
    result = pre_s4.run(token=token)
    assert isinstance(result, HashCancelled)
    assert result.error.phase == "hash.s4"
    pre_s4.lease.close()

    post_s4 = make_hash_case(b"abcd")
    post_token = CancellationToken()
    original = lease_module._LeaseIoSession.recheck

    def cancel_after_recheck(session, cancellation):
        result = original(session, CancellationToken())
        post_token.cancel()
        return result

    monkeypatch.setattr(lease_module._LeaseIoSession, "recheck", cancel_after_recheck)
    result = post_s4.run(token=post_token)
    assert isinstance(result, HashCancelled)
    assert result.error.phase == "hash.commit"
    post_s4.lease.close()
    monkeypatch.setattr(lease_module._LeaseIoSession, "recheck", original)

    direct_changed = make_hash_case(b"abcd")
    changed = replace(direct_changed.lease.s0, size_bytes=5)
    monkeypatch.setattr(
        lease_module._LeaseIoSession,
        "recheck",
        lambda session, cancellation: RecheckOk(changed),
    )
    assert isinstance(direct_changed.run(), SourceChanged)
    direct_changed.lease.close()


def test_public_nonlease_is_rejected() -> None:
    result = hash_lease_source(  # type: ignore[arg-type]
        object(),
        CancellationToken(),
        PROJECT_ID,
        HASH_RUN_ID,
    )
    assert isinstance(result, HashIoError)


def test_receipt_remaining_validation_and_publish_io_edges(monkeypatch) -> None:
    case, completed = _completed()
    payload = completed.receipt.model_dump()
    payload["validation_epoch"] = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(ValueError):
        HashReceipt.model_validate(payload)

    valid = receipt_module.hash_receipt_bytes(completed.receipt)
    pretty = ("{\n" + valid.decode().lstrip("{")).encode()
    with pytest.raises(ValueError):
        parse_hash_receipt_bytes(pretty)
    monkeypatch.setattr(receipt_module, "canonical_bytes", lambda receipt: b"x" * (2**20 + 1))
    with pytest.raises(ValueError):
        receipt_module.hash_receipt_bytes(completed.receipt)
    monkeypatch.undo()

    target = _target(case)
    case.port.add_file(target.canonical_dos_path, b"existing\n")
    case.port.failures["ReadFile"] = [955]
    read_error = publish_hash_receipt(case.port, target, completed, CancellationToken())
    assert isinstance(read_error, HashReceiptPublishIoError)

    fresh_case, fresh_completed = _completed()
    fresh_target = _target(fresh_case)
    monkeypatch.setattr(
        publish_module,
        "parse_hash_receipt_bytes",
        lambda data: (_ for _ in ()).throw(ValueError("validator")),
    )
    validation_error = publish_hash_receipt(
        fresh_case.port,
        fresh_target,
        fresh_completed,
        CancellationToken(),
    )
    assert isinstance(validation_error, HashReceiptPublishIoError)
    monkeypatch.undo()

    detail = failure(
        ErrorCode.ATOMIC_PUBLISH_FAILED,
        ErrorCategory.IO,
        "publish",
        "forced",
        win32_code=956,
    )
    monkeypatch.setattr(
        publish_module,
        "publish_initial",
        lambda *args, **kwargs: AtomicPublishIntegrity(detail),
    )
    forced = publish_hash_receipt(
        fresh_case.port,
        fresh_target,
        fresh_completed,
        CancellationToken(),
    )
    assert isinstance(forced, HashReceiptPublishIoError)
    assert forced.error.win32_code == 956
    case.lease.close()
    fresh_case.lease.close()
