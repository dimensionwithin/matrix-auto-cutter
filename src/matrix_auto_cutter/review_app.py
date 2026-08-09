"""Small local Tk review application with deliberate approval decisions."""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import re
import secrets
import sys
import threading
import webbrowser
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from matrix_auto_cutter.approval import (
    DecisionFailed,
    inspect_approval_state,
    record_selected_decision,
)
from matrix_auto_cutter.cut_proposal import ProposalFailed, load_proposal
from matrix_auto_cutter.product_runner import (
    default_log_directory,
    default_state_directory,
    load_runner_status,
    runner_health,
    tail_runner_log,
)
from matrix_auto_cutter.render import (
    DEFAULT_RENDER_DIRECTORY,
    RenderAccepted,
    load_render_status,
    submit_render_request,
    target_path_for,
)
from matrix_auto_cutter.review import write_review
from matrix_auto_cutter.selection import (
    SelectionFailed,
    SelectionReady,
    ensure_selection,
    update_selection,
)

# Padding of the single outer content frame, needed twice: once when the frame
# is built and once when the control row's width is turned into a window width.
_FRAME_PADDING = 16
# Height of the scrollable cut list in text lines. The list is the only part of
# the window that gives way, so it carries both numbers: what it asks for when
# the window opens, and how little it may be squeezed to before the window
# refuses to shrink further. Neither depends on how many cuts a recording has -
# a tk.Text never sizes itself to its content.
_LIST_DEFAULT_LINES = 18
_LIST_MINIMUM_LINES = 4
_WINDOW_STATE_FILENAME = "review-window.json"
_GEOMETRY_PATTERN = re.compile(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)")


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    """One Tk window box: size plus position, in pixels."""

    width: int
    height: int
    x: int
    y: int

    def as_geometry(self) -> str:
        """Render the box in Tk's own ``WxH+X+Y`` notation."""
        return f"{self.width}x{self.height}+{self.x}+{self.y}"

    @classmethod
    def parse(cls, value: str) -> WindowGeometry | None:
        """Read Tk's ``WxH+X+Y`` notation, rejecting anything else."""
        match = _GEOMETRY_PATTERN.fullmatch(value.strip())
        if match is None:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return cls(width=width, height=height, x=int(match.group(3)), y=int(match.group(4)))

    def fitted(
        self,
        *,
        minimum: tuple[int, int],
        screen: tuple[int, int],
    ) -> WindowGeometry:
        """Clamp a remembered box so it stays usable on the current screen.

        A box saved on a second monitor, or before the controls grew, must never
        put the window off screen or below the size at which the lower controls
        are cut off again.
        """
        width = max(minimum[0], min(self.width, screen[0]))
        height = max(minimum[1], min(self.height, screen[1]))
        return WindowGeometry(
            width=width,
            height=height,
            x=min(max(self.x, 0), max(screen[0] - width, 0)),
            y=min(max(self.y, 0), max(screen[1] - height, 0)),
        )


def review_window_state_path(directory: Path | None = None) -> Path:
    """Return the window-box file next to the other review state files."""
    return (directory if directory is not None else default_state_directory()) / (
        _WINDOW_STATE_FILENAME
    )


