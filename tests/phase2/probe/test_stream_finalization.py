from __future__ import annotations

import copy
import json
import pickle
from dataclasses import FrozenInstanceError, replace
from itertools import permutations

import pytest
from tests.phase2.probe.conftest import golden_json, golden_stream

from matrix_auto_cutter.phase2.probe import (
    STREAM_SELECTION_POLICY_VERSION,
    AudioSelectionReason,
    FinalizedStreamSelection,
    ProbeAmbiguousStreams,
    ProbeJsonRejected,
    ProbeUnsupportedMedia,
    StreamsSelected,
    VideoSelectionReason,
    parse_probe_json,
    select_streams,
)


def parsed(streams):
    result = parse_probe_json(golden_json(list(streams)))
    assert not isinstance(result, ProbeJsonRejected), result
    return result.streams


def selected(streams) -> FinalizedStreamSelection:
    result = select_streams(parsed(streams))
    assert isinstance(result, StreamsSelected), result
    return result.selection


def test_one_video_one_audio_is_fully_finalized() -> None:
    selection = selected([golden_stream(7, "video"), golden_stream(2, "audio")])
    assert selection.video_index == 7
    assert selection.audio_index == 2
    assert selection.policy_version == STREAM_SELECTION_POLICY_VERSION
    assert selection.integrity_valid()
    assert len(selection.evidence_digest) == 64
    assert len(selection.selection_identity) == 64


def test_attached_pictures_never_compete_with_main_video_in_any_position() -> None:
    cover = golden_stream(0, "video", default=1, attached=1, width=8000, height=8000)
    main = golden_stream(9, "video", pix_fmt="yuv444p", profile="Main")
    audio = golden_stream(4, "audio")
    for ordered in ([cover, main, audio], [audio, cover, main], [main, audio, cover]):
        selection = selected(ordered)
        assert selection.video_index == 9
        assert selection.video.profile == "Main"
        assert selection.video.pix_fmt == "yuv444p"


def test_only_attached_pictures_is_structured_missing_video() -> None:
    result = select_streams(
        parsed(
            [
                golden_stream(3, "video", attached=1),
                golden_stream(4, "video", attached=1),
                golden_stream(8, "audio"),
            ]
        )
    )
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == "stream_selection.video_missing"


def test_missing_required_kind_is_distinct_from_incomplete_metadata() -> None:
    missing_video = select_streams(parsed([golden_stream(1, "audio")]))
    missing_audio = select_streams(parsed([golden_stream(1, "video")]))
    bad_video = select_streams(
        parsed([golden_stream(1, "video", width=None), golden_stream(2, "audio")])
    )
    bad_audio = select_streams(
        parsed([golden_stream(1, "video"), golden_stream(2, "audio", duration=None)])
    )
    assert isinstance(missing_video, ProbeUnsupportedMedia)
    assert isinstance(missing_audio, ProbeUnsupportedMedia)
    assert isinstance(bad_video, ProbeUnsupportedMedia)
    assert isinstance(bad_audio, ProbeUnsupportedMedia)
    assert missing_video.error.phase == "stream_selection.video_missing"
    assert missing_audio.error.phase == "stream_selection.audio_missing"
    assert bad_video.error.phase == "stream_selection.video_metadata"
    assert bad_audio.error.phase == "stream_selection.audio_metadata"


@pytest.mark.parametrize("field", ["width", "height"])
def test_missing_required_video_metadata_blocks_finalization(field: str) -> None:
    result = select_streams(
        parsed([golden_stream(1, "video", **{field: None}), golden_stream(2, "audio")])
    )
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == "stream_selection.video_metadata"


@pytest.mark.parametrize(
    "updates",
    [
        {"codec_name": "unknown"},
    ],
)
def test_unknown_codec_and_invalid_video_rate_are_not_candidates(updates) -> None:
    result = select_streams(
        parsed([golden_stream(1, "video", **updates), golden_stream(2, "audio")])
    )
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == "stream_selection.video_metadata"


def test_video_default_and_resolution_priorities_are_exact() -> None:
    default = selected(
        [
            golden_stream(8, "video", default=1, width=1280, height=720),
            golden_stream(1, "video", width=3840, height=2160),
            golden_stream(2, "audio"),
        ]
    )
    assert default.video_index == 8

    resolution = selected(
        [
            golden_stream(8, "video", width=1280, height=720),
            golden_stream(1, "video", width=1920, height=1080),
            golden_stream(2, "audio"),
        ]
    )
    assert resolution.video_index == 1

    frame_rate = select_streams(
        parsed(
            [
                golden_stream(8, "video"),
                golden_stream(
                    1,
                    "video",
                    r_frame_rate="60/1",
                    avg_frame_rate="60/1",
                    nb_frames="60",
                ),
                golden_stream(2, "audio"),
            ]
        )
    )
    assert isinstance(frame_rate, ProbeAmbiguousStreams)


