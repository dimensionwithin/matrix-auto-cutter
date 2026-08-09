"""Atomic export of the repeat_diagnostics/1.0 and repeat_diagnostics/1.1 output contracts."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from matrix_auto_cutter.atomic import replace_atomically
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
DIAGNOSTICS_SCHEMA_VERSION_1_2: Literal["1.2"] = "1.2"

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


class RepeatCandidateV1_2(CanonicalModel):
    """One diagnosed pair, tagged by every detector that found it.

    When both detectors report the identical passage pair (both spans'
    ``start_ms``/``end_ms`` match exactly), it is one candidate here with
    ``detector=("utterance", "boundary")`` and both ``utterance_score`` and
    ``boundary_score`` set, rather than the two separate 1.1 entries. The two
    scores are still never merged or blended into a single number -- they
    are different measurements over different material (whole utterance vs.
    short boundary window) and stay in separate fields side by side.
    """

    detector: tuple[Literal["utterance", "boundary"], ...] = Field(min_length=1)
    first: UtteranceSpan
    second: UtteranceSpan
    gap_ms: int = Field(ge=0)
    reasons: tuple[str, ...]
    utterance_score: SimilarityScore | None = None
    boundary_score: float | None = Field(default=None, ge=0.0, le=1.0)
    window_words: int | None = Field(default=None, ge=1)
    first_window_text: str | None = None
    second_window_text: str | None = None

    @model_validator(mode="after")
    def _at_least_one_score(self) -> RepeatCandidateV1_2:
        if self.utterance_score is None and self.boundary_score is None:
            msg = "Mindestens einer von utterance_score/boundary_score muss gesetzt sein."
            raise ValueError(msg)
        return self


class RepeatDiagnosticsDocumentV1_2(CanonicalModel):
    """Kanonisches Ausgabeartefakt ``repeat_diagnostics/1.2``. Keine Schnitt-Entscheidung.

    Replaces ``RepeatDiagnosticsDocumentV1_1`` as what ``build_diagnostics``
    writes when the boundary detector is active: ``detector`` becomes a
    nonempty list instead of a single value, so a pair both detectors agree
    on collapses into one candidate instead of two. 1.1 documents remain
    readable via ``RepeatDiagnosticsDocumentV1_1`` -- this type is additive,
    not a replacement of that model.
    """

    artifact_type: Literal["matrix_auto_cutter_repeat_diagnostics"] = DIAGNOSTICS_ARTIFACT_TYPE
    schema_version: Literal["1.2"] = DIAGNOSTICS_SCHEMA_VERSION_1_2
    parameters: DetectionParams
    boundary_parameters: BoundaryDetectionParams
    total_pairs_checked: int = Field(ge=0)
    boundary_total_pairs_checked: int = Field(ge=0)
    candidates: tuple[RepeatCandidateV1_2, ...]


AnyDiagnosticsDocument = (
    RepeatDiagnosticsDocument | RepeatDiagnosticsDocumentV1_1 | RepeatDiagnosticsDocumentV1_2
)


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
    is ``None``. Writes ``repeat_diagnostics/1.2`` when it is given: both
    detectors run over the same utterances -- the whole-utterance detector
    builds them from ``params.utterance_params`` internally, and the
    boundary detector here is explicitly given the same
    ``params.utterance_params``. When both detectors report the identical
    passage pair (both spans' ``start_ms``/``end_ms`` match exactly), it
    becomes one candidate with ``detector=("utterance", "boundary")`` and
    both scores set; otherwise each candidate keeps its single-element
    ``detector`` list, exactly as in 1.1. Scores are never merged or
    score-blended, only reported side by side.
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
    boundary_by_pair = {
        (
            candidate.first.start_ms,
            candidate.first.end_ms,
            candidate.second.start_ms,
            candidate.second.end_ms,
        ): candidate
        for candidate in boundary_result.candidates
    }
    matched_boundary_pairs: set[tuple[int, int, int, int]] = set()
    candidates: list[RepeatCandidateV1_2] = []
    for utterance_candidate in utterance_result.candidates:
        pair_key = (
            utterance_candidate.first.start_ms,
            utterance_candidate.first.end_ms,
            utterance_candidate.second.start_ms,
            utterance_candidate.second.end_ms,
        )
        boundary_candidate = boundary_by_pair.get(pair_key)
        if boundary_candidate is None:
            candidates.append(
                RepeatCandidateV1_2(
                    detector=("utterance",),
                    first=utterance_candidate.first,
                    second=utterance_candidate.second,
                    gap_ms=utterance_candidate.gap_ms,
                    reasons=utterance_candidate.reasons,
                    utterance_score=utterance_candidate.scores,
                )
            )
            continue
        matched_boundary_pairs.add(pair_key)
        candidates.append(
            RepeatCandidateV1_2(
                detector=("utterance", "boundary"),
                first=utterance_candidate.first,
                second=utterance_candidate.second,
                gap_ms=utterance_candidate.gap_ms,
                reasons=tuple(
                    dict.fromkeys((*utterance_candidate.reasons, *boundary_candidate.reasons))
                ),
                utterance_score=utterance_candidate.scores,
                boundary_score=boundary_candidate.score,
                window_words=boundary_candidate.window_words,
                first_window_text=boundary_candidate.first_window_text,
                second_window_text=boundary_candidate.second_window_text,
            )
        )
    for pair_key, boundary_candidate in boundary_by_pair.items():
        if pair_key in matched_boundary_pairs:
            continue
        candidates.append(
            RepeatCandidateV1_2(
                detector=("boundary",),
                first=boundary_candidate.first,
                second=boundary_candidate.second,
                gap_ms=boundary_candidate.gap_ms,
                reasons=boundary_candidate.reasons,
                boundary_score=boundary_candidate.score,
                window_words=boundary_candidate.window_words,
                first_window_text=boundary_candidate.first_window_text,
                second_window_text=boundary_candidate.second_window_text,
            )
        )
    ordered = tuple(
        sorted(
            candidates,
            key=lambda candidate: (candidate.first.start_ms, candidate.second.start_ms),
        )
    )
    return RepeatDiagnosticsDocumentV1_2(
        parameters=active_params,
        boundary_parameters=boundary_params,
        total_pairs_checked=utterance_result.total_pairs_checked,
        boundary_total_pairs_checked=boundary_result.total_pairs_checked,
        candidates=ordered,
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
        replace_atomically(temporary, target)
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
