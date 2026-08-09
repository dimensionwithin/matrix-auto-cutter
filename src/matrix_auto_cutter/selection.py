"""Canonical, proposal-bound selection of existing cut candidates.

The selection is deliberately separate from the immutable proposal.  It can only
flip existing candidates on or off; it can never introduce or move a boundary.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, ValidationError, model_validator

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.cut_proposal import (
    ProposalFailed,
    ProposalReady,
    load_proposal,
)
from matrix_auto_cutter.models import CanonicalModel, Sha256

SELECTION_FILE_NAME = "cut-selection.json"
SELECTION_SCHEMA_VERSION: Literal["1.0"] = "1.0"
MAX_SELECTION_BYTES = 256 * 1024
_DIGEST_DOMAIN = b"matrix-auto-cutter/cut-selection/1.0\0"


class SelectedCandidate(CanonicalModel):
    """One immutable proposal reference and the user's boolean decision."""

    candidate_id: str = Field(pattern=r"^candidate-[0-9a-f]{24}$")
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    enabled: bool

    @model_validator(mode="after")
    def positive(self) -> SelectedCandidate:
        """Require a positive half-open proposal frame range."""
        if self.start_frame >= self.end_frame:
            raise ValueError("selected candidate requires start_frame < end_frame")
        return self


class CutSelectionContent(CanonicalModel):
    """All selection fields that are protected by ``selection_digest``."""

    artifact_type: Literal["matrix_auto_cutter_cut_selection"]
    schema_version: Literal["1.0"]
    proposal_id: str = Field(pattern=r"^proposal-[0-9a-f]{32}$")
    proposal_sha256: Sha256
    proposal_digest: Sha256
    source_identity_digest: Sha256
    recording_id: str = Field(min_length=1, max_length=100)
    sidecar_sha256: Sha256
    candidates: tuple[SelectedCandidate, ...]
    enabled_count: int = Field(ge=0)
    selected_savings_ms: int = Field(ge=0)
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def count_matches(self) -> CutSelectionContent:
        """Validate aggregate count and candidate-ID uniqueness."""
        if self.enabled_count != sum(item.enabled for item in self.candidates):
            raise ValueError("enabled selection count mismatch")
        identifiers = tuple(item.candidate_id for item in self.candidates)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("selection contains duplicate candidate ids")
        return self


class CutSelection(CutSelectionContent):
    """Canonical on-disk selection including a domain-separated digest."""

    selection_digest: Sha256

    @model_validator(mode="after")
    def digest_matches(self) -> CutSelection:
        """Bind the embedded digest to every selection content field."""
        if self.selection_digest != selection_content_digest(_content(self)):
            raise ValueError("selection digest mismatch")
        return self


@dataclass(frozen=True, slots=True)
class SelectionReady:
    """A strictly loaded and proposal-bound canonical selection."""

    selection: CutSelection
    selection_path: Path
    selection_sha256: str
    reused: bool


@dataclass(frozen=True, slots=True)
class SelectionFailed:
    """A selection failure that never authorizes approval or render."""

    code: str
    message_de: str


type SelectionResult = SelectionReady | SelectionFailed


def selection_path_for(proposal_path: Path) -> Path:
    """Derive the sole selection file location for a proposal generation."""
    if proposal_path.name != "cut-proposal.json":
        raise ValueError("proposal path must end in cut-proposal.json")
    return proposal_path.with_name(SELECTION_FILE_NAME)


def _content(selection: CutSelection) -> CutSelectionContent:
    return CutSelectionContent.model_validate_json(
        selection.model_dump_json(exclude={"selection_digest"})
    )


def selection_content_digest(content: CutSelectionContent) -> str:
    """Hash all canonical selection content with domain separation."""
    return hashlib.sha256(_DIGEST_DOMAIN + content.model_dump_json().encode("utf-8")).hexdigest()


def selection_bytes(selection: CutSelection) -> bytes:
    """Return the only canonical bytes permitted on disk."""
    return (selection.model_dump_json() + "\n").encode("utf-8")


def _selection_for(
    ready: ProposalReady,
    enabled: Mapping[str, bool],
    now: datetime,
) -> CutSelection:
    proposal = ready.proposal
    known = {item.candidate_id for item in proposal.proposed_cuts}
    if set(enabled) != known:
        raise ValueError("selection must contain every and only proposal candidate")
    candidates = tuple(
        SelectedCandidate(
            candidate_id=item.candidate_id,
            start_frame=item.start_frame,
            end_frame=item.end_frame,
            enabled=enabled[item.candidate_id],
        )
        for item in proposal.proposed_cuts
    )
    content = CutSelectionContent(
        artifact_type="matrix_auto_cutter_cut_selection",
        schema_version=SELECTION_SCHEMA_VERSION,
        proposal_id=proposal.proposal_id,
        proposal_sha256=ready.proposal_sha256,
        proposal_digest=proposal.proposal_digest,
        source_identity_digest=proposal.source_identity_digest,
        recording_id=proposal.recording_id,
        sidecar_sha256=proposal.sidecar_sha256,
        candidates=candidates,
        enabled_count=sum(item.enabled for item in candidates),
        selected_savings_ms=sum(
            item.duration_ms for item in proposal.proposed_cuts if enabled[item.candidate_id]
        ),
        updated_at=now,
    )
    payload = content.model_dump(mode="python")
    payload["selection_digest"] = selection_content_digest(content)
    return CutSelection.model_validate(payload)


