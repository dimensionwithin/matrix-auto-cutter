"""Protection-Resolver, Policyunion, Properties und atomare JSON-Ausgabe."""

from __future__ import annotations

import json
import os
from itertools import pairwise
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hypothesis import given
from hypothesis import strategies as st

from conftest import event, hard_protection, sidecar_dict, soft_protection
from matrix_auto_cutter.atomic import ProtectionRangesDocument, write_protection_ranges
from matrix_auto_cutter.errors import ErrorCode
from matrix_auto_cutter.models import (
    MaterializedFrameRange,
    ProtectionLevel,
    ProtectionPolicy,
)
from matrix_auto_cutter.protection import (
    is_local_audio_repair_blocked,
    materialize_protection,
    normalize_ranges,
)
from matrix_auto_cutter.sidecar import ObsEventSidecar
from matrix_auto_cutter.timebase import Frame, FrameRange

FIXED_ID = UUID("6ba7b814-9dad-4b8a-92fb-2a41f5468719")


def parse(raw: dict[str, Any]) -> ObsEventSidecar:
    return ObsEventSidecar.model_validate_json(json.dumps(raw))


def materialized(
    start: int,
    end: int,
    *,
    hard: bool,
    time: bool,
    overlays: bool,
    audio: bool,
) -> MaterializedFrameRange:
    return MaterializedFrameRange(
        protection_id="input",
        source_start_frame=start,
        source_end_frame=end,
        level=ProtectionLevel.HARD if hard else ProtectionLevel.SOFT,
        source_event_ids=(FIXED_ID,),
        uncertainty_padding_frames=0,
        policy=ProtectionPolicy(
            blocks_time_edits=time,
            blocks_overlays=overlays,
            blocks_local_audio_repair=audio,
            allows_global_mastering=True,
        ),
    )


def test_start_without_end_and_end_without_start_are_conservative() -> None:
    raw = sidecar_dict()
    pair_a = str(uuid4())
    pair_b = str(uuid4())
    raw["events"][1:1] = [
        event(str(uuid4()), "intro_started", 100, pair_id=pair_a, uncertainty_ms=0),
        event(str(uuid4()), "outro_ended", 500, pair_id=pair_b, uncertainty_ms=0),
    ]
    result = materialize_protection(parse(raw))
    assert result.status == "materialized"
    intro_ranges = [item for item in result.ranges if item.source_end_frame == 600]
    outro_ranges = [item for item in result.ranges if item.source_start_frame == 0]
    assert intro_ranges
    assert outro_ranges


def test_ambiguous_pair_rejected_without_exception() -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    raw["events"][1:1] = [
        event(str(uuid4()), "stinger_started", 100, pair_id=pair_id),
        event(str(uuid4()), "stinger_started", 120, pair_id=pair_id),
    ]
    result = materialize_protection(parse(raw))
    assert result.status == "rejected"
    assert result.errors[0].code == ErrorCode.SIDECAR_EVENT_PAIRS


def test_missing_pair_id_rejected_without_exception() -> None:
    raw = sidecar_dict()
    raw["events"].insert(1, event(str(uuid4()), "intro_started", 100))
    result = materialize_protection(parse(raw))
    assert result.status == "rejected"
    assert result.errors[0].code == ErrorCode.SIDECAR_EVENT_PAIRS


def test_uncertainty_plus_two_outward_and_clamp() -> None:
    raw = sidecar_dict()
    marker_id = str(uuid4())
    raw["events"].insert(
        1,
        event(
            marker_id,
            "manual_protection",
            1,
            protection=hard_protection(100, 100),
            uncertainty_ms=100,
            counter=1,
        ),
    )
    result = materialize_protection(parse(raw))
    containing = [item for item in result.ranges if UUID(marker_id) in item.source_event_ids]
    assert containing[0].source_start_frame == 0
    assert max(item.source_end_frame for item in containing) == 16
    assert max(item.uncertainty_padding_frames for item in containing) == 8
    assert all(item.source_start_frame < item.source_end_frame <= 600 for item in result.ranges)


