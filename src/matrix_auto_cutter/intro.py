"""Label-bound intro resolution and frame-exact lead-in removal.

Mirror image of :mod:`matrix_auto_cutter.outro`: the outro resolves the *last*
bound scene event and discards everything after its protected block, this module
resolves the *first* bound scene event and discards everything before it.

Two deliberate differences to the outro contract are recorded here because they
are decisions, not omissions:

* There is no local binding file and no OBS scene-collection cross-check.  The
  intro scene is bound by its journal label, with the stable OBS scene UUID as
  the fallback layer.  A wrong intro binding removes lead-in material; a wrong
  outro binding would keep unpublished material, which is why only the outro
  carries the heavier evidence chain.
* Several matching events are not an error.  The first occurrence is the intro
  by definition and it removes the least material, so it wins and the plural is
  recorded as its own status instead of failing closed.

The resolution works on the source frame axis via ``mapped_source_frame``, never
on ``monotonic_ns``: the two clocks sit roughly 283 ms apart (the offset between
the output start signal and the first video frame) and the renderer counts
frames.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from matrix_auto_cutter.event_lag import pipeline_lag_frames
from matrix_auto_cutter.models import CanonicalModel, CanonicalUuid4, Sha256
from matrix_auto_cutter.sidecar import SidecarEvent, SidecarEventV12, ValidatedObsEventSidecar

INTRO_SCENE_LABEL = "Intro with Cam"
INTRO_SCENE_UUID = UUID("df50e171-befb-4d89-b9e9-66a29dd0865e")

# Abstand vom *sichtbaren* Szenenanfang bis zum Schnitt, in Frames bei 60 FPS.
#
# Die Bezugsgröße ist bewusst nicht mehr die Journalmarke, sondern der Frame, an
# dem die Szene wirklich im Bild steht: ``marker + pipeline_lag_frames``.  Der
# Lag schwankt laufabhängig zwischen 16 und 63 Frames und wird seit dieser
# Fassung gerechnet statt in diese Konstante eingebacken (siehe
# :mod:`matrix_auto_cutter.event_lag`).  Genau diese Vermischung war der Fehler:
# eine feste Zahl kann eine laufabhängige Größe nicht treffen.
#
# Was hier übersprungen wird, ist der Vorlauf von
# ``intro-sting-sovereign-1440p.webm``.  Die Datei liegt als Medienquelle in der
# Szene „Intro with Cam" und startet mit ihrer Aktivierung: erst tippt sich die
# Kopfzeile ein, dann zeichnet sich die Chartlinie, ab ihrem Frame 187 steht die
# Karte.  Der Zielpunkt ist **der Anfang der Chart-Animation, rund 5 % der
# Linie** — nicht die stehende Karte, die wäre viel zu spät.
#
# Am 09.08.2026 abends an drei Läufen gemessen, Schnitt jeweils bei Marke + 148,
# der Abstand zum sichtbaren Szenenanfang ist damit 148 - Lag:
#
#   16-50-21   Lag 63   ->   85 Frames   -> vom Nutzer als richtig abgenommen
#   17-45-21   Lag 16   ->  132 Frames   -> zu spät, Chart zu rund 70 % gezeichnet
#   17-52-38   Lag 16   ->  132 Frames   -> dito
#
# Die Zahl hängt am Sting, nicht an einem Szenenübergang: die Sammlung schaltet
# mit „Schnitt" (100 ms), einen Stingerwisch gibt es an dieser Stelle nicht.
# Wird der Sting neu gerendert, wandert sie mit seiner Animation und wird am
# Anfang des gerenderten Videos nachjustiert.
INTRO_CUT_OFFSET_FRAMES = 85

# Geschützte Einstiegszone hinter dem Intro-Schnitt (7,5 s bei 60 FPS).  Der
# Nutzer gestaltet den Einstieg bewusst; in diesem Fenster wird nicht
# geschnitten.
#
# Die Zone ist fest und wird nicht mehr aus den Stilledaten abgeleitet.  Der
# Lauf vom 09.08.2026 zeigte warum: hinter dem Schnitt läuft das Intro mit
# Musik, also ohne erkennbare Stille, und die Pause davor beziehungsweise
# danach lag außerhalb jeder abgeleiteten Zone — der Cutter nahm alles bis zum
# ersten Wort weg.  silencedetect trennt laut von leise, nicht Intro von
# Gespräch; eine feste Länge trifft den gestalteten Einstieg zuverlässiger als
# jede Ableitung daraus.  Die Zahl wird am Ergebnis nachjustiert.
INTRO_FLOW_PROTECTED_FRAMES = 450

type IntroBindingBasis = Literal["scene_name", "scene_uuid"]
type IntroResolvedStatus = Literal["resolved", "resolved_first_of_multiple"]

_RESOLVED_STATUSES: frozenset[str] = frozenset({"resolved", "resolved_first_of_multiple"})


class IntroResolutionEvidence(CanonicalModel):
    """Bounded typed result; the absent label is a normal, non-failing outcome."""

    status: Literal[
        "resolved",
        "resolved_first_of_multiple",
        "no_matching_scene_event",
        "nothing_before_intro",
        "event_out_of_bounds",
        "overlaps_outro_tail",
    ]
    sidecar_sha256: Sha256
    binding_basis: IntroBindingBasis | None = None
    scene_event_id: CanonicalUuid4 | None = None
    scene_uuid: CanonicalUuid4 | None = None
    scene_name: str | None = Field(default=None, max_length=200)
    intro_start_frame: int | None = Field(default=None, ge=0)
    removed_frames: int | None = Field(default=None, ge=0)
    removed_ms: int | None = Field(default=None, ge=0)
    matching_scene_event_count: int = Field(default=0, ge=0)
    total_source_frames: int = Field(ge=1)
    # Nachrechenbar machen, was den Schnitt verschiebt: ohne dieses Feld stünde
    # im Proposal wieder eine Zahl, die niemand gegen das Journal prüfen kann.
    # Abwesend in Proposal-1.1-Bytes, gesetzt ab 1.2.
    pipeline_lag_frames: int | None = Field(default=None, ge=0)

    @model_serializer(mode="wrap")
    def omit_unavailable_fields(self, handler: SerializerFunctionWrapHandler) -> object:
        """Represent unavailable typed evidence by field absence, never JSON null."""
        serialized: dict[str, object] = handler(self)
        for name in (
            "binding_basis",
            "scene_event_id",
            "scene_uuid",
            "scene_name",
            "intro_start_frame",
            "removed_frames",
            "removed_ms",
            "pipeline_lag_frames",
        ):
            if getattr(self, name) is None:
                serialized.pop(name, None)
        return serialized


class IntroCandidateEvidence(CanonicalModel):
    """Immutable evidence for the sole frame-exact lead-in candidate."""

    sidecar_sha256: Sha256
    binding_basis: IntroBindingBasis
    scene_event_id: CanonicalUuid4
    scene_uuid: CanonicalUuid4 | None = None
    scene_name: str | None = Field(default=None, max_length=200)
    intro_start_frame: int = Field(gt=0)
    removed_frames: int = Field(gt=0)
    removed_ms: int = Field(gt=0)
    matching_scene_event_count: int = Field(ge=1)
    total_source_frames: int = Field(ge=1)
    resolution_status: IntroResolvedStatus

    @model_validator(mode="after")
    def exact_frame_contract(self) -> IntroCandidateEvidence:
        """Require the half-open lead-in ``[0, intro_start_frame)`` arithmetic."""
        if (
            self.removed_frames != self.intro_start_frame
            or self.removed_ms != removed_milliseconds(self.removed_frames)
            or not self.intro_start_frame < self.total_source_frames
            or (self.matching_scene_event_count > 1)
            != (self.resolution_status == "resolved_first_of_multiple")
        ):
            raise ValueError("intro candidate frames violate the exact lead-in contract")
        return self

    @model_serializer(mode="wrap")
    def omit_unavailable_fields(self, handler: SerializerFunctionWrapHandler) -> object:
        """Keep an absent 1.1 scene UUID or scene name out of canonical JSON."""
        serialized: dict[str, object] = handler(self)
        for name in ("scene_uuid", "scene_name"):
            if getattr(self, name) is None:
                serialized.pop(name, None)
        return serialized


def removed_milliseconds(frames: int) -> int:
    """Convert removed frames to milliseconds exactly as the cut list does."""
    return int((Decimal(frames) * Decimal(1000) / Decimal(60)).to_integral_value())


def _resolution(payload: Mapping[str, object]) -> IntroResolutionEvidence:
    """Validate a typed resolution mapping while retaining bounded outcomes."""
    return IntroResolutionEvidence.model_validate(payload)


def _optional_scene_uuid(event: SidecarEvent) -> UUID | None:
    if not isinstance(event, SidecarEventV12):
        return None
    return event.scene_uuid if isinstance(event.scene_uuid, UUID) else None


def _optional_scene_name(event: SidecarEvent) -> str | None:
    return event.scene_name if isinstance(event.scene_name, str) else None


def _first(events: list[SidecarEvent]) -> SidecarEvent:
    """Pick the earliest event on the frame axis; sidecar order is not ordered."""
    return min(
        events,
        key=lambda item: (
            item.mapped_source_frame,
            item.clock_sample.monotonic_ns,
            str(item.event_id),
        ),
    )


def is_resolved(resolution: IntroResolutionEvidence) -> bool:
    """Report whether this resolution authorizes exactly one lead-in cut."""
    return resolution.status in _RESOLVED_STATUSES


def resolve_intro(
    sidecar: ValidatedObsEventSidecar,
    *,
    sidecar_sha256: str,
    outro_tail_start_frame: int | None = None,
) -> IntroResolutionEvidence:
    """Resolve the first bound intro scene event, or report why no cut is made."""
    total = sidecar.source.video_frame_count
    lag = pipeline_lag_frames(sidecar)
    common: dict[str, object] = {
        "sidecar_sha256": sidecar_sha256,
        "total_source_frames": total,
        "pipeline_lag_frames": lag,
    }
    scenes = [event for event in sidecar.events if event.type == "scene_changed"]
    basis: IntroBindingBasis = "scene_name"
    matching = [event for event in scenes if _optional_scene_name(event) == INTRO_SCENE_LABEL]
    if not matching:
        basis = "scene_uuid"
        matching = [event for event in scenes if _optional_scene_uuid(event) == INTRO_SCENE_UUID]
    if not matching:
        # The label is absent from most journals; that is a normal run, not a fault.
        return _resolution({"status": "no_matching_scene_event", **common})
    event = _first(matching)
    bound: dict[str, object] = {
        "binding_basis": basis,
        "scene_event_id": event.event_id,
        "matching_scene_event_count": len(matching),
        **common,
    }
    scene_uuid = _optional_scene_uuid(event)
    if scene_uuid is not None:
        bound["scene_uuid"] = scene_uuid
    scene_name = _optional_scene_name(event)
    if scene_name is not None:
        bound["scene_name"] = scene_name
    marker = event.mapped_source_frame
    if marker == 0:
        # Die Szene lag beim Aufnahmestart schon im Programm; es gibt keine
        # Vorszene und damit keinen Vorlauf, den man wegnehmen könnte.  Geprüft
        # wird die rohe Marke: der Lag verschiebt den sichtbaren Anfang, nicht
        # die Tatsache, dass davor nichts liegt.
        return _resolution({"status": "nothing_before_intro", "intro_start_frame": 0, **bound})
    # Sichtbarer Szenenanfang, dann der gemessene Vorlauf des Stings.
    start = marker + lag + INTRO_CUT_OFFSET_FRAMES
    if start >= total:
        # Deckt beides ab: eine Marke jenseits der Quelle und eine so späte
        # Marke, dass erst der Versatz über das Ende hinausreicht.
        return _resolution({"status": "event_out_of_bounds", **bound})
    if outro_tail_start_frame is not None and start > outro_tail_start_frame:
        # A lead-in reaching into the outro tail would produce overlapping cuts.
        return _resolution({"status": "overlaps_outro_tail", "intro_start_frame": start, **bound})
    return _resolution(
        {
            "status": "resolved" if len(matching) == 1 else "resolved_first_of_multiple",
            "intro_start_frame": start,
            "removed_frames": start,
            "removed_ms": removed_milliseconds(start),
            **bound,
        }
    )
