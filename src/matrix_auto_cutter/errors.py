"""Strukturierte, stabile Fehler des Vertragskerns."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    """Im ersten Coding-Auftrag öffentliche Fehlercodes."""

    JOURNAL_INCOMPLETE = "E_JOURNAL_INCOMPLETE"
    JOURNAL_SEQUENCE = "E_JOURNAL_SEQUENCE"
    JOURNAL_OUTPUT_FAILURE = "E_JOURNAL_OUTPUT_FAILURE"
    SIDECAR_ARTIFACT_TYPE = "E_SIDECAR_ARTIFACT_TYPE"
    SIDECAR_VERSION = "E_SIDECAR_VERSION"
    SIDECAR_NOT_FINALIZED = "E_SIDECAR_NOT_FINALIZED"
    SIDECAR_IDENTITY = "E_SIDECAR_IDENTITY"
    SIDECAR_CLOCK_UNRELIABLE = "E_SIDECAR_CLOCK_UNRELIABLE"
    SIDECAR_PAUSE_SEQUENCE = "E_SIDECAR_PAUSE_SEQUENCE"
    SIDECAR_POLICY = "E_SIDECAR_POLICY"
    SIDECAR_EVENT_PAIRS = "E_SIDECAR_EVENT_PAIRS"
    SIDECAR_OUTPUT = "E_SIDECAR_OUTPUT"


_USER_TEXT: dict[ErrorCode, str] = {
    ErrorCode.JOURNAL_INCOMPLETE: "Das Aufnahmejournal ist unvollständig.",
    ErrorCode.JOURNAL_SEQUENCE: "Die Reihenfolge des Aufnahmejournals ist ungültig.",
    ErrorCode.JOURNAL_OUTPUT_FAILURE: "OBS hat die Aufnahme nicht erfolgreich beendet.",
    ErrorCode.SIDECAR_ARTIFACT_TYPE: "Die Datei ist kein finalisiertes OBS-Sidecar.",
    ErrorCode.SIDECAR_VERSION: "Die Sidecar-Version wird nicht unterstützt.",
    ErrorCode.SIDECAR_NOT_FINALIZED: "Das OBS-Sidecar ist nicht finalisiert.",
    ErrorCode.SIDECAR_IDENTITY: "Das OBS-Sidecar gehört nicht zur ausgewählten Quelldatei.",
    ErrorCode.SIDECAR_CLOCK_UNRELIABLE: "Die Zeitbasis des OBS-Sidecars ist unzuverlässig.",
    ErrorCode.SIDECAR_PAUSE_SEQUENCE: "Die Pause-/Fortsetzen-Folge ist inkonsistent.",
    ErrorCode.SIDECAR_POLICY: "Eine Schutzrichtlinie des OBS-Sidecars ist ungültig.",
    ErrorCode.SIDECAR_EVENT_PAIRS: "Schutzereignisse lassen sich nicht eindeutig paaren.",
    ErrorCode.SIDECAR_OUTPUT: "Die JSON-Ausgabe konnte nicht atomar geschrieben werden.",
}


class CoreError(BaseModel):
    """Fehlerwert statt erwartbarer roher Exception an öffentlichen Grenzen."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: ErrorCode
    user_text_de: str
    technical_context: dict[str, object]
    artifact_id: str | None = None
    retryable: bool = False


def core_error(
    code: ErrorCode,
    context: dict[str, object],
    *,
    artifact_id: str | None = None,
    retryable: bool = False,
) -> CoreError:
    """Erzeuge einen vollständig strukturierten Fehler."""
    return CoreError(
        code=code,
        user_text_de=_USER_TEXT[code],
        technical_context=context,
        artifact_id=artifact_id,
        retryable=retryable,
    )
