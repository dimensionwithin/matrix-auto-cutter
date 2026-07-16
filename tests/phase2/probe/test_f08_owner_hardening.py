from __future__ import annotations

import copy
import copyreg
import pickle
import sys
from dataclasses import replace

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import FakeProcessPort, issued_inspection_for

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe import FfprobeCandidate, ProbeFailed
from matrix_auto_cutter.phase2.probe import binary as binary_module
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryEvidence,
    BinaryInspection,
    BinaryInspectionFailed,
    NativeBinaryTrustPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.errors import ProbeErrorCode, probe_error
from matrix_auto_cutter.phase2.probe.runner import run_probe
from matrix_auto_cutter.phase2.snapshots import snapshot_file
from matrix_auto_cutter.phase2.win32_port import (
    FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ,
    GENERIC_READ,
    OPEN_EXISTING,
    HandleState,
    OwnedHandle,
    Win32Ok,
)


class SequenceTrust:
    def __init__(self, inspections: list[BinaryInspection]) -> None:
        self.inspections = inspections

    def inspect(self, _path):
        return self.inspections.pop(0)


def inspection_for(
    binary,
    calls: list[int],
    value: int,
    *,
    close_failure: int | BaseException | None = None,
    on_close=None,
) -> BinaryInspection:
    return issued_inspection_for(
        binary,
        calls,
        value=value,
        close_failure=close_failure,
        on_close=on_close,
    )


class EvidenceFailure(RuntimeError):
    pass


class DirectOwnerFailure(BaseException):
    pass


class DirectCloseFailure(BaseException):
    pass


def capture_restrictive_handles(
    fake_port: FakePort, monkeypatch: pytest.MonkeyPatch
) -> list[OwnedHandle]:
    """Observe only the native trust port's restrictive binary handles."""
    captured: list[OwnedHandle] = []
    original_open = fake_port.open_file

    def capture_open(long_path, desired_access, share_mode, creation_disposition, flags):
        opened = original_open(
            long_path,
            desired_access,
            share_mode,
            creation_disposition,
            flags,
        )
        if (
            isinstance(opened, Win32Ok)
            and share_mode == FILE_SHARE_READ
            and creation_disposition == OPEN_EXISTING
            and flags == FILE_FLAG_OPEN_REPARSE_POINT
        ):
            captured.append(opened.value)
        return opened

    monkeypatch.setattr(fake_port, "open_file", capture_open)
    return captured


class HostileEvidence:
    def __getattribute__(self, name: str):
        del name
        raise EvidenceFailure("evidence access must remain inside owner boundary")


def hostile_inspection_for(binary, calls: list[int], value: int) -> BinaryInspection:
    inspection = inspection_for(binary, calls, value)
    inspection.evidence = HostileEvidence()
    return inspection


def test_binary_inspection_rejects_copy_deepcopy_pickle_and_dataclass_replace(
    validated_binary,
) -> None:
    calls: list[int] = []
    inspection = inspection_for(validated_binary, calls, 801)
    initial_state = inspection._close_state
    native_value = inspection._handle.value

    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(inspection)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.deepcopy(inspection)
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        with pytest.raises(TypeError, match="cannot be serialized"):
            pickle.dumps(inspection, protocol=protocol)
    with pytest.raises(TypeError):
        replace(inspection)
    with pytest.raises(TypeError, match="state cannot be exported"):
        inspection.__getstate__()
    with pytest.raises(TypeError, match="state cannot be restored"):
        inspection.__setstate__({})
    with pytest.raises(TypeError, match="cannot be serialized"):
        inspection.__reduce__()
    with pytest.raises(TypeError, match="cannot be serialized"):
        inspection.__reduce_ex__(pickle.HIGHEST_PROTOCOL)
    with pytest.raises(TypeError, match="cannot be reconstructed"):
        inspection.__getnewargs__()
    with pytest.raises(TypeError, match="cannot be reconstructed"):
        inspection.__getnewargs_ex__()
    with pytest.raises(TypeError):
        vars(inspection)

    assert BinaryInspection not in copyreg.dispatch_table
    assert inspection._close_state is initial_state
    assert str(native_value) not in repr(inspection)
    assert "OwnedHandle" not in repr(inspection)

    assert inspection.evidence.sha256 == validated_binary.sha256
    assert inspection.close() is None
    assert calls == [801]


