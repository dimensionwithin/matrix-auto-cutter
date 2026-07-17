from __future__ import annotations

from dataclasses import replace

import pytest
from tests.phase2.source_confirmation.conftest import (
    ambiguous_streams,
    make_case,
    unique_streams,
)

import matrix_auto_cutter.phase2.source_confirmation.orchestrator as orchestrator
from matrix_auto_cutter.phase2 import CancellationToken, ErrorCategory
from matrix_auto_cutter.phase2.close_gate import RecheckOk
from matrix_auto_cutter.phase2.probe import ProbeFailed, ProbeOk, ProbeRequest, run_probe
from matrix_auto_cutter.phase2.probe.errors import ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.snapshots import DifferentInstance, NotComparable, snapshot_file
from matrix_auto_cutter.phase2.source_confirmation import (
    ConfirmationErrorCategory,
    ConfirmationErrorCode,
    ConfirmationFailure,
    SourceConfirmationCancelled,
    SourceConfirmationFailed,
    SourceInvalidated,
    SourceUnsupported,
)
from matrix_auto_cutter.phase2.source_confirmation.evidence import ArtifactReference
from matrix_auto_cutter.phase2.source_confirmation.path_revalidation import (
    PathDisappeared,
    PathRevalidated,
    PathRevalidationFailed,
    revalidate_lease_path,
)
from matrix_auto_cutter.phase2.source_confirmation.state import SourceState, SourceStateMachine
from matrix_auto_cutter.phase2.source_hash import (
    HashCompleted,
    HashErrorCategory,
    HashErrorCode,
    HashFailure,
    HashReceiptConflict,
    HashReceiptPublishIoError,
    hash_lease_source,
)


