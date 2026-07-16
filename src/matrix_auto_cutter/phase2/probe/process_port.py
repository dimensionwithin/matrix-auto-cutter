"""Injectable process boundary and stable process outcomes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.probe.errors import ProbeError

STDOUT_LIMIT = 16 * 1024 * 1024
STDERR_LIMIT = 4 * 1024 * 1024
VERSION_OUTPUT_LIMIT = 1024 * 1024
MAX_ARGUMENTS = 64
MAX_COMMAND_LINE_CHARS = 32767


@dataclass(frozen=True, slots=True)
class ProcessSpec:
    """Structured shell-free Windows process request."""

    application_path: str
    arguments: tuple[str, ...]
    timeout_seconds: int
    stdout_limit: int = STDOUT_LIMIT
    stderr_limit: int = STDERR_LIMIT

    def __post_init__(self) -> None:
        """Enforce all finite process limits."""
        if not 1 <= self.timeout_seconds <= 600:
            raise ValueError("timeout must be within 1..600 seconds")
        if self.stdout_limit < 1 or self.stderr_limit < 1:
            raise ValueError("output limits must be positive")
        if not self.application_path:
            raise ValueError("application path must not be empty")
        if (
            len(self.application_path) < 3
            or not self.application_path[0].isascii()
            or not self.application_path[0].isalpha()
            or self.application_path[1:3] != ":\\"
        ):
            raise ValueError("application path must be a fully qualified local DOS path")
        if not self.arguments or len(self.arguments) > MAX_ARGUMENTS:
            raise ValueError("argument count exceeds its finite contract")
        if "\0" in self.application_path or any("\0" in value for value in self.arguments):
            raise ValueError("process paths and arguments must not contain NUL")
        if len(serialize_windows_command_line(self.arguments)) > MAX_COMMAND_LINE_CHARS:
            raise ValueError("serialized Windows command line exceeds its finite contract")


@dataclass(frozen=True, slots=True)
class ProcessDiagnostics:
    """Bounded output and cleanup diagnostics common to every outcome."""

    stdout: bytes
    stderr: bytes
    cleanup_errors: tuple[ProbeError, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeProcessOk:
    """Exit code zero with complete bounded output."""

    diagnostics: ProcessDiagnostics
    exit_code: int = 0


@dataclass(frozen=True, slots=True)
class ProbeCancelled:
    """Cancellation won before successful completion."""

    error: ProbeError
    diagnostics: ProcessDiagnostics


@dataclass(frozen=True, slots=True)
class ProbeTimeout:
    """Finite deadline expired and the job tree was terminated."""

    error: ProbeError
    diagnostics: ProcessDiagnostics


@dataclass(frozen=True, slots=True)
class ProbeStartFailed:
    """The exact application could not be securely started."""

    error: ProbeError
    diagnostics: ProcessDiagnostics


@dataclass(frozen=True, slots=True)
class ProbeOutputLimitExceeded:
    """At least one bounded output channel exceeded its cap."""

    error: ProbeError
    diagnostics: ProcessDiagnostics
    channel: str


@dataclass(frozen=True, slots=True)
class ProbeProcessFailed:
    """Nonzero exit or process/pipe/wait failure."""

    error: ProbeError
    diagnostics: ProcessDiagnostics
    exit_code: int | None


@dataclass(frozen=True, slots=True)
class ProbeBinaryChanged:
    """Prelaunch binding could not prove the validated binary."""

    error: ProbeError
    diagnostics: ProcessDiagnostics


type ProbeProcessResult = (
    ProbeProcessOk
    | ProbeCancelled
    | ProbeTimeout
    | ProbeStartFailed
    | ProbeOutputLimitExceeded
    | ProbeProcessFailed
    | ProbeBinaryChanged
)


class ProcessPort(Protocol):
    """Shell-free process runner callable used by the domain core."""

    run: Callable[[ProcessSpec, CancellationToken], ProbeProcessResult]


def serialize_windows_command_line(arguments: tuple[str, ...]) -> str:
    """Serialize argv with the documented MS C-runtime quoting algorithm."""
    encoded: list[str] = []
    for argument in arguments:
        if argument and not any(character in ' \t"' for character in argument):
            encoded.append(argument)
            continue
        result = ['"']
        backslashes = 0
        for character in argument:
            if character == "\\":
                backslashes += 1
                continue
            if character == '"':
                result.append("\\" * (backslashes * 2 + 1))
                result.append('"')
                backslashes = 0
                continue
            result.append("\\" * backslashes)
            backslashes = 0
            result.append(character)
        result.append("\\" * (backslashes * 2))
        result.append('"')
        encoded.append("".join(result))
    return " ".join(encoded)
