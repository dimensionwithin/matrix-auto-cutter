"""Secure ffprobe binary inspection, hashing and validation."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, SupportsIndex, cast

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, UnavailableIdentity
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.pathing import PathRejected, PathRole, ValidatedPath, validate_path
from matrix_auto_cutter.phase2.probe.contracts import (
    BINARY_VALIDATION_CONTRACT_VERSION,
    BinaryEvidence,
    FfprobeCandidate,
    FfprobeVersion,
    ValidatedFfprobeBinary,
)
from matrix_auto_cutter.phase2.probe.errors import ProbeError, ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.probe.process_port import (
    VERSION_OUTPUT_LIMIT,
    ProbeProcessOk,
    ProcessPort,
    ProcessSpec,
)
from matrix_auto_cutter.phase2.probe.supported_versions import (
    PRODUCT_FFPROBE_SUPPORT_POLICY,
)
from matrix_auto_cutter.phase2.probe.versioning import (
    VersionParsed,
    VersionSupported,
    evaluate_ffprobe_support,
    parse_ffprobe_version,
)
from matrix_auto_cutter.phase2.snapshots import (
    FileSnapshot,
    FileTime,
    SameInstanceUnchanged,
    compare_snapshots,
)
from matrix_auto_cutter.phase2.win32_port import (
    DRIVE_FIXED,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ,
    FILE_TYPE_DISK,
    GENERIC_READ,
    OPEN_EXISTING,
    OwnedHandle,
    RawFileInfo,
    Win32Err,
    Win32Port,
)

HASH_CHUNK_BYTES = 8 * 1024 * 1024
MAX_BINARY_BYTES = 1024 * 1024 * 1024


def _capability_payload(binary: ValidatedFfprobeBinary) -> bytes:
    """Return a complete deterministic binding for every security-relevant slot."""
    values = (
        binary.path,
        binary.canonical_dos_path,
        binary.long_path,
        binary.volume_id,
        binary.file_id,
        binary.size_bytes,
        binary.creation_time,
        binary.last_write_time,
        binary.change_time,
        binary.sha256,
        binary.raw_version_output,
        binary.version,
        binary.version_stderr_output,
        binary.support_policy_revision,
        binary.support_policy_type,
        binary.support_policy_digest,
        binary.validation_contract_version,
        binary.validated_at_utc,
        binary.original_snapshot,
        id(binary),
    )
    return repr(values).encode("utf-8", errors="strict")


class _InspectionCloseState(StrEnum):
    OPEN = "open"
    CLOSE_CONFIRMED = "close_confirmed"
    OWNERSHIP_UNRESOLVED = "ownership_unresolved"


class _BinaryInspectionView(Protocol):
    """Structural internal view of a lexically issued inspection owner."""

    evidence: BinaryEvidence
    _handle: OwnedHandle
    _close_state: _InspectionCloseState

    def close(self) -> ProbeError | None: ...


class _NativeBinaryTrustPortView(Protocol):
    """Structural constructor surface of the concrete native inspection port."""

    def __init__(self, port: Win32Port) -> None: ...

    def inspect(self, path: ValidatedPath) -> BinaryInspectionResult: ...


def _build_binary_inspection_boundary() -> tuple[
    type[_BinaryInspectionView], type[_NativeBinaryTrustPortView]
]:
    """Build the sole inspection owner and its concrete lexical issuer."""
    issuer_key = object()

    class BinaryInspection:
        """Exclusive owner of evidence plus one restrictive verification handle."""

        __slots__ = ("_close_state", "_handle", "evidence")

        evidence: BinaryEvidence
        _handle: OwnedHandle
        _close_state: _InspectionCloseState

        def __new__(
            cls,
            evidence: BinaryEvidence,
            handle: OwnedHandle,
            *,
            _issuer_key: object | None = None,
        ) -> BinaryInspection:
            del evidence, handle
            if cls is not BinaryInspection or _issuer_key is not issuer_key:
                raise TypeError("binary inspections are issued only by native inspection")
            return super().__new__(cls)

        def __init__(
            self,
            evidence: BinaryEvidence,
            handle: OwnedHandle,
            *,
            _issuer_key: object | None = None,
        ) -> None:
            """Bind evidence and handle only for the lexical native issuer."""
            if _issuer_key is not issuer_key:
                raise TypeError("binary inspections are issued only by native inspection")
            self.evidence = evidence
            self._handle = handle
            self._close_state = _InspectionCloseState.OPEN

        def __init_subclass__(cls, **kwargs: object) -> None:
            del cls, kwargs
            raise TypeError("binary inspections cannot be subclassed")

        def __copy__(self) -> BinaryInspection:
            """Reject shallow copies that would alias exclusive handle ownership."""
            raise TypeError("binary inspections cannot be copied")

        def __deepcopy__(self, memo: dict[int, object]) -> BinaryInspection:
            """Reject deep copies that would reconstruct exclusive handle ownership."""
            del memo
            raise TypeError("binary inspections cannot be copied")

        def __getstate__(self) -> object:
            """Reject slot-state export that would disclose the owned handle."""
            raise TypeError("binary inspection state cannot be exported")

        def __setstate__(self, state: object) -> None:
            """Reject state restoration as an alternate owner constructor."""
            del state
            raise TypeError("binary inspection state cannot be restored")

        def __reduce__(self) -> str | tuple[object, ...]:
            """Reject legacy pickle reconstruction."""
            raise TypeError("binary inspections cannot be serialized")

        def __reduce_ex__(self, protocol: SupportsIndex) -> str | tuple[object, ...]:
            """Reject pickle and copy-protocol reconstruction of this owner wrapper."""
            del protocol
            raise TypeError("binary inspections cannot be serialized")

        def __getnewargs__(self) -> tuple[object, ...]:
            """Reject positional reconstruction arguments."""
            raise TypeError("binary inspections cannot be reconstructed")

        def __getnewargs_ex__(self) -> tuple[tuple[object, ...], dict[str, object]]:
            """Reject keyword reconstruction arguments."""
            raise TypeError("binary inspections cannot be reconstructed")

        def close(self) -> ProbeError | None:
            """Attempt release once and retain an explicit confirmed/unresolved state."""
            if self._close_state is not _InspectionCloseState.OPEN:
                raise RuntimeError("binary inspection handle close was already attempted")
            try:
                closed = self._handle.close()
            except BaseException:
                self._close_state = _InspectionCloseState.OWNERSHIP_UNRESOLVED
                raise
            if isinstance(closed, Win32Err):
                self._close_state = _InspectionCloseState.OWNERSHIP_UNRESOLVED
                return probe_error(
                    ProbeErrorCode.BINARY_ACCESS,
                    ErrorCategory.IO,
                    "binary_handle_close",
                    closed.error.detail,
                    win32_code=closed.error.code,
                )
            self._close_state = _InspectionCloseState.CLOSE_CONFIRMED
            return None

    class NativeBinaryTrustPort:
        """Safe binary reader and sole issuer of inspection owner wrappers."""

        def __init__(self, port: Win32Port) -> None:
            """Bind one Win32 adapter."""
            self._port = port

        def inspect(self, path: ValidatedPath) -> BinaryInspectionResult:
            """Validate, restrictively open, snapshot and fully hash one binary."""
            checked = validate_path(
                self._port,
                path.canonical_dos_path,
                PathRole.EXTERNAL_SOURCE_READ_ONLY,
                require_existing=True,
                require_regular_file=True,
            )
            if isinstance(checked, PathRejected):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_ACCESS,
                        checked.error.category,
                        "binary_path_validation",
                        checked.error.message,
                        win32_code=checked.error.win32_code,
                        cause=checked.error.cause,
                    )
                )
            opened = self._port.open_file(
                path.long_path,
                GENERIC_READ,
                FILE_SHARE_READ,
                OPEN_EXISTING,
                FILE_FLAG_OPEN_REPARSE_POINT,
            )
            if isinstance(opened, Win32Err):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_ACCESS,
                        ErrorCategory.ACCESS,
                        "binary_open",
                        opened.error.detail,
                        win32_code=opened.error.code,
                    )
                )
            handle = opened.value
            transferred = False
            failure: BinaryInspectionFailed | None = None
            inspection: BinaryInspection | None = None
            try:
                result = self._inspect_open(path, handle)
                if isinstance(result, BinaryInspectionFailed):
                    failure = result
                else:
                    if not isinstance(result, BinaryEvidence):
                        raise TypeError("binary inspection helper returned an invalid result")
                    inspection = BinaryInspection(result, handle, _issuer_key=issuer_key)
                    transferred = True
            finally:
                if not transferred:
                    active = sys.exception()
                    close_error = _close_local_handle_once(handle, active)
                    if active is None:
                        assert failure is not None
                        failure = BinaryInspectionFailed(
                            _with_secondary(failure.error, close_error)
                        )
                    else:
                        assert close_error is None
            if failure is not None:
                return failure
            assert inspection is not None
            return inspection

        def _inspect_open(
            self, path: ValidatedPath, handle: OwnedHandle
        ) -> BinaryEvidence | BinaryInspectionFailed:
            """Return verified evidence only; this helper never transfers handle ownership."""
            before = self._port.query_file_info(handle)
            if isinstance(before, Win32Err):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.IO,
                        "binary_snapshot_before",
                        before.error.detail,
                        win32_code=before.error.code,
                    )
                )
            info = before.value
            if (
                info.attributes & FILE_ATTRIBUTE_REPARSE_POINT
                or info.file_type != FILE_TYPE_DISK
                or info.is_directory
                or info.filesystem_name != "NTFS"
                or info.drive_type != DRIVE_FIXED
            ):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.POLICY,
                        "binary_snapshot_before",
                        "regular local NTFS binary evidence is required",
                    )
                )
            if info.file_id_128 is None:
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.INTEGRITY,
                        "binary_snapshot_before",
                        "FILE_ID_128 is required to prove binary instance binding",
                    )
                )
            if info.size_bytes < 1 or info.size_bytes > MAX_BINARY_BYTES:
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.POLICY,
                        "binary_snapshot_before",
                        "binary size is outside the finite validation contract",
                    )
                )
            expected = self._port.ordinal_case_key(path.canonical_dos_path)
            actual_text = (
                info.final_dos_path[4:]
                if info.final_dos_path.startswith("\\\\?\\")
                else info.final_dos_path
            )
            actual = self._port.ordinal_case_key(actual_text)
            if isinstance(expected, Win32Err) or isinstance(actual, Win32Err):
                failure_value = expected if isinstance(expected, Win32Err) else actual
                assert isinstance(failure_value, Win32Err)
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.IO,
                        "binary_final_path",
                        failure_value.error.detail,
                        win32_code=failure_value.error.code,
                    )
                )
            if expected.value != actual.value:
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_CHANGED,
                        ErrorCategory.INTEGRITY,
                        "binary_final_path",
                        "binary final path no longer matches the validated DOS path",
                    )
                )
            digest = hashlib.sha256()
            remaining = info.size_bytes
            while remaining:
                chunk = self._port.read_file(handle, min(HASH_CHUNK_BYTES, remaining))
                if isinstance(chunk, Win32Err):
                    return BinaryInspectionFailed(
                        probe_error(
                            ProbeErrorCode.BINARY_HASH,
                            ErrorCategory.IO,
                            "binary_hash",
                            chunk.error.detail,
                            win32_code=chunk.error.code,
                        )
                    )
                if not chunk.value:
                    return BinaryInspectionFailed(
                        probe_error(
                            ProbeErrorCode.BINARY_HASH,
                            ErrorCategory.INTEGRITY,
                            "binary_hash",
                            "unexpected EOF while hashing binary",
                        )
                    )
                if len(chunk.value) > remaining:
                    return BinaryInspectionFailed(
                        probe_error(
                            ProbeErrorCode.BINARY_HASH,
                            ErrorCategory.INTEGRITY,
                            "binary_hash",
                            "binary reader exceeded the requested bounded chunk",
                        )
                    )
                digest.update(chunk.value)
                remaining -= len(chunk.value)
            eof = self._port.read_file(handle, 1)
            if isinstance(eof, Win32Err) or eof.value:
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_HASH,
                        ErrorCategory.INTEGRITY,
                        "binary_hash_eof",
                        "binary EOF could not be proven",
                        win32_code=eof.error.code if isinstance(eof, Win32Err) else None,
                    )
                )
            after = self._port.query_file_info(handle)
            if isinstance(after, Win32Err):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_EVIDENCE,
                        ErrorCategory.IO,
                        "binary_snapshot_after_hash",
                        after.error.detail,
                        win32_code=after.error.code,
                    )
                )
            first_evidence = _evidence_from_info(info, path, digest.hexdigest())
            second = _evidence_from_info(after.value, path, digest.hexdigest())
            comparison = compare_snapshots(first_evidence.snapshot, second.snapshot)
            if not isinstance(comparison, SameInstanceUnchanged):
                return BinaryInspectionFailed(
                    probe_error(
                        ProbeErrorCode.BINARY_CHANGED,
                        ErrorCategory.INTEGRITY,
                        "binary_snapshot_after_hash",
                        "binary changed during complete hashing",
                    )
                )
            return first_evidence

    return (
        cast("type[_BinaryInspectionView]", BinaryInspection),
        cast("type[_NativeBinaryTrustPortView]", NativeBinaryTrustPort),
    )


BinaryInspection, NativeBinaryTrustPort = _build_binary_inspection_boundary()
del _build_binary_inspection_boundary


_CLEANUP_DIAGNOSTIC_LIMIT = 256


def _bounded_cleanup_text(value: str, fallback: str) -> str:
    """Return bounded plain text without trusting unusual string subclasses."""
    try:
        bounded = value[:_CLEANUP_DIAGNOSTIC_LIMIT]
        if not isinstance(bounded, str) or not bounded:
            return fallback
        return bounded
    except BaseException:
        return fallback


def _safe_cleanup_exception_summary(cleanup_exception: BaseException) -> str:
    """Render one bounded cleanup exception summary without ever raising."""
    static_fallback = "cleanup exception detail unavailable"
    try:
        detail = str(cleanup_exception)
    except BaseException:
        try:
            type_name = _bounded_cleanup_text(type(cleanup_exception).__name__, "cleanup exception")
            return f"{type_name}: <detail unavailable>"
        except BaseException:
            return static_fallback
    try:
        type_name = _bounded_cleanup_text(type(cleanup_exception).__name__, "cleanup exception")
        safe_detail = _bounded_cleanup_text(detail, "<no detail>")
        return _bounded_cleanup_text(
            f"{type_name}: {safe_detail}",
            static_fallback,
        )
    except BaseException:
        return static_fallback


def _append_cleanup_diagnostic(
    active: BaseException, cleanup_failure: BaseException | ProbeError
) -> None:
    """Attach a bounded cleanup note to *active* without allowing any failure out."""
    static_fallback = "binary handle cleanup failed; diagnostic unavailable"
    try:
        if isinstance(cleanup_failure, ProbeError):
            phase = _bounded_cleanup_text(cleanup_failure.phase, "unknown phase")
            message = _bounded_cleanup_text(cleanup_failure.message, "detail unavailable")
            note = _bounded_cleanup_text(
                f"binary handle cleanup was unresolved: {phase}: {message}",
                static_fallback,
            )
        else:
            summary = _safe_cleanup_exception_summary(cleanup_failure)
            note = _bounded_cleanup_text(
                f"binary handle cleanup raised {summary}",
                static_fallback,
            )
    except BaseException:
        note = static_fallback
    try:
        active.add_note(note)
    except BaseException:
        return


def _close_local_handle_once(
    handle: OwnedHandle, active: BaseException | None
) -> ProbeError | None:
    """Close one pre-transfer handle without replacing an established primary cause."""
    try:
        closed = handle.close()
    except BaseException as close_failure:
        if active is not None:
            _append_cleanup_diagnostic(active, close_failure)
            return None
        return probe_error(
            ProbeErrorCode.BINARY_ACCESS,
            ErrorCategory.IO,
            "binary_handle_close",
            _safe_cleanup_exception_summary(close_failure),
            cause=close_failure,
        )
    if isinstance(closed, Win32Err):
        close_error = probe_error(
            ProbeErrorCode.BINARY_ACCESS,
            ErrorCategory.IO,
            "binary_handle_close",
            closed.error.detail,
            win32_code=closed.error.code,
        )
        if active is not None:
            _append_cleanup_diagnostic(active, close_error)
            return None
        return close_error
    return None


def _close_inspection(inspection: _BinaryInspectionView) -> ProbeError | None:
    try:
        return inspection.close()
    except Exception as exc:
        return probe_error(
            ProbeErrorCode.BINARY_ACCESS,
            ErrorCategory.IO,
            "binary_handle_close",
            _safe_cleanup_exception_summary(exc),
            cause=exc,
        )


def _close_inspection_preserving_active(
    inspection: _BinaryInspectionView,
) -> ProbeError | None:
    """Close once without allowing cleanup to replace an exception in flight."""
    active = sys.exception()
    try:
        close_error = _close_inspection(inspection)
    except BaseException as close_failure:
        if active is None:
            raise
        _append_cleanup_diagnostic(active, close_failure)
        return None
    if active is not None and close_error is not None:
        _append_cleanup_diagnostic(active, close_error)
    return close_error


def _with_secondary(primary: ProbeError, secondary: ProbeError | None) -> ProbeError:
    if secondary is None:
        return primary
    return ProbeError(
        primary.code,
        primary.category,
        primary.phase,
        primary.message,
        primary.win32_code,
        primary.cause,
        primary.retryable,
        (*primary.secondary, secondary)[:8],
    )


@dataclass(frozen=True, slots=True)
class BinaryInspectionFailed:
    """No usable held verification handle/evidence was produced."""

    error: ProbeError


type BinaryInspectionResult = _BinaryInspectionView | BinaryInspectionFailed


class BinaryTrustPort(Protocol):
    """Injectable safe handle/hash boundary."""

    inspect: Callable[[ValidatedPath], BinaryInspectionResult]


@dataclass(frozen=True, slots=True)
class BinaryValidated:
    """Successful candidate validation."""

    binary: ValidatedFfprobeBinary


@dataclass(frozen=True, slots=True)
class BinaryValidationFailed:
    """Structured candidate rejection."""

    error: ProbeError


type BinaryValidationResult = BinaryValidated | BinaryValidationFailed


def _evidence_from_info(info: RawFileInfo, path: ValidatedPath, sha256: str) -> BinaryEvidence:
    attributes = info.attributes
    size = info.size_bytes
    creation = FileTime(info.creation_time_100ns)
    last = FileTime(info.last_write_time_100ns)
    change = FileTime(info.change_time_100ns)
    volume = AvailableIdentity(scheme="ntfs_volume_serial", value=f"{info.volume_serial:016x}")
    raw_file_id = info.file_id_128
    file_id = (
        AvailableIdentity(scheme="file_id_128", value=bytes(raw_file_id).hex())
        if raw_file_id is not None
        else UnavailableIdentity()
    )
    snapshot = FileSnapshot(
        path,
        "regular_file",
        size,
        last,
        creation,
        change,
        attributes,
        volume,
        file_id,
    )
    return BinaryEvidence(volume, file_id, size, creation, last, change, sha256, snapshot)


def _same_binary(expected: ValidatedFfprobeBinary | BinaryEvidence, actual: BinaryEvidence) -> bool:
    if isinstance(expected, ValidatedFfprobeBinary):
        if not _capability_is_authentic(expected):
            return False
        expected_snapshot = expected.original_snapshot
        if (
            expected.path.role is not PathRole.EXTERNAL_SOURCE_READ_ONLY
            or expected.canonical_dos_path != expected.path.canonical_dos_path
            or expected.long_path != expected.path.long_path
            or expected.validation_contract_version != BINARY_VALIDATION_CONTRACT_VERSION
            or expected.volume_id != expected_snapshot.volume_id
            or expected.file_id != expected_snapshot.file_id
            or expected.size_bytes != expected_snapshot.size_bytes
            or expected.creation_time != expected_snapshot.creation_time
            or expected.last_write_time != expected_snapshot.last_write_time
            or expected.change_time != expected_snapshot.change_time
        ):
            return False
    else:
        expected_snapshot = expected.snapshot
    expected_hash = expected.sha256
    return (
        isinstance(compare_snapshots(expected_snapshot, actual.snapshot), SameInstanceUnchanged)
        and expected_hash == actual.sha256
    )


class _BinaryValidator(Protocol):
    def __call__(
        self,
        candidate: FfprobeCandidate,
        win32_port: Win32Port,
        trust_port: BinaryTrustPort,
        process_port: ProcessPort,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> BinaryValidationResult: ...


def _build_capability_boundary() -> tuple[
    _BinaryValidator,
    Callable[[ValidatedFfprobeBinary], bool],
    Callable[[ValidatedFfprobeBinary], bool],
]:
    """Build one lexical validator/verification boundary with no exposed issuer."""
    authentication_key = secrets.token_bytes(32)
    product_policy = PRODUCT_FFPROBE_SUPPORT_POLICY
    product_policy_identity = product_policy.identity

    def product_policy_is_intact() -> bool:
        """Fail closed if even reflective mutation changes captured policy content."""
        try:
            return product_policy.identity == product_policy_identity
        except (AttributeError, TypeError, ValueError):
            return False

    def policy_is_current(binary: ValidatedFfprobeBinary) -> bool:
        """Check all content-derived policy identity fields against the product policy."""
        try:
            return (
                product_policy_is_intact()
                and binary.support_policy_revision == product_policy_identity.revision
                and binary.support_policy_type == product_policy_identity.policy_type
                and binary.support_policy_digest == product_policy_identity.content_sha256
            )
        except AttributeError:
            return False

    def capability_is_authentic(binary: ValidatedFfprobeBinary) -> bool:
        try:
            expected = hmac.digest(authentication_key, _capability_payload(binary), "sha256")
            if not hmac.compare_digest(binary._seal, expected):
                return False
            if binary.raw_version_output != binary.version.raw_output:
                return False
            if not policy_is_current(binary):
                return False
            parsed = parse_ffprobe_version(
                binary.raw_version_output.encode("utf-8", errors="strict")
            )
            if not isinstance(parsed, VersionParsed) or parsed.version != binary.version:
                return False
            support = evaluate_ffprobe_support(
                parsed.version.semantic_version,
                product_policy,
            )
            if (
                not isinstance(support, VersionSupported)
                or support.policy_identity != product_policy_identity
            ):
                return False
            validated_at = datetime.fromisoformat(binary.validated_at_utc)
            if validated_at.tzinfo is None:
                return False
        except (AttributeError, TypeError, ValueError, UnicodeError):
            return False
        return True

    def issue_after_complete_validation(
        path: ValidatedPath,
        evidence: BinaryEvidence,
        version: FfprobeVersion,
        stderr_output: bytes,
        validated_at_utc: str,
    ) -> ValidatedFfprobeBinary:
        binary = object.__new__(ValidatedFfprobeBinary)
        bound = {
            "path": path,
            "canonical_dos_path": path.canonical_dos_path,
            "long_path": path.long_path,
            "volume_id": evidence.volume_id,
            "file_id": evidence.file_id,
            "size_bytes": evidence.size_bytes,
            "creation_time": evidence.creation_time,
            "last_write_time": evidence.last_write_time,
            "change_time": evidence.change_time,
            "sha256": evidence.sha256,
            "raw_version_output": version.raw_output,
            "version": version,
            "version_stderr_output": stderr_output,
            "support_policy_revision": product_policy_identity.revision,
            "support_policy_type": product_policy_identity.policy_type,
            "support_policy_digest": product_policy_identity.content_sha256,
            "validation_contract_version": BINARY_VALIDATION_CONTRACT_VERSION,
            "validated_at_utc": validated_at_utc,
            "original_snapshot": evidence.snapshot,
        }
        for name, value in bound.items():
            object.__setattr__(binary, name, value)
        seal = hmac.digest(authentication_key, _capability_payload(binary), "sha256")
        object.__setattr__(binary, "_seal", seal)
        return binary

    def validate(
        candidate: FfprobeCandidate,
        win32_port: Win32Port,
        trust_port: BinaryTrustPort,
        process_port: ProcessPort,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> BinaryValidationResult:
        path_result = validate_path(
            win32_port,
            candidate.path,
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
            require_regular_file=True,
        )
        if isinstance(path_result, PathRejected):
            return BinaryValidationFailed(
                probe_error(
                    ProbeErrorCode.BINARY_ACCESS,
                    path_result.error.category,
                    "binary_path_validation",
                    path_result.error.message,
                    win32_code=path_result.error.win32_code,
                )
            )
        path = path_result.path
        initial = trust_port.inspect(path)
        if isinstance(initial, BinaryInspectionFailed):
            return BinaryValidationFailed(initial.error)
        try:
            result: BinaryValidationResult
            try:
                process = process_port.run(
                    ProcessSpec(
                        path.canonical_dos_path,
                        (path.canonical_dos_path, "-version"),
                        30,
                        VERSION_OUTPUT_LIMIT,
                        VERSION_OUTPUT_LIMIT,
                    ),
                    CancellationToken(),
                )
            except Exception as exc:
                result = BinaryValidationFailed(
                    probe_error(
                        ProbeErrorCode.PROCESS_FAILED,
                        ErrorCategory.IO,
                        "version_process_adapter",
                        str(exc) or type(exc).__name__,
                        cause=exc,
                    )
                )
            else:
                if not isinstance(process, ProbeProcessOk):
                    result = BinaryValidationFailed(process.error)
                else:
                    raw = process.diagnostics.stdout
                    try:
                        parsed = parse_ffprobe_version(raw)
                    except (ValueError, UnicodeError) as exc:
                        result = BinaryValidationFailed(
                            probe_error(
                                ProbeErrorCode.VERSION_OUTPUT,
                                ErrorCategory.INTEGRITY,
                                "version_parse",
                                str(exc) or type(exc).__name__,
                                cause=exc,
                            )
                        )
                    else:
                        if not isinstance(parsed, VersionParsed):
                            result = BinaryValidationFailed(parsed.error)
                        elif not product_policy_is_intact():
                            result = BinaryValidationFailed(
                                probe_error(
                                    ProbeErrorCode.BINARY_CHANGED,
                                    ErrorCategory.INTEGRITY,
                                    "binary_policy_contract",
                                    "active ffprobe support policy content changed",
                                )
                            )
                        else:
                            support = evaluate_ffprobe_support(
                                parsed.version.semantic_version,
                                product_policy,
                            )
                            if not isinstance(support, VersionSupported):
                                result = BinaryValidationFailed(support.error)
                            else:
                                try:
                                    post = trust_port.inspect(path)
                                except Exception as exc:
                                    result = BinaryValidationFailed(
                                        probe_error(
                                            ProbeErrorCode.BINARY_ACCESS,
                                            ErrorCategory.IO,
                                            "binary_post_version_adapter",
                                            str(exc) or type(exc).__name__,
                                            cause=exc,
                                        )
                                    )
                                else:
                                    if isinstance(post, BinaryInspectionFailed):
                                        result = BinaryValidationFailed(post.error)
                                    else:
                                        try:
                                            unchanged = _same_binary(
                                                initial.evidence, post.evidence
                                            )
                                        finally:
                                            post_close = _close_inspection_preserving_active(post)
                                        if not unchanged:
                                            result = BinaryValidationFailed(
                                                _with_secondary(
                                                    probe_error(
                                                        ProbeErrorCode.BINARY_CHANGED,
                                                        ErrorCategory.INTEGRITY,
                                                        "binary_post_version",
                                                        "binary instance or content changed during "
                                                        "version validation",
                                                    ),
                                                    post_close,
                                                )
                                            )
                                        elif post_close is not None:
                                            result = BinaryValidationFailed(post_close)
                                        else:
                                            result = BinaryValidated(
                                                issue_after_complete_validation(
                                                    path,
                                                    initial.evidence,
                                                    parsed.version,
                                                    process.diagnostics.stderr,
                                                    now().astimezone(UTC).isoformat(),
                                                )
                                            )
        finally:
            initial_close = _close_inspection_preserving_active(initial)
        if initial_close is not None:
            if isinstance(result, BinaryValidationFailed):
                return BinaryValidationFailed(_with_secondary(result.error, initial_close))
            return BinaryValidationFailed(initial_close)
        return result

    return validate, capability_is_authentic, policy_is_current


(
    validate_ffprobe_binary,
    _capability_is_authentic,
    _capability_policy_is_current,
) = _build_capability_boundary()
del _build_capability_boundary


def _open_verified_binary_for_launch(
    binary: ValidatedFfprobeBinary, trust_port: BinaryTrustPort
) -> BinaryInspectionResult:
    """Internally rehash before launch and retain the verification handle."""
    inspection = trust_port.inspect(binary.path)
    if isinstance(inspection, BinaryInspectionFailed):
        return inspection
    try:
        retain_for_launch = False
        failure: ProbeError | None = None
        close_error: ProbeError | None = None
        if not _capability_policy_is_current(binary):
            failure = probe_error(
                ProbeErrorCode.BINARY_CHANGED,
                ErrorCategory.INTEGRITY,
                "binary_policy_binding",
                "validated binary is not bound to the active ffprobe support policy",
            )
        elif not _same_binary(binary, inspection.evidence):
            failure = probe_error(
                ProbeErrorCode.BINARY_CHANGED,
                ErrorCategory.INTEGRITY,
                "binary_prelaunch",
                "validated binary binding changed before process start",
            )
        else:
            retain_for_launch = True
    finally:
        if not retain_for_launch:
            close_error = _close_inspection_preserving_active(inspection)
    if retain_for_launch:
        return inspection
    assert failure is not None
    return BinaryInspectionFailed(_with_secondary(failure, close_error))
