"""Journal-/Sidecar-Trennung, Lifecycle und Safe-Mode."""

from __future__ import annotations

import copy
import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from conftest import STOP_ID, event, hard_protection, soft_protection
from matrix_auto_cutter.clock_bounds import MAX_DRIFT_PPM
from matrix_auto_cutter.errors import ErrorCode
from matrix_auto_cutter.journal import validate_journal
from matrix_auto_cutter.models import (
    ClockSample,
    MaterializedFrameRange,
    PauseInterval,
    ProtectionLevel,
    ProtectionPolicy,
    SourceIdentity,
)
from matrix_auto_cutter.paths import expected_sidecar_path
from matrix_auto_cutter.sidecar import ObsEventSidecar, validate_sidecar


def journal_header() -> dict[str, Any]:
    return {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "header",
        "sequence": 0,
        "recording_session_id": "835fc47a-7e8c-4700-9f6f-8f7e23ac740c",
        "lifecycle_status": "recording",
        "producer": {
            "name": "matrix-auto-cutter-obs-producer",
            "version": "0.1",
            "obs_version": "32.2",
        },
        "clock": {
            "source": "windows_qpc",
            "unit": "ns",
            "origin": "producer_monotonic_at_output_start_signal",
        },
        "capabilities": {
            "pause_resume": "supported_v1",
            "file_splitting": "unsupported_v1",
        },
        "initial_output_path": "F:\\Video\\aufnahme.mkv",
    }


def journal_stop(
    sequence: int, *, counter: int = 600, recording_paused: bool = False
) -> dict[str, Any]:
    return {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "stop",
        "sequence": sequence,
        "lifecycle_status": "stopped_unfinalized",
        "monotonic_ns": 10_000_000_000,
        "output_frame_count": counter,
        "recording_paused": recording_paused,
        "last_recording_path": "F:\\Video\\aufnahme.mkv",
        "output_result": "success",
        "file_splitting_detected": False,
    }


def journal_record(record_type: str, sequence: int, ns: int, counter: int) -> dict[str, Any]:
    base: dict[str, Any] = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": record_type,
        "sequence": sequence,
        "monotonic_ns": ns,
        "output_frame_count": counter,
    }
    if record_type in {"pause", "resume"}:
        base["event_id"] = str(uuid4())
    if record_type == "pause":
        base["recording_paused"] = True
    elif record_type in {
        "resume",
        "calibration_sample",
        "path_snapshot",
        "split_status",
        "output_error",
    }:
        base["recording_paused"] = False
    return base


def test_expected_sidecar_paths() -> None:
    assert str(expected_sidecar_path("aufnahme.mp4")) == "aufnahme.obs-events.json"
    assert str(expected_sidecar_path("Ein schöner Clip.v1.final.MP4")) == (
        "Ein schöner Clip.v1.final.obs-events.json"
    )
    assert str(expected_sidecar_path(r"F:\Roh Ablage\größer.test.mP4")) == (
        r"F:\Roh Ablage\größer.test.obs-events.json"
    )
    with pytest.raises(ValueError):
        expected_sidecar_path("aufnahme.mkv")


def test_complete_journal_and_event_during_pause_are_valid() -> None:
    pause = journal_record("pause", 1, 1_000, 60)
    paused_event = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "event",
        "sequence": 2,
        "event_id": str(uuid4()),
        "event_type": "scene_changed",
        "monotonic_ns": 2_000,
        "output_frame_count": 61,
        "recording_paused": True,
    }
    resume = journal_record("resume", 3, 3_000, 62)
    result = validate_journal([journal_header(), pause, paused_event, resume, journal_stop(4)])
    assert result.valid and not result.errors and result.recording_session_id is not None


def test_real_obs_interleaver_pause_drain_is_monotonic_and_valid() -> None:
    accepted = validate_journal(
        [
            journal_header(),
            journal_record("pause", 1, 1_000, 60),
            journal_record("resume", 2, 2_000, 109),
            journal_stop(3, counter=169),
        ]
    )
    assert accepted.valid

    rejected = validate_journal(
        [
            journal_header(),
            journal_record("pause", 1, 1_000, 60),
            journal_record("resume", 2, 2_000, 59),
            journal_stop(3, counter=120),
        ]
    )
    assert ErrorCode.JOURNAL_SEQUENCE in {error.code for error in rejected.errors}


def test_stop_while_paused_is_valid() -> None:
    result = validate_journal(
        [
            journal_header(),
            journal_record("pause", 1, 1_000, 599),
            journal_stop(2, counter=600, recording_paused=True),
        ]
    )
    assert result.valid


