"""Injectable monotonic clock and cancellable wait boundary for package 2C."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from matrix_auto_cutter.phase2.cancellation import CancellationToken

MINIMUM_STABILITY_INTERVAL_SECONDS = 1.0


class WaitClockPort(Protocol):
    """Clock/wait operations used by the S0-S2 stability window."""

    monotonic: Callable[[], float]
    wait: Callable[[CancellationToken, float], bool]


class SystemWaitClock:
    """Real monotonic, cancellation-aware Windows/runtime wait implementation."""

    @staticmethod
    def monotonic() -> float:
        """Return the process monotonic clock."""
        return time.monotonic()

    @staticmethod
    def wait(cancellation: CancellationToken, seconds: float) -> bool:
        """Wait once for cancellation or the requested timeout."""
        return cancellation.wait(seconds)
