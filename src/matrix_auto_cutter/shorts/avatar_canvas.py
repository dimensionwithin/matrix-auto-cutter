r"""Stufe 5b: die nachgeschnittene Avatardatei auf die Leinwand legen.

Legt eine bereits nachgeschnittene Avatardatei (Ausgabe von Stufe 1,
``avatar_cut.py``) auf eine bestehende 1080x1920-Leinwand (Ausgabe von
Stufe 5a, ``canvas.py``). Die Avatardatei hat KEINEN Alphakanal - Freistellung
geschieht per ``blend=all_mode=lighten`` gegen die Leinwandflaeche
(``canvas.BACKGROUND_COLOR_RGB``). Kein Keying, keine Maske, kein
Schwellenwert.

Seit Auftrag shorts-hintergrund-schwarz ist der Leinwandhintergrund selbst
schwarz (0,0,0) - dieselbe Farbe wie der gemessene Avatarhintergrund, siehe
``canvas.BACKGROUND_COLOR_RGB``. Damit verschwindet der Avatarhintergrund
weiterhin restlos (0,0,0 unter 0,0,0 bleibt 0,0,0). Der Mantel (gemessen in
``avatar-cut.mp4`` fuer 2026-08-07 11-35-16: rund (21..27, 19..25, 17..23),
je nach Kompressionsrauschen) und die Kapuze (rund (38,36,34)) waren zuvor
DUNKLER als das alte ``--ink`` (23,22,20) und verschwanden deshalb ebenfalls
per ``lighten``. Gegen ein rein schwarzes (0,0,0) Leinwandband ist jeder
dieser Werte jetzt HELLER als der Hintergrund und bleibt dadurch sichtbar -
Mantel und Kapuze zeichnen sich nun als eigene, etwas hellere Flaeche gegen
die schwarze Leinwand ab, statt nahtlos zu verschwinden. Das ist eine reale,
gemessene Verhaltensaenderung (siehe Bericht
``artefakte\repeat\shorts-hintergrund-schwarz\BERICHT-2026-08-18.md``),
keine Verletzung der harten Geometriebedingungen dieses Moduls - Gesicht
(255,255,255) bleibt so oder so sichtbar, und die Kontur bleibt innerhalb
des Avatarbands.

**Aus dem Bericht shorts-stufe-5d gelernt (dort erstmals gefunden und
behoben):** ``blend=all_mode=lighten`` handelt ohne ausdrueckliches
``format=rgb24`` auf BEIDEN Zweigen in einem von ffmpeg intern gewaehlten
YUV-Format statt in RGB - "heller je Kanal" bedeutet dann je Y/U/V statt je
R/G/B und erzeugt Farbstiche. Deshalb hier von Anfang an ``format=rgb24``
auf Leinwand- UND Avatarzweig vor dem ``blend``, und ``-pix_fmt yuv420p``
ausdruecklich am Ausgang gesetzt (sonst waehlt libx264 wegen des
``format=rgb24``-Zwischenschritts 4:4:4 statt 4:2:0).

Geometrie (SHORTS-KONTEXT Abschnitt 5): Der Avatar gehoert nach links,
nichts darf rechts von x=930 liegen (``CANVAS_WIDTH - canvas.SAFE_RIGHT``).
Das Gesicht muss im Band ab y=1100 sichtbar sein (``canvas.PANEL_Y +
canvas.PANEL_HEIGHT``); der Avatar darf das Chartpanel (y=200 bis y=1099)
an keiner Stelle ueberlappen - eine harte Bedingung, kein Stilfrage. Ausblutung
ist gewollt: Schultern und Koerper duerfen unter y=1440 hinter die
Kommentarleiste laufen und aus dem Bild hinauslaufen - hier umgesetzt, indem
der skalierte Ausschnitt exakt auf die verbleibende Bandhoehe bis zum
unteren Leinwandrand zugeschnitten wird.

**Im Bau gefunden und behoben:** Ein erster Versuch hat den Avatarzweig per
``pad`` auf volle Leinwandgroesse gebracht und dann per ``blend=lighten``
GEGEN DIE GANZE LEINWAND geblendet. Das fuellt ``pad`` ausserhalb des
Avatars mit ``--ink`` - und ``lighten`` hellt dadurch jeden Leinwand-Pixel
auf, der irgendwo dunkler als ``--ink`` ist, auch mitten im Chartpanel
(dunkle Flaechen, schwarze Kerzenkoerper werden sichtbar aufgehellt). Genau
das beschreibt die harte Bedingung oben - "ueber dem Chart wuerde `lighten`
das Chart durch den Avatar hindurchscheinen lassen" gilt eben nicht nur dort,
wo der Avatar sichtbar ist, sondern ueberall, wo geblendet wird. Behoben,
indem ``blend`` nur auf das Avatarband selbst angewendet wird (aus der
Leinwand an derselben Stelle herausgeschnitten) und das Ergebnis per
``overlay`` - nicht ``blend`` - an die unveraenderte Leinwand gesetzt wird.

Ton wird unveraendert aus der Leinwand-Eingabe uebernommen (``-c:a copy``) -
die gute, lautheitskorrigierte Spur liegt bereits im gerenderten Video. Der
Avatarton wird NICHT gemischt.

Skalierung, Position und Ausschnitt der Avatardatei sind benannte Konstanten
an EINER Stelle unten. Die Quelldatei ist 630x422 - fuer einen grossen Kopf
wird hochgerechnet (Aufloesungsgrenze bekannt, wird NICHT durch Schaerfen
oder Nachbearbeiten kaschiert).

AUSDRUECKLICH NICHT Teil dieses Moduls: Untertitel, Endcard (siehe
``endcard.py`` - bleibt liegen, wird hier nicht aufgerufen), Mausverfolgung,
Ausblenden am Ende.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.cut_proposal import discover_ffmpeg
from matrix_auto_cutter.shorts.avatar_cut import probe_frame_count
from matrix_auto_cutter.shorts.canvas import (
    CANVAS_FPS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PANEL_HEIGHT,
    PANEL_Y,
    SAFE_RIGHT,
)
from matrix_auto_cutter.shorts.chart_crop import probe_audio_track_count, probe_dimensions
from matrix_auto_cutter.shorts.frame_map import candidate_frame_span
from matrix_auto_cutter.shorts.inventory import discover_ffprobe

# ---------------------------------------------------------------------------
# Geometrie - benannte Konstanten an EINER Stelle (Auftrag shorts-stufe-5b).
# Herkunft: docs\repeat\SHORTS-KONTEXT-2026-08-09.md Abschnitt 5 (Aufteilung,
# Sicherheitszone) und Abschnitt 7 (Avatarfarben). Leinwand-Konstanten werden
# aus canvas.py uebernommen, nicht neu erfunden.
# ---------------------------------------------------------------------------

AVATAR_CROP_REFERENCE_HEIGHT = 422
AVATAR_CROP_REFERENCE_WIDTH = 430
"""Massstab fuer die Ausschnittherleitung: die alte, abgenommene 630x422-Quelle
(gemessen am 9.8., Abschnitt 7) mit ihrem damals per Sichtpruefung und
``cropdetect`` ermittelten Ausschnitt x=[100,530] (Breite 430) - Kopf und
Schultern lagen stabil innerhalb x=[157,458], y=[59,401] der Quelle. Seit
Auftrag shorts-avatar-1920 ist die Quellaufloesung NICHT mehr fest verdrahtet
(eine neue Aufnahme liefert 1920x1080 statt 630x422, siehe
:func:`compute_avatar_crop_geometry`) - diese beiden Zahlen sind die
Referenz, aus der jede unterstuetzte Quellaufloesung ihren Ausschnitt
herleitet, keine Quellgroesse mehr selbst."""

AVATAR_ASPECT_RATIO_LEGACY = 630 / 422
AVATAR_ASPECT_RATIO_1920X1080 = 1920 / 1080
AVATAR_ASPECT_RATIO_TOLERANCE = 0.01
"""Die beiden GEMESSENEN Seitenverhaeltnisse der Avatarquelle (Auftrag
shorts-avatar-1920, Teil A: 630x422 vom 9.8., 1920x1080 vom 18.8. - je ein
Einzelbild aus der Mitte von ``avatar-cut.mp4`` verglichen). Jede andere
Quellaufloesung wird in :func:`compute_avatar_crop_geometry` fail closed
abgelehnt statt still eine ungemessene Herleitung anzuwenden."""


class AvatarSourceGeometryError(Exception):
    """Fail-closed Fehler, wenn die Avatarquelle ein unbekanntes Seitenverhaeltnis hat.

    Auftrag shorts-avatar-1920, Teil B: die Herleitung des Ausschnitts unten ist nur fuer die
    zwei GEMESSENEN Seitenverhaeltnisse belegt (siehe AVATAR_ASPECT_RATIO_TOLERANCE) - ein
    drittes, ungemessenes Format wuerde sonst still eine geratene Groesse liefern.
    """

    def __init__(self, code: str, message_de: str) -> None:
        """Trage Fehlercode und deutschsprachige Meldung ein."""
        super().__init__(message_de)
        self.code = code
        self.message_de = message_de


@dataclass(frozen=True, slots=True)
class AvatarCropGeometry:
    """Vollstaendig berechnete Ausschnitt- und Skaliergeometrie fuer eine gemessene Quelle."""

    source_width: int
    source_height: int
    crop_x: int
    crop_y: int
    crop_width: int
    crop_height: int
    scale_width: int
    scale_height: int
    final_width: int
    final_height: int


def compute_avatar_crop_geometry(source_width: int, source_height: int) -> AvatarCropGeometry:
    """Leite Ausschnitt- und Skaliergeometrie aus der GEMESSENEN Quellaufloesung her.

    Herkunft (Auftrag shorts-avatar-1920, Teil A/B): Ein Einzelbildvergleich aus der Mitte von
    ``avatar-cut.mp4`` fuer 630x422 (9.8.) und 1920x1080 (18.8.) zeigt, dass der Hoehen-Anteil
    der Figur nahezu unveraendert bleibt (88,61% vs. 89,34% der Bildhoehe), waehrend der
    Breiten-Anteil deutlich abnimmt (62,38% -> 52,19% der Bildbreite) - UND dass die Figur in
    beiden Aufnahmen symmetrisch um die Bildmitte liegt (der alte Ausschnitt x=[100,530] ist
    bereits exakt um die alte Bildmitte 315 zentriert: 100+530=630). Das Bildfeld ist also
    NICHT proportional gewachsen: dieselbe senkrechte Kameraeinstellung (Kopf/Schultern behalten
    dieselbe Groesse je Hoehenpixel), aber mehr waagerechte Bildbreite, symmetrisch um die Mitte
    verteilt (mehr Hintergrund links und rechts, keine Verzerrung der Figur). Der Ausschnitt wird
    deshalb mit dem Hoehen-Skalierungsfaktor (``source_height / AVATAR_CROP_REFERENCE_HEIGHT``)
    aus der alten Ausschnittbreite hergeleitet und um die neue Bildmitte zentriert - das
    reproduziert fuer 630x422 (Skalierungsfaktor 1) exakt die alten Werte (100, 430) und liefert
    fuer 1920x1080 einen Ausschnitt (1100 breit, x=410), der Kopf und Schultern in derselben
    Endgroesse auf der Leinwand zeigt wie zuvor (siehe Bericht, Teil B: beide Faelle ergaben
    scale_height=913 bei AVATAR_SCALE_WIDTH=930; seit Auftrag shorts-avatar-position
    (AVATAR_SCALE_WIDTH=830) scale_height=815; seit Auftrag shorts-avatar-position-2
    (AVATAR_SCALE_WIDTH=747) ergeben beide Faelle scale_height=733).

    Faellt das gemessene Seitenverhaeltnis NICHT mit einem der beiden gemessenen Faelle
    zusammen (Toleranz ``AVATAR_ASPECT_RATIO_TOLERANCE``), wird fail closed mit
    :class:`AvatarSourceGeometryError` gemeldet - fuer ein drittes Format ist diese Herleitung
    nicht belegt.
    """
    if source_width <= 0 or source_height <= 0:
        raise AvatarSourceGeometryError(
            "avatar_source_dimensions_invalid",
            f"Quellaufloesung {source_width}x{source_height} ist ungueltig",
        )
    aspect_ratio = source_width / source_height
    known_aspect_ratios = (AVATAR_ASPECT_RATIO_LEGACY, AVATAR_ASPECT_RATIO_1920X1080)
    if not any(
        abs(aspect_ratio - candidate) <= AVATAR_ASPECT_RATIO_TOLERANCE
        for candidate in known_aspect_ratios
    ):
        raise AvatarSourceGeometryError(
            "avatar_source_aspect_ratio_unsupported",
            f"Seitenverhaeltnis {aspect_ratio:.4f} ({source_width}x{source_height}) weicht von "
            f"beiden gemessenen Faellen ab (alt {AVATAR_ASPECT_RATIO_LEGACY:.4f}, 1920x1080 "
            f"{AVATAR_ASPECT_RATIO_1920X1080:.4f}, Toleranz {AVATAR_ASPECT_RATIO_TOLERANCE}) - "
            "die Ausschnittherleitung ist dafuer nicht belegt",
        )

    scale = source_height / AVATAR_CROP_REFERENCE_HEIGHT
    crop_height = source_height
    crop_width = round(AVATAR_CROP_REFERENCE_WIDTH * scale)
    crop_x = round((source_width - crop_width) / 2)
    crop_y = 0

    if crop_x < 0 or crop_x + crop_width > source_width:
        raise AvatarSourceGeometryError(
            "avatar_crop_outside_source",
            f"hergeleiteter Ausschnitt x=[{crop_x},{crop_x + crop_width}] liegt ausserhalb der "
            f"Quellbreite {source_width}",
        )

    scale_width = AVATAR_SCALE_WIDTH
    scale_height = round(crop_height * scale_width / crop_width)
    final_width = scale_width
    final_height = min(scale_height, AVATAR_BAND_HEIGHT)

    return AvatarCropGeometry(
        source_width=source_width,
        source_height=source_height,
        crop_x=crop_x,
        crop_y=crop_y,
        crop_width=crop_width,
        crop_height=crop_height,
        scale_width=scale_width,
        scale_height=scale_height,
        final_width=final_width,
        final_height=final_height,
    )


# --- Skalierung: Ausschnittbreite auf die erlaubte Avatarbreite hochrechnen
# (die Breite bis zur rechten Sicherheitszone, MINUS Luft am linken Rand -
# Auftrag shorts-avatar-position/-2, siehe AVATAR_PLACE_X unten) -
# Seitenverhaeltnis des Ausschnitts bleibt erhalten. Haengt NICHT von der
# Quellaufloesung ab (siehe compute_avatar_crop_geometry oben), deshalb
# weiterhin eine feste Konstante.
#
# Befund (Auftrag shorts-avatar-position): im gebauten Short kandidat-33 der
# Aufnahme 2026-08-18 08-51-21 sass der Avatar bei AVATAR_SCALE_WIDTH=930 und
# AVATAR_PLACE_X=0 buendig am linken Rand ohne Luft - bei einer
# Sondervariante mit Requisit (Pistole) fehlte dadurch ein Stueck des
# Requisits am linken Bildrand. Rechts vom Kopf blieb viel Platz. Die Breite
# wurde deshalb zunaechst auf 830 verkleinert (930 - 80) bei
# AVATAR_PLACE_X=80 (Auftrag shorts-avatar-position).
#
# Auftrag shorts-avatar-position-2: nochmals um zehn Prozent verkleinert
# (830 -> 747) und AVATAR_PLACE_X weiter nach rechts gesetzt (80 -> 140) -
# der Avatar endet damit bei 140 + 747 = 887, weiterhin vor der
# Sicherheitsgrenze 930. Links entstehen 140 px Luft statt 80.
AVATAR_SCALE_WIDTH = 747

# --- Position: 140px Luft am linken Rand (Auftrag shorts-avatar-position-2,
# vorher 80px seit shorts-avatar-position, davor buendig links). Oberkante
# NICHT an der Panelkante (Auftrag shorts-5b-5c-nachbesserung, Teil B): der
# Untertitel steht bei y=1124..1180
# (subtitle_burn.SUBTITLE_TEXT_Y/-TEXT_HEIGHT_PX) und lag im Gesicht des
# Avatars, solange dieser direkt unter dem Panel begann. Um 100 px tiefer
# gesetzt - der sichtbare Avatar wird dadurch 100 px kuerzer (Ausblutung
# nach unten bleibt gewollt, siehe AVATAR_FINAL_HEIGHT unten).
AVATAR_PLACE_X = 140
AVATAR_PLACE_Y = 1200

assert AVATAR_PLACE_Y == PANEL_Y + PANEL_HEIGHT + 100, (
    "AVATAR_PLACE_Y haengt an canvas.PANEL_Y/PANEL_HEIGHT plus dem 100px-Tieferruecken"
)

# --- Endgueltige Groesse auf der Leinwand: Breite bleibt (747, s.o.), Hoehe
# wird auf die verbleibende Bandhoehe bis zum unteren Leinwandrand
# zugeschnitten (720 < die skalierte Hoehe von compute_avatar_crop_geometry,
# fuer beide gemessenen Quellaufloesungen 733 seit AVATAR_SCALE_WIDTH=747,
# Auftrag shorts-avatar-position-2 - vorher 815 bei AVATAR_SCALE_WIDTH=830,
# davor 913 bei AVATAR_SCALE_WIDTH=930) - das
# ``pad`` weiter unten verlangt, dass sein Inhalt nicht groesser als die
# Zielflaeche ist; ein Zuschnitt VOR dem Platzieren erledigt genau das
# gewollte Hinauslaufen aus dem Bild am unteren Rand. AVATAR_FINAL_WIDTH/
# -HEIGHT haengen NICHT von der Quellaufloesung ab (nur AVATAR_SCALE_WIDTH
# und die Bandhoehe) - sie bleiben deshalb feste Konstanten, obwohl der
# Ausschnitt selbst jetzt pro Quelle berechnet wird
# (compute_avatar_crop_geometry.final_width/-height liefern fuer beide
# unterstuetzten Quellen dieselben Werte).
AVATAR_BAND_HEIGHT = CANVAS_HEIGHT - AVATAR_PLACE_Y
AVATAR_FINAL_WIDTH = AVATAR_SCALE_WIDTH
AVATAR_FINAL_HEIGHT = AVATAR_BAND_HEIGHT

assert AVATAR_BAND_HEIGHT == 720, "AVATAR_BAND_HEIGHT haengt an CANVAS_HEIGHT/AVATAR_PLACE_Y"
assert AVATAR_FINAL_HEIGHT == 720, "AVATAR_FINAL_HEIGHT haengt an AVATAR_BAND_HEIGHT"

# Harte Bedingungen aus dem Auftrag, als Asserts abgesichert statt nur im
# Text behauptet.
assert AVATAR_PLACE_X + AVATAR_FINAL_WIDTH <= CANVAS_WIDTH - SAFE_RIGHT, (
    "kein Avatarpixel darf rechts von x=930 liegen"
)
assert AVATAR_PLACE_Y >= PANEL_Y + PANEL_HEIGHT, (
    "der Avatar darf das Chartpanel an keiner Stelle ueberlappen"
)
assert AVATAR_PLACE_Y + AVATAR_FINAL_HEIGHT <= CANVAS_HEIGHT, "Avatar muss auf der Leinwand liegen"

# Selbstpruefung: fuer BEIDE gemessenen Quellaufloesungen (Auftrag
# shorts-avatar-1920, Teil A) muss compute_avatar_crop_geometry auf dieselbe
# skalierte Hoehe (733 seit AVATAR_SCALE_WIDTH=747, Auftrag
# shorts-avatar-position-2 - vorher 815, davor 913, > AVATAR_BAND_HEIGHT)
# kommen, die AVATAR_FINAL_HEIGHT oben als feste 720 voraussetzt - sonst
# waere die Klemmung auf AVATAR_BAND_HEIGHT fuer eine der beiden Quellen
# keine reine Kuerzung mehr.
_legacy_geometry = compute_avatar_crop_geometry(630, 422)
_new_geometry = compute_avatar_crop_geometry(1920, 1080)
assert _legacy_geometry.crop_x == 100, "Herleitung muss die alten 630x422-Werte reproduzieren"
assert _legacy_geometry.crop_width == 430, "Herleitung muss die alten 630x422-Werte reproduzieren"
assert _legacy_geometry.scale_height == 733, "skalierte Hoehe der Referenzquelle haengt an 733"
assert _new_geometry.scale_height == 733, "skalierte Hoehe der 1920x1080-Quelle haengt an 733"
assert _legacy_geometry.final_height == AVATAR_FINAL_HEIGHT
assert _new_geometry.final_height == AVATAR_FINAL_HEIGHT
del _legacy_geometry, _new_geometry

AVATAR_CANVAS_REPORT_SCHEMA_VERSION = "1.0"

ACHSENABWEICHUNG_MAX_FRAMES = 5
"""Toleranz der Achsenpruefung in Frames (Auftrag shorts-achsenpruefung-warnung, 2026-08-19).

