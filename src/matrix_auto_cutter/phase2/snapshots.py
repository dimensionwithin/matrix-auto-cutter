"""Immutable handle-derived file snapshots and comparisons."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from matrix_auto_cutter.phase2.artifacts import (
    AvailableIdentity,
    IdentityEvidence,
    UnavailableIdentity,
)
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail, failure
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    ValidatedPath,
    ValidatedWorkspaceRoot,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_FILE_NOT_FOUND,
    ERROR_PATH_NOT_FOUND,
    Win32Port,
)


@dataclass(frozen=True, slots=True)
class FileTime:
    """Exact Windows FILETIME evidence."""

    value: int
    unit: Literal["100ns"] = "100ns"
    epoch: Literal["1601-01-01T00:00:00Z"] = "1601-01-01T00:00:00Z"


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One successful file metadata observation."""

    path_ref: ValidatedPath
    file_type: Literal["regular_file"]
    size_bytes: int
    last_write_time: FileTime
    creation_time: FileTime | UnavailableIdentity
    change_time: FileTime | UnavailableIdentity
    attributes: int
    volume_id: IdentityEvidence
    file_id: IdentityEvidence
    evidence_version: Literal["file_snapshot/1.0"] = "file_snapshot/1.0"
    snapshot_key: str = field(init=False)

    def __post_init__(self) -> None:
        """Validate evidence and derive its canonical snapshot key."""
        expected = _validated_snapshot_key(self)
        if expected is None:
            raise ValueError("invalid file snapshot evidence")
        object.__setattr__(self, "snapshot_key", expected)


@dataclass(frozen=True, slots=True)
class SnapshotOk:
    """Successful snapshot result."""

    snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotFileMissing:
    """Safely validated path whose final file is absent."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class SnapshotNotRegular:
    """The final object is not a regular file."""

    error: ErrorDetail
    observed_type: str


@dataclass(frozen=True, slots=True)
class SnapshotAccessDenied:
    """Snapshot access was denied."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class SnapshotUnsafePath:
    """Path policy or reparse validation rejected the source."""

    error: ErrorDetail
    path_error: ErrorDetail


