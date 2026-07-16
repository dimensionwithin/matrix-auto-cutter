from __future__ import annotations

import copy
import pickle
from dataclasses import replace

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import VERSION_TEXT, FakeProcessPort

from matrix_auto_cutter.phase2.artifacts import UnavailableIdentity
from matrix_auto_cutter.phase2.pathing import PathRole
from matrix_auto_cutter.phase2.probe import (
    PRODUCT_FFPROBE_SUPPORT_POLICY,
    BinaryValidated,
    BinaryValidationFailed,
    FfprobeCandidate,
    ProbeErrorCode,
    SemanticVersion,
    ValidatedFfprobeBinary,
    VersionParsed,
    VersionRejected,
    VersionSupported,
    evaluate_ffprobe_support,
    parse_ffprobe_version,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
    _open_verified_binary_for_launch,
)
from matrix_auto_cutter.phase2.win32_port import FILE_ATTRIBUTE_REPARSE_POINT


def test_exact_version_preserves_suffix_and_complete_evidence() -> None:
    result = parse_ffprobe_version(VERSION_TEXT.encode())
    assert isinstance(result, VersionParsed)
    assert result.version.semantic_version == SemanticVersion(8, 1, 1)
    assert result.version.build_suffix == "-test-build"
    assert result.version.compiler_line.startswith("built with gcc")
    assert result.version.configuration_line.endswith("--disable-network")
    assert [library.name for library in result.version.libraries] == [
        "libavutil",
        "libavcodec",
    ]
    assert result.version.raw_output == VERSION_TEXT


@pytest.mark.parametrize(
    "text",
    [
        VERSION_TEXT.replace("8.1.1", "8.1.2", 1),
        VERSION_TEXT.replace("8.1.1", "8.2.1", 1),
        VERSION_TEXT.replace("8.1.1", "9.1.1", 1),
    ],
)
def test_versions_at_or_above_documented_minimum_are_supported(text: str) -> None:
    result = parse_ffprobe_version(text.encode())
    assert isinstance(result, VersionParsed)
    assert isinstance(
        evaluate_ffprobe_support(
            result.version.semantic_version,
            PRODUCT_FFPROBE_SUPPORT_POLICY,
        ),
        VersionSupported,
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"\xef\xbb\xbf" + VERSION_TEXT.encode(),
        VERSION_TEXT.encode() + b"\xff",
        VERSION_TEXT.replace("ffprobe version ", "ffprobe versions ").encode(),
        (VERSION_TEXT + VERSION_TEXT).encode(),
        VERSION_TEXT.replace("8.1.1-test-build", "8.1-test-build").encode(),
    ],
)
def test_malformed_or_ambiguous_version_output(raw: bytes) -> None:
    result = parse_ffprobe_version(raw)
    assert isinstance(result, VersionRejected)
    assert result.error.code is ProbeErrorCode.VERSION_OUTPUT


def test_semantic_version_rejects_negative_and_formats() -> None:
    with pytest.raises(ValueError):
        SemanticVersion(-1, 0, 0)
    assert str(SemanticVersion(8, 1, 1)) == "8.1.1"


def test_binary_validation_binds_hash_path_ids_and_exact_start(fake_port: FakePort) -> None:
    node = fake_port.add_file(r"C:\Tools With Space\ffprobe.exe", b"trusted-binary")
    process = FakeProcessPort()
    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        process,
    )
    assert isinstance(result, BinaryValidated)
    assert (
        result.binary.sha256 == "94bfbc5b9a95c1e17ffb07b413f68ccd74601c45f1ed1d71dfa6c76aeebd10d1"
    )
    assert result.binary.file_id.value == node.file_id.hex()
    assert result.binary.volume_id.value == "0000000000000001"
    assert result.binary.support_policy_revision == "1.0"
    assert result.binary.support_policy_type == "minimum_semantic_version"
    assert (
        result.binary.support_policy_digest
        == PRODUCT_FFPROBE_SUPPORT_POLICY.identity.content_sha256
    )
    assert result.binary.version_stderr_output == b""
    assert process.calls is not None
    assert process.calls[0].application_path == node.path
    assert process.calls[0].arguments == (node.path, "-version")


def test_binary_exchange_during_version_is_rejected(fake_port: FakePort) -> None:
    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"old")

    def exchange(*_args: object) -> None:
        node.file_id = fake_port._new_id()
        node.data[:] = b"new"

    result = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(callback=exchange),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED


