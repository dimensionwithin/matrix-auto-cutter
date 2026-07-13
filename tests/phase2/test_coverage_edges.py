from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from matrix_auto_cutter.phase2 import (
    atomic_project,
    locks,
    pathing,
    snapshots,
)
from matrix_auto_cutter.phase2 import (
    workspace as workspace_module,
)
from matrix_auto_cutter.phase2.artifacts import (
    AvailableIdentity,
    UnavailableIdentity,
    canonical_bytes,
)
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    CasConflict,
    _atomic_error,
    _cleanup,
    _read_target,
    _replace_project_cas_locked,
    _root_from,
    _TargetRead,
    _TargetReadFailed,
    _write_temp,
    publish_immutable,
    publish_initial,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, failure
from matrix_auto_cutter.phase2.locks import (
    LockAcquired,
    acquire_path_lock,
    acquire_project_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    PathValidated,
    SecureDeleteFailed,
    SecureReadFailed,
    _validate_opened_info,
    ensure_directory_tree,
    secure_delete_file,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.snapshots import (
    FileTime,
    SnapshotOk,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.win32_port import (
    CREATE_NEW,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_NORMAL,
    HandleState,
    OwnedHandle,
    Win32Err,
    Win32Failure,
    Win32Ok,
)
from matrix_auto_cutter.phase2.workspace import (
    ProjectCreated,
    ProjectMetadataInvalid,
    ProjectOpenFailed,
    WorkspaceReady,
    _MetadataReadFailed,
    create_project,
    ensure_workspace,
    open_project,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
OTHER_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"


def workspace_and_project(fake_port) -> tuple[WorkspaceReady, ProjectCreated]:
    workspace = ensure_workspace(fake_port, r"C:\W")
    assert isinstance(workspace, WorkspaceReady)
    project = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(project, ProjectCreated)
    return workspace, project


def test_owned_handle_guards_context() -> None:
    closed: list[int] = []
    handle = OwnedHandle(7, lambda value: closed.append(value) or Win32Ok(None))
    with handle as entered:
        assert entered.value == 7
    assert closed == [7] and handle.closed
    with pytest.raises(RuntimeError):
        _ = handle.value
    with pytest.raises(RuntimeError):
        handle.close()
    with pytest.raises(RuntimeError):
        handle.__enter__()

    failed = OwnedHandle(
        8, lambda value: Win32Err(Win32Failure(700, "CloseHandle", f"failed {value}"))
    )
    assert isinstance(failed.close(), Win32Err)
    assert failed.state is HandleState.CLOSE_FAILED_OR_UNKNOWN
    with pytest.raises(RuntimeError):
        _ = failed.value
    with pytest.raises(RuntimeError):
        failed.close()


def test_secure_handle_boundary_rejects_changed_evidence(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    path = project.project.metadata_path
    opened = fake_port.open_file(path.long_path, 0, 1, 3, 0)
    assert isinstance(opened, Win32Ok)
    queried = fake_port.query_file_info(opened.value)
    assert isinstance(queried, Win32Ok)
    info = queried.value
    opened.value.close()

    assert _validate_opened_info(fake_port, path, workspace.root, info, regular=True) is None
    external = replace(path, role=PathRole.EXTERNAL_SOURCE_READ_ONLY, root_binding=None)
    assert _validate_opened_info(fake_port, external, None, info, regular=True) is None

    original_case = fake_port.ordinal_case_key
    for fail_call in range(1, 6):
        calls = 0

        def fail_nth(value, *, expected=fail_call):
            nonlocal calls
            calls += 1
            if calls == expected:
                return Win32Err(Win32Failure(801, "LCMapStringEx", "case mapping failed"))
            return original_case(value)

        monkeypatch.setattr(fake_port, "ordinal_case_key", fail_nth)
        assert isinstance(
            _validate_opened_info(fake_port, path, workspace.root, info, regular=True),
            PathRejected,
        )
    monkeypatch.setattr(fake_port, "ordinal_case_key", original_case)

    escaped_path = replace(
        path,
        canonical_dos_path=r"C:\Else\project.json",
        long_path=r"\\?\C:\Else\project.json",
    )
    escaped_info = replace(info, final_dos_path=r"\\?\C:\Else\project.json")
    assert isinstance(
        _validate_opened_info(fake_port, escaped_path, workspace.root, escaped_info, regular=True),
        PathRejected,
    )
    unavailable = workspace.root.binding.model_copy(
        update={"volume_identity": UnavailableIdentity()}
    )
    unavailable_root = replace(
        workspace.root,
        binding=unavailable,
        path=replace(workspace.root.path, root_binding=unavailable),
    )
    assert isinstance(
        _validate_opened_info(fake_port, path, unavailable_root, info, regular=True),
        PathRejected,
    )
    assert isinstance(
        _validate_opened_info(
            fake_port, path, workspace.root, replace(info, volume_serial=99), regular=True
        ),
        PathRejected,
    )
    root_info = replace(
        info,
        attributes=FILE_ATTRIBUTE_DIRECTORY,
        final_dos_path="\\\\?\\" + workspace.root.path.canonical_dos_path,
        file_id_128=b"\xff" * 16,
    )
    assert (
        _validate_opened_info(
            fake_port,
            workspace.root.path,
            workspace.root,
            replace(root_info, file_id_128=None),
            regular=False,
        )
        is None
    )
    assert isinstance(
        _validate_opened_info(
            fake_port, workspace.root.path, workspace.root, root_info, regular=False
        ),
        PathRejected,
    )


def test_secure_read_and_delete_adapter_failure_paths(fake_port, monkeypatch) -> None:
    _, project = workspace_and_project(fake_port)
    path = project.project.metadata_path
    with pytest.raises(ValueError):
        secure_read_file(fake_port, path, -1)
    external = replace(path, role=PathRole.EXTERNAL_SOURCE_READ_ONLY, root_binding=None)
    assert not isinstance(secure_read_file(fake_port, external, 1024), SecureReadFailed)

    original_open = fake_port.open_file
    target_opens = 0

    def fail_second_open(long_path, access, share, disposition, flags):
        nonlocal target_opens
        if fake_port._key(long_path) == fake_port._key(path.long_path):
            target_opens += 1
            if target_opens == 2:
                return Win32Err(Win32Failure(802, "CreateFileW", "swapped before open"))
        return original_open(long_path, access, share, disposition, flags)

    monkeypatch.setattr(fake_port, "open_file", fail_second_open)
    assert isinstance(secure_read_file(fake_port, path, 1024), SecureReadFailed)
    monkeypatch.setattr(fake_port, "open_file", original_open)

    original_query = fake_port.query_file_info
    target_queries = 0

    def fail_second_query(handle):
        nonlocal target_queries
        key, _ = fake_port.handles[handle.value]
        if key == fake_port._key(path.long_path):
            target_queries += 1
            if target_queries == 2:
                return Win32Err(Win32Failure(803, "GetFileInformationByHandleEx", "failed"))
        return original_query(handle)

    monkeypatch.setattr(fake_port, "query_file_info", fail_second_query)
    assert isinstance(secure_read_file(fake_port, path, 1024), SecureReadFailed)
    monkeypatch.setattr(fake_port, "query_file_info", original_query)

    def fail_delete_open(long_path, access, share, disposition, flags):
        if access & 0x00010000:
            return Win32Err(Win32Failure(804, "CreateFileW", "delete open failed"))
        return original_open(long_path, access, share, disposition, flags)

    monkeypatch.setattr(fake_port, "open_file", fail_delete_open)
    delete_open_failed = secure_delete_file(fake_port, path)
    assert isinstance(delete_open_failed, SecureDeleteFailed)
    assert delete_open_failed.error.win32_code == 804
    monkeypatch.setattr(fake_port, "open_file", original_open)

    delete_handles: set[int] = set()

    def mark_delete_open(long_path, access, share, disposition, flags):
        result = original_open(long_path, access, share, disposition, flags)
        if access & 0x00010000 and isinstance(result, Win32Ok):
            delete_handles.add(result.value.value)
        return result

    def fail_delete_query(handle):
        if handle.value in delete_handles:
            return Win32Err(Win32Failure(805, "GetFileInformationByHandleEx", "failed"))
        return original_query(handle)

    monkeypatch.setattr(fake_port, "open_file", mark_delete_open)
    monkeypatch.setattr(fake_port, "query_file_info", fail_delete_query)
    delete_query_failed = secure_delete_file(fake_port, path)
    assert isinstance(delete_query_failed, SecureDeleteFailed)
    assert delete_query_failed.error.win32_code == 805
    monkeypatch.setattr(fake_port, "query_file_info", original_query)

    def reject_delete_query(handle):
        result = original_query(handle)
        if handle.value in delete_handles and isinstance(result, Win32Ok):
            return Win32Ok(replace(result.value, final_dos_path=r"\\?\C:\swapped"))
        return result

    monkeypatch.setattr(fake_port, "query_file_info", reject_delete_query)
    assert secure_delete_file(fake_port, path) is not None


def test_ancestor_swap_and_volume_change_are_rejected(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    original_query = fake_port.query_file_info

    def swapped_ancestor(handle):
        result = original_query(handle)
        key, _ = fake_port.handles[handle.value]
        if key == fake_port._key("C:\\") and isinstance(result, Win32Ok):
            return Win32Ok(replace(result.value, final_dos_path="\\\\?\\D:\\"))
        return result

    monkeypatch.setattr(fake_port, "query_file_info", swapped_ancestor)
    result = validate_path(
        fake_port,
        project.project.metadata_path.canonical_dos_path,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
        require_existing=True,
    )
    assert isinstance(result, PathRejected)
    monkeypatch.setattr(fake_port, "query_file_info", original_query)
    drive = fake_port.nodes[fake_port._key("C:\\")]
    drive.volume = 99
    result = validate_path(
        fake_port,
        project.project.metadata_path.canonical_dos_path,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
        require_existing=True,
    )
    assert isinstance(result, PathRejected)
    drive.volume = 1


def test_path_port_error_and_component_edge_branches(fake_port) -> None:
    fake_port.failures["LCMapStringEx"] = [1]
    rejected = validate_path(fake_port, r"C:\X", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(rejected, PathValidated)
    assert isinstance(pathing.path_lock_key(fake_port, rejected.path), PathRejected)
    fake_port.make_tree(r"C:\Root")
    root = ensure_directory_tree(fake_port, r"C:\Root")
    assert not isinstance(root, PathRejected)
    embedded = validate_path(
        fake_port,
        ("bad\\part",),
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    assert isinstance(embedded, PathRejected)
    fake_port.failures["LCMapStringEx"] = [1]
    internal = validate_path(
        fake_port,
        r"C:\Root\x",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    assert isinstance(internal, PathRejected)

    file = fake_port.add_file(r"C:\Root\file")
    fake_port.failures["GetFileInformationByHandleEx"] = [123]
    queried = validate_path(
        fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
    )
    assert isinstance(queried, PathRejected) and queried.error.win32_code == 123
    fake_port.nodes[fake_port._key(r"C:\Root")].attributes = FILE_ATTRIBUTE_NORMAL
    ancestor = validate_path(
        fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
    )
    assert isinstance(ancestor, PathRejected)
    fake_port.nodes[fake_port._key(r"C:\Root")].attributes = FILE_ATTRIBUTE_DIRECTORY


def test_workspace_binding_unavailable_and_mismatch_edges(fake_port) -> None:
    workspace = ensure_workspace(fake_port, r"C:\Root")
    assert isinstance(workspace, WorkspaceReady)
    unavailable_binding = workspace.root.binding.model_copy(
        update={"volume_identity": UnavailableIdentity()}
    )
    unavailable_root = replace(
        workspace.root,
        binding=unavailable_binding,
        path=replace(workspace.root.path, root_binding=unavailable_binding),
    )
    result = validate_path(
        fake_port,
        r"C:\Root\projects",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=unavailable_root,
        require_existing=True,
    )
    assert isinstance(result, PathRejected)
    bad_id_binding = workspace.root.binding.model_copy(
        update={"root_file_id": AvailableIdentity(scheme="file_id_128", value="00" * 16)}
    )
    bad_root = replace(
        workspace.root,
        binding=bad_id_binding,
        path=replace(workspace.root.path, root_binding=bad_id_binding),
    )
    result = validate_path(
        fake_port,
        r"C:\Root\projects",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=bad_root,
        require_existing=True,
    )
    assert isinstance(result, PathRejected)


def test_snapshot_internal_and_unavailable_key_markers(fake_port) -> None:
    workspace, project = workspace_and_project(fake_port)
    result = snapshot_file(fake_port, project.project.metadata_path)
    assert isinstance(result, SnapshotOk)
    snapshot = result.snapshot
    unavailable = replace(
        snapshot,
        creation_time=UnavailableIdentity(),
        change_time=UnavailableIdentity(),
        volume_id=UnavailableIdentity(),
        file_id=UnavailableIdentity(),
    )
    key = snapshots._snapshot_key(
        unavailable.size_bytes,
        unavailable.last_write_time,
        unavailable.creation_time,
        unavailable.change_time,
        unavailable.attributes,
        unavailable.volume_id,
        unavailable.file_id,
    )
    assert len(key) == 64
    assert type(compare_snapshots(unavailable, snapshot)).__name__ == "NotComparable"
    assert FileTime(123).value == 123


def test_atomic_private_error_read_write_and_cleanup_edges(fake_port) -> None:
    workspace, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    no_binding = replace(target, root_binding=None)
    with pytest.raises(ValueError):
        _root_from(no_binding)
    path_error = PathRejected(failure(ErrorCode.PATH_REPARSE, ErrorCategory.POLICY, "path", "bad"))
    assert _atomic_error(path_error, "x").code is ErrorCode.ATOMIC_PUBLISH_FAILED
    assert _atomic_error(path_error, "x", integrity=True).code is ErrorCode.ATOMIC_PUBLISH_INTEGRITY

    fake_port.failures["CreateFileW"] = [71]
    assert isinstance(_read_target(fake_port, target, 100), _TargetReadFailed)
    fake_port.failures["GetFileInformationByHandleEx"] = [72]
    assert isinstance(_read_target(fake_port, target, 100), _TargetReadFailed)
    node = fake_port.nodes[fake_port._key(target.long_path)]
    original_attributes = node.attributes
    node.attributes = FILE_ATTRIBUTE_DIRECTORY
    assert isinstance(_read_target(fake_port, target, 100), _TargetReadFailed)
    node.attributes = original_attributes
    assert isinstance(_read_target(fake_port, target, 1), _TargetReadFailed)
    fake_port.failures["ReadFile"] = [73]
    assert isinstance(_read_target(fake_port, target, 1000), _TargetReadFailed)

    unknown = replace(target, canonical_dos_path=r"C:\unknown.tmp", long_path=r"\\?\C:\unknown.tmp")
    diagnostics = _cleanup(fake_port, unknown, UUID(PROJECT_ID))
    assert diagnostics and diagnostics[0].code is ErrorCode.ATOMIC_PUBLISH_INTEGRITY
    owned_missing = replace(
        target,
        canonical_dos_path=rf"C:\.~matrix-2a-x-{PROJECT_ID}.tmp",
        long_path=rf"\\?\C:\.~matrix-2a-x-{PROJECT_ID}.tmp",
    )
    assert _cleanup(fake_port, owned_missing, UUID(PROJECT_ID))
    parent, _, _ = target.canonical_dos_path.rpartition("\\")
    safely_missing = replace(
        target,
        canonical_dos_path=rf"{parent}\.~matrix-2a-x-{PROJECT_ID}.tmp",
        long_path=rf"\\?\{parent}\.~matrix-2a-x-{PROJECT_ID}.tmp",
    )
    assert _cleanup(fake_port, safely_missing, UUID(PROJECT_ID)) == ()

    invalid_temp = _write_temp(fake_port, target, "bad:name", UUID(PROJECT_ID), b"x")
    assert isinstance(invalid_temp, AtomicPublishFailed)
    fake_port.failures["CreateFileW"] = [74]
    create_failed = _write_temp(fake_port, target, "x", UUID(PROJECT_ID), b"x")
    assert isinstance(create_failed, AtomicPublishFailed)
    fake_port.failures["CloseHandle"] = [75]
    close_failed = _write_temp(fake_port, target, "x", UUID(OTHER_ID), b"x")
    assert isinstance(close_failed, AtomicPublishFailed)


def test_publish_parent_temp_and_reopen_failure_edges(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    target = validate_path(
        fake_port,
        project.project.project_directory.canonical_dos_path + r"\new.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert isinstance(target, PathValidated)
    parent_key = fake_port._key(project.project.project_directory.long_path)
    parent_node = fake_port.nodes.pop(parent_key)
    assert isinstance(
        publish_initial(
            fake_port,
            target.path,
            b"x",
            lambda data: True,
            CancellationToken(),
            artifact="x",
        ),
        AtomicPublishFailed,
    )
    fake_port.nodes[parent_key] = parent_node

    original_query = fake_port.query_file_info

    def reject_temp(handle):
        key, _ = fake_port.handles[handle.value]
        if ".~MATRIX-2A" in key:
            fake_port.nodes[key].attributes |= 0x400
        return original_query(handle)

    monkeypatch.setattr(fake_port, "query_file_info", reject_temp)
    assert isinstance(
        publish_initial(
            fake_port,
            target.path,
            b"x",
            lambda data: True,
            CancellationToken(),
            artifact="x",
        ),
        AtomicPublishIntegrity,
    )


def test_cas_second_read_mutations_and_post_replace_edges(fake_port, monkeypatch) -> None:
    _, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    target_key = fake_port._key(target.long_path)
    original_read = fake_port.read_file
    target_reads = 0

    def mutate_valid(handle, maximum):
        nonlocal target_reads
        key, _ = fake_port.handles[handle.value]
        if key == target_key:
            target_reads += 1
            if target_reads == 2:
                fake_port.nodes[key].data[:] = canonical_bytes(replacement)
        return original_read(handle, maximum)

    monkeypatch.setattr(fake_port, "read_file", mutate_valid)
    result = _replace_project_cas_locked(
        fake_port,
        target,
        expected,
        replacement,
        CancellationToken(),
    )
    assert isinstance(result, CasConflict)

    fake_port.nodes[target_key].data[:] = canonical_bytes(expected)
    target_reads = 0

    def mutate_invalid(handle, maximum):
        nonlocal target_reads
        key, _ = fake_port.handles[handle.value]
        if key == target_key:
            target_reads += 1
            if target_reads == 2:
                fake_port.nodes[key].data[:] = b"invalid\n"
        return original_read(handle, maximum)

    monkeypatch.setattr(fake_port, "read_file", mutate_invalid)
    result = _replace_project_cas_locked(
        fake_port,
        target,
        expected,
        replacement,
        CancellationToken(),
    )
    assert isinstance(result, AtomicPublishIntegrity)

    fake_port.nodes[target_key].data[:] = canonical_bytes(expected)
    monkeypatch.setattr(fake_port, "read_file", original_read)
    original_replace = fake_port.replace_file

    def corrupt_after_replace(target_long, replacement_long, backup):
        result = original_replace(target_long, replacement_long, backup)
        fake_port.nodes[target_key].data[:] = b"corrupt\n"
        return result

    monkeypatch.setattr(fake_port, "replace_file", corrupt_after_replace)
    result = _replace_project_cas_locked(
        fake_port,
        target,
        expected,
        replacement,
        CancellationToken(),
    )
    assert isinstance(result, AtomicPublishIntegrity)


def test_workspace_metadata_io_edges(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    target_key = fake_port._key(project.project.metadata_path.long_path)
    fake_port.failures["GetFileInformationByHandleEx"] = [88]
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectOpenFailed)
    node = fake_port.nodes[target_key]
    node.attributes = FILE_ATTRIBUTE_DIRECTORY
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectMetadataInvalid)
    node.attributes = FILE_ATTRIBUTE_NORMAL
    fake_port.failures["ReadFile"] = [89]
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectOpenFailed)

    original_read = fake_port.read_file

    def short_read(handle, maximum):
        result = original_read(handle, maximum)
        if isinstance(result, Win32Ok) and result.value:
            return Win32Ok(result.value[:-1])
        return result

    monkeypatch.setattr(fake_port, "read_file", short_read)
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectMetadataInvalid)


def test_lock_lease_close_failure_context_and_root_failures(fake_port) -> None:
    acquired = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(acquired, LockAcquired)
    lease = acquired.lease
    fake_port.failures["CloseHandle"] = [9]
    with lease as held:
        assert held.held
    assert not lease.held
    with pytest.raises(RuntimeError):
        lease.__enter__()

    fake_port.failures["SHGetKnownFolderPath"] = [5]
    assert (
        type(acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())).__name__
        == "LockAccessDenied"
    )
    fake_port.failures["SHGetKnownFolderPath"] = [999]
    assert (
        type(acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())).__name__
        == "LockIoError"
    )
    path = validate_path(fake_port, r"C:\x", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(path, PathValidated)
    fake_port.failures["LCMapStringEx"] = [999]
    assert (
        type(acquire_path_lock(fake_port, path.path, CancellationToken())).__name__ == "LockIoError"
    )


def test_atomic_remaining_io_and_cas_classifications(fake_port, monkeypatch) -> None:
    _, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    assert _atomic_error(object(), "x").code is ErrorCode.ATOMIC_PUBLISH_FAILED
    original_read = fake_port.read_file

    def chunked(handle, maximum):
        return original_read(handle, min(maximum, 10))

    monkeypatch.setattr(fake_port, "read_file", chunked)
    read = _read_target(fake_port, target, 1024)
    assert isinstance(read, _TargetRead) and read.data == canonical_bytes(expected)
    monkeypatch.setattr(fake_port, "read_file", lambda handle, maximum: Win32Ok(b""))
    assert isinstance(_read_target(fake_port, target, 1024), _TargetReadFailed)
    monkeypatch.setattr(fake_port, "read_file", original_read)
    fake_port.failures["CreateFileW"] = [601]
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishFailed,
    )


def test_initial_cas_conflict_and_public_capability_type_guard(fake_port) -> None:
    _, project = workspace_and_project(fake_port)
    expected = project.project.document
    changed = expected.model_copy(update={"revision": 1})
    fake_port.nodes[fake_port._key(project.project.metadata_path.long_path)].data[:] = (
        canonical_bytes(changed)
    )
    result = _replace_project_cas_locked(
        fake_port,
        project.project.metadata_path,
        expected,
        changed,
        CancellationToken(),
    )
    assert isinstance(result, CasConflict)
    with pytest.raises(TypeError):
        atomic_project.replace_project_cas(
            fake_port,
            object(),
            changed,
            CancellationToken(),
            project_lock=object(),
        )


def test_lock_issuer_release_and_guard_misuse_are_fail_closed(fake_port) -> None:
    path = validate_path(fake_port, r"C:\x", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(path, PathValidated)
    with pytest.raises(TypeError):
        locks.LockLease(
            locks.LockKind.PROJECT,
            PROJECT_ID,
            path.path,
            OwnedHandle(1, lambda value: Win32Ok(None)),
            _seal=object(),
        )
    acquired = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(acquired, LockAcquired)
    guard = acquired.lease._project_mutation_authority(PROJECT_ID)
    assert guard is not None
    with guard:
        pass
    with pytest.raises(RuntimeError):
        guard.__exit__(None, None, None)
    acquired.lease.release()
    with pytest.raises(RuntimeError):
        acquired.lease.release()


def test_project_capability_constructor_requires_internal_issuer(fake_port) -> None:
    workspace, project = workspace_and_project(fake_port)
    with pytest.raises(TypeError):
        workspace_module.ProjectCapability(
            workspace,
            project.project.project_directory,
            project.project.metadata_path,
            project.project.document,
            workspace_module._ProjectTrust(),
        )


def test_atomic_foreign_initial_and_temp_creation_failure(fake_port, monkeypatch) -> None:
    _, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    foreign = expected.model_copy(update={"project_id": OTHER_ID})
    fake_port.nodes[fake_port._key(target.long_path)].data[:] = canonical_bytes(foreign)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishIntegrity,
    )
    fake_port.nodes[fake_port._key(target.long_path)].data[:] = canonical_bytes(expected)
    original_open = fake_port.open_file

    def fail_temp(path, access, share, disposition, flags):
        if disposition == CREATE_NEW:
            return Win32Err(Win32Failure(602, "CreateFileW", "temp"))
        return original_open(path, access, share, disposition, flags)

    monkeypatch.setattr(fake_port, "open_file", fail_temp)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishFailed,
    )


def test_atomic_post_publish_and_post_replace_read_edges(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    target = validate_path(
        fake_port,
        project.project.project_directory.canonical_dos_path + r"\new.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert isinstance(target, PathValidated)
    original_open = fake_port.open_file
    moved = False

    def fail_reopen(path, access, share, disposition, flags):
        if fake_port._key(path) == fake_port._key(target.path.long_path) and moved:
            return Win32Err(Win32Failure(603, "CreateFileW", "reopen"))
        return original_open(path, access, share, disposition, flags)

    def mark_move(port, source, destination):
        nonlocal moved
        del port, source, destination
        moved = True

    fake_port.on_move = mark_move
    monkeypatch.setattr(fake_port, "open_file", fail_reopen)
    assert isinstance(
        publish_initial(
            fake_port,
            target.path,
            b"x",
            lambda data: True,
            CancellationToken(),
            artifact="x",
        ),
        AtomicPublishFailed,
    )
    monkeypatch.setattr(fake_port, "open_file", original_open)
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    target_key = fake_port._key(project.project.metadata_path.long_path)
    original_replace = fake_port.replace_file

    def replace_with_other(target_long, replacement_long, backup):
        result = original_replace(target_long, replacement_long, backup)
        other = replacement.model_copy(update={"revision": 2})
        fake_port.nodes[target_key].data[:] = canonical_bytes(other)
        return result

    monkeypatch.setattr(fake_port, "replace_file", replace_with_other)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            project.project.metadata_path,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishIntegrity,
    )


def test_lock_private_classification_and_adapter_failures(fake_port, monkeypatch) -> None:
    assert (
        type(locks._lock_error(locks.LockKind.PATH, 5, "open", "denied")).__name__
        == "LockAccessDenied"
    )
    assert type(locks._lock_error(locks.LockKind.PATH, 999, "open", "io")).__name__ == "LockIoError"
    fake_port.failures["GetProcessTimes"] = [604]
    acquired = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(acquired, LockAcquired)
    assert acquired.diagnostic_errors[0].win32_code == 604
    acquired.lease.release()
    root = ensure_directory_tree(fake_port, r"C:\Diag")
    assert not isinstance(root, PathRejected)
    monkeypatch.setattr(
        locks,
        "ensure_directory_tree",
        lambda port, path: PathRejected(
            failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "diag", "bad")
        ),
    )
    assert locks._diagnose(
        fake_port,
        root,
        "key",
        UUID(PROJECT_ID),
        locks.LockKind.PATH,
        None,
        "failed",
        (1, 2),
    )


def test_workspace_orchestration_injected_boundaries(fake_port, monkeypatch) -> None:
    original_ensure = workspace_module.ensure_directory_tree

    def fail_projects(port, path):
        if path.endswith("\\projects"):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "projects", "bad")
            )
        return original_ensure(port, path)

    monkeypatch.setattr(workspace_module, "ensure_directory_tree", fail_projects)
    assert type(ensure_workspace(fake_port, r"C:\Injected")).__name__ == "WorkspaceInvalid"
    monkeypatch.setattr(workspace_module, "ensure_directory_tree", original_ensure)
    workspace = ensure_workspace(fake_port, r"C:\Injected2")
    assert isinstance(workspace, WorkspaceReady)
    original_validate = workspace_module.validate_path

    def reject_project_json(port, value, role, **kwargs):
        if isinstance(value, str) and value.endswith("project.json"):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "metadata", "bad")
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(workspace_module, "validate_path", reject_project_json)
    assert isinstance(
        create_project(
            fake_port,
            workspace,
            CancellationToken(),
            uuid_factory=lambda: UUID(PROJECT_ID),
        ),
        ProjectOpenFailed,
    )


def test_workspace_publish_race_and_invalid_top_level_json(fake_port) -> None:
    workspace = ensure_workspace(fake_port, r"C:\Race")
    assert isinstance(workspace, WorkspaceReady)

    def race(port, source, destination):
        del source
        port.add_file(port._dos(destination), b"foreign")

    fake_port.on_move = race
    result = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert type(result).__name__ == "ProjectAlreadyExists"
    fake_port.on_move = None
    metadata_path = (
        workspace.projects_directory.canonical_dos_path + "\\" + PROJECT_ID + "\\project.json"
    )
    fake_port.nodes[fake_port._key(metadata_path)].data[:] = b"[]\n"
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectMetadataInvalid)


