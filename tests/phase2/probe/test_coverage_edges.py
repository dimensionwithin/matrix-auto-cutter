from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import (
    VERSION_TEXT,
    FakeProcessPort,
    golden_json,
    golden_stream,
    issued_inspection_for,
)
from tests.phase2.probe.test_runner_process import source_request

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.pathing import PathRole, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    ProbeErrorCode,
    ProbeFailed,
    ProbeProcessFailed,
    ProbeProcessOk,
    ProbeRequest,
    ProcessDiagnostics,
    parse_probe_json,
    run_probe,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspection,
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.errors import probe_error
from matrix_auto_cutter.phase2.probe.json_parser import MAX_JSON_BYTES, _bound_tree
from matrix_auto_cutter.phase2.probe.runner import (
    _close_with_primary,
    _snapshot_failure,
    _verify_source,
)
from matrix_auto_cutter.phase2.probe.stream_selection import _dominates
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file
from matrix_auto_cutter.phase2.win32_port import (
    Win32Err,
    Win32Failure,
    Win32Ok,
)


class SequenceTrust:
    def __init__(self, values):
        self.values = list(values)

    def inspect(self, _path):
        return self.values.pop(0)


def inspection_for(binary, *, close_code: int | None = None) -> BinaryInspection:
    return issued_inspection_for(
        binary,
        [],
        value=123,
        close_failure=close_code,
    )


def test_contract_and_small_helper_edges(validated_binary) -> None:
    with pytest.raises(ValueError):
        ProbeRequest(validated_binary, validated_binary.path, "x", 0)
    with pytest.raises(ValueError):
        ProbeRequest(validated_binary, validated_binary.path, "x", 601)
    workspace_source = replace(validated_binary.path, role=PathRole.WORKSPACE_INTERNAL)
    with pytest.raises(ValueError):
        ProbeRequest(validated_binary, workspace_source, "0" * 64)
    with pytest.raises(ValueError):
        ProbeRequest(validated_binary, validated_binary.path, "x")
    with pytest.raises(ValueError):
        ProbeRequest(validated_binary, validated_binary.path, "g" * 64)
    with pytest.raises(ValueError):
        _snapshot_failure(SnapshotOk(validated_binary.original_snapshot), "x")
    close = probe_error(ProbeErrorCode.BINARY_ACCESS, ErrorCategory.IO, "close", "close")
    assert _close_with_primary(None, close) is close
    assert _close_with_primary(primary=None, close_error=None) is None
    primary = probe_error(ProbeErrorCode.PROCESS_FAILED, ErrorCategory.IO, "primary", "primary")
    combined = _close_with_primary(primary, close)
    assert combined is not None and combined.secondary == (close,)
    altered = replace(
        validated_binary.original_snapshot,
        last_write_time=replace(validated_binary.last_write_time, value=123456),
    )
    assert _verify_source(altered, altered, validated_binary.original_snapshot.snapshot_key)


def test_partial_resolution_dominance_private_edges() -> None:
    result = parse_probe_json(
        golden_json(
            [
                golden_stream(0, "video", width=1920, height=1080),
                golden_stream(1, "video", width=1280, height=720),
            ]
        )
    )
    assert not hasattr(result, "error")
    assert _dominates(result.streams[0], result.streams[1])
    assert not _dominates(result.streams[1], result.streams[0])


