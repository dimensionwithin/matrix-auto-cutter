"""Native package-2C adapter built on the package-2A Win32 implementation."""

from __future__ import annotations

from matrix_auto_cutter.phase2.win32_native import (
    FILE_STANDARD_INFO,
    FILE_STANDARD_INFO_CLASS,
    NativeWin32Port,
    _failure,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Ok, Win32Result


class NativeCloseGateWin32Port(NativeWin32Port):
    """Native adapter adding only FileStandardInfo.DeletePending."""

    def query_delete_pending(self, handle: OwnedHandle) -> Win32Result[bool]:
        """Query reliable delete-pending state on the already-held source handle."""
        standard = FILE_STANDARD_INFO()
        if not self._query(handle, FILE_STANDARD_INFO_CLASS, standard):
            return _failure("GetFileInformationByHandleEx:FileStandardInfo")
        return Win32Ok(bool(standard.DeletePending))
