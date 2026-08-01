from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from tests.phase2.finalizer.conftest import (
    RUN_ID,
    SESSION_ID,
    add_validated_file,
    journal_bytes,
    journal_records,
)
from tests.phase2.source_confirmation.conftest import make_case, unique_streams

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationRequest,
    Finalized,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    finalize,
)
from matrix_auto_cutter.phase2.source_confirmation import (
    SourceConfirmationFailed,
    SourceConfirmed,
    confirm_source,
    parse_source_identity_evidence_bytes,
)
from matrix_auto_cutter.phase2.source_confirmation.identity import (
    _exact_scaled_integer,
    _quantize_duration_milliseconds,
    source_identity_digest,
)
from matrix_auto_cutter.sidecar import validate_sidecar


@pytest.mark.parametrize(
    ("duration", "expected_ms"),
    (
        ("6.800000", 6800),
        ("6.816667", 6817),
        ("6.833333", 6833),
        ("1.234500", 1235),
    ),
)
def test_duration_projection_uses_decimal_half_up(duration: str, expected_ms: int) -> None:
    assert _quantize_duration_milliseconds(Decimal(duration)) == expected_ms


@pytest.mark.parametrize(
    "duration",
    (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), Decimal("0.0004")),
)
def test_duration_projection_rejects_invalid_or_sub_millisecond_values(duration: Decimal) -> None:
    with pytest.raises(ValueError):
        _quantize_duration_milliseconds(duration)


def _streams_with_frame_count(frame_count: int) -> list[dict[str, object]]:
    streams = unique_streams()
    streams[0] = streams[0] | {"nb_frames": str(frame_count)}
    return streams


def _confirm(frame_count: int, duration: str):
    case = make_case(
        streams=_streams_with_frame_count(frame_count),
        format_duration=duration,
    )
    result = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(result, SourceConfirmed), result
    return case, result


def test_exact_millisecond_identity_bytes_and_digest_remain_unchanged() -> None:
    case, result = _confirm(60, "1.000000000")
    try:
        assert canonical_bytes(result.source_identity) == (
            b'{"audio_start_time_ns":0,"binding":"direct_mp4","duration_ms":1000,'
            b'"file_name":"source.mp4","fps_den":1,"fps_num":60,'
            b'"sha256":"757f0742475658083b49ee2850ed8e1a6be64f3cce5e289ac1c7874ae2b1bb38",'
            b'"size_bytes":115,"video_frame_count":60,"video_start_time_ns":0}\n'
        )
        assert source_identity_digest(result.source_identity) == (
            "f787147812b884c8d5bac1411122eab997514e4501a6ddfa23bd17a6c7dbdc17"
        )
    finally:
        case.close()


def test_equal_authoritative_evidence_is_deterministic() -> None:
    first_case, first = _confirm(409, "6.816667")
    second_case, second = _confirm(409, "6.816667")
    try:
        assert first.source_identity == second.source_identity
        assert canonical_bytes(first.source_identity) == canonical_bytes(second.source_identity)
        assert source_identity_digest(first.source_identity) == source_identity_digest(
            second.source_identity
        )
    finally:
        first_case.close()
        second_case.close()


@pytest.mark.parametrize(
    ("frame_count", "duration", "expected_ms"),
    ((409, "6.816667", 6817), (410, "6.833333", 6833)),
)
def test_authentic_confirmation_accepts_nonintegral_60fps_format_duration(
    frame_count: int, duration: str, expected_ms: int
) -> None:
    case, result = _confirm(frame_count, duration)
    try:
        assert result.source_identity.video_frame_count == frame_count
        assert result.source_identity.duration_ms == expected_ms
        evidence = parse_source_identity_evidence_bytes(
            bytes(
                case.port.nodes[case.port._key(result.evidence.source_identity_evidence_path)].data
            )
        )
        assert evidence.source_identity_digest == source_identity_digest(result.source_identity)
    finally:
        case.close()


@pytest.mark.parametrize("field", ("video", "audio"))
def test_start_times_remain_exactly_representable_in_nanoseconds(field: str) -> None:
    streams = unique_streams()
    index = 0 if field == "video" else 1
    streams[index] = streams[index] | {"start_time": "0.0000000001"}
    case = make_case(streams=streams)
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmationFailed)
        assert result.error.phase == "identity.build"
    finally:
        case.close()
    assert _exact_scaled_integer(Decimal("0"), 1_000_000_000, "start") == 0


@pytest.mark.parametrize("duration", ("0", "-1", "NaN", "Infinity", "0.0004"))
def test_full_confirmation_rejects_invalid_format_durations(duration: str) -> None:
    case = make_case(format_duration=duration)
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert not isinstance(result, SourceConfirmed)
    finally:
        case.close()


@pytest.mark.parametrize(
    ("frame_count", "duration", "expected_ms"),
    ((409, "6.816667", 6817), (410, "6.833333", 6833)),
)
def test_finalizer_publishes_validated_sidecar_for_both_60fps_remainders(
    frame_count: int, duration: str, expected_ms: int
) -> None:
    case, confirmed = _confirm(frame_count, duration)
    try:
        journal_path = r"C:\Input\recording.ndjson"
        validated_journal = add_validated_file(
            case.port,
            journal_path,
            journal_bytes(
                journal_records(
                    output_frame_count=frame_count,
                    stop_monotonic_ns=round(frame_count * 1_000_000_000 / 60),
                    calibration_samples=(
                        (round(frame_count * 1_000_000_000 / 120), frame_count // 2),
                    ),
                )
            ),
        )
        request = FinalizationRequest(
            case.project,
            RUN_ID,
            JournalInputProfile.LEGACY,
            JournalInputPaths(validated_journal),
            confirmed.confirmed_source,
            SESSION_ID,
        )
        result = finalize(
            FinalizerPorts(
                case.port,
                lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
                uuid4,
            ),
            request,
            CancellationToken(),
        )
        assert isinstance(result, Finalized), result
        payload = json.loads(
            bytes(case.port.nodes[case.port._key(result.sidecar.canonical_path)].data),
            parse_float=Decimal,
        )
        validated = validate_sidecar(payload, confirmed.source_identity)
        assert validated.mode == "validated_sidecar_1_1"
        assert validated.sidecar is not None
        assert validated.sidecar.source.duration_ms == expected_ms
    finally:
        case.close()
