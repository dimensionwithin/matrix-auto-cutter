from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from tests.phase2.conftest import FakePort

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, ValidatedPath, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    ProbeProcessOk,
    ProcessDiagnostics,
    ProcessSpec,
    ValidatedFfprobeBinary,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspection,
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
)
from matrix_auto_cutter.phase2.probe.process_native import NativeProcessPort
from matrix_auto_cutter.phase2.probe.process_port import ProbeProcessResult
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port
from matrix_auto_cutter.phase2.win32_port import Win32Err, Win32Failure

VERSION_TEXT = """ffprobe version 8.1.1-test-build Copyright test
built with gcc 15.2.0 test
configuration: --enable-test --disable-network
libavutil      60. 26.101 / 60. 26.101
libavcodec     62. 28.101 / 62. 28.101
"""


@dataclass
class F09EvidenceState:
    active: bool = False
    discovery_source: str = "not_run"
    candidate_path: str = "not_available"
    canonical_path: str = "not_available"
    size_bytes: int | None = None
    sha256: str = "not_available"
    semantic_version: str = "not_available"
    version_line: str = "not_available"
    policy_revision: str = "not_available"
    policy_type: str = "not_available"
    policy_digest: str = "not_available"
    volume_id: str = "not_available"
    file_id: str = "not_available"
    support_decision: str = "not_run"
    executed: set[str] = field(default_factory=set)
    race_modes: set[str] = field(default_factory=set)
    junction_modes: set[str] = field(default_factory=set)
    skips: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class F09LocalFfprobe:
    discovery_source: str
    candidate_path: Path
    port: NativeWin32Port
    binary: ValidatedFfprobeBinary
    original_sha256: str
    original_size: int


@dataclass(frozen=True)
class F09DiscoveredFfprobe:
    discovery_source: str
    caller_path: str
    binary: ValidatedFfprobeBinary


_F09_EVIDENCE = F09EvidenceState()


def _identity_text(value: object) -> str:
    availability = getattr(value, "availability", "not_available")
    if availability != "available":
        return str(availability)
    identity = value
    return f"{identity.scheme}:{identity.value}"  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def f09_evidence() -> F09EvidenceState:
    return _F09_EVIDENCE


@pytest.fixture(scope="session")
def f09_windows() -> None:
    if os.name != "nt":
        pytest.skip("F09_WINDOWS_ONLY")


def _validate_f09_discovery(
    evidence: F09EvidenceState,
    *,
    environ: Mapping[str, str],
    find_executable: Callable[[str], str | None],
    port: Any,
    trust_port: Any,
    process_port: Any,
) -> F09DiscoveredFfprobe:
    environment_set = "FFPROBE_AUDIT_PATH" in environ
    if environment_set:
        source = "environment"
        caller_path = environ["FFPROBE_AUDIT_PATH"]
        if not caller_path:
            evidence.discovery_source = source
            evidence.candidate_path = caller_path
            evidence.support_decision = "invalid"
            pytest.fail("FFPROBE_AUDIT_PATH is set but empty")
    else:
        source = "path"
        caller_path = find_executable("ffprobe.exe") or find_executable("ffprobe")
        if caller_path is None:
            evidence.discovery_source = "none"
            evidence.support_decision = "not_available"
            pytest.skip("F09_NO_LOCAL_FFPROBE")

    evidence.discovery_source = source
    evidence.candidate_path = caller_path
    validated = validate_ffprobe_binary(
        FfprobeCandidate(caller_path),
        port,
        trust_port,
        process_port,
    )
    if not isinstance(validated, BinaryValidated):
        evidence.support_decision = "rejected"
        pytest.fail(
            "local ffprobe validation or support policy rejected the discovered caller path: "
            f"source={source!r}, caller_path={caller_path!r}, error={validated.error!r}"
        )
    return F09DiscoveredFfprobe(source, caller_path, validated.binary)


@pytest.fixture(scope="session")
def f09_local_ffprobe(f09_windows: None, f09_evidence: F09EvidenceState) -> F09LocalFfprobe:
    del f09_windows
    port = NativeWin32Port()
    discovered = _validate_f09_discovery(
        f09_evidence,
        environ=os.environ,
        find_executable=shutil.which,
        port=port,
        trust_port=NativeBinaryTrustPort(port),
        process_port=NativeProcessPort(),
    )
    binary = discovered.binary
    candidate_path = Path(binary.canonical_dos_path)
    try:
        validated_bytes = candidate_path.read_bytes()
    except OSError as exc:
        f09_evidence.support_decision = "invalid_after_validation"
        pytest.fail(
            "validated local ffprobe cannot be read for post-validation evidence: "
            f"canonical_path={binary.canonical_dos_path!r}, error={exc!r}"
        )
    if (
        len(validated_bytes) != binary.size_bytes
        or hashlib.sha256(validated_bytes).hexdigest() != binary.sha256
    ):
        f09_evidence.support_decision = "changed_after_validation"
        pytest.fail(
            "local ffprobe changed after successful product validation: "
            f"canonical_path={binary.canonical_dos_path!r}"
        )

    f09_evidence.canonical_path = binary.canonical_dos_path
    f09_evidence.size_bytes = binary.size_bytes
    f09_evidence.sha256 = binary.sha256
    f09_evidence.semantic_version = str(binary.version.semantic_version)
    f09_evidence.version_line = binary.version.first_line
    f09_evidence.policy_revision = binary.support_policy_revision
    f09_evidence.policy_type = binary.support_policy_type
    f09_evidence.policy_digest = binary.support_policy_digest
    f09_evidence.volume_id = _identity_text(binary.volume_id)
    f09_evidence.file_id = _identity_text(binary.file_id)
    f09_evidence.support_decision = "supported"
    return F09LocalFfprobe(
        discovered.discovery_source,
        candidate_path,
        port,
        binary,
        binary.sha256,
        binary.size_bytes,
    )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if "test_f09_windows_integration.py" not in report.nodeid or not report.skipped:
        return
    reason = str(report.longrepr)
    for allowed in (
        "F09_WINDOWS_ONLY",
        "F09_NO_LOCAL_FFPROBE",
        "F09_REAL_JUNCTION_UNAVAILABLE",
    ):
        if allowed in reason:
            _F09_EVIDENCE.skips.append(allowed)
            return
    _F09_EVIDENCE.skips.append(f"UNEXPECTED:{reason}")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    _F09_EVIDENCE.active = any("test_f09_windows_integration.py" in item.nodeid for item in items)