def _probe(case) -> ProbeOk | ProbeFailed:
    return run_probe(
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


def _failure(code: ConfirmationErrorCode = ConfirmationErrorCode.INTEGRITY):
    return ConfirmationFailure(code, ConfirmationErrorCategory.INTEGRITY, "forced", "forced")


class _Session:
    def __init__(self, result: object) -> None:
        self.result = result

    def recheck(self, _cancellation: CancellationToken) -> object:
        return self.result


def test_small_orchestration_validation_helpers(monkeypatch) -> None:
    case = make_case()
    ambiguous_case = make_case(streams=ambiguous_streams())
    try:
        unsupported_state = SourceStateMachine()
        unsupported_state.transition(SourceState.PROBING)
        assert isinstance(
            orchestrator._unsupported(unsupported_state, _failure()), SourceUnsupported
        )

        monkeypatch.setattr(
            orchestrator, "compare_snapshots", lambda *_args: (_ for _ in ()).throw(TypeError())
        )
        assert not orchestrator._snapshots_match_s0(case.request.lease, case.request.lease.s0)
        monkeypatch.setattr(orchestrator, "compare_snapshots", lambda *_args: NotComparable())
        assert not orchestrator._snapshots_match_s0(case.request.lease, case.request.lease.s0)
        monkeypatch.undo()

        successful = _probe(case)
        assert isinstance(successful, ProbeOk)
        invalid = replace(
            successful.profile,
            expected_snapshot_key="0" * 64,
        )
        assert orchestrator._validate_probe_profile(case.request, ProbeOk(invalid)) is not None
        monkeypatch.setattr(orchestrator, "selection_semantically_matches", lambda *_args: False)
        assert orchestrator._validate_probe_profile(case.request, successful) is not None
        monkeypatch.undo()

        ambiguous = _probe(ambiguous_case)
        assert isinstance(ambiguous, ProbeFailed) and ambiguous.profile is not None
        wrong_digest = replace(ambiguous.profile, stream_selection_evidence_digest="0" * 64)
        assert (
            orchestrator._validate_probe_profile(ambiguous_case.request, wrong_digest) is not None
        )

        pre_path = revalidate_lease_path(case.port, case.request.lease, "before_probe")
        assert isinstance(pre_path, PathRevalidated)
        media = orchestrator._media_probe(case.request, pre_path, case.request.lease.s0, successful)
        monkeypatch.setattr(orchestrator, "artifact_target", lambda *_args: _failure())
        assert isinstance(
            orchestrator._publish_media(case.request, case.ports, media, CancellationToken()),
            orchestrator.ArtifactIoFailure,
        )
        monkeypatch.undo()

        changed = replace(case.request.lease.s0, size_bytes=case.request.lease.s0.size_bytes + 1)
        changed_result = orchestrator._recheck(
            _Session(RecheckOk(changed)),
            case.request,
            CancellationToken(),
            "s3",  # type: ignore[arg-type]
        )
        assert isinstance(changed_result, ConfirmationFailure)
        monkeypatch.setattr(orchestrator, "compare_snapshots", lambda *_args: NotComparable())
        incomparable = orchestrator._recheck(
            _Session(RecheckOk(case.request.lease.s0)),  # type: ignore[arg-type]
            case.request,
            CancellationToken(),
            "s3",
        )
        assert isinstance(incomparable, ConfirmationFailure)
        monkeypatch.undo()

        case.request.lease.close()
        closed = case.request.lease.recheck(CancellationToken())
        assert isinstance(
            orchestrator._recheck(
                _Session(closed),
                case.request,
                CancellationToken(),
                "s3",  # type: ignore[arg-type]
            ),
            ConfirmationFailure,
        )
    finally:
        case.close()
        ambiguous_case.close()


def test_selection_hash_and_probe_failure_helpers(monkeypatch) -> None:
    case = make_case()
    ambiguous_case = make_case(streams=ambiguous_streams())
    other = make_case()
    try:
        probe = _probe(case)
        assert isinstance(probe, ProbeOk)
        pre_path = revalidate_lease_path(case.port, case.request.lease, "before_probe")
        assert isinstance(pre_path, PathRevalidated)
        media = orchestrator._media_probe(case.request, pre_path, case.request.lease.s0, probe)
        reference = ArtifactReference(
            artifact_type="media_probe",
            artifact_id=case.request.probe_id,
            artifact_digest="0" * 64,
            canonical_path=r"C:\Workspace\probe\media-probe.json",
        )
        monkeypatch.setattr(orchestrator, "selection_semantically_matches", lambda *_args: False)
        assert isinstance(
            orchestrator._selection_from_media(case.request, case.ports, media, reference, probe),
            ConfirmationFailure,
        )
        monkeypatch.undo()
        request_with_assignment = replace(case.request, assignment=reference)
        assert isinstance(
            orchestrator._selection_from_media(
                request_with_assignment, case.ports, media, reference, probe
            ),
            ConfirmationFailure,
        )

        ambiguous = _probe(ambiguous_case)
        assert isinstance(ambiguous, ProbeFailed) and ambiguous.profile is not None
        ambiguous_media = orchestrator._media_probe(
            ambiguous_case.request,
            revalidate_lease_path(
                ambiguous_case.port, ambiguous_case.request.lease, "before_probe"
            ),  # type: ignore[arg-type]
            ambiguous_case.request.lease.s0,
            ambiguous,
        )
        assert isinstance(
            orchestrator._selection_from_media(
                ambiguous_case.request,
                ambiguous_case.ports,
                ambiguous_media,
                reference,
                ambiguous,
            ),
            ConfirmationFailure,
        )
        nonambiguous_failure = ProbeFailed(
            probe_error(
                ProbeErrorCode.UNSUPPORTED_MEDIA,
                ErrorCategory.POLICY,
                "probe",
                "unsupported",
            ),
            ambiguous.profile,
        )
        assert isinstance(
            orchestrator._selection_from_media(
                ambiguous_case.request,
                ambiguous_case.ports,
                ambiguous_media,
                reference,
                nonambiguous_failure,
            ),
            ConfirmationFailure,
        )

        forged = object.__new__(HashCompleted)
        assert orchestrator._validate_completed(case.request, forged) is not None
        completed = hash_lease_source(
            case.request.lease,
            CancellationToken(),
            case.project.document.project_id,
            case.request.hash_run_id,
        )
        assert isinstance(completed, HashCompleted)
        monkeypatch.setattr(orchestrator, "compare_snapshots", lambda *_args: DifferentInstance())
        assert orchestrator._validate_completed(case.request, completed) is not None
        monkeypatch.setattr(orchestrator, "compare_snapshots", lambda *_args: NotComparable())
        assert orchestrator._validate_completed(case.request, completed) is not None
        monkeypatch.undo()
        other_completed = hash_lease_source(
            other.request.lease,
            CancellationToken(),
            other.project.document.project_id,
            other.request.hash_run_id,
        )
        assert isinstance(other_completed, HashCompleted)
        assert orchestrator._validate_completed(case.request, other_completed) is not None

        for code, expected in (
            (ProbeErrorCode.CANCELLED, SourceConfirmationCancelled),
            (ProbeErrorCode.SOURCE_CHANGED, SourceInvalidated),
            (ProbeErrorCode.UNSUPPORTED_MEDIA, SourceUnsupported),
        ):
            state = SourceStateMachine()
            state.transition(SourceState.PROBING)
            result = orchestrator._probe_failure_result(
                state, probe_error(code, ErrorCategory.INTEGRITY, "probe", "failure")
            )
            assert isinstance(result, expected)
    finally:
        case.close()
        ambiguous_case.close()
        other.close()


def test_public_input_and_lease_unavailable_edges(monkeypatch) -> None:
    case = make_case()
    try:
        invalid = replace(case.request, probe_run_id="not-a-run-id")
        assert isinstance(
            orchestrator.confirm_source(case.ports, invalid, CancellationToken()),
            SourceConfirmationFailed,
        )
        monkeypatch.setattr(
            orchestrator,
            "_run_lease_usage",
            lambda *_args, **_kwargs: orchestrator._LeaseUsageUnavailable("ownership"),
        )
        assert isinstance(
            orchestrator.confirm_source(case.ports, case.request, CancellationToken()),
            SourceConfirmationFailed,
        )
        monkeypatch.setattr(
            orchestrator,
            "_run_lease_usage",
            lambda *_args, **_kwargs: orchestrator._LeaseUsageUnavailable("cancelled"),
        )
        assert isinstance(
            orchestrator.confirm_source(case.ports, case.request, CancellationToken()),
            SourceConfirmationCancelled,
        )
    finally:
        case.close()


@pytest.mark.parametrize(
    "edge",
    (
        "invalid_snapshotter",
        "probe_validation",
        "media_build",
        "media_publish_cancel",
        "unsupported_media",
        "completed_changed",
        "completed_integrity",
        "identity_build",
        "receipt_target",
        "receipt_conflict",
        "receipt_io",
        "s5_io",
        "final_disappeared",
        "final_path_io",
        "evidence_validation",
        "evidence_target",
        "evidence_cancel",
        "evidence_io",
        "commit_cancel",
    ),
)
def test_full_orchestrator_failure_linearization_edges(edge: str, monkeypatch) -> None:
    streams = unique_streams()[:1] if edge == "unsupported_media" else None
    case = make_case(streams=streams)
    cancellation = CancellationToken()
    expected: type[object] = SourceConfirmationFailed
    raises = False
    try:
        if edge == "invalid_snapshotter":

            def invalid_snapshotter(*args, **kwargs):
                del kwargs
                args[3](object())

            monkeypatch.setattr(orchestrator, "run_probe", invalid_snapshotter)
            raises = True
        elif edge == "probe_validation":
            monkeypatch.setattr(orchestrator, "_validate_probe_profile", lambda *_args: _failure())
        elif edge == "media_build":
            monkeypatch.setattr(
                orchestrator,
                "_media_probe",
                lambda *_args: (_ for _ in ()).throw(ValueError("bad media evidence")),
            )
        elif edge == "media_publish_cancel":
            monkeypatch.setattr(
                orchestrator,
                "_publish_media",
                lambda *_args: orchestrator.ArtifactPublishCancelled(_failure()),
            )
            expected = SourceConfirmationCancelled
        elif edge == "unsupported_media":
            expected = SourceUnsupported
        elif edge == "completed_changed":
            monkeypatch.setattr(
                orchestrator,
                "_validate_completed",
                lambda *_args: _failure(ConfirmationErrorCode.SOURCE_CHANGED),
            )
            expected = SourceInvalidated
        elif edge == "completed_integrity":
            monkeypatch.setattr(orchestrator, "_validate_completed", lambda *_args: _failure())
        elif edge == "identity_build":
            monkeypatch.setattr(orchestrator, "build_source_identity", lambda *_args: _failure())
        elif edge in {"receipt_target", "evidence_target"}:
            original_target = orchestrator.artifact_target
            rejected_name = (
                "hash-receipt.json" if edge == "receipt_target" else "source-identity-evidence.json"
            )

            def targeted_failure(*args, **kwargs):
                if args[-1] == rejected_name:
                    return _failure()
                return original_target(*args, **kwargs)

            monkeypatch.setattr(orchestrator, "artifact_target", targeted_failure)
        elif edge in {"receipt_conflict", "receipt_io"}:
            hash_error = HashFailure(
                HashErrorCode.RECEIPT_CONFLICT if edge == "receipt_conflict" else HashErrorCode.IO,
                HashErrorCategory.CONFLICT if edge == "receipt_conflict" else HashErrorCategory.IO,
                "receipt.publish",
                "forced",
            )
            receipt_result = (
                HashReceiptConflict(hash_error)
                if edge == "receipt_conflict"
                else HashReceiptPublishIoError(hash_error)
            )
            monkeypatch.setattr(orchestrator, "publish_hash_receipt", lambda *_args: receipt_result)
        elif edge == "s5_io":
            original_recheck = orchestrator._recheck

            def s5_failure(*args, **kwargs):
                if args[3] == "s5":
                    return _failure(ConfirmationErrorCode.IO)
                return original_recheck(*args, **kwargs)

            monkeypatch.setattr(orchestrator, "_recheck", s5_failure)
        elif edge in {"final_disappeared", "final_path_io"}:
            original_path = orchestrator.revalidate_lease_path

            def final_failure(*args, **kwargs):
                if args[2] == "before_identity_commit":
                    failure = _failure(ConfirmationErrorCode.IO)
                    if edge == "final_disappeared":
                        return PathDisappeared(failure)
                    return PathRevalidationFailed(failure)
                return original_path(*args, **kwargs)

            monkeypatch.setattr(orchestrator, "revalidate_lease_path", final_failure)
            if edge == "final_disappeared":
                expected = orchestrator.SourceDisappeared
        elif edge == "evidence_validation":
            monkeypatch.setattr(
                orchestrator,
                "_validate_identity_evidence_chain",
                lambda *_args: _failure(),
            )
        elif edge in {"evidence_cancel", "evidence_io"}:
            original_publish = orchestrator.publish_artifact

            def evidence_publish(*args, **kwargs):
                if kwargs.get("artifact_name") == "source-identity-evidence":
                    if edge == "evidence_cancel":
                        return orchestrator.ArtifactPublishCancelled(_failure())
                    return orchestrator.ArtifactIoFailure(_failure())
                return original_publish(*args, **kwargs)

            monkeypatch.setattr(orchestrator, "publish_artifact", evidence_publish)
            if edge == "evidence_cancel":
                expected = SourceConfirmationCancelled
        elif edge == "commit_cancel":

            def cancel_commit(_session, token: CancellationToken) -> bool:
                token.cancel()
                return False

            monkeypatch.setattr(orchestrator._LeaseUsageSession, "commit", cancel_commit)
            expected = SourceConfirmationCancelled

        if raises:
            with pytest.raises(TypeError):
                orchestrator.confirm_source(case.ports, case.request, cancellation)
        else:
            result = orchestrator.confirm_source(case.ports, case.request, cancellation)
            assert isinstance(result, expected)
    finally:
        case.close()
