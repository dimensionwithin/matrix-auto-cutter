"""Bounded explicit-profile journal and bundle loader."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PureWindowsPath

from matrix_auto_cutter.errors import ErrorCode as CoreErrorCode
from matrix_auto_cutter.journal import validate_journal
from matrix_auto_cutter.models import _json_mapping_payload
from matrix_auto_cutter.phase2.finalizer.errors import (
    FinalizerErrorCategory,
    FinalizerErrorCode,
    FinalizerFailure,
    failure,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    MAX_BUNDLE_COMPONENT_BYTES,
    MAX_JOURNAL_BYTES,
    MAX_JOURNAL_LINE_BYTES,
    MAX_JOURNAL_RECORDS,
    BundleBinding,
    JournalInputProfile,
    RecordingJournalBundle,
    RecordingJournalIntegrity,
    RecordingJournalSession,
    UnavailableProvenance,
    parse_canonical,
)
from matrix_auto_cutter.phase2.pathing import SecureReadFailed, ValidatedPath, secure_read_file
from matrix_auto_cutter.phase2.win32_port import Win32Port


@dataclass(frozen=True, slots=True)
class JournalInputPaths:
    """Explicit already-validated paths for one selected input profile."""

    journal: ValidatedPath
    session_receipt: ValidatedPath | None = None
    integrity_receipt: ValidatedPath | None = None
    bundle_manifest: ValidatedPath | None = None


@dataclass(frozen=True, slots=True)
class LoadedJournal:
    """Bounded Phase-1-validated journal plus optional bundle provenance."""

    profile: JournalInputProfile
    records: tuple[dict[str, object], ...]
    canonical_bytes: bytes
    sha256: str
    size_bytes: int
    recording_id: str
    bundle_binding: BundleBinding | UnavailableProvenance


@dataclass(frozen=True, slots=True)
class JournalLoadFailed:
    """Structured failure returned without mutating any journal input."""

    error: FinalizerFailure


type JournalLoadResult = LoadedJournal | JournalLoadFailed


def _bounded_read(
    port: Win32Port,
    path: ValidatedPath | None,
    maximum: int,
    phase: str,
) -> bytes | JournalLoadFailed:
    if path is None:
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.BUNDLE_MISSING,
                FinalizerErrorCategory.INPUT,
                phase,
                "required bundle component is missing",
            )
        )
    result = secure_read_file(port, path, maximum)
    if isinstance(result, SecureReadFailed):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.BUNDLE_MISSING,
                FinalizerErrorCategory.IO,
                phase,
                result.error.message,
                win32_code=result.error.win32_code,
                cause=result.error.cause,
                underlying=result.error,
            )
        )
    return result.data


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _journal_error(records: tuple[dict[str, object], ...]) -> FinalizerFailure | None:
    validated = validate_journal(records)
    if validated.valid:
        return None
    core = validated.errors[0]
    mapping = {
        CoreErrorCode.JOURNAL_INCOMPLETE: FinalizerErrorCode.JOURNAL_INCOMPLETE,
        CoreErrorCode.JOURNAL_SEQUENCE: FinalizerErrorCode.JOURNAL_SEQUENCE,
        CoreErrorCode.JOURNAL_OUTPUT_FAILURE: FinalizerErrorCode.JOURNAL_OUTPUT_FAILURE,
    }
    return failure(
        mapping.get(core.code, FinalizerErrorCode.JOURNAL_CORRUPT),
        FinalizerErrorCategory.INPUT,
        "journal.phase1_validation",
        core.user_text_de,
        underlying=validated,
    )


def _parse_journal(
    data: bytes, expected_recording_id: str | None
) -> LoadedJournal | JournalLoadFailed:
    if data.startswith(b"\xef\xbb\xbf"):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.JOURNAL_CORRUPT,
                FinalizerErrorCategory.INPUT,
                "journal.encoding",
                "journal UTF-8 BOM is forbidden",
            )
        )
    if not data.endswith(b"\n"):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.JOURNAL_INCOMPLETE,
                FinalizerErrorCategory.INPUT,
                "journal.ndjson",
                "journal must end after a complete LF-terminated record",
            )
        )
    lines = data[:-1].split(b"\n")
    if not lines or len(lines) > MAX_JOURNAL_RECORDS:
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.JOURNAL_CORRUPT,
                FinalizerErrorCategory.INPUT,
                "journal.record_limit",
                "journal record count is outside the bounded contract",
            )
        )
    records: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        if not line or len(line) > MAX_JOURNAL_LINE_BYTES:
            return JournalLoadFailed(
                failure(
                    FinalizerErrorCode.JOURNAL_CORRUPT,
                    FinalizerErrorCategory.INPUT,
                    "journal.line_limit",
                    f"journal line {index} is empty or exceeds 64 KiB",
                )
            )
        try:
            value = json.loads(
                line,
                parse_float=Decimal,
                object_pairs_hook=_unique_object,
            )
            if not isinstance(value, dict) or _json_mapping_payload(value).encode("utf-8") != line:
                raise ValueError("journal line is not canonical JSON")
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return JournalLoadFailed(
                failure(
                    FinalizerErrorCode.JOURNAL_CORRUPT,
                    FinalizerErrorCategory.INPUT,
                    "journal.parse",
                    f"invalid journal record {index}: {exc}",
                    cause=exc,
                )
            )
        records.append(value)
    record_tuple = tuple(records)
    invalid = _journal_error(record_tuple)
    if invalid is not None:
        return JournalLoadFailed(invalid)
    recording_id = str(record_tuple[0]["recording_session_id"])
    if expected_recording_id is not None and recording_id != expected_recording_id:
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.JOURNAL_SOURCE_MISMATCH,
                FinalizerErrorCategory.INTEGRITY,
                "journal.recording_id",
                "journal recording ID differs from the requested recording",
            )
        )
    return LoadedJournal(
        JournalInputProfile.LEGACY,
        record_tuple,
        data,
        hashlib.sha256(data).hexdigest(),
        len(data),
        recording_id,
        UnavailableProvenance(),
    )


def _reference_matches(reference: str, path: ValidatedPath) -> bool:
    return PureWindowsPath(reference).name == PureWindowsPath(path.canonical_dos_path).name


def load_journal(
    port: Win32Port,
    profile: JournalInputProfile,
    paths: JournalInputPaths,
    *,
    expected_recording_id: str | None = None,
) -> JournalLoadResult:
    """Load exactly the explicitly requested profile without fallback."""
    journal_data = _bounded_read(port, paths.journal, MAX_JOURNAL_BYTES, "journal.read")
    if isinstance(journal_data, JournalLoadFailed):
        error = journal_data.error
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.JOURNAL_INCOMPLETE,
                error.category,
                error.phase,
                error.message,
                win32_code=error.win32_code,
                underlying=error,
            )
        )
    parsed = _parse_journal(journal_data, expected_recording_id)
    if isinstance(parsed, JournalLoadFailed) or profile is JournalInputProfile.LEGACY:
        return parsed
    session_data = _bounded_read(
        port, paths.session_receipt, MAX_BUNDLE_COMPONENT_BYTES, "bundle.session"
    )
    integrity_data = _bounded_read(
        port, paths.integrity_receipt, MAX_BUNDLE_COMPONENT_BYTES, "bundle.integrity"
    )
    manifest_data = _bounded_read(
        port, paths.bundle_manifest, MAX_BUNDLE_COMPONENT_BYTES, "bundle.manifest"
    )
    for value in (session_data, integrity_data, manifest_data):
        if isinstance(value, JournalLoadFailed):
            return value
    assert isinstance(session_data, bytes)
    assert isinstance(integrity_data, bytes)
    assert isinstance(manifest_data, bytes)
    try:
        session = parse_canonical(
            session_data,
            MAX_BUNDLE_COMPONENT_BYTES,
            RecordingJournalSession,
        )
        integrity = parse_canonical(
            integrity_data,
            MAX_BUNDLE_COMPONENT_BYTES,
            RecordingJournalIntegrity,
        )
        manifest = parse_canonical(
            manifest_data,
            MAX_BUNDLE_COMPONENT_BYTES,
            RecordingJournalBundle,
        )
    except (UnicodeError, ValueError) as exc:
        code = (
            FinalizerErrorCode.BUNDLE_VERSION
            if b"schema_version" in manifest_data or b"bundle_schema_version" in manifest_data
            else FinalizerErrorCode.BUNDLE_CORRUPT
        )
        return JournalLoadFailed(
            failure(code, FinalizerErrorCategory.INPUT, "bundle.parse", str(exc), cause=exc)
        )
    digests = {
        "journal": hashlib.sha256(journal_data).hexdigest(),
        "session": hashlib.sha256(session_data).hexdigest(),
        "integrity": hashlib.sha256(integrity_data).hexdigest(),
    }
    header = parsed.records[0]
    if (
        session.recording_session_id != parsed.recording_id
        or integrity.recording_session_id != parsed.recording_id
        or manifest.recording_session_id != parsed.recording_id
        or len({session.plugin_run_id, integrity.plugin_run_id, manifest.plugin_run_id}) != 1
        or session.producer_version != manifest.producer_version
        or session.obs_version != manifest.obs_version
        or header.get("producer")
        != {
            "name": session.producer_name,
            "obs_version": session.obs_version,
            "version": session.producer_version,
        }
    ):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.BUNDLE_BINDING,
                FinalizerErrorCategory.INTEGRITY,
                "bundle.binding",
                "bundle IDs or producer versions are inconsistent",
            )
        )
    if (
        integrity.journal_size_bytes != len(journal_data)
        or integrity.journal_sha256 != digests["journal"]
        or integrity.session_receipt_digest != digests["session"]
        or manifest.journal.size_bytes != len(journal_data)
        or manifest.journal.sha256 != digests["journal"]
        or manifest.session_receipt.size_bytes != len(session_data)
        or manifest.session_receipt.sha256 != digests["session"]
        or manifest.integrity_receipt.size_bytes != len(integrity_data)
        or manifest.integrity_receipt.sha256 != digests["integrity"]
    ):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.BUNDLE_DIGEST,
                FinalizerErrorCategory.INTEGRITY,
                "bundle.digest",
                "bundle size or digest binding differs",
            )
        )
    assert paths.session_receipt is not None
    assert paths.integrity_receipt is not None
    if not (
        _reference_matches(integrity.journal_reference, paths.journal)
        and _reference_matches(manifest.journal.safe_reference, paths.journal)
        and _reference_matches(manifest.session_receipt.safe_reference, paths.session_receipt)
        and _reference_matches(manifest.integrity_receipt.safe_reference, paths.integrity_receipt)
    ):
        return JournalLoadFailed(
            failure(
                FinalizerErrorCode.BUNDLE_BINDING,
                FinalizerErrorCategory.INTEGRITY,
                "bundle.reference",
                "bundle safe references differ from the supplied artifacts",
            )
        )
    return LoadedJournal(
        profile,
        parsed.records,
        parsed.canonical_bytes,
        parsed.sha256,
        parsed.size_bytes,
        parsed.recording_id,
        BundleBinding(
            plugin_run_id=str(session.plugin_run_id),
            session_receipt_digest=digests["session"],
            integrity_receipt_digest=digests["integrity"],
            bundle_manifest_digest=manifest.bundle_manifest_digest,
            producer_version=session.producer_version,
            obs_version=session.obs_version,
        ),
    )
