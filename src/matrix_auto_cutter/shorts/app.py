"""Lokales Tk-Fenster: Liste der gerenderten Shorts-Quellen plus ein Knopf.

Reines Tk, keine HTTP-Brücke. Für achtzehn Zeilen mit je einem Knopf gibt es
keinen Sprung ins Video und keine Live-Auswahl wie im Review-Fenster - das
einzige, was eine Brücke rechtfertigen würde. Übernommen aus dem Muster von
``review_app`` sind Standardbibliothek-Tk, eine Einzelinstanz-Sperrdatei
(eigener Name, siehe :class:`ShortsSingleInstance`), gemerkte
Fenstergeometrie und atomares Schreiben (:mod:`matrix_auto_cutter.shorts.job`).
"""

from __future__ import annotations

import argparse
import json
import msvcrt
import os
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.product_runner import default_state_directory
from matrix_auto_cutter.shorts.inventory import VideoRow, build_inventory
from matrix_auto_cutter.shorts.job import build_job_payload, job_output_path, write_job

_WINDOW_STATE_FILENAME = "shorts-window.json"
_GEOMETRY_PATTERN = re.compile(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)")
JOBS_ROOT = Path("artefakte") / "repeat" / "shorts"


@dataclass(frozen=True, slots=True)
class WindowGeometry:
    """Ein Tk-Fensterrechteck: Größe plus Position, in Pixeln."""

    width: int
    height: int
    x: int
    y: int

    def as_geometry(self) -> str:
        """Gib das Rechteck in Tks ``WxH+X+Y``-Notation aus."""
        return f"{self.width}x{self.height}+{self.x}+{self.y}"

    @classmethod
    def parse(cls, value: str) -> WindowGeometry | None:
        """Lies Tks ``WxH+X+Y``-Notation, verwirf alles andere."""
        match = _GEOMETRY_PATTERN.fullmatch(value.strip())
        if match is None:
            return None
        width, height = int(match.group(1)), int(match.group(2))
        if width <= 0 or height <= 0:
            return None
        return cls(width=width, height=height, x=int(match.group(3)), y=int(match.group(4)))

    def fitted(self, *, minimum: tuple[int, int], screen: tuple[int, int]) -> WindowGeometry:
        """Klemme ein gemerktes Rechteck auf den aktuellen Bildschirm."""
        width = max(minimum[0], min(self.width, screen[0]))
        height = max(minimum[1], min(self.height, screen[1]))
        return WindowGeometry(
            width=width,
            height=height,
            x=min(max(self.x, 0), max(screen[0] - width, 0)),
            y=min(max(self.y, 0), max(screen[1] - height, 0)),
        )


def window_state_path(directory: Path | None = None) -> Path:
    """Eigene Fenster-Rechteck-Datei, getrennt von der Review-Anwendung."""
    return (directory if directory is not None else default_state_directory()) / (
        _WINDOW_STATE_FILENAME
    )


def load_window_geometry(path: Path) -> WindowGeometry | None:
    """Lies ein gemerktes Fensterrechteck; jeder Defekt heißt "nicht gemerkt"."""
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
    """Persistiere das Fensterrechteck; ein Fehler darf das Werkzeug nie stoppen."""
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
        replace_atomically(temporary, path)


class ShortsSingleInstance:
    """Erlaube höchstens ein Shorts-Werkzeug pro Benutzer, eigene Sperrdatei."""

    def __init__(self, path: Path | None = None) -> None:
        """Bereite eine ungehaltene Sperre vor; berührt niemals ``review.lock``."""
        self.path = path if path is not None else default_state_directory() / "shorts-tool.lock"
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Erwerbe die Ein-Byte-Sperre ohne eine GUI zu öffnen."""
        if os.name != "nt":
            raise RuntimeError("Das Shorts-Werkzeug unterstützt nur Windows.")
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
        """Gib die Sperre genau einmal frei."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


def _format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "Dauer unbekannt"
    seconds = duration_ms // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _format_exists(label: str, path: Path, exists: bool) -> str:
    mark = "vorhanden" if exists else "FEHLT"
    return f"{label}: {mark} - {path}"