@pytest.mark.parametrize(
    ("records", "code"),
    [
        ([], ErrorCode.JOURNAL_INCOMPLETE),
        ([journal_header()], ErrorCode.JOURNAL_INCOMPLETE),
        ([journal_header(), {**journal_stop(2), "sequence": 2}], ErrorCode.JOURNAL_SEQUENCE),
        (
            [journal_header(), {**journal_stop(1), "output_result": "failure"}],
            ErrorCode.JOURNAL_OUTPUT_FAILURE,
        ),
        (
            [journal_header(), {**journal_stop(1), "file_splitting_detected": True}],
            ErrorCode.JOURNAL_OUTPUT_FAILURE,
        ),
    ],
)
def test_journal_structured_failures(
    records: list[dict[str, Any]],
    code: ErrorCode,
) -> None:
    result = validate_journal(records)
    assert not result.valid
    assert code in {error.code for error in result.errors}


def test_journal_record_types_clock_and_pause_failures() -> None:
    unknown = {**journal_stop(1), "record_type": "mystery"}
    assert not validate_journal([journal_header(), unknown]).valid

    double_pause = [
        journal_header(),
        journal_record("pause", 1, 100, 10),
        journal_record("pause", 2, 200, 10),
        journal_stop(3),
    ]
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {
        error.code for error in validate_journal(double_pause).errors
    }
    resume_without_pause = [
        journal_header(),
        journal_record("resume", 1, 100, 10),
        journal_stop(2),
    ]
    assert not validate_journal(resume_without_pause).valid
    moved = [
        journal_header(),
        journal_record("pause", 1, 100, 10),
        journal_record("resume", 2, 200, 14),
        journal_stop(3),
    ]
    assert validate_journal(moved).valid
    regressed = [
        journal_header(),
        journal_record("pause", 1, 100, 10),
        journal_record("resume", 2, 200, 9),
        journal_stop(3),
    ]
    assert ErrorCode.JOURNAL_SEQUENCE in {
        error.code for error in validate_journal(regressed).errors
    }
    qpc_back = [
        journal_header(),
        journal_record("calibration_sample", 1, 200, 20) | {"recording_paused": False},
        journal_record("calibration_sample", 2, 100, 19) | {"recording_paused": False},
        journal_stop(3),
    ]
    assert ErrorCode.JOURNAL_SEQUENCE in {error.code for error in validate_journal(qpc_back).errors}

    counterless_event = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
        "record_type": "event",
        "sequence": 2,
        "event_id": str(uuid4()),
        "event_type": "scene_changed",
        "monotonic_ns": 200,
        "output_frame_count": None,
        "recording_paused": False,
    }
    regression_across_counterless = [
        journal_header(),
        journal_record("calibration_sample", 1, 100, 20) | {"recording_paused": False},
        counterless_event,
        journal_record("calibration_sample", 3, 300, 19) | {"recording_paused": False},
        journal_stop(4),
    ]
    assert ErrorCode.JOURNAL_SEQUENCE in {
        error.code for error in validate_journal(regression_across_counterless).errors
    }

    output_error = journal_record("output_error", 1, 100, 10) | {
        "output_result": "failure",
        "diagnostic": "encoder",
    }
    assert ErrorCode.JOURNAL_OUTPUT_FAILURE in {
        error.code
        for error in validate_journal([journal_header(), output_error, journal_stop(2)]).errors
    }
    split = journal_record("split_status", 1, 100, 10) | {
        "split_requested": True,
        "file_splitting_detected": False,
    }
    assert ErrorCode.JOURNAL_OUTPUT_FAILURE in {
        error.code for error in validate_journal([journal_header(), split, journal_stop(2)]).errors
    }


def test_canonical_model_validation_branches() -> None:
    with pytest.raises(ValidationError):
        SourceIdentity.model_validate_json(
            '{"file_name":"x.mp4","size_bytes":1,"sha256":"INVALID"}'
        )
    with pytest.raises(ValidationError):
        ClockSample(monotonic_ns=0, output_frame_count=None, mapping_basis="output_frame_counter")
    with pytest.raises(ValidationError):
        MaterializedFrameRange(
            protection_id="x",
            source_start_frame=2,
            source_end_frame=1,
            level=ProtectionLevel.HARD,
            source_event_ids=(),
            uncertainty_padding_frames=0,
            policy=ProtectionPolicy(
                blocks_time_edits=True,
                blocks_overlays=True,
                blocks_local_audio_repair=True,
                allows_global_mastering=True,
            ),
        )
    base = {
        "pause_event_id": str(uuid4()),
        "close_event_id": str(uuid4()),
        "end_reason": "resumed",
        "pause_monotonic_ns": 2,
        "end_monotonic_ns": 1,
        "mapped_source_frame_before": 0,
        "mapped_source_frame_after": 0,
    }
    with pytest.raises(ValidationError):
        PauseInterval.model_validate_json(json.dumps(base))
    base["pause_monotonic_ns"] = 0
    base["end_monotonic_ns"] = 1
    base["mapped_source_frame_before"] = 5
    base["mapped_source_frame_after"] = 4
    with pytest.raises(ValidationError):
        PauseInterval.model_validate_json(json.dumps(base))


