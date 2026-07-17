"""Atomic create-if-absent and cooperative revision-CAS primitives."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from matrix_auto_cutter.phase2.artifacts import (
    MAX_PROJECT_BYTES,
    ProjectDocument,
    canonical_bytes,
    parse_project_bytes,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail, failure
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureDeleteFailed,
    SecureReadFailed,
    ValidatedPath,
    ValidatedWorkspaceRoot,
    secure_delete_file,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import (
    CREATE_NEW,
    ERROR_ALREADY_EXISTS,
    ERROR_FILE_EXISTS,
    FILE_ATTRIBUTE_NORMAL,
    GENERIC_READ,
    GENERIC_WRITE,
    Win32Err,
    Win32Port,
)


@dataclass(frozen=True, slots=True)
class PublishOk:
    """Fully post-validated publish result."""

    target: ValidatedPath
    bytes_written: int


@dataclass(frozen=True, slots=True)
class PublishAlreadyExists:
    """Create-if-absent target collision."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class ImmutableConflict:
    """Existing immutable target differs from intended bytes."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class AtomicPublishFailed:
    """OS-level publish failure with secondary cleanup diagnostics."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class AtomicPublishIntegrity:
    """Unproven or unexpected final target state."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class CasConflict:
    """Expected cooperative revision mismatch."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


