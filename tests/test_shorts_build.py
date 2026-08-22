"""Tests fuer Auftrag shorts-bau: die Kette zu einem Aufruf verdrahten.

Reine Verdrahtungstests - die einzelnen Stufen (chart_crop, canvas,
avatar_canvas, subtitle_burn) sind in ihren eigenen Testdateien abgedeckt und
werden hier fuer den End-zu-Ende-Test gefaelscht (kein echtes ffmpeg noetig).
Der reale Pruefstein gegen die Aufnahme 2026-08-07 11-35-16 steht im Bericht
``artefakte/repeat/shorts-bau/BERICHT-2026-08-18.md``.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from matrix_auto_cutter.approval import ApprovalGateResult
from matrix_auto_cutter.shorts import avatar_canvas, build, canvas, chart_crop, subtitle_burn
from matrix_auto_cutter.shorts.candidates import load_candidates
from matrix_auto_cutter.shorts.level_cut import LevelCutFailed, LevelSnap, StilleVorlauf
from matrix_auto_cutter.shorts.scene_windows import SceneWindow


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _word_token(start_ms: int, end_ms: int, text: str) -> dict[str, object]:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


def _write_whole_video_transcript(job_dir: Path, tokens: list[dict[str, object]]) -> None:
    raw_json_path, _ = build.transcript_paths(job_dir, wav_name=build.RENDERED_WAV_NAME)
    _write_json(raw_json_path, {"transcription": [{"tokens": tokens}]})


# ---------------------------------------------------------------------------
# load_job / derive_inputs - Punkt 1, fail closed bei fehlenden Feldern.
# ---------------------------------------------------------------------------


def test_load_job_rejects_missing_file(tmp_path: Path) -> None:
    result = build.load_job(tmp_path / "nope.json")
    assert isinstance(result, build.BuildFailed)
    assert result.code == "job_unreadable"


def test_load_job_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "shorts-job.json"
    path.write_text("{not json", encoding="utf-8")
    result = build.load_job(path)
    assert isinstance(result, build.BuildFailed)
    assert result.code == "job_invalid_json"


def test_derive_inputs_rejects_missing_video_name(tmp_path: Path) -> None:
    result = build.derive_inputs(
        {"rendered_video": {"path": "x.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=None,
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "job_field_missing"


def test_derive_inputs_rejects_missing_rendered_path(tmp_path: Path) -> None:
    result = build.derive_inputs(
        {"video_name": "vid"},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=None,
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "job_field_missing"


def _stub_chart_crop_probe_dimensions(
    monkeypatch, dimensions_by_path: dict[str, tuple[int, int]]
) -> None:
    def fake(path, **k):
        del k
        return dimensions_by_path.get(str(path))

    monkeypatch.setattr(chart_crop, "probe_dimensions", fake)


def test_derive_inputs_measures_the_five_plus_four_values_once(
    tmp_path: Path, monkeypatch
) -> None:
    """Auftrag shorts-framezahl-cache: die vier je Aufnahme konstanten Werte
    (Aufloesung des gerenderten Videos, Avatar-Framezahl/-Aufloesung) werden
    hier zusammen mit den fuenf urspruenglichen Werten EINMAL erhoben."""
    monkeypatch.setattr(build, "probe_frame_count", lambda path, **k: (
        4321 if str(path) == "rendered.mp4" else 999
    ))
    _stub_chart_crop_probe_dimensions(
        monkeypatch,
        {
            "rendered.mp4": (chart_crop.SOURCE_WIDTH, chart_crop.SOURCE_HEIGHT),
            "avatar.mp4": (1920, 1080),
        },
    )
    result = build.derive_inputs(
        {"video_name": "2026-08-07 11-35-16", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.DerivedInputs)
    assert result.canvas_recording_id == "2026-08-07 11-35-16"
    assert result.avatar_recording_id == "2026-08-07 11-35-16"
    assert result.expected_avatar_frame_count == 4321
    assert result.rendered_video_path == Path("rendered.mp4")
    assert result.rendered_video_dimensions == (chart_crop.SOURCE_WIDTH, chart_crop.SOURCE_HEIGHT)
    assert result.avatar_frame_count == 999
    assert result.avatar_source_width == 1920
    assert result.avatar_source_height == 1080


def test_derive_inputs_rejects_unmeasurable_rendered_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: None)
    result = build.derive_inputs(
        {"video_name": "vid", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "rendered_video_frame_count_unknown"


def test_derive_inputs_rejects_unmeasurable_rendered_dimensions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 4321)
    _stub_chart_crop_probe_dimensions(monkeypatch, {})
    result = build.derive_inputs(
        {"video_name": "vid", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "chart_crop_resolution_unknown"


def test_derive_inputs_rejects_mismatched_rendered_dimensions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 4321)
    _stub_chart_crop_probe_dimensions(monkeypatch, {"rendered.mp4": (100, 100)})
    result = build.derive_inputs(
        {"video_name": "vid", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "chart_crop_resolution_mismatch"


def test_derive_inputs_rejects_unmeasurable_avatar_dimensions(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 4321)
    _stub_chart_crop_probe_dimensions(
        monkeypatch, {"rendered.mp4": (chart_crop.SOURCE_WIDTH, chart_crop.SOURCE_HEIGHT)}
    )
    result = build.derive_inputs(
        {"video_name": "vid", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "avatar_resolution_unknown"


def test_derive_inputs_rejects_unmeasurable_avatar_frame_count(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(build, "probe_frame_count", lambda path, **k: (
        4321 if str(path) == "rendered.mp4" else None
    ))
    _stub_chart_crop_probe_dimensions(
        monkeypatch,
        {
            "rendered.mp4": (chart_crop.SOURCE_WIDTH, chart_crop.SOURCE_HEIGHT),
            "avatar.mp4": (1920, 1080),
        },
    )
    result = build.derive_inputs(
        {"video_name": "vid", "rendered_video": {"path": "rendered.mp4"}},
        avatar_cut_path=Path("avatar.mp4"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=5,
    )
    assert isinstance(result, build.BuildFailed)
    assert result.code == "avatar_frame_count_unknown"


# ---------------------------------------------------------------------------
# _load_rendered_charts_windows - Punkt 3a: kein Journal heisst "melden, alle bauen".
# ---------------------------------------------------------------------------


def test_scene_filter_skipped_when_no_recording_id_in_job(tmp_path: Path) -> None:
    windows, info, _segmente = build._load_rendered_charts_windows({}, journal_directory=tmp_path)
    assert windows is None
    assert info.applied is False
    assert info.skip_reason == "kein_recording_id_im_auftrag"


def test_scene_filter_skipped_when_journal_missing(tmp_path: Path) -> None:
    job = {"proposal": {"recording_id": "rec-x", "path": str(tmp_path / "cut-proposal.json")}}
    windows, info, _segmente = build._load_rendered_charts_windows(job, journal_directory=tmp_path)
    assert windows is None
    assert info.applied is False
    assert info.skip_reason == "journal_nicht_gefunden"
    assert info.journal_path == tmp_path / "rec-x.recording-journal.ndjson"


@dataclass(frozen=True, slots=True)
class _FakeCut:
    candidate_id: str
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class _FakeProposal:
    proposed_cuts: list[_FakeCut]
    source_frame_count: int


def test_scene_filter_maps_windows_when_journal_and_proposal_available(
    tmp_path: Path, monkeypatch
) -> None:
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal_path = journal_dir / "rec-x.recording-journal.ndjson"
    journal_path.write_text("", encoding="utf-8")

    fake_gate = ApprovalGateResult(
        authorized=True,
        decision="approved",
        reason="ok",
        proposal=_FakeProposal(proposed_cuts=[], source_frame_count=100000),
        approval=None,
    )
    monkeypatch.setattr(build, "inspect_approval_state", lambda path: fake_gate)
    monkeypatch.setattr(
        build, "load_scene_windows", lambda path, **k: (SceneWindow(0, 5000),)
    )

    job = {"proposal": {"recording_id": "rec-x", "path": str(tmp_path / "cut-proposal.json")}}
    windows, info, _segmente = build._load_rendered_charts_windows(
        job, journal_directory=journal_dir
    )
    assert windows == ((0, 5000),)
    assert info.applied is True
    assert info.skip_reason is None


# ---------------------------------------------------------------------------
# _words_for_span - Woerter auf 0 verschoben, Randwoerter geklemmt statt
# verworfen (Auftrag shorts-untertitel-randwoerter-2).
# ---------------------------------------------------------------------------


def test_words_for_span_shifts_and_filters() -> None:
    words = [
        build.Word(9000, 9200, "Vor"),
        build.Word(10000, 10300, "Start"),
        build.Word(15000, 15300, "Mitte"),
        build.Word(20800, 21100, "Nach"),
    ]
    selected = build._words_for_span(words, 10000, 20000)
    assert [w.text for w in selected] == ["Start", "Mitte"]
    assert selected[0].start_ms == 0
    assert selected[1].start_ms == 5000


def test_words_for_span_klemmt_randwort_ueber_der_schwelle() -> None:
    """Ein Wort, das die Spanne mit >= RANDWORT_MINDESTANTEIL ueberlappt, wird
    aufgenommen und auf die Spanne geklemmt statt verworfen - vorher waeren
    solche Woerter hoerbar, aber nicht im Untertitel gewesen.
    """
    words = [
        # Randwort am Anfang, Dauer 1000ms, 500ms (50 %) in der Spanne - an
        # der Schwelle, damit noch aufgenommen (>= statt >).
        build.Word(9500, 10500, "Rand"),
        build.Word(15000, 15300, "Mitte"),
        # Randwort am Ende, Dauer 800ms, 400ms (50 %) in der Spanne.
        build.Word(19600, 20400, "Ende"),
    ]
    selected = build._words_for_span(words, 10000, 20000)
    assert [w.text for w in selected] == ["Rand", "Mitte", "Ende"]

    rand = selected[0]
    assert rand.start_ms == 0
    assert rand.end_ms == 500

    ende = selected[2]
    assert ende.start_ms == 9600
    assert ende.end_ms == 10000


def test_words_for_span_verwirft_randwort_unter_der_schwelle() -> None:
    """Ragt weniger als die Haelfte eines Wortes in die Spanne, bleibt es weg -
    ein zu kurzer Fetzen waere unlesbar.
    """
    words = [
        # Dauer 1000ms, nur 300ms (30 %) in der Spanne.
        build.Word(9300, 10300, "Fetzen"),
        build.Word(15000, 15300, "Mitte"),
        # Dauer 800ms, nur 100ms (12,5 %) in der Spanne.
        build.Word(19900, 20700, "Rest"),
    ]
    selected = build._words_for_span(words, 10000, 20000)
    assert [w.text for w in selected] == ["Mitte"]


def test_words_for_span_klemmt_zeiten_nie_negativ_oder_ueber_cliplaenge() -> None:
    words = [
        build.Word(9000, 10800, "Lang"),
        build.Word(19500, 21500, "Auchlang"),
    ]
    selected = build._words_for_span(words, 10000, 20000)
    assert all(w.start_ms >= 0 for w in selected)
    assert all(w.end_ms <= 10000 for w in selected)


# ---------------------------------------------------------------------------
# run_shorts_build - Ende-zu-Ende mit gefaelschten Stufen, kein echtes ffmpeg.
# ---------------------------------------------------------------------------


def _fake_chart_crop_checks(plan: chart_crop.ChartCropPlan) -> chart_crop.VerifyChecks:
    return chart_crop.VerifyChecks(
        actual_frame_count=plan.expected_frame_count,
        expected_frame_count=plan.expected_frame_count,
        frame_count_ok=True,
        actual_width=chart_crop.OUTPUT_WIDTH,
        actual_height=chart_crop.OUTPUT_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        av_offset_ms=0.0,
        baseline_av_offset_ms=chart_crop.BASELINE_AV_OFFSET_MS,
        av_offset_ok=True,
    )


def test_run_shorts_build_end_to_end_with_faked_stages(tmp_path: Path, monkeypatch) -> None:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job_path = job_dir / "shorts-job.json"
    rendered_path = tmp_path / "rendered.matrix-cut.mp4"
    proposal_path = tmp_path / "cut-proposal.json"
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    (journal_dir / "rec-1.recording-journal.ndjson").write_text("", encoding="utf-8")
    (job_dir / build.AVATAR_CUT_FILE_NAME).write_bytes(b"")

    _write_json(
        job_path,
        {
            "video_name": "test-video",
            "rendered_video": {"path": str(rendered_path)},
            "proposal": {"recording_id": "rec-1", "path": str(proposal_path)},
        },
    )

    kandidaten_path = job_dir / "kandidaten.json"
    _write_json(
        kandidaten_path,
        {
            "kandidaten": [
                {
                    "index": 0,
                    "start_ms": 10000,
                    "end_ms": 20000,
                    "titel": "Gebaut",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
                {
                    "index": 1,
                    "start_ms": 200000,
                    "end_ms": 210000,
                    "titel": "Ausserhalb Charts-Fenster",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
                {
                    "index": 2,
                    "start_ms": 30000,
                    "end_ms": 40000,
                    "titel": "Nicht rastbar",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
                {
                    "index": 3,
                    "start_ms": 50000,
                    "end_ms": 60000,
                    "titel": "ffmpeg schlaegt fehl",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
            ]
        },
    )

    _write_whole_video_transcript(
        job_dir,
        [
            _word_token(9000, 9200, " Vor"),
            _word_token(10000, 10300, " Start"),
            _word_token(15000, 15300, " Mitte"),
            _word_token(19700, 20000, " Ende"),
            _word_token(20800, 21100, " Nach"),
            _word_token(49000, 49200, " Vor3"),
            _word_token(50000, 50300, " Start3"),
            _word_token(55000, 55300, " Mitte3"),
            _word_token(59700, 60000, " Ende3"),
            _word_token(60800, 61100, " Nach3"),
        ],
    )

    # Punkt 1: einmal je Video gemessen.
    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 999999)

    # Punkt 3a: Charts-Fenster deckt Frame 0..5000 - Kandidat 1 (200-210s,
    # Frame 12000-12600) liegt ausserhalb, die anderen liegen drin.
    fake_gate = ApprovalGateResult(
        authorized=True,
        decision="approved",
        reason="ok",
        proposal=_FakeProposal(proposed_cuts=[], source_frame_count=1_000_000),
        approval=None,
    )
    monkeypatch.setattr(build, "inspect_approval_state", lambda path: fake_gate)
    monkeypatch.setattr(
        build, "load_scene_windows", lambda path, **k: (SceneWindow(0, 5000),)
    )

    # chart_crop: Kandidat 3 schlaegt fehl, alle anderen gelingen.
    monkeypatch.setattr(chart_crop, "probe_dimensions", lambda *a, **k: (2560, 1440))

    def fake_run_chart_crop(
        *, input_path, output_path, plan, ffmpeg_path, process_runner=None, timeout_seconds=1800
    ):
        del input_path, output_path, ffmpeg_path, process_runner, timeout_seconds
        if plan.candidate_index == 3:
            return chart_crop.ProcessResult(1, b"ffmpeg kaputt")
        return chart_crop.ProcessResult(0, b"")

    monkeypatch.setattr(chart_crop, "run_chart_crop", fake_run_chart_crop)
    monkeypatch.setattr(
        chart_crop,
        "verify_chart_crop_output",
        lambda output_path, plan, **k: _fake_chart_crop_checks(plan),
    )

    monkeypatch.setattr(
        canvas, "run_stage5a", lambda **k: canvas.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        avatar_canvas, "run_stage5b", lambda **k: avatar_canvas.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        subtitle_burn, "run_stage5c", lambda **k: subtitle_burn.ProcessResult(0, b"")
    )

    result = build.run_shorts_build(
        job_path=job_path,
        kandidaten_path=kandidaten_path,
        output_dir=tmp_path / "out",
        ffmpeg_path=Path("ffmpeg.exe"),
        ffprobe_path=Path("ffprobe.exe"),
        journal_directory=journal_dir,
    )

    assert isinstance(result, build.BuildResult)
    assert result.scene_filter.applied is True
    assert len(result.outcomes) == 4
    by_index = {outcome.index: outcome for outcome in result.outcomes}

    assert by_index[0].status == "gebaut"
    assert by_index[0].output_path is not None
    assert by_index[0].schleifen_einstufung in {"geeignet", "grenzwertig"}

    assert by_index[1].status == "nicht_gebaut"
    assert by_index[1].grund_code == "ausserhalb_charts_fenster"

    assert by_index[2].status == "nicht_gebaut"
    assert by_index[2].grund_code == "schleife_nicht_rastbar"

    assert by_index[3].status == "nicht_gebaut"
    assert by_index[3].grund_code == "chart_crop_ffmpeg_failed"

    assert result.built_count == 1
    assert result.excluded_by_scene_filter_count == 1
    assert result.excluded_by_loop_point_count == 1

    payload = build.build_report_payload(result)
    assert payload["summary"]["built_count"] == 1
    assert len(payload["candidates"]) == 4


# ---------------------------------------------------------------------------
# Auftrag shorts-pegelschnitt: Pegelkorrektur nach dem Rasten, vor dem Bau.
# ---------------------------------------------------------------------------


def _prepare_single_candidate_build(tmp_path: Path, monkeypatch) -> dict[str, object]:
    """Ein minimaler, vollstaendig gefaelschter Baulauf mit genau einem Kandidaten."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job_path = job_dir / "shorts-job.json"
    rendered_path = tmp_path / "rendered.matrix-cut.mp4"
    (job_dir / build.AVATAR_CUT_FILE_NAME).write_bytes(b"")

    _write_json(
        job_path,
        {"video_name": "test-video", "rendered_video": {"path": str(rendered_path)}},
    )
    kandidaten_path = job_dir / "kandidaten.json"
    _write_json(
        kandidaten_path,
        {
            "kandidaten": [
                {
                    "index": 0,
                    "start_ms": 10000,
                    "end_ms": 20000,
                    "titel": "Gebaut",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                }
            ]
        },
    )
    _write_whole_video_transcript(
        job_dir,
        [
            _word_token(9000, 9200, " Vor"),
            _word_token(10000, 10300, " Start"),
            _word_token(15000, 15300, " Mitte"),
            _word_token(19700, 20000, " Ende"),
            _word_token(20800, 21100, " Nach"),
        ],
    )

    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 999999)
    monkeypatch.setattr(chart_crop, "probe_dimensions", lambda *a, **k: (2560, 1440))
    monkeypatch.setattr(
        chart_crop,
        "run_chart_crop",
        lambda **k: chart_crop.ProcessResult(0, b""),
    )
    monkeypatch.setattr(
        chart_crop,
        "verify_chart_crop_output",
        lambda output_path, plan, **k: _fake_chart_crop_checks(plan),
    )
    monkeypatch.setattr(canvas, "run_stage5a", lambda **k: canvas.ProcessResult(0, b""))
    monkeypatch.setattr(
        avatar_canvas, "run_stage5b", lambda **k: avatar_canvas.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        subtitle_burn, "run_stage5c", lambda **k: subtitle_burn.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        build,
        "finde_stillevorlauf",
        lambda media_path, mark_ms, candidate_end_ms, **k: StilleVorlauf(
            mark_ms, mark_ms, 0, False, -30.0, 0
        ),
    )
    return {
        "job_path": job_path,
        "kandidaten_path": kandidaten_path,
        "output_dir": tmp_path / "out",
        "ffmpeg_path": Path("ffmpeg.exe"),
        "ffprobe_path": Path("ffprobe.exe"),
        "journal_directory": tmp_path / "leer",
    }


