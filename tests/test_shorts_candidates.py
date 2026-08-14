"""Tests für Shorts-Stufe 2, Teil 3: das Kandidaten-Schema (``kandidaten.json``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matrix_auto_cutter.shorts import candidates as cd


def _base_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "index": 0,
        "start_ms": 1_000,
        "end_ms": 5_000,
        "titel": "Ein Titel",
        "begruendung": "Eine Begründung",
        "sicherheit": "hoch",
        "enthaelt": [],
    }
    entry.update(overrides)
    return entry


def test_parse_candidates_reads_a_minimal_valid_entry() -> None:
    raw = json.dumps([_base_entry()])
    result = cd.parse_candidates(raw)
    assert result == [
        cd.Candidate(
            index=0,
            start_ms=1_000,
            end_ms=5_000,
            titel="Ein Titel",
            begruendung="Eine Begründung",
            sicherheit="hoch",
            enthaelt=(),
        )
    ]


def test_parse_candidates_accepts_wrapper_object_like_zerlegung_blind() -> None:
    raw = json.dumps({"kandidaten": [_base_entry()], "verworfen": []})
    result = cd.parse_candidates(raw)
    assert len(result) == 1


def test_parse_candidates_with_enthaelt_references_a_contained_candidate() -> None:
    raw = json.dumps(
        [
            _base_entry(index=0, start_ms=0, end_ms=10_000, enthaelt=[1]),
            _base_entry(index=1, start_ms=2_000, end_ms=6_000, titel="Kurzfassung"),
        ]
    )
    result = cd.parse_candidates(raw)
    by_index = {c.index: c for c in result}
    assert by_index[0].enthaelt == (1,)
    assert by_index[1].enthaelt == ()


def test_parse_candidates_rejects_malformed_json() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="gültiges JSON"):
        cd.parse_candidates("{not json")


def test_parse_candidates_rejects_non_list_payload() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="Liste"):
        cd.parse_candidates(json.dumps({"kandidaten": "nicht eine liste"}))


def test_parse_candidates_rejects_empty_list() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="keinen einzigen"):
        cd.parse_candidates(json.dumps([]))


def test_parse_candidates_rejects_missing_begruendung() -> None:
    entry = _base_entry()
    del entry["begruendung"]
    with pytest.raises(cd.CandidatesSchemaError, match="begruendung"):
        cd.parse_candidates(json.dumps([entry]))


def test_parse_candidates_rejects_blank_begruendung() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="begruendung"):
        cd.parse_candidates(json.dumps([_base_entry(begruendung="   ")]))


def test_parse_candidates_rejects_missing_titel() -> None:
    entry = _base_entry()
    del entry["titel"]
    with pytest.raises(cd.CandidatesSchemaError, match="titel"):
        cd.parse_candidates(json.dumps([entry]))


def test_parse_candidates_rejects_invalid_sicherheit() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="sicherheit"):
        cd.parse_candidates(json.dumps([_base_entry(sicherheit="unsicher")]))


def test_parse_candidates_rejects_end_before_start() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="end_ms"):
        cd.parse_candidates(json.dumps([_base_entry(start_ms=5_000, end_ms=1_000)]))


def test_parse_candidates_rejects_end_equal_start() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="end_ms"):
        cd.parse_candidates(json.dumps([_base_entry(start_ms=1_000, end_ms=1_000)]))


def test_parse_candidates_rejects_negative_start() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="start_ms"):
        cd.parse_candidates(json.dumps([_base_entry(start_ms=-1)]))


def test_parse_candidates_rejects_duplicate_index() -> None:
    raw = json.dumps([_base_entry(index=0), _base_entry(index=0)])
    with pytest.raises(cd.CandidatesSchemaError, match="doppelt"):
        cd.parse_candidates(raw)


def test_parse_candidates_rejects_enthaelt_referencing_unknown_index() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="unbekannte Indizes"):
        cd.parse_candidates(json.dumps([_base_entry(enthaelt=[99])]))


def test_parse_candidates_rejects_enthaelt_referencing_self() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="sich selbst"):
        cd.parse_candidates(json.dumps([_base_entry(index=0, enthaelt=[0])]))


def test_parse_candidates_rejects_non_integer_enthaelt_entries() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="enthaelt"):
        cd.parse_candidates(json.dumps([_base_entry(enthaelt=["eins"])]))


def test_parse_candidates_rejects_non_dict_entry() -> None:
    with pytest.raises(cd.CandidatesSchemaError, match="kein Objekt"):
        cd.parse_candidates(json.dumps(["nicht ein objekt"]))


def test_candidate_duration_ms_and_sicherheit_rank() -> None:
    candidate = cd.Candidate(
        index=0,
        start_ms=1_000,
        end_ms=4_500,
        titel="t",
        begruendung="b",
        sicherheit="mittel",
        enthaelt=(),
    )
    assert candidate.duration_ms == 3_500
    assert candidate.sicherheit_rank == 1


def test_load_candidates_reads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "kandidaten.json"
    path.write_text(json.dumps([_base_entry()]), encoding="utf-8")
    result = cd.load_candidates(path)
    assert len(result) == 1


def test_load_candidates_missing_file_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        cd.load_candidates(tmp_path / "does-not-exist.json")
