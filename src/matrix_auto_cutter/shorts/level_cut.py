r"""Auftrag shorts-pegelschnitt: eine gerastete Zeitmarke auf die leiseste Stelle schieben.

Das Rasten auf Wortgrenzen (:mod:`matrix_auto_cutter.shorts.loop_point`) trifft
einen PUNKT - die Sprechpause ist aber ein BEREICH. Geschnitten werden muss in
deren Mitte, nicht an ihrem Rand. Dazu kommt, dass whisper bei rund 80 % der
Uebergaenge das Wortende auf den naechsten Wortanfang legt: Die Wortgrenze liegt
dann am ANFANG der Pause (beim vorigen Wort) und am ENDE der Pause (beim
naechsten). Deshalb ueberwiegt beim Rasten "zu spaet" - im Lauf
``artefakte/repeat/shorts-bau/lauf-2`` bei den Kandidaten 6, 10 und 14, waehrend
nur 20 zu frueh lag.

Dieses Modul poliert nur die Kante: Es sucht in einem engen Fenster um die
gerastete Marke die leiseste Stelle und verschiebt die Marke dorthin. Den
thematischen Zuschnitt bestimmt weiterhin die Zerlegung, nicht dieses Modul -
:data:`SEARCH_WINDOW_MS` ist bewusst eng gewaehlt.

Es gibt KEINEN absoluten Stilleschwellwert. Unter der Aufnahme liegt
durchgehend Hintergrundmusik, echte Stille kommt nicht vor. Gesucht wird immer
nur relativ zum Fenster.

Gesucht wird dabei NICHT der tiefste einzelne Punkt, sondern die MITTE des
leisen BEREICHS (Auftrag shorts-pegelmedian). Bei gedehnter Aussprache liegen
zwischen den Lauten eines Wortes kurze leise Stellen, die punktuell tiefer
sein koennen als die echte Sprechpause daneben - der tiefste Punkt landet dann
mitten im Wort. Ein Bereich, der lang genug ist, um eine Sprechpause zu sein
(:data:`MIN_PAUSE_MS`), trifft diese Lautluecken nicht. Erfuellt kein Bereich
im Fenster die Bedingung, faellt das Modul auf den tiefsten Punkt zurueck; das
gewaehlte Verfahren steht in :attr:`LevelSnap.verfahren` und gehoert in den
Laufbericht.

Schlaegt die Messung fehl, haelt das Modul mit :class:`LevelCutFailed` an. Es
gibt bewusst KEINEN stillen Rueckfall auf die unkorrigierte Marke - der
Aufrufer soll wissen, dass nicht poliert wurde.
"""

from __future__ import annotations

import math
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Die Konstanten des Verfahrens, alle an EINER Stelle.
# ---------------------------------------------------------------------------

SEARCH_WINDOW_MS = 250
"""Suchweite je Richtung - das Fenster ist also 500 ms breit.

Bewusst eng: Die Shorts sollen kuenftig unter 30 Sekunden liegen, ein
breiteres Fenster wuerde den thematischen Zuschnitt verschieben statt ihn nur
zu polieren."""

SEARCH_WINDOW_START_MS = 150
"""Suchweite rueckwaerts an der STARTgrenze, wirksam nur bei ``nur_rueckwaerts``.

Auftrag shorts-pegelfenster-vergleich: An der Startgrenze zeigte sich
``SEARCH_WINDOW_MS`` (250 ms) zu grosszuegig - die Kandidaten 3 und 17 der
Aufnahme 2026-08-18 08-51-21 wanderten die vollen 250 bzw. 200 ms und
schnitten dadurch zu frueh. Die ENDgrenze bleibt unveraendert bei
``SEARCH_WINDOW_MS`` - dort waren Kandidat 28 und 41 tadellos. Vorlaeufiger
Wert 150, vom Nutzer nach Vergleichshoeren gegen 100 ms zu bestaetigen."""

STEP_MS = 10
"""Schrittweite, in der das Fenster abgetastet wird."""

MEASURE_MS = 40
"""Laenge des gemessenen Ausschnitts je Schritt.

Der Ausschnitt liegt MITTIG um die jeweils gepruefte Stelle: ein Schnitt bei
``t`` faellt auf, wenn kurz VOR und kurz NACH ``t`` Energie liegt - genau das
misst ein um ``t`` zentrierter Ausschnitt."""

TIE_TOLERANCE_DB = 0.5
"""Gleichstandsband um das Minimum. Innerhalb dieses Bandes gewinnt die
FRUEHER liegende Stelle.

Ein Short, das eine Silbe zu frueh endet, klingt zu Ende; eines, das ins
naechste Wort ragt, klingt abgeschnitten. Die im Lauf lauf-2 beobachtete
Tendenz (viermal "zu spaet", einmal "zu frueh") wird damit bewusst nach vorn
korrigiert."""