def test_journal_and_sidecar_are_strictly_separate(
    expected_source: SourceIdentity,
) -> None:
    result = validate_sidecar(journal_header(), expected_source)
    assert result.mode == "no_sidecar_safe_mode"
    assert result.reasons[0].code == ErrorCode.SIDECAR_ARTIFACT_TYPE


def test_sidecar_version_lifecycle_and_schema_safe_modes(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    assert validate_sidecar(None, expected_source).mode == "no_sidecar_safe_mode"
    old = copy.deepcopy(raw_sidecar)
    old["schema_version"] = "1.0"
    assert validate_sidecar(old, expected_source).reasons[0].code == ErrorCode.SIDECAR_VERSION
    unfinished = copy.deepcopy(raw_sidecar)
    unfinished["lifecycle"]["status"] = "recording"
    assert validate_sidecar(unfinished, expected_source).reasons[0].code == (
        ErrorCode.SIDECAR_NOT_FINALIZED
    )
    malformed = copy.deepcopy(raw_sidecar)
    malformed["unknown"] = True
    assert validate_sidecar(malformed, expected_source).mode == "no_sidecar_safe_mode"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("file_name", "anders.mp4"),
        ("size_bytes", 1),
        ("sha256", "b" * 64),
        ("duration_ms", 10_017),
        ("video_frame_count", 601),
    ],
)
def test_source_identity_mismatch_fields(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
    field: str,
    value: object,
) -> None:
    raw_sidecar["source"][field] = value
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_IDENTITY in {reason.code for reason in result.reasons}


