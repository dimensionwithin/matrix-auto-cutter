"""Tests für Shorts-Stufe 1: Avatardatei anhand des Proposals nachschneiden.

Die reine Rechnung (frame_map, avatar_axis) wird ohne Video getestet, unter
anderem gegen das Beispiel aus
``artefakte/repeat/shorts-tonabgleich/TONABGLEICH-UND-ZEITBRUECKE-2026-08-10.md``
Teil C.2. Die Orchestrierung (avatar_cut) wird wie in ``test_shorts_tool.py``
mit einem gefälschten ffmpeg-Prozess getestet - kein echtes Video, kein
echtes ffmpeg läuft in diesen Tests.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from matrix_auto_cutter.approval import DecisionWritten, record_decision
from matrix_auto_cutter.cut_proposal import FfmpegProcessResult, ProposalReady, generate_proposal
from matrix_auto_cutter.shorts import avatar_axis as ax
from matrix_auto_cutter.shorts import avatar_cut as ac
from matrix_auto_cutter.shorts import frame_map as fm

# --- gemeinsame Testhilfen (dupliziert aus test_shorts_tool.py, siehe dort) --------


def _fake_ffmpeg_process(silence: bytes = b"") -> Callable[..., FfmpegProcessResult]:
    def runner(arguments: object, timeout: int) -> FfmpegProcessResult:
        del timeout
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version test-build\n")
        return FfmpegProcessResult(0, silence)

    return runner


def _generate_test_proposal(
    work_dir: Path,
    raw_sidecar: dict[str, Any],
    artifacts_dir: Path,
    *,
    seed: bytes,
    generated_at: datetime,
    silence: bytes,
) -> ProposalReady:
    work_dir.mkdir(parents=True, exist_ok=True)
    source = work_dir / "source.mp4"
    source.write_bytes(b"source-bytes-" + seed)
    from copy import deepcopy

    payload = deepcopy(raw_sidecar)
    source_identity = payload["source"]
    assert isinstance(source_identity, dict)
    source_identity.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    ffmpeg = work_dir / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fake-ffmpeg-binary")
    result = generate_proposal(
        source,
        sidecar,
        str(payload["recording_session_id"]),
        artifacts_dir,
        ffmpeg,
        process_runner=_fake_ffmpeg_process(silence=silence),
        now=lambda: generated_at,
    )
    assert isinstance(result, ProposalReady), result
    return result


def _approved_proposal(
    work_dir: Path,
    raw_sidecar: dict[str, Any],
    artifacts_dir: Path,
    *,
    seed: bytes,
    generated_at: datetime,
    silence: bytes,
) -> ProposalReady:
    ready = _generate_test_proposal(
        work_dir, raw_sidecar, artifacts_dir, seed=seed, generated_at=generated_at, silence=silence
    )
    outcome = record_decision(ready.proposal_path, "approved", now=lambda: generated_at)
    assert isinstance(outcome, DecisionWritten), outcome
    return ready


GENERATED_AT = datetime(2026, 8, 11, 9, 0, 0, tzinfo=UTC)
SILENCE_2S_TO_4S = b"silence_start: 2.0\nsilence_end: 4.0 | silence_duration: 2.0\n"


# --- frame_map: reine Abbildung Quellframe -> gerendertes Frame -------------------


def test_worked_example_frame_5400_lands_on_rendered_3809() -> None:
    """Proposal d5c634d3 aus Teil C.2: Cuts (0,941),(2686,2723),(4699,5017),(5105,5400)."""
    cuts = [(0, 941), (2686, 2723), (4699, 5017), (5105, 5400)]
    segments = fm.keep_segments_from_intervals(cuts, 64150)
    assert fm.map_source_frame(segments, 5400) == 1745 + 1976 + 88
    assert fm.map_source_frame(segments, 5400) == 3809


def test_worked_example_frames_inside_cuts_vanish() -> None:
    cuts = [(0, 941), (2686, 2723), (4699, 5017), (5105, 5400)]
    segments = fm.keep_segments_from_intervals(cuts, 64150)
    assert fm.map_source_frame(segments, 600) is None  # t=10s, in cut #1
    assert fm.map_source_frame(segments, 2700) is None  # t=45s, in cut #2


def test_map_source_frame_first_kept_frame_maps_to_zero() -> None:
    segments = fm.keep_segments_from_intervals([(0, 10)], 20)
    assert fm.map_source_frame(segments, 10) == 0
    assert fm.map_source_frame(segments, 19) == 9


def test_map_source_frame_negative_rejected() -> None:
    segments = fm.keep_segments_from_intervals([(0, 10)], 20)
    with pytest.raises(ValueError, match="negativ"):
        fm.map_source_frame(segments, -1)


def test_keep_segments_no_cuts_is_one_full_segment() -> None:
    segments = fm.keep_segments_from_intervals([], 100)
    assert segments == (fm.KeepSegment(0, 100),)


def test_keep_segments_cut_covers_whole_range() -> None:
    segments = fm.keep_segments_from_intervals([(0, 100)], 100)
    assert segments == ()


def test_keep_segments_rejects_unsorted_overlap() -> None:
    with pytest.raises(ValueError, match="sortiert"):
        fm.keep_segments_from_intervals([(10, 20), (15, 25)], 100)


def test_keep_segments_rejects_out_of_bounds() -> None:
    with pytest.raises(ValueError, match="sortiert"):
        fm.keep_segments_from_intervals([(0, 200)], 100)


def test_keep_segments_rejects_non_positive_total() -> None:
    with pytest.raises(ValueError, match="positiv"):
        fm.keep_segments_from_intervals([], 0)


def test_keep_segment_rejects_empty_interval() -> None:
    with pytest.raises(ValueError, match="start_frame < end_frame"):
        fm.KeepSegment(5, 5)


def test_effective_cuts_none_means_all() -> None:
    from matrix_auto_cutter.cut_proposal import AnalysisParameters  # noqa: F401

    class _FakeCut:
        def __init__(self, candidate_id: str) -> None:
            self.candidate_id = candidate_id

    cuts = [_FakeCut("a"), _FakeCut("b")]
    assert fm.effective_cuts(cuts, None) == tuple(cuts)  # type: ignore[arg-type]
    assert fm.effective_cuts(cuts, ["a"]) == (cuts[0],)  # type: ignore[arg-type]
    assert fm.effective_cuts(cuts, []) == ()  # type: ignore[arg-type]


# --- avatar_axis: Verschiebung um |L| und die zwei Ränder --------------------------


def test_shift_intervals_basic() -> None:
    shifted = ax.shift_intervals_to_avatar_axis(
        [(1000, 1100), (2000, 2050)], lag_frames=500, avatar_frame_count=10_000
    )
    assert shifted == ((500, 600), (1500, 1550))


def test_shift_intervals_clips_leading_cut_that_starts_before_avatar() -> None:
    shifted = ax.shift_intervals_to_avatar_axis(
        [(0, 941)], lag_frames=500, avatar_frame_count=10_000
    )
    assert shifted == ((0, 441),)


def test_shift_intervals_drops_cut_entirely_before_avatar_start() -> None:
    shifted = ax.shift_intervals_to_avatar_axis(
        [(0, 400)], lag_frames=500, avatar_frame_count=10_000
    )
    assert shifted == ()


def test_shift_intervals_drops_cut_entirely_after_avatar_end() -> None:
    shifted = ax.shift_intervals_to_avatar_axis(
        [(9700, 9800)], lag_frames=500, avatar_frame_count=9000
    )
    assert shifted == ()


def test_shift_intervals_clips_trailing_cut_past_avatar_end() -> None:
    shifted = ax.shift_intervals_to_avatar_axis(
        [(9400, 9600)], lag_frames=500, avatar_frame_count=9000
    )
    assert shifted == ((8900, 9000),)


def test_shift_intervals_rejects_negative_lag() -> None:
    with pytest.raises(ValueError, match="negativ"):
        ax.shift_intervals_to_avatar_axis([(0, 10)], lag_frames=-1, avatar_frame_count=100)


def test_shift_intervals_rejects_non_positive_avatar_count() -> None:
    with pytest.raises(ValueError, match="positiv"):
        ax.shift_intervals_to_avatar_axis([(0, 10)], lag_frames=0, avatar_frame_count=0)


def test_rendered_axis_coverage_covered_by_intro_cut() -> None:
    # Erste erhaltene Bildschirmframe ist 941 (Intro-Cut [0,941)); lag=500 < 941 -> abgedeckt.
    segments = fm.keep_segments_from_intervals([(0, 941)], 10_000)
    coverage = ax.rendered_axis_coverage(segments, lag_frames=500, avatar_frame_count=9500)
    assert coverage.first_rendered_frame == 0
    assert coverage.missing_frames_front == 0


def test_rendered_axis_coverage_not_covered_when_lag_exceeds_intro_cut() -> None:
    # Keep-Segment beginnt bei 100 (Cut [0,100)); lag=500 liegt mitten im Keep-Segment ->
    # Renderpositionen 0..399 (Screenframes 100..499) haben keine Avatar-Entsprechung.
    segments = fm.keep_segments_from_intervals([(0, 100)], 10_000)
    coverage = ax.rendered_axis_coverage(segments, lag_frames=500, avatar_frame_count=9500)
    assert coverage.first_rendered_frame == 400
    assert coverage.missing_frames_front == 400


def test_rendered_axis_coverage_no_keep_segments_at_all() -> None:
    with pytest.raises(ValueError, match="inkonsistent"):
        ax.rendered_axis_coverage((), lag_frames=200, avatar_frame_count=50)


def test_rendered_axis_coverage_worked_example_punkt_a() -> None:
    # LAGMESSUNG-2026-08-11.md Punkt A, Lauf 2026-08-09 07-25-37: von Hand
    # nachgerechnet (screen_keep_segments = Komplement von source_frame_count=8302).
    segments = fm.keep_segments_from_intervals(
        [(0, 474), (525, 626), (671, 6078), (8110, 8302)], 8302
    )
    coverage = ax.rendered_axis_coverage(segments, lag_frames=528, avatar_frame_count=7757)
    assert coverage.first_rendered_frame == 51
    assert coverage.missing_frames_front == 51
    assert coverage.last_rendered_frame == 2127
    # Auftrag 12, Punkt 1: rechnet jetzt auf der gerenderten Achse (total=2128 Frames,
    # 51+45+2032), nicht mehr die rohe Stoppdifferenz (die stand hier vorher fälschlich als 17).
    assert coverage.missing_frames_back == 0


def test_rendered_axis_coverage_last_frame_inconsistent_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ax, "map_source_frame_floor", lambda *a, **k: None)
    segments = fm.keep_segments_from_intervals([(0, 10)], 100)
    with pytest.raises(ValueError, match="inkonsistent"):
        ax.rendered_axis_coverage(segments, lag_frames=0, avatar_frame_count=5)


def test_trailing_edge_reports_missing_tail() -> None:
    finding = ax.trailing_edge_finding(
        source_frame_count=64150, lag_frames=528, avatar_frame_count=64150 - 528 - 60
    )
    assert finding.missing_frames == 60


def test_trailing_edge_zero_when_avatar_covers_everything() -> None:
    finding = ax.trailing_edge_finding(
        source_frame_count=1000, lag_frames=100, avatar_frame_count=1000
    )
    assert finding.missing_frames == 0


# --- lag_frames_from_ms: Vorzeichenkonvention --------------------------------------


def test_lag_frames_from_ms_negative_lag() -> None:
    # -8800 ms bei 60fps -> 528 Frames (Paar 4 aus dem Tonabgleichsbericht).
    assert ac.lag_frames_from_ms(-8800.0, fps_num=60, fps_den=1) == 528


def test_lag_frames_from_ms_zero_lag() -> None:
    assert ac.lag_frames_from_ms(0.0, fps_num=60, fps_den=1) == 0


def test_lag_frames_from_ms_positive_rejected() -> None:
    with pytest.raises(ValueError, match="Konvention"):
        ac.lag_frames_from_ms(1.0, fps_num=60, fps_den=1)


def test_lag_frames_from_ms_rounds() -> None:
    # -108.33ms bei 60fps = 6.4998 Frames -> rundet auf 6 (Paar mit kleinstem |L|).
    assert ac.lag_frames_from_ms(-100.0, fps_num=60, fps_den=1) == 6


# --- build_avatar_cut_plan: Planung ohne IO ----------------------------------------


def test_build_avatar_cut_plan_success(tmp_path: Path, raw_sidecar: dict[str, Any]) -> None:
    ready = _approved_proposal(
        tmp_path / "run",
        raw_sidecar,
        tmp_path / "artifacts",
        seed=b"1",
        generated_at=GENERATED_AT,
        silence=SILENCE_2S_TO_4S,
    )
    proposal = ready.proposal
    assert proposal.status == "ready"
    assert len(proposal.proposed_cuts) >= 1
    cut = proposal.proposed_cuts[0]
    lag = ac.LagInput(lag_ms=-50.0, source="test")
    avatar_frame_count = proposal.source_frame_count  # Avatar mind. so lang wie nötig
    plan = ac.build_avatar_cut_plan(
        proposal, active_candidate_ids=None, lag=lag, avatar_frame_count=avatar_frame_count
    )
    assert isinstance(plan, ac.AvatarCutPlan), plan
    assert plan.lag_frames == 3  # -50ms * 60fps / 1000 = -3 -> |L|=3
    assert plan.applied_cut_count == len(proposal.proposed_cuts)
    assert plan.expected_output_frame_count == sum(s.length for s in plan.avatar_keep_segments)
    assert plan.expected_output_frame_count < avatar_frame_count
    # Der verschobene Schnitt beginnt lag_frames früher als der Bildschirmschnitt.
    shifted_start, _shifted_end = plan.avatar_cut_intervals[0]
    assert shifted_start == max(0, cut.start_frame - plan.lag_frames)


def test_build_avatar_cut_plan_rejects_non_positive_avatar_frame_count(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run",
        raw_sidecar,
        tmp_path / "artifacts",
        seed=b"1",
        generated_at=GENERATED_AT,
        silence=SILENCE_2S_TO_4S,
    )
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=0,
    )
    assert isinstance(plan, ac.PlanFailed)
    assert plan.code == "invalid_avatar_frame_count"


def test_build_avatar_cut_plan_rejects_no_cuts_proposal(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _generate_test_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=b"",
    )
    assert ready.proposal.status == "no_cuts"
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=600,
    )
    assert isinstance(plan, ac.PlanFailed)
    assert plan.code == "no_cuts_in_proposal"


def test_build_avatar_cut_plan_rejects_positive_lag(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=5.0, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.PlanFailed)
    assert plan.code == "lag_sign_violation"


def test_build_avatar_cut_plan_rejects_empty_active_selection(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=["candidate-does-not-exist" + "0" * 8],
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.PlanFailed)
    assert plan.code == "no_active_cuts"


def test_build_avatar_cut_plan_subsets_by_active_candidate_ids(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    only_id = ready.proposal.proposed_cuts[0].candidate_id
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=[only_id],
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.AvatarCutPlan)
    assert plan.applied_cut_count == 1


# --- ffmpeg-Kommandoaufbau ---------------------------------------------------------


def test_filter_complex_single_segment() -> None:
    filt, vlabel, alabel = ac.build_ffmpeg_filter_complex(
        (fm.KeepSegment(0, 100),), fps_num=60, fps_den=1
    )
    assert "trim=start_frame=0:end_frame=100" in filt
    assert vlabel == "[v0]"
    assert alabel == "[a0]"
    assert "concat" not in filt


def test_filter_complex_multi_segment_uses_concat() -> None:
    filt, vlabel, alabel = ac.build_ffmpeg_filter_complex(
        (fm.KeepSegment(0, 100), fm.KeepSegment(200, 300)), fps_num=60, fps_den=1
    )
    assert "concat=n=2:v=1:a=1" in filt
    assert vlabel == "[outv]"
    assert alabel == "[outa]"


def test_filter_complex_rejects_empty_segments() -> None:
    with pytest.raises(ValueError, match="Keep-Segment"):
        ac.build_ffmpeg_filter_complex((), fps_num=60, fps_den=1)


def test_build_ffmpeg_arguments_shape(tmp_path: Path, raw_sidecar: dict[str, Any]) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.AvatarCutPlan)
    args = ac.build_ffmpeg_arguments(
        Path("ffmpeg.exe"), tmp_path / "avatar.mp4", tmp_path / "out.mp4", plan
    )
    assert args[0] == "ffmpeg.exe"
    assert "-filter_complex" in args
    assert args[-1] == str(tmp_path / "out.mp4")
    assert "-r" in args
    assert args[args.index("-r") + 1] == "60"


# --- write_avatar_cut_report: atomares Schreiben -----------------------------------


def test_write_avatar_cut_report_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "out" / ac.AVATAR_CUT_REPORT_NAME
    ac.write_avatar_cut_report(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}
    ac.write_avatar_cut_report(path, {"a": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 2}


def test_plan_report_payload_shape(tmp_path: Path, raw_sidecar: dict[str, Any]) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.AvatarCutPlan)
    payload = ac.plan_report_payload(plan, actual_output_frame_count=123)
    assert payload["actual_output_frame_count"] == 123
    assert payload["lag"] == {
        "ms": -10.0,
        "frames": plan.lag_frames,
        "method": "override",
        "peak_ratio": None,
    }
    assert payload["applied_cut_count"] == plan.applied_cut_count


# --- probe_frame_count: ffprobe-Auskunft -------------------------------------------


def test_probe_frame_count_no_ffprobe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ac, "discover_ffprobe", lambda: None)
    assert ac.probe_frame_count(tmp_path / "video.mp4") is None


def test_probe_frame_count_parses_stdout(tmp_path: Path) -> None:
    class _FakeCompleted:
        returncode = 0
        stdout = b"1234\n"

    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    orig = sp.run
    sp.run = fake_run  # type: ignore[assignment]
    try:
        assert ac.probe_frame_count(tmp_path / "v.mp4", ffprobe_path=Path("ffprobe.exe")) == 1234
    finally:
        sp.run = orig  # type: ignore[assignment]


def test_probe_frame_count_nonzero_exit(tmp_path: Path) -> None:
    class _FakeCompleted:
        returncode = 1
        stdout = b""

    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    orig = sp.run
    sp.run = fake_run  # type: ignore[assignment]
    try:
        assert ac.probe_frame_count(tmp_path / "v.mp4", ffprobe_path=Path("ffprobe.exe")) is None
    finally:
        sp.run = orig  # type: ignore[assignment]


def test_probe_frame_count_unparseable_output(tmp_path: Path) -> None:
    class _FakeCompleted:
        returncode = 0
        stdout = b"N/A\n"

    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> _FakeCompleted:
        return _FakeCompleted()

    orig = sp.run
    sp.run = fake_run  # type: ignore[assignment]
    try:
        assert ac.probe_frame_count(tmp_path / "v.mp4", ffprobe_path=Path("ffprobe.exe")) is None
    finally:
        sp.run = orig  # type: ignore[assignment]


def test_probe_frame_count_process_error(tmp_path: Path) -> None:
    import subprocess as sp

    def fake_run(*args: object, **kwargs: object) -> object:
        raise OSError("boom")

    orig = sp.run
    sp.run = fake_run  # type: ignore[assignment]
    try:
        assert ac.probe_frame_count(tmp_path / "v.mp4", ffprobe_path=Path("ffprobe.exe")) is None
    finally:
        sp.run = orig  # type: ignore[assignment]


# --- execute_avatar_cut: Erfolg und Fehlschlag -------------------------------------


def _plan_for(ready: ProposalReady, *, lag_ms: float = -10.0) -> ac.AvatarCutPlan:
    plan = ac.build_avatar_cut_plan(
        ready.proposal,
        active_candidate_ids=None,
        lag=ac.LagInput(lag_ms=lag_ms, source="test"),
        avatar_frame_count=ready.proposal.source_frame_count,
    )
    assert isinstance(plan, ac.AvatarCutPlan)
    return plan


def test_execute_avatar_cut_success(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = _plan_for(ready)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: 999)

    def fake_runner(arguments: Any, timeout: int) -> ac.ProcessResult:
        del arguments, timeout
        return ac.ProcessResult(0, b"")

    result = ac.execute_avatar_cut(
        avatar_path=tmp_path / "avatar.mp4",
        output_video_path=tmp_path / "out" / "avatar-cut.mp4",
        output_report_path=tmp_path / "out" / "avatar-cut.json",
        plan=plan,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=fake_runner,
    )
    assert result.status == "written"
    assert result.actual_output_frame_count == 999
    payload = json.loads((tmp_path / "out" / "avatar-cut.json").read_text(encoding="utf-8"))
    assert payload["actual_output_frame_count"] == 999


def test_execute_avatar_cut_creates_missing_output_directory(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = _plan_for(ready)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: 999)
    target_dir = tmp_path / "not-yet-created"
    assert not target_dir.exists()

    def fake_runner(arguments: Any, timeout: int) -> ac.ProcessResult:
        del arguments, timeout
        assert target_dir.is_dir()
        return ac.ProcessResult(0, b"")

    result = ac.execute_avatar_cut(
        avatar_path=tmp_path / "avatar.mp4",
        output_video_path=target_dir / "avatar-cut.mp4",
        output_report_path=target_dir / "avatar-cut.json",
        plan=plan,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=fake_runner,
    )
    assert result.status == "written"


def test_execute_avatar_cut_ffmpeg_failure_still_writes_report(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    plan = _plan_for(ready)

    def failing_runner(arguments: Any, timeout: int) -> ac.ProcessResult:
        del arguments, timeout
        return ac.ProcessResult(1, b"encoder explodierte")

    result = ac.execute_avatar_cut(
        avatar_path=tmp_path / "avatar.mp4",
        output_video_path=tmp_path / "out" / "avatar-cut.mp4",
        output_report_path=tmp_path / "out" / "avatar-cut.json",
        plan=plan,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=failing_runner,
    )
    assert result.status == "failed"
    assert result.output_video_path is None
    assert "encoder explodierte" in (result.error or "")
    payload = json.loads((tmp_path / "out" / "avatar-cut.json").read_text(encoding="utf-8"))
    assert "error" in payload


# --- run_stage1_for_job: Ende-zu-Ende über die Auftragsdatei -----------------------


def _write_job(
    tmp_path: Path, *, proposal_path: Path, avatar_path: Path, video_name: str = "v1"
) -> Path:
    job_path = tmp_path / "job" / "shorts-job.json"
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(
        json.dumps(
            {
                "video_name": video_name,
                "proposal": {"path": str(proposal_path)},
                "avatar": {"path": str(avatar_path)},
            }
        ),
        encoding="utf-8",
    )
    return job_path


def test_run_stage1_for_job_success(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: ready.proposal.source_frame_count)

    def fake_runner(arguments: Any, timeout: int) -> ac.ProcessResult:
        del arguments, timeout
        return ac.ProcessResult(0, b"")

    output_root = tmp_path / "artefakte"
    result = ac.run_stage1_for_job(
        job_path,
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        output_root=output_root,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=fake_runner,
    )
    assert isinstance(result, ac.AvatarCutResult)
    assert result.status == "written"
    assert result.output_video_path == str(output_root / "v1" / "avatar-cut.mp4")


def test_run_stage1_for_job_unreadable_job(tmp_path: Path) -> None:
    result = ac.run_stage1_for_job(
        tmp_path / "missing.json",
        lag=ac.LagInput(lag_ms=-10.0, source="test"),
        output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "job_unreadable"


def test_run_stage1_for_job_missing_proposal_field(tmp_path: Path) -> None:
    job_path = tmp_path / "shorts-job.json"
    job_path.write_text(json.dumps({"video_name": "v1", "avatar": {"path": "x"}}), encoding="utf-8")
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=-10.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "no_proposal"


def test_run_stage1_for_job_missing_avatar_field(tmp_path: Path) -> None:
    job_path = tmp_path / "shorts-job.json"
    job_path.write_text(
        json.dumps({"video_name": "v1", "proposal": {"path": "x"}}), encoding="utf-8"
    )
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=-10.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "no_avatar"


def test_run_stage1_for_job_avatar_file_missing(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    job_path = _write_job(
        tmp_path, proposal_path=ready.proposal_path, avatar_path=tmp_path / "does-not-exist.mp4"
    )
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=-10.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "avatar_missing"


def test_run_stage1_for_job_not_authorized(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    ready = _generate_test_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    # bewusst keine Entscheidung getroffen -> "pending", nicht autorisiert
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=-10.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "not_authorized"


def test_run_stage1_for_job_avatar_frame_count_unknown(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: None)
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=-10.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "avatar_frame_count_unknown"


def test_run_stage1_for_job_measures_lag_when_not_given(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: ready.proposal.source_frame_count)
    calls: list[tuple[Path, Path, Path]] = []

    def fake_measure(*, ffmpeg_path: Path, screen_path: Path, avatar_path: Path) -> ac.LagInput:
        calls.append((ffmpeg_path, screen_path, avatar_path))
        return ac.LagInput(lag_ms=-10.0, source="test-measured", method="measured", peak_ratio=42.0)

    monkeypatch.setattr(ac, "measure_lag_input", fake_measure)

    def fake_runner(arguments: Any, timeout: int) -> ac.ProcessResult:
        del arguments, timeout
        return ac.ProcessResult(0, b"")

    output_root = tmp_path / "artefakte"
    result = ac.run_stage1_for_job(
        job_path,
        lag=None,
        output_root=output_root,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=fake_runner,
    )
    assert isinstance(result, ac.AvatarCutResult)
    assert result.status == "written"
    assert len(calls) == 1
    assert calls[0] == (Path("ffmpeg.exe"), Path(ready.proposal.source_path), avatar_path)
    assert result.plan is not None
    assert result.plan.lag_method == "measured"
    assert result.plan.lag_peak_ratio == 42.0


def test_run_stage1_for_job_lag_measurement_failed(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(
        ac, "measure_lag_input", lambda **k: ac.LagMeasurementUnavailable("Stille")
    )
    result = ac.run_stage1_for_job(
        job_path, lag=None, output_root=tmp_path, ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "lag_measurement_failed"
    assert result.message_de == "Stille"


def test_run_stage1_for_job_plan_failure_propagates(
    tmp_path: Path, raw_sidecar: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: 1)  # too small -> invalid, but >0
    result = ac.run_stage1_for_job(
        job_path, lag=ac.LagInput(lag_ms=100.0, source="test"), output_root=tmp_path,
        ffmpeg_path=Path("ffmpeg.exe"),
    )
    assert isinstance(result, ac.Stage1Failed)
    assert result.code == "lag_sign_violation"


# --- main(): CLI --------------------------------------------------------------------


def test_main_ffmpeg_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: None)
    exit_code = ac.main([str(tmp_path / "shorts-job.json"), "--lag-ms", "-10.0"])
    assert exit_code == 2
    assert "ffmpeg nicht gefunden" in capsys.readouterr().out


def test_main_stage1_failure_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: Path("ffmpeg.exe"))
    exit_code = ac.main(
        [str(tmp_path / "missing.json"), "--lag-ms", "-10.0", "--output-root", str(tmp_path)]
    )
    assert exit_code == 1
    assert "ANGEHALTEN" in capsys.readouterr().out


def test_main_success(
    tmp_path: Path,
    raw_sidecar: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: Path("ffmpeg.exe"))
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: ready.proposal.source_frame_count)
    exit_code = ac.main(
        [str(job_path), "--lag-ms", "-10.0", "--output-root", str(tmp_path / "artefakte")],
        process_runner=lambda arguments, timeout_seconds: ac.ProcessResult(0, b""),
    )
    assert exit_code == 0
    assert "geschrieben" in capsys.readouterr().out


def test_main_without_lag_ms_measures(
    tmp_path: Path,
    raw_sidecar: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: Path("ffmpeg.exe"))
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: ready.proposal.source_frame_count)
    monkeypatch.setattr(
        ac,
        "measure_lag_input",
        lambda **k: ac.LagInput(lag_ms=-10.0, source="measured", method="measured"),
    )
    exit_code = ac.main(
        [str(job_path), "--output-root", str(tmp_path / "artefakte")],
        process_runner=lambda arguments, timeout_seconds: ac.ProcessResult(0, b""),
    )
    assert exit_code == 0
    assert "geschrieben" in capsys.readouterr().out


def test_main_without_lag_ms_measurement_failed_reported(
    tmp_path: Path,
    raw_sidecar: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: Path("ffmpeg.exe"))
    monkeypatch.setattr(
        ac, "measure_lag_input", lambda **k: ac.LagMeasurementUnavailable("kein eindeutiger Gipfel")
    )
    exit_code = ac.main([str(job_path), "--output-root", str(tmp_path / "artefakte")])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ANGEHALTEN" in out
    assert "kein eindeutiger Gipfel" in out


def test_main_ffmpeg_failure_reported(
    tmp_path: Path,
    raw_sidecar: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ready = _approved_proposal(
        tmp_path / "run", raw_sidecar, tmp_path / "artifacts", seed=b"1",
        generated_at=GENERATED_AT, silence=SILENCE_2S_TO_4S,
    )
    avatar_path = tmp_path / "avatar.mp4"
    avatar_path.write_bytes(b"fake-avatar")
    job_path = _write_job(tmp_path, proposal_path=ready.proposal_path, avatar_path=avatar_path)
    monkeypatch.setattr(ac, "discover_ffmpeg", lambda *a, **k: Path("ffmpeg.exe"))
    monkeypatch.setattr(ac, "probe_frame_count", lambda *a, **k: ready.proposal.source_frame_count)
    exit_code = ac.main(
        [str(job_path), "--lag-ms", "-10.0", "--output-root", str(tmp_path / "artefakte")],
        process_runner=lambda arguments, timeout_seconds: ac.ProcessResult(1, b"x"),
    )
    assert exit_code == 1
    assert "ffmpeg fehlgeschlagen" in capsys.readouterr().out
