from __future__ import annotations

from dataclasses import replace
from datetime import UTC
from uuid import UUID, uuid4

import pytest
from tests.phase2.finalizer.conftest import (
    PROJECT_ID,
    RUN_ID,
    add_validated_file,
    loaded_legacy,
    make_intent,
)

from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    CasConflict,
    PublishAlreadyExists,
    PublishCancelled,
    PublishOk,
    publish_external_create_if_absent,
    replace_revision_cas,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import RecheckOk
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode
from matrix_auto_cutter.phase2.errors import failure as phase2_failure
from matrix_auto_cutter.phase2.finalizer.errors import (
    FinalizerDiagnostic,
    FinalizerErrorCategory,
    FinalizerErrorCode,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    FinalizerState,
    FinalizerStateName,
    JournalInputProfile,
    parse_intent_bytes,
)
from matrix_auto_cutter.phase2.finalizer.persistence import (
    ArtifactStored,
    ArtifactStoreFailed,
    StateStored,
    StateStoreFailed,
    _state_binding,
    project_artifact_path,
    store_immutable,
    store_state,
)
from matrix_auto_cutter.phase2.finalizer.publisher import (
    SidecarPublished,
    SidecarPublishFailed,
    TargetInvalid,
    TargetMissing,
    TargetValid,
    publish_sidecar,
    read_committed_sidecar,
    sidecar_bytes,
    validate_target,
)
from matrix_auto_cutter.phase2.finalizer.sidecar_builder import build_sidecar
from matrix_auto_cutter.phase2.locks import (
    LockAcquired,
    LockIoError,
    LockKind,
    ProjectLockLease,
    acquire_project_lock,
    acquire_target_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    PathValidated,
    ValidatedPath,
    derive_external_target,
)
from matrix_auto_cutter.phase2.win32_port import RawFileInfo, Win32Ok
from matrix_auto_cutter.phase2.workspace import (
    ProjectCreated,
    WorkspaceReady,
    create_project,
    ensure_workspace,
)
from matrix_auto_cutter.sidecar import ObsEventSidecar


