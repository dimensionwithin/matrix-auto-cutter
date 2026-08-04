"""Strict validation of the repeat_transcript/1.0 input contract."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.repeat.errors import RepeatContractError

TRANSCRIPT_ARTIFACT_TYPE: Literal["matrix_auto_cutter_repeat_transcript"] = (
    "matrix_auto_cutter_repeat_transcript"
)
TRANSCRIPT_SCHEMA_VERSION: Literal["1.0"] = "1.0"


class RepeatWord(CanonicalModel):
    """Single validated word timing inside a repeat transcript segment."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)
    probability: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> RepeatWord:
        """Reject a word whose interval is not strictly ascending."""
        if self.start_ms >= self.end_ms:
            msg = "Wortintervalle benötigen start_ms < end_ms."
            raise ValueError(msg)
        return self


class RepeatSegment(CanonicalModel):
    """Single validated transcript segment with monotonic, contained word timings."""

    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    words: tuple[RepeatWord, ...]

    @model_validator(mode="after")
    def _ordered(self) -> RepeatSegment:
        """Reject inverted segments and words that fall outside or overlap."""
        if self.start_ms >= self.end_ms:
            msg = "Segmentintervalle benötigen start_ms < end_ms."
            raise ValueError(msg)
        previous_end: int | None = None
        for word in self.words:
            if word.start_ms < self.start_ms or word.end_ms > self.end_ms:
                msg = "Wörter müssen innerhalb ihres Segments liegen."
                raise ValueError(msg)
            if previous_end is not None and word.start_ms < previous_end:
                msg = "Wortzeiten innerhalb eines Segments müssen monoton sein."
                raise ValueError(msg)
            previous_end = word.end_ms
        return self


class RepeatTranscriptDocument(CanonicalModel):
    """Kanonisches Eingabeartefakt ``repeat_transcript/1.0``."""

    artifact_type: Literal["matrix_auto_cutter_repeat_transcript"]
    schema_version: Literal["1.0"]
    audio_stream_specifier: str = Field(min_length=1)
    source_duration_ms: int = Field(gt=0)
    segments: tuple[RepeatSegment, ...]

    @model_validator(mode="after")
    def _ordered(self) -> RepeatTranscriptDocument:
        """Reject segments that exceed the source duration or overlap each other."""
        previous_end: int | None = None
        for segment in self.segments:
            if segment.end_ms > self.source_duration_ms:
                msg = "Segmente dürfen die Quelldauer nicht überschreiten."
                raise ValueError(msg)
            if previous_end is not None and segment.start_ms < previous_end:
                msg = "Segmente müssen zeitlich monoton und überlappungsfrei sein."
                raise ValueError(msg)
            previous_end = segment.end_ms
        return self


def load_transcript(path: str | Path) -> RepeatTranscriptDocument:
    """Read and strictly validate a ``repeat_transcript/1.0`` document from disk."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        msg = f"Transkript konnte nicht gelesen werden: {exc}"
        raise RepeatContractError(msg) from exc
    try:
        return RepeatTranscriptDocument.model_validate_json(raw)
    except ValidationError as exc:
        msg = f"Transkript verletzt den Vertrag repeat_transcript/1.0: {exc}"
        raise RepeatContractError(msg) from exc
