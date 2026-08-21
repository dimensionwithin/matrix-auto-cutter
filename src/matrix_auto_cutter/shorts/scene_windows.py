"""Szenenfenster aus dem OBS-Producer-Journal, als Quellframe-Intervalle.

Reine Auswertung der rohen ``*.recording-journal.ndjson``-Zeilen (Auftrag
shorts-szenenfilter, Teil B). Die Frameachse ist die des Journals selbst
(``output_frame_count``), nicht die kalibrierte Proposal-Quellachse aus
``cut_proposal.py``/``calibration.py``. Laut Teil A des Auftragsberichts
(``artefakte/repeat/shorts-szenenfilter/BERICHT-2026-08-17.md``) stimmen
beide Achsen an Anfang und Ende der Aufnahme auf einen Frame genau überein -
für eine grobe Szenenfilterung genügt das; die framegenaue Kalibrierung
(``pipeline_lag_frames`` in ``event_lag.py``) ist eigens für die
Intro/Outro-Schnittkante gebaut und hier nicht nötig.

Bekannte Lücke: ``scene_changed`` feuert nur bei einem Wechsel. Beginnt die
Aufnahme bereits in der Zielszene, gibt es dafür keine Zeile. Journal-Schema
1.0 (``matrix_auto_cutter.journal``) führt die Anfangsszene nirgends explizit
- weder in der Kopfzeile noch in ``recording_started`` (dessen ``label``-Feld
das Schema erlaubt, das aber in jeder bisher beobachteten Aufnahme fehlt).
Bleibt die Anfangsszene unbekannt UND würde die Suche sonst kein einziges
Fenster finden, meldet die Funktion :class:`SceneWindowsFailed` statt eines
stillen leeren Ergebnisses - eine leere Liste wäre nicht von "diese Szene
kommt in der Aufnahme nicht vor" zu unterscheiden.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Einzige Stelle, an der der gesuchte Szenenname steht (Auftrag
# shorts-szenenfilter, Teil B) - hier ändern, wenn er sich ändert.
CHARTS_SCENE_LABEL = "Charts"

# Eigene Fehlercodes (siehe :class:`SceneWindowsFailed`), keine Freitext-Rätsel.
REASON_MISSING_STOP = "missing_stop_record"
REASON_UNKNOWN_INITIAL_SCENE = "unknown_initial_scene"


@dataclass(frozen=True, slots=True)
class SceneWindow:
    """Ein Szenenfenster auf der Journal-Quellframeachse, halboffen."""

    start_frame: int
    end_frame: int

    def __post_init__(self) -> None:
        """Erzwinge die Intervall-Invariante direkt bei der Konstruktion."""
        if self.start_frame < 0 or self.end_frame <= self.start_frame:
            raise ValueError("scene window requires 0 <= start_frame < end_frame")


@dataclass(frozen=True, slots=True)
class SceneWindowsFailed:
    """Fail-closed Auskunft - kein stilles leeres Ergebnis, siehe Moduldoc."""

    reason: str


def scene_windows_from_records(
    records: Sequence[Mapping[str, object]],
    *,
    scene_label: str = CHARTS_SCENE_LABEL,
) -> tuple[SceneWindow, ...] | SceneWindowsFailed:
    """Lies Szenenfenster aus geparsten Journalzeilen (dicts, roh aus ``ndjson``).

    Erwartet mindestens einen ``event``-Record vom Typ ``recording_started``
    und einen ``stop``-Record; andere Recordtypen (Kalibrierproben, Pause,
    Resume, ...) werden ignoriert. Die vollständige Validierung des Journals
    selbst ist Aufgabe von :mod:`matrix_auto_cutter.journal`, nicht dieser
    Funktion - sie liest defensiv und meldet nur, was sie für die
    Szenenfenster tatsächlich braucht.

    Ein Fenster beginnt bei einer ``scene_changed``-Zeile mit dem gesuchten
    Label und endet bei der nächsten ``scene_changed``-Zeile gleich welchen
    Labels, oder bei der ``stop``-Zeile, wenn keine mehr folgt. Beginnt die
    Aufnahme bereits in der gesuchten Szene und trägt ``recording_started``
    ein ``label``, zählt auch das führende Fenster vor dem ersten Wechsel.
    """
    scene_changes: list[tuple[int, str]] = []
    initial_label: str | None = None
    stop_frame: int | None = None
    for record in records:
        record_type = record.get("record_type")
        if record_type == "event" and record.get("event_type") == "recording_started":
            label = record.get("label")
            if isinstance(label, str) and label:
                initial_label = label
        elif record_type == "event" and record.get("event_type") == "scene_changed":
            frame = record.get("output_frame_count")
            label = record.get("label")
            if isinstance(frame, int) and isinstance(label, str):
                scene_changes.append((frame, label))
        elif record_type == "stop":
            frame = record.get("output_frame_count")
            if isinstance(frame, int):
                stop_frame = frame

    if stop_frame is None:
        return SceneWindowsFailed(REASON_MISSING_STOP)

    scene_changes.sort(key=lambda item: item[0])
    first_change_frame = scene_changes[0][0] if scene_changes else stop_frame

    windows: list[SceneWindow] = []
    if initial_label == scene_label and first_change_frame > 0:
        windows.append(SceneWindow(0, first_change_frame))
    leading_scene_unknown = initial_label is None and first_change_frame > 0

    for index, (frame, label) in enumerate(scene_changes):
        if label != scene_label:
            continue
        end_frame = (
            scene_changes[index + 1][0] if index + 1 < len(scene_changes) else stop_frame
        )
        if end_frame > frame:
            windows.append(SceneWindow(frame, end_frame))

    if windows:
        return tuple(windows)
    if leading_scene_unknown:
        return SceneWindowsFailed(REASON_UNKNOWN_INITIAL_SCENE)
    return ()


def load_scene_windows(
    journal_path: Path, *, scene_label: str = CHARTS_SCENE_LABEL
) -> tuple[SceneWindow, ...] | SceneWindowsFailed:
    """Lies ein Recording-Journal (``*.recording-journal.ndjson``) von der Platte."""
    records: list[Mapping[str, object]] = []
    with journal_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            records.append(json.loads(stripped))
    return scene_windows_from_records(records, scene_label=scene_label)
