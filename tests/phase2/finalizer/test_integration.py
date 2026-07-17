from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PureWindowsPath
from uuid import uuid4

from tests.phase2.finalizer.conftest import RUN_ID, SESSION_ID, add_validated_file, journal_bytes
from tests.phase2.source_confirmation.conftest import make_case

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.finalizer import (
    FinalizationRequest,
    Finalized,
    FinalizerPorts,
    JournalInputPaths,
    JournalInputProfile,
    RecoveryRequest,
    finalize,
    recover,
)
from matrix_auto_cutter.phase2.source_confirmation import SourceConfirmed, confirm_source


def test_authentic_confirmation_finalize_idempotent_and_recover() -> None:
    case = make_case()
    try:
        confirmed = confirm_source(case.ports, case.request, CancellationToken())
        assert isinstance(confirmed, SourceConfirmed)
        journal_path = add_validated_file(
            case.port,
            r"C:\Input\recording.ndjson",
            journal_bytes(),
        )
        ports = FinalizerPorts(
            case.port,
            lambda: datetime(2026, 7, 17, 12, tzinfo=UTC),
            uuid4,
        )
        request = FinalizationRequest(
            case.project,
            RUN_ID,
            JournalInputProfile.LEGACY,
            JournalInputPaths(journal_path),
            confirmed.confirmed_source,
            SESSION_ID,
        )
        first = finalize(ports, request, CancellationToken())
        assert isinstance(first, Finalized)
        assert not first.idempotent
        assert PureWindowsPath(first.sidecar.canonical_path).name == "source.obs-events.json"
        source_before = bytes(case.port.nodes[case.port._key(r"C:\Sources\source.mp4")].data)

        second = finalize(ports, request, CancellationToken())
        assert isinstance(second, Finalized)
        assert second.idempotent
        assert second.sidecar == first.sidecar

        receipt_key = case.port._key(first.receipt.canonical_path)
        state_key = case.port._key(first.state.canonical_path)
        del case.port.nodes[receipt_key]
        del case.port.nodes[state_key]
        recovered = recover(
            ports,
            RecoveryRequest(
                case.project,
                first.sidecar.canonical_path,
                JournalInputProfile.LEGACY,
                RUN_ID,
                SESSION_ID,
                JournalInputPaths(journal_path),
                confirmed.confirmed_source,
            ),
            CancellationToken(),
        )
        assert isinstance(recovered, Finalized)
        assert recovered.receipt is not None
        assert recovered.state is not None
        assert (
            bytes(case.port.nodes[case.port._key(r"C:\Sources\source.mp4")].data) == source_before
        )
        assert not any(".TMP." in key for key in case.port.nodes)
    finally:
        case.close()
