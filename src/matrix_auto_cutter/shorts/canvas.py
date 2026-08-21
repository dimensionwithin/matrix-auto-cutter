r"""Stufe 5a: Chartpanel auf die senkrechte Leinwand setzen.

Setzt ein fertiges 1080x900-Chartpanel (Ausgabe von Stufe 3a) auf eine
1080x1920-Leinwand mit schwarzem Hintergrund (RGB 0,0,0 - Auftrag
shorts-hintergrund-schwarz, Teil A/B: gemessen aus einem Einzelbild der
Mitte von ``avatar-cut.mp4`` fuer 2026-08-07 11-35-16, dieselbe Farbe wie
der Avatarhintergrund, damit an der Avatarkante keine Kante sichtbar ist -
ausdrueckliche Entscheidung des Nutzers vom 18.8.). Das Panel sitzt buendig
ab y=200, x=0. Kein Neuschnitt aus dem Quellvideo - die Eingabe ist bereits
die fertige Chartpanel-Datei aus Stufe 3a. ``pad`` statt ``overlay``: ein
einziger Eingabestrom, keine zwei Quellen, die zueinander synchron gehalten
werden muessten - die Framezahl bleibt dadurch unveraendert.

Avatar, Untertitel, Endcard, Schriften und weitere Designsystem-Token sind
AUSDRUECKLICH NICHT Teil dieses Moduls - das ist Stufe 5b bis 5d.
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
from matrix_auto_cutter.shorts.chart_crop import probe_audio_track_count, probe_dimensions
from matrix_auto_cutter.shorts.inventory import discover_ffprobe

# ---------------------------------------------------------------------------
# Geometrie - benannte Konstanten an EINER Stelle (Auftrag shorts-stufe-5a).
# Herkunft: docs\repeat\SHORTS-KONTEXT-2026-08-09.md, Abschnitt 5
# (Sicherheitszone, Aufteilung). Der Hintergrundfarbwert stammt NICHT mehr
# von dort, siehe BACKGROUND_COLOR_RGB unten (Auftrag shorts-hintergrund-schwarz).
# ---------------------------------------------------------------------------

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920
"""Groesse der senkrechten Leinwand - der Vertrag fuer alle Stufe-5-Module."""

PANEL_WIDTH = 1080
PANEL_HEIGHT = 900
"""Erwartete Groesse der Eingabe - die Chartpanel-Ausgabe aus Stufe 3a."""

PANEL_X = 0
PANEL_Y = 200
"""Position des Panels auf der Leinwand - buendig links, ab y=200."""

BACKGROUND_COLOR_RGB = (0, 0, 0)
"""Gleiche Farbe wie der Avatarhintergrund, damit an der Avatarkante keine
Kante sichtbar ist (Auftrag shorts-hintergrund-schwarz, Teil A/B) -
ausdruecklich so entschieden vom Nutzer am 18.8., nicht mehr das
``--ink``-Designsystem-Token vom 9.8. Gemessen an einem Einzelbild aus der
Mitte von ``avatar-cut.mp4`` (Aufnahme 2026-08-07 11-35-16): (0,0,0) ist im
gesamten Bild UND in allen vier Bildecken die klar dominante Farbe. Die
Marke bleibt ueber das Chart praesent, das dieselbe Farbfamilie traegt."""

BACKGROUND_COLOR_HEX = "000000"
"""Dieselbe Farbe als ffmpeg-Farbausdruck (``0x000000``), ohne Alpha."""

CANVAS_FPS = 60
"""Ausgabe-Bildrate - ausdruecklich gesetzt, nicht dem Encoder ueberlassen."""

assert PANEL_Y + PANEL_HEIGHT == 1100, "Panel-Ende haengt an PANEL_Y/PANEL_HEIGHT - Abschnitt 5"
assert PANEL_X + PANEL_WIDTH == CANVAS_WIDTH, "Panel-Breite haengt an CANVAS_WIDTH - Abschnitt 5"
assert PANEL_Y + PANEL_HEIGHT <= CANVAS_HEIGHT, "Panel muss auf der Leinwand liegen"

# Sicherheitszone aus Abschnitt 5 - 5a braucht sie noch nicht, 5b und 5c
# (Avatar, Untertitel) greifen darauf zu. Hier hinterlegt, damit es nur eine
# Stelle fuer diese Zahlen gibt.
SAFE_TOP = 200
SAFE_BOTTOM = 480
SAFE_RIGHT = 150

CANVAS_REPORT_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# ffmpeg: Panel per pad auf die Leinwand setzen, Ton unveraendert uebernehmen.
# ---------------------------------------------------------------------------


def build_ffmpeg_filter_complex() -> tuple[str, str]:
    """Baue den ``-filter_complex``-Ausdruck und das Ausgabelabel fuer die Leinwand."""
    video = (
        f"[0:v]pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{PANEL_X}:{PANEL_Y}:"
        f"0x{BACKGROUND_COLOR_HEX}[v0]"
    )
    return video, "[v0]"


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    *,
    fps: int = CANVAS_FPS,
) -> list[str]:
    """Vollstaendiges ffmpeg-Kommando: Panel auf die Leinwand setzen.

    ``pad`` haengt Raender an - die Framezahl bleibt dabei exakt erhalten,
    anders als bei ``trim``/``atrim`` in Stufe 3a, die hier nicht gebraucht
    werden: die Eingabe ist bereits der fertige Ausschnitt. Ton wird per
    ``-c:a copy`` unveraendert aus der Eingabe uebernommen - er wird von
    diesem Modul nicht angefasst.
    """
    filter_complex, video_label = build_ffmpeg_filter_complex()
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
        "0:a",
        "-r",
        f"{float(fps):g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(output_path),
    ]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded ffmpeg-Prozessausgang - eigenstaendig, analog zu ``chart_crop``."""

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


