"""Tests für Stufe 0 kopflos: ``shorts-job.json`` ohne Tk-Fenster.

Kein Test liest ``%LOCALAPPDATA%`` und keiner sieht ``F:\\``:
``build_inventory`` und ``default_state_directory`` sind in jedem Test
umgebogen, die Zeilen kommen aus :func:`_zeile`. Geschrieben wird nur
unter ``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auftrag
from matrix_auto_cutter.shorts.inventory import (
    AvatarMatch,
    CursorMatch,
    ProposalMatch,
    VideoRow,
)


def _zeile(name: str, *, duration_ms: int | None = 584900) -> VideoRow:
    """Eine vollständige Inventarzeile, wie sie der echte Bestand liefert."""
    return VideoRow(
        name=name,
        rendered_path=Path(f"F:/MatrixMarketAutoEdit/Rendered/{name}.matrix-cut.mp4"),
        duration_ms=duration_ms,
        raw_path=Path(f"F:/MatrixMarketAutoEdit/{name}.mp4"),
        raw_exists=True,
        sidecar_path=Path(f"F:/MatrixMarketAutoEdit/{name}.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(
            recording_id="3c3e8763-c985-494a-86b5-651268f4c5ec",
            proposal_path=Path("C:/zustand/artifacts/x/proposals/y/cut-proposal.json"),
            schema_version="1.2",
            candidate_count=1,
            ambiguous=False,
            unclear=False,
        ),
        avatar=AvatarMatch(path=Path(f"F:/ShortsQuellen/Avatar/AvatarWebcam-{name}.mp4"),
                           match_kind="exact"),
        cursor=CursorMatch(
            path=Path(f"F:/ShortsQuellen/Cursor/cursor-{name}.csv"),
            match_kind="sidecar",
            lead_seconds=-0.1575172,
        ),
    )


@pytest.fixture
def bestand(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Biege Zustandsverzeichnis und Bestandsaufnahme auf Testwerte um."""
    zeilen: list[VideoRow] = []

    def _setze(*neu: VideoRow) -> None:
        zeilen[:] = list(neu)

    def _build_inventory(**_kwargs: object) -> list[VideoRow]:
        return list(zeilen)

    monkeypatch.setattr(auftrag, "default_state_directory", lambda: tmp_path / "zustand")
    monkeypatch.setattr(auftrag, "build_inventory", _build_inventory)
    return _setze


