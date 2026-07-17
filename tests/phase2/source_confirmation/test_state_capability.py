from __future__ import annotations

import copy
import gc
import pickle
from dataclasses import replace

import pytest
from tests.phase2.source_confirmation.conftest import make_case

import matrix_auto_cutter.phase2.source_confirmation.capability as capability_module
from matrix_auto_cutter.models import SourceBinding
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.close_gate import RecheckCancelled, RecheckOk
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmedSource,
    SourceConfirmed,
    SourceState,
    SourceStateInvariantError,
    SourceStateMachine,
    confirm_source,
)


def test_source_state_exact_success_graph_and_all_error_edges() -> None:
    success = SourceStateMachine()
    for target in (
        SourceState.PROBING,
        SourceState.PROBED,
        SourceState.HASHING,
        SourceState.HASH_COMPLETED,
        SourceState.CONFIRMING_IDENTITY,
        SourceState.CONFIRMED,
    ):
        success.transition(target)
    assert success.state is SourceState.CONFIRMED

    allowed = {
        SourceState.CLOSED: (
            SourceState.INVALIDATED,
            SourceState.DISAPPEARED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
        SourceState.PROBING: (
            SourceState.INVALIDATED,
            SourceState.UNSUPPORTED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
        SourceState.PROBED: (
            SourceState.INVALIDATED,
            SourceState.UNSUPPORTED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
        SourceState.HASHING: (
            SourceState.INVALIDATED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
        SourceState.HASH_COMPLETED: (
            SourceState.INVALIDATED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
        SourceState.CONFIRMING_IDENTITY: (
            SourceState.INVALIDATED,
            SourceState.DISAPPEARED,
            SourceState.FAILED,
            SourceState.CANCELLED,
        ),
    }
    prefixes = {
        SourceState.CLOSED: (),
        SourceState.PROBING: (SourceState.PROBING,),
        SourceState.PROBED: (SourceState.PROBING, SourceState.PROBED),
        SourceState.HASHING: (
            SourceState.PROBING,
            SourceState.PROBED,
            SourceState.HASHING,
        ),
        SourceState.HASH_COMPLETED: (
            SourceState.PROBING,
            SourceState.PROBED,
            SourceState.HASHING,
            SourceState.HASH_COMPLETED,
        ),
        SourceState.CONFIRMING_IDENTITY: (
            SourceState.PROBING,
            SourceState.PROBED,
            SourceState.HASHING,
            SourceState.HASH_COMPLETED,
            SourceState.CONFIRMING_IDENTITY,
        ),
    }
    for origin, targets in allowed.items():
        for target in targets:
            machine = SourceStateMachine()
            for step in prefixes[origin]:
                machine.transition(step)
            machine.transition(target)
            with pytest.raises(SourceStateInvariantError):
                machine.transition(SourceState.CONFIRMED)

    with pytest.raises(SourceStateInvariantError):
        SourceStateMachine().transition("probing")  # type: ignore[arg-type]
    assert {item.value for item in SourceState} == {
        "unknown",
        "located",
        "awaiting_close",
        "closed",
        "probing",
        "probed",
        "hashing",
        "hash_completed",
        "confirming_identity",
        "confirmed",
        "invalidated",
        "disappeared",
        "unsupported",
        "cancelled",
        "failed",
    }


def test_confirmed_source_cannot_be_constructed_copied_serialized_or_forged() -> None:
    with pytest.raises(TypeError):
        ConfirmedSource()
    case = make_case()
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        capability = result.confirmed_source
        with pytest.raises(TypeError):
            replace(capability, project_id="x")
        with pytest.raises(TypeError):
            copy.copy(capability)
        with pytest.raises(TypeError):
            copy.deepcopy(capability)
        with pytest.raises(TypeError):
            pickle.dumps(capability)
        with pytest.raises(AttributeError):
            capability._token = object()

        forged = object.__new__(ConfirmedSource)
        for slot in ConfirmedSource.__slots__:
            if slot != "__weakref__":
                object.__setattr__(forged, slot, getattr(capability, slot))
        assert not forged.authorized
        with pytest.raises(RuntimeError):
            forged.require_authorized()

        different = result.source_identity.model_copy(
            update={"binding": SourceBinding.MANUAL_REMUX}
        )
        object.__setattr__(capability, "_identity", different)
        assert not capability.authorized
    finally:
        case.close()


def test_confirmed_source_registry_is_weak_and_closed_lease_revokes_authority() -> None:
    case = make_case()
    result = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(result, SourceConfirmed)
    before = capability_module._CONFIRMED_AUTHORITY.count()
    capability = result.confirmed_source
    assert capability.authorized
    case.request.lease.close()
    assert not capability.authorized
    del capability
    del result
    gc.collect()
    assert capability_module._CONFIRMED_AUTHORITY.count() == before - 1


def test_private_confirmed_usage_is_nonblocking_revocable_and_never_reusable() -> None:
    case = make_case()
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        capability = result.confirmed_source
        captured = []

        def inspect(usage):
            captured.append(usage)
            usage_token = CancellationToken()
            return (
                usage.source_identity,
                usage.evidence,
                usage.project_id,
                usage.run_id,
                usage.source_path,
                usage.volume_id,
                usage.file_id,
                usage.matches_port(case.port),
                usage.matches_port(object()),
                isinstance(usage.recheck(usage_token), RecheckOk),
                usage.commit(usage_token),
                usage.run_project_locked(lambda lock: lock.held),
            )

        observed = capability_module._run_confirmed_source_usage(
            capability,
            CancellationToken(),
            inspect,
        )
        assert observed[-5:] == (True, False, True, True, True)
        stale = captured[0]
        with pytest.raises(RuntimeError, match="no longer active"):
            stale.commit(CancellationToken())
        with pytest.raises(RuntimeError, match="no longer active"):
            stale.recheck(CancellationToken())
        with pytest.raises(RuntimeError, match="no longer active"):
            stale.run_project_locked(lambda lock: lock.held)
        with pytest.raises(RuntimeError, match="no longer active"):
            stale.matches_port(case.port)

        lease_record = __import__(
            "matrix_auto_cutter.phase2.close_gate.lease",
            fromlist=["_LEASE_AUTHORITY"],
        )._LEASE_AUTHORITY._record(capability._lease)
        assert lease_record is not None
        lease_record.close_requested = True
        try:
            unavailable = capability_module._run_confirmed_source_usage(
                capability,
                CancellationToken(),
                lambda usage: usage,
            )
            assert unavailable.reason == "lease_closed"
        finally:
            lease_record.close_requested = False

        capability_module._invalidate_confirmed_source(capability)
        capability_module._invalidate_confirmed_source(capability)
        unavailable = capability_module._run_confirmed_source_usage(
            capability,
            CancellationToken(),
            lambda usage: usage,
        )
        assert unavailable.reason == "confirmed_source_not_authorized"

        forged = object.__new__(ConfirmedSource)
        unavailable = capability_module._run_confirmed_source_usage(
            forged,
            CancellationToken(),
            lambda usage: usage,
        )
        assert unavailable.reason == "confirmed_source_not_authorized"

    finally:
        case.close()

    revoked_during_usage = make_case()
    try:
        confirmed = confirm_source(
            revoked_during_usage.ports,
            revoked_during_usage.request,
            CancellationToken(),
        )
        assert isinstance(confirmed, SourceConfirmed)
        capability = confirmed.confirmed_source

        def revoke(usage):
            capability_module._invalidate_confirmed_source(capability)
            assert usage.commit(CancellationToken()) is False
            assert isinstance(usage.recheck(CancellationToken()), RecheckCancelled)
            with pytest.raises(RuntimeError, match="authority"):
                usage.run_project_locked(lambda lock: lock.held)
            return "revoked"

        assert (
            capability_module._run_confirmed_source_usage(
                capability,
                CancellationToken(),
                revoke,
            )
            == "revoked"
        )
    finally:
        revoked_during_usage.close()

    cancelled_case = make_case()
    try:
        confirmed = confirm_source(
            cancelled_case.ports,
            cancelled_case.request,
            CancellationToken(),
        )
        assert isinstance(confirmed, SourceConfirmed)
        token = CancellationToken()
        token.cancel()
        unavailable = capability_module._run_confirmed_source_usage(
            confirmed.confirmed_source,
            token,
            lambda usage: usage,
        )
        assert unavailable.reason == "confirmed_source_not_authorized"
    finally:
        cancelled_case.close()


def test_private_usage_is_bound_to_issuer_snapshots_during_the_operation() -> None:
    case = make_case()
    try:
        confirmed = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(confirmed, SourceConfirmed)
        capability = confirmed.confirmed_source
        record = capability_module._CONFIRMED_AUTHORITY._records[capability]
        assert not record.matches(object.__new__(ConfirmedSource))

        def tamper(usage):
            object.__setattr__(capability, "_identity", usage.source_identity.model_copy())
            assert usage.commit(CancellationToken()) is False
            assert isinstance(usage.recheck(CancellationToken()), RecheckCancelled)
            with pytest.raises(RuntimeError, match="authority"):
                usage.run_project_locked(lambda lock: lock.held)

        capability_module._run_confirmed_source_usage(
            capability,
            CancellationToken(),
            tamper,
        )
        assert not capability.authorized
    finally:
        case.close()
