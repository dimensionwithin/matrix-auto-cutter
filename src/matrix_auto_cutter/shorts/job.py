"""Atomarer Export von ``shorts-job.json`` - der einzige Knopf dieser Stufe.

Der Knopf startet nichts, rendert nichts, ruft kein ffmpeg und kein whisper.
Er schreibt nur die gesammelten Pfade und Befunde aus
:mod:`matrix_auto_cutter.shorts.inventory` in eine nachprüfbare Datei.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.shorts.inventory import VideoRow

JOB_FILE_NAME = "shorts-job.json"
JOB_SCHEMA_VERSION = "0.2"


def job_output_path(jobs_root: Path, video_name: str) -> Path:
    """Zielpfad der Auftragsdatei zu einem Video."""
    return jobs_root / video_name / JOB_FILE_NAME


def build_job_payload(row: VideoRow, *, created_at: str) -> dict[str, object]:
    """Baue den JSON-Inhalt aus einer bereits ermittelten Inventarzeile."""
    return {
        "artifact_type": "matrix_auto_cutter_shorts_job",
        "schema_version": JOB_SCHEMA_VERSION,
        "video_name": row.name,
        "created_at": created_at,
        "rendered_video": {
            "path": str(row.rendered_path),
            "duration_ms": row.duration_ms,
        },
        "raw_recording": {
            "path": str(row.raw_path),
            "exists": row.raw_exists,
        },
        "sidecar": {
            "path": str(row.sidecar_path),
            "exists": row.sidecar_exists,
        },
        "proposal": {
            "recording_id": row.proposal.recording_id,
            "path": str(row.proposal.proposal_path) if row.proposal.proposal_path else None,
            "schema_version": row.proposal.schema_version,
            "candidate_count": row.proposal.candidate_count,
            "ambiguous": row.proposal.ambiguous,
            "unclear": row.proposal.unclear,
        },
        "avatar": {
            "path": str(row.avatar.path) if row.avatar.path else None,
            "match_kind": row.avatar.match_kind,
            "offset_seconds": row.avatar.offset_seconds,
        },
        "cursor_log": {
            "path": str(row.cursor.path) if row.cursor.path else None,
            "match_kind": row.cursor.match_kind,
            "lead_seconds": row.cursor.lead_seconds,
        },
    }


def write_job(path: Path, payload: dict[str, object], *, overwrite: bool) -> None:
    """Schreibe die Auftragsdatei atomar; ohne ``overwrite`` bleibt sie einmalig.

    Nutzt dasselbe Muster wie ``cut_proposal.py``/``atomic.py``: Temporärdatei
    im Zielverzeichnis, flush plus fsync, dann atomarer Tausch. Existiert die
    Datei schon und ``overwrite`` ist falsch, hebt ``os.rename`` (über
    ``create_only``) unverändert ``FileExistsError``; der Aufrufer entscheidet
    davor per Rückfrage, ob überschrieben werden soll.
    """
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
        replace_atomically(temporary, path, create_only=not overwrite)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
