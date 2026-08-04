"""Konservative Eventpaarung und elementare Protection-Policy-Partition."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from itertools import pairwise
from typing import Literal
from uuid import UUID

from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import (
    CanonicalModel,
    MaterializedFrameRange,
    ProtectionLevel,
    ProtectionPolicy,
)
from matrix_auto_cutter.outro import OutroResolutionEvidence
from matrix_auto_cutter.sidecar import (
    SidecarEvent,
    ValidatedObsEventSidecar,
    _pair_structure_failures,
)
from matrix_auto_cutter.timebase import Frame, FrameRange


@dataclass(frozen=True, slots=True)
class _RawRange:
    start: int
    end: int
    level: ProtectionLevel
    event_ids: tuple[UUID, ...]
    uncertainty_padding_frames: int
    policy: ProtectionPolicy


class ProtectionResolutionResult(CanonicalModel):
    """Materialisierung oder strukturierte Ablehnung."""

    status: Literal["materialized", "rejected"]
    ranges: tuple[MaterializedFrameRange, ...]
    errors: tuple[CoreError, ...]


def _ceil_frames(milliseconds: int | Decimal) -> int:
    value = Decimal(milliseconds) * Decimal(60) / Decimal(1000)
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _uncertainty_padding(event: SidecarEvent) -> int:
    return _ceil_frames(event.uncertainty_ms) + 2


def _union_policy(events: tuple[SidecarEvent, ...]) -> ProtectionPolicy:
    policies = tuple(event.protection.policy for event in events)
    return ProtectionPolicy(
        blocks_time_edits=any(policy.blocks_time_edits for policy in policies),
        blocks_overlays=any(policy.blocks_overlays for policy in policies),
        blocks_local_audio_repair=any(policy.blocks_local_audio_repair for policy in policies),
        allows_global_mastering=True,
    )


def _event_range(event: SidecarEvent, total_frames: int) -> _RawRange | None:
    uncertainty = _uncertainty_padding(event)
    before = _ceil_frames(event.protection.buffer_before_ms)
    after = _ceil_frames(event.protection.buffer_after_ms)
    start = max(0, event.mapped_source_frame - uncertainty - before)
    anchor_end = (
        event.end_mapped_source_frame
        if isinstance(event.end_mapped_source_frame, int)
        else event.mapped_source_frame + 1
    )
    end = min(total_frames, anchor_end + uncertainty + after)
    if start >= end:
        return None
    return _RawRange(
        start=start,
        end=end,
        level=event.protection.level,
        event_ids=(event.event_id,),
        uncertainty_padding_frames=uncertainty,
        policy=event.protection.policy,
    )


def _pair_range(
    start_event: SidecarEvent | None,
    end_event: SidecarEvent | None,
    total_frames: int,
) -> _RawRange | None:
    events = tuple(event for event in (start_event, end_event) if event is not None)
    start = 0
    end = total_frames
    if start_event is not None:
        start = max(
            0,
            start_event.mapped_source_frame
            - _uncertainty_padding(start_event)
            - _ceil_frames(start_event.protection.buffer_before_ms),
        )
    if end_event is not None:
        end = min(
            total_frames,
            end_event.mapped_source_frame
            + _uncertainty_padding(end_event)
            + _ceil_frames(end_event.protection.buffer_after_ms),
        )
    if start >= end:
        return None
    return _RawRange(
        start=start,
        end=end,
        level=(
            ProtectionLevel.HARD
            if any(event.protection.level == ProtectionLevel.HARD for event in events)
            else ProtectionLevel.SOFT
        ),
        event_ids=tuple(event.event_id for event in events),
        uncertainty_padding_frames=max(_uncertainty_padding(event) for event in events),
        policy=_union_policy(events),
    )


def _partition(raw_ranges: tuple[_RawRange, ...]) -> tuple[MaterializedFrameRange, ...]:
    if not raw_ranges:
        return ()
    boundaries = sorted({value for item in raw_ranges for value in (item.start, item.end)})
    result: list[MaterializedFrameRange] = []
    for start, end in pairwise(boundaries):
        active = tuple(item for item in raw_ranges if item.start < end and start < item.end)
        if not active or start >= end:
            continue
        policies = tuple(item.policy for item in active)
        event_ids = tuple(
            sorted({event_id for item in active for event_id in item.event_ids}, key=str)
        )
        result.append(
            MaterializedFrameRange(
                protection_id=f"prot-{len(result) + 1:04d}",
                source_start_frame=start,
                source_end_frame=end,
                level=(
                    ProtectionLevel.HARD
                    if any(item.level == ProtectionLevel.HARD for item in active)
                    else ProtectionLevel.SOFT
                ),
                source_event_ids=event_ids,
                uncertainty_padding_frames=max(item.uncertainty_padding_frames for item in active),
                policy=ProtectionPolicy(
                    blocks_time_edits=any(policy.blocks_time_edits for policy in policies),
                    blocks_overlays=any(policy.blocks_overlays for policy in policies),
                    blocks_local_audio_repair=any(
                        policy.blocks_local_audio_repair for policy in policies
                    ),
                    allows_global_mastering=True,
                ),
            )
        )
    return tuple(result)


def normalize_ranges(
    ranges: tuple[MaterializedFrameRange, ...],
) -> tuple[MaterializedFrameRange, ...]:
    """Partitioniere beliebige materialisierte Eingaben erneut; idempotent kanonisch."""
    raw = tuple(
        _RawRange(
            start=item.source_start_frame,
            end=item.source_end_frame,
            level=item.level,
            event_ids=item.source_event_ids,
            uncertainty_padding_frames=item.uncertainty_padding_frames,
            policy=item.policy,
        )
        for item in ranges
    )
    return _partition(raw)


def materialize_protection(sidecar: ValidatedObsEventSidecar) -> ProtectionResolutionResult:
    """Paare Events konservativ, puffere, clamp und partitioniere alle Policies."""
    pair_failures = _pair_structure_failures(sidecar.events)
    if pair_failures:
        return ProtectionResolutionResult(
            status="rejected",
            ranges=(),
            errors=(
                core_error(
                    ErrorCode.SIDECAR_EVENT_PAIRS,
                    {"failures": pair_failures},
                ),
            ),
        )
    grouped: dict[tuple[str, UUID], list[SidecarEvent]] = {}
    for event in sidecar.events:
        pair_id = event.pair_id
        if event.type.startswith(("intro_", "outro_", "stinger_")) and isinstance(pair_id, UUID):
            family = event.type.split("_", maxsplit=1)[0]
            grouped.setdefault((family, pair_id), []).append(event)

    raw_ranges: list[_RawRange] = []
    for (family, _pair_id), events in grouped.items():
        starts = [event for event in events if event.type == f"{family}_started"]
        ends = [event for event in events if event.type == f"{family}_ended"]
        paired = _pair_range(
            starts[0] if starts else None,
            ends[0] if ends else None,
            sidecar.source.video_frame_count,
        )
        if paired is not None:
            raw_ranges.append(paired)

    paired_ids = {event.event_id for events in grouped.values() for event in events}
    for event in sidecar.events:
        if event.event_id in paired_ids or event.type in {
            "recording_paused",
            "recording_resumed",
            # A generic scene marker acquires semantic protection only after an
            # explicit local outro binding resolves it below.
            "scene_changed",
        }:
            continue
        item = _event_range(event, sidecar.source.video_frame_count)
        if item is not None:
            raw_ranges.append(item)
    return ProtectionResolutionResult(
        status="materialized",
        ranges=_partition(tuple(raw_ranges)),
        errors=(),
    )


def materialize_protection_with_outro(
    sidecar: ValidatedObsEventSidecar,
    resolution: OutroResolutionEvidence,
) -> ProtectionResolutionResult:
    """Add only an exactly resolved hard outro range, then canonically partition."""
    base = materialize_protection(sidecar)
    if base.status != "materialized" or resolution.status != "resolved":
        return base
    assert resolution.scene_event_id is not None
    assert resolution.protected_start_frame is not None
    assert resolution.protected_end_frame is not None
    outro = MaterializedFrameRange(
        protection_id="prot-outro-900",
        source_start_frame=resolution.protected_start_frame,
        source_end_frame=resolution.protected_end_frame,
        level=ProtectionLevel.HARD,
        source_event_ids=(resolution.scene_event_id,),
        uncertainty_padding_frames=0,
        policy=ProtectionPolicy(
            blocks_time_edits=True,
            blocks_overlays=True,
            blocks_local_audio_repair=True,
            allows_global_mastering=True,
        ),
    )
    return ProtectionResolutionResult(
        status="materialized",
        ranges=normalize_ranges((*base.ranges, outro)),
        errors=(),
    )


def is_local_audio_repair_blocked(
    candidate: FrameRange,
    ranges: tuple[MaterializedFrameRange, ...],
) -> bool:
    """Verwerfe einen lokalen Audiokandidaten vollständig bei jeder Blockschnittmenge."""
    return any(
        item.policy.blocks_local_audio_repair
        and candidate.intersects(
            FrameRange(Frame(item.source_start_frame), Frame(item.source_end_frame))
        )
        for item in ranges
    )
