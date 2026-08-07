"""Deterministic composition of the unchanged Phase-1 sidecar core."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from fractions import Fraction
from typing import Literal, cast
from uuid import UUID

from matrix_auto_cutter.calibration import (
    affine_counter_frame,
    calculate_drift_ppm,
    calculate_event_uncertainty_ms,
    calibration_residual_ms,
    map_event_to_source_frame,
    map_qpc_frame,
    sample_gaps_valid,
    subtract_paused_ns,
)
from matrix_auto_cutter.clock_bounds import DRIFT_WARNING_PPM, MAX_DRIFT_PPM
from matrix_auto_cutter.journal import (
    JournalCalibrationSample,
    JournalEvent,
    JournalHeader,
    JournalPause,
    JournalRecord,
    JournalResume,
    JournalStop,
    _parse_record,
)
from matrix_auto_cutter.models import (
    CalibrationSample,
    ClockCalibration,
    ClockSample,
    EventProtection,
    FinalizationEvidence,
    Lifecycle,
    PauseInterval,
    PauseMeasurement,
    Producer,
    ProtectionLevel,
    ProtectionPolicy,
)
from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.finalizer.errors import (
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerFailure,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.loader import LoadedJournal
from matrix_auto_cutter.phase2.finalizer.models import FinalizationIntent
from matrix_auto_cutter.protection import materialize_protection
from matrix_auto_cutter.sidecar import (
    ObsEventSidecarV12,
    SidecarCapabilities,
    SidecarEventV12,
    SidecarValidationResult,
    validate_sidecar,
)

_LOGGER = logging.getLogger(__name__)


class _ConstructionCancelled(RuntimeError):
    pass


def _check_cancelled(cancellation: CancellationToken | None) -> None:
    if cancellation is not None and cancellation.is_cancelled:
        raise _ConstructionCancelled


def _cancel_failure() -> FinalizerFailure:
    return failure(
        FinalizerErrorCode.CANCELLED,
        FinalizerErrorCategory.CANCELLED,
        "sidecar.construct",
        "sidecar construction cancelled",
        retryable=True,
    )


def _policy(*, blocked: bool) -> EventProtection:
    return EventProtection(
        level=ProtectionLevel.HARD if blocked else ProtectionLevel.SOFT,
        buffer_before_ms=0,
        buffer_after_ms=0,
        policy=ProtectionPolicy(
            blocks_time_edits=blocked,
            blocks_overlays=blocked,
            blocks_local_audio_repair=blocked,
            allows_global_mastering=True,
        ),
    )


def _automatic_policy(event_type: str) -> EventProtection:
    before = 1000 if event_type == "recording_stopped" else 0
    if before == 0 and event_type.endswith("_ended"):
        before = 250
    after = 1000 if event_type == "recording_started" else 0
    if after == 0 and event_type.endswith("_started"):
        after = 250
    base = _policy(blocked=True)
    return EventProtection(
        level=base.level,
        buffer_before_ms=before,
        buffer_after_ms=after,
        policy=base.policy,
    )


def _typed_records(
    journal: LoadedJournal,
    cancellation: CancellationToken | None = None,
) -> tuple[JournalRecord, ...]:
    records: list[JournalRecord] = []
    for record in journal.records:
        _check_cancelled(cancellation)
        records.append(_parse_record(record))
    return tuple(records)


def _pauses(
    records: tuple[JournalRecord, ...],
    cancellation: CancellationToken | None = None,
) -> tuple[
    tuple[tuple[JournalPause, JournalResume | JournalStop], ...],
    tuple[PauseMeasurement, ...],
]:
    pairs: list[tuple[JournalPause, JournalResume | JournalStop]] = []
    opened: JournalPause | None = None
    for record in records:
        _check_cancelled(cancellation)
        if isinstance(record, JournalPause):
            opened = record
        elif (isinstance(record, JournalResume) and opened is not None) or (
            isinstance(record, JournalStop) and opened is not None
        ):
            pairs.append((opened, record))
            opened = None
    measurements = tuple(
        PauseMeasurement(start_ns=a.monotonic_ns, end_ns=b.monotonic_ns) for a, b in pairs
    )
    return tuple(pairs), measurements


def _calibration_samples(
    records: tuple[JournalRecord, ...],
    start: JournalEvent,
    stop: JournalStop,
    cancellation: CancellationToken | None = None,
) -> tuple[CalibrationSample, ...]:
    assert start.output_frame_count is not None
    values = [
        CalibrationSample(
            monotonic_ns=start.monotonic_ns, output_frame_count=start.output_frame_count
        )
    ]
    for item in records:
        _check_cancelled(cancellation)
        if isinstance(item, JournalCalibrationSample):
            values.append(
                CalibrationSample(
                    monotonic_ns=item.monotonic_ns,
                    output_frame_count=item.output_frame_count,
                )
            )
    values.append(
        CalibrationSample(
            monotonic_ns=stop.monotonic_ns, output_frame_count=stop.output_frame_count
        )
    )
    unique = {(item.monotonic_ns, item.output_frame_count): item for item in values}
    return tuple(sorted(unique.values(), key=lambda item: item.monotonic_ns))


def _clock_values(
    samples: tuple[CalibrationSample, ...],
    pauses: tuple[PauseMeasurement, ...],
    counter_start: int,
    counter_end: int,
    total_frames: int,
    cancellation: CancellationToken | None = None,
) -> tuple[Decimal, Decimal] | FinalizerFailure:
    if not sample_gaps_valid(samples, pauses):
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.clock_samples",
            "active calibration sample gaps exceed the Phase-1 contract",
        )
    try:
        residuals: list[Decimal] = []
        for sample in samples:
            _check_cancelled(cancellation)
            residuals.append(
                calibration_residual_ms(
                    Fraction(
                        map_qpc_frame(
                            sample.monotonic_ns,
                            samples,
                            pauses,
                            counter_start,
                            counter_end,
                            total_frames,
                        )
                    ),
                    Fraction(
                        affine_counter_frame(
                            sample.output_frame_count,
                            counter_start,
                            counter_end,
                            total_frames,
                        )
                    ),
                )
            )
        residual = max(residuals, default=Decimal(0))
        active_ns = subtract_paused_ns(samples[0].monotonic_ns, samples[-1].monotonic_ns, pauses)
        drift = calculate_drift_ppm(active_ns, counter_end - counter_start)
    except (ArithmeticError, ValueError) as exc:
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.clock",
            str(exc),
            cause=exc,
        )
    if residual > 50 or drift > MAX_DRIFT_PPM:
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.clock_gate",
            "Phase-1 residual or drift gate was exceeded",
        )
    if drift > DRIFT_WARNING_PPM:
        _LOGGER.warning(
            "calibration drift %s ppm exceeds the %s ppm warning bound and stays below the "
            "%s ppm rejection bound; the resulting frame mapping is correspondingly uncertain",
            drift,
            DRIFT_WARNING_PPM,
            MAX_DRIFT_PPM,
        )
    return drift, residual


def _phase1_rejection_message(validated: SidecarValidationResult) -> str:
    # The validator reports the offending check and its measured values in
    # technical_context. Carrying that into the message keeps the finalizer
    # failure as diagnosable as the pydantic error it replaces; without it the
    # caller only learns that some unnamed Phase-1 check said no.
    # Read defensively: this renders a failure message, so it must never raise
    # and mask the failure it is describing.
    reasons = tuple(getattr(validated, "reasons", ()))
    shown = reasons[:8]
    parts = [
        f"{reason.code.value} "
        f"{json.dumps(reason.technical_context, default=str, sort_keys=True, ensure_ascii=False)}"
        for reason in shown
    ]
    if len(reasons) > len(shown):
        parts.append(f"(+{len(reasons) - len(shown)} further reasons)")
    if not parts:
        parts.append(
            "validator reported no reason; the re-parsed sidecar differs from the "
            "constructed object"
        )
    return (
        f"constructed sidecar failed the complete Phase-1 validator "
        f"(mode={validated.mode}): {'; '.join(parts)}"
    )


def build_sidecar(
    journal: LoadedJournal,
    intent: FinalizationIntent,
    cancellation: CancellationToken | None = None,
) -> ObsEventSidecarV12 | FinalizerFailure:
    """Compose Phase-1 models and reject unless its validators accept the result."""
    try:
        records = _typed_records(journal, cancellation)
        header = next(item for item in records if isinstance(item, JournalHeader))
        stop = next(item for item in records if isinstance(item, JournalStop))
        starts = [
            item
            for item in records
            if isinstance(item, JournalEvent) and item.event_type == "recording_started"
        ]
        if len(starts) != 1 or starts[0].output_frame_count is None:
            raise ValueError("exactly one counter-bound recording_started event is required")
        start = starts[0]
        pairs, pause_measurements = _pauses(records, cancellation)
        samples = _calibration_samples(records, start, stop, cancellation)
        counter_start = start.output_frame_count
        counter_end = stop.output_frame_count
        assert counter_start is not None
        assert counter_end is not None
        total_frames = intent.source_identity.video_frame_count
        values = _clock_values(
            samples,
            pause_measurements,
            counter_start,
            counter_end,
            total_frames,
            cancellation,
        )
        if isinstance(values, FinalizerFailure):
            return values
        drift, residual = values
        events: list[SidecarEventV12] = []
        warnings: list[str] = []

        def mapped_event(
            *,
            event_id: object,
            event_type: str,
            monotonic_ns: int,
            output_frame_count: int | None,
            manual: bool,
            pair_id: object | None = None,
            scene_uuid: object | None = None,
            scene_name: str | None = None,
            label: str | None = None,
            protection: EventProtection,
        ) -> SidecarEventV12:
            _check_cancelled(cancellation)
            mapped = map_event_to_source_frame(
                event_counter=output_frame_count,
                event_ns=monotonic_ns,
                manual=manual,
                samples=samples,
                pauses=pause_measurements,
                counter_start=counter_start,
                counter_end=counter_end,
                source_frame_count=total_frames,
            )
            if mapped.status != "mapped" or mapped.source_frame is None:
                raise ValueError("Phase-1 event mapping rejected a journal event")
            uncertainty = calculate_event_uncertainty_ms(
                manual=manual,
                max_residual_ms=residual,
                qpc_fallback=output_frame_count is None,
            )
            kwargs: dict[str, object] = {
                "event_id": event_id,
                "type": event_type,
                "mapped_source_frame": mapped.source_frame,
                "uncertainty_ms": uncertainty,
                "clock_sample": ClockSample(
                    monotonic_ns=monotonic_ns,
                    output_frame_count=output_frame_count,
                    mapping_basis=(
                        "output_frame_counter" if output_frame_count is not None else "qpc_fallback"
                    ),
                ),
                "protection": protection,
            }
            if pair_id is not None:
                kwargs["pair_id"] = pair_id
            if scene_uuid is not None:
                kwargs["scene_uuid"] = scene_uuid
            if scene_name is not None:
                kwargs["scene_name"] = scene_name
            if label is not None:
                kwargs["label"] = label
            return SidecarEventV12.model_validate(kwargs)

        for record in records:
            _check_cancelled(cancellation)
            if isinstance(record, JournalEvent):
                if record.recording_paused:
                    warnings.append(f"event_during_pause:{record.event_id}")
                    continue
                events.append(
                    mapped_event(
                        event_id=record.event_id,
                        event_type=record.event_type,
                        monotonic_ns=record.monotonic_ns,
                        output_frame_count=record.output_frame_count,
                        manual=record.event_type == "manual_protection",
                        pair_id=record.pair_id,
                        scene_uuid=(
                            record.source_uuid if record.event_type == "scene_changed" else None
                        ),
                        scene_name=(
                            record.label if record.event_type == "scene_changed" else None
                        ),
                        label=(record.label if record.event_type == "manual_protection" else None),
                        protection=_automatic_policy(record.event_type),
                    )
                )
            elif isinstance(record, JournalPause | JournalResume):
                events.append(
                    mapped_event(
                        event_id=record.event_id,
                        event_type=(
                            "recording_paused"
                            if isinstance(record, JournalPause)
                            else "recording_resumed"
                        ),
                        monotonic_ns=record.monotonic_ns,
                        output_frame_count=record.output_frame_count,
                        manual=False,
                        protection=_policy(blocked=False),
                    )
                )
        events.append(
            mapped_event(
                event_id=intent.synthetic_stop_event_id,
                event_type="recording_stopped",
                monotonic_ns=stop.monotonic_ns,
                output_frame_count=stop.output_frame_count,
                manual=False,
                protection=_automatic_policy("recording_stopped"),
            )
        )
        event_by_id = {str(item.event_id): item for item in events}
        pause_intervals = tuple(
            PauseInterval(
                pause_event_id=pause.event_id,
                close_event_id=(
                    close.event_id
                    if isinstance(close, JournalResume)
                    else UUID(intent.synthetic_stop_event_id)
                ),
                end_reason=(
                    "resumed"
                    if isinstance(close, JournalResume)
                    else "recording_stopped_while_paused"
                ),
                pause_monotonic_ns=pause.monotonic_ns,
                end_monotonic_ns=close.monotonic_ns,
                mapped_source_frame_before=event_by_id[str(pause.event_id)].mapped_source_frame,
                mapped_source_frame_after=event_by_id[
                    str(close.event_id)
                    if isinstance(close, JournalResume)
                    else intent.synthetic_stop_event_id
                ].mapped_source_frame,
            )
            for pause, close in pairs
        )
        maximum_uncertainty = max(item.uncertainty_ms for item in events)
        remux = cast(
            Literal["not_used", "obs_auto_verified", "manual_verified", "rebind_verified"],
            {
                "direct_mp4": "not_used",
                "obs_auto_remux": "obs_auto_verified",
                "manual_remux": "manual_verified",
                "renamed_rebind": "rebind_verified",
            }[intent.source_identity.binding.value],
        )
        sidecar = ObsEventSidecarV12(
            artifact_type="obs_event_sidecar",
            schema_version="1.2",
            producer=Producer(
                name="matrix-auto-cutter-obs-producer",
                version=header.producer.version,
                obs_version=header.producer.obs_version,
                finalizer_version="phase2f/1.0",
            ),
            lifecycle=Lifecycle(
                status="finalized",
                journal_schema_version="1.0",
                finalized_at=intent.finalized_at,
                finalizer_run_id=UUID(intent.finalizer_run_id),
            ),
            recording_session_id=UUID(intent.recording_id),
            source=intent.source_identity,
            clock=ClockCalibration(
                origin="producer_monotonic_at_output_start_signal",
                monotonic_source="windows_qpc",
                mapping="obs_output_frame_counter_calibrated_to_final_video_frames",
                counter_start=counter_start,
                counter_end=counter_end,
                drift_ppm=drift,
                max_calibration_residual_ms=residual,
                max_event_uncertainty_ms=maximum_uncertainty,
                calibration_sample_count=len(samples),
            ),
            capabilities=SidecarCapabilities(
                pause_resume="supported_v1",
                file_splitting="not_used_unsupported_v1",
                remux=remux,
            ),
            pause_intervals=pause_intervals,
            events=tuple(events),
            finalization=FinalizationEvidence(
                file_closed_verified=True,
                full_sha256_verified=True,
                probe_verified=True,
                journal_complete=True,
                warnings=tuple(warnings),
            ),
        )
    except _ConstructionCancelled:
        return _cancel_failure()
    except (ArithmeticError, KeyError, StopIteration, TypeError, ValueError) as exc:
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.construct",
            str(exc),
            cause=exc,
        )
    if cancellation is not None and cancellation.is_cancelled:
        return _cancel_failure()
    phase1_payload = json.loads(sidecar.model_dump_json(), parse_float=Decimal)
    validated = validate_sidecar(phase1_payload, intent.source_identity)
    if validated.mode != "validated_sidecar_1_2" or validated.sidecar != sidecar:
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.phase1_validation",
            _phase1_rejection_message(validated),
            underlying=validated,
        )
    if cancellation is not None and cancellation.is_cancelled:
        return _cancel_failure()
    protection = materialize_protection(sidecar)
    if protection.status != "materialized":
        return failure(
            FinalizerErrorCode.JOURNAL_CORRUPT,
            FinalizerErrorCategory.INTEGRITY,
            "sidecar.protection",
            "constructed sidecar failed Phase-1 protection materialization",
            underlying=protection,
        )
    return sidecar
