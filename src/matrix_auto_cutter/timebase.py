"""Rationale CFR-60-Zeitbasis und halboffene Frameintervalle."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


@dataclass(frozen=True, slots=True)
class FrameRate:
    """Die einzige unterstützte Auto-Cutter-Zeitbasis: 60/1."""

    numerator: int = 60
    denominator: int = 1

    def __post_init__(self) -> None:
        """Verwerfe jede andere Zeitbasis."""
        if (self.numerator, self.denominator) != (60, 1):
            msg = "Nur CFR 60/1 wird unterstützt."
            raise ValueError(msg)

    def protection_start(self, milliseconds: int | Fraction) -> int:
        """Runde einen Schutzstart in Millisekunden nach außen ab."""
        if milliseconds < 0:
            msg = "Zeitwerte dürfen nicht negativ sein."
            raise ValueError(msg)
        return _floor_fraction(Fraction(milliseconds) * self.numerator / (1000 * self.denominator))

    def protection_end(self, milliseconds: int | Fraction) -> int:
        """Runde ein Schutzende in Millisekunden nach außen auf."""
        if milliseconds < 0:
            msg = "Zeitwerte dürfen nicht negativ sein."
            raise ValueError(msg)
        return _ceil_fraction(Fraction(milliseconds) * self.numerator / (1000 * self.denominator))


@dataclass(frozen=True, order=True, slots=True)
class Frame:
    """Nichtnegative ganzzahlige Frameposition."""

    value: int

    def __post_init__(self) -> None:
        """Verwerfe boolesche, nichtganzzahlige und negative Werte."""
        if isinstance(self.value, bool) or not isinstance(self.value, int) or self.value < 0:
            msg = "Ein Frame muss eine nichtnegative Ganzzahl sein."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class FrameRange:
    """Nichtleeres halboffenes Intervall ``[start, end)``."""

    start: Frame
    end: Frame

    def __post_init__(self) -> None:
        """Verwerfe leere und rückwärts laufende Intervalle."""
        if self.start >= self.end:
            msg = "FrameRange erfordert start < end."
            raise ValueError(msg)

    @property
    def length(self) -> int:
        """Anzahl enthaltener Frames."""
        return self.end.value - self.start.value

    def contains(self, frame: Frame) -> bool:
        """Prüfe halboffenes Enthaltensein eines Frames."""
        return self.start <= frame < self.end

    def intersects(self, other: FrameRange) -> bool:
        """Prüfe auf eine nichtleere Schnittmenge."""
        return self.start < other.end and other.start < self.end

    def intersection(self, other: FrameRange) -> FrameRange | None:
        """Liefere die nichtleere Schnittmenge oder ``None``."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return FrameRange(start, end) if start < end else None

    def clamp(self, bounds: FrameRange) -> FrameRange | None:
        """Begrenze das Intervall auf ``bounds``; leere Ergebnisse sind ``None``."""
        return self.intersection(bounds)