QUIET_REGION_DEPTH_DB = 6.0
"""Wie weit unter dem Fenstermittel ein Block liegen muss, um zum leisen
BEREICH zu zaehlen - Auftrag shorts-pegelmedian.

Bewusst relativ zum FENSTERMITTEL und nicht zum Fensterminimum: ein Fenster
ohne jede Pause (gemessen an der Startgrenze von Kandidat 41, Spannweite
1,6 dB ueber die ganzen 150 ms) hat trotzdem ein Minimum - eine Schwelle
"Minimum plus X" wuerde dort einen 160 ms langen "Bereich" erfinden, wo nur
gleichmaessig laute Sprache liegt. Die Schwelle relativ zum Mittel findet dort
richtigerweise gar nichts und faellt zurueck.

Gemessen an 48 s der Aufnahme 2026-08-18 08-51-21 (4904 Fensterlagen, siehe
``artefakte/repeat/shorts-pegelmedian/BERICHT-2026-08-19.md``): 6 dB liegt
zusammen mit 5 dB am Bestwert des Verhaeltnisses "Marke wandert aus der
Wortmitte heraus" zu "Marke wandert hinein" (699 zu 256), und ist von beiden
der vorsichtigere Wert."""

MIN_PAUSE_MS = 100
"""Mindestlaenge eines leisen Bereichs, damit er als SPRECHPAUSE zaehlt.

An echtem Material gemessen (dieselben 48 s): leise Bereiche, die vollstaendig
INNERHALB eines whisper-Wortes liegen - also sichere Lautluecken - haben den
Median 70 ms; Bereiche an einer Wortgrenze oder in einer whisper-Pause den
Median 130 bzw. 160 ms. Bei 100 ms erfuellen nur noch 29 % der Lautluecken,
aber 70 % der echten Uebergaenge die Bedingung. In der Parametersuche ueber
Schwellen von 4 bis 10 dB ist 100 ms in JEDER Zeile der Bestwert - unter 100 ms
kommen Lautluecken zurueck, darueber verliert das Verfahren zu viele echte
Pausen."""

SPEECH_BAND_HIGHPASS_HZ = 200
"""Untere Grenze des Sprachbands, auf das vor der Messung gefiltert wird."""

SPEECH_BAND_LOWPASS_HZ = 3400
"""Obere Grenze des Sprachbands.

Gemessen am Pruefstein (12 Grenzen, Video 2026-08-07 11-35-16) liegt das
Minimum im Sprachband im Median 22,4 dB unter dem Fenstermittel, breitbandig
nur 18,0 dB: Die Pause tritt gefiltert deutlich schaerfer hervor, weil die
durchgehende Musik ihre Energie groesstenteils unterhalb 200 Hz hat. Noch
engere Baender (300-3000, 400-2500) messen zwar groessere Abstaende, waehlen
aber im Median dieselbe Stelle (Abweichung 0 ms) - der Zugewinn ist
rechnerisch, nicht hoerbar - und wuerden Reibelaute (s, f, sch) verlieren, die
oberhalb 3400 Hz liegen. 200-3400 Hz ist deshalb der Haltepunkt."""

MEASURE_SAMPLE_RATE = 48000
"""Abtastrate, auf die vor der Messung gebracht wird - macht die Blockgroesse
in Samples von der Quelle unabhaengig."""

_BLOCK_SAMPLES = MEASURE_SAMPLE_RATE * STEP_MS // 1000
_BLOCKS_PER_MEASURE = MEASURE_MS // STEP_MS
_ASTATS_KEY = "lavfi.astats.Overall.RMS_level"

assert MEASURE_MS % STEP_MS == 0, "MEASURE_MS muss ein Vielfaches von STEP_MS sein"
assert SEARCH_WINDOW_MS % STEP_MS == 0, "SEARCH_WINDOW_MS muss ein Vielfaches von STEP_MS sein"
assert SEARCH_WINDOW_START_MS % STEP_MS == 0, (
    "SEARCH_WINDOW_START_MS muss ein Vielfaches von STEP_MS sein"
)
assert MIN_PAUSE_MS % STEP_MS == 0, "MIN_PAUSE_MS muss ein Vielfaches von STEP_MS sein"


VERFAHREN_BEREICHSMITTE = "bereichsmitte"
"""Die Marke kam aus der Mitte eines ausreichend langen leisen Bereichs."""

VERFAHREN_TIEFSTER_PUNKT = "tiefster_punkt"
"""Rueckfall: kein Bereich im Fenster war lang genug, es zaehlte der tiefste Punkt."""


class LevelCutFailed(RuntimeError):
    """Die Pegelmessung ist fehlgeschlagen - fail closed, kein stiller Rueckfall.

    ``code`` benennt die Ursache maschinenlesbar, ``message_de`` im Klartext.
    """

    def __init__(self, code: str, message_de: str) -> None:
        """Halte Fehlercode und Klartext nebeneinander fest."""
        super().__init__(f"[{code}] {message_de}")
        self.code = code
        self.message_de = message_de


