"""Lokaler Produkt-Runner für die automatische Post-Stop-Finalisierung."""

from __future__ import annotations

import argparse
import msvcrt
import ntpath
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import Literal, TextIO, cast
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, ValidationError

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

JOURNAL_SUFFIX = ".recording-journal.ndjson"
DEFAULT_SOURCE_ROOT = r"F:\MatrixMarketAutoEdit"
POLL_SECONDS = 2.0
STATUS_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class RunnerStatusCode(StrEnum):
    """Maschinenlesbare Zustände mit stabiler Bedeutung."""

    RUNNER_READY = "runner_ready"
    JOURNAL_INCOMPLETE = "journal_incomplete"
    RECORDING_DETECTED = "recording_detected"
    FINALIZER_RUNNING = "finalizer_running"
    SIDECAR_SUCCEEDED = "sidecar_succeeded"
    SOURCE_OUTSIDE_ROOT = "source_outside_root"
    SOURCE_MISSING = "source_missing"
    JOURNAL_INVALID = "journal_invalid"
    STOP_NOT_FINALIZABLE = "stop_not_finalizable"
    FFPROBE_UNAVAILABLE = "ffprobe_unavailable"
    FFPROBE_UNTRUSTED = "ffprobe_untrusted"
    FINALIZER_FAILED = "finalizer_failed"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    RUNNER_STOPPED = "runner_stopped"


class RunnerStatus(CanonicalModel):
    """Persistierte Betriebsansicht des zuletzt beobachteten Ereignisses."""

    artifact_type: Literal["matrix_auto_cutter_product_runner_status"]
    schema_version: Literal["1.0"]
    runner_instance_id: CanonicalUuid4
    code: RunnerStatusCode
    message_de: str = Field(min_length=1, max_length=2000)
    runner_ready: bool
    updated_at: AwareDatetime
    recording_session_id: CanonicalUuid4 | None = None
    journal_path: str | None = None
    source_path: str | None = None
    sidecar_path: str | None = None
    error_code: str | None = None


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


@dataclass(frozen=True, slots=True)
class RunnerDependencies:
    """Schmale Ports für Parser, Projektanlage und bestehenden Finalizer."""

    inspect_journal: JournalInspector
    ensure_project: ProjectEnsurer
    finalize: FinalizerRunner
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    uuid_factory: Callable[[], UUID] = uuid4


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


def native_dependencies(workspace_path: str, ffprobe_path: str | None) -> RunnerDependencies:
    """Verdrahte den Runner mit den bestehenden nativen Produktkompositionen."""
    ports = ManualFinalizerPorts.native()

    def inspect(path: Path) -> JournalInspection:
        return inspect_journal_native(path, ports)

    def ensure(project_id: str, workspace: str, token: CancellationToken) -> str | None:
        return _ensure_project_native(ports, workspace, project_id, token)

    def finalize_existing(request: ManualFinalizerRequest) -> ManualFinalizationResult:
        return run_manual_finalizer(ports, replace(request, ffprobe_path=ffprobe_path))

    return RunnerDependencies(inspect, ensure, finalize_existing)


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
        self.instance_id = dependencies.uuid_factory()
        if self.instance_id.version != 4:
            raise ValueError("runner instance ID must be UUIDv4")
        self._attempted_this_run: set[str] = set()
        self._last_console_key: tuple[object, ...] | None = None

    @property
    def status_path(self) -> Path:
        """Liefere den Pfad der aktuellen maschinenlesbaren Betriebsansicht."""
        return self.state_directory / "status.json"

    @property
    def sessions_directory(self) -> Path:
        """Liefere die Ablage der dauerhaften Session-Claims."""
        return self.state_directory / "sessions"

    def _session_path(self, recording_id: str) -> Path:
        return self.sessions_directory / f"{recording_id}.json"

    def _publish_status(
        self,
        code: RunnerStatusCode,
        message: str,
        *,
        recording_id: str | None = None,
        journal_path: str | None = None,
        source_path: str | None = None,
        sidecar_path: str | None = None,
        error_code: str | None = None,
        ready: bool = True,
    ) -> None:
        status = RunnerStatus(
            artifact_type="matrix_auto_cutter_product_runner_status",
            schema_version=STATUS_SCHEMA_VERSION,
            runner_instance_id=self.instance_id,
            code=code,
            message_de=message,
            runner_ready=ready,
            updated_at=self.dependencies.now(),
            recording_session_id=UUID(recording_id) if recording_id is not None else None,
            journal_path=journal_path,
            source_path=source_path,
            sidecar_path=sidecar_path,
            error_code=error_code,
        )
        _atomic_bytes(self.status_path, _model_bytes(status), create_only=False)
        key = (code, recording_id, journal_path, source_path, sidecar_path, error_code, message)
        if key != self._last_console_key:
            timestamp = status.updated_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] [{code.value}] {message}", file=self.output, flush=True)
            self._last_console_key = key

    def ready(self) -> None:
        """Veröffentliche den sichtbaren Bereitschaftszustand."""
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.sessions_directory.mkdir(parents=True, exist_ok=True)
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
        terminal = {
            RunnerStatusCode.SIDECAR_SUCCEEDED,
            RunnerStatusCode.SOURCE_OUTSIDE_ROOT,
        }
        if state.status in terminal:
            self._publish_status(
                state.status,
                state.message_de,
                recording_id=str(state.recording_session_id),
                journal_path=state.journal_path,
                source_path=state.source_path,
                sidecar_path=state.sidecar_path,
                error_code=state.error_code,
            )
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
            return
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
    parser.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    parser.add_argument("--once", action="store_true", help="genau einen Poll für Tests/Diagnose")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Den sichtbaren Einzelinstanz-Runner bis Ctrl+C starten."""
    args = _parser().parse_args(argv)
    if not 0.25 <= args.poll_seconds <= 60:
        print("Fehler: --poll-seconds muss zwischen 0.25 und 60 liegen.", file=sys.stderr)
        return 2
    guard = SingleInstance()
    try:
        if not guard.acquire():
            print("Matrix Auto Cutter Product Runner läuft bereits.")
            return 2
        runner = ProductRunner(
            args.journal_directory or default_journal_directory(),
            args.state_directory or default_state_directory(),
            args.source_root,
            args.workspace,
            native_dependencies(args.workspace, args.ffprobe),
        )
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
            runner.scan_once()
        runner._publish_status(
            RunnerStatusCode.RUNNER_STOPPED,
            "Runner wurde sauber beendet.",
            ready=False,
        )
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Runner-Startfehler: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
