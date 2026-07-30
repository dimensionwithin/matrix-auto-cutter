# Matrix Auto Cutter

Automated, conservative post-production for long-form market-analysis videos.

Matrix Auto Cutter takes a raw multi-hour recording, transcribes it locally, and produces a
tightened edit — removing dead time and filler while **deliberately protecting the pauses
that matter** (e.g. silence while reading a chart on screen). It never publishes blindly:
every cut decision is written to a reviewable edit list before anything is rendered.

The whole thing runs **offline** — local speech transcription only, no cloud API and no
internet requirement.

---

## Why it exists

Editing long trading/analysis videos by hand is slow and repetitive, but naive
"silence removal" tools cut the wrong things — they strip out the deliberate pauses where
you're reading a chart or letting a point land. This tool encodes that domain knowledge:
it distinguishes *unwanted* dead time from *intentional* pauses, and stays conservative by
default so it never over-cuts.

---

## How it works

A phased pipeline, built to be auditable at each stage:

1. **Transcribe** — local speech-to-text; every transcribed segment is timestamped
   (from/to) so the editor knows exactly when each word was spoken.
2. **Analyze** — classify gaps into removable dead time vs. protected reading/thinking pauses.
3. **Protect** — protection gating keeps flagged regions (chart-reading pauses, etc.) intact.
4. **Plan** — schedule CTA overlays and assemble the edit.
5. **Review** — emit an **EDL / JSON / HTML** review artifact for a human to check.
6. **Finalize / render** — produce the final cut only after the plan is confirmed.

Core modules: `timebase`, `journal`, `sidecar`, `protection`, `calibration`, `atomic`,
plus a `phase2` subsystem (probe → source-hash confirmation → protection gating →
finalize / render).

---

## Engineering

This project is built to production discipline rather than as a throwaway script:

- **`Python 3.12`**, packaged with Hatchling
- **`Pydantic v2`** models throughout
- **`mypy --strict`** and **`ruff`** clean
- **100% branch coverage** enforced via `pytest` + `pytest-cov`
- **Property-based testing** with `Hypothesis`
- Test code exceeds production code — the pipeline is designed to be provably safe before
  it ever touches a render.

---

## Status

Actively developed; the core pipeline works. Local desktop tool — no cloud, no telemetry.

---

## License

MIT — see [LICENSE](LICENSE).
