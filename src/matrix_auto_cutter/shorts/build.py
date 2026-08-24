r"""Auftrag shorts-bau: die ganze Kette zu einem Aufruf verdrahten.

Baut aus ``shorts-job.json`` und ``kandidaten.json`` fertige Short-Dateien -
ein Aufruf je Video, nicht je Kandidat. Ablauf je Kandidat:
``chart_crop -> canvas -> avatar_canvas -> subtitle_burn``. Endcard wird
NICHT angehaengt (beendet ein Short sichtbar und zerstoert die
Wiederholschleife, auf die diese Linie zielt). Die sieben bestehenden Module
(chart_crop, canvas, avatar_canvas, subtitle_burn, subtitle_lines,
scene_windows, loop_point) werden IMPORTIERT und aufgerufen, nicht umgebaut.

Fuenf Werte, die zuvor von Hand eingetippt wurden, werden hier abgeleitet
(Punkt 1): ``canvas_recording_id``/``avatar_recording_id`` aus
``shorts-job.json`` (``video_name``, beide gleich - dieselbe Aufnahme),
``candidate_start_ms``/``candidate_end_ms`` aus ``kandidaten.json`` je
Kandidat (nach dem Schleifenpunkt-Rasten korrigiert, siehe unten), und
``expected_avatar_frame_count`` einmal je Video per
``ffprobe -count_frames`` auf ``rendered_video.path`` gemessen - nicht je
Kandidat.

Zwei Filter schliessen einzelne Kandidaten aus, bevor irgendein ffmpeg fuer
sie laeuft:

* Szenenfilter (Punkt 3a): ``scene_windows`` liefert die Charts-Fenster aus
  dem Producer-Journal, ``frame_map.map_source_interval_to_rendered`` bildet
  sie auf die gerenderte Achse ab. Ein Kandidat ausserhalb aller Fenster wird
  nicht gebaut. Fehlt das Journal, wird das gemeldet und OHNE Szenenfilter
  weitergebaut - ein fehlendes Journal darf nicht stillschweigend das ganze
  Video ausschliessen.
* Schleifenpunkt (Punkt 3b): ``loop_point.rasten_auf_wortgrenzen`` korrigiert
  die Kandidatengrenzen auf Wortgrenzen der WORTLISTE DES GANZEN VIDEOS
  (nicht des Ausschnitts - nur so sind die Pausen vor/nach dem Kandidaten
  messbar), ``beurteile_grenzen`` stuft ein. "ungeeignet" wird nicht gebaut,
  "grenzwertig" wird gebaut und vermerkt. Die korrigierten (und samt
  ``loop_point.LOOP_PAD_MS`` gepolsterten) Grenzen sind die tatsaechlich
  gebaute Spanne, nicht die rohen Werte aus ``kandidaten.json``.

Bricht ein Kandidat ab, laufen die uebrigen weiter (kein Kandidat haelt die
anderen auf). Das Ergebnis ist immer eine vollstaendige Uebersicht: welcher
Kandidat wurde gebaut, welcher warum nicht.

Kandidaten haengen NICHT voneinander ab - die vier Stufen eines Kandidaten
schon (jede frisst die Ausgabe der vorigen). Nebenlaeufig gebaut werden
deshalb Kandidaten, nie Stufen (Auftrag shorts-bau-parallel, ``--parallel N``,
Voreinstellung :data:`PARALLEL_DEFAULT`). Genutzt werden Threads, nicht
Prozesse: die Arbeit steckt in den ffmpeg-Unterprozessen, waehrend derer
Python den GIL freigibt. ``--parallel 1`` nimmt weiterhin die schlichte
Schleife - denselben Weg wie vor diesem Auftrag.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from matrix_auto_cutter.approval import SelectiveProposalApproval, inspect_approval_state
from matrix_auto_cutter.cut_proposal import discover_ffmpeg
from matrix_auto_cutter.product_runner import default_journal_directory
from matrix_auto_cutter.shorts import avatar_canvas, canvas, chart_crop, subtitle_burn
from matrix_auto_cutter.shorts.avatar_cut import probe_frame_count
from matrix_auto_cutter.shorts.candidates import Candidate, CandidatesSchemaError, load_candidates
from matrix_auto_cutter.shorts.cursor_track import (
    CursorProtokollError,
    CursorZeile,
    Versatzkurve,
    lies_cursorprotokoll,
    versatzkurve,
)
from matrix_auto_cutter.shorts.frame_map import (
    KeepSegment,
    candidate_frame_span,
    candidate_outside_windows,
    effective_cuts,
    keep_segments_from_intervals,
    map_source_interval_to_rendered,
)
from matrix_auto_cutter.shorts.inventory import discover_ffprobe
from matrix_auto_cutter.shorts.level_cut import (
    MIN_NACHKLANG_MS,
    VERFAHREN_BEREICHSMITTE,
    VERFAHREN_TONBLENDE_GROSSZUEGIG,
    VERFAHREN_WORTRAND_KOLLISION,
    LevelCutFailed,
    LevelSnap,
    StilleVorlauf,
    finde_nachbarrand_ausklang,
    finde_nachbarrand_einsatz,
    finde_stillevorlauf,
    finde_worteinsatz_ton,
    finde_wortende_ton,
    finde_wortrand_anfang,
    finde_wortrand_ende,
    miss_pegel_bei_marke,
)
from matrix_auto_cutter.shorts.loop_point import (
    GEEIGNET,
    GRENZWERTIG,
    LOOP_PAD_MS,
    MIN_SPAN_MS,
    LoopBoundaries,
    LoopPointError,
    beurteile_grenzen,
    rasten_auf_wortgrenzen,
)
from matrix_auto_cutter.shorts.scene_windows import SceneWindowsFailed, load_scene_windows
from matrix_auto_cutter.shorts.subtitle_lines import (
    Word,
    build_subtitle_lines,
    words_from_whisper_json,
)
from matrix_auto_cutter.shorts.transcript import RENDERED_WAV_NAME, transcript_paths

BUILD_FPS = chart_crop.SOURCE_FPS
"""Die eine Bildrate, mit der die ganze Kette rechnet - siehe chart_crop/canvas."""

assert BUILD_FPS == canvas.CANVAS_FPS, "chart_crop und canvas muessen dieselbe fps annehmen"

BUILD_REPORT_SCHEMA_VERSION = "2.0"
"""1.1 (Auftrag shorts-pegelschnitt): je Kandidat ``pegelkorrektur`` ergaenzt.
1.2 (Auftrag shorts-pegelmedian): je Grenze ``verfahren`` und
``leiser_bereich_ms`` ergaenzt - welches Verfahren griff, wie lang der
gewaehlte leise Bereich war.
1.3 (Auftrag shorts-achsenpruefung-warnung): je Kandidat
``achsenabweichung_frames``/``achsenabweichung_hinweis`` ergaenzt, dazu
``summary.achsenabweichung_count`` - die Achsenpruefung aus avatar_canvas ist
von einem Abbruch zu einer Warnung geworden (Toleranz
``avatar_canvas.ACHSENABWEICHUNG_MAX_FRAMES``).
1.4 (Auftrag shorts-arbeitskopie): ``arbeitskopie`` (Punkt: einmalige
sequentielle Kopie von rendered_video/avatar-cut auf das Laufwerk des
Ausgabeordners, statt je Kandidat von der Festplatte zu springen) und
``dauer_sekunden`` ergaenzt.
1.5 (Auftrag shorts-framezahl-cache): ``derived_inputs`` um
``rendered_video_dimensions``/``avatar_frame_count``/``avatar_source_width``/
``avatar_source_height`` ergaenzt - die je Aufnahme (nicht je Kandidat)
konstanten Messungen, die jetzt einmal in :func:`derive_inputs` erhoben und
an jeden Kandidaten durchgereicht werden, statt je Kandidat neu gemessen zu
werden.
1.6 (Auftrag shorts-bau-parallel): ``summary.parallel`` ergaenzt - mit wievielen
gleichzeitigen Kandidaten der Lauf gebaut hat (1 = das serielle Verhalten).
1.7 (Auftrag shorts-framezahl-seitendatei): ``derived_inputs`` um
``rendered_video_framecount_cache``/``avatar_framecount_cache`` ergaenzt - ob
die Framezahl aus einer Seitendatei gelesen wurde statt neu mit
``ffprobe -count_frames`` gemessen (der teuerste Teil des Vorlaufs, siehe
:func:`_probe_frame_count_cached`).
1.8 (Auftrag shorts-stillevorlauf): ``pegelkorrektur.stillevorlauf`` ergaenzt -
ob die Startgrenze wegen langer Stille vor dem ersten Ton vorgeschoben wurde,
der gemessene Sprechpegel und die gefundene Stillelaenge. Siehe
:func:`matrix_auto_cutter.shorts.level_cut.finde_stillevorlauf`.
1.9 (Auftrag shorts-stillevorlauf-toleranz): ``pegelkorrektur.stillevorlauf``
um ``unterbrechungen_anzahl``/``laengste_unterbrechung_ms`` ergaenzt - wie
viele kurze Unterbrechungen (Nachhall, Musikakzente) im gewaehlten
Stillebereich ueberbrueckt wurden und wie lang die laengste davon war.
2.0 (Auftrag shorts-3b-verdrahtung): je Kandidat ``mausverfolgung`` ergaenzt -
``grund`` aus :attr:`cursor_track.Versatzkurve.grund` (oder der Grund, warum
gar keine Kurve gerechnet wurde), Zahl der ``fahrten``, ``versatz_anfang`` und
``versatz_ende``, dazu ``naehte`` und ``eingefrorene_frames``. Ohne diese
Zeilen laesst sich spaeter nicht nachsehen, was Stufe 3b getan hat."""

BUILD_REPORT_FILE_NAME = "shorts-bau-bericht.json"
AVATAR_CUT_FILE_NAME = "avatar-cut.mp4"
AUSSCHNITT_FILE_NAME = "ausschnitt.json"
ARBEITSKOPIE_DIR_NAME = "arbeitskopie"

PARALLEL_DEFAULT = 4
"""Voreingestellte Zahl gleichzeitig gebauter Kandidaten - Auftrag shorts-bau-parallel.

