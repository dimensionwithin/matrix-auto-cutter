"""Public package-2E requests, results, and preserved failure contracts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from matrix_auto_cutter.models import SourceBinding, SourceIdentity
from matrix_auto_cutter.phase2.close_gate import CloseGateLease, CloseGateWin32Port
from matrix_auto_cutter.phase2.probe import ProbeError, ValidatedFfprobeBinary
from matrix_auto_cutter.phase2.probe.binary import BinaryTrustPort
from matrix_auto_cutter.phase2.probe.process_port import ProcessPort
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    ArtifactReference,
    SourceIdentityEvidence,
    StreamAssignment,
)
from matrix_auto_cutter.phase2.source_confirmation.state import SourceState
from matrix_auto_cutter.phase2.source_hash import HashFailure
from matrix_auto_cutter.phase2.workspace import ProjectCapability

if TYPE_CHECKING:
    from matrix_auto_cutter.phase2.source_confirmation.capability import ConfirmedSource


class ConfirmationErrorCode(StrEnum):
    """Package-specific codes layered over preserved 2B/2C/2D failures."""

    INVALID_INPUT = "E_SOURCE_CONFIRMATION_INPUT"
    LEASE_INVALID = "E_SOURCE_CONFIRMATION_LEASE"
    SOURCE_CHANGED = "E_SOURCE_CHANGED"
    ASSIGNMENT_STALE = "E_STREAM_ASSIGNMENT_STALE"
    ASSIGNMENT_INVALID = "E_STREAM_ASSIGNMENT_INVALID"
    ARTIFACT_CONFLICT = "E_SOURCE_ARTIFACT_CONFLICT"
    INTEGRITY = "E_SOURCE_CONFIRMATION_INTEGRITY"
    IO = "E_SOURCE_CONFIRMATION_IO"
    CANCELLED = "E_CANCELLED"


class ConfirmationErrorCategory(StrEnum):
    """Closed categories needed only for package-2E integration failures."""

    INPUT = "input"
    POLICY = "policy"
    INTEGRITY = "integrity"
    CONFLICT = "conflict"
    IO = "io"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ConfirmationDiagnostic:
    """Bounded secondary cleanup or post-validation diagnostic."""

    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None


@dataclass(frozen=True, slots=True)
class ConfirmationFailure:
    """Structured 2E-only failure that retains its primary lower-layer cause."""

    code: ConfirmationErrorCode
    category: ConfirmationErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None
    underlying: object | None = None
    retryable: bool = False
    cleanup_diagnostics: tuple[ConfirmationDiagnostic, ...] = ()

    def with_cleanup(self, diagnostics: tuple[ConfirmationDiagnostic, ...]) -> ConfirmationFailure:
        """Append bounded cleanup evidence without replacing the primary failure."""
        return replace(
            self,
            cleanup_diagnostics=(*self.cleanup_diagnostics, *diagnostics)[:8],
        )


type PrimaryFailure = ConfirmationFailure | ProbeError | HashFailure


@dataclass(frozen=True, slots=True)
class ConfirmationPorts:
    """Injectable adapters used by the 2E integration core."""

    win32: CloseGateWin32Port
    binary_trust: BinaryTrustPort
    probe_process: ProcessPort


@dataclass(frozen=True, slots=True)
class SourceConfirmationRequest:
    """All caller inputs for one new probe/hash/identity confirmation run."""

    project: ProjectCapability
    identity_run_id: str
    probe_id: str
    probe_run_id: str
    hash_run_id: str
    lease: CloseGateLease
    binary: ValidatedFfprobeBinary
    binding: SourceBinding
    assignment: ArtifactReference | None = None
    probe_timeout_seconds: int = 120


@dataclass(frozen=True, slots=True)
class EvidenceReferences:
    """Validated immutable artifacts produced by one successful run."""

    media_probe: ArtifactReference
    hash_receipt: ArtifactReference
    source_identity_evidence_path: str
    source_identity_evidence_digest: str


@dataclass(frozen=True, slots=True)
class SourceConfirmed:
    """Successful 2E result with runtime capability and immutable references."""

    confirmed_source: ConfirmedSource
    source_identity: SourceIdentity
    evidence: EvidenceReferences
    final_state: Literal[SourceState.CONFIRMED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceConfirmationCancelled:
    """Cancellation won before the final runtime commit."""

    error: PrimaryFailure
    final_state: Literal[SourceState.CANCELLED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceInvalidated:
    """Evidence proved a changed path, file instance, snapshot, or lease epoch."""

    error: PrimaryFailure
    final_state: Literal[SourceState.INVALIDATED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceDisappeared:
    """The bound source path disappeared during a required revalidation."""

    error: PrimaryFailure
    final_state: Literal[SourceState.DISAPPEARED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceUnsupported:
    """Current media or binding cannot satisfy the closed confirmation contract."""

    error: PrimaryFailure
    media_probe: ArtifactReference | None
    final_state: Literal[SourceState.UNSUPPORTED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceAssignmentRequired:
    """A genuine current 2B ambiguity was persisted for explicit assignment."""

    error: ProbeError
    media_probe: ArtifactReference
    final_state: Literal[SourceState.UNSUPPORTED]
    state_history: tuple[SourceState, ...]


@dataclass(frozen=True, slots=True)
class SourceConfirmationFailed:
    """Operational or integrity failure with the original primary cause."""

    error: PrimaryFailure
    media_probe: ArtifactReference | None
    final_state: Literal[SourceState.FAILED]
    state_history: tuple[SourceState, ...]


type SourceConfirmationResult = (
    SourceConfirmed
    | SourceConfirmationCancelled
    | SourceInvalidated
    | SourceDisappeared
    | SourceUnsupported
    | SourceAssignmentRequired
    | SourceConfirmationFailed
)


@dataclass(frozen=True, slots=True)
class StreamAssignmentRequest:
    """Explicit user choice bound to one published ambiguous media probe."""

    project: ProjectCapability
    assignment_run_id: str
    media_probe: ArtifactReference
    video_index: int
    audio_index: int
    diagnostic_note: str | None = None


@dataclass(frozen=True, slots=True)
class StreamAssignmentCreated:
    """New or byte-identical assignment artifact."""

    status: Literal["published", "idempotent"]
    assignment: StreamAssignment
    reference: ArtifactReference


@dataclass(frozen=True, slots=True)
class StreamAssignmentCancelled:
    """Cancellation won before assignment publication."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class StreamAssignmentConflict:
    """The immutable assignment target already contains different bytes."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class StreamAssignmentFailed:
    """Assignment input, source evidence, validation, or I/O failed."""

    error: ConfirmationFailure


type StreamAssignmentResult = (
    StreamAssignmentCreated
    | StreamAssignmentCancelled
    | StreamAssignmentConflict
    | StreamAssignmentFailed
)


@dataclass(frozen=True, slots=True)
class PreparedIdentityEvidence:
    """Private typed pair used at the evidence publication boundary."""

    identity: SourceIdentity
    evidence: SourceIdentityEvidence
