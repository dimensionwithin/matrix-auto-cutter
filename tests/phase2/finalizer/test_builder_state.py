from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from tests.phase2.finalizer.conftest import (
    SESSION_ID,
    add_validated_file,
    journal_bytes,
    journal_records,
    loaded_legacy,
    make_intent,
    source_identity,
)

from matrix_auto_cutter.models import CalibrationSample, PauseMeasurement
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.finalizer import JournalInputPaths
from matrix_auto_cutter.phase2.finalizer.errors import FinalizerErrorCode, FinalizerFailure
from matrix_auto_cutter.phase2.finalizer.loader import LoadedJournal, load_journal
from matrix_auto_cutter.phase2.finalizer.models import FinalizerStateName, JournalInputProfile
from matrix_auto_cutter.phase2.finalizer.sidecar_builder import (
    _automatic_policy,
    _clock_values,
    _pauses,
    build_sidecar,
)
from matrix_auto_cutter.phase2.finalizer.state_machine import (
    FinalizerStateInvariantError,
    FinalizerStateMachine,
)
from matrix_auto_cutter.sidecar import ObsEventSidecarV12


def _loaded(port, records: tuple[dict[str, object], ...]) -> LoadedJournal:
    path = add_validated_file(port, r"C:\Input\custom.ndjson", journal_bytes(records))
    value = load_journal(
        port,
        JournalInputProfile.LEGACY,
        JournalInputPaths(path),
        expected_recording_id=SESSION_ID,
    )
    assert isinstance(value, LoadedJournal)
    return value


def _complex_records() -> tuple[dict[str, object], ...]:
    header, start, stop = journal_records()
    common = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
    }
    middle = (
        {
            **common,
            "record_type": "event",
            "sequence": 2,
            "event_id": "11111111-1111-4111-8111-111111111111",
            "event_type": "intro_started",
            "monotonic_ns": 166_666_667,
            "output_frame_count": 10,
            "recording_paused": False,
            "pair_id": "22222222-2222-4222-8222-222222222222",
        },
        {
            **common,
            "record_type": "pause",
            "sequence": 3,
            "event_id": "33333333-3333-4333-8333-333333333333",
            "monotonic_ns": 333_333_333,
            "output_frame_count": 20,
            "recording_paused": True,
        },
        {
            **common,
            "record_type": "event",
            "sequence": 4,
            "event_id": "44444444-4444-4444-8444-444444444444",
            "event_type": "scene_changed",
            "monotonic_ns": 400_000_000,
            "output_frame_count": 20,
            "recording_paused": True,
        },
        {
            **common,
            "record_type": "resume",
            "sequence": 5,
            "event_id": "55555555-5555-4555-8555-555555555555",
            "monotonic_ns": 500_000_000,
            "output_frame_count": 20,
            "recording_paused": False,
        },
        {
            **common,
            "record_type": "event",
            "sequence": 6,
            "event_id": "66666666-6666-4666-8666-666666666666",
            "event_type": "intro_ended",
            "monotonic_ns": 666_666_667,
            "output_frame_count": 30,
            "recording_paused": False,
            "pair_id": "22222222-2222-4222-8222-222222222222",
        },
        {
            **common,
            "record_type": "event",
            "sequence": 7,
            "event_id": "77777777-7777-4777-8777-777777777777",
            "event_type": "manual_protection",
            "monotonic_ns": 833_333_333,
            "output_frame_count": 40,
            "recording_paused": False,
            "label": "keep",
        },
    )
    return (
        header,
        start,
        *middle,
        {
            **stop,
            "sequence": 8,
            "monotonic_ns": 1_166_666_667,
        },
    )


def test_builder_reuses_phase1_clock_pause_pairing_and_protection(fake_port) -> None:
    journal = _loaded(fake_port, _complex_records())
    intent = make_intent(journal)
    first = build_sidecar(journal, intent)
    second = build_sidecar(journal, intent)
    assert isinstance(first, ObsEventSidecarV12)
    assert first.schema_version == "1.2"
    assert second == first
    assert first.model_dump_json() == second.model_dump_json()
    assert first.recording_session_id == UUID(SESSION_ID)
    assert first.lifecycle.finalizer_run_id == UUID(intent.finalizer_run_id)
    assert first.source == intent.source_identity
    assert len(first.pause_intervals) == 1
    assert first.pause_intervals[0].end_reason == "resumed"
    assert "event_during_pause:44444444-4444-4444-8444-444444444444" in (
        first.finalization.warnings
    )
    assert all(item.type != "scene_changed" for item in first.events)
    assert any(item.type == "manual_protection" for item in first.events)
    assert first.clock.drift_ppm <= Decimal("500")


