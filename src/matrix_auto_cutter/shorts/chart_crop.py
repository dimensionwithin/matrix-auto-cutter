r"""Stufe 3a: fester Chart-Ausschnitt aus dem gerenderten Video, frameexakt.

Schneidet je Kandidat einen festen, waagerecht verschiebbaren Bildausschnitt
aus dem gerenderten Video (``rendered_video.path`` aus ``shorts-job.json``)
und verkleinert ihn auf eine feste Ausgabegroesse. Das Verfahren folgt Stufe 1
(``avatar_cut.py``, abgenommen): ``trim``/``atrim`` auf Frames statt ``-ss``/
``-t`` auf Sekunden, Neukodierung von Bild UND Ton (keine Streamkopie - eine
Streamkopie kann nur an AAC-Paketgrenzen enden, gemessen 17 ms und 10 ms
Ueberhang in den Probelaeufen des ersten Baus), Ausgabe-Framerate ausdruecklich
gesetzt.

Setzt eine Quellaufloesung von 2560x1440 voraus (belegt per ffprobe im
Auftrag "shorts-stufe-3a", Schritt 0). Mausverfolgung, Cursorprotokoll,
Glaettung des Versatzes und ein Rueckfall bei negativem x sind AUSDRUECKLICH
NICHT Teil dieses Moduls - das ist Stufe 3b.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.cut_proposal import discover_ffmpeg
from matrix_auto_cutter.shorts.avatar_cut import probe_frame_count
from matrix_auto_cutter.shorts.candidates import Candidate, CandidatesSchemaError, load_candidates
from matrix_auto_cutter.shorts.frame_map import candidate_frame_span
from matrix_auto_cutter.shorts.inventory import discover_ffprobe

# ---------------------------------------------------------------------------
# Geometrie - vier benannte Konstanten an EINER Stelle (Auftrag
# shorts-stufe-3a). SOURCE_WIDTH/SOURCE_HEIGHT sind der geprüfte Vertrag
# (Schritt 0 des Auftrags: 2560x1440 per ffprobe belegt), nicht frei
# waehlbar - die Konstanten unten haengen an diesem Wert. Die ``assert``-
# Zeilen fangen ab, falls jemand SOURCE_WIDTH aendert, ohne die genannten
# Zahlen im Auftrag neu zu pruefen.
# ---------------------------------------------------------------------------

SOURCE_WIDTH = 2560
SOURCE_HEIGHT = 1440

CROP_WIDTH = 1728
"""Breite des Chart-Ausschnitts aus der Quelle, in Pixeln."""

CROP_HEIGHT = SOURCE_HEIGHT
"""Hoehe des Chart-Ausschnitts - volle Quellhoehe, kein senkrechter Versatz."""

X_OFFSET_MIN = 0
X_OFFSET_MAX = SOURCE_WIDTH - CROP_WIDTH
"""Waagerechter Weg des Ausschnitts: 0 (linke Kante) bis X_OFFSET_MAX (rechte Kante)."""

X_OFFSET_DEFAULT = X_OFFSET_MAX // 2
"""Voreinstellung, wenn ``ausschnitt.json`` keinen Eintrag fuer einen Kandidaten hat."""

SCALE_FACTOR = 0.625
"""Verkleinerungsfaktor vom Ausschnitt auf die Ausgabegroesse."""

OUTPUT_WIDTH = round(CROP_WIDTH * SCALE_FACTOR)
OUTPUT_HEIGHT = round(CROP_HEIGHT * SCALE_FACTOR)

assert X_OFFSET_MAX == 832, "X_OFFSET_MAX haengt an SOURCE_WIDTH/CROP_WIDTH - siehe Schritt 0"
assert X_OFFSET_DEFAULT == 416, "X_OFFSET_DEFAULT haengt an X_OFFSET_MAX - siehe Schritt 0"
assert OUTPUT_WIDTH == 1080, "OUTPUT_WIDTH haengt an CROP_WIDTH/SCALE_FACTOR - siehe Schritt 0"
assert OUTPUT_HEIGHT == 900, "OUTPUT_HEIGHT haengt an CROP_HEIGHT/SCALE_FACTOR - siehe Schritt 0"

SOURCE_FPS = 60
"""Bildrate des gerenderten Videos - belegt per ffprobe in Schritt 0 (avg_frame_rate
und r_frame_rate beide 60/1). Einzige Bildrate, mit der dieses Modul rechnet."""

AUSSCHNITT_FILE_NAME = "ausschnitt.json"
AUSSCHNITT_SCHEMA_VERSION = "1.0"

CHART_CROP_REPORT_SCHEMA_VERSION = "1.0"

# Vergleichsmass aus Schritt 0 des Auftrags shorts-stufe-3a-frames: eine
# abgenommene Stufe-1-Ausgabe (avatar-cut.mp4,
# artefakte/repeat/shorts/2026-08-09 18-54-14/) hat Videospur-start_time
# 0.000000 s und Tonspur-start_time 0.000000 s - Versatz 0 ms. Beide Spuren
# stammen aus demselben ffmpeg-filter_complex-Lauf mit ``setpts=PTS-STARTPTS``
# je Spur, genau wie hier - deshalb ist 0 ms der Massstab, den dieses Modul
# treffen soll.
BASELINE_AV_OFFSET_MS = 0.0


class AusschnittSchemaError(Exception):
    """``ausschnitt.json`` verletzt den Versatz-Kontrakt - kein stilles Klemmen."""


def _validate_offset(value: object, *, context: str) -> int:
    """Pruefe einen Versatzwert: Ganzzahl, gerade, in [X_OFFSET_MIN, X_OFFSET_MAX]."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise AusschnittSchemaError(f"{context}: Versatz muss eine Ganzzahl sein, nicht {value!r}")
    if value % 2 != 0:
        raise AusschnittSchemaError(f"{context}: Versatz {value} ist nicht gerade")
    if not (X_OFFSET_MIN <= value <= X_OFFSET_MAX):
        raise AusschnittSchemaError(
            f"{context}: Versatz {value} liegt ausserhalb [{X_OFFSET_MIN}, {X_OFFSET_MAX}]"
        )
    return value


