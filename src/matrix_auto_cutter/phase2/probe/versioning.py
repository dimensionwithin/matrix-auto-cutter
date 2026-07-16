"""Bounded canonical parser for complete ``ffprobe -version`` stdout evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass

from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.contracts import FfprobeVersion, LibraryVersion
from matrix_auto_cutter.phase2.probe.errors import ProbeError, ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.probe.process_port import VERSION_OUTPUT_LIMIT
from matrix_auto_cutter.phase2.probe.supported_versions import (
    MAX_VERSION_COMPONENT,
    MAX_VERSION_COMPONENT_DIGITS,
    FfprobeSupportPolicy,
    FfprobeSupportPolicyIdentity,
    SemanticVersion,
)

_VERSION_PREFIX = "ffprobe version "
_DECIMAL = rf"(?:0|[1-9][0-9]{{0,{MAX_VERSION_COMPONENT_DIGITS - 1}}})"
_SEMANTIC_CORE = re.compile(
    rf"\A(?P<major>{_DECIMAL})\.(?P<minor>{_DECIMAL})\.(?P<patch>{_DECIMAL})\Z"
)
# This optional evidence parser is bounded by the already enforced one-MiB report limit.
# A line that does not match remains authenticated raw evidence; it cannot reject a report.
_LIBRARY = re.compile(
    rf"\A(?P<name>lib[A-Za-z0-9_]{{1,{VERSION_OUTPUT_LIMIT}}})[ ]{{1,32}}"
    rf"(?P<a>{_DECIMAL})\.[ ]{{0,32}}(?P<b>{_DECIMAL})\.[ ]{{0,32}}"
    rf"(?P<c>{_DECIMAL})[ ]{{0,32}}/[ ]{{0,32}}(?P<x>{_DECIMAL})\."
    rf"[ ]{{0,32}}(?P<y>{_DECIMAL})\.[ ]{{0,32}}(?P<z>{_DECIMAL})\Z"
)


@dataclass(frozen=True, slots=True)
class VersionParsed:
    """Successful syntax parse, independent of product support policy."""

    version: FfprobeVersion


@dataclass(frozen=True, slots=True)
class VersionRejected:
    """Structured rejection of unsafe or ambiguous report syntax."""

    error: ProbeError


@dataclass(frozen=True, slots=True)
class VersionSupported:
    """Successful evaluation under one explicit support-policy identity."""

    policy_identity: FfprobeSupportPolicyIdentity


@dataclass(frozen=True, slots=True)
class VersionUnsupported:
    """Structured rejection of valid syntax by one explicit support policy."""

    error: ProbeError


type VersionParseResult = VersionParsed | VersionRejected
type VersionSupportResult = VersionSupported | VersionUnsupported


def _reject(code: ProbeErrorCode, phase: str, message: str) -> VersionRejected:
    return VersionRejected(probe_error(code, ErrorCategory.POLICY, phase, message))


def _bounded_decimal(component: str) -> int | None:
    """Convert one component only after finite ASCII and representation checks."""
    if (
        not component
        or len(component) > MAX_VERSION_COMPONENT_DIGITS
        or any(character not in "0123456789" for character in component)
        or (len(component) > 1 and component[0] == "0")
    ):
        return None
    if len(component) == MAX_VERSION_COMPONENT_DIGITS and component > str(MAX_VERSION_COMPONENT):
        return None
    return int(component)


def _lines_from_report(text: str) -> list[str] | None:
    """Split LF/CRLF safely without imposing undocumented line-count limits."""
    if "\r" in text.replace("\r\n", ""):
        return None
    return text.replace("\r\n", "\n").split("\n")


def _optional_library(line: str) -> LibraryVersion | None:
    """Parse recognized library evidence; leave every other line as raw evidence."""
    library = _LIBRARY.fullmatch(line)
    if library is None:
        return None
    components = tuple(_bounded_decimal(library[name]) for name in ("a", "b", "c", "x", "y", "z"))
    if any(component is None for component in components):
        return None
    a, b, c, x, y, z = components
    assert all(component is not None for component in components)
    return LibraryVersion(
        library["name"],
        (a, b, c),  # type: ignore[arg-type]
        (x, y, z),  # type: ignore[arg-type]
        line,
    )


def parse_ffprobe_version(raw: bytes) -> VersionParseResult:
    """Parse one unambiguous product version from bounded complete stdout bytes."""
    if type(raw) is not bytes:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_decode",
            "version output must be immutable bytes",
        )
    if not raw or len(raw) > VERSION_OUTPUT_LIMIT:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_bounds",
            f"version output must contain 1..{VERSION_OUTPUT_LIMIT} bytes",
        )
    if raw.startswith(b"\xef\xbb\xbf"):
        return _reject(ProbeErrorCode.VERSION_OUTPUT, "version_decode", "BOM is forbidden")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return VersionRejected(
            probe_error(
                ProbeErrorCode.VERSION_OUTPUT,
                ErrorCategory.INTEGRITY,
                "version_decode",
                "version output is not strict UTF-8",
                cause=exc,
            )
        )
    if any(
        character == "\x00" or (not character.isprintable() and character not in "\r\n")
        for character in text
    ):
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "version output contains a forbidden control character",
        )
    lines = _lines_from_report(text)
    if lines is None:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "version output contains an unsafe carriage-return line separator",
        )
    version_lines = [line for line in lines if line.startswith(_VERSION_PREFIX)]
    if len(version_lines) != 1:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "version output must contain exactly one ffprobe product-version line",
        )
    first_line = version_lines[0]
    version_and_trailer = first_line[len(_VERSION_PREFIX) :]
    version_token, separator, trailer = version_and_trailer.partition(" ")
    if not version_token or (separator and not trailer):
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "the product-version line contains incomplete version evidence",
        )
    semantic_token, suffix_separator, suffix_text = version_token.partition("-")
    if suffix_separator and not suffix_text:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "the version build suffix must not be empty",
        )
    match = _SEMANTIC_CORE.fullmatch(semantic_token)
    if match is None:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_parse",
            "the ffprobe version is not canonical major.minor.patch syntax",
        )
    major = _bounded_decimal(match["major"])
    minor = _bounded_decimal(match["minor"])
    patch = _bounded_decimal(match["patch"])
    if major is None or minor is None or patch is None:
        return _reject(
            ProbeErrorCode.VERSION_OUTPUT,
            "version_bounds",
            f"version components must not exceed {MAX_VERSION_COMPONENT}",
        )
    compiler_line = next(
        (line for line in lines if line.startswith("built with ") and line != "built with "),
        "",
    )
    configuration_line = next(
        (
            line
            for line in lines
            if line.startswith("configuration: ") and line != "configuration: "
        ),
        "",
    )
    libraries = tuple(library for line in lines if (library := _optional_library(line)) is not None)
    return VersionParsed(
        FfprobeVersion(
            SemanticVersion(major, minor, patch),
            f"-{suffix_text}" if suffix_separator else "",
            first_line,
            compiler_line,
            configuration_line,
            libraries,
            text,
        )
    )


def evaluate_ffprobe_support(
    version: SemanticVersion, policy: FfprobeSupportPolicy
) -> VersionSupportResult:
    """Evaluate one parsed version under an explicit, validated policy value."""
    if type(policy) is not FfprobeSupportPolicy:
        raise TypeError("support evaluation requires an FfprobeSupportPolicy")
    if policy.supports(version):
        return VersionSupported(policy.identity)
    return VersionUnsupported(
        probe_error(
            ProbeErrorCode.UNSUPPORTED_VERSION,
            ErrorCategory.POLICY,
            "version_policy",
            f"ffprobe {version} is below supported minimum {policy.minimum_version}",
        )
    )
