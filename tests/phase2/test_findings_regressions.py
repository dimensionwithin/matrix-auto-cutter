from __future__ import annotations

import copy
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID

import pytest

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    ImmutableConflict,
    PublishCancelled,
    PublishOk,
    publish_immutable,
    publish_initial,
    replace_project_cas,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.locks import (
    LockAcquired,
    LockCancelled,
    ProjectLockLease,
    acquire_path_lock,
    acquire_project_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureDeleteFailed,
    SecureRead,
    SecureReadFailed,
    secure_delete_file,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    FileTime,
    SameInstanceChanged,
    SnapshotOk,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port
from matrix_auto_cutter.phase2.win32_port import (
    FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_DELETE,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    GENERIC_READ,
    OPEN_EXISTING,
    OwnedHandle,
    Win32Err,
    Win32Failure,
    Win32Ok,
)
from matrix_auto_cutter.phase2.workspace import (
    ProjectCreated,
    ProjectOpened,
    ProjectOpenFailed,
    WorkspaceInvalid,
    WorkspaceReady,
    create_project,
    ensure_workspace,
    open_project,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"


def _created(fake_port, project_id: str = PROJECT_ID) -> tuple[WorkspaceReady, ProjectCreated]:
    workspace = ensure_workspace(fake_port, r"C:\FindingWorkspace")
    assert isinstance(workspace, WorkspaceReady)
    created = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(project_id),
    )
    assert isinstance(created, ProjectCreated)
    return workspace, created


def test_f02_bool_foreign_and_released_lock_proofs_are_rejected(fake_port) -> None:
    workspace, created = _created(fake_port)
    replacement = created.project.document.model_copy(update={"revision": 1})

    @dataclass
    class ForgedLease:
        held: bool = True

    for forged in (True, ForgedLease()):
        with pytest.raises(TypeError):
            replace_project_cas(
                fake_port,
                created.project,
                replacement,
                CancellationToken(),
                project_lock=forged,
            )

    foreign = acquire_project_lock(fake_port, OTHER_ID, CancellationToken())
    assert isinstance(foreign, LockAcquired)
    with pytest.raises(ValueError):
        replace_project_cas(
            fake_port,
            created.project,
            replacement,
            CancellationToken(),
            project_lock=foreign.lease,
        )
    foreign.lease.release()

    lease = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(lease, LockAcquired)
    lease.lease.release()
    with pytest.raises(ValueError):
        replace_project_cas(
            fake_port,
            created.project,
            replacement,
            CancellationToken(),
            project_lock=lease.lease,
        )
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectOpened)


def test_reaud_f01_path_copy_and_forged_leases_cannot_authorize_cas(fake_port) -> None:
    workspace, created = _created(fake_port)
    replacement = created.project.document.model_copy(update={"revision": 1})
    target_key = fake_port._key(created.project.metadata_path.long_path)
    before = bytes(fake_port.nodes[target_key].data)
    path_lock = acquire_path_lock(fake_port, created.project.metadata_path, CancellationToken())
    project_lock = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(path_lock, LockAcquired) and isinstance(project_lock, LockAcquired)
    assert path_lock.lease.kind.value == "path"
    assert project_lock.lease.kind.value == "project"
    assert project_lock.lease.key == PROJECT_ID
    assert path_lock.lease.ownership_path != project_lock.lease.ownership_path

    for field_name, field_value in (("kind", "project"), ("key", PROJECT_ID)):
        with pytest.raises(AttributeError):
            setattr(path_lock.lease, field_name, field_value)
    with pytest.raises(AttributeError):
        delattr(path_lock.lease, "_key")
    with pytest.raises(TypeError):
        replace(path_lock.lease)
    with pytest.raises(TypeError):
        copy.copy(path_lock.lease)
    with pytest.raises(TypeError):
        copy.copy(project_lock.lease)
    with pytest.raises(TypeError):
        copy.deepcopy(project_lock.lease)

    @dataclass
    class ForgedProjectLease:
        kind: str = "project"
        key: str = PROJECT_ID
        held: bool = True
        acquisition_token: object = object()

    unissued_runtime_type = object.__new__(ProjectLockLease)
    with pytest.raises(TypeError):
        unissued_runtime_type._initialize(
            PROJECT_ID,
            project_lock.lease.ownership_path,
            OwnedHandle(999, lambda value: Win32Ok(None)),
            object(),
            _seal=object(),
        )
    for forged, expected_error in (
        (path_lock.lease, TypeError),
        (ForgedProjectLease(), TypeError),
        (unissued_runtime_type, ValueError),
    ):
        with pytest.raises(expected_error):
            replace_project_cas(
                fake_port,
                created.project,
                replacement,
                CancellationToken(),
                project_lock=forged,
            )
        assert bytes(fake_port.nodes[target_key].data) == before

    object.__setattr__(path_lock.lease, "_key", PROJECT_ID)
    with pytest.raises(TypeError):
        replace_project_cas(
            fake_port,
            created.project,
            replacement,
            CancellationToken(),
            project_lock=path_lock.lease,
        )
    assert bytes(fake_port.nodes[target_key].data) == before

    published = replace_project_cas(
        fake_port,
        created.project,
        replacement,
        CancellationToken(),
        project_lock=project_lock.lease,
    )
    assert isinstance(published, PublishOk)
    assert bytes(fake_port.nodes[target_key].data) == canonical_bytes(replacement)
    path_lock.lease.release()
    project_lock.lease.release()


