"""Reine Abbildung Quellframe -> gerendertes Frame, ohne Video und ohne IO.

Diese Rechnung ist unabhängig davon, worauf sie später angewendet wird - auf
die Avatardatei (diese Stufe) oder später auf ein Cursorprotokoll (Stufe 3,
noch nicht gebaut). Sie kennt nur eine sortierte, disjunkte Liste halboffener
Schnittintervalle ``[start_frame, end_frame)`` auf einer Quellachse und deren
Gesamtlänge; sie öffnet keine Datei und ruft kein ffmpeg.

Gegengerechnet gegen das Beispiel aus
``artefakte/repeat/shorts-tonabgleich/TONABGLEICH-UND-ZEITBRUECKE-2026-08-10.md``
Teil C.2 (Proposal ``d5c634d3``): Quellframe 5400 liegt in keinem Schnitt und
landet auf gerendertem Frame 3809, weil die Keep-Segmente davor
1745 + 1976 + 88 = 3809 Frames umfassen.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from matrix_auto_cutter.cut_proposal import CutProposal, ProposedCut


@dataclass(frozen=True, slots=True)
class KeepSegment:
    """Ein zusammenhängender, im Ergebnis erhaltener Abschnitt der Quellachse."""

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        """Erzwinge die Intervall-Invariante direkt bei der Konstruktion."""
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("keep segment requires 0 <= start_frame < end_frame")

    @property
    def length(self) -> int:
        """Anzahl der Frames in diesem Segment."""
        return self.end_frame - self.start_frame


def ms_to_frame(ms: int, fps: int) -> int:
    """Runde eine Millisekundenmarke auf ihre Framezahl bei ``fps``.

    Ab hier die EINZIGE Stelle, an der aus Millisekunden eine Framezahl wird
    (Auftrag shorts-stufe-3a-frames, Teil A) - auch fuer Stufe 3b und Stufe 5.
    """
    return round(ms * fps / 1000)


def candidate_frame_span(start_ms: int, end_ms: int, fps: int) -> tuple[int, int]:
    """Framespanne ``(start_frame, end_frame)`` eines Kandidaten, ``end_frame`` ausschliessend.

    Dieselbe Konvention wie bei den Keep-Segmenten. Rundet Start und Ende
    unabhaengig (nicht die Dauer) - eine nicht-positive Framezahl ist ein
    Fehler, kein stilles Klemmen.
    """
    start_frame = ms_to_frame(start_ms, fps)
    end_frame = ms_to_frame(end_ms, fps)
    if end_frame - start_frame <= 0:
        raise ValueError(
            f"Framespanne muss positiv sein, ist aber {end_frame - start_frame} "
            f"(start_ms={start_ms}, end_ms={end_ms}, fps={fps})"
        )
    return start_frame, end_frame


def effective_cuts(
    proposed_cuts: Sequence[ProposedCut],
    active_candidate_ids: Sequence[str] | None,
) -> tuple[ProposedCut, ...]:
    """Die tatsächlich freigegebenen Schnitte.

    ``active_candidate_ids is None`` steht für die vollständige Freigabe
    (Entscheidung ``approved``): alle vorgeschlagenen Schnitte gelten. Ist die
    Sequenz gesetzt (Entscheidung ``selected_cuts_approved``), gilt nur die
    benannte Teilmenge - exakt das, was auch tatsächlich gerendert wurde.
    """
    if active_candidate_ids is None:
        return tuple(proposed_cuts)
    active = set(active_candidate_ids)
    return tuple(cut for cut in proposed_cuts if cut.candidate_id in active)


def keep_segments_from_intervals(
    cut_intervals: Sequence[tuple[int, int]],
    total_frame_count: int,
) -> tuple[KeepSegment, ...]:
    """Komplement sortierter, disjunkter halboffener Schnittintervalle.

    Reine Ganzzahlarithmetik, keine Kenntnis eines ``CutProposal`` nötig -
    das macht die Funktion für Quell- und Avatarachse gleichermaßen nutzbar.
    """
    if total_frame_count <= 0:
        raise ValueError("total_frame_count muss positiv sein")
    ordered = sorted(cut_intervals, key=lambda interval: interval[0])
    segments: list[KeepSegment] = []
    cursor = 0
    for start, end in ordered:
        if start < cursor or end > total_frame_count or start >= end:
            raise ValueError("Schnittintervalle müssen sortiert, disjunkt und im Rahmen sein")
        if start > cursor:
            segments.append(KeepSegment(cursor, start))
        cursor = end
    if cursor < total_frame_count:
        segments.append(KeepSegment(cursor, total_frame_count))
    return tuple(segments)


def keep_segments(
    proposal: CutProposal,
    cuts: Sequence[ProposedCut],
) -> tuple[KeepSegment, ...]:
    """Komplement der freigegebenen Schnitte auf der Proposal-Quellachse."""
    intervals = [(cut.start_frame, cut.end_frame) for cut in cuts]
    return keep_segments_from_intervals(intervals, proposal.source_frame_count)


def map_source_frame(segments: Sequence[KeepSegment], source_frame: int) -> int | None:
    """Bilde einen Quellframe auf sein gerendertes Gegenstück ab.

    Gibt ``None`` zurück, wenn der Frame in einem Schnitt liegt - er hat dann
    kein Gegenstück im gerenderten Ergebnis. Kein Rateversuch, keine
    Interpolation.
    """
    if source_frame < 0:
        raise ValueError("source_frame darf nicht negativ sein")
    rendered = 0
    for segment in segments:
        if segment.start_frame <= source_frame < segment.end_frame:
            return rendered + (source_frame - segment.start_frame)
        rendered += segment.length
    return None


def map_source_frame_ceiling(segments: Sequence[KeepSegment], source_frame: int) -> int:
    """Kleinste gerenderte Position, deren Quellframe ``>= source_frame``.

    Liegt ``source_frame`` selbst in einem erhaltenen Segment, ist das
    Ergebnis identisch zu :func:`map_source_frame`. Liegt er in einem
    Schnitt oder davor, ist das Ergebnis die gerenderte Position, an der das
    nächste erhaltene Segment beginnt. Gibt es kein solches Segment mehr,
    ist das Ergebnis die Gesamtlänge (kein gültiges Frame, aber eine
    eindeutige obere Schranke).
    """
    if source_frame < 0:
        raise ValueError("source_frame darf nicht negativ sein")
    rendered = 0
    for segment in segments:
        if source_frame <= segment.start_frame:
            return rendered
        if source_frame < segment.end_frame:
            return rendered + (source_frame - segment.start_frame)
        rendered += segment.length
    return rendered


def map_source_interval_to_rendered(
    segments: Sequence[KeepSegment], source_windows: Sequence[tuple[int, int]]
) -> tuple[tuple[int, int], ...]:
    """Bilde halboffene Quellframe-Fenster (z. B. Szenenfenster) auf die gerenderte Achse ab.

    Kein neues Verfahren: beide Enden laufen über :func:`map_source_frame_ceiling`
    (kleinste gerenderte Position, deren Quellframe >= der gesuchten Marke) -
    dieselbe Rundung an Anfang und Ende hält das Fenster halboffen, so wie es
    :func:`keep_segments_from_intervals` selbst schon voraussetzt. Ein Fenster,
    das vollständig in einem Schnitt liegt, hat kein Gegenstück im gerenderten
    Ergebnis und entfällt (leeres Zwischenergebnis, kein Nulleintrag).
    """
    rendered: list[tuple[int, int]] = []
    for start_frame, end_frame in source_windows:
        rendered_start = map_source_frame_ceiling(segments, start_frame)
        rendered_end = map_source_frame_ceiling(segments, end_frame)
        if rendered_end > rendered_start:
            rendered.append((rendered_start, rendered_end))
    return tuple(rendered)


def candidate_outside_windows(
    candidate_span: tuple[int, int], rendered_windows: Sequence[tuple[int, int]]
) -> bool:
    """Melde, ob eine Kandidaten-Framespanne keines der gerenderten Fenster überschneidet.

    Beide Spannen sind halboffen. Ein Kandidat, der ein Fenster auch nur
    teilweise berührt, gilt als nicht außerhalb - nur eine Spanne ohne jede
    Überschneidung wird gekennzeichnet.
    """
    start, end = candidate_span
    return not any(start < w_end and end > w_start for w_start, w_end in rendered_windows)


def map_source_frame_floor(segments: Sequence[KeepSegment], source_frame: int) -> int | None:
    """Größte gerenderte Position, deren Quellframe ``<= source_frame``.

    Liegt ``source_frame`` selbst in einem erhaltenen Segment, ist das
    Ergebnis identisch zu :func:`map_source_frame`. Liegt er in einem
    Schnitt oder danach, ist das Ergebnis die gerenderte Position, an der
    das vorhergehende erhaltene Segment endet. ``None``, wenn kein
    erhaltenes Segment vor oder bei ``source_frame`` liegt.
    """
    if source_frame < 0:
        raise ValueError("source_frame darf nicht negativ sein")
    rendered = 0
    last_covered: int | None = None
    for segment in segments:
        if source_frame < segment.start_frame:
            break
        if source_frame < segment.end_frame:
            return rendered + (source_frame - segment.start_frame)
        rendered += segment.length
        last_covered = rendered - 1
    return last_covered


def map_rendered_frame(segments: Sequence[KeepSegment], rendered_frame: int) -> int | None:
    """Der Rueckweg: bilde ein gerendertes Frame auf seinen Quellframe ab.

    Gegenrichtung zu :func:`map_source_frame` und auf allen erhaltenen Frames
    dessen exakte Umkehrung::

        map_source_frame(segments, map_rendered_frame(segments, f)) == f

    fuer jedes ``f`` von 0 bis zur Gesamtlaenge der Segmente. Reine
    Ganzzahlarithmetik, keine Gleitkommazahlen, kein Rateversuch.

    Verhalten ausserhalb des gueltigen Bereichs - dieselbe Form wie bei den
    Nachbarn, und aus demselben Grund: Ein NEGATIVES ``rendered_frame`` ist ein
    Aufruffehler und wirft ``ValueError``, genau wie ein negatives
    ``source_frame`` in :func:`map_source_frame`,
    :func:`map_source_frame_ceiling` und :func:`map_source_frame_floor` - eine
    Frameachse hat kein Frame vor null, das ist keine Frage der Daten, sondern
    ein Fehler des Aufrufers. Ein ``rendered_frame`` AB der Gesamtlaenge der
    Segmente ist dagegen ein zulaessiger, nur eben unbesetzter Wert: Er hat
    kein Gegenstueck auf der Quellachse, und das ist ``None`` - dieselbe
    Auskunft, die :func:`map_source_frame` fuer einen Quellframe in einem
    Schnitt gibt. Kein Klemmen auf den Rand.
    """
    if rendered_frame < 0:
        raise ValueError("rendered_frame darf nicht negativ sein")
    rest = rendered_frame
    for segment in segments:
        if rest < segment.length:
            return segment.start_frame + rest
        rest -= segment.length
    return None
