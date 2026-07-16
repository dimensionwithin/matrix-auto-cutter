"""Stable package-2B error codes and structured diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from threading import Lock

from matrix_auto_cutter.phase2.errors import ErrorCategory


class ProbeErrorCode(StrEnum):
    """Public package-2B failure codes."""

    BINARY_CHANGED = "E_PROBE_BINARY_CHANGED"
    UNSUPPORTED_VERSION = "E_PROBE_UNSUPPORTED_VERSION"
    VERSION_OUTPUT = "E_PROBE_VERSION_OUTPUT"
    BINARY_ACCESS = "E_PROBE_BINARY_ACCESS"
    BINARY_HASH = "E_PROBE_BINARY_HASH"
    BINARY_EVIDENCE = "E_PROBE_BINARY_EVIDENCE"
    CANCELLED = "E_PROBE_CANCELLED"
    TIMEOUT = "E_PROBE_TIMEOUT"
    START_FAILED = "E_PROBE_START_FAILED"
    OUTPUT_LIMIT = "E_PROBE_OUTPUT_LIMIT"
    PROCESS_FAILED = "E_PROBE_PROCESS_FAILED"
    INVALID_UTF8 = "E_PROBE_INVALID_UTF8"
    INVALID_JSON = "E_PROBE_INVALID_JSON"
    SCHEMA = "E_PROBE_SCHEMA"
    UNSUPPORTED_MEDIA = "E_PROBE_UNSUPPORTED_MEDIA"
    AMBIGUOUS_STREAMS = "E_PROBE_AMBIGUOUS_STREAMS"
    STREAM_INTEGRITY = "E_PROBE_STREAM_INTEGRITY"
    SOURCE_CHANGED = "E_PROBE_SOURCE_CHANGED"
    SOURCE_EVIDENCE_INSUFFICIENT = "E_PROBE_SOURCE_EVIDENCE_INSUFFICIENT"


class ProbeErrorDetail(StrEnum):
    """Closed package-2B detail codes below stable top-level errors."""

    AUDIO_LAYOUT_UNSUPPORTED = "audio_layout_unsupported"


class _TerminalKind(StrEnum):
    """Internal exhaustive terminal-state taxonomy for one probe operation."""

    NONE = "none"
    PROCESS_EXIT_PENDING = "process_exit_pending"
    SUCCESS = "success"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    READER_IO = "reader_io"
    PROCESS_START = "process_start"
    PROCESS_CONTROL = "process_control"
    EXIT_CODE = "exit_code"
    BINARY_INTEGRITY = "binary_integrity"
    POST_SNAPSHOT = "post_snapshot"
    CLEANUP = "cleanup"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ProbeError:
    """A primary package-2B failure with bounded secondary diagnostics."""

    code: ProbeErrorCode
    category: ErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None
    retryable: bool = False
    secondary: tuple[ProbeError, ...] = ()
    detail: ProbeErrorDetail | None = None


def probe_error(
    code: ProbeErrorCode,
    category: ErrorCategory,
    phase: str,
    message: str,
    *,
    win32_code: int | None = None,
    cause: BaseException | None = None,
    retryable: bool = False,
    secondary: tuple[ProbeError, ...] = (),
    detail: ProbeErrorDetail | None = None,
) -> ProbeError:
    """Build one immutable, bounded error value."""
    return ProbeError(
        code,
        category,
        phase,
        message[:1024],
        win32_code,
        cause,
        retryable,
        secondary[:8],
        detail,
    )


class _TerminalLatch:
    """Synchronize the first causal terminal event and retain later diagnostics."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._kind = _TerminalKind.NONE
        self._primary: ProbeError | None = None
        self._secondary: list[ProbeError] = []

    @property
    def kind(self) -> _TerminalKind:
        """Return the currently linearized state."""
        with self._lock:
            return self._kind

    def process_exited(self) -> None:
        """Record process exit as pending, never as publishable success."""
        with self._lock:
            if self._kind is _TerminalKind.NONE:
                self._kind = _TerminalKind.PROCESS_EXIT_PENDING

    def fail(self, kind: _TerminalKind, error: ProbeError) -> bool:
        """Atomically latch the first failure; append every later failure as evidence."""
        if kind in {
            _TerminalKind.NONE,
            _TerminalKind.PROCESS_EXIT_PENDING,
            _TerminalKind.SUCCESS,
        }:
            raise ValueError("fail requires a terminal failure kind")
        with self._lock:
            if self._primary is None and self._kind in {
                _TerminalKind.NONE,
                _TerminalKind.PROCESS_EXIT_PENDING,
            }:
                self._kind = kind
                self._primary = error
                return True
            self._secondary.append(error)
            del self._secondary[8:]
            return False

    def diagnose(self, error: ProbeError) -> None:
        """Append a non-terminal or cleanup diagnostic without changing the outcome."""
        with self._lock:
            self._secondary.append(error)
            del self._secondary[8:]

    def finalize_success(self) -> bool:
        """Publish success only if no terminal failure has linearized."""
        with self._lock:
            if self._primary is not None:
                return False
            if self._kind not in {_TerminalKind.NONE, _TerminalKind.PROCESS_EXIT_PENDING}:
                return False
            self._kind = _TerminalKind.SUCCESS
            return True

    def error(self) -> ProbeError | None:
        """Return the primary error with all bounded secondary evidence attached."""
        with self._lock:
            if self._primary is None:
                return None
            primary = self._primary
            return ProbeError(
                primary.code,
                primary.category,
                primary.phase,
                primary.message,
                primary.win32_code,
                primary.cause,
                primary.retryable,
                (*primary.secondary, *self._secondary)[:8],
                primary.detail,
            )

    def diagnostics(self) -> tuple[ProbeError, ...]:
        """Return secondary diagnostics for successful outcomes."""
        with self._lock:
            return tuple(self._secondary)
