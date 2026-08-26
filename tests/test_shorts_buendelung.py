"""Tests fuer die Buendelung: Stufe 7 der Kette und ``auswahl.pruefe_buendel``.

Kein Test startet einen echten Prozess. Die Buendelung laeuft wie die
Zerlegung ueber :func:`kette.fuehre_prozess`, und genau diese eine Funktion
wird umgebogen - damit ist ausgeschlossen, dass ein Testlauf ``claude``
anfasst.

Der Kern der Pruefung ist nicht, ob eine Buendelung *schoen* ist - das kann
kein Test sagen -, sondern ob sie die Kandidaten ueberhaupt trifft: jeden
Index genau einmal, je Gruppe genau eine Empfehlung, und die Paare mit
``laengere_fassung_von`` nicht auseinandergerissen. Genau daran haengen die
Urteile des Nutzers.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auswahl, kette

AUFNAHME = "2026-08-21 10-46-08"


# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------


def kandidat(index: int, *, laengere_fassung_von: int | None = None) -> dict[str, object]:
    """Ein roher Kandidat, wie er in ``kandidaten.json`` steht."""
    eintrag: dict[str, object] = {
        "index": index,
        "start_ms": index * 10_000,
        "end_ms": index * 10_000 + 9_000,
        "titel": f"Titel {index}",
        "begruendung": "Begruendung",
        "sicherheit": "hoch",
        "enthaelt": [],
    }
    if laengere_fassung_von is not None:
        eintrag["laengere_fassung_von"] = laengere_fassung_von
    return eintrag


def buendeleintrag(
    index: int, gruppe: int, rang: int, *, empfohlen: bool | None = None
) -> dict[str, object]:
    """Ein roher Buendeleintrag; ohne Angabe empfiehlt Rang 1."""
    return {
        "index": index,
        "projekt": "Bitcoin",
        "thema": f"Thema {index}",
        "gruppe": gruppe,
        "rang": rang,
        "empfohlen": rang == 1 if empfohlen is None else empfohlen,
        "begruendung": "Begruendung",
    }


def _job_dir(tmp_path: Path) -> Path:
    return tmp_path / kette.JOBS_ROOT / AUFNAHME


def _lege_vorstufen_an(job_dir: Path) -> None:
    """Die Ausgaben der Stufen 1 bis 6 - Inhalt egal, ausser bei zweien."""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / kette.JOB_FILE_NAME).write_text(
        json.dumps({"rendered_video": {"duration_ms": 120_000}}), encoding="utf-8"
    )
    (job_dir / "avatar-cut.mp4").write_text("", encoding="utf-8")
    (job_dir / "transkript-rendered.json").write_text("{}", encoding="utf-8")
    (job_dir / "wortliste.json").write_text("{}", encoding="utf-8")
    satz = {
        "kandidaten": [kandidat(1), kandidat(2)],
        "achse": "gerendert",
        "video_name": AUFNAHME,
        "lauf": 1,
        "modell": "opus",
    }
    (job_dir / "kandidaten-lauf1.json").write_text(json.dumps(satz), encoding="utf-8")
    (job_dir / "kandidaten.json").write_text(json.dumps(satz), encoding="utf-8")


def _buendeldatei(job_dir: Path, eintraege: list[dict[str, object]]) -> Path:
    ziel = job_dir / auswahl.BUENDEL_FILE_NAME
    ziel.write_text(
        json.dumps(
            {
                "artifact_type": auswahl.BUENDEL_ARTIFACT_TYPE,
                "schema_version": auswahl.BUENDEL_SCHEMA_VERSION,
                "video_name": AUFNAHME,
                "kandidaten_gesamt": len(eintraege),
                "gruppen_gesamt": len({eintrag["gruppe"] for eintrag in eintraege}),
                "modell": "opus",
                "gebuendelt_am": "2026-08-26T12:00:00+00:00",
                "buendel": eintraege,
            }
        ),
        encoding="utf-8",
    )
    return ziel


@pytest.fixture
def kein_bestand(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verbiete jeden Griff auf den Bestand - er laege auf ``F:`` und riefe ``ffprobe``."""

    def _verboten(**_kwargs: object) -> list[object]:
        raise AssertionError("sammle_aufnahmen darf hier nicht gerufen werden")

    monkeypatch.setattr(kette, "sammle_aufnahmen", _verboten)


# --------------------------------------------------------------------------
# pruefe_buendel: Indizes
# --------------------------------------------------------------------------


def test_vollstaendige_buendelung_meldet_nichts() -> None:
    """Der Normalfall: jeder Index einmal, je Gruppe eine Empfehlung."""
    kandidaten = [kandidat(1), kandidat(2), kandidat(3)]
    buendel = [
        buendeleintrag(1, gruppe=1, rang=1),
        buendeleintrag(2, gruppe=1, rang=2),
        buendeleintrag(3, gruppe=2, rang=1),
    ]

    assert auswahl.pruefe_buendel(kandidaten, buendel) == []