# ---------------------------------------------------------------------------
# Auftrag shorts-framezahl-cache: rendered_video/avatar-cut-Messungen sind je
# Aufnahme konstant - EINMAL erhoben statt je Kandidat neu.
# ---------------------------------------------------------------------------


def test_run_shorts_build_measures_constant_values_once_across_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    """Zwei Kandidaten, aber ``chart_crop.probe_dimensions``/``probe_frame_count``
    laufen je genau ZWEIMAL insgesamt (einmal fuer rendered_video, einmal fuer
    avatar-cut) - nicht ZWEIMAL JE KANDIDAT wie vor diesem Auftrag."""
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    job_path = job_dir / "shorts-job.json"
    rendered_path = tmp_path / "rendered.matrix-cut.mp4"
    avatar_path = job_dir / build.AVATAR_CUT_FILE_NAME
    avatar_path.write_bytes(b"")

    _write_json(
        job_path,
        {"video_name": "test-video", "rendered_video": {"path": str(rendered_path)}},
    )
    kandidaten_path = job_dir / "kandidaten.json"
    _write_json(
        kandidaten_path,
        {
            "kandidaten": [
                {
                    "index": 0,
                    "start_ms": 10000,
                    "end_ms": 20000,
                    "titel": "Erster",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
                {
                    "index": 1,
                    "start_ms": 50000,
                    "end_ms": 60000,
                    "titel": "Zweiter",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                },
            ]
        },
    )
    _write_whole_video_transcript(
        job_dir,
        [
            _word_token(9000, 9200, " Vor"),
            _word_token(10000, 10300, " Start"),
            _word_token(15000, 15300, " Mitte"),
            _word_token(19700, 20000, " Ende"),
            _word_token(20800, 21100, " Nach"),
            _word_token(49000, 49200, " Vor2"),
            _word_token(50000, 50300, " Start2"),
            _word_token(55000, 55300, " Mitte2"),
            _word_token(59700, 60000, " Ende2"),
            _word_token(60800, 61100, " Nach2"),
        ],
    )

    dimension_calls: list[Path] = []
    frame_count_calls: list[Path] = []

    def fake_probe_dimensions(path, **k):
        del k
        dimension_calls.append(Path(path))
        return (2560, 1440)

    def fake_probe_frame_count(path, **k):
        del k
        frame_count_calls.append(Path(path))
        return 999999

    monkeypatch.setattr(build, "probe_frame_count", fake_probe_frame_count)
    monkeypatch.setattr(chart_crop, "probe_dimensions", fake_probe_dimensions)
    monkeypatch.setattr(
        chart_crop, "run_chart_crop", lambda **k: chart_crop.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        chart_crop,
        "verify_chart_crop_output",
        lambda output_path, plan, **k: _fake_chart_crop_checks(plan),
    )
    monkeypatch.setattr(canvas, "run_stage5a", lambda **k: canvas.ProcessResult(0, b""))

    avatar_calls: list[dict[str, object]] = []

    def fake_run_stage5b(**k):
        avatar_calls.append(k)
        return avatar_canvas.ProcessResult(0, b"")

    monkeypatch.setattr(avatar_canvas, "run_stage5b", fake_run_stage5b)
    monkeypatch.setattr(
        subtitle_burn, "run_stage5c", lambda **k: subtitle_burn.ProcessResult(0, b"")
    )

    result = build.run_shorts_build(
        job_path=job_path,
        kandidaten_path=kandidaten_path,
        output_dir=tmp_path / "out",
        ffmpeg_path=Path("ffmpeg.exe"),
        ffprobe_path=Path("ffprobe.exe"),
        journal_directory=tmp_path / "leer",
    )

    assert isinstance(result, build.BuildResult)
    assert result.built_count == 2

    # Genau zwei probe_dimensions-Aufrufe insgesamt (rendered_video + avatar-cut
    # in derive_inputs) - NICHT vier (zwei Kandidaten x zwei Dateien), wie es
    # vor diesem Auftrag gewesen waere (chart_crop-Zuschnittpruefung je
    # Kandidat, Avatar-Aufloesung je Kandidat in avatar_canvas).
    assert dimension_calls == [rendered_path, avatar_path]
    # Genau zwei probe_frame_count-Aufrufe insgesamt (expected_avatar_frame_count
    # + avatar_frame_count in derive_inputs) - NICHT vier.
    assert frame_count_calls == [rendered_path, avatar_path]

    # Die gemessenen Werte werden an jeden Kandidaten durchgereicht.
    assert len(avatar_calls) == 2
    for call in avatar_calls:
        assert call["avatar_frame_count"] == 999999
        assert call["avatar_source_width"] == 2560
        assert call["avatar_source_height"] == 1440


def test_pegelkorrektur_verschiebt_beide_grenzen_und_wird_berichtet(
    tmp_path: Path, monkeypatch
) -> None:
    """Beide gerasteten Grenzen wandern; die Zahlen stehen je Grenze im Bericht."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    gemessen: list[int] = []

    def fake_snap(media_path, mark_ms, **k):
        del media_path, k
        gemessen.append(mark_ms)
        shift = -120 if mark_ms < 15000 else 80
        return LevelSnap(
            original_ms=mark_ms,
            corrected_ms=mark_ms + shift,
            shift_ms=shift,
            level_db=-64.5,
            window_mean_db=-30.25,
            verfahren="bereichsmitte" if mark_ms < 15000 else "tiefster_punkt",
            quiet_region_ms=140 if mark_ms < 15000 else 0,
        )

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)
    monkeypatch.setattr(build, "finde_wortende_ton", lambda *a, **k: a[1])
    monkeypatch.setattr(build, "finde_worteinsatz_ton", lambda *a, **k: 0)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    outcome = result.outcomes[0]
    assert outcome.status == "gebaut"
    assert outcome.pegelkorrektur is not None
    assert outcome.pegelkorrektur.applied is True

    # Reihenfolge: gemessen wird auf den GERASTETEN Marken, nicht auf den rohen
    # Werten aus kandidaten.json - erst rasten, dann Pegel.
    assert gemessen == [9800, 20200], "loop_point rastet und polstert vor der Messung"
    assert outcome.build_start_ms == 9800 - 120
    assert outcome.build_end_ms == 20200 + 80

    payload = build.build_report_payload(result)
    pegel = payload["candidates"][0]["pegelkorrektur"]
    assert pegel["angewendet"] is True
    assert pegel["start"]["verschiebung_ms"] == -120
    assert pegel["ende"]["verschiebung_ms"] == 80
    assert pegel["start"]["pegel_db"] == -64.5
    assert pegel["start"]["fenstermittel_db"] == -30.25
    assert pegel["start"]["tiefe_unter_mittel_db"] == 34.25
    # Auftrag shorts-pegelmedian: welches Verfahren griff, wie lang der Bereich war.
    assert pegel["start"]["verfahren"] == "bereichsmitte"
    assert pegel["start"]["leiser_bereich_ms"] == 140
    assert pegel["ende"]["verfahren"] == "tiefster_punkt"
    assert pegel["ende"]["leiser_bereich_ms"] == 0


def test_pegelkorrektur_sucht_bei_der_startgrenze_nur_rueckwaerts(
    tmp_path: Path, monkeypatch
) -> None:
    """Auftrag shorts-pegelschnitt-richtung: nur die STARTgrenze sucht rueckwaerts."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    aufrufe: list[tuple[int, bool]] = []

    def fake_snap(media_path, mark_ms, *, nur_rueckwaerts=False, **k):
        del media_path, k
        aufrufe.append((mark_ms, nur_rueckwaerts))
        return LevelSnap(mark_ms, mark_ms, 0, -60.0, -30.0)

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)
    monkeypatch.setattr(build, "finde_wortende_ton", lambda *a, **k: a[1])
    monkeypatch.setattr(build, "finde_worteinsatz_ton", lambda *a, **k: 0)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    assert aufrufe == [(9800, True), (20200, False)]


