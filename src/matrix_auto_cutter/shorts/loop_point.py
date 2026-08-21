r"""Auftrag shorts-schleifenpunkt: Grenzen auf Wortgrenzen rasten, Schleifentauglichkeit beurteilen.

Ein Short laeuft bei YouTube in einer Schleife. Ziel ist, dass der Uebergang von
Ende zu Anfang nicht auffaellt - ein abgehacktes Wort am Ende ist der lauteste
Hinweis darauf, dass es von vorn losgeht. Dieses Modul rechnet nur: es schneidet
nicht und rendert nicht. Teil A rastet eine Kandidatenspanne auf die jeweils
naechstgelegene Wortgrenze. Teil B ist eine reine Funktion, die aus diesen
Zahlen ein Urteil ueber die Schleifentauglichkeit bildet - sie entscheidet nichts
ueber Bildinhalt, nur ueber den Ton, weil dort der Bruch hoerbar wird.

Beide Teile arbeiten auf der Wortliste aus
:func:`matrix_auto_cutter.shorts.subtitle_lines.words_from_whisper_json`.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass

from matrix_auto_cutter.shorts.subtitle_lines import Word

# ---------------------------------------------------------------------------
# Teil A: Grenzwerte, alle an einer Stelle.
# ---------------------------------------------------------------------------

MIN_PAUSE_MS = 250
"""Nur noch fuer die Einstufung (Teil B) benutzt, nicht mehr fuer das Rasten.