def test_fehlender_index_wird_gemeldet() -> None:
    """Ein Kandidat ohne Eintrag ist einer, ueber den nie entschieden wird."""
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [buendeleintrag(1, gruppe=1, rang=1)]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == ["Kandidat 2: fehlt in buendel.json"]


def test_ueberzaehliger_index_wird_gemeldet() -> None:
    """Ein Eintrag auf einen Index, den es nicht gibt, meint irgendetwas."""
    kandidaten = [kandidat(1)]
    buendel = [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(7, gruppe=2, rang=1)]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == ["Buendeleintrag 7: kein Kandidat mit diesem Index vorhanden"]


def test_fehlender_und_ueberzaehliger_index_werden_beide_gemeldet() -> None:
    """Beide Befunde nebeneinander - die Pruefung haelt nicht beim ersten an."""
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(9, gruppe=2, rang=1)]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == [
        "Kandidat 2: fehlt in buendel.json",
        "Buendeleintrag 9: kein Kandidat mit diesem Index vorhanden",
    ]


def test_doppelt_vergebener_index_wird_gemeldet() -> None:
    """Genau EIN Eintrag je Kandidat - zwei waeren zwei Entscheidungen."""
    kandidaten = [kandidat(1)]
    buendel = [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(1, gruppe=2, rang=1)]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert "Buendeleintrag 1: Index doppelt vergeben" in meldungen


# --------------------------------------------------------------------------
# pruefe_buendel: Empfehlung und Rang
# --------------------------------------------------------------------------


