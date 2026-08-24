r"""Kopfloser Ausgabeweg fuer die Wortliste: ``wortliste.json`` je Aufnahmeordner.

Die Zerlegung braucht als Eingabe eine Wortliste mit Zeitmarken und
Interpunktion. ``words_from_whisper_json``
(:mod:`matrix_auto_cutter.shorts.subtitle_lines`) baut diese Liste bereits
aus der whisper-Rohausgabe der gerenderten Fassung
(``transkript-rendered.wav.json``) - dieses Modul haengt nur Lesen, Schreiben
und Wiederanlauf drumherum, ohne die Funktion zu kopieren.

Eine abgebrochene ``*.wav.json`` sieht von aussen fertig aus: die Datei
existiert und laesst sich oeffnen, ist aber kein gueltiges JSON oder traegt
keine Tokens. Ein stiller Rueckfall auf eine leere Wortliste wuerde diesen
Fehler verschlucken und die Zerlegung mit einer leeren Eingabe weiterlaufen
lassen. Deshalb sind ein JSON-Parsefehler und eine leere Wortliste hier
eigene, laute Fehlschlaege mit eigenem Rueckgabecode - keine leere Datei
landet je auf der Platte.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.subtitle_lines import Word, words_from_whisper_json
from matrix_auto_cutter.shorts.transcript import RENDERED_WAV_NAME, transcript_paths

WORTLISTE_FILE_NAME = "wortliste.json"
WORTLISTE_SCHEMA_VERSION = "1.0"

RUECKGABECODE_JOB_NICHT_LESBAR = 2
RUECKGABECODE_ROHAUSGABE_FEHLT = 2
RUECKGABECODE_ROHAUSGABE_KAPUTT = 3
RUECKGABECODE_NULL_WOERTER = 4


def wortliste_pfad(job_path: Path) -> Path:
    """Pfad der Ausgabedatei: Aufnahmeordner (Ordner von ``job_path``) plus Dateiname."""
    return job_path.parent / WORTLISTE_FILE_NAME


def lade_rohausgabe(pfad: Path) -> str:
    """Lies die whisper-Rohausgabe als Text."""
    return pfad.read_text(encoding="utf-8")


def baue_wortliste_payload(woerter: Sequence[Word], *, quelle: str) -> dict[str, object]:
    """Baue den JSON-Inhalt von ``wortliste.json`` aus der geparsten Wortliste."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_wortliste",
        "schema_version": WORTLISTE_SCHEMA_VERSION,
        "quelle": quelle,
        "wort_anzahl": len(woerter),
        "woerter": [
            {"start_ms": wort.start_ms, "end_ms": wort.end_ms, "text": wort.text}
            for wort in woerter
        ],
    }


def schreibe_wortliste(pfad: Path, payload: dict[str, object]) -> None:
    """Schreibe ``wortliste.json`` atomar - dasselbe Muster wie ``write_transcript``."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{pfad.name}.tmp.",
            dir=pfad.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, pfad, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: Wortliste der gerenderten Fassung aus ``shorts-job.json``.

    ``job_path`` ist Pflicht, ``--force`` erzwingt Neuberechnung auch wenn
    ``wortliste.json`` schon vorliegt.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Shorts: Wortliste mit Zeitmarken und Interpunktion"
    )
    parser.add_argument("job_path", type=Path, help="Pfad zur shorts-job.json")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    job_path: Path = args.job_path
    if not job_path.is_file():
        print(f"ANGEHALTEN [job_nicht_lesbar]: shorts-job.json nicht lesbar: {job_path}")
        return RUECKGABECODE_JOB_NICHT_LESBAR

    ziel_pfad = wortliste_pfad(job_path)

    if ziel_pfad.is_file() and not args.force:
        bestehend = json.loads(ziel_pfad.read_text(encoding="utf-8"))
        wort_anzahl = bestehend.get("wort_anzahl")
        print(f"{ziel_pfad} ({wort_anzahl} Woerter, wiederverwendet)")
        return 0

    raw_json_path, _ = transcript_paths(job_path.parent, wav_name=RENDERED_WAV_NAME)
    if not raw_json_path.is_file():
        print(f"ANGEHALTEN [rohausgabe_fehlt]: Rohausgabe fehlt: {raw_json_path}")
        return RUECKGABECODE_ROHAUSGABE_FEHLT

    raw_json = lade_rohausgabe(raw_json_path)
    try:
        woerter = words_from_whisper_json(raw_json)
    except json.JSONDecodeError as exc:
        print(
            f"ANGEHALTEN [rohausgabe_kaputt]: Rohausgabe nicht als JSON lesbar: "
            f"{raw_json_path} ({exc})"
        )
        return RUECKGABECODE_ROHAUSGABE_KAPUTT

    if not woerter:
        print(f"ANGEHALTEN [null_woerter]: Wortliste ist leer: {raw_json_path}")
        return RUECKGABECODE_NULL_WOERTER

    payload = baue_wortliste_payload(woerter, quelle=raw_json_path.name)
    schreibe_wortliste(ziel_pfad, payload)
    print(f"{ziel_pfad} ({len(woerter)} Woerter, neu berechnet)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
