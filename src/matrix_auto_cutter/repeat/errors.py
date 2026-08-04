"""Errors for the isolated repeat/self-correction detection package."""

from __future__ import annotations


class RepeatContractError(Exception):
    """Raised when repeat package input or output violates its declared contract."""
