from __future__ import annotations

from dataclasses import dataclass
from pathlib import PureWindowsPath

import pytest

from matrix_auto_cutter.phase2.win32_port import (
    CREATE_NEW,
    ERROR_ALREADY_EXISTS,
    ERROR_FILE_NOT_FOUND,
    ERROR_PATH_NOT_FOUND,
    ERROR_SHARING_VIOLATION,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_NORMAL,
    OPEN_ALWAYS,
    OPEN_EXISTING,
    OwnedHandle,
    RawFileInfo,
    Win32Err,
    Win32Failure,
    Win32Ok,
    Win32Result,
)


@dataclass
class Node:
    path: str
    attributes: int
    data: bytearray
    file_id: bytes | None
    volume: int = 1
    filesystem: str = "NTFS"
    drive_type: int = 3
    creation: int = 10
    write_time: int = 20
    change_time: int = 30
    delete_pending: bool = False


class FakePort:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.handles: dict[int, tuple[str, int]] = {}
        self.exclusive: set[str] = set()
        self.next_handle = 100
        self.next_id = 1
        self.failures: dict[str, list[int]] = {}
        self.close_results: dict[str, list[int | None]] = {}
        self.close_attempts: dict[str, int] = {}
        self.close_attempts_by_handle: dict[int, int] = {}
        self.open_history: list[tuple[int, str]] = []
        self.delete_close_failures: list[int] = []
        self.partial_write: int | None = None
        self.on_move = None
        self.local_path = r"C:\Local"
        self._mkdir("C:\\")
        self._mkdir(r"C:\Local")

    @staticmethod
    def _dos(path: str) -> str:
        return path[4:] if path.startswith("\\\\?\\") else path

    @classmethod
    def _key(cls, path: str) -> str:
        return cls._dos(path).upper()

    def _error(self, operation: str, default: int | None = None) -> Win32Err | None:
        queued = self.failures.get(operation, [])
        if queued:
            code = queued.pop(0)
            return Win32Err(Win32Failure(code, operation, f"error {code}"))
        if default is not None:
            return Win32Err(Win32Failure(default, operation, f"error {default}"))
        return None

    def _new_id(self) -> bytes:
        value = self.next_id.to_bytes(16, "little")
        self.next_id += 1
        return value

    def _mkdir(self, path: str, *, attributes: int = FILE_ATTRIBUTE_DIRECTORY) -> Node:
        canonical = str(PureWindowsPath(path))
        if canonical.endswith(":"):
            canonical += "\\"
        node = Node(canonical, attributes | FILE_ATTRIBUTE_DIRECTORY, bytearray(), self._new_id())
        self.nodes[self._key(canonical)] = node
        return node

    def add_file(
        self, path: str, data: bytes = b"data", *, attributes: int = FILE_ATTRIBUTE_NORMAL
    ) -> Node:
        parent = str(PureWindowsPath(path).parent)
        if self._key(parent) not in self.nodes:
            self.make_tree(parent)
        node = Node(str(PureWindowsPath(path)), attributes, bytearray(data), self._new_id())
        self.nodes[self._key(path)] = node
        return node

    def make_tree(self, path: str) -> None:
        parts = PureWindowsPath(path).parts
        current = parts[0]
        for part in parts[1:]:
            current = str(PureWindowsPath(current) / part)
            if self._key(current) not in self.nodes:
                self._mkdir(current)

    def create_directory(self, long_path: str) -> Win32Result[None]:
        failed = self._error("CreateDirectoryW")
        if failed is not None:
            return failed
        key = self._key(long_path)
        if key in self.nodes:
            return self._error("CreateDirectoryW", ERROR_ALREADY_EXISTS)  # type: ignore[return-value]
        parent = self._key(str(PureWindowsPath(self._dos(long_path)).parent))
        if parent not in self.nodes:
            return self._error("CreateDirectoryW", ERROR_PATH_NOT_FOUND)  # type: ignore[return-value]
        self._mkdir(self._dos(long_path))
        return Win32Ok(None)

    def open_file(
        self,
        long_path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags: int,
    ) -> Win32Result[OwnedHandle]:
        del desired_access, flags
        failed = self._error("CreateFileW")
        if failed is not None:
            return failed
        key = self._key(long_path)
        node = self.nodes.get(key)
        if creation_disposition == CREATE_NEW:
            if node is not None:
                return self._error("CreateFileW", ERROR_ALREADY_EXISTS)  # type: ignore[return-value]
            node = self.add_file(self._dos(long_path), b"")
        elif creation_disposition == OPEN_ALWAYS:
            if key in self.exclusive:
                return self._error("CreateFileW", ERROR_SHARING_VIOLATION)  # type: ignore[return-value]
            if node is None:
                node = self.add_file(self._dos(long_path), b"")
        elif creation_disposition == OPEN_EXISTING and node is None:
            return self._error("CreateFileW", ERROR_FILE_NOT_FOUND)  # type: ignore[return-value]
        elif creation_disposition == OPEN_EXISTING and key in self.exclusive:
            return self._error("CreateFileW", ERROR_SHARING_VIOLATION)  # type: ignore[return-value]
        assert node is not None
        value = self.next_handle
        self.next_handle += 1
        self.handles[value] = (key, 0)
        self.open_history.append((value, key))
        if share_mode == 0:
            self.exclusive.add(key)

        def close(raw: int) -> Win32Result[None]:
            handle_key, _ = self.handles[raw]
            self.close_attempts[handle_key] = self.close_attempts.get(handle_key, 0) + 1
            self.close_attempts_by_handle[raw] = self.close_attempts_by_handle.get(raw, 0) + 1
            queued_close = self.close_results.get(handle_key, [])
            node_before_close = self.nodes.get(handle_key)
            if (
                node_before_close is not None
                and node_before_close.delete_pending
                and self.delete_close_failures
            ):
                injected_code = self.delete_close_failures.pop(0)
            else:
                injected_code = queued_close.pop(0) if queued_close else None
            failed_close = (
                Win32Err(Win32Failure(injected_code, "CloseHandle", f"error {injected_code}"))
                if injected_code is not None
                else self._error("CloseHandle")
            )
            if failed_close is not None:
                return failed_close
            self.handles.pop(raw)
            self.exclusive.discard(handle_key)
            node_after_close = self.nodes.get(handle_key)
            if node_after_close is not None and node_after_close.delete_pending:
                still_open = any(key == handle_key for key, _ in self.handles.values())
                if not still_open:
                    del self.nodes[handle_key]
            return Win32Ok(None)

        return Win32Ok(OwnedHandle(value, close))

    def query_file_info(self, handle: OwnedHandle) -> Win32Result[RawFileInfo]:
        failed = self._error("GetFileInformationByHandleEx")
        if failed is not None:
            return failed
        key, _ = self.handles[handle.value]
        node = self.nodes[key]
        return Win32Ok(
            RawFileInfo(
                node.attributes,
                len(node.data),
                node.creation,
                node.write_time,
                node.change_time,
                node.volume,
                node.file_id,
                "\\\\?\\" + node.path,
                node.filesystem,
                node.drive_type,
                number_of_links=1,
            )
        )

    def write_file(self, handle: OwnedHandle, data: bytes) -> Win32Result[int]:
        failed = self._error("WriteFile")
        if failed is not None:
            return failed
        key, offset = self.handles[handle.value]
        count = min(len(data), self.partial_write) if self.partial_write is not None else len(data)
        node = self.nodes[key]
        node.data[offset : offset + count] = data[:count]
        self.handles[handle.value] = (key, offset + count)
        return Win32Ok(count)

    def read_file(self, handle: OwnedHandle, maximum_bytes: int) -> Win32Result[bytes]:
        failed = self._error("ReadFile")
        if failed is not None:
            return failed
        key, offset = self.handles[handle.value]
        data = bytes(self.nodes[key].data[offset : offset + maximum_bytes])
        self.handles[handle.value] = (key, offset + len(data))
        return Win32Ok(data)

    def flush_file(self, handle: OwnedHandle) -> Win32Result[None]:
        del handle
        return self._error("FlushFileBuffers") or Win32Ok(None)

    def move_no_replace(self, source_long: str, target_long: str) -> Win32Result[None]:
        failed = self._error("MoveFileExW")
        if failed is not None:
            return failed
        if self.on_move is not None:
            self.on_move(self, source_long, target_long)
        source = self._key(source_long)
        target = self._key(target_long)
        if target in self.nodes:
            return self._error("MoveFileExW", ERROR_ALREADY_EXISTS)  # type: ignore[return-value]
        node = self.nodes.pop(source)
        node.path = self._dos(target_long)
        self.nodes[target] = node
        return Win32Ok(None)

    def replace_file(
        self, target_long: str, replacement_long: str, backup_long: str | None
    ) -> Win32Result[None]:
        del backup_long
        failed = self._error("ReplaceFileW")
        if failed is not None:
            return failed
        target = self._key(target_long)
        replacement = self._key(replacement_long)
        node = self.nodes.pop(replacement)
        node.path = self._dos(target_long)
        self.nodes[target] = node
        return Win32Ok(None)

    def delete_file(self, long_path: str) -> Win32Result[None]:
        failed = self._error("DeleteFileW")
        if failed is not None:
            return failed
        key = self._key(long_path)
        if key not in self.nodes:
            return self._error("DeleteFileW", ERROR_FILE_NOT_FOUND)  # type: ignore[return-value]
        del self.nodes[key]
        return Win32Ok(None)

    def delete_file_handle(self, handle: OwnedHandle) -> Win32Result[None]:
        failed = self._error("SetFileInformationByHandle")
        if failed is not None:
            return failed
        key, _ = self.handles[handle.value]
        if key not in self.nodes:
            return self._error("SetFileInformationByHandle", ERROR_FILE_NOT_FOUND)  # type: ignore[return-value]
        self.nodes[key].delete_pending = True
        return Win32Ok(None)

    def local_app_data(self) -> Win32Result[str]:
        failed = self._error("SHGetKnownFolderPath")
        return failed or Win32Ok(self.local_path)

    def process_identity(self) -> Win32Result[tuple[int, int]]:
        failed = self._error("GetProcessTimes")
        return failed or Win32Ok((123, 456))

    def ordinal_case_key(self, value: str) -> Win32Result[str]:
        failed = self._error("LCMapStringEx")
        return failed or Win32Ok(value.upper())


@pytest.fixture
def fake_port() -> FakePort:
    return FakePort()
