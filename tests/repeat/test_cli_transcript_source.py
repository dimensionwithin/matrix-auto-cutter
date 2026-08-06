"""Tests for combining --transcript with --source (REPEAT-2B).

whisper-cli must never run when --transcript is given, regardless of whether
--source is also given. --source in that combination exists only to supply
audio for --snippet-dir/--emit-review. No real ffmpeg/ffprobe/whisper
subprocess is started.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.repeat.conftest import transcript_dict, utterance_segment

import matrix_auto_cutter.repeat.cli as cli_module
from matrix_auto_cutter.repeat.cli import main
from matrix_auto_cutter.repeat.process import ProcessResult


def _valid_transcript_json() -> str:
    raw = transcript_dict(
        [
            utterance_segment("ich gehe jetzt nach hause", 0),
            utterance_segment("ich gehe jetzt nach hause", 2_000),
        ],
        source_duration_ms=10_000,
    )
    return json.dumps(raw)


class _FakeRunner:
    """Records every argv it is called with; never invokes whisper for real."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
        self.calls.append(argv)
        if "-show_entries" in argv:
            return ProcessResult(0, "10.0", "", False, 1)
        if "-c:a" in argv and "aac" in argv:
            output_path = Path(argv[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"m4a-bytes")
            return ProcessResult(0, "", "", False, 1)
        if "-ojf" in argv:
            raise AssertionError("whisper-cli must not be invoked when --transcript is given")
        raise AssertionError(f"unexpected argv: {argv}")

    def whisper_was_called(self) -> bool:
        return any("-ojf" in call for call in self.calls)

    def ffmpeg_snippet_calls(self) -> list[list[str]]:
        return [call for call in self.calls if "-c:a" in call and "aac" in call]


def _patch_runner(monkeypatch: Any, runner: _FakeRunner) -> None:
    monkeypatch.setattr(cli_module, "NativeProcessRunner", lambda: runner)


def test_transcript_and_source_together_skips_whisper_and_writes_review(
    tmp_path: Path, monkeypatch: Any
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    snippet_dir = tmp_path / "snippets"
    review_path = tmp_path / "review.html"
    runner = _FakeRunner()
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--transcript",
            str(transcript_path),
            "--source",
            str(source),
            "--out",
            str(out_path),
            "--snippet-dir",
            str(snippet_dir),
            "--emit-review",
            str(review_path),
        ]
    )

    assert exit_code == 0
    assert not runner.whisper_was_called()
    assert len(runner.ffmpeg_snippet_calls()) >= 1
    assert review_path.is_file()
    document = json.loads(out_path.read_text(encoding="utf-8"))
    assert document["artifact_type"] == "matrix_auto_cutter_repeat_diagnostics"


def test_transcript_only_with_emit_review_and_no_source_is_a_parser_error(
    tmp_path: Path,
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")
    review_path = tmp_path / "review.html"

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--transcript",
                str(transcript_path),
                "--out",
                str(tmp_path / "out.json"),
                "--snippet-dir",
                str(tmp_path / "snippets"),
                "--emit-review",
                str(review_path),
            ]
        )

    assert excinfo.value.code == 2


def test_transcript_only_with_snippet_dir_and_no_source_is_a_parser_error(
    tmp_path: Path, capsys: Any
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--transcript",
                str(transcript_path),
                "--out",
                str(tmp_path / "out.json"),
                "--snippet-dir",
                str(tmp_path / "snippets"),
            ]
        )

    assert excinfo.value.code == 2
    assert "--source" in capsys.readouterr().err


def test_transcript_and_source_with_whisper_binary_warns_and_ignores_it(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    out_path = tmp_path / "diagnostics.json"
    runner = _FakeRunner()
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--transcript",
            str(transcript_path),
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--whisper-model",
            str(model),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    assert not runner.whisper_was_called()
    stderr = capsys.readouterr().err
    assert "Warnung" in stderr
    assert "--whisper-binary" in stderr


def test_transcript_and_source_with_only_whisper_binary_still_warns(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    out_path = tmp_path / "diagnostics.json"
    runner = _FakeRunner()
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--transcript",
            str(transcript_path),
            "--source",
            str(source),
            "--whisper-binary",
            str(binary),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    assert "Warnung" in capsys.readouterr().err


def test_transcript_and_source_without_whisper_flags_is_silent(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(_valid_transcript_json(), encoding="utf-8")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    runner = _FakeRunner()
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            "--transcript",
            str(transcript_path),
            "--source",
            str(source),
            "--out",
            str(out_path),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().err == ""


def test_missing_transcript_and_source_is_still_an_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--out", str(tmp_path / "out.json")])
    assert excinfo.value.code == 2