def test_video_ties_and_multiple_defaults_are_order_independent_ambiguities() -> None:
    for candidates in (
        [golden_stream(7, "video"), golden_stream(1, "video")],
        [golden_stream(7, "video", default=1), golden_stream(1, "video", default=1)],
    ):
        forward = select_streams(parsed([*candidates, golden_stream(3, "audio")]))
        reverse = select_streams(parsed([*reversed(candidates), golden_stream(3, "audio")]))
        assert isinstance(forward, ProbeAmbiguousStreams)
        assert isinstance(reverse, ProbeAmbiguousStreams)
        assert forward.error.phase == reverse.error.phase == "stream_selection.video_ambiguous"
        assert forward.error.message == reverse.error.message


def test_audio_default_and_supported_layout_priority_are_exact() -> None:
    default = selected(
        [
            golden_stream(0, "video"),
            golden_stream(8, "audio", default=1, channels=1, channel_layout="mono"),
            golden_stream(1, "audio"),
        ]
    )
    assert default.audio_index == 8

    layout = selected(
        [
            golden_stream(0, "video"),
            golden_stream(8, "audio", channels=1, channel_layout="mono"),
            golden_stream(1, "audio"),
        ]
    )
    assert layout.audio_index == 1


def test_audio_ties_languages_and_multiple_defaults_remain_ambiguous() -> None:
    cases = (
        [golden_stream(8, "audio"), golden_stream(1, "audio")],
        [
            golden_stream(8, "audio", tags={"language": "deu"}),
            golden_stream(1, "audio", tags={"language": "eng"}),
        ],
        [golden_stream(8, "audio", default=1), golden_stream(1, "audio", default=1)],
    )
    for candidates in cases:
        forward = select_streams(parsed([golden_stream(0, "video"), *candidates]))
        reverse = select_streams(parsed([golden_stream(0, "video"), *reversed(candidates)]))
        assert isinstance(forward, ProbeAmbiguousStreams)
        assert isinstance(reverse, ProbeAmbiguousStreams)
        assert forward.error.phase == reverse.error.phase == "stream_selection.audio_ambiguous"


@pytest.mark.parametrize(
    "updates",
    [
        {"codec_name": "unknown"},
        {"sample_rate": None},
        {"channels": None},
        {"channel_layout": None},
        {"channel_layout": "5.1", "channels": 6},
        {"duration": "0"},
    ],
)
def test_incomplete_audio_metadata_blocks_finalization(updates) -> None:
    result = select_streams(
        parsed([golden_stream(0, "video"), golden_stream(1, "audio", **updates)])
    )
    assert isinstance(result, ProbeUnsupportedMedia)
    assert result.error.phase == "stream_selection.audio_metadata"


def test_missing_language_and_title_are_optional_evidence_not_tiebreakers() -> None:
    no_language = selected(
        [golden_stream(0, "video"), golden_stream(1, "audio", tags={"title": "Voice"})]
    )
    assert no_language.audio.language is None
    assert no_language.audio.title == "Voice"

    no_title = selected(
        [golden_stream(0, "video"), golden_stream(1, "audio", tags={"language": "deu"})]
    )
    assert no_title.audio.language == "deu"
    assert no_title.audio.title is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda root: root["streams"][0].pop("index"),
        lambda root: root["streams"][0].update(index=-1),
        lambda root: root["streams"][0].update(index=True),
        lambda root: root["streams"].append(golden_stream(0, "audio")),
        lambda root: root["streams"][0].update(disposition={"default": True}),
    ],
)
def test_invalid_stream_objects_are_structured_schema_failures(mutation) -> None:
    root = json.loads(golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]))
    mutation(root)
    result = parse_probe_json(json.dumps(root).encode())
    assert isinstance(result, ProbeJsonRejected)
    assert result.error.phase == "json_schema"


def test_stream_array_permutations_produce_identical_final_evidence() -> None:
    evidence = [
        golden_stream(9, "video", default=1),
        golden_stream(2, "audio", default=1),
        golden_stream(0, "video", attached=1),
        golden_stream(5, "data", codec_name="bin_data"),
    ]
    outcomes = [selected(order) for order in permutations(evidence)]
    assert all(outcome == outcomes[0] for outcome in outcomes)
    assert all(outcome.selection_identity == outcomes[0].selection_identity for outcome in outcomes)
    assert [stream.index for stream in outcomes[0].stream_evidence] == [0, 2, 5, 9]


