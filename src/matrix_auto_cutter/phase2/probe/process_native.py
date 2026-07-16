"""Native Windows process adapter with private handle-bound output files."""

from __future__ import annotations

import ctypes
import os
import tempfile
from ctypes import wintypes
from dataclasses import dataclass
from time import monotonic, sleep
from typing import Any, ClassVar, Final, TypeGuard, cast

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.errors import (
    ProbeError,
    ProbeErrorCode,
    _TerminalKind,
    _TerminalLatch,
    probe_error,
)
from matrix_auto_cutter.phase2.probe.process_port import (
    ProbeCancelled,
    ProbeOutputLimitExceeded,
    ProbeProcessFailed,
    ProbeProcessOk,
    ProbeProcessResult,
    ProbeStartFailed,
    ProbeTimeout,
    ProcessDiagnostics,
    ProcessSpec,
    serialize_windows_command_line,
)

INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value
HANDLE_FLAG_INHERIT: Final = 0x00000001
STARTF_USESTDHANDLES: Final = 0x00000100
CREATE_SUSPENDED: Final = 0x00000004
EXTENDED_STARTUPINFO_PRESENT: Final = 0x00080000
PROC_THREAD_ATTRIBUTE_HANDLE_LIST: Final = 0x00020002
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS: Final = 1
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS: Final = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: Final = 0x00002000
WAIT_OBJECT_0: Final = 0
WAIT_TIMEOUT: Final = 258
WAIT_FAILED: Final = 0xFFFFFFFF
READ_CHUNK: Final = 64 * 1024
WAIT_SLICE_MS: Final = 25
TERMINATION_WAIT_MS: Final = 5000
WORKDIR_CLEANUP_TIMEOUT_MS: Final = 500
GENERIC_READ: Final = 0x80000000
GENERIC_WRITE: Final = 0x40000000
DELETE: Final = 0x00010000
FILE_SHARE_READ: Final = 0x00000001
FILE_SHARE_WRITE: Final = 0x00000002
FILE_SHARE_DELETE: Final = 0x00000004
CREATE_NEW: Final = 1
FILE_BEGIN: Final = 0
FILE_ATTRIBUTE_TEMPORARY: Final = 0x00000100
FILE_FLAG_DELETE_ON_CLOSE: Final = 0x04000000