def test_builder_stop_while_paused_and_automatic_buffers(fake_port) -> None:
    records = list(journal_records())
    pause = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "pause",
        "sequence": 2,
        "event_id": "33333333-3333-4333-8333-333333333333",
        "monotonic_ns": 500_000_000,
        "output_frame_count": 30,
        "recording_paused": True,
    }
    records[-1] = {
        **records[-1],
        "sequence": 3,
        "monotonic_ns": 1_500_000_000,
        "output_frame_count": 30,
        "recording_paused": True,
    }
    records.insert(2, pause)
    journal = _loaded(fake_port, tuple(records))
    source = source_identity().model_copy(update={"duration_ms": 500, "video_frame_count": 30})
    sidecar = build_sidecar(journal, make_intent(journal, source=source))
    assert isinstance(sidecar, ObsEventSidecarV12)
    assert sidecar.pause_intervals[0].end_reason == "recording_stopped_while_paused"
    assert _automatic_policy("recording_started").buffer_after_ms == 1000
    assert _automatic_policy("outro_started").buffer_after_ms == 250
    assert _automatic_policy("outro_ended").buffer_before_ms == 250
    assert _automatic_policy("recording_stopped").buffer_before_ms == 1000


def test_builder_preserves_scene_uuid_and_exact_name_from_journal(fake_port) -> None:
    records = list(journal_records())
    records.insert(
        2,
        {
            "artifact_type": "recording_event_journal",
            "journal_schema_version": "1.0",
            "record_type": "event",
            "sequence": 2,
            "event_id": "77777777-7777-4777-8777-777777777777",
            "event_type": "scene_changed",
            "monotonic_ns": 500_000_000,
            "output_frame_count": 30,
            "recording_paused": False,
            "source_uuid": "444eb885-e589-4338-832c-8f5fd7eaaf41",
            "label": "Outro",
        },
    )
    records[-1] = {**records[-1], "sequence": 3}
    journal = _loaded(fake_port, tuple(records))
    result = build_sidecar(journal, make_intent(journal))
    assert isinstance(result, ObsEventSidecarV12)
    assert result.schema_version == "1.2"
    scenes = [item for item in result.events if item.type == "scene_changed"]
    assert len(scenes) == 1
    assert str(scenes[0].scene_uuid) == "444eb885-e589-4338-832c-8f5fd7eaaf41"
    assert scenes[0].scene_name == "Outro"
    assert not isinstance(scenes[0].label, str)


def test_builder_structured_failures(fake_port, monkeypatch) -> None:
    journal = loaded_legacy(fake_port)
    intent = make_intent(journal)
    records = list(journal.records)
    records[1] = {**records[1], "event_type": "scene_changed"}
    invalid_start = replace(journal, records=tuple(records))
    assert isinstance(build_sidecar(invalid_start, intent), FinalizerFailure)

    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.finalizer.sidecar_builder.map_event_to_source_frame",
        lambda **kwargs: SimpleNamespace(status="unmapped", source_frame=None),
    )
    assert isinstance(build_sidecar(journal, intent), FinalizerFailure)


