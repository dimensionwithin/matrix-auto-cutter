"""Canonical immutable source-hash receipt 1.0 model and validator."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from matrix_auto_cutter.models import CanonicalModel
from matrix_auto_cutter.phase2.artifacts import CanonicalUuidText, canonical_bytes

MAX_HASH_RECEIPT_BYTES = 1024 * 1024
HASH_CONTRACT_VERSION = "lease_bound_source_hash/1.0"
HASH_ALGORITHM_VERSION = "1.0"

Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
VolumeIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{16}$")]
FileIdHex = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class HashReceipt(CanonicalModel):
    """Strict canonical persistent evidence for one completed full hash."""

    artifact_type: Literal["source_hash_receipt"] = "source_hash_receipt"
    schema_version: Literal["1.0"] = "1.0"
    project_id: CanonicalUuidText
    hash_run_id: CanonicalUuidText
    lease_id: CanonicalUuidText
    validation_epoch: CanonicalUuidText
    s0_snapshot_key: Sha256Hex
    s4_snapshot_key: Sha256Hex
    s0_size_bytes: int = Field(ge=0)
    bytes_read: int = Field(ge=0)
    volume_id: VolumeIdHex
    file_id: FileIdHex
    file_id_scheme: Literal["file_id_128"]
    hash_algorithm: Literal["sha256"] = "sha256"
    hash_algorithm_version: Literal["1.0"] = "1.0"
    hash_contract_version: Literal["lease_bound_source_hash/1.0"] = "lease_bound_source_hash/1.0"
    block_size_bytes: int = Field(gt=0, le=8 * 1024 * 1024)
    sha256: Sha256Hex

    @model_validator(mode="after")
    def bindings_are_complete(self) -> Self:
        """Reject receipts that do not prove exact size, epoch, and S0/S4 equality."""
        if self.bytes_read != self.s0_size_bytes:
            raise ValueError("receipt byte count must equal the S0 size")
        if self.s0_snapshot_key != self.s4_snapshot_key:
            raise ValueError("receipt S0 and S4 snapshot keys must match")
        if self.lease_id != self.validation_epoch:
            raise ValueError("receipt lease ID and validation epoch must match")
        return self


def hash_receipt_bytes(receipt: HashReceipt) -> bytes:
    """Return canonical UTF-8 receipt bytes with exactly one final LF."""
    data = canonical_bytes(receipt)
    if len(data) > MAX_HASH_RECEIPT_BYTES:
        raise ValueError("hash receipt exceeds the 1 MiB limit")
    return data


def parse_hash_receipt_bytes(data: bytes) -> HashReceipt:
    """Strictly validate bounded canonical receipt bytes."""
    if len(data) > MAX_HASH_RECEIPT_BYTES:
        raise ValueError("hash receipt exceeds the 1 MiB limit")
    if data.startswith(b"\xef\xbb\xbf") or not data.endswith(b"\n"):
        raise ValueError("hash receipt is not canonical UTF-8")
    text = data.decode("utf-8", errors="strict")
    receipt = HashReceipt.model_validate_json(text)
    if hash_receipt_bytes(receipt) != data:
        raise ValueError("hash receipt bytes are not canonical")
    return receipt


def receipt_from_completed(completed: object) -> HashReceipt:
    """Return receipt evidence only for an authentic HashCompleted value."""
    from matrix_auto_cutter.phase2.source_hash.contracts import (
        HashCompleted,
        _is_authentic_completed,
    )

    if not isinstance(completed, HashCompleted) or not _is_authentic_completed(completed):
        raise TypeError("an authentic HashCompleted value is required")
    return completed.receipt
