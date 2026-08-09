"""Frame-exact, label-bound INTRO lead-in contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from matrix_auto_cutter.cut_proposal import (
    PROPOSAL_FILE_NAME,
    AnalysisParameters,
    CutProposal,
    CutProposalContent,
    FfmpegProcessResult,
    ProposalReady,
    ProposedCut,
    generate_proposal,
    load_proposal,
    proposal_bytes,
    proposal_content_digest,
)
from matrix_auto_cutter.intro import (
    INTRO_CUT_OFFSET_FRAMES,
    INTRO_FLOW_PROTECTED_FRAMES,
    INTRO_SCENE_LABEL,
    INTRO_SCENE_UUID,
    IntroCandidateEvidence,
    removed_milliseconds,
    resolve_intro,
)
from matrix_auto_cutter.outro import OutroSceneBindingContent, binding_bytes, binding_from_content
from matrix_auto_cutter.render import KeepSegment, build_keep_segments
from matrix_auto_cutter.selection import SelectionReady, ensure_selection
from matrix_auto_cutter.sidecar import ObsEventSidecar, ObsEventSidecarV12

OUTRO_UUID = "444eb885-e589-4338-832c-8f5fd7eaaf41"
OTHER_SCENE_UUID = "11115555-e589-4338-832c-8f5fd7eaaf41"
SIDECAR_SHA = "a" * 64

# Recording ff2618be carried the marker at output frame 3080; the journal clock
# read 51,633 s at the same record.  The frame axis is the authority here.
FF2618BE_FRAME = 3080
# Geschnitten wird hinter dem Stinger, nicht auf der Marke.
FF2618BE_CUT = FF2618BE_FRAME + INTRO_CUT_OFFSET_FRAMES


def _scene_event(
    frame: int,
    *,
    scene_name: str | None = None,
    scene_uuid: str | None = None,
    protection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": str(uuid4()),
        "type": "scene_changed",
        "mapped_source_frame": frame,
        "uncertainty_ms": 100,
        "clock_sample": {
            "monotonic_ns": frame * 16_666_667,
            "output_frame_count": frame,
            "mapping_basis": "output_frame_counter",
        },
        "protection": protection,
    }
    if scene_name is not None:
        event["scene_name"] = scene_name
    if scene_uuid is not None:
        event["scene_uuid"] = scene_uuid
    return event


def _raw(
    raw_sidecar: dict[str, object],
    *,
    total: int,
    scenes: tuple[dict[str, Any], ...],
    schema_version: str = "1.2",
) -> dict[str, Any]:
    raw = deepcopy(raw_sidecar)
    source = raw["source"]
    clock = raw["clock"]
    events = raw["events"]
    producer = raw["producer"]
    assert (
        isinstance(source, dict)
        and isinstance(clock, dict)
        and isinstance(events, list)
        and isinstance(producer, dict)
    )
    producer["obs_version"] = "32.1.2"
    raw["schema_version"] = schema_version
    source["video_frame_count"] = total
    source["duration_ms"] = total * 1000 // 60
    clock["counter_end"] = total
    for event in events:
        assert isinstance(event, dict)
        if event["type"] == "recording_stopped":
            event["mapped_source_frame"] = total
            sample = event["clock_sample"]
            assert isinstance(sample, dict)
            sample["output_frame_count"] = total
            sample["monotonic_ns"] = total * 16_666_667
    hard = events[0]["protection"]
    for index, scene in enumerate(scenes, start=1):
        scene["protection"] = deepcopy(hard)
        if schema_version == "1.1":
            scene.pop("scene_uuid", None)
        events.insert(index, scene)
    return raw


def _sidecar_v12(raw_sidecar: dict[str, object], **kwargs: Any) -> ObsEventSidecarV12:
    return ObsEventSidecarV12.model_validate_json(json.dumps(_raw(raw_sidecar, **kwargs)))


def _sidecar_v11(raw_sidecar: dict[str, object], **kwargs: Any) -> ObsEventSidecar:
    return ObsEventSidecar.model_validate_json(
        json.dumps(_raw(raw_sidecar, schema_version="1.1", **kwargs))
    )


def _outro_binding(tmp_path: Path) -> tuple[Path, Path]:
    binding = binding_from_content(
        OutroSceneBindingContent(
            artifact_type="matrix_auto_cutter_outro_scene_binding",
            schema_version="1.0",
            purpose="outro",
            scene_collection_name="Unbenannt",
            scene_name="Outro",
            scene_uuid=OUTRO_UUID,
            expected_obs_major=32,
            expected_obs_product_version="32.1.2",
        )
    )
    binding_path = tmp_path / "outro-scene-binding.json"
    binding_path.write_bytes(binding_bytes(binding))
    collection = tmp_path / "Unbenannt.json"
    collection.write_text(
        json.dumps({"sources": [{"id": "scene", "name": "Outro", "uuid": OUTRO_UUID}]}),
        encoding="utf-8",
    )
    return binding_path, collection


def _fake_ffmpeg(silences: bytes) -> Any:
    def runner(arguments: object, _timeout: int) -> FfmpegProcessResult:
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version test\n")
        return FfmpegProcessResult(0, silences)

    return runner


def _proposal(
    tmp_path: Path,
    raw: dict[str, Any],
    *,
    silences: bytes = b"",
    outro_binding: Path | None = None,
    collections_root: Path | None = None,
    parameters: AnalysisParameters | None = None,
) -> ProposalReady:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    payload = deepcopy(raw)
    source_identity = payload["source"]
    assert isinstance(source_identity, dict)
    source_identity.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    model = (
        ObsEventSidecarV12 if payload["schema_version"] == "1.2" else ObsEventSidecar
    ).model_validate_json(json.dumps(payload))
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_bytes((model.model_dump_json() + "\n").encode())
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    result = generate_proposal(
        source,
        sidecar,
        str(payload["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        parameters=parameters,
        process_runner=_fake_ffmpeg(silences),
        outro_binding_path=outro_binding or (tmp_path / "__absent__.json"),
        obs_scene_collections_root=collections_root or tmp_path,
    )
    assert isinstance(result, ProposalReady)
    return result


def test_label_binds_the_intro_on_the_frame_axis(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(
            _scene_event(
                FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL, scene_uuid=str(INTRO_SCENE_UUID)
            ),
        ),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert resolution.binding_basis == "scene_name"
    assert resolution.intro_start_frame == FF2618BE_CUT
    assert resolution.removed_frames == FF2618BE_CUT
    assert resolution.removed_ms == 53_800
    assert resolution.matching_scene_event_count == 1


def test_the_stinger_offset_moves_the_cut_behind_the_marker(
    raw_sidecar: dict[str, object],
) -> None:
    """Der Schnitt liegt hinter der Szenenmarke, es wird mehr weggenommen.

    OBS schreibt ``scene_changed`` beim Umschalten, der Stinger wischt danach
    noch.  Im ersten echten Lauf standen deshalb rund 30 Frames der Vorszene am
    Anfang des Videos.
    """
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(1500, scene_name=INTRO_SCENE_LABEL),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert INTRO_CUT_OFFSET_FRAMES == 148
    assert resolution.intro_start_frame == 1500 + INTRO_CUT_OFFSET_FRAMES
    assert resolution.removed_frames == 1648
    assert resolution.removed_frames > 1500


def test_the_offset_alone_can_push_the_marker_out_of_bounds(
    raw_sidecar: dict[str, object],
) -> None:
    """Eine Marke kurz vor Schluss: erst der Versatz reicht über das Ende."""
    inside = 6000 - INTRO_CUT_OFFSET_FRAMES - 1
    resolved = resolve_intro(
        _sidecar_v12(
            raw_sidecar,
            total=6000,
            scenes=(_scene_event(inside, scene_name=INTRO_SCENE_LABEL),),
        ),
        sidecar_sha256=SIDECAR_SHA,
    )
    assert resolved.status == "resolved"
    assert resolved.intro_start_frame == 5999

    beyond = resolve_intro(
        _sidecar_v12(
            raw_sidecar,
            total=6000,
            scenes=(_scene_event(inside + 1, scene_name=INTRO_SCENE_LABEL),),
        ),
        sidecar_sha256=SIDECAR_SHA,
    )
    # Die Marke selbst liegt noch in der Quelle, der Schnittpunkt nicht mehr.
    assert beyond.status == "event_out_of_bounds"
    assert beyond.intro_start_frame is None
    assert beyond.removed_frames is None


def test_label_binds_without_a_scene_uuid_on_sidecar_1_1(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v11(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert resolution.binding_basis == "scene_name"
    assert resolution.scene_uuid is None
    assert resolution.intro_start_frame == FF2618BE_CUT


def test_scene_uuid_is_the_fallback_layer(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(900, scene_name="Umbenannt", scene_uuid=str(INTRO_SCENE_UUID)),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert resolution.binding_basis == "scene_uuid"
    assert resolution.scene_name == "Umbenannt"
    assert resolution.intro_start_frame == 1048


def test_scene_uuid_fallback_survives_a_scene_event_without_any_name(
    raw_sidecar: dict[str, object],
) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(900, scene_uuid=str(INTRO_SCENE_UUID)),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved"
    assert resolution.binding_basis == "scene_uuid"
    assert resolution.scene_name is None
    assert "scene_name" not in json.loads(resolution.model_dump_json())


def test_candidate_evidence_rejects_inconsistent_lead_in_arithmetic() -> None:
    exact: dict[str, Any] = {
        "sidecar_sha256": SIDECAR_SHA,
        "binding_basis": "scene_name",
        "scene_event_id": str(uuid4()),
        "scene_name": INTRO_SCENE_LABEL,
        "intro_start_frame": FF2618BE_FRAME,
        "removed_frames": FF2618BE_FRAME,
        "removed_ms": removed_milliseconds(FF2618BE_FRAME),
        "matching_scene_event_count": 1,
        "total_source_frames": 6000,
        "resolution_status": "resolved",
    }
    assert IntroCandidateEvidence.model_validate(exact).removed_frames == FF2618BE_FRAME
    for override in (
        {"removed_frames": FF2618BE_FRAME - 1},
        {"removed_ms": removed_milliseconds(FF2618BE_FRAME) + 1},
        {"total_source_frames": FF2618BE_FRAME},
        {"matching_scene_event_count": 2},
        {"resolution_status": "resolved_first_of_multiple"},
    ):
        with pytest.raises(ValidationError):
            IntroCandidateEvidence.model_validate({**exact, **override})


def test_first_occurrence_wins_and_is_its_own_status(raw_sidecar: dict[str, object]) -> None:
    # Deliberately out of chronological order: the sidecar does not guarantee one.
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(
            _scene_event(4200, scene_name=INTRO_SCENE_LABEL),
            _scene_event(1500, scene_name=INTRO_SCENE_LABEL),
            _scene_event(3000, scene_name=INTRO_SCENE_LABEL),
        ),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "resolved_first_of_multiple"
    assert resolution.matching_scene_event_count == 3
    assert resolution.intro_start_frame == 1648


def test_missing_label_is_a_normal_run(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(1500, scene_name="Hauptszene", scene_uuid=OTHER_SCENE_UUID),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "no_matching_scene_event"
    assert resolution.intro_start_frame is None
    assert resolution.matching_scene_event_count == 0
    assert "intro_start_frame" not in json.loads(resolution.model_dump_json())


def test_marker_at_frame_zero_removes_nothing(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(0, scene_name=INTRO_SCENE_LABEL),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "nothing_before_intro"
    assert resolution.removed_frames is None


def test_marker_beyond_the_source_is_out_of_bounds(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(6000, scene_name=INTRO_SCENE_LABEL),),
    )
    resolution = resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA)
    assert resolution.status == "event_out_of_bounds"
    assert resolution.removed_frames is None


def test_a_lead_in_reaching_into_the_outro_tail_is_refused(raw_sidecar: dict[str, object]) -> None:
    sidecar = _sidecar_v12(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(5000, scene_name=INTRO_SCENE_LABEL),),
    )
    assert (
        resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA, outro_tail_start_frame=4000).status
        == "overlaps_outro_tail"
    )
    # Entschieden wird über den versetzten Schnittpunkt, nicht über die Marke:
    # bei einem Tail auf der Marke selbst reicht der Versatz hinein.
    assert (
        resolve_intro(sidecar, sidecar_sha256=SIDECAR_SHA, outro_tail_start_frame=5000).status
        == "overlaps_outro_tail"
    )
    assert (
        resolve_intro(
            sidecar,
            sidecar_sha256=SIDECAR_SHA,
            outro_tail_start_frame=5000 + INTRO_CUT_OFFSET_FRAMES,
        ).status
        == "resolved"
    )


def test_lead_in_flows_through_the_immutable_proposal_and_selection(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),),
    )
    ready = _proposal(tmp_path, raw)
    lead_in = [item for item in ready.proposal.proposed_cuts if item.reason == "intro_lead_in"]
    assert len(lead_in) == 1
    assert (lead_in[0].start_frame, lead_in[0].end_frame) == (0, FF2618BE_CUT)
    assert lead_in[0].start_timecode == "00:00:00.000"
    assert lead_in[0].end_timecode == "00:00:53.800"
    assert lead_in[0].duration_ms == removed_milliseconds(FF2618BE_CUT)
    evidence = lead_in[0].intro_evidence
    assert evidence is not None
    assert evidence.removed_frames == FF2618BE_CUT
    assert evidence.resolution_status == "resolved"
    assert ready.proposal.intro_resolution is not None
    assert ready.proposal.intro_resolution.status == "resolved"
    selection = ensure_selection(ready.proposal_path)
    assert isinstance(selection, SelectionReady)
    assert selection.selection.candidates[0].candidate_id == lead_in[0].candidate_id
    assert selection.selection.candidates[0].enabled


def test_generated_lead_in_records_the_plural_marker_as_its_own_status(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(
            _scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),
            _scene_event(4200, scene_name=INTRO_SCENE_LABEL),
        ),
    )
    ready = _proposal(tmp_path, raw)
    lead_in = ready.proposal.proposed_cuts[0]
    assert lead_in.reason == "intro_lead_in"
    assert (lead_in.start_frame, lead_in.end_frame) == (0, FF2618BE_CUT)
    assert lead_in.intro_evidence is not None
    assert lead_in.intro_evidence.resolution_status == "resolved_first_of_multiple"
    assert lead_in.intro_evidence.matching_scene_event_count == 2
    assert ready.proposal.intro_resolution is not None
    assert ready.proposal.intro_resolution.status == "resolved_first_of_multiple"


def test_proposed_cut_refuses_a_lead_in_without_matching_evidence(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),),
    )
    lead_in = _proposal(tmp_path, raw).proposal.proposed_cuts[0]
    canonical = json.loads(lead_in.model_dump_json())
    assert canonical["reason"] == "intro_lead_in"
    for override in (
        {"protection_result": "clear_no_blocking_overlap"},
        {"start_frame": 1},
        {"reason": "outro_excess_tail"},
    ):
        with pytest.raises(ValidationError):
            ProposedCut.model_validate({**canonical, **override})
    without_evidence = {key: value for key, value in canonical.items() if key != "intro_evidence"}
    with pytest.raises(ValidationError):
        ProposedCut.model_validate(without_evidence)


def test_missing_label_leaves_the_generated_proposal_untouched(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(1500, scene_name="Hauptszene", scene_uuid=OTHER_SCENE_UUID),),
    )
    ready = _proposal(
        tmp_path,
        raw,
        silences=b"silence_start: 20.0\nsilence_end: 25.0 | silence_duration: 5.0\n",
    )
    assert all(item.reason != "intro_lead_in" for item in ready.proposal.proposed_cuts)
    assert ready.proposal.intro_resolution is not None
    assert ready.proposal.intro_resolution.status == "no_matching_scene_event"
    assert ready.proposal.status == "ready"
    # Ohne Marke gibt es weder einen Versatz noch eine Einstiegszone: der
    # Stille-Cut steht unverschoben da, wo ihn die Handles hinlegen.
    assert [item.reason for item in ready.proposal.rejected_candidates] == []
    assert [(item.start_frame, item.end_frame) for item in ready.proposal.proposed_cuts] == [
        (1221, 1479)
    ]
    assert "intro_resolution" in json.loads(ready.proposal_path.read_bytes())
    # The runner, the review window and the renderer all re-read the published
    # bytes; only that route revalidates through a dict and can reject them.
    reloaded = load_proposal(ready.proposal_path)
    assert isinstance(reloaded, ProposalReady), getattr(reloaded, "message_de", "")
    assert reloaded.proposal == ready.proposal


def _without_intro_resolution(ready: ProposalReady, target: Path) -> Path:
    """Rebuild the exact 1.1 bytes a build without intro support published."""
    payload = json.loads(ready.proposal_path.read_bytes())
    del payload["proposal_digest"]
    assert payload.pop("intro_resolution")["status"] == "no_matching_scene_event"
    content = CutProposalContent.model_validate_json(json.dumps(payload))
    payload["proposal_digest"] = proposal_content_digest(content)
    legacy = CutProposal.model_validate_json(json.dumps(payload))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(proposal_bytes(legacy))
    return target


def test_a_proposal_1_1_published_before_the_intro_field_still_loads(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    """Reproduce the productive failure: 1.1 bytes that predate ``intro_resolution``.

    Recording e9eb8e4d was analyzed by a runner started before this module existed
    and published Proposal-1.1 without the field; the review window then refused
    its own artifact with ``proposal-1.1 requires typed outro and intro resolution``.
    """
    binding_path, _collection = _outro_binding(tmp_path)
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(4500, scene_name="Outro", scene_uuid=OUTRO_UUID),),
    )
    ready = _proposal(tmp_path, raw, outro_binding=binding_path)
    assert ready.proposal.intro_resolution is not None
    legacy_path = _without_intro_resolution(ready, tmp_path / "legacy" / PROPOSAL_FILE_NAME)

    loaded = load_proposal(legacy_path)
    assert isinstance(loaded, ProposalReady), getattr(loaded, "message_de", "")
    assert loaded.proposal.schema_version == "1.1"
    assert loaded.proposal.intro_resolution is None
    assert loaded.proposal.outro_resolution is not None
    assert "intro_resolution" not in json.loads(legacy_path.read_bytes())


def test_proposal_1_1_still_requires_the_outro_resolution(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    """The intro relaxation may not weaken the outro half of the same rule."""
    binding_path, _collection = _outro_binding(tmp_path)
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(4500, scene_name="Outro", scene_uuid=OUTRO_UUID),),
    )
    ready = _proposal(tmp_path, raw, outro_binding=binding_path)
    payload = json.loads(ready.proposal_path.read_bytes())
    del payload["proposal_digest"]
    del payload["outro_resolution"]
    with pytest.raises(ValidationError, match="requires typed outro resolution"):
        CutProposalContent.model_validate_json(json.dumps(payload))


def test_silence_before_the_marker_is_superseded_not_proposed(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),),
    )
    ready = _proposal(
        tmp_path,
        raw,
        # 20-25 s lies inside the lead-in, 70-75 s well behind it.
        silences=(
            b"silence_start: 20.0\nsilence_end: 25.0 | silence_duration: 5.0\n"
            b"silence_start: 70.0\nsilence_end: 75.0 | silence_duration: 5.0\n"
        ),
    )
    superseded = [
        item
        for item in ready.proposal.rejected_candidates
        if item.reason == "superseded_by_intro_lead_in"
    ]
    assert len(superseded) == 1
    assert superseded[0].raw_silence_start_ms == 20_000
    reasons = [item.reason for item in ready.proposal.proposed_cuts]
    assert reasons == ["intro_lead_in", "conservative_silence_dead_air"]
    counts = {item.reason: item.count for item in ready.proposal.rejection_counts}
    assert counts["superseded_by_intro_lead_in"] == 1


def test_a_micro_island_behind_the_lead_in_is_dropped_so_the_render_stays_possible(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    """Die Inselregel bleibt hinter der festen Zone erreichbar.

    Mit den Standardwerten deckt die 450-Frame-Zone die 30-Frame-Inselgrenze
    vollständig ab: was die Insel verwerfen würde, ist längst geschützt. Erst
    eine Inselgrenze jenseits der Zone macht die Regel wieder sichtbar — und sie
    muss dann greifen, sonst weigert sich der Renderer über einem Mikrosegment.
    """
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(3000, scene_name=INTRO_SCENE_LABEL),),
    )
    ready = _proposal(
        tmp_path,
        raw,
        # Der Schnitt liegt bei 3148, die Zone endet auf 3598. Der Kandidat
        # beginnt auf 3600 und ist damit nicht geschützt; zur Lead-in-Grenze
        # bleiben 452 Frames, unter der hier gesetzten Inselgrenze von 600.
        silences=b"silence_start: 59.65\nsilence_end: 65.0 | silence_duration: 5.35\n",
        parameters=AnalysisParameters(minimum_keep_island_ms=10_000),
    )
    assert [item.reason for item in ready.proposal.proposed_cuts] == ["intro_lead_in"]
    assert [item.reason for item in ready.proposal.rejected_candidates] == ["minimum_keep_island"]
    assert build_keep_segments(ready.proposal) == (KeepSegment(start_frame=3148, end_frame=6000),)


def test_intro_and_outro_apply_in_the_same_run_without_touching_the_outro(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, _collection = _outro_binding(tmp_path)
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(
            # The outro refuses a 1.2 sidecar in which any scene event lacks its
            # UUID, so both markers carry one here.
            _scene_event(
                FF2618BE_FRAME,
                scene_name=INTRO_SCENE_LABEL,
                scene_uuid=str(INTRO_SCENE_UUID),
            ),
            _scene_event(4500, scene_name="Outro", scene_uuid=OUTRO_UUID),
        ),
    )
    ready = _proposal(
        tmp_path,
        raw,
        # 20-25 s liegt vor dem Lead-in, 62-67 s hinter der Einstiegszone
        # (Frame 3678) und vor dem Schutzblock des Outros (Frame 4500): dieser
        # Schnitt bleibt und belegt, dass beide Zonen einander nicht stören.
        silences=(
            b"silence_start: 20.0\nsilence_end: 25.0 | silence_duration: 5.0\n"
            b"silence_start: 62.0\nsilence_end: 67.0 | silence_duration: 5.0\n"
        ),
        outro_binding=binding_path,
    )
    cuts = ready.proposal.proposed_cuts
    assert [item.reason for item in cuts] == [
        "intro_lead_in",
        "conservative_silence_dead_air",
        "outro_excess_tail",
    ]
    assert (cuts[0].start_frame, cuts[0].end_frame) == (0, FF2618BE_CUT)
    # The outro tail keeps its own arithmetic: 4500 + 900 protected frames.
    assert (cuts[-1].start_frame, cuts[-1].end_frame) == (5400, 6000)
    assert cuts[-1].outro_evidence is not None
    assert cuts[-1].outro_evidence.outro_start_frame == 4500
    assert ready.proposal.outro_resolution is not None
    assert ready.proposal.outro_resolution.status == "resolved"
    previous_end = 0
    for item in cuts:
        assert item.start_frame >= previous_end
        previous_end = item.end_frame


# Marke 600 plus Versatz: der Intro-Schnitt endet auf Frame 748, die feste
# Einstiegszone reicht damit bis ausschließlich Frame 1198.
FLOW_MARKER = 600
FLOW_CUT = FLOW_MARKER + INTRO_CUT_OFFSET_FRAMES
FLOW_END = FLOW_CUT + INTRO_FLOW_PROTECTED_FRAMES


def test_the_pause_after_a_musical_intro_is_protected(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    """Der Produktivfall vom 09.08.2026, den die alte Ableitung nicht fing.

    Hinter dem Schnitt läuft das Intro mit Musik, es gibt dort also keine
    erkannte Stille. Die abgeleitete Zone endete deshalb schon am Ausklang des
    Stingers, und die eigentliche Pause vor dem ersten Wort lag ungeschützt —
    der Cutter nahm alles bis zum ersten Wort weg. Die feste Zone schützt sie.
    """
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FLOW_MARKER, scene_name=INTRO_SCENE_LABEL),),
    )
    ready = _proposal(
        tmp_path,
        raw,
        # 12,0-13,5 s ist der leise Ausklang des Stingers rund um den Schnitt;
        # danach läuft die Intromusik. 15,0-17,0 s ist die Pause vor dem ersten
        # Wort — unter der Ableitung endete die Zone auf Frame 810 und dieser
        # Kandidat (921 bis 999) wurde geschnitten. 25,0-30,0 s liegt hinter der
        # Zone und wird normal geschnitten.
        silences=(
            b"silence_start: 12.0\nsilence_end: 13.5 | silence_duration: 1.5\n"
            b"silence_start: 15.0\nsilence_end: 17.0 | silence_duration: 2.0\n"
            b"silence_start: 25.0\nsilence_end: 30.0 | silence_duration: 5.0\n"
        ),
    )
    protected = [
        item for item in ready.proposal.rejected_candidates if item.reason == "intro_flow_protected"
    ]
    assert len(protected) == 1
    assert protected[0].raw_silence_start_ms == 15_000
    counts = {item.reason: item.count for item in ready.proposal.rejection_counts}
    assert counts == {"intro_flow_protected": 1, "superseded_by_intro_lead_in": 1}

    cuts = ready.proposal.proposed_cuts
    assert [item.reason for item in cuts] == ["intro_lead_in", "conservative_silence_dead_air"]
    assert (cuts[0].start_frame, cuts[0].end_frame) == (0, FLOW_CUT)
    assert (cuts[1].start_frame, cuts[1].end_frame) == (1521, 1779)
    # Intro, Musik und die Pause davor bleiben am Stück stehen.
    assert build_keep_segments(ready.proposal) == (
        KeepSegment(start_frame=FLOW_CUT, end_frame=1521),
        KeepSegment(start_frame=1779, end_frame=6000),
    )
    assert isinstance(load_proposal(ready.proposal_path), ProposalReady)


def test_a_candidate_starting_inside_the_zone_is_dropped_whole(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    """Verworfen, nicht gekürzt: sonst fiele der Schnitt doch in die Pause."""
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FLOW_MARKER, scene_name=INTRO_SCENE_LABEL),),
    )
    ready = _proposal(
        tmp_path,
        raw,
        # Kandidat 801 bis 2379: beginnt in der Zone und reicht weit darüber
        # hinaus. Gekürzt würde er ab Frame 1198 schneiden und damit doch noch
        # in den gestalteten Einstieg hinein.
        silences=b"silence_start: 13.0\nsilence_end: 40.0 | silence_duration: 27.0\n",
    )
    assert [item.reason for item in ready.proposal.proposed_cuts] == ["intro_lead_in"]
    assert [item.reason for item in ready.proposal.rejected_candidates] == ["intro_flow_protected"]
    assert ready.proposal.status == "ready"
    assert build_keep_segments(ready.proposal) == (
        KeepSegment(start_frame=FLOW_CUT, end_frame=6000),
    )
    assert isinstance(load_proposal(ready.proposal_path), ProposalReady)


def test_the_zone_boundary_is_half_open(tmp_path: Path, raw_sidecar: dict[str, object]) -> None:
    """``[intro_start, intro_start + 450)``: der Frame der Grenze zählt nicht mehr dazu."""
    raw = _raw(
        raw_sidecar,
        total=6000,
        scenes=(_scene_event(FLOW_MARKER, scene_name=INTRO_SCENE_LABEL),),
    )
    # 19,6 s ergibt nach den Handles Startframe 1197, einen vor der Grenze.
    inside = _proposal(
        tmp_path / "inside",
        raw,
        silences=b"silence_start: 19.6\nsilence_end: 25.0 | silence_duration: 5.4\n",
    )
    assert [item.reason for item in inside.proposal.rejected_candidates] == ["intro_flow_protected"]
    assert [item.reason for item in inside.proposal.proposed_cuts] == ["intro_lead_in"]

    # 19,61 s ergibt Startframe 1198, also genau die Grenze: bleibt erhalten.
    outside = _proposal(
        tmp_path / "outside",
        raw,
        silences=b"silence_start: 19.61\nsilence_end: 25.0 | silence_duration: 5.39\n",
    )
    assert [item.reason for item in outside.proposal.rejected_candidates] == []
    kept = outside.proposal.proposed_cuts[1]
    assert kept.reason == "conservative_silence_dead_air"
    assert kept.start_frame == FLOW_END == 1198
    assert isinstance(load_proposal(outside.proposal_path), ProposalReady)


def test_proposal_identity_separates_two_different_intro_resolutions(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    with_label = _proposal(
        tmp_path / "a",
        _raw(
            raw_sidecar,
            total=6000,
            scenes=(_scene_event(FF2618BE_FRAME, scene_name=INTRO_SCENE_LABEL),),
        ),
    )
    without_label = _proposal(
        tmp_path / "b",
        _raw(
            raw_sidecar,
            total=6000,
            scenes=(_scene_event(FF2618BE_FRAME, scene_name="Hauptszene"),),
        ),
    )
    assert with_label.proposal.proposal_id != without_label.proposal.proposal_id
