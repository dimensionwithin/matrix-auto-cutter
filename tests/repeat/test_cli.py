"""CLI entry point: exact exit codes, no interaction."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.repeat.conftest import segment, transcript_dict, utterance_segment, word

from matrix_auto_cutter.repeat.cli import main


def _write_transcript(path: Path) -> None:
    raw = transcript_dict(
        [
            utterance_segment("ich gehe jetzt nach hause", 0),
            utterance_segment("ich gehe jetzt nach hause", 2_000),
        ],
        source_duration_ms=5_000,
    )
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_cli_success_writes_diagnostics(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    out_path = tmp_path / "diagnostics.json"
    _write_transcript(transcript_path)
    exit_code = main(["--transcript", str(transcript_path), "--out", str(out_path)])
    assert exit_code == 0
    document = json.loads(out_path.read_text(encoding="utf-8"))
    assert document["artifact_type"] == "matrix_auto_cutter_repeat_diagnostics"
    assert len(document["candidates"]) == 1


def test_cli_missing_transcript_returns_nonzero(tmp_path: Path) -> None:
    out_path = tmp_path / "diagnostics.json"
    exit_code = main(["--transcript", str(tmp_path / "missing.json"), "--out", str(out_path)])
    assert exit_code != 0
    assert not out_path.exists()


def test_cli_contract_violation_returns_nonzero(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    out_path = tmp_path / "diagnostics.json"
    raw = transcript_dict([segment(0, 100, [word(0, 100, "Hi")])])
    raw["schema_version"] = "9.9"
    transcript_path.write_text(json.dumps(raw), encoding="utf-8")
    exit_code = main(["--transcript", str(transcript_path), "--out", str(out_path)])
    assert exit_code != 0
    assert not out_path.exists()


def test_cli_write_failure_returns_nonzero(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    _write_transcript(transcript_path)
    forbidden_out = tmp_path / "cut-proposal.json"
    exit_code = main(["--transcript", str(transcript_path), "--out", str(forbidden_out)])
    assert exit_code != 0
    assert not forbidden_out.exists()


def test_module_entry_point_runs_main_under_dunder_main(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    transcript_path = tmp_path / "transcript.json"
    out_path = tmp_path / "diagnostics.json"
    _write_transcript(transcript_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["prog", "--transcript", str(transcript_path), "--out", str(out_path)],
    )
    sys.modules.pop("matrix_auto_cutter.repeat.cli", None)
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("matrix_auto_cutter.repeat.cli", run_name="__main__")
    assert excinfo.value.code == 0
    assert out_path.exists()
