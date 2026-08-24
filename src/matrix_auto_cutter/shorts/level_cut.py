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

VERFAHREN_WORTGRENZE_STEHT = "wortgrenze_steht"
"""Auftrag shorts-pegel-wortgrenze: ``such_min_ms``/``such_max_ms`` liessen kein
gueltiges Suchfenster (leer oder zu schmal fuer eine Messstelle) - die Marke
bleibt unveraendert stehen. Kein Ausweichen, keine Naeherung."""

VERFAHREN_WORT_EINSATZ = "wort_einsatz"
"""Auftrag shorts-pegel-wortgrenze, TEIL 3: die Startgrenze wurde auf den am
Ton gemessenen Worteinsatz (:func:`finde_worteinsatz_ton`) vorgerueckt - nicht
von der leiseste-Stelle-Suche selbst gewaehlt (siehe Aufrufstelle in
``build._apply_level_correction``: der Einsatz gewinnt, wenn er spaeter liegt
als das Ergebnis der rueckwaertigen leiseste-Stelle-Suche)."""

VERFAHREN_LAUTENDE_STEHT = "lautende_steht"
"""Auftrag shorts-endgrenze-schranke, TEIL 1: das Fenster zwischen dem
gemessenen Lautende und dem whisper-Anfang des naechsten Wortes ist leer oder
negativ (das naechste Wort folgt ohne messbare Pause) - die Endgrenze bleibt
am gemessenen Lautende stehen. Kein Suchen, kein Fallback, keine Naeherung
darueber hinaus (siehe Aufrufstelle in ``build._apply_level_correction``)."""

VERFAHREN_FENSTER_AUSGESCHOEPFT = "fenster_ausgeschoepft"
"""Auftrag shorts-endgrenze-schranke, TEIL 2: der Mindestnachklang
(:data:`MIN_NACHKLANG_MS`) passt nicht mehr in das Fenster aus TEIL 1
(``[gemessenes Lautende, whisper-Anfang des naechsten Wortes]``) - die
Endgrenze wandert bis an das obere Ende dieses Fensters (unmittelbar vor das
naechste Wort), nicht darueber hinaus (siehe Aufrufstelle in
``build._apply_level_correction``)."""

VERFAHREN_WORTRAND_KOLLISION = "wortrand_kollision"
"""Auftrag shorts-wortrand-abstand, TEIL 2: der noetige Sicherheitsabstand
(:data:`WORTRAND_ABSTAND_ENDE_MS`/:data:`WORTRAND_ABSTAND_ANFANG_MS`) passt
nicht zwischen das wahre Wortende des eigenen Wortes und den wahren Anfang
des Nachbarworts (oder umgekehrt an der Startgrenze) - die Grenze liegt dann
auf der Mitte zwischen beiden wahren Wortraendern. Kein Fallback auf eine
Whisper-Marke (siehe Aufrufstelle in ``build._apply_level_correction``)."""

VERFAHREN_TONBLENDE_GROSSZUEGIG = "tonblende_grosszuegig"
"""Auftrag shorts-tonblende: die Grenze wurde DIREKT auf den grosszuegigen
Wert gesetzt (``own_true_end_ms + MIN_NACHKLANG_MS`` an der Endgrenze,
``own_true_start_ms - chart_crop.TON_EINBLENDE_MS`` an der Startgrenze,
jeweils am wahren Rand des Nachbarworts gedeckelt) statt wie zuvor die
leiseste Stelle im Fenster zu SUCHEN (:func:`verschiebe_auf_leiseste_stelle`).
Moeglich, weil die neue Ton-Ein-/Ausblende
(``chart_crop.TON_EINBLENDE_MS``/``chart_crop.TON_AUSBLENDE_MS``) den
Uebergang jetzt selbst weich macht - drei Fassungen der reinen Punktsuche
klangen trotzdem angeschnitten (siehe :data:`chart_crop.TON_EINBLENDE_MS`),
deshalb darf das Wort bewusst ganz in der gebauten Spanne bleiben, statt den
Schnitt weiter vor ihm zu suchen. Keine neue Messung - nur eine andere Wahl
innerhalb der schon gemessenen Wortraender (siehe Aufrufstelle in
``build._apply_level_correction``)."""


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


def _filter_chain(*, hochband: bool = False) -> str:
    """Der Filtergraph: ein Band, 10-ms-Bloecke, RMS je Block auf stdout.

    EIN ffmpeg-Aufruf misst damit das ganze Fenster. Aus nicht ueberlappenden
    10-ms-Bloecken laesst sich der 40-ms-Wert an jeder 10-ms-Stelle exakt
    zusammensetzen (siehe :func:`_combine_to_measure_window`), weil sich
    Leistungen addieren.

    ``hochband=True`` (Auftrag shorts-pegel-wortgrenze, Nachtrag N1) misst
    statt des Sprachbands (Vorgabe, 200-3400 Hz - fuer die leiseste-Stelle-
    Suche unveraendert) alles OBERHALB :data:`SPEECH_BAND_LOWPASS_HZ` - das
    Band, das Reibe- und Verschlusslaute traegt, die das Sprachband allein
    kaum sieht (Befund, Abschnitt 1.6/1.8: der Reibelaut von kandidat-01
    steht im Sprachband bei nur -30 bis -39 dB, im Hochband deutlich hoeher).
    Nur :func:`finde_wortende_ton` und :func:`finde_worteinsatz_ton` nutzen
    beide Baender (je Block das Maximum, siehe :func:`_fetch_zweiband_levels`)
    - die leiseste-Stelle-Suche selbst bleibt unveraendert einbaendig.
    """
    band = (
        f"highpass=f={SPEECH_BAND_LOWPASS_HZ}"
        if hochband
        else f"highpass=f={SPEECH_BAND_HIGHPASS_HZ},lowpass=f={SPEECH_BAND_LOWPASS_HZ}"
    )
    return (
        f"aresample={MEASURE_SAMPLE_RATE},"
        f"{band},"
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
    hochband: bool = False,
) -> list[float]:
    """Miss die 40-ms-Pegel im 10-ms-Raster ueber ``[fetch_start_ms, +fetch_duration_ms)``.

    Gemeinsamer Kern fuer die Sprechpegel- und die Vorwaertsmessung in
    :func:`finde_stillevorlauf` - dieselbe Filterkette wie
    :func:`verschiebe_auf_leiseste_stelle`. ``context`` geht nur in die
    Fehlermeldung ein, damit sich Sprechpegel- von Vorlauf-Fehlern unterscheiden
    lassen. ``hochband`` reicht an :func:`_filter_chain` durch (Auftrag
    shorts-pegel-wortgrenze, Nachtrag N1) - Vorgabe unveraendert das Sprachband.
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
        _filter_chain(hochband=hochband),
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


def _fetch_zweiband_levels(
    media_path: Path,
    *,
    fetch_start_ms: int,
    fetch_duration_ms: int,
    ffmpeg_path: Path,
    timeout_seconds: int,
    process_runner: ProcessRunner,
    context: str,
) -> list[float]:
    """Wie :func:`_fetch_measured_levels`, aber je Block das Maximum.

    Sprachband und Hochband (Auftrag shorts-pegel-wortgrenze, Nachtrag N1) -
    fuer :func:`finde_wortende_ton` und :func:`finde_worteinsatz_ton`, NICHT
    fuer die leiseste-Stelle-Suche (siehe :func:`_filter_chain`).
    """
    sprachband = _fetch_measured_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=process_runner,
        context=f"{context}/Sprachband",
        hochband=False,
    )
    hochband = _fetch_measured_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=process_runner,
        context=f"{context}/Hochband",
        hochband=True,
    )
    laenge = min(len(sprachband), len(hochband))
    return [max(sprachband[i], hochband[i]) for i in range(laenge)]


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


WORTENDE_SUCHE_MS = 150
"""Fenster (je Richtung um eine Whisper-Wortmarke) fuer :func:`finde_wortende_ton`
(Auftrag shorts-pegel-wortgrenze, Nachtrag N1).

