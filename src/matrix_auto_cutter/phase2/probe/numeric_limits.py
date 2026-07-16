"""Central finite numeric-complexity contract for untrusted ffprobe evidence."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Final, cast

# Conservative limits cover ordinary ffprobe values while keeping every exact operation small.
MAX_NUMERIC_LEXEME_CHARS: Final = 64
MAX_SIGNIFICANT_DIGITS: Final = 32
MAX_INTEGER_DIGITS: Final = 19
MAX_DECIMAL_EXPONENT_ABS: Final = 18
MAX_RATIONAL_COMPONENT_CHARS: Final = 20
MAX_RATIONAL_COMPONENT_ABS: Final = (1 << 63) - 1
MAX_DERIVED_INTEGER_BITS: Final = 127
MAX_DECIMAL_SCALE: Final = 32

_DECIMAL = re.compile(
    r"-?(?P<integer>0|[1-9][0-9]*)(?:\.(?P<fraction>[0-9]+))?"
    r"(?:[eE](?P<exponent_sign>[+-]?)(?P<exponent>[0-9]+))?"
)
_INTEGER = re.compile(r"-?(?P<digits>0|[1-9][0-9]*)")
_INTEGER_WITH_LEADING_ZEROES = re.compile(r"-?(?P<digits>[0-9]+)")


def _bounded_exponent(sign: str, digits: str | None) -> int:
    if digits is None:
        return 0
    significant = digits.lstrip("0") or "0"
    limit_text = str(MAX_DECIMAL_EXPONENT_ABS)
    if len(significant) > len(limit_text) or (
        len(significant) == len(limit_text) and significant > limit_text
    ):
        raise ValueError("decimal exponent exceeds finite limit")
    value = int(significant)
    return -value if sign == "-" else value


def validate_decimal_lexeme(value: str) -> Decimal:
    """Validate an ASCII decimal lexeme completely before constructing ``Decimal``."""
    if not value or len(value) > MAX_NUMERIC_LEXEME_CHARS or not value.isascii():
        raise ValueError("decimal lexeme exceeds its finite ASCII contract")
    match = _DECIMAL.fullmatch(value)
    if match is None:
        raise ValueError("decimal lexeme does not match the strict grammar")
    integer = match["integer"]
    fraction = match["fraction"] or ""
    coefficient = (integer + fraction).lstrip("0") or "0"
    if len(coefficient) > MAX_SIGNIFICANT_DIGITS:
        raise ValueError("decimal significant digits exceed limit")
    exponent = _bounded_exponent(match["exponent_sign"] or "", match["exponent"])
    scale = len(fraction) - exponent
    if abs(scale) > MAX_DECIMAL_SCALE:
        raise ValueError("decimal scaling effort exceeds limit")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid bounded decimal") from exc
    if not parsed.is_finite():
        raise ValueError("decimal must be finite")
    return parsed


def validate_decimal_value(value: Decimal) -> tuple[int, tuple[int, ...], int]:
    """Bound an already-created Decimal before coefficient or power materialization."""
    if not value.is_finite():
        raise ValueError("non-finite Decimal cannot become an exact rational")
    sign, digits, raw_exponent = value.as_tuple()
    exponent = cast(int, raw_exponent)
    first_nonzero = next((index for index, digit in enumerate(digits) if digit), len(digits))
    significant_count = max(1, len(digits) - first_nonzero)
    if significant_count > MAX_SIGNIFICANT_DIGITS:
        raise ValueError("Decimal significant digits exceed limit")
    if exponent > MAX_DECIMAL_EXPONENT_ABS or exponent < -MAX_DECIMAL_SCALE:
        raise ValueError("Decimal exponent exceeds limit")
    return sign, digits, exponent


def validate_integer_lexeme(value: str, *, allow_leading_zeroes: bool = False) -> int:
    """Validate an integer string before bounded conversion."""
    if not value or len(value) > MAX_RATIONAL_COMPONENT_CHARS or not value.isascii():
        raise ValueError("integer lexeme exceeds its finite ASCII contract")
    grammar = _INTEGER_WITH_LEADING_ZEROES if allow_leading_zeroes else _INTEGER
    match = grammar.fullmatch(value)
    if match is None:
        raise ValueError("integer lexeme does not match the strict grammar")
    significant = match["digits"].lstrip("0") or "0"
    if len(significant) > MAX_INTEGER_DIGITS:
        raise ValueError("integer digits exceed limit")
    parsed = int(value)
    if abs(parsed) > MAX_RATIONAL_COMPONENT_ABS:
        raise ValueError("integer magnitude exceeds limit")
    return parsed


def validate_bounded_integer(value: int) -> int:
    """Reject bools and already-materialized integers outside the finite contract."""
    if isinstance(value, bool) or abs(value) > MAX_RATIONAL_COMPONENT_ABS:
        raise ValueError("integer magnitude exceeds limit")
    return value


def validate_derived_integer(value: int) -> int:
    """Bound results before subsequent GCD, multiplication or comparison work."""
    if value.bit_length() > MAX_DERIVED_INTEGER_BITS:
        raise ValueError("derived integer bit length exceeds limit")
    return value
