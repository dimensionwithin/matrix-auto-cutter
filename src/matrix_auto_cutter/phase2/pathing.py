"""Windows path policy and handle-based validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from matrix_auto_cutter.phase2.artifacts import (
    AvailableIdentity,
    UnavailableIdentity,
    WorkspaceRootBinding,
)
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail, failure
from matrix_auto_cutter.phase2.win32_port import (
    DELETE,
    DRIVE_FIXED,
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS,
    FILE_ATTRIBUTE_RECALL_ON_OPEN,
    FILE_FLAG_BACKUP_SEMANTICS,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_DELETE,
    FILE_SHARE_READ,
    FILE_SHARE_WRITE,
    FILE_TYPE_DISK,
    GENERIC_READ,
    OPEN_EXISTING,
    OwnedHandle,
    RawFileInfo,
    Win32Err,
    Win32Port,
)

_DOS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_CONTROL_OR_WILDCARD = re.compile(r"[\x00-\x1f*?]")
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?$", re.IGNORECASE)
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\", "\\\\?\\VOLUME{", "GLOBALROOT")


class PathRole(StrEnum):
    """Closed package-2A path capability roles."""

    WORKSPACE_INTERNAL = "workspace_internal"
    EXTERNAL_SOURCE_READ_ONLY = "external_source_read_only"
    EXTERNAL_TARGET_CREATE_ONLY = "external_target_create_only"


@dataclass(frozen=True, slots=True)
class ValidatedPath:
    """Immutable lexical and handle-validated path capability."""

    role: PathRole
    original_input: str | tuple[str, ...]
    canonical_dos_path: str
    long_path: str
    root_binding: WorkspaceRootBinding | None
    policy_checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidatedWorkspaceRoot:
    """Validated workspace root and its binding evidence."""

    path: ValidatedPath
    binding: WorkspaceRootBinding


@dataclass(frozen=True, slots=True)
class PathValidated:
    """Successful discriminated path result."""

    path: ValidatedPath
    file_info: RawFileInfo | None


@dataclass(frozen=True, slots=True)
class PathRejected:
    """Rejected discriminated path result."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


PathResult = PathValidated | PathRejected


@dataclass(frozen=True, slots=True)
class SecureRead:
    """Bytes and evidence read through the same validated open handle."""

    data: bytes
    file_info: RawFileInfo


