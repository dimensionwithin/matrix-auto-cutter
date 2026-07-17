from __future__ import annotations

import hashlib
import json

import pytest
from tests.phase2.source_confirmation.conftest import make_case

import matrix_auto_cutter.phase2.source_confirmation.orchestrator as orchestrator
from matrix_auto_cutter.models import SourceIdentity
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.artifacts import canonical_bytes
from matrix_auto_cutter.phase2.source_confirmation import (
    MAX_MEDIA_PROBE_BYTES,
    MAX_SOURCE_IDENTITY_EVIDENCE_BYTES,
    SourceConfirmationFailed,
    SourceConfirmed,
    confirm_source,
    parse_media_probe_bytes,
    parse_source_identity_evidence_bytes,
)
from matrix_auto_cutter.phase2.source_confirmation.identity import source_identity_digest
from matrix_auto_cutter.phase2.source_confirmation.path_revalidation import PathRevalidated
from matrix_auto_cutter.phase2.source_confirmation.persistence import artifact_target
from matrix_auto_cutter.phase2.source_hash import HashCompleted, hash_lease_source


def _canonical_mutation(data: bytes, key: str, value: object) -> bytes:
    payload = json.loads(data)
    payload[key] = value
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def test_media_probe_and_identity_evidence_strict_canonical_gates() -> None:
    case = make_case()
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        media_data = bytes(
            case.port.nodes[case.port._key(result.evidence.media_probe.canonical_path)].data
        )
        evidence_data = bytes(
            case.port.nodes[case.port._key(result.evidence.source_identity_evidence_path)].data
        )
        assert media_data.endswith(b"\n") and not media_data.startswith(b"\xef\xbb\xbf")
        assert evidence_data.endswith(b"\n") and not evidence_data.startswith(b"\xef\xbb\xbf")
        assert canonical_bytes(parse_media_probe_bytes(media_data)) == media_data
        assert canonical_bytes(parse_source_identity_evidence_bytes(evidence_data)) == evidence_data

        for mutation in (
            _canonical_mutation(media_data, "unknown", True),
            _canonical_mutation(media_data, "schema_version", "9.0"),
            media_data[:-1],
            b"\xef\xbb\xbf" + media_data,
            b"x" * (MAX_MEDIA_PROBE_BYTES + 1),
        ):
            with pytest.raises((UnicodeError, ValueError)):
                parse_media_probe_bytes(mutation)
        for mutation in (
            _canonical_mutation(evidence_data, "unknown", True),
            _canonical_mutation(evidence_data, "schema_version", "9.0"),
            _canonical_mutation(evidence_data, "source_identity_digest", "0" * 64),
            _canonical_mutation(evidence_data, "hash_receipt_digest", "0" * 64),
            evidence_data[:-1],
            b"x" * (MAX_SOURCE_IDENTITY_EVIDENCE_BYTES + 1),
        ):
            with pytest.raises((UnicodeError, ValueError)):
                parse_source_identity_evidence_bytes(mutation)
    finally:
        case.close()


def test_phase1_source_identity_field_set_canonical_digest_and_binding_are_unchanged() -> None:
    case = make_case()
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        identity = result.source_identity
        assert set(SourceIdentity.model_fields) == {
            "file_name",
            "size_bytes",
            "sha256",
            "duration_ms",
            "video_frame_count",
            "fps_num",
            "fps_den",
            "video_start_time_ns",
            "audio_start_time_ns",
            "binding",
        }
        assert SourceIdentity.model_validate_json(identity.model_dump_json()) == identity
        digest = source_identity_digest(identity)
        assert digest == source_identity_digest(identity)
        assert len(digest) == 64
        assert hashlib.sha256(case.source_data).hexdigest() == identity.sha256
    finally:
        case.close()


def test_existing_media_probe_conflict_is_immutable_and_no_hash_starts() -> None:
    case = make_case()
    try:
        target = artifact_target(
            case.port,
            case.project,
            ("probe", case.request.probe_id),
            "media-probe.json",
        )
        assert not hasattr(target, "code")
        original = b"foreign-target\n"
        case.port.add_file(target.canonical_dos_path, original)
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmationFailed)
        assert result.error.code.value == "E_SOURCE_ARTIFACT_CONFLICT"
        assert bytes(case.port.nodes[case.port._key(target.canonical_dos_path)].data) == original
        assert case.port.hash_read_count == 0
        assert not case.port.failures.get("ReplaceFileW")
    finally:
        case.close()


def test_identical_retry_reprobes_and_rehashes_instead_of_trusting_artifacts() -> None:
    case = make_case()
    try:
        first = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(first, SourceConfirmed)
        process_count = len(case.process.calls or [])
        hash_count = case.port.hash_read_count
        second = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(second, SourceConfirmed)
        assert len(case.process.calls or []) == process_count + 1
        assert case.port.hash_read_count > hash_count
        assert first.evidence == second.evidence
    finally:
        case.close()


def test_identity_evidence_is_explicitly_cross_validated_against_current_runtime_chain() -> None:
    case = make_case()
    try:
        result = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(result, SourceConfirmed)
        media = parse_media_probe_bytes(
            bytes(case.port.nodes[case.port._key(result.evidence.media_probe.canonical_path)].data)
        )
        evidence = parse_source_identity_evidence_bytes(
            bytes(
                case.port.nodes[case.port._key(result.evidence.source_identity_evidence_path)].data
            )
        )
        completed = hash_lease_source(
            case.request.lease,
            CancellationToken(),
            case.project.document.project_id,
            case.request.hash_run_id,
        )
        assert isinstance(completed, HashCompleted)
        streams = {item.index: item for item in media.profile.streams}
        selection = orchestrator._BoundSelection(
            streams[evidence.video_index],
            streams[evidence.audio_index],
            evidence.video_reason_code,
            evidence.audio_reason_code,
            evidence.selection_identity,
            evidence.selection_mode,
            evidence.assignment,
        )
        arguments = (
            case.request,
            evidence,
            media,
            result.evidence.media_probe,
            selection,
            completed,
            result.evidence.hash_receipt,
            PathRevalidated(evidence.pre_probe_path_revalidation, case.request.lease.s0),
            PathRevalidated(evidence.pre_commit_path_revalidation, case.request.lease.s0),
            case.request.lease.s0,
        )
        assert orchestrator._validate_identity_evidence_chain(*arguments) is None
        for altered in (
            evidence.model_copy(update={"binary_sha256": "0" * 64}),
            evidence.model_copy(
                update={
                    "media_probe": evidence.media_probe.model_copy(
                        update={"artifact_digest": "0" * 64}
                    )
                }
            ),
            evidence.model_copy(update={"selection_identity": "0" * 64}),
        ):
            assert isinstance(
                orchestrator._validate_identity_evidence_chain(
                    case.request,
                    altered,
                    *arguments[2:],
                ),
                orchestrator.ConfirmationFailure,
            )
        invalid_selection = orchestrator._BoundSelection(
            selection.video.model_copy(update={"nb_frames": None}),
            selection.audio,
            selection.video_reason_code,
            selection.audio_reason_code,
            selection.selection_identity,
            selection.mode,
            selection.assignment,
        )
        assert isinstance(
            orchestrator._validate_identity_evidence_chain(
                case.request,
                evidence,
                media,
                result.evidence.media_probe,
                invalid_selection,
                *arguments[5:],
            ),
            orchestrator.ConfirmationFailure,
        )
    finally:
        case.close()
