"""Package-2F workspace artifact paths, immutable publish, and state CAS."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    CasConflict,
    ImmutableConflict,
    PublishAlreadyExists,
    PublishCancelled,
    PublishOk,
    publish_immutable,
    publish_initial,
    replace_revision_cas,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.finalizer.errors import (
    ArtifactLocation,
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerFailure,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    MAX_STATE_BYTES,
    FinalizerState,
    parse_state_bytes,
)
from matrix_auto_cutter.phase2.locks import ProjectLockLease
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureReadFailed,
    ValidatedPath,
    ensure_directory_tree,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.phase2.workspace import ProjectCapability


@dataclass(frozen=True, slots=True)
class ArtifactStored:
    """Successful immutable artifact publication."""

    location: ArtifactLocation


@dataclass(frozen=True, slots=True)
class ArtifactStoreFailed:
    """Immutable publication failure or conflicting existing artifact."""

    error: FinalizerFailure


type ArtifactStoreResult = ArtifactStored | ArtifactStoreFailed


@dataclass(frozen=True, slots=True)
class StateStored:
    """Successful revision-CAS state publication."""

    state: FinalizerState
    location: ArtifactLocation


@dataclass(frozen=True, slots=True)
class StateStoreFailed:
    """Replaceable state publication failure."""

    error: FinalizerFailure


type StateStoreResult = StateStored | StateStoreFailed


def project_artifact_path(
    port: Win32Port,
    project: ProjectCapability,
    directories: tuple[str, ...],
    filename: str,
) -> ValidatedPath | FinalizerFailure:
    """Create only the explicitly requested project directories and validate the target."""
    current = project.project_directory.canonical_dos_path
    for component in directories:
        current += "\\" + component
        ensured = ensure_directory_tree(port, current)
        if isinstance(ensured, PathRejected):
            return failure(
                FinalizerErrorCode.FINALIZER_INTERNAL,
                FinalizerErrorCategory.IO,
                "artifact.directory",
                ensured.error.message,
                win32_code=ensured.error.win32_code,
                underlying=ensured.error,
            )
    target = validate_path(
        port,
        current + "\\" + filename,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=project.workspace.root,
    )
    if isinstance(target, PathRejected):
        return failure(
            FinalizerErrorCode.FINALIZER_INTERNAL,
            FinalizerErrorCategory.INTEGRITY,
            "artifact.path",
            target.error.message,
            win32_code=target.error.win32_code,
            underlying=target.error,
        )
    return target.path


def _location(path: ValidatedPath, data: bytes) -> ArtifactLocation:
    return ArtifactLocation(path.canonical_dos_path, hashlib.sha256(data).hexdigest(), len(data))


def store_immutable(
    port: Win32Port,
    target: ValidatedPath,
    data: bytes,
    parser: object,
    cancellation: CancellationToken,
    *,
    artifact: str,
    operation_id: UUID,
) -> ArtifactStoreResult:
    """Publish immutable canonical bytes, accepting only exact validated identity."""
    parse = parser

    def validates(candidate: bytes) -> bool:
        try:
            return callable(parse) and parse(candidate) is not None and candidate == data
        except (UnicodeError, ValueError):
            return False

    result = publish_immutable(
        port,
        target,
        data,
        validates,
        cancellation,
        artifact=artifact,
        operation_id=operation_id,
    )
    if isinstance(result, PublishOk):
        return ArtifactStored(_location(target, data))
    if isinstance(result, PublishCancelled):
        code = FinalizerErrorCode.CANCELLED
        category = FinalizerErrorCategory.CANCELLED
    elif isinstance(result, ImmutableConflict | PublishAlreadyExists | AtomicPublishIntegrity):
        code = FinalizerErrorCode.RECOVERY_CONFLICT
        category = FinalizerErrorCategory.INTEGRITY
    else:
        assert isinstance(result, AtomicPublishFailed | CasConflict)
        code = FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
        category = FinalizerErrorCategory.IO
    return ArtifactStoreFailed(
        failure(
            code,
            category,
            f"{artifact}.publish",
            result.error.message,
            win32_code=result.error.win32_code,
            cause=result.error.cause,
            underlying=result,
        )
    )


def _state_binding(data: bytes) -> tuple[str, int] | None:
    try:
        state = parse_state_bytes(data)
    except (UnicodeError, ValueError):
        return None
    return state.project_id, state.revision


def store_state(
    port: Win32Port,
    target: ValidatedPath,
    desired: FinalizerState,
    cancellation: CancellationToken,
    project_lock: ProjectLockLease,
    *,
    operation_id: UUID,
) -> StateStoreResult:
    """First-publish or revision-CAS one diagnostic finalizer state."""
    read = secure_read_file(port, target, MAX_STATE_BYTES)
    if isinstance(read, SecureReadFailed) and read.error.win32_code in {2, 3}:
        state = desired.model_copy(update={"revision": 0})
        data = canonical_bytes(state)
        result = publish_initial(
            port,
            target,
            data,
            lambda candidate: _state_binding(candidate) == (state.project_id, 0),
            cancellation,
            artifact="finalizer-state",
            operation_id=operation_id,
        )
    elif isinstance(read, SecureReadFailed):
        return StateStoreFailed(
            failure(
                FinalizerErrorCode.RECOVERY_CONFLICT,
                FinalizerErrorCategory.INTEGRITY,
                "state.read",
                read.error.message,
                win32_code=read.error.win32_code,
                underlying=read.error,
            )
        )
    else:
        try:
            existing = parse_state_bytes(read.data)
        except (UnicodeError, ValueError) as exc:
            return StateStoreFailed(
                failure(
                    FinalizerErrorCode.RECOVERY_CONFLICT,
                    FinalizerErrorCategory.INTEGRITY,
                    "state.parse",
                    str(exc),
                    cause=exc,
                )
            )
        state = desired.model_copy(update={"revision": existing.revision + 1})
        data = canonical_bytes(state)
        result = replace_revision_cas(
            port,
            target,
            read.data,
            data,
            _state_binding,
            cancellation,
            project_id=state.project_id,
            expected_revision=existing.revision,
            project_lock=project_lock,
            artifact="finalizer-state",
            maximum_bytes=MAX_STATE_BYTES,
            operation_id=operation_id,
        )
    if isinstance(result, PublishOk):
        return StateStored(state, _location(target, data))
    code = (
        FinalizerErrorCode.CANCELLED
        if isinstance(result, PublishCancelled)
        else FinalizerErrorCode.RECOVERY_CONFLICT
        if isinstance(result, CasConflict | AtomicPublishIntegrity | PublishAlreadyExists)
        else FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
    )
    return StateStoreFailed(
        failure(
            code,
            (
                FinalizerErrorCategory.CANCELLED
                if code is FinalizerErrorCode.CANCELLED
                else FinalizerErrorCategory.INTEGRITY
            ),
            "state.publish",
            result.error.message,
            win32_code=result.error.win32_code,
            underlying=result,
        )
    )
