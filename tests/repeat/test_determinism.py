"""End-to-end determinism: two runs on the same input produce identical bytes."""

from __future__ import annotations

import json
from pathlib import Path

from tests.repeat.conftest import transcript_dict, utterance_segment

from matrix_auto_cutter.repeat.diagnostics import build_diagnostics, write_diagnostics
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument


def test_two_runs_produce_identical_bytes(tmp_path: Path) -> None:
    raw = transcript_dict(
        [
            utterance_segment("ich habe drei aepfel gekauft", 0),
            utterance_segment("nein ich habe vier aepfel gekauft", 1_800),
            utterance_segment("und das war alles fuer heute", 4_500),
        ],
        source_duration_ms=10_000,
    )
    transcript = RepeatTranscriptDocument.model_validate_json(json.dumps(raw))
    first_target = tmp_path / "first" / "diagnostics.json"
    second_target = tmp_path / "second" / "diagnostics.json"
    write_diagnostics(first_target, build_diagnostics(transcript))
    write_diagnostics(second_target, build_diagnostics(transcript))
    assert first_target.read_bytes() == second_target.read_bytes()
