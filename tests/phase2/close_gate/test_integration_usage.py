from __future__ import annotations

from threading import Event, Thread

import pytest
from tests.phase2.close_gate.conftest import PROJECT_A, gate

import matrix_auto_cutter.phase2.close_gate.lease as lease_module
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.close_gate import CloseGateClosed, RecheckOk


def test_private_integration_usage_blocks_close_and_keeps_recheck_available(
    close_port, source_path
) -> None:
    gated = gate(close_port, source_path)
    assert isinstance(gated, CloseGateClosed)
    entered = Event()
    proceed = Event()
    close_done = Event()
    outputs = []

    def operation(session):
        entered.set()
        assert proceed.wait(2)
        outputs.append(session.recheck(CancellationToken()))
        assert not session.commit(CancellationToken())
        return "finished"

    usage = []
    worker = Thread(
        target=lambda: usage.append(
            lease_module._run_lease_usage(
                gated.lease,
                PROJECT_A,
                CancellationToken(),
                operation,
            )
        )
    )
    worker.start()
    assert entered.wait(2)
    closer = Thread(target=lambda: (gated.lease.close(), close_done.set()))
    closer.start()
    assert not close_done.wait(0.05)
    proceed.set()
    worker.join(2)
    closer.join(2)
    assert usage == ["finished"]
    assert isinstance(outputs[0], RecheckOk)
    assert close_done.is_set()
    assert not close_port.handles


def test_integration_usage_authenticates_every_ownership_and_deactivates_session(
    close_port, source_path
) -> None:
    gated = gate(close_port, source_path)
    assert isinstance(gated, CloseGateClosed)
    lease = gated.lease
    token = CancellationToken()
    cancelled = CancellationToken()
    cancelled.cancel()
    assert lease_module._run_lease_usage(lease, PROJECT_A, cancelled, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("cancelled")
    )
    assert lease_module._run_lease_usage(lease, "wrong", token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("project_ownership_unavailable")
    )
    record = lease_module._LEASE_AUTHORITY._record(lease)
    assert record is not None

    project = record.resources.project_lock
    record.resources.project_lock = None
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("project_ownership_unavailable")
    )
    record.resources.project_lock = project

    path = record.resources.path_lock
    record.resources.path_lock = None
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("path_ownership_unavailable")
    )
    record.resources.path_lock = path

    ownership = record.resources.source_ownership
    record.resources.source_ownership = None
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("source_ownership_unavailable")
    )
    record.resources.source_ownership = ownership

    source = record.resources.source_handle
    record.resources.source_handle = None
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("source_handle_unavailable")
    )
    record.resources.source_handle = source

    leaked = lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: session)
    assert isinstance(leaked, lease_module._LeaseUsageSession)
    with pytest.raises(RuntimeError):
        leaked.recheck(token)
    with pytest.raises(RuntimeError):
        leaked.commit(token)
    with pytest.raises(RuntimeError):
        leaked.run_project_locked(lambda lock: lock.held)
    with pytest.raises(RuntimeError):
        leaked.matches_port(close_port)

    assert lease_module._run_lease_usage(
        lease,
        PROJECT_A,
        token,
        lambda session: (session.matches_port(close_port), session.matches_port(object())),
    ) == (True, False)

    assert lease_module._run_lease_usage(
        lease,
        PROJECT_A,
        token,
        lambda session: session.run_project_locked(lambda lock: lock.held),
    )

    def missing_project(session):
        held_project = record.resources.project_lock
        record.resources.project_lock = None
        try:
            with pytest.raises(RuntimeError, match="project ownership"):
                session.run_project_locked(lambda lock: lock.held)
        finally:
            record.resources.project_lock = held_project

    lease_module._run_lease_usage(lease, PROJECT_A, token, missing_project)

    assert lease_module._run_lease_usage(
        lease,
        PROJECT_A,
        token,
        lambda session: session.commit(token),
    )
    record.close_requested = True
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("lease_closed")
    )
    record.close_requested = False

    with pytest.raises(KeyboardInterrupt):
        lease_module._run_lease_usage(
            lease,
            PROJECT_A,
            token,
            lambda session: (_ for _ in ()).throw(KeyboardInterrupt("primary")),
        )
    assert record.active_usages == 0
    lease.close()
    assert lease_module._run_lease_usage(lease, PROJECT_A, token, lambda session: None) == (
        lease_module._LeaseUsageUnavailable("lease_not_authorized")
    )
