"""Frame-exact, source-bound OUTRO-1B contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from matrix_auto_cutter.cut_proposal import FfmpegProcessResult, ProposalReady, generate_proposal
from matrix_auto_cutter.models import ProtectionLevel
from matrix_auto_cutter.outro import (
    OutroSceneBindingContent,
    binding_bytes,
    binding_file_sha256,
    binding_from_content,
    collection_matches,
    resolve_outro,
)
from matrix_auto_cutter.protection import materialize_protection_with_outro
from matrix_auto_cutter.selection import SelectionReady, ensure_selection
from matrix_auto_cutter.sidecar import ObsEventSidecar, ObsEventSidecarV12, validate_sidecar

OUTRO_UUID = "444eb885-e589-4338-832c-8f5fd7eaaf41"
OUTRO_EVENT = "ccceb885-e589-4338-832c-8f5fd7eaaf41"


def _binding() -> object:
    return binding_from_content(
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


def _binding_and_collection(tmp_path: Path) -> tuple[Path, Path]:
    binding = _binding()
    binding_path = tmp_path / "outro-scene-binding.json"
    binding_path.write_bytes(binding_bytes(binding))
    collection = tmp_path / "Unbenannt.json"
    collection.write_text(
        json.dumps({"sources": [{"id": "scene", "name": "Outro", "uuid": OUTRO_UUID}]}),
        encoding="utf-8",
    )
    return binding_path, collection


def _sidecar(raw_sidecar: dict[str, object], *, start: int, total: int) -> ObsEventSidecarV12:
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
    raw["schema_version"] = "1.2"
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
    events.insert(
        1,
        {
            "event_id": OUTRO_EVENT,
            "type": "scene_changed",
            "mapped_source_frame": start,
            "uncertainty_ms": 100,
            "scene_uuid": OUTRO_UUID,
            "scene_name": "Outro",
            "clock_sample": {
                "monotonic_ns": start * 16_666_667,
                "output_frame_count": start,
                "mapping_basis": "output_frame_counter",
            },
            "protection": events[0]["protection"],
        },
    )
    return ObsEventSidecarV12.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    ("total", "protected_end", "tail"),
    [(899, 899, None), (900, 900, None), (901, 900, 900)],
)
def test_exact_900_frame_boundaries(
    tmp_path: Path,
    raw_sidecar: dict[str, object],
    total: int,
    protected_end: int,
    tail: int | None,
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    result = resolve_outro(
        _sidecar(raw_sidecar, start=0, total=total),
        sidecar_sha256="a" * 64,
        binding_path=binding_path,
        collection_file=collection,
    )
    assert result.status == "resolved"
    assert result.protected_start_frame == 0
    assert result.protected_end_frame == protected_end
    assert result.tail_start_frame == tail


def test_resolution_rejects_ambiguity_and_name_uuid_mismatch(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    sidecar = _sidecar(raw_sidecar, start=100, total=1200)
    assert resolve_outro(
        sidecar, sidecar_sha256="a" * 64, binding_path=binding_path, collection_file=collection
    ).status == "resolved"
    raw = json.loads(sidecar.model_dump_json())
    raw["events"].append(deepcopy(raw["events"][1]))
    raw["events"][-1]["event_id"] = str(uuid4())
    raw["events"][-1]["clock_sample"]["monotonic_ns"] += 1
    assert resolve_outro(
        ObsEventSidecarV12.model_validate_json(json.dumps(raw)),
        sidecar_sha256="a" * 64,
        binding_path=binding_path,
        collection_file=collection,
    ).status == "ambiguous_scene_events"
    raw = json.loads(sidecar.model_dump_json())
    raw["events"][1]["scene_name"] = "outro"
    assert resolve_outro(
        ObsEventSidecarV12.model_validate_json(json.dumps(raw)),
        sidecar_sha256="a" * 64,
        binding_path=binding_path,
        collection_file=collection,
    ).status == "scene_name_mismatch"


def test_binding_canonicality_and_collection_identity_are_strict(tmp_path: Path) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    binding = _binding()
    assert binding_path.read_bytes() == binding_bytes(binding)
    assert binding_file_sha256(binding) == hashlib.sha256(binding_path.read_bytes()).hexdigest()
    assert collection_matches(binding, collection)
    collection.write_text(
        json.dumps(
            {"sources": [
                {"id": "scene", "name": "Outro", "uuid": OUTRO_UUID},
                {"id": "scene", "name": "Outro", "uuid": str(uuid4())},
            ]}
        ),
        encoding="utf-8",
    )
    assert not collection_matches(binding, collection)
    with pytest.raises(ValidationError):
        binding.model_validate_json(
            binding_bytes(binding).replace(b"}\n", b',"unknown":1}\n')
        )


def test_scene_uuid_is_sidecar_only_and_legacy_is_readable(
    raw_sidecar: dict[str, object]
) -> None:
    expected = ObsEventSidecar.model_validate_json(json.dumps(raw_sidecar)).source
    legacy = validate_sidecar(raw_sidecar, expected)
    assert legacy.mode == "validated_sidecar_1_1"
    wrong = deepcopy(raw_sidecar)
    event = wrong["events"][0]
    assert isinstance(event, dict)
    event["scene_uuid"] = OUTRO_UUID
    assert validate_sidecar(wrong, expected).mode == (
        "no_sidecar_safe_mode"
    )


def test_sidecar_v11_never_authorizes_outro_even_with_exact_scene_name(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    v12 = _sidecar(raw_sidecar, start=100, total=1200)
    legacy = json.loads(v12.model_dump_json())
    legacy["schema_version"] = "1.1"
    event = legacy["events"][1]
    assert isinstance(event, dict)
    del event["scene_uuid"]
    sidecar = ObsEventSidecar.model_validate_json(json.dumps(legacy))
    assert resolve_outro(
        sidecar,
        sidecar_sha256="a" * 64,
        binding_path=binding_path,
        collection_file=collection,
    ).status == "sidecar_missing_scene_uuid"


def test_sidecar_v12_scene_uuid_contract_is_versioned_and_strict(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    sidecar = _sidecar(raw_sidecar, start=100, total=1200)
    canonical = sidecar.model_dump_json()
    assert ObsEventSidecarV12.model_validate_json(canonical).model_dump_json() == canonical
    expected = sidecar.source
    assert validate_sidecar(json.loads(canonical), expected).mode == "validated_sidecar_1_2"

    without_uuid = json.loads(canonical)
    event = without_uuid["events"][1]
    assert isinstance(event, dict)
    del event["scene_uuid"]
    missing_uuid = ObsEventSidecarV12.model_validate_json(json.dumps(without_uuid))
    assert resolve_outro(
        missing_uuid,
        sidecar_sha256="a" * 64,
        binding_path=binding_path,
        collection_file=collection,
    ).status == "sidecar_missing_scene_uuid"

    for field, value in (("scene_uuid", None), ("scene_uuid", "not-a-uuid"), ("unknown", 1)):
        invalid = json.loads(canonical)
        target = invalid["events"][1]
        assert isinstance(target, dict)
        target[field] = value
        with pytest.raises(ValidationError):
            ObsEventSidecarV12.model_validate_json(json.dumps(invalid))

    wrong_type = json.loads(canonical)
    target = wrong_type["events"][0]
    assert isinstance(target, dict)
    target["scene_uuid"] = OUTRO_UUID
    assert validate_sidecar(wrong_type, expected).mode == "no_sidecar_safe_mode"


def test_exact_protection_is_hard_and_partitioned(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    sidecar = _sidecar(raw_sidecar, start=100, total=1200)
    resolution = resolve_outro(
        sidecar, sidecar_sha256="a" * 64, binding_path=binding_path, collection_file=collection
    )
    ranges = materialize_protection_with_outro(sidecar, resolution).ranges
    protected = {
        frame
        for item in ranges
        if item.level == ProtectionLevel.HARD and item.policy.blocks_time_edits
        for frame in range(item.source_start_frame, item.source_end_frame)
    }
    assert set(range(100, 1000)).issubset(protected)


def test_tail_candidate_flows_through_immutable_proposal_and_selection(
    tmp_path: Path, raw_sidecar: dict[str, object]
) -> None:
    binding_path, collection = _binding_and_collection(tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    raw = json.loads(_sidecar(raw_sidecar, start=100, total=1200).model_dump_json())
    source_identity = raw["source"]
    assert isinstance(source_identity, dict)
    source_identity.update(
        {
            "file_name": source.name,
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    )
    sidecar = source.with_suffix(".obs-events.json")
    sidecar.write_bytes(
        (ObsEventSidecarV12.model_validate_json(json.dumps(raw)).model_dump_json() + "\n").encode()
    )
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"ffmpeg")

    def fake_process(arguments: object, _timeout: int) -> FfmpegProcessResult:
        values = tuple(arguments)  # type: ignore[arg-type]
        if "-version" in values:
            return FfmpegProcessResult(0, b"ffmpeg version test\n")
        return FfmpegProcessResult(
            0,
            b"silence_start: 17.0\nsilence_end: 19.0 | silence_duration: 2.0\n",
        )

    result = generate_proposal(
        source,
        sidecar,
        str(raw["recording_session_id"]),
        tmp_path / "artifacts",
        ffmpeg,
        process_runner=fake_process,
        outro_binding_path=binding_path,
        obs_scene_collections_root=tmp_path,
    )
    assert isinstance(result, ProposalReady)
    tail = [item for item in result.proposal.proposed_cuts if item.reason == "outro_excess_tail"]
    assert len(tail) == 1
    assert (tail[0].start_frame, tail[0].end_frame) == (1000, 1200)
    assert tail[0].outro_evidence is not None
    assert str(tail[0].outro_evidence.scene_uuid) == OUTRO_UUID
    selection = ensure_selection(result.proposal_path)
    assert isinstance(selection, SelectionReady)
    assert selection.selection.candidates[-1].candidate_id == tail[0].candidate_id
    assert selection.selection.candidates[-1].enabled
