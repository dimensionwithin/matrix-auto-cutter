"""Tests fuer Teil B des Auftrags shorts-stufe-4: Untertitelzeilen aus Woertern bilden."""

from __future__ import annotations

import itertools
import json

import pytest

from matrix_auto_cutter.shorts.subtitle_lines import (
    MAX_CHARS_PER_LINE,
    MAX_GAP_MS,
    MAX_WORDS_PER_LINE,
    SubtitleLine,
    SubtitleWordTimingError,
    Word,
    build_subtitle_lines,
    words_from_whisper_json,
)


def _word(start_ms: int, end_ms: int, text: str) -> Word:
    return Word(start_ms, end_ms, text)


def test_word_rejects_end_before_start() -> None:
    with pytest.raises(SubtitleWordTimingError):
        Word(1000, 900, "x")


def test_build_subtitle_lines_empty_input() -> None:
    assert build_subtitle_lines([]) == []


def test_build_subtitle_lines_splits_at_three_words() -> None:
    words = [
        _word(0, 100, "eins"),
        _word(100, 200, "zwei"),
        _word(200, 300, "drei"),
        _word(300, 400, "vier"),
    ]
    lines = build_subtitle_lines(words)
    assert len(lines) == 2
    assert lines[0] == SubtitleLine(0, 300, tuple(words[:3]))
    assert lines[1] == SubtitleLine(300, 400, tuple(words[3:]))


def test_build_subtitle_lines_splits_on_char_limit() -> None:
    words = [_word(0, 100, "zwoelfzeichenlg"), _word(100, 200, "zwoelfzeichenl2")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 2
    assert len(lines[0].text) <= MAX_CHARS_PER_LINE
    assert len(lines[1].text) <= MAX_CHARS_PER_LINE


def test_build_subtitle_lines_splits_on_gap() -> None:
    gap_start = 100 + MAX_GAP_MS + 1
    words = [_word(0, 100, "eins"), _word(gap_start, gap_start + 200, "zwei")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 2
    assert lines[0] == SubtitleLine(0, 100, (words[0],))
    assert lines[1] == SubtitleLine(gap_start, gap_start + 200, (words[1],))


def test_build_subtitle_lines_does_not_split_within_gap_limit() -> None:
    gap_start = 100 + MAX_GAP_MS
    words = [_word(0, 100, "eins"), _word(gap_start, gap_start + 200, "zwei")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 1


def test_build_subtitle_lines_splits_after_sentence_end() -> None:
    words = [_word(0, 100, "Satz."), _word(100, 200, "Naechster")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 2
    assert lines[0] == SubtitleLine(0, 100, (words[0],))
    assert lines[1] == SubtitleLine(100, 200, (words[1],))


@pytest.mark.parametrize("punct", [".", "!", "?"])
def test_build_subtitle_lines_recognizes_all_sentence_end_chars(punct: str) -> None:
    words = [_word(0, 100, f"Satz{punct}"), _word(100, 200, "Weiter")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 2


def test_build_subtitle_lines_comma_does_not_force_break() -> None:
    words = [_word(0, 100, "eins,"), _word(100, 200, "zwei")]
    lines = build_subtitle_lines(words)
    assert len(lines) == 1


def test_build_subtitle_lines_never_overlap_and_leave_gap_empty() -> None:
    words = [
        _word(0, 100, "eins"),
        _word(100, 200, "zwei"),
        _word(200, 300, "drei"),
        _word(1000, 1100, "vier"),
    ]
    lines = build_subtitle_lines(words)
    for earlier, later in itertools.pairwise(lines):
        assert earlier.end_ms <= later.start_ms


def test_build_subtitle_lines_respects_all_limits_at_once() -> None:
    words = [
        _word(0, 100, "eins"),
        _word(100, 200, "zwei"),
        _word(200, 300, "drei"),
        _word(300, 400, "vier"),
        _word(400, 500, "fuenf"),
    ]
    for line in build_subtitle_lines(words):
        assert len(line.words) <= MAX_WORDS_PER_LINE
        assert len(line.text) <= MAX_CHARS_PER_LINE


def _whisper_json(tokens: list[dict[str, object]]) -> str:
    return json.dumps({"transcription": [{"tokens": tokens}]})


def _token(text: str, start_ms: int, end_ms: int) -> dict[str, object]:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


def test_words_from_whisper_json_merges_subword_tokens() -> None:
    raw = _whisper_json(
        [
            _token("[_BEG_]", 0, 0),
            _token(" Bere", 870, 1090),
            _token("iche", 1090, 1350),
            _token(" dort", 1350, 1580),
        ]
    )
    words = words_from_whisper_json(raw)
    assert words == [Word(870, 1350, "Bereiche"), Word(1350, 1580, "dort")]


def test_words_from_whisper_json_attaches_punctuation_without_leading_space() -> None:
    raw = _whisper_json(
        [
            _token(" gesprochen", 6560, 7020),
            _token(",", 7020, 7060),
            _token(" 74", 7120, 7380),
        ]
    )
    words = words_from_whisper_json(raw)
    assert words == [Word(6560, 7060, "gesprochen,"), Word(7120, 7380, "74")]


def test_words_from_whisper_json_skips_special_tokens_only() -> None:
    raw = _whisper_json(
        [
            _token("[_BEG_]", 0, 0),
            _token(" Wort", 10, 100),
            _token("[_TT_50]", 100, 100),
        ]
    )
    words = words_from_whisper_json(raw)
    assert words == [Word(10, 100, "Wort")]


def test_words_from_whisper_json_fails_closed_on_missing_offsets() -> None:
    raw = json.dumps(
        {"transcription": [{"tokens": [{"text": " Wort"}]}]}
    )
    with pytest.raises(SubtitleWordTimingError):
        words_from_whisper_json(raw)


def test_words_from_whisper_json_fails_closed_on_end_before_start() -> None:
    raw = _whisper_json([_token(" Wort", 500, 100)])
    with pytest.raises(SubtitleWordTimingError):
        words_from_whisper_json(raw)


def test_words_from_whisper_json_empty_transcription() -> None:
    assert words_from_whisper_json(json.dumps({"transcription": []})) == []
