"""Bounded immutable artifact paths, reads, and create-if-absent publication."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    PublishAlreadyExists,
    PublishCancelled,
    PublishOk,
    publish_initial,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorDetail
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    SecureReadFailed,
    ValidatedPath,
    ensure_directory_tree,
    secure_read_file,
    validate_path,
)
from matrix_auto_cutter.phase2.source_confirmation.contracts import (
    ConfirmationDiagnostic,
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import ArtifactReference
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.phase2.workspace import ProjectCapability


@dataclass(frozen=True, slots=True)
class ArtifactPublished:
    """New or identical fully validated immutable artifact."""

    status: Literal["published", "idempotent"]
    target: ValidatedPath
    reference: ArtifactReference


@dataclass(frozen=True, slots=True)
class ArtifactPublishCancelled:
    """Cancellation won before the immutable rename commit."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class ArtifactConflict:
    """Existing target is malformed, oversized, or byte-different."""

    error: ConfirmationFailure


@dataclass(frozen=True, slots=True)
class ArtifactIoFailure:
    """Path, secure-read, or atomic-publish operation failed."""

    error: ConfirmationFailure


type ArtifactPublishResult = (
    ArtifactPublished | ArtifactPublishCancelled | ArtifactConflict | ArtifactIoFailure
)


def _diagnostics(details: tuple[ErrorDetail, ...]) -> tuple[ConfirmationDiagnostic, ...]:
    return tuple(
        ConfirmationDiagnostic(item.phase, item.message[:512], item.win32_code, item.cause)
        for item in details[:8]
    )


def _from_detail(
    detail: ErrorDetail,
    *,
    diagnostics: tuple[ErrorDetail, ...] = (),
) -> ConfirmationFailure:
    return ConfirmationFailure(
        ConfirmationErrorCode.IO,
        ConfirmationErrorCategory.IO,
        detail.phase,
        detail.message[:512],
        win32_code=detail.win32_code,
        cause=detail.cause,
        underlying=detail,
        cleanup_diagnostics=_diagnostics(diagnostics),
    )


def artifact_target(
    port: Win32Port,
    project: ProjectCapability,
    directories: tuple[str, ...],
    filename: str,
) -> ValidatedPath | ConfirmationFailure:
    """Create and revalidate only the requested closed project subdirectory tree."""
    if not project.trusted:
        return ConfirmationFailure(
            ConfirmationErrorCode.INVALID_INPUT,
            ConfirmationErrorCategory.INTEGRITY,
            "artifact.project",
            "project capability is no longer trusted",
        )
    base = project.project_directory.canonical_dos_path
    directory = base + "\\" + "\\".join(directories)
    ensured = ensure_directory_tree(port, directory)
    if isinstance(ensured, PathRejected):
        return _from_detail(ensured.error, diagnostics=ensured.diagnostics)
    target = validate_path(
        port,
        directory + "\\" + filename,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=project.workspace.root,
    )
    if isinstance(target, PathRejected):
        return _from_detail(target.error, diagnostics=target.diagnostics)
    return target.path


