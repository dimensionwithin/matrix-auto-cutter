from __future__ import annotations

from dataclasses import replace

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import VERSION_TEXT, FakeProcessPort, golden_json, golden_stream
from tests.phase2.probe.test_runner_process import source_request

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe import (
    PRODUCT_FFPROBE_SUPPORT_POLICY,
    BinaryValidated,
    FfprobeCandidate,
    ProbeErrorCode,
    ProbeFailed,
    ProbeOk,
    ProbeProcessOk,
    ProcessDiagnostics,
    SemanticVersion,
    VersionParsed,
    VersionRejected,
    VersionSupported,
    VersionUnsupported,
    evaluate_ffprobe_support,
    parse_ffprobe_version,
    run_probe,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
    _open_verified_binary_for_launch,
)
from matrix_auto_cutter.phase2.probe.errors import probe_error
from matrix_auto_cutter.phase2.probe.process_port import VERSION_OUTPUT_LIMIT
from matrix_auto_cutter.phase2.probe.supported_versions import (
    MAX_VERSION_COMPONENT,
    FfprobeSupportPolicy,
)
from matrix_auto_cutter.phase2.probe.versioning import _bounded_decimal
from matrix_auto_cutter.phase2.snapshots import snapshot_file


def _report(version: str, *, ending: str = "\n") -> bytes:
    text = VERSION_TEXT.replace("8.1.1-test-build", version, 1)
    if ending != "\n":
        text = text.replace("\n", ending)
    return text.encode("utf-8")


def _bounded_test_id(value: object) -> str | None:
    if isinstance(value, bytes | str) and len(value) > 80:
        return f"pathological-length-{len(value)}"
    return None


@pytest.mark.parametrize(
    ("token", "semantic", "suffix"),
    [
        ("0.0.0", SemanticVersion(0, 0, 0), ""),
        ("8.1.1", SemanticVersion(8, 1, 1), ""),
        ("8.1.1-test-build", SemanticVersion(8, 1, 1), "-test-build"),
        ("8.1.1-0+vendor.1~dev", SemanticVersion(8, 1, 1), "-0+vendor.1~dev"),
        (
            f"{MAX_VERSION_COMPONENT}.{MAX_VERSION_COMPONENT}.{MAX_VERSION_COMPONENT}",
            SemanticVersion(
                MAX_VERSION_COMPONENT,
                MAX_VERSION_COMPONENT,
                MAX_VERSION_COMPONENT,
            ),
            "",
        ),
    ],
)
def test_canonical_valid_forms_preserve_evidence_and_normalize_numerically(
    token: str, semantic: SemanticVersion, suffix: str
) -> None:
    raw = _report(token)
    result = parse_ffprobe_version(raw)
    assert isinstance(result, VersionParsed)
    assert result.version.semantic_version == semantic
    assert str(result.version.semantic_version) == (
        f"{semantic.major}.{semantic.minor}.{semantic.patch}"
    )
    assert result.version.build_suffix == suffix
    assert result.version.raw_output.encode("utf-8") == raw


def test_canonical_report_accepts_only_lf_or_crlf_without_losing_original_evidence() -> None:
    lf = parse_ffprobe_version(_report("8.1.1-test-build"))
    crlf_raw = _report("8.1.1-test-build", ending="\r\n")
    crlf = parse_ffprobe_version(crlf_raw)
    no_final_lf = parse_ffprobe_version(VERSION_TEXT.rstrip("\n").encode())
    assert isinstance(lf, VersionParsed)
    assert isinstance(crlf, VersionParsed)
    assert isinstance(no_final_lf, VersionParsed)
    assert lf.version.semantic_version == crlf.version.semantic_version
    assert crlf.version.raw_output.encode() == crlf_raw


@pytest.mark.parametrize(
    "token",
    [
        "",
        " ",
        "8.1",
        "8.1.1.0",
        "8.a.1",
        "-8.1.1",
        "+8.1.1",
        "8..1",
        ".8.1.1",
        "8.1.1.",
        "08.1.1",
        "8.01.1",
        "8.1.01",
        "\u0668.\u0661.\u0661",
        "8.1.1+vendor",
        "8.1.1-",
        "8.1.1-test-build\x00tail",
        "8.1.1-test-build\ttail",
        "8.1.1-test-build trailing\x00junk",
        f"{MAX_VERSION_COMPONENT + 1}.1.1",
        "9" * 100_000 + ".1.1",
        "8.1.1.2.3.4.5.6.7.8.9",
    ],
    ids=_bounded_test_id,
)
def test_noncanonical_version_tokens_fail_closed_without_partial_matches(token: str) -> None:
    result = parse_ffprobe_version(_report(token))
    assert isinstance(result, VersionRejected)
    assert result.error.code is ProbeErrorCode.VERSION_OUTPUT
    assert result.error.phase in {"version_parse", "version_bounds"}


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"   ",
        b"x" * (VERSION_OUTPUT_LIMIT + 1),
        VERSION_TEXT.replace("ffprobe version ", "ffprobe versions ").encode(),
        VERSION_TEXT.replace("ffprobe version ", "FFPROBE version ").encode(),
        VERSION_TEXT.replace(
            "ffprobe version 8.1.1-test-build Copyright test",
            "ffprobe version 8.1.1 ",
        ).encode(),
        VERSION_TEXT.replace("\n", "\r", 1).encode(),
    ],
    ids=_bounded_test_id,
)
def test_malformed_or_pathological_reports_are_structured_rejections(raw: bytes) -> None:
    result = parse_ffprobe_version(raw)
    assert isinstance(result, VersionRejected)
    assert result.error.code is ProbeErrorCode.VERSION_OUTPUT


