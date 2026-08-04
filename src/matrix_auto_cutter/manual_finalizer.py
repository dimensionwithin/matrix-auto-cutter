"""Produktiver manueller Direct-MP4-/Legacy-Journal-Finalizer."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TextIO, cast
from uuid import UUID, uuid4

from matrix_auto_cutter.models import SourceBinding, SourceIdentity
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.close_gate import (
    CloseGateBusy,
    CloseGateClosed,
    CloseGateDeletePending,
    CloseGateLease,
    CloseGateUnstable,
    CloseGateWin32Port,
    NativeCloseGateWin32Port,
    SystemWaitClock,
    WaitClockPort,
    run_close_gate,
)
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationRequest,
    Finalized,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    finalize,
)
from matrix_auto_cutter.phase2.finalizer.publisher import TargetValid, read_committed_sidecar
from matrix_auto_cutter.phase2.pathing import PathRejected, PathRole, ValidatedPath, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    NativeProcessPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import BinaryTrustPort, NativeBinaryTrustPort
from matrix_auto_cutter.phase2.probe.process_port import ProcessPort
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationPorts,
    SourceConfirmationRequest,
    SourceConfirmed,
    confirm_source,
)
from matrix_auto_cutter.phase2.win32_port import Win32Port
from matrix_auto_cutter.phase2.workspace import (
    ProjectCapability,
    ProjectCreated,
    ProjectOpened,
    WorkspaceReady,
    create_project,
    ensure_workspace,
    open_project,
    resolve_default_workspace_root,
)
from matrix_auto_cutter.sidecar import validate_sidecar


@dataclass(frozen=True, slots=True)
class CloseGateRetryPolicy:
    """Kleine begrenzte Retry-Policy für kurzzeitig offene Aufnahmen."""

    max_attempts: int = 3
    delay_seconds: float = 0.5
    lock_timeout_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Verhindere unbegrenzte oder ungültige Wartewerte."""
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("close-gate max_attempts must be between 1 and 10")
        if not math.isfinite(self.delay_seconds) or not 0 < self.delay_seconds <= 10:
            raise ValueError("close-gate delay_seconds must be finite and between 0 and 10")
        if not math.isfinite(self.lock_timeout_seconds) or not 0 <= self.lock_timeout_seconds <= 10:
            raise ValueError("close-gate lock_timeout_seconds must be between 0 and 10")


@dataclass(frozen=True, slots=True)
class ManualFinalizerRequest:
    """Explizite Eingaben des ausschließlich manuellen Legacy-Produktpfads."""

    source_path: str
    journal_path: str
    workspace_path: str | None = None
    ffprobe_path: str | None = None
    project_id: str | None = None
    probe_timeout_seconds: int = 120
    close_gate_retry: CloseGateRetryPolicy = field(default_factory=CloseGateRetryPolicy)


@dataclass(frozen=True, slots=True)
class ManualFinalizerPorts:
    """Produktadapter und kontrollierbare Zeit-/ID-Grenzen des Runners."""

    win32: CloseGateWin32Port
    binary_trust: BinaryTrustPort
    probe_process: ProcessPort
    wait_clock: WaitClockPort = field(
        default_factory=lambda: cast(WaitClockPort, SystemWaitClock())
    )
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    uuid_factory: Callable[[], UUID] = uuid4

    @classmethod
    def native(cls) -> ManualFinalizerPorts:
        """Erzeuge die reale Windows-Komposition mit genau einem Win32-Port."""
        win32 = cast(CloseGateWin32Port, NativeCloseGateWin32Port())
        return cls(
            win32,
            cast(BinaryTrustPort, NativeBinaryTrustPort(cast(Win32Port, win32))),
            cast(ProcessPort, NativeProcessPort()),
        )


@dataclass(frozen=True, slots=True)
class ManualFinalizationSucceeded:
    """Erfolg nach erneuter sichtbarer Sidecar-Validierung."""

    sidecar_path: str
    project_id: str
    recording_id: str
    idempotent: bool


@dataclass(frozen=True, slots=True)
class ManualFinalizationFailed:
    """Konkreter Runner-Fehler mit stabiler Phasenangabe."""

    stage: str
    code: str
    message: str
    published_sidecar_path: str | None = None


type ManualFinalizationResult = ManualFinalizationSucceeded | ManualFinalizationFailed


