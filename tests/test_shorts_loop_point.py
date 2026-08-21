"""Tests fuer den Auftrag shorts-schleifenpunkt(-korrektur): Rasten auf Wortgrenzen und Urteil."""

from __future__ import annotations

import pytest

from matrix_auto_cutter.shorts.loop_point import (
    EINSCHLUSS_TOLERANZ_MS,
    GEEIGNET,
    GRENZWERTIG,
    LOOP_PAD_MS,
    MAX_SHIFT_MS,
    MIN_PAUSE_MS,
    MIN_SPAN_MS,
    PREFERRED_SPAN_MS,
    UNGEEIGNET,
    LoopBoundaries,
    LoopPointError,
    NotSnappableError,
    SpanTooShortError,
    beurteile_grenzen,
    beurteile_schleifentauglichkeit,
    rasten_auf_wortgrenzen,
)
from matrix_auto_cutter.shorts.subtitle_lines import Word


def _word(start_ms: int, end_ms: int, text: str) -> Word:
    return Word(start_ms, end_ms, text)


# ---------------------------------------------------------------------------
# Teil A: rasten_auf_wortgrenzen
# ---------------------------------------------------------------------------


def test_rasten_wirft_bei_leerer_wortliste() -> None:
    with pytest.raises(NotSnappableError):
        rasten_auf_wortgrenzen([], 0, 10000)


def test_rasten_wirft_wenn_ende_vor_start_liegt() -> None:
    words = [_word(0, 300, "Wort")]
    with pytest.raises(LoopPointError):
        rasten_auf_wortgrenzen(words, 5000, 1000)


def test_rasten_erstes_und_letztes_wort_ohne_nachbar_liefert_none_pausen() -> None:
    # Genau der Kandidat-18-Fall aus dem Pruefstein: Startgrenze vor dem
    # ersten Wort, Endgrenze auf dem letzten Wortende - keine Nachbarworte
    # vorhanden, Pausen also nicht pruefbar (None), kein Fehler.
    words = [
        _word(10, 160, "Wir"),
        _word(160, 270, "haben"),
        _word(74000, 74600, "eintritt."),
    ]
    boundaries = rasten_auf_wortgrenzen(words, 0, 74600)
    # Ohne Nachbarwort ist die Pause nicht pruefbar (None) - der volle
    # LOOP_PAD_MS-Puffer gilt dann ungebremst (Punkt 4a).
    assert boundaries.start_ms == 10 - LOOP_PAD_MS
    assert boundaries.start_shift_ms == 10
    assert boundaries.pause_before_ms is None
    assert boundaries.end_ms == 74600 + LOOP_PAD_MS
    assert boundaries.end_shift_ms == 0
    assert boundaries.pause_after_ms is None


def test_rasten_mitten_im_wort_faellt_heraus_die_spanne_wird_nur_kleiner() -> None:
    # Auftrag shorts-rastrichtung: die gerastete Spanne ist stets eine
    # TEILMENGE der vorgegebenen, nie eine Obermenge - abgeloest ist die
    # bisherige Regel, die auch rueckwaerts (spannenverlaengernd) auf die
    # naechstgelegene Wortgrenze schnappte.
    words = [
        _word(0, 300, "Erstes"),
        _word(600, 900, "Zweites"),
        _word(1200, 1900, "Drittes"),
        _word(2200, 2500, "Viertes"),
        _word(10000, 10300, "Fuenftes"),
        _word(10600, 11300, "Sechstes"),
        _word(11600, 11900, "Siebtes"),
    ]
    # start_ms=1500 liegt mitten in "Drittes" (1200-1900), mehr als
    # EINSCHLUSS_TOLERANZ_MS (150) nach dessen Anfang entfernt (300 ms) -
    # "Drittes" faellt heraus, das erste vollstaendig enthaltene Wort ist
    # "Viertes" (2200-2500), die Startgrenze wandert vorwaerts auf 2200.
    # end_ms=11000 liegt mitten in "Sechstes" (10600-11300), mehr als
    # EINSCHLUSS_TOLERANZ_MS vor dessen Ende entfernt (300 ms) - "Sechstes"
    # faellt heraus, das letzte vollstaendig enthaltene Wort ist "Fuenftes"
    # (10000-10300), die Endgrenze wandert rueckwaerts auf 10300.
    boundaries = rasten_auf_wortgrenzen(words, 1500, 11000)
    # Beide Pausen (300 ms) tragen den vollen LOOP_PAD_MS-Puffer (200 ms).
    assert boundaries == LoopBoundaries(
        start_ms=2200 - LOOP_PAD_MS,
        end_ms=10300 + LOOP_PAD_MS,
        start_shift_ms=700,
        end_shift_ms=700,
        pause_before_ms=300,
        pause_after_ms=300,
    )


