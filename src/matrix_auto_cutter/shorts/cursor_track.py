r"""Stufe 3b: Mausverfolgung als Versatzkurve - reine Funktionen, kein Video.

Dieses Modul beantwortet EINE Frage: Welchen waagerechten Ausschnittversatz
soll Stufe 3a fuer jedes einzelne gerenderte Frame eines Kandidaten waehlen,
damit der Mauszeiger im Bild bleibt? Es liest dazu das Cursorprotokoll des
Waechters (``cursor-*.csv``) und die Keep-Segmente des Schnitts, und liefert
je Kandidat eine Folge von Versatzwerten.

Was dieses Modul AUSDRUECKLICH NICHT tut: ffmpeg aufrufen, Video schreiben,
eine CLI anbieten, oder sich selbst in ``chart_crop.py``/``build.py``
verdrahten. Das ist ein spaeterer Auftrag. Hier stehen nur Funktionen ohne
Nebenwirkung - bis auf :func:`lies_cursorprotokoll`, die eine Datei liest.

DIE ACHSENKETTE, in dieser Reihenfolge und nur so:

    gerendertes Frame
      -> :func:`~matrix_auto_cutter.shorts.frame_map.map_rendered_frame`
    Quellframe der Rohaufnahme
      -> :func:`quellframe_zu_wanduhrzeit`
    Wanduhrzeit
      -> :func:`gleitender_median`
    Median von x, EINMAL umgerechnet in Leinwandspalten
      -> :func:`versatzkurve`
    Ausschnittversatz

BILDSCHIRMSPALTE IST NICHT LEINWANDSPALTE. Das Cursorprotokoll traegt
Desktopkoordinaten von Bildschirm 1 (0..2559); die Leinwand zeigt aber ein
FENSTERABBILD der Quelle ``Charts Tradingview``, das OBS um
:data:`LEINWAND_SKALA_X` streckt. Beide Achsen direkt zu vergleichen war der
Fehler der ersten Fassung dieses Moduls. Umgerechnet wird an GENAU EINER
Stelle, direkt nach dem Median (:func:`gleitender_median`); ab dort rechnet
das Modul durchgehend in Leinwandspalten - Trittzone, Zielversatz,
Anfangsversatz, alles.

Der Cursor kann auf dem ZWEITEN Monitor stehen. Der liegt links (x von -2560
bis -1); ein negatives x heisst also schlicht "Cursor nicht im Bild". Er kann
auch RECHTS aus der Leinwand laufen, ohne den Bildschirm zu verlassen: OBS
schneidet die rechte Fensterspalte (die TradingView-Watchlist) weg, siehe
:data:`LEINWAND_BREITE`. Das ist ein Austritt nach rechts, nicht "nicht im
Bild" - Begruendung bei :func:`gleitender_median`.

DER VERSATZBEREICH IST HIER ENGER ALS DER IN ``chart_crop.py`` ERLAUBTE.
``chart_crop.X_OFFSET_MIN`` ist 0, dieses Modul faehrt nie unter
:data:`X_OFFSET_MIN_3B` = 482. Grund: In den Leinwandspalten 0..481 liegt die
OBS-Quelle ``AVATAR`` (PNGtuber, ``game_capture`` von veadotube-mini). Stufe
5b legt ohnehin die separat aufgenommene Avatardatei auf die Leinwand; ein
ZWEITER Avatar, der aus dem Bildschirminhalt mitgeschnitten wird, waere ein
Fehler und kein Merkmal. Jeder Kurvenwert erfuellt zusaetzlich den Kontrakt
von ``chart_crop._validate_offset``: Ganzzahl, gerade, in [0, 832].

Der RUECKFALL bleibt dagegen bei ``chart_crop.X_OFFSET_DEFAULT`` = 416 - das
ist das heutige, an 33 gebauten Shorts abgenommene Bild und betrifft 20 von
27 Aufnahmen. 482 gilt nur dort, wo tatsaechlich eine Kurve gerechnet wird.

RUECKFAELLE SIND DER NORMALFALL, nicht die Ausnahme: nur 7 von 27 gerenderten
Aufnahmen haben ueberhaupt ein Cursorprotokoll. Fehlt es, deckt es die Spanne
nicht ab, oder gibt es in der ganzen Spanne keinen gueltigen Median, liefert
:func:`versatzkurve` eine konstante Kurve auf
``chart_crop.X_OFFSET_DEFAULT`` und nennt den Grund benannt im Rueckgabewert.
Es gibt auf diesem Weg keine Ausnahme und keinen Abbruch. Fail-closed ist
allein das LESEN des Protokolls (:func:`parse_cursorprotokoll`, wie
``avatar_lag.py``): eine kaputte Zeile ist ein Fehler, kein stilles
Ueberspringen.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from matrix_auto_cutter.shorts.chart_crop import (
    CROP_WIDTH,
    SOURCE_FPS,
    SOURCE_HEIGHT,
    SOURCE_WIDTH,
    X_OFFSET_DEFAULT,
    X_OFFSET_MAX,
)
from matrix_auto_cutter.shorts.frame_map import KeepSegment, map_rendered_frame, ms_to_frame

# ---------------------------------------------------------------------------
# Teil 1 - der ORTSBEZUG: Desktopspalte -> Leinwandspalte.
#
# ACHTUNG, DIESE ZAHLEN HAENGEN AN DER OBS-SZENE. Sie stammen woertlich aus
# der Szenendatei %APPDATA%\obs-studio\basic\scenes\Experimental.json, Szene
# ``Charts``, Szenenelement ``Charts Tradingview`` (``window_capture`` auf
# ``TradingView.exe``):
#
#     "pos":   {"x": 0.0,                "y": -81.0}
#     "scale": {"x": 1.107031226158142,  "y": 1.1070921421051025}
#     "align": 5   (links|oben)   "bounds_type": 0   (keine Bounds)
#     crop_left/top/right/bottom je 0, keine Filter an der Quelle
#
# AENDERT SICH POSITION ODER SKALIERUNG DIESER QUELLE, sind die Konstanten
# hier neu zu bestimmen. Ohne sie rechnet dieses Modul am falschen Ort.
#
# Belegt wurde die Abbildung gegen die 40 Stichproben des Auftrags
# ``shorts-anker-kalibrierung`` (Auftrag ``shorts-3b-verdrahtung``, N1b): der
# Median-Restfehler zwischen erwarteter und im Bild gemessener
# Fadenkreuzspalte faellt von 169,5 px auf 8,9 px (Aufnahme 19.8.) und von
# 121,7 px auf 9,2 px (Aufnahme 21.8.). Eine ausreisserfeste Ausgleichsrechnung
# ueber dieselben Stichproben schaetzt die Skala unabhaengig auf 1,1173 bzw.
# 1,0957 - beide innerhalb von 1,1 % des Szenenwerts.
# ---------------------------------------------------------------------------

FENSTER_LINKS_DESKTOP = 0
"""Desktopspalte der linken Kante des aufgenommenen TradingView-Fensters.