Die Pruefung 'Avatar-Framezahl + missing_frames_front + missing_frames_back ==
expected_avatar_frame_count' (siehe run_stage5b, Punkt 5) ist von einem Abbruch zu einer
Warnung geworden - ein Frame Abweichung ist Proposal-Arithmetik (gerundete
Schnittintervalle vs. tatsaechliches Renderergebnis), kein Fehler, und blockierte zuvor
JEDEN Kandidaten einer sonst unauffaelligen Aufnahme. Diese Konstante ist die Grenze, ab
der aus der Warnung wieder ein Abbruch wird: 60 Frames waren in Auftrag
shorts-avatar-endversatz ein echter, gefangener Fehler (verwechselte Achse) - die
Pruefung darf durch die Lockerung nicht zahnlos werden. Eine Abweichung <= 5 Frames wird
gebaut und im Laufbericht vermerkt (achsenabweichung_frames/-hinweis in build.py), eine
groessere bricht weiterhin mit demselben Fehlercode ab wie zuvor."""


# ---------------------------------------------------------------------------
# ffmpeg: Avatar zurechtschneiden/skalieren/platzieren, per blend=lighten
# gegen die Leinwand freistellen.
# ---------------------------------------------------------------------------


def build_ffmpeg_filter_complex(
    *,
    canvas_frame_count: int,
    avatar_frame_count: int,
    avatar_start_frame: int,
    avatar_end_frame: int,
    avatar_source_width: int,
    avatar_source_height: int,
) -> tuple[str, str, str]:
    """Baue den ``-filter_complex``-Ausdruck und die Ausgabelabels.

    Die Leinwand ist ein Kandidatenausschnitt aus der Mitte des gerenderten
    Videos, kein Video ab Frame 0 (Auftrag shorts-5b-5c-nachbesserung, Teil
    A) - der Avatarzweig muss deshalb an derselben Stelle beginnen wie die
    Leinwand: ``avatar_start_frame``/``avatar_end_frame`` (die Framespanne
    des Kandidaten auf der gerenderten Achse, siehe
    :func:`matrix_auto_cutter.shorts.frame_map.candidate_frame_span`) werden
    IMMER per ``trim`` aus dem Avatarzweig herausgeschnitten, geklemmt auf
    die tatsaechlich vorhandene Avatar-Framezahl UND auf die Leinwandlaenge.
    Reicht der Avatar nicht bis zum Ende der Spanne, wird die fehlende
    Differenz per ``tpad`` (letztes Bild stehen lassen) ergaenzt - derselbe
    Mechanismus wie zuvor, jetzt auf den zugeschnittenen Abschnitt
    angewendet statt auf die ganze Datei ab Frame 0.

    ``avatar_source_width``/``avatar_source_height`` sind die GEMESSENE
    Aufloesung der Avatardatei (Auftrag shorts-avatar-1920, Teil B - vorher
    fest auf 630x422 verdrahtet) - der Ausschnitt wird daraus per
    :func:`compute_avatar_crop_geometry` hergeleitet, siehe dort. Ein
    unbekanntes Seitenverhaeltnis loest :class:`AvatarSourceGeometryError`
    aus, statt still einen falschen Ausschnitt zu bauen.

    **Im Bau gefundener und behobener Fehler:** ``blend=lighten`` mit einem
    auf volle Leinwandgroesse gepaddeten Avatarzweig (``pad`` fuellt ALLES
    ausserhalb des Avatars mit ``--ink``) hellt jeden Chart-Pixel auf, der in
    irgendeinem Kanal dunkler als ``--ink`` ist - und genau das kommt im
    Chartpanel staendig vor (dunkle Flaechen, schwarze Kerzenkoerper). Das
    verletzt die harte Bedingung, dass der Avatar das Chartpanel an keiner
    Stelle veraendern darf. Behoben, indem ``blend`` nur auf das Avatarband
    selbst angewendet wird (``crop`` aus der Leinwand an derselben Stelle,
    an der der Avatar landet) und das Ergebnis per ``overlay`` - nicht
    ``blend`` - auf die unveraenderte Leinwand gesetzt wird. So beeinflusst
    das Freistellen nur das Rechteck, in dem der Avatar tatsaechlich liegt.

    **Im Bau gefundener und behobener Fehler (Auftrag shorts-avatar-position-2):**
    alle ``crop``-Aufrufe tragen ``exact=1``. Ohne dieses Flag rundet ffmpegs
    ``crop``-Filter eine UNGERADE Zielbreite auf yuv420p-Material (4:2:0,
    Chroma-Subsampling) standardmaessig auf die naechste GERADE Zahl ab - aber
    NUR dort, wo tatsaechlich etwas weggeschnitten wird. Bei
    ``AVATAR_SCALE_WIDTH=747`` (ungerade) betraf das den Leinwandzweig
    (``crop=747:...`` aus 1080 Breite - ein echter Ausschnitt, abgerundet auf
    746) NICHT aber den Avatarzweig (der zweite ``crop=747:...`` dort schneidet
    aus einem bereits exakt 747 breiten skalierten Bild - keine echte
    Verkleinerung, deshalb keine Abrundung). Ergebnis ohne ``exact=1``:
    ``blend`` zwischen 746 und 747 Pixel breiten Eingaengen scheitert mit
    ``Failed to configure output pad`` - ffmpeg schreibt gar keine Ausgabe.
    Bei den fruehreren, stets GERADEN Werten (930, 830) trat das nie auf.
    """
    if canvas_frame_count <= 0:
        raise ValueError("canvas_frame_count muss positiv sein")
    if avatar_frame_count <= 0:
        raise ValueError("avatar_frame_count muss positiv sein")
    if avatar_start_frame < 0 or avatar_end_frame <= avatar_start_frame:
        raise ValueError("avatar_start_frame/avatar_end_frame muessen eine positive Spanne bilden")
    if avatar_start_frame >= avatar_frame_count:
        raise ValueError("avatar_start_frame liegt ausserhalb der vorhandenen Avatar-Framezahl")

    geometry = compute_avatar_crop_geometry(avatar_source_width, avatar_source_height)

    # Geklemmt auf das Ende der Avatardatei UND auf die maximal benoetigte
    # Laenge (Leinwandlaenge) - ein einziger trim deckt beide Faelle ab.
    clipped_end_frame = min(
        avatar_end_frame, avatar_frame_count, avatar_start_frame + canvas_frame_count
    )
    segment_frame_count = clipped_end_frame - avatar_start_frame

    avatar_parts = ["[1:v]"]
    avatar_parts.append(
        f"trim=start_frame={avatar_start_frame}:end_frame={clipped_end_frame},"
        "setpts=PTS-STARTPTS,"
    )
    if segment_frame_count < canvas_frame_count:
        pad_frames = canvas_frame_count - segment_frame_count
        avatar_parts.append(f"tpad=stop={pad_frames}:stop_mode=clone,")
    avatar_parts.append(
        f"crop={geometry.crop_width}:{geometry.crop_height}:{geometry.crop_x}:{geometry.crop_y}:"
        "exact=1,"
        f"scale={geometry.scale_width}:{geometry.scale_height},"
        f"crop={geometry.final_width}:{geometry.final_height}:0:0:exact=1,"
        # format=rgb24 auf BEIDEN Zweigen ist notwendig, nicht kosmetisch -
        # siehe Modul-Docstring und der Befund aus shorts-stufe-5d.
        "format=rgb24[avatar_final]"
    )
    # ``canvas_base`` bleibt UNKONVERTIERT (kein format=rgb24 auf dem ganzen
    # Bild) - nur der kleine Ausschnitt, der tatsaechlich geblendet wird,
    # durchlaeuft die RGB-Umwandlung. So bleiben alle Pixel ausserhalb des
    # Avatarbands exakt so, wie sie aus der Leinwand dekodiert wurden -
    # overlay setzt den geblendeten Ausschnitt nur an seiner Stelle ein.
    video = (
        "[0:v]split=2[canvas_base][canvas_crop_src];"
        f"[canvas_crop_src]crop={geometry.final_width}:{geometry.final_height}:"
        f"{AVATAR_PLACE_X}:{AVATAR_PLACE_Y}:exact=1,format=rgb24[canvas_band];"
        f"{''.join(avatar_parts)};"
        "[canvas_band][avatar_final]blend=all_mode=lighten:shortest=1[band_blended];"
        f"[canvas_base][band_blended]overlay=x={AVATAR_PLACE_X}:y={AVATAR_PLACE_Y}[outv]"
    )
    return video, "[outv]", "0:a"


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    canvas_path: Path,
    avatar_path: Path,
    output_path: Path,
    *,
    canvas_frame_count: int,
    avatar_frame_count: int,
    avatar_start_frame: int,
    avatar_end_frame: int,
    avatar_source_width: int,
    avatar_source_height: int,
    fps: int = CANVAS_FPS,
) -> list[str]:
    """Vollstaendiges ffmpeg-Kommando: Avatar auf die Leinwand legen."""
    filter_complex, video_label, audio_label = build_ffmpeg_filter_complex(
        canvas_frame_count=canvas_frame_count,
        avatar_frame_count=avatar_frame_count,
        avatar_start_frame=avatar_start_frame,
        avatar_end_frame=avatar_end_frame,
        avatar_source_width=avatar_source_width,
        avatar_source_height=avatar_source_height,
    )
    return [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(canvas_path),
        "-i",
        str(avatar_path),
        "-filter_complex",
        filter_complex,
        "-map",
        video_label,
        "-map",
        audio_label,
        "-r",
        f"{float(fps):g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        # yuv420p ausdruecklich erzwingen: das vorangehende format=rgb24 vor
        # blend laesst libx264 sonst 4:4:4 statt 4:2:0 waehlen - siehe
        # Modul-Docstring.
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        str(output_path),
    ]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded ffmpeg-Prozessausgang - eigenstaendig, analog zu ``canvas``.

    ``achsenabweichung_frames``/``achsenabweichung_hinweis`` (Auftrag
    shorts-achsenpruefung-warnung) sind nur auf dem von :func:`run_stage5b`
    zurueckgegebenen Erfolgsergebnis gesetzt - der rohe ffmpeg-Lauf
    (:func:`run_avatar_canvas`) kennt die Achsenpruefung nicht und laesst beide
    Felder auf ihrem Vorgabewert.
    """

    exit_code: int
    stderr: bytes
    achsenabweichung_frames: int = 0
    achsenabweichung_hinweis: str | None = None


