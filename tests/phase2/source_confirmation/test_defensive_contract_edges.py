from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest
from tests.phase2.source_confirmation.conftest import ambiguous_streams, make_case

import matrix_auto_cutter.phase2.source_confirmation.assignment as assignment_module
import matrix_auto_cutter.phase2.source_confirmation.capability as capability_module
import matrix_auto_cutter.phase2.source_confirmation.identity as identity_module
import matrix_auto_cutter.phase2.source_confirmation.path_revalidation as path_module
import matrix_auto_cutter.phase2.source_confirmation.persistence as persistence_module
from matrix_auto_cutter.models import SourceBinding, SourceIdentity
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.artifacts import UnavailableIdentity, canonical_bytes
from matrix_auto_cutter.phase2.probe import (
    ProbeOk,
    ProbeRequest,
    ProgramProfile,
    run_probe,
)
from matrix_auto_cutter.phase2.snapshots import (
    ComparisonFailed,
    NotComparable,
    SnapshotOk,
    snapshot_file,
)
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationDiagnostic,
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
    ConfirmedSource,
    SourceAssignmentRequired,
    SourceConfirmed,
    StreamAssignmentCancelled,
    StreamAssignmentCreated,
    StreamAssignmentFailed,
    StreamAssignmentRequest,
    confirm_source,
    create_stream_assignment,
    parse_media_probe_bytes,
    parse_source_identity_evidence_bytes,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import (
    BinaryIdentityEvidence,
    FormatEvidence,
    ProgramEvidence,
    SnapshotEvidence,
    StreamEvidence,
)
from matrix_auto_cutter.phase2.source_hash import HashCompleted, hash_lease_source

ASSIGNMENT_ID = "44444444-4444-4444-8444-444444444444"


def _success_models(case):
    result = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(result, SourceConfirmed)
    media = parse_media_probe_bytes(
        bytes(case.port.nodes[case.port._key(result.evidence.media_probe.canonical_path)].data)
    )
    evidence = parse_source_identity_evidence_bytes(
        bytes(case.port.nodes[case.port._key(result.evidence.source_identity_evidence_path)].data)
    )
    return result, media, evidence


