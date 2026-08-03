from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from matrix_auto_cutter.approval import check_render_authorization, record_decision
from matrix_auto_cutter.cut_proposal import (
    FfmpegProcessResult,
    ProposalReady,
    generate_proposal,
)
from matrix_auto_cutter.render import (
    KeepSegment,
    ProcessResult,
    RenderAccepted,
    RenderFailed,
    RenderSucceeded,
    build_filtergraph,
    build_keep_segments,
    execute_approved_render,
    load_render_status,
    submit_render_request,
    write_render_status,
)
from matrix_auto_cutter.review_app import review_render_view

NOW = datetime(2026, 8, 3, 18, tzinfo=UTC)
SESSION = "835fc47a-7e8c-4700-9f6f-8f7e23ac740c"
ATTEMPT_UUID = UUID("11111111-1111-4111-8111-111111111111")
RENDER_UUID = UUID("22222222-2222-4222-8222-222222222222")


class SilenceAnalysis:
    def __call__(self, arguments: object, timeout: int) -> FfmpegProcessResult:
        del timeout
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version render-test\n")
        return FfmpegProcessResult(
            0,
            b"silence_start: 2.0\nsilence_end: 4.0 | silence_duration: 2.0\n",
        )


def _binary(name: str) -> Path:
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} unavailable")
    return Path(found).resolve(strict=True)


def _make_source(path: Path) -> None:
    ffmpeg = _binary("ffmpeg")
    subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=60:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:sample_rate=48000:duration=6",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        shell=False,
        timeout=60,
    )


def _proposal(tmp_path: Path, raw_sidecar: dict[str, object]) -> tuple[Path, Path, ProposalReady]:
    source = tmp_path / "synthetic.mp4"
    _make_source(source)
    raw = deepcopy(raw_sidecar)
    source_data = raw["source"]
    assert isinstance(source_data, dict)
    source_data.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "duration_ms": 6000,
            "video_frame_count": 360,
        }
    )
    clock = raw["clock"]
    assert isinstance(clock, dict)
    clock["counter_end"] = 360
    events = raw["events"]
    assert isinstance(events, list)
    stop = events[-1]
    assert isinstance(stop, dict)
    stop["mapped_source_frame"] = 360
    sample = stop["clock_sample"]
    assert isinstance(sample, dict)
    sample["output_frame_count"] = 360
    sample["monotonic_ns"] = 360 * 16_666_667
    raw["recording_session_id"] = SESSION
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_text(json.dumps(raw), encoding="utf-8")
    result = generate_proposal(
        source,
        sidecar,
        SESSION,
        tmp_path / "artifacts",
        _binary("ffmpeg"),
        process_runner=SilenceAnalysis(),
        now=lambda: NOW,
    )
    assert isinstance(result, ProposalReady)
    return source, sidecar, result


