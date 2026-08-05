"""Deterministic utterance formation from word-time pauses and duration caps."""

from __future__ import annotations

import json

from tests.repeat.conftest import segment, transcript_dict, word

from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument
from matrix_auto_cutter.repeat.utterances import UtteranceParams, build_utterances


def test_single_utterance_when_pauses_are_small() -> None:
    raw = transcript_dict(
        [segment(0, 900, [word(0, 200, "Der"), word(250, 500, "Hund"), word(550, 900, "läuft")])]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert len(utterances) == 1
    assert utterances[0].text == "Der Hund läuft"
    assert utterances[0].start_ms == 0
    assert utterances[0].end_ms == 900


def test_pause_above_threshold_splits_utterances() -> None:
    raw = transcript_dict(
        [
            segment(0, 200, [word(0, 200, "Hallo")]),
            segment(1_500, 1_700, [word(1_500, 1_700, "Welt")]),
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document, UtteranceParams(max_pause_ms=700))
    assert [u.text for u in utterances] == ["Hallo", "Welt"]


def test_max_duration_splits_utterances_even_without_a_pause() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                1_000,
                [word(0, 400, "eins"), word(400, 700, "zwei"), word(700, 1_000, "drei")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(
        document, UtteranceParams(max_pause_ms=10_000, max_utterance_duration_ms=600)
    )
    assert [u.text for u in utterances] == ["eins", "zwei drei"]


def test_empty_transcript_produces_no_utterances() -> None:
    raw = transcript_dict([], source_duration_ms=1_000)
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    assert build_utterances(document) == ()


def test_default_params_are_used_when_none_given() -> None:
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hallo")])])
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    assert build_utterances(document, None) == build_utterances(document)


def test_sentence_final_period_splits_even_with_zero_pause() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                600,
                [word(0, 200, "Hallo."), word(200, 400, "Welt"), word(400, 600, "heute")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["Hallo.", "Welt heute"]


def test_sentence_final_question_and_exclamation_marks_split() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                600,
                [word(0, 200, "Wirklich?"), word(200, 400, "Ja!"), word(400, 600, "gut")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["Wirklich?", "Ja!", "gut"]


def test_mid_word_period_without_trailing_punctuation_does_not_split() -> None:
    # An abbreviation-style period sits mid-word, not at the end, so it must
    # not trigger a split -- only a sentence-final position at the word's end
    # counts.
    raw = transcript_dict(
        [
            segment(
                0,
                600,
                [word(0, 200, "Nr.5"), word(200, 400, "ist"), word(400, 600, "frei")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["Nr.5 ist frei"]


def test_split_on_sentence_punctuation_disabled_restores_old_behavior() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                600,
                [word(0, 200, "Hallo."), word(200, 400, "Welt"), word(400, 600, "heute")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document, UtteranceParams(split_on_sentence_punctuation=False))
    assert len(utterances) == 1
    assert utterances[0].text == "Hallo. Welt heute"


def test_max_duration_still_splits_a_long_passage_without_punctuation() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                1_000,
                [word(0, 400, "eins"), word(400, 700, "zwei"), word(700, 1_000, "drei")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(
        document, UtteranceParams(max_pause_ms=10_000, max_utterance_duration_ms=600)
    )
    assert [u.text for u in utterances] == ["eins", "zwei drei"]


def test_default_max_utterance_duration_is_20000ms() -> None:
    assert UtteranceParams().max_utterance_duration_ms == 20_000


def test_a_long_punctuated_sentence_under_the_cap_stays_a_single_utterance() -> None:
    # ~14s sentence with a trailing period, one word every 1150ms (gap 670ms,
    # under max_pause_ms): with the default 20000ms cap the duration check
    # never fires mid-sentence, so only the sentence-final period ends the
    # utterance -- exactly the shape of utterance the repeat detector needs
    # intact to score a candidate.
    tokens = [
        "Ich",
        "denke",
        "das",
        "koennte",
        "heute",
        "noch",
        "spannend",
        "werden",
        "wenn",
        "wir",
        "das",
        "genau",
        "beobachten.",
    ]
    words = []
    cursor = 0
    for token in tokens:
        words.append(word(cursor, cursor + 480, token))
        cursor += 1_150
    raw = transcript_dict([segment(0, words[-1]["end_ms"], words)])
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert len(utterances) == 1
    assert utterances[0].text == " ".join(tokens)
    total_duration_ms = utterances[0].end_ms - utterances[0].start_ms
    assert 13_000 < total_duration_ms < 15_000
    assert total_duration_ms < UtteranceParams().max_utterance_duration_ms


def test_ordinal_period_does_not_split_the_utterance() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                800,
                [
                    word(0, 200, "am"),
                    word(200, 400, "2."),
                    word(400, 800, "September"),
                ],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["am 2. September"]


def test_sentence_final_period_after_word_still_splits() -> None:
    raw = transcript_dict(
        [
            segment(
                0,
                600,
                [word(0, 200, "Ende."), word(200, 400, "Neuer"), word(400, 600, "Satz")],
            )
        ]
    )
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["Ende.", "Neuer Satz"]


def test_ordinal_period_at_utterance_end_without_following_word_does_not_crash() -> None:
    raw = transcript_dict([segment(0, 400, [word(0, 200, "am"), word(200, 400, "2.")])])
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    utterances = build_utterances(document)
    assert [u.text for u in utterances] == ["am 2."]


def test_max_duration_cap_still_splits_a_long_unpunctuated_passage() -> None:
    # Over 20s with no sentence punctuation anywhere: the emergency brake
    # must still fire since sentence punctuation never will.
    tokens = [f"wort{i}" for i in range(50)]
    words = []
    cursor = 0
    for token in tokens:
        words.append(word(cursor, cursor + 450, token))
        cursor += 500
    raw = transcript_dict([segment(0, words[-1]["end_ms"], words)])
    document = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    total_duration_ms = words[-1]["end_ms"] - words[0]["start_ms"]
    assert total_duration_ms > 20_000
    utterances = build_utterances(document)
    assert len(utterances) > 1
    assert all(u.end_ms - u.start_ms <= 20_000 for u in utterances)
