"""Convert whisper.cpp's raw ``-ojf`` JSON into ``repeat_transcript/1.0``."""

from __future__ import annotations

import json
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


def _convert_segment(raw_segment: dict[str, Any], window_offset_ms: int) -> RepeatSegment | None:
    builder = _WordBuilder(clock_ms=0)
    for token in raw_segment.get("tokens", []):
        text = token.get("text", "")
        if _is_special_or_empty(text):
            continue
        offsets = token.get("offsets", {})
        start_raw = int(offsets.get("from", 0)) + window_offset_ms
        end_raw = int(offsets.get("to", 0)) + window_offset_ms
        builder.add_token(text, start_raw, end_raw, token.get("p"))
    words = builder.finish()
    if not words:
        return None
    segment_start_ms = words[0].start_ms
    segment_end_ms = words[-1].end_ms
    offsets = raw_segment.get("offsets", {})
    if "from" in offsets:
        segment_start_ms = min(segment_start_ms, int(offsets["from"]) + window_offset_ms)
    if "to" in offsets:
        segment_end_ms = max(segment_end_ms, int(offsets["to"]) + window_offset_ms)
    return RepeatSegment(start_ms=segment_start_ms, end_ms=segment_end_ms, words=tuple(words))


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
    """
    try:
        raw: Any = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RawOutputMissingError(f"kein gültiges JSON: {exc}") from exc
    raw_segments = raw.get("transcription", []) if isinstance(raw, dict) else []
    segments: list[RepeatSegment] = []
    for raw_segment in raw_segments:
        segment = _convert_segment(raw_segment, window_offset_ms)
        if segment is not None:
            segments.append(segment)
    if not segments:
        raise RawOutputEmptyError("keine verwertbaren Wort-Tokens in der Rohausgabe")
    return RepeatTranscriptDocument(
        artifact_type="matrix_auto_cutter_repeat_transcript",
        schema_version="1.0",
        audio_stream_specifier=audio_stream_specifier,
        source_duration_ms=source_duration_ms,
        segments=tuple(segments),
    )
