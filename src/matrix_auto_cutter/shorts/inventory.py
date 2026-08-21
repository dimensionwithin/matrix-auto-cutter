"""Reine Bestandsaufnahme der gerenderten Shorts-Quellen.

Kein Zuschnitt, kein Render, keine Transkription - nur das Sammeln und
Zuordnen von Pfaden, die spätere Stufen brauchen werden.  Jede Zuordnung, die
raten muss (Avatardatei über Zeitversatz, Cursorprotokoll über Nähe), sagt es
über ``match_kind`` statt es stillschweigend als Treffer zu verbuchen.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from matrix_auto_cutter.approval import _matches as _approval_matches
from matrix_auto_cutter.approval import _read_approval as _read_approval_artifact
from matrix_auto_cutter.approval import approval_path_for
from matrix_auto_cutter.cut_proposal import ProposalFailed, load_proposal

RENDERED_SUFFIX = ".matrix-cut.mp4"
AVATAR_PREFIX = "AvatarWebcam-"
CURSOR_PREFIX = "cursor-"
_NAME_TIME_FORMAT = "%Y-%m-%d %H-%M-%S"

# Vorgabewerte aus dem Auftrag; jede Zuordnungsfunktion nimmt ihr Verzeichnis
# aber als Parameter, damit Tests eigene Verzeichnisse ohne F:\ benutzen
# können.
DEFAULT_RENDERED_DIR = Path("F:/MatrixMarketAutoEdit/Rendered")
DEFAULT_RAW_DIR = Path("F:/MatrixMarketAutoEdit")
DEFAULT_AVATAR_DIR = Path("F:/ShortsQuellen/Avatar")
DEFAULT_CURSOR_DIR = Path("F:/ShortsQuellen/Cursor")
DEFAULT_DRIVE_ROOT = Path("F:/")

# Suchfenster für die Avatar-Zeitversatz-Zuordnung. Die bekannten Sonderfälle
# liegen bei +1, +8 und +9 Sekunden; 15 s lässt Luft, ohne benachbarte,
# unabhängige Aufnahmen fälschlich zu verbinden. OBS startet die zweite
# Aufnahme systematisch SPÄTER, nie früher - ein negativer Versatz (Avatar vor
# dem Aufnahmebeginn) gehört deshalb nicht ins Fenster, siehe unten in
# ``find_avatar``.
AVATAR_OFFSET_WINDOW_SECONDS = 15
# Suchfenster für die Cursor-Zuordnung: das Cursorprotokoll beginnt vor der
# eigentlichen Aufnahme (im bekannten Fall rund 6 Minuten). Die
# Arbeitsanweisung schreibt vor, den Logger vor OBS zu starten - 30 Minuten
# Vorlauf deckt das mit Rand ab, ohne quer über mehrere Tage hinweg zu
# verbinden.
CURSOR_LEAD_WINDOW_SECONDS = 1800


def parse_name_timestamp(name: str) -> datetime | None:
    """Lies den sekundengenauen Zeitstempel aus einem Dateinamen-Stamm."""
    try:
        return datetime.strptime(name, _NAME_TIME_FORMAT)
    except ValueError:
        return None


def list_rendered_videos(rendered_dir: Path) -> list[Path]:
    """Liefere alle gerenderten Videos, sortiert nach Name."""
    if not rendered_dir.is_dir():
        return []
    return sorted(
        (path for path in rendered_dir.glob(f"*{RENDERED_SUFFIX}") if path.is_file()),
        key=lambda path: path.name,
    )


def video_name(rendered_path: Path) -> str:
    """Leite den gemeinsamen Namensstamm aus dem gerenderten Dateinamen ab."""
    return rendered_path.name[: -len(RENDERED_SUFFIX)]


def raw_video_path(raw_dir: Path, name: str) -> Path:
    """Erwarteter Pfad der Rohaufnahme zu einem Namensstamm."""
    return raw_dir / f"{name}.mp4"


def sidecar_path(raw_dir: Path, name: str) -> Path:
    """Erwarteter Pfad des OBS-Sidecars zu einem Namensstamm."""
    return raw_dir / f"{name}.obs-events.json"


@dataclass(frozen=True, slots=True)
class AvatarMatch:
    """Ergebnis der Avatardatei-Zuordnung, mit sichtbarer Unsicherheit."""

    path: Path | None
    match_kind: Literal["exact", "offset_guess", "root_fallback", "none"]
    offset_seconds: int | None = None


def find_avatar(
    name: str,
    avatar_dir: Path,
    drive_root: Path,
    *,
    offset_window_seconds: int = AVATAR_OFFSET_WINDOW_SECONDS,
) -> AvatarMatch:
    """Erkenne die Avatardatei: namensgleich, sonst über Zeitversatz, sonst Root."""
    exact = avatar_dir / f"{AVATAR_PREFIX}{name}.mp4"
    if exact.is_file():
        return AvatarMatch(exact, "exact")

    timestamp = parse_name_timestamp(name)
    if timestamp is not None and avatar_dir.is_dir():
        # Alle Kandidaten im Fenster sammeln statt nur den naechstliegenden zu
        # merken: mehrere Treffer heissen "ungeklaert", nicht "den naechsten
        # raten".
        in_window: list[tuple[int, Path]] = []
        for candidate in avatar_dir.glob(f"{AVATAR_PREFIX}*.mp4"):
            candidate_name = candidate.name[len(AVATAR_PREFIX) : -len(".mp4")]
            candidate_timestamp = parse_name_timestamp(candidate_name)
            if candidate_timestamp is None:
                continue
            delta = int((candidate_timestamp - timestamp).total_seconds())
            if 0 <= delta <= offset_window_seconds:
                in_window.append((delta, candidate))
        if len(in_window) == 1:
            delta, path = in_window[0]
            return AvatarMatch(path, "offset_guess", delta)

    root_fallback = drive_root / f"{AVATAR_PREFIX}{name}.mp4"
    if root_fallback.is_file():
        return AvatarMatch(root_fallback, "root_fallback")
    return AvatarMatch(None, "none")


@dataclass(frozen=True, slots=True)
class CursorMatch:
    """Ergebnis der Cursorprotokoll-Zuordnung, mit sichtbarer Unsicherheit."""

    path: Path | None
    match_kind: Literal["sidecar", "matched_guess", "none"]
    lead_seconds: float | None = None


def _sidecar_json_path(csv_path: Path) -> Path:
    """Pfad der zur CSV gleichnamigen Seitendatei des Waechters."""
    return csv_path.with_suffix(".json")


def _find_cursor_via_sidecar(name: str, cursor_dir: Path) -> CursorMatch | None:
    """Eindeutige Zuordnung über die Waechter-Seitendatei, wenn vorhanden.

    Die Seitendatei trägt ``obs_output_path`` - den tatsächlichen Pfad der
    Aufnahme, die dieses Cursorprotokoll begleitet hat. Stimmt ihr Dateistamm
    mit ``name`` überein, ist das der Treffer; keine Zeitrechnung, kein
    Fenster, kein Raten.
    """
    for candidate in sorted(cursor_dir.glob(f"{CURSOR_PREFIX}*.csv")):
        sidecar = _sidecar_json_path(candidate)
        if not sidecar.is_file():
            continue
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        output_path = payload.get("obs_output_path")
        if not isinstance(output_path, str) or not output_path:
            continue
        if Path(output_path).stem != name:
            continue
        lead_seconds = payload.get("lead_seconds")
        if not isinstance(lead_seconds, int | float):
            lead_seconds = None
        return CursorMatch(candidate, "sidecar", lead_seconds)
    return None


def _first_cursor_row_timestamp(csv_path: Path) -> datetime | None:
    """Lies den Zeitstempel der ersten Datenzeile - nur sie ist eine Messung.

    Der Dateiname trägt den Zeitpunkt der Dateierzeugung, nicht den der ersten
    Abfrage; die Anlaufzeit des Loggers dazwischen lag im bekannten Fall bei
    rund 9 s (siehe Auftrag 04, Eingriff 3). Lässt sich die Zeile nicht lesen
    oder parsen, gibt es dafür keinen geschätzten Wert.
    """
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as stream:
            row = next(csv.DictReader(stream), None)
    except (OSError, UnicodeError, csv.Error):
        return None
    if row is None:
        return None
    raw = row.get("zeit")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def find_cursor(
    name: str,
    cursor_dir: Path,
    *,
    max_lead_seconds: int = CURSOR_LEAD_WINDOW_SECONDS,
) -> CursorMatch:
    """Erkenne das nächstgelegene, vor der Aufnahme gestartete Cursorprotokoll."""
    if not cursor_dir.is_dir():
        return CursorMatch(None, "none")
    sidecar_match = _find_cursor_via_sidecar(name, cursor_dir)
    if sidecar_match is not None:
        return sidecar_match
    timestamp = parse_name_timestamp(name)
    if timestamp is None:
        return CursorMatch(None, "none")
    best_lead: int | None = None
    best_path: Path | None = None
    for candidate in cursor_dir.glob(f"{CURSOR_PREFIX}*.csv"):
        candidate_name = candidate.name[len(CURSOR_PREFIX) : -len(".csv")]
        candidate_timestamp = parse_name_timestamp(candidate_name)
        if candidate_timestamp is None:
            continue
        lead = int((timestamp - candidate_timestamp).total_seconds())
        if 0 <= lead <= max_lead_seconds and (best_lead is None or lead < best_lead):
            best_lead, best_path = lead, candidate
    if best_path is None:
        return CursorMatch(None, "none")
    row_timestamp = _first_cursor_row_timestamp(best_path)
    lead_seconds = None
    if row_timestamp is not None:
        lead_seconds = round((timestamp - row_timestamp.replace(tzinfo=None)).total_seconds())
    return CursorMatch(best_path, "matched_guess", lead_seconds)


def _canonical(path_text: str) -> str:
    return str(Path(path_text)).casefold()


def find_recording_id(
    raw_path: Path,
    rendered_path: Path,
    sessions_dir: Path,
) -> str | None:
    """Erkenne die recording_id über source_path/render_target_path der Sessions.

    Die Zeitstempel der Session-JSONs sind UTC, die Dateinamen dagegen
    Ortszeit; deshalb wird ausschließlich über den Pfad verglichen, nie über
    ``updated_at``.
    """
    if not sessions_dir.is_dir():
        return None
    raw_key = _canonical(str(raw_path))
    rendered_key = _canonical(str(rendered_path))
    matches: set[str] = set()
    for session_file in sorted(sessions_dir.glob("*.json")):
        try:
            payload = json.loads(session_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        source = payload.get("source_path")
        target = payload.get("render_target_path")
        if (isinstance(source, str) and _canonical(source) == raw_key) or (
            isinstance(target, str) and _canonical(target) == rendered_key
        ):
            matches.add(session_file.stem)
    if not matches:
        return None
    return sorted(matches)[0]


@dataclass(frozen=True, slots=True)
class ProposalMatch:
    """Ergebnis der Proposal-Zuordnung: nur freigegebene, digestgebundene Kandidaten zählen.

    ``candidate_count``/``ambiguous`` beziehen sich auf die Zahl der
    freigegebenen Kandidaten, nicht auf alle vorhandenen Proposal-Verzeichnisse.
    ``unclear`` (``"ungeklärt"``) heißt: es gab Proposal-Kandidaten, aber keiner
    davon konnte eindeutig als freigegeben und zeitlich geordnet bestimmt
    werden - das ist ein eigener Zustand, kein stillschweigendes "jüngstes".
    """

    recording_id: str | None
    proposal_path: Path | None
    schema_version: str | None
    candidate_count: int
    ambiguous: bool
    unclear: bool = False


_NO_PROPOSAL = ProposalMatch(
    recording_id=None,
    proposal_path=None,
    schema_version=None,
    candidate_count=0,
    ambiguous=False,
    unclear=False,
)


def _parse_generated_at(proposal_file: Path) -> datetime | None:
    """Lies ``generated_at`` als zeitzonenbewussten Zeitpunkt; kein Rateergebnis.

    Lexikalische Sortierung von ISO-8601-Zeitstempeln geht nur gut, solange
    alle dasselbe Format und denselben Zonenversatz tragen. Ein ``Z``, ein
    naives Datum oder ein anderer Versatz sortiert sonst still falsch.
    """
    try:
        payload = json.loads(proposal_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        return None
    try:
        parsed = datetime.fromisoformat(generated_at)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


@dataclass(frozen=True, slots=True)
class _ApprovedCandidate:
    """Ein Proposal-Kandidat mit gültiger Freigabe und intakter Digestbindung."""

    proposal_path: Path
    generated_at: datetime | None
    schema_version: str | None


def _approved_candidate(proposal_file: Path) -> _ApprovedCandidate | None:
    """Prüfe Freigabe und Digestbindung über ``approval.py``, nicht nachgebaut."""
    loaded = load_proposal(proposal_file)
    if isinstance(loaded, ProposalFailed):
        return None
    approval = _read_approval_artifact(approval_path_for(proposal_file))
    if approval is None or approval.decision not in {"approved", "selected_cuts_approved"}:
        return None
    if not _approval_matches(approval, loaded):
        return None
    return _ApprovedCandidate(
        proposal_path=proposal_file,
        generated_at=_parse_generated_at(proposal_file),
        schema_version=loaded.proposal.schema_version,
    )


def find_proposal(recording_id: str | None, artifacts_dir: Path) -> ProposalMatch:
    """Erkenne das jüngste freigegebene Proposal einer recording_id.

    Nur Kandidaten mit ``decision in {"approved", "selected_cuts_approved"}``
    und gültiger Digestbindung (geprüft über :mod:`matrix_auto_cutter.approval`)
    zählen. Bleibt keiner übrig oder lässt sich die Reihenfolge unter den
    verbliebenen nicht zweifelsfrei bestimmen, ist das Ergebnis ``unclear``.
    """
    if recording_id is None:
        return _NO_PROPOSAL
    proposals_dir = artifacts_dir / recording_id / "proposals"
    if not proposals_dir.is_dir():
        return ProposalMatch(
            recording_id=recording_id,
            proposal_path=None,
            schema_version=None,
            candidate_count=0,
            ambiguous=False,
            unclear=False,
        )
    proposal_files = [
        entry / "cut-proposal.json"
        for entry in proposals_dir.iterdir()
        if entry.is_dir() and (entry / "cut-proposal.json").is_file()
    ]
    if not proposal_files:
        return ProposalMatch(
            recording_id=recording_id,
            proposal_path=None,
            schema_version=None,
            candidate_count=0,
            ambiguous=False,
            unclear=False,
        )
    approved = [
        candidate
        for candidate in (_approved_candidate(proposal_file) for proposal_file in proposal_files)
        if candidate is not None
    ]
    if not approved or any(candidate.generated_at is None for candidate in approved):
        return ProposalMatch(
            recording_id=recording_id,
            proposal_path=None,
            schema_version=None,
            candidate_count=len(approved),
            ambiguous=len(approved) > 1,
            unclear=True,
        )
    timestamped = [
        (candidate.generated_at, candidate)
        for candidate in approved
        if candidate.generated_at is not None
    ]
    newest = max(timestamped, key=lambda item: item[0])[1]
    return ProposalMatch(
        recording_id=recording_id,
        proposal_path=newest.proposal_path,
        schema_version=newest.schema_version,
        candidate_count=len(approved),
        ambiguous=len(approved) > 1,
        unclear=False,
    )


def discover_ffprobe() -> Path | None:
    """Erkenne ``ffprobe`` über den PATH; kein Fund ist kein Fehler."""
    found = shutil.which("ffprobe")
    return Path(found) if found else None


def probe_duration_ms(video_path: Path, ffprobe_path: Path | None = None) -> int | None:
    """Lies die Dauer einer Videodatei über ``ffprobe``; ``None`` bei Fehlern."""
    ffprobe = ffprobe_path if ffprobe_path is not None else discover_ffprobe()
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                str(ffprobe),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = result.stdout.decode("utf-8", errors="ignore").strip()
    try:
        seconds = float(output)
    except ValueError:
        return None
    return round(seconds * 1000)


@dataclass(frozen=True, slots=True)
class VideoRow:
    """Eine Zeile der Liste: ein gerendertes Video mit seiner Datenlage."""

    name: str
    rendered_path: Path
    duration_ms: int | None
    raw_path: Path
    raw_exists: bool
    sidecar_path: Path
    sidecar_exists: bool
    proposal: ProposalMatch
    avatar: AvatarMatch
    cursor: CursorMatch


def build_inventory(
    *,
    rendered_dir: Path = DEFAULT_RENDERED_DIR,
    raw_dir: Path = DEFAULT_RAW_DIR,
    avatar_dir: Path = DEFAULT_AVATAR_DIR,
    cursor_dir: Path = DEFAULT_CURSOR_DIR,
    drive_root: Path = DEFAULT_DRIVE_ROOT,
    sessions_dir: Path,
    artifacts_dir: Path,
    probe_duration: bool = True,
) -> list[VideoRow]:
    """Baue die vollständige Zeilenliste für alle gerenderten Videos."""
    rows: list[VideoRow] = []
    for rendered_path in list_rendered_videos(rendered_dir):
        name = video_name(rendered_path)
        raw_path = raw_video_path(raw_dir, name)
        sidecar = sidecar_path(raw_dir, name)
        recording_id = find_recording_id(raw_path, rendered_path, sessions_dir)
        proposal = find_proposal(recording_id, artifacts_dir)
        avatar = find_avatar(name, avatar_dir, drive_root)
        cursor = find_cursor(name, cursor_dir)
        duration_ms = probe_duration_ms(rendered_path) if probe_duration else None
        rows.append(
            VideoRow(
                name=name,
                rendered_path=rendered_path,
                duration_ms=duration_ms,
                raw_path=raw_path,
                raw_exists=raw_path.is_file(),
                sidecar_path=sidecar,
                sidecar_exists=sidecar.is_file(),
                proposal=proposal,
                avatar=avatar,
                cursor=cursor,
            )
        )
    return rows
