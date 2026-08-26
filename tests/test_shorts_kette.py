"""Tests für den Kettenlaeufer.

Kein Test startet einen echten Prozess. Alle Stufen ausser der
Zusammenfuehrung laufen über :func:`kette.fuehre_prozess`, und genau
diese eine Funktion wird umgebogen - damit ist ausgeschlossen, dass ein
Testlauf ``claude``, ``ffmpeg`` oder ``whisper`` anfasst.

``sammle_aufnahmen`` liest den Bestand auf ``F:`` und ruft ``ffprobe``.
Jeder Test, der ohne ``--aufnahme`` läuft, biegt es deshalb um - auch
dort, wo er beweisen will, dass es gar nicht erst gerufen wird.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import kette

# --------------------------------------------------------------------------
# Hilfen
# --------------------------------------------------------------------------

AUFNAHME = "2026-08-21 10-46-08"


def _job_dir(tmp_path: Path, name: str = AUFNAHME) -> Path:
    return tmp_path / kette.JOBS_ROOT / name


def kandidatensatz(lauf: int = 1, *, start_ms: int = 0) -> dict[str, object]:
    """Ein knapper, aber gueltiger Zerlegungslauf - Stufe 6 liest ihn wirklich."""
    return {
        "kandidaten": [
            {
                "index": 1,
                "start_ms": start_ms,
                "end_ms": start_ms + 10_000,
                "titel": f"Titel aus Lauf {lauf}",
                "begruendung": "Begruendung",
                "sicherheit": "hoch",
                "enthaelt": [],
            }
        ],
        "achse": "gerendert",
        "video_name": AUFNAHME,
        "lauf": lauf,
        "modell": "sonnet",
    }


def _lege_ausgaben_an(
    job_dir: Path, *stufennamen: str, dauer_ms: int = 584_900, lauf: int = 1
) -> None:
    """Lege die Ausgaben der genannten Stufen an - Inhalt egal, ausser bei zwei.

    Der Auftrag traegt seine Dauer, weil die Kopfzeile der Transkription sie
    liest. Die Zerlegung traegt einen gueltigen Kandidatensatz, seit Stufe 6
    nicht mehr kopiert, sondern zusammenfuehrt: eine leere ``{}`` waere
    keine Laufdatei mehr, sondern ein Lesefehler.
    """
    job_dir.mkdir(parents=True, exist_ok=True)
    for stufe in kette.stufen_fuer(lauf):
        if stufe.name not in stufennamen:
            continue
        ziel = job_dir / stufe.ausgabe
        if stufe.name == "auftrag":
            ziel.write_text(
                json.dumps({"rendered_video": {"duration_ms": dauer_ms}}), encoding="utf-8"
            )
        elif stufe.name == "zerlegung":
            ziel.write_text(json.dumps(kandidatensatz(lauf)), encoding="utf-8")
        else:
            ziel.write_text("{}", encoding="utf-8")


ALLE_STUFEN = tuple(stufe.name for stufe in kette.STUFEN)


@pytest.fixture
def kein_bestand(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verbiete jeden Griff auf den Bestand - er läge auf ``F:`` und riefe ``ffprobe``."""

    def _verboten(**_kwargs: object) -> list[object]:
        raise AssertionError("sammle_aufnahmen darf hier nicht gerufen werden")

    monkeypatch.setattr(kette, "sammle_aufnahmen", _verboten)


@pytest.fixture
def prozesse(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fange jeden Prozessstart ab; die Stufe gilt als geglückt und legt ihre Ausgabe an."""
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)
    return aufgezeichnet


def _prozess_legt_ausgabe_an(
    job_dir: Path, aufgezeichnet: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wie ``prozesse``, aber jede Stufe hinterlässt auch ihre Ausgabe."""

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        _lege_ausgaben_an(job_dir, etikett)
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)


def _lies_zustand(job_dir: Path) -> dict[str, object]:
    zustand = kette.lies_zustand(job_dir / kette.ZUSTAND_FILE_NAME)
    assert zustand is not None
    return zustand


def _status(zustand: dict[str, object], name: str) -> object:
    stufen = zustand["stufen"]
    assert isinstance(stufen, dict)
    return stufen[name]["status"]


