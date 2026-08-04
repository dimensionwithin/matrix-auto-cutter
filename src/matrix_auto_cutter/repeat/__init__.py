"""Isolated adjacent-repeat and self-correction detection package. No ASR, no model, no audio."""

from __future__ import annotations

from matrix_auto_cutter.repeat.detect import (
    DetectionParams,
    DetectionResult,
    RepeatCandidate,
    UtteranceSpan,
    detect_repeats,
)
from matrix_auto_cutter.repeat.diagnostics import (
    DiagnosticsWriteResult,
    RepeatDiagnosticsDocument,
    build_diagnostics,
    write_diagnostics,
)
from matrix_auto_cutter.repeat.errors import RepeatContractError
from matrix_auto_cutter.repeat.similarity import (
    SimilarityParams,
    SimilarityScore,
    WordDiffOp,
    compute_similarity,
    normalize_text,
)
from matrix_auto_cutter.repeat.transcript import (
    RepeatSegment,
    RepeatTranscriptDocument,
    RepeatWord,
    load_transcript,
)
from matrix_auto_cutter.repeat.utterances import Utterance, UtteranceParams, build_utterances

__all__ = [
    "DetectionParams",
    "DetectionResult",
    "DiagnosticsWriteResult",
    "RepeatCandidate",
    "RepeatContractError",
    "RepeatDiagnosticsDocument",
    "RepeatSegment",
    "RepeatTranscriptDocument",
    "RepeatWord",
    "SimilarityParams",
    "SimilarityScore",
    "Utterance",
    "UtteranceParams",
    "UtteranceSpan",
    "WordDiffOp",
    "build_diagnostics",
    "build_utterances",
    "compute_similarity",
    "detect_repeats",
    "load_transcript",
    "normalize_text",
    "write_diagnostics",
]