Gemessen, nicht geraten: siehe ``artefakte/repeat/shorts-bau-parallel/``. ``1``
ist das serielle Verhalten von vor diesem Auftrag, Zeichen fuer Zeichen derselbe
Weg durch den Code (kein Thread, kein Pool)."""

ABBRUCH_CODE = "abgebrochen"
"""Grund eines Kandidaten, der wegen Strg+C nicht mehr fertig gebaut wurde."""

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class BuildFailed:
    """Fail-closed Auskunft, bevor irgendein Kandidat gebaut wurde."""

    code: str
    message_de: str


# ---------------------------------------------------------------------------
# Framezahl-Seitendatei (Auftrag shorts-framezahl-seitendatei): die beiden
# ``ffprobe -count_frames``-Messungen in :func:`derive_inputs` (gerendertes
# Video, avatar-cut.mp4) sind der teuerste Teil des Vorlaufs - 64 % der
# Gesamtzeit, 27 % CPU-Auslastung (gemessen im Auftrag shorts-bau-parallel).
# Das Ergebnis ist eine Eigenschaft der Datei und aendert sich nie, solange
# Groesse und Aenderungszeit gleich bleiben - es wird deshalb neben der Datei
# vermerkt und beim naechsten Lauf gelesen statt gemessen.
# ---------------------------------------------------------------------------

FRAMECOUNT_CACHE_SCHEMA_VERSION = "1"
FRAMECOUNT_SIDECAR_SUFFIX = ".framecount.json"
FRAMECOUNT_CACHE_FALLBACK_DIRNAME = "shorts-framecount-cache"
"""Ausweichverzeichnis unter ``artefakte/repeat/`` fuer Quelllaufwerke, auf die
nicht geschrieben werden darf (siehe :data:`READONLY_DRIVES`)."""

READONLY_DRIVES = frozenset({"F:"})
"""Laufwerke, auf die dieser Auftrag NIE schreibt - F: ist das Quelllaufwerk der
Aufnahmen, ausschliesslich lesend benutzt."""


def _sanitize_path_for_filename(path: Path) -> str:
    """Baue aus einem vollstaendigen Pfad einen Dateinamen fuer das Ausweichverzeichnis.

    Eindeutig statt huebsch: Laufwerksbuchstabe und Trenner werden durch ``_``
    ersetzt. Wird der Name (z. B. durch sehr lange Pfade) unhandlich lang,
    tritt ein Kurzhash an die Stelle der Mitte - die Eindeutigkeit bleibt
    erhalten, der Dateiname bleibt dateisystemtauglich.
    """
    raw = str(path).replace(":", "").replace("\\", "_").replace("/", "_")
    if len(raw) <= 150:
        return raw
    import hashlib

    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f"{raw[:80]}__{digest}__{raw[-40:]}"


def _framecount_sidecar_path(video_path: Path, *, fallback_dir: Path) -> Path:
    """Wo die Seitendatei liegt: neben der Quelldatei, ausser deren Laufwerk ist gesperrt."""
    resolved = video_path.resolve()
    if resolved.drive.upper() in READONLY_DRIVES:
        return fallback_dir / f"{_sanitize_path_for_filename(resolved)}{FRAMECOUNT_SIDECAR_SUFFIX}"
    return video_path.parent / f"{video_path.name}{FRAMECOUNT_SIDECAR_SUFFIX}"


def _read_framecount_cache(sidecar_path: Path, *, video_path: Path) -> int | None:
    """Lies die Seitendatei; ``None`` wenn sie fehlt, kaputt ist oder nicht mehr passt.

    Gueltig heisst: ``schema_version`` bekannt, UND Groesse UND Aenderungszeit
    der Quelldatei stimmen mit dem Vermerk ueberein. Weicht eines ab (Datei neu
    gerendert, ueberschrieben, ...), gilt die Seitendatei als nicht vorhanden -
    der Aufrufer misst neu.
    """
    try:
        stat = video_path.stat()
    except OSError:
        return None
    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != FRAMECOUNT_CACHE_SCHEMA_VERSION:
        return None
    if payload.get("source_size_bytes") != stat.st_size:
        return None
    if payload.get("source_mtime_ns") != stat.st_mtime_ns:
        return None
    frame_count = payload.get("frame_count")
    if not isinstance(frame_count, int) or frame_count <= 0:
        return None
    return frame_count


def _write_framecount_cache(
    sidecar_path: Path, *, video_path: Path, frame_count: int
) -> str | None:
    """Schreibe die Seitendatei atomar (erst temporaer, dann umbenennen).

    Gibt bei Erfolg ``None`` zurueck, sonst die Fehlermeldung - ein
    fehlgeschlagenes Schreiben soll den Lauf nicht anhalten, nur vermerkt
    werden (siehe :func:`_probe_frame_count_cached`).
    """
    try:
        stat = video_path.stat()
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": FRAMECOUNT_CACHE_SCHEMA_VERSION,
            "video_path": str(video_path),
            "frame_count": frame_count,
            "source_size_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
        }
        tmp_path = sidecar_path.with_name(
            f"{sidecar_path.name}.tmp-{os.getpid()}-{threading.get_ident()}"
        )
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, sidecar_path)
    except OSError as exc:
        return str(exc)
    return None


@dataclass(frozen=True, slots=True)
class FrameCountCacheInfo:
    """Auskunft ueber die Framezahl-Seitendatei einer Datei - fuer den Bericht."""

    aktiv: bool
    cache_treffer: bool
    pfad: Path | None
    geschrieben: bool
    schreibfehler_de: str | None


def _probe_frame_count_cached(
    video_path: Path,
    *,
    ffprobe_path: Path,
    timeout_seconds: int,
    cache_aktiv: bool,
    fallback_dir: Path,
) -> tuple[int | None, FrameCountCacheInfo]:
    """Wie ``avatar_cut.probe_frame_count``, aber mit Seitendatei-Cache.

    Auftrag shorts-framezahl-seitendatei. Gilt AUSSCHLIESSLICH fuer die
    Framezahl der EINGABEN (gerendertes Video, avatar-cut.mp4) - die vier
    Ausgabepruefungen je Kandidat messen weiterhin jedes Mal neu, sie sind
    die Absicherung.
    """
    if not cache_aktiv:
        frame_count = probe_frame_count(
            video_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
        )
        return frame_count, FrameCountCacheInfo(False, False, None, False, None)

    sidecar_path = _framecount_sidecar_path(video_path, fallback_dir=fallback_dir)
    cached = _read_framecount_cache(sidecar_path, video_path=video_path)
    if cached is not None:
        return cached, FrameCountCacheInfo(True, True, sidecar_path, False, None)

    frame_count = probe_frame_count(
        video_path, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    if frame_count is None:
        return None, FrameCountCacheInfo(True, False, sidecar_path, False, None)

    schreibfehler = _write_framecount_cache(
        sidecar_path, video_path=video_path, frame_count=frame_count
    )
    return frame_count, FrameCountCacheInfo(
        True, False, sidecar_path, schreibfehler is None, schreibfehler
    )


def _artefakte_repeat_root(output_dir: Path) -> Path:
    """Suche ``artefakte/repeat/`` oberhalb von ``output_dir`` - sonst ``cwd``-relativ.

    ``output_dir`` liegt konventionell unter ``artefakte/repeat/<auftrag>/`` -
    das Ausweichverzeichnis der Framezahl-Seitendatei
    (:data:`FRAMECOUNT_CACHE_FALLBACK_DIRNAME`) soll ein Geschwister aller
    Auftraege sein, nicht in einem einzelnen davon verschwinden.
    """
    resolved = output_dir.resolve()
    for parent in (resolved, *resolved.parents):
        if parent.name == "repeat" and parent.parent.name == "artefakte":
            return parent
    return Path.cwd() / "artefakte" / "repeat"


@dataclass(frozen=True, slots=True)
class DerivedInputs:
    """Die fuenf frueher handgetippten Werte, mit Quelle - Punkt 1.

    ``rendered_video_dimensions``/``avatar_frame_count``/``avatar_source_width``/
    ``avatar_source_height`` (Auftrag shorts-framezahl-cache): je Aufnahme
    konstante Messungen, die zuvor JE KANDIDAT neu erhoben wurden
    (``chart_crop.probe_dimensions`` auf das gerenderte Video in
    ``_run_chart_crop_for_span``, ``probe_dimensions``/``probe_frame_count`` auf
    ``avatar-cut.mp4`` in ``avatar_canvas.run_stage5b``) - hier einmal gemessen
    und an jeden Kandidaten durchgereicht, siehe :func:`derive_inputs`.

    ``rendered_video_framecount_cache``/``avatar_framecount_cache`` (Auftrag
    shorts-framezahl-seitendatei): Auskunft ueber die Seitendatei, die die
    beiden ``ffprobe -count_frames``-Messungen dieser Klasse ueberspringen
    kann, siehe :func:`_probe_frame_count_cached`.
    """

    canvas_recording_id: str
    avatar_recording_id: str
    expected_avatar_frame_count: int
    rendered_video_path: Path
    rendered_video_dimensions: tuple[int, int]
    avatar_frame_count: int
    avatar_source_width: int
    avatar_source_height: int
    rendered_video_framecount_cache: FrameCountCacheInfo = field(
        default_factory=lambda: FrameCountCacheInfo(False, False, None, False, None)
    )
    avatar_framecount_cache: FrameCountCacheInfo = field(
        default_factory=lambda: FrameCountCacheInfo(False, False, None, False, None)
    )


@dataclass(frozen=True, slots=True)
class SceneFilterInfo:
    """Auskunft ueber den angewendeten (oder uebersprungenen) Szenenfilter - Punkt 3a."""

    applied: bool
    skip_reason: str | None
    journal_path: Path | None
    excluded_candidate_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class LevelCorrectionInfo:
    """Auskunft ueber die Pegelkorrektur beider Grenzen - Auftrag shorts-pegelschnitt.

    ``applied`` ist ``False``, wenn die Messung fehlgeschlagen ist; dann steht in
    ``fail_code``/``fail_message_de``, warum, und der Kandidat wurde mit den rein
    gerasteten Grenzen gebaut ("ohne Pegelkorrektur"). Ein Messfehler soll keinen
    Kandidaten kosten - er wird nur vermerkt.
    """

    applied: bool
    fail_code: str | None
    fail_message_de: str | None
    start: LevelSnap | None
    end: LevelSnap | None
    stillevorlauf: StilleVorlauf | None = None
    """Auftrag shorts-stillevorlauf: Ergebnis der Vorpruefung an der STARTgrenze,
    bevor ``start`` gemessen wurde. ``None``, wenn ``--kein-stillevorlauf`` gesetzt war."""


@dataclass(frozen=True, slots=True)
class ArbeitskopieInfo:
    """Auskunft ueber die Arbeitskopie - Auftrag shorts-arbeitskopie.

    ``build.py`` liest sonst je Kandidat erneut vom Quelllaufwerk (gerendertes
    Video fuer chart_crop, avatar-cut.mp4 fuer avatar_canvas) - bei vielen
    Kandidaten viele Sprungzugriffe, was einer Festplatte schlecht liegt. Statt
    dessen wird VOR dem ersten Kandidaten einmal sequentiell auf das Laufwerk
    des Ausgabeordners kopiert, alle Kandidaten lesen danach von dort.

    ``aktiv`` ist ``False``, wenn ``--keine-arbeitskopie`` gesetzt war, beide
    Dateien schon auf dem Ziellaufwerk lagen, oder das Kopieren fehlschlug -
    in jedem Fall wird dann mit den Originalpfaden weitergebaut (ein
    langsamer Lauf ist besser als kein Lauf).
    """

    aktiv: bool
    grund_deaktiviert: str | None
    kopierte_dateien: tuple[str, ...]
    uebersprungene_dateien: tuple[str, ...]
    kopierdauer_sekunden: float
    fehlgeschlagen: bool
    fehler_de: str | None
    arbeitsverzeichnis: Path | None


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """Ergebnis eines einzelnen Kandidaten: gebaut oder nicht, mit Grund."""

    index: int
    titel: str
    status: str  # "gebaut" | "nicht_gebaut"
    grund_code: str | None
    grund_de: str | None
    schleifen_einstufung: str | None
    build_start_ms: int | None
    build_end_ms: int | None
    output_path: str | None
    pegelkorrektur: LevelCorrectionInfo | None = None
    """``None``, solange der Kandidat es gar nicht bis zur Pegelmessung geschafft hat."""
    mausverfolgung: KurvenInfo | None = None
    """Auftrag shorts-3b-verdrahtung: was Stufe 3b fuer diesen Kandidaten entschieden hat -
    ``None``, solange der Kandidat es gar nicht bis dorthin geschafft hat."""
    achsenabweichung_frames: int | None = None
    achsenabweichung_hinweis: str | None = None
    """Auftrag shorts-achsenpruefung-warnung: die Achsenpruefung (avatar_canvas, Punkt 5)
    ist von einem Abbruch zu einer Warnung geworden - beide Felder sind ``None``, solange
    der Kandidat es gar nicht bis avatar_canvas geschafft hat, sonst die von
    :func:`matrix_auto_cutter.shorts.avatar_canvas.run_stage5b` gemeldete Abweichung
    (0, wenn keine)."""


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Vollstaendiges Ergebnis eines Baulaufs - immer eine Uebersicht, nie ein Teilausfall."""

    video_name: str
    derived: DerivedInputs
    scene_filter: SceneFilterInfo
    outcomes: tuple[CandidateOutcome, ...]
    arbeitskopie: ArbeitskopieInfo
    dauer_sekunden: float
    parallel: int
    """Auftrag shorts-bau-parallel: wieviele Kandidaten gleichzeitig gebaut wurden (1 = seriell)."""
    derive_inputs_dauer_sekunden: float
    """Auftrag shorts-framezahl-seitendatei: Zeit fuer :func:`derive_inputs` allein - der
    Vorlauf, den die Framezahl-Seitendatei verkuerzen soll. Separat von ``dauer_sekunden``
    (dem ganzen Lauf), damit die Ersparnis belegbar ist."""

    @property
    def built_count(self) -> int:
        """Anzahl tatsaechlich gebauter Kandidaten."""
        return sum(1 for outcome in self.outcomes if outcome.status == "gebaut")

    @property
    def excluded_by_scene_filter_count(self) -> int:
        """Anzahl vom Szenenfilter ausgeschlossener Kandidaten."""
        return sum(
            1 for outcome in self.outcomes if outcome.grund_code == "ausserhalb_charts_fenster"
        )

    @property
    def excluded_by_loop_point_count(self) -> int:
        """Anzahl vom Schleifenpunkt ausgeschlossener Kandidaten."""
        return sum(
            1
            for outcome in self.outcomes
            if outcome.grund_code in {"schleife_ungeeignet", "schleife_nicht_rastbar"}
        )

    @property
    def achsenabweichung_count(self) -> int:
        """Anzahl gebauter Kandidaten mit von null verschiedener Achsenabweichung.

        Auftrag shorts-achsenpruefung-warnung: macht die Warnung in der Uebersicht
        sichtbar, statt dass sie nur je Kandidat im Bericht steht.
        """
        return sum(
            1
            for outcome in self.outcomes
            if outcome.achsenabweichung_frames not in (None, 0)
        )


# ---------------------------------------------------------------------------
# Punkt 1: die fuenf Werte ableiten, fail closed bei fehlenden Feldern.
# ---------------------------------------------------------------------------


def _require_str(payload: dict[str, object], *keys: str) -> str | BuildFailed:
    node: object = payload
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return BuildFailed(
                "job_field_missing", f"shorts-job.json: Feld {'.'.join(keys)} fehlt"
            )
        node = node[key]
    if not isinstance(node, str) or not node.strip():
        return BuildFailed(
            "job_field_invalid", f"shorts-job.json: Feld {'.'.join(keys)} ist kein gueltiger Text"
        )
    return node


