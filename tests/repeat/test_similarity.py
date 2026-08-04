"""Similarity scoring, normalization, correction markers, and visible word diff."""

from __future__ import annotations

from matrix_auto_cutter.repeat.similarity import (
    SimilarityParams,
    compute_similarity,
    normalize_text,
)


def test_exact_repetition_scores_at_the_top() -> None:
    score = compute_similarity("Ich gehe jetzt nach Hause", "Ich gehe jetzt nach Hause")
    assert score.ratio == 1.0
    assert score.total == 1.0
    assert "high_fuzzy_ratio" in score.triggered_reasons


def test_dissimilar_texts_score_low() -> None:
    score = compute_similarity("Der Hund läuft im Park", "Die Sonne scheint heute sehr warm")
    assert score.total < 0.55


def test_filler_words_are_removed_but_number_words_are_kept() -> None:
    normalized = normalize_text("also äh zwei drei vier", SimilarityParams().filler_words)
    assert normalized == "zwei drei vier"


def test_number_correction_is_visible_in_the_word_diff() -> None:
    score = compute_similarity("Ich habe drei Äpfel gekauft", "nein, ich habe vier Äpfel gekauft")
    replace_ops = [op for op in score.word_diff if op.kind == "replace"]
    assert any("drei" in op.first_tokens and "vier" in op.second_tokens for op in replace_ops)
    assert score.correction_marker_bonus > 0
    assert "correction_marker_detected" in score.triggered_reasons


def test_negation_correction_is_visible_in_the_word_diff() -> None:
    score = compute_similarity("Das Ergebnis ist richtig", "nein, das Ergebnis ist nicht richtig")
    insert_ops = [op for op in score.word_diff if op.kind == "insert"]
    assert any("nicht" in op.second_tokens for op in insert_ops)


def test_correction_marker_bonus_only_applies_when_second_text_starts_with_marker() -> None:
    with_marker = compute_similarity("Das ist gut", "sorry, das ist gut")
    without_marker = compute_similarity("Das ist gut", "das ist wirklich sorry gut")
    assert with_marker.correction_marker_bonus > 0
    assert without_marker.correction_marker_bonus == 0


def test_total_score_is_clipped_to_one() -> None:
    params = SimilarityParams(correction_marker_bonus=1.0)
    score = compute_similarity("Hallo Welt", "nein Hallo Welt", params)
    assert score.total == 1.0


def test_empty_texts_are_perfectly_similar() -> None:
    score = compute_similarity("", "")
    assert score.ratio == 1.0
    assert score.ngram_similarity == 1.0
    assert score.prefix_similarity == 1.0
    assert score.subsequence_similarity == 1.0


def test_one_sided_empty_text_scores_zero_ngram_and_subsequence() -> None:
    score = compute_similarity("", "hallo welt")
    assert score.ngram_similarity == 0.0
    assert score.subsequence_similarity == 0.0


def test_ngram_similarity_handles_texts_shorter_than_ngram_size() -> None:
    params = SimilarityParams(ngram_size=5)
    score = compute_similarity("ab", "ab")
    assert score.ratio == 1.0
    score_diff = compute_similarity("ab", "cd", params)
    assert score_diff.ngram_similarity == 0.0


def test_custom_filler_and_correction_marker_lists_are_respected() -> None:
    params = SimilarityParams(filler_words=("blah",), correction_markers=("actually wait",))
    normalized = normalize_text("blah hello blah world", params.filler_words)
    assert normalized == "hello world"
    score = compute_similarity("Hello world", "actually wait hello world", params)
    assert score.correction_marker_bonus == params.correction_marker_bonus
