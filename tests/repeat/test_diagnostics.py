"""Atomic export of the repeat_diagnostics/1.0 output contract."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tests.repeat.conftest import transcript_dict, utterance_segment

from matrix_auto_cutter.repeat.boundary import BoundaryDetectionParams
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


def test_v1_0_document_forbids_the_detector_field() -> None:
    document = build_diagnostics(_document())
    dumped = json.loads(document.model_dump_json())
    assert dumped["schema_version"] == "1.0"
    assert "detector" not in dumped["candidates"][0]


def test_v1_2_document_requires_a_nonempty_detector_list_on_every_candidate() -> None:
    document = build_diagnostics(_document(), DetectionParams(), BoundaryDetectionParams())
    dumped = json.loads(document.model_dump_json())
    assert dumped["schema_version"] == "1.2"
    assert len(dumped["candidates"]) >= 1
    for candidate in dumped["candidates"]:
        assert candidate["detector"]
        assert set(candidate["detector"]) <= {"utterance", "boundary"}


def test_both_detectors_reporting_the_same_pair_merge_into_one_candidate() -> None:
    document = build_diagnostics(_document(), DetectionParams(), BoundaryDetectionParams())
    assert document.schema_version == "1.2"
    assert len(document.candidates) == 1
    candidate = document.candidates[0]
    assert candidate.detector == ("utterance", "boundary")
    assert candidate.first.start_ms == 0
    assert candidate.second.start_ms == 2_000
    assert candidate.utterance_score is not None
    assert candidate.boundary_score is not None
    assert candidate.window_words is not None
    assert candidate.first_window_text is not None


def test_v1_1_document_is_still_readable() -> None:
    from matrix_auto_cutter.repeat.diagnostics import (
        RepeatCandidateV1_1,
        RepeatDiagnosticsDocumentV1_1,
    )

    raw = {
        "artifact_type": "matrix_auto_cutter_repeat_diagnostics",
        "schema_version": "1.1",
        "parameters": json.loads(DetectionParams().model_dump_json()),
        "boundary_parameters": json.loads(BoundaryDetectionParams().model_dump_json()),
        "total_pairs_checked": 1,
        "boundary_total_pairs_checked": 1,
        "candidates": [
            {
                "detector": "utterance",
                "first": {"text": "a", "start_ms": 0, "end_ms": 100},
                "second": {"text": "a", "start_ms": 200, "end_ms": 300},
                "gap_ms": 100,
                "reasons": ("score_above_threshold",),
            }
        ],
    }
    document = RepeatDiagnosticsDocumentV1_1.model_validate_json(json.dumps(raw))
    assert document.schema_version == "1.1"
    assert isinstance(document.candidates[0], RepeatCandidateV1_1)
    assert document.candidates[0].detector == "utterance"


def test_boundary_only_pair_is_reported_as_a_single_element_detector_list() -> None:
    document = build_diagnostics(
        _document(), DetectionParams(max_gap_ms=0), BoundaryDetectionParams()
    )
    assert document.schema_version == "1.2"
    assert document.total_pairs_checked == 0
    assert len(document.candidates) == 1
    candidate = document.candidates[0]
    assert candidate.detector == ("boundary",)
    assert candidate.utterance_score is None
    assert candidate.boundary_score is not None


def test_v1_2_candidate_requires_at_least_one_score() -> None:
    import pytest

    from matrix_auto_cutter.repeat.detect import UtteranceSpan
    from matrix_auto_cutter.repeat.diagnostics import RepeatCandidateV1_2

    span = UtteranceSpan(text="a", start_ms=0, end_ms=100)
    with pytest.raises(ValueError, match="utterance_score/boundary_score"):
        RepeatCandidateV1_2(
            detector=("utterance",),
            first=span,
            second=span,
            gap_ms=0,
            reasons=(),
        )


def test_boundary_disabled_returns_v1_0_unchanged() -> None:
    without_boundary = build_diagnostics(_document(), DetectionParams(), None)
    baseline = build_diagnostics(_document())
    assert without_boundary == baseline
    assert without_boundary.schema_version == "1.0"


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
