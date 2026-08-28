"""Pruefungen fuer ``scripts/START-SHORTS-KETTE.ps1``.

Ein PowerShell-Skript laesst sich aus pytest heraus nicht sinnvoll
ausfuehren -- der Lauf dauerte anderthalb Stunden und ruefe ein Modell an.
Geprueft wird darum der Text der Datei.

Ueber die im Auftrag genannten Textprufungen hinaus steht hier eine
Kopplungspruefung: das Skript traegt die Zahl 2 als "nichts zu tun"
fest eingebaut, und diese Zahl stammt aus ``kette.CODE_KEINE_AUFNAHME``.
Wird sie dort je geaendert oder an einer dritten Stelle vergeben, wuerde
das Skript ohne diese Pruefung STILL falsch werden: es meldete der
Aufgabenplanung Erfolg, wo ein Fehlschlag vorliegt. Genau diesen Fall
faengt ``test_nichts_zu_tun_code_stimmt_mit_kette_ueberein`` ab. Textgleichheit
allein leistet das nicht, darum die zusaetzliche Form.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import kette

WURZEL = Path(__file__).resolve().parents[1]
SKRIPT = WURZEL / "scripts" / "START-SHORTS-KETTE.ps1"


@pytest.fixture(scope="module")
def text() -> str:
    return SKRIPT.read_text(encoding="utf-8")


def test_skript_existiert() -> None:
    assert SKRIPT.is_file(), f"{SKRIPT} fehlt"


def test_traegt_den_festen_repo_pfad(text: str) -> None:
    """Der Aufgabenplaner startet mit unbestimmtem Arbeitsverzeichnis."""
    assert r"$RepoWurzel = 'P:\DimensionWithin-MatrixMarketAutoEditor'" in text
    assert "Set-Location -LiteralPath $RepoWurzel" in text


def test_ruft_die_kette_auf(text: str) -> None:
    assert "'run', 'python', '-m', 'matrix_auto_cutter.shorts.kette'" in text


def test_ohne_parameter_kein_aufnahme_schalter(text: str) -> None:
    """``--aufnahme`` haengt am Parameter ``-Aufnahme`` und nur an ihm.

    Ohne ihn waehlt die Kette selbst die juengste unverfallene Aufnahme
    (``kette.bestimme_aufnahme``) -- genau das will die Aufgabenplanung.
    """
    grundaufruf = next(z for z in text.splitlines() if "$Argumente = @(" in z)
    assert "--aufnahme" not in grundaufruf, f"--aufnahme steht schon im Grundaufruf: {grundaufruf}"
    anhaenge = [
        zeile
        for zeile in text.splitlines()
        if "$Argumente +=" in zeile and "--aufnahme" in zeile
    ]
    assert anhaenge, "--aufnahme wird nirgends angehaengt"
    for zeile in anhaenge:
        assert "if ($Aufnahme)" in zeile, f"--aufnahme unbedingt angehaengt: {zeile}"


@pytest.mark.parametrize(
    ("schalter", "durchgereicht"),
    [("$Trocken", "--trocken"), ("$Modell", "--modell")],
)
def test_handschalter_werden_durchgereicht(text: str, schalter: str, durchgereicht: str) -> None:
    assert f"if ({schalter})" in text
    assert durchgereicht in text


def test_kein_urteil_kein_bau_kein_git(text: str) -> None:
    """Die Kette endet bei den Kandidaten -- das menschliche Tor bleibt.

    Der Wortlaut ``urteilslauf`` steht in einer Schlussbemerkung des
    Skripts; geprueft wird darum auf einen AUFRUF, nicht auf das Wort.
    """
    ohne_kommentare = "\n".join(
        zeile for zeile in text.splitlines() if not zeile.lstrip().startswith("#")
    )
    for verbot in (
        r"shorts\.urteilslauf",
        r"\burteilslauf\b\s*(?:--|\n)",
        r"\bgit\b\s+\w",
        r"shorts\.build\b",
        r"\bbuild\b\s+--",
    ):
        gefunden = re.findall(verbot, ohne_kommentare)
        assert not gefunden, f"verbotener Aufruf {verbot!r}: {gefunden}"


def test_protokollpfad(text: str) -> None:
    assert r"artefakte\repeat\kette-protokoll" in text
    assert "yyyy-MM-dd-HHmmss" in text


def test_zusammenfassung_nennt_die_geforderten_stuecke(text: str) -> None:
    zeile = next(z for z in text.splitlines() if "ZUSAMMENFASSUNG" in z)
    for stueck in ("Start", "Ende", "Dauer", "Rueckgabecode", "Aufnahme"):
        assert stueck in zeile


def test_nichts_zu_tun_code_stimmt_mit_kette_ueberein(text: str) -> None:
    """Die 2 im Skript ist ``kette.CODE_KEINE_AUFNAHME`` -- keine geratene Zahl."""
    assert kette.CODE_KEINE_AUFNAHME == 2
    assert "if ($Rueckgabecode -eq 2)" in text


def test_nichts_zu_tun_bleibt_von_fehlschlaegen_unterscheidbar() -> None:
    """Code 2 darf in ``kette.py`` nur fuer "nichts zu tun" stehen.

    Bekaeme ein echter Fehlschlag je dieselbe 2, senkte das Skript ihn
    stillschweigend auf 0 und die Aufgabenplanung meldete nichts.
    """
    quelle = (WURZEL / "src" / "matrix_auto_cutter" / "shorts" / "kette.py").read_text(
        encoding="utf-8"
    )
    andere = {
        name: int(wert)
        for name, wert in re.findall(r"^(CODE_[A-Z_]+) = (\d+)$", quelle, re.MULTILINE)
        if name != "CODE_KEINE_AUFNAHME"
    }
    assert kette.CODE_KEINE_AUFNAHME not in andere.values(), (
        f"Code 2 doppelt vergeben: {andere}"
    )


def test_marker_der_beiden_faelle_gibt_es_in_kette(text: str) -> None:
    """Die Marker, die das Skript mitliest, muessen in ``kette.py`` stehen."""
    quelle = (WURZEL / "src" / "matrix_auto_cutter" / "shorts" / "kette.py").read_text(
        encoding="utf-8"
    )
    assert 'print(f"ANGEHALTEN [{fehler.code_name}]' in quelle
    for marker in ("keine_aufnahme", "nur_verfallen"):
        assert f'KetteFehlschlag(\n            "{marker}"' in quelle or (
            f'KetteFehlschlag("{marker}"' in quelle
        ), f"{marker} nicht mehr in kette.py"
        assert marker in text, f"{marker} nicht mehr im Skript"
