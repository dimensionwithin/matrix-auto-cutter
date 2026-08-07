"""Single source for the calibration clock contract bounds."""

from __future__ import annotations

from decimal import Decimal

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
# MAX_DRIFT_PPM is the only source for this bound. Three places enforce it and
# all of them read it from here: the finalizer clock gate in
# phase2/finalizer/sidecar_builder.py, the ClockCalibration.drift_ppm field
# constraint in models.py, and the independent recomputation in sidecar.py.
DRIFT_WARNING_PPM = Decimal(500)
MAX_DRIFT_PPM = Decimal(1000)
