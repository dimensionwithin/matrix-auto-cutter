"""Second, independent detector: short echoes at an utterance boundary."""

from __future__ import annotations

import json

import pytest
from rapidfuzz import fuzz
from tests.repeat.conftest import transcript_dict, utterance_segment

from matrix_auto_cutter.repeat.boundary import (
    BoundaryDetectionParams,
    detect_boundary_echoes,
)
from matrix_auto_cutter.repeat.similarity import normalize_text
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument

# utterances.build_utterances splits into a new utterance once the gap between
# consecutive words exceeds max_pause_ms (default 700ms). A gap of 900ms keeps
# two segments as separate utterances while staying inside the default
# max_gap_ms (2_000ms) the boundary detector's own pairing window uses.
_UTTERANCE_GAP_MS = 900


def _segment_end_ms(text: str, start_ms: int, step_ms: int = 200) -> int:
    word_count = len(text.split())
    return start_ms + word_count * step_ms - 20


def _document(segments: list[dict[str, object]], duration_ms: int) -> RepeatTranscriptDocument:
    raw = transcript_dict(segments, source_duration_ms=duration_ms)
    return RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def _two_utterance_document(
    first_text: str, second_text: str, duration_ms: int
) -> RepeatTranscriptDocument:
    second_start_ms = _segment_end_ms(first_text, 0) + _UTTERANCE_GAP_MS
    segments = [
        utterance_segment(first_text, 0),
        utterance_segment(second_text, second_start_ms),
    ]
    return _document(segments, duration_ms)


def test_echo_at_boundary_is_found() -> None:
    document = _two_utterance_document(
        "wir haben das erreicht in dem sinne", "in dem sinne war das gut", 5_000
    )
    result = detect_boundary_echoes(document)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.first_window_text == "in dem sinne"
    assert candidate.second_window_text == "in dem sinne"
    assert candidate.score == 1.0
    assert candidate.window_words == 3
    assert "boundary_echo_detected" in candidate.reasons


def test_utterance_shorter_than_min_window_words_skips_the_pair() -> None:
    document = _two_utterance_document("hallo welt", "welt ist schoen heute wieder", 5_000)
    result = detect_boundary_echoes(document, BoundaryDetectionParams(min_window_words=3))
    assert result.candidates == ()
    assert result.total_pairs_checked == 1


def test_winning_window_width_is_chosen_correctly() -> None:
    # last4(first)="start zzz yyy xxx wort"[-4:] = "zzz yyy xxx wort" is an exact
    # match against first4(second) = "zzz yyy xxx wort" -> n=4 scores 1.0.
    # n=3 ("yyy xxx wort" vs "zzz yyy xxx") and n=5 (whole utterances, which
    # differ at the padding word) both score below 1.0, so the winner must be
    # n=4 -- proving the search actually spans the full window range and picks
    # the best score, not just the first or the last one tried.
    document = _two_utterance_document("start zzz yyy xxx wort", "zzz yyy xxx wort ende", 5_000)
    result = detect_boundary_echoes(document)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.window_words == 4
    assert candidate.score == 1.0
    assert candidate.first_window_text == "zzz yyy xxx wort"
    assert candidate.second_window_text == "zzz yyy xxx wort"


def test_score_threshold_narrowly_excludes() -> None:
    first_words = ("aaa", "bbb", "ccc")
    second_words = ("aaa", "bbb", "ddd")
    expected_score = (
        fuzz.ratio(
            normalize_text(" ".join(first_words), ()),
            normalize_text(" ".join(second_words), ()),
        )
        / 100.0
    )
    document = _two_utterance_document(" ".join(first_words), " ".join(second_words), 5_000)

    excluding_params = BoundaryDetectionParams(score_threshold=expected_score + 0.0001)
    excluded = detect_boundary_echoes(document, excluding_params)
    assert excluded.candidates == ()

    including_params = BoundaryDetectionParams(score_threshold=expected_score - 0.0001)
    included = detect_boundary_echoes(document, including_params)
    assert len(included.candidates) == 1
    assert included.candidates[0].score == expected_score


def test_gap_beyond_max_gap_ms_is_not_checked() -> None:
    segments = [
        utterance_segment("in dem sinne", 0),
        utterance_segment("in dem sinne", 20_000),
    ]
    document = _document(segments, 25_000)
    result = detect_boundary_echoes(document, BoundaryDetectionParams(max_gap_ms=2_000))
    assert result.candidates == ()
    assert result.total_pairs_checked == 0


def test_no_filler_word_removal_unlike_similarity_normalization() -> None:
    # "also" is a filler word removed by similarity.normalize_text's default
    # filler list, but the boundary detector must not remove it -- passing
    # filler_words=() to normalize_text is exactly what makes that so.
    document = _two_utterance_document("wir sind also fertig", "also fertig sind wir jetzt", 5_000)
    result = detect_boundary_echoes(document, BoundaryDetectionParams(min_window_words=2))
    assert len(result.candidates) == 1
    assert "also" in result.candidates[0].first_window_text.lower().split()


def test_default_params_are_used_when_none_given() -> None:
    document = _two_utterance_document("in dem sinne", "in dem sinne", 5_000)
    assert detect_boundary_echoes(document, None) == detect_boundary_echoes(document)


def test_boundary_detection_params_rejects_min_greater_than_max() -> None:
    with pytest.raises(ValueError, match="min_window_words"):
        BoundaryDetectionParams(min_window_words=5, max_window_words=3)


def test_candidates_are_sorted_deterministically() -> None:
    text = "in dem sinne"
    starts = [0]
    for _ in range(3):
        starts.append(_segment_end_ms(text, starts[-1]) + _UTTERANCE_GAP_MS)
    segments = [utterance_segment(text, start) for start in starts]
    document = _document(segments, 10_000)
    result = detect_boundary_echoes(document, BoundaryDetectionParams(max_gap_ms=2_000))
    assert result.total_pairs_checked == 3
    found_starts = [candidate.first.start_ms for candidate in result.candidates]
    assert found_starts == sorted(found_starts)