Null, weil das Fenster am linken Rand von Bildschirm 1 steht. Belegt durch
N1b: mit ``FENSTER_LINKS_DESKTOP = 0`` und dem Achsenabschnitt 0 unten passen
die gemessenen Fadenkreuzspalten auf 9 px genau; die freie Ausgleichsrechnung
findet Achsenabschnitte von -14,7 px und +17,3 px, also null im Rahmen der
Messgenauigkeit.
"""

LEINWAND_SKALA_X = 1.107031226158142
"""``scale.x`` des Szenenelements ``Charts Tradingview``."""

LEINWAND_VERSATZ_X = 0.0
"""``pos.x`` des Szenenelements ``Charts Tradingview``."""

LEINWAND_SKALA_Y = 1.1070921421051025
"""``scale.y`` des Szenenelements ``Charts Tradingview``."""

LEINWAND_VERSATZ_Y = -81.0
"""``pos.y`` des Szenenelements ``Charts Tradingview`` - das Fenster sitzt hoeher
als die Leinwand, die obersten 81 Leinwandzeilen zeigen die Browser-Leiste nicht."""

LEINWAND_BREITE = SOURCE_WIDTH
LEINWAND_HOEHE = SOURCE_HEIGHT


def desktop_zu_leinwand_x(desktop_x: int) -> int:
    """Rechne eine Desktopspalte in eine Leinwandspalte um - abgerundet, ganzzahlig.

    NICHT geklemmt: ein Ergebnis ausserhalb ``[0, LEINWAND_BREITE - 1]`` ist
    eine Aussage und kein Fehler. Rechts heisst es "der Zeiger steht auf
    Bildschirm 1, aber in dem Streifen, den OBS wegschneidet" (die
    TradingView-Watchlist); genau diesen Zustand braucht
    :func:`austrittsrichtung`, um ihn als Austritt nach RECHTS zu behandeln.
    Wuerde hier geklemmt, saehe er wie "Zeiger am rechten Rand" aus und der
    Ausschnitt bliebe stehen.

    Abgerundet wird mit :func:`math.floor`, nicht mit ``int`` - ``int``
    schnitte bei negativen Werten zur Null hin ab, und -0,4 wuerde dann zur
    gueltigen Spalte 0 statt zur ungueltigen -1.

    Die rechte Grenze: Desktopspalte 2312 bildet auf Leinwandspalte 2559 ab,
    die letzte der Leinwand; ab Desktopspalte 2313 (Leinwandspalte 2560) ist
    der Zeiger in dem Streifen, den OBS wegschneidet.
    """
    return math.floor(
        (desktop_x - FENSTER_LINKS_DESKTOP) * LEINWAND_SKALA_X + LEINWAND_VERSATZ_X
    )


def desktop_zu_leinwand_y(desktop_y: int) -> int:
    """Rechne eine Desktopzeile in eine Leinwandzeile um - abgerundet, ganzzahlig.

    Die Grenzen: Desktopzeile 73 bildet auf Leinwandzeile -1 ab (noch die
    Browser-Leiste), 74 auf 0; Desktopzeile 1373 auf 1439, die letzte der
    Leinwand, 1374 auf 1440 (unterhalb der Leinwand, in der Praxis die
    Taskleiste). Zum Abrunden siehe :func:`desktop_zu_leinwand_x`.
    """
    return math.floor(desktop_y * LEINWAND_SKALA_Y + LEINWAND_VERSATZ_Y)



# ---------------------------------------------------------------------------
# Teil 2 - die Zeitbruecke.
# ---------------------------------------------------------------------------

AUFNAHMESTART_VERSATZ_MS = 0
"""Korrektur zwischen dem uebergebenen Anker und der Wanduhrzeit des Quellframes 0.

HIER STEHT ABSICHTLICH NOCH KEIN GEMESSENER WERT. Die 0 ist eine
Platzhalterstellung, kein Messergebnis - jede Zahl, die dieses Modul heute
mit ihr rechnet, ist vorlaeufig. Der richtige Wert wird im Auftrag
``shorts-anker-kalibrierung`` gemessen und danach hier eingetragen.

