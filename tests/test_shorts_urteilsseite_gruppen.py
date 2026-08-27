"""Tests fuer die gruppierte Urteilsseite (Auftrag urteilsseite-gruppiert).

Kein Test startet den echten Server auf einem echten Auftragsordner. Wo eine
Serverantwort noetig ist, laeuft ein ``build_server`` auf ``127.0.0.1:0``
gegen ein ``tmp_path``-Verzeichnis - dasselbe Muster wie in
``test_shorts_judge_server.py``, nur mit erfundenen Dateien.

Die Bequemlichkeit "Diese Fassung nehmen" ist Javascript und laeuft hier
nicht. Geprueft wird beides getrennt: dass die Seite sie richtig verdrahtet
(Zeichenketten im erzeugten HTML, wie schon bei der gefuehrten Sitzung), und
dass die Folge von Anfragen, die sie ausloest, im Urteilsstand genau ein
``ja`` und zwei ``nein`` hinterlaesst - und sonst nichts.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import auswahl, judge
from matrix_auto_cutter.shorts import candidates as cd
from matrix_auto_cutter.shorts import judge_server as srv
from matrix_auto_cutter.shorts.judge import JudgeEntry

# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

# Die Form des Bestandes vom 25.8.: 69 Kandidaten in 47 Gruppen, davon 29
# Einzelgruppen, 14 Zweier- und 4 Dreiergruppen. Die echte buendel.json
# liegt unter ``artefakte/`` und ist nicht versioniert - ein Test, der sie
# laese, scheiterte auf jedem anderen Rechner. Nachgebaut wird deshalb ihre
# GESTALT, nicht ihr Inhalt.
_GRUPPENGROESSEN = [3] * 4 + [2] * 14 + [1] * 29
_KANDIDATEN_GESAMT = sum(_GRUPPENGROESSEN)
_GRUPPEN_GESAMT = len(_GRUPPENGROESSEN)


def buendel_bestand() -> list[dict[str, object]]:
    """Eine gueltige Buendelung in der Gestalt des Bestandes vom 25.8."""
    eintraege: list[dict[str, object]] = []
    index = 1
    for nummer, groesse in enumerate(_GRUPPENGROESSEN, start=1):
        for rang in range(1, groesse + 1):
            eintraege.append(
                {
                    "index": index,
                    "projekt": f"Projekt {nummer % 5}",
                    "thema": f"Thema der Gruppe {nummer}",
                    "gruppe": nummer,
                    "rang": rang,
                    "empfohlen": rang == 1,
                    "begruendung": f"Begruendung fuer Rang {rang} in Gruppe {nummer}",
                }
            )
            index += 1
    return eintraege


def kandidaten_bestand() -> list[dict[str, object]]:
    """Die rohen Kandidaten zur :func:`buendel_bestand`."""
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
        for index in range(1, _KANDIDATEN_GESAMT + 1)
    ]


def _candidate(roh: dict[str, object]) -> cd.Candidate:
    return cd.Candidate(
        index=int(roh["index"]),  # type: ignore[arg-type]
        start_ms=int(roh["start_ms"]),  # type: ignore[arg-type]
        end_ms=int(roh["end_ms"]),  # type: ignore[arg-type]
        titel=str(roh["titel"]),
        begruendung=str(roh["begruendung"]),
        sicherheit="hoch",
        enthaelt=(),
    )


def _entry(roh: dict[str, object]) -> JudgeEntry:
    return JudgeEntry(
        index=int(roh["index"]),  # type: ignore[arg-type]
        titel=str(roh["titel"]),
        begruendung=str(roh["begruendung"]),
        sicherheit="hoch",
        start_ms=int(roh["start_ms"]),  # type: ignore[arg-type]
        end_ms=int(roh["end_ms"]),  # type: ignore[arg-type]
        is_child=False,
        transcript_text="Text",
        transcript_precise=True,
        cluster=(),
    )


def eingebettete_gruppen(html_text: str) -> list[dict[str, object]]:
    """Das ``GRUPPEN``-Literal aus der erzeugten Seite, zurueckgelesen."""
    treffer = re.search(r"^const GRUPPEN = (.*);$", html_text, re.MULTILINE)
    assert treffer is not None, "GRUPPEN steht nicht in der Seite"
    geladen = json.loads(treffer.group(1))
    assert isinstance(geladen, list)
    return geladen


def schreibe_ordner(
    job_dir: Path,
    *,
    kandidaten: list[dict[str, object]],
    buendel: list[dict[str, object]] | None,
) -> None:
    """Lege einen Auftragsordner an; ``buendel=None`` heisst: keine Buendelung."""
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "kandidaten.json").write_text(
        json.dumps({"kandidaten": kandidaten, "video_name": "T"}), encoding="utf-8"
    )
    if buendel is None:
        return
    (job_dir / auswahl.BUENDEL_FILE_NAME).write_text(
        json.dumps(
            {
                "artifact_type": auswahl.BUENDEL_ARTIFACT_TYPE,
                "schema_version": auswahl.BUENDEL_SCHEMA_VERSION,
                "video_name": "T",
                "kandidaten_gesamt": len(buendel),
                "gruppen_gesamt": len({e["gruppe"] for e in buendel}),
                "modell": "opus",
                "gebuendelt_am": "2026-08-27T12:00:00+00:00",
                "buendel": buendel,
            }
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# baue_gruppen
# --------------------------------------------------------------------------


def test_baue_gruppen_stellt_den_empfohlenen_nach_vorn() -> None:
    """Nicht Rang 1 im Rohdatensatz entscheidet die Reihenfolge, sondern ``empfohlen``."""
    buendel = [
        {"index": 7, "gruppe": 1, "rang": 2, "empfohlen": True, "projekt": "P", "thema": "T",
         "begruendung": "der beste"},
        {"index": 4, "gruppe": 1, "rang": 1, "empfohlen": False, "projekt": "P", "thema": "T",
         "begruendung": "steht zurueck"},
    ]

    gruppen = judge.baue_gruppen(buendel)

    assert len(gruppen) == 1
    assert gruppen[0].empfohlen == 7
    assert gruppen[0].indizes[0] == 7
    assert gruppen[0].weitere == (4,)
    assert gruppen[0].begruendung == "der beste"


def test_baue_gruppen_haelt_die_nummernfolge_ein() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())

    assert [gruppe.nummer for gruppe in gruppen] == list(range(1, _GRUPPEN_GESAMT + 1))


def test_baue_gruppen_uebernimmt_projekt_und_thema_vom_empfohlenen() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())

    assert gruppen[0].thema == "Thema der Gruppe 1"
    assert gruppen[0].projekt.startswith("Projekt ")


def test_einzelgruppe_hat_keine_weiteren_fassungen() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())
    einzeln = [gruppe for gruppe in gruppen if len(gruppe.indizes) == 1]

    assert len(einzeln) == 29
    assert all(gruppe.weitere == () for gruppe in einzeln)


# --------------------------------------------------------------------------
# Die Seite mit Buendelung
# --------------------------------------------------------------------------


def test_seite_mit_buendelung_traegt_47_gruppen_und_47_empfohlene() -> None:
    """Der Pruefstein: 47 Gruppen, je genau ein sichtbarer Empfohlener."""
    kandidaten = kandidaten_bestand()
    gruppen = judge.baue_gruppen(buendel_bestand())

    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten], kriterien_text=None, gruppen=gruppen
    )
    eingebettet = eingebettete_gruppen(html_text)

    assert len(eingebettet) == 47
    assert len({gruppe["empfohlen"] for gruppe in eingebettet}) == 47
    assert sum(len(gruppe["indizes"]) for gruppe in eingebettet) == 69


def test_seite_mit_buendelung_zeigt_die_begruendung_beim_empfohlenen() -> None:
    """Der Nutzer soll sehen, WARUM diese Fassung vorgeschlagen wird."""
    gruppen = judge.baue_gruppen(buendel_bestand())
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()], kriterien_text=None, gruppen=gruppen
    )

    assert "Warum diese Fassung vorgeschlagen wird" in html_text
    assert "Begruendung fuer Rang 1 in Gruppe 1" in html_text


def test_seite_mit_buendelung_klappt_die_uebrigen_fassungen_ein() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()], kriterien_text=None, gruppen=gruppen
    )

    assert 'details.className = "weitere"' in html_text
    assert '" weitere Fassungen"' in html_text
    assert '"1 weitere Fassung"' in html_text
    # Titel UND Dauer stehen schon im zugeklappten Zustand.
    assert 'zeile.textContent = "#" + entry.index + " " + entry.titel + " (" + dauerS' in html_text


def test_seite_mit_buendelung_zaehlt_gruppen_statt_kandidaten() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()], kriterien_text=None, gruppen=gruppen
    )

    assert '" Gruppen entschieden, "' in html_text
    assert '" offen"' in html_text
    assert "function gruppeEntschieden(gruppe)" in html_text


def test_seite_mit_buendelung_traegt_keinen_rueckfallhinweis() -> None:
    gruppen = judge.baue_gruppen(buendel_bestand())
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()], kriterien_text=None, gruppen=gruppen
    )

    assert 'id="rueckfall-hinweis"' not in html_text


def test_knopf_diese_fassung_nehmen_nur_bei_mehr_als_einem_kandidaten() -> None:
    """Eine Gruppe mit nur einem Kandidaten sieht aus wie heute."""
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        gruppen=judge.baue_gruppen(buendel_bestand()),
    )

    assert "if (gruppe && gruppe.indizes.length > 1) {" in html_text
    assert '"Diese Fassung nehmen"' in html_text


def test_knopf_setzt_ja_fuer_den_gewaehlten_und_nein_fuer_die_uebrigen() -> None:
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        gruppen=judge.baue_gruppen(buendel_bestand()),
    )

    assert 'state[pos].urteil = index === gewaehlt ? "ja" : "nein";' in html_text
    # Je Kandidat ein eigener Schreibvorgang - kein Gruppenurteil.
    assert "saveUrteil(pos);" in html_text


def test_nichts_wird_vorbelegt_solange_niemand_drueckt() -> None:
    """Ohne Klick bleibt jeder Kandidat offen - der Knopf ist ein Angebot."""
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        gruppen=judge.baue_gruppen(buendel_bestand()),
    )

    assert "const state = ENTRIES.map(() => ({ urteil: null, notiz: \"\" }));" in html_text
    assert "nimmFassung" in html_text
    # nimmFassung laeuft ausschliesslich aus einem Klickhorcher heraus.
    assert 'nehmen.addEventListener("click", () => nimmFassung(gruppe, entry.index));' in html_text


# --------------------------------------------------------------------------
# Rueckfall auf die flache Liste
# --------------------------------------------------------------------------


def test_ohne_buendelung_bleibt_die_seite_flach() -> None:
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()], kriterien_text=None
    )

    assert eingebettete_gruppen(html_text) == []
    assert 'id="rueckfall-hinweis"' not in html_text


def test_rueckfallgrund_steht_im_kopf() -> None:
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        rueckfall_grund="buendel.json liegt nicht im Auftragsordner.",
    )

    assert 'id="rueckfall-hinweis"' in html_text
    assert "buendel.json liegt nicht im Auftragsordner." in html_text
    assert eingebettete_gruppen(html_text) == []


def test_rueckfallgrund_wird_maskiert() -> None:
    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        rueckfall_grund="<script>alert(1)</script>",
    )

    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_lade_buendel_gruppen_ohne_datei_nennt_den_grund(tmp_path: Path) -> None:
    schreibe_ordner(tmp_path, kandidaten=kandidaten_bestand(), buendel=None)

    gruppen, grund = srv.lade_buendel_gruppen(tmp_path)

    assert gruppen == ()
    assert grund is not None
    assert "liegt nicht im Auftragsordner" in grund


def test_lade_buendel_gruppen_mit_gueltiger_datei(tmp_path: Path) -> None:
    schreibe_ordner(tmp_path, kandidaten=kandidaten_bestand(), buendel=buendel_bestand())

    gruppen, grund = srv.lade_buendel_gruppen(tmp_path)

    assert grund is None
    assert len(gruppen) == 47
    assert sum(len(gruppe.indizes) for gruppe in gruppen) == 69


def test_fehlerhafte_buendelung_faellt_flach_zurueck_und_nennt_den_grund(
    tmp_path: Path,
) -> None:
    """Ein fehlender Index versteckte genau den Kandidaten, ueber den nie entschieden wird."""
    buendel = [eintrag for eintrag in buendel_bestand() if eintrag["index"] != 5]
    schreibe_ordner(tmp_path, kandidaten=kandidaten_bestand(), buendel=buendel)

    gruppen, grund = srv.lade_buendel_gruppen(tmp_path)

    assert gruppen == ()
    assert grund is not None
    assert "Kandidat 5: fehlt in buendel.json" in grund
    assert "alle Kandidaten einzeln" in grund

    html_text = judge.build_judge_html(
        [_entry(roh) for roh in kandidaten_bestand()],
        kriterien_text=None,
        gruppen=gruppen,
        rueckfall_grund=grund,
    )
    assert eingebettete_gruppen(html_text) == []
    assert "Kandidat 5: fehlt in buendel.json" in html_text


def test_unlesbare_buendelung_faellt_flach_zurueck(tmp_path: Path) -> None:
    schreibe_ordner(tmp_path, kandidaten=kandidaten_bestand(), buendel=None)
    (tmp_path / auswahl.BUENDEL_FILE_NAME).write_text("kein JSON", encoding="utf-8")

    gruppen, grund = srv.lade_buendel_gruppen(tmp_path)

    assert gruppen == ()
    assert grund is not None
    assert "nicht lesbar" in grund


# --------------------------------------------------------------------------
# Was der Knopf im Urteilsstand hinterlaesst
# --------------------------------------------------------------------------


@pytest.fixture
def dreiergruppe(tmp_path: Path) -> Iterator[tuple[srv.ThreadingHTTPServer, Path, list[int]]]:
    """Ein Server ueber einer Dreiergruppe; ``tmp_path``, kein echter Auftragsordner."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\0" * 64)
    urteile_path = tmp_path / "urteile-2026-08-27-120000.json"
    kandidaten = kandidaten_bestand()[:3]
    entries = [_entry(roh) for roh in kandidaten]
    server = srv.build_server(
        html=b"<!doctype html><title>Test</title>",
        video_path=video_path,
        urteile_path=urteile_path,
        entries=entries,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, urteile_path, [1, 2, 3]
    finally:
        srv.shutdown_server(server)
        thread.join(timeout=2)


def _post(server: srv.ThreadingHTTPServer, index: int, urteil: str | None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request(
        "POST",
        "/urteile",
        body=json.dumps({"index": index, "urteil": urteil, "notiz": ""}),
        headers={"Content-Type": "application/json"},
    )
    status = conn.getresponse().status
    conn.close()
    return status


def _nimm_fassung(server: srv.ThreadingHTTPServer, indizes: list[int], gewaehlt: int) -> None:
    """Genau das, was der Knopf im Browser tut: je Kandidat eine eigene Anfrage."""
    for index in indizes:
        assert _post(server, index, "ja" if index == gewaehlt else "nein") == 204


def _urteile(urteile_path: Path) -> dict[int, str | None]:
    return {
        index: urteil.urteil
        for index, urteil in srv.load_urteile(urteile_path).items()
    }


def test_diese_fassung_nehmen_setzt_ein_ja_und_zwei_nein(
    dreiergruppe: tuple[srv.ThreadingHTTPServer, Path, list[int]],
) -> None:
    server, urteile_path, indizes = dreiergruppe

    _nimm_fassung(server, indizes, gewaehlt=2)

    assert _urteile(urteile_path) == {1: "nein", 2: "ja", 3: "nein"}


def test_diese_fassung_nehmen_fasst_nichts_ausserhalb_der_gruppe_an(
    tmp_path: Path,
) -> None:
    """Und sonst nichts: Kandidaten ausserhalb der Gruppe bleiben offen."""
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"\0" * 64)
    urteile_path = tmp_path / "urteile-2026-08-27-120000.json"
    kandidaten = kandidaten_bestand()[:5]
    server = srv.build_server(
        html=b"",
        video_path=video_path,
        urteile_path=urteile_path,
        entries=[_entry(roh) for roh in kandidaten],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _nimm_fassung(server, [1, 2, 3], gewaehlt=1)
    finally:
        srv.shutdown_server(server)
        thread.join(timeout=2)

    stand = _urteile(urteile_path)
    assert stand == {1: "ja", 2: "nein", 3: "nein"}
    assert 4 not in stand
    assert 5 not in stand


def test_einzelurteil_nach_dem_knopf_bleibt_stehen(
    dreiergruppe: tuple[srv.ThreadingHTTPServer, Path, list[int]],
) -> None:
    """Jedes so gesetzte Urteil bleibt einzeln aenderbar - und die Aenderung haelt."""
    server, urteile_path, indizes = dreiergruppe
    _nimm_fassung(server, indizes, gewaehlt=2)

    assert _post(server, 3, "ja") == 204

    assert _urteile(urteile_path) == {1: "nein", 2: "ja", 3: "ja"}


def test_urteilsdatei_traegt_weiterhin_genau_die_sieben_felder(
    dreiergruppe: tuple[srv.ThreadingHTTPServer, Path, list[int]],
) -> None:
    """Das Schema bleibt kandidatenbezogen - sonst braechen auswahl und die Pruefung."""
    server, urteile_path, indizes = dreiergruppe
    _nimm_fassung(server, indizes, gewaehlt=2)

    payload = json.loads(urteile_path.read_text(encoding="utf-8"))

    assert payload["artifact_type"] == "matrix_auto_cutter_shorts_urteile"
    assert set(payload["kandidaten"]) == {"1", "2", "3"}
    for schluessel, eintrag in payload["kandidaten"].items():
        assert set(eintrag) == {"titel", "start_ms", "end_ms", "ist_kind", "urteil", "notiz"}, (
            f"Kandidat {schluessel} traegt andere Felder"
        )
    # Der siebte Wert ist der Schluessel selbst - der Index.
    assert sorted(int(schluessel) for schluessel in payload["kandidaten"]) == [1, 2, 3]
    assert "gruppe" not in payload
    assert "buendel" not in payload


def test_pruefe_uebereinstimmung_meldet_nichts_gegen_die_so_entstandene_datei(
    dreiergruppe: tuple[srv.ThreadingHTTPServer, Path, list[int]],
) -> None:
    """Der eigentliche Nachweis: die Urteile zeigen auf genau die Kandidaten, die sie meinen."""
    server, urteile_path, indizes = dreiergruppe
    _nimm_fassung(server, indizes, gewaehlt=2)

    kandidaten = [_candidate(roh) for roh in kandidaten_bestand()[:3]]
    meldungen = auswahl.pruefe_uebereinstimmung(kandidaten, srv.load_urteile(urteile_path))

    assert meldungen == []


def test_buendelung_ist_anzeige_und_nicht_wahrheit(
    dreiergruppe: tuple[srv.ThreadingHTTPServer, Path, list[int]],
    tmp_path: Path,
) -> None:
    """Faellt die Buendelung weg, bleiben die Urteile vollstaendig und gueltig.

    Die Urteilsdatei nennt nirgends eine Gruppennummer. Eine zweite
    Buendelung darf die Gruppen anders schneiden - an den drei Urteilen
    aendert das nichts, weil sie am ``index`` haengen und an sonst nichts.
    """
    server, urteile_path, indizes = dreiergruppe
    _nimm_fassung(server, indizes, gewaehlt=2)

    # Eine Buendelung mit anderem Schnitt taucht auf und verschwindet wieder.
    buendel_pfad = tmp_path / auswahl.BUENDEL_FILE_NAME
    buendel_pfad.write_text(json.dumps({"buendel": []}), encoding="utf-8")
    buendel_pfad.unlink()

    assert _urteile(urteile_path) == {1: "nein", 2: "ja", 3: "nein"}
    payload = json.loads(urteile_path.read_text(encoding="utf-8"))
    roh = json.dumps(payload)
    assert "gruppe" not in roh
    assert "buendel" not in roh
