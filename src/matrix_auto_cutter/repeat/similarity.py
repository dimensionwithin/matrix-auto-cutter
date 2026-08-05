"""Deterministic similarity scoring and visible word diff for utterance pairs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Literal

from pydantic import Field
from rapidfuzz import fuzz

from matrix_auto_cutter.models import CanonicalModel

_DEFAULT_FILLER_WORDS: tuple[str, ...] = (
    "ähm",
    "äh",
    "also",
    "halt",
    "quasi",
    "sozusagen",
    "irgendwie",
    "eigentlich",
    "um",
    "uh",
    "like",
    "basically",
    "actually",
)
_DEFAULT_CORRECTION_MARKERS: tuple[str, ...] = (
    "nein",
    "also",
    "ich meine",
    "sorry",
    "entschuldigung",
    "i mean",
    "no wait",
)

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")

_RATIO_WEIGHT = 0.4
_NGRAM_WEIGHT = 0.2
_PREFIX_WEIGHT = 0.2
_SUBSEQUENCE_WEIGHT = 0.2


class SimilarityParams(CanonicalModel):
    """Configurable inputs to the deterministic similarity combination."""

    ngram_size: int = Field(default=3, ge=1, le=8)
    correction_marker_bonus: float = Field(default=0.05, ge=0.0, le=1.0)
    filler_words: tuple[str, ...] = Field(default=_DEFAULT_FILLER_WORDS)
    correction_markers: tuple[str, ...] = Field(default=_DEFAULT_CORRECTION_MARKERS)


class WordDiffOp(CanonicalModel):
    """One opcode of a word-level diff that preserves original tokens."""

    kind: Literal["equal", "insert", "delete", "replace"]
    first_tokens: tuple[str, ...]
    second_tokens: tuple[str, ...]


class SimilarityScore(CanonicalModel):
    """Individual scores, documented total, triggered reasons, and word diff."""

    ratio: float = Field(ge=0.0, le=1.0)
    ngram_similarity: float = Field(ge=0.0, le=1.0)
    prefix_similarity: float = Field(ge=0.0, le=1.0)
    subsequence_similarity: float = Field(ge=0.0, le=1.0)
    correction_marker_bonus: float = Field(ge=0.0, le=1.0)
    total: float = Field(ge=0.0, le=1.0)
    triggered_reasons: tuple[str, ...]
    word_diff: tuple[WordDiffOp, ...]


def normalize_text(text: str, filler_words: Sequence[str]) -> str:
    """Lowercase, strip punctuation, and drop filler words; number words are kept as-is."""
    lowered = text.lower()
    stripped = _PUNCTUATION_PATTERN.sub(" ", lowered)
    tokens = [token for token in _WHITESPACE_PATTERN.split(stripped) if token]
    filler_set = {word.lower() for word in filler_words}
    filtered = [token for token in tokens if token not in filler_set]
    return " ".join(filtered)


def _char_ngrams(text: str, size: int) -> set[str]:
    if len(text) < size:
        return {text} if text else set()
    return {text[index : index + size] for index in range(len(text) - size + 1)}


def _jaccard(first: set[str], second: set[str]) -> float:
    if not first and not second:
        return 1.0
    if not first or not second:
        return 0.0
    return len(first & second) / len(first | second)


def _common_prefix_ratio(first: str, second: str) -> float:
    max_len = max(len(first), len(second))
    if max_len == 0:
        return 1.0
    common = 0
    for first_char, second_char in zip(first, second, strict=False):
        if first_char != second_char:
            break
        common += 1
    return common / max_len


def _lcs_length(first: str, second: str) -> int:
    if not first or not second:
        return 0
    previous = [0] * (len(second) + 1)
    for first_char in first:
        current = [0] * (len(second) + 1)
        for index, second_char in enumerate(second, start=1):
            if first_char == second_char:
                current[index] = previous[index - 1] + 1
            else:
                current[index] = max(previous[index], current[index - 1])
        previous = current
    return previous[len(second)]


def _subsequence_ratio(first: str, second: str) -> float:
    max_len = max(len(first), len(second))
    if max_len == 0:
        return 1.0
    return _lcs_length(first, second) / max_len


def _strip_leading_correction_marker(text: str, markers: Sequence[str]) -> tuple[str, bool]:
    """Remove one leading correction marker (e.g. "nein,") and report whether it matched.

    Matching is case-insensitive and requires a word boundary right after the marker,
    so "nein" does not match inside "neinerlei". Only the first configured marker
    that matches is removed; later occurrences of the same word are left untouched.
    """
    working = text.lstrip()
    lowered = working.lower()
    for marker in markers:
        marker_lower = marker.lower()
        if not lowered.startswith(marker_lower):
            continue
        boundary = len(marker_lower)
        if boundary < len(lowered) and lowered[boundary].isalnum():
            continue
        return working[boundary:].lstrip(" \t,.:;!?-"), True
    return text, False


def _word_diff(first_text: str, second_text: str) -> tuple[WordDiffOp, ...]:
    first_tokens = first_text.split()
    second_tokens = second_text.split()
    matcher = SequenceMatcher(a=first_tokens, b=second_tokens, autojunk=False)
    ops: list[WordDiffOp] = []
    for tag, first_start, first_end, second_start, second_end in matcher.get_opcodes():
        ops.append(
            WordDiffOp(
                kind=tag,
                first_tokens=tuple(first_tokens[first_start:first_end]),
                second_tokens=tuple(second_tokens[second_start:second_end]),
            )
        )
    return tuple(ops)


def compute_similarity(
    first_text: str,
    second_text: str,
    params: SimilarityParams | None = None,
) -> SimilarityScore:
    """Combine RapidFuzz ratio, n-gram, prefix, and subsequence similarity.

    ``total = 0.4 * ratio + 0.2 * ngram_similarity + 0.2 * prefix_similarity
    + 0.2 * subsequence_similarity + correction_marker_bonus``, clipped to ``[0, 1]``.
    Before ``ratio``/``ngram_similarity``/``prefix_similarity``/``subsequence_similarity``
    are computed, a leading correction marker (e.g. "nein", "ich meine", "sorry") is
    removed from ``second_text``, so the marker itself does not drag those four scores
    down for what is otherwise a near-identical repetition. The correction marker
    bonus is added exactly when such a marker was found. ``second_text`` itself is
    never mutated: the unstripped original is what is reported back to the caller via
    the word diff, so the marker stays visible to a human reading the passage.
    """
    active_params = params if params is not None else SimilarityParams()
    scoring_second_text, marker_matched = _strip_leading_correction_marker(
        second_text, active_params.correction_markers
    )
    normalized_first = normalize_text(first_text, active_params.filler_words)
    normalized_second = normalize_text(scoring_second_text, active_params.filler_words)
    ratio = fuzz.ratio(normalized_first, normalized_second) / 100.0
    ngram_similarity = _jaccard(
        _char_ngrams(normalized_first, active_params.ngram_size),
        _char_ngrams(normalized_second, active_params.ngram_size),
    )
    prefix_similarity = _common_prefix_ratio(normalized_first, normalized_second)
    subsequence_similarity = _subsequence_ratio(normalized_first, normalized_second)
    bonus = active_params.correction_marker_bonus if marker_matched else 0.0
    raw_total = (
        _RATIO_WEIGHT * ratio
        + _NGRAM_WEIGHT * ngram_similarity
        + _PREFIX_WEIGHT * prefix_similarity
        + _SUBSEQUENCE_WEIGHT * subsequence_similarity
        + bonus
    )
    total = min(1.0, max(0.0, raw_total))
    reasons: list[str] = []
    if bonus > 0:
        reasons.append("correction_marker_detected")
    if ratio >= 0.9:
        reasons.append("high_fuzzy_ratio")
    if ngram_similarity >= 0.9:
        reasons.append("high_ngram_overlap")
    return SimilarityScore(
        ratio=ratio,
        ngram_similarity=ngram_similarity,
        prefix_similarity=prefix_similarity,
        subsequence_similarity=subsequence_similarity,
        correction_marker_bonus=bonus,
        total=total,
        triggered_reasons=tuple(reasons),
        word_diff=_word_diff(first_text, second_text),
    )
