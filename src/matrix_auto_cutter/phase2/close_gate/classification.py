"""Exhaustive package-2C error classification without optimistic guessing."""

from __future__ import annotations

from matrix_auto_cutter.phase2.close_gate.contracts import (
    CloseGateBusy,
    CloseGateCancelled,
    CloseGateDeletePending,
    CloseGateDisappeared,
    CloseGateErrorCategory,
    CloseGateErrorCode,
    CloseGateFailure,
    CloseGateInaccessible,
    CloseGateResult,
    CloseGateUnknownWin32Error,
    CloseGateUnstable,
    CloseGateUnsupported,
)
from matrix_auto_cutter.phase2.close_gate.win32_port import (
    ERROR_DELETE_PENDING,
    ERROR_INVALID_HANDLE,
    STATUS_DELETE_PENDING,
    ntstatus_from_failure,
)
from matrix_auto_cutter.phase2.errors import ErrorCode as Phase2ErrorCode
from matrix_auto_cutter.phase2.errors import ErrorDetail
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_ACCESS_DENIED,
    ERROR_FILE_NOT_FOUND,
    ERROR_LOCK_VIOLATION,
    ERROR_PATH_NOT_FOUND,
    ERROR_SHARING_VIOLATION,
    Win32Failure,
)

type CloseGateFailureResult = (
    CloseGateBusy
    | CloseGateInaccessible
    | CloseGateDisappeared
    | CloseGateDeletePending
    | CloseGateUnsupported
    | CloseGateUnstable
    | CloseGateUnknownWin32Error
    | CloseGateCancelled
)

_UNSUPPORTED_2A_CODES = {
    Phase2ErrorCode.PATH_INPUT_FORM,
    Phase2ErrorCode.PATH_COMPONENT_EMPTY,
    Phase2ErrorCode.PATH_DOT_COMPONENT,
    Phase2ErrorCode.PATH_ROOT_ESCAPE,
    Phase2ErrorCode.PATH_ADS,
    Phase2ErrorCode.PATH_UNC,
    Phase2ErrorCode.PATH_DEVICE_NAMESPACE,
    Phase2ErrorCode.PATH_RESERVED_NAME,
    Phase2ErrorCode.PATH_TRAILING_DOT_SPACE,
    Phase2ErrorCode.PATH_CASE_COLLISION,
    Phase2ErrorCode.PATH_UNICODE_ROUNDTRIP,
    Phase2ErrorCode.PATH_REPARSE,
    Phase2ErrorCode.PATH_ROOT_MISMATCH,
    Phase2ErrorCode.PATH_NOT_REGULAR,
    Phase2ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
    Phase2ErrorCode.PATH_UNSAFE,
    Phase2ErrorCode.FILE_NOT_REGULAR,
    Phase2ErrorCode.SNAPSHOT_EVIDENCE_INSUFFICIENT,
}


def _native_cause(error: Win32Failure) -> OSError:
    return OSError(error.code, error.detail)


def _native_failure(
    error: Win32Failure,
    phase: str,
    code: CloseGateErrorCode,
    category: CloseGateErrorCategory,
    *,
    retryable: bool = False,
    message: str | None = None,
) -> CloseGateFailure:
    return CloseGateFailure(
        code,
        category,
        phase,
        message or error.detail,
        win32_code=error.code,
        ntstatus_code=ntstatus_from_failure(error),
        cause=_native_cause(error),
        retryable=retryable,
    )


def classify_native(
    error: Win32Failure,
    phase: str,
    *,
    source_operation: bool = False,
    ownership_operation: bool = False,
) -> CloseGateFailureResult:
    """Classify native evidence only in its proven object context."""
    ntstatus = ntstatus_from_failure(error)
    if source_operation and (
        error.code == ERROR_DELETE_PENDING or ntstatus == STATUS_DELETE_PENDING
    ):
        return CloseGateDeletePending(
            _native_failure(
                error,
                phase,
                CloseGateErrorCode.DELETE_PENDING,
                CloseGateErrorCategory.INPUT,
                retryable=True,
            )
        )
    if (source_operation or ownership_operation) and error.code in {
        ERROR_SHARING_VIOLATION,
        ERROR_LOCK_VIOLATION,
    }:
        return CloseGateBusy(
            _native_failure(
                error,
                phase,
                CloseGateErrorCode.BUSY,
                CloseGateErrorCategory.CONCURRENCY,
                retryable=True,
            )
        )
    if error.code == ERROR_ACCESS_DENIED:
        return CloseGateInaccessible(
            _native_failure(
                error,
                phase,
                CloseGateErrorCode.INACCESSIBLE,
                CloseGateErrorCategory.ACCESS,
            )
        )
    if error.code in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}:
        return CloseGateDisappeared(
            _native_failure(
                error,
                phase,
                CloseGateErrorCode.DISAPPEARED,
                CloseGateErrorCategory.INPUT,
                retryable=True,
            )
        )
    message = (
        "invalid native handle; classification remains unknown"
        if error.code == ERROR_INVALID_HANDLE
        else error.detail
    )
    return CloseGateUnknownWin32Error(
        _native_failure(
            error,
            phase,
            CloseGateErrorCode.WIN32_UNKNOWN,
            CloseGateErrorCategory.IO,
            message=message,
        )
    )


