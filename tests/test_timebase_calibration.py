"""Tests der rationalen Zeitbasis und reinen Kalibrierung."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

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
from matrix_auto_cutter.models import CalibrationSample, PauseMeasurement
from matrix_auto_cutter.timebase import Frame, FrameRange, FrameRate


def sample(ns: int, counter: int) -> CalibrationSample:
    return CalibrationSample(monotonic_ns=ns, output_frame_count=counter)


def pause(start: int, end: int) -> PauseMeasurement:
    return PauseMeasurement(start_ns=start, end_ns=end)


def test_frame_rate_and_outward_rounding() -> None:
    rate = FrameRate()
    assert rate.protection_start(Fraction(1001, 1)) == 60
    assert rate.protection_end(Fraction(1001, 1)) == 61
    assert rate.protection_start(0) == 0
    assert rate.protection_end(0) == 0
    with pytest.raises(ValueError):
        FrameRate(30, 1)
    with pytest.raises(ValueError):
        rate.protection_start(-1)
    with pytest.raises(ValueError):
        rate.protection_end(-1)


def test_frame_and_half_open_range_contract() -> None:
    area = FrameRange(Frame(10), Frame(20))
    overlap = FrameRange(Frame(15), Frame(25))
    adjacent = FrameRange(Frame(20), Frame(30))
    bounds = FrameRange(Frame(12), Frame(18))
    assert area.length == 10
    assert area.contains(Frame(10))
    assert not area.contains(Frame(20))
    assert area.intersects(overlap)
    assert not area.intersects(adjacent)
    assert area.intersection(overlap) == FrameRange(Frame(15), Frame(20))
    assert area.intersection(adjacent) is None
    assert area.clamp(bounds) == bounds
    assert adjacent.clamp(area) is None
    with pytest.raises(ValueError):
        Frame(-1)
    with pytest.raises(ValueError):
        Frame(True)
    with pytest.raises(ValueError):
        FrameRange(Frame(1), Frame(1))


@given(st.integers(min_value=0, max_value=100_000), st.integers(min_value=1, max_value=1000))
def test_frame_range_length_property(start: int, length: int) -> None:
    area = FrameRange(Frame(start), Frame(start + length))
    assert area.length == length
    assert area.contains(Frame(start))
    assert not area.contains(Frame(start + length))


def test_affine_counter_mapping_and_failures() -> None:
    assert affine_counter_frame(50, 0, 100, 601) == 301
    assert affine_counter_frame(100, 0, 100, 600) == 600
    with pytest.raises(ValueError):
        affine_counter_frame(0, 1, 1, 10)
    with pytest.raises(ValueError):
        affine_counter_frame(11, 0, 10, 10)


def test_pause_subtraction_and_overlap_rejection() -> None:
    pauses = (pause(20, 40), pause(60, 80))
    assert subtract_paused_ns(10, 70, pauses) == 30
    assert subtract_paused_ns(0, 10, pauses) == 10
    with pytest.raises(ValueError):
        subtract_paused_ns(2, 1, ())
    with pytest.raises(ValueError):
        subtract_paused_ns(0, 100, (pause(10, 50), pause(40, 60)))
    with pytest.raises(ValidationError):
        pause(2, 1)


def test_qpc_piecewise_mapping_with_real_pause_removed() -> None:
    samples = (sample(0, 0), sample(12_000_000_000, 600))
    pauses = (pause(5_000_000_000, 7_000_000_000),)
    assert map_qpc_frame(7_000_000_000, samples, pauses, 0, 600, 600) == 300
    assert map_qpc_frame(12_000_000_000, samples, pauses, 0, 600, 600) == 600
    with pytest.raises(ValueError):
        map_qpc_frame(1, (sample(0, 0),), (), 0, 1, 1)
    with pytest.raises(ValueError):
        map_qpc_frame(5, (sample(0, 2), sample(10, 1)), (), 0, 2, 2)
    three = (sample(0, 0), sample(5_000_000_000, 300), sample(10_000_000_000, 600))
    assert map_qpc_frame(7_500_000_000, three, (), 0, 600, 600) == 450


def test_residual_drift_and_uncertainty_boundaries() -> None:
    assert calibration_residual_ms(Fraction(3), Fraction(0)) == Decimal(50)
    assert calibration_residual_ms(Fraction(3_000_001, 1_000_000), Fraction(0)) > 50
    assert calculate_drift_ppm(10_005_000_000, 600) == Decimal(500)
    assert calculate_drift_ppm(10_005_000_001, 600) > 500
    assert calculate_event_uncertainty_ms(
        manual=False,
        max_residual_ms=Decimal(0),
        qpc_fallback=False,
    ) == Decimal(100) + Decimal(1000) / Decimal(60)
    assert (
        calculate_event_uncertainty_ms(
            manual=True,
            max_residual_ms=Decimal(10),
            qpc_fallback=True,
        )
        > 200
    )
    with pytest.raises(ValueError):
        calculate_drift_ppm(0, 1)
    with pytest.raises(ValueError):
        calculate_event_uncertainty_ms(
            manual=False,
            max_residual_ms=Decimal(-1),
            qpc_fallback=False,
        )


def test_sample_gap_gate_and_pause_adjustment() -> None:
    assert sample_gaps_valid((sample(0, 0), sample(5_000_000_000, 300)))
    assert not sample_gaps_valid((sample(0, 0), sample(5_000_000_001, 300)))
    assert sample_gaps_valid(
        (sample(0, 0), sample(7_000_000_000, 300)),
        (pause(2_000_000_000, 4_000_000_000),),
    )
    assert not sample_gaps_valid((sample(0, 0),))
    assert not sample_gaps_valid((sample(0, 2), sample(1, 1)))


def test_event_mapping_counter_qpc_and_manual_gate() -> None:
    samples = (sample(0, 0), sample(10_000_000_000, 600))
    counter = map_event_to_source_frame(
        event_counter=300,
        event_ns=5_000_000_000,
        manual=True,
        samples=samples,
        pauses=(),
        counter_start=0,
        counter_end=600,
        source_frame_count=600,
    )
    assert counter.status == "mapped" and counter.source_frame == 300
    qpc = map_event_to_source_frame(
        event_counter=None,
        event_ns=5_000_000_000,
        manual=False,
        samples=samples,
        pauses=(),
        counter_start=0,
        counter_end=600,
        source_frame_count=600,
    )
    assert qpc.status == "mapped" and qpc.source_frame == 300
    manual = map_event_to_source_frame(
        event_counter=None,
        event_ns=1,
        manual=True,
        samples=samples,
        pauses=(),
        counter_start=0,
        counter_end=600,
        source_frame_count=600,
    )
    assert manual.status == "rejected" and manual.error is not None
    rejected = map_event_to_source_frame(
        event_counter=700,
        event_ns=1,
        manual=False,
        samples=samples,
        pauses=(),
        counter_start=0,
        counter_end=600,
        source_frame_count=600,
    )
    assert rejected.status == "rejected" and rejected.error is not None