Bekannt ist bisher nur die GROESSENORDNUNG der Unsicherheit: 170 bis 980 ms,
und sie faellt von Aufnahme zu Aufnahme verschieden aus. Genau deshalb darf
dieser Wert NICHT geraten werden - eine Groesse, die zwischen zwei Aufnahmen
um 800 ms wandert, ist keine Konstante, die man aus dem Gefuehl setzt. Bei
60 fps sind 980 ms fast 59 Frames; ein geratener Wert verschoebe die ganze
Versatzkurve gegen das Bild, und zwar unbemerkt.
"""


def quellframe_zu_wanduhrzeit(
    quellframe: int,
    anker: datetime,
    *,
    fps: int = SOURCE_FPS,
) -> datetime:
    """Rechne einen Quellframe der Rohaufnahme in Wanduhrzeit um.

    Der Anker ist die Wanduhrzeit des Quellframes 0, also der Beginn der
    Rohaufnahme - in der Praxis ``recording_started_at`` oder
    ``csv_first_row_at`` aus der Waechter-Seitendatei ``cursor-*.json``.
    Diese Funktion NIMMT ihn entgegen, sie sucht ihn nicht selbst und sie
    kennt die Seitendatei nicht: Welcher der beiden Zeitstempel der richtige
    Anker ist, ist eine Frage der Kalibrierung und nicht der Arithmetik.

    Auf den Anker kommt :data:`AUFNAHMESTART_VERSATZ_MS`, die noch nicht
    gemessene Korrektur. Gerechnet wird in Mikrosekunden und ganzzahlig, damit
    sich ueber die knapp 50000 Frames einer Aufnahme kein Gleitkommafehler
    aufsummiert.
    """
    if quellframe < 0:
        raise ValueError("quellframe darf nicht negativ sein")
    if fps <= 0:
        raise ValueError("fps muss positiv sein")
    if anker.tzinfo is None:
        raise ValueError("anker muss einen Zeitzonen-Offset tragen")
    versatz = timedelta(microseconds=quellframe * 1_000_000 // fps)
    return anker + versatz + timedelta(milliseconds=AUFNAHMESTART_VERSATZ_MS)


# ---------------------------------------------------------------------------
# Teil 3, Punkt 1 - das Cursorprotokoll lesen. Fail-closed wie avatar_lag.py.
# ---------------------------------------------------------------------------

CURSOR_KOPFZEILE = "zeit,x,y"
"""Die einzige Kopfzeile, die dieses Modul akzeptiert."""


class CursorProtokollError(Exception):
    """Das Cursorprotokoll verletzt seinen Kontrakt - kein stilles Ueberspringen."""


@dataclass(frozen=True, slots=True)
class CursorZeile:
    """Eine Zeile des Cursorprotokolls: Wanduhrzeit und Bildschirmkoordinaten."""

    zeit: datetime
    x: int
    y: int


def _ganzzahl(text: str, *, feld: str, quelle: str, zeilennummer: int) -> int:
    """Lies ein Feld streng als Ganzzahl - kein Gleitkomma, kein leeres Feld."""
    roh = text.strip()
    try:
        return int(roh)
    except ValueError as exc:
        raise CursorProtokollError(
            f"{quelle}, Zeile {zeilennummer}: {feld} ist keine Ganzzahl, sondern {text!r}"
        ) from exc


def parse_cursorprotokoll(roher_text: str, *, quelle: str = "<text>") -> tuple[CursorZeile, ...]:
    """Lies ein Cursorprotokoll aus Text - streng, mit benannter Fehlerstelle.

    Geprueft wird: die Kopfzeile ist woertlich :data:`CURSOR_KOPFZEILE`; jede
    Datenzeile hat genau drei Felder; der Zeitstempel ist ISO-8601 UND traegt
    einen Zeitzonen-Offset (ohne ihn liesse sich die Zeile keiner Wanduhrzeit
    zuordnen); ``x`` und ``y`` sind Ganzzahlen; die Zeilen sind nach Zeit
    nicht absteigend sortiert.

    Jeder Verstoss wirft :class:`CursorProtokollError` mit Zeilennummer -
    fail-closed wie ``avatar_lag.py``. Eine kaputte Zeile ist ein Fehler und
    wird NICHT still uebersprungen: ein Protokoll, das an einer Stelle
    unlesbar ist, ist an dieser Stelle auch nicht auswertbar, und ein
    stilles Loch im Protokoll saehe spaeter genau wie "Cursor nicht im Bild"
    aus - zwei sehr verschiedene Dinge.

    Ein Protokoll mit Kopfzeile, aber ohne Datenzeile ist zulaessig und
    liefert ein leeres Ergebnis; :func:`versatzkurve` behandelt das als
    Rueckfall, nicht als Fehler.
    """
    zeilen_text = roher_text.lstrip("﻿").splitlines()
    if not zeilen_text:
        raise CursorProtokollError(f"{quelle}: leere Datei, keine Kopfzeile")
    kopf = zeilen_text[0].strip()
    if kopf != CURSOR_KOPFZEILE:
        raise CursorProtokollError(
            f"{quelle}: Kopfzeile ist {kopf!r}, erwartet {CURSOR_KOPFZEILE!r}"
        )
    zeilen: list[CursorZeile] = []
    for versatz, rohzeile in enumerate(zeilen_text[1:]):
        zeilennummer = versatz + 2
        if not rohzeile.strip():
            raise CursorProtokollError(f"{quelle}, Zeile {zeilennummer}: leere Zeile")
        felder = rohzeile.split(",")
        if len(felder) != 3:
            raise CursorProtokollError(
                f"{quelle}, Zeile {zeilennummer}: {len(felder)} Felder statt 3 ({rohzeile!r})"
            )
        try:
            zeit = datetime.fromisoformat(felder[0].strip())
        except ValueError as exc:
            raise CursorProtokollError(
                f"{quelle}, Zeile {zeilennummer}: {felder[0]!r} ist kein ISO-8601-Zeitstempel"
            ) from exc
        if zeit.tzinfo is None:
            raise CursorProtokollError(
                f"{quelle}, Zeile {zeilennummer}: Zeitstempel {felder[0]!r} ohne Zeitzonen-Offset"
            )
        x = _ganzzahl(felder[1], feld="x", quelle=quelle, zeilennummer=zeilennummer)
        y = _ganzzahl(felder[2], feld="y", quelle=quelle, zeilennummer=zeilennummer)
        if zeilen and zeit < zeilen[-1].zeit:
            raise CursorProtokollError(
                f"{quelle}, Zeile {zeilennummer}: Zeitstempel {zeit.isoformat()} liegt vor "
                f"der Vorzeile ({zeilen[-1].zeit.isoformat()})"
            )
        zeilen.append(CursorZeile(zeit=zeit, x=x, y=y))
    return tuple(zeilen)


def lies_cursorprotokoll(pfad: Path) -> tuple[CursorZeile, ...]:
    """Lies ein Cursorprotokoll von der Platte - die einzige IO dieses Moduls."""
    return parse_cursorprotokoll(pfad.read_text(encoding="utf-8-sig"), quelle=str(pfad))


# ---------------------------------------------------------------------------
# Teil 3, Punkt 2 - der gleitende Median.
# ---------------------------------------------------------------------------

MEDIAN_FENSTER_MS = 375
"""Breite des zentrierten Medianfensters, in MILLISEKUNDEN - nicht in Zeilen.

