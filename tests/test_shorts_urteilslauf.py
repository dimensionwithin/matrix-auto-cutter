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
from matrix_auto_cutter.shorts.candidates import load_candidates
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


def _ohne_kandidaten(satz: dict[str, object]) -> dict[str, object]:
    """Die Wurzelfelder ohne die Kandidatenliste - zum Vergleich zweier Baulisten."""
    return {schluessel: wert for schluessel, wert in satz.items() if schluessel != "kandidaten"}


def _baue_aufnahme(
    job_dir: Path,
    *,
    wurzelfelder: dict[str, object] | None = _STANDARD_WURZEL,
    urteile: dict[int, Urteil] | None = None,
    indizes: list[int] | None = None,
) -> Path:
    """Lege einen vollständigen Auftragsordner an: Kandidaten, Auftrag, Urteile.

    ``indizes`` legt einen Satz mit frei gewählten, nicht lückenlosen
    Kandidatennummern an - alle angenommen. So sehen die Teilbau-Tests
    dieselbe Lage wie der echte Bestand, dessen Bauliste bei 1 beginnt und
    Lücken hat.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    if indizes is None:
        kandidaten = [_kandidat(0, titel="Erster"), _kandidat(1, titel="Zweiter")]
    else:
        kandidaten = [
            _kandidat(index, start_ms=index * 20_000, end_ms=index * 20_000 + 10_000)
            for index in indizes
        ]
        if urteile is None:
            urteile = {
                index: _urteil(
                    index, "ja", start_ms=index * 20_000, end_ms=index * 20_000 + 10_000
                )
                for index in indizes
            }
    payload: dict[str, object] = {"kandidaten": kandidaten}
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

    # ``auch_verfallen`` schaltet allein den Verfall ab (Auftrag
    # vorauswahl-verfall): die beiden Ordnernamen liegen einen Monat
    # auseinander und koennen gar nicht beide frisch sein, geprueft wird hier
    # aber die Aenderungszeit und nicht das Alter.
    assert urteilslauf.finde_aufnahme(tmp_path, auch_verfallen=True) == neu


def test_kein_ordner_mit_kandidaten_gibt_code_2(tmp_path: Path) -> None:
    (tmp_path / urteilslauf.AUFNAHMEN_UNTERPFAD / "leer").mkdir(parents=True)

    code = urteilslauf.main(["--wurzel", str(tmp_path), "--kein-server", "--kein-bau"])

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

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

    assert code == 5
    assert not (job_dir / auswahl.BAULISTE_FILE_NAME).exists()
    assert not (tmp_path / urteilslauf.SICHERUNG_DIR).exists()


def test_erfolgsfall_erzeugt_bauliste_und_beide_sicherungen(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

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

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    assert bestand.read_text(encoding="utf-8") == "aelterer Bestand"
    assert "nicht ueberschrieben" in capsys.readouterr().out
    assert (ziel_dir / "kandidaten-2026-08-21 10-46-08-lauf1-sonnet.json").is_file()


def test_fehlende_wurzelfelder_geben_unbekannt_im_namen(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, wurzelfelder=None)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    assert (ziel_dir / "urteile-unbekannt-laufunbekannt-unbekannt.json").is_file()
    assert (ziel_dir / "kandidaten-unbekannt-laufunbekannt-unbekannt.json").is_file()


def test_nur_modelle_gibt_die_modelle_im_namen_und_in_der_trefferquote(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    """Aeltere zusammengefuehrte Saetze tragen nur ``modelle`` - das genuegt.

    ``fuehre_zusammen`` schreibt das Wurzelfeld ``modell`` erst seit dem
    27.8.; davor stand dort nur die Abbildung ``modelle``. Bis dahin hiess
    das Modell in beiden Verbrauchern ``unbekannt`` - im Sicherungsnamen und
    im Trefferquote-Eintrag. Gebildet wird es jetzt genauso, wie
    ``fuehre_zusammen`` es heute schreibt: die Werte in Laufreihenfolge mit
    ``+`` verbunden.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(
        job_dir,
        wurzelfelder={
            "video_name": "2026-08-21 10-46-08",
            "lauf": 1,
            "laeufe": [1, 2],
            "modelle": {"1": "sonnet", "2": "opus"},
        },
    )

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    assert (ziel_dir / "urteile-2026-08-21 10-46-08-lauf1+2-sonnet+opus.json").is_file()
    assert (ziel_dir / "kandidaten-2026-08-21 10-46-08-lauf1+2-sonnet+opus.json").is_file()
    eintraege = json.loads(trefferquote_umgebogen.read_text(encoding="utf-8"))["eintraege"]
    assert [eintrag["modell"] for eintrag in eintraege] == ["sonnet+opus"]


