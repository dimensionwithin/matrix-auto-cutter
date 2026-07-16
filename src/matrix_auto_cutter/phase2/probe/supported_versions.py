"""Canonical ffprobe semantic versions and the immutable product support policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final, Literal

MAX_VERSION_COMPONENT_DIGITS = 10
MAX_VERSION_COMPONENT = 2_147_483_647


@dataclass(frozen=True, slots=True, order=True)
class SemanticVersion:
    """A strict three-component semantic version."""

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        """Reject non-integer, negative, and non-interoperable components."""
        components = (self.major, self.minor, self.patch)
        if any(type(component) is not int for component in components):
            raise TypeError("semantic version components must be integers")
        if any(not 0 <= component <= MAX_VERSION_COMPONENT for component in components):
            raise ValueError(
                f"semantic version components must be within 0..{MAX_VERSION_COMPONENT}"
            )

    def __str__(self) -> str:
        """Render canonical dotted text."""
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class FfprobeSupportPolicyIdentity:
    """Canonical identity of one completely validated support-policy value."""

    revision: Literal["1.0"]
    policy_type: Literal["minimum_semantic_version"]
    content_sha256: str

    def __post_init__(self) -> None:
        """Reject identities that are not canonical policy-1.0 identities."""
        if type(self.revision) is not str or self.revision != "1.0":
            raise ValueError("support policy revision must be canonical revision 1.0")
        if type(self.policy_type) is not str or self.policy_type != "minimum_semantic_version":
            raise ValueError("support policy type must be minimum_semantic_version")
        digest = self.content_sha256
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("support policy digest must be canonical lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class FfprobeSupportPolicy:
    """Validated minimum-version policy with content-derived identity.

    The minimum is an internal policy value, not a parser default.  Copies, pickle
    round-trips, and ``dataclasses.replace`` remain safe because construction always
    revalidates the complete value and the identity is derived from current content.
    """

    revision: Literal["1.0"] = "1.0"
    policy_type: Literal["minimum_semantic_version"] = "minimum_semantic_version"
    minimum_version: SemanticVersion = SemanticVersion(7, 0, 0)

    def __post_init__(self) -> None:
        """Enforce the one canonical policy schema at runtime."""
        if type(self.revision) is not str or self.revision != "1.0":
            raise ValueError("support policy revision must be canonical revision 1.0")
        if type(self.policy_type) is not str or self.policy_type != "minimum_semantic_version":
            raise ValueError("support policy type must be minimum_semantic_version")
        if type(self.minimum_version) is not SemanticVersion:
            raise TypeError("support policy minimum must be a SemanticVersion")

    @property
    def canonical_bytes(self) -> bytes:
        """Return the fixed, locale-independent canonical policy representation."""
        return (
            "matrix-auto-cutter/ffprobe-support-policy\n"
            f"revision={self.revision}\n"
            f"policy_type={self.policy_type}\n"
            f"minimum_version={self.minimum_version}\n"
        ).encode("ascii", errors="strict")

    @property
    def identity(self) -> FfprobeSupportPolicyIdentity:
        """Bind revision, type, and complete policy content."""
        return FfprobeSupportPolicyIdentity(
            self.revision,
            self.policy_type,
            hashlib.sha256(self.canonical_bytes).hexdigest(),
        )

    def supports(self, version: SemanticVersion) -> bool:
        """Apply the documented numerical minimum-version comparison."""
        if type(version) is not SemanticVersion:
            raise TypeError("support evaluation requires a SemanticVersion")
        return version >= self.minimum_version


PRODUCT_FFPROBE_SUPPORT_POLICY: Final = FfprobeSupportPolicy()
