"""Cross-language acceptance check for journals emitted by the native harness."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from itertools import pairwise
from pathlib import Path


def main() -> int:
    """Run the harness and validate its bytes with the existing Phase-2F loader."""
    harness = Path(sys.argv[1]).resolve()
    repository = Path(sys.argv[2]).resolve()
    sys.path.insert(0, str(repository / "src"))
    from matrix_auto_cutter.models import _json_mapping_payload
    from matrix_auto_cutter.phase2.finalizer.loader import LoadedJournal, _parse_journal

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
        if not isinstance(_parse_journal(paused_journal.read_bytes(), None), LoadedJournal):
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
    print("native journal accepted by the existing Phase-2F legacy loader")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
