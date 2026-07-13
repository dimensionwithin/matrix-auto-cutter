"""Synchronous bounded progress delivery."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Condition, RLock, get_ident
from types import MappingProxyType
from uuid import UUID

type ProgressScalar = str | int | float | bool
type ProgressListener = Callable[["ProgressEvent"], None]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """Immutable progress event."""

    operation_id: UUID
    sequence: int
    kind: str
    payload: Mapping[str, ProgressScalar]


@dataclass(frozen=True, slots=True)
class ListenerDiagnostic:
    """Bounded secondary listener failure."""

    sequence: int
    listener_name: str
    error_type: str
    detail: str


@dataclass(frozen=True, slots=True)
class ProgressEmissionRejected:
    """Bounded rejection of a reentrant emission attempt."""

    reason: str
    active_sequence: int


class ProgressReporter:
    """Deliver progress synchronously with natural backpressure."""

    def __init__(self, operation_id: UUID, *, diagnostic_limit: int = 32) -> None:
        """Create a reporter for one operation."""
        if diagnostic_limit < 1:
            raise ValueError("diagnostic_limit must be positive")
        self._operation_id = operation_id
        self._condition = Condition(RLock())
        self._sequence = 0
        self._next_delivery = 1
        self._delivery_owner: int | None = None
        self._listeners: list[ProgressListener] = []
        self._diagnostics: deque[ListenerDiagnostic] = deque(maxlen=diagnostic_limit)

    def add_listener(self, listener: ProgressListener) -> None:
        """Register a listener for subsequent stable snapshots."""
        with self._condition:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: ProgressListener) -> None:
        """Remove a listener if present."""
        with self._condition:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def emit(
        self, kind: str, payload: Mapping[str, ProgressScalar]
    ) -> ProgressEvent | ProgressEmissionRejected:
        """Allocate a sequence and synchronously deliver one event."""
        if not kind or len(kind) > 64 or len(payload) > 32:
            raise ValueError("progress kind or payload exceeds bounds")
        copied = dict(payload)
        if any(len(key) > 64 for key in copied):
            raise ValueError("progress payload key exceeds bounds")
        if any(isinstance(value, str) and len(value) > 1024 for value in copied.values()):
            raise ValueError("progress payload value exceeds bounds")
        thread_id = get_ident()
        with self._condition:
            if self._delivery_owner == thread_id:
                rejection = ProgressEmissionRejected("reentrant_emit_rejected", self._sequence)
                self._diagnostics.append(
                    ListenerDiagnostic(
                        self._sequence,
                        "ProgressReporter",
                        "ReentrantEmitRejected",
                        rejection.reason,
                    )
                )
                return rejection
            self._sequence += 1
            event = ProgressEvent(
                self._operation_id,
                self._sequence,
                kind,
                MappingProxyType(copied),
            )
            listeners = tuple(self._listeners)
            self._condition.notify_all()
            while event.sequence != self._next_delivery or self._delivery_owner is not None:
                self._condition.wait()
            self._delivery_owner = thread_id
        try:
            for listener in listeners:
                try:
                    listener(event)
                except Exception as exc:  # Listener isolation is the public contract.
                    diagnostic = ListenerDiagnostic(
                        event.sequence,
                        _safe_listener_name(listener),
                        _safe_type_name(exc),
                        _safe_exception_detail(exc),
                    )
                    with self._condition:
                        self._diagnostics.append(diagnostic)
        finally:
            with self._condition:
                self._delivery_owner = None
                self._next_delivery += 1
                self._condition.notify_all()
        return event

    def diagnostics(self) -> tuple[ListenerDiagnostic, ...]:
        """Return a stable snapshot of secondary failures."""
        with self._condition:
            return tuple(self._diagnostics)


def _safe_listener_name(listener: object) -> str:
    try:
        value = getattr(listener, "__name__", type(listener).__name__)
        return str(value)[:128]
    except BaseException:
        return "<listener-name-unavailable>"


def _safe_type_name(exc: BaseException) -> str:
    try:
        return str(type(exc).__name__)[:128]
    except BaseException:
        return "<exception-type-unavailable>"


def _safe_exception_detail(exc: BaseException) -> str:
    try:
        return str(exc)[:1024]
    except BaseException:
        try:
            return repr(exc)[:1024]
        except BaseException:
            return "<exception-detail-unavailable>"
