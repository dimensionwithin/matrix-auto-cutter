from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from tests.phase2.close_gate.conftest import FakeWaitClock
from tests.phase2.finalizer.conftest import (
    PLUGIN_RUN_ID,
    SESSION_ID,
    journal_bytes,
    journal_records,
)

from matrix_auto_cutter.models import SourceBinding
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateClosed,
    CloseGateLease,
    NativeCloseGateWin32Port,
    run_close_gate,
)
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationConflict,
    FinalizationRejected,
    FinalizationRequest,
    Finalized,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    RecoveryRequest,
    finalize,
    recover,
)
from matrix_auto_cutter.phase2.finalizer.errors import FinalizerErrorCode
from matrix_auto_cutter.phase2.finalizer.models import (
    BundleComponent,
    RecordingJournalBundle,
    RecordingJournalIntegrity,
    RecordingJournalSession,
    bundle_manifest_digest,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    NativeProcessPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationPorts,
    SourceConfirmationRequest,
    SourceConfirmed,
    confirm_source,
)
from matrix_auto_cutter.phase2.win32_port import Win32Ok
from matrix_auto_cutter.phase2.workspace import (
    ProjectCapability,
    ProjectCreated,
    WorkspaceReady,
    create_project,
    ensure_workspace,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Win32 integration")

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
IDENTITY_RUN_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"
PROBE_ID = "2e157a84-2e31-49d9-b64e-494c24f8f612"
PROBE_RUN_ID = "11111111-1111-4111-8111-111111111111"
HASH_RUN_ID = "22222222-2222-4222-8222-222222222222"
LEGACY_FINALIZER_RUN_ID = "33333333-3333-4333-8333-333333333333"
BUNDLE_FINALIZER_RUN_ID = "44444444-4444-4444-8444-444444444444"


class AuditedFinalizerPort(NativeCloseGateWin32Port):
    def __init__(self, local_root: Path) -> None:
        self._local_root = str(local_root)
        self.opened_handles: list[int] = []
        self.closed_handles: list[int] = []
        self.flush_calls = 0
        self.move_calls = 0
        self.sidecar_move_calls = 0
        super().__init__()

    def local_app_data(self):
        return Win32Ok(self._local_root)

    def open_file(self, long_path, desired_access, share_mode, creation_disposition, flags):
        result = super().open_file(
            long_path,
            desired_access,
            share_mode,
            creation_disposition,
            flags,
        )
        if isinstance(result, Win32Ok):
            self.opened_handles.append(result.value.value)
        return result

    def _close(self, value: int):
        self.closed_handles.append(value)
        return super()._close(value)

    def flush_file(self, handle):
        self.flush_calls += 1
        return super().flush_file(handle)

    def move_no_replace(self, source_long_path: str, target_long_path: str):
        self.move_calls += 1
        if target_long_path.casefold().endswith(".obs-events.json"):
            self.sidecar_move_calls += 1
        return super().move_no_replace(source_long_path, target_long_path)


@dataclass
class NativeFinalizerCase:
    port: AuditedFinalizerPort
    project: ProjectCapability
    lease: CloseGateLease
    confirmed: SourceConfirmed
    source: Path
    source_bytes: bytes
    journal_path: Path

    def close(self) -> None:
        self.lease.close()


def _make_media(ffmpeg: str, target: Path) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=60:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(target),
        ],
        check=True,
        timeout=30,
    )


def _validated(port: AuditedFinalizerPort, path: Path):
    result = validate_path(port, str(path), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathValidated), result
    return result.path


