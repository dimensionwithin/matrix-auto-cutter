"""Öffentliche Regressionstests für die Findings des Read-only-Audits."""

from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_DNS, UUID, uuid1, uuid3, uuid4, uuid5

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError

import matrix_auto_cutter.atomic as atomic_module
import matrix_auto_cutter.journal as journal_module
import matrix_auto_cutter.models as models_module
from conftest import START_ID, event, sidecar_dict, soft_protection
from matrix_auto_cutter.atomic import ProtectionRangesDocument, write_protection_ranges
from matrix_auto_cutter.clock_bounds import MAX_DRIFT_PPM
from matrix_auto_cutter.errors import ErrorCode
from matrix_auto_cutter.journal import JournalEvent, JournalHeader, validate_journal
from matrix_auto_cutter.models import (
    CanonicalModel,
    ClockCalibration,
    Lifecycle,
    ProtectionLevel,
    SourceIdentity,
    _canonical_json_value,
    _restore_exact_decimals,
)
from matrix_auto_cutter.protection import materialize_protection, normalize_ranges
from matrix_auto_cutter.sidecar import (
    _MISSING_EVENT_VALUE,
    ObsEventSidecar,
    SidecarEvent,
    _clock_errors,
    validate_sidecar,
)
from test_journal_sidecar import journal_header, journal_record, journal_stop
from test_protection_atomic import materialized

_OPTIONAL_EVENT_FIELD_NAMES = (
    "end_mapped_source_frame",
    "pair_id",
    "scene_name",
    "label",
)


class _PayloadModel(CanonicalModel):
    payload: object


def _expected_source(raw: dict[str, Any]) -> SourceIdentity:
    return SourceIdentity.model_validate_json(json.dumps(raw["source"]))


def _clock_codes(raw: dict[str, Any]) -> set[ErrorCode]:
    return {reason.code for reason in validate_sidecar(raw, _expected_source(raw)).reasons}


def test_counter_frame_evidence_is_recomputed_and_positive_case_passes() -> None:
    inconsistent = sidecar_dict()
    inconsistent["events"].insert(1, event(str(uuid4()), "scene_changed", 100, counter=500))
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(inconsistent)

    correct = sidecar_dict()
    correct["events"].insert(1, event(str(uuid4()), "scene_changed", 500, counter=500))
    assert validate_sidecar(correct, _expected_source(correct)).mode == "validated_sidecar_1_1"


def test_recording_stop_frame_at_source_end_with_start_counter_is_rejected() -> None:
    raw = sidecar_dict()
    raw["events"][-1]["clock_sample"]["output_frame_count"] = 0
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(raw)


def test_inconsistent_qpc_fallback_and_clock_sample_ranges_are_rejected() -> None:
    raw = sidecar_dict()
    fallback = event(str(uuid4()), "scene_changed", 100, counter=None)
    fallback["clock_sample"]["monotonic_ns"] = 5_000_000_000
    raw["events"].insert(1, fallback)
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(raw)

    outside = sidecar_dict()
    sample = event(str(uuid4()), "scene_changed", 500, counter=500)
    sample["clock_sample"]["monotonic_ns"] = 20_000_000_000
    outside["events"].insert(1, sample)
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(outside)


def test_maximum_event_uncertainty_must_match_events_exactly() -> None:
    raw = sidecar_dict()
    raw["clock"]["max_event_uncertainty_ms"] = 99
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(raw)

    no_events = sidecar_dict()
    no_events["events"] = []
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(no_events)


def test_invalid_counter_span_and_unmappable_qpc_are_clock_failures() -> None:
    span = sidecar_dict()
    span["clock"]["counter_start"] = 600
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(span)

    fallback = sidecar_dict()
    item = event(str(uuid4()), "scene_changed", 100, counter=None)
    item["clock_sample"]["monotonic_ns"] = 20_000_000_000
    fallback["events"].insert(1, item)
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(fallback)


def _add_valid_resume_pause(raw: dict[str, Any], start_ns: int, end_ns: int) -> None:
    pause_id = str(uuid4())
    resume_id = str(uuid4())
    paused = event(pause_id, "recording_paused", 300, protection=soft_protection(), counter=300)
    resumed = event(resume_id, "recording_resumed", 300, protection=soft_protection(), counter=300)
    paused["clock_sample"]["monotonic_ns"] = start_ns
    resumed["clock_sample"]["monotonic_ns"] = end_ns
    raw["events"][1:1] = [paused, resumed]
    raw["pause_intervals"] = [
        {
            "pause_event_id": pause_id,
            "close_event_id": resume_id,
            "end_reason": "resumed",
            "pause_monotonic_ns": start_ns,
            "end_monotonic_ns": end_ns,
            "mapped_source_frame_before": 300,
            "mapped_source_frame_after": 300,
        }
    ]


def test_consumer_accepts_a_declared_drift_it_cannot_recompute() -> None:
    """`drift_ppm` ist eine deklarierte Kennzahl; der Verbraucher prüft nur den Bereich.

    Die Steigung über die Kalibrierreihe ist aus dem Sidecar nicht nachrechenbar,
    weil das Sidecar nur `calibration_sample_count` trägt, nicht die Reihe. Die
    frühere Nachrechnung aus Start- und Stop-Event maß nicht die Uhr, sondern den
    Interleaver-Rückstand im Moment des Stops: Aufnahme 89c344e6 vom 07.08.2026
    scheiterte daran mit 1257 ppm, obwohl die Uhr über 411 s exakt lief.
    Behandelt wird `drift_ppm` damit wie `max_calibration_residual_ms`, das seit
    jeher deklariert und nie nachgerechnet wird.
    """
    raw = sidecar_dict()
    raw["events"][-1]["clock_sample"]["monotonic_ns"] = 100_000_000_000
    raw["clock"]["drift_ppm"] = 0
    assert validate_sidecar(raw, _expected_source(raw)).mode == "validated_sidecar_1_1"

    out_of_range = sidecar_dict()
    out_of_range["clock"]["drift_ppm"] = float(MAX_DRIFT_PPM) + 0.0001
    assert validate_sidecar(out_of_range, _expected_source(out_of_range)).mode != (
        "validated_sidecar_1_1"
    )


def test_pause_interval_survives_validation_and_json_roundtrip() -> None:
    valid = sidecar_dict()
    _add_valid_resume_pause(valid, 5_000_000_000, 7_000_000_000)
    valid["events"][-1]["clock_sample"]["monotonic_ns"] = 12_000_000_000
    validated = validate_sidecar(valid, _expected_source(valid))
    assert validated.mode == "validated_sidecar_1_1"
    assert validated.sidecar is not None
    assert len(validated.sidecar.pause_intervals) == 1
    roundtrip_payload = json.loads(validated.sidecar.model_dump_json())
    assert (
        validate_sidecar(roundtrip_payload, _expected_source(roundtrip_payload)).mode
        == "validated_sidecar_1_1"
    )


def test_overlapping_pause_intervals_stay_rejected() -> None:
    raw = sidecar_dict()
    _add_valid_resume_pause(raw, 5_000_000_000, 7_000_000_000)
    raw["pause_intervals"].append(dict(raw["pause_intervals"][0]))
    assert _clock_codes(raw)


def test_stop_while_paused_and_nonpositive_active_qpc_duration() -> None:
    stopped = sidecar_dict()
    pause_id = str(uuid4())
    paused = event(pause_id, "recording_paused", 600, protection=soft_protection(), counter=600)
    paused["clock_sample"]["monotonic_ns"] = 10_000_000_000
    stopped["events"][-1]["clock_sample"]["monotonic_ns"] = 12_000_000_000
    stopped["events"][1:1] = [paused]
    stopped["pause_intervals"] = [
        {
            "pause_event_id": pause_id,
            "close_event_id": stopped["events"][-1]["event_id"],
            "end_reason": "recording_stopped_while_paused",
            "pause_monotonic_ns": 10_000_000_000,
            "end_monotonic_ns": 12_000_000_000,
            "mapped_source_frame_before": 600,
            "mapped_source_frame_after": 600,
        }
    ]
    stopped["clock"]["drift_ppm"] = 0
    assert validate_sidecar(stopped, _expected_source(stopped)).mode == "validated_sidecar_1_1"

    invalid = sidecar_dict()
    invalid["events"][-1]["clock_sample"]["monotonic_ns"] = 0
    invalid["clock"]["drift_ppm"] = 0
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_codes(invalid)


@given(st.integers(min_value=0, max_value=600))
def test_counter_cross_field_identity_property(counter: int) -> None:
    raw = sidecar_dict()
    raw["events"].insert(1, event(str(uuid4()), "scene_changed", counter, counter=counter))
    assert validate_sidecar(raw, _expected_source(raw)).mode == "validated_sidecar_1_1"


