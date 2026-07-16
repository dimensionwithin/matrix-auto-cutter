from __future__ import annotations

import ctypes
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import fields
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import FakeProcessPort, golden_json, golden_stream

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.probe import (
    NativeProcessPort,
    ProbeCancelled,
    ProbeErrorCode,
    ProbeFailed,
    ProbeOk,
    ProbeOutputLimitExceeded,
    ProbeProcessFailed,
    ProbeProcessOk,
    ProbeRequest,
    ProbeStartFailed,
    ProbeTimeout,
    ProcessDiagnostics,
    ProcessSpec,
    StreamsSelected,
    run_probe,
    serialize_windows_command_line,
)
from matrix_auto_cutter.phase2.probe import process_native as native
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.probe.errors import probe_error
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file


def source_request(fake_port: FakePort, validated_binary):
    fake_port.add_file(r"C:\Media With Space\source.wav", b"media")
    result = validate_path(
        fake_port,
        r"C:\Media With Space\source.wav",
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
    )
    assert isinstance(result, PathValidated)
    snap = snapshot_file(fake_port, result.path)
    assert isinstance(snap, SnapshotOk)
    return ProbeRequest(validated_binary, result.path, snap.snapshot.snapshot_key)


def test_probe_core_happy_path_exact_arguments_and_no_finality_capability(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                golden_json(
                    [
                        golden_stream(7, "video", default=1),
                        golden_stream(2, "audio", default=1),
                    ]
                ),
                b"",
            )
        )
    )
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeOk)
    assert result.profile.selection.audio_index == 2
    assert result.profile.selection.video_index == 7
    assert process.calls is not None
    assert process.calls[0].application_path == validated_binary.canonical_dos_path
    assert process.calls[0].arguments[-1] == request.source.canonical_dos_path
    names = {field.name for field in fields(result.profile)}
    assert not names.intersection(
        {"closed", "final", "stable", "lease", "close_gate_lease", "source_identity"}
    )


def test_runner_rejects_integrity_deviation_after_stream_finalization(
    fake_port: FakePort, validated_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix_auto_cutter.phase2.probe import runner

    original = runner.select_streams

    def tampered_selection(streams):
        result = original(streams)
        assert isinstance(result, StreamsSelected)
        object.__setattr__(result.selection, "selection_identity", "0" * 64)
        return result

    monkeypatch.setattr(runner, "select_streams", tampered_selection)
    result = run_probe(
        source_request(fake_port, validated_binary),
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(
            ProbeProcessOk(
                ProcessDiagnostics(
                    golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]), b""
                )
            )
        ),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.STREAM_INTEGRITY
    assert result.error.phase == "stream_finalization_integrity"


def test_cancel_before_probe_starts(fake_port: FakePort, validated_binary) -> None:
    request = source_request(fake_port, validated_binary)
    token = CancellationToken()
    token.cancel()
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
        lambda path: snapshot_file(fake_port, path),
        token,
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.CANCELLED


def test_expected_snapshot_mismatch_prevents_process_start(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    process = FakeProcessPort()
    result = run_probe(
        ProbeRequest(request.binary, request.source, "0" * 64),
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.SOURCE_CHANGED
    assert process.calls is None


@pytest.mark.parametrize("mutation", ["content", "file_id", "volume", "missing_id"])
def test_source_change_and_insufficient_instance_evidence_fail_closed(
    fake_port: FakePort, validated_binary, mutation: str
) -> None:
    request = source_request(fake_port, validated_binary)
    node = fake_port.nodes[fake_port._key(request.source.canonical_dos_path)]

    def mutate(*_args: object) -> None:
        if mutation == "content":
            node.data.extend(b"x")
        elif mutation == "file_id":
            node.file_id = fake_port._new_id()
        elif mutation == "volume":
            node.volume += 1
        else:
            node.file_id = None

    process = FakeProcessPort(
        ProbeProcessOk(ProcessDiagnostics(golden_json([golden_stream(0, "audio")]), b"")),
        callback=mutate,
    )
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    expected = (
        ProbeErrorCode.SOURCE_EVIDENCE_INSUFFICIENT
        if mutation == "missing_id"
        else ProbeErrorCode.SOURCE_CHANGED
    )
    assert result.error.code is expected


def test_binary_exchange_between_validation_and_probe_prevents_start(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    binary_node = fake_port.nodes[fake_port._key(validated_binary.canonical_dos_path)]
    binary_node.data[:] = b"replacement"
    process = FakeProcessPort()
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
    assert process.calls is None


@pytest.mark.parametrize(
    "result",
    [
        ProbeCancelled(
            probe_error(ProbeErrorCode.CANCELLED, ErrorCategory.CANCELLED, "x", "x"),
            ProcessDiagnostics(b"", b""),
        ),
        ProbeTimeout(
            probe_error(ProbeErrorCode.TIMEOUT, ErrorCategory.IO, "x", "x"),
            ProcessDiagnostics(b"", b""),
        ),
        ProbeStartFailed(
            probe_error(ProbeErrorCode.START_FAILED, ErrorCategory.IO, "x", "x"),
            ProcessDiagnostics(b"", b""),
        ),
        ProbeOutputLimitExceeded(
            probe_error(ProbeErrorCode.OUTPUT_LIMIT, ErrorCategory.IO, "x", "x"),
            ProcessDiagnostics(b"", b""),
            "stdout",
        ),
        ProbeProcessFailed(
            probe_error(ProbeErrorCode.PROCESS_FAILED, ErrorCategory.IO, "x", "x"),
            ProcessDiagnostics(b"", b""),
            1,
        ),
    ],
)
def test_process_failures_never_produce_partial_profile(
    fake_port: FakePort, validated_binary, result
) -> None:
    request = source_request(fake_port, validated_binary)
    outcome = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(result),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(outcome, ProbeFailed)
    assert outcome.error.code is result.error.code


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("simple",), "simple"),
        (("",), '""'),
        (("a b",), '"a b"'),
        (('a"b',), '"a\\"b"'),
        ((r"C:\path with space\\",), r'"C:\path with space\\\\"'),
        (("one", "two three", r"a\\\"b"), 'one "two three" "a\\\\\\\\\\\\\\"b"'),
    ],
)
def test_windows_commandline_serialization(arguments: tuple[str, ...], expected: str) -> None:
    assert serialize_windows_command_line(arguments) == expected


