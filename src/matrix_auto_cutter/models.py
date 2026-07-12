"""Gemeinsame strikte kanonische Vertragsmodelle."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    UUID4,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    model_validator,
)
from pydantic.main import IncEx


def _decimal_json_lexeme(value: Decimal) -> str:
    if not value.is_finite():
        msg = "Kanonische JSON-Dezimalwerte müssen endlich sein."
        raise ValueError(msg)
    if value.is_zero():
        return "0"
    return format(value, "f")


def _canonical_json_mapping(
    value: Mapping[object, object],
    *,
    ensure_ascii: bool,
    indent: int | None,
    level: int,
    active_containers: set[int],
    path: str,
) -> str:
    identity = id(value)
    if identity in active_containers:
        msg = f"Kanonische JSON-Strukturen dürfen keine Containerzyklen enthalten: {path}."
        raise ValueError(msg)
    active_containers.add(identity)
    try:
        items = tuple(sorted(value.items()))
        if not items:
            return "{}"
        if indent is None:
            return (
                "{"
                + ",".join(
                    json.dumps(key, ensure_ascii=ensure_ascii)
                    + ":"
                    + _canonical_json_value(
                        item,
                        ensure_ascii=ensure_ascii,
                        indent=indent,
                        level=level + 1,
                        active_containers=active_containers,
                        path=f"{path}.{key}",
                    )
                    for key, item in items
                )
                + "}"
            )
        padding = " " * (max(indent, 0) * (level + 1))
        closing = " " * (max(indent, 0) * level)
        return (
            "{\n"
            + ",\n".join(
                padding
                + json.dumps(key, ensure_ascii=ensure_ascii)
                + ": "
                + _canonical_json_value(
                    item,
                    ensure_ascii=ensure_ascii,
                    indent=indent,
                    level=level + 1,
                    active_containers=active_containers,
                    path=f"{path}.{key}",
                )
                for key, item in items
            )
            + "\n"
            + closing
            + "}"
        )
    finally:
        active_containers.remove(identity)


def _canonical_json_sequence(
    value: Sequence[object],
    *,
    ensure_ascii: bool,
    indent: int | None,
    level: int,
    active_containers: set[int],
    path: str,
) -> str:
    identity = id(value)
    if identity in active_containers:
        msg = f"Kanonische JSON-Strukturen dürfen keine Containerzyklen enthalten: {path}."
        raise ValueError(msg)
    active_containers.add(identity)
    try:
        if not value:
            return "[]"
        if indent is None:
            return (
                "["
                + ",".join(
                    _canonical_json_value(
                        item,
                        ensure_ascii=ensure_ascii,
                        indent=indent,
                        level=level + 1,
                        active_containers=active_containers,
                        path=f"{path}[{index}]",
                    )
                    for index, item in enumerate(value)
                )
                + "]"
            )
        padding = " " * (max(indent, 0) * (level + 1))
        closing = " " * (max(indent, 0) * level)
        return (
            "[\n"
            + ",\n".join(
                padding
                + _canonical_json_value(
                    item,
                    ensure_ascii=ensure_ascii,
                    indent=indent,
                    level=level + 1,
                    active_containers=active_containers,
                    path=f"{path}[{index}]",
                )
                for index, item in enumerate(value)
            )
            + "\n"
            + closing
            + "]"
        )
    finally:
        active_containers.remove(identity)


def _canonical_json_value(
    value: object,
    *,
    ensure_ascii: bool,
    indent: int | None,
    level: int,
    active_containers: set[int] | None = None,
    path: str = "$",
) -> str:
    if active_containers is None:
        active_containers = set()
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = "Kanonische JSON-Fließkommawerte müssen endlich sein."
            raise ValueError(msg)
        return repr(value)
    if isinstance(value, Decimal):
        return _decimal_json_lexeme(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=ensure_ascii)
    if isinstance(value, UUID):
        return json.dumps(str(value), ensure_ascii=ensure_ascii)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            msg = "Kanonische JSON-Zeitstempel müssen eine Zeitzone besitzen."
            raise ValueError(msg)
        return json.dumps(value.isoformat(), ensure_ascii=ensure_ascii)
    if isinstance(value, bytes | bytearray | memoryview):
        msg = (
            f"Wert vom Typ {type(value).__name__} ist am JSON-Pfad {path} "
            "nicht kanonisch JSON-serialisierbar."
        )
        raise TypeError(msg)
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            msg = "Kanonische JSON-Objektschlüssel müssen Strings sein."
            raise TypeError(msg)
        return _canonical_json_mapping(
            value,
            ensure_ascii=ensure_ascii,
            indent=indent,
            level=level,
            active_containers=active_containers,
            path=path,
        )
    if isinstance(value, Sequence):
        return _canonical_json_sequence(
            value,
            ensure_ascii=ensure_ascii,
            indent=indent,
            level=level,
            active_containers=active_containers,
            path=path,
        )
    msg = f"Wert vom Typ {type(value).__name__} ist nicht kanonisch JSON-serialisierbar."
    raise TypeError(msg)


def _restore_exact_decimals(parsed: object, validated: object) -> object:
    if isinstance(validated, Decimal):
        if isinstance(parsed, bool) or not isinstance(parsed, int | Decimal):
            return validated
        return Decimal(parsed)
    if isinstance(parsed, Mapping) and isinstance(validated, Mapping):
        return {
            key: _restore_exact_decimals(parsed[key], item) if key in parsed else item
            for key, item in validated.items()
        }
    if (
        isinstance(parsed, Sequence)
        and not isinstance(parsed, str | bytes | bytearray)
        and isinstance(validated, Sequence)
        and not isinstance(validated, str | bytes | bytearray)
        and len(parsed) == len(validated)
    ):
        restored = [
            _restore_exact_decimals(parsed_item, validated_item)
            for parsed_item, validated_item in zip(parsed, validated, strict=True)
        ]
        return tuple(restored) if isinstance(validated, tuple) else restored
    return validated


class CanonicalModel(BaseModel):
    """Strikte, unveränderliche Basis aller kanonischen JSON-Verträge."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="always",
        arbitrary_types_allowed=True,
    )

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Revalidiere Instanzen und bewahre dabei deren gesetzte Feldmenge."""
        validated = super().model_validate(
            obj,
            strict=strict,
            extra=extra,
            from_attributes=from_attributes,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        if isinstance(obj, cls):
            object.__setattr__(validated, "__pydantic_fields_set__", obj.model_fields_set.copy())
        return validated

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        """Kopiere kanonische Modelle nur mit vollständiger Revalidation."""
        copied = super().model_copy(update=update, deep=deep)
        return type(self).model_validate(copied)

    def model_dump(
        self,
        *,
        mode: Literal["json", "python"] | str = "python",
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[object], object] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> dict[str, object]:
        """Sperre JSON-Mappings; kanonisches JSON liefert nur ``model_dump_json()``."""
        if mode == "json":
            msg = "model_dump(mode='json') ist gesperrt; verwende model_dump_json()."
            raise ValueError(msg)
        return super().model_dump(
            mode=mode,
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: Literal["allow", "ignore", "forbid"] | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validiere JSON mit exakt erhaltenen Decimal-Zahllexemen."""
        validated = super().model_validate_json(
            json_data,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )
        parsed = json.loads(json_data, parse_float=Decimal)
        runtime = validated.model_dump(mode="python", exclude_unset=True, round_trip=True)
        exact_runtime = _restore_exact_decimals(parsed, runtime)
        return cls.model_validate(
            exact_runtime,
            strict=strict,
            extra=extra,
            context=context,
            by_alias=by_alias,
            by_name=by_name,
        )

    def model_dump_json(
        self,
        *,
        indent: int | None = None,
        ensure_ascii: bool = False,
        include: IncEx | None = None,
        exclude: IncEx | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        exclude_computed_fields: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        fallback: Callable[[object], object] | None = None,
        serialize_as_any: bool = False,
        polymorphic_serialization: bool | None = None,
    ) -> str:
        """Serialisiere kanonisch ohne binäre Floatkonvertierung."""
        payload = self.model_dump(
            mode="python",
            include=include,
            exclude=exclude,
            context=context,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            exclude_computed_fields=exclude_computed_fields,
            round_trip=round_trip,
            warnings=warnings,
            fallback=fallback,
            serialize_as_any=serialize_as_any,
            polymorphic_serialization=polymorphic_serialization,
        )
        return _canonical_json_value(
            payload,
            ensure_ascii=ensure_ascii,
            indent=indent,
            level=0,
        )


Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def _strict_uuid4_input(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            msg = "UUID-Werte müssen syntaktisch gültige UUIDv4-Strings sein."
            raise ValueError(msg) from exc
    msg = "UUID-Werte müssen als String oder UUID-Objekt übergeben werden."
    raise ValueError(msg)


CanonicalUuid4 = Annotated[
    UUID4,
    BeforeValidator(_strict_uuid4_input),
    WithJsonSchema({"type": "string", "format": "uuid"}),
]


class _JsonInputError(ValueError):
    """Erwartbarer Fehler für Werte, die kein JSON-Artefakt darstellen können."""


def _ensure_json_compatible(value: object, active_containers: set[int]) -> None:
    if value is None or isinstance(value, str | int | bool):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = "JSON-Zahlen müssen endlich sein."
            raise _JsonInputError(msg)
        return
    if isinstance(value, Decimal):
        if not value.is_finite():
            msg = "JSON-Dezimalzahlen müssen endlich sein."
            raise _JsonInputError(msg)
        return
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active_containers:
            msg = "JSON-Eingaben dürfen keine zyklischen Objekte enthalten."
            raise _JsonInputError(msg)
        active_containers.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    msg = "JSON-Objektschlüssel müssen Strings sein."
                    raise _JsonInputError(msg)
                _ensure_json_compatible(item, active_containers)
        finally:
            active_containers.remove(identity)
        return
    if isinstance(value, list | tuple):
        identity = id(value)
        if identity in active_containers:
            msg = "JSON-Eingaben dürfen keine zyklischen Objekte enthalten."
            raise _JsonInputError(msg)
        active_containers.add(identity)
        try:
            for item in value:
                _ensure_json_compatible(item, active_containers)
        finally:
            active_containers.remove(identity)
        return
    msg = f"Wert vom Typ {type(value).__name__} ist nicht JSON-kompatibel."
    raise _JsonInputError(msg)


def _json_mapping_payload(raw: Mapping[str, object]) -> str:
    """Serialisiere Mapping-Eingaben über denselben präzisen kanonischen Writer."""
    _ensure_json_compatible(raw, set())
    return _canonical_json_value(dict(raw), ensure_ascii=False, indent=None, level=0)


def _strict_decimal_input(value: object) -> object:
    if isinstance(value, str | bool):
        msg = "Dezimalwerte müssen als JSON-Zahl beziehungsweise Decimal übergeben werden."
        raise ValueError(msg)
    if isinstance(value, int | float):
        return Decimal(str(value))
    return value


DecimalMax500 = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input),
    Field(ge=0, le=500),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 500}),
]
DecimalMax50 = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input),
    Field(ge=0, le=50),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 50}),
]
DecimalMax250 = Annotated[
    Decimal,
    BeforeValidator(_strict_decimal_input),
    Field(ge=0, le=250),
    WithJsonSchema({"type": "number", "minimum": 0, "maximum": 250}),
]


