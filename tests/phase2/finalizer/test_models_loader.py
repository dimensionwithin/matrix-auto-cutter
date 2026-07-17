from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError
from tests.phase2.finalizer.conftest import (
    SESSION_ID,
    add_bundle,
    add_validated_file,
    journal_bytes,
    journal_records,
    loaded_legacy,
    make_intent,
)

from matrix_auto_cutter.models import _json_mapping_payload
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.finalizer import JournalInputPaths, JournalInputProfile
from matrix_auto_cutter.phase2.finalizer.errors import FinalizerErrorCode
from matrix_auto_cutter.phase2.finalizer.loader import (
    JournalLoadFailed,
    LoadedJournal,
    _parse_journal,
    _reference_matches,
    _unique_object,
    load_journal,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    MAX_INTENT_BYTES,
    BundleComponent,
    FinalizationIntent,
    FinalizationReceipt,
    RecordingJournalBundle,
    RecordingJournalIntegrity,
    parse_canonical,
    parse_intent_bytes,
    strict_artifact_bytes,
)


def _rewrite(port, path: str, update) -> None:
    key = port._key(path)
    value = json.loads(bytes(port.nodes[key].data))
    update(value)
    port.nodes[key].data[:] = (_json_mapping_payload(value) + "\n").encode()


def _rewrite_manifest(port, update) -> None:
    path = r"C:\Input\journal-bundle.json"
    key = port._key(path)
    value = json.loads(bytes(port.nodes[key].data))
    update(value)
    digest_payload = {key: item for key, item in value.items() if key != "bundle_manifest_digest"}
    value["bundle_manifest_digest"] = hashlib.sha256(
        b"matrix-journal-bundle/1.0\0" + _json_mapping_payload(digest_payload).encode()
    ).hexdigest()
    port.nodes[key].data[:] = (_json_mapping_payload(value) + "\n").encode()


def test_legacy_profile_is_explicit_bounded_and_provenance_unavailable(fake_port) -> None:
    path = add_validated_file(fake_port, r"C:\Input\recording.ndjson", journal_bytes())
    before = bytes(fake_port.nodes[fake_port._key(path.canonical_dos_path)].data)
    result = load_journal(
        fake_port,
        JournalInputProfile.LEGACY,
        JournalInputPaths(path),
        expected_recording_id=SESSION_ID,
    )
    assert isinstance(result, LoadedJournal)
    assert result.bundle_binding.status == "not_available"
    assert result.canonical_bytes == before
    assert bytes(fake_port.nodes[fake_port._key(path.canonical_dos_path)].data) == before
    assert _reference_matches("recording.ndjson", path)
    assert not _reference_matches("elsewhere.ndjson", path)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"\xef\xbb\xbf{}\n", FinalizerErrorCode.JOURNAL_CORRUPT),
        (journal_bytes()[:-1], FinalizerErrorCode.JOURNAL_INCOMPLETE),
        (b"\n", FinalizerErrorCode.JOURNAL_CORRUPT),
        (b"{bad}\n", FinalizerErrorCode.JOURNAL_CORRUPT),
        (b'{"a":1,"a":1}\n', FinalizerErrorCode.JOURNAL_CORRUPT),
        (b"\xff\n", FinalizerErrorCode.JOURNAL_CORRUPT),
        (b'{"sequence": 0, "record_type":"header"}\n', FinalizerErrorCode.JOURNAL_CORRUPT),
    ],
)
def test_legacy_rejects_encoding_structure_and_canonicality(data, code) -> None:
    result = _parse_journal(data, None)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is code


def test_legacy_missing_final_lf_has_specific_primary_error() -> None:
    result = _parse_journal(b"{}", None)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.phase == "journal.ndjson"


