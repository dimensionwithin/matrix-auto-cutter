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
