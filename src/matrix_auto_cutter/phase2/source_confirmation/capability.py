"""Issuer-bound runtime-only ConfirmedSource capability."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock, RLock
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from matrix_auto_cutter.models import SourceIdentity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import CloseGateLease, RecheckResult
from matrix_auto_cutter.phase2.close_gate.lease import (
    _LeaseUsageSession,
    _LeaseUsageUnavailable,
    _run_lease_usage,
)
from matrix_auto_cutter.phase2.locks import ProjectLockLease
from matrix_auto_cutter.phase2.pathing import ValidatedPath
from matrix_auto_cutter.phase2.source_confirmation.evidence import SourceIdentityEvidence

_CONFIRMED_SOURCE_SEAL: Final = object()


class ConfirmedSource:
    """Live source authority that can never be persisted or freely constructed."""

    __slots__ = (
        "__weakref__",
        "_evidence",
        "_identity",
        "_lease",
        "_lease_epoch",
        "_project_id",
        "_run_id",
        "_token",
    )
    _evidence: SourceIdentityEvidence
    _identity: SourceIdentity
    _lease: CloseGateLease
    _lease_epoch: UUID
    _project_id: str
    _run_id: str
    _token: object

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Reject construction outside the private committed issuer."""
        del args, kwargs
        raise TypeError("ConfirmedSource is issued only after a committed 2E confirmation")

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation of every authority-bearing slot."""
        del name, value
        raise AttributeError("ConfirmedSource bindings are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject deletion of every authority-bearing slot."""
        del name
        raise AttributeError("ConfirmedSource bindings are immutable")

    def _initialize(
        self,
        identity: SourceIdentity,
        evidence: SourceIdentityEvidence,
        project_id: str,
        run_id: str,
        lease: CloseGateLease,
        token: object,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _CONFIRMED_SOURCE_SEAL:
            raise TypeError("ConfirmedSource is issued only by package 2E")
        object.__setattr__(self, "_identity", identity)
        object.__setattr__(self, "_evidence", evidence)
        object.__setattr__(self, "_project_id", project_id)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_lease", lease)
        object.__setattr__(self, "_lease_epoch", lease.validation_epoch)
        object.__setattr__(self, "_token", token)

    @property
    def source_identity(self) -> SourceIdentity:
        """Return the unchanged Phase-1 identity value."""
        return self._identity

    @property
    def evidence(self) -> SourceIdentityEvidence:
        """Return the fully validated immutable evidence value."""
        return self._evidence

    @property
    def project_id(self) -> str:
        """Return the project authority binding."""
        return self._project_id

    @property
    def run_id(self) -> str:
        """Return the identity-run authority binding."""
        return self._run_id

    @property
    def lease_epoch(self) -> str:
        """Return the live lease epoch without exposing the source handle or lease."""
        return str(self._lease_epoch)

    @property
    def authorized(self) -> bool:
        """Return whether issuer authority and the held lease are still live."""
        return _CONFIRMED_AUTHORITY.is_authorized(self)

    def require_authorized(self) -> SourceIdentity:
        """Return the identity only while this exact runtime capability remains live."""
        if not self.authorized:
            raise RuntimeError("ConfirmedSource authority is closed, invalidated, or forged")
        return self._identity

    def __copy__(self) -> ConfirmedSource:
        """Reject shallow capability copying."""
        raise TypeError("ConfirmedSource capabilities cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> ConfirmedSource:
        """Reject deep capability copying."""
        del memo
        raise TypeError("ConfirmedSource capabilities cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        """Reject pickle and reconstruction authority."""
        raise TypeError("ConfirmedSource capabilities cannot be serialized")


@dataclass(frozen=True, slots=True)
class _ConfirmedSourceUsageUnavailable:
    """Private fail-closed reason for an unavailable authenticated usage."""

    reason: str


class _ConfirmedSourceUsage:
    """Handle-free authenticated 2E usage held across a dependent commit."""

    __slots__ = ("_active", "_capability", "_lease_usage", "_record")

    def __init__(
        self,
        capability: ConfirmedSource,
        lease_usage: _LeaseUsageSession,
        record: _ConfirmedAuthorityRecord,
    ) -> None:
        self._capability = capability
        self._lease_usage = lease_usage
        self._record = record
        self._active = True

    def _authorized(self) -> bool:
        with self._record.lock:
            return (
                self._active
                and not self._record.revoked
                and self._record.active_usages > 0
                and self._record.matches(self._capability)
            )

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("confirmed source usage is no longer active")

    def _deactivate(self) -> None:
        self._active = False

    @property
    def source_identity(self) -> SourceIdentity:
        return self._record.identity

    @property
    def evidence(self) -> SourceIdentityEvidence:
        return self._record.evidence

    @property
    def project_id(self) -> str:
        return self._record.project_id

    @property
    def run_id(self) -> str:
        return self._record.run_id

    @property
    def source_path(self) -> ValidatedPath:
        return self._record.lease.source_path

    @property
    def volume_id(self) -> str:
        return self._record.lease.volume_id

    @property
    def file_id(self) -> str:
        return self._record.lease.file_id

    def recheck(self, cancellation: CancellationToken) -> RecheckResult:
        """Recheck through the same hidden lease while this usage is active."""
        self._require_active()
        with self._record.lock:
            if (
                self._record.revoked
                or self._record.active_usages <= 0
                or not self._record.matches(self._capability)
            ):
                cancelled = CancellationToken()
                cancelled.cancel()
                return self._lease_usage.recheck(cancelled)
            return self._lease_usage.recheck(cancellation)

    def commit(self, cancellation: CancellationToken) -> bool:
        """Linearize a dependent commit against cancellation and lease close."""
        self._require_active()
        with self._record.lock:
            if (
                self._record.revoked
                or self._record.active_usages <= 0
                or not self._record.matches(self._capability)
            ):
                return False
            return self._lease_usage.commit(cancellation)

    def run_project_locked[ResultT](
        self,
        operation: Callable[[ProjectLockLease], ResultT],
    ) -> ResultT:
        """Use the already-held Project Lock without exporting it publicly."""
        self._require_active()
        if not self._authorized():
            raise RuntimeError("confirmed source authority is unavailable")
        return self._lease_usage.run_project_locked(operation)

    def matches_port(self, port: object) -> bool:
        """Bind dependent I/O and target locks to the lease's exact OS adapter."""
        self._require_active()
        return self._lease_usage.matches_port(port)


@dataclass(slots=True)
class _ConfirmedAuthorityRecord:
    """Per-capability authority state; never held across a finalizer operation."""

    token: object
    identity: SourceIdentity
    evidence: SourceIdentityEvidence
    project_id: str
    run_id: str
    lease: CloseGateLease
    lock: Lock = field(default_factory=Lock)
    active_usages: int = 0
    revoked: bool = False

    def matches(self, capability: ConfirmedSource) -> bool:
        try:
            return (
                capability._token is self.token
                and capability._identity is self.identity
                and capability._evidence is self.evidence
                and capability._project_id == self.project_id
                and capability._run_id == self.run_id
                and capability._lease is self.lease
                and capability._lease_epoch == self.lease.validation_epoch
                and self.evidence.source_identity == self.identity
                and self.evidence.project_id == self.project_id
            )
        except AttributeError:
            return False


class _ConfirmedSourceAuthority:
    """Weak issuer registry that cannot prolong capability or lease lifetime."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: WeakKeyDictionary[ConfirmedSource, _ConfirmedAuthorityRecord] = (
            WeakKeyDictionary()
        )

    def issue(
        self,
        identity: SourceIdentity,
        evidence: SourceIdentityEvidence,
        project_id: str,
        run_id: str,
        lease: CloseGateLease,
    ) -> ConfirmedSource:
        if lease.closed:
            raise ValueError("cannot issue ConfirmedSource for a closed lease")
        if (
            evidence.source_identity != identity
            or evidence.project_id != project_id
            or evidence.identity_run_id != run_id
            or evidence.lease_epoch != str(lease.validation_epoch)
        ):
            raise ValueError("ConfirmedSource inputs are not evidence-identical")
        token = object()
        capability = object.__new__(ConfirmedSource)
        capability._initialize(
            identity,
            evidence,
            project_id,
            run_id,
            lease,
            token,
            _seal=_CONFIRMED_SOURCE_SEAL,
        )
        with self._lock:
            self._records[capability] = _ConfirmedAuthorityRecord(
                token,
                identity,
                evidence,
                project_id,
                run_id,
                lease,
            )
        return capability

    def is_authorized(self, capability: ConfirmedSource) -> bool:
        try:
            token = capability._token
        except AttributeError:
            return False
        with self._lock:
            record = self._records.get(capability)
        if record is None:
            return False
        with record.lock:
            registered = record.token is token and not record.revoked and record.matches(capability)
        return (
            registered
            and not record.lease.closed
            and record.evidence.lease_epoch == str(record.lease.validation_epoch)
        )

    def revoke(self, capability: ConfirmedSource) -> None:
        with self._lock:
            record = self._records.pop(capability, None)
        if record is not None:
            with record.lock:
                record.revoked = True

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def run_usage[UsageResultT](
        self,
        capability: ConfirmedSource,
        cancellation: CancellationToken,
        operation: Callable[[_ConfirmedSourceUsage], UsageResultT],
    ) -> UsageResultT | _ConfirmedSourceUsageUnavailable:
        """Hold issuer identity and the existing lease usage for one operation."""
        try:
            token = capability._token
        except AttributeError:
            return _ConfirmedSourceUsageUnavailable("confirmed_source_not_authorized")
        with self._lock:
            record = self._records.get(capability)
        if record is None:
            return _ConfirmedSourceUsageUnavailable("confirmed_source_not_authorized")
        with record.lock:
            if (
                record.token is not token
                or record.revoked
                or not record.matches(capability)
                or cancellation.is_cancelled
            ):
                return _ConfirmedSourceUsageUnavailable("confirmed_source_not_authorized")
            record.active_usages += 1

        def run(lease_usage: _LeaseUsageSession) -> UsageResultT:
            session = _ConfirmedSourceUsage(capability, lease_usage, record)
            try:
                return operation(session)
            finally:
                session._deactivate()

        try:
            result = _run_lease_usage(
                record.lease,
                record.project_id,
                cancellation,
                run,
            )
            if isinstance(result, _LeaseUsageUnavailable):
                return _ConfirmedSourceUsageUnavailable(result.reason)
            return result
        finally:
            with record.lock:
                record.active_usages -= 1


_CONFIRMED_AUTHORITY = _ConfirmedSourceAuthority()


def _issue_confirmed_source(
    identity: SourceIdentity,
    evidence: SourceIdentityEvidence,
    project_id: str,
    run_id: str,
    lease: CloseGateLease,
) -> ConfirmedSource:
    """Issue only after the orchestrator has crossed its final commit boundary."""
    return _CONFIRMED_AUTHORITY.issue(identity, evidence, project_id, run_id, lease)


def _invalidate_confirmed_source(capability: ConfirmedSource) -> None:
    """Revoke issuer authority without closing or exporting the lease."""
    _CONFIRMED_AUTHORITY.revoke(capability)


def _run_confirmed_source_usage[UsageResultT](
    capability: ConfirmedSource,
    cancellation: CancellationToken,
    operation: Callable[[_ConfirmedSourceUsage], UsageResultT],
) -> UsageResultT | _ConfirmedSourceUsageUnavailable:
    """Run one private authenticated handle-free ConfirmedSource usage."""
    return _CONFIRMED_AUTHORITY.run_usage(capability, cancellation, operation)
