from __future__ import annotations

import pytest
from tests.phase2.close_gate.conftest import alias_source
from tests.phase2.source_confirmation.conftest import make_case

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.source_confirmation import (
    SourceConfirmationFailed,
    SourceConfirmed,
    SourceDisappeared,
    SourceInvalidated,
    confirm_source,
)
from matrix_auto_cutter.phase2.win32_port import FILE_ATTRIBUTE_REPARSE_POINT


@pytest.mark.parametrize("mutation", ["file_id", "volume", "size", "reparse"])
def test_pre_probe_path_or_instance_change_prevents_process_start(mutation: str) -> None:
    case = make_case()
    try:
        node = case.port.nodes[case.port._key(case.request.lease.source_path.canonical_dos_path)]
        if mutation == "file_id":
            node.file_id = b"z" * 16
        elif mutation == "volume":
            node.volume += 1
        elif mutation == "size":
            node.data.extend(b"replacement")
        else:
            node.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
        result = confirm_source(case.ports, case.request, CancellationToken())
        if mutation == "reparse":
            assert isinstance(result, SourceConfirmationFailed)
        else:
            assert isinstance(result, SourceInvalidated)
        assert case.process.calls is None
        assert case.port.hash_read_count == 0
    finally:
        case.close()


def test_deleted_bound_path_is_disappeared_before_probe() -> None:
    case = make_case()
    try:
        del case.port.nodes[case.port._key(case.request.lease.source_path.canonical_dos_path)]
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceDisappeared)
        assert case.process.calls is None
    finally:
        case.close()


def test_final_path_swap_after_successful_s5_is_invalidated() -> None:
    case = make_case()
    try:
        node = case.port.nodes[case.port._key(case.request.lease.source_path.canonical_dos_path)]
        original = case.port.query_file_info

        def swap_after_s5(handle):
            result = original(handle)
            if (
                handle.value in case.port.source_gate_handles
                and case.port.snapshot_query_count == 6
            ):
                node.file_id = b"q" * 16
            return result

        case.port.query_file_info = swap_after_s5
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceInvalidated)
        assert result.error.code.value == "E_SOURCE_CHANGED"
    finally:
        case.close()


def test_hardlink_same_instance_does_not_rewrite_original_source_binding() -> None:
    case = make_case(source_path=r"C:\Long Unicode Ã¤\source.mp4")
    try:
        alias = alias_source(case.port, case.request.lease.source_path, r"C:\Alias\other.mp4")
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        assert result.confirmed_source.evidence.source_path == (
            case.request.lease.source_path.canonical_dos_path
        )
        assert result.confirmed_source.evidence.source_path != alias.canonical_dos_path
    finally:
        case.close()
