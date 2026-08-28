"""Raeume die Zwischenstufen aus den Bauordnern einer Aufnahme.

Ein Kandidatenordner traegt vier Videodateien: ``ausschnitt.mp4`` ->
``leinwand.mp4`` -> ``mit-avatar.mp4`` -> ``short.mp4``. Jede Stufe frisst die
Ausgabe der vorigen, und ``build.py`` raeumt keine davon auf. Gebraucht wird
am Ende nur ``short.mp4``; die drei Zwischenstufen machen rund 70 % des
Zielordners aus.

Gelesen werden die Zwischenstufen NUR innerhalb eines einzigen Bauvorgangs
(``build._build_one_candidate`` schreibt jede wenige Zeilen, bevor sie die
naechste Stufe liest). Kein spaeterer Lauf sieht sie an: der Teilbau in
``urteilslauf`` entscheidet allein an ``short.mp4`` und an den Ordnernamen.
Deshalb kostet ihr Loeschen keinen spaeteren Lauf.

Voreinstellung ist das Anzeigen, nicht das Loeschen. Ohne
``--wirklich-loeschen`` wird nichts angefasst - eine Fahne, die man vergessen
kann, darf nicht die Fahne sein, die vor dem Loeschen schuetzt.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.urteilslauf import RENDER_WURZEL

ZWISCHENSTUFEN = ("ausschnitt.mp4", "leinwand.mp4", "mit-avatar.mp4")
"""Die drei loeschbaren Baustufen - eine FESTE Liste, kein Muster.

Absichtlich kein ``glob("*.mp4")`` und keine Endungsregel: eine Musterregel
loescht eines Tages etwas, das noch nicht erfunden war. Kommt eine fuenfte
Stufe hinzu, wird sie hier von Hand eingetragen - oder sie bleibt liegen, was
der harmlose der beiden Fehler ist.
"""

SHORT_NAME = "short.mp4"
PROTOKOLL_DIR = Path("artefakte/repeat/aufraeumen")
MINDESTALTER_STUNDEN_VORGABE = 48
"""Wie alt die juengste ``short.mp4`` einer Aufnahme sein muss.

