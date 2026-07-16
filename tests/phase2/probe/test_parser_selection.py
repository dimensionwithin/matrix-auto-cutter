from __future__ import annotations

import json
from decimal import Decimal

import pytest
from tests.phase2.probe.conftest import golden_json, golden_stream

from matrix_auto_cutter.phase2.probe import (
    CanonicalJsonObject,
    ProbeAmbiguousStreams,
    ProbeErrorCode,
    ProbeJsonRejected,
    ProbeUnsupportedMedia,
    Rational,
    StreamsSelected,
    StreamType,
    parse_probe_json,
    select_streams,
)
from matrix_auto_cutter.phase2.probe.json_parser import (
    MAX_ARRAY_ITEMS,
    MAX_DEPTH,
    MAX_OBJECT_ITEMS,
    MAX_STRING_CHARS,
)
from matrix_auto_cutter.phase2.probe.numeric_limits import (
    MAX_DECIMAL_EXPONENT_ABS,
    MAX_DERIVED_INTEGER_BITS,
    MAX_INTEGER_DIGITS,
    MAX_NUMERIC_LEXEME_CHARS,
    MAX_SIGNIFICANT_DIGITS,
    validate_decimal_lexeme,
    validate_integer_lexeme,
)


def parsed_streams(raw: bytes):
    result = parse_probe_json(raw)
    assert not isinstance(result, ProbeJsonRejected), result
    return result.streams


def complete_av(*streams):
    """Add only the unrelated required kind so one selection policy can be isolated."""
    result = list(streams)
    indexes = {stream["index"] for stream in result}
    spare = next(index for index in range(1000, 2000) if index not in indexes)
    if not any(stream["codec_type"] == "video" for stream in result):
        result.append(golden_stream(spare, "video", default=1))
        spare += 1
    if not any(stream["codec_type"] == "audio" for stream in result):
        result.append(golden_stream(spare, "audio", default=1))
    return result


def test_valid_reference_json_preserves_programs_types_tags_side_data_and_rotation() -> None:
    streams = [
        golden_stream(
            0,
            "video",
            default=1,
            tags={"rotate": "90", "name": "untrusted"},
            side_data_list=[
                {
                    "side_data_type": "Display Matrix",
                    "displaymatrix": "matrix bytes",
                    "rotation": 90,
                    "mystery": {"x": 1},
                },
                {"side_data_type": "Unknown", "opaque": "kept"},
            ],
        ),
        golden_stream(1, "audio", default=1),
        golden_stream(2, "subtitle", codec_name="subrip"),
        golden_stream(3, "data", codec_name="bin_data"),
        golden_stream(4, "attachment", codec_name="ttf"),
        golden_stream(5, "future_type", codec_name="future"),
    ]
    programs = [{"program_id": 7, "streams": [{"index": 0}, {"index": 1}], "tags": {"x": "y"}}]
    result = parse_probe_json(golden_json(streams, programs=programs))
    assert not isinstance(result, ProbeJsonRejected)
    assert [stream.stream_type for stream in result.streams] == [
        StreamType.VIDEO,
        StreamType.AUDIO,
        StreamType.SUBTITLE,
        StreamType.DATA,
        StreamType.ATTACHMENT,
        StreamType.UNKNOWN,
    ]
    assert result.streams[0].rotation.explicit_degrees is None
    assert result.streams[0].rotation.display_matrix_degrees == 90
    assert result.streams[0].side_data[0].untrusted_fields == (
        ("mystery", CanonicalJsonObject((("x", 1),))),
    )
    assert result.programs[0].stream_indexes == (0, 1)
    assert result.format.tags == (("title", "untrusted"),)
    numeric_unknown = golden_json(
        [
            golden_stream(
                7,
                "video",
                side_data_list=[{"side_data_type": "Future", "decimal": 1.25}],
            )
        ]
    )
    numeric_result = parse_probe_json(numeric_unknown)
    assert not isinstance(numeric_result, ProbeJsonRejected)
    assert numeric_result.streams[0].side_data[0].untrusted_fields == (
        ("decimal", Decimal("1.25")),
    )


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"\xef\xbb\xbf{}", ProbeErrorCode.INVALID_UTF8),
        (b"\xff", ProbeErrorCode.INVALID_UTF8),
        (b"\xe2\x82", ProbeErrorCode.INVALID_UTF8),
        (b"", ProbeErrorCode.INVALID_JSON),
        (b"{", ProbeErrorCode.INVALID_JSON),
        (b'{"x":1,"x":2}', ProbeErrorCode.INVALID_JSON),
        (b'{"x":NaN}', ProbeErrorCode.INVALID_JSON),
        (b'{"x":Infinity}', ProbeErrorCode.INVALID_JSON),
        (b'{"x":-Infinity}', ProbeErrorCode.INVALID_JSON),
        (b"[]", ProbeErrorCode.SCHEMA),
        (b'{"programs":[],"streams":[],"format":{},"unknown":1}', ProbeErrorCode.SCHEMA),
        (b'{"programs":[],"streams":[],"format":{},"error":{}}', ProbeErrorCode.UNSUPPORTED_MEDIA),
    ],
)
def test_decode_and_json_boundary_rejections(raw: bytes, code: ProbeErrorCode) -> None:
    result = parse_probe_json(raw)
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is code


