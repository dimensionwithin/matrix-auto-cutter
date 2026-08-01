from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import PureWindowsPath
from uuid import UUID

import pytest
from tests.phase2.close_gate.conftest import FakeWaitClock, make_source
from tests.phase2.probe.conftest import FakeProcessPort, golden_json, golden_stream
from tests.phase2.source_hash.conftest import HashingFakePort

from matrix_auto_cutter.models import SourceBinding
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.close_gate import CloseGateClosed, run_close_gate
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    ProbeProcessOk,
    ProcessDiagnostics,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationPorts,
    SourceConfirmationRequest,
)
from matrix_auto_cutter.phase2.workspace import (
    ProjectCapability,
    ProjectCreated,
    WorkspaceReady,
    create_project,
    ensure_workspace,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
IDENTITY_RUN_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"
PROBE_ID = "2e157a84-2e31-49d9-b64e-494c24f8f612"
PROBE_RUN_ID = "11111111-1111-4111-8111-111111111111"
HASH_RUN_ID = "22222222-2222-4222-8222-222222222222"
LEASE_ID = UUID("33333333-3333-4333-8333-333333333333")


def unique_streams() -> list[dict[str, object]]:
    return [
        golden_stream(
            0,
            "video",
            default=1,
            r_frame_rate="60/1",
            avg_frame_rate="60/1",
            nb_frames="60",
            start_time="0.000000000",
        ),
        golden_stream(1, "audio", default=1, start_time="0.000000000"),
    ]


def ambiguous_streams() -> list[dict[str, object]]:
    return [
        golden_stream(
            0,
            "video",
            r_frame_rate="60/1",
            avg_frame_rate="60/1",
            nb_frames="60",
            start_time="0.000000000",
        ),
        golden_stream(
            1,
            "video",
            r_frame_rate="60/1",
            avg_frame_rate="60/1",
            nb_frames="60",
            start_time="0.000000000",
        ),
        golden_stream(2, "audio", default=1, start_time="0.000000000"),
    ]


def probe_json(
    source_path: str,
    data: bytes,
    streams: list[dict[str, object]],
    format_duration: str = "1.000000000",
) -> bytes:
    return golden_json(
        streams,
        format={
            "filename": source_path,
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "format_long_name": "QuickTime / MOV",
            "start_time": "0.000000000",
            "duration": format_duration,
            "size": str(len(data)),
            "bit_rate": str(len(data) * 8),
            "tags": {"title": "bounded"},
        },
    )


class ConfirmationFakePort(HashingFakePort):
    pass


@dataclass
class ConfirmationCase:
    port: ConfirmationFakePort
    project: ProjectCapability
    lease: object
    binary: object
    process: FakeProcessPort
    request: SourceConfirmationRequest
    ports: ConfirmationPorts
    source_data: bytes

    def close(self) -> None:
        if not self.request.lease.closed:
            self.request.lease.close()

    def renewed_request(
        self,
        *,
        lease_id: UUID,
        probe_id: str,
        probe_run_id: str,
        hash_run_id: str,
        assignment=None,
    ) -> SourceConfirmationRequest:
        source = self.request.lease.source_path
        self.request.lease.close()
        gated = run_close_gate(
            self.port,
            PROJECT_ID,
            source,
            CancellationToken(),
            wait_clock=FakeWaitClock(),
            lease_id_factory=lambda: lease_id,
        )
        assert isinstance(gated, CloseGateClosed)
        return replace(
            self.request,
            lease=gated.lease,
            probe_id=probe_id,
            probe_run_id=probe_run_id,
            hash_run_id=hash_run_id,
            assignment=assignment,
        )


def make_case(
    *,
    streams: list[dict[str, object]] | None = None,
    source_path: str = r"C:\Sources\source.mp4",
    source_data: bytes = b"controlled-source-media" * 5,
    format_duration: str = "1.000000000",
) -> ConfirmationCase:
    port = ConfirmationFakePort()
    workspace = ensure_workspace(port, r"C:\Workspace")
    assert isinstance(workspace, WorkspaceReady)
    created = create_project(
        port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(created, ProjectCreated)
    source = make_source(port, source_path, data=source_data)
    gated = run_close_gate(
        port,
        PROJECT_ID,
        source,
        CancellationToken(),
        wait_clock=FakeWaitClock(),
        lease_id_factory=lambda: LEASE_ID,
    )
    assert isinstance(gated, CloseGateClosed)

    port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    binary_result = validate_ffprobe_binary(
        FfprobeCandidate(r"C:\Tools\ffprobe.exe"),
        port,
        NativeBinaryTrustPort(port),
        FakeProcessPort(),
    )
    assert isinstance(binary_result, BinaryValidated)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                probe_json(
                    source.canonical_dos_path,
                    source_data,
                    streams or unique_streams(),
                    format_duration,
                ),
                b"",
            )
        )
    )
    request = SourceConfirmationRequest(
        created.project,
        IDENTITY_RUN_ID,
        PROBE_ID,
        PROBE_RUN_ID,
        HASH_RUN_ID,
        gated.lease,
        binary_result.binary,
        SourceBinding.DIRECT_MP4,
    )
    ports = ConfirmationPorts(port, NativeBinaryTrustPort(port), process)
    assert PureWindowsPath(source.canonical_dos_path).name == "source.mp4"
    return ConfirmationCase(
        port,
        created.project,
        gated.lease,
        binary_result.binary,
        process,
        request,
        ports,
        source_data,
    )


@pytest.fixture
def confirmation_case():
    case = make_case()
    yield case
    case.close()