def classify_phase2(
    error: ErrorDetail,
    phase: str | None = None,
    *,
    source_operation: bool = False,
) -> CloseGateFailureResult:
    """Preserve package-2A detail while mapping only closed package-2C classes."""
    effective_phase = phase or error.phase
    if source_operation and error.win32_code == ERROR_DELETE_PENDING:
        return CloseGateDeletePending(
            CloseGateFailure(
                CloseGateErrorCode.DELETE_PENDING,
                CloseGateErrorCategory.INPUT,
                effective_phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
                retryable=True,
            )
        )
    if error.code in {
        Phase2ErrorCode.PROJECT_LOCK_BUSY,
        Phase2ErrorCode.PATH_LOCK_BUSY,
    } or (source_operation and error.win32_code in {ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}):
        return CloseGateBusy(
            CloseGateFailure(
                CloseGateErrorCode.BUSY,
                CloseGateErrorCategory.CONCURRENCY,
                effective_phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
                retryable=True,
            )
        )
    if error.code in {
        Phase2ErrorCode.PATH_ACCESS_DENIED,
        Phase2ErrorCode.LOCK_ACCESS_DENIED,
        Phase2ErrorCode.SNAPSHOT_ACCESS_DENIED,
    }:
        return CloseGateInaccessible(
            CloseGateFailure(
                CloseGateErrorCode.INACCESSIBLE,
                CloseGateErrorCategory.ACCESS,
                effective_phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
            )
        )
    if error.code is Phase2ErrorCode.SNAPSHOT_NOT_FOUND or error.win32_code in {
        ERROR_FILE_NOT_FOUND,
        ERROR_PATH_NOT_FOUND,
    }:
        return CloseGateDisappeared(
            CloseGateFailure(
                CloseGateErrorCode.DISAPPEARED,
                CloseGateErrorCategory.INPUT,
                effective_phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
                retryable=True,
            )
        )
    if error.code in _UNSUPPORTED_2A_CODES:
        return CloseGateUnsupported(
            CloseGateFailure(
                CloseGateErrorCode.UNSUPPORTED,
                CloseGateErrorCategory.POLICY,
                effective_phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
            )
        )
    if error.code is Phase2ErrorCode.CANCELLED:
        return cancelled(effective_phase, underlying=error)
    return CloseGateUnknownWin32Error(
        CloseGateFailure(
            CloseGateErrorCode.WIN32_UNKNOWN,
            CloseGateErrorCategory.IO,
            effective_phase,
            error.message,
            win32_code=error.win32_code,
            cause=error.cause,
            underlying=error,
        )
    )


def cancelled(phase: str, *, underlying: object | None = None) -> CloseGateCancelled:
    """Build the controlled package-2C cancellation result."""
    return CloseGateCancelled(
        CloseGateFailure(
            CloseGateErrorCode.CANCELLED,
            CloseGateErrorCategory.CANCELLED,
            phase,
            "close-gate operation cancelled",
            underlying=underlying,
            retryable=True,
        )
    )


def unsupported(
    phase: str, message: str, *, underlying: object | None = None
) -> CloseGateUnsupported:
    """Build an explicit fail-closed evidence/policy result."""
    return CloseGateUnsupported(
        CloseGateFailure(
            CloseGateErrorCode.UNSUPPORTED,
            CloseGateErrorCategory.POLICY,
            phase,
            message,
            underlying=underlying,
        )
    )


def unknown(
    phase: str, message: str, *, cause: BaseException | None = None
) -> CloseGateUnknownWin32Error:
    """Build a non-native unknown result without optimistic reclassification."""
    return CloseGateUnknownWin32Error(
        CloseGateFailure(
            CloseGateErrorCode.WIN32_UNKNOWN,
            CloseGateErrorCategory.IO,
            phase,
            message,
            cause=cause,
        )
    )


def primary_error(result: CloseGateResult | CloseGateFailureResult) -> CloseGateFailure | None:
    """Return the primary failure from every non-success result."""
    return getattr(result, "error", None)
