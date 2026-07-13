"""Thread-safe monotone cancellation with a linearized commit boundary."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock


@dataclass(frozen=True, slots=True)
class CommitPermit:
    """Proof that an irreversible commit linearized before cancellation."""

    sequence: int


class CancellationToken:
    """Monotone cancellation state shared by cooperating threads."""

    def __init__(self) -> None:
        """Create a fresh non-cancelled token."""
        self._lock = Lock()
        self._event = Event()
        self._commit_sequence = 0

    def cancel(self) -> bool:
        """Linearize cancellation; return whether this call changed state."""
        with self._lock:
            changed = not self._event.is_set()
            self._event.set()
            return changed

    @property
    def is_cancelled(self) -> bool:
        """Return the monotone cancellation state."""
        return self._event.is_set()

    def wait(self, timeout_seconds: float | None = None) -> bool:
        """Wait for cancellation without resetting it."""
        return self._event.wait(timeout_seconds)

    def begin_irreversible_commit(self) -> CommitPermit | None:
        """Atomically order cancellation against an irreversible publish."""
        with self._lock:
            if self._event.is_set():
                return None
            self._commit_sequence += 1
            return CommitPermit(self._commit_sequence)
