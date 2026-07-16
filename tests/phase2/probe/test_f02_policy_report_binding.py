from __future__ import annotations

import copy
import inspect
import pickle
from dataclasses import replace

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import VERSION_TEXT, FakeProcessPort

from matrix_auto_cutter.phase2.probe import (
    PRODUCT_FFPROBE_SUPPORT_POLICY,
    BinaryValidated,
    FfprobeCandidate,
    FfprobeSupportPolicy,
    ProbeErrorCode,
    ProbeProcessOk,
    ProcessDiagnostics,
    SemanticVersion,
    ValidatedFfprobeBinary,
    VersionParsed,
    VersionRejected,
    VersionSupported,
    VersionUnsupported,
    evaluate_ffprobe_support,
    parse_ffprobe_version,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
    _capability_is_authentic,
    _capability_policy_is_current,
    _open_verified_binary_for_launch,
)
from matrix_auto_cutter.phase2.probe.process_port import VERSION_OUTPUT_LIMIT
from matrix_auto_cutter.phase2.probe.supported_versions import (
    MAX_VERSION_COMPONENT,
    FfprobeSupportPolicyIdentity,
)


def _minimal_report(version: str = "8.1.1-vendor") -> bytes:
    return f"ffprobe version {version} Copyright vendor\n".encode()


def _forged_copy(binary: ValidatedFfprobeBinary, **updates: object) -> ValidatedFfprobeBinary:
    forged = object.__new__(ValidatedFfprobeBinary)
    for slot in ValidatedFfprobeBinary.__slots__:
        object.__setattr__(forged, slot, updates.get(slot, getattr(binary, slot)))
    return forged


def test_local_ffprobe_811_report_shape_is_accepted() -> None:
    captured = (
        b"ffprobe version 8.1.1-full_build-www.gyan.dev "
        b"Copyright (c) 2007-2026 the FFmpeg developers\n"
        b"built with gcc 15.2.0 (Rev13, Built by MSYS2 project)\n"
        b"configuration: --enable-gpl --enable-version3 --enable-static "
        b"--disable-w32threads --enable-whisper\n"
        b"libavutil      60. 26.101 / 60. 26.101\n"
        b"libavcodec     62. 28.101 / 62. 28.101\n"
        b"libavformat    62. 12.101 / 62. 12.101\n"
        b"libavdevice    62.  3.101 / 62.  3.101\n"
        b"libavfilter    11. 14.101 / 11. 14.101\n"
        b"libswscale      9.  5.101 /  9.  5.101\n"
        b"libswresample   6.  3.101 /  6.  3.101\n"
    )
    parsed = parse_ffprobe_version(captured)
    assert isinstance(parsed, VersionParsed)
    assert parsed.version.semantic_version == SemanticVersion(8, 1, 1)
    assert parsed.version.raw_output.encode() == captured


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_report_contract_accepts_optional_unknown_and_reordered_lines(ending: str) -> None:
    lines = [
        "vendor banner: deterministic local build",
        "configuration: --enable-gpl",
        "ffprobe version 8.1.1-vendor/build+local Copyright vendor",
        "future ffprobe field: retained exactly",
        "built with clang 20",
    ]
    raw = (ending.join(lines) + ending).encode()
    parsed = parse_ffprobe_version(raw)
    assert isinstance(parsed, VersionParsed)
    assert parsed.version.compiler_line == "built with clang 20"
    assert parsed.version.configuration_line == "configuration: --enable-gpl"
    assert parsed.version.libraries == ()
    assert parsed.version.raw_output.encode() == raw
    assert parsed.version.build_suffix == "-vendor/build+local"


def test_mixed_lf_crlf_many_lines_and_long_line_remain_bounded_and_valid() -> None:
    unknown = "x" * 70_000
    lines = [f"vendor-{index}" for index in range(100)]
    text = "\r\n".join(lines[:50]) + "\n" + "\n".join(lines[50:])
    raw = (text + "\n" + unknown + "\nffprobe version 9.0.0-future\n").encode()
    assert len(raw) < VERSION_OUTPUT_LIMIT
    parsed = parse_ffprobe_version(raw)
    assert isinstance(parsed, VersionParsed)
    assert parsed.version.raw_output.encode() == raw