def load_window_geometry(path: Path) -> WindowGeometry | None:
    """Read a remembered window box, treating every defect as "not remembered"."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    values = []
    for key in ("width", "height", "x", "y"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        values.append(value)
    width, height, x, y = values
    if width <= 0 or height <= 0:
        return None
    return WindowGeometry(width=width, height=height, x=x, y=y)


def store_window_geometry(path: Path, geometry: WindowGeometry) -> None:
    """Persist the window box; a failed convenience must never break the review."""
    with suppress(OSError, ValueError):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "width": geometry.width,
                    "height": geometry.height,
                    "x": geometry.x,
                    "y": geometry.y,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)


class _Measurable(Protocol):
    """The slice of the Tk widget API the size derivation actually uses."""

    def winfo_reqwidth(self) -> int: ...

    def winfo_reqheight(self) -> int: ...


class _Root(_Measurable, Protocol):
    def update_idletasks(self) -> None: ...


def measure_window_bounds(
    root: _Root,
    controls: _Measurable,
    set_list_lines: Callable[[int], object],
    padding: int = _FRAME_PADDING,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Derive the smallest and the preferred window box from the real widgets.

    The width floor comes from the control block alone, because that is what has
    to stay readable: its buttons carry fixed captions, so the number is stable
    across recordings. The height floor is the whole window measured while the
    cut list is squeezed to ``_LIST_MINIMUM_LINES`` - everything above and below
    the list has a fixed line count, so what remains is exactly the space the
    labels and the lower controls need.

    The preferred box is the same window with the list back at its full height.
    Both numbers are read off the widgets, so they follow captions, fonts and
    display scaling instead of being guessed once and going stale.
    """
    set_list_lines(_LIST_MINIMUM_LINES)
    root.update_idletasks()
    minimum = (controls.winfo_reqwidth() + 2 * padding, root.winfo_reqheight())
    set_list_lines(_LIST_DEFAULT_LINES)
    root.update_idletasks()
    preferred = (
        max(root.winfo_reqwidth(), minimum[0]),
        max(root.winfo_reqheight(), minimum[1]),
    )
    return minimum, preferred


@dataclass(frozen=True, slots=True)
class ReviewRenderView:
    """Testable UI projection for the render controls."""

    state: str
    message_de: str
    target_path: Path
    render_enabled: bool
    output_enabled: bool
    phase: str | None = None
    encoder_de: str | None = None
    fallback_de: str | None = None
    progress_percent: int | None = None
    elapsed_de: str | None = None
    eta_de: str | None = None
    speed_de: str | None = None
    attempt_de: str | None = None
    verification_de: str | None = None


def _format_milliseconds(value: int | None) -> str:
    """Use a stable neutral marker for legacy or not-yet-measurable values."""
    if value is None:
        return "-"
    seconds = max(0, value // 1000)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def review_render_view(
    proposal_path: Path,
    target_directory: Path = DEFAULT_RENDER_DIRECTORY,
) -> ReviewRenderView:
    """Project current gate and persistent render state into button behavior."""
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        raise ValueError(loaded.message_de)
    gate = inspect_approval_state(proposal_path)
    target = target_path_for(loaded.proposal, target_directory).resolve(strict=False)
    status = load_render_status(proposal_path)
    if status is not None and status.state in {
        "render_running",
        "render_verifying",
        "render_succeeded",
        "render_failed",
    }:
        state = status.state
        message = status.message_de
    else:
        state = "render_ready" if gate.authorized else "render_not_authorized"
        message = gate.reason
    running = state in {"render_running", "render_verifying"}
    succeeded = state == "render_succeeded"
    active_encoder = getattr(status, "active_encoder", None) if status is not None else None
    final_encoder = getattr(status, "final_encoder", None) if status is not None else None
    encoder = final_encoder or active_encoder
    encoder_de = "NVIDIA NVENC" if encoder == "h264_nvenc" else "CPU / libx264" if encoder else None
    fallback_reason = getattr(status, "fallback_reason", None) if status is not None else None
    fallback_de = "nein" if status is not None and not fallback_reason else fallback_reason
    attempt = getattr(status, "encoder_attempt", None) if status is not None else None
    maximum = getattr(status, "max_encoder_attempts", 2) if status is not None else 2
    speed_x = getattr(status, "speed_x", None) if status is not None else None
    return ReviewRenderView(
        state=state,
        message_de=message,
        target_path=Path(status.target_path)
        if status is not None and status.target_path
        else target,
        render_enabled=gate.authorized and not running and not succeeded,
        output_enabled=succeeded,
        phase=getattr(status, "phase", state) if status is not None else state,
        encoder_de=encoder_de,
        fallback_de=fallback_de,
        progress_percent=getattr(status, "progress_percent", None) if status is not None else None,
        elapsed_de=_format_milliseconds(
            getattr(status, "elapsed_total_ms", None) if status is not None else None
        ),
        eta_de=_format_milliseconds(
            getattr(status, "eta_ms", None) if status is not None else None
        ),
        speed_de=f"{speed_x:.2f}x" if speed_x is not None else "-",
        attempt_de=f"{attempt} von {maximum}" if attempt is not None else "-",
        verification_de=(
            getattr(status, "verification_status", "not_run") if status is not None else "not_run"
        ),
    )