class FrameRateModel(CanonicalModel):
    """Serialisierbare 60/1-Zeitbasis."""

    fps_num: Literal[60] = 60
    fps_den: Literal[1] = 1


class SourceBinding(StrEnum):
    """Zulässige, bereits extern verifizierte Bindungsarten."""

    DIRECT_MP4 = "direct_mp4"
    OBS_AUTO_REMUX = "obs_auto_remux"
    MANUAL_REMUX = "manual_remux"
    RENAMED_REBIND = "renamed_rebind"


class SourceIdentity(CanonicalModel):
    """Vom späteren Medienmodul gelieferte, hier nur geprüfte Identität."""

    file_name: str = Field(pattern=r"^[^/\\]+\.mp4$")
    size_bytes: int = Field(ge=1)
    sha256: Sha256
    duration_ms: int = Field(ge=1)
    video_frame_count: int = Field(ge=1)
    fps_num: Literal[60]
    fps_den: Literal[1]
    video_start_time_ns: int
    audio_start_time_ns: int
    binding: SourceBinding


class ProtectionLevel(StrEnum):
    """Priorität eines Schutzbereichs."""

    HARD = "hard"
    SOFT = "soft"


class ProtectionPolicy(CanonicalModel):
    """Orthogonale Bearbeitungsverbote eines Schutzbereichs."""

    blocks_time_edits: bool
    blocks_overlays: bool
    blocks_local_audio_repair: bool
    allows_global_mastering: Literal[True]


