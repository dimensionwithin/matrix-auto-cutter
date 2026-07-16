"""Native package-2C adapter built on the package-2A Win32 implementation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from matrix_auto_cutter.phase2.win32_native import (
    FILE_STANDARD_INFO,
    FILE_STANDARD_INFO_CLASS,
    NativeWin32Port,
    _failure,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Ok, Win32Result

FILE_BEGIN = 0


class NativeCloseGateWin32Port(NativeWin32Port):
    """Native adapter adding only FileStandardInfo.DeletePending."""

    def _configure_signatures(self) -> None:
        super()._configure_signatures()
        self._kernel32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        self._kernel32.SetFilePointerEx.restype = wintypes.BOOL

    def query_delete_pending(self, handle: OwnedHandle) -> Win32Result[bool]:
        """Query reliable delete-pending state on the already-held source handle."""
        standard = FILE_STANDARD_INFO()
        if not self._query(handle, FILE_STANDARD_INFO_CLASS, standard):
            return _failure("GetFileInformationByHandleEx:FileStandardInfo")
        return Win32Ok(bool(standard.DeletePending))

    def set_file_offset(self, handle: OwnedHandle, offset: int) -> Win32Result[int]:
        """Position the held synchronous source handle at an exact byte offset."""
        position = ctypes.c_longlong()
        if not self._kernel32.SetFilePointerEx(
            wintypes.HANDLE(handle.value),
            ctypes.c_longlong(offset),
            ctypes.byref(position),
            FILE_BEGIN,
        ):
            return _failure("SetFilePointerEx")
        return Win32Ok(int(position.value))