class ReviewSingleInstance:
    """Allow at most one Matrix Auto Cutter review application per user."""

    def __init__(self, path: Path | None = None) -> None:
        """Create an unheld lock in the per-user runner state directory."""
        if path is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise RuntimeError("LOCALAPPDATA ist nicht gesetzt")
            path = (
                Path(local)
                / "DimensionWithin"
                / "MatrixAutoCutter"
                / "product-runner"
                / "review.lock"
            )
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Acquire the one-byte lock without opening any GUI."""
        if os.name != "nt":
            raise RuntimeError("Die Review-Anwendung unterstützt nur Windows.")
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
        """Release the lock exactly once."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


class LogViewerSingleInstance:
    """Reserve exactly one local Matrix Auto Cutter log viewer window."""

    def __init__(self, path: Path | None = None) -> None:
        """Create an unheld sibling lock in the fixed runner state directory."""
        if path is None:
            local = os.environ.get("LOCALAPPDATA")
            if not local:
                raise RuntimeError("LOCALAPPDATA ist nicht gesetzt")
            path = (
                Path(local)
                / "DimensionWithin"
                / "MatrixAutoCutter"
                / "product-runner"
                / "log-viewer.lock"
            )
        self.path = path
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Acquire the viewer reservation without opening a GUI."""
        if os.name != "nt":
            raise RuntimeError("Die Review-Anwendung unterstÃ¼tzt nur Windows.")
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
        """Release the viewer reservation exactly once."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


class ReviewSelectionBridge:
    """Proposal-specific loopback bridge for the browser's selection controls."""

    max_request_bytes = 64 * 1024

    def __init__(self, proposal_path: Path) -> None:
        """Prepare an unstarted, random-token bridge for exactly one proposal."""
        self.proposal_path = proposal_path.resolve(strict=True)
        self.token = secrets.token_urlsafe(32)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def api_prefix(self) -> str:
        """Return the only browser-visible endpoint prefix for this bridge."""
        server = self._server
        if server is None:
            raise RuntimeError("Review bridge is not started")
        port = server.server_address[1]
        return f"http://127.0.0.1:{port}/{self.token}"

    def _selection_payload(self) -> tuple[int, dict[str, object]]:
        selected = ensure_selection(self.proposal_path)
        if isinstance(selected, SelectionFailed):
            return 409, {"message": selected.message_de}
        selection = selected.selection
        return 200, {
            "selection_digest": selection.selection_digest,
            "enabled_count": selection.enabled_count,
            "selected_savings_ms": selection.selected_savings_ms,
            "candidates": [
                {"candidate_id": item.candidate_id, "enabled": item.enabled}
                for item in selection.candidates
            ],
        }

    def start(self) -> None:
        """Bind only IPv4 loopback and start one daemon request thread."""
        if self._server is not None:
            return
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                """Avoid leaking browser request details into product logs."""

            def _respond(self, status: int, payload: dict[str, object]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "null")
                self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(data)

            def _authorized_path(self, suffix: str) -> bool:
                return urlsplit(self.path).path == f"/{bridge.token}{suffix}"

            def do_OPTIONS(self) -> None:
                """Permit only the one content type needed by local file review."""
                if not self._authorized_path("/selection"):
                    self._respond(404, {"message": "Unbekannter Review-Endpunkt."})
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "null")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Vary", "Origin")
                self.end_headers()

            def do_GET(self) -> None:
                """Return only the canonical persisted selection state."""
                if not self._authorized_path("/selection"):
                    self._respond(404, {"message": "Unbekannter Review-Endpunkt."})
                    return
                status, payload = bridge._selection_payload()
                self._respond(status, payload)

            def do_POST(self) -> None:
                """Persist an all-candidate boolean selection after strict validation."""
                if not self._authorized_path("/selection"):
                    self._respond(404, {"message": "Unbekannter Review-Endpunkt."})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "-1"))
                except ValueError:
                    length = -1
                if length < 1 or length > bridge.max_request_bytes:
                    self._respond(413, {"message": "Ungültige Request-Größe."})
                    return
                if self.headers.get_content_type() != "application/json":
                    self._respond(415, {"message": "Content-Type muss application/json sein."})
                    return
                try:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict) or set(body) != {
                        "enabled",
                        "expected_selection_digest",
                    }:
                        raise ValueError("body")
                    enabled = body.get("enabled")
                    expected = body.get("expected_selection_digest")
                    if (
                        not isinstance(enabled, dict)
                        or not all(
                            isinstance(key, str) and isinstance(value, bool)
                            for key, value in enabled.items()
                        )
                        or not isinstance(expected, str)
                    ):
                        raise ValueError("shape")
                except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                    self._respond(400, {"message": "Ungültige Auswahl-Anforderung."})
                    return
                result = update_selection(
                    bridge.proposal_path,
                    enabled,
                    expected_selection_digest=expected,
                )
                if isinstance(result, SelectionFailed):
                    _status, payload = bridge._selection_payload()
                    payload["message"] = result.message_de
                    self._respond(409, payload)
                    return
                status, payload = bridge._selection_payload()
                self._respond(status, payload)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server = server
        thread = threading.Thread(target=server.serve_forever, name="matrix-review-bridge")
        thread.daemon = True
        thread.start()
        self._thread = thread

    def close(self) -> None:
        """Stop the listener before the owning review process exits."""
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokale Matrix-Schnittvorschlag-Review")
    parser.add_argument("--proposal", type=Path, required=True)
    return parser