@dataclass(frozen=True, slots=True)
class LevelSnap:
    """Ergebnis der Pegelkorrektur einer einzelnen Zeitmarke."""

    original_ms: int
    """Die uebergebene, grob gerastete Marke."""

    corrected_ms: int
    """Die leiseste Stelle im Fenster - die korrigierte Marke."""

    shift_ms: int
    """``corrected_ms - original_ms``; negativ heisst: nach vorn gewandert."""

    level_db: float
    """Gemessener Pegel an der gewaehlten Stelle."""

    window_mean_db: float
    """Mittlerer Pegel ueber alle gemessenen Stellen des Fensters."""

    verfahren: str = VERFAHREN_TIEFSTER_PUNKT
    """Welches Verfahren die Stelle bestimmt hat - siehe :data:`VERFAHREN_BEREICHSMITTE`
    und :data:`VERFAHREN_TIEFSTER_PUNKT`. Gehoert in den Laufbericht, damit der
    Nutzer sieht, wann welches griff."""

    quiet_region_ms: int = 0
    """Laenge des gewaehlten leisen Bereichs; 0 beim Rueckfall auf den tiefsten Punkt."""

    @property
    def depth_db(self) -> float:
        """Wie tief das Minimum unter dem Fenstermittel liegt.

        DIE entscheidende Zahl: ein bis zwei dB heissen, dass im Fenster gar
        keine Pause liegt und 250 ms zu eng sind; zehn oder mehr dB heissen,
        dass die Pause getroffen ist.
        """
        return self.window_mean_db - self.level_db


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Begrenzter ffmpeg-Prozessausgang - eigenstaendig, analog zu ``avatar_cut``."""

    exit_code: int
    stdout: str


ProcessRunner = Callable[[Sequence[str], int], ProcessResult]


def _default_process_runner(arguments: Sequence[str], timeout_seconds: int) -> ProcessResult:
    """Fuehre ffmpeg aus und gib nur stdout zurueck - dort landet ``ametadata``."""
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessResult(-1, f"ffmpeg nicht ausfuehrbar: {exc}")
    return ProcessResult(result.returncode, (result.stdout or b"").decode("utf-8", "replace"))


def _filter_chain() -> str:
    """Der Filtergraph: Sprachband, 10-ms-Bloecke, RMS je Block auf stdout.

    EIN ffmpeg-Aufruf misst damit das ganze Fenster. Aus nicht ueberlappenden
    10-ms-Bloecken laesst sich der 40-ms-Wert an jeder 10-ms-Stelle exakt
    zusammensetzen (siehe :func:`_combine_to_measure_window`), weil sich
    Leistungen addieren.
    """
    return (
        f"aresample={MEASURE_SAMPLE_RATE},"
        f"highpass=f={SPEECH_BAND_HIGHPASS_HZ},"
        f"lowpass=f={SPEECH_BAND_LOWPASS_HZ},"
        f"asetnsamples=n={_BLOCK_SAMPLES}:p=0,"
        f"astats=metadata=1:reset=1,"
        f"ametadata=print:key={_ASTATS_KEY}:file=-"
    )


def _parse_block_levels(stdout: str) -> list[float]:
    """Lies die je Block gedruckten RMS-Werte in dB aus der ffmpeg-Ausgabe."""
    levels: list[float] = []
    prefix = f"{_ASTATS_KEY}="
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        raw = line[len(prefix) :].strip()
        try:
            value = float(raw)
        except ValueError:
            # astats schreibt bei digitaler Stille "-inf" bzw. "nan".
            value = -math.inf
        levels.append(-math.inf if math.isnan(value) else value)
    return levels


def _combine_to_measure_window(block_levels: Sequence[float]) -> list[float]:
    """Setze aus 10-ms-Bloecken die 40-ms-Werte im 10-ms-Raster zusammen.

    Der RMS ueber 40 ms ist die Wurzel aus dem Mittel der vier 10-ms-Mittel-
    quadrate - die Kombination ist also exakt, keine Naeherung. Am Pruefstein
    stimmt sie mit ``volumedetect`` auf denselben 40 ms auf +-0,05 dB ueberein,
    also innerhalb dessen 0,1-dB-Anzeige.
    """
    combined: list[float] = []
    for index in range(len(block_levels) - _BLOCKS_PER_MEASURE + 1):
        window = block_levels[index : index + _BLOCKS_PER_MEASURE]
        power = sum(10 ** (level / 10) for level in window) / len(window)
        combined.append(10 * math.log10(power) if power > 0 else -math.inf)
    return combined


def _mean_level_db(levels: Sequence[float]) -> float:
    """Mittlerer Pegel: Leistungsmittel, nicht dB-Mittel - dB sind logarithmisch."""
    power = sum(10 ** (level / 10) for level in levels) / len(levels)
    return 10 * math.log10(power) if power > 0 else -math.inf


def _choose_quietest(levels: Sequence[float]) -> int:
    """Index der leisesten Stelle; bei Gleichstand gewinnt die FRUEHERE.

    "Gleichstand" heisst: innerhalb von :data:`TIE_TOLERANCE_DB` des Minimums
    (siehe dort, warum bewusst nach vorn korrigiert wird).
    """
    minimum = min(levels)
    if minimum == -math.inf:
        return levels.index(minimum)
    limit = minimum + TIE_TOLERANCE_DB
    for index, level in enumerate(levels):
        if level <= limit:
            return index
    return levels.index(minimum)  # unerreichbar, aber kein stiller Rueckfall


def _quiet_regions(levels: Sequence[float], threshold_db: float) -> list[tuple[int, int]]:
    """Zusammenhaengende Laeufe von Stellen, die unter ``threshold_db`` liegen.

    Zurueck kommen halboffene Indexpaare ``(erster, letzter)`` - jede Stelle
    steht fuer :data:`STEP_MS`, der Bereich ist also
    ``(letzter - erster + 1) * STEP_MS`` lang.
    """
    regions: list[tuple[int, int]] = []
    index = 0
    while index < len(levels):
        if levels[index] > threshold_db:
            index += 1
            continue
        end = index
        while end + 1 < len(levels) and levels[end + 1] <= threshold_db:
            end += 1
        regions.append((index, end))
        index = end + 1
    return regions


def _choose_cut_point(levels: Sequence[float]) -> tuple[int, str, int]:
    """Waehle die Schnittstelle im Fenster: Bereichsmitte, sonst tiefster Punkt.

    Ein "leiser Bereich" ist ein zusammenhaengender Lauf von Stellen, die
    mindestens :data:`QUIET_REGION_DEPTH_DB` unter dem Fenstermittel liegen. Als
    Sprechpause zaehlt er erst ab :data:`MIN_PAUSE_MS` - kuerzere Laeufe sind
    Lautluecken zwischen den Lauten eines gedehnt gesprochenen Wortes und
    werden verworfen, auch wenn sie punktuell tiefer reichen.

    Erfuellen mehrere Bereiche die Bedingung, gewinnt der LAENGSTE; bei
    gleicher Laenge der leisere (Minimum des Bereichs, gestuft in
    :data:`TIE_TOLERANCE_DB`), bei Gleichstand auch darin der FRUEHERE - die
    Gleichstandsregel des tiefsten Punktes, sinngemaess auf Bereiche
    uebertragen.

    Erfuellt kein Bereich die Bedingung, faellt die Wahl auf den tiefsten Punkt
    (:func:`_choose_quietest`) zurueck. Das ist der ausdruecklich zugelassene
    Rueckfall - er wird ueber den Rueckgabewert ausgewiesen, nicht verschwiegen.

    Zurueck kommen Index, Verfahrensname und Bereichslaenge in ms (0 beim
    Rueckfall).
    """
    threshold_db = _mean_level_db(levels) - QUIET_REGION_DEPTH_DB
    minimum_run = MIN_PAUSE_MS // STEP_MS
    regions = [
        region
        for region in _quiet_regions(levels, threshold_db)
        if region[1] - region[0] + 1 >= minimum_run
    ]
    if not regions:
        return _choose_quietest(levels), VERFAHREN_TIEFSTER_PUNKT, 0

    def rang(region: tuple[int, int]) -> tuple[int, float, int]:
        start, end = region
        quietest = min(levels[start : end + 1])
        stufe = math.floor(quietest / TIE_TOLERANCE_DB) if quietest != -math.inf else -math.inf
        return (-(end - start), stufe, start)

    start, end = min(regions, key=rang)
    return (start + end) // 2, VERFAHREN_BEREICHSMITTE, (end - start + 1) * STEP_MS


VORLAUF_SUCHE_MAX_MS = 3000
"""Hoechste Suchweite vorwaerts ab der gerasteten Startmarke fuer den
Stillevorlauf-Test (Auftrag shorts-stillevorlauf, siehe :func:`finde_stillevorlauf`).