def test_weder_modell_noch_modelle_bleibt_unbekannt(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    """Geraten wird nicht: fehlt beides, steht ``unbekannt`` da wie bisher."""
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(
        job_dir, wurzelfelder={"video_name": "2026-08-21 10-46-08", "lauf": 1}
    )

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )

    assert code == 0
    ziel_dir = tmp_path / urteilslauf.SICHERUNG_DIR
    assert (ziel_dir / "urteile-2026-08-21 10-46-08-lauf1-unbekannt.json").is_file()
    eintraege = json.loads(trefferquote_umgebogen.read_text(encoding="utf-8"))["eintraege"]
    assert [eintrag["modell"] for eintrag in eintraege] == ["unbekannt"]


def test_keine_sicherung_laesst_labels_unberuehrt(
    tmp_path: Path, trefferquote_umgebogen: Path
) -> None:
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [
            str(job_path),
            "--kein-server",
            "--kein-bau",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
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
        [str(job_path), "--kein-server", "--kein-bau", "--keine-auswahl", "--wurzel", str(tmp_path)]
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
        [
            str(job_path),
            "--kein-server",
            "--kein-bau",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "  2 von 2 beurteilt - 1 ja, 1 nein, 0 offen - Quote 50 %" in ausgabe.splitlines()
    assert r'--output-dir "F:\MatrixMarketAutoEdit\Shorts Rendered\2026-08-21 10-46-08"' in ausgabe


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
            "--kein-bau",
            "--platzhalter-server",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )


# Platzhalterprozess: das Warten auf ein echtes Kind ist der Gegenstand.
@pytest.mark.echter_unterprozess
def test_warteschleife_kehrt_zurueck_wenn_das_kind_von_selbst_endet() -> None:
    process = _kurzer_platzhalter(0.3)
    try:
        code = urteilslauf.warte_auf_kind(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert code == 0


# Platzhalterprozess: das Warten auf ein echtes Kind ist der Gegenstand.
@pytest.mark.echter_unterprozess
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
    assert "  2 von 2 beurteilt - 1 ja, 1 nein, 0 offen - Quote 50 %" in zeilen
    assert any(zeile.startswith("Schritt 7:") for zeile in zeilen)


# Platzhalterprozess: das Warten auf ein echtes Kind ist der Gegenstand.
@pytest.mark.echter_unterprozess
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


def test_platzhalter_nimmt_eine_eigene_schlafdauer_an() -> None:
    """--platzhalter-server 12.5 schlaeft 12.5 s, ohne Zahl die Vorgabe 4.0."""
    vorgabe = urteilslauf._server_argv(Path("egal.json"), platzhalter=True)
    eigene = urteilslauf._server_argv(Path("egal.json"), platzhalter=12.5)
    echter = urteilslauf._server_argv(Path("egal.json"), platzhalter=False)

    assert f"time.sleep({urteilslauf._PLATZHALTER_SEKUNDEN})" in vorgabe[-1]
    assert "time.sleep(12.5)" in eigene[-1]
    assert echter[-1] == "egal.json"


# Platzhalterprozess: das Warten auf ein echtes Kind ist der Gegenstand.
@pytest.mark.echter_unterprozess
def test_platzhalter_mit_zahl_laeuft_ueber_die_befehlszeile(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    aufgezeichnete_kinder: list[Any],
) -> None:
    """Die Zahl hinter --platzhalter-server erreicht den Kindprozess wirklich."""
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [
            str(job_path),
            "--kein-bau",
            "--platzhalter-server",
            "0.2",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    assert len(aufgezeichnete_kinder) == 1
    assert aufgezeichnete_kinder[0].poll() == 0
    assert "  Urteilsseite beendet (Rueckgabecode 0) - weiter geht es." in zeilen


# --------------------------------------------------------------------------
# Schritt 7: bauen (TEIL 2 des Auftrags kettenlaeufer)
# --------------------------------------------------------------------------


def _beide_ja() -> dict[int, Urteil]:
    """Beide Kandidaten angenommen - so hat die Bauliste zwei Eintraege statt einem."""
    return {0: _urteil(0, "ja", titel="Erster"), 1: _urteil(1, "ja", titel="Zweiter")}


@pytest.fixture
def bauziel_umgebogen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Biege ``RENDER_WURZEL`` auf ``tmp_path`` um - nie auf das echte ``F:``."""
    wurzel = tmp_path / "Shorts Rendered"
    monkeypatch.setattr(urteilslauf, "RENDER_WURZEL", str(wurzel))
    return wurzel / "2026-08-21 10-46-08"


def test_kein_bau_gibt_die_bauzeile_nur_aus(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--kein-bau ist der Weg fuer Aufnahmen, deren Shorts schon gebaut sind."""

    def _kein_prozess(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mit --kein-bau darf kein Prozess starten")

    monkeypatch.setattr(urteilslauf.subprocess, "Popen", _kein_prozess)
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--kein-bau", "--wurzel", str(tmp_path)]
    )
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    assert "Schritt 7: Bau uebersprungen (--kein-bau) - diese Zeile baut die Shorts:" in zeilen
    assert zeilen[-1].startswith("python -m matrix_auto_cutter.shorts.build ")
    assert not bauziel_umgebogen.exists()


def test_vorhandener_kandidatenordner_wird_nicht_neu_gebaut(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ersatz fuer den frueheren Code-7-Test: belegtes Ziel haelt nicht mehr an.

    Bis zum 27.8. hielt ein belegter Zielordner den Lauf mit Code 7 an,
    weil ``build`` vorhandene Ausgaben nicht ueberspringt. Diese Aufgabe hat
    jetzt die Teilbauliste: gebaut wird nur, was noch offen ist. Die
    Zusicherung des Vorgaengers bleibt dieselbe - die vorhandene
    ``short.mp4`` wird nicht angeruehrt.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())
    fertig = bauziel_umgebogen / "kandidat-00"
    fertig.mkdir(parents=True)
    (fertig / "short.mp4").write_text("fertiger Short", encoding="utf-8")

    gebaute_listen: list[list[int]] = []

    def _baue_nur_offene(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        indizes = urteilslauf.bauliste_indizes(urteilslauf.lies_bauliste(bauliste_pfad))
        gebaute_listen.append(indizes)
        for index in indizes:
            ordner = ziel_dir / f"kandidat-{index:02d}"
            ordner.mkdir(parents=True, exist_ok=True)
            (ordner / "short.mp4").write_text("neuer Short", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _baue_nur_offene)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "ANGEHALTEN" not in ausgabe
    assert gebaute_listen == [[1]]
    assert (fertig / "short.mp4").read_text(encoding="utf-8") == "fertiger Short"


def test_teilbauliste_traegt_nur_die_offenen_mit_gleichen_indizes(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fuenf Eintraege, drei schon gebaut - die Teilbauliste nennt genau die zwei offenen.

    Die ``index``-Werte bleiben unveraendert: ``build`` bildet daraus die
    Ordnernamen, und eine Neunummerierung schriebe den Teilbau in die Ordner
    der schon gebauten Shorts.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, indizes=[1, 4, 7, 12, 25])
    for index in (4, 12, 25):
        ordner = bauziel_umgebogen / f"kandidat-{index:02d}"
        ordner.mkdir(parents=True)
        (ordner / "short.mp4").write_text("fertig", encoding="utf-8")

    gesehen: list[dict[str, object]] = []

    def _merke_bauliste(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        gesehen.append(urteilslauf.lies_bauliste(bauliste_pfad))
        for index in (1, 7):
            ordner = ziel_dir / f"kandidat-{index:02d}"
            ordner.mkdir(parents=True, exist_ok=True)
            (ordner / "short.mp4").write_text("neu", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _merke_bauliste)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    teil = json.loads((job_dir / urteilslauf.TEILBAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert urteilslauf.bauliste_indizes(teil) == [1, 7]
    assert gesehen and urteilslauf.bauliste_indizes(gesehen[0]) == [1, 7]
    # Dieselbe Wurzel wie die Bauliste - nur die Kandidatenliste ist kuerzer.
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert _ohne_kandidaten(teil) == _ohne_kandidaten(bauliste)


def test_verweis_auf_einen_schon_gebauten_kandidaten_faellt_weg(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Fall, an dem der erste echte Teilbau abbrach.

    ``parse_candidates`` weist einen ``enthaelt``-Verweis auf einen
    unbekannten Index zurueck. In der Teilbauliste fehlen die schon
    gebauten Kandidaten - ein Verweis auf einen von ihnen liesse ``build``
    mit ``candidates_unreadable`` anhalten, bevor er den ersten Short
    anfasst. Genau das geschah am 27.8. mit Kandidat 67, der auf den
    bereits gebauten 36 verweist.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, indizes=[4, 7])
    kandidaten_pfad = job_dir / "kandidaten.json"
    satz = json.loads(kandidaten_pfad.read_text(encoding="utf-8"))
    satz["kandidaten"][1]["enthaelt"] = [4]
    kandidaten_pfad.write_text(json.dumps(satz, ensure_ascii=False, indent=2), encoding="utf-8")
    fertig = bauziel_umgebogen / "kandidat-04"
    fertig.mkdir(parents=True)
    (fertig / "short.mp4").write_text("fertig", encoding="utf-8")

    def _baue_offene(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        # So streng wie build: eine unlesbare Kandidatenliste haelt an.
        load_candidates(bauliste_pfad)
        ordner = ziel_dir / "kandidat-07"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "short.mp4").write_text("neu", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _baue_offene)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    teil = json.loads((job_dir / urteilslauf.TEILBAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert urteilslauf.bauliste_indizes(teil) == [7]
    assert teil["kandidaten"][0]["enthaelt"] == []
    # Die Bauliste selbst behaelt ihren Verweis - gekuerzt wird nur die Kopie.
    bauliste = json.loads((job_dir / auswahl.BAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert [eintrag["enthaelt"] for eintrag in bauliste["kandidaten"]] == [[], [4]]


def test_alles_gebaut_baut_nichts_und_endet_mit_null(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kein offener Kandidat heisst kein Bau - und kein Fehlschlag."""

    def _kein_bau(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        raise AssertionError("ist alles gebaut, darf kein Bau starten")

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _kein_bau)
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())
    for index in (0, 1):
        ordner = bauziel_umgebogen / f"kandidat-{index:02d}"
        ordner.mkdir(parents=True)
        (ordner / "short.mp4").write_text("fertig", encoding="utf-8")

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "ist bereits gebaut - nichts zu tun" in ausgabe
    assert "0 neu gebaut, 2 insgesamt" in ausgabe
    assert not (job_dir / urteilslauf.TEILBAULISTE_FILE_NAME).exists()


def test_ordner_ohne_short_gilt_als_offen(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Ordner allein ist kein Nachweis - erst die ``short.mp4`` ist einer.

    Ein abgebrochener Bau laesst ``kandidat-NN`` mit Zwischenstufen zurueck.
    Zaehlte der Ordner als fertig, fiele dieser Kandidat still aus dem
    Teilbau heraus und fehlte am Ende, ohne dass es jemand meldet. Geloescht
    wird der Ordner nicht - ``build`` schreibt hinein.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())
    unfertig = bauziel_umgebogen / "kandidat-00"
    unfertig.mkdir(parents=True)
    (unfertig / "avatar-cut.mp4").write_text("Zwischenstufe", encoding="utf-8")

    def _baue_offene(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        for index in urteilslauf.bauliste_indizes(urteilslauf.lies_bauliste(bauliste_pfad)):
            ordner = ziel_dir / f"kandidat-{index:02d}"
            ordner.mkdir(parents=True, exist_ok=True)
            (ordner / "short.mp4").write_text("Short", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _baue_offene)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])

    assert code == 0
    teil = json.loads((job_dir / urteilslauf.TEILBAULISTE_FILE_NAME).read_text(encoding="utf-8"))
    assert urteilslauf.bauliste_indizes(teil) == [0, 1]
    assert (unfertig / "avatar-cut.mp4").read_text(encoding="utf-8") == "Zwischenstufe"


def test_weniger_neue_shorts_als_offene_eintraege_ist_code_8(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gezaehlt wird der ZUWACHS, nicht der Bestand.

    Ein schon vorhandener Short darf einen ausgefallenen Teilbau nicht
    ausgleichen: zwei offene Eintraege, nur einer neu gebaut - Code 8,
    obwohl danach zwei ``short.mp4`` im Ziel liegen.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, indizes=[1, 4, 7])
    fertig = bauziel_umgebogen / "kandidat-01"
    fertig.mkdir(parents=True)
    (fertig / "short.mp4").write_text("fertig", encoding="utf-8")

    def _halber_teilbau(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        ordner = ziel_dir / "kandidat-04"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "short.mp4").write_text("ein Short", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _halber_teilbau)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 8
    assert "ANGEHALTEN [bau_unvollstaendig]: 1 neue short.mp4 statt 2 - " in zeilen[-1]


def test_nur_offene_zeigen_baut_nichts(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Die Fahne nennt die offenen Indizes - und ruehrt weder Ziel noch Teilbauliste an."""

    def _kein_prozess(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("mit --nur-offene-zeigen darf kein Prozess starten")

    monkeypatch.setattr(urteilslauf.subprocess, "Popen", _kein_prozess)
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, indizes=[1, 4, 7])
    fertig = bauziel_umgebogen / "kandidat-04"
    fertig.mkdir(parents=True)
    (fertig / "short.mp4").write_text("fertig", encoding="utf-8")

    code = urteilslauf.main(
        [str(job_path), "--kein-server", "--nur-offene-zeigen", "--wurzel", str(tmp_path)]
    )
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "  offene Indizes (2): 1, 7" in ausgabe
    assert not (job_dir / urteilslauf.TEILBAULISTE_FILE_NAME).exists()
    assert sorted(eintrag.name for eintrag in bauziel_umgebogen.iterdir()) == ["kandidat-04"]


def test_weniger_shorts_als_baulisteneintraege_ist_code_8(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erfolgsnachweis ist die Zahl der ``short.mp4``, nicht der Code von ``build``.

    Der falsche ``build`` hier endet mit 0 und baut nur einen von zwei
    Shorts - genau der Fall, den ein Blick auf den Rueckgabecode uebersieht.
    """
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())

    def _halber_bau(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        ordner = ziel_dir / "kandidat-00"
        ordner.mkdir(parents=True, exist_ok=True)
        (ordner / "short.mp4").write_text("ein Short", encoding="utf-8")
        return 0

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _halber_bau)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 8
    assert any(
        zeile.startswith("Fertig: ") and " 1 von 2 offenen Shorts neu gebaut " in zeile
        for zeile in zeilen
    )
    assert "ANGEHALTEN [bau_unvollstaendig]: 1 neue short.mp4 statt 2 - " in zeilen[-1]


def test_vollstaendiger_bau_endet_mit_code_0(
    tmp_path: Path,
    trefferquote_umgebogen: Path,
    bauziel_umgebogen: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zwei Baulisteneintraege, zwei ``short.mp4`` - die Schlusszeile nennt beides."""
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())

    def _ganzer_bau(job_path_: Path, bauliste_pfad: Path, ziel_dir: Path) -> int:
        for nummer in range(urteilslauf.zaehle_baulisteneintraege(bauliste_pfad)):
            ordner = ziel_dir / f"kandidat-{nummer:02d}"
            ordner.mkdir(parents=True, exist_ok=True)
            (ordner / "short.mp4").write_text("Short", encoding="utf-8")
        return 3  # Auskunft, kein Fehlschlag - gezaehlt wird die Zahl der Shorts.

    monkeypatch.setattr(urteilslauf, "fuehre_bau_aus", _ganzer_bau)

    code = urteilslauf.main([str(job_path), "--kein-server", "--wurzel", str(tmp_path)])
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    assert zeilen[-1].startswith(f"Fertig: {bauziel_umgebogen} - 2 von 2 offenen Shorts neu ")
    assert zeilen[-1].endswith(", 2 insgesamt im Zielordner")


# --------------------------------------------------------------------------
# Quote in Prozent und Nachschlagbefehl
# --------------------------------------------------------------------------


def test_quote_in_prozent_rundet_auf_ganze_zahlen() -> None:
    """27 von 31 sind 87,09 % - die Zeile traegt 87, nicht 87,1."""
    assert urteilslauf.quote_prozent(27, 31) == 87
    assert urteilslauf.quote_prozent(1, 2) == 50
    assert urteilslauf.quote_prozent(31, 31) == 100
    assert urteilslauf.quote_prozent(0, 31) == 0


def test_quote_ohne_kandidaten_ist_null_statt_ein_absturz() -> None:
    """Ein leerer Kandidatensatz ist kein Fehlerpfad dieses Werkzeugs."""
    assert urteilslauf.quote_prozent(0, 0) == 0


def test_nachschlagbefehl_nennt_aufnahme_stufe_und_modell() -> None:
    befehl = urteilslauf.nachschlagbefehl("2026-08-21 10-46-08")

    assert befehl == (
        'python -m matrix_auto_cutter.shorts.kette --aufnahme "2026-08-21 10-46-08" '
        "--neu-ab zerlegung --lauf 2 --modell opus"
    )


def test_nachschlagzeile_steht_unter_der_quote(
    tmp_path: Path, trefferquote_umgebogen: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Erklaerung, Befehl und Warnung - alle drei, in dieser Reihenfolge."""
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir)

    code = urteilslauf.main(
        [
            str(job_path),
            "--kein-server",
            "--kein-bau",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )
    zeilen = capsys.readouterr().out.splitlines()

    assert code == 0
    quote = next(i for i, zeile in enumerate(zeilen) if " beurteilt - " in zeile)
    assert zeilen[quote].endswith(" - Quote 50 %")
    assert zeilen[quote + 1] == (
        "  Nachschlag mit einem anderen Modell, falls die Ausbeute zu mager war:"
    )
    assert zeilen[quote + 2] == f"  {urteilslauf.nachschlagbefehl('2026-08-21 10-46-08')}"
    # Seit dem 26.8. steht dort kein Warnhinweis mehr, sondern der Anschluss:
    # die Zusammenfuehrung ist gebaut, und sie erhaelt die Nummerierung.
    assert "neu zusammengefuehrt" in zeilen[quote + 3]
    assert "Urteile bleiben gueltig" in zeilen[quote + 3]


def test_nachschlagzeile_nimmt_den_video_name_nicht_den_ordnernamen(
    tmp_path: Path, trefferquote_umgebogen: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ordner und Aufnahme duerfen auseinanderfallen - ``kette`` sucht die Aufnahme.

    Ein Auftragsordner darf heissen, wie er will; ``kette --aufnahme``
    sucht nach dem Aufnahmenamen. Naehme die Zeile den Ordnernamen, faende
    der Nachschlag die Aufnahme nicht und begaenne mit Code 2.
    """
    job_dir = tmp_path / "ein-ganz-anderer-ordnername"
    job_path = _baue_aufnahme(job_dir)

    urteilslauf.main(
        [
            str(job_path),
            "--kein-server",
            "--kein-bau",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )
    ausgabe = capsys.readouterr().out

    assert '--aufnahme "2026-08-21 10-46-08"' in ausgabe
    assert "ein-ganz-anderer-ordnername" not in ausgabe.split("Schritt 5")[0].split("Schritt 4")[1]


def test_nachschlagzeile_erscheint_auch_bei_voller_ausbeute(
    tmp_path: Path, trefferquote_umgebogen: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keine Schwelle: ob 100 % genuegen, entscheidet der Nutzer, nicht das Werkzeug."""
    job_dir = tmp_path / "auftrag"
    job_path = _baue_aufnahme(job_dir, urteile=_beide_ja())

    urteilslauf.main(
        [
            str(job_path),
            "--kein-server",
            "--kein-bau",
            "--keine-sicherung",
            "--wurzel",
            str(tmp_path),
        ]
    )
    ausgabe = capsys.readouterr().out

    assert " - Quote 100 %" in ausgabe
    assert "Nachschlag mit einem anderen Modell" in ausgabe
