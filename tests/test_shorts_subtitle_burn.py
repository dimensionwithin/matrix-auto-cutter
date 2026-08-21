"""Tests fuer Shorts-Stufe 5c: Untertitel einbrennen.

Reine Rechnung (Geometrie, Filtergraph, Kommandozeile, Pruefergebnisse) ohne
echtes Video/ffmpeg - dasselbe Muster wie ``test_shorts_canvas.py``/
``test_shorts_avatar_canvas.py``. Der echte ffmpeg-Lauf gegen den realen
Pruefstein (``kandidat-18-avatar.mp4`` + ``probe-turbo.wav.json``) steht im
Bericht ``artefakte\\repeat\\shorts-stufe-5c\\BERICHT-2026-08-17.md``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import subtitle_burn
from matrix_auto_cutter.shorts.subtitle_lines import Word, build_subtitle_lines

# --- Geometrie: benannte Konstanten an einer Stelle, harte Bedingungen -------------


def test_colors_match_the_designsystem() -> None:
    assert subtitle_burn.BONE_DIM_HEX == "bfb9ac"
    assert subtitle_burn.BRASS_HEX == "a98246"


def test_char_advance_width_matches_measured_ratio() -> None:
    assert subtitle_burn.CHAR_ADVANCE_WIDTH_PX == 30.0
    assert subtitle_burn.CHAR_ADVANCE_WIDTH_PX == 0.6 * subtitle_burn.SUBTITLE_FONT_SIZE


def test_subtitle_field_matches_avatar_safe_zone() -> None:
    assert subtitle_burn.SUBTITLE_FIELD_X == 0
    assert subtitle_burn.SUBTITLE_FIELD_WIDTH == 930


def test_longest_possible_line_fits_the_field() -> None:
    assert subtitle_burn.SUBTITLE_MAX_LINE_WIDTH_PX <= subtitle_burn.SUBTITLE_FIELD_WIDTH


def test_subtitle_y_is_derived_from_canvas_panel_edge() -> None:
    assert subtitle_burn.SUBTITLE_TOP_Y == 1100
    assert subtitle_burn.SUBTITLE_TEXT_Y == 1124


def test_subtitle_stays_above_the_youtube_control_bar() -> None:
    assert (
        subtitle_burn.SUBTITLE_TEXT_Y + subtitle_burn.SUBTITLE_TEXT_HEIGHT_PX
        <= subtitle_burn.SUBTITLE_BOTTOM_LIMIT_Y
    )


# --- Geometrie je Zeile/Wort ---------------------------------------------------------


def _word(start_ms: int, end_ms: int, text: str) -> Word:
    return Word(start_ms, end_ms, text)


def test_line_x_position_centers_short_line() -> None:
    line = build_subtitle_lines([_word(0, 100, "eins")])[0]
    width_px = len("eins") * subtitle_burn.CHAR_ADVANCE_WIDTH_PX
    expected = (subtitle_burn.SUBTITLE_FIELD_WIDTH - width_px) / 2
    assert subtitle_burn.line_x_position(line) == pytest.approx(expected)


def test_word_char_offset_accounts_for_spaces() -> None:
    line = build_subtitle_lines(
        [_word(0, 100, "eins"), _word(100, 200, "zwei"), _word(200, 300, "drei")]
    )[0]
    assert subtitle_burn.word_char_offset(line, 0) == 0
    assert subtitle_burn.word_char_offset(line, 1) == len("eins") + 1
    assert subtitle_burn.word_char_offset(line, 2) == len("eins") + 1 + len("zwei") + 1


def test_word_x_position_is_line_x_plus_offset() -> None:
    line = build_subtitle_lines([_word(0, 100, "eins"), _word(100, 200, "zwei")])[0]
    expected = subtitle_burn.line_x_position(line) + subtitle_burn.CHAR_ADVANCE_WIDTH_PX * (
        len("eins") + 1
    )
    assert subtitle_burn.word_x_position(line, 1) == pytest.approx(expected)


# --- Filtergraph: ein drawtext je Zeile plus ein drawtext je Wort ------------------


def test_build_subtitle_filter_complex_chains_dim_and_brass_per_word() -> None:
    # Teil-C-Fix: je Wort EIN drawtext-Paar (--bone-dim, --brass), dieselbe
    # Textdatei fuer beide Farben - garantiert dieselbe Kastenhoehe/Grundlinie.
    lines = build_subtitle_lines([_word(0, 100, "eins"), _word(100, 200, "zwei")])
    filter_complex, video_label = subtitle_burn.build_subtitle_filter_complex(
        lines,
        font_path=Path("font.ttf"),
        word_text_paths=[[Path("w0.txt"), Path("w1.txt")]],
    )
    parts = filter_complex.split(";")
    assert len(parts) == 4  # zwei Woerter, je ein dim- und ein brass-drawtext
    assert parts[0].startswith("[0:v]drawtext=")
    assert f"fontcolor=0x{subtitle_burn.BONE_DIM_HEX}" in parts[0]
    assert "textfile='w0.txt'" in parts[0]
    assert "between(t,0.000,0.200)" in parts[0]  # volle Zeilendauer
    assert f"fontcolor=0x{subtitle_burn.BRASS_HEX}" in parts[1]
    assert "textfile='w0.txt'" in parts[1]  # dieselbe Datei wie parts[0]
    assert "between(t,0.000,0.100)" in parts[1]
    assert f"fontcolor=0x{subtitle_burn.BONE_DIM_HEX}" in parts[2]
    assert "textfile='w1.txt'" in parts[2]
    assert "between(t,0.000,0.200)" in parts[2]
    assert f"fontcolor=0x{subtitle_burn.BRASS_HEX}" in parts[3]
    assert "textfile='w1.txt'" in parts[3]
    assert "between(t,0.100,0.200)" in parts[3]
    assert video_label == "[v3]"


def test_build_subtitle_filter_complex_dim_and_brass_share_position_and_file() -> None:
    lines = build_subtitle_lines([_word(0, 100, "eins"), _word(100, 200, "zwei")])
    filter_complex, _ = subtitle_burn.build_subtitle_filter_complex(
        lines,
        font_path=Path("font.ttf"),
        word_text_paths=[[Path("w0.txt"), Path("w1.txt")]],
    )
    parts = filter_complex.split(";")
    # x-Position und Textdatei sind fuer dim/brass desselben Wortes identisch.
    dim_x = parts[0].split("x=")[1].split(":")[0]
    brass_x = parts[1].split("x=")[1].split(":")[0]
    assert dim_x == brass_x


def test_build_subtitle_filter_complex_escapes_windows_paths() -> None:
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    filter_complex, _ = subtitle_burn.build_subtitle_filter_complex(
        lines,
        font_path=Path("C:/Fonts/JetBrainsMono-Regular.ttf"),
        word_text_paths=[[Path("C:/tmp/w0.txt")]],
    )
    assert "C\\:/Fonts/JetBrainsMono-Regular.ttf" in filter_complex
    assert "C\\:/tmp/w0.txt" in filter_complex


def test_build_subtitle_filter_complex_rejects_mismatched_line_paths() -> None:
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    with pytest.raises(ValueError):
        subtitle_burn.build_subtitle_filter_complex(
            lines, font_path=Path("font.ttf"), word_text_paths=[]
        )


def test_build_subtitle_filter_complex_rejects_mismatched_word_paths() -> None:
    lines = build_subtitle_lines([_word(0, 100, "eins"), _word(100, 200, "zwei")])
    with pytest.raises(ValueError):
        subtitle_burn.build_subtitle_filter_complex(
            lines,
            font_path=Path("font.ttf"),
            word_text_paths=[[Path("w0.txt")]],  # nur ein Pfad statt zwei Woertern
        )


def test_build_ffmpeg_arguments_uses_filter_complex_script() -> None:
    arguments = subtitle_burn.build_ffmpeg_arguments(
        Path("ffmpeg.exe"),
        Path("in.mp4"),
        Path("out.mp4"),
        Path("filter.txt"),
        "[v5]",
    )
    assert "-filter_complex_script" in arguments
    assert arguments[arguments.index("-filter_complex_script") + 1] == str(Path("filter.txt"))
    assert "-filter_complex" not in arguments
    assert arguments.count("-map") == 2
    assert "[v5]" in arguments
    assert "0:a" in arguments
    assert arguments[arguments.index("-c:a") + 1] == "copy"
    assert str(Path("out.mp4")) == arguments[-1]


# --- Orchestrierung: gefaelschter ffmpeg-Prozess, kein echtes Video -----------------


def _canvas_dimensions(*_args, **_kwargs) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    return (subtitle_burn.CANVAS_WIDTH, subtitle_burn.CANVAS_HEIGHT)


def _fake_process_runner(exit_code: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def runner(arguments, timeout):  # type: ignore[no-untyped-def]
        del timeout
        calls.append(list(arguments))
        return subtitle_burn.ProcessResult(exit_code, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_subtitle_burn_rejects_missing_font(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "discover_jetbrains_mono_font", lambda: None)
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    result = subtitle_burn.run_subtitle_burn(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, subtitle_burn.Stage5cFailed)
    assert result.code == "font_not_found"


def test_run_subtitle_burn_rejects_empty_lines(tmp_path: Path) -> None:
    result = subtitle_burn.run_subtitle_burn(
        input_path=tmp_path / "in.mp4",
        lines=[],
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, subtitle_burn.Stage5cFailed)
    assert result.code == "no_subtitle_lines"


def test_run_subtitle_burn_writes_text_files_and_calls_ffmpeg(tmp_path: Path) -> None:
    lines = build_subtitle_lines([_word(0, 100, "eins"), _word(100, 200, "zwei")])
    runner = _fake_process_runner()
    result = subtitle_burn.run_subtitle_burn(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "nested" / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=runner,
    )
    assert isinstance(result, subtitle_burn.ProcessResult)
    assert result.exit_code == 0
    assert (tmp_path / "nested").is_dir()
    [call] = runner.calls  # type: ignore[attr-defined]
    assert "-filter_complex_script" in call


def test_run_stage5c_rejects_input_resolution_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "probe_dimensions", lambda *a, **k: (1920, 1080))
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    result = subtitle_burn.run_stage5c(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, subtitle_burn.Stage5cFailed)
    assert result.code == "input_resolution_mismatch"


def test_run_stage5c_rejects_unknown_input_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "probe_dimensions", lambda *a, **k: None)
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    result = subtitle_burn.run_stage5c(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, subtitle_burn.Stage5cFailed)
    assert result.code == "input_resolution_unknown"


def test_run_stage5c_propagates_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "probe_dimensions", _canvas_dimensions)
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    result = subtitle_burn.run_stage5c(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(exit_code=1, stderr=b"boom"),
    )
    assert isinstance(result, subtitle_burn.ProcessResult)
    assert result.exit_code == 1


def _ok_checks(frame_count: int) -> subtitle_burn.VerifyChecks:
    return subtitle_burn.VerifyChecks(
        input_frame_count=frame_count,
        output_frame_count=frame_count,
        frame_count_ok=True,
        actual_width=subtitle_burn.CANVAS_WIDTH,
        actual_height=subtitle_burn.CANVAS_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        video_start_time=0.0,
        audio_start_time=0.0,
        start_time_ok=True,
    )


def test_run_stage5c_happy_path_writes_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "probe_dimensions", _canvas_dimensions)
    monkeypatch.setattr(
        subtitle_burn, "verify_subtitle_burn_output", lambda *a, **k: _ok_checks(4476)
    )
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    runner = _fake_process_runner()
    result = subtitle_burn.run_stage5c(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=runner,
    )
    assert isinstance(result, subtitle_burn.ProcessResult)
    assert result.exit_code == 0
    report_path = tmp_path / "out.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["checks"]["frame_count"]["input"] == 4476
    assert report["checks"]["frame_count"]["output"] == 4476


def test_run_stage5c_reports_specific_failure_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(subtitle_burn, "probe_dimensions", _canvas_dimensions)
    failing_checks = dataclasses.replace(_ok_checks(4476), dimensions_ok=False)
    monkeypatch.setattr(
        subtitle_burn, "verify_subtitle_burn_output", lambda *a, **k: failing_checks
    )
    lines = build_subtitle_lines([_word(0, 100, "eins")])
    result = subtitle_burn.run_stage5c(
        input_path=tmp_path / "in.mp4",
        lines=lines,
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        font_path=tmp_path / "font.ttf",
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, subtitle_burn.Stage5cFailed)
    assert result.code == "dimension_mismatch"


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


def test_write_subtitle_burn_report_is_atomic_and_readable(tmp_path: Path) -> None:
    checks = _ok_checks(4476)
    payload = subtitle_burn.subtitle_burn_report_payload(checks)
    report_path = tmp_path / "nested" / "out.json"
    subtitle_burn.write_subtitle_burn_report(report_path, payload)
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == payload


def test_discover_jetbrains_mono_font_finds_real_font() -> None:
    # Der Auftrag sagt, die Schrift sei seit heute installiert - Regressionsschutz
    # dafuer, dass die Suchorte auch tatsaechlich treffen.
    found = subtitle_burn.discover_jetbrains_mono_font()
    assert found is not None
    assert found.name == "JetBrainsMono-Regular.ttf"