@pytest.mark.parametrize(
    "raw",
    [
        b"vendor only\n",
        _minimal_report() + _minimal_report(),
        _minimal_report("8.1.1") + _minimal_report("9.0.0"),
        _minimal_report("8.1"),
        _minimal_report("08.1.1"),
        _minimal_report() + b"unsafe\x00line\n",
        _minimal_report().replace(b"\n", b"\r", 1),
        b"\xff",
        b"x" * (VERSION_OUTPUT_LIMIT + 1),
    ],
    ids=lambda raw: f"bounded-{len(raw)}-bytes" if len(raw) > 80 else None,
)
def test_report_contract_rejects_missing_ambiguous_or_unsafe_information(raw: bytes) -> None:
    rejected = parse_ffprobe_version(raw)
    assert isinstance(rejected, VersionRejected)
    assert rejected.error.code is ProbeErrorCode.VERSION_OUTPUT


def test_nonempty_stderr_is_bounded_diagnostic_evidence_not_a_version_source(
    fake_port: FakePort,
) -> None:
    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    stderr = b"ffprobe version 1.0.0 is only a diagnostic claim"
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(VERSION_TEXT.encode(), stderr))),
    )
    assert isinstance(result, BinaryValidated)
    assert result.binary.version.semantic_version == SemanticVersion(8, 1, 1)
    assert result.binary.version_stderr_output == stderr


def test_minimum_policy_boundaries_and_maximum_representable_version() -> None:
    expected = {
        SemanticVersion(6, 9, 9): False,
        SemanticVersion(7, 0, 0): True,
        SemanticVersion(7, 0, 1): True,
        SemanticVersion(8, 1, 1): True,
        SemanticVersion(9, 0, 0): True,
        SemanticVersion(
            MAX_VERSION_COMPONENT,
            MAX_VERSION_COMPONENT,
            MAX_VERSION_COMPONENT,
        ): True,
    }
    for version, supported in expected.items():
        decision = evaluate_ffprobe_support(version, PRODUCT_FFPROBE_SUPPORT_POLICY)
        assert isinstance(decision, VersionSupported) is supported
        assert isinstance(decision, VersionUnsupported) is (not supported)


def test_validator_reports_valid_but_unsupported_version_without_parser_reclassification(
    fake_port: FakePort,
) -> None:
    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    version_6 = VERSION_TEXT.replace("8.1.1-test-build", "6.9.9-test-build").encode()
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(version_6, b""))),
    )
    assert not isinstance(result, BinaryValidated)
    assert result.error.code is ProbeErrorCode.UNSUPPORTED_VERSION
    assert result.error.phase == "version_policy"


def test_policy_identity_binds_revision_type_and_content() -> None:
    policy_a = FfprobeSupportPolicy(minimum_version=SemanticVersion(7, 0, 0))
    policy_b = FfprobeSupportPolicy(minimum_version=SemanticVersion(8, 0, 0))
    assert policy_a.revision == policy_b.revision == "1.0"
    assert policy_a.policy_type == policy_b.policy_type
    assert policy_a.canonical_bytes != policy_b.canonical_bytes
    assert policy_a.identity != policy_b.identity
    assert policy_a.identity.content_sha256 != policy_b.identity.content_sha256
    assert FfprobeSupportPolicy() == policy_a
    assert FfprobeSupportPolicy().identity == policy_a.identity


def test_policy_runtime_validation_and_safe_copy_pickle_replace() -> None:
    policy = FfprobeSupportPolicy()
    assert copy.copy(policy) == policy
    assert copy.deepcopy(policy) == policy
    assert pickle.loads(pickle.dumps(policy)) == policy
    replacement = replace(policy, minimum_version=SemanticVersion(8, 0, 0))
    assert replacement.identity != policy.identity
    with pytest.raises(ValueError):
        FfprobeSupportPolicy(revision="01.0")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        FfprobeSupportPolicy(policy_type="exact_versions")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FfprobeSupportPolicy(minimum_version=(7, 0, 0))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        FfprobeSupportPolicy(versions=(SemanticVersion(7, 0, 0),))  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        FfprobeSupportPolicyIdentity("1.0", "minimum_semantic_version", "x" * 64)
    with pytest.raises(ValueError):
        FfprobeSupportPolicyIdentity(  # type: ignore[arg-type]
            "01.0", "minimum_semantic_version", "0" * 64
        )
    with pytest.raises(ValueError):
        FfprobeSupportPolicyIdentity(  # type: ignore[arg-type]
            "1.0", "exact_versions", "0" * 64
        )
    with pytest.raises(TypeError):
        policy.supports((7, 0, 0))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        evaluate_ffprobe_support(SemanticVersion(7, 0, 0), object())  # type: ignore[arg-type]