@dataclass(frozen=True, slots=True)
class SecureReadFailed:
    """A secure read failed without producing trusted bytes."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


SecureReadResult = SecureRead | SecureReadFailed


@dataclass(frozen=True, slots=True)
class SecureDeleteFailed:
    """Handle-bound deletion failed or its delete-on-close completion is unproven."""

    error: ErrorDetail
    diagnostics: tuple[ErrorDetail, ...] = ()


SecureDeleteResult = SecureDeleteFailed | None


def _reject(code: ErrorCode, message: str, phase: str = "lexical_path") -> PathRejected:
    return PathRejected(failure(code, ErrorCategory.POLICY, phase, message))


def _dos_from_final(value: str) -> str:
    return value[4:] if value.startswith("\\\\?\\") else value


def to_long_path(canonical_dos_path: str) -> str:
    """Derive a Win32 long path only after DOS-path validation."""
    return "\\\\?\\" + canonical_dos_path


def _split_absolute(value: str) -> tuple[str, tuple[str, ...]] | PathRejected:
    upper = value.upper()
    if any(upper.startswith(prefix.upper()) for prefix in _DEVICE_PREFIXES):
        return _reject(ErrorCode.PATH_DEVICE_NAMESPACE, "device namespaces are forbidden")
    if value.startswith("\\\\"):
        return _reject(ErrorCode.PATH_UNC, "UNC paths are forbidden")
    if not _DOS_ABSOLUTE.match(value):
        return _reject(ErrorCode.PATH_INPUT_FORM, "a fully qualified local DOS path is required")
    normalized = value.replace("/", "\\")
    return normalized[0].upper() + ":\\", tuple(normalized[3:].split("\\"))


def _validate_components(components: tuple[str, ...]) -> PathRejected | None:
    if not components:
        return _reject(ErrorCode.PATH_COMPONENT_EMPTY, "empty component sequence")
    for component in components:
        if component == "":
            return _reject(ErrorCode.PATH_COMPONENT_EMPTY, "empty path component")
        if component in {".", ".."}:
            return _reject(ErrorCode.PATH_DOT_COMPONENT, "dot components are forbidden")
        if "\\" in component or "/" in component:
            return _reject(ErrorCode.PATH_INPUT_FORM, "embedded separators are forbidden")
        if _CONTROL_OR_WILDCARD.search(component):
            return _reject(
                ErrorCode.PATH_INPUT_FORM, "control characters and wildcards are forbidden"
            )
        if ":" in component:
            return _reject(ErrorCode.PATH_ADS, "colons and alternate data streams are forbidden")
        if component.endswith((".", " ")):
            return _reject(
                ErrorCode.PATH_TRAILING_DOT_SPACE, "trailing dots and spaces are forbidden"
            )
        if _RESERVED.match(component):
            return _reject(ErrorCode.PATH_RESERVED_NAME, "reserved Windows device name")
        try:
            roundtrip = component.encode("utf-16-le").decode("utf-16-le")
        except UnicodeError:
            return _reject(ErrorCode.PATH_UNICODE_ROUNDTRIP, "Unicode roundtrip failed")
        assert roundtrip == component
    return None


def _key(port: Win32Port, value: str) -> str | PathRejected:
    result = port.ordinal_case_key(value)
    if isinstance(result, Win32Err):
        return PathRejected(
            failure(
                ErrorCode.PATH_OS_ERROR,
                ErrorCategory.IO,
                result.error.operation,
                result.error.detail,
                win32_code=result.error.code,
            )
        )
    return result.value


def _within(port: Win32Port, child: str, root: str) -> bool | PathRejected:
    child_key = _key(port, child.rstrip("\\"))
    if isinstance(child_key, PathRejected):
        return child_key
    root_key = _key(port, root.rstrip("\\"))
    if isinstance(root_key, PathRejected):
        return root_key
    return child_key == root_key or child_key.startswith(root_key + "\\")


def validate_path(
    port: Win32Port,
    value: str | tuple[str, ...],
    role: PathRole,
    *,
    workspace_root: ValidatedWorkspaceRoot | None = None,
    require_existing: bool = False,
    require_regular_file: bool = False,
) -> PathResult:
    """Validate caller input and optionally bind every existing component by handle."""
    if role is PathRole.WORKSPACE_INTERNAL and workspace_root is None:
        return _reject(ErrorCode.PATH_EVIDENCE_INSUFFICIENT, "workspace root binding required")
    if isinstance(value, tuple):
        if role is not PathRole.WORKSPACE_INTERNAL:
            return _reject(ErrorCode.PATH_INPUT_FORM, "external paths cannot be relative")
        components = value
        rejected = _validate_components(components)
        if rejected is not None:
            return rejected
        assert workspace_root is not None
        canonical = (
            workspace_root.path.canonical_dos_path.rstrip("\\") + "\\" + "\\".join(components)
        )
        original: str | tuple[str, ...] = value
    else:
        split = _split_absolute(value)
        if isinstance(split, PathRejected):
            return split
        drive, components = split
        rejected = _validate_components(components)
        if rejected is not None:
            return rejected
        canonical = drive + "\\".join(components)
        original = value
        if role is PathRole.WORKSPACE_INTERNAL:
            assert workspace_root is not None
            containment = _within(port, canonical, workspace_root.path.canonical_dos_path)
            if isinstance(containment, PathRejected):
                return containment
            if not containment:
                return _reject(ErrorCode.PATH_ROOT_ESCAPE, "path escapes workspace root")
    path = ValidatedPath(
        role,
        original,
        canonical,
        to_long_path(canonical),
        workspace_root.binding
        if workspace_root is not None and role is PathRole.WORKSPACE_INTERNAL
        else None,
        ("input_form", "components", "reserved_names", "unicode_roundtrip", "lexical_containment"),
    )
    if not require_existing:
        return PathValidated(path, None)
    return _validate_handles(port, path, workspace_root, require_regular_file)


def derive_external_target(
    port: Win32Port,
    source: ValidatedPath,
    target_name: str,
) -> PathResult:
    """Derive one create-only sibling target from a validated external source."""
    if source.role is not PathRole.EXTERNAL_SOURCE_READ_ONLY:
        return _reject(
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
            "external targets require a validated read-only source capability",
        )
    rejected = _validate_components((target_name,))
    if rejected is not None:
        return rejected
    parent, separator, _ = source.canonical_dos_path.rpartition("\\")
    if not separator:
        return _reject(ErrorCode.PATH_INPUT_FORM, "source has no safe parent directory")
    parent_check = validate_path(
        port,
        parent,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
    )
    if isinstance(parent_check, PathRejected):
        return parent_check
    if parent_check.file_info is None or not parent_check.file_info.is_directory:
        return _reject(ErrorCode.PATH_NOT_REGULAR, "source parent is not a directory")
    return validate_path(
        port,
        parent + "\\" + target_name,
        PathRole.EXTERNAL_TARGET_CREATE_ONLY,
    )


def _ancestors(canonical: str) -> tuple[str, ...]:
    drive = canonical[:3]
    parts = canonical[3:].split("\\")
    current = drive.rstrip("\\")
    result: list[str] = [drive]
    for part in parts:
        if not part:
            continue
        current += "\\" + part
        result.append(current)
    return tuple(result)


def _classify_os(error_code: int, operation: str, detail: str) -> PathRejected:
    if error_code == 5:
        return PathRejected(
            failure(
                ErrorCode.PATH_ACCESS_DENIED,
                ErrorCategory.ACCESS,
                operation,
                detail,
                win32_code=error_code,
            )
        )
    return PathRejected(
        failure(
            ErrorCode.PATH_OS_ERROR,
            ErrorCategory.IO,
            operation,
            detail,
            win32_code=error_code,
        )
    )


def _validate_raw(info: RawFileInfo, *, final: bool, regular: bool) -> PathRejected | None:
    if info.is_reparse:
        return _reject(ErrorCode.PATH_REPARSE, "reparse point rejected", "handle_validation")
    cloud = (
        FILE_ATTRIBUTE_OFFLINE
        | FILE_ATTRIBUTE_RECALL_ON_OPEN
        | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    )
    if info.attributes & cloud:
        return _reject(
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
            "cloud/offline object rejected",
            "handle_validation",
        )
    if info.filesystem_name != "NTFS" or info.drive_type != DRIVE_FIXED:
        return _reject(
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT, "local fixed NTFS required", "handle_validation"
        )
    if final and regular and (info.is_directory or info.file_type != FILE_TYPE_DISK):
        return _reject(ErrorCode.PATH_NOT_REGULAR, "regular file required", "handle_validation")
    if not final and not info.is_directory:
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH, "ancestor is not a directory", "handle_validation"
        )
    return None


def _validate_opened_info(
    port: Win32Port,
    path: ValidatedPath,
    workspace_root: ValidatedWorkspaceRoot | None,
    info: RawFileInfo,
    *,
    regular: bool,
) -> PathRejected | None:
    """Bind one already-open final handle to its expected path and root."""
    rejected = _validate_raw(info, final=True, regular=regular)
    if rejected is not None:
        return rejected
    expected_key = _key(port, path.canonical_dos_path)
    if isinstance(expected_key, PathRejected):
        return expected_key
    actual = _dos_from_final(info.final_dos_path)
    actual_key = _key(port, actual)
    if isinstance(actual_key, PathRejected):
        return actual_key
    if expected_key != actual_key:
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH, "final handle path mismatch", "handle_validation"
        )
    if workspace_root is None:
        return None
    containment = _within(port, actual, workspace_root.path.canonical_dos_path)
    if isinstance(containment, PathRejected):
        return containment
    if not containment:
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH,
            "final handle escaped workspace root",
            "handle_validation",
        )
    volume = workspace_root.binding.volume_identity
    if isinstance(volume, UnavailableIdentity):
        return _reject(
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
            "workspace volume identity unavailable",
            "handle_validation",
        )
    if info.volume_serial != int(volume.value, 16):
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH,
            "workspace volume binding mismatch",
            "handle_validation",
        )
    root_key = _key(port, workspace_root.path.canonical_dos_path)
    if isinstance(root_key, PathRejected):
        return root_key
    if actual_key == root_key:
        root_id = workspace_root.binding.root_file_id
        if (
            isinstance(root_id, AvailableIdentity)
            and info.file_id_128 is not None
            and info.file_id_128.hex() != root_id.value
        ):
            return _reject(
                ErrorCode.PATH_ROOT_MISMATCH,
                "workspace root file ID mismatch",
                "handle_validation",
            )
    return None


def _validate_handles(
    port: Win32Port,
    path: ValidatedPath,
    workspace_root: ValidatedWorkspaceRoot | None,
    require_regular_file: bool,
) -> PathResult:
    final_info: RawFileInfo | None = None
    ancestors = _ancestors(path.canonical_dos_path)
    for index, ancestor in enumerate(ancestors):
        final = index == len(ancestors) - 1
        opened = port.open_file(
            to_long_path(ancestor),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
        )
        if isinstance(opened, Win32Err):
            return _classify_os(opened.error.code, opened.error.operation, opened.error.detail)
        handle = opened.value
        inspected = _inspect_validation_handle(
            port,
            path,
            workspace_root,
            handle,
            ancestor,
            final=final,
            require_regular_file=require_regular_file,
        )
        closed = handle.close()
        if isinstance(closed, Win32Err):
            close_error = _validation_close_error(closed, final=final)
            if isinstance(inspected, PathRejected):
                return PathRejected(
                    inspected.error,
                    (*inspected.diagnostics, close_error)[:8],
                )
            return PathRejected(close_error)
        if isinstance(inspected, PathRejected):
            return inspected
        if final:
            final_info = inspected
    return PathValidated(path, final_info)


def _validation_close_error(closed: Win32Err, *, final: bool) -> ErrorDetail:
    context = "target" if final else "ancestor"
    return failure(
        ErrorCode.PATH_OS_ERROR,
        ErrorCategory.IO,
        f"close_validation_{context}",
        f"{context} validation handle release was not proven: {closed.error.detail[:512]}",
        win32_code=closed.error.code,
        cause=OSError(closed.error.code, closed.error.detail),
    )


def _inspect_validation_handle(
    port: Win32Port,
    path: ValidatedPath,
    workspace_root: ValidatedWorkspaceRoot | None,
    handle: OwnedHandle,
    ancestor: str,
    *,
    final: bool,
    require_regular_file: bool,
) -> RawFileInfo | PathRejected:
    """Inspect one held validation handle without taking ownership of its close."""
    queried = port.query_file_info(handle)
    if isinstance(queried, Win32Err):
        return _classify_os(queried.error.code, queried.error.operation, queried.error.detail)
    info = queried.value
    if final:
        rejected = _validate_opened_info(
            port,
            path,
            workspace_root,
            info,
            regular=require_regular_file,
        )
        return rejected or info
    rejected = _validate_raw(info, final=False, regular=require_regular_file)
    if rejected is not None:
        return rejected
    expected_key = _key(port, ancestor)
    if isinstance(expected_key, PathRejected):
        return expected_key
    actual_key = _key(port, _dos_from_final(info.final_dos_path))
    if isinstance(actual_key, PathRejected):
        return actual_key
    if expected_key != actual_key:
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH,
            "final handle path mismatch",
            "handle_validation",
        )
    if workspace_root is None:
        return info
    containment = _within(
        port,
        _dos_from_final(info.final_dos_path),
        workspace_root.path.canonical_dos_path,
    )
    if isinstance(containment, PathRejected):
        return containment
    reverse_containment = _within(
        port,
        workspace_root.path.canonical_dos_path,
        _dos_from_final(info.final_dos_path),
    )
    if isinstance(reverse_containment, PathRejected):
        return reverse_containment
    volume = workspace_root.binding.volume_identity
    if isinstance(volume, UnavailableIdentity):
        return _reject(
            ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
            "workspace volume identity unavailable",
            "handle_validation",
        )
    if (not containment and not reverse_containment) or info.volume_serial != int(volume.value, 16):
        return _reject(
            ErrorCode.PATH_ROOT_MISMATCH,
            "workspace handle binding mismatch",
            "handle_validation",
        )
    actual = _dos_from_final(info.final_dos_path)
    actual_key_for_root = _key(port, actual)
    root_key = _key(port, workspace_root.path.canonical_dos_path)
    if isinstance(actual_key_for_root, PathRejected):
        return actual_key_for_root
    if isinstance(root_key, PathRejected):
        return root_key
    if actual_key_for_root == root_key:
        root_id = workspace_root.binding.root_file_id
        if (
            isinstance(root_id, AvailableIdentity)
            and info.file_id_128 is not None
            and info.file_id_128.hex() != root_id.value
        ):
            return _reject(
                ErrorCode.PATH_ROOT_MISMATCH,
                "workspace root file ID mismatch",
                "handle_validation",
            )
    return info


def secure_read_file(port: Win32Port, path: ValidatedPath, maximum_bytes: int) -> SecureReadResult:
    """Validate ancestors, then validate and read bytes through one held handle."""
    if maximum_bytes < 0:
        raise ValueError("maximum_bytes must be non-negative")
    root = _root_for_path(path)
    checked = validate_path(
        port,
        path.canonical_dos_path,
        path.role,
        workspace_root=root,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(checked, PathRejected):
        return SecureReadFailed(checked.error, checked.diagnostics)
    opened = port.open_file(
        path.long_path,
        GENERIC_READ,
        FILE_SHARE_READ,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
    )
    if isinstance(opened, Win32Err):
        return SecureReadFailed(
            _classify_os(opened.error.code, opened.error.operation, opened.error.detail).error
        )
    handle = opened.value
    result = _read_validated_handle(port, path, root, handle, maximum_bytes)
    closed = handle.close()
    if isinstance(closed, Win32Err):
        close_error = failure(
            ErrorCode.PATH_OS_ERROR,
            ErrorCategory.IO,
            "close_after_secure_read",
            f"secure read handle release was not proven: {closed.error.detail[:512]}",
            win32_code=closed.error.code,
        )
        if isinstance(result, SecureReadFailed):
            return SecureReadFailed(result.error, (*result.diagnostics, close_error)[:8])
        return SecureReadFailed(close_error)
    return result


def _read_validated_handle(
    port: Win32Port,
    path: ValidatedPath,
    root: ValidatedWorkspaceRoot | None,
    handle: OwnedHandle,
    maximum_bytes: int,
) -> SecureReadResult:
    queried = port.query_file_info(handle)
    if isinstance(queried, Win32Err):
        return SecureReadFailed(
            _classify_os(queried.error.code, queried.error.operation, queried.error.detail).error
        )
    info = queried.value
    rejected = _validate_opened_info(port, path, root, info, regular=True)
    if rejected is not None:
        return SecureReadFailed(rejected.error)
    if info.size_bytes < 0 or info.size_bytes > maximum_bytes:
        return SecureReadFailed(
            failure(
                ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
                ErrorCategory.INTEGRITY,
                "secure_read_size",
                "file exceeds the bounded read limit",
            )
        )
    remaining = info.size_bytes + 1
    chunks: list[bytes] = []
    while remaining > 0:
        read = port.read_file(handle, remaining)
        if isinstance(read, Win32Err):
            return SecureReadFailed(
                _classify_os(read.error.code, read.error.operation, read.error.detail).error
            )
        if not read.value:
            break
        chunks.append(read.value)
        remaining -= len(read.value)
    data = b"".join(chunks)
    if len(data) != info.size_bytes:
        return SecureReadFailed(
            failure(
                ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
                ErrorCategory.INTEGRITY,
                "secure_read_size",
                "file changed while its validated handle was held",
            )
        )
    return SecureRead(data, info)


def secure_delete_file(port: Win32Port, path: ValidatedPath) -> SecureDeleteResult:
    """Delete the exact reparse-safe object validated through the held delete handle."""
    root = _root_for_path(path)
    checked = validate_path(
        port,
        path.canonical_dos_path,
        path.role,
        workspace_root=root,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(checked, PathRejected):
        return SecureDeleteFailed(checked.error, checked.diagnostics)
    opened = port.open_file(
        path.long_path,
        DELETE | GENERIC_READ,
        0,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
    )
    if isinstance(opened, Win32Err):
        return SecureDeleteFailed(
            _classify_os(opened.error.code, opened.error.operation, opened.error.detail).error
        )
    handle = opened.value
    primary: ErrorDetail | None = None
    disposition_set = False
    queried = port.query_file_info(handle)
    if isinstance(queried, Win32Err):
        primary = _classify_os(
            queried.error.code, queried.error.operation, queried.error.detail
        ).error
    else:
        rejected = _validate_opened_info(port, path, root, queried.value, regular=True)
        if rejected is not None:
            primary = rejected.error
        else:
            deleted = port.delete_file_handle(handle)
            if isinstance(deleted, Win32Err):
                primary = _classify_os(
                    deleted.error.code, deleted.error.operation, deleted.error.detail
                ).error
            else:
                disposition_set = True
    closed = handle.close()
    if isinstance(closed, Win32Err):
        phase = "close_after_delete_disposition" if disposition_set else "close_after_secure_delete"
        close_error = failure(
            ErrorCode.PATH_OS_ERROR,
            ErrorCategory.IO,
            phase,
            (
                "delete disposition was set but delete-on-close completion is unproven"
                if disposition_set
                else "secure delete handle release was not proven"
            )
            + f": {closed.error.detail[:512]}",
            win32_code=closed.error.code,
        )
        if primary is not None:
            return SecureDeleteFailed(primary, (close_error,))
        return SecureDeleteFailed(close_error)
    if primary is not None:
        return SecureDeleteFailed(primary)
    return None


def _root_for_path(path: ValidatedPath) -> ValidatedWorkspaceRoot | None:
    binding = path.root_binding
    if binding is None:
        return None
    root_path = ValidatedPath(
        PathRole.WORKSPACE_INTERNAL,
        binding.canonical_dos_path,
        binding.canonical_dos_path,
        to_long_path(binding.canonical_dos_path),
        binding,
        ("root_binding",),
    )
    return ValidatedWorkspaceRoot(root_path, binding)


def validate_workspace_root(port: Win32Port, root: str) -> ValidatedWorkspaceRoot | PathRejected:
    """Validate an existing root and create its immutable binding."""
    lexical = validate_path(port, root, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True)
    if isinstance(lexical, PathRejected):
        return lexical
    info = lexical.file_info
    if info is None or not info.is_directory:
        return _reject(ErrorCode.WORKSPACE_INVALID, "workspace root is not a directory")
    binding = WorkspaceRootBinding(
        canonical_dos_path=lexical.path.canonical_dos_path,
        volume_identity=AvailableIdentity(
            scheme="ntfs_volume_serial", value=f"{info.volume_serial:016x}"
        ),
        root_file_id=(
            AvailableIdentity(scheme="file_id_128", value=info.file_id_128.hex())
            if info.file_id_128 is not None
            else UnavailableIdentity()
        ),
    )
    internal = ValidatedPath(
        PathRole.WORKSPACE_INTERNAL,
        root,
        lexical.path.canonical_dos_path,
        lexical.path.long_path,
        binding,
        (*lexical.path.policy_checks, "root_binding"),
    )
    return ValidatedWorkspaceRoot(internal, binding)


def path_lock_key(port: Win32Port, path: ValidatedPath) -> str | PathRejected:
    """Build a domain-separated case-insensitive path key."""
    import hashlib

    key = _key(port, path.canonical_dos_path)
    if isinstance(key, PathRejected):
        return key
    payload = b"matrix-auto-cutter/path-lock/v1\0" + key.encode("utf-16-le")
    return hashlib.sha256(payload).hexdigest()


def reject_case_collisions(port: Win32Port, names: tuple[str, ...]) -> PathRejected | None:
    """Reject names that collide under the documented Windows case mapping."""
    keys: set[str] = set()
    for name in names:
        rejected = _validate_components((name,))
        if rejected is not None:
            return rejected
        key = _key(port, name)
        if isinstance(key, PathRejected):
            return key
        if key in keys:
            return _reject(ErrorCode.PATH_CASE_COLLISION, "case-insensitive artifact collision")
        keys.add(key)
    return None


def ensure_directory_tree(
    port: Win32Port, absolute_dos_path: str
) -> ValidatedWorkspaceRoot | PathRejected:
    """Create missing components one at a time and revalidate every component."""
    lexical = validate_path(port, absolute_dos_path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    if isinstance(lexical, PathRejected):
        return lexical
    for ancestor in _ancestors(lexical.path.canonical_dos_path):
        if len(ancestor) > 3:
            created = port.create_directory(to_long_path(ancestor))
            if isinstance(created, Win32Err) and created.error.code != 183:
                return _classify_os(
                    created.error.code, created.error.operation, created.error.detail
                )
        else:
            drive_check = _validate_handles(
                port,
                ValidatedPath(
                    PathRole.EXTERNAL_SOURCE_READ_ONLY,
                    ancestor,
                    ancestor,
                    to_long_path(ancestor),
                    None,
                    ("drive_root",),
                ),
                None,
                False,
            )
            if isinstance(drive_check, PathRejected):
                return drive_check
            continue
        checked = validate_path(
            port,
            ancestor,
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
        )
        if isinstance(checked, PathRejected):
            return checked
        if checked.file_info is None or not checked.file_info.is_directory:
            return _reject(ErrorCode.WORKSPACE_INVALID, "workspace component is not a directory")
    return validate_workspace_root(port, lexical.path.canonical_dos_path)
