"""Zweistufige Lautheitsangleichung des Produktrenders auf ein Sendeziel.

Die Renderkette kodiert den Ton ohnehin neu (``render._render_arguments``:
``-c:a aac -ar 48000``, kein ``-c copy``), und es gibt genau eine Tonspur --
``amix`` und ``amerge`` kommen im ganzen Produktivcode nicht vor.  Die
Angleichung ist deshalb ein Filter mehr im bestehenden Audiozweig und keine
zusätzliche Kodierstufe.

Gemessen wird in einem eigenen Durchgang **durch dieselben Schnitte**, durch die
auch der Render läuft.  Der Schnitt verschiebt den integrierten Wert -- am Lauf
2026-08-09 08-43-22 um +0,36 dB, weil mit 13,3 s Stille auch leises Material
wegfällt.  Eine Messung an der ungeschnittenen Quelle misst darum die falsche
Datei.

Alle Zahlen hier sind **Startwerte**.  Sie werden am gerenderten Ergebnis
nachjustiert, nicht hergeleitet -- wie die Konstanten in
:mod:`matrix_auto_cutter.intro`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel

# YouTube normalisiert auf -14 LUFS und regelt dabei nur herunter, nie herauf.
# Gemessen an den fertigen Rendern vor der Angleichung: -30,96 LUFS (Lauf
# 2026-08-08 07-28-18), -31,78 (2026-08-09 08-43-22), -35,14 (2026-08-09
# 08-14-05) und -35,74 bis -35,85 in der Messreihe vom 06.08.2026
# (``artefakte\repeat\lautheit\``).  Das sind 17 bis 21 dB zu leise.
#
# Das Ergebnis landet regelmäßig etwas unter dem Ziel -- abgenommener Lauf
# 2026-08-09 12-09-50: -14,34 LUFS.  Das Ziel wird deshalb NICHT nachgezogen:
# Der Ausgang ist spitzengebunden, nicht zielgebunden.  loudnorm und der
# Limiter halten den True Peak, und der fehlende Rest Lautheit ist genau der
# Preis dafür.  Ein höheres Ziel bringt keine Lautheit, sondern nur mehr
# Pegelreduktion durch den Limiter.
TARGET_I_LUFS = -14.0

# Zielspitze von loudnorm.  Der eigentliche Schutz ist der Limiter am Ende der
# Kette; dieser Wert hält den Regler davor schon in der Nähe.
TARGET_TP_DBTP = -1.0

# Gemessener Umfang der fertigen Datei: 8,0 LU über den ganzen Lauf
# 2026-08-08 07-28-18, 6,2 LU im reinen Gesprächsfenster 08:00-12:00 desselben
# Laufs.  Das Ziel liegt bewusst darüber, damit loudnorm den Umfang des
# Gesprochenen nicht zusammenzieht und nur die Ausreißer einfängt.
TARGET_LRA_LU = 11.0

# Der Kompressor sitzt VOR loudnorm und existiert nur, damit loudnorm linear
# arbeiten kann: ohne ihn verlangt die nötige Anhebung von rund +17 dB mehr
# Spielraum, als die Datei hat (True Peak -0,55 dBTP am Lauf 07-28-18), und
# loudnorm fällt auf den dynamischen Modus zurück.
#
# Der Sprach-RMS liegt bei -33 bis -39 dBFS (gemessene Fenster desselben Laufs).
# Die Schwelle liegt darüber und greift damit nicht am Gesprochenen, sondern an
# den Transienten: 20 Stellen über -8 dBFS, angeführt von einem einzelnen
# Ereignis bei 03:15,2 mit -0,6 dBTP und 21 dB über der umgebenden Sprache.
#
# BEOBACHTUNG, nicht nachjustiert: Der Kompressor arbeitet nur dort kräftig, wo
# ein echter Ausreißer über der Schwelle liegt.  Gemessen als Crestgewinn
# (Abstand True Peak zu integrierter Lautheit, vorher gegen nachher) und
# Pegelverlust:
#
#   Lauf 2026-08-08 07-28-18   4,93 dB Crestgewinn für 1,90 dB Pegelverlust
#   Lauf 2026-08-09 08-43-22   0,84 dB Crestgewinn für 2,97 dB Pegelverlust
#
# Im ersten Lauf liegt das Einzelereignis bei 03:15,2 über der Schwelle, im
# zweiten liegt nichts darüber -- dort kostet der Kompressor mehr Pegel, als er
# an Spielraum einbringt.  Verdacht: 5 ms Attack gegen 120 ms Release bei einer
# Schwelle von -24 dB; die lange Rückstellzeit hält die Absenkung über
# Passagen, in denen gar kein Ausreißer mehr ist.  Nur festgehalten, bis eine
# Messreihe über mehrere Läufe vorliegt.
COMP_THRESHOLD_DB = -24.0
COMP_RATIO = 4.0
COMP_ATTACK_MS = 5.0
COMP_RELEASE_MS = 120.0
COMP_KNEE_DB = 6.0

# 0,5 dB Reserve gegen Zwischenwertspitzen bei 48 kHz: der True Peak eines
# rekonstruierten Signals liegt über dem höchsten Abtastwert.
#
# ``alimiter`` nimmt eine lineare Amplitude; das Suffix ``dB`` rechnet ffmpegs
# Ausdrucksauswerter mit 10^(x/20) selbst um.  Am Prüfton nachgemessen: ein
# Sinus mit 0 dBFS verlässt ``limit=-1.5dB`` mit exakt -1,500000 dBFS.
LIMITER_DB = -1.5

# Muss der Ausgabe-Abtastrate des Renders entsprechen (``-ar 48000``).
OUTPUT_SAMPLE_RATE = 48_000

# Abnahmeschranken, geprüft am fertig kodierten Ergebnis -- nicht am Innenleben
# von loudnorm.  ``normalization_type`` taugt dafür nicht: der Modus fällt bei
# jedem realistischen Lauf auf ``dynamic`` zurück, weil ein Sprachsignal mit 17
# bis 21 dB Anhebungsbedarf den für linear nötigen Crest von 13 dB nicht
# erreicht.  Er gehört ins Protokoll, nicht in eine Warnung.
#
# Abweichung der integrierten Lautheit vom Ziel.  Gemessene Ergebnisse:
# -14,34 LUFS (abgenommener Lauf 2026-08-09 12-09-50), -14,60 (07-28-18),
# -15,30 (08-43-22).  Die Streuung liegt bei rund 1 dB; 1,5 dB lässt sie durch
# und fängt einen echten Ausreißer.
ACCEPT_I_TOLERANCE_DB = 1.5

# ``alimiter`` begrenzt den Abtastspitzenwert bei 48 kHz, ``loudnorm`` misst den
# True Peak mit Überabtastung.  Der True Peak liegt darum systematisch ÜBER dem
# Limiterwert: gemessen -1,19 / -1,21 / -1,00 dBTP gegen einen Limiter auf
# -1,5.  Eine Schranke auf dem Limiterwert wäre derselbe Fehler wie
# ``normalization_type`` -- sie würde immer feuern.  -0,5 dBTP lässt rund 0,5 dB
# Luft über dem höchsten je gemessenen Wert und schlägt trotzdem an, bevor
# irgendetwas an die Vollaussteuerung stößt.
ACCEPT_TP_CEILING_DBTP = -0.5

# Untergrenze des Lautheitsumfangs; darunter ist der Ton hörbar
# zusammengedrückt.  Gemessene Ergebnisse: 4,80 LU (07-28-18), 6,60
# (12-09-50), 13,20 (08-43-22).  Die Schranke liegt knapp unter dem niedrigsten
# davon -- sie ist der Wächter gegen das Pumpen, nicht dessen Definition.
ACCEPT_LRA_FLOOR_LU = 4.0


class LoudnessMeasurement(CanonicalModel):
    """Die fünf Messwerte aus Durchgang 1, in den Grenzen von ``loudnorm``.

    Die Grenzen sind die der ``measured_*``-Optionen selbst.  Ein Wert außerhalb
    -- etwa das ``-inf`` einer vollständig stummen Tonspur -- lässt die
    Validierung scheitern, und der Lauf fällt auf einen Durchgang zurück,
    statt FFmpeg mit einem unannehmbaren Argument zu starten.
    """

    input_i: float = Field(ge=-99.0, le=0.0)
    input_lra: float = Field(ge=0.0, le=99.0)
    input_tp: float = Field(ge=-99.0, le=99.0)
    input_thresh: float = Field(ge=-99.0, le=0.0)
    target_offset: float = Field(ge=-99.0, le=99.0)


def _number(value: float) -> str:
    """Format one filter argument stably, without exponent or locale."""
    return f"{value:g}"


def compressor_filter() -> str:
    """Build the transient compressor that both passes must share."""
    return (
        f"acompressor=threshold={_number(COMP_THRESHOLD_DB)}dB"
        f":ratio={_number(COMP_RATIO)}"
        f":attack={_number(COMP_ATTACK_MS)}"
        f":release={_number(COMP_RELEASE_MS)}"
        f":knee={_number(COMP_KNEE_DB)}dB"
    )


def _loudnorm_filter(measured: LoudnessMeasurement | None) -> str:
    parts = [
        f"loudnorm=I={_number(TARGET_I_LUFS)}",
        f"TP={_number(TARGET_TP_DBTP)}",
        f"LRA={_number(TARGET_LRA_LU)}",
    ]
    if measured is not None:
        parts.extend(
            (
                f"measured_I={_number(measured.input_i)}",
                f"measured_LRA={_number(measured.input_lra)}",
                f"measured_TP={_number(measured.input_tp)}",
                f"measured_thresh={_number(measured.input_thresh)}",
                f"offset={_number(measured.target_offset)}",
                "linear=true",
            )
        )
    parts.append("print_format=json")
    return ":".join(parts)


def measurement_chain() -> str:
    """Build pass 1: the render's own compressor, then measure and print JSON."""
    return f"{compressor_filter()},{_loudnorm_filter(None)}"