def test_worteinsatz_gewinnt_wenn_er_spaeter_liegt_als_die_pegelkorrektur(
    tmp_path: Path, monkeypatch
) -> None:
    """Auftrag shorts-pegel-wortgrenze, TEIL 3: der gemessene Worteinsatz ist ein
    ZIEL - liegt er spaeter als das Ergebnis der rueckwaertigen leiseste-Stelle-
    Suche, wird er stattdessen uebernommen (VERFAHREN_WORT_EINSATZ)."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)

    def fake_snap(media_path, mark_ms, **k):
        del media_path, k
        # Die Pegelkorrektur selbst schiebt die Startgrenze weit zurueck.
        shift = -300 if mark_ms < 15000 else 0
        return LevelSnap(mark_ms, mark_ms + shift, shift, -60.0, -30.0)

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)
    monkeypatch.setattr(build, "finde_wortende_ton", lambda *a, **k: a[1])
    # Der gemessene Einsatz liegt 50 ms NACH der (ungepolsterten) Wortmarke,
    # also klar nach dem Ergebnis der Pegelkorrektur (mark_ms - 300).
    monkeypatch.setattr(build, "finde_worteinsatz_ton", lambda *a, **k: a[1] + 50)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    outcome = result.outcomes[0]
    assert outcome.pegelkorrektur is not None
    assert outcome.pegelkorrektur.start is not None
    assert outcome.pegelkorrektur.start.verfahren == "wort_einsatz"
    # finde_worteinsatz_ton wird mit new_start_ms (10000, ungepolstert) aufgerufen -
    # Einsatz also 10000 + 50 = 10050, klar spaeter als 9800 - 300 = 9500.
    assert outcome.build_start_ms == 10000 + 50


def test_pegelkorrektur_wird_auf_dem_gerenderten_video_gemessen(
    tmp_path: Path, monkeypatch
) -> None:
    """Gemessen wird auf ``rendered_video.path`` - dort liegt der endgueltige Ton."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    gemessene_dateien: list[Path] = []

    def fake_snap(media_path, mark_ms, **k):
        del k
        gemessene_dateien.append(media_path)
        return LevelSnap(mark_ms, mark_ms, 0, -60.0, -30.0)

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)
    monkeypatch.setattr(build, "finde_wortende_ton", lambda *a, **k: a[1])
    monkeypatch.setattr(build, "finde_worteinsatz_ton", lambda *a, **k: 0)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    assert gemessene_dateien
    assert all(path == result.derived.rendered_video_path for path in gemessene_dateien)


