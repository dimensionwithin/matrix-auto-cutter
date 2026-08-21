r"""Stufe 5c: Untertitelzeilen aus ``subtitle_lines.py`` in ein fertiges Short einbrennen.

Nimmt ein fertiges 1080x1920-Short (Ausgabe von Stufe 5b: Leinwand + Avatar)
und eine Wortliste (:mod:`matrix_auto_cutter.shorts.subtitle_lines`) entgegen
und brennt die Untertitelzeilen per ``drawtext`` ein - keine Endcard, kein
Ausblenden, keine Mausverfolgung, keine Hintergrundflaeche hinter dem Text
(alles ausdruecklich nicht Teil dieses Auftrags).

**Bewusste Abweichung vom Designsystem, hier vermerkt statt stillschweigend
umgesetzt (Auftrag shorts-stufe-5c):** Das Designsystem verlangt Versalien
fuer Datentags. Ein 40-Sekunden-Short komplett in Versalien ist unlesbar -
der Untertitel bleibt deshalb in der Schreibweise, die whisper liefert
(Gemischtschreibung), Grossbuchstaben werden an keiner Stelle erzwungen.

Farben (Designsystem Abschnitt 10, nicht verhandelbar): die ganze Zeile in
``--bone-dim`` (``#bfb9ac``), das jeweils aktive Wort zusaetzlich in
``--brass`` (``#a98246``) an DERSELBEN Stelle darueber gezeichnet - nur die
Farbe wechselt, kein Springen, kein Skalieren, keine Bewegung, kein Schatten,
keine Kontur. Umgesetzt als ein ``drawtext`` je Zeile (``--bone-dim``, aktiv
fuer die volle Zeilendauer) plus ein weiterer ``drawtext`` je Wort
(``--brass``, aktiv nur fuer die Wortdauer) an derselben x/y-Position wie das
Wort innerhalb der Zeile - JetBrains Mono ist dicktengleich, die x-Position
jedes Wortes ergibt sich rein rechnerisch aus der Zeichenzahl davor mal der
gemessenen Zeichenbreite (:data:`CHAR_ADVANCE_WIDTH_PX`, siehe dort zur
Messmethode). Bei den ueblichen ~60 Zeilen/~150 Woertern eines Kandidaten
entstehen so 200+ ``drawtext``-Aufrufe; der Filtergraph geht deshalb immer
per ``-filter_complex_script`` (Datei) an ffmpeg, nie ueber die Kommandozeile
selbst - ausdruecklich erlaubte Loesung laut Auftrag.

Lage: der Untertitel steht im Band unter dem Chartpanel (``canvas.PANEL_Y +
canvas.PANEL_HEIGHT`` = 1100), oben im Band, ueber dem Kopf des Avatars -
nicht daneben, dafuer ist links vom Avatar kein Platz. Waagerecht mittig im
Feld von x=0 bis ``canvas.CANVAS_WIDTH - canvas.SAFE_RIGHT`` (930), nichts
unterhalb y=1440 (YouTubes Bedienleiste, feste Plattformvorgabe, nicht aus
canvas.py herleitbar).

AUSDRUECKLICH NICHT Teil dieses Moduls: Endcard (``endcard.py`` - bleibt
liegen, wird hier nicht aufgerufen), Ausblenden am Ende, Mausverfolgung,
Hintergrundflaeche/Kasten hinter dem Text.
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
    CANVAS_FPS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PANEL_HEIGHT,
    PANEL_Y,
    SAFE_RIGHT,
)
from matrix_auto_cutter.shorts.chart_crop import probe_audio_track_count, probe_dimensions
from matrix_auto_cutter.shorts.inventory import discover_ffprobe
from matrix_auto_cutter.shorts.subtitle_lines import MAX_CHARS_PER_LINE, SubtitleLine

# ---------------------------------------------------------------------------
# Farben - Designsystem Abschnitt 10, nicht verhandelbar (Auftrag shorts-stufe-5c).
# ---------------------------------------------------------------------------

BONE_DIM_HEX = "bfb9ac"
"""``--bone-dim`` - Farbe der ganzen Zeile."""

BRASS_HEX = "a98246"
"""``--brass`` - Farbe des jeweils aktiven Wortes, an derselben Stelle darueber."""

# ---------------------------------------------------------------------------
# Schrift und Zeichenbreite - JetBrains Mono, EINMAL gemessen (siehe unten).
# ---------------------------------------------------------------------------

SUBTITLE_FONT_SIZE = 50
"""Gewaehlt, damit die laengstmoegliche Zeile (24 Zeichen,
``subtitle_lines.MAX_CHARS_PER_LINE``) bequem im Feld 0..930 Platz hat, siehe
Assert unten."""

CHAR_ADVANCE_WIDTH_PX = 30.0
"""Dicktenbreite von JetBrainsMono-Regular.ttf bei ``SUBTITLE_FONT_SIZE=50``,
EINMAL gemessen (nicht bei jedem Lauf neu): ein Einzelbild mit ffmpeg
gerendert (``drawtext`` auf ``--ink``-Hintergrund, Ziffernfolgen
unterschiedlicher Laenge), roh als RGB24 dekodiert, hellste Spalten je Laenge
verglichen. Differenzmethode haelt Rand/Bearing exakt heraus, unabhaengig vom
Einzelglyphen: 10x '0' -> Tintenbreite 292 px, 20x '0' -> 592 px, Dickte =
(592-292)/10 = 30,0 px. Vier Stichproben bei 44/48/52/56 px Schriftgroesse
ergaben durchgehend das Verhaeltnis 0,6 * Schriftgroesse - JetBrains Mono ist
dicktengleich, das Verhaeltnis ist bei jeder Groesse gleich."""

assert CHAR_ADVANCE_WIDTH_PX == 0.6 * SUBTITLE_FONT_SIZE, "gemessenes Verhaeltnis 0,6"

# ---------------------------------------------------------------------------
# Geometrie - benannte Konstanten an einer Stelle, hergeleitet aus canvas.py
# (Auftrag shorts-stufe-5c). y=1440 (YouTube-Bedienleiste) ist eine feste
# Plattformvorgabe aus dem Auftrag, nicht aus canvas.py herleitbar.
# ---------------------------------------------------------------------------

SUBTITLE_FIELD_X = 0
SUBTITLE_FIELD_WIDTH = CANVAS_WIDTH - SAFE_RIGHT
"""Nichts rechts von x=930 - dasselbe Feld wie der Avatar (``avatar_canvas.AVATAR_SCALE_WIDTH``)."""

assert SUBTITLE_FIELD_WIDTH == 930, "SUBTITLE_FIELD_WIDTH haengt an CANVAS_WIDTH/SAFE_RIGHT"

SUBTITLE_MAX_LINE_WIDTH_PX = CHAR_ADVANCE_WIDTH_PX * MAX_CHARS_PER_LINE
assert SUBTITLE_MAX_LINE_WIDTH_PX <= SUBTITLE_FIELD_WIDTH, (
    "die laengstmoegliche Zeile muss ins Feld 0..930 passen"
)

SUBTITLE_TOP_Y = PANEL_Y + PANEL_HEIGHT
"""Direkt unter der Panelkante - dieselbe Kante, an der auch der Avatar beginnt."""

assert SUBTITLE_TOP_Y == 1100, "SUBTITLE_TOP_Y haengt an canvas.PANEL_Y/PANEL_HEIGHT"

SUBTITLE_TOP_PADDING_PX = 24
"""Sichtbarer Abstand zur Panelkante, damit der Text nicht auf der Kante klebt."""

SUBTITLE_TEXT_Y = SUBTITLE_TOP_Y + SUBTITLE_TOP_PADDING_PX

SUBTITLE_TEXT_HEIGHT_PX = 56
"""Gemessen: Tintenhoehe von JetBrainsMono-Regular.ttf bei fontsize=50 ueber
Umlaute, Ober- und Unterlaengen zusammen (``"ÄÖÜbgjpqy072"``) - 56 px."""

SUBTITLE_BOTTOM_LIMIT_Y = 1440
"""YouTubes Bedienleiste beginnt hier - feste Plattformvorgabe aus dem Auftrag."""

assert SUBTITLE_TEXT_Y + SUBTITLE_TEXT_HEIGHT_PX <= SUBTITLE_BOTTOM_LIMIT_Y, (
    "der Untertitel muss oberhalb y=1440 enden"
)

SUBTITLE_REPORT_SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# Geometrie je Zeile/Wort - reine Rechnung, kein IO.
# ---------------------------------------------------------------------------


def line_x_position(line: SubtitleLine) -> float:
    """Waagerecht mittig im Feld 0..930 - Breite ergibt sich aus der Zeichenzahl."""
    width_px = len(line.text) * CHAR_ADVANCE_WIDTH_PX
    return SUBTITLE_FIELD_X + (SUBTITLE_FIELD_WIDTH - width_px) / 2


def word_char_offset(line: SubtitleLine, word_index: int) -> int:
    """Zeichenposition, an der ``line.words[word_index]`` innerhalb ``line.text`` beginnt.

    ``line.text`` fuegt die Woerter mit je einem Leerzeichen zusammen
    (:meth:`SubtitleLine.text`) - der Versatz ist deshalb die Summe aus
    Textlaenge plus einem Leerzeichen je vorangehendem Wort.
    """
    return sum(len(word.text) + 1 for word in line.words[:word_index])


def word_x_position(line: SubtitleLine, word_index: int) -> float:
    """x-Position des Wortes - dieselbe Stelle, an der es innerhalb der Zeile steht."""
    return line_x_position(line) + CHAR_ADVANCE_WIDTH_PX * word_char_offset(line, word_index)


# ---------------------------------------------------------------------------
# ffmpeg: ein drawtext je Wort in --bone-dim (fuer die volle Zeilendauer)
# plus ein weiterer drawtext je Wort in --brass (nur waehrend seiner eigenen
# Dauer), verkettet ueber den Video-Stream, an eine Skriptdatei geschrieben
# statt auf die Kommandozeile (Auftrag: erlaubte Loesung bei vielen Zeilen).
#
# **Teil-C-Befund (Auftrag shorts-5b-5c-nachbesserung):** urspruenglich gab es
# EINEN drawtext fuer die ganze Zeile (--bone-dim) plus je einen drawtext pro
# Wort (--brass). ``drawtext``s ``y`` ist die Oberkante der TATSAECHLICHEN
# Tintenausdehnung DIESES Aufrufs - eine Zeile mit Oberlaengen (z. B. "Und was
# kam" mit U/d/k) hat eine andere Kastenhoehe als ein einzelnes Wort ohne
# Oberlaengen (z. B. "was" allein), obwohl beide denselben ``y``-Wert
# bekommen - die Grundlinie wandert dadurch, das hervorgehobene Wort sitzt
# sichtbar hoeher oder tiefer als der Rest der Zeile. Belegt per Einzelbild-
# Rohvergleich, siehe Bericht. Ein unsichtbarer Anker ausserhalb der Leinwand
# wurde geprueft und verworfen: ffmpeg berechnet die Kastenhoehe nur aus den
# tatsaechlich innerhalb des Bildes gezeichneten Pixeln, ein Anker jenseits
# der Bildkante zaehlt nicht mit.
#
# Behoben nach dem einfacheren der beiden im Auftrag vorgeschlagenen Wege:
# je Wort EIN drawtext fuer beide Farben (--bone-dim UND --brass), beide mit
# exakt demselben Text an derselben Position - dieselbe Zeichenkette erzeugt
# zwangslaeufig dieselbe Kastenhoehe, die Grundlinie von --brass liegt damit
# immer exakt auf der Grundlinie des darunterliegenden --bone-dim-Wortes.
# Einfacher als der Alternativvorschlag (jede Zeichenkette um eine konstante
# Tintenausdehnung ergaenzen), weil er ohne zusaetzliche Platz-/Maskierungs-
# rechnung auskommt und dieselbe Wortliste/-position wiederverwendet, die für
# die Brass-Schicht ohnehin schon existiert.
# ---------------------------------------------------------------------------


def _ffmpeg_escape_path(path: Path) -> str:
    r"""Mache einen Windows-Pfad fuer ``-filter_complex``-Optionswerte sicher.

    Der Doppelpunkt trennt sonst Filteroptionen (``fontfile=C:\...``) - er
    wird escaped, Rueckstriche werden durch Schraegstriche ersetzt, das ist
    ffmpegs eigene empfohlene Windows-Konvention (dieselbe Funktion wie in
    ``endcard.py`` - hier eigenstaendig, ``endcard.py`` wird nicht verwendet).
    """
    text = str(path).replace("\\", "/")
    return text.replace(":", "\\:")


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def build_subtitle_filter_complex(
    lines: Sequence[SubtitleLine],
    *,
    font_path: Path,
    word_text_paths: Sequence[Sequence[Path]],
) -> tuple[str, str]:
    """Baue den ``-filter_complex``-Ausdruck: je Wort --bone-dim und --brass, gleiche Grundlinie.

    Reine Zeichenkette, kein IO - ``word_text_paths`` sind bereits
    geschriebene Textdateien (ein ``drawtext`` je Wort und Farbe ueber
    ``textfile=`` liest sie, kein Escapen des Wortinhalts noetig).
    ``word_text_paths[i][j]`` gehoert zu ``lines[i].words[j]`` und wird fuer
    BEIDE Farben desselben Wortes wiederverwendet (Teil-C-Fix, siehe oben) -
    dieselbe Datei, zweimal gelesen, garantiert dieselbe Kastenhoehe.
    """
    if len(word_text_paths) != len(lines):
        raise ValueError("word_text_paths muss zu jeder Zeile eine Wortliste haben")

    font = _ffmpeg_escape_path(font_path)
    parts: list[str] = []
    label = "[0:v]"
    index = 0

    for line, word_paths in zip(lines, word_text_paths, strict=True):
        if len(word_paths) != len(line.words):
            raise ValueError(
                "word_text_paths[i] muss zu jedem Wort der Zeile genau einen Pfad haben"
            )

        word_pairs = enumerate(zip(line.words, word_paths, strict=True))
        for word_index, (word, word_text_path) in word_pairs:
            word_text_file = _ffmpeg_escape_path(word_text_path)
            x_position = word_x_position(line, word_index)

            out_label = f"[v{index}]"
            parts.append(
                f"{label}drawtext=fontfile='{font}':textfile='{word_text_file}':"
                f"fontcolor=0x{BONE_DIM_HEX}:fontsize={SUBTITLE_FONT_SIZE}:"
                f"x={x_position:.3f}:y={SUBTITLE_TEXT_Y}:"
                f"enable='between(t,{_seconds(line.start_ms)},{_seconds(line.end_ms)})'{out_label}"
            )
            label = out_label
            index += 1

            out_label = f"[v{index}]"
            parts.append(
                f"{label}drawtext=fontfile='{font}':textfile='{word_text_file}':"
                f"fontcolor=0x{BRASS_HEX}:fontsize={SUBTITLE_FONT_SIZE}:"
                f"x={x_position:.3f}:y={SUBTITLE_TEXT_Y}:"
                f"enable='between(t,{_seconds(word.start_ms)},{_seconds(word.end_ms)})'{out_label}"
            )
            label = out_label
            index += 1

    return ";".join(parts), label


def build_ffmpeg_arguments(
    ffmpeg_path: Path,
    input_path: Path,
    output_path: Path,
    filter_script_path: Path,
    video_label: str,
    *,
    fps: int = CANVAS_FPS,
) -> list[str]:
    """Vollstaendiges ffmpeg-Kommando: Untertitel einbrennen, Ton unveraendert uebernehmen."""
    return [
        str(ffmpeg_path),
        "-y",
        "-i",
        str(input_path),
        "-filter_complex_script",
        str(filter_script_path),
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
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
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


def _write_utf8_text_file(directory: Path, name: str, text: str) -> Path:
    """Schreibe eine kurze UTF-8-Textdatei fuer ``drawtext``s ``textfile``-Option."""
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Schriftdatei finden - eigenstaendig, ``endcard.py`` wird nicht verwendet.
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


@dataclass(frozen=True, slots=True)
class Stage5cFailed:
    """Fail-closed Auskunft, warum kein Untertitel eingebrannt werden konnte."""

    code: str
    message_de: str


def run_subtitle_burn(
    *,
    input_path: Path,
    lines: Sequence[SubtitleLine],
    output_path: Path,
    ffmpeg_path: Path,
    font_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage5cFailed:
    """Schreibe die Textdateien, baue den Filtergraph, fuehre ffmpeg tatsaechlich aus."""
    resolved_font = font_path or discover_jetbrains_mono_font()
    if resolved_font is None:
        return Stage5cFailed(
            "font_not_found",
            "JetBrainsMono-Regular.ttf wurde in keinem der bekannten Schriftorte gefunden",
        )
    if not lines:
        return Stage5cFailed("no_subtitle_lines", "die Wortliste ergab keine Untertitelzeile")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shorts-subtitle-burn-") as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        # Eine Textdatei je Wort, fuer BEIDE Farben wiederverwendet (Teil-C-Fix).
        word_text_paths = [
            [
                _write_utf8_text_file(tmp_dir, f"line_{i:03d}_word_{j:03d}.txt", word.text)
                for j, word in enumerate(line.words)
            ]
            for i, line in enumerate(lines)
        ]
        filter_complex, video_label = build_subtitle_filter_complex(
            lines,
            font_path=resolved_font,
            word_text_paths=word_text_paths,
        )
        filter_script_path = tmp_dir / "filter_complex.txt"
        filter_script_path.write_text(filter_complex, encoding="utf-8")

        arguments = build_ffmpeg_arguments(
            ffmpeg_path, input_path, output_path, filter_script_path, video_label
        )
        return process_runner(arguments, timeout_seconds)


# ---------------------------------------------------------------------------
# Teil C: Ausgabe pruefen, vier unabhaengige Pruefungen mit je eigenem
# Fehlercode - nach dem Muster von canvas.py/avatar_canvas.py/endcard.py.
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


def verify_subtitle_burn_output(
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


def subtitle_burn_report_payload(checks: VerifyChecks) -> dict[str, object]:
    """Baue den Inhalt des Laufberichts, nach dem Muster von ``canvas-report.json``."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_subtitle_burn",
        "schema_version": SUBTITLE_REPORT_SCHEMA_VERSION,
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


