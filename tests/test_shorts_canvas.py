"""Tests für Shorts-Stufe 5a: Leinwand und Chartpanel.

Reine Rechnung (Geometrie, Kommandozeile, Pruefergebnisse) ohne echtes Video.
Die Orchestrierung wird wie in ``test_shorts_chart_crop.py`` mit einem
gefälschten ffmpeg-Prozess getestet - kein echtes ffmpeg läuft in diesen
Tests. Der echte ffmpeg-Lauf gegen die realen Pruefsteine
(``kandidat-1-kuerzester-frames.mp4``, ``kandidat-18-laengster-frames.mp4``)
steht im Bericht ``artefakte\\repeat\\shorts-stufe-5a\\BERICHT-2026-08-17.md``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from matrix_auto_cutter.shorts import canvas

# --- Geometrie: benannte Konstanten an einer Stelle ---------------------------------


def test_geometry_constants_match_the_task() -> None:
    assert canvas.CANVAS_WIDTH == 1080
    assert canvas.CANVAS_HEIGHT == 1920
    assert canvas.PANEL_WIDTH == 1080
    assert canvas.PANEL_HEIGHT == 900
    assert canvas.PANEL_X == 0
    assert canvas.PANEL_Y == 200
    # Auftrag shorts-hintergrund-schwarz: gemessen aus avatar-cut.mp4, nicht mehr
    # das --ink-Designsystem-Token vom 9.8.
    assert canvas.BACKGROUND_COLOR_RGB == (0, 0, 0)
    assert canvas.BACKGROUND_COLOR_HEX == "000000"
    assert canvas.CANVAS_FPS == 60


def test_safe_zone_constants_match_the_task() -> None:
    assert canvas.SAFE_TOP == 200
    assert canvas.SAFE_BOTTOM == 480
    assert canvas.SAFE_RIGHT == 150


# --- Kommandozeile: pad statt overlay, Ton unveraendert ------------------------------


def test_build_ffmpeg_filter_complex_pads_panel_onto_canvas() -> None:
    filter_complex, video_label = canvas.build_ffmpeg_filter_complex()
    assert video_label == "[v0]"
    assert filter_complex == "[0:v]pad=1080:1920:0:200:0x000000[v0]"


def test_build_ffmpeg_arguments_uses_expected_shape() -> None:
    arguments = canvas.build_ffmpeg_arguments(
        Path("ffmpeg.exe"), Path("in.mp4"), Path("out.mp4")
    )
    assert arguments[0] == "ffmpeg.exe"
    # Kein Neuschnitt: keine trim/atrim-Frame-Angaben noetig, kein -ss/-t.
    assert "-ss" not in arguments
    assert "-t" not in arguments
    assert "-filter_complex" in arguments
    filter_complex = arguments[arguments.index("-filter_complex") + 1]
    assert "pad=1080:1920:0:200:0x000000" in filter_complex
    assert arguments.count("-map") == 2
    assert "[v0]" in arguments
    assert "0:a" in arguments
    # Ausgabe-Framerate ausdruecklich gesetzt.
    assert "-r" in arguments
    assert arguments[arguments.index("-r") + 1] == "60"
    # Ton wird unveraendert uebernommen - Streamkopie, keine Neukodierung.
    assert "-c:a" in arguments
    assert arguments[arguments.index("-c:a") + 1] == "copy"
    assert str(Path("out.mp4")) == arguments[-1]


# --- Orchestrierung: gefaelschter ffmpeg-Prozess, kein echtes Video -----------------


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_process_runner(exit_code: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def runner(arguments, timeout):  # type: ignore[no-untyped-def]
        del timeout
        calls.append(list(arguments))
        return canvas.ProcessResult(exit_code, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_canvas_creates_output_directory_and_calls_ffmpeg(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "out.mp4"
    runner = _fake_process_runner()
    result = canvas.run_canvas(
        input_path=tmp_path / "in.mp4",
        output_path=output,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
    )
    assert result.exit_code == 0
    assert output.parent.is_dir()
    assert runner.calls  # type: ignore[attr-defined]


def test_run_stage5a_rejects_input_resolution_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(canvas, "probe_dimensions", lambda *a, **k: (1920, 1080))
    result = canvas.run_stage5a(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, canvas.Stage5aFailed)
    assert result.code == "input_resolution_mismatch"


def test_run_stage5a_rejects_unknown_input_resolution(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(canvas, "probe_dimensions", lambda *a, **k: None)
    result = canvas.run_stage5a(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, canvas.Stage5aFailed)
    assert result.code == "input_resolution_unknown"


def test_run_stage5a_propagates_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(canvas, "probe_dimensions", lambda *a, **k: (1080, 900))
    result = canvas.run_stage5a(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(exit_code=1, stderr=b"boom"),
    )
    assert isinstance(result, canvas.ProcessResult)
    assert result.exit_code == 1


def _ok_checks(frame_count: int) -> canvas.VerifyChecks:
    return canvas.VerifyChecks(
        input_frame_count=frame_count,
        output_frame_count=frame_count,
        frame_count_ok=True,
        actual_width=canvas.CANVAS_WIDTH,
        actual_height=canvas.CANVAS_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        video_start_time=0.0,
        audio_start_time=0.0,
        start_time_ok=True,
    )


def test_run_stage5a_happy_path_writes_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(canvas, "probe_dimensions", lambda *a, **k: (1080, 900))
    monkeypatch.setattr(canvas, "verify_canvas_output", lambda *a, **k: _ok_checks(860))
    runner = _fake_process_runner()
    result = canvas.run_stage5a(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
    )
    assert isinstance(result, canvas.ProcessResult)
    assert result.exit_code == 0
    [call] = runner.calls  # type: ignore[attr-defined]
    assert "pad=1080:1920:0:200:0x000000" in call[call.index("-filter_complex") + 1]
    report_path = tmp_path / "out.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["checks"]["frame_count"]["input"] == 860
    assert report["checks"]["frame_count"]["output"] == 860


def test_run_stage5a_reports_specific_failure_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(canvas, "probe_dimensions", lambda *a, **k: (1080, 900))
    failing_checks = canvas.VerifyChecks(
        input_frame_count=860,
        output_frame_count=859,
        frame_count_ok=False,
        actual_width=canvas.CANVAS_WIDTH,
        actual_height=canvas.CANVAS_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        video_start_time=0.0,
        audio_start_time=0.0,
        start_time_ok=True,
    )
    monkeypatch.setattr(canvas, "verify_canvas_output", lambda *a, **k: failing_checks)
    result = canvas.run_stage5a(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, canvas.Stage5aFailed)
    assert result.code == "frame_count_mismatch"
    report_path = tmp_path / "out.json"
    assert report_path.is_file()


# --- VerifyChecks: vier unabhaengige Pruefungen, kein Sammelcode --------------------


def test_verify_checks_first_failure_code_orders_checks() -> None:
    ok = _ok_checks(100)
    assert ok.first_failure_code is None
    assert ok.all_ok is True

    bad_frames = dataclasses.replace(ok, frame_count_ok=False)
    assert bad_frames.first_failure_code == "frame_count_mismatch"

    bad_dimensions = dataclasses.replace(ok, dimensions_ok=False)
    assert bad_dimensions.first_failure_code == "dimension_mismatch"

    bad_audio = dataclasses.replace(ok, audio_track_count_ok=False)
    assert bad_audio.first_failure_code == "audio_track_count_invalid"

    bad_start_time = dataclasses.replace(ok, start_time_ok=False)
    assert bad_start_time.first_failure_code == "start_time_nonzero"


def test_write_canvas_report_is_atomic_and_readable(tmp_path: Path) -> None:
    checks = _ok_checks(4476)
    payload = canvas.canvas_report_payload(checks)
    report_path = tmp_path / "nested" / "out.json"
    canvas.write_canvas_report(report_path, payload)
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == payload
