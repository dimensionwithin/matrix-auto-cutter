from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import uuid4

from tests.phase2.close_gate.conftest import FakeWaitClock
from tests.phase2.finalizer.conftest import journal_bytes, journal_records
from tests.phase2.probe.conftest import VERSION_TEXT
from tests.phase2.source_confirmation.conftest import (
    ConfirmationFakePort,
    probe_json,
    unique_streams,
)

import matrix_auto_cutter.manual_finalizer as manual_finalizer_module
from matrix_auto_cutter.manual_finalizer import (
    CloseGateRetryPolicy,
    ManualFinalizationFailed,
    ManualFinalizationSucceeded,
    ManualFinalizerPorts,
    ManualFinalizerRequest,
    run_manual_finalizer,
)
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.probe import (
    ProbeProcessOk,
    ProcessDiagnostics,
    ProcessSpec,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.probe.process_port import ProbeProcessResult
from matrix_auto_cutter.phase2.win32_port import ERROR_SHARING_VIOLATION, Win32Failure

SOURCE_PATH = r"C:\Sources\source.mp4"
JOURNAL_PATH = r"C:\Input\recording.ndjson"
FFPROBE_PATH = r"C:\Tools\ffprobe.exe"
WORKSPACE_PATH = r"C:\Workspace"
SIDECAR_PATH = r"C:\Sources\source.obs-events.json"


@dataclass
class RoutingProcessPort:
    probe_stdout: bytes
    calls: list[ProcessSpec] = field(default_factory=list)

    def run(self, spec: ProcessSpec, token: CancellationToken) -> ProbeProcessResult:
        del token
        self.calls.append(spec)
        stdout = VERSION_TEXT.encode() if "-version" in spec.arguments else self.probe_stdout
        return ProbeProcessOk(ProcessDiagnostics(stdout, b""))


@dataclass
class RunnerCase:
    port: ConfirmationFakePort
    process: RoutingProcessPort
    clock: FakeWaitClock
    request: ManualFinalizerRequest
    ports: ManualFinalizerPorts
    source_data: bytes


def _case(*, streams: list[dict[str, object]] | None = None) -> RunnerCase:
    port = ConfirmationFakePort()
    source_data = b"controlled-direct-mp4-media" * 5
    port.add_file(SOURCE_PATH, source_data)
    port.add_file(FFPROBE_PATH, b"trusted-ffprobe-binary")
    port.add_file(JOURNAL_PATH, journal_bytes(journal_records(SOURCE_PATH)))
    process = RoutingProcessPort(probe_json(SOURCE_PATH, source_data, streams or unique_streams()))
    clock = FakeWaitClock()
    ports = ManualFinalizerPorts(
        port,
        NativeBinaryTrustPort(port),
        process,
        clock,
        lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        uuid4,
    )
    request = ManualFinalizerRequest(
        SOURCE_PATH,
        JOURNAL_PATH,
        WORKSPACE_PATH,
        FFPROBE_PATH,
    )
    return RunnerCase(port, process, clock, request, ports, source_data)


def _assert_resources_released(case: RunnerCase) -> None:
    assert case.port.handles == {}
    assert case.port.exclusive == set()


def test_controlled_composition_publishes_and_validates_legacy_sidecar(monkeypatch) -> None:
    case = _case()
    validation_modes: list[str] = []
    real_validator = manual_finalizer_module.validate_sidecar

    def tracked_validator(raw, expected_source):
        validated = real_validator(raw, expected_source)
        validation_modes.append(validated.mode)
        return validated

    monkeypatch.setattr(manual_finalizer_module, "validate_sidecar", tracked_validator)
    result = run_manual_finalizer(case.ports, case.request)

    assert isinstance(result, ManualFinalizationSucceeded), result
    assert result.sidecar_path == SIDECAR_PATH
    assert result.idempotent is False
    assert validation_modes == ["validated_sidecar_1_2"]
    assert case.port._key(SIDECAR_PATH) in case.port.nodes
    assert bytes(case.port.nodes[case.port._key(SOURCE_PATH)].data) == case.source_data
    assert len(case.process.calls) == 2
    _assert_resources_released(case)


def test_invalid_journal_path_is_rejected_before_project_or_gate() -> None:
    case = _case()
    request = replace(case.request, journal_path=r"C:\Input\missing.ndjson")

    result = run_manual_finalizer(case.ports, request)

    assert isinstance(result, ManualFinalizationFailed)
    assert result.stage == "journal_path"
    assert "not found" in result.message.casefold() or "error 2" in result.message.casefold()
    assert case.process.calls == []
    _assert_resources_released(case)


def test_unconfirmable_source_is_rejected_and_lease_is_closed() -> None:
    streams = unique_streams()
    streams[0]["r_frame_rate"] = "30/1"
    streams[0]["avg_frame_rate"] = "30/1"
    streams[0]["nb_frames"] = "30"
    case = _case(streams=streams)

    result = run_manual_finalizer(case.ports, case.request)

    assert isinstance(result, ManualFinalizationFailed)
    assert result.stage == "source_confirmation"
    assert case.port._key(SIDECAR_PATH) not in case.port.nodes
    _assert_resources_released(case)


def test_close_gate_busy_is_retried_only_to_the_finite_limit() -> None:
    case = _case()

    def busy() -> None:
        case.port.source_open_error = Win32Failure(
            ERROR_SHARING_VIOLATION,
            "CreateFileW",
            "recording still open",
        )

    busy()
    case.clock.callbacks.extend([busy, busy])
    request = replace(
        case.request,
        close_gate_retry=CloseGateRetryPolicy(max_attempts=3, delay_seconds=0.25),
    )

    result = run_manual_finalizer(case.ports, request)

    assert isinstance(result, ManualFinalizationFailed)
    assert result.stage == "close_gate"
    assert result.code == "E_CLOSE_GATE_BUSY"
    assert "3 Versuch(en)" in result.message
    assert case.clock.calls == [0.25, 0.25]
    assert case.process.calls == []
    _assert_resources_released(case)


def test_foreign_sidecar_conflict_is_preserved_and_lease_is_closed() -> None:
    case = _case()
    foreign = b"foreign-sidecar-must-remain"
    case.port.add_file(SIDECAR_PATH, foreign)

    result = run_manual_finalizer(case.ports, case.request)

    assert isinstance(result, ManualFinalizationFailed)
    assert result.stage == "finalizer"
    assert result.code == "E_TARGET_ALREADY_EXISTS"
    assert bytes(case.port.nodes[case.port._key(SIDECAR_PATH)].data) == foreign
    assert bytes(case.port.nodes[case.port._key(SOURCE_PATH)].data) == case.source_data
    _assert_resources_released(case)
