"""Inline synthetic transcript builders for the isolated repeat package tests."""

from __future__ import annotations

from typing import Any


def word(start_ms: int, end_ms: int, text: str, probability: float = 0.9) -> dict[str, Any]:
    return {"start_ms": start_ms, "end_ms": end_ms, "text": text, "probability": probability}


def segment(start_ms: int, end_ms: int, words: list[dict[str, Any]]) -> dict[str, Any]:
    return {"start_ms": start_ms, "end_ms": end_ms, "words": words}


def words_for(text: str, start_ms: int, step_ms: int = 200) -> list[dict[str, Any]]:
    tokens = text.split()
    words: list[dict[str, Any]] = []
    cursor = start_ms
    for token in tokens:
        words.append(word(cursor, cursor + step_ms - 20, token))
        cursor += step_ms
    return words


def utterance_segment(text: str, start_ms: int, step_ms: int = 200) -> dict[str, Any]:
    words = words_for(text, start_ms, step_ms)
    return segment(start_ms, words[-1]["end_ms"], words)


def transcript_dict(
    segments: list[dict[str, Any]],
    *,
    source_duration_ms: int | None = None,
    audio_stream_specifier: str = "0:a:0",
) -> dict[str, Any]:
    duration = source_duration_ms
    if duration is None:
        duration = max((item["end_ms"] for item in segments), default=1_000)
    return {
        "artifact_type": "matrix_auto_cutter_repeat_transcript",
        "schema_version": "1.0",
        "audio_stream_specifier": audio_stream_specifier,
        "source_duration_ms": duration,
        "segments": segments,
    }
