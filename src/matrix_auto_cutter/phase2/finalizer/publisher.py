"""No-replace Sidecar 1.1 publisher and complete target validator."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal

from matrix_auto_cutter.models import SourceIdentity
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.atomic_project import (
    AtomicPublishFailed,
    AtomicPublishIntegrity,
    PublishAlreadyExists,
    PublishCancelled,
    PublishOk,
    publish_external_create_if_absent,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import RecheckOk
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, ErrorDetail
from matrix_auto_cutter.phase2.errors import failure as phase2_failure
from matrix_auto_cutter.phase2.finalizer.errors import (
    ArtifactLocation,
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerFailure,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.models import MAX_SIDECAR_BYTES, FinalizationIntent
from matrix_auto_cutter.phase2.pathing import SecureReadFailed, ValidatedPath, secure_read_file
from matrix_auto_cutter.phase2.source_confirmation.capability import _ConfirmedSourceUsage
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.sidecar import ObsEventSidecar, validate_sidecar


@dataclass(frozen=True, slots=True)
class TargetMissing:
    """Evidence that the expected sidecar target is absent."""

    pass


@dataclass(frozen=True, slots=True)
class TargetValid:
    """Fully validated visible Sidecar-1.1 commit evidence."""

    sidecar: ObsEventSidecar
    location: ArtifactLocation
    volume_serial: int
    file_id_128: bytes | None


@dataclass(frozen=True, slots=True)
class TargetInvalid:
    """Evidence that an existing target cannot establish this commit."""

    error: FinalizerFailure


type TargetValidation = TargetMissing | TargetValid | TargetInvalid


@dataclass(frozen=True, slots=True)
class SidecarPublished:
    """Successful visible and revalidated Sidecar 1.1 publication."""

    location: ArtifactLocation
    idempotent: bool


@dataclass(frozen=True, slots=True)
class SidecarPublishFailed:
    """No-replace publication failure with optional observed commit evidence."""

    error: FinalizerFailure
    committed: TargetValid | None = None


type SidecarPublishResult = SidecarPublished | SidecarPublishFailed


def sidecar_bytes(sidecar: ObsEventSidecar) -> bytes:
    """Return bounded canonical Sidecar-1.1 bytes with final LF."""
    data = canonical_bytes(sidecar)
    if len(data) > MAX_SIDECAR_BYTES:
        raise ValueError("sidecar exceeds its bounded size contract")
    return data


def read_committed_sidecar(
    port: Win32Port,
    target: ValidatedPath,
    expected_source: SourceIdentity | None = None,
) -> TargetValidation:
    """Recognize commit only from a bounded, fully Phase-1-valid target."""
    read = secure_read_file(port, target, MAX_SIDECAR_BYTES)
    if isinstance(read, SecureReadFailed):
        if read.error.win32_code in {2, 3}:
            return TargetMissing()
        return TargetInvalid(
            failure(
                FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                FinalizerErrorCategory.IO,
                "target.read",
                read.error.message,
                win32_code=read.error.win32_code,
                underlying=read.error,
            )
        )
    if read.file_info.number_of_links != 1:
        return TargetInvalid(
            failure(
                FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                FinalizerErrorCategory.INTEGRITY,
                "target.hardlink",
                "sidecar target has unavailable or unexpected hardlink count",
            )
        )
    try:
        if read.data.startswith(b"\xef\xbb\xbf") or not read.data.endswith(b"\n"):
            raise ValueError("sidecar is not canonical UTF-8 with final LF")
        sidecar = ObsEventSidecar.model_validate_json(read.data.decode("utf-8", errors="strict"))
        if sidecar_bytes(sidecar) != read.data:
            raise ValueError("sidecar bytes are not canonical")
    except (UnicodeError, ValueError) as exc:
        return TargetInvalid(
            failure(
                FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                FinalizerErrorCategory.INTEGRITY,
                "target.parse",
                str(exc),
                cause=exc,
            )
        )
    evidence = expected_source if expected_source is not None else sidecar.source
    phase1_payload = json.loads(sidecar.model_dump_json(), parse_float=Decimal)
    phase1 = validate_sidecar(phase1_payload, evidence)
    if phase1.mode != "validated_sidecar_1_1" or phase1.sidecar != sidecar:
        return TargetInvalid(
            failure(
                FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                FinalizerErrorCategory.INTEGRITY,
                "target.phase1_validation",
                "existing target is not a valid source-identical Sidecar 1.1",
                underlying=phase1,
            )
        )
    return TargetValid(
        sidecar,
        ArtifactLocation(
            target.canonical_dos_path,
            hashlib.sha256(read.data).hexdigest(),
            len(read.data),
        ),
        read.file_info.volume_serial,
        read.file_info.file_id_128,
    )


def validate_target(
    port: Win32Port,
    target: ValidatedPath,
    intent: FinalizationIntent | None,
    expected: ObsEventSidecar | None,
) -> TargetValidation:
    """Bounded-open and fully Phase-1-validate one expected sidecar target."""
    observed = read_committed_sidecar(
        port,
        target,
        intent.source_identity if intent is not None else expected.source if expected else None,
    )
    if not isinstance(observed, TargetValid):
        return observed
    if intent is not None:
        file_id = observed.file_id_128
        if (
            file_id is not None
            and observed.volume_serial == int(intent.source_volume_id, 16)
            and file_id.hex() == intent.source_file_id
        ):
            return TargetInvalid(
                failure(
                    FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                    FinalizerErrorCategory.INTEGRITY,
                    "target.source_alias",
                    "sidecar target aliases the source instance",
                )
            )
    if expected is not None and observed.sidecar != expected:
        return TargetInvalid(
            failure(
                FinalizerErrorCode.TARGET_ALREADY_EXISTS,
                FinalizerErrorCategory.INTEGRITY,
                "target.intent_identity",
                "existing sidecar is not semantically identical to the persisted intent",
            )
        )
    return observed


def publish_sidecar(
    port: Win32Port,
    target: ValidatedPath,
    temp: ValidatedPath,
    intent_path: ValidatedPath,
    intent_data: bytes,
    intent: FinalizationIntent,
    sidecar: ObsEventSidecar,
    usage: _ConfirmedSourceUsage,
    cancellation: CancellationToken,
) -> SidecarPublishResult:
    """Publish with one lease-aware cancellation/rename linearization."""
    data = sidecar_bytes(sidecar)

    def validates(candidate: bytes) -> bool:
        validated = validate_target(port, target, intent, sidecar)
        return isinstance(validated, TargetValid) and candidate == data

    def commit() -> ErrorDetail | None:
        intent_read = secure_read_file(port, intent_path, len(intent_data))
        if isinstance(intent_read, SecureReadFailed) or intent_read.data != intent_data:
            return phase2_failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "sidecar.intent_revalidation",
                "persisted finalization intent changed or became unreadable",
            )
        target_before = validate_target(port, target, intent, sidecar)
        if not isinstance(target_before, TargetMissing):
            return phase2_failure(
                ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "sidecar.target_revalidation",
                "target appeared before the no-replace commit",
            )
        recheck = usage.recheck(cancellation)
        if not isinstance(recheck, RecheckOk):
            return phase2_failure(
                (
                    ErrorCode.CANCELLED
                    if cancellation.is_cancelled
                    else ErrorCode.ATOMIC_PUBLISH_INTEGRITY
                ),
                (ErrorCategory.CANCELLED if cancellation.is_cancelled else ErrorCategory.INTEGRITY),
                "sidecar.source_revalidation",
                "confirmed source recheck failed immediately before commit",
            )
        if not usage.commit(cancellation):
            return phase2_failure(
                ErrorCode.CANCELLED
                if cancellation.is_cancelled
                else ErrorCode.ATOMIC_PUBLISH_INTEGRITY,
                (ErrorCategory.CANCELLED if cancellation.is_cancelled else ErrorCategory.INTEGRITY),
                "sidecar.commit_linearization",
                "cancellation or lease close linearized before sidecar commit",
            )
        return None

    primitive = publish_external_create_if_absent(
        port,
        target,
        temp,
        data,
        validates,
        commit,
        owned_temp_suffix=f".tmp.{intent.finalizer_run_id}",
        cancellation=cancellation,
    )
    observed = validate_target(port, target, intent, sidecar)
    if isinstance(observed, TargetValid):
        return SidecarPublished(observed.location, not isinstance(primitive, PublishOk))
    if isinstance(primitive, PublishCancelled):
        code = FinalizerErrorCode.CANCELLED
        category = FinalizerErrorCategory.CANCELLED
    elif isinstance(primitive, PublishAlreadyExists | AtomicPublishIntegrity):
        code = FinalizerErrorCode.TARGET_ALREADY_EXISTS
        category = FinalizerErrorCategory.INTEGRITY
    else:
        assert isinstance(primitive, AtomicPublishFailed)
        code = FinalizerErrorCode.ATOMIC_PUBLISH_FAILED
        category = FinalizerErrorCategory.IO
    detail = observed.error if isinstance(observed, TargetInvalid) else None
    return SidecarPublishFailed(
        failure(
            code,
            category,
            "sidecar.publish",
            detail.message if detail is not None else primitive.error.message,
            win32_code=primitive.error.win32_code,
            underlying=primitive,
        )
    )
