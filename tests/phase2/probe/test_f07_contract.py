from __future__ import annotations

import copy
import hashlib
import inspect
import pickle
from dataclasses import replace
from decimal import Decimal
from itertools import permutations

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import FakeProcessPort, golden_json, golden_stream
from tests.phase2.probe.test_runner_process import source_request

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.probe import (
    STREAM_SELECTION_POLICY_VERSION,
    AudioSelectionReason,
    CanonicalJsonArray,
    CanonicalJsonObject,
    FinalizedStreamSelection,
    ProbeAmbiguousStreams,
    ProbeErrorCode,
    ProbeErrorDetail,
    ProbeFailed,
    ProbeJsonRejected,
    ProbeOk,
    ProbeProcessOk,
    ProbeUnsupportedMedia,
    ProcessDiagnostics,
    StreamsSelected,
    TagProjectionStatus,
    VideoSelectionReason,
    canonical_selection_identity_payload_bytes,
    canonical_stream_evidence_bytes,
    parse_probe_json,
    run_probe,
    select_streams,
    selection_semantically_matches,
    stream_selection_evidence_digest,
    validate_selection_identity_payload,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.probe.json_parser import (
    _canonical_json_value,
    _has_critical_duplicate,
)
from matrix_auto_cutter.phase2.probe.numeric_limits import (
    MAX_INTEGER_DIGITS,
    validate_bounded_integer,
    validate_decimal_value,
)
from matrix_auto_cutter.phase2.snapshots import snapshot_file


def parsed(*streams):
    result = parse_probe_json(golden_json(list(streams)))
    assert not isinstance(result, ProbeJsonRejected), result
    return result.streams


def selected(*streams) -> FinalizedStreamSelection:
    result = select_streams(parsed(*streams))
    assert isinstance(result, StreamsSelected), result
    return result.selection


def raw_with_index(index_lexeme: str) -> bytes:
    raw = golden_json([golden_stream(0, "video")]).decode()
    return raw.replace('"index":0', f'"index":{index_lexeme}', 1).encode()


@pytest.mark.parametrize(
    "raw_index",
    ["true", '"1"', "1.0", "1e0", "null", "-1", "9" * MAX_INTEGER_DIGITS],
)
def test_stream_index_requires_bounded_raw_nonnegative_json_integer(raw_index: str) -> None:
    result = parse_probe_json(raw_with_index(raw_index))
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA
    assert result.error.phase == "json_schema"


def test_duplicate_stream_indexes_and_duplicate_critical_keys_are_schema_errors() -> None:
    duplicate_index = parse_probe_json(
        golden_json([golden_stream(1, "video"), golden_stream(1, "audio")])
    )
    assert isinstance(duplicate_index, ProbeJsonRejected)
    assert duplicate_index.error.code is ProbeErrorCode.SCHEMA
    duplicate_key = golden_json([golden_stream(0, "video")]).replace(
        b'"index":0', b'"index":0,"index":1', 1
    )
    result = parse_probe_json(duplicate_key)
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA


def test_outer_stream_permutations_leave_digest_selection_and_identity_unchanged() -> None:
    source = [
        golden_stream(9, "video", default=1),
        golden_stream(2, "audio", default=1),
        golden_stream(0, "video", attached=1),
        golden_stream(5, "data", codec_name="bin_data", future={"x": [1, True]}),
    ]
    outcomes = tuple(selected(*order) for order in permutations(source))
    assert all(item == outcomes[0] for item in outcomes)
    assert [stream.index for stream in outcomes[0].stream_evidence] == [0, 2, 5, 9]


def test_unknown_stream_fields_are_recursive_typeful_bound_evidence() -> None:
    streams = parsed(
        golden_stream(
            0,
            "video",
            future={"z": [None, True, 1, 1.25, "x"], "a": {"k": "v"}},
        )
    )
    assert streams[0].extra_fields == (
        (
            "future",
            CanonicalJsonObject(
                (
                    ("a", CanonicalJsonObject((("k", "v"),))),
                    (
                        "z",
                        CanonicalJsonArray((None, True, 1, Decimal("1.25"), "x")),
                    ),
                )
            ),
        ),
    )


def test_unknown_field_changes_digest_but_not_the_policy_decision() -> None:
    first = selected(
        golden_stream(0, "video", future={"x": 1}),
        golden_stream(1, "audio"),
    )
    second = selected(
        golden_stream(0, "video", future={"x": 2}),
        golden_stream(1, "audio"),
    )
    assert (first.video_index, first.audio_index) == (second.video_index, second.audio_index)
    assert (first.video_reason_code, first.audio_reason_code) == (
        second.video_reason_code,
        second.audio_reason_code,
    )
    assert first.stream_selection_evidence_digest != second.stream_selection_evidence_digest


def test_unknown_boolean_integer_object_and_array_order_digest_rules() -> None:
    base_video = golden_stream(0, "video")
    audio = golden_stream(1, "audio")
    bool_digest = selected(base_video | {"future": True}, audio).evidence_digest
    int_digest = selected(base_video | {"future": 1}, audio).evidence_digest
    array_one = selected(base_video | {"future": [1, 2]}, audio).evidence_digest
    array_two = selected(base_video | {"future": [2, 1]}, audio).evidence_digest
    object_one = selected(base_video | {"future": {"a": 1, "b": 2}}, audio).evidence_digest
    object_two = selected(base_video | {"future": {"b": 2, "a": 1}}, audio).evidence_digest
    assert bool_digest != int_digest
    assert array_one != array_two
    assert object_one == object_two


@pytest.mark.parametrize(
    "mutation",
    [
        lambda stream: stream.update(Index=9),
        lambda stream: stream.update(CODEC_NAME="h264"),
        lambda stream: stream.update(
            disposition={"default": 0, "attached_pic": 0, "forced": 0, "Forced": 0}
        ),
        lambda stream: stream.update(disposition={"default": 0, "attached_pic": 0, "future": 0}),
        lambda stream: stream.update(side_data_list=[{"side_data_type": "x", "Rotation": 90}]),
    ],
)
def test_unknown_critical_semantics_and_ascii_case_collisions_fail_schema(mutation) -> None:
    stream = golden_stream(0, "video")
    mutation(stream)
    result = parse_probe_json(golden_json([stream]))
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.code is ProbeErrorCode.SCHEMA
    assert result.error.phase == "json_schema"


def test_unknown_side_data_extra_is_typeful_and_bound() -> None:
    stream = golden_stream(
        0,
        "video",
        side_data_list=[{"side_data_type": "Future", "opaque": {"items": [False, 7, 1.5]}}],
    )
    result = parse_probe_json(golden_json([stream]))
    assert not isinstance(result, ProbeJsonRejected)
    opaque = result.streams[0].side_data[0].extra_fields[0][1]
    assert isinstance(opaque, CanonicalJsonObject)


@pytest.mark.parametrize("codec_name", [None, "", "   ", "unknown", "NONE", "N/A"])
@pytest.mark.parametrize("kind", ["video", "audio"])
def test_missing_or_sentinel_codec_blocks_every_relevant_stream(kind: str, codec_name) -> None:
    streams = [golden_stream(0, "video"), golden_stream(1, "audio")]
    streams[0 if kind == "video" else 1]["codec_name"] = codec_name
    result = select_streams(parsed(*streams))
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == f"stream_selection.{kind}_metadata"


def test_missing_codec_field_normalizes_then_fails_selection_not_schema() -> None:
    video = golden_stream(0, "video")
    video.pop("codec_name")
    streams = parsed(video, golden_stream(1, "audio"))
    assert streams[0].codec_name is None
    result = select_streams(streams)
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == "stream_selection.video_metadata"


@pytest.mark.parametrize(
    ("kind", "field"),
    [
        ("video", "attached_pic"),
        ("video", "default"),
        ("audio", "default"),
    ],
)
def test_missing_critical_disposition_is_metadata_not_missing(kind: str, field: str) -> None:
    stream = golden_stream(0, kind)
    stream["disposition"].pop(field)
    companions = [stream, golden_stream(1, "audio" if kind == "video" else "video")]
    result = select_streams(parsed(*companions))
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == f"stream_selection.{kind}_metadata"


def test_incomplete_nondefault_video_and_audio_block_complete_primary_streams() -> None:
    video = select_streams(
        parsed(
            golden_stream(0, "video", default=1),
            golden_stream(1, "video", width=None),
            golden_stream(2, "audio"),
        )
    )
    audio = select_streams(
        parsed(
            golden_stream(0, "video"),
            golden_stream(1, "audio", default=1),
            golden_stream(2, "audio", duration=None),
        )
    )
    assert isinstance(video, ProbeUnsupportedMedia)
    assert video.error.phase == "stream_selection.video_metadata"
    assert isinstance(audio, ProbeUnsupportedMedia)
    assert audio.error.phase == "stream_selection.audio_metadata"


def test_video_partial_resolution_order_orientation_and_no_area_rank() -> None:
    dominant = selected(
        golden_stream(0, "video", width=1080, height=1920),
        golden_stream(1, "video", width=1280, height=720),
        golden_stream(2, "audio"),
    )
    assert dominant.video_index == 0
    assert dominant.video_reason_code is VideoSelectionReason.UNIQUE_RESOLUTION_MAXIMUM
    incomparable = select_streams(
        parsed(
            golden_stream(0, "video", width=1920, height=1080),
            golden_stream(1, "video", width=2560, height=900),
            golden_stream(2, "audio"),
        )
    )
    assert isinstance(incomparable, ProbeAmbiguousStreams)


def test_pix_fmt_and_cfr_summaries_have_no_selection_effect() -> None:
    pix_fmt_optional = selected(golden_stream(0, "video", pix_fmt=None), golden_stream(1, "audio"))
    assert pix_fmt_optional.video.pix_fmt is None
    cfr_tie = select_streams(
        parsed(
            golden_stream(0, "video", avg_frame_rate="30/1", r_frame_rate="30/1"),
            golden_stream(
                1,
                "video",
                avg_frame_rate="60/1",
                r_frame_rate="60/1",
                nb_frames="60",
            ),
            golden_stream(2, "audio"),
        )
    )
    assert isinstance(cfr_tie, ProbeAmbiguousStreams)


def test_audio_default_precedes_support_and_nondefaults_filter_after_classification() -> None:
    unsupported_default = select_streams(
        parsed(
            golden_stream(0, "video"),
            golden_stream(1, "audio", default=1, channels=6, channel_layout="5.1"),
            golden_stream(2, "audio"),
        )
    )
    assert isinstance(unsupported_default, ProbeUnsupportedMedia)
    assert unsupported_default.error.detail is ProbeErrorDetail.AUDIO_LAYOUT_UNSUPPORTED
    supported_nondefault = selected(
        golden_stream(0, "video"),
        golden_stream(1, "audio"),
        golden_stream(2, "audio", channels=6, channel_layout="5.1"),
    )
    assert supported_nondefault.audio_index == 1
    assert supported_nondefault.audio_reason_code is AudioSelectionReason.SINGLE_ELIGIBLE


def test_audio_multiple_defaults_are_ambiguous_before_support_filtering() -> None:
    result = select_streams(
        parsed(
            golden_stream(0, "video"),
            golden_stream(1, "audio", default=1),
            golden_stream(2, "audio", default=1, channels=6, channel_layout="5.1"),
        )
    )
    assert isinstance(result, ProbeAmbiguousStreams)
    assert result.error.phase == "stream_selection.audio_ambiguous"


def test_tag_case_variants_are_evidence_with_diagnostic_ambiguity_only() -> None:
    selection = selected(
        golden_stream(0, "video"),
        golden_stream(
            1,
            "audio",
            tags={
                "language": "deu",
                "LANGUAGE": "eng",
                "title": "one",
                "TITLE": "two",
                "rotate": "bad",
                "ROTATE": "different",
            },
        ),
    )
    assert selection.audio.language_projection.status is TagProjectionStatus.AMBIGUOUS
    assert selection.audio.title_projection.status is TagProjectionStatus.AMBIGUOUS
    assert selection.audio.language is None
    assert selection.audio.rotation.explicit_degrees is None


def test_rotation_tags_do_not_conflict_with_or_change_side_data_rotation() -> None:
    first = selected(
        golden_stream(
            0,
            "video",
            tags={"rotate": "90"},
            side_data_list=[{"side_data_type": "Display Matrix", "rotation": 180}],
        ),
        golden_stream(1, "audio"),
    )
    second = selected(
        golden_stream(
            0,
            "video",
            tags={"ROTATE": "invalid"},
            side_data_list=[{"side_data_type": "Display Matrix", "rotation": 180}],
        ),
        golden_stream(1, "audio"),
    )
    assert first.video.rotation.display_matrix_degrees == 180
    assert second.video.rotation.display_matrix_degrees == 180
    assert first.evidence_digest != second.evidence_digest


def test_evidence_and_identity_domains_payload_and_closed_schema_are_exact() -> None:
    selection = selected(golden_stream(7, "video"), golden_stream(2, "audio"))
    canonical = canonical_selection_identity_payload_bytes(
        selection.evidence_digest,
        7,
        2,
        selection.video_reason_code,
        selection.audio_reason_code,
    )
    assert canonical == (
        b'{"audio_index":2,"audio_reason_code":"audio_single_eligible",'
        b'"policy_id":"stream_selection/1.0","stream_selection_evidence_digest":"'
        + selection.evidence_digest.encode()
        + b'","video_index":7,"video_reason_code":"video_single_eligible"}'
    )
    assert not canonical.endswith(b"\n")
    expected_identity = hashlib.sha256(
        b"matrix-stream-selection-identity/1.0\0" + canonical
    ).hexdigest()
    assert selection.selection_identity == expected_identity
    assert selection.evidence_digest == stream_selection_evidence_digest(selection.stream_evidence)


def test_identity_payload_validator_rejects_missing_extra_types_and_unknown_reasons() -> None:
    payload = {
        "policy_id": STREAM_SELECTION_POLICY_VERSION,
        "stream_selection_evidence_digest": "a" * 64,
        "video_index": 0,
        "audio_index": 1,
        "video_reason_code": VideoSelectionReason.SINGLE_ELIGIBLE.value,
        "audio_reason_code": AudioSelectionReason.SINGLE_ELIGIBLE.value,
    }
    assert validate_selection_identity_payload(payload)
    for invalid in (
        payload | {"seventh": 1},
        {key: value for key, value in payload.items() if key != "audio_index"},
        payload | {"video_index": True},
        payload | {"audio_index": "1"},
        payload | {"video_reason_code": "future"},
        payload | {"policy_id": "stream_selection/2.0"},
    ):
        assert not validate_selection_identity_payload(invalid)


def test_copy_pickle_replace_and_self_consistent_wrong_selection_have_no_authority() -> None:
    evidence = parsed(
        golden_stream(0, "video", default=1),
        golden_stream(1, "video"),
        golden_stream(2, "audio"),
    )
    correct_result = select_streams(evidence)
    assert isinstance(correct_result, StreamsSelected)
    correct = correct_result.selection
    assert selection_semantically_matches(copy.copy(correct), evidence)
    assert selection_semantically_matches(copy.deepcopy(correct), evidence)
    assert selection_semantically_matches(pickle.loads(pickle.dumps(correct)), evidence)
    wrong = FinalizedStreamSelection(
        evidence[1],
        evidence[2],
        evidence,
        VideoSelectionReason.UNIQUE_DEFAULT,
        AudioSelectionReason.SINGLE_ELIGIBLE,
    )
    assert wrong.integrity_valid()
    assert not selection_semantically_matches(wrong, evidence)
    changed_reason = replace(
        correct, video_reason_code=VideoSelectionReason.UNIQUE_RESOLUTION_MAXIMUM
    )
    assert changed_reason.integrity_valid()
    assert not selection_semantically_matches(changed_reason, evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_id", "stream_selection/9.9"),
        ("stream_selection_evidence_digest", "0" * 64),
        ("selection_identity", "0" * 64),
        ("video_reason_code", AudioSelectionReason.SINGLE_ELIGIBLE),
    ],
)
def test_frozen_bypass_manipulations_fail_semantic_authority(field: str, value: object) -> None:
    evidence = parsed(golden_stream(0, "video"), golden_stream(1, "audio"))
    selection = selected(golden_stream(0, "video"), golden_stream(1, "audio"))
    object.__setattr__(selection, field, value)
    assert not selection_semantically_matches(selection, evidence)