Ersetzt die alte, rein an Whisper gemessene Pause als Schranke der
Pegelkorrektur: Whispers Marken sind eine Naeherung (Befund
``shorts-bau-21-08-befund/BERICHT-2026-08-22.md``, Abschnitt 1.7 - Wortanfaenge
liegen 35-65 ms zu frueh), deshalb bestimmt dieses Modul das tatsaechliche
Lautende/den tatsaechlichen Ausklang selbst am Ton, in einem Fenster von
150 ms um die Whisper-Marke."""

WORTENDE_FALLBACK_MS = 65
"""Konservative Untergrenze, wenn :func:`finde_wortende_ton` auch innerhalb von
:data:`WORTENDE_SUCHE_MAX_MS` keinen dauerhaften Uebergang von laut zu leise
findet (Nachtrag N1) - dieselbe Zahl wie die OBERE gemessene Frueh-Abweichung
der Wortanfaenge (Befund, Abschnitt 1.7: 35-65 ms), hier konservativ als
"mindestens so lange noch Ton" auf die Endgrenze uebertragen. Kein
Geschmackswert."""

WORTENDE_SUCHE_MAX_MS = 800
"""Sicherheitsdeckel der Vorwaertssuche in :func:`finde_wortende_ton`, falls
der Ton ueber :data:`WORTENDE_SUCHE_MS` hinaus laut bleibt.

Die reine Whisper-Marke ist in durchlaufender Rede keine verlaessliche
Messgroesse (Befund, Abschnitt 1.7): bei kandidat-04 liegt das tatsaechliche
Lautende 400 ms, bei kandidat-06 300 ms nach der Marke - beides deutlich
ueber :data:`WORTENDE_SUCHE_MS`. Die Suche folgt dem lauten Lauf deshalb bei
Bedarf weiter, bis maximal 800 ms - reichlich Sicherheitsabstand ueber der
groessten im Befund gemessenen Abweichung (400 ms), dieselbe Groessenordnung
wie der Sicherheitsdeckel des Stillevorlaufs (:data:`VORLAUF_SUCHE_MAX_MS`).
Erst danach greift :data:`WORTENDE_FALLBACK_MS`."""

START_EINSATZ_SUCHE_MS = 120
"""Hoechste Vorwaertsverschiebung der Startgrenze auf den tatsaechlichen
Lauteinsatz des ersten enthaltenen Wortes (Auftrag shorts-pegel-wortgrenze,
TEIL 3/Nachtrag N1). Stellwert, hergeleitet aus der gemessenen systematischen
Frueh-Abweichung der whisper-Wortanfaenge von 35 bis 65 ms (Befund, Abschnitt
1.7), mit Sicherheitsabstand - kein Geschmackswert."""

EINSATZ_BESTAETIGUNG_MS = 30
"""Mindestlaenge eines durchgehend lauten Laufs, damit :func:`finde_worteinsatz_ton`
ihn als echten Worteinsatz gelten laesst statt als kurzes Stoergeraeusch
(Klick, Atmer) - deutlich kuerzer als jedes gesprochene Wort, aber laenger als
ein einzelner 10-ms-Ausreisser."""

WORTENDE_UNTERBRECHUNG_MAX_MS = 20
"""Toleranz fuer :func:`finde_wortende_ton`, um einzelne Rauschausreisser NICHT
schon als Lautende zu werten - bewusst NICHT :data:`STILLE_UNTERBRECHUNG_MAX_MS`
(120 ms): jener Wert ist fuer den Stillevorlauf kalibriert, wo echte Pausen
hunderte ms bis Sekunden dauern und kurze Betonungsspitzen ueberbrueckt werden
muessen. An einer Wortgrenze sind die echten Pausen selbst kurz (Befund,
Tabelle 1.2: 0-170 ms) - eine 120-ms-Toleranz wuerde genau diese echten,
kurzen Pausen ueberbruecken und die Suche in den naechsten Satz hineinlaufen
lassen (gemessen bei kandidat-00: die echte 40-ms-Pause nach "richtig?" wird
mit 120 ms Toleranz uebersprungen, das Lautende landet 420 ms spaeter im
naechsten Satz). 20 ms liegt klar unter den gemessenen echten Pausen (kuerzeste
40 ms) und klar ueber einzelnen 10-ms-Rauschbloecken."""

assert WORTENDE_SUCHE_MS % STEP_MS == 0, "WORTENDE_SUCHE_MS muss ein Vielfaches von STEP_MS sein"
# WORTENDE_FALLBACK_MS (65) ist bewusst KEIN Vielfaches von STEP_MS: es ist ein
# Additionsterm auf eine whisper-Millisekundenmarke, die selbst nicht auf dem
# 10-ms-Messraster liegt - keine Rasterbedingung noetig.
assert START_EINSATZ_SUCHE_MS % STEP_MS == 0, (
    "START_EINSATZ_SUCHE_MS muss ein Vielfaches von STEP_MS sein"
)
assert EINSATZ_BESTAETIGUNG_MS % STEP_MS == 0, (
    "EINSATZ_BESTAETIGUNG_MS muss ein Vielfaches von STEP_MS sein"
)
assert WORTENDE_SUCHE_MAX_MS % STEP_MS == 0, (
    "WORTENDE_SUCHE_MAX_MS muss ein Vielfaches von STEP_MS sein"
)
assert WORTENDE_SUCHE_MAX_MS >= WORTENDE_SUCHE_MS, (
    "WORTENDE_SUCHE_MAX_MS muss mindestens WORTENDE_SUCHE_MS sein"
)
assert WORTENDE_UNTERBRECHUNG_MAX_MS % STEP_MS == 0, (
    "WORTENDE_UNTERBRECHUNG_MAX_MS muss ein Vielfaches von STEP_MS sein"
)

MIN_NACHKLANG_MS = 60
"""Auftrag shorts-endgrenze-schranke, TEIL 2: Mindestabstand zwischen dem
gemessenen Lautende und der tatsaechlich geschnittenen Endgrenze.

Ein Wort, das im selben Augenblick endet wie das Video, wirkt beim Sprung in
die Schleife abgeschnitten, auch wenn es vollstaendig ist - der Ohreindruck
braucht etwas Luft NACH dem letzten Laut, nicht nur einen Schnitt, der ihn
gerade eben nicht mehr anschneidet. Am Nutzerurteil gemessen (Bericht
``shorts-kandidat-00-endgrenze/BERICHT-2026-08-23.md``): kandidat-00 hatte
nur 10 ms Nachklang (Lautende 45870, gebaute Grenze 45880) und klang
abgehackt; kandidat-04 (Lautende 120390, Grenze 120480, 90 ms) und
kandidat-08 (Lautende 347145, Grenze 347300, 155 ms) klangen beide gut. 60 ms
liegt klar ueber dem beanstandeten Wert (10 ms) und klar unter den beiden
gebilligten (90/155 ms) - die Mitte zwischen "zu wenig" und "beobachtet
ausreichend", nicht deren Obergrenze.

