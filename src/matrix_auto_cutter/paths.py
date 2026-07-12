"""Deterministische Sidecar-Pfadzuordnung ohne Dateisuche."""

from __future__ import annotations

from pathlib import Path, PurePath, PureWindowsPath


def expected_sidecar_path(mp4_path: str | PurePath) -> PurePath:
    """Ersetze ausschließlich eine MP4-Erweiterung durch ``.obs-events.json``."""
    raw = str(mp4_path)
    path: PurePath = PureWindowsPath(raw) if "\\" in raw else Path(raw)
    if path.suffix.casefold() != ".mp4":
        msg = "Der Eingabepfad muss auf .mp4 enden."
        raise ValueError(msg)
    return path.with_suffix(".obs-events.json")