def pytest_terminal_summary(terminalreporter: Any) -> None:
    evidence = _F09_EVIDENCE
    if not evidence.active:
        return
    fields = {
        "discovery_source": evidence.discovery_source,
        "candidate_path": evidence.candidate_path,
        "canonical_path": evidence.canonical_path,
        "size_bytes": evidence.size_bytes,
        "sha256": evidence.sha256,
        "semantic_version": evidence.semantic_version,
        "version_line": evidence.version_line,
        "policy_revision": evidence.policy_revision,
        "policy_type": evidence.policy_type,
        "policy_digest": evidence.policy_digest,
        "volume_id": evidence.volume_id,
        "file_id": evidence.file_id,
        "support_decision": evidence.support_decision,
        "executed": sorted(evidence.executed),
        "binary_race_modes": sorted(evidence.race_modes),
        "junction_swap_modes": sorted(evidence.junction_modes),
        "skipped_real_parts": len(evidence.skips),
        "skip_reasons": evidence.skips,
    }
    terminalreporter.write_line(
        "F09_EVIDENCE " + json.dumps(fields, ensure_ascii=False, sort_keys=True)
    )


@dataclass
class FakeProcessPort:
    result: ProbeProcessResult = field(
        default_factory=lambda: ProbeProcessOk(ProcessDiagnostics(VERSION_TEXT.encode(), b""))
    )
    callback: Callable[[ProcessSpec, CancellationToken], None] | None = None
    calls: list[ProcessSpec] | None = None

    def run(self, spec: ProcessSpec, token: CancellationToken) -> ProbeProcessResult:
        if self.calls is None:
            self.calls = []
        self.calls.append(spec)
        if self.callback is not None:
            self.callback(spec, token)
        return self.result


def issued_inspection_for(
    binary: ValidatedFfprobeBinary,
    calls: list[int],
    *,
    value: int,
    close_failure: int | BaseException | None = None,
    on_close: Callable[[], None] | None = None,
) -> BinaryInspection:
    """Issue a real inspection while retaining deterministic close instrumentation."""
    port = FakePort()
    port.add_file(binary.canonical_dos_path, b"trusted-binary")
    inspection = NativeBinaryTrustPort(port).inspect(binary.path)
    assert not isinstance(inspection, BinaryInspectionFailed)
    original_error = port._error

    def instrumented_error(operation: str, default: int | None = None):
        if operation == "CloseHandle":
            calls.append(value)
            if on_close is not None:
                on_close()
            if isinstance(close_failure, BaseException):
                raise close_failure
            if close_failure is not None:
                return Win32Err(
                    Win32Failure(close_failure, "CloseHandle", f"error {close_failure}")
                )
        return original_error(operation, default)

    port._error = instrumented_error
    return inspection


@pytest.fixture
def binary_path(fake_port: FakePort) -> ValidatedPath:
    fake_port.add_file(r"C:\Tools With Space\ffprobe.exe", b"trusted-binary")
    result = validate_path(
        fake_port,
        r"C:\Tools With Space\ffprobe.exe",
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
    )
    assert isinstance(result, PathValidated)
    return result.path


@pytest.fixture
def validated_binary(fake_port: FakePort) -> ValidatedFfprobeBinary:
    fake_port.add_file(r"C:\Tools With Space\ffprobe.exe", b"trusted-binary")
    result = validate_ffprobe_binary(
        FfprobeCandidate(r"C:\Tools With Space\ffprobe.exe"),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert isinstance(result, BinaryValidated)
    return result.binary


def golden_stream(
    index: int,
    kind: str,
    *,
    default: int = 0,
    attached: int = 0,
    **updates: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "index": index,
        "codec_name": "h264" if kind == "video" else "pcm_s16le",
        "codec_type": kind,
        "time_base": "1/30" if kind == "video" else "1/48000",
        "r_frame_rate": "30/1" if kind == "video" else "0/0",
        "avg_frame_rate": "30/1" if kind == "video" else "0/0",
        "duration": "1.000000",
        "disposition": {"default": default, "attached_pic": attached},
    }
    if kind == "video":
        base.update(
            {
                "profile": "High",
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
                "nb_frames": "30",
            }
        )
    elif kind == "audio":
        base.update(
            {
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "tags": {"language": "und", "title": "Program Audio"},
            }
        )
    base.update(updates)
    return base


def golden_json(streams: list[dict[str, Any]], **updates: Any) -> bytes:
    root: dict[str, Any] = {
        "programs": [],
        "streams": streams,
        "format": {
            "filename": r"C:\Media\source.wav",
            "format_name": "test",
            "format_long_name": "Test",
            "duration": "1.000000",
            "size": "100",
            "bit_rate": "800",
            "tags": {"title": "untrusted"},
        },
    }
    root.update(updates)
    return json.dumps(root, ensure_ascii=False, separators=(",", ":")).encode()
