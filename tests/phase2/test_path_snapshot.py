from __future__ import annotations

from dataclasses import replace

import pytest

from matrix_auto_cutter.phase2.artifacts import AvailableIdentity, UnavailableIdentity
from matrix_auto_cutter.phase2.errors import ErrorCode
from matrix_auto_cutter.phase2.pathing import (
    PathRejected,
    PathRole,
    PathValidated,
    ensure_directory_tree,
    path_lock_key,
    reject_case_collisions,
    validate_path,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    DifferentInstance,
    FileTime,
    NotComparable,
    SameInstanceChanged,
    SameInstanceUnchanged,
    SnapshotAccessDenied,
    SnapshotEvidenceInsufficient,
    SnapshotFileMissing,
    SnapshotNotRegular,
    SnapshotOk,
    SnapshotOsError,
    SnapshotUnsafePath,
    compare_snapshots,
    snapshot_file,
)
from matrix_auto_cutter.phase2.win32_port import (
    FILE_ATTRIBUTE_OFFLINE,
    FILE_ATTRIBUTE_REPARSE_POINT,
)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (r"relative\file.mp4", ErrorCode.PATH_INPUT_FORM),
        (r"\\server\share\file.mp4", ErrorCode.PATH_UNC),
        (r"\\?\C:\file.mp4", ErrorCode.PATH_DEVICE_NAMESPACE),
        (r"\\.\C:\file.mp4", ErrorCode.PATH_DEVICE_NAMESPACE),
        (r"\??\C:\file.mp4", ErrorCode.PATH_DEVICE_NAMESPACE),
        (r"C:\a\\b", ErrorCode.PATH_COMPONENT_EMPTY),
        (r"C:\a\.\b", ErrorCode.PATH_DOT_COMPONENT),
        (r"C:\a\..\b", ErrorCode.PATH_DOT_COMPONENT),
        (r"C:\a\file:stream", ErrorCode.PATH_ADS),
        (r"C:\a\CON.txt", ErrorCode.PATH_RESERVED_NAME),
        (r"C:\a\COM¹", ErrorCode.PATH_RESERVED_NAME),
        (r"C:\a\LPT².log", ErrorCode.PATH_RESERVED_NAME),
        (r"C:\a\name.", ErrorCode.PATH_TRAILING_DOT_SPACE),
        ("C:\\a\\name ", ErrorCode.PATH_TRAILING_DOT_SPACE),
        ("C:\\a\\bad\x00name", ErrorCode.PATH_INPUT_FORM),
        (r"C:\a\*.mp4", ErrorCode.PATH_INPUT_FORM),
        ("C:\\a\\\ud800", ErrorCode.PATH_UNICODE_ROUNDTRIP),
    ],
)
def test_rejected_external_inputs(fake_port, value: str, code: ErrorCode) -> None:
    result = validate_path(fake_port, value, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathRejected)
    assert result.error.code is code


