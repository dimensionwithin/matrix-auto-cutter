from __future__ import annotations

import copy
import pickle
from dataclasses import replace

import pytest
from tests.phase2.close_gate.conftest import FakeWaitClock, gate

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    ERROR_DELETE_PENDING,
    ERROR_INVALID_HANDLE,
    STATUS_DELETE_PENDING,
    CloseGateBusy,
    CloseGateClosed,
    CloseGateDeletePending,
    CloseGateDisappeared,
    CloseGateInaccessible,
    CloseGateLease,
    CloseGateUnknownWin32Error,
    CloseGateUnstable,
    CloseGateUnsupported,
    CloseGateWin32Failure,
)
from matrix_auto_cutter.phase2.pathing import PathRole
from matrix_auto_cutter.phase2.win32_port import (
    ERROR_ACCESS_DENIED,
    ERROR_FILE_NOT_FOUND,
    ERROR_LOCK_VIOLATION,
    ERROR_PATH_NOT_FOUND,
    ERROR_SHARING_VIOLATION,
    FILE_ATTRIBUTE_DIRECTORY,
    FILE_ATTRIBUTE_REPARSE_POINT,
    FILE_SHARE_READ,
    GENERIC_READ,
    OPEN_EXISTING,
    Win32Failure,
)


def test_success_uses_exact_open_same_handle_and_exact_s0_s1_s2(close_port, source_path) -> None:
    clock = FakeWaitClock()
    result = gate(close_port, source_path, clock=clock)
    assert isinstance(result, CloseGateClosed)
    lease = result.lease
    assert lease.source_path.canonical_dos_path == source_path.canonical_dos_path
    assert lease.volume_id == "0000000000000001"
    assert len(lease.file_id) == 32 and lease.file_id_scheme == "file_id_128"
    assert lease.lease_id == lease.validation_epoch
    assert lease.s0 == lease.s1 == lease.s2
    assert clock.calls == [1.0, 1.0]
    assert close_port.snapshot_query_count == 3
    assert len(close_port.source_gate_handles) == 1
    gate_open = next(
        item
        for item in close_port.detailed_open_history
        if item[0].casefold().endswith("source.mp4")
        and item[1:4] == (GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING)
    )
    assert gate_open[1:4] == (GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING)
    assert not lease.closed
    assert lease.close() == ()
    assert lease.closed
    assert lease.close() == ()
    assert not close_port.handles


@pytest.mark.parametrize(
    "mutation",
    ["size", "last_write", "change", "attributes", "file_id", "volume"],
)
def test_every_snapshot_change_is_unstable(close_port, source_path, mutation: str) -> None:
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]

    def mutate() -> None:
        if mutation == "size":
            node.data.extend(b"x")
        elif mutation == "last_write":
            node.write_time += 1
        elif mutation == "change":
            node.change_time += 1
        elif mutation == "attributes":
            node.attributes ^= 0x20
        elif mutation == "file_id":
            node.file_id = b"z" * 16
        else:
            node.volume += 1

    result = gate(close_port, source_path, clock=FakeWaitClock([mutate]))
    assert isinstance(result, CloseGateUnstable)
    assert not close_port.handles


def test_snapshot_key_tamper_is_not_accepted(monkeypatch, close_port, source_path) -> None:
    import matrix_auto_cutter.phase2.close_gate.gate as gate_module

    original = gate_module.measure_snapshot
    count = 0

    def tampered(*args, **kwargs):
        nonlocal count
        count += 1
        measured = original(*args, **kwargs)
        if count == 2 and hasattr(measured, "snapshot"):
            object.__setattr__(measured.snapshot, "snapshot_key", "0" * 64)
        return measured

    monkeypatch.setattr(gate_module, "measure_snapshot", tampered)
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateUnknownWin32Error)


def test_missing_file_or_volume_identity_is_unsupported(close_port, source_path) -> None:
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    node.file_id = None
    missing_file = gate(close_port, source_path)
    assert isinstance(missing_file, CloseGateUnsupported)
    assert missing_file.error.underlying is not None
    node.file_id = b"f" * 16
    close_port.volume_available = False
    missing_volume = gate(close_port, source_path)
    assert isinstance(missing_volume, CloseGateUnsupported)
    assert not any(
        "\\locks\\ownership\\sources\\" in item[0].casefold()
        for item in close_port.detailed_open_history
    )


