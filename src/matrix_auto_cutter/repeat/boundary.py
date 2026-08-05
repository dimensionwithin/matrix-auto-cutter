"""Second, independent detector: short echoes carried across an utterance boundary.

Unlike the whole-utterance detector in ``detect.py``, this one only ever
compares a short trailing window of one utterance against a short leading
window of the next -- the pattern seen when a phrase gets echoed right
across a cut, not restated as a full sentence.
"""

from __future__ import annotations

from pydantic import Field, model_validator
from rapidfuzz import fuzz

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.detect import UtteranceSpan
from matrix_auto_cutter.repeat.similarity import normalize_text
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument
from matrix_auto_cutter.repeat.utterances import Utterance, UtteranceParams, build_utterances

DEFAULT_MIN_WINDOW_WORDS = 3
DEFAULT_MAX_WINDOW_WORDS = 8
DEFAULT_SCORE_THRESHOLD = 0.70
DEFAULT_MAX_GAP_MS = 2_000

_NO_FILLER_WORDS: tuple[str, ...] = ()


class BoundaryDetectionParams(CanonicalModel):
    """Configurable thresholds for the boundary-echo detector.

    Defaults were measured on ONE real sample (window 2:00-5:00 of a real
    recording): the two confirmed echoes scored 1.000 and 0.811, the
    closest non-match scored 0.545. ``min_window_words=3`` is load-bearing,
    not a round-number guess: at ``min_window_words=2`` that same
    non-match rises to 0.720 and would clear ``score_threshold=0.70``.
    These values come from a single sample and are explicitly provisional.
    """

    max_gap_ms: int = Field(default=DEFAULT_MAX_GAP_MS, ge=0)
    min_window_words: int = Field(default=DEFAULT_MIN_WINDOW_WORDS, ge=1)
    max_window_words: int = Field(default=DEFAULT_MAX_WINDOW_WORDS, ge=1)
    score_threshold: float = Field(default=DEFAULT_SCORE_THRESHOLD, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered_window(self) -> BoundaryDetectionParams:
        """Reject a window range where the minimum exceeds the maximum."""
        if self.min_window_words > self.max_window_words:
            msg = "min_window_words darf max_window_words nicht überschreiten."
            raise ValueError(msg)
        return self


class BoundaryCandidate(CanonicalModel):
    """One diagnosed boundary echo: the winning window, its width, and its score."""

    first: UtteranceSpan
    second: UtteranceSpan
    gap_ms: int = Field(ge=0)
    score: float = Field(ge=0.0, le=1.0)
    window_words: int = Field(ge=1)
    first_window_text: str
    second_window_text: str
    reasons: tuple[str, ...]


class BoundaryDetectionResult(CanonicalModel):
    """Sorted boundary candidates plus the total number of utterance pairs checked."""

    candidates: tuple[BoundaryCandidate, ...]
    total_pairs_checked: int = Field(ge=0)


def _window_score(first_words: tuple[str, ...], second_words: tuple[str, ...]) -> float:
    first_text = " ".join(first_words)
    second_text = " ".join(second_words)
    normalized_first = normalize_text(first_text, _NO_FILLER_WORDS)
    normalized_second = normalize_text(second_text, _NO_FILLER_WORDS)
    return fuzz.ratio(normalized_first, normalized_second) / 100.0


def _best_window(
    first: Utterance,
    second: Utterance,
    params: BoundaryDetectionParams,
) -> tuple[int, float, str, str] | None:
    max_n = min(params.max_window_words, len(first.words), len(second.words))
    if max_n < params.min_window_words:
        return None
    best: tuple[int, float, str, str] | None = None
    for n in range(params.min_window_words, max_n + 1):
        first_words = tuple(word.text for word in first.words[-n:])
        second_words = tuple(word.text for word in second.words[:n])
        score = _window_score(first_words, second_words)
        if best is None or score > best[1]:
            best = (n, score, " ".join(first_words), " ".join(second_words))
    return best


def detect_boundary_echoes(
    transcript: RepeatTranscriptDocument,
    params: BoundaryDetectionParams | None = None,
    utterance_params: UtteranceParams | None = None,
) -> BoundaryDetectionResult:
    """Compare only short trailing/leading word windows of nearby utterance pairs.

    Uses the exact same windowed-pair iteration as ``detect.detect_repeats``
    (same ``max_gap_ms`` semantics, same early ``break`` once a later
    utterance falls outside the window) so both detectors see the same set
    of candidate pairs. ``utterance_params`` should be the same value the
    whole-utterance detector was run with, so both detectors diagnose
    literally the same utterances -- ``build_utterances`` is pure and
    deterministic, so passing matching params reproduces the identical list
    without the two detectors needing to share Python state.
    """
    active_params = params if params is not None else BoundaryDetectionParams()
    utterances = build_utterances(transcript, utterance_params)
    candidates: list[BoundaryCandidate] = []
    checked = 0
    for index, first in enumerate(utterances):
        for second in utterances[index + 1 :]:
            gap_ms = second.start_ms - first.end_ms
            if gap_ms > active_params.max_gap_ms:
                break
            checked += 1
            best = _best_window(first, second, active_params)
            if best is None:
                continue
            window_words, score, first_window_text, second_window_text = best
            if score < active_params.score_threshold:
                continue
            candidates.append(
                BoundaryCandidate(
                    first=UtteranceSpan(
                        text=first.text, start_ms=first.start_ms, end_ms=first.end_ms
                    ),
                    second=UtteranceSpan(
                        text=second.text, start_ms=second.start_ms, end_ms=second.end_ms
                    ),
                    gap_ms=gap_ms,
                    score=score,
                    window_words=window_words,
                    first_window_text=first_window_text,
                    second_window_text=second_window_text,
                    reasons=("boundary_echo_detected",),
                )
            )
    ordered = tuple(
        sorted(
            candidates, key=lambda candidate: (candidate.first.start_ms, candidate.second.start_ms)
        )
    )
    return BoundaryDetectionResult(candidates=ordered, total_pairs_checked=checked)
