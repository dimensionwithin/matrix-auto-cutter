from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from matrix_auto_cutter.manual_finalizer import (
    ManualFinalizationFailed,
    ManualFinalizationResult,
    ManualFinalizationSucceeded,
    ManualFinalizerRequest,
)
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.product_runner import (
    JOURNAL_SUFFIX,
    JournalInspection,
    JournalReady,
    JournalUnavailable,
    ProductRunner,
    RunnerDependencies,
    RunnerStatus,
    RunnerStatusCode,
    SessionState,
    SingleInstance,
)

SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"
JOURNAL_SHA = "a" * 64
NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)


@dataclass
class FakePorts:
    inspections: dict[str, JournalInspection]
    finalizer_results: list[ManualFinalizationResult | BaseException]
    requests: list[ManualFinalizerRequest] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)

    def inspect(self, path: Path) -> JournalInspection:
        return self.inspections[path.name]

    def ensure(self, project_id: str, workspace: str, token: CancellationToken) -> str | None:
        del workspace, token
        self.projects.append(project_id)
        return None

    def finalize(self, request: ManualFinalizerRequest) -> ManualFinalizationResult:
        self.requests.append(request)
        result = self.finalizer_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _success(source: Path) -> ManualFinalizationSucceeded:
    return ManualFinalizationSucceeded(
        str(source.with_suffix(".obs-events.json")),
        "33333333-3333-4333-8333-333333333333",
        SESSION_A,
        False,
    )


def _case(
    tmp_path: Path,
    inspections: dict[str, JournalInspection],
    results: list[ManualFinalizationResult | BaseException] | None = None,
) -> tuple[ProductRunner, FakePorts, Path, Path]:
    journals = tmp_path / "journals"
    state = tmp_path / "state"
    sources = tmp_path / "sources"
    journals.mkdir(exist_ok=True)
    sources.mkdir(exist_ok=True)
    for name in inspections:
        (journals / name).write_bytes(b"journal\n")
    ports = FakePorts(inspections, results or [])
    runner = ProductRunner(
        journals,
        state,
        str(sources),
        str(tmp_path / "workspace"),
        RunnerDependencies(ports.inspect, ports.ensure, ports.finalize, lambda: NOW, uuid4),
        output=io.StringIO(),
    )
    runner.ready()
    return runner, ports, state, sources


def _journal_name(session: str) -> str:
    return f"{session}{JOURNAL_SUFFIX}"


def _ready(session: str, source: Path) -> JournalReady:
    return JournalReady(session, JOURNAL_SHA, str(source))


def _status(state: Path) -> RunnerStatus:
    return RunnerStatus.model_validate_json((state / "status.json").read_bytes())


def _session(state: Path, session: str = SESSION_A) -> SessionState:
    return SessionState.model_validate_json((state / "sessions" / f"{session}.json").read_bytes())


def test_incomplete_journal_is_not_finalized(tmp_path: Path) -> None:
    pending = JournalUnavailable(
        RunnerStatusCode.JOURNAL_INCOMPLETE,
        "E_JOURNAL_INCOMPLETE",
        "Journal ist noch nicht vollständig.",
        SESSION_A,
    )
    runner, ports, state, _ = _case(tmp_path, {_journal_name(SESSION_A): pending})

    runner.scan_once()

    assert ports.requests == []
    assert _status(state).code is RunnerStatusCode.JOURNAL_INCOMPLETE
    assert not (state / "sessions" / f"{SESSION_A}.json").exists()


def test_valid_stop_uses_exact_journal_source_and_preserves_raw_file(tmp_path: Path) -> None:
    source_name = "exact recording.mp4"
    source = tmp_path / "sources" / source_name
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"raw-recording-must-not-change")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    other = source.with_name("newer similarly named.mp4")
    other.write_bytes(b"wrong file")
    ready = _ready(SESSION_A, source)
    runner, ports, state, _ = _case(
        tmp_path,
        {_journal_name(SESSION_A): ready},
        [_success(source)],
    )

    runner.scan_once()

    assert len(ports.requests) == 1
    assert ports.requests[0].source_path == str(source)
    assert ports.requests[0].journal_path.endswith(_journal_name(SESSION_A))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert _session(state).status is RunnerStatusCode.SIDECAR_SUCCEEDED
    assert _status(state).code is RunnerStatusCode.SIDECAR_SUCCEEDED


