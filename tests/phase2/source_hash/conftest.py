from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from tests.phase2.close_gate.conftest import FakeCloseGatePort, gate, make_source

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.close_gate import CloseGateClosed, CloseGateLease
from matrix_auto_cutter.phase2.source_hash import HashResult, hash_lease_source
from matrix_auto_cutter.phase2.win32_port import (
    OwnedHandle,
    Win32Err,
    Win32Failure,
    Win32Ok,
    Win32Result,
)

PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"
HASH_RUN_ID = "6ba7b814-9dad-4b8a-92fb-2a41f5468719"
PUBLISH_OPERATION_ID = UUID("2e157a84-2e31-49d9-b64e-494c24f8f612")


class HashingFakePort(FakeCloseGatePort):
    def __init__(self) -> None:
        super().__init__()
        self.hash_read_count = 0
        self.read_requests: list[int] = []
        self.read_handles: list[int] = []
        self.position_calls: list[tuple[int, int]] = []
        self.read_plan: list[bytes | Win32Failure] = []
        self.before_reads: dict[int, Callable[[], None]] = {}
        self.after_reads: dict[int, Callable[[], None]] = {}

    def set_file_offset(self, handle: OwnedHandle, offset: int) -> Win32Result[int]:
        self.position_calls.append((handle.value, offset))
        return super().set_file_offset(handle, offset)

    def read_file(self, handle: OwnedHandle, maximum_bytes: int) -> Win32Result[bytes]:
        if handle.value not in self.source_gate_handles:
            return super().read_file(handle, maximum_bytes)
        self.hash_read_count += 1
        index = self.hash_read_count
        self.read_requests.append(maximum_bytes)
        self.read_handles.append(handle.value)
        callback = self.before_reads.get(index)
        if callback is not None:
            callback()
        if self.read_plan:
            planned = self.read_plan.pop(0)
            if isinstance(planned, Win32Failure):
                result: Win32Result[bytes] = Win32Err(planned)
            else:
                key, offset = self.handles[handle.value]
                self.handles[handle.value] = (key, offset + len(planned))
                result = Win32Ok(planned)
        else:
            result = super().read_file(handle, maximum_bytes)
        callback = self.after_reads.get(index)
        if callback is not None:
            callback()
        return result


@dataclass
class HashCase:
    port: HashingFakePort
    lease: CloseGateLease

    def run(
        self,
        *,
        token: CancellationToken | None = None,
        block_size: int = 4,
        project_id: str = PROJECT_ID,
        hash_run_id: str = HASH_RUN_ID,
    ) -> HashResult:
        return hash_lease_source(
            self.lease,
            token or CancellationToken(),
            project_id,
            hash_run_id,
            block_size_bytes=block_size,
        )


def make_hash_case(data: bytes, path: str = r"C:\Sources\source.mp4") -> HashCase:
    port = HashingFakePort()
    source = make_source(port, path, data=data)
    result = gate(port, source)
    assert isinstance(result, CloseGateClosed)
    return HashCase(port, result.lease)


@pytest.fixture
def hash_case() -> HashCase:
    case = make_hash_case(b"abcdefghij")
    yield case
    if not case.lease.closed:
        case.lease.close()