def test_binary_inspection_constructor_and_subclassing_are_not_issuance_paths(
    validated_binary,
) -> None:
    calls: list[int] = []
    candidate = OwnedHandle(820, lambda value: calls.append(value) or Win32Ok(None))

    for evidence in (validated_binary.original_snapshot, object()):
        with pytest.raises(TypeError, match="issued only by native inspection") as raised:
            BinaryInspection(evidence, candidate)
        assert "820" not in str(raised.value)
    with pytest.raises(TypeError, match="issued only by native inspection"):
        BinaryInspection(
            validated_binary.original_snapshot,
            candidate,
            _issuer_key=object(),
        )
    with pytest.raises(TypeError, match="cannot be subclassed"):

        class ForgedInspection(BinaryInspection):
            pass

    assert calls == []
    candidate.close()
    assert calls == [820]
    assert not hasattr(binary_module, "_build_binary_inspection_boundary")
    assert not any("issue_binary_inspection" in name for name in vars(binary_module))
    assert not any("issuer_key" in name for name in vars(binary_module))


def test_constructor_and_getstate_remain_blocked_after_all_close_outcomes(
    validated_binary,
) -> None:
    confirmed_calls: list[int] = []
    confirmed = inspection_for(validated_binary, confirmed_calls, 821)
    confirmed_handle = confirmed._handle
    confirmed_value = confirmed_handle.value
    with pytest.raises(TypeError, match="issued only by native inspection"):
        confirmed.__init__(confirmed.evidence, confirmed_handle)
    with pytest.raises(TypeError, match="issued only by native inspection"):
        BinaryInspection(confirmed.evidence, confirmed_handle)
    with pytest.raises(TypeError, match="issued only by native inspection"):
        BinaryInspection(object(), confirmed_handle)
    assert confirmed._close_state is binary_module._InspectionCloseState.OPEN
    assert confirmed.close() is None
    with pytest.raises(TypeError, match="issued only by native inspection") as confirmed_alias:
        BinaryInspection(confirmed.evidence, confirmed_handle)
    with pytest.raises(TypeError, match="state cannot be exported"):
        confirmed.__getstate__()
    assert str(confirmed_value) not in str(confirmed_alias.value)
    assert confirmed_calls == [821]

    unresolved_calls: list[int] = []
    unresolved = inspection_for(
        validated_binary,
        unresolved_calls,
        822,
        close_failure=91,
    )
    unresolved_handle = unresolved._handle
    unresolved_value = unresolved_handle.value
    assert unresolved.close() is not None
    with pytest.raises(TypeError, match="issued only by native inspection") as unresolved_alias:
        BinaryInspection(object(), unresolved_handle)
    with pytest.raises(TypeError, match="state cannot be exported"):
        unresolved.__getstate__()
    assert str(unresolved_value) not in str(unresolved_alias.value)
    assert unresolved_calls == [822]


def test_inspect_open_is_evidence_only_for_bound_and_unbound_calls(
    fake_port: FakePort, binary_path
) -> None:
    """The handle-accepting helper cannot transfer ownership or issue an inspection."""
    opened = fake_port.open_file(
        binary_path.long_path,
        GENERIC_READ,
        FILE_SHARE_READ,
        OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT,
    )
    assert isinstance(opened, Win32Ok)
    handle = opened.value
    native_value = handle.value
    close_attempts = dict(fake_port.close_attempts_by_handle)
    trust = NativeBinaryTrustPort(fake_port)

    first = trust._inspect_open(binary_path, handle)
    assert isinstance(first, BinaryEvidence)
    assert not isinstance(first, BinaryInspection)

    path_key, _offset = fake_port.handles[native_value]
    fake_port.handles[native_value] = (path_key, 0)
    second = NativeBinaryTrustPort._inspect_open(trust, binary_path, handle)
    assert isinstance(second, BinaryEvidence)
    assert second == first
    assert handle.value == native_value
    assert fake_port.close_attempts_by_handle == close_attempts

    assert handle.close() == Win32Ok(None)
    assert fake_port.close_attempts_by_handle[native_value] == 1


