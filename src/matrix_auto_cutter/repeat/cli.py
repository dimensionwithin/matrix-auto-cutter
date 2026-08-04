"""CLI entry point: ``python -m matrix_auto_cutter.repeat.cli``. No interaction."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from matrix_auto_cutter.repeat.detect import DetectionParams
from matrix_auto_cutter.repeat.diagnostics import build_diagnostics, write_diagnostics
from matrix_auto_cutter.repeat.errors import RepeatContractError
from matrix_auto_cutter.repeat.transcript import load_transcript


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m matrix_auto_cutter.repeat.cli",
        description=(
            "Erkenne benachbarte Wiederholungen und Selbstkorrekturen in einem Transkript."
        ),
    )
    parser.add_argument("--transcript", required=True, help="Pfad zur repeat_transcript/1.0-Datei")
    parser.add_argument("--out", required=True, help="Zielpfad der repeat_diagnostics/1.0-Datei")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate a transcript, detect adjacent repeats, and write diagnostics atomically."""
    args = _parser().parse_args(argv)
    try:
        transcript = load_transcript(args.transcript)
        document = build_diagnostics(transcript, DetectionParams())
        result = write_diagnostics(args.out, document)
    except RepeatContractError as exc:
        print(f"Vertragsfehler: {exc}", file=sys.stderr)
        return 1
    if result.status != "written":
        print(f"Fehler beim Schreiben: {result.error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