def test_erfolgsfall_schreibt_jedes_pflichtfeld(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand(_zeile("2026-08-21 10-46-08"))
    ziel = tmp_path / "probe" / "shorts-job.json"

    code = auftrag.main(["--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    inhalt = json.loads(ziel.read_text(encoding="utf-8"))
    assert auftrag.fehlende_pflichtfelder(inhalt) == []
    assert inhalt["video_name"] == "2026-08-21 10-46-08"
    assert inhalt["rendered_video"]["duration_ms"] == 584900
    assert inhalt["artifact_type"] == "matrix_auto_cutter_shorts_job"
    assert "Auftragsdatei geschrieben:" in ausgabe
    assert "(2026-08-21 10-46-08, 584900 ms)" in ausgabe


def test_vorhandene_datei_bleibt_ohne_force_byteweise_gleich(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand(_zeile("2026-08-21 10-46-08"))
    ziel = tmp_path / "probe" / "shorts-job.json"
    assert auftrag.main(["--ausgabe", str(ziel)]) == 0
    vorher = ziel.read_bytes()
    capsys.readouterr()

    code = auftrag.main(["--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert ziel.read_bytes() == vorher
    assert "vorhanden, unveraendert uebernommen" in ausgabe


def test_force_schreibt_die_datei_neu(bestand, tmp_path: Path) -> None:
    bestand(_zeile("2026-08-21 10-46-08"))
    ziel = tmp_path / "probe" / "shorts-job.json"
    assert auftrag.main(["--ausgabe", str(ziel)]) == 0
    vorher = json.loads(ziel.read_text(encoding="utf-8"))

    assert auftrag.main(["--ausgabe", str(ziel), "--force"]) == 0
    nachher = json.loads(ziel.read_text(encoding="utf-8"))

    assert nachher["created_at"] != vorher["created_at"]
    assert {k: v for k, v in nachher.items() if k != "created_at"} == {
        k: v for k, v in vorher.items() if k != "created_at"
    }


def test_unbekannte_aufnahme_endet_mit_zwei(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand(_zeile("2026-08-21 10-46-08"))
    ziel = tmp_path / "probe" / "shorts-job.json"

    code = auftrag.main(["--aufnahme", "1999-01-01 00-00-00", "--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 2
    assert not ziel.exists()
    assert ausgabe.startswith("ANGEHALTEN [aufnahme_unbekannt]:")


def test_ohne_aufnahmen_endet_mit_zwei(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand()
    ziel = tmp_path / "probe" / "shorts-job.json"

    code = auftrag.main(["--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 2
    assert not ziel.exists()
    assert ausgabe.startswith("ANGEHALTEN [keine_aufnahmen]:")


def test_fehlendes_pflichtfeld_endet_mit_vier(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand(_zeile("2026-08-21 10-46-08", duration_ms=None))
    ziel = tmp_path / "probe" / "shorts-job.json"

    code = auftrag.main(["--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 4
    assert not ziel.exists()
    assert ausgabe.startswith("ANGEHALTEN [pflichtfeld_fehlt]:")
    assert "rendered_video.duration_ms" in ausgabe


def test_zustandsverzeichnis_unbekannt_endet_mit_drei(
    bestand, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _kein_zustand() -> Path:
        raise RuntimeError("LOCALAPPDATA ist nicht gesetzt")

    monkeypatch.setattr(auftrag, "default_state_directory", _kein_zustand)
    ziel = tmp_path / "probe" / "shorts-job.json"

    code = auftrag.main(["--ausgabe", str(ziel)])
    ausgabe = capsys.readouterr().out

    assert code == 3
    assert not ziel.exists()
    assert ausgabe.startswith("ANGEHALTEN [zustand_unbekannt]:")


def test_liste_schreibt_nichts(
    bestand, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bestand(_zeile("2026-08-21 10-46-08"), _zeile("2026-08-24 09-00-00"))

    code = auftrag.main(["--liste"])
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    assert zeilen[0] == "2 Aufnahmen gefunden:"
    assert "2026-08-24 09-00-00" in zeilen[1]
    assert "2026-08-21 10-46-08" in zeilen[2]
    assert list(tmp_path.rglob("shorts-job.json")) == []


def test_ohne_aufnahme_wird_die_juengste_gewaehlt(bestand, tmp_path: Path) -> None:
    """Die erste Zeile ist hier die jüngste - gewählt werden muss die letzte."""
    bestand(
        _zeile("2026-08-24 09-00-00"),
        _zeile("2026-08-21 10-46-08"),
        _zeile("2026-08-25 18-30-00"),
    )
    ziel = tmp_path / "probe" / "shorts-job.json"

    assert auftrag.main(["--ausgabe", str(ziel)]) == 0

    inhalt = json.loads(ziel.read_text(encoding="utf-8"))
    assert inhalt["video_name"] == "2026-08-25 18-30-00"


def test_zielpfad_liegt_im_aufnahmeordner() -> None:
    row = _zeile("2026-08-21 10-46-08")

    assert auftrag.zielpfad(row) == Path(
        "artefakte/repeat/shorts/2026-08-21 10-46-08/shorts-job.json"
    )


def test_schreibe_auftrag_laesst_keine_temporaerdatei_zurueck(tmp_path: Path) -> None:
    ziel = tmp_path / "ordner" / "shorts-job.json"

    auftrag.schreibe_auftrag(ziel, {"a": 1})

    assert json.loads(ziel.read_text(encoding="utf-8")) == {"a": 1}
    assert [p.name for p in ziel.parent.iterdir()] == ["shorts-job.json"]
