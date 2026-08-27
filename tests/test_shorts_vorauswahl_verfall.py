r"""Tests zum Auftrag vorauswahl-verfall-wurzelfelder.

Drei Dinge, die zusammengehoeren, und die hier auch zusammen geprueft
werden: die Vorauswahl der besten Gruppen (``gruppen_rang``,
``vorauswahl``), der Verfall einer Aufnahme nach 48 Stunden, und die
Wurzelfelder, die eine Zusammenfuehrung bisher verlor (``modell``,
``laeufe``).

Kein Test startet ein Modell, keiner schreibt in einen echten
Aufnahmeordner, keiner fasst ``labels/repeat/`` an. Der Verfall wird ueber
einen ausdruecklich uebergebenen Bezugszeitpunkt geprueft und nie ueber die
Uhr des Rechners - ein Test, der auf ``datetime.now()`` beruht, ginge
48 Stunden nach seiner Niederschrift von selbst kaputt.

Der wichtigste Test dieser Datei ist
:func:`test_verfall_schreibt_in_keine_urteilsdatei`: der Verfall wird durch
NICHT-ANBIETEN abgebildet und nicht durch Schreiben. Eine Urteilsdatei
traegt nur, was der Mensch entschieden hat.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auswahl, judge, urteilslauf

# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

# Die Gestalt des Bestandes vom 25.8.: 69 Kandidaten in 47 Gruppen. Dieselbe
# Zerlegung wie in ``test_shorts_urteilsseite_gruppen.py`` - nachgebaut wird
# die GESTALT, nicht der Inhalt der echten, unversionierten Datei.
_GRUPPENGROESSEN = [3] * 4 + [2] * 14 + [1] * 29
_KANDIDATEN_GESAMT = sum(_GRUPPENGROESSEN)
_GRUPPEN_GESAMT = len(_GRUPPENGROESSEN)

_JETZT = datetime(2026, 8, 27, 14, 0, 0)


def kandidaten_bestand(anzahl: int = _KANDIDATEN_GESAMT) -> list[dict[str, object]]:
    return [
        {
            "index": index,
            "start_ms": index * 20_000,
            "end_ms": index * 20_000 + 10_000,
            "titel": f"Titel {index}",
            "begruendung": f"Begruendung {index}",
            "sicherheit": "hoch",
            "enthaelt": [],
        }
        for index in range(1, anzahl + 1)
    ]


def buendel_bestand(
    *,
    mit_vorauswahl: bool = True,
    groessen: list[int] | None = None,
) -> list[dict[str, object]]:
    """Eine gueltige Buendelung; ``mit_vorauswahl=False`` laesst die neuen Felder weg.

    Der ``gruppen_rang`` laeuft hier absichtlich GEGEN die Gruppennummer
    (Gruppe 1 bekommt den letzten Rang): so faellt in jedem Test auf, wenn
    doch nach der Nummer statt nach der Staerke sortiert wird.
    """
    groessen = groessen if groessen is not None else _GRUPPENGROESSEN
    gesamt = len(groessen)
    eintraege: list[dict[str, object]] = []
    index = 1
    for nummer, groesse in enumerate(groessen, start=1):
        gruppen_rang = gesamt - nummer + 1
        for rang in range(1, groesse + 1):
            eintrag: dict[str, object] = {
                "index": index,
                "projekt": f"Projekt {nummer}",
                "thema": f"Thema der Gruppe {nummer}",
                "gruppe": nummer,
                "rang": rang,
                "empfohlen": rang == 1,
                "begruendung": f"Begruendung fuer Rang {rang} in Gruppe {nummer}",
            }
            if mit_vorauswahl:
                eintrag["gruppen_rang"] = gruppen_rang
                eintrag["vorauswahl"] = gruppen_rang <= min(
                    auswahl.VORAUSWAHL_GROESSE, gesamt
                )
            eintraege.append(eintrag)
            index += 1
    return eintraege


def _setze(
    buendel: list[dict[str, object]], gruppe: int, feld: str, wert: object
) -> list[dict[str, object]]:
    """Setze ``feld`` bei allen Eintraegen einer Gruppe - fuer den Fehlerfall."""
    return [
        {**eintrag, feld: wert} if eintrag["gruppe"] == gruppe else eintrag
        for eintrag in buendel
    ]


def _aufnahmeordner(basis: Path, name: str) -> Path:
    """Ein Aufnahmeordner mit ``kandidaten.json``, wie ``finde_aufnahme`` ihn sucht."""
    ordner = basis / urteilslauf.AUFNAHMEN_UNTERPFAD / name
    ordner.mkdir(parents=True)
    (ordner / "kandidaten.json").write_text(
        json.dumps({"kandidaten": kandidaten_bestand(2), "video_name": name}),
        encoding="utf-8",
    )
    return ordner


def _laufsatz(nummer: int, modell: str | None, anzahl: int = 2) -> dict[str, object]:
    """Ein Zerlegungslauf, wie ``lade_laufdateien`` ihn liefert."""
    satz: dict[str, object] = {
        "kandidaten": [
            {
                "index": index,
                "start_ms": (index + nummer * 100) * 20_000,
                "end_ms": (index + nummer * 100) * 20_000 + 10_000,
                "titel": f"Lauf {nummer} Titel {index}",
                "begruendung": "Begruendung",
                "sicherheit": "hoch",
                "enthaelt": [],
            }
            for index in range(1, anzahl + 1)
        ],
        "lauf": nummer,
        "video_name": "2026-08-25 15-14-00",
        "kriterien_fassung": "0.8",
    }
    if modell is not None:
        satz["modell"] = modell
    return satz


# --------------------------------------------------------------------------
# TEIL 3a: pruefe_buendel findet die kaputte Vorauswahl
# --------------------------------------------------------------------------


def test_gueltige_vorauswahl_meldet_nichts() -> None:
    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel_bestand())

    assert meldungen == []


def test_doppelter_gruppen_rang_wird_gemeldet() -> None:
    """Zwei Gruppen auf demselben Rang: dann ist die Reihenfolge nicht bestimmt."""
    buendel = _setze(buendel_bestand(), 2, "gruppen_rang", _GRUPPEN_GESAMT)

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("doppelt vergeben" in zeile and "gruppen_rang" in zeile for zeile in meldungen)


def test_lueckenhafter_gruppen_rang_wird_gemeldet() -> None:
    """Ein Rang jenseits der Gruppenzahl reisst ein Loch in die Folge."""
    buendel = _setze(buendel_bestand(), 3, "gruppen_rang", _GRUPPEN_GESAMT + 5)

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("fehlt - erwartet 1 bis" in zeile for zeile in meldungen)


def test_gruppenuneinheitlicher_gruppen_rang_wird_gemeldet() -> None:
    """Innerhalb einer Gruppe zwei verschiedene Werte - der Rang gilt der GRUPPE."""
    buendel = [dict(eintrag) for eintrag in buendel_bestand()]
    # Gruppe 1 ist eine Dreiergruppe; einer ihrer Kandidaten schert aus.
    ausreisser = next(eintrag for eintrag in buendel if eintrag["gruppe"] == 1)
    ausreisser["gruppen_rang"] = 99

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("uneinheitlich" in zeile for zeile in meldungen)


def test_vorauswahl_falscher_groesse_wird_gemeldet() -> None:
    """16 statt 15 vorausgewaehlte Gruppen - eine zu viel ist auch falsch."""
    buendel = _setze(buendel_bestand(), 32, "vorauswahl", True)

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("statt 15" in zeile for zeile in meldungen)


def test_schlechter_gerangte_gruppe_in_der_vorauswahl_wird_gemeldet() -> None:
    """Die Vorauswahl widerspricht sich, wenn sie Rang 30 nimmt und Rang 15 auslaesst."""
    buendel = buendel_bestand()
    # Nur die ZUGEHOERIGKEIT wird getauscht, nicht der Rang: die Zahl der
    # vorausgewaehlten Gruppen bleibt 15, die Auswahl aber widerspricht sich.
    buendel = _setze(buendel, 33, "vorauswahl", False)
    buendel = _setze(buendel, 18, "vorauswahl", True)

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("groesseren" in zeile and "gruppen_rang" in zeile for zeile in meldungen)


def test_buendel_ohne_die_neuen_felder_ist_keine_abweichung() -> None:
    """Eine aeltere buendel.json bleibt gueltig - sonst waeren alte Aufnahmen verloren."""
    meldungen = auswahl.pruefe_buendel(
        kandidaten_bestand(), buendel_bestand(mit_vorauswahl=False)
    )

    assert meldungen == []


def test_halbe_vorauswahl_wird_gemeldet() -> None:
    """Entweder alle Gruppen tragen die Felder oder keine - eine halbe ist schlimmer."""
    buendel = [
        {
            schluessel: wert
            for schluessel, wert in eintrag.items()
            if not (eintrag["gruppe"] == 5 and schluessel in ("gruppen_rang", "vorauswahl"))
        }
        for eintrag in buendel_bestand()
    ]

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(), buendel)

    assert any("entweder alle oder keine" in zeile for zeile in meldungen)


def test_weniger_als_fuenfzehn_gruppen_sind_alle_vorausgewaehlt() -> None:
    """Bei 9 Gruppen ist ``min(15, 9)`` die Vorgabe - nicht 15."""
    buendel = buendel_bestand(groessen=[1] * 9)

    meldungen = auswahl.pruefe_buendel(kandidaten_bestand(9), buendel)

    assert meldungen == []
    assert all(eintrag["vorauswahl"] is True for eintrag in buendel)


# --------------------------------------------------------------------------
# TEIL 3b/c: die Urteilsseite
# --------------------------------------------------------------------------


def _seite(buendel: list[dict[str, object]], anzahl: int = _KANDIDATEN_GESAMT) -> str:
    from tests.test_shorts_urteilsseite_gruppen import _entry

    return judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand(anzahl)],
        kriterien_text=None,
        gruppen=judge.baue_gruppen(buendel),
    )


def test_baue_gruppen_sortiert_nach_gruppen_rang() -> None:
    """Nicht die Gruppennummer entscheidet die Reihenfolge, sondern die Staerke."""
    gruppen = judge.baue_gruppen(buendel_bestand())

    assert [gruppe.gruppen_rang for gruppe in gruppen] == list(range(1, _GRUPPEN_GESAMT + 1))
    # Die Testdaten laufen gegen die Nummer: die staerkste Gruppe ist die letzte.
    assert gruppen[0].nummer == _GRUPPEN_GESAMT
    assert gruppen[-1].nummer == 1


def test_baue_gruppen_ohne_die_neuen_felder_bleibt_bei_der_nummernfolge() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand(mit_vorauswahl=False))

    assert [gruppe.nummer for gruppe in gruppen] == list(range(1, _GRUPPEN_GESAMT + 1))
    assert all(gruppe.gruppen_rang is None for gruppe in gruppen)
    # Ohne Vorauswahl gilt jede Gruppe als vorausgewaehlt - die Seite verhaelt
    # sich dann genau wie vor diesem Auftrag.
    assert all(gruppe.vorauswahl for gruppe in gruppen)


def test_seite_zeigt_fuenfzehn_gruppen_und_klappt_den_rest_auf() -> None:
    """Der Pruefstein der Anzeige: 15 vorausgewaehlt, 32 hinter einer Zeile."""
    from tests.test_shorts_urteilsseite_gruppen import eingebettete_gruppen

    html_text = _seite(buendel_bestand())
    eingebettet = eingebettete_gruppen(html_text)

    assert len(eingebettet) == _GRUPPEN_GESAMT
    assert sum(1 for gruppe in eingebettet if gruppe["vorauswahl"]) == 15
    assert 'details.className = "uebrige-gruppen"' in html_text
    assert '" weitere Gruppen anzeigen"' in html_text
    assert "function vorauswahlGruppen()" in html_text


def test_zaehler_nennt_die_vorauswahl_und_die_gesamtzahl() -> None:
    html_text = _seite(buendel_bestand())

    assert '" Gruppen entschieden, "' in html_text
    assert '" (Vorauswahl; "' in html_text
    assert '" Gruppen insgesamt)"' in html_text


def test_seite_ohne_die_neuen_felder_hat_keinen_aufklapper() -> None:
    """Alte Anzeige: alle Gruppen stehen offen da, keine Zeile davor."""
    from tests.test_shorts_urteilsseite_gruppen import eingebettete_gruppen

    eingebettet = eingebettete_gruppen(_seite(buendel_bestand(mit_vorauswahl=False)))

    assert all(gruppe["gruppen_rang"] is None for gruppe in eingebettet)
    assert all(gruppe["vorauswahl"] for gruppe in eingebettet)


def test_gefuehrte_sitzung_laeuft_ueber_die_vorauswahl() -> None:
    """Die Sitzung fuehrt ueber die Empfohlenen der vorausgewaehlten Gruppen."""
    html_text = _seite(buendel_bestand())

    assert "return vorauswahlGruppen()" in html_text
    assert ".filter((g) => !gruppeEntschieden(g))" in html_text


# --------------------------------------------------------------------------
# TEIL 4a/c: die Wurzelfelder modell und laeufe
# --------------------------------------------------------------------------


def test_fuehre_zusammen_schreibt_modell_als_sonnet_plus_opus() -> None:
    """Der Fehler, der zwei Sicherungen ``unbekannt`` heissen liess."""
    payload = auswahl.fuehre_zusammen(
        [(1, _laufsatz(1, "sonnet")), (2, _laufsatz(2, "opus"))]
    )

    assert payload["modell"] == "sonnet+opus"
    assert payload["modelle"] == {"1": "sonnet", "2": "opus"}
    assert payload["laeufe"] == [1, 2]
    assert payload["lauf"] == 1


def test_fuehre_zusammen_eines_einzigen_laufs_schreibt_dessen_modell() -> None:
    payload = auswahl.fuehre_zusammen([(1, _laufsatz(1, "sonnet"))])

    assert payload["modell"] == "sonnet"


def test_fehlendes_modell_steht_als_unbekannt_darin() -> None:
    payload = auswahl.fuehre_zusammen([(1, _laufsatz(1, None)), (2, _laufsatz(2, "opus"))])

    assert payload["modell"] == "unbekannt+opus"


def test_sicherungsnamen_bei_zwei_laeufen() -> None:
    urteile, kandidaten = urteilslauf.sicherungsnamen(
        {
            "video_name": "2026-08-25 15-14-00",
            "lauf": 1,
            "laeufe": [1, 2],
            "modell": "sonnet+opus",
        }
    )

    assert urteile == "urteile-2026-08-25 15-14-00-lauf1+2-sonnet+opus.json"
    assert kandidaten == "kandidaten-2026-08-25 15-14-00-lauf1+2-sonnet+opus.json"


def test_sicherungsnamen_bei_einem_lauf_bleiben_wie_bisher() -> None:
    urteile, _ = urteilslauf.sicherungsnamen(
        {"video_name": "2026-08-25 15-14-00", "lauf": 1, "modell": "sonnet"}
    )

    assert urteile == "urteile-2026-08-25 15-14-00-lauf1-sonnet.json"


# --------------------------------------------------------------------------
# TEIL 4b/d: die Trefferquote
# --------------------------------------------------------------------------


def _eintrag(laeufe: list[object], lauf: object = 1, notiz: str = "") -> dict[str, object]:
    return auswahl.trefferquote_eintrag(
        video_name="2026-08-25 15-14-00",
        lauf=lauf,
        laeufe=laeufe,
        notiz=notiz,
        modell="sonnet",
        kriterien_fassung="0.8",
        kandidaten=[],
        angenommen=[],
        abgelehnt=[],
        ohne_urteil=[],
    )


def _eintraege(pfad: Path) -> list[dict[str, object]]:
    inhalt = json.loads(pfad.read_text(encoding="utf-8"))
    eintraege = inhalt["eintraege"]
    assert isinstance(eintraege, list)
    return eintraege


def test_zwei_saetze_mit_verschiedenen_laeufen_ergeben_zwei_eintraege(tmp_path: Path) -> None:
    """Der Eintrag, der am 26.8. fehlte: derselbe Film, ein anderer Bestand."""
    pfad = tmp_path / "trefferquote.json"

    auswahl.schreibe_trefferquote(pfad, _eintrag([1]))
    auswahl.schreibe_trefferquote(pfad, _eintrag([1, 2]))

    eintraege = _eintraege(pfad)
    assert len(eintraege) == 2
    assert [eintrag["laeufe"] for eintrag in eintraege] == [[1], [1, 2]]


def test_derselbe_lauf_ergibt_keinen_zweiten_eintrag(tmp_path: Path) -> None:
    pfad = tmp_path / "trefferquote.json"

    auswahl.schreibe_trefferquote(pfad, _eintrag([1, 2]))
    auswahl.schreibe_trefferquote(pfad, _eintrag([1, 2]))

    assert len(_eintraege(pfad)) == 1


def test_alteintrag_ohne_laeufe_blockiert_weiterhin_seinen_eigenen_fall(
    tmp_path: Path,
) -> None:
    """Die zwei Alteintraege im Bestand tragen kein ``laeufe`` - sie gelten als ``[lauf]``."""
    pfad = tmp_path / "trefferquote.json"
    alt = _eintrag([1])
    del alt["laeufe"]
    pfad.write_text(json.dumps({"schema_version": "1.0", "eintraege": [alt]}), encoding="utf-8")

    assert auswahl._hat_bestehenden_eintrag(pfad, video_name="2026-08-25 15-14-00", laeufe=[1])
    assert not auswahl._hat_bestehenden_eintrag(
        pfad, video_name="2026-08-25 15-14-00", laeufe=[1, 2]
    )

    auswahl.schreibe_trefferquote(pfad, _eintrag([1]))
    assert len(_eintraege(pfad)) == 1
    # Der Alteintrag bleibt, wie er ist - nicht umgeschrieben, nicht ergaenzt.
    assert "laeufe" not in _eintraege(pfad)[0]


def test_eintrag_traegt_lauf_und_laeufe() -> None:
    eintrag = _eintrag([1, 2])

    assert eintrag["lauf"] == 1
    assert eintrag["laeufe"] == [1, 2]


def test_notiz_wird_durchgereicht_und_nicht_ausgewertet() -> None:
    eintrag = _eintrag([1], notiz="Probelauf, nicht repraesentativ")

    assert eintrag["notiz"] == "Probelauf, nicht repraesentativ"
    assert _eintrag([1])["notiz"] == ""


def test_notiz_steht_in_der_befehlszeile() -> None:
    import io
    from contextlib import redirect_stdout

    puffer = io.StringIO()
    with redirect_stdout(puffer), pytest.raises(SystemExit):
        auswahl.main(["--help"])

    assert "--notiz" in puffer.getvalue()


# --------------------------------------------------------------------------
# TEIL 5: der Verfall
# --------------------------------------------------------------------------


def test_aufnahme_aelter_als_achtundvierzig_stunden_wird_uebergangen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _aufnahmeordner(tmp_path, "2026-08-25 10-00-00")

    gefunden = urteilslauf.finde_aufnahme(tmp_path, jetzt=_JETZT)

    assert gefunden is None
    ausgabe = capsys.readouterr().out
    assert "uebergangen: 2026-08-25 10-00-00 (verfallen," in ausgabe
    assert "h alt)" in ausgabe


def test_frische_aufnahme_wird_angeboten(tmp_path: Path) -> None:
    ordner = _aufnahmeordner(tmp_path, "2026-08-27 10-00-00")

    assert urteilslauf.finde_aufnahme(tmp_path, jetzt=_JETZT) == ordner


def test_auch_verfallen_hebt_den_verfall_auf(tmp_path: Path) -> None:
    ordner = _aufnahmeordner(tmp_path, "2026-08-25 10-00-00")

    assert urteilslauf.finde_aufnahme(tmp_path, auch_verfallen=True, jetzt=_JETZT) == ordner


def test_die_frischeste_gewinnt_und_die_verfallene_faellt_heraus(tmp_path: Path) -> None:
    _aufnahmeordner(tmp_path, "2026-08-24 10-00-00")
    frisch = _aufnahmeordner(tmp_path, "2026-08-27 09-00-00")

    assert urteilslauf.finde_aufnahme(tmp_path, jetzt=_JETZT) == frisch


def test_genau_achtundvierzig_stunden_gilt_noch_nicht_als_verfallen() -> None:
    """Die Grenze ist ``aelter als``, nicht ``mindestens``."""
    genau = _JETZT - timedelta(hours=urteilslauf.VERFALL_STUNDEN)

    assert not urteilslauf.ist_verfallen(genau.strftime("%Y-%m-%d %H-%M-%S"), _JETZT)


def test_unlesbarer_name_gilt_als_nicht_verfallen(tmp_path: Path) -> None:
    """Lieber eine Aufnahme zu viel anbieten als eine wegen des Namens verschweigen."""
    ordner = _aufnahmeordner(tmp_path, "ohne-zeitstempel")

    assert urteilslauf.alter_stunden("ohne-zeitstempel", _JETZT) is None
    assert urteilslauf.finde_aufnahme(tmp_path, jetzt=_JETZT) == ordner


def test_urteilslauf_endet_mit_code_zwei_wenn_alles_verfallen_ist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _aufnahmeordner(tmp_path, "2020-01-01 10-00-00")

    code = urteilslauf.main(["--wurzel", str(tmp_path), "--kein-server"])

    assert code == 2
    ausgabe = capsys.readouterr().out
    assert "nur_verfallen" in ausgabe
    assert "--auch-verfallen" in ausgabe


def test_ausdruecklich_uebergebener_pfad_verfaellt_nicht(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wer den Ordner tippt, weiss, was er tut - nur eine Warnzeile."""
    ordner = _aufnahmeordner(tmp_path, "2020-01-01 10-00-00")

    urteilslauf.main(
        [
            str(ordner),
            "--wurzel",
            str(tmp_path),
            "--kein-server",
            "--keine-sicherung",
            "--keine-auswahl",
            "--kein-bau",
        ]
    )

    ausgabe = capsys.readouterr().out
    assert "WARNUNG" in ausgabe
    assert "ausdruecklich angegeben" in ausgabe
    # Angehalten wurde jedenfalls NICHT wegen des Verfalls.
    assert "nur_verfallen" not in ausgabe


