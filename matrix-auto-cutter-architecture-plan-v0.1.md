# Matrix Auto Cutter — Verbindlicher Architekturplan v0.1

Status: Architektur-Freeze für Version 1  
Stand: 2026-07-12  
Produktvertrag: `matrix-auto-cutter-planning-brief-v0.5.md`  
Assetvertrag: `matrix-auto-cutter-asset-manifest-v0.5.json`

## 1. Architekturentscheidungen

Dieser Plan entscheidet die technische Umsetzung. Genannte Alternativen sind keine gleichwertigen Implementierungsoptionen, sondern bewusst verworfene Wege oder klar begrenzte Fallbacks.

| Bereich | Verbindliche Entscheidung | Kurze Begründung |
|---|---|---|
| Sprache | Python 3.12, 64 Bit | Sehr gutes lokales Medien-/ML-Ökosystem, direkte JSON-/Prozessintegration und auf Windows stabil einsetzbar. |
| UI | PySide6 mit Qt Widgets | Eine native anklickbare Windows-App ohne Browserdienst; Qt Widgets ist für eine status- und formularlastige v1-Oberfläche ausreichend und stabil. PySide6 sind die offiziellen Qt-Python-Bindings ([Qt for Python](https://doc.qt.io/qtforpython-6/)). |
| Kernmodelle | Pydantic 2, aus Modellen exportierte JSON Schemas | Ein Validierungsvertrag für App, Worker, Dateien und Tests; unbekannte Felder werden in kanonischen Artefakten abgewiesen. |
| Konfiguration | TOML für Defaults/User/Projekt-Overrides; JSON für Laufzeitartefakte | TOML ist menschenlesbar; JSON ist eindeutig, schemafähig und direkt reviewbar. |
| Jobarchitektur | Ein GUI-Prozess und genau ein Worker-Subprozess pro aktivem Job; v1 führt maximal einen Medienjob gleichzeitig aus | FFmpeg/ML dürfen die UI nicht blockieren. Ein Job vermeidet GPU-/Datenträgerkonkurrenz und vereinfacht Wiederaufnahme. Prozessbasierte Isolation ist unter Windows zuverlässig ([Python multiprocessing](https://docs.python.org/3.12/library/multiprocessing.html)). |
| Worker-Kommunikation | `QProcess`, UTF-8-NDJSON auf stdout; strukturierte Logs auf Datei; stderr nur für ungefangene Fehler | Fortschritt ist streambar und maschinenlesbar. Der Worker bleibt separat testbar, ohne dass das Produkt CLI-only wird. |
| FFmpeg | Externe, konfigurierte `ffmpeg.exe`/`ffprobe.exe`; Aufruf als Argumentliste ohne Shell; Filtergraph über Datei; Mindestversion 7.0 | FFmpeg ist der einzige Decoder/Encoder/Compositor. Keine fragilen Shellquotings, keine zweite Medienengine. `alphamerge`, `adeclick` und `loudnorm` sind offiziell dokumentiert ([FFmpeg Filters](https://ffmpeg.org/ffmpeg-filters.html)). |
| Transkription | `faster-whisper`, Modell `large-v3`, `word_timestamps=True`, automatische Spracherkennung, kein VAD-bedingtes Entfernen | Gute lokale Whisper-Inferenz und Wortzeiten; Deutsch/Englisch bleiben in einem multilingualen Modell. Wortzeiten werden per Cross-Attention/DTW ausgegeben ([faster-whisper](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)). |
| Rechenpfad | RTX/CUDA mit `float16`, wenn Preflight und Testinferenz bestehen; sonst CPU `int8`; kein automatischer Modelldownload während eines Jobs | Hardwarebeschleunigung ist optional, Reproduzierbarkeit und Offlinefähigkeit bleiben erhalten. |
| Zeitmodell | Halboffene Intervalle `[start_frame, end_frame)` auf einer rationalen `60/1`-Timeline; 48-kHz-Audio entspricht exakt 800 Samples pro Frame | Ganzzahlen verhindern kumulative Rundungsfehler und geben Audio/Video dieselben Grenzen. |
| Persistenz | Dateibasierte, versionierte JSON-Artefakte und atomare Statusdateien; keine Datenbank in v1 | Ein einzelner Nutzer und ein Job benötigen keine Datenbank. Artefakte bleiben transparent, portabel und reviewbar. |
| Rendering | Ein finaler Video-Decodier-/Encode-Durchlauf; Audio wird vorher verlustfrei geschnitten, gemischt und zweipassig gemastert. Ausgabe: H.264 `libx264` CRF 18/preset slow, AAC 320 kbit/s/48 kHz, `+faststart` | Der getrennte PCM-Audiopfad macht zweipassige Lautheit und exakte Sync-Prüfung möglich; Video wird nur einmal encodiert. NVENC ist nicht v1-Default. |
| Review | Kanonisches `review.json` plus eigenständiges statisches `review.html` aus Jinja2-Templates | Browserlesbar ohne Server; dieselben IDs und Werte wie in den Maschinenartefakten. |
| Asset-Cache | SHA-256-adressierter ProRes-4444-Alpha-Cache unter `%LOCALAPPDATA%`; Originale bleiben read-only | Einmalige, validierbare Track-Matte-Konvertierung vermeidet komplexe Wiederholung im finalen Filtergraph. |

Qt Quick, Electron/Tauri, WhisperX, Cloud-ASR, eine interne FFmpeg-Bibliotheksbindung, SQLite und ein professioneller Timeline-Editor werden in v1 nicht verwendet. Das reduziert Laufzeitkopplung, Packaging-Risiko und unnötige UI-Komplexität.

## 2. Architekturübersicht

```text
PySide6 GUI
  ├─ Projekt-/Konfigurationsservice
  ├─ Preflight-Anzeige und Review-Launcher
  └─ QProcess → Job Worker
                 ├─ ffprobe/Medien-Probe
                 ├─ Sidecar-Validator → Protection Resolver
                 ├─ Audio Analyzer
                 ├─ faster-whisper Transcriber
                 ├─ Cut Analyzer → EDL Validator
                 ├─ Timeline Mapper
                 ├─ CTA Classifier → Overlay/Sound Scheduler
                 ├─ Asset Registry → Alpha Cache
                 ├─ FFmpeg Render Planner/Runner
                 └─ Review Builder + Output Verifier
```

Die Analyseebene schreibt unveränderliche Stage-Artefakte. Die Entscheidungsebene erzeugt EDL, Mapping und Overlayplan. Nur der Render Runner darf Medienausgaben schreiben. Das finale Ergebnis wird erst nach technischer Prüfung atomar aus einer `.partial`-Datei veröffentlicht.

## 3. Komponenten und Verantwortlichkeiten

### 3.1 GUI

Die Qt-Widgets-Oberfläche importiert MP4/Sidecar, zeigt Preflight und Schutzmodus, verwaltet Profile, startet oder stoppt einen Job und öffnet Ergebnisse. Sie enthält keine Analysealgorithmen. Ein Stop sendet zuerst eine kooperative Abbruchanforderung, danach `CTRL_BREAK`; nur nach zehn Sekunden wird der Worker beendet. Partielle Ausgaben bleiben als Diagnose markiert und werden nie als final angezeigt.

### 3.2 Project Service

Erzeugt `project.json`, berechnet die Source-Identität, löst sichere Laufzeitpfade auf und schreibt JSON atomar über `datei.tmp` plus `os.replace`. Die Source liegt außerhalb des Projektarbeitsverzeichnisses und wird ausschließlich lesend geöffnet.

### 3.3 Preflight und Media Probe

`ffprobe` liefert Streams, Dauer, Startzeit, Framerate, Sample-Rate, Pixel-/Farbformat und Rotation als JSON. Zeitliches Autoediting ist in v1 nur für eine decodierbare CFR-Quelle mit 60/1 FPS, einer primären Videospur und einer decodierbaren Audiospur aktiv. Abweichungen führen nicht zu stiller Konvertierung: Die App wechselt in Analysis-/Safe-Mode oder weist den Render mit Fehlercode ab.

Preflight prüft außerdem FFmpeg-Version und benötigte Filter/Encoder, Schreibrechte ausschließlich in Arbeits-/Exportpfaden, freien Speicher, Modellbestand und -hash, CUDA-Testinferenz, Asset-/ZIP-Hashes und vorhandenen Alpha-Cache. Der aktuelle Projektbestand belegt nur Assets, keine installierten späteren Laufzeitabhängigkeiten.

### 3.4 OBS Sidecar Validator und Protection Resolver

Der Validator ordnet nur `<mp4-stem>.obs-events.json` neben `<mp4-stem>.mp4` zu. Er prüft Schema, Source-Dateiname, Größe, SHA-256, Dauer (Toleranz ein Frame), monotone Zeitwerte, eindeutige Event-IDs und Eventpaare. Der Resolver bildet Events plus Puffer auf Frameintervalle ab und vereinigt Überlappungen nach Schutzstärke.

### 3.5 Audio Analyzer

FFmpeg dekodiert eine Analyse-WAV als Mono, Float32, 48 kHz. NumPy/SciPy/SoundFile bestimmen 20-ms-RMS, Noise Floor, spektrale Impulse und Nullstellen; FFmpegs `silencedetect` dient als unabhängige Diagnose, nicht als alleiniger Schnittentscheider.

Der adaptive Stille-Schwellwert ist `clamp(noise_floor_dbfs + 6, -55, -42)`. Erst mindestens 1,2 Sekunden kontinuierliche Unterschreitung erzeugen einen Kandidaten. Vor und nach Sprache bleiben mindestens 350 ms stehen; die entfernbare Mitte muss wenigstens 500 ms lang sein. Diese Defaults sind zentral konfiguriert und in jedem Kandidaten materialisiert.

### 3.6 Transcriber

Der Worker verwendet `faster-whisper large-v3` mit Beam Size 5, Temperatur 0, `language=None`, multilingualem Modell, `word_timestamps=True`, `condition_on_previous_text=True` und `vad_filter=False`. Das separate Audiomodul muss Stille sehen; ASR-VAD darf die Source-Timeline nicht verkürzen. Ein konfiguriertes Hotword-Set enthält projektspezifische Begriffe wie Hyperliquid und TruthPill, darf aber keine CTA-Entscheidung erzwingen.

Jedes Wort erhält Text, normalisierte Form, Start-/Endframe, Rohsekunden und Probability. Nicht monotone oder außerhalb der Quelle liegende Wortzeiten sind Validierungsfehler. Die erkannte dominante Sprache ist Metadatum, keine erzwungene Sprache für den gesamten Clip.

### 3.7 Cut Analyzer

Er kombiniert Stille, Wörter, Satzgrenzen, Schutzbereiche und später Füllwort-/Korrektur-/Artefaktsignale. Er schreibt zunächst Kandidaten mit Evidenz und Kontraindikatoren. Nur Kandidaten mit `decision=accept`, gültigem Sidecar und passendem Mindestscore gelangen in die EDL. Kandidaten werden niemals direkt gerendert.

### 3.8 EDL Validator und Timeline Mapper

Der EDL Validator sortiert, begrenzt und normalisiert Schnitte. Er weist Überlappung mit hartem Schutz, nicht positive Dauer, Source-Grenzüberschreitung, zu kurze unbeabsichtigte Keep-Inseln und nicht framegenaue Werte ab. Aus dem Komplement der Cuts entstehen Keep-Segmente. Der Mapper summiert deren Längen und erzeugt eine bijektive Abbildung innerhalb jedes Keep-Segments.

### 3.9 CTA Classifier und Overlay Scheduler

Der CTA Classifier arbeitet ausschließlich auf Source-Wörtern und erzeugt Intent-Kandidaten. Der Scheduler erhält erst nach der EDL das Mapping, verwirft entfernte Intents, plant einen Output-Frame und wendet Schutz-, Position-, Kollisions-, Cooldown- und Frequenzregeln an.

### 3.10 Asset Registry und Alpha Cache

Die Registry liest `matrix-auto-cutter-asset-manifest-v0.5.json`, prüft Package-Hash und ZIP-Mitgliedsnamen gegen Zip-Slip, Größe, Streams, gerade Breite, halbe Effektivbreite und Dauer. Für CTA-WebMs wird Fill links und Maske rechts gecroppt und mit `alphamerge` zu ProRes 4444 (`yuva444p10le`, ohne Audio) konvertiert. Die offizielle Funktion von `alphamerge` ist das Einsetzen einer Graustufenquelle als Alphaebene ([FFmpeg-Dokumentation](https://ffmpeg.org/ffmpeg-filters.html#alphamerge)).

Cache-Key ist SHA-256 über Originalmitgliedbytes, Manifest-Asseteintrag, Konverterprofil und FFmpeg-Major-Version. Cacheinhalt: `overlay.mov`, `probe.json`, `validation.json`. Validierung dekodiert Anfang/Mitte/Ende, prüft nichtleere Alpha-Bounds, identische Dauer innerhalb eines Frames und erwartete Auflösung. Ein fehlgeschlagenes Asset wird gesperrt, nicht dynamisch „best effort“ verwendet. `glocke.webm`-Audio wird nur bei explizit freigegebenem `internal`-Modus separat nach WAV extrahiert; Default bleibt `none`.

### 3.11 Render Planner/Runner und Output Verifier

Der Planner erzeugt `render-plan.json`, Audio- und Video-Filtergraphen als Textdateien und Argumentlisten. Der Runner verwendet nie `shell=True`, liest FFmpeg-Fortschritt über `-progress pipe:1` und erstellt zuerst verlustfreie Audiozwischenstände, danach `<ziel>.partial.mp4`. Der Verifier prüft Streams, Dauer, CFR, Framezahl, Auflösung, Audio, Decodierbarkeit, Loudness/True Peak und A/V-Differenz. Erst danach erfolgt atomare Veröffentlichung.

### 3.12 Review Builder

Der Builder sammelt nur validierte Artefakte, kopiert keine Medien in HTML und escaped alle Transkripttexte. `review.html` funktioniert ohne Server oder Netzwerk. Es zeigt Source- und Output-Zeiten, Gründe, Confidences, Schutzkonflikte, verworfene Entscheidungen, Audiochain, Asset-Hashes, Warnungen und Verifikationsstatus.

## 4. Datenfluss vom Import bis Export

1. GUI wählt die MP4 aus; Project Service erstellt eine neue Projekt-ID und berechnet die unveränderliche Source-Identität.
2. `ffprobe` prüft Medienprofil. Preflight prüft Umgebung, Assets, Modell, Speicher und Sidecar-Zuordnung.
3. Sidecar wird validiert. Fehlt er oder ist er unbrauchbar, setzt der Job `no_sidecar_safe_mode` und verbietet zeitentfernende EDL-Einträge.
4. Protection Resolver erzeugt gepufferte harte und weiche Schutzintervalle auf Source-Frames.
5. FFmpeg erzeugt Analyseaudio. Audio Analyzer erzeugt Stille-/Impulssignale.
6. `faster-whisper` erzeugt Source-Transkript und Wortzeiten.
7. Cut Analyzer erzeugt Kandidaten; Schutz- und Qualitätsregeln erzeugen eine validierte EDL.
8. Timeline Mapper erzeugt Keep-Segmente und Output-Abbildung.
9. CTA Classifier erkennt Source-Intents; Overlay Scheduler mappt nur erhaltene Intents und plant konfliktfreie Output-Ereignisse.
10. Asset Registry validiert beziehungsweise befüllt den Alpha-Cache. Sound Scheduler erstellt einen unabhängigen Audioplan.
11. Render Planner kompiliert die Entscheidung in getrennte Audio-/Videopläne; Runner erzeugt und mastert verlustfreie Audiozwischenstände und rendert danach `.partial.mp4`.
12. Output Verifier prüft die Datei. Review Builder schreibt JSON/HTML. Erst bei Erfolg wird die finale MP4 veröffentlicht.

Jede Stufe besitzt `input_hashes`, `config_hash`, `schema_version` und `completed_at`. Ein Neustart darf eine Stufe nur wiederverwenden, wenn alle drei Vertragswerte identisch sind.

## 5. Datenverträge und JSON-Beispiele

Alle Zeiten in kanonischen Artefakten sind Frames auf `60/1`, alle Intervalle halb offen. Millisekunden kommen nur im OBS-Roh-Sidecar vor und werden einmal konservativ gerundet.

### 5.1 Projektdatei `project.json`

```json
{
  "project_schema_version": "1.0",
  "project_id": "018f2ca1-7e6d-7a62-9f8d-8de26eaf2101",
  "created_at": "2026-07-12T14:30:00+02:00",
  "source": {
    "path": "F:\\VIDEO ROHABLAGE\\aufnahme.mp4",
    "file_name": "aufnahme.mp4",
    "size_bytes": 12003400567,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  },
  "obs_sidecar_path": "F:\\VIDEO ROHABLAGE\\aufnahme.obs-events.json",
  "workspace": "D:\\workspace\\.matrix-auto-cutter\\projects\\018f2ca1-7e6d-7a62-9f8d-8de26eaf2101",
  "export_directory": "D:\\workspace",
  "profile": "youtube_1440p60_v1",
  "config_overrides": {},
  "source_immutable": true
}
```

Pfadfelder müssen absolut und normalisiert sein. `project_id` ist UUIDv7. Source-Pfad und Identität sind nach Projekterstellung unveränderlich.

### 5.2 OBS-Ereignis-Sidecar: konkretes JSON Schema

Dateiname: Für `aufnahme.mp4` zwingend `aufnahme.obs-events.json` im selben Verzeichnis.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://dimensionwithin.local/schemas/obs-events-1.0.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "source", "clock", "events"],
  "properties": {
    "schema_version": {"const": "1.0"},
    "source": {
      "type": "object",
      "additionalProperties": false,
      "required": ["file_name", "size_bytes", "sha256", "duration_ms"],
      "properties": {
        "file_name": {"type": "string", "pattern": "^[^/\\\\]+\\.mp4$"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "duration_ms": {"type": "integer", "minimum": 1}
      }
    },
    "clock": {
      "type": "object",
      "additionalProperties": false,
      "required": ["unit", "origin", "monotonic"],
      "properties": {
        "unit": {"const": "ms"},
        "origin": {"const": "first_encoded_frame"},
        "monotonic": {"const": true}
      }
    },
    "events": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": ["event_id", "type", "source_ms", "protection"],
        "properties": {
          "event_id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,80}$"},
          "type": {
            "enum": [
              "recording_started", "recording_stopped", "scene_changed",
              "intro_started", "intro_ended", "outro_started", "outro_ended",
              "stinger_started", "stinger_ended", "manual_protection"
            ]
          },
          "source_ms": {"type": "integer", "minimum": 0},
          "end_source_ms": {"type": "integer", "minimum": 0},
          "pair_id": {"type": "string", "pattern": "^[A-Za-z0-9._-]{1,80}$"},
          "scene_name": {"type": "string", "maxLength": 200},
          "label": {"type": "string", "maxLength": 500},
          "protection": {
            "type": "object",
            "additionalProperties": false,
            "required": ["level", "buffer_before_ms", "buffer_after_ms", "blocks_overlays"],
            "properties": {
              "level": {"enum": ["hard", "soft"]},
              "buffer_before_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
              "buffer_after_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
              "blocks_overlays": {"type": "boolean"}
            }
          }
        }
      }
    }
  }
}
```

Semantische Regeln zusätzlich zum JSON Schema:

- `recording_started` steht bei 0 ms; `recording_stopped` liegt innerhalb eines Frames der geprobten Dauer.
- `intro_*`, `outro_*` und `stinger_*` benötigen pro Intervall dieselbe eindeutige `pair_id`.
- `manual_protection` darf ein Punktmarker sein oder mit `end_source_ms >= source_ms` ein Intervall bilden.
- `end_source_ms` ist nur bei `manual_protection` zulässig; `pair_id` ist nur bei Intro-/Outro-/Stinger-Start und -Ende zulässig.
- Aufnahmegrenzen, Szenenwechsel, Intro, Outro und Stinger werden unabhängig vom gelieferten `level` als hart und Overlay-blockierend normalisiert; nur manuelle Schutzbereiche dürfen wirksam weich sein.
- Die Arrayreihenfolge ist nicht semantisch; der Validator sortiert eine Kopie. Doppelte IDs sind ungültig.
- Alle Zeitwerte müssen innerhalb der geprobten Source-Dauer liegen.

Repräsentatives Sidecar:

```json
{
  "schema_version": "1.0",
  "source": {
    "file_name": "aufnahme.mp4",
    "size_bytes": 12003400567,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "duration_ms": 3600000
  },
  "clock": {"unit": "ms", "origin": "first_encoded_frame", "monotonic": true},
  "events": [
    {"event_id": "rec-start", "type": "recording_started", "source_ms": 0, "protection": {"level": "hard", "buffer_before_ms": 0, "buffer_after_ms": 1000, "blocks_overlays": true}},
    {"event_id": "intro-a", "type": "intro_started", "pair_id": "intro-1", "source_ms": 8000, "protection": {"level": "hard", "buffer_before_ms": 250, "buffer_after_ms": 0, "blocks_overlays": true}},
    {"event_id": "intro-b", "type": "intro_ended", "pair_id": "intro-1", "source_ms": 14046, "protection": {"level": "hard", "buffer_before_ms": 0, "buffer_after_ms": 250, "blocks_overlays": true}},
    {"event_id": "scene-8", "type": "scene_changed", "source_ms": 900000, "scene_name": "Chart", "protection": {"level": "hard", "buffer_before_ms": 350, "buffer_after_ms": 650, "blocks_overlays": true}},
    {"event_id": "manual-1", "type": "manual_protection", "source_ms": 1200000, "end_source_ms": 1210000, "label": "wichtige Chartpause", "protection": {"level": "soft", "buffer_before_ms": 500, "buffer_after_ms": 1000, "blocks_overlays": false}},
    {"event_id": "rec-stop", "type": "recording_stopped", "source_ms": 3600000, "protection": {"level": "hard", "buffer_before_ms": 1000, "buffer_after_ms": 0, "blocks_overlays": true}}
  ]
}
```

### 5.3 Transkript `transcript.json`

```json
{
  "schema_version": "1.0",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "time_base": {"fps_num": 60, "fps_den": 1},
  "engine": {"name": "faster-whisper", "model": "large-v3", "device": "cuda", "compute_type": "float16"},
  "detected_language": "de",
  "language_probability": 0.93,
  "segments": [
    {
      "segment_id": "seg-000123",
      "start_frame": 72000,
      "end_frame": 72360,
      "text": "Den Hyperliquid Link findest du unten.",
      "words": [
        {"word_id": "w-00421", "text": "Den", "normalized": "den", "start_frame": 72000, "end_frame": 72018, "probability": 0.98},
        {"word_id": "w-00422", "text": "Hyperliquid", "normalized": "hyperliquid", "start_frame": 72019, "end_frame": 72075, "probability": 0.96}
      ]
    }
  ]
}
```

### 5.4 Schnittkandidaten `cut-candidates.json`

```json
{
  "schema_version": "1.0",
  "time_base": {"fps_num": 60, "fps_den": 1},
  "sidecar_mode": "validated",
  "candidates": [
    {
      "candidate_id": "cutcand-0042",
      "kind": "dead_air",
      "source_start_frame": 18000,
      "source_end_frame": 18120,
      "confidence": 0.97,
      "decision": "accept",
      "evidence": ["rms_below_adaptive_threshold_2.0s", "no_words", "no_chart_context"],
      "conflicts": [],
      "retained_handle_before_frames": 21,
      "retained_handle_after_frames": 21
    },
    {
      "candidate_id": "cutcand-0043",
      "kind": "filler",
      "source_start_frame": 22000,
      "source_end_frame": 22018,
      "confidence": 0.71,
      "decision": "review",
      "evidence": ["token_ähm"],
      "conflicts": ["possible_self_correction"]
    }
  ]
}
```

### 5.5 EDL `edl.json`

```json
{
  "schema_version": "1.0",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "time_base": {"fps_num": 60, "fps_den": 1},
  "source_total_frames": 216000,
  "sidecar_mode": "validated",
  "cuts": [
    {
      "edit_id": "edit-0001",
      "source_start_frame": 18000,
      "source_end_frame": 18120,
      "kind": "dead_air",
      "candidate_ids": ["cutcand-0042"],
      "confidence": 0.97,
      "automatic": true
    }
  ],
  "output_total_frames": 215880,
  "validation": {"valid": true, "hard_protection_intersections": 0}
}
```

Im `no_sidecar_safe_mode` muss `cuts` leer sein und `output_total_frames == source_total_frames` gelten.

### 5.6 Source-to-Output-Mapping `timeline-map.json`

```json
{
  "schema_version": "1.0",
  "time_base": {"fps_num": 60, "fps_den": 1},
  "segments": [
    {"keep_id": "keep-0001", "source_start_frame": 0, "source_end_frame": 18000, "output_start_frame": 0, "output_end_frame": 18000},
    {"keep_id": "keep-0002", "source_start_frame": 18120, "source_end_frame": 216000, "output_start_frame": 18000, "output_end_frame": 215880}
  ],
  "removed": [
    {"source_start_frame": 18000, "source_end_frame": 18120, "edit_id": "edit-0001"}
  ]
}
```

Für ein Segment gilt exakt `output_frame = output_start_frame + source_frame - source_start_frame`. Außerhalb aller Keep-Segmente ist das Ergebnis `null`. Umkehrung ist innerhalb eines Segments eindeutig.

### 5.7 Overlay-Events `overlay-events.json`

```json
{
  "schema_version": "1.0",
  "events": [
    {
      "overlay_id": "ov-0012",
      "category": "referral",
      "asset_file": "referral.webm",
      "intent_id": "cta-0088",
      "source_word_ids": ["w-00421", "w-00422"],
      "source_anchor_frame": 72075,
      "output_start_frame": 71967,
      "output_end_frame": 72109,
      "mapping_keep_id": "keep-0002",
      "position": "bottom_left",
      "confidence": 0.91,
      "sound": {"mode": "none", "gain_db": -12.0, "ducking": false},
      "checks": {"frequency": "pass", "collision": "pass", "protected_zone": "pass", "cut_boundary": "pass"}
    }
  ]
}
```

`output_end_frame` ist exklusiv. Die Overlaydauer wird auf Frames aufgerundet; der sichtbare Bereich plus sechs Frames Rand muss vollständig in einem einzigen Keep-Mapping-Segment liegen.

### 5.8 Review-Ergebnis `review.json`

```json
{
  "schema_version": "1.0",
  "project_id": "018f2ca1-7e6d-7a62-9f8d-8de26eaf2101",
  "status": "success_with_warnings",
  "mode": "validated_sidecar",
  "summary": {
    "source_frames": 216000,
    "output_frames": 215880,
    "accepted_cuts": 1,
    "review_cuts": 1,
    "overlays_planned": 1
  },
  "warnings": [
    {"code": "CTA_MEDIUM_CONFIDENCE", "message": "CTA nur im Review", "artifact_id": "cta-0090"}
  ],
  "artifacts": {
    "edl": "artifacts/edl.json",
    "timeline_map": "artifacts/timeline-map.json",
    "overlay_events": "artifacts/overlay-events.json",
    "render_plan": "artifacts/render-plan.json",
    "final_video": "../../../../2026-07-12_aufnahme_final.mp4"
  },
  "verification": {
    "decode": "pass",
    "fps": "60/1",
    "av_sync_max_frames": 1,
    "integrated_lufs": -14.1,
    "true_peak_dbtp": -1.6,
    "source_immutability": "pass"
  }
}
```

## 6. Schnitt- und Schutzregeln

### 6.1 Rundung und Intervallbildung

- Millisekundenstart eines Schutzes wird mit `floor(ms × 60 / 1000)` nach außen abgerundet; sein Ende mit `ceil` aufgerundet.
- Schnittstart wird mit `ceil` nach innen aufgerundet, Schnittende mit `floor` abgerundet.
- Nach Pufferung werden Intervalle auf `[0, source_total_frames)` begrenzt.
- Überlappende harte Bereiche werden vereinigt. Harte Schutzstärke gewinnt gegen weich. `blocks_overlays=true` bleibt bei der Vereinigung erhalten, wenn es ein Teilintervall verlangt.

### 6.2 Sidecar-Fallback und unvollständige Daten

- Nicht vorhandener Sidecar: `no_sidecar_safe_mode`, keine zeitentfernenden Auto-Edits.
- Falscher Name, Hash, Dateigröße oder Dauer: Sidecar vollständig unbrauchbar, keine Auto-Edits.
- JSON-/Schemafehler, unbekannter Eventtyp, negative/außerhalb liegende Zeit oder doppelte Event-ID: Sidecar vollständig unbrauchbar.
- Fehlender `recording_started`/`recording_stopped`: Grenzen aus Probe ableiten, Warnung; andere valide Ereignisse bleiben verwendbar.
- Start ohne passendes Ende: harter Schutz vom Start bis Source-Ende, unabhängig vom deklarierten Level, Warnung.
- Ende ohne passenden Start: harter Schutz von Source-Start bis Ende, Warnung.
- Mehrdeutiges Paar oder Start nach Ende: Sidecar unbrauchbar.
- Aufnahmebeginn schützt standardmäßig die erste Sekunde, Aufnahmeende die letzte Sekunde. Intro/Outro/Stinger erhalten 250 ms Außenpuffer; Szenenwechsel 350 ms davor und 650 ms danach. Explizite größere Sidecar-Puffer gewinnen.

### 6.3 Konfliktregeln

1. Ein Schnitt mit Schnittmenge zu hartem Schutz wird verworfen; er wird nicht nur gekürzt, weil dadurch die beabsichtigte Semantik des Kandidaten wechseln könnte.
2. Bei weichem Schutz wird standardmäßig der geschützte Anteil subtrahiert. Ein verbleibender Teil darf nur mit Confidence ≥ 0,98, mindestens 500 ms Cutdauer und 750 ms zusätzlichem Abstand zur weichen Grenze akzeptiert werden.
3. Wortbereiche erhalten 250 ms Guard vor dem Wortanfang und nach dem Wortende. Unsichere Wortzeitstempel vergrößern den Guard auf 400 ms.
4. Chartphrasen schützen 1,5 Sekunden vor Beginn bis 2,5 Sekunden nach Ende weich. Dramaturgische Satzmuster und Korrekturkontexte sind weich geschützt, bis die Spezialanalyse eine sichere Entscheidung belegt.
5. Überlappende oder direkt angrenzende akzeptierte Cuts werden vereinigt. Ein ungeschützter Gap unter zwölf Frames wird nur dann mit entfernt, wenn er kein Wort, keinen Audioimpuls und kein Schutzsignal enthält.
6. Eine Keep-Insel unter zwölf Frames ist unzulässig. Enthält sie Sprache/Schutz, werden die angrenzenden Cuts verworfen; andernfalls werden sie vereinigt.

### 6.4 Kandidatentypen

- `dead_air`: automatisierbar ab 0,92, aber nur mit gültigem Sidecar und allen Guards.
- `filler`: `äh/ähm` automatisierbar ab 0,97; übrige Füllwörter ab 0,99 und nur als eigenständige Diskursmarker. Akustische Handles müssen beidseitig vorhanden sein.
- `false_start/self_correction`: Nur der nachweislich verworfene Teil darf ab 0,98 entfernt werden, wenn Korrekturmarker und vollständige korrigierte Aussage erhalten bleiben. Zahlen-/Preis-/Prozentkorrekturen ohne eindeutige Struktur bleiben Review.
- `mouth_click/smack`: Isolierte Impulse außerhalb von Sprache dürfen ab 0,98 lokal mit niedrig aggressivem De-Click behandelt werden. Während Sprache wird in v1 standardmäßig nur markiert. Eine Reparatur verändert die Timeline nicht.
- Stilistische Wiederholung hat keinen automatischen Cuttyp.

## 7. CTA- und Notification-Regeln

### 7.1 Intent-Scoring

Das Scoring erfolgt auf einem Fenster aus Satz plus jeweils fünf Wörtern Kontext. Normalisierung umfasst Kleinschreibung, Unicode-Normalisierung, deutsche Flexionsvarianten und robuste ASR-Schreibvarianten, aber keine semantische Cloud-API.

Positive Signale:

- bekannte vollständige CTA-Phrase: +0,55;
- Imperativ/Handlungsverb („abonniere“, „klick“, „tritt bei“, „visit“, „use“): +0,20;
- eindeutiges Kategorieobjekt: +0,15;
- direkte Zuschaueradressierung oder Link-/Beschreibungskontext: +0,10;
- konsistenter Nahkontext: +0,10.

Der positive Score wird mit der mittleren Probability der tragenden Wörter multipliziert. Neutrale/deskriptive Verwendung zieht 0,60 ab; explizite Verneinung der Handlung 0,80; konkurrierende Objektbedeutung 0,50. Ergebnis wird auf 0 bis 1 begrenzt. `>=0,78` ist Auto-Intent, `0,60–0,779` Review ohne Overlay, darunter ignoriert.

Kategorieauflösung:

- „Mitglied werden“, „Kanal unterstützen“ plus Mitgliedsobjekt → `kanalmitglied`, nie `community`.
- Community, Discord, TruthPill plus Beitritts-/Linkabsicht → `community`.
- Link/Code/Referral plus Nutzungsabsicht → `referral`; bloße Plattformnutzung mit CTA → `hyperliquid`.
- Ein Satz darf zwei klare Intents tragen, etwa Abo und Glocke; sie werden getrennt geplant, nie überlappend.

### 7.2 Ausschlüsse

Neutraler Plural wie „Mitglieder des Marktes“, englisches Vergleichs-„like“, Börsen-/Marktglocke, Kommentare als Inhaltszitat, fremdes Service-Abo, analytische Hyperliquid-Erwähnung sowie Website-/Numerologie-Erwähnung ohne Zuschauerhandlung lösen nichts aus. Eine CTA darf nicht allein auf einem Wort mit Probability < 0,70 beruhen.

### 7.3 Zentrale v1-Limits

```toml
[notifications]
global_max_per_video = 6
global_min_start_gap_seconds = 30
no_overlay_first_seconds = 20
no_overlay_last_seconds = 15
max_simultaneous = 1
post_overlay_gap_seconds = 1

[notifications.category_max]
like = 2
abo = 2
glocke = 1
kommentar = 2
kanalmitglied = 1
community = 1
hyperliquid = 1
referral = 1
website = 1
numerologie = 1

[notifications.category_cooldown_seconds]
like = 600
abo = 600
glocke = 600
kommentar = 600
kanalmitglied = 900
community = 900
hyperliquid = 900
referral = 900
website = 900
numerologie = 900

[notifications.campaign_max]
hyperliquid_or_referral = 1
```

Diese Werte sind konfigurierbar, aber zentral versioniert. Bei Limitkonflikten gewinnt höhere Confidence, dann der frühere Source-Intent. Ein Assetfehler unterdrückt nur das betroffene Overlay und erzeugt eine Review-Warnung.

### 7.4 Mapping und Platzierung

Ein CTA bleibt nur erhalten, wenn sämtliche tragenden Wörter gemappt werden oder bei Teilkürzung mindestens 60 % der Intentwörter einschließlich Handlungsverb und Objekt übrig bleiben. Anker ist 250 ms nach Ende des letzten erhaltenen tragenden Worts. Der Scheduler sucht danach innerhalb ±5 Sekunden die nächste zulässige Stelle.

Overlaydauer wird `ceil(duration_seconds × 60)` Frames. Der gesamte Bereich plus sechs Frames Rand muss innerhalb eines Keep-Segments liegen, darf keinen `blocks_overlays`-Bereich berühren und muss mindestens eine Sekunde Abstand zu einem anderen Overlay halten. Findet sich kein Platz, wird das Overlay mit Grund `no_safe_output_window` unterdrückt.

Positionen sind ausschließlich `bottom_left` und `top_right`. Safe Margin ist 80 Pixel bei 2560×1440 und proportional bei anderer unterstützter Auflösung. `bottom_right` ist global verboten. Die effektiven Alpha-Bounds werden gegen den Frame geprüft; kein Cropping ist zulässig.

### 7.5 Notification-Audio

- Sichtbares Overlay und Sound haben getrennte Flags und Limits.
- Defaultmodus aller Assets ist `none`; `glocke.webm` darf erst nach einem bestandenen Audit auf `internal` gestellt werden.
- Externe Sounds benötigen einen eigenen Manifest-Eintrag in einer späteren Manifestversion; ein beliebiger Dateipfad reicht nicht.
- Sound wird vor dem Mix auf −24 LUFS short-term normalisiert und standardmäßig mit −12 dB Gain eingeblendet.
- Optionales Ducking senkt Programmaudio während des Sounds um maximal 2,5 dB, Attack 30 ms, Release 250 ms.
- Nach dem Mix folgt die zweipassige EBU-R128-Zielung auf −14 LUFS integrated, LRA 11, danach ein Peak-Limiter bei −1,5 dBTP und eine abschließende Messung. FFmpegs `loudnorm` unterstützt Zweipassbetrieb und True-Peak-Zielung ([Dokumentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm)).

## 8. Renderstrategie

### 8.1 Zeitkompression

FFmpeg normalisiert Source-Startzeit auf null. Video wird beim finalen Render einmal decodiert, per EDL-Frameintervallen selektiert und mit `setpts` auf eine lückenlose 60-FPS-Output-Timeline gesetzt. Audio wird in einem vorgelagerten verlustfreien Pfad auf 48 kHz resampled und in Blöcke zu exakt 800 Samples (`asetnsamples`) geteilt. Dieselben Keep-Frameintervalle selektieren Audioblöcke; `asetpts` komprimiert die Timeline. Dadurch stimmen alle Schnittgrenzen ohne Fließkomma-Drift überein.

An akzeptierten Audiogrenzen werden innerhalb der erhaltenen Handles fünf Millisekunden Fade-out/Fade-in ohne Daueränderung gesetzt, um Klicks zu vermeiden. Video wird nicht unabhängig gerundet. Der Filtergraph liegt als Datei im Job-Temp, damit Windows-Kommandozeilenlimits keine Rolle spielen.

### 8.2 Overlay-Compositing

Der Alpha-Cache wird als Loop-/Kurzinput geladen, auf seine native effektive Größe geprüft, mit konfigurierbarem Faktor skaliert und über `overlay` anhand von Output-Frames aktiviert. Der Renderplan enthält niemals Source-Zeit als Overlaystart. Z-Reihenfolge ist chronologisch; wegen `max_simultaneous=1` existiert kein visueller Stapelkonflikt.

### 8.3 Audiochain

Die Standardchain ist: Resample/Frameauswahl → boundary fades → optional isoliertes `adeclick` auf freigegebenen Bereichen → sanfter Kompressor nur bei geprobter hoher Dynamik (Ratio 1,5:1, maximal 3 dB Gain Reduction) → Notification-Mix/optionales Ducking → verlustfreies `edit-mix.wav` → Loudness-Messpass → linearer zweiter `loudnorm`-Pass auf −14 LUFS, LRA 11, −1,5 dBTP → `alimiter` als Sicherheitsnetz → verlustfreies `mastered-audio.wav`. Erst der finale MP4-Render encodiert dieses Master einmal zu AAC 320 kbit/s, 48 kHz, Stereo.

Kein Noise Gate läuft blind über die ganze Aufnahme. Eine Rauschminderung wird nur aktiviert, wenn ein gemessenes Noise-Profil und ein A/B-Regressionstest keine Sprachschädigung zeigen; Default v1 ist aus.

### 8.4 Videoencode und Veröffentlichung

Standard: H.264 High Profile, `libx264`, CRF 18, preset slow, `yuv420p`, Source-Auflösung, CFR 60/1, MP4 `+faststart`. Farbmetadaten werden aus der Source übernommen, soweit sie konsistent sind. Rotation wird normalisiert. Der Exportname wird aus lokalem Datum und bereinigtem Titel gebildet; bei Kollision folgen `_v02`, `_v03` usw.

Der Verifier dekodiert Stichproben um jede Schnitt-/Overlaygrenze plus Anfang/Ende und führt einen vollständigen FFmpeg-Decode-Fehlerlauf aus. Erwartete Dauer ist `output_total_frames / 60`; Toleranz höchstens ein Frame. Finalisierung geschieht auf demselben Volume durch atomisches Rename.

## 9. Konfiguration

Priorität: eingebettete versionierte Defaults < `%APPDATA%\DimensionWithin\MatrixAutoCutter\config.toml` < Projekt-Overrides. Nur explizit erlaubte Schlüssel sind überschreibbar. Jeder Job materialisiert die effektive Konfiguration als `artifacts/effective-config.toml` samt SHA-256.

Konfiguriert werden Pfade, Analysegrenzen, Protection-Defaults, CTA-Regeln, Limits, Positionen, Sound, Audiochain, Encodeprofil und Hardwarepräferenz. Produktinvarianten sind nicht abschaltbar: Originalschutz, Bottom-right-Verbot, hartes Protection-Veto, Source-to-Output-Mapping, atomare Finalisierung und no-sidecar-Schnittsperre.

## 10. Fehlerbehandlung, Logging und Hardwareprüfung

### 10.1 Fehlerklassen

- `E_INPUT_*`: fehlende/geänderte Source, nicht unterstütztes Medienprofil;
- `E_SIDECAR_*`: Schema, Identität, Paarung, Zeitbasis;
- `E_ASSET_*`: Hash, ZIP-Mitglied, Track-Matte, Cache;
- `E_MODEL_*`: Modell fehlt, Hash/Initialisierung/Inferenz;
- `E_ANALYSIS_*`: ungültige Stage-Artefakte;
- `E_EDL_*`: Schutzverletzung, Zeitmodell, Mapping;
- `E_RENDER_*`: FFmpeg-Start, Filter, Encode, Abbruch;
- `E_VERIFY_*`: Decode, Streamprofil, Sync, Loudness;
- `E_IO_*`: Speicher, Berechtigung, atomare Veröffentlichung.

Erwartete Probleme erzeugen Code, Nutzertext, technischen Kontext, betroffene Artefakt-ID und `retryable`. Ein Fehler führt nie zu einem stillen Fallback, außer zu den ausdrücklich definierten Safe-Modes.

### 10.2 Logging

`logs/job.ndjson` enthält UTC-Zeit, Level, Eventcode, Stage, Job-ID, Artefakt-ID und strukturierte Felder. FFmpeg-Kommandos werden als Argumentarray mit Version protokolliert; keine Umgebungsgeheimnisse. `logs/ffmpeg-<stage>.log` hält vollständige Toolausgabe. Logrotation pro Job: zehn Dateien à 20 MB. Die GUI zeigt kurze deutsche Meldungen, der Review verlinkt technische Logs.

### 10.3 Hardware-/Umgebungs-Preflight

Pflichtprüfungen:

- Windows 10/11 64 Bit und Python-Runtime der ausgelieferten App;
- `ffmpeg`/`ffprobe` gleiche Major-Version, Mindestversion 7, Filter `alphamerge`, `overlay`, `loudnorm`, `adeclick`, `asetnsamples`, Encoder `libx264` und AAC;
- Quellprobe und mindestens `max(20 GB, 2 × geschätzte Ausgabedatei + Cachebedarf)` freier Speicher;
- 48-kHz-Audiopfad und 60-FPS-CFR für Autoediting;
- lokales `large-v3`-Modell und Modellhash;
- CTranslate2 CUDA-Test auf kleinem eingebettetem Sample, andernfalls expliziter CPU-Fallback;
- Manifest-/Package-/Asset-Hashes und Cachevalidität;
- Schreibtest nur in Workspace, Cache und Export-Staging.

## 11. Projektdaten, Cache und temporäre Dateien

```text
D:\workspace\
├─ 2026-07-12_title_final.mp4
└─ .matrix-auto-cutter\
   └─ projects\
      └─ <project-id>\
         ├─ project.json
         ├─ job.json
         ├─ artifacts\
         │  ├─ media-probe.json
         │  ├─ effective-config.toml
         │  ├─ protection-ranges.json
         │  ├─ audio-analysis.json
         │  ├─ transcript.json
         │  ├─ cut-candidates.json
         │  ├─ edl.json
         │  ├─ timeline-map.json
         │  ├─ cta-intents.json
         │  ├─ overlay-events.json
         │  ├─ render-plan.json
         │  ├─ review.json
         │  └─ review.html
         ├─ logs\
         │  ├─ job.ndjson
         │  └─ ffmpeg-*.log
         └─ tmp\
            ├─ analysis-audio.wav
            ├─ audio-filtergraph.txt
            ├─ video-filtergraph.txt
            ├─ edit-mix.wav
            ├─ mastered-audio.wav
            └─ <export>.partial.mp4

%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\
├─ cache\assets\<cache-key>\{overlay.mov,probe.json,validation.json}
└─ models\faster-whisper-large-v3\...

%APPDATA%\DimensionWithin\MatrixAutoCutter\config.toml
```

Originalquellen bleiben an ihrem Ort. Tempdaten werden nach Erfolg standardmäßig entfernt, Logs und Artefakte bleiben. Nach Fehler bleiben Tempdaten sieben Tage oder bis zur manuellen Bereinigung. Cleanup akzeptiert nur aufgelöste Pfade unter dem konkreten Projekt-`tmp`; Symlinks/Reparse-Points werden nicht verfolgt.

Die spätere Codebasis erhält folgende klare Module, ohne sie in diesem Auftrag anzulegen:

```text
src/matrix_auto_cutter/
├─ core/          # Modelle, Zeitbasis, IDs, Konfiguration
├─ project/       # Projektdatei, Pfade, atomare IO
├─ media/         # ffprobe/FFmpeg und Hardware-Preflight
├─ protection/    # Sidecar und Intervallauflösung
├─ analysis/      # Audio, Transkript, Schnittkandidaten
├─ editing/       # EDL und Timeline-Mapping
├─ notifications/ # CTA, Overlay, Sound, Assets
├─ render/        # Plan, Runner, Verifier
├─ review/        # JSON/HTML
├─ worker/        # Stage-Orchestrierung und NDJSON-Protokoll
└─ ui/            # PySide6
```

## 12. Phasenplan mit Exit-Kriterien

Die vorgegebene Reihenfolge bleibt bestehen, weil Schutz und Zeitmodell vor jeder inhaltlichen Automatisierung stabil sein müssen.

1. **Projekt- und Umgebungsfundament.** Python-Projekt, Pydantic-Verträge, Zeitbasis, atomare IO, strukturierte Fehler/Logs, Tests. Exit: Schema-/Timebase-Tests grün; keine Medienmutation möglich.
2. **Medien-Probe und sichere Projektverzeichnisse.** `ffprobe`, Source-Identität, Pfadguard, Preflight, Stage-Artefakte. Exit: synthetische und echte Probe; Hash/mtime der Source unverändert; unsichere Pfade abgewiesen.
3. **OBS-Ereignis-Sidecar und Schutzbereiche.** Zuordnung, Schema, Paarungsfallback, Intervalle/Puffer. Exit: vollständige Fixturematrix; kein Cutmodul vorhanden, aber Schutzresultat deterministisch.
4. **EDL und Source-to-Output-Timeline-Mapping.** Validator, Keep-Komplement, Vor-/Rückabbildung, Rundung. Exit: Property-Tests über zufällige Cuts; Schutzverletzung unmöglich; 60-FPS-Goldenwerte.
5. **Konservative Stille-Erkennung und Test-Rendering.** Adaptive RMS-Kandidaten, Guards, EDL-Kompilierung, framegenauer FFmpeg-Test. Exit: synthetischer 1440p60-Clip; erwartete Frames; A/V-Sync ≤ 1 Frame; no-sidecar rendert ungekürzt.
6. **Lokale Transkription und Wortzeitstempel.** Modell-Preflight, CUDA/CPU, Mischsprache, Transcriptvertrag. Exit: DE/EN-Fixtures, monotone Wortframes, reproduzierbare Engine-Metadaten.
7. **CTA-Erkennung.** Phrasen/Intent/Negativregeln, Score und getrennte Kategorien. Exit: kuratierter Testsatz inklusive Kanalmitglied/Community und neutraler Marktmitglieder mit festgelegter Precision.
8. **Track-Matte-Overlay-Pipeline.** Registry, ZIP-Sicherheit, Alpha-Cache, Output-Scheduler. Exit: alle zehn CTA-Assets validiert; Alpha-Goldenframes; keine Bottom-right-/Kollisionsverletzung.
9. **Notification-Sound-Mixing.** Soundmodi, Freigabe, Gain, Ducking, Limiter. Exit: Default-Glocke stumm; Peak/Loudness- und A/V-Sync-Tests bestehen.
10. **HTML-/JSON-Review.** Kanonischer Reviewvertrag, statisches escaped HTML, Ergebnisverlinkung. Exit: Snapshot-/Schema-Tests und Offline-Öffnung.
11. **Füllwörter, Selbstkorrekturen und Audioartefakte.** Strikte Kandidaten/Confidences, isoliertes De-Click, Regressionen. Exit: kein Zahlenkorrektur-/Wiederholungs-Golden wird semantisch beschädigt; unsichere Fälle nur Review.
12. **Klickbare lokale Oberfläche.** PySide6-Workflow, QProcess, Fortschritt, Abbruch, Ergebnisöffnung, Packaging. Exit: kompletter lokaler Happy Path und kontrollierte Fehler-/Abbruchszenarien ohne CLI-Nutzung.

Jede Phase wird erst freigegeben, wenn ihre Artefaktschemas versioniert, Tests grün und Source-Immutabilität nachgewiesen sind.

## 13. Teststrategie und technische Akzeptanz

### 13.1 Testebenen

- **Unit:** Zeitrundung, halboffene Intervalle, Protection-Union, Scoring, Cooldowns, Pfadguards, Schemafehler.
- **Property-based:** zufällige disjunkte Cuts; Mapping ist monoton, lückenlos im Output und innerhalb Keep-Segmenten invertierbar; kein Outputframe doppelt.
- **Golden Fixtures:** Sidecarpaare, DE/EN-Transkripte, CTA-Sätze, Chartpausen, Selbstkorrekturen, Overlay-Alpha-Frames und Review-HTML.
- **Medienintegration:** deterministisch erzeugte 60-FPS-Testclips mit Timecode, Klickspur, Testton und Schutzereignissen; echte Assets nur lesend.
- **End-to-End:** Import bis geprüfte `.mp4`, inklusive Abbruch, Platte-voll-Simulation und Wiederaufnahme.
- **Regression:** Source-/Asset-Hashes vor und nach Tests; A/V-Sync an jedem Schnitt; keine Overlay-Kollision.

### 13.2 Gates

- 100 % Branch Coverage für Zeitbasis, Protection, EDL und Mapping; mindestens 90 % für übrigen Kern, UI ausgenommen.
- Kein akzeptierter Cut mit Hard-Protection-Intersection.
- Mapping-Outputlänge entspricht exakt der Summe der Keep-Längen.
- CTA-Goldens: 100 % der fest definierten negativen Kanalmitglied/Community-Beispiele bleiben negativ; alle vorgegebenen positiven Beispiele richtig kategorisiert.
- Track-Matte-Cache: erwartete Größe/Dauer, nichtleere Alphaebene, Originalhash unverändert.
- Standardrender: exakt erwartete Video-Framezahl; Audio maximal ein Frame länger/kürzer; −14 ±0,5 LUFS und ≤ −1,5 dBTP.
- Ein Output wird nur bei vollständig bestandenem Verifier final benannt.

Die Produkt-Akzeptanzkriterien in `matrix-auto-cutter-planning-brief-v0.5.md` sind zusätzlich bindend.

## 14. Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| OBS-Sidecar fehlt oder driftet | Source-Hash/Größe/Dauer prüfen; keine Auto-Cuts ohne gültige Zuordnung; später Fingerprinting ergänzen. |
| VFR oder nicht 60 FPS | Preflight sperrt zeitliches Autoediting; keine stille Zeitbasisannahme. |
| Whisper-Wortzeiten bei Code-Switching ungenau | Multilinguales large-v3, Wahrscheinlichkeiten, größere Guards bei Unsicherheit, keine riskanten Cuts nur aus ASR. |
| Dead Air wird mit Chart-/Denkpause verwechselt | Sidecar-, Chart-, Satz- und Wortschutz; hohe Schwelle; Review für Grenzfälle. |
| Selbstkorrektur verändert Fakten | Zahlenkorrekturen standardmäßig Review; vollständige korrigierte Aussage muss erhalten bleiben. |
| De-Click schädigt Konsonanten | Automatisch nur isoliert außerhalb Sprache; niedrige Aggressivität und Regressionstest. |
| Track-Matte ist anders als erwartet | Stream-/Geometrie-/Alpha-Validierung und Goldenframes; Asset bei Fehler sperren. Der Stinger bleibt ohnehin OBS-only. |
| Glockenton übersteuert oder ist ungeeignet | Default stumm; separates Audit; Gain, Ducking, Loudnorm und True-Peak-Verifikation. |
| Viele Schnitte erzeugen FFmpeg-/Windows-Limits | Filtergraphdatei, normalisierte Intervalle und ein Decodepfad statt lange Shellkommandozeile. |
| Lange Jobs brechen ab | Stage-Hashes/Checkpoints, atomare Artefakte, wiederholbare Stufen, kontrollierter Abbruch. |
| Tempdaten füllen Datenträger | konservative Speicherprognose, Projektquoten, sieben Tage Fehlerretention, sicherer Cleanup. |
| Original wird versehentlich überschrieben | Sourcepfad nie als Schreibziel zulassen, Export-Staging getrennt, Kollisionssuffix, Hash-/mtime-Verifikation. |
| HTML injiziert Transkriptinhalt | Jinja2 Autoescape, keine aktiven externen Ressourcen, Content Security Policy. |

## 15. Einzelner erster Coding-Auftrag für die spätere Implementierung

**Auftrag: Implementiere ausschließlich den versionierten Zeitbasis-/OBS-Sidecar-/Protection-Kern, noch ohne UI, FFmpeg, Transkription, EDL, Rendering oder Assetkonvertierung.**

Lieferumfang:

1. Minimales Python-3.12-Projekt mit `src`-Layout, Pydantic 2 und pytest.
2. Unveränderliche Modelle für `FrameRate(60,1)`, halboffene `FrameRange` und die Sidecar-Version 1.0 aus Abschnitt 5.2.
3. Funktion `expected_sidecar_path(mp4_path)`, die ausschließlich `<stem>.obs-events.json` liefert.
4. Validator gegen Dateiname, bereitgestellte Sourcegröße, SHA-256 und Dauer; er liest die MP4 in Tests nicht selbst, sondern erhält eine `SourceIdentity`.
5. Protection Resolver mit den Rundungs-, Puffer-, Pairing- und Unionregeln aus Abschnitt 6. Bei nicht vorhandenem/identitätsfalschem/schemaungültigem Sidecar liefert er explizit `no_sidecar_safe_mode`; fehlende Einzelpaare werden konservativ bis zum Source-Rand geschützt.
6. Export von `protection-ranges.json` über atomare Schreibfunktion.
7. Strukturierte Fehlercodes `E_SIDECAR_*`; keine unstrukturierte Exception darf die öffentliche API verlassen.

Pflichttests:

- Dateiname `aufnahme.mp4` → `aufnahme.obs-events.json`, einschließlich Pfaden mit Leerzeichen und Umlauten;
- korrektes Beispiel aus Abschnitt 5.2;
- Hash-/Größen-/Dauerabweichung aktiviert Safe-Mode;
- Start ohne Ende schützt bis Source-Ende, Ende ohne Start ab Source-Start;
- mehrdeutiges Paar macht das Sidecar unbrauchbar;
- Schutzstart wird nach außen ab-, Schutzende nach außen aufgerundet;
- Hard-over-Soft-Priorität und `blocks_overlays`-Union;
- Clamp an Sourcegrenzen, keine Null-/Negativintervalle;
- atomare Ausgabe ist valides JSON und enthält Schema-/Inputhash;
- Property-Test: normalisierte Bereiche sind sortiert, nicht überlappend und innerhalb der Source.

Definition of Done: `pytest` ist vollständig grün, Kernmodule erreichen 100 % Branch Coverage, `mypy --strict` und `ruff check` sind grün, es werden ausschließlich Fixture-Dateien in temporären Testverzeichnissen geschrieben, und kein Anwendungsskelett oder Mediencode wird angelegt. Dieser Auftrag schafft den Schutzvertrag, auf dem Phase 2 bis 4 ohne neue Grundsatzentscheidung aufbauen.

## 16. Architekturkonformität

Änderungen an Produktinvarianten benötigen eine neue Planning-Brief-Version. Änderungen an Datenverträgen, Zeitbasis, Sidecar-Fallback, Rendercodec, CTA-Schwellen oder zentralen Limits benötigen mindestens eine neue Architekturplan-Version und eine Migration der betroffenen Artefaktschemas. Implementierungsdetails dürfen variieren, wenn sie nachweislich dieselben Verträge, Tests und Akzeptanzkriterien erfüllen.
