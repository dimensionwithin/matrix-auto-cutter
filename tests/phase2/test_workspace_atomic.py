from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from matrix_auto_cutter.phase2.artifacts import ProjectDocument, canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    ImmutableConflict,
    PublishAlreadyExists,
    PublishCancelled,
    PublishOk,
    publish_immutable,
    publish_initial,
    replace_project_cas,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCode
from matrix_auto_cutter.phase2.locks import (
    LockAcquired,
    acquire_path_lock,
    acquire_project_lock,
)
from matrix_auto_cutter.phase2.pathing import PathRejected, PathRole, validate_path
from matrix_auto_cutter.phase2.workspace import (
    WORKSPACE_ROOT_ENV_VAR,
    InvalidProjectId,
    OrphanProjectDirectory,
    ProjectBindingMismatch,
    ProjectCreated,
    ProjectIdCollision,
    ProjectMetadataInvalid,
    ProjectMetadataMissing,
    ProjectOpened,
    ProjectOpenFailed,
    UnsupportedProjectVersion,
    WorkspaceInvalid,
    WorkspaceReady,
    classify_project_directory,
    create_project,
    ensure_workspace,
    open_project,
    resolve_default_workspace_root,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
SECOND_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"


def ready(fake_port) -> WorkspaceReady:
    result = ensure_workspace(fake_port, r"C:\Root\.matrix-auto-cutter")
    assert isinstance(result, WorkspaceReady)
    return result


def created(fake_port, workspace: WorkspaceReady, project_id: str = PROJECT_ID) -> ProjectCreated:
    result = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(project_id),
    )
    assert isinstance(result, ProjectCreated)
    return result


def test_resolve_default_workspace_root_uses_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKSPACE_ROOT_ENV_VAR, r"D:\CustomWorkspace")
    assert resolve_default_workspace_root() == r"D:\CustomWorkspace"


def test_resolve_default_workspace_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKSPACE_ROOT_ENV_VAR, raising=False)
    assert resolve_default_workspace_root() == str(Path.home() / ".matrix-auto-cutter")


def test_workspace_closed_layout_and_project_open(fake_port) -> None:
    workspace = ready(fake_port)
    result = created(fake_port, workspace)
    root_key = fake_port._key(workspace.root.path.canonical_dos_path)
    descendants = {
        key
        for key in fake_port.nodes
        if key == root_key or key.startswith(root_key.rstrip("\\") + "\\")
    }
    assert descendants == {
        root_key,
        fake_port._key(workspace.projects_directory.canonical_dos_path),
        fake_port._key(result.project.project_directory.canonical_dos_path),
        fake_port._key(result.project.metadata_path.canonical_dos_path),
    }
    assert result.project.document.revision == 0
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectOpened)
    extra = fake_port.add_file(
        result.project.project_directory.canonical_dos_path + r"\unknown.bin", b"keep"
    )
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectOpened)
    assert bytes(extra.data) == b"keep"


def test_workspace_failures(fake_port) -> None:
    fake_port.failures["CreateDirectoryW"] = [5]
    assert isinstance(ensure_workspace(fake_port, r"C:\Denied"), WorkspaceInvalid)
    workspace = ready(fake_port)
    token = CancellationToken()
    token.cancel()
    cancelled = create_project(fake_port, workspace, token)
    assert isinstance(cancelled, ProjectOpenFailed)
    assert cancelled.error.code is ErrorCode.CANCELLED
    invalid_uuid = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID("550e8400-e29b-11d4-a716-446655440000"),
    )
    assert isinstance(invalid_uuid, ProjectOpenFailed)
    fake_port.failures["CreateDirectoryW"] = [5]
    failed = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(failed, ProjectOpenFailed)


