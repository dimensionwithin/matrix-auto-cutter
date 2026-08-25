r"""Auftrag urteilslauf: ein Befehl statt vier Handgriffen.

Zwischen Zerlegung und Bau standen bisher vier getrennte Handgriffe: die
juengste Aufnahme heraussuchen, pruefen dass alte Urteile nicht auf neue
Kandidaten zeigen, den Urteilsserver starten, und danach Quote melden,
Bauliste erzeugen und die Urteile sichern. Dieses Modul reiht sie
aneinander - es rechnet nichts selbst aus, sondern ruft ``judge_server``
und ``auswahl`` in der richtigen Reihenfolge auf und schreibt zum
Schluss die naechste Handlung hin.

Die Pruefung (``auswahl.pruefe_uebereinstimmung``) laeuft VOR dem
Serverstart, nicht danach: ``judge_server`` uebernimmt beim Start den
Inhalt der juengsten ``urteile*.json`` des Auftragsordners - passt die
nicht zu den Kandidaten, waeren die falsch zugeordneten Urteile nach dem
Start bereits in die neue Sitzungsdatei uebernommen.

Urteilsdateien werden hier ausschliesslich GELESEN und KOPIERT. Eine
vorhandene Sicherung unter ``labels/repeat/`` wird nie ueberschrieben -
am 14.8. kostete ein ueberschreibender Probelauf schon einmal einen
echten Urteilsstand (siehe ``judge_server``, sitzungseigene
Urteilsdatei).

Zu Strg+C siehe :func:`starte_urteilsseite`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path

from matrix_auto_cutter.shorts import auswahl
from matrix_auto_cutter.shorts.candidates import (
    CANDIDATES_FILE_NAME,
    CandidatesSchemaError,
    load_candidates,
)
from matrix_auto_cutter.shorts.judge_server import load_urteile

SICHERUNG_DIR = Path("labels/repeat")
AUFNAHMEN_UNTERPFAD = Path("artefakte/repeat/shorts")
JOB_FILE_NAME = "shorts-job.json"
RENDER_WURZEL = r"F:\MatrixMarketAutoEdit\Shorts-Rendered"

_CODE_ERFOLG = 0
_CODE_KEINE_AUFNAHME = 2
_CODE_AUFTRAG_UNLESBAR = 2
_CODE_URTEILE_ABWEICHUNG = 5
_CODE_SICHERUNG_FEHLGESCHLAGEN = 6

_BEENDE_FRIST_SEKUNDEN = 5.0
_TAKT_SEKUNDEN = 0.25
_ABBRUCH_MELDUNG = "Strg+C empfangen - Urteilsseite wird beendet."
_PLATZHALTER_SEKUNDEN = 4.0
_ABBRUCH_SIGNALE = ("SIGINT", "SIGBREAK")


def finde_aufnahme(wurzel: Path) -> Path | None:
    """Der Aufnahmeordner mit der juengsten ``kandidaten.json``, falls es einen gibt.

    "Juengster" heisst nach Aenderungszeit der ``kandidaten.json``, nicht
    nach Ordnernamen: die Ordner tragen den Aufnahmezeitpunkt im Namen,
    und eine zweite Zerlegung einer aelteren Aufnahme ist genau der Fall,
    in dem der Name in die Irre fuehrt.
    """
    basis = wurzel / AUFNAHMEN_UNTERPFAD
    if not basis.is_dir():
        return None
    treffer: list[tuple[int, Path]] = []
    for ordner in sorted(basis.iterdir()):
        if not ordner.is_dir():
            continue
        kandidaten_pfad = ordner / CANDIDATES_FILE_NAME
        if not kandidaten_pfad.is_file():
            continue
        treffer.append((kandidaten_pfad.stat().st_mtime_ns, ordner))
    if not treffer:
        return None
    return max(treffer, key=lambda paar: paar[0])[1]


def pruefe_vor_start(job_dir: Path) -> list[str]:
    """Melde jede Abweichung zwischen vorhandenen Urteilen und den Kandidaten.

    Keine Urteilsdatei vorhanden heisst leere Liste - das ist der
    Normalfall beim ersten Urteilen einer Aufnahme und kein Fehler.
    """
    urteile_pfad = auswahl.juengste_urteilsdatei(job_dir)
    if urteile_pfad is None:
        return []
    kandidaten = load_candidates(job_dir / CANDIDATES_FILE_NAME)
    return auswahl.pruefe_uebereinstimmung(kandidaten, load_urteile(urteile_pfad))


def _beende_kind(process: subprocess.Popen[bytes]) -> int:
    """Beende den Server nach dem Muster von ``product_runner._stop_review_process``."""
    if process.poll() is not None:
        return int(process.returncode)
    try:
        process.terminate()
        process.wait(timeout=_BEENDE_FRIST_SEKUNDEN)
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired, KeyboardInterrupt):
        with suppress(OSError, subprocess.SubprocessError):
            process.kill()
        with suppress(OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            process.wait(timeout=_BEENDE_FRIST_SEKUNDEN)
    return int(process.returncode) if process.returncode is not None else 0


@contextmanager
def _abbruch_merker() -> Iterator[threading.Event]:
    """Halte Strg+C als Merker fest, statt es zur Ausnahme werden zu lassen.

    Der eigene Handler ist die zweite Vorkehrung neben der Warteschleife:
    er greift auch dort, wo ein ``KeyboardInterrupt`` gar nicht erst
    zugestellt wird. Und er wirkt waehrend des Beendens weiter - ein
    zweites Strg+C setzt denselben Merker noch einmal und reisst den Lauf
    nicht auseinander.

    Nicht vorhandene Signale (``SIGBREAK`` gibt es nur unter Windows)
    werden uebersprungen; das ist kein Fehlschlag, sondern eine
    Plattformauskunft.
    """
    merker = threading.Event()

    def _fange(signalnummer: int, rahmen: object) -> None:
        merker.set()

    vorher: list[tuple[int, object]] = []
    for name in _ABBRUCH_SIGNALE:
        nummer = getattr(signal, name, None)
        if nummer is None:
            print(f"  {name} auf dieser Plattform nicht vorhanden - uebersprungen")
            continue
        try:
            vorher.append((int(nummer), signal.signal(nummer, _fange)))
        except (OSError, ValueError, RuntimeError):
            print(f"  {name} nicht setzbar - uebersprungen")
    try:
        yield merker
    finally:
        for nummer, alter_handler in vorher:
            with suppress(OSError, ValueError, RuntimeError, TypeError):
                signal.signal(nummer, alter_handler)  # type: ignore[arg-type]


def warte_auf_kind(
    process: subprocess.Popen[bytes], merker: threading.Event | None = None
) -> int | None:
    """Warte in kurzen Takten auf den Kindprozess; ``None`` heisst Abbruchwunsch.

    Muster: ``render._run`` wartet ebenso in Takten statt in einem
    einzigen langen Aufruf. Der Unterschied ist genau der, an dem der
    Handlauf vom 25.8. haengenblieb: ``process.wait()`` ohne Frist steht
    unter Windows in einem nicht unterbrechbaren Betriebssystem-Warten,
    und ein Strg+C wird erst zugestellt, wenn es ohnehin zu spaet ist.
    """
    while True:
        code = process.poll()
        if code is not None:
            return int(code)
        if merker is not None and merker.is_set():
            return None
        time.sleep(_TAKT_SEKUNDEN)


def _server_argv(job_path: Path, *, platzhalter: bool) -> list[str]:
    """Die Befehlszeile des Kindprozesses - echter Urteilsserver oder Platzhalter."""
    if platzhalter:
        return [sys.executable, "-c", f"import time; time.sleep({_PLATZHALTER_SEKUNDEN})"]
    return [sys.executable, "-m", "matrix_auto_cutter.shorts.judge_server", str(job_path)]


def starte_urteilsseite(job_path: Path, *, platzhalter: bool = False) -> int:
    """Fuehre den Urteilsserver aus und warte auf ihn, ohne an Strg+C mitzusterben.

    Der Nutzer beendet die Urteilsseite mit Strg+C. Unter Windows geht
    dieses Ereignis an die ganze Konsolengruppe - naiv gebaut stuerbe
    dieses Werkzeug mit dem Server, und Quote, Bauliste und Sicherung
    liefen nie. Drei Vorkehrungen zusammen verhindern das:

    1. Der Server startet mit ``CREATE_NEW_PROCESS_GROUP`` (Muster:
       ``product_runner._open_review_native``) - er gehoert damit einer
       eigenen Gruppe an und bekommt das Konsolenereignis gar nicht
       erst. Damit bleibt genau ein Empfaenger uebrig: dieses Werkzeug.
    2. Gewartet wird in kurzen Takten (:func:`warte_auf_kind`), nicht in
       einem einzigen ``process.wait()``. Nur so kommt ein
       ``KeyboardInterrupt`` zwischen zwei Takten ueberhaupt an.
    3. Zusaetzlich haelt ein eigener Signalhandler das Ereignis als
       Merker fest (:func:`_abbruch_merker`) - fuer den Fall, dass unter
       Windows gar kein ``KeyboardInterrupt`` zugestellt wird.

    Danach beendet dieses Werkzeug den Server selbst und geraeumt
    (terminate, Frist, notfalls kill) und laeuft REGULAER weiter. Kein
    Urteil geht dabei verloren - ``judge_server`` schreibt nach jedem
    einzelnen Klick atomar. Der Rueckgabecode des Servers ist Auskunft,
    kein Fehlschlag: der regulaere Weg endet in einem Abbruch.

    ``platzhalter`` startet statt des Servers einen schlafenden
    Einzeiler - dieselbe Prozessgruppe, dieselbe Schleife, dieselbe
    Abbruchbehandlung, nur ohne Zugriff auf Urteilsdateien.
    """
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        _server_argv(job_path, platzhalter=platzhalter),
        stdin=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creation_flags,
        shell=False,
    )
    with _abbruch_merker() as merker:
        try:
            code = warte_auf_kind(process, merker)
        except KeyboardInterrupt:
            code = None
        if code is not None:
            return code
        print(_ABBRUCH_MELDUNG)
        try:
            return _beende_kind(process)
        except KeyboardInterrupt:
            # Zweites Strg+C mitten im Beenden: hart beenden, aber weiterlaufen.
            with suppress(OSError, subprocess.SubprocessError):
                process.kill()
            with suppress(OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                process.wait(timeout=_BEENDE_FRIST_SEKUNDEN)
            return int(process.returncode) if process.returncode is not None else 0


def lies_kandidaten_wurzel(pfad: Path) -> dict[str, object]:
    """Lies die Wurzelfelder von ``kandidaten.json`` roh - ohne die Kandidatenliste.

    ``load_candidates`` schneidet ``modell``, ``lauf`` und ``video_name``
    weg (sie sind nicht Teil des Kandidaten-Kontrakts, siehe
    ``candidates.py``), gebraucht werden sie hier nur fuer die
    Sicherungsnamen. Ein unlesbarer oder unerwarteter Inhalt fuehrt zu
    einem leeren Wurzelsatz und damit zu ``unbekannt`` im Namen - das ist
    nicht der Fehlerpfad dieses Werkzeugs.
    """
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(roh, dict):
        return {}
    return {schluessel: wert for schluessel, wert in roh.items() if schluessel != "kandidaten"}


def _namensteil(wurzel: dict[str, object], feld: str) -> str:
    wert = wurzel.get(feld)
    if isinstance(wert, bool):
        return "unbekannt"
    if isinstance(wert, int):
        return str(wert)
    if isinstance(wert, str) and wert.strip():
        return wert.strip()
    return "unbekannt"


def sicherungsnamen(kandidaten_wurzel: dict[str, object]) -> tuple[str, str]:
    """Die beiden Zielnamen unter ``labels/repeat/``: (Urteile, Kandidaten).

    Fehlt eines der drei Felder ``video_name``, ``lauf``, ``modell``,
    steht an seiner Stelle ``unbekannt`` - ein unvollstaendig benannter
    Beleg ist besser als gar keiner.
    """
    video_name = _namensteil(kandidaten_wurzel, "video_name")
    lauf = _namensteil(kandidaten_wurzel, "lauf")
    modell = _namensteil(kandidaten_wurzel, "modell")
    kern = f"{video_name}-lauf{lauf}-{modell}"
    return f"urteile-{kern}.json", f"kandidaten-{kern}.json"


def sichere_urteile(
    job_dir: Path, ziel_dir: Path, kandidaten_wurzel: dict[str, object]
) -> list[Path]:
    """Kopiere die juengste Urteilsdatei und die Kandidatendatei nach ``ziel_dir``.

    Kopieren, nicht verschieben: der Auftragsordner bleibt vollstaendig.
    Eine vorhandene Zieldatei wird gemeldet und uebersprungen, nie
    ueberschrieben - eine Sicherung, die eine aeltere Sicherung frisst,
    ist keine.
    """
    urteile_name, kandidaten_name = sicherungsnamen(kandidaten_wurzel)
    urteile_quelle = auswahl.juengste_urteilsdatei(job_dir)
    paare: list[tuple[Path | None, str]] = [
        (urteile_quelle, urteile_name),
        (job_dir / CANDIDATES_FILE_NAME, kandidaten_name),
    ]
    ziel_dir.mkdir(parents=True, exist_ok=True)
    kopiert: list[Path] = []
    for quelle, name in paare:
        ziel = ziel_dir / name
        if quelle is None or not quelle.is_file():
            print(f"  uebersprungen: keine Quelldatei fuer {ziel}")
            continue
        if ziel.exists():
            print(f"  uebersprungen: {ziel} liegt bereits vor - nicht ueberschrieben")
            continue
        shutil.copy2(quelle, ziel)
        print(f"  kopiert: {quelle} -> {ziel}")
        kopiert.append(ziel)
    return kopiert


def zaehle_urteile(job_dir: Path) -> tuple[int, int, int, int]:
    """(gesamt, ja, nein, offen) - offen ist alles ohne ``ja``/``nein``.

    ``spaeter`` zaehlt als offen, nicht als Ablehnung: es ist ein
    aufgeschobenes, kein gefaelltes Urteil. Fuer die Bauliste bleibt es
    trotzdem eine Nichtannahme, das entscheidet ``auswahl``.
    """
    try:
        kandidaten = load_candidates(job_dir / CANDIDATES_FILE_NAME)
    except (CandidatesSchemaError, OSError):
        return 0, 0, 0, 0
    urteile_pfad = auswahl.juengste_urteilsdatei(job_dir)
    urteile = load_urteile(urteile_pfad) if urteile_pfad is not None else {}
    gefaellt = [urteile.get(kandidat.index) for kandidat in kandidaten]
    ja = sum(1 for urteil in gefaellt if urteil is not None and urteil.urteil == "ja")
    nein = sum(1 for urteil in gefaellt if urteil is not None and urteil.urteil == "nein")
    gesamt = len(kandidaten)
    return gesamt, ja, nein, gesamt - ja - nein


def baubefehl(job_path: Path, bauliste_pfad: Path, video_name: str) -> str:
    """Die vollstaendige ``build.py``-Kommandozeile - nur zum Hinschreiben.

    Ohne abschliessenden Trennstrich im ``--output-dir``: unter Windows
    beendet ein Backslash vor dem schliessenden Anfuehrungszeichen dieses
    nicht, sondern maskiert es - die Zeile waere zum Kopieren unbrauchbar.
    """
    ausgabe = f"{RENDER_WURZEL}\\{video_name}"
    return (
        "python -m matrix_auto_cutter.shorts.build "
        f'"{job_path}" "{bauliste_pfad}" --output-dir "{ausgabe}"'
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: einmal aufrufen, urteilen, Fenster schliessen - der Rest laeuft von selbst."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shorts-Urteilslauf: Aufnahme finden, urteilen, Quote, Bauliste, Sicherung"
    )
    parser.add_argument("job_path", type=Path, nargs="?", default=None)
    parser.add_argument("--kein-server", action="store_true")
    parser.add_argument("--keine-sicherung", action="store_true")
    parser.add_argument("--keine-auswahl", action="store_true")
    parser.add_argument("--wurzel", type=Path, default=None)
    parser.add_argument(
        "--platzhalter-server",
        action="store_true",
        help=(
            "Erprobungshilfe: statt der Urteilsseite laeuft ein schlafender Einzeiler. "
            "Damit laesst sich Strg+C ueben, ohne den Urteilsserver auf einen echten "
            "Auftragsordner loszulassen."
        ),
    )
    args = parser.parse_args(argv)

    wurzel: Path = args.wurzel if args.wurzel is not None else Path.cwd()

    # ---- Schritt 1: Aufnahme bestimmen -----------------------------------
    print("Schritt 1: Aufnahme bestimmen")
    job_arg: Path | None = args.job_path
    if job_arg is not None and job_arg.is_file():
        job_path = job_arg
        job_dir = job_arg.parent
        try:
            json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ANGEHALTEN [auftrag_unlesbar]: {job_path} nicht als JSON lesbar: {exc}")
            return _CODE_AUFTRAG_UNLESBAR
    elif job_arg is not None and job_arg.is_dir():
        job_dir = job_arg
        job_path = job_dir / JOB_FILE_NAME
    elif job_arg is not None:
        print(f"ANGEHALTEN [auftrag_unlesbar]: {job_arg} ist weder Datei noch Ordner")
        return _CODE_AUFTRAG_UNLESBAR
    else:
        gefunden = finde_aufnahme(wurzel)
        if gefunden is None:
            print(
                "ANGEHALTEN [keine_aufnahme]: kein Ordner mit "
                f"{CANDIDATES_FILE_NAME} unter {wurzel / AUFNAHMEN_UNTERPFAD}"
            )
            return _CODE_KEINE_AUFNAHME
        job_dir = gefunden
        job_path = job_dir / JOB_FILE_NAME
    kandidaten_pfad = job_dir / CANDIDATES_FILE_NAME
    if not kandidaten_pfad.is_file():
        print(f"ANGEHALTEN [keine_aufnahme]: {kandidaten_pfad} fehlt")
        return _CODE_KEINE_AUFNAHME
    print(f"  Auftragsordner: {job_dir}")
    print(f"  Auftragsdatei:  {job_path}")

    # ---- Schritt 2: Urteile gegen Kandidaten pruefen ----------------------
    print("Schritt 2: vorhandene Urteile gegen die Kandidaten pruefen")
    try:
        abweichungen = pruefe_vor_start(job_dir)
    except (CandidatesSchemaError, OSError) as exc:
        print(f"ANGEHALTEN [keine_aufnahme]: {kandidaten_pfad} nicht lesbar: {exc}")
        return _CODE_KEINE_AUFNAHME
    if abweichungen:
        print("ANGEHALTEN [urteile_abweichung]: Urteile passen nicht zu den Kandidaten")
        for zeile in abweichungen:
            print(f"  {zeile}")
        print("  Urteilsseite NICHT gestartet.")
        return _CODE_URTEILE_ABWEICHUNG
    print("  0 Abweichungen - die vorhandenen Urteile passen zu den Kandidaten.")

    # ---- Schritt 3: Urteilsseite -----------------------------------------
    if args.kein_server:
        print("Schritt 3: Urteilsseite uebersprungen (--kein-server)")
    else:
        if not job_path.is_file():
            print(f"ANGEHALTEN [auftrag_unlesbar]: {job_path} fehlt - Urteilsseite nicht startbar")
            return _CODE_AUFTRAG_UNLESBAR
        print("Schritt 3: Urteilsseite starten (Strg+C beendet sie, der Lauf geht danach weiter)")
        if args.platzhalter_server:
            print("  Erprobung: Platzhalterprozess statt Urteilsseite (--platzhalter-server)")
        server_code = starte_urteilsseite(job_path, platzhalter=args.platzhalter_server)
        print(f"  Urteilsseite beendet (Rueckgabecode {server_code}) - weiter geht es.")

    # ---- Schritt 4: Quote -------------------------------------------------
    print("Schritt 4: Urteile zaehlen")
    gesamt, ja, nein, offen = zaehle_urteile(job_dir)
    print(f"  {ja + nein} von {gesamt} beurteilt - {ja} ja, {nein} nein, {offen} offen")

    # ---- Schritt 5: Bauliste ---------------------------------------------
    bauliste_pfad = job_dir / auswahl.BAULISTE_FILE_NAME
    if args.keine_auswahl:
        print("Schritt 5: Bauliste uebersprungen (--keine-auswahl)")
    else:
        print("Schritt 5: Bauliste erzeugen (auswahl)")
        auswahl_code = auswahl.main([str(job_dir)])
        print(f"  auswahl endete mit Rueckgabecode {auswahl_code}")
        if auswahl_code == _CODE_URTEILE_ABWEICHUNG:
            print("ANGEHALTEN [urteile_abweichung]: auswahl meldet abweichende Urteile")
            return _CODE_URTEILE_ABWEICHUNG

    # ---- Schritt 6: Sicherung --------------------------------------------
    kandidaten_wurzel = lies_kandidaten_wurzel(kandidaten_pfad)
    if args.keine_sicherung:
        print("Schritt 6: Sicherung uebersprungen (--keine-sicherung)")
    else:
        ziel_dir = wurzel / SICHERUNG_DIR
        print(f"Schritt 6: Urteile und Kandidaten sichern nach {ziel_dir}")
        try:
            kopiert = sichere_urteile(job_dir, ziel_dir, kandidaten_wurzel)
        except OSError as exc:
            print(f"ANGEHALTEN [sicherung_fehlgeschlagen]: {exc}")
            return _CODE_SICHERUNG_FEHLGESCHLAGEN
        print(f"  {len(kopiert)} Datei(en) kopiert")

    # ---- Schritt 7: naechste Handlung ------------------------------------
    print("Schritt 7: naechste Handlung - diese Zeile ausfuehren, wenn die Bauliste stimmt:")
    print(baubefehl(job_path, bauliste_pfad, _namensteil(kandidaten_wurzel, "video_name")))
    return _CODE_ERFOLG


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
