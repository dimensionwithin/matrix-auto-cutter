"""Issuer-bound CloseGateLease ownership, recheck, and synchronized cleanup."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Condition, Lock
from typing import Final
from uuid import UUID, uuid4

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate.classification import (
    cancelled,
    classify_native,
    classify_phase2,
    unknown,
    unsupported,
)
from matrix_auto_cutter.phase2.close_gate.contracts import (
    CloseGateDeletePending,
    CloseGateDiagnostic,
    CloseGateErrorCategory,
    CloseGateErrorCode,
    CloseGateFailure,
    CloseGateUnknownWin32Error,
    CloseGateUnsupported,
    RecheckCancelled,
    RecheckClosed,
    RecheckDeletePending,
    RecheckOk,
    RecheckResult,
    RecheckUnknownWin32Error,
    RecheckUnstable,
    RecheckUnsupported,
)
from matrix_auto_cutter.phase2.close_gate.ownership import SourceOwnership
from matrix_auto_cutter.phase2.close_gate.snapshot import (
    SnapshotMeasurementFailed,
    measure_snapshot,
)
from matrix_auto_cutter.phase2.close_gate.win32_port import CloseGateWin32Port
from matrix_auto_cutter.phase2.locks import PathLockLease, ProjectLockLease
from matrix_auto_cutter.phase2.pathing import ValidatedPath
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    FileSnapshot,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    compare_snapshots,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Err, Win32Result

_LEASE_ISSUER_SEAL: Final = object()


@dataclass(slots=True)
class _OwnedGateResources:
    """All handles transferred into a successful lease, in acquisition order."""

    project_lock: ProjectLockLease | None = None
    path_lock: PathLockLease | None = None
    source_handle: OwnedHandle | None = None
    source_ownership: SourceOwnership | None = None

    @staticmethod
    def _handle_diagnostic(phase: str, result: Win32Err) -> CloseGateDiagnostic:
        return CloseGateDiagnostic(
            phase,
            result.error.detail,
            win32_code=result.error.code,
            cause=OSError(result.error.code, result.error.detail),
        )

    def close(self) -> tuple[CloseGateDiagnostic, ...]:
        """Close File-ID, source, path, then project ownership exactly once."""
        diagnostics: list[CloseGateDiagnostic] = []
        ownership = self.source_ownership
        self.source_ownership = None
        if ownership is not None and not ownership.handle.closed:
            try:
                closed = ownership.handle.close()
                if isinstance(closed, Win32Err):
                    diagnostics.append(self._handle_diagnostic("close_source_lock", closed))
            except BaseException as exc:
                diagnostics.append(CloseGateDiagnostic("close_source_lock", str(exc), cause=exc))
        source = self.source_handle
        self.source_handle = None
        if source is not None and not source.closed:
            try:
                closed = source.close()
                if isinstance(closed, Win32Err):
                    diagnostics.append(self._handle_diagnostic("close_source_handle", closed))
            except BaseException as exc:
                diagnostics.append(CloseGateDiagnostic("close_source_handle", str(exc), cause=exc))
        path_lock = self.path_lock
        self.path_lock = None
        if path_lock is not None and path_lock.held:
            try:
                error = path_lock.release()
                if error is not None:
                    diagnostics.append(
                        CloseGateDiagnostic(
                            "release_path_lock",
                            error.message,
                            win32_code=error.win32_code,
                            cause=error.cause,
                        )
                    )
            except BaseException as exc:
                diagnostics.append(CloseGateDiagnostic("release_path_lock", str(exc), cause=exc))
        project_lock = self.project_lock
        self.project_lock = None
        if project_lock is not None and project_lock.held:
            try:
                error = project_lock.release()
                if error is not None:
                    diagnostics.append(
                        CloseGateDiagnostic(
                            "release_project_lock",
                            error.message,
                            win32_code=error.win32_code,
                            cause=error.cause,
                        )
                    )
            except BaseException as exc:
                diagnostics.append(CloseGateDiagnostic("release_project_lock", str(exc), cause=exc))
        return tuple(diagnostics[:8])


class CloseGateLease:
    """Authentic runtime capability proving only the completed S0-S2 close gate."""

    __slots__ = (
        "_close_result",
        "_file_id",
        "_file_id_scheme",
        "_lease_id",
        "_source_path",
        "_token",
        "_volume_id",
        "s0",
        "s1",
        "s2",
    )
    _close_result: tuple[CloseGateDiagnostic, ...] | None
    _file_id: str
    _file_id_scheme: str
    _lease_id: UUID
    _source_path: ValidatedPath
    _token: object
    _volume_id: str
    s0: FileSnapshot
    s1: FileSnapshot
    s2: FileSnapshot

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Reject public construction; only a committed gate may issue a lease."""
        del args, kwargs
        raise TypeError("close-gate leases are issued only by a committed gate")

    def __setattr__(self, name: str, value: object) -> None:
        """Reject post-issuance mutation of security bindings."""
        del name, value
        raise AttributeError("close-gate lease bindings are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject removal of security bindings."""
        del name
        raise AttributeError("close-gate lease bindings are immutable")

    def _initialize(
        self,
        token: object,
        source_path: ValidatedPath,
        volume_id: str,
        file_id: str,
        file_id_scheme: str,
        s0: FileSnapshot,
        s1: FileSnapshot,
        s2: FileSnapshot,
        lease_id: UUID,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _LEASE_ISSUER_SEAL:
            raise TypeError("close-gate leases are issued only by a committed gate")
        object.__setattr__(self, "_token", token)
        object.__setattr__(self, "_source_path", source_path)
        object.__setattr__(self, "_volume_id", volume_id)
        object.__setattr__(self, "_file_id", file_id)
        object.__setattr__(self, "_file_id_scheme", file_id_scheme)
        object.__setattr__(self, "s0", s0)
        object.__setattr__(self, "s1", s1)
        object.__setattr__(self, "s2", s2)
        object.__setattr__(self, "_lease_id", lease_id)
        object.__setattr__(self, "_close_result", None)

    @property
    def source_path(self) -> ValidatedPath:
        """Return the validated external source-path binding."""
        return self._source_path

    @property
    def volume_id(self) -> str:
        """Return the proven NTFS volume identity."""
        return self._volume_id

    @property
    def file_id(self) -> str:
        """Return the proven file identity."""
        return self._file_id

    @property
    def file_id_scheme(self) -> str:
        """Return the identity scheme bound by S0-S2."""
        return self._file_id_scheme

    @property
    def lease_id(self) -> UUID:
        """Return the validation-epoch/lease identifier."""
        return self._lease_id

    @property
    def validation_epoch(self) -> UUID:
        """Return the same identifier under its validation-epoch meaning."""
        return self._lease_id

    @property
    def closed(self) -> bool:
        """Return whether controlled close has linearized."""
        return not _LEASE_AUTHORITY.is_open(self)

    def recheck(self, cancellation: CancellationToken | None = None) -> RecheckResult:
        """Measure one unnamed snapshot over the same held source handle."""
        return _LEASE_AUTHORITY.recheck(self, cancellation or CancellationToken())

    def close(self) -> tuple[CloseGateDiagnostic, ...]:
        """Close idempotently after all active rechecks have stopped."""
        return _LEASE_AUTHORITY.close(self)

    def __enter__(self) -> CloseGateLease:
        """Enter an authentic open lease context."""
        if self.closed:
            raise RuntimeError("close-gate lease is closed")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close through the controlled idempotent path."""
        self.close()

    def __copy__(self) -> CloseGateLease:
        """Reject capability aliasing by copy."""
        raise TypeError("close-gate lease capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> CloseGateLease:
        """Reject capability aliasing by deep copy."""
        del memo
        raise TypeError("close-gate lease capabilities cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        """Reject serialization as an authority path."""
        raise TypeError("close-gate lease capabilities cannot be serialized")


@dataclass(slots=True)
class _LeaseRecord:
    lease: CloseGateLease
    token: object
    port: CloseGateWin32Port
    resources: _OwnedGateResources
    condition: Condition = field(default_factory=lambda: Condition(Lock()))
    state: str = "open"
    active_rechecks: int = 0
    io_active: bool = False
    close_diagnostics: tuple[CloseGateDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class _LeaseIoUnavailable:
    """Internal fail-closed reason for an unavailable lease I/O session."""

    reason: str


class _LeaseIoSession:
    """Short-lived exclusive I/O facade that never exposes the raw source handle."""

    __slots__ = ("_active", "_lease", "_record")

    def __init__(self, lease: CloseGateLease, record: _LeaseRecord) -> None:
        self._lease = lease
        self._record = record
        self._active = True

    def _require_active(self) -> OwnedHandle:
        if not self._active:
            raise RuntimeError("lease I/O session is no longer active")
        source = self._record.resources.source_handle
        if source is None or source.closed:
            raise RuntimeError("lease source handle is unavailable")
        return source

    def position(self, offset: int) -> Win32Result[int]:
        """Position the same held handle without exposing it to package 2D."""
        return self._record.port.set_file_offset(self._require_active(), offset)

    def read(self, maximum_bytes: int) -> Win32Result[bytes]:
        """Read a caller-bounded block from the same held handle."""
        return self._record.port.read_file(self._require_active(), maximum_bytes)

    def recheck(self, cancellation: CancellationToken) -> RecheckResult:
        """Use the existing authenticated lease recheck while this session is active."""
        self._require_active()
        return _LEASE_AUTHORITY.recheck(self._lease, cancellation)

    def commit(self, cancellation: CancellationToken) -> bool:
        """Linearize successful I/O publication against close and cancellation."""
        self._require_active()
        with self._record.condition:
            if self._record.state != "open" or not self._record.io_active:
                return False
            return cancellation.begin_irreversible_commit() is not None

    def _deactivate(self) -> None:
        self._active = False


def _closed_failure() -> CloseGateFailure:
    return CloseGateFailure(
        CloseGateErrorCode.LEASE_CLOSED,
        CloseGateErrorCategory.INTEGRITY,
        "lease_recheck",
        "close linearized before recheck publication",
    )


def _recheck_failure(result: object) -> RecheckResult:
    error = getattr(result, "error", None)
    assert isinstance(error, CloseGateFailure)
    if isinstance(result, CloseGateDeletePending):
        return RecheckDeletePending(error)
    if isinstance(result, CloseGateUnsupported):
        return RecheckUnsupported(error)
    if isinstance(result, CloseGateUnknownWin32Error):
        return RecheckUnknownWin32Error(error)
    from matrix_auto_cutter.phase2.close_gate.contracts import CloseGateCancelled

    if isinstance(result, CloseGateCancelled):
        return RecheckCancelled(error)
    return RecheckUnknownWin32Error(error)


class _LeaseAuthorityRegistry:
    """Issuer registry binding one lease object to all live handles and rechecks."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: dict[object, _LeaseRecord] = {}

    def issue(
        self,
        port: CloseGateWin32Port,
        resources: _OwnedGateResources,
        source_path: ValidatedPath,
        s0: FileSnapshot,
        s1: FileSnapshot,
        s2: FileSnapshot,
        *,
        lease_id_factory: Callable[[], UUID] = uuid4,
    ) -> CloseGateLease:
        """Issue exactly one lease for already-committed resources."""
        if not isinstance(s0.volume_id, AvailableIdentity) or not isinstance(
            s0.file_id, AvailableIdentity
        ):
            raise ValueError("lease issuance requires volume/file identity")
        lease_id = lease_id_factory()
        if not isinstance(lease_id, UUID) or lease_id.version != 4:
            raise ValueError("lease ID must be UUIDv4")
        token = object()
        lease = object.__new__(CloseGateLease)
        lease._initialize(
            token,
            source_path,
            s0.volume_id.value,
            s0.file_id.value,
            s0.file_id.scheme,
            s0,
            s1,
            s2,
            lease_id,
            _seal=_LEASE_ISSUER_SEAL,
        )
        record = _LeaseRecord(lease, token, port, resources)
        with self._lock:
            self._records[token] = record
        return lease

    def _record(self, lease: CloseGateLease) -> _LeaseRecord | None:
        try:
            token = lease._token
        except AttributeError:
            return None
        with self._lock:
            record = self._records.get(token)
        if record is None or record.lease is not lease or record.token is not token:
            return None
        return record

    def is_open(self, lease: CloseGateLease) -> bool:
        record = self._record(lease)
        if record is None:
            return False
        with record.condition:
            return record.state == "open"

    def run_io[IoResultT](
        self,
        lease: CloseGateLease,
        cancellation: CancellationToken,
        operation: Callable[[_LeaseIoSession], IoResultT],
    ) -> IoResultT | _LeaseIoUnavailable:
        """Run one exclusive same-handle I/O operation under lease authority."""
        record = self._record(lease)
        if record is None:
            return _LeaseIoUnavailable("lease_not_authorized")
        with record.condition:
            if record.state != "open":
                return _LeaseIoUnavailable("lease_closed")
            if cancellation.is_cancelled:
                return _LeaseIoUnavailable("cancelled")
            source = record.resources.source_handle
            if source is None or source.closed:
                return _LeaseIoUnavailable("source_handle_unavailable")
            if record.io_active:
                return _LeaseIoUnavailable("lease_io_already_active")
            record.io_active = True
        session = _LeaseIoSession(lease, record)
        try:
            return operation(session)
        finally:
            session._deactivate()
            with record.condition:
                record.io_active = False
                record.condition.notify_all()

    def recheck(self, lease: CloseGateLease, cancellation: CancellationToken) -> RecheckResult:
        record = self._record(lease)
        if record is None:
            return RecheckClosed(_closed_failure())
        with record.condition:
            if record.state != "open":
                return RecheckClosed(_closed_failure())
            record.active_rechecks += 1
        try:
            if cancellation.is_cancelled:
                return RecheckCancelled(cancelled("before_recheck").error)
            source = record.resources.source_handle
            if source is None or source.closed:
                return RecheckClosed(_closed_failure())
            pending = record.port.query_delete_pending(source)
            if isinstance(pending, Win32Err):
                return _recheck_failure(
                    classify_native(
                        pending.error,
                        "recheck_delete_pending",
                        source_operation=True,
                    )
                )
            if cancellation.is_cancelled:
                return RecheckCancelled(cancelled("after_recheck_delete_pending").error)
            if pending.value:
                return RecheckDeletePending(
                    CloseGateFailure(
                        CloseGateErrorCode.DELETE_PENDING,
                        CloseGateErrorCategory.INPUT,
                        "recheck_delete_pending",
                        "held source handle reports delete-pending",
                        retryable=True,
                    )
                )
            measured = measure_snapshot(
                record.port,
                lease.source_path,
                source,
                "lease_recheck_snapshot",
            )
            if isinstance(measured, SnapshotMeasurementFailed):
                if measured.native_error is not None:
                    return _recheck_failure(
                        classify_native(
                            measured.native_error,
                            measured.phase,
                            source_operation=True,
                        )
                    )
                if measured.path_error is not None:
                    return _recheck_failure(
                        classify_phase2(
                            measured.path_error,
                            measured.phase,
                            source_operation=True,
                        )
                    )
                return RecheckUnknownWin32Error(
                    unknown(measured.phase, measured.message, cause=measured.cause).error
                )
            if cancellation.is_cancelled:
                return RecheckCancelled(cancelled("after_recheck_snapshot").error)
            comparison = compare_snapshots(lease.s2, measured.snapshot)
            if isinstance(comparison, SameInstanceChanged | DifferentInstance):
                return RecheckUnstable(
                    CloseGateFailure(
                        CloseGateErrorCode.UNSTABLE,
                        CloseGateErrorCategory.INTEGRITY,
                        "lease_recheck_compare",
                        "lease-bound source evidence changed",
                    ),
                    measured.snapshot,
                )
            if isinstance(comparison, NotComparable):
                return RecheckUnsupported(
                    unsupported(
                        "lease_recheck_compare",
                        "source instance evidence became insufficient",
                    ).error
                )
            if isinstance(comparison, ComparisonFailed):
                return RecheckUnknownWin32Error(
                    unknown("lease_recheck_compare", comparison.reason).error
                )
            assert isinstance(comparison, SameInstanceUnchanged)
            with record.condition:
                if record.state != "open":
                    return RecheckClosed(_closed_failure())
                if cancellation.begin_irreversible_commit() is None:
                    return RecheckCancelled(cancelled("recheck_commit").error)
                return RecheckOk(measured.snapshot)
        finally:
            with record.condition:
                record.active_rechecks -= 1
                record.condition.notify_all()

    def close(self, lease: CloseGateLease) -> tuple[CloseGateDiagnostic, ...]:
        record = self._record(lease)
        if record is None:
            result = getattr(lease, "_close_result", None)
            if isinstance(result, tuple):
                return result
            raise TypeError("lease was not issued by this close-gate authority")
        first_closer = False
        with record.condition:
            if record.state == "open":
                record.state = "closing"
                first_closer = True
            while not first_closer and record.state == "closing":
                record.condition.wait()
            if not first_closer:
                return record.close_diagnostics
            while record.active_rechecks or record.io_active:
                record.condition.wait()
        diagnostics = record.resources.close()
        with record.condition:
            record.close_diagnostics = diagnostics
            record.state = "closed"
            object.__setattr__(lease, "_close_result", diagnostics)
            record.condition.notify_all()
        with self._lock:
            self._records.pop(record.token, None)
        return diagnostics


_LEASE_AUTHORITY = _LeaseAuthorityRegistry()


def _issue_close_gate_lease(
    port: CloseGateWin32Port,
    resources: _OwnedGateResources,
    source_path: ValidatedPath,
    s0: FileSnapshot,
    s1: FileSnapshot,
    s2: FileSnapshot,
    *,
    lease_id_factory: Callable[[], UUID] = uuid4,
) -> CloseGateLease:
    """Issue a lease only after the cancellation commit boundary."""
    return _LEASE_AUTHORITY.issue(
        port,
        resources,
        source_path,
        s0,
        s1,
        s2,
        lease_id_factory=lease_id_factory,
    )


def _run_lease_io[IoResultT](
    lease: CloseGateLease,
    cancellation: CancellationToken,
    operation: Callable[[_LeaseIoSession], IoResultT],
) -> IoResultT | _LeaseIoUnavailable:
    """Run an authenticated exclusive lease-handle I/O callback."""
    return _LEASE_AUTHORITY.run_io(lease, cancellation, operation)