Solange eine Aufnahme noch im Umlauf ist, koennte ein Teilbau folgen. Ein
Teilbau baut aus dem Quellmaterial neu, die Zwischenstufen sind dabei zwar
entbehrlich - aber der Nutzen des Loeschens ist in diesem Fenster gering und
das Risiko unnoetig. Nach 48 Stunden ist die Aufnahme ohnehin verfallen
(``urteilslauf.VERFALL_STUNDEN``) und kein Bau mehr zu erwarten.
"""

CODE_ERFOLG = 0
CODE_WURZEL_FEHLT = 2
CODE_KEINE_AUFNAHME = 3
CODE_ORDNER_OHNE_SHORT = 4

_CODE_NAMEN = {
    CODE_WURZEL_FEHLT: "wurzel_fehlt",
    CODE_KEINE_AUFNAHME: "keine_aufnahme",
    CODE_ORDNER_OHNE_SHORT: "ordner_ohne_short",
}

_STUNDE_SEKUNDEN = 3600.0


@dataclass(frozen=True)
class KandidatPlan:
    """Was in genau einem Kandidatenordner geschehen soll."""

    ordner: Path
    dateien: tuple[Path, ...]
    bytes_frei: int
    grund: str


@dataclass(frozen=True)
class Plan:
    """Der Aufraeumplan einer Aufnahme - vollstaendig, bevor etwas geschieht."""

    aufnahme_dir: Path
    loeschbar: tuple[KandidatPlan, ...] = ()
    uebersprungen: tuple[KandidatPlan, ...] = ()

    @property
    def dateien(self) -> tuple[Path, ...]:
        """Jede loeschbare Datei des Plans, ueber alle Kandidatenordner hinweg."""
        return tuple(pfad for eintrag in self.loeschbar for pfad in eintrag.dateien)

    @property
    def bytes_frei(self) -> int:
        """Die Summe der Bytes, die das Loeschen freigibt."""
        return sum(eintrag.bytes_frei for eintrag in self.loeschbar)

    @property
    def ohne_short(self) -> tuple[KandidatPlan, ...]:
        """Die uebersprungenen Ordner, denen die ``short.mp4`` ganz fehlt."""
        return tuple(e for e in self.uebersprungen if e.grund.startswith("keine "))


@dataclass
class Ergebnis:
    """Was tatsaechlich geschehen ist."""

    geloescht: list[dict[str, object]] = field(default_factory=list)
    verschwunden: list[str] = field(default_factory=list)
    uebersprungen: list[dict[str, str]] = field(default_factory=list)
    bytes_frei: int = 0


def finde_bauordner(wurzel: Path) -> list[Path]:
    """Die Aufnahmeordner unter der Renderwurzel, nach Namen sortiert.

    Der Ordnername traegt den Aufnahmezeitpunkt, die Sortierung ist damit
    zugleich die zeitliche Reihenfolge.
    """
    if not wurzel.is_dir():
        return []
    return sorted(eintrag for eintrag in wurzel.iterdir() if eintrag.is_dir())


def zwischenstufen(kandidat_dir: Path) -> list[Path]:
    """Die vorhandenen Dateien aus :data:`ZWISCHENSTUFEN`, in fester Reihenfolge.

    Nur exakte Namen. Kein Muster, keine Endungsregel, kein ``glob("*.mp4")``:
    eine Musterregel loescht eines Tages etwas, das noch nicht erfunden war -
    eine ``short.mp4.partial`` etwa, oder eine Stufe, die es heute nicht gibt.
    """
    return [
        kandidat_dir / name for name in ZWISCHENSTUFEN if (kandidat_dir / name).is_file()
    ]


def darf_aufraeumen(kandidat_dir: Path) -> tuple[bool, str]:
    """Sage, ob in diesem Ordner geraeumt werden darf - und warum (nicht).

    Wahr NUR, wenn eine ``short.mp4`` im selben Ordner liegt und groesser als
    0 Byte ist. Die ``short.mp4`` ist das Ergebnis der ganzen Linie; fehlt sie
    oder ist sie leer, ist der Bau unfertig und die Zwischenstufen sind das
    einzige, was von ihm uebrig ist.
    """
    short = kandidat_dir / SHORT_NAME
    if not short.is_file():
        return False, f"keine {SHORT_NAME}"
    groesse = short.stat().st_size
    if groesse <= 0:
        return False, f"{SHORT_NAME} ist leer (0 Byte)"
    return True, f"{SHORT_NAME} vorhanden ({groesse} Byte)"


def _kandidatenordner(aufnahme_dir: Path) -> list[Path]:
    return sorted(
        eintrag
        for eintrag in aufnahme_dir.iterdir()
        if eintrag.is_dir() and eintrag.name.startswith("kandidat-")
    )


def plane_aufraeumung(aufnahme_dir: Path) -> Plan:
    """Sammle je Kandidatenordner, was loeschbar ist - und was nicht, mit Grund."""
    if not aufnahme_dir.is_dir():
        return Plan(aufnahme_dir=aufnahme_dir)
    loeschbar: list[KandidatPlan] = []
    uebersprungen: list[KandidatPlan] = []
    for ordner in _kandidatenordner(aufnahme_dir):
        erlaubt, grund = darf_aufraeumen(ordner)
        if not erlaubt:
            uebersprungen.append(KandidatPlan(ordner, (), 0, grund))
            continue
        dateien = zwischenstufen(ordner)
        if not dateien:
            uebersprungen.append(
                KandidatPlan(ordner, (), 0, "keine Zwischenstufe mehr vorhanden")
            )
            continue
        frei = sum(pfad.stat().st_size for pfad in dateien)
        loeschbar.append(KandidatPlan(ordner, tuple(dateien), frei, grund))
    return Plan(
        aufnahme_dir=aufnahme_dir,
        loeschbar=tuple(loeschbar),
        uebersprungen=tuple(uebersprungen),
    )


def juengste_short_zeit(aufnahme_dir: Path) -> float | None:
    """Die Aenderungszeit der juengsten ``short.mp4`` - ``None``, wenn keine da ist."""
    zeiten = [
        (ordner / SHORT_NAME).stat().st_mtime
        for ordner in _kandidatenordner(aufnahme_dir)
        if (ordner / SHORT_NAME).is_file()
    ]
    return max(zeiten) if zeiten else None


def alter_stunden(aufnahme_dir: Path, jetzt: float | None = None) -> float | None:
    """Wie alt die juengste ``short.mp4`` der Aufnahme in Stunden ist."""
    zeit = juengste_short_zeit(aufnahme_dir)
    if zeit is None:
        return None
    bezug = jetzt if jetzt is not None else datetime.now().timestamp()
    return (bezug - zeit) / _STUNDE_SEKUNDEN


def fuehre_aus(plan: Plan) -> Ergebnis:
    """Loesche, was der Plan nennt - und ueberlebe, was zwischendurch verschwand.

    Eine Datei, die zwischen Plan und Ausfuehrung verschwindet, ist kein
    Fehlschlag: sie wird gemeldet, und der Lauf geht weiter.
    """
    ergebnis = Ergebnis()
    for eintrag in plan.uebersprungen:
        ergebnis.uebersprungen.append(
            {"ordner": str(eintrag.ordner), "grund": eintrag.grund}
        )
    for eintrag in plan.loeschbar:
        for pfad in eintrag.dateien:
            try:
                groesse = pfad.stat().st_size
                pfad.unlink()
            except FileNotFoundError:
                print(f"  verschwunden zwischen Plan und Ausfuehrung: {pfad}")
                ergebnis.verschwunden.append(str(pfad))
                continue
            except OSError as fehler:
                print(f"  nicht loeschbar: {pfad} ({fehler})")
                ergebnis.uebersprungen.append(
                    {"ordner": str(pfad), "grund": f"nicht loeschbar: {fehler}"}
                )
                continue
            ergebnis.geloescht.append({"pfad": str(pfad), "bytes": groesse})
            ergebnis.bytes_frei += groesse
    return ergebnis


def _protokoll_pfad(jetzt: datetime | None = None) -> Path:
    zeit = jetzt if jetzt is not None else datetime.now()
    return PROTOKOLL_DIR / f"{zeit:%Y-%m-%d-%H%M%S}.json"


def schreibe_protokoll(
    pfad: Path, aufnahmen: list[str], ergebnis: Ergebnis, wirklich: bool
) -> Path:
    """Schreibe das Protokoll atomar - erst daneben, dann darueber."""
    pfad.parent.mkdir(parents=True, exist_ok=True)
    inhalt = {
        "zeitpunkt": datetime.now().isoformat(timespec="seconds"),
        "wirklich_geloescht": wirklich,
        "aufnahmen": aufnahmen,
        "geloescht": ergebnis.geloescht,
        "bytes_frei": ergebnis.bytes_frei,
        "anzahl_geloescht": len(ergebnis.geloescht),
        "verschwunden": ergebnis.verschwunden,
        "uebersprungen": ergebnis.uebersprungen,
    }
    temporaer = pfad.with_name(f"{pfad.name}.tmp")
    temporaer.write_text(
        json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    replace_atomically(temporaer, pfad)
    return pfad


def _melde_angehalten(code: int, text: str) -> None:
    print(f"ANGEHALTEN [{_CODE_NAMEN.get(code, str(code))}]: {text}")


def _zeige_plan(plan: Plan) -> None:
    print(f"Aufnahme: {plan.aufnahme_dir.name}")
    print(f"  Kandidatenordner:   {len(plan.loeschbar) + len(plan.uebersprungen)}")
    print(f"  loeschbare Dateien: {len(plan.dateien)}")
    print(f"  freiwerdende Bytes: {plan.bytes_frei}")
    for eintrag in plan.loeschbar:
        namen = ", ".join(pfad.name for pfad in eintrag.dateien)
        print(f"    {eintrag.ordner.name}: {namen} ({eintrag.bytes_frei} Byte)")
    for eintrag in plan.uebersprungen:
        print(f"    uebersprungen {eintrag.ordner.name}: {eintrag.grund}")


def _ist_schlichter_name(name: str) -> bool:
    """Sage, ob ``name`` ein blosser Ordnername ist - kein Pfad, kein Ausbruch.

    ``--aufnahme`` nimmt einen NAMEN, keinen Pfad. Ohne diese Pruefung traegt
    ``wurzel / name`` ein ``..`` oder einen absoluten Pfad klaglos aus der
    Renderwurzel heraus, und ein Werkzeug, das loescht, darf sich nicht darauf
    verlassen, dass der Aufrufer es gut meint.
    """
    if not name or name in (".", ".."):
        return False
    if Path(name).is_absolute() or len(Path(name).parts) != 1:
        return False
    return not any(zeichen in name for zeichen in ("/", "\\", ":"))


def _waehle_aufnahmen(
    wurzel: Path, aufnahme: str | None, alle: bool, mindestalter: float
) -> tuple[list[Path], list[str]]:
    """Die zu raeumenden Aufnahmen und die Gruende der uebergangenen."""
    if aufnahme is not None:
        if not _ist_schlichter_name(aufnahme):
            return [], [f"{aufnahme}: kein schlichter Ordnername"]
        ziel = wurzel / aufnahme
        return ([ziel] if ziel.is_dir() else []), []
    hinweise: list[str] = []
    reif: list[Path] = []
    for ordner in finde_bauordner(wurzel):
        alter = alter_stunden(ordner)
        if alter is None:
            hinweise.append(f"{ordner.name}: keine {SHORT_NAME} gefunden")
            continue
        if alter < mindestalter:
            hinweise.append(
                f"{ordner.name}: erst {alter:.1f} h alt "
                f"(Mindestalter {mindestalter:g} h)"
            )
            continue
        reif.append(ordner)
    if alle:
        return reif, hinweise
    return reif[:1], hinweise


def main(argv: list[str] | None = None) -> int:
    """Einstieg der Befehlszeile - zeigt den Plan, loescht nur auf Geheiss."""
    parser = argparse.ArgumentParser(
        prog="python -m matrix_auto_cutter.shorts.aufraeumen",
        description="Entferne die Zwischenstufen aus den Bauordnern einer Aufnahme.",
    )
    parser.add_argument("--aufnahme", default=None)
    parser.add_argument("--alle", action="store_true")
    parser.add_argument("--wirklich-loeschen", action="store_true")
    parser.add_argument(
        "--mindestalter-stunden", type=float, default=MINDESTALTER_STUNDEN_VORGABE
    )
    args = parser.parse_args(argv)

    wurzel = Path(RENDER_WURZEL)
    if not wurzel.is_dir():
        _melde_angehalten(CODE_WURZEL_FEHLT, f"Renderwurzel nicht erreichbar: {wurzel}")
        return CODE_WURZEL_FEHLT

    aufnahmen, hinweise = _waehle_aufnahmen(
        wurzel, args.aufnahme, args.alle, args.mindestalter_stunden
    )
    for hinweis in hinweise:
        print(f"  uebergangen: {hinweis}")
    if not aufnahmen:
        if args.aufnahme is not None:
            _melde_angehalten(
                CODE_KEINE_AUFNAHME, f"Aufnahme nicht gefunden: {args.aufnahme}"
            )
        else:
            _melde_angehalten(
                CODE_KEINE_AUFNAHME,
                f"keine Aufnahme erfuellt das Mindestalter von "
                f"{args.mindestalter_stunden:g} Stunden",
            )
        return CODE_KEINE_AUFNAHME

    plaene = [plane_aufraeumung(ordner) for ordner in aufnahmen]
    for plan in plaene:
        _zeige_plan(plan)
    gesamt_dateien = sum(len(plan.dateien) for plan in plaene)
    gesamt_bytes = sum(plan.bytes_frei for plan in plaene)
    print(f"Summe: {gesamt_dateien} Dateien, {gesamt_bytes} Byte")

    ohne_short = [e for plan in plaene for e in plan.ohne_short]

    if not args.wirklich_loeschen:
        print("Nur Plan - ohne --wirklich-loeschen wird nichts geloescht.")
        if ohne_short:
            for eintrag in ohne_short:
                _melde_angehalten(
                    CODE_ORDNER_OHNE_SHORT,
                    f"{eintrag.ordner} - {eintrag.grund}; nicht angefasst",
                )
            return CODE_ORDNER_OHNE_SHORT
        return CODE_ERFOLG

    gesamt = Ergebnis()
    # Das Protokoll wird auch dann geschrieben, wenn eine Loeschung unerwartet
    # abbricht: geloeschte Dateien ohne Nachweis waeren der schlimmere Ausgang.
    try:
        for plan in plaene:
            teil = fuehre_aus(plan)
            gesamt.geloescht.extend(teil.geloescht)
            gesamt.verschwunden.extend(teil.verschwunden)
            gesamt.uebersprungen.extend(teil.uebersprungen)
            gesamt.bytes_frei += teil.bytes_frei
    finally:
        protokoll = schreibe_protokoll(
            _protokoll_pfad(), [plan.aufnahme_dir.name for plan in plaene], gesamt, True
        )
    print(
        f"Geloescht: {len(gesamt.geloescht)} Dateien, {gesamt.bytes_frei} Byte "
        f"frei. Protokoll: {protokoll}"
    )
    if ohne_short:
        for eintrag in ohne_short:
            _melde_angehalten(
                CODE_ORDNER_OHNE_SHORT,
                f"{eintrag.ordner} - {eintrag.grund}; nicht angefasst",
            )
        return CODE_ORDNER_OHNE_SHORT
    return CODE_ERFOLG


if __name__ == "__main__":  # pragma: no cover - Einstieg
    raise SystemExit(main())
