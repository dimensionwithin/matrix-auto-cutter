r"""Stufe 5d: Endcard als Standbild-Videoclip erzeugen und an einen Short anhaengen.

Baut eine dreisekuendige Endcard (Monogramm, Haarlinie, Text, ein einzelner
eigener Knopf) direkt per ffmpeg-Filtern (``drawbox``, ``drawtext``,
``blend``, ``overlay``-Verwandte) und haengt sie per ``xfade``/``acrossfade``
an einen bestehenden 1080x1920-Short an.

**Abweichung vom Auftrag, hier dokumentiert statt stillschweigend
umgesetzt:** Der Auftrag empfiehlt, Ueberlagerungen als HTML/CSS zu rendern
und dann mit ffmpeg zusammenzusetzen (SHORTS-KONTEXT Abschnitt 6). Im
Repository ist kein Weg vorhanden, HTML zu rendern (kein Chromium, kein
Playwright/Puppeteer, keine vergleichbare Abhaengigkeit) - keiner der
bestehenden Stufe-5-Module (:mod:`canvas`, :mod:`avatar_cut`,
:mod:`chart_crop`) tut das. Der Auftrag selbst sieht diesen Fall vor: ist
kein Weg vorhanden, wird KEIN neuer eingefuehrt, sondern die Endcard direkt
per ffmpeg gezeichnet - das ist hier geschehen.

**Zweite dokumentierte Abweichung:** Der verlangte Knopf-Eckenradius von 2 px
ist mit ``drawbox`` nicht erreichbar - der Filter zeichnet ausschliesslich
scharfe Rechtecke. Der Knopf wird deshalb als scharfkantiges Rechteck
gezeichnet.

**Dritte dokumentierte Abweichung:** ``xfade`` bietet ausschliesslich eine
feste Liste benannter Uebergaenge, keine frei waehlbare Bezier-Kurve. Die
naechstliegende Wahl ist ``transition=fade`` - eine lineare Ueberblendung.
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
from matrix_auto_cutter.shorts.canvas import (
    BACKGROUND_COLOR_HEX,
    CANVAS_FPS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    SAFE_BOTTOM,
    SAFE_TOP,
)
from matrix_auto_cutter.shorts.chart_crop import probe_audio_track_count, probe_dimensions
from matrix_auto_cutter.shorts.inventory import discover_ffprobe

# ---------------------------------------------------------------------------
# Farben und Leinwand - aus canvas.py uebernommen, nicht neu erfunden
# (Auftrag shorts-stufe-5d). Messing und Knochen sind neu, nur hier gebraucht.
# ---------------------------------------------------------------------------

INK_HEX = BACKGROUND_COLOR_HEX
"""``--ink`` - identisch mit ``canvas.BACKGROUND_COLOR_HEX``, hier nur benannt."""

BRASS_HEX = "a98246"
"""``--brass`` - einziger erlaubter Akzent (Designsystem Abschnitt 10)."""

BONE_HEX = "ece8e0"
"""``--bone`` - Textfarbe fuer die Zeile "Donnerstag 20:00 ..."."""

# ---------------------------------------------------------------------------
# Dauer und Uebergang
# ---------------------------------------------------------------------------

ENDCARD_DURATION_FRAMES = 180
"""3 Sekunden bei 60 fps - feste Konstante laut Auftrag."""

ENDCARD_DURATION_SECONDS = ENDCARD_DURATION_FRAMES / CANVAS_FPS
assert ENDCARD_DURATION_SECONDS == 3.0, "180 Frames bei 60 fps sind 3 Sekunden"

TRANSITION_MS = 600
"""Cross-Fade-Laenge - der einzige erlaubte Uebergang (Designsystem Abschnitt 4)."""

TRANSITION_FRAMES = round(TRANSITION_MS * CANVAS_FPS / 1000)
assert TRANSITION_FRAMES == 36, "600 ms bei 60 fps sind 36 Frames"

TRANSITION_SECONDS = TRANSITION_MS / 1000.0

XFADE_TRANSITION = "fade"
"""Naechstliegender xfade-Uebergang zu cubic-bezier(.2,.7,.2,1) - siehe Modul-Docstring."""

# ---------------------------------------------------------------------------
# Monogramm - blend=lighten gegen die Ink-Flaeche, siehe SHORTS-KONTEXT
# Abschnitt 7 (dort fuer den Avatar gemessen und begruendet, hier auf die
# Bildmarke uebertragen: Bildmarkenhintergrund #14110c liegt in jedem Kanal
# unter --ink #171614 und verschwindet, Ring #c2a25a liegt darueber).
# ---------------------------------------------------------------------------

MONOGRAM_SOURCE_PATH = Path(r"P:\DimensionWithin\DW Logo\dimensionwithin-bildmarke-b-400.png")
"""Kraeftigerer Strich (b-400) - traegt bei der Verkleinerung auf 240 px besser."""

MONOGRAM_SOURCE_WIDTH = 400
MONOGRAM_SOURCE_HEIGHT = 400

MONOGRAM_WIDTH = 240
MONOGRAM_HEIGHT = 240
"""Quelle ist quadratisch (400x400) - Verkleinerung auf 240 px haelt das Seitenverhaeltnis."""

MONOGRAM_X = (CANVAS_WIDTH - MONOGRAM_WIDTH) // 2
MONOGRAM_Y = SAFE_TOP + 60
"""Mittig waagerecht, im oberen Drittel des nutzbaren Feldes (siehe Assert unten)."""

_USABLE_FIELD_HEIGHT = CANVAS_HEIGHT - SAFE_BOTTOM - SAFE_TOP
_USABLE_FIELD_UPPER_THIRD_END = SAFE_TOP + _USABLE_FIELD_HEIGHT // 3

assert MONOGRAM_X == 420, "MONOGRAM_X haengt an CANVAS_WIDTH/MONOGRAM_WIDTH"
assert (
    MONOGRAM_Y + MONOGRAM_HEIGHT <= _USABLE_FIELD_UPPER_THIRD_END
), "Monogramm muss im oberen Drittel des nutzbaren Feldes liegen"

# ---------------------------------------------------------------------------
# Haarlinie - "the learned signature", nicht verhandelbar (Designsystem
# Abschnitt 7).
# ---------------------------------------------------------------------------

HAIRLINE_WIDTH = 600
HAIRLINE_HEIGHT = 1
HAIRLINE_X = (CANVAS_WIDTH - HAIRLINE_WIDTH) // 2
HAIRLINE_Y = MONOGRAM_Y + MONOGRAM_HEIGHT + 80

assert HAIRLINE_X == 240, "HAIRLINE_X haengt an CANVAS_WIDTH/HAIRLINE_WIDTH"
assert HAIRLINE_Y == 580, "HAIRLINE_Y haengt an MONOGRAM_Y/MONOGRAM_HEIGHT"

# ---------------------------------------------------------------------------
# Text - JetBrains Mono, --bone. Halbgeviertstrich (U+2013), kein Bindestrich.
# ---------------------------------------------------------------------------

ENDCARD_TEXT = "Donnerstag 20:00 \u2013 Inner Circle"
ENDCARD_TEXT_FONT_SIZE = 40
ENDCARD_TEXT_Y = HAIRLINE_Y + 70

assert ENDCARD_TEXT_Y == 650, "ENDCARD_TEXT_Y haengt an HAIRLINE_Y"

# ---------------------------------------------------------------------------
# Knopf - GENAU EINER, Rechteck mit 1 px Rand in --brass, Beschriftung in
# --brass. Eckenradius 2 px ist mit drawbox nicht erreichbar - siehe
# Modul-Docstring, zweite dokumentierte Abweichung.
# ---------------------------------------------------------------------------

BUTTON_WIDTH = 420
BUTTON_HEIGHT = 90
BUTTON_X = (CANVAS_WIDTH - BUTTON_WIDTH) // 2
BUTTON_Y = ENDCARD_TEXT_Y + 110
BUTTON_BORDER_THICKNESS = 1
BUTTON_CORNER_RADIUS = 2
"""Verlangt, aber mit drawbox nicht umsetzbar - siehe Modul-Docstring."""

BUTTON_LABEL = "Inner Circle beitreten"
"""Wortlaut ist im Auftrag nicht festgelegt - nur Rand, Farbe, "genau ein Knopf"."""

BUTTON_FONT_SIZE = 32

assert BUTTON_X == 330, "BUTTON_X haengt an CANVAS_WIDTH/BUTTON_WIDTH"
assert BUTTON_Y == 760, "BUTTON_Y haengt an ENDCARD_TEXT_Y"
assert (
    BUTTON_Y + BUTTON_HEIGHT <= CANVAS_HEIGHT - SAFE_BOTTOM
), "Knopf muss innerhalb der unteren Sicherheitszone enden"

ENDCARD_REPORT_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Schriftdatei finden - JetBrains Mono liegt am 17.8. im Nutzerprofil, nicht
# unter C:\Windows\Fonts (SHORTS-KONTEXT Abschnitt 9, Punkt 2 war der Stand
# vom 9.8.; der Auftrag sagt, sie sei seit heute installiert).
# ---------------------------------------------------------------------------

_FONT_CANDIDATE_DIRS = (
    Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
    Path(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
    if os.environ.get("LOCALAPPDATA")
    else None,
)


def discover_jetbrains_mono_font() -> Path | None:
    """Suche ``JetBrainsMono-Regular.ttf`` in den bekannten Windows-Schriftorten."""
    for directory in _FONT_CANDIDATE_DIRS:
        if directory is None:
            continue
        candidate = directory / "JetBrainsMono-Regular.ttf"
        if candidate.is_file():
            return candidate
    return None


def _ffmpeg_escape_path(path: Path) -> str:
    r"""Mache einen Windows-Pfad fuer ``-filter_complex``-Optionswerte sicher.

    Der Doppelpunkt trennt sonst Filteroptionen (``fontfile=C:\...``) - er
    wird escaped, Rueckstriche werden durch Schraegstriche ersetzt, das ist
    ffmpegs eigene empfohlene Windows-Konvention.
    """
    text = str(path).replace("\\", "/")
    return text.replace(":", "\\:")


# ---------------------------------------------------------------------------
# ffmpeg: Endcard zeichnen, dann per xfade/acrossfade anhaengen.
# ---------------------------------------------------------------------------


def build_endcard_filter_complex(
    *,
    font_path: Path,
    text_file_path: Path,
    button_text_file_path: Path,
) -> str:
    """Baue den Filterausdruck, der NUR die Endcard-Standbildspur erzeugt (``[endcard_v]``).

    Reine Zeichenkette, kein IO - Reihenfolge folgt dem Auftrag von oben nach
    unten: Monogramm (``blend=lighten``), Haarlinie, Text, Knopf.
    """
    font = _ffmpeg_escape_path(font_path)
    text_file = _ffmpeg_escape_path(text_file_path)
    button_text_file = _ffmpeg_escape_path(button_text_file_path)
    parts = [
        f"color=c=0x{INK_HEX}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:"
        f"d={ENDCARD_DURATION_SECONDS:g}:r={CANVAS_FPS},format=rgb24[endcard_bg]",
        f"[1:v]scale={MONOGRAM_WIDTH}:{MONOGRAM_HEIGHT},"
        f"pad={CANVAS_WIDTH}:{CANVAS_HEIGHT}:{MONOGRAM_X}:{MONOGRAM_Y}:0x{INK_HEX},"
        "format=rgb24[endcard_logo]",
        # format=rgb24 auf beiden Zweigen ist notwendig, nicht kosmetisch: ohne
        # das handelt blend intern in einem YUV-Format, "lighten" je Kanal
        # bedeutet dann je Y/U/V statt je R/G/B - das erzeugt sichtbare
        # Farbstiche (gemessen: (194,146,160) statt (194,162,90) am Ring).
        "[endcard_bg][endcard_logo]blend=all_mode=lighten:shortest=1[endcard_mono]",
        f"[endcard_mono]drawbox=x={HAIRLINE_X}:y={HAIRLINE_Y}:w={HAIRLINE_WIDTH}:"
        f"h={HAIRLINE_HEIGHT}:color=0x{BRASS_HEX}:t=fill[endcard_hair]",
        f"[endcard_hair]drawtext=fontfile='{font}':textfile='{text_file}':"
        f"fontcolor=0x{BONE_HEX}:fontsize={ENDCARD_TEXT_FONT_SIZE}:"
        f"x=(w-text_w)/2:y={ENDCARD_TEXT_Y}[endcard_text]",
        f"[endcard_text]drawbox=x={BUTTON_X}:y={BUTTON_Y}:w={BUTTON_WIDTH}:h={BUTTON_HEIGHT}:"
        f"color=0x{BRASS_HEX}:t={BUTTON_BORDER_THICKNESS}[endcard_box]",
        f"[endcard_box]drawtext=fontfile='{font}':textfile='{button_text_file}':"
        f"fontcolor=0x{BRASS_HEX}:fontsize={BUTTON_FONT_SIZE}:"
        f"x=(w-text_w)/2:y={BUTTON_Y}+({BUTTON_HEIGHT}-text_h)/2[endcard_pre_v]",
        f"[endcard_pre_v]settb=1/{CANVAS_FPS}[endcard_v]",
    ]
    return ";".join(parts)


def build_append_filter_complex(
    *,
    font_path: Path,
    text_file_path: Path,
    button_text_file_path: Path,
    input_frame_count: int,
) -> tuple[str, str, str]:
    """Baue den vollstaendigen Filterausdruck: Endcard zeichnen und anhaengen.

    ``offset`` fuer ``xfade`` ist die Laenge der Eingabe in Sekunden minus die
    Uebergangslaenge - reine Rechnung aus der (bereits geprueften) 60-fps-
    Framezahl, kein Runden auf krumme Werte noetig (0,6 s sind bei 60 fps
    exakt 36 Frames).
    """
    endcard_filter = build_endcard_filter_complex(
        font_path=font_path,
        text_file_path=text_file_path,
        button_text_file_path=button_text_file_path,
    )
    offset_seconds = input_frame_count / CANVAS_FPS - TRANSITION_SECONDS
    tail = (
        f"[0:v]settb=1/{CANVAS_FPS}[main_v];"
        f"anullsrc=r=48000:cl=stereo:d={ENDCARD_DURATION_SECONDS:g}[endcard_silence];"
        f"[main_v][endcard_v]xfade=transition={XFADE_TRANSITION}:"
        f"duration={TRANSITION_SECONDS:g}:offset={offset_seconds:.9f}[outv];"
        "[0:a][endcard_silence]acrossfade=d="
        f"{TRANSITION_SECONDS:g}[outa]"
    )
    filter_complex = f"{endcard_filter};{tail}"
    return filter_complex, "[outv]", "[outa]"


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    input_path: Path,
    monogram_path: Path,
    output_path: Path,
    *,
    font_path: Path,
    text_file_path: Path,
    button_text_file_path: Path,
    input_frame_count: int,
) -> list[str]:
    """Vollstaendiges ffmpeg-Kommando: Endcard zeichnen, per Cross-Fade anhaengen."""
    filter_complex, video_label, audio_label = build_append_filter_complex(
        font_path=font_path,
        text_file_path=text_file_path,
        button_text_file_path=button_text_file_path,
        input_frame_count=input_frame_count,
    )
    return [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(input_path),
        "-loop",
        "1",
        "-i",
        str(monogram_path),
        "-filter_complex",
        filter_complex,
        "-map",
        video_label,
        "-map",
        audio_label,
        "-r",
        f"{CANVAS_FPS:g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        # yuv420p ausdruecklich erzwingen: das vorangehende ``format=rgb24``
        # vor ``blend`` (siehe build_endcard_filter_complex) laesst libx264
        # sonst 4:4:4 statt des im Rest dieses Codebases ueblichen und fuer
        # YouTube-Uploads kompatiblen 4:2:0-Profils waehlen.
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded ffmpeg-Prozessausgang - eigenstaendig, analog zu ``canvas``."""

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
class Stage5dFailed:
    """Fail-closed Auskunft, warum keine Endcard gebaut werden konnte."""

    code: str
    message_de: str


