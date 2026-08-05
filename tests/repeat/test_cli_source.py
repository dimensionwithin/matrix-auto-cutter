"""Tests for the ``--source`` CLI path. No real ffmpeg/ffprobe/whisper subprocess is started."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import matrix_auto_cutter.repeat.cli as cli_module
from matrix_auto_cutter.repeat.cli import main
from matrix_auto_cutter.repeat.process import ProcessResult

_RAW_WHISPER_JSON = json.dumps(
    {
        "transcription": [
            {
                "offsets": {"from": 0, "to": 2_000},
                "tokens": [
                    {"text": "[_BEG_]", "offsets": {"from": 0, "to": 0}, "p": 0.9},
                    {"text": " ich", "offsets": {"from": 0, "to": 400}, "p": 0.9},
                    {"text": " gehe", "offsets": {"from": 400, "to": 900}, "p": 0.9},
                    {"text": " nach", "offsets": {"from": 900, "to": 1_400}, "p": 0.9},
                    {"text": " Hause", "offsets": {"from": 1_400, "to": 2_000}, "p": 0.9},
                    {"text": "[_EOT_]", "offsets": {"from": 2_000, "to": 2_000}, "p": 0.9},
                ],
            }
        ]
    }
)


class _FakeRunner:
    def __init__(self, whisper_json: str, ffprobe_stdout: str = "5.0") -> None:
        self._whisper_json = whisper_json
        self._ffprobe_stdout = ffprobe_stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
        self.calls.append(argv)
        if "-show_entries" in argv:
            return ProcessResult(0, self._ffprobe_stdout, "", False, 1)
        if "-c:a" in argv:
            output_path = Path(argv[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"RIFF")
            return ProcessResult(0, "", "", False, 1)
        if "-ojf" in argv:
            wav_path = Path(argv[argv.index("-f") + 1])
            json_path = wav_path.with_name(wav_path.name + ".json")
            json_path.write_text(self._whisper_json, encoding="utf-8")
            return ProcessResult(0, "", "", False, 1)
        raise AssertionError(f"unexpected argv: {argv}")


def _patch_runner(monkeypatch: Any, runner: _FakeRunner) -> None:
    monkeypatch.setattr(cli_module, "NativeProcessRunner", lambda: runner)


def test_source_path_transcribes_and_writes_diagnostics(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    out_path = tmp_path / "diagnostics.json"
    _patch_runner(monkeypatch, _FakeRunner(_RAW_WHISPER_JSON))

    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    document = json.loads(out_path.read_text(encoding="utf-8"))
    assert document["artifact_type"] == "matrix_auto_cutter_repeat_diagnostics"


def test_source_default_max_segment_len_is_passed_to_whisper(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    runner = _FakeRunner(_RAW_WHISPER_JSON)
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert exit_code == 0
    whisper_argv = next(call for call in runner.calls if "-ojf" in call)
    assert whisper_argv[-2:] == ["-ml", "60"]


def test_source_custom_max_segment_len_is_passed_to_whisper(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    runner = _FakeRunner(_RAW_WHISPER_JSON)
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--max-segment-len",
            "0",
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert exit_code == 0
    whisper_argv = next(call for call in runner.calls if "-ojf" in call)
    assert "-ml" not in whisper_argv


def test_source_and_transcript_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--source",
                "in.mp4",
                "--transcript",
                "t.json",
                "--out",
                str(tmp_path / "out.json"),
            ]
        )
    assert excinfo.value.code == 2


def test_missing_transcript_and_source_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--out", str(tmp_path / "out.json")])
    assert excinfo.value.code == 2


def test_source_requires_whisper_binary_model_and_work_dir(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--source", "in.mp4", "--out", str(tmp_path / "out.json")])
    assert excinfo.value.code == 2


def test_emit_transcript_writes_converted_document(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    out_path = tmp_path / "diagnostics.json"
    emit_path = tmp_path / "transcript.json"
    _patch_runner(monkeypatch, _FakeRunner(_RAW_WHISPER_JSON))

    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--emit-transcript",
            str(emit_path),
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    emitted = json.loads(emit_path.read_text(encoding="utf-8"))
    assert emitted["artifact_type"] == "matrix_auto_cutter_repeat_transcript"
    assert emitted["source_duration_ms"] == 5_000


def test_source_windowed_transcription_uses_window_offset(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    out_path = tmp_path / "diagnostics.json"
    emit_path = tmp_path / "transcript.json"
    runner = _FakeRunner(_RAW_WHISPER_JSON, ffprobe_stdout="600.0")
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--window-start-ms",
            "120000",
            "--window-end-ms",
            "300000",
            "--emit-transcript",
            str(emit_path),
            "--out",
            str(out_path),
        ]
    )
    assert exit_code == 0
    emitted = json.loads(emit_path.read_text(encoding="utf-8"))
    assert emitted["source_duration_ms"] == 600_000
    first_word = emitted["segments"][0]["words"][0]
    assert first_word["start_ms"] == 120_000


def test_ffprobe_failure_maps_to_documented_exit_code(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")

    class _FailingRunner:
        def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
            return ProcessResult(1, "", "probe failed", False, 1)

    monkeypatch.setattr(cli_module, "NativeProcessRunner", lambda: _FailingRunner())
    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert exit_code == 5


def test_emit_transcript_persists_when_a_later_step_raises(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    out_path = tmp_path / "diagnostics.json"
    emit_path = tmp_path / "transcript.json"
    _patch_runner(monkeypatch, _FakeRunner(_RAW_WHISPER_JSON))

    def _boom(*args: Any, **kwargs: Any) -> Any:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(cli_module, "build_diagnostics", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        main(
            [
                "--source",
                str(source),
                "--whisper-binary",
                str(binary),
                "--whisper-model",
                str(model),
                "--work-dir",
                str(tmp_path / "work"),
                "--emit-transcript",
                str(emit_path),
                "--out",
                str(out_path),
            ]
        )

    assert emit_path.exists()
    emitted = json.loads(emit_path.read_text(encoding="utf-8"))
    assert emitted["artifact_type"] == "matrix_auto_cutter_repeat_transcript"
    assert not out_path.exists()


def test_binary_not_found_maps_to_documented_exit_code(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    _patch_runner(monkeypatch, _FakeRunner(_RAW_WHISPER_JSON))
    exit_code = main(
        [
            "--source",
            str(source),
            "--whisper-binary",
            str(tmp_path / "missing-binary.exe"),
            "--whisper-model",
            str(model),
            "--work-dir",
            str(tmp_path / "work"),
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert exit_code == 2