# --------------------------------------------------------------------------
# Zustandsdatei
# --------------------------------------------------------------------------


def test_zustandsdatei_entsteht_nach_der_ersten_stufe(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nach Stufe 1 liegt ``kette.json`` da - nicht erst am Ende des Laufs."""
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--bis", "1"]
    )

    assert code == 0
    zustand = _lies_zustand(job_dir)
    assert zustand["artifact_type"] == kette.ARTIFACT_TYPE
    assert zustand["video_name"] == AUFNAHME
    assert _status(zustand, "auftrag") == kette.STATUS_FERTIG
    assert _status(zustand, "transcript") == kette.STATUS_OFFEN


def test_zustandsdatei_traegt_nach_jeder_stufe_den_stand(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Stand wird fortgeschrieben, nicht gesammelt: Stufe 3 sieht 1 und 2 fertig."""
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []
    gesehen: list[list[object]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        pfad = job_dir / kette.ZUSTAND_FILE_NAME
        if pfad.is_file():
            zustand = _lies_zustand(job_dir)
            gesehen.append([_status(zustand, name) for name in ALLE_STUFEN])
        _lege_ausgaben_an(job_dir, etikett)
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])

    assert code == 0
    # Beim Start von Stufe 3 sind 1 und 2 fertig, 3 laeuft, der Rest ist offen.
    assert gesehen[2] == [
        kette.STATUS_FERTIG,
        kette.STATUS_FERTIG,
        kette.STATUS_LAEUFT,
        kette.STATUS_OFFEN,
        kette.STATUS_OFFEN,
        kette.STATUS_OFFEN,
    ]
    zustand = _lies_zustand(job_dir)
    assert [_status(zustand, name) for name in ALLE_STUFEN] == [kette.STATUS_FERTIG] * 6
    assert (job_dir / "kandidaten.json").is_file()


# --------------------------------------------------------------------------
# Ueberspringen und Erzwingen
# --------------------------------------------------------------------------


def test_fertige_stufe_mit_ausgabe_wird_uebersprungen(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Alles liegt vor, nichts läuft - auch ohne vorhandene ``kette.json``."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, *ALLE_STUFEN)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert prozesse == []
    assert ausgabe.count("uebersprungen -") == 6


def test_neu_erzwingt_jede_stufe(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--neu`` läuft alles noch einmal, obwohl jede Ausgabe schon daliegt."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, *ALLE_STUFEN)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu"])

    assert code == 0
    # Fuenf Prozesse: die Zusammenfuehrung ist eine Kopie, kein Prozess.
    assert len(aufgezeichnet) == 5
    assert "--force" in aufgezeichnet[0]


