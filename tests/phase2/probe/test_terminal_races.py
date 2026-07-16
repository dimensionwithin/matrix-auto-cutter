from __future__ import annotations

from threading import Barrier, Event, Thread

import pytest

from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.errors import (
    ProbeErrorCode,
    _TerminalKind,
    _TerminalLatch,
    probe_error,
)


def event_error(name: str):
    return probe_error(ProbeErrorCode.PROCESS_FAILED, ErrorCategory.IO, name, name)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (_TerminalKind.READER_IO, _TerminalKind.CANCELLED),
        (_TerminalKind.CANCELLED, _TerminalKind.READER_IO),
        (_TerminalKind.OUTPUT_LIMIT, _TerminalKind.CANCELLED),
        (_TerminalKind.CANCELLED, _TerminalKind.OUTPUT_LIMIT),
        (_TerminalKind.TIMEOUT, _TerminalKind.READER_IO),
        (_TerminalKind.READER_IO, _TerminalKind.TIMEOUT),
        (_TerminalKind.PROCESS_CONTROL, _TerminalKind.CANCELLED),
        (_TerminalKind.CANCELLED, _TerminalKind.PROCESS_CONTROL),
        (_TerminalKind.EXIT_CODE, _TerminalKind.POST_SNAPSHOT),
        (_TerminalKind.OUTPUT_LIMIT, _TerminalKind.POST_SNAPSHOT),
        (_TerminalKind.CLEANUP, _TerminalKind.POST_SNAPSHOT),
        (_TerminalKind.READER_IO, _TerminalKind.CLEANUP),
    ],
)
def test_terminal_race_matrix_preserves_first_causal_failure(
    first: _TerminalKind, second: _TerminalKind
) -> None:
    for _ in range(25):
        latch = _TerminalLatch()
        assert latch.fail(first, event_error(first.value))
        assert not latch.fail(second, event_error(second.value))
        result = latch.error()
        assert result is not None
        assert result.phase == first.value
        assert [error.phase for error in result.secondary] == [second.value]


def test_process_exit_is_pending_and_cancel_wins_before_final_success() -> None:
    latch = _TerminalLatch()
    latch.process_exited()
    assert latch.kind is _TerminalKind.PROCESS_EXIT_PENDING
    assert latch.fail(_TerminalKind.CANCELLED, event_error("cancel"))
    assert not latch.finalize_success()
    assert latch.error() is not None


def test_success_can_retain_nonterminal_diagnostic() -> None:
    latch = _TerminalLatch()
    latch.process_exited()
    diagnostic = event_error("diagnostic")
    latch.diagnose(diagnostic)
    assert latch.finalize_success()
    assert latch.kind is _TerminalKind.SUCCESS
    assert latch.error() is None
    assert latch.diagnostics() == (diagnostic,)
    assert not latch.finalize_success()


def test_latch_rejects_nonfailure_kinds() -> None:
    for kind in (
        _TerminalKind.NONE,
        _TerminalKind.PROCESS_EXIT_PENDING,
        _TerminalKind.SUCCESS,
    ):
        with pytest.raises(ValueError):
            _TerminalLatch().fail(kind, event_error("invalid"))


def test_barrier_synchronized_event_order_is_repeatably_linearized() -> None:
    for _ in range(25):
        latch = _TerminalLatch()
        barrier = Barrier(3)
        release_reader = Event()
        release_cancel = Event()
        reader_done = Event()

        def reader(
            local_barrier: Barrier = barrier,
            release: Event = release_reader,
            local_latch: _TerminalLatch = latch,
            done: Event = reader_done,
        ) -> None:
            local_barrier.wait()
            release.wait()
            local_latch.fail(_TerminalKind.READER_IO, event_error("reader"))
            done.set()

        def cancel(
            local_barrier: Barrier = barrier,
            release: Event = release_cancel,
            local_latch: _TerminalLatch = latch,
        ) -> None:
            local_barrier.wait()
            release.wait()
            local_latch.fail(_TerminalKind.CANCELLED, event_error("cancel"))

        reader_thread = Thread(target=reader)
        cancel_thread = Thread(target=cancel)
        reader_thread.start()
        cancel_thread.start()
        barrier.wait()
        release_reader.set()
        assert reader_done.wait(1)
        release_cancel.set()
        reader_thread.join()
        cancel_thread.join()
        result = latch.error()
        assert result is not None
        assert result.phase == "reader"
        assert [error.phase for error in result.secondary] == ["cancel"]
