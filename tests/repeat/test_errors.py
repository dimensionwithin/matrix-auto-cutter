"""Direct tests for each error class: message content, attributes, and exit-code mapping."""

from __future__ import annotations

from matrix_auto_cutter.repeat import errors
from matrix_auto_cutter.repeat.cli import _exit_code_for


def test_binary_not_found_error() -> None:
    exc = errors.BinaryNotFoundError("C:/missing.exe")
    assert exc.path == "C:/missing.exe"
    assert "C:/missing.exe" in str(exc)


def test_model_not_found_error() -> None:
    exc = errors.ModelNotFoundError("C:/missing.bin")
    assert exc.path == "C:/missing.bin"
    assert "C:/missing.bin" in str(exc)


def test_source_not_found_error() -> None:
    exc = errors.SourceNotFoundError("C:/missing.mp4")
    assert exc.path == "C:/missing.mp4"
    assert "C:/missing.mp4" in str(exc)


def test_ffprobe_error_carries_exit_code_and_stderr_tail() -> None:
    stderr = "\n".join(f"line{i}" for i in range(30))
    exc = errors.FfprobeError(exit_code=7, stderr=stderr)
    assert exc.exit_code == 7
    assert "line29" in exc.stderr_tail
    assert "line0" not in exc.stderr_tail


def test_ffmpeg_error_carries_exit_code_and_stderr_tail() -> None:
    exc = errors.FfmpegError(exit_code=8, stderr="bad codec")
    assert exc.exit_code == 8
    assert "bad codec" in str(exc)


def test_whisper_error_carries_exit_code_and_stderr_tail() -> None:
    exc = errors.WhisperError(exit_code=9, stderr="oom")
    assert exc.exit_code == 9
    assert "oom" in str(exc)


def test_process_timeout_error_carries_label_and_exit_code() -> None:
    exc = errors.ProcessTimeoutError("ffmpeg", timeout_ms=5_000, exit_code=-9, stderr="killed")
    assert exc.label == "ffmpeg"
    assert exc.timeout_ms == 5_000
    assert exc.exit_code == -9
    assert "killed" in str(exc)


def test_raw_output_missing_error() -> None:
    exc = errors.RawOutputMissingError("path/to.json")
    assert exc.detail == "path/to.json"
    assert "path/to.json" in str(exc)


def test_raw_output_empty_error() -> None:
    exc = errors.RawOutputEmptyError("no tokens")
    assert exc.detail == "no tokens"
    assert "no tokens" in str(exc)


def test_exit_code_mapping_is_documented_and_distinct() -> None:
    mapping = {
        errors.BinaryNotFoundError("x"): 2,
        errors.ModelNotFoundError("x"): 3,
        errors.SourceNotFoundError("x"): 4,
        errors.FfprobeError(1, ""): 5,
        errors.FfmpegError(1, ""): 6,
        errors.WhisperError(1, ""): 7,
        errors.ProcessTimeoutError("x", 1, 1, ""): 8,
        errors.RawOutputMissingError("x"): 9,
        errors.RawOutputEmptyError("x"): 10,
    }
    for exc, expected_code in mapping.items():
        assert _exit_code_for(exc) == expected_code
    assert len({expected for expected in mapping.values()}) == len(mapping)


def test_unmapped_contract_error_falls_back_to_exit_code_one() -> None:
    assert _exit_code_for(errors.RepeatContractError("generic")) == 1
