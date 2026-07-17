"""Persistent package-2A artifact models and canonical bytes."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from matrix_auto_cutter.models import CanonicalModel

MAX_PROJECT_BYTES = 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 1024 * 1024

_UUID4_PATTERN = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
CanonicalUuidText = Annotated[str, Field(pattern=_UUID4_PATTERN)]


def is_canonical_uuid4(value: str) -> bool:
    """Return whether *value* is canonical lower-case UUIDv4 text."""
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and str(parsed) == value


class UnavailableIdentity(CanonicalModel):
    """Explicit absence marker for platform identity evidence."""

    availability: Literal["not_available"] = "not_available"


class AvailableIdentity(CanonicalModel):
    """Explicit available identity evidence."""

    availability: Literal["available"] = "available"
    scheme: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=256)


IdentityEvidence = AvailableIdentity | UnavailableIdentity


class WorkspaceRootBinding(CanonicalModel):
    """Canonical and handle-derived workspace-root binding."""

    canonical_dos_path: str = Field(min_length=3, max_length=32767)
    volume_identity: IdentityEvidence
    root_file_id: IdentityEvidence


class ProjectDocument(CanonicalModel):
    """Canonical ``project.json`` schema 1.0."""

    artifact_type: Literal["matrix_project"] = "matrix_project"
    schema_version: Literal["1.0"] = "1.0"
    project_id: CanonicalUuidText
    workspace_root_binding: WorkspaceRootBinding
    revision: int = Field(ge=0)


class LockDiagnostic(CanonicalModel):
    """Non-authoritative lock diagnostic schema 1.0."""

    artifact_type: Literal["lock_diagnostic"] = "lock_diagnostic"
    schema_version: Literal["1.0"] = "1.0"
    run_id: CanonicalUuidText
    project_id: CanonicalUuidText | UnavailableIdentity
    process_id: int = Field(ge=0)
    process_start_time_100ns: int = Field(ge=0)
    lock_kind: Literal["project", "path", "target"]
    redacted_key: str = Field(min_length=1, max_length=128)
    attempted_at_100ns: int = Field(ge=0)
    status: Literal["attempting", "acquired", "busy", "failed", "released"]


def canonical_bytes(model: CanonicalModel) -> bytes:
    """Serialize canonical UTF-8 without BOM and with a final LF."""
    return (model.model_dump_json() + "\n").encode("utf-8")


def parse_project_bytes(data: bytes) -> ProjectDocument:
    """Strictly parse bounded canonical project metadata."""
    if len(data) > MAX_PROJECT_BYTES:
        raise ValueError("project metadata exceeds size limit")
    if data.startswith(b"\xef\xbb\xbf") or not data.endswith(b"\n"):
        raise ValueError("project metadata is not canonical UTF-8")
    text = data.decode("utf-8", errors="strict")
    model = ProjectDocument.model_validate_json(text)
    if canonical_bytes(model) != data:
        raise ValueError("project metadata bytes are not canonical")
    return model