def test_all_final_metadata_comes_from_the_selected_stream_objects() -> None:
    selection = selected(
        [
            golden_stream(
                11,
                "video",
                default=1,
                codec_name="h264",
                profile="Main",
                pix_fmt="yuv422p",
                width=1280,
                height=720,
                tags={"language": "zxx", "title": "Main Camera"},
            ),
            golden_stream(
                3,
                "video",
                codec_name="vp9",
                profile="Profile 2",
                pix_fmt="yuv444p",
                width=3840,
                height=2160,
            ),
            golden_stream(
                12,
                "audio",
                default=1,
                codec_name="aac",
                sample_rate="44100",
                channels=1,
                channel_layout="mono",
                tags={"language": "deu", "title": "German Voice"},
            ),
            golden_stream(
                4,
                "audio",
                codec_name="opus",
                sample_rate="48000",
                channels=2,
                channel_layout="stereo",
                tags={"language": "eng", "title": "Wrong Candidate"},
            ),
        ]
    )
    video = selection.video
    assert (video.index, video.codec_name, video.profile, video.pix_fmt) == (
        11,
        "h264",
        "Main",
        "yuv422p",
    )
    assert (video.width, video.height, video.title, video.language) == (
        1280,
        720,
        "Main Camera",
        "zxx",
    )
    assert video.time_base is not None
    assert video.r_frame_rate is not None
    assert video.avg_frame_rate is not None
    assert video.disposition.default

    audio = selection.audio
    assert (audio.index, audio.codec_name, audio.sample_rate) == (12, "aac", 44100)
    assert (audio.channels, audio.channel_layout) == (1, "mono")
    assert (audio.language, audio.title) == ("deu", "German Voice")
    assert audio.disposition.default


def test_selected_and_nonselected_changes_are_bound_but_order_is_not() -> None:
    base = [
        golden_stream(8, "video", default=1),
        golden_stream(9, "audio", default=1),
        golden_stream(1, "video", attached=1, tags={"title": "Cover A"}),
    ]
    original = selected(base)
    changed_selected = selected(
        [golden_stream(8, "video", default=1, profile="Main"), base[1], base[2]]
    )
    changed_nonselected = selected(
        [base[0], base[1], golden_stream(1, "video", attached=1, tags={"title": "Cover B"})]
    )
    reordered = selected(list(reversed(base)))
    assert changed_selected.selection_identity != original.selection_identity
    assert changed_nonselected.selection_identity != original.selection_identity
    assert reordered == original


def test_raw_mutation_copy_pickle_replace_and_frozen_contract() -> None:
    raw_video = golden_stream(0, "video")
    raw_audio = golden_stream(1, "audio")
    selection = selected([raw_video, raw_audio])
    identity = selection.selection_identity
    raw_video["width"] = 1
    raw_audio["tags"]["language"] = "tampered"
    assert selection.selection_identity == identity
    assert selection.video.width == 1920
    assert selection.audio.language == "und"

    assert copy.copy(selection) == selection
    assert copy.deepcopy(selection) == selection
    assert pickle.loads(pickle.dumps(selection)) == selection
    assert replace(selection, video_reason_code=selection.video_reason_code) == selection
    with pytest.raises(FrozenInstanceError):
        selection.video = selection.video
    with pytest.raises(ValueError):
        replace(selection, stream_evidence=tuple(reversed(selection.stream_evidence)))


def test_incomplete_or_cross_bound_final_state_cannot_be_constructed() -> None:
    streams = parsed([golden_stream(0, "video"), golden_stream(1, "audio")])
    video, audio = streams
    with pytest.raises(ValueError):
        FinalizedStreamSelection(
            video,
            audio,
            (video,),
            VideoSelectionReason.SINGLE_ELIGIBLE,
            AudioSelectionReason.SINGLE_ELIGIBLE,
        )
    with pytest.raises(ValueError):
        FinalizedStreamSelection(
            video,
            video,
            streams,
            VideoSelectionReason.SINGLE_ELIGIBLE,
            AudioSelectionReason.SINGLE_ELIGIBLE,
        )
    duplicate_audio = replace(audio, index=video.index)
    with pytest.raises(ValueError):
        FinalizedStreamSelection(
            video,
            duplicate_audio,
            (video, duplicate_audio),
            VideoSelectionReason.SINGLE_ELIGIBLE,
            AudioSelectionReason.SINGLE_ELIGIBLE,
        )
    attached_video = replace(video, disposition=replace(video.disposition, attached_pic=True))
    with pytest.raises(ValueError):
        FinalizedStreamSelection(
            attached_video,
            audio,
            (attached_video, audio),
            VideoSelectionReason.SINGLE_ELIGIBLE,
            AudioSelectionReason.SINGLE_ELIGIBLE,
        )
    for video_reason in ("", "future"):
        with pytest.raises(ValueError):
            FinalizedStreamSelection(
                video,
                audio,
                streams,
                video_reason,
                AudioSelectionReason.SINGLE_ELIGIBLE,
            )


def test_integrity_deviation_is_detectable_without_reselection() -> None:
    selection = selected([golden_stream(0, "video"), golden_stream(1, "audio")])
    object.__setattr__(selection, "selection_identity", "0" * 64)
    assert not selection.integrity_valid()
    object.__setattr__(selection, "stream_evidence", ("invalid",))
    assert not selection.integrity_valid()


def test_other_stream_types_never_become_main_streams() -> None:
    selection = selected(
        [
            golden_stream(4, "subtitle", codec_name="subrip"),
            golden_stream(3, "data", codec_name="bin_data"),
            golden_stream(2, "attachment", codec_name="ttf"),
            golden_stream(1, "future", codec_name="future"),
            golden_stream(8, "video"),
            golden_stream(9, "audio"),
        ]
    )
    assert selection.video_index == 8
    assert selection.audio_index == 9