def load_job(job_path: Path) -> dict[str, object] | BuildFailed:
    """Lies ``shorts-job.json``; jeder Defekt ist ein eigener Fehlercode."""
    try:
        payload = json.loads(job_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return BuildFailed("job_unreadable", f"{job_path} konnte nicht gelesen werden: {exc}")
    except ValueError as exc:
        return BuildFailed("job_invalid_json", f"{job_path} ist kein gueltiges JSON: {exc}")
    if not isinstance(payload, dict):
        return BuildFailed("job_invalid_json", f"{job_path}: erwartet ein Objekt")
    return payload


def derive_inputs(
    job: dict[str, object],
    *,
    avatar_cut_path: Path,
    ffprobe_path: Path | None,
    timeout_seconds: int,
    framecount_cache_aktiv: bool = True,
    framecount_cache_fallback_dir: Path | None = None,
) -> DerivedInputs | BuildFailed:
    """Leite die frueher handgetippten Werte aus der Auftragsdatei ab (Punkt 1).

    Auftrag shorts-framezahl-cache: neben den fuenf urspruenglichen Werten
    werden hier zusaetzlich die Aufloesung des gerenderten Videos (fuer
    chart_crop) und Framezahl/Aufloesung von ``avatar_cut_path`` (fuer
    avatar_canvas) EINMAL gemessen, statt je Kandidat neu - beide Dateien
    sind je Lauf konstant. Schlaegt eine dieser Messungen fehl, waere sie bei
    JEDEM Kandidaten identisch fehlgeschlagen (dieselbe Datei) - der Lauf
    haelt deshalb hier an, bevor irgendein Kandidat gebaut wird, statt densel
    denselben Fehler N Mal zu wiederholen.

    Auftrag shorts-framezahl-seitendatei: die beiden ``ffprobe -count_frames``-
    Messungen (``expected_avatar_frame_count``/``avatar_frame_count``) laufen
    ueber :func:`_probe_frame_count_cached` - bei einer gueltigen Seitendatei
    entfaellt die Messung ganz. ``framecount_cache_fallback_dir`` (Standard
    ``Path.cwd() / "artefakte" / "repeat" / "shorts-framecount-cache"``, wenn
    ``None``) nimmt die Seitendatei auf, wenn die Quelldatei auf einem
    gesperrten Laufwerk liegt (siehe :data:`READONLY_DRIVES`).
    """
    video_name = _require_str(job, "video_name")
    if isinstance(video_name, BuildFailed):
        return video_name
    rendered_path_text = _require_str(job, "rendered_video", "path")
    if isinstance(rendered_path_text, BuildFailed):
        return rendered_path_text
    rendered_video_path = Path(rendered_path_text)

    fallback_dir = (
        framecount_cache_fallback_dir
        if framecount_cache_fallback_dir is not None
        else Path.cwd() / "artefakte" / "repeat" / FRAMECOUNT_CACHE_FALLBACK_DIRNAME
    )

    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return BuildFailed("ffprobe_not_found", "ffprobe nicht gefunden (PATH pruefen)")
    expected_avatar_frame_count, rendered_framecount_cache = _probe_frame_count_cached(
        rendered_video_path,
        ffprobe_path=ffprobe,
        timeout_seconds=timeout_seconds,
        cache_aktiv=framecount_cache_aktiv,
        fallback_dir=fallback_dir,
    )
    if expected_avatar_frame_count is None:
        return BuildFailed(
            "rendered_video_frame_count_unknown",
            f"ffprobe konnte die Framezahl des gerenderten Videos nicht ermitteln: "
            f"{rendered_video_path}",
        )

    rendered_video_dimensions = chart_crop.probe_dimensions(
        rendered_video_path, ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
    )
    if rendered_video_dimensions is None:
        return BuildFailed(
            "chart_crop_resolution_unknown",
            f"ffprobe konnte die Aufloesung des gerenderten Videos nicht ermitteln: "
            f"{rendered_video_path}",
        )
    if rendered_video_dimensions != (chart_crop.SOURCE_WIDTH, chart_crop.SOURCE_HEIGHT):
        return BuildFailed(
            "chart_crop_resolution_mismatch",
            f"Quellaufloesung {rendered_video_dimensions[0]}x{rendered_video_dimensions[1]} "
            f"weicht von {chart_crop.SOURCE_WIDTH}x{chart_crop.SOURCE_HEIGHT} ab",
        )

    avatar_dimensions = chart_crop.probe_dimensions(
        avatar_cut_path, ffprobe_path=ffprobe, timeout_seconds=timeout_seconds
    )
    if avatar_dimensions is None:
        return BuildFailed(
            "avatar_resolution_unknown",
            f"ffprobe konnte die Avatar-Aufloesung nicht ermitteln: {avatar_cut_path}",
        )
    avatar_frame_count, avatar_framecount_cache = _probe_frame_count_cached(
        avatar_cut_path,
        ffprobe_path=ffprobe,
        timeout_seconds=timeout_seconds,
        cache_aktiv=framecount_cache_aktiv,
        fallback_dir=fallback_dir,
    )
    if avatar_frame_count is None:
        return BuildFailed(
            "avatar_frame_count_unknown",
            f"ffprobe konnte die Avatar-Framezahl nicht ermitteln: {avatar_cut_path}",
        )

    return DerivedInputs(
        canvas_recording_id=video_name,
        avatar_recording_id=video_name,
        expected_avatar_frame_count=expected_avatar_frame_count,
        rendered_video_path=rendered_video_path,
        rendered_video_dimensions=rendered_video_dimensions,
        avatar_frame_count=avatar_frame_count,
        avatar_source_width=avatar_dimensions[0],
        avatar_source_height=avatar_dimensions[1],
        rendered_video_framecount_cache=rendered_framecount_cache,
        avatar_framecount_cache=avatar_framecount_cache,
    )


# ---------------------------------------------------------------------------
# Punkt 3a: Szenenfilter - Journal fehlt/kaputt heisst "melden, alle bauen".
# ---------------------------------------------------------------------------


def _load_rendered_charts_windows(
    job: dict[str, object],
    *,
    journal_directory: Path,
) -> tuple[
    tuple[tuple[int, int], ...] | None, SceneFilterInfo, tuple[KeepSegment, ...]
]:
    """Bilde die Charts-Fenster auf die gerenderte Achse ab, wenn moeglich.

    Gibt ``(None, info)`` zurueck, wenn der Filter aus irgendeinem Grund
    nicht angewendet werden kann (kein Journal, kein Proposal, keine
    Freigabe) - der Aufrufer baut dann ALLE Kandidaten statt das ganze Video
    stillschweigend auszuschliessen.
    """
    proposal_node = job.get("proposal")
    recording_id = (
        proposal_node.get("recording_id") if isinstance(proposal_node, dict) else None
    )
    proposal_path_text = (
        proposal_node.get("path") if isinstance(proposal_node, dict) else None
    )
    if not isinstance(recording_id, str) or not recording_id:
        return None, SceneFilterInfo(False, "kein_recording_id_im_auftrag", None, ()), ()
    if not isinstance(proposal_path_text, str) or not proposal_path_text:
        return None, SceneFilterInfo(False, "kein_proposal_im_auftrag", None, ()), ()

    journal_path = journal_directory / f"{recording_id}.recording-journal.ndjson"
    if not journal_path.is_file():
        return None, SceneFilterInfo(False, "journal_nicht_gefunden", journal_path, ()), ()

    gate = inspect_approval_state(Path(proposal_path_text))
    if not gate.authorized or gate.proposal is None:
        return None, SceneFilterInfo(False, "proposal_nicht_freigegeben", journal_path, ()), ()

    active_candidate_ids = (
        gate.approval.active_candidate_ids
        if isinstance(gate.approval, SelectiveProposalApproval)
        else None
    )
    cuts = effective_cuts(gate.proposal.proposed_cuts, active_candidate_ids)
    screen_intervals = tuple((cut.start_frame, cut.end_frame) for cut in cuts)
    try:
        keep_segments = keep_segments_from_intervals(
            screen_intervals, gate.proposal.source_frame_count
        )
    except ValueError:
        return None, SceneFilterInfo(False, "schnittintervalle_ungueltig", journal_path, ()), ()

    scene_result = load_scene_windows(journal_path)
    if isinstance(scene_result, SceneWindowsFailed):
        # Keep-Segmente stehen hier schon fest und bleiben gueltig: die
        # Mausverfolgung braucht sie, den Szenenfilter aber nicht.
        return (
            None,
            SceneFilterInfo(False, f"journal_{scene_result.reason}", journal_path, ()),
            tuple(keep_segments),
        )

    source_windows = tuple((window.start_frame, window.end_frame) for window in scene_result)
    rendered_windows = map_source_interval_to_rendered(keep_segments, source_windows)
    return rendered_windows, SceneFilterInfo(True, None, journal_path, ()), tuple(keep_segments)


# ---------------------------------------------------------------------------
# Punkt 3c: Mausverfolgung (Stufe 3b) - EINMAL JE LAUF gelesen, nicht je Kandidat.
#
# Der Bau ist am 20.8. von 515 s auf 63 s je Short gebracht worden, unter
# anderem dadurch, dass Werte einmal je Lauf statt je Kandidat gemessen
# werden. Das Cursorprotokoll hat 5000 bis 7000 Zeilen; es 33 mal zu lesen
# waere genau der Rueckschritt, den dieses Muster vermeiden soll.
# ---------------------------------------------------------------------------

MAUSVERFOLGUNG_ABGESCHALTET = "abgeschaltet"
MAUSVERFOLGUNG_KEIN_EINTRAG = "kein_cursorprotokoll_im_auftrag"
MAUSVERFOLGUNG_DATEI_FEHLT = "cursorprotokoll_nicht_gefunden"
MAUSVERFOLGUNG_UNLESBAR = "cursorprotokoll_unlesbar"
MAUSVERFOLGUNG_KEINE_SEGMENTE = "keine_keep_segmente"
MAUSVERFOLGUNG_AUSSCHNITT_VORRANG = "ausschnitt_json_vorrang"
MAUSVERFOLGUNG_SPANNE_UNGUELTIG = "kandidatenspanne_ungueltig"


@dataclass(frozen=True, slots=True)
class Mausverfolgung:
    """Alles, was die Versatzkurve braucht - fuer die Dauer des Laufs unveraenderlich.

    Wird von mehreren Kandidaten NEBENLAEUFIG gelesen (siehe
    :func:`_kandidat_verarbeiten`) und darf deshalb nur unveraenderliche
    Felder tragen.
    """

    aktiv: bool
    grund: str | None
    """Warum keine Kurve gerechnet wird - ``None``, solange ``aktiv``."""

    csv_pfad: Path | None = None
    anker: datetime | None = None
    zeilen: tuple[CursorZeile, ...] = ()
    segmente: tuple[KeepSegment, ...] = ()
    rendered_windows: tuple[tuple[int, int], ...] | None = None


@dataclass(frozen=True, slots=True)
class KurvenInfo:
    """Was Stufe 3b fuer EINEN Kandidaten entschieden hat - fuer den Baubericht."""

    grund: str
    fahrten: int
    versatz_anfang: int
    versatz_ende: int
    naehte: int = 0
    eingefrorene_frames: int = 0


def _lade_mausverfolgung(
    job: dict[str, object],
    *,
    segmente: tuple[KeepSegment, ...],
    rendered_windows: tuple[tuple[int, int], ...] | None,
    aktiviert: bool,
) -> Mausverfolgung:
    """Lies das Cursorprotokoll EINMAL und halte es samt Anker fuer den ganzen Lauf.

    RUECKFAELLE SIND DER NORMALFALL: nur 7 von 27 gerenderten Aufnahmen haben
    ueberhaupt ein Cursorprotokoll. Fehlt es, ist es unlesbar oder fehlen die
    Keep-Segmente, ist das KEIN Abbruch - der Lauf baut dann mit dem festen
    Versatz ``chart_crop.X_OFFSET_DEFAULT`` weiter und nennt den Grund im
    Baubericht.

    DER ANKER ist ``csv_first_row_at`` aus der Waechter-Seitendatei
    ``cursor-<aufnahme>.json``, NICHT ``recording_started_at`` - beide liegen
    rund 158 ms auseinander, und die Kalibrierung (Auftrag
    ``shorts-anker-kalibrierung``) hat gegen ``csv_first_row_at`` gemessen.
    ``shorts-job.json`` traegt den Wert nicht selbst; es traegt aber den Pfad
    zur csv, und ``csv_first_row_at`` ist per Definition der Zeitstempel ihrer
    ersten Zeile (an beiden bekannten Aufnahmen woertlich nachgeprueft). Der
    Anker ist damit ohne jede Aenderung an ``inventory.py`` oder ``job.py`` zu
    bekommen - der kleinstmoegliche Eingriff.
    """
    if not aktiviert:
        return Mausverfolgung(False, MAUSVERFOLGUNG_ABGESCHALTET)
    knoten = job.get("cursor_log")
    pfad_text = knoten.get("path") if isinstance(knoten, dict) else None
    if not isinstance(pfad_text, str) or not pfad_text:
        return Mausverfolgung(False, MAUSVERFOLGUNG_KEIN_EINTRAG)
    csv_pfad = Path(pfad_text)
    if not csv_pfad.is_file():
        return Mausverfolgung(False, MAUSVERFOLGUNG_DATEI_FEHLT, csv_pfad=csv_pfad)
    try:
        zeilen = lies_cursorprotokoll(csv_pfad)
    except (OSError, CursorProtokollError):
        return Mausverfolgung(False, MAUSVERFOLGUNG_UNLESBAR, csv_pfad=csv_pfad)
    if not zeilen:
        return Mausverfolgung(False, MAUSVERFOLGUNG_UNLESBAR, csv_pfad=csv_pfad)
    if not segmente:
        return Mausverfolgung(False, MAUSVERFOLGUNG_KEINE_SEGMENTE, csv_pfad=csv_pfad)
    return Mausverfolgung(
        aktiv=True,
        grund=None,
        csv_pfad=csv_pfad,
        anker=zeilen[0].zeit,
        zeilen=zeilen,
        segmente=segmente,
        rendered_windows=rendered_windows,
    )


def _berechne_versatzkurve(
    mausverfolgung: Mausverfolgung,
    *,
    kandidat_index: int,
    build_start_ms: int,
    build_end_ms: int,
    offsets: dict[int, int],
) -> tuple[tuple[int, ...] | None, KurvenInfo]:
    """Die Versatzkurve fuer GENAU EINEN Kandidaten, samt Auskunft fuer den Bericht.

    ``ausschnitt.json`` HAT VORRANG: Traegt sie einen Eintrag fuer diesen
    Kandidaten, wird gar keine Kurve gerechnet. Das ist der Notausgang, wenn
    eine Kurve einmal danebenliegt.

    Gibt ``(None, info)`` zurueck, wenn der feste Versatz gilt - der Aufrufer
    reicht dann nichts an ``chart_crop`` durch und bekommt genau den Weg von
    vor diesem Auftrag.
    """
    fester = offsets.get(kandidat_index, chart_crop.X_OFFSET_DEFAULT)
    if kandidat_index in offsets:
        return None, KurvenInfo(MAUSVERFOLGUNG_AUSSCHNITT_VORRANG, 0, fester, fester)
    if not mausverfolgung.aktiv:
        grund = mausverfolgung.grund or MAUSVERFOLGUNG_KEIN_EINTRAG
        return None, KurvenInfo(grund, 0, fester, fester)
    try:
        spanne = candidate_frame_span(build_start_ms, build_end_ms, BUILD_FPS)
    except ValueError:
        return None, KurvenInfo(MAUSVERFOLGUNG_SPANNE_UNGUELTIG, 0, fester, fester)
    if spanne[1] <= spanne[0]:
        return None, KurvenInfo(MAUSVERFOLGUNG_SPANNE_UNGUELTIG, 0, fester, fester)

    kurve: Versatzkurve = versatzkurve(
        kandidatenspanne=spanne,
        segmente=mausverfolgung.segmente,
        zeilen=mausverfolgung.zeilen,
        anker=mausverfolgung.anker,
        szenenfenster=mausverfolgung.rendered_windows,
        fps=BUILD_FPS,
    )
    info = KurvenInfo(
        grund=kurve.grund,
        fahrten=len(kurve.fahrten),
        versatz_anfang=kurve.werte[0],
        versatz_ende=kurve.werte[-1],
        naehte=len(kurve.naehte),
        eingefrorene_frames=kurve.eingefrorene_frames,
    )
    if kurve.ist_rueckfall:
        # Rueckfaelle bleiben Rueckfaelle: fester Versatz, Grund benannt, kein
        # Abbruch. Eine konstante Kurve durchzureichen waere derselbe Bildinhalt
        # bei mehr beweglichen Teilen im ffmpeg-Aufruf.
        return None, info
    return kurve.werte, info


# ---------------------------------------------------------------------------
# Punkt 3b: Schleifenpunkt - Wortliste des GANZEN Videos, nicht des Ausschnitts.
# ---------------------------------------------------------------------------


def load_whole_video_words(job_path: Path) -> list[Word] | BuildFailed:
    """Lies die Wortliste des ganzen (gerenderten) Videos fuer den Schleifenpunkt-Filter.

    Aus der whisper-Rohausgabe der gerenderten Fassung
    (``transkript-rendered.wav.json``, neben ``shorts-job.json`` - siehe
    :mod:`matrix_auto_cutter.shorts.transcript`), NICHT aus einem
    Ausschnitt-Transkript - nur so sind die Pausen vor und nach einem
    Kandidaten ueberhaupt messbar.
    """
    raw_json_path, _ = transcript_paths(job_path.parent, wav_name=RENDERED_WAV_NAME)
    if not raw_json_path.is_file():
        return BuildFailed(
            "whole_video_transcript_missing",
            f"Wortliste des ganzen Videos fehlt: {raw_json_path} - ohne sie ist der "
            "Schleifenpunkt-Filter nicht pruefbar",
        )
    try:
        raw_json = raw_json_path.read_text(encoding="utf-8")
        return words_from_whisper_json(raw_json)
    except (OSError, ValueError) as exc:
        return BuildFailed(
            "whole_video_transcript_unreadable", f"{raw_json_path} unlesbar: {exc}"
        )


RANDWORT_MINDESTANTEIL = 0.5
"""Mindestanteil eines Wortes, der in die Spanne ragen muss, damit es (geklemmt)
aufgenommen wird."""


def _words_for_span(words: Sequence[Word], start_ms: int, end_ms: int) -> list[Word]:
    """Woerter, die ``[start_ms, end_ms)`` beruehren, geklemmt und auf 0 verschoben.

    Ein Wort, das die Spanne nur teilweise ueberlappt, ist im Ton hoerbar -
    bleibt es unberuecksichtigt, hoert der Zuschauer ein Wort mehr, als er im
    Untertitel liest. Deshalb wird jedes beruehrende Wort aufgenommen und auf
    die Spanne geklemmt, sofern mindestens ``RANDWORT_MINDESTANTEIL`` seiner
    Dauer in der Spanne liegt - sonst bliebe ein unlesbarer Wortfetzen uebrig.

    Die Ausgabe-Zeitachse eines gebauten Kandidaten beginnt bei 0 (Frame 0
    des Ausschnitts), ``subtitle_burn``s ``enable='between(t,...)'`` erwartet
    genau diese relative Achse - deshalb werden die geklemmten Zeiten auch
    nie negativ oder ueber die Cliplaenge hinaus.
    """
    result = []
    for word in words:
        if word.end_ms <= start_ms or word.start_ms >= end_ms:
            continue
        overlap_start = max(word.start_ms, start_ms)
        overlap_end = min(word.end_ms, end_ms)
        overlap_ms = overlap_end - overlap_start
        word_duration_ms = word.end_ms - word.start_ms
        if word_duration_ms > 0 and overlap_ms / word_duration_ms < RANDWORT_MINDESTANTEIL:
            continue
        result.append(Word(overlap_start - start_ms, overlap_end - start_ms, word.text))
    return result


# ---------------------------------------------------------------------------
# Arbeitskopie (Auftrag shorts-arbeitskopie): einmal sequentiell auf das
# Laufwerk des Ausgabeordners kopieren statt je Kandidat vom Quelllaufwerk zu
# springen. Auf F: (oder jedem anderen Quelllaufwerk) wird NICHTS geschrieben
# und NICHTS geloescht - die Kopie liegt ausschliesslich unter output_dir.
# ---------------------------------------------------------------------------


def _laufwerksbuchstabe(path: Path) -> str:
    """Der Laufwerksbuchstabe (z. B. ``"P:"``) - Vergleichsbasis ist NIE der Pfad."""
    return path.resolve().drive.upper()


def _bereite_arbeitskopie_vor(
    *,
    output_dir: Path,
    rendered_video_path: Path,
    avatar_cut_path: Path,
    aktiviert: bool,
) -> tuple[Path, Path, ArbeitskopieInfo]:
    """Kopiere rendered_video/avatar-cut einmalig auf das Laufwerk von ``output_dir``.

    Gibt ``(rendered_video_path, avatar_cut_path, info)`` zurueck - zeigen auf die
    Kopie, wenn eine angelegt wurde, sonst unveraendert auf die Originale. Jede
    Datei, die schon auf dem Laufwerk von ``output_dir`` liegt, wird NICHT
    kopiert (Vergleich ueber Laufwerksbuchstaben, nicht Pfade).

    Schlaegt das Kopieren fehl (kein Platz, Lesefehler, ...): NICHT abbrechen -
    mit den Originalpfaden weiterbauen und das im zurueckgegebenen ``info``
    vermerken. Ein langsamer Lauf ist besser als kein Lauf.
    """
    if not aktiviert:
        return (
            rendered_video_path,
            avatar_cut_path,
            ArbeitskopieInfo(
                aktiv=False,
                grund_deaktiviert="--keine-arbeitskopie",
                kopierte_dateien=(),
                uebersprungene_dateien=(),
                kopierdauer_sekunden=0.0,
                fehlgeschlagen=False,
                fehler_de=None,
                arbeitsverzeichnis=None,
            ),
        )

    ziel_laufwerk = _laufwerksbuchstabe(output_dir)
    quellen = {
        "rendered_video": rendered_video_path,
        "avatar_cut": avatar_cut_path,
    }
    zu_kopieren = [
        name for name, quelle in quellen.items() if _laufwerksbuchstabe(quelle) != ziel_laufwerk
    ]

    if not zu_kopieren:
        return (
            rendered_video_path,
            avatar_cut_path,
            ArbeitskopieInfo(
                aktiv=False,
                grund_deaktiviert="beide_dateien_bereits_auf_zielaufwerk",
                kopierte_dateien=(),
                uebersprungene_dateien=(),
                kopierdauer_sekunden=0.0,
                fehlgeschlagen=False,
                fehler_de=None,
                arbeitsverzeichnis=None,
            ),
        )

    arbeitsverzeichnis = output_dir / ARBEITSKOPIE_DIR_NAME
    start = time.perf_counter()
    try:
        arbeitsverzeichnis.mkdir(parents=True, exist_ok=True)
        neue_pfade: dict[str, Path] = {}
        for name in zu_kopieren:
            ziel = arbeitsverzeichnis / quellen[name].name
            shutil.copy2(quellen[name], ziel)
            neue_pfade[name] = ziel
    except OSError as exc:
        kopierdauer_sekunden = time.perf_counter() - start
        shutil.rmtree(arbeitsverzeichnis, ignore_errors=True)
        return (
            rendered_video_path,
            avatar_cut_path,
            ArbeitskopieInfo(
                aktiv=False,
                grund_deaktiviert=None,
                kopierte_dateien=(),
                uebersprungene_dateien=(),
                kopierdauer_sekunden=kopierdauer_sekunden,
                fehlgeschlagen=True,
                fehler_de=str(exc),
                arbeitsverzeichnis=None,
            ),
        )
    kopierdauer_sekunden = time.perf_counter() - start

    return (
        neue_pfade.get("rendered_video", rendered_video_path),
        neue_pfade.get("avatar_cut", avatar_cut_path),
        ArbeitskopieInfo(
            aktiv=True,
            grund_deaktiviert=None,
            kopierte_dateien=tuple(zu_kopieren),
            uebersprungene_dateien=tuple(name for name in quellen if name not in zu_kopieren),
            kopierdauer_sekunden=kopierdauer_sekunden,
            fehlgeschlagen=False,
            fehler_de=None,
            arbeitsverzeichnis=arbeitsverzeichnis,
        ),
    )


# ---------------------------------------------------------------------------
# Nebenlaeufigkeit (Auftrag shorts-bau-parallel): die Arbeit steckt in den
# ffmpeg-Unterprozessen, nicht in Python - deshalb Threads, kein zweiter
# Interpreter. Damit Strg+C trotzdem sauber endet, laeuft JEDER ffmpeg-Lauf der
# vier Stufen ueber :class:`_ProzessWache`: sie kennt jeden gestarteten Prozess
# und beendet sie alle auf Zuruf. Die vier Stufenmodule bleiben unveraendert -
# sie nehmen den ``process_runner`` seit jeher als Parameter entgegen.
# ---------------------------------------------------------------------------


class _ProzessWache:
    """Startet die ffmpeg-Laeufe der Stufen und kann sie alle gemeinsam abbrechen.

    Verhaelt sich Zeichen fuer Zeichen wie der ``_default_process_runner`` der
    vier Stufenmodule (dieselbe Umleitung, dieselbe Zeitgrenze, dieselbe
    Fehlerbehandlung) - mit einem Unterschied: der laufende Prozess steht in
    einer Liste, solange er laeuft. :meth:`brich_ab` beendet jeden davon.

    Ohne diese Liste bliebe bei Strg+C ein Thread in ``subprocess.run`` haengen:
    Ein KeyboardInterrupt erreicht nur den Hauptthread, nie die Arbeitsthreads.
    """

    def __init__(self) -> None:
        self._sperre = threading.Lock()
        self._laufende: set[subprocess.Popen[bytes]] = set()
        self.abgebrochen = threading.Event()

    def brich_ab(self) -> None:
        """Setze die Abbruchmarke und beende jeden gerade laufenden ffmpeg-Prozess."""
        self.abgebrochen.set()
        with self._sperre:
            laufende = list(self._laufende)
        for prozess in laufende:
            prozess.kill()

    def runner(self, ergebnis: Callable[[int, bytes], _T]) -> Callable[[Sequence[str], int], _T]:
        """Baue den ``process_runner`` fuer eine Stufe - je Modul eigene ProcessResult-Klasse."""

        def _runner(arguments: Sequence[str], timeout_seconds: int) -> _T:
            """Fuehre ein Kommando aus und melde Ausgang samt begrenzter Diagnoseausgabe."""
            return self._lauf(arguments, timeout_seconds, ergebnis)

        return _runner

    def _lauf(
        self,
        arguments: Sequence[str],
        timeout_seconds: int,
        ergebnis: Callable[[int, bytes], _T],
    ) -> _T:
        if self.abgebrochen.is_set():
            return ergebnis(-1, b"Lauf abgebrochen (Strg+C) - nicht gestartet")
        try:
            prozess = subprocess.Popen(
                list(arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ergebnis(-1, str(exc).encode("utf-8", errors="replace"))
        with self._sperre:
            self._laufende.add(prozess)
        if self.abgebrochen.is_set():
            # Der Abbruch kam zwischen Start und Eintrag - sonst liefe dieser
            # eine Prozess als einziger weiter.
            prozess.kill()
        try:
            stdout, _ = prozess.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            prozess.kill()
            prozess.communicate()
            return ergebnis(-1, str(exc).encode("utf-8", errors="replace"))
        except (OSError, subprocess.SubprocessError) as exc:
            prozess.kill()
            prozess.communicate()
            return ergebnis(-1, str(exc).encode("utf-8", errors="replace"))
        finally:
            with self._sperre:
                self._laufende.discard(prozess)
        return ergebnis(prozess.returncode, stdout or b"")


class _Laufnotizen:
    """Merkt sich, welche Kandidatenordner DIESER Lauf angelegt hat - fuer den Abbruch.

    Bei Strg+C sollen keine halb gebauten Ordner zurueckbleiben. Geloescht wird
    aber nur, was dieser Lauf selbst angelegt hat und was nicht fertig geworden
    ist - ein Ordner aus einem frueheren Lauf bleibt unangetastet.
    """

    def __init__(self) -> None:
        self._sperre = threading.Lock()
        self._angelegt: dict[int, Path] = {}
        self._fertig: set[int] = set()

    def verzeichnis_angelegt(self, index: int, pfad: Path) -> None:
        """Vermerke, dass ``pfad`` fuer Kandidat ``index`` neu angelegt wurde."""
        with self._sperre:
            self._angelegt[index] = pfad

    def kandidat_fertig(self, index: int) -> None:
        """Vermerke, dass Kandidat ``index`` vollstaendig durchgelaufen ist."""
        with self._sperre:
            self._fertig.add(index)

    def raeume_unfertige_auf(self) -> tuple[Path, ...]:
        """Loesche jeden neu angelegten Ordner, dessen Kandidat nicht fertig wurde."""
        with self._sperre:
            offen = {
                index: pfad
                for index, pfad in self._angelegt.items()
                if index not in self._fertig
            }
        for pfad in offen.values():
            shutil.rmtree(pfad, ignore_errors=True)
        return tuple(sorted(offen.values()))


# ---------------------------------------------------------------------------
# Die vier Filterstufen je Kandidat - chart_crop -> canvas -> avatar_canvas ->
# subtitle_burn. chart_crop und avatar_canvas erwarten eigene Kandidatengrenzen,
# canvas und subtitle_burn nur den jeweiligen Eingabepfad.
# ---------------------------------------------------------------------------


def _load_offsets(ausschnitt_path: Path | None) -> dict[int, int] | BuildFailed:
    if ausschnitt_path is None:
        return {}
    try:
        return chart_crop.load_offsets(ausschnitt_path)
    except chart_crop.AusschnittSchemaError as exc:
        return BuildFailed("ausschnitt_invalid", str(exc))


def _run_chart_crop_for_span(
    *,
    rendered_video_path: Path,
    candidate: Candidate,
    offsets: dict[int, int],
    kurve: Sequence[int] | None,
    output_path: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None,
    timeout_seconds: int,
    wache: _ProzessWache,
) -> chart_crop.ProcessResult | BuildFailed:
    """Wie ``chart_crop.run_stage3a_for_candidate``, aber mit selbst gebauter Kandidatenspanne.

    ``run_stage3a_for_candidate`` liest die Kandidatengrenzen selbst aus
    ``kandidaten.json`` - hier sind die Grenzen bereits durch den
    Schleifenpunkt-Filter korrigiert, deshalb wird der Zuschnittplan direkt
    aus dem (bereits korrigierten) ``candidate`` gebaut, mit denselben
    oeffentlichen Bausteinen wie die Stufe selbst (kein Umbau von
    ``chart_crop.py``).

    Auftrag shorts-framezahl-cache: die Aufloesungspruefung auf
    ``rendered_video_path`` (dieselbe Datei fuer jeden Kandidaten) ist NICHT
    mehr hier - sie ist Teil von :func:`derive_inputs` (Punkt 1) geworden und
    laeuft dort genau einmal je Lauf, statt hier bei jedem Kandidaten erneut
    per ffprobe gemessen zu werden.
    """
    plan = chart_crop.plan_chart_crop(candidate, offsets=offsets, fps=BUILD_FPS, kurve=kurve)
    process_result = chart_crop.run_chart_crop(
        input_path=rendered_video_path,
        output_path=output_path,
        plan=plan,
        ffmpeg_path=ffmpeg_path,
        process_runner=wache.runner(chart_crop.ProcessResult),
        timeout_seconds=timeout_seconds,
    )
    if process_result.exit_code != 0:
        return BuildFailed(
            "chart_crop_ffmpeg_failed",
            process_result.stderr.decode("utf-8", errors="replace"),
        )
    checks = chart_crop.verify_chart_crop_output(
        output_path, plan, ffprobe_path=ffprobe_path, timeout_seconds=timeout_seconds
    )
    report_path = output_path.parent / f"{output_path.stem}.json"
    chart_crop.write_chart_crop_report(
        report_path, chart_crop.chart_crop_report_payload(plan, checks)
    )
    failure_code = checks.first_failure_code
    if failure_code is not None:
        return BuildFailed(
            f"chart_crop_{failure_code}", f"chart_crop-Pruefung fehlgeschlagen: {report_path}"
        )
    return process_result


LEVEL_CUT_TIMEOUT_SECONDS = 120
"""Zeitgrenze je Pegelmessung - eine Messung dauert real unter 0,1 s, das ist
reichlich Luft und unabhaengig von der grossen Zeitgrenze des Baus."""


def _apply_level_correction(
    *,
    boundaries: LoopBoundaries,
    rendered_video_path: Path,
    ffmpeg_path: Path,
    search_window_start_ms: int | None = None,
    stillevorlauf_aktiv: bool = True,
    end_min_nachklang_ms: int | None = None,
) -> tuple[int, int, LevelCorrectionInfo]:
    """Schiebe beide gerasteten Grenzen auf die jeweils leiseste Stelle - Punkt 5.

    Die Reihenfolge ist bewusst: erst rasten (grob, thematisch, ``loop_point``),
    dann Stillevorlauf (grob, akustisch, Auftrag shorts-stillevorlauf), dann
    Pegel (fein, akustisch). Gemessen wird auf dem GERENDERTEN Video - dort
    liegt der endgueltige Ton samt Musik und Lautheitskorrektur.

    ``stillevorlauf_aktiv`` (Standard an, ``--kein-stillevorlauf`` in der CLI):
    prueft VOR der Pegelmessung, ob vor dem ersten Ton lange Stille liegt
    (:func:`level_cut.finde_stillevorlauf`) - eine whisper-Segmentgrenze kann
    mehrere Sekunden vor dem eigentlichen Satzbeginn liegen, was die enge
    Pegelmessung unten (150 ms) nicht erreicht. Wird eine gefunden, gilt die
    verschobene Marke als Ausgangspunkt fuer die anschliessende Pegelmessung.

    ``search_window_start_ms`` (Auftrag shorts-pegelfenster-vergleich) ist seit
    Auftrag shorts-tonblende OHNE WIRKUNG in dieser Funktion: es reichte das
    Suchfenster der :func:`level_cut.verschiebe_auf_leiseste_stelle`-Suche an
    der STARTgrenze durch, und genau diese Suche entfaellt jetzt im
    Normalfall (die Startgrenze wird direkt gesetzt, siehe unten) - der
    Parameter bleibt nur der Signatur/CLI zuliebe erhalten (unveraendert
    durchgereicht bis hierher), nicht weil er noch etwas bewirkt.

    Auftrag shorts-tonblende: drei Fassungen der reinen Punktsuche
    (shorts-wortgrenzen, shorts-endgrenze-schranke, shorts-wortrand-abstand)
    klangen weiterhin angeschnitten - zwischen zwei Woertern fluessiger Rede
    liegt selten ein wirklich sauberer Schnittpunkt (gemessene Pausen dieser
    Aufnahme im Median 40 ms). Statt die Grenze weiter vor das Wort zu
    schieben, bleibt das Wort jetzt bewusst GANZ in der gebauten Spanne, und
    eine kurze Ton-Ein-/Ausblende (:data:`chart_crop.TON_EINBLENDE_MS`/
    :data:`chart_crop.TON_AUSBLENDE_MS`, in ``chart_crop.build_ffmpeg_filter_complex``
    angewandt) macht den harten Schnitt am Rand unhoerbar. Beide Grenzen
    werden weiterhin ueber :func:`level_cut.finde_wortrand_ende`/
    :func:`level_cut.finde_wortrand_anfang` (Pausengrund-Verfahren, TEIL 1)
    bestimmt - nur die WAHL der tatsaechlichen Grenze aus diesen gemessenen
    Wortraendern hat sich geaendert, keine neue Messung:

    * ENDgrenze: DIREKT ``wahres_wortende + max(MIN_NACHKLANG_MS,
      end_min_nachklang_ms)``, gedeckelt auf hoechstens den wahren Anfang des
      naechsten Wortes (nicht mehr dessen Whisper-Marke - bei
      ``pause_after_ms = 0`` ist die echte Pause nicht null, nur in den
      Whisper-Zahlen unsichtbar). Kein Suchen mehr
      (:func:`level_cut.verschiebe_auf_leiseste_stelle` entfaellt hier) -
      Verfahren :data:`level_cut.VERFAHREN_TONBLENDE_GROSSZUEGIG`.
    * STARTgrenze: DIREKT ``wahrer_wortanfang - chart_crop.TON_EINBLENDE_MS``,
      gedeckelt auf mindestens das wahre Ende des vorigen Wortes (ebenso am
      Ton gemessen, nicht dessen Whisper-Marke). Ebenfalls kein Suchen mehr -
      Verfahren :data:`level_cut.VERFAHREN_TONBLENDE_GROSSZUEGIG`.
    * Kollidieren beide Seiten (der direkte Wert wuerde vor bzw. an den
      wahren Rand des Nachbarworts selbst reichen - kein Platz mehr), gilt
      unveraendert die Mitte zwischen wahrem Wortende und wahrem Anfang des
      Nachbarn (:data:`level_cut.VERFAHREN_WORTRAND_KOLLISION`) - kein
      Fallback auf eine Whisper-Marke.
    * Findet :func:`level_cut.finde_wortrand_ende`/
      :func:`level_cut.finde_wortrand_anfang` keinen Bereich (selten - kein
      Pausengrund im Suchfenster), fallen die beiden Funktionen auf die
      aelteren, threshold-basierten :func:`level_cut.finde_wortende_ton`/
      :func:`level_cut.finde_worteinsatz_ton` zurueck.

    Die frueheren Konstanten :data:`level_cut.WORTRAND_ABSTAND_ENDE_MS`/
    :data:`level_cut.WORTRAND_ABSTAND_ANFANG_MS` (Auftrag
    shorts-wortrand-abstand) werden in dieser Formel nicht mehr verwendet -
    ihre WERTE bleiben unveraendert stehen (siehe dort), nur der Gebrauch
    hier entfaellt, weil die Blende den Uebergang jetzt traegt.

    LOOP_PAD_MS selbst bleibt unveraendert (VERBOTEN, siehe Auftrag).

    Schlaegt eine Messung fehl, bleiben die gerasteten Grenzen stehen und der
    Kandidat wird "ohne Pegelkorrektur" gebaut: Ein Messfehler soll keinen
    Kandidaten kosten. Die Module selbst fallen nicht still zurueck - der
    Rueckfall wird hier bewusst und vermerkt entschieden.
    """
    # Die reinen (ungepolsterten) Wortgrenzen aus den gemessenen Pausen
    # zurueckrechnen - siehe loop_point.rasten_auf_wortgrenzen:
    # padded_start = new_start - min(LOOP_PAD_MS, pause_before_ms)
    # padded_end   = new_end   + min(LOOP_PAD_MS, pause_after_ms)
    pause_before_ms = boundaries.pause_before_ms
    pause_after_ms = boundaries.pause_after_ms
    start_pad_ms = LOOP_PAD_MS if pause_before_ms is None else min(LOOP_PAD_MS, pause_before_ms)
    end_pad_ms = LOOP_PAD_MS if pause_after_ms is None else min(LOOP_PAD_MS, pause_after_ms)
    new_start_ms = boundaries.start_ms + start_pad_ms
    new_end_ms = boundaries.end_ms - end_pad_ms
    prev_word_end_ms = None if pause_before_ms is None else new_start_ms - pause_before_ms

    stille_ergebnis: StilleVorlauf | None = None
    try:
        if stillevorlauf_aktiv:
            stille_ergebnis = finde_stillevorlauf(
                rendered_video_path,
                boundaries.start_ms,
                boundaries.end_ms,
                ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )

        # Auftrag shorts-wortrand-abstand: der whisper-Anfang des naechsten
        # (bzw. Ende des vorigen) Wortes dient nur noch als ANKER fuer die
        # Pausengrund-Suchen - die eigentlichen Schranken sind die daraus
        # gemessenen wahren Wortraender, nicht mehr die Whisper-Marken selbst.
        next_word_start_ms = None if pause_after_ms is None else new_end_ms + pause_after_ms

        # Eigenes Wortende/eigener Wortanfang - Pausengrund-Verfahren, mit
        # Rueckfall auf die aeltere, threshold-basierte Messung, falls kein
        # Pausengrund-Bereich im Suchfenster liegt.
        own_true_end_ms = finde_wortrand_ende(
            rendered_video_path, new_end_ms, ffmpeg_path=ffmpeg_path,
            timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
        )
        if own_true_end_ms is None:
            own_true_end_ms = finde_wortende_ton(
                rendered_video_path, new_end_ms, ffmpeg_path=ffmpeg_path,
                ober_grenze_ms=next_word_start_ms, timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )
        # Anker fuer die eigene Anlauf-Suche: normalerweise die reine
        # Wortgrenze - AUSSER der Stillevorlauf hat gerade gegriffen
        # (``verschoben``): dann steht new_start_ms auf einer whisper-Marke,
        # die selbst um bis zu :data:`level_cut.VORLAUF_SUCHE_MAX_MS`
        # danebenliegt (Auftrag shorts-stillevorlauf) - die enge, lokale
        # Wortrand-Suche (+-150/60 ms) wuerde dort blind in der falschen
        # Stille suchen. Der Stillevorlauf hat den echten Sprechbeginn schon
        # robust (bis 3000 ms) gefunden - dessen Ergebnis ist der bessere Anker.
        anfang_anker_ms = (
            stille_ergebnis.corrected_ms
            if stille_ergebnis is not None and stille_ergebnis.verschoben
            else new_start_ms
        )
        own_true_start_ms = finde_wortrand_anfang(
            rendered_video_path, anfang_anker_ms, ffmpeg_path=ffmpeg_path,
            timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
        )
        if own_true_start_ms is None:
            own_true_start_ms = finde_worteinsatz_ton(
                rendered_video_path, new_start_ms, ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )

        # Nachbarwoerter: wahres Ende des vorigen (Schranke der Startgrenze)
        # und wahrer Anfang des naechsten (Schranke der Endgrenze) - jeweils
        # am eigenen wahren Wortrand gedeckelt, sonst koennte die Suche ueber
        # das eigene, kurze Wort hinweglaufen (Befund: "angekommen." vor
        # kandidat-00, ungedeckelt 31140 - hinter dem Anfang von "Na," selbst).
        # Auftrag shorts-nachbarrand: bei einer Nullpause (pause_before_ms/
        # pause_after_ms <= 0) liegt die whisper-Marke des Nachbarworts exakt
        # auf der eigenen, ungekorrigierten Wortgrenze - die Pausengrund-Suche
        # um DIESE Marke (finde_wortrand_ende/finde_wortrand_anfang) trifft
        # dann oft nur den eigenen Ausklang/Anlauf statt die tatsaechliche
        # Pause zum Nachbarn. In diesem Fall sucht zuerst die neue,
        # unabhaengige Suche ab dem schon gemessenen EIGENEN wahren Wortrand
        # (finde_nachbarrand_ausklang/finde_nachbarrand_einsatz); liefert sie
        # keinen Treffer (oder ist die Pause > 0 bzw. unbekannt), gilt
        # unveraendert das bisherige Verfahren.
        neighbor_true_end_ms = None
        if prev_word_end_ms is not None:
            if pause_before_ms is not None and pause_before_ms <= 0:
                neighbor_true_end_ms = finde_nachbarrand_ausklang(
                    rendered_video_path, own_true_start_ms, ffmpeg_path=ffmpeg_path,
                    timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
                )
            if neighbor_true_end_ms is None:
                neighbor_true_end_ms = finde_wortrand_ende(
                    rendered_video_path, prev_word_end_ms, ffmpeg_path=ffmpeg_path,
                    ober_grenze_ms=own_true_start_ms, timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
                )
            if neighbor_true_end_ms is None:
                neighbor_true_end_ms = prev_word_end_ms
        neighbor_true_start_ms = None
        if next_word_start_ms is not None:
            if pause_after_ms is not None and pause_after_ms <= 0:
                neighbor_true_start_ms = finde_nachbarrand_einsatz(
                    rendered_video_path, own_true_end_ms, ffmpeg_path=ffmpeg_path,
                    timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
                )
            if neighbor_true_start_ms is None:
                neighbor_true_start_ms = finde_wortrand_anfang(
                    rendered_video_path, next_word_start_ms, ffmpeg_path=ffmpeg_path,
                    unter_grenze_ms=own_true_end_ms, timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
                )
            if neighbor_true_start_ms is None:
                neighbor_true_start_ms = next_word_start_ms

        # --- Startgrenze (Auftrag shorts-tonblende): grosszuegig DIREKT
        # gesetzt statt gesucht - das Wort darf ganz in der Spanne bleiben,
        # weil die neue Ton-Einblende (chart_crop.TON_EINBLENDE_MS) den
        # Uebergang jetzt selbst weich macht. Keine neue Messung, nur eine
        # andere Wahl innerhalb der schon gemessenen Wortraender. Kollidiert
        # der direkte Wert mit dem wahren Ende des vorigen Wortes (kein Platz
        # mehr), gilt weiterhin die Mitte (VERFAHREN_WORTRAND_KOLLISION,
        # unveraendert).
        start_floor_ms = own_true_start_ms - chart_crop.TON_EINBLENDE_MS
        if neighbor_true_end_ms is not None and neighbor_true_end_ms >= start_floor_ms:
            kollision_start_ms = (neighbor_true_end_ms + own_true_start_ms) // 2
            level_db, window_mean_db = miss_pegel_bei_marke(
                rendered_video_path, kollision_start_ms, ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )
            start_snap = LevelSnap(
                original_ms=boundaries.start_ms,
                corrected_ms=kollision_start_ms,
                shift_ms=kollision_start_ms - boundaries.start_ms,
                level_db=level_db,
                window_mean_db=window_mean_db,
                verfahren=VERFAHREN_WORTRAND_KOLLISION,
                quiet_region_ms=0,
            )
        else:
            start_ms = (
                start_floor_ms
                if neighbor_true_end_ms is None
                else max(start_floor_ms, neighbor_true_end_ms)
            )
            level_db, window_mean_db = miss_pegel_bei_marke(
                rendered_video_path, start_ms, ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )
            start_snap = LevelSnap(
                original_ms=boundaries.start_ms,
                corrected_ms=start_ms,
                shift_ms=start_ms - boundaries.start_ms,
                level_db=level_db,
                window_mean_db=window_mean_db,
                verfahren=VERFAHREN_TONBLENDE_GROSSZUEGIG,
                quiet_region_ms=0,
            )

        # --- Endgrenze (Auftrag shorts-tonblende): spiegelbildlich
        # grosszuegig DIREKT gesetzt statt gesucht - die Ton-Ausblende
        # (chart_crop.TON_AUSBLENDE_MS) traegt den Uebergang. Der
        # Mindestnachklang (MIN_NACHKLANG_MS, ``end_min_nachklang_ms`` wenn
        # gesetzt) bleibt die einzige Reserve zum wahren Wortende. Kollidiert
        # der direkte Wert mit dem wahren Anfang des naechsten Wortes (kein
        # Platz mehr), gilt weiterhin die Mitte (VERFAHREN_WORTRAND_KOLLISION,
        # unveraendert).
        nachklang_ms = MIN_NACHKLANG_MS if end_min_nachklang_ms is None else end_min_nachklang_ms
        end_floor_ms = own_true_end_ms + nachklang_ms
        if neighbor_true_start_ms is not None and end_floor_ms >= neighbor_true_start_ms:
            kollision_end_ms = (own_true_end_ms + neighbor_true_start_ms) // 2
            level_db, window_mean_db = miss_pegel_bei_marke(
                rendered_video_path, kollision_end_ms, ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )
            end_snap = LevelSnap(
                original_ms=boundaries.end_ms,
                corrected_ms=kollision_end_ms,
                shift_ms=kollision_end_ms - boundaries.end_ms,
                level_db=level_db,
                window_mean_db=window_mean_db,
                verfahren=VERFAHREN_WORTRAND_KOLLISION,
                quiet_region_ms=0,
            )
        else:
            end_ms = (
                end_floor_ms
                if neighbor_true_start_ms is None
                else min(end_floor_ms, neighbor_true_start_ms)
            )
            level_db, window_mean_db = miss_pegel_bei_marke(
                rendered_video_path, end_ms, ffmpeg_path=ffmpeg_path,
                timeout_seconds=LEVEL_CUT_TIMEOUT_SECONDS,
            )
            end_snap = LevelSnap(
                original_ms=boundaries.end_ms,
                corrected_ms=end_ms,
                shift_ms=end_ms - boundaries.end_ms,
                level_db=level_db,
                window_mean_db=window_mean_db,
                verfahren=VERFAHREN_TONBLENDE_GROSSZUEGIG,
                quiet_region_ms=0,
            )
    except LevelCutFailed as exc:
        return (
            boundaries.start_ms,
            boundaries.end_ms,
            LevelCorrectionInfo(
                applied=False,
                fail_code=exc.code,
                fail_message_de=exc.message_de,
                start=None,
                end=None,
                stillevorlauf=stille_ergebnis,
            ),
        )

    if end_snap.corrected_ms - start_snap.corrected_ms < MIN_SPAN_MS:
        # Bei +-250 ms Verschiebung auf mehreren Sekunden Spanne praktisch
        # unerreichbar - aber eine verdrehte oder zu kurze Spanne waere
        # schlimmer als eine ungeschliffene Kante.
        return (
            boundaries.start_ms,
            boundaries.end_ms,
            LevelCorrectionInfo(
                applied=False,
                fail_code="pegelkorrektur_verkuerzt_spanne",
                fail_message_de=(
                    f"pegelkorrigierte Spanne ({end_snap.corrected_ms - start_snap.corrected_ms} "
                    f"ms) unterschreitet {MIN_SPAN_MS} ms - gerastete Grenzen behalten"
                ),
                start=None,
                end=None,
                stillevorlauf=stille_ergebnis,
            ),
        )

    return (
        start_snap.corrected_ms,
        end_snap.corrected_ms,
        LevelCorrectionInfo(
            applied=True,
            fail_code=None,
            fail_message_de=None,
            start=start_snap,
            end=end_snap,
            stillevorlauf=stille_ergebnis,
        ),
    )


def _build_one_candidate(
    *,
    candidate: Candidate,
    build_start_ms: int,
    build_end_ms: int,
    kurve: tuple[int, ...] | None,
    whole_video_words: Sequence[Word],
    rendered_video_path: Path,
    avatar_cut_path: Path,
    offsets: dict[int, int],
    derived: DerivedInputs,
    candidate_dir: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None,
    timeout_seconds: int,
    wache: _ProzessWache,
) -> tuple[str, str, int, str | None] | BuildFailed:
    """Fuehre die vier Stufen fuer genau einen (bereits gefilterten) Kandidaten aus.

    Gibt bei Erfolg ``(short_output_path, short_report_path,
    achsenabweichung_frames, achsenabweichung_hinweis)`` zurueck - die letzten
    beiden von :func:`avatar_canvas.run_stage5b` durchgereicht (Auftrag
    shorts-achsenpruefung-warnung).

    Die vier Stufen haengen voneinander ab (jede frisst die Ausgabe der
    vorigen) - sie laufen deshalb IMMER nacheinander. Nebenlaeufig sind
    Kandidaten, nicht Stufen (Auftrag shorts-bau-parallel). Zwischen den
    Stufen wird auf die Abbruchmarke der ``wache`` gesehen, damit Strg+C nicht
    erst nach der letzten Stufe wirkt.
    """
    span_candidate = replace(candidate, start_ms=build_start_ms, end_ms=build_end_ms)

    ausschnitt_path = candidate_dir / "ausschnitt.mp4"
    chart_result = _run_chart_crop_for_span(
        rendered_video_path=rendered_video_path,
        candidate=span_candidate,
        offsets=offsets,
        kurve=kurve,
        output_path=ausschnitt_path,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        timeout_seconds=timeout_seconds,
        wache=wache,
    )
    if isinstance(chart_result, BuildFailed):
        return chart_result

    if wache.abgebrochen.is_set():
        return BuildFailed(ABBRUCH_CODE, "Lauf abgebrochen (Strg+C)")

    leinwand_path = candidate_dir / "leinwand.mp4"
    canvas_result = canvas.run_stage5a(
        input_path=ausschnitt_path,
        output_path=leinwand_path,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        process_runner=wache.runner(canvas.ProcessResult),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(canvas_result, canvas.Stage5aFailed):
        return BuildFailed(f"canvas_{canvas_result.code}", canvas_result.message_de)
    if canvas_result.exit_code != 0:
        return BuildFailed(
            "canvas_ffmpeg_failed", canvas_result.stderr.decode("utf-8", errors="replace")
        )

    if wache.abgebrochen.is_set():
        return BuildFailed(ABBRUCH_CODE, "Lauf abgebrochen (Strg+C)")

    mit_avatar_path = candidate_dir / "mit-avatar.mp4"
    avatar_result = avatar_canvas.run_stage5b(
        canvas_path=leinwand_path,
        avatar_path=avatar_cut_path,
        output_path=mit_avatar_path,
        ffmpeg_path=ffmpeg_path,
        canvas_recording_id=derived.canvas_recording_id,
        avatar_recording_id=derived.avatar_recording_id,
        candidate_start_ms=build_start_ms,
        candidate_end_ms=build_end_ms,
        expected_avatar_frame_count=derived.expected_avatar_frame_count,
        avatar_frame_count=derived.avatar_frame_count,
        avatar_source_width=derived.avatar_source_width,
        avatar_source_height=derived.avatar_source_height,
        ffprobe_path=ffprobe_path,
        process_runner=wache.runner(avatar_canvas.ProcessResult),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(avatar_result, avatar_canvas.Stage5bFailed):
        return BuildFailed(f"avatar_canvas_{avatar_result.code}", avatar_result.message_de)
    if avatar_result.exit_code != 0:
        return BuildFailed(
            "avatar_canvas_ffmpeg_failed", avatar_result.stderr.decode("utf-8", errors="replace")
        )
    achsenabweichung_frames = avatar_result.achsenabweichung_frames
    achsenabweichung_hinweis = avatar_result.achsenabweichung_hinweis

    if wache.abgebrochen.is_set():
        return BuildFailed(ABBRUCH_CODE, "Lauf abgebrochen (Strg+C)")

    candidate_words = _words_for_span(whole_video_words, build_start_ms, build_end_ms)
    lines = build_subtitle_lines(candidate_words)
    short_path = candidate_dir / "short.mp4"
    subtitle_result = subtitle_burn.run_stage5c(
        input_path=mit_avatar_path,
        lines=lines,
        output_path=short_path,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        process_runner=wache.runner(subtitle_burn.ProcessResult),
        timeout_seconds=timeout_seconds,
    )
    if isinstance(subtitle_result, subtitle_burn.Stage5cFailed):
        return BuildFailed(f"subtitle_burn_{subtitle_result.code}", subtitle_result.message_de)
    if subtitle_result.exit_code != 0:
        return BuildFailed(
            "subtitle_burn_ffmpeg_failed",
            subtitle_result.stderr.decode("utf-8", errors="replace"),
        )
    report_path = short_path.parent / f"{short_path.stem}.json"
    return str(short_path), str(report_path), achsenabweichung_frames, achsenabweichung_hinweis


# ---------------------------------------------------------------------------
# Ende-zu-Ende: ein Aufruf je Video.
# ---------------------------------------------------------------------------


def run_shorts_build(
    *,
    job_path: Path,
    kandidaten_path: Path,
    output_dir: Path,
    ffmpeg_path: Path,
    avatar_cut_path: Path | None = None,
    ausschnitt_path: Path | None = None,
    journal_directory: Path | None = None,
    ffprobe_path: Path | None = None,
    timeout_seconds: int = 1800,
    search_window_start_ms: int | None = None,
    stillevorlauf_aktiv: bool = True,
    end_min_nachklang_ms: int | None = None,
    arbeitskopie_aktiv: bool = True,
    parallel: int = PARALLEL_DEFAULT,
    framecount_cache_aktiv: bool = True,
    mausverfolgung_aktiv: bool = True,
) -> BuildResult | BuildFailed:
    """Ende-zu-Ende: einen Auftrag samt Kandidatenliste zu fertigen Shorts bauen.

    Fail closed VOR jedem Kandidatenlauf (Punkt 1), wenn ein abgeleiteter
    Wert fehlt. Danach laeuft jeder Kandidat unabhaengig - bricht einer ab,
    bauen die uebrigen weiter (siehe Moduldoc).

    ``search_window_start_ms`` reicht (Auftrag shorts-pegelfenster-vergleich)
    unveraendert an :func:`_apply_level_correction` durch - siehe dort.

    ``end_min_nachklang_ms`` reicht (Auftrag shorts-endgrenze-schranke)
    unveraendert an :func:`_apply_level_correction` durch - ``None`` (Vorgabe)
    nimmt :data:`level_cut.MIN_NACHKLANG_MS`. Fuer den Pruefstein, der zwei
    Fassungen von kandidat-00 gegeneinander baut (Mindestnachklang gegen die
    maximal von TEIL 1 erlaubte Grenze), ohne diese Funktion zu verdoppeln.

    ``stillevorlauf_aktiv`` (Auftrag shorts-stillevorlauf, Standard an): schaltet
    die Stillevorlauf-Pruefung an der STARTgrenze ab - ``--kein-stillevorlauf``
    in der CLI. Siehe :func:`_apply_level_correction` und
    :func:`matrix_auto_cutter.shorts.level_cut.finde_stillevorlauf`.

    ``framecount_cache_aktiv`` (Auftrag shorts-framezahl-seitendatei, Standard
    an): schaltet die Framezahl-Seitendatei in :func:`derive_inputs` ab -
    ``--kein-framecount-cache`` in der CLI.

    ``mausverfolgung_aktiv`` (Auftrag shorts-3b-verdrahtung, Standard an):
    schaltet Stufe 3b ab - ``--keine-mausverfolgung`` in der CLI. Der Lauf
    faellt dann vollstaendig auf Stufe 3a zurueck (fester Versatz
    ``chart_crop.X_OFFSET_DEFAULT``, sonst identische Kandidatenspannen). Das
    ist der Weg, auf dem sich beide Fassungen nebeneinander vergleichen
    lassen.

    ``parallel`` (Auftrag shorts-bau-parallel, Voreinstellung
    :data:`PARALLEL_DEFAULT`): wieviele Kandidaten gleichzeitig gebaut werden.
    ``1`` ist genau das serielle Verhalten von zuvor. Ein Strg+C waehrend des
    Laufs beendet jeden laufenden ffmpeg-Prozess, loescht die angefangenen
    Kandidatenordner und die Arbeitskopie und reicht den ``KeyboardInterrupt``
    an den Aufrufer weiter.

    ``arbeitskopie_aktiv`` (Auftrag shorts-arbeitskopie, Standard an): vor dem
    ersten Kandidaten wird rendered_video/avatar-cut einmal sequentiell auf das
    Laufwerk von ``output_dir`` kopiert, alle Kandidaten lesen von dort - das
    Arbeitsverzeichnis wird am Ende IMMER geloescht (auch bei Fehlern), siehe
    :func:`_bereite_arbeitskopie_vor`.
    """
    if parallel < 1:
        return BuildFailed(
            "parallel_ungueltig", f"--parallel muss mindestens 1 sein, war {parallel}"
        )
    lauf_start = time.perf_counter()
    job = load_job(job_path)
    if isinstance(job, BuildFailed):
        return job

    resolved_avatar_cut_path = (
        avatar_cut_path if avatar_cut_path is not None else job_path.parent / AVATAR_CUT_FILE_NAME
    )
    if not resolved_avatar_cut_path.is_file():
        return BuildFailed(
            "avatar_cut_missing",
            f"nachgeschnittene Avatardatei (Stufe 1) fehlt: {resolved_avatar_cut_path}",
        )

    derive_inputs_start = time.perf_counter()
    derived = derive_inputs(
        job,
        avatar_cut_path=resolved_avatar_cut_path,
        ffprobe_path=ffprobe_path,
        timeout_seconds=timeout_seconds,
        framecount_cache_aktiv=framecount_cache_aktiv,
        framecount_cache_fallback_dir=(
            _artefakte_repeat_root(output_dir) / FRAMECOUNT_CACHE_FALLBACK_DIRNAME
        ),
    )
    derive_inputs_dauer_sekunden = time.perf_counter() - derive_inputs_start
    if isinstance(derived, BuildFailed):
        return derived

    candidate_ausschnitt_path = (
        ausschnitt_path
        if ausschnitt_path is not None
        else job_path.parent / AUSSCHNITT_FILE_NAME
    )
    resolved_ausschnitt_path = (
        candidate_ausschnitt_path if candidate_ausschnitt_path.is_file() else None
    )
    offsets = _load_offsets(resolved_ausschnitt_path)
    if isinstance(offsets, BuildFailed):
        return offsets

    try:
        candidates = load_candidates(kandidaten_path)
    except (OSError, CandidatesSchemaError) as exc:
        return BuildFailed("candidates_unreadable", str(exc))

    whole_video_words = load_whole_video_words(job_path)
    if isinstance(whole_video_words, BuildFailed):
        return whole_video_words

    resolved_journal_directory = (
        journal_directory if journal_directory is not None else default_journal_directory()
    )
    rendered_windows, scene_filter_info, keep_segments = _load_rendered_charts_windows(
        job, journal_directory=resolved_journal_directory
    )
    # EINMAL JE LAUF, nicht je Kandidat - siehe :func:`_lade_mausverfolgung`.
    mausverfolgung = _lade_mausverfolgung(
        job,
        segmente=keep_segments,
        rendered_windows=rendered_windows,
        aktiviert=mausverfolgung_aktiv,
    )

    video_name = derived.canvas_recording_id
    output_dir.mkdir(parents=True, exist_ok=True)

    active_rendered_video_path, active_avatar_cut_path, arbeitskopie_info = (
        _bereite_arbeitskopie_vor(
            output_dir=output_dir,
            rendered_video_path=derived.rendered_video_path,
            avatar_cut_path=resolved_avatar_cut_path,
            aktiviert=arbeitskopie_aktiv,
        )
    )

    wache = _ProzessWache()
    notizen = _Laufnotizen()
    try:
        excluded_by_scene, outcomes = _build_all_candidates(
            candidates=candidates,
            whole_video_words=whole_video_words,
            rendered_windows=rendered_windows,
            mausverfolgung=mausverfolgung,
            rendered_video_path=active_rendered_video_path,
            avatar_cut_path=active_avatar_cut_path,
            offsets=offsets,
            derived=derived,
            output_dir=output_dir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            timeout_seconds=timeout_seconds,
            search_window_start_ms=search_window_start_ms,
            stillevorlauf_aktiv=stillevorlauf_aktiv,
            end_min_nachklang_ms=end_min_nachklang_ms,
            parallel=parallel,
            wache=wache,
            notizen=notizen,
        )
    finally:
        if arbeitskopie_info.arbeitsverzeichnis is not None:
            shutil.rmtree(arbeitskopie_info.arbeitsverzeichnis, ignore_errors=True)

    scene_filter_info = replace(
        scene_filter_info, excluded_candidate_indices=tuple(excluded_by_scene)
    )
    return BuildResult(
        video_name=video_name,
        derived=derived,
        scene_filter=scene_filter_info,
        outcomes=tuple(outcomes),
        arbeitskopie=arbeitskopie_info,
        dauer_sekunden=time.perf_counter() - lauf_start,
        parallel=parallel,
        derive_inputs_dauer_sekunden=derive_inputs_dauer_sekunden,
    )


def _kandidat_verarbeiten(
    *,
    candidate: Candidate,
    whole_video_words: Sequence[Word],
    rendered_windows: tuple[tuple[int, int], ...] | None,
    mausverfolgung: Mausverfolgung,
    rendered_video_path: Path,
    avatar_cut_path: Path,
    offsets: dict[int, int],
    derived: DerivedInputs,
    output_dir: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None,
    timeout_seconds: int,
    search_window_start_ms: int | None,
    stillevorlauf_aktiv: bool,
    end_min_nachklang_ms: int | None,
    wache: _ProzessWache,
    notizen: _Laufnotizen,
) -> tuple[CandidateOutcome, bool]:
    """Beide Filter, die Pegelkorrektur und die vier Stufen fuer GENAU EINEN Kandidaten.

    Gibt ``(Ergebnis, vom_szenenfilter_ausgeschlossen)`` zurueck.

    Diese Funktion ist die Einheit, die nebenlaeufig laufen darf (Auftrag
    shorts-bau-parallel): Kandidaten haengen nicht voneinander ab. Alles, was
    sie liest - Wortliste, Versaetze, Fenster, die abgeleiteten Werte, beide
    Eingabedateien - ist fuer die Dauer des Laufs unveraenderlich; alles, was
    sie schreibt, liegt ausschliesslich unter ``output_dir/kandidat-NN``.
    """
    try:
        candidate_span = candidate_frame_span(candidate.start_ms, candidate.end_ms, BUILD_FPS)
    except ValueError as exc:
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code="ungueltige_kandidatenspanne",
                grund_de=str(exc),
                schleifen_einstufung=None,
                build_start_ms=None,
                build_end_ms=None,
                output_path=None,
            ),
            False,
        )

    if rendered_windows is not None and candidate_outside_windows(
        candidate_span, rendered_windows
    ):
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code="ausserhalb_charts_fenster",
                grund_de="Kandidat liegt ausserhalb aller Charts-Fenster des Journals",
                schleifen_einstufung=None,
                build_start_ms=None,
                build_end_ms=None,
                output_path=None,
            ),
            True,
        )

    try:
        boundaries: LoopBoundaries = rasten_auf_wortgrenzen(
            whole_video_words, candidate.start_ms, candidate.end_ms
        )
    except LoopPointError as exc:
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code="schleife_nicht_rastbar",
                grund_de=str(exc),
                schleifen_einstufung=None,
                build_start_ms=None,
                build_end_ms=None,
                output_path=None,
            ),
            False,
        )

    urteil = beurteile_grenzen(boundaries)
    if urteil.einstufung not in {GEEIGNET, GRENZWERTIG}:
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code="schleife_ungeeignet",
                grund_de=(
                    f"Schleifenurteil {urteil.einstufung!r}: Pause davor "
                    f"{urteil.pause_before_ms} ms, danach {urteil.pause_after_ms} ms"
                ),
                schleifen_einstufung=urteil.einstufung,
                build_start_ms=None,
                build_end_ms=None,
                output_path=None,
            ),
            False,
        )

    # Punkt 5: erst rasten (oben), dann Pegel - nie umgekehrt.
    build_start_ms, build_end_ms, level_info = _apply_level_correction(
        boundaries=boundaries,
        rendered_video_path=rendered_video_path,
        ffmpeg_path=ffmpeg_path,
        search_window_start_ms=search_window_start_ms,
        stillevorlauf_aktiv=stillevorlauf_aktiv,
        end_min_nachklang_ms=end_min_nachklang_ms,
    )

    # Stufe 3b auf der TATSAECHLICH gebauten Spanne, nicht auf den rohen
    # Kandidatengrenzen: die Pegelkorrektur oben hat sie gerade verschoben,
    # und die Kurve muss Frame fuer Frame zu dem passen, was gebaut wird.
    kurve, kurven_info = _berechne_versatzkurve(
        mausverfolgung,
        kandidat_index=candidate.index,
        build_start_ms=build_start_ms,
        build_end_ms=build_end_ms,
        offsets=offsets,
    )

    candidate_dir = output_dir / f"kandidat-{candidate.index:02d}"
    if not candidate_dir.exists():
        notizen.verzeichnis_angelegt(candidate.index, candidate_dir)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    try:
        build_result = _build_one_candidate(
            candidate=candidate,
            build_start_ms=build_start_ms,
            build_end_ms=build_end_ms,
            kurve=kurve,
            whole_video_words=whole_video_words,
            rendered_video_path=rendered_video_path,
            avatar_cut_path=avatar_cut_path,
            offsets=offsets,
            derived=derived,
            candidate_dir=candidate_dir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            timeout_seconds=timeout_seconds,
            wache=wache,
        )
    except Exception as exc:  # ein Kandidat darf die anderen nie stoppen
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code=f"unerwarteter_fehler:{type(exc).__name__}",
                grund_de=str(exc),
                schleifen_einstufung=urteil.einstufung,
                build_start_ms=build_start_ms,
                build_end_ms=build_end_ms,
                output_path=None,
                pegelkorrektur=level_info,
                mausverfolgung=kurven_info,
            ),
            False,
        )

    if isinstance(build_result, BuildFailed):
        return (
            CandidateOutcome(
                index=candidate.index,
                titel=candidate.titel,
                status="nicht_gebaut",
                grund_code=build_result.code,
                grund_de=build_result.message_de,
                schleifen_einstufung=urteil.einstufung,
                build_start_ms=build_start_ms,
                build_end_ms=build_end_ms,
                output_path=None,
                pegelkorrektur=level_info,
                mausverfolgung=kurven_info,
            ),
            False,
        )

    short_path, _report_path, achsenabweichung_frames, achsenabweichung_hinweis = build_result
    notizen.kandidat_fertig(candidate.index)
    return (
        CandidateOutcome(
            index=candidate.index,
            titel=candidate.titel,
            status="gebaut",
            grund_code=None,
            grund_de=None,
            schleifen_einstufung=urteil.einstufung,
            build_start_ms=build_start_ms,
            build_end_ms=build_end_ms,
            output_path=short_path,
            pegelkorrektur=level_info,
            mausverfolgung=kurven_info,
            achsenabweichung_frames=achsenabweichung_frames,
            achsenabweichung_hinweis=achsenabweichung_hinweis,
        ),
        False,
    )


