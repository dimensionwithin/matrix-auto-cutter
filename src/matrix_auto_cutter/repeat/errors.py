"""Errors for the isolated repeat/self-correction detection package."""

from __future__ import annotations

_STDERR_TAIL_LINES = 20


class RepeatContractError(Exception):
    """Raised when repeat package input or output violates its declared contract."""


def _tail(stderr: str, lines: int = _STDERR_TAIL_LINES) -> str:
    return "\n".join(stderr.splitlines()[-lines:])


class BinaryNotFoundError(RepeatContractError):
    """A required executable (whisper-cli) does not exist at the given path."""

    def __init__(self, path: str) -> None:
        """Store the missing binary path."""
        self.path = path
        super().__init__(f"Binärdatei nicht gefunden: {path}")


class ModelNotFoundError(RepeatContractError):
    """The whisper model file does not exist at the given path."""

    def __init__(self, path: str) -> None:
        """Store the missing model path."""
        self.path = path
        super().__init__(f"Modell nicht gefunden: {path}")


class SourceNotFoundError(RepeatContractError):
    """The audio/video source file does not exist at the given path."""

    def __init__(self, path: str) -> None:
        """Store the missing source path."""
        self.path = path
        super().__init__(f"Quelldatei nicht gefunden: {path}")


class FfprobeError(RepeatContractError):
    """ffprobe exited with a nonzero code while probing the source duration."""

    def __init__(self, exit_code: int, stderr: str) -> None:
        """Store the subprocess exit code and the last lines of its stderr."""
        self.exit_code = exit_code
        self.stderr_tail = _tail(stderr)
        super().__init__(f"ffprobe fehlgeschlagen (exit={exit_code}): {self.stderr_tail}")


class FfmpegError(RepeatContractError):
    """ffmpeg exited with a nonzero code while extracting audio."""

    def __init__(self, exit_code: int, stderr: str) -> None:
        """Store the subprocess exit code and the last lines of its stderr."""
        self.exit_code = exit_code
        self.stderr_tail = _tail(stderr)
        super().__init__(f"ffmpeg fehlgeschlagen (exit={exit_code}): {self.stderr_tail}")


class WhisperError(RepeatContractError):
    """whisper-cli exited with a nonzero code while transcribing."""

    def __init__(self, exit_code: int, stderr: str) -> None:
        """Store the subprocess exit code and the last lines of its stderr."""
        self.exit_code = exit_code
        self.stderr_tail = _tail(stderr)
        super().__init__(f"whisper-cli fehlgeschlagen (exit={exit_code}): {self.stderr_tail}")


class ProcessTimeoutError(RepeatContractError):
    """A subprocess (ffprobe, ffmpeg, or whisper-cli) exceeded its timeout and was killed."""

    def __init__(self, label: str, timeout_ms: int, exit_code: int, stderr: str) -> None:
        """Store the timed-out subprocess's label, timeout, exit code, and stderr tail."""
        self.label = label
        self.timeout_ms = timeout_ms
        self.exit_code = exit_code
        self.stderr_tail = _tail(stderr)
        super().__init__(
            f"{label} Zeitüberschreitung nach {timeout_ms} ms "
            f"(exit={exit_code}): {self.stderr_tail}"
        )


class RawOutputMissingError(RepeatContractError):
    """whisper-cli's raw JSON output file is missing, unreadable, or not valid JSON."""

    def __init__(self, detail: str) -> None:
        """Store a description of what was missing or unreadable."""
        self.detail = detail
        super().__init__(f"Rohausgabe fehlt oder ist unlesbar: {detail}")


class RawOutputEmptyError(RepeatContractError):
    """whisper-cli's raw JSON output contains no usable (non-special, non-empty) tokens."""

    def __init__(self, detail: str) -> None:
        """Store a description of the empty-token condition."""
        self.detail = detail
        super().__init__(f"Rohausgabe enthält keine verwertbaren Tokens: {detail}")
