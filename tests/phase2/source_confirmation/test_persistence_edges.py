from __future__ import annotations

import hashlib

from tests.phase2.source_confirmation.conftest import make_case

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.source_confirmation import (
    ArtifactReference,
    SourceConfirmed,
    confirm_source,
    parse_media_probe_bytes,
)
from matrix_auto_cutter.phase2.source_confirmation.contracts import ConfirmationFailure
from matrix_auto_cutter.phase2.source_confirmation.persistence import (
    ArtifactConflict,
    ArtifactIoFailure,
    ArtifactPublishCancelled,
    artifact_target,
    publish_artifact,
    read_artifact,
)


def _media(case):
    result = confirm_source(case.ports, case.request, CancellationToken())
    assert isinstance(result, SourceConfirmed)
    data = bytes(case.port.nodes[case.port._key(result.evidence.media_probe.canonical_path)].data)
    return parse_media_probe_bytes(data)


def test_artifact_target_trust_directory_and_filename_failures() -> None:
    untrusted = make_case()
    try:
        untrusted.project._invalidate_trust()
        assert isinstance(
            artifact_target(
                untrusted.port,
                untrusted.project,
                ("probe", "99999999-9999-4999-8999-999999999999"),
                "media-probe.json",
            ),
            ConfirmationFailure,
        )
    finally:
        untrusted.close()

    directory_failure = make_case()
    try:
        directory_failure.port.failures["CreateDirectoryW"] = [5]
        assert isinstance(
            artifact_target(
                directory_failure.port,
                directory_failure.project,
                ("probe", "99999999-9999-4999-8999-999999999999"),
                "media-probe.json",
            ),
            ConfirmationFailure,
        )
        assert isinstance(
            artifact_target(
                directory_failure.port,
                directory_failure.project,
                ("probe", directory_failure.request.probe_id),
                "bad:name.json",
            ),
            ConfirmationFailure,
        )
    finally:
        directory_failure.close()


def test_publish_size_cancel_existing_size_read_and_atomic_failures() -> None:
    case = make_case()
    try:
        media = _media(case)
        target = artifact_target(
            case.port,
            case.project,
            ("probe", "99999999-9999-4999-8999-999999999999"),
            "media-probe.json",
        )
        assert not isinstance(target, ConfirmationFailure)
        oversized = publish_artifact(
            case.port,
            target,
            media,
            1,
            parse_media_probe_bytes,
            CancellationToken(),
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(oversized, ArtifactIoFailure)

        cancelled = CancellationToken()
        cancelled.cancel()
        cancelled_result = publish_artifact(
            case.port,
            target,
            media,
            4 * 1024 * 1024,
            parse_media_probe_bytes,
            cancelled,
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(cancelled_result, ArtifactPublishCancelled)

        case.port.add_file(target.canonical_dos_path, b"x" * (4 * 1024 * 1024 + 1))
        existing_large = publish_artifact(
            case.port,
            target,
            media,
            4 * 1024 * 1024,
            parse_media_probe_bytes,
            CancellationToken(),
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(existing_large, ArtifactConflict)
    finally:
        case.close()

    read_failure = make_case()
    try:
        media = _media(read_failure)
        target = artifact_target(
            read_failure.port,
            read_failure.project,
            ("probe", "99999999-9999-4999-8999-999999999999"),
            "media-probe.json",
        )
        assert not isinstance(target, ConfirmationFailure)
        read_failure.port.add_file(target.canonical_dos_path, b"foreign\n")
        read_failure.port.failures["ReadFile"] = [955]
        result = publish_artifact(
            read_failure.port,
            target,
            media,
            4 * 1024 * 1024,
            parse_media_probe_bytes,
            CancellationToken(),
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(result, ArtifactIoFailure)
    finally:
        read_failure.close()

    write_failure = make_case()
    try:
        media = _media(write_failure)
        target = artifact_target(
            write_failure.port,
            write_failure.project,
            ("probe", "99999999-9999-4999-8999-999999999999"),
            "media-probe.json",
        )
        assert not isinstance(target, ConfirmationFailure)
        write_failure.port.failures["WriteFile"] = [956]
        result = publish_artifact(
            write_failure.port,
            target,
            media,
            4 * 1024 * 1024,
            parse_media_probe_bytes,
            CancellationToken(),
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(result, ArtifactIoFailure)

        next_target = artifact_target(
            write_failure.port,
            write_failure.project,
            ("probe", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            "media-probe.json",
        )
        assert not isinstance(next_target, ConfirmationFailure)
        parser_failure = publish_artifact(
            write_failure.port,
            next_target,
            media,
            4 * 1024 * 1024,
            lambda _data: (_ for _ in ()).throw(ValueError("forced parser")),
            CancellationToken(),
            artifact_name="media-probe",
            artifact_id=media.probe_id,
            artifact_type="media_probe",
        )
        assert isinstance(parser_failure, ArtifactIoFailure)
    finally:
        write_failure.close()


def test_referenced_read_path_io_digest_and_parser_failures() -> None:
    case = make_case()
    try:
        media = _media(case)
        outside = ArtifactReference(
            artifact_type="media_probe",
            artifact_id=media.probe_id,
            artifact_digest="0" * 64,
            canonical_path=r"C:\Outside\media-probe.json",
        )
        assert isinstance(
            read_artifact(
                case.port,
                case.project,
                outside,
                4 * 1024 * 1024,
                parse_media_probe_bytes,
            ),
            ConfirmationFailure,
        )

        missing = outside.model_copy(
            update={
                "canonical_path": case.project.project_directory.canonical_dos_path
                + r"\missing.json"
            }
        )
        assert isinstance(
            read_artifact(
                case.port,
                case.project,
                missing,
                4 * 1024 * 1024,
                parse_media_probe_bytes,
            ),
            ConfirmationFailure,
        )

        path = case.project.project_directory.canonical_dos_path + r"\malformed.json"
        malformed = b"not-json\n"
        case.port.add_file(path, malformed)
        wrong_digest = ArtifactReference(
            artifact_type="media_probe",
            artifact_id=media.probe_id,
            artifact_digest="0" * 64,
            canonical_path=path,
        )
        assert isinstance(
            read_artifact(
                case.port,
                case.project,
                wrong_digest,
                4 * 1024 * 1024,
                parse_media_probe_bytes,
            ),
            ConfirmationFailure,
        )
        parse_failure = wrong_digest.model_copy(
            update={"artifact_digest": hashlib.sha256(malformed).hexdigest()}
        )
        assert isinstance(
            read_artifact(
                case.port,
                case.project,
                parse_failure,
                4 * 1024 * 1024,
                parse_media_probe_bytes,
            ),
            ConfirmationFailure,
        )
    finally:
        case.close()