@pytest.mark.parametrize("close_error", [None, 777])
def test_f03_cancel_during_ownership_open_closes_handle(
    fake_port, monkeypatch, close_error: int | None
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original_open = fake_port.open_file

    def controlled_open(path, access, share, disposition, flags):
        result = original_open(path, access, share, disposition, flags)
        if path.endswith(f"{PROJECT_ID}.lck"):
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(fake_port, "open_file", controlled_open)
    token = CancellationToken()
    results: list[object] = []
    worker = threading.Thread(
        target=lambda: results.append(acquire_project_lock(fake_port, PROJECT_ID, token))
    )
    worker.start()
    assert entered.wait(5)
    token.cancel()
    if close_error is not None:
        fake_port.failures["CloseHandle"] = [close_error]
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert isinstance(results[0], LockCancelled)
    if close_error is None:
        assert results[0].cleanup_diagnostics == ()
    else:
        assert results[0].cleanup_diagnostics[0].win32_code == close_error
    if close_error is None:
        assert not fake_port.exclusive
        assert not fake_port.handles
    else:
        assert len(fake_port.exclusive) == 1
        assert len(fake_port.handles) == 1


def test_f04_snapshot_key_recomputes_and_corruption_fails(fake_port) -> None:
    fake_port.add_file(r"C:\snapshot\source.bin", b"abc")
    path = validate_path(fake_port, r"C:\snapshot\source.bin", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    result = snapshot_file(fake_port, path.path)
    assert isinstance(result, SnapshotOk)
    snapshot = result.snapshot
    variants = (
        replace(snapshot, size_bytes=4),
        replace(snapshot, last_write_time=FileTime(snapshot.last_write_time.value + 1)),
        replace(snapshot, attributes=snapshot.attributes | 2),
    )
    for variant in variants:
        assert variant.snapshot_key != snapshot.snapshot_key
        assert isinstance(compare_snapshots(snapshot, variant), SameInstanceChanged)

    corrupted = replace(snapshot)
    object.__setattr__(corrupted, "snapshot_key", "0" * 64)
    assert isinstance(compare_snapshots(snapshot, corrupted), ComparisonFailed)
    unknown = replace(snapshot)
    object.__setattr__(unknown, "evidence_version", "file_snapshot/9.0")
    assert isinstance(compare_snapshots(snapshot, unknown), ComparisonFailed)

    invalid_values = (
        ("file_type", "directory"),
        ("size_bytes", -1),
        ("attributes", -1),
    )
    for field_name, value in invalid_values:
        invalid = replace(snapshot)
        object.__setattr__(invalid, field_name, value)
        assert isinstance(compare_snapshots(snapshot, invalid), ComparisonFailed)
    bad_unit_time = FileTime(snapshot.last_write_time.value)
    object.__setattr__(bad_unit_time, "unit", "seconds")
    bad_unit = replace(snapshot)
    object.__setattr__(bad_unit, "last_write_time", bad_unit_time)
    assert isinstance(compare_snapshots(snapshot, bad_unit), ComparisonFailed)
    bad_epoch_time = FileTime(snapshot.last_write_time.value)
    object.__setattr__(bad_epoch_time, "epoch", "1970-01-01T00:00:00Z")
    bad_epoch = replace(snapshot)
    object.__setattr__(bad_epoch, "last_write_time", bad_epoch_time)
    assert isinstance(compare_snapshots(snapshot, bad_epoch), ComparisonFailed)
    malformed_time = replace(snapshot)
    object.__setattr__(malformed_time, "last_write_time", object())
    assert isinstance(compare_snapshots(snapshot, malformed_time), ComparisonFailed)
    with pytest.raises(ValueError):
        replace(snapshot, size_bytes=-1)
    with pytest.raises(ValueError):
        replace(
            snapshot,
            file_id=AvailableIdentity(scheme="file_id_128", value="\ud800"),
        )


def test_reaud_f02_secure_read_requires_successful_close(fake_port) -> None:
    normal = fake_port.add_file(r"C:\secure-read\normal.bin", b"normal")
    normal_path = validate_path(fake_port, normal.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(normal_path, PathRejected)
    assert isinstance(secure_read_file(fake_port, normal_path.path, 64), SecureRead)

    failed = fake_port.add_file(r"C:\secure-read\close-fails.bin", b"secret")
    failed_path = validate_path(fake_port, failed.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(failed_path, PathRejected)
    failed_key = fake_port._key(failed.path)
    fake_port.close_results[failed_key] = [None, 811]
    result = secure_read_file(fake_port, failed_path.path, 64)
    assert isinstance(result, SecureReadFailed)
    assert result.error.phase == "close_after_secure_read"
    assert result.error.win32_code == 811
    assert fake_port.close_attempts[failed_key] == 2
    assert any(key == failed_key for key, _ in fake_port.handles.values())


def test_reaud_f02_read_failure_stays_primary_when_close_also_fails(fake_port) -> None:
    node = fake_port.add_file(r"C:\secure-read\both-fail.bin", b"secret")
    path = validate_path(fake_port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    key = fake_port._key(node.path)
    fake_port.close_results[key] = [None, 813]
    fake_port.failures["ReadFile"] = [812]
    result = secure_read_file(fake_port, path.path, 64)
    assert isinstance(result, SecureReadFailed)
    assert result.error.win32_code == 812
    assert result.diagnostics[0].phase == "close_after_secure_read"
    assert result.diagnostics[0].win32_code == 813
    assert fake_port.close_attempts[key] == 2


def test_reaud_f02_metadata_close_failure_never_opens_project(fake_port) -> None:
    workspace, created = _created(fake_port)
    key = fake_port._key(created.project.metadata_path.long_path)
    fake_port.close_results[key] = [None, 814]
    result = open_project(fake_port, workspace, PROJECT_ID)
    assert isinstance(result, ProjectOpenFailed)
    assert result.error.phase == "close_after_secure_read"
    assert result.error.win32_code == 814


def test_reaud_f02_secure_delete_models_delete_on_successful_close(fake_port) -> None:
    normal = fake_port.add_file(r"C:\secure-delete\normal.tmp", b"x")
    normal_path = validate_path(fake_port, normal.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(normal_path, PathRejected)
    assert secure_delete_file(fake_port, normal_path.path) is None
    assert fake_port._key(normal.path) not in fake_port.nodes

    close_failed = fake_port.add_file(r"C:\secure-delete\close-fails.tmp", b"x")
    failed_path = validate_path(fake_port, close_failed.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(failed_path, PathRejected)
    failed_key = fake_port._key(close_failed.path)
    fake_port.delete_close_failures = [821]
    failed = secure_delete_file(fake_port, failed_path.path)
    assert isinstance(failed, SecureDeleteFailed)
    assert failed.error.phase == "close_after_delete_disposition"
    assert failed.error.win32_code == 821
    assert fake_port.nodes[failed_key].delete_pending
    assert any(key == failed_key for key, _ in fake_port.handles.values())


def test_reaud_f02_delete_error_stays_primary_when_close_also_fails(fake_port) -> None:
    node = fake_port.add_file(r"C:\secure-delete\both-fail.tmp", b"x")
    path = validate_path(fake_port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    key = fake_port._key(node.path)
    fake_port.close_results[key] = [None, 823]
    fake_port.failures["SetFileInformationByHandle"] = [822]
    result = secure_delete_file(fake_port, path.path)
    assert isinstance(result, SecureDeleteFailed)
    assert result.error.win32_code == 822
    assert result.diagnostics[0].phase == "close_after_secure_delete"
    assert result.diagnostics[0].win32_code == 823
    assert not fake_port.nodes[key].delete_pending


def test_micro_f01_ancestor_close_failure_stops_secure_read_before_target_open(fake_port) -> None:
    node = fake_port.add_file(r"C:\validation-close\read.bin", b"trusted")
    path = validate_path(fake_port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    drive_key = fake_port._key("C:\\")
    target_key = fake_port._key(node.path)
    fake_port.close_results[drive_key] = [831]
    fake_port.open_history.clear()

    result = secure_read_file(fake_port, path.path, 64)

    assert isinstance(result, SecureReadFailed)
    assert result.error.phase == "close_validation_ancestor"
    assert result.error.win32_code == 831
    assert isinstance(result.error.cause, OSError)
    assert [key for _, key in fake_port.open_history] == [drive_key]
    assert fake_port.close_attempts_by_handle[fake_port.open_history[0][0]] == 1
    assert target_key not in (key for _, key in fake_port.open_history)
    assert any(key == drive_key for key, _ in fake_port.handles.values())


def test_micro_f01_target_close_failure_stops_secure_delete_before_disposition(fake_port) -> None:
    node = fake_port.add_file(r"C:\validation-close\delete.tmp", b"keep")
    path = validate_path(fake_port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    target_key = fake_port._key(node.path)
    fake_port.close_results[target_key] = [832]
    fake_port.open_history.clear()

    result = secure_delete_file(fake_port, path.path)

    assert isinstance(result, SecureDeleteFailed)
    assert result.error.phase == "close_validation_target"
    assert result.error.win32_code == 832
    target_opens = [(raw, key) for raw, key in fake_port.open_history if key == target_key]
    assert len(target_opens) == 1
    assert fake_port.close_attempts_by_handle[target_opens[0][0]] == 1
    assert bytes(fake_port.nodes[target_key].data) == b"keep"
    assert not fake_port.nodes[target_key].delete_pending
    assert any(key == target_key for key, _ in fake_port.handles.values())


def test_micro_f01_validation_error_stays_primary_and_close_is_secondary(
    fake_port, monkeypatch
) -> None:
    node = fake_port.add_file(r"C:\validation-close\both.bin", b"keep")
    path = validate_path(fake_port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert not isinstance(path, PathRejected)
    target_key = fake_port._key(node.path)
    original_query = fake_port.query_file_info

    def fail_target_query(handle):
        key, _ = fake_port.handles[handle.value]
        if key == target_key:
            return Win32Err(Win32Failure(833, "GetFileInformationByHandleEx", "query failed"))
        return original_query(handle)

    monkeypatch.setattr(fake_port, "query_file_info", fail_target_query)
    fake_port.close_results[target_key] = [834]

    result = validate_path(
        fake_port,
        node.path,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )

    assert isinstance(result, PathRejected)
    assert result.error.win32_code == 833
    assert result.error.phase == "GetFileInformationByHandleEx"
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].phase == "close_validation_target"
    assert result.diagnostics[0].win32_code == 834


def test_micro_f01_metadata_validation_close_failure_cannot_issue_capability(fake_port) -> None:
    workspace, created = _created(fake_port)
    metadata_key = fake_port._key(created.project.metadata_path.long_path)
    fake_port.close_results[metadata_key] = [835]
    fake_port.open_history.clear()

    result = open_project(fake_port, workspace, PROJECT_ID)

    assert isinstance(result, ProjectOpenFailed)
    assert result.error.phase == "close_validation_target"
    assert result.error.win32_code == 835
    assert len([(raw, key) for raw, key in fake_port.open_history if key == metadata_key]) == 1


def test_micro_f01_workspace_validation_close_failure_preserves_native_error(fake_port) -> None:
    drive_key = fake_port._key("C:\\")
    fake_port.close_results[drive_key] = [839]
    fake_port.open_history.clear()

    result = ensure_workspace(fake_port, r"C:\CloseFailureWorkspace")

    assert isinstance(result, WorkspaceInvalid)
    assert result.error.phase == "close_validation_target"
    assert result.error.win32_code == 839
    assert result.error.category.value == "io"
    assert len(fake_port.open_history) == 1
    raw, key = fake_port.open_history[0]
    assert key == drive_key
    assert fake_port.close_attempts_by_handle[raw] == 1


def test_micro_f01_atomic_pre_and_post_validation_close_fail_closed(fake_port) -> None:
    workspace, created = _created(fake_port)
    target_result = validate_path(
        fake_port,
        created.project.project_directory.canonical_dos_path + r"\close-bound.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert not isinstance(target_result, PathRejected)
    target = target_result.path
    parent_key = fake_port._key(created.project.project_directory.long_path)
    fake_port.close_results[parent_key] = [836]
    fake_port.open_history.clear()

    pre_failed = publish_initial(
        fake_port, target, b"{}\n", lambda data: data == b"{}\n", CancellationToken(), artifact="x"
    )

    assert isinstance(pre_failed, AtomicPublishFailed)
    assert pre_failed.error.phase == "close_validation_target"
    assert pre_failed.error.win32_code == 836
    assert pre_failed.error.category.value == "io"
    assert not any(".~MATRIX-2A-" in key for _, key in fake_port.open_history)
    assert fake_port._key(target.long_path) not in fake_port.nodes

    fake_port.close_results[parent_key] = []
    target_key = fake_port._key(target.long_path)
    fake_port.close_results[target_key] = [837]
    fake_port.open_history.clear()
    post_failed = publish_initial(
        fake_port, target, b"{}\n", lambda data: data == b"{}\n", CancellationToken(), artifact="x"
    )

    assert isinstance(post_failed, AtomicPublishFailed)
    assert post_failed.error.phase == "close_validation_target"
    assert post_failed.error.win32_code == 837
    assert bytes(fake_port.nodes[target_key].data) == b"{}\n"


def test_micro_f01_cas_post_validation_close_revokes_project_trust(fake_port) -> None:
    _, created = _created(fake_port)
    metadata_key = fake_port._key(created.project.metadata_path.long_path)
    replacement = created.project.document.model_copy(update={"revision": 1})
    lease = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(lease, LockAcquired)
    closes_before = fake_port.close_attempts.get(metadata_key, 0)
    fake_port.close_results[metadata_key] = [None, None, None, None, 838]

    failed = replace_project_cas(
        fake_port,
        created.project,
        replacement,
        CancellationToken(),
        project_lock=lease.lease,
    )

    assert isinstance(failed, AtomicPublishFailed)
    assert failed.error.phase == "close_validation_target"
    assert failed.error.win32_code == 838
    assert not created.project.trusted
    assert fake_port.close_attempts[metadata_key] - closes_before == 5

    rejected = replace_project_cas(
        fake_port,
        created.project,
        replacement.model_copy(update={"revision": 2}),
        CancellationToken(),
        project_lock=lease.lease,
    )
    assert isinstance(rejected, AtomicPublishIntegrity)
    assert rejected.error.phase == "project_trust"
    lease.lease.release()


def test_f06_diagnostics_failure_is_secondary_to_ownership(fake_port, monkeypatch) -> None:
    from matrix_auto_cutter.phase2 import locks

    original_ensure = locks.ensure_directory_tree

    def fail_diagnostics(port, path):
        if path.endswith("\\diagnostics"):
            return PathRejected(
                locks.failure(
                    locks.ErrorCode.PATH_OS_ERROR,
                    locks.ErrorCategory.IO,
                    "diagnostics",
                    "denied",
                )
            )
        return original_ensure(port, path)

    monkeypatch.setattr(locks, "ensure_directory_tree", fail_diagnostics)
    acquired = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(acquired, LockAcquired)
    assert acquired.lease.held and acquired.diagnostic_errors
    acquired.lease.release()


def test_f09_cleanup_diagnostics_survive_all_primary_outcomes(fake_port) -> None:
    workspace, created = _created(fake_port)
    target_result = validate_path(
        fake_port,
        created.project.project_directory.canonical_dos_path + r"\artifact.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert not isinstance(target_result, PathRejected)
    target = target_result.path

    cancelled_token = CancellationToken()
    cancelled_token.cancel()
    fake_port.delete_close_failures = [901]
    cancelled = publish_initial(
        fake_port, target, b"x", lambda data: True, cancelled_token, artifact="finding"
    )
    assert isinstance(cancelled, PublishCancelled)
    assert cancelled.cleanup_diagnostics[0].win32_code == 901

    fake_port.failures["MoveFileExW"] = [902]
    fake_port.delete_close_failures = [903]
    initial_failed = publish_initial(
        fake_port, target, b"x", lambda data: True, CancellationToken(), artifact="finding"
    )
    assert isinstance(initial_failed, AtomicPublishFailed)
    assert initial_failed.cleanup_diagnostics[0].win32_code == 903

    fake_port.add_file(target.canonical_dos_path, b"other")
    fake_port.delete_close_failures = [904]
    immutable = publish_immutable(
        fake_port,
        target,
        b"wanted",
        lambda data: data == b"wanted",
        CancellationToken(),
        artifact="finding",
    )
    assert isinstance(immutable, ImmutableConflict)
    assert immutable.cleanup_diagnostics[0].win32_code == 904

    lease = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(lease, LockAcquired)
    fake_port.failures["ReplaceFileW"] = [905]
    fake_port.delete_close_failures = [906]
    cas = replace_project_cas(
        fake_port,
        created.project,
        created.project.document.model_copy(update={"revision": 1}),
        CancellationToken(),
        project_lock=lease.lease,
    )
    assert isinstance(cas, AtomicPublishFailed)
    assert cas.cleanup_diagnostics[0].win32_code == 906
    lease.lease.release()

    other_workspace = ensure_workspace(fake_port, r"C:\CleanupWorkspace")
    assert isinstance(other_workspace, WorkspaceReady)
    fake_port.failures["FlushFileBuffers"] = [907]
    fake_port.delete_close_failures = [908]
    failed_project = create_project(
        fake_port,
        other_workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(OTHER_ID),
    )
    assert isinstance(failed_project, ProjectOpenFailed)
    assert failed_project.cleanup_diagnostics[0].win32_code == 908


class _RacePort(NativeWin32Port):
    def __init__(self, local_root: str) -> None:
        self._local_root = local_root
        self.race_target: str | None = None
        self.backup: Path | None = None
        self.attacker: Path | None = None
        self._target_opens = 0
        super().__init__()

    def local_app_data(self):
        return Win32Ok(self._local_root)

    def open_file(self, long_path, desired_access, share_mode, creation_disposition, flags):
        if self.race_target is not None and long_path == self.race_target:
            self._target_opens += 1
            if self._target_opens == 2:
                project_dir = Path(self._dos_path(self.race_target)).parent
                assert self.backup is not None and self.attacker is not None
                os.replace(project_dir, self.backup)
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(project_dir), str(self.attacker)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                assert completed.returncode == 0, completed.stderr
        return super().open_file(long_path, desired_access, share_mode, creation_disposition, flags)

    @staticmethod
    def _dos_path(value: str) -> str:
        return value[4:] if value.startswith("\\\\?\\") else value


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 junction race")
def test_f01_real_junction_swap_never_opens_project(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    port = _RacePort(str(local))
    workspace = ensure_workspace(port, str(tmp_path / "workspace"))
    assert isinstance(workspace, WorkspaceReady)
    created = create_project(
        port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(created, ProjectCreated)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "project.json").write_bytes(canonical_bytes(created.project.document))
    backup = tmp_path / "project-backup"
    project_dir = Path(created.project.project_directory.canonical_dos_path)
    port.race_target = created.project.metadata_path.long_path
    port.backup = backup
    port.attacker = attacker
    try:
        result = open_project(port, workspace, PROJECT_ID)
        assert not isinstance(result, ProjectOpened)
        assert isinstance(result, ProjectOpenFailed)
    finally:
        if project_dir.is_dir():
            os.rmdir(project_dir)
        if backup.exists():
            os.replace(backup, project_dir)


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 FILE_STANDARD_INFO")
def test_f07_real_native_file_and_directory_type(tmp_path: Path) -> None:
    file = tmp_path / "file.bin"
    file.write_bytes(b"x")
    directory = tmp_path / "directory"
    directory.mkdir()
    port = NativeWin32Port()
    file_open = port.open_file(
        "\\\\?\\" + str(file),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
    )
    directory_open = port.open_file(
        "\\\\?\\" + str(directory),
        GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
    )
    assert isinstance(file_open, Win32Ok) and isinstance(directory_open, Win32Ok)
    try:
        file_info = port.query_file_info(file_open.value)
        directory_info = port.query_file_info(directory_open.value)
        assert isinstance(file_info, Win32Ok) and not file_info.value.is_directory
        assert isinstance(directory_info, Win32Ok) and directory_info.value.is_directory
    finally:
        file_open.value.close()
        directory_open.value.close()


@pytest.mark.skipif(sys.platform != "win32", reason="real Win32 delete-on-close")
def test_reaud_f02_real_native_delete_on_close_success(tmp_path: Path) -> None:
    target = tmp_path / "delete-on-close.tmp"
    target.write_bytes(b"owned")
    port = NativeWin32Port()
    validated = validate_path(
        port,
        str(target),
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert not isinstance(validated, PathRejected)
    assert secure_delete_file(port, validated.path) is None
    assert not target.exists()