def _build_all_candidates(
    *,
    candidates: Sequence[Candidate],
    whole_video_words: Sequence[Word],
    rendered_windows: tuple[tuple[int, int], ...] | None,
    mausverfolgung: Mausverfolgung,
    rendered_video_path: Path,
    avatar_cut_path: Path,
    offsets: dict[int, int],
    derived: DerivedInputs,
    output_dir: Path,
    ffmpeg_path: Path,
    ffprobe_path: Path | None,
    timeout_seconds: int,
    search_window_start_ms: int | None,
    stillevorlauf_aktiv: bool,
    end_min_nachklang_ms: int | None,
    parallel: int,
    wache: _ProzessWache,
    notizen: _Laufnotizen,
) -> tuple[list[int], list[CandidateOutcome]]:
    """Der eigentliche Kandidatenlauf - seriell oder mit ``parallel`` Kandidaten zugleich.

    Ausgelagert, damit die Arbeitskopie in ``run_shorts_build`` per try/finally
    aufgeraeumt werden kann, egal was hier passiert. ``rendered_video_path``/
    ``avatar_cut_path`` zeigen bereits auf die Arbeitskopie, falls eine angelegt
    wurde (siehe :func:`_bereite_arbeitskopie_vor`).

    ``parallel == 1`` nimmt die schlichte Schleife - denselben Weg wie vor dem
    Auftrag shorts-bau-parallel, ohne Thread und ohne Pool. Ab ``2`` laufen
    ``parallel`` Kandidaten gleichzeitig in Threads; die eigentliche Arbeit
    steckt in den ffmpeg-Unterprozessen, waehrend derer Python den GIL freigibt.

    Die Uebersicht ist in beiden Faellen nach Kandidatenindex sortiert - nie
    nach Fertigstellungszeit, sonst laege der Bericht bei jedem Lauf anders.
    """

    def _verarbeite(candidate: Candidate) -> tuple[CandidateOutcome, bool]:
        """Ein vollstaendiger Kandidat - der Rumpf eines Arbeitsthreads."""
        return _kandidat_verarbeiten(
            candidate=candidate,
            whole_video_words=whole_video_words,
            rendered_windows=rendered_windows,
            mausverfolgung=mausverfolgung,
            rendered_video_path=rendered_video_path,
            avatar_cut_path=avatar_cut_path,
            offsets=offsets,
            derived=derived,
            output_dir=output_dir,
            ffmpeg_path=ffmpeg_path,
            ffprobe_path=ffprobe_path,
            timeout_seconds=timeout_seconds,
            search_window_start_ms=search_window_start_ms,
            stillevorlauf_aktiv=stillevorlauf_aktiv,
            end_min_nachklang_ms=end_min_nachklang_ms,
            wache=wache,
            notizen=notizen,
        )

    ergebnisse: list[tuple[CandidateOutcome, bool]] = []
    if parallel <= 1:
        try:
            for candidate in candidates:
                ergebnisse.append(_verarbeite(candidate))
        except KeyboardInterrupt:
            wache.brich_ab()
            notizen.raeume_unfertige_auf()
            raise
    else:
        with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="shorts-bau") as pool:
            futures: list[Future[tuple[CandidateOutcome, bool]]] = [
                pool.submit(_verarbeite, candidate) for candidate in candidates
            ]
            try:
                ergebnisse = [future.result() for future in futures]
            except KeyboardInterrupt:
                # Nur der Hauptthread bekommt Strg+C - die Arbeitsthreads haengen
                # in ffmpeg. Erst die Prozesse toeten, dann die noch nicht
                # gestarteten Kandidaten streichen und auf die laufenden warten,
                # erst danach aufraeumen (sonst sind Dateien noch offen).
                wache.brich_ab()
                pool.shutdown(wait=True, cancel_futures=True)
                notizen.raeume_unfertige_auf()
                raise

    nach_index = sorted(ergebnisse, key=lambda eintrag: eintrag[0].index)
    outcomes = [outcome for outcome, _ in nach_index]
    excluded_by_scene = [outcome.index for outcome, ausserhalb in nach_index if ausserhalb]
    return excluded_by_scene, outcomes