def _error_value(
    stage: str, value: object, *, message_prefix: str = ""
) -> ManualFinalizationFailed:
    error = getattr(value, "error", value)
    code_value = getattr(error, "code", None)
    code = str(code_value) if code_value is not None else "E_MANUAL_FINALIZER"
    message_value = getattr(error, "message", None)
    if not isinstance(message_value, str):
        message_value = getattr(error, "user_text_de", None)
    if not isinstance(message_value, str) or not message_value:
        message_value = type(value).__name__
    phase = getattr(error, "phase", None)
    phase_prefix = f"{phase}: " if isinstance(phase, str) and phase else ""
    return ManualFinalizationFailed(
        stage,
        code,
        f"{message_prefix}{phase_prefix}{message_value}",
    )


def _uuid_text(factory: Callable[[], UUID], field_name: str) -> str:
    value = factory()
    if not isinstance(value, UUID) or value.version != 4:
        raise ValueError(f"{field_name} factory result is not UUIDv4")
    return str(value)


def _resolve_ffprobe_path(requested: str | None) -> str | ManualFinalizationFailed:
    if requested is not None:
        return requested
    discovered = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
    if discovered is None:
        return ManualFinalizationFailed(
            "ffprobe_discovery",
            "E_FFPROBE_NOT_FOUND",
            "ffprobe wurde weder explizit angegeben noch im PATH gefunden",
        )
    return os.path.abspath(discovered)


def _close_gate_with_retry(
    ports: ManualFinalizerPorts,
    project: ProjectCapability,
    source_path: ValidatedPath,
    cancellation: CancellationToken,
    policy: CloseGateRetryPolicy,
) -> CloseGateClosed | ManualFinalizationFailed:
    for attempt in range(1, policy.max_attempts + 1):
        gated = run_close_gate(
            ports.win32,
            project.document.project_id,
            source_path,
            cancellation,
            wait_clock=ports.wait_clock,
            lock_timeout_seconds=policy.lock_timeout_seconds,
            lease_id_factory=ports.uuid_factory,
        )
        if isinstance(gated, CloseGateClosed):
            return gated
        retryable = isinstance(gated, CloseGateBusy | CloseGateDeletePending | CloseGateUnstable)
        if not retryable or attempt == policy.max_attempts:
            return _error_value(
                "close_gate",
                gated,
                message_prefix=f"Close-Gate nach {attempt} Versuch(en) abgelehnt: ",
            )
        try:
            cancelled = ports.wait_clock.wait(cancellation, policy.delay_seconds)
        except Exception as exc:
            return ManualFinalizationFailed(
                "close_gate_retry",
                "E_CLOSE_GATE_RETRY_CLOCK",
                f"Close-Gate-Retry-Warteadapter fehlgeschlagen: {exc}",
            )
        if cancelled or cancellation.is_cancelled:
            return ManualFinalizationFailed(
                "close_gate_retry",
                "E_CANCELLED",
                f"Abbruch während Close-Gate-Retry nach Versuch {attempt}",
            )
    raise AssertionError("bounded close-gate retry loop did not return")