def render_chain(measured: LoudnessMeasurement | None) -> str:
    """Build pass 2, or the single-pass fallback when pass 1 gave no measurement."""
    return (
        f"{compressor_filter()},"
        f"{_loudnorm_filter(measured)},"
        f"aresample={OUTPUT_SAMPLE_RATE},"
        f"alimiter=limit={_number(LIMITER_DB)}dB:level=false"
    )


def parse_report(output: bytes) -> dict[str, str] | None:
    """Read the last ``print_format=json`` block out of bounded FFmpeg output.

    ``loudnorm`` prints its report at ``AV_LOG_INFO``; both passes therefore run
    at ``-loglevel info -nostats``, which was measured at 26 stderr lines for a
    complete run.  The block carries no nested braces, so the last brace pair is
    the whole report.
    """
    text = output.decode("utf-8", errors="replace")
    closing = text.rfind("}")
    if closing < 0:
        return None
    opening = text.rfind("{", 0, closing)
    if opening < 0:
        return None
    try:
        payload: dict[str, object] = json.loads(text[opening : closing + 1])
    except ValueError:
        return None
    return {str(key): str(value) for key, value in payload.items()}


def measurement_from_report(report: Mapping[str, str]) -> LoudnessMeasurement | None:
    """Convert one parsed report into bounded measured values, or report failure."""
    try:
        return LoudnessMeasurement(
            input_i=float(report["input_i"]),
            input_lra=float(report["input_lra"]),
            input_tp=float(report["input_tp"]),
            input_thresh=float(report["input_thresh"]),
            target_offset=float(report["target_offset"]),
        )
    except (KeyError, ValueError):
        return None