Die Schranke aus TEIL 1 (:data:`VERFAHREN_LAUTENDE_STEHT`/
:data:`VERFAHREN_FENSTER_AUSGESCHOEPFT`) gewinnt immer: passt der
Mindestnachklang nicht mehr in das Fenster bis zum naechsten Wort, wird das
Fenster nur bis zu dessen Anfang ausgeschoepft, nie darueber hinaus (siehe
Aufrufstelle in ``build._apply_level_correction``)."""

PAUSENGRUND_ABSTAND_DB = QUIET_REGION_DEPTH_DB
"""Auftrag shorts-wortrand-abstand, TEIL 1: wie tief ein Bereich unter dem
Fenstermittel liegen muss, um als echte Pause (statt Wortrand) zu gelten -
dieselbe Konstante wie :data:`QUIET_REGION_DEPTH_DB` (6 dB), hier fuer das
Pausengrund-Verfahren wiederverwendet statt neu erfunden."""

PAUSENGRUND_MIN_PAUSE_ENDE_MS = 80
"""Mindestlaenge, ab der ein leiser Bereich nach einem Wortende als echte
Pause zaehlt (Auftrag shorts-wortrand-abstand, :func:`finde_wortrand_ende`) -
etwas kuerzer als :data:`MIN_PAUSE_MS` (100 ms), weil hier per Definition
bereits NACH dem Wortende gesucht wird (keine Lautluecken IM Wort mehr
moeglich, die abzugrenzen waeren)."""

PAUSENGRUND_MIN_PAUSE_ANFANG_MS = 30
"""Mindestlaenge, ab der ein leiser Bereich vor einem Wortanfang als echte
Pause zaehlt (:func:`finde_wortrand_anfang`) - kuerzer als bei Wortenden
(:data:`PAUSENGRUND_MIN_PAUSE_ENDE_MS`), weil die Ruecksuche bewusst eng
gefuehrt wird (siehe dort) und ein Wortanfang typischerweise schneller
anschwillt, als ein Wortende ausklingt."""

WORTRAND_SUCHE_ENDE_MS = WORTENDE_SUCHE_MAX_MS
"""Suchweite vorwaerts fuer :func:`finde_wortrand_ende` - dieselbe wie die
erweiterte Suche von :func:`finde_wortende_ton` (800 ms), damit beide
Verfahren denselben Bereich abdecken."""

WORTRAND_SUCHE_ANFANG_RUECK_MS = WORTENDE_SUCHE_MS
"""Suchweite rueckwaerts fuer :func:`finde_wortrand_anfang` (150 ms) -
dieselbe wie der Kontext von :func:`finde_wortende_ton`/
:func:`finde_worteinsatz_ton`."""

WORTRAND_SUCHE_ANFANG_VOR_MS = 60
"""Suchweite vorwaerts (ueber die Whisper-Marke hinaus) fuer
:func:`finde_wortrand_anfang` - bewusst eng: eine breitere Vorwaertssuche
lief bei kurzen Woertern (Befund unten, Fall "Boden"/"der") regelmaessig in
die Pause NACH dem Wort statt in dessen eigenen, kurzen Anlauf."""

WORTRAND_ABSTAND_ENDE_MS = 390
"""Auftrag shorts-wortrand-abstand, TEIL 2: Sicherheitsabstand zwischen dem
per Pausengrund-Verfahren gemessenen wahren Wortende
(:func:`finde_wortrand_ende`) und der fruehesten erlaubten Endgrenze.

Aus TEIL 1 abgeleitet, auf das gemessene MAXIMUM aufgerundet (nicht den
Median - ein zu kurzer Abstand schneidet hoerbar an, ein zu langer kostet
nur Stille). Messreihe (Zweiband, 10-ms-Raster, Aufnahme
2026-08-21 10-46-08), Differenz = wahres Wortende minus heutige Schwelle:

Zehn Wortenden mit Whisper-Pause > 200 ms (Median -47, Min -400, Max 425 -
zwei davon, "der" und "Numerologie", erwiesen sich als durch ein
dazwischenliegendes, von Whisper nicht erkanntes drittes Wort verunreinigt
und tragen NICHT zum Maximum bei, siehe Bericht TEIL 1):
Gedanken -40, aus -65, das -55, der +425 (verunreinigt), Numerologie +365
(verunreinigt), den -80, totally -40, Boden +485 (verunreinigt), na +240,
hier -10.

Die vier konkreten Faelle des Nutzers, hier massgeblich (kein
Whisper-Pause, exakt der Fall, den TEIL 2 behebt):
"klarer." (kandidat-04): heutig 120000, wahr 120390, **+390**.
"richtig?" (kandidat-00, spaetere Fassung): heutig 46000, wahr 46230, +230.

**390 ms ist das Maximum der unverunreinigten Messungen** (die drei
verunreinigten Ausreisser bei "der"/"Numerologie"/"Boden" sind keine echten
Wortraender, sondern ein Messfehler durch eine von Whisper uebersehene
Pause NACH einem dazwischenliegenden Wort - siehe Bericht, TEIL 1)."""

WORTRAND_ABSTAND_ANFANG_MS = 130
"""Auftrag shorts-wortrand-abstand, TEIL 2: Sicherheitsabstand zwischen dem
per Pausengrund-Verfahren gemessenen wahren Wortanfang
(:func:`finde_wortrand_anfang`) und der spaetesten erlaubten Startgrenze.

Messreihe (Differenz = heutige Schwelle minus wahrer Anfang): zehn
Wortanfaenge mit Whisper-Pause > 200 ms (Median 35, Min -60, Max 130):
diesen +10, Angst -30, gekommen -20, eingesehen -40, diese -50, oben +110,
und +60, die +90, das +130, damit -60.

Die zwei konkreten Faelle: "Na," (kandidat-00): heutig 31000 (whisper-Marke
selbst, kein Vorruecken durch die heutige Suche), wahr 30940, **+130** (auf
den tatsaechlich GEBAUTEN Wert 31070 bezogen: +130). "Nachdem" (kandidat-01,
gilt als gut): heutig 63090 (gebauter Wert), wahr 63060, nur +30 - bestaetigt
den Nutzereindruck.

**130 ms ist das Maximum** - Generik und "Na"-Fall stimmen ueberein, keine
Verunreinigung wie bei den Wortenden festgestellt (die engere Ruecksuche
von :data:`WORTRAND_SUCHE_ANFANG_VOR_MS` verhindert das)."""

assert WORTRAND_ABSTAND_ENDE_MS % STEP_MS == 0, (
    "WORTRAND_ABSTAND_ENDE_MS muss ein Vielfaches von STEP_MS sein"
)
assert WORTRAND_ABSTAND_ANFANG_MS % STEP_MS == 0, (
    "WORTRAND_ABSTAND_ANFANG_MS muss ein Vielfaches von STEP_MS sein"
)

assert MIN_NACHKLANG_MS % STEP_MS == 0, "MIN_NACHKLANG_MS muss ein Vielfaches von STEP_MS sein"

NACHBARRAND_SUCHE_MS = 250
"""Auftrag shorts-nachbarrand: Suchweite fuer :func:`finde_nachbarrand_einsatz`/
:func:`finde_nachbarrand_ausklang`, wenn ``pause_after_ms``/``pause_before_ms``
an einer Kandidatengrenze null oder negativ ist.

