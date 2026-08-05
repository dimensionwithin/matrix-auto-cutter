"""Isolated adjacent-repeat and self-correction detection package. No ASR, no model, no audio."""

from __future__ import annotations

from matrix_auto_cutter.repeat.asr import (
    DEFAULT_THREADS,
    WhisperRunResult,
    build_whisper_argv,
    default_timeout_ms,
    run_whisper,
    whisper_json_path,
)
from matrix_auto_cutter.repeat.audio import (
    build_ffmpeg_argv,
    build_ffprobe_argv,
    extract_audio,
    probe_duration_ms,
)
from matrix_auto_cutter.repeat.boundary import (
    BoundaryCandidate,
    BoundaryDetectionParams,
    BoundaryDetectionResult,
    detect_boundary_echoes,
)
from matrix_auto_cutter.repeat.detect import (
    DetectionParams,
    DetectionResult,
    RepeatCandidate,
    UtteranceSpan,
    detect_repeats,
)
from matrix_auto_cutter.repeat.diagnostics import (
    AnyDiagnosticsDocument,
    DiagnosticsWriteResult,
    RepeatCandidateV1_1,
    RepeatCandidateV1_2,
    RepeatDiagnosticsDocument,
    RepeatDiagnosticsDocumentV1_1,
    RepeatDiagnosticsDocumentV1_2,
    build_diagnostics,
    write_diagnostics,
)
from matrix_auto_cutter.repeat.errors import (
    BinaryNotFoundError,
    FfmpegError,
    FfprobeError,
    ModelNotFoundError,
    ProcessTimeoutError,
    RawOutputEmptyError,
    RawOutputMissingError,
    RepeatContractError,
    SourceNotFoundError,
    WhisperError,
)
from matrix_auto_cutter.repeat.process import (
    NativeProcessRunner,
    ProcessResult,
    ProcessRunner,
    run_process,
)
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
from matrix_auto_cutter.repeat.whisper_json import convert_whisper_output

__all__ = [
    "DEFAULT_THREADS",
    "AnyDiagnosticsDocument",
    "BinaryNotFoundError",
    "BoundaryCandidate",
    "BoundaryDetectionParams",
    "BoundaryDetectionResult",
    "DetectionParams",
    "DetectionResult",
    "DiagnosticsWriteResult",
    "FfmpegError",
    "FfprobeError",
    "ModelNotFoundError",
    "NativeProcessRunner",
    "ProcessResult",
    "ProcessRunner",
    "ProcessTimeoutError",
    "RawOutputEmptyError",
    "RawOutputMissingError",
    "RepeatCandidate",
    "RepeatCandidateV1_1",
    "RepeatCandidateV1_2",
    "RepeatContractError",
    "RepeatDiagnosticsDocument",
    "RepeatDiagnosticsDocumentV1_1",
    "RepeatDiagnosticsDocumentV1_2",
    "RepeatSegment",
    "RepeatTranscriptDocument",
    "RepeatWord",
    "SimilarityParams",
    "SimilarityScore",
    "SourceNotFoundError",
    "Utterance",
    "UtteranceParams",
    "UtteranceSpan",
    "WhisperError",
    "WhisperRunResult",
    "WordDiffOp",
    "build_diagnostics",
    "build_ffmpeg_argv",
    "build_ffprobe_argv",
    "build_utterances",
    "build_whisper_argv",
    "compute_similarity",
    "convert_whisper_output",
    "default_timeout_ms",
    "detect_boundary_echoes",
    "detect_repeats",
    "extract_audio",
    "load_transcript",
    "normalize_text",
    "probe_duration_ms",
    "run_process",
    "run_whisper",
    "whisper_json_path",
    "write_diagnostics",
]
