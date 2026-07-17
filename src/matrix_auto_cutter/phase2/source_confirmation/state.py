"""Closed package-2E runtime source-state transition guard."""

from __future__ import annotations

from enum import StrEnum


class SourceState(StrEnum):
    """Normative source states shared with the phase-2 architecture contract."""

    UNKNOWN = "unknown"
    LOCATED = "located"
    AWAITING_CLOSE = "awaiting_close"
    CLOSED = "closed"
    PROBING = "probing"
    PROBED = "probed"
    HASHING = "hashing"
    HASH_COMPLETED = "hash_completed"
    CONFIRMING_IDENTITY = "confirming_identity"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    DISAPPEARED = "disappeared"
    UNSUPPORTED = "unsupported"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SourceStateInvariantError(RuntimeError):
    """An implementation attempted a transition outside the closed 2E graph."""


_FAILURE_TARGETS = {
    SourceState.INVALIDATED,
    SourceState.FAILED,
    SourceState.CANCELLED,
}
_ALLOWED: dict[SourceState, frozenset[SourceState]] = {
    SourceState.CLOSED: frozenset(
        {SourceState.PROBING, SourceState.DISAPPEARED, *_FAILURE_TARGETS}
    ),
    SourceState.PROBING: frozenset(
        {SourceState.PROBED, SourceState.UNSUPPORTED, *_FAILURE_TARGETS}
    ),
    SourceState.PROBED: frozenset(
        {SourceState.HASHING, SourceState.UNSUPPORTED, *_FAILURE_TARGETS}
    ),
    SourceState.HASHING: frozenset({SourceState.HASH_COMPLETED, *_FAILURE_TARGETS}),
    SourceState.HASH_COMPLETED: frozenset({SourceState.CONFIRMING_IDENTITY, *_FAILURE_TARGETS}),
    SourceState.CONFIRMING_IDENTITY: frozenset(
        {SourceState.CONFIRMED, SourceState.DISAPPEARED, *_FAILURE_TARGETS}
    ),
}


class SourceStateMachine:
    """Record exact 2E transitions without accepting caller-provided state text."""

    __slots__ = ("_history", "_state")

    def __init__(self) -> None:
        """Start one immediate 2E operation at its valid leased closed input."""
        self._state = SourceState.CLOSED
        self._history = [SourceState.CLOSED]

    @property
    def state(self) -> SourceState:
        """Return the current runtime state."""
        return self._state

    @property
    def history(self) -> tuple[SourceState, ...]:
        """Return the immutable state sequence observed by this operation."""
        return tuple(self._history)

    def transition(self, target: SourceState) -> None:
        """Apply one allowed transition or raise an invariant failure."""
        if not isinstance(target, SourceState) or target not in _ALLOWED.get(self._state, ()):
            raise SourceStateInvariantError(
                f"forbidden source-state transition: {self._state.value} -> {target!r}"
            )
        self._state = target
        self._history.append(target)
