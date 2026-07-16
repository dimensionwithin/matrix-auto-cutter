from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from threading import Event, Thread

import pytest
from tests.phase2.source_hash.conftest import HASH_RUN_ID, PROJECT_ID

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateClosed,
    NativeCloseGateWin32Port,
    run_close_gate,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.source_hash import (
    PRODUCTION_BLOCK_SIZE_BYTES,
    HashCompleted,
    HashIoError,
    hash_lease_source,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Ok

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")


class NativeHashPort(NativeCloseGateWin32Port):
    def __init__(self, local_root: Path) -> None:
        self._local_root = str(local_root)
        self.open_calls: list[str] = []
        self.read_handles: list[int] = []
        self.position_handles: list[int] = []
        self.query_handles: list[int] = []
        self.closed_handles: list[int] = []
        self.block_entered: Event | None = None
        self.block_proceed: Event | None = None
        super().__init__()

    def _close(self, value: int):
        self.closed_handles.append(value)
        return super()._close(value)

    def local_app_data(self):
        return Win32Ok(self._local_root)

    def open_file(self, long_path, desired_access, share_mode, creation_disposition, flags):
        self.open_calls.append(long_path)
        return super().open_file(
            long_path,
            desired_access,
            share_mode,
            creation_disposition,
            flags,
        )

    def query_file_info(self, handle: OwnedHandle):
        self.query_handles.append(handle.value)
        return super().query_file_info(handle)

    def set_file_offset(self, handle: OwnedHandle, offset: int):
        self.position_handles.append(handle.value)
        return super().set_file_offset(handle, offset)

    def read_file(self, handle: OwnedHandle, maximum_bytes: int):
        self.read_handles.append(handle.value)
        entered = self.block_entered
        proceed = self.block_proceed
        if entered is not None and proceed is not None and not entered.is_set():
            entered.set()
            assert proceed.wait(5)
        return super().read_file(handle, maximum_bytes)


def _lease(port: NativeHashPort, source: Path):
    validated = validate_path(port, str(source), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(validated, PathValidated)
    result = run_close_gate(port, PROJECT_ID, validated.path, CancellationToken())
    assert isinstance(result, CloseGateClosed)
    return result.lease


@pytest.mark.parametrize("data", [b"", b"\x00\xffreal-binary\x00payload"])
def test_real_empty_and_binary_file_use_actual_lease_handle(tmp_path: Path, data: bytes) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source = tmp_path / "real.mp4"
    source.write_bytes(data)
    before = (source.stat().st_size, source.stat().st_mtime_ns)
    port = NativeHashPort(local)
    lease = _lease(port, source)
    opens_before_hash = tuple(port.open_calls)
    query_count = len(port.query_handles)
    read_count = len(port.read_handles)
    position_count = len(port.position_handles)
    result = hash_lease_source(lease, CancellationToken(), PROJECT_ID, HASH_RUN_ID)
    assert isinstance(result, HashCompleted)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert tuple(port.open_calls) == opens_before_hash
    hash_reads = port.read_handles[read_count:]
    hash_positions = port.position_handles[position_count:]
    assert len(set(hash_positions + hash_reads)) == 1
    source_handle = hash_positions[0]
    assert port.query_handles[query_count:] == [source_handle]
    assert not lease.closed
    lease.close()
    assert (source.stat().st_size, source.stat().st_mtime_ns) == before


def test_real_multiblock_long_unicode_and_hardlink_alias(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    long_directory = tmp_path / (("lange-unicode-Ã¤-Î©-") * 11)
    long_directory.mkdir()
    source = long_directory / "aufnahme-ÃŸ-æ¼¢å­—.mp4"
    alias = tmp_path / "hardlink-alias.mp4"
    data = bytes(range(251)) * ((PRODUCTION_BLOCK_SIZE_BYTES // 251) + 2)
    source.write_bytes(data)
    os.link(source, alias)
    assert os.stat(source).st_ino == os.stat(alias).st_ino
    before_digest = hashlib.sha256(data).hexdigest()
    port = NativeHashPort(local)
    lease = _lease(port, source)
    opens_before_hash = tuple(port.open_calls)
    read_count = len(port.read_handles)
    position_count = len(port.position_handles)
    result = hash_lease_source(lease, CancellationToken(), PROJECT_ID, HASH_RUN_ID)
    assert isinstance(result, HashCompleted)
    assert result.sha256 == before_digest
    hash_reads = port.read_handles[read_count:]
    hash_positions = port.position_handles[position_count:]
    assert len(hash_reads) >= 3
    assert len(set(hash_reads + hash_positions)) == 1
    assert tuple(port.open_calls) == opens_before_hash
    assert lease.source_path.canonical_dos_path.casefold() != str(alias).casefold()
    lease.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_digest


def test_real_close_waits_for_active_hash_and_cannot_close_handle_early(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source = tmp_path / "blocking.mp4"
    source.write_bytes(b"x" * (1024 * 1024))
    port = NativeHashPort(local)
    lease = _lease(port, source)
    read_count = len(port.read_handles)
    close_count = len(port.closed_handles)
    entered = Event()
    proceed = Event()
    port.block_entered = entered
    port.block_proceed = proceed
    outputs = []
    hash_thread = Thread(
        target=lambda: outputs.append(
            hash_lease_source(lease, CancellationToken(), PROJECT_ID, HASH_RUN_ID)
        )
    )
    hash_thread.start()
    assert entered.wait(5)
    close_done = Event()
    close_thread = Thread(target=lambda: (lease.close(), close_done.set()))
    close_thread.start()
    try:
        assert not close_done.wait(0.1)
        source_handle = port.read_handles[read_count]
        assert source_handle not in port.closed_handles[close_count:]
    finally:
        proceed.set()
    hash_thread.join(5)
    close_thread.join(5)
    assert close_done.is_set()
    assert isinstance(outputs[0], HashIoError)
    assert lease.closed
    assert source_handle in port.closed_handles
    assert not hash_thread.is_alive() and not close_thread.is_alive()