def test_evidence_projection_and_every_model_cross_binding_guard(monkeypatch) -> None:
    case = make_case()
    try:
        result, media, evidence = _success_models(case)
        unavailable = replace(case.request.lease.s0, file_id=UnavailableIdentity())
        with pytest.raises(ValueError):
            SnapshotEvidence.from_snapshot(unavailable)
        with pytest.raises(ValueError):
            media.pre_probe_path_revalidation.model_copy(update={"lease_file_id": "f" * 32})
        with pytest.raises(ValueError):
            media.profile.model_copy(update={"streams": tuple(reversed(media.profile.streams))})

        profile_result = run_probe(
            ProbeRequest(
                case.request.binary,
                case.request.lease.source_path,
                case.request.lease.s0.snapshot_key,
            ),
            case.ports.binary_trust,
            case.process,
            lambda path: snapshot_file(case.port, path),
            CancellationToken(),
        )
        assert isinstance(profile_result, ProbeOk)
        optional = replace(
            profile_result.profile.streams[0],
            start_time=None,
            duration=None,
            time_base=None,
        )
        projected = StreamEvidence.from_stream(optional)
        assert projected.start_time is None and projected.duration is None
        no_times = replace(
            profile_result.profile.format,
            start_time=None,
            duration=None,
        )
        assert FormatEvidence.from_format(no_times).duration is None
        assert ProgramEvidence.from_program(ProgramProfile(1, (), ())).program_id == 1

        forged_binary = object.__new__(type(case.request.binary))
        for slot in type(case.request.binary).__slots__:
            value = (
                UnavailableIdentity() if slot == "file_id" else getattr(case.request.binary, slot)
            )
            object.__setattr__(forged_binary, slot, value)
        with pytest.raises(ValueError):
            BinaryIdentityEvidence.from_binary(forged_binary)

        invalid_media_updates = (
            {"lease_epoch": "99999999-9999-4999-8999-999999999999"},
            {"s3": media.s3.model_copy(update={"file_id": "f" * 32})},
            {"s3": media.s3.model_copy(update={"snapshot_key": "f" * 64})},
            {"expected_snapshot_key": "f" * 64},
            {"profile": media.profile.model_copy(update={"canonical_stream_evidence_json": "[]"})},
            {"semantic_profile_digest": "f" * 64},
            {"automatic_selection": None},
        )
        for update in invalid_media_updates:
            with pytest.raises(ValueError):
                media.model_copy(update=update)
        with pytest.raises(ValueError):
            media.model_copy(
                update={
                    "outcome": "unsupported",
                    "automatic_selection": None,
                    "error_code": None,
                    "error_phase": None,
                }
            )

        invalid_evidence_updates = (
            {"s5": evidence.s5.model_copy(update={"file_id": "f" * 32})},
            {
                "pre_commit_path_revalidation": evidence.pre_commit_path_revalidation.model_copy(
                    update={"snapshot": media.s3.model_copy(update={"snapshot_key": "f" * 64})}
                )
            },
            {"hash_receipt_digest": "f" * 64},
            {
                "source_identity": evidence.source_identity.model_copy(
                    update={"binding": SourceBinding.MANUAL_REMUX}
                )
            },
            {"selection_mode": "explicit_assignment", "assignment": None},
        )
        for update in invalid_evidence_updates:
            with pytest.raises(ValueError):
                evidence.model_copy(update=update)
        altered_identity = evidence.source_identity.model_copy(
            update={"binding": SourceBinding.MANUAL_REMUX}
        )
        altered_digest = identity_module.source_identity_digest(altered_identity)
        with pytest.raises(ValueError, match="SourceIdentity differs"):
            evidence.model_copy(
                update={
                    "source_identity": altered_identity,
                    "source_identity_digest": altered_digest,
                    "evidence_id": altered_digest,
                }
            )

        pretty = json.dumps(json.loads(canonical_bytes(media)), indent=2).encode() + b"\n"
        with pytest.raises(ValueError, match="not canonical"):
            parse_media_probe_bytes(pretty)
        assert result.source_identity == evidence.source_identity
    finally:
        case.close()


