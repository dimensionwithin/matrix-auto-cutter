from __future__ import annotations

import hashlib
import io
import json
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from matrix_auto_cutter.approval import approval_path_for, record_decision
from matrix_auto_cutter.cut_proposal import (
    FfmpegProcessResult,
    ProposalFailed,
    ProposalReady,
    ProposalResult,
    generate_proposal,
)
from matrix_auto_cutter.manual_finalizer import (
    ManualFinalizationResult,
    ManualFinalizationSucceeded,
    ManualFinalizerRequest,
)
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.product_runner import (
    JOURNAL_SUFFIX,
    JournalInspection,
    JournalReady,
    ProductRunner,
    RunnerDependencies,
    RunnerStatusCode,
    SessionState,
)
from matrix_auto_cutter.render import (
    RenderAccepted,
    RenderExecution,
    RenderFailed,
    RenderRequest,
    StatusCallback,
    submit_render_request,
)
from matrix_auto_cutter.review_app import ReviewSingleInstance

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
SESSION_A = "11111111-1111-4111-8111-111111111111"
SESSION_B = "22222222-2222-4222-8222-222222222222"


class FakeProcess:
    def __init__(self) -> None:
        self.analysis_calls = 0

    def __call__(self, arguments: object, timeout: int) -> FfmpegProcessResult:
        del timeout
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version runner-test\n")
        self.analysis_calls += 1
        return FfmpegProcessResult(
            0,
            b"silence_start: 2.0\nsilence_end: 5.0 | silence_duration: 3.0\n",
        )


@dataclass
class FakeReviewProcess:
    pid: int
    exit_code: int | None = None
    terminate_calls: int = 0
    kill_calls: int = 0

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.exit_code = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.exit_code = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.exit_code is None:
            raise TimeoutError("fake process is still running")
        return self.exit_code


@dataclass
class FakeRunnerPorts:
    inspections: dict[str, JournalInspection]
    finalizer_results: list[ManualFinalizationResult]
    proposals: dict[str, ProposalResult]
    proposal_calls: list[str] = field(default_factory=list)
    review_opens: list[Path] = field(default_factory=list)
    review_processes: list[FakeReviewProcess] = field(default_factory=list)
    finalizer_calls: int = 0
    render_results: list[RenderExecution] = field(default_factory=list)
    render_calls: list[str] = field(default_factory=list)

    def inspect(self, path: Path) -> JournalInspection:
        return self.inspections[path.name]

    def ensure(self, project_id: str, workspace: str, token: CancellationToken) -> str | None:
        del project_id, workspace, token
        return None

    def finalize(self, request: ManualFinalizerRequest) -> ManualFinalizationResult:
        del request
        self.finalizer_calls += 1
        return self.finalizer_results.pop(0)

    def propose(self, source: Path, sidecar: Path, recording_id: str, root: Path) -> ProposalResult:
        del source, sidecar, root
        self.proposal_calls.append(recording_id)
        return self.proposals[recording_id]

    def open_review(self, proposal_path: Path) -> FakeReviewProcess:
        self.review_opens.append(proposal_path)
        process = FakeReviewProcess(10_000 + len(self.review_processes))
        self.review_processes.append(process)
        return process

    def render(
        self,
        proposal_path: Path,
        request: RenderRequest,
        cancellation: object,
        callback: StatusCallback | None,
    ) -> RenderExecution:
        del proposal_path, cancellation, callback
        self.render_calls.append(request.attempt_id)
        return self.render_results.pop(0)


