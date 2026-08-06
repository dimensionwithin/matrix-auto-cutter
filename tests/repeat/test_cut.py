"""Tests for the pure cut-plan calculation in cut.py. No process execution involved."""

from __future__ import annotations

import pytest

from matrix_auto_cutter.repeat.cut import (
    CutIntegrityError,
    EmptyResultError,
    KeptSegment,
    compute_cut_plan,
    resolve_schnitt,
)


def _urteil(
    *,
    urteil: str | None = "versprecher",
    schnitt: str | None = "__unset__",
    erste_start: int = 0,
    erste_end: int = 1_000,
    zweite_start: int = 1_000,
    zweite_end: int = 2_000,
) -> dict:
    entry = {
        "datei": "stem",
        "eintragsnummer": 1,
        "erste_passage": {"start_ms": erste_start, "end_ms": erste_end},
        "zweite_passage": {"start_ms": zweite_start, "end_ms": zweite_end},
        "scores": {"utterance": None, "boundary": 0.9},
        "detektoren": ["boundary"],
        "urteil": urteil,
        "notiz": "",
    }
    if schnitt != "__unset__":
        entry["schnitt"] = schnitt
    return entry


def test_resolve_schnitt_defaults_to_erste_when_field_missing() -> None:
    assert resolve_schnitt(_urteil(schnitt="__unset__")) == "erste"


def test_resolve_schnitt_null_for_non_versprecher() -> None:
    assert resolve_schnitt(_urteil(urteil="bewusst", schnitt=None)) is None
    assert resolve_schnitt(_urteil(urteil="unsinn", schnitt=None)) is None
    assert resolve_schnitt(_urteil(urteil=None, schnitt=None)) is None


def test_resolve_schnitt_passes_through_explicit_value() -> None:
    assert resolve_schnitt(_urteil(schnitt="zweite")) == "zweite"
    assert resolve_schnitt(_urteil(schnitt="beide")) == "beide"


def test_empty_urteile_list_keeps_whole_file() -> None:
    plan = compute_cut_plan([], 10_000)
    assert plan.kept_segments == (KeptSegment(0, 10_000),)
    assert plan.cut_count == 0
    assert plan.cut_count_before_merge == 0
    assert plan.removed_duration_ms == 0
    assert plan.duration_before_ms == 10_000
    assert plan.duration_after_ms == 10_000


def test_non_versprecher_entries_produce_no_cuts() -> None:
    urteile = [_urteil(urteil="bewusst", schnitt=None), _urteil(urteil="unsinn", schnitt=None)]
    plan = compute_cut_plan(urteile, 5_000)
    assert plan.kept_segments == (KeptSegment(0, 5_000),)
    assert plan.cut_count == 0


@pytest.mark.parametrize(
    ("schnitt", "expected_removed"),
    [
        ("erste", (0, 1_000)),
        ("zweite", (1_000, 2_000)),
        ("beide", (0, 2_000)),
    ],
)
def test_all_three_schnitt_values_remove_the_right_range(
    schnitt: str, expected_removed: tuple[int, int]
) -> None:
    plan = compute_cut_plan([_urteil(schnitt=schnitt)], 5_000)
    removed_start, removed_end = expected_removed
    expected_kept = []
    if removed_start > 0:
        expected_kept.append(KeptSegment(0, removed_start))
    if removed_end < 5_000:
        expected_kept.append(KeptSegment(removed_end, 5_000))
    assert plan.kept_segments == tuple(expected_kept)
    assert plan.removed_duration_ms == removed_end - removed_start


def test_cut_at_file_start() -> None:
    urteil = _urteil(schnitt="erste", erste_start=0, erste_end=500)
    plan = compute_cut_plan([urteil], 5_000)
    assert plan.kept_segments == (KeptSegment(500, 5_000),)
    assert plan.duration_after_ms == 4_500


def test_cut_at_file_end() -> None:
    urteil = _urteil(schnitt="zweite", zweite_start=4_500, zweite_end=5_000)
    plan = compute_cut_plan([urteil], 5_000)
    assert plan.kept_segments == (KeptSegment(0, 4_500),)
    assert plan.duration_after_ms == 4_500


def test_overlapping_removals_are_merged() -> None:
    a = _urteil(
        schnitt="beide", erste_start=1_000, erste_end=2_000, zweite_start=2_000, zweite_end=3_000
    )
    b = _urteil(
        schnitt="erste", erste_start=2_500, erste_end=2_800, zweite_start=9_000, zweite_end=9_500
    )
    plan = compute_cut_plan([a, b], 10_000)
    assert plan.cut_count_before_merge == 2
    assert plan.cut_count == 1
    assert plan.kept_segments == (KeptSegment(0, 1_000), KeptSegment(3_000, 10_000))


def test_adjacent_touching_removals_are_merged() -> None:
    a = _urteil(schnitt="erste", erste_start=1_000, erste_end=2_000)
    b = _urteil(schnitt="zweite", zweite_start=2_000, zweite_end=3_000)
    plan = compute_cut_plan([a, b], 10_000)
    assert plan.cut_count_before_merge == 2
    assert plan.cut_count == 1
    assert plan.kept_segments == (KeptSegment(0, 1_000), KeptSegment(3_000, 10_000))


def test_non_adjacent_removals_stay_separate() -> None:
    a = _urteil(schnitt="erste", erste_start=1_000, erste_end=2_000)
    b = _urteil(schnitt="zweite", zweite_start=2_500, zweite_end=3_000)
    plan = compute_cut_plan([a, b], 10_000)
    assert plan.cut_count == 2
    assert plan.kept_segments == (
        KeptSegment(0, 1_000),
        KeptSegment(2_000, 2_500),
        KeptSegment(3_000, 10_000),
    )


def test_file_that_would_be_entirely_removed_raises() -> None:
    urteil = _urteil(
        schnitt="beide", erste_start=0, erste_end=1_000, zweite_start=1_000, zweite_end=5_000
    )
    with pytest.raises(EmptyResultError):
        compute_cut_plan([urteil], 5_000)


def test_removal_beyond_declared_duration_raises_integrity_error() -> None:
    """A passage timestamp past ``duration_ms`` means the caller passed the wrong duration."""
    urteil = _urteil(schnitt="erste", erste_start=0, erste_end=6_000)
    with pytest.raises(CutIntegrityError) as exc_info:
        compute_cut_plan([urteil], 5_000)
    assert exc_info.value.total_ms == 5_000
    assert exc_info.value.kept_ms == 0
    assert exc_info.value.removed_ms == 6_000