def _project(port):
    workspace = ensure_workspace(port, r"C:\Workspace")
    assert isinstance(workspace, WorkspaceReady)
    created = create_project(
        port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(created, ProjectCreated)
    return created.project


def _project_lock(port) -> ProjectLockLease:
    acquired = acquire_project_lock(
        port,
        PROJECT_ID,
        CancellationToken(),
        run_id=uuid4(),
    )
    assert isinstance(acquired, LockAcquired)
    assert isinstance(acquired.lease, ProjectLockLease)
    return acquired.lease


def _state() -> FinalizerState:
    from datetime import datetime

    return FinalizerState(
        project_id=PROJECT_ID,
        finalizer_run_id="2e157a84-2e31-49d9-b64e-494c24f8f612",
        revision=0,
        current_state=FinalizerStateName.PREPARING_INTENT,
        input_profile=JournalInputProfile.LEGACY,
        recording_id="835fc47a-7e8c-4700-9f6f-8f7e23ac740c",
        intent_id="not_available",
        target_generation="not_available",
        journal_sha256="a" * 64,
        source_identity_digest="not_available",
        sidecar_sha256="not_available",
        last_safe_transition="confirming_identity->preparing_intent",
        error_or_cancel_reference="not_available",
        observed_at=datetime(2026, 7, 17, tzinfo=UTC),
        recovery_status="normal",
    )


def test_error_cleanup_is_bounded_and_failure_constructor() -> None:
    base = failure(
        FinalizerErrorCode.FINALIZER_INTERNAL,
        FinalizerErrorCategory.INTERNAL,
        "phase",
        "message",
    )
    diagnostics = tuple(FinalizerDiagnostic(str(index), "x") for index in range(20))
    assert len(base.with_cleanup(diagnostics).cleanup_diagnostics) == 8
    assert base.message == "message"


def test_project_artifact_immutable_and_state_cas(fake_port) -> None:
    project = _project(fake_port)
    path = project_artifact_path(
        fake_port,
        project,
        ("runs", "2e157a84-2e31-49d9-b64e-494c24f8f612"),
        "finalization-intent.json",
    )
    assert isinstance(path, ValidatedPath)
    intent = make_intent(loaded_legacy(fake_port))
    data = canonical_bytes(intent)
    stored = store_immutable(
        fake_port,
        path,
        data,
        parse_intent_bytes,
        CancellationToken(),
        artifact="intent",
        operation_id=uuid4(),
    )
    assert isinstance(stored, ArtifactStored)
    again = store_immutable(
        fake_port,
        path,
        data,
        parse_intent_bytes,
        CancellationToken(),
        artifact="intent",
        operation_id=uuid4(),
    )
    assert isinstance(again, ArtifactStored)
    conflict = store_immutable(
        fake_port,
        path,
        b"{}\n",
        lambda value: value,
        CancellationToken(),
        artifact="intent",
        operation_id=uuid4(),
    )
    assert isinstance(conflict, ArtifactStoreFailed)

    state_path = project_artifact_path(
        fake_port,
        project,
        ("runs", "2e157a84-2e31-49d9-b64e-494c24f8f612", "state"),
        "finalizer-state.json",
    )
    assert isinstance(state_path, ValidatedPath)
    lock = _project_lock(fake_port)
    try:
        first = store_state(
            fake_port,
            state_path,
            _state(),
            CancellationToken(),
            lock,
            operation_id=uuid4(),
        )
        assert isinstance(first, StateStored)
        second = store_state(
            fake_port,
            state_path,
            _state().model_copy(update={"current_state": FinalizerStateName.CONSTRUCTING_SIDECAR}),
            CancellationToken(),
            lock,
            operation_id=uuid4(),
        )
        assert isinstance(second, StateStored)
        assert second.state.revision == 1
    finally:
        lock.release()


def test_persistence_failure_paths(fake_port) -> None:
    project = _project(fake_port)
    fake_port.failures["CreateDirectoryW"] = [5]
    failed_path = project_artifact_path(fake_port, project, ("new",), "x.json")
    assert not isinstance(failed_path, ValidatedPath)

    state_path = project_artifact_path(fake_port, project, ("state",), "state.json")
    assert isinstance(state_path, ValidatedPath)
    fake_port.add_file(state_path.canonical_dos_path, b"bad\n")
    lock = _project_lock(fake_port)
    try:
        assert isinstance(
            store_state(
                fake_port,
                state_path,
                _state(),
                CancellationToken(),
                lock,
                operation_id=uuid4(),
            ),
            StateStoreFailed,
        )
        missing_path = project_artifact_path(fake_port, project, ("state",), "missing.json")
        assert isinstance(missing_path, ValidatedPath)
        fake_port.failures["CreateFileW"] = [5]
        assert isinstance(
            store_state(
                fake_port,
                missing_path,
                _state(),
                CancellationToken(),
                lock,
                operation_id=uuid4(),
            ),
            StateStoreFailed,
        )
    finally:
        lock.release()
    assert _state_binding(b"bad\n") is None


def test_project_artifact_final_path_rejection(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.persistence as module

    project = _project(fake_port)
    original = module.validate_path

    def reject_final(port, value, role, **kwargs):
        if str(value).endswith("x.json"):
            from matrix_auto_cutter.phase2.pathing import PathRejected

            return PathRejected(
                phase2_failure(
                    ErrorCode.PATH_INPUT_FORM,
                    ErrorCategory.INPUT,
                    "path",
                    "rejected",
                )
            )
        return original(port, value, role, **kwargs)

    monkeypatch.setattr(module, "validate_path", reject_final)
    assert not isinstance(
        project_artifact_path(fake_port, project, ("valid",), "x.json"),
        ValidatedPath,
    )


def test_immutable_cancel_io_and_parser_failure(fake_port) -> None:
    project = _project(fake_port)
    path = project_artifact_path(fake_port, project, ("artifacts",), "value.json")
    assert isinstance(path, ValidatedPath)
    token = CancellationToken()
    token.cancel()
    cancelled = store_immutable(
        fake_port,
        path,
        b"{}\n",
        lambda value: value,
        token,
        artifact="value",
        operation_id=uuid4(),
    )
    assert isinstance(cancelled, ArtifactStoreFailed)
    assert cancelled.error.code is FinalizerErrorCode.CANCELLED

    path2 = project_artifact_path(fake_port, project, ("artifacts",), "io.json")
    assert isinstance(path2, ValidatedPath)
    fake_port.failures["WriteFile"] = [5]
    io = store_immutable(
        fake_port,
        path2,
        b"{}\n",
        lambda value: value,
        CancellationToken(),
        artifact="value",
        operation_id=uuid4(),
    )
    assert isinstance(io, ArtifactStoreFailed)
    assert io.error.code is FinalizerErrorCode.ATOMIC_PUBLISH_FAILED

    path3 = project_artifact_path(fake_port, project, ("artifacts",), "parser.json")
    assert isinstance(path3, ValidatedPath)
    fake_port.add_file(path3.canonical_dos_path, b"{}\n")

    def bad_parser(value):
        raise ValueError("bad")

    assert isinstance(
        store_immutable(
            fake_port,
            path3,
            b"{}\n",
            bad_parser,
            CancellationToken(),
            artifact="value",
            operation_id=uuid4(),
        ),
        ArtifactStoreFailed,
    )


def _sidecar_context(port):
    source = add_validated_file(port, r"C:\Sources\source.mp4", b"source")
    target_result = derive_external_target(port, source, "source.obs-events.json")
    journal = loaded_legacy(port)
    intent = make_intent(journal)
    temp_result = derive_external_target(
        port,
        source,
        f".source.obs-events.json.tmp.{intent.finalizer_run_id}",
    )
    assert hasattr(target_result, "path") and hasattr(temp_result, "path")
    sidecar = build_sidecar(journal, intent)
    assert isinstance(sidecar, ObsEventSidecar)
    return source, target_result.path, temp_result.path, intent, sidecar


def test_external_target_derivation_rejects_every_untrusted_boundary(
    fake_port,
    monkeypatch,
) -> None:
    import matrix_auto_cutter.phase2.pathing as module

    project = _project(fake_port)
    assert isinstance(
        derive_external_target(fake_port, project.metadata_path, "target.json"),
        PathRejected,
    )
    source = add_validated_file(fake_port, r"C:\Sources\source.mp4", b"source")
    assert isinstance(derive_external_target(fake_port, source, ".."), PathRejected)
    without_parent = replace(
        source,
        canonical_dos_path="source.mp4",
        long_path="source.mp4",
    )
    assert isinstance(
        derive_external_target(fake_port, without_parent, "target.json"),
        PathRejected,
    )

    original = module.validate_path
    rejected = PathRejected(
        phase2_failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "parent", "failed")
    )
    monkeypatch.setattr(module, "validate_path", lambda *args, **kwargs: rejected)
    assert derive_external_target(fake_port, source, "target.json") is rejected

    for info in (
        None,
        RawFileInfo(0, 0, 0, 0, 0, 1, b"1" * 16, r"C:\Sources", "NTFS", 3),
    ):
        monkeypatch.setattr(
            module,
            "validate_path",
            lambda *args, _info=info, **kwargs: PathValidated(source, _info),
        )
        assert isinstance(
            derive_external_target(fake_port, source, "target.json"),
            PathRejected,
        )
    monkeypatch.setattr(module, "validate_path", original)
    derived = derive_external_target(fake_port, source, "target.json")
    assert isinstance(derived, PathValidated)
    assert derived.path.role is PathRole.EXTERNAL_TARGET_CREATE_ONLY


def test_target_lock_requires_create_only_path_and_valid_key(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.locks as module

    source, target, _, _, _ = _sidecar_context(fake_port)
    assert isinstance(
        acquire_target_lock(fake_port, source, CancellationToken()),
        LockIoError,
    )
    rejected = PathRejected(
        phase2_failure(ErrorCode.PATH_OS_ERROR, ErrorCategory.IO, "key", "failed")
    )
    monkeypatch.setattr(module, "path_lock_key", lambda *args: rejected)
    assert isinstance(
        acquire_target_lock(fake_port, target, CancellationToken()),
        LockIoError,
    )
    monkeypatch.undo()
    acquired = acquire_target_lock(fake_port, target, CancellationToken(), run_id=uuid4())
    assert isinstance(acquired, LockAcquired)
    assert acquired.lease.kind is LockKind.TARGET
    assert acquired.lease.release() is None


def test_committed_sidecar_reader_and_conflicts(fake_port, monkeypatch) -> None:
    _, target, _, intent, sidecar = _sidecar_context(fake_port)
    assert isinstance(read_committed_sidecar(fake_port, target), TargetMissing)
    fake_port.add_file(target.canonical_dos_path, sidecar_bytes(sidecar))
    assert isinstance(read_committed_sidecar(fake_port, target), TargetValid)
    assert isinstance(validate_target(fake_port, target, intent, sidecar), TargetValid)
    assert isinstance(validate_target(fake_port, target, None, sidecar), TargetValid)

    foreign = sidecar.model_copy(
        update={
            "lifecycle": sidecar.lifecycle.model_copy(
                update={"finalizer_run_id": UUID("99999999-9999-4999-8999-999999999999")}
            )
        }
    )
    assert isinstance(validate_target(fake_port, target, intent, foreign), TargetInvalid)

    original = fake_port.query_file_info

    def linked(handle):
        value = original(handle)
        assert isinstance(value, Win32Ok)
        return Win32Ok(replace(value.value, number_of_links=2))

    monkeypatch.setattr(fake_port, "query_file_info", linked)
    assert isinstance(read_committed_sidecar(fake_port, target), TargetInvalid)


def test_sidecar_reader_io_phase1_noncanonical_and_size(fake_port, monkeypatch) -> None:
    _, target, _, intent, sidecar = _sidecar_context(fake_port)
    fake_port.failures["CreateFileW"] = [5]
    assert isinstance(read_committed_sidecar(fake_port, target), TargetInvalid)

    fake_port.add_file(
        target.canonical_dos_path,
        (sidecar.model_dump_json(indent=2) + "\n").encode(),
    )
    assert isinstance(read_committed_sidecar(fake_port, target), TargetInvalid)

    fake_port.nodes[fake_port._key(target.canonical_dos_path)].data[:] = sidecar_bytes(sidecar)
    foreign_source = intent.source_identity.model_copy(update={"sha256": "f" * 64})
    assert isinstance(read_committed_sidecar(fake_port, target, foreign_source), TargetInvalid)

    import matrix_auto_cutter.phase2.finalizer.publisher as module

    monkeypatch.setattr(module, "MAX_SIDECAR_BYTES", 1)
    with pytest.raises(ValueError, match="exceeds"):
        sidecar_bytes(sidecar)


@pytest.mark.parametrize("payload", [b"bad\n", b"\xef\xbb\xbf{}\n", b"{}"])
def test_sidecar_reader_rejects_corrupt_noncanonical(fake_port, payload) -> None:
    _, target, _, _, _ = _sidecar_context(fake_port)
    fake_port.add_file(target.canonical_dos_path, payload)
    assert isinstance(read_committed_sidecar(fake_port, target), TargetInvalid)


def test_target_source_alias_is_rejected(fake_port) -> None:
    source, target, _, intent, sidecar = _sidecar_context(fake_port)
    source_node = fake_port.nodes[fake_port._key(source.canonical_dos_path)]
    fake_port.add_file(target.canonical_dos_path, sidecar_bytes(sidecar))
    target_node = fake_port.nodes[fake_port._key(target.canonical_dos_path)]
    target_node.file_id = source_node.file_id
    target_node.volume = source_node.volume
    values = intent.model_dump()
    values["source_identity"] = intent.source_identity
    values["bundle_binding"] = intent.bundle_binding
    values["source_volume_id"] = f"{source_node.volume:016x}"
    values["source_file_id"] = source_node.file_id.hex()
    alias_intent = type(intent).model_construct(**values)
    assert isinstance(validate_target(fake_port, target, alias_intent, sidecar), TargetInvalid)


def test_external_create_only_atomic_success_partial_and_existing(fake_port) -> None:
    _, target, temp, _, _ = _sidecar_context(fake_port)
    fake_port.partial_write = 2
    result = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"abcdef",
        lambda data: data == b"abcdef",
        lambda: None,
        owned_temp_suffix=f".tmp.{RUN_ID}",
    )
    assert isinstance(result, PublishOk)
    assert bytes(fake_port.nodes[fake_port._key(target.canonical_dos_path)].data) == b"abcdef"

    _, target2, temp2, _, _ = _sidecar_context(fake_port)
    fake_port.partial_write = None
    fake_port.add_file(target2.canonical_dos_path, b"foreign")
    result = publish_external_create_if_absent(
        fake_port,
        target2,
        temp2,
        b"new",
        lambda data: data == b"new",
        lambda: None,
        owned_temp_suffix=f".tmp.{RUN_ID}",
    )
    assert isinstance(result, PublishAlreadyExists)
    assert bytes(fake_port.nodes[fake_port._key(target2.canonical_dos_path)].data) == b"foreign"


def test_external_create_only_cancel_integrity_and_io(fake_port) -> None:
    source = add_validated_file(fake_port, r"C:\Other\source.mp4", b"source")

    def paths(name: str):
        target = derive_external_target(fake_port, source, f"{name}.json")
        temp = derive_external_target(fake_port, source, f".{name}.json.tmp.run")
        return target.path, temp.path

    target, temp = paths("cancel")
    cancelled = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"data",
        lambda data: True,
        lambda: phase2_failure(
            ErrorCode.CANCELLED,
            ErrorCategory.CANCELLED,
            "commit",
            "cancelled",
        ),
        owned_temp_suffix=".tmp.run",
    )
    assert isinstance(cancelled, PublishCancelled)

    target, temp = paths("integrity")
    integrity = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"data",
        lambda data: True,
        lambda: phase2_failure(
            ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
            ErrorCategory.INTEGRITY,
            "commit",
            "changed",
        ),
        owned_temp_suffix=".tmp.run",
    )
    assert isinstance(integrity, AtomicPublishIntegrity)

    target, temp = paths("flush")
    fake_port.failures["FlushFileBuffers"] = [5]
    assert isinstance(
        publish_external_create_if_absent(
            fake_port,
            target,
            temp,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=".tmp.run",
        ),
        AtomicPublishFailed,
    )

    target, temp = paths("post")
    assert isinstance(
        publish_external_create_if_absent(
            fake_port,
            target,
            temp,
            b"data",
            lambda data: False,
            lambda: None,
            owned_temp_suffix=".tmp.run",
        ),
        AtomicPublishIntegrity,
    )

    target, temp = paths("move-io")
    fake_port.failures["MoveFileExW"] = [5]
    assert isinstance(
        publish_external_create_if_absent(
            fake_port,
            target,
            temp,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=".tmp.run",
        ),
        AtomicPublishFailed,
    )


