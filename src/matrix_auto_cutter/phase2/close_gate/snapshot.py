"""Same-handle package-2C snapshot measurement and comparison."""

from __future__ import annotations

from dataclasses import dataclass

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, UnavailableIdentity
from matrix_auto_cutter.phase2.close_gate.win32_port import CloseGateWin32Port
from matrix_auto_cutter.phase2.errors import ErrorDetail
from matrix_auto_cutter.phase2.pathing import PathRejected, ValidatedPath, _validate_opened_info
from matrix_auto_cutter.phase2.snapshots import FileSnapshot, FileTime
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Err, Win32Failure


@dataclass(frozen=True, slots=True)
class SnapshotMeasured:
    """One successful measurement over the already-held source handle."""

    snapshot: FileSnapshot


@dataclass(frozen=True, slots=True)
class SnapshotMeasurementFailed:
    """Native, path, or evidence failure from a same-handle measurement."""

    phase: str
    message: str
    native_error: Win32Failure | None = None
    path_error: ErrorDetail | None = None
    cause: BaseException | None = None


SnapshotMeasurement = SnapshotMeasured | SnapshotMeasurementFailed


def _optional_time(value: object) -> FileTime | UnavailableIdentity:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return FileTime(value)
    return UnavailableIdentity()


def measure_snapshot(
    port: CloseGateWin32Port,
    path: ValidatedPath,
    handle: OwnedHandle,
    phase: str,
) -> SnapshotMeasurement:
    """Measure one snapshot without reopening or seeking the source."""
    queried = port.query_file_info(handle)
    if isinstance(queried, Win32Err):
        return SnapshotMeasurementFailed(
            phase,
            queried.error.detail,
            native_error=queried.error,
        )
    info = queried.value
    rejected = _validate_opened_info(port, path, None, info, regular=True)
    if isinstance(rejected, PathRejected):
        return SnapshotMeasurementFailed(
            phase,
            rejected.error.message,
            path_error=rejected.error,
        )
    if (
        not isinstance(info.size_bytes, int)
        or isinstance(info.size_bytes, bool)
        or info.size_bytes < 0
        or not isinstance(info.last_write_time_100ns, int)
        or isinstance(info.last_write_time_100ns, bool)
        or info.last_write_time_100ns < 0
        or not isinstance(info.attributes, int)
        or isinstance(info.attributes, bool)
        or info.attributes < 0
    ):
        return SnapshotMeasurementFailed(phase, "required snapshot evidence is invalid")
    volume = (
        AvailableIdentity(
            scheme="ntfs_volume_serial",
            value=f"{info.volume_serial:016x}",
        )
        if isinstance(info.volume_serial, int)
        and not isinstance(info.volume_serial, bool)
        and info.volume_serial >= 0
        else UnavailableIdentity()
    )
    file_id = (
        AvailableIdentity(scheme="file_id_128", value=info.file_id_128.hex())
        if isinstance(info.file_id_128, bytes) and len(info.file_id_128) == 16
        else UnavailableIdentity()
    )
    try:
        snapshot = FileSnapshot(
            path,
            "regular_file",
            info.size_bytes,
            FileTime(info.last_write_time_100ns),
            _optional_time(info.creation_time_100ns),
            _optional_time(info.change_time_100ns),
            info.attributes,
            volume,
            file_id,
        )
    except (TypeError, ValueError) as exc:
        return SnapshotMeasurementFailed(
            phase,
            "snapshot construction failed",
            cause=exc,
        )
    return SnapshotMeasured(snapshot)