def test_json_explicit_structural_limits() -> None:
    cases = [
        b"[" * (MAX_DEPTH + 1) + b"]" * (MAX_DEPTH + 1),
        json.dumps("x" * (MAX_STRING_CHARS + 1)).encode(),
        json.dumps([0] * (MAX_ARRAY_ITEMS + 1)).encode(),
        json.dumps({str(i): i for i in range(MAX_OBJECT_ITEMS + 1)}).encode(),
    ]
    for raw in cases:
        result = parse_probe_json(raw)
        assert isinstance(result, ProbeJsonRejected)
        assert result.error.code is ProbeErrorCode.INVALID_JSON


@pytest.mark.parametrize(
    "mutation",
    [
        lambda root: root.update(streams={}),
        lambda root: root.update(programs={}),
        lambda root: root.update(format=[]),
        lambda root: root["streams"].append("bad"),
        lambda root: root["streams"][0].update(index=-1),
        lambda root: root["streams"][0].update(index=True),
        lambda root: root["streams"][0].update(time_base="1/0"),
        lambda root: root["streams"][0].update(time_base="one/30"),
        lambda root: root["streams"][0].update(nb_frames="-1"),
        lambda root: root["streams"][0].update(disposition={"default": 2}),
        lambda root: root["streams"][0].update(tags={"x": 1}),
        lambda root: root["streams"][0].update(side_data_list={}),
        lambda root: root["streams"][0].update(side_data_list=["bad"]),
        lambda root: root["streams"][0].update(side_data_list=[{}]),
        lambda root: root["streams"].append(golden_stream(0, "audio")),
        lambda root: root.update(programs=[{"program_id": 1, "streams": [{"index": 99}]}]),
    ],
)
def test_wrong_types_missing_fields_and_schema_contradictions(mutation) -> None:
    root = json.loads(golden_json([golden_stream(0, "video")]))
    mutation(root)
    result = parse_probe_json(json.dumps(root).encode())
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA


@pytest.mark.parametrize(
    "stream_update",
    [
        {"side_data_list": [{"side_data_type": "Display Matrix", "rotation": "90.5"}]},
        {
            "side_data_list": [
                {"side_data_type": "Display Matrix", "rotation": 90},
                {"side_data_type": "Display Matrix", "rotation": 180},
            ]
        },
    ],
)
def test_rotation_conflicts_and_rounding_fail_closed(stream_update) -> None:
    stream = golden_stream(0, "video", **stream_update)
    result = parse_probe_json(golden_json([stream]))
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA


def test_rational_normalization_extremes_and_decimal_precision() -> None:
    assert Rational(2, -4) == Rational(-1, 2)
    assert Rational(0, -999).denominator == 1
    assert Rational(10**18, 2 * 10**18) == Rational(1, 2)
    assert Rational(1, 3).compare(Rational(2, 6)) == 0
    with pytest.raises(ValueError):
        Rational(1, 0)
    stream = golden_stream(0, "audio", duration="0.1000000000000000000000000001")
    result = parse_probe_json(golden_json([stream]))
    assert not isinstance(result, ProbeJsonRejected)
    assert result.streams[0].duration.value == Decimal("0.1000000000000000000000000001")


@pytest.mark.parametrize(
    "lexeme",
    [
        "1e1000000000",
        "1e-1000000000",
        "9e999999999999999999999999",
        "0." + "0" * 40 + "1",
        "9" * 40,
        "+1e1000000000",
        "-1e1000000000",
        "01e1000000000",
        "1E+1000000000",
        "1E-1000000000",
        "NaN",
        "Infinity",
        "-Infinity",
    ],
)
def test_adversarial_decimal_strings_fail_before_expensive_conversion(lexeme: str) -> None:
    stream = golden_stream(0, "audio", duration=lexeme)
    result = parse_probe_json(golden_json([stream]))
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA


@pytest.mark.parametrize(
    "rational",
    [
        f"{'9' * 40}/1",
        f"1/{'9' * 40}",
        f"{'9' * 40}/{'9' * 40}",
    ],
)
def test_adversarial_rational_components_fail_before_integer_conversion(rational: str) -> None:
    result = parse_probe_json(golden_json([golden_stream(0, "video", time_base=rational)]))
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA


def test_numeric_limits_have_exact_boundaries_and_normal_ffprobe_values() -> None:
    at_lexeme_limit = "1e" + "0" * (MAX_NUMERIC_LEXEME_CHARS - 2)
    assert validate_decimal_lexeme(at_lexeme_limit) == Decimal(1)
    with pytest.raises(ValueError):
        validate_decimal_lexeme(at_lexeme_limit + "0")
    assert validate_decimal_lexeme("1" * MAX_SIGNIFICANT_DIGITS).is_finite()
    with pytest.raises(ValueError):
        validate_decimal_lexeme("1" * (MAX_SIGNIFICANT_DIGITS + 1))
    assert validate_decimal_lexeme(f"1e{MAX_DECIMAL_EXPONENT_ABS}").is_finite()
    assert validate_decimal_lexeme(f"1e-{MAX_DECIMAL_EXPONENT_ABS}").is_finite()
    with pytest.raises(ValueError):
        validate_decimal_lexeme(f"1e{MAX_DECIMAL_EXPONENT_ABS + 1}")
    assert validate_integer_lexeme(str((1 << 63) - 1)) == (1 << 63) - 1
    assert len(str((1 << 63) - 1)) == MAX_INTEGER_DIGITS
    with pytest.raises(ValueError):
        validate_integer_lexeme("1" + "0" * MAX_INTEGER_DIGITS)
    assert validate_integer_lexeme("000001", allow_leading_zeroes=True) == 1
    assert validate_decimal_lexeme("-0") == Decimal("-0")
    assert Rational(30000, 1001) == Rational(30000, 1001)
    assert Rational(24000, 1001) == Rational(24000, 1001)
    for normal in ("48000", "44100", "0.000000", "5400.125000"):
        assert validate_decimal_lexeme(normal).is_finite()


def test_derived_integer_bit_length_boundary() -> None:
    assert Rational(1 << (MAX_DERIVED_INTEGER_BITS - 1), 1).positive
    with pytest.raises(ValueError):
        Rational(1 << MAX_DERIVED_INTEGER_BITS, 1)


def test_huge_exponent_is_rejected_before_decimal_constructor(monkeypatch) -> None:
    from matrix_auto_cutter.phase2.probe import numeric_limits

    called = False

    def forbidden_decimal(_value: str):
        nonlocal called
        called = True
        raise AssertionError("Decimal construction must not occur")

    monkeypatch.setattr(numeric_limits, "Decimal", forbidden_decimal)
    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_lexeme("1e1000000000")
    assert not called


def test_numeric_defensive_conversion_and_magnitude_edges(monkeypatch) -> None:
    from decimal import InvalidOperation

    from matrix_auto_cutter.phase2.probe import numeric_limits

    real_decimal = Decimal
    monkeypatch.setattr(
        numeric_limits,
        "Decimal",
        lambda _value: (_ for _ in ()).throw(InvalidOperation()),
    )
    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_lexeme("1.0")
    monkeypatch.setattr(numeric_limits, "Decimal", lambda _value: real_decimal("NaN"))
    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_lexeme("1.0")
    monkeypatch.setattr(numeric_limits, "Decimal", real_decimal)

    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_value(real_decimal("1" * 33))
    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_value(real_decimal("1E+19"))
    with pytest.raises(ValueError):
        numeric_limits.validate_decimal_value(real_decimal("1E-33"))
    with pytest.raises(ValueError):
        validate_integer_lexeme(str(1 << 63))
    with pytest.raises(ValueError):
        numeric_limits.validate_bounded_integer(True)
    with pytest.raises(ValueError):
        numeric_limits.validate_bounded_integer(1 << 63)


def test_integer_schema_rejects_non_numeric_container() -> None:
    result = parse_probe_json(golden_json([golden_stream(0, "audio", channels=[])]))
    assert isinstance(result, ProbeJsonRejected)


def test_unique_defaults_and_attached_picture_exclusion() -> None:
    streams = parsed_streams(
        golden_json(
            [
                golden_stream(9, "video", default=1),
                golden_stream(1, "video", attached=1, width=8000, height=8000),
                golden_stream(8, "audio", default=1),
                golden_stream(0, "data"),
            ]
        )
    )
    result = select_streams(streams)
    assert isinstance(result, StreamsSelected)
    assert result.status.video_index == 9
    assert result.status.audio_index == 8


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_multiple_defaults_are_always_ambiguous(kind: str) -> None:
    streams = parsed_streams(
        golden_json(
            complete_av(golden_stream(8, kind, default=1), golden_stream(2, kind, default=1))
        )
    )
    result = select_streams(streams)
    assert isinstance(result, ProbeAmbiguousStreams)
    assert result.error.code is ProbeErrorCode.AMBIGUOUS_STREAMS


