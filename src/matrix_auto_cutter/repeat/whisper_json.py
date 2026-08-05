"""Convert whisper.cpp's raw ``-ojf`` JSON into ``repeat_transcript/1.0``."""

from __future__ import annotations

import json
import sys
from typing import Any

from matrix_auto_cutter.repeat.errors import RawOutputEmptyError, RawOutputMissingError
from matrix_auto_cutter.repeat.transcript import (
    RepeatSegment,
    RepeatTranscriptDocument,
    RepeatWord,
)


def _is_special_or_empty(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    return stripped.startswith("[") and stripped.endswith("]")


def _is_bracketed_word(text: str) -> bool:
    """Report whether a fully assembled word looks like ``[Musik]`` or ``[Applaus]``.

    whisper.cpp splits these into several BPE subword tokens (e.g. ``" [Mus"``,
    ``"ik"``, ``"]"``), none of which is bracketed on its own, so the per-token
    filter in ``_is_special_or_empty`` never sees them. This check runs after
    ``_WordBuilder`` has joined the tokens back into one word.
    """
    stripped = text.strip()
    return stripped.startswith("[") and stripped.endswith("]")


class _WordBuilder:
    """Accumulates BPE subword tokens into one word under a monotone clock."""

    def __init__(self, clock_ms: int) -> None:
        self._clock_ms = clock_ms
        self._text: str | None = None
        self._start_raw = 0
        self._end_raw = 0
        self._probabilities: list[float] = []
        self.words: list[RepeatWord] = []

    def add_token(self, text: str, start_raw: int, end_raw: int, probability: float | None) -> None:
        starts_new_word = text.startswith(" ")
        piece = text[1:] if starts_new_word else text
        if starts_new_word or self._text is None:
            self._flush()
            self._text = piece
            self._start_raw = start_raw
            self._end_raw = end_raw
            self._probabilities = [probability] if probability is not None else []
        else:
            self._text += piece
            self._end_raw = end_raw
            if probability is not None:
                self._probabilities.append(probability)

    def finish(self) -> list[RepeatWord]:
        self._flush()
        return self.words

    @property
    def clock_ms(self) -> int:
        return self._clock_ms

    def _flush(self) -> None:
        if self._text is None:
            return
        start_ms = max(self._start_raw, self._clock_ms)
        end_ms = max(self._end_raw, start_ms + 1)
        probability = min(self._probabilities) if self._probabilities else 1.0
        self.words.append(
            RepeatWord(start_ms=start_ms, end_ms=end_ms, text=self._text, probability=probability)
        )
        self._clock_ms = end_ms
        self._text = None


def _convert_segment(
    raw_segment: dict[str, Any],
    window_offset_ms: int,
    clock_ms: int,
    previous_segment_end_ms: int | None,
) -> tuple[RepeatSegment | None, int]:
    """Convert one raw segment under a clock carried in from the whole transcript so far.

    Returns the segment (or ``None`` if it has no words left after filtering)
    and the clock position to carry into the next raw segment: the segment's
    own end when one was produced, otherwise wherever the word clock landed
    while consuming this segment's (all-filtered) tokens.
    """
    builder = _WordBuilder(clock_ms=clock_ms)
    for token in raw_segment.get("tokens", []):
        text = token.get("text", "")
        if _is_special_or_empty(text):
            continue
        offsets = token.get("offsets", {})
        start_raw = int(offsets.get("from", 0)) + window_offset_ms
        end_raw = int(offsets.get("to", 0)) + window_offset_ms
        builder.add_token(text, start_raw, end_raw, token.get("p"))
    words = [word for word in builder.finish() if not _is_bracketed_word(word.text)]
    next_clock_ms = builder.clock_ms
    if not words:
        return None, next_clock_ms
    segment_start_ms = words[0].start_ms
    segment_end_ms = words[-1].end_ms
    offsets = raw_segment.get("offsets", {})
    if "from" in offsets:
        segment_start_ms = min(segment_start_ms, int(offsets["from"]) + window_offset_ms)
    if "to" in offsets:
        segment_end_ms = max(segment_end_ms, int(offsets["to"]) + window_offset_ms)
    if previous_segment_end_ms is not None:
        segment_start_ms = max(segment_start_ms, previous_segment_end_ms)
        segment_end_ms = max(segment_end_ms, segment_start_ms + 1)
    segment = RepeatSegment(start_ms=segment_start_ms, end_ms=segment_end_ms, words=tuple(words))
    return segment, max(next_clock_ms, segment_end_ms)


def convert_whisper_output(
    raw_json: str,
    source_duration_ms: int,
    audio_stream_specifier: str,
    window_offset_ms: int = 0,
) -> RepeatTranscriptDocument:
    """Convert whisper.cpp's raw ``-ojf`` JSON text into a validated ``RepeatTranscriptDocument``.

    ``window_offset_ms`` is the absolute source-time offset of the transcribed
    window's start; whisper's own timestamps are window-relative, so every
    converted time is shifted by this amount before validation. Segment
    boundaries come from the raw output's own segment offsets (widened, never
    shrunk, if the monotone word clock pushed a word past them) -- they carry
    the detection signal downstream and are not rebuilt from the words.

    The word clock runs across the *entire* transcript, not per segment: a
    segment's end becomes the next segment's clock floor, and a later
    segment's start is never allowed before the previous segment's end, even
    if that segment's own raw offsets would otherwise pull it earlier. This
    is what whisper.cpp's per-segment, occasionally overlapping or
    out-of-order raw offsets need to come out monotonic and non-overlapping.
    """
    try:
        raw: Any = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RawOutputMissingError(f"kein gültiges JSON: {exc}") from exc
    raw_segments = raw.get("transcription", []) if isinstance(raw, dict) else []
    segments: list[RepeatSegment] = []
    clock_ms = 0
    previous_segment_end_ms: int | None = None
    for raw_segment in raw_segments:
        segment, clock_ms = _convert_segment(
            raw_segment, window_offset_ms, clock_ms, previous_segment_end_ms
        )
        if segment is not None:
            segments.append(segment)
            previous_segment_end_ms = segment.end_ms
    if not segments:
        raise RawOutputEmptyError("keine verwertbaren Wort-Tokens in der Rohausgabe")
    reconciled_duration_ms = _reconcile_source_duration_ms(source_duration_ms, segments)
    return RepeatTranscriptDocument(
        artifact_type="matrix_auto_cutter_repeat_transcript",
        schema_version="1.0",
        audio_stream_specifier=audio_stream_specifier,
        source_duration_ms=reconciled_duration_ms,
        segments=tuple(segments),
    )


def _reconcile_source_duration_ms(source_duration_ms: int, segments: list[RepeatSegment]) -> int:
    """Widen ``source_duration_ms`` to cover the transcript actually produced from it.

    ffprobe reports a rounded container duration; whisper's last segment can end a
    few milliseconds past that rounded value. The source is at minimum as long as
    what was transcribed from it, so the later of the two bounds wins -- nothing is
    truncated and no word is ever dropped to satisfy the contract.
    """
    max_segment_end_ms = max(segment.end_ms for segment in segments)
    reconciled = max(source_duration_ms, max_segment_end_ms)
    if reconciled != source_duration_ms:
        diff_ms = reconciled - source_duration_ms
        print(
            f"Quelldauer angepasst: ffprobe={source_duration_ms}ms, "
            f"Segmentende={max_segment_end_ms}ms, Differenz={diff_ms}ms",
            file=sys.stderr,
        )
    return reconciled
