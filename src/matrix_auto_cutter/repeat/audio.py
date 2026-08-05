"""ffprobe/ffmpeg argument-vector construction and execution over the process seam."""

from __future__ import annotations

from pathlib import Path

from matrix_auto_cutter.repeat.errors import (
    FfmpegError,
    FfprobeError,
    ProcessTimeoutError,
    SourceNotFoundError,
)
from matrix_auto_cutter.repeat.process import ProcessRunner

_SAMPLE_RATE_HZ = 16_000


def build_ffprobe_argv(ffprobe_path: str, source_path: str | Path) -> list[str]:
    """Build the argv that prints the source's total duration in seconds to stdout."""
    return [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source_path),
    ]


def _seconds_arg(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def build_ffmpeg_argv(
    ffmpeg_path: str,
    source_path: str | Path,
    output_wav_path: str | Path,
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> list[str]:
    """Build the argv extracting a 16 kHz mono PCM WAV, optionally windowed by ``-ss``/``-to``."""
    argv = [ffmpeg_path, "-nostdin", "-y", "-i", str(source_path)]
    if window_start_ms is not None:
        argv += ["-ss", _seconds_arg(window_start_ms)]
    if window_end_ms is not None:
        argv += ["-to", _seconds_arg(window_end_ms)]
    argv += [
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        str(_SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        str(output_wav_path),
    ]
    return argv


def probe_duration_ms(
    source_path: str | Path,
    ffprobe_path: str,
    runner: ProcessRunner,
    timeout_ms: int,
) -> int:
    """Return the source's total duration in integer milliseconds, rounded exactly once."""
    source = Path(source_path)
    if not source.is_file():
        raise SourceNotFoundError(str(source))
    argv = build_ffprobe_argv(ffprobe_path, source)
    result = runner(argv, timeout_ms)
    if result.timed_out:
        raise ProcessTimeoutError("ffprobe", timeout_ms, result.exit_code, result.stderr)
    if result.exit_code != 0:
        raise FfprobeError(result.exit_code, result.stderr)
    try:
        seconds = float(result.stdout.strip())
    except ValueError as exc:
        msg = f"ffprobe lieferte keine lesbare Dauer: {result.stdout.strip()!r}"
        raise FfprobeError(result.exit_code, msg) from exc
    return round(seconds * 1000)


def extract_audio(
    source_path: str | Path,
    ffmpeg_path: str,
    work_dir: str | Path,
    runner: ProcessRunner,
    timeout_ms: int,
    window_start_ms: int | None = None,
    window_end_ms: int | None = None,
) -> Path:
    """Extract a 16 kHz mono WAV from ``source_path`` into ``work_dir`` and return its path."""
    source = Path(source_path)
    if not source.is_file():
        raise SourceNotFoundError(str(source))
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    output_path = work / "audio.wav"
    argv = build_ffmpeg_argv(
        ffmpeg_path,
        source,
        output_path,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )
    result = runner(argv, timeout_ms)
    if result.timed_out:
        raise ProcessTimeoutError("ffmpeg", timeout_ms, result.exit_code, result.stderr)
    if result.exit_code != 0:
        raise FfmpegError(result.exit_code, result.stderr)
    return output_path
