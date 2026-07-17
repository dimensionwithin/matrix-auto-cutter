"""Closed package-2F runtime state machine."""

from __future__ import annotations

from itertools import pairwise

from matrix_auto_cutter.phase2.finalizer.models import FinalizerStateName

_TERMINAL = {
    FinalizerStateName.FINALIZED,
    FinalizerStateName.CANCELLED,
    FinalizerStateName.FAILED,
    FinalizerStateName.QUARANTINED,
}
_LINEAR = (
    FinalizerStateName.DISCOVERED,
    FinalizerStateName.VALIDATING_INPUT,
    FinalizerStateName.RESOLVING_SOURCE,
    FinalizerStateName.AWAITING_CLOSE,
    FinalizerStateName.PROBING,
    FinalizerStateName.HASHING,
    FinalizerStateName.CONFIRMING_IDENTITY,
    FinalizerStateName.PREPARING_INTENT,
    FinalizerStateName.CONSTRUCTING_SIDECAR,
    FinalizerStateName.COMMITTING_SIDECAR,
    FinalizerStateName.FINALIZED,
)
_NEXT = dict(pairwise(_LINEAR))


class FinalizerStateInvariantError(RuntimeError):
    """Raised when a caller attempts a non-normative state transition."""

    pass


class FinalizerStateMachine:
    """Closed monotone runtime state machine for one finalizer operation."""

    def __init__(self) -> None:
        """Start one operation in the normative discovered state."""
        self._state = FinalizerStateName.DISCOVERED
        self._history = [self._state]

    @property
    def state(self) -> FinalizerStateName:
        """Return the current normative state."""
        return self._state

    @property
    def history(self) -> tuple[FinalizerStateName, ...]:
        """Return the immutable transition history."""
        return tuple(self._history)

    def transition(self, target: FinalizerStateName) -> None:
        """Apply one allowed transition or reject it without mutation."""
        if self._state in _TERMINAL:
            raise FinalizerStateInvariantError("terminal finalizer state cannot transition")
        if target not in _TERMINAL and _NEXT.get(self._state) is not target:
            raise FinalizerStateInvariantError(
                f"forbidden finalizer transition {self._state.value}->{target.value}"
            )
        if (
            target is FinalizerStateName.FINALIZED
            and self._state is not FinalizerStateName.COMMITTING_SIDECAR
        ):
            raise FinalizerStateInvariantError("finalized requires committing_sidecar")
        self._state = target
        self._history.append(target)