def test_rasten_einschlusstoleranz_nimmt_knapp_angeschnittenes_wort_mit() -> None:
    # Die Ausnahme: liegt die Grenze hoechstens EINSCHLUSS_TOLERANZ_MS nach
    # dem Anfang (bzw. vor dem Ende) eines Wortes, gilt es als gewollt und
    # die Grenze wandert auf sein Ende zurueck/vor - auch wenn das Wort
    # selbst nicht vollstaendig in der Spanne liegt.
    words = [
        _word(0, 300, "Vor"),
        _word(1000, 1900, "Angeschnitten"),
        _word(2200, 2500, "Mitte"),
        _word(9000, 9900, "Ebenfalls"),
        _word(20000, 20300, "Nach"),
    ]
    # start_ms=1100 liegt 100 ms (< 150) nach dem Anfang von "Angeschnitten"
    # (1000-1900) - das Wort gilt als gewollt, die Startgrenze wandert auf
    # seinen Anfang (1000) zurueck, obwohl "Angeschnitten" selbst nicht
    # vollstaendig in [1100, ...] liegt.
    assert EINSCHLUSS_TOLERANZ_MS == 150
    boundaries = rasten_auf_wortgrenzen(words, 1100, 9800)
    assert boundaries.start_ms == 1000 - LOOP_PAD_MS
    assert boundaries.start_shift_ms == -100
    # end_ms=9800 liegt 100 ms (< 150) vor dem Ende von "Ebenfalls"
    # (9000-9900) - das Wort gilt als gewollt, die Endgrenze wandert auf
    # sein Ende (9900) vor.
    assert boundaries.end_ms == 9900 + LOOP_PAD_MS
    assert boundaries.end_shift_ms == -100


def test_rasten_ohne_einschlusstoleranz_und_ohne_vollstaendiges_wort_wirft() -> None:
    # Liegt kein Wort vollstaendig in der Spanne und greift auch die
    # Einschlusstoleranz nicht, ist die Grenze nicht rastbar.
    words = [
        _word(0, 300, "Erstes"),
        _word(9000, 9400, "Letztes"),
    ]
    with pytest.raises(NotSnappableError):
        rasten_auf_wortgrenzen(words, 5000, 5100)


def test_rasten_sauber_in_grossen_pausen_braucht_keine_verschiebung() -> None:
    words = [
        _word(0, 300, "Vor"),
        _word(6000, 6300, "Start"),
        _word(6600, 6900, "Mitte"),
        _word(14300, 14600, "Ende"),
        _word(21000, 21300, "Nach"),
    ]
    boundaries = rasten_auf_wortgrenzen(words, 6000, 14600)
    # Beide Pausen liegen weit ueber LOOP_PAD_MS - der volle Puffer greift.
    assert boundaries.start_ms == 6000 - LOOP_PAD_MS
    assert boundaries.start_shift_ms == 0
    assert boundaries.end_ms == 14600 + LOOP_PAD_MS
    assert boundaries.end_shift_ms == 0
    assert boundaries.pause_before_ms == 5700
    assert boundaries.pause_after_ms == 6400


def test_rasten_wirft_wenn_mehr_als_max_shift_gebraucht_wird() -> None:
    words = [
        _word(0, 300, "Erstes"),
        _word(9000, 9400, "Letztes"),
    ]
    with pytest.raises(NotSnappableError):
        rasten_auf_wortgrenzen(words, -(MAX_SHIFT_MS + 1000), 20000)


def test_rasten_wirft_wenn_ergebnis_kuerzer_als_min_span() -> None:
    words = [
        _word(0, 300, "Vor"),
        _word(5000, 5300, "Start"),
        _word(5600, 5900, "Mitte"),
        _word(6200, 6500, "Ende"),
        _word(15000, 15300, "Nach"),
    ]
    assert MIN_SPAN_MS > 6500 - 5000
    with pytest.raises(SpanTooShortError):
        rasten_auf_wortgrenzen(words, 5000, 6500)


def test_rasten_akzeptiert_auch_eine_kurze_pause_zum_nachbarwort() -> None:
    # shorts-schleifenpunkt-korrektur, Teil B: MIN_PAUSE_MS ist keine
    # Rastbedingung mehr. "B" liegt nur 100 ms vor "Nach" (< MIN_PAUSE_MS,
    # 250) - fruehr waere die Endgrenze deshalb auf "A" zurueckgewandert,
    # jetzt bleibt sie auf "B", weil das die naechstgelegene Wortgrenze ist.
    words = [
        _word(0, 300, "Vor"),
        _word(1000, 1300, "Start"),
        _word(6600, 6900, "A"),
        _word(7200, 7500, "B"),
        _word(7600, 7900, "Nach"),
    ]
    boundaries = rasten_auf_wortgrenzen(words, 1000, 7500)
    assert boundaries.end_shift_ms == 0
    assert boundaries.pause_after_ms == 100  # Pause zu "Nach": 7600-7500
    # Der Puffer wird auf die (kurze) Pause gekappt, statt ins Nachbarwort
    # "Nach" hineinzuragen (Teil C).
    assert boundaries.end_ms == 7500 + 100


