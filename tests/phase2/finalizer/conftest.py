from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from matrix_auto_cutter.models import SourceBinding, SourceIdentity, _json_mapping_payload
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.finalizer.loader import (
    JournalInputPaths,
    LoadedJournal,
    load_journal,
)
from matrix_auto_cutter.phase2.finalizer.models import (
    BundleBinding,
    BundleComponent,
    FinalizationIntent,
    JournalInputProfile,
    RecordingJournalBundle,
    RecordingJournalIntegrity,
    RecordingJournalSession,
    UnavailableProvenance,
    bundle_manifest_digest,
    finalization_key,
)
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path

SESSION_ID = "835fc47a-7e8c-4700-9f6f-8f7e23ac740c"
RUN_ID = "2e157a84-2e31-49d9-b64e-494c24f8f612"
START_ID = "bfc5ea5a-593f-4261-8262-6d6e508bc6df"
PLUGIN_RUN_ID = "11111111-1111-4111-8111-111111111111"
TARGET_GENERATION = "22222222-2222-4222-8222-222222222222"
SYNTHETIC_STOP_ID = "44444444-4444-4444-8444-444444444444"
PROJECT_ID = "550e8400-e29b-41d4-a716-446655440000"


def journal_records(source_path: str = r"C:\Sources\source.mp4") -> tuple[dict[str, object], ...]:
    common = {
        "artifact_type": "recording_event_journal",
        "journal_schema_version": "1.0",
    }
    return (
        {
            **common,
            "record_type": "header",
            "sequence": 0,
            "recording_session_id": SESSION_ID,
            "lifecycle_status": "recording",
            "producer": {
                "name": "matrix-auto-cutter-obs-producer",
                "version": "0.1.0",
                "obs_version": "32.2.0",
            },
            "clock": {
                "source": "windows_qpc",
                "unit": "ns",
                "origin": "producer_monotonic_at_output_start_signal",
            },
            "capabilities": {
                "pause_resume": "supported_v1",
                "file_splitting": "unsupported_v1",
            },
            "initial_output_path": source_path,
        },
        {
            **common,
            "record_type": "event",
            "sequence": 1,
            "event_id": START_ID,
            "event_type": "recording_started",
            "monotonic_ns": 0,
            "output_frame_count": 0,
            "recording_paused": False,
        },
        {
            **common,
            "record_type": "stop",
            "sequence": 2,
            "lifecycle_status": "stopped_unfinalized",
            "monotonic_ns": 1_000_000_000,
            "output_frame_count": 60,
            "recording_paused": False,
            "last_recording_path": source_path,
            "output_result": "success",
            "file_splitting_detected": False,
        },
    )


def journal_bytes(records: tuple[dict[str, object], ...] | None = None) -> bytes:
    return (
        "\n".join(_json_mapping_payload(item) for item in (records or journal_records())) + "\n"
    ).encode()