Auftrag shorts-schleifenpunkt-korrektur (2026-08-18): an der Wortliste von
2026-08-18 08-51-21 (3070 Woerter) lagen 0 ms an Wortluecken beim 50./60./70./
80. Perzentil und noch beim 90. Perzentil nur 30 ms - eine Mindestpause von
250 ms traf auf real gemessene Kandidatenspannen fast nie zu (34 von 3069
Luecken erreichten sie) und liess das Rasten regelmaessig ueber
:data:`MAX_SHIFT_MS` hinauslaufen, bevor es ueberhaupt eine taugliche Pause
fand. Als Rastbedingung war sie damit unbrauchbar; als Schwelle fuer die
Einstufung (`GEEIGNET` in Teil B) bleibt sie sinnvoll, weil dort eine belegte
Stille tatsaechlich etwas aussagen soll."""

MAX_SHIFT_MS = 1500
"""Hoechste erlaubte Verschiebung je Grenze, bevor der Kandidat als nicht rastbar gilt."""

MIN_SPAN_MS = 5000
"""Kuerzeste erlaubte Spannenlaenge nach dem Rasten."""

EINSCHLUSS_TOLERANZ_MS = 150
"""Auftrag shorts-rastrichtung: die gerastete Spanne ist stets eine TEILMENGE der
vorgegebenen, nie eine Obermenge - das Rasten zieht die vorgegebene Aussengrenze
auf Wortgrenzen zusammen, es weitet sie nicht mehr auf die naechstgelegene
Wortgrenze in beide Richtungen auf (das war die bisherige, jetzt abgeloeste
Regel). Eine Ausnahme, sonst verlieren wir gewollte Woerter an ungenaue
Zerlegungsmarken: liegt eine Grenze hoechstens :data:`EINSCHLUSS_TOLERANZ_MS`
in ein Wort hinein (Start: nach dessen Anfang; Ende: vor dessen Ende), gilt
dieses Wort als gewollt und die Grenze wandert auf sein Ende zurueck. 150 ms
sind Setzungenauigkeit der Zerlegung, ein halbes Wort ist Absicht."""

LOOP_PAD_MS = 200
"""Nachlaufpuffer je Grenze (Auftrag shorts-bau, Punkt 4a): ein Wort, das
exakt auf der Kandidatengrenze endet, klingt abgehackt, auch wenn die
Wortgrenze selbst sauber ist. Nach dem letzten Wortende bzw. vor dem ersten
Wortanfang bleiben deshalb je bis zu 200 ms Luft, sofern die gemessene Pause
zum Nachbarwort das hergibt (sonst wird nur so viel Puffer genommen, wie die
Pause traegt). Die Spanne wird dadurch groesser, nie kleiner."""


class LoopPointError(ValueError):
    """Basisklasse fuer alle Fehler beim Rasten einer Kandidatenspanne."""


class NotSnappableError(LoopPointError):
    """Die naechstgelegene Wortgrenze braucht mehr als :data:`MAX_SHIFT_MS`.

    Keine stille Notloesung: der Kandidat gilt als nicht rastbar.
    """


class SpanTooShortError(LoopPointError):
    """Die gerastete Spanne unterschreitet :data:`MIN_SPAN_MS`."""


@dataclass(frozen=True, slots=True)
class LoopBoundaries:
    """Ergebnis des Rastens: korrigierte Spanne, Verschiebung, tatsaechliche Pausen.

    ``pause_before_ms``/``pause_after_ms`` sind ``None``, wenn die gewaehlte
    Grenzenwort das erste bzw. letzte Wort der uebergebenen Wortliste ist -
    dann liegt kein Nachbarwort vor, an dem sich die Pause messen liesse. Das
    ist kein Fehler: eine Pause zu einem nicht vorhandenen Wort ist durch
    nichts begrenzt.
    """

    start_ms: int
    end_ms: int
    start_shift_ms: int
    end_shift_ms: int
    pause_before_ms: int | None
    pause_after_ms: int | None


def _snap_start(words: Sequence[Word], start_ms: int, end_ms: int) -> tuple[int, int, int | None]:
    """Raste die Startgrenze auf den Anfang des ersten vollstaendig enthaltenen Wortes.

    Das erste Wort, das VOLLSTAENDIG innerhalb ``[start_ms, end_ms]`` liegt -
    die Grenze wandert also nur vorwaerts oder bleibt (Auftrag
    shorts-rastrichtung).

    Ausnahme (:data:`EINSCHLUSS_TOLERANZ_MS`): liegt ``start_ms`` hoechstens
    so weit nach dem Anfang eines Wortes, das damit nur zur Haelfte erfasst
    wuerde, gilt dieses Wort als gewollt und die Grenze wandert auf seinen
    Anfang zurueck - auch wenn das Wort selbst nicht vollstaendig in der
    Spanne liegt.
    """
    starts = [word.start_ms for word in words]
    subset_idx: int | None = None
    for idx, word in enumerate(words):
        if word.start_ms >= start_ms and word.end_ms <= end_ms:
            subset_idx = idx
            break

    exception_idx: int | None = None
    idx_at_or_before = bisect.bisect_right(starts, start_ms) - 1
    if idx_at_or_before >= 0:
        candidate = words[idx_at_or_before]
        if start_ms - candidate.start_ms <= EINSCHLUSS_TOLERANZ_MS:
            exception_idx = idx_at_or_before

    if subset_idx is not None and exception_idx is not None:
        idx = (
            subset_idx
            if words[subset_idx].start_ms <= words[exception_idx].start_ms
            else exception_idx
        )
    elif subset_idx is not None:
        idx = subset_idx
    elif exception_idx is not None:
        idx = exception_idx
    else:
        raise NotSnappableError(
            "Kein Wort liegt vollstaendig innerhalb der Spanne - Startgrenze nicht rastbar"
        )

    word = words[idx]
    shift = word.start_ms - start_ms
    if abs(shift) > MAX_SHIFT_MS:
        raise NotSnappableError(
            f"Startgrenze braeuchte {abs(shift)} ms Verschiebung, erlaubt sind "
            f"{MAX_SHIFT_MS} ms"
        )
    pause_before = None if idx == 0 else word.start_ms - words[idx - 1].end_ms
    return word.start_ms, shift, pause_before


def _snap_end(words: Sequence[Word], start_ms: int, end_ms: int) -> tuple[int, int, int | None]:
    """Raste die Endgrenze auf das Ende des letzten vollstaendig enthaltenen Wortes.

    Das letzte Wort, das VOLLSTAENDIG innerhalb ``[start_ms, end_ms]`` liegt -
    die Grenze wandert also nur rueckwaerts oder bleibt (Auftrag
    shorts-rastrichtung). Ausnahme (:data:`EINSCHLUSS_TOLERANZ_MS`): liegt ``end_ms`` hoechstens so
    weit vor dem Ende eines Wortes, das damit nur zur Haelfte erfasst wuerde,
    gilt dieses Wort als gewollt und die Grenze wandert auf sein Ende vor -
    auch wenn das Wort selbst nicht vollstaendig in der Spanne liegt.
    """
    ends = [word.end_ms for word in words]
    subset_idx: int | None = None
    for idx in range(len(words) - 1, -1, -1):
        word = words[idx]
        if word.start_ms >= start_ms and word.end_ms <= end_ms:
            subset_idx = idx
            break

    exception_idx: int | None = None
    idx_at_or_after = bisect.bisect_left(ends, end_ms)
    if idx_at_or_after < len(words):
        candidate = words[idx_at_or_after]
        if candidate.end_ms - end_ms <= EINSCHLUSS_TOLERANZ_MS:
            exception_idx = idx_at_or_after

    if subset_idx is not None and exception_idx is not None:
        idx = (
            subset_idx
            if words[subset_idx].end_ms >= words[exception_idx].end_ms
            else exception_idx
        )
    elif subset_idx is not None:
        idx = subset_idx
    elif exception_idx is not None:
        idx = exception_idx
    else:
        raise NotSnappableError(
            "Kein Wort liegt vollstaendig innerhalb der Spanne - Endgrenze nicht rastbar"
        )

    word = words[idx]
    shift = end_ms - word.end_ms
    if abs(shift) > MAX_SHIFT_MS:
        raise NotSnappableError(
            f"Endgrenze braeuchte {abs(shift)} ms Verschiebung, erlaubt sind {MAX_SHIFT_MS} ms"
        )
    pause_after = None if idx == len(words) - 1 else words[idx + 1].start_ms - word.end_ms
    return word.end_ms, shift, pause_after


def rasten_auf_wortgrenzen(words: Sequence[Word], start_ms: int, end_ms: int) -> LoopBoundaries:
    """Raste eine Kandidatenspanne auf Wortgrenzen.

    Die gerastete Spanne ist stets eine TEILMENGE der vorgegebenen, nie eine
    Obermenge (Auftrag shorts-rastrichtung). Die Startgrenze wandert auf den
    Anfang des ersten Wortes, das vollstaendig innerhalb der Spanne liegt
    (also nur vorwaerts oder bleibt), die Endgrenze auf das Ende des letzten
    vollstaendig enthaltenen Wortes (also nur rueckwaerts oder bleibt).
    Ausnahme: liegt eine Grenze hoechstens
    :data:`EINSCHLUSS_TOLERANZ_MS` in ein Wort hinein, gilt dieses Wort als
    gewollt und die Grenze wandert auf sein Ende zurueck. Die tatsaechlich
    gemessenen Pausen werden weiterhin in ``pause_before_ms``/``pause_after_ms``
    berichtet.

    Wirft :class:`NotSnappableError`, wenn keine Grenze bestimmbar ist oder eine
    Grenze mehr als :data:`MAX_SHIFT_MS` wandern muesste, und
    :class:`SpanTooShortError`, wenn die gerastete Spanne dadurch kuerzer als
    :data:`MIN_SPAN_MS` wird.
    """
    if end_ms < start_ms:
        raise LoopPointError(f"end_ms ({end_ms}) liegt vor start_ms ({start_ms})")
    if not words:
        raise NotSnappableError("Leere Wortliste - keine Wortgrenze zum Rasten vorhanden")
    new_start, start_shift, pause_before = _snap_start(words, start_ms, end_ms)
    new_end, end_shift, pause_after = _snap_end(words, start_ms, end_ms)

    # Nachlaufpuffer (Punkt 4a): so viel von LOOP_PAD_MS nehmen, wie die
    # gemessene Pause zum Nachbarwort hergibt - ohne Nachbarwort (None) ist
    # nichts begrenzt, der volle Puffer gilt.
    start_pad = LOOP_PAD_MS if pause_before is None else min(LOOP_PAD_MS, pause_before)
    end_pad = LOOP_PAD_MS if pause_after is None else min(LOOP_PAD_MS, pause_after)
    padded_start = new_start - start_pad
    padded_end = new_end + end_pad

    if padded_end - padded_start < MIN_SPAN_MS:
        raise SpanTooShortError(
            f"Gerastete Spanne ({padded_end - padded_start} ms) unterschreitet {MIN_SPAN_MS} ms"
        )
    return LoopBoundaries(
        start_ms=padded_start,
        end_ms=padded_end,
        start_shift_ms=start_shift,
        end_shift_ms=end_shift,
        pause_before_ms=pause_before,
        pause_after_ms=pause_after,
    )


# ---------------------------------------------------------------------------
# Teil B: Schleifentauglichkeit. Reine Funktion aus den Zahlen von Teil A -
# entscheidet nichts ueber Bildinhalt, nur ueber den Ton. Als automatisches
# Ausschlusskriterium gedacht (die Verdrahtung kommt spaeter): "ungeeignet"
# soll gar nicht erst zur Beurteilung kommen.
# ---------------------------------------------------------------------------

PREFERRED_SPAN_MS = 8000
"""Spannenlaenge, ab der haeufiges Wiederholen nicht zusaetzlich auffaellt."""

GEEIGNET = "geeignet"
GRENZWERTIG = "grenzwertig"
UNGEEIGNET = "ungeeignet"


@dataclass(frozen=True, slots=True)
class SchleifenUrteil:
    """Einstufung der Schleifentauglichkeit samt den Zahlen, auf denen sie beruht."""

    einstufung: str
    pause_before_ms: int | None
    pause_after_ms: int | None
    span_ms: int
    pausen_ausreichend: bool


def beurteile_schleifentauglichkeit(
    *, pause_before_ms: int | None, pause_after_ms: int | None, span_ms: int
) -> SchleifenUrteil:
    """Bilde aus Pausen und Spannenlaenge ein Urteil ueber die Schleifentauglichkeit.

    Auftrag shorts-schleifenpunkt-korrektur, Teil D: Seit das Rasten (Teil A)
    keine Mindestpause mehr voraussetzt, ist eine bekannte Pause unter
    :data:`MIN_PAUSE_MS` der Regelfall (an echtem Material rund 99 % aller
    Kandidaten) und damit als automatischer Ausschlussgrund unbrauchbar.
    :data:`UNGEEIGNET` markiert deshalb nur noch eine zu kurze Spanne -
    dass das Rasten selbst scheitert, meldet :func:`rasten_auf_wortgrenzen`
    schon vorher per Ausnahme und kommt hier gar nicht erst an.

    Eine Pause von ``None`` bedeutet: kein Nachbarwort in der Wortliste
    vorhanden, die Pause ist also nicht pruefbar. Das zaehlt nicht als
    Verstoss, ist aber auch kein Beleg fuer einen sauberen Uebergang -
    :data:`GEEIGNET` bleibt daher beiden bekannten, ausreichenden Pausen
    vorbehalten.
    """
    pausen_ausreichend = (
        pause_before_ms is not None
        and pause_after_ms is not None
        and pause_before_ms >= MIN_PAUSE_MS
        and pause_after_ms >= MIN_PAUSE_MS
    )

    if span_ms < MIN_SPAN_MS:
        einstufung = UNGEEIGNET
    elif pausen_ausreichend and span_ms >= PREFERRED_SPAN_MS:
        einstufung = GEEIGNET
    else:
        einstufung = GRENZWERTIG

    return SchleifenUrteil(
        einstufung=einstufung,
        pause_before_ms=pause_before_ms,
        pause_after_ms=pause_after_ms,
        span_ms=span_ms,
        pausen_ausreichend=pausen_ausreichend,
    )


def beurteile_grenzen(boundaries: LoopBoundaries) -> SchleifenUrteil:
    """Bequemlichkeitsfunktion: Urteil direkt aus einem :class:`LoopBoundaries`-Ergebnis."""
    return beurteile_schleifentauglichkeit(
        pause_before_ms=boundaries.pause_before_ms,
        pause_after_ms=boundaries.pause_after_ms,
        span_ms=boundaries.end_ms - boundaries.start_ms,
    )