def test_manual_point_and_interval_and_pair_buffers() -> None:
    raw = sidecar_dict()
    pair_id = str(uuid4())
    raw["events"][1:1] = [
        event(
            str(uuid4()),
            "intro_started",
            100,
            pair_id=pair_id,
            protection=hard_protection(250, 0),
            uncertainty_ms=0,
        ),
        event(
            str(uuid4()),
            "intro_ended",
            200,
            pair_id=pair_id,
            protection=hard_protection(0, 250),
            uncertainty_ms=0,
        ),
        event(
            str(uuid4()),
            "manual_protection",
            300,
            end_frame=320,
            protection=soft_protection(),
            uncertainty_ms=0,
            counter=300,
        ),
    ]
    result = materialize_protection(parse(raw))
    assert any(item.source_start_frame == 83 for item in result.ranges)
    assert any(item.source_end_frame == 217 for item in result.ranges)
    assert any(
        item.source_start_frame <= 298 and item.source_end_frame >= 322 for item in result.ranges
    )


def test_soft_pair_materializes_and_reversed_pair_is_rejected() -> None:
    raw = sidecar_dict()
    soft_pair = str(uuid4())
    reversed_pair = str(uuid4())
    raw["events"][1:1] = [
        event(
            str(uuid4()),
            "intro_started",
            100,
            pair_id=soft_pair,
            protection=soft_protection(),
            uncertainty_ms=0,
        ),
        event(
            str(uuid4()),
            "intro_ended",
            200,
            pair_id=soft_pair,
            protection=soft_protection(),
            uncertainty_ms=0,
        ),
        event(str(uuid4()), "outro_started", 500, pair_id=reversed_pair),
        event(str(uuid4()), "outro_ended", 100, pair_id=reversed_pair),
    ]
    result = materialize_protection(parse(raw))
    assert result.status == "rejected"
    assert result.errors[0].code == ErrorCode.SIDECAR_EVENT_PAIRS


def test_out_of_bounds_point_is_clamped_away() -> None:
    raw = sidecar_dict()
    identifier = str(uuid4())
    raw["events"].insert(1, event(identifier, "scene_changed", 10_000, counter=10_000))
    result = materialize_protection(parse(raw))
    assert all(UUID(identifier) not in item.source_event_ids for item in result.ranges)


def test_elementary_partition_or_union_hard_over_soft_and_audio_query() -> None:
    ranges = (
        materialized(10, 30, hard=False, time=True, overlays=False, audio=False),
        materialized(20, 40, hard=True, time=False, overlays=True, audio=True),
    )
    normalized = normalize_ranges(ranges)
    assert [(item.source_start_frame, item.source_end_frame) for item in normalized] == [
        (10, 20),
        (20, 30),
        (30, 40),
    ]
    middle = normalized[1]
    assert middle.level == ProtectionLevel.HARD
    assert middle.policy.blocks_time_edits
    assert middle.policy.blocks_overlays
    assert middle.policy.blocks_local_audio_repair
    assert middle.policy.allows_global_mastering
    assert is_local_audio_repair_blocked(FrameRange(Frame(0), Frame(21)), normalized)
    assert not is_local_audio_repair_blocked(FrameRange(Frame(0), Frame(10)), normalized)


