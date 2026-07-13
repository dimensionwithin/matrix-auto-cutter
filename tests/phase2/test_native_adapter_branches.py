from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from matrix_auto_cutter.phase2.win32_native import (
    FILE_STANDARD_INFO,
    NativeWin32Port,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Err, Win32Ok


class KernelStub:
    def __init__(self) -> None:
        self.fail: set[str] = set()
        self.query_fail_class: int | None = None
        self.final_written = 12
        self.directory = True
        self.map_second_fails = False
        self.file_id_unsupported = False
        self.final_sizes: list[int] = []

    def CreateDirectoryW(self, path, security):
        del path, security
        return "CreateDirectoryW" not in self.fail

    def CloseHandle(self, handle):
        del handle
        return "CloseHandle" not in self.fail

    def CreateFileW(self, path, access, share, security, disposition, flags, template):
        del path, access, share, security, disposition, flags, template
        if "CreateFileW-invalid" in self.fail:
            return ctypes.c_void_p(-1).value
        if "CreateFileW-null" in self.fail:
            return None
        return 123

    def GetFileInformationByHandleEx(self, handle, info_class, output, size):
        del handle, size
        if self.query_fail_class == info_class:
            ctypes.set_last_error(999)
            return False
        if info_class == 18 and self.file_id_unsupported:
            ctypes.set_last_error(87)
            return False
        obj = output._obj
        if info_class == 0:
            obj.CreationTime = 1
            obj.LastWriteTime = 2
            obj.ChangeTime = 3
            obj.FileAttributes = 0
        elif info_class == 1:
            obj.EndOfFile = 7
            obj.Directory = self.directory
        else:
            obj.VolumeSerialNumber = 9
            for index in range(16):
                obj.FileId.Identifier[index] = index
        return True

    def GetFinalPathNameByHandleW(self, handle, buffer, size, mode):
        del handle, mode
        if "GetFinal-needed" in self.fail and buffer is None:
            return 0
        if buffer is None:
            return 12
        if "GetFinal-written" in self.fail:
            return 0
        if "GetFinal-overflow" in self.fail:
            return size
        if self.final_sizes:
            value = self.final_sizes.pop(0)
            if value >= size:
                return value
        buffer.value = r"\\?\C:\file"
        return len(buffer.value)

    def GetFileType(self, handle):
        del handle
        if "GetFileType" in self.fail:
            ctypes.set_last_error(806)
            return 0
        return 1

    def SetFileInformationByHandle(self, handle, info_class, value, size):
        del handle, info_class, value, size
        return "SetFileInformationByHandle" not in self.fail

    def GetVolumePathNameW(self, final, buffer, size):
        del final, size
        if "GetVolumePathNameW" in self.fail:
            return False
        buffer.value = "C:\\"
        return True

    def GetVolumeInformationW(
        self, root, label, label_size, serial, max_component, flags, fs, fs_size
    ):
        del root, label, label_size, max_component, flags, fs_size
        if "GetVolumeInformationW" in self.fail:
            return False
        serial._obj.value = 9
        fs.value = "NTFS"
        return True

    def GetDriveTypeW(self, root):
        del root
        return 3

    def WriteFile(self, handle, buffer, size, written, overlapped):
        del handle, buffer, overlapped
        if "WriteFile" in self.fail:
            return False
        written._obj.value = size
        return True

    def ReadFile(self, handle, buffer, size, read, overlapped):
        del handle, overlapped
        if "ReadFile" in self.fail:
            return False
        payload = b"abc"[:size]
        ctypes.memmove(buffer, payload, len(payload))
        read._obj.value = len(payload)
        return True

    def FlushFileBuffers(self, handle):
        del handle
        return "FlushFileBuffers" not in self.fail

    def MoveFileExW(self, source, target, flags):
        del source, target, flags
        return "MoveFileExW" not in self.fail

    def ReplaceFileW(self, target, replacement, backup, flags, exclude, reserved):
        del target, replacement, backup, flags, exclude, reserved
        return "ReplaceFileW" not in self.fail

    def DeleteFileW(self, path):
        del path
        return "DeleteFileW" not in self.fail

    def GetCurrentProcess(self):
        return 1

    def GetProcessTimes(self, process, creation, exit_time, kernel, user):
        del process, exit_time, kernel, user
        if "GetProcessTimes" in self.fail:
            return False
        creation._obj.dwHighDateTime = 1
        creation._obj.dwLowDateTime = 2
        return True

    def GetCurrentProcessId(self):
        return 42

    def LCMapStringEx(
        self, locale, flags, value, length, buffer, buffer_length, version, reserved, sort
    ):
        del locale, flags, length, buffer_length, version, reserved, sort
        if "LCMapStringEx-first" in self.fail and buffer is None:
            return 0
        if buffer is None:
            return len(value)
        if self.map_second_fails:
            return 0
        for index, character in enumerate(value.upper()):
            buffer[index] = character
        return len(value)


class ShellStub:
    def __init__(self, status: int) -> None:
        self.status = status

    def SHGetKnownFolderPath(self, folder, flags, token, result):
        del folder, flags, token, result
        return self.status


def stub_port(kernel: KernelStub | None = None, shell_status: int = 1) -> NativeWin32Port:
    port = object.__new__(NativeWin32Port)
    port._kernel32 = kernel or KernelStub()
    port._shell32 = ShellStub(shell_status)
    port._ole32 = SimpleNamespace(CoTaskMemFree=lambda value: None)
    return port


def handle() -> OwnedHandle:
    return OwnedHandle(123, lambda value: Win32Ok(None))


def test_native_simple_success_and_failure_methods(monkeypatch) -> None:
    kernel = KernelStub()
    port = stub_port(kernel)
    assert isinstance(port.create_directory(r"\\?\C:\x"), Win32Ok)
    assert isinstance(port.open_file("x", 0, 0, 3, 0), Win32Ok)
    assert isinstance(port.write_file(handle(), b"abc"), Win32Ok)
    assert port.read_file(handle(), 3).value == b"abc"
    assert isinstance(port.flush_file(handle()), Win32Ok)
    assert isinstance(port.move_no_replace("a", "b"), Win32Ok)
    assert isinstance(port.replace_file("a", "b", None), Win32Ok)
    assert isinstance(port.delete_file("a"), Win32Ok)
    assert isinstance(port.delete_file_handle(handle()), Win32Ok)
    assert port.process_identity().value == (42, (1 << 32) | 2)
    assert port.ordinal_case_key("Abc").value == "ABC"

    for operation, call in (
        ("CreateDirectoryW", lambda: port.create_directory("x")),
        ("CloseHandle", lambda: port._close(1)),
        ("WriteFile", lambda: port.write_file(handle(), b"x")),
        ("ReadFile", lambda: port.read_file(handle(), 1)),
        ("FlushFileBuffers", lambda: port.flush_file(handle())),
        ("MoveFileExW", lambda: port.move_no_replace("a", "b")),
        ("ReplaceFileW", lambda: port.replace_file("a", "b", None)),
        ("DeleteFileW", lambda: port.delete_file("a")),
        ("SetFileInformationByHandle", lambda: port.delete_file_handle(handle())),
        ("GetProcessTimes", port.process_identity),
    ):
        kernel.fail.add(operation)
        assert isinstance(call(), Win32Err)
        kernel.fail.remove(operation)

    kernel.fail.add("CreateFileW-invalid")
    assert isinstance(port.open_file("x", 0, 0, 3, 0), Win32Err)
    kernel.fail.clear()
    kernel.fail.add("CreateFileW-null")
    assert isinstance(port.open_file("x", 0, 0, 3, 0), Win32Err)

    monkeypatch.delattr(ctypes, "WinDLL")
    with pytest.raises(OSError):
        NativeWin32Port()


@pytest.mark.parametrize(
    "failure",
    [
        "query-0",
        "query-1",
        "query-18",
        "GetFinal-needed",
        "GetFinal-written",
        "GetFinal-overflow",
        "GetVolumePathNameW",
        "GetVolumeInformationW",
    ],
)
def test_native_query_failure_stages(failure: str) -> None:
    kernel = KernelStub()
    if failure.startswith("query-"):
        kernel.query_fail_class = int(failure.split("-")[1])
    else:
        kernel.fail.add(failure)
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Err)