def _validate_published_sidecar(
    ports: ManualFinalizerPorts,
    finalized: Finalized,
    expected_source: SourceIdentity,
) -> ManualFinalizationFailed | None:
    checked = validate_path(
        ports.win32,
        finalized.sidecar.canonical_path,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(checked, PathRejected):
        return _error_value("sidecar_path_validation", checked)
    observed = read_committed_sidecar(ports.win32, checked.path, expected_source)
    if not isinstance(observed, TargetValid):
        return _error_value(
            "sidecar_validation",
            observed,
            message_prefix="Veröffentlichtes Sidecar konnte nicht erneut validiert werden: ",
        )
    if observed.location != finalized.sidecar:
        return ManualFinalizationFailed(
            "sidecar_validation",
            "E_SIDECAR_COMMIT_MISMATCH",
            "erneut gelesenes Sidecar stimmt nicht mit dem Finalizer-Ergebnis überein",
            finalized.sidecar.canonical_path,
        )
    raw = json.loads(observed.sidecar.model_dump_json(), parse_float=Decimal)
    payload = cast(Mapping[str, object], raw)
    validation = validate_sidecar(payload, expected_source)
    if (
        validation.mode not in {"validated_sidecar_1_1", "validated_sidecar_1_2"}
        or validation.sidecar != observed.sidecar
    ):
        reasons = ", ".join(reason.code.value for reason in validation.reasons)
        return ManualFinalizationFailed(
            "sidecar_validation",
            "E_SIDECAR_VALIDATION",
            f"bestehender Sidecarvalidator lehnte das veröffentlichte Artefakt ab: {reasons}",
            finalized.sidecar.canonical_path,
        )
    return None


def _run_with_lease(
    ports: ManualFinalizerPorts,
    request: ManualFinalizerRequest,
    project: ProjectCapability,
    journal_path: ValidatedPath,
    ffprobe_path: str,
    lease: CloseGateLease,
    cancellation: CancellationToken,
) -> ManualFinalizationResult:
    binary = validate_ffprobe_binary(
        FfprobeCandidate(ffprobe_path),
        ports.win32,
        ports.binary_trust,
        ports.probe_process,
        now=ports.now,
    )
    if not isinstance(binary, BinaryValidated):
        return _error_value("ffprobe_validation", binary)

    confirmation = confirm_source(
        ConfirmationPorts(ports.win32, ports.binary_trust, ports.probe_process),
        SourceConfirmationRequest(
            project,
            _uuid_text(ports.uuid_factory, "identity_run_id"),
            _uuid_text(ports.uuid_factory, "probe_id"),
            _uuid_text(ports.uuid_factory, "probe_run_id"),
            _uuid_text(ports.uuid_factory, "hash_run_id"),
            lease,
            binary.binary,
            SourceBinding.DIRECT_MP4,
            probe_timeout_seconds=request.probe_timeout_seconds,
        ),
        cancellation,
    )
    if not isinstance(confirmation, SourceConfirmed):
        return _error_value("source_confirmation", confirmation)
    if confirmation.source_identity.binding is not SourceBinding.DIRECT_MP4:
        return ManualFinalizationFailed(
            "source_confirmation",
            "E_SOURCE_BINDING",
            "Source Confirmation lieferte keine Direct-MP4-Bindung",
        )

    finalized = finalize(
        FinalizerPorts(ports.win32, ports.now, ports.uuid_factory),
        FinalizationRequest(
            project,
            _uuid_text(ports.uuid_factory, "finalizer_run_id"),
            JournalInputProfile.LEGACY,
            JournalInputPaths(journal_path),
            confirmation.confirmed_source,
        ),
        cancellation,
    )
    if not isinstance(finalized, Finalized):
        return _error_value("finalizer", finalized)

    sidecar_failure = _validate_published_sidecar(
        ports,
        finalized,
        confirmation.source_identity,
    )
    if sidecar_failure is not None:
        return sidecar_failure
    return ManualFinalizationSucceeded(
        finalized.sidecar.canonical_path,
        project.document.project_id,
        finalized.recording_id,
        finalized.idempotent,
    )


def run_manual_finalizer(
    ports: ManualFinalizerPorts,
    request: ManualFinalizerRequest,
    cancellation: CancellationToken | None = None,
) -> ManualFinalizationResult:
    """Komponiere den realen Direct-MP4-/Legacy-Pfad unter einer offenen Lease."""
    token = cancellation or CancellationToken()
    if request.probe_timeout_seconds < 1 or request.probe_timeout_seconds > 600:
        return ManualFinalizationFailed(
            "input",
            "E_PROBE_TIMEOUT_INPUT",
            "probe_timeout_seconds muss zwischen 1 und 600 liegen",
        )
    if not request.source_path.casefold().endswith(".mp4"):
        return ManualFinalizationFailed(
            "source_path",
            "E_SOURCE_BINDING",
            "der manuelle Runner unterstützt ausschließlich Direct-MP4-Quelldateien",
        )
    ffprobe_path = _resolve_ffprobe_path(request.ffprobe_path)
    if isinstance(ffprobe_path, ManualFinalizationFailed):
        return ffprobe_path

    source = validate_path(
        ports.win32,
        request.source_path,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(source, PathRejected):
        return _error_value("source_path", source)
    journal = validate_path(
        ports.win32,
        request.journal_path,
        PathRole.EXTERNAL_SOURCE_READ_ONLY,
        require_existing=True,
        require_regular_file=True,
    )
    if isinstance(journal, PathRejected):
        return _error_value("journal_path", journal)

    workspace = ensure_workspace(
        ports.win32,
        request.workspace_path or resolve_default_workspace_root(),
    )
    if not isinstance(workspace, WorkspaceReady):
        return _error_value("workspace", workspace)
    project_result: object
    if request.project_id is None:
        project_result = create_project(
            ports.win32,
            workspace,
            token,
            uuid_factory=ports.uuid_factory,
        )
    else:
        project_result = open_project(
            ports.win32,
            workspace,
            request.project_id,
        )
    if not isinstance(project_result, ProjectCreated | ProjectOpened):
        return _error_value("project", project_result)
    project = project_result.project

    gated = _close_gate_with_retry(
        ports,
        project,
        source.path,
        token,
        request.close_gate_retry,
    )
    if isinstance(gated, ManualFinalizationFailed):
        return gated

    outcome: ManualFinalizationResult
    try:
        try:
            outcome = _run_with_lease(
                ports,
                request,
                project,
                journal.path,
                ffprobe_path,
                gated.lease,
                token,
            )
        except Exception as exc:
            outcome = ManualFinalizationFailed(
                "runner",
                "E_MANUAL_FINALIZER_EXCEPTION",
                f"unerwarteter Infrastrukturfehler: {type(exc).__name__}: {exc}",
            )
    finally:
        try:
            cleanup = gated.lease.close()
        except Exception as exc:
            cleanup = ()
            cleanup_exception: Exception | None = exc
        else:
            cleanup_exception = None

    published = outcome.sidecar_path if isinstance(outcome, ManualFinalizationSucceeded) else None
    if cleanup_exception is not None:
        return ManualFinalizationFailed(
            "resource_cleanup",
            "E_RESOURCE_CLEANUP",
            f"Close-Gate-Lease konnte nicht zuverlässig geschlossen werden: {cleanup_exception}",
            published,
        )
    if cleanup:
        first = cleanup[0]
        return ManualFinalizationFailed(
            "resource_cleanup",
            "E_RESOURCE_CLEANUP",
            f"{first.phase}: {first.message}",
            published,
        )
    return outcome


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matrix-auto-finalize",
        description="Finalisiert eine Direct-MP4-Aufnahme mit einem Legacy-Journal.",
    )
    parser.add_argument("source", help="absoluter Pfad zur Direct-MP4-Quelldatei")
    parser.add_argument("journal", help="absoluter Pfad zum Legacy-NDJSON-Journal")
    parser.add_argument("--workspace", help="absoluter Workspacepfad")
    parser.add_argument("--ffprobe", help="absoluter ffprobe-Pfad; Standard: PATH")
    parser.add_argument("--project-id", help="bestehendes Projekt öffnen statt neu anlegen")
    parser.add_argument(
        "--probe-timeout-seconds",
        type=int,
        default=120,
        help="ffprobe-Timeout zwischen 1 und 600 Sekunden (Standard: 120)",
    )
    return parser


def _print_failure(result: ManualFinalizationFailed, stream: TextIO) -> None:
    print(f"Fehler [{result.stage}/{result.code}]: {result.message}", file=stream)
    if result.published_sidecar_path is not None:
        print(
            f"Hinweis: Das Sidecar kann bereits veröffentlicht sein: "
            f"{result.published_sidecar_path}",
            file=stream,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstieg mit verständlichem Erfolg und Nicht-null-Fehlercodes."""
    args = _parser().parse_args(argv)
    request = ManualFinalizerRequest(
        args.source,
        args.journal,
        args.workspace,
        args.ffprobe,
        args.project_id,
        args.probe_timeout_seconds,
    )
    try:
        ports = ManualFinalizerPorts.native()
        result = run_manual_finalizer(ports, request)
    except KeyboardInterrupt:
        print("Fehler [cancelled/E_CANCELLED]: manueller Finalizer abgebrochen", file=sys.stderr)
        return 130
    except Exception as exc:
        print(
            f"Fehler [startup/E_MANUAL_FINALIZER_STARTUP]: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if isinstance(result, ManualFinalizationFailed):
        _print_failure(result, sys.stderr)
        return 1
    print(f"Sidecar erfolgreich veröffentlicht und validiert: {result.sidecar_path}")
    print(f"Projekt: {result.project_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
