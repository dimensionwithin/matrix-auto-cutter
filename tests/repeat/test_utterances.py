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
