"""Tests for the single self-contained review.html generator."""

from __future__ import annotations

import base64
import json
import re

from matrix_auto_cutter.repeat.review import ReviewEntry, build_review_html


def _entry(**overrides: object) -> ReviewEntry:
    defaults: dict[str, object] = {
        "stem": "2026-02-19-20-00-22",
        "nr": 1,
        "source": "F:\\OLD\\2026-02-19 20-00-22.mp4",
        "first_text": "Erste Passage.",
        "first_start_ms": 1_000,
        "first_end_ms": 2_000,
        "second_text": "Zweite Passage.",
        "second_start_ms": 2_000,
        "second_end_ms": 3_000,
        "detectors": ("boundary",),
        "utterance_score": None,
        "boundary_score": 0.875,
        "window_words": 4,
        "first_window_text": "window one",
        "second_window_text": "window two",
        "audio_bytes": b"m4a-bytes",
        "audio_error": None,
    }
    defaults.update(overrides)
    return ReviewEntry(**defaults)  # type: ignore[arg-type]


def _extract_entries_json(html_text: str) -> list[dict]:
    match = re.search(r"const ENTRIES = (\[.*?\]);\n", html_text, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_build_review_html_is_single_self_contained_document() -> None:
    html_text = build_review_html([_entry()])
    assert html_text.startswith("<!doctype html>")
    assert "<script src=" not in html_text
    assert "cdn." not in html_text
    assert "<link " not in html_text


def test_build_review_html_embeds_audio_as_data_uri() -> None:
    html_text = build_review_html([_entry()])
    entries = _extract_entries_json(html_text)
    assert entries[0]["audio_data_uri"].startswith("data:audio/mp4;base64,")
    encoded = entries[0]["audio_data_uri"].split(",", 1)[1]
    assert base64.b64decode(encoded) == b"m4a-bytes"


def test_build_review_html_no_audio_sets_error_and_no_data_uri() -> None:
    html_text = build_review_html([_entry(audio_bytes=None, audio_error="ffmpeg fehlgeschlagen")])
    entries = _extract_entries_json(html_text)
    assert entries[0]["audio_data_uri"] is None
    assert entries[0]["audio_error"] == "ffmpeg fehlgeschlagen"


def test_build_review_html_escapes_text_fields() -> None:
    html_text = build_review_html([_entry(first_text="<script>alert(1)</script>")])
    entries = _extract_entries_json(html_text)
    assert entries[0]["first"]["text"] == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert "<script>alert(1)</script>" not in html_text


def test_build_review_html_carries_both_scores_and_detectors() -> None:
    html_text = build_review_html(
        [_entry(detectors=("utterance", "boundary"), utterance_score=0.6, boundary_score=0.9)]
    )
    entries = _extract_entries_json(html_text)
    assert entries[0]["detectors"] == ["utterance", "boundary"]
    assert entries[0]["utterance_score"] == 0.6
    assert entries[0]["boundary_score"] == 0.9


def test_build_review_html_no_entries_still_renders() -> None:
    html_text = build_review_html([])
    entries = _extract_entries_json(html_text)
    assert entries == []
    assert "<!doctype html>" in html_text


def test_build_review_html_nothing_preselected_no_sort_hint() -> None:
    html_text = build_review_html([_entry()])
    assert "recommended" not in html_text.lower()
    entries = _extract_entries_json(html_text)
    assert "urteil" not in entries[0]