Millisekunden statt Zeilenzahl, weil das Abtastintervall des Waechters real
rund 124 ms betraegt und nicht die eingestellten 100 ms (gemessen:
``sample_interval_measured_ms`` 124,21 ms fuer die Aufnahme 2026-08-19
17-26-15). Es kann ausserdem mit der Systemlast wandern. Eine feste
Zeilenzahl wanderte damit mit: dasselbe "Fenster ueber 4 Zeilen" waere je
nach Last mal 400 und mal 520 ms breit, und die Glaettung waere nicht mehr
dieselbe. Ein Fenster in Millisekunden ist von der Abtastrate unabhaengig.
"""


def zeile_ist_im_bild(zeile: CursorZeile) -> bool:
    """Wahr, wenn diese Protokollzeile ueberhaupt auf der Leinwand liegt.

    Zwei Ausschlussgruende, beide "Zeiger nicht im Bild":

    * ``x < 0`` - der zweite Monitor liegt links (x von -2560 bis -1).
    * die Leinwandzeile liegt ausserhalb ``[0, LEINWAND_HOEHE - 1]``. Oben ist
      das die Browser-Leiste des TradingView-Fensters (Desktopzeile 0..73,
      denn ``pos.y`` ist -81), unten der Streifen unterhalb der Leinwand (ab
      Desktopzeile 1374 - in der Praxis die Taskleiste).

    Ein zu grosses ``x`` ist AUSDRUECKLICH KEIN Ausschlussgrund: der Zeiger
    ist dort im Fenster, nur nicht im aufgenommenen Ausschnitt - siehe
    :func:`desktop_zu_leinwand_x`.
    """
    if zeile.x < 0:
        return False
    return 0 <= desktop_zu_leinwand_y(zeile.y) < LEINWAND_HOEHE


def gleitender_median(
    zeilen: Sequence[CursorZeile],
    zeit: datetime,
    *,
    fenster_ms: int = MEDIAN_FENSTER_MS,
    von: datetime | None = None,
    bis: datetime | None = None,
) -> int | None:
    """Zentrierter gleitender Median um ``zeit``, ALS LEINWANDSPALTE.

    Das Fenster ist ``fenster_ms`` breit und liegt mittig um ``zeit``. Mit
    ``von``/``bis`` laesst es sich beschneiden - so haelt :func:`versatzkurve`
    das Fenster innerhalb EINES Keep-Segments und filtert nie ueber eine
    Schnittnaht hinweg.

    Zeilen, die nicht im Bild liegen, gehen nicht in den Median ein - siehe
    :func:`zeile_ist_im_bild`. Ein Mittelwert aus Koordinaten beider Monitore
    waere eine Zahl ohne Bedeutung. Enthaelt das Fenster keine einzige Zeile
    im Bild, ist der Median UNDEFINIERT - das Ergebnis ist dann ``None`` und
    steht fuer den Zustand "Cursor nicht im Bild", nicht fuer "Cursor bei 0".

    HIER UND NUR HIER wird von Desktop- auf Leinwandspalten umgerechnet. Der
    Median wird auf der Desktopachse gebildet und danach EINMAL abgebildet -
    nicht je Zeile, denn eine affine Abbildung vertauscht mit der
    Medianbildung, und eine Umrechnung je Zeile haette nur mehr
    Rundungsfehler zur Folge. Alles, was diesen Wert weiterverarbeitet,
    rechnet in Leinwandspalten.

    Bei gerader Anzahl ist das Ergebnis das ganzzahlig abgerundete Mittel der
    beiden mittleren Werte; die Kurve wird ohnehin auf gerade Ganzzahlen
    gerundet, ein Halbpixel im Median waere ohne Wirkung.
    """
    if fenster_ms <= 0:
        raise ValueError("fenster_ms muss positiv sein")
    halb = timedelta(microseconds=fenster_ms * 500)
    anfang = zeit - halb
    ende = zeit + halb
    if von is not None and anfang < von:
        anfang = von
    if bis is not None and ende > bis:
        ende = bis
    if ende < anfang:
        return None
    links = bisect.bisect_left(zeilen, anfang, key=lambda zeile: zeile.zeit)
    rechts = bisect.bisect_right(zeilen, ende, key=lambda zeile: zeile.zeit)
    werte = sorted(zeile.x for zeile in zeilen[links:rechts] if zeile_ist_im_bild(zeile))
    if not werte:
        return None
    mitte = len(werte) // 2
    if len(werte) % 2 == 1:
        return desktop_zu_leinwand_x(werte[mitte])
    return desktop_zu_leinwand_x((werte[mitte - 1] + werte[mitte]) // 2)


# ---------------------------------------------------------------------------
# Teil 3, Punkt 3 - der Versatzbereich dieser Stufe. Begruendung im Moduldoc.
# ---------------------------------------------------------------------------

X_OFFSET_MIN_3B = 482
"""Linker Anschlag der Kurve - tiefer faehrt Stufe 3b nie.

Das Bild wird in diesem Bereich nur hin und her geschoben; es dockt mit
seinen Raendern an die Raender an, bleibt dort und geht nicht darueber hinaus.

WOHER DIE 482 KOMMT: Die OBS-Quelle ``AVATAR`` (PNGtuber, ``game_capture``
von veadotube-mini) steht in der Szene ``Charts`` laut Szenendatei bei

    "pos":   {"x": 481.0, "y": 1108.0}
    "scale": {"x": -0.35364583134651184, "y": 0.35370370745658875}
    "align": 5   (links|oben)   "bounds_type": 0   (keine Bounds)

Die x-Skalierung ist NEGATIV, das Bild also waagerecht gespiegelt: die Quelle
wird von Spalte 481 aus nach LINKS gezeichnet. Bei einer nativen Fensterbreite
von 1360 px (= 481 / 0,353646) belegt sie die Leinwandspalten 0 bis 481 -
Spalte 482 ist damit die erste garantiert avatarfreie Spalte, und zwar
unabhaengig davon, was der Avatar gerade zeichnet oder wie er wackelt. Ein
ZWEITER, aus dem Bildschirminhalt mitgeschnittener Avatar waere ein Fehler.

Fruehere Messwerte ("sichtbar etwa Spalte 0..300") sind Momentaufnahmen und
taugen NICHT als Grenze; die Box tut es. Gegengeprueft an den 71 vorhandenen
Einzelbildern (Auftrag ``shorts-3b-verdrahtung``, N2b): die am weitesten
rechts liegende Avatarspalte ueber alle Bilder ist 316.
"""

X_OFFSET_MAX_3B = X_OFFSET_MAX
"""Rechter Anschlag der Kurve - identisch mit dem von ``chart_crop.py`` erlaubten.

Rechts liegen Preisskala und Datumsskala von TradingView. Beide gehoeren zum
Chart und sind gewollt; in den Leinwandspalten 2144..2559 liegt sonst nichts
(N2c, geprueft an denselben 71 Bildern).
"""

assert X_OFFSET_MIN_3B == 482, "X_OFFSET_MIN_3B haengt an der Avatarbox der OBS-Szene"
assert X_OFFSET_MAX_3B == 832, "X_OFFSET_MAX_3B haengt an chart_crop.X_OFFSET_MAX"
assert X_OFFSET_MIN_3B > 481, "482 ist die erste Spalte rechts der Avatarbox"

VERSATZ_SPANNE_PX = X_OFFSET_MAX_3B - X_OFFSET_MIN_3B
"""Der volle Weg dieser Stufe: 350 px. Massstab fuer die Fahrtdauer, NICHT 832."""


# ---------------------------------------------------------------------------
# Teil 3, Punkt 4 - Zustandsfuehrung der Kurve. Alle Startwerte benannt.
# ---------------------------------------------------------------------------

TRITTZONE_RAND_PX = 300
"""Die EMPFINDLICHKEIT: wie nah der Zeiger an die Ausschnittkante kommen darf,
bevor gefahren wird.

