"""Package-2C close-gate orchestration and linear lease publication."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate.classification import (
    CloseGateFailureResult,
    cancelled,
    classify_native,
    classify_phase2,
    primary_error,
    unknown,
    unsupported,
)
from matrix_auto_cutter.phase2.close_gate.contracts import (
    CloseGateBusy,
    CloseGateClosed,
    CloseGateDeletePending,
    CloseGateDiagnostic,
    CloseGateErrorCategory,
    CloseGateErrorCode,
    CloseGateFailure,
    CloseGateResult,
    CloseGateUnstable,
)
from matrix_auto_cutter.phase2.close_gate.lease import (
    _issue_close_gate_lease,
    _OwnedGateResources,
)
from matrix_auto_cutter.phase2.close_gate.ownership import (
    SourceOwnershipAcquired,
    SourceOwnershipRejected,
    acquire_source_ownership,
)
from matrix_auto_cutter.phase2.close_gate.snapshot import (
    SnapshotMeasurementFailed,
    measure_snapshot,
)
from matrix_auto_cutter.phase2.close_gate.waiting import (
    MINIMUM_STABILITY_INTERVAL_SECONDS,
    SystemWaitClock,
    WaitClockPort,
)
from matrix_auto_cutter.phase2.close_gate.win32_port import CloseGateWin32Port
from matrix_auto_cutter.phase2.locks import (
    LockAccessDenied,
    LockAcquired,
    LockBusy,
    LockCancelled,
    LockIoError,
    LockTimedOut,
    PathLockLease,
    ProjectLockLease,
    acquire_path_lock,
    acquire_project_lock,
)
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    ValidatedPath,
    validate_path,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    FileSnapshot,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    compare_snapshots,
)
from matrix_auto_cutter.phase2.win32_port import (
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ,
    GENERIC_READ,
    OPEN_EXISTING,
    Win32Err,
)


def _attach_cleanup(
    result: CloseGateFailureResult,
    diagnostics: tuple[CloseGateDiagnostic, ...],
) -> CloseGateFailureResult:
    if not diagnostics:
        return result
    error = primary_error(result)
    assert error is not None
    return replace(result, error=error.with_cleanup(diagnostics))


def _cleanup_failure(
    result: CloseGateFailureResult,
    resources: _OwnedGateResources,
) -> CloseGateFailureResult:
    return _attach_cleanup(result, resources.close())


def _lock_failure(result: object) -> CloseGateFailureResult:
    if isinstance(result, LockBusy | LockTimedOut):
        error = result.error
        return CloseGateBusy(
            CloseGateFailure(
                CloseGateErrorCode.BUSY,
                CloseGateErrorCategory.CONCURRENCY,
                error.phase,
                error.message,
                win32_code=error.win32_code,
                cause=error.cause,
                underlying=error,
                retryable=True,
            )
        )
    if isinstance(result, LockAccessDenied | LockIoError):
        return classify_phase2(result.error)
    if isinstance(result, LockCancelled):
        return cancelled(result.error.phase, underlying=result.error)
    raise TypeError("unexpected package-2A lock result")


def _measurement_failure(measured: SnapshotMeasurementFailed) -> CloseGateFailureResult:
    if measured.native_error is not None:
        return classify_native(measured.native_error, measured.phase, source_operation=True)
    if measured.path_error is not None:
        return classify_phase2(measured.path_error, measured.phase, source_operation=True)
    return unknown(measured.phase, measured.message, cause=measured.cause)


def _wait_interval(
    wait_clock: WaitClockPort,
    cancellation: CancellationToken,
    phase: str,
) -> CloseGateFailureResult | None:
    if cancellation.is_cancelled:
        return cancelled(f"before_{phase}")
    try:
        started = wait_clock.monotonic()
        signalled = wait_clock.wait(cancellation, MINIMUM_STABILITY_INTERVAL_SECONDS)
        finished = wait_clock.monotonic()
    except Exception as exc:
        return unknown(phase, "clock/wait adapter failed", cause=exc)
    if signalled or cancellation.is_cancelled:
        return cancelled(f"after_{phase}")
    elapsed = finished - started
    if not math.isfinite(started) or not math.isfinite(finished) or not math.isfinite(elapsed):
        return unknown(phase, "monotonic clock produced a non-finite value")
    if elapsed < MINIMUM_STABILITY_INTERVAL_SECONDS:
        return unknown(phase, "cancellable wait returned before the one-second minimum")
    return None


def _compare_window(
    s0: FileSnapshot,
    s1: FileSnapshot,
    s2: FileSnapshot,
) -> CloseGateFailureResult | None:
    for label, comparison in (
        ("s0_s1", compare_snapshots(s0, s1)),
        ("s1_s2", compare_snapshots(s1, s2)),
        ("s0_s2", compare_snapshots(s0, s2)),
    ):
        if isinstance(comparison, SameInstanceChanged | DifferentInstance):
            return CloseGateUnstable(
                CloseGateFailure(
                    CloseGateErrorCode.UNSTABLE,
                    CloseGateErrorCategory.INTEGRITY,
                    "stability_compare",
                    f"snapshot evidence changed during {label}",
                    underlying=comparison,
                    retryable=True,
                )
            )
        if isinstance(comparison, NotComparable):
            return unsupported(
                "stability_compare",
                f"snapshot instance evidence is insufficient for {label}",
                underlying=comparison,
            )
        if isinstance(comparison, ComparisonFailed):
            return unknown("stability_compare", comparison.reason)
        assert isinstance(comparison, SameInstanceUnchanged)
    return None


def run_close_gate(
    port: CloseGateWin32Port,
    project_id: str,
    source: ValidatedPath,
    cancellation: CancellationToken,
    *,
    wait_clock: WaitClockPort | None = None,
    lock_timeout_seconds: float = 0,
    lease_id_factory: Callable[[], UUID] = uuid4,
) -> CloseGateResult:
    """Acquire Project -> Path -> File-ID ownership and prove S0-S2 stability."""
    resources = _OwnedGateResources()
    clock = cast(WaitClockPort, wait_clock or SystemWaitClock())
    try:
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_project_lock"), resources)
        if source.role is not PathRole.EXTERNAL_SOURCE_READ_ONLY:
            return _cleanup_failure(
                unsupported("source_role", "close gate requires an external read-only path"),
                resources,
            )

        project_result = acquire_project_lock(
            port,
            project_id,
            cancellation,
            timeout_seconds=lock_timeout_seconds,
        )
        if not isinstance(project_result, LockAcquired):
            return _cleanup_failure(_lock_failure(project_result), resources)
        assert isinstance(project_result.lease, ProjectLockLease)
        resources.project_lock = project_result.lease
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_project_lock"), resources)

        path_result = acquire_path_lock(
            port,
            source,
            cancellation,
            timeout_seconds=lock_timeout_seconds,
        )
        if not isinstance(path_result, LockAcquired):
            return _cleanup_failure(_lock_failure(path_result), resources)
        assert isinstance(path_result.lease, PathLockLease)
        resources.path_lock = path_result.lease
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_path_lock"), resources)

        checked = validate_path(
            port,
            source.canonical_dos_path,
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
            require_regular_file=True,
        )
        if isinstance(checked, PathRejected):
            return _cleanup_failure(
                classify_phase2(
                    checked.error,
                    "source_path_check",
                    source_operation=True,
                ),
                resources,
            )
        source_path = checked.path
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_source_path_check"), resources)

        opened = port.open_file(
            source_path.long_path,
            GENERIC_READ,
            FILE_SHARE_READ,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
        )
        if isinstance(opened, Win32Err):
            return _cleanup_failure(
                classify_native(
                    opened.error,
                    "restrictive_source_open",
                    source_operation=True,
                ),
                resources,
            )
        resources.source_handle = opened.value
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_restrictive_source_open"), resources)

        pending = port.query_delete_pending(opened.value)
        if isinstance(pending, Win32Err):
            return _cleanup_failure(
                classify_native(
                    pending.error,
                    "delete_pending_query",
                    source_operation=True,
                ),
                resources,
            )
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_delete_pending_query"), resources)
        if pending.value:
            return _cleanup_failure(
                CloseGateDeletePending(
                    CloseGateFailure(
                        CloseGateErrorCode.DELETE_PENDING,
                        CloseGateErrorCategory.INPUT,
                        "delete_pending_query",
                        "held source handle reports delete-pending",
                        retryable=True,
                    )
                ),
                resources,
            )

        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_s0"), resources)
        measured0 = measure_snapshot(port, source_path, opened.value, "snapshot_s0")
        if isinstance(measured0, SnapshotMeasurementFailed):
            return _cleanup_failure(_measurement_failure(measured0), resources)
        s0 = measured0.snapshot
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_s0"), resources)
        if not isinstance(s0.volume_id, AvailableIdentity) or not isinstance(
            s0.file_id, AvailableIdentity
        ):
            return _cleanup_failure(
                unsupported(
                    "snapshot_s0_identity",
                    "volume/file identity is required for a close-gate lease",
                    underlying={"volume_id": s0.volume_id, "file_id": s0.file_id},
                ),
                resources,
            )
        if s0.volume_id.scheme != "ntfs_volume_serial" or s0.file_id.scheme != "file_id_128":
            return _cleanup_failure(
                unsupported(
                    "snapshot_s0_identity",
                    "unsupported volume/file identity scheme",
                    underlying={
                        "volume_scheme": s0.volume_id.scheme,
                        "file_id_scheme": s0.file_id.scheme,
                    },
                ),
                resources,
            )

        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_file_id_lock"), resources)
        source_lock = acquire_source_ownership(
            port,
            s0.volume_id.value,
            s0.file_id.value,
            cancellation,
        )
        if isinstance(source_lock, SourceOwnershipRejected):
            failed = _attach_cleanup(source_lock.result, source_lock.cleanup_diagnostics)
            return _cleanup_failure(failed, resources)
        assert isinstance(source_lock, SourceOwnershipAcquired)
        resources.source_ownership = source_lock.ownership
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_file_id_lock"), resources)

        wait_failure = _wait_interval(clock, cancellation, "first_stability_wait")
        if wait_failure is not None:
            return _cleanup_failure(wait_failure, resources)
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_s1"), resources)
        measured1 = measure_snapshot(port, source_path, opened.value, "snapshot_s1")
        if isinstance(measured1, SnapshotMeasurementFailed):
            return _cleanup_failure(_measurement_failure(measured1), resources)
        s1 = measured1.snapshot
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_s1"), resources)

        wait_failure = _wait_interval(clock, cancellation, "second_stability_wait")
        if wait_failure is not None:
            return _cleanup_failure(wait_failure, resources)
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_s2"), resources)
        measured2 = measure_snapshot(port, source_path, opened.value, "snapshot_s2")
        if isinstance(measured2, SnapshotMeasurementFailed):
            return _cleanup_failure(_measurement_failure(measured2), resources)
        s2 = measured2.snapshot
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("after_s2"), resources)

        comparison_failure = _compare_window(s0, s1, s2)
        if comparison_failure is not None:
            return _cleanup_failure(comparison_failure, resources)
        if cancellation.is_cancelled:
            return _cleanup_failure(cancelled("before_lease_commit"), resources)
        if cancellation.begin_irreversible_commit() is None:
            return _cleanup_failure(cancelled("lease_commit"), resources)

        lease = _issue_close_gate_lease(
            port,
            resources,
            source_path,
            s0,
            s1,
            s2,
            lease_id_factory=lease_id_factory,
        )
        return CloseGateClosed(lease)
    except BaseException as exc:
        diagnostics = resources.close()
        for diagnostic in diagnostics:
            exc.add_note(
                f"secondary close-gate cleanup failure: {diagnostic.phase}: {diagnostic.message}"
            )
        raise
