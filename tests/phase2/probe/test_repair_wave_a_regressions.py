from __future__ import annotations

from threading import Event, Thread

import pytest
from tests.phase2.conftest import FakePort
from tests.phase2.probe.conftest import (
    VERSION_TEXT,
    FakeProcessPort,
    golden_json,
    golden_stream,
    issued_inspection_for,
)
from tests.phase2.probe.test_runner_process import source_request

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    ProbeFailed,
    ProbeOk,
    ProbeProcessOk,
    ProcessDiagnostics,
    run_probe,
)
from matrix_auto_cutter.phase2.probe.binary import (
    BinaryInspection,
    BinaryValidationFailed,
    NativeBinaryTrustPort,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.snapshots import snapshot_file


class SequenceTrust:
    def __init__(self, inspections: list[BinaryInspection]) -> None:
        self.inspections = inspections

    def inspect(self, _path):
        return self.inspections.pop(0)


def tracked_inspection(
    binary,
    calls: list[int],
    *,
    value: int,
    close_code: int | None = None,
    close_exception: BaseException | None = None,
) -> BinaryInspection:
    close_failure = close_exception if close_exception is not None else close_code
    return issued_inspection_for(
        binary,
        calls,
        value=value,
        close_failure=close_failure,
    )


def valid_process() -> ProbeProcessOk:
    return ProbeProcessOk(
        ProcessDiagnostics(
            golden_json(
                [
                    golden_stream(0, "video", default=1),
                    golden_stream(1, "audio", default=1),
                ]
            ),
            b"",
        )
    )


def test_handle_cleanup_regression_validator_adapter_exception_closes_once(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []

    class ExplodingVersionProcess:
        def run(self, _spec, _token):
            raise RuntimeError("version adapter failed")

    result = validate_ffprobe_binary(
        FfprobeCandidate(validated_binary.canonical_dos_path),
        fake_port,
        SequenceTrust([tracked_inspection(validated_binary, calls, value=101)]),
        ExplodingVersionProcess(),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.phase == "version_process_adapter"
    assert isinstance(result.error.cause, RuntimeError)
    assert calls == [101]


def test_handle_cleanup_regression_validator_base_exception_still_closes_once(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []

    class StoppingVersionProcess:
        def run(self, _spec, _token):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        validate_ffprobe_binary(
            FfprobeCandidate(validated_binary.canonical_dos_path),
            fake_port,
            SequenceTrust([tracked_inspection(validated_binary, calls, value=102)]),
            StoppingVersionProcess(),
        )
    assert calls == [102]


def test_handle_cleanup_regression_runner_adapter_exception_and_close_error(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    calls: list[int] = []

    class ExplodingProcess:
        def run(self, _spec, _token):
            raise RuntimeError("probe adapter failed")

    trust = SequenceTrust(
        [
            tracked_inspection(validated_binary, calls, value=201, close_code=81),
            tracked_inspection(validated_binary, calls, value=202),
        ]
    )
    result = run_probe(
        request,
        trust,
        ExplodingProcess(),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.phase == "process_adapter"
    assert isinstance(result.error.cause, RuntimeError)
    assert any(error.phase == "binary_handle_close" for error in result.error.secondary)
    assert calls == [202, 201]


def test_handle_cleanup_regression_runner_base_exception_still_closes_once(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    calls: list[int] = []

    class StoppingProcess:
        def run(self, _spec, _token):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_probe(
            request,
            SequenceTrust([tracked_inspection(validated_binary, calls, value=203)]),
            StoppingProcess(),
            lambda path: snapshot_file(fake_port, path),
            CancellationToken(),
        )
    assert calls == [203]


def test_late_cancellation_regression_during_second_snapshot_prevents_success(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    token = CancellationToken()
    calls = 0

    def snapshotter(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            token.cancel()
        return snapshot_file(fake_port, path)

    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        snapshotter,
        token,
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code.value == "E_PROBE_CANCELLED"
    assert calls == 2


def test_late_cancellation_regression_after_parser_before_selection(
    fake_port: FakePort, validated_binary, monkeypatch
) -> None:
    from matrix_auto_cutter.phase2.probe import runner

    request = source_request(fake_port, validated_binary)
    token = CancellationToken()
    original = runner.select_streams

    def cancel_then_select(streams):
        token.cancel()
        return original(streams)

    monkeypatch.setattr(runner, "select_streams", cancel_then_select)
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        token,
    )
    assert isinstance(result, ProbeFailed)
    assert result.error.code.value == "E_PROBE_CANCELLED"


def test_late_cancellation_regression_preserves_completed_failure_before_cancel(
    fake_port: FakePort, validated_binary, monkeypatch
) -> None:
    from matrix_auto_cutter.phase2.probe import runner

    request = source_request(fake_port, validated_binary)
    token = CancellationToken()
    source_key = fake_port._key(request.source.canonical_dos_path)
    snapshot_calls = 0

    def remove_source(*_args):
        fake_port.nodes.pop(source_key, None)

    def snapshot_then_cancel(path):
        nonlocal snapshot_calls
        snapshot_calls += 1
        result = snapshot_file(fake_port, path)
        if snapshot_calls == 2:
            token.cancel()
        return result

    snapshot_failure = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process(), callback=remove_source),
        snapshot_then_cancel,
        token,
    )
    assert isinstance(snapshot_failure, ProbeFailed)
    assert snapshot_failure.error.phase == "source_snapshot_after"
    assert snapshot_failure.error.secondary[0].code.value == "E_PROBE_CANCELLED"

    request = source_request(fake_port, validated_binary)
    token = CancellationToken()
    original = runner.select_streams

    def select_then_cancel(streams):
        result = original(())
        token.cancel()
        return result

    monkeypatch.setattr(runner, "select_streams", select_then_cancel)
    selection_failure = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        token,
    )
    assert isinstance(selection_failure, ProbeFailed)
    assert selection_failure.error.code.value == "E_PROBE_UNSUPPORTED_MEDIA"
    assert selection_failure.error.secondary[0].code.value == "E_PROBE_CANCELLED"


def test_late_cancellation_regression_cancel_wins_real_final_commit_race(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    ready = Event()
    release = Event()

    class GatedToken(CancellationToken):
        def begin_irreversible_commit(self):
            ready.set()
            assert release.wait(2)
            return super().begin_irreversible_commit()

    token = GatedToken()
    results: list[object] = []
    worker = Thread(
        target=lambda: results.append(
            run_probe(
                request,
                NativeBinaryTrustPort(fake_port),
                FakeProcessPort(valid_process()),
                lambda path: snapshot_file(fake_port, path),
                token,
            )
        )
    )
    worker.start()
    assert ready.wait(2)
    token.cancel()
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], ProbeFailed)


def test_late_cancellation_regression_committed_success_wins_later_cancel(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    committed = Event()
    release = Event()

    class GatedToken(CancellationToken):
        def begin_irreversible_commit(self):
            permit = super().begin_irreversible_commit()
            committed.set()
            assert release.wait(2)
            return permit

    token = GatedToken()
    results: list[object] = []
    worker = Thread(
        target=lambda: results.append(
            run_probe(
                request,
                NativeBinaryTrustPort(fake_port),
                FakeProcessPort(valid_process()),
                lambda path: snapshot_file(fake_port, path),
                token,
            )
        )
    )
    worker.start()
    assert committed.wait(2)
    token.cancel()
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert len(results) == 1 and isinstance(results[0], ProbeOk)


def test_complete_validator_and_runner_success_close_all_handles(
    fake_port: FakePort,
) -> None:
    node = fake_port.add_file(r"C:\Tools\ffprobe.exe", b"trusted-binary")
    validation = validate_ffprobe_binary(
        FfprobeCandidate(node.path),
        fake_port,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(),
    )
    assert isinstance(validation, BinaryValidated)
    request = source_request(fake_port, validation.binary)
    result = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(result, ProbeOk)
    assert not fake_port.handles


def test_handle_cleanup_regression_validator_parser_exception_is_structured(
    fake_port: FakePort, validated_binary, monkeypatch
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    calls: list[int] = []

    def fail_parse(_raw):
        raise ValueError("bounded version conversion failed")

    monkeypatch.setattr(binary_module, "parse_ffprobe_version", fail_parse)
    result = validate_ffprobe_binary(
        FfprobeCandidate(validated_binary.canonical_dos_path),
        fake_port,
        SequenceTrust([tracked_inspection(validated_binary, calls, value=301)]),
        FakeProcessPort(),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.phase == "version_parse"
    assert calls == [301]


def test_handle_cleanup_regression_validator_raising_close_is_structured(
    fake_port: FakePort, validated_binary
) -> None:
    attempts: list[int] = []

    class ExplodingProcess:
        def run(self, _spec, _token):
            raise RuntimeError("version adapter failed")

    result = validate_ffprobe_binary(
        FfprobeCandidate(validated_binary.canonical_dos_path),
        fake_port,
        SequenceTrust(
            [
                tracked_inspection(
                    validated_binary,
                    attempts,
                    value=1,
                    close_exception=RuntimeError("close adapter failed"),
                )
            ]
        ),
        ExplodingProcess(),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.phase == "version_process_adapter"
    assert any(error.phase == "binary_handle_close" for error in result.error.secondary)
    assert attempts == [1]


def test_runner_adapter_boundaries_are_structured(
    fake_port: FakePort, validated_binary, monkeypatch
) -> None:
    from matrix_auto_cutter.phase2.probe import runner

    request = source_request(fake_port, validated_binary)

    def fail_snapshot(_path):
        raise RuntimeError("snapshot adapter failed")

    before = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        fail_snapshot,
        CancellationToken(),
    )
    assert isinstance(before, ProbeFailed)
    assert before.error.phase == "source_snapshot_before_adapter"

    class FailingTrust:
        def inspect(self, _path):
            raise RuntimeError("trust adapter failed")

    prelaunch = run_probe(
        request,
        FailingTrust(),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(prelaunch, ProbeFailed)
    assert prelaunch.error.phase == "binary_prelaunch_adapter"

    original_parse = runner.parse_probe_json
    monkeypatch.setattr(
        runner,
        "parse_probe_json",
        lambda _raw: (_ for _ in ()).throw(RuntimeError("parser failed")),
    )
    parser = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(parser, ProbeFailed)
    assert parser.error.phase == "json_parser"
    monkeypatch.setattr(runner, "parse_probe_json", original_parse)

    original_selection = runner.select_streams
    monkeypatch.setattr(
        runner,
        "select_streams",
        lambda _streams: (_ for _ in ()).throw(RuntimeError("selection failed")),
    )
    selection = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(selection, ProbeFailed)
    assert selection.error.phase == "stream_selection"
    monkeypatch.setattr(runner, "select_streams", original_selection)


def test_runner_post_adapters_and_raising_close_are_structured(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    calls: list[int] = []

    class PostFailingTrust:
        def __init__(self) -> None:
            self.count = 0

        def inspect(self, _path):
            self.count += 1
            if self.count == 1:
                return tracked_inspection(validated_binary, calls, value=401)
            raise RuntimeError("post trust adapter failed")

    post = run_probe(
        request,
        PostFailingTrust(),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(post, ProbeFailed)
    assert post.error.phase == "binary_post_probe_adapter"
    assert calls == [401]

    snapshots = 0

    def fail_second_snapshot(path):
        nonlocal snapshots
        snapshots += 1
        if snapshots == 2:
            raise RuntimeError("post snapshot adapter failed")
        return snapshot_file(fake_port, path)

    after = run_probe(
        request,
        NativeBinaryTrustPort(fake_port),
        FakeProcessPort(valid_process()),
        fail_second_snapshot,
        CancellationToken(),
    )
    assert isinstance(after, ProbeFailed)
    assert after.error.phase == "source_snapshot_after_adapter"

    raising = tracked_inspection(
        validated_binary,
        calls,
        value=402,
        close_exception=RuntimeError("close adapter failed"),
    )
    close_result = run_probe(
        request,
        SequenceTrust([raising, tracked_inspection(validated_binary, calls, value=403)]),
        FakeProcessPort(valid_process()),
        lambda path: snapshot_file(fake_port, path),
        CancellationToken(),
    )
    assert isinstance(close_result, ProbeFailed)
    assert close_result.error.phase == "binary_handle_close"


def test_handle_cleanup_regression_underlying_closer_becomes_unresolved(
    validated_binary,
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    attempts: list[int] = []
    inspection = tracked_inspection(
        validated_binary,
        attempts,
        value=501,
        close_exception=RuntimeError("native close adapter raised"),
    )
    close_error = binary_module._close_inspection(inspection)
    assert close_error is not None and close_error.phase == "binary_handle_close"
    assert inspection._close_state is binary_module._InspectionCloseState.OWNERSHIP_UNRESOLVED
    assert not inspection._handle.closed
    assert attempts == [501]
    with pytest.raises(RuntimeError, match="already attempted"):
        inspection.close()
    assert attempts == [501]


def test_handle_cleanup_regression_second_validator_inspection_exception_is_structured(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []

    class PostExplodingTrust:
        def __init__(self) -> None:
            self.count = 0

        def inspect(self, _path):
            self.count += 1
            if self.count == 1:
                return tracked_inspection(validated_binary, calls, value=502)
            raise RuntimeError("second trust inspection failed")

    result = validate_ffprobe_binary(
        FfprobeCandidate(validated_binary.canonical_dos_path),
        fake_port,
        PostExplodingTrust(),
        FakeProcessPort(),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.phase == "binary_post_version_adapter"
    assert isinstance(result.error.cause, RuntimeError)
    assert calls == [502]


@pytest.mark.parametrize("primary", [KeyboardInterrupt("stop"), SystemExit("exit")])
def test_handle_cleanup_regression_base_exception_survives_raising_close(
    fake_port: FakePort, validated_binary, primary: BaseException
) -> None:
    request = source_request(fake_port, validated_binary)
    attempts: list[int] = []

    class StoppingProcess:
        def run(self, _spec, _token):
            raise primary

    with pytest.raises(type(primary)) as raised:
        run_probe(
            request,
            SequenceTrust(
                [
                    tracked_inspection(
                        validated_binary,
                        attempts,
                        value=503,
                        close_exception=SystemExit("close must not replace primary"),
                    )
                ]
            ),
            StoppingProcess(),
            lambda path: snapshot_file(fake_port, path),
            CancellationToken(),
        )
    assert raised.value is primary
    assert raised.value.__traceback__ is not None
    assert any("binary handle cleanup raised SystemExit" in note for note in raised.value.__notes__)
    assert attempts == [503]


def test_handle_cleanup_regression_base_exception_retains_typed_close_diagnostic(
    fake_port: FakePort, validated_binary
) -> None:
    request = source_request(fake_port, validated_binary)
    primary = KeyboardInterrupt("stop")
    calls: list[int] = []

    class StoppingProcess:
        def run(self, _spec, _token):
            raise primary

    with pytest.raises(KeyboardInterrupt) as raised:
        run_probe(
            request,
            SequenceTrust([tracked_inspection(validated_binary, calls, value=504, close_code=93)]),
            StoppingProcess(),
            lambda path: snapshot_file(fake_port, path),
            CancellationToken(),
        )
    assert raised.value is primary
    assert any("binary handle cleanup was unresolved" in note for note in primary.__notes__)
    assert calls == [504]


def test_handle_cleanup_regression_close_base_exception_propagates_without_primary(
    validated_binary,
) -> None:
    from matrix_auto_cutter.phase2.probe import binary as binary_module

    with pytest.raises(SystemExit, match="standalone close failure"):
        binary_module._close_inspection_preserving_active(
            tracked_inspection(
                validated_binary,
                [],
                value=505,
                close_exception=SystemExit("standalone close failure"),
            )
        )


def test_handle_cleanup_regression_real_oversized_version_is_structured(
    fake_port: FakePort, validated_binary
) -> None:
    calls: list[int] = []
    huge = VERSION_TEXT.replace("8.1.1-test-build", f"{'8' * 5000}.1.1-test-build").encode()
    result = validate_ffprobe_binary(
        FfprobeCandidate(validated_binary.canonical_dos_path),
        fake_port,
        SequenceTrust([tracked_inspection(validated_binary, calls, value=506)]),
        FakeProcessPort(ProbeProcessOk(ProcessDiagnostics(huge, b""))),
    )
    assert isinstance(result, BinaryValidationFailed)
    assert result.error.phase == "version_parse"
    assert calls == [506]
