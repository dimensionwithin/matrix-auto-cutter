"""Process execution seam. Mirrors render.NativeProcessRunner without importing it."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import IO

_DEFAULT_MAX_STREAM_BYTES = 1_000_000
_READ_CHUNK_BYTES = 65_536
_DRAIN_JOIN_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of one subprocess run, bounded stdout/stderr, never a raised OSError."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


ProcessRunner = Callable[[Sequence[str], int], ProcessResult]
PopenFactory = Callable[..., "subprocess.Popen[bytes]"]


@dataclass
class _StreamState:
    chunks: list[bytes] = field(default_factory=list)
    total_bytes: int = 0


def _drain(stream: IO[bytes], state: _StreamState, max_bytes: int) -> None:
    while True:
        chunk = stream.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        kept_so_far = state.total_bytes
        state.total_bytes += len(chunk)
        if kept_so_far < max_bytes:
            state.chunks.append(chunk[: max_bytes - kept_so_far])


def _decode_bounded(state: _StreamState, max_bytes: int) -> str:
    return b"".join(state.chunks)[:max_bytes].decode("utf-8", errors="replace")


def run_process(
    argv: Sequence[str],
    timeout_ms: int,
    popen_factory: PopenFactory = subprocess.Popen,
    max_stream_bytes: int = _DEFAULT_MAX_STREAM_BYTES,
) -> ProcessResult:
    """Run ``argv`` (never through a shell) and return a bounded, drained result.

    stdout and stderr are drained incrementally on background threads so a full
    pipe buffer on one stream cannot deadlock the other -- the classic Windows
    subprocess hang. Each stream is capped at ``max_stream_bytes``; bytes beyond
    that are read (to keep draining the pipe) but discarded. On timeout the
    process is killed and reaped so nothing is left orphaned.
    """
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    start = time.monotonic()
    process = popen_factory(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    if process.stdout is None or process.stderr is None:  # pragma: no cover -- PIPE guarantees this
        msg = "Popen lieferte keine stdout/stderr-Pipes."
        raise RuntimeError(msg)
    stdout_state = _StreamState()
    stderr_state = _StreamState()
    stdout_thread = threading.Thread(
        target=_drain, args=(process.stdout, stdout_state, max_stream_bytes), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain, args=(process.stderr, stderr_state, max_stream_bytes), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    try:
        process.wait(timeout=timeout_ms / 1000)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    stdout_thread.join(timeout=_DRAIN_JOIN_TIMEOUT_S)
    stderr_thread.join(timeout=_DRAIN_JOIN_TIMEOUT_S)

    duration_ms = int((time.monotonic() - start) * 1000)
    exit_code = process.returncode if process.returncode is not None else -1
    return ProcessResult(
        exit_code=exit_code,
        stdout=_decode_bounded(stdout_state, max_stream_bytes),
        stderr=_decode_bounded(stderr_state, max_stream_bytes),
        timed_out=timed_out,
        duration_ms=duration_ms,
    )


@dataclass(frozen=True)
class NativeProcessRunner:
    """Injectable, callable ``ProcessRunner`` bound to a ``popen_factory``."""

    popen_factory: PopenFactory = subprocess.Popen
    max_stream_bytes: int = _DEFAULT_MAX_STREAM_BYTES

    def __call__(self, argv: Sequence[str], timeout_ms: int) -> ProcessResult:
        """Run ``argv`` through :func:`run_process` using this runner's bound factory."""
        return run_process(argv, timeout_ms, self.popen_factory, self.max_stream_bytes)
