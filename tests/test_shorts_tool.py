"""Tests für das Shorts-Werkzeug Stufe 0: Zuordnung, Liste, Auftragsdatei.

Deckt gezielt die bekannten Sonderfälle aus dem Tonabgleichsbericht ab: drei
Videos mit Zeitversatz im Avatar-Dateinamen, ein Video mit Avatardatei
außerhalb des Namensmusters (Root-Pfad), ein Video mit Cursorprotokoll, und
eine recording_id mit zwei Proposals.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from matrix_auto_cutter.approval import DecisionWritten, ensure_pending_approval, record_decision
from matrix_auto_cutter.cut_proposal import FfmpegProcessResult, ProposalReady, generate_proposal
from matrix_auto_cutter.shorts import inventory as inv
from matrix_auto_cutter.shorts import job as job_module
from matrix_auto_cutter.shorts.inventory import (
    AvatarMatch,
    CursorMatch,
    ProposalMatch,
    VideoRow,
    build_inventory,
    find_avatar,
    find_cursor,
    find_proposal,
    find_recording_id,
    list_rendered_videos,
    parse_name_timestamp,
    probe_duration_ms,
    raw_video_path,
    sidecar_path,
    video_name,
)
from matrix_auto_cutter.shorts.job import build_job_payload, job_output_path, write_job


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_ffmpeg_process(silence: bytes = b"") -> Callable[..., FfmpegProcessResult]:
    """Fälsche den ffmpeg-Prozess: nur ``-version`` und ein Stille-Ergebnis."""

    def runner(arguments: object, timeout: int) -> FfmpegProcessResult:
        del timeout
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version test-build\n")
        return FfmpegProcessResult(0, silence)

    return runner


def _generate_test_proposal(
    work_dir: Path,
    raw_sidecar: dict[str, Any],
    artifacts_dir: Path,
    *,
    seed: bytes,
    generated_at: datetime,
    recording_id: str | None = None,
) -> ProposalReady:
    """Erzeuge ein echtes, digestgültiges Proposal - über generate_proposal, nicht handgebaut."""
    work_dir.mkdir(parents=True, exist_ok=True)
    source = work_dir / "source.mp4"
    source.write_bytes(b"source-bytes-" + seed)
    payload = deepcopy(raw_sidecar)
    if recording_id is not None:
        payload["recording_session_id"] = recording_id
    source_identity = payload["source"]
    assert isinstance(source_identity, dict)
    source_identity.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_text(json.dumps(payload), encoding="utf-8")
    ffmpeg = work_dir / "ffmpeg.exe"
    ffmpeg.write_bytes(b"fake-ffmpeg-binary")
    result = generate_proposal(
        source,
        sidecar,
        str(payload["recording_session_id"]),
        artifacts_dir,
        ffmpeg,
        process_runner=_fake_ffmpeg_process(),
        now=lambda: generated_at,
    )
    assert isinstance(result, ProposalReady), result
    return result


def _approved_proposal(
    work_dir: Path,
    raw_sidecar: dict[str, Any],
    artifacts_dir: Path,
    *,
    seed: bytes,
    generated_at: datetime,
    recording_id: str | None = None,
) -> ProposalReady:
    """Erzeuge ein Proposal und gib ihm über ``approval.py`` eine echte Freigabe."""
    ready = _generate_test_proposal(
        work_dir, raw_sidecar, artifacts_dir, seed=seed, generated_at=generated_at,
        recording_id=recording_id,
    )
    outcome = record_decision(ready.proposal_path, "approved", now=lambda: generated_at)
    assert isinstance(outcome, DecisionWritten), outcome
    return ready


def _pending_proposal(
    work_dir: Path,
    raw_sidecar: dict[str, Any],
    artifacts_dir: Path,
    *,
    seed: bytes,
    generated_at: datetime,
    recording_id: str | None = None,
) -> ProposalReady:
    """Erzeuge ein Proposal, das im Startzustand ``pending`` bleibt - keine Entscheidung."""
    ready = _generate_test_proposal(
        work_dir, raw_sidecar, artifacts_dir, seed=seed, generated_at=generated_at,
        recording_id=recording_id,
    )
    outcome = ensure_pending_approval(ready.proposal_path, now=lambda: generated_at)
    assert isinstance(outcome, DecisionWritten), outcome
    return ready


# --- Namensstamm und Zeitparsing -------------------------------------------------


def test_video_name_strips_rendered_suffix() -> None:
    assert video_name(Path("2026-08-09 15-10-19.matrix-cut.mp4")) == "2026-08-09 15-10-19"


def test_parse_name_timestamp_valid() -> None:
    timestamp = parse_name_timestamp("2026-08-09 15-10-19")
    assert timestamp is not None
    assert (timestamp.year, timestamp.month, timestamp.day) == (2026, 8, 9)
    assert (timestamp.hour, timestamp.minute, timestamp.second) == (15, 10, 19)


def test_parse_name_timestamp_invalid() -> None:
    assert parse_name_timestamp("not-a-timestamp") is None
    assert parse_name_timestamp("AvatarWebcam-2026-08-09 15-10-19") is None


# --- Liste der gerenderten Videos --------------------------------------------------


def test_list_rendered_videos_ignores_partial_and_upload_files(tmp_path: Path) -> None:
    rendered = tmp_path / "Rendered"
    _touch(rendered / "2026-08-09 15-10-19.matrix-cut.mp4")
    _touch(rendered / "2026-08-07 11-35-16.matrix-cut.mp4")
    _touch(rendered / "2026-08-07 11-35-16.upload.mp4")
    _touch(
        rendered
        / "2026-08-09 16-50-21.matrix-cut.render-attempt-efe1a0f266.h264_nvenc.partial.mp4"
    )
    names = [video_name(path) for path in list_rendered_videos(rendered)]
    assert names == ["2026-08-07 11-35-16", "2026-08-09 15-10-19"]


def test_list_rendered_videos_missing_directory(tmp_path: Path) -> None:
    assert list_rendered_videos(tmp_path / "does-not-exist") == []


def test_raw_and_sidecar_paths(tmp_path: Path) -> None:
    assert raw_video_path(tmp_path, "2026-08-09 15-10-19") == tmp_path / "2026-08-09 15-10-19.mp4"
    assert sidecar_path(tmp_path, "2026-08-09 15-10-19") == (
        tmp_path / "2026-08-09 15-10-19.obs-events.json"
    )


# --- Avatar-Zuordnung: exakt, Zeitversatz, Root-Sonderfall, keine ------------------


def test_find_avatar_exact_match(tmp_path: Path) -> None:
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-2026-08-07 11-35-16.mp4")
    match = find_avatar("2026-08-07 11-35-16", avatar_dir, tmp_path / "drive-root")
    assert match == AvatarMatch(
        avatar_dir / "AvatarWebcam-2026-08-07 11-35-16.mp4", "exact", None
    )


@pytest.mark.parametrize(
    ("video_name_value", "avatar_suffix", "expected_offset"),
    [
        ("2026-08-09 07-25-37", "2026-08-09 07-25-46", 9),
        ("2026-08-09 07-29-51", "2026-08-09 07-29-52", 1),
        ("2026-08-09 12-09-50", "2026-08-09 12-09-58", 8),
    ],
)
def test_find_avatar_offset_guess_matches_known_cases(
    tmp_path: Path, video_name_value: str, avatar_suffix: str, expected_offset: int
) -> None:
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / f"AvatarWebcam-{avatar_suffix}.mp4")
    match = find_avatar(video_name_value, avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "offset_guess"
    assert match.offset_seconds == expected_offset
    assert match.path == avatar_dir / f"AvatarWebcam-{avatar_suffix}.mp4"


def test_find_avatar_root_fallback_for_out_of_pattern_case(tmp_path: Path) -> None:
    avatar_dir = tmp_path / "Avatar"
    avatar_dir.mkdir(parents=True)
    drive_root = tmp_path / "drive-root"
    _touch(drive_root / "AvatarWebcam-2026-08-04 01-11-36.mp4")
    match = find_avatar("2026-08-04 01-11-36", avatar_dir, drive_root)
    assert match.match_kind == "root_fallback"
    assert match.path == drive_root / "AvatarWebcam-2026-08-04 01-11-36.mp4"


def test_find_avatar_none_when_nothing_matches(tmp_path: Path) -> None:
    avatar_dir = tmp_path / "Avatar"
    avatar_dir.mkdir(parents=True)
    match = find_avatar("2026-08-09 08-43-22", avatar_dir, tmp_path / "drive-root")
    assert match == AvatarMatch(None, "none", None)


def test_find_avatar_ignores_unparseable_candidate_names(tmp_path: Path) -> None:
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-not-a-timestamp.mp4")
    match = find_avatar("2026-08-09 08-43-22", avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "none"


def test_find_avatar_offset_outside_window_is_not_matched(tmp_path: Path) -> None:
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-14-50.mp4")
    match = find_avatar("2026-08-09 08-14-05", avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "none"


# --- Cursorprotokoll-Zuordnung: der eine Treffer, sonst keiner --------------------


def test_find_cursor_matches_the_one_known_pair(tmp_path: Path) -> None:
    """Vorlauf gegen die erste Datenzeile (~367,9 s), nicht gegen den Dateinamen (377 s).

    Reale Werte aus ``F:\\ShortsQuellen\\Cursor\\cursor-2026-08-07 11-28-59.csv``: der
    Logger läuft rund 9 s zwischen Dateierzeugung und erster Abfrage an.
    """
    cursor_dir = tmp_path / "Cursor"
    _touch(
        cursor_dir / "cursor-2026-08-07 11-28-59.csv",
        "zeit,x,y\n2026-08-07T11:29:08.0642210+02:00,-792,367\n",
    )
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "matched_guess"
    assert match.path == cursor_dir / "cursor-2026-08-07 11-28-59.csv"
    filename_lead = 6 * 60 + 17
    assert match.lead_seconds != filename_lead
    assert match.lead_seconds == 368


def test_find_cursor_lead_seconds_is_none_when_first_row_unreadable(tmp_path: Path) -> None:
    """Ist die Messung nicht lesbar, gibt es dafür keinen geschätzten Wert."""
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-07 11-28-59.csv", "zeit,x,y\n")
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "matched_guess"
    assert match.lead_seconds is None


def test_find_cursor_no_match_for_unrelated_video(tmp_path: Path) -> None:
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-07 11-28-59.csv")
    match = find_cursor("2026-08-09 15-10-19", cursor_dir)
    assert match == CursorMatch(None, "none", None)


def test_find_cursor_rejects_log_started_after_the_recording(tmp_path: Path) -> None:
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-07 12-00-00.csv")
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "none"


def test_find_cursor_missing_directory(tmp_path: Path) -> None:
    assert find_cursor("2026-08-07 11-35-16", tmp_path / "no-such-dir") == CursorMatch(
        None, "none", None
    )


def test_find_cursor_uses_sidecar_when_present(tmp_path: Path) -> None:
    """Eine Seitendatei entscheidet über den Dateistamm von obs_output_path - kein Raten."""
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-17 19-45-36.csv", "zeit,x,y\n")
    _touch(
        cursor_dir / "cursor-2026-08-17 19-45-36.json",
        json.dumps(
            {
                "recording_started_at": "2026-08-17T19:45:36.7261941+02:00",
                "csv_first_row_at": "2026-08-17T19:45:36.8888058+02:00",
                "lead_seconds": -0.1626117,
                "rows": 555,
                "obs_output_path": "F:/MatrixMarketAutoEdit/2026-08-17 19-45-36.mp4",
            }
        ),
    )
    match = find_cursor("2026-08-17 19-45-36", cursor_dir)
    assert match.match_kind == "sidecar"
    assert match.path == cursor_dir / "cursor-2026-08-17 19-45-36.csv"
    assert match.lead_seconds == pytest.approx(-0.1626117)


def test_find_cursor_sidecar_ignored_for_other_video_name(tmp_path: Path) -> None:
    """Eine Seitendatei, die auf ein anderes Video zeigt, ist kein Treffer für dieses."""
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-17 19-45-36.csv", "zeit,x,y\n")
    _touch(
        cursor_dir / "cursor-2026-08-17 19-45-36.json",
        json.dumps({"obs_output_path": "F:/MatrixMarketAutoEdit/2026-08-17 19-45-36.mp4"}),
    )
    match = find_cursor("2026-08-09 15-10-19", cursor_dir)
    assert match == CursorMatch(None, "none", None)


def test_find_cursor_without_sidecar_still_uses_time_window(tmp_path: Path) -> None:
    """Pruefstein: das Protokoll vom 7.8. ohne Seitendatei bleibt unveraendert zugeordnet."""
    cursor_dir = tmp_path / "Cursor"
    _touch(
        cursor_dir / "cursor-2026-08-07 11-28-59.csv",
        "zeit,x,y\n2026-08-07T11:29:08.0642210+02:00,-792,367\n",
    )
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "matched_guess"
    assert match.path == cursor_dir / "cursor-2026-08-07 11-28-59.csv"
    assert match.lead_seconds == 368


# --- Sessions -> recording_id, UTC-Fallen umgehen ----------------------------------


def _write_session(sessions_dir: Path, recording_id: str, **fields: object) -> None:
    payload = {"recording_session_id": recording_id, **fields}
    _touch(sessions_dir / f"{recording_id}.json", json.dumps(payload))


def test_find_recording_id_matches_via_source_path(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    raw = Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.mp4")
    rendered = Path("F:/MatrixMarketAutoEdit/Rendered/2026-08-09 15-10-19.matrix-cut.mp4")
    _write_session(
        sessions_dir,
        "114a7c6e-d730-4e12-ba71-f6d1a6baaa0d",
        source_path=str(raw),
        render_target_path=None,
        updated_at="2026-08-09T13:11:55.624969+00:00",
    )
    assert find_recording_id(raw, rendered, sessions_dir) == "114a7c6e-d730-4e12-ba71-f6d1a6baaa0d"


def test_find_recording_id_matches_via_render_target_path(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    raw = Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.mp4")
    rendered = Path("F:/MatrixMarketAutoEdit/Rendered/2026-08-09 15-10-19.matrix-cut.mp4")
    _write_session(
        sessions_dir,
        "114a7c6e-d730-4e12-ba71-f6d1a6baaa0d",
        source_path=None,
        render_target_path=str(rendered),
    )
    assert find_recording_id(raw, rendered, sessions_dir) == "114a7c6e-d730-4e12-ba71-f6d1a6baaa0d"


def test_find_recording_id_ignores_malformed_session_files(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    _touch(sessions_dir / "broken.json", "{not json")
    _touch(sessions_dir / "not-a-dict.json", "[1, 2, 3]")
    raw = Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.mp4")
    rendered = Path("F:/MatrixMarketAutoEdit/Rendered/2026-08-09 15-10-19.matrix-cut.mp4")
    assert find_recording_id(raw, rendered, sessions_dir) is None


def test_find_recording_id_no_match(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions"
    _write_session(sessions_dir, "some-id", source_path="F:/other.mp4", render_target_path=None)
    raw = Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.mp4")
    rendered = Path("F:/MatrixMarketAutoEdit/Rendered/2026-08-09 15-10-19.matrix-cut.mp4")
    assert find_recording_id(raw, rendered, sessions_dir) is None


def test_find_recording_id_missing_directory(tmp_path: Path) -> None:
    raw = Path("F:/x.mp4")
    rendered = Path("F:/Rendered/x.matrix-cut.mp4")
    assert find_recording_id(raw, rendered, tmp_path / "no-sessions") is None


# --- Proposal-Zuordnung: nur freigegebene, digestgebundene Kandidaten --------------
#
# `find_proposal()` wählte früher schlicht das jüngste vorhandene Proposal. Belegt in
# `LAG-UND-PROPOSAL-2026-08-10.md` Abschnitt B.3 hätte das bei zwei realen recording_ids
# anstandslos ein niemals freigegebenes Proposal gewählt. Die Tests unten bauen deshalb
# echte, digestgültige Proposals über `generate_proposal()` und geben ihnen über
# `approval.py` eine echte Entscheidung - nicht handgestrickte JSON-Fragmente, die die
# Digestprüfung ohnehin nie bestehen würden.


def test_find_proposal_single_approved_candidate(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    recording_id = str(uuid4())
    ready = _approved_proposal(
        tmp_path / "src-a",
        raw_sidecar,
        artifacts_dir,
        seed=b"single",
        generated_at=datetime(2026, 8, 9, 13, 11, 14, tzinfo=UTC),
        recording_id=recording_id,
    )
    match = find_proposal(recording_id, artifacts_dir)
    assert match.recording_id == recording_id
    assert match.schema_version == ready.proposal.schema_version
    assert match.candidate_count == 1
    assert not match.ambiguous
    assert not match.unclear
    assert match.proposal_path == ready.proposal_path


def test_find_proposal_picks_newest_of_two_approved_and_flags_ambiguous(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    artifacts_dir = tmp_path / "artifacts"
    recording_id = str(uuid4())
    _approved_proposal(
        tmp_path / "src-old",
        raw_sidecar,
        artifacts_dir,
        seed=b"old",
        generated_at=datetime(2026, 8, 5, 10, tzinfo=UTC),
        recording_id=recording_id,
    )
    newer = _approved_proposal(
        tmp_path / "src-new",
        raw_sidecar,
        artifacts_dir,
        seed=b"new",
        generated_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        recording_id=recording_id,
    )
    match = find_proposal(recording_id, artifacts_dir)
    assert match.candidate_count == 2
    assert match.ambiguous
    assert not match.unclear
    assert match.proposal_path == newer.proposal_path


def test_find_proposal_no_recording_id() -> None:
    assert find_proposal(None, Path("unused")) == inv._NO_PROPOSAL


def test_find_proposal_no_proposals_directory(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "rec-3").mkdir(parents=True)
    match = find_proposal("rec-3", artifacts_dir)
    assert match.proposal_path is None
    assert match.candidate_count == 0
    assert not match.unclear


def test_find_proposal_directory_without_proposal_file(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    (artifacts_dir / "rec-4" / "proposals" / "proposal-empty").mkdir(parents=True)
    match = find_proposal("rec-4", artifacts_dir)
    assert match.proposal_path is None
    assert match.candidate_count == 0
    assert not match.unclear


def test_find_proposal_invalid_proposal_json_is_not_a_candidate_result_unclear(
    tmp_path: Path,
) -> None:
    """Weder gültig noch freigebbar - kein stiller Kandidat, sondern ungeklärt."""
    artifacts_dir = tmp_path / "artifacts"
    directory = artifacts_dir / "rec-6" / "proposals" / "proposal-x"
    _touch(directory / "cut-proposal.json", "{not json")
    match = find_proposal("rec-6", artifacts_dir)
    assert match.proposal_path is None
    assert match.candidate_count == 0
    assert match.unclear


def test_parse_generated_at_requires_a_timezone_aware_iso_timestamp(tmp_path: Path) -> None:
    aware = tmp_path / "aware.json"
    _touch(aware, json.dumps({"generated_at": "2026-08-09T12:00:00+00:00"}))
    assert inv._parse_generated_at(aware) == datetime(2026, 8, 9, 12, tzinfo=UTC)

    naive = tmp_path / "naive.json"
    _touch(naive, json.dumps({"generated_at": "2026-08-09T12:00:00"}))
    assert inv._parse_generated_at(naive) is None

    garbage = tmp_path / "garbage.json"
    _touch(garbage, json.dumps({"generated_at": "not-a-timestamp"}))
    assert inv._parse_generated_at(garbage) is None

    missing = tmp_path / "missing.json"
    _touch(missing, json.dumps({"schema_version": "1.0"}))
    assert inv._parse_generated_at(missing) is None


def test_find_proposal_pending_decision_is_not_chosen_result_unclear(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    """Belegt B.3: ein `pending`-Proposal wird nie stillschweigend als Treffer verbucht."""
    artifacts_dir = tmp_path / "artifacts"
    recording_id = str(uuid4())
    _pending_proposal(
        tmp_path / "src-pending",
        raw_sidecar,
        artifacts_dir,
        seed=b"pending",
        generated_at=datetime(2026, 8, 3, 14, 28, 15, tzinfo=UTC),
        recording_id=recording_id,
    )
    match = find_proposal(recording_id, artifacts_dir)
    assert match.proposal_path is None
    assert match.candidate_count == 0
    assert match.unclear


def test_find_proposal_broken_digest_binding_is_not_chosen(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    """Ein Approval, das an ein anderes Proposal gebunden ist, zählt nicht als Kandidat."""
    artifacts_dir = tmp_path / "artifacts"
    recording_id = str(uuid4())
    approved = _approved_proposal(
        tmp_path / "src-approved",
        raw_sidecar,
        artifacts_dir,
        seed=b"approved",
        generated_at=datetime(2026, 8, 9, 9, tzinfo=UTC),
        recording_id=recording_id,
    )
    unapproved = _generate_test_proposal(
        tmp_path / "src-unapproved",
        raw_sidecar,
        artifacts_dir,
        seed=b"unapproved",
        generated_at=datetime(2026, 8, 9, 11, tzinfo=UTC),
        recording_id=recording_id,
    )
    # Freigabe existiert, ist aber - über das digestgebundene Approval hinweg kopiert -
    # an das falsche Proposal gebunden. `approval.py` muss das erkennen, nicht wir.
    approved_approval_bytes = approved.proposal_path.with_name("approval.json").read_bytes()
    unapproved.proposal_path.with_name("approval.json").write_bytes(approved_approval_bytes)

    match = find_proposal(recording_id, artifacts_dir)
    assert match.candidate_count == 1
    assert not match.unclear
    assert match.proposal_path == approved.proposal_path


def test_find_proposal_sorts_by_actual_instant_across_utc_offsets(
    tmp_path: Path, raw_sidecar: dict[str, Any]
) -> None:
    """Ein späterer Zonenversatz darf einen tatsächlich früheren Zeitpunkt nicht schlagen.

    12:00 UTC ist später als 13:30+02:00 (== 11:30 UTC). Lexikalischer Stringvergleich
    ("13" > "12") hätte das falsche Proposal gewählt.
    """
    artifacts_dir = tmp_path / "artifacts"
    recording_id = str(uuid4())
    actually_later = _approved_proposal(
        tmp_path / "src-later",
        raw_sidecar,
        artifacts_dir,
        seed=b"later",
        generated_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        recording_id=recording_id,
    )
    _approved_proposal(
        tmp_path / "src-earlier",
        raw_sidecar,
        artifacts_dir,
        seed=b"earlier",
        generated_at=datetime(2026, 8, 9, 13, 30, tzinfo=timezone(timedelta(hours=2))),
        recording_id=recording_id,
    )
    match = find_proposal(recording_id, artifacts_dir)
    assert match.candidate_count == 2
    assert match.ambiguous
    assert not match.unclear
    assert match.proposal_path == actually_later.proposal_path


# --- Dauer über ffprobe -------------------------------------------------------------


def test_probe_duration_ms_no_ffprobe_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ohne ``ffprobe_path`` sucht die Funktion selbst - und fand auf dieser
    Maschine das echte ``ffprobe`` samt Unterprozess. Der Test hiess "nicht
    verfuegbar" und pruefte in Wahrheit den Rueckfall auf eine unlesbare
    Ausgabe. Erst die abgeschaltete Suche stellt den gemeinten Fall her."""
    monkeypatch.setattr(inv, "discover_ffprobe", lambda *a, **k: None)

    assert probe_duration_ms(tmp_path / "video.mp4", ffprobe_path=None) is None