def load_offsets(path: Path) -> dict[int, int]:
    """Lies ``ausschnitt.json``: Kandidat-Index -> Versatz.

    Fehlt die Datei, gibt es kein Ergebnis - der Aufrufer wendet dann
    ``X_OFFSET_DEFAULT`` an (siehe :func:`offset_for_candidate`). Ein
    vorhandener, aber ungueltiger Eintrag bricht dagegen mit einer konkreten
    Meldung ab statt ihn stillschweigend zu klemmen.
    """
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AusschnittSchemaError(f"{path} ist kein gueltiges JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AusschnittSchemaError(f"{path}: erwartet ein Objekt")
    raw = payload.get("versatz", {})
    if not isinstance(raw, dict):
        raise AusschnittSchemaError(f"{path}: 'versatz' muss ein Objekt sein")
    result: dict[int, int] = {}
    for key, value in raw.items():
        try:
            index = int(key)
        except (TypeError, ValueError) as exc:
            raise AusschnittSchemaError(
                f"{path}: Schluessel {key!r} ist kein Kandidat-Index"
            ) from exc
        result[index] = _validate_offset(value, context=f"{path}, Kandidat {index}")
    return result


def offset_for_candidate(offsets: dict[int, int], index: int) -> int:
    """Der Versatz eines Kandidaten: aus ``offsets``, sonst ``X_OFFSET_DEFAULT``."""
    return offsets.get(index, X_OFFSET_DEFAULT)


# ---------------------------------------------------------------------------
# ffmpeg: Ausschnitt frameexakt schneiden, verkleinern, Ton neu kodieren.
# Verfahren wie avatar_cut.build_ffmpeg_filter_complex/build_ffmpeg_arguments:
# trim/atrim plus setpts, Neukodierung, feste Ausgabe-Framerate.
# ---------------------------------------------------------------------------


def crop_scale_filter(x_offset: int) -> str:
    """Der Crop-plus-Skalierungs-Teilausdruck fuer einen gegebenen Versatz."""
    if not (X_OFFSET_MIN <= x_offset <= X_OFFSET_MAX):
        raise ValueError(f"x_offset {x_offset} liegt ausserhalb [{X_OFFSET_MIN}, {X_OFFSET_MAX}]")
    return (
        f"crop={CROP_WIDTH}:{CROP_HEIGHT}:{x_offset}:0,"
        f"scale={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}"
    )


def build_ffmpeg_filter_complex(
    *, start_frame: int, end_frame: int, x_offset: int, fps: int
) -> tuple[str, str, str]:
    """Baue den ``-filter_complex``-Ausdruck und die Ausgabelabels fuer den Ausschnitt.

    Frameexakt fuer Video (``trim=start_frame:end_frame``), aus denselben
    Frames in Sekunden gerechnet fuer Audio (``atrim``) - Audio kennt keine
    Frames, aber die Sekundengrenze muss von derselben Framezahl abgeleitet
    sein wie das Video, sonst laufen beide Spuren auseinander. Genau das
    Verfahren aus ``avatar_cut.build_ffmpeg_filter_complex``, hier auf einen
    einzelnen Ausschnitt statt mehrere Keep-Segmente angewendet - kein
    ``concat`` noetig, es gibt nur eine Spanne je Kandidat.
    """
    if end_frame <= start_frame:
        raise ValueError(f"end_frame ({end_frame}) muss nach start_frame ({start_frame}) liegen")
    start_s = start_frame / fps
    end_s = end_frame / fps
    video = (
        f"[0:v]trim=start_frame={start_frame}:end_frame={end_frame},"
        f"setpts=PTS-STARTPTS,{crop_scale_filter(x_offset)}[v0]"
    )
    audio = f"[0:a]atrim=start={start_s:.9f}:end={end_s:.9f},asetpts=PTS-STARTPTS[a0]"
    return f"{video};{audio}", "[v0]", "[a0]"


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    *,
    start_frame: int,
    end_frame: int,
    x_offset: int,
    fps: int = SOURCE_FPS,
) -> list[str]:
    """Vollstaendiges ffmpeg-Kommando fuer einen Kandidaten-Ausschnitt.

    Kein ``-ss`` vor ``-i``, kein ``-t``: der Ausschnitt wird ueber
    ``trim``/``atrim`` auf Frames gesetzt (siehe
    :func:`build_ffmpeg_filter_complex`). Ton wird neu kodiert (``aac``),
    nicht per Streamkopie uebernommen - eine Streamkopie kann nur an
    AAC-Paketgrenzen enden. Die Ausgabe-Framerate wird ausdruecklich per
    ``-r`` gesetzt, nicht dem Encoder ueberlassen.
    """
    filter_complex, video_label, audio_label = build_ffmpeg_filter_complex(
        start_frame=start_frame, end_frame=end_frame, x_offset=x_offset, fps=fps
    )
    return [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(input_path),
        "-filter_complex",
        filter_complex,
        "-map",
        video_label,
        "-map",
        audio_label,
        "-r",
        f"{float(fps):g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded ffmpeg-Prozessausgang - eigenstaendig, analog zu ``avatar_cut``."""

    exit_code: int
    stderr: bytes


ProcessRunner = Callable[[Sequence[str], int], ProcessResult]


def _default_process_runner(arguments: Sequence[str], timeout_seconds: int) -> ProcessResult:
    """Fuehre ein Kommando mit begrenzter Diagnoseausgabe aus - der reale Standardweg."""
    try:
        result = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return ProcessResult(-1, str(exc).encode("utf-8", errors="replace"))
    return ProcessResult(result.returncode, result.stdout or b"")


@dataclass(frozen=True, slots=True)
class ChartCropPlan:
    """Vollstaendig berechneter Zuschnittplan fuer genau einen Kandidaten."""

    candidate_index: int
    start_ms: int
    end_ms: int
    x_offset: int
    start_frame: int
    end_frame: int
    fps: int

    @property
    def expected_frame_count(self) -> int:
        """Erwartete Ausgabe-Framezahl - exakt ``end_frame - start_frame``, keine Toleranz."""
        return self.end_frame - self.start_frame

    @property
    def expected_duration_ms(self) -> int:
        """Erwartete Ausgabedauer aus den Millisekunden - Hinweis, nicht das Pruefmass."""
        return self.end_ms - self.start_ms


def plan_chart_crop(
    candidate: Candidate, *, offsets: dict[int, int], fps: int = SOURCE_FPS
) -> ChartCropPlan:
    """Baue den Zuschnittplan aus einem Kandidaten und den geladenen Versaetzen."""
    start_frame, end_frame = candidate_frame_span(candidate.start_ms, candidate.end_ms, fps)
    return ChartCropPlan(
        candidate_index=candidate.index,
        start_ms=candidate.start_ms,
        end_ms=candidate.end_ms,
        x_offset=offset_for_candidate(offsets, candidate.index),
        start_frame=start_frame,
        end_frame=end_frame,
        fps=fps,
    )


def run_chart_crop(
    *,
    input_path: Path,
    output_path: Path,
    plan: ChartCropPlan,
    ffmpeg_path: Path,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult:
    """Fuehre den geplanten Zuschnitt tatsaechlich per ffmpeg aus."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments = build_ffmpeg_arguments(
        ffmpeg_path,
        input_path,
        output_path,
        start_frame=plan.start_frame,
        end_frame=plan.end_frame,
        x_offset=plan.x_offset,
        fps=plan.fps,
    )
    return process_runner(arguments, timeout_seconds)


# ---------------------------------------------------------------------------
# Quellaufloesung pruefen - fail closed statt an falscher Stelle zu schneiden.
# ---------------------------------------------------------------------------


def probe_dimensions(
    video_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 30,
) -> tuple[int, int] | None:
    """Lies Breite und Hoehe des ersten Videostreams; ``None`` bei Fehlern."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="ignore").strip()
    width_text, _, height_text = text.partition("x")
    try:
        return int(width_text), int(height_text)
    except ValueError:
        return None


def probe_audio_track_count(
    video_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 30,
) -> int | None:
    """Zaehle die Tonspuren; ``None`` bei Fehlern."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="ignore").strip()
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines)


def _probe_stream_start_time(
    video_path: Path,
    select_stream: str,
    *,
    ffprobe_path: Path,
    timeout_seconds: int,
) -> float | None:
    """Lies ``start_time`` einer einzelnen Spur (z. B. ``v:0``); ``None`` bei Fehlern."""
    try:
        result = subprocess.run(
            [
                str(ffprobe_path),
                "-v",
                "error",
                "-select_streams",
                select_stream,
                "-show_entries",
                "stream=start_time",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="ignore").strip()
    try:
        return float(text)
    except ValueError:
        return None


def probe_av_offset_ms(
    video_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 30,
) -> float | None:
    """Versatz zwischen Bild- und Tonspur in Millisekunden, aus ``start_time`` je Spur."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return None
    video_start = _probe_stream_start_time(
        video_path, "v:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
    )
    audio_start = _probe_stream_start_time(
        video_path, "a:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
    )
    if video_start is None or audio_start is None:
        return None
    return abs(video_start - audio_start) * 1000.0


# ---------------------------------------------------------------------------
# Teil C: Ausgabe pruefen, fail closed mit einem eigenen Fehlercode je
# Bedingung - E_RENDER_VERIFY im Produktpfad sagt nicht, welche Bedingung
# gefallen ist, das soll sich hier nicht wiederholen.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyChecks:
    """Istwerte der vier Pruefungen aus Teil C, unabhaengig vom Ergebnis erhoben."""

    actual_frame_count: int | None
    expected_frame_count: int
    frame_count_ok: bool

    actual_width: int | None
    actual_height: int | None
    dimensions_ok: bool

    audio_track_count: int | None
    audio_track_count_ok: bool

    av_offset_ms: float | None
    baseline_av_offset_ms: float
    av_offset_ok: bool

    @property
    def all_ok(self) -> bool:
        """Alle vier Pruefungen bestanden."""
        return (
            self.frame_count_ok
            and self.dimensions_ok
            and self.audio_track_count_ok
            and self.av_offset_ok
        )

    @property
    def first_failure_code(self) -> str | None:
        """Fehlercode der ersten gefallenen Pruefung, ``None`` wenn alle bestanden."""
        if not self.frame_count_ok:
            return "frame_count_mismatch"
        if not self.dimensions_ok:
            return "dimension_mismatch"
        if not self.audio_track_count_ok:
            return "audio_track_count_invalid"
        if not self.av_offset_ok:
            return "av_offset_exceeds_baseline"
        return None


def verify_chart_crop_output(
    output_path: Path,
    plan: ChartCropPlan,
    *,
    ffprobe_path: Path | None = None,
    baseline_av_offset_ms: float = BASELINE_AV_OFFSET_MS,
    timeout_seconds: int = 120,
) -> VerifyChecks:
    """Erhebe alle vier Istwerte aus Teil C, unabhaengig davon, ob einer schon gefallen ist."""
    actual_frame_count = probe_frame_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    frame_count_ok = actual_frame_count == plan.expected_frame_count

    dimensions = probe_dimensions(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    actual_width, actual_height = dimensions if dimensions is not None else (None, None)
    dimensions_ok = dimensions == (OUTPUT_WIDTH, OUTPUT_HEIGHT)

    audio_track_count = probe_audio_track_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    audio_track_count_ok = audio_track_count == 1

    av_offset_ms = probe_av_offset_ms(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    av_offset_ok = av_offset_ms is not None and av_offset_ms <= baseline_av_offset_ms

    return VerifyChecks(
        actual_frame_count=actual_frame_count,
        expected_frame_count=plan.expected_frame_count,
        frame_count_ok=frame_count_ok,
        actual_width=actual_width,
        actual_height=actual_height,
        dimensions_ok=dimensions_ok,
        audio_track_count=audio_track_count,
        audio_track_count_ok=audio_track_count_ok,
        av_offset_ms=av_offset_ms,
        baseline_av_offset_ms=baseline_av_offset_ms,
        av_offset_ok=av_offset_ok,
    )


def chart_crop_report_payload(plan: ChartCropPlan, checks: VerifyChecks) -> dict[str, object]:
    """Baue den Inhalt des Laufberichts, nach dem Muster von ``avatar-cut.json``."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_chart_crop",
        "schema_version": CHART_CROP_REPORT_SCHEMA_VERSION,
        "candidate_index": plan.candidate_index,
        "start_frame": plan.start_frame,
        "end_frame": plan.end_frame,
        "x_offset": plan.x_offset,
        "fps": plan.fps,
        "checks": {
            "frame_count": {
                "expected": checks.expected_frame_count,
                "actual": checks.actual_frame_count,
                "ok": checks.frame_count_ok,
            },
            "dimensions": {
                "expected": [OUTPUT_WIDTH, OUTPUT_HEIGHT],
                "actual": [checks.actual_width, checks.actual_height],
                "ok": checks.dimensions_ok,
            },
            "audio_track_count": {
                "expected": 1,
                "actual": checks.audio_track_count,
                "ok": checks.audio_track_count_ok,
            },
            "av_offset_ms": {
                "baseline": checks.baseline_av_offset_ms,
                "actual": checks.av_offset_ms,
                "ok": checks.av_offset_ok,
            },
        },
        "all_ok": checks.all_ok,
    }


def write_chart_crop_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe den Laufbericht atomar - dasselbe Muster wie ``avatar-cut.json``."""
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
class Stage3aFailed:
    """Fail-closed Auskunft, warum kein Chart-Ausschnitt gebaut werden konnte."""

    code: str
    message_de: str


def run_stage3a_for_candidate(
    *,
    rendered_video_path: Path,
    kandidaten_path: Path,
    candidate_index: int,
    output_path: Path,
    ausschnitt_path: Path | None,
    ffmpeg_path: Path,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage3aFailed:
    """Ende-zu-Ende: Kandidat auswaehlen, Versatz laden, Quellaufloesung pruefen, schneiden.

    Schreibt nach einem erfolgreichen ffmpeg-Lauf einen Laufbericht neben die
    Ausgabe (``<output>.json``) mit den vier Pruefergebnissen aus Teil C. Ist
    eine der vier Pruefungen gefallen, ist das Ergebnis ``Stage3aFailed`` mit
    dem passenden, eigenstaendigen Fehlercode - der Bericht wird trotzdem
    geschrieben, damit der Befund nachvollziehbar bleibt.
    """
    dimensions = probe_dimensions(
        rendered_video_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if dimensions is None:
        return Stage3aFailed(
            "resolution_unknown",
            f"ffprobe konnte die Aufloesung nicht ermitteln: {rendered_video_path}",
        )
    if dimensions != (SOURCE_WIDTH, SOURCE_HEIGHT):
        return Stage3aFailed(
            "resolution_mismatch",
            f"Quellaufloesung {dimensions[0]}x{dimensions[1]} weicht von "
            f"{SOURCE_WIDTH}x{SOURCE_HEIGHT} ab - die Geometrie dieses Moduls setzt sie voraus",
        )

    try:
        candidates = load_candidates(kandidaten_path)
    except (OSError, CandidatesSchemaError) as exc:
        return Stage3aFailed("candidates_unreadable", str(exc))
    candidate = next((item for item in candidates if item.index == candidate_index), None)
    if candidate is None:
        return Stage3aFailed(
            "candidate_not_found", f"Kandidat {candidate_index} nicht in {kandidaten_path}"
        )

    try:
        offsets = load_offsets(ausschnitt_path) if ausschnitt_path is not None else {}
    except AusschnittSchemaError as exc:
        return Stage3aFailed("ausschnitt_invalid", str(exc))

    plan = plan_chart_crop(candidate, offsets=offsets)
    process_result = run_chart_crop(
        input_path=rendered_video_path,
        output_path=output_path,
        plan=plan,
        ffmpeg_path=ffmpeg_path,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    if process_result.exit_code != 0:
        return process_result

    checks = verify_chart_crop_output(
        output_path, plan, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    write_chart_crop_report(report_path, chart_crop_report_payload(plan, checks))
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return Stage3aFailed(
            failure_code,
            f"Kandidat {candidate_index}: Pruefung '{failure_code}' fehlgeschlagen, "
            f"Bericht: {report_path}",
        )
    return process_result


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: einen einzelnen Kandidaten aus einer ``kandidaten.json`` zuschneiden."""
    import argparse

    parser = argparse.ArgumentParser(description="Stufe 3a: fester Chart-Ausschnitt")
    parser.add_argument("kandidaten_path", type=Path)
    parser.add_argument("rendered_video_path", type=Path)
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--ausschnitt", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    result = run_stage3a_for_candidate(
        rendered_video_path=args.rendered_video_path,
        kandidaten_path=args.kandidaten_path,
        candidate_index=args.index,
        output_path=args.output,
        ausschnitt_path=args.ausschnitt,
        ffmpeg_path=Path(ffmpeg_path),
        ffprobe_path=args.ffprobe,
    )
    if isinstance(result, Stage3aFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.exit_code != 0:
        print(f"ffmpeg fehlgeschlagen: {result.stderr.decode('utf-8', errors='replace')}")
        return 1
    print(f"geschrieben: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