Bei einer Nullpause liegt die whisper-Marke des Nachbarworts (``next_word_start_ms``/
``prev_word_end_ms``) exakt auf der eigenen, noch ungekorrigierten Wortgrenze -
:func:`finde_wortrand_anfang`/:func:`finde_wortrand_ende`, um diese Marke
herum gesucht, trifft dann oft das eigene Wortende/den eigenen Wortanfang
statt die tatsaechliche Pause zum Nachbarn (siehe Auftragsbeschreibung
shorts-nachbarrand). Diese beiden Funktionen suchen deshalb unabhaengig vom
schon gemessenen EIGENEN wahren Wortrand aus weiter - 250 ms ist dieselbe
Groessenordnung wie :data:`SEARCH_WINDOW_MS`, reichlich fuer eine echte
Sprechpause, aber eng genug, um nicht in ein weiteres, dazwischenliegendes
Wort hineinzulaufen."""

assert NACHBARRAND_SUCHE_MS % STEP_MS == 0, (
    "NACHBARRAND_SUCHE_MS muss ein Vielfaches von STEP_MS sein"
)


def _sprechpegel_aus_fenster(finite_levels: Sequence[float]) -> float:
    """Sprechpegel eines (kleinen) Fensters.

    Energetisches Mittel der lauteren Haelfte aller endlichen Pegel -
    dieselbe Definition wie in :func:`finde_stillevorlauf` (dort mit voller
    Begruendung), hier auf ein enges Wortgrenzenfenster angewandt.
    """
    laute_haelfte = sorted(finite_levels, reverse=True)
    laute_haelfte = laute_haelfte[: max(1, len(laute_haelfte) // 2)]
    return _mean_level_db(laute_haelfte)


def _folge_bis_wechsel(
    levels: Sequence[float],
    start_index: int,
    threshold_db: float,
    *,
    ab_index_laut: bool,
    toleranz_bloecke: int,
) -> int | None:
    """Suche ab ``start_index`` den ersten DAUERHAFTEN Wechsel in den anderen Zustand.

    Verallgemeinert :func:`_stillebereich_laenge` auf beide Richtungen: laut
    zu leise fuer Lautende/Ausklang (:func:`finde_wortende_ton`), leise zu
    laut fuer den Worteinsatz (:func:`finde_worteinsatz_ton`). Kurze
    Ausreisser bis ``toleranz_bloecke`` im jeweils anderen Zustand werden
    ueberbrueckt, wie dort.

    Gibt den Index des ERSTEN Blocks im neuen, dauerhaften Zustand zurueck -
    das ist genau die gesuchte Grenze (erster leiser Block = Lautende, erster
    lauter Block = Worteinsatz) - oder ``None``, wenn kein dauerhafter Wechsel
    innerhalb der Liste liegt.
    """
    index = start_index
    while index < len(levels):
        is_loud = levels[index] >= threshold_db
        if is_loud == ab_index_laut:
            index += 1
            continue
        lauf_start = index
        while index < len(levels) and (levels[index] >= threshold_db) != ab_index_laut:
            index += 1
        if index - lauf_start > toleranz_bloecke:
            return lauf_start
    return None


def finde_wortende_ton(
    media_path: Path,
    whisper_end_ms: int,
    *,
    ffmpeg_path: Path,
    erweiterte_suche: bool = True,
    ober_grenze_ms: int | None = None,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int:
    """Bestimme das tatsaechliche Lautende/den tatsaechlichen Ausklang am Ton.

    Auftrag shorts-pegel-wortgrenze, Nachtrag N1. Dient zwei Zwecken mit
    demselben Verfahren: als Untergrenze der ENDgrenze eines Kandidaten
    (``whisper_end_ms`` = Whisper-Endmarke des letzten enthaltenen Wortes,
    ``erweiterte_suche=True``, Vorgabe) und als Untergrenze der STARTgrenze
    (``whisper_end_ms`` = Whisper-Endmarke des VORIGEN Wortes - "der gemessene
    Ausklang des Vorgaengerwortes", ``erweiterte_suche=False``).

    ``erweiterte_suche=False`` deckelt die Vorwaertssuche auf
    :data:`WORTENDE_SUCHE_MS` (keine Verlaengerung bis
    :data:`WORTENDE_SUCHE_MAX_MS`): geht die Suche als Ausklang des VORIGEN
    Wortes in eine Kandidatenspanne OHNE Pause hinein (Regelfall, Befund
    Tabelle 1.2: 9 von 12 Grenzen 0 ms), gibt es dort keinen "Ausklang" im
    eigentlichen Sinn - das naechste Wort schliesst nahtlos an, und die neue
    Rede laeuft ungebremst weiter. Eine unbeschraenkte Suche wuerde dann nicht
    "den Ausklang", sondern irgendeine spaetere, thematisch fremde Pause
    finden (gemessen bei kandidat-01: 470 ms nach der Marke, mitten im
    naechsten Satz) - kein sinnvoller Bezug fuer die Startgrenze. Fuer die
    ENDgrenze eines Kandidaten (``erweiterte_suche=True``) gilt das nicht:
    dort IST das gesuchte Lautende per Definition irgendwo nach der Marke,
    auch wenn es (kandidat-04, kandidat-06) mehrere hundert ms entfernt liegt.

    Verfahren (im Zweiband-Maximum aus Sprach- und Hochband, siehe
    :func:`_fetch_zweiband_levels` - das Sprachband allein uebersieht
    Reibe-/Verschlusslaute am Wortrand, Befund Abschnitt 1.8, dieselben
    10-ms-Bloecke/40-ms-Ausschnitte wie :func:`verschiebe_auf_leiseste_stelle`):
    der Sprechpegel des Fensters ``[whisper_end_ms - WORTENDE_SUCHE_MS,
    whisper_end_ms + WORTENDE_SUCHE_MS]`` wird als energetisches Mittel der
    lauteren Haelfte bestimmt (wie :func:`finde_stillevorlauf`), die Schwelle
    liegt :data:`STILLE_ABSTAND_DB` darunter. Steht die Whisper-Marke selbst
    schon unter der Schwelle, ist der Ton dort bereits leise - keine
    Korrektur noetig, die Marke selbst gilt als Lautende. Steht sie darueber
    (der Regelfall), wird vorwaerts verfolgt, bis der Pegel DAUERHAFT (laenger
    als :data:`WORTENDE_UNTERBRECHUNG_MAX_MS`, kurze Aussetzer werden
    ueberbrueckt - siehe dort, warum bewusst NICHT die groessere Toleranz des
    Stillevorlaufs) unter die Schwelle faellt; der erste leise Block dieses
    dauerhaften Uebergangs ist das Lautende. Bleibt der Ton ueber
    :data:`WORTENDE_SUCHE_MS` hinaus laut
    (durchlaufende Rede ohne Pause an der Whisper-Marke, Befund Abschnitt
    1.7), folgt die Suche dem lauten Lauf weiter, hoechstens bis
    :data:`WORTENDE_SUCHE_MAX_MS` - genau der Fall bei kandidat-04 (reale
    Pause 400 ms nach der Marke) und kandidat-06 (300 ms).

    Findet sich auch innerhalb von :data:`WORTENDE_SUCHE_MAX_MS` kein
    dauerhafter Uebergang, gilt ``whisper_end_ms + WORTENDE_FALLBACK_MS`` als
    konservative Untergrenze - kein Ausweichen, keine Naeherung ueber diesen
    Stellwert hinaus.

    ``ober_grenze_ms`` (Auftrag shorts-endgrenze-schranke, TEIL 1): deckelt
    JEDES Ergebnis dieser Funktion (die drei Faelle oben UND den Fallback) auf
    hoechstens diesen Wert - unabhaengig davon, was die Suche selbst
    gemessen haette. Ohne diesen Deckel kann die Suche (bei kandidat-06 belegt:
    ``pause_after_ms=0``) ungebremst in den naechsten Satz hineinlaufen und
    dort eine spaete, thematisch fremde Stille finden (271090, mitten im Wort
    "nichts" - Befund ``shorts-kandidat-00-endgrenze/BERICHT-2026-08-23.md``).
    Gedacht fuer den whisper-Anfang des naechsten, nicht mehr enthaltenen
    Wortes: das Lautende eines Kandidaten darf nie hinter diesem Anfang
    liegen, ganz gleich, was am Ton noch an spaeterer Stille zu finden waere.
    ``None`` (Vorgabe) laesst die Suche unveraendert unbeschraenkt - der alte
    Aufrufweg bleibt exakt erhalten.

    Gibt IMMER einen Wert zurueck (nie ``None``) - eine fehlgeschlagene
    Tonmessung wirft weiterhin :class:`LevelCutFailed`, wie im ganzen Modul.
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    vorwaerts_ms = WORTENDE_SUCHE_MAX_MS if erweiterte_suche else WORTENDE_SUCHE_MS
    fetch_start_ms = max(0, whisper_end_ms - WORTENDE_SUCHE_MS - MEASURE_MS // 2)
    fetch_duration_ms = WORTENDE_SUCHE_MS + vorwaerts_ms + MEASURE_MS
    levels = _fetch_zweiband_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Wortende/Ausklang",
    )

    def _gedeckelt(ergebnis_ms: int) -> int:
        return ergebnis_ms if ober_grenze_ms is None else min(ergebnis_ms, ober_grenze_ms)

    # Der Sprechpegel (Kontext fuer die Schwelle) stuetzt sich nur auf das
    # engere Kontextfenster [-WORTENDE_SUCHE_MS, +WORTENDE_SUCHE_MS] - der
    # erweiterte Sicherheitsbereich danach gehoert noch zum selben Wort und
    # wuerde den Sprechpegel nur (richtig) erhoehen, ist aber fuer die
    # Schwellenbestimmung nicht noetig.
    kontext_bloecke = 2 * WORTENDE_SUCHE_MS // STEP_MS + 1
    finite_levels = [level for level in levels[:kontext_bloecke] if level != -math.inf]
    if not finite_levels:
        return _gedeckelt(whisper_end_ms + WORTENDE_FALLBACK_MS)

    sprechpegel_db = _sprechpegel_aus_fenster(finite_levels)
    threshold_db = sprechpegel_db - STILLE_ABSTAND_DB
    anchor_index = round((whisper_end_ms - fetch_start_ms - MEASURE_MS // 2) / STEP_MS)
    anchor_index = max(0, min(len(levels) - 1, anchor_index))

    if levels[anchor_index] < threshold_db:
        # An der Whisper-Marke ist es bereits leise - kein Nachlauf noetig.
        return _gedeckelt(whisper_end_ms)

    toleranz_bloecke = WORTENDE_UNTERBRECHUNG_MAX_MS // STEP_MS
    idx = _folge_bis_wechsel(
        levels, anchor_index, threshold_db, ab_index_laut=True, toleranz_bloecke=toleranz_bloecke
    )
    if idx is None:
        return _gedeckelt(whisper_end_ms + WORTENDE_FALLBACK_MS)
    return _gedeckelt(fetch_start_ms + idx * STEP_MS + MEASURE_MS // 2)


def finde_worteinsatz_ton(
    media_path: Path,
    whisper_start_ms: int,
    *,
    ffmpeg_path: Path,
    nicht_vor_ms: int | None = None,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int:
    """Bestimme den tatsaechlichen Lauteinsatz eines Wortes am Ton (TEIL 3).

    Auftrag shorts-pegel-wortgrenze, Nachtrag N1: whisper-Wortanfaenge liegen
    systematisch 35-65 ms zu frueh (Befund, Abschnitt 1.7); diese Funktion
    sucht, hoechstens bis ``whisper_start_ms + START_EINSATZ_SUCHE_MS``, nach
    dem Punkt, an dem der Ton dauerhaft (laenger als
    :data:`EINSATZ_BESTAETIGUNG_MS`, um einzelne Stoergeraeusche nicht als
    Einsatz zu werten) ueber die Schwelle (Sprechpegel des Fensters minus
    :data:`STILLE_ABSTAND_DB`) steigt. Gemessen wird NUR im Sprachband
    (200-3400 Hz, nicht im Zweiband-Maximum wie :func:`finde_wortende_ton`):
    das Hochband traegt den ABKLINGENDEN Reibelaut des Vorgaengerwortes oft
    noch weit in den Suchbereich hinein (kandidat-01: Hochband bleibt bis
    63075 erhoeht) und wuerde die Suche dadurch gerade dort blind machen, wo
    der wahre Einsatz des NEUEN Wortes liegt - im Sprachband zeigt sich dieser
    dagegen klar (Befund, Abschnitt 1.6: Sprung von -39.7 auf -20.8 dB).

    ``nicht_vor_ms`` (typischerweise der Rueckgabewert von
    :func:`finde_wortende_ton` fuer das VORIGE Wort): die Suche beginnt
    fruehestens dort, nie vor ``whisper_start_ms``. Ohne diese Verankerung
    wuerde die Suche oft schon an der Whisper-Marke selbst abbrechen, weil
    dort noch der Ausklang des Vorgaengerwortes steht (laut genug, um die
    Schwelle zu ueberschreiten) - das waere dann faelschlich "hier ist schon
    Ton", obwohl es der FALSCHE, alte Ton ist.

    Steht die Marke, ab der gesucht wird, selbst schon ueber der Schwelle,
    ist dort bereits Ton - kein Vorruecken noetig, ``whisper_start_ms`` bleibt
    der Einsatz. Findet sich innerhalb der Suchweite kein dauerhafter Einsatz,
    bleibt ``whisper_start_ms`` selbst die Obergrenze (TEIL 3: "Findest du
    keinen klaren Einsatz ... bleibt die Grenze stehen") - keine Naeherung
    nach vorn ohne klaren Beleg am Ton.

    Der Sprechpegel (und damit die Schwelle) wird bewusst NICHT nur aus dem
    schmalen Vorwaertsfenster bestimmt, sondern wie bei
    :func:`finde_wortende_ton` aus einem um :data:`WORTENDE_SUCHE_MS`
    RUECKWAERTS erweiterten Fenster (robust auf klarer Rede vor der Marke) -
    ein rein lokales Fenster kann selbst ueberwiegend aus dem leisen
    Ausklang bestehen und liefert dann einen kuenstlich zu nachsichtigen
    Sprechpegel.

    Gibt IMMER einen Wert zurueck (nie ``None``).
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, whisper_start_ms - WORTENDE_SUCHE_MS - MEASURE_MS // 2)
    fetch_duration_ms = WORTENDE_SUCHE_MS + START_EINSATZ_SUCHE_MS + MEASURE_MS
    levels = _fetch_measured_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Worteinsatz",
    )
    finite_levels = [level for level in levels if level != -math.inf]
    if not finite_levels:
        return whisper_start_ms

    sprechpegel_db = _sprechpegel_aus_fenster(finite_levels)
    threshold_db = sprechpegel_db - STILLE_ABSTAND_DB
    such_ab_ms = whisper_start_ms if nicht_vor_ms is None else max(whisper_start_ms, nicht_vor_ms)
    anchor_index = round((such_ab_ms - fetch_start_ms - MEASURE_MS // 2) / STEP_MS)
    anchor_index = max(0, min(len(levels) - 1, anchor_index))

    if levels[anchor_index] >= threshold_db:
        # An der Marke, ab der gesucht wird, ist bereits Ton.
        return min(such_ab_ms, whisper_start_ms + START_EINSATZ_SUCHE_MS)

    toleranz_bloecke = EINSATZ_BESTAETIGUNG_MS // STEP_MS
    idx = _folge_bis_wechsel(
        levels, anchor_index, threshold_db, ab_index_laut=False, toleranz_bloecke=toleranz_bloecke
    )
    if idx is None:
        return whisper_start_ms
    onset_ms = fetch_start_ms + idx * STEP_MS + MEASURE_MS // 2
    return min(onset_ms, whisper_start_ms + START_EINSATZ_SUCHE_MS)


def verschiebe_auf_leiseste_stelle(
    media_path: Path,
    mark_ms: int,
    *,
    ffmpeg_path: Path,
    nur_rueckwaerts: bool = False,
    search_window_start_ms: int | None = None,
    such_min_ms: int | None = None,
    such_max_ms: int | None = None,
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

    ``such_min_ms``/``such_max_ms`` (Auftrag shorts-pegel-wortgrenze, Nachtrag
    N1/N2, je einzeln optional): harte, am Ton gemessene Schranken - die
    korrigierte Marke liegt niemals frueher als ``such_min_ms`` und niemals
    spaeter als ``such_max_ms``. Stellen ausserhalb ``[such_min_ms,
    such_max_ms]`` werden von der Auswahl ausgeschlossen. Das Suchfenster wird
    dazu NUR in der Richtung erweitert, in der die jeweilige Schranke
    tatsaechlich liegt - ``such_min_ms`` (Untergrenze) weitet das Fenster
    hoechstens VORWAERTS aus (relevant fuer die ENDgrenze, deren gemessenes
    Lautende mehrere hundert ms nach der Marke liegen kann), ``such_max_ms``
    (Obergrenze) hoechstens RUECKWAERTS. NIE in die jeweils andere Richtung:
    eine rueckwaertige Ausweitung fuer ``such_min_ms`` wuerde sonst genau dort
    suchen, wo :func:`finde_stillevorlauf` bewusst eine lange Stille
    uebersprungen hat, und diese Verschiebung wieder rueckgaengig machen.
    Wird das Fenster dadurch leer (``such_max_ms <= such_min_ms`` oder keine
    Messstelle passt hinein), bleibt die Marke UNVERAENDERT stehen
    (:data:`VERFAHREN_WORTGRENZE_STEHT`) - kein Ausweichen, keine Naeherung.
    Ohne beide Parameter (Vorgabe, ``None``) ist das Verhalten exakt wie zuvor
    - der alte Aufrufweg ohne Wortgrenzen bleibt unveraendert.

    Wirft :class:`LevelCutFailed`, wenn ffmpeg scheitert oder das Fenster keine
    einzige vollstaendige Messung hergibt. KEIN Rueckfall auf ``mark_ms``
    (ausser im ausdruecklich benannten ``such_min_ms``/``such_max_ms``-Fall
    oben).
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
    fetch_lo_ms = mark_ms - window_before_ms
    fetch_hi_ms = mark_ms + window_after_ms
    # such_min_ms ist eine UNTERGRENZE des Ergebnisses - das Fenster muss nur
    # so weit VORWAERTS reichen, dass such_min_ms ueberhaupt eine gueltige
    # Messstelle ist (relevant fuer die ENDgrenze, wo das gemessene Lautende
    # mehrere hundert ms nach der Marke liegen kann). NIE rueckwaerts
    # ausweiten: rueckwaerts ist such_min_ms nur eine Schranke, keine Suche -
    # eine Ausweitung wuerde sonst gerade dort suchen, wo Stillevorlauf
    # (Auftrag shorts-stillevorlauf) bewusst eine lange Stille uebersprungen
    # hat, und diese Verschiebung wieder rueckgaengig machen.
    if such_min_ms is not None and such_min_ms > fetch_hi_ms:
        fetch_hi_ms = such_min_ms
    # such_max_ms spiegelbildlich: nur so weit RUECKWAERTS ausweiten, dass es
    # ueberhaupt eine gueltige Messstelle gibt, nie vorwaerts.
    if such_max_ms is not None and such_max_ms < fetch_lo_ms:
        fetch_lo_ms = such_max_ms
    window_before_ms = max(0, mark_ms - fetch_lo_ms)
    window_after_ms = max(0, fetch_hi_ms - mark_ms)

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

    # Stelle i misst mittig um (fetch_start + i*STEP + MEASURE_MS/2).
    def _position(index: int) -> int:
        return fetch_start_ms + index * STEP_MS + MEASURE_MS // 2

    # "such_max_ms <= such_min_ms" ist IMMER ein leeres Fenster (Pause null
    # oder negativ) - unabhaengig davon, ob zufaellig eine einzelne Messstelle
    # genau auf diesen einen Punkt faellt.
    leeres_fenster = (
        such_min_ms is not None and such_max_ms is not None and such_max_ms <= such_min_ms
    )

    lo_idx = 0
    hi_idx = len(levels) - 1
    if such_min_ms is not None:
        # kleinster Index mit Position >= such_min_ms (Aufrundung).
        offset = such_min_ms - fetch_start_ms - MEASURE_MS // 2
        lo_idx = max(lo_idx, -(-offset // STEP_MS))
    if such_max_ms is not None:
        # groesster Index mit Position <= such_max_ms (Abrundung).
        offset = such_max_ms - fetch_start_ms - MEASURE_MS // 2
        hi_idx = min(hi_idx, offset // STEP_MS)

    if leeres_fenster or ((such_min_ms is not None or such_max_ms is not None) and lo_idx > hi_idx):
        nearest = min(range(len(levels)), key=lambda i: abs(_position(i) - mark_ms))
        return LevelSnap(
            original_ms=mark_ms,
            corrected_ms=mark_ms,
            shift_ms=0,
            level_db=levels[nearest],
            window_mean_db=_mean_level_db(levels),
            verfahren=VERFAHREN_WORTGRENZE_STEHT,
            quiet_region_ms=0,
        )

    sub_levels = levels[lo_idx : hi_idx + 1]
    chosen_sub, verfahren, quiet_region_ms = _choose_cut_point(sub_levels)
    chosen = chosen_sub + lo_idx
    corrected_ms = _position(chosen)
    return LevelSnap(
        original_ms=mark_ms,
        corrected_ms=corrected_ms,
        shift_ms=corrected_ms - mark_ms,
        level_db=levels[chosen],
        window_mean_db=_mean_level_db(levels),
        verfahren=verfahren,
        quiet_region_ms=quiet_region_ms,
    )


def miss_pegel_bei_marke(
    media_path: Path,
    mark_ms: int,
    *,
    ffmpeg_path: Path,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> tuple[float, float]:
    """Miss Pegel und Fenstermittel an einer FESTEN Stelle, ohne zu suchen.

    Auftrag shorts-endgrenze-schranke: TEIL 1 (:data:`VERFAHREN_LAUTENDE_STEHT`)
    und TEIL 2 (:data:`VERFAHREN_FENSTER_AUSGESCHOEPFT`) setzen die Endgrenze
    manchmal direkt auf eine vorgegebene Stelle (das gemessene Lautende oder
    den whisper-Anfang des naechsten Wortes), statt sie wie
    :func:`verschiebe_auf_leiseste_stelle` zu suchen - der Baubericht braucht
    an dieser Stelle trotzdem einen echten, gemessenen Pegelwert. Dieselbe
    Messung (Sprachband, 10-ms-Raster, 40-ms-Ausschnitte) wie
    :func:`verschiebe_auf_leiseste_stelle`, nur ohne dessen Suche.

    Gibt ``(Pegel an der ``mark_ms`` naechstgelegenen Messstelle,
    Fenstermittel)`` zurueck.
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, mark_ms - SEARCH_WINDOW_MS - MEASURE_MS // 2)
    fetch_duration_ms = 2 * SEARCH_WINDOW_MS + MEASURE_MS
    levels = _fetch_measured_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Endgrenze/feste Stelle",
    )

    def _position(index: int) -> int:
        return fetch_start_ms + index * STEP_MS + MEASURE_MS // 2

    nearest = min(range(len(levels)), key=lambda i: abs(_position(i) - mark_ms))
    return levels[nearest], _mean_level_db(levels)


def finde_wortrand_ende(
    media_path: Path,
    word_end_ms: int,
    *,
    ffmpeg_path: Path,
    ober_grenze_ms: int | None = None,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int | None:
    """Bestimme das WAHRE Wortende am Ton - Pausengrund-Verfahren.

    Auftrag shorts-wortrand-abstand, TEIL 1. Ein Wort ist im Ton kein Rechteck:
    Anlaut und Ausklang sind leise und werden von der bisherigen Schwelle
    (Sprechpegel minus :data:`STILLE_ABSTAND_DB`, siehe
    :func:`finde_wortende_ton`) oft schon als Stille gewertet, obwohl der
    Ausklang noch hoerbar zum Wort gehoert.
    Dieses Verfahren sucht stattdessen die erste Stelle, an der der Pegel
    unter den PAUSENGRUND faellt - den Pegel der echten Sprechpause danach,
    nicht nur unter eine feste Schwelle relativ zum Sprechpegel.

    Verfahren: miss den Pegel (Zweiband-Maximum wie :func:`finde_wortende_ton`,
    10-ms-Raster) vorwaerts ab ``word_end_ms``, hoechstens
    :data:`WORTRAND_SUCHE_ENDE_MS`. Suche darin - wie
    :func:`verschiebe_auf_leiseste_stelle`s ``_choose_cut_point``, dieselbe
    Konstante :data:`PAUSENGRUND_ABSTAND_DB` - den FRUEHESTEN Bereich, der
    mindestens :data:`PAUSENGRUND_ABSTAND_DB` unter dem Fenstermittel liegt
    UND mindestens :data:`PAUSENGRUND_MIN_PAUSE_ENDE_MS` lang ist (kuerzere
    Bereiche sind Lautluecken im Ausklang selbst, keine echte Pause). Der
    FRUEHESTE (nicht der tiefste oder laengste) Bereich zaehlt: ein spaeterer,
    zufaellig tieferer oder laengerer Bereich koennte bereits zu einem
    dazwischenliegenden, von whisper uebersehenen Wort gehoeren (Befund,
    Auftrag shorts-wortrand-abstand TEIL 1: bei "der" und "Numerologie" hat
    genau das die Messung verunreinigt, als der laengste statt der fruehesten
    Bereich gewaehlt wurde).

    Der Anfang dieses Bereichs ist das wahre Wortende.

    ``ober_grenze_ms`` (Auftrag shorts-wortrand-abstand, TEIL 2): deckelt das
    Ergebnis auf hoechstens diesen Wert - noetig, wenn dieses Wort nicht das
    eigentliche Zielwort ist, sondern das VORIGE Wort an einer Startgrenze
    (dessen wahres Ende als Schranke gebraucht wird): ohne Deckel kann die
    Suche ueber ein kurzes, dazwischenliegendes Wort hinweglaufen, weil dessen
    eigener Pegelverlauf keinen ausreichend langen "erneuten Peak" bildet, um
    die Suche zu stoppen (Befund: "angekommen." vor kandidat-00, ungedeckelt
    31140 - das liegt HINTER dem gemessenen Anfang von "Na," selbst).

    Gibt ``None`` zurueck, wenn innerhalb der Suchweite kein solcher Bereich
    existiert - kein Fallback, kein Ausweichen; der Aufrufer entscheidet dann
    selbst (siehe ``build._apply_level_correction``).
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, word_end_ms - MEASURE_MS // 2)
    fetch_duration_ms = WORTRAND_SUCHE_ENDE_MS + MEASURE_MS
    levels = _fetch_zweiband_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Wortrand/Ende",
    )
    if not levels:
        return None
    mean_db = _mean_level_db(levels)
    threshold_db = mean_db - PAUSENGRUND_ABSTAND_DB
    min_blocks = PAUSENGRUND_MIN_PAUSE_ENDE_MS // STEP_MS
    candidates = [
        region
        for region in _quiet_regions(levels, threshold_db)
        if region[1] - region[0] + 1 >= min_blocks
    ]
    if not candidates:
        return None
    start_index, _end_index = min(candidates, key=lambda region: region[0])
    ergebnis_ms = fetch_start_ms + start_index * STEP_MS + MEASURE_MS // 2
    return ergebnis_ms if ober_grenze_ms is None else min(ergebnis_ms, ober_grenze_ms)


def finde_wortrand_anfang(
    media_path: Path,
    word_start_ms: int,
    *,
    ffmpeg_path: Path,
    unter_grenze_ms: int | None = None,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int | None:
    """Bestimme den WAHREN Wortanfang am Ton - Pausengrund-Verfahren.

    Auftrag shorts-wortrand-abstand, TEIL 1. Spiegelbildlich zu
    :func:`finde_wortrand_ende`. Miss den Pegel vorwaerts ab
    ``word_start_ms - WORTRAND_SUCHE_ANFANG_RUECK_MS`` bis
    ``word_start_ms + WORTRAND_SUCHE_ANFANG_VOR_MS`` - bewusst eng
    (150 ms zurueck, 60 ms vor): eine breitere Vorwaertssuche lief bei kurzen
    Woertern regelmaessig ueber das Wort hinaus in die Pause DANACH (Befund:
    Fall "Boden" bei einer fruehen Fassung dieses Verfahrens). Suche darin den
    SPAETESTEN (dem Wortanfang naechstgelegenen) Bereich, der mindestens
    :data:`PAUSENGRUND_ABSTAND_DB` unter dem Fenstermittel liegt und
    mindestens :data:`PAUSENGRUND_MIN_PAUSE_ANFANG_MS` lang ist.

    Das Ende dieses Bereichs ist der wahre Wortanfang.

    ``unter_grenze_ms`` (Auftrag shorts-wortrand-abstand, TEIL 2): deckelt das
    Ergebnis auf mindestens diesen Wert - spiegelbildlich zu ``ober_grenze_ms``
    bei :func:`finde_wortrand_ende`, fuer denselben Fall an der Endgrenze
    (dieses Wort ist das NAECHSTE Wort, dessen wahrer Anfang als Schranke
    gebraucht wird).

    Gibt ``None`` zurueck, wenn kein solcher Bereich existiert.
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, word_start_ms - WORTRAND_SUCHE_ANFANG_RUECK_MS - MEASURE_MS // 2)
    fetch_duration_ms = (
        (word_start_ms - fetch_start_ms) + WORTRAND_SUCHE_ANFANG_VOR_MS + MEASURE_MS // 2
    )
    levels = _fetch_zweiband_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Wortrand/Anfang",
    )
    if not levels:
        return None
    mean_db = _mean_level_db(levels)
    threshold_db = mean_db - PAUSENGRUND_ABSTAND_DB
    min_blocks = PAUSENGRUND_MIN_PAUSE_ANFANG_MS // STEP_MS
    candidates = [
        region
        for region in _quiet_regions(levels, threshold_db)
        if region[1] - region[0] + 1 >= min_blocks
    ]
    if not candidates:
        return None
    _start_index, end_index = max(candidates, key=lambda region: region[1])
    ergebnis_ms = fetch_start_ms + end_index * STEP_MS + MEASURE_MS // 2
    return ergebnis_ms if unter_grenze_ms is None else max(ergebnis_ms, unter_grenze_ms)


def finde_nachbarrand_einsatz(
    media_path: Path,
    own_true_end_ms: int,
    *,
    ober_grenze_ms: int | None = None,
    ffmpeg_path: Path,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int | None:
    """Suche den wahren Einsatz des NAECHSTEN Wortes bei einer Nullpause.

    Auftrag shorts-nachbarrand: greift nur, wenn ``pause_after_ms <= 0`` ist -
    dann liegt ``next_word_start_ms`` (die whisper-Marke, um die
    :func:`finde_wortrand_anfang` sonst suchen wuerde) exakt auf
    ``own_true_end_ms`` bzw. der noch ungekorrigierten eigenen Wortgrenze, und
    das enge Fenster jener Funktion (150 ms zurueck, 60 ms vor) trifft dann
    oft nur den eigenen Ausklang statt die tatsaechliche Pause zum Nachbarn
    (siehe Auftragsbeschreibung, Befund kandidat-01/kandidat-06).

    Miss stattdessen den Pegel (Zweiband-Maximum wie :func:`finde_wortrand_ende`,
    10-ms-Raster) VORWAERTS ab ``own_true_end_ms`` (dem schon gemessenen
    eigenen wahren Wortende), hoechstens :data:`NACHBARRAND_SUCHE_MS`. Suche
    darin - dieselben Kriterien wie :func:`finde_wortrand_ende`
    (:data:`PAUSENGRUND_ABSTAND_DB`, :data:`PAUSENGRUND_MIN_PAUSE_ENDE_MS`) -
    den FRUEHESTEN zusammenhaengenden Bereich, der als echte Pause zaehlt.

    Anders als :func:`finde_wortrand_ende` ist hier aber nicht der ANFANG
    dieses Bereichs gesucht (das waere wieder das eigene Wortende, das schon
    bekannt ist), sondern dessen ENDE - der Punkt, an dem der Pegel nach der
    Pause wieder dauerhaft ueber den Pausengrund steigt, also der wahre
    Einsatz des Nachbarworts.

    ``ober_grenze_ms`` deckelt das Ergebnis nach oben, falls uebergeben -
    analog zu :func:`finde_wortrand_anfang`s ``unter_grenze_ms``.

    Gibt ``None`` zurueck, wenn innerhalb der Suchweite kein solcher Bereich
    existiert - der Aufrufer faellt dann auf das bisherige Verhalten zurueck
    (Deckelung an der whisper-Marke ``next_word_start_ms``, siehe
    ``build._apply_level_correction``).
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, own_true_end_ms - MEASURE_MS // 2)
    fetch_duration_ms = NACHBARRAND_SUCHE_MS + MEASURE_MS
    levels = _fetch_zweiband_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Nachbarrand/Einsatz",
    )
    if not levels:
        return None
    mean_db = _mean_level_db(levels)
    threshold_db = mean_db - PAUSENGRUND_ABSTAND_DB
    min_blocks = PAUSENGRUND_MIN_PAUSE_ENDE_MS // STEP_MS
    candidates = [
        region
        for region in _quiet_regions(levels, threshold_db)
        if region[1] - region[0] + 1 >= min_blocks
    ]
    if not candidates:
        return None
    _start_index, end_index = min(candidates, key=lambda region: region[0])
    ergebnis_ms = fetch_start_ms + end_index * STEP_MS + MEASURE_MS // 2
    return ergebnis_ms if ober_grenze_ms is None else min(ergebnis_ms, ober_grenze_ms)


def finde_nachbarrand_ausklang(
    media_path: Path,
    own_true_start_ms: int,
    *,
    unter_grenze_ms: int | None = None,
    ffmpeg_path: Path,
    timeout_seconds: int = 120,
    process_runner: ProcessRunner | None = None,
) -> int | None:
    """Suche den wahren Ausklang des VORIGEN Wortes bei einer Nullpause.

    Auftrag shorts-nachbarrand: spiegelbildlich zu
    :func:`finde_nachbarrand_einsatz`, greift nur, wenn ``pause_before_ms <= 0``
    ist. Miss den Pegel RUECKWAERTS ab ``own_true_start_ms`` (dem schon
    gemessenen eigenen wahren Wortanfang), hoechstens
    :data:`NACHBARRAND_SUCHE_MS`. Suche darin - dieselben Kriterien wie
    :func:`finde_wortrand_anfang` (:data:`PAUSENGRUND_ABSTAND_DB`,
    :data:`PAUSENGRUND_MIN_PAUSE_ANFANG_MS`) - den SPAETESTEN (dem eigenen
    Wortanfang naechstgelegenen) Bereich, der als echte Pause zaehlt.

    Anders als :func:`finde_wortrand_anfang` ist hier aber nicht das ENDE
    dieses Bereichs gesucht (das waere wieder der eigene Wortanfang), sondern
    dessen ANFANG - der wahre Ausklang des Vorgaengerworts.

    ``unter_grenze_ms`` deckelt das Ergebnis nach unten, falls uebergeben.

    Gibt ``None`` zurueck, wenn kein solcher Bereich existiert - der Aufrufer
    faellt dann auf das bisherige Verhalten zurueck (siehe
    ``build._apply_level_correction``).
    """
    runner = process_runner if process_runner is not None else _default_process_runner
    fetch_start_ms = max(0, own_true_start_ms - NACHBARRAND_SUCHE_MS - MEASURE_MS // 2)
    fetch_duration_ms = (own_true_start_ms - fetch_start_ms) + MEASURE_MS // 2
    levels = _fetch_zweiband_levels(
        media_path,
        fetch_start_ms=fetch_start_ms,
        fetch_duration_ms=fetch_duration_ms,
        ffmpeg_path=ffmpeg_path,
        timeout_seconds=timeout_seconds,
        process_runner=runner,
        context="Nachbarrand/Ausklang",
    )
    if not levels:
        return None
    mean_db = _mean_level_db(levels)
    threshold_db = mean_db - PAUSENGRUND_ABSTAND_DB
    min_blocks = PAUSENGRUND_MIN_PAUSE_ANFANG_MS // STEP_MS
    candidates = [
        region
        for region in _quiet_regions(levels, threshold_db)
        if region[1] - region[0] + 1 >= min_blocks
    ]
    if not candidates:
        return None
    start_index, _end_index = max(candidates, key=lambda region: region[1])
    ergebnis_ms = fetch_start_ms + start_index * STEP_MS + MEASURE_MS // 2
    return ergebnis_ms if unter_grenze_ms is None else max(ergebnis_ms, unter_grenze_ms)
