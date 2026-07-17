from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread

import pytest
from tests.phase2.source_confirmation.conftest import ambiguous_streams, make_case

import matrix_auto_cutter.phase2.source_confirmation.orchestrator as orchestrator
from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe import (
    ProbeErrorCode,
    ProbeTimeout,
    ProcessDiagnostics,
)
from matrix_auto_cutter.phase2.probe.errors import probe_error
from matrix_auto_cutter.phase2.source_confirmation import (
    ArtifactReference,
    SourceConfirmationCancelled,
    SourceConfirmationFailed,
    SourceConfirmed,
    SourceInvalidated,
    confirm_source,
)
from matrix_auto_cutter.phase2.win32_port import Win32Failure


def test_close_during_probe_waits_for_complete_usage_and_prevents_late_commit() -> None:
    case = make_case()
    entered = Event()
    proceed = Event()
    close_done = Event()
    original_result = case.process.result

    def block_probe(_spec, _token) -> None:
        entered.set()
        assert proceed.wait(5)

    case.process.callback = block_probe
    outputs = []
    worker = Thread(
        target=lambda: outputs.append(confirm_source(case.ports, case.request, CancellationToken()))
    )
    worker.start()
    assert entered.wait(5)
    closer = Thread(target=lambda: (case.request.lease.close(), close_done.set()))
    closer.start()
    assert not close_done.wait(0.05)
    assert case.process.result is original_result
    proceed.set()
    worker.join(5)
    closer.join(5)
    assert isinstance(outputs[0], SourceConfirmationFailed)
    assert outputs[0].error.code.value == "E_SOURCE_CONFIRMATION_LEASE"
    assert close_done.is_set()
    assert not worker.is_alive() and not closer.is_alive()


def test_cancel_during_probe_preserves_probe_cancellation_and_never_hashes() -> None:
    case = make_case()
    token = CancellationToken()
    case.process.callback = lambda _spec, _token: token.cancel()
    try:
        result = confirm_source(case.ports, case.request, token)
        assert isinstance(result, SourceConfirmationCancelled)
        assert result.error.code is ProbeErrorCode.CANCELLED
        assert case.port.hash_read_count == 0
    finally:
        case.close()


def test_probe_timeout_and_binary_exchange_remain_probe_failures() -> None:
    timeout_case = make_case()
    try:
        error = probe_error(
            ProbeErrorCode.TIMEOUT,
            ErrorCategory.IO,
            "process_wait",
            "timeout",
        )
        timeout_case.process.result = ProbeTimeout(error, ProcessDiagnostics(b"", b""))
        timeout = confirm_source(
            timeout_case.ports,
            timeout_case.request,
            CancellationToken(),
        )
        assert isinstance(timeout, SourceConfirmationFailed)
        assert timeout.error.code is ProbeErrorCode.TIMEOUT
    finally:
        timeout_case.close()

    changed_case = make_case()
    try:
        binary_node = changed_case.port.nodes[
            changed_case.port._key(changed_case.request.binary.canonical_dos_path)
        ]
        binary_node.data.extend(b"changed")
        changed = confirm_source(
            changed_case.ports,
            changed_case.request,
            CancellationToken(),
        )
        assert isinstance(changed, SourceConfirmationFailed)
        assert changed.error.code is ProbeErrorCode.BINARY_CHANGED
        assert changed_case.process.calls is None
    finally:
        changed_case.close()


@pytest.mark.parametrize("kind", ["s3_changed", "s3_io", "hash_io", "early_eof", "s4", "s5"])
def test_recheck_and_hash_failures_linearize_without_later_authority(kind: str) -> None:
    case = make_case()
    try:
        node = case.port.nodes[case.port._key(case.request.lease.source_path.canonical_dos_path)]
        if kind == "s3_changed":
            case.port.snapshot_callbacks[4] = lambda: setattr(
                node, "write_time", node.write_time + 1
            )
        elif kind == "s3_io":
            case.port.snapshot_errors[4] = Win32Failure(811, "query", "S3 failed")
        elif kind == "hash_io":
            case.port.read_plan = [Win32Failure(812, "ReadFile", "hash failed")]
        elif kind == "early_eof":
            case.port.read_plan = [b""]
        elif kind == "s4":
            case.port.snapshot_callbacks[5] = lambda: setattr(
                node, "change_time", node.change_time + 1
            )
        else:
            case.port.snapshot_callbacks[6] = lambda: setattr(
                node, "attributes", node.attributes ^ 0x20
            )
        result = confirm_source(case.ports, case.request, CancellationToken())
        if kind in {"s3_changed", "s4", "s5"}:
            assert isinstance(result, SourceInvalidated)
            assert result.error.code.value == "E_SOURCE_CHANGED"
        else:
            assert isinstance(result, SourceConfirmationFailed)
        if kind.startswith("s3"):
            assert case.port.hash_read_count == 0
        assert not hasattr(result, "confirmed_source")
    finally:
        case.close()


