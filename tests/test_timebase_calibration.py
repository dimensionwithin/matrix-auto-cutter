"""Tests der rationalen Zeitbasis und reinen Kalibrierung."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from matrix_auto_cutter.calibration import (
    FrameLoss,
    affine_counter_frame,
    calculate_drift_ppm,
    calculate_event_uncertainty_ms,
    calibration_residual_ms,
    detect_frame_losses,
    estimate_drift_ppm,
    map_event_to_source_frame,
    map_qpc_frame,
    sample_gaps_valid,
    subtract_paused_ns,
)
from matrix_auto_cutter.clock_bounds import (
    DRIFT_WARNING_PPM,
    MAX_DRIFT_PPM,
    minimum_measurable_ns,
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


def recording(
    seconds: float,
    *,
    drift_ppm: int = 0,
    losses: tuple[tuple[float, int], ...] = (),
    interval_s: float = 2.0,
) -> tuple[CalibrationSample, ...]:
    """Baue eine Kalibrierreihe mit gleichmäßiger Drift und punktuellen Verlusten.

    ``drift_ppm`` lässt Counter und QPC gleichmäßig auseinanderlaufen,
    ``losses`` entfernt zu einem Zeitpunkt schlagartig Frames — die beiden
    physikalisch verschiedenen Ursachen, die die alte Endpunktformel in eine
    einzige Zahl geworfen hat.
    """
    samples = []
    step_ns = int(interval_s * 1_000_000_000)
    for index in range(int(seconds / interval_s) + 1):
        monotonic_ns = index * step_ns
        counted = monotonic_ns / 1_000_000_000 * 60 * (1 - drift_ppm / 1_000_000)
        lost = sum(frames for at_s, frames in losses if monotonic_ns >= at_s * 1_000_000_000)
        samples.append(sample(monotonic_ns, round(counted) - lost))
    return tuple(samples)


def reported(losses: tuple[FrameLoss, ...]) -> list[tuple[int, int]]:
    return [(round(item.active_seconds), item.frames) for item in losses]


def test_drift_is_a_slope_over_the_calibration_series_not_an_endpoint() -> None:
    """Halte die Semantik von `drift_ppm` fest: Steigung, nicht Endpunkt.

    Die Zahl ist der Theil-Sen-Median aller paarweisen Steigungen des
    Counterrückstands über die Kalibrierreihe. Sie misst, ob QPC und
    Framecounter *auseinanderlaufen*. Sie misst ausdrücklich nicht mehr, wie
    weit der Counter im Moment des Stops zurücklag — das war die alte Formel,
    und sie hat den Interleaver-Rückstand als Uhrendrift ausgewiesen.

    Punktueller Frameverlust gehört nicht in diese Zahl. Er wird absolut
    gemeldet, in Frames und mit Zeitpunkt, und lässt den Lauf nicht scheitern.
    """
    # Gesunde Uhr, egal wie lang: die Steigung ist null.
    for seconds in (60, 300, 1037):
        assert estimate_drift_ppm(recording(seconds)) == 0

    # Ein punktueller Verlust verschiebt die Steigung nicht, er wird separat
    # und absolut gemeldet.
    with_loss = recording(1037, losses=((996.5, 42),))
    assert estimate_drift_ppm(with_loss) == 0
    assert reported(detect_frame_losses(with_loss)) == [(996, 42)]

    # Echte Drift schlägt weiterhin voll durch.
    assert estimate_drift_ppm(recording(300, drift_ppm=800)) == pytest.approx(800, abs=1)


def test_measured_drift_survives_the_real_frame_loss_recordings() -> None:
    """Die beiden Aufnahmen, die an der alten Formel gescheitert sind.

    89c344e6 vom 07.08.2026: 411 s, ein Sprung von 45 Frames beim Wechsel zur
    Szene „Charts" bei 44 s. Die alte Endpunktformel maß 1257 ppm und lehnte
    den Lauf mit E_JOURNAL_CORRUPT ab, obwohl die Uhr exakt lief.
    ff2618be vom selben Tag: 1037 s, 42 Frames bei 996 s, alte Formel 739 ppm.
    """
    charts = recording(411.4, losses=((44.4, 45),))
    assert estimate_drift_ppm(charts) == 0
    assert reported(detect_frame_losses(charts)) == [(44, 45)]

    outro = recording(1036.6, losses=((996.2, 42),))
    assert estimate_drift_ppm(outro) == 0
    assert reported(detect_frame_losses(outro)) == [(996, 42)]


def test_short_healthy_recording_is_below_the_measurable_duration() -> None:
    """51afb549 vom 03.08.2026: zehn Sekunden, gesund, darf nicht gegatet werden."""
    short = recording(10.1)
    assert estimate_drift_ppm(short) == 0
    assert detect_frame_losses(short) == ()
    assert minimum_measurable_ns(MAX_DRIFT_PPM) > 10_100_000_000


def test_minimum_measurable_duration_follows_the_bounds() -> None:
    # 16,7 s für 1000 ppm, 33,3 s für 500 ppm — abgeleitet, nicht gesetzt.
    assert minimum_measurable_ns(MAX_DRIFT_PPM) == 16_666_666_666
    assert minimum_measurable_ns(DRIFT_WARNING_PPM) == 33_333_333_333
    assert minimum_measurable_ns(MAX_DRIFT_PPM * 2) == minimum_measurable_ns(MAX_DRIFT_PPM) // 2
    with pytest.raises(ValueError):
        minimum_measurable_ns(Decimal(0))


def test_steady_clock_drift_still_trips_the_gate() -> None:
    """Ohne diesen Test hätten wir die Prüfung nur abgeschafft."""
    healthy = recording(300)
    assert estimate_drift_ppm(healthy) <= DRIFT_WARNING_PPM

    warning = recording(300, drift_ppm=700)
    assert DRIFT_WARNING_PPM < estimate_drift_ppm(warning) <= MAX_DRIFT_PPM

    rejected = recording(300, drift_ppm=1500)
    assert estimate_drift_ppm(rejected) > MAX_DRIFT_PPM
    # Gleichmäßige Drift ist kein Frameverlust und wird nicht als solcher gemeldet.
    assert detect_frame_losses(rejected) == ()

    # Richtung egal: ein zu schneller Counter zählt genauso.
    assert estimate_drift_ppm(recording(300, drift_ppm=-1500)) > MAX_DRIFT_PPM


def test_drift_estimation_rejects_series_it_cannot_evaluate() -> None:
    with pytest.raises(ValueError):
        estimate_drift_ppm((sample(0, 0),))
    with pytest.raises(ValueError):
        estimate_drift_ppm((sample(0, 0), sample(0, 5)))


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
