"""Native ctypes implementation of the package-2A Win32 port."""

from __future__ import annotations

import ctypes
from collections.abc import Sequence
from ctypes import wintypes
from typing import Any, ClassVar, Final, cast
from uuid import UUID

from matrix_auto_cutter.phase2.win32_port import (
    ERROR_INSUFFICIENT_BUFFER,
    FILE_ATTRIBUTE_DIRECTORY,
    OwnedHandle,
    RawFileInfo,
    Win32Err,
    Win32Failure,
    Win32Ok,
    Win32Result,
)

INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
FILE_BASIC_INFO_CLASS: Final = 0
FILE_STANDARD_INFO_CLASS: Final = 1
FILE_DISPOSITION_INFO_CLASS: Final = 4
FILE_ID_INFO_CLASS: Final = 18
ERROR_INVALID_FUNCTION: Final = 1
ERROR_NOT_SUPPORTED: Final = 50
ERROR_INVALID_PARAMETER: Final = 87
MOVEFILE_WRITE_THROUGH: Final = 0x00000008
REPLACEFILE_WRITE_THROUGH: Final = 0x00000001
VOLUME_NAME_DOS: Final = 0
KF_FLAG_DEFAULT: Final = 0
LCMAP_UPPERCASE: Final = 0x00000200
LOCALE_NAME_INVARIANT: Final = ""
MAX_FINAL_PATH_ATTEMPTS: Final = 4
MAX_FINAL_PATH_CAPACITY: Final = 32768


class FILE_BASIC_INFO(ctypes.Structure):
    """Windows FILE_BASIC_INFO."""

    _fields_: ClassVar[Sequence[tuple[str, type[Any]]]] = [
        ("CreationTime", ctypes.c_longlong),
        ("LastAccessTime", ctypes.c_longlong),
        ("LastWriteTime", ctypes.c_longlong),
        ("ChangeTime", ctypes.c_longlong),
        ("FileAttributes", wintypes.DWORD),
    ]


class FILE_STANDARD_INFO(ctypes.Structure):
    """Windows FILE_STANDARD_INFO."""

    _fields_: ClassVar[Sequence[tuple[str, type[Any]]]] = [
        ("AllocationSize", ctypes.c_longlong),
        ("EndOfFile", ctypes.c_longlong),
        ("NumberOfLinks", wintypes.DWORD),
        ("DeletePending", ctypes.c_ubyte),
        ("Directory", ctypes.c_ubyte),
    ]


class FILE_DISPOSITION_INFO(ctypes.Structure):
    """Windows FILE_DISPOSITION_INFO."""

    _fields_: ClassVar[Sequence[tuple[str, type[Any]]]] = [
        ("DeleteFile", ctypes.c_ubyte),
    ]


class FILE_ID_128(ctypes.Structure):
    """Windows FILE_ID_128."""

    _fields_: ClassVar[Sequence[tuple[str, type[Any]]]] = [("Identifier", ctypes.c_ubyte * 16)]


class FILE_ID_INFO(ctypes.Structure):
    """Windows FILE_ID_INFO."""

    _fields_: ClassVar[Sequence[tuple[str, type[Any]]]] = [
        ("VolumeSerialNumber", ctypes.c_ulonglong),
        ("FileId", FILE_ID_128),
    ]


def _failure(operation: str) -> Win32Err:
    code = ctypes.get_last_error()
    return Win32Err(Win32Failure(code, operation, ctypes.FormatError(code)))


