from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from tests.phase2.source_hash.conftest import make_hash_case

from matrix_auto_cutter.phase2.artifacts import UnavailableIdentity
from matrix_auto_cutter.phase2.source_hash import (
    PRODUCTION_BLOCK_SIZE_BYTES,
    HashCompleted,
    HashIoError,
    HashUnexpectedEof,
    SourceChanged,
)
from matrix_auto_cutter.phase2.win32_port import Win32Failure


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"abc",
        b"The quick brown fox jumps over the lazy dog",
        b"\x00\xff\x00binary\x00bytes",
        "GrÃ¼ÃŸe â€” æ¼¢å­—".encode(),
        b"x" * 4,
        b"x" * 5,
        bytes(range(256)) * 3,
    ],
)
def test_standard_vectors_and_byte_exact_content(data: bytes) -> None:
    case = make_hash_case(data)
    result = case.run(block_size=4)
    assert isinstance(result, HashCompleted)
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.bytes_read == len(data) == result.s0_size_bytes
    assert result.s0 == result.s4
    assert all(request <= 4 for request in case.port.read_requests)
    case.lease.close()


def test_chunk_partitions_and_partial_positive_reads_are_digest_invariant() -> None:
    data = b"abcdefghij"
    digests = []
    for plan in ([b"a", b"bc", b"d", b"efgh", b"ij", b""], None):
        case = make_hash_case(data)
        if plan is not None:
            case.port.read_plan = list(plan)
        result = case.run(block_size=4)
        assert isinstance(result, HashCompleted)
        digests.append(result.sha256)
        case.lease.close()
    assert digests == [hashlib.sha256(data).hexdigest()] * 2


def test_start_offset_is_forced_to_zero_and_same_handle_is_used(hash_case) -> None:
    handle = next(iter(hash_case.port.source_gate_handles))
    key, _ = hash_case.port.handles[handle]
    hash_case.port.handles[handle] = (key, 7)
    result = hash_case.run(block_size=3)
    assert isinstance(result, HashCompleted)
    assert result.sha256 == hashlib.sha256(b"abcdefghij").hexdigest()
    assert hash_case.port.position_calls == [(handle, 0)]
    assert set(hash_case.port.read_handles) == {handle}


@pytest.mark.parametrize(
    "plan",
    [
        [b""],
        [b"ab", b"cd", b""],
    ],
)
def test_early_eof_never_publishes_digest_or_receipt(plan: list[bytes]) -> None:
    case = make_hash_case(b"abcdefghij")
    case.port.read_plan = plan
    result = case.run(block_size=4)
    assert isinstance(result, HashUnexpectedEof)
    assert not hasattr(result, "sha256")
    assert not hasattr(result, "receipt")
    case.lease.close()


@pytest.mark.parametrize("extra", [b"x", b"several-extra-bytes"])
def test_extra_data_at_s0_end_is_source_changed(extra: bytes) -> None:
    case = make_hash_case(b"abcd")
    node = case.port.nodes[case.port._key(case.lease.source_path.canonical_dos_path)]
    case.port.after_reads[1] = lambda: node.data.extend(extra)
    result = case.run(block_size=4)
    assert isinstance(result, SourceChanged)
    assert result.error.phase == "hash.eof"
    case.lease.close()


def test_exact_eof_uses_one_immediate_bounded_probe() -> None:
    case = make_hash_case(b"abcd")
    result = case.run(block_size=4)
    assert isinstance(result, HashCompleted)
    assert case.port.read_requests == [4, 1]
    case.lease.close()


def test_seek_read_and_eof_probe_errors_preserve_native_code() -> None:
    seek_case = make_hash_case(b"abc")
    seek_case.port.failures["SetFilePointerEx"] = [701]
    seek = seek_case.run()
    assert isinstance(seek, HashIoError) and seek.error.win32_code == 701
    seek_case.lease.close()

    read_case = make_hash_case(b"abc")
    read_case.port.read_plan = [Win32Failure(702, "ReadFile", "read failed")]
    read = read_case.run()
    assert isinstance(read, HashIoError) and read.error.win32_code == 702
    read_case.lease.close()

    eof_case = make_hash_case(b"abc")
    eof_case.port.read_plan = [b"abc", Win32Failure(703, "ReadFile", "eof failed")]
    eof = eof_case.run(block_size=3)
    assert isinstance(eof, HashIoError) and eof.error.win32_code == 703
    eof_case.lease.close()


def test_invalid_s0_and_malformed_adapter_reads_fail_closed() -> None:
    negative = make_hash_case(b"abc")
    object.__setattr__(negative.lease.s0, "size_bytes", -1)
    assert isinstance(negative.run(), HashIoError)
    assert not negative.port.read_requests
    negative.lease.close()

    unavailable = make_hash_case(b"abc")
    object.__setattr__(unavailable.lease.s0, "file_id", UnavailableIdentity())
    assert isinstance(unavailable.run(), HashIoError)
    unavailable.lease.close()

    malformed = make_hash_case(b"abcdef")
    malformed.port.read_plan = [b"12345"]
    assert isinstance(malformed.run(block_size=4), HashIoError)
    malformed.lease.close()


@pytest.mark.parametrize(
    "block_size",
    [0, -1, True, 1.5, PRODUCTION_BLOCK_SIZE_BYTES + 1],
)
def test_invalid_block_sizes_are_rejected_before_hashing(block_size: object) -> None:
    case = make_hash_case(b"abc")
    result = case.run(block_size=block_size)  # type: ignore[arg-type]
    assert isinstance(result, HashIoError)
    assert not case.port.read_requests
    case.lease.close()


def test_huge_declared_s0_size_does_not_preallocate_or_expand_reads() -> None:
    case = make_hash_case(b"abc")
    huge = replace(case.lease.s0, size_bytes=10**12)
    object.__setattr__(case.lease, "s0", huge)
    case.port.read_plan = [b""]
    result = case.run(block_size=1024)
    assert isinstance(result, HashUnexpectedEof)
    assert case.port.read_requests == [1024]
    case.lease.close()