def _validate_against(selection: CutSelection, ready: ProposalReady) -> None:
    proposal = ready.proposal
    if (
        selection.proposal_id != proposal.proposal_id
        or selection.proposal_sha256 != ready.proposal_sha256
        or selection.proposal_digest != proposal.proposal_digest
        or selection.source_identity_digest != proposal.source_identity_digest
        or selection.recording_id != proposal.recording_id
        or selection.sidecar_sha256 != proposal.sidecar_sha256
    ):
        raise ValueError("selection is not bound to this exact proposal")
    expected = tuple(
        (item.candidate_id, item.start_frame, item.end_frame) for item in proposal.proposed_cuts
    )
    observed = tuple(
        (item.candidate_id, item.start_frame, item.end_frame) for item in selection.candidates
    )
    if observed != expected:
        raise ValueError("selection candidate order or frame boundaries differ from proposal")
    expected_savings = sum(
        item.duration_ms
        for item, selected in zip(proposal.proposed_cuts, selection.candidates, strict=True)
        if selected.enabled
    )
    if selection.selected_savings_ms != expected_savings:
        raise ValueError("selection savings differ from proposal candidates")


def load_selection(proposal_path: Path) -> SelectionResult:
    """Strictly load canonical bytes and validate all proposal bindings."""
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return SelectionFailed(loaded.code, loaded.message_de)
    path = selection_path_for(proposal_path)
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_SELECTION_BYTES:
            raise ValueError("selection size is outside the contract")
        selection = CutSelection.model_validate_json(data)
        if data != selection_bytes(selection):
            raise ValueError("selection bytes are not canonical")
        _validate_against(selection, loaded)
        return SelectionReady(selection, path, hashlib.sha256(data).hexdigest(), True)
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        return SelectionFailed("E_SELECTION_INVALID", f"Auswahl ist ungültig: {exc}")


def _atomic_write(path: Path, data: bytes, *, replace: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{hashlib.sha256(data).hexdigest()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            replace_atomically(temporary, path)
            return True
        try:
            replace_atomically(temporary, path, create_only=True)
            return True
        except FileExistsError:
            return False
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def ensure_selection(
    proposal_path: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SelectionResult:
    """Create the all-enabled default exactly once, never adopting an old proposal."""
    existing = load_selection(proposal_path)
    if isinstance(existing, SelectionReady):
        return existing
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return SelectionFailed(loaded.code, loaded.message_de)
    target = selection_path_for(proposal_path)
    if target.exists():
        return existing
    selection = _selection_for(
        loaded, {item.candidate_id: True for item in loaded.proposal.proposed_cuts}, now()
    )
    data = selection_bytes(selection)
    if not _atomic_write(target, data, replace=False):
        return load_selection(proposal_path)
    return SelectionReady(selection, target, hashlib.sha256(data).hexdigest(), False)


def update_selection(
    proposal_path: Path,
    enabled: Mapping[str, bool],
    *,
    expected_selection_digest: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SelectionResult:
    """Atomically replace one selection after revalidating its proposal binding."""
    current = ensure_selection(proposal_path, now=now)
    if isinstance(current, SelectionFailed):
        return current
    if (
        expected_selection_digest is not None
        and current.selection.selection_digest != expected_selection_digest
    ):
        return SelectionFailed("E_SELECTION_CONFLICT", "Auswahl wurde zwischenzeitlich geändert.")
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        return SelectionFailed(loaded.code, loaded.message_de)
    try:
        selection = _selection_for(loaded, enabled, now())
    except ValueError as exc:
        return SelectionFailed("E_SELECTION_INVALID", f"Auswahl ist ungültig: {exc}")
    data = selection_bytes(selection)
    try:
        _atomic_write(selection_path_for(proposal_path), data, replace=True)
    except OSError as exc:
        return SelectionFailed(
            "E_SELECTION_WRITE", f"Auswahl konnte nicht atomar gespeichert werden: {exc}"
        )
    observed = load_selection(proposal_path)
    if not isinstance(observed, SelectionReady) or observed.selection != selection:
        return SelectionFailed(
            "E_SELECTION_VERIFY", "Gespeicherte Auswahl konnte nicht identisch gelesen werden."
        )
    return observed


def active_candidate_ids(selection: CutSelection) -> tuple[str, ...]:
    """Return enabled candidate IDs in immutable proposal order."""
    return tuple(item.candidate_id for item in selection.candidates if item.enabled)