def test_run_probe_calls_same_selection_function_once_for_selection_and_once_for_revalidation(
    fake_port: FakePort,
    validated_binary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrix_auto_cutter.phase2.probe import runner, stream_selection

    original = stream_selection.select_streams
    calls = 0

    def counted(streams):
        nonlocal calls
        calls += 1
        return original(streams)

    monkeypatch.setattr(runner, "select_streams", counted)
    monkeypatch.setattr(stream_selection, "select_streams", counted)
    result = run_probe(
        source_request(fake_port, validated_binary),
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(
            ProbeProcessOk(
                ProcessDiagnostics(
                    golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]),
                    b"",
                )
            )
        ),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeOk)
    assert calls == 2
    assert inspect.getsource(runner.run_probe).count("finalize_success()") == 1


@pytest.mark.parametrize(
    "streams",
    [
        [golden_stream(0, "audio")],
        [golden_stream(0, "video", codec_name=None), golden_stream(1, "audio")],
        [golden_stream(0, "video"), golden_stream(1, "video"), golden_stream(2, "audio")],
    ],
)
def test_post_parse_failures_retain_profile_evidence_digest_without_selection_identity(
    fake_port: FakePort,
    validated_binary,
    streams,
) -> None:
    result = run_probe(
        source_request(fake_port, validated_binary),
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(golden_json(streams), b""))),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.profile is not None
    assert result.profile.streams
    assert len(result.profile.stream_selection_evidence_digest) == 64
    assert not hasattr(result.profile, "selection")
    assert not hasattr(result.profile, "selection_identity")


