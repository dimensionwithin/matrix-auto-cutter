"""Closed package-2F errors and discriminated public results."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from matrix_auto_cutter.models import SourceIdentity


class FinalizerErrorCode(StrEnum):
    """Closed public package-2F error-code vocabulary."""

    CANCELLED = "E_CANCELLED"
    JOURNAL_INCOMPLETE = "E_JOURNAL_INCOMPLETE"
    JOURNAL_SEQUENCE = "E_JOURNAL_SEQUENCE"
    JOURNAL_OUTPUT_FAILURE = "E_JOURNAL_OUTPUT_FAILURE"
    JOURNAL_CORRUPT = "E_JOURNAL_CORRUPT"
    JOURNAL_SOURCE_MISMATCH = "E_JOURNAL_SOURCE_MISMATCH"
    BUNDLE_MISSING = "E_BUNDLE_MISSING"
    BUNDLE_VERSION = "E_BUNDLE_VERSION"
    BUNDLE_CORRUPT = "E_BUNDLE_CORRUPT"
    BUNDLE_DIGEST = "E_BUNDLE_DIGEST"
    BUNDLE_BINDING = "E_BUNDLE_BINDING"
    SOURCE_UNAUTHORIZED = "E_SOURCE_UNAUTHORIZED"
    FINALIZER_CONCURRENT = "E_FINALIZER_CONCURRENT"
    TARGET_ALREADY_EXISTS = "E_TARGET_ALREADY_EXISTS"
    RECOVERY_CONFLICT = "E_RECOVERY_CONFLICT"
    ATOMIC_PUBLISH_FAILED = "E_ATOMIC_PUBLISH_FAILED"
    FINALIZER_INTERNAL = "E_FINALIZER_INTERNAL"


class FinalizerErrorCategory(StrEnum):
    """Stable classification of package-2F failures."""

    INPUT = "input"
    INTEGRITY = "integrity"
    CONCURRENCY = "concurrency"
    IO = "io"
    CANCELLED = "cancelled"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class FinalizerDiagnostic:
    """Bounded secondary diagnostic that never replaces the primary cause."""

    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None


@dataclass(frozen=True, slots=True)
class FinalizerFailure:
    """Structured package-2F primary failure with preserved native context."""

    code: FinalizerErrorCode
    category: FinalizerErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None
    underlying: object | None = None
    retryable: bool = False
    cleanup_diagnostics: tuple[FinalizerDiagnostic, ...] = ()

    def with_cleanup(self, values: tuple[FinalizerDiagnostic, ...]) -> FinalizerFailure:
        """Return this failure with bounded secondary cleanup diagnostics."""
        return replace(self, cleanup_diagnostics=(*self.cleanup_diagnostics, *values)[:8])


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """Safe persistent-artifact reference without authority-bearing handles."""

    canonical_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class Finalized:
    """Successful result backed by a visible fully validated Sidecar 1.1."""

    sidecar: ArtifactLocation
    intent: ArtifactLocation | None
    receipt: ArtifactLocation | None
    state: ArtifactLocation | None
    recording_id: str
    source_identity: SourceIdentity
    target_generation: str | None
    idempotent: bool
    evidence_status: str = "complete"
    diagnostics: tuple[FinalizerDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalizationCancelled:
    """Cancellation result when cancellation linearized before sidecar commit."""

    error: FinalizerFailure


@dataclass(frozen=True, slots=True)
class FinalizationRejected:
    """Input, source-authority, or operational rejection before commit."""

    error: FinalizerFailure


@dataclass(frozen=True, slots=True)
class FinalizationConflict:
    """Target or recovery conflict that leaves existing artifacts unchanged."""

    error: FinalizerFailure
    committed_sidecar: ArtifactLocation | None = None


type FinalizationResult = (
    Finalized | FinalizationCancelled | FinalizationRejected | FinalizationConflict
)


def failure(
    code: FinalizerErrorCode,
    category: FinalizerErrorCategory,
    phase: str,
    message: str,
    *,
    win32_code: int | None = None,
    cause: BaseException | None = None,
    underlying: object | None = None,
    retryable: bool = False,
) -> FinalizerFailure:
    """Create one bounded structured finalizer failure."""
    return FinalizerFailure(
        code,
        category,
        phase,
        message[:1024],
        win32_code,
        cause,
        underlying,
        retryable,
    )