@dataclass(frozen=True, slots=True)
class SnapshotEvidenceInsufficient:
    """Required platform evidence could not be established."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class SnapshotOsError:
    """Unknown snapshot OS failure with preserved code."""

    error: ErrorDetail


SnapshotResult = (
    SnapshotOk
    | SnapshotFileMissing
    | SnapshotNotRegular
    | SnapshotAccessDenied
    | SnapshotUnsafePath
    | SnapshotEvidenceInsufficient
    | SnapshotOsError
)


def _identity_payload(value: IdentityEvidence) -> dict[str, str]:
    if isinstance(value, AvailableIdentity):
        return {"availability": "available", "scheme": value.scheme, "value": value.value}
    return {"availability": "not_available"}


def _snapshot_key(
    size: int,
    last: FileTime,
    creation: FileTime | UnavailableIdentity,
    change: FileTime | UnavailableIdentity,
    attributes: int,
    volume: IdentityEvidence,
    file_id: IdentityEvidence,
) -> str:
    def time_value(value: FileTime | UnavailableIdentity) -> dict[str, object]:
        if isinstance(value, FileTime):
            return {
                "availability": "available",
                "value": value.value,
                "unit": value.unit,
                "epoch": value.epoch,
            }
        return {"availability": "not_available"}

    payload = {
        "attributes": attributes,
        "change_time": time_value(change),
        "creation_time": time_value(creation),
        "evidence_version": "file_snapshot/1.0",
        "file_id": _identity_payload(file_id),
        "file_type": "regular_file",
        "last_write_time": time_value(last),
        "size_bytes": size,
        "volume_id": _identity_payload(volume),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(b"matrix-auto-cutter/file-snapshot/v1\0" + encoded).hexdigest()


def _validated_snapshot_key(snapshot: FileSnapshot) -> str | None:
    try:
        if snapshot.evidence_version != "file_snapshot/1.0":
            return None
        if snapshot.file_type != "regular_file" or snapshot.size_bytes < 0:
            return None
        if snapshot.attributes < 0:
            return None
        if snapshot.last_write_time.unit != "100ns":
            return None
        if snapshot.last_write_time.epoch != "1601-01-01T00:00:00Z":
            return None
        return _snapshot_key(
            snapshot.size_bytes,
            snapshot.last_write_time,
            snapshot.creation_time,
            snapshot.change_time,
            snapshot.attributes,
            snapshot.volume_id,
            snapshot.file_id,
        )
    except (AttributeError, TypeError, UnicodeError, ValueError):
        return None


def snapshot_file(port: Win32Port, path: ValidatedPath) -> SnapshotResult:
    """Safely reopen and snapshot a regular file."""
    workspace_root: ValidatedWorkspaceRoot | None = None
    if path.root_binding is not None:
        root_path = ValidatedPath(
            PathRole.WORKSPACE_INTERNAL,
            path.root_binding.canonical_dos_path,
            path.root_binding.canonical_dos_path,
            "\\\\?\\" + path.root_binding.canonical_dos_path,
            path.root_binding,
            ("reconstructed_root_binding",),
        )
        workspace_root = ValidatedWorkspaceRoot(root_path, path.root_binding)
    result = validate_path(
        port,
        path.canonical_dos_path,
        path.role,
        workspace_root=workspace_root,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(result, PathRejected):
        code = result.error.win32_code
        if code in {ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND}:
            return SnapshotFileMissing(
                failure(
                    ErrorCode.SNAPSHOT_NOT_FOUND,
                    ErrorCategory.INPUT,
                    "snapshot_open",
                    "file not found",
                    win32_code=code,
                )
            )
        if result.error.code is ErrorCode.PATH_ACCESS_DENIED:
            return SnapshotAccessDenied(
                failure(
                    ErrorCode.SNAPSHOT_ACCESS_DENIED,
                    ErrorCategory.ACCESS,
                    "snapshot_open",
                    result.error.message,
                    win32_code=code,
                )
            )
        if result.error.code is ErrorCode.PATH_NOT_REGULAR:
            return SnapshotNotRegular(
                failure(
                    ErrorCode.FILE_NOT_REGULAR,
                    ErrorCategory.POLICY,
                    "snapshot",
                    "not a regular file",
                ),
                "directory",
            )
        if result.error.code is ErrorCode.PATH_EVIDENCE_INSUFFICIENT:
            return SnapshotEvidenceInsufficient(
                failure(
                    ErrorCode.SNAPSHOT_EVIDENCE_INSUFFICIENT,
                    ErrorCategory.POLICY,
                    "snapshot",
                    result.error.message,
                )
            )
        if result.error.code is ErrorCode.PATH_OS_ERROR:
            return SnapshotOsError(
                failure(
                    ErrorCode.SNAPSHOT_OS_ERROR,
                    ErrorCategory.IO,
                    "snapshot",
                    result.error.message,
                    win32_code=code,
                )
            )
        return SnapshotUnsafePath(
            failure(ErrorCode.PATH_UNSAFE, ErrorCategory.POLICY, "snapshot", "unsafe path"),
            result.error,
        )
    info = result.file_info
    assert info is not None
    last = FileTime(info.last_write_time_100ns)
    creation: FileTime | UnavailableIdentity = FileTime(info.creation_time_100ns)
    change: FileTime | UnavailableIdentity = FileTime(info.change_time_100ns)
    volume: IdentityEvidence = AvailableIdentity(
        scheme="ntfs_volume_serial", value=f"{info.volume_serial:016x}"
    )
    file_id: IdentityEvidence = (
        AvailableIdentity(scheme="file_id_128", value=info.file_id_128.hex())
        if info.file_id_128 is not None
        else UnavailableIdentity()
    )
    return SnapshotOk(
        FileSnapshot(
            result.path,
            "regular_file",
            info.size_bytes,
            last,
            creation,
            change,
            info.attributes,
            volume,
            file_id,
        )
    )


@dataclass(frozen=True, slots=True)
class SameInstanceUnchanged:
    """Both snapshots prove one unchanged file instance."""

    pass


@dataclass(frozen=True, slots=True)
class SameInstanceChanged:
    """Both snapshots prove one instance with changed metadata."""

    pass


@dataclass(frozen=True, slots=True)
class DifferentInstance:
    """Snapshots prove different file instances."""

    pass


@dataclass(frozen=True, slots=True)
class NotComparable:
    """At least one snapshot lacks common instance evidence."""

    pass


@dataclass(frozen=True, slots=True)
class ComparisonFailed:
    """Comparison contract or evidence version failed."""

    reason: str


SnapshotComparison = (
    SameInstanceUnchanged
    | SameInstanceChanged
    | DifferentInstance
    | NotComparable
    | ComparisonFailed
)


def compare_snapshots(left: FileSnapshot, right: FileSnapshot) -> SnapshotComparison:
    """Compare file-instance evidence without trusting path equality."""
    left_key = _validated_snapshot_key(left)
    right_key = _validated_snapshot_key(right)
    if left_key is None or right_key is None:
        return ComparisonFailed("invalid_snapshot_evidence")
    if left.snapshot_key != left_key or right.snapshot_key != right_key:
        return ComparisonFailed("snapshot_key_mismatch")
    if not isinstance(left.volume_id, AvailableIdentity) or not isinstance(
        right.volume_id, AvailableIdentity
    ):
        return NotComparable()
    if not isinstance(left.file_id, AvailableIdentity) or not isinstance(
        right.file_id, AvailableIdentity
    ):
        return NotComparable()
    left_instance = (
        left.volume_id.scheme,
        left.volume_id.value,
        left.file_id.scheme,
        left.file_id.value,
    )
    right_instance = (
        right.volume_id.scheme,
        right.volume_id.value,
        right.file_id.scheme,
        right.file_id.value,
    )
    if left_instance != right_instance:
        return DifferentInstance()
    if left_key == right_key:
        return SameInstanceUnchanged()
    return SameInstanceChanged()
