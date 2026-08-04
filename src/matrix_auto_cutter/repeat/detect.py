"""Detection of adjacent repeats and self-corrections. Diagnosis only, never a cut."""

from __future__ import annotations

from itertools import pairwise

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.similarity import (
    SimilarityParams,
    SimilarityScore,
    compute_similarity,
)
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument
from matrix_auto_cutter.repeat.utterances import UtteranceParams, build_utterances


class DetectionParams(CanonicalModel):
    """Configurable thresholds for adjacent-pair repeat detection."""

    max_gap_ms: int = Field(default=2_000, ge=0)
    score_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    utterance_params: UtteranceParams = Field(default_factory=UtteranceParams)
    similarity_params: SimilarityParams = Field(default_factory=SimilarityParams)


class UtteranceSpan(CanonicalModel):
    """Minimal utterance identity carried into a diagnosed candidate."""

    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)


class RepeatCandidate(CanonicalModel):
    """One diagnosed adjacent repeat/self-correction pair."""

    first: UtteranceSpan
    second: UtteranceSpan
    gap_ms: int = Field(ge=0)
    scores: SimilarityScore
    reasons: tuple[str, ...]


class DetectionResult(CanonicalModel):
    """Sorted candidates plus the total number of adjacent pairs actually compared."""

    candidates: tuple[RepeatCandidate, ...]
    total_pairs_checked: int = Field(ge=0)


def detect_repeats(
    transcript: RepeatTranscriptDocument,
    params: DetectionParams | None = None,
) -> DetectionResult:
    """Compare only time-adjacent utterance pairs; never a global or vector search.

    A pair is checked (counted toward ``total_pairs_checked``) only when the gap
    between the end of the first utterance and the start of the second is at most
    ``max_gap_ms``. Among checked pairs, a candidate is produced only when the
    combined similarity score reaches ``score_threshold``.
    """
    active_params = params if params is not None else DetectionParams()
    utterances = build_utterances(transcript, active_params.utterance_params)
    candidates: list[RepeatCandidate] = []
    checked = 0
    for first, second in pairwise(utterances):
        gap_ms = second.start_ms - first.end_ms
        if gap_ms > active_params.max_gap_ms:
            continue
        checked += 1
        scores = compute_similarity(first.text, second.text, active_params.similarity_params)
        if scores.total < active_params.score_threshold:
            continue
        reasons = ("score_above_threshold", *scores.triggered_reasons)
        candidates.append(
            RepeatCandidate(
                first=UtteranceSpan(text=first.text, start_ms=first.start_ms, end_ms=first.end_ms),
                second=UtteranceSpan(
                    text=second.text,
                    start_ms=second.start_ms,
                    end_ms=second.end_ms,
                ),
                gap_ms=gap_ms,
                scores=scores,
                reasons=reasons,
            )
        )
    ordered = tuple(
        sorted(
            candidates, key=lambda candidate: (candidate.first.start_ms, candidate.second.start_ms)
        )
    )
    return DetectionResult(candidates=ordered, total_pairs_checked=checked)