def test_out_of_range_optional_library_components_remain_only_raw_evidence() -> None:
    raw = _minimal_report() + b"libavutil 9999999999.1.1 / 1.1.1\n"
    parsed = parse_ffprobe_version(raw)
    assert isinstance(parsed, VersionParsed)
    assert parsed.version.libraries == ()
    assert parsed.version.raw_output.encode() == raw


def test_product_parser_and_validator_expose_no_policy_injection_parameter() -> None:
    assert tuple(inspect.signature(parse_ffprobe_version).parameters) == ("raw",)
    assert "policy" not in inspect.signature(validate_ffprobe_binary).parameters
    assert "matrix" not in inspect.signature(validate_ffprobe_binary).parameters


def test_capability_bound_to_policy_a_fails_closed_under_policy_b_identity(
    fake_port: FakePort,
    validated_binary: ValidatedFfprobeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    policy_b = FfprobeSupportPolicy(minimum_version=SemanticVersion(8, 0, 0))
    monkeypatch.setattr(binary_module.hmac, "compare_digest", lambda *_args: True)
    forged = _forged_copy(
        validated_binary,
        support_policy_revision=policy_b.identity.revision,
        support_policy_type=policy_b.identity.policy_type,
        support_policy_digest=policy_b.identity.content_sha256,
    )
    assert not _capability_is_authentic(forged)
    prelaunch = _open_verified_binary_for_launch(
        forged,
        NativeBinaryTrustPort(fake_port),
    )
    assert isinstance(prelaunch, BinaryInspectionFailed)
    assert prelaunch.error.phase == "binary_policy_binding"


def test_policy_identity_access_and_unsupported_forged_capability_fail_closed(
    validated_binary: ValidatedFfprobeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert not _capability_policy_is_current(object())  # type: ignore[arg-type]

    parsed = parse_ffprobe_version(_minimal_report("6.9.9-vendor"))
    assert isinstance(parsed, VersionParsed)
    monkeypatch.setattr(
        "matrix_auto_cutter.phase2.probe.binary.hmac.compare_digest", lambda *_: True
    )
    forged = _forged_copy(
        validated_binary,
        raw_version_output=parsed.version.raw_output,
        version=parsed.version,
    )
    assert not _capability_is_authentic(forged)


def test_reflective_product_policy_mutation_fails_closed_during_validation(
    fake_port: FakePort,
    validated_binary: ValidatedFfprobeBinary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid_identity(_policy: FfprobeSupportPolicy) -> FfprobeSupportPolicyIdentity:
        raise ValueError("reflectively corrupted policy")

    monkeypatch.setattr(FfprobeSupportPolicy, "identity", property(invalid_identity))
    assert not _capability_is_authentic(validated_binary)

    node = fake_port.add_file(r"C:\Tools\second-ffprobe.exe", b"trusted-binary")
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert not isinstance(result, BinaryValidated)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED
    assert result.error.phase == "binary_policy_contract"


def test_validator_capability_and_prelaunch_share_exact_product_policy_identity(
    fake_port: FakePort,
) -> None:
    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert isinstance(result, BinaryValidated)
    identity = PRODUCT_FFPROBE_SUPPORT_POLICY.identity
    assert result.binary.support_policy_revision == identity.revision
    assert result.binary.support_policy_type == identity.policy_type
    assert result.binary.support_policy_digest == identity.content_sha256
    assert _capability_is_authentic(result.binary)
    held = _open_verified_binary_for_launch(
        result.binary,
        NativeBinaryTrustPort(fake_port),
    )
    assert not isinstance(held, BinaryInspectionFailed)
    assert held.close() is None


def test_module_policy_reassignment_cannot_change_the_lexically_bound_product_policy(
    fake_port: FakePort,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    monkeypatch.setattr(
        binary_module,
        "PRODUCT_FFPROBE_SUPPORT_POLICY",
        FfprobeSupportPolicy(minimum_version=SemanticVersion(9, 0, 0)),
    )
    version_7 = VERSION_TEXT.replace("8.1.1-test-build", "7.0.0-test-build").encode()
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(version_7, b""))),
    )
    assert isinstance(result, BinaryValidated)
    assert result.binary.version.semantic_version == SemanticVersion(7, 0, 0)
    assert (
        result.binary.support_policy_digest
        == PRODUCT_FFPROBE_SUPPORT_POLICY.identity.content_sha256
    )