def test_final_pathing_edges(fake_port, monkeypatch) -> None:
    calls = 0
    original_key = fake_port.ordinal_case_key

    def fail_second(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            return Win32Err(Win32Failure(701, "LCMapStringEx", "bad"))
        return original_key(value)

    monkeypatch.setattr(fake_port, "ordinal_case_key", fail_second)
    assert isinstance(pathing._within(fake_port, r"C:\A", r"C:\B"), PathRejected)
    monkeypatch.setattr(fake_port, "ordinal_case_key", original_key)

    fake_port.make_tree(r"C:\Root")
    root = ensure_directory_tree(fake_port, r"C:\Root")
    assert not isinstance(root, PathRejected)
    child = fake_port.add_file(r"C:\Root\child")
    child.volume = 2
    mismatch = validate_path(
        fake_port,
        child.path,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
        require_existing=True,
    )
    assert isinstance(mismatch, PathRejected)
    child.volume = 1
    unavailable = root.binding.model_copy(update={"root_file_id": UnavailableIdentity()})
    unavailable_root = replace(
        root,
        binding=unavailable,
        path=replace(root.path, root_binding=unavailable),
    )
    assert isinstance(
        validate_path(
            fake_port,
            child.path,
            PathRole.WORKSPACE_INTERNAL,
            workspace_root=unavailable_root,
            require_existing=True,
        ),
        PathValidated,
    )
    assert isinstance(pathing.validate_workspace_root(fake_port, r"\\server\root"), PathRejected)
    assert isinstance(pathing.validate_workspace_root(fake_port, child.path), PathRejected)
    fake_port.failures["LCMapStringEx"] = [702]
    assert isinstance(pathing.reject_case_collisions(fake_port, ("one",)), PathRejected)
    assert isinstance(ensure_directory_tree(fake_port, "relative"), PathRejected)

    original_create = fake_port.create_directory

    def create_file_instead(path):
        dos = fake_port._dos(path)
        if fake_port._key(dos) not in fake_port.nodes:
            fake_port.add_file(dos, b"")
        return Win32Ok(None)

    monkeypatch.setattr(fake_port, "create_directory", create_file_instead)
    assert isinstance(ensure_directory_tree(fake_port, r"C:\NotDir"), PathRejected)
    monkeypatch.setattr(fake_port, "create_directory", original_create)


def test_atomic_last_read_and_mutation_edges(fake_port, monkeypatch) -> None:
    workspace, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    node = fake_port.nodes[fake_port._key(target.long_path)]
    original_read = fake_port.read_file
    grew = False

    def grow_during_read(handle, maximum):
        nonlocal grew
        key, _ = fake_port.handles[handle.value]
        if key == fake_port._key(target.long_path) and not grew:
            node.data.extend(b"x")
            grew = True
        return original_read(handle, maximum)

    monkeypatch.setattr(fake_port, "read_file", grow_during_read)
    assert isinstance(_read_target(fake_port, target, 1024), _TargetReadFailed)
    node.data[:] = canonical_bytes(expected)
    monkeypatch.setattr(fake_port, "read_file", original_read)

    monkeypatch.setattr(
        atomic_project,
        "_read_target",
        lambda port, path, maximum: _TargetReadFailed(
            failure(ErrorCode.ATOMIC_PUBLISH_FAILED, ErrorCategory.IO, "read", "bad")
        ),
    )
    assert isinstance(
        publish_immutable(
            fake_port,
            target,
            b"different",
            lambda data: False,
            CancellationToken(),
            artifact="x",
        ),
        AtomicPublishFailed,
    )
    monkeypatch.undo()

    original = atomic_project._read_target
    reads = 0

    def fail_second(port, path, maximum):
        nonlocal reads
        reads += 1
        if reads == 2:
            return _TargetReadFailed(
                failure(ErrorCode.ATOMIC_PUBLISH_FAILED, ErrorCategory.IO, "read", "bad")
            )
        return original(port, path, maximum)

    monkeypatch.setattr(atomic_project, "_read_target", fail_second)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishFailed,
    )


