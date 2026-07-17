from __future__ import annotations

import gc
import hashlib
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from uuid import UUID

import pytest

import matrix_auto_cutter.phase2.source_confirmation.capability as capability_module
from matrix_auto_cutter.models import SourceBinding
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateClosed,
    NativeCloseGateWin32Port,
    run_close_gate,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    NativeProcessPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationPorts,
    SourceConfirmationRequest,
    SourceConfirmed,
    confirm_source,
)
from matrix_auto_cutter.phase2.source_confirmation.path_revalidation import (
    PathRevalidated,
    revalidate_lease_path,
)
from matrix_auto_cutter.phase2.win32_port import OwnedHandle, Win32Ok
from matrix_auto_cutter.phase2.workspace import (
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


class AuditedNativePort(NativeCloseGateWin32Port):
    def __init__(self, local_root: Path) -> None:
        self._local_root = str(local_root)
        self.opened_handles: list[int] = []
        self.closed_handles: list[int] = []
        self.query_handles: list[int] = []
        self.read_handles: list[int] = []
        self.position_handles: list[int] = []
        super().__init__()

    def local_app_data(self):
        return Win32Ok(self._local_root)

    def open_file(self, long_path, desired_access, share_mode, creation_disposition, flags):
        result = super().open_file(
            long_path, desired_access, share_mode, creation_disposition, flags
        )
        if isinstance(result, Win32Ok):
            self.opened_handles.append(result.value.value)
        return result

    def _close(self, value: int):
        self.closed_handles.append(value)
        return super()._close(value)

    def query_file_info(self, handle: OwnedHandle):
        self.query_handles.append(handle.value)
        return super().query_file_info(handle)

    def read_file(self, handle: OwnedHandle, maximum_bytes: int):
        self.read_handles.append(handle.value)
        return super().read_file(handle, maximum_bytes)

    def set_file_offset(self, handle: OwnedHandle, offset: int):
        self.position_handles.append(handle.value)
        return super().set_file_offset(handle, offset)


class LeaseObservingProcessPort:
    def __init__(self, lease) -> None:
        self._lease = lease
        self._native = NativeProcessPort()
        self.calls = 0

    def run(self, spec, cancellation):
        assert not self._lease.closed
        self.calls += 1
        result = self._native.run(spec, cancellation)
        assert not self._lease.closed
        return result


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


def test_real_full_confirmation_uses_one_live_lease_and_leaves_no_resources(
    tmp_path: Path,
) -> None:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if ffprobe is None:
        pytest.skip("the locally installed product ffprobe binary is unavailable")
    if ffmpeg is None:
        pytest.skip("optional local ffmpeg fixture generation is unavailable")

    local = tmp_path / "local"
    workspace_path = tmp_path / "workspace"
    local.mkdir()
    generated = tmp_path / "generated.mp4"
    _make_media(ffmpeg, generated)
    long_directory = tmp_path / (("lange-unicode-ä-Ω-") * 7)
    long_directory.mkdir()
    source = long_directory / "aufnahme-ß-漢字-60fps.mp4"
    generated.replace(source)
    alias = tmp_path / "hardlink-alias.mp4"
    os.link(source, alias)
    source_bytes = source.read_bytes()
    source_stat = source.stat()

    port = AuditedNativePort(local)
    workspace = ensure_workspace(port, str(workspace_path))
    assert isinstance(workspace, WorkspaceReady)
    project = create_project(
        port,
        workspace,
        CancellationToken(),
        uuid_factory=lambda: UUID(PROJECT_ID),
    )
    assert isinstance(project, ProjectCreated)
    source_path = validate_path(port, str(source), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    alias_path = validate_path(port, str(alias), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(source_path, PathValidated) and isinstance(alias_path, PathValidated)
    gated = run_close_gate(
        port,
        PROJECT_ID,
        source_path.path,
        CancellationToken(),
    )
    assert isinstance(gated, CloseGateClosed)
    lease = gated.lease
    alias_snapshot = snapshot_file(port, alias_path.path)
    assert isinstance(alias_snapshot, SnapshotOk)
    assert alias_snapshot.snapshot.volume_id.value == lease.volume_id
    assert alias_snapshot.snapshot.file_id == lease.s0.file_id
    assert isinstance(revalidate_lease_path(port, lease, "before_probe"), PathRevalidated)

    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"replacement")
    with pytest.raises(OSError):
        os.replace(replacement, source)
    assert source.read_bytes() == source_bytes

    native_process = NativeProcessPort()
    validated_binary = validate_ffprobe_binary(
        FfprobeCandidate(str(Path(ffprobe).resolve())),
        port,
        NativeBinaryTrustPort(port),
        native_process,
    )
    assert isinstance(validated_binary, BinaryValidated), validated_binary
    observer = LeaseObservingProcessPort(lease)
    reads_before = len(port.read_handles)
    positions_before = len(port.position_handles)
    registry_before = capability_module._CONFIRMED_AUTHORITY.count()
    request = SourceConfirmationRequest(
        project.project,
        IDENTITY_RUN_ID,
        PROBE_ID,
        PROBE_RUN_ID,
        HASH_RUN_ID,
        lease,
        validated_binary.binary,
        SourceBinding.DIRECT_MP4,
    )
    result = confirm_source(
        ConfirmationPorts(port, NativeBinaryTrustPort(port), observer),
        request,
        CancellationToken(),
    )
    assert isinstance(result, SourceConfirmed), result
    assert observer.calls == 1
    assert result.confirmed_source.authorized
    assert result.confirmed_source.require_authorized() == result.source_identity
    assert result.source_identity.sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert result.source_identity.size_bytes == len(source_bytes)
    assert (result.source_identity.fps_num, result.source_identity.fps_den) == (60, 1)
    assert result.evidence.media_probe.artifact_type == "media_probe"
    assert Path(result.evidence.media_probe.canonical_path).is_file()
    assert Path(result.evidence.hash_receipt.canonical_path).is_file()
    assert Path(result.evidence.source_identity_evidence_path).is_file()
    assert result.confirmed_source.evidence.source_path == source_path.path.canonical_dos_path
    assert result.confirmed_source.evidence.source_path != alias_path.path.canonical_dos_path
    assert (
        result.confirmed_source.evidence.pre_probe_path_revalidation.snapshot.file_id
        == lease.file_id
    )
    assert (
        result.confirmed_source.evidence.pre_commit_path_revalidation.snapshot.file_id
        == lease.file_id
    )

    hash_positions = port.position_handles[positions_before:]
    assert len(set(hash_positions)) == 1
    lease_handle = hash_positions[0]
    assert lease_handle in port.read_handles[reads_before:]
    assert port.query_handles.count(lease_handle) >= 6
    assert not lease.closed
    assert not list(workspace_path.rglob(".~matrix-2a-*.tmp"))
    assert (source.stat().st_size, source.stat().st_mtime_ns) == (
        source_stat.st_size,
        source_stat.st_mtime_ns,
    )

    lease.close()
    assert not result.confirmed_source.authorized
    with pytest.raises(RuntimeError):
        result.confirmed_source.require_authorized()
    del result
    gc.collect()
    assert capability_module._CONFIRMED_AUTHORITY.count() == registry_before
    assert Counter(port.opened_handles) == Counter(port.closed_handles)
    assert not list(workspace_path.rglob(".~matrix-2a-*.tmp"))
