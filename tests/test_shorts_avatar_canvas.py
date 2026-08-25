"""Tests für Shorts-Stufe 5b: Avatar auf die Leinwand.

Reine Rechnung (Geometrie, Kommandozeile, Pruefergebnisse) ohne echtes Video.
Die Orchestrierung wird wie in ``test_shorts_canvas.py``/``test_shorts_endcard.py``
mit einem gefälschten ffmpeg-Prozess getestet - kein echtes ffmpeg läuft in
diesen Tests. Der echte ffmpeg-Lauf gegen den realen Pruefstein
(``kandidat-18-laengster-leinwand.mp4`` + ``avatar-cut.mp4``) steht im
Bericht ``artefakte\\repeat\\shorts-stufe-5b\\BERICHT-2026-08-17.md``.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from matrix_auto_cutter.shorts import avatar_canvas

# --- Geometrie: benannte Konstanten an einer Stelle, harte Bedingungen -------------
#
# Auftrag shorts-avatar-1920, Teil B: AVATAR_SOURCE_WIDTH/HEIGHT und
# AVATAR_CROP_X/-Y/-WIDTH/-HEIGHT sind keine festen Modulkonstanten mehr - die
# Quellaufloesung wird jetzt zur Laufzeit gemessen und der Ausschnitt per
# compute_avatar_crop_geometry() daraus hergeleitet (siehe unten). Die
# frueheren "Konstanten bleiben fest"-Tests sind durch Tests auf die
# Herleitungsfunktion ersetzt - inhaltlich derselbe Vertrag (fuer die alte
# 630x422-Quelle liefert die Herleitung exakt dieselben Zahlen wie zuvor),
# nur nicht mehr an Modulkonstanten, sondern an einer Funktion belegt.


def test_compute_avatar_crop_geometry_reproduces_the_legacy_constants() -> None:
    geometry = avatar_canvas.compute_avatar_crop_geometry(630, 422)
    assert geometry.crop_x == 100
    assert geometry.crop_y == 0
    assert geometry.crop_width == 430
    assert geometry.crop_height == 422
    assert geometry.crop_x + geometry.crop_width <= geometry.source_width
    assert geometry.crop_y + geometry.crop_height <= geometry.source_height


def test_compute_avatar_crop_geometry_derives_a_centered_crop_for_1920x1080() -> None:
    # Auftrag shorts-avatar-1920, Teil A/B: Hoehen-Skalierungsfaktor 1080/422 auf die alte
    # Ausschnittbreite 430 angewendet und um die neue Bildmitte zentriert - siehe Bericht.
    geometry = avatar_canvas.compute_avatar_crop_geometry(1920, 1080)
    assert geometry.crop_width == 1100
    assert geometry.crop_x == 410
    assert geometry.crop_y == 0
    assert geometry.crop_height == 1080
    assert geometry.crop_x + geometry.crop_width <= geometry.source_width


def test_compute_avatar_crop_geometry_rejects_unknown_aspect_ratio() -> None:
    import pytest

    with pytest.raises(avatar_canvas.AvatarSourceGeometryError) as excinfo:
        avatar_canvas.compute_avatar_crop_geometry(800, 600)
    assert excinfo.value.code == "avatar_source_aspect_ratio_unsupported"


def test_compute_avatar_crop_geometry_rejects_invalid_dimensions() -> None:
    import pytest

    with pytest.raises(avatar_canvas.AvatarSourceGeometryError) as excinfo:
        avatar_canvas.compute_avatar_crop_geometry(0, 422)
    assert excinfo.value.code == "avatar_source_dimensions_invalid"


def test_scale_preserves_crop_aspect_ratio() -> None:
    # Auftrag shorts-avatar-position-2: 747 statt 830 (zehn Prozent kleiner),
    # damit bei AVATAR_PLACE_X=140 mehr Luft entsteht, ohne die rechte
    # Sicherheitsgrenze (930) zu veraendern.
    assert avatar_canvas.AVATAR_SCALE_WIDTH == 747
    assert avatar_canvas.AVATAR_SCALE_WIDTH < avatar_canvas.CANVAS_WIDTH - avatar_canvas.SAFE_RIGHT
    # Beide gemessenen Quellaufloesungen ergeben dieselbe skalierte Hoehe (733).
    assert avatar_canvas.compute_avatar_crop_geometry(630, 422).scale_height == 733
    assert avatar_canvas.compute_avatar_crop_geometry(1920, 1080).scale_height == 733


def test_placement_sits_100px_below_the_chart_panel() -> None:
    # Teil B (Auftrag shorts-5b-5c-nachbesserung): 100px tiefer als die
    # Panelkante, damit der Untertitel (y=1124..1180) nicht im Gesicht liegt.
    # AVATAR_PLACE_X=140 (Auftrag shorts-avatar-position-2, vorher 80 seit
    # shorts-avatar-position): Luft am linken Rand statt buendig links (0),
    # damit die Figur nicht am Rand angeschnitten wird.
    assert avatar_canvas.AVATAR_PLACE_X == 140
    assert avatar_canvas.AVATAR_PLACE_Y == 1200
    assert avatar_canvas.AVATAR_PLACE_Y == avatar_canvas.PANEL_Y + avatar_canvas.PANEL_HEIGHT + 100


def test_final_size_is_clamped_to_the_remaining_band_height() -> None:
    assert avatar_canvas.AVATAR_BAND_HEIGHT == 720
    assert avatar_canvas.AVATAR_FINAL_WIDTH == 747
    assert avatar_canvas.AVATAR_FINAL_HEIGHT == 720
    geometry = avatar_canvas.compute_avatar_crop_geometry(630, 422)
    assert geometry.scale_height > avatar_canvas.AVATAR_FINAL_HEIGHT
    assert geometry.final_height == avatar_canvas.AVATAR_FINAL_HEIGHT


def test_no_avatar_pixel_may_lie_right_of_the_safe_boundary() -> None:
    right_edge = avatar_canvas.AVATAR_PLACE_X + avatar_canvas.AVATAR_FINAL_WIDTH
    assert right_edge <= avatar_canvas.CANVAS_WIDTH - avatar_canvas.SAFE_RIGHT
    assert right_edge == 887


def test_avatar_top_still_clears_the_chart_panel_after_the_shift() -> None:
    assert avatar_canvas.AVATAR_PLACE_Y > avatar_canvas.PANEL_Y + avatar_canvas.PANEL_HEIGHT


def test_avatar_cannot_overlap_the_chart_panel() -> None:
    assert avatar_canvas.AVATAR_PLACE_Y >= avatar_canvas.PANEL_Y + avatar_canvas.PANEL_HEIGHT


def test_avatar_stays_on_the_canvas() -> None:
    assert (
        avatar_canvas.AVATAR_PLACE_Y + avatar_canvas.AVATAR_FINAL_HEIGHT
        <= avatar_canvas.CANVAS_HEIGHT
    )


# --- Kommandozeile: Freistellung nur im Avatarband, Ton unveraendert ----------------


def test_build_ffmpeg_filter_complex_confines_blend_to_the_avatar_band() -> None:
    filter_complex, video_label, audio_label = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    assert video_label == "[outv]"
    assert audio_label == "0:a"
    # canvas_base bleibt unkonvertiert - nur der Ausschnitt geht durch RGB.
    assert filter_complex.startswith("[0:v]split=2[canvas_base][canvas_crop_src];")
    assert (
        "[canvas_crop_src]crop=747:720:140:1200:exact=1,format=rgb24[canvas_band]"
        in filter_complex
    )
    assert "blend=all_mode=lighten:shortest=1[band_blended]" in filter_complex
    assert "[canvas_base][band_blended]overlay=x=140:y=1200[outv]" in filter_complex
    # trim ab avatar_start_frame=0 bis avatar_end_frame - kein tpad noetig,
    # wenn die Spanne genau der Leinwandlaenge entspricht.
    assert "[1:v]trim=start_frame=0:end_frame=1000,setpts=PTS-STARTPTS," in filter_complex
    assert "tpad" not in filter_complex


def test_build_ffmpeg_filter_complex_crops_the_candidate_span_from_the_middle() -> None:
    # Der zentrale Befund aus Teil A: die Leinwand ist ein Kandidatenausschnitt
    # aus der Mitte, der Avatarzweig muss an derselben Stelle beginnen.
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=4476,
        avatar_frame_count=52913,
        avatar_start_frame=38941,
        avatar_end_frame=43417,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    assert (
        "[1:v]trim=start_frame=38941:end_frame=43417,setpts=PTS-STARTPTS," in filter_complex
    )
    assert "tpad" not in filter_complex


def test_build_ffmpeg_filter_complex_pads_when_the_span_runs_past_the_avatar_end() -> None:
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=4476,
        avatar_frame_count=41417,
        avatar_start_frame=38941,
        avatar_end_frame=43417,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    # Die Avatardatei endet bei 41417 - nur 2476 der angeforderten 4476 Frames
    # sind vorhanden, der Rest (2000) wird per tpad nachgezogen.
    assert "[1:v]trim=start_frame=38941:end_frame=41417,setpts=PTS-STARTPTS," in filter_complex
    assert "tpad=stop=2000:stop_mode=clone," in filter_complex


def test_build_ffmpeg_filter_complex_clips_a_too_long_span_to_the_canvas_length() -> None:
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=5000,
        avatar_start_frame=100,
        avatar_end_frame=2000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    # avatar_end_frame (2000) reicht weiter als benoetigt - auf 100+1000=1100 geklemmt.
    assert "[1:v]trim=start_frame=100:end_frame=1100,setpts=PTS-STARTPTS," in filter_complex
    assert "tpad" not in filter_complex


def test_build_ffmpeg_filter_complex_uses_format_rgb24_on_both_branches() -> None:
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    assert filter_complex.count("format=rgb24") == 2


def test_build_ffmpeg_filter_complex_avatar_branch_crop_scale_crop() -> None:
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    assert (
        "crop=430:422:100:0:exact=1,scale=747:733,crop=747:720:0:0:exact=1,"
        "format=rgb24[avatar_final]"
    ) in filter_complex


def test_build_ffmpeg_filter_complex_avatar_branch_crop_scale_crop_1920x1080() -> None:
    # Auftrag shorts-avatar-1920: dieselbe Herleitung, aber fuer die neue Quellaufloesung -
    # crop_width=1100, crop_x=410 (zentriert, Hoehen-Skalierungsfaktor 1080/422).
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=1920,
        avatar_source_height=1080,
    )
    assert (
        "crop=1100:1080:410:0:exact=1,scale=747:733,crop=747:720:0:0:exact=1,"
        "format=rgb24[avatar_final]"
    ) in filter_complex


def test_build_ffmpeg_filter_complex_all_crops_are_exact() -> None:
    # Auftrag shorts-avatar-position-2: ffmpegs crop-Filter rundet eine
    # UNGERADE Zielbreite auf yuv420p standardmaessig auf die naechste gerade
    # Zahl ab - aber nur dort, wo tatsaechlich etwas weggeschnitten wird.
    # AVATAR_SCALE_WIDTH=747 ist ungerade und betrifft genau diesen Fall (die
    # frueheren, stets geraden Werte 930/830 haben das nie aufgedeckt). Ohne
    # ``exact=1`` auf JEDEM crop-Aufruf driften Leinwand- und Avatarzweig um
    # 1 Pixel auseinander und ``blend`` scheitert mit "Failed to configure
    # output pad" - siehe Modul-/Funktionsdocstring.
    filter_complex, _, _ = avatar_canvas.build_ffmpeg_filter_complex(
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    crop_count = filter_complex.count("crop=")
    exact_crop_count = filter_complex.count(":exact=1")
    assert crop_count == 3, "canvas_band + zwei Avatar-crops"
    assert exact_crop_count == crop_count, "jeder crop-Aufruf muss exact=1 tragen"


def test_build_ffmpeg_filter_complex_rejects_unsupported_aspect_ratio() -> None:
    import pytest

    with pytest.raises(avatar_canvas.AvatarSourceGeometryError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=1000,
            avatar_frame_count=1000,
            avatar_start_frame=0,
            avatar_end_frame=1000,
            avatar_source_width=800,
            avatar_source_height=600,
        )


def test_build_ffmpeg_filter_complex_rejects_nonpositive_frame_counts() -> None:
    import pytest

    with pytest.raises(ValueError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=0,
            avatar_frame_count=100,
            avatar_start_frame=0,
            avatar_end_frame=100,
            avatar_source_width=630,
            avatar_source_height=422,
        )
    with pytest.raises(ValueError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=100,
            avatar_frame_count=0,
            avatar_start_frame=0,
            avatar_end_frame=100,
            avatar_source_width=630,
            avatar_source_height=422,
        )


def test_build_ffmpeg_filter_complex_rejects_invalid_span() -> None:
    import pytest

    with pytest.raises(ValueError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=100,
            avatar_frame_count=1000,
            avatar_start_frame=50,
            avatar_end_frame=50,
            avatar_source_width=630,
            avatar_source_height=422,
        )
    with pytest.raises(ValueError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=100,
            avatar_frame_count=1000,
            avatar_start_frame=-1,
            avatar_end_frame=50,
            avatar_source_width=630,
            avatar_source_height=422,
        )


def test_build_ffmpeg_filter_complex_rejects_span_starting_past_the_avatar_end() -> None:
    import pytest

    with pytest.raises(ValueError):
        avatar_canvas.build_ffmpeg_filter_complex(
            canvas_frame_count=100,
            avatar_frame_count=1000,
            avatar_start_frame=1000,
            avatar_end_frame=1100,
            avatar_source_width=630,
            avatar_source_height=422,
        )


def test_build_ffmpeg_arguments_uses_expected_shape() -> None:
    arguments = avatar_canvas.build_ffmpeg_arguments(
        Path("ffmpeg.exe"),
        Path("canvas.mp4"),
        Path("avatar.mp4"),
        Path("out.mp4"),
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
    )
    assert arguments[0] == "ffmpeg.exe"
    assert arguments.count("-i") == 2
    assert str(Path("canvas.mp4")) in arguments
    assert str(Path("avatar.mp4")) in arguments
    assert "-filter_complex" in arguments
    assert arguments.count("-map") == 2
    assert "[outv]" in arguments
    assert "0:a" in arguments
    assert "-r" in arguments
    assert arguments[arguments.index("-r") + 1] == "60"
    # Ton wird unveraendert uebernommen - Streamkopie, keine Neukodierung.
    assert "-c:a" in arguments
    assert arguments[arguments.index("-c:a") + 1] == "copy"
    # yuv420p ausdruecklich erzwungen.
    assert "-pix_fmt" in arguments
    assert arguments[arguments.index("-pix_fmt") + 1] == "yuv420p"
    assert str(Path("out.mp4")) == arguments[-1]


# --- Orchestrierung: gefaelschter ffmpeg-Prozess, kein echtes Video -----------------


def _fake_process_runner(exit_code: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def runner(arguments, timeout):  # type: ignore[no-untyped-def]
        del timeout
        calls.append(list(arguments))
        return avatar_canvas.ProcessResult(exit_code, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_avatar_canvas_creates_output_directory_and_calls_ffmpeg(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "out.mp4"
    runner = _fake_process_runner()
    result = avatar_canvas.run_avatar_canvas(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=output,
        ffmpeg_path=Path("ffmpeg.exe"),
        canvas_frame_count=1000,
        avatar_frame_count=1000,
        avatar_start_frame=0,
        avatar_end_frame=1000,
        avatar_source_width=630,
        avatar_source_height=422,
        process_runner=runner,
    )
    assert result.exit_code == 0
    assert output.parent.is_dir()
    assert runner.calls  # type: ignore[attr-defined]


# --- run_stage5b: gemeinsame Standardargumente fuer Teil-A-Pruefungen -----------------

_STAGE5B_DEFAULTS = dict(
    canvas_recording_id="2026-08-07 11-35-16",
    avatar_recording_id="2026-08-07 11-35-16",
    candidate_start_ms=649017,
    candidate_end_ms=723617,
    expected_avatar_frame_count=52913,
)


_STAGE5B_DEFAULTS_NO_FRAME_COUNT = {
    key: value
    for key, value in _STAGE5B_DEFAULTS.items()
    if key != "expected_avatar_frame_count"
}


def test_run_stage5b_rejects_missing_expected_frame_count_without_rendered_video(
    tmp_path: Path,
) -> None:
    """Auftrag shorts-bau, Punkt 2: ohne Flag UND ohne Pfad wird fail closed gemeldet."""
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS_NO_FRAME_COUNT,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "expected_avatar_frame_count_missing"


def test_run_stage5b_rejects_unmeasurable_rendered_video(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: None)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        rendered_video_path=tmp_path / "rendered.mp4",
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS_NO_FRAME_COUNT,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "rendered_video_frame_count_unknown"


def test_run_stage5b_measures_expected_frame_count_from_rendered_video(
    tmp_path: Path, monkeypatch
) -> None:
    """Fehlt --expected-avatar-frame-count, misst run_stage5b ihn selbst per ffprobe."""
    monkeypatch.setattr(
        avatar_canvas, "probe_dimensions", lambda path, **k: (
            (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)
            if "canvas" in str(path)
            else (630, 422)
        ),
    )
    # Reihenfolge: 1) Messung aus rendered_video_path, 2) canvas_frame_count,
    # 3) avatar_frame_count - alle drei liefern hier denselben Wert.
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 4476)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(4476)
    )
    args = dict(
        _STAGE5B_DEFAULTS_NO_FRAME_COUNT,
        candidate_start_ms=0,
        candidate_end_ms=74600,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        rendered_video_path=tmp_path / "rendered.mp4",
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 0


def test_run_stage5b_rejects_recording_mismatch(tmp_path: Path) -> None:
    args = dict(_STAGE5B_DEFAULTS, avatar_recording_id="2026-08-09 18-54-14")
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "recording_mismatch"


def test_run_stage5b_rejects_canvas_resolution_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", lambda *a, **k: (1920, 1080))
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "canvas_resolution_mismatch"


def test_run_stage5b_rejects_unknown_canvas_resolution(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", lambda *a, **k: None)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "canvas_resolution_unknown"


def test_run_stage5b_rejects_avatar_aspect_ratio_unsupported(tmp_path: Path, monkeypatch) -> None:
    # Auftrag shorts-avatar-1920, Teil B: die Quellaufloesung ist nicht mehr fest verdrahtet -
    # statt gegen 630x422 zu vergleichen, wird jetzt das Seitenverhaeltnis geprueft. 1280x720
    # ist keiner der beiden gemessenen Faelle (630x422, 1920x1080).
    dimensions_by_call = [(1080, 1920), (800, 600)]

    def fake_probe_dimensions(*args, **kwargs):
        del args, kwargs
        return dimensions_by_call.pop(0)

    monkeypatch.setattr(avatar_canvas, "probe_dimensions", fake_probe_dimensions)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_source_aspect_ratio_unsupported"


def test_run_stage5b_accepts_the_new_1920x1080_avatar_source(tmp_path: Path, monkeypatch) -> None:
    # Auftrag shorts-avatar-1920: die neue, gemessene Quellaufloesung wird akzeptiert, nicht
    # mehr als "avatar_resolution_mismatch" abgelehnt - das ist der zentrale Verhaltenswechsel
    # dieses Auftrags.
    dimensions_by_call = [(1080, 1920), (1920, 1080)]

    def fake_probe_dimensions(*args, **kwargs):
        del args, kwargs
        return dimensions_by_call.pop(0)

    monkeypatch.setattr(avatar_canvas, "probe_dimensions", fake_probe_dimensions)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(52913)
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)


# --- Auftrag shorts-framezahl-cache: Avatarframezahl/-aufloesung als optionale
# vorgemessene Werte - je Lauf konstant, run_stage5b misst sie dann NICHT
# erneut. -----------------------------------------------------------------


def test_run_stage5b_skips_avatar_probing_when_precomputed_values_given(
    tmp_path: Path, monkeypatch
) -> None:
    """Sind Avatar-Framezahl UND -Aufloesung uebergeben, ruft run_stage5b weder
    probe_dimensions noch probe_frame_count fuer ``avatar_path`` auf - nur
    noch fuer ``canvas_path`` (das per-Kandidat-Leinwandbild bleibt gemessen)."""
    dimension_calls: list[Path] = []
    frame_count_calls: list[Path] = []

    def fake_probe_dimensions(path, **k):
        del k
        dimension_calls.append(path)
        assert "avatar" not in path.name, "avatar_path darf hier NICHT geprobt werden"
        return (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)

    def fake_probe_frame_count(path, **k):
        del k
        frame_count_calls.append(path)
        assert "avatar" not in path.name, "avatar_path darf hier NICHT geprobt werden"
        return 52913

    monkeypatch.setattr(avatar_canvas, "probe_dimensions", fake_probe_dimensions)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", fake_probe_frame_count)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(4476)
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        avatar_frame_count=52913,
        avatar_source_width=1920,
        avatar_source_height=1080,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 0
    # Genau ein Aufruf je Funktion, beide auf canvas_path - avatar_path wurde
    # nie geprobt (die obigen asserts in den Fakes waeren sonst schon gefallen).
    assert dimension_calls == [tmp_path / "canvas.mp4"]
    assert frame_count_calls == [tmp_path / "canvas.mp4"]


def test_run_stage5b_still_measures_avatar_when_only_frame_count_given(
    tmp_path: Path, monkeypatch
) -> None:
    """Fehlt (auch nur) eine der drei Groessen, misst run_stage5b sie weiterhin
    selbst - kein stiller Rueckfall auf einen falschen oder halben Cache-Wert."""
    monkeypatch.setattr(
        avatar_canvas,
        "probe_dimensions",
        lambda path, **k: (
            (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)
            if "canvas" in str(path)
            else (1920, 1080)
        ),
    )
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 4476)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(4476)
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        avatar_frame_count=52913,
        # avatar_source_width/-height bewusst NICHT gesetzt.
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 0


def test_run_stage5b_rejects_unknown_avatar_resolution(tmp_path: Path, monkeypatch) -> None:
    dimensions_by_call = [(1080, 1920), None]

    def fake_probe_dimensions(*args, **kwargs):
        del args, kwargs
        return dimensions_by_call.pop(0)

    monkeypatch.setattr(avatar_canvas, "probe_dimensions", fake_probe_dimensions)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_resolution_unknown"


def _fake_probe_dimensions_ok(path, **kwargs):  # type: ignore[no-untyped-def]
    del kwargs
    if "canvas" in str(path):
        return (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)
    return (630, 422)


def test_run_stage5b_rejects_unknown_canvas_frame_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: None)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "canvas_frame_count_unknown"


def test_run_stage5b_rejects_unknown_avatar_frame_count(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    frame_counts = [4476, None]

    def fake_probe_frame_count(*args, **kwargs):
        del args, kwargs
        return frame_counts.pop(0)

    monkeypatch.setattr(avatar_canvas, "probe_frame_count", fake_probe_frame_count)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_frame_count_unknown"


# --- Teil A (Auftrag shorts-avatar-versatz): coverage.missing_frames_front lesen ---------


def test_read_avatar_coverage_missing_frames_front_reads_the_sidecar(tmp_path: Path) -> None:
    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(
        json.dumps({"coverage": {"missing_frames_front": 6}}), encoding="utf-8"
    )
    assert avatar_canvas.read_avatar_coverage_missing_frames_front(avatar_path) == 6


def test_read_avatar_coverage_missing_frames_front_rejects_missing_sidecar(
    tmp_path: Path,
) -> None:
    import pytest

    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_front(tmp_path / "avatar-cut.mp4")
    assert excinfo.value.code == "avatar_coverage_sidecar_missing"


def test_read_avatar_coverage_missing_frames_front_rejects_missing_field(
    tmp_path: Path,
) -> None:
    import pytest

    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(json.dumps({"coverage": {}}), encoding="utf-8")
    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_front(avatar_path)
    assert excinfo.value.code == "avatar_coverage_field_missing"


def test_read_avatar_coverage_missing_frames_front_rejects_negative_value(
    tmp_path: Path,
) -> None:
    import pytest

    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(
        json.dumps({"coverage": {"missing_frames_front": -1}}), encoding="utf-8"
    )
    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_front(avatar_path)
    assert excinfo.value.code == "avatar_coverage_field_negative"


# --- Auftrag shorts-avatar-endversatz: coverage.missing_frames_back lesen, dieselben
# Fehlercodes wie missing_frames_front (siehe _read_avatar_coverage_field). ---------------


def test_read_avatar_coverage_missing_frames_back_reads_the_sidecar(tmp_path: Path) -> None:
    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(
        json.dumps({"coverage": {"missing_frames_back": 60}}), encoding="utf-8"
    )
    assert avatar_canvas.read_avatar_coverage_missing_frames_back(avatar_path) == 60


def test_read_avatar_coverage_missing_frames_back_rejects_missing_sidecar(
    tmp_path: Path,
) -> None:
    import pytest

    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_back(tmp_path / "avatar-cut.mp4")
    assert excinfo.value.code == "avatar_coverage_sidecar_missing"


def test_read_avatar_coverage_missing_frames_back_rejects_missing_field(
    tmp_path: Path,
) -> None:
    import pytest

    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(json.dumps({"coverage": {}}), encoding="utf-8")
    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_back(avatar_path)
    assert excinfo.value.code == "avatar_coverage_field_missing"


def test_read_avatar_coverage_missing_frames_back_rejects_negative_value(
    tmp_path: Path,
) -> None:
    import pytest

    avatar_path = tmp_path / "avatar-cut.mp4"
    (tmp_path / "avatar-cut.json").write_text(
        json.dumps({"coverage": {"missing_frames_back": -1}}), encoding="utf-8"
    )
    with pytest.raises(avatar_canvas.AvatarCoverageError) as excinfo:
        avatar_canvas.read_avatar_coverage_missing_frames_back(avatar_path)
    assert excinfo.value.code == "avatar_coverage_field_negative"


def test_run_stage5b_rejects_missing_avatar_coverage_sidecar(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_coverage_sidecar_missing"


# --- Teil C: die berichtigte Achsenpruefung (Framezahl + Versatz == Videolaenge) ---------


def test_run_stage5b_rejects_avatar_frame_count_axis_mismatch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    args = dict(_STAGE5B_DEFAULTS, expected_avatar_frame_count=52919)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_frame_count_axis_mismatch"


# --- Auftrag shorts-achsenpruefung-warnung: Toleranz statt Abbruch bei kleiner Abweichung -

def test_run_stage5b_still_rejects_a_deviation_of_six_frames(tmp_path: Path, monkeypatch) -> None:
    """Pruefstein Punkt 2: sechs Frames Abweichung ueberschreiten die Toleranz (5) - Abbruch."""
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    args = dict(_STAGE5B_DEFAULTS, expected_avatar_frame_count=52913 + 6)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "avatar_frame_count_axis_mismatch"


def test_run_stage5b_warns_but_builds_at_a_deviation_of_five_frames(
    tmp_path: Path, monkeypatch
) -> None:
    """Pruefstein Punkt 2: fuenf Frames Abweichung liegen noch innerhalb der Toleranz."""
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(52913)
    )
    args = dict(_STAGE5B_DEFAULTS, expected_avatar_frame_count=52913 + 5)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 0
    assert result.achsenabweichung_frames == -5
    assert result.achsenabweichung_hinweis is not None
    assert "5" in result.achsenabweichung_hinweis


def test_run_stage5b_reports_zero_deviation_as_no_hint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(52913)
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.achsenabweichung_frames == 0
    assert result.achsenabweichung_hinweis is None


def test_run_stage5b_accepts_axis_check_once_the_offset_is_added_back(
    tmp_path: Path, monkeypatch
) -> None:
    # 52913 (gemessene Avatar-Framezahl) + 6 (missing_frames_front) + 0 (missing_frames_back)
    # == 52919 (Videolaenge) - die zuvor falsche Bedingung (Framezahl == Videolaenge) haette
    # das zurueckgewiesen.
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 6
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(52913)
    )
    args = dict(_STAGE5B_DEFAULTS, expected_avatar_frame_count=52919)
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)


def test_run_stage5b_accepts_axis_check_once_the_back_offset_is_added(
    tmp_path: Path, monkeypatch
) -> None:
    """Auftrag shorts-avatar-endversatz - der eigentliche Befund dieses Auftrags.

    46483 (gemessene Avatar-Framezahl) + 0 (missing_frames_front) + 60 (missing_frames_back)
    == 46543 (Videolaenge). Die VORHERIGE Pruefung (nur Framezahl + missing_frames_front)
    haette hier 46483 != 46543 gerechnet und JEDEN Kandidaten dieser Aufnahme abgelehnt, obwohl
    der hintere Versatz der Normalfall ist (Source Record stoppt minimal frueher).
    """
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 46483)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 60
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(46483)
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=46543,
        candidate_start_ms=649017,
        candidate_end_ms=723617,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)


# --- Teil B: die Kandidatenspanne wird um missing_frames_front verschoben ----------------


def test_run_stage5b_shifts_the_candidate_span_by_missing_frames_front(
    tmp_path: Path, monkeypatch
) -> None:
    # Beleg aus dem Auftrag: Kandidat 18 ergibt gerendert (38941, 43417); im Avatar muss
    # (38935, 43411) herausgeschnitten werden - Laenge bleibt 4476.
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 6
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    # Die Ausgabepruefung nach dem (gefaelschten) ffmpeg-Aufruf sondierte
    # bisher mit echtem ``ffprobe`` an der nie entstandenen out.mp4 - ein
    # Unterprozess, den dieser Test nicht braucht und nicht prueft.
    monkeypatch.setattr(avatar_canvas, "discover_ffprobe", lambda *a, **k: None)
    monkeypatch.setattr(avatar_canvas, "probe_audio_track_count", lambda *a, **k: 1)
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=52919,
        candidate_start_ms=649017,
        candidate_end_ms=723617,
    )
    runner = _fake_process_runner()
    avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
        **args,
    )
    # Der ffmpeg-Aufruf selbst ist das, was hier belegt wird - was danach mit dem
    # (in diesem Test nicht gefaelschten) Ergebnis der Ausgabepruefung passiert, ist nicht
    # Gegenstand dieses Tests.
    [call] = runner.calls  # type: ignore[attr-defined]
    filter_complex = call[call.index("-filter_complex") + 1]
    assert "trim=start_frame=38935:end_frame=43411,setpts=PTS-STARTPTS," in filter_complex


def test_run_stage5b_rejects_candidate_preceding_avatar_start(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 100)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 6
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=106,
        candidate_start_ms=0,
        candidate_end_ms=1000,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "candidate_precedes_avatar_start"


def test_run_stage5b_rejects_candidate_span_outside_avatar(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 100)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=100,
        candidate_start_ms=100_000,
        candidate_end_ms=110_000,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "candidate_span_outside_avatar"


# --- Punkt 6 (Auftrag shorts-avatar-endversatz): eine Spanne, die den fehlenden Bereich AM
# ENDE beruehrt (aber nicht komplett dahinter beginnt), wird fail closed abgelehnt statt
# stillschweigend mit einem stehenden letzten Bild gebaut zu werden. ----------------------


def test_run_stage5b_rejects_candidate_span_touching_missing_frames_back(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 100)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 10
    )
    # Avatar hat nur 100 Frames (front=0, back=10 -> erwartete Videolaenge 110). Der
    # Kandidat beginnt bei Frame 60 (innerhalb der Avatardatei) und endet bei Frame 120 -
    # er reicht damit ueber avatar_frame_count (100) hinaus, in den fehlenden Bereich am
    # Ende. candidate_start_ms=1000/candidate_end_ms=2000 ergeben bei CANVAS_FPS=60 genau
    # diese Framespanne (ms_to_frame(1000,60)=60, ms_to_frame(2000,60)=120).
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=110,
        candidate_start_ms=1000,
        candidate_end_ms=2000,
    )
    runner = _fake_process_runner()
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
    assert result.code == "candidate_span_touches_missing_frames_back"
    # kein ffmpeg-Aufruf - der Kandidat wird gar nicht erst gebaut.
    assert runner.calls == []  # type: ignore[attr-defined]


def test_run_stage5b_accepts_a_span_that_stays_clear_of_missing_frames_back(
    tmp_path: Path, monkeypatch
) -> None:
    # Derselbe Aufbau wie oben, aber der Kandidat endet VOR dem fehlenden Bereich
    # (Frame 90 < avatar_frame_count 100) - unberuehrt, wird normal gebaut.
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 100)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 10
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(100)
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=110,
        candidate_start_ms=1000,
        candidate_end_ms=1500,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)


def test_run_stage5b_propagates_ffmpeg_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(avatar_canvas, "probe_dimensions", _fake_probe_dimensions_ok)
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 52913)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(exit_code=1, stderr=b"boom"),
        **_STAGE5B_DEFAULTS,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 1


def _ok_checks(frame_count: int) -> avatar_canvas.VerifyChecks:
    return avatar_canvas.VerifyChecks(
        canvas_frame_count=frame_count,
        output_frame_count=frame_count,
        frame_count_ok=True,
        actual_width=avatar_canvas.CANVAS_WIDTH,
        actual_height=avatar_canvas.CANVAS_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        video_start_time=0.0,
        audio_start_time=0.0,
        start_time_ok=True,
    )


def test_run_stage5b_happy_path_writes_report(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        avatar_canvas, "probe_dimensions", lambda path, **k: (
            (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)
            if "canvas" in str(path)
            else (630, 422)
        ),
    )
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 4476)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: _ok_checks(4476)
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=4476,
        candidate_start_ms=0,
        candidate_end_ms=74600,
    )
    runner = _fake_process_runner()
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
        **args,
    )
    assert isinstance(result, avatar_canvas.ProcessResult)
    assert result.exit_code == 0
    [call] = runner.calls  # type: ignore[attr-defined]
    assert "overlay=x=140:y=1200[outv]" in call[call.index("-filter_complex") + 1]
    assert "trim=start_frame=0:end_frame=4476,setpts=PTS-STARTPTS," in call[
        call.index("-filter_complex") + 1
    ]
    report_path = tmp_path / "out.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["checks"]["frame_count"]["canvas"] == 4476
    assert report["checks"]["frame_count"]["output"] == 4476


def test_run_stage5b_reports_specific_failure_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        avatar_canvas, "probe_dimensions", lambda path, **k: (
            (avatar_canvas.CANVAS_WIDTH, avatar_canvas.CANVAS_HEIGHT)
            if "canvas" in str(path)
            else (630, 422)
        ),
    )
    monkeypatch.setattr(avatar_canvas, "probe_frame_count", lambda *a, **k: 4476)
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_front", lambda *a, **k: 0
    )
    monkeypatch.setattr(
        avatar_canvas, "read_avatar_coverage_missing_frames_back", lambda *a, **k: 0
    )
    failing_checks = dataclasses.replace(
        _ok_checks(4476), output_frame_count=4470, frame_count_ok=False
    )
    monkeypatch.setattr(
        avatar_canvas, "verify_avatar_canvas_output", lambda *a, **k: failing_checks
    )
    args = dict(
        _STAGE5B_DEFAULTS,
        expected_avatar_frame_count=4476,
        candidate_start_ms=0,
        candidate_end_ms=74600,
    )
    result = avatar_canvas.run_stage5b(
        canvas_path=tmp_path / "canvas.mp4",
        avatar_path=tmp_path / "avatar.mp4",
        output_path=tmp_path / "out.mp4",
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
        **args,
    )
    assert isinstance(result, avatar_canvas.Stage5bFailed)
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


def test_write_avatar_canvas_report_is_atomic_and_readable(tmp_path: Path) -> None:
    checks = _ok_checks(4476)
    payload = avatar_canvas.avatar_canvas_report_payload(checks)
    report_path = tmp_path / "nested" / "out.json"
    avatar_canvas.write_avatar_canvas_report(report_path, payload)
    assert report_path.is_file()
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    assert loaded == payload