@given(
    st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=999),
            st.integers(min_value=1, max_value=50),
            st.booleans(),
            st.booleans(),
            st.booleans(),
            st.booleans(),
        ),
        min_size=0,
        max_size=20,
    )
)
def test_normalization_properties(data: list[tuple[int, int, bool, bool, bool, bool]]) -> None:
    inputs = tuple(
        materialized(
            start,
            min(1000, start + length),
            hard=hard,
            time=time,
            overlays=overlays,
            audio=audio,
        )
        for start, length, hard, time, overlays, audio in data
    )
    result = normalize_ranges(inputs)
    assert list(result) == sorted(result, key=lambda item: item.source_start_frame)
    assert all(0 <= item.source_start_frame < item.source_end_frame <= 1000 for item in result)
    assert all(
        left.source_end_frame <= right.source_start_frame for left, right in pairwise(result)
    )
    for original in inputs:
        covering = [
            item
            for item in result
            if item.source_start_frame < original.source_end_frame
            and original.source_start_frame < item.source_end_frame
        ]
        assert covering
        if original.policy.blocks_time_edits:
            assert all(item.policy.blocks_time_edits for item in covering)
        if original.policy.blocks_overlays:
            assert all(item.policy.blocks_overlays for item in covering)
        if original.policy.blocks_local_audio_repair:
            assert all(item.policy.blocks_local_audio_repair for item in covering)
        if original.level == ProtectionLevel.HARD:
            assert all(item.level == ProtectionLevel.HARD for item in covering)
    assert normalize_ranges(result) == result


@given(
    st.integers(min_value=0, max_value=250),
    st.integers(min_value=0, max_value=250),
)
def test_more_uncertainty_never_shrinks_protection(first: int, second: int) -> None:
    low, high = sorted((first, second))
    low_raw = sidecar_dict()
    high_raw = sidecar_dict()
    marker_id = str(uuid4())
    low_raw["events"].insert(
        1,
        event(marker_id, "manual_protection", 300, uncertainty_ms=low, counter=300),
    )
    high_raw["events"].insert(
        1,
        event(marker_id, "manual_protection", 300, uncertainty_ms=high, counter=300),
    )
    low_ranges = materialize_protection(parse(low_raw)).ranges
    high_ranges = materialize_protection(parse(high_raw)).ranges
    low_related = [item for item in low_ranges if UUID(marker_id) in item.source_event_ids]
    high_related = [item for item in high_ranges if UUID(marker_id) in item.source_event_ids]
    assert min(item.source_start_frame for item in high_related) <= min(
        item.source_start_frame for item in low_related
    )
    assert max(item.source_end_frame for item in high_related) >= max(
        item.source_end_frame for item in low_related
    )


def test_atomic_deterministic_utf8_output(tmp_path: Path) -> None:
    target = tmp_path / "protection-ranges.json"
    document = ProtectionRangesDocument(
        source_sha256="a" * 64,
        input_hash="b" * 64,
        configuration_hash="c" * 64,
        ranges=(materialized(1, 3, hard=True, time=True, overlays=True, audio=True),),
    )
    first = write_protection_ranges(target, document)
    assert first.status == "written" and first.error is None
    first_bytes = target.read_bytes()
    assert not first_bytes.startswith(b"\xef\xbb\xbf")
    assert json.loads(first_bytes)["schema_version"] == "1.0"
    second = write_protection_ranges(target, document)
    assert second.status == "written"
    assert target.read_bytes() == first_bytes
    assert not list(tmp_path.glob("*.tmp.*"))


def test_atomic_output_failure_is_structured(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "nested" / "protection-ranges.json"
    document = ProtectionRangesDocument(
        source_sha256="a" * 64,
        input_hash="b" * 64,
        configuration_hash="c" * 64,
        ranges=(),
    )
    result = write_protection_ranges(target, document)
    assert result.status == "failed"
    assert result.error is not None and result.error.code == ErrorCode.SIDECAR_OUTPUT
    assert not target.exists()


def test_atomic_replace_failure_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "protection-ranges.json"
    document = ProtectionRangesDocument(
        source_sha256="a" * 64,
        input_hash="b" * 64,
        configuration_hash="c" * 64,
        ranges=(),
    )

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulierter Replace-Fehler")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = write_protection_ranges(target, document)
    assert result.status == "failed"
    assert not target.exists()
    assert not list(tmp_path.iterdir())