def test_workspace_final_injected_boundaries(fake_port, monkeypatch) -> None:
    workspace = ensure_workspace(fake_port, r"C:\Boundaries")
    assert isinstance(workspace, WorkspaceReady)
    original_validate = workspace_module.validate_path

    def reject_candidate(port, value, role, **kwargs):
        if (
            isinstance(value, str)
            and value.endswith(PROJECT_ID)
            and not kwargs.get("require_existing")
        ):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "candidate", "bad")
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(workspace_module, "validate_path", reject_candidate)
    assert isinstance(
        create_project(
            fake_port, workspace, CancellationToken(), uuid_factory=lambda: UUID(PROJECT_ID)
        ),
        ProjectOpenFailed,
    )
    monkeypatch.setattr(workspace_module, "validate_path", original_validate)
    project = create_project(
        fake_port, workspace, CancellationToken(), uuid_factory=lambda: UUID(OTHER_ID)
    )
    assert isinstance(project, ProjectCreated)
    fake_port.failures["GetFileInformationByHandleEx"] = [703]
    assert isinstance(
        workspace_module._read_metadata(fake_port, project.project.metadata_path),
        _MetadataReadFailed,
    )


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4, 5, 7, 8, 9, 10])
def test_handle_key_error_propagation_edges(fake_port, monkeypatch, failure_call: int) -> None:
    fake_port.make_tree(r"C:\Root")
    root = ensure_directory_tree(fake_port, r"C:\Root")
    assert not isinstance(root, PathRejected)
    fake_port.add_file(r"C:\Root\file")
    original = pathing._key
    calls = 0

    def fail_nth(port, value):
        nonlocal calls
        calls += 1
        if calls == failure_call:
            return PathRejected(failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "key", "bad"))
        return original(port, value)

    monkeypatch.setattr(pathing, "_key", fail_nth)
    result = validate_path(
        fake_port,
        r"C:\Root\file",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
        require_existing=True,
    )
    assert isinstance(result, PathRejected)


