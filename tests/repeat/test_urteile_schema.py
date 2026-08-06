"""Schema test for the bindende Urteils-Datei labels/repeat/urteile-2026-08-05.json.

The 25 existing judgments MUST stay readable against this schema.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
_URTEILE_PATH = _REPO_ROOT / "labels" / "repeat" / "urteile-2026-08-05.json"


class _Passage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    start_ms: int
    end_ms: int


class _Scores(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    utterance: float | None
    boundary: float | None


class _Urteil(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    datei: str
    eintragsnummer: int
    erste_passage: _Passage
    zweite_passage: _Passage
    scores: _Scores
    detektoren: list[str]
    urteil: Literal["versprecher", "bewusst", "unsinn"] | None
    notiz: str


def _load_urteile() -> list[dict]:
    return json.loads(_URTEILE_PATH.read_text(encoding="utf-8"))


def test_urteile_file_exists() -> None:
    assert _URTEILE_PATH.is_file()


def test_urteile_has_25_entries() -> None:
    assert len(_load_urteile()) == 25


def test_all_urteile_entries_match_schema() -> None:
    for raw in _load_urteile():
        _Urteil.model_validate(raw)


def test_urteil_values_are_restricted_to_known_labels() -> None:
    allowed = {"versprecher", "bewusst", "unsinn", None}
    for raw in _load_urteile():
        assert raw["urteil"] in allowed
