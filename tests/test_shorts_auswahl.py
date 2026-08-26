"""Tests für Auftrag shorts-auswahl: aus Kandidaten plus Urteilen eine Bauliste machen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auswahl
from matrix_auto_cutter.shorts.candidates import load_candidates
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


# --------------------------------------------------------------------------
# Zusammenfuehrung mehrerer Zerlegungslaeufe
# --------------------------------------------------------------------------


def _laufdatei(
    job_dir: Path,
    lauf: int,
    kandidaten: list[dict[str, object]],
    *,
    modell: str = "sonnet",
) -> None:
    """Schreibe ``kandidaten-lauf<N>.json`` mit den Wurzelfeldern eines echten Laufs."""
    payload: dict[str, object] = {
        "kandidaten": kandidaten,
        "achse": "gerendert",
        "video_name": "2026-08-25 15-14-00",
        "kriterien_fassung": "Fassung 0.8 (24. August 2026)",
        "lauf": lauf,
        "modell": modell,
    }
    (job_dir / f"kandidaten-lauf{lauf}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_zwei_saetze_ohne_ueberschneidung_geben_alle_kandidaten(tmp_path: Path) -> None:
    """Kein Kandidat gleicht einem anderen - alle bleiben, und keiner wandert."""
    _laufdatei(
        tmp_path,
        1,
        [
            _kandidat(1, start_ms=0, end_ms=10_000, titel="A"),
            _kandidat(2, start_ms=20_000, end_ms=30_000, titel="B"),
        ],
    )
    _laufdatei(
        tmp_path,
        2,
        [
            _kandidat(1, start_ms=40_000, end_ms=50_000, titel="C"),
            _kandidat(2, start_ms=60_000, end_ms=70_000, titel="D"),
        ],
        modell="opus",
    )

    ergebnis = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))
    kandidaten = ergebnis["kandidaten"]
    assert isinstance(kandidaten, list)

    assert [k["index"] for k in kandidaten] == [1, 2, 3, 4]
    assert [k["titel"] for k in kandidaten] == ["A", "B", "C", "D"]
    # Die Indizes des ersten Satzes zeigen unveraendert auf ihre Zeitbereiche.
    assert (kandidaten[0]["start_ms"], kandidaten[0]["end_ms"]) == (0, 10_000)
    assert (kandidaten[1]["start_ms"], kandidaten[1]["end_ms"]) == (20_000, 30_000)
    assert [k["aus_lauf"] for k in kandidaten] == [1, 1, 2, 2]
    assert ergebnis["laeufe"] == [1, 2]
    assert ergebnis["modelle"] == {"1": "sonnet", "2": "opus"}
    assert ergebnis["lauf"] == 1
    assert isinstance(ergebnis["zusammengefuehrt_am"], str)


def test_sechzig_prozent_ueberlappung_gilt_als_derselbe_kandidat(tmp_path: Path) -> None:
    """Kuerzere Dauer 10 s, Ueberlappung 6 s - mehr als die Haelfte, also derselbe.

    Ersetzt am 26.8. den gleichnamigen Test, der die alte Regel prueft:
    dort ueberschrieb die laengere Fassung Index 1 (``start_ms`` 4000,
    Titel "Zweiter, laenger", ``aus_laeufen == [1, 2]``). Dieselbe
    Ueberlappungsrechnung, dieselbe Aussage "das ist derselbe Ausschnitt" -
    nur die Folge ist jetzt eine andere: anhaengen statt ersetzen.
    """
    _laufdatei(tmp_path, 1, [_kandidat(1, start_ms=0, end_ms=10_000, titel="Erster")])
    _laufdatei(
        tmp_path,
        2,
        [_kandidat(1, start_ms=4_000, end_ms=18_000, titel="Zweiter, laenger")],
    )

    ergebnis = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))
    kandidaten = ergebnis["kandidaten"]
    assert isinstance(kandidaten, list)

    # Zwei Eintraege: der kurze bleibt, der laengere kommt dazu.
    assert len(kandidaten) == 2
    assert kandidaten[0]["index"] == 1
    assert (kandidaten[0]["start_ms"], kandidaten[0]["end_ms"]) == (0, 10_000)
    assert kandidaten[0]["titel"] == "Erster"
    assert "laengere_fassung_von" not in kandidaten[0]
    assert kandidaten[1]["index"] == 2
    assert (kandidaten[1]["start_ms"], kandidaten[1]["end_ms"]) == (4_000, 18_000)
    assert kandidaten[1]["titel"] == "Zweiter, laenger"
    assert kandidaten[1]["laengere_fassung_von"] == 1
    assert kandidaten[1]["aus_lauf"] == 2
    # ``aus_laeufen`` gibt es nicht mehr - kein Eintrag stammt aus zwei Laeufen.
    assert all("aus_laeufen" not in eintrag for eintrag in kandidaten)


def test_kuerzere_fassung_aus_spaeterem_lauf_faellt_weg(tmp_path: Path) -> None:
    """Gleicher Ausschnitt, aber nicht laenger - dann kommt nichts dazu."""
    _laufdatei(tmp_path, 1, [_kandidat(1, start_ms=0, end_ms=20_000, titel="Lang")])
    _laufdatei(tmp_path, 2, [_kandidat(1, start_ms=2_000, end_ms=14_000, titel="Kurz")])

    kandidaten = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))["kandidaten"]
    assert isinstance(kandidaten, list)

    assert [k["index"] for k in kandidaten] == [1]
    assert kandidaten[0]["titel"] == "Lang"
    assert (kandidaten[0]["start_ms"], kandidaten[0]["end_ms"]) == (0, 20_000)


def test_laengere_fassungen_kommen_hinter_die_neuen_kandidaten(tmp_path: Path) -> None:
    """Erst die neuen Ausschnitte eines Laufs, dann seine laengeren Fassungen.

    Das ist die Reihenfolge, in der der Bestand ``2026-08-25 15-14-00``
    steht: 1-39 aus Lauf 1, 40-63 die neuen aus Lauf 2, danach die
    laengeren Fassungen. Waeren sie verschraenkt, verschoeben sich die
    Indizes der neuen Kandidaten je nachdem, an welcher Stelle im Lauf ein
    Doppelgaenger auftaucht.
    """
    _laufdatei(
        tmp_path,
        1,
        [
            _kandidat(1, start_ms=0, end_ms=10_000, titel="A"),
            _kandidat(2, start_ms=100_000, end_ms=110_000, titel="B"),
        ],
    )
    _laufdatei(
        tmp_path,
        2,
        [
            # Laengere Fassung von 1 - steht VORN im Lauf, landet trotzdem hinten.
            _kandidat(1, start_ms=0, end_ms=25_000, titel="A lang"),
            _kandidat(2, start_ms=300_000, end_ms=310_000, titel="Neu"),
            _kandidat(3, start_ms=100_000, end_ms=130_000, titel="B lang"),
        ],
    )

    kandidaten = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))["kandidaten"]
    assert isinstance(kandidaten, list)

    assert [k["index"] for k in kandidaten] == [1, 2, 3, 4, 5]
    assert [k["titel"] for k in kandidaten] == ["A", "B", "Neu", "A lang", "B lang"]
    assert [k.get("laengere_fassung_von") for k in kandidaten] == [None, None, None, 1, 2]


def test_urteile_bleiben_nach_zusammenfuehrung_gueltig(tmp_path: Path) -> None:
    """Die eigentliche Probe: kein Urteil des Grundsatzes weicht danach ab.

    Vor dem 26.8. meldete ``pruefe_uebereinstimmung`` hier drei
    Abweichungen (``start_ms``, ``end_ms``, ``titel`` von Kandidat 2), weil
    die laengere Fassung in Index 2 hineingeschrieben wurde.
    """
    _laufdatei(
        tmp_path,
        1,
        [
            _kandidat(1, start_ms=0, end_ms=10_000, titel="Erster"),
            _kandidat(2, start_ms=50_000, end_ms=60_000, titel="Zweiter"),
        ],
    )
    _laufdatei(
        tmp_path,
        2,
        [
            _kandidat(1, start_ms=48_000, end_ms=75_000, titel="Zweiter, laenger"),
            _kandidat(2, start_ms=200_000, end_ms=210_000, titel="Neu"),
        ],
    )
    urteile = {
        1: _urteil(1, "ja", start_ms=0, end_ms=10_000, titel="Erster"),
        2: _urteil(2, "nein", start_ms=50_000, end_ms=60_000, titel="Zweiter"),
    }

    auswahl.schreibe_kandidatensatz(
        tmp_path / "kandidaten.json",
        auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path)),
    )
    danach = load_candidates(tmp_path / "kandidaten.json")

    assert auswahl.pruefe_uebereinstimmung(danach, urteile) == []
    assert [k.index for k in danach] == [1, 2, 3, 4]
    assert [k.titel for k in danach] == ["Erster", "Zweiter", "Neu", "Zweiter, laenger"]


def test_veraenderte_indizes_meldet_nur_umgedeutete_indizes() -> None:
    """Neue Indizes sind unbedenklich, umgedeutete und fehlende nicht."""
    alt = [
        _kandidat(1, start_ms=0, end_ms=10_000, titel="A"),
        _kandidat(2, start_ms=50_000, end_ms=60_000, titel="B"),
        _kandidat(3, start_ms=90_000, end_ms=99_000, titel="C"),
    ]
    neu = [
        # 1 unveraendert, nur die Buchfuehrung kommt dazu.
        {**_kandidat(1, start_ms=0, end_ms=10_000, titel="A"), "aus_lauf": 1},
        # 2 umgedeutet.
        _kandidat(2, start_ms=48_000, end_ms=75_000, titel="B, laenger"),
        # 3 fehlt ganz.
        # 4 ist neu - kein Urteil kann darauf zeigen.
        {**_kandidat(4, start_ms=200_000, end_ms=210_000), "laengere_fassung_von": 2},
    ]

    assert auswahl.veraenderte_indizes(alt, neu) == [2, 3]
    assert auswahl.veraenderte_indizes(alt, alt) == []


def test_vierzig_prozent_ueberlappung_gilt_als_verschieden(tmp_path: Path) -> None:
    """Kuerzere Dauer 10 s, Ueberlappung 4 s - weniger als die Haelfte, also zwei."""
    _laufdatei(tmp_path, 1, [_kandidat(1, start_ms=0, end_ms=10_000, titel="Erster")])
    _laufdatei(tmp_path, 2, [_kandidat(1, start_ms=6_000, end_ms=20_000, titel="Zweiter")])

    ergebnis = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))
    kandidaten = ergebnis["kandidaten"]
    assert isinstance(kandidaten, list)

    assert [k["index"] for k in kandidaten] == [1, 2]
    assert [k["titel"] for k in kandidaten] == ["Erster", "Zweiter"]


def test_genau_die_haelfte_reicht_nicht() -> None:
    """Die Regel sagt MEHR als die Haelfte - 5 s von 10 s sind zu wenig."""
    a = _kandidat(1, start_ms=0, end_ms=10_000)
    b = _kandidat(1, start_ms=5_000, end_ms=15_000)

    assert auswahl.gleicher_kandidat(a, b) is False


def test_kein_ueberlappen_ist_nie_derselbe_kandidat() -> None:
    """Beruehrung an der Kante ist keine Ueberlappung."""
    a = _kandidat(1, start_ms=0, end_ms=10_000)
    b = _kandidat(1, start_ms=10_000, end_ms=20_000)

    assert auswahl.gleicher_kandidat(a, b) is False


def test_eine_einzige_laufdatei_ergibt_eine_kopie(tmp_path: Path) -> None:
    """Ein Lauf, kein Sonderfall: dieselben Kandidaten, dieselben Indizes."""
    eingabe = [
        _kandidat(1, start_ms=0, end_ms=10_000, titel="A"),
        _kandidat(2, start_ms=20_000, end_ms=30_000, titel="B"),
    ]
    _laufdatei(tmp_path, 1, eingabe)

    ergebnis = auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path))
    kandidaten = ergebnis["kandidaten"]
    assert isinstance(kandidaten, list)

    assert len(kandidaten) == len(eingabe)
    for gewesen, geworden in zip(eingabe, kandidaten, strict=True):
        for feld in ("index", "start_ms", "end_ms", "titel", "begruendung", "sicherheit"):
            assert geworden[feld] == gewesen[feld]
    assert ergebnis["laeufe"] == [1]


def test_laufdateien_werden_nach_zahl_sortiert_nicht_nach_namen(tmp_path: Path) -> None:
    """``lauf10`` steht alphabetisch vor ``lauf2`` - die Nummer entscheidet."""
    for lauf in (1, 2, 10):
        _laufdatei(tmp_path, lauf, [_kandidat(1, start_ms=lauf * 100_000)])

    assert [nummer for nummer, _ in auswahl.lade_laufdateien(tmp_path)] == [1, 2, 10]


def test_zusammenfuehren_schreibt_kandidaten_json(tmp_path: Path) -> None:
    """Die Befehlszeile ``--zusammenfuehren`` erzeugt die Datei, aus der gebaut wird."""
    _laufdatei(tmp_path, 1, [_kandidat(1, start_ms=0, end_ms=10_000, titel="A")])
    _laufdatei(tmp_path, 2, [_kandidat(1, start_ms=40_000, end_ms=50_000, titel="C")])

    code = auswahl.main([str(tmp_path), "--zusammenfuehren"])

    assert code == 0
    ergebnis = json.loads((tmp_path / "kandidaten.json").read_text(encoding="utf-8"))
    assert [k["index"] for k in ergebnis["kandidaten"]] == [1, 2]


def test_zusammenfuehren_bei_vorhandenen_urteilen_ist_code_9(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Urteile zeigen auf die alte Nummerierung - hier wird gemeldet, nicht geschrieben."""
    _laufdatei(tmp_path, 1, [_kandidat(1, start_ms=0, end_ms=10_000, titel="A")])
    _laufdatei(tmp_path, 2, [_kandidat(1, start_ms=40_000, end_ms=50_000, titel="C")])
    _schreibe_kandidaten(tmp_path / "kandidaten.json", [_kandidat(1, titel="A")])
    vorher = (tmp_path / "kandidaten.json").read_text(encoding="utf-8")
    write_urteile(tmp_path / "urteile-2026-08-25-120000.json", {1: _urteil(1, "ja", titel="A")})

    code = auswahl.main([str(tmp_path), "--zusammenfuehren"])
    ausgabe = capsys.readouterr().out

    assert code == 9
    assert "ANGEHALTEN [urteile_vorhanden]" in ausgabe
    assert (tmp_path / "kandidaten.json").read_text(encoding="utf-8") == vorher