def _level_snap_payload(snap: LevelSnap | None) -> dict[str, object] | None:
    """Zahlen einer Grenze fuer die Uebersicht - Verschiebung, Pegel, Fenstermittel."""
    if snap is None:
        return None
    return {
        "gerastet_ms": snap.original_ms,
        "korrigiert_ms": snap.corrected_ms,
        "verschiebung_ms": snap.shift_ms,
        "pegel_db": round(snap.level_db, 2),
        "fenstermittel_db": round(snap.window_mean_db, 2),
        "tiefe_unter_mittel_db": round(snap.depth_db, 2),
        "verfahren": snap.verfahren,
        "leiser_bereich_ms": snap.quiet_region_ms,
    }


def _stillevorlauf_payload(stille: StilleVorlauf | None) -> dict[str, object] | None:
    """Stillevorlauf-Pruefung an der Startgrenze fuer die Uebersicht.

    Auftrag shorts-stillevorlauf.
    """
    if stille is None:
        return None
    return {
        "verschoben": stille.verschoben,
        "gerastet_ms": stille.original_ms,
        "korrigiert_ms": stille.corrected_ms,
        "verschiebung_ms": stille.shift_ms,
        "sprechpegel_db": round(stille.sprechpegel_db, 2),
        "stille_laenge_ms": stille.stille_laenge_ms,
        "unterbrechungen_anzahl": stille.unterbrechungen_anzahl,
        "laengste_unterbrechung_ms": stille.laengste_unterbrechung_ms,
    }