def test_assignment_input_read_publish_and_staleness_edges(monkeypatch) -> None:
    case = make_case(streams=ambiguous_streams())
    try:
        ambiguous = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(ambiguous, SourceAssignmentRequired)
        base_request = StreamAssignmentRequest(
            case.project,
            ASSIGNMENT_ID,
            ambiguous.media_probe,
            0,
            2,
        )
        invalid = create_stream_assignment(
            case.port,
            replace(base_request, video_index=-1),
            CancellationToken(),
        )
        assert isinstance(invalid, StreamAssignmentFailed)
        cancelled = CancellationToken()
        cancelled.cancel()
        assert isinstance(
            create_stream_assignment(case.port, base_request, cancelled),
            StreamAssignmentCancelled,
        )
        bad_probe = ambiguous.media_probe.model_copy(update={"artifact_digest": "f" * 64})
        assert isinstance(
            create_stream_assignment(
                case.port,
                replace(base_request, media_probe=bad_probe),
                CancellationToken(),
            ),
            StreamAssignmentFailed,
        )
        assert isinstance(
            create_stream_assignment(
                case.port,
                replace(base_request, video_index=99),
                CancellationToken(),
            ),
            StreamAssignmentFailed,
        )

        failure = ConfirmationFailure(
            ConfirmationErrorCode.IO,
            ConfirmationErrorCategory.IO,
            "forced",
            "forced",
        )
        monkeypatch.setattr(assignment_module, "artifact_target", lambda *_args: failure)
        assert isinstance(
            create_stream_assignment(case.port, base_request, CancellationToken()),
            StreamAssignmentFailed,
        )
        monkeypatch.undo()

        monkeypatch.setattr(
            assignment_module,
            "publish_artifact",
            lambda *_args, **_kwargs: persistence_module.ArtifactPublishCancelled(failure),
        )
        assert isinstance(
            create_stream_assignment(case.port, base_request, CancellationToken()),
            StreamAssignmentCancelled,
        )
        monkeypatch.setattr(
            assignment_module,
            "publish_artifact",
            lambda *_args, **_kwargs: persistence_module.ArtifactIoFailure(failure),
        )
        assert isinstance(
            create_stream_assignment(case.port, base_request, CancellationToken()),
            StreamAssignmentFailed,
        )
        monkeypatch.undo()

        created = create_stream_assignment(
            case.port,
            base_request,
            CancellationToken(),
        )
        assert isinstance(created, StreamAssignmentCreated)
        with pytest.raises(ValueError, match="stream assignment bindings"):
            created.assignment.model_copy(update={"video": created.assignment.audio})
        media = parse_media_probe_bytes(
            bytes(case.port.nodes[case.port._key(ambiguous.media_probe.canonical_path)].data)
        )
        wrong_type = created.reference.model_copy(update={"artifact_type": "media_probe"})
        assert isinstance(
            assignment_module.validate_stream_assignment(
                case.port, case.project, wrong_type, media
            ),
            ConfirmationFailure,
        )
        bad_assignment = created.reference.model_copy(update={"artifact_digest": "f" * 64})
        assert isinstance(
            assignment_module.validate_stream_assignment(
                case.port, case.project, bad_assignment, media
            ),
            ConfirmationFailure,
        )
        monkeypatch.setattr(
            assignment_module,
            "_indexed_pair",
            lambda *_args: (created.assignment.audio, created.assignment.video),
        )
        assert isinstance(
            assignment_module.validate_stream_assignment(
                case.port, case.project, created.reference, media
            ),
            ConfirmationFailure,
        )
        monkeypatch.undo()

        calls = 0
        original_read = assignment_module.read_artifact

        def fail_original(*args, **kwargs):
            nonlocal calls
            calls += 1
            return failure if calls == 2 else original_read(*args, **kwargs)

        monkeypatch.setattr(assignment_module, "read_artifact", fail_original)
        assert isinstance(
            assignment_module.validate_stream_assignment(
                case.port, case.project, created.reference, media
            ),
            ConfirmationFailure,
        )
        monkeypatch.undo()

        stale_original = (
            media.model_copy(update={"semantic_profile_digest": "f" * 64}) if False else media
        )
        monkeypatch.setattr(
            assignment_module,
            "read_artifact",
            lambda *_args, **_kwargs: created.assignment
            if _args[2] is created.reference
            else media.model_copy(update={"project_id": "99999999-9999-4999-8999-999999999999"}),
        )
        assert isinstance(
            assignment_module.validate_stream_assignment(
                case.port, case.project, created.reference, media
            ),
            ConfirmationFailure,
        )
        del stale_original
    finally:
        case.close()


