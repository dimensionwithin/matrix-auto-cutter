"""Atomic export of the repeat_diagnostics/1.0 output contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tests.repeat.conftest import transcript_dict, utterance_segment

from matrix_auto_cutter.repeat.detect import DetectionParams
from matrix_auto_cutter.repeat.diagnostics import build_diagnostics, write_diagnostics
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument


def _document() -> RepeatTranscriptDocument:
    raw = transcript_dict(
        [
            utterance_segment("ich gehe jetzt nach hause", 0),
            utterance_segment("ich gehe jetzt nach hause", 2_000),
        ],
        source_duration_ms=5_000,
    )
    return RepeatTranscriptDocument.model_validate_json(json.dumps(raw))


def test_build_diagnostics_reports_parameters_and_candidates() -> None:
    document = build_diagnostics(_document())
    assert document.artifact_type == "matrix_auto_cutter_repeat_diagnostics"
    assert document.schema_version == "1.0"
    assert document.total_pairs_checked == 1
    assert len(document.candidates) == 1
    assert document.parameters == DetectionParams()


def test_build_diagnostics_default_params_match_explicit_default() -> None:
    assert build_diagnostics(_document(), None) == build_diagnostics(_document())


def test_write_diagnostics_is_atomic_and_deterministic(tmp_path: Path) -> None:
    document = build_diagnostics(_document())
    target = tmp_path / "repeat-diagnostics.json"
    first = write_diagnostics(target, document)
    assert first.status == "written" and first.error is None
    first_bytes = target.read_bytes()
    assert not first_bytes.startswith(b"\xef\xbb\xbf")
    parsed = json.loads(first_bytes)
    assert parsed["artifact_type"] == "matrix_auto_cutter_repeat_diagnostics"
    second = write_diagnostics(target, document)
    assert second.status == "written"
    assert target.read_bytes() == first_bytes
    assert not list(tmp_path.glob("*.tmp.*"))


def test_write_diagnostics_rejects_forbidden_output_names(tmp_path: Path) -> None:
    document = build_diagnostics(_document())
    for name in ("cut-proposal.json", "selection.json", "approval.json"):
        result = write_diagnostics(tmp_path / name, document)
        assert result.status == "failed"
        assert result.error == "invalid_output_target"
        assert not (tmp_path / name).exists()


def test_write_diagnostics_rejects_parent_traversal(tmp_path: Path) -> None:
    document = build_diagnostics(_document())
    result = write_diagnostics(tmp_path / ".." / "escape.json", document)
    assert result.status == "failed"
    assert result.error == "invalid_output_target"


def test_write_diagnostics_reports_structured_failure_on_unwritable_parent(
    tmp_path: Path,
) -> None:
    blocking_file = tmp_path / "blocked"
    blocking_file.write_text("not a directory", encoding="utf-8")
    target = blocking_file / "nested" / "repeat-diagnostics.json"
    document = build_diagnostics(_document())
    result = write_diagnostics(target, document)
    assert result.status == "failed"
    assert result.error is not None
    assert not target.exists()


def test_write_diagnostics_replace_failure_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "repeat-diagnostics.json"
    document = build_diagnostics(_document())

    def fail_replace(_source: object, _target: object) -> None:
        raise OSError("simulierter Replace-Fehler")

    monkeypatch.setattr(os, "replace", fail_replace)
    result = write_diagnostics(target, document)
    assert result.status == "failed"
    assert not target.exists()
    assert not list(tmp_path.iterdir())
