from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

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
class FakeRunnerPorts:
    inspections: dict[str, JournalInspection]
    finalizer_results: list[ManualFinalizationResult]
    proposals: dict[str, ProposalResult]
    proposal_calls: list[str] = field(default_factory=list)
    review_opens: list[Path] = field(default_factory=list)

    def inspect(self, path: Path) -> JournalInspection:
        return self.inspections[path.name]

    def ensure(self, project_id: str, workspace: str, token: CancellationToken) -> str | None:
        del project_id, workspace, token
        return None

    def finalize(self, request: ManualFinalizerRequest) -> ManualFinalizationResult:
        del request
        return self.finalizer_results.pop(0)

    def propose(self, source: Path, sidecar: Path, recording_id: str, root: Path) -> ProposalResult:
        del source, sidecar, root
        self.proposal_calls.append(recording_id)
        return self.proposals[recording_id]


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
            ports.review_opens.append,
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

    assert second_ports.proposal_calls == [SESSION_A]
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