def test_fehlgeschlagene_pegelmessung_kostet_keinen_kandidaten(
    tmp_path: Path, monkeypatch
) -> None:
    """Messfehler: mit der gerasteten Grenze gebaut und als "ohne Pegelkorrektur" vermerkt."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)

    def fake_snap(media_path, mark_ms, **k):
        del media_path, mark_ms, k
        raise LevelCutFailed("ffmpeg_fehlgeschlagen", "ffmpeg endete mit Code 1")

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    outcome = result.outcomes[0]
    assert outcome.status == "gebaut", "ein Messfehler darf keinen Kandidaten kosten"
    assert outcome.pegelkorrektur is not None
    assert outcome.pegelkorrektur.applied is False
    assert outcome.pegelkorrektur.fail_code == "ffmpeg_fehlgeschlagen"
    # Gebaut wird mit den rein gerasteten Grenzen.
    assert outcome.build_start_ms == 9800
    assert outcome.build_end_ms == 20200

    payload = build.build_report_payload(result)
    assert payload["candidates"][0]["pegelkorrektur"]["angewendet"] is False


# ---------------------------------------------------------------------------
# Auftrag shorts-arbeitskopie: einmal sequentiell auf das Laufwerk des
# Ausgabeordners kopieren statt je Kandidat vom Quelllaufwerk zu springen.
# ---------------------------------------------------------------------------


def test_laufwerksbuchstabe_vergleicht_laufwerk_nicht_pfad(tmp_path: Path) -> None:
    a = tmp_path / "a" / "video.mp4"
    b = tmp_path / "b" / "video.mp4"
    assert build._laufwerksbuchstabe(a) == build._laufwerksbuchstabe(b)


def test_arbeitskopie_deaktiviert_ueber_schalter(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.mp4"
    avatar = tmp_path / "avatar-cut.mp4"
    rendered.write_bytes(b"r")
    avatar.write_bytes(b"a")
    output_dir = tmp_path / "out"

    neuer_rendered, neuer_avatar, info = build._bereite_arbeitskopie_vor(
        output_dir=output_dir,
        rendered_video_path=rendered,
        avatar_cut_path=avatar,
        aktiviert=False,
    )
    assert neuer_rendered == rendered
    assert neuer_avatar == avatar
    assert info.aktiv is False
    assert info.grund_deaktiviert == "--keine-arbeitskopie"
    assert info.arbeitsverzeichnis is None


def test_arbeitskopie_ueberspringt_dateien_auf_demselben_laufwerk(
    tmp_path: Path, monkeypatch
) -> None:
    """Beide Eingaben liegen (laut vorgetaeuschtem Laufwerksvergleich) schon auf
    dem Zielaufwerk - es wird nichts kopiert."""
    rendered = tmp_path / "rendered.mp4"
    avatar = tmp_path / "avatar-cut.mp4"
    rendered.write_bytes(b"r")
    avatar.write_bytes(b"a")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    monkeypatch.setattr(build, "_laufwerksbuchstabe", lambda path: "P:")

    neuer_rendered, neuer_avatar, info = build._bereite_arbeitskopie_vor(
        output_dir=output_dir,
        rendered_video_path=rendered,
        avatar_cut_path=avatar,
        aktiviert=True,
    )
    assert neuer_rendered == rendered
    assert neuer_avatar == avatar
    assert info.aktiv is False
    assert info.grund_deaktiviert == "beide_dateien_bereits_auf_zielaufwerk"
    assert info.arbeitsverzeichnis is None


def test_arbeitskopie_kopiert_nur_dateien_von_anderem_laufwerk(
    tmp_path: Path, monkeypatch
) -> None:
    """rendered_video liegt (vorgetaeuscht) auf F:, avatar-cut schon auf P: -
    nur rendered_video wird kopiert."""
    quelle_dir = tmp_path / "quelle"
    quelle_dir.mkdir()
    rendered = quelle_dir / "rendered.mp4"
    avatar = quelle_dir / "avatar-cut.mp4"
    rendered.write_bytes(b"rendered-inhalt")
    avatar.write_bytes(b"avatar-inhalt")
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    def fake_laufwerk(path: Path) -> str:
        return "F:" if path.resolve() == rendered.resolve() else "P:"

    monkeypatch.setattr(build, "_laufwerksbuchstabe", fake_laufwerk)

    neuer_rendered, neuer_avatar, info = build._bereite_arbeitskopie_vor(
        output_dir=output_dir,
        rendered_video_path=rendered,
        avatar_cut_path=avatar,
        aktiviert=True,
    )
    assert info.aktiv is True
    assert info.kopierte_dateien == ("rendered_video",)
    assert info.uebersprungene_dateien == ("avatar_cut",)
    assert info.fehlgeschlagen is False
    assert info.arbeitsverzeichnis == output_dir / build.ARBEITSKOPIE_DIR_NAME

    assert neuer_rendered == info.arbeitsverzeichnis / "rendered.mp4"
    assert neuer_rendered.read_bytes() == b"rendered-inhalt"
    assert neuer_avatar == avatar, "unveraendert, weil schon auf dem Zielaufwerk"


def test_arbeitskopie_faellt_bei_kopierfehler_auf_originalpfade_zurueck(
    tmp_path: Path, monkeypatch
) -> None:
    """Schlaegt das Kopieren fehl (z. B. Platte voll): NICHT abbrechen, mit den
    Originalpfaden weiterbauen und den Fehler vermerken."""
    rendered = tmp_path / "rendered.mp4"
    avatar = tmp_path / "avatar-cut.mp4"
    rendered.write_bytes(b"r")
    avatar.write_bytes(b"a")
    output_dir = tmp_path / "out"

    monkeypatch.setattr(
        build, "_laufwerksbuchstabe", lambda path: "F:" if path == rendered else "P:"
    )

    def fake_copy2(src, dst):
        raise OSError("kein Platz mehr")

    monkeypatch.setattr(build.shutil, "copy2", fake_copy2)

    neuer_rendered, neuer_avatar, info = build._bereite_arbeitskopie_vor(
        output_dir=output_dir,
        rendered_video_path=rendered,
        avatar_cut_path=avatar,
        aktiviert=True,
    )
    assert neuer_rendered == rendered
    assert neuer_avatar == avatar
    assert info.aktiv is False
    assert info.fehlgeschlagen is True
    assert info.fehler_de is not None
    assert info.arbeitsverzeichnis is None
    assert not (output_dir / build.ARBEITSKOPIE_DIR_NAME).exists(), (
        "ein halb kopiertes Arbeitsverzeichnis darf nicht liegen bleiben"
    )


def test_run_shorts_build_arbeitskopie_wird_benutzt_und_am_ende_geloescht(
    tmp_path: Path, monkeypatch
) -> None:
    """Ende-zu-Ende: die Kandidaten lesen von der Arbeitskopie, die danach weg ist."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    (tmp_path / "rendered.matrix-cut.mp4").write_bytes(b"rendertes-video-inhalt")
    monkeypatch.setattr(
        build,
        "_apply_level_correction",
        lambda *, boundaries, rendered_video_path, ffmpeg_path, search_window_start_ms=None,
        stillevorlauf_aktiv=True: (
            boundaries.start_ms,
            boundaries.end_ms,
            build.LevelCorrectionInfo(False, None, None, None, None),
        ),
    )

    gesehene_rendered_pfade: list[Path] = []
    original_build_one = build._build_one_candidate

    def spy_build_one(**k):
        gesehene_rendered_pfade.append(k["rendered_video_path"])
        return original_build_one(**k)

    monkeypatch.setattr(build, "_build_one_candidate", spy_build_one)
    monkeypatch.setattr(
        build, "_laufwerksbuchstabe", lambda path: "F:" if "rendered" in path.name else "P:"
    )

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    assert result.arbeitskopie.aktiv is True
    assert result.arbeitskopie.kopierte_dateien == ("rendered_video",)
    arbeitsverzeichnis = kwargs["output_dir"] / build.ARBEITSKOPIE_DIR_NAME
    assert not arbeitsverzeichnis.exists(), "Arbeitsverzeichnis muss am Ende geloescht sein"

    assert gesehene_rendered_pfade, "chart_crop haette von der Arbeitskopie lesen sollen"
    assert all(
        p.parent.name == build.ARBEITSKOPIE_DIR_NAME for p in gesehene_rendered_pfade
    )
    assert result.dauer_sekunden >= 0.0


