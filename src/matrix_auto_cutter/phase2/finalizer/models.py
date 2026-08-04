"""Strict canonical package-2F bundle, intent, state, and receipt models."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, field_validator, model_validator

from matrix_auto_cutter.models import CanonicalModel, SourceIdentity
from matrix_auto_cutter.phase2.artifacts import CanonicalUuidText, canonical_bytes
from matrix_auto_cutter.phase2.source_confirmation.identity import source_identity_digest

MAX_BUNDLE_COMPONENT_BYTES = 1024 * 1024
MAX_INTENT_BYTES = 2 * 1024 * 1024
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_FINALIZATION_RECEIPT_BYTES = 2 * 1024 * 1024
MAX_SIDECAR_BYTES = 256 * 1024 * 1024
MAX_JOURNAL_BYTES = 256 * 1024 * 1024
MAX_JOURNAL_LINE_BYTES = 64 * 1024
MAX_JOURNAL_RECORDS = 1_000_000

Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
VolumeIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$")]
FileIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


def _safe_reference(value: str) -> str:
    if (
        PureWindowsPath(value).name != value
        or value in {".", ".."}
        or any(character in value for character in "\\/:")
    ):
        raise ValueError("bundle references must be safe leaf names")
    return value


class JournalInputProfile(StrEnum):
    """Exact caller-selected journal input profiles accepted by package 2F."""

    LEGACY = "legacy_journal_1_0"
    BUNDLE = "phase2_journal_bundle_1_0"


class RecordingJournalSession(CanonicalModel):
    """Strict consumer model for recording-journal session receipt 1.0."""

    artifact_type: Literal["recording_journal_session"] = "recording_journal_session"
    schema_version: Literal["1.0"] = "1.0"
    recording_session_id: CanonicalUuidText
    plugin_run_id: CanonicalUuidText
    producer_name: Literal["matrix-auto-cutter-obs-producer"]
    producer_version: str = Field(min_length=1, max_length=128)
    obs_version: str = Field(min_length=1, max_length=128)
    journal_schema_version: Literal["1.0"] = "1.0"


class RecordingJournalIntegrity(CanonicalModel):
    """Strict consumer model for recording-journal integrity receipt 1.0."""

    artifact_type: Literal["recording_journal_integrity"] = "recording_journal_integrity"
    schema_version: Literal["1.0"] = "1.0"
    recording_session_id: CanonicalUuidText
    plugin_run_id: CanonicalUuidText
    journal_reference: str = Field(min_length=1, max_length=32767)
    journal_size_bytes: int = Field(ge=1, le=MAX_JOURNAL_BYTES)
    journal_sha256: Sha256Hex
    session_receipt_digest: Sha256Hex
    journal_schema_version: Literal["1.0"] = "1.0"

    _journal_reference_is_safe = field_validator("journal_reference")(_safe_reference)


class BundleComponent(CanonicalModel):
    """Digest, size, type, version, and safe reference for one bundle member."""

    artifact_type: Literal[
        "recording_event_journal",
        "recording_journal_session",
        "recording_journal_integrity",
    ]
    schema_version: Literal["1.0"]
    safe_reference: str = Field(min_length=1, max_length=32767)
    size_bytes: int = Field(ge=1, le=MAX_JOURNAL_BYTES)
    sha256: Sha256Hex

    _safe_reference_is_safe = field_validator("safe_reference")(_safe_reference)


class RecordingJournalBundle(CanonicalModel):
    """Strict self-digesting recording-journal bundle manifest 1.0."""

    artifact_type: Literal["recording_journal_bundle"] = "recording_journal_bundle"
    bundle_schema_version: Literal["1.0"] = "1.0"
    recording_session_id: CanonicalUuidText
    plugin_run_id: CanonicalUuidText
    producer_version: str = Field(min_length=1, max_length=128)
    obs_version: str = Field(min_length=1, max_length=128)
    journal: BundleComponent
    session_receipt: BundleComponent
    integrity_receipt: BundleComponent
    bundle_manifest_digest: Sha256Hex

    @model_validator(mode="after")
    def digest_is_self_consistent(self) -> Self:
        """Reject manifests whose domain-separated self-digest differs."""
        if (
            self.journal.artifact_type != "recording_event_journal"
            or self.session_receipt.artifact_type != "recording_journal_session"
            or self.integrity_receipt.artifact_type != "recording_journal_integrity"
        ):
            raise ValueError("bundle component artifact types differ from their positions")
        if self.bundle_manifest_digest != bundle_manifest_digest(self):
            raise ValueError("bundle manifest digest mismatch")
        return self


def bundle_manifest_digest(bundle: RecordingJournalBundle) -> str:
    """Return the domain-separated digest of all manifest semantics."""
    payload = bundle.model_dump_json(exclude={"bundle_manifest_digest"}).encode("utf-8")
    return hashlib.sha256(b"matrix-journal-bundle/1.0\0" + payload).hexdigest()


class UnavailableProvenance(CanonicalModel):
    """Explicit legacy-profile marker for unavailable producer provenance."""

    status: Literal["not_available"] = "not_available"


class BundleBinding(CanonicalModel):
    """Validated cross-binding retained from a complete bundle profile."""

    status: Literal["validated"] = "validated"
    bundle_schema_version: Literal["1.0"] = "1.0"
    plugin_run_id: CanonicalUuidText
    session_receipt_digest: Sha256Hex
    integrity_receipt_digest: Sha256Hex
    bundle_manifest_digest: Sha256Hex
    producer_version: str = Field(min_length=1, max_length=128)
    obs_version: str = Field(min_length=1, max_length=128)


class FinalizationIntent(CanonicalModel):
    """Immutable retry provenance published before sidecar construction."""

    artifact_type: Literal["finalization_intent"] = "finalization_intent"
    schema_version: Literal["1.0"] = "1.0"
    finalizer_run_id: CanonicalUuidText
    finalized_at: AwareDatetime
    project_id: CanonicalUuidText
    input_profile: JournalInputProfile
    recording_id: CanonicalUuidText
    journal_sha256: Sha256Hex
    journal_size_bytes: int = Field(ge=1, le=MAX_JOURNAL_BYTES)
    bundle_binding: BundleBinding | UnavailableProvenance
    source_identity: SourceIdentity
    source_identity_digest: Sha256Hex
    source_identity_evidence_id: Sha256Hex
    source_identity_evidence_digest: Sha256Hex
    source_volume_id: VolumeIdHex
    source_file_id: FileIdHex
    probe_artifact_id: str = Field(min_length=1, max_length=128)
    hash_artifact_id: str = Field(min_length=1, max_length=128)
    assignment_artifact_id: str = Field(min_length=1, max_length=128)
    sidecar_schema_version: Literal["1.1", "1.2"] = "1.2"
    journal_schema_version: Literal["1.0"] = "1.0"
    bundle_schema_version: Literal["1.0", "not_available"]
    finalizer_version: Literal["phase2f/1.0"] = "phase2f/1.0"
    clock_contract: Literal["phase1_clock/1.0"] = "phase1_clock/1.0"
    pairing_contract: Literal["phase1_pairing/1.0"] = "phase1_pairing/1.0"
    protection_contract: Literal["phase1_protection/1.0"] = "phase1_protection/1.0"
    serialization_contract: Literal["phase1_canonical_json/1.0"] = "phase1_canonical_json/1.0"
    target_path_digest: Sha256Hex
    target_generation: CanonicalUuidText
    synthetic_stop_event_id: CanonicalUuidText
    finalization_key: Sha256Hex

    @model_validator(mode="after")
    def key_is_self_consistent(self) -> Self:
        """Reject inconsistent intent keys and input-profile bindings."""
        if self.source_identity_digest != source_identity_digest(self.source_identity):
            raise ValueError("intent SourceIdentity digest mismatch")
        if self.finalization_key != finalization_key(self):
            raise ValueError("finalization key mismatch")
        if (self.input_profile is JournalInputProfile.BUNDLE) != isinstance(
            self.bundle_binding, BundleBinding
        ):
            raise ValueError("input profile and bundle binding differ")
        return self


def finalization_key(intent: FinalizationIntent) -> str:
    """Return the domain-separated digest of every semantic intent value."""
    payload = intent.model_dump_json(exclude={"finalization_key"}).encode("utf-8")
    return hashlib.sha256(b"matrix-auto-cutter/finalization-intent/1.0\0" + payload).hexdigest()


class FinalizerStateName(StrEnum):
    """Exact normative package-2F runtime-state vocabulary."""

    DISCOVERED = "discovered"
    VALIDATING_INPUT = "validating_input"
    RESOLVING_SOURCE = "resolving_source"
    AWAITING_CLOSE = "awaiting_close"
    PROBING = "probing"
    HASHING = "hashing"
    CONFIRMING_IDENTITY = "confirming_identity"
    PREPARING_INTENT = "preparing_intent"
    CONSTRUCTING_SIDECAR = "constructing_sidecar"
    COMMITTING_SIDECAR = "committing_sidecar"
    FINALIZED = "finalized"
    CANCELLED = "cancelled"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class FinalizerState(CanonicalModel):
    """Replaceable diagnostic state 1.0; never sidecar commit evidence."""

    artifact_type: Literal["finalizer_state"] = "finalizer_state"
    schema_version: Literal["1.0"] = "1.0"
    project_id: CanonicalUuidText
    finalizer_run_id: CanonicalUuidText
    revision: int = Field(ge=0)
    current_state: FinalizerStateName
    input_profile: JournalInputProfile
    recording_id: str = Field(min_length=1, max_length=128)
    intent_id: str = Field(min_length=1, max_length=128)
    target_generation: str = Field(min_length=1, max_length=128)
    journal_sha256: str = Field(min_length=1, max_length=128)
    source_identity_digest: str = Field(min_length=1, max_length=128)
    sidecar_sha256: str = Field(min_length=1, max_length=128)
    last_safe_transition: str = Field(min_length=1, max_length=256)
    error_or_cancel_reference: str = Field(min_length=1, max_length=512)
    observed_at: AwareDatetime
    recovery_status: Literal["normal", "recovering", "reconstructed"]


class FinalizationReceipt(CanonicalModel):
    """Reconstructable post-commit evidence receipt 1.0."""

    artifact_type: Literal["finalization_receipt"] = "finalization_receipt"
    schema_version: Literal["1.0"] = "1.0"
    receipt_contract_version: Literal["finalization_receipt/1.0"] = "finalization_receipt/1.0"
    project_id: CanonicalUuidText
    intent_run_id: CanonicalUuidText
    target_generation: CanonicalUuidText
    recording_id: CanonicalUuidText
    source_identity: SourceIdentity
    source_identity_digest: Sha256Hex
    sidecar_path_digest: Sha256Hex
    sidecar_sha256: Sha256Hex
    sidecar_size_bytes: int = Field(ge=1, le=MAX_SIDECAR_BYTES)
    sidecar_schema_version: Literal["1.1", "1.2"] = "1.2"
    finalizer_run_id: CanonicalUuidText
    finalized_at: AwareDatetime
    intent_id: Sha256Hex
    intent_digest: Sha256Hex

    @model_validator(mode="after")
    def source_digest_is_self_consistent(self) -> Self:
        """Reject receipts whose embedded SourceIdentity digest differs."""
        if self.source_identity_digest != source_identity_digest(self.source_identity):
            raise ValueError("receipt SourceIdentity digest mismatch")
        return self


def strict_artifact_bytes(model: CanonicalModel, maximum: int) -> bytes:
    """Serialize canonically and enforce the artifact-specific size bound."""
    data = canonical_bytes(model)
    if len(data) > maximum:
        raise ValueError("artifact exceeds its bounded size contract")
    return data


def parse_canonical[ModelT: CanonicalModel](
    data: bytes,
    maximum: int,
    model_type: type[ModelT],
) -> ModelT:
    """Parse one bounded strict canonical UTF-8 JSON artifact."""
    if len(data) > maximum:
        raise ValueError("artifact exceeds its bounded size contract")
    if data.startswith(b"\xef\xbb\xbf") or not data.endswith(b"\n"):
        raise ValueError("artifact is not canonical UTF-8")
    model = model_type.model_validate_json(data.decode("utf-8", errors="strict"))
    if canonical_bytes(model) != data:
        raise ValueError("artifact bytes are not canonical")
    return model


def parse_intent_bytes(data: bytes) -> FinalizationIntent:
    """Parse bounded canonical finalization-intent bytes."""
    return parse_canonical(data, MAX_INTENT_BYTES, FinalizationIntent)


def parse_state_bytes(data: bytes) -> FinalizerState:
    """Parse bounded canonical finalizer-state bytes."""
    return parse_canonical(data, MAX_STATE_BYTES, FinalizerState)


def parse_finalization_receipt_bytes(data: bytes) -> FinalizationReceipt:
    """Parse bounded canonical finalization-receipt bytes."""
    return parse_canonical(data, MAX_FINALIZATION_RECEIPT_BYTES, FinalizationReceipt)