def test_source_duration_within_one_frame_is_accepted(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    raw_sidecar["source"]["duration_ms"] = 10_016
    assert validate_sidecar(raw_sidecar, expected_source).mode == "validated_sidecar_1_1"


@pytest.mark.parametrize(
    ("clock_field", "invalid"),
    [
        ("max_calibration_residual_ms", 50.0001),
        ("drift_ppm", float(MAX_DRIFT_PPM) + 0.0001),
        ("max_event_uncertainty_ms", 250.0001),
    ],
)
def test_clock_model_gates_above_boundary(
    raw_sidecar: dict[str, Any],
    clock_field: str,
    invalid: object,
) -> None:
    ObsEventSidecar.model_validate_json(json.dumps(raw_sidecar))
    raw_sidecar["clock"][clock_field] = invalid
    with pytest.raises(ValidationError):
        ObsEventSidecar.model_validate_json(json.dumps(raw_sidecar))


def test_counter_span_within_six_still_requires_consistent_event_evidence(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    raw_sidecar["clock"]["counter_end"] = 606
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in {reason.code for reason in result.reasons}


def test_manual_marker_requires_counter_and_safe_policy(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    marker = event(
        str(uuid4()),
        "manual_protection",
        200,
        protection=soft_protection(),
        counter=None,
    )
    raw_sidecar["events"].insert(1, marker)
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in {reason.code for reason in result.reasons}
    marker["clock_sample"]["output_frame_count"] = 200
    marker["clock_sample"]["mapping_basis"] = "output_frame_counter"
    marker["protection"] = hard_protection()
    marker["protection"]["policy"]["blocks_time_edits"] = False
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in result.reasons}


def test_automatic_policy_cannot_be_weakened(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    raw_sidecar["events"][0]["protection"]["policy"]["blocks_overlays"] = False
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_POLICY in {reason.code for reason in result.reasons}


def test_valid_pause_resume_stop_while_paused_and_event_during_pause(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    pause_id = str(uuid4())
    resume_id = str(uuid4())
    pause_event = event(
        pause_id, "recording_paused", 300, protection=soft_protection(), counter=300
    )
    pause_event["clock_sample"]["monotonic_ns"] = 5_000_000_000
    resume_event = event(
        resume_id,
        "recording_resumed",
        301,
        protection=soft_protection(),
        counter=301,
    )
    resume_event["clock_sample"]["monotonic_ns"] = 7_000_000_000
    raw_sidecar["events"][-1]["clock_sample"]["monotonic_ns"] = 12_000_000_200
    raw_sidecar["events"].insert(1, pause_event)
    raw_sidecar["events"].insert(2, resume_event)
    raw_sidecar["pause_intervals"] = [
        {
            "pause_event_id": pause_id,
            "close_event_id": resume_id,
            "end_reason": "resumed",
            "pause_monotonic_ns": 5_000_000_000,
            "end_monotonic_ns": 7_000_000_000,
            "mapped_source_frame_before": 300,
            "mapped_source_frame_after": 301,
        }
    ]
    assert validate_sidecar(raw_sidecar, expected_source).mode == "validated_sidecar_1_1"
    raw_sidecar["events"].insert(
        2,
        event(str(uuid4()), "scene_changed", 300, counter=300),
    )
    raw_sidecar["events"][2]["clock_sample"]["monotonic_ns"] = 6_000_000_000
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {reason.code for reason in result.reasons}

    stopped = copy.deepcopy(raw_sidecar)
    stop_pause = event(pause_id, "recording_paused", 600, protection=soft_protection(), counter=600)
    stop_pause["clock_sample"]["monotonic_ns"] = 10_000_000_200
    stop_event = stopped["events"][-1]
    stop_event["clock_sample"]["monotonic_ns"] = 12_000_000_200
    stopped["events"] = [stopped["events"][0], stop_pause, stop_event]
    stopped["pause_intervals"] = [
        {
            "pause_event_id": pause_id,
            "close_event_id": STOP_ID,
            "end_reason": "recording_stopped_while_paused",
            "pause_monotonic_ns": 10_000_000_200,
            "end_monotonic_ns": 12_000_000_200,
            "mapped_source_frame_before": 600,
            "mapped_source_frame_after": 600,
        }
    ]
    assert validate_sidecar(stopped, expected_source).mode == "validated_sidecar_1_1"


def test_ambiguous_pair_is_safe_mode(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    pair_id = str(uuid4())
    raw_sidecar["events"][1:1] = [
        event(str(uuid4()), "intro_started", 100, pair_id=pair_id),
        event(str(uuid4()), "intro_started", 120, pair_id=pair_id),
    ]
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_EVENT_PAIRS in {reason.code for reason in result.reasons}


def test_sidecar_semantic_event_failures_are_structured(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    raw_sidecar["source"]["duration_ms"] = 20_000
    raw_sidecar["events"][0]["mapped_source_frame"] = 1
    raw_sidecar["events"][1]["mapped_source_frame"] = 599
    out_of_bounds = event(str(uuid4()), "scene_changed", 601, counter=601)
    out_of_bounds["end_mapped_source_frame"] = 602
    pair_missing = event(str(uuid4()), "intro_started", 100)
    manual_invalid = event(
        str(uuid4()),
        "manual_protection",
        300,
        end_frame=299,
        counter=300,
    )
    raw_sidecar["events"][1:1] = [out_of_bounds, pair_missing, manual_invalid]
    result = validate_sidecar(raw_sidecar, expected_source)
    codes = {reason.code for reason in result.reasons}
    assert ErrorCode.SIDECAR_CLOCK_UNRELIABLE in codes
    assert ErrorCode.SIDECAR_POLICY in codes


def test_pause_coverage_overlap_and_wrong_close_are_structured(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    pause_one = str(uuid4())
    pause_two = str(uuid4())
    resume_one = str(uuid4())
    resume_orphan = str(uuid4())
    for identifier, kind, ns in (
        (pause_one, "recording_paused", 100),
        (pause_two, "recording_paused", 150),
        (resume_one, "recording_resumed", 300),
        (resume_orphan, "recording_resumed", 400),
    ):
        item = event(identifier, kind, 100, protection=soft_protection(), counter=100)
        item["clock_sample"]["monotonic_ns"] = ns
        raw_sidecar["events"].insert(-1, item)
    raw_sidecar["pause_intervals"] = [
        {
            "pause_event_id": pause_one,
            "close_event_id": pause_two,
            "end_reason": "resumed",
            "pause_monotonic_ns": 100,
            "end_monotonic_ns": 250,
            "mapped_source_frame_before": 100,
            "mapped_source_frame_after": 100,
        },
        {
            "pause_event_id": pause_two,
            "close_event_id": resume_one,
            "end_reason": "resumed",
            "pause_monotonic_ns": 150,
            "end_monotonic_ns": 300,
            "mapped_source_frame_before": 100,
            "mapped_source_frame_after": 100,
        },
    ]
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {reason.code for reason in result.reasons}


def test_pause_event_without_interval_is_structured(
    raw_sidecar: dict[str, Any],
    expected_source: SourceIdentity,
) -> None:
    raw_sidecar["events"].insert(
        -1,
        event(
            str(uuid4()),
            "recording_paused",
            100,
            protection=soft_protection(),
            counter=100,
        ),
    )
    result = validate_sidecar(raw_sidecar, expected_source)
    assert ErrorCode.SIDECAR_PAUSE_SEQUENCE in {reason.code for reason in result.reasons}
