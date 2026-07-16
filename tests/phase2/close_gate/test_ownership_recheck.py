from __future__ import annotations

from threading import Barrier, Event, Thread

from tests.phase2.close_gate.conftest import (
    PROJECT_A,
    PROJECT_B,
    alias_source,
    gate,
    make_source,
)

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateBusy,
    CloseGateClosed,
    CloseGateInaccessible,
    RecheckCancelled,
    RecheckClosed,
    RecheckDeletePending,
    RecheckOk,
    RecheckUnstable,
)
from matrix_auto_cutter.phase2.win32_port import ERROR_ACCESS_DENIED, Win32Failure


def test_project_path_source_lock_order_and_reverse_cleanup(close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    opens = [item[0].casefold() for item in close_port.detailed_open_history]
    project_index = next(i for i, value in enumerate(opens) if "\\ownership\\projects\\" in value)
    path_index = next(i for i, value in enumerate(opens) if "\\ownership\\paths\\" in value)
    source_open_index = next(
        i
        for i, item in enumerate(close_port.detailed_open_history)
        if item[0].casefold().endswith("source.mp4") and item[2] == 1
    )
    source_lock_index = next(
        i for i, value in enumerate(opens) if "\\ownership\\sources\\" in value
    )
    assert project_index < path_index < source_open_index < source_lock_index
    result.lease.close()
    ownership_closes = [value.casefold() for value in close_port.close_events if ".lck" in value]
    assert "\\sources\\" in ownership_closes[-3]
    assert "\\paths\\" in ownership_closes[-2]
    assert "\\projects\\" in ownership_closes[-1]


def test_no_source_lock_before_file_id_and_partial_failure_reverses(
    close_port, source_path
) -> None:
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    node.file_id = None
    gate(close_port, source_path)
    assert not any(
        "\\locks\\ownership\\sources\\" in item[0].casefold()
        for item in close_port.detailed_open_history
    )
    assert not close_port.handles


def test_hardlink_aliases_share_source_ownership(close_port, source_path) -> None:
    alias = alias_source(close_port, source_path, r"C:\Aliases\same.mp4")
    first = gate(close_port, source_path, project_id=PROJECT_A)
    assert isinstance(first, CloseGateClosed)
    second = gate(close_port, alias, project_id=PROJECT_B)
    assert isinstance(second, CloseGateBusy)
    assert second.error.phase == "source_lock_open"
    first.lease.close()
    retry = gate(close_port, alias, project_id=PROJECT_B)
    assert isinstance(retry, CloseGateClosed)
    retry.lease.close()


def test_different_file_ids_do_not_block_at_source_lock(close_port, source_path) -> None:
    other = make_source(close_port, r"C:\Sources\other.mp4")
    first = gate(close_port, source_path, project_id=PROJECT_A)
    second = gate(close_port, other, project_id=PROJECT_B)
    assert isinstance(first, CloseGateClosed)
    assert isinstance(second, CloseGateClosed)
    first.lease.close()
    second.lease.close()


def test_access_denied_on_source_ownership_is_not_busy(close_port, source_path) -> None:
    close_port.source_lock_error = Win32Failure(
        ERROR_ACCESS_DENIED,
        "CreateFileW",
        "source ownership denied",
    )
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateInaccessible)
    assert not isinstance(result, CloseGateBusy)


def test_lock_diagnostic_file_does_not_prove_ownership(close_port, source_path) -> None:
    close_port.add_file(
        r"C:\Local\DimensionWithin\MatrixAutoCutter\locks\diagnostics\fake.json",
        b"not authority",
    )
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    result.lease.close()


def test_recheck_open_changed_delete_pending_cancelled_and_after_close(
    close_port, source_path
) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    lease = result.lease
    assert isinstance(lease.recheck(), RecheckOk)
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    node.write_time += 1
    assert isinstance(lease.recheck(), RecheckUnstable)
    node.write_time -= 1
    node.delete_pending = True
    assert isinstance(lease.recheck(), RecheckDeletePending)
    node.delete_pending = False
    token = CancellationToken()
    token.cancel()
    assert isinstance(lease.recheck(token), RecheckCancelled)
    lease.close()
    assert isinstance(lease.recheck(), RecheckClosed)


def test_multiple_parallel_rechecks_are_safe(close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    barrier = Barrier(6)
    outputs = []

    def worker() -> None:
        barrier.wait()
        outputs.append(result.lease.recheck())

    threads = [Thread(target=worker) for _ in range(5)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(2)
    assert len(outputs) == 5
    assert all(isinstance(item, RecheckOk) for item in outputs)
    result.lease.close()


def test_recheck_close_race_never_publishes_success_after_close(close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    entered = Event()
    proceed = Event()
    original = close_port.query_file_info

    def blocking_query(handle):
        if handle.value in close_port.source_gate_handles and close_port.snapshot_query_count >= 3:
            entered.set()
            assert proceed.wait(2)
        return original(handle)

    close_port.query_file_info = blocking_query
    rechecks = []
    recheck_thread = Thread(target=lambda: rechecks.append(result.lease.recheck()))
    recheck_thread.start()
    assert entered.wait(2)
    close_done = Event()
    close_thread = Thread(target=lambda: (result.lease.close(), close_done.set()))
    close_thread.start()
    proceed.set()
    recheck_thread.join(2)
    close_thread.join(2)
    assert close_done.is_set()
    assert isinstance(rechecks[0], RecheckClosed)
    assert not close_port.handles


def test_no_successful_recheck_with_closed_source_handle(close_port, source_path) -> None:
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    result.lease.close()
    assert isinstance(result.lease.recheck(), RecheckClosed)
