"""Korrektur des Versatzes zwischen Frontend-Marke und Compositor-Frame.

``mapped_source_frame`` ist ``output_frame_count - 1``.  Der Zähler, den der
Adapter beim OBS-Frontend-Callback abgreift, hinkt dem, was der Compositor
gerade rendert, um die Tiefe der Ausgabepipeline hinterher — und diese Tiefe
*ist* die Anlaufzeit, die ``recording_started`` misst.  Eine Marke, die aus
einem Frontend-Callback stammt, liegt deshalb systematisch um genau diesen
Betrag zu früh auf der Frameachse.

Gemessen über 17 Läufe vom 7. bis 9.8.2026: der Betrag nimmt 16, 17, 62 oder
63 Frames an, je nach Anlaufzeit der Aufnahme (267 ms bis 1050 ms).  Die
Vorhersage aus ``recording_started.clock_sample.monotonic_ns`` trifft den am
Bild gemessenen Versatz auf 0 bis 3 Frames.  Der Beleg steht in
``docs/repeat/INTRO-CUT-BEFUND-2026-08-09.md``.

**Nur Frontend-Marken sind betroffen.**  ``recording_started`` ist per
Konstruktion am ersten Videoframe verankert und ``recording_stopped`` am
letzten; Pause und Resume kommen aus denselben Ausgangssignalen.  Diese vier
sind exakt, und sie um den Lag zu verschieben wäre aktiv schädlich — der
Schutzblock am Anfang der Aufnahme würde die erste Sekunde freigeben.
Verschoben wird deshalb ausschließlich, was aus einem Frontend-Callback stammt.

Die Korrektur sitzt bewusst **nicht** in der Sidecar-Abbildung.  Die ist an
beiden Enden verankert (``sidecar.py`` verlangt Frame 0 für
``recording_started`` und ``video_frame_count`` für ``recording_stopped``) und
wird beim Einlesen nachgerechnet; eine geänderte Formel würde jedes bereits
geschriebene Sidecar ungültig machen.  Der Lag gehört zu den Ereignissen, nicht
zur Zeitachse.
"""

from __future__ import annotations

from fractions import Fraction

from matrix_auto_cutter.sidecar import SidecarEvent, ValidatedObsEventSidecar

# Ereignistypen, deren Zeitpunkt aus einem OBS-Frontend-Callback stammt und
# deren Zähler deshalb der Bildausgabe hinterherhinkt.  ``manual_protection``
# wird heute nicht abgesetzt, käme aber über denselben Weg.
FRONTEND_SAMPLED_EVENT_TYPES: frozenset[str] = frozenset({"scene_changed", "manual_protection"})


def pipeline_lag_frames(sidecar: ValidatedObsEventSidecar) -> int:
    """Rechne die Anlaufzeit der Aufnahme in ganze Frames um.

    Fehlt ``recording_started`` oder gibt es mehr als eines, wird 0 geliefert —
    dann ist die Größe nicht bestimmbar und Raten wäre schlechter als die
    bisherige, bekannte Ungenauigkeit.  Die Sidecar-Validierung garantiert genau
    ein solches Ereignis, der Zweig ist eine Absicherung.
    """
    starts = [event for event in sidecar.events if event.type == "recording_started"]
    if len(starts) != 1:
        return 0
    nanoseconds = starts[0].clock_sample.monotonic_ns
    if nanoseconds <= 0:
        return 0
    frames = (
        Fraction(nanoseconds)
        * Fraction(sidecar.source.fps_num, sidecar.source.fps_den)
        / Fraction(1_000_000_000)
    )
    # Halbe Frames nach oben, wie ``_round_half_up`` in ``calibration.py``.
    return int(frames + Fraction(1, 2))


def is_frontend_sampled(event: SidecarEvent) -> bool:
    """Melde, ob der Zeitpunkt dieses Ereignisses die Pipelinetiefe enthält."""
    return event.type in FRONTEND_SAMPLED_EVENT_TYPES


def corrected_source_frame(event: SidecarEvent, lag_frames: int, total_frames: int) -> int:
    """Liefere den Frame, an dem dieses Ereignis wirklich im Bild steht."""
    if not is_frontend_sampled(event):
        return event.mapped_source_frame
    return min(total_frames, event.mapped_source_frame + lag_frames)


def corrected_end_source_frame(
    event: SidecarEvent, lag_frames: int, total_frames: int
) -> int | None:
    """Verschiebe das Ende eines Paarereignisses nach derselben Regel."""
    end = event.end_mapped_source_frame
    if not isinstance(end, int):
        return None
    if not is_frontend_sampled(event):
        return end
    return min(total_frames, end + lag_frames)