def test_native_query_directory_and_regular_success() -> None:
    kernel = KernelStub()
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Ok)
    assert result.value.is_directory and result.value.size_bytes == 7
    kernel.directory = False
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Ok)
    assert not result.value.is_directory
    kernel.file_id_unsupported = True
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Ok)
    assert result.value.file_id_128 is None
    kernel.fail.add("GetFileType")
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Err)
    assert result.error.code == 806


def test_file_standard_info_sdk_layout() -> None:
    assert ctypes.sizeof(FILE_STANDARD_INFO) == 24
    assert FILE_STANDARD_INFO.AllocationSize.offset == 0
    assert FILE_STANDARD_INFO.EndOfFile.offset == 8
    assert FILE_STANDARD_INFO.NumberOfLinks.offset == 16
    assert FILE_STANDARD_INFO.DeletePending.offset == 20
    assert FILE_STANDARD_INFO.Directory.offset == 21


def test_final_path_resize_growth_equal_capacity_and_exhaustion(monkeypatch) -> None:
    kernel = KernelStub()
    kernel.final_sizes = [13, 20]
    assert isinstance(stub_port(kernel).query_file_info(handle()), Win32Ok)

    kernel = KernelStub()
    kernel.final_sizes = [13, 20, 30, 40]
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Err)
    assert result.error.operation.endswith("retry_exhausted")

    kernel = KernelStub()
    kernel.fail.add("GetFinal-written")
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 1234)
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Err)
    assert result.error.code == 1234

    kernel = KernelStub()
    kernel.final_written = 40000
    original_final = kernel.GetFinalPathNameByHandleW

    def oversized(handle_value, buffer, size, mode):
        if buffer is None:
            return 40000
        return original_final(handle_value, buffer, size, mode)

    kernel.GetFinalPathNameByHandleW = oversized
    result = stub_port(kernel).query_file_info(handle())
    assert isinstance(result, Win32Err)
    assert result.error.operation.endswith("size_limit")


def test_native_localappdata_and_case_mapping_failures() -> None:
    assert isinstance(NativeWin32Port().local_app_data(), Win32Ok)
    assert isinstance(stub_port(shell_status=-1).local_app_data(), Win32Err)
    assert isinstance(stub_port(shell_status=0).local_app_data(), Win32Err)
    kernel = KernelStub()
    kernel.fail.add("LCMapStringEx-first")
    assert isinstance(stub_port(kernel).ordinal_case_key("x"), Win32Err)
    kernel.fail.clear()
    kernel.map_second_fails = True
    assert isinstance(stub_port(kernel).ordinal_case_key("x"), Win32Err)
