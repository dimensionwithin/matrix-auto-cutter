"""CLI entry point: ``python -m matrix_auto_cutter.repeat.cli``. No interaction."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from matrix_auto_cutter.repeat.asr import DEFAULT_MAX_SEGMENT_LEN, DEFAULT_THREADS, run_whisper
from matrix_auto_cutter.repeat.audio import extract_audio, probe_duration_ms
from matrix_auto_cutter.repeat.boundary import (
    DEFAULT_MAX_WINDOW_WORDS,
    DEFAULT_MIN_WINDOW_WORDS,
    DEFAULT_SCORE_THRESHOLD,
    BoundaryDetectionParams,
)
from matrix_auto_cutter.repeat.detect import DetectionParams
from matrix_auto_cutter.repeat.diagnostics import build_diagnostics, write_diagnostics
from matrix_auto_cutter.repeat.errors import (
    BinaryNotFoundError,
    FfmpegError,
    FfprobeError,
    ModelNotFoundError,
    ProcessTimeoutError,
    RawOutputEmptyError,
    RawOutputMissingError,
    RepeatContractError,
    SourceNotFoundError,
    WhisperError,
)
from matrix_auto_cutter.repeat.process import NativeProcessRunner
from matrix_auto_cutter.repeat.transcript import RepeatTranscriptDocument, load_transcript
from matrix_auto_cutter.repeat.whisper_json import convert_whisper_output

_PROBE_TIMEOUT_MS = 30_000
_AUDIO_STREAM_SPECIFIER = "0:a:0"

_EXIT_CODES: tuple[tuple[type[RepeatContractError], int], ...] = (
    (BinaryNotFoundError, 2),
    (ModelNotFoundError, 3),
    (SourceNotFoundError, 4),
    (FfprobeError, 5),
    (FfmpegError, 6),
    (WhisperError, 7),
    (ProcessTimeoutError, 8),
    (RawOutputMissingError, 9),
    (RawOutputEmptyError, 10),
)


def _exit_code_for(exc: RepeatContractError) -> int:
    for exc_type, code in _EXIT_CODES:
        if isinstance(exc, exc_type):
            return code
    return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m matrix_auto_cutter.repeat.cli",
        description=(
            "Erkenne benachbarte Wiederholungen und Selbstkorrekturen in einem Transkript."
        ),
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--transcript", help="Pfad zur repeat_transcript/1.0-Datei")
    source_group.add_argument("--source", help="Pfad zur Audio-/Videoquelle für die Transkription")
    parser.add_argument("--out", required=True, help="Zielpfad der repeat_diagnostics/1.0-Datei")
    parser.add_argument(
        "--emit-transcript", help="Schreibt das konvertierte Transkript zusätzlich an diesen Pfad"
    )
    parser.add_argument("--whisper-binary", help="Pfad zu whisper-cli.exe (mit --source Pflicht)")
    parser.add_argument("--whisper-model", help="Pfad zum whisper-Modell (mit --source Pflicht)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg-Binärname oder -Pfad")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe-Binärname oder -Pfad")
    parser.add_argument("--work-dir", help="Arbeitsverzeichnis für WAV/JSON (mit --source Pflicht)")
    parser.add_argument("--window-start-ms", type=int, default=None)
    parser.add_argument("--window-end-ms", type=int, default=None)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--timeout-ms", type=int, default=None)
    parser.add_argument(
        "--max-segment-len",
        type=int,
        default=DEFAULT_MAX_SEGMENT_LEN,
        help="whisper-cli -ml (mit --source Pflicht); <= 0 unterdrückt das Flag",
    )
    parser.add_argument(
        "--boundary",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Zweiter Detektor für kurze Echos an der Äußerungsgrenze (repeat_diagnostics/1.1)",
    )
    parser.add_argument(
        "--boundary-threshold",
        type=float,
        default=DEFAULT_SCORE_THRESHOLD,
        help="Score-Schwelle des Boundary-Detektors",
    )
    parser.add_argument(
        "--boundary-min-words",
        type=int,
        default=DEFAULT_MIN_WINDOW_WORDS,
        help="Kleinste Fensterbreite (Wörter) des Boundary-Detektors",
    )
    parser.add_argument(
        "--boundary-max-words",
        type=int,
        default=DEFAULT_MAX_WINDOW_WORDS,
        help="Größte Fensterbreite (Wörter) des Boundary-Detektors",
    )
    return parser


def _require_source_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    missing = [
        name
        for name, value in (
            ("--whisper-binary", args.whisper_binary),
            ("--whisper-model", args.whisper_model),
            ("--work-dir", args.work_dir),
        )
        if value is None
    ]
    if missing:
        parser.error(f"--source erfordert: {', '.join(missing)}")


def _transcribe_source(args: argparse.Namespace) -> RepeatTranscriptDocument:
    runner = NativeProcessRunner()
    source_duration_ms = probe_duration_ms(args.source, args.ffprobe, runner, _PROBE_TIMEOUT_MS)
    window_duration_ms = source_duration_ms
    if args.window_start_ms is not None and args.window_end_ms is not None:
        window_duration_ms = args.window_end_ms - args.window_start_ms
    ffmpeg_timeout_ms = max(_PROBE_TIMEOUT_MS, window_duration_ms * 2)
    wav_path = extract_audio(
        args.source,
        args.ffmpeg,
        args.work_dir,
        runner,
        ffmpeg_timeout_ms,
        window_start_ms=args.window_start_ms,
        window_end_ms=args.window_end_ms,
    )
    whisper_result = run_whisper(
        wav_path,
        args.whisper_binary,
        args.whisper_model,
        runner,
        audio_duration_ms=window_duration_ms,
        threads=args.threads,
        timeout_ms=args.timeout_ms,
        max_segment_len=args.max_segment_len,
    )
    window_offset_ms = args.window_start_ms if args.window_start_ms is not None else 0
    return convert_whisper_output(
        whisper_result.raw_json,
        source_duration_ms=source_duration_ms,
        audio_stream_specifier=_AUDIO_STREAM_SPECIFIER,
        window_offset_ms=window_offset_ms,
    )


def _emit_transcript(path: str, transcript: RepeatTranscriptDocument) -> None:
    Path(path).write_text(transcript.model_dump_json(indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Validate/transcribe a source, detect adjacent repeats, and write diagnostics atomically."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.source is not None:
        _require_source_args(parser, args)
    try:
        transcript = (
            load_transcript(args.transcript)
            if args.transcript is not None
            else _transcribe_source(args)
        )
        if args.emit_transcript is not None:
            _emit_transcript(args.emit_transcript, transcript)
        boundary_params = (
            BoundaryDetectionParams(
                score_threshold=args.boundary_threshold,
                min_window_words=args.boundary_min_words,
                max_window_words=args.boundary_max_words,
            )
            if args.boundary
            else None
        )
        document = build_diagnostics(transcript, DetectionParams(), boundary_params)
        result = write_diagnostics(args.out, document)
    except RepeatContractError as exc:
        print(f"Vertragsfehler: {exc}", file=sys.stderr)
        return _exit_code_for(exc)
    if result.status != "written":
        print(f"Fehler beim Schreiben: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