def add_validated_file(port, path: str, data: bytes):
    port.add_file(path, data)
    result = validate_path(port, path, PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(result, PathValidated)
    return result.path


def loaded_legacy(port, path: str = r"C:\Input\recording.ndjson") -> LoadedJournal:
    validated = add_validated_file(port, path, journal_bytes())
    result = load_journal(
        port,
        JournalInputProfile.LEGACY,
        JournalInputPaths(validated),
        expected_recording_id=SESSION_ID,
    )
    assert isinstance(result, LoadedJournal)
    return result


def source_identity() -> SourceIdentity:
    return SourceIdentity(
        file_name="source.mp4",
        size_bytes=115,
        sha256="a" * 64,
        duration_ms=1000,
        video_frame_count=60,
        fps_num=60,
        fps_den=1,
        video_start_time_ns=0,
        audio_start_time_ns=0,
        binding=SourceBinding.DIRECT_MP4,
    )


def make_intent(
    journal: LoadedJournal,
    *,
    source: SourceIdentity | None = None,
    profile: JournalInputProfile = JournalInputProfile.LEGACY,
    bundle: BundleBinding | UnavailableProvenance | None = None,
) -> FinalizationIntent:
    identity = source or source_identity()
    provisional = FinalizationIntent.model_construct(
        finalizer_run_id=RUN_ID,
        finalized_at=datetime(2026, 7, 17, 12, tzinfo=UTC),
        project_id=PROJECT_ID,
        input_profile=profile,
        recording_id=journal.recording_id,
        journal_sha256=journal.sha256,
        journal_size_bytes=journal.size_bytes,
        bundle_binding=bundle or UnavailableProvenance(),
        source_identity=identity,
        source_identity_digest=hashlib.sha256(
            b"matrix-auto-cutter/source-identity/1.0\0" + identity.model_dump_json().encode()
        ).hexdigest(),
        source_identity_evidence_id="b" * 64,
        source_identity_evidence_digest="c" * 64,
        source_volume_id="0000000000000001",
        source_file_id="01" + "00" * 15,
        probe_artifact_id="probe",
        hash_artifact_id="hash",
        assignment_artifact_id="not_available",
        bundle_schema_version="1.0" if profile is JournalInputProfile.BUNDLE else "not_available",
        target_path_digest="d" * 64,
        target_generation=TARGET_GENERATION,
        synthetic_stop_event_id=SYNTHETIC_STOP_ID,
        finalization_key="0" * 64,
    )
    values = provisional.model_dump()
    values["finalization_key"] = finalization_key(provisional)
    return FinalizationIntent.model_validate(values)


def add_bundle(port, journal_path: str = r"C:\Input\recording.ndjson") -> JournalInputPaths:
    journal_data = journal_bytes()
    journal = add_validated_file(port, journal_path, journal_data)
    session = RecordingJournalSession(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        producer_name="matrix-auto-cutter-obs-producer",
        producer_version="0.1.0",
        obs_version="32.2.0",
    )
    session_data = canonical_bytes(session)
    integrity = RecordingJournalIntegrity(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        journal_reference="recording.ndjson",
        journal_size_bytes=len(journal_data),
        journal_sha256=hashlib.sha256(journal_data).hexdigest(),
        session_receipt_digest=hashlib.sha256(session_data).hexdigest(),
    )
    integrity_data = canonical_bytes(integrity)
    provisional = RecordingJournalBundle.model_construct(
        recording_session_id=SESSION_ID,
        plugin_run_id=PLUGIN_RUN_ID,
        producer_version="0.1.0",
        obs_version="32.2.0",
        journal=BundleComponent(
            artifact_type="recording_event_journal",
            schema_version="1.0",
            safe_reference="recording.ndjson",
            size_bytes=len(journal_data),
            sha256=hashlib.sha256(journal_data).hexdigest(),
        ),
        session_receipt=BundleComponent(
            artifact_type="recording_journal_session",
            schema_version="1.0",
            safe_reference="journal-session.json",
            size_bytes=len(session_data),
            sha256=hashlib.sha256(session_data).hexdigest(),
        ),
        integrity_receipt=BundleComponent(
            artifact_type="recording_journal_integrity",
            schema_version="1.0",
            safe_reference="journal-integrity.json",
            size_bytes=len(integrity_data),
            sha256=hashlib.sha256(integrity_data).hexdigest(),
        ),
        bundle_manifest_digest="0" * 64,
    )
    values = provisional.model_dump()
    values["bundle_manifest_digest"] = bundle_manifest_digest(provisional)
    manifest = RecordingJournalBundle.model_validate(values)
    return JournalInputPaths(
        journal,
        add_validated_file(port, r"C:\Input\journal-session.json", session_data),
        add_validated_file(port, r"C:\Input\journal-integrity.json", integrity_data),
        add_validated_file(port, r"C:\Input\journal-bundle.json", canonical_bytes(manifest)),
    )