@given(
    st.integers(min_value=10, max_value=100),
    st.integers(min_value=0, max_value=500),
    st.booleans(),
    st.integers(min_value=0, max_value=2_000_000_000),
)
def test_joint_qpc_counter_and_pause_property(
    seconds: int, drift_ppm: int, slower_qpc: bool, pause_ns: int
) -> None:
    # `drift_ppm` steuert hier nur noch, wie weit QPC und Counter auseinander
    # gelegt werden. Die Zahl selbst wird vom Verbraucher nicht mehr
    # nachgerechnet; geprüft bleiben Counterspanne, Dauer, Pausenabzug und die
    # QPC-Reichweite der Events.
    raw = sidecar_dict()
    frames = seconds * 60
    expected_active_ns = seconds * 1_000_000_000
    offset_ns = expected_active_ns * drift_ppm // 1_000_000
    active_ns = expected_active_ns + offset_ns if slower_qpc else expected_active_ns - offset_ns
    raw["source"]["duration_ms"] = seconds * 1000
    raw["source"]["video_frame_count"] = frames
    raw["clock"]["counter_end"] = frames
    raw["events"][-1]["mapped_source_frame"] = frames
    raw["events"][-1]["clock_sample"]["output_frame_count"] = frames
    raw["events"][-1]["clock_sample"]["monotonic_ns"] = active_ns + pause_ns
    raw["clock"]["drift_ppm"] = 0
    if pause_ns:
        pause_id = str(uuid4())
        resume_id = str(uuid4())
        middle_frame = frames // 2
        pause_start = active_ns // 2
        pause_end = pause_start + pause_ns
        paused = event(
            pause_id,
            "recording_paused",
            middle_frame,
            protection=soft_protection(),
            counter=middle_frame,
        )
        resumed = event(
            resume_id,
            "recording_resumed",
            middle_frame,
            protection=soft_protection(),
            counter=middle_frame,
        )
        paused["clock_sample"]["monotonic_ns"] = pause_start
        resumed["clock_sample"]["monotonic_ns"] = pause_end
        raw["events"][1:1] = [paused, resumed]
        raw["pause_intervals"] = [
            {
                "pause_event_id": pause_id,
                "close_event_id": resume_id,
                "end_reason": "resumed",
                "pause_monotonic_ns": pause_start,
                "end_monotonic_ns": pause_end,
                "mapped_source_frame_before": middle_frame,
                "mapped_source_frame_after": middle_frame,
            }
        ]
    assert validate_sidecar(raw, _expected_source(raw)).mode == "validated_sidecar_1_1"


@pytest.mark.parametrize(("start", "end"), [(500, 100), (500, 500)])
def test_complete_non_forward_pairs_are_rejected_by_consumer_and_resolver(
    start: int, end: int
) -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    raw["events"][1:1] = [
        event(str(uuid4()), "intro_started", start, pair_id=pair_id),
        event(str(uuid4()), "intro_ended", end, pair_id=pair_id),
    ]
    assert ErrorCode.SIDECAR_EVENT_PAIRS in _clock_or_pair_codes(raw)
    parsed = ObsEventSidecar.model_validate_json(json.dumps(raw))
    resolution = materialize_protection(parsed)
    assert resolution.status == "rejected"
    assert resolution.errors[0].code == ErrorCode.SIDECAR_EVENT_PAIRS


def _clock_or_pair_codes(raw: dict[str, Any]) -> set[ErrorCode]:
    return {reason.code for reason in validate_sidecar(raw, _expected_source(raw)).reasons}


@pytest.mark.parametrize(("start", "end"), [(100, 500), (100, None), (None, 500)])
def test_forward_and_incomplete_pairs_remain_conservatively_valid(
    start: int | None, end: int | None
) -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    additions = []
    if start is not None:
        additions.append(event(str(uuid4()), "intro_started", start, pair_id=pair_id))
    if end is not None:
        additions.append(event(str(uuid4()), "intro_ended", end, pair_id=pair_id))
    raw["events"][1:1] = additions
    assert validate_sidecar(raw, _expected_source(raw)).mode == "validated_sidecar_1_1"


@pytest.mark.parametrize("duplicate_side", ["started", "ended"])
def test_multiple_pair_sides_with_same_pair_id_are_rejected(duplicate_side: str) -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    kind = f"stinger_{duplicate_side}"
    raw["events"][1:1] = [
        event(str(uuid4()), kind, 100, pair_id=pair_id),
        event(str(uuid4()), kind, 200, pair_id=pair_id),
    ]
    assert ErrorCode.SIDECAR_EVENT_PAIRS in _clock_or_pair_codes(raw)


@pytest.mark.parametrize("second_family", ["outro", "stinger"])
def test_pair_id_is_globally_unique_across_families(second_family: str) -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    raw["events"][1:1] = [
        event(str(uuid4()), "intro_started", 100, pair_id=pair_id, counter=100),
        event(str(uuid4()), "intro_ended", 200, pair_id=pair_id, counter=200),
        event(str(uuid4()), f"{second_family}_started", 300, pair_id=pair_id, counter=300),
        event(str(uuid4()), f"{second_family}_ended", 400, pair_id=pair_id, counter=400),
    ]
    consumer = validate_sidecar(raw, _expected_source(raw))
    assert consumer.mode == "no_sidecar_safe_mode"
    assert ErrorCode.SIDECAR_EVENT_PAIRS in {reason.code for reason in consumer.reasons}
    resolver = materialize_protection(ObsEventSidecar.model_validate_json(json.dumps(raw)))
    assert resolver.status == "rejected"
    assert resolver.errors[0].code == ErrorCode.SIDECAR_EVENT_PAIRS


def test_distinct_pair_ids_validate_and_all_events_are_materialized() -> None:
    raw = sidecar_dict()
    intro_pair = str(uuid4())
    outro_pair = str(uuid4())
    pair_events = [
        event(str(uuid4()), "intro_started", 100, pair_id=intro_pair, counter=100),
        event(str(uuid4()), "intro_ended", 200, pair_id=intro_pair, counter=200),
        event(str(uuid4()), "outro_started", 300, pair_id=outro_pair, counter=300),
        event(str(uuid4()), "outro_ended", 400, pair_id=outro_pair, counter=400),
    ]
    raw["events"][1:1] = pair_events
    validated = validate_sidecar(raw, _expected_source(raw))
    assert validated.mode == "validated_sidecar_1_1"
    assert validated.sidecar is not None
    resolution = materialize_protection(validated.sidecar)
    assert resolution.status == "materialized"
    materialized_ids = {
        identifier for item in resolution.ranges for identifier in item.source_event_ids
    }
    assert {UUID(item["event_id"]) for item in pair_events}.issubset(materialized_ids)


@given(
    st.sampled_from([("intro", "outro"), ("intro", "stinger"), ("outro", "stinger")]),
    st.booleans(),
)
def test_pair_id_multi_family_property(families: tuple[str, str], reuse_id: bool) -> None:
    raw = sidecar_dict()
    first_id = str(uuid4())
    second_id = first_id if reuse_id else str(uuid4())
    raw["events"][1:1] = [
        event(str(uuid4()), f"{families[0]}_started", 100, pair_id=first_id, counter=100),
        event(str(uuid4()), f"{families[0]}_ended", 200, pair_id=first_id, counter=200),
        event(str(uuid4()), f"{families[1]}_started", 300, pair_id=second_id, counter=300),
        event(str(uuid4()), f"{families[1]}_ended", 400, pair_id=second_id, counter=400),
    ]
    result = validate_sidecar(raw, _expected_source(raw))
    assert (result.mode == "no_sidecar_safe_mode") is reuse_id


def _sidecar_with_pause() -> dict[str, Any]:
    raw = sidecar_dict()
    pause_id = str(uuid4())
    resume_id = str(uuid4())
    paused = event(pause_id, "recording_paused", 300, protection=soft_protection(), counter=300)
    resumed = event(resume_id, "recording_resumed", 301, protection=soft_protection(), counter=301)
    paused["clock_sample"]["monotonic_ns"] = 5_000
    resumed["clock_sample"]["monotonic_ns"] = 8_000
    raw["events"][1:1] = [paused, resumed]
    raw["pause_intervals"] = [
        {
            "pause_event_id": pause_id,
            "close_event_id": resume_id,
            "end_reason": "resumed",
            "pause_monotonic_ns": 5_000,
            "end_monotonic_ns": 8_000,
            "mapped_source_frame_before": 300,
            "mapped_source_frame_after": 301,
        }
    ]
    return raw


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pause_monotonic_ns", 4_999),
        ("end_monotonic_ns", 8_001),
        ("mapped_source_frame_before", 299),
        ("mapped_source_frame_after", 300),
    ],
)
def test_pause_interval_must_exactly_match_referenced_events(field: str, value: int) -> None:
    raw = _sidecar_with_pause()
    raw["pause_intervals"][0][field] = value
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in _clock_or_pair_codes(raw)


def test_duplicate_event_and_pause_references_are_rejected() -> None:
    duplicate = sidecar_dict()
    duplicate["events"].insert(1, event(START_ID, "scene_changed", 100, counter=100))
    assert ErrorCode.SIDECAR_EVENT_PAIRS in _clock_or_pair_codes(duplicate)

    repeated_reference = _sidecar_with_pause()
    repeated_reference["pause_intervals"].append(
        copy.deepcopy(repeated_reference["pause_intervals"][0])
    )
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in _clock_or_pair_codes(repeated_reference)


