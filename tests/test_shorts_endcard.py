"""Tests für Shorts-Stufe 5d: Endcard erzeugen und anhaengen.

Reine Rechnung (Geometrie, Kommandozeile, Pruefergebnisse) ohne echtes Video.
Die Orchestrierung wird wie in ``test_shorts_canvas.py`` mit einem
gefälschten ffmpeg-Prozess getestet - kein echtes ffmpeg läuft in diesen
Tests. Der echte ffmpeg-Lauf gegen den realen Pruefstein
(``kandidat-1-kuerzester-leinwand.mp4``) steht im Bericht
``artefakte\\repeat\\shorts-stufe-5d\\BERICHT-2026-08-17.md``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from matrix_auto_cutter.shorts import endcard

# --- Geometrie: benannte Konstanten aus dem Auftrag, canvas.py nicht neu erfunden ---


def test_colors_match_the_task() -> None:
    # INK_HEX = canvas.BACKGROUND_COLOR_HEX (importiert, nicht dupliziert) - seit
    # Auftrag shorts-hintergrund-schwarz "000000" statt "171614". endcard.py selbst
    # ist in diesem Auftrag unveraendert und nicht Teil der Shorts-Kette.
    assert endcard.INK_HEX == "000000"
    assert endcard.BRASS_HEX == "a98246"
    assert endcard.BONE_HEX == "ece8e0"


def test_duration_and_transition_constants_match_the_task() -> None:
    assert endcard.ENDCARD_DURATION_FRAMES == 180
    assert endcard.ENDCARD_DURATION_SECONDS == 3.0
    assert endcard.TRANSITION_MS == 600
    assert endcard.TRANSITION_FRAMES == 36
    assert endcard.TRANSITION_SECONDS == 0.6


def test_monogram_geometry() -> None:
    assert endcard.MONOGRAM_SOURCE_WIDTH == 400
    assert endcard.MONOGRAM_SOURCE_HEIGHT == 400
    assert endcard.MONOGRAM_WIDTH == 240
    assert endcard.MONOGRAM_HEIGHT == 240
    assert endcard.MONOGRAM_X == 420
    assert endcard.MONOGRAM_Y == 260
    # Im oberen Drittel des nutzbaren Feldes (200..1440, Drittel bis 613).
    assert endcard.MONOGRAM_Y + endcard.MONOGRAM_HEIGHT < 613


def test_hairline_geometry() -> None:
    assert endcard.HAIRLINE_WIDTH == 600
    assert endcard.HAIRLINE_HEIGHT == 1
    assert endcard.HAIRLINE_X == 240
    assert endcard.HAIRLINE_Y == 580


def test_text_uses_en_dash_not_hyphen() -> None:
    assert "\u2013" in endcard.ENDCARD_TEXT
    assert "-" not in endcard.ENDCARD_TEXT
    assert "\u2014" not in endcard.ENDCARD_TEXT  # kein Geviertstrich
    assert endcard.ENDCARD_TEXT == "Donnerstag 20:00 \u2013 Inner Circle"


def test_button_geometry_and_single_cta() -> None:
    assert endcard.BUTTON_WIDTH == 420
    assert endcard.BUTTON_HEIGHT == 90
    assert endcard.BUTTON_X == 330
    assert endcard.BUTTON_Y == 760
    assert endcard.BUTTON_BORDER_THICKNESS == 1
    # Innerhalb der unteren Sicherheitszone (Ende <= 1920-480=1440).
    assert endcard.BUTTON_Y + endcard.BUTTON_HEIGHT <= 1440


def test_expected_output_frame_count_formula() -> None:
    # Eingabeframes + 180 minus die Ueberblendlaenge (36), keine Toleranz.
    assert endcard.expected_output_frame_count(860) == 860 + 180 - 36
    assert endcard.expected_output_frame_count(4476) == 4476 + 144


# --- Kommandozeile: Filterausdruck, Escaping, xfade/acrossfade ---------------------


def test_ffmpeg_escape_path_escapes_drive_colon_and_backslashes() -> None:
    escaped = endcard._ffmpeg_escape_path(Path(r"C:\Users\me\font.ttf"))
    assert escaped == "C\\:/Users/me/font.ttf"


def test_build_endcard_filter_complex_contains_blend_lighten_with_rgb_format() -> None:
    filter_complex = endcard.build_endcard_filter_complex(
        font_path=Path("font.ttf"),
        text_file_path=Path("text.txt"),
        button_text_file_path=Path("button.txt"),
    )
    assert "blend=all_mode=lighten" in filter_complex
    # format=rgb24 ist notwendig, sonst blendet blend in YUV statt je R/G/B.
    assert filter_complex.count("format=rgb24") == 2
    assert f"color=c=0x{endcard.INK_HEX}" in filter_complex
    assert f"drawbox=x={endcard.HAIRLINE_X}:y={endcard.HAIRLINE_Y}" in filter_complex
    assert f"drawbox=x={endcard.BUTTON_X}:y={endcard.BUTTON_Y}" in filter_complex
    # Genau ein Knopf: genau zwei drawbox-Aufrufe (Haarlinie + Knopfrand).
    assert filter_complex.count("drawbox=") == 2


def test_build_append_filter_complex_uses_expected_offset_and_transition() -> None:
    filter_complex, video_label, audio_label = endcard.build_append_filter_complex(
        font_path=Path("font.ttf"),
        text_file_path=Path("text.txt"),
        button_text_file_path=Path("button.txt"),
        input_frame_count=860,
    )
    assert video_label == "[outv]"
    assert audio_label == "[outa]"
    assert f"transition={endcard.XFADE_TRANSITION}" in filter_complex
    assert "duration=0.6" in filter_complex
    expected_offset = 860 / 60 - 0.6
    assert f"offset={expected_offset:.9f}" in filter_complex
    assert "acrossfade=d=0.6" in filter_complex
    assert "anullsrc=r=48000:cl=stereo" in filter_complex


def test_build_ffmpeg_arguments_uses_expected_shape() -> None:
    arguments = endcard.build_ffmpeg_arguments(
        Path("ffmpeg.exe"),
        Path("in.mp4"),
        Path("logo.png"),
        Path("out.mp4"),
        font_path=Path("font.ttf"),
        text_file_path=Path("text.txt"),
        button_text_file_path=Path("button.txt"),
        input_frame_count=860,
    )
    assert arguments[0] == "ffmpeg.exe"
    assert "-loop" in arguments
    assert arguments[arguments.index("-loop") + 1] == "1"
    assert "-filter_complex" in arguments
    assert arguments.count("-map") == 2
    assert "[outv]" in arguments
    assert "[outa]" in arguments
    assert "-r" in arguments
    assert arguments[arguments.index("-r") + 1] == "60"
    assert "-c:a" in arguments
    assert arguments[arguments.index("-c:a") + 1] == "aac"
    assert str(Path("out.mp4")) == arguments[-1]


# --- Orchestrierung: gefaelschter ffmpeg-Prozess, kein echtes Video -----------------


def _touch(path: Path, content: bytes = b"\x00") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _fake_process_runner(exit_code: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def runner(arguments, timeout):  # type: ignore[no-untyped-def]
        del timeout
        calls.append(list(arguments))
        return endcard.ProcessResult(exit_code, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_endcard_reports_missing_font(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: (400, 400))
    monkeypatch.setattr(endcard, "discover_jetbrains_mono_font", lambda: None)
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    result = endcard.run_endcard(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        monogram_path=tmp_path / "logo.png",
        font_path=None,
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "font_not_found"


def test_run_endcard_rejects_monogram_resolution_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: (100, 100))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    result = endcard.run_endcard(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        monogram_path=tmp_path / "logo.png",
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "monogram_resolution_mismatch"


def test_run_endcard_rejects_unknown_input_frame_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: (400, 400))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: None)
    result = endcard.run_endcard(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        monogram_path=tmp_path / "logo.png",
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "input_frame_count_unknown"


def test_run_endcard_calls_ffmpeg_and_creates_output_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: (400, 400))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    output = tmp_path / "nested" / "out.mp4"
    runner = _fake_process_runner()
    result = endcard.run_endcard(
        input_path=tmp_path / "in.mp4",
        output_path=output,
        ffmpeg_path=Path("ffmpeg.exe"),
        monogram_path=tmp_path / "logo.png",
        font_path=tmp_path / "font.ttf",
        process_runner=runner,
    )
    assert isinstance(result, endcard.ProcessResult)
    assert result.exit_code == 0
    assert output.parent.is_dir()
    assert runner.calls  # type: ignore[attr-defined]


def test_run_stage5d_rejects_input_resolution_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: (1920, 1080))
    result = endcard.run_stage5d(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "input_resolution_mismatch"


def test_run_stage5d_rejects_unknown_input_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", lambda *a, **k: None)
    result = endcard.run_stage5d(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "input_resolution_unknown"


def _dims_for(input_path: Path) -> object:
    """Unterscheide per Pfad: die Eingabe ist 1080x1920, die Bildmarke 400x400."""

    def probe(path, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return (400, 400) if path == endcard.MONOGRAM_SOURCE_PATH else (1080, 1920)

    return probe


def test_run_stage5d_propagates_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", _dims_for(tmp_path))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    result = endcard.run_stage5d(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(exit_code=1, stderr=b"boom"),
    )
    assert isinstance(result, endcard.ProcessResult)
    assert result.exit_code == 1


def _ok_checks(input_frames: int) -> endcard.VerifyChecks:
    expected = endcard.expected_output_frame_count(input_frames)
    return endcard.VerifyChecks(
        input_frame_count=input_frames,
        output_frame_count=expected,
        expected_frame_count=expected,
        frame_count_ok=True,
        actual_width=endcard.CANVAS_WIDTH,
        actual_height=endcard.CANVAS_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        video_start_time=0.0,
        audio_start_time=0.0,
        start_time_ok=True,
    )


def test_run_stage5d_happy_path_writes_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", _dims_for(tmp_path))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    monkeypatch.setattr(endcard, "verify_endcard_output", lambda *a, **k: _ok_checks(860))
    runner = _fake_process_runner()
    result = endcard.run_stage5d(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=runner,
    )
    assert isinstance(result, endcard.ProcessResult)
    assert result.exit_code == 0
    report_path = tmp_path / "out.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["checks"]["frame_count"]["input"] == 860
    assert report["checks"]["frame_count"]["expected"] == 1004


def test_run_stage5d_reports_specific_failure_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(endcard, "probe_dimensions", _dims_for(tmp_path))
    monkeypatch.setattr(endcard, "probe_frame_count", lambda *a, **k: 860)
    failing_checks = dataclasses.replace(_ok_checks(860), frame_count_ok=False)
    monkeypatch.setattr(endcard, "verify_endcard_output", lambda *a, **k: failing_checks)
    result = endcard.run_stage5d(
        input_path=tmp_path / "in.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, endcard.Stage5dFailed)
    assert result.code == "frame_count_mismatch"
    assert (tmp_path / "out.json").is_file()


# --- VerifyChecks: vier unabhaengige Pruefungen, kein Sammelcode --------------------


def test_verify_checks_first_failure_code_orders_checks() -> None:
    ok = _ok_checks(860)
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


def test_write_endcard_report_is_atomic_and_readable(tmp_path: Path) -> None:
    checks = _ok_checks(4476)
    payload = endcard.endcard_report_payload(checks)
    report_path = tmp_path / "nested" / "out.json"
    endcard.write_endcard_report(report_path, payload)
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == payload


# --- Schriftdatei finden -------------------------------------------------------------


def test_discover_jetbrains_mono_font_finds_user_scope_font(tmp_path: Path, monkeypatch) -> None:
    fonts_dir = tmp_path / "Fonts"
    fonts_dir.mkdir()
    font_file = fonts_dir / "JetBrainsMono-Regular.ttf"
    font_file.write_bytes(b"\x00")
    monkeypatch.setattr(endcard, "_FONT_CANDIDATE_DIRS", (None, fonts_dir))
    assert endcard.discover_jetbrains_mono_font() == font_file


def test_discover_jetbrains_mono_font_returns_none_when_missing(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(endcard, "_FONT_CANDIDATE_DIRS", (tmp_path / "nope", None))
    assert endcard.discover_jetbrains_mono_font() is None