def test_capability_identity_and_path_defensive_edges(monkeypatch) -> None:
    case = make_case()
    try:
        result, media, evidence = _success_models(case)
        capability = result.confirmed_source
        assert capability.source_identity == result.source_identity
        assert capability.project_id == case.project.document.project_id
        assert capability.run_id == case.request.identity_run_id
        assert capability.lease_epoch == str(case.request.lease.validation_epoch)
        with pytest.raises(AttributeError):
            del capability._token
        forged = object.__new__(ConfirmedSource)
        with pytest.raises(TypeError):
            forged._initialize(
                result.source_identity,
                evidence,
                case.project.document.project_id,
                case.request.identity_run_id,
                case.request.lease,
                object(),
                _seal=object(),
            )
        assert not forged.authorized

        case.request.lease.close()
        with pytest.raises(ValueError):
            capability_module._CONFIRMED_AUTHORITY.issue(
                result.source_identity,
                evidence,
                case.project.document.project_id,
                case.request.identity_run_id,
                case.request.lease,
            )
        other_case = make_case()
        try:
            with pytest.raises(ValueError):
                capability_module._CONFIRMED_AUTHORITY.issue(
                    result.source_identity,
                    evidence,
                    "99999999-9999-4999-8999-999999999999",
                    case.request.identity_run_id,
                    other_case.request.lease,
                )
        finally:
            other_case.close()
        capability_module._invalidate_confirmed_source(capability)

        diagnostic = ConfirmationDiagnostic("cleanup", "secondary")
        primary = ConfirmationFailure(
            ConfirmationErrorCode.IO,
            ConfirmationErrorCategory.IO,
            "primary",
            "primary",
        )
        assert primary.with_cleanup((diagnostic,)).cleanup_diagnostics == (diagnostic,)

        assert identity_module._exact_scaled_integer(Decimal("1.5"), 2, "x") == 3
        with pytest.raises(ValueError):
            identity_module._exact_scaled_integer(Decimal("0.0001"), 1000, "x")
        completed_case = make_case()
        try:
            completed = hash_lease_source(
                completed_case.request.lease,
                CancellationToken(),
                completed_case.project.document.project_id,
                completed_case.request.hash_run_id,
            )
            assert isinstance(completed, HashCompleted)
            alternate = SourceIdentity.model_validate(
                result.source_identity.model_dump() | {"file_name": "alternate.mp4"}
            )
            monkeypatch.setattr(
                SourceIdentity,
                "model_validate_json",
                classmethod(lambda _cls, _data: alternate),
            )
            with pytest.raises(ValueError, match="canonical value comparison"):
                identity_module.source_identity_digest(result.source_identity)
            assert isinstance(
                identity_module.build_source_identity(
                    completed_case.request.lease.source_path.canonical_dos_path,
                    completed,
                    media.profile.format,
                    media.profile.streams[0],
                    media.profile.streams[1],
                    SourceBinding.DIRECT_MP4,
                ),
                ConfirmationFailure,
            )
            monkeypatch.undo()
            invalid_duration = media.profile.format.model_copy(update={"duration": None})
            assert isinstance(
                identity_module.build_source_identity(
                    completed_case.request.lease.source_path.canonical_dos_path,
                    completed,
                    invalid_duration,
                    media.profile.streams[0],
                    media.profile.streams[1],
                    SourceBinding.DIRECT_MP4,
                ),
                ConfirmationFailure,
            )
            invalid_video = media.profile.streams[0].model_copy(update={"nb_frames": None})
            assert isinstance(
                identity_module.build_source_identity(
                    completed_case.request.lease.source_path.canonical_dos_path,
                    completed,
                    media.profile.format,
                    invalid_video,
                    media.profile.streams[1],
                    SourceBinding.DIRECT_MP4,
                ),
                ConfirmationFailure,
            )
        finally:
            completed_case.close()

        live_case = make_case()
        try:
            other_path = replace(
                live_case.request.lease.source_path,
                canonical_dos_path=r"C:\Other\source.mp4",
                long_path=r"\\?\C:\Other\source.mp4",
            )
            changed_path_snapshot = replace(
                live_case.request.lease.s0,
                path_ref=other_path,
            )
            monkeypatch.setattr(
                path_module,
                "snapshot_file",
                lambda *_args: SnapshotOk(changed_path_snapshot),
            )
            assert isinstance(
                path_module.revalidate_lease_path(
                    live_case.port, live_case.request.lease, "before_probe"
                ),
                path_module.PathRevalidationFailed,
            )
            monkeypatch.setattr(path_module, "compare_snapshots", lambda *_args: NotComparable())
            assert isinstance(
                path_module.revalidate_lease_path(
                    live_case.port, live_case.request.lease, "before_probe"
                ),
                path_module.PathRevalidationFailed,
            )
            monkeypatch.setattr(
                path_module,
                "compare_snapshots",
                lambda *_args: ComparisonFailed("bad"),
            )
            assert isinstance(
                path_module.revalidate_lease_path(
                    live_case.port, live_case.request.lease, "before_probe"
                ),
                path_module.PathRevalidationFailed,
            )
        finally:
            live_case.close()
    finally:
        case.close()