def write_subtitle_burn_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe den Laufbericht atomar - dasselbe Muster wie ``canvas``/``endcard``."""
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


def run_stage5c(
    *,
    input_path: Path,
    lines: Sequence[SubtitleLine],
    output_path: Path,
    ffmpeg_path: Path,
    font_path: Path | None = None,
    ffprobe_path: Path | None = None,
    process_runner: ProcessRunner = _default_process_runner,
    timeout_seconds: int = 1800,
) -> ProcessResult | Stage5cFailed:
    """Ende-zu-Ende: Eingabe pruefen, Untertitel einbrennen, Ergebnis pruefen.

    Schreibt nach einem erfolgreichen ffmpeg-Lauf einen Laufbericht neben die
    Ausgabe (``<output>.json``) mit den vier Pruefergebnissen aus Teil C. Ist
    eine der vier Pruefungen gefallen, ist das Ergebnis ``Stage5cFailed`` mit
    dem passenden, eigenstaendigen Fehlercode - der Bericht wird trotzdem
    geschrieben, damit der Befund nachvollziehbar bleibt.
    """
    input_dimensions = probe_dimensions(
        input_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if input_dimensions is None:
        return Stage5cFailed(
            "input_resolution_unknown",
            f"ffprobe konnte die Aufloesung nicht ermitteln: {input_path}",
        )
    if input_dimensions != (CANVAS_WIDTH, CANVAS_HEIGHT):
        return Stage5cFailed(
            "input_resolution_mismatch",
            f"Eingabegroesse {input_dimensions[0]}x{input_dimensions[1]} weicht von "
            f"{CANVAS_WIDTH}x{CANVAS_HEIGHT} ab - die Geometrie dieses Moduls setzt sie voraus",
        )

    process_result = run_subtitle_burn(
        input_path=input_path,
        lines=lines,
        output_path=output_path,
        ffmpeg_path=ffmpeg_path,
        font_path=font_path,
        process_runner=process_runner,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(process_result, Stage5cFailed):
        return process_result
    if process_result.exit_code != 0:
        return process_result

    checks = verify_subtitle_burn_output(
        input_path, output_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    write_subtitle_burn_report(report_path, subtitle_burn_report_payload(checks))
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return Stage5cFailed(
            failure_code,
            f"Pruefung '{failure_code}' fehlgeschlagen, Bericht: {report_path}",
        )
    return process_result


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: Untertitel aus einer whisper-Rohausgabe in ein fertiges Short einbrennen."""
    import argparse

    from matrix_auto_cutter.shorts.subtitle_lines import (
        build_subtitle_lines,
        words_from_whisper_json,
    )

    parser = argparse.ArgumentParser(description="Stufe 5c: Untertitel einbrennen")
    parser.add_argument("input_path", type=Path)
    parser.add_argument("whisper_json_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    parser.add_argument("--font", type=Path, default=None)
    args = parser.parse_args(argv)

    ffmpeg_path = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_path is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    raw_json = args.whisper_json_path.read_text(encoding="utf-8")
    words = words_from_whisper_json(raw_json)
    lines = build_subtitle_lines(words)

    result = run_stage5c(
        input_path=args.input_path,
        lines=lines,
        output_path=args.output,
        ffmpeg_path=Path(ffmpeg_path),
        font_path=args.font,
        ffprobe_path=args.ffprobe,
    )
    if isinstance(result, Stage5cFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1
    if result.exit_code != 0:
        print(f"ffmpeg fehlgeschlagen: {result.stderr.decode('utf-8', errors='replace')}")
        return 1
    print(f"geschrieben: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