class NativeWin32Port:
    """Thin native adapter without domain classification."""

    def __init__(self) -> None:
        """Load DLLs and configure every used native signature."""
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("NativeWin32Port requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        self._configure_signatures()

    def _configure_signatures(self) -> None:
        k32 = self._kernel32
        k32.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID]
        k32.CreateDirectoryW.restype = wintypes.BOOL
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.GetFileInformationByHandleEx.restype = wintypes.BOOL
        k32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetFileInformationByHandle.restype = wintypes.BOOL
        k32.GetFileType.argtypes = [wintypes.HANDLE]
        k32.GetFileType.restype = wintypes.DWORD
        k32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        k32.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        k32.GetVolumePathNameW.restype = wintypes.BOOL
        k32.GetVolumeInformationW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        k32.GetVolumeInformationW.restype = wintypes.BOOL
        k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        k32.GetDriveTypeW.restype = wintypes.UINT
        k32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.WriteFile.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.ReadFile.restype = wintypes.BOOL
        k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
        k32.FlushFileBuffers.restype = wintypes.BOOL
        k32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
        k32.MoveFileExW.restype = wintypes.BOOL
        k32.ReplaceFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        k32.ReplaceFileW.restype = wintypes.BOOL
        k32.DeleteFileW.argtypes = [wintypes.LPCWSTR]
        k32.DeleteFileW.restype = wintypes.BOOL
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetCurrentProcessId.argtypes = []
        k32.GetCurrentProcessId.restype = wintypes.DWORD
        k32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        k32.GetProcessTimes.restype = wintypes.BOOL
        k32.LCMapStringEx.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.LPCWSTR,
            ctypes.c_int,
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.LPARAM,
        ]
        k32.LCMapStringEx.restype = ctypes.c_int
        self._shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(ctypes.c_byte * 16),
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self._shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        self._ole32.CoTaskMemFree.argtypes = [wintypes.LPVOID]
        self._ole32.CoTaskMemFree.restype = None

    def create_directory(self, long_path: str) -> Win32Result[None]:
        """Call CreateDirectoryW exactly once."""
        if not self._kernel32.CreateDirectoryW(long_path, None):
            return _failure("CreateDirectoryW")
        return Win32Ok(None)

    def _close(self, value: int) -> Win32Result[None]:
        if not self._kernel32.CloseHandle(wintypes.HANDLE(value)):
            return _failure("CloseHandle")
        return Win32Ok(None)

    def open_file(
        self,
        long_path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags: int,
    ) -> Win32Result[OwnedHandle]:
        """Call CreateFileW and return an owned handle."""
        raw = self._kernel32.CreateFileW(
            long_path, desired_access, share_mode, None, creation_disposition, flags, None
        )
        value = cast(int | None, raw)
        if value == INVALID_HANDLE_VALUE:
            return _failure("CreateFileW")
        if value is None:
            return Win32Err(Win32Failure(0, "CreateFileW", "unexpected null handle"))
        return Win32Ok(OwnedHandle(value, self._close))

    def _query(self, handle: OwnedHandle, info_class: int, value: ctypes.Structure) -> bool:
        return bool(
            self._kernel32.GetFileInformationByHandleEx(
                wintypes.HANDLE(handle.value), info_class, ctypes.byref(value), ctypes.sizeof(value)
            )
        )

    def query_file_info(self, handle: OwnedHandle) -> Win32Result[RawFileInfo]:
        """Query attributes, timestamps, ID, final path and volume facts."""
        basic = FILE_BASIC_INFO()
        standard = FILE_STANDARD_INFO()
        file_id = FILE_ID_INFO()
        for info_class, value in (
            (FILE_BASIC_INFO_CLASS, basic),
            (FILE_STANDARD_INFO_CLASS, standard),
        ):
            if not self._query(handle, info_class, value):
                return _failure("GetFileInformationByHandleEx")
        file_id_available = self._query(handle, FILE_ID_INFO_CLASS, file_id)
        if not file_id_available and ctypes.get_last_error() not in {
            ERROR_INVALID_FUNCTION,
            ERROR_NOT_SUPPORTED,
            ERROR_INVALID_PARAMETER,
        }:
            return _failure("GetFileInformationByHandleEx")
        final_path_result = self._get_final_path(handle)
        if isinstance(final_path_result, Win32Err):
            return final_path_result
        final_path = final_path_result.value
        volume_buffer = ctypes.create_unicode_buffer(32768)
        if not self._kernel32.GetVolumePathNameW(final_path, volume_buffer, len(volume_buffer)):
            return _failure("GetVolumePathNameW")
        fs_buffer = ctypes.create_unicode_buffer(64)
        serial = wintypes.DWORD()
        max_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        if not self._kernel32.GetVolumeInformationW(
            volume_buffer.value,
            None,
            0,
            ctypes.byref(serial),
            ctypes.byref(max_component),
            ctypes.byref(flags),
            fs_buffer,
            len(fs_buffer),
        ):
            return _failure("GetVolumeInformationW")
        attributes = int(basic.FileAttributes)
        if standard.Directory:
            attributes |= FILE_ATTRIBUTE_DIRECTORY
        ctypes.set_last_error(0)
        file_type = int(self._kernel32.GetFileType(wintypes.HANDLE(handle.value)))
        if file_type == 0 and ctypes.get_last_error() != 0:
            return _failure("GetFileType")
        return Win32Ok(
            RawFileInfo(
                attributes=attributes,
                size_bytes=int(standard.EndOfFile),
                creation_time_100ns=int(basic.CreationTime),
                last_write_time_100ns=int(basic.LastWriteTime),
                change_time_100ns=int(basic.ChangeTime),
                volume_serial=int(serial.value),
                file_id_128=bytes(file_id.FileId.Identifier) if file_id_available else None,
                final_dos_path=final_path,
                filesystem_name=fs_buffer.value,
                drive_type=int(self._kernel32.GetDriveTypeW(volume_buffer.value)),
                file_type=file_type,
            )
        )

    def _get_final_path(self, handle: OwnedHandle) -> Win32Result[str]:
        """Read a complete final path with bounded resize/retry semantics."""
        capacity = self._kernel32.GetFinalPathNameByHandleW(
            wintypes.HANDLE(handle.value), None, 0, VOLUME_NAME_DOS
        )
        if capacity == 0:
            return _failure("GetFinalPathNameByHandleW:initial_size")
        capacity = int(capacity) + 1
        for _attempt in range(MAX_FINAL_PATH_ATTEMPTS):
            if capacity > MAX_FINAL_PATH_CAPACITY:
                return Win32Err(
                    Win32Failure(
                        ERROR_INSUFFICIENT_BUFFER,
                        "GetFinalPathNameByHandleW:size_limit",
                        "final path exceeds bounded capacity",
                    )
                )
            buffer = ctypes.create_unicode_buffer(capacity)
            written = int(
                self._kernel32.GetFinalPathNameByHandleW(
                    wintypes.HANDLE(handle.value), buffer, capacity, VOLUME_NAME_DOS
                )
            )
            if written == 0:
                return _failure("GetFinalPathNameByHandleW:read")
            if written < capacity:
                return Win32Ok(buffer.value)
            capacity = written + 1
        return Win32Err(
            Win32Failure(
                ERROR_INSUFFICIENT_BUFFER,
                "GetFinalPathNameByHandleW:retry_exhausted",
                "final path size changed during every bounded retry",
            )
        )

    def write_file(self, handle: OwnedHandle, data: bytes) -> Win32Result[int]:
        """Write one bounded byte buffer."""
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(data)
        if not self._kernel32.WriteFile(
            wintypes.HANDLE(handle.value), buffer, len(data), ctypes.byref(written), None
        ):
            return _failure("WriteFile")
        return Win32Ok(int(written.value))

    def read_file(self, handle: OwnedHandle, maximum_bytes: int) -> Win32Result[bytes]:
        """Read at most a caller-bounded number of bytes from current offset."""
        buffer = ctypes.create_string_buffer(maximum_bytes)
        read = wintypes.DWORD()
        if not self._kernel32.ReadFile(
            wintypes.HANDLE(handle.value), buffer, maximum_bytes, ctypes.byref(read), None
        ):
            return _failure("ReadFile")
        return Win32Ok(buffer.raw[: read.value])

    def flush_file(self, handle: OwnedHandle) -> Win32Result[None]:
        """Flush file data."""
        if not self._kernel32.FlushFileBuffers(wintypes.HANDLE(handle.value)):
            return _failure("FlushFileBuffers")
        return Win32Ok(None)

    def move_no_replace(self, source_long: str, target_long: str) -> Win32Result[None]:
        """Atomically move without replacement."""
        if not self._kernel32.MoveFileExW(source_long, target_long, MOVEFILE_WRITE_THROUGH):
            return _failure("MoveFileExW")
        return Win32Ok(None)

    def replace_file(
        self, target_long: str, replacement_long: str, backup_long: str | None
    ) -> Win32Result[None]:
        """Atomically replace an existing file."""
        if not self._kernel32.ReplaceFileW(
            target_long,
            replacement_long,
            backup_long,
            REPLACEFILE_WRITE_THROUGH,
            None,
            None,
        ):
            return _failure("ReplaceFileW")
        return Win32Ok(None)

    def delete_file(self, long_path: str) -> Win32Result[None]:
        """Delete one caller-proven owned path."""
        if not self._kernel32.DeleteFileW(long_path):
            return _failure("DeleteFileW")
        return Win32Ok(None)

    def delete_file_handle(self, handle: OwnedHandle) -> Win32Result[None]:
        """Mark the exact open object for deletion without reopening its path."""
        disposition = FILE_DISPOSITION_INFO(1)
        if not self._kernel32.SetFileInformationByHandle(
            wintypes.HANDLE(handle.value),
            FILE_DISPOSITION_INFO_CLASS,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            return _failure("SetFileInformationByHandle")
        return Win32Ok(None)

    def local_app_data(self) -> Win32Result[str]:
        """Resolve FOLDERID_LocalAppData for the current user."""
        folder_id = (ctypes.c_byte * 16).from_buffer_copy(
            UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091").bytes_le
        )
        result = wintypes.LPWSTR()
        status = self._shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), KF_FLAG_DEFAULT, None, ctypes.byref(result)
        )
        if status != 0:
            return Win32Err(Win32Failure(status & 0xFFFFFFFF, "SHGetKnownFolderPath", str(status)))
        try:
            value = result.value
            if value is None:
                return Win32Err(Win32Failure(0, "SHGetKnownFolderPath", "unexpected null path"))
            return Win32Ok(value)
        finally:
            self._ole32.CoTaskMemFree(result)

    def process_identity(self) -> Win32Result[tuple[int, int]]:
        """Return process ID and exact creation FILETIME ticks."""
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        process = self._kernel32.GetCurrentProcess()
        if not self._kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return _failure("GetProcessTimes")
        ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return Win32Ok((int(self._kernel32.GetCurrentProcessId()), ticks))

    def ordinal_case_key(self, value: str) -> Win32Result[str]:
        """Map text with Windows invariant uppercase rules for stable keys."""
        needed = self._kernel32.LCMapStringEx(
            LOCALE_NAME_INVARIANT, LCMAP_UPPERCASE, value, len(value), None, 0, None, None, 0
        )
        if needed == 0:
            return _failure("LCMapStringEx")
        buffer = ctypes.create_unicode_buffer(needed)
        written = self._kernel32.LCMapStringEx(
            LOCALE_NAME_INVARIANT,
            LCMAP_UPPERCASE,
            value,
            len(value),
            buffer,
            len(buffer),
            None,
            None,
            0,
        )
        if written == 0:
            return _failure("LCMapStringEx")
        return Win32Ok("".join(buffer[:written]))
