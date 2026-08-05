"""Tests for whisper-cli argv construction and execution. No real whisper-cli call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.repeat.asr import (
    build_whisper_argv,
    default_timeout_ms,
    run_whisper,
    whisper_json_path,
)
from matrix_auto_cutter.repeat.errors import (
    BinaryNotFoundError,
    ModelNotFoundError,
    ProcessTimeoutError,
    RawOutputMissingError,
    WhisperError,
)
from matrix_auto_cutter.repeat.process import ProcessResult


def _runner(result: ProcessResult) -> Any:
    calls: list[list[str]] = []

    def run(argv: list[str], timeout_ms: int) -> ProcessResult:
        calls.append(list(argv))
        return result

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_build_whisper_argv_exact() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav", threads=6)
    assert argv == [
        "whisper-cli.exe",
        "-m",
        "model.bin",
        "-f",
        "audio.wav",
        "-l",
        "de",
        "-t",
        "6",
        "-ojf",
        "-ml",
        "60",
    ]


def test_build_whisper_argv_default_threads() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav")
    assert "-t" in argv
    assert argv[argv.index("-t") + 1] == "4"


def test_build_whisper_argv_default_max_segment_len_appends_ml_60() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav")
    assert argv[-2:] == ["-ml", "60"]


def test_build_whisper_argv_zero_max_segment_len_omits_flag() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav", max_segment_len=0)
    assert "-ml" not in argv


def test_build_whisper_argv_negative_max_segment_len_omits_flag() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav", max_segment_len=-1)
    assert "-ml" not in argv


def test_build_whisper_argv_custom_max_segment_len() -> None:
    argv = build_whisper_argv("whisper-cli.exe", "model.bin", "audio.wav", max_segment_len=30)
    assert argv[-2:] == ["-ml", "30"]


def test_whisper_json_path_appends_json_suffix() -> None:
    assert whisper_json_path("work/audio.wav") == Path("work/audio.wav.json")


def test_default_timeout_ms_formula() -> None:
    assert default_timeout_ms(0) == 60_000
    assert default_timeout_ms(10_000) == 60_000 + 4 * 10_000


def test_run_whisper_binary_not_found(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    with pytest.raises(BinaryNotFoundError):
        run_whisper(
            wav,
            str(tmp_path / "missing-binary.exe"),
            str(model),
            _runner(ProcessResult(0, "", "", False, 1)),
            audio_duration_ms=1_000,
        )


def test_run_whisper_model_not_found(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    wav = tmp_path / "audio.wav"
    with pytest.raises(ModelNotFoundError):
        run_whisper(
            wav,
            str(binary),
            str(tmp_path / "missing-model.bin"),
            _runner(ProcessResult(0, "", "", False, 1)),
            audio_duration_ms=1_000,
        )


def test_run_whisper_nonzero_exit_raises_whisper_error(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    result = ProcessResult(exit_code=3, stdout="", stderr="crash", timed_out=False, duration_ms=1)
    with pytest.raises(WhisperError):
        run_whisper(wav, str(binary), str(model), _runner(result), audio_duration_ms=1_000)


def test_run_whisper_timeout(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    result = ProcessResult(exit_code=-1, stdout="", stderr="", timed_out=True, duration_ms=1)
    with pytest.raises(ProcessTimeoutError):
        run_whisper(wav, str(binary), str(model), _runner(result), audio_duration_ms=1_000)


def test_run_whisper_missing_output_json_raises(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    with pytest.raises(RawOutputMissingError):
        run_whisper(wav, str(binary), str(model), _runner(result), audio_duration_ms=1_000)


def test_run_whisper_success_reads_json_next_to_wav(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    json_path = tmp_path / "audio.wav.json"
    json_path.write_text('{"transcription": []}', encoding="utf-8")
    result = ProcessResult(exit_code=0, stdout="report", stderr="", timed_out=False, duration_ms=42)
    run_result = run_whisper(
        wav, str(binary), str(model), _runner(result), audio_duration_ms=1_000, timeout_ms=5_000
    )
    assert run_result.raw_json == '{"transcription": []}'
    assert run_result.json_path == json_path
    assert run_result.stdout == "report"
    assert run_result.duration_ms == 42


def test_run_whisper_passes_through_max_segment_len(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    json_path = tmp_path / "audio.wav.json"
    json_path.write_text('{"transcription": []}', encoding="utf-8")
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    runner = _runner(result)
    run_whisper(
        wav,
        str(binary),
        str(model),
        runner,
        audio_duration_ms=1_000,
        max_segment_len=30,
    )
    assert runner.calls[0][-2:] == ["-ml", "30"]


def test_run_whisper_default_max_segment_len_is_60(tmp_path: Path) -> None:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    wav = tmp_path / "audio.wav"
    json_path = tmp_path / "audio.wav.json"
    json_path.write_text('{"transcription": []}', encoding="utf-8")
    result = ProcessResult(exit_code=0, stdout="", stderr="", timed_out=False, duration_ms=1)
    runner = _runner(result)
    run_whisper(wav, str(binary), str(model), runner, audio_duration_ms=1_000)
    assert runner.calls[0][-2:] == ["-ml", "60"]