def normalization_type(report: Mapping[str, str]) -> str | None:
    """Report which mode ``loudnorm`` actually ran in, or ``None`` if unstated."""
    applied = report.get("normalization_type")
    return applied if applied in {"linear", "dynamic"} else None


def acceptance_warnings(measured: LoudnessMeasurement) -> tuple[str, ...]:
    """Name every acceptance bound the finished output misses.

    Measured on the rendered file, not derived from the filter's own report:
    what ships is what counts.
    """
    warnings: list[str] = []
    deviation = measured.input_i - TARGET_I_LUFS
    if abs(deviation) > ACCEPT_I_TOLERANCE_DB:
        warnings.append(
            f"Lautheit {measured.input_i:.2f} LUFS weicht um {deviation:+.2f} dB vom Ziel "
            f"{_number(TARGET_I_LUFS)} LUFS ab."
        )
    if measured.input_tp > ACCEPT_TP_CEILING_DBTP:
        warnings.append(
            f"True Peak {measured.input_tp:.2f} dBTP liegt über "
            f"{_number(ACCEPT_TP_CEILING_DBTP)} dBTP."
        )
    if measured.input_lra < ACCEPT_LRA_FLOOR_LU:
        warnings.append(
            f"Lautheitsumfang {measured.input_lra:.2f} LU liegt unter "
            f"{_number(ACCEPT_LRA_FLOOR_LU)} LU; der Ton ist zusammengedrückt."
        )
    return tuple(warnings)


def protocol_line(measured: LoudnessMeasurement | None, applied: str | None) -> str:
    """Describe the finished loudness for runner.log.  Information, not a warning."""
    mode = applied or "unbekannt"
    if measured is None:
        return f"Lautheit: nicht messbar · loudnorm {mode}"
    return (
        f"Lautheit: I {measured.input_i:.2f} LUFS · TP {measured.input_tp:.2f} dBTP · "
        f"LRA {measured.input_lra:.2f} LU · loudnorm {mode}"
    )