def test_zero_to_sixteen_collisions_and_noncollision_not_counted(fake_port) -> None:
    workspace = ready(fake_port)
    ids = [UUID(PROJECT_ID), UUID(SECOND_ID)]
    fake_port.make_tree(workspace.projects_directory.canonical_dos_path + "\\" + PROJECT_ID)
    result = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: ids.pop(0),
    )
    assert isinstance(result, ProjectCreated)
    collision_ids = [UUID(int=(4 << 76) | (2 << 62) | index) for index in range(16)]
    for value in collision_ids:
        fake_port.make_tree(workspace.projects_directory.canonical_dos_path + "\\" + str(value))
    iterator = iter(collision_ids)
    exhausted = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: next(iterator),
    )
    assert isinstance(exhausted, ProjectIdCollision)


def test_crash_orphan_missing_corrupt_version_and_binding(fake_port) -> None:
    workspace = ready(fake_port)
    fake_port.failures["FlushFileBuffers"] = [111]
    failed = create_project(
        fake_port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(failed, ProjectOpenFailed)
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectMetadataMissing)
    assert isinstance(
        classify_project_directory(fake_port, workspace, PROJECT_ID), OrphanProjectDirectory
    )

    metadata = fake_port.add_file(
        workspace.projects_directory.canonical_dos_path + "\\" + PROJECT_ID + r"\project.json",
        b"not-json\n",
    )
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectMetadataInvalid)
    metadata.data[:] = b'{"artifact_type":"matrix_project","schema_version":"9.0"}\n'
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), UnsupportedProjectVersion)
    wrong = ProjectDocument(
        project_id=SECOND_ID,
        workspace_root_binding=workspace.root.binding,
        revision=0,
    )
    metadata.data[:] = canonical_bytes(wrong)
    assert isinstance(open_project(fake_port, workspace, PROJECT_ID), ProjectBindingMismatch)
    assert isinstance(open_project(fake_port, workspace, "INVALID"), InvalidProjectId)


