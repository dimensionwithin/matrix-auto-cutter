"""Public package-2D hash results and sealed successful completion value."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Final
from uuid import UUID
from weakref import WeakKeyDictionary

from matrix_auto_cutter.phase2.snapshots import FileSnapshot

if TYPE_CHECKING:
    from matrix_auto_cutter.phase2.source_hash.receipt import HashReceipt


class HashErrorCode(StrEnum):
    """Stable package-2D error codes."""

    CANCELLED = "E_HASH_CANCELLED"
    IO = "E_HASH_IO"
    UNEXPECTED_EOF = "E_HASH_UNEXPECTED_EOF"
    SOURCE_CHANGED = "E_SOURCE_CHANGED"
    RECEIPT_CONFLICT = "E_HASH_RECEIPT_CONFLICT"


class HashErrorCategory(StrEnum):
    """Closed package-2D error categories."""

    CANCELLED = "cancelled"
    IO = "io"
    INTEGRITY = "integrity"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class HashDiagnostic:
    """Bounded secondary diagnostic that never replaces the primary failure."""

    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None


@dataclass(frozen=True, slots=True)
class HashFailure:
    """Structured package-2D failure without digest or receipt fields."""

    code: HashErrorCode
    category: HashErrorCategory
    phase: str
    message: str
    win32_code: int | None = None
    cause: BaseException | None = None
    underlying: object | None = None
    retryable: bool = False
    cleanup_diagnostics: tuple[HashDiagnostic, ...] = ()

    def with_cleanup(self, diagnostics: tuple[HashDiagnostic, ...]) -> HashFailure:
        """Append at most eight secondary cleanup diagnostics."""
        return replace(
            self,
            cleanup_diagnostics=(*self.cleanup_diagnostics, *diagnostics)[:8],
        )


@dataclass(frozen=True, slots=True)
class HashCancelled:
    """Cancellation linearized before successful hash publication."""

    error: HashFailure


@dataclass(frozen=True, slots=True)
class HashIoError:
    """I/O, lease, adapter, or unproven snapshot failure."""

    error: HashFailure


@dataclass(frozen=True, slots=True)
class HashUnexpectedEof:
    """The held source reached EOF before the S0-bound end offset."""

    error: HashFailure


@dataclass(frozen=True, slots=True)
class SourceChanged:
    """Extra bytes or snapshot evidence prove a source change."""

    error: HashFailure


HashFailureResult = HashCancelled | HashIoError | HashUnexpectedEof | SourceChanged

_HASH_COMPLETED_SEAL: Final = object()
_AUTH_LOCK = Lock()
_AUTHENTIC_COMPLETIONS: WeakKeyDictionary[HashCompleted, object] = WeakKeyDictionary()


class HashCompleted:
    """Authentic immutable result issued only after full hash/S4 commit."""

    __slots__ = ("__weakref__", "_receipt", "_s0", "_s4", "_token")
    _receipt: HashReceipt
    _s0: FileSnapshot
    _s4: FileSnapshot
    _token: object

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Reject direct construction outside the successful hash issuer."""
        del args, kwargs
        raise TypeError("HashCompleted is issued only by a committed lease-bound hash")

    def __setattr__(self, name: str, value: object) -> None:
        """Reject mutation of completed hash bindings."""
        del name, value
        raise AttributeError("HashCompleted bindings are immutable")

    def __delattr__(self, name: str) -> None:
        """Reject deletion of completed hash bindings."""
        del name
        raise AttributeError("HashCompleted bindings are immutable")

    def _initialize(
        self,
        receipt: HashReceipt,
        s0: FileSnapshot,
        s4: FileSnapshot,
        token: object,
        *,
        _seal: object,
    ) -> None:
        if _seal is not _HASH_COMPLETED_SEAL:
            raise TypeError("HashCompleted is issued only by package 2D")
        object.__setattr__(self, "_receipt", receipt)
        object.__setattr__(self, "_s0", s0)
        object.__setattr__(self, "_s4", s4)
        object.__setattr__(self, "_token", token)

    @property
    def sha256(self) -> str:
        """Return the committed lowercase SHA-256 digest."""
        return self._receipt.sha256

    @property
    def bytes_read(self) -> int:
        """Return the exact committed byte count."""
        return self._receipt.bytes_read

    @property
    def s0_size_bytes(self) -> int:
        """Return the S0-bound source size."""
        return self._receipt.s0_size_bytes

    @property
    def s0(self) -> FileSnapshot:
        """Return the gate-bound S0 snapshot."""
        return self._s0

    @property
    def s4(self) -> FileSnapshot:
        """Return the post-hash S4 snapshot."""
        return self._s4

    @property
    def lease_id(self) -> UUID:
        """Return the bound close-gate lease identifier."""
        return UUID(self._receipt.lease_id)

    @property
    def validation_epoch(self) -> UUID:
        """Return the bound close-gate validation epoch."""
        return UUID(self._receipt.validation_epoch)

    @property
    def project_id(self) -> str:
        """Return the canonical project binding."""
        return self._receipt.project_id

    @property
    def hash_run_id(self) -> str:
        """Return the canonical hash-run binding."""
        return self._receipt.hash_run_id

    @property
    def hash_algorithm(self) -> str:
        """Return the fixed hash algorithm name."""
        return self._receipt.hash_algorithm

    @property
    def hash_algorithm_version(self) -> str:
        """Return the fixed algorithm version."""
        return self._receipt.hash_algorithm_version

    @property
    def block_size_bytes(self) -> int:
        """Return the bounded block size used by this run."""
        return self._receipt.block_size_bytes

    @property
    def receipt(self) -> HashReceipt:
        """Return the immutable canonical receipt value."""
        return self._receipt

    def __copy__(self) -> HashCompleted:
        """Reject shallow copying as an authority path."""
        raise TypeError("HashCompleted values cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> HashCompleted:
        """Reject deep copying as an authority path."""
        del memo
        raise TypeError("HashCompleted values cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        """Reject serialization as an authority path."""
        raise TypeError("HashCompleted values cannot be serialized as authority")


HashResult = HashCompleted | HashFailureResult


def _issue_hash_completed(
    receipt: HashReceipt,
    s0: FileSnapshot,
    s4: FileSnapshot,
) -> HashCompleted:
    token = object()
    completed = object.__new__(HashCompleted)
    completed._initialize(receipt, s0, s4, token, _seal=_HASH_COMPLETED_SEAL)
    with _AUTH_LOCK:
        _AUTHENTIC_COMPLETIONS[completed] = token
    return completed


def _is_authentic_completed(completed: HashCompleted) -> bool:
    try:
        token = completed._token
    except AttributeError:
        return False
    with _AUTH_LOCK:
        return _AUTHENTIC_COMPLETIONS.get(completed) is token
