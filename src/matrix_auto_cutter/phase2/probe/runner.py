"""Lease-free package-2B probe-core orchestration."""

from __future__ import annotations

import sys
from collections.abc import Callable

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspectionFailed,
    BinaryTrustPort,
    _close_inspection_preserving_active,
    _open_verified_binary_for_launch,
    _same_binary,
)
from matrix_auto_cutter.phase2.probe.contracts import (
    PROBE_CONTRACT_VERSION,
    MediaProfile,
    ProbeCoreResult,
    ProbeDiagnosticProfile,
    ProbeFailed,
    ProbeOk,
    ProbeRequest,
    StreamsSelected,
)
from matrix_auto_cutter.phase2.probe.errors import (
    ProbeError,
    ProbeErrorCode,
    _TerminalKind,
    _TerminalLatch,
    probe_error,
)
from matrix_auto_cutter.phase2.probe.json_parser import ParsedProbeJson, parse_probe_json
from matrix_auto_cutter.phase2.probe.process_port import ProbeProcessOk, ProcessPort, ProcessSpec
from matrix_auto_cutter.phase2.probe.stream_selection import (
    select_streams,
    selection_semantically_matches,
)
from matrix_auto_cutter.phase2.snapshots import (
    FileSnapshot,
    NotComparable,
    SameInstanceUnchanged,
    SnapshotOk,
    SnapshotResult,
    compare_snapshots,
)

PROBE_ARGUMENTS = (
    "-v",
    "error",
    "-print_format",
    "json",
    "-show_error",
    "-show_format",
    "-show_streams",
    "-show_programs",
)


def _snapshot_failure(result: SnapshotResult, phase: str) -> ProbeFailed:
    if isinstance(result, SnapshotOk):
        raise ValueError("successful snapshot is not a failure")
    error = result.error
    code = (
        ProbeErrorCode.SOURCE_EVIDENCE_INSUFFICIENT
        if "EVIDENCE_INSUFFICIENT" in str(error.code)
        else ProbeErrorCode.SOURCE_CHANGED
    )
    return ProbeFailed(
        probe_error(
            code,
            error.category,
            phase,
            error.message,
            win32_code=error.win32_code,
            cause=error.cause,
        )
    )


def _close_with_primary(
    primary: ProbeError | None, close_error: ProbeError | None
) -> ProbeError | None:
    if close_error is None:
        return primary
    if primary is None:
        return close_error
    return ProbeError(
        primary.code,
        primary.category,
        primary.phase,
        primary.message,
        primary.win32_code,
        primary.cause,
        primary.retryable,
        (*primary.secondary, close_error)[:8],
        primary.detail,
    )


def _verify_source(
    before: FileSnapshot,
    after: FileSnapshot,
    expected_key: str,
) -> ProbeError | None:
    if before.snapshot_key != expected_key:
        return probe_error(
            ProbeErrorCode.SOURCE_CHANGED,
            ErrorCategory.INTEGRITY,
            "source_snapshot_before",
            "source snapshot does not match the expected snapshot key",
        )
    comparison = compare_snapshots(before, after)
    if isinstance(comparison, NotComparable):
        return probe_error(
            ProbeErrorCode.SOURCE_EVIDENCE_INSUFFICIENT,
            ErrorCategory.INTEGRITY,
            "source_snapshot_after",
            "source instance equality cannot be proven",
        )
    if not isinstance(comparison, SameInstanceUnchanged):
        return probe_error(
            ProbeErrorCode.SOURCE_CHANGED,
            ErrorCategory.INTEGRITY,
            "source_snapshot_after",
            "source instance or snapshot evidence changed during probe",
        )
    return None


def _adapter_exception(
    code: ProbeErrorCode,
    category: ErrorCategory,
    phase: str,
    exc: Exception,
) -> ProbeError:
    return probe_error(code, category, phase, str(exc) or type(exc).__name__, cause=exc)


def _record_cancellation(
    latch: _TerminalLatch,
    cancellation: CancellationToken,
    phase: str,
    already_recorded: bool,
) -> bool:
    if already_recorded or not cancellation.is_cancelled:
        return already_recorded
    latch.fail(
        _TerminalKind.CANCELLED,
        probe_error(
            ProbeErrorCode.CANCELLED,
            ErrorCategory.CANCELLED,
            phase,
            "probe was cancelled before final success",
        ),
    )
    return True


def _record_cleanup(latch: _TerminalLatch, errors: list[ProbeError]) -> None:
    for error in errors:
        latch.fail(_TerminalKind.CLEANUP, error)


