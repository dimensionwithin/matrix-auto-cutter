from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from matrix_auto_cutter.phase2 import CancellationToken
from matrix_auto_cutter.phase2.pathing import PathRole, PathValidated, validate_path
from matrix_auto_cutter.phase2.probe import (
    BinaryValidated,
    FfprobeCandidate,
    NativeProcessPort,
    ProbeFailed,
    ProbeOk,
    ProbeRequest,
    run_probe,
    validate_ffprobe_binary,
)
from matrix_auto_cutter.phase2.probe.binary import NativeBinaryTrustPort
from matrix_auto_cutter.phase2.snapshots import SnapshotOk, snapshot_file
from matrix_auto_cutter.phase2.win32_native import NativeWin32Port


@pytest.fixture(scope="module")
def native_probe():
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        pytest.skip("the local ffprobe integration binary is unavailable")
    port = NativeWin32Port()
    validated = validate_ffprobe_binary(
        FfprobeCandidate(str(Path(ffprobe).resolve())),
        port,
        NativeBinaryTrustPort(port),
        NativeProcessPort(),
    )
    assert isinstance(validated, BinaryValidated), validated
    return port, validated.binary, str(Path(ffprobe).resolve())


def run_real(path: Path, native_probe):
    port, binary, _ffprobe = native_probe
    validated_path = validate_path(port, str(path.resolve()), PathRole.EXTERNAL_SOURCE_READ_ONLY)
    assert isinstance(validated_path, PathValidated), validated_path
    snapshot = snapshot_file(port, validated_path.path)
    assert isinstance(snapshot, SnapshotOk), snapshot
    return run_probe(
        ProbeRequest(binary, validated_path.path, snapshot.snapshot.snapshot_key),
        NativeBinaryTrustPort(port),
        NativeProcessPort(),
        lambda source: snapshot_file(port, source),
        CancellationToken(),
    )


def probe_real(path: Path, native_probe):
    result = run_real(path, native_probe)
    assert isinstance(result, ProbeOk), result
    return result.profile


def raw_streams(path: Path, ffprobe: str):
    raw = subprocess.check_output(
        [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path.resolve()),
        ],
        timeout=30,
    )
    return json.loads(raw)["streams"]


def test_real_local_encoded_fixture_matches_productive_indexes_and_repeats(
    native_probe, tmp_path: Path
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("the local ffmpeg fixture generator is unavailable")
    path = tmp_path / "real-one-video-one-audio.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=30:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
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
        timeout=30,
    )
    first = probe_real(path, native_probe)
    second = probe_real(path, native_probe)
    _port, _binary, ffprobe = native_probe
    streams = raw_streams(path, ffprobe)
    expected_video = next(
        stream["index"]
        for stream in streams
        if stream["codec_type"] == "video"
        and not stream.get("disposition", {}).get("attached_pic", 0)
    )
    expected_audio = next(stream["index"] for stream in streams if stream["codec_type"] == "audio")
    assert first.selection.video_index == expected_video
    assert first.selection.audio_index == expected_audio
    assert first.selection == second.selection
    assert first.selection.selection_identity == second.selection.selection_identity


def test_real_webm_never_uses_global_duration_as_missing_audio_stream_duration(
    native_probe,
) -> None:
    path = Path(__file__).parents[3] / "intro-sting-sovereign-1440p.webm"
    assert path.is_file()
    result = run_real(path, native_probe)
    assert isinstance(result, ProbeFailed)
    assert result.error.phase == "stream_selection.audio_metadata"


def test_real_external_multistream_cover_fixture_excludes_cover_and_selects_default_audio(
    native_probe, tmp_path: Path
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("the local ffmpeg fixture generator is unavailable")
    path = tmp_path / "real-cover-multistream.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=64x64:rate=30:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=1000:sample_rate=48000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=500:sample_rate=48000:duration=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=32x32:rate=1:duration=1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map",
            "2:a:0",
            "-map",
            "3:v:0",
            "-c:v:0",
            "libx264",
            "-pix_fmt:v:0",
            "yuv420p",
            "-c:a",
            "aac",
            "-c:v:1",
            "mjpeg",
            "-disposition:a:0",
            "default",
            "-disposition:a:1",
            "0",
            "-disposition:v:1",
            "attached_pic",
            "-shortest",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    profile = probe_real(path, native_probe)
    _port, _binary, ffprobe = native_probe
    streams = raw_streams(path, ffprobe)
    main_video = next(
        stream
        for stream in streams
        if stream["codec_type"] == "video"
        and not stream.get("disposition", {}).get("attached_pic", 0)
    )
    cover = next(
        stream
        for stream in streams
        if stream["codec_type"] == "video" and stream.get("disposition", {}).get("attached_pic", 0)
    )
    default_audio = next(
        stream
        for stream in streams
        if stream["codec_type"] == "audio" and stream["disposition"]["default"] == 1
    )
    assert profile.selection.video_index == main_video["index"]
    assert profile.selection.video_index != cover["index"]
    assert profile.selection.audio_index == default_audio["index"]
    assert len([stream for stream in streams if stream["codec_type"] == "audio"]) == 2
