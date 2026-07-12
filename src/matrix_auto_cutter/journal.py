"""Strikter Recording-Journal-1.0-Vertrag und Lifecycle-Validierung."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import PureWindowsPath
from typing import Literal

from pydantic import Field, TypeAdapter, ValidationError

from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import (
    CanonicalModel,
    CanonicalUuid4,
    _json_mapping_payload,
    _JsonInputError,
)


class JournalCapabilities(CanonicalModel):
    """Fähigkeiten des Rohproducers."""

    pause_resume: Literal["supported_v1"]
    file_splitting: Literal["unsupported_v1"]


class JournalClock(CanonicalModel):
    """QPC-Vertrag des Rohjournals."""

    source: Literal["windows_qpc"]
    unit: Literal["ns"]
    origin: Literal["producer_monotonic_at_output_start_signal"]


class JournalProducer(CanonicalModel):
    """Producerfelder vor Existenz eines Finalizers."""

    name: Literal["matrix-auto-cutter-obs-producer"]
    version: str = Field(min_length=1)
    obs_version: str = Field(min_length=1)


class JournalHeader(CanonicalModel):
    """Erster und einziger Headerrecord."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["header"]
    sequence: Literal[0]
    recording_session_id: CanonicalUuid4
    lifecycle_status: Literal["recording"]
    producer: JournalProducer
    clock: JournalClock
    capabilities: JournalCapabilities
    initial_output_path: str = Field(min_length=1)


class JournalEvent(CanonicalModel):
    """Semantisches OBS- oder manuelles Ereignis."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["event"]
    sequence: int = Field(gt=0)
    event_id: CanonicalUuid4
    event_type: Literal[
        "recording_started",
        "scene_changed",
        "intro_started",
        "intro_ended",
        "outro_started",
        "outro_ended",
        "stinger_started",
        "stinger_ended",
        "manual_protection",
    ]
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int | None = Field(default=None, ge=0)
    recording_paused: bool
    source_uuid: CanonicalUuid4 | None = None
    pair_id: CanonicalUuid4 | None = None
    label: str | None = Field(default=None, max_length=500)


class JournalCalibrationSample(CanonicalModel):
    """Periodische aktive QPC-/Counterprobe."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["calibration_sample"]
    sequence: int = Field(gt=0)
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: Literal[False]


class JournalPause(CanonicalModel):
    """Output-Pausesignal."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["pause"]
    sequence: int = Field(gt=0)
    event_id: CanonicalUuid4
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: Literal[True]


class JournalResume(CanonicalModel):
    """Output-Unpausesignal."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["resume"]
    sequence: int = Field(gt=0)
    event_id: CanonicalUuid4
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: Literal[False]


class JournalPathSnapshot(CanonicalModel):
    """Diagnostischer Output-Pfadsnapshot."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["path_snapshot"]
    sequence: int = Field(gt=0)
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: bool
    output_path: str = Field(min_length=1)


class JournalSplitStatus(CanonicalModel):
    """Explizite Split-Erkennung."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["split_status"]
    sequence: int = Field(gt=0)
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: bool
    split_requested: bool
    file_splitting_detected: bool


class JournalOutputError(CanonicalModel):
    """Lifecycle-relevanter Outputfehler."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["output_error"]
    sequence: int = Field(gt=0)
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: bool
    output_result: Literal["failure"]
    diagnostic: str = Field(min_length=1)


class JournalRecovery(CanonicalModel):
    """Recoveryregistrierung; niemals finalisierbarer Stoprecord."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["recovery"]
    sequence: int = Field(gt=0)
    lifecycle_status: Literal["aborted", "finalization_failed"]
    diagnostic: str = Field(min_length=1)


class JournalStop(CanonicalModel):
    """Einziger normal finalisierbarer Endrecord."""

    artifact_type: Literal["recording_event_journal"]
    journal_schema_version: Literal["1.0"]
    record_type: Literal["stop"]
    sequence: int = Field(gt=0)
    lifecycle_status: Literal["stopped_unfinalized"]
    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)
    recording_paused: bool
    last_recording_path: str = Field(min_length=1)
    output_result: Literal["success", "failure"]
    file_splitting_detected: bool


JournalRecord = (
    JournalHeader
    | JournalEvent
    | JournalCalibrationSample
    | JournalPause
    | JournalResume
    | JournalPathSnapshot
    | JournalSplitStatus
    | JournalOutputError
    | JournalRecovery
    | JournalStop
)


class JournalValidationResult(CanonicalModel):
    """Öffentliches Ergebnis ohne erwartbare Exceptions."""

    valid: bool
    recording_session_id: CanonicalUuid4 | None = None
    errors: tuple[CoreError, ...]


_RECORD_TYPES = frozenset(
    {
        "header",
        "event",
        "calibration_sample",
        "pause",
        "resume",
        "path_snapshot",
        "split_status",
        "output_error",
        "recovery",
        "stop",
    }
)
_RECORD_ADAPTER: TypeAdapter[JournalRecord] = TypeAdapter(JournalRecord)


def _parse_record(raw: Mapping[str, object]) -> JournalRecord:
    record_type = raw.get("record_type")
    if not isinstance(record_type, str) or record_type not in _RECORD_TYPES:
        msg = "Unbekannter Journalrecordtyp."
        raise _JsonInputError(msg)
    payload = _json_mapping_payload(raw)
    return _RECORD_ADAPTER.validate_json(payload)


