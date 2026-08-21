"""Tests fuer Auftrag shorts-szenenfilter, Teil B: Szenenfenster aus dem Journal.

PRUEFSTEIN-Belegwerte aus der realen Aufnahme "2026-08-17 20-47-23"
(``artefakte/repeat/shorts-szenenfilter/BERICHT-2026-08-17.md``): genau ein
Charts-Fenster, ``output_frame_count`` 1270 bis 3295, stop bei 4366.
"""

from __future__ import annotations

import json
from pathlib import Path

from matrix_auto_cutter.shorts.scene_windows import (
    CHARTS_SCENE_LABEL,
    REASON_MISSING_STOP,
    REASON_UNKNOWN_INITIAL_SCENE,
    SceneWindow,
    SceneWindowsFailed,
    load_scene_windows,
    scene_windows_from_records,
)


def _event(event_type: str, frame: int, *, label: str | None = None) -> dict:
    record = {"record_type": "event", "event_type": event_type, "output_frame_count": frame}
    if label is not None:
        record["label"] = label
    return record


def _stop(frame: int) -> dict:
    return {"record_type": "stop", "output_frame_count": frame}


def _calibration(frame: int) -> dict:
    return {"record_type": "calibration_sample", "output_frame_count": frame}


# --- Konstante -----------------------------------------------------------------


def test_charts_scene_label_is_charts_exactly() -> None:
    assert CHARTS_SCENE_LABEL == "Charts"


# --- Pruefstein: reale Aufnahme 2026-08-17 20-47-23 -----------------------------


def _pruefstein_records() -> list[dict]:
    return [
        _event("recording_started", 1),
        _calibration(120),
        _event("scene_changed", 303, label="Intro with Cam"),
        _calibration(1208),
        _event("scene_changed", 1270, label="Charts"),
        _calibration(3265),
        _event("scene_changed", 3295, label="Outro"),
        _calibration(4354),
        _stop(4366),
    ]


def test_pruefstein_genau_ein_charts_fenster() -> None:
    result = scene_windows_from_records(_pruefstein_records())
    assert result == (SceneWindow(1270, 3295),)


def test_pruefstein_ignoriert_kalibrierproben_und_andere_szenen() -> None:
    result = scene_windows_from_records(_pruefstein_records())
    assert isinstance(result, tuple)
    assert len(result) == 1


# --- Mehrere Charts-Fenster ------------------------------------------------------


def test_mehrere_charts_fenster_werden_alle_gefunden() -> None:
    records = [
        _event("recording_started", 1),
        _event("scene_changed", 100, label="Intro"),
        _event("scene_changed", 500, label="Charts"),
        _event("scene_changed", 900, label="Talk"),
        _event("scene_changed", 1400, label="Charts"),
        _stop(2000),
    ]
    result = scene_windows_from_records(records)
    assert result == (SceneWindow(500, 900), SceneWindow(1400, 2000))


# --- Anfangsszene dokumentiert (recording_started traegt label) ------------------


def test_anfangsszene_ist_charts_und_dokumentiert() -> None:
    records = [
        _event("recording_started", 1, label="Charts"),
        _event("scene_changed", 400, label="Outro"),
        _stop(1000),
    ]
    result = scene_windows_from_records(records)
    assert result == (SceneWindow(0, 400),)


def test_anfangsszene_dokumentiert_aber_nicht_charts_liefert_leere_liste() -> None:
    records = [
        _event("recording_started", 1, label="Intro with Cam"),
        _event("scene_changed", 400, label="Outro"),
        _stop(1000),
    ]
    result = scene_windows_from_records(records)
    assert result == ()


def test_anfangsszene_charts_plus_spaeteres_charts_fenster() -> None:
    """Fuehrendes Fenster (dokumentierte Anfangsszene) und ein spaeteres per Wechsel."""
    records = [
        _event("recording_started", 1, label="Charts"),
        _event("scene_changed", 300, label="Talk"),
        _event("scene_changed", 800, label="Charts"),
        _stop(1200),
    ]
    result = scene_windows_from_records(records)
    assert result == (SceneWindow(0, 300), SceneWindow(800, 1200))


# --- Bekannte Luecke: Anfangsszene nicht dokumentiert -----------------------------


def test_unbekannte_anfangsszene_ohne_jeden_charts_fund_meldet_eigenen_fehlercode() -> None:
    """Kein scene_changed-Ereignis ueberhaupt: die ganze Aufnahme koennte Charts sein."""
    records = [
        _event("recording_started", 1),
        _stop(1000),
    ]
    result = scene_windows_from_records(records)
    assert isinstance(result, SceneWindowsFailed)
    assert result.reason == REASON_UNKNOWN_INITIAL_SCENE


def test_unbekannte_anfangsszene_mit_anderen_szenenwechseln_aber_ohne_charts() -> None:
    """scene_changed-Zeilen existieren, aber keine davon ist Charts - Anfangsszene bleibt offen."""
    records = [
        _event("recording_started", 1),
        _event("scene_changed", 400, label="Outro"),
        _stop(1000),
    ]
    result = scene_windows_from_records(records)
    assert isinstance(result, SceneWindowsFailed)
    assert result.reason == REASON_UNKNOWN_INITIAL_SCENE


def test_unbekannte_anfangsszene_aber_spaeteres_charts_fenster_gefunden_kein_fehler() -> None:
    """Sobald ein Charts-Fenster ueber einen Wechsel gefunden wird, ist es kein leeres Ergebnis."""
    records = [
        _event("recording_started", 1),
        _event("scene_changed", 400, label="Charts"),
        _stop(1000),
    ]
    result = scene_windows_from_records(records)
    assert result == (SceneWindow(400, 1000),)


def test_fehlender_stop_record_meldet_eigenen_fehlercode() -> None:
    records = [
        _event("recording_started", 1),
        _event("scene_changed", 400, label="Charts"),
    ]
    result = scene_windows_from_records(records)
    assert isinstance(result, SceneWindowsFailed)
    assert result.reason == REASON_MISSING_STOP


# --- Eigener Szenenname (Konstante ist konfigurierbar) ---------------------------


def test_scene_label_ist_konfigurierbar() -> None:
    records = [
        _event("recording_started", 1, label="Intro"),
        _event("scene_changed", 400, label="Outro"),
        _stop(1000),
    ]
    result = scene_windows_from_records(records, scene_label="Outro")
    assert result == (SceneWindow(400, 1000),)


# --- SceneWindow-Invariante --------------------------------------------------------


def test_scene_window_erzwingt_halboffenes_intervall() -> None:
    import pytest

    with pytest.raises(ValueError):
        SceneWindow(10, 10)
    with pytest.raises(ValueError):
        SceneWindow(-1, 10)


# --- load_scene_windows liest eine echte ndjson-Datei -----------------------------


def test_load_scene_windows_liest_ndjson_datei(tmp_path: Path) -> None:
    journal_path = tmp_path / "sample.recording-journal.ndjson"
    lines = [json.dumps(record) for record in _pruefstein_records()]
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = load_scene_windows(journal_path)
    assert result == (SceneWindow(1270, 3295),)


def test_load_scene_windows_ueberspringt_leerzeilen(tmp_path: Path) -> None:
    journal_path = tmp_path / "sample.recording-journal.ndjson"
    lines = [json.dumps(record) for record in _pruefstein_records()]
    journal_path.write_text("\n\n".join(lines) + "\n\n", encoding="utf-8")
    result = load_scene_windows(journal_path)
    assert result == (SceneWindow(1270, 3295),)