@pytest.mark.parametrize("partial", [None, 2])
def test_external_temp_write_observes_cancellation(fake_port, monkeypatch, partial) -> None:
    source = add_validated_file(fake_port, r"C:\Cancel\source.mp4", b"source")
    target = derive_external_target(fake_port, source, "cancelled.json").path
    temp = derive_external_target(fake_port, source, ".cancelled.json.tmp.run").path
    token = CancellationToken()
    original = fake_port.write_file
    fake_port.partial_write = partial

    def cancel_after_write(handle, data):
        result = original(handle, data)
        token.cancel()
        return result

    monkeypatch.setattr(fake_port, "write_file", cancel_after_write)
    result = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"abcdef",
        lambda data: True,
        lambda: None,
        owned_temp_suffix=".tmp.run",
        cancellation=token,
    )
    assert isinstance(result, PublishCancelled)
    assert fake_port._key(target.canonical_dos_path) not in fake_port.nodes
    assert fake_port._key(temp.canonical_dos_path) not in fake_port.nodes


def test_external_temp_checks_cancel_before_create_and_after_flush(fake_port, monkeypatch) -> None:
    source = add_validated_file(fake_port, r"C:\CancelFlush\source.mp4", b"source")

    def paths(name):
        return (
            derive_external_target(fake_port, source, f"{name}.json").path,
            derive_external_target(fake_port, source, f".{name}.json.tmp.run").path,
        )

    target, temp = paths("before")
    token = CancellationToken()
    token.cancel()
    result = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"data",
        lambda data: True,
        lambda: None,
        owned_temp_suffix=".tmp.run",
        cancellation=token,
    )
    assert isinstance(result, PublishCancelled)
    assert fake_port._key(temp.canonical_dos_path) not in fake_port.nodes

    target, temp = paths("flush")
    token = CancellationToken()
    original_flush = fake_port.flush_file

    def cancel_after_flush(handle):
        result = original_flush(handle)
        token.cancel()
        return result

    monkeypatch.setattr(fake_port, "flush_file", cancel_after_flush)
    result = publish_external_create_if_absent(
        fake_port,
        target,
        temp,
        b"data",
        lambda data: True,
        lambda: None,
        owned_temp_suffix=".tmp.run",
        cancellation=token,
    )
    assert isinstance(result, PublishCancelled)
    assert fake_port._key(target.canonical_dos_path) not in fake_port.nodes
    assert fake_port._key(temp.canonical_dos_path) not in fake_port.nodes