Randstreifen des Ausschnitts an BEIDEN Seiten; solange der Median tiefer drin
liegt, haelt das Bild. 300 von :data:`chart_crop.CROP_WIDTH` = 1728 sind 17 %,
und der Nutzer hat 10 bis 20 % verlangt.

STELLWERT DES NUTZERS, keine gemessene Groesse. Wer ihn aendert, aendert, wie
frueh sich der Ausschnitt in Bewegung setzt - nicht, wie genau gemessen wird.
Der Ausloeser bleibt eine SCHWELLE: kein stufenloses Nachfuehren im
Randstreifen, kein Zucken bei kleinen Bewegungen.

Zusammen mit :data:`RESERVE_PX` gilt die Invariante
``2 * TRITTZONE_RAND_PX + RESERVE_PX <= CROP_WIDTH - 1`` - siehe dort.
"""

AUSTRITT_VERZOEGERUNG_MS = 300
"""So lange muss ein Austritt anhalten, bevor gefahren wird - gegen kurze Wischer."""

MINDESTVERWEILDAUER_MS = 1000
"""So lange nach dem Ende einer Fahrt beginnt keine neue."""

RESERVE_PX = 250
"""Der VORLAUF: wie weit der Ausschnitt ueber den Zeiger hinaus in dessen
Bewegungsrichtung schiebt.

Zusatzabstand zur Austrittskante nach der Fahrt - das Ziel ist NICHT die Mitte.
Zusammen mit :data:`TRITTZONE_RAND_PX` steht der Zeiger nach einer Fahrt
300 + 250 = 550 px von der Austrittskante entfernt (bisher 250).

STELLWERT DES NUTZERS, keine gemessene Groesse.

DIE INVARIANTE, die beide Werte aneinander bindet: Nach einer Fahrt muss der
Zeiger vom GEGENUEBERLIEGENDEN Rand weiter entfernt sein als
:data:`TRITTZONE_RAND_PX` - sonst loeste jede Fahrt sofort die Gegenfahrt aus
und das Bild flatterte. Der Zeiger steht nach der Fahrt
``TRITTZONE_RAND_PX + RESERVE_PX`` von der Austrittskante entfernt, also
``CROP_WIDTH - 1 - (TRITTZONE_RAND_PX + RESERVE_PX)`` vom gegenueberliegenden
Rand. Verlangt ist damit

    2 * TRITTZONE_RAND_PX + RESERVE_PX <= CROP_WIDTH - 1

Heute: 2 * 300 + 250 = 850 <= 1727. Der Zeiger steht nach einer Fahrt 1177 px
vom gegenueberliegenden Rand entfernt, also 877 px innerhalb dessen
Trittzonengrenze. Die ``assert``-Zeile unten haelt das fest.
"""

assert 2 * TRITTZONE_RAND_PX + RESERVE_PX <= CROP_WIDTH - 1, (
    "Invariante verletzt: eine Fahrt wuerde sofort die Gegenfahrt ausloesen - siehe RESERVE_PX"
)

FAHRT_MIN_MS = 250
"""Dauer der kuerzesten Fahrt (Weg 0).

STELLWERT DES NUTZERS, keine gemessene Groesse.
"""

FAHRT_MAX_MS = 450
"""Dauer der laengsten Fahrt (Weg :data:`VERSATZ_SPANNE_PX`).

