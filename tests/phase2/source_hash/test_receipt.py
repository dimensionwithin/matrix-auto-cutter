from __future__ import annotations

import copy
import json
import pickle
from threading import Event, Thread

import pytest
from tests.phase2.source_hash.conftest import (
    HASH_RUN_ID,
    PROJECT_ID,
    PUBLISH_OPERATION_ID,
    make_hash_case,
)

from matrix_auto_cutter.phase2.cancellation import CancellationToken
from matrix_auto_cutter.phase2.pathing import (
    PathRole,
    PathValidated,
    ValidatedPath,
    ValidatedWorkspaceRoot,
    validate_path,
    validate_workspace_root,
)
from matrix_auto_cutter.phase2.source_hash import (
    MAX_HASH_RECEIPT_BYTES,
    HashCompleted,
    HashFailure,
    HashIoError,
    HashReceipt,
    HashReceiptConflict,
    HashReceiptPublishCancelled,
    HashReceiptPublished,
    HashReceiptPublishIoError,
    hash_receipt_bytes,
    parse_hash_receipt_bytes,
    publish_hash_receipt,
    receipt_from_completed,
)
from matrix_auto_cutter.phase2.source_hash.contracts import HashErrorCategory, HashErrorCode


def _completed():
    case = make_hash_case(b"abc")
    result = case.run(block_size=2)
    assert isinstance(result, HashCompleted)
    return case, result


def _target(case) -> ValidatedPath:
    case.port.make_tree(r"C:\Workspace\identity\candidate")
    root = validate_workspace_root(case.port, r"C:\Workspace")
    assert isinstance(root, ValidatedWorkspaceRoot)
    target = validate_path(
        case.port,
        r"C:\Workspace\identity\candidate\hash-receipt.json",
        PathRole.WORKSPACE_INTERNAL,
        workspace_root=root,
    )
    assert isinstance(target, PathValidated)
    return target.path


def test_receipt_has_exact_canonical_utf8_bytes_and_bindings() -> None:
    case, completed = _completed()
    receipt = receipt_from_completed(completed)
    data = hash_receipt_bytes(receipt)
    expected = (
        '{"artifact_type":"source_hash_receipt","block_size_bytes":2,'
        f'"bytes_read":3,"file_id":"{receipt.file_id}",'
        '"file_id_scheme":"file_id_128","hash_algorithm":"sha256",'
        '"hash_algorithm_version":"1.0",'
        '"hash_contract_version":"lease_bound_source_hash/1.0",'
        f'"hash_run_id":"{HASH_RUN_ID}",'
        '"lease_id":"2e157a84-2e31-49d9-b64e-494c24f8f612",'
        f'"project_id":"{PROJECT_ID}","s0_size_bytes":3,'
        f'"s0_snapshot_key":"{completed.s0.snapshot_key}",'
        f'"s4_snapshot_key":"{completed.s4.snapshot_key}",'
        '"schema_version":"1.0",'
        '"sha256":"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",'
        '"validation_epoch":"2e157a84-2e31-49d9-b64e-494c24f8f612",'
        '"volume_id":"0000000000000001"}\n'
    ).encode()
    assert data == expected
    assert data.endswith(b"\n") and not data.startswith(b"\xef\xbb\xbf")
    assert parse_hash_receipt_bytes(data) == receipt
    case.lease.close()


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("unknown", 1),
        ("schema_version", "9.0"),
        ("sha256", "A" * 64),
        ("s4_snapshot_key", "0" * 64),
        ("bytes_read", 2),
    ],
)
def test_receipt_rejects_unknown_version_bad_hash_and_binding_mismatch(
    mutation: str, value: object
) -> None:
    case, completed = _completed()
    payload = completed.receipt.model_dump()
    payload[mutation] = value
    with pytest.raises(ValueError):
        HashReceipt.model_validate_json(json.dumps(payload, separators=(",", ":")))
    case.lease.close()


def test_receipt_rejects_missing_fields_noncanonical_bytes_and_oversize() -> None:
    case, completed = _completed()
    payload = completed.receipt.model_dump()
    payload.pop("file_id")
    with pytest.raises(ValueError):
        HashReceipt.model_validate_json(json.dumps(payload, separators=(",", ":")))
    with pytest.raises(ValueError):
        parse_hash_receipt_bytes(hash_receipt_bytes(completed.receipt)[:-1])
    with pytest.raises(ValueError):
        parse_hash_receipt_bytes(b"x" * (MAX_HASH_RECEIPT_BYTES + 1))
    case.lease.close()