def test_remaining_canonical_evidence_and_identity_defensive_edges() -> None:
    with_null_and_decimal = selected(
        golden_stream(0, "video", future=[None, 0.0, "x"]),
        golden_stream(1, "audio"),
    )
    assert len(with_null_and_decimal.evidence_digest) == 64
    duplicate = replace(with_null_and_decimal.audio, index=with_null_and_decimal.video_index)
    with pytest.raises(ValueError):
        canonical_stream_evidence_bytes((with_null_and_decimal.video, duplicate))
    with pytest.raises(ValueError):
        canonical_selection_identity_payload_bytes(
            "x",
            0,
            1,
            VideoSelectionReason.SINGLE_ELIGIBLE,
            AudioSelectionReason.SINGLE_ELIGIBLE,
        )
    payload = {
        "policy_id": STREAM_SELECTION_POLICY_VERSION,
        "stream_selection_evidence_digest": "a" * 64,
        "video_index": 1 << 300,
        "audio_index": 1,
        "video_reason_code": VideoSelectionReason.SINGLE_ELIGIBLE.value,
        "audio_reason_code": AudioSelectionReason.SINGLE_ELIGIBLE.value,
    }
    assert not validate_selection_identity_payload(payload)
    assert with_null_and_decimal.rationale == (
        "video_single_eligible",
        "audio_single_eligible",
    )
    with pytest.raises(ValueError):
        FinalizedStreamSelection(
            with_null_and_decimal.video,
            with_null_and_decimal.audio,
            with_null_and_decimal.stream_evidence,
            VideoSelectionReason.SINGLE_ELIGIBLE,
            "bad-audio-reason",
        )


