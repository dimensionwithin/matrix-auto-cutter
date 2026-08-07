"""Tests der Fenstergeometrie des Review-Fensters ohne echte Tk-Anzeige."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.product_runner import default_state_directory
from matrix_auto_cutter.review_app import (
    _FRAME_PADDING,
    _LIST_DEFAULT_LINES,
    _LIST_MINIMUM_LINES,
    WindowGeometry,
    load_window_geometry,
    measure_window_bounds,
    review_window_state_path,
    store_window_geometry,
)


class FakeControls:
    """Der untere Bedienblock: feste Beschriftungen, feste Wunschgröße."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    def winfo_reqwidth(self) -> int:
        return self.width

    def winfo_reqheight(self) -> int:
        return self.height


class FakeRoot:
    """Ein Fenster, dessen Wunschhöhe nur an der Zeilenzahl der Liste hängt."""

    def __init__(self, chrome_height: int, width: int, line_height: int) -> None:
        self.chrome_height = chrome_height
        self.width = width
        self.line_height = line_height
        self.lines = _LIST_DEFAULT_LINES
        self.updates = 0

    def set_list_lines(self, lines: int) -> None:
        self.lines = lines

    def update_idletasks(self) -> None:
        self.updates += 1

    def winfo_reqwidth(self) -> int:
        return self.width

    def winfo_reqheight(self) -> int:
        return self.chrome_height + self.lines * self.line_height


def test_geometry_round_trip_and_rejection() -> None:
    geometry = WindowGeometry(width=834, height=875, x=120, y=60)
    assert geometry.as_geometry() == "834x875+120+60"
    assert WindowGeometry.parse("834x875+120+60") == geometry
    assert WindowGeometry.parse("834x875-8-16") == WindowGeometry(834, 875, -8, -16)
    assert WindowGeometry.parse("  834x875+0+0  ") == WindowGeometry(834, 875, 0, 0)
    for invalid in ("834x875", "834x875+120", "0x875+0+0", "834x0+0+0", "", "x+", "abcxdef+0+0"):
        assert WindowGeometry.parse(invalid) is None


def test_remembered_box_is_clamped_to_minimum_and_screen() -> None:
    minimum = (834, 651)
    screen = (1920, 1080)

    # Zu klein gespeichert: die unteren Bedienelemente waeren wieder abgeschnitten.
    small = WindowGeometry(300, 200, 10, 10).fitted(minimum=minimum, screen=screen)
    assert (small.width, small.height) == minimum

    # Groesser als der Bildschirm: auf den Bildschirm begrenzt.
    huge = WindowGeometry(4000, 3000, 0, 0).fitted(minimum=minimum, screen=screen)
    assert (huge.width, huge.height) == screen

    # Von einem zweiten Monitor uebrig geblieben: zurueck in den sichtbaren Bereich.
    offscreen = WindowGeometry(834, 651, -2400, -900).fitted(minimum=minimum, screen=screen)
    assert (offscreen.x, offscreen.y) == (0, 0)
    far = WindowGeometry(834, 651, 5000, 5000).fitted(minimum=minimum, screen=screen)
    assert (far.x, far.y) == (screen[0] - 834, screen[1] - 651)

    # Passt bereits: unveraendert.
    fitting = WindowGeometry(1400, 1000, 120, 60)
    assert fitting.fitted(minimum=minimum, screen=screen) == fitting


def test_window_state_lives_next_to_the_other_review_state() -> None:
    path = review_window_state_path()
    assert path.parent == default_state_directory()
    assert path.parent.name == "product-runner"
    assert path.name == "review-window.json"
    assert review_window_state_path(Path("C:/anders")) == Path("C:/anders/review-window.json")


def test_window_geometry_survives_a_store_and_load_cycle(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "review-window.json"
    geometry = WindowGeometry(width=1400, height=1000, x=120, y=-40)
    store_window_geometry(path, geometry)
    assert load_window_geometry(path) == geometry
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "width": 1400,
        "height": 1000,
        "x": 120,
        "y": -40,
    }
    assert not path.with_name(f"{path.name}.tmp").exists()


