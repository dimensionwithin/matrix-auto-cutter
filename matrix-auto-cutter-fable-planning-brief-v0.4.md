# Matrix Auto Cutter — Fable Planning Brief v0.4

Status: Planning Brief / Lead-Architect Input  
Owner: Joshua / DimensionWithin  
Project name: Matrix Auto Cutter  
Primary purpose: local automatic video postproduction for Joshua's YouTube longform crypto/chart-analysis videos  
Target first model role: Fable as Lead Architect / Planning Agent  
Implementation role after planning: Opus/Sonnet/Codex or equivalent coding agents  

---

## 1. Why This Version Exists

Version 0.4 updates the previous Fable planning brief with the actual uploaded overlay package plus the newly provided intro and stinger assets.

Important correction:

The overlay assets are not merely static PNGs anymore. Joshua has provided a ZIP package containing WebM track-matte overlays. These should be treated as real available production assets for Version 1 planning.

The package is:

`dimensionwithin-overlays-webm.zip`

The extracted overlay files are listed in the asset manifest:

`matrix-auto-cutter-overlay-manifest-v0.3.json`

---

## 2. Purpose of This Brief

This document is not intended to over-specify every technical decision.

Joshua defines the product goal, creative workflow, hard constraints, asset reality, and quality expectations. Fable should act as the lead architect and decide the best technical approach, libraries, architecture, implementation phases, tests, and risk controls.

Decision hierarchy:

1. Joshua decides **what the product must accomplish**.
2. Fable decides **how the system should be designed**.
3. Opus/Sonnet/Codex execute **implementation tasks from Fable's plan**.

This project should not become blocked by Joshua needing to decide every engineering detail.

---

## 3. Product Summary

Matrix Auto Cutter is a local Windows application that automatically edits Joshua's raw OBS recordings into finished YouTube longform videos.

The tool should analyze the video/audio, transcribe speech, identify silence, filler words, mouth noises, self-corrections, CTA phrases, and important chart-context moments, then automatically create a finished edited video while also producing JSON/HTML review artifacts.

The tool is specialized for Joshua's content format:

- YouTube longform crypto/chart-analysis videos
- OBS recordings
- chart/screen recording as main visual
- small Pepe-avatar facecam in the bottom-right corner
- German + English mixed speech
- premium Matrix Market Theory / DimensionWithin branding
- automatic CTA overlays
- hook → intro → main section → CTA → outro structure

This is not intended to be a general-purpose video editor.

---

## 4. Locked Product Requirements

The following requirements are fixed unless Joshua explicitly changes them.

### 4.1 Project Identity

- Project name: **Matrix Auto Cutter**
- Main use case: **automatic editing of Joshua's YouTube longform chart-analysis videos**
- First version target: **local Windows app**
- First user: **Joshua**
- First content type: **YouTube Longform**

### 4.2 Input Workflow

- Recording software: **OBS**
- Typical input format: **MP4**
- Typical resolution: **2560x1440**
- Typical FPS: **60 FPS**
- Typical raw length: **45 minutes to 1.5 hours**
- Desired final length: **10 to 30 minutes**
- Audio: **one finished audio/video file**
- Visual format: **chart/screen recording with small Pepe-avatar facecam bottom-right**

### 4.3 Local Paths

- Repo/project location: `P:\DimensionWithin\Matrix Auto Cutter`
- Raw video source folder: `F:\VIDEO ROHABLAGE`
- Final export folder: `D:\workspace`
- Existing overlay/design asset folder: `P:\DimensionWithin\Abo-Like Button & New Video Card etc`
- Intro folder: `P:\DimensionWithin\DimensionWithin Intro`
- Outro folder: `P:\DimensionWithin\DimensionWithin Outro`

### 4.4 Local-First Requirement

- The system should run locally by default.
- No API dependency for Version 1.
- Original input video and original audio must never be overwritten.
- Quality is more important than speed.

### 4.5 UI Requirement

- Version 1 should not be CLI-only.
- Version 1 should be clickable/local-app oriented.
- JSON + HTML review artifacts are enough for the first review layer.
- A full professional timeline UI is not required for the first version.

### 4.6 Export Requirement

- Target platform for Version 1: **YouTube**
- Standard export resolution: **2560x1440**
- Standard export FPS: **60 FPS**
- Source resolution should generally be preserved.
- Desired export filename pattern: `YYYY-MM-DD_title_final.mp4`

---

## 5. Product Intent

The tool should automatically:

- cut long silence and non-speech sections
- reduce empty dead air
- preserve meaningful pauses
- preserve chart-reading pauses
- preserve dramatic pauses before important statements
- detect and cut filler words when safe
- detect self-corrections after spoken mistakes
- leave stylistic repetitions alone
- detect mouth noises such as smacking and clicks
- apply reasonable audio cleanup
- preserve original audio/video sources
- detect CTA phrases
- plan branded CTA overlays
- insert hook, intro, main section, CTA, and outro structure
- render a finished YouTube-ready video
- produce review artifacts for decisions