def _record_clock(record: JournalRecord) -> tuple[int, int | None] | None:
    if isinstance(record, JournalHeader | JournalRecovery):
        return None
    return record.monotonic_ns, record.output_frame_count


def validate_journal(records: Sequence[Mapping[str, object]]) -> JournalValidationResult:
    """Validiere Syntax, Sequenz, Lifecycle, Clock, Pause, Stop und Split."""
    errors: list[CoreError] = []
    parsed: list[JournalRecord] = []
    for index, raw in enumerate(records):
        try:
            parsed.append(_parse_record(raw))
        except (_JsonInputError, ValidationError) as exc:
            errors.append(
                core_error(
                    ErrorCode.JOURNAL_SEQUENCE,
                    {"record_index": index, "detail": str(exc)},
                )
            )

    header = parsed[0] if parsed and isinstance(parsed[0], JournalHeader) else None
    session_id = header.recording_session_id if header is not None else None
    if (
        len(parsed) != len(records)
        or header is None
        or sum(isinstance(r, JournalHeader) for r in parsed) != 1
    ):
        errors.append(core_error(ErrorCode.JOURNAL_INCOMPLETE, {"reason": "header"}))

    if any(record.sequence != index for index, record in enumerate(parsed)):
        errors.append(core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "non_contiguous_sequence"}))

    stops = [
        (index, record) for index, record in enumerate(parsed) if isinstance(record, JournalStop)
    ]
    stopped = stops[0][1] if len(stops) == 1 else None
    if stopped is None or stops[0][0] != len(parsed) - 1:
        errors.append(
            core_error(ErrorCode.JOURNAL_INCOMPLETE, {"reason": "successful_stop_missing"})
        )
    if len(stops) != 1:
        errors.append(core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "stop_count"}))
    elif stops[0][0] != len(parsed) - 1:
        errors.append(core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "record_after_stop"}))
    elif stopped is not None and (
        stopped.output_result != "success" or stopped.file_splitting_detected
    ):
        errors.append(
            core_error(
                ErrorCode.JOURNAL_OUTPUT_FAILURE,
                {"output_result": stopped.output_result, "split": stopped.file_splitting_detected},
            )
        )

    if any(isinstance(record, JournalOutputError | JournalRecovery) for record in parsed):
        errors.append(core_error(ErrorCode.JOURNAL_OUTPUT_FAILURE, {"reason": "failure_record"}))
    if any(
        isinstance(record, JournalSplitStatus)
        and (record.split_requested or record.file_splitting_detected)
        for record in parsed
    ):
        errors.append(core_error(ErrorCode.JOURNAL_OUTPUT_FAILURE, {"reason": "split_detected"}))

    event_ids = [
        record.event_id
        for record in parsed
        if isinstance(record, JournalEvent | JournalPause | JournalResume)
    ]
    if any(count > 1 for count in Counter(event_ids).values()):
        errors.append(core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "duplicate_event_id"}))

    paths = [header.initial_output_path] if header is not None else []
    paths.extend(record.output_path for record in parsed if isinstance(record, JournalPathSnapshot))
    if stopped is not None:
        paths.append(stopped.last_recording_path)
    normalized_paths = {str(PureWindowsPath(path)).casefold() for path in paths}
    split_marked = any(
        isinstance(record, JournalSplitStatus)
        and (record.split_requested or record.file_splitting_detected)
        for record in parsed
    ) or (stopped is not None and stopped.file_splitting_detected)
    if len(normalized_paths) > 1 and not split_marked:
        errors.append(
            core_error(ErrorCode.JOURNAL_OUTPUT_FAILURE, {"reason": "unmarked_path_change"})
        )

    paused = False
    pause_counter = 0
    previous_monotonic_ns: int | None = None
    previous_counter: int | None = None
    for record in parsed:
        clock = _record_clock(record)
        if clock is not None:
            monotonic_ns, counter = clock
            if previous_monotonic_ns is not None and monotonic_ns < previous_monotonic_ns:
                errors.append(core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "qpc_regression"}))
            if (
                previous_counter is not None
                and counter is not None
                and not paused
                and counter < previous_counter
            ):
                errors.append(
                    core_error(ErrorCode.JOURNAL_SEQUENCE, {"reason": "counter_regression"})
                )
            previous_monotonic_ns = monotonic_ns
            if counter is not None:
                previous_counter = counter
        if isinstance(record, JournalPause):
            if paused:
                errors.append(
                    core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"reason": "double_pause"})
                )
            paused = True
            pause_counter = record.output_frame_count
        elif isinstance(record, JournalResume):
            if not paused:
                errors.append(
                    core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"reason": "resume_without_pause"})
                )
            elif record.output_frame_count - pause_counter not in range(3):
                errors.append(
                    core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"reason": "pause_counter_moved"})
                )
            paused = False
        elif (
            isinstance(
                record,
                JournalEvent
                | JournalCalibrationSample
                | JournalPathSnapshot
                | JournalSplitStatus
                | JournalOutputError
                | JournalStop,
            )
            and record.recording_paused != paused
        ):
            errors.append(
                core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"reason": "wrong_paused_flag"})
            )
        elif (
            paused
            and clock is not None
            and clock[1] is not None
            and clock[1] - pause_counter not in range(3)
        ):
            errors.append(
                core_error(ErrorCode.SIDECAR_PAUSE_SEQUENCE, {"reason": "pause_counter_moved"})
            )

    return JournalValidationResult(
        valid=not errors, recording_session_id=session_id, errors=tuple(errors)
    )