def test_atomic_initial_race_immutable_and_cancellation_cleanup(fake_port) -> None:
    workspace = ready(fake_port)
    target = validate_path(
        fake_port,
        workspace.projects_directory.canonical_dos_path + r"\artifact.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert not isinstance(target, PathRejected)
    token = CancellationToken()
    token.cancel()
    cancelled = publish_initial(
        fake_port, target.path, b"x", lambda data: data == b"x", token, artifact="test"
    )
    assert isinstance(cancelled, PublishCancelled)
    assert not any(".~MATRIX-2A" in key for key in fake_port.nodes)

    def race(port, source, destination) -> None:
        del source
        port.add_file(port._dos(destination), b"other")

    fake_port.on_move = race
    raced = publish_initial(
        fake_port,
        target.path,
        b"wanted",
        lambda data: data == b"wanted",
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(raced, PublishAlreadyExists)
    assert bytes(fake_port.nodes[fake_port._key(target.path.long_path)].data) == b"other"
    fake_port.on_move = None
    conflict = publish_immutable(
        fake_port,
        target.path,
        b"wanted",
        lambda data: data == b"wanted",
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(conflict, ImmutableConflict)
    same = publish_immutable(
        fake_port,
        target.path,
        b"other",
        lambda data: data == b"other",
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(same, PublishOk)


@pytest.mark.parametrize(
    ("operation", "code"),
    [
        ("WriteFile", 101),
        ("FlushFileBuffers", 102),
        ("MoveFileExW", 104),
    ],
)
def test_atomic_write_flush_close_move_failures(fake_port, operation: str, code: int) -> None:
    workspace = ready(fake_port)
    target = validate_path(
        fake_port,
        workspace.projects_directory.canonical_dos_path + r"\artifact.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert not isinstance(target, PathRejected)
    fake_port.failures[operation] = [code]
    result = publish_initial(
        fake_port,
        target.path,
        b"bytes",
        lambda data: data == b"bytes",
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(result, AtomicPublishFailed)
    assert result.error.win32_code == code


def test_partial_write_cleanup_secondary_and_post_validation(fake_port) -> None:
    workspace = ready(fake_port)
    target = validate_path(
        fake_port,
        workspace.projects_directory.canonical_dos_path + r"\artifact.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=workspace.root,
    )
    assert not isinstance(target, PathRejected)
    fake_port.partial_write = 0
    fake_port.failures["SetFileInformationByHandle"] = [999]
    failed = publish_initial(
        fake_port,
        target.path,
        b"bytes",
        lambda data: True,
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(failed, AtomicPublishFailed)
    assert failed.cleanup_diagnostics[0].win32_code == 999
    fake_port.partial_write = None
    integrity = publish_initial(
        fake_port,
        target.path,
        b"bytes",
        lambda data: False,
        CancellationToken(),
        artifact="test",
    )
    assert isinstance(integrity, AtomicPublishIntegrity)


def test_project_revision_cas_success_conflict_integrity_and_replace_failure(fake_port) -> None:
    workspace = ready(fake_port)
    capability = created(fake_port, workspace).project
    replacement = capability.document.model_copy(update={"revision": 1})
    with pytest.raises(TypeError):
        replace_project_cas(
            fake_port,
            capability,
            replacement,
            CancellationToken(),
            project_lock=True,
        )
    path_lock = acquire_path_lock(fake_port, capability.metadata_path, CancellationToken())
    assert isinstance(path_lock, LockAcquired)
    with pytest.raises(TypeError):
        replace_project_cas(
            fake_port,
            capability,
            replacement,
            CancellationToken(),
            project_lock=path_lock.lease,
        )
    path_lock.lease.release()
    locked = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(locked, LockAcquired)
    success = replace_project_cas(
        fake_port,
        capability,
        replacement,
        CancellationToken(),
        project_lock=locked.lease,
    )
    assert isinstance(success, PublishOk)
    assert not capability.trusted
    assert isinstance(
        replace_project_cas(
            fake_port,
            capability,
            replacement,
            CancellationToken(),
            project_lock=locked.lease,
        ),
        AtomicPublishIntegrity,
    )
    opened = open_project(fake_port, workspace, PROJECT_ID)
    assert isinstance(opened, ProjectOpened)
    current = opened.project
    node = fake_port.nodes[fake_port._key(current.metadata_path.long_path)]
    node.data[:] = b"foreign\n"
    integrity = replace_project_cas(
        fake_port,
        current,
        replacement.model_copy(update={"revision": 2}),
        CancellationToken(),
        project_lock=locked.lease,
    )
    assert isinstance(integrity, AtomicPublishIntegrity)
    assert not current.trusted

    node.data[:] = canonical_bytes(replacement)
    reopened = open_project(fake_port, workspace, PROJECT_ID)
    assert isinstance(reopened, ProjectOpened)
    fake_port.failures["ReplaceFileW"] = [777]
    failed = replace_project_cas(
        fake_port,
        reopened.project,
        replacement.model_copy(update={"revision": 2}),
        CancellationToken(),
        project_lock=locked.lease,
    )
    assert isinstance(failed, AtomicPublishFailed)
    assert failed.error.win32_code == 777
    assert not reopened.project.trusted
    locked.lease.release()


def test_invalid_replacement_and_cas_cancellation(fake_port) -> None:
    workspace = ready(fake_port)
    capability = created(fake_port, workspace).project
    locked = acquire_project_lock(fake_port, PROJECT_ID, CancellationToken())
    assert isinstance(locked, LockAcquired)
    with pytest.raises(ValueError):
        replace_project_cas(
            fake_port,
            capability,
            capability.document.model_copy(update={"revision": 2}),
            CancellationToken(),
            project_lock=locked.lease,
        )
    token = CancellationToken()
    token.cancel()
    result = replace_project_cas(
        fake_port,
        capability,
        capability.document.model_copy(update={"revision": 1}),
        token,
        project_lock=locked.lease,
    )
    assert isinstance(result, PublishCancelled)
    assert capability.trusted
    locked.lease.release()