def test_parser_remaining_lexical_and_field_edges() -> None:
    raws = [
        b"]",
        b'"' + b"x" * (MAX_JSON_BYTES + 1),
        golden_json([golden_stream(0, "audio", duration=1)]),
        golden_json([golden_stream(0, "audio", duration="bad")]),
        golden_json([golden_stream(0, "audio", r_frame_rate=1)]),
        golden_json([golden_stream(0, "audio", r_frame_rate="1/")]),
        golden_json([golden_stream(0, "audio", tags=[])]),
        golden_json([golden_stream(0, "audio", disposition=[])]),
        golden_json(
            [
                golden_stream(
                    0,
                    "video",
                    side_data_list=[{"side_data_type": "Display Matrix", "rotation": True}],
                )
            ]
        ),
        golden_json(
            [
                golden_stream(
                    0,
                    "video",
                    side_data_list=[{"side_data_type": "Display Matrix", "rotation": []}],
                )
            ]
        ),
        golden_json([golden_stream(0, "audio")], programs=["bad"]),
        golden_json(
            [golden_stream(0, "audio")],
            programs=[{"program_id": 1, "streams": {}}],
        ),
        golden_json(
            [golden_stream(0, "audio")],
            programs=[{"program_id": 1, "streams": [1]}],
        ),
    ]
    for raw in raws:
        assert hasattr(parse_probe_json(raw), "error")
    for tags in ({"rotate": "bad"}, {"rotate": "90", "Rotate": "different"}):
        assert not hasattr(
            parse_probe_json(golden_json([golden_stream(0, "video", tags=tags)])),
            "error",
        )
    valid_none = json.loads(golden_json([golden_stream(0, "audio")]))
    valid_none["streams"][0].pop("disposition")
    assert not hasattr(parse_probe_json(json.dumps(valid_none).encode()), "error")
    escaped = ('"' + "\\u0061" * 1_048_577 + '"').encode()
    assert hasattr(parse_probe_json(escaped), "error")
    with pytest.raises(ValueError):
        _bound_tree("x" * 1_048_577)
    rational_none = golden_stream(0, "audio", time_base=None)
    assert not hasattr(parse_probe_json(golden_json([rational_none])), "error")


class FaultPort(FakePort):
    def __init__(self) -> None:
        super().__init__()
        self.query_count = 0
        self.case_count = 0
        self.query_mutation: str | None = None
        self.extra_eof = False
        self.overread = False

    def query_file_info(self, handle):
        result = super().query_file_info(handle)
        self.query_count += 1
        if isinstance(result, Win32Err):
            return result
        info = result.value
        if self.query_mutation == "short" and self.query_count == 1:
            return Win32Ok(replace(info, size_bytes=info.size_bytes + 1))
        if self.query_mutation == "after_error" and self.query_count == 2:
            return Win32Err(Win32Failure(77, "GetFileInformationByHandleEx", "after"))
        if self.query_mutation == "after_change" and self.query_count == 2:
            return Win32Ok(replace(info, change_time_100ns=info.change_time_100ns + 1))
        if self.query_mutation == "too_large" and self.query_count == 1:
            return Win32Ok(replace(info, size_bytes=1024 * 1024 * 1024 + 1))
        return result

    def read_file(self, handle, maximum_bytes):
        result = super().read_file(handle, maximum_bytes)
        if self.overread and isinstance(result, Win32Ok) and result.value:
            self.overread = False
            return Win32Ok(result.value + b"x")
        if self.extra_eof and isinstance(result, Win32Ok) and not result.value:
            self.extra_eof = False
            return Win32Ok(b"x")
        return result

    def ordinal_case_key(self, value):
        self.case_count += 1
        if self.case_count == 2 and self.query_mutation == "actual_case_error":
            return Win32Err(Win32Failure(88, "LCMapStringEx", "actual"))
        return super().ordinal_case_key(value)