Version 1 should produce a real final edited video automatically, not only suggestions.

However, risky/hard cuts should remain reviewable.

---

## 6. Priority Ranking

Joshua's priority order for Version 1:

1. **Möglichst viel Automatisierung**
2. **Maximale Schnittqualität**
3. **Schöne Benutzeroberfläche**
4. **Schnell funktionierende Pipeline**

Interpretation for Fable:

- Do not build a toy pipeline that technically works but produces poor edits.
- Do not spend months building a beautiful UI before the editing logic works.
- Choose a pragmatic architecture that can produce real edited videos quickly, while keeping quality and automation as the north star.
- Use review artifacts to compensate for not having a full timeline UI in v1.

---

## 7. Editing Style Target

Default longform editing style should be between:

- natural, calm, only obvious pauses removed
- clean and denser, but not hectic

The reference style is calm crypto chart-analysis editing, not hyperactive Shorts editing.

Important protected pause types:

- Denkpausen vor wichtigen Aussagen
- Chart-Zeigepausen
- dramatische Pausen

Chart-analysis content requires the viewer to have enough time to read the chart. The system must not blindly remove every silence.

---

## 8. Filler Words and Speech Cleanup

Words/sounds to detect:

- äh
- ähm
- mhm
- ja
- also
- sozusagen
- im Grunde
- genau

Current preferred behavior:

- "äh" and "ähm" may be cut automatically when safe.
- Do not cut them if they are part of a self-correction context.
- Self-corrections should be detected, e.g. "Bitcoin ist bei 120k... äh nein, 112k."
- Repetitions should generally not be removed, because repetition can be a stylistic device.

Fable should define the exact detection and safety logic.

---

## 9. Mouth Noise and Audio Cleanup

Important noises:

- smacking / Schmatzen
- clicks / Klicks

Version 1 should attempt to automatically process these if technically reasonable, while preserving original audio.

Allowed audio processing:

- de-click
- noise gate
- loudness normalization
- compressor
- other sensible local audio filters if Fable recommends them

Fable should decide the exact audio-processing chain.

---

## 10. CTA Detection and Overlay Assets

CTA types to detect:

- Like
- Abo / Subscribe
- Glocke
- Kommentar
- Kanalmitglied
- Community
- Hyperliquid
- Referral-Link
- Website
- Numerologie

Known phrase:

- "Abo nicht vergessen"

Overlay frequency limits:

- Subscribe/Abo overlay: maximum 2 per video
- Community overlay: maximum 1 per video
- Hyperliquid/Referral overlay: maximum 1 per video

Behavior:

- The tool should directly plan overlays, not only detect CTA moments.
- Overlay decisions should still be visible in JSON/HTML review artifacts.
- Missing assets should be handled gracefully.
- Existing assets should be used automatically where available.

Overlay placement preferences:

- usually bottom-left or top-right
- do not cover bottom-right, because the Pepe avatar facecam sits there

### 10.1 Uploaded WebM Overlay Package

Joshua has provided a ZIP file containing the following WebM overlays:

| Trigger / Asset Type | File |
|---|---|
| Like | `like.webm` |
| Abo / Subscribe | `abo.webm` |
| Glocke / Bell | `glocke.webm` |
| Kommentar | `kommentar.webm` |
| Kanalmitglied | `kanalmitglied.webm` |
| Community | `community.webm` |
| Hyperliquid | `hyperliquid.webm` |
| Referral | `referral.webm` |
| Website | `website.webm` |
| Numerologie | `numerologie.webm` |
| Lower Third | `lowerthird.webm` |
| Card / New Video Card | `card.webm` |

Important asset details:

- The WebM files use a track-matte style.
- Each file contains the fill on the left half and the black/white mask on the right half.
- These are not standard alpha WebMs until converted or processed.
- FFmpeg should crop the left/right halves and use `alphamerge`.
- `glocke.webm` includes an Opus audio stream.
- Most overlay durations are roughly 2.3–3.35 seconds.
- For final rendering, Fable should decide whether to:
  - convert all overlays to true alpha files during setup, or
  - apply the crop/alphamerge process dynamically during rendering.

Reference FFmpeg concept from the package README:

```bash
ffmpeg -i like.webm -filter_complex "[0:v]crop=iw/2:ih:0:0[f];[0:v]crop=iw/2:ih:iw/2:0[a];[f][a]alphamerge" -c:v libvpx-vp9 -pix_fmt yuva420p like_alpha.webm
```

