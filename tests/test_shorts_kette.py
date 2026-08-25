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


def _lege_ausgaben_an(job_dir: Path, *stufennamen: str, dauer_ms: int = 584_900) -> None:
    """Lege die Ausgaben der genannten Stufen an - Inhalt egal, ausser beim Auftrag."""
    job_dir.mkdir(parents=True, exist_ok=True)
    for stufe in kette.STUFEN:
        if stufe.name not in stufennamen:
            continue
        ziel = job_dir / stufe.ausgabe
        if stufe.name == "auftrag":
            ziel.write_text(
                json.dumps({"rendered_video": {"duration_ms": dauer_ms}}), encoding="utf-8"
            )
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


def test_zwei_zerlegungslaeufe_geben_code_6(
    tmp_path: Path,
    kein_bestand: None,
    prozesse: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Zwei ``kandidaten-laufN.json`` - hier wird nicht geraten, hier wird angehalten."""
    job_dir = _job_dir(tmp_path)
    _lege_ausgaben_an(job_dir, "auftrag", "avatar_cut", "transcript", "wortliste", "zerlegung")
    (job_dir / "kandidaten-lauf2.json").write_text("{}", encoding="utf-8")

    code = kette.main(["--aufnahme", AUFNAHME, "--wurzel", str(tmp_path)])
    ausgabe = capsys.readouterr().out

    assert code == 6
    assert "ANGEHALTEN [zusammenfuehrung_fehlt]" in ausgabe
    assert not (job_dir / "kandidaten.json").exists()


def test_zusammenfuehrung_kopiert_den_einzigen_lauf(tmp_path: Path) -> None:
    """Bei einem Lauf ist die Zusammenfuehrung eine Kopie und sonst nichts."""
    job_dir = _job_dir(tmp_path)
    job_dir.mkdir(parents=True)
    (job_dir / "kandidaten-lauf1.json").write_text('{"kandidaten": []}', encoding="utf-8")

    ziel = kette.fuehre_zusammen(job_dir)

    assert ziel == job_dir / "kandidaten.json"
    assert ziel.read_text(encoding="utf-8") == '{"kandidaten": []}'


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