def test_neu_ab_4_laesst_1_bis_3_stehen(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--neu-ab 4`` fasst die Transkription nicht an - sie ist die teuerste Stufe."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, *ALLE_STUFEN)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "4"])

    assert code == 0
    gestartet = [argv[-1] if argv[0] != kette.CLAUDE_BEFEHL else "claude" for argv in aufgezeichnet]
    # Nur Stufe 4 (wortliste) und Stufe 5 (zerlegung) liefen wieder.
    assert len(aufgezeichnet) == 2
    assert gestartet[1] == "claude"
    zustand = _lies_zustand(job_dir)
    assert _status(zustand, "transcript") == kette.STATUS_FERTIG


# --------------------------------------------------------------------------
# Fehlschlag
# --------------------------------------------------------------------------


def test_gescheiterte_stufe_bricht_die_kette_ab(
    tmp_path: Path,
    kein_bestand: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Code 5, Fremdcode in der Meldung, Folgestufen bleiben offen."""
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        if etikett == "transcript":
            return 3
        _lege_ausgaben_an(job_dir, etikett)
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 5
    assert "ANGEHALTEN [stufe_gescheitert]" in ausgabe
    assert "transcript" in ausgabe
    assert "Rueckgabecode 3" in ausgabe
    zustand = _lies_zustand(job_dir)
    assert _status(zustand, "avatar_cut") == kette.STATUS_FERTIG
    assert _status(zustand, "transcript") == kette.STATUS_GESCHEITERT
    assert _status(zustand, "wortliste") == kette.STATUS_OFFEN
    assert _status(zustand, "zerlegung") == kette.STATUS_OFFEN
    stufen = zustand["stufen"]
    assert isinstance(stufen, dict)
    assert "3" in str(stufen["transcript"]["meldung"])


def test_fehlendes_claude_ist_code_5_und_kein_absturz(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ist ``claude`` nicht auffindbar, meldet die Kette das - sie stürzt nicht ab."""

    def _kein_programm(argv: Sequence[str], **_kwargs: object) -> object:
        raise FileNotFoundError(2, "Das System kann die angegebene Datei nicht finden")

    monkeypatch.setattr(kette.subprocess, "Popen", _kein_programm)

    with pytest.raises(kette.KetteFehlschlag) as fehler:
        kette.fuehre_prozess(kette.zerlegung_argv(AUFNAHME), etikett="zerlegung")

    assert fehler.value.rueckgabecode == kette.CODE_STUFE_GESCHEITERT
    assert "claude" in fehler.value.text


# --------------------------------------------------------------------------
# Zusammenfuehrung
# --------------------------------------------------------------------------


def test_zwei_zerlegungslaeufe_werden_vereinigt_statt_angehalten(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Bis zum 25.8. war das Code 6 - der Platzhalter fuer die fehlende Regel.

    Er ist weg: zwei Laufdateien ergeben jetzt einen vereinigten
    Kandidatensatz, und die Nummerierung des ersten Laufs bleibt stehen.
    """
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste", "zerlegung")
    (job_dir / "kandidaten-lauf2.json").write_text(
        json.dumps(kandidatensatz(2, start_ms=60_000)), encoding="utf-8"
    )

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "ANGEHALTEN" not in ausgabe
    ergebnis = json.loads((job_dir / "kandidaten.json").read_text(encoding="utf-8"))
    assert [k["index"] for k in ergebnis["kandidaten"]] == [1, 2]
    assert ergebnis["laeufe"] == [1, 2]


def test_zusammenfuehrung_uebernimmt_den_einzigen_lauf(tmp_path: Path) -> None:
    """Bei einem Lauf aendert sich am Kandidatensatz nichts - nur Wurzelfelder kommen dazu.

    Frueher war das eine Dateikopie und die beiden Dateien waren bytegleich.
    Das ist vorbei: das Ergebnis traegt jetzt ``laeufe``, ``modelle`` und
    ``zusammengefuehrt_am``, damit einer spaeteren Trefferquote nicht die
    Herkunft fehlt. Die Kandidaten selbst sind unveraendert - darauf kommt
    es an.
    """
    job_dir = _job_dir(tmp_path)
    job_dir.mkdir(parents=True)
    satz = kandidatensatz(1)
    (job_dir / "kandidaten-lauf1.json").write_text(json.dumps(satz), encoding="utf-8")

    ziel = kette.fuehre_zusammen(job_dir)
    ergebnis = json.loads(ziel.read_text(encoding="utf-8"))

    assert ziel == job_dir / "kandidaten.json"
    gewesen = satz["kandidaten"][0]
    geworden = ergebnis["kandidaten"][0]
    assert all(geworden[feld] == gewesen[feld] for feld in gewesen)
    assert ergebnis["laeufe"] == [1]
    assert ergebnis["modelle"] == {"1": "sonnet"}


def _urteilsdatei(job_dir: Path) -> None:
    """Eine Urteilsdatei, wie ``judge_server`` sie hinterlaesst - Inhalt egal.

    Die Sperre fragt ueber ``auswahl.juengste_urteilsdatei`` nur, OB eine
    ``urteile*.json`` daliegt; welche Urteile darin stehen, entscheidet sie
    nicht - das entscheidet der Vergleich der Kandidatensaetze.
    """
    (job_dir / "urteile-2026-08-21-101010.json").write_text(
        json.dumps({"artifact_type": "shorts-urteile", "schema_version": 1, "kandidaten": []}),
        encoding="utf-8",
    )


def test_stufe_sechs_laeuft_durch_wenn_kein_index_umgedeutet_wird(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Urteile UND kandidaten.json liegen vor - aber Lauf 2 bringt nur Neues.

    Nach der Regel aus ``auswahl.fuehre_zusammen`` ist das der Normalfall:
    Index 1 behaelt seinen Inhalt, Lauf 2 haengt Index 2 an. Ein Urteil auf
    1 meint danach denselben Ausschnitt, also darf geschrieben werden.
    """
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste", "zerlegung")
    _urteilsdatei(job_dir)
    # Der Stand, auf den die Urteile zeigen: Lauf 1 allein.
    kette.fuehre_zusammen(job_dir)
    (job_dir / "kandidaten-lauf2.json").write_text(
        json.dumps(kandidatensatz(2, start_ms=60_000)), encoding="utf-8"
    )

    # ``--neu-ab``, weil ``kandidaten.json`` schon daliegt - sonst gaelte
    # Stufe 6 als erledigt und die Sperre kaeme gar nicht erst zum Zug.
    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "zusammenfuehrung"]
    )
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert "ANGEHALTEN" not in ausgabe
    ergebnis = json.loads((job_dir / "kandidaten.json").read_text(encoding="utf-8"))
    assert [k["index"] for k in ergebnis["kandidaten"]] == [1, 2]
    assert ergebnis["kandidaten"][0]["titel"] == "Titel aus Lauf 1"


def test_stufe_sechs_haelt_mit_code_neun_an_wenn_ein_index_umgedeutet_wird(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Wuerde ein beurteilter Index anderes bedeuten, wird nichts geschrieben.

    Hergestellt wird der Fall ueber eine ``kandidaten.json``, die nicht aus
    den heutigen Laufdateien stammt - genau das, was passiert, wenn nach
    einer Zusammenfuehrung eine Laufdatei ersetzt wird.
    """
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste", "zerlegung")
    _urteilsdatei(job_dir)
    vorher = kandidatensatz(1)
    vorher["kandidaten"][0]["titel"] = "So stand es, als geurteilt wurde"
    (job_dir / "kandidaten.json").write_text(json.dumps(vorher), encoding="utf-8")

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "zusammenfuehrung"]
    )
    ausgabe = capsys.readouterr().out

    assert code == kette.CODE_URTEILE_VORHANDEN == 9
    assert "ANGEHALTEN [urteile_vorhanden]" in ausgabe
    assert "Indizes 1" in ausgabe
    # Nichts geschrieben: der Stand, auf den die Urteile zeigen, steht noch da.
    danach = json.loads((job_dir / "kandidaten.json").read_text(encoding="utf-8"))
    assert danach["kandidaten"][0]["titel"] == "So stand es, als geurteilt wurde"


def test_stufe_sechs_schreibt_ohne_urteilsdatei_ohne_vergleich(tmp_path: Path) -> None:
    """Ohne Urteile gibt es nichts zu schuetzen - auch ein umgedeuteter Index geht durch."""
    job_dir = _job_dir(tmp_path)
    job_dir.mkdir(parents=True)
    (job_dir / "kandidaten-lauf1.json").write_text(
        json.dumps(kandidatensatz(1)), encoding="utf-8"
    )
    vorher = kandidatensatz(1)
    vorher["kandidaten"][0]["titel"] = "Etwas ganz anderes"
    (job_dir / "kandidaten.json").write_text(json.dumps(vorher), encoding="utf-8")

    ziel = kette.fuehre_zusammen(job_dir)
    ergebnis = json.loads(ziel.read_text(encoding="utf-8"))

    assert ergebnis["kandidaten"][0]["titel"] == "Titel aus Lauf 1"


# --------------------------------------------------------------------------
# Aufnahmename
# --------------------------------------------------------------------------


def test_name_wird_nicht_neu_erfragt_wenn_kette_json_ihn_traegt(
    tmp_path: Path, kein_bestand: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """Liegt eine ``kette.json`` da, bleibt der Bestand ungelesen.

    ``kein_bestand`` lässt ``sammle_aufnahmen`` scheitern - dieser Test
    besteht nur, wenn es gar nicht erst gerufen wird.
    """
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, *ALLE_STUFEN)
    kette.schreibe_zustand(job_dir / kette.ZUSTAND_FILE_NAME, kette.leerer_zustand(AUFNAHME))

    code = kette.main(["--wurzel", str(tmp_path), "--trocken"])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert f"Aufnahme:       {AUFNAHME}" in ausgabe
    assert "Bestand nicht erneut gelesen" in ausgabe


def test_ohne_bestand_und_ohne_kette_ist_es_code_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Findet der Bestand nichts, ist das Code 2 - nicht Code 5."""

    def _leer(**_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(kette, "sammle_aufnahmen", _leer)

    code = kette.main(["--wurzel", str(tmp_path)])

    assert code == 2
    assert "ANGEHALTEN [keine_aufnahme]" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Trockenlauf und Fortschritt
# --------------------------------------------------------------------------


def test_trocken_fuehrt_nichts_aus_und_schreibt_nichts(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ein Probelauf, der eine ``kette.json`` hinterliesse, wäre keiner."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut")

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--trocken"])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert prozesse == []
    assert not (job_dir / kette.ZUSTAND_FILE_NAME).exists()
    assert ausgabe.count("wird uebersprungen") == 2
    assert ausgabe.count("wuerde laufen") == 4
    assert "2 von 6 Stufen wuerden uebersprungen." in ausgabe


def test_erwartete_transkriptionsdauer_steht_in_der_kopfzeile(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """584,9 s Audio mal 1,27 sind rund 12 min 22 s - die Zahl steht vor Stufe 3."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", dauer_ms=584_900)

    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--trocken"])
    ausgabe = capsys.readouterr().out

    assert "Stufe 3 von 6: Transkription, erwartet rund 12 min 22 s" in ausgabe


def test_ohne_auftragsdatei_bleibt_die_kopfzeile_ohne_erwartung(tmp_path: Path) -> None:
    """Fehlt ``shorts-job.json``, wird nichts geraten - die Zeile nennt dann keine Dauer."""
    zeile = kette._kopfzeile(3, kette.STUFEN[2], tmp_path / "gibt-es-nicht.json")

    assert zeile == "Stufe 3 von 6: Transkription"


def test_dauer_text_schneidet_ab_statt_zu_runden() -> None:
    assert kette.dauer_text(742.8) == "12 min 22 s"
    assert kette.dauer_text(48.9) == "48 s"
    assert kette.dauer_text(60.0) == "1 min 0 s"


def test_zerlegung_verweist_auf_den_auftragstext_statt_ihn_zu_wiederholen() -> None:
    """Der Auftragstext steht einmal, in der Datei - nicht auch noch im Code."""
    argv = kette.zerlegung_argv(AUFNAHME)

    assert argv[0] == kette.CLAUDE_BEFEHL
    assert argv[1] == "-p"
    assert str(kette.ZERLEGUNG_AUFTRAGSTEXT_PFAD) in argv[2]
    assert AUFNAHME in argv[2]
    assert "<N> ist 1" in argv[2]
    assert argv[3:] == ["--model", "sonnet", "--permission-mode", "acceptEdits"]


def test_unbekannte_stufe_wird_benannt(
    tmp_path: Path, kein_bestand: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--bis", "quark"])

    assert code == 5
    assert "ANGEHALTEN [stufe_unbekannt]" in capsys.readouterr().out


# --------------------------------------------------------------------------
# --modell: die Fahne der Zerlegung
# --------------------------------------------------------------------------


def test_modell_erreicht_die_claude_kommandozeile_und_den_auftragstext() -> None:
    """``--model`` und das Wurzelfeld ``modell`` tragen beide denselben Wert.

    Beide, nicht eines: die Fahne bestimmt, womit gefahren wird, das
    Wurzelfeld haelt fest, womit gefahren wurde. Fiele eines von beiden
    weg, koennte die Trefferquote spaeter nicht mehr zuordnen.
    """
    argv = kette.zerlegung_argv(AUFNAHME, modell="opus")

    assert argv[3:] == ["--model", "opus", "--permission-mode", "acceptEdits"]
    assert 'Trage als Wurzelfeld modell den Wert "opus" ein.' in argv[2]


def test_ohne_modell_bleibt_es_bei_sonnet() -> None:
    """Die bisherige Konstante ist die Vorgabe - ein Lauf ohne Fahne faehrt wie bisher."""
    argv = kette.zerlegung_argv(AUFNAHME)

    assert kette.ZERLEGUNG_MODELL == "sonnet"
    assert argv[3:] == ["--model", "sonnet", "--permission-mode", "acceptEdits"]
    assert 'Trage als Wurzelfeld modell den Wert "sonnet" ein.' in argv[2]


def test_modell_aus_der_befehlszeile_landet_im_claude_aufruf(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Von ``main`` bis zum Prozessstart - der Weg dazwischen ist der Punkt."""
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste")

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--modell", "opus"]
    )

    assert code == 0
    zerlegung = [argv for argv in aufgezeichnet if argv[0] == kette.CLAUDE_BEFEHL]
    assert len(zerlegung) == 1
    assert "--model" in zerlegung[0]
    assert zerlegung[0][zerlegung[0].index("--model") + 1] == "opus"


def test_modell_steht_bei_der_zerlegung_in_kette_json(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spaeter soll nachvollziehbar sein, womit gefahren wurde - nicht nur, dass."""
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste")

    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--modell", "opus"])
    stufen = _lies_zustand(job_dir)["stufen"]

    assert isinstance(stufen, dict)
    assert stufen["zerlegung"]["modell"] == "opus"


def test_ohne_modell_steht_sonnet_in_kette_json(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []
    _prozess_legt_ausgabe_an(job_dir, aufgezeichnet, monkeypatch)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste")

    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    stufen = _lies_zustand(job_dir)["stufen"]

    assert isinstance(stufen, dict)
    assert stufen["zerlegung"]["modell"] == "sonnet"


def test_uebersprungene_zerlegung_traegt_das_modell_von_heute_nicht_nach(
    tmp_path: Path, kein_bestand: None, prozesse: list[list[str]]
) -> None:
    """Wer nichts gefahren hat, darf nichts behaupten.

    Sonst schriebe ein Lauf mit ``--modell opus``, der die vorhandene
    Zerlegung ueberspringt, ``opus`` in die Buchfuehrung - und die
    Trefferquote schriebe die Ausbeute des Sonnet-Laufs dem falschen
    Modell zu.
    """
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, *ALLE_STUFEN)

    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--modell", "opus"])
    stufen = _lies_zustand(job_dir)["stufen"]

    assert isinstance(stufen, dict)
    assert prozesse == []
    assert "modell" not in stufen["zerlegung"]


def test_trockenlauf_nennt_das_modell_bei_der_zerlegung(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Vor dem Warten sehen, dass die Fahne angekommen ist - nicht erst danach."""
    kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--trocken", "--modell", "opus"]
    )
    ausgabe = capsys.readouterr().out

    assert "Stufe 5 von 6: Zerlegung (Modell), Modell opus" in ausgabe


def test_kopfzeile_der_zerlegung_nennt_ohne_fahne_die_vorgabe(tmp_path: Path) -> None:
    zeile = kette._kopfzeile(5, kette.STUFEN[4], tmp_path / "gibt-es-nicht.json")

    assert zeile == "Stufe 5 von 6: Zerlegung (Modell), Modell sonnet"


# --------------------------------------------------------------------------
# Laufnummer
# --------------------------------------------------------------------------


def test_lauf_2_schreibt_lauf2_und_laesst_lauf1_unberuehrt(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der Kern des Nachschlags: der zweite Lauf legt sich NEBEN den ersten."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste", "zerlegung")
    lauf1_vorher = (job_dir / "kandidaten-lauf1.json").read_text(encoding="utf-8")

    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        if etikett == "zerlegung":
            (job_dir / "kandidaten-lauf2.json").write_text(
                json.dumps(kandidatensatz(2, start_ms=60_000)), encoding="utf-8"
            )
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    code = kette.main(
        ["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--neu-ab", "zerlegung", "--lauf", "2"]
    )

    assert code == 0
    assert (job_dir / "kandidaten-lauf2.json").is_file()
    assert (job_dir / "kandidaten-lauf1.json").read_text(encoding="utf-8") == lauf1_vorher


def test_laufnummer_erreicht_den_auftragstext_des_modellschritts() -> None:
    """``<N>`` und der Auftragsname tragen die Nummer - sonst schriebe das Modell lauf1."""
    text = kette.zerlegung_auftragstext(AUFNAHME, lauf=3)

    assert "<N> ist 3" in text
    assert "zerlegung-lauf3" in text


def test_lauf_2_bestimmt_den_dateinamen_der_stufe() -> None:
    stufen = kette.stufen_fuer(2)

    assert stufen[4].name == "zerlegung"
    assert stufen[4].ausgabe == "kandidaten-lauf2.json"
    # Die uebrigen fuenf Stufen haengen nicht an der Laufnummer.
    assert [stufe.ausgabe for stufe in stufen] != [stufe.ausgabe for stufe in kette.STUFEN]
    assert stufen[:4] == kette.STUFEN[:4]
    assert stufen[5] == kette.STUFEN[5]


def test_trockenlauf_nennt_die_datei_des_zweiten_laufs(
    tmp_path: Path, kein_bestand: None, prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Vor dem Warten sehen, wohin geschrieben wuerde - nicht erst hinterher."""
    kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--trocken", "--lauf", "2"])
    ausgabe = capsys.readouterr().out

    assert "kandidaten-lauf2.json" in ausgabe
    assert "kandidaten-lauf1.json" not in ausgabe


def test_vorhandene_laufdatei_wird_gemeldet_und_uebersprungen(
    tmp_path: Path, kein_bestand: None, prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Liegt ``kandidaten-lauf2.json`` schon da, laeuft kein zweiter Modellaufruf."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste")
    (job_dir / "kandidaten-lauf2.json").write_text(
        json.dumps(kandidatensatz(2, start_ms=60_000)), encoding="utf-8"
    )

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path), "--lauf", "2"])
    ausgabe = capsys.readouterr().out

    assert code == 0
    assert [argv for argv in prozesse if argv[0] == kette.CLAUDE_BEFEHL] == []
    assert "uebersprungen - " in ausgabe
    assert "kandidaten-lauf2.json" in ausgabe


def test_kette_json_traegt_nach_zwei_laeufen_beide_zerlegungseintraege(
    tmp_path: Path, kein_bestand: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der zweite Lauf ueberschreibt die Buchfuehrung des ersten NICHT.

    Beide Eintraege stehen unter ``stufen.zerlegung.laeufe``, je Nummer
    einer - dieselbe Form traegt auch einen dritten Lauf.
    """
    job_dir = _job_dir(tmp_path)
    aufgezeichnet: list[list[str]] = []

    def _falscher_prozess(argv: Sequence[str], *, etikett: str) -> int:
        aufgezeichnet.append(list(argv))
        if etikett == "zerlegung":
            lauf = 2 if any(arg.endswith("zerlegung-lauf2.") for arg in argv) else 1
            (job_dir / f"kandidaten-lauf{lauf}.json").write_text(
                json.dumps(kandidatensatz(lauf, start_ms=60_000 * (lauf - 1))), encoding="utf-8"
            )
        else:
            _lege_ausgaben_an(job_dir, etikett)
        return 0

    monkeypatch.setattr(kette, "fuehre_prozess", _falscher_prozess)

    assert kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)]) == 0
    assert (
        kette.main(
            [
                "--aufnahme",
                AUFNAHME,
                "--wurzel",
                str(tmp_path),
                "--neu-ab",
                "zerlegung",
                "--lauf",
                "2",
                "--modell",
                "opus",
            ]
        )
        == 0
    )

    stufen = _lies_zustand(job_dir)["stufen"]
    assert isinstance(stufen, dict)
    laeufe = stufen["zerlegung"]["laeufe"]
    assert sorted(laeufe) == ["1", "2"]
    assert laeufe["1"]["modell"] == "sonnet"
    assert laeufe["2"]["modell"] == "opus"
    assert laeufe["1"]["ausgabe"].endswith("kandidaten-lauf1.json")
    assert laeufe["2"]["ausgabe"].endswith("kandidaten-lauf2.json")
    assert laeufe["1"]["status"] == kette.STATUS_FERTIG
    # Und die Zusammenfuehrung hat beide Laeufe gesehen.
    ergebnis = json.loads((job_dir / "kandidaten.json").read_text(encoding="utf-8"))
    assert ergebnis["laeufe"] == [1, 2]