def test_probe_duration_ms_uses_given_ffprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ffprobe = tmp_path / "ffprobe.exe"
    fake_ffprobe.write_text("", encoding="utf-8")

    class _FakeResult:
        stdout = b"49.583000\n"

    def _fake_run(*_args: object, **_kwargs: object) -> _FakeResult:
        return _FakeResult()

    monkeypatch.setattr(inv.subprocess, "run", _fake_run)
    duration = probe_duration_ms(tmp_path / "video.mp4", ffprobe_path=fake_ffprobe)
    assert duration == 49583


def test_probe_duration_ms_handles_subprocess_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ffprobe = tmp_path / "ffprobe.exe"
    fake_ffprobe.write_text("", encoding="utf-8")

    def _fake_run(*_args: object, **_kwargs: object) -> object:
        raise OSError("no such process")

    monkeypatch.setattr(inv.subprocess, "run", _fake_run)
    assert probe_duration_ms(tmp_path / "video.mp4", ffprobe_path=fake_ffprobe) is None


def test_probe_duration_ms_handles_unparseable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_ffprobe = tmp_path / "ffprobe.exe"
    fake_ffprobe.write_text("", encoding="utf-8")

    class _FakeResult:
        stdout = b"not-a-number\n"

    monkeypatch.setattr(inv.subprocess, "run", lambda *a, **k: _FakeResult())
    assert probe_duration_ms(tmp_path / "video.mp4", ffprobe_path=fake_ffprobe) is None


