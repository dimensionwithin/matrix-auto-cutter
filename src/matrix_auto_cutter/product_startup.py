"""Quiet, local Windows startup helper for the Matrix Auto Cutter runner."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from matrix_auto_cutter.product_runner import (
    RunnerLogSink,
    default_state_directory,
    load_runner_status,
)

READY_TIMEOUT_SECONDS = 8.0


def _runner_executable() -> Path:
    """Return the direct base ``pythonw.exe`` rather than a venv launcher wrapper."""
    base = Path(str(getattr(sys, "_base_executable", sys.executable)))
    return base.with_name("pythonw.exe")


def _runner_environment(repository_root: Path) -> dict[str, str]:
    """Build the fixed import path needed by the direct base interpreter."""
    environment = os.environ.copy()
    python_paths = [
        str(repository_root / "src"),
        str(repository_root / ".venv" / "Lib" / "site-packages"),
    ]
    inherited = environment.get("PYTHONPATH")
    if inherited:
        python_paths.append(inherited)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    return environment


def _show_start_error(message: str, log_path: Path) -> None:
    """Show exactly one Unicode Windows error dialog without any shell involvement."""
    import ctypes

    ctypes.windll.user32.MessageBoxW(
        None,
        f"Matrix Auto Cutter konnte nicht betriebsbereit gestartet werden.\n\n"
        f"{message}\n\nProtokoll: {log_path}",
        "Matrix Auto Cutter - Startfehler",
        0x10,
    )


def _start_runner(repository_root: Path) -> subprocess.Popen[bytes]:
    """Start the direct hidden runner process with explicit non-shell arguments."""
    executable = _runner_executable()
    if not executable.is_file():
        raise RuntimeError(f"Python-Fensterlaufzeit fehlt: {executable}")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [str(executable), "-m", "matrix_auto_cutter.product_runner"],
        cwd=repository_root,
        env=_runner_environment(repository_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=flags,
        shell=False,
    )


def _wait_for_ready(process: subprocess.Popen[bytes], state_directory: Path) -> str | None:
    """Wait for a matching local ready status or describe the startup failure."""
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = load_runner_status(state_directory)
        if status is not None and status.runner_ready and status.runner_pid == process.pid:
            return None
        exit_code = process.poll()
        if exit_code is not None:
            if exit_code == 2:
                return None
            return f"Product Runner beendete sich mit Exitcode {exit_code}."
        time.sleep(0.15)
    return (
        "Der Runner hat innerhalb von acht Sekunden keinen gültigen Bereitschaftsstatus gemeldet."
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Start the runner quietly and report one logged Windows error on failure."""
    del argv
    try:
        state_directory = default_state_directory()
        diagnostics = RunnerLogSink(state_directory / "logs")
    except RuntimeError as exc:
        fallback = Path(os.environ.get("TEMP", ".")) / "MatrixAutoCutter-runner-fallback.log"
        _show_start_error(str(exc), fallback)
        return 1
    try:
        diagnostics.event("INFO", "Product Runner wird im Hintergrund gestartet.")
        process = _start_runner(Path(__file__).resolve().parents[2])
        failure = _wait_for_ready(process, state_directory)
        if failure is None:
            diagnostics.event("INFO", "Product Runner ist betriebsbereit.")
            return 0
        raise RuntimeError(failure)
    except Exception as exc:
        diagnostics.event(
            "ERROR",
            f"Product-Startfehler: {type(exc).__name__}: {exc}",
            status_code="runner_startup_failed",
            error_code="E_RUNNER_STARTUP",
        )
        diagnostics.flush()
        _show_start_error(str(exc), diagnostics.path)
        return 1
    finally:
        diagnostics.flush()


if __name__ == "__main__":
    raise SystemExit(main())
