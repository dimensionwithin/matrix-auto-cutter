from __future__ import annotations

import hashlib

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.source_confirmation import (
    SourceConfirmed,
    SourceState,
    confirm_source,
    parse_media_probe_bytes,
    parse_source_identity_evidence_bytes,
)


def test_complete_automatic_confirmation_exact_state_and_artifacts(confirmation_case) -> None:
    case = confirmation_case
    result = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(result, SourceConfirmed)
    assert result.state_history == (
        SourceState.CLOSED,
        SourceState.PROBING,
        SourceState.PROBED,
        SourceState.HASHING,
        SourceState.HASH_COMPLETED,
        SourceState.CONFIRMING_IDENTITY,
        SourceState.CONFIRMED,
    )
    assert result.source_identity.sha256 == hashlib.sha256(case.source_data).hexdigest()
    assert result.source_identity.size_bytes == len(case.source_data)
    assert result.source_identity.duration_ms == 1000
    assert result.source_identity.video_frame_count == 60
    assert result.confirmed_source.authorized
    assert result.confirmed_source.require_authorized() == result.source_identity
    assert not hasattr(result.confirmed_source, "handle")
    assert not hasattr(result.confirmed_source, "artifact_type")

    media_data = bytes(
        case.port.nodes[case.port._key(result.evidence.media_probe.canonical_path)].data
    )
    media = parse_media_probe_bytes(media_data)
    assert media.outcome == "selected"
    assert media.s0 == media.s1 == media.s2 == media.s3
    assert media.pre_probe_path_revalidation.same_instance is True

    evidence_data = bytes(
        case.port.nodes[case.port._key(result.evidence.source_identity_evidence_path)].data
    )
    evidence = parse_source_identity_evidence_bytes(evidence_data)
    assert evidence.source_identity == result.source_identity
    assert evidence.s0 == evidence.s1 == evidence.s2 == evidence.s3 == evidence.s4 == evidence.s5
    assert evidence.selection_mode == "automatic_unique"
    assert evidence.assignment is None
    assert evidence.pre_commit_path_revalidation.same_instance is True
    assert (
        hashlib.sha256(evidence_data).hexdigest() == result.evidence.source_identity_evidence_digest
    )

    case.request.lease.close()
    assert not result.confirmed_source.authorized
