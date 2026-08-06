"""Extract review-audio snippets (m4a) around diagnosed candidate pairs.

Reuses the same injectable ``ProcessRunner`` seam as ``audio.py`` -- no
second process-execution mechanism is introduced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from matrix_auto_cutter.repeat.errors import (
    FfmpegError,
    ProcessTimeoutError,
    SourceNotFoundError,
)
from matrix_auto_cutter.repeat.process import ProcessRunner

_PADDING_MS = 2_000
_AUDIO_STREAM_SPECIFIER = "0:a:0"
_AAC_BITRATE = "64k"


class _SpanLike(Protocol):
    start_ms: int
    end_ms: int


class _CandidateLike(Protocol):
    first: _SpanLike
    second: _SpanLike


def _seconds_arg(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.3f}"


def build_snippet_ffmpeg_argv(
    ffmpeg_path: str,
    source_path: str | Path,
    output_path: str | Path,
    start_ms: int,
    end_ms: int,
) -> list[str]:
    """Build the argv extracting a mono AAC (64 kbit/s) m4a clip via ``-ss``/``-to``."""
    return [
        ffmpeg_path,
        "-nostdin",
        "-y",
        "-i",
        str(source_path),
        "-ss",
        _seconds_arg(start_ms),
        "-to",
        _seconds_arg(end_ms),
        "-map",
        _AUDIO_STREAM_SPECIFIER,
        "-ac",
        "1",
        "-c:a",
        "aac",
        "-b:a",
        _AAC_BITRATE,
        str(output_path),
    ]


def clip_window_ms(
    first_start_ms: int,
    second_end_ms: int,
    source_duration_ms: int,
    padding_ms: int = _PADDING_MS,
) -> tuple[int, int]:
    """Clamp ``[first_start - padding, second_end + padding]`` to ``[0, source_duration_ms]``."""
    start = max(0, first_start_ms - padding_ms)
    end = min(source_duration_ms, second_end_ms + padding_ms)
    return start, end


def extract_snippet(
    source_path: str | Path,
    ffmpeg_path: str,
    output_path: str | Path,
    start_ms: int,
    end_ms: int,
    runner: ProcessRunner,
    timeout_ms: int,
) -> Path:
    """Extract one m4a snippet ``[start_ms, end_ms)`` from ``source_path``."""
    source = Path(source_path)
    if not source.is_file():
        raise SourceNotFoundError(str(source))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    argv = build_snippet_ffmpeg_argv(ffmpeg_path, source, output, start_ms, end_ms)
    result = runner(argv, timeout_ms)
    if result.timed_out:
        raise ProcessTimeoutError("ffmpeg", timeout_ms, result.exit_code, result.stderr)
    if result.exit_code != 0:
        raise FfmpegError(result.exit_code, result.stderr)
    return output


@dataclass(frozen=True)
class SnippetManifestEntry:
    """One row of ``snippets.json``: where the clip lives and how it maps back to the source."""

    candidate_id: str
    nr: int
    path: str | None
    clip_start_ms: int
    clip_end_ms: int
    first_offset_ms: tuple[int, int]
    second_offset_ms: tuple[int, int]
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain JSON-able dict."""
        return {
            "candidate_id": self.candidate_id,
            "nr": self.nr,
            "path": self.path,
            "clip_start_ms": self.clip_start_ms,
            "clip_end_ms": self.clip_end_ms,
            "first_offset_ms": {
                "start_ms": self.first_offset_ms[0],
                "end_ms": self.first_offset_ms[1],
            },
            "second_offset_ms": {
                "start_ms": self.second_offset_ms[0],
                "end_ms": self.second_offset_ms[1],
            },
            "error": self.error,
        }


def _timeout_ms(clip_duration_ms: int) -> int:
    return max(30_000, clip_duration_ms * 2)


def build_snippets(
    candidates: list[_CandidateLike],
    stem: str,
    source_path: str | Path,
    source_duration_ms: int,
    ffmpeg_path: str,
    snippet_dir: str | Path,
    runner: ProcessRunner,
) -> list[SnippetManifestEntry]:
    """Extract one m4a snippet per candidate; per-candidate failures are recorded, not raised."""
    out_dir = Path(snippet_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    entries: list[SnippetManifestEntry] = []
    for nr, candidate in enumerate(candidates, start=1):
        clip_start_ms, clip_end_ms = clip_window_ms(
            candidate.first.start_ms, candidate.second.end_ms, source_duration_ms
        )
        candidate_id = f"{stem}_{nr:03d}"
        output_path = out_dir / f"{candidate_id}.m4a"
        first_offset = (
            candidate.first.start_ms - clip_start_ms,
            candidate.first.end_ms - clip_start_ms,
        )
        second_offset = (
            candidate.second.start_ms - clip_start_ms,
            candidate.second.end_ms - clip_start_ms,
        )
        try:
            extract_snippet(
                source_path,
                ffmpeg_path,
                output_path,
                clip_start_ms,
                clip_end_ms,
                runner,
                _timeout_ms(clip_end_ms - clip_start_ms),
            )
        except (FfmpegError, ProcessTimeoutError, SourceNotFoundError) as exc:
            entries.append(
                SnippetManifestEntry(
                    candidate_id=candidate_id,
                    nr=nr,
                    path=None,
                    clip_start_ms=clip_start_ms,
                    clip_end_ms=clip_end_ms,
                    first_offset_ms=first_offset,
                    second_offset_ms=second_offset,
                    error=str(exc),
                )
            )
            continue
        entries.append(
            SnippetManifestEntry(
                candidate_id=candidate_id,
                nr=nr,
                path=str(output_path),
                clip_start_ms=clip_start_ms,
                clip_end_ms=clip_end_ms,
                first_offset_ms=first_offset,
                second_offset_ms=second_offset,
            )
        )
    return entries


def write_snippet_manifest(snippet_dir: str | Path, entries: list[SnippetManifestEntry]) -> Path:
    """Write ``snippets.json`` into ``snippet_dir`` and return its path."""
    manifest_path = Path(snippet_dir) / "snippets.json"
    payload = [entry.to_dict() for entry in entries]
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return manifest_path
