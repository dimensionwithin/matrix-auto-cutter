from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest

import matrix_auto_cutter.approval as approval_module
from matrix_auto_cutter.approval import (
    DecisionWritten,
    check_render_authorization,
    ensure_pending_approval,
    record_decision,
    record_selected_decision,
)
from matrix_auto_cutter.cut_proposal import (
    AnalysisParameters,
    FfmpegProcessResult,
    ProposalFailed,
    ProposalReady,
    SilenceInterval,
    build_cut_candidates,
    generate_proposal,
)
from matrix_auto_cutter.models import (
    MaterializedFrameRange,
    ProtectionLevel,
    ProtectionPolicy,
    SourceBinding,
    SourceIdentity,
)
from matrix_auto_cutter.review import write_review
from matrix_auto_cutter.review_app import ReviewSelectionBridge
from matrix_auto_cutter.selection import SelectionReady, ensure_selection, update_selection

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
SILENCE_OUTPUT = (
    b"[silencedetect @ 000] silence_start: 2.000\n"
    b"[silencedetect @ 000] silence_end: 5.000 | silence_duration: 3.000\n"
)


class FakeProcess:
    def __init__(self, analysis: FfmpegProcessResult | None = None) -> None:
        self.analysis = analysis or FfmpegProcessResult(0, SILENCE_OUTPUT)
        self.analysis_calls = 0

    def __call__(self, arguments: object, timeout: int) -> FfmpegProcessResult:
        del timeout
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version test-build\n")
        self.analysis_calls += 1
        assert values[-3:] == ("null", "NUL") or values[-2:] == ("null", "NUL")
        return self.analysis


