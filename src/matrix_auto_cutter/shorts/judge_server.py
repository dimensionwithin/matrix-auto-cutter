r"""Stufe 2, Teil 3: der lokale Urteilsserver - Video statt Ton (Auftrag 22).

Muster übernommen von ``review_app`` (gelesen, nicht geändert, nicht
kopiert): Standardbibliothek-``http.server`` (``ThreadingHTTPServer``),
Bindung ausschließlich an ``127.0.0.1``, ein freier Port statt eines festen,
eine Einzelinstanz-Sperrdatei mit eigenem Namen (``shorts-urteilsserver.lock``
- berührt niemals ``review.lock`` oder ``shorts-tool.lock``), sauberes
Herunterfahren über ``server.shutdown()``/``server.server_close()``.

**Kein allgemeiner Dateiserver.** Der Server kennt genau drei Routen -
``/`` (die erzeugte Seite), ``/video`` (die eine gerenderte Videodatei aus
``shorts-job.json``) und ``/urteile`` (Lesen/Schreiben der Urteile). Jede
andere Anfrage, auch ein Pfadausbruchsversuch wie
``/../../../windows/win.ini``, trifft keinen der drei exakten Routennamen
und bekommt 404 - es gibt keinen Codepfad, der eine Anfrage in einen
Dateisystempfad übersetzt.

**Bereichsanfragen (RFC 7233) sind Pflicht für ``/video``**, sonst lädt
jedes ``<video>``-Element bei jedem Kandidatensprung die ganze, knapp
einstündige Datei von vorn.
"""

from __future__ import annotations

import json
import msvcrt
import os
import tempfile
import threading
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.product_runner import default_state_directory
from matrix_auto_cutter.shorts.candidates import (
    CANDIDATES_FILE_NAME,
    CandidatesSchemaError,
    load_candidates,
)
from matrix_auto_cutter.shorts.judge import (
    DEFAULT_KRITERIEN_PATH,
    JudgeEntry,
    build_judge_entries,
    build_judge_html,
    load_transcript_segments,
    load_transcript_words,
)
from matrix_auto_cutter.shorts.transcript import RENDERED_TRANSCRIPT_FILE_NAME, RENDERED_WAV_NAME

_URTEILE_SCHEMA_VERSION = "1.0"
_URTEILE_VALUES = ("ja", "nein", "spaeter")
_MAX_URTEIL_REQUEST_BYTES = 8 * 1024
_STREAM_CHUNK_BYTES = 1024 * 1024


# ---------------------------------------------------------------------------
# Einzelinstanz-Sperre - eigener Name, siehe Modul-Docstring.
# ---------------------------------------------------------------------------


class JudgeServerSingleInstance:
    """Erlaube höchstens einen Urteilsserver pro Benutzer, eigene Sperrdatei."""

    def __init__(self, path: Path | None = None) -> None:
        """Bereite eine ungehaltene Sperre vor; berührt niemals ``review.lock``."""
        self.path = (
            path if path is not None else default_state_directory() / "shorts-urteilsserver.lock"
        )
        self._descriptor: int | None = None

    def acquire(self) -> bool:
        """Erwerbe die Ein-Byte-Sperre, ohne einen Server zu starten."""
        if os.name != "nt":
            raise RuntimeError("Der Urteilsserver unterstützt nur Windows.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(descriptor)
            return False
        self._descriptor = descriptor
        return True

    def close(self) -> None:
        """Gib die Sperre genau einmal frei."""
        if self._descriptor is None:
            return
        descriptor = self._descriptor
        self._descriptor = None
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)


# ---------------------------------------------------------------------------
# Urteile: atomar geschrieben nach jedem einzelnen Urteil, wie
# ``shorts-job.json`` (``shorts/job.py``). Ein Absturz nach dem fünfzehnten
# Kandidaten darf nicht fünfzehn Urteile kosten.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Urteil:
    """Ein gespeichertes Urteil zu genau einem Kandidaten."""

    index: int
    titel: str
    start_ms: int
    end_ms: int
    ist_kind: bool
    urteil: str | None
    notiz: str