def _materialize_proposal(
    directory: Path,
    artifacts_root: Path,
    raw: dict[str, object],
    recording_id: str,
) -> tuple[Path, Path, ProposalReady, FakeProcess]:
    directory.mkdir(parents=True)
    source = directory / f"{recording_id}.mp4"
    source.write_bytes(b"runner-read-only-source")
    source_payload = raw["source"]
    assert isinstance(source_payload, dict)
    source_payload.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    raw["recording_session_id"] = recording_id
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_text(json.dumps(raw), encoding="utf-8")
    ffmpeg = directory / "ffmpeg.exe"
    ffmpeg.write_bytes(b"runner-ffmpeg")
    process = FakeProcess()
    result = generate_proposal(
        source,
        sidecar,
        recording_id,
        artifacts_root,
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(result, ProposalReady)
    return source, sidecar, result, process


def _journal_name(recording_id: str) -> str:
    return f"{recording_id}{JOURNAL_SUFFIX}"


def _runner(
    tmp_path: Path,
    ports: FakeRunnerPorts,
    sources: Path,
) -> ProductRunner:
    journals = tmp_path / "journals"
    journals.mkdir(exist_ok=True)
    for name in ports.inspections:
        (journals / name).write_bytes(b"journal\n")
    runner = ProductRunner(
        journals,
        tmp_path / "state",
        str(sources),
        str(tmp_path / "workspace"),
        RunnerDependencies(
            ports.inspect,
            ports.ensure,
            ports.finalize,
            lambda: NOW,
            uuid4,
            ports.propose,
            ports.open_review,
            render=ports.render,
        ),
        output=io.StringIO(),
    )
    runner.ready()
    return runner


def _success(source: Path, sidecar: Path, recording_id: str) -> ManualFinalizationSucceeded:
    return ManualFinalizationSucceeded(
        str(sidecar),
        "33333333-3333-4333-8333-333333333333",
        recording_id,
        False,
    )


def _session(tmp_path: Path, recording_id: str) -> SessionState:
    path = tmp_path / "state" / "sessions" / f"{recording_id}.json"
    return SessionState.model_validate_json(path.read_bytes())


def test_runner_proposes_after_sidecar_opens_once_and_restart_reuses(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    source, sidecar, proposal, process = _materialize_proposal(
        sources, tmp_path / "state" / "artifacts", raw_sidecar, SESSION_A
    )
    inspections = {_journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source))}
    first_ports = FakeRunnerPorts(
        inspections,
        [_success(source, sidecar, SESSION_A)],
        {SESSION_A: proposal},
    )
    first = _runner(tmp_path, first_ports, sources)

    first.scan_once()
    first.scan_once()

    state = _session(tmp_path, SESSION_A)
    assert state.status is RunnerStatusCode.APPROVAL_PENDING
    assert state.proposal_path == str(proposal.proposal_path.resolve())
    assert state.review_path is not None and Path(state.review_path).is_file()
    assert len(first_ports.review_opens) == 1
    assert first_ports.proposal_calls == [SESSION_A]

    second_ports = FakeRunnerPorts(inspections, [], {SESSION_A: proposal})
    second = _runner(tmp_path, second_ports, sources)
    second.scan_once()

    assert second_ports.proposal_calls == []
    assert second_ports.review_opens == []
    assert process.analysis_calls == 1
    assert _session(tmp_path, SESSION_A).proposal_path == state.proposal_path


def test_analysis_failure_does_not_block_later_recording(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    first_raw = deepcopy(raw_sidecar)
    second_raw = deepcopy(raw_sidecar)
    source_a, sidecar_a, _proposal_a, _ = _materialize_proposal(
        sources / "a", tmp_path / "state" / "artifacts", first_raw, SESSION_A
    )
    source_b, sidecar_b, proposal_b, _ = _materialize_proposal(
        sources / "b", tmp_path / "state" / "artifacts", second_raw, SESSION_B
    )
    inspections = {
        _journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source_a)),
        _journal_name(SESSION_B): JournalReady(SESSION_B, "b" * 64, str(source_b)),
    }
    ports = FakeRunnerPorts(
        inspections,
        [
            _success(source_a, sidecar_a, SESSION_A),
            _success(source_b, sidecar_b, SESSION_B),
        ],
        {
            SESSION_A: ProposalFailed("E_TEST_ANALYSIS", "kontrollierter Analysefehler"),
            SESSION_B: proposal_b,
        },
    )
    runner = _runner(tmp_path, ports, sources)

    runner.scan_once()

    assert _session(tmp_path, SESSION_A).status is RunnerStatusCode.PROPOSAL_FAILED
    assert _session(tmp_path, SESSION_B).status is RunnerStatusCode.APPROVAL_PENDING
    assert ports.proposal_calls == [SESSION_A, SESSION_B]
    assert len(ports.review_opens) == 1


