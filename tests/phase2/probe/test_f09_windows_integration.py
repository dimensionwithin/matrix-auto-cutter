from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from ctypes import wintypes
from functools import partial
from pathlib import Path
from threading import Thread
from types import TracebackType
from typing import Any

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import (
    F09EvidenceState,
    F09LocalFfprobe,
    FakeProcessPort,
    _validate_f09_discovery,
)

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    PathValidated,
    validate_path,
)
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    NativeProcessPort,
    ProbeCancelled,
    ProbeFailed,
    ProbeOk,
    ProbeRequest,
    ProbeTimeout,
    ProcessSpec,
    StreamType,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
)
from matrix_auto_cutter.phase2.probe.errors import ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.probe.runner import run_probe
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port
from matrix_auto_cutter.phase2.win32_port import FILE_ATTRIBUTE_REPARSE_POINT

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_OBJECT_0 = 0
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FSCTL_SET_REPARSE_POINT = 0x000900A4
FSCTL_GET_REPARSE_POINT = 0x000900A8
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003
IO_REPARSE_TAG_SYMLINK = 0xA000000C
MAXIMUM_REPARSE_DATA_BUFFER_SIZE = 16 * 1024
F09_REAL_JUNCTION_UNAVAILABLE = "F09_REAL_JUNCTION_UNAVAILABLE"
F09_REAL_JUNCTION_UNAVAILABLE_WINERRORS = frozenset({1, 5, 50, 1314})
_F09_CLEANUP_FAILURE_LIMIT = 8
_F09_CLEANUP_ITEM_LIMIT = 512
_F09_CLEANUP_NOTE_LIMIT = 4096


def _bounded_cleanup_summary(label: str, error: BaseException) -> str:
    try:
        detail = str(error)
    except BaseException:
        detail = "exception detail unavailable"
    summary = f"{label}: {type(error).__name__}: {detail}"
    return summary[:_F09_CLEANUP_ITEM_LIMIT]


class _F09Cleanup:
    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], object]]] = []

    def defer(
        self,
        label: str,
        callback: Callable[..., object],
        /,
        *args: object,
        **kwargs: object,
    ) -> None:
        self._steps.append((label, partial(callback, *args, **kwargs)))

    def __enter__(self) -> _F09Cleanup:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        primary: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exception_type, traceback
        failures: list[tuple[str, BaseException]] = []
        failure_count = 0
        for label, cleanup in reversed(self._steps):
            try:
                cleanup()
            except BaseException as error:
                failure_count += 1
                if len(failures) < _F09_CLEANUP_FAILURE_LIMIT:
                    failures.append((label, error))
        if not failures:
            return False

        summaries = [_bounded_cleanup_summary(label, error) for label, error in failures]
        if failure_count > len(failures):
            summaries.append(f"{failure_count - len(failures)} additional cleanup failures omitted")
        note = ("F09 cleanup failures: " + " | ".join(summaries))[:_F09_CLEANUP_NOTE_LIMIT]
        if primary is not None:
            BaseException.add_note(primary, note)
            return False
        if failure_count == 1:
            error = failures[0][1]
            BaseException.add_note(error, note)
            raise error
        grouped = BaseExceptionGroup(
            f"F09 cleanup failed in {failure_count} steps",
            [error for _, error in failures],
        )
        BaseException.add_note(grouped, note)
        raise grouped


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _product_temp_dirs() -> set[Path]:
    return set(Path(tempfile.gettempdir()).glob("matrix-auto-cutter-probe-*"))


def _unlink_bounded(path: Path, timeout_seconds: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.025)