def test_pause_semantic_event_is_structurally_excluded_from_protection() -> None:
    raw = _sidecar_with_pause()
    item = event(str(uuid4()), "scene_changed", 300, counter=300)
    item["clock_sample"]["monotonic_ns"] = 6_000
    raw["events"].insert(2, item)
    result = validate_sidecar(raw, _expected_source(raw))
    assert result.mode == "no_sidecar_safe_mode"
    assert result.sidecar is None
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {reason.code for reason in result.reasons}


def _journal_event(
    sequence: int, ns: int, paused: bool, event_id: str | None = None
) -> dict[str, Any]:
    return {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "event",
        "sequence": sequence,
        "event_id": event_id or str(uuid4()),
        "event_type": "scene_changed",
        "monotonic_ns": ns,
        "output_frame_count": sequence,
        "recording_paused": paused,
    }


def test_journal_stop_is_unique_and_terminal() -> None:
    two_stops = [journal_header(), journal_stop(1), journal_stop(2)]
    after_stop = [
        journal_header(),
        journal_stop(1),
        journal_record("calibration_sample", 2, 10_000_000_001, 600) | {"recording_paused": False},
    ]
    for records in (two_stops, after_stop):
        result = validate_journal(records)
        assert not result.valid
        assert ErrorCode.JOURNAL_SEQUENCE in {error.code for error in result.errors}


def _path_snapshot(sequence: int, path: str) -> dict[str, Any]:
    return journal_record("path_snapshot", sequence, sequence * 100, sequence) | {
        "output_path": path
    }


def _split_status(sequence: int) -> dict[str, Any]:
    return journal_record("split_status", sequence, sequence * 100, sequence) | {
        "split_requested": False,
        "file_splitting_detected": True,
    }


def test_journal_path_change_requires_split_and_split_blocks_finalization() -> None:
    header = journal_header()
    header["initial_output_path"] = "F:\\Video\\A.mkv"
    unmarked = [header, _path_snapshot(1, "F:\\Video\\B.mkv"), journal_stop(2)]
    unmarked[-1]["last_recording_path"] = "F:\\Video\\B.mkv"
    assert ErrorCode.JOURNAL_OUTPUT_FAILURE in {
        error.code for error in validate_journal(unmarked).errors
    }

    marked = [header, _path_snapshot(1, "F:\\Video\\B.mkv"), _split_status(2), journal_stop(3)]
    marked[-1]["last_recording_path"] = "F:\\Video\\B.mkv"
    result = validate_journal(marked)
    assert not result.valid
    assert ErrorCode.JOURNAL_OUTPUT_FAILURE in {error.code for error in result.errors}


def test_journal_reconstructs_pause_flags_and_event_ids() -> None:
    active_claims_pause = [journal_header(), _journal_event(1, 100, True), journal_stop(2)]
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {
        error.code for error in validate_journal(active_claims_pause).errors
    }

    identifier = str(uuid4())
    wrong_during_pause = [
        journal_header(),
        journal_record("pause", 1, 100, 1),
        _journal_event(2, 200, False),
        journal_record("resume", 3, 300, 2),
        journal_stop(4),
    ]
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {
        error.code for error in validate_journal(wrong_during_pause).errors
    }
    duplicates = [
        journal_header(),
        _journal_event(1, 100, False, identifier),
        _journal_event(2, 200, False, identifier),
        journal_stop(3),
    ]
    assert ErrorCode.JOURNAL_SEQUENCE in {
        error.code for error in validate_journal(duplicates).errors
    }

    sample_during_pause = [
        journal_header(),
        journal_record("pause", 1, 100, 1),
        journal_record("calibration_sample", 2, 200, 2) | {"recording_paused": False},
        journal_record("resume", 3, 300, 2),
        journal_stop(4),
    ]
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {
        error.code for error in validate_journal(sample_during_pause).errors
    }

    moving_snapshot = _path_snapshot(2, "F:\\Video\\aufnahme.mkv")
    moving_snapshot["output_frame_count"] = 5
    moving_snapshot["recording_paused"] = True
    counter_moves_during_pause = [
        journal_header(),
        journal_record("pause", 1, 100, 1),
        moving_snapshot,
        journal_record("resume", 3, 300, 5),
        journal_stop(4),
    ]
    assert validate_journal(counter_moves_during_pause).valid


@given(st.integers(min_value=0, max_value=20))
def test_journal_sequence_property(sample_count: int) -> None:
    records = [journal_header()]
    records.extend(
        journal_record("calibration_sample", index, index * 100, index)
        | {"recording_paused": False}
        for index in range(1, sample_count + 1)
    )
    records.append(journal_stop(sample_count + 1))
    assert validate_journal(records).valid


@given(
    st.sampled_from(["calibration", "path", "split", "output_error", "recovery"]),
    st.booleans(),
)
def test_journal_record_and_lifecycle_property(record_kind: str, terminal_stop: bool) -> None:
    if record_kind == "calibration":
        middle = journal_record("calibration_sample", 1, 100, 1) | {"recording_paused": False}
    elif record_kind == "path":
        middle = _path_snapshot(1, "F:\\Video\\aufnahme.mkv")
    elif record_kind == "split":
        middle = _split_status(1)
    elif record_kind == "output_error":
        middle = journal_record("output_error", 1, 100, 1) | {
            "output_result": "failure",
            "diagnostic": "encoder",
        }
    else:
        middle = {
            "artifact_type": "recording_event_journal",
            "journal_schema_version": "1.0",
            "record_type": "recovery",
            "sequence": 1,
            "lifecycle_status": "aborted",
            "diagnostic": "crash",
        }
    records = [journal_header(), middle]
    if terminal_stop:
        records.append(journal_stop(2))
    result = validate_journal(records)
    expected_valid = terminal_stop and record_kind in {"calibration", "path"}
    assert result.valid is expected_valid


def test_source_binding_is_part_of_complete_identity() -> None:
    raw = sidecar_dict()
    raw["source"]["binding"] = "manual_remux"
    expected_data = copy.deepcopy(raw["source"])
    expected_data["binding"] = "direct_mp4"
    expected = SourceIdentity.model_validate_json(json.dumps(expected_data))
    assert ErrorCode.SIDECAR_IDENTITY in {
        reason.code for reason in validate_sidecar(raw, expected).reasons
    }


@pytest.mark.parametrize(
    ("location", "field"),
    [
        ("clock", "drift_ppm"),
        ("clock", "max_calibration_residual_ms"),
        ("clock", "max_event_uncertainty_ms"),
        ("event", "uncertainty_ms"),
    ],
)
def test_decimal_numeric_strings_are_rejected_from_json_and_python(
    location: str, field: str
) -> None:
    raw = sidecar_dict()
    target = raw["clock"] if location == "clock" else raw["events"][0]
    target[field] = "10"
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate(raw)


def test_consumer_classifies_schema_failures_by_contract_group() -> None:
    clock = sidecar_dict()
    clock["clock"]["drift_ppm"] = "10"
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_or_pair_codes(clock)

    event_clock = sidecar_dict()
    event_clock["events"][0]["uncertainty_ms"] = "10"
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in _clock_or_pair_codes(event_clock)

    pause = _sidecar_with_pause()
    pause["pause_intervals"][0]["mapped_source_frame_after"] = 299
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in _clock_or_pair_codes(pause)

    non_json = sidecar_dict()
    non_json["finalization"]["warnings"] = {"not-json"}
    result = validate_sidecar(non_json, _expected_source(non_json))
    assert result.reasons[0].code == ErrorCode.SIDECAR_POLICY


@pytest.mark.parametrize(
    "failure", [TypeError("programming defect"), RuntimeError("programming defect")]
)
def test_sidecar_internal_programming_errors_propagate(
    monkeypatch: pytest.MonkeyPatch, failure: TypeError | RuntimeError
) -> None:
    raw = sidecar_dict()
    expected = _expected_source(raw)

    def fail_validation(_payload: str) -> ObsEventSidecar:
        raise failure

    monkeypatch.setattr(ObsEventSidecar, "model_validate_json", fail_validation)
    with pytest.raises(type(failure), match="programming defect"):
        validate_sidecar(raw, expected)


def test_internal_json_serialization_type_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = sidecar_dict()
    expected = _expected_source(raw)

    def fail_serialization(*_args: object, **_kwargs: object) -> str:
        raise TypeError("programming defect")

    monkeypatch.setattr(models_module.json, "dumps", fail_serialization)
    with pytest.raises(TypeError, match="programming defect"):
        validate_sidecar(raw, expected)
    with pytest.raises(TypeError, match="programming defect"):
        validate_journal([journal_header(), journal_stop(1)])


def test_journal_internal_programming_errors_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_record(_raw: object) -> object:
        raise RuntimeError("programming defect")

    monkeypatch.setattr(journal_module, "_parse_record", fail_record)
    with pytest.raises(RuntimeError, match="programming defect"):
        validate_journal([journal_header(), journal_stop(1)])


def test_non_json_input_remains_a_structured_safe_mode() -> None:
    raw = sidecar_dict()
    raw["finalization"]["warnings"] = {"not-json"}
    result = validate_sidecar(raw, _expected_source(raw))
    assert result.mode == "no_sidecar_safe_mode"
    assert result.reasons[0].code == ErrorCode.SIDECAR_POLICY


