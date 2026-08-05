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


def test_leading_correction_marker_is_stripped_before_scoring() -> None:
    # "nein," is not a filler word, so before the marker was stripped from the
    # text going into ratio/ngram/prefix/subsequence it dragged all four scores
    # down for what is otherwise a near-identical repetition. Stripping exactly
    # one leading marker leaves a remainder identical to the marker-free text.
    first = "Ich habe drei Aepfel gekauft"
    second_with_marker = "Nein, ich habe vier Aepfel gekauft"
    second_without_marker = "ich habe vier Aepfel gekauft"
    with_marker = compute_similarity(first, second_with_marker)
    without_marker_reference = compute_similarity(first, second_without_marker)
    assert with_marker.ratio == without_marker_reference.ratio
    assert with_marker.ngram_similarity == without_marker_reference.ngram_similarity
    assert with_marker.prefix_similarity == without_marker_reference.prefix_similarity
    assert with_marker.subsequence_similarity == without_marker_reference.subsequence_similarity
    # The marker bonus is the only difference in the total: this pair is a
    # verified self-correction (marker detected) getting a strictly higher
    # score than an unmarked, otherwise-identical repetition would.
    assert with_marker.total == without_marker_reference.total + with_marker.correction_marker_bonus
    assert with_marker.correction_marker_bonus > 0
    # Before this change, "Nein, " was left in the scored text and this exact
    # pair scored total=0.637 (ratio dragged down to ~0.66 by the unstripped
    # marker). Stripping the marker before scoring raises it to 0.775.
    assert round(without_marker_reference.total, 3) == 0.725
    assert round(with_marker.total, 3) == 0.775
    assert with_marker.total > 0.637


def test_leading_correction_marker_stripping_preserves_the_raw_word_diff() -> None:
    score = compute_similarity(
        "Ich habe die Zahlen fuer Q3 fertig",
        "Nein, ich meine, ich habe die Zahlen fuer Q3 fertig",
    )
    all_second_tokens = [token for op in score.word_diff for token in op.second_tokens]
    assert "Nein," in all_second_tokens
    assert "meine," in all_second_tokens


def test_correction_marker_boundary_requires_a_word_break() -> None:
    # "nein" must not match inside a longer word like "neinerlei".
    score = compute_similarity("Das ist gut", "neinerlei sache, das ist gut")
    assert score.correction_marker_bonus == 0


def test_also_leading_marker_is_stripped_exactly_once() -> None:
    # "also" is both a filler word and a correction marker. Stripping the
    # leading marker instance must not interact badly with filler removal of
    # a later, unrelated "also" in the same text.
    score = compute_similarity("wir sind dann fertig", "also, wir sind also dann fertig")
    normalized = normalize_text("wir sind also dann fertig", SimilarityParams().filler_words)
    assert normalized == "wir sind dann fertig"
    assert score.correction_marker_bonus > 0
    assert score.ratio == 1.0
