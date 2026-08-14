"""Stufe 2, Teil 3: Schema und Ladefunktion für ``kandidaten.json``.

Das Format ist der Ausgabekontrakt von Teil 2 (Kandidatensuche durch ein
Sprachmodell - vorerst ein Claude-Code-Fenster, siehe Auftrag 20 Abschnitt 0)
und der Eingabekontrakt von Teil 3 (der Urteilsseite, ``judge.py``). Es ist an
``artefakte/repeat/shorts-blindzerlegung/zerlegung-blind.json`` angelehnt -
dort auf der Rohachse und mit einem zusätzlichen ``verworfen``-Block, hier auf
der gerenderten Achse und ohne diesen Block, weil die Urteilsseite nur
Kandidaten zeigt, keine Verwerfungen.

Ein Schemafehler wirft :class:`CandidatesSchemaError` mit einer konkreten,
auf den betroffenen Kandidaten zeigenden Meldung - kein stiller Ausfall,
keine halb aufgebaute Seite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

Sicherheit = Literal["hoch", "mittel", "niedrig"]
_SICHERHEIT_VALUES: tuple[Sicherheit, ...] = get_args(Sicherheit)
_SICHERHEIT_RANK: dict[str, int] = {"hoch": 0, "mittel": 1, "niedrig": 2}

CANDIDATES_FILE_NAME = "kandidaten.json"
CANDIDATES_SCHEMA_VERSION = "1.0"


class CandidatesSchemaError(Exception):
    """``kandidaten.json`` verletzt den Kandidaten-Kontrakt."""


@dataclass(frozen=True, slots=True)
class Candidate:
    """Ein Kandidat auf der gerenderten Achse (dieselbe Achse wie die fertigen Shorts)."""

    index: int
    start_ms: int
    end_ms: int
    titel: str
    begruendung: str
    sicherheit: Sicherheit
    enthaelt: tuple[int, ...]

    @property
    def duration_ms(self) -> int:
        """Dauer des Ausschnitts in Millisekunden."""
        return self.end_ms - self.start_ms

    @property
    def sicherheit_rank(self) -> int:
        """Sortierrang der Sicherheit: 0 = hoch, 1 = mittel, 2 = niedrig."""
        return _SICHERHEIT_RANK[self.sicherheit]


def parse_candidates(raw_json: str) -> list[Candidate]:
    """Lies und prüfe die Kandidatenliste; jeder Verstoß wirft ``CandidatesSchemaError``.

    Akzeptiert entweder eine Wurzel-Liste oder ein Objekt mit dem Feld
    ``kandidaten`` (wie ``zerlegung-blind.json``). Referenzen in ``enthaelt``
    werden gegen die tatsächlich vorhandenen Indizes geprüft, nachdem alle
    Einträge gelesen sind.
    """
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise CandidatesSchemaError(f"kandidaten.json ist kein gültiges JSON: {exc}") from exc

    raw_list = payload.get("kandidaten") if isinstance(payload, dict) else payload
    if not isinstance(raw_list, list):
        raise CandidatesSchemaError(
            "kandidaten.json: erwartet eine Liste unter 'kandidaten' oder als Wurzel"
        )
    if not raw_list:
        raise CandidatesSchemaError("kandidaten.json: enthält keinen einzigen Kandidaten")

    candidates: list[Candidate] = []
    seen_indices: set[int] = set()
    for position, entry in enumerate(raw_list):
        if not isinstance(entry, dict):
            raise CandidatesSchemaError(f"Eintrag {position}: kein Objekt")

        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise CandidatesSchemaError(
                f"Eintrag {position}: 'index' fehlt oder ist keine Ganzzahl"
            )
        if index in seen_indices:
            raise CandidatesSchemaError(f"Kandidat {index}: Index doppelt vergeben")
        seen_indices.add(index)

        start_ms = entry.get("start_ms")
        if not isinstance(start_ms, int) or isinstance(start_ms, bool) or start_ms < 0:
            raise CandidatesSchemaError(f"Kandidat {index}: 'start_ms' fehlt oder ist ungültig")

        end_ms = entry.get("end_ms")
        if not isinstance(end_ms, int) or isinstance(end_ms, bool) or end_ms <= start_ms:
            raise CandidatesSchemaError(
                f"Kandidat {index}: 'end_ms' fehlt, ist ungültig oder liegt nicht nach 'start_ms'"
            )

        titel = entry.get("titel")
        if not isinstance(titel, str) or not titel.strip():
            raise CandidatesSchemaError(f"Kandidat {index}: 'titel' fehlt oder ist leer")

        begruendung = entry.get("begruendung")
        if not isinstance(begruendung, str) or not begruendung.strip():
            raise CandidatesSchemaError(f"Kandidat {index}: 'begruendung' fehlt oder ist leer")

        sicherheit = entry.get("sicherheit")
        if sicherheit not in _SICHERHEIT_VALUES:
            raise CandidatesSchemaError(
                f"Kandidat {index}: 'sicherheit' muss eine von {_SICHERHEIT_VALUES} sein, "
                f"nicht {sicherheit!r}"
            )

        enthaelt_raw = entry.get("enthaelt", [])
        if not isinstance(enthaelt_raw, list) or any(
            not isinstance(value, int) or isinstance(value, bool) for value in enthaelt_raw
        ):
            raise CandidatesSchemaError(
                f"Kandidat {index}: 'enthaelt' muss eine Liste von Ganzzahlen sein"
            )

        candidates.append(
            Candidate(
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                titel=titel,
                begruendung=begruendung,
                sicherheit=sicherheit,
                enthaelt=tuple(enthaelt_raw),
            )
        )

    known_indices = {candidate.index for candidate in candidates}
    for candidate in candidates:
        if candidate.index in candidate.enthaelt:
            raise CandidatesSchemaError(
                f"Kandidat {candidate.index}: 'enthaelt' verweist auf sich selbst"
            )
        unknown = [ref for ref in candidate.enthaelt if ref not in known_indices]
        if unknown:
            raise CandidatesSchemaError(
                f"Kandidat {candidate.index}: 'enthaelt' verweist auf unbekannte Indizes {unknown}"
            )

    return candidates


def load_candidates(path: Path) -> list[Candidate]:
    """Lies ``kandidaten.json`` von der Platte; Schemafehler werden nicht verschluckt."""
    return parse_candidates(path.read_text(encoding="utf-8"))
