"""Strict local OBS outro binding and frame-exact tail resolution."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    model_serializer,
    model_validator,
)

from matrix_auto_cutter.event_lag import pipeline_lag_frames
from matrix_auto_cutter.models import CanonicalModel, CanonicalUuid4, Sha256
from matrix_auto_cutter.sidecar import ObsEventSidecarV12, ValidatedObsEventSidecar

OUTRO_BINDING_FILE_NAME = "outro-scene-binding.json"
MAX_BINDING_BYTES = 64 * 1024
_BINDING_DOMAIN = b"matrix-auto-cutter/outro-scene-binding/1.0\0"


class OutroSceneBindingContent(CanonicalModel):
    """Immutable identity of one explicitly configured OBS program scene."""

    artifact_type: Literal["matrix_auto_cutter_outro_scene_binding"]
    schema_version: Literal["1.0"]
    purpose: Literal["outro"]
    scene_collection_name: str = Field(min_length=1, max_length=200)
    scene_name: str = Field(min_length=1, max_length=500)
    scene_uuid: CanonicalUuid4
    expected_obs_major: Literal[32]
    expected_obs_product_version: Literal["32.1.2"]


class OutroSceneBinding(OutroSceneBindingContent):
    """Binding content plus its domain-separated canonical digest."""

    binding_digest: Sha256

    @model_validator(mode="after")
    def digest_matches(self) -> OutroSceneBinding:
        """Bind every identity field to the declared digest."""
        if self.binding_digest != binding_digest(_binding_content(self)):
            raise ValueError("outro binding digest mismatch")
        return self


class OutroResolutionEvidence(CanonicalModel):
    """Bounded typed result; no free-text scene matching is permitted."""

    status: Literal[
        "resolved",
        "binding_missing",
        "binding_invalid",
        "scene_collection_missing",
        "scene_collection_mismatch",
        "obs_version_mismatch",
        "sidecar_missing_scene_uuid",
        "no_matching_scene_event",
        "ambiguous_scene_events",
        "scene_name_mismatch",
        "event_out_of_bounds",
    ]
    binding_digest: Sha256 | None = None
    binding_file_sha256: Sha256 | None = None
    sidecar_sha256: Sha256
    scene_event_id: CanonicalUuid4 | None = None
    scene_uuid: CanonicalUuid4 | None = None
    scene_name: str | None = Field(default=None, max_length=500)
    outro_start_frame: int | None = Field(default=None, ge=0)
    protected_start_frame: int | None = Field(default=None, ge=0)
    protected_end_frame: int | None = Field(default=None, ge=0)
    tail_start_frame: int | None = Field(default=None, ge=0)
    total_source_frames: int = Field(ge=1)
    protection_length_frames: Literal[900] = 900
    # Wie beim Intro: der Betrag, um den die Journalmarke nach hinten korrigiert
    # wurde, damit im Proposal nachrechenbar bleibt, worauf sich die 900 Frames
    # beziehen.  Abwesend in Proposal-1.1-Bytes, gesetzt ab 1.2.
    pipeline_lag_frames: int | None = Field(default=None, ge=0)

    @model_serializer(mode="wrap")
    def omit_unavailable_fields(self, handler: SerializerFunctionWrapHandler) -> object:
        """Represent unavailable typed evidence by field absence, never JSON null."""
        serialized: dict[str, object] = handler(self)
        for name in (
            "binding_digest",
            "binding_file_sha256",
            "scene_event_id",
            "scene_uuid",
            "scene_name",
            "outro_start_frame",
            "protected_start_frame",
            "protected_end_frame",
            "tail_start_frame",
            "pipeline_lag_frames",
        ):
            if getattr(self, name) is None:
                serialized.pop(name, None)
        return serialized


class OutroCandidateEvidence(CanonicalModel):
    """Immutable evidence for the sole frame-exact excess-tail candidate."""

    binding_digest: Sha256
    binding_file_sha256: Sha256
    sidecar_sha256: Sha256
    scene_event_id: CanonicalUuid4
    scene_uuid: CanonicalUuid4
    scene_name: str = Field(min_length=1, max_length=500)
    outro_start_frame: int = Field(ge=0)
    protected_start_frame: int = Field(ge=0)
    protected_end_frame: int = Field(ge=0)
    tail_start_frame: int = Field(ge=0)
    total_source_frames: int = Field(ge=1)
    protection_length_frames: Literal[900] = 900
    resolution_status: Literal["resolved"] = "resolved"

    @model_validator(mode="after")
    def exact_frame_contract(self) -> OutroCandidateEvidence:
        """Require the exact half-open 900-frame arithmetic."""
        if (
            self.protected_start_frame != self.outro_start_frame
            or self.protected_end_frame
            != min(self.outro_start_frame + self.protection_length_frames, self.total_source_frames)
            or self.tail_start_frame != self.outro_start_frame + self.protection_length_frames
            or not self.tail_start_frame < self.total_source_frames
        ):
            raise ValueError("outro candidate frames violate the exact 900-frame contract")
        return self


def default_binding_path(local_app_data: Path | None = None) -> Path:
    """Return the sole local configuration path for an explicit binding."""
    root = local_app_data or Path(os.environ["LOCALAPPDATA"])
    return root / "DimensionWithin" / "MatrixAutoCutter" / "config" / OUTRO_BINDING_FILE_NAME


def _binding_content(binding: OutroSceneBinding) -> OutroSceneBindingContent:
    return OutroSceneBindingContent.model_validate_json(
        binding.model_dump_json(exclude={"binding_digest"})
    )


def binding_digest(content: OutroSceneBindingContent) -> str:
    """Hash canonical content with an outro-binding-specific domain."""
    return hashlib.sha256(_BINDING_DOMAIN + content.model_dump_json().encode("utf-8")).hexdigest()


def binding_from_content(content: OutroSceneBindingContent) -> OutroSceneBinding:
    """Attach the canonical digest to validated immutable content."""
    payload = json.loads(content.model_dump_json())
    payload["binding_digest"] = binding_digest(content)
    return OutroSceneBinding.model_validate(payload)


def binding_bytes(binding: OutroSceneBinding) -> bytes:
    """Return the only accepted on-disk binding representation."""
    return (binding.model_dump_json() + "\n").encode("utf-8")


def binding_file_sha256(binding: OutroSceneBinding) -> str:
    """Hash the exact canonical binding bytes used for proposal reuse."""
    return hashlib.sha256(binding_bytes(binding)).hexdigest()


def load_binding(path: Path) -> OutroSceneBinding | None:
    """Load only exact canonical bytes; missing/invalid bindings are unavailable."""
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_BINDING_BYTES:
            return None
        binding = OutroSceneBinding.model_validate_json(data)
        return binding if data == binding_bytes(binding) else None
    except (OSError, UnicodeError, ValidationError, ValueError):
        return None


def collection_matches(binding: OutroSceneBinding, collection_file: Path) -> bool:
    """Read the explicitly named OBS collection; do not consult global.ini or search."""
    try:
        raw = json.loads(collection_file.read_bytes())
        if not isinstance(raw, Mapping) or not isinstance(raw.get("sources"), list):
            return False
        uuid_matches: list[Mapping[str, object]] = []
        name_matches: list[Mapping[str, object]] = []
        for source in raw["sources"]:
            if not isinstance(source, Mapping):
                continue
            if source.get("uuid") == str(binding.scene_uuid):
                uuid_matches.append(source)
            if source.get("name") == binding.scene_name:
                name_matches.append(source)
        return (
            len(uuid_matches) == 1
            and len(name_matches) == 1
            and uuid_matches[0] is name_matches[0]
            and uuid_matches[0].get("id") == "scene"
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _resolution(payload: Mapping[str, object]) -> OutroResolutionEvidence:
    """Validate a typed resolution mapping while retaining bounded failure states."""
    return OutroResolutionEvidence.model_validate(payload)


def resolve_outro(
    sidecar: ValidatedObsEventSidecar,
    *,
    sidecar_sha256: str,
    binding_path: Path,
    collection_file: Path,
) -> OutroResolutionEvidence:
    """Resolve exactly one final, source-bound scene event or fail closed."""
    total = sidecar.source.video_frame_count
    lag = pipeline_lag_frames(sidecar)
    # Der Lag hängt allein am Journal und steht auch dann fest, wenn die Bindung
    # fehlt; er gehört deshalb in jede Rückgabe, nicht nur in die aufgelöste.
    unbound: dict[str, object] = {
        "sidecar_sha256": sidecar_sha256,
        "total_source_frames": total,
        "pipeline_lag_frames": lag,
    }
    if not isinstance(sidecar, ObsEventSidecarV12):
        return _resolution({"status": "sidecar_missing_scene_uuid", **unbound})
    binding = load_binding(binding_path)
    if binding is None:
        return _resolution(
            {
                "status": "binding_missing" if not binding_path.exists() else "binding_invalid",
                **unbound,
            }
        )
    common = {
        "binding_digest": binding.binding_digest,
        "binding_file_sha256": binding_file_sha256(binding),
        **unbound,
    }
    if not collection_file.is_file():
        return _resolution({"status": "scene_collection_missing", **common})
    if not collection_matches(binding, collection_file):
        return _resolution({"status": "scene_collection_mismatch", **common})
    if sidecar.producer.obs_version != binding.expected_obs_product_version:
        return _resolution({"status": "obs_version_mismatch", **common})
    scenes = [event for event in sidecar.events if event.type == "scene_changed"]
    if any(not isinstance(event.scene_uuid, UUID) for event in scenes):
        return _resolution({"status": "sidecar_missing_scene_uuid", **common})
    matching = [event for event in scenes if event.scene_uuid == binding.scene_uuid]
    if not matching:
        return _resolution({"status": "no_matching_scene_event", **common})
    if len(matching) != 1:
        return _resolution({"status": "ambiguous_scene_events", **common})
    event = matching[0]
    if event.scene_name != binding.scene_name:
        return _resolution({"status": "scene_name_mismatch", **common})
    # Der sichtbare Szenenanfang, nicht die Journalmarke: der Schutzblock von
    # 900 Frames deckte sonst nur 900 minus Lag echte Outroframes ab und der
    # Tailschnitt läge um denselben Betrag zu früh.
    corrected_start = event.mapped_source_frame + lag
    if corrected_start >= total:
        return _resolution({"status": "event_out_of_bounds", **common})
    if any(
        other.event_id != event.event_id
        for other in scenes
        if other.clock_sample.monotonic_ns >= event.clock_sample.monotonic_ns
    ):
        return _resolution({"status": "ambiguous_scene_events", **common})
    start = corrected_start
    protected_end = min(start + 900, total)
    tail_start = start + 900 if start + 900 < total else None
    return _resolution(
        {
            "status": "resolved",
            "scene_event_id": event.event_id,
            "scene_uuid": binding.scene_uuid,
            "scene_name": binding.scene_name,
            "outro_start_frame": start,
            "protected_start_frame": start,
            "protected_end_frame": protected_end,
            "tail_start_frame": tail_start,
            **common,
        }
    )