def test_run_shorts_build_arbeitskopie_bleibt_nach_kandidatenfehler_nicht_liegen(
    tmp_path: Path, monkeypatch
) -> None:
    """try/finally: das Arbeitsverzeichnis wird auch geloescht, wenn ein
    Kandidat mitten im Bau abbricht."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    (tmp_path / "rendered.matrix-cut.mp4").write_bytes(b"rendertes-video-inhalt")
    monkeypatch.setattr(
        chart_crop, "run_chart_crop", lambda **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(
        build, "_laufwerksbuchstabe", lambda path: "F:" if "rendered" in path.name else "P:"
    )

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    assert result.outcomes[0].status == "nicht_gebaut"
    arbeitsverzeichnis = kwargs["output_dir"] / build.ARBEITSKOPIE_DIR_NAME
    assert not arbeitsverzeichnis.exists()


def test_run_shorts_build_keine_arbeitskopie_schalter(tmp_path: Path, monkeypatch) -> None:
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    monkeypatch.setattr(
        build, "_laufwerksbuchstabe", lambda path: "F:" if "rendered" in path.name else "P:"
    )

    result = build.run_shorts_build(**kwargs, arbeitskopie_aktiv=False)

    assert isinstance(result, build.BuildResult)
    assert result.arbeitskopie.aktiv is False
    assert result.arbeitskopie.grund_deaktiviert == "--keine-arbeitskopie"
    assert not (kwargs["output_dir"] / build.ARBEITSKOPIE_DIR_NAME).exists()


def test_pegelkorrektur_die_die_spanne_verkuerzt_wird_verworfen(
    tmp_path: Path, monkeypatch
) -> None:
    """Eine Korrektur, die die Spanne unter MIN_SPAN_MS zieht, wird nicht uebernommen."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)

    def fake_snap(media_path, mark_ms, **k):
        del media_path, k
        # Beide Grenzen auf dieselbe Stelle - Spanne 0 ms.
        return LevelSnap(mark_ms, 15000, 15000 - mark_ms, -60.0, -30.0)

    monkeypatch.setattr(build, "verschiebe_auf_leiseste_stelle", fake_snap)
    monkeypatch.setattr(build, "finde_wortende_ton", lambda *a, **k: a[1])
    monkeypatch.setattr(build, "finde_worteinsatz_ton", lambda *a, **k: 0)

    result = build.run_shorts_build(**kwargs)

    assert isinstance(result, build.BuildResult)
    outcome = result.outcomes[0]
    assert outcome.status == "gebaut"
    assert outcome.pegelkorrektur is not None
    assert outcome.pegelkorrektur.applied is False
    assert outcome.pegelkorrektur.fail_code == "pegelkorrektur_verkuerzt_spanne"
    assert outcome.build_start_ms == 9800
    assert outcome.build_end_ms == 20200


# ---------------------------------------------------------------------------
# Auftrag shorts-bau-parallel: Kandidaten nebenlaeufig, Stufen weiterhin
# nacheinander. Ergebnis, Reihenfolge und Aufraeumen duerfen sich nicht aendern.
# ---------------------------------------------------------------------------


def _prepare_multi_candidate_build(
    tmp_path: Path, monkeypatch, indices: list[int]
) -> dict[str, object]:
    """Ein gefaelschter Baulauf mit mehreren Kandidaten - je Kandidat 10 s Spanne.

    ``indices`` bestimmt die REIHENFOLGE in ``kandidaten.json``; jeder Kandidat
    ``i`` deckt ``[10000 + i*20000, 20000 + i*20000)`` ab. Bewusst auch
    unsortiert benutzbar - die Uebersicht muss trotzdem nach Index sortiert sein.
    """
    job_dir = tmp_path / "job"
    job_dir.mkdir(parents=True)
    job_path = job_dir / "shorts-job.json"
    rendered_path = tmp_path / "rendered.matrix-cut.mp4"
    (job_dir / build.AVATAR_CUT_FILE_NAME).write_bytes(b"")

    _write_json(
        job_path,
        {"video_name": "test-video", "rendered_video": {"path": str(rendered_path)}},
    )
    kandidaten_path = job_dir / "kandidaten.json"
    _write_json(
        kandidaten_path,
        {
            "kandidaten": [
                {
                    "index": index,
                    "start_ms": 10000 + index * 20000,
                    "end_ms": 20000 + index * 20000,
                    "titel": f"Kandidat {index}",
                    "begruendung": "x",
                    "sicherheit": "hoch",
                    "enthaelt": [],
                }
                for index in indices
            ]
        },
    )
    tokens: list[dict[str, object]] = []
    for index in sorted(indices):
        versatz = index * 20000
        tokens.extend(
            [
                _word_token(9000 + versatz, 9200 + versatz, " Vor"),
                _word_token(10000 + versatz, 10300 + versatz, " Start"),
                _word_token(15000 + versatz, 15300 + versatz, " Mitte"),
                _word_token(19700 + versatz, 20000 + versatz, " Ende"),
                _word_token(20800 + versatz, 21100 + versatz, " Nach"),
            ]
        )
    _write_whole_video_transcript(job_dir, tokens)

    monkeypatch.setattr(build, "probe_frame_count", lambda *a, **k: 999999)
    monkeypatch.setattr(chart_crop, "probe_dimensions", lambda *a, **k: (2560, 1440))
    monkeypatch.setattr(
        chart_crop, "run_chart_crop", lambda **k: chart_crop.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        chart_crop,
        "verify_chart_crop_output",
        lambda output_path, plan, **k: _fake_chart_crop_checks(plan),
    )
    monkeypatch.setattr(canvas, "run_stage5a", lambda **k: canvas.ProcessResult(0, b""))
    monkeypatch.setattr(
        avatar_canvas, "run_stage5b", lambda **k: avatar_canvas.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        subtitle_burn, "run_stage5c", lambda **k: subtitle_burn.ProcessResult(0, b"")
    )
    monkeypatch.setattr(
        build,
        "finde_stillevorlauf",
        lambda media_path, mark_ms, candidate_end_ms, **k: StilleVorlauf(
            mark_ms, mark_ms, 0, False, -30.0, 0
        ),
    )
    return {
        "job_path": job_path,
        "kandidaten_path": kandidaten_path,
        "output_dir": tmp_path / "out",
        "ffmpeg_path": Path("ffmpeg.exe"),
        "ffprobe_path": Path("ffprobe.exe"),
        "journal_directory": tmp_path / "leer",
    }


def _vergleichbar(result: build.BuildResult) -> list[tuple[object, ...]]:
    return [
        (o.index, o.titel, o.status, o.grund_code, o.build_start_ms, o.build_end_ms)
        for o in result.outcomes
    ]


def test_parallel_ergibt_dasselbe_wie_seriell(tmp_path: Path, monkeypatch) -> None:
    """Nebenlaeufigkeit darf am Ergebnis nichts aendern - nur an der Laufzeit."""
    seriell_kwargs = _prepare_multi_candidate_build(tmp_path / "a", monkeypatch, [0, 1, 2, 3, 4])
    seriell = build.run_shorts_build(**seriell_kwargs, parallel=1)
    parallel_kwargs = _prepare_multi_candidate_build(tmp_path / "b", monkeypatch, [0, 1, 2, 3, 4])
    parallel = build.run_shorts_build(**parallel_kwargs, parallel=4)

    assert isinstance(seriell, build.BuildResult)
    assert isinstance(parallel, build.BuildResult)
    assert seriell.built_count == 5
    assert _vergleichbar(seriell) == _vergleichbar(parallel)
    assert seriell.parallel == 1
    assert parallel.parallel == 4


