"""Auftrag shorts-pegelschnitt: die Pegelkorrektur einer Zeitmarke.

Alle Tests arbeiten mit einem eingesetzten ``process_runner``: das Verfahren
selbst (Fenster, Raster, Gleichstandsregel, Fehlerpfade) haengt nicht an einer
echten Mediendatei, und die Tests laufen ohne ffmpeg und ohne Laufwerk F:.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts.level_cut import (
    _ASTATS_KEY,
    _BLOCKS_PER_MEASURE,
    MEASURE_MS,
    MIN_PAUSE_MS,
    SEARCH_WINDOW_MS,
    SEARCH_WINDOW_START_MS,
    STEP_MS,
    STILLE_ABSTAND_DB,
    STILLE_UNTERBRECHUNG_MAX_MS,
    TIE_TOLERANCE_DB,
    VERFAHREN_BEREICHSMITTE,
    VERFAHREN_TIEFSTER_PUNKT,
    VORLAUF_MAX_MS,
    VORLAUF_REST_MS,
    VORLAUF_SUCHE_MAX_MS,
    LevelCutFailed,
    ProcessResult,
    _combine_to_measure_window,
    finde_stillevorlauf,
    verschiebe_auf_leiseste_stelle,
)

MEDIA = Path("egal.mp4")
FFMPEG = Path("ffmpeg.exe")

BLOCK_COUNT = 2 * SEARCH_WINDOW_MS // STEP_MS + _BLOCKS_PER_MEASURE
"""So viele 10-ms-Bloecke liefert ein vollstaendiges Fenster."""


def _stdout_for(levels: list[float]) -> str:
    """Baue eine ffmpeg-``ametadata``-Ausgabe, wie astats sie je Block druckt."""
    lines = []
    for index, level in enumerate(levels):
        lines.append(f"frame:{index}    pts:{index * 480}     pts_time:{index / 100:.2f}")
        lines.append(f"{_ASTATS_KEY}={level:.6f}")
    return "\n".join(lines) + "\n"


def _runner_for(levels: list[float], exit_code: int = 0):
    """Ein ``process_runner``, der genau diese Blockpegel zurueckmeldet."""
    calls: list[list[str]] = []

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        calls.append(list(arguments))
        return ProcessResult(exit_code, _stdout_for(levels))

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _flat_with_dip(dip_block_index: int, *, loud: float = -20.0, quiet: float = -60.0):
    """Gleichmaessig lautes Fenster mit einer leisen Senke von 4 Bloecken (= 40 ms)."""
    levels = [loud] * BLOCK_COUNT
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[dip_block_index + offset] = quiet
    return levels


def test_marke_wandert_auf_die_leiseste_stelle() -> None:
    """Die Senke liegt 100 ms nach der Marke - die Marke wandert genau dorthin."""
    mark_ms = 10_000
    # Fensterbeginn ist mark - 250 - 20; die Stelle i misst mittig um
    # fetch_start + i*10 + 20, also ist Stelle i die Marke + (i*10 - 250) ms.
    dip_at_index = (SEARCH_WINDOW_MS + 100) // STEP_MS
    runner = _runner_for(_flat_with_dip(dip_at_index))

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert snap.original_ms == mark_ms
    assert snap.shift_ms == 100
    assert snap.corrected_ms == mark_ms + 100
    assert snap.level_db == pytest.approx(-60.0)
    assert snap.window_mean_db > snap.level_db
    assert snap.depth_db == pytest.approx(snap.window_mean_db - snap.level_db)


def test_gleichstand_gewinnt_die_fruehere_stelle() -> None:
    """Zwei gleich leise Senken: die FRUEHERE gewinnt - bewusste Korrektur nach vorn."""
    mark_ms = 10_000
    levels = [-20.0] * BLOCK_COUNT
    early = (SEARCH_WINDOW_MS - 150) // STEP_MS
    late = (SEARCH_WINDOW_MS + 150) // STEP_MS
    for start in (early, late):
        for offset in range(_BLOCKS_PER_MEASURE):
            levels[start + offset] = -60.0

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.shift_ms == -150


def test_knapp_lauteres_minimum_verliert_gegen_die_fruehere_stelle() -> None:
    """Die spaetere Stelle ist minimal leiser, liegt aber im Gleichstandsband."""
    mark_ms = 10_000
    levels = [-20.0] * BLOCK_COUNT
    early = (SEARCH_WINDOW_MS - 150) // STEP_MS
    late = (SEARCH_WINDOW_MS + 150) // STEP_MS
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[early + offset] = -60.0
        levels[late + offset] = -60.0 - TIE_TOLERANCE_DB / 2

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.shift_ms == -150, "innerhalb von 0,5 dB gewinnt die fruehere Stelle"


def test_deutlich_leiseres_spaeteres_minimum_gewinnt() -> None:
    """Ausserhalb des Gleichstandsbands entscheidet der Pegel, nicht die Lage."""
    mark_ms = 10_000
    levels = [-20.0] * BLOCK_COUNT
    early = (SEARCH_WINDOW_MS - 150) // STEP_MS
    late = (SEARCH_WINDOW_MS + 150) // STEP_MS
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[early + offset] = -60.0
        levels[late + offset] = -75.0

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.shift_ms == 150


def test_suche_bleibt_im_fenster() -> None:
    """Auch bei durchgehend gleichem Pegel bleibt die Verschiebung im Fenster."""
    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, 10_000, ffmpeg_path=FFMPEG, process_runner=_runner_for([-30.0] * BLOCK_COUNT)
    )

    assert abs(snap.shift_ms) <= SEARCH_WINDOW_MS
    assert snap.shift_ms == -SEARCH_WINDOW_MS, "bei Gleichstand die frueheste Stelle"


def test_kein_absoluter_schwellwert_nur_das_minimum_im_fenster() -> None:
    """Ein durchweg lautes Fenster liefert trotzdem eine Stelle - relativ, nicht absolut."""
    mark_ms = 10_000
    dip_at_index = (SEARCH_WINDOW_MS + 50) // STEP_MS
    # Alles sehr laut, die Senke nur 3 dB darunter - kein "still" im absoluten Sinn.
    snap = verschiebe_auf_leiseste_stelle(
        MEDIA,
        mark_ms,
        ffmpeg_path=FFMPEG,
        process_runner=_runner_for(_flat_with_dip(dip_at_index, loud=-12.0, quiet=-15.0)),
    )

    assert snap.shift_ms == 50
    assert snap.depth_db < 3.5, "flache Senke: die Tiefe meldet, dass keine Pause im Fenster liegt"


def test_nur_rueckwaerts_findet_leisere_stelle_davor() -> None:
    """Auftrag shorts-pegelschnitt-richtung: bei ``nur_rueckwaerts`` nur nach vorn suchen."""
    mark_ms = 10_000
    block_count = SEARCH_WINDOW_START_MS // STEP_MS + _BLOCKS_PER_MEASURE
    levels = [-20.0] * block_count
    dip_at_index = (SEARCH_WINDOW_START_MS - 100) // STEP_MS
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[dip_at_index + offset] = -60.0

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        return ProcessResult(0, _stdout_for(levels))

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, nur_rueckwaerts=True, process_runner=runner
    )

    assert snap.shift_ms == -100


def test_nur_rueckwaerts_geht_nie_ueber_die_marke_hinaus() -> None:
    """Auch wenn danach die leisere Stelle liegt, bleibt die Suche vor der Marke."""
    mark_ms = 10_000
    block_count = SEARCH_WINDOW_START_MS // STEP_MS + _BLOCKS_PER_MEASURE
    # Die einzige leise Stelle liegt am allerletzten Block - direkt an der Marke,
    # nicht dahinter, weil das Fenster bei nur_rueckwaerts dort endet.
    levels = [-20.0] * block_count
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[-1 - offset] = -60.0

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        assert float(arguments[arguments.index("-t") + 1]) == pytest.approx(
            (SEARCH_WINDOW_START_MS + MEASURE_MS) / 1000
        )
        return ProcessResult(0, _stdout_for(levels))

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, nur_rueckwaerts=True, process_runner=runner
    )

    assert snap.shift_ms <= 0
    assert snap.corrected_ms <= mark_ms


def test_nur_rueckwaerts_mit_explizitem_fenster() -> None:
    """``search_window_start_ms`` ueberschreibt die Vorgabe - fuer den Pruefstein."""
    mark_ms = 10_000
    fenster_ms = 100
    block_count = fenster_ms // STEP_MS + _BLOCKS_PER_MEASURE
    levels = [-20.0] * block_count
    for offset in range(_BLOCKS_PER_MEASURE):
        levels[offset] = -60.0

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        assert float(arguments[arguments.index("-t") + 1]) == pytest.approx(
            (fenster_ms + MEASURE_MS) / 1000
        )
        return ProcessResult(0, _stdout_for(levels))

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA,
        mark_ms,
        ffmpeg_path=FFMPEG,
        nur_rueckwaerts=True,
        search_window_start_ms=fenster_ms,
        process_runner=runner,
    )

    assert snap.shift_ms == -fenster_ms


def test_marke_am_dateianfang_sucht_ab_null() -> None:
    """Nahe 0 ms wird nicht ins Negative gesucht, sondern ab Dateianfang."""
    runner = _runner_for([-30.0] * BLOCK_COUNT)

    snap = verschiebe_auf_leiseste_stelle(MEDIA, 50, ffmpeg_path=FFMPEG, process_runner=runner)

    assert snap.corrected_ms >= 0
    seek = runner.calls[0][runner.calls[0].index("-ss") + 1]  # type: ignore[attr-defined]
    assert float(seek) == 0.0


def test_ffmpeg_fehler_haelt_an_ohne_rueckfall() -> None:
    """Fail closed: kein stiller Rueckfall auf die unkorrigierte Marke."""
    with pytest.raises(LevelCutFailed) as excinfo:
        verschiebe_auf_leiseste_stelle(
            MEDIA,
            10_000,
            ffmpeg_path=FFMPEG,
            process_runner=_runner_for([-30.0] * BLOCK_COUNT, exit_code=1),
        )

    assert excinfo.value.code == "ffmpeg_fehlgeschlagen"


def test_zu_wenige_bloecke_halten_an() -> None:
    """Kein messbarer Ton (zu kurzes Fenster) ist ein Fehler, kein Rueckfall."""
    with pytest.raises(LevelCutFailed) as excinfo:
        verschiebe_auf_leiseste_stelle(
            MEDIA,
            10_000,
            ffmpeg_path=FFMPEG,
            process_runner=_runner_for([-30.0] * (_BLOCKS_PER_MEASURE - 1)),
        )

    assert excinfo.value.code == "keine_messung"


def test_stumme_tonspur_haelt_an() -> None:
    """Durchgehend digitale Stille: eigener Fehlercode, keine willkuerliche Stelle."""

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        lines = [f"{_ASTATS_KEY}=-inf"] * BLOCK_COUNT
        return ProcessResult(0, "\n".join(lines) + "\n")

    with pytest.raises(LevelCutFailed) as excinfo:
        verschiebe_auf_leiseste_stelle(MEDIA, 10_000, ffmpeg_path=FFMPEG, process_runner=runner)

    assert excinfo.value.code == "kein_ton"


def test_negative_marke_haelt_an() -> None:
    with pytest.raises(LevelCutFailed) as excinfo:
        verschiebe_auf_leiseste_stelle(
            MEDIA, -1, ffmpeg_path=FFMPEG, process_runner=_runner_for([])
        )

    assert excinfo.value.code == "marke_negativ"


def test_ffmpeg_aufruf_nutzt_sprachband_und_ein_fenster() -> None:
    """EIN Aufruf je Fenster, gefiltert auf das Sprachband, 10-ms-Bloecke."""
    runner = _runner_for([-30.0] * BLOCK_COUNT)

    verschiebe_auf_leiseste_stelle(MEDIA, 10_000, ffmpeg_path=FFMPEG, process_runner=runner)

    assert len(runner.calls) == 1, "ein Fenster = ein ffmpeg-Aufruf"  # type: ignore[attr-defined]
    arguments = runner.calls[0]  # type: ignore[attr-defined]
    chain = arguments[arguments.index("-af") + 1]
    assert "highpass=f=200" in chain
    assert "lowpass=f=3400" in chain
    assert "asetnsamples=n=480" in chain
    assert "astats=metadata=1:reset=1" in chain
    duration = float(arguments[arguments.index("-t") + 1])
    assert duration == pytest.approx((2 * SEARCH_WINDOW_MS + MEASURE_MS) / 1000)


def test_bloecke_werden_verlustfrei_zu_messfenstern_kombiniert() -> None:
    """Vier gleiche 10-ms-Bloecke ergeben denselben Wert als 40-ms-Fenster.

    Leistungen addieren sich - deshalb ist die Kombination exakt und nicht
    genaehert. Der Beleg gegen ``volumedetect`` steht im Bericht.
    """
    combined = _combine_to_measure_window([-30.0] * 8)

    assert len(combined) == 5
    assert all(value == pytest.approx(-30.0) for value in combined)


def test_kombination_mittelt_leistung_nicht_dezibel() -> None:
    """Drei stille und ein lauter Block: das Leistungsmittel liegt 6 dB unter laut."""
    combined = _combine_to_measure_window([-math.inf, -math.inf, -math.inf, 0.0])

    assert combined[0] == pytest.approx(10 * math.log10(0.25))


# ---------------------------------------------------------------------------
# Auftrag shorts-pegelmedian: die Mitte des leisen BEREICHS statt des tiefsten
# EINZELNEN Punktes.
# ---------------------------------------------------------------------------


def _flat_with_quiet_region(
    region_start_index: int, region_length_ms: int, *, loud: float = -20.0, quiet: float = -60.0
) -> list[float]:
    """Fenster mit EINEM leisen Bereich, der ``region_length_ms`` Messstellen breit ist.

    Eine Messstelle mittelt ueber :data:`_BLOCKS_PER_MEASURE` Bloecke; damit
    ``n`` Messstellen VOLLSTAENDIG leise sind, muessen ``n + 3`` Bloecke leise
    sein.
    """
    levels = [loud] * BLOCK_COUNT
    stellen = region_length_ms // STEP_MS
    for offset in range(stellen + _BLOCKS_PER_MEASURE - 1):
        levels[region_start_index + offset] = quiet
    return levels


def test_bereichsmitte_statt_tiefstem_punkt() -> None:
    """Ein 130 ms langer leiser Bereich: die Marke landet in seiner MITTE."""
    mark_ms = 10_000
    region_start = (SEARCH_WINDOW_MS + 100) // STEP_MS

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA,
        mark_ms,
        ffmpeg_path=FFMPEG,
        process_runner=_runner_for(_flat_with_quiet_region(region_start, 130)),
    )

    assert snap.verfahren == VERFAHREN_BEREICHSMITTE
    assert snap.quiet_region_ms == 130
    # Bereich reicht von +100 bis +220 ms, Mitte bei +160 ms.
    assert snap.shift_ms == 160


def test_kurze_lautluecke_verliert_gegen_die_laengere_sprechpause() -> None:
    """Der Befund des Nutzers: die tiefste Stelle ist eine Luecke IM Wort.

    Die 30 ms kurze Senke reicht 20 dB tiefer als die 130 ms lange Pause - und
    verliert trotzdem, weil sie zu kurz ist, um eine Sprechpause zu sein.
    """
    mark_ms = 10_000
    levels = _flat_with_quiet_region((SEARCH_WINDOW_MS + 100) // STEP_MS, 130, quiet=-45.0)
    luecke = (SEARCH_WINDOW_MS - 150) // STEP_MS
    for offset in range(30 // STEP_MS + _BLOCKS_PER_MEASURE - 1):
        levels[luecke + offset] = -80.0

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.verfahren == VERFAHREN_BEREICHSMITTE
    assert snap.quiet_region_ms == 130
    assert snap.shift_ms == 160
    assert snap.level_db == pytest.approx(-45.0)


def test_zu_kurzer_bereich_faellt_auf_den_tiefsten_punkt_zurueck() -> None:
    """Nichts im Fenster ist lang genug: Rueckfall, und er wird ausgewiesen."""
    mark_ms = 10_000
    dip_at_index = (SEARCH_WINDOW_MS + 100) // STEP_MS

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(_flat_with_dip(dip_at_index))
    )

    assert snap.verfahren == VERFAHREN_TIEFSTER_PUNKT
    assert snap.quiet_region_ms == 0
    assert snap.shift_ms == 100


def test_bereich_genau_an_der_mindestlaenge_zaehlt_noch() -> None:
    """Die Grenze ist einschliessend: genau MIN_PAUSE_MS reichen."""
    mark_ms = 10_000
    region_start = (SEARCH_WINDOW_MS + 100) // STEP_MS

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA,
        mark_ms,
        ffmpeg_path=FFMPEG,
        process_runner=_runner_for(_flat_with_quiet_region(region_start, MIN_PAUSE_MS)),
    )

    assert snap.verfahren == VERFAHREN_BEREICHSMITTE
    assert snap.quiet_region_ms == MIN_PAUSE_MS


def test_bei_gleich_langen_bereichen_gewinnt_der_leisere() -> None:
    """Gleiche Laenge, verschiedene Tiefe: der leisere Bereich entscheidet."""
    mark_ms = 10_000
    levels = _flat_with_quiet_region((SEARCH_WINDOW_MS - 250) // STEP_MS, 130, quiet=-45.0)
    spaet = (SEARCH_WINDOW_MS + 100) // STEP_MS
    for offset in range(130 // STEP_MS + _BLOCKS_PER_MEASURE - 1):
        levels[spaet + offset] = -60.0

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.shift_ms == 160, "der leisere Bereich gewinnt, obwohl er spaeter liegt"
    assert snap.level_db == pytest.approx(-60.0)


def test_bei_gleich_langen_und_gleich_leisen_bereichen_gewinnt_der_fruehere() -> None:
    """Die Gleichstandsregel gilt sinngemaess auch fuer Bereiche."""
    mark_ms = 10_000
    levels = _flat_with_quiet_region((SEARCH_WINDOW_MS - 250) // STEP_MS, 130)
    spaet = (SEARCH_WINDOW_MS + 100) // STEP_MS
    for offset in range(130 // STEP_MS + _BLOCKS_PER_MEASURE - 1):
        levels[spaet + offset] = -60.0

    snap = verschiebe_auf_leiseste_stelle(
        MEDIA, mark_ms, ffmpeg_path=FFMPEG, process_runner=_runner_for(levels)
    )

    assert snap.shift_ms == -190, "der fruehere Bereich gewinnt: Mitte von -250 bis -130 ms"


# ---------------------------------------------------------------------------
# Auftrag shorts-stillevorlauf: lange Stille VOR dem ersten Ton verschiebt die
# Startmarke - unabhaengig von der (viel engeren) Pegelmessung oben.
# ---------------------------------------------------------------------------


def _stille_runner(
    *,
    speech_level_db: float,
    mark_ms: int,
    candidate_end_ms: int,
    search_levels: list[float],
):
    """Ein ``process_runner``, der Sprechpegel- und Vorwaertsmessung getrennt beliefert.

    :func:`finde_stillevorlauf` ruft ffmpeg zuerst fuer den Sprechpegel (ueber
    die ganze Kandidatenspanne) und danach fuer die Vorwaertssuche auf - genau
    diese Reihenfolge bedient dieser Runner ueber die Aufrufzaehlung.
    """
    speech_block_count = (candidate_end_ms - mark_ms) // STEP_MS
    speech_levels = [speech_level_db] * speech_block_count
    calls: list[list[str]] = []

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        calls.append(list(arguments))
        levels = speech_levels if len(calls) == 1 else search_levels
        return ProcessResult(0, _stdout_for(levels))

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _search_block_count() -> int:
    return VORLAUF_SUCHE_MAX_MS // STEP_MS + _BLOCKS_PER_MEASURE


def test_lange_stille_ab_der_marke_verschiebt_die_marke() -> None:
    """Belegt am Befund: 1900 ms echte Stille ab der Marke (Kandidat 31)."""
    mark_ms = 706_910
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -14.8
    silence_db = -39.9
    stille_ms = 1900
    search_levels = [silence_db] * _search_block_count()
    stille_bloecke = stille_ms // STEP_MS + _BLOCKS_PER_MEASURE - 1
    for offset in range(stille_bloecke, len(search_levels)):
        search_levels[offset] = speech_level_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.verschoben is True
    assert ergebnis.stille_laenge_ms == stille_ms
    assert ergebnis.shift_ms == stille_ms - VORLAUF_REST_MS
    assert ergebnis.corrected_ms == mark_ms + stille_ms - VORLAUF_REST_MS
    assert ergebnis.sprechpegel_db == pytest.approx(speech_level_db)


def test_gehaltener_laut_ist_keine_stille_und_stoppt_die_suche() -> None:
    """Belegt am Befund: der 400 ms gehaltene Laut bei Kandidat 5 bleibt im Short.

    600 ms Stille, dann ein Laut nur 4,4 dB unter Sprechpegel - klar innerhalb
    von :data:`STILLE_ABSTAND_DB` und damit KEINE Stille. Die Suche muss davor
    stoppen, nicht dahinter."""
    mark_ms = 201_280
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -14.8
    laut_db = speech_level_db - (STILLE_ABSTAND_DB - 15.6)  # 4,4 dB unter Sprechpegel
    stille_ms = 600
    search_levels = [-39.7] * _search_block_count()
    stille_bloecke = stille_ms // STEP_MS + _BLOCKS_PER_MEASURE - 1
    for offset in range(stille_bloecke, len(search_levels)):
        search_levels[offset] = laut_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.verschoben is True
    assert ergebnis.stille_laenge_ms == stille_ms
    assert ergebnis.corrected_ms == mark_ms + stille_ms - VORLAUF_REST_MS
    assert ergebnis.corrected_ms < mark_ms + stille_ms, "die Marke landet VOR dem Laut"


def test_kurzer_vorlauf_bleibt_unveraendert() -> None:
    """150 ms Stille (Kandidat 14) liegt unter :data:`VORLAUF_MAX_MS` - keine Verschiebung."""
    mark_ms = 360_420
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.7
    stille_ms = 150
    search_levels = [speech_level_db] * _search_block_count()
    stille_bloecke = stille_ms // STEP_MS + _BLOCKS_PER_MEASURE - 1
    for offset in range(stille_bloecke):
        search_levels[offset] = -33.4

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.verschoben is False
    assert ergebnis.corrected_ms == mark_ms
    assert ergebnis.shift_ms == 0


def test_stille_genau_an_der_schwelle_verschiebt_nicht() -> None:
    """Die Schwelle ist ausschliessend: genau ``VORLAUF_MAX_MS`` reicht NICHT."""
    mark_ms = 10_000
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.0
    search_levels = [speech_level_db] * _search_block_count()
    stille_bloecke = VORLAUF_MAX_MS // STEP_MS + _BLOCKS_PER_MEASURE - 1
    for offset in range(stille_bloecke):
        search_levels[offset] = -60.0

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.stille_laenge_ms == VORLAUF_MAX_MS
    assert ergebnis.verschoben is False


def test_durchgehende_stille_wird_bei_der_suchweite_gedeckelt() -> None:
    """Ist das ganze Suchfenster still, wandert die Marke hoechstens VORLAUF_SUCHE_MAX_MS weit."""
    mark_ms = 10_000
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.0
    search_levels = [-60.0] * _search_block_count()

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.stille_laenge_ms == VORLAUF_SUCHE_MAX_MS
    assert ergebnis.shift_ms == VORLAUF_SUCHE_MAX_MS - VORLAUF_REST_MS


# ---------------------------------------------------------------------------
# Auftrag shorts-stillevorlauf-toleranz: kurze Unterbrechungen (Nachhall,
# Musikakzente) beenden den Stillebereich nicht, solange sie hoechstens
# STILLE_UNTERBRECHUNG_MAX_MS lang sind.
# ---------------------------------------------------------------------------


def test_kurze_unterbrechung_wird_ueberbrueckt() -> None:
    """Eine kurze Unterbrechung (Nachhall) wird ueberbrueckt, der Bereich laeuft weiter -
    eine spaetere, laengere Unterbrechung (150 ms roher Block, > STILLE_UNTERBRECHUNG_MAX_MS)
    beendet ihn danach wie gewohnt."""
    mark_ms = 10_000
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.0
    search_levels = [-60.0] * _search_block_count()
    for offset in range(20, 23):  # 200-230 ms: kurze Unterbrechung
        search_levels[offset] = speech_level_db
    for offset in range(60, 75):  # ab 600 ms: 150 ms Unterbrechung - beendet den Bereich
        search_levels[offset] = speech_level_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    # 570 statt 600: die 40-ms-Fenstermessung blendet ein paar Millisekunden der
    # Unterbrechung schon in benachbarte Messstellen ein (siehe MEASURE_MS) - die
    # ueberbrueckte Unterbrechung selbst bleibt trotzdem klar unter der Toleranz.
    assert ergebnis.stille_laenge_ms == 570
    assert ergebnis.verschoben is True
    assert ergebnis.shift_ms == 570 - VORLAUF_REST_MS
    assert ergebnis.unterbrechungen_anzahl == 1
    assert ergebnis.laengste_unterbrechung_ms < STILLE_UNTERBRECHUNG_MAX_MS


def test_unterbrechung_ueber_der_toleranz_beendet_den_bereich_sofort() -> None:
    """Eine Unterbrechung laenger als STILLE_UNTERBRECHUNG_MAX_MS zaehlt nicht mehr als Stille."""
    mark_ms = 10_000
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.0
    search_levels = [-60.0] * _search_block_count()
    unterbrechung_bloecke = STILLE_UNTERBRECHUNG_MAX_MS // STEP_MS + 1
    for offset in range(10, 10 + unterbrechung_bloecke):
        search_levels[offset] = speech_level_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.stille_laenge_ms < VORLAUF_MAX_MS
    assert ergebnis.verschoben is False
    assert ergebnis.unterbrechungen_anzahl == 0, "die zu lange Unterbrechung wird NICHT gezaehlt"
    assert ergebnis.laengste_unterbrechung_ms == 0


def test_gehaltener_laut_ueberschreitet_die_toleranz_und_wird_nicht_ueberbrueckt() -> None:
    """Belegt am Befund: der 400 ms gehaltene Laut bei Kandidat 5 ist dreimal so lang
    wie die Toleranz und muss den Bereich beenden, nicht ueberbrueckt werden."""
    mark_ms = 201_280
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -14.8
    laut_db = speech_level_db - 4.4  # 4,4 dB unter Sprechpegel, wie im Befund
    search_levels = [-39.7] * _search_block_count()
    for offset in range(60, 100):  # 600-1000 ms: der 400 ms gehaltene Laut
        search_levels[offset] = laut_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.verschoben is True
    assert ergebnis.corrected_ms == mark_ms + ergebnis.stille_laenge_ms - VORLAUF_REST_MS
    assert ergebnis.corrected_ms < mark_ms + 600, "die Marke landet VOR dem Laut, nicht dahinter"
    assert ergebnis.unterbrechungen_anzahl == 0, "der Laut wird NICHT ueberbrueckt"


def test_unterbrechung_direkt_an_der_marke_wird_ueberbrueckt() -> None:
    """Belegt am Befund: der kurze Ausklang direkt an der Marke (Kandidat 31, ~30 ms)
    darf die Messung nicht mehr auf 0 ms zuruecksetzen."""
    mark_ms = 706_910
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -14.78
    search_levels = [-60.0] * _search_block_count()
    for offset in range(0, 3):  # 0-30 ms: Ausklang direkt an der Marke
        search_levels[offset] = speech_level_db
    for offset in range(190, 210):  # ab 1900 ms: der eigentliche Satzbeginn
        search_levels[offset] = speech_level_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.verschoben is True
    assert ergebnis.stille_laenge_ms > 1800, (
        "die Unterbrechung an der Marke darf nicht auf 0 zuruecksetzen"
    )
    assert ergebnis.shift_ms == ergebnis.stille_laenge_ms - VORLAUF_REST_MS
    assert ergebnis.unterbrechungen_anzahl == 1
    assert ergebnis.laengste_unterbrechung_ms < STILLE_UNTERBRECHUNG_MAX_MS


def test_mehrere_unterbrechungen_werden_gezaehlt() -> None:
    """Mehrere kurze Unterbrechungen werden alle ueberbrueckt und gezaehlt."""
    mark_ms = 10_000
    candidate_end_ms = mark_ms + 20_000
    speech_level_db = -15.0
    search_levels = [-60.0] * _search_block_count()
    for start, laenge in ((10, 2), (30, 5), (55, 4)):  # 20, 50, 40 ms
        for offset in range(start, start + laenge):
            search_levels[offset] = speech_level_db
    for offset in range(80, 95):  # ab 800 ms: 150 ms - beendet den Bereich
        search_levels[offset] = speech_level_db

    runner = _stille_runner(
        speech_level_db=speech_level_db,
        mark_ms=mark_ms,
        candidate_end_ms=candidate_end_ms,
        search_levels=search_levels,
    )

    ergebnis = finde_stillevorlauf(
        MEDIA, mark_ms, candidate_end_ms, ffmpeg_path=FFMPEG, process_runner=runner
    )

    assert ergebnis.unterbrechungen_anzahl == 3
    assert ergebnis.laengste_unterbrechung_ms < STILLE_UNTERBRECHUNG_MAX_MS
    assert ergebnis.stille_laenge_ms < 800, (
        "die abschliessende 150-ms-Unterbrechung beendet den Bereich"
    )


def test_stillevorlauf_negative_marke_haelt_an() -> None:
    with pytest.raises(LevelCutFailed) as excinfo:
        finde_stillevorlauf(
            MEDIA, -1, 20_000, ffmpeg_path=FFMPEG, process_runner=_runner_for([])
        )

    assert excinfo.value.code == "marke_negativ"


def test_stillevorlauf_ungueltige_spanne_haelt_an() -> None:
    with pytest.raises(LevelCutFailed) as excinfo:
        finde_stillevorlauf(
            MEDIA, 10_000, 10_000, ffmpeg_path=FFMPEG, process_runner=_runner_for([])
        )

    assert excinfo.value.code == "kandidatenspanne_ungueltig"


def test_stillevorlauf_stumme_spanne_haelt_an() -> None:
    """Ist der ganze Kandidat stumm, gibt es keinen Sprechpegel - fail closed."""

    def runner(arguments, timeout_seconds):  # type: ignore[no-untyped-def]
        block_count = 20_000 // STEP_MS
        lines = [f"{_ASTATS_KEY}=-inf"] * block_count
        return ProcessResult(0, "\n".join(lines) + "\n")

    with pytest.raises(LevelCutFailed) as excinfo:
        finde_stillevorlauf(MEDIA, 10_000, 30_000, ffmpeg_path=FFMPEG, process_runner=runner)

    assert excinfo.value.code == "kein_ton"