def publish_artifact[ModelT: CanonicalModel](
    port: Win32Port,
    target: ValidatedPath,
    model: ModelT,
    maximum_bytes: int,
    parser: Callable[[bytes], ModelT],
    cancellation: CancellationToken,
    *,
    artifact_name: str,
    artifact_id: str,
    artifact_type: Literal["media_probe", "stream_assignment", "source_identity_evidence"],
) -> ArtifactPublishResult:
    """Publish canonical bytes and accept an existing target only after bounded validation."""
    data = canonical_bytes(model)
    if len(data) > maximum_bytes:
        return ArtifactIoFailure(
            ConfirmationFailure(
                ConfirmationErrorCode.INTEGRITY,
                ConfirmationErrorCategory.INTEGRITY,
                f"{artifact_name}.size",
                f"{artifact_name} exceeds its bounded artifact limit",
            )
        )

    def validates(candidate: bytes) -> bool:
        try:
            return candidate == data and parser(candidate) == model
        except (UnicodeError, ValueError):
            return False

    result = publish_initial(
        port,
        target,
        data,
        validates,
        cancellation,
        artifact=artifact_name,
    )
    if isinstance(result, PublishOk):
        return ArtifactPublished(
            "published",
            target,
            ArtifactReference(
                artifact_type=artifact_type,
                artifact_id=artifact_id,
                artifact_digest=hashlib.sha256(data).hexdigest(),
                canonical_path=target.canonical_dos_path,
            ),
        )
    if isinstance(result, PublishCancelled):
        return ArtifactPublishCancelled(
            ConfirmationFailure(
                ConfirmationErrorCode.CANCELLED,
                ConfirmationErrorCategory.CANCELLED,
                f"{artifact_name}.publish",
                f"{artifact_name} publication cancelled",
                underlying=result.error,
                retryable=True,
                cleanup_diagnostics=_diagnostics(result.cleanup_diagnostics),
            )
        )
    if isinstance(result, PublishAlreadyExists):
        existing = secure_read_file(port, target, maximum_bytes)
        if isinstance(existing, SecureReadFailed):
            if existing.error.phase == "secure_read_size":
                return ArtifactConflict(
                    ConfirmationFailure(
                        ConfirmationErrorCode.ARTIFACT_CONFLICT,
                        ConfirmationErrorCategory.CONFLICT,
                        f"{artifact_name}.existing",
                        f"existing {artifact_name} exceeds its bounded contract",
                        underlying=existing.error,
                        cleanup_diagnostics=_diagnostics(result.cleanup_diagnostics),
                    )
                )
            return ArtifactIoFailure(
                _from_detail(
                    existing.error,
                    diagnostics=(*result.cleanup_diagnostics, *existing.diagnostics)[:8],
                )
            )
        try:
            parsed = parser(existing.data)
        except (UnicodeError, ValueError) as exc:
            return ArtifactConflict(
                ConfirmationFailure(
                    ConfirmationErrorCode.ARTIFACT_CONFLICT,
                    ConfirmationErrorCategory.CONFLICT,
                    f"{artifact_name}.existing",
                    f"existing {artifact_name} is malformed or noncanonical",
                    cause=exc,
                    cleanup_diagnostics=_diagnostics(result.cleanup_diagnostics),
                )
            )
        if existing.data == data and parsed == model:
            return ArtifactPublished(
                "idempotent",
                target,
                ArtifactReference(
                    artifact_type=artifact_type,
                    artifact_id=artifact_id,
                    artifact_digest=hashlib.sha256(data).hexdigest(),
                    canonical_path=target.canonical_dos_path,
                ),
            )
        return ArtifactConflict(
            ConfirmationFailure(
                ConfirmationErrorCode.ARTIFACT_CONFLICT,
                ConfirmationErrorCategory.CONFLICT,
                f"{artifact_name}.existing",
                f"existing {artifact_name} differs from the current run",
                underlying=parsed,
                cleanup_diagnostics=_diagnostics(result.cleanup_diagnostics),
            )
        )
    assert isinstance(result, AtomicPublishFailed | AtomicPublishIntegrity)
    return ArtifactIoFailure(_from_detail(result.error, diagnostics=result.cleanup_diagnostics))


def read_artifact[ModelT: CanonicalModel](
    port: Win32Port,
    project: ProjectCapability,
    reference: ArtifactReference,
    maximum_bytes: int,
    parser: Callable[[bytes], ModelT],
) -> ModelT | ConfirmationFailure:
    """Boundedly read and digest-check one referenced internal immutable artifact."""
    checked = validate_path(
        port,
        reference.canonical_path,
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=project.workspace.root,
    )
    if isinstance(checked, PathRejected):
        return _from_detail(checked.error, diagnostics=checked.diagnostics)
    read = secure_read_file(port, checked.path, maximum_bytes)
    if isinstance(read, SecureReadFailed):
        return _from_detail(read.error, diagnostics=read.diagnostics)
    if hashlib.sha256(read.data).hexdigest() != reference.artifact_digest:
        return ConfirmationFailure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "artifact.digest",
            "artifact bytes do not match the bound reference digest",
        )
    try:
        return parser(read.data)
    except (UnicodeError, ValueError) as exc:
        return ConfirmationFailure(
            ConfirmationErrorCode.INTEGRITY,
            ConfirmationErrorCategory.INTEGRITY,
            "artifact.parse",
            "artifact bytes are malformed, noncanonical, or unsupported",
            cause=exc,
        )