def run_review(proposal_path: Path) -> int:
    """Show one generation and expose separate decision and render actions."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        messagebox.showerror("Matrix Auto Cutter", loaded.message_de)
        return 2
    proposal = loaded.proposal
    persisted_selection = ensure_selection(proposal_path)
    if isinstance(persisted_selection, SelectionFailed):
        messagebox.showerror("Matrix Auto Cutter", persisted_selection.message_de)
        return 2
    bridge = ReviewSelectionBridge(proposal_path)
    try:
        bridge.start()
        review_path = write_review(proposal_path, api_prefix=bridge.api_prefix)
    except (OSError, ValueError) as exc:
        bridge.close()
        messagebox.showerror("Matrix Auto Cutter", f"Review konnte nicht erzeugt werden: {exc}")
        return 2

    root = tk.Tk()
    root.title(f"Matrix Auto Cutter Review - {proposal.source_identity.file_name}")

    frame = ttk.Frame(root, padding=_FRAME_PADDING)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Schnittvorschlag prüfen", font=("Segoe UI", 18, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text=(
            "Nur Vorschlag · Rohaufnahme bleibt unverändert · Noch nicht gerendert · "
            "Schließen bedeutet keine Freigabe"
        ),
        foreground="#137333",
    ).pack(anchor="w", pady=(4, 12))
    ttk.Label(
        frame,
        text=(
            f"{proposal.source_path}\nDauer: {proposal.source_duration_ms / 1000:.3f} s · "
            f"Schnitte: {proposal.total_proposed_cuts} · "
            f"Kürzung: {proposal.total_proposed_savings_ms / 1000:.3f} s\n"
            f"Proposal: {proposal.proposal_id} · {proposal.proposal_digest[:16]}…"
        ),
        justify="left",
    ).pack(anchor="w")

    decision_var = tk.StringVar()
    render_var = tk.StringVar(value="render_not_authorized")
    runner_var = tk.StringVar(value="Runner: Status wird gelesen …")
    target_path = target_path_for(proposal, DEFAULT_RENDER_DIRECTORY).resolve(strict=False)
    render_button: ttk.Button
    open_output_button: ttk.Button
    open_folder_button: ttk.Button
    log_window: tk.Toplevel | None = None
    log_text: tk.Text | None = None
    log_viewer_guard: LogViewerSingleInstance | None = None
    log_content = ""
    selected_index = 0
    selection_var = tk.StringVar()
    selection_summary_var = tk.StringVar()

    def current_selection() -> SelectionReady | None:
        current = ensure_selection(proposal_path)
        return current if isinstance(current, SelectionReady) else None

    def refresh_selection() -> None:
        nonlocal persisted_selection
        current = current_selection()
        if current is None:
            selection_var.set("Auswahl ist ungültig")
            return
        persisted_selection = current
        if not proposal.proposed_cuts:
            selection_var.set("Keine renderbaren Schnitte")
            selection_summary_var.set("0 aktiviert · 0 deaktiviert")
            return
        item = proposal.proposed_cuts[selected_index]
        enabled = current.selection.candidates[selected_index].enabled
        selection_var.set(
            f"Schnitt {selected_index + 1} von {len(proposal.proposed_cuts)} · "
            f"{item.candidate_id}\n"
            f"Start: {item.start_timecode} · Ende: {item.end_timecode} · "
            f"Dauer: {item.duration_ms / 1000:.3f} s · "
            f"Status: {'aktiviert' if enabled else 'deaktiviert'}"
        )
        selection_summary_var.set(
            f"{current.selection.enabled_count} aktiviert · "
            f"{len(current.selection.candidates) - current.selection.enabled_count} deaktiviert · "
            f"Kürzung {current.selection.selected_savings_ms / 1000:.3f} s · "
            f"Ausgabe "
            f"{(proposal.source_duration_ms-current.selection.selected_savings_ms)/1000:.3f} s · "
            f"Selection {current.selection.selection_digest[:16]}…"
        )

    def refresh_runner_status() -> None:
        status = load_runner_status()
        health = runner_health(status)
        labels = {
            "active": "AKTIV",
            "starting": "STARTET",
            "not_reachable": "NICHT ERREICHBAR",
            "stale": "STATUS VERALTET",
        }
        detail = health.message_de
        if status is not None:
            detail += f" PID: {status.runner_pid}; letzter Status: {status.last_status_code.value}."
            if status.last_error_code and status.last_error_message_de:
                detail += (
                    f" Letzter Fehler {status.last_error_code}: {status.last_error_message_de}"
                )
        runner_var.set(f"Runner: {labels[health.state]} - {detail}")

    def refresh_status() -> None:
        gate = inspect_approval_state(proposal_path)
        labels = {
            "pending": "NOCH KEINE ENTSCHEIDUNG",
            "approved": "FREIGEGEBEN" if gate.authorized else "FREIGEGEBEN (kein Schnitt)",
            "rejected": "ABGELEHNT",
            "selected_cuts_approved": "AUSGEWÄHLTE SCHNITTE FREIGEGEBEN",
            "all_rejected": "ALLE SCHNITTE ABGELEHNT",
        }
        decision_var.set(f"Status: {labels[gate.decision]} - {gate.reason}")
        view = review_render_view(proposal_path)
        render_button.configure(state="normal" if view.render_enabled else "disabled")
        render_var.set(
            f"Phase: {view.phase}\n"
            f"Encoder: {view.encoder_de or '-'}\n"
            f"Versuch: {view.attempt_de}\n"
            f"Fallback: {view.fallback_de or '-'}\n"
            f"Fortschritt: "
            f"{str(view.progress_percent) + ' %' if view.progress_percent is not None else '-'}\n"
            f"Vergangen: {view.elapsed_de}\n"
            f"Restzeit: {view.eta_de}\n"
            f"Geschwindigkeit: {view.speed_de}\n"
            f"Verifikation: {view.verification_de}\n"
            f"Render: {view.state} - {view.message_de}\nZiel: {view.target_path}"
        )
        output_state = "normal" if view.output_enabled else "disabled"
        open_output_button.configure(state=output_state)
        open_folder_button.configure(state=output_state)
        refresh_selection()
        refresh_runner_status()

    ttk.Label(frame, textvariable=decision_var, font=("Segoe UI", 11, "bold")).pack(
        anchor="w", pady=(12, 8)
    )
    ttk.Label(frame, textvariable=render_var, justify="left").pack(anchor="w", pady=(0, 8))
    ttk.Label(frame, textvariable=runner_var, justify="left").pack(anchor="w", pady=(0, 8))
    # The controls claim their parcel from the bottom edge before the cut list is
    # packed, so pack hands the list only what is left over. Shrinking the window
    # therefore eats into the list and never into the buttons below it - which is
    # how the lower two blocks used to disappear on open.
    controls = ttk.Frame(frame)
    controls.pack(side="bottom", fill="x")
    text = tk.Text(frame, wrap="word", height=_LIST_DEFAULT_LINES)
    text.pack(fill="both", expand=True)
    if proposal.proposed_cuts:
        for index, item in enumerate(proposal.proposed_cuts, start=1):
            # Drei Schnittarten, drei Zeilen. Der Intro-Lead-in trägt wie der
            # Outro-Tail kein audio_evidence und stand deshalb bis zum 09.08.2026
            # fälschlich unter der Outro-Beschriftung. Der Szenenname kommt als
            # freier Text aus OBS und wird deshalb über !r gesetzt: das klammert
            # ihn sichtbar ein und neutralisiert Steuerzeichen, die die Zeile
            # sonst im Text-Widget zerreißen würden.
            if item.audio_evidence is not None:
                evidence = (
                    f"Konservative Stille; Evidence "
                    f"{item.audio_evidence.raw_silence_start_ms / 1000:.3f} bis "
                    f"{item.audio_evidence.raw_silence_end_ms / 1000:.3f} s bei "
                    f"{item.audio_evidence.threshold_db} dB; Protection frei."
                )
            elif item.intro_evidence is not None:
                marker = item.intro_evidence.scene_name or "Intro-Szene (UUID-Bindung)"
                evidence = (
                    f"Intro-Lead-in vor der Szenenmarke {marker!r}; "
                    f"{item.intro_evidence.removed_ms / 1000:.3f} s entfernt "
                    f"({item.intro_evidence.removed_frames} Frames) bis Sourceframe "
                    f"{item.intro_evidence.intro_start_frame}."
                )
            else:
                evidence = "Exakter Outro-Tail nach 900 geschützten Sourceframes."
            text.insert(
                "end",
                f"{index}. {item.start_timecode} bis {item.end_timecode} "
                f"({item.duration_ms / 1000:.3f} s)\n"
                f"   {evidence}\n\n",
            )
    else:
        text.insert("end", "Keine zeitentfernenden Schnitte vorgeschlagen.\n")
    if proposal.rejection_counts:
        text.insert("end", "Verworfen:\n")
        for rejection in proposal.rejection_counts:
            text.insert("end", f"  {rejection.reason}: {rejection.count}\n")
    text.configure(state="disabled")

    selection_box = ttk.LabelFrame(controls, text="Selektive Cut-Auswahl", padding=8)
    selection_box.pack(fill="x", pady=(8, 0))
    ttk.Label(selection_box, textvariable=selection_var, justify="left").pack(anchor="w")
    ttk.Label(selection_box, textvariable=selection_summary_var, justify="left").pack(anchor="w")

    def update_enabled(value: bool | None = None, *, all_cuts: bool = False) -> None:
        nonlocal selected_index
        current = current_selection()
        if current is None:
            messagebox.showerror("Auswahl", "Auswahl konnte nicht gelesen werden.", parent=root)
            return
        enabled = {item.candidate_id: item.enabled for item in current.selection.candidates}
        if all_cuts:
            enabled = {candidate_id: bool(value) for candidate_id in enabled}
        elif proposal.proposed_cuts:
            candidate_id = proposal.proposed_cuts[selected_index].candidate_id
            enabled[candidate_id] = not enabled[candidate_id] if value is None else value
        result = update_selection(
            proposal_path,
            enabled,
            expected_selection_digest=current.selection.selection_digest,
        )
        if isinstance(result, SelectionFailed):
            messagebox.showerror("Auswahl", result.message_de, parent=root)
            return
        with suppress(OSError, ValueError):
            write_review(proposal_path, api_prefix=bridge.api_prefix)
        refresh_status()

    def navigate(delta: int) -> None:
        nonlocal selected_index
        if proposal.proposed_cuts:
            selected_index = min(max(0, selected_index + delta), len(proposal.proposed_cuts) - 1)
        refresh_selection()

    selection_buttons = ttk.Frame(selection_box)
    selection_buttons.pack(fill="x", pady=(5, 0))
    previous_button = ttk.Button(
        selection_buttons, text="← Vorheriger Schnitt", command=lambda: navigate(-1)
    )
    previous_button.pack(side="left")
    next_button = ttk.Button(
        selection_buttons, text="Nächster Schnitt →", command=lambda: navigate(1)
    )
    next_button.pack(side="left", padx=5)
    toggle_button = ttk.Button(
        selection_buttons, text="Cut aktivieren/deaktivieren", command=update_enabled
    )
    toggle_button.pack(side="left")
    all_enabled_button = ttk.Button(
        selection_buttons,
        text="Alle aktivieren",
        command=lambda: update_enabled(True, all_cuts=True),
    )
    all_enabled_button.pack(side="left", padx=5)
    all_disabled_button = ttk.Button(
        selection_buttons,
        text="Alle deaktivieren",
        command=lambda: update_enabled(False, all_cuts=True),
    )
    all_disabled_button.pack(side="left")

    buttons = ttk.Frame(controls)
    buttons.pack(fill="x", pady=(12, 0))

    def open_html() -> None:
        webbrowser.open(review_path.as_uri(), new=2)

    def decide_selected(value: Literal["selected_cuts_approved", "all_rejected"]) -> None:
        label = (
            "ausgewählten Schnitte freigeben"
            if value == "selected_cuts_approved"
            else "alle Schnitte ablehnen"
        )
        if not messagebox.askyesno(
            "Entscheidung bestätigen",
            f"Diesen vollständigen, digestgebundenen Vorschlag wirklich {label}?",
            parent=root,
        ):
            return
        result = record_selected_decision(proposal_path, value)
        if isinstance(result, DecisionFailed):
            messagebox.showerror("Entscheidung fehlgeschlagen", result.message_de, parent=root)
            return
        write_review(proposal_path)
        refresh_status()
        messagebox.showinfo(
            "Entscheidung gespeichert",
            "Ausgewählte Schnitte wurden atomar gespeichert und freigegeben."
            if value == "selected_cuts_approved"
            else "Ablehnung wurde atomar gespeichert; sie autorisiert keinen Render.",
            parent=root,
        )

    def request_render() -> None:
        if not messagebox.askyesno(
            "Finalen Render starten",
            f"Freigegebenen Vorschlag jetzt als neue MP4 rendern?\n\nZiel: {target_path}",
            parent=root,
        ):
            return
        result = submit_render_request(proposal_path, DEFAULT_RENDER_DIRECTORY)
        if not isinstance(result, RenderAccepted):
            messagebox.showerror("Render verweigert", result.message_de, parent=root)
        refresh_status()

    def open_output() -> None:
        status = load_render_status(proposal_path)
        if status is not None and status.state == "render_succeeded" and status.target_path:
            os.startfile(status.target_path)

    def open_folder() -> None:
        status = load_render_status(proposal_path)
        if status is not None and status.state == "render_succeeded" and status.target_path:
            os.startfile(str(Path(status.target_path).parent))

    def close_log_window() -> None:
        nonlocal log_window, log_text, log_viewer_guard
        window = log_window
        log_window = None
        log_text = None
        if log_viewer_guard is not None:
            log_viewer_guard.close()
            log_viewer_guard = None
        if window is not None and window.winfo_exists():
            window.destroy()

    def refresh_log_window() -> None:
        nonlocal log_content
        window = log_window
        text_widget = log_text
        if window is None or text_widget is None or not window.winfo_exists():
            return
        current = tail_runner_log()
        if current != log_content:
            log_content = current
            text_widget.configure(state="normal")
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", current)
            text_widget.see("end")
            text_widget.configure(state="disabled")
        window.after(1000, refresh_log_window)

    def show_log() -> None:
        nonlocal log_window, log_text, log_viewer_guard, log_content
        if log_window is not None and log_window.winfo_exists():
            log_window.deiconify()
            log_window.lift()
            log_window.focus_force()
            return
        guard = LogViewerSingleInstance()
        if not guard.acquire():
            messagebox.showinfo(
                "Protokoll bereits geöffnet",
                (
                    "Das Matrix-Auto-Cutter-Protokoll ist bereits in einem anderen "
                    "Review-Fenster geöffnet."
                ),
                parent=root,
            )
            return
        log_viewer_guard = guard
        window = tk.Toplevel(root)
        log_window = window
        window.title("Matrix Auto Cutter - Protokoll")
        window.geometry("980x620")
        window.minsize(680, 360)
        window.protocol("WM_DELETE_WINDOW", close_log_window)
        body = ttk.Frame(window, padding=12)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Lokales Runner-Protokoll: {default_log_directory()}").pack(
            anchor="w"
        )
        text_widget = tk.Text(body, wrap="none", height=26)
        text_widget.pack(fill="both", expand=True, pady=(8, 0))
        text_widget.configure(state="disabled")
        log_text = text_widget
        log_content = ""
        refresh_log_window()

    def open_log_folder() -> None:
        directory = default_log_directory()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            os.startfile(str(directory))
        except OSError as exc:
            messagebox.showerror(
                "Protokollordner", f"Ordner konnte nicht geöffnet werden: {exc}", parent=root
            )

    def poll_render() -> None:
        refresh_status()
        root.after(750, poll_render)

    ttk.Button(buttons, text="HTML-Review mit Videosprüngen öffnen", command=open_html).pack(
        side="left"
    )
    ttk.Button(buttons, text="Protokoll anzeigen", command=show_log).pack(side="left", padx=8)
    ttk.Button(buttons, text="Protokollordner öffnen", command=open_log_folder).pack(side="left")
    ttk.Button(
        buttons,
        text="Alle Schnitte ablehnen",
        command=lambda: decide_selected("all_rejected"),
    ).pack(side="right")
    ttk.Button(
        buttons,
        text="Ausgewählte Schnitte freigeben",
        command=lambda: decide_selected("selected_cuts_approved"),
    ).pack(side="right", padx=8)
    render_controls = ttk.Frame(controls)
    render_controls.pack(fill="x", pady=(10, 0))
    render_button = ttk.Button(
        render_controls,
        text="Final rendern",
        command=request_render,
        state="disabled",
    )
    render_button.pack(side="left")
    open_output_button = ttk.Button(
        render_controls,
        text="Ausgabe öffnen",
        command=open_output,
        state="disabled",
    )
    open_output_button.pack(side="left", padx=8)
    open_folder_button = ttk.Button(
        render_controls,
        text="Ordner öffnen",
        command=open_folder,
        state="disabled",
    )
    open_folder_button.pack(side="left")
    # Fill every label before measuring: the render block alone is eleven lines
    # and is empty until the first refresh, so measuring earlier would floor the
    # window below the height its own contents need.
    refresh_status()
    minimum, preferred = measure_window_bounds(
        root, controls, lambda lines: text.configure(height=lines)
    )
    root.minsize(*minimum)
    screen = (root.winfo_screenwidth(), root.winfo_screenheight())
    state_path = review_window_state_path()
    remembered = load_window_geometry(state_path)
    if remembered is not None:
        root.geometry(remembered.fitted(minimum=minimum, screen=screen).as_geometry())
    else:
        # No stored box yet: open at the size the content asks for, capped by the
        # screen. Position stays with the window manager.
        root.geometry(
            f"{max(min(preferred[0], screen[0]), minimum[0])}"
            f"x{max(min(preferred[1], screen[1]), minimum[1])}"
        )

    def remember_window() -> None:
        with suppress(tk.TclError):
            store_window_geometry(
                state_path,
                WindowGeometry(
                    width=root.winfo_width(),
                    height=root.winfo_height(),
                    x=root.winfo_x(),
                    y=root.winfo_y(),
                ),
            )

    def close_review() -> None:
        remember_window()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_review)
    root.after(750, poll_render)
    root.mainloop()
    bridge.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by the product runner."""
    args = _parser().parse_args(argv)
    guard = ReviewSingleInstance()
    try:
        if not guard.acquire():
            return 3
        return run_review(args.proposal.resolve(strict=True))
    except Exception as exc:
        print(f"Review-Startfehler: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
