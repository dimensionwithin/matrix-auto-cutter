"""Kopfloser Weg zu ``shorts-job.json`` - Stufe 0 ohne Tk-Fenster.

``app.py`` kann dieselbe Datei schreiben, verlangt dafuer aber ein Fenster,
einen Mausklick auf die richtige Zeile und eine Rueckfrage im Dialog. Der
Kettenlaeufer hat nichts davon. Dieses Modul nimmt denselben Bestand
(:func:`matrix_auto_cutter.shorts.inventory.build_inventory`) und denselben
Inhalt (:func:`matrix_auto_cutter.shorts.job.build_job_payload`), ersetzt
aber die Zeilenauswahl durch ``--aufnahme`` (ohne Angabe: die juengste
Aufnahme) und die Ueberschreib-Rueckfrage durch ``--force``.

Es startet nichts, rendert nichts und beurteilt nichts - es schreibt genau
eine Datei, und nur dann, wenn jedes Pflichtfeld gefuellt ist.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.product_runner import default_state_directory
from matrix_auto_cutter.shorts.inventory import VideoRow, build_inventory, parse_name_timestamp
from matrix_auto_cutter.shorts.job import build_job_payload, job_output_path

JOBS_ROOT = Path("artefakte") / "repeat" / "shorts"

CODE_ERFOLG = 0
CODE_KEINE_AUFNAHME = 2
CODE_ZUSTAND_UNBEKANNT = 3
CODE_PFLICHTFELD = 4

# Pfade in die Auftragsdatei, die gefuellt sein MUESSEN. Alles andere darf
# fehlen und sagt das ueber sich selbst: ``avatar.path`` ist bei
# ``match_kind == "none"`` regulaer leer, ``offset_seconds`` regulaer null.
# Diese hier duerfen es nicht sein - eine Auftragsdatei ohne
# ``rendered_video.duration_ms`` (kein ffprobe gefunden) sieht von aussen
# fertig aus und faellt erst drei Stufen spaeter auf.
PFLICHTFELDER: tuple[str, ...] = (
    "artifact_type",
    "schema_version",
    "video_name",
    "created_at",
    "rendered_video.path",
    "rendered_video.duration_ms",
    "raw_recording.path",
    "sidecar.path",
    "proposal.recording_id",
    "proposal.path",
    "proposal.schema_version",
)


class AuftragFehlschlag(Exception):
    """Ein benannter Abbruchgrund samt Rueckgabecode, wie in ``build.py``."""

    def __init__(self, code_name: str, text: str, rueckgabecode: int) -> None:
        """Halte Kurzname, deutschen Text und Rueckgabecode zusammen fest."""
        super().__init__(text)
        self.code_name = code_name
        self.text = text
        self.rueckgabecode = rueckgabecode


def sammle_aufnahmen(*, probe_duration: bool = True) -> list[VideoRow]:
    """Sammle den Bestand ueber ``build_inventory`` - ohne jede Tk-Beteiligung.

    Das Zustandsverzeichnis kommt aus ``product_runner`` und wird nur
    gelesen; geschrieben wird dort nichts.
    """
    try:
        zustand = default_state_directory()
    except RuntimeError as fehler:
        raise AuftragFehlschlag(
            "zustand_unbekannt",
            f"Zustandsverzeichnis nicht ermittelbar: {fehler}",
            CODE_ZUSTAND_UNBEKANNT,
        ) from fehler
    return build_inventory(
        sessions_dir=zustand / "sessions",
        artifacts_dir=zustand / "artifacts",
        probe_duration=probe_duration,
    )


def _sortierschluessel(row: VideoRow) -> tuple[datetime, str]:
    """Ordne nach dem Zeitstempel im Namen; namenlose Zeiten ganz nach hinten."""
    zeitpunkt = parse_name_timestamp(row.name)
    return (zeitpunkt if zeitpunkt is not None else datetime.min, row.name)


def waehle_aufnahme(zeilen: Sequence[VideoRow], name: str | None) -> VideoRow:
    """Waehle die benannte Aufnahme, ohne Namen die juengste.

    "Juengste" heisst: der spaeteste Zeitstempel im Dateinamen, nicht die
    erste oder letzte Zeile der Liste. ``build_inventory`` sortiert nach
    Name, was bei diesem Namensformat meist dasselbe ergibt - aber eben nur
    meist, und der Kettenlaeufer soll sich darauf nicht verlassen muessen.
    """
    if not zeilen:
        raise AuftragFehlschlag(
            "keine_aufnahmen",
            "keine gerenderten Aufnahmen gefunden",
            CODE_KEINE_AUFNAHME,
        )
    if name is None:
        return max(zeilen, key=_sortierschluessel)
    for row in zeilen:
        if row.name == name:
            return row
    raise AuftragFehlschlag(
        "aufnahme_unbekannt",
        f"{name} ist unter den {len(zeilen)} gefundenen Aufnahmen nicht dabei",
        CODE_KEINE_AUFNAHME,
    )


def zielpfad(row: VideoRow, jobs_root: Path = JOBS_ROOT) -> Path:
    """Der Ort der Auftragsdatei unter ``artefakte/repeat/shorts/<name>/``."""
    return job_output_path(jobs_root, row.name)


def _wert(payload: dict[str, object], feld: str) -> object:
    """Lies einen Punktpfad wie ``rendered_video.duration_ms`` aus dem Inhalt."""
    aktuell: object = payload
    for teil in feld.split("."):
        if not isinstance(aktuell, dict):
            return None
        aktuell = aktuell.get(teil)
    return aktuell


def fehlende_pflichtfelder(payload: dict[str, object]) -> list[str]:
    """Nenne jedes Pflichtfeld, das fehlt oder leer ist.

    ``False`` und ``0`` gelten als gesetzt - nur ``None`` und die leere
    Zeichenkette gelten als fehlend.
    """
    fehlend: list[str] = []
    for feld in PFLICHTFELDER:
        wert = _wert(payload, feld)
        if wert is None or (isinstance(wert, str) and not wert.strip()):
            fehlend.append(feld)
    return fehlend


def schreibe_auftrag(pfad: Path, payload: dict[str, object]) -> None:
    """Schreibe die Auftragsdatei atomar - Vorbild ``write_transcript``.

    Temporaerdatei im Zielverzeichnis, ``flush`` plus ``fsync``, dann
    atomarer Tausch. Ein abgebrochener Lauf hinterlaesst damit entweder die
    alte Datei oder die vollstaendige neue, nie eine halbe.
    """
    pfad.parent.mkdir(parents=True, exist_ok=True)
    daten = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporaer: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{pfad.name}.tmp.",
            dir=pfad.parent,
            delete=False,
        ) as griff:
            temporaer = Path(griff.name)
            griff.write(daten)
            griff.flush()
            os.fsync(griff.fileno())
        replace_atomically(temporaer, pfad, create_only=False)
    finally:
        if temporaer is not None and temporaer.exists():
            temporaer.unlink(missing_ok=True)


def _liste_ausgeben(zeilen: Sequence[VideoRow]) -> None:
    """Gib die gefundenen Aufnahmen samt Datum aus; schreibe nichts."""
    print(f"{len(zeilen)} Aufnahmen gefunden:")
    for row in sorted(zeilen, key=_sortierschluessel, reverse=True):
        zeitpunkt = parse_name_timestamp(row.name)
        datum = zeitpunkt.isoformat(sep=" ") if zeitpunkt is not None else "Datum unbekannt"
        print(f"  {row.name}  ({datum})")


def _parser() -> argparse.ArgumentParser:
    """Die Befehlszeile dieses Werkzeugs."""
    parser = argparse.ArgumentParser(
        description="Shorts Stufe 0 kopflos: shorts-job.json ohne Tk-Fenster schreiben"
    )
    parser.add_argument("--aufnahme", default=None, help="Name der Aufnahme; ohne: die juengste")
    parser.add_argument(
        "--liste", action="store_true", help="nur die gefundenen Aufnahmen zeigen, nichts schreiben"
    )
    parser.add_argument(
        "--force", action="store_true", help="eine vorhandene Auftragsdatei ueberschreiben"
    )
    parser.add_argument("--ausgabe", type=Path, default=None, help="abweichender Zielpfad")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Sammle, waehle, pruefe, schreibe - und sage in einer Zeile, was geschah."""
    args = _parser().parse_args(argv)
    try:
        zeilen = sammle_aufnahmen(probe_duration=not args.liste)
        if args.liste:
            if not zeilen:
                raise AuftragFehlschlag(
                    "keine_aufnahmen",
                    "keine gerenderten Aufnahmen gefunden",
                    CODE_KEINE_AUFNAHME,
                )
            _liste_ausgeben(zeilen)
            return CODE_ERFOLG

        row = waehle_aufnahme(zeilen, args.aufnahme)
        ziel: Path = args.ausgabe if args.ausgabe is not None else zielpfad(row)
        if ziel.exists() and not args.force:
            print(f"Auftragsdatei vorhanden, unveraendert uebernommen: {ziel}")
            return CODE_ERFOLG

        payload = build_job_payload(row, created_at=datetime.now(UTC).isoformat())
        fehlend = fehlende_pflichtfelder(payload)
        if fehlend:
            raise AuftragFehlschlag(
                "pflichtfeld_fehlt",
                f"{row.name}: leer oder nicht gesetzt - {', '.join(fehlend)}",
                CODE_PFLICHTFELD,
            )
        schreibe_auftrag(ziel, payload)
    except AuftragFehlschlag as fehler:
        print(f"ANGEHALTEN [{fehler.code_name}]: {fehler.text}")
        return fehler.rueckgabecode
    print(f"Auftragsdatei geschrieben: {ziel} ({row.name}, {row.duration_ms} ms)")
    return CODE_ERFOLG


if __name__ == "__main__":  # pragma: no cover - Einstieg nur ueber -m
    raise SystemExit(main())