Behebt einen anderen Fehler als das Rasten/die Pegelmessung oben: whisper legt
manche SEGMENTgrenzen an den Anfang einer Sprechpause statt an den Beginn des
naechsten Satzes und streckt dessen Woerter ueber die Pause - das trifft
weder das Rasten (das nur an Wortgrenzen der Wortliste arbeitet, die genau
diesen Fehler traegt) noch die obige Pegelmessung (deren Fenster von
:data:`SEARCH_WINDOW_START_MS` mit 150 ms viel zu eng ist, um eine mehrere
Sekunden lange Fehlmarke zu erreichen). Belegt in
``artefakte/repeat/shorts-startgrenze/BEFUND-2026-08-21.md``: Kandidat 31 der
Aufnahme 2026-08-19 17-26-15 beginnt mit 1900 ms echter Stille, Kandidat 5 mit
600 ms Stille, einem 400 ms gehaltenen Laut und noch einmal 950 ms Stille."""

STILLE_ABSTAND_DB = 20.0
"""Wieviel unter dem SPRECHPEGEL des Kandidaten eine Stelle liegen muss, um als
Stille zu zaehlen (Auftrag shorts-stillevorlauf).

Der gehaltene Laut bei Kandidat 5 der Aufnahme 2026-08-19 17-26-15 liegt bei
-19,2 dB gegen -14,8 dB Sprechpegel - nur 4,4 dB darunter, also klar innerhalb
dieser Schwelle und damit KEINE Stille: er ist ein Stilmittel des Sprechers
und muss im Short bleiben. Die echte Stille bei Kandidat 31 liegt dagegen
25,1 dB unter Sprechpegel, klar darueber."""

VORLAUF_MAX_MS = 500
"""Ab welcher Laenge durchgehender Stille AB DER MARKE verschoben wird
(Auftrag shorts-stillevorlauf).

