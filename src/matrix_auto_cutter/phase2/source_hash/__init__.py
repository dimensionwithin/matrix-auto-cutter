"""Public package-2D lease-bound source hashing API."""

from matrix_auto_cutter.phase2.source_hash.contracts import (
    HashCancelled,
    HashCompleted,
    HashDiagnostic,
    HashErrorCategory,
    HashErrorCode,
    HashFailure,
    HashIoError,
    HashResult,
    HashUnexpectedEof,
    SourceChanged,
)
from matrix_auto_cutter.phase2.source_hash.hashing import (
    EOF_PROBE_BYTES,
    PRODUCTION_BLOCK_SIZE_BYTES,
    hash_lease_source,
)
from matrix_auto_cutter.phase2.source_hash.publish import (
    HashReceiptConflict,
    HashReceiptPublishCancelled,
    HashReceiptPublished,
    HashReceiptPublishIoError,
    HashReceiptPublishResult,
    publish_hash_receipt,
)
from matrix_auto_cutter.phase2.source_hash.receipt import (
    HASH_ALGORITHM_VERSION,
    HASH_CONTRACT_VERSION,
    MAX_HASH_RECEIPT_BYTES,
    HashReceipt,
    hash_receipt_bytes,
    parse_hash_receipt_bytes,
    receipt_from_completed,
)

__all__ = [
    "EOF_PROBE_BYTES",
    "HASH_ALGORITHM_VERSION",
    "HASH_CONTRACT_VERSION",
    "MAX_HASH_RECEIPT_BYTES",
    "PRODUCTION_BLOCK_SIZE_BYTES",
    "HashCancelled",
    "HashCompleted",
    "HashDiagnostic",
    "HashErrorCategory",
    "HashErrorCode",
    "HashFailure",
    "HashIoError",
    "HashReceipt",
    "HashReceiptConflict",
    "HashReceiptPublishCancelled",
    "HashReceiptPublishIoError",
    "HashReceiptPublishResult",
    "HashReceiptPublished",
    "HashResult",
    "HashUnexpectedEof",
    "SourceChanged",
    "hash_lease_source",
    "hash_receipt_bytes",
    "parse_hash_receipt_bytes",
    "publish_hash_receipt",
    "receipt_from_completed",
]
