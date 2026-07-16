"""Create-if-absent publication for canonical source-hash receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

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
    PathRole,
    SecureReadFailed,
    ValidatedPath,
    secure_read_file,
)
from matrix_auto_cutter.phase2.source_hash.contracts import (
    HashCompleted,
    HashDiagnostic,
    HashErrorCategory,
    HashErrorCode,
    HashFailure,
    _is_authentic_completed,
)
from matrix_auto_cutter.phase2.source_hash.receipt import (
    MAX_HASH_RECEIPT_BYTES,
    HashReceipt,
    hash_receipt_bytes,
    parse_hash_receipt_bytes,
)
from matrix_auto_cutter.phase2.win32_port import Win32Port


@dataclass(frozen=True, slots=True)
class HashReceiptPublished:
    """Receipt was newly published or an identical existing target was accepted."""

    status: Literal["published", "idempotent"]
    target: ValidatedPath
    receipt: HashReceipt
    cleanup_diagnostics: tuple[HashDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class HashReceiptPublishCancelled:
    """Cancellation linearized before create-if-absent publication."""

    error: HashFailure


@dataclass(frozen=True, slots=True)
class HashReceiptConflict:
    """An existing receipt target is not byte- and evidence-identical."""

    error: HashFailure


@dataclass(frozen=True, slots=True)
class HashReceiptPublishIoError:
    """Receipt I/O or post-validation failed without a successful artifact result."""

    error: HashFailure


HashReceiptPublishResult = (
    HashReceiptPublished
    | HashReceiptPublishCancelled
    | HashReceiptConflict
    | HashReceiptPublishIoError
)


def _diagnostics(details: tuple[ErrorDetail, ...]) -> tuple[HashDiagnostic, ...]:
    return tuple(
        HashDiagnostic(
            item.phase,
            item.message[:512],
            win32_code=item.win32_code,
            cause=item.cause,
        )
        for item in details[:8]
    )


def _failure_from_detail(
    detail: ErrorDetail,
    *,
    code: HashErrorCode = HashErrorCode.IO,
    category: HashErrorCategory = HashErrorCategory.IO,
    diagnostics: tuple[ErrorDetail, ...] = (),
) -> HashFailure:
    return HashFailure(
        code,
        category,
        "hash.receipt",
        detail.message[:512],
        win32_code=detail.win32_code,
        cause=detail.cause,
        underlying=detail,
        cleanup_diagnostics=_diagnostics(diagnostics),
    )


def _cancelled(diagnostics: tuple[ErrorDetail, ...] = ()) -> HashReceiptPublishCancelled:
    return HashReceiptPublishCancelled(
        HashFailure(
            HashErrorCode.CANCELLED,
            HashErrorCategory.CANCELLED,
            "hash.receipt",
            "hash receipt publication cancelled",
            retryable=True,
            cleanup_diagnostics=_diagnostics(diagnostics),
        )
    )


def _conflict(
    message: str,
    *,
    cause: BaseException | None = None,
    underlying: object | None = None,
    diagnostics: tuple[ErrorDetail, ...] = (),
) -> HashReceiptConflict:
    return HashReceiptConflict(
        HashFailure(
            HashErrorCode.RECEIPT_CONFLICT,
            HashErrorCategory.CONFLICT,
            "hash.receipt",
            message[:512],
            cause=cause,
            underlying=underlying,
            cleanup_diagnostics=_diagnostics(diagnostics),
        )
    )


def publish_hash_receipt(
    port: Win32Port,
    target: ValidatedPath,
    completed: HashCompleted,
    cancellation: CancellationToken,
    *,
    operation_id: UUID | None = None,
) -> HashReceiptPublishResult:
    """Publish an authentic completed hash receipt without replacing any target."""
    if not isinstance(completed, HashCompleted) or not _is_authentic_completed(completed):
        raise TypeError("an authentic HashCompleted value is required")
    if cancellation.is_cancelled:
        return _cancelled()
    if (
        target.role is not PathRole.WORKSPACE_INTERNAL
        or target.root_binding is None
        or target.canonical_dos_path.rpartition("\\")[2] != "hash-receipt.json"
    ):
        return HashReceiptPublishIoError(
            HashFailure(
                HashErrorCode.IO,
                HashErrorCategory.IO,
                "hash.receipt",
                "receipt target must be a validated internal hash-receipt.json path",
            )
        )
    receipt = completed.receipt
    data = hash_receipt_bytes(receipt)

    def validates(candidate: bytes) -> bool:
        try:
            return parse_hash_receipt_bytes(candidate) == receipt
        except (UnicodeError, ValueError):
            return False

    result = publish_initial(
        port,
        target,
        data,
        validates,
        cancellation,
        artifact="source-hash-receipt",
        operation_id=operation_id,
    )
    if isinstance(result, PublishOk):
        return HashReceiptPublished("published", target, receipt)
    if isinstance(result, PublishCancelled):
        return _cancelled(result.cleanup_diagnostics)
    if isinstance(result, PublishAlreadyExists):
        existing = secure_read_file(port, target, MAX_HASH_RECEIPT_BYTES)
        if isinstance(existing, SecureReadFailed):
            if existing.error.phase == "secure_read_size":
                return _conflict(
                    "existing hash receipt exceeds the bounded receipt contract",
                    underlying=existing.error,
                    diagnostics=result.cleanup_diagnostics,
                )
            return HashReceiptPublishIoError(
                _failure_from_detail(
                    existing.error,
                    diagnostics=(*result.cleanup_diagnostics, *existing.diagnostics)[:8],
                )
            )
        try:
            parsed = parse_hash_receipt_bytes(existing.data)
        except (UnicodeError, ValueError) as exc:
            return _conflict(
                "existing hash receipt is malformed or noncanonical",
                cause=exc,
                diagnostics=result.cleanup_diagnostics,
            )
        if existing.data == data and parsed == receipt:
            return HashReceiptPublished(
                "idempotent",
                target,
                receipt,
                _diagnostics(result.cleanup_diagnostics),
            )
        return _conflict(
            "existing hash receipt differs from the completed hash evidence",
            underlying=parsed,
            diagnostics=result.cleanup_diagnostics,
        )
    assert isinstance(result, AtomicPublishFailed | AtomicPublishIntegrity)
    return HashReceiptPublishIoError(
        _failure_from_detail(result.error, diagnostics=result.cleanup_diagnostics)
    )
