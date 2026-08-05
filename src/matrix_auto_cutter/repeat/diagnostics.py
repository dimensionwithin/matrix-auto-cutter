"""Atomic export of the repeat_diagnostics/1.0 and repeat_diagnostics/1.1 output contracts."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.boundary import (
    BoundaryDetectionParams,
    detect_boundary_echoes,
)
from matrix_auto_cutter.repeat.detect import (
    DetectionParams,
    RepeatCandidate,
    UtteranceSpan,
    detect_repeats,
)
from matrix_auto_cutter.repeat.similarity import SimilarityScore
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument

DIAGNOSTICS_ARTIFACT_TYPE: Literal["matrix_auto_cutter_repeat_diagnostics"] = (
    "matrix_auto_cutter_repeat_diagnostics"
)
DIAGNOSTICS_SCHEMA_VERSION: Literal["1.0"] = "1.0"
DIAGNOSTICS_SCHEMA_VERSION_1_1: Literal["1.1"] = "1.1"

_FORBIDDEN_OUTPUT_NAMES = frozenset({"cut-proposal.json", "selection.json", "approval.json"})


class RepeatDiagnosticsDocument(CanonicalModel):
    """Kanonisches Ausgabeartefakt ``repeat_diagnostics/1.0``. Keine Schnitt-Entscheidung."""

    artifact_type: Literal["matrix_auto_cutter_repeat_diagnostics"] = DIAGNOSTICS_ARTIFACT_TYPE
    schema_version: Literal["1.0"] = DIAGNOSTICS_SCHEMA_VERSION
    parameters: DetectionParams
    total_pairs_checked: int = Field(ge=0)
    candidates: tuple[RepeatCandidate, ...]


class RepeatCandidateV1_1(CanonicalModel):
    """One diagnosed pair from either detector, tagged by which one found it.

    ``scores`` is populated for ``detector="utterance"`` candidates, exactly
    as in ``repeat_diagnostics/1.0``. ``boundary_score``, ``window_words``,
    ``first_window_text``, and ``second_window_text`` are populated for
    ``detector="boundary"`` candidates. The two detectors' scores are never
    merged or compared against each other -- they are different
    measurements over different material (whole utterance vs. short
    boundary window) and stay in separate fields.
    """

    detector: Literal["utterance", "boundary"]
    first: UtteranceSpan
    second: UtteranceSpan
    gap_ms: int = Field(ge=0)
    reasons: tuple[str, ...]
    scores: SimilarityScore | None = None
    boundary_score: float | None = Field(default=None, ge=0.0, le=1.0)
    window_words: int | None = Field(default=None, ge=1)
    first_window_text: str | None = None
    second_window_text: str | None = None


class RepeatDiagnosticsDocumentV1_1(CanonicalModel):
    """Kanonisches Ausgabeartefakt ``repeat_diagnostics/1.1``. Keine Schnitt-Entscheidung.

    Adds the mandatory ``detector`` field (forbidden in 1.0) so a reader can
    tell which detector produced each candidate. Written only when the
    boundary detector is active; otherwise ``RepeatDiagnosticsDocument``
    (1.0) is written unchanged, exactly as before this detector existed.
    """

    artifact_type: Literal["matrix_auto_cutter_repeat_diagnostics"] = DIAGNOSTICS_ARTIFACT_TYPE
    schema_version: Literal["1.1"] = DIAGNOSTICS_SCHEMA_VERSION_1_1
    parameters: DetectionParams
    boundary_parameters: BoundaryDetectionParams
    total_pairs_checked: int = Field(ge=0)
    boundary_total_pairs_checked: int = Field(ge=0)
    candidates: tuple[RepeatCandidateV1_1, ...]


AnyDiagnosticsDocument = RepeatDiagnosticsDocument | RepeatDiagnosticsDocumentV1_1


class DiagnosticsWriteResult(CanonicalModel):
    """Structured IO result of writing diagnostics without a raw expected exception."""

    status: Literal["written", "failed"]
    output_path: str
    error: str | None = None


def build_diagnostics(
    transcript: RepeatTranscriptDocument,
    params: DetectionParams | None = None,
    boundary_params: BoundaryDetectionParams | None = None,
) -> AnyDiagnosticsDocument:
    """Run detection and assemble the deterministic diagnostics document.

    Writes ``repeat_diagnostics/1.0`` (unchanged) when ``boundary_params``
    is ``None``. Writes ``repeat_diagnostics/1.1`` when it is given: both
    detectors run over the same utterances -- the whole-utterance detector
    builds them from ``params.utterance_params`` internally, and the
    boundary detector here is explicitly given the same
    ``params.utterance_params`` -- and their candidates appear as separate
    entries, never merged or score-blended.
    """
    active_params = params if params is not None else DetectionParams()
    utterance_result = detect_repeats(transcript, active_params)
    if boundary_params is None:
        return RepeatDiagnosticsDocument(
            parameters=active_params,
            total_pairs_checked=utterance_result.total_pairs_checked,
            candidates=utterance_result.candidates,
        )
    boundary_result = detect_boundary_echoes(
        transcript, boundary_params, utterance_params=active_params.utterance_params
    )
    candidates = tuple(
        RepeatCandidateV1_1(
            detector="utterance",
            first=candidate.first,
            second=candidate.second,
            gap_ms=candidate.gap_ms,
            reasons=candidate.reasons,
            scores=candidate.scores,
        )
        for candidate in utterance_result.candidates
    ) + tuple(
        RepeatCandidateV1_1(
            detector="boundary",
            first=candidate.first,
            second=candidate.second,
            gap_ms=candidate.gap_ms,
            reasons=candidate.reasons,
            boundary_score=candidate.score,
            window_words=candidate.window_words,
            first_window_text=candidate.first_window_text,
            second_window_text=candidate.second_window_text,
        )
        for candidate in boundary_result.candidates
    )
    return RepeatDiagnosticsDocumentV1_1(
        parameters=active_params,
        boundary_parameters=boundary_params,
        total_pairs_checked=utterance_result.total_pairs_checked,
        boundary_total_pairs_checked=boundary_result.total_pairs_checked,
        candidates=candidates,
    )


def _deterministic_json(document: AnyDiagnosticsDocument) -> bytes:
    return (document.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_diagnostics(
    output_path: str | Path,
    document: AnyDiagnosticsDocument,
) -> DiagnosticsWriteResult:
    """Atomically write diagnostics; refuse proposal/selection/approval output paths."""
    target = Path(output_path)
    if target.name in _FORBIDDEN_OUTPUT_NAMES or ".." in target.parts:
        return DiagnosticsWriteResult(
            status="failed",
            output_path=str(target),
            error="invalid_output_target",
        )
    temporary: Path | None = None
    error: str | None = None
    try:
        target.parent.mkdir(parents=False, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.tmp.",
            dir=target.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(_deterministic_json(document))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        temporary = None
    except OSError as exc:
        error = str(exc)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
    if error is not None:
        return DiagnosticsWriteResult(status="failed", output_path=str(target), error=error)
    return DiagnosticsWriteResult(status="written", output_path=str(target))