def test_parallel_uebersicht_ist_nach_index_sortiert_nicht_nach_fertigstellung(
    tmp_path: Path, monkeypatch
) -> None:
    """Auch bei unsortierter kandidaten.json steht die Uebersicht nach Index."""
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [3, 0, 4, 1, 2])

    result = build.run_shorts_build(**kwargs, parallel=4)

    assert isinstance(result, build.BuildResult)
    assert [outcome.index for outcome in result.outcomes] == [0, 1, 2, 3, 4]


def test_parallel_ein_gescheiterter_kandidat_haelt_die_anderen_nicht_auf(
    tmp_path: Path, monkeypatch
) -> None:
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1, 2, 3])

    def fake_run_stage5a(*, input_path, **k):
        del k
        if "kandidat-02" in str(input_path):
            return canvas.ProcessResult(1, b"ffmpeg kaputt")
        return canvas.ProcessResult(0, b"")

    monkeypatch.setattr(canvas, "run_stage5a", fake_run_stage5a)

    result = build.run_shorts_build(**kwargs, parallel=4)

    assert isinstance(result, build.BuildResult)
    by_index = {outcome.index: outcome for outcome in result.outcomes}
    assert by_index[2].status == "nicht_gebaut"
    assert by_index[2].grund_code == "canvas_ffmpeg_failed"
    assert [by_index[i].status for i in (0, 1, 3)] == ["gebaut"] * 3


def test_parallel_baut_kandidaten_wirklich_gleichzeitig(tmp_path: Path, monkeypatch) -> None:
    """Nicht nur ein anderer Code-Weg: es laufen tatsaechlich mehrere zugleich."""
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1, 2, 3])
    sperre = threading.Lock()
    gleichzeitig = 0
    hoechststand = 0

    def fake_run_stage5a(**k):
        nonlocal gleichzeitig, hoechststand
        del k
        with sperre:
            gleichzeitig += 1
            hoechststand = max(hoechststand, gleichzeitig)
        time.sleep(0.05)
        with sperre:
            gleichzeitig -= 1
        return canvas.ProcessResult(0, b"")

    monkeypatch.setattr(canvas, "run_stage5a", fake_run_stage5a)

    result = build.run_shorts_build(**kwargs, parallel=4)

    assert isinstance(result, build.BuildResult)
    assert result.built_count == 4
    assert hoechststand >= 2


def test_parallel_eins_nimmt_keinen_thread(tmp_path: Path, monkeypatch) -> None:
    """--parallel 1 laeuft im Hauptthread - derselbe Weg wie vor diesem Auftrag."""
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1])
    threads: set[str] = set()

    def fake_run_stage5a(**k):
        del k
        threads.add(threading.current_thread().name)
        return canvas.ProcessResult(0, b"")

    monkeypatch.setattr(canvas, "run_stage5a", fake_run_stage5a)

    result = build.run_shorts_build(**kwargs, parallel=1)

    assert isinstance(result, build.BuildResult)
    assert threads == {threading.current_thread().name}


def test_parallel_kleiner_als_eins_wird_abgewiesen(tmp_path: Path, monkeypatch) -> None:
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0])

    result = build.run_shorts_build(**kwargs, parallel=0)

    assert isinstance(result, build.BuildFailed)
    assert result.code == "parallel_ungueltig"


def test_bericht_nennt_die_parallelstufe(tmp_path: Path, monkeypatch) -> None:
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1])

    result = build.run_shorts_build(**kwargs, parallel=2)

    assert isinstance(result, build.BuildResult)
    payload = build.build_report_payload(result)
    assert payload["summary"]["parallel"] == 2


# ---------------------------------------------------------------------------
# Strg+C: laufende ffmpeg-Prozesse beenden, nichts liegen lassen.
# ---------------------------------------------------------------------------


def test_prozesswache_liefert_denselben_ausgang_wie_der_standardweg() -> None:
    wache = build._ProzessWache()
    runner = wache.runner(canvas.ProcessResult)

    ergebnis = runner([sys.executable, "-c", "print('hallo')"], 30)

    assert isinstance(ergebnis, canvas.ProcessResult)
    assert ergebnis.exit_code == 0
    assert b"hallo" in ergebnis.stderr


def test_prozesswache_meldet_fehlstart_statt_zu_werfen() -> None:
    wache = build._ProzessWache()
    runner = wache.runner(chart_crop.ProcessResult)

    ergebnis = runner(["gibt-es-nicht-ffmpeg.exe"], 30)

    assert ergebnis.exit_code == -1
    assert ergebnis.stderr


def test_prozesswache_beendet_laufende_prozesse_und_startet_keine_neuen() -> None:
    """brich_ab toetet den laufenden Prozess - und jeder spaetere startet gar nicht."""
    wache = build._ProzessWache()
    runner = wache.runner(canvas.ProcessResult)
    ergebnisse: list[canvas.ProcessResult] = []

    def _lauf() -> None:
        ergebnisse.append(runner([sys.executable, "-c", "import time; time.sleep(30)"], 300))

    arbeiter = threading.Thread(target=_lauf)
    arbeiter.start()
    for _ in range(200):  # warten, bis der Prozess wirklich laeuft
        if wache._laufende:
            break
        time.sleep(0.01)
    wache.brich_ab()
    arbeiter.join(timeout=30)

    assert not arbeiter.is_alive()
    assert ergebnisse and ergebnisse[0].exit_code != 0
    spaeter = runner([sys.executable, "-c", "print('nie')"], 30)
    assert spaeter.exit_code == -1
    assert b"abgebrochen" in spaeter.stderr


def test_laufnotizen_loeschen_nur_unfertige_neu_angelegte_ordner(tmp_path: Path) -> None:
    notizen = build._Laufnotizen()
    fertig = tmp_path / "kandidat-00"
    unfertig = tmp_path / "kandidat-01"
    fremd = tmp_path / "kandidat-02"
    for ordner in (fertig, unfertig, fremd):
        ordner.mkdir()
        (ordner / "datei.mp4").write_bytes(b"x")
    notizen.verzeichnis_angelegt(0, fertig)
    notizen.verzeichnis_angelegt(1, unfertig)
    notizen.kandidat_fertig(0)

    geloescht = notizen.raeume_unfertige_auf()

    assert geloescht == (unfertig,)
    assert fertig.is_dir()
    assert fremd.is_dir()
    assert not unfertig.exists()


def _keyboard_interrupt_stage(**k: object) -> canvas.ProcessResult:
    raise KeyboardInterrupt


def test_strg_c_raeumt_arbeitskopie_und_angefangene_ordner_auf(
    tmp_path: Path, monkeypatch
) -> None:
    """Nach Strg+C bleibt weder die Arbeitskopie noch ein halber Kandidatenordner."""
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1])
    monkeypatch.setattr(
        build, "_laufwerksbuchstabe", lambda path: "F:" if "rendered" in path.name else "P:"
    )
    rendered = tmp_path / "rendered.matrix-cut.mp4"
    rendered.write_bytes(b"video")
    monkeypatch.setattr(canvas, "run_stage5a", _keyboard_interrupt_stage)

    with pytest.raises(KeyboardInterrupt):
        build.run_shorts_build(**kwargs, parallel=1)

    output_dir = Path(str(kwargs["output_dir"]))
    assert not (output_dir / build.ARBEITSKOPIE_DIR_NAME).exists()
    assert not (output_dir / "kandidat-00").exists()
    assert not (output_dir / "kandidat-01").exists()


def test_strg_c_wirkt_auch_im_parallelen_lauf(tmp_path: Path, monkeypatch) -> None:
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0, 1, 2, 3])
    monkeypatch.setattr(canvas, "run_stage5a", _keyboard_interrupt_stage)

    with pytest.raises(KeyboardInterrupt):
        build.run_shorts_build(**kwargs, parallel=4)

    output_dir = Path(str(kwargs["output_dir"]))
    assert not any(output_dir.glob("kandidat-*"))


def test_abbruchmarke_stoppt_die_stufen_eines_kandidaten(tmp_path: Path, monkeypatch) -> None:
    """Ist abgebrochen gesetzt, laeuft nach chart_crop keine weitere Stufe mehr."""
    kwargs = _prepare_multi_candidate_build(tmp_path, monkeypatch, [0])
    gelaufen: list[str] = []

    def fake_run_chart_crop(**k):
        del k
        gelaufen.append("chart_crop")
        return chart_crop.ProcessResult(0, b"")

    def fake_run_stage5a(**k):
        del k
        gelaufen.append("canvas")
        return canvas.ProcessResult(0, b"")

    monkeypatch.setattr(chart_crop, "run_chart_crop", fake_run_chart_crop)
    monkeypatch.setattr(canvas, "run_stage5a", fake_run_stage5a)

    wache = build._ProzessWache()
    wache.abgebrochen.set()
    ergebnis = build._build_one_candidate(
        candidate=load_candidates(Path(str(kwargs["kandidaten_path"])))[0],
        build_start_ms=10000,
        build_end_ms=20000,
        whole_video_words=[],
        rendered_video_path=tmp_path / "rendered.matrix-cut.mp4",
        avatar_cut_path=tmp_path / "avatar-cut.mp4",
        offsets={},
        derived=build.DerivedInputs(
            canvas_recording_id="v",
            avatar_recording_id="v",
            expected_avatar_frame_count=1,
            rendered_video_path=tmp_path / "rendered.matrix-cut.mp4",
            rendered_video_dimensions=(2560, 1440),
            avatar_frame_count=1,
            avatar_source_width=1920,
            avatar_source_height=1080,
        ),
        kurve=None,
        candidate_dir=tmp_path / "kandidat-00",
        ffmpeg_path=Path("ffmpeg.exe"),
        ffprobe_path=Path("ffprobe.exe"),
        timeout_seconds=60,
        wache=wache,
    )

    assert isinstance(ergebnis, build.BuildFailed)
    assert ergebnis.code == build.ABBRUCH_CODE
    assert gelaufen == ["chart_crop"]


