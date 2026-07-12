"""Kanonische Testfixtures für den Vertragskern."""

from __future__ import annotations

import json
from typing import Any

import pytest

from matrix_auto_cutter.models import SourceIdentity
from matrix_auto_cutter.sidecar import ObsEventSidecar

SESSION_ID = "835fc47a-7e8c-4700-9f6f-8f7e23ac740c"
RUN_ID = "2e157a84-2e31-49d9-b64e-494c24f8f612"
START_ID = "bfc5ea5a-593f-4261-8262-6d6e508bc6df"
STOP_ID = "e3fdaf55-f895-49fa-913d-a7b20fa6cc41"
INTRO_PAIR = "b950d183-bf61-4df5-9419-b121e05ac366"


def hard_protection(before: int = 0, after: int = 0) -> dict[str, Any]:
    return {
        "level": "hard",
        "buffer_before_ms": before,
        "buffer_after_ms": after,
        "policy": {
            "blocks_time_edits": True,
            "blocks_overlays": True,
            "blocks_local_audio_repair": True,
            "allows_global_mastering": True,
        },
    }


def soft_protection() -> dict[str, Any]:
    return {
        "level": "soft",
        "buffer_before_ms": 0,
        "buffer_after_ms": 0,
        "policy": {
            "blocks_time_edits": False,
            "blocks_overlays": False,
            "blocks_local_audio_repair": False,
            "allows_global_mastering": True,
        },
    }


def event(
    event_id: str,
    event_type: str,
    frame: int,
    *,
    protection: dict[str, Any] | None = None,
    pair_id: str | None = None,
    end_frame: int | None = None,
    uncertainty_ms: int | float = 100,
    counter: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_id": event_id,
        "type": event_type,
        "mapped_source_frame": frame,
        "uncertainty_ms": uncertainty_ms,
        "clock_sample": {
            "monotonic_ns": frame * 16_666_667,
            "output_frame_count": counter,
            "mapping_basis": "output_frame_counter" if counter is not None else "qpc_fallback",
        },
        "protection": protection or hard_protection(),
    }
    if pair_id is not None:
        result["pair_id"] = pair_id
    if end_frame is not None:
        result["end_mapped_source_frame"] = end_frame
    return result


def source_dict() -> dict[str, Any]:
    return {
        "file_name": "aufnahme.mp4",
        "size_bytes": 12_003_400_567,
        "sha256": "a" * 64,
        "duration_ms": 10_000,
        "video_frame_count": 600,
        "fps_num": 60,
        "fps_den": 1,
        "video_start_time_ns": 46_000_000,
        "audio_start_time_ns": 49_000_000,
        "binding": "direct_mp4",
    }


def sidecar_dict() -> dict[str, Any]:
    return {
        "artifact_type": "obs_event_sidecar",
        "schema_version": "1.1",
        "producer": {
            "name": "matrix-auto-cutter-obs-producer",
            "version": "0.1.0",
            "obs_version": "32.2.0",
            "finalizer_version": "0.1.0",
        },
        "lifecycle": {
            "status": "finalized",
            "journal_schema_version": "1.0",
            "finalized_at": "2026-07-12T16:00:00+02:00",
            "finalizer_run_id": RUN_ID,
        },
        "recording_session_id": SESSION_ID,
        "source": source_dict(),
        "clock": {
            "origin": "producer_monotonic_at_output_start_signal",
            "monotonic_source": "windows_qpc",
            "mapping": "obs_output_frame_counter_calibrated_to_final_video_frames",
            "counter_start": 0,
            "counter_end": 600,
            "drift_ppm": 0.02,
            "max_calibration_residual_ms": 50,
            "max_event_uncertainty_ms": 100,
            "calibration_sample_count": 2,
        },
        "capabilities": {
            "pause_resume": "supported_v1",
            "file_splitting": "not_used_unsupported_v1",
            "remux": "not_used",
        },
        "pause_intervals": [],
        "events": [
            event(START_ID, "recording_started", 0, counter=0),
            event(STOP_ID, "recording_stopped", 600, counter=600),
        ],
        "finalization": {
            "file_closed_verified": True,
            "full_sha256_verified": True,
            "probe_verified": True,
            "journal_complete": True,
            "warnings": [],
        },
    }


@pytest.fixture
def raw_sidecar() -> dict[str, Any]:
    return sidecar_dict()


@pytest.fixture
def expected_source() -> SourceIdentity:
    return SourceIdentity.model_validate_json(json.dumps(source_dict()))


@pytest.fixture
def parsed_sidecar(raw_sidecar: dict[str, Any]) -> ObsEventSidecar:
    return ObsEventSidecar.model_validate_json(json.dumps(raw_sidecar))
