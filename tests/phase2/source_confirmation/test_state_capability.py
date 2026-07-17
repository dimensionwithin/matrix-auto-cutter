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
