"""Tests for review-audio snippet extraction. No real ffmpeg subprocess is started."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.repeat.detect import UtteranceSpan
from matrix_auto_cutter.repeat.errors import FfmpegError, ProcessTimeoutError, SourceNotFoundError
from matrix_auto_cutter.repeat.process import ProcessResult
from matrix_auto_cutter.repeat.snippets import (
    build_snippet_ffmpeg_argv,
    build_snippets,
    clip_window_ms,
    extract_snippet,
    write_snippet_manifest,
)


class _Candidate:
    def __init__(self, first: UtteranceSpan, second: UtteranceSpan) -> None:
        self.first = first
        self.second = second


def _span(start_ms: int, end_ms: int, text: str = "x") -> UtteranceSpan:
    return UtteranceSpan(start_ms=start_ms, end_ms=end_ms, text=text)


def _runner(result: ProcessResult) -> Any:
    calls: list[list[str]] = []

    def run(argv: list[str], timeout_ms: int) -> ProcessResult:
        calls.append(argv)
        output_path = Path(argv[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if result.exit_code == 0:
            output_path.write_bytes(b"m4a-bytes")
        return result

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_build_snippet_ffmpeg_argv_exact() -> None:
    argv = build_snippet_ffmpeg_argv("ffmpeg", "in.mp4", "out.m4a", 1_000, 5_500)
    assert argv == [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        "in.mp4",
        "-ss",
        "1.000",
        "-to",
        "5.500",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        "out.m4a",
    ]


def test_clip_window_ms_applies_padding() -> None:
    start, end = clip_window_ms(5_000, 8_000, source_duration_ms=1_000_000)
    assert (start, end) == (3_000, 10_000)


def test_clip_window_ms_clamps_to_source_bounds() -> None:
    start, end = clip_window_ms(500, 999_500, source_duration_ms=1_000_000)
    assert start == 0
    assert end == 1_000_000


def test_extract_snippet_success(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    output = tmp_path / "snippets" / "clip.m4a"
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    path = extract_snippet(source, "ffmpeg", output, 0, 1_000, _runner(result), 1_000)
    assert path == output
    assert output.read_bytes() == b"m4a-bytes"


def test_extract_snippet_source_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    with pytest.raises(SourceNotFoundError):
        extract_snippet(missing, "ffmpeg", tmp_path / "out.m4a", 0, 1_000, _runner(result), 1_000)


def test_extract_snippet_nonzero_exit_raises_ffmpeg_error(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(
        exit_code=2, stdout="", stderr="bad codec", timed_out=False, duration_ms=1
    )
    with pytest.raises(FfmpegError):
        extract_snippet(source, "ffmpeg", tmp_path / "out.m4a", 0, 1_000, _runner(result), 1_000)


def test_extract_snippet_timeout(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(exit_code=-1, stdout="", stderr="", timed_out=True, duration_ms=1)
    with pytest.raises(ProcessTimeoutError):
        extract_snippet(source, "ffmpeg", tmp_path / "out.m4a", 0, 1_000, _runner(result), 1_000)


def test_build_snippets_writes_one_file_per_candidate(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    candidates = [
        _Candidate(_span(10_000, 12_000), _span(12_000, 14_000)),
        _Candidate(_span(50_000, 51_000), _span(51_000, 52_000)),
    ]
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    snippet_dir = tmp_path / "snippets"
    entries = build_snippets(
        candidates=candidates,
        stem="mystem",
        source_path=source,
        source_duration_ms=1_000_000,
        ffmpeg_path="ffmpeg",
        snippet_dir=snippet_dir,
        runner=_runner(result),
    )
    assert len(entries) == 2
    assert entries[0].candidate_id == "mystem_001"
    assert entries[0].nr == 1
    assert entries[0].path == str(snippet_dir / "mystem_001.m4a")
    assert Path(entries[0].path).exists()
    assert entries[0].clip_start_ms == 8_000
    assert entries[0].clip_end_ms == 16_000
    assert entries[0].first_offset_ms == (2_000, 4_000)
    assert entries[0].second_offset_ms == (4_000, 6_000)
    assert entries[1].candidate_id == "mystem_002"


def test_build_snippets_records_error_and_continues(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    candidates = [_Candidate(_span(0, 1_000), _span(1_000, 2_000))]
    result = ProcessResult(
        exit_code=2, stdout="", stderr="bad codec", timed_out=False, duration_ms=1
    )
    entries = build_snippets(
        candidates=candidates,
        stem="s",
        source_path=source,
        source_duration_ms=1_000_000,
        ffmpeg_path="ffmpeg",
        snippet_dir=tmp_path / "snippets",
        runner=_runner(result),
    )
    assert len(entries) == 1
    assert entries[0].path is None
    assert entries[0].error is not None


def test_write_snippet_manifest_writes_json(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    candidates = [_Candidate(_span(0, 1_000), _span(1_000, 2_000))]
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    snippet_dir = tmp_path / "snippets"
    entries = build_snippets(
        candidates=candidates,
        stem="s",
        source_path=source,
        source_duration_ms=1_000_000,
        ffmpeg_path="ffmpeg",
        snippet_dir=snippet_dir,
        runner=_runner(result),
    )
    manifest_path = write_snippet_manifest(snippet_dir, entries)
    assert manifest_path == snippet_dir / "snippets.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload[0]["candidate_id"] == "s_001"
    assert payload[0]["first_offset_ms"] == {"start_ms": 0, "end_ms": 1_000}