Ueber alle 33 Kandidaten der Aufnahme 2026-08-19 17-26-15 haben die 25 Marken
ausserhalb einer whisper-Segmentgrenze Median 0 ms Vorlauf, und selbst unter
den 8 Marken AUF einer Segmentgrenze liegen nur zwei (Kandidat 5 mit 600 ms,
Kandidat 31 mit 1900 ms) ueber 600 ms - die naechstkleineren echten Vorlaeufe
liegen bei 150 (Kandidat 14) und 250 ms (Kandidat 23) und sollen unangetastet
bleiben. 500 ms trennt beide Gruppen klar."""

VORLAUF_REST_MS = 150
"""Anlauf, der nach der Verschiebung vor dem ersten Ton stehenbleibt (Auftrag
shorts-stillevorlauf) - derselbe Wert wie der von Kandidat 14 gebilligte
Vorlauf (150 ms), damit die verschobene Marke nicht wieder zu dicht am Ton
sitzt."""

STILLE_UNTERBRECHUNG_MAX_MS = 120
"""Hoechstlaenge einer Unterbrechung, die einen Stillebereich noch nicht
beendet (Auftrag shorts-stillevorlauf-toleranz).

Ohne Toleranz verlangt der Bereich luckenlose Stille ab dem ALLERERSTEN
gemessenen Block - an echtem Ton unerfuellbar: der letzte Laut klingt 20 bis
30 ms nach, und durchlaufende Hintergrundmusik durchbricht die Schwelle bei
Kandidat 31 der Aufnahme 2026-08-19 17-26-15 alle rund 1,2 s fuer 30 bis
40 ms. Ohne Toleranz maass die Stille dort durchgehend 0 ms (siehe
``artefakte/repeat/shorts-stillevorlauf/BERICHT-2026-08-21.md``).

