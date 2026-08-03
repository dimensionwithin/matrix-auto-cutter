"""Atomic human decisions and the sole future render-authorization gate."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError

from matrix_auto_cutter.cut_proposal import (
    CutProposal,
    ProposalFailed,
    ProposalReady,
    load_proposal,
)
from matrix_auto_cutter.models import CanonicalModel, Sha256

APPROVAL_FILE_NAME = "approval.json"
APPROVAL_SCHEMA_VERSION: Literal["1.0"] = "1.0"
MAX_APPROVAL_BYTES = 64 * 1024

Decision = Literal["pending", "approved", "rejected"]


class ProposalApproval(CanonicalModel):
    """Last explicit decision bound to exact proposal and source evidence."""

    artifact_type: Literal["matrix_auto_cutter_proposal_approval"]
    schema_version: Literal["1.0"]
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    proposal_sha256: Sha256
    proposal_digest: Sha256
    source_identity_digest: Sha256
    recording_id: str = Field(min_length=1, max_length=100)
    sidecar_sha256: Sha256
    decision: Decision
    decided_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class ApprovalGateResult:
    """Fail-closed result consumed by every future rendering composition."""

    authorized: bool
    decision: Decision
    reason: str
    proposal: CutProposal | None = None
    approval: ProposalApproval | None = None


@dataclass(frozen=True, slots=True)
class DecisionWritten:
    """Successful atomic decision update."""

    approval: ProposalApproval
    approval_path: Path


@dataclass(frozen=True, slots=True)
class DecisionFailed:
    """Stable failure that never authorizes rendering."""

    code: str
    message_de: str


type DecisionResult = DecisionWritten | DecisionFailed


def approval_path_for(proposal_path: Path) -> Path:
    """Derive the sole approval location from the immutable proposal generation."""
    if proposal_path.name != "cut-proposal.json":
        raise ValueError("proposal path must end in cut-proposal.json")
    return proposal_path.with_name(APPROVAL_FILE_NAME)


def _approval_bytes(approval: ProposalApproval) -> bytes:
    return (approval.model_dump_json() + "\n").encode("utf-8")


def _bound_approval(
    ready: ProposalReady,
    decision: Decision,
    decided_at: datetime,
) -> ProposalApproval:
    proposal = ready.proposal
    return ProposalApproval(
        artifact_type="matrix_auto_cutter_proposal_approval",
        schema_version=APPROVAL_SCHEMA_VERSION,
        proposal_id=proposal.proposal_id,
        proposal_sha256=ready.proposal_sha256,
        proposal_digest=proposal.proposal_digest,
        source_identity_digest=proposal.source_identity_digest,
        recording_id=proposal.recording_id,
        sidecar_sha256=proposal.sidecar_sha256,
        decision=decision,
        decided_at=decided_at,
    )


def _atomic_write(path: Path, data: bytes, *, create_only: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{hashlib.sha256(data).hexdigest()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            try:
                os.rename(temporary, path)
            except FileExistsError:
                return False
        else:
            os.replace(temporary, path)
        return True
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _read_approval(path: Path) -> ProposalApproval | None:
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_APPROVAL_BYTES:
            return None
        approval = ProposalApproval.model_validate_json(data)
        if data != _approval_bytes(approval):
            return None
        return approval
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None


def ensure_pending_approval(
    proposal_path: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DecisionResult:
    """Materialize the default pending state exactly once without replacing decisions."""
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return DecisionFailed(loaded.code, loaded.message_de)
    target = approval_path_for(proposal_path)
    if target.exists():
        gate = check_render_authorization(proposal_path)
        if gate.approval is None:
            return DecisionFailed(
                "E_APPROVAL_INVALID",
                "Vorhandenes Approval ist beschädigt oder nicht an dieses Proposal gebunden.",
            )
        return DecisionWritten(gate.approval, target)
    pending = _bound_approval(loaded, "pending", now())
    if _atomic_write(target, _approval_bytes(pending), create_only=True):
        return DecisionWritten(pending, target)
    existing = _read_approval(target)
    if existing is None:
        return DecisionFailed("E_APPROVAL_RACE", "Gleichzeitig erschienenes Approval ist ungültig.")
    gate = check_render_authorization(proposal_path)
    if gate.approval is None:
        return DecisionFailed("E_APPROVAL_RACE", "Approval-Race war nicht proposalgebunden.")
    return DecisionWritten(gate.approval, target)


def record_decision(
    proposal_path: Path,
    decision: Literal["approved", "rejected"],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> DecisionResult:
    """Re-read, validate, and hash the proposal immediately before atomic replacement."""
    if decision not in {"approved", "rejected"}:
        return DecisionFailed(
            "E_DECISION", "Nur approved oder rejected ist eine Benutzerentscheidung."
        )
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return DecisionFailed(loaded.code, loaded.message_de)
    approval = _bound_approval(loaded, decision, now())
    target = approval_path_for(proposal_path)
    try:
        _atomic_write(target, _approval_bytes(approval), create_only=False)
    except OSError as exc:
        return DecisionFailed(
            "E_APPROVAL_WRITE",
            f"Approval konnte nicht atomar geschrieben werden: {exc}",
        )
    observed = _read_approval(target)
    if observed != approval:
        return DecisionFailed(
            "E_APPROVAL_VERIFY",
            "Atomar geschriebenes Approval konnte nicht identisch erneut gelesen werden.",
        )
    return DecisionWritten(approval, target)


def _matches(approval: ProposalApproval, ready: ProposalReady) -> bool:
    proposal = ready.proposal
    return (
        approval.proposal_id == proposal.proposal_id
        and approval.proposal_sha256 == ready.proposal_sha256
        and approval.proposal_digest == proposal.proposal_digest
        and approval.source_identity_digest == proposal.source_identity_digest
        and approval.recording_id == proposal.recording_id
        and approval.sidecar_sha256 == proposal.sidecar_sha256
    )


def _sha256_file(path: Path) -> str | None:
    """Hash one regular file without ever opening it for writing."""
    try:
        if not path.is_file():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _live_bindings_match(ready: ProposalReady) -> str | None:
    """Return a fail-closed reason when current source or sidecar evidence changed."""
    proposal = ready.proposal
    source = Path(proposal.source_path)
    try:
        source_stat = source.stat()
    except OSError:
        return "Source fehlt oder kann nicht mehr gelesen werden."
    if (
        not source.is_file()
        or source.name != proposal.source_identity.file_name
        or source_stat.st_size != proposal.source_identity.size_bytes
        or _sha256_file(source) != proposal.source_identity.sha256
    ):
        return "Aktuelle SourceIdentity passt nicht mehr zum freigegebenen Proposal."
    if _sha256_file(Path(proposal.sidecar_path)) != proposal.sidecar_sha256:
        return "Aktuelles Sidecar passt nicht mehr zum freigegebenen Proposal."
    return None


def _check_authorization(proposal_path: Path, *, verify_live_bindings: bool) -> ApprovalGateResult:
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return ApprovalGateResult(False, "pending", loaded.message_de)
    approval = _read_approval(approval_path_for(proposal_path))
    if approval is None:
        return ApprovalGateResult(
            False,
            "pending",
            "Approval fehlt, ist beschädigt oder hat ein unbekanntes Schema.",
            loaded.proposal,
        )
    if not _matches(approval, loaded):
        return ApprovalGateResult(
            False,
            approval.decision,
            "Approval-Bindung passt nicht vollständig zum Proposal.",
            loaded.proposal,
            approval,
        )
    if verify_live_bindings:
        live_failure = _live_bindings_match(loaded)
        if live_failure is not None:
            return ApprovalGateResult(
                False,
                approval.decision,
                live_failure,
                loaded.proposal,
                approval,
            )
    if approval.decision == "rejected":
        return ApprovalGateResult(
            False,
            "rejected",
            "Proposal wurde ausdrücklich abgelehnt.",
            loaded.proposal,
            approval,
        )
    if approval.decision == "pending":
        return ApprovalGateResult(
            False,
            "pending",
            "Proposal wartet auf eine ausdrückliche Entscheidung.",
            loaded.proposal,
            approval,
        )
    if loaded.proposal.status != "ready" or not loaded.proposal.proposed_cuts:
        return ApprovalGateResult(
            False,
            "approved",
            "Proposal ist freigegeben, enthält aber keinen renderbaren Schnitt.",
            loaded.proposal,
            approval,
        )
    return ApprovalGateResult(
        True,
        "approved",
        "Exakt dieses Proposal ist ausdrücklich freigegeben.",
        loaded.proposal,
        approval,
    )


def inspect_approval_state(proposal_path: Path) -> ApprovalGateResult:
    """Inspect proposal/approval artifacts without authorizing a render."""
    return _check_authorization(proposal_path, verify_live_bindings=False)


def check_render_authorization(proposal_path: Path) -> ApprovalGateResult:
    """Central fail-closed gate including current source and sidecar evidence."""
    return _check_authorization(proposal_path, verify_live_bindings=True)