def test_discover_ffprobe_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inv.shutil, "which", lambda _name: None)
    assert inv.discover_ffprobe() is None


def test_discover_ffprobe_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(inv.shutil, "which", lambda _name: "C:/tools/ffprobe.exe")
    assert inv.discover_ffprobe() == Path("C:/tools/ffprobe.exe")


# --- build_inventory: Gesamtlauf über ein kleines, gestelltes Verzeichnis ----------


def test_build_inventory_end_to_end(tmp_path: Path, raw_sidecar: dict[str, Any]) -> None:
    rendered_dir = tmp_path / "Rendered"
    raw_dir = tmp_path / "raw"
    avatar_dir = tmp_path / "Avatar"
    cursor_dir = tmp_path / "Cursor"
    drive_root = tmp_path / "drive-root"
    sessions_dir = tmp_path / "sessions"
    artifacts_dir = tmp_path / "artifacts"

    _touch(rendered_dir / "2026-08-09 15-10-19.matrix-cut.mp4")
    _touch(raw_dir / "2026-08-09 15-10-19.mp4")
    _touch(raw_dir / "2026-08-09 15-10-19.obs-events.json")
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 15-10-19.mp4")
    recording_id = str(uuid4())
    _write_session(
        sessions_dir,
        recording_id,
        source_path=str(raw_dir / "2026-08-09 15-10-19.mp4"),
        render_target_path=None,
    )
    ready = _approved_proposal(
        tmp_path / "src-e2e",
        raw_sidecar,
        artifacts_dir,
        seed=b"e2e",
        generated_at=datetime(2026, 8, 9, 10, tzinfo=UTC),
        recording_id=recording_id,
    )

    rows = build_inventory(
        rendered_dir=rendered_dir,
        raw_dir=raw_dir,
        avatar_dir=avatar_dir,
        cursor_dir=cursor_dir,
        drive_root=drive_root,
        sessions_dir=sessions_dir,
        artifacts_dir=artifacts_dir,
        probe_duration=False,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.name == "2026-08-09 15-10-19"
    assert row.duration_ms is None
    assert row.raw_exists
    assert row.sidecar_exists
    assert row.proposal.proposal_path == ready.proposal_path
    assert row.proposal.schema_version == ready.proposal.schema_version
    assert not row.proposal.unclear
    assert row.avatar.match_kind == "exact"
    assert row.cursor.match_kind == "none"


def test_build_inventory_missing_everything(tmp_path: Path) -> None:
    rendered_dir = tmp_path / "Rendered"
    _touch(rendered_dir / "2026-08-01 00-00-00.matrix-cut.mp4")
    rows = build_inventory(
        rendered_dir=rendered_dir,
        raw_dir=tmp_path / "raw",
        avatar_dir=tmp_path / "Avatar",
        cursor_dir=tmp_path / "Cursor",
        drive_root=tmp_path / "drive-root",
        sessions_dir=tmp_path / "sessions",
        artifacts_dir=tmp_path / "artifacts",
        probe_duration=False,
    )
    assert len(rows) == 1
    row = rows[0]
    assert not row.raw_exists
    assert not row.sidecar_exists
    assert row.proposal.proposal_path is None
    assert row.avatar.match_kind == "none"
    assert row.cursor.match_kind == "none"


# --- shorts-job.json: Inhalt und atomares Schreiben --------------------------------


def _sample_row() -> VideoRow:
    return VideoRow(
        name="2026-08-09 15-10-19",
        rendered_path=Path("F:/MatrixMarketAutoEdit/Rendered/2026-08-09 15-10-19.matrix-cut.mp4"),
        duration_ms=49583,
        raw_path=Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.mp4"),
        raw_exists=True,
        sidecar_path=Path("F:/MatrixMarketAutoEdit/2026-08-09 15-10-19.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(
            recording_id="rec-1",
            proposal_path=Path("artifacts/rec-1/proposals/proposal-a/cut-proposal.json"),
            schema_version="1.1",
            candidate_count=1,
            ambiguous=False,
            unclear=False,
        ),
        avatar=AvatarMatch(
            path=Path("F:/ShortsQuellen/Avatar/AvatarWebcam-2026-08-09 15-10-19.mp4"),
            match_kind="exact",
        ),
        cursor=CursorMatch(path=None, match_kind="none"),
    )


def test_build_job_payload_contains_all_collected_fields() -> None:
    payload = build_job_payload(_sample_row(), created_at="2026-08-10T12:00:00+00:00")
    assert payload["video_name"] == "2026-08-09 15-10-19"
    assert payload["created_at"] == "2026-08-10T12:00:00+00:00"
    assert payload["rendered_video"]["duration_ms"] == 49583
    assert payload["raw_recording"]["exists"] is True
    assert payload["sidecar"]["exists"] is True
    assert payload["proposal"]["schema_version"] == "1.1"
    assert payload["proposal"]["ambiguous"] is False
    assert payload["proposal"]["unclear"] is False
    assert payload["avatar"]["match_kind"] == "exact"
    assert payload["cursor_log"]["path"] is None


def test_job_output_path() -> None:
    assert job_output_path(Path("artefakte/repeat/shorts"), "2026-08-09 15-10-19") == Path(
        "artefakte/repeat/shorts/2026-08-09 15-10-19/shorts-job.json"
    )


def test_write_job_creates_file_atomically(tmp_path: Path) -> None:
    target = tmp_path / "2026-08-09 15-10-19" / "shorts-job.json"
    payload = build_job_payload(_sample_row(), created_at="2026-08-10T12:00:00+00:00")
    write_job(target, payload, overwrite=False)
    assert target.is_file()
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["video_name"] == "2026-08-09 15-10-19"
    remaining_temp_files = list(target.parent.glob(".shorts-job.json.tmp.*"))
    assert remaining_temp_files == []


def test_write_job_without_overwrite_refuses_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "2026-08-09 15-10-19" / "shorts-job.json"
    payload = build_job_payload(_sample_row(), created_at="2026-08-10T12:00:00+00:00")
    write_job(target, payload, overwrite=False)
    with pytest.raises(FileExistsError):
        write_job(target, payload, overwrite=False)
    remaining_temp_files = list(target.parent.glob(".shorts-job.json.tmp.*"))
    assert remaining_temp_files == []


def test_write_job_with_overwrite_replaces_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "2026-08-09 15-10-19" / "shorts-job.json"
    payload = build_job_payload(_sample_row(), created_at="2026-08-10T12:00:00+00:00")
    write_job(target, payload, overwrite=False)
    updated_payload = dict(payload)
    updated_payload["created_at"] = "2026-08-10T13:00:00+00:00"
    write_job(target, updated_payload, overwrite=True)
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["created_at"] == "2026-08-10T13:00:00+00:00"


def test_job_module_uses_shared_atomic_replace() -> None:
    assert job_module.replace_atomically is not None


def test_job_schema_version_bumped_for_the_unclear_field() -> None:
    """Auftrag 04, Eingriff 1-3: neue Felder, also eine neue Schemaversion."""
    assert job_module.JOB_SCHEMA_VERSION == "0.2"


# --- app.py: Fenstergeometrie, Sperrdatei, Textformatierung ------------------------


from matrix_auto_cutter.shorts import app as shorts_app  # noqa: E402


def test_window_geometry_parse_and_render() -> None:
    geometry = shorts_app.WindowGeometry.parse("980x620+100-50")
    assert geometry == shorts_app.WindowGeometry(width=980, height=620, x=100, y=-50)
    assert geometry.as_geometry() == "980x620+100+-50"


def test_window_geometry_parse_rejects_garbage() -> None:
    assert shorts_app.WindowGeometry.parse("not-a-geometry") is None
    assert shorts_app.WindowGeometry.parse("0x0+0+0") is None


def test_window_geometry_fitted_clamps_to_screen() -> None:
    geometry = shorts_app.WindowGeometry(width=5000, height=5000, x=-100, y=9000)
    fitted = geometry.fitted(minimum=(200, 200), screen=(1920, 1080))
    assert fitted.width == 1920
    assert fitted.height == 1080
    assert fitted.x == 0
    assert fitted.y == 0


def test_window_state_path_uses_own_filename(tmp_path: Path) -> None:
    path = shorts_app.window_state_path(tmp_path)
    assert path == tmp_path / "shorts-window.json"
    assert path.name != "review-window.json"


def test_load_window_geometry_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "shorts-window.json"
    geometry = shorts_app.WindowGeometry(width=800, height=600, x=10, y=20)
    shorts_app.store_window_geometry(path, geometry)
    loaded = shorts_app.load_window_geometry(path)
    assert loaded == geometry


def test_load_window_geometry_missing_file(tmp_path: Path) -> None:
    assert shorts_app.load_window_geometry(tmp_path / "missing.json") is None


def test_load_window_geometry_rejects_malformed_content(tmp_path: Path) -> None:
    path = tmp_path / "shorts-window.json"
    path.write_text("not json", encoding="utf-8")
    assert shorts_app.load_window_geometry(path) is None
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert shorts_app.load_window_geometry(path) is None
    path.write_text(json.dumps({"width": 0, "height": 10, "x": 0, "y": 0}), encoding="utf-8")
    assert shorts_app.load_window_geometry(path) is None
    path.write_text(json.dumps({"width": "a", "height": 10, "x": 0, "y": 0}), encoding="utf-8")
    assert shorts_app.load_window_geometry(path) is None


def test_shorts_single_instance_uses_its_own_lock_name(tmp_path: Path) -> None:
    guard = shorts_app.ShortsSingleInstance()
    assert guard.path.name == "shorts-tool.lock"
    assert guard.path.name != "review.lock"


def test_shorts_single_instance_second_acquire_fails(tmp_path: Path) -> None:
    lock_path = tmp_path / "shorts-tool.lock"
    first = shorts_app.ShortsSingleInstance(lock_path)
    second = shorts_app.ShortsSingleInstance(lock_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.close()
    third = shorts_app.ShortsSingleInstance(lock_path)
    assert third.acquire() is True
    third.close()


def test_format_duration_known_and_unknown() -> None:
    assert shorts_app._format_duration(49583) == "00:49"
    assert shorts_app._format_duration(None) == "Dauer unbekannt"


def test_row_summary_reports_every_guess_kind() -> None:
    row = VideoRow(
        name="2026-08-09 12-09-50",
        rendered_path=Path("R/2026-08-09 12-09-50.matrix-cut.mp4"),
        duration_ms=None,
        raw_path=Path("raw/2026-08-09 12-09-50.mp4"),
        raw_exists=False,
        sidecar_path=Path("raw/2026-08-09 12-09-50.obs-events.json"),
        sidecar_exists=False,
        proposal=ProposalMatch(
            recording_id="rec-2",
            proposal_path=Path("artifacts/rec-2/proposals/proposal-new/cut-proposal.json"),
            schema_version="1.2",
            candidate_count=2,
            ambiguous=True,
        ),
        avatar=AvatarMatch(
            path=Path("Avatar/AvatarWebcam-2026-08-09 12-09-58.mp4"),
            match_kind="offset_guess",
            offset_seconds=8,
        ),
        cursor=CursorMatch(
            path=Path("Cursor/cursor-2026-08-07 11-28-59.csv"),
            match_kind="matched_guess",
            lead_seconds=377,
        ),
    )
    summary = shorts_app._row_summary(row)
    assert "FEHLT" in summary
    assert "mehrdeutig" in summary
    assert "Zeitversatz +8 s, geraten" in summary
    assert "Vorlauf 377 s, geraten" in summary
    assert summary.index("1.2") < summary.index(
        str(row.proposal.proposal_path)
    )


def test_row_summary_reports_missing_avatar_proposal_and_cursor() -> None:
    row = VideoRow(
        name="2026-08-09 08-43-22",
        rendered_path=Path("R/2026-08-09 08-43-22.matrix-cut.mp4"),
        duration_ms=62900,
        raw_path=Path("raw/2026-08-09 08-43-22.mp4"),
        raw_exists=True,
        sidecar_path=Path("raw/2026-08-09 08-43-22.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(None, None, None, 0, False),
        avatar=AvatarMatch(None, "none", None),
        cursor=CursorMatch(None, "none", None),
    )
    summary = shorts_app._row_summary(row)
    assert "Proposal: nicht gefunden" in summary
    assert "Avatar: nicht gefunden" in summary
    assert "Cursorprotokoll: nicht gefunden" in summary


def test_row_summary_reports_unclear_proposal_distinct_from_not_found() -> None:
    """Auftrag 06, Aufgabe 2: die UNGEKLÄRT-Zeile war bisher nur über die GUI erreichbar."""
    row = VideoRow(
        name="2026-08-03 16-25-19",
        rendered_path=Path("R/2026-08-03 16-25-19.matrix-cut.mp4"),
        duration_ms=None,
        raw_path=Path("raw/2026-08-03 16-25-19.mp4"),
        raw_exists=True,
        sidecar_path=Path("raw/2026-08-03 16-25-19.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(
            recording_id="rec-unclear",
            proposal_path=None,
            schema_version=None,
            candidate_count=2,
            ambiguous=False,
            unclear=True,
        ),
        avatar=AvatarMatch(None, "none", None),
        cursor=CursorMatch(None, "none", None),
    )
    summary = shorts_app._row_summary(row)
    assert "Proposal: UNGEKLÄRT" in summary
    assert "2 freigegebene Kandidaten" in summary
    assert "Proposal: nicht gefunden" not in summary


def test_row_summary_reports_avatar_offset_guess_as_guessed() -> None:
    row = VideoRow(
        name="2026-08-09 07-25-37",
        rendered_path=Path("R/2026-08-09 07-25-37.matrix-cut.mp4"),
        duration_ms=None,
        raw_path=Path("raw/2026-08-09 07-25-37.mp4"),
        raw_exists=True,
        sidecar_path=Path("raw/2026-08-09 07-25-37.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(None, None, None, 0, False),
        avatar=AvatarMatch(
            path=Path("Avatar/AvatarWebcam-2026-08-09 07-25-46.mp4"),
            match_kind="offset_guess",
            offset_seconds=9,
        ),
        cursor=CursorMatch(None, "none", None),
    )
    summary = shorts_app._row_summary(row)
    assert "geraten" in summary
    assert "Zeitversatz +9 s, geraten" in summary


def _row_with_cursor(lead_seconds: int | None) -> VideoRow:
    return VideoRow(
        name="2026-08-07 11-35-16",
        rendered_path=Path("R/2026-08-07 11-35-16.matrix-cut.mp4"),
        duration_ms=None,
        raw_path=Path("raw/2026-08-07 11-35-16.mp4"),
        raw_exists=True,
        sidecar_path=Path("raw/2026-08-07 11-35-16.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(None, None, None, 0, False),
        avatar=AvatarMatch(None, "none", None),
        cursor=CursorMatch(
            path=Path("Cursor/cursor-2026-08-07 11-28-59.csv"),
            match_kind="matched_guess",
            lead_seconds=lead_seconds,
        ),
    )


def test_row_summary_cursor_lead_seconds_present_and_none() -> None:
    """Kein geschätzter Wert und kein Null-Text, wenn ``lead_seconds`` fehlt."""
    summary_with_lead = shorts_app._row_summary(_row_with_cursor(368))
    assert "Vorlauf 368 s, geraten" in summary_with_lead

    summary_without_lead = shorts_app._row_summary(_row_with_cursor(None))
    assert "Vorlauf None" not in summary_without_lead
    assert "Vorlauf 368" not in summary_without_lead


def test_row_summary_reports_root_fallback_avatar() -> None:
    row = VideoRow(
        name="2026-08-04 01-11-36",
        rendered_path=Path("R/2026-08-04 01-11-36.matrix-cut.mp4"),
        duration_ms=1044100,
        raw_path=Path("raw/2026-08-04 01-11-36.mp4"),
        raw_exists=True,
        sidecar_path=Path("raw/2026-08-04 01-11-36.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(None, None, None, 0, False),
        avatar=AvatarMatch(
            path=Path("drive-root/AvatarWebcam-2026-08-04 01-11-36.mp4"),
            match_kind="root_fallback",
        ),
        cursor=CursorMatch(None, "none", None),
    )
    summary = shorts_app._row_summary(row)
    assert "außerhalb des Musters, Root-Pfad" in summary
    assert "geraten" in summary
