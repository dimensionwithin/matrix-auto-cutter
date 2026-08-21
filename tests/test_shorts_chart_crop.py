"""Tests für Shorts-Stufe 3a: fester Chart-Ausschnitt.

Reine Rechnung (Geometrie, Kommandozeile, ``ausschnitt.json``) ohne Video.
Die Orchestrierung wird wie in ``test_shorts_avatar_cut.py`` mit einem
gefälschten ffmpeg-Prozess getestet - kein echtes Video, kein echtes ffmpeg
läuft in diesen Tests. Der echte ffmpeg-Lauf gegen die reale Aufnahme steht im
Bericht ``artefakte\\repeat\\shorts-stufe-3a\\BAUBERICHT-STUFE-3A-2026-08-14.md``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import chart_crop as cc
from matrix_auto_cutter.shorts.candidates import Candidate


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- Geometrie: vier Konstanten an einer Stelle, aus 2560x1440 hergeleitet --------


def test_geometry_constants_match_the_task() -> None:
    assert cc.SOURCE_WIDTH == 2560
    assert cc.SOURCE_HEIGHT == 1440
    assert cc.CROP_WIDTH == 1728
    assert cc.CROP_HEIGHT == 1440
    assert cc.X_OFFSET_MIN == 0
    assert cc.X_OFFSET_MAX == 832
    assert cc.X_OFFSET_DEFAULT == 416
    assert cc.SCALE_FACTOR == 0.625
    assert cc.OUTPUT_WIDTH == 1080
    assert cc.OUTPUT_HEIGHT == 900


def test_source_fps_is_sixty() -> None:
    assert cc.SOURCE_FPS == 60


def test_crop_scale_filter_at_both_edges() -> None:
    assert cc.crop_scale_filter(0) == "crop=1728:1440:0:0,scale=1080:900"
    assert cc.crop_scale_filter(832) == "crop=1728:1440:832:0,scale=1080:900"


def test_crop_scale_filter_rejects_offset_outside_window() -> None:
    with pytest.raises(ValueError):
        cc.crop_scale_filter(-2)
    with pytest.raises(ValueError):
        cc.crop_scale_filter(834)


# --- ausschnitt.json: laden, Voreinstellung, Validierung --------------------------


def test_load_offsets_missing_file_is_empty(tmp_path: Path) -> None:
    assert cc.load_offsets(tmp_path / "does-not-exist.json") == {}


def test_load_offsets_reads_valid_mapping(tmp_path: Path) -> None:
    path = tmp_path / "ausschnitt.json"
    _touch(
        path,
        json.dumps(
            {
                "artifact_type": "matrix_auto_cutter_shorts_ausschnitt",
                "schema_version": "1.0",
                "versatz": {"1": 200, "18": 600},
            }
        ),
    )
    assert cc.load_offsets(path) == {1: 200, 18: 600}


def test_offset_for_candidate_falls_back_to_default() -> None:
    assert cc.offset_for_candidate({1: 200}, 1) == 200
    assert cc.offset_for_candidate({1: 200}, 2) == cc.X_OFFSET_DEFAULT


@pytest.mark.parametrize(
    "versatz",
    [
        {"1": 201},  # ungerade
        {"1": -2},  # unter dem Minimum
        {"1": 834},  # ueber dem Maximum
        {"1": 100.5},  # keine Ganzzahl
        {"1": True},  # bool ist kein zulaessiger Ganzzahlwert
        {"1": "416"},  # String statt Ganzzahl
    ],
)
def test_load_offsets_rejects_invalid_values_loudly(
    tmp_path: Path, versatz: dict[str, object]
) -> None:
    path = tmp_path / "ausschnitt.json"
    _touch(path, json.dumps({"versatz": versatz}))
    with pytest.raises(cc.AusschnittSchemaError):
        cc.load_offsets(path)


def test_load_offsets_rejects_non_numeric_key(tmp_path: Path) -> None:
    path = tmp_path / "ausschnitt.json"
    _touch(path, json.dumps({"versatz": {"not-an-index": 200}}))
    with pytest.raises(cc.AusschnittSchemaError):
        cc.load_offsets(path)


def test_load_offsets_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "ausschnitt.json"
    _touch(path, "{not json")
    with pytest.raises(cc.AusschnittSchemaError):
        cc.load_offsets(path)


def test_load_offsets_missing_versatz_key_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "ausschnitt.json"
    _touch(path, json.dumps({"artifact_type": "x"}))
    assert cc.load_offsets(path) == {}


# --- Kommandozeile: Frames statt Sekunden, Neukodierung von Bild UND Ton ----------


def test_build_ffmpeg_filter_complex_uses_trim_atrim_on_frames() -> None:
    filter_complex, video_label, audio_label = cc.build_ffmpeg_filter_complex(
        start_frame=7555, end_frame=8415, x_offset=416, fps=60
    )
    assert video_label == "[v0]"
    assert audio_label == "[a0]"
    assert "trim=start_frame=7555:end_frame=8415" in filter_complex
    assert "setpts=PTS-STARTPTS" in filter_complex
    assert "crop=1728:1440:416:0,scale=1080:900" in filter_complex
    assert f"atrim=start={7555 / 60:.9f}:end={8415 / 60:.9f}" in filter_complex
    assert "asetpts=PTS-STARTPTS" in filter_complex


def test_build_ffmpeg_filter_complex_rejects_non_positive_span() -> None:
    with pytest.raises(ValueError):
        cc.build_ffmpeg_filter_complex(start_frame=100, end_frame=100, x_offset=0, fps=60)


def test_build_ffmpeg_arguments_uses_expected_shape() -> None:
    arguments = cc.build_ffmpeg_arguments(
        Path("ffmpeg.exe"),
        Path("in.mp4"),
        Path("out.mp4"),
        start_frame=7555,
        end_frame=8415,
        x_offset=416,
        fps=60,
    )
    assert arguments[0] == "ffmpeg.exe"
    # Kein -ss/-t vor -i: der Ausschnitt wird ueber trim/atrim gesetzt.
    assert "-ss" not in arguments
    assert "-t" not in arguments
    assert "-filter_complex" in arguments
    filter_complex = arguments[arguments.index("-filter_complex") + 1]
    assert "trim=start_frame=7555:end_frame=8415" in filter_complex
    assert "crop=1728:1440:416:0,scale=1080:900" in filter_complex
    assert arguments.count("-map") == 2
    assert "[v0]" in arguments
    assert "[a0]" in arguments
    # Ausgabe-Framerate ausdruecklich gesetzt.
    assert "-r" in arguments
    assert arguments[arguments.index("-r") + 1] == "60"
    # Ton wird neu kodiert, keine Streamkopie.
    assert "-c:a" in arguments
    assert arguments[arguments.index("-c:a") + 1] == "aac"
    assert "copy" not in arguments
    assert str(Path("out.mp4")) == arguments[-1]


def test_build_ffmpeg_arguments_rejects_non_positive_span() -> None:
    with pytest.raises(ValueError):
        cc.build_ffmpeg_arguments(
            Path("ffmpeg.exe"), Path("in.mp4"), Path("out.mp4"),
            start_frame=1000, end_frame=1000, x_offset=0,
        )


# --- Plan aus einem Kandidaten ------------------------------------------------------


def _candidate(index: int, start_ms: int, end_ms: int) -> Candidate:
    return Candidate(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        titel="Titel",
        begruendung="Begruendung",
        sicherheit="hoch",
        enthaelt=(),
    )


def test_plan_chart_crop_uses_default_offset_when_unmapped() -> None:
    plan = cc.plan_chart_crop(_candidate(1, 125917, 140250), offsets={})
    assert plan.candidate_index == 1
    assert plan.start_ms == 125917
    assert plan.end_ms == 140250
    assert plan.x_offset == cc.X_OFFSET_DEFAULT
    assert plan.expected_duration_ms == 14333
    assert plan.start_frame == 7555
    assert plan.end_frame == 8415
    assert plan.expected_frame_count == 860


def test_plan_chart_crop_uses_mapped_offset() -> None:
    plan = cc.plan_chart_crop(_candidate(18, 649017, 723617), offsets={18: 600})
    assert plan.x_offset == 600
    assert plan.expected_duration_ms == 74600
    assert plan.start_frame == 38941
    assert plan.end_frame == 43417
    assert plan.expected_frame_count == 4476


# --- Orchestrierung: gefaelschter ffmpeg-Prozess, kein echtes Video ---------------


def _fake_process_runner(exit_code: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def runner(arguments, timeout):  # type: ignore[no-untyped-def]
        del timeout
        calls.append(list(arguments))
        return cc.ProcessResult(exit_code, stderr)

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_chart_crop_creates_output_directory_and_calls_ffmpeg(tmp_path: Path) -> None:
    plan = cc.ChartCropPlan(
        candidate_index=1, start_ms=0, end_ms=1000, x_offset=0,
        start_frame=0, end_frame=60, fps=60,
    )
    output = tmp_path / "nested" / "out.mp4"
    runner = _fake_process_runner()
    result = cc.run_chart_crop(
        input_path=tmp_path / "in.mp4",
        output_path=output,
        plan=plan,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
    )
    assert result.exit_code == 0
    assert output.parent.is_dir()
    assert runner.calls  # type: ignore[attr-defined]


def test_run_stage3a_for_candidate_rejects_resolution_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: (1920, 1080))
    kandidaten_path = tmp_path / "kandidaten.json"
    _touch(
        kandidaten_path,
        json.dumps(
            {
                "kandidaten": [
                    {
                        "index": 1,
                        "start_ms": 0,
                        "end_ms": 1000,
                        "titel": "t",
                        "begruendung": "b",
                        "sicherheit": "hoch",
                    }
                ]
            }
        ),
    )
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=kandidaten_path,
        candidate_index=1,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=None,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, cc.Stage3aFailed)
    assert result.code == "resolution_mismatch"


def test_run_stage3a_for_candidate_rejects_unknown_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: None)
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=tmp_path / "kandidaten.json",
        candidate_index=1,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=None,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, cc.Stage3aFailed)
    assert result.code == "resolution_unknown"


def test_run_stage3a_for_candidate_rejects_unknown_candidate_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: (2560, 1440))
    kandidaten_path = tmp_path / "kandidaten.json"
    _touch(
        kandidaten_path,
        json.dumps(
            {
                "kandidaten": [
                    {
                        "index": 1,
                        "start_ms": 0,
                        "end_ms": 1000,
                        "titel": "t",
                        "begruendung": "b",
                        "sicherheit": "hoch",
                    }
                ]
            }
        ),
    )
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=kandidaten_path,
        candidate_index=99,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=None,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, cc.Stage3aFailed)
    assert result.code == "candidate_not_found"


def test_run_stage3a_for_candidate_rejects_invalid_ausschnitt_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: (2560, 1440))
    kandidaten_path = tmp_path / "kandidaten.json"
    _touch(
        kandidaten_path,
        json.dumps(
            {
                "kandidaten": [
                    {
                        "index": 1,
                        "start_ms": 0,
                        "end_ms": 1000,
                        "titel": "t",
                        "begruendung": "b",
                        "sicherheit": "hoch",
                    }
                ]
            }
        ),
    )
    ausschnitt_path = tmp_path / "ausschnitt.json"
    _touch(ausschnitt_path, json.dumps({"versatz": {"1": 833}}))
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=kandidaten_path,
        candidate_index=1,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=ausschnitt_path,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, cc.Stage3aFailed)
    assert result.code == "ausschnitt_invalid"


def _ok_checks(expected_frame_count: int) -> cc.VerifyChecks:
    return cc.VerifyChecks(
        actual_frame_count=expected_frame_count,
        expected_frame_count=expected_frame_count,
        frame_count_ok=True,
        actual_width=cc.OUTPUT_WIDTH,
        actual_height=cc.OUTPUT_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        av_offset_ms=0.0,
        baseline_av_offset_ms=cc.BASELINE_AV_OFFSET_MS,
        av_offset_ok=True,
    )


def test_run_stage3a_for_candidate_happy_path_uses_mapped_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: (2560, 1440))
    monkeypatch.setattr(cc, "verify_chart_crop_output", lambda *a, **k: _ok_checks(4476))
    kandidaten_path = tmp_path / "kandidaten.json"
    _touch(
        kandidaten_path,
        json.dumps(
            {
                "kandidaten": [
                    {
                        "index": 18,
                        "start_ms": 649017,
                        "end_ms": 723617,
                        "titel": "t",
                        "begruendung": "b",
                        "sicherheit": "hoch",
                    }
                ]
            }
        ),
    )
    ausschnitt_path = tmp_path / "ausschnitt.json"
    _touch(ausschnitt_path, json.dumps({"versatz": {"18": 600}}))
    runner = _fake_process_runner()
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=kandidaten_path,
        candidate_index=18,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=ausschnitt_path,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=runner,
    )
    assert isinstance(result, cc.ProcessResult)
    assert result.exit_code == 0
    [call] = runner.calls  # type: ignore[attr-defined]
    filter_complex = call[call.index("-filter_complex") + 1]
    assert "crop=1728:1440:600:0,scale=1080:900" in filter_complex
    assert "trim=start_frame=38941:end_frame=43417" in filter_complex
    report_path = tmp_path / "out.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["all_ok"] is True
    assert report["candidate_index"] == 18


def test_run_stage3a_for_candidate_reports_specific_failure_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cc, "probe_dimensions", lambda *a, **k: (2560, 1440))
    failing_checks = cc.VerifyChecks(
        actual_frame_count=859,
        expected_frame_count=860,
        frame_count_ok=False,
        actual_width=cc.OUTPUT_WIDTH,
        actual_height=cc.OUTPUT_HEIGHT,
        dimensions_ok=True,
        audio_track_count=1,
        audio_track_count_ok=True,
        av_offset_ms=0.0,
        baseline_av_offset_ms=cc.BASELINE_AV_OFFSET_MS,
        av_offset_ok=True,
    )
    monkeypatch.setattr(cc, "verify_chart_crop_output", lambda *a, **k: failing_checks)
    kandidaten_path = tmp_path / "kandidaten.json"
    _touch(
        kandidaten_path,
        json.dumps(
            {
                "kandidaten": [
                    {
                        "index": 1,
                        "start_ms": 125917,
                        "end_ms": 140250,
                        "titel": "t",
                        "begruendung": "b",
                        "sicherheit": "hoch",
                    }
                ]
            }
        ),
    )
    result = cc.run_stage3a_for_candidate(
        rendered_video_path=tmp_path / "in.mp4",
        kandidaten_path=kandidaten_path,
        candidate_index=1,
        output_path=tmp_path / "out.mp4",
        ausschnitt_path=None,
        ffmpeg_path=Path("ffmpeg.exe"),
        process_runner=_fake_process_runner(),
    )
    assert isinstance(result, cc.Stage3aFailed)
    assert result.code == "frame_count_mismatch"
    report_path = tmp_path / "out.json"
    assert report_path.is_file()


# --- Auftrag shorts-3b-verdrahtung: der bewegte Ausschnitt ------------------------


def test_fester_versatz_bleibt_woertlich_wie_bisher() -> None:
    """Ohne Kurve aendert sich am bisherigen Weg kein Zeichen."""
    assert cc.crop_scale_filter(416) == "crop=1728:1440:416:0,scale=1080:900"


def test_sendcmd_setzt_je_wechsel_genau_ein_kommando() -> None:
    kommandos = cc.crop_sendcmd_kommandos([482, 482, 484, 484, 480], fps=60)
    assert kommandos.count(";") == 2
    assert "crop x 484;" in kommandos
    assert "crop x 480;" in kommandos


def test_sendcmd_setzt_das_kommando_ein_halbes_frame_zu_frueh() -> None:
    """Der Wert an Frame k muss der Kurvenwert an Frame k sein, nicht der an k+1."""
    kommandos = cc.crop_sendcmd_kommandos([482, 600], fps=60)
    # Frame 1 liegt bei 1/60 s = 0,016667 s; das Kommando eine halbe Framedauer davor.
    assert kommandos == "0.008333 crop x 600;"


def test_sendcmd_ohne_wechsel_ist_leer_und_der_filter_bleibt_fest() -> None:
    assert cc.crop_sendcmd_kommandos([600] * 50) == ""
    gefiltert = cc.crop_scale_filter(416, kurve=[600] * 50)
    assert gefiltert == "crop=1728:1440:600:0,scale=1080:900"
    assert "sendcmd" not in gefiltert


def test_crop_startet_auf_dem_ersten_kurvenwert_nicht_auf_x_offset() -> None:
    gefiltert = cc.crop_scale_filter(416, kurve=[482, 600])
    assert gefiltert.startswith("sendcmd=c='")
    assert "crop=1728:1440:482:0" in gefiltert
    assert ":416:" not in gefiltert


def test_sendcmd_verwirft_werte_ausserhalb_des_kontrakts() -> None:
    with pytest.raises(cc.AusschnittSchemaError):
        cc.crop_sendcmd_kommandos([482, 834])  # ausserhalb [0, 832]
    with pytest.raises(cc.AusschnittSchemaError):
        cc.crop_sendcmd_kommandos([482, 483])  # ungerade
    with pytest.raises(ValueError):
        cc.crop_sendcmd_kommandos([])


def test_filter_complex_verlangt_eine_kurve_in_spannenlaenge() -> None:
    with pytest.raises(ValueError):
        cc.build_ffmpeg_filter_complex(
            start_frame=10, end_frame=20, x_offset=416, fps=60, kurve=[482] * 9
        )
    ausdruck, _, _ = cc.build_ffmpeg_filter_complex(
        start_frame=10, end_frame=20, x_offset=416, fps=60, kurve=[482] * 10
    )
    assert "trim=start_frame=10:end_frame=20" in ausdruck
    assert "crop=1728:1440:482:0" in ausdruck


def test_zu_lange_kommandoliste_schlaegt_laut_an() -> None:
    """Eine abgeschnittene Kommandozeile waere ein Syntaxfehler an falscher Stelle."""
    wechselnd = [482 + 2 * (i % 100) for i in range(20_000)]
    with pytest.raises(ValueError, match="Zeichen lang"):
        cc.crop_sendcmd_kommandos(wechselnd)


def test_ausschnitt_json_hat_vorrang_vor_der_kurve() -> None:
    """Der Notausgang, wenn eine Kurve einmal danebenliegt."""
    kandidat = _candidate(3, 0, 1000)
    plan = cc.plan_chart_crop(kandidat, offsets={3: 700}, kurve=[482] * 60)
    assert plan.kurve is None
    assert plan.bewegt is False
    assert plan.x_offset == 700

    ohne_eintrag = cc.plan_chart_crop(kandidat, offsets={}, kurve=[482] * 60)
    assert ohne_eintrag.bewegt is True
    assert ohne_eintrag.kurve is not None and ohne_eintrag.kurve[0] == 482


def test_kurve_erscheint_im_laufbericht() -> None:
    kandidat = _candidate(0, 0, 1000)
    plan = cc.plan_chart_crop(kandidat, offsets={}, kurve=[482] * 59 + [700])
    nutzlast = cc.chart_crop_report_payload(
        plan,
        cc.VerifyChecks(60, 60, True, 1080, 900, True, 1, True, 0.0, 0.0, True),
    )
    assert nutzlast["bewegter_ausschnitt"] is True
    assert nutzlast["x_offset_anfang"] == 482
    assert nutzlast["x_offset_ende"] == 700