def test_gruppe_ohne_empfehlung_wird_gemeldet() -> None:
    """Eine Gruppe ohne Empfehlung legt dem Nutzer nichts hin."""
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [
        buendeleintrag(1, gruppe=1, rang=1, empfohlen=False),
        buendeleintrag(2, gruppe=1, rang=2, empfohlen=False),
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == ["Gruppe 1: 0 Empfehlungen statt genau einer - keiner"]


def test_gruppe_mit_zwei_empfehlungen_wird_gemeldet() -> None:
    """Zwei Empfehlungen sind wieder zwei Entscheidungen - genau das soll weg."""
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [
        buendeleintrag(1, gruppe=1, rang=1, empfohlen=True),
        buendeleintrag(2, gruppe=1, rang=2, empfohlen=True),
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == ["Gruppe 1: 2 Empfehlungen statt genau einer - 1, 2"]


def test_doppelter_rang_wird_gemeldet() -> None:
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [
        buendeleintrag(1, gruppe=1, rang=1),
        buendeleintrag(2, gruppe=1, rang=1, empfohlen=False),
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert "Gruppe 1: Rang 1 doppelt vergeben" in meldungen
    assert "Gruppe 1: Rang 2 fehlt - erwartet 1 bis 2" in meldungen


def test_fehlender_rang_wird_gemeldet() -> None:
    """Raenge 1 und 3 bei zwei Eintraegen: 2 fehlt, und das faellt sonst niemandem auf."""
    kandidaten = [kandidat(1), kandidat(2)]
    buendel = [
        buendeleintrag(1, gruppe=1, rang=1),
        buendeleintrag(2, gruppe=1, rang=3, empfohlen=False),
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == ["Gruppe 1: Rang 2 fehlt - erwartet 1 bis 2"]


def test_rang_der_keine_ganzzahl_ist_wird_gemeldet() -> None:
    kandidaten = [kandidat(1)]
    eintrag = buendeleintrag(1, gruppe=1, rang=1)
    eintrag["rang"] = "erster"
    meldungen = auswahl.pruefe_buendel(kandidaten, [eintrag])

    assert "Gruppe 1: kein ganzzahliger 'rang' bei 1" in meldungen


def test_gruppe_die_keine_ganzzahl_ist_wird_gemeldet() -> None:
    kandidaten = [kandidat(1)]
    eintrag = buendeleintrag(1, gruppe=1, rang=1)
    eintrag["gruppe"] = "Bitcoin"
    meldungen = auswahl.pruefe_buendel(kandidaten, [eintrag])

    assert meldungen == ["Buendeleintrag 1: 'gruppe' fehlt oder ist keine Ganzzahl"]


# --------------------------------------------------------------------------
# pruefe_buendel: laengere_fassung_von
# --------------------------------------------------------------------------


def test_paar_mit_laengerer_fassung_in_getrennten_gruppen_wird_gemeldet() -> None:
    """Der wichtigste Befund: dasselbe Material in zwei Gruppen.

    Genau daran ist der Nutzer am 26.8. dreimal haengengeblieben - er hat
    drei Paare angenommen, die dieselbe Stelle zeigen.
    """
    kandidaten = [kandidat(10), kandidat(64, laengere_fassung_von=10)]
    buendel = [
        buendeleintrag(10, gruppe=1, rang=1),
        buendeleintrag(64, gruppe=2, rang=1),
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten, buendel)

    assert meldungen == [
        "Kandidat 64 ist die laengere Fassung von 10, steht aber in Gruppe 2 statt in Gruppe 1"
    ]


def test_paar_mit_laengerer_fassung_in_derselben_gruppe_meldet_nichts() -> None:
    kandidaten = [kandidat(10), kandidat(64, laengere_fassung_von=10)]
    buendel = [
        buendeleintrag(10, gruppe=1, rang=2, empfohlen=False),
        buendeleintrag(64, gruppe=1, rang=1),
    ]

    assert auswahl.pruefe_buendel(kandidaten, buendel) == []


# --------------------------------------------------------------------------
# lies_buendel
# --------------------------------------------------------------------------


def test_lies_buendel_gibt_die_liste_zurueck(tmp_path: Path) -> None:
    pfad = _buendeldatei(tmp_path, [buendeleintrag(1, gruppe=1, rang=1)])

    assert [eintrag["index"] for eintrag in auswahl.lies_buendel(pfad)] == [1]


def test_lies_buendel_ohne_liste_ist_leer(tmp_path: Path) -> None:
    """Keine Liste heisst nichts - und dann meldet die Pruefung jeden Index als fehlend."""
    pfad = tmp_path / auswahl.BUENDEL_FILE_NAME
    pfad.write_text(json.dumps({"buendel": "nichts"}), encoding="utf-8")

    assert auswahl.lies_buendel(pfad) == []


# --------------------------------------------------------------------------
# Die Stufe in der Kette
# --------------------------------------------------------------------------


def test_buendelung_ist_die_siebte_stufe() -> None:
    assert [stufe.name for stufe in kette.STUFEN][-1] == "buendelung"
    assert kette.STUFEN[-1].ausgabe == "buendel.json"
    assert len(kette.STUFEN) == 7


def test_buendelung_verweist_auf_den_auftragstext_statt_ihn_zu_wiederholen() -> None:
    """Derselbe Grund wie bei der Zerlegung: zwei Fassungen veralteten nebeneinander."""
    argv = kette.buendelung_argv(AUFNAHME)

    assert argv[0] == kette.CLAUDE_BEFEHL
    assert argv[1] == "-p"
    assert "BUENDELUNG-AUFTRAGSTEXT.md" in argv[2]
    assert f'<AUFNAHME> ist "{AUFNAHME}"' in argv[2]
    assert argv[3:] == ["--model", "opus", "--permission-mode", "acceptEdits"]


def test_zerlegung_und_buendelung_nehmen_denselben_weg() -> None:
    """Ein Geruest, zwei Auftragstexte - ``modell_argv`` ist die eine Stelle."""
    zerlegung = kette.zerlegung_argv(AUFNAHME, modell="sonnet")
    buendelung = kette.buendelung_argv(AUFNAHME, modell="sonnet")

    assert zerlegung[:2] == buendelung[:2]
    assert zerlegung[3:] == buendelung[3:]
    assert zerlegung[2] != buendelung[2]


def test_vorgabe_der_zerlegung_ist_opus() -> None:
    """Entscheidung des Auftraggebers vom 26.8. - bis dahin war es ``sonnet``."""
    assert kette.ZERLEGUNG_MODELL == "opus"
    assert kette.BUENDELUNG_MODELL == "opus"


def test_stufe_wird_uebersprungen_wenn_buendel_json_vorliegt(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Wie jede andere Stufe: liegt die Ausgabe da, laeuft kein Modellaufruf."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)
    _buendeldatei(
        job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
    )
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert aufgezeichnet == []
    assert "buendel.json liegt bereits vor" in ausgabe


def test_neu_ab_buendelung_erzwingt_die_stufe(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--neu-ab buendelung`` laesst 1 bis 6 stehen und faehrt nur die siebte."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)
    _buendeldatei(job_dir, [buendeleintrag(1, gruppe=1, rang=1)])
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        _buendeldatei(
            job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
        )
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "buendelung"]
    )

    assert code == 0
    assert len(aufgezeichnet) == 1
    assert "BUENDELUNG-AUFTRAGSTEXT.md" in aufgezeichnet[0][2]


def test_modell_buendelung_landet_in_der_kommandozeile(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--modell-buendelung`` trifft die Buendelung und laesst ``--modell`` in Ruhe."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        _buendeldatei(
            job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
        )
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        [
            "--aufnahme",
            AUFNAHME,
            "--wurzel",
            str(tmp_path),
            "--neu-ab",
            "buendelung",
            "--modell-buendelung",
            "sonnet",
        ]
    )

    assert code == 0
    assert aufgezeichnet[0][aufgezeichnet[0].index("--model") + 1] == "sonnet"
    assert 'Trage als Wurzelfeld modell den Wert "sonnet" ein.' in aufgezeichnet[0][2]


def test_modell_buendelung_steht_in_kette_json(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spaeter soll nachvollziehbar sein, womit gebuendelt wurde - nicht nur, dass."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        _buendeldatei(
            job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
        )
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    kette.main(
        [
            "--aufnahme",
            AUFNAHME,
            "--wurzel",
            str(tmp_path),
            "--neu-ab",
            "buendelung",
            "--modell-buendelung",
            "sonnet",
        ]
    )
    zustand = kette.lies_zustand(job_dir / kette.ZUSTAND_FILE_NAME)

    assert zustand is not None
    stufen = zustand["stufen"]
    assert isinstance(stufen, dict)
    assert stufen["buendelung"]["modell"] == "sonnet"
    assert stufen["buendelung"]["status"] == kette.STATUS_FERTIG


def test_uebersprungene_buendelung_traegt_das_modell_von_heute_nicht_nach(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wer nichts gefahren hat, darf nichts behaupten - wie bei der Zerlegung."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)
    _buendeldatei(
        job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
    )

    def _verboten(argv: Sequence[str], *, etikett: str) -> int:
        raise AssertionError("hier darf kein Prozess starten")

    monkeypatch.setattr(kette, "fuehre_prozess", _verboten)

    kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--modell-buendelung", "sonnet"]
    )
    zustand = kette.lies_zustand(job_dir / kette.ZUSTAND_FILE_NAME)

    assert zustand is not None
    stufen = zustand["stufen"]
    assert isinstance(stufen, dict)
    assert "modell" not in stufen["buendelung"]


def test_trockenlauf_nennt_das_modell_der_buendelung(
    tmp_path: Path, kein_bestand: None, capsys: pytest.CaptureFixture[str]
) -> None:
    kette.main(
        [
            "--aufnahme",
            AUFNAHME,
            "--wurzel",
            str(tmp_path),
            "--trocken",
            "--modell-buendelung",
            "sonnet",
        ]
    )
    ausgabe = capsys.readouterr().out

    assert "Stufe 7 von 7: Buendelung (Modell), Modell sonnet" in ausgabe


# --------------------------------------------------------------------------
# Die Stufe scheitert an einer Abweichung
# --------------------------------------------------------------------------


def test_abweichende_buendelung_laesst_die_stufe_scheitern(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Eigener Code, jede Meldezeile genannt - und die Datei bleibt liegen."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        # Index 2 fehlt: die Buendelung hat einen Kandidaten uebergangen.
        _buendeldatei(job_dir, [buendeleintrag(1, gruppe=1, rang=1)])
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "buendelung"]
    )
    ausgabe = capsys.readouterr().out

    assert code == kette.CODE_BUENDEL_ABWEICHUNG
    assert "Kandidat 2: fehlt in buendel.json" in ausgabe
    assert (job_dir / auswahl.BUENDEL_FILE_NAME).is_file()


def test_gescheiterte_buendelung_steht_so_in_kette_json(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        _buendeldatei(job_dir, [buendeleintrag(1, gruppe=1, rang=1)])
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "buendelung"])
    zustand = kette.lies_zustand(job_dir / kette.ZUSTAND_FILE_NAME)

    assert zustand is not None
    stufen = zustand["stufen"]
    assert isinstance(stufen, dict)
    assert stufen["buendelung"]["status"] == kette.STATUS_GESCHEITERT


def test_unlesbare_buendeldatei_ist_derselbe_code(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Kein Absturz, sondern eine Meldung - der Modellaufruf hat schliesslich geliefert."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        (job_dir / auswahl.BUENDEL_FILE_NAME).write_text("kein JSON", encoding="utf-8")
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "buendelung"]
    )
    ausgabe = capsys.readouterr().out

    assert code == kette.CODE_BUENDEL_ABWEICHUNG
    assert "nicht lesbar" in ausgabe


def test_kandidaten_json_bleibt_unberuehrt(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Die Buendelung BESCHREIBT nur - an den Indizes haengen die Urteile des Nutzers."""
    job_dir = _job_dir(tmp_path)
    _lege_vorstufen_an(job_dir)
    vorher = (job_dir / "kandidaten.json").read_text(encoding="utf-8")

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        _buendeldatei(
            job_dir, [buendeleintrag(1, gruppe=1, rang=1), buendeleintrag(2, gruppe=2, rang=1)]
        )
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "buendelung"]
    )

    assert code == 0
    assert (job_dir / "kandidaten.json").read_text(encoding="utf-8") == vorher
