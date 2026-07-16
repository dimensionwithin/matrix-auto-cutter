from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

import pytest
from tests.phase2.conftest import FakePort

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import run_close_gate
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, ValidatedPath, validate_path
from matrix_auto_cutter.phase2.win32_port import (
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ,
    GENERIC_READ,
    OPEN_EXISTING,
    OwnedHandle,
    RawFileInfo,
    Win32Err,
    Win32Failure,
    Win32Ok,
    Win32Result,
)

PROJECT_A = "550e8400-e29b-41d4-a716-446655440000"
PROJECT_B = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"
LEASE_ID = UUID("2e157a84-2e31-49d9-b64e-494c24f8f612")


class FakeWaitClock:
    def __init__(self, callbacks: list[Callable[[], None]] | None = None) -> None:
        self.now = 100.0
        self.calls: list[float] = []
        self.callbacks = callbacks or []

    def monotonic(self) -> float:
        return self.now

    def wait(self, cancellation: CancellationToken, seconds: float) -> bool:
        index = len(self.calls)
        self.calls.append(seconds)
        if index < len(self.callbacks):
            self.callbacks[index]()
        self.now += seconds
        return cancellation.is_cancelled


class FakeCloseGatePort(FakePort):
    def __init__(self) -> None:
        super().__init__()
        self.source_open_error: Win32Failure | None = None
        self.source_lock_error: Win32Failure | None = None
        self.source_handle_close_error: Win32Failure | None = None
        self.delete_pending_error: Win32Failure | None = None
        self.delete_pending_override = False
        self.snapshot_errors: dict[int, Win32Failure] = {}
        self.snapshot_callbacks: dict[int, Callable[[], None]] = {}
        self.snapshot_query_count = 0
        self.source_gate_handles: set[int] = set()
        self.detailed_open_history: list[tuple[str, int, int, int, int]] = []
        self.close_events: list[str] = []
        self.volume_available = True

    def open_file(
        self,
        long_path: str,
        desired_access: int,
        share_mode: int,
        creation_disposition: int,
        flags: int,
    ) -> Win32Result[OwnedHandle]:
        dos = self._dos(long_path)
        self.detailed_open_history.append(
            (dos, desired_access, share_mode, creation_disposition, flags)
        )
        is_source_lock = "\\locks\\ownership\\sources\\" in dos.casefold()
        is_gate_open = (
            desired_access == GENERIC_READ
            and share_mode == FILE_SHARE_READ
            and creation_disposition == OPEN_EXISTING
            and flags == FILE_FLAG_OPEN_REPARSE_POINT
            and dos.casefold().endswith(".mp4")
        )
        injected = self.source_lock_error if is_source_lock else None
        if is_gate_open:
            injected = self.source_open_error
        if injected is not None:
            if is_source_lock:
                self.source_lock_error = None
            else:
                self.source_open_error = None
            return Win32Err(injected)
        opened = super().open_file(
            long_path,
            desired_access,
            share_mode,
            creation_disposition,
            flags,
        )
        if isinstance(opened, Win32Err):
            return opened
        original = opened.value
        label = dos

        def close(raw: int) -> Win32Result[None]:
            self.close_events.append(label)
            if is_gate_open and self.source_handle_close_error is not None:
                error = self.source_handle_close_error
                self.source_handle_close_error = None
                return Win32Err(error)
            return original._closer(raw)

        wrapped = OwnedHandle(original.value, close)
        if is_gate_open:
            self.source_gate_handles.add(wrapped.value)
        return Win32Ok(wrapped)

    def query_file_info(self, handle: OwnedHandle) -> Win32Result[RawFileInfo]:
        if handle.value in self.source_gate_handles:
            self.snapshot_query_count += 1
            index = self.snapshot_query_count
            callback = self.snapshot_callbacks.get(index)
            if callback is not None:
                callback()
            injected = self.snapshot_errors.get(index)
            if injected is not None:
                return Win32Err(injected)
        result = super().query_file_info(handle)
        if (
            not self.volume_available
            and handle.value in self.source_gate_handles
            and isinstance(result, Win32Ok)
        ):
            return Win32Ok(replace(result.value, volume_serial=None))  # type: ignore[arg-type]
        return result

    def query_delete_pending(self, handle: OwnedHandle) -> Win32Result[bool]:
        if self.delete_pending_error is not None:
            error = self.delete_pending_error
            self.delete_pending_error = None
            return Win32Err(error)
        key, _ = self.handles[handle.value]
        return Win32Ok(self.delete_pending_override or self.nodes[key].delete_pending)

    def set_file_offset(self, handle: OwnedHandle, offset: int) -> Win32Result[int]:
        failed = self._error("SetFilePointerEx")
        if failed is not None:
            return failed
        key, _ = self.handles[handle.value]
        self.handles[handle.value] = (key, offset)
        return Win32Ok(offset)


def make_source(
    port: FakeCloseGatePort,
    path: str = r"C:\Sources\source.mp4",
    *,
    data: bytes = b"stable-source",
) -> ValidatedPath:
    port.add_file(path, data)
    result = validate_path(port, path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathValidated)
    return result.path


def alias_source(
    port: FakeCloseGatePort,
    source: ValidatedPath,
    alias: str,
) -> ValidatedPath:
    original = port.nodes[port._key(source.canonical_dos_path)]
    node = port.add_file(alias, bytes(original.data))
    node.file_id = original.file_id
    node.volume = original.volume
    result = validate_path(port, alias, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathValidated)
    return result.path


def gate(
    port: FakeCloseGatePort,
    source: ValidatedPath,
    *,
    project_id: str = PROJECT_A,
    token: CancellationToken | None = None,
    clock: FakeWaitClock | None = None,
):
    return run_close_gate(
        port,
        project_id,
        source,
        token or CancellationToken(),
        wait_clock=clock or FakeWaitClock(),
        lease_id_factory=lambda: LEASE_ID,
    )


@pytest.fixture
def close_port() -> FakeCloseGatePort:
    return FakeCloseGatePort()


@pytest.fixture
def source_path(close_port: FakeCloseGatePort) -> ValidatedPath:
    return make_source(close_port)
