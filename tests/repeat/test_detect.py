"""Windowed repeat/self-correction detection. Diagnosis only, never a cut."""

from __future__ import annotations

import json

from tests.repeat.conftest import transcript_dict, utterance_segment

from matrix_auto_cutter.repeat.detect import DetectionParams, detect_repeats
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument


def _document(segments: list[dict[str, object]], duration_ms: int) -> RepeatTranscriptDocument:
    raw = transcript_dict(segments, source_duration_ms=duration_ms)
    return RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_exact_repetition_is_a_candidate() -> None:
    segments = [
        utterance_segment("ich gehe jetzt nach hause", 0),
        utterance_segment("ich gehe jetzt nach hause", 2_000),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.first.text == "ich gehe jetzt nach hause"
    assert candidate.scores.total == 1.0
    assert "score_above_threshold" in candidate.reasons


def test_restart_after_aborted_sentence_is_a_candidate() -> None:
    segments = [
        utterance_segment("ich wollte kurz sagen", 0),
        utterance_segment("also ich wollte kurz sagen dass es klappt", 1_500),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    assert len(result.candidates) == 1
    assert "correction_marker_detected" in result.candidates[0].reasons


def test_number_correction_pair_is_a_candidate_with_visible_diff() -> None:
    segments = [
        utterance_segment("ich habe drei aepfel gekauft", 0),
        utterance_segment("nein ich habe vier aepfel gekauft", 1_800),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    assert len(result.candidates) == 1
    replace_ops = [op for op in result.candidates[0].scores.word_diff if op.kind == "replace"]
    assert any("drei" in op.first_tokens and "vier" in op.second_tokens for op in replace_ops)


def test_negation_correction_pair_is_a_candidate_with_visible_diff() -> None:
    segments = [
        utterance_segment("das ergebnis ist richtig", 0),
        utterance_segment("nein das ergebnis ist nicht richtig", 1_800),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    assert len(result.candidates) == 1
    insert_ops = [op for op in result.candidates[0].scores.word_diff if op.kind == "insert"]
    assert any("nicht" in op.second_tokens for op in insert_ops)


def test_similar_passages_with_too_large_a_gap_are_not_a_candidate() -> None:
    segments = [
        utterance_segment("ich gehe jetzt nach hause", 0),
        utterance_segment("ich gehe jetzt nach hause", 20_000),
    ]
    document = _document(segments, 25_000)
    result = detect_repeats(document, DetectionParams(max_gap_ms=2_000))
    assert result.candidates == ()
    assert result.total_pairs_checked == 0


def test_dissimilar_neighbors_are_not_a_candidate() -> None:
    segments = [
        utterance_segment("der hund laeuft im park", 0),
        utterance_segment("die sonne scheint heute sehr warm", 1_800),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    assert result.candidates == ()
    assert result.total_pairs_checked == 1


def test_candidates_are_sorted_deterministically() -> None:
    segments = [
        utterance_segment("hallo welt heute", 0),
        utterance_segment("hallo welt heute", 1_480),
        utterance_segment("hallo welt heute", 2_960),
        utterance_segment("hallo welt heute", 4_440),
    ]
    document = _document(segments, 10_000)
    result = detect_repeats(document, DetectionParams(max_gap_ms=2_000))
    starts = [candidate.first.start_ms for candidate in result.candidates]
    assert starts == sorted(starts)
    assert result.total_pairs_checked == 3


def test_default_params_are_used_when_none_given() -> None:
    segments = [
        utterance_segment("hallo welt", 0),
        utterance_segment("hallo welt", 1_000),
    ]
    document = _document(segments, 5_000)
    assert detect_repeats(document, None) == detect_repeats(document)


def test_repetition_separated_by_one_short_fragment_is_found() -> None:
    # first ends at 980; the "ähm" fragment (>700ms later) becomes its own
    # utterance, so the repeat is not the fragment's immediate neighbor either
    # way. Adjacent-only pairing (the old itertools.pairwise behavior) would
    # only ever compare (first, fragment) and (fragment, third) and would
    # never compare (first, third) directly.
    segments = [
        utterance_segment("ich gehe jetzt nach hause", 0),
        utterance_segment("ähm", 1_700),
        utterance_segment("ich gehe jetzt nach hause", 2_600),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document)
    repeat_candidates = [
        candidate
        for candidate in result.candidates
        if candidate.first.start_ms == 0 and candidate.second.start_ms == 2_600
    ]
    assert len(repeat_candidates) == 1
    assert repeat_candidates[0].scores.total == 1.0


def test_repetition_separated_by_a_fragment_beyond_max_gap_is_not_a_candidate() -> None:
    # Same shape as above, but the window is tightened so the first-to-third
    # gap (1_620ms) no longer fits; the fragment itself is too dissimilar to
    # either passage to be a candidate on its own.
    segments = [
        utterance_segment("ich gehe jetzt nach hause", 0),
        utterance_segment("ähm", 1_700),
        utterance_segment("ich gehe jetzt nach hause", 2_600),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document, DetectionParams(max_gap_ms=1_500))
    assert result.candidates == ()


def test_pair_leaving_the_window_stops_the_inner_scan_and_is_not_counted() -> None:
    # first (end=980) to fragment (start=1_700): gap 720ms, within the 1_500ms
    # window -> checked. first to third (start=2_600): gap 1_620ms, exceeds the
    # window -> the inner scan for `first` stops there and this pair is not
    # counted. fragment to third: gap 900ms, within the window -> checked.
    # Total: exactly 2 pairs checked, and the far (first, third) pair never
    # appears as a candidate even though the two passages are identical.
    segments = [
        utterance_segment("ich gehe jetzt nach hause", 0),
        utterance_segment("ähm", 1_700),
        utterance_segment("ich gehe jetzt nach hause", 2_600),
    ]
    document = _document(segments, 5_000)
    result = detect_repeats(document, DetectionParams(max_gap_ms=1_500))
    assert result.total_pairs_checked == 2
    assert not any(
        candidate.first.start_ms == 0 and candidate.second.start_ms == 2_600
        for candidate in result.candidates
    )