Fable should create a robust overlay asset pipeline that can register, validate, convert/cache, and apply these assets safely.

---

## 10.2 Uploaded Intro and Stinger Assets

Joshua has now also provided these additional WebM assets:

| Role | File | Notes |
|---|---|---|
| Intro / Intro Sting | `intro-sting-sovereign-1440p.webm` | WebM, 2560x1440, duration approx. 6.046s, codec `vp9`, audio: True |
| Transition Stinger | `stinger-sovereign-desk-2200ms-trackmatte-1440p.webm` | WebM, 5120x1440, duration approx. 2.391s, codec `vp9`, audio: True; filename suggests track-matte format |

Important:

- These assets should be treated as available production assets for Version 1 planning.
- Fable should decide how they fit into the hook → intro → main section → CTA → outro structure.
- Fable should validate whether the stinger uses the same track-matte handling as the CTA overlays.
- Fable should decide whether to convert/cache these assets during setup or process them dynamically during rendering.
- The tool must never overwrite original intro/stinger assets.
- If the outro is still missing, Fable should design graceful fallback behavior: render without outro, use placeholder, or mark missing asset in review artifacts.

The combined asset manifest is:

`matrix-auto-cutter-asset-manifest-v0.4.json`


---

## 11. Chart Context Protection

Known chart-context phrases:

- "interessanter Bereich"
- "interessanter Stelle"

The system should also infer or expand a larger phrase library for chart-analysis language.

Important behavior:

- Chart-context moments should be protected from bad cuts.
- The user allowed cuts inside protected chart moments, but they must be careful.
- Mouse movement/cursor activity should later be considered as an additional chart-context signal.

Fable should decide:

- how many seconds before and after chart-context phrases should be protected
- whether cuts inside protected ranges are shortened rather than removed
- how to rank chart-context confidence
- how to avoid destroying chart explanations

---

## 12. Hook / Intro / Outro Structure

Target video structure:

1. Hook
2. Intro
3. Main section
4. CTA
5. Outro

Desired behavior:

- Intro should be inserted automatically.
- Outro should be inserted automatically.
- There should be a hook before the intro.
- Fable should decide how simple or advanced hook handling should be in Version 1.
- Longform-to-Shorts / automatic highlight extraction is a Version 2 direction, not Version 1 core scope.

---

## 13. Review vs Autopilot

Version 1 should automatically render a final edited video.

Automatic decisions may include:

- long silence cuts
- intro insertion
- outro insertion
- loudness normalization
- filler words when safe
- overlays
- mouth-noise processing

Decisions requiring review:

- hard cuts
- risky cuts
- ambiguous self-corrections
- questionable audio removals
- overlay conflicts

Review format for v1:

- JSON
- HTML

A complex timeline UI is not required in v1.

Undo/restore is not a major priority because originals must be preserved.

---

## 14. Hardware

Known hardware:

- CPU: AMD Ryzen 9 3900X
- GPU: RTX 3060
- RAM: 64 GB 3200 MHz
- Storage: enough free space
- NVIDIA/CUDA availability: unknown

Fable should include a startup hardware/environment check, especially for:

- FFmpeg availability
- GPU/CUDA availability
- transcription model availability
- overlay package availability
- overlay conversion/cache availability
- required local dependencies

Quality should be prioritized over speed.

---

## 15. Version 2 Direction

Version 2 should explore:

- Longform → Shorts generation
- automatic highlight/hook discovery
- project history
- stronger local app feel
- improved UI
- possibly cursor/mouse activity as chart-context signal
- more advanced overlay design integration
- more advanced audio cleanup
- maybe automatic chapters/descriptions if later desired

Note: Joshua currently does not need automatic chapters as a Version 1 requirement.

---

## 16. What Fable Should Decide

Fable should decide the following:

1. Overall architecture
2. MVP phase plan
3. Best local transcription engine
4. Whether to use WhisperX, faster-whisper, Whisper.cpp, or another local stack
5. How to handle German + English mixed speech
6. How to handle word-level timestamps
7. How to detect and cut silence safely
8. How to detect filler words and self-corrections
9. How to process mouth clicks/smacking locally
10. How to structure the edit-decision system
11. How to generate and validate JSON/HTML review artifacts
12. How to render with FFmpeg or alternative tooling
13. How to build a clickable local UI without overcomplicating the first version
14. Whether to use Tauri, Electron, a local web app, or another app stack
15. Whether Git is necessary in the implementation workflow
16. Whether tests should be included from the beginning
17. How to keep original files safe
18. How to structure project folders
19. How to handle missing intro/outro assets
20. How to validate and preprocess the WebM track-matte overlay package
21. How to cache converted alpha overlays
22. How to phase implementation so the project does not explode

---