def test_process_spec_rejects_unbounded_values() -> None:
    for timeout in (0, 601):
        with pytest.raises(ValueError):
            ProcessSpec(r"C:\x.exe", (r"C:\x.exe",), timeout)
    with pytest.raises(ValueError):
        ProcessSpec("", ("x",), 1)
    with pytest.raises(ValueError):
        ProcessSpec("relative.exe", ("relative.exe",), 1)
    with pytest.raises(ValueError):
        ProcessSpec("X", ("X",), 1)
    with pytest.raises(ValueError):
        ProcessSpec("Ä:\\x.exe", ("Ä:\\x.exe",), 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"1:\x.exe", (r"1:\x.exe",), 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"C:\x.exe", (r"C:\x.exe",), 1, 0, 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"C:\x.exe", (), 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"C:\x.exe", tuple("x" for _ in range(65)), 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"C:\x.exe", ("x\0y",), 1)
    with pytest.raises(ValueError):
        ProcessSpec(r"C:\x.exe", ("x" * 32768,), 1)


def native_spec(
    code: str, *, timeout: int = 5, stdout_limit: int = 1024, stderr_limit: int = 1024
) -> ProcessSpec:
    return ProcessSpec(
        sys.executable,
        (sys.executable, "-c", code),
        timeout,
        stdout_limit,
        stderr_limit,
    )


def test_native_normal_exit_nonzero_and_start_failure() -> None:
    port = NativeProcessPort()
    ok = port.run(
        native_spec('import sys;sys.stdout.write("out");sys.stderr.write("err")'),
        CancellationToken(),
    )
    assert isinstance(ok, ProbeProcessOk)
    assert ok.diagnostics.stdout == b"out" and ok.diagnostics.stderr == b"err"

    failed = port.run(native_spec("raise SystemExit(7)"), CancellationToken())
    assert isinstance(failed, ProbeProcessFailed) and failed.exit_code == 7

    missing = port.run(
        ProcessSpec(r"C:\definitely-missing\helper.exe", ("helper.exe",), 1),
        CancellationToken(),
    )
    assert isinstance(missing, ProbeStartFailed)
    assert missing.error.win32_code is not None


def test_native_timeout_cancel_and_bounded_output(tmp_path: Path) -> None:
    port = NativeProcessPort()
    timeout = port.run(native_spec("import time;time.sleep(30)", timeout=1), CancellationToken())
    assert isinstance(timeout, ProbeTimeout)

    marker = tmp_path / "started"
    token = CancellationToken()
    done = Event()
    holder: list[object] = []

    def invoke() -> None:
        holder.append(
            port.run(
                native_spec(
                    f"from pathlib import Path;Path({str(marker)!r}).write_text('x');"
                    "import time;time.sleep(30)"
                ),
                token,
            )
        )
        done.set()

    thread = Thread(target=invoke)
    thread.start()
    deadline = monotonic() + 5
    while not marker.exists() and monotonic() < deadline:
        done.wait(0.01)
    assert marker.exists()
    token.cancel()
    assert done.wait(10)
    thread.join()
    assert isinstance(holder[0], ProbeCancelled)

    stdout = port.run(
        native_spec("import sys;sys.stdout.write('x'*2000)", stdout_limit=64), CancellationToken()
    )
    assert isinstance(stdout, ProbeOutputLimitExceeded)
    assert stdout.channel == "stdout" and stdout.diagnostics.stdout == b""

    stderr = port.run(
        native_spec("import sys;sys.stderr.write('x'*2000)", stderr_limit=64), CancellationToken()
    )
    assert isinstance(stderr, ProbeOutputLimitExceeded)
    assert stderr.channel == "stderr" and stderr.diagnostics.stderr == b""