STELLWERT DES NUTZERS, keine gemessene Groesse. Der volle Weg von 350 px in
450 ms ergibt bei Smoothstep eine Spitzengeschwindigkeit von rund 1170 px/s,
also knapp 20 px je Frame bei 60 fps - die Grenze, ab der die Bewegung
zwischen zwei Bildern zu ruckeln beginnt.
"""

AUSTRITT_LINKS = "links"
AUSTRITT_RECHTS = "rechts"

GRUND_BERECHNET = "berechnet"
"""Die Kurve wurde aus dem Cursorprotokoll gerechnet - kein Rueckfall."""

GRUND_KEIN_PROTOKOLL = "kein_cursorprotokoll"
GRUND_KEIN_ANKER = "kein_anker"
GRUND_SPANNE_NICHT_GEDECKT = "protokoll_deckt_spanne_nicht"
GRUND_SPANNE_AUSSERHALB_GERENDERT = "spanne_ausserhalb_gerenderter_achse"
GRUND_KEIN_MEDIAN = "kein_gueltiger_median"


@dataclass(frozen=True, slots=True)
class Fahrt:
    """Eine einzelne Fahrt des Ausschnitts innerhalb einer Kandidatenspanne.

    ``nach`` ist der TATSAECHLICH erreichte Versatz, nicht das angestrebte
    Ziel. Beides faellt auseinander, wenn die Kandidatenspanne endet oder ein
    Szenenfenster verlassen wird, waehrend die Fahrt noch laeuft: die Fahrt
    wird dann mit dem Wert festgehalten, bei dem sie steht, und
    ``dauer_frames`` zaehlt nur die Frames, die sie wirklich bekommen hat.
    ``dauer_frames`` ist deshalb nicht immer gleich
    ``fahrtdauer_frames(nach - von)``.
    """

    start_frame: int
    """Erstes Frame der Fahrt, RELATIV zum Anfang der Kandidatenspanne."""

    dauer_frames: int
    von: int
    nach: int
    richtung: str

    @property
    def weg(self) -> int:
        """Zurueckgelegte Strecke in Pixeln."""
        return abs(self.nach - self.von)


@dataclass(frozen=True, slots=True)
class Versatzkurve:
    """Je gerendertem Frame einer Kandidatenspanne ein Ausschnittversatz."""

    werte: tuple[int, ...]
    grund: str
    """Benannter Grund: :data:`GRUND_BERECHNET` oder einer der Rueckfallgruende."""

    fahrten: tuple[Fahrt, ...] = ()
    naehte: tuple[int, ...] = ()
    """Relative Frames, an denen eine Keep-Segment-Naht den Versatz hart neu gesetzt hat."""

    eingefrorene_frames: int = 0
    """Frames ausserhalb eines Charts-Szenenfensters - dort haelt der Versatz."""

    @property
    def ist_rueckfall(self) -> bool:
        """Wahr, wenn die Kurve nicht gerechnet, sondern zurueckgefallen ist."""
        return self.grund != GRUND_BERECHNET


def auf_geraden_versatz(wert: int) -> int:
    """Runde auf eine gerade Ganzzahl ab und klemme auf [X_OFFSET_MIN_3B, X_OFFSET_MAX_3B].

    Erst runden, dann klemmen - in dieser Reihenfolge, weil beide Anschlaege
    selbst gerade sind und das Klemmen die Geradheit damit nicht wieder
    zerstoeren kann. Abgerundet (statt kaufmaennisch) wird, damit die Rundung
    bei jedem Vorzeichen dieselbe Richtung hat und die Kurve reproduzierbar
    bleibt; ein Pixel Unterschied liegt weit unter der Sichtbarkeitsschwelle.
    """
    gerade = wert - (wert % 2)
    return max(X_OFFSET_MIN_3B, min(X_OFFSET_MAX_3B, gerade))


def trittzone(versatz: int) -> tuple[int, int]:
    """Die Trittzone bei einem gegebenen Versatz, beide Grenzen einschliessend.

    Bei Versatz ``o`` zeigt der Short die Quellspalten ``o`` bis
    ``o + CROP_WIDTH - 1``. Die Trittzone laesst an beiden Seiten
    :data:`TRITTZONE_RAND_PX` frei, ist also ``[o+100, o+1627]``. Liegt der
    Median darin, haelt das Bild.
    """
    return versatz + TRITTZONE_RAND_PX, versatz + CROP_WIDTH - 1 - TRITTZONE_RAND_PX


def austrittsrichtung(versatz: int, median: int | None) -> str | None:
    """Melde, ob und wohin der Median aus der Trittzone ausgetreten ist.

    ``median`` ist eine LEINWANDSPALTE (:func:`gleitender_median`).

    Ein UNDEFINIERTER Median (Cursor nicht im Bild: zweiter Monitor oder
    Leinwandzeile ausserhalb) gilt als Austritt nach links - siehe
    :func:`zielversatz` fuer die Begruendung.

    Ein Median jenseits von ``LEINWAND_BREITE`` braucht keinen eigenen Zweig:
    Der Zeiger steht dann auf Bildschirm 1 in dem Streifen, den OBS
    wegschneidet (die TradingView-Watchlist). Das ist ein gewoehnlicher
    Austritt nach RECHTS, nur mit einem besonders grossen Wert - und
    :func:`zielversatz` klemmt ihn auf den rechten Anschlag. Genau das ist die
    gewollte Behandlung: der Ausschnitt faehrt so weit nach rechts, wie er
    kann, und bleibt dort.
    """
    if median is None:
        return AUSTRITT_LINKS
    links, rechts = trittzone(versatz)
    if median < links:
        return AUSTRITT_LINKS
    if median > rechts:
        return AUSTRITT_RECHTS
    return None


def zielversatz(median: int | None, richtung: str) -> int:
    """Der Versatz, den eine Fahrt anstreben soll.

    Das Ziel ist NICHT die Mitte. Nach der Fahrt soll der Median im Abstand
    ``TRITTZONE_RAND_PX + RESERVE_PX`` von DERJENIGEN Ausschnittkante stehen,
    aus der er ausgetreten ist - der Ausschnitt schiebt also Reserve in die
    Richtung, in die sich der Zeiger gerade bewegt, statt sie symmetrisch zu
    verteilen. Wer nach links hinauslaeuft, laeuft meist weiter nach links.

    Ist der Median undefiniert, ist das Ziel :data:`X_OFFSET_MIN_3B`, der
    linke Anschlag. Der zweite Monitor liegt links; geht der Zeiger dorthin,
    faehrt der Ausschnitt mit nach links bis an den Anschlag und BLEIBT dort
    stehen - kein Zuruecksbringen zur Mitte, kein Hin-und-her-Schnappen. Die
    Maus kommt immer wieder von links herein, und dann steht das Bild schon
    richtig.
    """
    if median is None:
        return X_OFFSET_MIN_3B
    abstand = TRITTZONE_RAND_PX + RESERVE_PX
    if richtung == AUSTRITT_LINKS:
        return auf_geraden_versatz(median - abstand)
    return auf_geraden_versatz(median - (CROP_WIDTH - 1) + abstand)


def anfangsversatz(median: int | None) -> int:
    """Der Versatz, mit dem ein Kandidat (oder ein Abschnitt nach einer Naht) aufgeht.

    KEINE ZENTRIERUNG. Das Bild soll nur so weit schieben, wie es muss, und
    dann andocken - nicht dem Zeiger in die Mitte nachlaufen. Es ist DIESELBE
    Regel wie im laufenden Betrieb, nur ohne Fahrt:

    * Der Anfangsversatz ist :data:`X_OFFSET_MIN_3B`, der linke Anschlag.
    * Liegt der Median bei diesem Versatz schon in der Trittzone, bleibt es
      dabei - der Short geht am Anschlag auf.
    * Sonst wird EINMAL :func:`zielversatz` angewandt, hart und ohne Fahrt.
      Der Short geht dann richtig gerahmt auf, aber am kleinstmoeglichen Ort.
    * Ohne gueltigen Median bleibt es bei :data:`X_OFFSET_MIN_3B`. Das kommt
      oft vor: beide bekannten Protokolle beginnen mit negativem x, der Zeiger
      steht am Anfang also auf dem zweiten Monitor.

    Der Short soll RICHTIG GERAHMT AUFGEHEN, nicht hineinfahren - deshalb
    hart und ohne :func:`ueberblendung`.
    """
    if median is None:
        return X_OFFSET_MIN_3B
    richtung = austrittsrichtung(X_OFFSET_MIN_3B, median)
    if richtung is None:
        return X_OFFSET_MIN_3B
    return zielversatz(median, richtung)


def fahrtdauer_frames(weg: int, *, fps: int = SOURCE_FPS) -> int:
    """Dauer einer Fahrt in Frames, linear nach Weglaenge.

    Linear zwischen :data:`FAHRT_MIN_MS` (Weg 0) und :data:`FAHRT_MAX_MS`
    (Weg :data:`VERSATZ_SPANNE_PX` = 350 px). Der Massstab ist AUSDRUECKLICH
    der Weg dieser Stufe, nicht der in ``chart_crop.py`` erlaubte volle Weg
    von 832 px - eine Fahrt ueber den ganzen hier moeglichen Bereich soll die
    volle Zeit brauchen, nicht einen Bruchteil.
    """
    begrenzt = min(abs(weg), VERSATZ_SPANNE_PX)
    dauer_ms = FAHRT_MIN_MS + (FAHRT_MAX_MS - FAHRT_MIN_MS) * begrenzt // VERSATZ_SPANNE_PX
    return max(1, ms_to_frame(dauer_ms, fps))


def ueberblendung(anteil: float) -> float:
    """Weiche Ueberblendung mit Ableitung null an BEIDEN Enden - Smoothstep ``3t^2 - 2t^3``.

    Die Ableitung ``6t - 6t^2`` ist bei ``t = 0`` und ``t = 1`` null: Die
    Fahrt setzt sich also aus dem Stand in Bewegung und kommt wieder zum
    Stand, statt an beiden Enden anzurucken. Eine lineare Blende haette an
    beiden Enden einen Sprung in der Geschwindigkeit, und genau der sieht wie
    ein Fehler aus.
    """
    t = min(1.0, max(0.0, anteil))
    return t * t * (3.0 - 2.0 * t)


def _segment_index(segmente: Sequence[KeepSegment], quellframe: int) -> int | None:
    """Der Index des Keep-Segments, das ``quellframe`` enthaelt - ``None``, wenn keins."""
    for index, segment in enumerate(segmente):
        if segment.start_frame <= quellframe < segment.end_frame:
            return index
    return None


def _im_fenster(frame: int, fenster: Sequence[tuple[int, int]] | None) -> bool:
    """Wahr, wenn ``frame`` in einem der halboffenen gerenderten Fenster liegt."""
    if fenster is None:
        return True
    return any(start <= frame < ende for start, ende in fenster)


def _fenster_ende(frame: int, fenster: Sequence[tuple[int, int]] | None) -> int | None:
    """Das Ende des Fensters, das ``frame`` enthaelt - ``None``, wenn ohne Fenster."""
    if fenster is None:
        return None
    for start, ende in fenster:
        if start <= frame < ende:
            return ende
    return None


def _rueckfall(laenge: int, grund: str) -> Versatzkurve:
    """Eine konstante Kurve auf ``chart_crop.X_OFFSET_DEFAULT`` mit benanntem Grund."""
    return Versatzkurve(werte=(X_OFFSET_DEFAULT,) * laenge, grund=grund)


def versatzkurve(
    *,
    kandidatenspanne: tuple[int, int],
    segmente: Sequence[KeepSegment],
    zeilen: Sequence[CursorZeile] | None,
    anker: datetime | None,
    szenenfenster: Sequence[tuple[int, int]] | None = None,
    fps: int = SOURCE_FPS,
) -> Versatzkurve:
    """Die Versatzkurve eines Kandidaten: je gerendertem Frame ein Wert.

    ``kandidatenspanne`` ist halboffen und liegt auf der GERENDERTEN Achse -
    genau das, was ``frame_map.candidate_frame_span`` liefert. ``segmente``
    sind die Keep-Segmente des Schnitts auf der Quellachse, ``zeilen`` das
    gelesene Cursorprotokoll, ``anker`` die Wanduhrzeit des Quellframes 0
    (siehe :func:`quellframe_zu_wanduhrzeit`). ``szenenfenster`` sind, wenn
    gesetzt, die Charts-Szenenfenster auf der gerenderten Achse.

    Die Laenge des Ergebnisses ist immer ``ende - anfang``, und jeder Wert ist
    eine gerade Ganzzahl in ``[X_OFFSET_MIN_3B, X_OFFSET_MAX_3B]``. Diese
    Funktion wirft KEINE Ausnahme wegen fehlender oder unpassender Daten - sie
    faellt auf eine konstante Kurve zurueck und nennt den Grund im
    Rueckgabewert. Eine unsinnige Spanne (Ende nicht nach Anfang) ist dagegen
    ein Aufruffehler und wirft ``ValueError``.

    ZUSTANDSFUEHRUNG, in der Reihenfolge, in der sie je Frame greift:

    1. Ausserhalb eines Szenenfensters friert der Versatz ein - dort ist kein
       Chart im Bild, es gibt also nichts zu verfolgen. Eine laufende Fahrt
       wird dabei abgebrochen und der Zustand zurueckgesetzt.
    2. An einer Keep-Segment-Naht wird der Versatz HART neu gesetzt, ohne
       Fahrt - ein Schnitt ist ohnehin ein Schnitt, eine weiche Fahrt darueber
       saehe wie Driften aus. Gibt es im neuen Segment keinen gueltigen
       Median, haelt der bisherige Versatz und die normale Zustandsfuehrung
       uebernimmt.
    3. Eine laufende Fahrt wird fortgesetzt (:func:`ueberblendung`).
    4. Sonst wird die Austrittsrichtung bestimmt. Ein Austritt muss
       :data:`AUSTRITT_VERZOEGERUNG_MS` anhalten und darf nicht in die
       :data:`MINDESTVERWEILDAUER_MS` nach der letzten Fahrt fallen, dann
       beginnt eine neue Fahrt auf :func:`zielversatz`. Eine Fahrt beginnt
       jedoch NICHT, wenn bis zum Ende der Kandidatenspanne oder des
       Szenenfensters weniger Frames uebrig sind, als sie braucht - der
       Versatz haelt dann bis zum Ende.

    Waehrend einer Fahrt wird kein Austritt gezaehlt; waehrend der
    Mindestverweildauer DOCH, damit ein durchgehend anhaltender Austritt
    unmittelbar nach ihrem Ablauf weiterfaehrt statt noch einmal 300 ms zu
    warten.
    """
    anfang, ende = kandidatenspanne
    laenge = ende - anfang
    if laenge <= 0:
        raise ValueError(f"Kandidatenspanne muss positiv sein, ist aber {laenge}")
    if not zeilen:
        return _rueckfall(laenge, GRUND_KEIN_PROTOKOLL)
    if anker is None:
        return _rueckfall(laenge, GRUND_KEIN_ANKER)

    quellframes: list[int] = []
    segmentindizes: list[int] = []
    for versatz_im_kandidaten in range(laenge):
        quellframe = map_rendered_frame(segmente, anfang + versatz_im_kandidaten)
        if quellframe is None:
            return _rueckfall(laenge, GRUND_SPANNE_AUSSERHALB_GERENDERT)
        index = _segment_index(segmente, quellframe)
        if index is None:
            return _rueckfall(laenge, GRUND_SPANNE_AUSSERHALB_GERENDERT)
        quellframes.append(quellframe)
        segmentindizes.append(index)

    zeitpunkte = [quellframe_zu_wanduhrzeit(q, anker, fps=fps) for q in quellframes]
    if zeitpunkte[0] < zeilen[0].zeit or zeitpunkte[-1] > zeilen[-1].zeit:
        return _rueckfall(laenge, GRUND_SPANNE_NICHT_GEDECKT)

    mediane: list[int | None] = []
    for zeitpunkt, index in zip(zeitpunkte, segmentindizes, strict=True):
        segment = segmente[index]
        mediane.append(
            gleitender_median(
                zeilen,
                zeitpunkt,
                von=quellframe_zu_wanduhrzeit(segment.start_frame, anker, fps=fps),
                bis=quellframe_zu_wanduhrzeit(segment.end_frame - 1, anker, fps=fps),
            )
        )
    if all(median is None for median in mediane):
        return _rueckfall(laenge, GRUND_KEIN_MEDIAN)

    verzoegerung_frames = ms_to_frame(AUSTRITT_VERZOEGERUNG_MS, fps)
    verweil_frames = ms_to_frame(MINDESTVERWEILDAUER_MS, fps)

    werte: list[int] = []
    fahrten: list[Fahrt] = []
    naehte: list[int] = []
    eingefroren = 0

    versatz = anfangsversatz(next((m for m in mediane if m is not None), None))
    richtung_gehalten: str | None = None
    austritt_frames = 0
    sperre_bis = -1
    fahrt_von = 0
    fahrt_nach = 0
    fahrt_dauer = 0
    fahrt_frames = 0
    fahrt_start = -1
    fahrt_richtung = AUSTRITT_LINKS
    in_fahrt = False

    for i in range(laenge):
        if not _im_fenster(anfang + i, szenenfenster):
            eingefroren += 1
            if in_fahrt:
                fahrten.append(
                    Fahrt(
                        start_frame=fahrt_start,
                        dauer_frames=fahrt_frames,
                        von=fahrt_von,
                        nach=versatz,
                        richtung=fahrt_richtung,
                    )
                )
                in_fahrt = False
            richtung_gehalten = None
            austritt_frames = 0
            werte.append(versatz)
            continue

        if i > 0 and segmentindizes[i] != segmentindizes[i - 1]:
            naehte.append(i)
            neuer_median = next(
                (
                    mediane[j]
                    for j in range(i, laenge)
                    if segmentindizes[j] == segmentindizes[i] and mediane[j] is not None
                ),
                None,
            )
            if neuer_median is not None:
                versatz = anfangsversatz(neuer_median)
            in_fahrt = False
            richtung_gehalten = None
            austritt_frames = 0
            sperre_bis = -1
            werte.append(versatz)
            continue

        if in_fahrt:
            fahrt_frames += 1
            if fahrt_frames >= fahrt_dauer:
                versatz = fahrt_nach
                fahrten.append(
                    Fahrt(
                        start_frame=fahrt_start,
                        dauer_frames=fahrt_frames,
                        von=fahrt_von,
                        nach=fahrt_nach,
                        richtung=fahrt_richtung,
                    )
                )
                in_fahrt = False
                sperre_bis = i + verweil_frames
            else:
                roh = fahrt_von + (fahrt_nach - fahrt_von) * ueberblendung(
                    fahrt_frames / fahrt_dauer
                )
                versatz = auf_geraden_versatz(round(roh))
            werte.append(versatz)
            continue

        median = mediane[i]
        richtung = austrittsrichtung(versatz, median)
        if richtung is None:
            richtung_gehalten = None
            austritt_frames = 0
            werte.append(versatz)
            continue

        if richtung != richtung_gehalten:
            richtung_gehalten = richtung
            austritt_frames = 0
        austritt_frames += 1

        if austritt_frames >= verzoegerung_frames and i >= sperre_bis:
            ziel = zielversatz(median, richtung)
            dauer = fahrtdauer_frames(ziel - versatz, fps=fps)
            fenster_ende = _fenster_ende(anfang + i, szenenfenster)
            grenze = laenge if fenster_ende is None else min(laenge, fenster_ende - anfang)
            if ziel == versatz:
                austritt_frames = 0
            elif grenze - i < dauer:
                # EINE FAHRT BEGINNT NICHT, WENN SIE NICHT FERTIG WIRD.
                # Ein Short, der mitten in der Bewegung abbricht, springt beim
                # Schleifendurchlauf auf seine eigene Anfangsrahmung zurueck -
                # und zerstoert damit genau die Schleife, auf die diese Linie
                # zielt. Der Versatz haelt dann bis zum Ende. Der Preis dafuer
                # ist, dass der Zeiger die letzte halbe Sekunde aus dem Bild
                # laufen kann; das ist die gewollte Abwaegung.
                pass
            else:
                fahrt_von = versatz
                fahrt_nach = ziel
                fahrt_dauer = dauer
                fahrt_frames = 1
                fahrt_start = i
                fahrt_richtung = richtung
                in_fahrt = True
                richtung_gehalten = None
                austritt_frames = 0
                if fahrt_frames >= fahrt_dauer:
                    versatz = fahrt_nach
                    fahrten.append(
                        Fahrt(
                            start_frame=fahrt_start,
                            dauer_frames=fahrt_frames,
                            von=fahrt_von,
                            nach=fahrt_nach,
                            richtung=fahrt_richtung,
                        )
                    )
                    in_fahrt = False
                    sperre_bis = i + verweil_frames
                else:
                    versatz = auf_geraden_versatz(
                        round(fahrt_von + (fahrt_nach - fahrt_von) * ueberblendung(1 / fahrt_dauer))
                    )
        werte.append(versatz)

    if in_fahrt:
        fahrten.append(
            Fahrt(
                start_frame=fahrt_start,
                dauer_frames=fahrt_frames,
                von=fahrt_von,
                nach=versatz,
                richtung=fahrt_richtung,
            )
        )

    return Versatzkurve(
        werte=tuple(werte),
        grund=GRUND_BERECHNET,
        fahrten=tuple(fahrten),
        naehte=tuple(naehte),
        eingefrorene_frames=eingefroren,
    )
