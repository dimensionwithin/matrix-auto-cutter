"""Conservative stream-selection policy ``stream_selection/1.0``."""

from __future__ import annotations

from matrix_auto_cutter.phase2.errors import ErrorCategory
from matrix_auto_cutter.phase2.probe.contracts import (
    AudioSelectionReason,
    FinalizedStreamSelection,
    MediaStream,
    ProbeAmbiguousStreams,
    ProbeUnsupportedMedia,
    StreamSelectionResult,
    StreamsSelected,
    StreamType,
    VideoSelectionReason,
    stream_selection_evidence_digest,
)
from matrix_auto_cutter.phase2.probe.errors import (
    ProbeErrorCode,
    ProbeErrorDetail,
    probe_error,
)

# Policy 1.0 intentionally supports only this fully tested conservative ranking.
AUDIO_LAYOUT_RANK_1_0: tuple[tuple[str, int, int], ...] = (
    ("mono", 1, 1),
    ("stereo", 2, 2),
)


def _ambiguous(streams: tuple[MediaStream, ...], kind: str, message: str) -> ProbeAmbiguousStreams:
    evidence = tuple(sorted(streams, key=lambda stream: stream.index))
    return ProbeAmbiguousStreams(
        probe_error(
            ProbeErrorCode.AMBIGUOUS_STREAMS,
            ErrorCategory.INPUT,
            f"stream_selection.{kind}_ambiguous",
            message,
        ),
        evidence,
        stream_selection_evidence_digest(evidence),
    )


def _unsupported(
    streams: tuple[MediaStream, ...],
    kind: str,
    message: str,
    *,
    detail: ProbeErrorDetail | None = None,
) -> ProbeUnsupportedMedia:
    evidence = tuple(sorted(streams, key=lambda stream: stream.index))
    return ProbeUnsupportedMedia(
        probe_error(
            ProbeErrorCode.UNSUPPORTED_MEDIA,
            ErrorCategory.POLICY,
            f"stream_selection.{kind}",
            message,
            detail=detail,
        ),
        evidence,
        stream_selection_evidence_digest(evidence),
    )


def _codec_available(codec_name: str | None) -> bool:
    return (
        codec_name is not None
        and bool(codec_name.strip())
        and codec_name.casefold() not in {"unknown", "none", "n/a"}
    )


def _video_complete(stream: MediaStream) -> bool:
    return (
        stream.disposition.default is not None
        and _codec_available(stream.codec_name)
        and stream.width is not None
        and stream.width > 0
        and stream.height is not None
        and stream.height > 0
    )


def _resolution(stream: MediaStream) -> tuple[int, int]:
    assert stream.width is not None and stream.height is not None
    return max(stream.width, stream.height), min(stream.width, stream.height)


def _dominates(left: MediaStream, right: MediaStream) -> bool:
    left_long, left_short = _resolution(left)
    right_long, right_short = _resolution(right)
    return (
        left_long >= right_long
        and left_short >= right_short
        and (left_long > right_long or left_short > right_short)
    )


def _select_video(
    streams: tuple[MediaStream, ...],
) -> tuple[MediaStream, VideoSelectionReason] | ProbeAmbiguousStreams | ProbeUnsupportedMedia:
    video_streams = tuple(stream for stream in streams if stream.stream_type is StreamType.VIDEO)
    if not video_streams:
        return _unsupported(streams, "video_missing", "required main video stream is absent")
    if any(stream.disposition.attached_pic is None for stream in video_streams):
        return _unsupported(
            streams,
            "video_metadata",
            "a video stream has no classifiable attached-picture disposition",
        )
    main_video = tuple(
        stream for stream in video_streams if stream.disposition.attached_pic is False
    )
    if not main_video:
        return _unsupported(streams, "video_missing", "required main video stream is absent")
    if any(not _video_complete(stream) for stream in main_video):
        return _unsupported(
            streams,
            "video_metadata",
            "a main video stream has incomplete technical selection metadata",
        )
    defaults = tuple(stream for stream in main_video if stream.disposition.default is True)
    if len(defaults) > 1:
        return _ambiguous(streams, "video", "multiple classifiable default video streams")
    if len(defaults) == 1:
        return defaults[0], VideoSelectionReason.UNIQUE_DEFAULT
    if len(main_video) == 1:
        return main_video[0], VideoSelectionReason.SINGLE_ELIGIBLE
    maxima = tuple(
        candidate
        for candidate in main_video
        if not any(_dominates(other, candidate) for other in main_video if other is not candidate)
    )
    if len(maxima) != 1:
        return _ambiguous(
            streams,
            "video",
            "video candidates have multiple or incomparable resolution maxima",
        )
    return maxima[0], VideoSelectionReason.UNIQUE_RESOLUTION_MAXIMUM