class EventProtection(CanonicalModel):
    """Schutzdefinition eines Sidecar-Events."""

    level: ProtectionLevel
    buffer_before_ms: int = Field(ge=0, le=10_000)
    buffer_after_ms: int = Field(ge=0, le=10_000)
    policy: ProtectionPolicy


class ClockSample(CanonicalModel):
    """Rohmessung am Callback-Zeitpunkt."""

    monotonic_ns: int = Field(ge=0)
    output_frame_count: int | None = Field(ge=0)
    mapping_basis: Literal["output_frame_counter", "qpc_fallback"]

    @model_validator(mode="after")
    def basis_matches_counter(self) -> ClockSample:
        """Counterbasis benötigt einen Counter; QPC darf einen besitzen."""
        if self.mapping_basis == "output_frame_counter" and self.output_frame_count is None:
            msg = "Counter-Mapping benötigt output_frame_count."
            raise ValueError(msg)
        return self


class CalibrationSample(CanonicalModel):
    """Probe zur reinen Counter-/QPC-Kalibrierung."""

    monotonic_ns: int = Field(ge=0)
    output_frame_count: int = Field(ge=0)


class PauseMeasurement(CanonicalModel):
    """Reales QPC-Pauseintervall für den Zeitabzug."""

    start_ns: int = Field(ge=0)
    end_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> PauseMeasurement:
        """Fordere ein nichtleeres, aufsteigendes Pauseintervall."""
        if self.start_ns >= self.end_ns:
            msg = "Pause erfordert start_ns < end_ns."
            raise ValueError(msg)
        return self