def test_cyclic_and_non_string_key_json_inputs_are_structured() -> None:
    for invalid_kind in ("mapping_cycle", "list_cycle", "non_string_key"):
        raw = sidecar_dict()
        expected = _expected_source(raw)
        if invalid_kind == "mapping_cycle":
            raw["finalization"]["cycle"] = raw
        elif invalid_kind == "list_cycle":
            cyclic: list[object] = []
            cyclic.append(cyclic)
            raw["finalization"]["warnings"] = cyclic
        else:
            raw["finalization"][1] = "invalid"
        result = validate_sidecar(raw, expected)
        assert result.mode == "no_sidecar_safe_mode"
        assert result.reasons[0].code == ErrorCode.SIDECAR_POLICY


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), float("-inf")])
def test_decimal_booleans_and_nonfinite_values_are_rejected(invalid: bool | float) -> None:
    raw = sidecar_dict()
    raw["clock"]["drift_ppm"] = invalid
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))


def test_sidecar_filename_timezone_extra_and_frozen_contracts() -> None:
    raw = sidecar_dict()
    raw["source"]["file_name"] = "aufnahme.MP4"
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))
    raw = sidecar_dict()
    raw["lifecycle"]["finalized_at"] = "2026-07-12T16:00:00"
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))
    raw = sidecar_dict()
    raw["clock"]["unknown"] = 1
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))
    parsed = ObsEventSidecar.model_validate_json(json.dumps(sidecar_dict()))
    with pytest.raises(ValidationError):
        parsed.schema_version = "1.1"
    assert ObsEventSidecar.model_validate(parsed.model_dump()).clock.drift_ppm == Decimal("0.02")


@pytest.mark.parametrize(
    "path",
    [
        ("source", "fps_num"),
        ("source", "fps_den"),
        ("events", 0, "protection", "policy", "allows_global_mastering"),
        ("events", 0, "clock_sample", "output_frame_count"),
    ],
)
def test_all_canonical_required_fields_must_be_present(path: tuple[str | int, ...]) -> None:
    raw = sidecar_dict()
    expected = _expected_source(raw)
    target: object = raw
    for component in path[:-1]:
        if isinstance(component, int):
            assert isinstance(target, list)
            target = target[component]
        else:
            assert isinstance(target, dict)
            target = target[component]
    last = path[-1]
    if isinstance(last, int):
        assert isinstance(target, list)
        del target[last]
    else:
        assert isinstance(target, dict)
        del target[last]
    result = validate_sidecar(raw, expected)
    assert result.mode == "no_sidecar_safe_mode"
    assert result.sidecar is None


def test_qpc_output_frame_count_requires_explicit_null() -> None:
    raw = sidecar_dict()
    fallback = event(str(uuid4()), "scene_changed", 300, counter=None)
    fallback["clock_sample"]["monotonic_ns"] = 5_000_000_100
    raw["events"].insert(1, fallback)
    assert validate_sidecar(raw, _expected_source(raw)).mode == "validated_sidecar_1_1"

    del fallback["clock_sample"]["output_frame_count"]
    result = validate_sidecar(raw, _expected_source(raw))
    assert result.mode == "no_sidecar_safe_mode"
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in {reason.code for reason in result.reasons}


def _event_with_valid_optional_field(field: str) -> dict[str, Any]:
    if field == "end_mapped_source_frame":
        return event(
            str(uuid4()),
            "manual_protection",
            300,
            end_frame=320,
            counter=300,
        )
    if field == "pair_id":
        return event(
            str(uuid4()),
            "intro_started",
            300,
            pair_id=str(uuid4()),
            counter=300,
        )
    item = event(
        str(uuid4()),
        "scene_changed" if field == "scene_name" else "manual_protection",
        300,
        counter=300,
    )
    item[field] = "Chart" if field == "scene_name" else "Wichtige Stelle"
    return item


@pytest.mark.parametrize(
    "field",
    ["end_mapped_source_frame", "pair_id", "scene_name", "label"],
)
def test_optional_event_fields_are_omittable_but_not_nullable(field: str) -> None:
    missing = sidecar_dict()
    neutral = event(str(uuid4()), "scene_changed", 300, counter=300)
    assert field not in neutral
    missing["events"].insert(1, neutral)
    assert validate_sidecar(missing, _expected_source(missing)).mode == "validated_sidecar_1_1"

    present = sidecar_dict()
    present["events"].insert(1, _event_with_valid_optional_field(field))
    assert validate_sidecar(present, _expected_source(present)).mode == "validated_sidecar_1_1"

    explicit_null = sidecar_dict()
    nullable = _event_with_valid_optional_field(field)
    nullable[field] = None
    explicit_null["events"].insert(1, nullable)
    result = validate_sidecar(explicit_null, _expected_source(explicit_null))
    assert result.mode == "no_sidecar_safe_mode"
    assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in result.reasons}


def test_all_optional_event_fields_null_or_wrong_type_are_safe_mode() -> None:
    all_null = sidecar_dict()
    item = event(str(uuid4()), "scene_changed", 300, counter=300)
    item.update(
        {
            "end_mapped_source_frame": None,
            "pair_id": None,
            "scene_name": None,
            "label": None,
        }
    )
    all_null["events"].insert(1, item)
    result = validate_sidecar(all_null, _expected_source(all_null))
    assert result.mode == "no_sidecar_safe_mode"
    assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in result.reasons}

    for field, invalid in (
        ("end_mapped_source_frame", "320"),
        ("pair_id", 42),
        ("scene_name", 42),
        ("label", 42),
    ):
        raw = sidecar_dict()
        invalid_event = _event_with_valid_optional_field(field)
        invalid_event[field] = invalid
        raw["events"].insert(1, invalid_event)
        invalid_result = validate_sidecar(raw, _expected_source(raw))
        assert invalid_result.mode == "no_sidecar_safe_mode"
        assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in invalid_result.reasons}


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("end_mapped_source_frame", 320, ErrorCode.SIDECAR_POLICY),
        ("pair_id", "6ba7b814-9dad-4b8a-92fb-2a41f5468719", ErrorCode.SIDECAR_EVENT_PAIRS),
        ("scene_name", "Falsche Szene", ErrorCode.SIDECAR_POLICY),
        ("label", "Falsches Label", ErrorCode.SIDECAR_POLICY),
    ],
)
def test_optional_event_fields_reject_valid_values_on_wrong_event_type(
    field: str, value: object, expected_code: ErrorCode
) -> None:
    raw = sidecar_dict()
    wrong_event = event(str(uuid4()), "recording_started", 0, counter=0)
    wrong_event[field] = value
    raw["events"].insert(1, wrong_event)
    result = validate_sidecar(raw, _expected_source(raw))
    assert result.mode == "no_sidecar_safe_mode"
    assert expected_code in {reason.code for reason in result.reasons}


def test_optional_event_field_serialization_omits_missing_and_preserves_values() -> None:
    raw = sidecar_dict()
    neutral = event(str(uuid4()), "scene_changed", 100, counter=100)
    manual = _event_with_valid_optional_field("end_mapped_source_frame")
    manual["label"] = "Wichtige Stelle"
    scene = _event_with_valid_optional_field("scene_name")
    paired = _event_with_valid_optional_field("pair_id")
    raw["events"][1:1] = [neutral, manual, scene, paired]
    validated = validate_sidecar(raw, _expected_source(raw))
    assert validated.mode == "validated_sidecar_1_1"
    assert validated.sidecar is not None

    serialized = json.loads(validated.sidecar.model_dump_json())
    serialized_events = serialized["events"]
    serialized_neutral = next(
        item for item in serialized_events if item["event_id"] == neutral["event_id"]
    )
    assert all(field not in serialized_neutral for field in _OPTIONAL_EVENT_FIELD_NAMES)
    serialized_manual = next(
        item for item in serialized_events if item["event_id"] == manual["event_id"]
    )
    assert serialized_manual["end_mapped_source_frame"] == 320
    assert serialized_manual["label"] == "Wichtige Stelle"
    serialized_scene = next(
        item for item in serialized_events if item["event_id"] == scene["event_id"]
    )
    assert serialized_scene["scene_name"] == "Chart"
    serialized_pair = next(
        item for item in serialized_events if item["event_id"] == paired["event_id"]
    )
    assert serialized_pair["pair_id"] == paired["pair_id"]

    first_json = validated.sidecar.model_dump_json()
    roundtrip = ObsEventSidecar.model_validate_json(first_json)
    assert roundtrip.model_dump_json() == first_json
    assert "null" not in json.dumps(serialized_neutral)


_IGNORED_SCHEMA_METADATA = {
    "title",
    "description",
    "$comment",
    "examples",
    "deprecated",
    "readOnly",
    "writeOnly",
}
_SCHEMA_COMBINATORS = {"anyOf", "oneOf", "allOf"}


