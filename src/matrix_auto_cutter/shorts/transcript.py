r"""Stufe 2, Transkription: aus einer Videodatei ein Transkript über die vorhandene ASR-Kette.

``asr.py``/``audio.py`` im repeat-Paket sind bereits generisch für jede
WAV-Datei gebaut (Auftrag 15, ``VORBEREITUNG-2026-08-11.md`` Abschnitt 3):
``run_whisper()``/``build_whisper_argv()`` nehmen Binärpfad, Modellpfad,
WAV-Pfad, Threads, ``max_segment_len`` und ``initial_prompt`` entgegen und
kennen das repeat-Paket selbst nicht. Dieses Modul zieht nur den Ton aus
einer Videodatei (``repeat.audio.extract_audio``) und ruft die vorhandene
Kette auf - keine zweite Transkriptionslogik.

Deutsch (``-l de``) und wortgenaues JSON (``-ojf``) sind in
``build_whisper_argv`` bereits fest verdrahtet, keine Übersteuerung nötig.
``--prompt`` läuft hier absichtlich nicht mit - die Vokabeldatei ist gemessen
wirkungslos (``SHORTS-1-UEBERGABE-2026-08-07.md`` Abschnitt 2.2).
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.repeat.asr import (
    DEFAULT_THREADS,
    default_timeout_ms,
    run_whisper,
    whisper_json_path,
)
from matrix_auto_cutter.repeat.audio import extract_audio
from matrix_auto_cutter.repeat.process import ProcessRunner

TRANSCRIPT_SCHEMA_VERSION = "1.0"
RAW_WAV_NAME = "transkript.wav"
TRANSCRIPT_FILE_NAME = "transkript.json"

# Eigene Dateinamen fuer die gerenderte Fassung (Auftrag 19/20): Ein Lauf
# ueber ``rendered_video.path`` darf das vorhandene Rohtranskript
# (``transkript.json``, Grundlage aller bisherigen Analysen) nie
# ueberschreiben. Siehe TRANSKRIPTQUELLE-2026-08-14.md Abschnitt B: die
# gerenderte Fassung ist als Transkriptquelle vorzuziehen, weil sie weder
# Intro-/Outro-Musik noch herausgeschnittene Stillen enthaelt und ihre
# Zeiten bereits auf derselben Achse wie die fertigen Shorts liegen.
RENDERED_WAV_NAME = "transkript-rendered.wav"
RENDERED_TRANSCRIPT_FILE_NAME = "transkript-rendered.json"

# Größer als der repeat-Diagnosewert (60): für die Kandidatensuche in Stufe 2
# soll ein zusammenhängender Gedanke nicht an einer festen Segmentgrenze
# zerreißen, wie es bei der repeat-Diagnose (kurze, präzise Segmente) gewollt
# ist. 120 s ist der doppelte repeat-Wert - ein erster, nicht gemessener
# Kompromiss; siehe Auftrag 15, Abschnitt 2.1.
DEFAULT_MAX_SEGMENT_LEN = 120


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """Ein Segment aus der whisper-Rohausgabe: Zeitspanne in Millisekunden plus Text."""

    start_ms: int
    end_ms: int
    text: str


def transcript_paths(
    target_dir: Path,
    *,
    wav_name: str = RAW_WAV_NAME,
    transcript_file_name: str = TRANSCRIPT_FILE_NAME,
) -> tuple[Path, Path]:
    """Pfade der Rohausgabe (``<wav_name>.json``) und des aufbereiteten Transkripts.

    ``wav_name``/``transcript_file_name`` wechseln zwischen der Rohaufnahme
    (Vorgabe) und der gerenderten Fassung (``RENDERED_WAV_NAME``,
    ``RENDERED_TRANSCRIPT_FILE_NAME``) - unterschiedliche Namen, damit ein
    Lauf über die gerenderte Fassung das vorhandene Rohtranskript nie
    überschreibt.
    """
    raw_json_path = whisper_json_path(target_dir / wav_name)
    return raw_json_path, target_dir / transcript_file_name


def parse_segments(raw_json: str) -> list[TranscriptSegment]:
    """Lies die Segmentliste aus whisper-cli's ``-ojf``-Rohausgabe.

    Fehlende oder unlesbare Felder werden als 0/leer behandelt statt die
    ganze Aufbereitung scheitern zu lassen - die Rohausgabe bleibt in jedem
    Fall unangetastet auf der Platte stehen und kann von Hand geprüft werden.
    """
    payload = json.loads(raw_json)
    entries = payload.get("transcription", []) if isinstance(payload, dict) else []
    segments: list[TranscriptSegment] = []
    for entry in entries:
        offsets = entry.get("offsets", {}) if isinstance(entry, dict) else {}
        text = str(entry.get("text", "")).strip() if isinstance(entry, dict) else ""
        segments.append(
            TranscriptSegment(
                start_ms=int(offsets.get("from", 0)),
                end_ms=int(offsets.get("to", 0)),
                text=text,
            )
        )
    return segments


def build_transcript_payload(
    segments: Sequence[TranscriptSegment], *, source_video: str
) -> dict[str, object]:
    """Baue den JSON-Inhalt von ``transkript.json`` aus den geparsten Segmenten."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_transcript",
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "source_video": source_video,
        "segment_count": len(segments),
        "segments": [
            {"start_ms": segment.start_ms, "end_ms": segment.end_ms, "text": segment.text}
            for segment in segments
        ],
    }