def test_cancel_after_evidence_publish_but_before_capability_commit(monkeypatch) -> None:
    case = make_case()
    token = CancellationToken()
    original = orchestrator.publish_artifact

    def publish_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("artifact_type") == "source_identity_evidence":
            token.cancel()
        return result

    monkeypatch.setattr(orchestrator, "publish_artifact", publish_then_cancel)
    try:
        result = confirm_source(case.ports, case.request, token)
        assert isinstance(result, SourceConfirmationCancelled)
        evidence_paths = [
            node.path
            for node in case.port.nodes.values()
            if node.path.endswith("source-identity-evidence.json")
        ]
        assert len(evidence_paths) == 1
        assert not hasattr(result, "confirmed_source")
    finally:
        case.close()


def test_baseexception_always_releases_lease_usage(monkeypatch) -> None:
    case = make_case()
    monkeypatch.setattr(
        orchestrator,
        "revalidate_lease_path",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt("primary")),
    )
    with pytest.raises(KeyboardInterrupt, match="primary"):
        confirm_source(case.ports, case.request, CancellationToken())
    assert case.request.lease.close() == ()
    assert case.request.lease.closed


@pytest.mark.parametrize(
    "stage",
    ("probe", "s3", "assignment", "hash", "s5", "evidence_publish"),
)
def test_baseexception_at_each_integrated_stage_releases_usage(stage: str, monkeypatch) -> None:
    case = make_case(streams=ambiguous_streams() if stage == "assignment" else None)
    request = case.request

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt(stage)

    if stage == "probe":
        monkeypatch.setattr(orchestrator, "run_probe", interrupt)
    elif stage == "s3":
        monkeypatch.setattr(orchestrator, "_recheck", interrupt)
    elif stage == "assignment":
        request = replace(
            request,
            assignment=ArtifactReference(
                artifact_type="stream_assignment",
                artifact_id="44444444-4444-4444-8444-444444444444",
                artifact_digest="0" * 64,
                canonical_path=r"C:\Workspace\assignment.json",
            ),
        )
        monkeypatch.setattr(orchestrator, "validate_stream_assignment", interrupt)
    elif stage == "hash":
        monkeypatch.setattr(orchestrator, "hash_lease_source", interrupt)
    elif stage == "s5":
        original_recheck = orchestrator._recheck

        def interrupt_s5(*args, **kwargs):
            if args[3] == "s5":
                raise KeyboardInterrupt(stage)
            return original_recheck(*args, **kwargs)

        monkeypatch.setattr(orchestrator, "_recheck", interrupt_s5)
    else:
        original_publish = orchestrator.publish_artifact

        def interrupt_evidence(*args, **kwargs):
            if kwargs.get("artifact_type") == "source_identity_evidence":
                raise KeyboardInterrupt(stage)
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(orchestrator, "publish_artifact", interrupt_evidence)

    with pytest.raises(KeyboardInterrupt, match=stage):
        confirm_source(case.ports, request, CancellationToken())
    assert case.request.lease.close() == ()
    assert case.request.lease.closed


class NthCancellation(CancellationToken):
    def __init__(self, target: int) -> None:
        super().__init__()
        self.target = target
        self.checks = 0

    @property
    def is_cancelled(self) -> bool:
        self.checks += 1
        if self.checks == self.target:
            self.cancel()
        return super().is_cancelled


@pytest.mark.parametrize("target", range(1, 90))
def test_cancellation_at_every_integration_checkpoint(target: int) -> None:
    case = make_case()
    token = NthCancellation(target)
    try:
        result = confirm_source(case.ports, case.request, token)
        if isinstance(result, SourceConfirmationCancelled):
            assert token.is_cancelled
        else:
            assert isinstance(result, SourceConfirmed)
    finally:
        case.close()