def run_probe(
    request: ProbeRequest,
    trust_port: BinaryTrustPort,
    process_port: ProcessPort,
    snapshotter: Callable[[object], SnapshotResult],
    cancellation: CancellationToken,
) -> ProbeCoreResult:
    """Run one bounded source probe without claiming close/stability/finality."""
    latch = _TerminalLatch()
    cancellation_recorded = _record_cancellation(latch, cancellation, "probe_before_start", False)
    if cancellation_recorded:
        error = latch.error()
        assert error is not None
        return ProbeFailed(error)
    try:
        before_result = snapshotter(request.source)
    except Exception as exc:
        latch.fail(
            _TerminalKind.POST_SNAPSHOT,
            _adapter_exception(
                ProbeErrorCode.SOURCE_CHANGED,
                ErrorCategory.IO,
                "source_snapshot_before_adapter",
                exc,
            ),
        )
        error = latch.error()
        assert error is not None
        return ProbeFailed(error)
    if not isinstance(before_result, SnapshotOk):
        latch.fail(
            _TerminalKind.POST_SNAPSHOT,
            _snapshot_failure(before_result, "source_snapshot_before").error,
        )
    elif before_result.snapshot.snapshot_key != request.expected_snapshot_key:
        latch.fail(
            _TerminalKind.POST_SNAPSHOT,
            probe_error(
                ProbeErrorCode.SOURCE_CHANGED,
                ErrorCategory.INTEGRITY,
                "source_snapshot_before",
                "source snapshot does not match the expected snapshot key",
            ),
        )
    cancellation_recorded = _record_cancellation(
        latch, cancellation, "probe_after_snapshot_before", cancellation_recorded
    )
    early_error = latch.error()
    if early_error is not None:
        return ProbeFailed(early_error)
    assert isinstance(before_result, SnapshotOk)
    try:
        held = _open_verified_binary_for_launch(request.binary, trust_port)
    except Exception as exc:
        latch.fail(
            _TerminalKind.BINARY_INTEGRITY,
            _adapter_exception(
                ProbeErrorCode.BINARY_ACCESS,
                ErrorCategory.IO,
                "binary_prelaunch_adapter",
                exc,
            ),
        )
        error = latch.error()
        assert error is not None
        return ProbeFailed(error)
    if isinstance(held, BinaryInspectionFailed):
        latch.fail(_TerminalKind.BINARY_INTEGRITY, held.error)
        error = latch.error()
        assert error is not None
        return ProbeFailed(error)
    try:
        parsed: ParsedProbeJson | None = None
        cleanup_errors: list[ProbeError] = []
        try:
            process = process_port.run(
                ProcessSpec(
                    request.binary.canonical_dos_path,
                    (
                        request.binary.canonical_dos_path,
                        *PROBE_ARGUMENTS,
                        request.source.canonical_dos_path,
                    ),
                    request.timeout_seconds,
                ),
                cancellation,
            )
        except Exception as exc:
            latch.fail(
                _TerminalKind.PROCESS_CONTROL,
                _adapter_exception(
                    ProbeErrorCode.PROCESS_FAILED,
                    ErrorCategory.IO,
                    "process_adapter",
                    exc,
                ),
            )
        else:
            if isinstance(process, ProbeProcessOk):
                try:
                    parsed_result = parse_probe_json(process.diagnostics.stdout)
                except Exception as exc:
                    latch.fail(
                        _TerminalKind.READER_IO,
                        _adapter_exception(
                            ProbeErrorCode.INVALID_JSON,
                            ErrorCategory.INTEGRITY,
                            "json_parser",
                            exc,
                        ),
                    )
                else:
                    if isinstance(parsed_result, ParsedProbeJson):
                        parsed = parsed_result
                    else:
                        latch.fail(_TerminalKind.READER_IO, parsed_result.error)
            else:
                primary = process.error
                kind = (
                    _TerminalKind.CANCELLED
                    if primary.code is ProbeErrorCode.CANCELLED
                    else _TerminalKind.TIMEOUT
                    if primary.code is ProbeErrorCode.TIMEOUT
                    else _TerminalKind.OUTPUT_LIMIT
                    if primary.code is ProbeErrorCode.OUTPUT_LIMIT
                    else _TerminalKind.PROCESS_START
                    if primary.code is ProbeErrorCode.START_FAILED
                    else _TerminalKind.PROCESS_CONTROL
                )
                latch.fail(kind, primary)
        cancellation_recorded = _record_cancellation(
            latch, cancellation, "probe_after_process", cancellation_recorded
        )
        try:
            post_binary = trust_port.inspect(request.binary.path)
        except Exception as exc:
            latch.fail(
                _TerminalKind.BINARY_INTEGRITY,
                _adapter_exception(
                    ProbeErrorCode.BINARY_ACCESS,
                    ErrorCategory.IO,
                    "binary_post_probe_adapter",
                    exc,
                ),
            )
        else:
            if isinstance(post_binary, BinaryInspectionFailed):
                latch.fail(_TerminalKind.BINARY_INTEGRITY, post_binary.error)
            else:
                try:
                    if not _same_binary(request.binary, post_binary.evidence):
                        latch.fail(
                            _TerminalKind.BINARY_INTEGRITY,
                            probe_error(
                                ProbeErrorCode.BINARY_CHANGED,
                                ErrorCategory.INTEGRITY,
                                "binary_post_probe",
                                "binary changed during probe execution",
                            ),
                        )
                finally:
                    active = sys.exception()
                    post_close = _close_inspection_preserving_active(post_binary)
                    if active is None and post_close is not None:
                        cleanup_errors.append(post_close)
    finally:
        active = sys.exception()
        held_close = _close_inspection_preserving_active(held)
        if active is None and held_close is not None:
            cleanup_errors.append(held_close)
    cancellation_recorded = _record_cancellation(
        latch, cancellation, "probe_after_cleanup", cancellation_recorded
    )
    try:
        after_result = snapshotter(request.source)
    except Exception as exc:
        latch.fail(
            _TerminalKind.POST_SNAPSHOT,
            _adapter_exception(
                ProbeErrorCode.SOURCE_CHANGED,
                ErrorCategory.IO,
                "source_snapshot_after_adapter",
                exc,
            ),
        )
        after_result = None
    if not isinstance(after_result, SnapshotOk):
        if after_result is not None:
            snapshot_error = _snapshot_failure(after_result, "source_snapshot_after").error
            latch.fail(_TerminalKind.POST_SNAPSHOT, snapshot_error)
        cancellation_recorded = _record_cancellation(
            latch, cancellation, "probe_after_snapshot_after", cancellation_recorded
        )
        _record_cleanup(latch, cleanup_errors)
        final_error = latch.error()
        assert final_error is not None
        return ProbeFailed(final_error)
    assert isinstance(after_result, SnapshotOk)
    cancellation_recorded = _record_cancellation(
        latch, cancellation, "probe_after_snapshot_after", cancellation_recorded
    )
    source_error = _verify_source(
        before_result.snapshot,
        after_result.snapshot,
        request.expected_snapshot_key,
    )
    if source_error is not None:
        latch.fail(_TerminalKind.POST_SNAPSHOT, source_error)
    final_error = latch.error()
    if final_error is not None:
        _record_cleanup(latch, cleanup_errors)
        final_error = latch.error()
        assert final_error is not None
        return ProbeFailed(final_error)
    assert parsed is not None
    diagnostic_profile: ProbeDiagnosticProfile | None = None
    cancellation_recorded = _record_cancellation(
        latch, cancellation, "probe_before_stream_selection", cancellation_recorded
    )
    try:
        selection = select_streams(parsed.streams)
    except Exception as exc:
        latch.fail(
            _TerminalKind.INTERNAL,
            _adapter_exception(
                ProbeErrorCode.UNSUPPORTED_MEDIA,
                ErrorCategory.POLICY,
                "stream_selection",
                exc,
            ),
        )
        selection = None
    if selection is not None and not isinstance(selection, StreamsSelected):
        latch.fail(_TerminalKind.INTERNAL, selection.error)
        diagnostic_profile = ProbeDiagnosticProfile(
            PROBE_CONTRACT_VERSION,
            request.binary,
            request.source,
            request.expected_snapshot_key,
            before_result.snapshot,
            after_result.snapshot,
            parsed.format,
            parsed.streams,
            parsed.programs,
            selection.stream_selection_evidence_digest,
        )
    cancellation_recorded = _record_cancellation(
        latch, cancellation, "probe_after_stream_selection", cancellation_recorded
    )
    _record_cleanup(latch, cleanup_errors)
    final_error = latch.error()
    if final_error is not None:
        return ProbeFailed(final_error, diagnostic_profile)
    assert isinstance(selection, StreamsSelected)
    profile = MediaProfile(
        PROBE_CONTRACT_VERSION,
        request.binary,
        request.source,
        request.expected_snapshot_key,
        before_result.snapshot,
        after_result.snapshot,
        parsed.format,
        parsed.streams,
        parsed.programs,
        selection.selection,
    )
    if not selection_semantically_matches(profile.selection, parsed.streams):
        latch.fail(
            _TerminalKind.INTERNAL,
            probe_error(
                ProbeErrorCode.STREAM_INTEGRITY,
                ErrorCategory.INTEGRITY,
                "stream_finalization_integrity",
                "profile stream selection failed final integrity validation",
            ),
        )
        final_error = latch.error()
        assert final_error is not None
        return ProbeFailed(final_error)
    if cancellation.begin_irreversible_commit() is None:
        _record_cancellation(latch, cancellation, "probe_final_commit", cancellation_recorded)
        cancel_error = latch.error()
        assert cancel_error is not None
        return ProbeFailed(cancel_error)
    assert latch.finalize_success()
    return ProbeOk(profile)