def write_transcript(path: Path, payload: dict[str, object]) -> None:
    """Schreibe ``transkript.json`` atomar - dasselbe Muster wie ``avatar-cut.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.tmp.",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, path, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """Ergebnis eines Transkriptionslaufs."""

    status: Literal["written", "reused"]
    raw_json_path: str
    transcript_path: str
    segment_count: int


def transcribe_video(
    video_path: Path,
    *,
    target_dir: Path,
    ffmpeg_path: str,
    whisper_binary: str,
    whisper_model: str,
    runner: ProcessRunner,
    audio_duration_ms: int,
    threads: int = DEFAULT_THREADS,
    max_segment_len: int = DEFAULT_MAX_SEGMENT_LEN,
    ffmpeg_timeout_ms: int | None = None,
    whisper_timeout_ms: int | None = None,
    force: bool = False,
    wav_name: str = RAW_WAV_NAME,
    transcript_file_name: str = TRANSCRIPT_FILE_NAME,
) -> TranscriptResult:
    """Ende-zu-Ende: Ton ziehen, whisper aufrufen, Transkript schreiben.

    Existiert die Rohausgabe (``<wav_name>.json``) schon und ``force`` ist
    falsch, wird sie wiederverwendet statt neu zu transkribieren (Auftrag 15,
    Abschnitt 2.2) - auch nach einem vorherigen Fehlschlag in der Aufbereitung
    (``UMGEBUNG.md``: nie neu transkribieren, die Rohausgabe steht noch). Die
    extrahierte WAV wird nach einem erfolgreichen Lauf gelöscht (``ABLAGE.md``)
    - sie ist groß und in Sekunden neu erzeugbar. ``wav_name``/
    ``transcript_file_name`` steuern, ob die Rohaufnahme (Vorgabe) oder die
    gerenderte Fassung (``RENDERED_WAV_NAME``, ``RENDERED_TRANSCRIPT_FILE_NAME``,
    Auftrag 19/20) transkribiert wird, ohne das jeweils andere Transkript zu
    berühren.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_json_path, transcript_path = transcript_paths(
        target_dir, wav_name=wav_name, transcript_file_name=transcript_file_name
    )
    wav_path = target_dir / wav_name

    status: Literal["written", "reused"]
    if raw_json_path.is_file() and not force:
        raw_json = raw_json_path.read_text(encoding="utf-8")
        status = "reused"
    else:
        extracted_wav_path = extract_audio(
            video_path,
            ffmpeg_path,
            target_dir,
            runner,
            ffmpeg_timeout_ms
            if ffmpeg_timeout_ms is not None
            else default_timeout_ms(audio_duration_ms),
        )
        extracted_wav_path.replace(wav_path)
        whisper_result = run_whisper(
            wav_path,
            whisper_binary,
            whisper_model,
            runner,
            audio_duration_ms=audio_duration_ms,
            threads=threads,
            timeout_ms=whisper_timeout_ms,
            max_segment_len=max_segment_len,
            initial_prompt=None,
        )
        raw_json = whisper_result.raw_json
        wav_path.unlink(missing_ok=True)
        status = "written"

    segments = parse_segments(raw_json)
    payload = build_transcript_payload(segments, source_video=video_path.name)
    write_transcript(transcript_path, payload)
    return TranscriptResult(
        status=status,
        raw_json_path=str(raw_json_path),
        transcript_path=str(transcript_path),
        segment_count=len(segments),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI für Teil 1: Transkript der *gerenderten* Fassung aus ``shorts-job.json``.

    Liest ``rendered_video.path`` aus der Auftragsdatei (Stufe 0) und
    transkribiert mit den Auftrag-19/20-Namen (``RENDERED_WAV_NAME``,
    ``RENDERED_TRANSCRIPT_FILE_NAME``), niemals mit den Rohaufnahme-Namen -
    das vorhandene Rohtranskript bleibt in jedem Fall unangetastet.
    """
    import argparse

    from matrix_auto_cutter.cut_proposal import discover_ffmpeg
    from matrix_auto_cutter.repeat.process import NativeProcessRunner
    from matrix_auto_cutter.shorts.inventory import discover_ffprobe, probe_duration_ms

    parser = argparse.ArgumentParser(
        description="Shorts Stufe 2, Teil 1: Transkript der gerenderten Fassung"
    )
    parser.add_argument("job_path", type=Path, help="Pfad zur shorts-job.json")
    parser.add_argument("--whisper-binary", required=True, type=Path)
    parser.add_argument("--whisper-model", required=True, type=Path)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--max-segment-len", type=int, default=DEFAULT_MAX_SEGMENT_LEN)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    ffmpeg_path = str(args.ffmpeg) if args.ffmpeg is not None else discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH prüfen oder --ffmpeg angeben)")
        return 2
    ffprobe_path = args.ffprobe if args.ffprobe is not None else discover_ffprobe()
    if ffprobe_path is None:
        print("ffprobe nicht gefunden (PATH prüfen oder --ffprobe angeben)")
        return 2

    job = json.loads(args.job_path.read_text(encoding="utf-8"))
    video_path = Path(job["rendered_video"]["path"])
    duration_ms = probe_duration_ms(video_path, Path(ffprobe_path))
    if duration_ms is None:
        print(f"ffprobe konnte die Dauer von {video_path} nicht bestimmen")
        return 2

    result = transcribe_video(
        video_path,
        target_dir=args.job_path.parent,
        ffmpeg_path=str(ffmpeg_path),
        whisper_binary=str(args.whisper_binary),
        whisper_model=str(args.whisper_model),
        runner=NativeProcessRunner(),
        audio_duration_ms=duration_ms,
        threads=args.threads,
        max_segment_len=args.max_segment_len,
        force=args.force,
        wav_name=RENDERED_WAV_NAME,
        transcript_file_name=RENDERED_TRANSCRIPT_FILE_NAME,
    )
    print(f"{result.status}: {result.segment_count} Segmente -> {result.transcript_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