def test_last_lock_diagnostic_and_root_edges(fake_port, monkeypatch) -> None:
    root = ensure_directory_tree(fake_port, r"C:\DiagRoot")
    assert not isinstance(root, PathRejected)
    original_validate = locks.validate_path
    monkeypatch.setattr(
        locks,
        "validate_path",
        lambda *args, **kwargs: PathRejected(
            failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "diagnostic_path", "bad")
        ),
    )
    assert locks._diagnose(
        fake_port,
        root,
        "key",
        UUID(PROJECT_ID),
        locks.LockKind.PATH,
        None,
        "failed",
        (1, 2),
    )
    monkeypatch.setattr(locks, "validate_path", original_validate)
    original_ensure = locks.ensure_directory_tree

    def reject_diagnostics(port, path):
        if path.endswith("\\diagnostics"):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "diagnostics", "bad")
            )
        return original_ensure(port, path)

    monkeypatch.setattr(locks, "ensure_directory_tree", reject_diagnostics)
    acquired = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(acquired, LockAcquired)
    assert acquired.diagnostic_errors
    acquired.lease.release()
    monkeypatch.setattr(locks, "ensure_directory_tree", original_ensure)
    monkeypatch.setattr(
        locks,
        "validate_path",
        lambda *args, **kwargs: PathRejected(
            failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "ownership", "bad")
        ),
    )
    assert (
        type(acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())).__name__
        == "LockIoError"
    )


