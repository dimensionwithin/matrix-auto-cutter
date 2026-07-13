from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from matrix_auto_cutter.phase2.artifacts import (
    AvailableIdentity,
    ProjectDocument,
    UnavailableIdentity,
    WorkspaceRootBinding,
    canonical_bytes,
    is_canonical_uuid4,
    parse_project_bytes,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory, ErrorCode, failure
from matrix_auto_cutter.phase2.progress import (
    ProgressEmissionRejected,
    ProgressEvent,
    ProgressReporter,
)


def binding() -> WorkspaceRootBinding:
    return WorkspaceRootBinding(
        canonical_dos_path=r"C:\Work",
        volume_identity=AvailableIdentity(scheme="volume", value="1"),
        root_file_id=UnavailableIdentity(),
    )


def test_uuid_and_project_canonical_contract() -> None:
    project_id = "550e8400-e29b-41d4-a716-446655440000"
    assert is_canonical_uuid4(project_id)
    for invalid in (
        project_id.upper(),
        "550e8400-e29b-11d4-a716-446655440000",
        "invalid",
    ):
        assert not is_canonical_uuid4(invalid)
    document = ProjectDocument(project_id=project_id, workspace_root_binding=binding(), revision=0)
    raw = canonical_bytes(document)
    assert raw.endswith(b"\n") and not raw.startswith(b"\xef\xbb\xbf")
    assert parse_project_bytes(raw) == document
    with pytest.raises(ValueError):
        parse_project_bytes(raw[:-1])
    with pytest.raises(ValueError):
        parse_project_bytes(b"\xef\xbb\xbf" + raw)
    with pytest.raises(ValueError):
        parse_project_bytes(b"{}\n" + b" " * (1024 * 1024))
    with pytest.raises(ValueError):
        parse_project_bytes(raw.replace(b'"revision":0', b'"revision":1 '))
    with pytest.raises(ValidationError):
        ProjectDocument.model_validate(
            {
                "project_id": project_id,
                "workspace_root_binding": binding(),
                "revision": -1,
                "unknown": True,
            }
        )


def test_structured_error_preserves_native_evidence() -> None:
    cause = OSError("disk")
    error = failure(
        ErrorCode.PATH_OS_ERROR,
        ErrorCategory.IO,
        "CreateFileW",
        "unknown",
        win32_code=999,
        cause=cause,
        retryable=True,
    )
    assert error.win32_code == 999 and error.cause is cause and error.retryable
    with pytest.raises(FrozenInstanceError):
        error.message = "changed"  # type: ignore[misc]


def test_cancellation_is_monotone_idempotent_and_thread_safe() -> None:
    token = CancellationToken()
    results: list[bool] = []
    threads = [threading.Thread(target=lambda: results.append(token.cancel())) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count(True) == 1
    assert token.is_cancelled and token.wait(0)
    assert token.begin_irreversible_commit() is None
    assert not token.cancel()


def test_commit_boundary_linearization() -> None:
    token = CancellationToken()
    permit = token.begin_irreversible_commit()
    assert permit is not None and permit.sequence == 1
    token.cancel()
    assert permit.sequence == 1
    assert token.begin_irreversible_commit() is None

    cancelled_first = CancellationToken()
    cancelled_first.cancel()
    assert cancelled_first.begin_irreversible_commit() is None


def test_progress_threads_snapshot_backpressure_and_listener_isolation() -> None:
    reporter = ProgressReporter(uuid4(), diagnostic_limit=2)
    seen: list[tuple[str, int]] = []
    entered = threading.Event()
    release = threading.Event()

    def slow(event) -> None:
        entered.set()
        release.wait(1)
        seen.append(("slow", event.sequence))

    def bad(event) -> None:
        raise RuntimeError(f"broken {event.sequence}")

    def mutating(event) -> None:
        seen.append(("mutating", event.sequence))
        reporter.remove_listener(mutating)
        reporter.add_listener(late)

    def late(event) -> None:
        seen.append(("late", event.sequence))

    reporter.add_listener(slow)
    reporter.add_listener(slow)
    reporter.add_listener(bad)
    reporter.add_listener(mutating)
    worker = threading.Thread(target=lambda: reporter.emit("start", {"value": 1}))
    worker.start()
    assert entered.wait(1)
    assert worker.is_alive()
    release.set()
    worker.join()
    second = reporter.emit("next", {"value": "ok"})
    assert isinstance(second, ProgressEvent)
    assert second.sequence == 2
    assert ("late", 2) in seen and ("late", 1) not in seen
    diagnostics = reporter.diagnostics()
    assert len(diagnostics) == 2 and diagnostics[-1].error_type == "RuntimeError"

    sequences: list[int] = []
    concurrent = ProgressReporter(UUID("550e8400-e29b-41d4-a716-446655440000"))
    concurrent.add_listener(lambda event: sequences.append(event.sequence))
    threads = [threading.Thread(target=lambda: concurrent.emit("tick", {})) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sequences == list(range(1, 21))


def test_progress_reentrant_rejection_and_unformattable_listener_error() -> None:
    reporter = ProgressReporter(uuid4())
    later: list[int] = []
    nested: list[object] = []

    class BrokenError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("broken str")

        def __repr__(self) -> str:
            raise RuntimeError("broken repr")

    def reentrant(event: ProgressEvent) -> None:
        nested.append(reporter.emit("nested", {}))

    def broken(event: ProgressEvent) -> None:
        raise BrokenError()

    reporter.add_listener(reentrant)
    reporter.add_listener(broken)
    reporter.add_listener(lambda event: later.append(event.sequence))
    result = reporter.emit("outer", {})
    assert isinstance(result, ProgressEvent)
    assert isinstance(nested[0], ProgressEmissionRejected)
    assert later == [1]
    diagnostics = reporter.diagnostics()
    assert any(item.error_type == "BrokenError" for item in diagnostics)
    assert diagnostics[-1].detail == "<exception-detail-unavailable>"


def test_progress_concurrent_delivery_waits_in_allocated_order() -> None:
    reporter = ProgressReporter(uuid4())
    entered = threading.Event()
    release = threading.Event()
    seen: list[int] = []

    def listener(event: ProgressEvent) -> None:
        if event.sequence == 1:
            entered.set()
            assert release.wait(5)
        seen.append(event.sequence)

    reporter.add_listener(listener)
    first = threading.Thread(target=lambda: reporter.emit("first", {}))
    second = threading.Thread(target=lambda: reporter.emit("second", {}))
    first.start()
    assert entered.wait(5)
    second.start()
    with reporter._condition:
        assert reporter._condition.wait_for(lambda: reporter._sequence == 2, timeout=5)
    release.set()
    first.join(5)
    second.join(5)
    assert seen == [1, 2]


def test_progress_hostile_listener_and_exception_names_are_isolated() -> None:
    reporter = ProgressReporter(uuid4())
    later: list[int] = []

    class HostileErrorMeta(type):
        def __getattribute__(cls, name: str) -> object:
            if name == "__name__":
                raise RuntimeError("no type name")
            return super().__getattribute__(name)

    class HostileError(Exception, metaclass=HostileErrorMeta):
        pass

    class HostileListener:
        @property
        def __name__(self) -> str:
            raise RuntimeError("no listener name")

        def __call__(self, event: ProgressEvent) -> None:
            raise HostileError()

    reporter.add_listener(HostileListener())
    reporter.add_listener(lambda event: later.append(event.sequence))
    result = reporter.emit("event", {})
    assert isinstance(result, ProgressEvent)
    assert later == [1]
    diagnostic = reporter.diagnostics()[0]
    assert diagnostic.listener_name == "<listener-name-unavailable>"
    assert diagnostic.error_type == "<exception-type-unavailable>"


@pytest.mark.parametrize(
    ("kind", "payload"),
    [
        ("", {}),
        ("x" * 65, {}),
        ("ok", {str(index): index for index in range(33)}),
        ("ok", {"x" * 65: 1}),
        ("ok", {"x": "v" * 1025}),
    ],
)
def test_progress_bounds(kind: str, payload: dict[str, object]) -> None:
    reporter = ProgressReporter(uuid4())
    with pytest.raises(ValueError):
        reporter.emit(kind, payload)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ProgressReporter(uuid4(), diagnostic_limit=0)


def test_progress_remove_absent_is_safe() -> None:
    reporter = ProgressReporter(uuid4())

    def listener(event) -> None:
        del event

    reporter.remove_listener(listener)
    assert reporter.diagnostics() == ()
