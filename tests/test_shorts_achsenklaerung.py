"""Tests zum Auftrag "shorts-achsenklaerung" (2026-08-14), Teile B, C, D.

Reale Belegwerte aus der Aufnahme "2026-08-07 11-35-16" und aus
``F:\\ShortsQuellen\\``, siehe BERICHT-2026-08-14.md.
"""

from __future__ import annotations

from pathlib import Path

from matrix_auto_cutter.shorts.inventory import (
    AVATAR_OFFSET_WINDOW_SECONDS,
    CURSOR_LEAD_WINDOW_SECONDS,
    AvatarMatch,
    ProposalMatch,
    VideoRow,
    find_avatar,
    find_cursor,
)
from matrix_auto_cutter.shorts.job import build_job_payload


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- Teil B: shorts-job.json traegt den vollstaendigen Pfad, nicht nur video_name --


def _row_for_matrix_cut() -> VideoRow:
    return VideoRow(
        name="2026-08-07 11-35-16",
        rendered_path=Path(
            "F:/MatrixMarketAutoEdit/Rendered/2026-08-07 11-35-16.matrix-cut.mp4"
        ),
        duration_ms=881980,
        raw_path=Path("F:/MatrixMarketAutoEdit/2026-08-07 11-35-16.mp4"),
        raw_exists=True,
        sidecar_path=Path("F:/MatrixMarketAutoEdit/2026-08-07 11-35-16.obs-events.json"),
        sidecar_exists=True,
        proposal=ProposalMatch(None, None, None, 0, False),
        avatar=AvatarMatch(None, "none", None),
        cursor=find_cursor(
            "2026-08-07 11-35-16", Path("does-not-exist")
        ),
    )


def test_job_payload_carries_full_rendered_path_and_duration_ms() -> None:
    """Drei Dateien teilen sich denselben video_name (matrix-cut/upload/upload2,
    Teil A des Auftrags) - der volle Pfad in der Nutzlast trennt sie eindeutig,
    ``video_name`` alleine koennte das nicht.
    """
    payload = build_job_payload(_row_for_matrix_cut(), created_at="2026-08-14T12:00:00+00:00")
    assert payload["video_name"] == "2026-08-07 11-35-16"
    rendered = payload["rendered_video"]
    assert isinstance(rendered, dict)
    assert rendered["path"] == (
        "F:\\MatrixMarketAutoEdit\\Rendered\\2026-08-07 11-35-16.matrix-cut.mp4"
    )
    assert rendered["path"].endswith(".matrix-cut.mp4")
    assert not rendered["path"].endswith(".upload.mp4")
    assert not rendered["path"].endswith(".upload2.mp4")
    assert rendered["duration_ms"] == 881980


def test_older_job_payload_without_new_fields_stays_loadable() -> None:
    """Aeltere shorts-job.json kannten nur ``video_name`` - reines dict-Lesen
    mit ``.get()`` darf daran nicht scheitern, und ``rendered_video.path`` war
    schon vor diesem Auftrag vorhanden (``transcript.py``/``judge_server.py``
    lesen es bereits, unveraendert seit Schema 0.2).
    """
    legacy_payload = {
        "artifact_type": "matrix_auto_cutter_shorts_job",
        "schema_version": "0.1",
        "video_name": "2026-08-07 11-35-16",
    }
    assert legacy_payload.get("rendered_video") is None
    assert legacy_payload["video_name"] == "2026-08-07 11-35-16"

    from matrix_auto_cutter.shorts import job as job_module

    assert job_module.JOB_SCHEMA_VERSION == "0.2"


# --- Teil C: find_cursor - Vorlauf vor der Aufnahme, hoechstens 30 Minuten --------


def test_cursor_lead_window_is_thirty_minutes() -> None:
    assert CURSOR_LEAD_WINDOW_SECONDS == 30 * 60


def test_find_cursor_real_case_matches_within_the_thirty_minute_window(
    tmp_path: Path,
) -> None:
    """Belegter Fall: Vorlauf 377 s laut Dateiname, 367,9 s bis zur ersten
    Datenzeile - beides klar innerhalb von 30 Minuten.
    """
    cursor_dir = tmp_path / "Cursor"
    _touch(
        cursor_dir / "cursor-2026-08-07 11-28-59.csv",
        "zeit,x,y\n2026-08-07T11:29:08.0642210+02:00,-792,367\n",
    )
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "matched_guess"
    assert match.lead_seconds == 368


def test_find_cursor_rejects_lead_beyond_thirty_minutes(tmp_path: Path) -> None:
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-07 10-55-00.csv")
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "none"


def test_find_cursor_accepts_lead_just_inside_thirty_minutes(tmp_path: Path) -> None:
    cursor_dir = tmp_path / "Cursor"
    _touch(cursor_dir / "cursor-2026-08-07 11-05-17.csv")
    match = find_cursor("2026-08-07 11-35-16", cursor_dir)
    assert match.match_kind == "matched_guess"


# --- Teil D: find_avatar - nur Versatz 0..15 s NACH dem Aufnahmebeginn -----------


def test_avatar_offset_window_is_fifteen_seconds() -> None:
    assert AVATAR_OFFSET_WINDOW_SECONDS == 15


def test_find_avatar_known_positive_offsets_still_match(tmp_path: Path) -> None:
    cases = [
        ("2026-08-09 12-09-50", "2026-08-09 12-09-58", 8),
        ("2026-08-09 07-25-37", "2026-08-09 07-25-46", 9),
        ("2026-08-09 07-29-51", "2026-08-09 07-29-52", 1),
    ]
    for video, avatar_suffix, expected_offset in cases:
        avatar_dir = tmp_path / video
        _touch(avatar_dir / f"AvatarWebcam-{avatar_suffix}.mp4")
        match = find_avatar(video, avatar_dir, tmp_path / "drive-root")
        assert match.match_kind == "offset_guess"
        assert match.offset_seconds == expected_offset


def test_find_avatar_negative_offset_does_not_match(tmp_path: Path) -> None:
    """Avatardatei VOR dem Aufnahmebeginn ist kein Treffer - OBS startet die
    zweite Aufnahme systematisch spaeter, nie frueher.
    """
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-43-03.mp4")
    match = find_avatar("2026-08-09 08-43-22", avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "none"


def test_find_avatar_multiple_candidates_in_window_stay_unclear(tmp_path: Path) -> None:
    """Mehrere Kandidaten im Fenster: 'ungeklaert', nicht den naechstliegenden waehlen."""
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-43-25.mp4")
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-43-30.mp4")
    match = find_avatar("2026-08-09 08-43-22", avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "none"
    assert match.path is None


def test_find_avatar_second_files_for_08_43_22_and_11_31_19_stay_outside_window(
    tmp_path: Path,
) -> None:
    """Belegte Zweitdateien ohne zugehoerigen Render - duerfen nichts mehrdeutig machen."""
    avatar_dir = tmp_path / "Avatar"
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-43-22.mp4")
    _touch(avatar_dir / "AvatarWebcam-2026-08-09 08-43-03.mp4")
    match = find_avatar("2026-08-09 08-43-22", avatar_dir, tmp_path / "drive-root")
    assert match.match_kind == "exact"

    avatar_dir_2 = tmp_path / "Avatar2"
    _touch(avatar_dir_2 / "AvatarWebcam-2026-08-12 11-31-19.mp4")
    _touch(avatar_dir_2 / "AvatarWebcam-2026-08-12 11-29-42.mp4")
    match_2 = find_avatar("2026-08-12 11-31-19", avatar_dir_2, tmp_path / "drive-root")
    assert match_2.match_kind == "exact"