def test_inspect_open_cannot_issue_from_closed_or_unresolved_handles(
    fake_port: FakePort, binary_path
) -> None:
    """Direct helper calls stay non-owning after both terminal handle outcomes."""
    trust = NativeBinaryTrustPort(fake_port)
    for close_failure in (None, 91):
        inspection = trust.inspect(binary_path)
        assert isinstance(inspection, BinaryInspection)
        handle = inspection._handle
        native_value = handle.value
        if close_failure is not None:
            path_key, _offset = fake_port.handles[native_value]
            fake_port.close_results[path_key] = [close_failure]
        close_error = inspection.close()
        assert (close_error is None) is (close_failure is None)
        assert inspection._close_state is (
            binary_module._InspectionCloseState.CLOSE_CONFIRMED
            if close_failure is None
            else binary_module._InspectionCloseState.OWNERSHIP_UNRESOLVED
        )
        close_attempts = fake_port.close_attempts_by_handle[native_value]

        with pytest.raises(RuntimeError, match="native handle is closed"):
            trust._inspect_open(binary_path, handle)

        assert fake_port.close_attempts_by_handle[native_value] == close_attempts


@pytest.mark.parametrize(
    "primary",
    [
        TypeError("adapter type failure"),
        RuntimeError("adapter failure"),
        KeyboardInterrupt("stop"),
        SystemExit("exit"),
        DirectOwnerFailure("direct base exception"),
    ],
)
def test_pretransfer_helper_exception_preserves_primary_and_closes_once(
    fake_port: FakePort,
    binary_path,
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException,
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)

    def raise_from_helper(_self, _path, _handle):
        raise primary

    monkeypatch.setattr(NativeBinaryTrustPort, "_inspect_open", raise_from_helper)
    with pytest.raises(type(primary)) as raised:
        NativeBinaryTrustPort(fake_port).inspect(binary_path)

    assert raised.value is primary
    traceback = raised.value.__traceback__
    assert traceback is not None
    assert any(
        frame.tb_frame.f_code is raise_from_helper.__code__ for frame in iter_traceback(traceback)
    )
    assert len(captured) == 1
    handle = captured[0]
    native_value = handle._value
    assert handle.state is HandleState.CLOSE_SUCCEEDED
    assert fake_port.close_attempts_by_handle[native_value] == 1
    assert native_value not in fake_port.handles
    with pytest.raises(RuntimeError, match="closed twice"):
        handle.close()
    assert fake_port.close_attempts_by_handle[native_value] == 1


def iter_traceback(traceback):
    while traceback is not None:
        yield traceback
        traceback = traceback.tb_next


def test_pretransfer_wrong_helper_result_closes_once(
    fake_port: FakePort, binary_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)
    monkeypatch.setattr(NativeBinaryTrustPort, "_inspect_open", lambda *_args: object())

    with pytest.raises(TypeError, match="helper returned an invalid result"):
        NativeBinaryTrustPort(fake_port).inspect(binary_path)

    handle = captured[0]
    native_value = handle._value
    assert handle.state is HandleState.CLOSE_SUCCEEDED
    assert fake_port.close_attempts_by_handle[native_value] == 1
    assert native_value not in fake_port.handles


def test_pretransfer_evidence_builder_exception_closes_once(
    fake_port: FakePort, binary_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)
    primary = RuntimeError("evidence builder failed")

    def reject_evidence(*_args):
        raise primary

    monkeypatch.setattr(binary_module, "_evidence_from_info", reject_evidence)
    with pytest.raises(RuntimeError) as raised:
        NativeBinaryTrustPort(fake_port).inspect(binary_path)

    assert raised.value is primary
    handle = captured[0]
    native_value = handle._value
    assert handle.state is HandleState.CLOSE_SUCCEEDED
    assert fake_port.close_attempts_by_handle[native_value] == 1
    assert native_value not in fake_port.handles


