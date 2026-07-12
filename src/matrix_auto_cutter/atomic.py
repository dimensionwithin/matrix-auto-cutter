"""Atomarer, deterministischer Export von ``protection-ranges.json``."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import Field

from matrix_auto_cutter.errors import CoreError, ErrorCode, core_error
from matrix_auto_cutter.models import (
    CanonicalModel,
    FrameRateModel,
    MaterializedFrameRange,
    Sha256,
)


class ProtectionRangesDocument(CanonicalModel):
    """Kanonisches Consumer-Artefakt des ersten Coding-Auftrags."""

    schema_version: Literal["1.0"] = "1.0"
    source_sha256: Sha256
    input_hash: Sha256
    configuration_hash: Sha256
    sidecar_schema_version: Literal["1.1"] = "1.1"
    time_base: FrameRateModel = Field(default_factory=FrameRateModel)
    ranges: tuple[MaterializedFrameRange, ...]


class AtomicWriteResult(CanonicalModel):
    """Strukturiertes IO-Ergebnis ohne erwartbare rohe Exception."""

    status: Literal["written", "failed"]
    output_path: str
    error: CoreError | None = None


def _deterministic_json(document: ProtectionRangesDocument) -> bytes:
    return (document.model_dump_json(indent=2) + "\n").encode("utf-8")


def write_protection_ranges(
    output_path: str | Path,
    document: ProtectionRangesDocument,
) -> AtomicWriteResult:
    """Flushe im Zielverzeichnis und ersetze anschließend atomar."""
    target = Path(output_path)
    if target.name != "protection-ranges.json" or ".." in target.parts:
        return AtomicWriteResult(
            status="failed",
            output_path=str(target),
            error=core_error(
                ErrorCode.SIDECAR_OUTPUT,
                {"path": str(target), "reason": "invalid_output_target"},
            ),
        )
    temporary: Path | None = None
    primary_error: OSError | None = None
    cleanup_error: OSError | RuntimeError | None = None
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
    except OSError as exc:
        primary_error = exc
    finally:
        if temporary is not None:
            active_error = sys.exception()
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, RuntimeError) as exc:
                cause = active_error or primary_error
                if cause is None:
                    raise
                cause.add_note(f"Sekundärer Tempdatei-Cleanupfehler: {exc}")
                cleanup_error = exc
    if primary_error is not None:
        context: dict[str, object] = {"path": str(target), "detail": str(primary_error)}
        if cleanup_error is not None:
            context["cleanup_detail"] = str(cleanup_error)
        return AtomicWriteResult(
            status="failed",
            output_path=str(target),
            error=core_error(
                ErrorCode.SIDECAR_OUTPUT,
                context,
                retryable=True,
            ),
        )
    return AtomicWriteResult(status="written", output_path=str(target))