120 ms liegt klar ueber diesen kurzen Stoerungen, aber klar unter dem 400 ms
langen gehaltenen Laut bei Kandidat 5 (mehr als das Dreifache) - dieser Laut
ist ein Stilmittel des Sprechers und muss die Verschiebung beenden, nicht
ueberbruecken."""

assert VORLAUF_SUCHE_MAX_MS % STEP_MS == 0, (
    "VORLAUF_SUCHE_MAX_MS muss ein Vielfaches von STEP_MS sein"
)
assert VORLAUF_MAX_MS % STEP_MS == 0, "VORLAUF_MAX_MS muss ein Vielfaches von STEP_MS sein"
assert VORLAUF_REST_MS % STEP_MS == 0, "VORLAUF_REST_MS muss ein Vielfaches von STEP_MS sein"
assert STILLE_UNTERBRECHUNG_MAX_MS % STEP_MS == 0, (
    "STILLE_UNTERBRECHUNG_MAX_MS muss ein Vielfaches von STEP_MS sein"
)


@dataclass(frozen=True, slots=True)
class StilleVorlauf:
    """Ergebnis der Stillevorlauf-Pruefung vor der Startgrenzen-Pegelmessung.

    Auftrag shorts-stillevorlauf - siehe :func:`finde_stillevorlauf` und
    :data:`VORLAUF_SUCHE_MAX_MS`.
    """

    original_ms: int
    """Die gerastete Marke, wie sie hereinkam."""

    corrected_ms: int
    """Marke nach der Verschiebung; gleich ``original_ms``, wenn nichts gefunden wurde."""

    shift_ms: int
    """``corrected_ms - original_ms`` (0, wenn nicht verschoben)."""

    verschoben: bool
    """Ob eine lange genug durchgehende Stille ab der Marke gefunden wurde."""

    sprechpegel_db: float
    """Sprechpegel des Kandidaten - energetisches Mittel der LAUTEREN HAELFTE aller
    40-ms-Ausschnitte der GANZEN Kandidatenspanne (nicht des Suchfensters), siehe
    :func:`finde_stillevorlauf`. Nicht das einfache Mittel ueber die ganze Spanne:
    das wird durch die Sprechpausen darin nach unten gezogen (bei Kandidat 31 der
    Aufnahme 2026-08-19 17-26-15 auf -17,6 statt -14,8 dB) und macht die
    Stille-Schwelle unnoetig eng. Dieselbe Definition wie in
    ``artefakte/repeat/shorts-startgrenze/BEFUND-2026-08-21.md`` ("energetisches
    Mittel der lauteren Haelfte des ganzen Shorts") - dort an denselben vier
    Kandidaten gegen den Ton geprueft."""

    stille_laenge_ms: int
    """Laenge des gewaehlten Stillebereichs ab der Marke, in ms - Unterbrechungen bis
    :data:`STILLE_UNTERBRECHUNG_MAX_MS` eingerechnet (0, wenn schon die erste
    Unterbrechung laenger ist)."""

    unterbrechungen_anzahl: int = 0
    """Wieviele Unterbrechungen (Bloecke ueber der Schwelle, jeweils hoechstens
    :data:`STILLE_UNTERBRECHUNG_MAX_MS` lang) im gewaehlten Bereich ueberbrueckt
    wurden - Auftrag shorts-stillevorlauf-toleranz. Gehoert in den Laufbericht,
    sonst laesst sich spaeter nicht beurteilen, ob die Toleranz passt."""

    laengste_unterbrechung_ms: int = 0
    """Laenge der laengsten ueberbrueckten Unterbrechung im gewaehlten Bereich, in ms
    (0, wenn keine Unterbrechung vorkam)."""


def _fetch_measured_levels(
    media_path: Path,
    *,
    fetch_start_ms: int,
    fetch_duration_ms: int,
    ffmpeg_path: Path,
    timeout_seconds: int,
    process_runner: ProcessRunner,
    context: str,
) -> list[float]:
    """Miss die 40-ms-Pegel im 10-ms-Raster ueber ``[fetch_start_ms, +fetch_duration_ms)``.

    Gemeinsamer Kern fuer die Sprechpegel- und die Vorwaertsmessung in
    :func:`finde_stillevorlauf` - dieselbe Filterkette wie
    :func:`verschiebe_auf_leiseste_stelle`. ``context`` geht nur in die
    Fehlermeldung ein, damit sich Sprechpegel- von Vorlauf-Fehlern unterscheiden
    lassen.
    """
    arguments = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{max(0, fetch_start_ms) / 1000:.3f}",
        "-t",
        f"{fetch_duration_ms / 1000:.3f}",
        "-i",
        str(media_path),
        "-map",
        "0:a:0",
        "-af",
        _filter_chain(),
        "-f",
        "null",
        "-",
    ]
    result = process_runner(arguments, timeout_seconds)
    if result.exit_code != 0:
        raise LevelCutFailed(
            "ffmpeg_fehlgeschlagen",
            f"ffmpeg endete mit Code {result.exit_code} fuer {media_path} bei "
            f"{fetch_start_ms} ms ({context})",
        )
    block_levels = _parse_block_levels(result.stdout)
    if len(block_levels) < _BLOCKS_PER_MEASURE:
        raise LevelCutFailed(
            "keine_messung",
            f"kein messbarer Ton bei {fetch_start_ms} ms in {media_path} ({context})",
        )
    return _combine_to_measure_window(block_levels)


def _stillebereich_laenge(
    levels: Sequence[float], threshold_db: float
) -> tuple[int, int, int]:
    """Laenge (in Bloecken) des Stillebereichs ab Index 0 - mit Toleranz.

    Auftrag shorts-stillevorlauf-toleranz: Ohne Toleranz beendet ein einzelner
    Block ueber ``threshold_db`` den Bereich sofort - an echtem Ton (Nachhall,
    Musikakzente) unerfuellbar, siehe :data:`STILLE_UNTERBRECHUNG_MAX_MS`. Eine
    Unterbrechung (ein zusammenhaengender Lauf von Bloecken >= ``threshold_db``)
    wird deshalb ueberbrueckt, solange sie hoechstens
    :data:`STILLE_UNTERBRECHUNG_MAX_MS` lang ist; eine laengere Unterbrechung
    beendet den Bereich an ihrem Anfang.

    Gibt ``(bereichslaenge_bloecke, unterbrechungen_anzahl, laengste_unterbrechung_bloecke)``
    zurueck - die letzten beiden gehoeren in den Laufbericht (siehe
    :attr:`StilleVorlauf.unterbrechungen_anzahl`), sonst laesst sich spaeter
    nicht beurteilen, ob die Toleranz passt.
    """
    toleranz_bloecke = STILLE_UNTERBRECHUNG_MAX_MS // STEP_MS
    index = 0
    unterbrechungen_anzahl = 0
    laengste_unterbrechung_bloecke = 0
    while index < len(levels):
        if levels[index] < threshold_db:
            index += 1
            continue
        lauf_start = index
        while index < len(levels) and levels[index] >= threshold_db:
            index += 1
        lauf_laenge = index - lauf_start
        if lauf_laenge > toleranz_bloecke:
            return lauf_start, unterbrechungen_anzahl, laengste_unterbrechung_bloecke
        unterbrechungen_anzahl += 1
        laengste_unterbrechung_bloecke = max(laengste_unterbrechung_bloecke, lauf_laenge)
    return index, unterbrechungen_anzahl, laengste_unterbrechung_bloecke


def finde_stillevorlauf(
    media_path: Path,
    mark_ms: int,
    candidate_end_ms: int,
    *,
    ffmpeg_path: Path,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> StilleVorlauf:
    """Liegt vor dem ersten Ton lange Stille, wird ``mark_ms`` nach vorn geschoben.

    Auftrag shorts-stillevorlauf - siehe Moduldoc-Verweis bei
    :data:`VORLAUF_SUCHE_MAX_MS` fuer die Ursache, die dieser Schritt behebt.

    Der SPRECHPEGEL wird einmal ueber die GANZE Kandidatenspanne
    ``[mark_ms, candidate_end_ms)`` bestimmt - energetisches Mittel der LAUTEREN
    HAELFTE aller gemessenen 40-ms-Ausschnitte (siehe :attr:`StilleVorlauf.sprechpegel_db`),
    NICHT aus dem Suchfenster, sonst waere der Bezug bei durchgehender Stille
    sinnlos (ein Fenster, das selbst nur aus Stille besteht, haette dann "sich
    selbst" als Sprechpegel).

    Danach wird ab ``mark_ms`` vorwaerts gemessen, in denselben 10-ms-Bloecken
    wie :func:`verschiebe_auf_leiseste_stelle`, hoechstens
    :data:`VORLAUF_SUCHE_MAX_MS`. Als Stille zaehlt eine Stelle, deren Pegel
    mehr als :data:`STILLE_ABSTAND_DB` unter dem Sprechpegel liegt. Der Bereich
    AB DER MARKE gilt als zusammenhaengend still, solange jede Unterbrechung
    (ein Lauf von Bloecken ueber der Schwelle) hoechstens
    :data:`STILLE_UNTERBRECHUNG_MAX_MS` dauert (Auftrag
    shorts-stillevorlauf-toleranz, siehe dort und :func:`_stillebereich_laenge`)
    - eine laengere Unterbrechung beendet ihn. Ist der so bestimmte Bereich
    laenger als :data:`VORLAUF_MAX_MS`, wandert die Marke an sein Ende,
    abzueglich :data:`VORLAUF_REST_MS` Anlauf. Sonst bleibt ``mark_ms``
    unveraendert - kein Fehler, kein Abbruch.

    Wirft :class:`LevelCutFailed`, wenn ffmpeg scheitert oder keine Messung
    zustandekommt - wie :func:`verschiebe_auf_leiseste_stelle` faengt der
    Aufrufer das ab und baut mit den unveraenderten Grenzen weiter.
    """
    if mark_ms < 0:
        raise LevelCutFailed("marke_negativ", f"Zeitmarke liegt vor Dateianfang: {mark_ms} ms")
    if candidate_end_ms <= mark_ms:
        raise LevelCutFailed(
            "kandidatenspanne_ungueltig",
            f"Kandidatenspanne endet nicht nach der Marke: {mark_ms}..{candidate_end_ms} ms "
            "(Stillevorlauf)",
        )

    runner = process_runner if process_runner is not None else _default_process_runner

    speech_levels = _fetch_measured_levels(
        media_path,
        fetch_start_ms=mark_ms,
        fetch_duration_ms=candidate_end_ms - mark_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Sprechpegel/Stillevorlauf",
    )
    finite_speech_levels = [level for level in speech_levels if level != -math.inf]
    if not finite_speech_levels:
        raise LevelCutFailed(
            "kein_ton",
            f"Tonspur ist ueber die ganze Kandidatenspanne stumm: {media_path} (Stillevorlauf)",
        )
    laute_haelfte = sorted(finite_speech_levels, reverse=True)
    laute_haelfte = laute_haelfte[: max(1, len(laute_haelfte) // 2)]
    sprechpegel_db = _mean_level_db(laute_haelfte)
    threshold_db = sprechpegel_db - STILLE_ABSTAND_DB

    # Der erste Ausschnitt der Vorwaertsmessung liegt mittig um mark_ms, beginnt
    # also eine halbe Ausschnittslaenge davor - wie bei verschiebe_auf_leiseste_stelle.
    search_fetch_start_ms = max(0, mark_ms - MEASURE_MS // 2)
    search_fetch_duration_ms = VORLAUF_SUCHE_MAX_MS + MEASURE_MS
    search_levels = _fetch_measured_levels(
        media_path,
        fetch_start_ms=search_fetch_start_ms,
        fetch_duration_ms=search_fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Vorwaertssuche/Stillevorlauf",
    )

    run_len, unterbrechungen_anzahl, laengste_unterbrechung_bloecke = _stillebereich_laenge(
        search_levels, threshold_db
    )
    # Die Vorwaertsmessung liefert (wegen der mittig liegenden Ausschnitte) einen
    # Messpunkt mehr als VORLAUF_SUCHE_MAX_MS // STEP_MS - gedeckelt, damit die
    # Suchweite nie ueber VORLAUF_SUCHE_MAX_MS hinausgeht (siehe Moduldoc "hoechstens").
    stille_laenge_ms = min(run_len * STEP_MS, VORLAUF_SUCHE_MAX_MS)
    laengste_unterbrechung_ms = laengste_unterbrechung_bloecke * STEP_MS

    if stille_laenge_ms > VORLAUF_MAX_MS:
        corrected_ms = mark_ms + stille_laenge_ms - VORLAUF_REST_MS
        return StilleVorlauf(
            original_ms=mark_ms,
            corrected_ms=corrected_ms,
            shift_ms=corrected_ms - mark_ms,
            verschoben=True,
            sprechpegel_db=sprechpegel_db,
            stille_laenge_ms=stille_laenge_ms,
            unterbrechungen_anzahl=unterbrechungen_anzahl,
            laengste_unterbrechung_ms=laengste_unterbrechung_ms,
        )
    return StilleVorlauf(
        original_ms=mark_ms,
        corrected_ms=mark_ms,
        shift_ms=0,
        verschoben=False,
        sprechpegel_db=sprechpegel_db,
        stille_laenge_ms=stille_laenge_ms,
        unterbrechungen_anzahl=unterbrechungen_anzahl,
        laengste_unterbrechung_ms=laengste_unterbrechung_ms,
    )


def verschiebe_auf_leiseste_stelle(
    media_path: Path,
    mark_ms: int,
    *,
    ffmpeg_path: Path,
    nur_rueckwaerts: bool = False,
    search_window_start_ms: int | None = None,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> LevelSnap:
    """Schiebe ``mark_ms`` auf die leiseste Stelle in ihrer unmittelbaren Umgebung.

    Gemessen wird in ``[mark_ms - SEARCH_WINDOW_MS, mark_ms + SEARCH_WINDOW_MS]``
    in Schritten von :data:`STEP_MS`, je Schritt ueber einen mittig liegenden
    Ausschnitt von :data:`MEASURE_MS`. Liegt die Marke so nah am Dateianfang,
    dass das Fenster darueber hinausreichen wuerde, wird bei 0 ms begonnen und
    entsprechend weniger nach vorn gesucht; am Dateiende endet die Suche dort,
    wo der Ton endet.

    Ist ``nur_rueckwaerts`` gesetzt, verengt sich das Fenster auf
    ``[mark_ms - fenster, mark_ms]`` - fuer die STARTgrenze eines Kandidaten,
    wo eine Korrektur nach spaeter das erste Wort anschneidet. Frueher
    anfangen ist dort harmlos, spaeter anfangen nicht (siehe Moduldoc). Die
    rueckwaertige Suchweite ist dann :data:`SEARCH_WINDOW_START_MS`, sofern
    ``search_window_start_ms`` nicht ausdruecklich einen anderen Wert setzt
    (fuer den Pruefstein, der mehrere Werte gegeneinander misst, ohne den
    Aufrufer zweimal zu aendern). Die ENDgrenze bleibt bei
    ``nur_rueckwaerts=False`` (Vorgabe) unveraendert in beide Richtungen offen,
    mit Suchweite :data:`SEARCH_WINDOW_MS`; ``search_window_start_ms`` wirkt
    dort nicht.

    Wirft :class:`LevelCutFailed`, wenn ffmpeg scheitert oder das Fenster keine
    einzige vollstaendige Messung hergibt. KEIN Rueckfall auf ``mark_ms``.
    """
    if mark_ms < 0:
        raise LevelCutFailed("marke_negativ", f"Zeitmarke liegt vor Dateianfang: {mark_ms} ms")

    runner = process_runner if process_runner is not None else _default_process_runner

    if nur_rueckwaerts:
        window_before_ms = (
            search_window_start_ms
            if search_window_start_ms is not None
            else SEARCH_WINDOW_START_MS
        )
    else:
        window_before_ms = SEARCH_WINDOW_MS
    window_after_ms = 0 if nur_rueckwaerts else SEARCH_WINDOW_MS

    # Der erste 40-ms-Ausschnitt liegt mittig um (mark - window_before_ms),
    # beginnt also eine halbe Ausschnittslaenge davor.
    fetch_start_ms = max(0, mark_ms - window_before_ms - MEASURE_MS // 2)
    fetch_duration_ms = window_before_ms + window_after_ms + MEASURE_MS

    arguments = [
        str(ffmpeg_path),
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{fetch_start_ms / 1000:.3f}",
        "-t",
        f"{fetch_duration_ms / 1000:.3f}",
        "-i",
        str(media_path),
        "-map",
        "0:a:0",
        "-af",
        _filter_chain(),
        "-f",
        "null",
        "-",
    ]
    result = runner(arguments, timeout_seconds)
    if result.exit_code != 0:
        raise LevelCutFailed(
            "ffmpeg_fehlgeschlagen",
            f"ffmpeg endete mit Code {result.exit_code} fuer {media_path} bei {mark_ms} ms",
        )

    block_levels = _parse_block_levels(result.stdout)
    if len(block_levels) < _BLOCKS_PER_MEASURE:
        raise LevelCutFailed(
            "keine_messung",
            f"kein messbarer Ton bei {mark_ms} ms in {media_path} "
            f"({len(block_levels)} Bloecke, {_BLOCKS_PER_MEASURE} noetig)",
        )

    levels = _combine_to_measure_window(block_levels)
    if all(level == -math.inf for level in levels):
        raise LevelCutFailed(
            "kein_ton",
            f"Tonspur ist im Fenster um {mark_ms} ms durchgehend stumm: {media_path}",
        )

    chosen, verfahren, quiet_region_ms = _choose_cut_point(levels)
    # Stelle i misst mittig um (fetch_start + i*STEP + MEASURE_MS/2).
    corrected_ms = fetch_start_ms + chosen * STEP_MS + MEASURE_MS // 2
    return LevelSnap(
        original_ms=mark_ms,
        corrected_ms=corrected_ms,
        shift_ms=corrected_ms - mark_ms,
        level_db=levels[chosen],
        window_mean_db=_mean_level_db(levels),
        verfahren=verfahren,
        quiet_region_ms=quiet_region_ms,
    )
