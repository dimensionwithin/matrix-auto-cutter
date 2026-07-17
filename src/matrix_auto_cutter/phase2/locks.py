"""Project and canonical-path ownership locks with separate diagnostics."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from matrix_auto_cutter.phase2.artifacts import (
    LockDiagnostic,
    UnavailableIdentity,
    canonical_bytes,
    is_canonical_uuid4,
)
from matrix_auto_cutter.phase2.atomic_project import PublishOk, publish_immutable
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail, failure
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    ValidatedPath,
    ValidatedWorkspaceRoot,
    ensure_directory_tree,
    path_lock_key,
    validate_path,
)
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_ACCESS_DENIED,
    ERROR_LOCK_VIOLATION,
    ERROR_SHARING_VIOLATION,
    FILE_ATTRIBUTE_NORMAL,
    GENERIC_READ,
    GENERIC_WRITE,
    OPEN_ALWAYS,
    OwnedHandle,
    Win32Err,
    Win32Port,
)


class LockKind(StrEnum):
    """Package-2A ownership lock kinds."""

    PROJECT = "project"
    PATH = "path"
    TARGET = "target"


class LockLease:
    """Non-constructible base for issuer-bound exclusive ownership capabilities."""

    __slots__ = ("_acquisition_token", "_handle", "_key", "_ownership_path", "_state_lock")
    _acquisition_token: object
    _handle: OwnedHandle
    _key: str
    _ownership_path: ValidatedPath
    _state_lock: Lock

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Reject public construction; successful acquisition is the only issuer."""
        del args, kwargs
        raise TypeError("lock leases are issued only by successful acquisition")

    def __setattr__(self, name: str, value: object) -> None:
        """Reject post-issuance mutation of acquisition bindings."""
        del name, value
        raise AttributeError("lock lease acquisition bindings are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject removal of acquisition bindings."""
        del name
        raise AttributeError("lock lease acquisition bindings are immutable")

    def _initialize(
        self,
        key: str,
        ownership_path: ValidatedPath,
        handle: OwnedHandle,
        acquisition_token: object,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _LOCK_ISSUER_SEAL:
            raise TypeError("lock leases are issued only by successful acquisition")
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_ownership_path", ownership_path)
        object.__setattr__(self, "_handle", handle)
        object.__setattr__(self, "_acquisition_token", acquisition_token)
        object.__setattr__(self, "_state_lock", Lock())

    @property
    def key(self) -> str:
        """Return immutable redacted acquisition key metadata."""
        return self._key

    @property
    def ownership_path(self) -> ValidatedPath:
        """Return immutable ownership-object metadata."""
        return self._ownership_path

    @property
    def kind(self) -> LockKind:
        """Return the runtime capability kind, not caller-controlled metadata."""
        if isinstance(self, ProjectLockLease):
            return LockKind.PROJECT
        return LockKind.TARGET if isinstance(self, TargetLockLease) else LockKind.PATH

    @property
    def held(self) -> bool:
        """Return whether the ownership handle remains open."""
        with self._state_lock:
            return _LOCK_AUTHORITY.is_live(self)

    def release(self) -> ErrorDetail | None:
        """Release exactly once and preserve a native close failure."""
        with self._state_lock:
            if not _LOCK_AUTHORITY.revoke(self):
                raise RuntimeError("lock lease is already released")
            result = self._handle.close()
        if isinstance(result, Win32Err):
            return failure(
                ErrorCode.LOCK_IO,
                ErrorCategory.IO,
                result.error.operation,
                result.error.detail,
                win32_code=result.error.code,
            )
        return None

    def __enter__(self) -> LockLease:
        """Enter a held-lock context."""
        if not self.held:
            raise RuntimeError("lock lease is already released")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Release the lock on context exit."""
        self.release()

    def __copy__(self) -> LockLease:
        """Reject copies that would alias one acquisition authority."""
        raise TypeError("lock lease capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> LockLease:
        """Reject deep copies that would imitate one acquisition authority."""
        del memo
        raise TypeError("lock lease capabilities cannot be copied")


class ProjectLockLease(LockLease):
    """Authentic project mutation capability issued by project-lock acquisition."""

    __slots__ = ()

    def _project_mutation_authority(self, project_id: str) -> _LeaseMutationGuard | None:
        return _LOCK_AUTHORITY.begin_project_mutation(self, project_id)


class PathLockLease(LockLease):
    """Path serialization capability that can never authorize project mutation."""

    __slots__ = ()


class TargetLockLease(LockLease):
    """Target serialization capability in the reserved target namespace."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class LockAcquired:
    """Successful ownership result; diagnostics are secondary."""

    lease: LockLease
    diagnostic_errors: tuple[ErrorDetail, ...]


@dataclass(frozen=True, slots=True)
class LockBusy:
    """A proven sharing or lock violation on the ownership object."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class LockAccessDenied:
    """Ownership open was denied and is not classified as busy."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class LockIoError:
    """Unknown or non-sharing lock I/O failure."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class LockTimedOut:
    """Explicit bounded wait elapsed."""

    error: ErrorDetail


@dataclass(frozen=True, slots=True)
class LockCancelled:
    """Cancellation interrupted acquisition or bounded waiting."""

    error: ErrorDetail
    cleanup_diagnostics: tuple[ErrorDetail, ...] = ()


LockResult = LockAcquired | LockBusy | LockAccessDenied | LockIoError | LockTimedOut | LockCancelled
type DiagnosticStatus = Literal["attempting", "acquired", "busy", "failed", "released"]

_LOCK_ISSUER_SEAL = object()


class _LeaseMutationGuard:
    """Internal guard holding a lease's state lock across one mutation."""

    __slots__ = ("_lock", "_released")

    def __init__(self, lock: Lock) -> None:
        self._lock = lock
        self._released = False

    def __enter__(self) -> _LeaseMutationGuard:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._released:
            raise RuntimeError("mutation authority released twice")
        self._released = True
        self._lock.release()


@dataclass(frozen=True, slots=True)
class _AcquisitionRecord:
    lease: LockLease
    kind: LockKind
    key: str
    ownership_path: ValidatedPath
    handle: OwnedHandle
    token: object


class _LockAuthorityRegistry:
    """Issuer registry binding one live lease object to one acquired handle."""

    __slots__ = ("_lock", "_records")

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[object, _AcquisitionRecord] = {}

    def issue(
        self,
        kind: LockKind,
        key: str,
        ownership_path: ValidatedPath,
        handle: OwnedHandle,
    ) -> LockLease:
        token = object()
        lease_type = (
            ProjectLockLease
            if kind is LockKind.PROJECT
            else TargetLockLease
            if kind is LockKind.TARGET
            else PathLockLease
        )
        lease = object.__new__(lease_type)
        lease._initialize(key, ownership_path, handle, token, _seal=_LOCK_ISSUER_SEAL)
        record = _AcquisitionRecord(lease, kind, key, ownership_path, handle, token)
        with self._lock:
            self._records[token] = record
        return lease

    @staticmethod
    def _identity_matches(lease: LockLease, record: _AcquisitionRecord | None) -> bool:
        return (
            record is not None
            and record.lease is lease
            and record.handle is lease._handle
            and record.token is lease._acquisition_token
        )

    def _matches(self, lease: LockLease, record: _AcquisitionRecord | None) -> bool:
        expected_kind = (
            LockKind.PROJECT
            if isinstance(lease, ProjectLockLease)
            else LockKind.TARGET
            if isinstance(lease, TargetLockLease)
            else LockKind.PATH
        )
        return (
            self._identity_matches(lease, record)
            and record is not None
            and record.kind is expected_kind
            and record.key == lease._key
            and record.ownership_path is lease._ownership_path
            and not record.handle.closed
        )

    def is_live(self, lease: LockLease) -> bool:
        with self._lock:
            record = self._records.get(lease._acquisition_token)
            return self._matches(lease, record)

    def revoke(self, lease: LockLease) -> bool:
        with self._lock:
            record = self._records.get(lease._acquisition_token)
            if not self._identity_matches(lease, record):
                return False
            del self._records[lease._acquisition_token]
            return True

    def begin_project_mutation(
        self, lease: ProjectLockLease, project_id: str
    ) -> _LeaseMutationGuard | None:
        try:
            state_lock = lease._state_lock
            token = lease._acquisition_token
        except AttributeError:
            return None
        state_lock.acquire()
        with self._lock:
            record = self._records.get(token)
            valid = (
                self._matches(lease, record)
                and record is not None
                and record.kind is LockKind.PROJECT
                and record.key == project_id
            )
        if not valid:
            state_lock.release()
            return None
        return _LeaseMutationGuard(state_lock)


_LOCK_AUTHORITY = _LockAuthorityRegistry()


@dataclass(frozen=True, slots=True)
class _LockRoots:
    ownership: ValidatedWorkspaceRoot
    diagnostics: ValidatedWorkspaceRoot | None
    diagnostic_errors: tuple[ErrorDetail, ...]


def _filetime_now() -> int:
    return time.time_ns() // 100 + 116_444_736_000_000_000


def _lock_error(kind: LockKind, code: int, operation: str, detail: str) -> LockResult:
    if code in {ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}:
        stable = (
            ErrorCode.PROJECT_LOCK_BUSY if kind is LockKind.PROJECT else ErrorCode.PATH_LOCK_BUSY
        )
        return LockBusy(
            failure(
                stable,
                ErrorCategory.CONCURRENCY,
                operation,
                detail,
                win32_code=code,
                retryable=True,
            )
        )
    if code == ERROR_ACCESS_DENIED:
        return LockAccessDenied(
            failure(
                ErrorCode.LOCK_ACCESS_DENIED,
                ErrorCategory.ACCESS,
                operation,
                detail,
                win32_code=code,
            )
        )
    return LockIoError(
        failure(ErrorCode.LOCK_IO, ErrorCategory.IO, operation, detail, win32_code=code)
    )


def _diagnose(
    port: Win32Port,
    diagnostic_root: ValidatedWorkspaceRoot,
    key: str,
    run_id: UUID,
    kind: LockKind,
    project_id: str | None,
    status: DiagnosticStatus,
    process: tuple[int, int],
) -> tuple[ErrorDetail, ...]:
    key_dir = diagnostic_root.path.canonical_dos_path.rstrip("\\") + "\\" + key
    ensured = ensure_directory_tree(port, key_dir)
    if isinstance(ensured, PathRejected):
        return (ensured.error,)
    target_result = validate_path(
        port,
        key_dir + "\\" + str(run_id) + ".json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=diagnostic_root,
    )
    if isinstance(target_result, PathRejected):
        return (target_result.error,)
    diagnostic = LockDiagnostic(
        run_id=str(run_id),
        project_id=project_id if project_id is not None else UnavailableIdentity(),
        process_id=process[0],
        process_start_time_100ns=process[1],
        lock_kind=kind.value,
        redacted_key=key,
        attempted_at_100ns=_filetime_now(),
        status=status,
    )
    token = CancellationToken()
    result = publish_immutable(
        port,
        target_result.path,
        canonical_bytes(diagnostic),
        lambda data: data == canonical_bytes(diagnostic),
        token,
        artifact="lock-diagnostic",
    )
    if isinstance(result, PublishOk):
        return ()
    error = result.error
    return (error,)


def _roots(
    port: Win32Port, kind: LockKind, key: str
) -> _LockRoots | LockIoError | LockAccessDenied:
    local = port.local_app_data()
    if isinstance(local, Win32Err):
        if local.error.code == ERROR_ACCESS_DENIED:
            return LockAccessDenied(
                failure(
                    ErrorCode.LOCK_ACCESS_DENIED,
                    ErrorCategory.ACCESS,
                    local.error.operation,
                    local.error.detail,
                    win32_code=local.error.code,
                )
            )
        return LockIoError(
            failure(
                ErrorCode.LOCK_IO,
                ErrorCategory.IO,
                local.error.operation,
                local.error.detail,
                win32_code=local.error.code,
            )
        )
    base = local.value.rstrip("\\") + "\\DimensionWithin\\MatrixAutoCutter\\locks"
    plural = "projects"
    if kind is LockKind.TARGET:
        plural = "targets"
    elif kind is LockKind.PATH:
        plural = "paths"
    ownership = ensure_directory_tree(port, base + "\\ownership\\" + plural)
    if isinstance(ownership, PathRejected):
        if ownership.error.code is ErrorCode.PATH_ACCESS_DENIED:
            return LockAccessDenied(
                failure(
                    ErrorCode.LOCK_ACCESS_DENIED,
                    ErrorCategory.ACCESS,
                    ownership.error.phase,
                    ownership.error.message,
                    win32_code=ownership.error.win32_code,
                )
            )
        return LockIoError(ownership.error)
    diagnostics = ensure_directory_tree(port, base + "\\diagnostics")
    if isinstance(diagnostics, PathRejected):
        return _LockRoots(ownership, None, (diagnostics.error,))
    return _LockRoots(ownership, diagnostics, ())


def _acquire(
    port: Win32Port,
    kind: LockKind,
    key: str,
    cancellation: CancellationToken,
    *,
    project_id: str | None,
    timeout_seconds: float,
    run_id: UUID | None,
) -> LockResult:
    if timeout_seconds < 0:
        raise ValueError("lock timeout cannot be negative")
    roots = _roots(port, kind, key)
    if isinstance(roots, LockIoError | LockAccessDenied):
        return roots
    ownership_root = roots.ownership
    suffix = ".lck"
    ownership_result = validate_path(
        port,
        ownership_root.path.canonical_dos_path.rstrip("\\") + "\\" + key + suffix,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=ownership_root,
    )
    if isinstance(ownership_result, PathRejected):
        return LockIoError(ownership_result.error)
    operation_id = run_id or uuid4()
    deadline = time.monotonic() + timeout_seconds
    while True:
        if cancellation.is_cancelled:
            return LockCancelled(
                failure(
                    ErrorCode.CANCELLED, ErrorCategory.CANCELLED, "lock_wait", "operation cancelled"
                )
            )
        opened = port.open_file(
            ownership_result.path.long_path,
            GENERIC_READ | GENERIC_WRITE,
            0,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
        )
        if not isinstance(opened, Win32Err):
            permit = cancellation.begin_irreversible_commit()
            if permit is None:
                closed = opened.value.close()
                cleanup: tuple[ErrorDetail, ...] = ()
                if isinstance(closed, Win32Err):
                    cleanup = (
                        failure(
                            ErrorCode.LOCK_IO,
                            ErrorCategory.IO,
                            closed.error.operation,
                            closed.error.detail,
                            win32_code=closed.error.code,
                        ),
                    )
                return LockCancelled(
                    failure(
                        ErrorCode.CANCELLED,
                        ErrorCategory.CANCELLED,
                        "lock_open_commit",
                        "operation cancelled while ownership open was pending",
                    ),
                    cleanup,
                )
            lease = _LOCK_AUTHORITY.issue(kind, key, ownership_result.path, opened.value)
            diagnostics = roots.diagnostic_errors
            process_result = port.process_identity()
            if isinstance(process_result, Win32Err):
                diagnostics += (
                    failure(
                        ErrorCode.LOCK_IO,
                        ErrorCategory.IO,
                        process_result.error.operation,
                        process_result.error.detail,
                        win32_code=process_result.error.code,
                    ),
                )
            elif roots.diagnostics is not None:
                diagnostics += _diagnose(
                    port,
                    roots.diagnostics,
                    key,
                    operation_id,
                    kind,
                    project_id,
                    "acquired",
                    process_result.value,
                )
            return LockAcquired(lease, diagnostics[:8])
        classified = _lock_error(
            kind, opened.error.code, opened.error.operation, opened.error.detail
        )
        if not isinstance(classified, LockBusy) or timeout_seconds == 0:
            return classified
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return LockTimedOut(
                failure(
                    ErrorCode.LOCK_TIMEOUT,
                    ErrorCategory.CONCURRENCY,
                    "lock_wait",
                    "bounded lock wait timed out",
                    retryable=True,
                )
            )
        cancellation.wait(min(remaining, 0.05))


def acquire_project_lock(
    port: Win32Port,
    project_id: str,
    cancellation: CancellationToken,
    *,
    timeout_seconds: float = 0,
    run_id: UUID | None = None,
) -> LockResult:
    """Acquire a project ownership handle."""
    if not is_canonical_uuid4(project_id):
        return LockIoError(
            failure(
                ErrorCode.PROJECT_ID_INVALID,
                ErrorCategory.INPUT,
                "project_lock",
                "invalid project UUID",
            )
        )
    return _acquire(
        port,
        LockKind.PROJECT,
        project_id,
        cancellation,
        project_id=project_id,
        timeout_seconds=timeout_seconds,
        run_id=run_id,
    )


def acquire_path_lock(
    port: Win32Port,
    path: ValidatedPath,
    cancellation: CancellationToken,
    *,
    timeout_seconds: float = 0,
    run_id: UUID | None = None,
) -> LockResult:
    """Acquire a redacted canonical-path ownership handle."""
    key = path_lock_key(port, path)
    if isinstance(key, PathRejected):
        return LockIoError(key.error)
    return _acquire(
        port,
        LockKind.PATH,
        key,
        cancellation,
        project_id=None,
        timeout_seconds=timeout_seconds,
        run_id=run_id,
    )


def acquire_target_lock(
    port: Win32Port,
    target: ValidatedPath,
    cancellation: CancellationToken,
    *,
    timeout_seconds: float = 0,
    run_id: UUID | None = None,
) -> LockResult:
    """Acquire a target ownership handle in the reserved target namespace."""
    if target.role is not PathRole.EXTERNAL_TARGET_CREATE_ONLY:
        return LockIoError(
            failure(
                ErrorCode.PATH_EVIDENCE_INSUFFICIENT,
                ErrorCategory.POLICY,
                "target_lock",
                "a create-only external target capability is required",
            )
        )
    key = path_lock_key(port, target)
    if isinstance(key, PathRejected):
        return LockIoError(key.error)
    return _acquire(
        port,
        LockKind.TARGET,
        key,
        cancellation,
        project_id=None,
        timeout_seconds=timeout_seconds,
        run_id=run_id,
    )
