"""Verschiebung der Bildschirm-Schnittliste auf die Avatarachse um ``|L|`` Frames.

Vorzeichen (siehe Auftrag 09, Abschnitt 1): Der gemessene Lag ``L`` erfüllt
``avatar[n] ≈ screen[n - L]`` und ist in jedem bekannten Lauf negativ - die
Avataraufnahme beginnt um ``|L|`` Frames *später* als die Bildschirmaufnahme.
Ein Ereignis bei Bildschirmframe ``t`` liegt in der Avatardatei bei
``t - |L|``. Diese Datei erwartet überall ``lag_frames = |L| >= 0`` als
bereits vorzeichenbereinigten Betrag.

Reine Funktionen, kein IO, kein Video.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from matrix_auto_cutter.shorts.frame_map import (
    KeepSegment,
    map_source_frame_ceiling,
    map_source_frame_floor,
)


def shift_intervals_to_avatar_axis(
    screen_cut_intervals: Sequence[tuple[int, int]],
    *,
    lag_frames: int,
    avatar_frame_count: int,
) -> tuple[tuple[int, int], ...]:
    """Verschiebe Schnittintervalle rückwärts um ``lag_frames`` und clippe auf die Avatardatei.

    Ein Schnitt, der vollständig vor Avatarframe 0 oder vollständig nach dem
    Ende der Avatardatei liegt, betrifft dort nichts und entfällt. Ein Schnitt,
    der über einen Rand hinausragt, wird an diesem Rand gekappt - er bleibt
    trotzdem ein Schnitt, nur kürzer.
    """
    if lag_frames < 0:
        raise ValueError("lag_frames darf nicht negativ sein (siehe Vorzeichen-Abschnitt)")
    if avatar_frame_count <= 0:
        raise ValueError("avatar_frame_count muss positiv sein")
    shifted: list[tuple[int, int]] = []
    for start, end in screen_cut_intervals:
        shifted_start = max(0, start - lag_frames)
        shifted_end = min(avatar_frame_count, end - lag_frames)
        if shifted_start < shifted_end:
            shifted.append((shifted_start, shifted_end))
    return tuple(shifted)


@dataclass(frozen=True, slots=True)
class RenderedAxisCoverage:
    """Befund: welchen Bereich der gerenderten Achse die Avatardatei tatsächlich abdeckt.

    Ersetzt die engere frühere ``leading_edge_finding`` (Auftrag 11,
    Eingriff 2, siehe ``LAGMESSUNG-2026-08-11.md`` Punkt A): die Avatardatei
    kann grundsätzlich nichts abdecken, was auf der Bildschirmachse vor
    Frame ``|L|`` liegt - unabhängig davon, ob der erste oder ein
    verketteter weiterer Schnitt dorthin reicht. Alle vier Felder rechnen auf
    der gerenderten Achse (Auftrag 12, Punkt 1) - die Stoppdifferenz auf der
    rohen Bildschirmachse ist eine andere Größe und steht getrennt in
    :class:`TrailingEdgeFinding`.
    """

    first_rendered_frame: int
    last_rendered_frame: int
    missing_frames_front: int
    missing_frames_back: int


def rendered_axis_coverage(
    screen_keep_segments: Sequence[KeepSegment],
    *,
    lag_frames: int,
    avatar_frame_count: int,
) -> RenderedAxisCoverage:
    """Bestimme den von der Avatardatei tatsächlich abgedeckten Bereich der Renderachse.

    Die Avatardatei kann grundsätzlich nichts zeigen, was auf der
    Bildschirmachse vor Frame ``lag_frames`` liegt (Avatarframe 0 == Screen-
    frame ``lag_frames``). Der erste gerenderte Frame, dessen Quellframe
    ``>= lag_frames`` ist, ist deshalb der erste, den die Avatardatei
    überhaupt zeigen könnte - unabhängig davon, ob ein einzelner oder
    mehrere verkettete Schnitte diesen Bereich ohnehin entfernen (das war
    die Modellierungslücke der früheren ``leading_edge_finding``, siehe
    ``LAGMESSUNG-2026-08-11.md`` Punkt A). Symmetrisch dazu ist der letzte
    gerenderte Frame, den die Avatardatei zeigen kann, der letzte mit
    Quellframe ``<= avatar_frame_count - 1 + lag_frames`` (letztes
    tatsächlich aufgenommenes Avatarframe, auf die Bildschirmachse
    gebracht). Beide über :func:`matrix_auto_cutter.shorts.frame_map.map_source_frame_ceiling`
    bzw. ``..._floor`` - keine Schätzung, keine Interpolation.

    ``missing_frames_back`` rechnet auf derselben gerenderten Achse wie die
    anderen drei Felder (Auftrag 12, Punkt 1): die Zahl der gerenderten
    Frames nach ``last_rendered_frame``, die diese Datei nicht zeigt. Vorher
    wurde hier die rohe Stoppdifferenz aus :func:`trailing_edge_finding`
    durchgereicht - eine andere Achse unter demselben Namen.
    """
    first_rendered = map_source_frame_ceiling(screen_keep_segments, lag_frames)
    last_source_frame = avatar_frame_count - 1 + lag_frames
    last_rendered = map_source_frame_floor(screen_keep_segments, last_source_frame)
    if last_rendered is None:
        raise ValueError(
            "keine gerenderte Position liegt vor oder bei dem letzten Avatarframe - "
            "Plan inkonsistent"
        )
    total_rendered = sum(segment.length for segment in screen_keep_segments)
    return RenderedAxisCoverage(
        first_rendered_frame=first_rendered,
        last_rendered_frame=last_rendered,
        missing_frames_front=first_rendered,
        missing_frames_back=max(0, total_rendered - 1 - last_rendered),
    )


@dataclass(frozen=True, slots=True)
class TrailingEdgeFinding:
    """Befund zum Endrand: Frames am Ende, für die es kein Avatarbild gibt."""

    source_frame_count: int
    lag_frames: int
    avatar_frame_count: int
    missing_frames: int


def trailing_edge_finding(
    *,
    source_frame_count: int,
    lag_frames: int,
    avatar_frame_count: int,
) -> TrailingEdgeFinding:
    """Wie viele Frames am Ende der Bildschirmachse haben kein Avatar-Gegenstück.

    Die Avataraufnahme endet typischerweise früher als die Bildschirmaufnahme
    (Pipelinetiefe beim Stopp). Diese Differenz wird berichtet, nicht
    aufgefüllt - kein Standbild, kein Schwarzbild.
    """
    missing = max(0, (source_frame_count - lag_frames) - avatar_frame_count)
    return TrailingEdgeFinding(
        source_frame_count=source_frame_count,
        lag_frames=lag_frames,
        avatar_frame_count=avatar_frame_count,
        missing_frames=missing,
    )