@pytest.mark.parametrize(
    "fault",
    [
        "open",
        "query_before",
        "unsafe",
        "case_expected",
        "actual_case_error",
        "path_mismatch",
        "read",
        "short",
        "extra_eof",
        "after_error",
        "after_change",
        "overread",
        "zero_size",
        "too_large",
        "failure_and_close",
    ],
)
def test_native_binary_inspector_fail_closed_edges(monkeypatch, fault: str) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    port = FaultPort()
    node = port.add_file(r"C:\Tools\ffprobe.exe", b"abc")
    lexical = validate_path(port, node.path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert hasattr(lexical, "path")
    monkeypatch.setattr(binary_module, "validate_path", lambda *_args, **_kwargs: lexical)
    if fault == "open":
        port.failures["CreateFileW"] = [5]
    elif fault == "query_before":
        port.failures["GetFileInformationByHandleEx"] = [6]
    elif fault == "unsafe":
        node.filesystem = "FAT32"
    elif fault == "case_expected":
        port.failures["LCMapStringEx"] = [7]
    elif fault == "actual_case_error":
        port.query_mutation = fault
    elif fault == "path_mismatch":
        node.path = r"C:\Other\ffprobe.exe"
    elif fault == "read":
        port.failures["ReadFile"] = [8]
    elif fault == "short":
        port.query_mutation = fault
    elif fault == "extra_eof":
        port.extra_eof = True
    elif fault == "overread":
        port.overread = True
    elif fault == "zero_size":
        node.data.clear()
    elif fault == "too_large" or fault == "after_error" or fault == "after_change":
        port.query_mutation = fault
    else:
        port.failures["GetFileInformationByHandleEx"] = [6]
        port.close_results[port._key(node.path)] = [9]
    result = NativeBinaryTrustPort(port).inspect(lexical.path)
    assert isinstance(result, BinaryInspectionFailed)


def test_runner_parser_selection_and_snapshot_failure_edges(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    trust = NativeBinaryTrustPort(fake_port)
    bad_json = run_probe(
        request,
        trust,
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(b"{}", b""))),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(bad_json, ProbeFailed) and bad_json.error.code is ProbeErrorCode.SCHEMA

    unsupported = run_probe(
        request,
        trust,
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(golden_json([]), b""))),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(unsupported, ProbeFailed)
    assert unsupported.error.code is ProbeErrorCode.UNSUPPORTED_MEDIA

    source_node = fake_port.nodes[fake_port._key(request.source.canonical_dos_path)]
    del fake_port.nodes[fake_port._key(request.source.canonical_dos_path)]
    missing_before = run_probe(
        request,
        trust,
        FakeProcessPort(),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(missing_before, ProbeFailed)
    fake_port.nodes[fake_port._key(request.source.canonical_dos_path)] = source_node

    def remove_source(*_args: object) -> None:
        del fake_port.nodes[fake_port._key(request.source.canonical_dos_path)]

    missing_after = run_probe(
        request,
        trust,
        FakeProcessPort(
            ProbeProcessOk(ProcessDiagnostics(golden_json([golden_stream(0, "audio")]), b"")),
            callback=remove_source,
        ),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(missing_after, ProbeFailed)


def test_runner_post_binary_failure_change_and_close_edges(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]), b""
            )
        )
    )
    failure = BinaryInspectionFailed(
        probe_error(ProbeErrorCode.BINARY_ACCESS, ErrorCategory.IO, "post", "post")
    )
    result = run_probe(
        request,
        SequenceTrust([inspection_for(validated_binary), failure]),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed) and result.error.code is ProbeErrorCode.BINARY_ACCESS

    changed_snapshot = replace(
        validated_binary.original_snapshot,
        last_write_time=replace(validated_binary.last_write_time, value=999),
    )
    from matrix_auto_cutter.phase2.probe.contracts import BinaryEvidence

    changed = inspection_for(validated_binary)
    changed.evidence = BinaryEvidence(
        changed.evidence.volume_id,
        changed.evidence.file_id,
        changed.evidence.size_bytes,
        changed.evidence.creation_time,
        changed.evidence.last_write_time,
        changed.evidence.change_time,
        "0" * 64,
        changed_snapshot,
    )
    result = run_probe(
        request,
        SequenceTrust([inspection_for(validated_binary), changed]),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed) and result.error.code is ProbeErrorCode.BINARY_CHANGED

    result = run_probe(
        request,
        SequenceTrust(
            [inspection_for(validated_binary, close_code=9), inspection_for(validated_binary)]
        ),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed) and result.error.phase == "binary_handle_close"

    result = run_probe(
        request,
        SequenceTrust(
            [inspection_for(validated_binary), inspection_for(validated_binary, close_code=10)]
        ),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed) and result.error.phase == "binary_handle_close"


