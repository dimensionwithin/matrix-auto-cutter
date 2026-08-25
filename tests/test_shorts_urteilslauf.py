"""Tests für Auftrag urteilslauf: ein Befehl statt vier Handgriffen.

Kein Test startet den Urteilsserver - jeder Lauf geht über
``--kein-server``. ``TREFFERQUOTE_PFAD`` ist relativ zum
Arbeitsverzeichnis und wird deshalb in jedem Test, der ``auswahl``
laufen lässt, per ``monkeypatch`` auf eine Datei unter ``tmp_path``
umgebogen - sonst schriebe der Test in die echte
``labels/repeat/trefferquote.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.shorts import auswahl, urteilslauf
from matrix_auto_cutter.shorts.judge_server import Urteil, write_urteile


def _kandidat(
    index: int,
    *,
    start_ms: int = 0,
    end_ms: int = 10_000,
    titel: str = "Titel",
) -> dict[str, object]:
    return {
        "index": index,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "titel": titel,
        "begruendung": "Begruendung",
        "sicherheit": "hoch",
        "enthaelt": [],
    }


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


_STANDARD_WURZEL: dict[str, object] = {
    "video_name": "2026-08-21 10-46-08",
    "lauf": 1,
    "modell": "sonnet",
}


def _baue_aufnahme(
    job_dir: Path,
    *,
    wurzelfelder: dict[str, object] | None = _STANDARD_WURZEL,
    urteile: dict[int, Urteil] | None = None,
) -> Path:
    """Lege einen vollständigen Auftragsordner an: Kandidaten, Auftrag, Urteile."""
    job_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "kandidaten": [_kandidat(0, titel="Erster"), _kandidat(1, titel="Zweiter")]
    }
    payload.update(wurzelfelder or {})
    (job_dir / "kandidaten.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (job_dir / "shorts-job.json").write_text(
        json.dumps({"rendered_video": {"path": "nicht-vorhanden.mp4"}}), encoding="utf-8"
    )
    if urteile is None:
        urteile = {
            0: _urteil(0, "ja", titel="Erster"),
            1: _urteil(1, "nein", titel="Zweiter"),
        }
    write_urteile(job_dir / "urteile-2026-08-25-120000.json", urteile)
    return job_dir / "shorts-job.json"


@pytest.fixture
def trefferquote_umgebogen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Biege ``TREFFERQUOTE_PFAD`` auf eine Testdatei um, nie auf die echte."""
    pfad = tmp_path / "trefferquote-test.json"
    monkeypatch.setattr(auswahl, "TREFFERQUOTE_PFAD", pfad)
    return pfad


def test_finde_aufnahme_nimmt_juengste_kandidatendatei(tmp_path: Path) -> None:
    """Entscheidend ist die Änderungszeit der ``kandidaten.json``, nicht der Ordnername."""
    basis = tmp_path / urteilslauf.AUFNAHMEN_UNTERPFAD
    alt = basis / "2026-08-01 00-00-00"
    neu = basis / "2026-07-01 00-00-00"
    for ordner in (alt, neu):
        _baue_aufnahme(ordner)
    ohne_kandidaten = basis / "2026-09-01 00-00-00"
    ohne_kandidaten.mkdir(parents=True)

    os.utime(alt / "kandidaten.json", (1_000_000, 1_000_000))
    os.utime(neu / "kandidaten.json", (2_000_000, 2_000_000))

    assert urteilslauf.finde_aufnahme(tmp_path) == neu


def test_kein_ordner_mit_kandidaten_gibt_code_2(tmp_path: Path) -> None:
    (tmp_path / urteilslauf.AUFNAHMEN_UNTERPFAD / "leer").mkdir(parents=True)

    code = urteilslauf.main(["--wurzel", str(tmp_path), "--kein-server"])

    assert code == 2


