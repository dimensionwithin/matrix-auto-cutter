from __future__ import annotations

from threading import Event, Thread

import pytest
from tests.phase2.close_gate.conftest import FakeWaitClock, gate

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateCancelled,
    CloseGateClosed,
    CloseGateUnstable,
)
from matrix_auto_cutter.phase2.win32_port import Win32Failure


def test_cancellation_before_open(close_port, source_path) -> None:
    token = CancellationToken()
    token.cancel()
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateCancelled)
    assert not close_port.handles


def test_cancellation_after_restrictive_open(close_port, source_path) -> None:
    token = CancellationToken()
    original = close_port.open_file

    def cancelling_open(*args, **kwargs):
        result = original(*args, **kwargs)
        if (
            not hasattr(result, "error")
            and args[0].casefold().endswith("source.mp4")
            and args[2] == 1
        ):
            token.cancel()
        return result

    close_port.open_file = cancelling_open
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateCancelled)
    assert not close_port.handles


@pytest.mark.parametrize("wait_index", [0, 1])
def test_cancellation_during_each_wait(close_port, source_path, wait_index: int) -> None:
    token = CancellationToken()
    callbacks = [lambda: None, lambda: None]
    callbacks[wait_index] = token.cancel
    result = gate(
        close_port,
        source_path,
        token=token,
        clock=FakeWaitClock(callbacks),
    )
    assert isinstance(result, CloseGateCancelled)
    assert not close_port.handles


def test_cancellation_before_file_id_lock(close_port, source_path) -> None:
    token = CancellationToken()
    close_port.snapshot_callbacks[1] = token.cancel
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateCancelled)
    assert not any(
        "\\locks\\ownership\\sources\\" in item[0].casefold()
        for item in close_port.detailed_open_history
    )


class CancelAtLeaseCommit(CancellationToken):
    def __init__(self) -> None:
        super().__init__()
        self.permits = 0

    def begin_irreversible_commit(self):
        self.permits += 1
        if self.permits == 3:
            self.cancel()
        return super().begin_irreversible_commit()


def test_cancellation_immediately_before_lease_commit(close_port, source_path) -> None:
    token = CancelAtLeaseCommit()
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateCancelled)
    assert token.permits == 3
    assert not close_port.handles


class CommitRaceToken(CancellationToken):
    def __init__(self, entered: Event, proceed: Event) -> None:
        super().__init__()
        self.permits = 0
        self.entered = entered
        self.proceed = proceed

    def begin_irreversible_commit(self):
        self.permits += 1
        if self.permits == 3:
            self.entered.set()
            assert self.proceed.wait(2)
        return super().begin_irreversible_commit()


def test_cancel_wins_cancel_vs_commit_race(close_port, source_path) -> None:
    entered = Event()
    proceed = Event()
    token = CommitRaceToken(entered, proceed)
    results = []
    worker = Thread(target=lambda: results.append(gate(close_port, source_path, token=token)))
    worker.start()
    assert entered.wait(2)
    token.cancel()
    proceed.set()
    worker.join(2)
    assert isinstance(results[0], CloseGateCancelled)
    assert not close_port.handles


def test_commit_wins_cancel_vs_commit_race(close_port, source_path) -> None:
    token = CancellationToken()
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateClosed)
    token.cancel()
    assert not result.lease.closed
    result.lease.close()


@pytest.mark.parametrize("failure_point", [1, 2, 3])
def test_cleanup_after_each_snapshot_failure(close_port, source_path, failure_point: int) -> None:
    close_port.snapshot_errors[failure_point] = Win32Failure(
        900 + failure_point,
        "GetFileInformationByHandleEx",
        "snapshot failed",
    )
    gate(close_port, source_path)
    assert not close_port.handles
    assert not close_port.exclusive


def test_cleanup_failure_is_secondary_and_primary_remains_unstable(close_port, source_path) -> None:
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    close_port.source_handle_close_error = Win32Failure(812, "CloseHandle", "error 812")
    result = gate(
        close_port,
        source_path,
        clock=FakeWaitClock([lambda: node.data.extend(b"changed")]),
    )
    assert isinstance(result, CloseGateUnstable)
    assert result.error.cleanup_diagnostics
    assert result.error.cleanup_diagnostics[0].win32_code == 812
    assert result.error.code.value == "E_CLOSE_GATE_UNSTABLE"


def test_baseexception_cleanup_is_safe(close_port, source_path) -> None:
    class RaisingClock(FakeWaitClock):
        def wait(self, cancellation, seconds):
            del cancellation, seconds
            raise KeyboardInterrupt("stop")

    with pytest.raises(KeyboardInterrupt):
        gate(close_port, source_path, clock=RaisingClock())
    assert not close_port.handles


def test_early_wait_is_unknown_and_cleans_up(close_port, source_path) -> None:
    clock = FakeWaitClock()

    def early_wait(cancellation, seconds):
        del cancellation, seconds
        return False

    clock.wait = early_wait
    result = gate(close_port, source_path, clock=clock)
    assert result.error.code.value == "E_CLOSE_GATE_WIN32_UNKNOWN"
    assert not close_port.handles