def test_legacy_limits_and_phase1_primary_codes(monkeypatch) -> None:
    import matrix_auto_cutter.phase2.finalizer.loader as module

    monkeypatch.setattr(module, "MAX_JOURNAL_RECORDS", 2)
    assert isinstance(_parse_journal(journal_bytes(), None), JournalLoadFailed)
    monkeypatch.setattr(module, "MAX_JOURNAL_RECORDS", 1_000_000)
    monkeypatch.setattr(module, "MAX_JOURNAL_LINE_BYTES", 10)
    assert isinstance(_parse_journal(journal_bytes(), None), JournalLoadFailed)
    monkeypatch.setattr(module, "MAX_JOURNAL_LINE_BYTES", 64 * 1024)

    records = list(journal_records())
    records[1] = {**records[1], "sequence": 9}
    failed = _parse_journal(journal_bytes(tuple(records)), None)
    assert isinstance(failed, JournalLoadFailed)
    assert failed.error.code is FinalizerErrorCode.JOURNAL_SEQUENCE

    records = list(journal_records())
    records[-1] = {**records[-1], "output_result": "failure"}
    failed = _parse_journal(journal_bytes(tuple(records)), None)
    assert isinstance(failed, JournalLoadFailed)
    assert failed.error.code is FinalizerErrorCode.JOURNAL_OUTPUT_FAILURE

    failed = _parse_journal(journal_bytes(), "11111111-1111-4111-8111-111111111111")
    assert isinstance(failed, JournalLoadFailed)
    assert failed.error.code is FinalizerErrorCode.JOURNAL_SOURCE_MISMATCH


def test_unique_object_rejects_duplicate() -> None:
    assert _unique_object([("a", 1)]) == {"a": 1}
    with pytest.raises(ValueError, match="duplicate"):
        _unique_object([("a", 1), ("a", 2)])


def test_missing_and_oversized_journal_are_structured(fake_port, monkeypatch) -> None:
    missing = add_validated_file(fake_port, r"C:\Input\placeholder", b"x")
    del fake_port.nodes[fake_port._key(missing.canonical_dos_path)]
    result = load_journal(fake_port, JournalInputProfile.LEGACY, JournalInputPaths(missing))
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.JOURNAL_INCOMPLETE

    import matrix_auto_cutter.phase2.finalizer.loader as module

    monkeypatch.setattr(module, "MAX_JOURNAL_BYTES", 5)
    path = add_validated_file(fake_port, r"C:\Input\large.ndjson", journal_bytes())
    result = load_journal(fake_port, JournalInputProfile.LEGACY, JournalInputPaths(path))
    assert isinstance(result, JournalLoadFailed)


def test_valid_bundle_cross_binds_all_components(fake_port) -> None:
    paths = add_bundle(fake_port)
    result = load_journal(
        fake_port,
        JournalInputProfile.BUNDLE,
        paths,
        expected_recording_id=SESSION_ID,
    )
    assert isinstance(result, LoadedJournal)
    assert result.profile is JournalInputProfile.BUNDLE
    assert result.bundle_binding.status == "validated"


@pytest.mark.parametrize("missing", ["session_receipt", "integrity_receipt", "bundle_manifest"])
def test_bundle_never_falls_back_when_component_missing(fake_port, missing) -> None:
    paths = add_bundle(fake_port)
    failed_paths = replace(paths, **{missing: None})
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, failed_paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.BUNDLE_MISSING
    legacy = load_journal(
        fake_port,
        JournalInputProfile.LEGACY,
        JournalInputPaths(paths.journal),
    )
    assert isinstance(legacy, LoadedJournal)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(recording_session_id="11111111-1111-4111-8111-111111111111"),
        lambda value: value.update(plugin_run_id="22222222-2222-4222-8222-222222222222"),
        lambda value: value.update(producer_version="other"),
        lambda value: value.update(obs_version="other"),
    ],
)
def test_bundle_rejects_id_and_producer_binding_conflicts(fake_port, mutation) -> None:
    paths = add_bundle(fake_port)
    _rewrite_manifest(fake_port, mutation)
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.BUNDLE_BINDING


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["journal"].update(sha256="f" * 64),
        lambda value: value["journal"].update(size_bytes=1),
        lambda value: value["session_receipt"].update(sha256="f" * 64),
        lambda value: value["integrity_receipt"].update(sha256="f" * 64),
    ],
)
def test_bundle_rejects_digest_and_size_conflicts(fake_port, mutation) -> None:
    paths = add_bundle(fake_port)
    _rewrite_manifest(fake_port, mutation)
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.BUNDLE_DIGEST


