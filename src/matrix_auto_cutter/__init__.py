"""Kanonischer Kern des Matrix Auto Cutter."""

from matrix_auto_cutter.atomic import write_protection_ranges
from matrix_auto_cutter.calibration import (
    affine_counter_frame,
    calculate_drift_ppm,
    calculate_event_uncertainty_ms,
    calibration_residual_ms,
    map_event_to_source_frame,
    map_qpc_frame,
    sample_gaps_valid,
    subtract_paused_ns,
)
from matrix_auto_cutter.journal import validate_journal
from matrix_auto_cutter.paths import expected_sidecar_path
from matrix_auto_cutter.protection import (
    is_local_audio_repair_blocked,
    materialize_protection,
    normalize_ranges,
)
from matrix_auto_cutter.sidecar import validate_sidecar
from matrix_auto_cutter.timebase import Frame, FrameRange, FrameRate

__all__ = [
    "Frame",
    "FrameRange",
    "FrameRate",
    "affine_counter_frame",
    "calculate_drift_ppm",
    "calculate_event_uncertainty_ms",
    "calibration_residual_ms",
    "expected_sidecar_path",
    "is_local_audio_repair_blocked",
    "map_event_to_source_frame",
    "map_qpc_frame",
    "materialize_protection",
    "normalize_ranges",
    "sample_gaps_valid",
    "subtract_paused_ns",
    "validate_journal",
    "validate_sidecar",
    "write_protection_ranges",
]
