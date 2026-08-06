"""Snap cut boundaries onto measured silence, over the process seam from process.py.

Whisper's word timestamps have a median inter-word gap of 0 ms -- the end of
one word is arithmetically the start of the next. Cutting exactly on such a
timestamp lands mid-word. This module measures real silence with ffmpeg's
``silencedetect`` filter and moves each boundary onto the nearest silence
found within a small window around it, so the cut lands in the gap instead
of in the word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from matrix_auto_cutter.repeat.errors import FfmpegError, ProcessTimeoutError, SourceNotFoundError
from matrix_auto_cutter.repeat.process import ProcessRunner

DEFAULT_WINDOW_MS = 750
DEFAULT_NOISE_DB = -35
DEFAULT_MIN_SILENCE_MS = 80

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[0-9.]+)")


@dataclass(frozen=True)
class SilencePeriod:
    """One measured silence span, in milliseconds, half-open [start_ms, end_ms)."""

    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SnapResult:
    """Outcome of snapping a single boundary point onto nearby silence."""

    original_ms: int
    new_ms: int
    shift_ms: int
    snapped: bool


def build_silencedetect_argv(
    ffmpeg_path: str,
    source: str | Path,
    noise_db: float,
    min_silence_s: float,
) -> list[str]:
    """Build the argv that measures silence over the whole file via stderr logging."""
    return [
        ffmpeg_path,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(source),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_s}",
        "-f",
        "null",
        "-",
    ]


def parse_silence_periods(stderr: str, duration_ms: int) -> list[SilencePeriod]:
    """Parse silence_start/silence_end pairs from ffmpeg stderr, in milliseconds.

    A trailing ``silence_start`` with no matching ``silence_end`` means the
    file ends inside silence -- that period is closed at ``duration_ms``.
    """
    starts = [round(float(m.group(1)) * 1000) for m in _SILENCE_START_RE.finditer(stderr)]
    ends = [round(float(m.group(1)) * 1000) for m in _SILENCE_END_RE.finditer(stderr)]
    periods: list[SilencePeriod] = []
    for i, start_ms in enumerate(starts):
        end_ms = ends[i] if i < len(ends) else duration_ms
        periods.append(SilencePeriod(start_ms, end_ms))
    return periods


def detect_silence(
    source: str | Path,
    ffmpeg_path: str,
    runner: ProcessRunner,
    timeout_ms: int,
    duration_ms: int,
    noise_db: float = DEFAULT_NOISE_DB,
    min_silence_ms: int = DEFAULT_MIN_SILENCE_MS,
) -> list[SilencePeriod]:
    """Run ``silencedetect`` over ``source`` and return the parsed silence periods."""
    source_path = Path(source)
    if not source_path.is_file():
        raise SourceNotFoundError(str(source_path))
    argv = build_silencedetect_argv(ffmpeg_path, source_path, noise_db, min_silence_ms / 1000)
    result = runner(argv, timeout_ms)
    if result.timed_out:
        raise ProcessTimeoutError("ffmpeg", timeout_ms, result.exit_code, result.stderr)
    if result.exit_code != 0:
        raise FfmpegError(result.exit_code, result.stderr)
    return parse_silence_periods(result.stderr, duration_ms)


def snap_point(
    point_ms: int,
    periods: list[SilencePeriod],
    window_ms: int = DEFAULT_WINDOW_MS,
) -> SnapResult:
    """Move ``point_ms`` onto the middle of the nearest silence within +/- ``window_ms``.

    Silence periods that extend past the window are clipped to the window
    before their middle is taken. Among several candidates, the one whose
    (clipped) middle is closest to ``point_ms`` wins. If no silence falls in
    the window at all, the point is returned unchanged and unsnapped.
    """
    window_start = point_ms - window_ms
    window_end = point_ms + window_ms
    best_mid: int | None = None
    best_distance: int | None = None
    for period in periods:
        clipped_start = max(period.start_ms, window_start)
        clipped_end = min(period.end_ms, window_end)
        if clipped_end <= clipped_start:
            continue
        mid = round((clipped_start + clipped_end) / 2)
        distance = abs(mid - point_ms)
        if best_distance is None or distance < best_distance:
            best_mid = mid
            best_distance = distance
    if best_mid is None:
        return SnapResult(point_ms, point_ms, 0, False)
    return SnapResult(point_ms, best_mid, best_mid - point_ms, True)