def test_suffix_has_no_undocumented_sublimit_but_total_report_limit_remains() -> None:
    long_vendor_suffix = "-" + "a" * 10_000 + "/vendor+build~local"
    accepted = parse_ffprobe_version(_report("8.1.1" + long_vendor_suffix))
    rejected = parse_ffprobe_version(b"x" * (VERSION_OUTPUT_LIMIT + 1))
    assert isinstance(accepted, VersionParsed)
    assert accepted.version.build_suffix == long_vendor_suffix
    assert isinstance(rejected, VersionRejected)


def test_semantic_comparison_and_minimum_policy_boundaries_are_numeric() -> None:
    assert SemanticVersion(2, 10, 0) > SemanticVersion(2, 9, 0)
    assert SemanticVersion(2, 10, 0) == SemanticVersion(2, 10, 0)
    for token in ("6.9.9", "7.0.0", "7.0.1", "8.1.1", "9.0.0"):
        parsed = parse_ffprobe_version(_report(token))
        assert isinstance(parsed, VersionParsed)
        support = evaluate_ffprobe_support(
            parsed.version.semantic_version,
            PRODUCT_FFPROBE_SUPPORT_POLICY,
        )
        if token == "6.9.9":
            assert isinstance(support, VersionUnsupported)
            assert support.error.code is ProbeErrorCode.UNSUPPORTED_VERSION
            assert support.error.phase == "version_policy"
        else:
            assert isinstance(support, VersionSupported)

    alternative = FfprobeSupportPolicy(minimum_version=SemanticVersion(8, 0, 0))
    assert not alternative.supports(SemanticVersion(7, 9, 9))


@pytest.mark.parametrize(
    "components",
    [
        (-1, 0, 0),
        (MAX_VERSION_COMPONENT + 1, 0, 0),
    ],
)
def test_semantic_version_runtime_bounds(components: tuple[int, int, int]) -> None:
    with pytest.raises(ValueError):
        SemanticVersion(*components)
    with pytest.raises(TypeError):
        SemanticVersion(True, 0, 0)


def test_parser_rejects_non_bytes_as_a_structured_error() -> None:
    result = parse_ffprobe_version("8.1.1")  # type: ignore[arg-type]
    assert isinstance(result, VersionRejected)
    assert result.error.phase == "version_decode"


def test_bounded_decimal_rejects_more_than_the_fixed_digit_count() -> None:
    assert _bounded_decimal("1" * 11) is None


def test_validator_prelaunch_and_post_inspection_share_one_canonical_result(
    fake_port: FakePort, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    original = binary_module.parse_ffprobe_version
    parsed_versions = []

    def recording_parse(raw: bytes):
        result = original(raw)
        if isinstance(result, VersionParsed):
            parsed_versions.append(result.version)
        return result

    monkeypatch.setattr(binary_module, "parse_ffprobe_version", recording_parse)
    validation = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert isinstance(validation, BinaryValidated)
    held = _open_verified_binary_for_launch(validation.binary, NativeBinaryTrustPort(fake_port))
    assert not isinstance(held, BinaryInspectionFailed)
    assert held.close() is None

    request = source_request(fake_port, validation.binary)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]), b""
            )
        )
    )
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeOk)
    assert result.profile.binary is validation.binary
    assert len(parsed_versions) == 4
    assert all(version == validation.binary.version for version in parsed_versions)


def test_prelaunch_parser_rejection_prevents_process_start(
    fake_port: FakePort, validated_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    monkeypatch.setattr(
        binary_module,
        "parse_ffprobe_version",
        lambda *_args, **_kwargs: VersionRejected(
            probe_error(
                ProbeErrorCode.VERSION_OUTPUT,
                ErrorCategory.INTEGRITY,
                "version_parse",
                "forced canonical rejection",
            )
        ),
    )
    request = source_request(fake_port, validated_binary)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]), b""
            )
        )
    )
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
    assert process.calls is None


def test_post_inspection_parser_rejection_after_start_prevents_success(
    fake_port: FakePort, validated_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    original = binary_module.parse_ffprobe_version
    calls = 0

    def fail_second_parse(raw: bytes):
        nonlocal calls
        calls += 1
        if calls == 2:
            return VersionRejected(
                probe_error(
                    ProbeErrorCode.VERSION_OUTPUT,
                    ErrorCategory.INTEGRITY,
                    "version_parse",
                    "forced post-inspection rejection",
                )
            )
        return original(raw)

    monkeypatch.setattr(binary_module, "parse_ffprobe_version", fail_second_parse)
    request = source_request(fake_port, validated_binary)
    process = FakeProcessPort(
        ProbeProcessOk(
            ProcessDiagnostics(
                golden_json([golden_stream(0, "video"), golden_stream(1, "audio")]), b""
            )
        )
    )
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        process,
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert calls == 2
    assert process.calls is not None
    assert isinstance(result, ProbeFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
    assert result.error.phase == "binary_post_probe"


def test_capability_original_and_canonical_version_evidence_cannot_diverge(
    fake_port: FakePort, validated_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    monkeypatch.setattr(binary_module.hmac, "compare_digest", lambda *_args: True)
    forged = object.__new__(type(validated_binary))
    altered = replace(
        validated_binary.version,
        raw_output=validated_binary.raw_version_output + "x",
    )
    for slot in type(validated_binary).__slots__:
        value = altered if slot == "version" else getattr(validated_binary, slot)
        object.__setattr__(forged, slot, value)
    result = _open_verified_binary_for_launch(forged, NativeBinaryTrustPort(fake_port))
    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
