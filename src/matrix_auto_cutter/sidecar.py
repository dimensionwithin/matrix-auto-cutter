"""Finalisiertes Sidecar 1.1 und exception-freier Consumer-Validator."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    WithJsonSchema,
    field_validator,
    model_serializer,
    model_validator,
)

from matrix_auto_cutter.calibration import (
    affine_counter_frame,
    calculate_drift_ppm,
    map_qpc_frame,
    subtract_paused_ns,
)
from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import (
    CalibrationSample,
    CanonicalModel,
    CanonicalUuid4,
    ClockCalibration,
    ClockSample,
    DecimalMax250,
    EventProtection,
    FinalizationEvidence,
    Lifecycle,
    PauseInterval,
    PauseMeasurement,
    Producer,
    ProtectionLevel,
    SourceIdentity,
    _json_mapping_payload,
    _JsonInputError,
)


class SidecarCapabilities(CanonicalModel):
    """Final nachgewiesene Producerfähigkeiten."""

    pause_resume: Literal["supported_v1"]
    file_splitting: Literal["not_used_unsupported_v1"]
    remux: Literal["not_used", "obs_auto_verified", "manual_verified", "rebind_verified"]


EventType = Literal[
    "recording_started",
    "recording_stopped",
    "recording_paused",
    "recording_resumed",
    "scene_changed",
    "intro_started",
    "intro_ended",
    "outro_started",
    "outro_ended",
    "stinger_started",
    "stinger_ended",
    "manual_protection",
]
_OPTIONAL_EVENT_FIELDS = (
    "end_mapped_source_frame",
    "pair_id",
    "scene_name",
    "label",
)


class _MissingEventValue:
    """Unveränderlicher interner Marker für ein nicht angegebenes Eventfeld."""

    __slots__ = ()

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> Self:
        return self


_MISSING_EVENT_VALUE = _MissingEventValue()


_OptionalEndFrame = Annotated[
    Annotated[int, Field(ge=0)] | _MissingEventValue,
    WithJsonSchema({"type": "integer", "minimum": 0}),
]
_OptionalPairId = Annotated[
    CanonicalUuid4 | _MissingEventValue,
    WithJsonSchema({"type": "string", "format": "uuid"}),
]
_OptionalSceneName = Annotated[
    Annotated[str, Field(max_length=200)] | _MissingEventValue,
    WithJsonSchema({"type": "string", "maxLength": 200}),
]
_OptionalLabel = Annotated[
    Annotated[str, Field(max_length=500)] | _MissingEventValue,
    WithJsonSchema({"type": "string", "maxLength": 500}),
]


def _missing_optional_event_value() -> _MissingEventValue:
    return _MISSING_EVENT_VALUE


class SidecarEvent(CanonicalModel):
    """Kanonisches, auf Sourceframes kalibriertes Sidecar-Ereignis."""

    event_id: CanonicalUuid4
    type: EventType
    mapped_source_frame: int = Field(ge=0)
    end_mapped_source_frame: _OptionalEndFrame = Field(
        default_factory=_missing_optional_event_value
    )
    uncertainty_ms: DecimalMax250
    pair_id: _OptionalPairId = Field(default_factory=_missing_optional_event_value)
    scene_name: _OptionalSceneName = Field(default_factory=_missing_optional_event_value)
    label: _OptionalLabel = Field(default_factory=_missing_optional_event_value)
    clock_sample: ClockSample
    protection: EventProtection

    @field_validator(*_OPTIONAL_EVENT_FIELDS, mode="before")
    @classmethod
    def reject_explicit_null_optional_fields(cls, value: object) -> object:
        """Unterscheide weggelassene Felder von explizitem JSON-``null``."""
        if value is None:
            msg = "Optionale Eventfelder dürfen nicht null sein."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def reject_injected_missing_marker(self) -> SidecarEvent:
        """Der interne Missing-Marker darf nicht als gesetzter Eingabewert auftreten."""
        if any(
            name in self.model_fields_set and isinstance(getattr(self, name), _MissingEventValue)
            for name in _OPTIONAL_EVENT_FIELDS
        ):
            msg = "Der interne Missing-Marker ist kein öffentlicher Eventfeldwert."
            raise ValueError(msg)
        return self

    @model_serializer(mode="wrap")
    def omit_missing_optional_fields(self, handler: SerializerFunctionWrapHandler) -> object:
        """Serialisiere fehlende optionale Felder niemals als ``null``."""
        serialized: dict[str, object] = handler(self)
        for name in _OPTIONAL_EVENT_FIELDS:
            value = getattr(self, name)
            if name in self.model_fields_set and value is None:
                msg = f"Explizites null in optionalem Eventfeld {name} ist nicht serialisierbar."
                raise ValueError(msg)
            if isinstance(value, _MissingEventValue):
                serialized.pop(name, None)
        return serialized


class ObsEventSidecar(CanonicalModel):
    """Vollständiger kanonischer Sidecar-1.1-Vertrag."""

    model_config = ConfigDict(
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://dimensionwithin.local/schemas/obs-events-1.1.json",
        }
    )

    artifact_type: Literal["obs_event_sidecar"]
    schema_version: Literal["1.1"]
    producer: Producer
    lifecycle: Lifecycle
    recording_session_id: CanonicalUuid4
    source: SourceIdentity
    clock: ClockCalibration
    capabilities: SidecarCapabilities
    pause_intervals: tuple[PauseInterval, ...]
    events: tuple[SidecarEvent, ...]
    finalization: FinalizationEvidence


class SidecarValidationResult(CanonicalModel):
    """Auto-Cut-Modus und alle strukturierten Safe-Mode-Gründe."""

    mode: Literal["validated_sidecar_1_1", "no_sidecar_safe_mode"]
    sidecar: ObsEventSidecar | None = None
    reasons: tuple[CoreError, ...]


_AUTOMATIC_TYPES = {
    "recording_started",
    "recording_stopped",
    "scene_changed",
    "intro_started",
    "intro_ended",
    "outro_started",
    "outro_ended",
    "stinger_started",
    "stinger_ended",
}
_PAIR_TYPES = {
    "intro_started": ("intro", "start"),
    "intro_ended": ("intro", "end"),
    "outro_started": ("outro", "start"),
    "outro_ended": ("outro", "end"),
    "stinger_started": ("stinger", "start"),
    "stinger_ended": ("stinger", "end"),
}
_DRIFT_MATCH_TOLERANCE_PPM = Decimal("0.001")


def _pair_structure_failures(events: tuple[SidecarEvent, ...]) -> list[str]:
    grouped: dict[tuple[str, UUID], dict[str, list[SidecarEvent]]] = {}
    families_by_pair_id: dict[UUID, set[str]] = {}
    failures: list[str] = []
    for event in events:
        if event.type not in _PAIR_TYPES:
            if isinstance(event.pair_id, UUID):
                failures.append("pair_id_on_non_pair_event")
            continue
        if not isinstance(event.pair_id, UUID):
            failures.append("pair_id_missing")
            continue
        family, side = _PAIR_TYPES[event.type]
        families_by_pair_id.setdefault(event.pair_id, set()).add(family)
        grouped.setdefault((family, event.pair_id), {}).setdefault(side, []).append(event)
    if any(len(families) != 1 for families in families_by_pair_id.values()):
        failures.append("pair_id_reused_across_families")
    for pair in grouped.values():
        starts = pair.get("start", [])
        ends = pair.get("end", [])
        if len(starts) > 1 or len(ends) > 1:
            failures.append("ambiguous_pair")
        elif starts and ends and starts[0].mapped_source_frame >= ends[0].mapped_source_frame:
            failures.append("non_forward_pair")
    return failures


def _safe_mode(code: ErrorCode, reason: str, **context: object) -> SidecarValidationResult:
    return SidecarValidationResult(
        mode="no_sidecar_safe_mode",
        reasons=(core_error(code, {"reason": reason, **context}),),
    )


def _identity_matches(actual: SourceIdentity, expected: SourceIdentity) -> bool:
    exact = actual.model_dump(exclude={"duration_ms"}) == expected.model_dump(
        exclude={"duration_ms"}
    )
    duration_within_one_frame = abs(actual.duration_ms - expected.duration_ms) * 60 <= 1000
    return exact and duration_within_one_frame


def _clock_errors(sidecar: ObsEventSidecar) -> list[CoreError]:
    clock = sidecar.clock
    source = sidecar.source
    span = clock.counter_end - clock.counter_start
    failures: list[str] = []
    if span <= 0 or abs(span - source.video_frame_count) > 6:
        failures.append("counter_span")
    if abs(source.duration_ms * 60 - span * 1000) > 6000:
        failures.append("duration_span")
    maximum = max((event.uncertainty_ms for event in sidecar.events), default=None)
    if maximum is None or clock.max_event_uncertainty_ms != maximum:
        failures.append("max_event_uncertainty_mismatch")
    counter_samples = tuple(
        CalibrationSample(
            monotonic_ns=event.clock_sample.monotonic_ns,
            output_frame_count=event.clock_sample.output_frame_count,
        )
        for event in sidecar.events
        if event.clock_sample.mapping_basis == "output_frame_counter"
        and event.clock_sample.output_frame_count is not None
    )
    pauses = tuple(
        PauseMeasurement(start_ns=item.pause_monotonic_ns, end_ns=item.end_monotonic_ns)
        for item in sidecar.pause_intervals
    )
    starts = [event for event in sidecar.events if event.type == "recording_started"]
    stops = [event for event in sidecar.events if event.type == "recording_stopped"]
    qpc_start = starts[0].clock_sample.monotonic_ns if len(starts) == 1 else None
    qpc_end = stops[0].clock_sample.monotonic_ns if len(stops) == 1 else None
    if qpc_start is not None and qpc_end is not None:
        try:
            active_elapsed_ns = subtract_paused_ns(qpc_start, qpc_end, pauses)
            actual_drift_ppm = calculate_drift_ppm(active_elapsed_ns, span)
        except ValueError:
            failures.append("invalid_active_qpc_duration")
        else:
            if actual_drift_ppm > 500:
                failures.append("actual_drift_ppm")
            if abs(clock.drift_ppm - actual_drift_ppm) > _DRIFT_MATCH_TOLERANCE_PPM:
                failures.append("declared_drift_mismatch")
    for event in sidecar.events:
        sample = event.clock_sample
        if event.type == "manual_protection" and sample.output_frame_count is None:
            failures.append("manual_marker_without_counter")
        if (
            qpc_start is not None
            and qpc_end is not None
            and not (qpc_start <= sample.monotonic_ns <= qpc_end)
        ):
            failures.append(f"qpc_out_of_range:{event.event_id}")
        if sample.output_frame_count is not None and not (
            clock.counter_start <= sample.output_frame_count <= clock.counter_end
        ):
            failures.append(f"counter_out_of_range:{event.event_id}")
            continue
        try:
            expected_frame = (
                affine_counter_frame(
                    sample.output_frame_count,
                    clock.counter_start,
                    clock.counter_end,
                    source.video_frame_count,
                )
                if sample.mapping_basis == "output_frame_counter"
                and sample.output_frame_count is not None
                else map_qpc_frame(
                    sample.monotonic_ns,
                    counter_samples,
                    pauses,
                    clock.counter_start,
                    clock.counter_end,
                    source.video_frame_count,
                )
            )
        except ValueError:
            failures.append(f"unmappable_clock_sample:{event.event_id}")
        else:
            if expected_frame != event.mapped_source_frame:
                failures.append(f"mapped_frame_mismatch:{event.event_id}")
    return (
        [core_error(ErrorCode.SIDECAR_CLOCK_UNRELIABLE, {"failures": failures})] if failures else []
    )


def _policy_errors(sidecar: ObsEventSidecar) -> list[CoreError]:
    failures: list[str] = []
    for event in sidecar.events:
        policy = event.protection.policy
        if event.type in _AUTOMATIC_TYPES and (
            event.protection.level != ProtectionLevel.HARD
            or not policy.blocks_time_edits
            or not policy.blocks_overlays
            or not policy.blocks_local_audio_repair
        ):
            failures.append(str(event.event_id))
        if event.type == "manual_protection" and (
            event.protection.level == ProtectionLevel.HARD and not policy.blocks_time_edits
        ):
            failures.append(str(event.event_id))
    return [core_error(ErrorCode.SIDECAR_POLICY, {"event_ids": failures})] if failures else []


def _event_errors(sidecar: ObsEventSidecar) -> list[CoreError]:
    failures: list[str] = []
    event_id_counts = Counter(event.event_id for event in sidecar.events)
    duplicate_ids = sorted(str(key) for key, count in event_id_counts.items() if count > 1)
    event_ids = {event.event_id: event for event in sidecar.events}
    if duplicate_ids:
        failures.append("duplicate_event_id")
    starts = [event for event in sidecar.events if event.type == "recording_started"]
    stops = [event for event in sidecar.events if event.type == "recording_stopped"]
    if len(starts) != 1 or starts[0].mapped_source_frame != 0:
        failures.append("recording_start")
    if len(stops) != 1 or stops[0].mapped_source_frame != sidecar.source.video_frame_count:
        failures.append("recording_stop")
    for event in sidecar.events:
        if event.mapped_source_frame > sidecar.source.video_frame_count:
            failures.append("event_out_of_bounds")
        if event.type == "manual_protection":
            if isinstance(event.end_mapped_source_frame, int) and (
                event.end_mapped_source_frame <= event.mapped_source_frame
                or event.end_mapped_source_frame > sidecar.source.video_frame_count
            ):
                failures.append("manual_interval")
        elif isinstance(event.end_mapped_source_frame, int):
            failures.append("end_frame_on_non_manual")
        if isinstance(event.scene_name, str) and event.type != "scene_changed":
            failures.append("scene_name_on_wrong_event_type")
        if isinstance(event.label, str) and event.type != "manual_protection":
            failures.append("label_on_wrong_event_type")
    pair_failures = _pair_structure_failures(sidecar.events)

    pause_failures: list[str] = []
    referenced_pause_ids: set[UUID] = set()
    referenced_close_ids: set[UUID] = set()
    previous_pause_end = -1
    for interval in sorted(sidecar.pause_intervals, key=lambda item: item.pause_monotonic_ns):
        if interval.pause_event_id in referenced_pause_ids:
            pause_failures.append(f"duplicate_pause_reference:{interval.pause_event_id}")
        if interval.close_event_id in referenced_close_ids:
            pause_failures.append(f"duplicate_close_reference:{interval.close_event_id}")
        if interval.pause_monotonic_ns < previous_pause_end:
            pause_failures.append("overlapping_pause_intervals")
        previous_pause_end = interval.end_monotonic_ns
        pause = event_ids.get(interval.pause_event_id)
        close = event_ids.get(interval.close_event_id)
        expected_close = (
            "recording_resumed" if interval.end_reason == "resumed" else "recording_stopped"
        )
        if (
            pause is None
            or pause.type != "recording_paused"
            or close is None
            or close.type != expected_close
        ):
            pause_failures.append(str(interval.pause_event_id))
        elif (
            interval.pause_monotonic_ns != pause.clock_sample.monotonic_ns
            or interval.end_monotonic_ns != close.clock_sample.monotonic_ns
            or interval.mapped_source_frame_before != pause.mapped_source_frame
            or interval.mapped_source_frame_after != close.mapped_source_frame
        ):
            pause_failures.append(f"interval_event_mismatch:{interval.pause_event_id}")
        referenced_pause_ids.add(interval.pause_event_id)
        referenced_close_ids.add(interval.close_event_id)
        for event in sidecar.events:
            if (
                interval.pause_monotonic_ns
                < event.clock_sample.monotonic_ns
                < interval.end_monotonic_ns
                and event.type not in {"recording_paused", "recording_resumed", "recording_stopped"}
            ):
                pause_failures.append(f"event_during_pause:{event.event_id}")
    actual_pause_ids = {
        event.event_id for event in sidecar.events if event.type == "recording_paused"
    }
    if referenced_pause_ids != actual_pause_ids:
        pause_failures.append("pause_interval_coverage")
    resumed_ids = {event.event_id for event in sidecar.events if event.type == "recording_resumed"}
    if not resumed_ids.issubset(referenced_close_ids):
        pause_failures.append("resume_interval_coverage")

    pause_open = False
    for event in sorted(sidecar.events, key=lambda item: item.clock_sample.monotonic_ns):
        if event.type == "recording_paused":
            if pause_open:
                pause_failures.append("double_pause")
            pause_open = True
        elif event.type == "recording_resumed":
            if not pause_open:
                pause_failures.append("resume_without_pause")
            pause_open = False
        elif event.type == "recording_stopped":
            pause_open = False

    errors: list[CoreError] = []
    if failures:
        code = (
            ErrorCode.SIDECAR_EVENT_PAIRS
            if "duplicate_event_id" in failures
            else ErrorCode.SIDECAR_POLICY
        )
        errors.append(core_error(code, {"failures": failures}))
    if pair_failures:
        errors.append(core_error(ErrorCode.SIDECAR_EVENT_PAIRS, {"failures": pair_failures}))
    if pause_failures:
        errors.append(core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"failures": pause_failures}))
    return errors


def validate_sidecar(
    raw: Mapping[str, object] | None,
    expected_source: SourceIdentity,
) -> SidecarValidationResult:
    """Prüfe ein bereitgestelltes Artefakt gegen Source, Clock und Policy."""
    if raw is None:
        return _safe_mode(ErrorCode.SIDECAR_ARTIFACT_TYPE, "sidecar_missing")
    preliminary: list[CoreError] = []
    if raw.get("artifact_type") != "obs_event_sidecar":
        preliminary.append(
            core_error(ErrorCode.SIDECAR_ARTIFACT_TYPE, {"reason": "wrong_artifact_type"})
        )
    if raw.get("schema_version") != "1.1":
        preliminary.append(
            core_error(ErrorCode.SIDECAR_VERSION, {"reason": "unsupported_schema_version"})
        )
    lifecycle = raw.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("status") != "finalized":
        preliminary.append(
            core_error(ErrorCode.SIDECAR_NOT_FINALIZED, {"reason": "lifecycle_not_finalized"})
        )
    if preliminary:
        return SidecarValidationResult(mode="no_sidecar_safe_mode", reasons=tuple(preliminary))
    try:
        payload = _json_mapping_payload(raw)
    except _JsonInputError as exc:
        return _safe_mode(ErrorCode.SIDECAR_POLICY, "schema_validation", detail=str(exc))
    try:
        sidecar = ObsEventSidecar.model_validate_json(payload)
    except ValidationError as exc:
        groups: set[ErrorCode] = set()
        for issue in exc.errors():
            location = issue["loc"]
            if (location and location[0] == "clock") or (
                len(location) > 2
                and location[0] == "events"
                and location[2] in {"uncertainty_ms", "clock_sample"}
            ):
                groups.add(ErrorCode.SIDECAR_CLOCK_UNRELIABLE)
            elif location and location[0] == "pause_intervals":
                groups.add(ErrorCode.SIDECAR_PAUSE_SEQUENCE)
            else:
                groups.add(ErrorCode.SIDECAR_POLICY)
        ordered_codes = tuple(
            code
            for code in (
                ErrorCode.SIDECAR_CLOCK_UNRELIABLE,
                ErrorCode.SIDECAR_PAUSE_SEQUENCE,
                ErrorCode.SIDECAR_POLICY,
            )
            if code in groups
        )
        return SidecarValidationResult(
            mode="no_sidecar_safe_mode",
            reasons=tuple(
                core_error(code, {"reason": "schema_validation", "detail": str(exc)})
                for code in ordered_codes
            ),
        )
    reasons: list[CoreError] = []
    if not _identity_matches(sidecar.source, expected_source):
        reasons.append(
            core_error(
                ErrorCode.SIDECAR_IDENTITY,
                {
                    "expected": expected_source.model_dump(),
                    "actual": sidecar.source.model_dump(),
                },
                artifact_id=str(sidecar.recording_session_id),
            )
        )
    reasons.extend(_clock_errors(sidecar))
    reasons.extend(_policy_errors(sidecar))
    reasons.extend(_event_errors(sidecar))
    if reasons:
        return SidecarValidationResult(mode="no_sidecar_safe_mode", reasons=tuple(reasons))
    return SidecarValidationResult(
        mode="validated_sidecar_1_1",
        sidecar=sidecar,
        reasons=(),
    )
