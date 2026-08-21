r"""Stufe 4, Teil B: Untertitelzeilen aus wortgenauer Transkription bilden.

Zwei Schritte, beide reine Funktionen ohne IO und ohne ffmpeg:

``words_from_whisper_json`` liest die Wortliste aus einer whisper-cli
``-ojf``-Rohausgabe. whisper.cpp zerlegt Woerter in Teilstuecke (BPE-Tokens);
ein Token mit fuehrendem Leerzeichen beginnt ein neues Wort, eines ohne
fuehrendes Leerzeichen (z. B. Wortfortsetzungen oder Satzzeichen) haengt sich
an das laufende Wort an - so entstehen "Bereichen" aus " Bere" + "ichen" und
"gesprochen," aus " gesprochen" + ",". Sondertokens wie ``[_BEG_]`` tragen
keine echte Zeitspanne und werden uebersprungen.

``build_subtitle_lines`` bildet aus dieser Wortliste die Einblendzeilen fuer
das Band ab y=1100 links von x=930 (Stufe 5c). Lange Zeilen passen dort nicht
und werden bei schneller Lesegeschwindigkeit ohnehin nicht gelesen, deshalb
die harten Grenzen unten. Schrift, Farben und das eigentliche Einblenden sind
nicht Teil dieses Auftrags.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Alle Grenzwerte an einer Stelle (Auftrag shorts-stufe-4, Teil B).
MAX_WORDS_PER_LINE = 3
MAX_CHARS_PER_LINE = 24
MAX_GAP_MS = 400
SENTENCE_END_CHARS = (".", "!", "?")

_SPECIAL_TOKEN = re.compile(r"^\[_.*\]$")


class SubtitleWordTimingError(ValueError):
    """Einem Wort fehlen Zeitstempel, oder end_ms liegt vor start_ms."""


@dataclass(frozen=True, slots=True)
class Word:
    """Ein Wort mit eigener Zeitspanne in Millisekunden."""

    start_ms: int
    end_ms: int
    text: str

    def __post_init__(self) -> None:
        """Erzwinge die Zeitspannen-Invariante direkt bei der Konstruktion."""
        if self.end_ms < self.start_ms:
            raise SubtitleWordTimingError(
                f"end_ms ({self.end_ms}) liegt vor start_ms ({self.start_ms}) "
                f"bei Wort {self.text!r}"
            )


@dataclass(frozen=True, slots=True)
class SubtitleLine:
    """Eine Einblendzeile: Zeitspanne plus ihre Woerter, jedes mit eigener Zeitspanne."""

    start_ms: int
    end_ms: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        """Die Zeile als Fliesstext, nur zur Anzeige/zum Vergleich - kein neues Datum."""
        return " ".join(word.text for word in self.words)


def _token_offsets(token: dict[str, Any]) -> tuple[int, int]:
    offsets = token.get("offsets")
    if not isinstance(offsets, dict):
        raise SubtitleWordTimingError(f"Token ohne offsets: {token!r}")
    start = offsets.get("from")
    end = offsets.get("to")
    if not isinstance(start, int) or not isinstance(end, int):
        raise SubtitleWordTimingError(f"Token mit unlesbaren Zeitstempeln: {token!r}")
    return start, end


def words_from_whisper_json(raw_json: str) -> list[Word]:
    """Baue die Wortliste aus einer whisper-cli ``-ojf``-Rohausgabe.

    Nimmt denselben Rohtext entgegen wie
    :func:`matrix_auto_cutter.shorts.transcript.parse_segments`. Sondertokens
    (``[_BEG_]``, ``[_TT_50]`` u. ae., erkennbar an der ``[_...]``-Klammerung)
    tragen keine echte Zeitspanne und werden uebersprungen, kein stilles
    Verwerten als Wort.
    """
    payload = json.loads(raw_json)
    segments = payload.get("transcription", []) if isinstance(payload, dict) else []
    words: list[Word] = []
    current_start = 0
    current_end = 0
    current_text = ""
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        for token in segment.get("tokens", []):
            if not isinstance(token, dict):
                continue
            text = str(token.get("text", ""))
            if _SPECIAL_TOKEN.match(text.strip()):
                continue
            start_ms, end_ms = _token_offsets(token)
            if text.startswith(" ") or current_text == "":
                if current_text:
                    words.append(Word(current_start, current_end, current_text))
                current_start = start_ms
                current_end = end_ms
                current_text = text.strip()
            else:
                current_end = end_ms
                current_text += text
    if current_text:
        words.append(Word(current_start, current_end, current_text))
    return words


def _line_text(words: Sequence[Word]) -> str:
    return " ".join(word.text for word in words)


def build_subtitle_lines(words: Sequence[Word]) -> list[SubtitleLine]:
    """Bilde Einblendzeilen aus einer Wortliste nach den Stufe-4-Regeln.

    Eine Zeile schliesst, bevor sie mehr als :data:`MAX_WORDS_PER_LINE`
    Woerter oder mehr als :data:`MAX_CHARS_PER_LINE` Zeichen tragen wuerde,
    bevor eine Pause von mehr als :data:`MAX_GAP_MS` zum vorigen Wort liegt,
    und direkt nach einem Wort, das mit einem Satzzeichen aus
    :data:`SENTENCE_END_CHARS` endet. Zeilen ueberlappen nie: die Luecke
    zwischen zwei Zeilen bleibt leer, es wird nichts interpoliert.
    """
    lines: list[SubtitleLine] = []
    current: list[Word] = []
    force_break = False
    for word in words:
        if current:
            gap_ms = word.start_ms - current[-1].end_ms
            candidate_text = _line_text([*current, word])
            exceeds_gap = gap_ms > MAX_GAP_MS
            exceeds_count = len(current) + 1 > MAX_WORDS_PER_LINE
            exceeds_chars = len(candidate_text) > MAX_CHARS_PER_LINE
            if force_break or exceeds_gap or exceeds_count or exceeds_chars:
                lines.append(
                    SubtitleLine(current[0].start_ms, current[-1].end_ms, tuple(current))
                )
                current = []
        current.append(word)
        force_break = current[-1].text.endswith(SENTENCE_END_CHARS)
    if current:
        lines.append(SubtitleLine(current[0].start_ms, current[-1].end_ms, tuple(current)))
    return lines