@pytest.mark.parametrize(
    "close_failure",
    [
        91,
        RuntimeError("close exception"),
        KeyboardInterrupt("close interrupt"),
        SystemExit("close exit"),
        DirectCloseFailure("direct close base exception"),
    ],
)
def test_pretransfer_active_base_exception_survives_every_close_failure(
    fake_port: FakePort,
    binary_path,
    monkeypatch: pytest.MonkeyPatch,
    close_failure: int | BaseException,
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)
    primary = DirectOwnerFailure("pre-transfer primary")
    original_error = fake_port._error

    def raise_primary(_self, _path, handle):
        path_key, _offset = fake_port.handles[handle.value]
        if isinstance(close_failure, int):
            fake_port.close_results[path_key] = [close_failure]
        else:

            def close_raises(operation: str, default: int | None = None):
                if operation == "CloseHandle":
                    raise close_failure
                return original_error(operation, default)

            monkeypatch.setattr(fake_port, "_error", close_raises)
        raise primary

    monkeypatch.setattr(NativeBinaryTrustPort, "_inspect_open", raise_primary)
    with pytest.raises(DirectOwnerFailure) as raised:
        NativeBinaryTrustPort(fake_port).inspect(binary_path)

    assert raised.value is primary
    assert raised.value.__traceback__ is not None
    handle = captured[0]
    assert fake_port.close_attempts_by_handle[handle._value] == 1
    assert len(primary.__notes__) == 1
    if isinstance(close_failure, int):
        assert handle.state is HandleState.CLOSE_FAILED_OR_UNKNOWN
        assert "cleanup was unresolved" in primary.__notes__[0]
    else:
        assert handle.state is HandleState.OPEN
        assert type(close_failure).__name__ in primary.__notes__[0]


@pytest.mark.parametrize(
    "close_failure",
    [
        None,
        92,
        RuntimeError("close exception"),
        KeyboardInterrupt("close interrupt"),
        SystemExit("close exit"),
        DirectCloseFailure("direct close base exception"),
    ],
)
def test_structured_evidence_failure_remains_primary_for_every_close_outcome(
    fake_port: FakePort,
    binary_path,
    monkeypatch: pytest.MonkeyPatch,
    close_failure: int | BaseException | None,
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)
    primary = probe_error(
        ProbeErrorCode.BINARY_EVIDENCE,
        ErrorCategory.INTEGRITY,
        "evidence_primary",
        "evidence rejected",
    )
    original_error = fake_port._error

    def reject_evidence(_self, _path, handle):
        path_key, _offset = fake_port.handles[handle.value]
        if isinstance(close_failure, int):
            fake_port.close_results[path_key] = [close_failure]
        elif close_failure is not None:

            def close_raises(operation: str, default: int | None = None):
                if operation == "CloseHandle":
                    raise close_failure
                return original_error(operation, default)

            monkeypatch.setattr(fake_port, "_error", close_raises)
        return BinaryInspectionFailed(primary)

    monkeypatch.setattr(NativeBinaryTrustPort, "_inspect_open", reject_evidence)
    result = NativeBinaryTrustPort(fake_port).inspect(binary_path)

    assert isinstance(result, BinaryInspectionFailed)
    assert result.error.code is primary.code
    assert result.error.category is primary.category
    assert result.error.phase == primary.phase
    assert result.error.message == primary.message
    handle = captured[0]
    assert fake_port.close_attempts_by_handle[handle._value] == 1
    if close_failure is None:
        assert result.error is primary
        assert handle.state is HandleState.CLOSE_SUCCEEDED
        assert handle._value not in fake_port.handles
    else:
        secondary = result.error.secondary[-1]
        assert secondary.phase == "binary_handle_close"
        if isinstance(close_failure, int):
            assert secondary.win32_code == close_failure
            assert handle.state is HandleState.CLOSE_FAILED_OR_UNKNOWN
        else:
            assert secondary.cause is close_failure
            assert type(close_failure).__name__ in secondary.message
            assert handle.state is HandleState.OPEN


