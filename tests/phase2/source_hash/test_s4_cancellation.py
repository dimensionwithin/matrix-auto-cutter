from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread

import pytest
from tests.phase2.source_hash.conftest import make_hash_case

import matrix_auto_cutter.phase2.close_gate.lease as lease_module
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import RecheckOk
from matrix_auto_cutter.phase2.source_hash import (
    HashCancelled,
    HashCompleted,
    HashIoError,
    SourceChanged,
)
from matrix_auto_cutter.phase2.win32_port import Win32Failure


@pytest.mark.parametrize(
    "mutation",
    ["size", "last_write", "change", "attributes", "volume", "file_id"],
)
def test_every_s4_evidence_change_is_source_changed(mutation: str) -> None:
    case = make_hash_case(b"abcdefgh")
    node = case.port.nodes[case.port._key(case.lease.source_path.canonical_dos_path)]

    def mutate() -> None:
        if mutation == "size":
            node.data.extend(b"x")
        elif mutation == "last_write":
            node.write_time += 1
        elif mutation == "change":
            node.change_time += 1
        elif mutation == "attributes":
            node.attributes ^= 0x20
        elif mutation == "volume":
            node.volume += 1
        else:
            node.file_id = b"z" * 16

    case.port.snapshot_callbacks[4] = mutate
    result = case.run(block_size=4)
    assert isinstance(result, SourceChanged)
    assert result.error.phase == "hash.s4"
    case.lease.close()


def test_s4_missing_identity_unknown_version_and_key_tamper_fail_io(monkeypatch) -> None:
    missing = make_hash_case(b"abcd")
    node = missing.port.nodes[missing.port._key(missing.lease.source_path.canonical_dos_path)]
    missing.port.snapshot_callbacks[4] = lambda: setattr(node, "file_id", None)
    assert isinstance(missing.run(), HashIoError)
    missing.lease.close()

    for mode in ("version", "key"):
        case = make_hash_case(b"abcd")
        original = lease_module._LeaseIoSession.recheck

        def tampered(session, cancellation, original=original, mode=mode):
            result = original(session, cancellation)
            assert isinstance(result, RecheckOk)
            snapshot = replace(result.snapshot)
            if mode == "version":
                object.__setattr__(snapshot, "evidence_version", "unknown/9")
            else:
                object.__setattr__(snapshot, "snapshot_key", "0" * 64)
            return RecheckOk(snapshot)

        monkeypatch.setattr(lease_module._LeaseIoSession, "recheck", tampered)
        assert isinstance(case.run(), HashIoError)
        case.lease.close()
        monkeypatch.setattr(lease_module._LeaseIoSession, "recheck", original)


def test_s4_operational_error_and_closed_lease_are_hash_io() -> None:
    io_case = make_hash_case(b"abcd")
    io_case.port.snapshot_errors[4] = Win32Failure(811, "query", "S4 failed")
    io_result = io_case.run()
    assert isinstance(io_result, HashIoError)
    assert io_result.error.underlying is not None
    io_case.lease.close()

    closed = make_hash_case(b"abcd")
    closed.lease.close()
    result = closed.run()
    assert isinstance(result, HashIoError)
    assert "lease_not_authorized" in result.error.message


class NthCancellation(CancellationToken):
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


@pytest.mark.parametrize("target", range(1, 16))
def test_cancellation_at_every_hash_checkpoint_never_leaks_success(target: int) -> None:
    case = make_hash_case(b"abcdefghij")
    token = NthCancellation(target)
    result = case.run(token=token, block_size=2)
    if isinstance(result, HashCompleted):
        assert not token.is_cancelled
    else:
        assert isinstance(result, HashCancelled)
        assert not hasattr(result, "sha256") and not hasattr(result, "receipt")
    case.lease.close()


def test_cancellation_from_reads_and_s4_callbacks() -> None:
    between = make_hash_case(b"abcdefgh")
    between_token = CancellationToken()
    between.port.after_reads[1] = between_token.cancel
    assert isinstance(between.run(token=between_token, block_size=4), HashCancelled)
    between.lease.close()

    after_eof = make_hash_case(b"abcd")
    eof_token = CancellationToken()
    after_eof.port.after_reads[2] = eof_token.cancel
    assert isinstance(after_eof.run(token=eof_token, block_size=4), HashCancelled)
    after_eof.lease.close()

    after_s4 = make_hash_case(b"abcd")
    s4_token = CancellationToken()
    after_s4.port.snapshot_callbacks[4] = s4_token.cancel
    assert isinstance(after_s4.run(token=s4_token), HashCancelled)
    after_s4.lease.close()


class CommitRaceToken(CancellationToken):
    def __init__(self, entered: Event, proceed: Event) -> None:
        super().__init__()
        self.commits = 0
        self.entered = entered
        self.proceed = proceed

    def begin_irreversible_commit(self):
        self.commits += 1
        if self.commits == 2:
            self.entered.set()
            assert self.proceed.wait(2)
        return super().begin_irreversible_commit()


def test_cancel_wins_hash_commit_race() -> None:
    case = make_hash_case(b"abcdefgh")
    entered = Event()
    proceed = Event()
    token = CommitRaceToken(entered, proceed)
    output = []
    worker = Thread(target=lambda: output.append(case.run(token=token)))
    worker.start()
    assert entered.wait(2)
    token.cancel()
    proceed.set()
    worker.join(2)
    assert isinstance(output[0], HashCancelled)
    case.lease.close()


def test_hash_commit_wins_and_late_cancel_does_not_revoke_success() -> None:
    case = make_hash_case(b"abcdefgh")
    token = CancellationToken()
    result = case.run(token=token)
    assert isinstance(result, HashCompleted)
    assert token.cancel()
    assert result.sha256
    case.lease.close()