def test_bundle_rejects_receipt_digest_and_reference_conflicts(fake_port) -> None:
    paths = add_bundle(fake_port)
    _rewrite(
        fake_port,
        r"C:\Input\journal-integrity.json",
        lambda value: value.update(journal_sha256="f" * 64),
    )
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.BUNDLE_DIGEST

    paths = add_bundle(fake_port, r"C:\Other\recording.ndjson")
    _rewrite_manifest(
        fake_port,
        lambda value: value["journal"].update(safe_reference="elsewhere.ndjson"),
    )
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.BUNDLE_BINDING


def test_bundle_corrupt_unknown_and_noncanonical_artifacts(fake_port) -> None:
    paths = add_bundle(fake_port)
    fake_port.nodes[fake_port._key(paths.session_receipt.canonical_dos_path)].data[:] = b"{}\n"
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code in {
        FinalizerErrorCode.BUNDLE_CORRUPT,
        FinalizerErrorCode.BUNDLE_VERSION,
    }

    paths = add_bundle(fake_port)
    _rewrite(
        fake_port,
        r"C:\Input\journal-session.json",
        lambda value: value.update(extra="forbidden"),
    )
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)


def test_intent_canonical_digest_size_and_strictness(fake_port) -> None:
    intent = make_intent(loaded_legacy(fake_port))
    data = strict_artifact_bytes(intent, MAX_INTENT_BYTES)
    assert parse_intent_bytes(data) == intent
    assert hashlib.sha256(data).hexdigest()
    with pytest.raises(ValueError, match="exceeds"):
        strict_artifact_bytes(intent, 1)
    with pytest.raises(ValueError, match="exceeds"):
        parse_canonical(data, 1, type(intent))
    with pytest.raises(ValueError, match="canonical UTF-8"):
        parse_intent_bytes(data[:-1])
    with pytest.raises(ValueError, match="canonical"):
        parse_intent_bytes(b"\xef\xbb\xbf" + data)
    noncanonical = json.dumps(json.loads(data), indent=2).encode() + b"\n"
    with pytest.raises(ValueError, match="canonical"):
        parse_intent_bytes(noncanonical)
    with pytest.raises(ValidationError):
        type(intent).model_validate({**intent.model_dump(), "unknown": True})
    with pytest.raises(ValidationError):
        type(intent).model_validate({**intent.model_dump(), "finalization_key": "f" * 64})
    assert canonical_bytes(intent) == data


def test_self_digest_and_profile_binding_validators_reject(fake_port) -> None:
    component = BundleComponent(
        artifact_type="recording_event_journal",
        schema_version="1.0",
        safe_reference="journal.ndjson",
        size_bytes=1,
        sha256="a" * 64,
    )
    with pytest.raises(ValidationError, match="manifest digest"):
        RecordingJournalBundle(
            recording_session_id=SESSION_ID,
            plugin_run_id="11111111-1111-4111-8111-111111111111",
            producer_version="1",
            obs_version="1",
            journal=component,
            session_receipt=component.model_copy(
                update={"artifact_type": "recording_journal_session"}
            ),
            integrity_receipt=component.model_copy(
                update={"artifact_type": "recording_journal_integrity"}
            ),
            bundle_manifest_digest="f" * 64,
        )
    intent = make_intent(loaded_legacy(fake_port))
    values = intent.model_dump()
    values["source_identity"] = intent.source_identity
    values["bundle_binding"] = intent.bundle_binding
    values["input_profile"] = JournalInputProfile.BUNDLE
    provisional = FinalizationIntent.model_construct(**values)
    values["finalization_key"] = __import__(
        "matrix_auto_cutter.phase2.finalizer.models",
        fromlist=["finalization_key"],
    ).finalization_key(provisional)
    with pytest.raises(ValidationError, match="bundle binding"):
        FinalizationIntent.model_validate(values)

    values = intent.model_dump()
    values["source_identity"] = intent.source_identity
    values["bundle_binding"] = intent.bundle_binding
    values["source_identity_digest"] = "f" * 64
    provisional = FinalizationIntent.model_construct(**values)
    values["finalization_key"] = __import__(
        "matrix_auto_cutter.phase2.finalizer.models",
        fromlist=["finalization_key"],
    ).finalization_key(provisional)
    with pytest.raises(ValidationError, match="SourceIdentity digest"):
        FinalizationIntent.model_validate(values)