def _prepare(
    tmp_path: Path,
    raw_sidecar: dict[str, object],
) -> tuple[Path, Path, Path, FakeProcess]:
    source = tmp_path / "recording & review.mp4"
    source.write_bytes(b"read-only-source-bytes")
    source_payload = raw_sidecar["source"]
    assert isinstance(source_payload, dict)
    source_payload.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_text(json.dumps(raw_sidecar), encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fake-ffmpeg-binary")
    return source, sidecar, ffmpeg, FakeProcess()


def _generate(
    tmp_path: Path,
    raw_sidecar: dict[str, object],
    *,
    process: FakeProcess | None = None,
    parameters: AnalysisParameters | None = None,
) -> ProposalReady:
    source, sidecar, ffmpeg, default_process = _prepare(tmp_path, raw_sidecar)
    result = generate_proposal(
        source,
        sidecar,
        str(raw_sidecar["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        parameters=parameters,
        process_runner=process or default_process,
        now=lambda: NOW,
    )
    assert isinstance(result, ProposalReady), result
    return result


def _source() -> SourceIdentity:
    return SourceIdentity(
        file_name="recording.mp4",
        size_bytes=100,
        sha256="a" * 64,
        duration_ms=10_000,
        video_frame_count=600,
        fps_num=60,
        fps_den=1,
        video_start_time_ns=0,
        audio_start_time_ns=0,
        binding=SourceBinding.DIRECT_MP4,
    )


def _protection(start: int, end: int, *, hard: bool = True) -> MaterializedFrameRange:
    return MaterializedFrameRange(
        protection_id="test-protection",
        source_start_frame=start,
        source_end_frame=end,
        level=ProtectionLevel.HARD if hard else ProtectionLevel.SOFT,
        source_event_ids=(),
        uncertainty_padding_frames=0,
        policy=ProtectionPolicy(
            blocks_time_edits=True,
            blocks_overlays=False,
            blocks_local_audio_repair=False,
            allows_global_mastering=True,
        ),
    )


def test_valid_source_and_sidecar_publish_concrete_deterministic_proposal(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, sidecar, ffmpeg, process = _prepare(tmp_path, raw_sidecar)
    before = (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest())

    first = generate_proposal(
        source,
        sidecar,
        str(raw_sidecar["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    second = generate_proposal(
        source,
        sidecar,
        str(raw_sidecar["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )

    assert isinstance(first, ProposalReady)
    assert isinstance(second, ProposalReady)
    assert first.proposal == second.proposal
    assert second.reused is True
    assert process.analysis_calls == 1
    assert first.proposal.total_proposed_cuts == 1
    assert first.proposal.proposed_cuts[0].audio_evidence.raw_silence_duration_ms == 3000
    assert (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest()) == before
    assert not list(source.parent.glob("*.tmp"))


def test_candidate_rules_trim_short_normalize_and_reject_protection() -> None:
    source = _source()
    rules = AnalysisParameters()
    short = SilenceInterval(Decimal("1"), Decimal("2"), Decimal("1"))
    cuts, rejected = build_cut_candidates((short,), source, (), rules, "seed")
    assert cuts == ()
    assert rejected[0].reason == "below_minimum_silence"

    long = SilenceInterval(Decimal("2"), Decimal("5"), Decimal("3"))
    cuts, rejected = build_cut_candidates((long,), source, (), rules, "seed")
    assert rejected == ()
    assert (cuts[0].start_frame, cuts[0].end_frame) == (141, 279)
    assert cuts[0].duration_ms == 2300

    overlapping = SilenceInterval(Decimal("4"), Decimal("6"), Decimal("2"))
    cuts, _ = build_cut_candidates((long, overlapping), source, (), rules, "seed")
    assert len(cuts) == 1
    assert cuts[0].start_frame < cuts[0].end_frame

    cuts, rejected = build_cut_candidates((long,), source, (_protection(200, 220),), rules, "seed")
    assert cuts == ()
    assert rejected[0].reason == "hard_protection_overlap"


def test_binding_changes_and_ffmpeg_or_sidecar_failure_fail_closed(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, sidecar, ffmpeg, process = _prepare(tmp_path, raw_sidecar)
    recording_id = str(raw_sidecar["recording_session_id"])
    ready = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(ready, ProposalReady)

    source.write_bytes(b"tampered-source-bytes!")
    changed = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
    )
    assert isinstance(changed, ProposalFailed)
    assert changed.code == "E_SIDECAR_SOURCE_BINDING"

    source.write_bytes(b"read-only-source-bytes")
    sidecar.write_text("{broken", encoding="utf-8")
    invalid = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
    )
    assert isinstance(invalid, ProposalFailed)
    assert invalid.code == "E_SIDECAR_SOURCE_BINDING"


def test_ffmpeg_failure_is_stable_and_creates_no_proposal(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, sidecar, ffmpeg, _ = _prepare(tmp_path, raw_sidecar)
    process = FakeProcess(FfmpegProcessResult(7, b"controlled failure"))
    result = generate_proposal(
        source,
        sidecar,
        str(raw_sidecar["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
    )
    assert isinstance(result, ProposalFailed)
    assert result.code == "E_FFMPEG_ANALYSIS"
    assert not list((tmp_path / "artifacts").rglob("cut-proposal.json"))


def test_new_proposal_pending_and_gate_accepts_only_exact_explicit_approval(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    ready = _generate(tmp_path, raw_sidecar)
    pending = ensure_pending_approval(ready.proposal_path, now=lambda: NOW)
    assert isinstance(pending, DecisionWritten)
    assert pending.approval.decision == "pending"
    assert check_render_authorization(ready.proposal_path).authorized is False

    rejected = record_decision(ready.proposal_path, "rejected", now=lambda: NOW)
    assert isinstance(rejected, DecisionWritten)
    assert check_render_authorization(ready.proposal_path).decision == "rejected"
    assert check_render_authorization(ready.proposal_path).authorized is False

    approved = record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    assert isinstance(approved, DecisionWritten)
    gate = check_render_authorization(ready.proposal_path)
    assert gate.authorized is True
    assert gate.approval is not None
    assert gate.approval.proposal_sha256 == ready.proposal_sha256


@pytest.mark.parametrize("field", ["proposal_sha256", "source_identity_digest"])
def test_wrong_approval_binding_and_corruption_never_authorize(
    tmp_path: Path, raw_sidecar: dict[str, object], field: str
) -> None:
    ready = _generate(tmp_path, raw_sidecar)
    written = record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    assert isinstance(written, DecisionWritten)
    approval = written.approval.model_copy(update={field: "f" * 64})
    written.approval_path.write_bytes((approval.model_dump_json() + "\n").encode())
    assert check_render_authorization(ready.proposal_path).authorized is False
    written.approval_path.write_bytes(b"{broken")
    assert check_render_authorization(ready.proposal_path).authorized is False


def test_old_approval_does_not_apply_to_new_generation_and_decision_is_atomic(
    tmp_path: Path, raw_sidecar: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, sidecar, ffmpeg, process = _prepare(tmp_path, raw_sidecar)
    recording_id = str(raw_sidecar["recording_session_id"])
    first = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(first, ProposalReady)
    assert isinstance(record_decision(first.proposal_path, "approved"), DecisionWritten)

    second = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        parameters=AnalysisParameters(silence_threshold_db=-50),
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(second, ProposalReady)
    assert second.proposal.proposal_id != first.proposal.proposal_id
    assert check_render_authorization(second.proposal_path).authorized is False

    calls: list[tuple[Path, Path]] = []
    native_replace = approval_module.os.replace

    def observed_replace(source_path: Path, target_path: Path) -> None:
        calls.append((Path(source_path), Path(target_path)))
        native_replace(source_path, target_path)

    monkeypatch.setattr(approval_module.os, "replace", observed_replace)
    assert isinstance(record_decision(second.proposal_path, "rejected"), DecisionWritten)
    assert calls and calls[-1][1].name == "approval.json"
    assert isinstance(record_decision(second.proposal_path, "approved"), DecisionWritten)
    assert check_render_authorization(second.proposal_path).authorized is True


def test_sidecar_digest_change_creates_new_generation(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, sidecar, ffmpeg, process = _prepare(tmp_path, raw_sidecar)
    recording_id = str(raw_sidecar["recording_session_id"])
    first = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(first, ProposalReady)
    changed = deepcopy(raw_sidecar)
    lifecycle = changed["lifecycle"]
    assert isinstance(lifecycle, dict)
    lifecycle["finalized_at"] = "2026-08-03T13:00:00+00:00"
    sidecar.write_text(json.dumps(changed), encoding="utf-8")
    second = generate_proposal(
        source,
        sidecar,
        recording_id,
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=process,
        now=lambda: NOW,
    )
    assert isinstance(second, ProposalReady)
    assert second.proposal.proposal_id != first.proposal.proposal_id


def test_review_contains_escaped_details_navigation_and_safety(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    ready = _generate(tmp_path, raw_sidecar)
    assert isinstance(ensure_pending_approval(ready.proposal_path), DecisionWritten)
    review_path = write_review(ready.proposal_path)
    text = review_path.read_text(encoding="utf-8")
    assert ready.proposal.proposal_id in text
    assert "Konservativer zusammenhängender Silence-/Dead-Air-Bereich" in text
    assert "Im Video prüfen" in text
    assert "Die Rohaufnahme bleibt unverändert" in text
    assert "noch nicht gerendert" in text
    assert "recording &amp; review.mp4" in text

    assert "recording & review.mp4" not in text
    assert "async function hydrate()" in text
    assert "fetch(`${apiPrefix}/selection`)" in text


def test_selection_is_canonical_and_invalidates_selective_approval(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    ready = _generate(tmp_path, raw_sidecar)
    selection = ensure_selection(ready.proposal_path, now=lambda: NOW)
    assert isinstance(selection, SelectionReady)
    assert selection.selection.enabled_count == ready.proposal.total_proposed_cuts
    first = selection.selection.candidates[0]
    changed = update_selection(
        ready.proposal_path,
        {first.candidate_id: False},
        expected_selection_digest=selection.selection.selection_digest,
        now=lambda: NOW,
    )
    assert isinstance(changed, SelectionReady)
    assert changed.selection.enabled_count == 0
    assert isinstance(
        record_selected_decision(ready.proposal_path, "all_rejected", now=lambda: NOW),
        DecisionWritten,
    )
    assert check_render_authorization(ready.proposal_path).authorized is False
    restored = update_selection(
        ready.proposal_path,
        {first.candidate_id: True},
        expected_selection_digest=changed.selection.selection_digest,
        now=lambda: NOW,
    )
    assert isinstance(restored, SelectionReady)
    approval = record_selected_decision(
        ready.proposal_path, "selected_cuts_approved", now=lambda: NOW
    )
    assert isinstance(approval, DecisionWritten)
    assert check_render_authorization(ready.proposal_path).authorized is True
    changed_again = update_selection(
        ready.proposal_path,
        {first.candidate_id: False},
        expected_selection_digest=restored.selection.selection_digest,
        now=lambda: NOW,
    )
    assert isinstance(changed_again, SelectionReady)
    assert check_render_authorization(ready.proposal_path).authorized is False


def _bridge_request(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, dict[str, object]]:
    headers = {"Content-Type": content_type} if content_type is not None else {}
    parsed = urlsplit(url)
    connection = HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        connection.request(method, parsed.path, body=body, headers=headers)
        response = connection.getresponse()
        data = response.read()
        payload = (
            json.loads(data)
            if response.headers.get_content_type() == "application/json"
            else {}
        )
        return response.status, payload
    finally:
        connection.close()


def test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    ready = _generate(tmp_path, raw_sidecar)
    bridge = ReviewSelectionBridge(ready.proposal_path)
    bridge.start()
    endpoint = f"{bridge.api_prefix}/selection"
    try:
        assert bridge._server is not None
        host, port = bridge._server.server_address[:2]
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0

        status, initial = _bridge_request(endpoint)
        assert status == 200
        assert bridge.token not in json.dumps(initial)
        digest = initial["selection_digest"]
        assert isinstance(digest, str)
        candidates = initial["candidates"]
        assert isinstance(candidates, list)
        enabled = {
            item["candidate_id"]: item["enabled"]
            for item in candidates
            if isinstance(item, dict)
            and isinstance(item.get("candidate_id"), str)
            and isinstance(item.get("enabled"), bool)
        }
        assert enabled

        assert _bridge_request(f"{bridge.api_prefix}/unknown")[0] == 404
        assert _bridge_request(endpoint.replace(bridge.token, "wrong-token"))[0] == 404
        assert _bridge_request(endpoint, method="PUT")[0] == 501
        assert _bridge_request(
            endpoint, method="POST", body=b"{}", content_type="text/plain"
        )[0] == 415
        assert _bridge_request(
            endpoint, method="POST", body=b"{", content_type="application/json"
        )[0] == 400
        assert _bridge_request(
            endpoint,
            method="POST",
            body=b"x" * (bridge.max_request_bytes + 1),
            content_type="application/json",
        )[0] == 413

        missing_expected = json.dumps({"enabled": enabled}).encode()
        assert _bridge_request(
            endpoint,
            method="POST",
            body=missing_expected,
            content_type="application/json",
        )[0] == 400
        extra_path = json.dumps(
            {
                "enabled": enabled,
                "expected_selection_digest": digest,
                "path": "C:/untrusted.json",
            }
        ).encode()
        assert _bridge_request(
            endpoint,
            method="POST",
            body=extra_path,
            content_type="application/json",
        )[0] == 400

        candidate_id = next(iter(enabled))
        unknown = enabled | {"candidate-000000000000000000000000": True}
        invalid_candidate = json.dumps(
            {"enabled": unknown, "expected_selection_digest": digest}
        ).encode()
        status, current = _bridge_request(
            endpoint,
            method="POST",
            body=invalid_candidate,
            content_type="application/json",
        )
        assert status == 409
        assert current["selection_digest"] == digest

        changed_enabled = enabled | {candidate_id: False}
        changed_request = json.dumps(
            {"enabled": changed_enabled, "expected_selection_digest": digest}
        ).encode()
        status, persisted = _bridge_request(
            endpoint,
            method="POST",
            body=changed_request,
            content_type="application/json",
        )
        assert status == 200
        persisted_digest = persisted["selection_digest"]
        assert isinstance(persisted_digest, str) and persisted_digest != digest
        assert any(
            item["candidate_id"] == candidate_id and item["enabled"] is False
            for item in persisted["candidates"]
            if isinstance(item, dict)
        )

        stale_request = json.dumps(
            {"enabled": enabled, "expected_selection_digest": digest}
        ).encode()
        status, stale = _bridge_request(
            endpoint,
            method="POST",
            body=stale_request,
            content_type="application/json",
        )
        assert status == 409
        assert stale["selection_digest"] == persisted_digest
        assert ensure_selection(ready.proposal_path).selection.selection_digest == persisted_digest
    finally:
        bridge.close()

    with pytest.raises(OSError):
        _bridge_request(endpoint)
