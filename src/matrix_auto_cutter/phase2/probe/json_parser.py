"""Bounded strict ffprobe JSON decoding and normalization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.contracts import (
    CanonicalJsonArray,
    CanonicalJsonObject,
    CanonicalJsonValue,
    Disposition,
    ExactTime,
    FormatProfile,
    MediaStream,
    ProgramProfile,
    Rational,
    RotationEvidence,
    SideData,
    StreamType,
    _ascii_fold_name,
)
from matrix_auto_cutter.phase2.probe.errors import ProbeError, ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.probe.numeric_limits import (
    MAX_NUMERIC_LEXEME_CHARS,
    validate_decimal_lexeme,
    validate_integer_lexeme,
)

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_DEPTH = 64
MAX_STRING_CHARS = 1024 * 1024
MAX_ARRAY_ITEMS = 65536
MAX_OBJECT_ITEMS = 4096
MAX_STREAMS = 4096
MAX_PROGRAMS = 4096
MAX_TAGS = 256
MAX_SIDE_DATA = 256
MAX_UNTRUSTED_FIELDS = 256

_KNOWN_STREAM_FIELDS = frozenset(
    {
        "index",
        "codec_name",
        "profile",
        "pix_fmt",
        "codec_type",
        "disposition",
        "time_base",
        "r_frame_rate",
        "avg_frame_rate",
        "start_time",
        "duration",
        "nb_frames",
        "width",
        "height",
        "sample_rate",
        "channels",
        "channel_layout",
        "tags",
        "side_data_list",
    }
)
_KNOWN_STREAM_FOLDED = frozenset(_ascii_fold_name(name) for name in _KNOWN_STREAM_FIELDS)
_KNOWN_DISPOSITIONS = frozenset(
    {
        "default",
        "dub",
        "original",
        "comment",
        "lyrics",
        "karaoke",
        "forced",
        "hearing_impaired",
        "visual_impaired",
        "clean_effects",
        "attached_pic",
        "timed_thumbnails",
        "non_diegetic",
        "captions",
        "descriptions",
        "metadata",
        "dependent",
        "still_image",
        "multilayer",
    }
)
_KNOWN_SIDE_DATA_FIELDS = frozenset({"side_data_type", "rotation", "displaymatrix"})
_KNOWN_SIDE_DATA_FOLDED = frozenset(_ascii_fold_name(name) for name in _KNOWN_SIDE_DATA_FIELDS)


@dataclass(frozen=True, slots=True)
class _JsonInteger:
    """A raw JSON integer lexeme retained until field-aware schema validation."""

    lexeme: str


class _JsonObject(dict[str, object]):
    """Decoded object retaining exact duplicate-key evidence for classification."""

    duplicate_keys: tuple[str, ...]

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        super().__init__()
        duplicates: list[str] = []
        for key, value in pairs:
            if key in self:
                duplicates.append(key)
            self[key] = value
        self.duplicate_keys = tuple(duplicates)


class _InvalidConstant(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedProbeJson:
    """Successful normalized parser output before source/binary binding."""

    format: FormatProfile
    streams: tuple[MediaStream, ...]
    programs: tuple[ProgramProfile, ...]


@dataclass(frozen=True, slots=True)
class ProbeJsonRejected:
    """Strict decode, JSON or schema failure."""

    error: ProbeError


type ProbeJsonResult = ParsedProbeJson | ProbeJsonRejected


def _reject(
    code: ProbeErrorCode, phase: str, message: str, cause: BaseException | None = None
) -> ProbeJsonRejected:
    category = ErrorCategory.INPUT if code is ProbeErrorCode.SCHEMA else ErrorCategory.INTEGRITY
    return ProbeJsonRejected(probe_error(code, category, phase, message, cause=cause))


def _pairs(pairs: list[tuple[str, object]]) -> _JsonObject:
    return _JsonObject(pairs)


def _integer_token(lexeme: str) -> _JsonInteger:
    if len(lexeme) > MAX_NUMERIC_LEXEME_CHARS:
        raise ValueError("JSON integer lexeme exceeds limit")
    return _JsonInteger(lexeme)


def _constant(value: str) -> None:
    raise _InvalidConstant(value)


def _scan_structure(text: str) -> None:
    depth = 0
    in_string = False
    escaped = False
    string_length = 0
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            else:
                string_length += 1
                if string_length > MAX_STRING_CHARS:
                    raise ValueError("JSON string exceeds limit")
            continue
        if character == '"':
            in_string = True
            string_length = 0
        elif character in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValueError("JSON nesting exceeds limit")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("unbalanced JSON structure")
    if in_string or depth != 0:
        raise ValueError("unterminated JSON structure")


def _bound_tree(root: object) -> None:
    stack = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            if len(value) > MAX_OBJECT_ITEMS:
                raise ValueError("JSON object exceeds item limit")
            stack.extend(value.values())
        elif isinstance(value, list):
            if len(value) > MAX_ARRAY_ITEMS:
                raise ValueError("JSON array exceeds item limit")
            stack.extend(value)
        elif isinstance(value, str) and len(value) > MAX_STRING_CHARS:
            raise ValueError("decoded JSON string exceeds limit")


def _has_duplicate(root: object) -> bool:
    stack = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, _JsonObject):
            if value.duplicate_keys:
                return True
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return False


def _has_critical_duplicate(root: object) -> bool:
    if not isinstance(root, dict):
        return False
    streams = root.get("streams")
    if not isinstance(streams, list):
        return False
    known_stream_fields = {_ascii_fold_name(name) for name in _KNOWN_STREAM_FIELDS}
    for stream in streams:
        if not isinstance(stream, _JsonObject):
            continue
        if any(_ascii_fold_name(key) in known_stream_fields for key in stream.duplicate_keys):
            return True
        disposition = stream.get("disposition")
        if isinstance(disposition, _JsonObject) and disposition.duplicate_keys:
            return True
        side_data = stream.get("side_data_list")
        if isinstance(side_data, list):
            for item in side_data:
                if not isinstance(item, _JsonObject):
                    continue
                if any(
                    _ascii_fold_name(key) in _KNOWN_SIDE_DATA_FOLDED for key in item.duplicate_keys
                ):
                    return True
    return False


def _text(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _integer(
    value: object,
    field: str,
    *,
    required: bool = False,
    minimum: int | None = None,
) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    if isinstance(value, _JsonInteger):
        parsed = validate_integer_lexeme(value.lexeme)
    elif isinstance(value, str):
        parsed = validate_integer_lexeme(value, allow_leading_zeroes=True)
    else:
        raise ValueError(f"{field} must be an exact integer")
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} is below its minimum")
    return parsed


def _stream_index(value: object) -> int:
    if not isinstance(value, _JsonInteger):
        raise ValueError("stream.index must be a raw JSON integer")
    parsed = validate_integer_lexeme(value.lexeme)
    if parsed < 0:
        raise ValueError("stream.index must be non-negative")
    return parsed


def _canonical_json_value(value: object, field: str) -> CanonicalJsonValue:
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, _JsonInteger):
        return validate_integer_lexeme(value.lexeme)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, list):
        return CanonicalJsonArray(
            tuple(_canonical_json_value(item, f"{field}[]") for item in value)
        )
    if isinstance(value, dict):
        return CanonicalJsonObject(
            tuple(
                sorted(
                    (key, _canonical_json_value(item, f"{field}.{key}"))
                    for key, item in value.items()
                )
            )
        )
    raise ValueError(f"{field} contains an unsupported JSON value")


def _check_known_field_collisions(value: dict[str, object]) -> None:
    for key in value:
        if key not in _KNOWN_STREAM_FIELDS and _ascii_fold_name(key) in _KNOWN_STREAM_FOLDED:
            raise ValueError(f"stream field {key!r} collides with a known field")


def _decimal(value: object, field: str, *, nonnegative: bool = False) -> ExactTime | None:
    if value is None or value == "N/A":
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a decimal string")
    parsed = validate_decimal_lexeme(value)
    if nonnegative and parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return ExactTime(parsed, value)


def _rational(value: object, field: str, *, allow_zero_zero: bool = False) -> Rational | None:
    if value is None or value == "N/A":
        return None
    if not isinstance(value, str) or value.count("/") != 1:
        raise ValueError(f"{field} must be a rational string")
    numerator_text, denominator_text = value.split("/")
    if not numerator_text or not denominator_text:
        raise ValueError(f"{field} is incomplete")
    try:
        numerator = validate_integer_lexeme(numerator_text, allow_leading_zeroes=True)
        denominator = validate_integer_lexeme(denominator_text, allow_leading_zeroes=True)
    except ValueError as exc:
        raise ValueError(f"{field} has invalid or unbounded integer components") from exc
    if denominator == 0:
        if allow_zero_zero and numerator == 0:
            return None
        raise ValueError(f"{field} denominator must not be zero")
    return Rational(numerator, denominator)


def _tags(value: object, field: str) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or len(value) > MAX_TAGS:
        raise ValueError(f"{field} tags must be a bounded object")
    result: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"{field} tags must contain strings")
        result.append((key, item))
    return tuple(sorted(result))


def _normalize_rotation(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} rotation must be an exact integer")
    if isinstance(value, _JsonInteger):
        parsed = validate_integer_lexeme(value.lexeme)
    elif isinstance(value, str):
        parsed_decimal = validate_decimal_lexeme(value)
        if parsed_decimal != parsed_decimal.to_integral_value():
            raise ValueError(f"{field} rotation must not be rounded")
        parsed = int(parsed_decimal)
    else:
        raise ValueError(f"{field} rotation must be exact")
    if parsed not in {-270, -180, -90, 0, 90, 180, 270}:
        raise ValueError(f"{field} rotation is unsupported")
    return parsed % 360


def _side_data(value: object) -> tuple[SideData, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_SIDE_DATA:
        raise ValueError("side_data_list must be a bounded array")
    result: list[SideData] = []
    for item in value:
        if not isinstance(item, dict) or len(item) > MAX_UNTRUSTED_FIELDS:
            raise ValueError("side-data entry must be a bounded object")
        kind = _text(item.get("side_data_type"), "side_data_type", required=True)
        assert kind is not None
        rotation = _normalize_rotation(item.get("rotation"), "display_matrix")
        matrix = _text(item.get("displaymatrix"), "displaymatrix")
        extras: list[tuple[str, CanonicalJsonValue]] = []
        for key, unknown in item.items():
            if key in _KNOWN_SIDE_DATA_FIELDS:
                continue
            if _ascii_fold_name(key) in _KNOWN_SIDE_DATA_FOLDED:
                raise ValueError(f"side-data field {key!r} collides with a known field")
            extras.append((key, _canonical_json_value(unknown, f"side_data.{key}")))
        result.append(SideData(kind, rotation, matrix, tuple(sorted(extras))))
    return tuple(result)


def _disposition(value: object) -> Disposition:
    if value is None:
        return Disposition(None, None, ())
    if not isinstance(value, dict) or len(value) > MAX_OBJECT_ITEMS:
        raise ValueError("disposition must be an object")
    flags: list[tuple[str, bool]] = []
    for key, item in value.items():
        if key not in _KNOWN_DISPOSITIONS:
            raise ValueError(f"unknown disposition field {key!r}")
        if not isinstance(item, _JsonInteger) or item.lexeme not in {"0", "1"}:
            raise ValueError("disposition values must be raw JSON integers 0 or 1")
        flags.append((key, item.lexeme == "1"))
    flags.sort()
    mapping = dict(flags)
    return Disposition(
        mapping.get("default"),
        mapping.get("attached_pic"),
        tuple(flags),
    )


def _stream_type(raw: str) -> StreamType:
    try:
        return StreamType(raw)
    except ValueError:
        return StreamType.UNKNOWN


def _stream(value: object) -> MediaStream:
    if not isinstance(value, dict) or len(value) > MAX_OBJECT_ITEMS:
        raise ValueError("stream must be a bounded object")
    _check_known_field_collisions(value)
    index = _stream_index(value.get("index"))
    codec_name = _text(value.get("codec_name"), "stream.codec_name")
    codec_type = _text(value.get("codec_type"), "stream.codec_type", required=True)
    assert codec_type is not None
    tags = _tags(value.get("tags"), "stream")
    side_data = _side_data(value.get("side_data_list"))
    matrices = {item.rotation for item in side_data if item.rotation is not None}
    if len(matrices) > 1:
        raise ValueError("conflicting display-matrix rotations")
    matrix_rotation = next(iter(matrices), None)
    matrices_raw = [item.display_matrix for item in side_data if item.display_matrix is not None]
    extra_fields = tuple(
        sorted(
            (key, _canonical_json_value(item, f"stream.{key}"))
            for key, item in value.items()
            if key not in _KNOWN_STREAM_FIELDS
        )
    )
    return MediaStream(
        index=index,
        codec_name=codec_name,
        profile=_text(value.get("profile"), "stream.profile"),
        pix_fmt=_text(value.get("pix_fmt"), "stream.pix_fmt"),
        codec_type_raw=codec_type,
        stream_type=_stream_type(codec_type),
        disposition=_disposition(value.get("disposition")),
        time_base=_rational(value.get("time_base"), "stream.time_base"),
        r_frame_rate=_rational(
            value.get("r_frame_rate"), "stream.r_frame_rate", allow_zero_zero=True
        ),
        avg_frame_rate=_rational(
            value.get("avg_frame_rate"), "stream.avg_frame_rate", allow_zero_zero=True
        ),
        start_time=_decimal(value.get("start_time"), "stream.start_time"),
        duration=_decimal(value.get("duration"), "stream.duration"),
        nb_frames=_integer(value.get("nb_frames"), "stream.nb_frames", minimum=0),
        width=_integer(value.get("width"), "stream.width"),
        height=_integer(value.get("height"), "stream.height"),
        sample_rate=_integer(value.get("sample_rate"), "stream.sample_rate"),
        channels=_integer(value.get("channels"), "stream.channels"),
        channel_layout=_text(value.get("channel_layout"), "stream.channel_layout"),
        tags=tags,
        side_data=side_data,
        rotation=RotationEvidence(None, matrix_rotation, matrices_raw[0] if matrices_raw else None),
        extra_fields=extra_fields,
    )


def _format(value: object) -> FormatProfile:
    if not isinstance(value, dict):
        raise ValueError("format must be an object")
    filename = _text(value.get("filename"), "format.filename", required=True)
    format_name = _text(value.get("format_name"), "format.format_name", required=True)
    assert filename is not None and format_name is not None
    return FormatProfile(
        filename,
        format_name,
        _text(value.get("format_long_name"), "format.format_long_name"),
        _decimal(value.get("start_time"), "format.start_time"),
        _decimal(value.get("duration"), "format.duration", nonnegative=True),
        _integer(value.get("size"), "format.size", minimum=0),
        _integer(value.get("bit_rate"), "format.bit_rate", minimum=0),
        _tags(value.get("tags"), "format"),
    )


def _program(value: object) -> ProgramProfile:
    if not isinstance(value, dict):
        raise ValueError("program must be an object")
    program_id = _integer(value.get("program_id"), "program.program_id", required=True, minimum=0)
    assert program_id is not None
    raw_streams = value.get("streams", [])
    if not isinstance(raw_streams, list) or len(raw_streams) > MAX_STREAMS:
        raise ValueError("program streams must be a bounded array")
    indexes: list[int] = []
    for stream in raw_streams:
        if not isinstance(stream, dict):
            raise ValueError("program stream reference must be an object")
        index = _integer(stream.get("index"), "program.stream.index", required=True, minimum=0)
        assert index is not None
        indexes.append(index)
    return ProgramProfile(program_id, tuple(sorted(indexes)), _tags(value.get("tags"), "program"))


def parse_probe_json(raw: bytes) -> ProbeJsonResult:
    """Strictly decode, bound, schema-check and normalize ffprobe JSON."""
    if len(raw) > MAX_JSON_BYTES:
        return _reject(ProbeErrorCode.INVALID_JSON, "json_size", "stdout exceeds JSON byte limit")
    if raw.startswith(b"\xef\xbb\xbf"):
        return _reject(ProbeErrorCode.INVALID_UTF8, "json_decode", "UTF-8 BOM is forbidden")
    if not raw:
        return _reject(ProbeErrorCode.INVALID_JSON, "json_decode", "empty output is forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return _reject(
            ProbeErrorCode.INVALID_UTF8, "json_decode", "stdout is not strict UTF-8", exc
        )
    try:
        _scan_structure(text)
        root = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=validate_decimal_lexeme,
            parse_int=_integer_token,
            parse_constant=_constant,
        )
        _bound_tree(root)
    except (
        json.JSONDecodeError,
        _InvalidConstant,
        ValueError,
        RecursionError,
    ) as exc:
        return _reject(ProbeErrorCode.INVALID_JSON, "json_parse", str(exc), exc)
    if not isinstance(root, dict):
        return _reject(ProbeErrorCode.SCHEMA, "json_schema", "root must be an object")
    if _has_critical_duplicate(root):
        return _reject(
            ProbeErrorCode.SCHEMA,
            "json_schema",
            "duplicate critical stream field",
        )
    if _has_duplicate(root):
        return _reject(ProbeErrorCode.INVALID_JSON, "json_parse", "duplicate JSON object key")
    if set(root) - {"format", "streams", "programs", "error"}:
        return _reject(ProbeErrorCode.SCHEMA, "json_schema", "unknown top-level structure")
    if "error" in root:
        return _reject(
            ProbeErrorCode.UNSUPPORTED_MEDIA, "json_schema", "ffprobe reported a media error"
        )
    streams = root.get("streams")
    programs = root.get("programs")
    if not isinstance(streams, list) or len(streams) > MAX_STREAMS:
        return _reject(ProbeErrorCode.SCHEMA, "json_schema", "streams must be a bounded array")
    if not isinstance(programs, list) or len(programs) > MAX_PROGRAMS:
        return _reject(ProbeErrorCode.SCHEMA, "json_schema", "programs must be a bounded array")
    try:
        normalized_streams = tuple(
            sorted((_stream(stream) for stream in streams), key=lambda s: s.index)
        )
        indexes = [stream.index for stream in normalized_streams]
        if len(indexes) != len(set(indexes)):
            raise ValueError("stream indexes must be unique")
        normalized_programs = tuple(_program(program) for program in programs)
        known = set(indexes)
        if any(
            index not in known
            for program in normalized_programs
            for index in program.stream_indexes
        ):
            raise ValueError("program references an unknown stream")
        normalized_format = _format(root.get("format"))
    except (ValueError, TypeError) as exc:
        return _reject(ProbeErrorCode.SCHEMA, "json_schema", str(exc), exc)
    return ParsedProbeJson(normalized_format, normalized_streams, normalized_programs)