def test_rasten_puffer_wird_auf_knappe_pause_gekappt_statt_ins_nachbarwort_zu_ragen() -> None:
    # Teil C: der Puffer darf nie in ein Nachbarwort hineinragen. Die Pause
    # vor der Startgrenze betraegt nur 50 ms (< LOOP_PAD_MS, 200) - der
    # Puffer wird auf diese 50 ms gekappt, die Startgrenze beruehrt damit
    # genau das Ende des Vorgaengerworts, ohne es anzuschneiden.
    words = [
        _word(0, 300, "Vor"),
        _word(350, 650, "Start"),
        _word(900, 1200, "Mitte"),
        _word(7000, 7300, "Ende"),
        _word(20000, 20300, "Nach"),
    ]
    boundaries = rasten_auf_wortgrenzen(words, 350, 7300)
    assert boundaries.pause_before_ms == 50
    assert boundaries.start_ms == 350 - 50
    assert boundaries.start_ms == words[0].end_ms  # beruehrt "Vor", schneidet es nicht an


def test_rasten_pad_bleibt_innerhalb_einer_knappen_aber_ausreichenden_pause() -> None:
    words = [
        _word(0, 20, "Vor"),
        _word(300, 600, "Start"),
        _word(900, 1200, "Mitte"),
        _word(7000, 7300, "Ende"),
        _word(20000, 20300, "Nach"),
    ]
    boundaries = rasten_auf_wortgrenzen(words, 300, 7300)
    assert boundaries.pause_before_ms == 280
    assert boundaries.start_ms == 300 - LOOP_PAD_MS


# ---------------------------------------------------------------------------
# Teil B: beurteile_schleifentauglichkeit / beurteile_grenzen
# ---------------------------------------------------------------------------


def test_urteil_geeignet_bei_ausreichenden_pausen_und_langer_spanne() -> None:
    urteil = beurteile_schleifentauglichkeit(
        pause_before_ms=MIN_PAUSE_MS, pause_after_ms=MIN_PAUSE_MS, span_ms=PREFERRED_SPAN_MS
    )
    assert urteil.einstufung == GEEIGNET
    assert urteil.pausen_ausreichend is True


def test_urteil_grenzwertig_bei_pause_unter_mindestwert() -> None:
    # shorts-schleifenpunkt-korrektur, Teil D: eine zu kurze Pause ist kein
    # Ausschlussgrund mehr, nur noch grenzwertig.
    urteil = beurteile_schleifentauglichkeit(
        pause_before_ms=MIN_PAUSE_MS - 1, pause_after_ms=MIN_PAUSE_MS, span_ms=PREFERRED_SPAN_MS
    )
    assert urteil.einstufung == GRENZWERTIG
    assert urteil.pausen_ausreichend is False


def test_urteil_grenzwertig_bei_nicht_pruefbarer_pause() -> None:
    urteil = beurteile_schleifentauglichkeit(
        pause_before_ms=None, pause_after_ms=MIN_PAUSE_MS, span_ms=PREFERRED_SPAN_MS
    )
    assert urteil.einstufung == GRENZWERTIG
    assert urteil.pausen_ausreichend is False


def test_urteil_ungeeignet_nur_noch_bei_zu_kurzer_spanne() -> None:
    # Teil D: selbst mit beiden Pausen bekannt und ausreichend bleibt eine
    # zu kurze Spanne der einzige Ausschlussgrund.
    urteil = beurteile_schleifentauglichkeit(
        pause_before_ms=MIN_PAUSE_MS, pause_after_ms=MIN_PAUSE_MS, span_ms=MIN_SPAN_MS - 1
    )
    assert urteil.einstufung == UNGEEIGNET


def test_urteil_ungeeignet_bei_zu_kurzer_spanne_auch_ohne_pausen() -> None:
    urteil = beurteile_schleifentauglichkeit(
        pause_before_ms=None, pause_after_ms=None, span_ms=MIN_SPAN_MS - 1
    )
    assert urteil.einstufung == UNGEEIGNET


def test_beurteile_grenzen_liest_werte_aus_loop_boundaries() -> None:
    boundaries = LoopBoundaries(
        start_ms=1000,
        end_ms=7000,
        start_shift_ms=0,
        end_shift_ms=0,
        pause_before_ms=MIN_PAUSE_MS,
        pause_after_ms=MIN_PAUSE_MS,
    )
    urteil = beurteile_grenzen(boundaries)
    assert urteil.span_ms == 6000
    assert urteil.einstufung == GRENZWERTIG  # Spanne < PREFERRED_SPAN_MS