def _local_schema_target(reference: object, root: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(reference, str), "$ref muss ein String sein."
    prefix = "#/$defs/"
    assert reference.startswith(prefix), f"Nicht unterstützter lokaler $ref: {reference}"
    definitions = root.get("$defs")
    assert isinstance(definitions, dict), "Lokaler $ref benötigt $defs."
    name = reference.removeprefix(prefix)
    assert name in definitions, f"Nicht auflösbarer lokaler $ref: {reference}"
    target = definitions[name]
    assert isinstance(target, dict), f"$ref-Ziel muss ein Schemaobjekt sein: {reference}"
    return target


def _resolved_schema_node(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    resolved = node
    seen: set[str] = set()
    while "$ref" in resolved:
        reference = resolved["$ref"]
        assert isinstance(reference, str), "$ref muss ein String sein."
        assert reference not in seen, f"Zyklische reine $ref-Kette: {reference}"
        seen.add(reference)
        target = _local_schema_target(reference, root)
        resolved = {**target, **{key: value for key, value in resolved.items() if key != "$ref"}}
    return resolved


def _collapse_simple_type_union(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "anyOf" not in node:
        return node
    choices = [_resolved_schema_node(choice, root) for choice in node["anyOf"]]
    if not choices or any(not isinstance(choice.get("type"), str) for choice in choices):
        return node
    forbidden = {
        "properties",
        "required",
        "additionalProperties",
        "items",
        "prefixItems",
        "anyOf",
        "oneOf",
        "allOf",
        "not",
    }
    if any(forbidden.intersection(choice) for choice in choices):
        return node
    collapsed = {key: value for key, value in node.items() if key != "anyOf"}
    collapsed["type"] = sorted(choice["type"] for choice in choices)
    for choice in choices:
        for key, value in choice.items():
            if key == "type" or key in _IGNORED_SCHEMA_METADATA:
                continue
            if key in collapsed and collapsed[key] != value:
                return node
            collapsed[key] = value
    return collapsed


def _json_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


class _SchemaPairState:
    def __init__(self) -> None:
        self.active: set[tuple[str, str]] = set()
        self.completed: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}


def _schema_node_token(node: dict[str, Any]) -> str:
    reference = node.get("$ref")
    if isinstance(reference, str):
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return reference + "|" + json.dumps(siblings, sort_keys=True)
    return f"inline:{id(node)}"


def _resolve_schema_node_once(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    if "$ref" not in node:
        return node
    target = _local_schema_target(node["$ref"], root)
    return {**target, **{key: value for key, value in node.items() if key != "$ref"}}


def _normalize_schema_scalar(key: str, value: Any) -> Any:
    if key in {"required", "type"} and isinstance(value, list):
        return sorted(value)
    if key == "enum":
        return sorted(value, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _normalized_schema_pair(
    canonical: dict[str, Any],
    exported: dict[str, Any],
    canonical_root: dict[str, Any],
    exported_root: dict[str, Any],
    state: _SchemaPairState,
) -> tuple[dict[str, Any], dict[str, Any]]:
    pair = (_schema_node_token(canonical), _schema_node_token(exported))
    if pair in state.active:
        marker = {"$recursivePair": True}
        return marker, marker.copy()
    if pair in state.completed:
        return state.completed[pair]
    state.active.add(pair)
    try:
        canonical_node = _resolve_schema_node_once(canonical, canonical_root)
        exported_node = _resolve_schema_node_once(exported, exported_root)
        if "$ref" in canonical_node or "$ref" in exported_node:
            result = _normalized_schema_pair(
                canonical_node,
                exported_node,
                canonical_root,
                exported_root,
                state,
            )
            state.completed[pair] = result
            return result
        canonical_node = _collapse_simple_type_union(canonical_node, canonical_root)
        exported_node = _collapse_simple_type_union(exported_node, exported_root)
        canonical_normalized: dict[str, Any] = {}
        exported_normalized: dict[str, Any] = {}
        ignored = _IGNORED_SCHEMA_METADATA | {"$defs", "$schema", "$id"}
        keys = (set(canonical_node) | set(exported_node)) - ignored
        for key in sorted(keys):
            if key not in canonical_node:
                exported_normalized[key] = exported_node[key]
                continue
            if key not in exported_node:
                canonical_normalized[key] = canonical_node[key]
                continue
            canonical_value = canonical_node[key]
            exported_value = exported_node[key]
            if (
                key == "properties"
                and isinstance(canonical_value, dict)
                and isinstance(exported_value, dict)
            ):
                canonical_properties: dict[str, Any] = {}
                exported_properties: dict[str, Any] = {}
                property_names = set(canonical_value) | set(exported_value)
                for name in sorted(property_names):
                    if name not in canonical_value:
                        exported_properties[name] = exported_value[name]
                    elif name not in exported_value:
                        canonical_properties[name] = canonical_value[name]
                    else:
                        left, right = _normalized_schema_pair(
                            canonical_value[name],
                            exported_value[name],
                            canonical_root,
                            exported_root,
                            state,
                        )
                        canonical_properties[name] = left
                        exported_properties[name] = right
                canonical_normalized[key] = canonical_properties
                exported_normalized[key] = exported_properties
            elif (
                key in {"items", "not", "additionalProperties"}
                and isinstance(canonical_value, dict)
                and isinstance(exported_value, dict)
            ):
                left, right = _normalized_schema_pair(
                    canonical_value,
                    exported_value,
                    canonical_root,
                    exported_root,
                    state,
                )
                canonical_normalized[key] = left
                exported_normalized[key] = right
            elif key == "prefixItems" or key in _SCHEMA_COMBINATORS:
                canonical_items: list[Any] = []
                exported_items: list[Any] = []
                for index in range(max(len(canonical_value), len(exported_value))):
                    if index >= len(canonical_value):
                        exported_items.append(exported_value[index])
                    elif index >= len(exported_value):
                        canonical_items.append(canonical_value[index])
                    else:
                        left, right = _normalized_schema_pair(
                            canonical_value[index],
                            exported_value[index],
                            canonical_root,
                            exported_root,
                            state,
                        )
                        canonical_items.append(left)
                        exported_items.append(right)
                if key in _SCHEMA_COMBINATORS:
                    canonical_items.sort(key=lambda item: json.dumps(item, sort_keys=True))
                    exported_items.sort(key=lambda item: json.dumps(item, sort_keys=True))
                canonical_normalized[key] = canonical_items
                exported_normalized[key] = exported_items
            else:
                canonical_normalized[key] = _normalize_schema_scalar(key, canonical_value)
                exported_normalized[key] = _normalize_schema_scalar(key, exported_value)
        for normalized in (canonical_normalized, exported_normalized):
            explicit_type = normalized.get("type")
            if "const" in normalized and explicit_type == _json_type(normalized["const"]):
                normalized.pop("type")
            if "enum" in normalized and isinstance(explicit_type, str):
                enum_types = {_json_type(item) for item in normalized["enum"]}
                if enum_types == {explicit_type}:
                    normalized.pop("type")
        result = canonical_normalized, exported_normalized
        state.completed[pair] = result
        return result
    finally:
        state.active.remove(pair)


def _normalized_schema(node: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    normalized, _ = _normalized_schema_pair(node, node, root, root, _SchemaPairState())
    return normalized


def _assert_schema_contract(
    canonical: dict[str, Any],
    exported: dict[str, Any],
    root: dict[str, Any],
) -> None:
    canonical_normalized, exported_normalized = _normalized_schema_pair(
        canonical,
        exported,
        canonical,
        root,
        _SchemaPairState(),
    )
    assert exported_normalized == canonical_normalized


def test_exported_sidecar_schema_contains_canonical_numeric_and_string_constraints() -> None:
    schema = ObsEventSidecar.model_json_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "https://dimensionwithin.local/schemas/obs-events-1.1.json"
    source = schema["$defs"]["SourceIdentity"]["properties"]
    lifecycle = schema["$defs"]["Lifecycle"]["properties"]
    clock = schema["$defs"]["ClockCalibration"]["properties"]
    sidecar_event = schema["$defs"]["SidecarEvent"]["properties"]
    assert source["file_name"]["pattern"] == r"^[^/\\]+\.mp4$"
    assert lifecycle["finalized_at"]["format"] == "date-time"
    assert clock["drift_ppm"] == {
        "maximum": int(MAX_DRIFT_PPM),
        "minimum": 0,
        "title": "Drift Ppm",
        "type": "number",
    }
    assert clock["max_calibration_residual_ms"]["maximum"] == 50
    assert clock["max_event_uncertainty_ms"]["maximum"] == 250
    assert sidecar_event["uncertainty_ms"]["maximum"] == 250
    assert sidecar_event["uncertainty_ms"]["type"] == "number"


def test_exported_schema_recursively_matches_canonical_sidecar_contract() -> None:
    architecture = Path("docs/matrix-auto-cutter-architecture-plan-v0.2.md").read_text(
        encoding="utf-8"
    )
    schema_section = architecture.split("### 8.3 Kanonisches Sidecar-JSON-Schema 1.1", 1)[1]
    canonical = json.loads(schema_section.split("```json", 1)[1].split("```", 1)[0])
    exported = ObsEventSidecar.model_json_schema()
    _assert_schema_contract(canonical, exported, exported)
    sha_schema = exported["$defs"]["SourceIdentity"]["properties"]["sha256"]
    assert sha_schema["pattern"] == r"^[a-f0-9]{64}$"


def test_optional_event_fields_export_exact_non_nullable_schema() -> None:
    exported_event = ObsEventSidecar.model_json_schema()["$defs"]["SidecarEvent"]
    expected_types = {
        "end_mapped_source_frame": "integer",
        "pair_id": "string",
        "scene_name": "string",
        "label": "string",
    }
    required = set(exported_event["required"])
    for name, expected_type in expected_types.items():
        property_schema = exported_event["properties"][name]
        assert name not in required
        assert property_schema["type"] == expected_type
        assert "anyOf" not in property_schema
        assert "null" not in property_schema.get("type", [])
        assert "default" not in property_schema


@pytest.mark.parametrize(
    "mutation",
    [
        "additional_null_type",
        "wrong_format",
        "wrong_min_length",
        "additional_maximum",
        "removed_maximum",
        "additional_enum",
        "removed_required",
        "additional_property",
        "removed_property",
        "additional_properties_true",
        "wrong_pattern",
        "additional_min_items",
        "additional_max_items",
        "additional_prefix_items",
        "additional_one_of",
        "additional_all_of",
        "additional_not",
    ],
)
def test_recursive_schema_walker_rejects_bidirectional_mutations(mutation: str) -> None:
    architecture = Path("docs/matrix-auto-cutter-architecture-plan-v0.2.md").read_text(
        encoding="utf-8"
    )
    schema_section = architecture.split("### 8.3 Kanonisches Sidecar-JSON-Schema 1.1", 1)[1]
    canonical = json.loads(schema_section.split("```json", 1)[1].split("```", 1)[0])
    exported = ObsEventSidecar.model_json_schema()
    event = exported["$defs"]["SidecarEvent"]
    event_properties = event["properties"]
    if mutation == "additional_null_type":
        original = event_properties["label"]
        event_properties["label"] = {"anyOf": [original, {"type": "null"}]}
    elif mutation == "wrong_format":
        event_properties["pair_id"]["format"] = "totally-wrong"
    elif mutation == "wrong_min_length":
        exported["$defs"]["Producer"]["properties"]["version"]["minLength"] = 999
    elif mutation == "additional_maximum":
        event_properties["mapped_source_frame"]["maximum"] = 999
    elif mutation == "removed_maximum":
        exported["$defs"]["ClockCalibration"]["properties"]["drift_ppm"].pop("maximum")
    elif mutation == "additional_enum":
        exported["$defs"]["SourceIdentity"]["properties"]["file_name"]["enum"] = ["aufnahme.mp4"]
    elif mutation == "removed_required":
        exported["required"].remove("finalization")
    elif mutation == "additional_property":
        exported["properties"]["unexpected"] = {"type": "string"}
    elif mutation == "removed_property":
        exported["properties"].pop("producer")
    elif mutation == "additional_properties_true":
        exported["additionalProperties"] = True
    elif mutation == "wrong_pattern":
        exported["$defs"]["SourceIdentity"]["properties"]["file_name"]["pattern"] = ".*"
    elif mutation == "additional_min_items":
        exported["properties"]["events"]["minItems"] = 1
    elif mutation == "additional_max_items":
        exported["properties"]["events"]["maxItems"] = 999
    elif mutation == "additional_prefix_items":
        exported["properties"]["events"]["prefixItems"] = [{"type": "string"}]
    elif mutation == "additional_one_of":
        event_properties["label"] = {"oneOf": [event_properties["label"]]}
    elif mutation == "additional_all_of":
        event_properties["label"] = {"allOf": [event_properties["label"]]}
    else:
        event_properties["label"]["not"] = {"const": "forbidden"}
    with pytest.raises(AssertionError):
        _assert_schema_contract(canonical, exported, exported)


def test_recursive_schema_contract_terminates_and_compares_deep_structure() -> None:
    canonical = {
        "$defs": {
            "Node": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/$defs/Node"},
                    "value": {"type": "string"},
                },
            }
        },
        "$ref": "#/$defs/Node",
    }
    equivalent = {
        "$defs": {
            "RecursiveNode": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/$defs/RecursiveNode"},
                    "value": {"type": "string"},
                },
            }
        },
        "$ref": "#/$defs/RecursiveNode",
    }
    _assert_schema_contract(canonical, equivalent, equivalent)

    different = copy.deepcopy(canonical)
    different["$defs"]["Node"]["properties"]["value"]["type"] = "integer"
    with pytest.raises(AssertionError):
        _assert_schema_contract(canonical, different, different)


def test_recursive_schema_contract_handles_indirect_cycles_and_repeated_refs() -> None:
    indirect = {
        "$defs": {
            "A": {"type": "object", "properties": {"next": {"$ref": "#/$defs/B"}}},
            "B": {"type": "object", "properties": {"next": {"$ref": "#/$defs/A"}}},
        },
        "$ref": "#/$defs/A",
    }
    _assert_schema_contract(indirect, copy.deepcopy(indirect), copy.deepcopy(indirect))

    repeated = {
        "$defs": {"Leaf": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {
            "left": {"$ref": "#/$defs/Leaf"},
            "right": {"$ref": "#/$defs/Leaf"},
        },
    }
    _assert_schema_contract(repeated, copy.deepcopy(repeated), copy.deepcopy(repeated))


def test_recursive_schema_contract_rejects_unresolved_local_reference() -> None:
    unresolved = {"$defs": {}, "$ref": "#/$defs/Missing"}
    with pytest.raises(AssertionError, match="Nicht auflösbarer lokaler \\$ref"):
        _assert_schema_contract(unresolved, copy.deepcopy(unresolved), copy.deepcopy(unresolved))


def _event_model_with_optional_field(field: str) -> SidecarEvent:
    return SidecarEvent.model_validate_json(json.dumps(_event_with_valid_optional_field(field)))


def _updated_optional_value(field: str) -> object:
    if field == "end_mapped_source_frame":
        return 321
    if field == "pair_id":
        return uuid4()
    return "Aktualisiert"


@pytest.mark.parametrize("field", _OPTIONAL_EVENT_FIELD_NAMES)
def test_optional_event_copy_revalidation_and_serialization_contract(field: str) -> None:
    original = _event_model_with_optional_field(field)
    for deep in (False, True):
        copied = original.model_copy(deep=deep)
        assert copied.model_dump_json() == original.model_dump_json()
        assert copied.model_fields_set == original.model_fields_set
        with pytest.raises(ValidationError):
            original.model_copy(update={field: None}, deep=deep)

    updated = original.model_copy(update={field: _updated_optional_value(field)})
    updated_payload = json.loads(updated.model_dump_json())
    assert field in updated_payload

    manipulated = original.model_copy()
    object.__setattr__(manipulated, field, None)
    assert field in manipulated.model_fields_set
    with pytest.raises(ValidationError):
        SidecarEvent.model_validate(manipulated)
    with pytest.raises(PydanticSerializationError):
        manipulated.model_dump_json()

    missing_raw = event(str(uuid4()), "scene_changed", 300, counter=300)
    missing = SidecarEvent.model_validate_json(json.dumps(missing_raw))
    missing_copy = missing.model_copy(deep=True)
    missing_marker = getattr(missing, field)
    assert copy.copy(missing_marker) is missing_marker
    assert copy.deepcopy(missing_marker) is missing_marker
    assert field not in missing.model_fields_set
    assert field not in missing_copy.model_fields_set
    assert field not in json.loads(missing.model_dump_json())
    assert field not in json.loads(missing_copy.model_dump_json())

    roundtrip = SidecarEvent.model_validate_json(original.model_dump_json())
    assert field in roundtrip.model_fields_set
    assert getattr(roundtrip, field) == getattr(original, field)

    injected = missing.model_dump()
    injected[field] = _MISSING_EVENT_VALUE
    with pytest.raises(ValidationError):
        SidecarEvent.model_validate(injected)


def _clock_payload(drift_ppm: Decimal) -> dict[str, object]:
    return {
        "origin": "producer_monotonic_at_output_start_signal",
        "monotonic_source": "windows_qpc",
        "mapping": "obs_output_frame_counter_calibrated_to_final_video_frames",
        "counter_start": 0,
        "counter_end": 600,
        "drift_ppm": drift_ppm,
        "max_calibration_residual_ms": Decimal("0"),
        "max_event_uncertainty_ms": Decimal("0"),
        "calibration_sample_count": 2,
    }


@pytest.mark.parametrize(
    "value",
    [
        Decimal("0"),
        Decimal("0.001"),
        Decimal("0.0010000000000000000000001"),
        Decimal("14.8"),
        Decimal("181.5"),
        Decimal("250"),
        Decimal("499.9999999999999999999999"),
        Decimal("500"),
        Decimal("0.1234567890123456789012345678"),
    ],
)
def test_decimal_json_numbers_are_exact_and_roundtrip_stable(value: Decimal) -> None:
    model = ClockCalibration.model_validate(_clock_payload(value))
    serialized = model.model_dump_json()
    decoded = json.loads(serialized, parse_float=Decimal)
    assert not isinstance(decoded["drift_ppm"], str)
    assert Decimal(decoded["drift_ppm"]) == value
    assert f'"drift_ppm":{format(value, "f")}' in serialized
    assert ClockCalibration.model_validate_json(serialized).drift_ppm == value
    assert model.model_dump_json() == serialized


def test_json_mode_mapping_dump_is_blocked_while_canonical_json_remains_exact() -> None:
    value = Decimal("0.0010000000000000000000001")
    model = ClockCalibration.model_validate(_clock_payload(value))
    with pytest.raises(ValueError, match=r"model_dump_json\(\)"):
        model.model_dump(mode="json")

    default_dump = model.model_dump()
    python_dump = model.model_dump(mode="python")
    assert default_dump == python_dump
    assert isinstance(python_dump["drift_ppm"], Decimal)
    assert python_dump["drift_ppm"] == value

    serialized = model.model_dump_json()
    decoded = json.loads(serialized, parse_float=Decimal)
    assert isinstance(decoded["drift_ppm"], Decimal)
    assert decoded["drift_ppm"] == value
    assert ClockCalibration.model_validate_json(serialized).drift_ppm == value


def _precise_drift_sidecar(declared: Decimal) -> ObsEventSidecar:
    raw = sidecar_dict()
    raw["events"][-1]["clock_sample"]["monotonic_ns"] = 10_000_000_000
    raw["clock"]["drift_ppm"] = 0
    parsed = ObsEventSidecar.model_validate_json(json.dumps(raw))
    payload = parsed.model_dump()
    payload["clock"]["drift_ppm"] = declared
    return ObsEventSidecar.model_validate(payload)


@pytest.mark.parametrize(
    "declared",
    [
        Decimal("0.0009999999999999999999999"),
        Decimal("0.001"),
        Decimal("0.0010000000000000000000001"),
    ],
)
def test_precise_drift_semantics_survive_json_roundtrip(declared: Decimal) -> None:
    # Die Zahl wird nicht mehr nachgerechnet, ihre Dezimalstellen müssen den
    # JSON-Weg aber unverändert überstehen: sie ist die einzige Aussage über die
    # Uhrensteigung, die das Sidecar transportiert.
    before = _precise_drift_sidecar(declared)
    after = ObsEventSidecar.model_validate_json(before.model_dump_json())
    assert not _clock_errors(before)
    assert not _clock_errors(after)
    assert after.clock.drift_ppm == declared
    public_payload = json.loads(after.model_dump_json(), parse_float=Decimal)
    public_result = validate_sidecar(public_payload, _expected_source(public_payload))
    assert public_result.mode == "validated_sidecar_1_1"


def test_decimal_gate_and_invalid_numeric_inputs_remain_strict() -> None:
    with pytest.raises(ValidationError):
        ClockCalibration.model_validate(
            _clock_payload(MAX_DRIFT_PPM + Decimal("0.0000000000000000001"))
        )
    for invalid in ("0.001", True, Decimal("NaN"), Decimal("Infinity")):
        payload = _clock_payload(Decimal("0"))
        payload["drift_ppm"] = invalid
        with pytest.raises(ValidationError):
            ClockCalibration.model_validate(payload)

    for nonfinite in (float("nan"), Decimal("NaN")):
        raw = sidecar_dict()
        expected = _expected_source(raw)
        raw["clock"]["drift_ppm"] = nonfinite
        result = validate_sidecar(raw, expected)
        assert result.mode == "no_sidecar_safe_mode"
        assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in result.reasons}


def test_canonical_json_encoder_supported_values_and_defensive_failures() -> None:
    def encode(value: object) -> str:
        return _canonical_json_value(
            value,
            ensure_ascii=False,
            indent=None,
            level=0,
        )

    assert encode(None) == "null"
    assert encode({}) == "{}"
    assert encode([]) == "[]"
    assert encode(Decimal("-0")) == "0"
    assert encode(0.5) == "0.5"
    assert encode(datetime(2026, 7, 12, tzinfo=UTC)) == '"2026-07-12T00:00:00+00:00"'
    assert encode([Decimal("1.20")]) == "[1.20]"
    assert _restore_exact_decimals(True, Decimal("1")) == Decimal("1")
    assert _restore_exact_decimals("not-a-number", Decimal("1")) == Decimal("1")
    assert _restore_exact_decimals([1], [1]) == [1]
    assert _restore_exact_decimals([1, 2], [1]) == [1]
    assert _restore_exact_decimals({"a": 1}, {"a": 1, "b": 2}) == {"a": 1, "b": 2}
    with pytest.raises(ValueError):
        encode(Decimal("NaN"))
    with pytest.raises(ValueError):
        encode(float("inf"))
    with pytest.raises(ValueError):
        encode(datetime(2026, 7, 12))
    with pytest.raises(TypeError):
        encode({1: "not-a-string-key"})
    with pytest.raises(TypeError):
        encode({1, 2})


def test_public_canonical_json_output_rejects_container_cycles_but_allows_sharing() -> None:
    cyclic_list: list[object] = []
    cyclic_list.append(cyclic_list)
    cyclic_mapping: dict[str, object] = {}
    cyclic_mapping["self"] = cyclic_mapping
    indirect_list: list[object] = []
    indirect_mapping: dict[str, object] = {"back": indirect_list}
    indirect_list.append(indirect_mapping)

    for cyclic in (cyclic_list, cyclic_mapping, indirect_list):
        with pytest.raises(ValueError, match="Containerzyklen") as captured:
            _PayloadModel(payload=cyclic).model_dump_json()
        assert not isinstance(captured.value, RecursionError)
        assert "$" in str(captured.value)

    shared = {"value": 1}
    shared_model = _PayloadModel(payload={"left": shared, "right": shared})
    assert json.loads(shared_model.model_dump_json()) == {
        "payload": {"left": {"value": 1}, "right": {"value": 1}}
    }

    recoverable: list[object] = [{1, 2}]
    model = _PayloadModel(payload=recoverable)
    with pytest.raises(TypeError):
        model.model_dump_json()
    recoverable[0] = {"valid": [1, 2]}
    assert json.loads(model.model_dump_json()) == {"payload": [{"valid": [1, 2]}]}


@pytest.mark.parametrize("binary_type", [bytes, bytearray, memoryview])
def test_public_canonical_json_output_rejects_binary_scalars_with_path(
    binary_type: type[bytes] | type[bytearray] | type[memoryview],
) -> None:
    direct = binary_type(b"AB")
    direct_before = bytes(direct)
    with pytest.raises(TypeError, match=binary_type.__name__) as captured:
        _PayloadModel(payload=direct).model_dump_json()
    assert not isinstance(captured.value, RecursionError)
    assert "$.payload" in str(captured.value)
    assert bytes(direct) == direct_before

    nested = binary_type(b"CD")
    nested_before = bytes(nested)
    with pytest.raises(TypeError, match=binary_type.__name__) as nested_captured:
        _PayloadModel(payload={"outer": [nested]}).model_dump_json()
    assert not isinstance(nested_captured.value, RecursionError)
    assert "$.payload.outer[0]" in str(nested_captured.value)
    assert bytes(nested) == nested_before

    assert json.loads(_PayloadModel(payload={"valid": [1, 2]}).model_dump_json()) == {
        "payload": {"valid": [1, 2]}
    }


def test_public_canonical_json_output_preserves_supported_sequence_classification() -> None:
    model = _PayloadModel(
        payload={
            "list": [1, 2],
            "tuple": (3, 4),
            "string": "AB",
        }
    )
    assert json.loads(model.model_dump_json()) == {
        "payload": {
            "list": [1, 2],
            "string": "AB",
            "tuple": [3, 4],
        }
    }


@pytest.mark.parametrize(
    "invalid_uuid",
    [uuid1(), uuid3(NAMESPACE_DNS, "matrix"), uuid5(NAMESPACE_DNS, "matrix")],
)
def test_uuid_runtime_requires_v4_while_schema_uses_uuid(invalid_uuid: UUID) -> None:
    raw = sidecar_dict()
    raw["recording_session_id"] = str(invalid_uuid)
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))


