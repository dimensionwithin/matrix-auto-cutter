r"""Auftrag shorts-auswahl: aus Kandidaten plus Urteilen eine Bauliste machen.

Zwischen Urteil (``judge_server.py``) und Bau (``build.py``) fehlte ein
Werkzeug: ``build.py`` liest ausschliesslich ``kandidaten.json`` und kennt
keine Urteile - ohne dieses Modul wuerde ein Aufruf alle Kandidaten bauen,
auch die verworfenen. ``waehle_kandidaten`` trifft die Auswahl (nur
``urteil == "ja"`` wird gebaut), ``pruefe_uebereinstimmung`` sichert davor
ab, dass ein Urteil wirklich zu dem Kandidaten gehoert, den es zu meinen
scheint - ein falsch zugeordnetes Urteil ist schlimmer als gar keins.

``bauliste.json`` traegt bewusst dasselbe Schema wie ``kandidaten.json``
(Wurzel ``kandidaten``, unveraenderte ``index``-Werte) - so kann sie
``build.py`` unveraendert als ``KANDIDATEN_PATH`` uebergeben werden, und die
von ``build.py`` gebildeten Ordnernamen (``kandidat-{index:02d}``) bleiben
auf die urspruenglichen Kandidaten bezogen.

``labels/repeat/trefferquote.json`` haelt fest, wie oft Kandidaten
angenommen wurden - das laesst sich aus ``bauliste.json`` allein nicht mehr
rekonstruieren, sobald das naechste Mal ueberschrieben wird, deshalb wird
hier angehaengt statt ersetzt.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.candidates import (
    CANDIDATES_FILE_NAME,
    Candidate,
    CandidatesSchemaError,
    load_candidates,
)
from matrix_auto_cutter.shorts.judge_server import Urteil, load_urteile

BAULISTE_FILE_NAME = "bauliste.json"
BUENDEL_FILE_NAME = "buendel.json"
BUENDEL_ARTIFACT_TYPE = "matrix_auto_cutter_shorts_buendel"
BUENDEL_SCHEMA_VERSION = "1.0"
# So viele Gruppen traegt die Vorauswahl der Urteilsseite. Der Nutzer
# veroeffentlicht 4 bis 10 Shorts je Aufnahme und hat dafuer 48 Stunden -
# 15 Gruppen sind genug Auswahl dafuer und wenig genug, um sie in dem
# Fenster wirklich durchzusehen.
VORAUSWAHL_GROESSE = 15
LAUFDATEI_GLOB = "kandidaten-lauf*.json"
_LAUFDATEI_MUSTER = re.compile(r"^kandidaten-lauf(\d+)\.json$")
TREFFERQUOTE_PFAD = Path("labels/repeat/trefferquote.json")
TREFFERQUOTE_SCHEMA_VERSION = "1.0"

_ZIELBEREICH_MIN_MS = 8000
_ZIELBEREICH_MAX_MS = 15000

_CODE_KANDIDATEN_UNLESBAR = 2
_CODE_KEINE_URTEILSDATEI = 2
_CODE_URTEILE_KEIN_JSON = 3
_CODE_KEINE_ANNAHMEN = 4
_CODE_URTEILE_ABWEICHUNG = 5
_CODE_KEINE_LAUFDATEI = 2
_CODE_URTEILE_VORHANDEN = 9


# --------------------------------------------------------------------------
# Zusammenfuehrung mehrerer Zerlegungslaeufe
# --------------------------------------------------------------------------


def lade_laufdateien(job_dir: Path) -> list[tuple[int, dict[str, object]]]:
    """Alle ``kandidaten-laufN.json`` des Auftragsordners, nach Laufnummer sortiert.

    Sortiert wird nach der ZAHL, nicht nach dem Namen: bei zehn Laeufen
    stuende ``kandidaten-lauf10.json`` alphabetisch vor
    ``kandidaten-lauf2.json``, und die Nummerierungsregel (der kleinste
    Lauf gibt vor) haenge dann an einer Zeichenkettensortierung.

    Dateien, deren Name nicht auf ``kandidaten-lauf<Zahl>.json`` passt,
    werden uebergangen - der Glob findet auch ``kandidaten-lauf1.bak.json``.
    """
    gefunden: list[tuple[int, dict[str, object]]] = []
    for pfad in sorted(job_dir.glob(LAUFDATEI_GLOB)):
        treffer = _LAUFDATEI_MUSTER.match(pfad.name)
        if treffer is None or not pfad.is_file():
            continue
        roh = json.loads(pfad.read_text(encoding="utf-8"))
        if not isinstance(roh, dict):
            raise CandidatesSchemaError(f"{pfad.name}: erwartet ein Wurzelobjekt")
        gefunden.append((int(treffer.group(1)), roh))
    gefunden.sort(key=lambda paar: paar[0])
    return gefunden


def _zeitbereich(kandidat: dict[str, object]) -> tuple[int, int]:
    start = kandidat.get("start_ms")
    ende = kandidat.get("end_ms")
    if isinstance(start, bool) or isinstance(ende, bool):
        raise CandidatesSchemaError("start_ms/end_ms muessen Ganzzahlen sein, nicht Wahrheitswerte")
    if not isinstance(start, int) or not isinstance(ende, int):
        raise CandidatesSchemaError("Kandidat ohne ganzzahlige 'start_ms'/'end_ms'")
    return start, ende


def gleicher_kandidat(a: dict[str, object], b: dict[str, object]) -> bool:
    r"""Sage, ob zwei Kandidaten aus verschiedenen Laeufen denselben Ausschnitt meinen.

    Die Regel steht in ``labels\repeat\shorts-kriterien.yaml``, Abschnitt
    ``zerlegung_laeuft_zweimal``: "als dasselbe gilt, was sich um mehr als
    die Haelfte der kuerzeren Dauer ueberlappt". Also nicht die laengere
    und nicht die Summe - die kuerzere Dauer ist der Massstab, sonst
    verschluckte ein langer Ausschnitt jeden kurzen, der zufaellig in ihm
    liegt.

    "Mehr als die Haelfte" ist streng gemeint: genau die Haelfte reicht
    nicht.
    """
    a_start, a_ende = _zeitbereich(a)
    b_start, b_ende = _zeitbereich(b)
    ueberlappung = min(a_ende, b_ende) - max(a_start, b_start)
    if ueberlappung <= 0:
        return False
    kuerzere = min(a_ende - a_start, b_ende - b_start)
    if kuerzere <= 0:
        return False
    return ueberlappung * 2 > kuerzere


def _ist_laenger(neu: dict[str, object], alt: dict[str, object]) -> bool:
    neu_start, neu_ende = _zeitbereich(neu)
    alt_start, alt_ende = _zeitbereich(alt)
    return (neu_ende - neu_start) > (alt_ende - alt_start)


def _jetzt() -> str:
    """Der Zeitpunkt in ISO-Form mit Zeitzone - so steht er in jeder Artefaktdatei."""
    return datetime.now(UTC).isoformat()


def _bilde_verweise_ab(
    kandidaten: list[dict[str, object]], zuordnung: dict[int, int]
) -> int:
    """Schreibe ``enthaelt`` auf die neue Nummerierung um; melde die weggefallenen Verweise.

    Ein Kandidat aus einem spaeteren Lauf traegt in ``enthaelt`` die Indizes
    SEINES Laufs. Die Zusammenfuehrung gibt ihm einen neuen Index - und bis
    zum 27.8. blieben seine Verweise trotzdem stehen. Sie zeigten danach auf
    irgendwelche Kandidaten des Grundsatzes: bei der Aufnahme vom 25.8.
    traegt Kandidat 67 ``"enthaelt": [36]``, obwohl der zusammengefuehrte
    Kandidat 36 sechs Minuten entfernt liegt. Heute ist das folgenlos, weil
    die Urteilsseite ``enthaelt`` nur zum Gruppieren nutzt; sobald ein
    Verweis auf einen Index zeigt, den es NICHT gibt, bricht
    ``parse_candidates`` den Bau mit einer Schemameldung ab.

    Ein Verweis ohne Entsprechung im Zielsatz faellt WEG statt falsch
    stehenzubleiben. Ein falscher Verweis behauptet etwas; ein fehlender
    behauptet nichts - und ``enthaelt`` ist eine Zusatzangabe, kein
    Pflichtfeld. Damit das nicht stillschweigend geschieht, zaehlt diese
    Funktion die Faelle; die Summe steht als Wurzelfeld
    ``verworfene_verweise`` in der Ausgabe.

    Ein Verweis auf den eigenen neuen Index faellt ebenfalls weg: er kann
    entstehen, wenn zwei Kandidaten eines Laufs auf denselben Eintrag des
    Grundsatzes abgebildet werden, und ``parse_candidates`` weist eine
    Selbstreferenz zurueck.
    """
    verworfen = 0
    for kandidat in kandidaten:
        roh = kandidat.get("enthaelt")
        if not isinstance(roh, list):
            continue
        eigener = kandidat.get("index")
        neu: list[int] = []
        for verweis in roh:
            ziel = zuordnung.get(verweis) if isinstance(verweis, int) else None
            if isinstance(verweis, bool):
                ziel = None
            if ziel is None or ziel == eigener or ziel in neu:
                verworfen += 1
                continue
            neu.append(ziel)
        kandidat["enthaelt"] = neu
    return verworfen


def fuehre_zusammen(saetze: list[tuple[int, dict[str, object]]]) -> dict[str, object]:
    """Vereinige mehrere Zerlegungslaeufe zu einem Kandidatensatz.

    Nummerierung - der Grund, warum sie nicht neu vergeben wird:
    Urteile haengen am ``index`` und an sonst nichts
    (``judge_server.Urteil``, ``auswahl.pruefe_uebereinstimmung``). Wer bei
    der Zusammenfuehrung neu nummeriert, laesst jedes vorhandene Urteil auf
    einen fremden Kandidaten zeigen - und zwar lautlos, denn eine Zahl
    passt immer auf eine Zahl.

    Ein Index ist ein Versprechen. Wer seinen Inhalt aendert, macht jedes
    Urteil darauf ungueltig - und Urteilszeit ist das einzige Artefakt
    dieser Kette, das sich nicht neu erzeugen laesst: Aufnahme, Transkript,
    Wortliste, Zerlegung und Bau laufen jederzeit wieder, ein einmal
    gefaelltes Urteil nicht. Deshalb wird ein Kandidat aus dem Grundsatz
    NIE inhaltlich veraendert - weder Grenzen noch Titel noch Begruendung.
    Bis zum 26.8. ersetzte diese Stelle die laengere Fassung in den
    vorhandenen Eintrag hinein; sechs beurteilte Indizes (10, 14, 16, 18,
    33, 34) meinten danach einen anderen Ausschnitt als das Urteil auf
    ihnen. Das ist der Fehler, den die folgende Regel abstellt:

    * Der Satz mit der KLEINSTEN Laufnummer gibt den Grundsatz vor; seine
      Kandidaten behalten ``index`` UND Inhalt unveraendert.
    * Ein Kandidat aus einem spaeteren Lauf ohne Entsprechung
      (:func:`gleicher_kandidat`) wird hinten angehaengt und bekommt den
      naechsten freien Index.
    * Gleicht er einem vorhandenen und ist seine Fassung LAENGER, wird er
      ebenfalls hinten angehaengt - als eigener Kandidat mit eigenem Index
      und dem Feld ``laengere_fassung_von`` (Index des Gegenstuecks). Der
      kuerzere behaelt Index, Inhalt und Urteil; der Nutzer sieht beide und
      entscheidet.
    * Gleicht er einem vorhandenen und ist nicht laenger, faellt er weg.

    Die laengeren Fassungen eines Laufs kommen dabei hinter dessen neue
    Kandidaten - erst die 24 neuen, dann die 6 laengeren, nicht
    verschraenkt. So bleibt die Reihenfolge dieselbe, gleich ob ein Lauf in
    einem Rutsch oder nachtraeglich zusammengefuehrt wird.

    ``enthaelt`` wird dabei auf die neue Nummerierung abgebildet
    (:func:`_bilde_verweise_ab`), und was keine Entsprechung hat, faellt
    weg statt falsch stehenzubleiben. Die Zahl der weggefallenen Verweise
    steht als Wurzelfeld ``verworfene_verweise``. Die Kandidaten des
    Grundsatzes bleiben davon unberuehrt: ihre Indizes aendern sich nicht,
    also aendern sich ihre Verweise nicht.

    Bei EINEM Satz ist das Ergebnis eine Kopie mit Wurzelfeldern - kein
    Sonderfall im Code, sondern derselbe Weg mit einer leeren zweiten
    Runde.
    """
    if not saetze:
        raise CandidatesSchemaError("kein einziger Zerlegungslauf zum Zusammenfuehren")
    geordnet = sorted(saetze, key=lambda paar: paar[0])
    erste_nummer, erster_satz = geordnet[0]

    ergebnis: list[dict[str, object]] = []
    hoechster_index = 0
    for roh in _kandidatenliste(erster_satz):
        kandidat = dict(roh)
        kandidat["aus_lauf"] = erste_nummer
        index = kandidat.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            hoechster_index = max(hoechster_index, index)
        ergebnis.append(kandidat)

    verworfene_verweise = 0
    for nummer, satz in geordnet[1:]:
        nachtraege: list[tuple[dict[str, object], int, int | None]] = []
        # Was in DIESEM Lauf welche Nummer hatte und im Ergebnis welche
        # bekommt. Erst wenn der ganze Lauf durch ist, steht die Abbildung
        # vollstaendig - ein ``enthaelt`` darf auf einen Kandidaten zeigen,
        # der weiter hinten in derselben Laufdatei steht.
        zuordnung: dict[int, int] = {}
        aus_diesem_lauf: list[dict[str, object]] = []
        for roh in _kandidatenliste(satz):
            alt_index = roh.get("index")
            if not isinstance(alt_index, int) or isinstance(alt_index, bool):
                alt_index = None
            vorhanden = next(
                (eintrag for eintrag in ergebnis if gleicher_kandidat(eintrag, roh)), None
            )
            if vorhanden is None:
                kandidat = dict(roh)
                hoechster_index += 1
                kandidat["index"] = hoechster_index
                kandidat["aus_lauf"] = nummer
                if alt_index is not None:
                    zuordnung[alt_index] = hoechster_index
                ergebnis.append(kandidat)
                aus_diesem_lauf.append(kandidat)
                continue
            gegenstueck = vorhanden.get("index")
            if not isinstance(gegenstueck, int) or isinstance(gegenstueck, bool):
                raise CandidatesSchemaError("Kandidat ohne ganzzahligen 'index'")
            if not _ist_laenger(roh, vorhanden):
                # Er faellt weg, sein Material aber nicht: es steht schon
                # unter ``gegenstueck``. Ein Verweis auf ihn zeigt danach
                # dorthin - sonst ginge er verloren, obwohl es ihn gibt.
                if alt_index is not None:
                    zuordnung[alt_index] = gegenstueck
                continue
            nachtraege.append((dict(roh), gegenstueck, alt_index))
        for kandidat, gegenstueck, alt_index in nachtraege:
            hoechster_index += 1
            kandidat["index"] = hoechster_index
            kandidat["aus_lauf"] = nummer
            kandidat["laengere_fassung_von"] = gegenstueck
            if alt_index is not None:
                zuordnung[alt_index] = hoechster_index
            ergebnis.append(kandidat)
            aus_diesem_lauf.append(kandidat)
        verworfene_verweise += _bilde_verweise_ab(aus_diesem_lauf, zuordnung)

    laeufe = [nummer for nummer, _ in geordnet]
    payload: dict[str, object] = {
        schluessel: wert
        for schluessel, wert in erster_satz.items()
        if schluessel not in ("kandidaten", "lauf", "modell")
    }
    payload["kandidaten"] = ergebnis
    payload["lauf"] = erste_nummer
    payload["laeufe"] = laeufe
    modelle = {str(nummer): _modellname(satz) for nummer, satz in geordnet}
    payload["modelle"] = modelle
    # ``modell`` bleibt zusaetzlich stehen, obwohl ``modelle`` dasselbe genauer
    # sagt: alles, was nach der Zusammenfuehrung kommt, sucht das Wurzelfeld
    # und nicht die Abbildung. Ohne diese Zeile hiessen die Sicherungen unter
    # ``labels/repeat/`` ``…-unbekannt.json`` (siehe
    # ``urteilslauf.sicherungsnamen``) - zwei solche Dateien liegen als Beleg
    # im Bestand.
    payload["modell"] = "+".join(modelle[str(nummer)] for nummer, _ in geordnet)
    payload["verworfene_verweise"] = verworfene_verweise
    payload["zusammengefuehrt_am"] = _jetzt()
    return payload


def _modellname(satz: dict[str, object]) -> str:
    """Das Modell eines Laufs - ``unbekannt``, wenn das Feld fehlt oder leer ist."""
    wert = satz.get("modell")
    if isinstance(wert, str) and wert.strip():
        return wert.strip()
    return "unbekannt"


def _kandidatenliste(satz: dict[str, object]) -> list[dict[str, object]]:
    liste = satz.get("kandidaten")
    if not isinstance(liste, list):
        raise CandidatesSchemaError("Zerlegungslauf ohne Liste unter 'kandidaten'")
    return [eintrag for eintrag in liste if isinstance(eintrag, dict)]


_BUCHFUEHRUNGSFELDER = frozenset({"aus_lauf", "aus_laeufen", "laengere_fassung_von"})


def _inhalt(kandidat: dict[str, object]) -> dict[str, object]:
    """Der Kandidat ohne die Felder, die nur die Zusammenfuehrung selbst fuehrt.

    ``aus_lauf``, ``aus_laeufen`` und ``laengere_fassung_von`` sagen, WOHER
    ein Eintrag kommt, nicht WAS er meint. Sie duerfen sich aendern, ohne
    dass ein Urteil darauf falsch wird - alles andere nicht.
    """
    return {
        schluessel: wert
        for schluessel, wert in kandidat.items()
        if schluessel not in _BUCHFUEHRUNGSFELDER
    }


def veraenderte_indizes(
    alt: list[dict[str, object]], neu: list[dict[str, object]]
) -> list[int]:
    r"""Melde jeden Index, den ``neu`` gegenueber ``alt`` inhaltlich anfasst.

    Gefragt ist nicht "sind die beiden Saetze gleich", sondern die engere
    Frage, an der ein Urteil haengt: bedeutet jeder Index, den es schon
    gab, danach noch dasselbe? Ein Index, der in ``neu`` dazukommt, ist
    unbedenklich - auf ihn zeigt kein Urteil. Ein Index, der in ``neu``
    FEHLT, ist es nicht: das Urteil darauf zeigt danach ins Leere.

    Verglichen wird der Inhalt ohne Buchfuehrungsfelder
    (:func:`_inhalt`); die Reihenfolge in der Liste spielt keine Rolle.
    """
    alt_nach_index = _nach_index(alt)
    neu_nach_index = _nach_index(neu)
    veraendert: list[int] = []
    for index in sorted(alt_nach_index):
        gegenstueck = neu_nach_index.get(index)
        if gegenstueck is None or _inhalt(gegenstueck) != _inhalt(alt_nach_index[index]):
            veraendert.append(index)
    return veraendert


def _nach_index(kandidaten: list[dict[str, object]]) -> dict[int, dict[str, object]]:
    """Die Kandidaten nach ``index``; Eintraege ohne ganzzahligen Index fallen weg.

    Sie fallen weg statt zu werfen: hier wird verglichen, nicht geprueft -
    das Schema durchzusetzen ist Sache von ``candidates.parse_candidates``.
    """
    nach_index: dict[int, dict[str, object]] = {}
    for kandidat in kandidaten:
        index = kandidat.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            nach_index[index] = kandidat
    return nach_index


def juengste_urteilsdatei(job_dir: Path) -> Path | None:
    """Die nach Aenderungszeit juengste ``urteile*.json`` im Auftragsordner, falls vorhanden.

    Dasselbe Suchmuster und Auswahlkriterium wie
    :func:`matrix_auto_cutter.shorts.judge_server._existing_urteile_files` /
    ``start_session_urteile`` (``job_dir.glob("urteile*.json")``, Auswahl nach
    ``st_mtime_ns``) - nur lesend, ohne eine neue Sitzungsdatei anzulegen.
    """
    kandidaten = [path for path in job_dir.glob("urteile*.json") if path.is_file()]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda path: path.stat().st_mtime_ns)


def waehle_kandidaten(
    kandidaten: list[Candidate], urteile: dict[int, Urteil]
) -> tuple[list[Candidate], list[Candidate], list[Candidate]]:
    """Teile die Kandidaten nach Urteil: (angenommen, abgelehnt, ohne_urteil).

    Angenommen heisst ausschliesslich ``urteil == "ja"``. Ein Kandidat ohne
    Eintrag in ``urteile`` (oder mit einem Eintrag ohne gesetztes ``urteil``)
    zaehlt als ohne Urteil - ``"nein"`` und ``"spaeter"`` zaehlen als
    abgelehnt, keines der beiden als Annahme.
    """
    angenommen: list[Candidate] = []
    abgelehnt: list[Candidate] = []
    ohne_urteil: list[Candidate] = []
    for candidate in kandidaten:
        urteil = urteile.get(candidate.index)
        if urteil is None or urteil.urteil is None:
            ohne_urteil.append(candidate)
        elif urteil.urteil == "ja":
            angenommen.append(candidate)
        else:
            abgelehnt.append(candidate)
    return angenommen, abgelehnt, ohne_urteil


def pruefe_uebereinstimmung(
    kandidaten: list[Candidate], urteile: dict[int, Urteil]
) -> list[str]:
    """Melde jede Abweichung zwischen einem Urteil und dem Kandidaten, den es meint.

    Je Urteil: existiert der Index ueberhaupt unter den Kandidaten, und
    stimmen ``start_ms``, ``end_ms`` und ``titel`` ueberein? Das ist die
    wichtigste Pruefung des Moduls - ein Urteil, das (z. B. nach einer neu
    erzeugten ``kandidaten.json``) auf einen anderen Kandidaten zeigt als
    gemeint, darf nicht stillschweigend uebernommen werden.
    """
    by_index = {candidate.index: candidate for candidate in kandidaten}
    meldungen: list[str] = []
    for index in sorted(urteile):
        urteil = urteile[index]
        candidate = by_index.get(index)
        if candidate is None:
            meldungen.append(f"Urteil {index}: kein Kandidat mit diesem Index vorhanden")
            continue
        if urteil.start_ms != candidate.start_ms:
            meldungen.append(
                f"Kandidat {index}: 'start_ms' weicht ab - Urteil {urteil.start_ms}, "
                f"Kandidat {candidate.start_ms}"
            )
        if urteil.end_ms != candidate.end_ms:
            meldungen.append(
                f"Kandidat {index}: 'end_ms' weicht ab - Urteil {urteil.end_ms}, "
                f"Kandidat {candidate.end_ms}"
            )
        if urteil.titel != candidate.titel:
            meldungen.append(
                f"Kandidat {index}: 'titel' weicht ab - Urteil {urteil.titel!r}, "
                f"Kandidat {candidate.titel!r}"
            )
    return meldungen


def lies_buendel(pfad: Path) -> list[dict[str, object]]:
    """Lies ``buendel.json`` roh und gib die Liste unter ``buendel`` zurueck.

    Roh und nicht ueber ein Schema-Objekt: die Buendelung wird von einem
    Modell geschrieben, und was daran nicht stimmt, soll
    :func:`pruefe_buendel` als LISTE von Meldungen sagen und nicht als
    erster Ausnahmefehler. Nur was gar keine Liste ist, gilt hier schon als
    nichts - dann meldet die Pruefung jeden Kandidaten als fehlend, und das
    ist die richtige Auskunft.
    """
    roh = json.loads(pfad.read_text(encoding="utf-8"))
    liste = roh.get("buendel") if isinstance(roh, dict) else roh
    if not isinstance(liste, list):
        return []
    return [eintrag for eintrag in liste if isinstance(eintrag, dict)]


def _ganzzahl(wert: object) -> int | None:
    """Der Wert als Ganzzahl - oder ``None``, wenn er keine ist (``True`` ist keine)."""
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def _pruefe_vorauswahl(
    gruppen: dict[int, list[tuple[int, dict[str, object]]]],
) -> list[str]:
    """Melde jede Unstimmigkeit an ``gruppen_rang`` und ``vorauswahl``.

    Beide Felder sind FREIWILLIG: eine ``buendel.json`` aus der Zeit vor der
    Vorauswahl traegt sie gar nicht, und das ist keine Abweichung - dann
    zeigt die Urteilsseite wie bisher alle Gruppen in Nummernfolge. Erst
    wenn EINE Gruppe sie traegt, muessen alle sie tragen: eine halbe
    Vorauswahl waere schlimmer als keine, weil die Seite dann eine
    Reihenfolge zeigte, die nur fuer einen Teil des Bestandes gilt.

    Geprueft wird gegen ``gruppen``, die schon nach ``gruppe`` gebuendelte
    Sicht aus :func:`pruefe_buendel` - so bleibt die Frage "tragen alle
    Kandidaten einer Gruppe denselben Wert" ohne zweiten Durchlauf zu haben.
    """
    if not gruppen:
        return []
    meldungen: list[str] = []

    mit_feld = [
        gruppe
        for gruppe, eintraege in gruppen.items()
        if any("gruppen_rang" in eintrag for _, eintrag in eintraege)
    ]
    if not mit_feld:
        return []
    ohne_feld = sorted(set(gruppen) - set(mit_feld))
    if ohne_feld:
        namen = ", ".join(str(gruppe) for gruppe in ohne_feld)
        meldungen.append(
            f"Gruppe {namen}: 'gruppen_rang' fehlt, andere Gruppen tragen ihn - "
            f"entweder alle oder keine"
        )

    # Je Gruppe genau ein Wert. Uneinheitliche Gruppen fallen fuer die
    # Eindeutigkeits- und Lueckenpruefung heraus: ihr "der" Rang ist nicht
    # bestimmt, und ein geratener Wert erzeugte Folgemeldungen, die vom
    # eigentlichen Befund ablenken.
    rang_je_gruppe: dict[int, int] = {}
    for gruppe in sorted(gruppen):
        eintraege = gruppen[gruppe]
        raenge = [_ganzzahl(eintrag.get("gruppen_rang")) for _, eintrag in eintraege]
        if any(rang is None for rang in raenge):
            ohne = [
                index
                for (index, _), rang in zip(eintraege, raenge, strict=True)
                if rang is None
            ]
            namen = ", ".join(str(index) for index in ohne)
            meldungen.append(f"Gruppe {gruppe}: kein ganzzahliger 'gruppen_rang' bei {namen}")
            continue
        verschieden = sorted({rang for rang in raenge if rang is not None})
        if len(verschieden) != 1:
            namen = ", ".join(str(rang) for rang in verschieden)
            meldungen.append(
                f"Gruppe {gruppe}: 'gruppen_rang' uneinheitlich - {namen}; "
                f"alle Kandidaten einer Gruppe tragen denselben Wert"
            )
            continue
        rang_je_gruppe[gruppe] = verschieden[0]

    vergeben = list(rang_je_gruppe.values())
    doppelt = sorted({rang for rang in vergeben if vergeben.count(rang) > 1})
    if doppelt:
        namen = ", ".join(str(rang) for rang in doppelt)
        meldungen.append(f"'gruppen_rang' {namen} doppelt vergeben")
    fehlend = sorted(set(range(1, len(gruppen) + 1)) - set(vergeben))
    if fehlend and not meldungen:
        namen = ", ".join(str(rang) for rang in fehlend)
        meldungen.append(
            f"'gruppen_rang' {namen} fehlt - erwartet 1 bis {len(gruppen)}, lueckenlos"
        )

    meldungen.extend(_pruefe_vorauswahl_grenze(gruppen, rang_je_gruppe))
    return meldungen


def _pruefe_vorauswahl_grenze(
    gruppen: dict[int, list[tuple[int, dict[str, object]]]],
    rang_je_gruppe: dict[int, int],
) -> list[str]:
    """Melde eine Vorauswahl falscher Groesse oder mit einer schlechter gerangten Gruppe.

    Der zweite Befund ist der wichtigere: eine Vorauswahl, die eine Gruppe
    mit ``gruppen_rang`` 30 enthaelt und eine mit 4 auslaesst, ist kein
    Zaehlfehler, sondern widerspricht sich selbst - dann sagt die Datei zwei
    verschiedene Dinge darueber, was die besten Gruppen sind.
    """
    meldungen: list[str] = []
    vorausgewaehlt: set[int] = set()
    for gruppe in sorted(gruppen):
        eintraege = gruppen[gruppe]
        werte = {eintrag.get("vorauswahl") is True for _, eintrag in eintraege}
        if len(werte) != 1:
            meldungen.append(
                f"Gruppe {gruppe}: 'vorauswahl' uneinheitlich - alle Kandidaten einer "
                f"Gruppe tragen denselben Wert"
            )
            continue
        if werte.pop():
            vorausgewaehlt.add(gruppe)

    erwartet = min(VORAUSWAHL_GROESSE, len(gruppen))
    if len(vorausgewaehlt) != erwartet:
        meldungen.append(
            f"{len(vorausgewaehlt)} Gruppen mit 'vorauswahl' statt {erwartet} - "
            f"erwartet min({VORAUSWAHL_GROESSE}, {len(gruppen)})"
        )

    uebrige = set(gruppen) - vorausgewaehlt
    if vorausgewaehlt and uebrige and rang_je_gruppe:
        schlechtester = max(
            (rang_je_gruppe[gruppe] for gruppe in vorausgewaehlt if gruppe in rang_je_gruppe),
            default=None,
        )
        bester_uebriger = min(
            (rang_je_gruppe[gruppe] for gruppe in uebrige if gruppe in rang_je_gruppe),
            default=None,
        )
        if (
            schlechtester is not None
            and bester_uebriger is not None
            and schlechtester > bester_uebriger
        ):
            drinnen = sorted(
                gruppe
                for gruppe in vorausgewaehlt
                if rang_je_gruppe.get(gruppe, 0) > bester_uebriger
            )
            namen = ", ".join(str(gruppe) for gruppe in drinnen)
            meldungen.append(
                f"Gruppe {namen} steht in der Vorauswahl, hat aber einen groesseren "
                f"'gruppen_rang' als die nicht vorausgewaehlte Gruppe mit "
                f"'gruppen_rang' {bester_uebriger}"
            )
    return meldungen


def pruefe_buendel(
    kandidaten: list[dict[str, object]], buendel: list[dict[str, object]]
) -> list[str]:
    """Melde jede Abweichung zwischen ``kandidaten.json`` und ``buendel.json``.

    Dasselbe Muster wie :func:`pruefe_uebereinstimmung`: eine Liste
    deutscher Meldungen, leer heisst in Ordnung. Geprueft wird gegen die
    Kandidatendatei als Leitliste - sie wird von der Buendelung nicht
    angefasst, an ihren Indizes haengen die Urteile des Nutzers, und eine
    Buendelung, die einen Index erfindet oder auslaesst, waere fuer die
    Auswahl unbrauchbar.

    Sechs Befunde, in dieser Reihenfolge: fehlende Indizes, ueberzaehlige
    Indizes, Gruppen ohne genau eine Empfehlung, Raenge die doppelt
    vorkommen oder fehlen, eine unstimmige Vorauswahl
    (:func:`_pruefe_vorauswahl`), und Paare mit ``laengere_fassung_von``, die
    auseinandergerissen wurden. Der letzte Befund ist der wichtigste - genau
    solche Paare zeigen dasselbe Material, und sie zu trennen hiesse den
    Nutzer zweimal ueber dieselbe Sache entscheiden zu lassen.

    Beide Eingaben sind ROHE Wortlisten und keine :class:`Candidate`-Objekte:
    ``laengere_fassung_von`` ist ein Buchfuehrungsfeld und wird von
    ``parse_candidates`` weggeschnitten (siehe :data:`_BUCHFUEHRUNGSFELDER`),
    stuende hier also gar nicht mehr zur Verfuegung.
    """
    meldungen: list[str] = []

    kandidaten_index = _nach_index(kandidaten)

    buendel_index: dict[int, dict[str, object]] = {}
    for position, eintrag in enumerate(buendel):
        index = _ganzzahl(eintrag.get("index"))
        if index is None:
            meldungen.append(f"Buendeleintrag {position}: 'index' fehlt oder ist keine Ganzzahl")
            continue
        if index in buendel_index:
            meldungen.append(f"Buendeleintrag {index}: Index doppelt vergeben")
            continue
        buendel_index[index] = eintrag

    for index in sorted(set(kandidaten_index) - set(buendel_index)):
        meldungen.append(f"Kandidat {index}: fehlt in {BUENDEL_FILE_NAME}")
    for index in sorted(set(buendel_index) - set(kandidaten_index)):
        meldungen.append(f"Buendeleintrag {index}: kein Kandidat mit diesem Index vorhanden")

    gruppen: dict[int, list[tuple[int, dict[str, object]]]] = {}
    for index in sorted(buendel_index):
        eintrag = buendel_index[index]
        gruppe = _ganzzahl(eintrag.get("gruppe"))
        if gruppe is None:
            meldungen.append(f"Buendeleintrag {index}: 'gruppe' fehlt oder ist keine Ganzzahl")
            continue
        gruppen.setdefault(gruppe, []).append((index, eintrag))

    for gruppe in sorted(gruppen):
        eintraege = gruppen[gruppe]
        empfohlen = [index for index, eintrag in eintraege if eintrag.get("empfohlen") is True]
        if len(empfohlen) != 1:
            wer = ", ".join(str(index) for index in empfohlen) if empfohlen else "keiner"
            meldungen.append(
                f"Gruppe {gruppe}: {len(empfohlen)} Empfehlungen statt genau einer - {wer}"
            )
        raenge = [_ganzzahl(eintrag.get("rang")) for _, eintrag in eintraege]
        gueltig = [rang for rang in raenge if rang is not None]
        if len(gueltig) != len(raenge):
            ohne = [
                index
                for (index, _), rang in zip(eintraege, raenge, strict=True)
                if rang is None
            ]
            namen = ", ".join(str(index) for index in ohne)
            meldungen.append(f"Gruppe {gruppe}: kein ganzzahliger 'rang' bei {namen}")
        erwartet = set(range(1, len(eintraege) + 1))
        doppelt = sorted({rang for rang in gueltig if gueltig.count(rang) > 1})
        if doppelt:
            namen = ", ".join(str(rang) for rang in doppelt)
            meldungen.append(f"Gruppe {gruppe}: Rang {namen} doppelt vergeben")
        fehlend = sorted(erwartet - set(gueltig))
        if fehlend:
            namen = ", ".join(str(rang) for rang in fehlend)
            meldungen.append(
                f"Gruppe {gruppe}: Rang {namen} fehlt - erwartet 1 bis {len(eintraege)}"
            )

    meldungen.extend(_pruefe_vorauswahl(gruppen))

    for index in sorted(kandidaten_index):
        ziel = _ganzzahl(kandidaten_index[index].get("laengere_fassung_von"))
        if ziel is None:
            continue
        hier = buendel_index.get(index)
        dort = buendel_index.get(ziel)
        if hier is None or dort is None:
            continue
        if hier.get("gruppe") != dort.get("gruppe"):
            meldungen.append(
                f"Kandidat {index} ist die laengere Fassung von {ziel}, steht aber in Gruppe "
                f"{hier.get('gruppe')} statt in Gruppe {dort.get('gruppe')}"
            )

    return meldungen


def _candidate_payload(candidate: Candidate) -> dict[str, object]:
    return {
        "index": candidate.index,
        "start_ms": candidate.start_ms,
        "end_ms": candidate.end_ms,
        "titel": candidate.titel,
        "begruendung": candidate.begruendung,
        "sicherheit": candidate.sicherheit,
        "enthaelt": list(candidate.enthaelt),
    }


def baue_bauliste_payload(
    *,
    angenommen: list[Candidate],
    abgelehnt_anzahl: int,
    ohne_urteil_anzahl: int,
    stammt_aus: str,
    urteile_aus: str,
) -> dict[str, object]:
    """Baue den Inhalt von ``bauliste.json`` - dasselbe Schema wie ``kandidaten.json``.

    Die urspruenglichen ``index``-Werte der angenommenen Kandidaten bleiben
    unveraendert erhalten (keine Neunummerierung), damit ``build.py`` daraus
    dieselben ``kandidat-{index:02d}``-Ordner bildet wie ein direkter Bau
    ueber ``kandidaten.json``.
    """
    return {
        "kandidaten": [_candidate_payload(candidate) for candidate in angenommen],
        "stammt_aus": stammt_aus,
        "urteile_aus": urteile_aus,
        "angenommen": len(angenommen),
        "abgelehnt": abgelehnt_anzahl,
        "ohne_urteil": ohne_urteil_anzahl,
    }


def _write_json_atomically(path: Path, payload: dict[str, object]) -> None:
    """Atomares Schreiben nach dem Muster von ``transcript.write_transcript``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.tmp.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, path, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def schreibe_kandidatensatz(pfad: Path, payload: dict[str, object]) -> None:
    """Schreibe einen zusammengefuehrten Kandidatensatz atomar nach ``pfad``."""
    _write_json_atomically(pfad, payload)


