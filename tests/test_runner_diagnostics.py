from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from matrix_auto_cutter.product_runner import (
    RunnerLogSink,
    RunnerStatus,
    RunnerStatusCode,
    main,
    request_runner_stop,
    runner_health,
    tail_runner_log,
)
from matrix_auto_cutter.review_app import LogViewerSingleInstance

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
RUNNER_ID = UUID("11111111-1111-4111-8111-111111111111")


def _status(**updates: object) -> RunnerStatus:
    values: dict[str, object] = {
        "artifact_type": "matrix_auto_cutter_product_runner_status",
        "schema_version": "1.0",
        "runner_instance_id": RUNNER_ID,
        "code": RunnerStatusCode.RUNNER_READY,
        "message_de": "Runner ist bereit.",
        "runner_ready": True,
        "updated_at": NOW,
        "runner_pid": 4242,
        "runner_started_at": NOW,
        "last_heartbeat_at": NOW,
        "last_status_code": RunnerStatusCode.RUNNER_READY,
    }
    values.update(updates)
    return RunnerStatus.model_validate(values)


def test_runner_health_distinguishes_active_dead_stale_and_failed_last_run() -> None:
    active = _status()
    assert runner_health(active, now=NOW, pid_exists=lambda pid: pid == 4242).state == "active"
    assert runner_health(active, now=NOW, pid_exists=lambda _pid: False).state == "not_reachable"
    assert (
        runner_health(active, now=NOW + timedelta(seconds=16), pid_exists=lambda _pid: True).state
        == "stale"
    )
    failed = _status(
        last_run_failed=True,
        last_error_code="E_TEST",
        last_error_message_de="Kontrollierter Fehler.",
    )
    health = runner_health(failed, now=NOW, pid_exists=lambda _pid: True)
    assert health.state == "active"
    assert health.last_run_failed is True
    incomplete = _status(
        code=RunnerStatusCode.JOURNAL_INCOMPLETE,
        last_status_code=RunnerStatusCode.JOURNAL_INCOMPLETE,
        last_error_code="E_JOURNAL_INCOMPLETE",
    )
    assert runner_health(incomplete, now=NOW, pid_exists=lambda _pid: True).last_run_failed is False


def test_runner_log_captures_stdout_stderr_structured_fields_and_rotates(
    tmp_path: Path, monkeypatch: object
) -> None:
    import matrix_auto_cutter.product_runner as runner_module

    monkeypatch.setattr(runner_module, "LOG_MAX_BYTES", 200)  # type: ignore[attr-defined]
    sink = RunnerLogSink(tmp_path / "logs")
    sink.write('stdout <tag> & "quoted"\n')
    sink.event(
        "ERROR",
        "stderr-ish message",
        status_code="proposal_failed",
        recording_id="11111111-1111-4111-8111-111111111111",
        proposal_id="proposal-test",
        render_id="render-test",
        error_code="E_TEST",
    )
    for number in range(8):
        sink.event("INFO", f"rotation-{number}-" + "x" * 120)
    sink.flush()

    generations = sorted(path.name for path in sink.directory.glob("runner.log*"))
    assert generations == [
        "runner.log",
        "runner.log.1",
        "runner.log.2",
        "runner.log.3",
        "runner.log.4",
    ]
    current = tail_runner_log(sink.directory)
    assert "rotation-7" in current
    archived = (sink.directory / "runner.log.4").read_text(encoding="utf-8")
    assert "time=" in archived and "status_code=" in archived and "message=" in archived


def test_log_viewer_lock_allows_one_window_reservation_without_gui(tmp_path: Path) -> None:
    first = LogViewerSingleInstance(tmp_path / "log-viewer.lock")
    second = LogViewerSingleInstance(tmp_path / "log-viewer.lock")
    try:
        assert first.acquire() is True
        assert second.acquire() is False
    finally:
        first.close()
        second.close()


def test_local_stop_request_needs_no_window_or_second_runner(tmp_path: Path) -> None:
    state_directory = tmp_path / "state"
    assert request_runner_stop(state_directory) is True
    assert (state_directory / "stop.request").read_bytes() == b"stop\n"
    assert main(["--state-directory", str(state_directory), "--stop"]) == 0


def test_startup_waits_for_matching_ready_pid_or_existing_runner(
    tmp_path: Path, monkeypatch: object
) -> None:
    import matrix_auto_cutter.product_startup as startup

    class FakeProcess:
        def __init__(self, pid: int, exit_code: int | None = None) -> None:
            self.pid = pid
            self.exit_code = exit_code

        def poll(self) -> int | None:
            return self.exit_code

    status = _status(runner_pid=123)
    monkeypatch.setattr(startup, "load_runner_status", lambda _state: status)  # type: ignore[attr-defined]
    assert startup._wait_for_ready(FakeProcess(123), tmp_path) is None
    assert startup._wait_for_ready(FakeProcess(999, exit_code=2), tmp_path) is None


def test_quiet_launcher_uses_pythonw_waits_for_readiness_and_never_pauses() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "src" / "matrix_auto_cutter" / "product_startup.py").read_text(
        encoding="utf-8"
    )
    command = (root / "START-MATRIX-AUTO-CUTTER.cmd").read_text(encoding="utf-8")
    review = (root / "src" / "matrix_auto_cutter" / "review_app.py").read_text(encoding="utf-8")

    assert "pythonw.exe" in launcher
    assert "CREATE_NO_WINDOW" in launcher
    assert "subprocess.Popen" in launcher and "shell=False" in launcher
    assert "runner_ready" in launcher and "runner_pid" in launcher
    assert "_show_start_error" in launcher and "RunnerLogSink" in launcher
    assert "powershell" not in command.casefold()
    assert "-WindowStyle" not in command
    assert "MATRIX_BASE_HOME" in command and "pythonw.exe" in command
    assert "PYTHONPATH" in command and "Start-Process" not in command
    assert "pause" not in command.casefold()
    assert "Protokoll anzeigen" in review
    assert "tail_runner_log" in review and "LogViewerSingleInstance" in review