def _row_summary(row: VideoRow) -> str:
    lines = [
        f"{row.name}  ({_format_duration(row.duration_ms)})",
        _format_exists("Rohaufnahme", row.raw_path, row.raw_exists),
        _format_exists("Sidecar", row.sidecar_path, row.sidecar_exists),
    ]
    proposal = row.proposal
    if proposal.unclear:
        lines.append(
            f"Proposal: UNGEKLÄRT ({proposal.candidate_count} freigegebene Kandidaten, "
            "keine eindeutige Auswahl möglich)"
        )
    elif proposal.proposal_path is None:
        lines.append("Proposal: nicht gefunden")
    else:
        ambiguity = (
            f" (mehrdeutig, {proposal.candidate_count} freigegebene Proposals, jüngstes gewählt)"
            if proposal.ambiguous
            else ""
        )
        lines.append(
            f"Proposal Schema {proposal.schema_version}{ambiguity}: {proposal.proposal_path}"
        )
    avatar = row.avatar
    if avatar.path is None:
        lines.append("Avatar: nicht gefunden")
    elif avatar.match_kind == "exact":
        lines.append(f"Avatar: {avatar.path}")
    elif avatar.match_kind == "offset_guess":
        lines.append(f"Avatar (Zeitversatz {avatar.offset_seconds:+d} s, geraten): {avatar.path}")
    else:
        lines.append(f"Avatar (außerhalb des Musters, Root-Pfad, geraten): {avatar.path}")
    cursor = row.cursor
    if cursor.path is None:
        lines.append("Cursorprotokoll: nicht gefunden")
    elif cursor.lead_seconds is None:
        lines.append(
            f"Cursorprotokoll (Vorlauf nicht bestimmbar): {cursor.path}"
        )
    else:
        lines.append(
            f"Cursorprotokoll (Vorlauf {cursor.lead_seconds} s, geraten): {cursor.path}"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Matrix Auto Cutter - Shorts-Werkzeug Stufe 0")
    parser.add_argument("--jobs-root", type=Path, default=JOBS_ROOT)
    return parser


def run_app(jobs_root: Path = JOBS_ROOT) -> int:
    """Zeige die Liste und biete je Video einen Knopf zum Auftrag-Schreiben."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    state_directory = default_state_directory()
    rows = build_inventory(
        sessions_dir=state_directory / "sessions",
        artifacts_dir=state_directory / "artifacts",
    )

    root = tk.Tk()
    root.title("Matrix Auto Cutter - Shorts-Werkzeug (Stufe 0)")

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(
        outer,
        text=f"{len(rows)} gerenderte Videos - reine Bestandsaufnahme, kein Rendern, kein Schnitt.",
        font=("Segoe UI", 12, "bold"),
    ).pack(anchor="w", pady=(0, 8))

    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    list_frame = ttk.Frame(canvas)
    list_frame.bind(
        "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    canvas.create_window((0, 0), window=list_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def on_mouse_wheel(event: tk.Event) -> None:
        canvas.yview_scroll(-1 * (event.delta // 120), "units")

    canvas.bind_all("<MouseWheel>", on_mouse_wheel)

    def on_write(row: VideoRow, status_var: tk.StringVar) -> None:
        target = job_output_path(jobs_root, row.name)
        overwrite = False
        if target.exists():
            overwrite = messagebox.askyesno(
                "Auftragsdatei existiert bereits",
                f"{target}\n\nexistiert schon. Wirklich überschreiben?",
                parent=root,
            )
            if not overwrite:
                status_var.set("Nicht geschrieben (Nutzer hat abgelehnt).")
                return
        payload = build_job_payload(row, created_at=datetime.now(UTC).isoformat())
        try:
            write_job(target, payload, overwrite=overwrite)
        except OSError as exc:
            messagebox.showerror(
                "Schreiben fehlgeschlagen", f"{target} konnte nicht geschrieben werden: {exc}",
                parent=root,
            )
            status_var.set(f"Fehlgeschlagen: {exc}")
            return
        status_var.set(f"Geschrieben: {target}")

    def make_write_handler(row: VideoRow, status_var: tk.StringVar) -> Callable[[], None]:
        return lambda: on_write(row, status_var)

    for row in rows:
        row_box = ttk.LabelFrame(list_frame, text=row.name, padding=8)
        row_box.pack(fill="x", padx=4, pady=4)
        ttk.Label(row_box, text=_row_summary(row), justify="left").pack(anchor="w")
        status_var = tk.StringVar(value="")
        controls = ttk.Frame(row_box)
        controls.pack(fill="x", pady=(6, 0))
        ttk.Button(
            controls,
            text="Auftragsdatei schreiben",
            command=make_write_handler(row, status_var),
        ).pack(side="left")
        ttk.Label(controls, textvariable=status_var, justify="left").pack(side="left", padx=8)

    if not rows:
        ttk.Label(list_frame, text="Keine gerenderten Videos gefunden.").pack(anchor="w")

    root.update_idletasks()
    minimum = (max(root.winfo_reqwidth(), 640), 300)
    root.minsize(*minimum)
    screen = (root.winfo_screenwidth(), root.winfo_screenheight())
    state_path = window_state_path()
    remembered = load_window_geometry(state_path)
    if remembered is not None:
        root.geometry(remembered.fitted(minimum=minimum, screen=screen).as_geometry())
    else:
        preferred = (min(root.winfo_reqwidth(), screen[0]), min(800, screen[1]))
        root.geometry(f"{max(preferred[0], minimum[0])}x{max(preferred[1], minimum[1])}")

    def close_app() -> None:
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
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_app)
    root.mainloop()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-Einstiegspunkt, analog zu ``matrix-auto-review``."""
    args = _parser().parse_args(argv)
    guard = ShortsSingleInstance()
    try:
        if not guard.acquire():
            print("Ein Shorts-Werkzeug läuft bereits für diesen Benutzer.", file=sys.stderr)
            return 3
        return run_app(args.jobs_root)
    except Exception as exc:
        print(f"Shorts-Werkzeug-Startfehler: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
