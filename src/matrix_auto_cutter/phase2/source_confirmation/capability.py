"""Issuer-bound runtime-only ConfirmedSource capability."""

from __future__ import annotations

from threading import Lock
from typing import Final
from uuid import UUID
from weakref import WeakKeyDictionary

from matrix_auto_cutter.models import SourceIdentity
from matrix_auto_cutter.phase2.close_gate import CloseGateLease
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


class _ConfirmedSourceAuthority:
    """Weak issuer registry that cannot prolong capability or lease lifetime."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._records: WeakKeyDictionary[ConfirmedSource, object] = WeakKeyDictionary()

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
            self._records[capability] = token
        return capability

    def is_authorized(self, capability: ConfirmedSource) -> bool:
        try:
            token = capability._token
            lease = capability._lease
            evidence = capability._evidence
            identity = capability._identity
        except AttributeError:
            return False
        with self._lock:
            registered = self._records.get(capability) is token
        return (
            registered
            and not lease.closed
            and evidence.source_identity == identity
            and evidence.lease_epoch == str(lease.validation_epoch)
        )

    def revoke(self, capability: ConfirmedSource) -> None:
        with self._lock:
            self._records.pop(capability, None)

    def count(self) -> int:
        with self._lock:
            return len(self._records)


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
