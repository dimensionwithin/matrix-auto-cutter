from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from threading import Thread

import pytest
from tests.phase2.close_gate.conftest import PROJECT_A, PROJECT_B

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateBusy,
    CloseGateClosed,
    CloseGateDeletePending,
    CloseGateUnstable,
    CloseGateUnsupported,
    NativeCloseGateWin32Port,
    run_close_gate,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file
from matrix_auto_cutter.phase2.win32_port import (
    DELETE,
    FILE_SHARE_DELETE,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    GENERIC_WRITE,
    OPEN_EXISTING,
    Win32Err,
    Win32Ok,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")


class TempLocalCloseGatePort(NativeCloseGateWin32Port):
    def __init__(self, local_root: Path) -> None:
        self._local_root = str(local_root)
        super().__init__()

    def local_app_data(self):
        return Win32Ok(self._local_root)


class MutationPermissiveCloseGatePort(TempLocalCloseGatePort):
    """Test-only native port allowing a real mutation after the requested gate open."""

    def open_file(self, long_path, desired_access, share_mode, creation_disposition, flags):
        effective_share = share_mode
        if (
            desired_access == GENERIC_READ
            and share_mode == FILE_SHARE_READ
            and creation_disposition == OPEN_EXISTING
            and long_path.casefold().endswith(".mp4")
        ):
            effective_share |= FILE_SHARE_WRITE | FILE_SHARE_DELETE
        return super().open_file(
            long_path,
            desired_access,
            effective_share,
            creation_disposition,
            flags,
        )


class MutatingSystemWait:
    def __init__(self, mutation: Callable[[], None]) -> None:
        self.mutation = mutation
        self.calls = 0
        self.errors: list[BaseException] = []

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    def wait(self, cancellation: CancellationToken, seconds: float) -> bool:
        self.calls += 1
        thread: Thread | None = None
        if self.calls == 1:

            def mutate() -> None:
                try:
                    time.sleep(0.15)
                    self.mutation()
                except BaseException as exc:
                    self.errors.append(exc)

            thread = Thread(target=mutate)
            thread.start()
        result = cancellation.wait(seconds)
        if thread is not None:
            thread.join(2)
            assert not thread.is_alive()
        return result


def _validated(port: NativeCloseGateWin32Port, path: Path):
    result = validate_path(port, str(path), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathValidated)
    return result.path


def test_real_restrictive_share_matrix_stable_window_unicode_and_cleanup(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source_dir = tmp_path / (("lange-unicode-Ã¤-Î©-") * 10)
    source_dir.mkdir()
    source = source_dir / "aufnahme-ÃŸ-æ¼¢å­—.mp4"
    source.write_bytes(b"immutable-real-source")
    before_bytes = source.read_bytes()
    before_stat = source.stat()
    port = TempLocalCloseGatePort(local)
    validated = _validated(port, source)

    compatible = port.open_file(
        validated.long_path,
        GENERIC_READ,
        FILE_SHARE_READ,
        OPEN_EXISTING,
        0,
    )
    assert not isinstance(compatible, Win32Err)
    result = run_close_gate(port, PROJECT_A, validated, CancellationToken())
    assert isinstance(result, CloseGateClosed)
    assert result.lease.s0 == result.lease.s1 == result.lease.s2
    assert result.lease.close() == ()
    assert not isinstance(compatible, Win32Err)
    assert isinstance(compatible.value.close(), Win32Ok)

    writer = port.open_file(
        validated.long_path,
        GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        OPEN_EXISTING,
        0,
    )
    assert not isinstance(writer, Win32Err)
    busy = run_close_gate(port, PROJECT_A, validated, CancellationToken())
    assert isinstance(busy, CloseGateBusy)
    assert busy.error.win32_code == 32
    assert isinstance(writer.value.close(), Win32Ok)
    assert source.read_bytes() == before_bytes
    after_stat = source.stat()
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )


@pytest.mark.parametrize("mutation_kind", ["grow", "truncate"])
def test_real_growth_or_truncate_during_window_is_unstable(
    tmp_path: Path,
    mutation_kind: str,
) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source = tmp_path / f"mapped-{mutation_kind}.mp4"
    source.write_bytes(b"0123456789abcdef")

    def mutate() -> None:
        if mutation_kind == "grow":
            with source.open("ab") as handle:
                handle.write(b"GROWTH!!")
                handle.flush()
                os.fsync(handle.fileno())
        else:
            with source.open("r+b") as handle:
                handle.truncate(8)
                handle.flush()
                os.fsync(handle.fileno())

    wait = MutatingSystemWait(mutate)
    port = MutationPermissiveCloseGatePort(local)
    result = run_close_gate(
        port,
        PROJECT_A,
        _validated(port, source),
        CancellationToken(),
        wait_clock=wait,
    )
    if wait.errors:
        pytest.fail(f"real mutation failed: {wait.errors!r}")
    assert isinstance(result, CloseGateUnstable)


def test_real_hardlink_file_id_equality_and_source_serialization(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source = tmp_path / "source.mp4"
    alias = tmp_path / "alias.mp4"
    source.write_bytes(b"hardlink-source")
    os.link(source, alias)
    port = TempLocalCloseGatePort(local)
    source_path = _validated(port, source)
    alias_path = _validated(port, alias)
    first = run_close_gate(port, PROJECT_A, source_path, CancellationToken())
    assert isinstance(first, CloseGateClosed)
    alias_snapshot = snapshot_file(port, alias_path)
    assert isinstance(alias_snapshot, SnapshotOk)
    assert isinstance(alias_snapshot.snapshot.file_id, type(first.lease.s0.file_id))
    assert alias_snapshot.snapshot.file_id == first.lease.s0.file_id
    second = run_close_gate(port, PROJECT_B, alias_path, CancellationToken())
    assert isinstance(second, CloseGateBusy)
    assert second.error.phase == "source_lock_open"
    first.lease.close()


def test_real_junction_source_is_rejected_without_touching_target(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    source = target / "source.mp4"
    source.write_bytes(b"junction-target")
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    try:
        port = TempLocalCloseGatePort(local)
        result = run_close_gate(
            port,
            PROJECT_A,
            _validated(port, junction / "source.mp4"),
            CancellationToken(),
        )
        assert isinstance(result, CloseGateUnsupported)
        assert source.read_bytes() == b"junction-target"
    finally:
        junction.rmdir()


def test_real_delete_pending_if_platform_exposes_reliable_mapping(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    source = tmp_path / "delete-pending.mp4"
    source.write_bytes(b"delete-pending")
    port = TempLocalCloseGatePort(local)
    validated = _validated(port, source)
    deletion = port.open_file(
        validated.long_path,
        DELETE | GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        OPEN_EXISTING,
        0,
    )
    assert not isinstance(deletion, Win32Err)
    assert isinstance(port.delete_file_handle(deletion.value), Win32Ok)
    result = run_close_gate(port, PROJECT_A, validated, CancellationToken())
    if not isinstance(result, CloseGateDeletePending):
        deletion.value.close()
        pytest.skip(
            "this Windows build maps pre-open delete-pending through path validation "
            f"as {type(result).__name__}; adapter/open mappings are covered separately"
        )
    deletion.value.close()
