"""Pure package-2C result, error, and recheck contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from matrix_auto_cutter.phase2.snapshots import FileSnapshot

if TYPE_CHECKING:
    from matrix_auto_cutter.phase2.close_gate.lease import CloseGateLease


class CloseGateErrorCode(StrEnum):
    """Stable package-2C error codes."""

    BUSY = "E_CLOSE_GATE_BUSY"
    INACCESSIBLE = "E_CLOSE_GATE_INACCESSIBLE"
    DISAPPEARED = "E_CLOSE_GATE_DISAPPEARED"
    DELETE_PENDING = "E_CLOSE_GATE_DELETE_PENDING"
    UNSUPPORTED = "E_CLOSE_GATE_UNSUPPORTED"
    UNSTABLE = "E_CLOSE_GATE_UNSTABLE"
    WIN32_UNKNOWN = "E_CLOSE_GATE_WIN32_UNKNOWN"
    CANCELLED = "E_CLOSE_GATE_CANCELLED"
    LEASE_CLOSED = "E_CLOSE_GATE_LEASE_CLOSED"


class CloseGateErrorCategory(StrEnum):
    """Stable package-2C failure categories."""

    CONCURRENCY = "concurrency"
    ACCESS = "access"
    INPUT = "input"
    POLICY = "policy"
    INTEGRITY = "integrity"
    IO = "io"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class CloseGateDiagnostic:
    """Secondary bounded diagnostic that never replaces a primary failure."""

    phase: str
    message: str
    win32_code: int | None = None
    ntstatus_code: int | None = None
    cause: BaseException | None = None


@dataclass(frozen=True, slots=True)
class CloseGateFailure:
    """Structured primary package-2C failure with native evidence."""

    code: CloseGateErrorCode
    category: CloseGateErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    ntstatus_code: int | None = None
    cause: BaseException | None = None
    underlying: object | None = None
    retryable: bool = False
    cleanup_diagnostics: tuple[CloseGateDiagnostic, ...] = ()

    def with_cleanup(self, diagnostics: tuple[CloseGateDiagnostic, ...]) -> CloseGateFailure:
        """Append bounded cleanup diagnostics while preserving this primary failure."""
        return replace(
            self,
            cleanup_diagnostics=(*self.cleanup_diagnostics, *diagnostics)[:8],
        )


@dataclass(frozen=True, slots=True)
class CloseGateClosed:
    """Successful S0-S2 gate result and its authentic open lease."""

    lease: CloseGateLease


@dataclass(frozen=True, slots=True)
class CloseGateBusy:
    """A proven sharing or lock violation on the gate/ownership object."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateInaccessible:
    """Access was denied and is never classified as busy."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateDisappeared:
    """The safely referenced source disappeared before gate completion."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateDeletePending:
    """Reliable native evidence says deletion is pending."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateUnsupported:
    """Path, type, filesystem, or identity evidence is insufficient."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateUnstable:
    """S0-S2 did not prove one unchanged file instance."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateUnknownWin32Error:
    """Unknown native, adapter, or invariant failure; never guessed into another class."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class CloseGateCancelled:
    """Controlled cancellation before lease publication."""

    error: CloseGateFailure


CloseGateResult = (
    CloseGateClosed
    | CloseGateBusy
    | CloseGateInaccessible
    | CloseGateDisappeared
    | CloseGateDeletePending
    | CloseGateUnsupported
    | CloseGateUnstable
    | CloseGateUnknownWin32Error
    | CloseGateCancelled
)


@dataclass(frozen=True, slots=True)
class RecheckOk:
    """A new unnamed lease-bound snapshot matches the gate evidence."""

    snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class RecheckUnstable:
    """The unnamed recheck no longer matches the gate evidence."""

    error: CloseGateFailure
    snapshot: FileSnapshot | None = None


@dataclass(frozen=True, slots=True)
class RecheckDeletePending:
    """The held source handle now reports delete-pending."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class RecheckUnsupported:
    """The recheck lacks sufficient safe instance evidence."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class RecheckUnknownWin32Error:
    """The recheck encountered an unclassified native or invariant failure."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class RecheckCancelled:
    """Cancellation linearized before recheck publication."""

    error: CloseGateFailure


@dataclass(frozen=True, slots=True)
class RecheckClosed:
    """Close linearized before recheck publication or the lease was already closed."""

    error: CloseGateFailure


RecheckResult = (
    RecheckOk
    | RecheckUnstable
    | RecheckDeletePending
    | RecheckUnsupported
    | RecheckUnknownWin32Error
    | RecheckCancelled
    | RecheckClosed
)
