"""Lokaler Produkt-Runner für die automatische Post-Stop-Finalisierung."""

from __future__ import annotations

import argparse
import io
import json
import msvcrt
import ntpath
import os
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Literal, Protocol, TextIO, cast
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, ValidationError

from matrix_auto_cutter.approval import (
    DecisionFailed,
    DecisionWritten,
    ensure_pending_approval,
    inspect_approval_state,
)
from matrix_auto_cutter.cut_proposal import (
    ProposalFailed,
    ProposalReady,
    ProposalResult,
    discover_ffmpeg,
    generate_proposal,
    load_proposal,
)
from matrix_auto_cutter.manual_finalizer import (
    ManualFinalizationFailed,
    ManualFinalizationResult,
    ManualFinalizerPorts,
    ManualFinalizerRequest,
    run_manual_finalizer,
)
from matrix_auto_cutter.models import CanonicalModel, CanonicalUuid4, Sha256
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.finalizer.errors import FinalizerErrorCode
from matrix_auto_cutter.phase2.finalizer.loader import (
    JournalInputPaths,
    JournalLoadFailed,
    LoadedJournal,
    load_journal,
)
from matrix_auto_cutter.phase2.finalizer.models import JournalInputProfile
from matrix_auto_cutter.phase2.pathing import PathRejected, PathRole, validate_path
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.phase2.workspace import (
    ProjectCreated,
    ProjectOpened,
    WorkspaceReady,
    create_project,
    ensure_workspace,
    open_project,
    resolve_default_workspace_root,
)
from matrix_auto_cutter.render import (
    NativeProcessRunner,
    RenderExecution,
    RenderFailed,
    RenderRequestModel,
    RenderStatus,
    RenderStatusV11,
    RenderSucceeded,
    StatusCallback,
    discover_ffprobe,
    execute_approved_render,
    load_render_request,
    render_request_path,
    write_render_status,
)
from matrix_auto_cutter.review import write_review

JOURNAL_SUFFIX = ".recording-journal.ndjson"
DEFAULT_SOURCE_ROOT = r"F:\MatrixMarketAutoEdit"
POLL_SECONDS = 2.0
STATUS_SCHEMA_VERSION: Literal["1.0"] = "1.0"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_GENERATIONS = 5
HEARTBEAT_STALE_SECONDS = 15.0


class RunnerStatusCode(StrEnum):
    """Maschinenlesbare Zustände mit stabiler Bedeutung."""

    RUNNER_STARTING = "runner_starting"
    RUNNER_READY = "runner_ready"
    JOURNAL_INCOMPLETE = "journal_incomplete"
    RECORDING_DETECTED = "recording_detected"
    FINALIZER_RUNNING = "finalizer_running"
    SIDECAR_SUCCEEDED = "sidecar_succeeded"
    ANALYSIS_PENDING = "analysis_pending"
    ANALYSIS_RUNNING = "analysis_running"
    PROPOSAL_READY = "proposal_ready"
    PROPOSAL_FAILED = "proposal_failed"
    APPROVAL_PENDING = "approval_pending"
    PROPOSAL_APPROVED = "proposal_approved"
    PROPOSAL_REJECTED = "proposal_rejected"
    RENDER_NOT_AUTHORIZED = "render_not_authorized"
    RENDER_READY = "render_ready"
    RENDER_RUNNING = "render_running"
    RENDER_VERIFYING = "render_verifying"
    RENDER_SUCCEEDED = "render_succeeded"
    RENDER_FAILED = "render_failed"
    SOURCE_OUTSIDE_ROOT = "source_outside_root"
    SOURCE_MISSING = "source_missing"
    JOURNAL_INVALID = "journal_invalid"
    STOP_NOT_FINALIZABLE = "stop_not_finalizable"
    FFPROBE_UNAVAILABLE = "ffprobe_unavailable"
    FFPROBE_UNTRUSTED = "ffprobe_untrusted"
    FINALIZER_FAILED = "finalizer_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    RUNNER_STOPPED = "runner_stopped"


RUNNER_FAILURE_CODES = frozenset(
    {
        RunnerStatusCode.PROPOSAL_FAILED,
        RunnerStatusCode.RENDER_FAILED,
        RunnerStatusCode.FINALIZER_FAILED,
        RunnerStatusCode.INFRASTRUCTURE_ERROR,
    }
)


class RunnerStatus(CanonicalModel):
    """Persistierte Betriebsansicht des zuletzt beobachteten Ereignisses."""

    artifact_type: Literal["matrix_auto_cutter_product_runner_status"]
    schema_version: Literal["1.0"]
    runner_instance_id: CanonicalUuid4
    code: RunnerStatusCode
    message_de: str = Field(min_length=1, max_length=2000)
    runner_ready: bool
    updated_at: AwareDatetime
    runner_pid: int = Field(default_factory=os.getpid, ge=1)
    runner_started_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    last_status_code: RunnerStatusCode = RunnerStatusCode.RUNNER_STARTING
    last_run_failed: bool = False
    last_error_code: str | None = None
    last_error_message_de: str | None = Field(default=None, max_length=2000)
    recording_session_id: CanonicalUuid4 | None = None
    journal_path: str | None = None
    source_path: str | None = None
    sidecar_path: str | None = None
    proposal_path: str | None = None
    review_path: str | None = None
    approval_decision: (
        Literal["pending", "approved", "rejected", "selected_cuts_approved", "all_rejected"]
        | None
    ) = None
    error_code: str | None = None
    render_id: str | None = Field(default=None, max_length=100)


class SessionState(CanonicalModel):
    """Atomarer Claim und Wiederaufnahmestand genau einer Recording-Session."""

    artifact_type: Literal["matrix_auto_cutter_product_runner_session"]
    schema_version: Literal["1.0"]
    recording_session_id: CanonicalUuid4
    journal_path: str = Field(min_length=1)
    journal_sha256: Sha256
    source_path: str = Field(min_length=1)
    project_id: CanonicalUuid4
    status: RunnerStatusCode
    message_de: str = Field(min_length=1, max_length=2000)
    attempt: int = Field(ge=0, le=1000)
    updated_at: AwareDatetime
    sidecar_path: str | None = None
    proposal_path: str | None = None
    review_path: str | None = None
    approval_decision: (
        Literal["pending", "approved", "rejected", "selected_cuts_approved", "all_rejected"]
        | None
    ) = None
    review_opened_proposal_id: str | None = Field(default=None, max_length=100)
    render_attempt_id: str | None = Field(default=None, max_length=100)
    render_id: str | None = Field(default=None, max_length=100)
    render_target_path: str | None = None
    render_result_path: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class JournalReady:
    """Vollständiges validiertes Journal mit normativer Source-Zuordnung."""

    recording_session_id: str
    journal_sha256: str
    source_path: str


@dataclass(frozen=True, slots=True)
class JournalUnavailable:
    """Noch nicht oder dauerhaft nicht übernehmbares Journal."""

    code: RunnerStatusCode
    error_code: str
    message_de: str
    recording_session_id: str | None = None


type JournalInspection = JournalReady | JournalUnavailable
type JournalInspector = Callable[[Path], JournalInspection]
type ProjectEnsurer = Callable[[str, str, CancellationToken], str | None]
type FinalizerRunner = Callable[[ManualFinalizerRequest], ManualFinalizationResult]
type ProposalRunner = Callable[[Path, Path, str, Path], ProposalResult]
type RenderRunner = Callable[
    [Path, RenderRequestModel, threading.Event, StatusCallback | None], RenderExecution
]


class ReviewProcess(Protocol):
    """Small process handle retained by the runner for one review application."""

    @property
    def pid(self) -> int:
        """Return the owned process identifier."""
        ...

    def poll(self) -> int | None:
        """Return None while the review is alive."""
        ...

    def terminate(self) -> None:
        """Request controlled process-tree termination."""
        ...

    def kill(self) -> None:
        """Force process-tree termination after timeout."""
        ...

    def wait(self, timeout: float | None = None) -> int:
        """Wait for termination and return the exit code."""
        ...


type ReviewOpener = Callable[[Path], ReviewProcess]


@dataclass(frozen=True, slots=True)
class RunnerDependencies:
    """Schmale Ports für Parser, Projektanlage und bestehenden Finalizer."""

    inspect_journal: JournalInspector
    ensure_project: ProjectEnsurer
    finalize: FinalizerRunner
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    uuid_factory: Callable[[], UUID] = uuid4
    propose: ProposalRunner | None = None
    open_review: ReviewOpener | None = None
    render: RenderRunner | None = None
    cancel_render: Callable[[], None] | None = None


