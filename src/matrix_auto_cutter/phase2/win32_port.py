"""Typed injectable boundary for package-2A Win32 operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

ERROR_ACCESS_DENIED = 5
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_ALREADY_EXISTS = 183
ERROR_FILE_EXISTS = 80
ERROR_SHARING_VIOLATION = 32
ERROR_LOCK_VIOLATION = 33
ERROR_INSUFFICIENT_BUFFER = 122

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
DELETE = 0x00010000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
CREATE_NEW = 1
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_ATTRIBUTE_DIRECTORY = 0x00000010
FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_OFFLINE = 0x00001000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
FILE_TYPE_DISK = 1


@dataclass(frozen=True, slots=True)
class Win32Failure:
    """Unmodified native failure from one adapter operation."""

    code: int
    operation: str
    detail: str


@dataclass(frozen=True, slots=True)
class Win32Ok[T]:
    """Successful raw adapter result."""

    value: T


@dataclass(frozen=True, slots=True)
class Win32Err:
    """Failed raw adapter result."""

    error: Win32Failure


type Win32Result[T] = Win32Ok[T] | Win32Err


class HandleState(StrEnum):
    """Explicit single-owner handle lifecycle."""

    OPEN = "open"
    CLOSE_SUCCEEDED = "close_succeeded"
    CLOSE_FAILED_OR_UNKNOWN = "close_failed_or_unknown"


class OwnedHandle:
    """Single-owner native handle with closed-handle guards."""

    __slots__ = ("_closer", "_state", "_value")

    def __init__(self, value: int, closer: Callable[[int], Win32Result[None]]) -> None:
        """Take exclusive ownership of one native handle value."""
        self._value = value
        self._closer = closer
        self._state = HandleState.OPEN

    @property
    def value(self) -> int:
        """Return the native value while it remains owned."""
        if self._state is not HandleState.OPEN:
            raise RuntimeError("native handle is closed")
        return self._value

    @property
    def closed(self) -> bool:
        """Report whether the raw value is no longer safe to use."""
        return self._state is not HandleState.OPEN

    @property
    def state(self) -> HandleState:
        """Return the explicit close outcome without implying successful release."""
        return self._state

    def close(self) -> Win32Result[None]:
        """Close exactly once; double-close is a programming error."""
        if self._state is not HandleState.OPEN:
            raise RuntimeError("native handle was closed twice")
        result = self._closer(self._value)
        self._state = (
            HandleState.CLOSE_FAILED_OR_UNKNOWN
            if isinstance(result, Win32Err)
            else HandleState.CLOSE_SUCCEEDED
        )
        return result

    def __enter__(self) -> OwnedHandle:
        """Enter an owned-handle context."""
        if self.closed:
            raise RuntimeError("native handle is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close on context exit."""
        self.close()


@dataclass(frozen=True, slots=True)
class RawFileInfo:
    """Raw handle-derived file evidence."""

    attributes: int
    size_bytes: int
    creation_time_100ns: int
    last_write_time_100ns: int
    change_time_100ns: int
    volume_serial: int
    file_id_128: bytes | None
    final_dos_path: str
    filesystem_name: str
    drive_type: int
    file_type: int = FILE_TYPE_DISK
    number_of_links: int | None = None

    @property
    def is_directory(self) -> bool:
        """Return the raw directory attribute."""
        return bool(self.attributes & FILE_ATTRIBUTE_DIRECTORY)

    @property
    def is_reparse(self) -> bool:
        """Return the raw reparse attribute."""
        return bool(self.attributes & FILE_ATTRIBUTE_REPARSE_POINT)


class Win32Port(Protocol):
    """All OS operations needed by package 2A."""

    # Callable attributes keep this boundary purely structural at runtime: there
    # are no executable Protocol bodies whose only purpose would be coverage.
    create_directory: Callable[[str], Win32Result[None]]
    open_file: Callable[[str, int, int, int, int], Win32Result[OwnedHandle]]
    query_file_info: Callable[[OwnedHandle], Win32Result[RawFileInfo]]
    write_file: Callable[[OwnedHandle, bytes], Win32Result[int]]
    read_file: Callable[[OwnedHandle, int], Win32Result[bytes]]
    flush_file: Callable[[OwnedHandle], Win32Result[None]]
    move_no_replace: Callable[[str, str], Win32Result[None]]
    replace_file: Callable[[str, str, str | None], Win32Result[None]]
    delete_file: Callable[[str], Win32Result[None]]
    delete_file_handle: Callable[[OwnedHandle], Win32Result[None]]
    local_app_data: Callable[[], Win32Result[str]]
    process_identity: Callable[[], Win32Result[tuple[int, int]]]
    ordinal_case_key: Callable[[str], Win32Result[str]]
