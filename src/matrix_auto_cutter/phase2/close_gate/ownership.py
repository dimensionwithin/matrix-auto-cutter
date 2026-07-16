"""File-ID ownership acquired only after package-2C has measured S0."""

from __future__ import annotations

from dataclasses import dataclass

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate.classification import (
    CloseGateFailureResult,
    cancelled,
    classify_native,
    classify_phase2,
)
from matrix_auto_cutter.phase2.close_gate.contracts import CloseGateDiagnostic
from matrix_auto_cutter.phase2.close_gate.win32_port import CloseGateWin32Port
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    ValidatedPath,
    ensure_directory_tree,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import (
    FILE_ATTRIBUTE_NORMAL,
    GENERIC_READ,
    GENERIC_WRITE,
    OPEN_ALWAYS,
    OwnedHandle,
    Win32Err,
)


@dataclass(frozen=True, slots=True)
class SourceOwnership:
    """Internal exact source-ownership object and its live share-zero handle."""

    key: str
    path: ValidatedPath
    handle: OwnedHandle


@dataclass(frozen=True, slots=True)
class SourceOwnershipAcquired:
    """Successful File-ID ownership acquisition."""

    ownership: SourceOwnership


@dataclass(frozen=True, slots=True)
class SourceOwnershipRejected:
    """Structured acquisition failure."""

    result: CloseGateFailureResult
    cleanup_diagnostics: tuple[CloseGateDiagnostic, ...] = ()


SourceOwnershipResult = SourceOwnershipAcquired | SourceOwnershipRejected


def source_ownership_key(volume_id: str, file_id: str) -> str:
    """Build the one canonical object name shared by every hardlink alias."""
    if len(volume_id) != 16 or len(file_id) != 32:
        raise ValueError("invalid NTFS volume/file identity")
    try:
        int(volume_id, 16)
        int(file_id, 16)
    except ValueError as exc:
        raise ValueError("invalid NTFS volume/file identity") from exc
    return f"{volume_id}-{file_id}"


def acquire_source_ownership(
    port: CloseGateWin32Port,
    volume_id: str,
    file_id: str,
    cancellation: CancellationToken,
) -> SourceOwnershipResult:
    """Acquire one fail-fast source lock after identity evidence exists."""
    if cancellation.is_cancelled:
        return SourceOwnershipRejected(cancelled("before_file_id_lock"))
    try:
        key = source_ownership_key(volume_id, file_id)
    except ValueError as exc:
        from matrix_auto_cutter.phase2.close_gate.classification import unsupported

        return SourceOwnershipRejected(unsupported("file_id_lock_key", str(exc), underlying=exc))
    local = port.local_app_data()
    if isinstance(local, Win32Err):
        return SourceOwnershipRejected(classify_native(local.error, "source_lock_root"))
    if cancellation.is_cancelled:
        return SourceOwnershipRejected(cancelled("after_source_lock_root_resolution"))
    root = ensure_directory_tree(
        port,
        local.value.rstrip("\\") + "\\DimensionWithin\\MatrixAutoCutter\\locks\\ownership\\sources",
    )
    if isinstance(root, PathRejected):
        return SourceOwnershipRejected(classify_phase2(root.error, "source_lock_root"))
    if cancellation.is_cancelled:
        return SourceOwnershipRejected(cancelled("after_source_lock_root_validation"))
    target = validate_path(
        port,
        root.path.canonical_dos_path.rstrip("\\") + "\\" + key + ".lck",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    if isinstance(target, PathRejected):
        return SourceOwnershipRejected(classify_phase2(target.error, "source_lock_path"))
    if cancellation.is_cancelled:
        return SourceOwnershipRejected(cancelled("before_file_id_lock_open"))
    opened = port.open_file(
        target.path.long_path,
        GENERIC_READ | GENERIC_WRITE,
        0,
        OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL,
    )
    if isinstance(opened, Win32Err):
        return SourceOwnershipRejected(
            classify_native(
                opened.error,
                "source_lock_open",
                ownership_operation=True,
            )
        )
    if cancellation.is_cancelled:
        closed = opened.value.close()
        diagnostics: tuple[CloseGateDiagnostic, ...] = ()
        if isinstance(closed, Win32Err):
            diagnostics = (
                CloseGateDiagnostic(
                    "source_lock_cancel_cleanup",
                    closed.error.detail,
                    win32_code=closed.error.code,
                ),
            )
        return SourceOwnershipRejected(cancelled("after_file_id_lock_open"), diagnostics)
    return SourceOwnershipAcquired(SourceOwnership(key, target.path, opened.value))