@pytest.mark.parametrize("field", ["file_id", "volume", "write_time", "change_time"])
def test_prelaunch_evidence_changes_are_rejected(
    fake_port: FakePort, validated_binary, field: str
) -> None:
    node = fake_port.nodes[fake_port._key(validated_binary.canonical_dos_path)]
    if field == "file_id":
        node.file_id = fake_port._new_id()
    elif field == "volume":
        node.volume += 1
    elif field == "write_time":
        node.write_time += 1
    else:
        node.change_time += 1
    result = _open_verified_binary_for_launch(validated_binary, NativeBinaryTrustPort(fake_port))
    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED


def test_hash_size_reparse_and_missing_file_id_fail_closed(
    fake_port: FakePort, validated_binary
) -> None:
    node = fake_port.nodes[fake_port._key(validated_binary.canonical_dos_path)]
    node.data.extend(b"x")
    changed = _open_verified_binary_for_launch(validated_binary, NativeBinaryTrustPort(fake_port))
    assert isinstance(changed, BinaryInspectionFailed)
    assert changed.error.code is ProbeErrorCode.BINARY_CHANGED

    node.data[:] = b"trusted-binary"
    node.attributes |= FILE_ATTRIBUTE_REPARSE_POINT
    reparse = _open_verified_binary_for_launch(validated_binary, NativeBinaryTrustPort(fake_port))
    assert isinstance(reparse, BinaryInspectionFailed)
    node.attributes &= ~FILE_ATTRIBUTE_REPARSE_POINT
    node.file_id = None
    missing = _open_verified_binary_for_launch(validated_binary, NativeBinaryTrustPort(fake_port))
    assert isinstance(missing, BinaryInspectionFailed)
    assert missing.error.code is ProbeErrorCode.BINARY_EVIDENCE


def _forged_copy(binary: ValidatedFfprobeBinary, **updates: object) -> ValidatedFfprobeBinary:
    forged = object.__new__(ValidatedFfprobeBinary)
    for slot in ValidatedFfprobeBinary.__slots__:
        object.__setattr__(forged, slot, updates.get(slot, getattr(binary, slot)))
    return forged


def test_capability_has_no_public_alternate_constructor(validated_binary) -> None:
    with pytest.raises(TypeError):
        ValidatedFfprobeBinary()
    with pytest.raises(TypeError):
        replace(validated_binary, sha256="0" * 64)
    with pytest.raises(TypeError):
        copy.copy(validated_binary)
    with pytest.raises(TypeError):
        copy.deepcopy(validated_binary)
    with pytest.raises(TypeError):
        validated_binary.path = validated_binary.path
    with pytest.raises(TypeError):
        pickle.dumps(validated_binary)


def test_capability_integrity_regression_has_no_importable_issuer() -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    assert not hasattr(binary_module, "_issue_capability")
    assert not any(
        callable(value) and "issue" in name and "capability" in name
        for name, value in vars(binary_module).items()
    )
    namespace: dict[str, object] = {}
    with pytest.raises(ImportError):
        exec(
            "from matrix_auto_cutter.phase2.probe.binary import _issue_capability",
            namespace,
        )


def test_manipulated_deserialization_clone_is_not_a_valid_capability(
    fake_port: FakePort, validated_binary
) -> None:
    result = _open_verified_binary_for_launch(
        _forged_copy(validated_binary), NativeBinaryTrustPort(fake_port)
    )
    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED


@pytest.mark.parametrize(
    "mutation",
    [
        "raw",
        "stderr",
        "version",
        "hash",
        "snapshot",
        "file_id",
        "top_level_conflict",
        "policy_revision",
        "policy_type",
        "policy_digest",
    ],
)
def test_every_capability_binding_is_authenticated(
    fake_port: FakePort, validated_binary, mutation: str
) -> None:
    if mutation == "raw":
        updates = {"raw_version_output": validated_binary.raw_version_output + "tampered"}
    elif mutation == "stderr":
        updates = {"version_stderr_output": b"tampered"}
    elif mutation == "version":
        updates = {
            "version": replace(
                validated_binary.version,
                semantic_version=SemanticVersion(8, 1, 2),
            )
        }
    elif mutation == "hash":
        updates = {"sha256": "0" * 64}
    elif mutation == "snapshot":
        updates = {
            "original_snapshot": replace(
                validated_binary.original_snapshot,
                last_write_time=replace(validated_binary.last_write_time, value=123),
            )
        }
    elif mutation == "file_id":
        updates = {"file_id": UnavailableIdentity()}
    elif mutation == "top_level_conflict":
        updates = {"size_bytes": validated_binary.size_bytes + 1}
    elif mutation == "policy_revision":
        updates = {"support_policy_revision": "0.9"}
    elif mutation == "policy_type":
        updates = {"support_policy_type": "exact_versions"}
    else:
        updates = {"support_policy_digest": "0" * 64}
    forged = _forged_copy(validated_binary, **updates)
    result = _open_verified_binary_for_launch(forged, NativeBinaryTrustPort(fake_port))
    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.code is ProbeErrorCode.BINARY_CHANGED


