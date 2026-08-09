"""Taktkorrektur: Ableitung, Reichweite und Wirkung auf Intro, Outro und Schutz.

``mapped_source_frame`` ist ``output_frame_count - 1``, und der Zähler hinkt dem
Compositor um die Tiefe der Ausgabepipeline hinterher.  Über 17 Läufe vom 7. bis
9.8.2026 nahm dieser Versatz 16, 17, 62 oder 63 Frames an; die Vorhersage aus
``recording_started.clock_sample.monotonic_ns`` traf die Bildmessung auf 0 bis 3
Frames.  Die Zahlen hier stammen aus diesen Journalen, nicht aus der Luft.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from conftest import event, hard_protection
from matrix_auto_cutter.event_lag import (
    FRONTEND_SAMPLED_EVENT_TYPES,
    corrected_source_frame,
    is_frontend_sampled,
    pipeline_lag_frames,
)
from matrix_auto_cutter.intro import (
    INTRO_CUT_OFFSET_FRAMES,
    INTRO_SCENE_LABEL,
    resolve_intro,
)
from matrix_auto_cutter.protection import materialize_protection
from matrix_auto_cutter.sidecar import ObsEventSidecarV12

SIDECAR_SHA = "b" * 64

# Gemessene Anlaufzeiten aus den Journalen auf F:, mit dem Frameversatz, den sie
# am Bild erzeugt haben.
REAL_START_LATENCIES = (
    (266_666_656, 16, "2026-08-08 20-28-17"),
    (283_333_322, 17, "2026-08-09 12-09-50"),
    (1_033_333_292, 62, "2026-08-09 07-54-23"),
    (1_049_999_958, 63, "2026-08-09 14-31-51"),
)


def _scene(frame: int, name: str) -> dict[str, Any]:
    return {
        "event_id": str(uuid4()),
        "type": "scene_changed",
        "mapped_source_frame": frame,
        "uncertainty_ms": 100,
        "clock_sample": {
            "monotonic_ns": frame * 16_666_667,
            "output_frame_count": frame,
            "mapping_basis": "output_frame_counter",
        },
        "scene_name": name,
        "scene_uuid": "df50e171-befb-4d89-b9e9-66a29dd0865e",
        "protection": hard_protection(),
    }


def _raw(
    raw_sidecar: dict[str, object],
    *,
    total: int = 6000,
    start_ns: int = 0,
    scenes: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    raw = deepcopy(raw_sidecar)
    source = raw["source"]
    clock = raw["clock"]
    events = raw["events"]
    producer = raw["producer"]
    assert isinstance(source, dict) and isinstance(clock, dict)
    assert isinstance(events, list) and isinstance(producer, dict)
    producer["obs_version"] = "32.1.2"
    raw["schema_version"] = "1.2"
    source["video_frame_count"] = total
    source["duration_ms"] = total * 1000 // 60
    clock["counter_end"] = total
    for item in events:
        assert isinstance(item, dict)
        if item["type"] == "recording_started":
            item["clock_sample"]["monotonic_ns"] = start_ns
        if item["type"] == "recording_stopped":
            item["mapped_source_frame"] = total
            item["clock_sample"]["output_frame_count"] = total
            item["clock_sample"]["monotonic_ns"] = total * 16_666_667
    for index, scene in enumerate(scenes, start=1):
        events.insert(index, scene)
    return raw


def _sidecar(raw_sidecar: dict[str, object], **kwargs: Any) -> ObsEventSidecarV12:
    return ObsEventSidecarV12.model_validate_json(json.dumps(_raw(raw_sidecar, **kwargs)))


# --------------------------------------------------------------------------
# Ableitung
# --------------------------------------------------------------------------


def test_a_zero_start_latency_yields_no_correction(raw_sidecar: dict[str, object]) -> None:
    """Sichert ab, dass die vorhandene Suite nicht zufällig grün ist.

    Alle Altfixtures bauen ``recording_started`` auf Frame 0 und damit auf
    ``monotonic_ns = 0``.  Wäre der Lag dort nicht exakt 0, hätte sich jede
    feste Framezahl in den Alttests verschoben.
    """
    assert pipeline_lag_frames(_sidecar(raw_sidecar, start_ns=0)) == 0


@pytest.mark.parametrize(("nanoseconds", "frames", "run"), REAL_START_LATENCIES)
def test_the_measured_start_latencies_map_to_their_measured_frame_offsets(
    raw_sidecar: dict[str, object], nanoseconds: int, frames: int, run: str
) -> None:
    """Die vier real vorgekommenen Anlaufzeiten, mit ihrem Lauf als Herkunft."""
    assert pipeline_lag_frames(_sidecar(raw_sidecar, start_ns=nanoseconds)) == frames, run


@pytest.mark.parametrize(
    ("nanoseconds", "frames"),
    [
        (8_333_333, 0),  # exakt eine halbe Frame minus 1 ns -> ab
        (8_333_334, 1),  # exakt eine halbe Frame -> auf
        (24_999_999, 1),
        (25_000_000, 2),  # anderthalb Frames -> auf
    ],
)
def test_half_frames_round_upwards_like_the_calibration(
    raw_sidecar: dict[str, object], nanoseconds: int, frames: int
) -> None:
    """Gleiche Rundungsrichtung wie ``_round_half_up`` in ``calibration.py``."""
    assert pipeline_lag_frames(_sidecar(raw_sidecar, start_ns=nanoseconds)) == frames


@pytest.mark.parametrize("copies", [0, 2])
def test_an_unusable_start_anchor_yields_no_correction(
    raw_sidecar: dict[str, object], copies: int
) -> None:
    """Ohne genau ein Ankerereignis ist die Größe unbestimmbar; Raten wäre schlechter.

    Die Sidecar-Validierung garantiert genau eines; dies sichert den Zweig ab,
    der greift, wenn diese Garantie einmal nicht mehr gilt.
    """
    raw = _raw(raw_sidecar, start_ns=283_333_322)
    events = raw["events"]
    assert isinstance(events, list)
    original = next(item for item in events if item["type"] == "recording_started")
    kept = [item for item in events if item["type"] != "recording_started"]
    for _ in range(copies):
        duplicate = deepcopy(original)
        duplicate["event_id"] = str(uuid4())
        kept.insert(0, duplicate)
    raw["events"] = kept
    sidecar = ObsEventSidecarV12.model_validate_json(json.dumps(raw))
    assert pipeline_lag_frames(sidecar) == 0


# --------------------------------------------------------------------------
# Reichweite: welche Ereignisse wandern
# --------------------------------------------------------------------------


def test_only_frontend_sampled_events_carry_the_lag() -> None:
    assert set(FRONTEND_SAMPLED_EVENT_TYPES) == {"scene_changed", "manual_protection"}


def test_the_output_anchored_events_never_move(raw_sidecar: dict[str, object]) -> None:
    """``recording_started`` und ``recording_stopped`` sind exakt, nicht verspätet."""
    sidecar = _sidecar(raw_sidecar, start_ns=1_033_333_292)
    lag = pipeline_lag_frames(sidecar)
    assert lag == 62
    for item in sidecar.events:
        if item.type in {"recording_started", "recording_stopped"}:
            assert not is_frontend_sampled(item)
            assert corrected_source_frame(item, lag, 6000) == item.mapped_source_frame


def test_the_protection_block_at_the_recording_start_stays_on_frame_zero(
    raw_sidecar: dict[str, object],
) -> None:
    """Der wichtigste Grund, warum die Anker nicht mitwandern dürfen.

    ``recording_started`` schützt die erste Sekunde.  Um 62 Frames verschoben
    gäbe dieser Block den Anfang der Aufnahme frei.
    """
    without = materialize_protection(_sidecar(raw_sidecar, start_ns=0))
    with_lag = materialize_protection(_sidecar(raw_sidecar, start_ns=1_033_333_292))
    assert without.status == "materialized" and with_lag.status == "materialized"
    assert with_lag.ranges[0].source_start_frame == 0
    assert without.ranges == with_lag.ranges


def test_a_frontend_marker_moves_its_protection_window(raw_sidecar: dict[str, object]) -> None:
    """Eine Zone um den rohen Frame schützt sonst die falsche Stelle."""
    marker = event(str(uuid4()), "manual_protection", 1000, counter=1000)
    quiet = _sidecar(raw_sidecar, start_ns=0, scenes=(marker,))
    delayed = _sidecar(raw_sidecar, start_ns=1_033_333_292, scenes=(deepcopy(marker),))
    lag = pipeline_lag_frames(delayed)

    def window(result: object) -> tuple[int, int]:
        ranges = [
            item
            for item in result.ranges  # type: ignore[attr-defined]
            if 500 < item.source_start_frame < 2000
        ]
        assert len(ranges) == 1
        return ranges[0].source_start_frame, ranges[0].source_end_frame

    quiet_start, quiet_end = window(materialize_protection(quiet))
    delayed_start, delayed_end = window(materialize_protection(delayed))
    assert delayed_start - quiet_start == lag == 62
    assert delayed_end - quiet_end == lag


# --------------------------------------------------------------------------
# Wirkung auf den Intro-Schnitt
# --------------------------------------------------------------------------


def test_the_intro_cut_sits_behind_the_visible_scene_start(
    raw_sidecar: dict[str, object],
) -> None:
    """Marke plus Lag plus gemessener Stingvorlauf, nicht Marke plus Konstante."""
    sidecar = _sidecar(
        raw_sidecar, start_ns=1_033_333_292, scenes=(_scene(1000, INTRO_SCENE_LABEL),)
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert resolution.pipeline_lag_frames == 62
    assert resolution.intro_start_frame == 1000 + 62 + INTRO_CUT_OFFSET_FRAMES


def test_two_runs_with_the_same_marker_cut_at_different_frames(
    raw_sidecar: dict[str, object],
) -> None:
    """Der Kern des Befunds: gleiche Marke, verschiedene Anlaufzeit, anderer Schnitt."""
    quick = resolve_intro(
        _sidecar(raw_sidecar, start_ns=283_333_322, scenes=(_scene(1000, INTRO_SCENE_LABEL),)),
        sidecar_sha256=SIDECAR_SHA,
    )
    slow = resolve_intro(
        _sidecar(raw_sidecar, start_ns=1_033_333_292, scenes=(_scene(1000, INTRO_SCENE_LABEL),)),
        sidecar_sha256=SIDECAR_SHA,
    )
    assert quick.intro_start_frame is not None and slow.intro_start_frame is not None
    assert slow.intro_start_frame - quick.intro_start_frame == 62 - 17 == 45


def test_a_marker_on_frame_zero_still_removes_nothing(raw_sidecar: dict[str, object]) -> None:
    """Der Lag verschiebt den sichtbaren Anfang, nicht die fehlende Vorszene."""
    resolution = resolve_intro(
        _sidecar(raw_sidecar, start_ns=1_033_333_292, scenes=(_scene(0, INTRO_SCENE_LABEL),)),
        sidecar_sha256=SIDECAR_SHA,
    )
    assert resolution.status == "nothing_before_intro"
    assert resolution.intro_start_frame == 0


def test_the_lag_can_push_the_cut_out_of_bounds(raw_sidecar: dict[str, object]) -> None:
    """Randlage: erst der Lag trägt die Marke über das Quellende hinaus."""
    marker = 6000 - INTRO_CUT_OFFSET_FRAMES - 30
    inside = resolve_intro(
        _sidecar(raw_sidecar, start_ns=0, scenes=(_scene(marker, INTRO_SCENE_LABEL),)),
        sidecar_sha256=SIDECAR_SHA,
    )
    outside = resolve_intro(
        _sidecar(raw_sidecar, start_ns=1_033_333_292, scenes=(_scene(marker, INTRO_SCENE_LABEL),)),
        sidecar_sha256=SIDECAR_SHA,
    )
    assert inside.status == "resolved"
    assert outside.status == "event_out_of_bounds"


# --------------------------------------------------------------------------
# Bewusst offen gelassen
# --------------------------------------------------------------------------


def test_frame_loss_before_the_marker_is_not_corrected(raw_sidecar: dict[str, object]) -> None:
    """Dokumentiert eine bekannte Lücke, sie wird hier nicht repariert.

    ``2026-08-09 08-14-05`` verlor 45 Frames bei 4,0 s.  Der Intro-Wechsel davor
    maß +15, die späteren Wechsel dahinter +61 und +59 — also Lag plus die
    verlorenen Frames.  Die Korrektur kennt nur den Lag; ein Verlust vor der
    Marke bleibt unkorrigiert.  Selten (1 von 17 Läufen), dann aber 45 Frames.

    Fällt dieser Test, weil der Verlustterm gebaut wurde: erwartete Zahl
    anpassen und die Lücke im Bericht streichen.
    """
    sidecar = _sidecar(raw_sidecar, start_ns=283_333_322, scenes=(_scene(1000, INTRO_SCENE_LABEL),))
    assert sidecar.finalization.warnings == ()
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.pipeline_lag_frames == 17
    # Kein Term für Frameverlust: der Schnitt folgt allein Marke, Lag, Offset.
    assert resolution.intro_start_frame == 1000 + 17 + INTRO_CUT_OFFSET_FRAMES


def test_the_report_path_for_the_measurement_exists() -> None:
    """Die Herkunft der Zahlen bleibt auffindbar."""
    assert Path("docs/repeat/INTRO-CUT-BEFUND-2026-08-09.md").is_file()
