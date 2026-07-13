from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest

from matrix_auto_cutter.phase2.atomic_project import PublishOk, replace_project_cas
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockAcquired,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
    acquire_path_lock,
    acquire_project_lock,
)
from matrix_auto_cutter.phase2.pathing import PathRejected, PathRole, validate_path
from matrix_auto_cutter.phase2.snapshots import (
    SameInstanceUnchanged,
    SnapshotOk,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port
from matrix_auto_cutter.phase2.win32_port import Win32Ok
from matrix_auto_cutter.phase2.workspace import (
    ProjectCreated,
    ProjectOpened,
    WorkspaceReady,
    create_project,
    ensure_workspace,
    open_project,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"


def test_fake_lock_classification_stale_file_and_diagnostic_authority(fake_port) -> None:
    first = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(first, LockAcquired)
    assert first.lease.held
    second = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(second, LockBusy)
    assert first.lease.release() is None
    stale = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(stale, LockAcquired)
    assert stale.lease.release() is None

    fake_port.failures["CreateFileW"] = [5]
    denied = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(denied, LockAccessDenied)
    fake_port.failures["CreateFileW"] = [999]
    io = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(io, LockIoError)


def test_lock_cancel_timeout_validation_and_diagnostic_failure(fake_port) -> None:
    token = CancellationToken()
    token.cancel()
    assert isinstance(acquire_project_lock(fake_port, PROJECT_ID, token), LockCancelled)
    assert isinstance(acquire_project_lock(fake_port, "bad", CancellationToken()), LockIoError)
    with pytest.raises(ValueError):
        acquire_project_lock(fake_port, PROJECT_ID, CancellationToken(), timeout_seconds=-1)

    held = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(held, LockAcquired)
    timed = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken(), timeout_seconds=0.001)
    assert isinstance(timed, LockTimedOut)
    held.lease.release()

    fake_port.failures["MoveFileExW"] = [888]
    acquired = acquire_project_lock(
        fake_port,
        PROJECT_ID,
        CancellationToken(),
        run_id=UUID("6ba7b814-9dad-4b8a-92fb-2a41f5468719"),
    )
    assert isinstance(acquired, LockAcquired)
    assert acquired.diagnostic_errors and acquired.lease.held
    acquired.lease.release()


def test_path_lock_is_redacted_and_case_equivalent(fake_port) -> None:
    left = validate_path(fake_port, r"C:\Secret\Source.MP4", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    right = validate_path(fake_port, r"c:\secret\source.mp4", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(left, PathRejected) and not isinstance(right, PathRejected)
    held = acquire_path_lock(fake_port, left.path, CancellationToken())
    assert isinstance(held, LockAcquired)
    assert "Secret" not in held.lease.ownership_path.canonical_dos_path
    assert isinstance(acquire_path_lock(fake_port, right.path, CancellationToken()), LockBusy)
    held.lease.release()


class TempLocalPort(NativeWin32Port):
    def __init__(self, local_root: str) -> None:
        self._local_root = local_root
        super().__init__()

    def local_app_data(self):
        return Win32Ok(self._local_root)


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")
def test_real_windows_workspace_locks_replace_hardlink_unicode_and_source_immutability(
    tmp_path: Path,
) -> None:
    local = tmp_path / "local"
    local.mkdir()
    port = TempLocalPort(str(local))
    workspace_result = ensure_workspace(port, str(tmp_path / "workspace"))
    assert isinstance(workspace_result, WorkspaceReady)
    created = create_project(
        port,
        workspace_result,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(created, ProjectCreated)
    opened = open_project(port, workspace_result, PROJECT_ID)
    assert isinstance(opened, ProjectOpened)
    project_lock = acquire_project_lock(port, PROJECT_ID, CancellationToken())
    assert isinstance(project_lock, LockAcquired)
    replacement = created.project.document.model_copy(update={"revision": 1})
    replaced = replace_project_cas(
        port,
        created.project,
        replacement,
        CancellationToken(),
        project_lock=project_lock.lease,
    )
    assert isinstance(replaced, PublishOk)
    project_lock.lease.release()

    long_directory = tmp_path / (("unicode-" + chr(0x00E4)) * 20)
    long_directory.mkdir()
    source = long_directory / ("source-" + chr(0x03A9) + ".mp4")
    source.write_bytes(b"immutable-source")
    before = source.read_bytes()
    alias = long_directory / "alias.mp4"
    os.link(source, alias)
    source_path = validate_path(
        port,
        str(source),
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    alias_path = validate_path(
        port,
        str(alias),
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert not isinstance(source_path, PathRejected) and not isinstance(alias_path, PathRejected)
    source_snapshot = snapshot_file(port, source_path.path)
    alias_snapshot = snapshot_file(port, alias_path.path)
    assert isinstance(source_snapshot, SnapshotOk) and isinstance(alias_snapshot, SnapshotOk)
    assert isinstance(
        compare_snapshots(source_snapshot.snapshot, alias_snapshot.snapshot), SameInstanceUnchanged
    )
    lock = acquire_path_lock(port, source_path.path, CancellationToken())
    assert isinstance(lock, LockAcquired)
    assert isinstance(acquire_path_lock(port, source_path.path, CancellationToken()), LockBusy)
    lock.lease.release()
    assert source.read_bytes() == before
    assert not list(tmp_path.rglob(".~matrix-2a-*.tmp"))


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")
def test_real_junction_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    port = NativeWin32Port()
    result = validate_path(
        port, str(junction), PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
    )
    assert isinstance(result, PathRejected)


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")
def test_real_second_process_busy_and_crash_release(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    code = f"""
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port
from matrix_auto_cutter.phase2.win32_port import Win32Ok
from matrix_auto_cutter.phase2.locks import acquire_project_lock
from matrix_auto_cutter.phase2.cancellation import CancellationToken
class P(NativeWin32Port):
 def local_app_data(self): return Win32Ok({str(local)!r})
p=P(); result=acquire_project_lock(p,{PROJECT_ID!r},CancellationToken())
print(type(result).__name__, flush=True)
input()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "LockAcquired"
    port = TempLocalPort(str(local))
    assert isinstance(acquire_project_lock(port, PROJECT_ID, CancellationToken()), LockBusy)
    child.kill()
    child.wait(timeout=5)
    reacquired = acquire_project_lock(port, PROJECT_ID, CancellationToken())
    assert isinstance(reacquired, LockAcquired)
    reacquired.lease.release()
