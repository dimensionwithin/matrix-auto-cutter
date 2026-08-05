"""Tests for the process-execution seam. No real subprocess is ever started."""

from __future__ import annotations

import io
import subprocess
from typing import Any

from matrix_auto_cutter.repeat import process as process_module
from matrix_auto_cutter.repeat.process import NativeProcessRunner, run_process


class _FakeProcess:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        exit_code: int = 0,
        raises_timeout: bool = False,
    ) -> None:
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode: int | None = None
        self._exit_code = exit_code
        self._raises_timeout = raises_timeout
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and self._raises_timeout:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        self.returncode = self._exit_code
        return self._exit_code

    def kill(self) -> None:
        self.killed = True


class _RecordingFactory:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakeProcess:
        self.calls.append({"argv": argv, **kwargs})
        return self.process


def test_run_process_success_captures_streams_and_exit_code() -> None:
    process = _FakeProcess(stdout=b"hello\n", stderr=b"", exit_code=0)
    factory = _RecordingFactory(process)
    result = run_process(["ffprobe", "-v", "error"], timeout_ms=5_000, popen_factory=factory)
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.duration_ms >= 0
    assert factory.calls[0]["stdin"] == subprocess.DEVNULL


def test_run_process_never_uses_shell() -> None:
    process = _FakeProcess()
    factory = _RecordingFactory(process)
    run_process(["ffmpeg"], timeout_ms=1_000, popen_factory=factory)
    assert "shell" not in factory.calls[0]


def test_run_process_passes_through_nonzero_exit_code() -> None:
    process = _FakeProcess(stderr=b"boom", exit_code=17)
    factory = _RecordingFactory(process)
    result = run_process(["whisper-cli"], timeout_ms=1_000, popen_factory=factory)
    assert result.exit_code == 17
    assert result.stderr == "boom"


def test_run_process_timeout_kills_and_reaps_the_process() -> None:
    process = _FakeProcess(exit_code=-9, raises_timeout=True)
    factory = _RecordingFactory(process)
    result = run_process(["whisper-cli"], timeout_ms=10, popen_factory=factory)
    assert result.timed_out is True
    assert process.killed is True
    assert result.exit_code == -9


def test_run_process_truncates_streams_beyond_max_bytes() -> None:
    process = _FakeProcess(stdout=b"abcdefghij", exit_code=0)
    factory = _RecordingFactory(process)
    result = run_process(["cmd"], timeout_ms=1_000, popen_factory=factory, max_stream_bytes=4)
    assert result.stdout == "abcd"


class _ChunkedStream:
    """A fake pipe that yields a fixed sequence of reads, one call per chunk."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = [*chunks, b""]

    def read(self, _size: int) -> bytes:
        return self._chunks.pop(0)


def test_run_process_keeps_draining_after_limit_without_appending_more() -> None:
    process = _FakeProcess(exit_code=0)
    process.stdout = _ChunkedStream([b"ab", b"cd", b"ef"])  # type: ignore[assignment]
    factory = _RecordingFactory(process)
    result = run_process(["cmd"], timeout_ms=1_000, popen_factory=factory, max_stream_bytes=3)
    assert result.stdout == "abc"


def test_run_process_windows_creationflags_set_on_win32(monkeypatch: Any) -> None:
    monkeypatch.setattr(process_module.sys, "platform", "win32")
    process = _FakeProcess()
    factory = _RecordingFactory(process)
    run_process(["cmd"], timeout_ms=1_000, popen_factory=factory)
    assert factory.calls[0]["creationflags"] == (
        subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    )


def test_run_process_creationflags_inert_on_other_platforms(monkeypatch: Any) -> None:
    monkeypatch.setattr(process_module.sys, "platform", "linux")
    process = _FakeProcess()
    factory = _RecordingFactory(process)
    run_process(["cmd"], timeout_ms=1_000, popen_factory=factory)
    assert factory.calls[0]["creationflags"] == 0


def test_native_process_runner_delegates_to_run_process() -> None:
    process = _FakeProcess(stdout=b"ok", exit_code=0)
    factory = _RecordingFactory(process)
    runner = NativeProcessRunner(popen_factory=factory)
    result = runner(["ffprobe"], 2_000)
    assert result.stdout == "ok"
    assert factory.calls[0]["argv"] == ["ffprobe"]


def test_native_process_runner_default_popen_factory_is_subprocess_popen() -> None:
    assert NativeProcessRunner().popen_factory is subprocess.Popen