def _write_utf8_text_file(directory: Path, name: str, text: str) -> Path:
    """Schreibe eine kurze UTF-8-Textdatei fuer ``drawtext``s ``textfile``-Option."""
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def run_endcard(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    monogram_path: Path = MONOGRAM_SOURCE_PATH,
    font_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
    ffprobe_path: Path | None = None,
) -> ProcessResult | Stage5dFailed:
    """Pruefe die Voraussetzungen, baue die Textdateien, fuehre ffmpeg tatsaechlich aus."""
    resolved_font = font_path or discover_jetbrains_mono_font()
    if resolved_font is None:
        return Stage5dFailed(
            "font_not_found",
            "JetBrainsMono-Regular.ttf wurde in keinem der bekannten Schriftorte gefunden",
        )

    monogram_dimensions = probe_dimensions(
        monogram_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if monogram_dimensions != (MONOGRAM_SOURCE_WIDTH, MONOGRAM_SOURCE_HEIGHT):
        return Stage5dFailed(
            "monogram_resolution_mismatch",
            f"Bildmarke {monogram_dimensions} weicht von "
            f"{MONOGRAM_SOURCE_WIDTH}x{MONOGRAM_SOURCE_HEIGHT} ab: {monogram_path}",
        )

    input_frame_count = probe_frame_count(
        input_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if input_frame_count is None:
        return Stage5dFailed(
            "input_frame_count_unknown",
            f"ffprobe konnte die Framezahl nicht ermitteln: {input_path}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shorts-endcard-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        text_file_path = _write_utf8_text_file(tmp_dir, "text.txt", ENDCARD_TEXT)
        button_text_file_path = _write_utf8_text_file(tmp_dir, "button.txt", BUTTON_LABEL)
        arguments = build_ffmpeg_arguments(
            ffmpeg_path,
            input_path,
            monogram_path,
            output_path,
            font_path=resolved_font,
            text_file_path=text_file_path,
            button_text_file_path=button_text_file_path,
            input_frame_count=input_frame_count,
        )
        return process_runner(arguments, timeout_seconds)


# ---------------------------------------------------------------------------
# Teil C: Ausgabe pruefen, vier unabhaengige Pruefungen mit je eigenem
# Fehlercode - nach dem Muster von canvas.py/chart_crop.py.
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


def expected_output_frame_count(input_frame_count: int) -> int:
    """Sollzahl laut Auftrag: Eingabeframes + 180 minus die Ueberblendlaenge, ohne Toleranz."""
    return input_frame_count + ENDCARD_DURATION_FRAMES - TRANSITION_FRAMES


@dataclass(frozen=True, slots=True)
class VerifyChecks:
    """Istwerte der vier Pruefungen aus Teil C, unabhaengig vom Ergebnis erhoben."""

    input_frame_count: int | None
    output_frame_count: int | None
    expected_frame_count: int | None
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


def verify_endcard_output(
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
    expected = (
        expected_output_frame_count(input_frame_count) if input_frame_count is not None else None
    )
    frame_count_ok = (
        expected is not None and output_frame_count is not None and output_frame_count == expected
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
        expected_frame_count=expected,
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


def endcard_report_payload(checks: VerifyChecks) -> dict[str, object]:
    """Baue den Inhalt des Laufberichts, nach dem Muster von ``chart-crop.json``."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_endcard",
        "schema_version": ENDCARD_REPORT_SCHEMA_VERSION,
        "checks": {
            "frame_count": {
                "input": checks.input_frame_count,
                "expected": checks.expected_frame_count,
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


def write_endcard_report(path: Path, payload: dict[str, object]) -> None:
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


def run_stage5d(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_path: Path,
    monogram_path: Path = MONOGRAM_SOURCE_PATH,
    font_path: Path | None = None,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage5dFailed:
    """Ende-zu-Ende: Voraussetzungen pruefen, Endcard bauen und anhaengen, Ergebnis pruefen.

    Schreibt nach einem erfolgreichen ffmpeg-Lauf einen Laufbericht neben die
    Ausgabe (``<output>.json``) mit den vier Pruefergebnissen aus Teil C. Ist
    eine der vier Pruefungen gefallen, ist das Ergebnis ``Stage5dFailed`` mit
    dem passenden, eigenstaendigen Fehlercode - der Bericht wird trotzdem
    geschrieben, damit der Befund nachvollziehbar bleibt.
    """
    input_dimensions = probe_dimensions(
        input_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if input_dimensions is None:
        return Stage5dFailed(
            "input_resolution_unknown",
            f"ffprobe konnte die Aufloesung nicht ermitteln: {input_path}",
        )
    if input_dimensions != (CANVAS_WIDTH, CANVAS_HEIGHT):
        return Stage5dFailed(
            "input_resolution_mismatch",
            f"Eingabegroesse {input_dimensions[0]}x{input_dimensions[1]} weicht von "
            f"{CANVAS_WIDTH}x{CANVAS_HEIGHT} ab - die Geometrie dieses Moduls setzt sie voraus",
        )

    process_result = run_endcard(
        input_path=input_path,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        monogram_path=monogram_path,
        font_path=font_path,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
        ffprobe_path=ffprobe_path,
    )
    if isinstance(process_result, Stage5dFailed):
        return process_result
    if process_result.exit_code != 0:
        return process_result

    checks = verify_endcard_output(
        input_path, output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    write_endcard_report(report_path, endcard_report_payload(checks))
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return Stage5dFailed(
            failure_code,
            f"Pruefung '{failure_code}' fehlgeschlagen, Bericht: {report_path}",
        )
    return process_result


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: eine Endcard bauen und an einen fertigen Short anhaengen."""
    import argparse

    parser = argparse.ArgumentParser(description="Stufe 5d: Endcard anhaengen")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--monogram", type=Path, default=MONOGRAM_SOURCE_PATH)
    parser.add_argument("--font", type=Path, default=None)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    result = run_stage5d(
        input_path=args.input_path,
        output_path=args.output,
        ffmpeg_path=Path(ffmpeg_path),
        monogram_path=args.monogram,
        font_path=args.font,
        ffprobe_path=args.ffprobe,
    )
    if isinstance(result, Stage5dFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.exit_code != 0:
        print(f"ffmpeg fehlgeschlagen: {result.stderr.decode('utf-8', errors='replace')}")
        return 1
    print(f"geschrieben: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
