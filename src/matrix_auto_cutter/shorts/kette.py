r"""Der Kettenlaeufer: eine frische Aufnahme bis zu den Kandidaten.

Sechs Stufen liegen zwischen einer gerenderten Aufnahme und der
Urteilsseite - Auftragsdatei, Avatarschnitt, Transkript, Wortliste,
Zerlegung, Zusammenfuehrung. Jede davon ist ein erprobtes Werkzeug, und
jede wurde bisher von Hand angestossen. Dieses Modul reiht sie
aneinander und gibt ihnen ein Gedaechtnis: ``kette.json`` im
Aufnahmeordner haelt nach JEDER Stufe fest, was gelaufen ist. Ein
abgebrochener Lauf laesst sich damit fortsetzen, statt von vorn zu
beginnen - bei einer Transkription, die rund das 1,27-fache der
Audiodauer braucht, ist das der Unterschied zwischen zehn Minuten und
zwanzig.

Dieses Modul rechnet nichts selbst aus. Es startet Prozesse, wartet auf
sie, schreibt den Stand fort und meldet Fortschritt - kein einziges der
aufgerufenen Werkzeuge meldet von sich aus, wie weit es ist.

Ueberspringen ohne Gedaechtnis
------------------------------
Eine Stufe gilt als erledigt, wenn ihre Ausgabe daliegt und der Eintrag
in ``kette.json`` sie nicht als gescheitert oder halb gelaufen fuehrt.
Der Eintrag DARF fehlen: die Aufnahmen, die vor diesem Modul von Hand
durchgefahren wurden, haben alle Ausgaben und keine ``kette.json``.
Wuerde ein fehlender Eintrag als "noch nicht gelaufen" gelten, baute der
erste Lauf dieses Werkzeugs ueber einem fertigen Bestand alles noch
einmal - einschliesslich Transkription und Zerlegung. Ein vorgefundener
Eintrag zaehlt dagegen sehr wohl, sobald er etwas aussagt:
``gescheitert`` und ``laeuft`` lassen die Stufe erneut laufen, auch wenn
eine (womoeglich halbe) Ausgabe herumliegt.

Der Aufnahmename wird EINMAL bestimmt und danach aus ``kette.json``
gelesen. Der Bestand waechst taeglich - "die juengste Aufnahme" ist
morgen eine andere, und eine Kette, die ihren Namen zwischendurch neu
erfragt, wechselt mitten im Lauf das Werkstueck.

Die Zerlegung (Stufe 5) hat kein Modul: sie ist der Modellschritt und
laeuft als ``claude -p``. Der Auftragstext steht nicht hier, sondern in
``docs\repeat\ZERLEGUNG-AUFTRAGSTEXT.md`` - stuende er an beiden
Stellen, veralteten zwei Fassungen nebeneinander.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.auftrag import (
    AuftragFehlschlag,
    sammle_aufnahmen,
    waehle_aufnahme,
)
from matrix_auto_cutter.shorts.candidates import CANDIDATES_FILE_NAME

JOBS_ROOT = Path("artefakte") / "repeat" / "shorts"
ZUSTAND_FILE_NAME = "kette.json"
JOB_FILE_NAME = "shorts-job.json"
ARTIFACT_TYPE = "matrix_auto_cutter_shorts_kette"
SCHEMA_VERSION = "1.0"

CODE_ERFOLG = 0
CODE_KEINE_AUFNAHME = 2
CODE_STUFE_GESCHEITERT = 5
CODE_ZUSAMMENFUEHRUNG_FEHLT = 6

# Belegt in ORCHESTRATOR-UEBERGABE-2026-08-25.md: die Transkription mit vier
# Threads braucht rund das 1,27-fache der Audiodauer. Der Wert ist eine
# Erwartung, keine Zusage - er steht in der Ausgabe, damit der Nutzer weiss,
# ob er warten oder in der Zwischenzeit etwas anderes tun soll.
TRANSKRIPTION_FAKTOR = 1.27

FORTSCHRITT_TAKT_SEKUNDEN = 30.0
_POLL_TAKT_SEKUNDEN = 0.25

CLAUDE_BEFEHL = "claude"
ZERLEGUNG_AUFTRAGSTEXT_PFAD = Path("docs") / "repeat" / "ZERLEGUNG-AUFTRAGSTEXT.md"
ZERLEGUNG_MODELL = "sonnet"
ZERLEGUNG_LAUF = 1

STATUS_OFFEN = "offen"
STATUS_LAEUFT = "laeuft"
STATUS_FERTIG = "fertig"
STATUS_GESCHEITERT = "gescheitert"


@dataclass(frozen=True)
class Stufe:
    """Eine Stufe der Kette: Name, erwartete Ausgabe, Klartext."""

    name: str
    ausgabe: str
    beschreibung: str


STUFEN: tuple[Stufe, ...] = (
    Stufe("auftrag", JOB_FILE_NAME, "Auftragsdatei schreiben"),
    Stufe("avatar_cut", "avatar-cut.mp4", "Avatar nachschneiden"),
    Stufe("transcript", "transkript-rendered.json", "Transkription"),
    Stufe("wortliste", "wortliste.json", "Wortliste"),
    Stufe("zerlegung", f"kandidaten-lauf{ZERLEGUNG_LAUF}.json", "Zerlegung (Modell)"),
    Stufe("zusammenfuehrung", CANDIDATES_FILE_NAME, "Zusammenfuehrung"),
)

_KANDIDATEN_LAUF_GLOB = "kandidaten-lauf*.json"


class KetteFehlschlag(Exception):
    """Ein benannter Abbruchgrund samt Rueckgabecode - Muster ``auftrag.py``."""

    def __init__(self, code_name: str, text: str, rueckgabecode: int) -> None:
        """Halte Kurzname, deutschen Text und Rueckgabecode zusammen fest."""
        super().__init__(text)
        self.code_name = code_name
        self.text = text
        self.rueckgabecode = rueckgabecode


# --------------------------------------------------------------------------
# Zustandsdatei
# --------------------------------------------------------------------------


def _jetzt() -> str:
    """Der Zeitpunkt in ISO-Form mit Zeitzone - so steht er in jeder Artefaktdatei."""
    return datetime.now(UTC).isoformat()


def _leerer_eintrag() -> dict[str, object]:
    """Ein Stufeneintrag, der noch nichts erlebt hat."""
    return {
        "status": STATUS_OFFEN,
        "begonnen_am": None,
        "beendet_am": None,
        "dauer_s": None,
        "ausgabe": None,
        "meldung": None,
    }


def leerer_zustand(video_name: str) -> dict[str, object]:
    """Ein frischer Zustand: alle sechs Stufen offen, nichts begonnen."""
    jetzt = _jetzt()
    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": SCHEMA_VERSION,
        "video_name": video_name,
        "begonnen_am": jetzt,
        "zuletzt_am": jetzt,
        "stufen": {stufe.name: _leerer_eintrag() for stufe in STUFEN},
    }


def lies_zustand(pfad: Path) -> dict[str, object] | None:
    """Lies ``kette.json``; unlesbar oder unerwartet heisst ``None``.

    Ein kaputter Zustand ist kein Abbruchgrund: die Kette faengt dann von
    vorn an zu buchfuehren, und die Stufen mit vorhandener Ausgabe werden
    trotzdem uebersprungen.
    """
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(roh, dict) or not isinstance(roh.get("stufen"), dict):
        return None
    return roh


def schreibe_zustand(pfad: Path, zustand: dict[str, object]) -> None:
    """Schreibe ``kette.json`` atomar - Vorbild ``auftrag.schreibe_auftrag``.

    Nach JEDER Stufe, nicht erst am Ende: ein Lauf, der in Stufe 3
    abbricht, muss die zwei fertigen Stufen davor belegen koennen. Sonst
    faengt der naechste Lauf bei der Transkription wieder von vorn an -
    und die kostet rund das 1,27-fache der Audiodauer.
    """
    zustand["zuletzt_am"] = _jetzt()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    daten = (json.dumps(zustand, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporaer: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{pfad.name}.tmp.", dir=pfad.parent, delete=False
        ) as griff:
            temporaer = Path(griff.name)
            griff.write(daten)
            griff.flush()
            os.fsync(griff.fileno())
        replace_atomically(temporaer, pfad, create_only=False)
    finally:
        if temporaer is not None and temporaer.exists():
            temporaer.unlink(missing_ok=True)


def _eintrag(zustand: dict[str, object], name: str) -> dict[str, object]:
    """Der Stufeneintrag; fehlt er, entsteht er offen."""
    stufen = zustand.get("stufen")
    if not isinstance(stufen, dict):
        stufen = {}
        zustand["stufen"] = stufen
    eintrag = stufen.get(name)
    if not isinstance(eintrag, dict):
        eintrag = _leerer_eintrag()
        stufen[name] = eintrag
    return eintrag


# --------------------------------------------------------------------------
# Aufnahme bestimmen
# --------------------------------------------------------------------------


def finde_laufende_kette(jobs_root: Path) -> tuple[Path, dict[str, object]] | None:
    """Der Aufnahmeordner mit der juengsten ``kette.json``, falls es einen gibt.

    Das ist der Weg, auf dem der Name NICHT neu erfragt wird: liegt eine
    Zustandsdatei da, traegt sie ihren Aufnahmenamen selbst, und der
    Bestand auf ``F:`` wird gar nicht erst angefasst.
    """
    if not jobs_root.is_dir():
        return None
    treffer: list[tuple[int, Path, dict[str, object]]] = []
    for ordner in sorted(jobs_root.iterdir()):
        if not ordner.is_dir():
            continue
        pfad = ordner / ZUSTAND_FILE_NAME
        if not pfad.is_file():
            continue
        zustand = lies_zustand(pfad)
        if zustand is None or not isinstance(zustand.get("video_name"), str):
            continue
        treffer.append((pfad.stat().st_mtime_ns, ordner, zustand))
    if not treffer:
        return None
    _, ordner, zustand = max(treffer, key=lambda dreier: dreier[0])
    return ordner, zustand


def bestimme_aufnahme(jobs_root: Path, name: str | None) -> tuple[str, dict[str, object] | None]:
    """(Aufnahmename, vorgefundener Zustand) - der Name wird hier einmal festgelegt.

    Drei Wege, in dieser Reihenfolge: der genannte Name, der Name aus einer
    vorgefundenen ``kette.json``, und erst zuletzt der Bestand. Nur der
    dritte Weg liest ``F:`` - die beiden anderen kommen ohne aus, und genau
    darauf beruht die Zusage, dass ein fortgesetzter Lauf sein Werkstueck
    nicht wechselt.
    """
    if name is not None:
        pfad = jobs_root / name / ZUSTAND_FILE_NAME
        return name, lies_zustand(pfad) if pfad.is_file() else None
    gefunden = finde_laufende_kette(jobs_root)
    if gefunden is not None:
        ordner, zustand = gefunden
        video_name = zustand["video_name"]
        assert isinstance(video_name, str)
        print(f"  Name aus {ordner / ZUSTAND_FILE_NAME} uebernommen, Bestand nicht erneut gelesen")
        return video_name, zustand
    try:
        row = waehle_aufnahme(sammle_aufnahmen(probe_duration=False), None)
    except AuftragFehlschlag as fehler:
        raise KetteFehlschlag("keine_aufnahme", fehler.text, CODE_KEINE_AUFNAHME) from fehler
    return row.name, None


# --------------------------------------------------------------------------
# Fortschritt
# --------------------------------------------------------------------------


def dauer_text(sekunden: float) -> str:
    """``12 min 22 s`` bzw. ``48 s`` - abgeschnitten, nicht gerundet."""
    ganz = int(sekunden)
    if ganz < 60:
        return f"{ganz} s"
    return f"{ganz // 60} min {ganz % 60} s"


def audiodauer_s(job_path: Path) -> float | None:
    """Die Dauer der gerenderten Aufnahme in Sekunden, aus ``shorts-job.json``.

    Das ist dieselbe Zahl, mit der die Zerlegung ihre lueckenlose Karte
    abgleicht (``rendered_video.duration_ms``). Dieses Modul ruft dafuer
    kein ``ffprobe`` auf, sondern liest die Auftragsdatei, die Stufe 1
    gerade geschrieben hat - der einzige Grund, warum die Erwartung fuer
    Stufe 3 ueberhaupt kostenlos zu haben ist.
    """
    try:
        roh = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(roh, dict):
        return None
    video = roh.get("rendered_video")
    if not isinstance(video, dict):
        return None
    dauer_ms = video.get("duration_ms")
    if isinstance(dauer_ms, bool) or not isinstance(dauer_ms, int | float):
        return None
    return float(dauer_ms) / 1000.0


def erwartete_transkriptionsdauer_s(job_path: Path) -> float | None:
    """Audiodauer mal :data:`TRANSKRIPTION_FAKTOR`, falls die Dauer bekannt ist."""
    dauer = audiodauer_s(job_path)
    return None if dauer is None else dauer * TRANSKRIPTION_FAKTOR


# --------------------------------------------------------------------------
# Prozesse
# --------------------------------------------------------------------------


def zerlegung_auftragstext(
    video_name: str, lauf: int = ZERLEGUNG_LAUF, modell: str = ZERLEGUNG_MODELL
) -> str:
    """Der Verweis auf den Auftragstext - nicht der Auftragstext selbst.

    Wiederholte man ihn hier, gaebe es ihn zweimal, und beim naechsten
    Kriterienwechsel bliebe eine der beiden Fassungen stehen.

    ``modell`` steht zweimal im Aufruf: hier als Wurzelfeld der
    Kandidatendatei und in :func:`zerlegung_argv` als ``--model``. Das ist
    kein Versehen. Die Fahne bestimmt, WOMIT gefahren wird; das Wurzelfeld
    haelt fest, WOMIT gefahren WURDE - daraus liest die Trefferquote
    spaeter, welches Modell welche Ausbeute gebracht hat. Ein
    Kandidatensatz ohne dieses Feld waere fuer den Vergleich wertlos.
    """
    return (
        f"Lies {ZERLEGUNG_AUFTRAGSTEXT_PFAD} vollstaendig und fuehre den darin "
        f'beschriebenen Auftrag aus. <AUFNAHME> ist "{video_name}", <N> ist {lauf}. '
        f'Trage als Wurzelfeld modell den Wert "{modell}" ein. '
        f"Auftragsname: zerlegung-lauf{lauf}."
    )


def zerlegung_argv(
    video_name: str, lauf: int = ZERLEGUNG_LAUF, modell: str = ZERLEGUNG_MODELL
) -> list[str]:
    """Die Befehlszeile des Modellschritts - an einer Stelle gefasst.

    An einer Stelle, damit die geplante Aufgabe spaeter dieselbe benutzt
    und nicht eine zweite, leicht abweichende.

    ``--permission-mode acceptEdits`` ist nicht Bequemlichkeit: ohne die
    Angabe scheitert ein unbeaufsichtigter Lauf am Freigabedialog, und zwar
    NACHDEM er die Arbeit geleistet hat (Uebergabe vom 25.8., 4.4).
    """
    return [
        CLAUDE_BEFEHL,
        "-p",
        zerlegung_auftragstext(video_name, lauf, modell),
        "--model",
        modell,
        "--permission-mode",
        "acceptEdits",
    ]


def stufen_argv(
    stufe: Stufe,
    *,
    job_path: Path,
    jobs_root: Path,
    video_name: str,
    erzwingen: bool,
    modell: str = ZERLEGUNG_MODELL,
) -> list[str] | None:
    """Die Befehlszeile einer Stufe; ``None`` heisst: kein Prozess, sondern Handarbeit.

    Nur die Zusammenfuehrung ist Handarbeit - sie kopiert eine Datei.
    """
    py = sys.executable
    if stufe.name == "auftrag":
        argv = [
            py,
            "-m",
            "matrix_auto_cutter.shorts.auftrag",
            "--aufnahme",
            video_name,
            "--ausgabe",
            str(job_path),
        ]
        return [*argv, "--force"] if erzwingen else argv
    if stufe.name == "avatar_cut":
        return [
            py,
            "-m",
            "matrix_auto_cutter.shorts.avatar_cut",
            str(job_path),
            "--output-root",
            str(jobs_root),
        ]
    if stufe.name in ("transcript", "wortliste"):
        argv = [py, "-m", f"matrix_auto_cutter.shorts.{stufe.name}", str(job_path)]
        return [*argv, "--force"] if erzwingen else argv
    if stufe.name == "zerlegung":
        return zerlegung_argv(video_name, modell=modell)
    return None


def fuehre_prozess(argv: Sequence[str], *, etikett: str) -> int:
    """Fuehre einen Prozess aus und melde alle 30 s, wie lange er schon laeuft.

    Der einzige Ort, an dem dieses Modul einen Prozess startet - Tests
    biegen genau diese Funktion um und starten damit weder ``claude`` noch
    ``ffmpeg`` noch ``whisper``.

    Gewartet wird in kurzen Takten statt in einem einzigen ``wait()``:
    dasselbe Muster wie in ``urteilslauf.warte_auf_kind``, und nur so laesst
    sich zwischendurch ueberhaupt etwas ausgeben. Kein Werkzeug dieser
    Kette meldet von sich aus, wie weit es ist.

    Ein fehlendes Programm (``claude`` steht nicht im Pfad) ist ein
    benannter Fehlschlag, kein Absturz.
    """
    try:
        process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL, close_fds=True)
    except (FileNotFoundError, NotADirectoryError, PermissionError) as fehler:
        raise KetteFehlschlag(
            "stufe_gescheitert",
            f"Stufe {etikett}: {argv[0]} nicht aufrufbar - {fehler}",
            CODE_STUFE_GESCHEITERT,
        ) from fehler
    begonnen = time.monotonic()
    naechste_meldung = FORTSCHRITT_TAKT_SEKUNDEN
    while True:
        code = process.poll()
        if code is not None:
            return int(code)
        verstrichen = time.monotonic() - begonnen
        if verstrichen >= naechste_meldung:
            print(f"  {etikett} laeuft seit {dauer_text(verstrichen)} ...", flush=True)
            naechste_meldung += FORTSCHRITT_TAKT_SEKUNDEN
        time.sleep(_POLL_TAKT_SEKUNDEN)


# --------------------------------------------------------------------------
# Zusammenfuehrung
# --------------------------------------------------------------------------


def laufdateien(job_dir: Path) -> list[Path]:
    """Alle ``kandidaten-laufN.json`` des Aufnahmeordners, nach Namen geordnet."""
    return sorted(job_dir.glob(_KANDIDATEN_LAUF_GLOB))


def fuehre_zusammen(job_dir: Path) -> Path:
    """Kopiere den einzigen Zerlegungslauf nach ``kandidaten.json``.

    Bei EINEM Lauf ist die Zusammenfuehrung eine Kopie und sonst nichts.
    Liegen mehrere Laeufe vor, wird hier nicht geraten: die
    Zusammenfuehrungslogik (welcher Vorschlag aus welchem Lauf gewinnt, was
    ein Doppeltreffer ist) ist nicht gebaut, und eine willkuerlich gewaehlte
    Datei waere schlimmer als ein Abbruch - sie sieht wie ein Ergebnis aus.
    """
    dateien = laufdateien(job_dir)
    if len(dateien) > 1:
        namen = ", ".join(pfad.name for pfad in dateien)
        raise KetteFehlschlag(
            "zusammenfuehrung_fehlt",
            f"{len(dateien)} Zerlegungslaeufe ({namen}) - die Zusammenfuehrung ist nicht gebaut",
            CODE_ZUSAMMENFUEHRUNG_FEHLT,
        )
    if not dateien:
        raise KetteFehlschlag(
            "stufe_gescheitert",
            f"kein {_KANDIDATEN_LAUF_GLOB} in {job_dir} - die Zerlegung hat nichts hinterlassen",
            CODE_STUFE_GESCHEITERT,
        )
    ziel = job_dir / CANDIDATES_FILE_NAME
    shutil.copy2(dateien[0], ziel)
    return ziel


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------


def stufen_index(bezeichnung: str) -> int:
    """``4`` oder ``wortliste`` - beides bezeichnet dieselbe Stufe."""
    text = bezeichnung.strip()
    if text.isdigit():
        nummer = int(text)
        if 1 <= nummer <= len(STUFEN):
            return nummer - 1
        raise KetteFehlschlag(
            "stufe_unbekannt",
            f"{text} ist keine Stufennummer zwischen 1 und {len(STUFEN)}",
            CODE_STUFE_GESCHEITERT,
        )
    for nummer, stufe in enumerate(STUFEN):
        if stufe.name == text:
            return nummer
    namen = ", ".join(stufe.name for stufe in STUFEN)
    raise KetteFehlschlag(
        "stufe_unbekannt",
        f"{text} ist keine Stufe - bekannt sind: {namen}",
        CODE_STUFE_GESCHEITERT,
    )


def wird_uebersprungen(stufe: Stufe, job_dir: Path, zustand: dict[str, object]) -> bool:
    """Sage, ob die Ausgabe daliegt und der Eintrag dem nicht entgegensteht.

    Ein fehlender Eintrag und ein Eintrag ``offen`` zaehlen beide als
    "keine Auskunft" und stehen dem Ueberspringen nicht entgegen - siehe
    Modulkopf. Entgegen stehen nur ``laeuft`` und ``gescheitert``: beide
    stammen von einem Lauf, der in genau dieser Stufe abgebrochen ist, und
    die Ausgabe daneben kann eine halbe sein.
    """
    if not (job_dir / stufe.ausgabe).exists():
        return False
    stufen = zustand.get("stufen")
    eintrag = stufen.get(stufe.name) if isinstance(stufen, dict) else None
    if not isinstance(eintrag, dict):
        return True
    return eintrag.get("status") not in (STATUS_LAEUFT, STATUS_GESCHEITERT)


def _kopfzeile(
    nummer: int, stufe: Stufe, job_path: Path, modell: str = ZERLEGUNG_MODELL
) -> str:
    """``Stufe 3 von 6: Transkription, erwartet rund 12 min 22 s``.

    Die Erwartung steht nur bei der Transkription: sie ist die einzige
    Stufe, deren Dauer sich vorher aus einer bekannten Zahl abschaetzen
    laesst, und die einzige, bei der die Frage "warten oder weggehen?"
    ueberhaupt aufkommt.

    Bei der Zerlegung steht statt einer Dauer das Modell. Es ist die
    einzige Stufe, deren Ergebnis von einer Fahne abhaengt, und wer einen
    Nachschlag faehrt, will vor dem Warten sehen, dass die Fahne
    angekommen ist - nicht erst danach in ``kette.json``.
    """
    zeile = f"Stufe {nummer} von {len(STUFEN)}: {stufe.beschreibung}"
    if stufe.name == "zerlegung":
        return f"{zeile}, Modell {modell}"
    if stufe.name != "transcript":
        return zeile
    erwartet = erwartete_transkriptionsdauer_s(job_path)
    if erwartet is None:
        return zeile
    return f"{zeile}, erwartet rund {dauer_text(erwartet)}"


def _trockenlauf(
    job_dir: Path,
    job_path: Path,
    zustand: dict[str, object],
    bis: int,
    modell: str = ZERLEGUNG_MODELL,
) -> int:
    """Nenne je Stufe, was geschehen wuerde - und fuehre nichts aus.

    Auch die Zustandsdatei bleibt ungeschrieben: ein Probelauf, der eine
    ``kette.json`` hinterlaesst, hat den Bestand veraendert und war keiner.
    """
    print("Trockenlauf (--trocken): es wird nichts ausgefuehrt und nichts geschrieben.")
    uebersprungen = 0
    for nummer, stufe in enumerate(STUFEN[: bis + 1], start=1):
        print(_kopfzeile(nummer, stufe, job_path, modell))
        ziel = job_dir / stufe.ausgabe
        if wird_uebersprungen(stufe, job_dir, zustand):
            uebersprungen += 1
            print(f"  wird uebersprungen - {ziel} liegt bereits vor")
        else:
            print(f"  wuerde laufen -> {ziel}")
    print(f"{uebersprungen} von {bis + 1} Stufen wuerden uebersprungen.")
    return CODE_ERFOLG


def _fuehre_stufe_aus(
    stufe: Stufe,
    *,
    job_dir: Path,
    job_path: Path,
    jobs_root: Path,
    video_name: str,
    erzwingen: bool,
    modell: str = ZERLEGUNG_MODELL,
) -> None:
    """Fuehre eine einzelne Stufe aus; jeder Fehlschlag ist ein :class:`KetteFehlschlag`.

    Ein Rueckgabecode 0 allein genuegt nicht - die erwartete Ausgabe muss
    danach dasein. Sonst gilt eine Stufe als fertig, die nichts
    hinterlassen hat, und die naechste faellt ueber eine fehlende Datei.
    """
    if stufe.name == "zusammenfuehrung":
        quelle = laufdateien(job_dir)
        ziel = fuehre_zusammen(job_dir)
        print(f"  {ziel.name} aus {quelle[0].name} kopiert")
        return
    argv = stufen_argv(
        stufe,
        job_path=job_path,
        jobs_root=jobs_root,
        video_name=video_name,
        erzwingen=erzwingen,
        modell=modell,
    )
    assert argv is not None
    code = fuehre_prozess(argv, etikett=stufe.name)
    if code != 0:
        raise KetteFehlschlag(
            "stufe_gescheitert",
            f"Stufe {stufe.name} endete mit Rueckgabecode {code}",
            CODE_STUFE_GESCHEITERT,
        )
    ziel = job_dir / stufe.ausgabe
    if not ziel.exists():
        raise KetteFehlschlag(
            "stufe_gescheitert",
            f"Stufe {stufe.name} endete mit Rueckgabecode 0, aber {ziel} fehlt",
            CODE_STUFE_GESCHEITERT,
        )


def _parser() -> argparse.ArgumentParser:
    """Die Befehlszeile des Kettenlaeufers."""
    parser = argparse.ArgumentParser(
        description="Shorts-Kettenlaeufer: eine Aufnahme bis zu den Kandidaten"
    )
    parser.add_argument("--aufnahme", default=None, help="Name der Aufnahme; ohne: die juengste")
    parser.add_argument("--neu", action="store_true", help="alle Stufen erzwingen")
    parser.add_argument(
        "--neu-ab", default=None, metavar="STUFE", help="ab dieser Stufe erzwingen (Name oder 1-6)"
    )
    parser.add_argument(
        "--bis", default=None, metavar="STUFE", help="nach dieser Stufe anhalten (Name oder 1-6)"
    )
    parser.add_argument(
        "--trocken", action="store_true", help="nur nennen, was geschaehe; nichts ausfuehren"
    )
    parser.add_argument(
        "--modell",
        default=ZERLEGUNG_MODELL,
        metavar="NAME",
        help=f"Modell der Zerlegung, an claude --model durchgereicht (Vorgabe: {ZERLEGUNG_MODELL})",
    )
    parser.add_argument("--wurzel", type=Path, default=None, help="abweichende Repo-Wurzel")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Sechs Stufen, ein Gedaechtnis - und nach jeder Stufe ein Stand auf der Platte."""
    args = _parser().parse_args(argv)
    wurzel: Path = args.wurzel if args.wurzel is not None else Path.cwd()
    jobs_root = wurzel / JOBS_ROOT
    job_dir = jobs_root

    try:
        ab = 0 if args.neu else (stufen_index(args.neu_ab) if args.neu_ab else len(STUFEN))
        bis = stufen_index(args.bis) if args.bis else len(STUFEN) - 1

        print("Aufnahme bestimmen")
        video_name, vorgefunden = bestimme_aufnahme(jobs_root, args.aufnahme)
        job_dir = jobs_root / video_name
        job_path = job_dir / JOB_FILE_NAME
        print(f"  Aufnahme:       {video_name}")
        print(f"  Aufnahmeordner: {job_dir}")

        zustand = vorgefunden if vorgefunden is not None else leerer_zustand(video_name)
        zustand["video_name"] = video_name

        if args.trocken:
            return _trockenlauf(job_dir, job_path, zustand, bis, args.modell)

        zustand_pfad = job_dir / ZUSTAND_FILE_NAME
        for nummer, stufe in enumerate(STUFEN[: bis + 1], start=1):
            print(_kopfzeile(nummer, stufe, job_path, args.modell))
            eintrag = _eintrag(zustand, stufe.name)
            erzwingen = nummer - 1 >= ab
            if not erzwingen and wird_uebersprungen(stufe, job_dir, zustand):
                print(f"  uebersprungen - {job_dir / stufe.ausgabe} liegt bereits vor")
                eintrag.update(
                    status=STATUS_FERTIG,
                    ausgabe=str(job_dir / stufe.ausgabe),
                    meldung="uebersprungen, Ausgabe lag bereits vor",
                )
                schreibe_zustand(zustand_pfad, zustand)
                continue
            begonnen = time.monotonic()
            eintrag.update(
                status=STATUS_LAEUFT,
                begonnen_am=_jetzt(),
                beendet_am=None,
                dauer_s=None,
                ausgabe=None,
                meldung=None,
            )
            if stufe.name == "zerlegung":
                # Nur hier und nur, wenn die Stufe wirklich anlaeuft: eine
                # uebersprungene Zerlegung hat mit diesem Modell nichts
                # gefahren, und der Eintrag des vorigen Laufs bleibt der
                # wahre. Ihn mit der Fahne von heute zu ueberschreiben
                # machte aus der Buchfuehrung eine Behauptung.
                eintrag["modell"] = args.modell
            schreibe_zustand(zustand_pfad, zustand)
            try:
                _fuehre_stufe_aus(
                    stufe,
                    job_dir=job_dir,
                    job_path=job_path,
                    jobs_root=jobs_root,
                    video_name=video_name,
                    erzwingen=erzwingen,
                    modell=args.modell,
                )
            except KetteFehlschlag as fehler:
                eintrag.update(
                    status=STATUS_GESCHEITERT,
                    beendet_am=_jetzt(),
                    dauer_s=round(time.monotonic() - begonnen, 1),
                    meldung=fehler.text,
                )
                schreibe_zustand(zustand_pfad, zustand)
                raise
            dauer = time.monotonic() - begonnen
            eintrag.update(
                status=STATUS_FERTIG,
                beendet_am=_jetzt(),
                dauer_s=round(dauer, 1),
                ausgabe=str(job_dir / stufe.ausgabe),
                meldung=None,
            )
            schreibe_zustand(zustand_pfad, zustand)
            print(f"  fertig in {dauer_text(dauer)} -> {job_dir / stufe.ausgabe}")
    except KetteFehlschlag as fehler:
        print(f"ANGEHALTEN [{fehler.code_name}]: {fehler.text}")
        return fehler.rueckgabecode

    print(f"Kette fertig: {job_dir / CANDIDATES_FILE_NAME}")
    print("Weiter mit: python -m matrix_auto_cutter.shorts.urteilslauf")
    return CODE_ERFOLG


if __name__ == "__main__":  # pragma: no cover - Einstieg nur ueber -m
    raise SystemExit(main())