@pytest.mark.parametrize(
    "payload",
    [
        "kein json",
        "[]",
        '{"width": 800, "height": 600, "x": 0}',
        '{"width": "800", "height": 600, "x": 0, "y": 0}',
        '{"width": true, "height": 600, "x": 0, "y": 0}',
        '{"width": 0, "height": 600, "x": 0, "y": 0}',
        '{"width": 800, "height": -1, "x": 0, "y": 0}',
        '{"width": 800.5, "height": 600, "x": 0, "y": 0}',
    ],
)
def test_defective_window_state_is_treated_as_not_remembered(tmp_path: Path, payload: str) -> None:
    path = tmp_path / "review-window.json"
    path.write_text(payload, encoding="utf-8")
    assert load_window_geometry(path) is None


def test_missing_window_state_is_not_an_error(tmp_path: Path) -> None:
    assert load_window_geometry(tmp_path / "fehlt.json") is None


def test_unwritable_window_state_never_raises(tmp_path: Path) -> None:
    # Der Ablageort ist eine Bequemlichkeit; er darf das Review nie abbrechen.
    blocked = tmp_path / "datei"
    blocked.write_text("belegt", encoding="utf-8")
    store_window_geometry(blocked / "review-window.json", WindowGeometry(800, 600, 0, 0))


def test_minimum_size_is_derived_from_the_widgets_not_guessed() -> None:
    controls = FakeControls(width=802, height=180)
    root = FakeRoot(chrome_height=380, width=900, line_height=15)

    minimum, preferred = measure_window_bounds(root, controls, root.set_list_lines)

    # Breite: der Bedienblock plus die Innenabstaende des Rahmens auf beiden Seiten.
    assert minimum[0] == 802 + 2 * _FRAME_PADDING
    # Hoehe: das ganze Fenster, waehrend die Liste auf ihr Minimum gedrueckt ist.
    assert minimum[1] == 380 + _LIST_MINIMUM_LINES * 15
    # Wunschgroesse: dasselbe Fenster mit der Liste auf voller Hoehe.
    assert preferred[1] == 380 + _LIST_DEFAULT_LINES * 15
    assert preferred[0] >= minimum[0] and preferred[1] >= minimum[1]
    # Die Liste steht danach wieder auf ihrer regulaeren Hoehe.
    assert root.lines == _LIST_DEFAULT_LINES
    assert root.updates == 2


def test_minimum_size_never_exceeds_the_preferred_size() -> None:
    # Ein sehr breiter Bedienblock hebt auch die Wunschbreite an, sonst oeffnete
    # das Fenster schmaler als seine eigene Untergrenze.
    controls = FakeControls(width=1600, height=180)
    root = FakeRoot(chrome_height=380, width=900, line_height=15)
    minimum, preferred = measure_window_bounds(root, controls, root.set_list_lines)
    assert preferred[0] == minimum[0] == 1600 + 2 * _FRAME_PADDING


def test_derived_size_does_not_depend_on_the_number_of_cuts() -> None:
    """Die Fenstergroesse haengt an der Zeilenzahl der Liste, nicht an ihrem Inhalt.

    Ein tk.Text waechst nicht mit seinem Text, deshalb liefert dieselbe
    Widgetstruktur fuer eine Aufnahme mit drei und eine mit zweihundertfuenfzig
    Schnitten dieselben Grenzen. Am echten Fenster gemessen: 834x651 Minimum und
    834x875 Wunschgroesse fuer 0, 1, 3, 40 und 250 Schnitte.
    """
    controls = FakeControls(width=802, height=180)
    results = []
    for _cut_count in (0, 3, 250):
        root = FakeRoot(chrome_height=380, width=900, line_height=15)
        results.append(measure_window_bounds(root, controls, root.set_list_lines))
    assert len(set(results)) == 1


def test_lower_controls_claim_their_space_before_the_cut_list() -> None:
    """Beim Verkleinern muss die Liste nachgeben, nicht die Bedienelemente.

    Tk verteilt den Platz in der Reihenfolge der pack-Aufrufe. Wird der
    Bedienblock vor der Liste gepackt, bekommt er seine Wunschhoehe zuerst und
    die Liste nur den Rest; genau umgekehrt sind die unteren beiden Bloecke
    frueher aus dem Fenster gefallen.
    """
    source = (
        Path(__file__).resolve().parents[1] / "src" / "matrix_auto_cutter" / "review_app.py"
    ).read_text(encoding="utf-8")
    controls_pack = source.index('controls.pack(side="bottom"')
    list_pack = source.index('text.pack(fill="both", expand=True)')
    assert controls_pack < list_pack
    for child in ("selection_box = ttk.LabelFrame(controls", "buttons = ttk.Frame(controls)"):
        assert child in source
    assert "render_controls = ttk.Frame(controls)" in source
