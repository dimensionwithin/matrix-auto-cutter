"""Tests für Shorts-Stufe 2, Transkription: kein echtes ffmpeg, kein echtes whisper-cli.

Der Transkriptionslauf wird wie in ``test_shorts_avatar_cut.py`` gegen einen
gefälschten Prozessläufer getestet, der auf ``ffmpeg``/``whisper-cli``
argv-Muster reagiert statt echte Binärdateien auszuführen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.repeat.errors import BinaryNotFoundError
from matrix_auto_cutter.repeat.process import ProcessResult
from matrix_auto_cutter.shorts import transcript as tr

_RAW_WHISPER_JSON = json.dumps(
    {
        "transcription": [
            {"offsets": {"from": 0, "to": 1200}, "text": " Hallo Welt"},
            {"offsets": {"from": 1200, "to": 2500}, "text": " zweiter Satz"},
        ]
    }
)


def _fake_runner(wav_target: Path, raw_json: str) -> tr.ProcessRunner:
    def run(argv: list[str], timeout_ms: int) -> ProcessResult:
        del timeout_ms
        if "-ojf" in argv:
            wav_path = Path(argv[argv.index("-f") + 1])
            wav_path.with_name(wav_path.name + ".json").write_text(raw_json, encoding="utf-8")
            return ProcessResult(0, "", "", False, 1)
        # ffmpeg-Aufruf: lege die erwartete WAV-Ausgabedatei an.
        output_path = Path(argv[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-wav-bytes")
        wav_target.parent.mkdir(parents=True, exist_ok=True)
        return ProcessResult(0, "", "", False, 1)

    return run


def _prepare_binaries(tmp_path: Path) -> tuple[Path, Path, Path]:
    video = tmp_path / "source.mp4"
    video.write_bytes(b"video-bytes")
    whisper_binary = tmp_path / "whisper-cli.exe"
    whisper_binary.write_bytes(b"b")
    whisper_model = tmp_path / "model.bin"
    whisper_model.write_bytes(b"m")
    return video, whisper_binary, whisper_model


def test_transcript_paths_under_target_dir(tmp_path: Path) -> None:
    raw_json_path, transcript_path = tr.transcript_paths(tmp_path)
    assert raw_json_path == tmp_path / "transkript.wav.json"
    assert transcript_path == tmp_path / "transkript.json"


def test_transcript_paths_rendered_variant_uses_distinct_names(tmp_path: Path) -> None:
    raw_json_path, transcript_path = tr.transcript_paths(
        tmp_path,
        wav_name=tr.RENDERED_WAV_NAME,
        transcript_file_name=tr.RENDERED_TRANSCRIPT_FILE_NAME,
    )
    assert raw_json_path == tmp_path / "transkript-rendered.wav.json"
    assert transcript_path == tmp_path / "transkript-rendered.json"
    # niemals gleich den Rohaufnahme-Namen, sonst wuerde ein Renderlauf das
    # vorhandene Rohtranskript ueberschreiben
    default_raw, default_transcript = tr.transcript_paths(tmp_path)
    assert raw_json_path != default_raw
    assert transcript_path != default_transcript


def test_parse_segments_reads_offsets_and_strips_text() -> None:
    segments = tr.parse_segments(_RAW_WHISPER_JSON)
    assert segments == [
        tr.TranscriptSegment(start_ms=0, end_ms=1200, text="Hallo Welt"),
        tr.TranscriptSegment(start_ms=1200, end_ms=2500, text="zweiter Satz"),
    ]


def test_parse_segments_empty_transcription() -> None:
    assert tr.parse_segments(json.dumps({"transcription": []})) == []


def test_build_transcript_payload_shape() -> None:
    segments = [tr.TranscriptSegment(start_ms=0, end_ms=100, text="hi")]
    payload = tr.build_transcript_payload(segments, source_video="video.mp4")
    assert payload == {
        "artifact_type": "matrix_auto_cutter_shorts_transcript",
        "schema_version": tr.TRANSCRIPT_SCHEMA_VERSION,
        "source_video": "video.mp4",
        "segment_count": 1,
        "segments": [{"start_ms": 0, "end_ms": 100, "text": "hi"}],
    }


def test_write_transcript_writes_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "transkript.json"
    tr.write_transcript(path, {"a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_transcribe_video_runs_ffmpeg_and_whisper(tmp_path: Path) -> None:
    video, whisper_binary, whisper_model = _prepare_binaries(tmp_path)
    target_dir = tmp_path / "out"
    wav_path = target_dir / tr.RAW_WAV_NAME
    runner = _fake_runner(wav_path, _RAW_WHISPER_JSON)

    result = tr.transcribe_video(
        video,
        target_dir=target_dir,
        ffmpeg_path="ffmpeg.exe",
        whisper_binary=str(whisper_binary),
        whisper_model=str(whisper_model),
        runner=runner,
        audio_duration_ms=2_500,
    )

    assert result.status == "written"
    assert result.segment_count == 2
    assert Path(result.raw_json_path).is_file()
    assert Path(result.transcript_path).is_file()
    payload = json.loads(Path(result.transcript_path).read_text(encoding="utf-8"))
    assert payload["segment_count"] == 2
    assert payload["source_video"] == "source.mp4"
    # die extrahierte WAV wird nach einem erfolgreichen Lauf gelöscht
    assert not wav_path.exists()


def test_transcribe_video_rendered_variant_never_touches_raw_transcript(tmp_path: Path) -> None:
    video, whisper_binary, whisper_model = _prepare_binaries(tmp_path)
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    # Vorhandenes Rohtranskript - darf durch den Renderlauf nicht angefasst werden.
    raw_json_path, raw_transcript_path = tr.transcript_paths(target_dir)
    raw_json_path.write_text(_RAW_WHISPER_JSON, encoding="utf-8")
    raw_payload = tr.build_transcript_payload(
        tr.parse_segments(_RAW_WHISPER_JSON), source_video="v"
    )
    tr.write_transcript(raw_transcript_path, raw_payload)
    raw_json_before = raw_json_path.read_text(encoding="utf-8")
    raw_transcript_before = raw_transcript_path.read_text(encoding="utf-8")

    rendered_wav_path = target_dir / tr.RENDERED_WAV_NAME
    rendered_whisper_json = json.dumps(
        {"transcription": [{"offsets": {"from": 0, "to": 900}, "text": " gerendert"}]}
    )
    runner = _fake_runner(rendered_wav_path, rendered_whisper_json)

    result = tr.transcribe_video(
        video,
        target_dir=target_dir,
        ffmpeg_path="ffmpeg.exe",
        whisper_binary=str(whisper_binary),
        whisper_model=str(whisper_model),
        runner=runner,
        audio_duration_ms=900,
        wav_name=tr.RENDERED_WAV_NAME,
        transcript_file_name=tr.RENDERED_TRANSCRIPT_FILE_NAME,
    )

    assert result.status == "written"
    assert result.segment_count == 1
    assert Path(result.raw_json_path) == target_dir / "transkript-rendered.wav.json"
    assert Path(result.transcript_path) == target_dir / "transkript-rendered.json"
    # das Rohtranskript ist byteidentisch geblieben
    assert raw_json_path.read_text(encoding="utf-8") == raw_json_before
    assert raw_transcript_path.read_text(encoding="utf-8") == raw_transcript_before
    # die extrahierte gerenderte WAV wird ebenfalls geloescht
    assert not rendered_wav_path.exists()


def test_transcribe_video_reuses_existing_raw_json(tmp_path: Path) -> None:
    video, whisper_binary, whisper_model = _prepare_binaries(tmp_path)
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    raw_json_path, _ = tr.transcript_paths(target_dir)
    raw_json_path.write_text(_RAW_WHISPER_JSON, encoding="utf-8")

    def runner_should_not_run(argv: list[str], timeout_ms: int) -> ProcessResult:
        raise AssertionError("kein Prozess sollte laufen, wenn die Rohausgabe schon existiert")

    result = tr.transcribe_video(
        video,
        target_dir=target_dir,
        ffmpeg_path="ffmpeg.exe",
        whisper_binary=str(whisper_binary),
        whisper_model=str(whisper_model),
        runner=runner_should_not_run,
        audio_duration_ms=2_500,
    )

    assert result.status == "reused"
    assert result.segment_count == 2


def test_transcribe_video_force_reruns_even_if_raw_json_exists(tmp_path: Path) -> None:
    video, whisper_binary, whisper_model = _prepare_binaries(tmp_path)
    target_dir = tmp_path / "out"
    target_dir.mkdir()
    raw_json_path, _ = tr.transcript_paths(target_dir)
    raw_json_path.write_text(json.dumps({"transcription": []}), encoding="utf-8")
    wav_path = target_dir / tr.RAW_WAV_NAME
    runner = _fake_runner(wav_path, _RAW_WHISPER_JSON)

    result = tr.transcribe_video(
        video,
        target_dir=target_dir,
        ffmpeg_path="ffmpeg.exe",
        whisper_binary=str(whisper_binary),
        whisper_model=str(whisper_model),
        runner=runner,
        audio_duration_ms=2_500,
        force=True,
    )

    assert result.status == "written"
    assert result.segment_count == 2


def test_transcribe_video_missing_whisper_binary_raises(tmp_path: Path) -> None:
    video, _whisper_binary, whisper_model = _prepare_binaries(tmp_path)
    target_dir = tmp_path / "out"
    wav_path = target_dir / tr.RAW_WAV_NAME
    runner = _fake_runner(wav_path, _RAW_WHISPER_JSON)

    with pytest.raises(BinaryNotFoundError):
        tr.transcribe_video(
            video,
            target_dir=target_dir,
            ffmpeg_path="ffmpeg.exe",
            whisper_binary=str(tmp_path / "missing-whisper.exe"),
            whisper_model=str(whisper_model),
            runner=runner,
            audio_duration_ms=2_500,
        )
