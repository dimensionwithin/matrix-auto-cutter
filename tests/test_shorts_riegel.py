r"""Der Riegel selbst unter Pruefung.

Eine Vorkehrung, die niemand prueft, ist eine Vorkehrung, von der man
glaubt, dass es sie gibt. Diese Datei heisst ``test_shorts_*``, damit die
Fixtures aus ``tests/conftest.py`` hier genauso greifen wie in den
uebrigen Shorts-Tests - sie prueft den Riegel also von innen, aus der
Lage eines gewoehnlichen Shorts-Tests heraus.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from conftest import RiegelVerletzt


def test_subprocess_run_ist_in_shorts_tests_versperrt() -> None:
    """Ein absichtlicher Start scheitert - und startet nichts."""
    with pytest.raises(RiegelVerletzt) as fehler:
        subprocess.run([sys.executable, "-c", "pass"], check=False)

    assert "subprocess.run ist in Shorts-Tests versperrt" in str(fehler.value)
    assert "echter_unterprozess" in str(fehler.value)


def test_subprocess_popen_ist_ebenso_versperrt() -> None:
    """``Popen`` ist der Weg, den der Urteilslauf nimmt - auch der ist zu."""
    with pytest.raises(RiegelVerletzt):
        subprocess.Popen([sys.executable, "-c", "pass"])


def test_ein_pfad_auf_f_laesst_den_test_scheitern() -> None:
    """``Path.exists`` verschluckt ``OSError`` - dieser Fehler muss durchkommen."""
    with pytest.raises(RiegelVerletzt) as fehler:
        Path(r"F:\MatrixMarketAutoEdit\Shorts Rendered").exists()

    assert "Tests schreiben und lesen nur unter tmp_path" in str(fehler.value)


def test_schreiben_nach_f_kommt_gar_nicht_erst_zum_dateisystem() -> None:
    """Auch ``open`` im Schreibmodus wird abgefangen, nicht nur das Lesen."""
    with (
        pytest.raises(RiegelVerletzt),
        open(r"F:\darf-es-nicht-geben.txt", "w", encoding="utf-8"),
    ):
        pass  # pragma: no cover - der Riegel laesst es nie so weit kommen


@pytest.mark.echter_unterprozess
def test_die_markierung_gibt_den_unterprozess_wieder_frei() -> None:
    """Ohne diesen Weg waeren die Platzhalter-Tests des Urteilslaufs unmoeglich."""
    fertig = subprocess.run([sys.executable, "-c", "print('frei')"], capture_output=True)

    assert fertig.returncode == 0
    assert fertig.stdout.strip() == b"frei"


def test_tmp_path_bleibt_ungehindert(tmp_path: Path) -> None:
    """Der Riegel darf den gewoehnlichen Testbetrieb nicht behindern."""
    ziel = tmp_path / "unterordner" / "datei.txt"
    ziel.parent.mkdir(parents=True)
    ziel.write_text("frei", encoding="utf-8")

    assert ziel.read_text(encoding="utf-8") == "frei"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unterordner"]