def test_remaining_parser_duplicate_rotation_and_private_defensive_edges() -> None:
    assert not _has_critical_duplicate([])
    assert isinstance(parse_probe_json(raw_with_index("1" * 65)), ProbeJsonRejected)
    disposition_duplicate = golden_json([golden_stream(0, "video")]).replace(
        b'"default":0', b'"default":0,"default":1', 1
    )
    side_duplicate = golden_json(
        [
            golden_stream(
                0,
                "video",
                side_data_list=[{"side_data_type": "x", "rotation": 90}],
            )
        ]
    ).replace(b'"rotation":90', b'"rotation":90,"rotation":180', 1)
    for raw in (disposition_duplicate, side_duplicate):
        result = parse_probe_json(raw)
        assert isinstance(result, ProbeJsonRejected)
        assert result.error.code is ProbeErrorCode.SCHEMA
    integer_bool = parse_probe_json(golden_json([golden_stream(0, "audio", channels=True)]))
    assert isinstance(integer_bool, ProbeJsonRejected)
    negative_format = parse_probe_json(
        golden_json(
            [golden_stream(0, "video")],
            format={
                "filename": "x.mp4",
                "format_name": "x",
                "duration": "-1",
            },
        )
    )
    assert isinstance(negative_format, ProbeJsonRejected)
    accepted_string_rotation = parse_probe_json(
        golden_json(
            [
                golden_stream(
                    0,
                    "video",
                    side_data_list=[{"side_data_type": "x", "rotation": "90"}],
                )
            ]
        )
    )
    assert not isinstance(accepted_string_rotation, ProbeJsonRejected)
    unsupported_rotation = parse_probe_json(
        golden_json(
            [
                golden_stream(
                    0,
                    "video",
                    side_data_list=[{"side_data_type": "x", "rotation": 45}],
                )
            ]
        )
    )
    assert isinstance(unsupported_rotation, ProbeJsonRejected)
    with pytest.raises(ValueError):
        _canonical_json_value({1, 2}, "test")


def test_remaining_numeric_and_semantic_revalidation_defensive_edges() -> None:
    with pytest.raises(ValueError):
        validate_decimal_value(Decimal("NaN"))
    assert validate_decimal_value(Decimal("1")) == (0, (1,), 0)
    assert validate_bounded_integer(1) == 1
    selection = selected(golden_stream(0, "video"), golden_stream(1, "audio"))
    assert not selection_semantically_matches(selection, ())
    assert not selection_semantically_matches(selection, ("invalid",))