def default_journal_directory() -> Path:
    """Liefere die normative Producer-Journalablage des aktuellen Benutzers."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA ist nicht gesetzt")
    return Path(local) / "DimensionWithin" / "MatrixAutoCutter" / "producer" / "journals"


def default_state_directory() -> Path:
    """Liefere die lokale persistente Runner-Statusablage."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA ist nicht gesetzt")
    return Path(local) / "DimensionWithin" / "MatrixAutoCutter" / "product-runner"


def default_log_directory() -> Path:
    """Liefere die feste, lokale und nur diagnostische Runner-Logablage."""
    return default_state_directory() / "logs"


class RunnerLogSink(io.TextIOBase):
    """Schreibe Runner-Diagnosen rotationsbegrenzt mit einem kleinen Fallback."""

    def __init__(self, directory: Path) -> None:
        """Initialisiere den nur lokalen Haupt- und Fallbackpfad."""
        self.directory = directory
        self.path = directory / "runner.log"
        self._fallback_path = Path(tempfile.gettempdir()) / "MatrixAutoCutter-runner-fallback.log"
        self._buffer = ""
        self._lock = threading.Lock()

    def writable(self) -> bool:
        """Melde die Textstream-Faehigkeit fuer print und Bibliotheksausgaben."""
        return True

    def write(self, text: str) -> int:
        """Nimm stdout/stderr ohne Verlust einzelner Zeilen in das Diagnoseprotokoll auf."""
        if not text:
            return 0
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line:
                    self._write_record("INFO", "stdout", line)
        return len(text)

    def flush(self) -> None:
        """Schreibe einen noch nicht zeilenweise abgeschlossenen Rest sicher weg."""
        with self._lock:
            if self._buffer:
                self._write_record("INFO", "stdout", self._buffer)
                self._buffer = ""

    def event(
        self,
        level: str,
        message: str,
        *,
        status_code: str | None = None,
        recording_id: str | None = None,
        proposal_id: str | None = None,
        render_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Schreibe eine strukturierte fachliche Runner-Meldung."""
        with self._lock:
            self._write_record(
                level,
                status_code or "diagnostic",
                message,
                recording_id=recording_id,
                proposal_id=proposal_id,
                render_id=render_id,
                error_code=error_code,
            )

    def status(self, status: RunnerStatus) -> None:
        """Protokolliere eine persistierte Statusaenderung samt vorhandener Kennungen."""
        proposal_id = None
        if status.proposal_path:
            parent = Path(status.proposal_path).parent.name
            proposal_id = parent.removeprefix("proposal-") or None
        self.event(
            "ERROR" if status.error_code else "INFO",
            status.message_de,
            status_code=status.code.value,
            recording_id=(
                str(status.recording_session_id) if status.recording_session_id else None
            ),
            proposal_id=proposal_id,
            render_id=status.render_id,
            error_code=status.error_code,
        )

    def _write_record(
        self,
        level: str,
        event: str,
        message: str,
        *,
        recording_id: str | None = None,
        proposal_id: str | None = None,
        render_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        timestamp = datetime.now(UTC).astimezone().isoformat(timespec="seconds")
        fields: dict[str, str | None] = {
            "time": timestamp,
            "level": level,
            "status_code": event,
            "recording_id": recording_id,
            "proposal_id": proposal_id,
            "render_id": render_id,
            "error_code": error_code,
            "message": message,
        }
        line = " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()
        )
        try:
            self._rotate_if_needed(len(line.encode("utf-8")) + 1)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except (OSError, UnicodeError):
            self._write_fallback(line)

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size + incoming_bytes <= LOG_MAX_BYTES:
            return
        for index in range(LOG_GENERATIONS - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if index == LOG_GENERATIONS - 1:
                with suppress(FileNotFoundError):
                    source.unlink()
            elif source.exists():
                os.replace(source, target)
        os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def _write_fallback(self, line: str) -> None:
        """Erhalte zumindest eine begrenzte lokale Fehlerspur, wenn das Hauptlog ausfaellt."""
        try:
            if self._fallback_path.exists() and self._fallback_path.stat().st_size >= 1024 * 1024:
                return
            with self._fallback_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
        except (OSError, UnicodeError):
            with suppress(OSError):
                os.write(2, (line + "\n").encode("utf-8", errors="replace"))


@dataclass(frozen=True, slots=True)
class RunnerHealth:
    """Sichere UI-Projektion der lokalen Runner-Statusdatei."""

    state: Literal["active", "starting", "not_reachable", "stale"]
    message_de: str
    last_run_failed: bool


def load_runner_status(state_directory: Path | None = None) -> RunnerStatus | None:
    """Lade ausschliesslich die kleine, lokale Statusdatei ohne Fehlerweitergabe."""
    path = (state_directory or default_state_directory()) / "status.json"
    try:
        return RunnerStatus.model_validate_json(path.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def runner_health(
    status: RunnerStatus | None,
    *,
    now: datetime | None = None,
    pid_exists: Callable[[int], bool] = _pid_exists,
) -> RunnerHealth:
    """Erkenne aktive, anlaufende, tote und veraltete Runner ohne Netzwerkdienst."""
    if status is None:
        return RunnerHealth(
            "not_reachable", "Runner ist nicht erreichbar; keine Statusdatei vorhanden.", False
        )
    failed = status.last_run_failed
    if status.code is RunnerStatusCode.RUNNER_STOPPED:
        return RunnerHealth("not_reachable", "Runner wurde kontrolliert beendet.", failed)
    if not status.runner_ready:
        return RunnerHealth("starting", "Runner startet noch.", failed)
    observed_at = now or datetime.now(UTC)
    age_seconds = (observed_at - status.last_heartbeat_at).total_seconds()
    if age_seconds > HEARTBEAT_STALE_SECONDS:
        return RunnerHealth("stale", "Runner-Status ist veraltet.", failed)
    if not pid_exists(status.runner_pid):
        return RunnerHealth("not_reachable", "Runner-Prozess ist nicht mehr vorhanden.", failed)
    message = "Runner ist aktiv."
    if failed:
        message += " Der letzte fachliche Lauf ist fehlgeschlagen."
    return RunnerHealth("active", message, failed)


def tail_runner_log(log_directory: Path | None = None, *, maximum_bytes: int = 192 * 1024) -> str:
    """Lese einen begrenzten, lokal validierten Log-Ausschnitt ohne Shell-Auswertung."""
    if not 1024 <= maximum_bytes <= 1024 * 1024:
        raise ValueError("maximum_bytes muss zwischen 1024 und 1048576 liegen")
    directory = (log_directory or default_log_directory()).resolve(strict=False)
    path = (directory / "runner.log").resolve(strict=False)
    try:
        path.relative_to(directory)
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - maximum_bytes), os.SEEK_SET)
            data = stream.read(maximum_bytes)
    except OSError as exc:
        return f"Protokoll ist derzeit nicht lesbar: {type(exc).__name__}: {exc}"
    text = data.decode("utf-8", errors="replace")
    if size > maximum_bytes:
        text = "… (ältere Zeilen ausgelassen)\n" + text
    return text


def _canonical_path(path: Path) -> str:
    return str(path.resolve(strict=False))


def _session_id_from_name(path: Path) -> str | None:
    if not path.name.endswith(JOURNAL_SUFFIX):
        return None
    candidate = path.name[: -len(JOURNAL_SUFFIX)]
    try:
        parsed = UUID(candidate)
    except ValueError:
        return None
    return candidate if parsed.version == 4 and str(parsed) == candidate else None


def inspect_journal_native(path: Path, ports: ManualFinalizerPorts) -> JournalInspection:
    """Lade ein Journal ausschließlich über den bestehenden sicheren Loader."""
    expected = _session_id_from_name(path)
    if expected is None:
        return JournalUnavailable(
            RunnerStatusCode.JOURNAL_INVALID,
            "E_JOURNAL_FILENAME",
            "Journalname enthält keine kanonische Recording-Session-ID.",
        )
    validated = validate_path(
        ports.win32,
        _canonical_path(path),
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(validated, PathRejected):
        return JournalUnavailable(
            RunnerStatusCode.INFRASTRUCTURE_ERROR,
            str(validated.error.code),
            f"Journal konnte nicht sicher geöffnet werden: {validated.error.message}",
            expected,
        )
    loaded = load_journal(
        cast(Win32Port, ports.win32),
        JournalInputProfile.LEGACY,
        JournalInputPaths(validated.path),
        expected_recording_id=expected,
    )
    if isinstance(loaded, JournalLoadFailed):
        code = loaded.error.code
        if code is FinalizerErrorCode.JOURNAL_INCOMPLETE:
            status = RunnerStatusCode.JOURNAL_INCOMPLETE
            message = "Journal ist noch nicht vollständig und wird noch nicht finalisiert."
        elif code is FinalizerErrorCode.JOURNAL_OUTPUT_FAILURE:
            status = RunnerStatusCode.STOP_NOT_FINALIZABLE
            message = "Stop war nicht erfolgreich oder die Aufnahme ist nicht finalisierbar."
        else:
            status = RunnerStatusCode.JOURNAL_INVALID
            message = f"Journal ist ungültig: {loaded.error.message}"
        return JournalUnavailable(status, str(code), message, expected)
    assert isinstance(loaded, LoadedJournal)
    stop = loaded.records[-1]
    source = stop.get("last_recording_path")
    if not isinstance(source, str) or not source:
        return JournalUnavailable(
            RunnerStatusCode.JOURNAL_INVALID,
            "E_JOURNAL_SOURCE_PATH",
            "Validiertes Journal enthält keinen Aufnahme-Endpfad.",
            expected,
        )
    return JournalReady(loaded.recording_id, loaded.sha256, source)


def _ensure_project_native(
    ports: ManualFinalizerPorts,
    workspace_path: str,
    project_id: str,
    cancellation: CancellationToken,
) -> str | None:
    workspace = ensure_workspace(ports.win32, workspace_path)
    if not isinstance(workspace, WorkspaceReady):
        return f"Workspace konnte nicht geöffnet werden: {workspace.error.message}"
    opened = open_project(ports.win32, workspace, project_id)
    if isinstance(opened, ProjectOpened):
        return None
    created = create_project(
        ports.win32,
        workspace,
        cancellation,
        uuid_factory=lambda: UUID(project_id),
    )
    if isinstance(created, ProjectCreated):
        return None
    opened = open_project(ports.win32, workspace, project_id)
    if isinstance(opened, ProjectOpened):
        return None
    error = getattr(created, "error", created)
    return f"Projekt {project_id} konnte nicht angelegt werden: {getattr(error, 'message', error)}"


@dataclass(slots=True)
class NativeReviewProcess:
    """Own one directly launched Windows review process."""

    process: subprocess.Popen[bytes]

    @property
    def pid(self) -> int:
        """Return the root PID of the review process tree."""
        return self.process.pid

    def poll(self) -> int | None:
        """Return the root process exit state."""
        return self.process.poll()

    def terminate(self) -> None:
        """Request termination of the exact directly owned process."""
        self.process.terminate()

    def kill(self) -> None:
        """Force termination of the exact directly owned process."""
        self.process.kill()

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the root process after terminating its tree."""
        return self.process.wait(timeout=timeout)


def _open_review_native(proposal_path: Path) -> ReviewProcess:
    """Launch one directly owned local review process without a launcher child."""
    base_python = Path(str(getattr(sys, "_base_executable", sys.executable)))
    base_pythonw = base_python.with_name("pythonw.exe")
    executable = base_pythonw if base_pythonw.is_file() else base_python
    environment = os.environ.copy()
    import_root = Path(__file__).resolve(strict=True).parents[1]
    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    inherited_pythonpath = environment.get("PYTHONPATH")
    python_paths = [str(import_root), str(site_packages)]
    if inherited_pythonpath:
        python_paths.append(inherited_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    creation_flags = 0
    if os.name == "nt":
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(
        [
            str(executable.resolve(strict=True)),
            "-m",
            "matrix_auto_cutter.review_app",
            "--proposal",
            str(proposal_path.resolve(strict=True)),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        env=environment,
        shell=False,
    )
    return NativeReviewProcess(process)


def native_dependencies(
    workspace_path: str,
    ffprobe_path: str | None,
    ffmpeg_path: str | None = None,
) -> RunnerDependencies:
    """Verdrahte den Runner mit den bestehenden nativen Produktkompositionen."""
    ports = ManualFinalizerPorts.native()

    def inspect(path: Path) -> JournalInspection:
        return inspect_journal_native(path, ports)

    def ensure(project_id: str, workspace: str, token: CancellationToken) -> str | None:
        return _ensure_project_native(ports, workspace, project_id, token)

    def finalize_existing(request: ManualFinalizerRequest) -> ManualFinalizationResult:
        return run_manual_finalizer(ports, replace(request, ffprobe_path=ffprobe_path))

    resolved_ffmpeg = discover_ffmpeg(ffmpeg_path)
    media_processes = NativeProcessRunner()

    def propose(
        source_path: Path,
        sidecar_path: Path,
        recording_id: str,
        artifacts_root: Path,
    ) -> ProposalResult:
        if resolved_ffmpeg is None:
            return ProposalFailed(
                "E_FFMPEG_UNAVAILABLE",
                "FFmpeg.exe wurde nicht als absolute reguläre Datei gefunden.",
            )
        return generate_proposal(
            source_path,
            sidecar_path,
            recording_id,
            artifacts_root,
            resolved_ffmpeg,
        )

    def render(
        proposal_path: Path,
        request: RenderRequestModel,
        cancellation: threading.Event,
        callback: StatusCallback | None,
    ) -> RenderExecution:
        if resolved_ffmpeg is None:
            return RenderFailed("E_FFMPEG_UNAVAILABLE", "FFmpeg.exe wurde nicht gefunden.")
        resolved_ffprobe = discover_ffprobe(resolved_ffmpeg, ffprobe_path)
        if resolved_ffprobe is None:
            return RenderFailed("E_FFPROBE_UNAVAILABLE", "FFprobe.exe wurde nicht gefunden.")
        return execute_approved_render(
            proposal_path,
            request,
            resolved_ffmpeg,
            resolved_ffprobe,
            process_runner=media_processes,
            cancellation=cancellation,
            status_callback=callback,
        )

    return RunnerDependencies(
        inspect,
        ensure,
        finalize_existing,
        propose=propose,
        open_review=_open_review_native,
        render=render,
        cancel_render=media_processes.cancel,
    )


def _atomic_bytes(path: Path, data: bytes, *, create_only: bool) -> bool:
    """Schreibe im selben Verzeichnis per Flush und atomarem Replace/Create."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            try:
                os.rename(temporary, path)
            except FileExistsError:
                return False
        else:
            os.replace(temporary, path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return True


def request_runner_stop(state_directory: Path | None = None) -> bool:
    """Fordere einen kontrollierten Stop ueber die feste lokale State-Ablage an."""
    directory = state_directory or default_state_directory()
    return _atomic_bytes(directory / "stop.request", b"stop\n", create_only=False)


def _model_bytes(model: CanonicalModel) -> bytes:
    return model.model_dump_json(indent=2).encode("utf-8") + b"\n"


def _read_session(path: Path) -> SessionState | None:
    try:
        data = path.read_bytes()
        if len(data) > 64 * 1024:
            return None
        return SessionState.model_validate_json(data)
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None


def _normalize_source(path: str) -> str | None:
    normalized = ntpath.normpath(path.replace("/", "\\"))
    if not ntpath.isabs(normalized) or normalized.startswith("\\\\"):
        return None
    return normalized


def _under_root(path: str, root: str) -> bool:
    try:
        return ntpath.commonpath((path, root)).casefold() == root.casefold()
    except ValueError:
        return False


class ProductRunner:
    """Sequenzieller, restart-idempotenter Intake für fertige Journale."""

    def __init__(
        self,
        journal_directory: Path,
        state_directory: Path,
        source_root: str,
        workspace_path: str,
        dependencies: RunnerDependencies,
        *,
        output: TextIO = sys.stdout,
        diagnostics: RunnerLogSink | None = None,
        runner_pid: int | None = None,
    ) -> None:
        """Binde Verzeichnisse, Produktgrenze und kontrollierbare Abhängigkeiten."""
        self.journal_directory = journal_directory
        self.state_directory = state_directory
        normalized_root = _normalize_source(source_root)
        if normalized_root is None or ntpath.splitdrive(normalized_root)[1] == "\\":
            raise ValueError(
                "source_root muss ein absolutes, nicht laufwerksweites Verzeichnis sein"
            )
        self.source_root = normalized_root.rstrip("\\")
        self._resolved_source_root = str(Path(self.source_root).resolve(strict=False))
        self.workspace_path = workspace_path
        self.dependencies = dependencies
        self.output = output
        self.diagnostics = diagnostics
        self.runner_pid = runner_pid or os.getpid()
        self.runner_started_at = dependencies.now()
        self.instance_id = dependencies.uuid_factory()
        if self.instance_id.version != 4:
            raise ValueError("runner instance ID must be UUIDv4")
        self._attempted_this_run: set[str] = set()
        self._proposal_attempted_this_run: set[str] = set()
        self._last_status_by_subject: dict[str, tuple[object, ...]] = {}
        self._review_process: ReviewProcess | None = None
        self._review_proposal_id: str | None = None
        self._render_thread: threading.Thread | None = None
        self._render_cancel = threading.Event()
        self._render_recording_id: str | None = None
        self._shutdown = False
        self._last_status: RunnerStatus | None = None
        self._last_error_code: str | None = None
        self._last_error_message_de: str | None = None

    @property
    def status_path(self) -> Path:
        """Liefere den Pfad der aktuellen maschinenlesbaren Betriebsansicht."""
        return self.state_directory / "status.json"

    @property
    def stop_request_path(self) -> Path:
        """Liefere den festen lokalen Request fuer einen kontrollierten Runner-Stop."""
        return self.state_directory / "stop.request"

    @property
    def sessions_directory(self) -> Path:
        """Liefere die Ablage der dauerhaften Session-Claims."""
        return self.state_directory / "sessions"

    @property
    def artifacts_directory(self) -> Path:
        """Liefere die kontrollierte generationsgebundene Runner-Artefaktablage."""
        return self.state_directory / "artifacts"

    def _session_path(self, recording_id: str) -> Path:
        return self.sessions_directory / f"{recording_id}.json"

    @property
    def review_process_id(self) -> int | None:
        """Return the PID of the one live review process owned by this runner."""
        process = self._review_process
        if process is None or process.poll() is not None:
            return None
        return process.pid

    def _stop_review_process(self) -> None:
        """Stop only the review process tree launched and retained by this runner."""
        process = self._review_process
        self._review_process = None
        self._review_proposal_id = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            with suppress(OSError, subprocess.SubprocessError):
                process.kill()
            with suppress(OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                process.wait(timeout=5)

    def _replace_review_process(self, proposal_path: Path, proposal_id: str) -> None:
        """Replace the globally single runner-owned review with one new generation."""
        process = self._review_process
        if (
            process is not None
            and process.poll() is None
            and self._review_proposal_id == proposal_id
        ):
            return
        self._stop_review_process()
        if self.dependencies.open_review is None:
            return
        opened = self.dependencies.open_review(proposal_path)
        if opened.poll() is not None:
            return
        self._review_process = opened
        self._review_proposal_id = proposal_id

    def shutdown(self) -> None:
        """Stop owned review/render children and publish one controlled shutdown."""
        if self._shutdown:
            return
        self._shutdown = True
        self._stop_review_process()
        self._render_cancel.set()
        if self.dependencies.cancel_render is not None:
            self.dependencies.cancel_render()
        thread = self._render_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=15)
        self._publish_status(
            RunnerStatusCode.RUNNER_STOPPED,
            "Runner wurde sauber beendet.",
            ready=False,
        )
        with suppress(OSError):
            self.stop_request_path.unlink(missing_ok=True)

    def starting(self) -> None:
        """Veröffentliche vor der Bereitschaft einen kurzen, stillen Anlaufzustand."""
        self.state_directory.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.stop_request_path.unlink(missing_ok=True)
        self._publish_status(
            RunnerStatusCode.RUNNER_STARTING,
            "Runner startet.",
            ready=False,
        )

    def heartbeat(self) -> None:
        """Aktualisiere die lokale Lebensmarke ohne sichtbares Protokollrauschen."""
        status = self._last_status
        if status is None:
            return
        refreshed = status.model_copy(update={"last_heartbeat_at": self.dependencies.now()})
        self._last_status = refreshed
        _atomic_bytes(self.status_path, _model_bytes(refreshed), create_only=False)

    def stop_requested(self) -> bool:
        """Pruefe den lokalen Stop-Request ohne Netzwerk oder Prozessmanipulation."""
        try:
            return self.stop_request_path.is_file()
        except OSError:
            return False

    def _on_render_status(
        self, state: SessionState, status: RenderStatus | RenderStatusV11
    ) -> None:
        """Publish only actual renderer transitions to the shared runner status."""
        self._publish_status(
            RunnerStatusCode(status.state),
            status.message_de,
            recording_id=str(state.recording_session_id),
            journal_path=state.journal_path,
            source_path=state.source_path,
            sidecar_path=state.sidecar_path,
            proposal_path=state.proposal_path,
            review_path=state.review_path,
            approval_decision=state.approval_decision,
            error_code=status.error_code,
            render_id=status.render_id,
        )

    def _render_worker(
        self,
        state: SessionState,
        proposal_path: Path,
        request: RenderRequestModel,
    ) -> None:
        """Execute one request and persist its terminal session binding."""
        assert self.dependencies.render is not None
        try:
            outcome = self.dependencies.render(
                proposal_path,
                request,
                self._render_cancel,
                lambda status: self._on_render_status(state, status),
            )
        except Exception as exc:
            outcome = RenderFailed(
                "E_RENDER_EXCEPTION",
                f"Unerwarteter Render-Infrastrukturfehler: {type(exc).__name__}: {exc}",
            )
        current = _read_session(self._session_path(str(state.recording_session_id))) or state
        if isinstance(outcome, RenderSucceeded):
            updated = current.model_copy(
                update={
                    "status": RunnerStatusCode.RENDER_SUCCEEDED,
                    "message_de": outcome.result.message_de,
                    "render_attempt_id": request.attempt_id,
                    "render_id": outcome.result.render_id,
                    "render_target_path": outcome.result.target_path,
                    "render_result_path": str(outcome.result_path),
                    "error_code": None,
                    "updated_at": self.dependencies.now(),
                }
            )
        else:
            updated = current.model_copy(
                update={
                    "status": RunnerStatusCode.RENDER_FAILED,
                    "message_de": outcome.message_de,
                    "render_attempt_id": request.attempt_id,
                    "render_id": outcome.result.render_id if outcome.result is not None else None,
                    "render_target_path": request.target_path,
                    "render_result_path": (
                        str(outcome.result_path) if outcome.result_path is not None else None
                    ),
                    "error_code": outcome.code,
                    "updated_at": self.dependencies.now(),
                }
            )
        self._store_session(updated)
        self._publish_status(
            updated.status,
            updated.message_de,
            recording_id=str(updated.recording_session_id),
            journal_path=updated.journal_path,
            source_path=updated.source_path,
            sidecar_path=updated.sidecar_path,
            proposal_path=updated.proposal_path,
            review_path=updated.review_path,
            approval_decision=updated.approval_decision,
            error_code=updated.error_code,
            render_id=updated.render_id,
        )
        self._render_recording_id = None

    def _start_render(
        self,
        state: SessionState,
        proposal_path: Path,
        request: RenderRequestModel,
    ) -> None:
        """Claim and start exactly one background render worker."""
        thread = self._render_thread
        if thread is not None and thread.is_alive():
            return
        self._render_cancel = threading.Event()
        self._render_recording_id = str(state.recording_session_id)
        running = state.model_copy(
            update={
                "status": RunnerStatusCode.RENDER_RUNNING,
                "message_de": "Expliziter Renderauftrag wurde angenommen.",
                "render_attempt_id": request.attempt_id,
                "render_target_path": request.target_path,
                "render_result_path": None,
                "error_code": None,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(running)
        thread = threading.Thread(
            target=self._render_worker,
            args=(running, proposal_path, request),
            name=f"matrix-render-{request.attempt_id[-8:]}",
            daemon=False,
        )
        self._render_thread = thread
        thread.start()

    def _handle_render_request(
        self,
        state: SessionState,
        ready: ProposalReady,
        *,
        authorized: bool,
        reason: str,
    ) -> None:
        """Observe one proposal-local request and never render implicitly."""
        request_path = render_request_path(ready.proposal_path)
        request = load_render_request(ready.proposal_path)
        if request is None:
            render_state: Literal["render_not_authorized", "render_ready", "render_failed"]
            error_code: str | None = None
            message = reason
            if request_path.exists():
                render_state = "render_failed"
                message = "Renderauftrag ist beschädigt oder hat ein unbekanntes Schema."
                error_code = "E_RENDER_REQUEST_INVALID"
            elif authorized:
                render_state = "render_ready"
                message = "Freigabe gültig; Render wartet auf die bewusste Benutzeraktion."
            else:
                render_state = "render_not_authorized"
            write_render_status(
                ready.proposal_path,
                RenderStatus(
                    artifact_type="matrix_auto_cutter_render_status",
                    schema_version="1.0",
                    proposal_id=ready.proposal.proposal_id,
                    state=render_state,
                    message_de=message,
                    updated_at=self.dependencies.now(),
                    error_code=error_code,
                ),
            )
            return
        if not authorized:
            write_render_status(
                ready.proposal_path,
                RenderStatus(
                    artifact_type="matrix_auto_cutter_render_status",
                    schema_version="1.0",
                    proposal_id=ready.proposal.proposal_id,
                    state="render_not_authorized",
                    message_de=reason,
                    updated_at=self.dependencies.now(),
                    attempt_id=request.attempt_id,
                    target_path=request.target_path,
                    error_code="E_RENDER_NOT_AUTHORIZED",
                ),
            )
            return
        if state.render_attempt_id == request.attempt_id:
            active = self._render_thread
            if state.status in {
                RunnerStatusCode.RENDER_RUNNING,
                RunnerStatusCode.RENDER_VERIFYING,
            } and (active is None or not active.is_alive()):
                interrupted = state.model_copy(
                    update={
                        "status": RunnerStatusCode.RENDER_FAILED,
                        "message_de": (
                            "Früherer Renderlauf besitzt nach Runnerneustart keinen Prozess; "
                            "manueller Retry ist möglich."
                        ),
                        "error_code": "E_RENDER_INTERRUPTED",
                        "updated_at": self.dependencies.now(),
                    }
                )
                self._store_session(interrupted)
                write_render_status(
                    ready.proposal_path,
                    RenderStatus(
                        artifact_type="matrix_auto_cutter_render_status",
                        schema_version="1.0",
                        proposal_id=ready.proposal.proposal_id,
                        state="render_failed",
                        message_de=interrupted.message_de,
                        updated_at=self.dependencies.now(),
                        attempt_id=request.attempt_id,
                        target_path=request.target_path,
                        error_code=interrupted.error_code,
                    ),
                )
            return
        active = self._render_thread
        if active is not None and active.is_alive():
            return
        if self.dependencies.render is not None:
            self._start_render(state, ready.proposal_path, request)

    def _publish_status(
        self,
        code: RunnerStatusCode,
        message: str,
        *,
        recording_id: str | None = None,
        journal_path: str | None = None,
        source_path: str | None = None,
        sidecar_path: str | None = None,
        proposal_path: str | None = None,
        review_path: str | None = None,
        approval_decision: (
            Literal["pending", "approved", "rejected", "selected_cuts_approved", "all_rejected"]
            | None
        ) = None,
        error_code: str | None = None,
        render_id: str | None = None,
        ready: bool = True,
    ) -> None:
        subject = recording_id or journal_path or "__runner__"
        key = (
            code,
            journal_path,
            source_path,
            sidecar_path,
            proposal_path,
            review_path,
            approval_decision,
            error_code,
            render_id,
            message,
            ready,
        )
        if self._last_status_by_subject.get(subject) == key:
            return
        self._last_status_by_subject[subject] = key
        if error_code is not None:
            self._last_error_code = error_code
            self._last_error_message_de = message
        now = self.dependencies.now()
        status = RunnerStatus(
            artifact_type="matrix_auto_cutter_product_runner_status",
            schema_version=STATUS_SCHEMA_VERSION,
            runner_instance_id=self.instance_id,
            code=code,
            message_de=message,
            runner_ready=ready,
            updated_at=now,
            runner_pid=self.runner_pid,
            runner_started_at=self.runner_started_at,
            last_heartbeat_at=now,
            last_status_code=code,
            last_run_failed=code in RUNNER_FAILURE_CODES,
            last_error_code=self._last_error_code,
            last_error_message_de=self._last_error_message_de,
            recording_session_id=UUID(recording_id) if recording_id is not None else None,
            journal_path=journal_path,
            source_path=source_path,
            sidecar_path=sidecar_path,
            proposal_path=proposal_path,
            review_path=review_path,
            approval_decision=approval_decision,
            error_code=error_code,
            render_id=render_id,
        )
        self._last_status = status
        _atomic_bytes(self.status_path, _model_bytes(status), create_only=False)
        if self.diagnostics is not None:
            self.diagnostics.status(status)
        timestamp = status.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] [{code.value}] {message}", file=self.output, flush=True)

    def ready(self) -> None:
        """Veröffentliche den sichtbaren Bereitschaftszustand."""
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.sessions_directory.mkdir(parents=True, exist_ok=True)
        self.artifacts_directory.mkdir(parents=True, exist_ok=True)
        self.journal_directory.mkdir(parents=True, exist_ok=True)
        self._publish_status(
            RunnerStatusCode.RUNNER_READY,
            f"Runner ist bereit und beobachtet {self.journal_directory}.",
        )

    def _state_for(self, ready: JournalReady, journal_path: str) -> SessionState | None:
        state_path = self._session_path(ready.recording_session_id)
        existing = _read_session(state_path) if state_path.exists() else None
        if state_path.exists() and existing is None:
            self._publish_status(
                RunnerStatusCode.INFRASTRUCTURE_ERROR,
                "Widersprüchlicher oder fremder Session-Status; Verarbeitung verweigert.",
                recording_id=ready.recording_session_id,
                journal_path=journal_path,
                source_path=ready.source_path,
                error_code="E_RUNNER_FOREIGN_STATE",
            )
            return None
        if existing is not None:
            matches = (
                str(existing.recording_session_id) == ready.recording_session_id
                and existing.journal_path.casefold() == journal_path.casefold()
                and existing.journal_sha256 == ready.journal_sha256
                and existing.source_path.casefold() == ready.source_path.casefold()
            )
            if not matches:
                self._publish_status(
                    RunnerStatusCode.INFRASTRUCTURE_ERROR,
                    "Session-Claim widerspricht Journal oder Aufnahme; Verarbeitung verweigert.",
                    recording_id=ready.recording_session_id,
                    journal_path=journal_path,
                    source_path=ready.source_path,
                    error_code="E_RUNNER_CLAIM_CONFLICT",
                )
                return None
            return existing
        state = SessionState(
            artifact_type="matrix_auto_cutter_product_runner_session",
            schema_version=STATUS_SCHEMA_VERSION,
            recording_session_id=UUID(ready.recording_session_id),
            journal_path=journal_path,
            journal_sha256=ready.journal_sha256,
            source_path=ready.source_path,
            project_id=self.dependencies.uuid_factory(),
            status=RunnerStatusCode.RECORDING_DETECTED,
            message_de="Aufnahme wurde aus einem vollständigen Journal erkannt.",
            attempt=0,
            updated_at=self.dependencies.now(),
        )
        if _atomic_bytes(state_path, _model_bytes(state), create_only=True):
            return state
        concurrent = _read_session(state_path)
        if concurrent is None:
            self._publish_status(
                RunnerStatusCode.INFRASTRUCTURE_ERROR,
                "Session-Claim erschien gleichzeitig, ist aber nicht vertrauenswürdig.",
                recording_id=ready.recording_session_id,
                journal_path=journal_path,
                error_code="E_RUNNER_CLAIM_RACE",
            )
            return None
        return self._state_for(ready, journal_path)

    def _store_session(self, state: SessionState) -> None:
        _atomic_bytes(
            self._session_path(str(state.recording_session_id)),
            _model_bytes(state),
            create_only=False,
        )

    def _terminal_without_finalizer(
        self,
        state: SessionState,
        code: RunnerStatusCode,
        message: str,
        error_code: str,
    ) -> None:
        if state.status is code and state.error_code == error_code and state.message_de == message:
            return
        updated = state.model_copy(
            update={
                "status": code,
                "message_de": message,
                "error_code": error_code,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(updated)
        self._publish_status(
            code,
            message,
            recording_id=str(state.recording_session_id),
            journal_path=state.journal_path,
            source_path=state.source_path,
            error_code=error_code,
        )

    def _proposal_failure(self, state: SessionState, code: str, message: str) -> None:
        if (
            state.status is RunnerStatusCode.PROPOSAL_FAILED
            and state.error_code == code
            and state.message_de == message
        ):
            return
        failed = state.model_copy(
            update={
                "status": RunnerStatusCode.PROPOSAL_FAILED,
                "message_de": message,
                "error_code": code,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(failed)
        self._publish_status(
            RunnerStatusCode.PROPOSAL_FAILED,
            message,
            recording_id=str(state.recording_session_id),
            journal_path=state.journal_path,
            source_path=state.source_path,
            sidecar_path=state.sidecar_path,
            proposal_path=state.proposal_path,
            review_path=state.review_path,
            approval_decision=state.approval_decision,
            error_code=code,
        )

    def _complete_proposal(self, state: SessionState, ready: ProposalReady) -> None:
        proposal = ready.proposal
        try:
            proposal_in_runner_workspace = ready.proposal_path.resolve(strict=True).is_relative_to(
                self.artifacts_directory.resolve(strict=True)
            )
        except OSError:
            proposal_in_runner_workspace = False
        if (
            not proposal_in_runner_workspace
            or proposal.recording_id != str(state.recording_session_id)
            or proposal.source_path.casefold()
            != _canonical_path(Path(state.source_path)).casefold()
            or state.sidecar_path is None
            or proposal.sidecar_path.casefold()
            != _canonical_path(Path(state.sidecar_path)).casefold()
        ):
            self._proposal_failure(
                state,
                "E_PROPOSAL_RUNNER_BINDING",
                "Proposal passt nicht exakt zu Recording, Source und Sidecar der Runner-Session.",
            )
            return
        pending = ensure_pending_approval(ready.proposal_path, now=self.dependencies.now)
        if isinstance(pending, DecisionFailed):
            self._proposal_failure(state, pending.code, pending.message_de)
            return
        assert isinstance(pending, DecisionWritten)
        try:
            review_path = write_review(ready.proposal_path)
        except (OSError, ValueError) as exc:
            self._proposal_failure(
                state,
                "E_REVIEW_WRITE",
                f"Lokale Review konnte nicht atomar erzeugt werden: {exc}",
            )
            return
        recording_id = str(state.recording_session_id)
        proposal_path_text = _canonical_path(ready.proposal_path)
        review_path_text = _canonical_path(review_path)
        gate = inspect_approval_state(ready.proposal_path)
        if gate.approval is None:
            self._proposal_failure(
                state,
                "E_APPROVAL_GATE",
                "Approval-Gate konnte die gebundene Entscheidung nicht erneut lesen.",
            )
            return
        proposal_message = (
            f"Schnittvorschlag bereit: {ready.proposal.total_proposed_cuts} Schnitt(e), "
            f"{ready.proposal.total_proposed_savings_ms / 1000:.3f} s mögliche Kürzung."
        )
        current = state.model_copy(
            update={
                "status": RunnerStatusCode.PROPOSAL_READY,
                "message_de": proposal_message,
                "proposal_path": proposal_path_text,
                "review_path": review_path_text,
                "approval_decision": gate.decision,
                "error_code": None,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(current)
        self._publish_status(
            RunnerStatusCode.PROPOSAL_READY,
            proposal_message,
            recording_id=recording_id,
            journal_path=current.journal_path,
            source_path=current.source_path,
            sidecar_path=current.sidecar_path,
            proposal_path=proposal_path_text,
            review_path=review_path_text,
            approval_decision=gate.decision,
        )
        open_note = ""
        if current.review_opened_proposal_id != ready.proposal.proposal_id:
            current = current.model_copy(
                update={
                    "review_opened_proposal_id": ready.proposal.proposal_id,
                    "updated_at": self.dependencies.now(),
                }
            )
            # Persist before launch: a crash may suppress a retry, never cause a second auto-open.
            self._store_session(current)
            if self.dependencies.open_review is not None:
                try:
                    self._replace_review_process(
                        ready.proposal_path,
                        ready.proposal.proposal_id,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    open_note = (
                        " Automatisches Öffnen scheiterte; Review bleibt unter "
                        f"{review_path_text} verfügbar: {exc}"
                    )
        status_by_decision = {
            "pending": RunnerStatusCode.APPROVAL_PENDING,
            "approved": RunnerStatusCode.PROPOSAL_APPROVED,
            "rejected": RunnerStatusCode.PROPOSAL_REJECTED,
            "selected_cuts_approved": RunnerStatusCode.PROPOSAL_APPROVED,
            "all_rejected": RunnerStatusCode.PROPOSAL_REJECTED,
        }
        message_by_decision = {
            "pending": "Schnittvorschlag wartet auf ausdrückliche Freigabe oder Ablehnung.",
            "approved": "Schnittvorschlag wurde ausdrücklich und digestgebunden freigegeben.",
            "rejected": (
                "Schnittvorschlag wurde ausdrücklich abgelehnt; Render ist nicht autorisiert."
            ),
            "selected_cuts_approved": (
                "Ausgewählte Schnitte wurden ausdrücklich und digestgebunden freigegeben."
            ),
            "all_rejected": (
                "Alle Schnitte wurden ausdrücklich abgelehnt; Render ist nicht autorisiert."
            ),
        }
        final_code = status_by_decision[gate.decision]
        final_message = message_by_decision[gate.decision] + open_note
        current = current.model_copy(
            update={
                "status": final_code,
                "message_de": final_message,
                "approval_decision": gate.decision,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(current)
        self._publish_status(
            final_code,
            final_message,
            recording_id=recording_id,
            journal_path=current.journal_path,
            source_path=current.source_path,
            sidecar_path=current.sidecar_path,
            proposal_path=proposal_path_text,
            review_path=review_path_text,
            approval_decision=gate.decision,
        )
        self._handle_render_request(
            current,
            ready,
            authorized=gate.authorized,
            reason=gate.reason,
        )

    def _refresh_proposal_state(self, state: SessionState, ready: ProposalReady) -> None:
        """Observe only approval transitions for an already materialized proposal."""
        proposal = ready.proposal
        if (
            proposal.recording_id != str(state.recording_session_id)
            or proposal.source_path.casefold()
            != _canonical_path(Path(state.source_path)).casefold()
            or state.sidecar_path is None
            or proposal.sidecar_path.casefold()
            != _canonical_path(Path(state.sidecar_path)).casefold()
        ):
            self._proposal_failure(
                state,
                "E_PROPOSAL_RUNNER_BINDING",
                "Persistiertes Proposal passt nicht mehr exakt zur Runner-Session.",
            )
            return
        gate = inspect_approval_state(ready.proposal_path)
        if gate.approval is None:
            self._proposal_failure(
                state,
                "E_APPROVAL_GATE",
                "Approval-Gate konnte die gebundene Entscheidung nicht erneut lesen.",
            )
            return
        if state.status in {
            RunnerStatusCode.RENDER_RUNNING,
            RunnerStatusCode.RENDER_VERIFYING,
            RunnerStatusCode.RENDER_SUCCEEDED,
            RunnerStatusCode.RENDER_FAILED,
        }:
            self._handle_render_request(
                state,
                ready,
                authorized=gate.authorized,
                reason=gate.reason,
            )
            return
        status_by_decision = {
            "pending": RunnerStatusCode.APPROVAL_PENDING,
            "approved": RunnerStatusCode.PROPOSAL_APPROVED,
            "rejected": RunnerStatusCode.PROPOSAL_REJECTED,
            "selected_cuts_approved": RunnerStatusCode.PROPOSAL_APPROVED,
            "all_rejected": RunnerStatusCode.PROPOSAL_REJECTED,
        }
        message_by_decision = {
            "pending": "Schnittvorschlag wartet auf ausdrückliche Freigabe oder Ablehnung.",
            "approved": "Schnittvorschlag wurde ausdrücklich und digestgebunden freigegeben.",
            "rejected": (
                "Schnittvorschlag wurde ausdrücklich abgelehnt; Render ist nicht autorisiert."
            ),
            "selected_cuts_approved": (
                "Ausgewählte Schnitte wurden ausdrücklich und digestgebunden freigegeben."
            ),
            "all_rejected": (
                "Alle Schnitte wurden ausdrücklich abgelehnt; Render ist nicht autorisiert."
            ),
        }
        final_code = status_by_decision[gate.decision]
        final_message = message_by_decision[gate.decision]
        current = state
        if gate.decision == "pending" and state.review_opened_proposal_id != proposal.proposal_id:
            current = state.model_copy(
                update={
                    "review_opened_proposal_id": proposal.proposal_id,
                    "updated_at": self.dependencies.now(),
                }
            )
            self._store_session(current)
            with suppress(OSError, RuntimeError, ValueError):
                self._replace_review_process(ready.proposal_path, proposal.proposal_id)
        if (
            current.status is final_code
            and current.approval_decision == gate.decision
            and current.message_de == final_message
        ):
            self._handle_render_request(
                current,
                ready,
                authorized=gate.authorized,
                reason=gate.reason,
            )
            return
        updated = current.model_copy(
            update={
                "status": final_code,
                "message_de": final_message,
                "approval_decision": gate.decision,
                "error_code": None,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(updated)
        self._publish_status(
            final_code,
            final_message,
            recording_id=str(updated.recording_session_id),
            journal_path=updated.journal_path,
            source_path=updated.source_path,
            sidecar_path=updated.sidecar_path,
            proposal_path=updated.proposal_path,
            review_path=updated.review_path,
            approval_decision=gate.decision,
        )
        self._handle_render_request(
            updated,
            ready,
            authorized=gate.authorized,
            reason=gate.reason,
        )

    def _propose(self, state: SessionState) -> None:
        if self.dependencies.propose is None:
            self._publish_status(
                state.status,
                state.message_de,
                recording_id=str(state.recording_session_id),
                journal_path=state.journal_path,
                source_path=state.source_path,
                sidecar_path=state.sidecar_path,
                proposal_path=state.proposal_path,
                review_path=state.review_path,
                approval_decision=state.approval_decision,
                error_code=state.error_code,
            )
            return
        recording_id = str(state.recording_session_id)
        if state.proposal_path is not None:
            self._proposal_attempted_this_run.add(recording_id)
            loaded = load_proposal(Path(state.proposal_path))
            if isinstance(loaded, ProposalFailed):
                self._proposal_failure(state, loaded.code, loaded.message_de)
                return
            self._refresh_proposal_state(state, loaded)
            return
        if recording_id in self._proposal_attempted_this_run:
            return
        self._proposal_attempted_this_run.add(recording_id)
        if state.sidecar_path is None:
            self._proposal_failure(
                state,
                "E_PROPOSAL_SIDECAR_MISSING",
                "Sidecarpfad fehlt; kein zeitentfernender Vorschlag wurde erzeugt.",
            )
            return
        pending = state.model_copy(
            update={
                "status": RunnerStatusCode.ANALYSIS_PENDING,
                "message_de": "Konservative Audioanalyse ist eingeplant.",
                "error_code": None,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(pending)
        self._publish_status(
            RunnerStatusCode.ANALYSIS_PENDING,
            pending.message_de,
            recording_id=recording_id,
            journal_path=pending.journal_path,
            source_path=pending.source_path,
            sidecar_path=pending.sidecar_path,
        )
        running = pending.model_copy(
            update={
                "status": RunnerStatusCode.ANALYSIS_RUNNING,
                "message_de": "FFmpeg analysiert die Aufnahme ausschließlich lesend auf Stille.",
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(running)
        self._publish_status(
            RunnerStatusCode.ANALYSIS_RUNNING,
            running.message_de,
            recording_id=recording_id,
            journal_path=running.journal_path,
            source_path=running.source_path,
            sidecar_path=running.sidecar_path,
        )
        try:
            assert running.sidecar_path is not None
            result = self.dependencies.propose(
                Path(running.source_path),
                Path(running.sidecar_path),
                recording_id,
                self.artifacts_directory,
            )
        except Exception as exc:
            result = ProposalFailed(
                "E_PROPOSAL_EXCEPTION",
                f"Unerwarteter Analyse-Infrastrukturfehler: {type(exc).__name__}: {exc}",
            )
        if isinstance(result, ProposalFailed):
            self._proposal_failure(running, result.code, result.message_de)
            return
        self._complete_proposal(running, result)

    def _finalize(self, state: SessionState) -> None:
        recording_id = str(state.recording_session_id)
        if recording_id in self._attempted_this_run:
            return
        self._attempted_this_run.add(recording_id)
        token = CancellationToken()
        project_error = self.dependencies.ensure_project(
            str(state.project_id), self.workspace_path, token
        )
        if project_error is not None:
            self._terminal_without_finalizer(
                state,
                RunnerStatusCode.INFRASTRUCTURE_ERROR,
                project_error,
                "E_RUNNER_PROJECT",
            )
            return
        running = state.model_copy(
            update={
                "status": RunnerStatusCode.FINALIZER_RUNNING,
                "message_de": "Finalizer läuft.",
                "attempt": state.attempt + 1,
                "updated_at": self.dependencies.now(),
                "error_code": None,
            }
        )
        self._store_session(running)
        self._publish_status(
            RunnerStatusCode.FINALIZER_RUNNING,
            f"Finalizer läuft für {running.source_path}.",
            recording_id=recording_id,
            journal_path=running.journal_path,
            source_path=running.source_path,
        )
        try:
            result = self.dependencies.finalize(
                ManualFinalizerRequest(
                    running.source_path,
                    running.journal_path,
                    self.workspace_path,
                    project_id=str(running.project_id),
                )
            )
        except Exception as exc:
            result = ManualFinalizationFailed(
                "runner",
                "E_RUNNER_FINALIZER_EXCEPTION",
                f"Unerwarteter Infrastrukturfehler: {type(exc).__name__}: {exc}",
            )
        if not isinstance(result, ManualFinalizationFailed):
            message = (
                f"Sidecar erfolgreich veröffentlicht und erneut validiert: {result.sidecar_path}"
            )
            succeeded = running.model_copy(
                update={
                    "status": RunnerStatusCode.SIDECAR_SUCCEEDED,
                    "message_de": message,
                    "sidecar_path": result.sidecar_path,
                    "error_code": None,
                    "updated_at": self.dependencies.now(),
                }
            )
            self._store_session(succeeded)
            self._publish_status(
                RunnerStatusCode.SIDECAR_SUCCEEDED,
                message,
                recording_id=recording_id,
                journal_path=running.journal_path,
                source_path=running.source_path,
                sidecar_path=result.sidecar_path,
            )
            self._propose(succeeded)
            return
        code = RunnerStatusCode.FINALIZER_FAILED
        if result.stage == "ffprobe_discovery":
            code = RunnerStatusCode.FFPROBE_UNAVAILABLE
        elif result.stage == "ffprobe_validation":
            code = RunnerStatusCode.FFPROBE_UNTRUSTED
        elif result.stage not in {"finalizer", "source_confirmation", "close_gate"}:
            code = RunnerStatusCode.INFRASTRUCTURE_ERROR
        message = f"Finalizerfehler [{result.stage}/{result.code}]: {result.message}"
        failed = running.model_copy(
            update={
                "status": code,
                "message_de": message,
                "sidecar_path": result.published_sidecar_path,
                "error_code": result.code,
                "updated_at": self.dependencies.now(),
            }
        )
        self._store_session(failed)
        self._publish_status(
            code,
            message,
            recording_id=recording_id,
            journal_path=running.journal_path,
            source_path=running.source_path,
            sidecar_path=result.published_sidecar_path,
            error_code=result.code,
        )

    def _process_ready(self, journal: Path, ready: JournalReady) -> None:
        journal_path = _canonical_path(journal)
        normalized_source = _normalize_source(ready.source_path)
        if normalized_source is None or ntpath.splitext(normalized_source)[1].casefold() != ".mp4":
            self._publish_status(
                RunnerStatusCode.JOURNAL_INVALID,
                "Journal enthält keinen absoluten Direct-MP4-Pfad.",
                recording_id=ready.recording_session_id,
                journal_path=journal_path,
                source_path=ready.source_path,
                error_code="E_RUNNER_SOURCE_PATH",
            )
            return
        normalized = replace(ready, source_path=normalized_source)
        state = self._state_for(normalized, journal_path)
        if state is None:
            return
        terminal_states = {
            RunnerStatusCode.SOURCE_OUTSIDE_ROOT,
            RunnerStatusCode.FINALIZER_FAILED,
            RunnerStatusCode.FFPROBE_UNAVAILABLE,
            RunnerStatusCode.FFPROBE_UNTRUSTED,
        }
        if state.status in terminal_states:
            return
        proposal_states = {
            RunnerStatusCode.SIDECAR_SUCCEEDED,
            RunnerStatusCode.ANALYSIS_PENDING,
            RunnerStatusCode.ANALYSIS_RUNNING,
            RunnerStatusCode.PROPOSAL_READY,
            RunnerStatusCode.PROPOSAL_FAILED,
            RunnerStatusCode.APPROVAL_PENDING,
            RunnerStatusCode.PROPOSAL_APPROVED,
            RunnerStatusCode.PROPOSAL_REJECTED,
            RunnerStatusCode.RENDER_NOT_AUTHORIZED,
            RunnerStatusCode.RENDER_READY,
            RunnerStatusCode.RENDER_RUNNING,
            RunnerStatusCode.RENDER_VERIFYING,
            RunnerStatusCode.RENDER_SUCCEEDED,
            RunnerStatusCode.RENDER_FAILED,
        }
        if state.status in proposal_states and state.sidecar_path is not None:
            self._propose(state)
            return
        if not _under_root(normalized_source, self.source_root):
            self._terminal_without_finalizer(
                state,
                RunnerStatusCode.SOURCE_OUTSIDE_ROOT,
                f"Aufnahme liegt außerhalb des konfigurierten Quellpfads {self.source_root}.",
                "E_RUNNER_SOURCE_OUTSIDE_ROOT",
            )
            return
        if not Path(normalized_source).is_file():
            self._terminal_without_finalizer(
                state,
                RunnerStatusCode.SOURCE_MISSING,
                f"MP4 fehlt; der Runner prüft beim nächsten Poll erneut: {normalized_source}",
                "E_RUNNER_SOURCE_MISSING",
            )
            return
        resolved_source = str(Path(normalized_source).resolve(strict=True))
        if not _under_root(resolved_source, self._resolved_source_root):
            self._terminal_without_finalizer(
                state,
                RunnerStatusCode.SOURCE_OUTSIDE_ROOT,
                "Aufgelöster Aufnahmeort liegt außerhalb des konfigurierten Quellpfads.",
                "E_RUNNER_SOURCE_REPARSE_ESCAPE",
            )
            return
        self._publish_status(
            RunnerStatusCode.RECORDING_DETECTED,
            f"Aufnahme erfolgreich erkannt: {normalized_source}",
            recording_id=normalized.recording_session_id,
            journal_path=journal_path,
            source_path=normalized_source,
        )
        self._finalize(state)

    def scan_once(self) -> None:
        """Prüfe jeden bekannten Journalpfad einmal und fahre nach Fehlern fort."""
        try:
            journals = sorted(
                self.journal_directory.glob(f"*{JOURNAL_SUFFIX}"),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
            )
        except OSError as exc:
            self._publish_status(
                RunnerStatusCode.INFRASTRUCTURE_ERROR,
                f"Journalablage konnte nicht gelesen werden: {exc}",
                error_code="E_RUNNER_JOURNAL_DIRECTORY",
            )
        else:
            for journal in journals:
                try:
                    inspected = self.dependencies.inspect_journal(journal)
                    if isinstance(inspected, JournalUnavailable):
                        self._publish_status(
                            inspected.code,
                            inspected.message_de,
                            recording_id=inspected.recording_session_id,
                            journal_path=_canonical_path(journal),
                            error_code=inspected.error_code,
                        )
                        continue
                    self._process_ready(journal, inspected)
                except Exception as exc:
                    self._publish_status(
                        RunnerStatusCode.INFRASTRUCTURE_ERROR,
                        f"Unerwarteter Infrastrukturfehler für {journal.name}: "
                        f"{type(exc).__name__}: {exc}",
                        journal_path=_canonical_path(journal),
                        error_code="E_RUNNER_UNEXPECTED",
                    )
        self.heartbeat()


class SingleInstance:
    """Pro Benutzer genau ein Runner mittels nicht verwaisender Windows-Dateisperre."""

    def __init__(self, path: Path | None = None) -> None:
        """Erzeuge eine noch nicht gehaltene Sperre in der Per-User-Statusablage."""
        self.path = path or (default_state_directory() / "runner.lock")
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Belege die Instanz oder melde eine bereits aktive Instanz."""
        if os.name != "nt":
            raise RuntimeError("Der Product Runner unterstützt nur Windows.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def close(self) -> None:
        """Gib Dateisperre und Descriptor genau einmal frei."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)

    def __enter__(self) -> SingleInstance:
        """Belege die Einzelinstanz für einen Kontext."""
        if not self.acquire():
            raise RuntimeError("runner_already_running")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Gib die Einzelinstanz beim Verlassen des Kontexts frei."""
        self.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automatischer OBS-Post-Stop-Product-Runner")
    parser.add_argument("--journal-directory", type=Path)
    parser.add_argument("--state-directory", type=Path)
    parser.add_argument("--source-root", default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--workspace", default=resolve_default_workspace_root())
    parser.add_argument("--ffprobe")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="genau einen Poll für Tests/Diagnose")
    parser.add_argument("--stop", action="store_true", help="kontrollierten Runner-Stop anfordern")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the quiet single-instance runner until a controlled stop."""
    args = _parser().parse_args(argv)
    try:
        state_directory = args.state_directory or default_state_directory()
    except RuntimeError as exc:
        print(f"Runner-Startfehler: {exc}", file=sys.stderr)
        return 1
    diagnostics = RunnerLogSink(state_directory / "logs")
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    sys.stdout = diagnostics
    sys.stderr = diagnostics
    if args.stop:
        requested = request_runner_stop(state_directory)
        diagnostics.event(
            "INFO" if requested else "ERROR",
            (
                "Kontrollierter Runner-Stop wurde angefordert."
                if requested
                else "Kontrollierter Runner-Stop konnte nicht angefordert werden."
            ),
            status_code="runner_stop_requested",
            error_code=None if requested else "E_RUNNER_STOP_REQUEST",
        )
        diagnostics.flush()
        sys.stdout, sys.stderr = previous_stdout, previous_stderr
        return 0 if requested else 1
    if not 0.25 <= args.poll_seconds <= 60:
        print("Fehler: --poll-seconds muss zwischen 0.25 und 60 liegen.", file=sys.stderr)
        diagnostics.flush()
        sys.stdout, sys.stderr = previous_stdout, previous_stderr
        return 2
    guard = SingleInstance()
    runner: ProductRunner | None = None
    try:
        if not guard.acquire():
            print("Matrix Auto Cutter Product Runner läuft bereits.")
            return 2
        runner = ProductRunner(
            args.journal_directory or default_journal_directory(),
            state_directory,
            args.source_root,
            args.workspace,
            native_dependencies(args.workspace, args.ffprobe, args.ffmpeg),
            output=cast(TextIO, diagnostics),
            diagnostics=diagnostics,
        )
        runner.starting()
        runner.ready()
        runner.scan_once()
        if args.once:
            return 0
        stopped = threading.Event()

        def stop(_signum: int, _frame: FrameType | None) -> None:
            stopped.set()

        signal.signal(signal.SIGINT, stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, stop)
        while not stopped.wait(args.poll_seconds):
            if runner.stop_requested():
                stopped.set()
            else:
                runner.scan_once()
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        diagnostics.event(
            "ERROR",
            f"Runner-Startfehler: {type(exc).__name__}: {exc}",
            status_code=RunnerStatusCode.INFRASTRUCTURE_ERROR.value,
            error_code="E_RUNNER_STARTUP",
        )
        if runner is not None:
            runner._publish_status(
                RunnerStatusCode.INFRASTRUCTURE_ERROR,
                f"Runner-Startfehler: {type(exc).__name__}: {exc}",
                error_code="E_RUNNER_STARTUP",
            )
        print(f"Runner-Startfehler: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if runner is not None:
            runner.shutdown()
        guard.close()
        diagnostics.flush()
        sys.stdout, sys.stderr = previous_stdout, previous_stderr


if __name__ == "__main__":
    raise SystemExit(main())