def test_zusammenfuehren_ohne_laufdatei_haelt_an(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = auswahl.main([str(tmp_path), "--zusammenfuehren"])

    assert code == 2
    assert "ANGEHALTEN [keine_laufdatei]" in capsys.readouterr().out


def test_urteil_zeigt_nach_der_zusammenfuehrung_auf_denselben_kandidaten(
    tmp_path: Path,
) -> None:
    """Der Kern der Nummerierungsregel: Index 5 meint danach noch dasselbe.

    Geprueft wird nicht die Zahl, sondern was ``pruefe_uebereinstimmung``
    dazu sagt - dieselbe Pruefung, an der ein echter Auswahllauf scheiterte,
    wenn die Zusammenfuehrung neu nummeriert haette.
    """
    erster = [
        _kandidat(
            index,
            start_ms=index * 20_000,
            end_ms=index * 20_000 + 10_000,
            titel=f"Kandidat {index}",
        )
        for index in range(1, 9)
    ]
    _laufdatei(tmp_path, 1, erster)
    # Lauf 2 bringt einen Doppelgaenger von Kandidat 3 und zwei neue Ausschnitte.
    _laufdatei(
        tmp_path,
        2,
        [
            _kandidat(1, start_ms=62_000, end_ms=69_000, titel="Doppelgaenger von 3"),
            _kandidat(2, start_ms=500_000, end_ms=510_000, titel="Neu A"),
            _kandidat(3, start_ms=600_000, end_ms=610_000, titel="Neu B"),
        ],
    )
    urteil_auf_fuenf = _urteil(5, "ja", start_ms=100_000, end_ms=110_000, titel="Kandidat 5")

    auswahl.schreibe_kandidatensatz(
        tmp_path / "kandidaten.json",
        auswahl.fuehre_zusammen(auswahl.lade_laufdateien(tmp_path)),
    )
    danach = load_candidates(tmp_path / "kandidaten.json")

    assert auswahl.pruefe_uebereinstimmung(danach, {5: urteil_auf_fuenf}) == []
    assert [k.index for k in danach] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert [k.titel for k in danach][-2:] == ["Neu A", "Neu B"]
