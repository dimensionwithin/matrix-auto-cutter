"""Lease-bound bounded SHA-256 hashing with exact EOF and S4 proof."""

from __future__ import annotations

import hashlib

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, is_canonical_uuid4
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateLease,
    RecheckCancelled,
    RecheckOk,
    RecheckUnstable,
)
from matrix_auto_cutter.phase2.close_gate.lease import (
    _LeaseIoSession,
    _LeaseIoUnavailable,
    _run_lease_io,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    compare_snapshots,
)
from matrix_auto_cutter.phase2.source_hash.contracts import (
    HashCancelled,
    HashErrorCategory,
    HashErrorCode,
    HashFailure,
    HashIoError,
    HashResult,
    HashUnexpectedEof,
    SourceChanged,
    _issue_hash_completed,
)
from matrix_auto_cutter.phase2.source_hash.receipt import HashReceipt
from matrix_auto_cutter.phase2.win32_port import Win32Err, Win32Failure

PRODUCTION_BLOCK_SIZE_BYTES = 8 * 1024 * 1024
EOF_PROBE_BYTES = 1


def _cancelled(phase: str) -> HashCancelled:
    return HashCancelled(
        HashFailure(
            HashErrorCode.CANCELLED,
            HashErrorCategory.CANCELLED,
            phase,
            "lease-bound hash cancelled",
            retryable=True,
        )
    )


def _io(
    phase: str,
    message: str,
    *,
    cause: BaseException | None = None,
    underlying: object | None = None,
    win32_code: int | None = None,
) -> HashIoError:
    return HashIoError(
        HashFailure(
            HashErrorCode.IO,
            HashErrorCategory.IO,
            phase,
            message[:512],
            win32_code=win32_code,
            cause=cause,
            underlying=underlying,
        )
    )


def _native_io(phase: str, error: Win32Failure) -> HashIoError:
    cause = OSError(error.code, error.detail)
    return _io(
        phase,
        error.detail,
        cause=cause,
        underlying=error,
        win32_code=error.code,
    )


def _unexpected_eof(bytes_read: int, expected: int) -> HashUnexpectedEof:
    return HashUnexpectedEof(
        HashFailure(
            HashErrorCode.UNEXPECTED_EOF,
            HashErrorCategory.INTEGRITY,
            "hash.read",
            "source reached EOF before the S0-bound end offset",
            underlying={"bytes_read": bytes_read, "s0_size_bytes": expected},
            retryable=True,
        )
    )


def _source_changed(phase: str, reason: str, underlying: object | None = None) -> SourceChanged:
    return SourceChanged(
        HashFailure(
            HashErrorCode.SOURCE_CHANGED,
            HashErrorCategory.INTEGRITY,
            phase,
            reason,
            underlying=underlying,
            retryable=True,
        )
    )


def _validate_hash_bindings(
    lease: CloseGateLease,
    project_id: str,
    hash_run_id: str,
) -> HashIoError | None:
    if not is_canonical_uuid4(project_id) or not is_canonical_uuid4(hash_run_id):
        return _io("hash.lease", "project and hash-run IDs must be canonical UUIDv4")
    s0 = lease.s0
    if (
        not isinstance(s0.size_bytes, int)
        or isinstance(s0.size_bytes, bool)
        or s0.size_bytes < 0
        or s0.evidence_version != "file_snapshot/1.0"
    ):
        return _io("hash.lease", "S0 size or snapshot version is invalid")
    if not isinstance(s0.volume_id, AvailableIdentity) or not isinstance(
        s0.file_id, AvailableIdentity
    ):
        return _io("hash.lease", "S0 lacks volume/file identity evidence")
    if (
        s0.volume_id.scheme != "ntfs_volume_serial"
        or s0.file_id.scheme != "file_id_128"
        or s0.volume_id.value != lease.volume_id
        or s0.file_id.value != lease.file_id
        or s0.file_id.scheme != lease.file_id_scheme
        or lease.lease_id != lease.validation_epoch
    ):
        return _io("hash.lease", "S0 identity is inconsistent with the lease epoch")
    comparison = compare_snapshots(s0, s0)
    if not isinstance(comparison, SameInstanceUnchanged):
        return _io(
            "hash.lease", "S0 snapshot evidence failed self-validation", underlying=comparison
        )
    return None