def test_cli_reicht_parallel_durch_und_hat_eine_voreinstellung(
    tmp_path: Path, monkeypatch
) -> None:
    gesehen: dict[str, object] = {}

    def fake_run(**k):
        gesehen.update(k)
        return build.BuildFailed("egal", "egal")

    monkeypatch.setattr(build, "run_shorts_build", fake_run)
    monkeypatch.setattr(build, "discover_ffmpeg", lambda: Path("ffmpeg.exe"))

    assert build.main(["job.json", "kandidaten.json", "--output-dir", str(tmp_path)]) == 1
    assert gesehen["parallel"] == build.PARALLEL_DEFAULT

    assert (
        build.main(
            ["job.json", "kandidaten.json", "--output-dir", str(tmp_path), "--parallel", "7"]
        )
        == 1
    )
    assert gesehen["parallel"] == 7


def test_cli_weist_parallel_kleiner_eins_ab(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(build, "discover_ffmpeg", lambda: Path("ffmpeg.exe"))

    code = build.main(
        ["job.json", "kandidaten.json", "--output-dir", str(tmp_path), "--parallel", "0"]
    )

    assert code == 2


def test_cli_meldet_abbruch_mit_eigenem_rueckgabewert(tmp_path: Path, monkeypatch) -> None:
    def fake_run(**k):
        del k
        raise KeyboardInterrupt

    monkeypatch.setattr(build, "run_shorts_build", fake_run)
    monkeypatch.setattr(build, "discover_ffmpeg", lambda: Path("ffmpeg.exe"))

    code = build.main(["job.json", "kandidaten.json", "--output-dir", str(tmp_path)])

    assert code == 130


# ---------------------------------------------------------------------------
# Auftrag shorts-framezahl-seitendatei: die beiden ffprobe -count_frames-
# Messungen in derive_inputs sind der teuerste Teil des Vorlaufs (568 s von
# 890,5 s Gesamtzeit, gemessen im Auftrag shorts-bau-parallel) - das Ergebnis
# ist eine Eigenschaft der Datei und wird neben ihr vermerkt statt bei jedem
# Lauf neu gemessen.
# ---------------------------------------------------------------------------


def test_framecount_cache_schreibt_und_liest_die_seitendatei(tmp_path: Path) -> None:
    """Erste Messung schreibt die Seitendatei, die zweite liest sie - ohne erneut
    zu messen."""
    video = tmp_path / "avatar-cut.mp4"
    video.write_bytes(b"video-inhalt")
    aufrufe: list[Path] = []

    def fake_probe(path, **k):
        del k
        aufrufe.append(Path(path))
        return 4321

    import matrix_auto_cutter.shorts.build as build_module

    real_probe_frame_count = build_module.probe_frame_count
    build_module.probe_frame_count = fake_probe
    try:
        erster_wert, erste_info = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=True,
            fallback_dir=tmp_path / "ausweich",
        )
        zweiter_wert, zweite_info = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=True,
            fallback_dir=tmp_path / "ausweich",
        )
    finally:
        build_module.probe_frame_count = real_probe_frame_count

    assert erster_wert == 4321
    assert erste_info.cache_treffer is False
    assert erste_info.geschrieben is True
    assert erste_info.schreibfehler_de is None

    assert zweiter_wert == 4321
    assert zweite_info.cache_treffer is True
    assert aufrufe == [video], "die zweite Messung darf ffprobe nicht erneut aufrufen"

    sidecar = video.parent / f"{video.name}{build.FRAMECOUNT_SIDECAR_SUFFIX}"
    assert sidecar.is_file()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["frame_count"] == 4321
    assert payload["schema_version"] == build.FRAMECOUNT_CACHE_SCHEMA_VERSION
    assert payload["source_size_bytes"] == video.stat().st_size


def test_framecount_cache_wird_ungueltig_wenn_sich_die_quelldatei_aendert(
    tmp_path: Path,
) -> None:
    """Kuenstlicher Fall: die Quelldatei wird nach der ersten Messung veraendert
    (andere Groesse, neue Aenderungszeit) - die Seitendatei gilt dann als nicht
    vorhanden, es wird neu gemessen."""
    video = tmp_path / "avatar-cut.mp4"
    video.write_bytes(b"alter-inhalt")

    werte = iter([111, 222])
    aufrufe: list[Path] = []

    def fake_probe(path, **k):
        del k
        aufrufe.append(Path(path))
        return next(werte)

    import matrix_auto_cutter.shorts.build as build_module

    real_probe_frame_count = build_module.probe_frame_count
    build_module.probe_frame_count = fake_probe
    try:
        erster_wert, _ = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=True,
            fallback_dir=tmp_path / "ausweich",
        )

        # Kuenstliche Veraenderung: neuer Inhalt (andere Groesse), neue Zeit.
        time.sleep(0.01)
        video.write_bytes(b"ganz-neuer-laengerer-inhalt")

        zweiter_wert, zweite_info = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=True,
            fallback_dir=tmp_path / "ausweich",
        )
    finally:
        build_module.probe_frame_count = real_probe_frame_count

    assert erster_wert == 111
    assert zweiter_wert == 222
    assert zweite_info.cache_treffer is False
    assert aufrufe == [video, video], "eine veraenderte Quelldatei muss neu gemessen werden"


def test_framecount_cache_schalter_deaktiviert_lesen_und_schreiben(tmp_path: Path) -> None:
    """--kein-framecount-cache: immer neu messen, keine Seitendatei anfassen."""
    video = tmp_path / "avatar-cut.mp4"
    video.write_bytes(b"video-inhalt")
    aufrufe: list[Path] = []

    def fake_probe(path, **k):
        del k
        aufrufe.append(Path(path))
        return 999

    import matrix_auto_cutter.shorts.build as build_module

    real_probe_frame_count = build_module.probe_frame_count
    build_module.probe_frame_count = fake_probe
    try:
        build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=False,
            fallback_dir=tmp_path / "ausweich",
        )
        _, info = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=False,
            fallback_dir=tmp_path / "ausweich",
        )
    finally:
        build_module.probe_frame_count = real_probe_frame_count

    assert aufrufe == [video, video], "abgeschaltet heisst: jedes Mal neu gemessen"
    assert info.aktiv is False
    sidecar = video.parent / f"{video.name}{build.FRAMECOUNT_SIDECAR_SUFFIX}"
    assert not sidecar.exists()


def test_framecount_sidecar_pfad_liegt_neben_der_datei_auf_normalem_laufwerk(
    tmp_path: Path,
) -> None:
    video = tmp_path / "quelle" / "avatar-cut.mp4"
    video.parent.mkdir()
    video.write_bytes(b"x")

    pfad = build._framecount_sidecar_path(video, fallback_dir=tmp_path / "ausweich")

    assert pfad == video.parent / f"{video.name}{build.FRAMECOUNT_SIDECAR_SUFFIX}"


def test_framecount_sidecar_pfad_weicht_auf_gesperrtem_laufwerk_aus(
    tmp_path: Path, monkeypatch
) -> None:
    """Auf einem Laufwerk in READONLY_DRIVES (F:) wird NICHT neben die Quelldatei
    geschrieben, sondern in das Ausweichverzeichnis."""
    video = Path("F:/quelle/avatar-cut.mp4")
    fallback_dir = tmp_path / "ausweich"

    class _FakeResolved:
        drive = "F:"

        def __str__(self) -> str:
            return "F:\\quelle\\avatar-cut.mp4"

    monkeypatch.setattr(Path, "resolve", lambda self: _FakeResolved() if self == video else self)

    pfad = build._framecount_sidecar_path(video, fallback_dir=fallback_dir)

    assert pfad.parent == fallback_dir
    assert pfad.name.endswith(build.FRAMECOUNT_SIDECAR_SUFFIX)
    assert "F" in pfad.name and ":" not in pfad.name


def test_framecount_cache_schreibfehler_stoppt_den_lauf_nicht(
    tmp_path: Path, monkeypatch
) -> None:
    """Schlaegt das Schreiben der Seitendatei fehl: NICHT abbrechen, mit dem
    gemessenen Wert weiterarbeiten, den Fehler vermerken."""
    video = tmp_path / "avatar-cut.mp4"
    video.write_bytes(b"video-inhalt")

    def fake_probe(path, **k):
        del path, k
        return 555

    import matrix_auto_cutter.shorts.build as build_module

    real_probe_frame_count = build_module.probe_frame_count
    build_module.probe_frame_count = fake_probe

    def fake_write(sidecar_path, *, video_path, frame_count):
        del sidecar_path, video_path, frame_count
        return "Platte voll (kuenstlich erzeugt)"

    monkeypatch.setattr(build, "_write_framecount_cache", fake_write)
    try:
        wert, info = build._probe_frame_count_cached(
            video,
            ffprobe_path=Path("ffprobe.exe"),
            timeout_seconds=5,
            cache_aktiv=True,
            fallback_dir=tmp_path / "ausweich",
        )
    finally:
        build_module.probe_frame_count = real_probe_frame_count

    assert wert == 555, "ein Schreibfehler darf den gemessenen Wert nicht kosten"
    assert info.geschrieben is False
    assert info.schreibfehler_de == "Platte voll (kuenstlich erzeugt)"