def test_runner_postsnapshot_and_cleanup_never_replace_prior_primary(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    source_key = fake_port._key(request.source.canonical_dos_path)
    source_node = fake_port.nodes[source_key]
    process_error = probe_error(
        ProbeErrorCode.PROCESS_FAILED,
        ErrorCategory.IO,
        "causal_process_failure",
        "process failed first",
    )

    def remove_source(*_args: object) -> None:
        fake_port.nodes.pop(source_key, None)

    failed = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(
            ProbeProcessFailed(process_error, ProcessDiagnostics(b"", b""), 1),
            callback=remove_source,
        ),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(failed, ProbeFailed)
    assert failed.error.phase == "causal_process_failure"
    assert failed.error.secondary

    fake_port.nodes[source_key] = source_node
    cleanup_first = run_probe(
        request,
        SequenceTrust(
            [inspection_for(validated_binary, close_code=71), inspection_for(validated_binary)]
        ),
        FakeProcessPort(
            ProbeProcessOk(ProcessDiagnostics(golden_json([golden_stream(0, "audio")]), b"")),
            callback=remove_source,
        ),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(cleanup_first, ProbeFailed)
    assert cleanup_first.error.phase == "source_snapshot_after"
    assert any(error.phase == "binary_handle_close" for error in cleanup_first.error.secondary)


def test_binary_validation_failure_or_close_paths(fake_port: FakePort, validated_binary) -> None:
    missing = validate_ffprobe_binary(
        type("Candidate", (), {"path": r"C:\missing\ffprobe.exe"})(),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert missing.error.code is ProbeErrorCode.BINARY_ACCESS

    inspect_failure = BinaryInspectionFailed(
        probe_error(ProbeErrorCode.BINARY_HASH, ErrorCategory.IO, "inspect", "inspect")
    )
    initial = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspect_failure]),
        FakeProcessPort(),
    )
    assert initial.error.code is ProbeErrorCode.BINARY_HASH

    process_error = probe_error(
        ProbeErrorCode.PROCESS_FAILED, ErrorCategory.IO, "process", "process"
    )
    failed_process = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspection_for(validated_binary)]),
        FakeProcessPort(ProbeProcessFailed(process_error, ProcessDiagnostics(b"", b""), 1)),
    )
    assert failed_process.error.code is ProbeErrorCode.PROCESS_FAILED

    bad_version = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspection_for(validated_binary)]),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(b"bad", b""))),
    )
    assert bad_version.error.code is ProbeErrorCode.VERSION_OUTPUT

    stderr_version = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspection_for(validated_binary), inspection_for(validated_binary)]),
        FakeProcessPort(
            ProbeProcessOk(
                ProcessDiagnostics(VERSION_TEXT.encode(), b"diagnostic ffprobe version 1.0.0")
            )
        ),
    )
    assert isinstance(stderr_version, BinaryValidated)
    assert stderr_version.binary.version_stderr_output == b"diagnostic ffprobe version 1.0.0"

    post_failure = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspection_for(validated_binary), inspect_failure]),
        FakeProcessPort(),
    )
    assert post_failure.error.code is ProbeErrorCode.BINARY_HASH

    close_failure = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust(
            [inspection_for(validated_binary), inspection_for(validated_binary, close_code=5)]
        ),
        FakeProcessPort(),
    )
    assert close_failure.error.code is ProbeErrorCode.BINARY_ACCESS

    both_failure = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust([inspection_for(validated_binary, close_code=6)]),
        FakeProcessPort(ProbeProcessFailed(process_error, ProcessDiagnostics(b"", b""), 1)),
    )
    assert both_failure.error.secondary

    initial_close = validate_ffprobe_binary(
        type("Candidate", (), {"path": validated_binary.canonical_dos_path})(),
        fake_port,
        SequenceTrust(
            [inspection_for(validated_binary, close_code=7), inspection_for(validated_binary)]
        ),
        FakeProcessPort(),
    )
    assert initial_close.error.phase == "binary_handle_close"


def test_media_profile_module_is_importable() -> None:
    from matrix_auto_cutter.phase2.probe import media_profile

    assert media_profile.MediaProfile.__name__ == "MediaProfile"