def test_builder_cancellation_is_structured(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.sidecar_builder as module

    journal = loaded_legacy(fake_port)
    intent = make_intent(journal)
    token = CancellationToken()
    token.cancel()
    result = build_sidecar(journal, intent, token)
    assert isinstance(result, FinalizerFailure)
    assert result.code is FinalizerErrorCode.CANCELLED

    original_check = module._check_cancelled
    monkeypatch.setattr(module, "_check_cancelled", lambda cancellation: None)
    result = build_sidecar(journal, intent, token)
    assert isinstance(result, FinalizerFailure)
    assert result.code is FinalizerErrorCode.CANCELLED
    monkeypatch.setattr(module, "_check_cancelled", original_check)

    token = CancellationToken()
    original_validate = module.validate_sidecar

    def cancel_during_postvalidation(*args):
        token.cancel()
        return original_validate(*args)

    monkeypatch.setattr(module, "validate_sidecar", cancel_during_postvalidation)
    result = build_sidecar(journal, intent, token)
    assert isinstance(result, FinalizerFailure)
    assert result.code is FinalizerErrorCode.CANCELLED


def test_builder_consumes_phase1_calibration_sample(fake_port) -> None:
    records = list(journal_records())
    records[-1] = {**records[-1], "sequence": 3}
    records.insert(
        2,
        {
            "artifact_type": "recording_event_journal",
            "journal_schema_version": "1.0",
            "record_type": "calibration_sample",
            "sequence": 2,
            "monotonic_ns": 500_000_000,
            "output_frame_count": 30,
            "recording_paused": False,
        },
    )
    journal = _loaded(fake_port, tuple(records))
    sidecar = build_sidecar(journal, make_intent(journal))
    assert isinstance(sidecar, ObsEventSidecarV12)
    assert sidecar.clock.calibration_sample_count == 3


def test_builder_phase1_and_protection_postvalidation_failures(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.sidecar_builder as module

    journal = loaded_legacy(fake_port)
    intent = make_intent(journal)
    original_clock = module._clock_values
    monkeypatch.setattr(
        module,
        "_clock_values",
        lambda *args: FinalizerFailure(
            __import__(
                "matrix_auto_cutter.phase2.finalizer.errors",
                fromlist=["FinalizerErrorCode"],
            ).FinalizerErrorCode.JOURNAL_CORRUPT,
            __import__(
                "matrix_auto_cutter.phase2.finalizer.errors",
                fromlist=["FinalizerErrorCategory"],
            ).FinalizerErrorCategory.INTEGRITY,
            "clock",
            "bad",
        ),
    )
    assert isinstance(build_sidecar(journal, intent), FinalizerFailure)
    monkeypatch.setattr(module, "_clock_values", original_clock)
    monkeypatch.setattr(
        module,
        "validate_sidecar",
        lambda *args: SimpleNamespace(mode="no_sidecar_safe_mode", sidecar=None),
    )
    assert isinstance(build_sidecar(journal, intent), FinalizerFailure)
    monkeypatch.undo()
    monkeypatch.setattr(
        module,
        "materialize_protection",
        lambda *args: SimpleNamespace(status="failed"),
    )
    assert isinstance(build_sidecar(journal, intent), FinalizerFailure)


def test_clock_failures_and_pause_pairing(monkeypatch) -> None:
    samples = (
        CalibrationSample(monotonic_ns=0, output_frame_count=0),
        CalibrationSample(monotonic_ns=1_000_000_000, output_frame_count=60),
    )
    assert isinstance(_clock_values(samples, (), 0, 60, 60), tuple)
    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.finalizer.sidecar_builder.sample_gaps_valid",
        lambda *args: False,
    )
    assert isinstance(_clock_values(samples, (), 0, 60, 60), FinalizerFailure)
    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.finalizer.sidecar_builder.sample_gaps_valid",
        lambda *args: True,
    )
    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.finalizer.sidecar_builder.calculate_drift_ppm",
        lambda *args: Decimal("501"),
    )
    assert isinstance(_clock_values(samples, (), 0, 60, 60), FinalizerFailure)
    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.finalizer.sidecar_builder.map_qpc_frame",
        lambda *args: (_ for _ in ()).throw(ValueError("bad clock")),
    )
    assert isinstance(_clock_values(samples, (), 0, 60, 60), FinalizerFailure)

    pause = SimpleNamespace(monotonic_ns=1)
    resume = SimpleNamespace(monotonic_ns=2)
    assert PauseMeasurement(start_ns=1, end_ns=2)
    assert _pauses(()) == ((), ())
    del pause, resume


def test_state_machine_exact_transitions_and_terminals() -> None:
    machine = FinalizerStateMachine()
    assert machine.state is FinalizerStateName.DISCOVERED
    assert machine.history == (FinalizerStateName.DISCOVERED,)
    for state in (
        FinalizerStateName.VALIDATING_INPUT,
        FinalizerStateName.RESOLVING_SOURCE,
        FinalizerStateName.AWAITING_CLOSE,
        FinalizerStateName.PROBING,
        FinalizerStateName.HASHING,
        FinalizerStateName.CONFIRMING_IDENTITY,
        FinalizerStateName.PREPARING_INTENT,
        FinalizerStateName.CONSTRUCTING_SIDECAR,
        FinalizerStateName.COMMITTING_SIDECAR,
        FinalizerStateName.FINALIZED,
    ):
        machine.transition(state)
    with pytest.raises(FinalizerStateInvariantError, match="terminal"):
        machine.transition(FinalizerStateName.FAILED)

    with pytest.raises(FinalizerStateInvariantError, match="forbidden"):
        FinalizerStateMachine().transition(FinalizerStateName.PROBING)
    with pytest.raises(FinalizerStateInvariantError, match="committing_sidecar"):
        FinalizerStateMachine().transition(FinalizerStateName.FINALIZED)
    for terminal in (
        FinalizerStateName.CANCELLED,
        FinalizerStateName.FAILED,
        FinalizerStateName.QUARANTINED,
    ):
        item = FinalizerStateMachine()
        item.transition(terminal)
        assert item.state is terminal
