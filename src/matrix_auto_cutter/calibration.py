"""Reine rationale Counter-/QPC-Kalibrierung ohne Datei- oder Medienzugriff."""

from __future__ import annotations

from bisect import bisect_left
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import pairwise
from typing import Literal

from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import CalibrationSample, CanonicalModel, PauseMeasurement


def _round_half_up(value: Fraction) -> int:
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _decimal(value: Fraction) -> Decimal:
    with localcontext() as context:
        context.prec = 28
        return Decimal(value.numerator) / Decimal(value.denominator)


def affine_counter_frame(
    event_counter: int,
    counter_start: int,
    counter_end: int,
    source_frame_count: int,
) -> int:
    """Mappe einen Counter rational und runde deterministisch auf den nächsten Frame."""
    if counter_start < 0 or counter_end <= counter_start or source_frame_count <= 0:
        msg = "Ungültige affine Kalibrierungsanker."
        raise ValueError(msg)
    if not counter_start <= event_counter <= counter_end:
        msg = "Eventcounter liegt außerhalb der Kalibrierungsanker."
        raise ValueError(msg)
    value = Fraction(
        (event_counter - counter_start) * source_frame_count, counter_end - counter_start
    )
    return min(source_frame_count, _round_half_up(value))


def subtract_paused_ns(
    start_ns: int,
    end_ns: int,
    pauses: tuple[PauseMeasurement, ...],
) -> int:
    """Ziehe nur die mit ``[start_ns,end_ns)`` überlappende reale Pause ab."""
    if start_ns < 0 or end_ns < start_ns:
        msg = "Ungültiges QPC-Intervall."
        raise ValueError(msg)
    paused = 0
    previous_end = -1
    for pause in sorted(pauses, key=lambda item: item.start_ns):
        if pause.start_ns < previous_end:
            msg = "Pauseintervalle dürfen nicht überlappen."
            raise ValueError(msg)
        previous_end = pause.end_ns
        overlap_start = max(start_ns, pause.start_ns)
        overlap_end = min(end_ns, pause.end_ns)
        if overlap_start < overlap_end:
            paused += overlap_end - overlap_start
    return end_ns - start_ns - paused


def _active_position(
    origin_ns: int,
    target_ns: int,
    pauses: tuple[PauseMeasurement, ...],
) -> int:
    return subtract_paused_ns(origin_ns, target_ns, pauses)


def map_qpc_frame(
    event_ns: int,
    samples: tuple[CalibrationSample, ...],
    pauses: tuple[PauseMeasurement, ...],
    counter_start: int,
    counter_end: int,
    source_frame_count: int,
) -> int:
    """Interpoliere QPC stückweise auf Counter und danach auf Sourceframes."""
    ordered = sorted(samples, key=lambda item: item.monotonic_ns)
    if (
        len(ordered) < 2
        or event_ns < ordered[0].monotonic_ns
        or event_ns > ordered[-1].monotonic_ns
    ):
        msg = "QPC-Fallback benötigt zwei umschließende Kalibrierungsproben."
        raise ValueError(msg)
    left = ordered[0]
    right = ordered[-1]
    right_index = max(1, bisect_left([item.monotonic_ns for item in ordered], event_ns))
    left, right = ordered[right_index - 1], ordered[right_index]
    active_span = _active_position(left.monotonic_ns, right.monotonic_ns, pauses)
    active_event = _active_position(left.monotonic_ns, event_ns, pauses)
    if active_span <= 0 or right.output_frame_count < left.output_frame_count:
        msg = "Unbrauchbare QPC-Kalibrierungsproben."
        raise ValueError(msg)
    interpolated = Fraction(
        left.output_frame_count * active_span
        + (right.output_frame_count - left.output_frame_count) * active_event,
        active_span,
    )
    counter_value = _round_half_up(interpolated)
    return affine_counter_frame(counter_value, counter_start, counter_end, source_frame_count)


def calculate_drift_ppm(active_elapsed_ns: int, counter_frames: int) -> Decimal:
    """Berechne absolute QPC-vs-Counter-Drift gegenüber CFR 60/1."""
    if active_elapsed_ns <= 0 or counter_frames <= 0:
        msg = "Drift benötigt positive Zeit und Counterspanne."
        raise ValueError(msg)
    expected_ns = Fraction(counter_frames * 1_000_000_000, 60)
    drift = abs(Fraction(active_elapsed_ns) - expected_ns) / expected_ns * 1_000_000
    return _decimal(drift)


def calibration_residual_ms(predicted_frame: Fraction, observed_frame: Fraction) -> Decimal:
    """Berechne eine Frameabweichung bei 60 FPS in Millisekunden."""
    return _decimal(abs(predicted_frame - observed_frame) * Fraction(1000, 60))


def sample_gaps_valid(
    samples: tuple[CalibrationSample, ...],
    pauses: tuple[PauseMeasurement, ...] = (),
) -> bool:
    """Prüfe aktive Abstände auf höchstens fünf Sekunden."""
    ordered = sorted(samples, key=lambda item: item.monotonic_ns)
    if len(ordered) < 2:
        return False
    return all(
        subtract_paused_ns(first.monotonic_ns, second.monotonic_ns, pauses) <= 5_000_000_000
        and second.output_frame_count >= first.output_frame_count
        for first, second in pairwise(ordered)
    )


def calculate_event_uncertainty_ms(
    *,
    manual: bool,
    max_residual_ms: Decimal,
    qpc_fallback: bool,
) -> Decimal:
    """Summiere Callback-, Residual-, Ein-Frame- und optional QPC-Budget."""
    if max_residual_ms < 0:
        msg = "Residual darf nicht negativ sein."
        raise ValueError(msg)
    base = Decimal(150 if manual else 100)
    frame_budget = Decimal(1000) / Decimal(60)
    fallback_budget = Decimal(50 if qpc_fallback else 0)
    return base + max_residual_ms + frame_budget + fallback_budget


class EventMappingResult(CanonicalModel):
    """Strukturiertes Ergebnis einer erwartbar fehlschlagenden Eventabbildung."""

    status: Literal["mapped", "rejected"]
    source_frame: int | None = None
    error: CoreError | None = None


def map_event_to_source_frame(
    *,
    event_counter: int | None,
    event_ns: int,
    manual: bool,
    samples: tuple[CalibrationSample, ...],
    pauses: tuple[PauseMeasurement, ...],
    counter_start: int,
    counter_end: int,
    source_frame_count: int,
) -> EventMappingResult:
    """Nutze Counter primär und QPC nur als expliziten Fallback."""
    if manual and event_counter is None:
        return EventMappingResult(
            status="rejected",
            error=core_error(
                ErrorCode.SIDECAR_CLOCK_UNRELIABLE,
                {"reason": "manual_marker_without_counter"},
            ),
        )
    try:
        frame = (
            affine_counter_frame(event_counter, counter_start, counter_end, source_frame_count)
            if event_counter is not None
            else map_qpc_frame(
                event_ns,
                samples,
                pauses,
                counter_start,
                counter_end,
                source_frame_count,
            )
        )
    except ValueError as exc:
        return EventMappingResult(
            status="rejected",
            error=core_error(ErrorCode.SIDECAR_CLOCK_UNRELIABLE, {"reason": str(exc)}),
        )
    return EventMappingResult(status="mapped", source_frame=frame)
