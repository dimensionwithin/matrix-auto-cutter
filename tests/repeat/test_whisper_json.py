"""Tests for the whisper.cpp raw-JSON -> repeat_transcript/1.0 conversion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.repeat.errors import RawOutputEmptyError, RawOutputMissingError
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument
from matrix_auto_cutter.repeat.whisper_json import convert_whisper_output

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "whisper_raw_sample.json"
_DEFAULT_SOURCE_DURATION_MS = 10_000


def _token(text: str, start_ms: int, end_ms: int, probability: float | None = 0.9) -> dict:
    token = {"text": text, "offsets": {"from": start_ms, "to": end_ms}}
    if probability is not None:
        token["p"] = probability
    return token


def _convert(segments: list[dict], **kwargs: Any) -> RepeatTranscriptDocument:
    kwargs.setdefault("source_duration_ms", _DEFAULT_SOURCE_DURATION_MS)
    kwargs.setdefault("audio_stream_specifier", "0:a:0")
    raw_json = json.dumps({"transcription": segments})
    return convert_whisper_output(raw_json, **kwargs)


def test_real_fixture_converts_and_validates_against_contract() -> None:
    raw_json = _FIXTURE_PATH.read_text(encoding="utf-8")
    document = convert_whisper_output(
        raw_json, source_duration_ms=999_000, audio_stream_specifier="0:a:0"
    )
    assert document.artifact_type == "matrix_auto_cutter_repeat_transcript"
    assert document.schema_version == "1.0"
    assert document.source_duration_ms == 999_000
    assert len(document.segments) == 2
    first_words = [word.text for word in document.segments[0].words]
    assert first_words[0] == "von"
    assert "," in "".join(first_words)


def test_bpe_subword_continuation_joins_without_space() -> None:
    segment = {
        "offsets": {"from": 0, "to": 1000},
        "tokens": [
            _token(" erstmal", 0, 400),
            _token("ig", 400, 500),
        ],
    }
    document = _convert([segment])
    words = document.segments[0].words
    assert len(words) == 1
    assert words[0].text == "erstmalig"


def test_leading_blank_is_stripped_from_new_word() -> None:
    segment = {
        "offsets": {"from": 0, "to": 500},
        "tokens": [_token(" Hallo", 0, 500)],
    }
    document = _convert([segment])
    assert document.segments[0].words[0].text == "Hallo"


def test_special_tokens_and_empty_text_are_dropped() -> None:
    segment = {
        "offsets": {"from": 0, "to": 500},
        "tokens": [
            _token("[_BEG_]", 0, 0),
            {"text": "", "offsets": {"from": 0, "to": 0}, "p": 0.5},
            _token(" Hallo", 0, 500),
            _token("[_EOT_]", 500, 500),
        ],
    }
    document = _convert([segment])
    words = document.segments[0].words
    assert len(words) == 1
    assert words[0].text == "Hallo"


def test_monotonic_clock_raises_end_by_one_ms_on_duplicate_timestamps() -> None:
    segment = {
        "offsets": {"from": 0, "to": 100},
        "tokens": [
            _token(" eins", 0, 50),
            _token(" zwei", 50, 50),
            _token(" drei", 50, 50),
        ],
    }
    document = _convert([segment])
    words = document.segments[0].words
    assert len(words) == 3
    previous_end = None
    for word in words:
        assert word.start_ms < word.end_ms
        if previous_end is not None:
            assert word.start_ms >= previous_end
        previous_end = word.end_ms
    assert words[1].start_ms == words[0].end_ms
    assert words[2].start_ms == words[1].end_ms


def test_window_offset_shifts_all_times_and_source_duration_is_absolute() -> None:
    segment = {
        "offsets": {"from": 0, "to": 1000},
        "tokens": [_token(" Hallo", 0, 500), _token(" Welt", 500, 1000)],
    }
    document = _convert([segment], source_duration_ms=999_000, window_offset_ms=120_000)
    segment_out = document.segments[0]
    assert segment_out.start_ms == 120_000
    assert segment_out.words[0].start_ms == 120_000
    assert segment_out.words[0].end_ms == 120_500
    assert segment_out.words[1].start_ms == 120_500
    assert segment_out.words[1].end_ms == 121_000
    assert document.source_duration_ms == 999_000


def test_probability_taken_as_minimum_across_word_tokens() -> None:
    segment = {
        "offsets": {"from": 0, "to": 500},
        "tokens": [
            _token(" er", 0, 200, probability=0.9),
            _token("st", 200, 500, probability=0.4),
        ],
    }
    document = _convert([segment])
    assert document.segments[0].words[0].probability == pytest.approx(0.4)


def test_probability_ignores_continuation_token_that_lacks_it() -> None:
    segment = {
        "offsets": {"from": 0, "to": 500},
        "tokens": [
            _token(" er", 0, 200, probability=0.4),
            _token("st", 200, 500, probability=None),
        ],
    }
    document = _convert([segment])
    assert document.segments[0].words[0].probability == pytest.approx(0.4)


def test_segment_without_offsets_falls_back_to_word_bounds() -> None:
    segment = {"tokens": [_token(" Hallo", 0, 500)]}
    document = _convert([segment])
    segment_out = document.segments[0]
    assert segment_out.start_ms == 0
    assert segment_out.end_ms == 500


def test_probability_defaults_when_absent_from_all_tokens() -> None:
    segment = {
        "offsets": {"from": 0, "to": 500},
        "tokens": [_token(" Hallo", 0, 500, probability=None)],
    }
    document = _convert([segment])
    assert document.segments[0].words[0].probability == 1.0


def test_invalid_json_raises_raw_output_missing_error() -> None:
    with pytest.raises(RawOutputMissingError):
        convert_whisper_output(
            "{not json", source_duration_ms=1_000, audio_stream_specifier="0:a:0"
        )


def test_no_usable_tokens_raises_raw_output_empty_error() -> None:
    segment = {"offsets": {"from": 0, "to": 500}, "tokens": [_token("[_BEG_]", 0, 0)]}
    with pytest.raises(RawOutputEmptyError):
        _convert([segment])


def test_no_segments_at_all_raises_raw_output_empty_error() -> None:
    with pytest.raises(RawOutputEmptyError):
        _convert([])


def test_segment_boundaries_widen_to_contain_all_words() -> None:
    segment = {
        "offsets": {"from": 100, "to": 200},
        "tokens": [_token(" Hallo", 0, 300)],
    }
    document = _convert([segment])
    segment_out = document.segments[0]
    assert segment_out.start_ms == 0
    assert segment_out.end_ms == 300