def test_external_atomic_ownership_write_and_revalidation_edges(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.atomic_project as module

    source, target, temp, _, _ = _sidecar_context(fake_port)

    def candidate(name: str) -> ValidatedPath:
        result = derive_external_target(
            fake_port,
            source,
            f".{name}.json.tmp.{RUN_ID}",
        )
        assert hasattr(result, "path")
        return result.path

    with pytest.raises(ValueError, match="create-only temp"):
        module._write_external_temp(fake_port, source, b"x", ".tmp")
    assert module._cleanup_external(fake_port, source, ".tmp")
    assert module._cleanup_external(fake_port, temp, "")
    assert (
        module.cleanup_external_owned_temp(
            fake_port,
            temp,
            owned_suffix=f".tmp.{RUN_ID}",
        )
        == ()
    )

    fake_port.add_file(temp.canonical_dos_path, b"owned")
    fake_port.failures["CreateFileW"] = [5]
    assert module._cleanup_external(fake_port, temp, f".tmp.{RUN_ID}")

    for operation, code in (("CreateFileW", 5), ("WriteFile", 5)):
        current = candidate(operation)
        fake_port.failures[operation] = [code]
        assert isinstance(
            module._write_external_temp(
                fake_port,
                current,
                b"data",
                f".tmp.{RUN_ID}",
            ),
            AtomicPublishFailed,
        )

    current = candidate("zero")
    fake_port.partial_write = 0
    assert isinstance(
        module._write_external_temp(fake_port, current, b"data", f".tmp.{RUN_ID}"),
        AtomicPublishFailed,
    )
    fake_port.partial_write = None

    current = candidate("close")
    candidate_key = fake_port._key(current.canonical_dos_path)
    fake_port.close_results[candidate_key] = [5]
    assert isinstance(
        module._write_external_temp(fake_port, current, b"data", f".tmp.{RUN_ID}"),
        AtomicPublishFailed,
    )

    with pytest.raises(ValueError, match="create-only target"):
        publish_external_create_if_absent(
            fake_port,
            source,
            temp,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=f".tmp.{RUN_ID}",
        )
    with pytest.raises(ValueError, match="distinct"):
        publish_external_create_if_absent(
            fake_port,
            target,
            target,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=f".tmp.{RUN_ID}",
        )

    other_source = add_validated_file(fake_port, r"C:\OtherParent\source.mp4", b"source")
    other_temp_result = derive_external_target(
        fake_port,
        other_source,
        f".source.obs-events.json.tmp.{RUN_ID}",
    )
    assert hasattr(other_temp_result, "path")
    with pytest.raises(ValueError, match="same-directory"):
        publish_external_create_if_absent(
            fake_port,
            target,
            other_temp_result.path,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=f".tmp.{RUN_ID}",
        )

    current = candidate("revalidate")
    original_validate = module.validate_path

    def reject_temp(port, value, role, **kwargs):
        if value == current.canonical_dos_path and kwargs.get("require_existing"):
            return PathRejected(
                phase2_failure(
                    ErrorCode.PATH_OS_ERROR,
                    ErrorCategory.IO,
                    "temp",
                    "rejected",
                    win32_code=5,
                )
            )
        return original_validate(port, value, role, **kwargs)

    monkeypatch.setattr(module, "validate_path", reject_temp)
    assert isinstance(
        publish_external_create_if_absent(
            fake_port,
            target,
            current,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=f".tmp.{RUN_ID}",
        ),
        AtomicPublishIntegrity,
    )
    monkeypatch.setattr(module, "validate_path", original_validate)

    current = candidate("read")
    original_read = module._read_target
    monkeypatch.setattr(
        module,
        "_read_target",
        lambda *args, **kwargs: module._TargetReadFailed(
            phase2_failure(
                ErrorCode.ATOMIC_PUBLISH_FAILED,
                ErrorCategory.IO,
                "target.read",
                "failed",
            )
        ),
    )
    assert isinstance(
        publish_external_create_if_absent(
            fake_port,
            target,
            current,
            b"data",
            lambda data: True,
            lambda: None,
            owned_temp_suffix=f".tmp.{RUN_ID}",
        ),
        AtomicPublishFailed,
    )
    monkeypatch.setattr(module, "_read_target", original_read)


def test_generic_revision_cas_guards_and_failure_matrix(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.atomic_project as module

    project = _project(fake_port)
    lock = _project_lock(fake_port)

    def path(name: str, data: bytes = b"0") -> ValidatedPath:
        value = project_artifact_path(fake_port, project, ("cas",), f"{name}.json")
        assert isinstance(value, ValidatedPath)
        fake_port.add_file(value.canonical_dos_path, data)
        return value

    def validator(data: bytes):
        if data in {b"0", b"1"}:
            return PROJECT_ID, int(data)
        return None

    internal = path("guards")
    source, _, _, _, _ = _sidecar_context(fake_port)
    with pytest.raises(ValueError, match="workspace-internal"):
        replace_revision_cas(
            fake_port,
            source,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        )
    with pytest.raises(ValueError, match="positive"):
        replace_revision_cas(
            fake_port,
            internal,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=0,
        )
    with pytest.raises(ValueError, match="exceeds"):
        replace_revision_cas(
            fake_port,
            internal,
            b"0",
            b"too-large",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=2,
        )
    with pytest.raises(TypeError, match="Project Lock"):
        replace_revision_cas(
            fake_port,
            internal,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=object(),
            artifact="state",
            maximum_bytes=10,
        )
    with pytest.raises(ValueError, match="live matching"):
        replace_revision_cas(
            fake_port,
            internal,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id="99999999-9999-4999-8999-999999999999",
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        )

    read_path = path("read")
    fake_port.failures["CreateFileW"] = [5]
    assert isinstance(
        replace_revision_cas(
            fake_port,
            read_path,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        AtomicPublishFailed,
    )
    assert isinstance(
        replace_revision_cas(
            fake_port,
            path("invalid", b"x"),
            b"x",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        AtomicPublishIntegrity,
    )
    assert isinstance(
        replace_revision_cas(
            fake_port,
            path("conflict"),
            b"different",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        CasConflict,
    )
    with pytest.raises(ValueError, match="increment"):
        replace_revision_cas(
            fake_port,
            path("replacement"),
            b"0",
            b"x",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        )

    failed = AtomicPublishFailed(
        phase2_failure(ErrorCode.ATOMIC_PUBLISH_FAILED, ErrorCategory.IO, "temp", "failed")
    )
    with monkeypatch.context() as patcher:
        patcher.setattr(module, "_write_temp", lambda *args, **kwargs: failed)
        assert (
            replace_revision_cas(
                fake_port,
                path("temp"),
                b"0",
                b"1",
                validator,
                CancellationToken(),
                project_id=PROJECT_ID,
                expected_revision=0,
                project_lock=lock,
                artifact="state",
                maximum_bytes=10,
            )
            is failed
        )

    def read_failure(phase: str):
        return module._TargetReadFailed(
            phase2_failure(
                ErrorCode.ATOMIC_PUBLISH_FAILED,
                ErrorCategory.IO,
                phase,
                "failed",
            )
        )

    original_read = module._read_target
    with monkeypatch.context() as patcher:
        calls = 0

        def second_read(*args, **kwargs):
            nonlocal calls
            calls += 1
            return read_failure("second") if calls == 2 else original_read(*args, **kwargs)

        patcher.setattr(module, "_read_target", second_read)
        assert isinstance(
            replace_revision_cas(
                fake_port,
                path("second"),
                b"0",
                b"1",
                validator,
                CancellationToken(),
                project_id=PROJECT_ID,
                expected_revision=0,
                project_lock=lock,
                artifact="state",
                maximum_bytes=10,
            ),
            AtomicPublishFailed,
        )

    with monkeypatch.context() as patcher:
        calls = 0

        def changed_read(*args, **kwargs):
            nonlocal calls
            calls += 1
            value = original_read(*args, **kwargs)
            if calls == 2 and isinstance(value, module._TargetRead):
                return replace(value, data=b"changed")
            return value

        patcher.setattr(module, "_read_target", changed_read)
        assert isinstance(
            replace_revision_cas(
                fake_port,
                path("changed"),
                b"0",
                b"1",
                validator,
                CancellationToken(),
                project_id=PROJECT_ID,
                expected_revision=0,
                project_lock=lock,
                artifact="state",
                maximum_bytes=10,
            ),
            CasConflict,
        )

    token = CancellationToken()
    token.cancel()
    assert isinstance(
        replace_revision_cas(
            fake_port,
            path("cancel"),
            b"0",
            b"1",
            validator,
            token,
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        PublishCancelled,
    )

    fake_port.failures["ReplaceFileW"] = [5]
    assert isinstance(
        replace_revision_cas(
            fake_port,
            path("replace"),
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        AtomicPublishFailed,
    )

    with monkeypatch.context() as patcher:
        calls = 0

        def final_read(*args, **kwargs):
            nonlocal calls
            calls += 1
            return read_failure("final") if calls == 3 else original_read(*args, **kwargs)

        patcher.setattr(module, "_read_target", final_read)
        assert isinstance(
            replace_revision_cas(
                fake_port,
                path("final"),
                b"0",
                b"1",
                validator,
                CancellationToken(),
                project_id=PROJECT_ID,
                expected_revision=0,
                project_lock=lock,
                artifact="state",
                maximum_bytes=10,
            ),
            AtomicPublishFailed,
        )

    calls = 0

    def invalid_final(data: bytes):
        nonlocal calls
        calls += 1
        return None if calls == 3 else validator(data)

    assert isinstance(
        replace_revision_cas(
            fake_port,
            path("post"),
            b"0",
            b"1",
            invalid_final,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        AtomicPublishIntegrity,
    )

    success = path("success")
    fake_port.nodes[fake_port._key(success.canonical_dos_path)].file_id = None
    assert isinstance(
        replace_revision_cas(
            fake_port,
            success,
            b"0",
            b"1",
            validator,
            CancellationToken(),
            project_id=PROJECT_ID,
            expected_revision=0,
            project_lock=lock,
            artifact="state",
            maximum_bytes=10,
        ),
        PublishOk,
    )
    lock.release()


class _Usage:
    def __init__(self, *, recheck=True, commit=True):
        self.recheck_ok = recheck
        self.commit_ok = commit

    def recheck(self, cancellation):
        del cancellation
        return RecheckOk(None) if self.recheck_ok else object()

    def commit(self, cancellation):
        del cancellation
        return self.commit_ok


def _publish_context(port):
    source, target, temp, intent, sidecar = _sidecar_context(port)
    intent_result = derive_external_target(port, source, "intent.json")
    assert hasattr(intent_result, "path")
    intent_data = canonical_bytes(intent)
    port.add_file(intent_result.path.canonical_dos_path, intent_data)
    return target, temp, intent_result.path, intent_data, intent, sidecar


def test_sidecar_publish_revalidation_failures(fake_port) -> None:
    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)
    bad_intent = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data + b"x",
        intent,
        sidecar,
        _Usage(),
        CancellationToken(),
    )
    assert isinstance(bad_intent, SidecarPublishFailed)

    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)
    fake_port.add_file(target.canonical_dos_path, sidecar_bytes(sidecar))
    appeared = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data,
        intent,
        sidecar,
        _Usage(),
        CancellationToken(),
    )
    assert isinstance(appeared, SidecarPublished)
    assert appeared.idempotent

    del fake_port.nodes[fake_port._key(target.canonical_dos_path)]
    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)
    recheck = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data,
        intent,
        sidecar,
        _Usage(recheck=False),
        CancellationToken(),
    )
    assert isinstance(recheck, SidecarPublishFailed)

    fake_port.nodes.pop(fake_port._key(target.canonical_dos_path), None)
    for key in tuple(fake_port.nodes):
        if ".TMP." in key:
            del fake_port.nodes[key]
    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)
    rejected_commit = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data,
        intent,
        sidecar,
        _Usage(commit=False),
        CancellationToken(),
    )
    assert isinstance(rejected_commit, SidecarPublishFailed)
    assert rejected_commit.error.code is FinalizerErrorCode.TARGET_ALREADY_EXISTS

    fake_port.nodes.pop(fake_port._key(target.canonical_dos_path), None)
    for key in tuple(fake_port.nodes):
        if ".TMP." in key:
            del fake_port.nodes[key]
    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)
    token = CancellationToken()
    token.cancel()
    cancelled = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data,
        intent,
        sidecar,
        _Usage(commit=False),
        token,
    )
    assert isinstance(cancelled, SidecarPublishFailed)
    assert cancelled.error.code is FinalizerErrorCode.CANCELLED


def test_sidecar_publish_atomic_error_classification(fake_port, monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.publisher as module

    target, temp, intent_path, intent_data, intent, sidecar = _publish_context(fake_port)

    def failed(*args, **kwargs):
        del args, kwargs
        return AtomicPublishFailed(
            phase2_failure(ErrorCode.ATOMIC_PUBLISH_FAILED, ErrorCategory.IO, "move", "io")
        )

    monkeypatch.setattr(module, "publish_external_create_if_absent", failed)
    result = publish_sidecar(
        fake_port,
        target,
        temp,
        intent_path,
        intent_data,
        intent,
        sidecar,
        _Usage(),
        CancellationToken(),
    )
    assert isinstance(result, SidecarPublishFailed)
    assert result.error.code is FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
