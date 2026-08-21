"""Tests fuer Teil A des Auftrags shorts-stufe-3a-frames: das zentrale Frameraster.

Belegte Zielwerte aus dem Auftrag (fps = 60):
  125917 ms -> 7555      140250 ms -> 8415      Framezahl 860
  649017 ms -> 38941     723617 ms -> 43417     Framezahl 4476
"""

from __future__ import annotations

import pytest

from matrix_auto_cutter.shorts.frame_map import (
    KeepSegment,
    candidate_frame_span,
    candidate_outside_windows,
    keep_segments_from_intervals,
    map_rendered_frame,
    map_source_frame,
    map_source_interval_to_rendered,
    ms_to_frame,
)


def test_ms_to_frame_matches_belegte_zielwerte() -> None:
    assert ms_to_frame(125917, 60) == 7555
    assert ms_to_frame(140250, 60) == 8415
    assert ms_to_frame(649017, 60) == 38941
    assert ms_to_frame(723617, 60) == 43417


def test_candidate_frame_span_kuerzester_kandidat() -> None:
    assert candidate_frame_span(125917, 140250, 60) == (7555, 8415)


def test_candidate_frame_span_laengster_kandidat() -> None:
    assert candidate_frame_span(649017, 723617, 60) == (38941, 43417)


def test_candidate_frame_span_end_frame_is_exclusive_frame_count() -> None:
    start_frame, end_frame = candidate_frame_span(125917, 140250, 60)
    assert end_frame - start_frame == 860
    start_frame, end_frame = candidate_frame_span(649017, 723617, 60)
    assert end_frame - start_frame == 4476


def test_candidate_frame_span_rejects_non_positive_span() -> None:
    with pytest.raises(ValueError):
        candidate_frame_span(1000, 1008, 60)


# --- map_source_interval_to_rendered: Auftrag shorts-szenenfilter, Teil B --------
#
# Belegte Keep-Segmente aus dem realen Proposal proposal-3f7b9528b037b6e0c3e2e537d740eeb6
# zur Aufnahme "2026-08-17 20-47-23" (source_frame_count=4365, sieben Schnitte:
# (0,404) (919,1081) (1178,1316) (1360,1410) (2079,2144) (3025,3076) (4211,4365)).
# Handnachgerechnet in artefakte/repeat/shorts-szenenfilter/BERICHT-2026-08-17.md.

_REAL_KEEP_SEGMENTS = keep_segments_from_intervals(
    [
        (0, 404),
        (919, 1081),
        (1178, 1316),
        (1360, 1410),
        (2079, 2144),
        (3025, 3076),
        (4211, 4365),
    ],
    4365,
)


def test_real_keep_segments_match_the_proposal_complement() -> None:
    expected = (
        KeepSegment(404, 919),
        KeepSegment(1081, 1178),
        KeepSegment(1316, 1360),
        KeepSegment(1410, 2079),
        KeepSegment(2144, 3025),
        KeepSegment(3076, 4211),
    )
    assert expected == _REAL_KEEP_SEGMENTS


def test_map_source_interval_to_rendered_charts_window_from_pruefstein() -> None:
    """Charts-Fenster (1270, 3295) aus dem Journal, handnachgerechnet gegen die Keep-Segmente."""
    rendered = map_source_interval_to_rendered(_REAL_KEEP_SEGMENTS, [(1270, 3295)])
    assert rendered == ((612, 2425),)


def test_map_source_interval_to_rendered_window_fully_inside_a_cut_vanishes() -> None:
    """Ein Fenster, das komplett in einem Schnitt liegt, hat kein gerendertes Gegenstueck."""
    rendered = map_source_interval_to_rendered(_REAL_KEEP_SEGMENTS, [(1178, 1316)])
    assert rendered == ()


def test_map_source_interval_to_rendered_keeps_disjoint_windows_separate() -> None:
    rendered = map_source_interval_to_rendered(
        _REAL_KEEP_SEGMENTS, [(404, 919), (3076, 4211)]
    )
    assert rendered == ((0, 515), (515 + 97 + 44 + 669 + 881, 515 + 97 + 44 + 669 + 881 + 1135))


# --- candidate_outside_windows ----------------------------------------------------


def test_candidate_outside_windows_true_when_no_overlap() -> None:
    assert candidate_outside_windows((0, 100), [(200, 300)]) is True


def test_candidate_outside_windows_false_on_full_overlap() -> None:
    assert candidate_outside_windows((220, 260), [(200, 300)]) is False


def test_candidate_outside_windows_false_on_partial_overlap() -> None:
    assert candidate_outside_windows((250, 350), [(200, 300)]) is False


def test_candidate_outside_windows_true_when_touching_but_not_overlapping() -> None:
    """Halboffene Intervalle: eine Beruehrung am Rand ist keine Ueberschneidung."""
    assert candidate_outside_windows((100, 200), [(200, 300)]) is True


def test_candidate_outside_windows_true_with_no_windows_at_all() -> None:
    assert candidate_outside_windows((0, 100), []) is True


# --- map_rendered_frame: der Rueckweg, Auftrag shorts-stufe-3b-modul Teil 1 --------
#
# Der Rundlauf gegen die ECHTEN Keep-Segmente der Aufnahme 2026-08-19 17-26-15
# (20 Schnitte, source_frame_count 48574, Keep-Summe 46543) steht im Bericht
# artefakte/repeat/shorts-stufe-3b-modul/BERICHT-2026-08-21.md: 46543 von 46543,
# null Abweichungen. Hier laeuft derselbe Rundlauf gegen die oben schon
# belegten Segmente der Aufnahme 2026-08-17 20-47-23 - synthetisch ist daran
# nichts, nur kleiner.


def test_map_rendered_frame_ist_die_umkehrung_von_map_source_frame() -> None:
    gesamt = sum(segment.length for segment in _REAL_KEEP_SEGMENTS)
    rundlauf = 0
    for rendered_frame in range(gesamt):
        source_frame = map_rendered_frame(_REAL_KEEP_SEGMENTS, rendered_frame)
        assert source_frame is not None
        if map_source_frame(_REAL_KEEP_SEGMENTS, source_frame) == rendered_frame:
            rundlauf += 1
    assert rundlauf == gesamt


def test_map_rendered_frame_trifft_das_erste_frame_jedes_segments() -> None:
    rendered = 0
    for segment in _REAL_KEEP_SEGMENTS:
        assert map_rendered_frame(_REAL_KEEP_SEGMENTS, rendered) == segment.start_frame
        rendered += segment.length


def test_map_rendered_frame_ohne_gegenstueck_ist_none() -> None:
    """Ab der Gesamtlaenge gibt es kein Quellframe mehr - kein Klemmen auf den Rand."""
    gesamt = sum(segment.length for segment in _REAL_KEEP_SEGMENTS)
    assert map_rendered_frame(_REAL_KEEP_SEGMENTS, gesamt) is None
    assert map_rendered_frame(_REAL_KEEP_SEGMENTS, gesamt + 5000) is None


def test_map_rendered_frame_verwirft_negative_frames_wie_seine_nachbarn() -> None:
    with pytest.raises(ValueError):
        map_rendered_frame(_REAL_KEEP_SEGMENTS, -1)


def test_map_rendered_frame_ueberspringt_den_schnitt() -> None:
    """Das erste geschnittene Frame taucht auf der gerenderten Achse nicht auf."""
    segments = keep_segments_from_intervals([(10, 20)], 30)
    assert map_rendered_frame(segments, 9) == 9
    assert map_rendered_frame(segments, 10) == 20
    assert map_source_frame(segments, 15) is None
