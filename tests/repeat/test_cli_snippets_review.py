"""Tests for --snippet-dir/--emit-review CLI wiring. No real ffmpeg/ffprobe/whisper subprocess."""

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
                    {"text": " Gehen", "offsets": {"from": 0, "to": 400}, "p": 0.9},
                    {"text": " wir", "offsets": {"from": 400, "to": 900}, "p": 0.9},
                    {"text": " mal", "offsets": {"from": 900, "to": 1_400}, "p": 0.9},
                    {"text": " rein.", "offsets": {"from": 1_400, "to": 2_000}, "p": 0.9},
                ],
            },
            {
                "offsets": {"from": 2_000, "to": 4_000},
                "tokens": [
                    {"text": " Gehen", "offsets": {"from": 2_000, "to": 2_400}, "p": 0.9},
                    {"text": " wir", "offsets": {"from": 2_400, "to": 2_900}, "p": 0.9},
                    {"text": " mal", "offsets": {"from": 2_900, "to": 3_400}, "p": 0.9},
                    {"text": " rein.", "offsets": {"from": 3_400, "to": 4_000}, "p": 0.9},
                    {"text": "[_EOT_]", "offsets": {"from": 4_000, "to": 4_000}, "p": 0.9},
                ],
            },
        ]
    }
)


class _FakeRunner:
    def __init__(self, whisper_json: str, ffprobe_stdout: str = "10.0") -> None:
        self._whisper_json = whisper_json
        self._ffprobe_stdout = ffprobe_stdout
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], timeout_ms: int) -> ProcessResult:
        self.calls.append(argv)
        if "-show_entries" in argv:
            return ProcessResult(0, self._ffprobe_stdout, "", False, 1)
        if "-c:a" in argv and "aac" in argv:
            output_path = Path(argv[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"m4a-bytes")
            return ProcessResult(0, "", "", False, 1)
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


def _base_args(tmp_path: Path, source: Path, out_path: Path) -> list[str]:
    binary = tmp_path / "whisper-cli.exe"
    binary.write_bytes(b"b")
    model = tmp_path / "model.bin"
    model.write_bytes(b"m")
    return [
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


def test_existing_behavior_unaffected_without_new_flags(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    _patch_runner(monkeypatch, _FakeRunner(_RAW_WHISPER_JSON))

    exit_code = main(_base_args(tmp_path, source, out_path))
    assert exit_code == 0
    document = json.loads(out_path.read_text(encoding="utf-8"))
    assert document["artifact_type"] == "matrix_auto_cutter_repeat_diagnostics"


def test_snippet_dir_writes_manifest_and_clips(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    snippet_dir = tmp_path / "snippets"
    runner = _FakeRunner(_RAW_WHISPER_JSON)
    _patch_runner(monkeypatch, runner)

    exit_code = main([*_base_args(tmp_path, source, out_path), "--snippet-dir", str(snippet_dir)])
    assert exit_code == 0
    manifest_path = snippet_dir / "snippets.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest) >= 1
    assert Path(manifest[0]["path"]).exists()


def test_emit_review_writes_single_html_file(tmp_path: Path, monkeypatch: Any) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    snippet_dir = tmp_path / "snippets"
    review_path = tmp_path / "review.html"
    runner = _FakeRunner(_RAW_WHISPER_JSON)
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            *_base_args(tmp_path, source, out_path),
            "--snippet-dir",
            str(snippet_dir),
            "--emit-review",
            str(review_path),
        ]
    )
    assert exit_code == 0
    html_text = review_path.read_text(encoding="utf-8")
    assert html_text.startswith("<!doctype html>")
    assert "m4a-bytes" not in html_text  # only present base64-encoded, not raw
    assert "base64" in html_text


def test_emit_review_without_snippet_dir_is_a_parser_error(tmp_path: Path) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    review_path = tmp_path / "review.html"
    with pytest.raises(SystemExit) as excinfo:
        main([*_base_args(tmp_path, source, out_path), "--emit-review", str(review_path)])
    assert excinfo.value.code == 2


def test_no_boundary_with_snippet_dir_and_review_uses_v1_0_candidates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    out_path = tmp_path / "diagnostics.json"
    snippet_dir = tmp_path / "snippets"
    review_path = tmp_path / "review.html"
    runner = _FakeRunner(_RAW_WHISPER_JSON)
    _patch_runner(monkeypatch, runner)

    exit_code = main(
        [
            *_base_args(tmp_path, source, out_path),
            "--no-boundary",
            "--snippet-dir",
            str(snippet_dir),
            "--emit-review",
            str(review_path),
        ]
    )
    assert exit_code == 0
    assert review_path.is_file()


class _FakeSpan:
    def __init__(self, start_ms: int, end_ms: int, text: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.text = text


class _FakeCandidate:
    def __init__(self) -> None:
        self.first = _FakeSpan(0, 1_000, "eins")
        self.second = _FakeSpan(1_000, 2_000, "zwei")


class _FakeManifestEntry:
    def __init__(self, nr: int, path: str | None, error: str | None) -> None:
        self.nr = nr
        self.path = path
        self.error = error


def test_candidate_detectors_string_detector_field() -> None:
    candidate = _FakeCandidate()
    candidate.detector = "utterance"
    assert cli_module._candidate_detectors(candidate) == ("utterance",)


def test_build_review_entries_manifest_entry_with_error() -> None:
    candidate = _FakeCandidate()
    manifest_entries = [_FakeManifestEntry(nr=1, path=None, error="ffmpeg fehlgeschlagen")]
    entries = cli_module._build_review_entries((candidate,), "stem", "source.mp4", manifest_entries)
    assert entries[0].audio_bytes is None
    assert entries[0].audio_error == "ffmpeg fehlgeschlagen"


def test_build_review_entries_no_manifest_entry_at_all() -> None:
    candidate = _FakeCandidate()
    entries = cli_module._build_review_entries((candidate,), "stem", "source.mp4", [])
    assert entries[0].audio_bytes is None
    assert entries[0].audio_error == "kein Schnipsel erzeugt"


def test_emit_snippets_and_review_without_snippet_dir(tmp_path: Path) -> None:
    class _FakeDocument:
        candidates = (_FakeCandidate(),)

    class _FakeArgs:
        source = str(tmp_path / "in.mp4")
        snippet_dir = None
        emit_review = str(tmp_path / "review.html")

    cli_module._emit_snippets_and_review(_FakeArgs(), _FakeDocument())
    assert Path(_FakeArgs.emit_review).is_file()


def test_snippet_dir_with_transcript_mode_is_a_parser_error(tmp_path: Path) -> None:
    transcript_path = tmp_path / "t.json"
    transcript_path.write_text(
        json.dumps(
            {
                "artifact_type": "matrix_auto_cutter_repeat_transcript",
                "schema_version": "1.0",
                "audio_stream_specifier": "0:a:0",
                "source_duration_ms": 5_000,
                "segments": [],
            }
        ),
        encoding="utf-8",
    )
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
