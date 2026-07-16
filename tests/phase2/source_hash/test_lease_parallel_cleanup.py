from __future__ import annotations

from threading import Event, Thread

import pytest
from tests.phase2.source_hash.conftest import HASH_RUN_ID, PROJECT_ID, make_hash_case

import matrix_auto_cutter.phase2.close_gate.lease as lease_module
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import CloseGateLease, RecheckOk
from matrix_auto_cutter.phase2.source_hash import (
    HashCompleted,
    HashIoError,
    hash_lease_source,
)
from matrix_auto_cutter.phase2.win32_port import Win32Failure


def test_only_authentic_open_lease_is_accepted() -> None:
    forged = object.__new__(CloseGateLease)
    result = hash_lease_source(
        forged,
        CancellationToken(),
        PROJECT_ID,
        HASH_RUN_ID,
        block_size_bytes=4,
    )
    assert isinstance(result, HashIoError)


def test_hash_close_race_keeps_handle_open_and_never_publishes_after_close() -> None:
    case = make_hash_case(b"abcdefgh")
    entered = Event()
    proceed = Event()
    closed = Event()
    case.port.before_reads[1] = lambda: (entered.set(), proceed.wait(2))
    results = []
    hash_thread = Thread(target=lambda: results.append(case.run(block_size=4)))
    hash_thread.start()
    assert entered.wait(2)
    close_thread = Thread(target=lambda: (case.lease.close(), closed.set()))
    close_thread.start()
    assert not closed.wait(0.05)
    handle = next(iter(case.port.source_gate_handles))
    assert handle in case.port.handles
    proceed.set()
    hash_thread.join(2)
    close_thread.join(2)
    assert closed.is_set()
    assert isinstance(results[0], HashIoError)
    assert not case.port.handles


def test_parallel_hash_is_fail_closed_without_shared_pointer_race() -> None:
    case = make_hash_case(b"abcdefgh")
    entered = Event()
    proceed = Event()
    case.port.before_reads[1] = lambda: (entered.set(), proceed.wait(2))
    first = []
    thread = Thread(target=lambda: first.append(case.run(block_size=4)))
    thread.start()
    assert entered.wait(2)
    second = case.run(block_size=4)
    assert isinstance(second, HashIoError)
    assert "lease_io_already_active" in second.error.message
    proceed.set()
    thread.join(2)
    assert isinstance(first[0], HashCompleted)
    case.lease.close()


def test_parallel_recheck_is_pointer_neutral_and_hash_remains_valid() -> None:
    case = make_hash_case(b"abcdefgh")
    entered = Event()
    proceed = Event()
    case.port.before_reads[1] = lambda: (entered.set(), proceed.wait(2))
    output = []
    thread = Thread(target=lambda: output.append(case.run(block_size=4)))
    thread.start()
    assert entered.wait(2)
    assert isinstance(case.lease.recheck(), RecheckOk)
    proceed.set()
    thread.join(2)
    assert isinstance(output[0], HashCompleted)
    assert len(set(case.port.read_handles)) == 1
    case.lease.close()


def test_baseexception_releases_internal_io_session(monkeypatch) -> None:
    read_case = make_hash_case(b"abcd")
    original_read = lease_module._LeaseIoSession.read
    monkeypatch.setattr(
        lease_module._LeaseIoSession,
        "read",
        lambda self, maximum: (_ for _ in ()).throw(KeyboardInterrupt("read")),
    )
    with pytest.raises(KeyboardInterrupt):
        read_case.run()
    monkeypatch.setattr(lease_module._LeaseIoSession, "read", original_read)
    assert isinstance(read_case.run(), HashCompleted)
    read_case.lease.close()

    s4_case = make_hash_case(b"abcd")
    original_recheck = lease_module._LeaseIoSession.recheck
    monkeypatch.setattr(
        lease_module._LeaseIoSession,
        "recheck",
        lambda self, cancellation: (_ for _ in ()).throw(KeyboardInterrupt("s4")),
    )
    with pytest.raises(KeyboardInterrupt):
        s4_case.run()
    monkeypatch.setattr(lease_module._LeaseIoSession, "recheck", original_recheck)
    assert isinstance(s4_case.run(), HashCompleted)
    s4_case.lease.close()


def test_primary_hash_error_remains_primary_when_later_lease_close_fails() -> None:
    case = make_hash_case(b"abcd")
    case.port.read_plan = [Win32Failure(913, "ReadFile", "primary")]
    case.port.source_handle_close_error = Win32Failure(914, "CloseHandle", "secondary")
    result = case.run()
    assert isinstance(result, HashIoError)
    assert result.error.win32_code == 913
    diagnostics = case.lease.close()
    assert diagnostics and diagnostics[0].win32_code == 914


def test_hash_path_never_reopens_source() -> None:
    case = make_hash_case(b"abcdefgh")
    opens_before = list(case.port.detailed_open_history)
    result = case.run()
    assert isinstance(result, HashCompleted)
    assert case.port.detailed_open_history == opens_before
    case.lease.close()