@dataclass(frozen=True, slots=True)
class PublishCancelled:
    """Cancellation linearized before commit."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


PublishResult = (
    PublishOk
    | PublishAlreadyExists
    | ImmutableConflict
    | AtomicPublishFailed
    | AtomicPublishIntegrity
    | CasConflict
    | PublishCancelled
)

RevisionValidator = Callable[[bytes], tuple[str, int] | None]


def _root_from(path: ValidatedPath) -> ValidatedWorkspaceRoot:
    binding = path.root_binding
    if binding is None:
        raise ValueError("atomic project paths require a workspace binding")
    root_path = ValidatedPath(
        PathRole.WORKSPACE_INTERNAL,
        binding.canonical_dos_path,
        binding.canonical_dos_path,
        "\\\\?\\" + binding.canonical_dos_path,
        binding,
        ("root_binding",),
    )
    return ValidatedWorkspaceRoot(root_path, binding)


def _atomic_error(error: object, phase: str, *, integrity: bool = False) -> ErrorDetail:
    if isinstance(error, Win32Err):
        return failure(
            ErrorCode.ATOMIC_PUBLISH_INTEGRITY if integrity else ErrorCode.ATOMIC_PUBLISH_FAILED,
            ErrorCategory.INTEGRITY if integrity else ErrorCategory.IO,
            phase,
            error.error.detail,
            win32_code=error.error.code,
        )
    if isinstance(error, PathRejected):
        close_validation = error.error.phase.startswith("close_validation_")
        return failure(
            ErrorCode.ATOMIC_PUBLISH_INTEGRITY if integrity else ErrorCode.ATOMIC_PUBLISH_FAILED,
            (
                error.error.category
                if close_validation
                else ErrorCategory.INTEGRITY
                if integrity
                else ErrorCategory.POLICY
            ),
            error.error.phase if close_validation else phase,
            error.error.message,
            win32_code=error.error.win32_code,
            cause=error.error.cause,
        )
    return failure(
        ErrorCode.ATOMIC_PUBLISH_INTEGRITY if integrity else ErrorCode.ATOMIC_PUBLISH_FAILED,
        ErrorCategory.INTEGRITY if integrity else ErrorCategory.IO,
        phase,
        str(error),
    )


@dataclass(frozen=True, slots=True)
class _TargetRead:
    data: bytes
    volume_serial: int
    file_id_128: bytes | None


@dataclass(frozen=True, slots=True)
class _TargetReadFailed:
    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


def _read_target(
    port: Win32Port, target: ValidatedPath, maximum: int
) -> _TargetRead | _TargetReadFailed:
    read = secure_read_file(port, target, maximum)
    if isinstance(read, SecureReadFailed):
        return _TargetReadFailed(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                read.error.category,
                read.error.phase,
                read.error.message,
                win32_code=read.error.win32_code,
                cause=read.error.cause,
            ),
            read.diagnostics,
        )
    return _TargetRead(read.data, read.file_info.volume_serial, read.file_info.file_id_128)


def _cleanup(port: Win32Port, temp: ValidatedPath, operation_id: UUID) -> tuple[ErrorDetail, ...]:
    marker = f"-{operation_id}.tmp"
    if (
        not temp.canonical_dos_path.endswith(marker)
        or ".~matrix-2a-" not in temp.canonical_dos_path
    ):
        return (
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "cleanup",
                "temp ownership could not be proven",
            ),
        )
    deleted = secure_delete_file(port, temp)
    if isinstance(deleted, SecureDeleteFailed):
        if deleted.error.win32_code in {2, 3}:
            return deleted.diagnostics[:8]
        primary = failure(
            ErrorCode.ATOMIC_PUBLISH_FAILED,
            deleted.error.category,
            deleted.error.phase,
            deleted.error.message,
            win32_code=deleted.error.win32_code,
            cause=deleted.error.cause,
        )
        return (primary, *deleted.diagnostics)[:8]
    return ()


def _write_temp(
    port: Win32Port,
    target: ValidatedPath,
    artifact: str,
    operation_id: UUID,
    data: bytes,
) -> ValidatedPath | AtomicPublishFailed:
    parent, _, _ = target.canonical_dos_path.rpartition("\\")
    name = f".~matrix-2a-{artifact}-{operation_id}.tmp"
    root = _root_from(target)
    validated = validate_path(
        port,
        parent + "\\" + name,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    if isinstance(validated, PathRejected):
        return AtomicPublishFailed(_atomic_error(validated, "validate_temp"))
    temp = validated.path
    opened = port.open_file(
        temp.long_path,
        GENERIC_READ | GENERIC_WRITE,
        0,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL,
    )
    if isinstance(opened, Win32Err):
        return AtomicPublishFailed(_atomic_error(opened, "create_temp"))
    handle = opened.value
    primary: ErrorDetail | None = None
    offset = 0
    try:
        while offset < len(data):
            written = port.write_file(handle, data[offset:])
            if isinstance(written, Win32Err):
                primary = _atomic_error(written, "write_temp")
                break
            if written.value <= 0 or written.value > len(data) - offset:
                primary = failure(
                    ErrorCode.ATOMIC_PUBLISH_FAILED,
                    ErrorCategory.IO,
                    "write_temp",
                    "partial write made no valid progress",
                )
                break
            offset += written.value
        if primary is None:
            flushed = port.flush_file(handle)
            if isinstance(flushed, Win32Err):
                primary = _atomic_error(flushed, "flush_temp")
    finally:
        closed = handle.close()
        if primary is None and isinstance(closed, Win32Err):
            primary = _atomic_error(closed, "close_temp")
    if primary is not None:
        return AtomicPublishFailed(primary, _cleanup(port, temp, operation_id))
    return temp


def _cleanup_external(
    port: Win32Port,
    temp: ValidatedPath,
    owned_suffix: str,
) -> tuple[ErrorDetail, ...]:
    if (
        temp.role is not PathRole.EXTERNAL_TARGET_CREATE_ONLY
        or not owned_suffix
        or not temp.canonical_dos_path.endswith(owned_suffix)
    ):
        return (
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "external_cleanup",
                "external temp ownership could not be proven",
            ),
        )
    deleted = secure_delete_file(port, temp)
    if isinstance(deleted, SecureDeleteFailed):
        if deleted.error.win32_code in {2, 3}:
            return deleted.diagnostics[:8]
        return (
            failure(
                ErrorCode.ATOMIC_PUBLISH_FAILED,
                deleted.error.category,
                deleted.error.phase,
                deleted.error.message,
                win32_code=deleted.error.win32_code,
                cause=deleted.error.cause,
            ),
            *deleted.diagnostics,
        )[:8]
    return ()


def cleanup_external_owned_temp(
    port: Win32Port,
    temp: ValidatedPath,
    *,
    owned_suffix: str,
) -> tuple[ErrorDetail, ...]:
    """Delete only one exact caller-bound external temp or return diagnostics."""
    return _cleanup_external(port, temp, owned_suffix)


def _write_external_temp(
    port: Win32Port,
    temp: ValidatedPath,
    data: bytes,
    owned_suffix: str,
    cancellation: CancellationToken | None = None,
) -> AtomicPublishFailed | PublishCancelled | None:
    if temp.role is not PathRole.EXTERNAL_TARGET_CREATE_ONLY:
        raise ValueError("external publish requires a create-only temp capability")
    if cancellation is not None and cancellation.is_cancelled:
        return PublishCancelled(
            failure(
                ErrorCode.CANCELLED,
                ErrorCategory.CANCELLED,
                "create_external_temp",
                "external temp creation cancelled",
            )
        )
    opened = port.open_file(
        temp.long_path,
        GENERIC_READ | GENERIC_WRITE,
        0,
        CREATE_NEW,
        FILE_ATTRIBUTE_NORMAL,
    )
    if isinstance(opened, Win32Err):
        return AtomicPublishFailed(_atomic_error(opened, "create_external_temp"))
    primary: ErrorDetail | None = None
    offset = 0
    try:
        while offset < len(data):
            if cancellation is not None and cancellation.is_cancelled:
                primary = failure(
                    ErrorCode.CANCELLED,
                    ErrorCategory.CANCELLED,
                    "write_external_temp",
                    "external temp write cancelled",
                )
                break
            written = port.write_file(opened.value, data[offset:])
            if isinstance(written, Win32Err):
                primary = _atomic_error(written, "write_external_temp")
                break
            if written.value <= 0 or written.value > len(data) - offset:
                primary = failure(
                    ErrorCode.ATOMIC_PUBLISH_FAILED,
                    ErrorCategory.IO,
                    "write_external_temp",
                    "partial write made no valid progress",
                )
                break
            offset += written.value
        if primary is None and cancellation is not None and cancellation.is_cancelled:
            primary = failure(
                ErrorCode.CANCELLED,
                ErrorCategory.CANCELLED,
                "write_external_temp",
                "external temp write cancelled",
            )
        if primary is None:
            flushed = port.flush_file(opened.value)
            if isinstance(flushed, Win32Err):
                primary = _atomic_error(flushed, "flush_external_temp")
            elif cancellation is not None and cancellation.is_cancelled:
                primary = failure(
                    ErrorCode.CANCELLED,
                    ErrorCategory.CANCELLED,
                    "flush_external_temp",
                    "external temp publish cancelled after flush",
                )
    finally:
        closed = opened.value.close()
        if primary is None and isinstance(closed, Win32Err):
            primary = _atomic_error(closed, "close_external_temp")
    if primary is not None:
        cleanup = _cleanup_external(port, temp, owned_suffix)
        if primary.code is ErrorCode.CANCELLED:
            return PublishCancelled(primary, cleanup)
        return AtomicPublishFailed(primary, cleanup)
    return None


def publish_external_create_if_absent(
    port: Win32Port,
    target: ValidatedPath,
    temp: ValidatedPath,
    data: bytes,
    validator: Callable[[bytes], bool],
    commit: Callable[[], ErrorDetail | None],
    *,
    owned_temp_suffix: str,
    cancellation: CancellationToken | None = None,
) -> PublishResult:
    """Publish one exact same-directory external target without replacement."""
    if (
        target.role is not PathRole.EXTERNAL_TARGET_CREATE_ONLY
        or temp.role is not PathRole.EXTERNAL_TARGET_CREATE_ONLY
    ):
        raise ValueError("external publish requires create-only target capabilities")
    target_parent = target.canonical_dos_path.rpartition("\\")[0]
    temp_parent = temp.canonical_dos_path.rpartition("\\")[0]
    if target_parent != temp_parent or target.canonical_dos_path == temp.canonical_dos_path:
        raise ValueError("external target and temp must be distinct same-directory paths")
    written = _write_external_temp(port, temp, data, owned_temp_suffix, cancellation)
    if written is not None:
        return written
    temp_check = validate_path(
        port,
        temp.canonical_dos_path,
        PathRole.EXTERNAL_TARGET_CREATE_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(temp_check, PathRejected):
        return AtomicPublishIntegrity(
            _atomic_error(temp_check, "revalidate_external_temp", integrity=True),
            (*temp_check.diagnostics, *_cleanup_external(port, temp, owned_temp_suffix))[:8],
        )
    commit_error = commit()
    if commit_error is not None:
        cleanup = _cleanup_external(port, temp, owned_temp_suffix)
        if commit_error.code is ErrorCode.CANCELLED:
            return PublishCancelled(commit_error, cleanup)
        return AtomicPublishIntegrity(commit_error, cleanup)
    moved = port.move_no_replace(temp.long_path, target.long_path)
    if isinstance(moved, Win32Err):
        cleanup = _cleanup_external(port, temp, owned_temp_suffix)
        if moved.error.code in {ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS}:
            return PublishAlreadyExists(
                failure(
                    ErrorCode.PROJECT_ALREADY_EXISTS,
                    ErrorCategory.CONCURRENCY,
                    "move_no_replace",
                    moved.error.detail,
                    win32_code=moved.error.code,
                ),
                cleanup,
            )
        return AtomicPublishFailed(_atomic_error(moved, "move_no_replace"), cleanup)
    actual = _read_target(port, target, max(len(data), 1))
    if isinstance(actual, _TargetReadFailed):
        return AtomicPublishFailed(actual.error, actual.diagnostics)
    if actual.data != data or not validator(actual.data):
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "external_post_validate",
                "published external target failed byte or schema validation",
            )
        )
    return PublishOk(target, len(data))


def publish_initial(
    port: Win32Port,
    target: ValidatedPath,
    data: bytes,
    validator: Callable[[bytes], bool],
    cancellation: CancellationToken,
    *,
    artifact: str,
    operation_id: UUID | None = None,
) -> PublishResult:
    """Publish the first version without replacement and post-validate it."""
    op_id = operation_id or uuid4()
    parent, _, _ = target.canonical_dos_path.rpartition("\\")
    parent_check = validate_path(
        port,
        parent,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=_root_from(target),
        require_existing=True,
    )
    if isinstance(parent_check, PathRejected):
        return AtomicPublishFailed(
            _atomic_error(parent_check, "validate_parent"), parent_check.diagnostics
        )
    temp_result = _write_temp(port, target, artifact, op_id, data)
    if isinstance(temp_result, AtomicPublishFailed):
        return temp_result
    temp = temp_result
    temp_check = validate_path(
        port,
        temp.canonical_dos_path,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=_root_from(target),
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(temp_check, PathRejected):
        return AtomicPublishIntegrity(
            _atomic_error(temp_check, "revalidate_temp", integrity=True),
            (*temp_check.diagnostics, *_cleanup(port, temp, op_id))[:8],
        )
    if cancellation.begin_irreversible_commit() is None:
        cleanup = _cleanup(port, temp, op_id)
        return PublishCancelled(
            failure(ErrorCode.CANCELLED, ErrorCategory.CANCELLED, "publish", "operation cancelled"),
            cleanup,
        )
    moved = port.move_no_replace(temp.long_path, target.long_path)
    if isinstance(moved, Win32Err):
        cleanup = _cleanup(port, temp, op_id)
        if moved.error.code in {ERROR_ALREADY_EXISTS, ERROR_FILE_EXISTS}:
            return PublishAlreadyExists(
                failure(
                    ErrorCode.PROJECT_ALREADY_EXISTS,
                    ErrorCategory.CONCURRENCY,
                    "move_no_replace",
                    moved.error.detail,
                    win32_code=moved.error.code,
                ),
                cleanup,
            )
        return AtomicPublishFailed(_atomic_error(moved, "move_no_replace"), cleanup)
    actual = _read_target(port, target, max(len(data), 1))
    if isinstance(actual, _TargetReadFailed):
        return AtomicPublishFailed(actual.error, actual.diagnostics)
    if actual.data != data or not validator(actual.data):
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "post_validate",
                "published target failed byte or schema validation",
            ),
        )
    return PublishOk(target, len(data))


def publish_immutable(
    port: Win32Port,
    target: ValidatedPath,
    data: bytes,
    validator: Callable[[bytes], bool],
    cancellation: CancellationToken,
    *,
    artifact: str,
    operation_id: UUID | None = None,
) -> PublishResult:
    """Publish immutable bytes, accepting only fully validated identity."""
    result = publish_initial(
        port,
        target,
        data,
        validator,
        cancellation,
        artifact=artifact,
        operation_id=operation_id,
    )
    if not isinstance(result, PublishAlreadyExists):
        return result
    existing = _read_target(port, target, max(len(data), 1))
    if isinstance(existing, _TargetReadFailed):
        return AtomicPublishFailed(existing.error, existing.diagnostics)
    if existing.data == data and validator(existing.data):
        return PublishOk(target, len(data))
    return ImmutableConflict(
        failure(
            ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
            ErrorCategory.INTEGRITY,
            "immutable_collision",
            "existing immutable target differs",
        ),
        result.cleanup_diagnostics,
    )


def _replace_project_cas_locked(
    port: Win32Port,
    target: ValidatedPath,
    expected: ProjectDocument,
    replacement: ProjectDocument,
    cancellation: CancellationToken,
    *,
    operation_id: UUID | None = None,
) -> PublishResult:
    """Perform CAS while the caller holds an internal lease mutation guard."""
    first = _read_target(port, target, MAX_PROJECT_BYTES)
    if isinstance(first, _TargetReadFailed):
        return AtomicPublishFailed(first.error, first.diagnostics)
    try:
        observed = parse_project_bytes(first.data)
    except (ValueError, UnicodeError) as exc:
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "cas_initial_validation",
                str(exc),
                cause=exc,
            )
        )
    if observed != expected:
        if (
            observed.project_id == expected.project_id
            and observed.workspace_root_binding == expected.workspace_root_binding
        ):
            return CasConflict(
                failure(
                    ErrorCode.CAS_CONFLICT,
                    ErrorCategory.CONCURRENCY,
                    "cas",
                    "expected revision changed",
                )
            )
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "cas",
                "foreign or invalid project mutation",
            )
        )
    if (
        replacement.revision != expected.revision + 1
        or replacement.project_id != expected.project_id
        or replacement.workspace_root_binding != expected.workspace_root_binding
    ):
        raise ValueError("replacement must preserve binding and increment revision exactly once")
    data = canonical_bytes(replacement)
    op_id = operation_id or uuid4()
    temp_result = _write_temp(port, target, "project", op_id, data)
    if isinstance(temp_result, AtomicPublishFailed):
        return temp_result
    temp = temp_result
    second = _read_target(port, target, MAX_PROJECT_BYTES)
    if isinstance(second, _TargetReadFailed):
        return AtomicPublishFailed(
            second.error,
            (*second.diagnostics, *_cleanup(port, temp, op_id))[:8],
        )
    same_instance = (first.file_id_128 is None and second.file_id_128 is None) or (
        first.file_id_128 is not None
        and second.file_id_128 is not None
        and first.volume_serial == second.volume_serial
        and first.file_id_128 == second.file_id_128
    )
    if second.data != first.data or not same_instance:
        cleanup = _cleanup(port, temp, op_id)
        try:
            changed = parse_project_bytes(second.data)
        except (ValueError, UnicodeError) as exc:
            return AtomicPublishIntegrity(
                failure(
                    ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                    ErrorCategory.INTEGRITY,
                    "cas_revalidate",
                    str(exc),
                    cause=exc,
                ),
                cleanup,
            )
        if (
            changed.project_id == expected.project_id
            and changed.workspace_root_binding == expected.workspace_root_binding
        ):
            return CasConflict(
                failure(
                    ErrorCode.CAS_CONFLICT,
                    ErrorCategory.CONCURRENCY,
                    "cas_revalidate",
                    "cooperative expectation changed",
                ),
                cleanup,
            )
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "cas_revalidate",
                "foreign mutation",
            ),
            cleanup,
        )
    if cancellation.begin_irreversible_commit() is None:
        cleanup = _cleanup(port, temp, op_id)
        return PublishCancelled(
            failure(ErrorCode.CANCELLED, ErrorCategory.CANCELLED, "replace", "operation cancelled"),
            cleanup,
        )
    replaced = port.replace_file(target.long_path, temp.long_path, None)
    if isinstance(replaced, Win32Err):
        return AtomicPublishFailed(
            _atomic_error(replaced, "ReplaceFileW"), _cleanup(port, temp, op_id)
        )
    final = _read_target(port, target, MAX_PROJECT_BYTES)
    if isinstance(final, _TargetReadFailed):
        return AtomicPublishFailed(final.error, final.diagnostics)
    try:
        validated = parse_project_bytes(final.data)
    except (ValueError, UnicodeError) as exc:
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "post_replace",
                str(exc),
                cause=exc,
            ),
        )
    if final.data != data or validated != replacement:
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "post_replace",
                "replacement state is unproven",
            )
        )
    return PublishOk(target, len(data))


def replace_revision_cas(
    port: Win32Port,
    target: ValidatedPath,
    expected_data: bytes,
    replacement_data: bytes,
    validator: RevisionValidator,
    cancellation: CancellationToken,
    *,
    project_id: str,
    expected_revision: int,
    project_lock: object,
    artifact: str,
    maximum_bytes: int,
    operation_id: UUID | None = None,
) -> PublishResult:
    """Replace one canonical R-artifact under a live matching Project Lock."""
    from matrix_auto_cutter.phase2.locks import ProjectLockLease

    if target.role is not PathRole.WORKSPACE_INTERNAL:
        raise ValueError("revision CAS requires a workspace-internal target")
    if maximum_bytes <= 0:
        raise ValueError("revision CAS requires a positive bounded size")
    if len(replacement_data) > maximum_bytes:
        raise ValueError("replacement exceeds its bounded artifact size")
    if not isinstance(project_lock, ProjectLockLease):
        raise TypeError("a Project Lock lease is required")
    guard = project_lock._project_mutation_authority(project_id)
    if guard is None:
        raise ValueError("a live matching Project Lock capability is required")
    with guard:
        first = _read_target(port, target, maximum_bytes)
        if isinstance(first, _TargetReadFailed):
            return AtomicPublishFailed(first.error, first.diagnostics)
        first_binding = validator(first.data)
        if first_binding is None:
            return AtomicPublishIntegrity(
                failure(
                    ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                    ErrorCategory.INTEGRITY,
                    "cas_initial_validation",
                    "current R-artifact is invalid",
                )
            )
        if first.data != expected_data or first_binding != (project_id, expected_revision):
            return CasConflict(
                failure(
                    ErrorCode.CAS_CONFLICT,
                    ErrorCategory.CONCURRENCY,
                    "cas",
                    "expected R-artifact revision changed",
                )
            )
        replacement_binding = validator(replacement_data)
        if replacement_binding != (project_id, expected_revision + 1):
            raise ValueError("replacement must preserve project and increment revision once")
        op_id = operation_id or uuid4()
        temp_result = _write_temp(port, target, artifact, op_id, replacement_data)
        if isinstance(temp_result, AtomicPublishFailed):
            return temp_result
        temp = temp_result
        second = _read_target(port, target, maximum_bytes)
        if isinstance(second, _TargetReadFailed):
            return AtomicPublishFailed(
                second.error,
                (*second.diagnostics, *_cleanup(port, temp, op_id))[:8],
            )
        same_instance = (first.file_id_128 is None and second.file_id_128 is None) or (
            first.file_id_128 is not None
            and second.file_id_128 is not None
            and first.volume_serial == second.volume_serial
            and first.file_id_128 == second.file_id_128
        )
        if second.data != first.data or not same_instance:
            return CasConflict(
                failure(
                    ErrorCode.CAS_CONFLICT,
                    ErrorCategory.CONCURRENCY,
                    "cas_revalidate",
                    "R-artifact changed before replace",
                ),
                _cleanup(port, temp, op_id),
            )
        if cancellation.begin_irreversible_commit() is None:
            return PublishCancelled(
                failure(
                    ErrorCode.CANCELLED,
                    ErrorCategory.CANCELLED,
                    "replace",
                    "operation cancelled",
                ),
                _cleanup(port, temp, op_id),
            )
        replaced = port.replace_file(target.long_path, temp.long_path, None)
        if isinstance(replaced, Win32Err):
            return AtomicPublishFailed(
                _atomic_error(replaced, "ReplaceFileW"),
                _cleanup(port, temp, op_id),
            )
        final = _read_target(port, target, maximum_bytes)
        if isinstance(final, _TargetReadFailed):
            return AtomicPublishFailed(final.error, final.diagnostics)
        if final.data != replacement_data or validator(final.data) != replacement_binding:
            return AtomicPublishIntegrity(
                failure(
                    ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                    ErrorCategory.INTEGRITY,
                    "post_replace",
                    "replacement R-artifact state is unproven",
                )
            )
        return PublishOk(target, len(replacement_data))


def replace_project_cas(
    port: Win32Port,
    project: object,
    replacement: ProjectDocument,
    cancellation: CancellationToken,
    *,
    project_lock: object,
    operation_id: UUID | None = None,
) -> PublishResult:
    """Replace project metadata using issued project and live lock capabilities."""
    # Local imports avoid an eager atomic_project <-> workspace/locks cycle.
    from matrix_auto_cutter.phase2.locks import ProjectLockLease
    from matrix_auto_cutter.phase2.workspace import ProjectCapability

    if not isinstance(project, ProjectCapability):
        raise TypeError("an issued ProjectCapability is required")
    if not project.trusted:
        return AtomicPublishIntegrity(
            failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "project_trust",
                "project capability trust was previously revoked",
            )
        )
    if not isinstance(project_lock, ProjectLockLease):
        raise TypeError("a Project Lock lease is required")
    guard = project_lock._project_mutation_authority(project.document.project_id)
    if guard is None:
        raise ValueError("a live matching Project Lock capability is required")
    with guard:
        result = _replace_project_cas_locked(
            port,
            project.metadata_path,
            project.document,
            replacement,
            cancellation,
            operation_id=operation_id,
        )
    if isinstance(result, PublishOk | AtomicPublishFailed | AtomicPublishIntegrity | CasConflict):
        project._invalidate_trust()
    return result
