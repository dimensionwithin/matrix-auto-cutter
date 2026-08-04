"""Strict validation of the repeat_transcript/1.0 input contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.repeat.conftest import segment, transcript_dict, word

from matrix_auto_cutter.repeat.errors import RepeatContractError
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument, load_transcript


def test_valid_transcript_round_trips() -> None:
    raw = transcript_dict(
        [
            segment(0, 3_120, [word(0, 240, "Der", 0.91), word(240, 600, "Hund", 0.88)]),
            segment(3_500, 4_000, [word(3_500, 4_000, "läuft", 0.9)]),
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    assert document.artifact_type == "matrix_auto_cutter_repeat_transcript"
    assert len(document.segments) == 2
    assert document.segments[0].words[0].text == "Der"


def test_unknown_top_level_field_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    raw["unexpected"] = True
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_unknown_word_field_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    raw["segments"][0]["words"][0]["extra"] = 1
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_wrong_artifact_type_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    raw["artifact_type"] = "something_else"
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_wrong_schema_version_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    raw["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_empty_audio_stream_specifier_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])], audio_stream_specifier="")
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_non_positive_source_duration_is_rejected() -> None:
    raw = transcript_dict([], source_duration_ms=0)
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_word_probability_out_of_range_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi", probability=1.5)])])
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_word_start_not_before_end_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(50, 50, "Hi")])])
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_segment_start_not_before_end_is_rejected() -> None:
    raw = transcript_dict([segment(100, 50, [])], source_duration_ms=200)
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_word_before_segment_start_is_rejected() -> None:
    raw = transcript_dict([segment(50, 100, [word(0, 40, "Hi")])], source_duration_ms=200)
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_word_after_segment_end_is_rejected() -> None:
    raw = transcript_dict([segment(0, 100, [word(50, 150, "Hi")])], source_duration_ms=200)
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_overlapping_words_within_segment_are_rejected() -> None:
    raw = transcript_dict(
        [segment(0, 100, [word(0, 60, "Hi"), word(50, 90, "there")])],
        source_duration_ms=200,
    )
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_segment_exceeding_source_duration_is_rejected() -> None:
    raw = transcript_dict([segment(0, 300, [])], source_duration_ms=200)
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_overlapping_segments_are_rejected() -> None:
    raw = transcript_dict(
        [segment(0, 100, []), segment(50, 150, [])],
        source_duration_ms=200,
    )
    with pytest.raises(ValidationError):
        RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_load_transcript_reads_valid_file(tmp_path: Path) -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    target = tmp_path / "transcript.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    document = load_transcript(target)
    assert document.segments[0].words[0].text == "Hi"


def test_load_transcript_missing_file_raises_contract_error(tmp_path: Path) -> None:
    with pytest.raises(RepeatContractError):
        load_transcript(tmp_path / "missing.json")


def test_load_transcript_invalid_json_raises_contract_error(tmp_path: Path) -> None:
    target = tmp_path / "transcript.json"
    target.write_text("not json", encoding="utf-8")
    with pytest.raises(RepeatContractError):
        load_transcript(target)


def test_load_transcript_contract_violation_raises_contract_error(tmp_path: Path) -> None:
    raw = transcript_dict([segment(0, 100, [])])
    raw["schema_version"] = "9.9"
    target = tmp_path / "transcript.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RepeatContractError):
        load_transcript(target)
