"""Tests fuer den Aufraeumer der Shorts-Bauordner.

Jeder Test arbeitet unter ``tmp_path``; ``RENDER_WURZEL`` wird umgebogen.
KEIN Test fasst ``F:`` an.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import aufraeumen

ALLE_VIER = ("ausschnitt.mp4", "leinwand.mp4", "mit-avatar.mp4", "short.mp4")


def _lege_kandidat(
    aufnahme: Path,
    name: str,
    *,
    dateien: tuple[str, ...] = ALLE_VIER,
    short_bytes: int = 400,
    extra: tuple[str, ...] = (),
) -> Path:
    ordner = aufnahme / name
    ordner.mkdir(parents=True, exist_ok=True)
    for datei in dateien:
        groesse = short_bytes if datei == "short.mp4" else 100
        (ordner / datei).write_bytes(b"x" * groesse)
    for datei in extra:
        (ordner / datei).write_bytes(b"fremd")
    return ordner


def _lege_aufnahme(wurzel: Path, name: str = "2026-08-20 10-00-00") -> Path:
    aufnahme = wurzel / name
    aufnahme.mkdir(parents=True, exist_ok=True)
    return aufnahme


def _altere(aufnahme: Path, stunden: float) -> None:
    """Setze jede ``short.mp4`` der Aufnahme um ``stunden`` in die Vergangenheit."""
    import os
    import time

    ziel = time.time() - stunden * 3600.0
    for short in aufnahme.glob("kandidat-*/short.mp4"):
        os.utime(short, (ziel, ziel))


@pytest.fixture
def wurzel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    pfad = tmp_path / "Shorts Rendered"
    pfad.mkdir()
    monkeypatch.setattr(aufraeumen, "RENDER_WURZEL", str(pfad))
    monkeypatch.setattr(
        aufraeumen, "PROTOKOLL_DIR", tmp_path / "artefakte" / "aufraeumen"
    )
    return pfad


def test_alle_vier_dateien_drei_loeschbar_short_bleibt(wurzel: Path) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(aufnahme, "kandidat-01")

    plan = aufraeumen.plane_aufraeumung(aufnahme)

    assert len(plan.dateien) == 3
    assert {p.name for p in plan.dateien} == set(aufraeumen.ZWISCHENSTUFEN)
    assert plan.bytes_frei == 300

    aufraeumen.fuehre_aus(plan)

    assert (ordner / "short.mp4").is_file()
    assert (ordner / "short.mp4").stat().st_size == 400
    for name in aufraeumen.ZWISCHENSTUFEN:
        assert not (ordner / name).exists()


def test_ordner_ohne_short_bleibt_unangetastet_und_code_vier(
    wurzel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ohne = _lege_kandidat(
        aufnahme, "kandidat-01", dateien=("ausschnitt.mp4", "leinwand.mp4")
    )
    mit = _lege_kandidat(aufnahme, "kandidat-02")
    _altere(aufnahme, 100)

    plan = aufraeumen.plane_aufraeumung(aufnahme)
    assert [e.ordner for e in plan.ohne_short] == [ohne]
    assert "keine short.mp4" in plan.uebersprungen[0].grund

    code = main_mit(["--aufnahme", aufnahme.name, "--wirklich-loeschen"])

    assert code == aufraeumen.CODE_ORDNER_OHNE_SHORT
    ausgabe = capsys.readouterr().out
    assert "ANGEHALTEN [ordner_ohne_short]" in ausgabe
    # Der unfertige Ordner bleibt vollstaendig ...
    assert (ohne / "ausschnitt.mp4").is_file()
    assert (ohne / "leinwand.mp4").is_file()
    # ... die uebrigen werden trotzdem aufgeraeumt.
    assert not (mit / "ausschnitt.mp4").exists()
    assert (mit / "short.mp4").is_file()


def test_leere_short_verhindert_das_aufraeumen(wurzel: Path) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(aufnahme, "kandidat-01", short_bytes=0)

    erlaubt, grund = aufraeumen.darf_aufraeumen(ordner)
    assert erlaubt is False
    assert "leer" in grund

    plan = aufraeumen.plane_aufraeumung(aufnahme)
    assert plan.dateien == ()

    aufraeumen.fuehre_aus(plan)
    for name in ALLE_VIER:
        assert (ordner / name).is_file()


def main_mit(argv: list[str]) -> int:
    return aufraeumen.main(argv)


def test_ohne_wirklich_loeschen_bleibt_jede_datei_liegen(
    wurzel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(aufnahme, "kandidat-01")
    _altere(aufnahme, 100)

    code = main_mit(["--aufnahme", aufnahme.name])

    assert code == aufraeumen.CODE_ERFOLG
    for name in ALLE_VIER:
        assert (ordner / name).is_file()
    ausgabe = capsys.readouterr().out
    assert "loeschbare Dateien: 3" in ausgabe
    assert "wird nichts geloescht" in ausgabe


def test_fremde_dateien_bleiben_unangetastet(wurzel: Path) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(
        aufnahme, "kandidat-01", extra=("notizen.txt", "short.mp4.partial")
    )

    plan = aufraeumen.plane_aufraeumung(aufnahme)
    assert {p.name for p in plan.dateien} == set(aufraeumen.ZWISCHENSTUFEN)

    aufraeumen.fuehre_aus(plan)

    assert (ordner / "notizen.txt").is_file()
    assert (ordner / "short.mp4.partial").is_file()


def test_mindestalter_ueberspringt_frische_aufnahme(
    wurzel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frisch = _lege_aufnahme(wurzel, "2026-08-28 09-00-00")
    _lege_kandidat(frisch, "kandidat-01")
    _altere(frisch, 2)

    code = main_mit(["--wirklich-loeschen"])

    assert code == aufraeumen.CODE_KEINE_AUFNAHME
    assert "ANGEHALTEN [keine_aufnahme]" in capsys.readouterr().out
    assert (frisch / "kandidat-01" / "ausschnitt.mp4").is_file()


def test_mindestalter_nimmt_alte_aufnahme(wurzel: Path) -> None:
    alt = _lege_aufnahme(wurzel, "2026-08-20 09-00-00")
    ordner = _lege_kandidat(alt, "kandidat-01")
    _altere(alt, 100)

    code = main_mit(["--wirklich-loeschen"])

    assert code == aufraeumen.CODE_ERFOLG
    assert not (ordner / "ausschnitt.mp4").exists()
    assert (ordner / "short.mp4").is_file()


def test_protokoll_traegt_jede_geloeschte_datei_mit_groesse(wurzel: Path) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    _lege_kandidat(aufnahme, "kandidat-01")
    _altere(aufnahme, 100)

    assert main_mit(["--aufnahme", aufnahme.name, "--wirklich-loeschen"]) == 0

    protokolle = sorted(aufraeumen.PROTOKOLL_DIR.glob("*.json"))
    assert len(protokolle) == 1
    inhalt = json.loads(protokolle[0].read_text(encoding="utf-8"))
    assert inhalt["anzahl_geloescht"] == 3
    assert inhalt["bytes_frei"] == 300
    assert {Path(e["pfad"]).name for e in inhalt["geloescht"]} == set(
        aufraeumen.ZWISCHENSTUFEN
    )
    assert all(e["bytes"] == 100 for e in inhalt["geloescht"])
    assert not list(aufraeumen.PROTOKOLL_DIR.glob("*.tmp"))


def test_zwischen_plan_und_ausfuehrung_entfernte_datei_bricht_nicht_ab(
    wurzel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(aufnahme, "kandidat-01")

    plan = aufraeumen.plane_aufraeumung(aufnahme)
    (ordner / "leinwand.mp4").unlink()

    ergebnis = aufraeumen.fuehre_aus(plan)

    assert len(ergebnis.geloescht) == 2
    assert ergebnis.bytes_frei == 200
    assert ergebnis.verschwunden == [str(ordner / "leinwand.mp4")]
    assert "verschwunden zwischen Plan und Ausfuehrung" in capsys.readouterr().out
    assert (ordner / "short.mp4").is_file()


def test_finde_bauordner_nennt_nur_verzeichnisse(wurzel: Path) -> None:
    _lege_aufnahme(wurzel, "2026-08-20 09-00-00")
    _lege_aufnahme(wurzel, "2026-08-21 09-00-00")
    (wurzel / "notiz.txt").write_text("x", encoding="utf-8")

    ordner = aufraeumen.finde_bauordner(wurzel)

    assert [p.name for p in ordner] == ["2026-08-20 09-00-00", "2026-08-21 09-00-00"]


def test_ohne_aufnahme_und_ohne_alle_nimmt_die_aelteste_reife(wurzel: Path) -> None:
    alt = _lege_aufnahme(wurzel, "2026-08-20 09-00-00")
    _lege_kandidat(alt, "kandidat-01")
    juenger = _lege_aufnahme(wurzel, "2026-08-22 09-00-00")
    _lege_kandidat(juenger, "kandidat-01")
    _altere(alt, 200)
    _altere(juenger, 100)

    assert main_mit(["--wirklich-loeschen"]) == 0

    assert not (alt / "kandidat-01" / "ausschnitt.mp4").exists()
    assert (juenger / "kandidat-01" / "ausschnitt.mp4").is_file()


def test_alle_nimmt_jede_reife_aufnahme(wurzel: Path) -> None:
    erste = _lege_aufnahme(wurzel, "2026-08-20 09-00-00")
    _lege_kandidat(erste, "kandidat-01")
    zweite = _lege_aufnahme(wurzel, "2026-08-22 09-00-00")
    _lege_kandidat(zweite, "kandidat-01")
    _altere(erste, 200)
    _altere(zweite, 100)

    assert main_mit(["--alle", "--wirklich-loeschen"]) == 0

    assert not (erste / "kandidat-01" / "ausschnitt.mp4").exists()
    assert not (zweite / "kandidat-01" / "ausschnitt.mp4").exists()


def test_fehlende_renderwurzel_meldet_code_zwei(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(aufraeumen, "RENDER_WURZEL", str(tmp_path / "gibt-es-nicht"))

    code = main_mit([])

    assert code == aufraeumen.CODE_WURZEL_FEHLT
    assert "ANGEHALTEN [wurzel_fehlt]" in capsys.readouterr().out


@pytest.mark.parametrize(
    "name", ["..", "../Rendered", r"..\Rendered", r"C:\Windows", "unter/ordner", ""]
)
def test_aufnahme_muss_ein_schlichter_name_sein(
    wurzel: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    aufnahme = _lege_aufnahme(wurzel)
    ordner = _lege_kandidat(aufnahme, "kandidat-01")

    code = main_mit(["--aufnahme", name, "--wirklich-loeschen"])

    assert code == aufraeumen.CODE_KEINE_AUFNAHME
    assert "ANGEHALTEN [keine_aufnahme]" in capsys.readouterr().out
    for datei in ALLE_VIER:
        assert (ordner / datei).is_file()