class MaterializedFrameRange(CanonicalModel):
    """Disjunkt materialisierter Policybereich auf der Source-Timeline."""

    protection_id: str = Field(min_length=1)
    source_start_frame: int = Field(ge=0)
    source_end_frame: int = Field(gt=0)
    level: ProtectionLevel
    source_event_ids: tuple[CanonicalUuid4, ...]
    uncertainty_padding_frames: int = Field(ge=0)
    policy: ProtectionPolicy

    @model_validator(mode="after")
    def nonempty(self) -> MaterializedFrameRange:
        """Fordere ein nichtleeres halboffenes Frameintervall."""
        if self.source_start_frame >= self.source_end_frame:
            msg = "Materialisierter Bereich muss nichtleer sein."
            raise ValueError(msg)
        return self


class FinalizationEvidence(CanonicalModel):
    """Vollständige Nachweise des externen Finalizers."""

    file_closed_verified: Literal[True]
    full_sha256_verified: Literal[True]
    probe_verified: Literal[True]
    journal_complete: Literal[True]
    warnings: tuple[str, ...]


class Lifecycle(CanonicalModel):
    """Finalisierter Sidecar-Lebenszyklus."""

    status: Literal["finalized"]
    journal_schema_version: Literal["1.0"]
    finalized_at: AwareDatetime
    finalizer_run_id: CanonicalUuid4


class Producer(CanonicalModel):
    """Identität von Producer und Finalizer."""

    name: Literal["matrix-auto-cutter-obs-producer"]
    version: str = Field(min_length=1)
    obs_version: str = Field(min_length=1)
    finalizer_version: str = Field(min_length=1)


class ClockCalibration(CanonicalModel):
    """Zusammengefasster, extern erzeugter Kalibrierungsnachweis."""

    origin: Literal["producer_monotonic_at_output_start_signal"]
    monotonic_source: Literal["windows_qpc"]
    mapping: Literal["obs_output_frame_counter_calibrated_to_final_video_frames"]
    counter_start: int = Field(ge=0)
    counter_end: int = Field(ge=1)
    drift_ppm: DecimalMax500
    max_calibration_residual_ms: DecimalMax50
    max_event_uncertainty_ms: DecimalMax250
    calibration_sample_count: int = Field(ge=2)


class PauseInterval(CanonicalModel):
    """Finalisiertes Pauseintervall."""

    pause_event_id: CanonicalUuid4
    close_event_id: CanonicalUuid4
    end_reason: Literal["resumed", "recording_stopped_while_paused"]
    pause_monotonic_ns: int = Field(ge=0)
    end_monotonic_ns: int = Field(ge=0)
    mapped_source_frame_before: int = Field(ge=0)
    mapped_source_frame_after: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent(self) -> PauseInterval:
        """Prüfe QPC-Reihenfolge und erlaubte Counterbewegung."""
        if self.pause_monotonic_ns >= self.end_monotonic_ns:
            msg = "Pauseintervall benötigt aufsteigende QPC-Werte."
            raise ValueError(msg)
        if self.mapped_source_frame_after - self.mapped_source_frame_before not in range(3):
            msg = "Der Framecounter darf während Pause höchstens zwei Frames steigen."
            raise ValueError(msg)
        return self