def _level_correction_payload(info: LevelCorrectionInfo | None) -> dict[str, object] | None:
    """Pegelkorrektur eines Kandidaten fuer die Uebersicht - Auftrag shorts-pegelschnitt."""
    if info is None:
        return None
    return {
        "angewendet": info.applied,
        "fehler_code": info.fail_code,
        "fehler_de": info.fail_message_de,
        "start": _level_snap_payload(info.start),
        "ende": _level_snap_payload(info.end),
        "stillevorlauf": _stillevorlauf_payload(info.stillevorlauf),
    }


def _framecount_cache_payload(info: FrameCountCacheInfo) -> dict[str, object]:
    """Auskunft ueber eine Framezahl-Seitendatei fuer den Bericht.

    Auftrag shorts-framezahl-seitendatei.
    """
    return {
        "aktiv": info.aktiv,
        "cache_treffer": info.cache_treffer,
        "pfad": str(info.pfad) if info.pfad is not None else None,
        "geschrieben": info.geschrieben,
        "schreibfehler_de": info.schreibfehler_de,
    }


def _mausverfolgung_payload(info: KurvenInfo | None) -> dict[str, object] | None:
    """Was Stufe 3b fuer diesen Kandidaten getan hat.

    Ohne diese Zeilen laesst sich spaeter nicht nachsehen, was passiert ist.
    """
    if info is None:
        return None
    return {
        "grund": info.grund,
        "fahrten": info.fahrten,
        "versatz_anfang": info.versatz_anfang,
        "versatz_ende": info.versatz_ende,
        "naehte": info.naehte,
        "eingefrorene_frames": info.eingefrorene_frames,
    }