def test_missing_mp4_produces_stable_retryable_status(tmp_path: Path) -> None:
    missing = tmp_path / "sources" / "missing.mp4"
    runner, ports, state, _ = _case(
        tmp_path,
        {_journal_name(SESSION_A): _ready(SESSION_A, missing)},
    )

    runner.scan_once()
    runner.scan_once()

    assert ports.requests == []
    assert _session(state).status is RunnerStatusCode.SOURCE_MISSING
    assert _status(state).error_code == "E_RUNNER_SOURCE_MISSING"


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (RunnerStatusCode.JOURNAL_INVALID, "E_JOURNAL_CORRUPT"),
        (RunnerStatusCode.STOP_NOT_FINALIZABLE, "E_JOURNAL_OUTPUT_FAILURE"),
    ],
)
def test_rejected_journal_has_stable_status(
    tmp_path: Path, code: RunnerStatusCode, error: str
) -> None:
    rejected = JournalUnavailable(code, error, "Journal abgelehnt.", SESSION_A)
    runner, ports, state, _ = _case(tmp_path, {_journal_name(SESSION_A): rejected})

    runner.scan_once()

    assert ports.requests == []
    assert _status(state).code is code
    assert _status(state).error_code == error


def test_source_outside_product_root_is_never_finalized(tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "recording.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"raw")
    runner, ports, state, _ = _case(
        tmp_path,
        {_journal_name(SESSION_A): _ready(SESSION_A, outside)},
    )

    runner.scan_once()
    runner.scan_once()

    assert ports.requests == []
    assert _session(state).status is RunnerStatusCode.SOURCE_OUTSIDE_ROOT


def test_success_remains_idempotent_after_runner_restart(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "recording.mp4"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"raw")
    inspections = {_journal_name(SESSION_A): _ready(SESSION_A, source)}
    first, first_ports, _, _ = _case(tmp_path, inspections, [_success(source)])
    first.scan_once()
    assert len(first_ports.requests) == 1
    second_ports = FakePorts(inspections, [])
    second = ProductRunner(
        tmp_path / "journals",
        tmp_path / "state",
        str(tmp_path / "sources"),
        str(tmp_path / "workspace"),
        RunnerDependencies(
            second_ports.inspect,
            second_ports.ensure,
            second_ports.finalize,
            lambda: NOW,
            uuid4,
        ),
        output=io.StringIO(),
    )

    second.ready()
    second.scan_once()

    assert second_ports.requests == []
    assert _status(tmp_path / "state").code is RunnerStatusCode.SIDECAR_SUCCEEDED


def test_interrupted_finalizer_resumes_same_claimed_project(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "recording.mp4"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"raw")
    inspections = {_journal_name(SESSION_A): _ready(SESSION_A, source)}
    first, _, state, _ = _case(tmp_path, inspections, [KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        first.scan_once()
    interrupted = _session(state)
    assert interrupted.status is RunnerStatusCode.FINALIZER_RUNNING
    second_ports = FakePorts(inspections, [_success(source)])
    second = ProductRunner(
        tmp_path / "journals",
        state,
        str(tmp_path / "sources"),
        str(tmp_path / "workspace"),
        RunnerDependencies(
            second_ports.inspect,
            second_ports.ensure,
            second_ports.finalize,
            lambda: NOW,
            uuid4,
        ),
        output=io.StringIO(),
    )

    second.ready()
    second.scan_once()

    assert second_ports.requests[0].project_id == str(interrupted.project_id)
    assert _session(state).status is RunnerStatusCode.SIDECAR_SUCCEEDED


def test_finalizer_failure_does_not_block_later_recording(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    first_source = sources / "first.mp4"
    second_source = sources / "second.mp4"
    first_source.write_bytes(b"first")
    second_source.write_bytes(b"second")
    inspections = {
        _journal_name(SESSION_A): _ready(SESSION_A, first_source),
        _journal_name(SESSION_B): _ready(SESSION_B, second_source),
    }
    failure = ManualFinalizationFailed("finalizer", "E_TEST", "kontrollierter Fehler")
    runner, ports, state, _ = _case(
        tmp_path,
        inspections,
        [failure, _success(second_source)],
    )

    runner.scan_once()

    assert len(ports.requests) == 2
    assert _session(state, SESSION_A).status is RunnerStatusCode.FINALIZER_FAILED
    assert _session(state, SESSION_B).status is RunnerStatusCode.SIDECAR_SUCCEEDED


def test_foreign_claim_fails_closed(tmp_path: Path) -> None:
    source = tmp_path / "sources" / "recording.mp4"
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(b"raw")
    runner, ports, state, _ = _case(
        tmp_path,
        {_journal_name(SESSION_A): _ready(SESSION_A, source)},
    )
    claims = state / "sessions"
    claims.mkdir(parents=True, exist_ok=True)
    (claims / f"{SESSION_A}.json").write_text('{"foreign":true}', encoding="utf-8")

    runner.scan_once()

    assert ports.requests == []
    assert _status(state).error_code == "E_RUNNER_FOREIGN_STATE"


def test_windows_user_lock_allows_exactly_one_instance(tmp_path: Path) -> None:
    lock_path = tmp_path / "runner.lock"
    first = SingleInstance(lock_path)
    second = SingleInstance(lock_path)
    third = SingleInstance(lock_path)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.close()
        assert third.acquire() is True
    finally:
        first.close()
        second.close()
        third.close()