def _hash_in_session(
    session: _LeaseIoSession,
    lease: CloseGateLease,
    cancellation: CancellationToken,
    project_id: str,
    hash_run_id: str,
    block_size_bytes: int,
) -> HashResult:
    if cancellation.is_cancelled:
        return _cancelled("hash.lease")
    binding_error = _validate_hash_bindings(lease, project_id, hash_run_id)
    if binding_error is not None:
        return binding_error
    s0 = lease.s0
    if cancellation.is_cancelled:
        return _cancelled("hash.position")
    positioned = session.position(0)
    if isinstance(positioned, Win32Err):
        return _native_io("hash.position", positioned.error)
    if positioned.value != 0:
        return _io("hash.position", "adapter did not establish byte offset zero")

    digest = hashlib.sha256()
    bytes_read = 0
    while bytes_read < s0.size_bytes:
        if cancellation.is_cancelled:
            return _cancelled("hash.read")
        requested = min(block_size_bytes, s0.size_bytes - bytes_read)
        read = session.read(requested)
        if isinstance(read, Win32Err):
            return _native_io("hash.read", read.error)
        chunk = read.value
        if not isinstance(chunk, bytes):
            return _io("hash.read", "adapter returned a non-bytes read value")
        if len(chunk) > requested:
            return _io("hash.read", "adapter returned more bytes than requested")
        if not chunk:
            return _unexpected_eof(bytes_read, s0.size_bytes)
        digest.update(chunk)
        bytes_read += len(chunk)
        if cancellation.is_cancelled:
            return _cancelled("hash.read")

    if cancellation.is_cancelled:
        return _cancelled("hash.eof")
    eof = session.read(EOF_PROBE_BYTES)
    if isinstance(eof, Win32Err):
        return _native_io("hash.eof", eof.error)
    if not isinstance(eof.value, bytes) or len(eof.value) > EOF_PROBE_BYTES:
        return _io("hash.eof", "adapter returned a malformed EOF probe")
    if cancellation.is_cancelled:
        return _cancelled("hash.eof")
    if eof.value:
        return _source_changed("hash.eof", "source contains bytes beyond the S0-bound end")

    if cancellation.is_cancelled:
        return _cancelled("hash.s4")
    rechecked = session.recheck(cancellation)
    if isinstance(rechecked, RecheckCancelled):
        return _cancelled("hash.s4")
    if isinstance(rechecked, RecheckUnstable):
        return _source_changed("hash.s4", "S4 proves changed source evidence", rechecked)
    if not isinstance(rechecked, RecheckOk):
        error = getattr(rechecked, "error", None)
        return _io("hash.s4", "lease S4 recheck failed", underlying=error)
    s4 = rechecked.snapshot
    comparison = compare_snapshots(s0, s4)
    if isinstance(comparison, SameInstanceChanged | DifferentInstance):
        return _source_changed("hash.s4", "S4 differs from S0", comparison)
    if isinstance(comparison, NotComparable | ComparisonFailed):
        return _io("hash.s4", "S4 cannot prove unchanged S0 evidence", underlying=comparison)
    assert isinstance(comparison, SameInstanceUnchanged)
    if cancellation.is_cancelled:
        return _cancelled("hash.commit")

    receipt = HashReceipt(
        project_id=project_id,
        hash_run_id=hash_run_id,
        lease_id=str(lease.lease_id),
        validation_epoch=str(lease.validation_epoch),
        s0_snapshot_key=s0.snapshot_key,
        s4_snapshot_key=s4.snapshot_key,
        s0_size_bytes=s0.size_bytes,
        bytes_read=bytes_read,
        volume_id=lease.volume_id,
        file_id=lease.file_id,
        file_id_scheme="file_id_128",
        block_size_bytes=block_size_bytes,
        sha256=digest.hexdigest(),
    )
    if not session.commit(cancellation):
        return (
            _cancelled("hash.commit")
            if cancellation.is_cancelled
            else _io("hash.commit", "lease close linearized before hash publication")
        )
    return _issue_hash_completed(receipt, s0, s4)


def hash_lease_source(
    lease: CloseGateLease,
    cancellation: CancellationToken,
    project_id: str,
    hash_run_id: str,
    *,
    block_size_bytes: int = PRODUCTION_BLOCK_SIZE_BYTES,
) -> HashResult:
    """Hash exactly S0 bytes over one authenticated exclusive lease I/O session."""
    if cancellation.is_cancelled:
        return _cancelled("hash.lease")
    if not isinstance(lease, CloseGateLease):
        return _io("hash.lease", "an authentic CloseGateLease is required")
    if (
        not isinstance(block_size_bytes, int)
        or isinstance(block_size_bytes, bool)
        or not 0 < block_size_bytes <= PRODUCTION_BLOCK_SIZE_BYTES
    ):
        return _io("hash.lease", "block size must be between 1 byte and 8 MiB")

    result = _run_lease_io(
        lease,
        cancellation,
        lambda session: _hash_in_session(
            session,
            lease,
            cancellation,
            project_id,
            hash_run_id,
            block_size_bytes,
        ),
    )
    if isinstance(result, _LeaseIoUnavailable):
        if result.reason == "cancelled":
            return _cancelled("hash.lease")
        return _io("hash.lease", f"lease I/O session unavailable: {result.reason}")
    return result
