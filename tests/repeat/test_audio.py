"""Tests for ffprobe/ffmpeg argv construction and execution. No real ffmpeg/ffprobe call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.repeat.audio import (
    build_ffmpeg_argv,
    build_ffprobe_argv,
    extract_audio,
    probe_duration_ms,
)
from matrix_auto_cutter.repeat.errors import (
    FfmpegError,
    FfprobeError,
    ProcessTimeoutError,
    SourceNotFoundError,
)
from matrix_auto_cutter.repeat.process import ProcessResult


def _runner(result: ProcessResult) -> Any:
    calls: list[list[str]] = []

    def run(argv: list[str], timeout_ms: int) -> ProcessResult:
        calls.append(argv)
        return result

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_build_ffprobe_argv_exact() -> None:
    argv = build_ffprobe_argv("ffprobe", "in.mp4")
    assert argv == [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        "in.mp4",
    ]


def test_build_ffmpeg_argv_exact_without_window() -> None:
    argv = build_ffmpeg_argv("ffmpeg", "in.mp4", "out.wav")
    assert argv == [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        "in.mp4",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "out.wav",
    ]


def test_build_ffmpeg_argv_exact_with_window() -> None:
    argv = build_ffmpeg_argv(
        "ffmpeg", "in.mp4", "out.wav", window_start_ms=120_000, window_end_ms=300_500
    )
    assert argv == [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        "in.mp4",
        "-ss",
        "120.000",
        "-to",
        "300.500",
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "out.wav",
    ]


def test_build_ffmpeg_argv_start_only_window() -> None:
    argv = build_ffmpeg_argv("ffmpeg", "in.mp4", "out.wav", window_start_ms=1_000)
    assert "-ss" in argv
    assert "-to" not in argv


def test_probe_duration_ms_rounds_once(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(
        exit_code=0, stdout="12.3456\n", stderr="", timed_out=False, duration_ms=1
    )
    duration = probe_duration_ms(source, "ffprobe", _runner(result), timeout_ms=1_000)
    assert duration == round(12.3456 * 1000)


def test_probe_duration_ms_source_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    with pytest.raises(SourceNotFoundError):
        probe_duration_ms(missing, "ffprobe", _runner(ProcessResult(0, "1.0", "", False, 1)), 1_000)


def test_probe_duration_ms_nonzero_exit_raises_ffprobe_error(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(
        exit_code=1, stdout="", stderr="no such file", timed_out=False, duration_ms=1
    )
    with pytest.raises(FfprobeError):
        probe_duration_ms(source, "ffprobe", _runner(result), 1_000)


def test_probe_duration_ms_timeout(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(exit_code=-1, stdout="", stderr="", timed_out=True, duration_ms=1)
    with pytest.raises(ProcessTimeoutError):
        probe_duration_ms(source, "ffprobe", _runner(result), 1_000)


def test_probe_duration_ms_unparseable_stdout_raises_ffprobe_error(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(
        exit_code=0, stdout="not-a-number", stderr="", timed_out=False, duration_ms=1
    )
    with pytest.raises(FfprobeError):
        probe_duration_ms(source, "ffprobe", _runner(result), 1_000)


def test_extract_audio_success_writes_into_work_dir(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    work_dir = tmp_path / "work"
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    output = extract_audio(source, "ffmpeg", work_dir, _runner(result), timeout_ms=1_000)
    assert output == work_dir / "audio.wav"
    assert work_dir.is_dir()


def test_extract_audio_source_not_found(tmp_path: Path) -> None:
    missing = tmp_path / "missing.mp4"
    with pytest.raises(SourceNotFoundError):
        extract_audio(
            missing, "ffmpeg", tmp_path / "work", _runner(ProcessResult(0, "", "", False, 1)), 1_000
        )


def test_extract_audio_nonzero_exit_raises_ffmpeg_error(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(
        exit_code=2, stdout="", stderr="bad codec", timed_out=False, duration_ms=1
    )
    with pytest.raises(FfmpegError):
        extract_audio(source, "ffmpeg", tmp_path / "work", _runner(result), 1_000)


def test_extract_audio_timeout(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    result = ProcessResult(exit_code=-1, stdout="", stderr="", timed_out=True, duration_ms=1)
    with pytest.raises(ProcessTimeoutError):
        extract_audio(source, "ffmpeg", tmp_path / "work", _runner(result), 1_000)