def test_video_resolution_and_cfr_never_breaks_a_tie() -> None:
    unique = parsed_streams(
        golden_json(
            complete_av(
                golden_stream(10, "video", width=1280, height=720),
                golden_stream(2, "video"),
            )
        )
    )
    result = select_streams(unique)
    assert isinstance(result, StreamsSelected) and result.status.video_index == 2

    cfr = parsed_streams(
        golden_json(
            complete_av(
                golden_stream(1, "video", r_frame_rate="30/1", avg_frame_rate="30/1"),
                golden_stream(
                    99, "video", r_frame_rate="60/1", avg_frame_rate="60/1", nb_frames="60"
                ),
            )
        )
    )
    result = select_streams(cfr)
    assert isinstance(result, ProbeAmbiguousStreams)


def test_video_ties_invalid_cfr_and_order_permutations() -> None:
    tied = parsed_streams(
        golden_json(complete_av(golden_stream(7, "video"), golden_stream(1, "video")))
    )
    assert isinstance(select_streams(tied), ProbeAmbiguousStreams)
    assert isinstance(select_streams(tuple(reversed(tied))), ProbeAmbiguousStreams)

    contradictory = parsed_streams(
        golden_json(complete_av(golden_stream(1, "video", avg_frame_rate="60/1")))
    )
    assert isinstance(select_streams(contradictory), StreamsSelected)

    inconsistent_frames = parsed_streams(
        golden_json(complete_av(golden_stream(1, "video", nb_frames="31")))
    )
    assert isinstance(select_streams(inconsistent_frames), StreamsSelected)
    zero_duration = parsed_streams(
        golden_json(complete_av(golden_stream(1, "video", duration="0", nb_frames="0")))
    )
    assert isinstance(select_streams(zero_duration), StreamsSelected)
    missing_counts = golden_stream(1, "video")
    missing_counts.pop("duration")
    missing_counts.pop("nb_frames")
    assert isinstance(
        select_streams(parsed_streams(golden_json(complete_av(missing_counts)))), StreamsSelected
    )

    descending = parsed_streams(
        golden_json(
            complete_av(
                golden_stream(
                    1,
                    "video",
                    r_frame_rate="60/1",
                    avg_frame_rate="60/1",
                    nb_frames="60",
                ),
                golden_stream(2, "video"),
            )
        )
    )
    selected = select_streams(descending)
    assert isinstance(selected, ProbeAmbiguousStreams)


def test_audio_policy_mono_stereo_ties_unknown_and_missing_fields() -> None:
    mono = golden_stream(5, "audio", channels=1, channel_layout="mono")
    stereo = golden_stream(9, "audio")
    result = select_streams(parsed_streams(golden_json(complete_av(mono, stereo))))
    assert isinstance(result, StreamsSelected) and result.status.audio_index == 9

    tied = parsed_streams(
        golden_json(complete_av(golden_stream(8, "audio"), golden_stream(1, "audio")))
    )
    assert isinstance(select_streams(tied), ProbeAmbiguousStreams)
    assert isinstance(select_streams(tuple(reversed(tied))), ProbeAmbiguousStreams)

    for updates in (
        {"channel_layout": "5.1"},
        {"channels": None},
        {"sample_rate": None},
        {"channel_layout": None},
    ):
        unsupported = parsed_streams(golden_json(complete_av(golden_stream(0, "audio", **updates))))
        assert isinstance(select_streams(unsupported), ProbeUnsupportedMedia)


def test_required_audio_video_and_only_attached_picture_fail_closed() -> None:
    audio = select_streams(parsed_streams(golden_json([golden_stream(4, "audio")])))
    assert isinstance(audio, ProbeUnsupportedMedia)
    assert audio.error.phase == "stream_selection.video_missing"

    video = select_streams(parsed_streams(golden_json([golden_stream(6, "video")])))
    assert isinstance(video, ProbeUnsupportedMedia)
    assert video.error.phase == "stream_selection.audio_missing"

    attached = select_streams(parsed_streams(golden_json([golden_stream(1, "video", attached=1)])))
    assert isinstance(attached, ProbeUnsupportedMedia)


def test_non_av_streams_never_become_main_streams() -> None:
    streams = parsed_streams(
        golden_json(
            [
                golden_stream(0, "data"),
                golden_stream(1, "subtitle"),
                golden_stream(2, "attachment"),
            ]
        )
    )
    assert isinstance(select_streams(streams), ProbeUnsupportedMedia)