def _make_case(tmp_path: Path, *, source_target_alias: bool = False) -> NativeFinalizerCase:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    assert ffprobe is not None, "real package-2F Windows test requires local product ffprobe"
    assert ffmpeg is not None, "real package-2F fixture generation requires local ffmpeg"
    local = tmp_path / "local"
    workspace_path = tmp_path / "workspace"
    journal_directory = tmp_path / "journal"
    local.mkdir()
    journal_directory.mkdir()
    generated = tmp_path / "generated.mp4"
    _make_media(ffmpeg, generated)
    long_directory = tmp_path / (("lange-unicode-ä-Ω-") * 12)
    long_directory.mkdir()
    source = long_directory / "aufnahme-ß-漢字-60fps.mp4"
    generated.replace(source)
    assert len(str(source)) > 260
    source_bytes = source.read_bytes()
    if source_target_alias:
        os.link(source, source.with_suffix(".obs-events.json"))

    port = AuditedFinalizerPort(local)
    workspace = ensure_workspace(port, str(workspace_path))
    assert isinstance(workspace, WorkspaceReady), workspace
    project = create_project(
        port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(project, ProjectCreated), project
    source_path = _validated(port, source)
    gated = run_close_gate(
        port,
        PROJECT_ID,
        source_path,
        CancellationToken(),
        wait_clock=FakeWaitClock(),
    )
    assert isinstance(gated, CloseGateClosed), gated
    process = NativeProcessPort()
    trust = NativeBinaryTrustPort(port)
    binary = validate_ffprobe_binary(
        FfprobeCandidate(str(Path(ffprobe).resolve())),
        port,
        trust,
        process,
    )
    assert isinstance(binary, BinaryValidated), binary
    confirmation = confirm_source(
        ConfirmationPorts(port, trust, process),
        SourceConfirmationRequest(
            project.project,
            IDENTITY_RUN_ID,
            PROBE_ID,
            PROBE_RUN_ID,
            HASH_RUN_ID,
            gated.lease,
            binary.binary,
            SourceBinding.DIRECT_MP4,
        ),
        CancellationToken(),
    )
    assert isinstance(confirmation, SourceConfirmed), confirmation
    journal_path = journal_directory / "recording.ndjson"
    journal_path.write_bytes(journal_bytes(journal_records(str(source))))
    return NativeFinalizerCase(
        port,
        project.project,
        gated.lease,
        confirmation,
        source,
        source_bytes,
        journal_path,
    )


def _legacy_inputs(case: NativeFinalizerCase) -> JournalInputPaths:
    return JournalInputPaths(_validated(case.port, case.journal_path))


def _bundle_inputs(case: NativeFinalizerCase) -> JournalInputPaths:
    journal_data = case.journal_path.read_bytes()
    directory = case.journal_path.parent
    session = RecordingJournalSession(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        producer_name="matrix-auto-cutter-obs-producer",
        producer_version="0.1.0",
        obs_version="32.2.0",
    )
    session_data = canonical_bytes(session)
    session_path = directory / "journal-session.json"
    session_path.write_bytes(session_data)
    integrity = RecordingJournalIntegrity(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        journal_reference=case.journal_path.name,
        journal_size_bytes=len(journal_data),
        journal_sha256=hashlib.sha256(journal_data).hexdigest(),
        session_receipt_digest=hashlib.sha256(session_data).hexdigest(),
    )
    integrity_data = canonical_bytes(integrity)
    integrity_path = directory / "journal-integrity.json"
    integrity_path.write_bytes(integrity_data)
    provisional = RecordingJournalBundle.model_construct(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        producer_version="0.1.0",
        obs_version="32.2.0",
        journal=BundleComponent(
            artifact_type="recording_event_journal",
            schema_version="1.0",
            safe_reference=case.journal_path.name,
            size_bytes=len(journal_data),
            sha256=hashlib.sha256(journal_data).hexdigest(),
        ),
        session_receipt=BundleComponent(
            artifact_type="recording_journal_session",
            schema_version="1.0",
            safe_reference=session_path.name,
            size_bytes=len(session_data),
            sha256=hashlib.sha256(session_data).hexdigest(),
        ),
        integrity_receipt=BundleComponent(
            artifact_type="recording_journal_integrity",
            schema_version="1.0",
            safe_reference=integrity_path.name,
            size_bytes=len(integrity_data),
            sha256=hashlib.sha256(integrity_data).hexdigest(),
        ),
        bundle_manifest_digest="0" * 64,
    )
    values = provisional.model_dump()
    values["bundle_manifest_digest"] = bundle_manifest_digest(provisional)
    manifest_data = canonical_bytes(RecordingJournalBundle.model_validate(values))
    manifest_path = directory / "journal-bundle.json"
    manifest_path.write_bytes(manifest_data)
    return JournalInputPaths(
        _validated(case.port, case.journal_path),
        _validated(case.port, session_path),
        _validated(case.port, integrity_path),
        _validated(case.port, manifest_path),
    )


def _ports(case: NativeFinalizerCase, checkpoint=lambda _name: None) -> FinalizerPorts:
    return FinalizerPorts(
        case.port,
        lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
        uuid4,
        checkpoint,
    )


def test_real_legacy_publish_conflicts_concurrency_unicode_and_cleanup(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    before_stat = case.source.stat()
    inputs = _legacy_inputs(case)
    request = FinalizationRequest(
        case.project,
        LEGACY_FINALIZER_RUN_ID,
        JournalInputProfile.LEGACY,
        inputs,
        case.confirmed.confirmed_source,
        SESSION_ID,
    )
    target = case.source.with_suffix(".obs-events.json")
    try:
        foreign = b"foreign-sidecar-must-remain"
        target.write_bytes(foreign)
        foreign_result = finalize(_ports(case), request, CancellationToken())
        assert isinstance(foreign_result, FinalizationConflict)
        assert foreign_result.error.code is FinalizerErrorCode.TARGET_ALREADY_EXISTS
        assert target.read_bytes() == foreign
        target.unlink()

        entered = Event()
        release = Event()
        outputs: list[object] = []

        def checkpoint(name: str) -> None:
            if name == "before_intent":
                entered.set()
                assert release.wait(10)

        thread = Thread(
            target=lambda: outputs.append(
                finalize(_ports(case, checkpoint), request, CancellationToken())
            )
        )
        thread.start()
        assert entered.wait(10)
        contender = finalize(_ports(case), request, CancellationToken())
        assert isinstance(contender, FinalizationRejected)
        assert contender.error.code is FinalizerErrorCode.FINALIZER_CONCURRENT
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(outputs) == 1 and isinstance(outputs[0], Finalized)
        committed = outputs[0]
        assert isinstance(committed, Finalized)
        assert target.is_file()
        assert case.port.flush_calls >= 1
        assert case.port.sidecar_move_calls == 1
        assert not list(target.parent.glob(".*.obs-events.json.tmp.*"))
        assert case.source.read_bytes() == case.source_bytes
        after_stat = case.source.stat()
        assert (after_stat.st_size, after_stat.st_mtime_ns) == (
            before_stat.st_size,
            before_stat.st_mtime_ns,
        )
    finally:
        case.close()
    assert Counter(case.port.opened_handles) == Counter(case.port.closed_handles)


def test_real_source_hardlink_target_is_rejected_unchanged(tmp_path: Path) -> None:
    case = _make_case(tmp_path, source_target_alias=True)
    inputs = _legacy_inputs(case)
    request = FinalizationRequest(
        case.project,
        LEGACY_FINALIZER_RUN_ID,
        JournalInputProfile.LEGACY,
        inputs,
        case.confirmed.confirmed_source,
        SESSION_ID,
    )
    target = case.source.with_suffix(".obs-events.json")
    before_source = case.source.read_bytes()
    before_target = target.read_bytes()
    try:
        result = finalize(_ports(case), request, CancellationToken())
        assert isinstance(result, FinalizationConflict)
        assert result.error.code is FinalizerErrorCode.TARGET_ALREADY_EXISTS
        assert case.source.read_bytes() == before_source
        assert target.read_bytes() == before_target
    finally:
        case.close()
    assert Counter(case.port.opened_handles) == Counter(case.port.closed_handles)


def test_real_bundle_crash_after_commit_recovers_receipt_and_state(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    inputs = _bundle_inputs(case)
    request = FinalizationRequest(
        case.project,
        BUNDLE_FINALIZER_RUN_ID,
        JournalInputProfile.BUNDLE,
        inputs,
        case.confirmed.confirmed_source,
        SESSION_ID,
    )

    def crash(name: str) -> None:
        if name == "after_commit":
            raise SystemExit("simulated crash immediately after sidecar commit")

    try:
        with pytest.raises(SystemExit, match="immediately after"):
            finalize(_ports(case, crash), request, CancellationToken())
        target = case.source.with_suffix(".obs-events.json")
        assert target.is_file()
        project_root = Path(case.project.project_directory.canonical_dos_path)
        state = project_root / "runs" / BUNDLE_FINALIZER_RUN_ID / "state" / "finalizer-state.json"
        if state.exists():
            state.unlink()
        receipt = project_root / "sidecar" / "receipts" / f"{SESSION_ID}.json"
        assert not receipt.exists()
        recovered = recover(
            _ports(case),
            RecoveryRequest(
                case.project,
                str(target),
                JournalInputProfile.BUNDLE,
                BUNDLE_FINALIZER_RUN_ID,
                SESSION_ID,
                inputs,
                case.confirmed.confirmed_source,
            ),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized), recovered
        assert recovered.receipt is not None
        assert Path(recovered.receipt.canonical_path).is_file()
        assert recovered.state is not None
        assert Path(recovered.state.canonical_path).is_file()
        assert recovered.evidence_status == "complete"
        assert case.source.read_bytes() == case.source_bytes
        assert not list(target.parent.glob(".*.obs-events.json.tmp.*"))
    finally:
        case.close()
    assert Counter(case.port.opened_handles) == Counter(case.port.closed_handles)
