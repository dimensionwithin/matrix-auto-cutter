"""Tests für Auftrag shorts-auswahl: aus Kandidaten plus Urteilen eine Bauliste machen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auswahl
from matrix_auto_cutter.shorts.judge_server import Urteil, write_urteile


def _kandidat(
    index: int,
    *,
    start_ms: int = 0,
    end_ms: int = 10_000,
    titel: str = "Titel",
    sicherheit: str = "hoch",
    polarisierend: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "index": index,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "titel": titel,
        "begruendung": "Begruendung",
        "sicherheit": sicherheit,
        "enthaelt": [],
    }
    if polarisierend is not None:
        payload["polarisierend"] = polarisierend
    return payload


def _schreibe_kandidaten(
    pfad: Path,
    kandidaten: list[dict[str, object]],
    *,
    wurzelfelder: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"kandidaten": kandidaten}
    if wurzelfelder:
        payload.update(wurzelfelder)
    pfad.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _urteil(
    index: int,
    urteil: str | None,
    *,
    start_ms: int = 0,
    end_ms: int = 10_000,
    titel: str = "Titel",
) -> Urteil:
    return Urteil(
        index=index,
        titel=titel,
        start_ms=start_ms,
        end_ms=end_ms,
        ist_kind=False,
        urteil=urteil,
        notiz="",
    )


def test_erfolgsfall_mit_gemischten_urteilen(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(
        job_dir / "kandidaten.json",
        [
            _kandidat(0, titel="Angenommen"),
            _kandidat(1, titel="Abgelehnt"),
            _kandidat(2, titel="Ohne Urteil"),
        ],
    )
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(
        urteile_path,
        {
            0: _urteil(0, "ja", titel="Angenommen"),
            1: _urteil(1, "nein", titel="Abgelehnt"),
        },
    )

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 0
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert bauliste["angenommen"] == 1
    assert bauliste["abgelehnt"] == 1
    assert bauliste["ohne_urteil"] == 1
    assert [k["index"] for k in bauliste["kandidaten"]] == [0]
    assert bauliste["kandidaten"][0]["titel"] == "Angenommen"


def test_kandidat_ohne_urteil_wird_nicht_gebaut(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(
        job_dir / "kandidaten.json",
        [_kandidat(0, titel="Angenommen"), _kandidat(1, titel="Ohne Urteil")],
    )
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "ja", titel="Angenommen")})

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 0
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert bauliste["angenommen"] == 1
    assert bauliste["ohne_urteil"] == 1
    assert [k["index"] for k in bauliste["kandidaten"]] == [0]


def test_spaeter_gilt_nicht_als_annahme(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(
        job_dir / "kandidaten.json",
        [_kandidat(0, titel="Angenommen"), _kandidat(1, titel="Spaeter")],
    )
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(
        urteile_path,
        {0: _urteil(0, "ja", titel="Angenommen"), 1: _urteil(1, "spaeter", titel="Spaeter")},
    )

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 0
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert bauliste["angenommen"] == 1
    assert bauliste["abgelehnt"] == 1
    assert [k["index"] for k in bauliste["kandidaten"]] == [0]


def test_abweichendes_end_ms_haelt_an_ohne_datei(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(job_dir / "kandidaten.json", [_kandidat(0, end_ms=10_000)])
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "ja", end_ms=99_999)})

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 5
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()


def test_keine_urteilsdatei_haelt_an(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(job_dir / "kandidaten.json", [_kandidat(0)])

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 2
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()


def test_null_annahmen_haelt_an(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(job_dir / "kandidaten.json", [_kandidat(0, titel="Abgelehnt")])
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "nein", titel="Abgelehnt")})

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 4
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()


def test_indizes_beginnen_nicht_bei_null_bleiben_erhalten(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(
        job_dir / "kandidaten.json",
        [_kandidat(5, titel="Fuenf"), _kandidat(7, titel="Sieben")],
    )
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(
        urteile_path,
        {5: _urteil(5, "ja", titel="Fuenf"), 7: _urteil(7, "nein", titel="Sieben")},
    )

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 0
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert [k["index"] for k in bauliste["kandidaten"]] == [5]


def test_urteil_ohne_zugehoerigen_kandidaten_haelt_an(tmp_path: Path) -> None:
    job_dir = tmp_path
    _schreibe_kandidaten(job_dir / "kandidaten.json", [_kandidat(0)])
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "ja"), 9: _urteil(9, "ja", titel="Geist")})

    code = auswahl.main([str(job_dir), "--keine-trefferquote"])

    assert code == 5
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()


def test_wurzelfelder_der_kandidatendatei_landen_im_trefferquote_eintrag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trefferquote_path = tmp_path / "trefferquote.json"
    monkeypatch.setattr(auswahl, "TREFFERQUOTE_PFAD", trefferquote_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _schreibe_kandidaten(
        job_dir / "kandidaten.json",
        [_kandidat(0, titel="Angenommen")],
        wurzelfelder={
            "modell": "sonnet",
            "lauf": 1,
            "kriterien_fassung": "Fassung 0.8 (24. August 2026)",
            "video_name": "2026-08-21 10-46-08",
        },
    )
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "ja", titel="Angenommen")})

    code = auswahl.main([str(job_dir)])

    assert code == 0
    daten = json.loads(trefferquote_path.read_text(encoding="utf-8"))
    eintrag = daten["eintraege"][0]
    assert eintrag["modell"] == "sonnet"
    assert eintrag["lauf"] == 1
    assert eintrag["kriterien_fassung"] == "Fassung 0.8 (24. August 2026)"
    assert eintrag["video_name"] == "2026-08-21 10-46-08"


def test_fehlende_wurzelfelder_ergeben_unbekannt_bzw_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trefferquote_path = tmp_path / "trefferquote.json"
    monkeypatch.setattr(auswahl, "TREFFERQUOTE_PFAD", trefferquote_path)
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _schreibe_kandidaten(job_dir / "kandidaten.json", [_kandidat(0, titel="Angenommen")])
    urteile_path = job_dir / "urteile-2026-08-25-120000.json"
    write_urteile(urteile_path, {0: _urteil(0, "ja", titel="Angenommen")})

    code = auswahl.main([str(job_dir)])

    assert code == 0
    daten = json.loads(trefferquote_path.read_text(encoding="utf-8"))
    eintrag = daten["eintraege"][0]
    assert eintrag["modell"] == "unbekannt"
    assert eintrag["kriterien_fassung"] == "unbekannt"
    assert eintrag["video_name"] == "unbekannt"
    assert eintrag["lauf"] is None
