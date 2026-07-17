from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from tests.phase2.probe.conftest import FakeProcessPort
from tests.phase2.source_confirmation.conftest import (
    ambiguous_streams,
    make_case,
    probe_json,
)

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.probe import ProbeProcessOk, ProcessDiagnostics
from matrix_auto_cutter.phase2.source_confirmation import (
    SourceAssignmentRequired,
    SourceConfirmationFailed,
    SourceConfirmed,
    StreamAssignmentConflict,
    StreamAssignmentCreated,
    StreamAssignmentFailed,
    StreamAssignmentRequest,
    confirm_source,
    create_stream_assignment,
    parse_source_identity_evidence_bytes,
)

ASSIGNMENT_RUN_ID = "44444444-4444-4444-8444-444444444444"
SECOND_PROBE_ID = "55555555-5555-4555-8555-555555555555"
SECOND_PROBE_RUN_ID = "66666666-6666-4666-8666-666666666666"
SECOND_HASH_RUN_ID = "77777777-7777-4777-8777-777777777777"
SECOND_LEASE_ID = UUID("88888888-8888-4888-8888-888888888888")


def _ambiguous_case():
    return make_case(streams=ambiguous_streams())


def _assignment(case, media_reference):
    return create_stream_assignment(
        case.port,
        StreamAssignmentRequest(
            case.project,
            ASSIGNMENT_RUN_ID,
            media_reference,
            0,
            2,
            "diagnostic only",
        ),
        CancellationToken(),
    )


def test_genuine_ambiguity_persists_evidence_and_explicit_retry_succeeds() -> None:
    case = _ambiguous_case()
    try:
        first = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(first, SourceAssignmentRequired)
        assert case.port.hash_read_count == 0

        created = _assignment(case, first.media_probe)
        assert isinstance(created, StreamAssignmentCreated)
        assert created.status == "published"
        assert created.assignment.diagnostic_note == "diagnostic only"
        identical = _assignment(case, first.media_probe)
        assert isinstance(identical, StreamAssignmentCreated)
        assert identical.status == "idempotent"

        request = case.renewed_request(
            lease_id=SECOND_LEASE_ID,
            probe_id=SECOND_PROBE_ID,
            probe_run_id=SECOND_PROBE_RUN_ID,
            hash_run_id=SECOND_HASH_RUN_ID,
            assignment=created.reference,
        )
        case.request = request
        second = confirm_source(case.ports, request, CancellationToken())
        assert isinstance(second, SourceConfirmed)
        evidence_data = bytes(
            case.port.nodes[case.port._key(second.evidence.source_identity_evidence_path)].data
        )
        evidence = parse_source_identity_evidence_bytes(evidence_data)
        assert evidence.selection_mode == "explicit_assignment"
        assert evidence.assignment == created.reference
        assert evidence.video_reason_code == "explicit_video_assignment"
        assert evidence.audio_reason_code == "explicit_audio_assignment"
    finally:
        case.close()


def test_assignment_conflict_and_nonambiguous_creation_fail_closed() -> None:
    case = _ambiguous_case()
    try:
        first = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(first, SourceAssignmentRequired)
        created = _assignment(case, first.media_probe)
        assert isinstance(created, StreamAssignmentCreated)
        conflicting = create_stream_assignment(
            case.port,
            StreamAssignmentRequest(
                case.project,
                ASSIGNMENT_RUN_ID,
                first.media_probe,
                1,
                2,
            ),
            CancellationToken(),
        )
        assert isinstance(conflicting, StreamAssignmentConflict)
    finally:
        case.close()

    selected_case = make_case()
    try:
        selected = confirm_source(
            selected_case.ports,
            selected_case.request,
            CancellationToken(),
        )
        assert isinstance(selected, SourceConfirmed)
        failed = _assignment(selected_case, selected.evidence.media_probe)
        assert isinstance(failed, StreamAssignmentFailed)
    finally:
        selected_case.close()


def test_assignment_staleness_rejects_changed_current_stream_before_hash() -> None:
    case = _ambiguous_case()
    try:
        first = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(first, SourceAssignmentRequired)
        created = _assignment(case, first.media_probe)
        assert isinstance(created, StreamAssignmentCreated)

        changed = ambiguous_streams()
        changed[0]["codec_name"] = "hevc"
        case.process = FakeProcessPort(
            ProbeProcessOk(
                ProcessDiagnostics(
                    probe_json(
                        case.request.lease.source_path.canonical_dos_path,
                        case.source_data,
                        changed,
                    ),
                    b"",
                )
            )
        )
        case.ports = replace(case.ports, probe_process=case.process)
        request = case.renewed_request(
            lease_id=SECOND_LEASE_ID,
            probe_id=SECOND_PROBE_ID,
            probe_run_id=SECOND_PROBE_RUN_ID,
            hash_run_id=SECOND_HASH_RUN_ID,
            assignment=created.reference,
        )
        case.request = request
        result = confirm_source(case.ports, request, CancellationToken())
        assert isinstance(result, SourceConfirmationFailed)
        assert result.error.code.value == "E_STREAM_ASSIGNMENT_STALE"
        assert case.port.hash_read_count == 0
    finally:
        case.close()
