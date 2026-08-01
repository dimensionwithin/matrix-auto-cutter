"""Cross-language acceptance check for journals emitted by the native harness."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path


def main() -> int:
    """Run the harness and validate its bytes with the existing Phase-2F loader."""
    harness = Path(sys.argv[1]).resolve()
    repository = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(repository / "src"))
    from matrix_auto_cutter.models import SourceBinding, SourceIdentity, _json_mapping_payload
    from matrix_auto_cutter.phase2.finalizer.loader import LoadedJournal, _parse_journal
    from matrix_auto_cutter.phase2.finalizer.models import (
        FinalizationIntent,
        JournalInputProfile,
        UnavailableProvenance,
        finalization_key,
    )
    from matrix_auto_cutter.phase2.finalizer.sidecar_builder import build_sidecar
    from matrix_auto_cutter.phase2.source_confirmation.identity import source_identity_digest
    from matrix_auto_cutter.sidecar import validate_sidecar

    def build_and_check_sidecar(
        loaded: LoadedJournal,
        recording: Path,
        *,
        duration_ms: int,
        frame_count: int,
        end_reason: str,
        expect_resume: bool,
    ) -> None:
        identity = SourceIdentity(
            file_name=recording.name,
            size_bytes=1,
            sha256="a" * 64,
            duration_ms=duration_ms,
            video_frame_count=frame_count,
            fps_num=60,
            fps_den=1,
            video_start_time_ns=0,
            audio_start_time_ns=0,
            binding=SourceBinding.DIRECT_MP4,
        )
        provisional = FinalizationIntent.model_construct(
            finalizer_run_id="2e157a84-2e31-49d9-b64e-494c24f8f612",
            finalized_at=datetime(2026, 8, 2, tzinfo=UTC),
            project_id="550e8400-e29b-41d4-a716-446655440000",
            input_profile=JournalInputProfile.LEGACY,
            recording_id=loaded.recording_id,
            journal_sha256=loaded.sha256,
            journal_size_bytes=loaded.size_bytes,
            bundle_binding=UnavailableProvenance(),
            source_identity=identity,
            source_identity_digest=source_identity_digest(identity),
            source_identity_evidence_id="b" * 64,
            source_identity_evidence_digest="c" * 64,
            source_volume_id="0000000000000001",
            source_file_id="01" + "00" * 15,
            probe_artifact_id="native-contract-probe",
            hash_artifact_id="native-contract-hash",
            assignment_artifact_id="not_available",
            bundle_schema_version="not_available",
            target_path_digest="d" * 64,
            target_generation="22222222-2222-4222-8222-222222222222",
            synthetic_stop_event_id="44444444-4444-4444-8444-444444444444",
            finalization_key="0" * 64,
        )
        values = provisional.model_dump()
        values["finalization_key"] = finalization_key(provisional)
        intent = FinalizationIntent.model_validate(values)
        sidecar = build_sidecar(loaded, intent)
        if not hasattr(sidecar, "pause_intervals"):
            raise AssertionError(sidecar)
        if len(sidecar.pause_intervals) != 1 or sidecar.pause_intervals[0].end_reason != end_reason:
            raise AssertionError("native journal produced the wrong sidecar pause interval")
        event_types = [event.type for event in sidecar.events]
        if event_types.count("recording_paused") != 1 or (
            event_types.count("recording_resumed") != (1 if expect_resume else 0)
        ):
            raise AssertionError("native pause/resume events were not preserved in the sidecar")
        validated = validate_sidecar(json.loads(sidecar.model_dump_json()), identity)
        if validated.mode != "validated_sidecar_1_1":
            raise AssertionError("native journal sidecar did not validate as Sidecar 1.1")
        if str(sidecar.recording_session_id) != loaded.recording_id:
            raise AssertionError("journal and sidecar session IDs differ")

    with tempfile.TemporaryDirectory(prefix="matrix-native-journal-") as temporary:
        root = Path(temporary)
        journal = root / "native.recording-journal.ndjson"
        recording = root / "native.mp4"
        completed = subprocess.run(
            [
                str(harness),
                "--journal",
                str(journal),
                "--recording",
                str(recording),
                "--duration-ns",
                "6000000000",
                "--final-frame-count",
                "360",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        data = journal.read_bytes()
        loaded = _parse_journal(data, None)
        if not isinstance(loaded, LoadedJournal):
            raise AssertionError(loaded)
        lines = data[:-1].split(b"\n")
        records = [json.loads(line) for line in lines]
        canonical_pairs = zip(records, lines, strict=True)
        if any(
            _json_mapping_payload(record).encode("utf-8") != line
            for record, line in canonical_pairs
        ):
            raise AssertionError("native records are not byte-canonical")
        if [record["sequence"] for record in records] != list(range(len(records))):
            raise AssertionError("native writer sequence is not contiguous")
        if records[0]["producer"]["version"] != "0.2.0-native-standalone":
            raise AssertionError("journal does not carry native harness provenance")
        if sum(record["record_type"] == "calibration_sample" for record in records) != 2:
            raise AssertionError("two-second active calibration cadence is missing")
        before = data
        repeated = subprocess.run(
            [
                str(harness),
                "--journal",
                str(journal),
                "--recording",
                str(recording),
                "--duration-ns",
                "6000000000",
                "--final-frame-count",
                "360",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if repeated.returncode == 0 or journal.read_bytes() != before:
            raise AssertionError("native producer overwrote an existing journal")

        long_journal = root / "native-long.recording-journal.ndjson"
        long_run = subprocess.run(
            [
                str(harness),
                "--journal",
                str(long_journal),
                "--recording",
                str(recording),
                "--duration-ns",
                "5400000000000",
                "--final-frame-count",
                "324000",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if long_run.returncode != 0:
            raise AssertionError(long_run.stderr)
        long_data = long_journal.read_bytes()
        if not isinstance(_parse_journal(long_data, None), LoadedJournal):
            raise AssertionError("90-minute native journal was rejected")
        long_records = [json.loads(line) for line in long_data[:-1].split(b"\n")]
        anchors = [
            record["monotonic_ns"]
            for record in long_records
            if record["record_type"] in {"event", "calibration_sample", "stop"}
        ]
        if max(right - left for left, right in pairwise(anchors)) > 2_000_000_000:
            raise AssertionError("90-minute active calibration cadence exceeded two seconds")

        paused_journal = root / "native-pause.recording-journal.ndjson"
        paused_run = subprocess.run(
            [
                str(harness),
                "--journal",
                str(paused_journal),
                "--recording",
                str(recording),
                "--duration-ns",
                "8000000000",
                "--final-frame-count",
                "360",
                "--pause-start-ns",
                "2000000000",
                "--resume-ns",
                "4000000000",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if paused_run.returncode != 0:
            raise AssertionError(paused_run.stderr)
        paused_records = [
            json.loads(line) for line in paused_journal.read_bytes()[:-1].split(b"\n")
        ]
        paused_loaded = _parse_journal(paused_journal.read_bytes(), None)
        if not isinstance(paused_loaded, LoadedJournal):
            raise AssertionError("native pause/resume journal was rejected")
        pauses = [record for record in paused_records if record["record_type"] == "pause"]
        resumes = [record for record in paused_records if record["record_type"] == "resume"]
        if (
            len(pauses) != 1
            or len(resumes) != 1
            or not pauses[0]["recording_paused"]
            or resumes[0]["recording_paused"]
            or resumes[0]["output_frame_count"] - pauses[0]["output_frame_count"] > 2
        ):
            raise AssertionError("native pause/resume records are not canonical")
        build_and_check_sidecar(
            paused_loaded,
            recording,
            duration_ms=6000,
            frame_count=360,
            end_reason="resumed",
            expect_resume=True,
        )

        paused_stop_journal = root / "native-pause-stop.recording-journal.ndjson"
        paused_stop_run = subprocess.run(
            [
                str(harness),
                "--journal",
                str(paused_stop_journal),
                "--recording",
                str(recording),
                "--duration-ns",
                "4000000000",
                "--final-frame-count",
                "120",
                "--pause-start-ns",
                "2000000000",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if paused_stop_run.returncode != 0:
            raise AssertionError(paused_stop_run.stderr)
        paused_stop_data = paused_stop_journal.read_bytes()
        paused_stop_loaded = _parse_journal(paused_stop_data, None)
        if not isinstance(paused_stop_loaded, LoadedJournal):
            raise AssertionError("native pause/stop journal was rejected")
        paused_stop_records = [json.loads(line) for line in paused_stop_data[:-1].split(b"\n")]
        if any(record["record_type"] == "resume" for record in paused_stop_records):
            raise AssertionError("native pause/stop journal synthesized a resume")
        if not paused_stop_records[-1]["recording_paused"]:
            raise AssertionError("native pause/stop record did not retain recording_paused=true")
        build_and_check_sidecar(
            paused_stop_loaded,
            recording,
            duration_ms=2000,
            frame_count=120,
            end_reason="recording_stopped_while_paused",
            expect_resume=False,
        )
    print("native journal accepted by the existing Phase-2F legacy loader")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