def load_urteile(path: Path) -> dict[int, Urteil]:
    """Lies vorhandene Urteile; jeder Defekt heißt "noch keine Urteile"."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    kandidaten = payload.get("kandidaten") if isinstance(payload, dict) else None
    if not isinstance(kandidaten, dict):
        return {}
    result: dict[int, Urteil] = {}
    for key, value in kandidaten.items():
        if not isinstance(value, dict):
            continue
        try:
            index = int(key)
            start_ms = int(value.get("start_ms", 0))
            end_ms = int(value.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        urteil = value.get("urteil")
        result[index] = Urteil(
            index=index,
            titel=str(value.get("titel", "")),
            start_ms=start_ms,
            end_ms=end_ms,
            ist_kind=bool(value.get("ist_kind", False)),
            urteil=urteil if urteil in _URTEILE_VALUES else None,
            notiz=str(value.get("notiz", "")),
        )
    return result


def write_urteile(path: Path, urteile: Mapping[int, Urteil]) -> None:
    """Schreibe alle Urteile atomar - dasselbe Muster wie ``shorts/job.py``.

    Der Temporärname kommt von :class:`tempfile.NamedTemporaryFile` (eindeutig
    je Aufruf) statt eines festen Namens: ein ``ThreadingHTTPServer`` bedient
    jede Anfrage in einem eigenen Thread, und zwei fast gleichzeitige Urteile
    (z. B. zwei Klicks kurz hintereinander) dürfen nicht dieselbe Temp-Datei
    teilen - sonst überschreiben sich die beiden Schreibvorgänge gegenseitig,
    bevor der atomare Tausch überhaupt beginnt.
    """
    payload = {
        "artifact_type": "matrix_auto_cutter_shorts_urteile",
        "schema_version": _URTEILE_SCHEMA_VERSION,
        "kandidaten": {
            str(item.index): {
                "titel": item.titel,
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "ist_kind": item.ist_kind,
                "urteil": item.urteil,
                "notiz": item.notiz,
            }
            for item in sorted(urteile.values(), key=lambda item: item.index)
        },
    }
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.tmp.", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_atomically(temporary, path, create_only=False)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Sitzungseigene Urteilsdatei (Auftrag shorts-urteilsschutz, Teil A).
#
# Bis hierher schrieb jede Sitzung nach demselben festen Namen
# ``urteile.json`` - am 14.8. überschrieb dadurch ein Werkzeug-Probelauf den
# echten Urteilsstand; gerettet wurde er nur von Hand durch Umbenennen. Ab
# jetzt legt jede Sitzung ihre EIGENE Datei an und übernimmt beim Start den
# Inhalt der jüngsten vorhandenen - die ältere bleibt unangetastet stehen.
# ---------------------------------------------------------------------------


class AmbiguousUrteileStateError(RuntimeError):
    """Der vorhandene Urteilsstand ist nicht eindeutig fortsetzbar."""


def _existing_urteile_files(job_dir: Path) -> list[Path]:
    """Alle vorhandenen Urteilsdateien, alte wie neue Namensform (``urteile*.json``)."""
    return [path for path in job_dir.glob("urteile*.json") if path.is_file()]


def start_session_urteile(
    job_dir: Path,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Path:
    """Lege die sitzungseigene Urteilsdatei an; übernimm den jüngsten vorhandenen Stand.

    Der Server schreibt fortan ausschließlich in die hier erzeugte Datei -
    niemals mehr nach ``urteile.json``. Existieren bereits Urteilsdateien,
    wird der Inhalt der nach Änderungszeit jüngsten in die neue Datei kopiert;
    ältere Dateien bleiben unverändert stehen. Teilen sich mehrere Dateien
    denselben Zeitstempel, oder ist die jüngste nicht lesbar, wird
    :class:`AmbiguousUrteileStateError` ausgelöst statt stillschweigend leer
    zu starten.
    """
    new_path = job_dir / now().strftime("urteile-%Y-%m-%d-%H%M%S.json")
    candidates = _existing_urteile_files(job_dir)
    if not candidates:
        return new_path
    by_mtime: dict[int, list[Path]] = {}
    for path in candidates:
        by_mtime.setdefault(path.stat().st_mtime_ns, []).append(path)
    newest = by_mtime[max(by_mtime)]
    if len(newest) > 1:
        raise AmbiguousUrteileStateError(
            "mehrere Urteilsdateien mit demselben Zeitstempel: "
            + ", ".join(str(path) for path in sorted(newest))
        )
    source = newest[0]
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AmbiguousUrteileStateError(f"jüngste Urteilsdatei nicht lesbar: {source}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("kandidaten"), dict):
        raise AmbiguousUrteileStateError(f"jüngste Urteilsdatei hat unerwartete Form: {source}")
    write_urteile(new_path, load_urteile(source))
    return new_path


# ---------------------------------------------------------------------------
# Bereichsanfragen für ``/video`` (RFC 7233, nur der einfache Ein-Bereich-Fall
# - das ist alles, was ein ``<video>``-Element je anfragt).
# ---------------------------------------------------------------------------


class UnsatisfiableRangeError(ValueError):
    """Der ``Range``-Header ist entweder unlesbar oder passt nicht zur Datei."""


def parse_range(header: str | None, file_size: int) -> tuple[int, int] | None:
    """Lies einen ``Range: bytes=...``-Header; ``None`` heißt "ganze Datei".

    Unterstützt genau eine Form je Anfrage: ``bytes=START-END``,
    ``bytes=START-`` (bis zum Ende) und ``bytes=-LAENGE`` (Suffix, die
    letzten N Bytes) - mehr, als ein ``<video>``-Element je anfragt.
    Mehrfachbereiche (``bytes=0-1,5-6``) werden auf den ersten reduziert.
    """
    if header is None:
        return None
    if not header.startswith("bytes="):
        raise UnsatisfiableRangeError(f"nicht unterstützte Range-Einheit: {header!r}")
    spec = header[len("bytes=") :].split(",")[0].strip()
    if "-" not in spec:
        raise UnsatisfiableRangeError(f"ungültige Range-Angabe: {spec!r}")
    start_text, _, end_text = spec.partition("-")
    try:
        if start_text == "":
            if end_text == "":
                raise UnsatisfiableRangeError("leere Range-Angabe")
            length = int(end_text)
            if length <= 0:
                raise UnsatisfiableRangeError(f"ungültige Suffix-Länge: {length}")
            start = max(0, file_size - length)
            end = file_size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text != "" else file_size - 1
    except ValueError as exc:
        raise UnsatisfiableRangeError(f"Range nicht lesbar: {spec!r}") from exc
    if start < 0 or end < start or start >= file_size:
        raise UnsatisfiableRangeError(f"Range außerhalb der Datei ({file_size} Bytes): {spec!r}")
    return start, min(end, file_size - 1)


# ---------------------------------------------------------------------------
# Der Server selbst
# ---------------------------------------------------------------------------

_KNOWN_PATHS = frozenset({"/", "/video", "/urteile"})


def build_server(
    *,
    html: bytes,
    video_path: Path,
    urteile_path: Path,
    entries: Sequence[JudgeEntry],
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    """Baue den Server; auslieferbar ist ausschließlich, was hier fest übergeben wird."""
    entries_by_index = {entry.index: entry for entry in entries}
    # Serialisiert Lesen-Ändern-Schreiben auf urteile.json: der Server bedient
    # jede Anfrage in einem eigenen Thread, zwei fast gleichzeitige Urteile
    # dürften sich sonst gegenseitig überschreiben (verlorenes Update), auch
    # mit eindeutigem Temp-Dateinamen je Schreibvorgang.
    urteile_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "MatrixAutoCutterUrteilsserver/1.0"

        def log_message(self, _format: str, *_args: object) -> None:
            """Kein Anfrage-Rauschen in der Konsole - lokales Ein-Nutzer-Werkzeug."""

        def _empty_response(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path not in _KNOWN_PATHS:
                self._empty_response(404)
                return
            if path == "/":
                self._serve_html()
            elif path == "/video":
                self._serve_video()
            else:
                self._serve_urteile_get()

        def do_POST(self) -> None:
            if urlsplit(self.path).path != "/urteile":
                self._empty_response(404)
                return
            self._serve_urteile_post()

        def _serve_html(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def _serve_video(self) -> None:
            try:
                file_size = video_path.stat().st_size
            except OSError:
                self._empty_response(404)
                return
            try:
                byte_range = parse_range(self.headers.get("Range"), file_size)
            except UnsatisfiableRangeError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start, end = byte_range if byte_range is not None else (0, file_size - 1)
            length = end - start + 1
            self.send_response(206 if byte_range is not None else 200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if byte_range is not None:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            with video_path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(_STREAM_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except OSError:
                        return
                    remaining -= len(chunk)

        def _serve_urteile_get(self) -> None:
            urteile = load_urteile(urteile_path)
            payload = {
                str(item.index): {"urteil": item.urteil, "notiz": item.notiz}
                for item in urteile.values()
            }
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_urteile_post(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                length = -1
            if length < 1 or length > _MAX_URTEIL_REQUEST_BYTES:
                self._empty_response(413)
                return
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("body")
                index = body["index"]
                if not isinstance(index, int) or isinstance(index, bool):
                    raise ValueError("index")
                urteil = body.get("urteil")
                if urteil is not None and urteil not in _URTEILE_VALUES:
                    raise ValueError("urteil")
                notiz = body.get("notiz", "")
                if not isinstance(notiz, str):
                    raise ValueError("notiz")
            except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
                self._empty_response(400)
                return
            entry = entries_by_index.get(index)
            if entry is None:
                self._empty_response(404)
                return
            with urteile_lock:
                store = load_urteile(urteile_path)
                store[index] = Urteil(
                    index=index,
                    titel=entry.titel,
                    start_ms=entry.start_ms,
                    end_ms=entry.end_ms,
                    ist_kind=entry.is_child,
                    urteil=urteil,
                    notiz=notiz,
                )
                write_urteile(urteile_path, store)
            self._empty_response(204)

    return ThreadingHTTPServer((host, port), Handler)


def server_url(server: ThreadingHTTPServer) -> str:
    """Die Basis-URL, unter der ein gestarteter Server erreichbar ist."""
    return f"http://127.0.0.1:{server.server_address[1]}/"


def shutdown_server(server: ThreadingHTTPServer) -> None:
    """Fahre den Server sauber herunter - derselbe Zweischritt wie ``review_app``."""
    server.shutdown()
    server.server_close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Baue die Urteilsseite, starte den Server, öffne den Standardbrowser."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Shorts Stufe 2, Teil 3: Urteilsserver mit Video starten"
    )
    parser.add_argument("job_path", type=Path, help="Pfad zur shorts-job.json")
    parser.add_argument("--kriterien", type=Path, default=DEFAULT_KRITERIEN_PATH)
    args = parser.parse_args(argv)

    guard = JudgeServerSingleInstance()
    if not guard.acquire():
        print(
            "Urteilsserver läuft bereits (Sperrdatei "
            f"{guard.path} gehalten) - höchstens eine Instanz pro Benutzer."
        )
        return 3
    try:
        job = json.loads(args.job_path.read_text(encoding="utf-8"))
        video_path = Path(job["rendered_video"]["path"])
        if not video_path.is_file():
            print(f"Videodatei nicht gefunden: {video_path}")
            return 2

        job_dir = args.job_path.parent
        candidates_path = job_dir / CANDIDATES_FILE_NAME
        try:
            candidates = load_candidates(candidates_path)
        except CandidatesSchemaError as exc:
            print(f"ANGEHALTEN: {exc}")
            return 1
        except OSError as exc:
            print(f"{candidates_path} nicht lesbar: {exc}")
            return 1

        transcript_segments = load_transcript_segments(job_dir / RENDERED_TRANSCRIPT_FILE_NAME)
        transcript_words = load_transcript_words(job_dir / f"{RENDERED_WAV_NAME}.json")
        kriterien_text = (
            args.kriterien.read_text(encoding="utf-8") if args.kriterien.is_file() else None
        )

        entries = build_judge_entries(
            candidates,
            transcript_segments=transcript_segments,
            transcript_words=transcript_words,
        )
        try:
            urteile_path = start_session_urteile(job_dir)
        except AmbiguousUrteileStateError as exc:
            print(f"ANGEHALTEN: {exc}")
            return 1
        html_bytes = build_judge_html(
            entries, kriterien_text=kriterien_text, urteile_path=urteile_path
        ).encode("utf-8")

        server = build_server(
            html=html_bytes,
            video_path=video_path,
            urteile_path=urteile_path,
            entries=entries,
        )
        url = server_url(server)
        print(
            f"Urteilsserver läuft: {url} ({len(entries)} Kandidaten, Strg+C zum Beenden) - "
            f"Urteile werden gespeichert in {urteile_path}"
        )
        webbrowser.open(url, new=2)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Beende Urteilsserver ...")
        finally:
            shutdown_server(server)
        return 0
    finally:
        guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