def test_unchanged_pending_polls_once_then_approved_transition_once(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    source, sidecar, proposal, _ = _materialize_proposal(
        sources, tmp_path / "state" / "artifacts", raw_sidecar, SESSION_A
    )
    ports = FakeRunnerPorts(
        {_journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source))},
        [_success(source, sidecar, SESSION_A)],
        {SESSION_A: proposal},
    )
    runner = _runner(tmp_path, ports, sources)

    runner.scan_once()
    state = _session(tmp_path, SESSION_A)
    assert state.proposal_path is not None
    proposal_path = Path(state.proposal_path)
    approval_path = approval_path_for(proposal_path)
    proposal_before = proposal_path.read_bytes()
    approval_before = approval_path.read_bytes()
    for _ in range(9):
        runner.scan_once()

    output = runner.output.getvalue()
    assert output.count("[proposal_ready]") == 1
    assert output.count("[approval_pending]") == 1
    assert ports.finalizer_calls == 1
    assert ports.proposal_calls == [SESSION_A]
    assert len(ports.review_opens) == 1
    assert proposal_path.read_bytes() == proposal_before
    assert approval_path.read_bytes() == approval_before

    record_decision(proposal_path, "approved", now=lambda: NOW)
    for _ in range(10):
        runner.scan_once()

    output = runner.output.getvalue()
    assert output.count("[proposal_approved]") == 1
    assert output.count("[approval_pending]") == 1
    assert ports.finalizer_calls == 1
    assert ports.proposal_calls == [SESSION_A]
    assert len(ports.review_opens) == 1


def test_multiple_generations_replace_global_review_and_shutdown_owned_only(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    source_a, sidecar_a, proposal_a, _ = _materialize_proposal(
        sources / "a",
        tmp_path / "state" / "artifacts",
        deepcopy(raw_sidecar),
        SESSION_A,
    )
    source_b, sidecar_b, proposal_b, _ = _materialize_proposal(
        sources / "b",
        tmp_path / "state" / "artifacts",
        deepcopy(raw_sidecar),
        SESSION_B,
    )
    ports = FakeRunnerPorts(
        {
            _journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source_a)),
            _journal_name(SESSION_B): JournalReady(SESSION_B, "b" * 64, str(source_b)),
        },
        [
            _success(source_a, sidecar_a, SESSION_A),
            _success(source_b, sidecar_b, SESSION_B),
        ],
        {SESSION_A: proposal_a, SESSION_B: proposal_b},
    )
    runner = _runner(tmp_path, ports, sources)
    foreign = FakeReviewProcess(99_999)

    runner.scan_once()

    assert len(ports.review_processes) == 2
    first, second = ports.review_processes
    assert first.poll() == 0
    assert first.terminate_calls == 1
    assert second.poll() is None
    assert runner.review_process_id == second.pid
    assert foreign.poll() is None

    runner.shutdown()

    assert second.poll() == 0
    assert second.terminate_calls == 1
    assert runner.review_process_id is None
    assert foreign.poll() is None


def test_decided_historical_proposal_is_not_reopened_after_restart(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    source, sidecar, proposal, _ = _materialize_proposal(
        sources, tmp_path / "state" / "artifacts", raw_sidecar, SESSION_A
    )
    inspections = {_journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source))}
    first_ports = FakeRunnerPorts(
        inspections,
        [_success(source, sidecar, SESSION_A)],
        {SESSION_A: proposal},
    )
    first = _runner(tmp_path, first_ports, sources)
    first.scan_once()
    state = _session(tmp_path, SESSION_A)
    assert state.proposal_path is not None
    record_decision(Path(state.proposal_path), "approved", now=lambda: NOW)
    first.scan_once()
    first.shutdown()

    second_ports = FakeRunnerPorts(inspections, [], {SESSION_A: proposal})
    second = _runner(tmp_path, second_ports, sources)
    for _ in range(10):
        second.scan_once()

    assert second_ports.proposal_calls == []
    assert second_ports.review_opens == []
    assert _session(tmp_path, SESSION_A).status is RunnerStatusCode.PROPOSAL_APPROVED


