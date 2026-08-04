"""Deterministic, word-time-only utterance formation."""

from __future__ import annotations

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument, RepeatWord


class UtteranceParams(CanonicalModel):
    """Configurable thresholds for splitting words into utterances."""

    max_pause_ms: int = Field(default=700, ge=0)
    max_utterance_duration_ms: int = Field(default=15_000, gt=0)


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
    """Group transcript words into utterances by word pause and max duration.

    An utterance ends at a word gap above ``max_pause_ms`` or when appending the
    next word would exceed ``max_utterance_duration_ms``. No language model and no
    punctuation heuristics beyond what the transcript already provides are used.
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
    if current:
        utterances.append(_finalize_utterance(current))
    return tuple(utterances)
