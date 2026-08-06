"""Tests for snap.py. No real ffmpeg is started -- a fake ProcessRunner stands in."""

from __future__ import annotations

from pathlib import Path

import pytest

from matrix_auto_cutter.repeat.errors import FfmpegError, ProcessTimeoutError, SourceNotFoundError
from matrix_auto_cutter.repeat.process import ProcessResult
from matrix_auto_cutter.repeat.snap import (
    SilencePeriod,
    build_silencedetect_argv,
    detect_silence,
    parse_silence_periods,
    snap_point,
)


class _FakeRunner:
    def __init__(
        self,
        stderr: str = "",
        exit_code: int = 0,
        timed_out: bool = False,
    ) -> None:
        self.calls: list[list[str]] = []
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out

    def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
        self.calls.append(argv)
        return ProcessResult(self.exit_code, "", self.stderr, self.timed_out, 1)


def _write_source(tmp_path: Path) -> Path:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source-bytes")
    return source


# --- build_silencedetect_argv -----------------------------------------------------


def test_build_silencedetect_argv_uses_null_output() -> None:
    argv = build_silencedetect_argv("ffmpeg", Path("in.mp4"), -35, 0.08)
    assert argv[0] == "ffmpeg"
    assert "-af" in argv
    assert argv[argv.index("-af") + 1] == "silencedetect=noise=-35dB:d=0.08"
    assert argv[-2:] == ["-f", "null"] or argv[-1] == "-"
    assert all(isinstance(a, str) for a in argv)


# --- parse_silence_periods ---------------------------------------------------------


def test_parse_silence_periods_reads_complete_pairs() -> None:
    stderr = (
        "[silencedetect @ 0x0] silence_start: 1.0\n"
        "[silencedetect @ 0x0] silence_end: 1.5 | silence_duration: 0.5\n"
        "[silencedetect @ 0x0] silence_start: 3.2\n"
        "[silencedetect @ 0x0] silence_end: 3.9 | silence_duration: 0.7\n"
    )
    periods = parse_silence_periods(stderr, duration_ms=10_000)
    assert periods == [SilencePeriod(1_000, 1_500), SilencePeriod(3_200, 3_900)]


def test_parse_silence_periods_closes_trailing_silence_at_duration() -> None:
    stderr = "[silencedetect @ 0x0] silence_start: 9.5\n"
    periods = parse_silence_periods(stderr, duration_ms=10_000)
    assert periods == [SilencePeriod(9_500, 10_000)]


def test_parse_silence_periods_empty_stderr_yields_no_periods() -> None:
    assert parse_silence_periods("", duration_ms=10_000) == []


# --- detect_silence ------------------------------------------------------------------


def test_detect_silence_parses_runner_stderr(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    runner = _FakeRunner(stderr="silence_start: 1.0\nsilence_end: 1.2\n")
    periods = detect_silence(source, "ffmpeg", runner, 30_000, duration_ms=10_000)
    assert periods == [SilencePeriod(1_000, 1_200)]
    assert runner.calls[0][0] == "ffmpeg"


def test_detect_silence_missing_source_raises(tmp_path: Path) -> None:
    runner = _FakeRunner()
    with pytest.raises(SourceNotFoundError):
        detect_silence(tmp_path / "missing.mp4", "ffmpeg", runner, 30_000, duration_ms=10_000)


def test_detect_silence_ffmpeg_failure_raises(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    runner = _FakeRunner(exit_code=1, stderr="boom")
    with pytest.raises(FfmpegError):
        detect_silence(source, "ffmpeg", runner, 30_000, duration_ms=10_000)


def test_detect_silence_timeout_raises(tmp_path: Path) -> None:
    source = _write_source(tmp_path)
    runner = _FakeRunner(timed_out=True)
    with pytest.raises(ProcessTimeoutError):
        detect_silence(source, "ffmpeg", runner, 30_000, duration_ms=10_000)


# --- snap_point ------------------------------------------------------------------

_AMPLE_DURATION_MS = 100_000


def test_snap_point_silence_exactly_at_target_gives_minimal_shift() -> None:
    periods = [SilencePeriod(1_960, 2_040)]
    result = snap_point(2_000, periods, _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 2_000
    assert result.shift_ms == 0


def test_snap_point_silence_at_window_edge_is_snapped() -> None:
    periods = [SilencePeriod(2_700, 2_760)]
    result = snap_point(2_000, periods, _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 2_725


def test_snap_point_silence_just_outside_window_is_not_snapped() -> None:
    periods = [SilencePeriod(2_760, 2_800)]
    result = snap_point(2_000, periods, _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is False
    assert result.new_ms == 2_000
    assert result.shift_ms == 0


def test_snap_point_multiple_silences_nearest_wins() -> None:
    periods = [SilencePeriod(1_100, 1_150), SilencePeriod(1_980, 2_010)]
    result = snap_point(2_000, periods, _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 1_995


def test_snap_point_silence_extends_past_window_is_clipped() -> None:
    periods = [SilencePeriod(1_000, 3_000)]
    result = snap_point(2_000, periods, _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 2_000


def test_snap_point_no_silence_leaves_point_unchanged_and_unsnapped() -> None:
    result = snap_point(2_000, [], _AMPLE_DURATION_MS, window_ms=750)
    assert result.snapped is False
    assert result.original_ms == 2_000
    assert result.new_ms == 2_000
    assert result.shift_ms == 0


def test_snap_point_reports_original_value() -> None:
    result = snap_point(2_000, [SilencePeriod(2_700, 2_760)], _AMPLE_DURATION_MS, window_ms=750)
    assert result.original_ms == 2_000


# --- snap_point window clamped to [0, duration_ms] ----------------------------------


def test_snap_point_near_file_start_clamps_window_to_zero_no_negative_result() -> None:
    """Without the clamp, the raw window [-650, 850] would let a silence period
    starting before 0 pull the snapped point negative -- impossible for a real
    file position. The clamp excludes anything before 0 from the search."""
    periods = [SilencePeriod(-200, 50)]
    result = snap_point(100, periods, duration_ms=10_000, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 25
    assert result.new_ms >= 0


def test_snap_point_near_file_end_clamps_window_to_duration() -> None:
    """Without the clamp, the raw window [9_150, 10_650] would let a silence
    period past the file's own duration pull the snapped point beyond it.
    The clamp excludes anything past duration_ms from the search."""
    periods = [SilencePeriod(9_950, 10_300)]
    result = snap_point(9_900, periods, duration_ms=10_000, window_ms=750)
    assert result.snapped is True
    assert result.new_ms == 9_975
    assert result.new_ms <= 10_000


def test_snap_point_silence_entirely_past_clamped_window_is_not_snapped() -> None:
    """A silence period that only exists past duration_ms (e.g. from a
    slightly-off probe) must never be reachable once the window is clamped."""
    periods = [SilencePeriod(10_050, 10_200)]
    result = snap_point(9_900, periods, duration_ms=10_000, window_ms=750)
    assert result.snapped is False
    assert result.new_ms == 9_900