def test_internal_relative_absolute_containment_and_long_path(fake_port) -> None:
    fake_port.make_tree(r"C:\Work")
    root = ensure_directory_tree(fake_port, r"C:\Work")
    assert not isinstance(root, PathRejected)
    relative = validate_path(
        fake_port,
        ("projects", "file.json"),
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    absolute = validate_path(
        fake_port,
        r"c:/work/projects/file.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    assert isinstance(relative, PathValidated) and isinstance(absolute, PathValidated)
    assert relative.path.long_path == r"\\?\C:\Work\projects\file.json"
    escaped = validate_path(
        fake_port,
        r"C:\Other\file.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    assert isinstance(escaped, PathRejected)
    assert escaped.error.code is ErrorCode.PATH_ROOT_ESCAPE
    missing_root = validate_path(fake_port, ("x",), PathRole.WORKSPACE_INTERNAL)
    assert isinstance(missing_root, PathRejected)
    assert missing_root.error.code is ErrorCode.PATH_EVIDENCE_INSUFFICIENT
    wrong_relative = validate_path(fake_port, ("x",), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(wrong_relative, PathRejected)
    empty = validate_path(fake_port, (), PathRole.WORKSPACE_INTERNAL, workspace_root=root)
    assert isinstance(empty, PathRejected)


def test_case_collision_and_lock_key_mapping(fake_port) -> None:
    assert reject_case_collisions(fake_port, ("Alpha.json", "alpha.JSON")) is not None
    assert reject_case_collisions(fake_port, ("Alpha.json", "Beta.json")) is None
    invalid = reject_case_collisions(fake_port, ("CON",))
    assert invalid is not None and invalid.error.code is ErrorCode.PATH_RESERVED_NAME
    left = validate_path(fake_port, r"C:\Source\File.MP4", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    right = validate_path(fake_port, r"c:\source\file.mp4", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(left, PathValidated) and isinstance(right, PathValidated)
    assert path_lock_key(fake_port, left.path) == path_lock_key(fake_port, right.path)
    fake_port.failures["LCMapStringEx"] = [999]
    assert isinstance(path_lock_key(fake_port, left.path), PathRejected)


def test_handle_reparse_cloud_type_volume_and_final_path_fail_closed(fake_port) -> None:
    fake_port.make_tree(r"C:\Safe")
    file = fake_port.add_file(r"C:\Safe\source.mp4")
    ok = validate_path(
        fake_port,
        file.path,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    assert isinstance(ok, PathValidated)

    file.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    assert (
        validate_path(
            fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
        ).error.code
        is ErrorCode.PATH_REPARSE
    )
    file.attributes &= ~FILE_ATTRIBUTE_REPARSE_POINT
    file.attributes |= FILE_ATTRIBUTE_OFFLINE
    assert (
        validate_path(
            fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
        ).error.code
        is ErrorCode.PATH_EVIDENCE_INSUFFICIENT
    )
    file.attributes &= ~FILE_ATTRIBUTE_OFFLINE
    file.filesystem = "FAT32"
    assert (
        validate_path(
            fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
        ).error.code
        is ErrorCode.PATH_EVIDENCE_INSUFFICIENT
    )
    file.filesystem = "NTFS"
    file.drive_type = 2
    assert (
        validate_path(
            fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY, require_existing=True
        ).error.code
        is ErrorCode.PATH_EVIDENCE_INSUFFICIENT
    )
    file.drive_type = 3
    file.path = r"C:\Elsewhere\source.mp4"
    assert (
        validate_path(
            fake_port,
            r"C:\Safe\source.mp4",
            PathRole.EXTERNAL_SOURCE_READ_ONLY,
            require_existing=True,
        ).error.code
        is ErrorCode.PATH_ROOT_MISMATCH
    )


def test_snapshot_results_and_key_contract(fake_port) -> None:
    fake_port.make_tree(r"C:\Sources")
    file = fake_port.add_file(r"C:\Sources\one.mp4", b"abc")
    validated = validate_path(fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(validated, PathValidated)
    result = snapshot_file(fake_port, validated.path)
    assert isinstance(result, SnapshotOk)
    snapshot = result.snapshot
    other_path = replace(
        snapshot.path_ref,
        original_input=r"C:\Alias\one.mp4",
        canonical_dos_path=r"C:\Alias\one.mp4",
        long_path=r"\\?\C:\Alias\one.mp4",
    )
    assert replace(snapshot, path_ref=other_path).snapshot_key == snapshot.snapshot_key
    assert isinstance(compare_snapshots(snapshot, snapshot), SameInstanceUnchanged)
    resized = replace(snapshot, size_bytes=4)
    assert resized.snapshot_key != snapshot.snapshot_key
    assert isinstance(compare_snapshots(snapshot, resized), SameInstanceChanged)
    different = replace(
        snapshot,
        file_id=AvailableIdentity(scheme="file_id_128", value="ff" * 16),
    )
    assert isinstance(compare_snapshots(snapshot, different), DifferentInstance)
    unavailable = replace(snapshot, file_id=UnavailableIdentity())
    assert isinstance(compare_snapshots(snapshot, unavailable), NotComparable)
    bad_version = replace(snapshot)
    object.__setattr__(bad_version, "evidence_version", "file_snapshot/2.0")
    assert isinstance(compare_snapshots(snapshot, bad_version), ComparisonFailed)

    missing = validate_path(
        fake_port, r"C:\Sources\missing.mp4", PathRole.EXTERNAL_SOURCE_READ_ONLY
    )
    assert isinstance(missing, PathValidated)
    assert isinstance(snapshot_file(fake_port, missing.path), SnapshotFileMissing)
    directory = validate_path(fake_port, r"C:\Sources", PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(directory, PathValidated)
    assert isinstance(snapshot_file(fake_port, directory.path), SnapshotNotRegular)
    fake_port.failures["CreateFileW"] = [5]
    assert isinstance(snapshot_file(fake_port, validated.path), SnapshotAccessDenied)
    fake_port.failures["CreateFileW"] = [999]
    assert isinstance(snapshot_file(fake_port, validated.path), SnapshotOsError)
    file.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    assert isinstance(snapshot_file(fake_port, validated.path), SnapshotUnsafePath)


def test_snapshot_evidence_insufficient_and_exact_times(fake_port) -> None:
    fake_port.make_tree(r"C:\Sources")
    file = fake_port.add_file(r"C:\Sources\one.mp4")
    file.attributes |= FILE_ATTRIBUTE_OFFLINE
    validated = validate_path(fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(validated, PathValidated)
    assert isinstance(snapshot_file(fake_port, validated.path), SnapshotEvidenceInsufficient)
    file.attributes &= ~FILE_ATTRIBUTE_OFFLINE
    result = snapshot_file(fake_port, validated.path)
    assert isinstance(result, SnapshotOk)
    assert result.snapshot.last_write_time == FileTime(20)


def test_file_id_not_available_is_explicit(fake_port) -> None:
    fake_port.make_tree(r"C:\NoId")
    file = fake_port.add_file(r"C:\NoId\source.mp4")
    file.file_id = None
    validated = validate_path(fake_port, file.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(validated, PathValidated)
    result = snapshot_file(fake_port, validated.path)
    assert isinstance(result, SnapshotOk)
    assert isinstance(result.snapshot.file_id, UnavailableIdentity)
    root_node = fake_port._mkdir(r"C:\NoIdRoot")
    root_node.file_id = None
    root = ensure_directory_tree(fake_port, r"C:\NoIdRoot")
    assert not isinstance(root, PathRejected)
    assert isinstance(root.binding.root_file_id, UnavailableIdentity)
    fake_port.nodes[fake_port._key("C:\\")].attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    assert isinstance(ensure_directory_tree(fake_port, r"C:\RejectedByDrive"), PathRejected)
    fake_port.nodes[fake_port._key("C:\\")].attributes &= ~FILE_ATTRIBUTE_REPARSE_POINT
    bad = fake_port._mkdir(r"C:\RejectedComponent")
    bad.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    assert isinstance(ensure_directory_tree(fake_port, bad.path), PathRejected)