def test_review_single_instance_lock_does_not_open_gui(tmp_path: Path) -> None:
    lock = tmp_path / "review.lock"
    first = ReviewSingleInstance(lock)
    second = ReviewSingleInstance(lock)
    third = ReviewSingleInstance(lock)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.close()
        assert third.acquire() is True
    finally:
        first.close()
        second.close()
        third.close()


def test_native_review_launch_bypasses_venv_launcher_child(
    tmp_path: Path, monkeypatch: object
) -> None:
    import matrix_auto_cutter.product_runner as runner_module

    proposal = tmp_path / "cut-proposal.json"
    proposal.write_bytes(b"proposal")
    captured: dict[str, object] = {}

    class DirectProcess:
        pid = 12345

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    def fake_popen(arguments: object, **kwargs: object) -> DirectProcess:
        captured["arguments"] = arguments
        captured["kwargs"] = kwargs
        return DirectProcess()

    monkeypatch.setattr(runner_module.subprocess, "Popen", fake_popen)  # type: ignore[attr-defined]
    opened = runner_module._open_review_native(proposal)
    arguments = captured["arguments"]
    assert isinstance(arguments, list)
    expected_base = Path(str(getattr(sys, "_base_executable", sys.executable)))
    expected = expected_base.with_name("pythonw.exe")
    assert Path(arguments[0]) == expected.resolve(strict=True)
    assert arguments[1:3] == ["-m", "matrix_auto_cutter.review_app"]
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert str(Path(sys.prefix) / "Lib" / "site-packages") in kwargs["env"]["PYTHONPATH"]
    opened.terminate()
    opened.kill()


def test_render_request_runs_once_retry_is_explicit_and_failure_does_not_block_later(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    sources = tmp_path / "sources"
    source_a, sidecar_a, proposal_a, _ = _materialize_proposal(
        sources / "a", tmp_path / "state" / "artifacts", deepcopy(raw_sidecar), SESSION_A
    )
    source_b, sidecar_b, proposal_b, _ = _materialize_proposal(
        sources / "b", tmp_path / "state" / "artifacts", deepcopy(raw_sidecar), SESSION_B
    )
    ports = FakeRunnerPorts(
        {_journal_name(SESSION_A): JournalReady(SESSION_A, "a" * 64, str(source_a))},
        [_success(source_a, sidecar_a, SESSION_A)],
        {SESSION_A: proposal_a, SESSION_B: proposal_b},
        render_results=[
            RenderFailed("E_TEST_RENDER", "kontrollierter Renderfehler"),
            RenderFailed("E_TEST_RETRY", "kontrollierter Retryfehler"),
        ],
    )
    runner = _runner(tmp_path, ports, sources)
    runner.scan_once()
    state_a = _session(tmp_path, SESSION_A)
    assert state_a.proposal_path is not None
    proposal_path = Path(state_a.proposal_path)
    record_decision(proposal_path, "approved", now=lambda: NOW)
    runner.scan_once()
    first = submit_render_request(
        proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: UUID("44444444-4444-4444-8444-444444444444"),
    )
    assert isinstance(first, RenderAccepted)
    runner.scan_once()
    assert runner._render_thread is not None
    runner._render_thread.join(timeout=5)
    for _ in range(5):
        runner.scan_once()
    assert len(ports.render_calls) == 1
    assert _session(tmp_path, SESSION_A).status is RunnerStatusCode.RENDER_FAILED

    second = submit_render_request(
        proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: UUID("55555555-5555-4555-8555-555555555555"),
    )
    assert isinstance(second, RenderAccepted)
    runner.scan_once()
    assert runner._render_thread is not None
    runner._render_thread.join(timeout=5)
    assert len(ports.render_calls) == 2

    name_b = _journal_name(SESSION_B)
    ports.inspections[name_b] = JournalReady(SESSION_B, "b" * 64, str(source_b))
    ports.finalizer_results.append(_success(source_b, sidecar_b, SESSION_B))
    (runner.journal_directory / name_b).write_bytes(b"journal\n")
    runner.scan_once()
    assert _session(tmp_path, SESSION_B).status is RunnerStatusCode.APPROVAL_PENDING
