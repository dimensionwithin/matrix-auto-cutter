from __future__ import annotations

import ctypes
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe import (
    ProbeCancelled,
    ProbeOutputLimitExceeded,
    ProbeProcessFailed,
    ProbeProcessOk,
    ProbeStartFailed,
    ProbeTimeout,
    ProcessSpec,
)
from matrix_auto_cutter.phase2.probe import process_native as native
from matrix_auto_cutter.phase2.probe.errors import (
    ProbeError,
    ProbeErrorCode,
    _TerminalLatch,
    probe_error,
)


def set_error(code: int = 5) -> None:
    ctypes.set_last_error(code)


class FakeKernel:
    """Deterministic file-object and process/job model for the native adapter."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.next_handle = 100
        self.next_file = 1
        self.create_file_count = 0
        self.handle_file: dict[int, int] = {}
        self.file_data: dict[int, bytearray] = {}
        self.file_position: dict[int, int] = {}
        self.parent_by_channel: dict[str, int] = {}
        self.child_by_channel: dict[str, int] = {}
        self.output = {"stdout": b"", "stderr": b""}
        self.created: list[tuple[Any, ...]] = []
        self.duplicate_calls: list[tuple[int, int, bool]] = []
        self.allowlist: tuple[int, ...] = ()
        self.std_handles: tuple[int, int, int] = ()
        self.closed_values: list[int] = []
        self.close_fail_values: set[int] = set()
        self.wait_values: list[int] = []
        self.active_values: list[int] = []
        self.exit_code = 0
        self.resumed = 0
        self.assigned = 0
        self.terminated = 0
        self.direct_terminated = 0
        self.read_calls: list[tuple[int, int]] = []
        self.size_calls: list[int] = []
        self.seek_calls: list[int] = []
        self.operations: list[str] = []
        self.attribute_deleted = 0
        self.size_override: dict[int, int] = {}
        self.size_fail_calls: set[int] = set()
        self.partial_read: int | None = None
        self.early_eof = False
        self.change_after_read = False

    def _new_handle(self) -> int:
        value = self.next_handle
        self.next_handle += 1
        return value

    def _fails(self, name: str) -> bool:
        if self.fail == name:
            set_error()
            return True
        return False

    def CreatePipe(self, read: Any, write: Any, _security: Any, _size: int) -> bool:
        if self._fails("CreatePipe"):
            return False
        read._obj.value = self._new_handle()
        write._obj.value = self._new_handle()
        return True

    def SetHandleInformation(self, *_args: Any) -> bool:
        return not self._fails("SetHandleInformation")

    def CreateFileW(self, *args: Any) -> int:
        self.create_file_count += 1
        self.created.append(args)
        if self._fails("CreateFileW") or self.fail == f"CreateFileW:{self.create_file_count}":
            return native.INVALID_HANDLE_VALUE
        handle = self._new_handle()
        file_id = self.next_file
        self.next_file += 1
        self.handle_file[handle] = file_id
        self.file_data[file_id] = bytearray()
        self.file_position[file_id] = 0
        channel = "stdout" if str(args[0]).endswith("stdout.bin") else "stderr"
        self.parent_by_channel[channel] = handle
        return handle

    def GetCurrentProcess(self) -> int:
        return -1

    def DuplicateHandle(
        self,
        _source_process: Any,
        source: Any,
        _target_process: Any,
        target: Any,
        desired_access: int,
        inheritable: bool,
        _options: int,
    ) -> bool:
        if self._fails("DuplicateHandle") or self.fail == (
            f"DuplicateHandle:{len(self.duplicate_calls) + 1}"
        ):
            return False
        source_value = int(source.value)
        duplicate = self._new_handle()
        self.handle_file[duplicate] = self.handle_file[source_value]
        target._obj.value = duplicate
        self.duplicate_calls.append((source_value, desired_access, bool(inheritable)))
        channel = next(
            name for name, parent in self.parent_by_channel.items() if parent == source_value
        )
        self.child_by_channel[channel] = duplicate
        return True

    def GetFileSizeEx(self, handle: Any, size: Any) -> bool:
        value = int(handle.value)
        self.size_calls.append(value)
        if self._fails("GetFileSizeEx") or len(self.size_calls) in self.size_fail_calls:
            set_error()
            return False
        file_id = self.handle_file[value]
        size._obj.value = self.size_override.get(value, len(self.file_data[file_id]))
        return True

    def SetFilePointerEx(self, handle: Any, offset: int, position: Any, _origin: int) -> bool:
        value = int(handle.value)
        self.seek_calls.append(value)
        if self._fails("SetFilePointerEx"):
            return False
        file_id = self.handle_file[value]
        self.file_position[file_id] = int(offset)
        position._obj.value = 1 if self.fail == "bad_seek_position" else int(offset)
        return True

    def ReadFile(
        self, handle: Any, buffer: Any, requested: int, read: Any, _overlapped: Any
    ) -> bool:
        value = int(handle.value)
        self.read_calls.append((value, requested))
        if self._fails("ReadFile"):
            return False
        file_id = self.handle_file[value]
        position = self.file_position[file_id]
        available = bytes(self.file_data[file_id][position : position + requested])
        if self.early_eof:
            available = b""
        elif self.partial_read is not None:
            available = available[: self.partial_read]
        if available:
            ctypes.memmove(buffer, available, len(available))
        read._obj.value = len(available)
        self.file_position[file_id] += len(available)
        if self.change_after_read:
            self.file_data[file_id].extend(b"!")
            self.change_after_read = False
        return True

    def CloseHandle(self, handle: Any) -> bool:
        value = int(handle.value)
        self.closed_values.append(value)
        self.operations.append(f"close:{value}")
        if value in self.close_fail_values:
            self.close_fail_values.remove(value)
            set_error(6)
            return False
        return True

    def InitializeProcThreadAttributeList(
        self, pointer: Any, _count: int, _flags: int, size: Any
    ) -> bool:
        if pointer is None:
            size._obj.value = 0 if self.fail == "attribute_size" else 64
            return False
        return not self._fails("attribute_init")

    def UpdateProcThreadAttribute(
        self,
        _list: Any,
        _flags: int,
        _attribute: int,
        value: Any,
        size: int,
        _previous: Any,
        _returned: Any,
    ) -> bool:
        if self._fails("attribute_update"):
            return False
        count = size // ctypes.sizeof(native.wintypes.HANDLE)
        array_type = native.wintypes.HANDLE * count
        handles = ctypes.cast(value, ctypes.POINTER(array_type)).contents
        self.allowlist = tuple(int(handle) for handle in handles)
        return True

    def DeleteProcThreadAttributeList(self, _pointer: Any) -> None:
        self.attribute_deleted += 1
        if self.fail == "attribute_delete":
            raise RuntimeError("attribute cleanup")

    def CreateJobObjectW(self, *_args: Any) -> int:
        if self._fails("job_create"):
            return 0
        return self._new_handle()

    def SetInformationJobObject(self, *_args: Any) -> bool:
        return not self._fails("job_configure")

    def QueryInformationJobObject(
        self, _job: Any, _kind: int, info: Any, _size: int, returned: Any
    ) -> bool:
        if self._fails("QueryInformationJobObject"):
            return False
        info._obj.ActiveProcesses = self.active_values.pop(0) if self.active_values else 0
        returned._obj.value = ctypes.sizeof(native.JOBOBJECT_BASIC_ACCOUNTING_INFORMATION)
        return True

    def CreateProcessW(
        self,
        _application: str,
        _command: Any,
        _process_security: Any,
        _thread_security: Any,
        inherit: bool,
        _flags: int,
        _environment: Any,
        _workdir: str,
        startup: Any,
        process_info: Any,
    ) -> bool:
        if self._fails("create_process"):
            return False
        assert inherit
        start = startup._obj
        self.std_handles = (
            int(start.hStdInput),
            int(start.hStdOutput),
            int(start.hStdError),
        )
        process_info._obj.hProcess = self._new_handle()
        process_info._obj.hThread = self._new_handle()
        process_info._obj.dwProcessId = 200
        process_info._obj.dwThreadId = 201
        return True

    def AssignProcessToJobObject(self, *_args: Any) -> bool:
        self.assigned += 1
        return not self._fails("job_assignment")

    def ResumeThread(self, _handle: Any) -> int:
        self.resumed += 1
        if self._fails("resume_thread"):
            return native.WAIT_FAILED
        for channel, data in self.output.items():
            file_id = self.handle_file[self.child_by_channel[channel]]
            self.file_data[file_id].extend(data)
            self.file_position[file_id] = len(self.file_data[file_id])
        return 0

    def WaitForSingleObject(self, _handle: Any, _milliseconds: int) -> int:
        if self.wait_values:
            value = self.wait_values.pop(0)
            if value == native.WAIT_FAILED:
                set_error(9)
            return value
        return native.WAIT_OBJECT_0

    def GetExitCodeProcess(self, _handle: Any, code: Any) -> bool:
        if self._fails("exit_code"):
            return False
        code._obj.value = self.exit_code
        return True

    def TerminateJobObject(self, *_args: Any) -> bool:
        self.terminated += 1
        return not self._fails("TerminateJobObject")

    def TerminateProcess(self, _handle: Any, _exit_code: int) -> bool:
        self.direct_terminated += 1
        return not self._fails("TerminateProcess")


def fake_port(kernel: FakeKernel) -> native.NativeProcessPort:
    port = object.__new__(native.NativeProcessPort)
    port._kernel32 = kernel
    return port


def spec(*, stdout_limit: int = 10, stderr_limit: int = 10) -> ProcessSpec:
    path = r"C:\Tools\helper.exe"
    return ProcessSpec(path, (path,), 1, stdout_limit, stderr_limit)


def error(phase: str = "primary") -> ProbeError:
    return probe_error(
        ProbeErrorCode.PROCESS_FAILED,
        ErrorCategory.IO,
        phase,
        phase,
    )


@pytest.mark.parametrize(
    "failure",
    [
        "CreatePipe",
        "SetHandleInformation",
        "CreateFileW:1",
        "DuplicateHandle:1",
        "CreateFileW:2",
        "DuplicateHandle:2",
        "attribute_size",
        "attribute_init",
        "attribute_update",
        "job_create",
        "job_configure",
        "create_process",
        "resume_thread",
    ],
)
def test_setup_failures_are_structured_and_close_once(failure: str) -> None:
    kernel = FakeKernel(failure)
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeStartFailed)
    assert len(kernel.closed_values) == len(set(kernel.closed_values))
    if failure == "resume_thread":
        assert kernel.terminated == 1


def test_private_files_are_create_new_distinct_and_child_only_inherited() -> None:
    kernel = FakeKernel()
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessOk)
    assert len(kernel.created) == 2
    assert {Path(call[0]).name for call in kernel.created} == {"stdout.bin", "stderr.bin"}
    assert all(call[4] == native.CREATE_NEW for call in kernel.created)
    assert all(call[5] & native.FILE_FLAG_DELETE_ON_CLOSE for call in kernel.created)
    assert all(call[2] & native.FILE_SHARE_DELETE for call in kernel.created)
    assert all(call[3] is None for call in kernel.created)
    assert all(
        access == native.GENERIC_WRITE and inherited
        for _, access, inherited in kernel.duplicate_calls
    )
    assert kernel.allowlist == kernel.std_handles
    assert kernel.allowlist[1:] == (
        kernel.child_by_channel["stdout"],
        kernel.child_by_channel["stderr"],
    )
    assert not set(kernel.parent_by_channel.values()) & set(kernel.allowlist)
    assert kernel.parent_by_channel["stdout"] != kernel.parent_by_channel["stderr"]


def test_output_handles_share_file_objects_and_reads_are_handle_bound() -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"alpha", "stderr": b"beta"}
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout == b"alpha"
    assert result.diagnostics.stderr == b"beta"
    assert kernel.seek_calls == [
        kernel.parent_by_channel["stdout"],
        kernel.parent_by_channel["stderr"],
    ]
    assert {value for value, _ in kernel.read_calls} == set(kernel.parent_by_channel.values())
    source = Path(native.__file__).read_text(encoding="utf-8")
    assert "open(" not in source


@pytest.mark.parametrize("channel", ["stdout", "stderr"])
def test_exact_limit_succeeds_and_one_over_fails_closed(channel: str) -> None:
    exact = FakeKernel()
    exact.output[channel] = b"x" * 10
    ok = fake_port(exact).run(spec(), CancellationToken())
    assert isinstance(ok, ProbeProcessOk)
    assert getattr(ok.diagnostics, channel) == b"x" * 10

    over = FakeKernel()
    over.output[channel] = b"x" * 11
    failed = fake_port(over).run(spec(), CancellationToken())
    assert isinstance(failed, ProbeOutputLimitExceeded)
    assert failed.channel == channel
    assert failed.diagnostics.stdout == failed.diagnostics.stderr == b""
    assert over.terminated == 1
    assert over.read_calls == []


@pytest.mark.parametrize(
    ("failure", "phase"),
    [
        ("GetFileSizeEx", "stdout_file_size"),
        ("SetFilePointerEx", "stdout_file_seek"),
        ("bad_seek_position", "stdout_file_seek"),
        ("ReadFile", "stdout_file_read"),
    ],
)
def test_size_seek_and_read_failures_fail_closed(failure: str, phase: str) -> None:
    kernel = FakeKernel(failure)
    kernel.output["stdout"] = b"abc"
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == phase


def test_negative_size_early_eof_and_post_read_growth_are_rejected() -> None:
    negative = FakeKernel()
    output = fake_port(negative)._output_file("unused", "stdout", 10)
    assert isinstance(output, native._OutputFile)
    negative.size_override[output.parent.value] = -1
    measured = fake_port(negative)._file_size(output)
    assert isinstance(measured, ProbeError) and measured.category is ErrorCategory.INTEGRITY

    for mode in ("early", "growth"):
        kernel = FakeKernel()
        kernel.output["stdout"] = b"abc"
        kernel.early_eof = mode == "early"
        kernel.change_after_read = mode == "growth"
        result = fake_port(kernel).run(spec(), CancellationToken())
        assert isinstance(result, ProbeProcessFailed)
        assert result.error.phase in {"stdout_file_read", "stdout_file_changed"}


def test_multichunk_and_partial_reads_are_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native, "READ_CHUNK", 2)
    kernel = FakeKernel()
    kernel.output["stdout"] = b"abcdef"
    kernel.partial_read = 1
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.stdout == b"abcdef"
    assert len(kernel.read_calls) == 6


def test_post_read_size_failure_is_structured() -> None:
    kernel = FakeKernel()
    output = fake_port(kernel)._output_file("unused", "stdout", 10)
    assert isinstance(output, native._OutputFile)
    file_id = kernel.handle_file[output.parent.value]
    kernel.file_data[file_id].extend(b"x")
    kernel.size_fail_calls.add(1)
    result = fake_port(kernel)._read_output(output, 1, native._SizeTrustState())
    assert isinstance(result, ProbeError)
    assert result.phase == "stdout_file_size"


class OneShotInvalidSizePort(native.NativeProcessPort):
    """Return one invalid adapter size while every later measurement would succeed."""

    def __init__(self, kernel: FakeKernel, invalid_call: int, invalid_value: object) -> None:
        self._kernel32 = kernel
        self.invalid_call = invalid_call
        self.invalid_value = invalid_value
        self.measurements = 0

    def _file_size(self, output: native._OutputFile) -> int | ProbeError:
        self.measurements += 1
        if self.measurements == self.invalid_call:
            return self.invalid_value  # type: ignore[return-value]
        return super()._file_size(output)


@pytest.mark.parametrize(
    (
        "final_sizes",
        "tree_terminal",
        "size_failed",
        "limit_exceeded",
        "diagnostics_allowed",
        "expected",
    ),
    [
        (native._ValidatedOutputSizes(1, 1), False, False, False, True, False),
        (None, True, False, False, True, False),
        (native._ValidatedOutputSizes(1, 1), True, True, False, True, False),
        (native._ValidatedOutputSizes(1, 1), True, False, True, True, False),
        (native._ValidatedOutputSizes(1, 1), True, False, False, False, False),
        (native._ValidatedOutputSizes(1, 1), True, False, False, True, True),
    ],
)
def test_f04_explicit_read_gate_requires_every_independent_condition(
    final_sizes: native._ValidatedOutputSizes | None,
    tree_terminal: bool,
    size_failed: bool,
    limit_exceeded: bool,
    diagnostics_allowed: bool,
    expected: bool,
) -> None:
    assert (
        native._output_read_permitted(
            final_sizes,
            process_tree_terminal_confirmed=tree_terminal,
            size_validation_failed=size_failed,
            output_limit_exceeded=limit_exceeded,
            diagnostics_read_allowed=diagnostics_allowed,
        )
        is expected
    )


@pytest.mark.parametrize(
    ("channel", "failure_call"),
    [("stdout", 3), ("stderr", 4)],
)
def test_f04_final_transient_get_file_size_failure_blocks_both_reads(
    channel: str, failure_call: int
) -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.size_fail_calls.add(failure_call)

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == f"{channel}_file_size"
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert kernel.seek_calls == []
    assert kernel.read_calls == []
    assert len(kernel.size_calls) == failure_call


@pytest.mark.parametrize(
    ("channel", "failure_call"),
    [("stdout", 3), ("stderr", 4)],
)
def test_f04_final_transient_negative_size_blocks_both_reads(
    channel: str, failure_call: int
) -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    port = OneShotInvalidSizePort(kernel, failure_call, -1)

    result = port.run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == f"{channel}_file_size"
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert kernel.seek_calls == []
    assert kernel.read_calls == []
    assert port.measurements == failure_call


@pytest.mark.parametrize("invalid_value", [object(), True])
def test_f04_impossible_size_representation_blocks_all_reads(invalid_value: object) -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    port = OneShotInvalidSizePort(kernel, 3, invalid_value)

    result = port.run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stdout_file_size"
    assert result.error.category is ErrorCategory.INTEGRITY
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert kernel.seek_calls == []
    assert kernel.read_calls == []
    assert port.measurements == 3


@pytest.mark.parametrize(
    ("channel", "failure_call", "expected_measurements"),
    [("stdout", 1, 1), ("stderr", 2, 2)],
)
def test_f04_polling_size_failure_is_sticky_and_has_no_recovery_attempt(
    channel: str, failure_call: int, expected_measurements: int
) -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.size_fail_calls.add(failure_call)
    kernel.exit_code = 7

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == f"{channel}_file_size"
    assert result.exit_code == 7
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert kernel.terminated == 1
    assert kernel.seek_calls == []
    assert kernel.read_calls == []
    assert len(kernel.size_calls) == expected_measurements


def test_f04_post_read_size_failure_stops_other_channel_and_discards_partial_output() -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.size_fail_calls.add(5)

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stdout_file_size"
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert kernel.seek_calls == [kernel.parent_by_channel["stdout"]]
    assert {handle for handle, _ in kernel.read_calls} == {kernel.parent_by_channel["stdout"]}
    assert len(kernel.size_calls) == 5


def test_f04_stderr_post_read_size_failure_discards_successful_stdout() -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.size_fail_calls.add(6)

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stderr_file_size"
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert len(kernel.read_calls) == 2
    assert len(kernel.size_calls) == 6


def test_f04_exact_limit_and_one_over_keep_distinct_limit_gate() -> None:
    exact = FakeKernel()
    exact.output = {"stdout": b"x" * 10, "stderr": b"y" * 10}
    exact_result = fake_port(exact).run(spec(), CancellationToken())
    assert isinstance(exact_result, ProbeProcessOk)
    assert exact_result.diagnostics.stdout == b"x" * 10
    assert exact_result.diagnostics.stderr == b"y" * 10

    over = FakeKernel()
    over.output["stderr"] = b"y" * 11
    over_result = fake_port(over).run(spec(), CancellationToken())
    assert isinstance(over_result, ProbeOutputLimitExceeded)
    assert over_result.channel == "stderr"
    assert over_result.error.code is ProbeErrorCode.OUTPUT_LIMIT
    assert over_result.diagnostics.stdout == over_result.diagnostics.stderr == b""
    assert over.seek_calls == []
    assert over.read_calls == []


def test_f04_output_limit_detected_only_by_final_check_still_blocks_reads() -> None:
    class DelayedVisibleSizePort(native.NativeProcessPort):
        def __init__(self, kernel: FakeKernel) -> None:
            self._kernel32 = kernel
            self.measurements = 0

        def _file_size(self, output: native._OutputFile) -> int | ProbeError:
            self.measurements += 1
            if self.measurements <= 2:
                return 0
            return super()._file_size(output)

    kernel = FakeKernel()
    kernel.output["stdout"] = b"x" * 11
    port = DelayedVisibleSizePort(kernel)

    result = port.run(spec(), CancellationToken())

    assert isinstance(result, ProbeOutputLimitExceeded)
    assert result.channel == "stdout"
    assert result.error.code is ProbeErrorCode.OUTPUT_LIMIT
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""
    assert port.measurements == 3
    assert kernel.seek_calls == []
    assert kernel.read_calls == []


def test_f04_nonzero_exit_keeps_diagnostic_reads_when_sizes_are_valid() -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.exit_code = 7

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "process_exit"
    assert result.diagnostics.stdout == b"out"
    assert result.diagnostics.stderr == b"err"


def test_f04_timeout_keeps_diagnostic_reads_when_sizes_are_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}
    kernel.wait_values = [native.WAIT_TIMEOUT]
    times = iter((0.0, 2.0, 2.0, 2.0))
    monkeypatch.setattr(native, "monotonic", lambda: next(times))

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeTimeout)
    assert result.diagnostics.stdout == b"out"
    assert result.diagnostics.stderr == b"err"


def test_f04_cancellation_keeps_diagnostic_reads_when_sizes_are_valid() -> None:
    class CancelDuringWait:
        checks = 0

        @property
        def is_cancelled(self) -> bool:
            self.checks += 1
            return self.checks >= 2

        def begin_irreversible_commit(self) -> object:
            return object()

    kernel = FakeKernel()
    kernel.output = {"stdout": b"out", "stderr": b"err"}

    result = fake_port(kernel).run(spec(), CancelDuringWait())

    assert isinstance(result, ProbeCancelled)
    assert result.diagnostics.stdout == b"out"
    assert result.diagnostics.stderr == b"err"


def test_f04_size_failure_stays_primary_when_cleanup_also_fails() -> None:
    kernel = FakeKernel("attribute_delete")
    kernel.output["stdout"] = b"out"
    kernel.size_fail_calls.add(3)

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stdout_file_size"
    assert any(error.phase == "cleanup_close" for error in result.error.secondary)
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""


@pytest.mark.parametrize("cleanup", [KeyboardInterrupt("cleanup"), SystemExit("cleanup")])
def test_f04_size_failure_survives_cleanup_base_exception(cleanup: BaseException) -> None:
    class CleanupBaseExceptionKernel(FakeKernel):
        def DeleteProcThreadAttributeList(self, _pointer: Any) -> None:
            raise cleanup

    kernel = CleanupBaseExceptionKernel()
    kernel.output["stdout"] = b"out"
    kernel.size_fail_calls.add(3)

    result = fake_port(kernel).run(spec(), CancellationToken())

    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stdout_file_size"
    assert any(error.cause is cleanup for error in result.error.secondary)
    assert result.diagnostics.stdout == result.diagnostics.stderr == b""


@pytest.mark.parametrize(
    ("wait_value", "phase"),
    [(native.WAIT_FAILED, "process_wait"), (123, "process_wait")],
)
def test_wait_errors_terminate_job(wait_value: int, phase: str) -> None:
    kernel = FakeKernel()
    kernel.wait_values = [wait_value]
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == phase
    assert kernel.terminated == 1


def test_timeout_and_inflight_cancellation_terminate_job(monkeypatch: pytest.MonkeyPatch) -> None:
    timeout_kernel = FakeKernel()
    timeout_kernel.wait_values = [native.WAIT_TIMEOUT]
    times = [0.0, 2.0]
    monkeypatch.setattr(native, "monotonic", lambda: times.pop(0) if len(times) > 1 else times[0])
    timed = fake_port(timeout_kernel).run(spec(), CancellationToken())
    assert isinstance(timed, ProbeTimeout)
    assert timeout_kernel.terminated == 1

    class CancelDuringWait:
        calls = 0

        @property
        def is_cancelled(self) -> bool:
            self.calls += 1
            return self.calls > 1

        def begin_irreversible_commit(self) -> object:
            return object()

    monkeypatch.setattr(native, "monotonic", lambda: 0.0)
    cancel_kernel = FakeKernel()
    cancelled = fake_port(cancel_kernel).run(spec(), CancelDuringWait())
    assert isinstance(cancelled, ProbeCancelled)
    assert cancel_kernel.terminated == 1


def test_pre_cancel_and_cancel_at_commit_do_not_publish_success() -> None:
    token = CancellationToken()
    assert token.cancel()
    kernel = FakeKernel()
    assert isinstance(fake_port(kernel).run(spec(), token), ProbeCancelled)
    assert kernel.created == []

    class CancelAtCommit:
        @property
        def is_cancelled(self) -> bool:
            return False

        def begin_irreversible_commit(self) -> None:
            return None

    assert isinstance(fake_port(FakeKernel()).run(spec(), CancelAtCommit()), ProbeCancelled)


def test_descendants_are_terminated_before_read_and_nonterminal_tree_is_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = FakeKernel()
    child.output["stdout"] = b"stable"
    child.active_values = [1, 0]
    ok = fake_port(child).run(spec(), CancellationToken())
    assert isinstance(ok, ProbeProcessOk)
    assert child.terminated == 1 and ok.diagnostics.stdout == b"stable"

    stuck = FakeKernel()
    stuck.output["stdout"] = b"unsafe"
    stuck.active_values = [1, 1]
    monkeypatch.setattr(native, "TERMINATION_WAIT_MS", 0)
    failed = fake_port(stuck).run(spec(), CancellationToken())
    assert isinstance(failed, ProbeProcessFailed)
    assert failed.error.phase == "job_terminal_wait"
    assert stuck.read_calls == []

    polled = FakeKernel()
    polled.active_values = [1, 1, 0]
    monkeypatch.setattr(native, "TERMINATION_WAIT_MS", 100)
    monkeypatch.setattr(native, "sleep", lambda _seconds: None)
    monkeypatch.setattr(native, "monotonic", lambda: 0.0)
    assert isinstance(fake_port(polled).run(spec(), CancellationToken()), ProbeProcessOk)


def test_job_query_and_termination_failures_are_structured() -> None:
    query = FakeKernel("QueryInformationJobObject")
    result = fake_port(query).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "job_accounting"

    terminate = FakeKernel("TerminateJobObject")
    terminate.wait_values = [native.WAIT_FAILED]
    result = fake_port(terminate).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "process_wait"
    assert any(item.phase == "job_terminate" for item in result.error.secondary)

    clean_latch = _TerminalLatch()
    terminate.active_values = [0]
    assert fake_port(terminate)._terminate_tree(native._Handle(500, terminate, "job"), clean_latch)
    assert clean_latch.error() is not None
    assert clean_latch.error().phase == "job_terminate"


def test_process_exit_latch_is_idempotent() -> None:
    latch = _TerminalLatch()
    latch.process_exited()
    latch.process_exited()


@pytest.mark.parametrize("terminal_wait", [native.WAIT_OBJECT_0, native.WAIT_TIMEOUT])
def test_assign_failure_directly_terminates_without_resume(terminal_wait: int) -> None:
    kernel = FakeKernel("job_assignment")
    kernel.wait_values = [terminal_wait]
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeStartFailed)
    assert kernel.direct_terminated == 1
    assert kernel.resumed == 0
    if terminal_wait == native.WAIT_TIMEOUT:
        assert any(
            item.phase == "process_assignment_terminal_wait" for item in result.error.secondary
        )


def test_direct_termination_failure_is_secondary() -> None:
    kernel = FakeKernel("job_assignment")
    kernel.wait_values = [native.WAIT_OBJECT_0]
    original = kernel.TerminateProcess

    def fail_terminate(handle: Any, code: int) -> bool:
        original(handle, code)
        set_error(5)
        return False

    kernel.TerminateProcess = fail_terminate  # type: ignore[method-assign]
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeStartFailed)
    assert any(item.phase == "process_direct_terminate" for item in result.error.secondary)


def test_nonzero_exit_and_exit_code_failure_are_process_failures() -> None:
    nonzero = FakeKernel()
    nonzero.exit_code = 7
    result = fake_port(nonzero).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed) and result.exit_code == 7

    failed = fake_port(FakeKernel("exit_code")).run(spec(), CancellationToken())
    assert isinstance(failed, ProbeProcessFailed)
    assert failed.error.phase == "process_exit_code"


def test_mandatory_close_failure_prevents_success_and_is_never_retried() -> None:
    kernel = FakeKernel()
    port = fake_port(kernel)
    original = port._wait

    def mark_parent(*args: Any, **kwargs: Any) -> tuple[str, bool, str | None]:
        kernel.close_fail_values.add(kernel.parent_by_channel["stdout"])
        return original(*args, **kwargs)

    port._wait = mark_parent  # type: ignore[method-assign]
    result = port.run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    parent = kernel.parent_by_channel["stdout"]
    assert kernel.closed_values.count(parent) == 1


def test_inherited_handle_close_failure_prevents_success_without_retry() -> None:
    kernel = FakeKernel()
    kernel.close_fail_values.add(100)
    result = fake_port(kernel).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert kernel.closed_values.count(100) == 1


def test_attribute_cleanup_failure_prevents_success() -> None:
    result = fake_port(FakeKernel("attribute_delete")).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "cleanup_close"


def test_workdir_cleanup_failure_is_secondary_to_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained = tmp_path / "retained"

    def create_retained(**_kwargs: object) -> str:
        retained.mkdir()
        return str(retained)

    monkeypatch.setattr(native.tempfile, "mkdtemp", create_retained)
    monkeypatch.setattr(native, "_remove_workdir_bounded", lambda _path: error("workdir"))
    result = fake_port(FakeKernel()).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessOk)
    assert result.diagnostics.cleanup_errors[0].phase == "workdir"
    retained.rmdir()


def test_workdir_creation_failure_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native.tempfile, "mkdtemp", lambda **_kwargs: (_ for _ in ()).throw(OSError("full"))
    )
    result = fake_port(FakeKernel()).run(spec(), CancellationToken())
    assert isinstance(result, ProbeStartFailed)
    assert result.error.phase == "working_directory_create"


def test_stderr_final_size_failure_and_finalize_cancellation() -> None:
    size_error = FakeKernel()
    size_error.size_fail_calls.add(6)
    result = fake_port(size_error).run(spec(), CancellationToken())
    assert isinstance(result, ProbeProcessFailed)
    assert result.error.phase == "stderr_file_size"

    class CancelAtFinalize:
        checks = 0

        @property
        def is_cancelled(self) -> bool:
            self.checks += 1
            return self.checks >= 3

        def begin_irreversible_commit(self) -> object:
            return object()

    cancelled = fake_port(FakeKernel()).run(spec(), CancelAtFinalize())
    assert isinstance(cancelled, ProbeCancelled)
    assert cancelled.error.phase == "process_finalize"


def test_unexpected_wait_unwind_preserves_exception_and_terminates_tree() -> None:
    kernel = FakeKernel()
    port = fake_port(kernel)
    failure = RuntimeError("unexpected wait")

    def fail_wait(*_args: object, **_kwargs: object) -> tuple[str, bool, str | None]:
        raise failure

    port._wait = fail_wait  # type: ignore[method-assign]
    with pytest.raises(RuntimeError) as raised:
        port.run(spec(), CancellationToken())
    assert raised.value is failure
    assert kernel.terminated == 1


def test_close_helpers_and_workdir_retry_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = FakeKernel()
    handle = native._Handle(1, kernel, "x")
    assert handle.close() is None
    with pytest.raises(RuntimeError, match="double-close"):
        handle.close()

    class BadClose:
        name = "bad"

        def close(self) -> None:
            raise KeyboardInterrupt

    cleanup = native._close_safely(BadClose())  # type: ignore[arg-type]
    assert cleanup is not None and isinstance(cleanup.cause, KeyboardInterrupt)

    values: Iterator[float] = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(native, "monotonic", lambda: next(values))
    monkeypatch.setattr(native, "sleep", lambda _seconds: None)
    monkeypatch.setattr(native.os, "rmdir", lambda _path: (_ for _ in ()).throw(OSError("busy")))
    failed = native._remove_workdir_bounded("x")
    assert failed is not None and failed.phase == "working_directory_cleanup"


def test_cleanup_exception_survives_broken_stringification() -> None:
    class Broken(BaseException):
        def __str__(self) -> str:
            raise RuntimeError

    result = native._cleanup_exception("resource", Broken())
    assert "unavailable" in result.message


def test_model_c_contains_none_of_the_removed_pipe_architecture() -> None:
    source = Path(native.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "CreateNamedPipeW",
        "ConnectNamedPipe",
        "PeekNamedPipe",
        "OVERLAPPED",
        "CancelIoEx",
        "GetOverlappedResult",
        "CreateEventW",
        "ResetEvent",
        "WaitForMultipleObjects",
        "threading",
        "pipe_shutdown",
    ):
        assert forbidden not in source


def test_constructor_rejects_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(native.ctypes, "WinDLL")
    with pytest.raises(OSError, match="requires Windows"):
        native.NativeProcessPort()


def test_configure_declares_file_and_job_apis() -> None:
    names = native.NativeProcessPort._configure.__code__.co_names
    for required in (
        "CreateFileW",
        "DuplicateHandle",
        "GetFileSizeEx",
        "SetFilePointerEx",
        "ReadFile",
        "QueryInformationJobObject",
    ):
        assert required in names
    assert native.STARTUPINFOW.dwFlags.offset > native.STARTUPINFOW.dwFillAttribute.offset