def schreibe_bauliste(pfad: Path, payload: dict[str, object]) -> None:
    """Schreibe ``bauliste.json`` atomar - billig, wird bei jedem Lauf ueberschrieben."""
    _write_json_atomically(pfad, payload)


def _sicherheit_paar(
    kandidaten: list[Candidate], angenommen_indizes: set[int]
) -> dict[str, dict[str, int]]:
    paare: dict[str, dict[str, int]] = {
        stufe: {"ja": 0, "nein": 0} for stufe in ("hoch", "mittel", "niedrig")
    }
    for candidate in kandidaten:
        zweig = "ja" if candidate.index in angenommen_indizes else "nein"
        paare[candidate.sicherheit][zweig] += 1
    return paare


def _polarisierend_paar(
    kandidaten: list[Candidate],
    angenommen_indizes: set[int],
    polarisierend_je_index: dict[int, bool],
) -> dict[str, int]:
    paar = {"wahr": 0, "falsch": 0}
    for candidate in kandidaten:
        if candidate.index not in angenommen_indizes:
            continue
        schluessel = "wahr" if polarisierend_je_index.get(candidate.index, False) else "falsch"
        paar[schluessel] += 1
    return paar


def lies_kandidaten_rohdaten(pfad: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Lies ``kandidaten.json`` roh - fuer Wurzelfelder und Kandidaten-Zusatzfelder.

    ``load_candidates``/``parse_candidates`` schneiden sowohl Wurzelfelder wie
    ``modell``, ``kriterien_fassung``, ``lauf``, ``video_name`` als auch
    Kandidaten-Zusatzfelder wie ``polarisierend`` weg - sie sind nicht Teil
    des Kandidaten-Kontrakts (``candidates.py``). Fuer die Trefferquote
    werden sie hier zusaetzlich roh gelesen, denselben Weg entlang wie
    ``polarisierend``. Ein Lese-/Parsefehler kann an dieser Stelle nicht mehr
    auftreten, ohne dass ``load_candidates`` vorher schon gescheitert waere -
    trotzdem still auf leere Werte zurueckfallen statt ein zweites Mal zu
    werfen, das ist nicht der Fehlerpfad dieses Werkzeugs.
    """
    try:
        roh = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        roh = None
    liste = roh.get("kandidaten") if isinstance(roh, dict) else None
    kandidaten_roh = (
        [item for item in liste if isinstance(item, dict)] if isinstance(liste, list) else []
    )
    wurzelfelder: dict[str, object] = {
        "modell": "unbekannt",
        "kriterien_fassung": "unbekannt",
        "video_name": "unbekannt",
        "lauf": None,
        "laeufe": [],
    }
    if isinstance(roh, dict):
        for feld in ("kriterien_fassung", "video_name"):
            wert = roh.get(feld)
            if isinstance(wert, str) and wert.strip():
                wurzelfelder[feld] = wert
        # ``modell`` nicht wie die uebrigen Felder: fehlt es, tritt ``modelle``
        # an seine Stelle (siehe :func:`modell_kennung`).
        wurzelfelder["modell"] = modell_kennung(roh)
        wurzelfelder["lauf"] = roh.get("lauf")
        wurzelfelder["laeufe"] = laeufe_kennung(roh)
    return kandidaten_roh, wurzelfelder


def laeufe_kennung(wurzel: dict[str, object]) -> list[object]:
    """Die Kennung eines Kandidatensatzes: ``laeufe``, ersatzweise ``[lauf]``.

    Ein zusammengefuehrter Satz traegt ``laeufe`` (``fuehre_zusammen``), ein
    einzelner Zerlegungslauf nur ``lauf``. Beides muss dieselbe Frage
    beantworten koennen - "aus welchen Laeufen stammt das hier" -, sonst galt
    der zusammengefuehrte Satz als Doppelgaenger des Laufes, dessen kleinste
    Nummer er geerbt hat. Genau daran fehlte der Trefferquote-Eintrag vom
    26. August 2026.
    """
    laeufe = wurzel.get("laeufe")
    if isinstance(laeufe, list) and laeufe:
        return list(laeufe)
    lauf = wurzel.get("lauf")
    return [] if lauf is None else [lauf]


def modell_kennung(wurzel: dict[str, object]) -> str:
    """Das Modell eines Kandidatensatzes: ``modell``, ersatzweise aus ``modelle``.

    ``fuehre_zusammen`` schreibt seit dem 27. August 2026 beides - das
    Wurzelfeld ``modell`` und die genauere Abbildung ``modelle`` (Laufnummer
    -> Modell). Aeltere zusammengefuehrte Dateien tragen nur ``modelle``, und
    fuer die hiess das Modell bis dahin ueberall ``unbekannt``: im
    Sicherungsnamen unter ``labels/repeat/`` und im Trefferquote-Eintrag.
    Hier wird es aus ``modelle`` gebildet - dieselbe Form, die
    ``fuehre_zusammen`` heute schreibt: die Werte in Laufreihenfolge mit
    ``+`` verbunden. Fehlt auch ``modelle``, bleibt es bei ``unbekannt``:
    geraten wird nicht.
    """
    wert = wurzel.get("modell")
    if isinstance(wert, str) and wert.strip():
        return wert.strip()
    modelle = wurzel.get("modelle")
    if not isinstance(modelle, dict) or not modelle:
        return "unbekannt"
    namen = [
        _modellname({"modell": modelle[schluessel]})
        for schluessel in sorted(modelle, key=_laufschluessel)
    ]
    return "+".join(namen)


def _laufschluessel(schluessel: object) -> tuple[int, str]:
    """Ordne Laufnummern numerisch, alles Uebrige danach alphabetisch.

    Die Schluessel von ``modelle`` sind Zeichenketten (``"1"``, ``"2"``, so
    schreibt es JSON). Alphabetisch sortiert stuende ``"10"`` vor ``"2"`` -
    bei zehn Laeufen waere die Reihenfolge eine andere als die, die
    ``fuehre_zusammen`` schreibt.
    """
    text = str(schluessel)
    try:
        return (int(text), "")
    except ValueError:
        return (2**31, text)


def trefferquote_eintrag(
    *,
    video_name: str,
    lauf: int | str | None,
    laeufe: list[object] | None = None,
    notiz: str = "",
    modell: str,
    kriterien_fassung: str,
    kandidaten: list[Candidate],
    angenommen: list[Candidate],
    abgelehnt: list[Candidate],
    ohne_urteil: list[Candidate],
    polarisierend_je_index: dict[int, bool] | None = None,
) -> dict[str, object]:
    """Baue einen Eintrag fuer ``trefferquote.json`` - reine Zaehlung, keine Wertung.

    Die Sicherheits- und Polarisierend-Paare zaehlen ``ja``/``wahr`` gegen
    ``angenommen`` - alles andere (abgelehnt UND ohne Urteil) gilt als
    Gegenstueck. ``polarisierend_je_index`` kommt aus der rohen
    ``kandidaten.json`` (das Feld ist in :class:`Candidate` nicht enthalten,
    siehe Modul-Docstring von ``candidates.py`` zu Zusatzfeldern) und wird
    nur fuer die tatsaechlich angenommenen Kandidaten ausgewertet.
    """
    angenommen_indizes = {candidate.index for candidate in angenommen}
    gesamt = len(kandidaten)
    quote = round(len(angenommen) / gesamt, 3) if gesamt else 0.0
    im_ziel_ja = sum(
        1
        for candidate in angenommen
        if _ZIELBEREICH_MIN_MS <= candidate.duration_ms <= _ZIELBEREICH_MAX_MS
    )
    return {
        "video_name": video_name,
        "lauf": lauf,
        # Beide Felder: ``laeufe`` ist die Kennung, ``lauf`` bleibt stehen,
        # damit die zwei Alteintraege und die neuen dasselbe Schema haben.
        "laeufe": list(laeufe) if laeufe is not None else ([] if lauf is None else [lauf]),
        "notiz": notiz,
        "modell": modell,
        "kriterien_fassung": kriterien_fassung,
        "kandidaten_gesamt": gesamt,
        "angenommen": len(angenommen),
        "abgelehnt": len(abgelehnt),
        "ohne_urteil": len(ohne_urteil),
        "quote": quote,
        "sicherheit": _sicherheit_paar(kandidaten, angenommen_indizes),
        "polarisierend": _polarisierend_paar(
            kandidaten, angenommen_indizes, polarisierend_je_index or {}
        ),
        "im_zielbereich_ja": im_ziel_ja,
        "im_zielbereich_nein": len(angenommen) - im_ziel_ja,
    }


def schreibe_trefferquote(pfad: Path, eintrag: dict[str, object]) -> None:
    """Haenge ``eintrag`` an ``trefferquote.json`` an - atomar, nie ueberschreibend.

    Existiert bereits ein Eintrag mit derselben Kombination aus
    ``video_name`` und ``laeufe``, passiert nichts - der Aufrufer (``main``)
    prueft das vorab und meldet es, damit ein Doppellauf nicht zweimal
    zaehlt. Die Kennung ist ``laeufe`` und nicht ``lauf``, siehe
    :func:`_hat_bestehenden_eintrag`.
    """
    if pfad.is_file():
        try:
            bestehend = json.loads(pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            bestehend = {}
    else:
        bestehend = {}
    eintraege = bestehend.get("eintraege") if isinstance(bestehend, dict) else None
    if not isinstance(eintraege, list):
        eintraege = []
    kennung = laeufe_kennung(eintrag)
    for vorhanden in eintraege:
        if (
            isinstance(vorhanden, dict)
            and vorhanden.get("video_name") == eintrag.get("video_name")
            and laeufe_kennung(vorhanden) == kennung
        ):
            return
    eintraege.append(eintrag)
    payload: dict[str, object] = {
        "schema_version": TREFFERQUOTE_SCHEMA_VERSION,
        "eintraege": eintraege,
    }
    _write_json_atomically(pfad, payload)


def _hat_bestehenden_eintrag(
    pfad: Path, *, video_name: str, laeufe: list[object]
) -> bool:
    """Sage, ob fuer diese Aufnahme und diese Laufkombination schon ein Eintrag liegt.

    Verglichen wird ``laeufe``, nicht ``lauf``: ein zusammengefuehrter Satz
    aus Lauf 1 und 2 traegt ``lauf`` 1 (die kleinste Nummer) und waere sonst
    nicht vom reinen Lauf 1 zu unterscheiden - er misst aber einen anderen
    Kandidatenbestand und gehoert als eigener Eintrag in die Reihe.

    Ein Alteintrag ohne ``laeufe`` gilt als ``[lauf]`` und blockiert damit
    weiterhin genau seinen eigenen Fall. Umgeschrieben wird er nicht: die
    Trefferquote ist eine Reihe von Messungen, und eine nachtraeglich
    ergaenzte Messung waere keine mehr.
    """
    if not pfad.is_file():
        return False
    try:
        bestehend = json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    eintraege = bestehend.get("eintraege") if isinstance(bestehend, dict) else None
    if not isinstance(eintraege, list):
        return False
    return any(
        isinstance(vorhanden, dict)
        and vorhanden.get("video_name") == video_name
        and laeufe_kennung(vorhanden) == laeufe
        for vorhanden in eintraege
    )


def _zusammenfuehren_cli(job_dir: Path, output: Path | None) -> int:
    """``--zusammenfuehren``: aus allen Laufdateien eine ``kandidaten.json`` machen.

    Liegen bereits Urteile UND eine ``kandidaten.json`` vor, wird nicht
    geschrieben, sondern gemeldet (Code 9). Nicht aus Vorsicht: die
    vorhandenen Urteile zeigen auf die Nummerierung der Datei, die
    dalaege - eine neu geschriebene ``kandidaten.json`` kann dieselben
    Indizes tragen und trotzdem andere Ausschnitte meinen, wenn zwischen
    den beiden Zusammenfuehrungen eine Laufdatei dazukam.
    """
    ziel = output or (job_dir / CANDIDATES_FILE_NAME)
    try:
        saetze = lade_laufdateien(job_dir)
    except (OSError, json.JSONDecodeError, CandidatesSchemaError) as exc:
        print(f"ANGEHALTEN [laufdatei_unlesbar]: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR
    if not saetze:
        print(f"ANGEHALTEN [keine_laufdatei]: kein {LAUFDATEI_GLOB} in {job_dir}")
        return _CODE_KEINE_LAUFDATEI

    urteilsdatei = juengste_urteilsdatei(job_dir)
    if ziel.is_file() and urteilsdatei is not None:
        print(
            f"ANGEHALTEN [urteile_vorhanden]: {ziel.name} liegt vor und {urteilsdatei.name} "
            f"zeigt auf dessen Nummerierung - nichts geschrieben"
        )
        return _CODE_URTEILE_VORHANDEN

    try:
        payload = fuehre_zusammen(saetze)
    except CandidatesSchemaError as exc:
        print(f"ANGEHALTEN [laufdatei_unlesbar]: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR
    schreibe_kandidatensatz(ziel, payload)
    kandidaten = payload["kandidaten"]
    assert isinstance(kandidaten, list)
    laeufe = ", ".join(str(nummer) for nummer, _ in saetze)
    print(f"Laeufe {laeufe} zusammengefuehrt: {len(kandidaten)} Kandidaten -> {ziel}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI: aus Kandidaten plus Urteilen eine Bauliste machen (und Trefferquote fortschreiben)."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shorts-Auswahl: aus kandidaten.json plus Urteilen eine bauliste.json machen"
    )
    parser.add_argument("job_path", type=Path)
    parser.add_argument("--kandidaten", type=Path, default=None)
    parser.add_argument("--urteile", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--keine-trefferquote", action="store_true")
    parser.add_argument(
        "--notiz",
        default="",
        metavar="TEXT",
        help=(
            "Freitext am Trefferquote-Eintrag, etwa um einen Lauf als nicht "
            "repraesentativ zu vermerken. Wird nur durchgereicht, nicht ausgewertet."
        ),
    )
    parser.add_argument(
        "--zusammenfuehren",
        action="store_true",
        help="alle kandidaten-lauf*.json zu kandidaten.json vereinigen und beenden",
    )
    args = parser.parse_args(argv)

    if args.zusammenfuehren:
        return _zusammenfuehren_cli(args.job_path, args.output)

    job_dir: Path = args.job_path
    kandidaten_path = args.kandidaten or (job_dir / "kandidaten.json")
    urteile_path = args.urteile
    output_path = args.output or (job_dir / BAULISTE_FILE_NAME)

    try:
        kandidaten = load_candidates(kandidaten_path)
    except CandidatesSchemaError as exc:
        print(f"ANGEHALTEN [kandidaten_unlesbar]: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR
    except OSError as exc:
        print(f"ANGEHALTEN [kandidaten_unlesbar]: {kandidaten_path} nicht lesbar: {exc}")
        return _CODE_KANDIDATEN_UNLESBAR

    if urteile_path is None:
        urteile_path = juengste_urteilsdatei(job_dir)
        if urteile_path is None:
            print(f"ANGEHALTEN [keine_urteilsdatei]: keine urteile*.json in {job_dir} gefunden")
            return _CODE_KEINE_URTEILSDATEI
    elif not urteile_path.is_file():
        print(f"ANGEHALTEN [keine_urteilsdatei]: {urteile_path} nicht gefunden")
        return _CODE_KEINE_URTEILSDATEI

    try:
        json.loads(urteile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ANGEHALTEN [urteile_kein_json]: {urteile_path} nicht als JSON lesbar: {exc}")
        return _CODE_URTEILE_KEIN_JSON
    urteile = load_urteile(urteile_path)

    abweichungen = pruefe_uebereinstimmung(kandidaten, urteile)
    if abweichungen:
        print("ANGEHALTEN [urteile_abweichung]: Urteile passen nicht zu den Kandidaten")
        for zeile in abweichungen:
            print(zeile)
        return _CODE_URTEILE_ABWEICHUNG

    angenommen, abgelehnt, ohne_urteil = waehle_kandidaten(kandidaten, urteile)
    if not angenommen:
        print("ANGEHALTEN [keine_annahmen]: null angenommene Kandidaten")
        return _CODE_KEINE_ANNAHMEN

    payload = baue_bauliste_payload(
        angenommen=angenommen,
        abgelehnt_anzahl=len(abgelehnt),
        ohne_urteil_anzahl=len(ohne_urteil),
        stammt_aus=kandidaten_path.name,
        urteile_aus=urteile_path.name,
    )
    schreibe_bauliste(output_path, payload)

    if not args.keine_trefferquote:
        kandidaten_roh, wurzelfelder = lies_kandidaten_rohdaten(kandidaten_path)
        video_name = str(wurzelfelder["video_name"])
        lauf = wurzelfelder["lauf"]
        assert isinstance(lauf, int | str) or lauf is None
        laeufe = wurzelfelder["laeufe"]
        assert isinstance(laeufe, list)
        if _hat_bestehenden_eintrag(TREFFERQUOTE_PFAD, video_name=video_name, laeufe=laeufe):
            print(
                f"Trefferquote-Eintrag fuer video_name={video_name!r} laeufe={laeufe!r} "
                f"existiert bereits in {TREFFERQUOTE_PFAD} - nichts angehaengt"
            )
        else:
            polarisierend_je_index: dict[int, bool] = {}
            for roh_eintrag in kandidaten_roh:
                roh_index = roh_eintrag.get("index")
                if isinstance(roh_index, int):
                    polarisierend_je_index[roh_index] = bool(
                        roh_eintrag.get("polarisierend", False)
                    )
            eintrag = trefferquote_eintrag(
                video_name=video_name,
                lauf=lauf,
                laeufe=laeufe,
                notiz=str(args.notiz),
                modell=str(wurzelfelder["modell"]),
                kriterien_fassung=str(wurzelfelder["kriterien_fassung"]),
                kandidaten=kandidaten,
                angenommen=angenommen,
                abgelehnt=abgelehnt,
                ohne_urteil=ohne_urteil,
                polarisierend_je_index=polarisierend_je_index,
            )
            schreibe_trefferquote(TREFFERQUOTE_PFAD, eintrag)

    print(
        f"{len(angenommen)} von {len(kandidaten)} angenommen, {len(abgelehnt)} abgelehnt, "
        f"{len(ohne_urteil)} ohne Urteil -> {output_path}"
    )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