def test_last_workspace_validation_edges(fake_port, monkeypatch) -> None:
    original_validate = workspace_module.validate_path

    def reject_projects(port, value, role, **kwargs):
        if (
            isinstance(value, str)
            and value.endswith("\\projects")
            and kwargs.get("require_existing")
        ):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "projects", "bad")
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(workspace_module, "validate_path", reject_projects)
    assert type(ensure_workspace(fake_port, r"C:\ProjectsReject")).__name__ == "WorkspaceInvalid"
    monkeypatch.setattr(workspace_module, "validate_path", original_validate)
    workspace = ensure_workspace(fake_port, r"C:\OpenReject")
    assert isinstance(workspace, WorkspaceReady)
    fake_port.make_tree(workspace.projects_directory.canonical_dos_path + "\\" + PROJECT_ID)

    def reject_metadata(port, value, role, **kwargs):
        if isinstance(value, str) and value.endswith("project.json"):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "metadata", "bad")
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(workspace_module, "validate_path", reject_metadata)
    assert isinstance(
        workspace_module._project_paths(fake_port, workspace, PROJECT_ID, existing=True),
        ProjectOpenFailed,
    )
    monkeypatch.setattr(workspace_module, "validate_path", original_validate)
    next_workspace = ensure_workspace(fake_port, r"C:\CheckReject")
    assert isinstance(next_workspace, WorkspaceReady)

    def reject_recheck(port, value, role, **kwargs):
        if isinstance(value, str) and value.endswith(PROJECT_ID) and kwargs.get("require_existing"):
            return PathRejected(
                failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "recheck", "bad")
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(workspace_module, "validate_path", reject_recheck)
    assert isinstance(
        create_project(
            fake_port,
            next_workspace,
            CancellationToken(),
            uuid_factory=lambda: UUID(PROJECT_ID),
        ),
        ProjectOpenFailed,
    )