@pytest.mark.parametrize("unsafe", ["reparse", "directory", "fat", "removable"])
def test_reparse_wrong_type_and_non_local_ntfs_are_unsupported(
    close_port, source_path, unsafe
) -> None:
    node = close_port.nodes[close_port._key(source_path.canonical_dos_path)]
    if unsafe == "reparse":
        node.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    elif unsafe == "directory":
        node.attributes |= FILE_ATTRIBUTE_DIRECTORY
    elif unsafe == "fat":
        node.filesystem = "FAT32"
    else:
        node.drive_type = 2
    assert isinstance(gate(close_port, source_path), CloseGateUnsupported)


def test_unc_and_wrong_role_are_unsupported(close_port, source_path) -> None:
    unc = replace(
        source_path,
        canonical_dos_path=r"\\server\share\source.mp4",
        long_path=r"\\server\share\source.mp4",
    )
    assert isinstance(gate(close_port, unc), CloseGateUnsupported)
    internal = replace(source_path, role=PathRole.WORKSPACE_INTERNAL)
    assert isinstance(gate(close_port, internal), CloseGateUnsupported)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (ERROR_ACCESS_DENIED, CloseGateInaccessible),
        (ERROR_FILE_NOT_FOUND, CloseGateDisappeared),
        (ERROR_PATH_NOT_FOUND, CloseGateDisappeared),
        (ERROR_SHARING_VIOLATION, CloseGateBusy),
        (ERROR_LOCK_VIOLATION, CloseGateBusy),
        (ERROR_INVALID_HANDLE, CloseGateUnknownWin32Error),
        (9999, CloseGateUnknownWin32Error),
    ],
)
def test_restrictive_open_win32_classification(close_port, source_path, code, expected) -> None:
    close_port.source_open_error = Win32Failure(code, "CreateFileW", f"native {code}")
    result = gate(close_port, source_path)
    assert isinstance(result, expected)
    assert result.error.win32_code == code
    if code not in {ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION}:
        assert not isinstance(result, CloseGateBusy)


def test_delete_pending_open_query_and_ntstatus_mapping(close_port, source_path) -> None:
    close_port.source_open_error = Win32Failure(
        ERROR_DELETE_PENDING, "CreateFileW", "delete pending"
    )
    assert isinstance(gate(close_port, source_path), CloseGateDeletePending)

    close_port.delete_pending_override = True
    assert isinstance(gate(close_port, source_path), CloseGateDeletePending)
    close_port.delete_pending_override = False

    close_port.source_open_error = CloseGateWin32Failure(
        ERROR_ACCESS_DENIED,
        "NtCreateFile",
        "native delete pending",
        STATUS_DELETE_PENDING,
    )
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateDeletePending)
    assert result.error.ntstatus_code == STATUS_DELETE_PENDING


def test_unknown_ntstatus_is_preserved_without_busy_guess(close_port, source_path) -> None:
    close_port.source_open_error = CloseGateWin32Failure(777, "NtCreateFile", "unknown", 0xC0000ABC)
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateUnknownWin32Error)
    assert result.error.ntstatus_code == 0xC0000ABC


def test_lease_cannot_be_freely_constructed_copied_serialized_or_mutated(
    close_port, source_path
) -> None:
    with pytest.raises(TypeError):
        CloseGateLease()
    result = gate(close_port, source_path)
    assert isinstance(result, CloseGateClosed)
    lease = result.lease
    with pytest.raises(TypeError):
        copy.copy(lease)
    with pytest.raises(TypeError):
        copy.deepcopy(lease)
    with pytest.raises(TypeError):
        pickle.dumps(lease)
    with pytest.raises(AttributeError):
        lease._file_id = "0" * 32
    lease.close()


def test_late_cancel_does_not_revoke_a_committed_lease(close_port, source_path) -> None:
    token = CancellationToken()
    result = gate(close_port, source_path, token=token)
    assert isinstance(result, CloseGateClosed)
    assert token.cancel()
    assert not result.lease.closed
    result.lease.close()
