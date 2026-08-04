"""Atomic export of the repeat_diagnostics/1.0 output contract."""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.detect import DetectionParams, RepeatCandidate, detect_repeats
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument

DIAGNOSTICS_ARTIFACT_TYPE: Literal["matrix_auto_cutter_repeat_diagnostics"] = (
    "matrix_auto_cutter_repeat_diagnostics"
)
DIAGNOSTICS_SCHEMA_VERSION: Literal["1.0"] = "1.0"

_FORBIDDEN_OUTPUT_NAMES = frozenset({"cut-proposal.json", "selection.json", "approval.json"})


class RepeatDiagnosticsDocument(CanonicalModel):
    """Kanonisches Ausgabeartefakt ``repeat_diagnostics/1.0``. Keine Schnitt-Entscheidung."""

    artifact_type: Literal["matrix_auto_cutter_repeat_diagnostics"] = DIAGNOSTICS_ARTIFACT_TYPE
    schema_version: Literal["1.0"] = DIAGNOSTICS_SCHEMA_VERSION
    parameters: DetectionParams
    total_pairs_checked: int = Field(ge=0)
    candidates: tuple[RepeatCandidate, ...]


class DiagnosticsWriteResult(CanonicalModel):
    """Structured IO result of writing diagnostics without a raw expected exception."""

    status: Literal["written", "failed"]
    output_path: str
    error: str | None = None


def build_diagnostics(
    transcript: RepeatTranscriptDocument,
    params: DetectionParams | None = None,
) -> RepeatDiagnosticsDocument:
    """Run detection and assemble the deterministic diagnostics document."""
    active_params = params if params is not None else DetectionParams()
    result = detect_repeats(transcript, active_params)
    return RepeatDiagnosticsDocument(
        parameters=active_params,
        total_pairs_checked=result.total_pairs_checked,
        candidates=result.candidates,
    )


def _deterministic_json(document: RepeatDiagnosticsDocument) -> bytes:
    return (document.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_diagnostics(
    output_path: str | Path,
    document: RepeatDiagnosticsDocument,
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