def test_last_cas_foreign_second_and_final_read_edges(fake_port, monkeypatch) -> None:
    _, project = workspace_and_project(fake_port)
    target = project.project.metadata_path
    expected = project.project.document
    replacement = expected.model_copy(update={"revision": 1})
    target_key = fake_port._key(target.long_path)
    original_read = fake_port.read_file
    reads = 0

    def foreign_second(handle, maximum):
        nonlocal reads
        key, _ = fake_port.handles[handle.value]
        if key == target_key:
            reads += 1
            if reads == 2:
                foreign = expected.model_copy(update={"project_id": OTHER_ID})
                fake_port.nodes[key].data[:] = canonical_bytes(foreign)
        return original_read(handle, maximum)

    monkeypatch.setattr(fake_port, "read_file", foreign_second)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishIntegrity,
    )
    fake_port.nodes[target_key].data[:] = canonical_bytes(expected)
    monkeypatch.setattr(fake_port, "read_file", original_read)
    original_target_read = atomic_project._read_target
    calls = 0

    def fail_final(port, path, maximum):
        nonlocal calls
        calls += 1
        if calls == 3:
            return _TargetReadFailed(
                failure(ErrorCode.ATOMIC_PUBLISH_FAILED, ErrorCategory.IO, "final", "bad")
            )
        return original_target_read(port, path, maximum)

    monkeypatch.setattr(atomic_project, "_read_target", fail_final)
    assert isinstance(
        _replace_project_cas_locked(
            fake_port,
            target,
            expected,
            replacement,
            CancellationToken(),
        ),
        AtomicPublishFailed,
    )