def _rmtree_if_exists(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _validated_source(port: NativeWin32Port, source: Path):
    validated = validate_path(
        port,
        str(source),
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert isinstance(validated, PathValidated), validated
    return validated.path


def _run_media_probe(local: F09LocalFfprobe, source: Path):
    source_bytes = source.read_bytes()
    source_size = source.stat().st_size
    validated_source = _validated_source(local.port, source)
    before = snapshot_file(local.port, validated_source)
    assert isinstance(before, SnapshotOk)
    product_temps_before = _product_temp_dirs()
    result = run_probe(
        ProbeRequest(local.binary, validated_source, before.snapshot.snapshot_key, 30),
        NativeBinaryTrustPort(local.port),
        NativeProcessPort(),
        lambda path: snapshot_file(local.port, path),
        CancellationToken(),
    )
    after = snapshot_file(local.port, validated_source)
    assert isinstance(after, SnapshotOk)
    assert source.read_bytes() == source_bytes
    assert source.stat().st_size == source_size
    assert after.snapshot.snapshot_key == before.snapshot.snapshot_key
    assert _product_temp_dirs() == product_temps_before
    return result


def _pcm_wave_extensible() -> bytes:
    channels = 2
    sample_rate = 48_000
    bits_per_sample = 16
    block_align = channels * bits_per_sample // 8
    frame_count = 4_800
    samples = bytearray()
    for frame in range(frame_count):
        left = ((frame * 97) % 20_000) - 10_000
        right = ((frame * 193) % 20_000) - 10_000
        samples.extend(struct.pack("<hh", left, right))
    pcm_guid = uuid.UUID("00000001-0000-0010-8000-00aa00389b71").bytes_le
    fmt = struct.pack(
        "<HHIIHHHHI16s",
        0xFFFE,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bits_per_sample,
        22,
        bits_per_sample,
        0x3,
        pcm_guid,
    )
    riff_size = 4 + 8 + len(fmt) + 8 + len(samples)
    return (
        b"RIFF"
        + struct.pack("<I", riff_size)
        + b"WAVEfmt "
        + struct.pack("<I", len(fmt))
        + fmt
        + b"data"
        + struct.pack("<I", len(samples))
        + bytes(samples)
    )


def _y4m() -> bytes:
    width = 32
    height = 24
    body = bytearray(b"YUV4MPEG2 W32 H24 F25:1 Ip A1:1 C420jpeg\n")
    for frame in range(3):
        body.extend(b"FRAME\n")
        body.extend(bytes((pixel + frame * 17) % 256 for pixel in range(width * height)))
        body.extend(bytes([64 + frame]) * (width * height // 4))
        body.extend(bytes([192 - frame]) * (width * height // 4))
    return bytes(body)


def _assert_failed_with_stream(result: object, stream_type: StreamType, phase: str):
    assert isinstance(result, ProbeFailed)
    assert not isinstance(result, ProbeOk)
    assert result.error.code is ProbeErrorCode.UNSUPPORTED_MEDIA
    assert result.error.phase == f"stream_selection.{phase}"
    assert result.profile is not None
    matching = tuple(
        stream for stream in result.profile.streams if stream.stream_type is stream_type
    )
    assert len(matching) == 1
    assert len(result.profile.stream_selection_evidence_digest) == 64
    return matching[0]


def test_f09_local_ffprobe_validation_is_complete_and_handle_free(
    f09_local_ffprobe: F09LocalFfprobe, f09_evidence: F09EvidenceState
) -> None:
    local = f09_local_ffprobe
    binary = local.binary
    assert local.candidate_path.is_file()
    assert local.candidate_path.stat().st_size == local.original_size == binary.size_bytes
    assert _sha256(local.candidate_path) == local.original_sha256 == binary.sha256
    assert binary.raw_version_output == binary.version.raw_output
    assert binary.version.first_line.startswith("ffprobe version ")
    assert binary.support_policy_revision == "1.0"
    assert binary.support_policy_digest
    assert binary.original_snapshot.snapshot_key
    assert binary.path.canonical_dos_path == binary.canonical_dos_path
    assert "handle" not in repr(binary).casefold()
    assert all("handle" not in slot.casefold() for slot in binary.__slots__)
    f09_evidence.executed.add("local_binary_validation")


def test_f09_real_pcm_wave_retains_audio_evidence_without_false_success(
    tmp_path: Path,
    f09_local_ffprobe: F09LocalFfprobe,
    f09_evidence: F09EvidenceState,
) -> None:
    source = tmp_path / "f09-pcm-extensible.wav"
    with _F09Cleanup() as cleanup:
        cleanup.defer("remove PCM source", source.unlink, missing_ok=True)
        source.write_bytes(_pcm_wave_extensible())
        stream = _assert_failed_with_stream(
            _run_media_probe(f09_local_ffprobe, source), StreamType.AUDIO, "video_missing"
        )
        assert stream.sample_rate == 48_000
        assert stream.channels == 2
        assert stream.channel_layout == "stereo"
        assert stream.duration is not None and stream.duration.value > 0
        f09_evidence.executed.add("real_pcm_wave")


def test_f09_real_y4m_retains_video_evidence_without_false_success(
    tmp_path: Path,
    f09_local_ffprobe: F09LocalFfprobe,
    f09_evidence: F09EvidenceState,
) -> None:
    source = tmp_path / "f09-video.y4m"
    with _F09Cleanup() as cleanup:
        cleanup.defer("remove Y4M source", source.unlink, missing_ok=True)
        source.write_bytes(_y4m())
        stream = _assert_failed_with_stream(
            _run_media_probe(f09_local_ffprobe, source), StreamType.VIDEO, "audio_missing"
        )
        assert (stream.width, stream.height) == (32, 24)
        assert stream.r_frame_rate is not None
        assert stream.avg_frame_rate is not None
        assert stream.cfr_status == "not_established"
        assert stream.nb_frames is None or stream.nb_frames == 3
        f09_evidence.executed.add("real_y4m")


def test_f09_real_long_unicode_source_round_trips_losslessly(
    tmp_path: Path,
    f09_local_ffprobe: F09LocalFfprobe,
    f09_evidence: F09EvidenceState,
) -> None:
    root = tmp_path / "f09-long-unicode"
    components = ("日本語", "кириллица", "العربية", "emoji-\U0001f680")
    current = root
    component_index = 0
    with _F09Cleanup() as cleanup:
        cleanup.defer("remove long Unicode source tree", _rmtree_if_exists, root)
        try:
            while len(str(current / "quelle-\U0001f680.wav")) <= 280:
                component = (
                    components[component_index % len(components)] + f"-{component_index:02d}"
                )
                current /= component
                component_index += 1
            current.mkdir(parents=True)
            source = current / "quelle-\U0001f680.wav"
            source.write_bytes(_pcm_wave_extensible())
            assert len(str(source)) > 260
            assert "\U0001f680" in str(source)
            assert os.fsdecode(os.fsencode(str(source))) == str(source)
            validated = _validated_source(f09_local_ffprobe.port, source)
            assert "?" not in validated.canonical_dos_path
            result = _run_media_probe(f09_local_ffprobe, source)
            stream = _assert_failed_with_stream(result, StreamType.AUDIO, "video_missing")
            assert isinstance(result, ProbeFailed)
            assert result.profile is not None
            assert "\U0001f680" in result.profile.format.filename
            assert "?" not in result.profile.format.filename
            assert stream.channels == 2
            f09_evidence.executed.add("real_long_unicode_source")
        except OSError as exc:
            pytest.fail(
                "controlled long Unicode path could not be created or used: "
                f"winerror={getattr(exc, 'winerror', None)!r}, error={exc!r}"
            )


_PROCESS_TREE_HELPER = r"""from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

role = sys.argv[1]
evidence = Path(sys.argv[2])
if role == "parent":
    subprocess.Popen([sys.executable, __file__, "child", str(evidence), str(os.getpid())])
elif role == "child":
    grandchild = subprocess.Popen([sys.executable, __file__, "grandchild", str(evidence)])
    payload = {"parent": int(sys.argv[3]), "child": os.getpid(), "grandchild": grandchild.pid}
    temporary = evidence.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, evidence)
while True:
    time.sleep(0.1)
"""


def _kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    return kernel32


def _wait_for_pid_evidence(path: Path, timeout_seconds: float = 5.0) -> dict[str, int]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.025)
            continue
        if set(payload) == {"parent", "child", "grandchild"}:
            return {name: int(value) for name, value in payload.items()}
    pytest.fail(f"process tree did not publish bounded PID evidence: {path!s}")


def _close_process_handles(kernel32: Any, handles: list[int]) -> None:
    close_failures: list[tuple[int, int]] = []
    for handle in handles:
        if not kernel32.CloseHandle(handle):
            close_failures.append((handle, ctypes.get_last_error()))
    assert not close_failures, f"process handle cleanup failed: {close_failures!r}"


def _assert_thread_stopped(runner: Thread) -> None:
    assert not runner.is_alive(), f"thread remained alive after cleanup: {runner.name}"


@pytest.mark.parametrize("mode", ["timeout", "cancellation"])
def test_f09_native_job_terminates_parent_child_and_grandchild(
    mode: str,
    tmp_path: Path,
    f09_windows: None,
    f09_evidence: F09EvidenceState,
) -> None:
    del f09_windows
    helper = tmp_path / "f09_process_tree.py"
    evidence = tmp_path / "f09_process_tree.json"
    helper.write_text(_PROCESS_TREE_HELPER, encoding="utf-8", newline="\n")
    token = CancellationToken()
    result: list[object] = []
    errors: list[BaseException] = []
    python = str(Path(sys.executable).resolve())
    timeout = 8 if mode == "timeout" else 30
    spec = ProcessSpec(python, (python, str(helper), "parent", str(evidence)), timeout)
    product_temps_before = _product_temp_dirs()

    def invoke() -> None:
        try:
            result.append(NativeProcessPort().run(spec, token))
        except BaseException as exc:
            errors.append(exc)

    runner = Thread(target=invoke, name=f"f09-{mode}-native-process-runner")
    handles: list[int] = []
    kernel32 = _kernel32()
    with _F09Cleanup() as cleanup:
        cleanup.defer("assert native process runner stopped", _assert_thread_stopped, runner)
        cleanup.defer("remove process helper", helper.unlink, missing_ok=True)
        cleanup.defer("remove process evidence", evidence.unlink, missing_ok=True)
        cleanup.defer(
            "remove process evidence temporary",
            evidence.with_suffix(".tmp").unlink,
            missing_ok=True,
        )
        cleanup.defer("close process handles", _close_process_handles, kernel32, handles)
        cleanup.defer("join native process runner", runner.join, 10)
        cleanup.defer("cancel native process runner", token.cancel)
        runner.start()
        pids = _wait_for_pid_evidence(evidence)
        for name, pid in pids.items():
            handle = kernel32.OpenProcess(
                SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            assert handle, (
                f"OpenProcess failed for {name} pid={pid}, win32={ctypes.get_last_error()}"
            )
            handles.append(int(handle))
        if mode == "cancellation":
            assert token.cancel()
        runner.join(10)
        assert not runner.is_alive()
        assert not errors
        assert len(result) == 1
        expected_type = ProbeTimeout if mode == "timeout" else ProbeCancelled
        expected_code = ProbeErrorCode.TIMEOUT if mode == "timeout" else ProbeErrorCode.CANCELLED
        assert isinstance(result[0], expected_type)
        assert result[0].error.code is expected_code
        assert not isinstance(result[0], ProbeOk)
        for handle in handles:
            assert kernel32.WaitForSingleObject(handle, 5_000) == WAIT_OBJECT_0
        assert _product_temp_dirs() == product_temps_before
        f09_evidence.executed.add(f"real_process_tree_{mode}")


class _RealPrelaunchMutationTrust:
    def __init__(self, port: NativeWin32Port, target: Path, replacement: Path) -> None:
        self._native = NativeBinaryTrustPort(port)
        self._target = target
        self._replacement = replacement
        self.attempted = False
        self.prevented = False
        self.replaced = False

    def inspect(self, path: object):
        inspection = self._native.inspect(path)  # type: ignore[arg-type]
        if self.attempted or isinstance(inspection, BinaryInspectionFailed):
            return inspection
        self.attempted = True
        try:
            os.replace(self._replacement, self._target)
        except OSError as exc:
            self.prevented = getattr(exc, "winerror", None) in {5, 32, 33} or isinstance(
                exc, PermissionError
            )
            if not self.prevented:
                raise
            return inspection
        self.replaced = True
        close_error = inspection.close()
        return BinaryInspectionFailed(
            probe_error(
                ProbeErrorCode.BINARY_CHANGED,
                ErrorCategory.INTEGRITY,
                "f09_prelaunch_race",
                "test adapter observed replacement at the retained-handle boundary",
                secondary=() if close_error is None else (close_error,),
            )
        )


def test_f09_real_binary_start_race_never_starts_replaced_bytes(
    tmp_path: Path,
    f09_local_ffprobe: F09LocalFfprobe,
    f09_evidence: F09EvidenceState,
) -> None:
    local = f09_local_ffprobe
    private = tmp_path / "ffprobe-f09-private.exe"
    replacement = tmp_path / "ffprobe-f09-replacement.exe"
    source = tmp_path / "f09-race.wav"
    with _F09Cleanup() as cleanup:
        cleanup.defer("remove private ffprobe", _unlink_bounded, private)
        cleanup.defer("remove replacement ffprobe", _unlink_bounded, replacement)
        cleanup.defer("remove race source", _unlink_bounded, source)
        shutil.copy2(local.candidate_path, private)
        replacement.write_bytes(b"F09_UNVALIDATED_REPLACEMENT_MUST_NOT_START")
        source.write_bytes(_pcm_wave_extensible())
        original_installation_hash = _sha256(local.candidate_path)
        port = NativeWin32Port()
        validated = validate_ffprobe_binary(
            FfprobeCandidate(str(private)),
            port,
            NativeBinaryTrustPort(port),
            NativeProcessPort(),
        )
        assert isinstance(validated, BinaryValidated), validated
        source_path = _validated_source(port, source)
        source_snapshot = snapshot_file(port, source_path)
        assert isinstance(source_snapshot, SnapshotOk)
        racing_trust = _RealPrelaunchMutationTrust(port, private, replacement)
        result = run_probe(
            ProbeRequest(validated.binary, source_path, source_snapshot.snapshot.snapshot_key, 30),
            racing_trust,
            NativeProcessPort(),
            lambda path: snapshot_file(port, path),
            CancellationToken(),
        )
        assert racing_trust.attempted
        if racing_trust.prevented:
            assert isinstance(result, ProbeFailed)
            assert result.error.phase == "stream_selection.video_missing"
        else:
            assert racing_trust.replaced
            assert isinstance(result, ProbeFailed)
            assert result.error.code is ProbeErrorCode.BINARY_CHANGED
            assert result.error.phase == "f09_prelaunch_race"
        assert _sha256(local.candidate_path) == original_installation_hash
        f09_evidence.race_modes.add("real")
        f09_evidence.executed.add("real_binary_start_race")


def test_f09_handle_faithful_prelaunch_revalidation_fails_closed(
    fake_port: FakePort,
    validated_binary: object,
    f09_windows: None,
    f09_evidence: F09EvidenceState,
) -> None:
    del f09_windows
    binary = validated_binary
    source_name = r"C:\Media\f09-source.bin"
    fake_port.add_file(source_name, b"source")
    source = validate_path(
        fake_port,
        source_name,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert isinstance(source, PathValidated)
    before = snapshot_file(fake_port, source.path)
    assert isinstance(before, SnapshotOk)
    fake_port.nodes[fake_port._key(binary.canonical_dos_path)].write_time += 1  # type: ignore[attr-defined]
    process = FakeProcessPort()
    result = run_probe(
        ProbeRequest(binary, source.path, before.snapshot.snapshot_key),  # type: ignore[arg-type]
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
    assert result.error.phase == "binary_prelaunch"
    assert process.calls is None
    f09_evidence.race_modes.add("handle_faithful")
    f09_evidence.executed.add("handle_faithful_binary_start_race")


def test_f09_handle_faithful_ancestor_swap_is_rejected(
    fake_port: FakePort, f09_windows: None, f09_evidence: F09EvidenceState
) -> None:
    del f09_windows
    candidate = r"C:\F09\ancestor\ffprobe.exe"
    fake_port.add_file(candidate, b"same-name-and-content")
    initial = validate_path(
        fake_port,
        candidate,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert isinstance(initial, PathValidated)
    ancestor = fake_port.nodes[fake_port._key(r"C:\F09\ancestor")]
    ancestor.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    revalidated = validate_path(
        fake_port,
        candidate,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert isinstance(revalidated, PathRejected)
    assert "reparse" in revalidated.error.message.casefold()
    f09_evidence.junction_modes.add("handle_faithful")
    f09_evidence.executed.add("handle_faithful_ancestor_swap")


class _F09JunctionWin32Error(RuntimeError):
    def __init__(
        self,
        operation: str,
        win32_code: int,
        path: Path,
        *,
        junction_created: bool,
    ) -> None:
        try:
            detail = ctypes.FormatError(win32_code).strip()
        except (OSError, ValueError):
            detail = "Win32 message unavailable"
        super().__init__(
            f"operation={operation}, win32={win32_code}, detail={detail!r}, "
            f"path={str(path)!r}, junction_created={junction_created}"
        )
        self.operation = operation
        self.win32_code = win32_code
        self.path = path
        self.junction_created = junction_created


def _junction_win32_error(
    operation: str, path: Path, *, junction_created: bool
) -> _F09JunctionWin32Error:
    return _F09JunctionWin32Error(
        operation,
        ctypes.get_last_error(),
        path,
        junction_created=junction_created,
    )


def _remove_directory_junction(path: Path) -> None:
    if os.path.lexists(path):
        path.rmdir()


def _create_directory_junction(junction: Path, target: Path) -> int:
    target_dos_path = os.path.abspath(target)
    substitute_name = ("\\??\\" + target_dos_path).encode("utf-16-le")
    print_name = target_dos_path.encode("utf-16-le")
    path_buffer = substitute_name + b"\0\0" + print_name + b"\0\0"
    reparse_data_length = 8 + len(path_buffer)
    payload = (
        struct.pack(
            "<IHHHHHH",
            IO_REPARSE_TAG_MOUNT_POINT,
            reparse_data_length,
            0,
            0,
            len(substitute_name),
            len(substitute_name) + 2,
            len(print_name),
        )
        + path_buffer
    )
    assert len(payload) <= MAXIMUM_REPARSE_DATA_BUFFER_SIZE

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    kernel32.DeviceIoControl.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    junction.mkdir()
    junction_created = False
    verified = False
    with _F09Cleanup() as cleanup:
        cleanup.defer(
            "remove incomplete junction",
            lambda: None if verified else _remove_directory_junction(junction),
        )
        handle = kernel32.CreateFileW(
            str(junction),
            GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle in {None, INVALID_HANDLE_VALUE}:
            raise _junction_win32_error("CreateFileW(junction)", junction, junction_created=False)

        def close_junction_handle() -> None:
            if not kernel32.CloseHandle(handle):
                raise _junction_win32_error(
                    "CloseHandle(junction)",
                    junction,
                    junction_created=junction_created,
                )

        cleanup.defer("close junction handle", close_junction_handle)
        payload_buffer = ctypes.create_string_buffer(payload)
        bytes_returned = wintypes.DWORD()
        if not kernel32.DeviceIoControl(
            handle,
            FSCTL_SET_REPARSE_POINT,
            ctypes.cast(payload_buffer, wintypes.LPVOID),
            len(payload),
            None,
            0,
            ctypes.byref(bytes_returned),
            None,
        ):
            raise _junction_win32_error(
                "DeviceIoControl(FSCTL_SET_REPARSE_POINT)",
                junction,
                junction_created=False,
            )
        junction_created = True

        output_buffer = ctypes.create_string_buffer(MAXIMUM_REPARSE_DATA_BUFFER_SIZE)
        if not kernel32.DeviceIoControl(
            handle,
            FSCTL_GET_REPARSE_POINT,
            None,
            0,
            ctypes.cast(output_buffer, wintypes.LPVOID),
            len(output_buffer),
            ctypes.byref(bytes_returned),
            None,
        ):
            raise _junction_win32_error(
                "DeviceIoControl(FSCTL_GET_REPARSE_POINT)",
                junction,
                junction_created=True,
            )
        if bytes_returned.value < 8:
            raise AssertionError(
                "junction reparse verification returned a truncated buffer: "
                f"bytes_returned={bytes_returned.value}"
            )
        actual_tag = struct.unpack_from("<I", output_buffer.raw)[0]
        assert actual_tag == IO_REPARSE_TAG_MOUNT_POINT, (
            "junction reparse tag mismatch: "
            f"expected={IO_REPARSE_TAG_MOUNT_POINT:#010x}, actual={actual_tag:#010x}"
        )
        verified = True
    return actual_tag


def _create_verified_junction_or_skip(
    junction: Path,
    target: Path,
    *,
    create_junction: Callable[[Path, Path], int] = _create_directory_junction,
) -> int:
    try:
        actual_tag = create_junction(junction, target)
    except _F09JunctionWin32Error as error:
        if (
            not error.junction_created
            and error.win32_code in F09_REAL_JUNCTION_UNAVAILABLE_WINERRORS
        ):
            pytest.skip(F09_REAL_JUNCTION_UNAVAILABLE)
        pytest.fail(f"F09 real junction creation failed: {error}")
    if actual_tag != IO_REPARSE_TAG_MOUNT_POINT:
        pytest.fail(
            "F09 junction creation returned an unverified reparse tag: "
            f"expected={IO_REPARSE_TAG_MOUNT_POINT:#010x}, actual={actual_tag:#010x}"
        )
    return actual_tag


def test_f09_real_junction_ancestor_swap_is_rejected_without_process_start(
    tmp_path: Path,
    f09_local_ffprobe: F09LocalFfprobe,
    f09_evidence: F09EvidenceState,
) -> None:
    root = tmp_path / "f09-real-junction"
    active = root / "active"
    retained_a = root / "retained-a"
    target_b = root / "target-b"
    binary_name = "ffprobe.exe"
    with _F09Cleanup() as cleanup:
        cleanup.defer("remove real junction test tree", _rmtree_if_exists, root)
        active.mkdir(parents=True)
        target_b.mkdir()
        shutil.copy2(f09_local_ffprobe.candidate_path, active / binary_name)
        shutil.copy2(f09_local_ffprobe.candidate_path, target_b / binary_name)
        assert _sha256(active / binary_name) == _sha256(target_b / binary_name)
        port = NativeWin32Port()
        initial = validate_path(
            port,
            str(active / binary_name),
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
            require_regular_file=True,
        )
        assert isinstance(initial, PathValidated)
        active.rename(retained_a)
        f09_evidence.executed.add("real_junction_creation_attempt")
        actual_tag = _create_verified_junction_or_skip(active, target_b)
        assert actual_tag == IO_REPARSE_TAG_MOUNT_POINT
        cleanup.defer("remove active junction", _remove_directory_junction, active)
        revalidated = validate_path(
            port,
            str(active / binary_name),
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
            require_regular_file=True,
        )
        assert isinstance(revalidated, PathRejected)
        assert _sha256(target_b / binary_name) == f09_local_ffprobe.binary.sha256
        f09_evidence.junction_modes.add("real")
        f09_evidence.executed.add("real_junction_ancestor_swap")


@pytest.mark.parametrize(
    "caller_path",
    [
        r"C:\F09\child\..\ffprobe.exe",
        r"C:\F09\.\ffprobe.exe",
    ],
    ids=["parent-component", "current-component"],
)
def test_f09_reaudit_001_environment_dot_components_fail_without_path_fallback(
    caller_path: str,
) -> None:
    evidence = F09EvidenceState()
    port = FakePort()
    process = FakeProcessPort()
    path_lookups: list[str] = []

    def forbidden_path_lookup(name: str) -> str | None:
        path_lookups.append(name)
        return r"C:\Fallback\ffprobe.exe"

    with pytest.raises(pytest.fail.Exception) as raised:
        _validate_f09_discovery(
            evidence,
            environ={"FFPROBE_AUDIT_PATH": caller_path},
            find_executable=forbidden_path_lookup,
            port=port,
            trust_port=NativeBinaryTrustPort(port),
            process_port=process,
        )
    assert "caller_path=" in str(raised.value)
    assert repr(caller_path) in str(raised.value)
    assert path_lookups == []
    assert process.calls is None
    assert evidence.discovery_source == "environment"
    assert evidence.candidate_path == caller_path
    assert evidence.canonical_path == "not_available"
    assert evidence.support_decision == "rejected"


@pytest.mark.parametrize(
    "caller_path",
    [
        r"C:\F09\ffprobe.exe",
        "C:\\F09 Prüflabor\\日本語\\ffprobe.exe",
    ],
    ids=["absolute", "unicode-and-spaces"],
)
def test_f09_reaudit_001_valid_environment_path_reaches_product_unchanged(
    caller_path: str,
) -> None:
    evidence = F09EvidenceState()
    port = FakePort()
    port.add_file(caller_path, b"trusted-binary")
    process = FakeProcessPort()

    discovered = _validate_f09_discovery(
        evidence,
        environ={"FFPROBE_AUDIT_PATH": caller_path},
        find_executable=lambda name: pytest.fail(f"unexpected PATH lookup: {name}"),
        port=port,
        trust_port=NativeBinaryTrustPort(port),
        process_port=process,
    )

    assert discovered.discovery_source == "environment"
    assert discovered.caller_path == caller_path
    assert discovered.binary.path.original_input == caller_path
    assert discovered.binary.canonical_dos_path == caller_path
    assert process.calls is not None and len(process.calls) == 1
    assert process.calls[0].application_path == caller_path
    assert evidence.candidate_path == caller_path


def test_f09_reaudit_002_junction_requires_verified_mount_point_tag(tmp_path: Path) -> None:
    junction = tmp_path / "junction"
    target = tmp_path / "target"

    assert (
        _create_verified_junction_or_skip(
            junction,
            target,
            create_junction=lambda _junction, _target: IO_REPARSE_TAG_MOUNT_POINT,
        )
        == IO_REPARSE_TAG_MOUNT_POINT
    )
    with pytest.raises(pytest.fail.Exception, match="unverified reparse tag"):
        _create_verified_junction_or_skip(
            junction,
            target,
            create_junction=lambda _junction, _target: IO_REPARSE_TAG_SYMLINK,
        )


def test_f09_reaudit_003_winerror_1314_maps_to_exact_junction_skip(tmp_path: Path) -> None:
    def fail_privilege(_junction: Path, _target: Path) -> int:
        raise _F09JunctionWin32Error(
            "DeviceIoControl(FSCTL_SET_REPARSE_POINT)",
            1314,
            tmp_path / "junction",
            junction_created=False,
        )

    with pytest.raises(pytest.skip.Exception) as raised:
        _create_verified_junction_or_skip(
            tmp_path / "junction",
            tmp_path / "target",
            create_junction=fail_privilege,
        )
    assert str(raised.value) == F09_REAL_JUNCTION_UNAVAILABLE


@pytest.mark.parametrize("win32_code", [87, 424242], ids=["invalid-parameter", "unknown"])
def test_f09_reaudit_003_unapproved_junction_errors_fail_with_full_diagnostics(
    tmp_path: Path, win32_code: int
) -> None:
    def fail_unapproved(_junction: Path, _target: Path) -> int:
        raise _F09JunctionWin32Error(
            "DeviceIoControl(FSCTL_SET_REPARSE_POINT)",
            win32_code,
            tmp_path / "junction",
            junction_created=False,
        )

    with pytest.raises(pytest.fail.Exception) as raised:
        _create_verified_junction_or_skip(
            tmp_path / "junction",
            tmp_path / "target",
            create_junction=fail_unapproved,
        )
    diagnostic = str(raised.value)
    assert "DeviceIoControl(FSCTL_SET_REPARSE_POINT)" in diagnostic
    assert f"win32={win32_code}" in diagnostic
    assert "junction_created=False" in diagnostic


def test_f09_reaudit_003_successful_junction_runs_complete_real_body(tmp_path: Path) -> None:
    execution: list[str] = []

    def create_successfully(_junction: Path, _target: Path) -> int:
        execution.append("junction_created_and_tag_verified")
        return IO_REPARSE_TAG_MOUNT_POINT

    actual_tag = _create_verified_junction_or_skip(
        tmp_path / "junction",
        tmp_path / "target",
        create_junction=create_successfully,
    )
    execution.append("ancestor_swap_revalidated")

    assert actual_tag == IO_REPARSE_TAG_MOUNT_POINT
    assert execution == ["junction_created_and_tag_verified", "ancestor_swap_revalidated"]


def test_f09_reaudit_003_created_junction_can_never_be_skipped_later(tmp_path: Path) -> None:
    def fail_after_creation(_junction: Path, _target: Path) -> int:
        raise _F09JunctionWin32Error(
            "CloseHandle(junction)",
            1314,
            tmp_path / "junction",
            junction_created=True,
        )

    with pytest.raises(pytest.fail.Exception, match="junction_created=True"):
        _create_verified_junction_or_skip(
            tmp_path / "junction",
            tmp_path / "target",
            create_junction=fail_after_creation,
        )


def test_f09_reaudit_004_only_primary_error_remains_top_level() -> None:
    primary = AssertionError("primary assertion")

    with pytest.raises(AssertionError) as raised, _F09Cleanup():
        raise primary

    assert raised.value is primary
    assert getattr(primary, "__notes__", []) == []


def test_f09_reaudit_004_only_cleanup_error_is_raised_normally() -> None:
    cleanup_error = RuntimeError("cleanup only")

    def fail_cleanup() -> None:
        raise cleanup_error

    with pytest.raises(RuntimeError) as raised, _F09Cleanup() as cleanup:
        cleanup.defer("single cleanup", fail_cleanup)

    assert raised.value is cleanup_error
    assert "single cleanup" in cleanup_error.__notes__[0]


def test_f09_reaudit_004_primary_plus_one_cleanup_keeps_primary_top_level() -> None:
    primary = AssertionError("primary assertion")
    cleanup_error = RuntimeError("cleanup secondary")

    def fail_cleanup() -> None:
        raise cleanup_error

    with pytest.raises(AssertionError) as raised, _F09Cleanup() as cleanup:
        cleanup.defer("secondary cleanup", fail_cleanup)
        raise primary

    assert raised.value is primary
    assert "secondary cleanup" in primary.__notes__[0]
    assert "RuntimeError: cleanup secondary" in primary.__notes__[0]


def test_f09_reaudit_004_primary_plus_multiple_cleanups_keeps_all_diagnostics() -> None:
    primary = ValueError("primary runtime failure")
    cleanup_calls: list[str] = []

    def fail_cleanup(name: str) -> None:
        cleanup_calls.append(name)
        raise RuntimeError(f"cleanup {name}")

    with pytest.raises(ValueError) as raised, _F09Cleanup() as cleanup:
        cleanup.defer("first cleanup", fail_cleanup, "first")
        cleanup.defer("second cleanup", fail_cleanup, "second")
        raise primary

    assert raised.value is primary
    assert cleanup_calls == ["second", "first"]
    assert "first cleanup: RuntimeError: cleanup first" in primary.__notes__[0]
    assert "second cleanup: RuntimeError: cleanup second" in primary.__notes__[0]


def test_f09_reaudit_004_successful_test_and_cleanup_remain_successful() -> None:
    cleanup_calls: list[str] = []

    with _F09Cleanup() as cleanup:
        cleanup.defer("successful cleanup", cleanup_calls.append, "cleaned")
        assert cleanup_calls == []

    assert cleanup_calls == ["cleaned"]
