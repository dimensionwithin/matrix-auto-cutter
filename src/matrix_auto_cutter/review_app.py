"""Small local Tk review application with deliberate approval decisions."""

from __future__ import annotations

import argparse
import msvcrt
import os
import sys
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from matrix_auto_cutter.approval import (
    DecisionFailed,
    check_render_authorization,
    record_decision,
)
from matrix_auto_cutter.cut_proposal import ProposalFailed, load_proposal
from matrix_auto_cutter.review import write_review


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lokale Matrix-Schnittvorschlag-Review")
    parser.add_argument("--proposal", type=Path, required=True)
    return parser


def run_review(proposal_path: Path) -> int:
    """Show one generation and expose only explicit approve/reject actions."""
    import tkinter as tk
    from tkinter import messagebox, ttk

    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        messagebox.showerror("Matrix Auto Cutter", loaded.message_de)
        return 2
    proposal = loaded.proposal
    try:
        review_path = write_review(proposal_path)
    except (OSError, ValueError) as exc:
        messagebox.showerror("Matrix Auto Cutter", f"Review konnte nicht erzeugt werden: {exc}")
        return 2

    root = tk.Tk()
    root.title(f"Matrix Auto Cutter Review - {proposal.source_identity.file_name}")
    root.geometry("980x720")
    root.minsize(760, 520)

    frame = ttk.Frame(root, padding=16)
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

    def refresh_status() -> None:
        gate = check_render_authorization(proposal_path)
        labels = {
            "pending": "NOCH KEINE ENTSCHEIDUNG",
            "approved": "FREIGEGEBEN" if gate.authorized else "FREIGEGEBEN (kein Schnitt)",
            "rejected": "ABGELEHNT",
        }
        decision_var.set(f"Status: {labels[gate.decision]} - {gate.reason}")

    ttk.Label(frame, textvariable=decision_var, font=("Segoe UI", 11, "bold")).pack(
        anchor="w", pady=(12, 8)
    )
    text = tk.Text(frame, wrap="word", height=18)
    text.pack(fill="both", expand=True)
    if proposal.proposed_cuts:
        for index, item in enumerate(proposal.proposed_cuts, start=1):
            text.insert(
                "end",
                f"{index}. {item.start_timecode} bis {item.end_timecode} "
                f"({item.duration_ms / 1000:.3f} s)\n"
                f"   Konservative Stille; Evidence "
                f"{item.audio_evidence.raw_silence_start_ms / 1000:.3f} bis "
                f"{item.audio_evidence.raw_silence_end_ms / 1000:.3f} s bei "
                f"{item.audio_evidence.threshold_db} dB; Protection frei.\n\n",
            )
    else:
        text.insert("end", "Keine zeitentfernenden Schnitte vorgeschlagen.\n")
    if proposal.rejection_counts:
        text.insert("end", "Verworfen:\n")
        for rejection in proposal.rejection_counts:
            text.insert("end", f"  {rejection.reason}: {rejection.count}\n")
    text.configure(state="disabled")

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))

    def open_html() -> None:
        webbrowser.open(review_path.as_uri(), new=2)

    def decide(value: Literal["approved", "rejected"]) -> None:
        label = "freigeben" if value == "approved" else "ablehnen"
        if not messagebox.askyesno(
            "Entscheidung bestätigen",
            f"Diesen vollständigen, digestgebundenen Vorschlag wirklich {label}?",
            parent=root,
        ):
            return
        result = record_decision(proposal_path, value)
        if isinstance(result, DecisionFailed):
            messagebox.showerror("Entscheidung fehlgeschlagen", result.message_de, parent=root)
            return
        write_review(proposal_path)
        refresh_status()
        messagebox.showinfo(
            "Entscheidung gespeichert",
            "Freigabe wurde atomar gespeichert."
            if value == "approved"
            else "Ablehnung wurde atomar gespeichert; sie autorisiert keinen Render.",
            parent=root,
        )

    ttk.Button(buttons, text="HTML-Review mit Videosprüngen öffnen", command=open_html).pack(
        side="left"
    )
    ttk.Button(
        buttons,
        text="Vorschlag ablehnen",
        command=lambda: decide("rejected"),
    ).pack(side="right")
    ttk.Button(
        buttons,
        text="Vorschlag freigeben",
        command=lambda: decide("approved"),
    ).pack(side="right", padx=8)
    refresh_status()
    root.mainloop()
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
