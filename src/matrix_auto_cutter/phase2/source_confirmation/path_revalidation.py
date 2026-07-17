"""Reparse-safe path-to-lease instance revalidation before probe and commit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from matrix_auto_cutter.phase2.close_gate import CloseGateLease
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    SnapshotFileMissing,
    SnapshotOk,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.source_confirmation.contracts import (
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    PathRevalidationEvidence,
    SnapshotEvidence,
)
from matrix_auto_cutter.phase2.win32_port import Win32Port


@dataclass(frozen=True, slots=True)
class PathRevalidated:
    """The original bound path still names the held lease instance."""

    evidence: PathRevalidationEvidence
    snapshot: object


@dataclass(frozen=True, slots=True)
class PathInstanceChanged:
    """Current path evidence proves a different or changed source."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class PathDisappeared:
    """The original bound path no longer resolves to a file."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class PathRevalidationFailed:
    """Operational or insufficient evidence prevented path confirmation."""

    error: ConfirmationFailure


type PathRevalidationResult = (
    PathRevalidated | PathInstanceChanged | PathDisappeared | PathRevalidationFailed
)


def revalidate_lease_path(
    port: Win32Port,
    lease: CloseGateLease,
    phase: Literal["before_probe", "before_identity_commit"],
) -> PathRevalidationResult:
    """Resolve the original path and compare handle-derived identity and metadata to S0."""
    measured = snapshot_file(port, lease.source_path)
    if isinstance(measured, SnapshotFileMissing):
        return PathDisappeared(
            ConfirmationFailure(
                ConfirmationErrorCode.SOURCE_CHANGED,
                ConfirmationErrorCategory.INPUT,
                f"path.{phase}",
                measured.error.message,
                win32_code=measured.error.win32_code,
                underlying=measured.error,
                retryable=True,
            )
        )
    if not isinstance(measured, SnapshotOk):
        error = measured.error
        return PathRevalidationFailed(
            ConfirmationFailure(
                ConfirmationErrorCode.IO,
                ConfirmationErrorCategory.IO,
                f"path.{phase}",
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
            )
        )
    snapshot = measured.snapshot
    comparison = compare_snapshots(lease.s0, snapshot)
    if isinstance(comparison, SameInstanceChanged | DifferentInstance):
        return PathInstanceChanged(
            ConfirmationFailure(
                ConfirmationErrorCode.SOURCE_CHANGED,
                ConfirmationErrorCategory.INTEGRITY,
                f"path.{phase}",
                "bound source path no longer proves the unchanged lease instance",
                underlying=comparison,
                retryable=True,
            )
        )
    if isinstance(comparison, NotComparable | ComparisonFailed):
        return PathRevalidationFailed(
            ConfirmationFailure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                f"path.{phase}",
                "path-to-lease identity evidence is insufficient or invalid",
                underlying=comparison,
            )
        )
    assert isinstance(comparison, SameInstanceUnchanged)
    if snapshot.path_ref.canonical_dos_path != lease.source_path.canonical_dos_path:
        return PathRevalidationFailed(
            ConfirmationFailure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                f"path.{phase}",
                "validated path reference changed during revalidation",
            )
        )
    evidence = PathRevalidationEvidence(
        phase=phase,
        source_path=lease.source_path.canonical_dos_path,
        lease_volume_id=lease.volume_id,
        lease_file_id=lease.file_id,
        snapshot=SnapshotEvidence.from_snapshot(snapshot),
    )
    return PathRevalidated(evidence, snapshot)