def build_report_payload(result: BuildResult) -> dict[str, object]:
    """Baue den JSON-Inhalt der Uebersicht - je Kandidat gebaut oder nicht, mit Grund."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_build",
        "schema_version": BUILD_REPORT_SCHEMA_VERSION,
        "video_name": result.video_name,
        "derived_inputs": {
            "canvas_recording_id": result.derived.canvas_recording_id,
            "avatar_recording_id": result.derived.avatar_recording_id,
            "expected_avatar_frame_count": result.derived.expected_avatar_frame_count,
            "rendered_video_path": str(result.derived.rendered_video_path),
            "rendered_video_dimensions": list(result.derived.rendered_video_dimensions),
            "avatar_frame_count": result.derived.avatar_frame_count,
            "avatar_source_width": result.derived.avatar_source_width,
            "avatar_source_height": result.derived.avatar_source_height,
            "rendered_video_framecount_cache": _framecount_cache_payload(
                result.derived.rendered_video_framecount_cache
            ),
            "avatar_framecount_cache": _framecount_cache_payload(
                result.derived.avatar_framecount_cache
            ),
        },
        "scene_filter": {
            "applied": result.scene_filter.applied,
            "skip_reason": result.scene_filter.skip_reason,
            "journal_path": (
                str(result.scene_filter.journal_path)
                if result.scene_filter.journal_path is not None
                else None
            ),
            "excluded_candidate_indices": list(result.scene_filter.excluded_candidate_indices),
        },
        "summary": {
            "candidate_count": len(result.outcomes),
            "built_count": result.built_count,
            "excluded_by_scene_filter_count": result.excluded_by_scene_filter_count,
            "excluded_by_loop_point_count": result.excluded_by_loop_point_count,
            "achsenabweichung_count": result.achsenabweichung_count,
            "dauer_sekunden": round(result.dauer_sekunden, 3),
            "parallel": result.parallel,
            "derive_inputs_dauer_sekunden": round(result.derive_inputs_dauer_sekunden, 3),
        },
        "arbeitskopie": {
            "aktiv": result.arbeitskopie.aktiv,
            "grund_deaktiviert": result.arbeitskopie.grund_deaktiviert,
            "kopierte_dateien": list(result.arbeitskopie.kopierte_dateien),
            "uebersprungene_dateien": list(result.arbeitskopie.uebersprungene_dateien),
            "kopierdauer_sekunden": round(result.arbeitskopie.kopierdauer_sekunden, 3),
            "fehlgeschlagen": result.arbeitskopie.fehlgeschlagen,
            "fehler_de": result.arbeitskopie.fehler_de,
        },
        "candidates": [
            {
                "index": outcome.index,
                "titel": outcome.titel,
                "status": outcome.status,
                "grund_code": outcome.grund_code,
                "grund_de": outcome.grund_de,
                "schleifen_einstufung": outcome.schleifen_einstufung,
                "build_start_ms": outcome.build_start_ms,
                "build_end_ms": outcome.build_end_ms,
                "output_path": outcome.output_path,
                "pegelkorrektur": _level_correction_payload(outcome.pegelkorrektur),
                "mausverfolgung": _mausverfolgung_payload(outcome.mausverfolgung),
                "achsenabweichung_frames": outcome.achsenabweichung_frames,
                "achsenabweichung_hinweis": outcome.achsenabweichung_hinweis,
            }
            for outcome in result.outcomes
        ],
    }


def write_build_report(path: Path, payload: dict[str, object]) -> None:
    """Schreibe die Uebersicht - kein atomarer Tausch noetig, reine Diagnoseausgabe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ein Video (``shorts-job.json`` + ``kandidaten.json``) zu fertigen Shorts bauen."""
    import argparse

    parser = argparse.ArgumentParser(description="Shorts-Bau: die ganze Kette in einem Aufruf")
    parser.add_argument("job_path", type=Path)
    parser.add_argument("kandidaten_path", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--avatar-cut", type=Path, default=None)
    parser.add_argument("--ausschnitt", type=Path, default=None)
    parser.add_argument("--journal-dir", type=Path, default=None)
    parser.add_argument("--ffmpeg", type=Path, default=None)
    parser.add_argument("--ffprobe", type=Path, default=None)
    parser.add_argument(
        "--parallel",
        type=int,
        default=PARALLEL_DEFAULT,
        help=(
            "wieviele Kandidaten gleichzeitig gebaut werden (Auftrag shorts-bau-parallel); "
            f"Voreinstellung {PARALLEL_DEFAULT}, gemessen. 1 = das serielle Verhalten"
        ),
    )
    parser.add_argument(
        "--keine-arbeitskopie",
        action="store_true",
        help=(
            "Arbeitskopie abschalten (Auftrag shorts-arbeitskopie) - Kandidaten lesen dann "
            "wieder direkt von den Originalpfaden, je Kandidat neu"
        ),
    )
    parser.add_argument(
        "--kein-framecount-cache",
        action="store_true",
        help=(
            "Framezahl-Seitendatei abschalten (Auftrag shorts-framezahl-seitendatei) - "
            "rendered_video/avatar-cut werden dann wieder bei jedem Lauf per "
            "ffprobe -count_frames neu gemessen"
        ),
    )
    parser.add_argument(
        "--kein-stillevorlauf",
        action="store_true",
        help=(
            "Stillevorlauf-Pruefung an der Startgrenze abschalten (Auftrag "
            "shorts-stillevorlauf) - eine Startmarke auf langer Stille vor dem ersten Ton "
            "wird dann nicht mehr vorgeschoben"
        ),
    )
    parser.add_argument(
        "--keine-mausverfolgung",
        action="store_true",
        help=(
            "Mausverfolgung (Stufe 3b) abschalten (Auftrag shorts-3b-verdrahtung) - der "
            "Ausschnitt steht dann wieder fest auf chart_crop.X_OFFSET_DEFAULT, bei sonst "
            "unveraenderten Kandidatenspannen. Der Weg, um beide Fassungen zu vergleichen"
        ),
    )
    args = parser.parse_args(argv)

    ffmpeg_found = args.ffmpeg or discover_ffmpeg()
    if ffmpeg_found is None:
        print("ffmpeg nicht gefunden (PATH pruefen oder --ffmpeg angeben)")
        return 2

    if args.parallel < 1:
        print("ANGEHALTEN [parallel_ungueltig]: --parallel muss mindestens 1 sein")
        return 2

    try:
        result = run_shorts_build(
            job_path=args.job_path,
            kandidaten_path=args.kandidaten_path,
            output_dir=args.output_dir,
            ffmpeg_path=Path(ffmpeg_found),
            avatar_cut_path=args.avatar_cut,
            ausschnitt_path=args.ausschnitt,
            journal_directory=args.journal_dir,
            ffprobe_path=args.ffprobe,
            arbeitskopie_aktiv=not args.keine_arbeitskopie,
            parallel=args.parallel,
            framecount_cache_aktiv=not args.kein_framecount_cache,
            stillevorlauf_aktiv=not args.kein_stillevorlauf,
            mausverfolgung_aktiv=not args.keine_mausverfolgung,
        )
    except KeyboardInterrupt:
        # Die Aufraeumarbeit ist an dieser Stelle schon geschehen: laufende
        # ffmpeg-Prozesse beendet, angefangene Kandidatenordner und die
        # Arbeitskopie geloescht (siehe run_shorts_build).
        print("ABGEBROCHEN (Strg+C) - laufende ffmpeg-Prozesse beendet, nichts liegen gelassen")
        return 130
    if isinstance(result, BuildFailed):
        print(f"ANGEHALTEN [{result.code}]: {result.message_de}")
        return 1

    report_path = args.output_dir / BUILD_REPORT_FILE_NAME
    write_build_report(report_path, build_report_payload(result))
    print(
        f"{result.built_count}/{len(result.outcomes)} Kandidaten gebaut in "
        f"{result.dauer_sekunden:.1f} s (parallel {result.parallel}, davon Vorlauf "
        f"{result.derive_inputs_dauer_sekunden:.1f} s) - Uebersicht: {report_path}"
    )
    for label, cache in (
        ("gerendertes Video", result.derived.rendered_video_framecount_cache),
        ("avatar-cut.mp4", result.derived.avatar_framecount_cache),
    ):
        if not cache.aktiv:
            print(f"  Framezahl-Cache {label}: abgeschaltet")
        elif cache.cache_treffer:
            print(f"  Framezahl-Cache {label}: Treffer ({cache.pfad})")
        elif cache.schreibfehler_de is not None:
            print(
                f"  Framezahl-Cache {label}: neu gemessen, Seitendatei NICHT geschrieben "
                f"({cache.schreibfehler_de})"
            )
        else:
            print(
                f"  Framezahl-Cache {label}: neu gemessen, Seitendatei geschrieben "
                f"({cache.pfad})"
            )
    ak = result.arbeitskopie
    if ak.aktiv:
        print(
            f"  Arbeitskopie: {', '.join(ak.kopierte_dateien)} kopiert in "
            f"{ak.kopierdauer_sekunden:.1f} s"
        )
    elif ak.fehlgeschlagen:
        print(f"  Arbeitskopie fehlgeschlagen ({ak.fehler_de}) - mit Originalpfaden gebaut")
    else:
        print(f"  Arbeitskopie nicht angelegt ({ak.grund_deaktiviert})")
    if result.achsenabweichung_count:
        # Auftrag shorts-achsenpruefung-warnung: die Achsenpruefung ist eine Warnung
        # geworden - damit sie nicht uebersehen wird, steht sie hier in der Zusammenfassung.
        print(
            f"  Achsenabweichung: {result.achsenabweichung_count} von {result.built_count} "
            "gebauten Kandidaten weichen von der erwarteten Videolaenge ab (Details je "
            "Kandidat unten und im Bericht)"
        )
    for outcome in result.outcomes:
        if outcome.status == "gebaut":
            level = outcome.pegelkorrektur
            vermerk = (
                ""
                if level is None or level.applied
                else f" (ohne Pegelkorrektur: {level.fail_code})"
            )
            if outcome.achsenabweichung_frames:
                vermerk += f" (Achsenabweichung {outcome.achsenabweichung_frames:+d} Frame(s))"
            print(f"  [{outcome.index:02d}] gebaut{vermerk}: {outcome.output_path}")
            stille = level.stillevorlauf if level is not None else None
            if stille is not None and stille.verschoben:
                # Auftrag shorts-stillevorlauf: die Verschiebung muss sichtbar
                # sein, sonst merkt niemand, dass sie stattfand.
                print(
                    f"       Stillevorlauf: {stille.original_ms} -> {stille.corrected_ms} ms "
                    f"({stille.shift_ms:+d} ms, {stille.stille_laenge_ms} ms Stille, "
                    f"Sprechpegel {stille.sprechpegel_db:.1f} dB, "
                    f"{stille.unterbrechungen_anzahl} Unterbrechung(en), laengste "
                    f"{stille.laengste_unterbrechung_ms} ms)"
                )
            if level is not None and level.applied:
                # Auftrag shorts-pegelmedian: welches Verfahren griff, und wie
                # lang der gewaehlte leise Bereich war - je Grenze.
                for rolle, snap in (("Start", level.start), ("Ende", level.end)):
                    if snap is None:
                        continue
                    bereich = (
                        f", Bereich {snap.quiet_region_ms} ms"
                        if snap.verfahren == VERFAHREN_BEREICHSMITTE
                        else ""
                    )
                    print(
                        f"       {rolle}: {snap.original_ms} -> {snap.corrected_ms} ms "
                        f"({snap.shift_ms:+d} ms, {snap.verfahren}{bereich})"
                    )
        else:
            print(
                f"  [{outcome.index:02d}] NICHT gebaut ({outcome.grund_code}): "
                f"{outcome.grund_de}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
