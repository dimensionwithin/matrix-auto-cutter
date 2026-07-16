"""Package-2C extension of the established injectable package-2A Win32 port."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from matrix_auto_cutter.phase2.win32_port import (
    OwnedHandle,
    Win32Failure,
    Win32Port,
    Win32Result,
)

ERROR_INVALID_HANDLE = 6
ERROR_DELETE_PENDING = 303
STATUS_DELETE_PENDING = 0xC0000056


@dataclass(frozen=True, slots=True)
class CloseGateWin32Failure(Win32Failure):
    """Optional native status evidence attached to an existing Win32 failure."""

    ntstatus_code: int | None = None


class CloseGateWin32Port(Win32Port, Protocol):
    """The package-2A port plus reliable handle-bound delete-pending evidence."""

    query_delete_pending: Callable[[OwnedHandle], Win32Result[bool]]


def ntstatus_from_failure(error: Win32Failure) -> int | None:
    """Return optional NTSTATUS evidence without requiring it from package 2A."""
    value = getattr(error, "ntstatus_code", None)
    return value if isinstance(value, int) else None