def test_bundle_positions_and_receipt_identity_digest_are_self_validating(fake_port) -> None:
    component = BundleComponent(
        artifact_type="recording_event_journal",
        schema_version="1.0",
        safe_reference="journal.ndjson",
        size_bytes=1,
        sha256="a" * 64,
    )
    provisional = RecordingJournalBundle.model_construct(
        recording_session_id=SESSION_ID,
        plugin_run_id="11111111-1111-4111-8111-111111111111",
        producer_version="1",
        obs_version="1",
        journal=component.model_copy(update={"artifact_type": "recording_journal_session"}),
        session_receipt=component,
        integrity_receipt=component.model_copy(
            update={"artifact_type": "recording_journal_integrity"}
        ),
        bundle_manifest_digest="0" * 64,
    )
    values = provisional.model_dump()
    values["bundle_manifest_digest"] = __import__(
        "matrix_auto_cutter.phase2.finalizer.models",
        fromlist=["bundle_manifest_digest"],
    ).bundle_manifest_digest(provisional)
    with pytest.raises(ValidationError, match="positions"):
        RecordingJournalBundle.model_validate(values)

    intent = make_intent(loaded_legacy(fake_port))
    receipt = FinalizationReceipt(
        project_id=intent.project_id,
        intent_run_id=intent.finalizer_run_id,
        target_generation=intent.target_generation,
        recording_id=intent.recording_id,
        source_identity=intent.source_identity,
        source_identity_digest=intent.source_identity_digest,
        sidecar_path_digest=intent.target_path_digest,
        sidecar_sha256="a" * 64,
        sidecar_size_bytes=1,
        finalizer_run_id=intent.finalizer_run_id,
        finalized_at=intent.finalized_at,
        intent_id=intent.finalization_key,
        intent_digest="b" * 64,
    )
    receipt_values = receipt.model_dump()
    receipt_values["source_identity"] = receipt.source_identity
    receipt_values["source_identity_digest"] = "f" * 64
    with pytest.raises(ValidationError, match="SourceIdentity digest"):
        FinalizationReceipt.model_validate(receipt_values)


@pytest.mark.parametrize(
    "reference",
    ["../recording.ndjson", r"folder\recording.ndjson", "C:recording.ndjson", ".."],
)
def test_bundle_references_are_safe_leaf_names(reference) -> None:
    with pytest.raises(ValidationError, match="safe leaf"):
        BundleComponent(
            artifact_type="recording_event_journal",
            schema_version="1.0",
            safe_reference=reference,
            size_bytes=1,
            sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="safe leaf"):
        RecordingJournalIntegrity(
            recording_session_id=SESSION_ID,
            plugin_run_id="11111111-1111-4111-8111-111111111111",
            journal_reference=reference,
            journal_size_bytes=1,
            journal_sha256="a" * 64,
            session_receipt_digest="b" * 64,
        )


def test_bundle_profile_preserves_journal_primary_error(fake_port) -> None:
    paths = add_bundle(fake_port)
    fake_port.nodes[fake_port._key(paths.journal.canonical_dos_path)].data[:] = b"bad\n"
    result = load_journal(fake_port, JournalInputProfile.BUNDLE, paths)
    assert isinstance(result, JournalLoadFailed)
    assert result.error.code is FinalizerErrorCode.JOURNAL_CORRUPT