def test_gate_rechecks_source_sidecar_and_separate_request_action(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, sidecar, ready = _proposal(tmp_path, raw_sidecar)
    proposal_path = ready.proposal_path

    assert check_render_authorization(proposal_path).authorized is False
    assert isinstance(submit_render_request(proposal_path, tmp_path / "rendered"), RenderFailed)
    record_decision(proposal_path, "rejected", now=lambda: NOW)
    assert isinstance(submit_render_request(proposal_path, tmp_path / "rendered"), RenderFailed)
    assert not (proposal_path.parent / "render-request.json").exists()

    record_decision(proposal_path, "approved", now=lambda: NOW)
    accepted = submit_render_request(
        proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: ATTEMPT_UUID,
    )
    assert isinstance(accepted, RenderAccepted)
    assert not Path(accepted.request.target_path).exists()

    source_before = source.read_bytes()
    source.write_bytes(source_before + b"changed")
    assert check_render_authorization(proposal_path).authorized is False
    source.write_bytes(source_before)
    sidecar_before = sidecar.read_bytes()
    sidecar.write_bytes(sidecar_before + b"changed")
    assert check_render_authorization(proposal_path).authorized is False
    sidecar.write_bytes(sidecar_before)
    assert check_render_authorization(proposal_path).authorized is True


def test_review_render_button_projection_requires_approval_and_blocks_running(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    from matrix_auto_cutter.render import RenderStatus

    _source, _sidecar, ready = _proposal(tmp_path, raw_sidecar)
    pending = review_render_view(ready.proposal_path, tmp_path / "rendered")
    assert pending.state == "render_not_authorized" and pending.render_enabled is False
    record_decision(ready.proposal_path, "rejected", now=lambda: NOW)
    assert review_render_view(ready.proposal_path, tmp_path / "rendered").render_enabled is False
    record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    approved = review_render_view(ready.proposal_path, tmp_path / "rendered")
    assert approved.state == "render_ready" and approved.render_enabled is True
    write_render_status(
        ready.proposal_path,
        RenderStatus(
            artifact_type="matrix_auto_cutter_render_status",
            schema_version="1.0",
            proposal_id=ready.proposal.proposal_id,
            state="render_running",
            message_de="läuft",
            updated_at=NOW,
            target_path=str(approved.target_path),
        ),
    )
    running = review_render_view(ready.proposal_path, tmp_path / "rendered")
    assert running.render_enabled is False and running.output_enabled is False


def test_keep_complement_boundaries_microsegments_and_filtergraph(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    _source, _sidecar, ready = _proposal(tmp_path, raw_sidecar)
    keeps = build_keep_segments(ready.proposal)
    assert keeps == (
        KeepSegment(start_frame=0, end_frame=141),
        KeepSegment(start_frame=219, end_frame=360),
    )
    graph = build_filtergraph(keeps, video_index=3, audio_index=7)
    assert "[0:3]trim=start=0.000000000:end=2.350000000" in graph
    assert "[0:7]atrim=start=3.650000000:end=6.000000000" in graph
    assert graph.endswith("concat=n=2:v=1:a=1[vout][aout]")
    assert graph == build_filtergraph(keeps, video_index=3, audio_index=7)
    with pytest.raises(ValueError, match="at least one"):
        build_filtergraph(())


def test_real_render_verifies_publishes_once_and_preserves_source(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    source, _sidecar, ready = _proposal(tmp_path, raw_sidecar)
    record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    accepted = submit_render_request(
        ready.proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: ATTEMPT_UUID,
    )
    assert isinstance(accepted, RenderAccepted)
    before = (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest())

    outcome = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        now=lambda: NOW,
        uuid_factory=lambda: RENDER_UUID,
    )

    assert isinstance(outcome, RenderSucceeded)
    assert outcome.plan.output_frame_count == 282
    assert outcome.plan.cut_frame_count == 78
    assert outcome.plan.expected_output_duration_ms == 4700
    assert outcome.result.verification_status == "passed"
    assert outcome.result.actual_duration_ms == pytest.approx(4700, abs=50)
    target = Path(outcome.result.target_path)
    assert target.is_file() and target.stat().st_size > 0
    assert not Path(outcome.plan.partial_path).exists()
    assert before == (source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest())
    status = load_render_status(ready.proposal_path)
    assert status is not None and status.state == "render_succeeded"

    reused = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        now=lambda: NOW,
        uuid_factory=lambda: UUID("33333333-3333-4333-8333-333333333333"),
    )
    assert isinstance(reused, RenderSucceeded) and reused.reused is True
    assert len(list((ready.proposal_path.parent / "renders").glob("*/render-plan.json"))) == 1


def test_cancelled_process_cannot_publish(tmp_path: Path, raw_sidecar: dict[str, object]) -> None:
    _source, _sidecar, ready = _proposal(tmp_path, raw_sidecar)
    record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    accepted = submit_render_request(
        ready.proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: ATTEMPT_UUID,
    )
    assert isinstance(accepted, RenderAccepted)

    class CancelAtRender:
        calls = 0

        def __call__(
            self, arguments: object, timeout: int, cancellation: threading.Event
        ) -> object:
            from matrix_auto_cutter.render import ProcessResult

            del timeout, cancellation
            self.calls += 1
            values = tuple(arguments)  # type: ignore[arg-type]
            if "ffprobe" in Path(str(values[0])).name:
                payload = {
                    "streams": [
                        {
                            "index": 0,
                            "codec_type": "video",
                            "width": 160,
                            "height": 90,
                            "avg_frame_rate": "60/1",
                        },
                        {"index": 1, "codec_type": "audio", "sample_rate": "48000"},
                    ],
                    "format": {"duration": "6.000"},
                }
                return ProcessResult(0, json.dumps(payload).encode())
            if "lavfi" in values:
                return ProcessResult(1 if "h264_nvenc" in values else 0)
            return ProcessResult(-9, cancelled=True)

    outcome = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=CancelAtRender(),  # type: ignore[arg-type]
        now=lambda: NOW,
        uuid_factory=lambda: RENDER_UUID,
    )
    assert isinstance(outcome, RenderFailed)
    assert outcome.code == "E_RENDER_CANCELLED"
    assert not Path(accepted.request.target_path).exists()


class ScriptedRender:
    def __init__(
        self,
        *,
        render_exit: int = 0,
        timed_out: bool = False,
        output_video: bool = True,
        output_audio: bool = True,
        output_rate: str = "60/1",
        output_duration: str = "4.700",
        output_sample_rate: str = "48000",
        decode_exit: int = 0,
    ) -> None:
        self.render_exit = render_exit
        self.timed_out = timed_out
        self.output_video = output_video
        self.output_audio = output_audio
        self.output_rate = output_rate
        self.output_duration = output_duration
        self.output_sample_rate = output_sample_rate
        self.decode_exit = decode_exit
        self.render_calls = 0

    def _profile(self, output: bool) -> bytes:
        streams: list[dict[str, object]] = []
        if not output or self.output_video:
            streams.append(
                {
                    "index": 0,
                    "codec_type": "video",
                    "width": 160,
                    "height": 90,
                    "avg_frame_rate": self.output_rate if output else "60/1",
                }
            )
        if not output or self.output_audio:
            streams.append(
                {
                    "index": 1,
                    "codec_type": "audio",
                    "sample_rate": self.output_sample_rate if output else "48000",
                }
            )
        return json.dumps(
            {
                "streams": streams,
                "format": {"duration": self.output_duration if output else "6.000"},
            }
        ).encode()

    def __call__(
        self, arguments: object, timeout: int, cancellation: threading.Event
    ) -> ProcessResult:
        del timeout, cancellation
        values = tuple(arguments)  # type: ignore[arg-type]
        if "ffprobe" in Path(str(values[0])).name:
            return ProcessResult(0, self._profile("partial" in str(values[-1])))
        if "lavfi" in values:
            return ProcessResult(1 if "h264_nvenc" in values else 0)
        if "-progress" in values:
            self.render_calls += 1
            if self.render_exit == 0 and not self.timed_out:
                Path(str(values[-1])).write_bytes(b"verified-render-output")
            return ProcessResult(
                self.render_exit,
                b"progress=end\n",
                b"controlled diagnostic",
                timed_out=self.timed_out,
            )
        return ProcessResult(self.decode_exit)


def _approved_request(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> tuple[Path, ProposalReady, RenderAccepted]:
    source, _sidecar, ready = _proposal(tmp_path, raw_sidecar)
    record_decision(ready.proposal_path, "approved", now=lambda: NOW)
    accepted = submit_render_request(
        ready.proposal_path,
        tmp_path / "rendered",
        now=lambda: NOW,
        uuid_factory=lambda: ATTEMPT_UUID,
    )
    assert isinstance(accepted, RenderAccepted)
    return source, ready, accepted


@pytest.mark.parametrize(
    ("process", "expected_code"),
    [
        (ScriptedRender(render_exit=7), "E_RENDER_FFMPEG"),
        (ScriptedRender(timed_out=True), "E_RENDER_TIMEOUT"),
        (ScriptedRender(output_video=False), "E_RENDER_VERIFY"),
        (ScriptedRender(output_audio=False), "E_RENDER_VERIFY"),
        (ScriptedRender(output_rate="30/1"), "E_RENDER_VERIFY"),
        (ScriptedRender(output_duration="4.900"), "E_RENDER_VERIFY"),
        (ScriptedRender(output_sample_rate="44100"), "E_RENDER_VERIFY"),
        (ScriptedRender(decode_exit=9), "E_RENDER_VERIFY"),
    ],
)
def test_process_and_verification_failures_never_publish(
    tmp_path: Path,
    raw_sidecar: dict[str, object],
    process: ScriptedRender,
    expected_code: str,
) -> None:
    _source, ready, accepted = _approved_request(tmp_path, raw_sidecar)
    outcome = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=process,
        now=lambda: NOW,
        uuid_factory=lambda: RENDER_UUID,
    )
    assert isinstance(outcome, RenderFailed)
    assert outcome.code == expected_code
    assert outcome.result is not None and outcome.result.status == "failed"
    assert not Path(accepted.request.target_path).exists()


def test_target_partial_and_request_conflicts_fail_before_render(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    _source, ready, accepted = _approved_request(tmp_path, raw_sidecar)
    target = Path(accepted.request.target_path)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"foreign")
    process = ScriptedRender()
    conflict = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=process,
        now=lambda: NOW,
        uuid_factory=lambda: RENDER_UUID,
    )
    assert isinstance(conflict, RenderFailed) and conflict.code == "E_RENDER_TARGET_CONFLICT"
    assert target.read_bytes() == b"foreign"
    target.unlink()

    partial = target.with_name(f"{target.stem}.{accepted.request.attempt_id}.partial.mp4")
    partial.write_bytes(b"foreign-partial")
    partial_conflict = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=process,
        now=lambda: NOW,
        uuid_factory=lambda: UUID("33333333-3333-4333-8333-333333333333"),
    )
    assert isinstance(partial_conflict, RenderFailed)
    assert partial_conflict.code == "E_RENDER_PARTIAL_CONFLICT"
    assert partial.read_bytes() == b"foreign-partial"
    partial.unlink()

    wrong = accepted.request.model_copy(update={"proposal_sha256": "f" * 64})
    binding = execute_approved_render(
        ready.proposal_path,
        wrong,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=process,
        now=lambda: NOW,
        uuid_factory=lambda: UUID("44444444-4444-4444-8444-444444444444"),
    )
    assert isinstance(binding, RenderFailed) and binding.code == "E_RENDER_REQUEST_BINDING"
    assert process.render_calls == 0


def test_gate_is_rechecked_immediately_before_process_start(
    tmp_path: Path, raw_sidecar: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, ready, accepted = _approved_request(tmp_path, raw_sidecar)
    from matrix_auto_cutter import render as render_module

    original = render_module.check_render_authorization
    calls = 0
    process = ScriptedRender()

    def observed(path: Path):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(render_module, "check_render_authorization", observed)
    outcome = execute_approved_render(
        ready.proposal_path,
        accepted.request,
        _binary("ffmpeg"),
        _binary("ffprobe"),
        process_runner=process,
        now=lambda: NOW,
        uuid_factory=lambda: RENDER_UUID,
    )
    assert isinstance(outcome, RenderSucceeded)
    assert calls >= 3
    assert process.render_calls == 1
