"""whisper-cli argument-vector construction, execution, and raw JSON retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from matrix_auto_cutter.repeat.errors import (
    BinaryNotFoundError,
    ModelNotFoundError,
    ProcessTimeoutError,
    RawOutputMissingError,
    WhisperError,
)
from matrix_auto_cutter.repeat.process import ProcessRunner

DEFAULT_THREADS = 4
DEFAULT_MAX_SEGMENT_LEN = 60
_BASE_TIMEOUT_MS = 60_000
_TIMEOUT_DURATION_MULTIPLIER = 4


def default_timeout_ms(audio_duration_ms: int) -> int:
    """60s base plus 4x the audio duration, the documented whisper-cli timeout default."""
    return _BASE_TIMEOUT_MS + _TIMEOUT_DURATION_MULTIPLIER * audio_duration_ms


def build_whisper_argv(
    whisper_binary: str,
    model_path: str,
    wav_path: str | Path,
    threads: int = DEFAULT_THREADS,
    max_segment_len: int = DEFAULT_MAX_SEGMENT_LEN,
    initial_prompt: str | None = None,
) -> list[str]:
    """Build the argv: German, word-level JSON via -ojf, no VAD, bounded segment length.

    whisper.cpp does not bound segment length without ``-ml``. Segment
    boundaries are what utterance formation is built on downstream, so an
    unbounded segment silently defeats it. The validated probe run used
    ``-owts``, which internally sets ``max_len=60``; ``-ml 60`` establishes
    the same bound explicitly, without the extra ``.wts`` output file.
    ``max_segment_len <= 0`` omits the flag entirely (whisper.cpp's own
    unbounded default). ``initial_prompt``, when given, is appended as
    ``--prompt <text>``; when ``None`` the flag is omitted entirely.
    """
    argv = [
        whisper_binary,
        "-m",
        model_path,
        "-f",
        str(wav_path),
        "-l",
        "de",
        "-t",
        str(threads),
        "-ojf",
    ]
    if max_segment_len > 0:
        argv += ["-ml", str(max_segment_len)]
    if initial_prompt is not None:
        argv += ["--prompt", initial_prompt]
    return argv


def whisper_json_path(wav_path: str | Path) -> Path:
    """whisper-cli writes its -ojf output next to the input WAV as ``<wav>.json``."""
    wav = Path(wav_path)
    return wav.with_name(wav.name + ".json")


@dataclass(frozen=True)
class WhisperRunResult:
    """whisper-cli's raw JSON output plus process bookkeeping kept for the report."""

    raw_json: str
    json_path: Path
    stdout: str
    duration_ms: int


def run_whisper(
    wav_path: str | Path,
    whisper_binary: str,
    model_path: str,
    runner: ProcessRunner,
    audio_duration_ms: int,
    threads: int = DEFAULT_THREADS,
    timeout_ms: int | None = None,
    max_segment_len: int = DEFAULT_MAX_SEGMENT_LEN,
    initial_prompt: str | None = None,
) -> WhisperRunResult:
    """Validate binary/model paths, run whisper-cli, and read back its raw JSON output."""
    binary = Path(whisper_binary)
    if not binary.is_file():
        raise BinaryNotFoundError(str(binary))
    model = Path(model_path)
    if not model.is_file():
        raise ModelNotFoundError(str(model))
    active_timeout_ms = (
        timeout_ms if timeout_ms is not None else default_timeout_ms(audio_duration_ms)
    )
    argv = build_whisper_argv(
        str(binary), str(model), wav_path, threads, max_segment_len, initial_prompt
    )
    result = runner(argv, active_timeout_ms)
    if result.timed_out:
        raise ProcessTimeoutError("whisper-cli", active_timeout_ms, result.exit_code, result.stderr)
    if result.exit_code != 0:
        raise WhisperError(result.exit_code, result.stderr)
    json_path = whisper_json_path(wav_path)
    try:
        raw_json = json_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RawOutputMissingError(str(json_path)) from exc
    return WhisperRunResult(
        raw_json=raw_json,
        json_path=json_path,
        stdout=result.stdout,
        duration_ms=result.duration_ms,
    )