ProcessRunner = Callable[[Sequence[str], int], ProcessResult]


def _default_process_runner(arguments: Sequence[str], timeout_seconds: int) -> ProcessResult:
    """Fuehre ein Kommando mit begrenzter Diagnoseausgabe aus - der reale Standardweg."""
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessResult(-1, str(exc).encode("utf-8", errors="replace"))
    return ProcessResult(result.returncode, result.stdout or b"")


def run_avatar_canvas(
    *,
    canvas_path: Path,
    avatar_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    canvas_frame_count: int,
    avatar_frame_count: int,
    avatar_start_frame: int,
    avatar_end_frame: int,
    avatar_source_width: int,
    avatar_source_height: int,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
    fps: int = CANVAS_FPS,
) -> ProcessResult:
    """Fuehre das Platzieren tatsaechlich per ffmpeg aus."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments = build_ffmpeg_arguments(
        ffmpeg_path,
        canvas_path,
        avatar_path,
        output_path,
        canvas_frame_count=canvas_frame_count,
        avatar_frame_count=avatar_frame_count,
        avatar_start_frame=avatar_start_frame,
        avatar_end_frame=avatar_end_frame,
        avatar_source_width=avatar_source_width,
        avatar_source_height=avatar_source_height,
        fps=fps,
    )
    return process_runner(arguments, timeout_seconds)


# ---------------------------------------------------------------------------
# Eingabe pruefen - fail closed statt an falscher Stelle zu platzieren.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stage5bFailed:
    """Fail-closed Auskunft, warum kein Avatar auf die Leinwand gelegt werden konnte."""

    code: str
    message_de: str


class AvatarCoverageError(Exception):
    """Fail-closed Fehler beim Lesen von ``coverage.missing_frames_front``.

    Auftrag shorts-avatar-versatz, Teil A - eigener Fehlercode je Ursache, kein stiller
    Rueckfall.
    """

    def __init__(self, code: str, message_de: str) -> None:
        """Trage Fehlercode und deutschsprachige Meldung ein."""
        super().__init__(message_de)
        self.code = code
        self.message_de = message_de


def _read_avatar_coverage_field(avatar_path: Path, field_name: str) -> int:
    """Lies ein Ganzzahlfeld aus ``coverage`` in der Stufe-1-Seitendatei neben ``avatar_path``.

    Gemeinsame Herleitung fuer ``missing_frames_front`` UND ``missing_frames_back``
    (Auftrag shorts-avatar-endversatz) - dieselbe Seitendatei, dieselben Fehlercodes bei
    fehlender Datei, fehlendem Feld oder negativem Wert. Stufe 1 hat beide Werte gemessen
    und ist abgenommen; sie werden hier NICHT neu hergeleitet oder nachgerechnet. Fail
    closed statt still auf 0 zurueckzufallen - ein stiller Nullwert waere genau der Fehler,
    den Auftrag shorts-avatar-versatz behoben hat.
    """
    sidecar_path = avatar_path.parent / f"{avatar_path.stem}.json"
    if not sidecar_path.is_file():
        raise AvatarCoverageError(
            "avatar_coverage_sidecar_missing",
            f"Seitendatei fehlt: {sidecar_path} - coverage.{field_name} kann nicht "
            "gelesen werden",
        )
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AvatarCoverageError(
            "avatar_coverage_sidecar_unreadable",
            f"Seitendatei {sidecar_path} konnte nicht gelesen/geparst werden: {exc}",
        ) from exc
    coverage = payload.get("coverage") if isinstance(payload, dict) else None
    value = coverage.get(field_name) if isinstance(coverage, dict) else None
    if not isinstance(value, int) or isinstance(value, bool):
        raise AvatarCoverageError(
            "avatar_coverage_field_missing",
            f"coverage.{field_name} fehlt oder ist keine Ganzzahl in {sidecar_path}",
        )
    if value < 0:
        raise AvatarCoverageError(
            "avatar_coverage_field_negative",
            f"coverage.{field_name} ist negativ ({value}) in {sidecar_path} - das "
            "kann nicht stimmen",
        )
    return value


def read_avatar_coverage_missing_frames_front(avatar_path: Path) -> int:
    """Lies ``coverage.missing_frames_front`` aus der Stufe-1-Seitendatei neben ``avatar_path``.

    Die Seitendatei (``<avatar_path>.json``, von Stufe 1/``avatar_cut.py`` geschrieben)
    traegt den gemessenen Versatz zwischen der gerenderten und der Avatar-Achse - siehe
    Modul-Docstring des Auftrags. Stufe 1 hat diesen Wert gemessen und ist abgenommen; er
    wird hier NICHT neu hergeleitet oder nachgerechnet. Fehlt die Datei, fehlt das Feld,
    oder ist der Wert negativ, wird fail closed mit eigenem Fehlercode gemeldet statt still
    auf 0 zurueckzufallen - ein stiller Nullwert waere genau der Fehler, den dieser Auftrag
    behebt.
    """
    return _read_avatar_coverage_field(avatar_path, "missing_frames_front")


def read_avatar_coverage_missing_frames_back(avatar_path: Path) -> int:
    """Lies ``coverage.missing_frames_back`` aus der Stufe-1-Seitendatei neben ``avatar_path``.

    Auftrag shorts-avatar-endversatz: der hintere Versatz (Source Record stoppt minimal
    frueher als die Hauptaufnahme) ist der Normalfall, nicht der Fehler - Stufe 1 hat ihn
    gemessen und in ``trailing_edge``/``coverage.missing_frames_back`` festgehalten. Diese
    Funktion liest ihn nur, mit denselben Fehlercodes wie
    :func:`read_avatar_coverage_missing_frames_front` (siehe :func:`_read_avatar_coverage_field`).
    """
    return _read_avatar_coverage_field(avatar_path, "missing_frames_back")


def _probe_stream_start_time(
    video_path: Path,
    select_stream: str,
    *,
    ffprobe_path: Path,
    timeout_seconds: int,
) -> float | None:
    """Lies ``start_time`` einer einzelnen Spur (z. B. ``v:0``); ``None`` bei Fehlern."""
    try:
        result = subprocess.run(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                select_stream,
                "-show_entries",
                "stream=start_time",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="ignore").strip()
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Teil C: Ausgabe pruefen, vier unabhaengige Pruefungen mit je eigenem
# Fehlercode - nach dem Muster von canvas.py/chart_crop.py/endcard.py.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyChecks:
    """Istwerte der vier Pruefungen aus Teil C, unabhaengig vom Ergebnis erhoben."""

    canvas_frame_count: int | None
    output_frame_count: int | None
    frame_count_ok: bool

    actual_width: int | None
    actual_height: int | None
    dimensions_ok: bool

    audio_track_count: int | None
    audio_track_count_ok: bool

    video_start_time: float | None
    audio_start_time: float | None
    start_time_ok: bool

    @property
    def all_ok(self) -> bool:
        """Alle vier Pruefungen bestanden."""
        return (
            self.frame_count_ok
            and self.dimensions_ok
            and self.audio_track_count_ok
            and self.start_time_ok
        )

    @property
    def first_failure_code(self) -> str | None:
        """Fehlercode der ersten gefallenen Pruefung, ``None`` wenn alle bestanden."""
        if not self.frame_count_ok:
            return "frame_count_mismatch"
        if not self.dimensions_ok:
            return "dimension_mismatch"
        if not self.audio_track_count_ok:
            return "audio_track_count_invalid"
        if not self.start_time_ok:
            return "start_time_nonzero"
        return None


def verify_avatar_canvas_output(
    canvas_path: Path,
    output_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 120,
) -> VerifyChecks:
    """Erhebe alle vier Istwerte aus Teil C, unabhaengig davon, ob einer schon gefallen ist."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()

    canvas_frame_count = probe_frame_count(
        canvas_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    output_frame_count = probe_frame_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    frame_count_ok = (
        canvas_frame_count is not None and canvas_frame_count == output_frame_count
    )

    dimensions = probe_dimensions(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    actual_width, actual_height = dimensions if dimensions is not None else (None, None)
    dimensions_ok = dimensions == (CANVAS_WIDTH, CANVAS_HEIGHT)

    audio_track_count = probe_audio_track_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    audio_track_count_ok = audio_track_count == 1

    if ffprobe is None:
        video_start_time = None
        audio_start_time = None
    else:
        video_start_time = _probe_stream_start_time(
            output_path, "v:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
        )
        audio_start_time = _probe_stream_start_time(
            output_path, "a:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
        )
    start_time_ok = video_start_time == 0.0 and audio_start_time == 0.0

    return VerifyChecks(
        canvas_frame_count=canvas_frame_count,
        output_frame_count=output_frame_count,
        frame_count_ok=frame_count_ok,
        actual_width=actual_width,
        actual_height=actual_height,
        dimensions_ok=dimensions_ok,
        audio_track_count=audio_track_count,
        audio_track_count_ok=audio_track_count_ok,
        video_start_time=video_start_time,
        audio_start_time=audio_start_time,
        start_time_ok=start_time_ok,
    )


def avatar_canvas_report_payload(checks: VerifyChecks) -> dict[str, object]:
    """Baue den Inhalt des Laufberichts, nach dem Muster von ``canvas-report.json``."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_avatar_canvas",
        "schema_version": AVATAR_CANVAS_REPORT_SCHEMA_VERSION,
        "checks": {
            "frame_count": {
                "canvas": checks.canvas_frame_count,
                "output": checks.output_frame_count,
                "ok": checks.frame_count_ok,
            },
            "dimensions": {
                "expected": [CANVAS_WIDTH, CANVAS_HEIGHT],
                "actual": [checks.actual_width, checks.actual_height],
                "ok": checks.dimensions_ok,
            },
            "audio_track_count": {
                "expected": 1,
                "actual": checks.audio_track_count,
                "ok": checks.audio_track_count_ok,
            },
            "start_time": {
                "video": checks.video_start_time,
                "audio": checks.audio_start_time,
                "ok": checks.start_time_ok,
            },
        },
        "all_ok": checks.all_ok,
    }


def write_avatar_canvas_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe den Laufbericht atomar - dasselbe Muster wie ``canvas``/``endcard``."""
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


def run_stage5b(
    *,
    canvas_path: Path,
    avatar_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    canvas_recording_id: str,
    avatar_recording_id: str,
    candidate_start_ms: int,
    candidate_end_ms: int,
    expected_avatar_frame_count: int | None = None,
    avatar_frame_count: int | None = None,
    avatar_source_width: int | None = None,
    avatar_source_height: int | None = None,
    rendered_video_path: Path | None = None,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage5bFailed:
    """Ende-zu-Ende: Eingaben pruefen, Avatar auf die Leinwand legen, Ergebnis pruefen.

    ``expected_avatar_frame_count`` ist optional (Auftrag shorts-bau, Punkt 2):
    fehlt er, misst dieser Lauf ihn selbst per ffprobe -count_frames auf
    ``rendered_video_path`` (dann Pflicht). Keine andere Aenderung an diesem
    Modul, insbesondere keine an Geometrie oder Filtergraph.

    ``avatar_frame_count``/``avatar_source_width``/``avatar_source_height``
    sind ebenfalls optional (Auftrag shorts-framezahl-cache): ``avatar_path``
    ist je Lauf IMMER dieselbe Datei - Framezahl und Aufloesung sind also je
    Aufnahme konstant, nicht je Kandidat verschieden. Sind sie gesetzt, misst
    dieser Lauf sie NICHT erneut per ffprobe, sondern uebernimmt die
    uebergebenen Werte direkt. Fehlt (auch nur) einer der drei, misst dieser
    Lauf ihn selbst - das heutige Verhalten bleibt fuer alle Aufrufer ohne
    diese Parameter (insbesondere die CLI unten) unveraendert, das Modul ist
    weiterhin allein benutzbar. Was hier NICHT zusammengefasst wird: die
    Framezahl-/Massepruefungen auf ``canvas_path`` und ``output_path`` (Teil
    C unten) - das sind Pruefungen auf die AUSGABE eines Kandidaten und je
    Kandidat verschieden.

    Schreibt nach einem erfolgreichen ffmpeg-Lauf einen Laufbericht neben die
    Ausgabe (``<output>.json``) mit den vier Pruefergebnissen aus Teil C. Ist
    eine der vier Pruefungen gefallen, ist das Ergebnis ``Stage5bFailed`` mit
    dem passenden, eigenstaendigen Fehlercode - der Bericht wird trotzdem
    geschrieben, damit der Befund nachvollziehbar bleibt.

    Zwei zusaetzliche, fail-closed Pruefungen VOR jedem ffmpeg-Lauf (Auftrag
    shorts-5b-5c-nachbesserung, Teil A - der urspruengliche Pruefstein hat
    Leinwand und Avatar aus zwei verschiedenen Aufnahmen zusammengesetzt,
    genau das darf nicht mehr passieren):

    1. ``canvas_recording_id`` und ``avatar_recording_id`` muessen
       identisch sein - Leinwand und Avatar MUESSEN aus derselben Aufnahme
       stammen, sonst ``recording_mismatch``. Der Aufrufer traegt die
       Aufnahme-Identitaet explizit ein (z. B. ``video_name`` aus
       ``shorts-job.json``) - dieses Modul rät nicht anhand von Dateinamen.
    2. Avatar-Framezahl PLUS der aus der Stufe-1-Seitendatei gelesene
       ``coverage.missing_frames_front``-Versatz muss der erwarteten
       Videolaenge entsprechen (Auftrag shorts-avatar-versatz, Teil C -
       ``avatar-cut.mp4`` beginnt an der Vorderkante um diesen Versatz
       SPAETER als das gerenderte Video, die beiden Achsen sind NICHT
       deckungsgleich), sonst ``avatar_frame_count_axis_mismatch`` - ohne
       diese Pruefung wuerde ein falsch zugeordneter Avatar unbemerkt an der
       falschen Stelle geschnitten.

    Zwei weitere fail-closed Pruefungen (Auftrag shorts-avatar-versatz):

    3. ``coverage.missing_frames_front`` wird aus der Seitendatei neben
       ``avatar_path`` gelesen (Teil A) - fehlt sie, fehlt das Feld, oder ist
       der Wert negativ, wird NICHT still auf 0 zurueckgefallen, siehe
       :func:`read_avatar_coverage_missing_frames_front`.
    4. Die Kandidatenspanne (auf der gerenderten Achse) wird um
       ``missing_frames_front`` auf die Avatar-Achse verschoben, bevor aus
       der Avatardatei geschnitten wird (Teil B). Wird die verschobene
       Startgrenze dadurch negativ, liegt der Kandidat vor dem Beginn der
       Avatardatei, sonst ``candidate_precedes_avatar_start``.

    Auftrag shorts-avatar-endversatz - der hintere Versatz (Source Record
    stoppt minimal frueher als die Hauptaufnahme, siehe Modul-Docstring) ist
    der Normalfall, kein Fehler. Er wird deshalb erst hier, unabhaengig von
    Punkt 2 oben, geprueft:

    5. Die Achsenpruefung aus Punkt 2 wird um ``missing_frames_back``
       ergaenzt: ``avatar_frame_count + missing_frames_front +
       missing_frames_back`` muss der erwarteten Videolaenge entsprechen -
       die vorherige Pruefung (nur ``missing_frames_front``) ignorierte den
       hinteren Versatz und schlug deshalb bei jeder Aufnahme fehl, bei der
       er ungleich 0 ist. Seit Auftrag shorts-achsenpruefung-warnung
       (2026-08-19) ist eine Abweichung bis einschliesslich
       :data:`ACHSENABWEICHUNG_MAX_FRAMES` KEIN Abbruch mehr, sondern eine
       Warnung: der Kandidat wird gebaut, die Abweichung steht auf dem
       zurueckgegebenen :class:`ProcessResult` (``achsenabweichung_frames``/
       ``achsenabweichung_hinweis``). Erst eine groessere Abweichung bricht
       weiterhin mit ``avatar_frame_count_axis_mismatch`` ab - siehe
       :data:`ACHSENABWEICHUNG_MAX_FRAMES` fuer den Grund der Grenze.
    6. Reicht die (verschobene) Kandidatenspanne ueber das Ende der
       Avatardatei hinaus - ``avatar_end_frame > avatar_frame_count`` -, wird
       NICHT stillschweigend mit einem stehenden letzten Bild gebaut
       (``tpad`` in :func:`build_ffmpeg_filter_complex` wuerde das sonst
       lautlos tun). Stattdessen fail closed mit
       ``candidate_span_touches_missing_frames_back`` - dieser eine Kandidat
       wird nicht gebaut, die uebrigen laufen weiter. Ein Kandidat, dessen
       Spanne den fehlenden Bereich nicht beruehrt, ist davon unberuehrt.
    """
    if canvas_recording_id != avatar_recording_id:
        return Stage5bFailed(
            "recording_mismatch",
            "Leinwand und Avatar stammen aus verschiedenen Aufnahmen: "
            f"{canvas_recording_id!r} != {avatar_recording_id!r} - beide muessen aus derselben "
            "Aufnahme stammen",
        )

    if expected_avatar_frame_count is None:
        if rendered_video_path is None:
            return Stage5bFailed(
                "expected_avatar_frame_count_missing",
                "expected_avatar_frame_count fehlt und rendered_video_path wurde nicht "
                "uebergeben - ohne eines von beiden kann die erwartete Framezahl nicht "
                "bestimmt werden",
            )
        measured = probe_frame_count(
            rendered_video_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
        )
        if measured is None:
            return Stage5bFailed(
                "rendered_video_frame_count_unknown",
                f"ffprobe konnte die Framezahl des gerenderten Videos nicht ermitteln: "
                f"{rendered_video_path}",
            )
        expected_avatar_frame_count = measured

    canvas_dimensions = probe_dimensions(
        canvas_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if canvas_dimensions is None:
        return Stage5bFailed(
            "canvas_resolution_unknown",
            f"ffprobe konnte die Leinwand-Aufloesung nicht ermitteln: {canvas_path}",
        )
    if canvas_dimensions != (CANVAS_WIDTH, CANVAS_HEIGHT):
        return Stage5bFailed(
            "canvas_resolution_mismatch",
            f"Leinwandgroesse {canvas_dimensions[0]}x{canvas_dimensions[1]} weicht von "
            f"{CANVAS_WIDTH}x{CANVAS_HEIGHT} ab - die Geometrie dieses Moduls setzt sie voraus",
        )

    if avatar_source_width is None or avatar_source_height is None:
        avatar_dimensions = probe_dimensions(
            avatar_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
        )
        if avatar_dimensions is None:
            return Stage5bFailed(
                "avatar_resolution_unknown",
                f"ffprobe konnte die Avatar-Aufloesung nicht ermitteln: {avatar_path}",
            )
        avatar_source_width, avatar_source_height = avatar_dimensions
    # Auftrag shorts-avatar-1920, Teil B: die Quellaufloesung ist nicht mehr fest verdrahtet -
    # statt auf 630x422 zu bestehen, wird hier nur das Seitenverhaeltnis gegen die beiden
    # GEMESSENEN Faelle geprueft (siehe compute_avatar_crop_geometry). Ein drittes,
    # ungemessenes Format faellt hier fail closed durch, statt an falscher Stelle zu schneiden.
    try:
        compute_avatar_crop_geometry(avatar_source_width, avatar_source_height)
    except AvatarSourceGeometryError as exc:
        return Stage5bFailed(exc.code, exc.message_de)

    canvas_frame_count = probe_frame_count(
        canvas_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if canvas_frame_count is None:
        return Stage5bFailed(
            "canvas_frame_count_unknown",
            f"ffprobe konnte die Leinwand-Framezahl nicht ermitteln: {canvas_path}",
        )
    if avatar_frame_count is None:
        avatar_frame_count = probe_frame_count(
            avatar_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
        )
        if avatar_frame_count is None:
            return Stage5bFailed(
                "avatar_frame_count_unknown",
                f"ffprobe konnte die Avatar-Framezahl nicht ermitteln: {avatar_path}",
            )
    try:
        missing_frames_front = read_avatar_coverage_missing_frames_front(avatar_path)
        missing_frames_back = read_avatar_coverage_missing_frames_back(avatar_path)
    except AvatarCoverageError as exc:
        return Stage5bFailed(exc.code, exc.message_de)

    # Teil C (Auftrag shorts-avatar-versatz), berichtigt in Auftrag shorts-avatar-endversatz:
    # die Avatardatei beginnt an der Vorderkante um missing_frames_front SPAETER als das
    # gerenderte Video UND endet an der Hinterkante um missing_frames_back FRUEHER (Source
    # Record stoppt minimal frueher als die Hauptaufnahme, siehe Modul-Docstring - der
    # Normalfall, kein Fehler). "Avatarframezahl + missing_frames_front == erwartete
    # Videolaenge" ignorierte den hinteren Versatz und schlug deshalb bei jeder Aufnahme fehl,
    # bei der er ungleich 0 ist (genau der Befund dieses Auftrags). Richtig ist Avatarframezahl
    # + missing_frames_front + missing_frames_back == erwartete Videolaenge.
    achsenabweichung_frames = (
        avatar_frame_count + missing_frames_front + missing_frames_back
    ) - expected_avatar_frame_count
    if abs(achsenabweichung_frames) > ACHSENABWEICHUNG_MAX_FRAMES:
        return Stage5bFailed(
            "avatar_frame_count_axis_mismatch",
            f"Avatar-Framezahl {avatar_frame_count} + missing_frames_front "
            f"{missing_frames_front} + missing_frames_back {missing_frames_back} = "
            f"{avatar_frame_count + missing_frames_front + missing_frames_back} weicht um "
            f"{achsenabweichung_frames:+d} Frame(s) von der erwarteten Videolaenge "
            f"{expected_avatar_frame_count} ab - das ueberschreitet die Toleranz von "
            f"{ACHSENABWEICHUNG_MAX_FRAMES} Frame(s) (siehe ACHSENABWEICHUNG_MAX_FRAMES): "
            "avatar-cut.mp4 muss (nach Ausgleich beider Versaetze) im Rahmen der Toleranz "
            "dieselbe Framezahl wie das gerenderte Video auf derselben Achse ergeben",
        )
    achsenabweichung_hinweis = (
        None
        if achsenabweichung_frames == 0
        else (
            f"Achsenabweichung {achsenabweichung_frames:+d} Frame(s) zwischen "
            "Proposal-Arithmetik und gemessener Avatar-Framezahl (Toleranz "
            f"{ACHSENABWEICHUNG_MAX_FRAMES}, siehe ACHSENABWEICHUNG_MAX_FRAMES) - Warnung, "
            "kein Abbruch"
        )
    )

    try:
        rendered_start_frame, rendered_end_frame = candidate_frame_span(
            candidate_start_ms, candidate_end_ms, CANVAS_FPS
        )
    except ValueError as exc:
        return Stage5bFailed("invalid_candidate_span", str(exc))

    # Teil B: candidate_frame_span liefert die Spanne auf der GERENDERTEN Achse - vor dem
    # Zuschnitt aus der Avatardatei wird von beiden Grenzen missing_frames_front abgezogen
    # (die Avatardatei beginnt spaeter, ihre Frame-0 entspricht gerendertem Frame
    # missing_frames_front). Wird eine Grenze dadurch negativ, liegt der Kandidat vor dem
    # Beginn der Avatardatei - fail closed, nicht auf 0 klemmen.
    avatar_start_frame = rendered_start_frame - missing_frames_front
    avatar_end_frame = rendered_end_frame - missing_frames_front
    if avatar_start_frame < 0:
        return Stage5bFailed(
            "candidate_precedes_avatar_start",
            f"Kandidat beginnt bei gerendertem Frame {rendered_start_frame}, um "
            f"missing_frames_front={missing_frames_front} verschoben ergibt das "
            f"{avatar_start_frame} < 0 - der Kandidat liegt vor dem Beginn der Avatardatei",
        )
    if avatar_start_frame >= avatar_frame_count:
        return Stage5bFailed(
            "candidate_span_outside_avatar",
            f"Kandidatenspanne beginnt bei Frame {avatar_start_frame}, Avatar hat nur "
            f"{avatar_frame_count} Frames - die Spanne liegt ausserhalb der Avatardatei",
        )

    # Punkt 6 (Auftrag shorts-avatar-endversatz): der Kandidat beginnt innerhalb der
    # Avatardatei (sonst waere er oben schon abgelehnt worden), reicht aber am Ende in den
    # fehlenden Bereich hinein (avatar_end_frame > avatar_frame_count). build_ffmpeg_filter_complex
    # wuerde die fehlende Differenz per tpad lautlos mit dem letzten Bild auffuellen - genau das
    # ist hier NICHT erlaubt: fail closed statt mit einem stehenden letzten Bild zu bauen. Die
    # uebrigen Kandidaten sind davon unberuehrt.
    if avatar_end_frame > avatar_frame_count:
        return Stage5bFailed(
            "candidate_span_touches_missing_frames_back",
            f"Kandidatenspanne endet bei Avatar-Frame {avatar_end_frame}, Avatar hat nur "
            f"{avatar_frame_count} Frames (fehlender Bereich am Ende: {missing_frames_back} "
            "Frames) - der Kandidat wuerde sonst mit einem stehenden letzten Bild gebaut",
        )

    process_result = run_avatar_canvas(
        canvas_path=canvas_path,
        avatar_path=avatar_path,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        canvas_frame_count=canvas_frame_count,
        avatar_frame_count=avatar_frame_count,
        avatar_start_frame=avatar_start_frame,
        avatar_end_frame=avatar_end_frame,
        avatar_source_width=avatar_source_width,
        avatar_source_height=avatar_source_height,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    if process_result.exit_code != 0:
        return process_result

    checks = verify_avatar_canvas_output(
        canvas_path, output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    write_avatar_canvas_report(report_path, avatar_canvas_report_payload(checks))
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return Stage5bFailed(
            failure_code,
            f"Pruefung '{failure_code}' fehlgeschlagen, Bericht: {report_path}",
        )
    process_result = replace(
        process_result,
        achsenabweichung_frames=achsenabweichung_frames,
        achsenabweichung_hinweis=achsenabweichung_hinweis,
    )
    return process_result


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: eine nachgeschnittene Avatardatei auf eine fertige Leinwand legen."""
    import argparse

    parser = argparse.ArgumentParser(description="Stufe 5b: Avatar auf die Leinwand")
    parser.add_argument("canvas_path", type=Path)
    parser.add_argument("avatar_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--canvas-recording", type=str, required=True)
    parser.add_argument("--avatar-recording", type=str, required=True)
    parser.add_argument("--candidate-start-ms", type=int, required=True)
    parser.add_argument("--candidate-end-ms", type=int, required=True)
    parser.add_argument("--expected-avatar-frame-count", type=int, default=None)
    parser.add_argument(
        "--rendered-video",
        type=Path,
        default=None,
        help="Pflicht, wenn --expected-avatar-frame-count fehlt: wird dann per ffprobe gemessen",
    )
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    result = run_stage5b(
        canvas_path=args.canvas_path,
        avatar_path=args.avatar_path,
        output_path=args.output,
        ffmpeg_path=Path(ffmpeg_path),
        canvas_recording_id=args.canvas_recording,
        avatar_recording_id=args.avatar_recording,
        candidate_start_ms=args.candidate_start_ms,
        candidate_end_ms=args.candidate_end_ms,
        expected_avatar_frame_count=args.expected_avatar_frame_count,
        rendered_video_path=args.rendered_video,
        ffprobe_path=args.ffprobe,
    )
    if isinstance(result, Stage5bFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.exit_code != 0:
        print(f"ffmpeg fehlgeschlagen: {result.stderr.decode('utf-8', errors='replace')}")
        return 1
    print(f"geschrieben: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