def test_framecount_cache_schreibt_atomar_ueber_temporaere_datei(tmp_path: Path) -> None:
    video = tmp_path / "avatar-cut.mp4"
    video.write_bytes(b"video-inhalt")
    sidecar = tmp_path / f"{video.name}{build.FRAMECOUNT_SIDECAR_SUFFIX}"

    fehler = build._write_framecount_cache(sidecar, video_path=video, frame_count=777)

    assert fehler is None
    assert sidecar.is_file()
    # Keine liegen gebliebene temporaere Datei.
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_run_shorts_build_reicht_framecount_cache_aktiv_an_derive_inputs_durch(
    tmp_path: Path, monkeypatch
) -> None:
    """Ende-zu-Ende: der Bericht zeigt Cache-Treffer im zweiten Lauf ueber dieselbe
    (unveraenderte) Quelldatei."""
    kwargs = _prepare_single_candidate_build(tmp_path, monkeypatch)
    rendered = tmp_path / "rendered.matrix-cut.mp4"
    rendered.write_bytes(b"rendertes-video-inhalt")
    avatar = tmp_path / "job" / build.AVATAR_CUT_FILE_NAME
    avatar.write_bytes(b"avatar-inhalt")

    # Die echte (gefaelschte) Messfunktion statt des in _prepare_single_candidate_build
    # gesetzten Immer-999999-Stubs - hier zaehlen wir Aufrufe.
    aufrufe: list[Path] = []

    def fake_probe(path, **k):
        del k
        aufrufe.append(Path(path))
        return 999999

    monkeypatch.setattr(build, "probe_frame_count", fake_probe)

    erster = build.run_shorts_build(**kwargs)
    assert isinstance(erster, build.BuildResult)
    aufrufe_nach_erstem_lauf = len(aufrufe)
    assert aufrufe_nach_erstem_lauf == 2  # rendered + avatar, je einmal gemessen

    zweiter = build.run_shorts_build(**kwargs)
    assert isinstance(zweiter, build.BuildResult)

    assert len(aufrufe) == aufrufe_nach_erstem_lauf, (
        "der zweite Lauf ueber dieselben, unveraenderten Dateien darf ffprobe nicht "
        "erneut fuer die Framezahl aufrufen"
    )
    assert zweiter.derived.rendered_video_framecount_cache.cache_treffer is True
    assert zweiter.derived.avatar_framecount_cache.cache_treffer is True

    payload = build.build_report_payload(zweiter)
    assert payload["derived_inputs"]["rendered_video_framecount_cache"]["cache_treffer"] is True
    assert payload["derived_inputs"]["avatar_framecount_cache"]["cache_treffer"] is True


# --- Auftrag shorts-3b-verdrahtung: Mausverfolgung, einmal je Lauf ----------------

_CURSOR_KOPF = "zeit,x,y\n"


def _cursor_csv(pfad: Path, zeilen: int = 200) -> Path:
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 8, 19, 17, 26, 15, tzinfo=timezone(timedelta(hours=2)))
    text = _CURSOR_KOPF + "".join(
        f"{(t0 + timedelta(milliseconds=100 * i)).isoformat()},1300,700\n" for i in range(zeilen)
    )
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(text, encoding="utf-8")
    return pfad


_SEGMENTE = (build.KeepSegment(0, 60_000),)


def test_mausverfolgung_abgeschaltet_nennt_den_grund(tmp_path: Path) -> None:
    ergebnis = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(_cursor_csv(tmp_path / "c.csv"))}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=False,
    )
    assert ergebnis.aktiv is False
    assert ergebnis.grund == build.MAUSVERFOLGUNG_ABGESCHALTET


def test_mausverfolgung_ohne_eintrag_im_auftrag_faellt_zurueck() -> None:
    ergebnis = build._lade_mausverfolgung(
        {}, segmente=_SEGMENTE, rendered_windows=None, aktiviert=True
    )
    assert ergebnis.aktiv is False
    assert ergebnis.grund == build.MAUSVERFOLGUNG_KEIN_EINTRAG


def test_mausverfolgung_bei_fehlender_datei_faellt_zurueck(tmp_path: Path) -> None:
    ergebnis = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(tmp_path / "fehlt.csv")}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=True,
    )
    assert ergebnis.aktiv is False
    assert ergebnis.grund == build.MAUSVERFOLGUNG_DATEI_FEHLT


def test_mausverfolgung_bei_kaputtem_protokoll_faellt_zurueck_statt_abzubrechen(
    tmp_path: Path,
) -> None:
    kaputt = tmp_path / "c.csv"
    kaputt.write_text("zeit,x,y\nnicht-iso,1,2\n", encoding="utf-8")
    ergebnis = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(kaputt)}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=True,
    )
    assert ergebnis.aktiv is False
    assert ergebnis.grund == build.MAUSVERFOLGUNG_UNLESBAR


def test_mausverfolgung_ohne_keep_segmente_faellt_zurueck(tmp_path: Path) -> None:
    ergebnis = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(_cursor_csv(tmp_path / "c.csv"))}},
        segmente=(),
        rendered_windows=None,
        aktiviert=True,
    )
    assert ergebnis.aktiv is False
    assert ergebnis.grund == build.MAUSVERFOLGUNG_KEINE_SEGMENTE


def test_anker_ist_die_erste_protokollzeile_also_csv_first_row_at(tmp_path: Path) -> None:
    """shorts-job.json traegt csv_first_row_at nicht - die erste Zeile ist er."""
    csv = _cursor_csv(tmp_path / "c.csv")
    ergebnis = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(csv)}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=True,
    )
    assert ergebnis.aktiv is True
    erste_zeile = csv.read_text(encoding="utf-8").splitlines()[1].split(",")[0]
    assert ergebnis.anker is not None
    assert ergebnis.anker.isoformat() == erste_zeile


def test_versatzkurve_wird_gerechnet_und_im_bericht_benannt(tmp_path: Path) -> None:
    mausverfolgung = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(_cursor_csv(tmp_path / "c.csv"))}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=True,
    )
    kurve, info = build._berechne_versatzkurve(
        mausverfolgung,
        kandidat_index=0,
        build_start_ms=1000,
        build_end_ms=6000,
        offsets={},
    )
    # Ruhiger Cursor in der Trittzone: gerechnet, aber ohne Fahrt und deshalb
    # eine konstante Kurve - die geht als Kurve durch, nicht als Rueckfall.
    assert info.grund == "berechnet"
    assert info.fahrten == 0
    assert info.versatz_anfang == info.versatz_ende == 422
    assert kurve is not None and set(kurve) == {422}


def test_ausschnitt_json_verhindert_dass_ueberhaupt_gerechnet_wird(tmp_path: Path) -> None:
    mausverfolgung = build._lade_mausverfolgung(
        {"cursor_log": {"path": str(_cursor_csv(tmp_path / "c.csv"))}},
        segmente=_SEGMENTE,
        rendered_windows=None,
        aktiviert=True,
    )
    kurve, info = build._berechne_versatzkurve(
        mausverfolgung,
        kandidat_index=7,
        build_start_ms=1000,
        build_end_ms=6000,
        offsets={7: 600},
    )
    assert kurve is None
    assert info.grund == build.MAUSVERFOLGUNG_AUSSCHNITT_VORRANG
    assert info.versatz_anfang == info.versatz_ende == 600


def test_rueckfall_liefert_keine_kurve_sondern_den_festen_versatz_416(tmp_path: Path) -> None:
    """Kein Protokoll -> fester Versatz 416, Grund benannt, KEIN Abbruch."""
    mausverfolgung = build._lade_mausverfolgung(
        {}, segmente=_SEGMENTE, rendered_windows=None, aktiviert=True
    )
    kurve, info = build._berechne_versatzkurve(
        mausverfolgung,
        kandidat_index=0,
        build_start_ms=1000,
        build_end_ms=6000,
        offsets={},
    )
    assert kurve is None
    assert info.grund == build.MAUSVERFOLGUNG_KEIN_EINTRAG
    assert info.versatz_anfang == info.versatz_ende == 416


def test_mausverfolgung_steht_je_kandidat_im_baubericht() -> None:
    nutzlast = build._mausverfolgung_payload(
        build.KurvenInfo("berechnet", 3, 482, 700, naehte=1, eingefrorene_frames=12)
    )
    assert nutzlast == {
        "grund": "berechnet",
        "fahrten": 3,
        "versatz_anfang": 482,
        "versatz_ende": 700,
        "naehte": 1,
        "eingefrorene_frames": 12,
    }
    assert build._mausverfolgung_payload(None) is None