def test_abweichendes_end_ms_haelt_an_ohne_bauliste_und_sicherung(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(
        job_dir,
        urteile={
            0: _urteil(0, "ja", titel="Erster"),
            1: _urteil(1, "ja", titel="Zweiter", end_ms=99_999),
        },
    )

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 5
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()
    assert not (tmp_path / urteilslauf.SICHERUNG_DIR).exists()


def test_erfolgsfall_erzeugt_bauliste_und_beide_sicherungen(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    assert (job_dir / auswahl.BAULISTE_FILE_NAME).is_file()
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    assert (ziel_dir / "urteile-2026-08-21 10-46-08-lauf1-sonnet.json").is_file()
    assert (ziel_dir / "kandidaten-2026-08-21 10-46-08-lauf1-sonnet.json").is_file()


def test_vorhandene_sicherung_bleibt_unberuehrt(
    tmp_path: Path, trefferquote_umgebogen: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    ziel_dir.mkdir(parents=True)
    bestand = ziel_dir / "urteile-2026-08-21 10-46-08-lauf1-sonnet.json"
    bestand.write_text("aelterer Bestand", encoding="utf-8")

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    assert bestand.read_text(encoding="utf-8") == "aelterer Bestand"
    assert "nicht ueberschrieben" in capsys.readouterr().out
    assert (ziel_dir / "kandidaten-2026-08-21 10-46-08-lauf1-sonnet.json").is_file()


def test_fehlende_wurzelfelder_geben_unbekannt_im_namen(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, wurzelfelder=None)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    assert (ziel_dir / "urteile-unbekannt-laufunbekannt-unbekannt.json").is_file()
    assert (ziel_dir / "kandidaten-unbekannt-laufunbekannt-unbekannt.json").is_file()


def test_keine_sicherung_laesst_labels_unberuehrt(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--keine-sicherung", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    assert (job_dir / auswahl.BAULISTE_FILE_NAME).is_file()
    assert not (tmp_path / urteilslauf.SICHERUNG_DIR).exists()


def test_keine_auswahl_laesst_die_bauliste_aus(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--keine-auswahl", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()
    assert (tmp_path / urteilslauf.SICHERUNG_DIR).is_dir()


def test_quotenzeile_und_baubefehl_stehen_am_ende(
    tmp_path: Path, trefferquote_umgebogen: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--keine-sicherung", "--wurzel", str(tmp_path)]
    )
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "  2 von 2 beurteilt - 1 ja, 1 nein, 0 offen" in ausgabe.splitlines()
    assert (
        r'--output-dir "F:\MatrixMarketAutoEdit\Shorts-Rendered\2026-08-21 10-46-08"' in ausgabe
    )


def _kurzer_platzhalter(sekunden: float) -> subprocess.Popen[bytes]:
    """Ein Kindprozess, der nur schläft - nie der echte Urteilsserver."""
    return subprocess.Popen(
        [sys.executable, "-c", f"import time; time.sleep({sekunden})"],
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def aufgezeichnete_kinder(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Any]]:
    """Merke jeden gestarteten Kindprozess und räume ihn am Testende sicher weg."""
    kinder: list[Any] = []
    echtes_popen = subprocess.Popen

    def _merke(*args: Any, **kwargs: Any) -> Any:
        kind = echtes_popen(*args, **kwargs)
        kinder.append(kind)
        return kind

    monkeypatch.setattr(urteilslauf.subprocess, "Popen", _merke)
    yield kinder
    for kind in kinder:
        if kind.poll() is None:
            kind.kill()
            kind.wait(timeout=5)


def _lauf_mit_abgefangenem_strg_c(
    tmp_path: Path, job_path: Path, monkeypatch: pytest.MonkeyPatch
) -> int:
    """Lasse ``main`` mit Platzhalter laufen, das Warten bricht mit Strg+C ab."""

    def _unterbrochen(process: Any, merker: Any = None) -> int | None:
        raise KeyboardInterrupt

    monkeypatch.setattr(urteilslauf, "warte_auf_kind", _unterbrochen)
    return urteilslauf.main(
        [
            str(job_path),
            "--platzhalter-server",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )


def test_warteschleife_kehrt_zurueck_wenn_das_kind_von_selbst_endet() -> None:
    process = _kurzer_platzhalter(0.3)
    try:
        code = urteilslauf.warte_auf_kind(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert code == 0


def test_strg_c_im_warten_bricht_main_nicht_ab(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    aufgezeichnete_kinder: list[Any],
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = _lauf_mit_abgefangenem_strg_c(tmp_path, job_path, monkeypatch)
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    assert "Strg+C empfangen - Urteilsseite wird beendet." in zeilen
    assert "Schritt 4: Urteile zaehlen" in zeilen
    assert "  2 von 2 beurteilt - 1 ja, 1 nein, 0 offen" in zeilen
    assert any(zeile.startswith("Schritt 7:") for zeile in zeilen)


def test_der_platzhalter_lebt_nach_dem_abfangen_nicht_mehr(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    aufgezeichnete_kinder: list[Any],
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = _lauf_mit_abgefangenem_strg_c(tmp_path, job_path, monkeypatch)
    capsys.readouterr()

    assert code == 0
    assert len(aufgezeichnete_kinder) == 1
    assert aufgezeichnete_kinder[0].poll() is not None