@pytest.mark.parametrize(
    "primary",
    [
        RuntimeError("issuance exception"),
        KeyboardInterrupt("issuance interrupt"),
        SystemExit("issuance exit"),
        DirectOwnerFailure("direct issuance base exception"),
    ],
)
def test_every_issuance_exception_keeps_local_owner_until_close(
    fake_port: FakePort,
    binary_path,
    monkeypatch: pytest.MonkeyPatch,
    primary: BaseException,
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)

    def reject_issuance(self, evidence, handle, *, _issuer_key=None) -> None:
        del self, evidence, handle, _issuer_key
        raise primary

    monkeypatch.setattr(BinaryInspection, "__init__", reject_issuance)
    with pytest.raises(type(primary)) as raised:
        NativeBinaryTrustPort(fake_port).inspect(binary_path)

    assert raised.value is primary
    handle = captured[0]
    assert handle.state is HandleState.CLOSE_SUCCEEDED
    assert fake_port.close_attempts_by_handle[handle._value] == 1
    assert handle._value not in fake_port.handles


def test_successful_transfer_uses_distinct_handles_and_never_closes_locally(
    fake_port: FakePort, binary_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_restrictive_handles(fake_port, monkeypatch)
    trust = NativeBinaryTrustPort(fake_port)

    first = trust.inspect(binary_path)
    second = trust.inspect(binary_path)

    assert isinstance(first, BinaryInspection)
    assert isinstance(second, BinaryInspection)
    assert len(captured) == 2
    assert captured[0] is first._handle
    assert captured[1] is second._handle
    assert captured[0]._value != captured[1]._value
    assert all(fake_port.close_attempts_by_handle.get(handle._value, 0) == 0 for handle in captured)
    assert first.close() is None
    assert second.close() is None
    assert all(fake_port.close_attempts_by_handle[handle._value] == 1 for handle in captured)


def test_inspect_is_the_only_native_method_that_issues_and_transfers_ownership(
    fake_port: FakePort, binary_path, monkeypatch
) -> None:
    """Construction failure closes the local owner; success transfers it exactly once."""
    trust = NativeBinaryTrustPort(fake_port)
    real_init = BinaryInspection.__init__

    def reject_issuance(self, evidence, handle, *, _issuer_key=None) -> None:
        del self, evidence, handle, _issuer_key
        raise EvidenceFailure("issuance rejected")

    monkeypatch.setattr(BinaryInspection, "__init__", reject_issuance)
    before_failure = len(fake_port.open_history)
    with pytest.raises(EvidenceFailure, match="issuance rejected"):
        trust.inspect(binary_path)
    assert len(fake_port.open_history) > before_failure
    failed_handle = fake_port.open_history[-1][0]
    assert fake_port.close_attempts_by_handle[failed_handle] == 1

    monkeypatch.setattr(BinaryInspection, "__init__", real_init)
    before_success = len(fake_port.open_history)
    issued = trust.inspect(binary_path)
    assert isinstance(issued, BinaryInspection)
    assert len(fake_port.open_history) > before_success
    issued_handle = fake_port.open_history[-1][0]
    assert issued._handle.value == issued_handle
    assert issued._close_state is binary_module._InspectionCloseState.OPEN
    assert issued.close() is None
    assert fake_port.close_attempts_by_handle[issued_handle] == 1


@pytest.mark.parametrize("close_mode", ["typed", "raising"])
def test_issuance_failure_preserves_primary_base_exception_during_local_close(
    fake_port: FakePort, binary_path, monkeypatch, close_mode: str
) -> None:
    """The local pre-transfer owner cannot replace a failed issuance primary cause."""
    trust = NativeBinaryTrustPort(fake_port)
    primary = DirectPrimary("issuance primary")

    def reject_issuance(self, evidence, handle, *, _issuer_key=None) -> None:
        del self, evidence, _issuer_key
        path_key, _offset = fake_port.handles[handle.value]
        if close_mode == "typed":
            fake_port.close_results[path_key] = [91]
        else:
            original_error = fake_port._error

            def close_raises(operation: str, default: int | None = None):
                if operation == "CloseHandle":
                    raise SystemExit("cleanup close rejected")
                return original_error(operation, default)

            fake_port._error = close_raises
        raise primary

    monkeypatch.setattr(BinaryInspection, "__init__", reject_issuance)
    before = len(fake_port.open_history)
    with pytest.raises(DirectPrimary) as raised:
        trust.inspect(binary_path)

    failed_handle = fake_port.open_history[-1][0]
    assert len(fake_port.open_history) > before
    assert raised.value is primary
    assert fake_port.close_attempts_by_handle[failed_handle] == 1
    if close_mode == "typed":
        assert "binary handle cleanup was unresolved" in primary.__notes__[0]
    else:
        assert "SystemExit: cleanup close rejected" in primary.__notes__[0]


def test_native_trust_port_has_no_other_issuer_method() -> None:
    """The concrete port exposes one owner-issuing flow and one evidence-only helper."""
    methods = {
        name
        for name, value in vars(NativeBinaryTrustPort).items()
        if callable(value) and not name.startswith("__")
    }

    assert methods == {"inspect", "_inspect_open"}
    assert NativeBinaryTrustPort._inspect_open.__annotations__["return"] == (
        "BinaryEvidence | BinaryInspectionFailed"
    )


class NoteRejectingPrimary(BaseException):
    def add_note(self, note: str) -> None:
        del note
        raise RuntimeError("note transport rejected")


class HostileTextCleanup(Exception):
    def __str__(self) -> str:
        raise RuntimeError("str rejected")

    def __repr__(self) -> str:
        raise RuntimeError("repr rejected")


class HostileReprCleanup(Exception):
    def __repr__(self) -> str:
        raise RuntimeError("repr rejected")


def test_cleanup_diagnostic_ignores_add_note_failure() -> None:
    binary_module._append_cleanup_diagnostic(NoteRejectingPrimary(), RuntimeError("cleanup failed"))


def test_cleanup_diagnostic_survives_throwing_str_and_repr() -> None:
    primary = RuntimeError("primary")
    binary_module._append_cleanup_diagnostic(primary, HostileTextCleanup())
    binary_module._append_cleanup_diagnostic(primary, HostileReprCleanup("safe str"))

    assert "HostileTextCleanup: <detail unavailable>" in primary.__notes__[0]
    assert "HostileReprCleanup: safe str" in primary.__notes__[1]


def test_cleanup_diagnostic_uses_static_fallback_for_unusual_type_representation(
    monkeypatch,
) -> None:
    class UnusualType:
        @property
        def __name__(self) -> str:
            raise RuntimeError("type name rejected")

    primary = RuntimeError("primary")
    monkeypatch.setattr(binary_module, "type", lambda _value: UnusualType(), raising=False)
    binary_module._append_cleanup_diagnostic(primary, HostileTextCleanup())

    assert primary.__notes__ == [
        "binary handle cleanup raised cleanup exception detail unavailable"
    ]


@pytest.mark.parametrize("cleanup", [KeyboardInterrupt("cleanup"), SystemExit("cleanup")])
def test_cleanup_diagnostic_accepts_direct_base_exception(cleanup: BaseException) -> None:
    primary = RuntimeError("primary")
    binary_module._append_cleanup_diagnostic(primary, cleanup)

    assert len(primary.__notes__) == 1
    assert cleanup.__class__.__name__ in primary.__notes__[0]


class DirectPrimary(BaseException):
    pass


@pytest.mark.parametrize(
    "primary",
    [KeyboardInterrupt("stop"), SystemExit("exit"), DirectPrimary("direct")],
)
def test_active_base_exception_identity_type_and_traceback_survive_cleanup_failure(
    validated_binary, primary: BaseException
) -> None:
    seen_tracebacks: list[object] = []

    def observe_active() -> None:
        active = sys.exception()
        assert active is primary
        seen_tracebacks.append(active.__traceback__)

    inspection = inspection_for(
        validated_binary,
        [],
        802,
        close_failure=SystemExit("cleanup must stay secondary"),
        on_close=observe_active,
    )

    def raise_with_cleanup() -> None:
        try:
            raise primary
        finally:
            binary_module._close_inspection_preserving_active(inspection)

    with pytest.raises(type(primary)) as raised:
        raise_with_cleanup()

    assert raised.value is primary
    assert type(raised.value) is type(primary)
    traceback = raised.value.__traceback__
    retained = False
    while traceback is not None:
        if traceback is seen_tracebacks[0]:
            retained = True
            break
        traceback = traceback.tb_next
    assert retained
    assert "SystemExit: cleanup must stay secondary" in primary.__notes__[0]


def test_cleanup_diagnostic_is_bounded() -> None:
    primary = RuntimeError("primary")
    binary_module._append_cleanup_diagnostic(primary, RuntimeError("x" * 10_000))

    assert len(primary.__notes__[0]) <= binary_module._CLEANUP_DIAGNOSTIC_LIMIT


def test_cleanup_text_bounding_has_total_fallbacks() -> None:
    class NonStringSlice(str):
        def __getitem__(self, _key):
            return 7

    class RaisingSlice(str):
        def __getitem__(self, _key):
            raise RuntimeError("slice rejected")

    assert binary_module._bounded_cleanup_text("", "fallback") == "fallback"
    assert binary_module._bounded_cleanup_text(NonStringSlice("x"), "fallback") == "fallback"
    assert binary_module._bounded_cleanup_text(RaisingSlice("x"), "fallback") == "fallback"


def test_cleanup_summary_and_note_formatting_failures_use_static_fallback(monkeypatch) -> None:
    def reject_bounding(_value: str, _fallback: str) -> str:
        raise RuntimeError("formatting rejected")

    monkeypatch.setattr(binary_module, "_bounded_cleanup_text", reject_bounding)
    assert (
        binary_module._safe_cleanup_exception_summary(RuntimeError("cleanup"))
        == "cleanup exception detail unavailable"
    )

    primary = RuntimeError("primary")
    binary_module._append_cleanup_diagnostic(primary, RuntimeError("cleanup"))
    assert primary.__notes__ == ["binary handle cleanup failed; diagnostic unavailable"]


def test_cleanup_probe_error_formatting_failure_never_escapes() -> None:
    primary = NoteRejectingPrimary()
    cleanup = probe_error(
        ProbeErrorCode.BINARY_ACCESS,
        ErrorCategory.IO,
        "binary_handle_close",
        "close failed",
    )

    binary_module._append_cleanup_diagnostic(primary, cleanup)


def test_initial_validator_inspection_enters_owner_boundary_before_evidence_access(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []
    trust = SequenceTrust(
        [
            hostile_inspection_for(validated_binary, calls, 811),
            inspection_for(validated_binary, calls, 812),
        ]
    )

    with pytest.raises(EvidenceFailure):
        validate_ffprobe_binary(
            FfprobeCandidate(validated_binary.canonical_dos_path),
            fake_port,
            trust,
            FakeProcessPort(),
        )

    assert calls == [812, 811]


def test_second_validator_inspection_enters_owner_boundary_before_evidence_access(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []
    trust = SequenceTrust(
        [
            inspection_for(validated_binary, calls, 813),
            hostile_inspection_for(validated_binary, calls, 814),
        ]
    )

    with pytest.raises(EvidenceFailure):
        validate_ffprobe_binary(
            FfprobeCandidate(validated_binary.canonical_dos_path),
            fake_port,
            trust,
            FakeProcessPort(),
        )

    assert calls == [814, 813]


def test_runner_prelaunch_inspection_enters_owner_boundary_before_evidence_access(
    fake_port: FakePort, validated_binary
) -> None:
    from tests.phase2.probe.test_runner_process import source_request

    calls: list[int] = []
    result = run_probe(
        source_request(fake_port, validated_binary),
        SequenceTrust([hostile_inspection_for(validated_binary, calls, 815)]),
        FakeProcessPort(),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )

    assert isinstance(result, ProbeFailed)
    assert result.error.phase == "binary_prelaunch_adapter"
    assert calls == [815]


def test_runner_post_inspection_enters_owner_boundary_before_evidence_access(
    fake_port: FakePort, validated_binary
) -> None:
    from tests.phase2.probe.test_runner_process import source_request

    calls: list[int] = []
    trust = SequenceTrust(
        [
            inspection_for(validated_binary, calls, 816),
            hostile_inspection_for(validated_binary, calls, 817),
        ]
    )

    with pytest.raises(EvidenceFailure):
        run_probe(
            source_request(fake_port, validated_binary),
            trust,
            FakeProcessPort(),
            lambda path: snapshot_file(fake_port, path),
            CancellationToken(),
        )

    assert calls == [817, 816]