def test_native_simultaneous_stdout_stderr_and_pre_cancel() -> None:
    port = NativeProcessPort()
    code = (
        "import os,threading;"
        "a=threading.Thread(target=lambda:os.write(1,b'a'*100000));"
        "b=threading.Thread(target=lambda:os.write(2,b'b'*100000));"
        "a.start();b.start();a.join();b.join()"
    )
    result = port.run(
        native_spec(code, stdout_limit=200000, stderr_limit=200000), CancellationToken()
    )
    assert isinstance(result, ProbeProcessOk)
    assert len(result.diagnostics.stdout) == len(result.diagnostics.stderr) == 100000

    token = CancellationToken()
    assert token.cancel()
    cancelled = port.run(native_spec("pass"), token)
    assert isinstance(cancelled, ProbeCancelled)


def test_native_parent_handle_observes_growth_and_returns_exact_child_writes() -> None:
    class ObservingPort(NativeProcessPort):
        def __init__(self) -> None:
            super().__init__()
            self.observed: dict[str, list[int]] = {"stdout": [], "stderr": []}

        def _file_size(self, output: native._OutputFile) -> int | native.ProbeError:
            measured = super()._file_size(output)
            if isinstance(measured, int):
                self.observed[output.channel].append(measured)
            return measured

    chunks = 30
    chunk_size = 4096
    code = (
        "import os,time;"
        f"[(os.write(1,b'x'*{chunk_size}),time.sleep(0.03)) for _ in range({chunks})];"
        "os.write(2,b'golden-stderr')"
    )
    port = ObservingPort()
    result = port.run(
        native_spec(code, stdout_limit=200000, stderr_limit=200000), CancellationToken()
    )
    expected = b"x" * (chunks * chunk_size)
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout == expected
    assert result.diagnostics.stderr == b"golden-stderr"
    assert any(0 < size < len(expected) for size in port.observed["stdout"])
    assert port.observed["stdout"][-1] == len(expected)


def test_native_read_stays_bound_to_original_file_after_namespace_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private = tmp_path / "private"

    def make_private(**_kwargs: object) -> str:
        private.mkdir()
        return str(private)

    monkeypatch.setattr(native.tempfile, "mkdtemp", make_private)
    monkeypatch.setattr(
        native,
        "_remove_workdir_bounded",
        lambda _path: probe_error(
            ProbeErrorCode.PROCESS_FAILED,
            ErrorCategory.IO,
            "working_directory_cleanup",
            "retained by namespace-replacement test",
        ),
    )

    class ReplacingPort(NativeProcessPort):
        def _wait(self, *args: object, **kwargs: object) -> tuple[str, bool, str | None]:
            outputs = args[2]
            assert isinstance(outputs, tuple)
            stdout = outputs[0]
            assert isinstance(stdout, native._OutputFile)
            original = Path(stdout.path)
            os.replace(original, original.with_suffix(".moved"))
            original.write_bytes(b"replacement")
            return super()._wait(*args, **kwargs)  # type: ignore[arg-type]

    result = ReplacingPort().run(
        native_spec("import os;os.write(1,b'original')"), CancellationToken()
    )
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout == b"original"
    assert (private / "stdout.bin").read_bytes() == b"replacement"
    for remaining in private.iterdir():
        remaining.unlink()
    private.rmdir()


def test_process_arguments_are_data_not_shell_syntax() -> None:
    payload = 'x & echo injected | powershell "quoted" \\\\ tail'
    result = NativeProcessPort().run(
        ProcessSpec(
            sys.executable,
            (sys.executable, "-c", "import sys;print(sys.argv[1])", payload),
            5,
        ),
        CancellationToken(),
    )
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout.decode().strip() == payload


def test_repeated_native_runs_leave_no_reader_threads() -> None:
    before = tuple(
        thread
        for thread in threading.enumerate()
        if thread.name in {"probe-stdout", "probe-stderr"}
    )
    port = NativeProcessPort()
    for _ in range(5):
        result = port.run(native_spec("pass"), CancellationToken())
        assert isinstance(result, ProbeProcessOk)
    after = tuple(
        thread
        for thread in threading.enumerate()
        if thread.name in {"probe-stdout", "probe-stderr"}
    )
    assert after == before


def test_native_descendant_writer_is_terminated_by_job_without_eof_wait() -> None:
    code = (
        "import subprocess,sys;"
        "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "close_fds=False);"
        "sys.stdout.write('parent-finished')"
    )
    started = monotonic()
    result = NativeProcessPort().run(native_spec(code, timeout=5), CancellationToken())
    elapsed = monotonic() - started
    assert elapsed < 4
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout == b"parent-finished"


def test_repeated_native_file_redirect_runs_do_not_grow_process_handles() -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetProcessHandleCount.restype = wintypes.BOOL

    def handle_count() -> int:
        count = wintypes.DWORD()
        assert kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
        return int(count.value)

    port = NativeProcessPort()
    before = handle_count()
    for _ in range(20):
        assert isinstance(port.run(native_spec("pass"), CancellationToken()), ProbeProcessOk)
    after = handle_count()
    assert after <= before + 2