def _audio_rank(stream: MediaStream) -> int | None:
    for layout, channels, rank in AUDIO_LAYOUT_RANK_1_0:
        if stream.channel_layout == layout and stream.channels == channels:
            return rank
    return None


def _audio_complete(stream: MediaStream) -> bool:
    return (
        stream.disposition.default is not None
        and _codec_available(stream.codec_name)
        and stream.sample_rate is not None
        and stream.sample_rate > 0
        and stream.channels is not None
        and stream.channels > 0
        and stream.channel_layout is not None
        and bool(stream.channel_layout.strip())
        and stream.duration is not None
        and stream.duration.value > 0
    )


def _layout_unsupported(streams: tuple[MediaStream, ...], message: str) -> ProbeUnsupportedMedia:
    return _unsupported(
        streams,
        "audio_metadata",
        message,
        detail=ProbeErrorDetail.AUDIO_LAYOUT_UNSUPPORTED,
    )


def _select_audio(
    streams: tuple[MediaStream, ...],
) -> tuple[MediaStream, AudioSelectionReason] | ProbeAmbiguousStreams | ProbeUnsupportedMedia:
    audio_streams = tuple(stream for stream in streams if stream.stream_type is StreamType.AUDIO)
    if not audio_streams:
        return _unsupported(streams, "audio_missing", "required main audio stream is absent")
    if any(not _audio_complete(stream) for stream in audio_streams):
        return _unsupported(
            streams,
            "audio_metadata",
            "an audio stream has incomplete technical selection metadata",
        )
    defaults = tuple(stream for stream in audio_streams if stream.disposition.default is True)
    if len(defaults) > 1:
        return _ambiguous(streams, "audio", "multiple classifiable default audio streams")
    if len(defaults) == 1:
        if _audio_rank(defaults[0]) is None:
            return _layout_unsupported(streams, "the default audio layout is not supported")
        return defaults[0], AudioSelectionReason.UNIQUE_DEFAULT
    candidates = tuple(stream for stream in audio_streams if _audio_rank(stream) is not None)
    if not candidates:
        return _layout_unsupported(streams, "no supported policy-1.0 audio layout is present")
    if len(candidates) == 1:
        return candidates[0], AudioSelectionReason.SINGLE_ELIGIBLE
    ranked = tuple((stream, _audio_rank(stream)) for stream in candidates)
    maximum = max(rank for _stream, rank in ranked if rank is not None)
    winners = tuple(stream for stream, rank in ranked if rank == maximum)
    if len(winners) != 1:
        return _ambiguous(streams, "audio", "audio candidates share the highest layout rank")
    return winners[0], AudioSelectionReason.UNIQUE_HIGHEST_SUPPORTED_LAYOUT


def select_streams(streams: tuple[MediaStream, ...]) -> StreamSelectionResult:
    """Run the only productive policy-1.0 selection algorithm."""
    evidence = tuple(sorted(streams, key=lambda stream: stream.index))
    video = _select_video(evidence)
    if isinstance(video, ProbeAmbiguousStreams | ProbeUnsupportedMedia):
        return video
    audio = _select_audio(evidence)
    if isinstance(audio, ProbeAmbiguousStreams | ProbeUnsupportedMedia):
        return audio
    selected_video, video_reason = video
    selected_audio, audio_reason = audio
    return StreamsSelected(
        FinalizedStreamSelection(
            selected_video,
            selected_audio,
            evidence,
            video_reason,
            audio_reason,
        )
    )


def selection_semantically_matches(
    selection: FinalizedStreamSelection,
    independently_bound_streams: tuple[MediaStream, ...],
) -> bool:
    """Re-run the same selector and compare every authority-bearing field exactly."""
    try:
        expected_result = select_streams(independently_bound_streams)
        if not isinstance(expected_result, StreamsSelected):
            return False
        expected = expected_result.selection
        return (
            selection.policy_id == expected.policy_id
            and selection.stream_selection_evidence_digest
            == expected.stream_selection_evidence_digest
            and selection.video_index == expected.video_index
            and selection.audio_index == expected.audio_index
            and selection.video_reason_code is expected.video_reason_code
            and selection.audio_reason_code is expected.audio_reason_code
            and selection.selection_identity == expected.selection_identity
        )
    except (AttributeError, TypeError, ValueError):
        return False
