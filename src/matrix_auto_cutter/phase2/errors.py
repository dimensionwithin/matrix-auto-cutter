"""Stable structured error foundation for package 2A."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Public package-2A error codes."""

    WORKSPACE_INVALID = "E_WORKSPACE_INVALID"
    PROJECT_ALREADY_EXISTS = "E_PROJECT_ALREADY_EXISTS"
    PROJECT_ID_COLLISION = "E_PROJECT_ID_COLLISION"
    PROJECT_ID_INVALID = "E_PROJECT_ID_INVALID"
    PROJECT_METADATA_MISSING = "E_PROJECT_METADATA_MISSING"
    PROJECT_METADATA_INVALID = "E_PROJECT_METADATA_INVALID"
    PROJECT_BINDING_MISMATCH = "E_PROJECT_BINDING_MISMATCH"
    PROJECT_ORPHAN = "E_PROJECT_ORPHAN"
    PROJECT_VERSION_UNSUPPORTED = "E_PROJECT_VERSION_UNSUPPORTED"
    PROJECT_OPEN_FAILED = "E_PROJECT_OPEN_FAILED"
    PATH_INPUT_FORM = "E_PATH_INPUT_FORM"
    PATH_COMPONENT_EMPTY = "E_PATH_COMPONENT_EMPTY"
    PATH_DOT_COMPONENT = "E_PATH_DOT_COMPONENT"
    PATH_ROOT_ESCAPE = "E_PATH_ROOT_ESCAPE"
    PATH_ADS = "E_PATH_ADS"
    PATH_UNC = "E_PATH_UNC"
    PATH_DEVICE_NAMESPACE = "E_PATH_DEVICE_NAMESPACE"
    PATH_RESERVED_NAME = "E_PATH_RESERVED_NAME"
    PATH_TRAILING_DOT_SPACE = "E_PATH_TRAILING_DOT_SPACE"
    PATH_CASE_COLLISION = "E_PATH_CASE_COLLISION"
    PATH_UNICODE_ROUNDTRIP = "E_PATH_UNICODE_ROUNDTRIP"
    PATH_REPARSE = "E_PATH_REPARSE"
    PATH_ROOT_MISMATCH = "E_PATH_ROOT_MISMATCH"
    PATH_NOT_REGULAR = "E_PATH_NOT_REGULAR"
    PATH_EVIDENCE_INSUFFICIENT = "E_PATH_EVIDENCE_INSUFFICIENT"
    PATH_ACCESS_DENIED = "E_PATH_ACCESS_DENIED"
    PATH_OS_ERROR = "E_PATH_OS_ERROR"
    PATH_UNSAFE = "E_PATH_UNSAFE"
    PROJECT_LOCK_BUSY = "E_PROJECT_LOCK_BUSY"
    PATH_LOCK_BUSY = "E_PATH_LOCK_BUSY"
    LOCK_ACCESS_DENIED = "E_LOCK_ACCESS_DENIED"
    LOCK_IO = "E_LOCK_IO"
    LOCK_TIMEOUT = "E_LOCK_TIMEOUT"
    SNAPSHOT_NOT_FOUND = "E_SNAPSHOT_NOT_FOUND"
    FILE_NOT_REGULAR = "E_FILE_NOT_REGULAR"
    SNAPSHOT_ACCESS_DENIED = "E_SNAPSHOT_ACCESS_DENIED"
    SNAPSHOT_EVIDENCE_INSUFFICIENT = "E_SNAPSHOT_EVIDENCE_INSUFFICIENT"
    SNAPSHOT_OS_ERROR = "E_SNAPSHOT_OS_ERROR"
    SNAPSHOT_FAILED = "E_SNAPSHOT_FAILED"
    ATOMIC_PUBLISH_FAILED = "E_ATOMIC_PUBLISH_FAILED"
    ATOMIC_PUBLISH_INTEGRITY = "E_ATOMIC_PUBLISH_INTEGRITY"
    CAS_CONFLICT = "E_CAS_CONFLICT"
    CANCELLED = "E_CANCELLED"


class ErrorCategory(StrEnum):
    """Stable high-level failure category."""

    INPUT = "input"
    POLICY = "policy"
    CONCURRENCY = "concurrency"
    ACCESS = "access"
    IO = "io"
    INTEGRITY = "integrity"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ErrorDetail:
    """Structured failure with preserved native evidence."""

    code: ErrorCode
    category: ErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None
    retryable: bool = False


def failure(
    code: ErrorCode,
    category: ErrorCategory,
    phase: str,
    message: str,
    *,
    win32_code: int | None = None,
    cause: BaseException | None = None,
    retryable: bool = False,
) -> ErrorDetail:
    """Build a stable immutable error value."""
    return ErrorDetail(code, category, phase, message, win32_code, cause, retryable)