## 17. What Fable Should Not Do

Fable should not:

- turn this into a general video editor
- require cloud APIs for Version 1
- overwrite original files
- require Joshua to manually make dozens of technical decisions before a plan exists
- prioritize a beautiful UI over working automated editing
- ignore the bottom-right avatar protection zone
- design only a CLI pipeline when Joshua wants a clickable local app
- assume Shorts generation is Version 1 core scope
- hide edit decisions without producing review artifacts
- remove all pauses blindly
- overcompress or overprocess audio until it sounds unnatural
- assume WebM overlays already have normal alpha without checking the track-matte structure

---

## 18. Required Fable Output

Fable's planning output should include:

1. Product interpretation
2. Technical architecture recommendation
3. Recommended local app stack
4. Recommended transcription/audio/video stack
5. Dependency list
6. Data model / project file structure
7. Edit Decision List concept
8. Silence/filler/self-correction strategy
9. Mouth-noise/audio cleanup strategy
10. CTA/overlay strategy
11. WebM track-matte overlay handling strategy
12. Chart-context protection strategy
13. Rendering/export strategy
14. First MVP implementation phases
15. Tests and validation strategy
16. Known risks and mitigations
17. Minimal first coding prompt for implementation agent
18. What not to build yet

Fable should make decisions where Joshua wrote "Fable decides."

---

## 19. Initial Fable Planning Prompt

Use this prompt to start the next planning step.

```text
You are acting as Lead Architect for Matrix Auto Cutter, a local Windows application for automatically editing Joshua's OBS-recorded YouTube longform crypto/chart-analysis videos.

Read the planning brief carefully.

Your task is not to implement code yet. Your task is to create a concrete technical architecture and phased implementation plan.

Joshua has intentionally not decided every engineering detail. You must make the technical decisions that a strong lead architect would make.

Hard product facts:
- Local Windows app
- No cloud/API dependency for Version 1
- Input: OBS MP4 recordings, 2560x1440, 60 FPS
- Raw length: 45–90 minutes
- Desired final length: 10–30 minutes
- Content: chart/screen recording with small Pepe-avatar facecam bottom-right
- Bottom-right area must not be covered by overlays
- Source videos: F:\VIDEO ROHABLAGE
- Exports: D:\workspace
- Repo: P:\DimensionWithin\Matrix Auto Cutter
- Existing assets: P:\DimensionWithin\Abo-Like Button & New Video Card etc
- Uploaded overlay package: dimensionwithin-overlays-webm.zip
- Uploaded intro asset: intro-sting-sovereign-1440p.webm
- Uploaded stinger asset: stinger-sovereign-desk-2200ms-trackmatte-1440p.webm
- Combined asset manifest: matrix-auto-cutter-asset-manifest-v0.4.json
- Overlay package contains WebM track-matte overlays, not simple alpha files
- Version 1 should be clickable/local-app oriented, not CLI-only
- Version 1 should automatically render a final edited YouTube longform video
- Version 1 should also produce JSON/HTML review artifacts
- Original video/audio must never be overwritten
- Quality matters more than speed
- Version 2 can explore Longform-to-Shorts

Desired functionality:
- cut long silence/dead air
- preserve important pauses and chart-reading pauses
- detect/cut filler words when safe
- detect self-corrections
- leave stylistic repetitions alone
- process mouth clicks/smacking if reasonable
- detect CTA phrases
- place branded overlays automatically
- preprocess/apply WebM track-matte overlays correctly
- insert/use intro and stinger assets correctly
- insert hook → intro → main section → CTA → outro structure
- render YouTube-ready output at 2560x1440 60 FPS where possible

Please produce:
1. A recommended architecture.
2. A recommended app/UI stack.
3. A recommended local transcription stack.
4. A recommended audio/video/rendering stack.
5. A project folder/data model.
6. An Edit Decision List design.
7. A detection strategy for silence, filler words, self-corrections, CTA phrases, mouth noises, and chart context.
8. A WebM track-matte overlay processing strategy.
9. A safe rendering strategy.
10. A phased MVP implementation plan.
11. A minimal first coding task for the implementation model.
12. A list of risks and mitigations.
13. A list of decisions you made and why.

Do not build a general-purpose editor. Design a specialized automatic postproduction tool for Joshua's workflow.
```

---

## 20. Recommended Next Step

Give this v0.4 brief, the asset manifest, the uploaded overlay ZIP, and the uploaded intro/stinger files to Fable and ask for a lead-architect plan.

After Fable returns the architecture plan, do not immediately implement everything.

Instead:

1. Review Fable's plan.
2. Compress it into a controlled implementation spec.
3. Start a new implementation thread for the first coding task.
4. Keep the first coding task small and verifiable.
5. Expand only after the first foundation works.