def run_canvas(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
    fps: int = CANVAS_FPS,
) -> ProcessResult:
    """Fuehre das Setzen auf die Leinwand tatsaechlich per ffmpeg aus."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    arguments = build_ffmpeg_arguments(ffmpeg_path, input_path, output_path, fps=fps)
    return process_runner(arguments, timeout_seconds)


# ---------------------------------------------------------------------------
# Eingabe pruefen - fail closed statt an falscher Stelle zu padden.
# ---------------------------------------------------------------------------


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


@dataclass(frozen=True, slots=True)
class Stage5aFailed:
    """Fail-closed Auskunft, warum keine Leinwand gebaut werden konnte."""

    code: str
    message_de: str


# ---------------------------------------------------------------------------
# Teil C: Ausgabe pruefen, vier unabhaengige Pruefungen mit je eigenem
# Fehlercode - kein Sammelcode, nach dem Muster von chart_crop.py.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class VerifyChecks:
    """Istwerte der vier Pruefungen aus Teil C, unabhaengig vom Ergebnis erhoben."""

    input_frame_count: int | None
    output_frame_count: int | None
    frame_count_ok: bool

    actual_width: int | None
    actual_height: int | None
    dimensions_ok: bool

    audio_track_count: int | None
    audio_track_count_ok: bool

    video_start_time: float | None
    audio_start_time: float | None
    start_time_ok: bool

    @property
    def all_ok(self) -> bool:
        """Alle vier Pruefungen bestanden."""
        return (
            self.frame_count_ok
            and self.dimensions_ok
            and self.audio_track_count_ok
            and self.start_time_ok
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
        if not self.start_time_ok:
            return "start_time_nonzero"
        return None


def verify_canvas_output(
    input_path: Path,
    output_path: Path,
    *,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 120,
) -> VerifyChecks:
    """Erhebe alle vier Istwerte aus Teil C, unabhaengig davon, ob einer schon gefallen ist."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()

    input_frame_count = probe_frame_count(
        input_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    output_frame_count = probe_frame_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    frame_count_ok = (
        input_frame_count is not None and input_frame_count == output_frame_count
    )

    dimensions = probe_dimensions(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    actual_width, actual_height = dimensions if dimensions is not None else (None, None)
    dimensions_ok = dimensions == (CANVAS_WIDTH, CANVAS_HEIGHT)

    audio_track_count = probe_audio_track_count(
        output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    audio_track_count_ok = audio_track_count == 1

    if ffprobe is None:
        video_start_time = None
        audio_start_time = None
    else:
        video_start_time = _probe_stream_start_time(
            output_path, "v:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
        )
        audio_start_time = _probe_stream_start_time(
            output_path, "a:0", ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
        )
    start_time_ok = video_start_time == 0.0 and audio_start_time == 0.0

    return VerifyChecks(
        input_frame_count=input_frame_count,
        output_frame_count=output_frame_count,
        frame_count_ok=frame_count_ok,
        actual_width=actual_width,
        actual_height=actual_height,
        dimensions_ok=dimensions_ok,
        audio_track_count=audio_track_count,
        audio_track_count_ok=audio_track_count_ok,
        video_start_time=video_start_time,
        audio_start_time=audio_start_time,
        start_time_ok=start_time_ok,
    )


def canvas_report_payload(checks: VerifyChecks) -> dict[str, object]:
    """Baue den Inhalt des Laufberichts, nach dem Muster von ``chart-crop.json``."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_canvas",
        "schema_version": CANVAS_REPORT_SCHEMA_VERSION,
        "checks": {
            "frame_count": {
                "input": checks.input_frame_count,
                "output": checks.output_frame_count,
                "ok": checks.frame_count_ok,
            },
            "dimensions": {
                "expected": [CANVAS_WIDTH, CANVAS_HEIGHT],
                "actual": [checks.actual_width, checks.actual_height],
                "ok": checks.dimensions_ok,
            },
            "audio_track_count": {
                "expected": 1,
                "actual": checks.audio_track_count,
                "ok": checks.audio_track_count_ok,
            },
            "start_time": {
                "video": checks.video_start_time,
                "audio": checks.audio_start_time,
                "ok": checks.start_time_ok,
            },
        },
        "all_ok": checks.all_ok,
    }


def write_canvas_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe den Laufbericht atomar - dasselbe Muster wie ``chart-crop.json``."""
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


def run_stage5a(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage5aFailed:
    """Ende-zu-Ende: Eingabegroesse pruefen, Leinwand bauen, Ergebnis pruefen.

    Schreibt nach einem erfolgreichen ffmpeg-Lauf einen Laufbericht neben die
    Ausgabe (``<output>.json``) mit den vier Pruefergebnissen aus Teil C. Ist
    eine der vier Pruefungen gefallen, ist das Ergebnis ``Stage5aFailed`` mit
    dem passenden, eigenstaendigen Fehlercode - der Bericht wird trotzdem
    geschrieben, damit der Befund nachvollziehbar bleibt.
    """
    dimensions = probe_dimensions(
        input_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if dimensions is None:
        return Stage5aFailed(
            "input_resolution_unknown",
            f"ffprobe konnte die Aufloesung nicht ermitteln: {input_path}",
        )
    if dimensions != (PANEL_WIDTH, PANEL_HEIGHT):
        return Stage5aFailed(
            "input_resolution_mismatch",
            f"Eingabegroesse {dimensions[0]}x{dimensions[1]} weicht von "
            f"{PANEL_WIDTH}x{PANEL_HEIGHT} ab - die Geometrie dieses Moduls setzt sie voraus",
        )

    process_result = run_canvas(
        input_path=input_path,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    if process_result.exit_code != 0:
        return process_result

    checks = verify_canvas_output(
        input_path, output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    write_canvas_report(report_path, canvas_report_payload(checks))
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return Stage5aFailed(
            failure_code,
            f"Pruefung '{failure_code}' fehlgeschlagen, Bericht: {report_path}",
        )
    return process_result


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: eine fertige Chartpanel-Datei auf die Leinwand setzen."""
    import argparse

    parser = argparse.ArgumentParser(description="Stufe 5a: Leinwand und Chartpanel")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    result = run_stage5a(
        input_path=args.input_path,
        output_path=args.output,
        ffmpeg_path=Path(ffmpeg_path),
        ffprobe_path=args.ffprobe,
    )
    if isinstance(result, Stage5aFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.exit_code != 0:
        print(f"ffmpeg fehlgeschlagen: {result.stderr.decode('utf-8', errors='replace')}")
        return 1
    print(f"geschrieben: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