def test_hash_completed_and_receipt_authority_cannot_be_forged_or_copied() -> None:
    with pytest.raises(TypeError):
        HashCompleted()
    case, completed = _completed()
    with pytest.raises(TypeError):
        copy.copy(completed)
    with pytest.raises(TypeError):
        copy.deepcopy(completed)
    with pytest.raises(TypeError):
        pickle.dumps(completed)
    with pytest.raises(AttributeError):
        completed._receipt = completed.receipt
    forged = object.__new__(HashCompleted)
    with pytest.raises(TypeError):
        receipt_from_completed(forged)
    case.lease.close()


def test_create_if_absent_idempotence_conflict_and_no_replace() -> None:
    case, completed = _completed()
    target = _target(case)
    case.port.replace_file = lambda *args: pytest.fail("immutable receipt attempted replace")
    first = publish_hash_receipt(
        case.port,
        target,
        completed,
        CancellationToken(),
        operation_id=PUBLISH_OPERATION_ID,
    )
    assert isinstance(first, HashReceiptPublished) and first.status == "published"
    original = bytes(case.port.nodes[case.port._key(target.canonical_dos_path)].data)

    second = publish_hash_receipt(
        case.port,
        target,
        completed,
        CancellationToken(),
        operation_id=PUBLISH_OPERATION_ID,
    )
    assert isinstance(second, HashReceiptPublished) and second.status == "idempotent"

    other = case.run(hash_run_id="11111111-1111-4111-8111-111111111111", block_size=2)
    assert isinstance(other, HashCompleted)
    conflict = publish_hash_receipt(
        case.port,
        target,
        other,
        CancellationToken(),
        operation_id=PUBLISH_OPERATION_ID,
    )
    assert isinstance(conflict, HashReceiptConflict)
    assert bytes(case.port.nodes[case.port._key(target.canonical_dos_path)].data) == original
    assert not any(".~matrix-2a-" in node.path for node in case.port.nodes.values())
    assert not case.port.failures.get("ReplaceFileW")
    case.lease.close()


def test_existing_malformed_or_oversized_target_is_conflict_and_unchanged() -> None:
    for existing in (b"not-json\n", b"x" * (MAX_HASH_RECEIPT_BYTES + 1)):
        case, completed = _completed()
        target = _target(case)
        case.port.add_file(target.canonical_dos_path, existing)
        result = publish_hash_receipt(
            case.port,
            target,
            completed,
            CancellationToken(),
            operation_id=PUBLISH_OPERATION_ID,
        )
        assert isinstance(result, HashReceiptConflict)
        assert bytes(case.port.nodes[case.port._key(target.canonical_dos_path)].data) == existing
        case.lease.close()


def test_receipt_publish_cancellation_and_commit_race() -> None:
    case, completed = _completed()
    target = _target(case)
    cancelled = CancellationToken()
    cancelled.cancel()
    assert isinstance(
        publish_hash_receipt(case.port, target, completed, cancelled),
        HashReceiptPublishCancelled,
    )

    entered = Event()
    proceed = Event()

    class RaceToken(CancellationToken):
        def begin_irreversible_commit(self):
            entered.set()
            assert proceed.wait(2)
            return super().begin_irreversible_commit()

    token = RaceToken()
    output = []
    thread = Thread(
        target=lambda: output.append(
            publish_hash_receipt(
                case.port,
                target,
                completed,
                token,
                operation_id=PUBLISH_OPERATION_ID,
            )
        )
    )
    thread.start()
    assert entered.wait(2)
    token.cancel()
    proceed.set()
    thread.join(2)
    assert isinstance(output[0], HashReceiptPublishCancelled)
    assert case.port._key(target.canonical_dos_path) not in case.port.nodes
    case.lease.close()


def test_late_publish_cancel_does_not_revoke_success_and_cleanup_is_secondary() -> None:
    case, completed = _completed()
    target = _target(case)
    token = CancellationToken()
    first = publish_hash_receipt(case.port, target, completed, token)
    assert isinstance(first, HashReceiptPublished)
    assert token.cancel()
    assert first.receipt == completed.receipt

    case.port.failures["SetFileInformationByHandle"] = [944]
    idempotent = publish_hash_receipt(case.port, target, completed, CancellationToken())
    assert isinstance(idempotent, HashReceiptPublished)
    assert idempotent.status == "idempotent"
    assert idempotent.cleanup_diagnostics[0].win32_code == 944
    case.lease.close()


def test_receipt_cannot_be_published_from_error_or_invalid_target() -> None:
    case, completed = _completed()
    failure = HashIoError(
        HashFailure(HashErrorCode.IO, HashErrorCategory.IO, "hash.read", "failed")
    )
    with pytest.raises(TypeError):
        receipt_from_completed(failure)
    with pytest.raises(TypeError):
        publish_hash_receipt(  # type: ignore[arg-type]
            case.port,
            _target(case),
            failure,
            CancellationToken(),
        )
    wrong = case.lease.source_path
    result = publish_hash_receipt(case.port, wrong, completed, CancellationToken())
    assert isinstance(result, HashReceiptPublishIoError)
    case.lease.close()