class SECURITY_ATTRIBUTES(ctypes.Structure):
    """Win32 SECURITY_ATTRIBUTES."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", wintypes.LPVOID),
        ("bInheritHandle", wintypes.BOOL),
    ]


class STARTUPINFOW(ctypes.Structure):
    """Win32 STARTUPINFOW."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    """Win32 STARTUPINFOEXW."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("StartupInfo", STARTUPINFOW),
        ("lpAttributeList", wintypes.LPVOID),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    """Win32 PROCESS_INFORMATION."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    """Win32 IO_COUNTERS."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    """Win32 JOBOBJECT_BASIC_LIMIT_INFORMATION."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    """Win32 JOBOBJECT_EXTENDED_LIMIT_INFORMATION."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    """Win32 JOBOBJECT_BASIC_ACCOUNTING_INFORMATION."""

    _fields_: ClassVar[list[tuple[str, type[Any]]]] = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


@dataclass(slots=True)
class _Handle:
    value: int
    kernel32: Any
    name: str
    closed: bool = False

    def close(self) -> ProbeError | None:
        """Attempt exactly one native close and retain an explicit local terminal state."""
        if self.closed:
            raise RuntimeError(f"double-close: {self.name}")
        self.closed = True
        if not self.kernel32.CloseHandle(wintypes.HANDLE(self.value)):
            code = ctypes.get_last_error()
            return _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                "cleanup_close",
                f"CloseHandle failed for {self.name}",
                code,
            )
        return None


@dataclass(slots=True)
class _OutputFile:
    """One private file object with parent-read and inheritable child-write handles."""

    parent: _Handle
    child: _Handle
    path: str
    channel: str
    limit: int


@dataclass(slots=True)
class _SizeTrustState:
    """Retain a size-validation failure for the remainder of one process run."""

    validation_failed: bool = False


@dataclass(frozen=True, slots=True)
class _ValidatedOutputSizes:
    """The jointly validated stdout and stderr sizes from one check."""

    stdout: int
    stderr: int


@dataclass(frozen=True, slots=True)
class _OutputLimitDetected:
    """Identify the first channel observed beyond its configured bound."""

    channel: str


@dataclass(frozen=True, slots=True)
class _SizeValidationFailed:
    """Distinguish an invalid measurement from a successful size check."""


def _output_read_permitted(
    final_sizes: _ValidatedOutputSizes | None,
    *,
    process_tree_terminal_confirmed: bool,
    size_validation_failed: bool,
    output_limit_exceeded: bool,
    diagnostics_read_allowed: bool,
) -> TypeGuard[_ValidatedOutputSizes]:
    """Require every independent run-local gate before reading either output."""
    return (
        process_tree_terminal_confirmed
        and final_sizes is not None
        and not size_validation_failed
        and not output_limit_exceeded
        and diagnostics_read_allowed
    )


class _StartAbort(Exception):
    """Internal structured unwind for setup failures."""

    def __init__(self, error: ProbeError) -> None:
        super().__init__(error.phase)
        self.error = error


def _native_error(
    code: ProbeErrorCode,
    phase: str,
    message: str,
    win32_code: int,
    *,
    category: ErrorCategory = ErrorCategory.IO,
    retryable: bool = False,
) -> ProbeError:
    detail = ctypes.FormatError(win32_code).strip()
    return probe_error(
        code,
        category,
        phase,
        f"{message}: {detail}",
        win32_code=win32_code,
        cause=OSError(win32_code, detail),
        retryable=retryable,
    )


def _cleanup_exception(name: str, exc: BaseException) -> ProbeError:
    try:
        detail = str(exc) or type(exc).__name__
    except BaseException:
        detail = "cleanup exception detail unavailable"
    return probe_error(
        ProbeErrorCode.PROCESS_FAILED,
        ErrorCategory.IO,
        "cleanup_close",
        f"cleanup failed for {name}: {detail}",
        cause=exc,
    )


def _close_safely(handle: _Handle) -> ProbeError | None:
    try:
        return handle.close()
    except BaseException as exc:
        return _cleanup_exception(handle.name, exc)


def _remove_workdir_bounded(path: str) -> ProbeError | None:
    """Retry directory removal within one fixed user-mode cleanup deadline."""
    deadline = monotonic() + WORKDIR_CLEANUP_TIMEOUT_MS / 1000
    while True:
        try:
            os.rmdir(path)
            return None
        except OSError as exc:
            remaining = deadline - monotonic()
            if remaining <= 0:
                return probe_error(
                    ProbeErrorCode.PROCESS_FAILED,
                    ErrorCategory.IO,
                    "working_directory_cleanup",
                    str(exc),
                    cause=exc,
                )
            sleep(min(0.01, remaining))


def _terminate_unassigned_process(
    kernel32: ctypes.WinDLL,
    process: _Handle,
    latch: _TerminalLatch,
) -> bool:
    """Directly terminate an unassigned child and boundedly prove terminality."""
    if not kernel32.TerminateProcess(wintypes.HANDLE(process.value), 1):
        code = ctypes.get_last_error()
        latch.fail(
            _TerminalKind.PROCESS_CONTROL,
            _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                "process_direct_terminate",
                "TerminateProcess failed for unassigned child",
                code,
            ),
        )
    waited = kernel32.WaitForSingleObject(
        wintypes.HANDLE(process.value),
        TERMINATION_WAIT_MS,
    )
    if waited == WAIT_OBJECT_0:
        return True
    code = ctypes.get_last_error() if waited == WAIT_FAILED else WAIT_TIMEOUT
    latch.fail(
        _TerminalKind.PROCESS_CONTROL,
        _native_error(
            ProbeErrorCode.PROCESS_FAILED,
            "process_assignment_terminal_wait",
            "unassigned child did not reach a terminal state within the bound",
            code,
        ),
    )
    return False


class NativeProcessPort:
    """Run one bounded local process with private handle-bound output files."""

    def __init__(self) -> None:
        """Load and type every kernel32 function used by the adapter."""
        if not hasattr(ctypes, "WinDLL"):
            raise OSError("NativeProcessPort requires Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure()

    def _configure(self) -> None:
        k32 = self._kernel32
        k32.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(SECURITY_ATTRIBUTES),
            wintypes.DWORD,
        ]
        k32.CreatePipe.restype = wintypes.BOOL
        k32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        k32.SetHandleInformation.restype = wintypes.BOOL
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.GetCurrentProcess.argtypes = []
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.DuplicateHandle.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        k32.DuplicateHandle.restype = wintypes.BOOL
        k32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
        k32.GetFileSizeEx.restype = wintypes.BOOL
        k32.SetFilePointerEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_longlong,
            ctypes.POINTER(ctypes.c_longlong),
            wintypes.DWORD,
        ]
        k32.SetFilePointerEx.restype = wintypes.BOOL
        k32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        k32.ReadFile.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.InitializeProcThreadAttributeList.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k32.UpdateProcThreadAttribute.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.c_size_t,
            wintypes.LPVOID,
            ctypes.c_size_t,
            wintypes.LPVOID,
            wintypes.LPVOID,
        ]
        k32.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
        k32.DeleteProcThreadAttributeList.restype = None
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        k32.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        k32.CreateProcessW.restype = wintypes.BOOL
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.ResumeThread.argtypes = [wintypes.HANDLE]
        k32.ResumeThread.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetExitCodeProcess.restype = wintypes.BOOL
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateJobObject.restype = wintypes.BOOL
        k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.TerminateProcess.restype = wintypes.BOOL

    def _stdin_pipe(self) -> tuple[_Handle, _Handle] | ProbeError:
        security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
        read = wintypes.HANDLE()
        write = wintypes.HANDLE()
        if not self._kernel32.CreatePipe(
            ctypes.byref(read), ctypes.byref(write), ctypes.byref(security), 0
        ):
            return _native_error(
                ProbeErrorCode.START_FAILED,
                "stdin_create",
                "CreatePipe failed for stdin",
                ctypes.get_last_error(),
            )
        read_handle = _Handle(cast(int, read.value), self._kernel32, "stdin_read")
        write_handle = _Handle(cast(int, write.value), self._kernel32, "stdin_write")
        if not self._kernel32.SetHandleInformation(
            wintypes.HANDLE(write_handle.value), HANDLE_FLAG_INHERIT, 0
        ):
            code = ctypes.get_last_error()
            secondary = tuple(
                error
                for handle in (read_handle, write_handle)
                for error in (_close_safely(handle),)
                if error is not None
            )
            return probe_error(
                ProbeErrorCode.START_FAILED,
                ErrorCategory.IO,
                "stdin_inheritance",
                "SetHandleInformation failed for stdin",
                win32_code=code,
                cause=OSError(code, ctypes.FormatError(code)),
                secondary=secondary,
            )
        return read_handle, write_handle

    def _output_file(self, workdir: str, channel: str, limit: int) -> _OutputFile | ProbeError:
        path = os.path.join(workdir, f"{channel}.bin")
        raw = self._kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE | DELETE,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE,
            None,
        )
        value = cast(int | None, raw)
        if value in {None, INVALID_HANDLE_VALUE}:
            return _native_error(
                ProbeErrorCode.START_FAILED,
                f"{channel}_file_create",
                f"CreateFileW failed for {channel}",
                ctypes.get_last_error(),
            )
        parent = _Handle(cast(int, value), self._kernel32, f"{channel}_parent")
        duplicate = wintypes.HANDLE()
        current = self._kernel32.GetCurrentProcess()
        if not self._kernel32.DuplicateHandle(
            current,
            wintypes.HANDLE(parent.value),
            current,
            ctypes.byref(duplicate),
            GENERIC_WRITE,
            True,
            0,
        ):
            code = ctypes.get_last_error()
            close_error = _close_safely(parent)
            secondary = () if close_error is None else (close_error,)
            return probe_error(
                ProbeErrorCode.START_FAILED,
                ErrorCategory.IO,
                f"{channel}_handle_duplicate",
                f"DuplicateHandle failed for {channel}",
                win32_code=code,
                cause=OSError(code, ctypes.FormatError(code)),
                secondary=secondary,
            )
        child = _Handle(cast(int, duplicate.value), self._kernel32, f"{channel}_child")
        return _OutputFile(parent, child, path, channel, limit)

    def _file_size(self, output: _OutputFile) -> int | ProbeError:
        size = ctypes.c_longlong()
        if not self._kernel32.GetFileSizeEx(
            wintypes.HANDLE(output.parent.value), ctypes.byref(size)
        ):
            return _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                f"{output.channel}_file_size",
                f"GetFileSizeEx failed for {output.channel}",
                ctypes.get_last_error(),
            )
        value = int(size.value)
        if value < 0:
            return probe_error(
                ProbeErrorCode.PROCESS_FAILED,
                ErrorCategory.INTEGRITY,
                f"{output.channel}_file_size",
                f"negative output size reported for {output.channel}",
            )
        return value

    def _validated_file_size(self, output: _OutputFile) -> int | ProbeError:
        """Reject adapter results that are not one concrete non-negative integer."""
        measured: object = self._file_size(output)
        if isinstance(measured, ProbeError):
            return measured
        if type(measured) is not int or measured < 0:
            return probe_error(
                ProbeErrorCode.PROCESS_FAILED,
                ErrorCategory.INTEGRITY,
                f"{output.channel}_file_size",
                f"invalid output size reported for {output.channel}",
            )
        return measured

    def _check_sizes(
        self,
        outputs: tuple[_OutputFile, _OutputFile],
        latch: _TerminalLatch,
        size_trust: _SizeTrustState,
    ) -> _ValidatedOutputSizes | _OutputLimitDetected | _SizeValidationFailed:
        measured_sizes: list[int] = []
        for output in outputs:
            measured = self._validated_file_size(output)
            if isinstance(measured, ProbeError):
                size_trust.validation_failed = True
                latch.fail(_TerminalKind.READER_IO, measured)
                return _SizeValidationFailed()
            if measured > output.limit:
                latch.fail(
                    _TerminalKind.OUTPUT_LIMIT,
                    probe_error(
                        ProbeErrorCode.OUTPUT_LIMIT,
                        ErrorCategory.INTEGRITY,
                        "process_output",
                        f"{output.channel} exceeded its bounded limit",
                    ),
                )
                return _OutputLimitDetected(output.channel)
            measured_sizes.append(measured)
        return _ValidatedOutputSizes(measured_sizes[0], measured_sizes[1])

    def _read_output(
        self,
        output: _OutputFile,
        expected_size: int,
        size_trust: _SizeTrustState,
    ) -> bytes | ProbeError:
        position = ctypes.c_longlong()
        if not self._kernel32.SetFilePointerEx(
            wintypes.HANDLE(output.parent.value),
            0,
            ctypes.byref(position),
            FILE_BEGIN,
        ):
            return _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                f"{output.channel}_file_seek",
                f"SetFilePointerEx failed for {output.channel}",
                ctypes.get_last_error(),
            )
        if position.value != 0:
            return probe_error(
                ProbeErrorCode.PROCESS_FAILED,
                ErrorCategory.INTEGRITY,
                f"{output.channel}_file_seek",
                f"unexpected file position for {output.channel}",
            )
        data = bytearray()
        remaining = expected_size
        while remaining:
            requested = min(READ_CHUNK, remaining)
            buffer = ctypes.create_string_buffer(requested)
            read = wintypes.DWORD()
            if not self._kernel32.ReadFile(
                wintypes.HANDLE(output.parent.value),
                buffer,
                requested,
                ctypes.byref(read),
                None,
            ):
                return _native_error(
                    ProbeErrorCode.PROCESS_FAILED,
                    f"{output.channel}_file_read",
                    f"ReadFile failed for {output.channel}",
                    ctypes.get_last_error(),
                )
            count = int(read.value)
            if count < 1 or count > requested:
                return probe_error(
                    ProbeErrorCode.PROCESS_FAILED,
                    ErrorCategory.INTEGRITY,
                    f"{output.channel}_file_read",
                    f"incomplete bounded read for {output.channel}",
                )
            data.extend(buffer.raw[:count])
            remaining -= count
        after = self._validated_file_size(output)
        if isinstance(after, ProbeError):
            size_trust.validation_failed = True
            return after
        if after != expected_size:
            size_trust.validation_failed = True
            return probe_error(
                ProbeErrorCode.PROCESS_FAILED,
                ErrorCategory.INTEGRITY,
                f"{output.channel}_file_changed",
                f"output size changed after terminal confirmation for {output.channel}",
            )
        return bytes(data)

    def _active_processes(self, job: _Handle) -> int | ProbeError:
        accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD()
        if not self._kernel32.QueryInformationJobObject(
            wintypes.HANDLE(job.value),
            JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION_CLASS,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            ctypes.byref(returned),
        ):
            return _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                "job_accounting",
                "QueryInformationJobObject failed",
                ctypes.get_last_error(),
            )
        return int(accounting.ActiveProcesses)

    def _confirm_job_terminal(self, job: _Handle, latch: _TerminalLatch) -> bool:
        deadline = monotonic() + TERMINATION_WAIT_MS / 1000
        while True:
            active = self._active_processes(job)
            if isinstance(active, ProbeError):
                latch.fail(_TerminalKind.PROCESS_CONTROL, active)
                return False
            if active == 0:
                return True
            remaining = deadline - monotonic()
            if remaining <= 0:
                latch.fail(
                    _TerminalKind.PROCESS_CONTROL,
                    probe_error(
                        ProbeErrorCode.PROCESS_FAILED,
                        ErrorCategory.IO,
                        "job_terminal_wait",
                        "job process tree did not become terminal within the bound",
                    ),
                )
                return False
            sleep(min(WAIT_SLICE_MS / 1000, remaining))

    def _terminate_tree(self, job: _Handle, latch: _TerminalLatch) -> bool:
        if not self._kernel32.TerminateJobObject(wintypes.HANDLE(job.value), 1):
            error = _native_error(
                ProbeErrorCode.PROCESS_FAILED,
                "job_terminate",
                "TerminateJobObject failed",
                ctypes.get_last_error(),
            )
            if latch.error() is None:
                latch.fail(_TerminalKind.PROCESS_CONTROL, error)
            else:
                latch.diagnose(error)
        return self._confirm_job_terminal(job, latch)

    def _wait(
        self,
        process: _Handle,
        job: _Handle,
        outputs: tuple[_OutputFile, _OutputFile],
        cancellation: CancellationToken,
        timeout_seconds: int,
        latch: _TerminalLatch,
        size_trust: _SizeTrustState,
    ) -> tuple[str, bool, str | None]:
        deadline = monotonic() + timeout_seconds
        while True:
            size_check = self._check_sizes(outputs, latch, size_trust)
            channel = size_check.channel if isinstance(size_check, _OutputLimitDetected) else None
            latched = latch.error()
            if latched is not None:
                terminal = self._terminate_tree(job, latch)
                kind = latch.kind
                outcome = (
                    "limit"
                    if kind is _TerminalKind.OUTPUT_LIMIT
                    else "cancelled"
                    if kind is _TerminalKind.CANCELLED
                    else "timeout"
                    if kind is _TerminalKind.TIMEOUT
                    else "failed"
                )
                return outcome, terminal, channel
            if cancellation.is_cancelled:
                latch.fail(
                    _TerminalKind.CANCELLED,
                    probe_error(
                        ProbeErrorCode.CANCELLED,
                        ErrorCategory.CANCELLED,
                        "process_wait",
                        "process cancelled",
                    ),
                )
                return "cancelled", self._terminate_tree(job, latch), None
            waited = self._kernel32.WaitForSingleObject(
                wintypes.HANDLE(process.value), WAIT_SLICE_MS
            )
            if waited == WAIT_OBJECT_0:
                latch.process_exited()
                active = self._active_processes(job)
                if isinstance(active, ProbeError):
                    latch.fail(_TerminalKind.PROCESS_CONTROL, active)
                    return "failed", self._terminate_tree(job, latch), None
                if active == 0:
                    return "ok", True, None
                terminal = self._terminate_tree(job, latch)
                return ("ok" if terminal and latch.error() is None else "failed"), terminal, None
            if waited == WAIT_FAILED:
                latch.fail(
                    _TerminalKind.PROCESS_CONTROL,
                    _native_error(
                        ProbeErrorCode.PROCESS_FAILED,
                        "process_wait",
                        "WaitForSingleObject failed",
                        ctypes.get_last_error(),
                    ),
                )
                return "failed", self._terminate_tree(job, latch), None
            if waited != WAIT_TIMEOUT:
                latch.fail(
                    _TerminalKind.PROCESS_CONTROL,
                    probe_error(
                        ProbeErrorCode.PROCESS_FAILED,
                        ErrorCategory.IO,
                        "process_wait",
                        f"unexpected wait result {waited}",
                    ),
                )
                return "failed", self._terminate_tree(job, latch), None
            if monotonic() >= deadline:
                latch.fail(
                    _TerminalKind.TIMEOUT,
                    probe_error(
                        ProbeErrorCode.TIMEOUT,
                        ErrorCategory.IO,
                        "process_wait",
                        "process timeout expired",
                        retryable=True,
                    ),
                )
                return "timeout", self._terminate_tree(job, latch), None

    def run(self, spec: ProcessSpec, cancellation: CancellationToken) -> ProbeProcessResult:
        """Start the exact application and return one exhaustive bounded outcome."""
        if cancellation.is_cancelled:
            error = probe_error(
                ProbeErrorCode.CANCELLED,
                ErrorCategory.CANCELLED,
                "process_before_start",
                "process cancelled before start",
            )
            return ProbeCancelled(error, ProcessDiagnostics(b"", b""))
        try:
            workdir = tempfile.mkdtemp(
                prefix="matrix-auto-cutter-probe-", dir=tempfile.gettempdir()
            )
        except OSError as exc:
            error = probe_error(
                ProbeErrorCode.START_FAILED,
                ErrorCategory.IO,
                "working_directory_create",
                str(exc),
                cause=exc,
            )
            return ProbeStartFailed(error, ProcessDiagnostics(b"", b""))

        stdin_read: _Handle | None = None
        stdin_write: _Handle | None = None
        stdout: _OutputFile | None = None
        stderr: _OutputFile | None = None
        job: _Handle | None = None
        process: _Handle | None = None
        thread: _Handle | None = None
        attribute_list: ctypes.Array[ctypes.c_char] | None = None
        attribute_initialized = False
        assigned_to_job = False
        tree_terminal = False
        latch = _TerminalLatch()
        size_trust = _SizeTrustState()
        outcome = "start_failed"
        limit_channel: str | None = None
        primary: ProbeError | None = None
        exit_code: int | None = None
        stdout_data = b""
        stderr_data = b""
        cleanup_errors: list[ProbeError] = []
        mandatory_cleanup_errors: list[ProbeError] = []

        try:
            stdin_pair = self._stdin_pipe()
            if isinstance(stdin_pair, ProbeError):
                raise _StartAbort(stdin_pair)
            stdin_read, stdin_write = stdin_pair
            stdout_result = self._output_file(workdir, "stdout", spec.stdout_limit)
            if isinstance(stdout_result, ProbeError):
                raise _StartAbort(stdout_result)
            stdout = stdout_result
            stderr_result = self._output_file(workdir, "stderr", spec.stderr_limit)
            if isinstance(stderr_result, ProbeError):
                raise _StartAbort(stderr_result)
            stderr = stderr_result

            size = ctypes.c_size_t()
            self._kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            if size.value == 0:
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "attribute_list_size",
                        "InitializeProcThreadAttributeList size query failed",
                        ctypes.get_last_error(),
                    )
                )
            attribute_list = ctypes.create_string_buffer(size.value)
            pointer = ctypes.cast(attribute_list, wintypes.LPVOID)
            if not self._kernel32.InitializeProcThreadAttributeList(
                pointer, 1, 0, ctypes.byref(size)
            ):
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "attribute_list_init",
                        "InitializeProcThreadAttributeList failed",
                        ctypes.get_last_error(),
                    )
                )
            attribute_initialized = True
            inherited = (wintypes.HANDLE * 3)(
                wintypes.HANDLE(stdin_read.value),
                wintypes.HANDLE(stdout.child.value),
                wintypes.HANDLE(stderr.child.value),
            )
            if not self._kernel32.UpdateProcThreadAttribute(
                pointer,
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(inherited, wintypes.LPVOID),
                ctypes.sizeof(inherited),
                None,
                None,
            ):
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "attribute_handle_list",
                        "UpdateProcThreadAttribute failed",
                        ctypes.get_last_error(),
                    )
                )
            job_raw = self._kernel32.CreateJobObjectW(None, None)
            job_value = cast(int | None, job_raw)
            if not job_value:
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "job_create",
                        "CreateJobObjectW failed",
                        ctypes.get_last_error(),
                    )
                )
            job = _Handle(job_value, self._kernel32, "job")
            limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not self._kernel32.SetInformationJobObject(
                wintypes.HANDLE(job.value),
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            ):
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "job_configure",
                        "SetInformationJobObject failed",
                        ctypes.get_last_error(),
                    )
                )
            startup = STARTUPINFOEXW()
            startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = wintypes.HANDLE(stdin_read.value)
            startup.StartupInfo.hStdOutput = wintypes.HANDLE(stdout.child.value)
            startup.StartupInfo.hStdError = wintypes.HANDLE(stderr.child.value)
            startup.lpAttributeList = pointer
            process_info = PROCESS_INFORMATION()
            command_line = ctypes.create_unicode_buffer(
                serialize_windows_command_line(spec.arguments)
            )
            if not self._kernel32.CreateProcessW(
                spec.application_path,
                command_line,
                None,
                None,
                True,
                CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT,
                None,
                workdir,
                ctypes.byref(startup.StartupInfo),
                ctypes.byref(process_info),
            ):
                raise _StartAbort(
                    _native_error(
                        ProbeErrorCode.START_FAILED,
                        "create_process",
                        "CreateProcessW failed",
                        ctypes.get_last_error(),
                    )
                )
            process = _Handle(cast(int, process_info.hProcess), self._kernel32, "process")
            thread = _Handle(cast(int, process_info.hThread), self._kernel32, "thread")
            for inherited_handle in (stdin_read, stdin_write, stdout.child, stderr.child):
                close_error = _close_safely(inherited_handle)
                if close_error is not None:
                    mandatory_cleanup_errors.append(close_error)
            if not self._kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job.value), wintypes.HANDLE(process.value)
            ):
                error = _native_error(
                    ProbeErrorCode.PROCESS_FAILED,
                    "job_assignment",
                    "AssignProcessToJobObject failed",
                    ctypes.get_last_error(),
                )
                latch.fail(_TerminalKind.PROCESS_CONTROL, error)
                tree_terminal = _terminate_unassigned_process(self._kernel32, process, latch)
                primary = latch.error()
                assert primary is not None
                raise _StartAbort(primary)
            assigned_to_job = True
            if self._kernel32.ResumeThread(wintypes.HANDLE(thread.value)) == WAIT_FAILED:
                error = _native_error(
                    ProbeErrorCode.PROCESS_FAILED,
                    "resume_thread",
                    "ResumeThread failed",
                    ctypes.get_last_error(),
                )
                latch.fail(_TerminalKind.PROCESS_CONTROL, error)
                raise _StartAbort(error)
            outcome, tree_terminal, limit_channel = self._wait(
                process,
                job,
                (stdout, stderr),
                cancellation,
                spec.timeout_seconds,
                latch,
                size_trust,
            )
            if tree_terminal:
                exit_value = wintypes.DWORD()
                if not self._kernel32.GetExitCodeProcess(
                    wintypes.HANDLE(process.value), ctypes.byref(exit_value)
                ):
                    latch.fail(
                        _TerminalKind.EXIT_CODE,
                        _native_error(
                            ProbeErrorCode.PROCESS_FAILED,
                            "process_exit_code",
                            "GetExitCodeProcess failed",
                            ctypes.get_last_error(),
                        ),
                    )
                else:
                    exit_code = int(exit_value.value)
                    if outcome == "ok" and exit_code != 0:
                        latch.fail(
                            _TerminalKind.EXIT_CODE,
                            probe_error(
                                ProbeErrorCode.PROCESS_FAILED,
                                ErrorCategory.INPUT,
                                "process_exit",
                                f"process exited with code {exit_code}",
                            ),
                        )
                final_sizes: _ValidatedOutputSizes | None = None
                if not size_trust.validation_failed and limit_channel is None:
                    final_size_check = self._check_sizes((stdout, stderr), latch, size_trust)
                    if isinstance(final_size_check, _ValidatedOutputSizes):
                        final_sizes = final_size_check
                    elif isinstance(final_size_check, _OutputLimitDetected):
                        limit_channel = final_size_check.channel
                        outcome = "limit"
                diagnostics_read_allowed = latch.kind is not _TerminalKind.OUTPUT_LIMIT
                if _output_read_permitted(
                    final_sizes,
                    process_tree_terminal_confirmed=tree_terminal,
                    size_validation_failed=size_trust.validation_failed,
                    output_limit_exceeded=limit_channel is not None,
                    diagnostics_read_allowed=diagnostics_read_allowed,
                ):
                    stdout_result_data = self._read_output(stdout, final_sizes.stdout, size_trust)
                    if isinstance(stdout_result_data, ProbeError):
                        latch.fail(_TerminalKind.READER_IO, stdout_result_data)
                    else:
                        stdout_data = stdout_result_data
                    if not size_trust.validation_failed:
                        stderr_result_data = self._read_output(
                            stderr, final_sizes.stderr, size_trust
                        )
                        if isinstance(stderr_result_data, ProbeError):
                            latch.fail(_TerminalKind.READER_IO, stderr_result_data)
                        else:
                            stderr_data = stderr_result_data
                    if size_trust.validation_failed:
                        stdout_data = b""
                        stderr_data = b""
            if cancellation.is_cancelled and latch.error() is None:
                latch.fail(
                    _TerminalKind.CANCELLED,
                    probe_error(
                        ProbeErrorCode.CANCELLED,
                        ErrorCategory.CANCELLED,
                        "process_finalize",
                        "process cancelled before final success",
                    ),
                )
            primary = latch.error()
            if primary is not None:
                kind = latch.kind
                outcome = (
                    "cancelled"
                    if kind is _TerminalKind.CANCELLED
                    else "timeout"
                    if kind is _TerminalKind.TIMEOUT
                    else "limit"
                    if kind is _TerminalKind.OUTPUT_LIMIT
                    else "failed"
                )
        except _StartAbort as abort:
            primary = abort.error
            outcome = "start_failed"
        finally:
            if process is not None and assigned_to_job and not tree_terminal and job is not None:
                if latch.error() is None:
                    primary = probe_error(
                        ProbeErrorCode.PROCESS_FAILED,
                        ErrorCategory.IO,
                        "process_unexpected_unwind",
                        "process control unwound before terminal confirmation",
                    )
                    latch.fail(_TerminalKind.PROCESS_CONTROL, primary)
                tree_terminal = self._terminate_tree(job, latch)
                primary = latch.error() or primary
            all_handles = (
                stdin_read,
                stdin_write,
                stdout.child if stdout is not None else None,
                stderr.child if stderr is not None else None,
                stdout.parent if stdout is not None else None,
                stderr.parent if stderr is not None else None,
                thread,
                process,
                job,
            )
            for handle in all_handles:
                if handle is not None and not handle.closed:
                    close_error = _close_safely(handle)
                    if close_error is not None:
                        mandatory_cleanup_errors.append(close_error)
            if attribute_initialized and attribute_list is not None:
                try:
                    self._kernel32.DeleteProcThreadAttributeList(
                        ctypes.cast(attribute_list, wintypes.LPVOID)
                    )
                except BaseException as exc:
                    mandatory_cleanup_errors.append(_cleanup_exception("attribute_list", exc))
            workdir_error = _remove_workdir_bounded(workdir)
            if workdir_error is not None:
                cleanup_errors.append(workdir_error)
        cleanup_errors = [*mandatory_cleanup_errors, *cleanup_errors]
        if outcome == "ok" and mandatory_cleanup_errors:
            primary = mandatory_cleanup_errors[0]
            outcome = "failed"
        if primary is not None and cleanup_errors:
            primary = ProbeError(
                primary.code,
                primary.category,
                primary.phase,
                primary.message,
                primary.win32_code,
                primary.cause,
                primary.retryable,
                (*primary.secondary, *cleanup_errors)[:8],
            )
        diagnostics = ProcessDiagnostics(stdout_data, stderr_data, tuple(cleanup_errors[:8]))
        if outcome == "ok":
            if cancellation.begin_irreversible_commit() is None:
                error = probe_error(
                    ProbeErrorCode.CANCELLED,
                    ErrorCategory.CANCELLED,
                    "process_finalize",
                    "process cancelled before final success",
                )
                return ProbeCancelled(error, diagnostics)
            return ProbeProcessOk(diagnostics)
        assert primary is not None
        if outcome == "cancelled":
            return ProbeCancelled(primary, diagnostics)
        if outcome == "timeout":
            return ProbeTimeout(primary, diagnostics)
        if outcome == "limit":
            assert limit_channel is not None
            return ProbeOutputLimitExceeded(primary, diagnostics, limit_channel)
        if outcome == "start_failed":
            return ProbeStartFailed(primary, diagnostics)
        return ProbeProcessFailed(primary, diagnostics, exit_code)
