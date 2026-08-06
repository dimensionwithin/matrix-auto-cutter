"""Pure computation of the kept-segment plan after removing "versprecher" passages.

No process execution here -- see ``cutcli.py`` for the ffmpeg-driving entry
point. This module only turns ``urteile.json`` plus a total duration into a
gap-free, overlap-free list of segments to keep.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from matrix_auto_cutter.repeat.errors import RepeatContractError

SchnittWert = Literal["erste", "zweite", "beide"]

_DEFAULT_SCHNITT: SchnittWert = "erste"


class CutIntegrityError(RepeatContractError):
    """Raised when kept duration + removed duration does not equal the total duration."""

    def __init__(self, total_ms: int, kept_ms: int, removed_ms: int) -> None:
        """Store the mismatching durations."""
        self.total_ms = total_ms
        self.kept_ms = kept_ms
        self.removed_ms = removed_ms
        super().__init__(
            f"Behalten ({kept_ms} ms) + entfernt ({removed_ms} ms) != Gesamtdauer ({total_ms} ms)"
        )


class EmptyResultError(RepeatContractError):
    """Raised when the computed cut plan would remove the entire file."""

    def __init__(self, total_ms: int) -> None:
        """Store the total duration that would be entirely cut away."""
        self.total_ms = total_ms
        super().__init__(f"Der gesamte Bereich (0 - {total_ms} ms) wuerde entfernt.")


@dataclass(frozen=True)
class _Removal:
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class KeptSegment:
    """One gap-free, overlap-free span of the source that survives the cut."""

    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class CutPlan:
    """The full result of computing a cut: what to keep, plus reporting figures."""

    kept_segments: tuple[KeptSegment, ...]
    cut_count_before_merge: int
    cut_count: int
    removed_duration_ms: int
    duration_before_ms: int
    duration_after_ms: int


def resolve_schnitt(urteil: dict) -> SchnittWert | None:
    """Resolve the effective ``schnitt`` value for one urteil entry.

    Non-"versprecher" entries never cut. Old files predating the "schnitt"
    field (or entries that simply omit it) default to "erste", matching the
    behaviour before this field existed.
    """
    if urteil.get("urteil") != "versprecher":
        return None
    value = urteil.get("schnitt")
    return _DEFAULT_SCHNITT if value is None else value


def _removal_for(urteil: dict) -> _Removal | None:
    schnitt = resolve_schnitt(urteil)
    if schnitt is None:
        return None
    erste = urteil["erste_passage"]
    zweite = urteil["zweite_passage"]
    if schnitt == "erste":
        return _Removal(erste["start_ms"], erste["end_ms"])
    if schnitt == "zweite":
        return _Removal(zweite["start_ms"], zweite["end_ms"])
    return _Removal(erste["start_ms"], zweite["end_ms"])


def _merge_removals(removals: list[_Removal]) -> list[_Removal]:
    """Sort and fuse overlapping or touching (adjacent) removal ranges."""
    if not removals:
        return []
    ordered = sorted(removals, key=lambda r: (r.start_ms, r.end_ms))
    merged = [ordered[0]]
    for removal in ordered[1:]:
        last = merged[-1]
        if removal.start_ms <= last.end_ms:
            merged[-1] = _Removal(last.start_ms, max(last.end_ms, removal.end_ms))
        else:
            merged.append(removal)
    return merged


def compute_cut_plan(urteile: list[dict], duration_ms: int) -> CutPlan:
    """Compute the kept-segment plan for ``urteile`` against a file of ``duration_ms``.

    Only "versprecher" entries contribute removals. Overlapping or adjacent
    removals are merged before the kept segments are derived, so two
    judgments sharing the same time range collapse into a single cut.
    """
    removals = [r for r in (_removal_for(u) for u in urteile) if r is not None]
    cut_count_before_merge = len(removals)
    merged = _merge_removals(removals)

    kept: list[KeptSegment] = []
    cursor = 0
    for removal in merged:
        if removal.start_ms > cursor:
            kept.append(KeptSegment(cursor, removal.start_ms))
        cursor = max(cursor, removal.end_ms)
    if cursor < duration_ms:
        kept.append(KeptSegment(cursor, duration_ms))

    removed_duration_ms = sum(r.end_ms - r.start_ms for r in merged)
    kept_duration_ms = sum(k.end_ms - k.start_ms for k in kept)

    if kept_duration_ms + removed_duration_ms != duration_ms:
        raise CutIntegrityError(duration_ms, kept_duration_ms, removed_duration_ms)
    if not kept:
        raise EmptyResultError(duration_ms)

    return CutPlan(
        kept_segments=tuple(kept),
        cut_count_before_merge=cut_count_before_merge,
        cut_count=len(merged),
        removed_duration_ms=removed_duration_ms,
        duration_before_ms=duration_ms,
        duration_after_ms=kept_duration_ms,
    )