def test_malformed_uuid_string_remains_invalid() -> None:
    raw = sidecar_dict()
    raw["recording_session_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw))


def _lifecycle_payload(finalizer_run_id: object) -> dict[str, object]:
    return {
        "status": "finalized",
        "journal_schema_version": "1.0",
        "finalized_at": datetime(2026, 7, 12, tzinfo=UTC),
        "finalizer_run_id": finalizer_run_id,
    }


def test_uuid4_public_python_json_schema_and_roundtrip_contract() -> None:
    identifier = uuid4()
    from_string = Lifecycle.model_validate(_lifecycle_payload(str(identifier)))
    from_object = Lifecycle.model_validate(_lifecycle_payload(identifier))
    from_json = Lifecycle.model_validate_json(
        json.dumps(
            {
                **_lifecycle_payload(str(identifier)),
                "finalized_at": "2026-07-12T00:00:00+00:00",
            }
        )
    )
    assert from_string.finalizer_run_id == identifier
    assert from_object.finalizer_run_id == identifier
    assert from_json.finalizer_run_id == identifier
    assert Lifecycle.model_validate_json(from_string.model_dump_json()) == from_string
    assert Lifecycle.model_json_schema()["properties"]["finalizer_run_id"] == {
        "format": "uuid",
        "title": "Finalizer Run Id",
        "type": "string",
    }


@pytest.mark.parametrize(
    "invalid_uuid",
    [uuid1(), uuid3(NAMESPACE_DNS, "python-mapping"), uuid5(NAMESPACE_DNS, "python-mapping")],
)
def test_uuid_non_v4_versions_are_rejected_as_python_string_and_object(
    invalid_uuid: UUID,
) -> None:
    for value in (invalid_uuid, str(invalid_uuid)):
        with pytest.raises(ValidationError):
            Lifecycle.model_validate(_lifecycle_payload(value))


@pytest.mark.parametrize(
    "invalid",
    [True, 1, 1.0, b"uuid", [], {}, "not-a-uuid"],
)
def test_uuid_python_input_rejects_non_string_non_uuid_and_malformed_values(
    invalid: object,
) -> None:
    with pytest.raises(ValidationError):
        Lifecycle.model_validate(_lifecycle_payload(invalid))


def test_journal_uuid_fields_use_same_runtime_and_schema_contract() -> None:
    invalid = journal_header()
    invalid["recording_session_id"] = str(uuid1())
    result = validate_journal([invalid, journal_stop(1)])
    assert not result.valid
    assert ErrorCode.JOURNAL_SEQUENCE in {error.code for error in result.errors}

    header_schema = JournalHeader.model_json_schema()
    event_schema = JournalEvent.model_json_schema()
    assert header_schema["properties"]["recording_session_id"]["format"] == "uuid"
    assert event_schema["properties"]["event_id"]["format"] == "uuid"
    for field in ("source_uuid", "pair_id"):
        choices = event_schema["properties"][field]["anyOf"]
        uuid_choice = next(choice for choice in choices if choice.get("type") == "string")
        assert uuid_choice["format"] == "uuid"


def test_all_sidecar_uuid_fields_share_canonical_schema_and_serialization() -> None:
    raw = sidecar_dict()
    identifier = uuid4()
    raw["recording_session_id"] = str(identifier)
    parsed = ObsEventSidecar.model_validate_json(json.dumps(raw))
    assert json.loads(parsed.model_dump_json())["recording_session_id"] == str(identifier)
    schema = parsed.model_json_schema()
    uuid_formats = []

    def collect_formats(node: object) -> None:
        if isinstance(node, dict):
            if node.get("format") in {"uuid", "uuid4"}:
                uuid_formats.append(node["format"])
            for child in node.values():
                collect_formats(child)
        elif isinstance(node, list):
            for child in node:
                collect_formats(child)

    collect_formats(schema)
    assert uuid_formats
    assert set(uuid_formats) == {"uuid"}


def test_safe_mode_aggregates_independent_header_reasons_deterministically() -> None:
    raw = sidecar_dict()
    raw["artifact_type"] = "recording_event_journal"
    raw["schema_version"] = "1.0"
    raw["lifecycle"]["status"] = "recording"
    first = validate_sidecar(raw, _expected_source(raw))
    second = validate_sidecar(raw, _expected_source(raw))
    assert [reason.code for reason in first.reasons] == [
        ErrorCode.SIDECAR_ARTIFACT_TYPE,
        ErrorCode.SIDECAR_VERSION,
        ErrorCode.SIDECAR_NOT_FINALIZED,
    ]
    assert first.reasons == second.reasons
    serialized = [reason.model_dump_json() for reason in first.reasons]
    assert len(serialized) == len(set(serialized))


def _document() -> ProtectionRangesDocument:
    return ProtectionRangesDocument(
        source_sha256="a" * 64,
        input_hash="b" * 64,
        configuration_hash="c" * 64,
        ranges=(),
    )


@pytest.mark.parametrize("relative", ["other.json", "nested/../protection-ranges.json"])
def test_atomic_export_rejects_invalid_targets(tmp_path: Path, relative: str) -> None:
    target = tmp_path / relative
    result = write_protection_ranges(target, _document())
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.SIDECAR_OUTPUT
    assert not target.exists()


def test_atomic_cleanup_on_serialization_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protection-ranges.json"

    def fail_serialization(_document: ProtectionRangesDocument) -> bytes:
        raise TypeError("serialization")

    monkeypatch.setattr(atomic_module, "_deterministic_json", fail_serialization)
    with pytest.raises(TypeError, match="serialization"):
        write_protection_ranges(target, _document())
    assert not list(tmp_path.iterdir())


def test_atomic_cleanup_failure_does_not_hide_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protection-ranges.json"

    def fail_write(_document: ProtectionRangesDocument) -> bytes:
        raise OSError("primary")

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise OSError("cleanup")

    with monkeypatch.context() as scoped:
        scoped.setattr(atomic_module, "_deterministic_json", fail_write)
        scoped.setattr(Path, "unlink", fail_cleanup)
        result = write_protection_ranges(target, _document())
    assert result.status == "failed"
    assert result.error is not None
    assert "primary" in str(result.error.technical_context["detail"])
    assert "cleanup" in str(result.error.technical_context["cleanup_detail"])
    for temporary in tmp_path.iterdir():
        temporary.unlink()


def test_runtime_cleanup_failure_preserves_structured_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protection-ranges.json"
    target.write_bytes(b"existing\n")

    def fail_primary(_document: ProtectionRangesDocument) -> bytes:
        raise OSError("PRIMARY")

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise RuntimeError("CLEANUP")

    with monkeypatch.context() as scoped:
        scoped.setattr(atomic_module, "_deterministic_json", fail_primary)
        scoped.setattr(Path, "unlink", fail_cleanup)
        result = write_protection_ranges(target, _document())
    assert result.status == "failed"
    assert result.error is not None
    assert "PRIMARY" in str(result.error.technical_context["detail"])
    assert "CLEANUP" in str(result.error.technical_context["cleanup_detail"])
    assert target.read_bytes() == b"existing\n"
    for temporary in tmp_path.glob(".protection-ranges.json.tmp.*"):
        temporary.unlink()


def test_runtime_cleanup_failure_preserves_propagating_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protection-ranges.json"

    def fail_primary(_document: ProtectionRangesDocument) -> bytes:
        raise TypeError("PRIMARY")

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise RuntimeError("CLEANUP")

    with monkeypatch.context() as scoped:
        scoped.setattr(atomic_module, "_deterministic_json", fail_primary)
        scoped.setattr(Path, "unlink", fail_cleanup)
        with pytest.raises(TypeError, match="PRIMARY") as captured:
            write_protection_ranges(target, _document())
    assert any("CLEANUP" in note for note in captured.value.__notes__)
    assert not target.exists()
    for temporary in tmp_path.glob(".protection-ranges.json.tmp.*"):
        temporary.unlink()


def test_cleanup_failure_without_primary_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "protection-ranges.json"

    def fail_cleanup(_path: Path, *, missing_ok: bool = False) -> None:
        del missing_ok
        raise RuntimeError("CLEANUP")

    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    with pytest.raises(RuntimeError, match="CLEANUP"):
        write_protection_ranges(target, _document())


def test_out_of_bounds_incomplete_pair_is_clamped_away() -> None:
    raw = sidecar_dict()
    raw["events"].insert(
        1,
        event(str(uuid4()), "intro_started", 10_000, pair_id=str(uuid4()), counter=None),
    )
    result = materialize_protection(ObsEventSidecar.model_validate_json(json.dumps(raw)))
    assert result.status == "materialized"


@pytest.mark.parametrize("operation", ["write", "fsync", "replace"])
def test_atomic_os_errors_preserve_existing_target_and_remove_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    target = tmp_path / "protection-ranges.json"
    original = b"existing\n"
    target.write_bytes(original)
    if operation == "write":

        def fail_write(_document: ProtectionRangesDocument) -> bytes:
            raise OSError("write")

        monkeypatch.setattr(atomic_module, "_deterministic_json", fail_write)
    elif operation == "fsync":
        monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("fsync")))
    else:
        monkeypatch.setattr(os, "replace", lambda _a, _b: (_ for _ in ()).throw(OSError("replace")))
    result = write_protection_ranges(target, _document())
    assert result.status == "failed"
    assert target.read_bytes() == original
    assert not list(tmp_path.glob(".protection-ranges.json.tmp.*"))


