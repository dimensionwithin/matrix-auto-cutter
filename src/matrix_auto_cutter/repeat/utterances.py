"""Deterministic, word-time-only utterance formation."""

from __future__ import annotations

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument, RepeatWord

_SENTENCE_END_CHARS: tuple[str, ...] = (".", "?", "!")


class UtteranceParams(CanonicalModel):
    """Configurable thresholds for splitting words into utterances.

    ``max_utterance_duration_ms`` defaults to 20000ms. It is purely an
    emergency brake for passages with no sentence punctuation at all, not the
    normal split criterion -- that role belongs to sentence punctuation (see
    ``split_on_sentence_punctuation``). At the previous default of 8000ms it
    still actively cut through the middle of long, punctuated sentences,
    i.e. exactly the passages a repeat/self-correction check needs intact:
    on a real transcript probe that dropped the best candidate pair's score
    from ~0.68 to ~0.45 and no candidate survived. At 20000ms only sentence
    punctuation realistically ever splits an utterance, and the result is
    stable against the exact value chosen.
    """

    max_pause_ms: int = Field(default=700, ge=0)
    max_utterance_duration_ms: int = Field(default=20_000, gt=0)
    split_on_sentence_punctuation: bool = True


class Utterance(CanonicalModel):
    """One deterministically formed utterance and its source words."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str
    words: tuple[RepeatWord, ...] = Field(min_length=1)


def _finalize_utterance(words: list[RepeatWord]) -> Utterance:
    return Utterance(
        start_ms=words[0].start_ms,
        end_ms=words[-1].end_ms,
        text=" ".join(word.text for word in words),
        words=tuple(words),
    )


def build_utterances(
    transcript: RepeatTranscriptDocument,
    params: UtteranceParams | None = None,
) -> tuple[Utterance, ...]:
    """Group transcript words into utterances by sentence punctuation, word pause, and max duration.

    An utterance ends, in order of evaluation: (1) at a word gap above
    ``max_pause_ms``, (2) when appending the next word would exceed
    ``max_utterance_duration_ms``, or (3) immediately after a word whose text
    ends with a sentence-final character (``.``, ``?``, ``!``) when
    ``split_on_sentence_punctuation`` is enabled. The third rule uses only the
    transcript's own punctuation at the end of a word's text -- a period inside
    a word (e.g. an abbreviation) does not trigger it. ``max_pause_ms`` and
    ``max_utterance_duration_ms`` remain in effect regardless, since raw
    recordings and punctuation-free transcripts still need them. No language
    model and no punctuation heuristics beyond what the transcript already
    provides are used.
    """
    active_params = params if params is not None else UtteranceParams()
    words = [word for segment in transcript.segments for word in segment.words]
    utterances: list[Utterance] = []
    current: list[RepeatWord] = []
    for word in words:
        if current:
            gap_ms = word.start_ms - current[-1].end_ms
            duration_if_added_ms = word.end_ms - current[0].start_ms
            if (
                gap_ms > active_params.max_pause_ms
                or duration_if_added_ms > active_params.max_utterance_duration_ms
            ):
                utterances.append(_finalize_utterance(current))
                current = []
        current.append(word)
        if active_params.split_on_sentence_punctuation and word.text.endswith(_SENTENCE_END_CHARS):
            utterances.append(_finalize_utterance(current))
            current = []
    if current:
        utterances.append(_finalize_utterance(current))
    return tuple(utterances)
