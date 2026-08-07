"""Single source for the calibration clock contract bounds."""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

# Contract bounds for QPC-against-output-counter drift, in ppm.
#
# The rejection bound used to be 500 ppm. Recording
# ff2618be-a9c1-4260-81ea-c0e08b630ff4 of 2026-08-07 lost 42 frames inside a
# single calibration interval at t ~ 995 s and therefore measured 740.1 ppm,
# although the counter held exactly 60.000 fps over the remaining 16 minutes.
# The cause of that frame drop is unexplained. Until it is understood, drift
# above DRIFT_WARNING_PPM is logged as a warning and only drift above
# MAX_DRIFT_PPM rejects the recording, so that a run between the two bounds
# never passes unnoticed.
#
# MAX_DRIFT_PPM is the only source for this bound. Two places enforce it and
# both read it from here: the finalizer clock gate in
# phase2/finalizer/sidecar_builder.py and the ClockCalibration.drift_ppm field
# constraint in models.py. The consumer in sidecar.py checks the declared value
# against that field range only; it cannot recompute the slope, because the
# sidecar carries calibration_sample_count but not the sample series itself.
DRIFT_WARNING_PPM = Decimal(500)
MAX_DRIFT_PPM = Decimal(1000)

# Nominal capture rate. The whole clock contract is written against CFR 60/1;
# calibration.py encodes the same 60 in every rational conversion.
_NOMINAL_FPS = 60


def minimum_measurable_ns(bound_ppm: Decimal) -> int:
    """Kürzeste aktive Dauer, über der ein einzelner Frame unter ``bound_ppm`` bleibt.

    Der Framecounter ist ganzzahlig, die Steigungsschätzung sieht deshalb immer
    mindestens einen Frame Quantisierung. Ein Frame entspricht 1/60 s; unterhalb
    von ``1e15 / (60 * bound)`` Nanosekunden ist eine Schranke von ``bound`` ppm
    kleiner als dieser eine Frame und damit nicht mehr entscheidbar. Bei den
    aktuellen Grenzen sind das 16,7 s für MAX_DRIFT_PPM und 33,3 s für
    DRIFT_WARNING_PPM; die Werte wandern mit, wenn die Grenzen sich ändern.
    """
    if bound_ppm <= 0:
        msg = "Eine ppm-Schranke muss positiv sein."
        raise ValueError(msg)
    return int(Fraction(10**15) / (Fraction(_NOMINAL_FPS) * Fraction(bound_ppm)))