@given(st.integers(min_value=0, max_value=800))
def test_three_overlapping_policies_preserve_union_and_hard_priority(start: int) -> None:
    ranges = (
        materialized(start, start + 30, hard=False, time=True, overlays=False, audio=False),
        materialized(start + 10, start + 40, hard=True, time=False, overlays=True, audio=False),
        materialized(start + 20, start + 50, hard=False, time=False, overlays=False, audio=True),
    )
    result = normalize_ranges(ranges)
    common = next(
        item
        for item in result
        if item.source_start_frame == start + 20 and item.source_end_frame == start + 30
    )
    assert common.level == ProtectionLevel.HARD
    assert common.policy.blocks_time_edits
    assert common.policy.blocks_overlays
    assert common.policy.blocks_local_audio_repair
    assert common.policy.allows_global_mastering
    assert normalize_ranges(result) == result


@given(st.permutations((0, 1, 2)))
def test_normalization_event_sources_are_unique_and_order_stable(order: list[int]) -> None:
    identifiers = (
        UUID("00000000-0000-4000-8000-000000000003"),
        UUID("00000000-0000-4000-8000-000000000001"),
        UUID("00000000-0000-4000-8000-000000000002"),
    )
    inputs = tuple(
        materialized(10, 20, hard=False, time=True, overlays=False, audio=False).model_copy(
            update={"source_event_ids": (identifiers[index],)}
        )
        for index in order
    )
    result = normalize_ranges(inputs)
    assert result[0].source_event_ids == tuple(sorted(identifiers, key=str))
    assert normalize_ranges(result) == result