@pytest.mark.parametrize(
    "mutation",
    ["role", "canonical", "long_path", "contract"],
)
def test_binary_runtime_contract_is_internally_consistent(
    fake_port: FakePort, validated_binary, mutation: str
) -> None:
    if mutation == "role":
        binary = _forged_copy(
            validated_binary,
            path=replace(validated_binary.path, role=PathRole.WORKSPACE_INTERNAL),
        )
    elif mutation == "canonical":
        binary = _forged_copy(validated_binary, canonical_dos_path=r"C:\Other\ffprobe.exe")
    elif mutation == "long_path":
        binary = _forged_copy(validated_binary, long_path=r"\\?\C:\Other\ffprobe.exe")
    else:
        binary = _forged_copy(validated_binary, validation_contract_version="other/1.0")
    result = _open_verified_binary_for_launch(binary, NativeBinaryTrustPort(fake_port))
    assert isinstance(result, BinaryInspectionFailed)


def test_unavailable_identity_is_never_treated_as_proof(
    fake_port: FakePort, validated_binary
) -> None:
    altered = _forged_copy(validated_binary, file_id=UnavailableIdentity())
    result = _open_verified_binary_for_launch(altered, NativeBinaryTrustPort(fake_port))
    assert isinstance(result, BinaryInspectionFailed)


def test_legitimate_capability_passes_prelaunch_and_repr_is_handle_free(
    fake_port: FakePort, validated_binary
) -> None:
    inspection = _open_verified_binary_for_launch(
        validated_binary, NativeBinaryTrustPort(fake_port)
    )
    assert not isinstance(inspection, BinaryInspectionFailed)
    assert inspection.close() is None
    rendered = repr(validated_binary)
    assert "handle" not in rendered.casefold()
    assert "_seal" not in rendered


def test_public_probe_api_exports_no_handle_ownership() -> None:
    from dataclasses import fields, is_dataclass

    from matrix_auto_cutter.phase2 import probe

    forbidden = {
        "BinaryInspection",
        "BinaryInspectionFailed",
        "BinaryTrustPort",
        "NativeBinaryTrustPort",
        "OwnedHandle",
        "open_verified_binary_for_launch",
    }
    assert forbidden.isdisjoint(probe.__all__)
    assert not hasattr(probe, "open_verified_binary_for_launch")
    for name in probe.__all__:
        exported = getattr(probe, name)
        if isinstance(exported, type) and is_dataclass(exported):
            for public_field in fields(exported):
                assert "handle" not in public_field.name.casefold()
                assert "OwnedHandle" not in str(public_field.type)


def test_capability_verifier_defensive_consistency_branches(
    fake_port: FakePort, validated_binary, monkeypatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    monkeypatch.setattr(binary_module.hmac, "compare_digest", lambda *_args: True)
    raw_conflict = _forged_copy(
        validated_binary,
        raw_version_output=validated_binary.raw_version_output + "x",
    )
    assert not binary_module._capability_is_authentic(raw_conflict)

    version_conflict = _forged_copy(
        validated_binary,
        version=replace(
            validated_binary.version,
            semantic_version=SemanticVersion(8, 1, 2),
        ),
    )
    assert not binary_module._capability_is_authentic(version_conflict)
    assert not binary_module._capability_is_authentic(
        _forged_copy(validated_binary, validated_at_utc="2026-07-14T12:00:00")
    )
    assert not binary_module._capability_is_authentic(
        _forged_copy(validated_binary, validated_at_utc="not-a-time")
    )

    monkeypatch.setattr(binary_module, "_capability_is_authentic", lambda _binary: True)
    inconsistent = _forged_copy(validated_binary, size_bytes=validated_binary.size_bytes + 1)
    inspection = binary_module._open_verified_binary_for_launch(
        inconsistent, NativeBinaryTrustPort(fake_port)
    )
    assert isinstance(inspection, BinaryInspectionFailed)


@pytest.mark.parametrize("close_code", [None, 72])
def test_policy_rejection_closes_internal_prelaunch_handle(
    validated_binary, monkeypatch, close_code: int | None
) -> None:
    from tests.phase2.probe.test_coverage_edges import SequenceTrust, inspection_for

    from matrix_auto_cutter.phase2.probe import binary as binary_module

    monkeypatch.setattr(binary_module, "_capability_is_authentic", lambda _binary: True)
    forged = _forged_copy(validated_binary, support_policy_digest="0" * 64)
    result = binary_module._open_verified_binary_for_launch(
        forged,
        SequenceTrust([inspection_for(validated_binary, close_code=close_code)]),
    )
    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.phase == "binary_policy_binding"
    assert bool(result.error.secondary) is (close_code is not None)
