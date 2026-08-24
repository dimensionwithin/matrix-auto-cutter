"""Tests fuer den kopflosen Ausgabeweg der Wortliste (``wortliste.json``)."""

from __future__ import annotations

import json
from pathlib import Path

from matrix_auto_cutter.shorts import wortliste
from matrix_auto_cutter.shorts.transcript import RENDERED_WAV_NAME, transcript_paths


def _token(text: str, start_ms: int, end_ms: int) -> dict[str, object]:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


def _whisper_json(tokens: list[dict[str, object]]) -> str:
    return json.dumps({"transcription": [{"tokens": tokens}]})


def _setup_job(tmp_path: Path) -> Path:
    job_path = tmp_path / "shorts-job.json"
    job_path.write_text(json.dumps({"rendered_video": {"path": "video.mp4"}}), encoding="utf-8")
    return job_path


def _write_rohausgabe(job_path: Path, raw_json: str) -> Path:
    raw_json_path, _ = transcript_paths(job_path.parent, wav_name=RENDERED_WAV_NAME)
    raw_json_path.write_text(raw_json, encoding="utf-8")
    return raw_json_path


def test_erfolgsfall_baut_wortliste(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)
    raw = _whisper_json(
        [
            _token(" Hallo", 0, 100),
            _token(" Welt", 100, 200),
            _token(",", 200, 210),
        ]
    )
    _write_rohausgabe(job_path, raw)

    code = wortliste.main([str(job_path)])

    assert code == 0
    ziel = wortliste.wortliste_pfad(job_path)
    payload = json.loads(ziel.read_text(encoding="utf-8"))
    assert payload["artifact_type"] == "matrix_auto_cutter_shorts_wortliste"
    assert payload["wort_anzahl"] == 2
    assert [w["text"] for w in payload["woerter"]] == ["Hallo", "Welt,"]
    assert payload["woerter"][0]["start_ms"] == 0
    assert payload["woerter"][1]["end_ms"] == 210


def test_vorhandene_ausgabe_wird_wiederverwendet(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)
    raw = _whisper_json([_token(" Eins", 0, 100)])
    _write_rohausgabe(job_path, raw)

    assert wortliste.main([str(job_path)]) == 0
    ziel = wortliste.wortliste_pfad(job_path)
    inhalt_vorher = ziel.read_bytes()

    # Rohausgabe aendern - bei Wiederverwendung darf das keine Rolle spielen.
    _write_rohausgabe(job_path, _whisper_json([_token(" Zwei", 0, 100), _token(" Drei", 100, 200)]))

    code = wortliste.main([str(job_path)])

    assert code == 0
    assert ziel.read_bytes() == inhalt_vorher


def test_force_rechnet_neu(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)
    _write_rohausgabe(job_path, _whisper_json([_token(" Eins", 0, 100)]))
    assert wortliste.main([str(job_path)]) == 0

    _write_rohausgabe(job_path, _whisper_json([_token(" Zwei", 0, 100), _token(" Drei", 100, 200)]))
    code = wortliste.main([str(job_path), "--force"])

    assert code == 0
    ziel = wortliste.wortliste_pfad(job_path)
    payload = json.loads(ziel.read_text(encoding="utf-8"))
    assert payload["wort_anzahl"] == 2


def test_kaputte_rohausgabe_kein_json(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)
    _write_rohausgabe(job_path, "das ist kein JSON {")

    code = wortliste.main([str(job_path)])

    assert code == wortliste.RUECKGABECODE_ROHAUSGABE_KAPUTT
    assert not wortliste.wortliste_pfad(job_path).exists()


def test_null_woerter(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)
    _write_rohausgabe(job_path, _whisper_json([_token("[_BEG_]", 0, 0)]))

    code = wortliste.main([str(job_path)])

    assert code == wortliste.RUECKGABECODE_NULL_WOERTER
    assert not wortliste.wortliste_pfad(job_path).exists()


def test_fehlende_rohausgabe(tmp_path: Path) -> None:
    job_path = _setup_job(tmp_path)

    code = wortliste.main([str(job_path)])

    assert code == wortliste.RUECKGABECODE_ROHAUSGABE_FEHLT