def test_verfall_schreibt_in_keine_urteilsdatei(tmp_path: Path) -> None:
    """Der wichtigste Test: Verfall ist NICHT-ANBIETEN, nicht Schreiben.

    Eine Urteilsdatei traegt nur, was der Mensch entschieden hat. Ein Wert
    ``verfallen`` darin waere eine Behauptung ueber seinen Willen, die er nie
    geaeussert hat - und sie ueberlebte jede spaetere Nacharbeit.
    """
    ordner = _aufnahmeordner(tmp_path, "2020-01-01 10-00-00")
    urteilsdatei = ordner / "urteile-2020-01-01-100000.json"
    inhalt = json.dumps(
        {
            "artifact_type": "matrix_auto_cutter_shorts_urteile",
            "schema_version": "1.0",
            "kandidaten": {
                "1": {
                    "start_ms": 0,
                    "end_ms": 1000,
                    "titel": "Titel 1",
                    "urteil": "ja",
                    "notiz": "",
                    "ist_kind": False,
                }
            },
        }
    )
    urteilsdatei.write_text(inhalt, encoding="utf-8")
    vorher = urteilsdatei.stat().st_mtime_ns

    urteilslauf.finde_aufnahme(tmp_path, jetzt=_JETZT)
    urteilslauf.main(["--wurzel", str(tmp_path), "--kein-server"])

    assert urteilsdatei.read_text(encoding="utf-8") == inhalt
    assert urteilsdatei.stat().st_mtime_ns == vorher
    assert sorted(pfad.name for pfad in ordner.glob("urteile*.json")) == [urteilsdatei.name]


def test_kette_waehlt_eine_verfallene_aufnahme_nicht_von_selbst() -> None:
    """``kette.py`` bekommt dieselbe Pruefung an derselben Stelle.

    Geprueft wird die Quelle und nicht der Lauf: ``bestimme_aufnahme`` liest
    auf diesem Weg den Bestand auf ``F:``, und ein Test, der ihn antriebe,
    haenge an einem Laufwerk, das auf keinem anderen Rechner existiert.
    """
    from matrix_auto_cutter.shorts import kette

    quelle = Path(kette.__file__).read_text(encoding="utf-8")

    assert "ist_verfallen(row.name)" in quelle
    assert "nur_verfallen" in quelle
