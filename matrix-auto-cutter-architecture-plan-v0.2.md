# Matrix Auto Cutter — Verbindlicher Architekturplan v0.2

Status: korrigierter Architektur-Freeze für Version 1  
Stand: 2026-07-12  
Produktvertrag: `matrix-auto-cutter-planning-brief-v0.5.md`  
Assetvertrag: `matrix-auto-cutter-asset-manifest-v0.5.json`  
Ersetzt technisch: `matrix-auto-cutter-architecture-plan-v0.1.md`

## 1. Geltung und Korrekturen gegenüber v0.1

Dieser Plan übernimmt alle weiterhin gültigen Entscheidungen aus v0.1 und ersetzt dessen Sidecar-Version 1.0 vollständig durch Version 1.1. Bei technischen Widersprüchen gilt v0.2. Der Planning Brief v0.5 bleibt der höherrangige Produktvertrag.

Die Produktgrenze bleibt unverändert: OBS produziert Intro, Outro, Szenenwechsel und Stinger bereits in der Aufnahme. Matrix Auto Cutter schützt diese Bereiche, fügt sie nicht ein, ersetzt oder verschiebt sie nicht und ordnet keine Inhaltsabschnitte neu.

v0.2 schließt insbesondere diese Lücken:

- Ein nativer OBS-Producer und ein separater Finalizer werden verbindlich festgelegt.
- Rohjournal und finales Sidecar sind zwei unterschiedliche Artefakte und Lebenszyklen.
- `first_encoded_frame` wird als nicht belegbare Clock-Origin verworfen.
- Pause/Resume und Remux werden unterstützt; File-Splitting wird in v1 erkannt und abgewiesen.
- Protection gilt getrennt für Zeitschnitte, Overlays und lokale Audio-Reparatur.
- Hashing, geschlossene Dateien, Remux, Recovery, Startoffsets und Schutz-Mapping werden konkretisiert.
- UUIDv4 ersetzt UUIDv7, damit Python 3.12 und das native Plugin keine zusätzliche ID-Bibliothek benötigen.
- Die 800-Samples-pro-Frame-Idee ist eine in Phase 5 zu beweisende Hypothese, keine vorweggenommene Garantie.

## 2. Weiterhin verbindliche Grundentscheidungen

| Bereich | Entscheidung | Begründung |
|---|---|---|
| Sprache | Python 3.12, 64 Bit | Gemeinsamer Kern für Modelle, Analyse, Jobs und Finalizer. |
| UI | PySide6 mit Qt Widgets | Native anklickbare Windows-App; Medienarbeit bleibt im Worker. |
| OBS-Producer | Natives C++20-OBS-Plugin plus lokaler Python-3.12-Finalizer | Nur das Plugin sieht OBS-Output-, Source- und Transition-Signale zuverlässig; Hashing und Medienprobe dürfen OBS nicht blockieren. |
| Kernmodelle | Pydantic 2; JSON Schema Draft 2020-12 | Strikte, exportierbare Verträge; unbekannte Felder werden abgewiesen. |
| IDs | UUIDv4: Python `uuid.uuid4()`, im Plugin Windows `CoCreateGuid` | Kollisionssicher, überall verfügbar, keine Zusatzbibliothek; Sortierung erfolgt über Zeit/Sequenznummer. |
| Jobs | GUI plus genau ein Worker-Subprozess; maximal ein aktiver Medienjob | UI bleibt responsiv, GPU- und Datenträgerkonkurrenz bleibt beherrschbar. |
| FFmpeg | Externe konfigurierte `ffmpeg.exe`/`ffprobe.exe`, mindestens 7.0; Argumentlisten ohne Shell | Ein Decoder/Encoder/Compositor, reproduzierbare Aufrufe. |
| Transkription | `faster-whisper large-v3`, lokal, `word_timestamps=True`, `language=None`, `vad_filter=False` | Gemischtes Deutsch/Englisch und Wortzeiten ohne Cloud; Stille bleibt für separate Analyse erhalten. |
| Zeitmodell im Auto-Cutter | Halboffene ganzzahlige Frameintervalle auf geprüfter CFR-60/1-Video-Timeline | Kein kumulativer Fließkommafehler; Source-to-Output-Mapping bleibt eindeutig. |
| Persistenz | Versionierte JSON-/NDJSON-Dateien, atomare Statusdateien, keine Datenbank in v1 | Transparente Ein-Nutzer-Projekte und wiederholbare Stufen. |
| Rendering | Ein finaler Videoencode; Audio vorher verlustfrei geschnitten, gemischt und zweipassig gemastert | Qualität, Prüfbarkeit und Source-Immutabilität vor Geschwindigkeit. |
| Review | Kanonisches `review.json` plus eigenständiges statisches `review.html` | Maschinen- und menschenlesbare Entscheidungen ohne Timeline-Editor. |
| Asset-Cache | SHA-256-adressierter ProRes-4444-Alpha-Cache unter `%LOCALAPPDATA%` | Track-Mattes werden einmal geprüft; Originalassets bleiben unverändert. |

Cloud-ASR, WhisperX, Electron/Tauri, SQLite, eine FFmpeg-Library-Bindung, Fingerprinting als Kernschutz und ein professioneller Timeline-Editor bleiben für v1 ausgeschlossen.

## 3. Architekturübersicht

```text
OBS Studio 32.x (Windows x64)
  └─ Matrix Auto Cutter Producer (natives C++20-Plugin)
       ├─ OBS-/Output-/Source-/Transition-Signale
       ├─ Hotkeys für manuelle Protection
       ├─ Output-Pfad- und Split-Erkennung
       └─ append-only Recording Journal (NDJSON)
             └─ lokaler Finalizer-Prozess
                  ├─ Journalvalidierung und Clock-Alignment
                  ├─ Datei-Close-/Stabilitätsprüfung
                  ├─ Direct-MP4-/Remux-Zielauflösung
                  ├─ ffprobe, vollständiges SHA-256
                  └─ atomare Veröffentlichung Sidecar 1.1

PySide6 GUI
  └─ QProcess → Auto-Cutter Worker
                 ├─ Projekt/Preflight/ffprobe
                 ├─ Sidecar-1.1-Consumer → Protection Resolver
                 ├─ Audioanalyse + faster-whisper
                 ├─ Cut Analyzer → EDL → Timeline Mapper
                 ├─ CTA → Overlay/Sound Scheduler
                 ├─ Track-Matte Registry/Cache
                 ├─ FFmpeg Render Planner/Runner
                 └─ Review Builder + Output Verifier
```

Producer und Consumer teilen nur versionierte Datenmodelle. Der Auto-Cutter liest niemals ein Rohjournal als Sidecar. Nur `artifact_type="obs_event_sidecar"`, `schema_version="1.1"`, `lifecycle.status="finalized"` und eine vollständig passende Source-Identität dürfen zeitentfernende Auto-Edits freischalten.

## 4. Verbindlicher OBS-Sidecar-Producer

### 4.1 Gewählte Kombination

Version 1 verwendet ein natives C++20-Plugin für OBS Studio 32.x unter Windows x64 und einen von diesem Plugin gestarteten lokalen Python-3.12-Finalizer. Ein reines OBS-Skript wird verworfen: Es wäre für die langfristige Bindung an Output-, Source- und Transition-Signale sowie Win32-Dateihandles weniger robust. Ein ausschließlich externer Recording-Begleiter wird verworfen, weil er semantische OBS-Ereignisse nicht zuverlässig sieht.

Das Plugin nutzt die offiziellen Frontend-Ereignisse für Recording, Pause/Resume und Szenenwechsel, den aktuellen beziehungsweise letzten Aufnahmepfad und den Recording-Output. OBS dokumentiert diese Ereignisse und Pfadfunktionen in der [Frontend API](https://docs.obsproject.com/reference-frontend-api). Der Output-Framecounter stammt aus `obs_output_get_total_frames`; er bezeichnet verarbeitete Outputframes, aber nicht automatisch den exakten visuellen Ereignisframe ([Output API](https://docs.obsproject.com/reference-outputs)). Transition- und Media-Start/-Ende werden über die dokumentierten Source-Signale erfasst ([Source API](https://docs.obsproject.com/reference-sources)). Daraus wird ausdrücklich keine framegenaue Ereignisgarantie abgeleitet.

### 4.2 Ereignisquellen

- **Aufnahme:** Bei `OBS_FRONTEND_EVENT_RECORDING_STARTING` holt das Plugin den Recording-Output und verbindet dessen `start`, `stop`, `pause` und `unpause`-Signale. Das Output-`start`-Signal eröffnet das Journal; das erfolgreiche Output-`stop`-Signal schließt die Ereigniserfassung. Frontend-Events dienen als zusätzliche Diagnose.
- **Szenenwechsel:** `OBS_FRONTEND_EVENT_SCENE_CHANGED` erzeugt `scene_changed` mit Scene-UUID und Name.
- **Intro/Outro:** In der Plugin-Konfiguration sind genau eine Intro- und eine Outro-Media-Source per stabiler OBS-Source-UUID registriert. Deren `media_started`/`media_ended`-Signale erzeugen die jeweiligen Start-/Endevents. Fehlende, doppelte oder umbenannte UUID-Zuordnung sperrt die Producer-Bereitschaft vor Aufnahmebeginn.
- **Stinger:** Genau eine als Stinger konfigurierte Transition-Source-UUID liefert `transition_start` und `transition_stop`. Andere Übergänge werden als Szenenwechsel erfasst, aber nicht als Stinger klassifiziert.
- **Manuelle Protection:** Das Plugin registriert zwei OBS-Hotkeys: `MAC hard protection marker` erzeugt einen Punktmarker mit Standardpuffer; `MAC toggle protection range` erzeugt ein Start-/Endpaar. Ein zweiter Start ohne Ende schließt zunächst das offene Intervall. Hotkeys während Pause werden journalisiert, aber nicht in ein finales Source-Event übernommen.
- **Pfad:** Beim Output-Start wird `obs_frontend_get_current_record_output_path()` gespeichert; nach erfolgreichem Stop zusätzlich `obs_frontend_get_last_recording()`. Beide Werte sowie periodische Pfadsnapshots gehen ins Journal. Der Finalizer entscheidet erst später, welche geschlossene MP4 die Source ist.

Jeder Callback erfasst in derselben Journalzeile Sequenznummer, UUIDv4, Windows-QPC-Zeit in Nanosekunden, aktuellen Output-Framecounter, Pausenstatus und Pfadsnapshot. Zusätzlich schreibt das Plugin alle zwei aktive Sekunden eine Kalibrierungsprobe. Jeder Record wird UTF-8 als eine Zeile angehängt, danach werden C-Laufzeitpuffer und Windows-Dateipuffer geleert. Mediencallbacks warten niemals auf Hashing oder `ffprobe`.

### 4.3 Zweistufiger Lebenszyklus

**Stufe 1 — Recording Journal:**

- Ablage: `%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\producer\journals\<session-id>.recording-journal.ndjson`.
- `artifact_type` ist `recording_event_journal`, `journal_schema_version` ist `1.0`.
- Das Journal enthält noch keine finale Source-Identität und wird niemals `<stem>.obs-events.json` genannt.
- Zustände sind `recording`, `stopped_unfinalized`, `finalization_failed` oder `aborted`.
- Nur ein erfolgreiches Output-Stop-Signal darf `stopped_unfinalized` schreiben.

**Stufe 2 — Finalisiertes Sidecar:**

- Der Finalizer akzeptiert nur ein lückenloses, syntaktisch valides Journal mit `stopped_unfinalized`.
- Er wartet auf eine vollständig geschlossene Ziel-MP4, probt sie, kalibriert die Zeitbasis, berechnet vollständiges SHA-256 und baut Sidecar 1.1.
- Veröffentlichung erfolgt ausschließlich als `<mp4-stem>.obs-events.json` neben der MP4.
- `artifact_type` ist `obs_event_sidecar`; `lifecycle.status` ist ausschließlich `finalized`.
- Erst dieses Artefakt ist ein möglicher Auto-Cut-Vertrag.

Ein Journal wird nicht durch Umbenennen zum Sidecar. Ein abgebrochenes, offenes, wiederhergestelltes oder nur teilweise lesbares Journal kann höchstens einen Recovery-Report erzeugen; es wird niemals zu einem Auto-Cut-fähigen Sidecar finalisiert.

### 4.4 Fehler und Recovery

Beim OBS-Start scannt das Plugin Journale im Zustand `recording`. Da nach einem Crash kein vertrauenswürdiges Stop-/Endcounter-Signal existiert, werden sie atomar im Recovery-Index als `aborted` registriert; die Originaljournale bleiben zur Diagnose erhalten. Der Auto-Cutter arbeitet für dazugehörige Medien nur im `no_sidecar_safe_mode`.

Sequenzlücken, eine abgeschnittene Zeile vor dem erfolgreichen Stoprecord, rückläufige QPC-/Framecounterwerte außerhalb erlaubter Pause, mehrfacher Output-Start, fehlende Producer-Konfiguration oder ein Output-Fehler erzeugen keinen finalen Sidecar. Ein nur am Dateiende abgeschnittener Record ist ebenfalls nicht reparierbar, wenn danach kein gültiger Stoprecord existiert.

## 5. Zeitbasis, Alignment und Genauigkeitsvertrag

### 5.1 Ehrliche Clock-Origin

`clock.origin="producer_monotonic_at_output_start_signal"`. Die Rohzeit ist Windows QueryPerformanceCounter, durch das Plugin in monotone Nanosekunden umgerechnet. Das ist der Zeitpunkt, an dem das Plugin das OBS-Output-Startsignal verarbeitet; er ist nicht der erste encodierte Frame und wird nicht so bezeichnet.

Primärer Eventanker ist der beim Callback gelesene `obs_output_get_total_frames()`-Wert. Sekundäranker ist QPC. Beide sind Messungen am Callback-Zeitpunkt, keine sichere Aussage darüber, auf welchem Frame ein visueller Inhalt erstmals sichtbar wurde.

### 5.2 Kalibrierung auf die Source-Timeline

Nach Dateischluss ermittelt der Finalizer mit `ffprobe`:

- ersten Video-PTS und ersten Audio-PTS;
- CFR-Framerate 60/1;
- decodierbare Video-Framezahl `N`;
- Medien- und Streamdauer;
- Streamlayout vor beziehungsweise nach Remux.

Seien `c0` und `c1` die Output-Framecounter am Start- und Stopanker. Für ein Event mit Counter `ce` gilt zunächst:

`mapped_source_frame = round((ce - c0) * N / (c1 - c0))`.

Der Finalizer prüft diese affine Counterabbildung gegen die zweisekündlichen QPC-/Counter-Proben. Für QPC-Fallbackevents wird pausierte reale Zeit aus der QPC-Differenz entfernt und über dieselben Kalibrierungsproben stückweise linear auf Sourceframes abgebildet. Pausenzeit wird daher nicht zur Source-Timeline addiert.

### 5.3 Zulässige Unsicherheit und Drift

Der Producer berechnet pro Event eine obere Unsicherheitsabschätzung:

- 100 ms Signal-/Callbackbudget für Recording, Szene, Media und Transition;
- 150 ms für manuelle Hotkeys;
- maximale gemessene Kalibrierungsabweichung;
- ein Frame Rundungsbudget;
- zusätzlich 50 ms, falls QPC statt Framecounter verwendet werden musste.

Ein Sidecar ist nur nutzbar, wenn:

- `c1 > c0` und `abs((c1 - c0) - N) <= 6` Frames;
- aktive Kalibrierungsproben höchstens fünf Sekunden auseinanderliegen;
- die maximale Kalibrierungsabweichung höchstens 50 ms beträgt;
- die QPC-vs-Counter-Drift nach Abzug der Pausen höchstens 500 ppm beträgt;
- kein schutzrelevantes Event mehr als 250 ms ausgewiesene Unsicherheit besitzt;
- jeder manuelle Marker einen Framecounterwert besitzt;
- die finale Dauer zur Counterspanne innerhalb sechs Frames passt.

Bei Überschreitung ist der Sidecar mit `E_SIDECAR_CLOCK_UNRELIABLE` für Auto-Cuts unbrauchbar. Drift wird nicht still korrigiert, wenn diese Gates verletzt sind. Die 250 ms sind eine maximale akzeptierte Unsicherheitsgrenze, keine Behauptung exakter Synchronität.

### 5.4 Rundung und zusätzlicher Schutz

Der im Sidecar gespeicherte `mapped_source_frame` ist ein kalibrierter Schätzwert. Der Consumer erweitert jeden schutzrelevanten Start nach links und jedes Ende nach rechts um:

`ceil(uncertainty_ms * 60 / 1000) + 2 Frames`.

Erst danach werden die konfigurierten Eventpuffer angewandt. Intervallstarts werden nach außen mit `floor`, Enden nach außen mit `ceil` gerundet; Schnittstarts werden nach innen mit `ceil`, Schnittenden nach innen mit `floor` gerundet. Alle Ergebnisse werden auf `[0, source_total_frames)` begrenzt.

## 6. Pause, Split, Remux, Rename und Abbruch

### 6.1 Pause und Fortsetzen: unterstützt

`recording_paused` und `recording_resumed` werden aus den Outputsignalen erfasst. Pauseintervalle müssen alternieren und dürfen nicht überlappen. Stop während Pause schließt das letzte Pauseintervall am Stopanker. Während Pause darf der Outputcounter höchstens um zwei Frames steigen; größere Bewegung macht die Zeitbasis unbrauchbar.

Die QPC-Wallclockdauer einer Pause wird dokumentiert, aber nicht auf Sourcezeit addiert. Pause und Resume erhalten denselben oder höchstens zwei Frames verschiedenen `mapped_source_frame`. Semantische Events während Pause erscheinen nur in `finalization.warnings` und erzeugen keinen Schutzbereich, weil sie nicht im aufgezeichneten Medium vorhanden sind. Ein fehlendes Resume vor normalem Stop ist zulässig; fehlende oder doppelte Pause-/Resume-Reihenfolge ist `E_SIDECAR_PAUSE_SEQUENCE`.

### 6.2 Dateisplitting: in v1 nicht unterstützt

OBS-Dateisplitting ist für Auto-Cuts in v1 ausgeschlossen. Das Plugin prüft beim Start die Recording-Konfiguration, protokolliert Split-Requests und überwacht Pfadwechsel sowie Counter-Resets. Aktiviertes Splitting, ein erfolgreicher Split-Request oder mehr als eine erzeugte Recordingdatei setzt `file_splitting_detected=true`. Der Finalizer veröffentlicht dann kein Sidecar und meldet `E_PRODUCER_SPLIT_UNSUPPORTED`. Der Auto-Cutter darf die einzelnen Dateien nur im no-sidecar-Safe-Mode bearbeiten.

### 6.3 Direct MP4 und Remux

Unterstützte Workflows:

1. **Direct MP4:** Der letzte Recordingpfad ist eine MP4. Nach Output-Stop, exklusivem Close-Gate, Stabilitätsprüfung, Probe und Hash wird sie gebunden.
2. **OBS Auto-Remux MKV → MP4:** Das Journal enthält den ursprünglichen MKV-Pfad und `remux.mode="obs_auto"`. Der Finalizer wartet höchstens zehn Minuten auf die gleichstämmige MP4 im selben Verzeichnis. Er akzeptiert sie nur, wenn Erstellzeit nach Recordingstart liegt, Video-/Audiostreamlayout kompatibel ist, die Video-Framezahl gleich ist und die normalisierten Dauern höchstens einen Frame abweichen. Die Clock wird gegen die finale MP4 neu kalibriert.
3. **Manueller Remux:** Das Journal bleibt `stopped_unfinalized`, bis der Nutzer dem Finalizer explizit genau eine Ziel-MP4 zuweist. Es gelten dieselben Stream-, Framezahl- und Dauergates. Vor dieser Zuweisung existiert kein finales Sidecar.

Der Producer remuxt nicht selbst und verändert keine Mediendatei. Ein nicht passender Remux erzeugt `E_REMUX_TARGET_MISMATCH`. MP4 und MKV werden nie allein über ähnlichen Namen oder ähnliche Dauer verknüpft.

### 6.4 Nachträgliches Umbenennen

Ein nach Finalisierung umbenanntes Video passt nicht mehr zu `source.file_name` und aktiviert Safe-Mode. Eine explizite Rebind-Funktion darf für dieselben Dateibytes ein neues Sidecar neben dem neuen Pfad erzeugen. Sie prüft erneut File-ID, Größe, Änderungszeit, vollständiges SHA-256 und Probe, trägt `lineage.renamed_from` ein und veröffentlicht atomar. Der alte Sidecar wird nicht verändert oder gelöscht.

### 6.5 Nicht sauber beendete Aufnahme

Fehlt ein erfolgreiches Output-Stop-Signal, ist die Aufnahme für Sidecar-Auto-Cuts nicht finalisierbar — auch wenn ein Container reparierbar oder abspielbar ist. Ergebnis ist `E_JOURNAL_INCOMPLETE` und no-sidecar-Safe-Mode. Das erfüllt den Schutzvertrag ohne eine nicht belegbare Endkalibrierung zu erfinden.

## 7. Hashing, Dateischluss und atomare Finalisierung

Nach erfolgreichem Output-Stop wartet der Finalizer zunächst auf drei unveränderte Größen-/mtime-Proben im Abstand von einer Sekunde. Danach öffnet er die Ziel-MP4 über Win32 `CreateFileW` mit Lesezugriff und ausschließlich `FILE_SHARE_READ`; ein noch aktiver Writer oder Rename/Delete-Vorgang verhindert damit das Close-Gate. Durch denselben Handle werden File-ID, Größe, letzte Änderungszeit und vollständiges SHA-256 gelesen.

SHA-256 wird in 8-MiB-Blöcken berechnet. Zwischen Blöcken wird ein Cancel-Token geprüft. Abbruch erzeugt `E_HASH_CANCELLED`, keinen Cacheeintrag und kein Sidecar; ein späterer Retry beginnt sicher erneut. Nach Hashing werden File-ID, Größe und mtime am offenen Handle erneut geprüft. Jede Änderung ist `E_SOURCE_CHANGED_DURING_HASH`.

Der optionale Hash-Cache liegt als atomar geschriebenes `%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\hash-cache-v1.json` vor. Schlüssel sind normalisierter casefolded absoluter Pfad, NTFS-Volume-ID, File-ID, Größe und mtime in 100-ns-Auflösung. Ein Cachetreffer ist nur die Wiederverwendung eines früher vollständig berechneten SHA-256; jede Feldänderung erzwingt vollständiges Rehashing. Die Erstfinalisierung hasht immer vollständig.

Das fertige JSON wird als `<stem>.obs-events.json.tmp.<run-id>` im Zielverzeichnis geschrieben, UTF-8 ohne BOM, geflusht und per `FlushFileBuffers` persistiert. Danach ersetzt `MoveFileExW(..., MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)` atomar ausschließlich ein Sidecar derselben Recording-Session; ein fremdes bestehendes Sidecar ist `E_SIDECAR_COLLISION`. Erst nach erfolgreichem Rename erhält das Journal einen separaten Finalization-Receipt. Mediendatei und Journal werden nicht überschrieben.

## 8. Datenverträge und Schema-Versionen

| Artefakt | Version | Zweck |
|---|---:|---|
| Recording Journal | `journal_schema_version: 1.0` | Append-only Rohmessungen während OBS-Aufnahme; nie Consumer-Sidecar. |
| Finales OBS-Sidecar | `schema_version: 1.1` | Finalisierter, quellgebundener Schutzvertrag. |
| Projekt/Analyse/EDL/Mapping/Review | jeweils `schema_version: 1.0` | Unveränderte v0.1-Verträge, ergänzt um Sidecar-/Policy-Felder. |

Die unterschiedlichen Versionsnummern sind beabsichtigt. `1.0` im Journal oder in EDL bedeutet niemals Sidecar 1.0.

### 8.1 Projektdatei

```json
{
  "project_schema_version": "1.0",
  "project_id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-07-12T14:30:00+02:00",
  "source": {"path": "D:\\media\\recording.mp4", "file_name": "recording.mp4", "size_bytes": 12003400567, "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "obs_sidecar_path": "D:\\media\\recording.obs-events.json",
  "required_sidecar_schema_version": "1.1",
  "workspace": "F:\\MatrixMarketAutoEdit\\.matrix-auto-cutter\\projects\\550e8400-e29b-41d4-a716-446655440000",
  "export_directory": "F:\\MatrixMarketAutoEdit",
  "profile": "youtube_1440p60_v1",
  "source_immutable": true
}
```

### 8.2 Recording Journal: repräsentative Records

Auf Disk sind dies einzelne NDJSON-Zeilen ohne Arrayklammern. Die Arraydarstellung macht das Beispiel als Ganzes valides JSON. Das Journal enthält absichtlich keine finale `source`-Identität. Kalibrierungs- und weitere Eventrecords sind zur Kürze ausgelassen; der Ausschnitt ist daher kein finalisierbares Gesamtjournal.

```json
[
  {
    "artifact_type": "recording_event_journal",
    "journal_schema_version": "1.0",
    "record_type": "header",
    "sequence": 0,
    "recording_session_id": "835fc47a-7e8c-4700-9f6f-8f7e23ac740c",
    "lifecycle_status": "recording",
    "producer": {"name": "matrix-auto-cutter-obs-producer", "version": "0.1.0", "obs_version": "32.2.0"},
    "clock": {"source": "windows_qpc", "unit": "ns", "origin": "producer_monotonic_at_output_start_signal"},
    "capabilities": {"pause_resume": "supported_v1", "file_splitting": "unsupported_v1"},
    "initial_output_path": "F:\\VIDEO ROHABLAGE\\aufnahme.mkv"
  },
  {
    "artifact_type": "recording_event_journal",
    "journal_schema_version": "1.0",
    "record_type": "event",
    "sequence": 1,
    "event_id": "6ba7b814-9dad-4b8a-92fb-2a41f5468719",
    "event_type": "stinger_started",
    "monotonic_ns": 489120034500,
    "output_frame_count": 18004,
    "recording_paused": false,
    "source_uuid": "38e0772c-c6c3-442e-a29d-318e111f632e"
  },
  {
    "artifact_type": "recording_event_journal",
    "journal_schema_version": "1.0",
    "record_type": "stop",
    "sequence": 2,
    "lifecycle_status": "stopped_unfinalized",
    "monotonic_ns": 4089120034500,
    "output_frame_count": 215998,
    "last_recording_path": "F:\\VIDEO ROHABLAGE\\aufnahme.mkv",
    "output_result": "success",
    "file_splitting_detected": false
  }
]
```

### 8.3 Kanonisches Sidecar-JSON-Schema 1.1

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://dimensionwithin.local/schemas/obs-events-1.1.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["artifact_type", "schema_version", "producer", "lifecycle", "recording_session_id", "source", "clock", "capabilities", "pause_intervals", "events", "finalization"],
  "properties": {
    "artifact_type": {"const": "obs_event_sidecar"},
    "schema_version": {"const": "1.1"},
    "producer": {
      "type": "object", "additionalProperties": false,
      "required": ["name", "version", "obs_version", "finalizer_version"],
      "properties": {
        "name": {"const": "matrix-auto-cutter-obs-producer"},
        "version": {"type": "string", "minLength": 1},
        "obs_version": {"type": "string", "minLength": 1},
        "finalizer_version": {"type": "string", "minLength": 1}
      }
    },
    "lifecycle": {
      "type": "object", "additionalProperties": false,
      "required": ["status", "journal_schema_version", "finalized_at", "finalizer_run_id"],
      "properties": {
        "status": {"const": "finalized"},
        "journal_schema_version": {"const": "1.0"},
        "finalized_at": {"type": "string", "format": "date-time"},
        "finalizer_run_id": {"type": "string", "format": "uuid"}
      }
    },
    "recording_session_id": {"type": "string", "format": "uuid"},
    "source": {
      "type": "object", "additionalProperties": false,
      "required": ["file_name", "size_bytes", "sha256", "duration_ms", "video_frame_count", "fps_num", "fps_den", "video_start_time_ns", "audio_start_time_ns", "binding"],
      "properties": {
        "file_name": {"type": "string", "pattern": "^[^/\\\\]+\\.mp4$"},
        "size_bytes": {"type": "integer", "minimum": 1},
        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "duration_ms": {"type": "integer", "minimum": 1},
        "video_frame_count": {"type": "integer", "minimum": 1},
        "fps_num": {"const": 60},
        "fps_den": {"const": 1},
        "video_start_time_ns": {"type": "integer"},
        "audio_start_time_ns": {"type": "integer"},
        "binding": {"enum": ["direct_mp4", "obs_auto_remux", "manual_remux", "renamed_rebind"]}
      }
    },
    "clock": {
      "type": "object", "additionalProperties": false,
      "required": ["origin", "monotonic_source", "mapping", "counter_start", "counter_end", "drift_ppm", "max_calibration_residual_ms", "max_event_uncertainty_ms", "calibration_sample_count"],
      "properties": {
        "origin": {"const": "producer_monotonic_at_output_start_signal"},
        "monotonic_source": {"const": "windows_qpc"},
        "mapping": {"const": "obs_output_frame_counter_calibrated_to_final_video_frames"},
        "counter_start": {"type": "integer", "minimum": 0},
        "counter_end": {"type": "integer", "minimum": 1},
        "drift_ppm": {"type": "number", "minimum": 0, "maximum": 500},
        "max_calibration_residual_ms": {"type": "number", "minimum": 0, "maximum": 50},
        "max_event_uncertainty_ms": {"type": "number", "minimum": 0, "maximum": 250},
        "calibration_sample_count": {"type": "integer", "minimum": 2}
      }
    },
    "capabilities": {
      "type": "object", "additionalProperties": false,
      "required": ["pause_resume", "file_splitting", "remux"],
      "properties": {
        "pause_resume": {"const": "supported_v1"},
        "file_splitting": {"const": "not_used_unsupported_v1"},
        "remux": {"enum": ["not_used", "obs_auto_verified", "manual_verified", "rebind_verified"]}
      }
    },
    "pause_intervals": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["pause_event_id", "close_event_id", "end_reason", "pause_monotonic_ns", "end_monotonic_ns", "mapped_source_frame_before", "mapped_source_frame_after"],
        "properties": {
          "pause_event_id": {"type": "string", "format": "uuid"},
          "close_event_id": {"type": "string", "format": "uuid"},
          "end_reason": {"enum": ["resumed", "recording_stopped_while_paused"]},
          "pause_monotonic_ns": {"type": "integer", "minimum": 0},
          "end_monotonic_ns": {"type": "integer", "minimum": 0},
          "mapped_source_frame_before": {"type": "integer", "minimum": 0},
          "mapped_source_frame_after": {"type": "integer", "minimum": 0}
        }
      }
    },
    "events": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["event_id", "type", "mapped_source_frame", "uncertainty_ms", "clock_sample", "protection"],
        "properties": {
          "event_id": {"type": "string", "format": "uuid"},
          "type": {"enum": ["recording_started", "recording_stopped", "recording_paused", "recording_resumed", "scene_changed", "intro_started", "intro_ended", "outro_started", "outro_ended", "stinger_started", "stinger_ended", "manual_protection"]},
          "mapped_source_frame": {"type": "integer", "minimum": 0},
          "end_mapped_source_frame": {"type": "integer", "minimum": 0},
          "uncertainty_ms": {"type": "number", "minimum": 0, "maximum": 250},
          "pair_id": {"type": "string", "format": "uuid"},
          "scene_name": {"type": "string", "maxLength": 200},
          "label": {"type": "string", "maxLength": 500},
          "clock_sample": {
            "type": "object", "additionalProperties": false,
            "required": ["monotonic_ns", "output_frame_count", "mapping_basis"],
            "properties": {
              "monotonic_ns": {"type": "integer", "minimum": 0},
              "output_frame_count": {"type": ["integer", "null"], "minimum": 0},
              "mapping_basis": {"enum": ["output_frame_counter", "qpc_fallback"]}
            }
          },
          "protection": {
            "type": "object", "additionalProperties": false,
            "required": ["level", "buffer_before_ms", "buffer_after_ms", "policy"],
            "properties": {
              "level": {"enum": ["hard", "soft"]},
              "buffer_before_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
              "buffer_after_ms": {"type": "integer", "minimum": 0, "maximum": 10000},
              "policy": {
                "type": "object", "additionalProperties": false,
                "required": ["blocks_time_edits", "blocks_overlays", "blocks_local_audio_repair", "allows_global_mastering"],
                "properties": {
                  "blocks_time_edits": {"type": "boolean"},
                  "blocks_overlays": {"type": "boolean"},
                  "blocks_local_audio_repair": {"type": "boolean"},
                  "allows_global_mastering": {"const": true}
                }
              }
            }
          }
        }
      }
    },
    "finalization": {
      "type": "object", "additionalProperties": false,
      "required": ["file_closed_verified", "full_sha256_verified", "probe_verified", "journal_complete", "warnings"],
      "properties": {
        "file_closed_verified": {"const": true},
        "full_sha256_verified": {"const": true},
        "probe_verified": {"const": true},
        "journal_complete": {"const": true},
        "warnings": {"type": "array", "items": {"type": "string"}}
      }
    }
  }
}
```

### 8.4 Finalisiertes Sidecar 1.1: repräsentatives Beispiel

```json
{
  "artifact_type": "obs_event_sidecar",
  "schema_version": "1.1",
  "producer": {"name": "matrix-auto-cutter-obs-producer", "version": "0.1.0", "obs_version": "32.2.0", "finalizer_version": "0.1.0"},
  "lifecycle": {"status": "finalized", "journal_schema_version": "1.0", "finalized_at": "2026-07-12T16:00:00+02:00", "finalizer_run_id": "2e157a84-2e31-49d9-b64e-494c24f8f612"},
  "recording_session_id": "835fc47a-7e8c-4700-9f6f-8f7e23ac740c",
  "source": {
    "file_name": "aufnahme.mp4", "size_bytes": 12003400567,
    "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "duration_ms": 3600000, "video_frame_count": 216000, "fps_num": 60, "fps_den": 1,
    "video_start_time_ns": 46000000, "audio_start_time_ns": 49000000, "binding": "obs_auto_remux"
  },
  "clock": {
    "origin": "producer_monotonic_at_output_start_signal", "monotonic_source": "windows_qpc",
    "mapping": "obs_output_frame_counter_calibrated_to_final_video_frames",
    "counter_start": 0, "counter_end": 215998, "drift_ppm": 22.4,
    "max_calibration_residual_ms": 14.8, "max_event_uncertainty_ms": 181.5,
    "calibration_sample_count": 1798
  },
  "capabilities": {"pause_resume": "supported_v1", "file_splitting": "not_used_unsupported_v1", "remux": "obs_auto_verified"},
  "pause_intervals": [
    {"pause_event_id": "2b26e33d-5d33-478c-a2f6-51eef272fa3f", "close_event_id": "a4195036-14ad-4dd6-af39-e9b8a7fc725e", "end_reason": "resumed", "pause_monotonic_ns": 1600000000000, "end_monotonic_ns": 1630000000000, "mapped_source_frame_before": 90000, "mapped_source_frame_after": 90001}
  ],
  "events": [
    {
      "event_id": "bfc5ea5a-593f-4261-8262-6d6e508bc6df", "type": "recording_started", "mapped_source_frame": 0, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 1000000000, "output_frame_count": 0, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "hard", "buffer_before_ms": 0, "buffer_after_ms": 1000, "policy": {"blocks_time_edits": true, "blocks_overlays": true, "blocks_local_audio_repair": true, "allows_global_mastering": true}}
    },
    {
      "event_id": "032a6ce3-3ab3-4c9c-adf9-3c4f8836a445", "type": "intro_started", "pair_id": "b950d183-bf61-4df5-9419-b121e05ac366", "mapped_source_frame": 480, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 9000000000, "output_frame_count": 480, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "hard", "buffer_before_ms": 250, "buffer_after_ms": 0, "policy": {"blocks_time_edits": true, "blocks_overlays": true, "blocks_local_audio_repair": true, "allows_global_mastering": true}}
    },
    {
      "event_id": "69a44a75-bd16-4100-99d1-cc24d4fd8480", "type": "intro_ended", "pair_id": "b950d183-bf61-4df5-9419-b121e05ac366", "mapped_source_frame": 843, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 15046000000, "output_frame_count": 843, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "hard", "buffer_before_ms": 0, "buffer_after_ms": 250, "policy": {"blocks_time_edits": true, "blocks_overlays": true, "blocks_local_audio_repair": true, "allows_global_mastering": true}}
    },
    {
      "event_id": "2b26e33d-5d33-478c-a2f6-51eef272fa3f", "type": "recording_paused", "mapped_source_frame": 90000, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 1600000000000, "output_frame_count": 89999, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "soft", "buffer_before_ms": 0, "buffer_after_ms": 0, "policy": {"blocks_time_edits": false, "blocks_overlays": false, "blocks_local_audio_repair": false, "allows_global_mastering": true}}
    },
    {
      "event_id": "a4195036-14ad-4dd6-af39-e9b8a7fc725e", "type": "recording_resumed", "mapped_source_frame": 90001, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 1630000000000, "output_frame_count": 90000, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "soft", "buffer_before_ms": 0, "buffer_after_ms": 0, "policy": {"blocks_time_edits": false, "blocks_overlays": false, "blocks_local_audio_repair": false, "allows_global_mastering": true}}
    },
    {
      "event_id": "94010443-b4b9-47f5-b822-83b3aed466ef", "type": "manual_protection", "mapped_source_frame": 120000, "end_mapped_source_frame": 120600, "uncertainty_ms": 164.8, "label": "wichtige Chartpause",
      "clock_sample": {"monotonic_ns": 2031000000000, "output_frame_count": 120000, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "hard", "buffer_before_ms": 500, "buffer_after_ms": 1000, "policy": {"blocks_time_edits": true, "blocks_overlays": false, "blocks_local_audio_repair": true, "allows_global_mastering": true}}
    },
    {
      "event_id": "e3fdaf55-f895-49fa-913d-a7b20fa6cc41", "type": "recording_stopped", "mapped_source_frame": 216000, "uncertainty_ms": 114.8,
      "clock_sample": {"monotonic_ns": 3631000000000, "output_frame_count": 215998, "mapping_basis": "output_frame_counter"},
      "protection": {"level": "hard", "buffer_before_ms": 1000, "buffer_after_ms": 0, "policy": {"blocks_time_edits": true, "blocks_overlays": true, "blocks_local_audio_repair": true, "allows_global_mastering": true}}
    }
  ],
  "finalization": {"file_closed_verified": true, "full_sha256_verified": true, "probe_verified": true, "journal_complete": true, "warnings": []}
}
```

### 8.5 Semantikregeln über das JSON Schema hinaus

- Nur Sidecar 1.1 mit finalisiertem Lifecycle ist für Auto-Cuts zulässig. Sidecar 1.0 ist nicht migrationsfähig, weil Producer-, Clock- und Finalisierungsnachweis fehlen; es führt zu Safe-Mode.
- Dateiname, Größe, SHA-256, Framezahl und Dauer müssen beim Consumer passen. Dauer toleriert höchstens einen Frame; SHA-256 keine Abweichung.
- Recording-Start mappt auf Frame 0, Recording-Stop auf `video_frame_count` als exklusives Ende.
- Intro-, Outro- und Stinger-Events brauchen eindeutige `pair_id`s. Start ohne Ende schützt konservativ bis Source-Ende, Ende ohne Start ab Source-Start. Mehrdeutige Paare machen den Sidecar unbrauchbar.
- `end_mapped_source_frame` ist nur bei manueller Protection zulässig. Pause-/Resume-Paare müssen mit `pause_intervals` übereinstimmen; bei `end_reason=resumed` verweist `close_event_id` auf `recording_resumed`, bei Stop während Pause auf `recording_stopped`.
- Automatische OBS-Events dürfen ihre Pflichtpolicy nicht abschwächen; `allows_global_mastering=false` ist in v1 ausgeschlossen.

### 8.6 Betroffene Consumer-Artefakte

```json
{
  "schema_version": "1.0",
  "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "sidecar_schema_version": "1.1",
  "time_base": {"fps_num": 60, "fps_den": 1},
  "ranges": [
    {
      "protection_id": "prot-0001", "source_start_frame": 456, "source_end_frame": 867, "level": "hard",
      "source_event_ids": ["032a6ce3-3ab3-4c9c-adf9-3c4f8836a445", "69a44a75-bd16-4100-99d1-cc24d4fd8480"],
      "uncertainty_padding_frames": 9,
      "policy": {"blocks_time_edits": true, "blocks_overlays": true, "blocks_local_audio_repair": true, "allows_global_mastering": true}
    }
  ]
}
```

```json
{
  "schema_version": "1.0",
  "sidecar_mode": "validated_sidecar_1_1",
  "candidates": [
    {"candidate_id": "cutcand-0042", "kind": "dead_air", "source_start_frame": 18000, "source_end_frame": 18120, "confidence": 0.97, "decision": "accept", "evidence": ["rms_below_adaptive_threshold_2.0s", "no_words"], "conflicts": [], "policy_checks": {"blocks_time_edits": "pass", "blocks_local_audio_repair": "not_applicable"}}
  ]
}
```

```json
{
  "schema_version": "1.0", "source_total_frames": 216000, "output_total_frames": 215880,
  "sidecar_mode": "validated_sidecar_1_1",
  "cuts": [{"edit_id": "edit-0001", "source_start_frame": 18000, "source_end_frame": 18120, "kind": "dead_air", "confidence": 0.97, "automatic": true}],
  "validation": {"valid": true, "blocked_time_edit_intersections": 0}
}
```

```json
{
  "schema_version": "1.0",
  "segments": [
    {"keep_id": "keep-0001", "source_start_frame": 0, "source_end_frame": 18000, "output_start_frame": 0, "output_end_frame": 18000},
    {"keep_id": "keep-0002", "source_start_frame": 18120, "source_end_frame": 216000, "output_start_frame": 18000, "output_end_frame": 215880}
  ],
  "removed": [{"source_start_frame": 18000, "source_end_frame": 18120, "edit_id": "edit-0001"}]
}
```

```json
{
  "schema_version": "1.0",
  "mapped_overlay_block_ranges": [{"protection_id": "prot-0001", "keep_id": "keep-0001", "output_start_frame": 456, "output_end_frame": 867}],
  "events": [
    {"overlay_id": "ov-0012", "category": "referral", "asset_file": "referral.webm", "source_anchor_frame": 72075, "output_start_frame": 71967, "output_end_frame": 72109, "mapping_keep_id": "keep-0002", "position": "bottom_left", "sound": {"mode": "none"}, "checks": {"blocks_overlays": "pass", "collision": "pass"}}
  ]
}
```

```json
{
  "schema_version": "1.0", "project_id": "550e8400-e29b-41d4-a716-446655440000", "status": "success_with_warnings", "mode": "validated_sidecar_1_1",
  "sidecar": {"schema_version": "1.1", "lifecycle": "finalized", "max_event_uncertainty_ms": 181.5},
  "summary": {"accepted_cuts": 1, "overlays_planned": 1, "local_audio_repairs_blocked": 2},
  "verification": {"decode": "pass", "fps": "60/1", "av_sync_max_frames": 1, "source_immutability": "pass"}
}
```

## 9. Erweiterte Protection Policy

### 9.1 Bedeutung und Defaults

- `blocks_time_edits`: EDL-Zeitschnitte dürfen das Intervall nicht entfernen, verkürzen oder verschieben. `hard` ist absolutes Veto; `soft` nutzt die konservativen v0.1-Schwellen.
- `blocks_overlays`: Sichtbare Notifications sowie zugehörige oder sound-only Notification-Ereignisse dürfen das Intervall nicht überlagern.
- `blocks_local_audio_repair`: Bereichsbezogenes De-Click, De-Smack, lokale Rauschminderung, lokale Gain-/Mute-Reparatur und andere selektive Audiobearbeitung sind verboten.
- `allows_global_mastering`: Abschließende globale Loudness-Normalisierung und Peak-Kontrolle bleiben erlaubt; v1 erzwingt `true`.

Intro, Outro, Stinger, Szenenwechsel-Puffer sowie Recording-Anfang/-Ende sind `hard` und setzen die drei Blockflags auf `true`, globales Mastering auf `true`. Manuelle Protection ist standardmäßig ebenso streng. Sicher konfigurierbar sind bei manueller Protection `blocks_overlays` und `blocks_local_audio_repair`; nur `level=soft` darf außerdem `blocks_time_edits=false` setzen. Automatische OBS-Policies können nicht gelockert werden. Chart-/Dramaturgieschutz ist `soft`, blockiert Time-Edits nach Soft-Regel und lokale Audioreparatur, standardmäßig aber keine Overlays.

### 9.2 Union und Priorität

Der Resolver partitioniert an allen Bereichsgrenzen in elementare disjunkte Intervalle. Blockflags werden per logischem OR vereinigt, `allows_global_mastering` per AND, `hard` gewinnt über `soft`, größere Puffer/Unsicherheitsausdehnungen gewinnen. Keine manuelle Policy darf eine automatische OBS-Policy lockern.

Ein lokaler Audioreparaturkandidat mit Schnittmenge zu `blocks_local_audio_repair=true` wird vollständig verworfen, nicht gekürzt. Globale Lautheits-/Peak-Kontrolle läuft anschließend über die gesamte erhaltene Ausgabe.

## 10. Sidecar-Consumer, EDL und Schutz-Mapping

Der Consumer ordnet weiterhin ausschließlich `<stem>.obs-events.json` neben `<stem>.mp4` zu. Vor Auto-Cuts prüft er Sidecar 1.1, Lifecycle, Producer, Source-Identität, Clock-Gates, Splitstatus, Pausekonsistenz und Policysemantik. Fehlend, 1.0, unfinalisiert, gehasht-abgebrochen, identitätsfalsch oder zeitlich unzuverlässig bedeutet `no_sidecar_safe_mode` und leere zeitentfernende EDL.

EDL-Regeln aus v0.1 bleiben: halboffene Intervalle, keine Hard-Intersection, Wortguards, Chartschutz, keine Mikro-Keep-Inseln, konservative Confidence-Schwellen und keine direkte Kandidatenrenderung. `dead_air` braucht mindestens 0,92, `äh/ähm` 0,97, andere Füllwörter 0,99, Selbstkorrektur-Autocut 0,98. Stilistische Wiederholungen bleiben erhalten. Lokale Mouth-Click-Reparatur ist nur isoliert außerhalb Sprache ab 0,98 und zusätzlich nur außerhalb blockierter Audiobereiche zulässig.

### 10.1 Mapping von `blocks_overlays`

Nach validierter EDL wird jedes Source-Schutzintervall `[p0,p1)` mit jedem Keep-Segment `[s0,s1)` geschnitten. Für eine nichtleere Schnittmenge `[a,b)` entsteht:

`[output_start + (a-s0), output_start + (b-s0))`.

Entfernte Teile erzeugen kein Outputintervall. Getrennte Fragmente bleiben getrennt; nur direkt angrenzende Outputintervalle mit identischer Policy und ohne Source-Diskontinuität werden vereinigt. Der Overlay Scheduler prüft sichtbare Dauer, sechs Frames Rand, Notification-Soundfenster und eine Sekunde Post-Gap gegen diese gemappten Sperren. Es wird niemals mit Source-Zeitstempeln gerendert.

### 10.2 End-to-End-Datenfluss

1. Das OBS-Plugin schreibt während der Aufnahme ausschließlich das Rohjournal; der Finalizer veröffentlicht nach Close/Probe/Hash gegebenenfalls Sidecar 1.1.
2. Die GUI importiert die MP4, erzeugt eine UUIDv4-Projekt-ID und speichert die Source ausschließlich lesend.
3. `ffprobe`, vollständiger Hash beziehungsweise starker Hash-Cachetreffer und Sidecar-Consumer prüfen Medienprofil, Identität, Lifecycle und Clock.
4. Der Protection Resolver materialisiert Unsicherheits-, Event- und Policyintervalle; Safe-Mode bleibt bei jedem Vertragsfehler schnittlos.
5. FFmpeg erzeugt Analyseaudio; Audioanalyse und `faster-whisper` schreiben Source-Timeline-Artefakte.
6. Der Cut Analyzer erzeugt Kandidaten; Time- und Audio-Policies entscheiden über Accept/Review/Reject.
7. Der EDL Validator erzeugt Cuts und das Keep-Komplement.
8. Timeline Mapper überträgt Source-Ereignisse und Overlay-Sperren auf Outputframes.
9. CTA Classifier und Scheduler planen nur erhaltene, konfliktfreie Notifications.
10. Asset Registry validiert beziehungsweise erzeugt den Alpha-Cache; Sound Scheduler respektiert dieselben Sperrfenster.
11. Render Planner normalisiert Startoffsets, kompiliert Audio-/Videopläne und rendert zunächst `.partial.mp4`.
12. Output Verifier prüft Decode, Frames, Sync, Loudness und Originalhash; Review Builder schreibt JSON/HTML, erst dann wird final veröffentlicht.

## 11. Analyse-, CTA-, Asset- und Review-Architektur

Die v0.1-Entscheidungen bleiben verbindlich:

- Audioanalyse dekodiert Mono-Float32 mit 48 kHz; 20-ms-RMS und Noise Floor erzeugen Stillekandidaten. Schwellwert ist `clamp(noise_floor_dbfs + 6, -55, -42)`, Mindeststille 1,2 s, Sprachhandles je 350 ms, entfernbare Mitte mindestens 500 ms.
- `faster-whisper large-v3` läuft bevorzugt CUDA/float16, nach Testinferenz ersatzweise CPU/int8; kein Modelldownload im Job.
- CTA-Scoring: Phrase +0,55, Handlungsverb +0,20, Objekt +0,15, Zuschauer-/Linkkontext +0,10, konsistenter Kontext +0,10; Multiplikation mit Wort-Confidence. Auto ab 0,78, Review 0,60–0,779.
- Kanalmitglied und Community bleiben getrennt. Referral gewinnt bei Link/Code-Absicht vor Hyperliquid; Kanalmitglied gewinnt bei „Mitglied werden“ vor Community. Neutrale Marktmitglieder lösen nichts aus.
- Zentrale Limits bleiben: global maximal 6, Startabstand 30 s, keine Overlays in ersten 20/letzten 15 s, maximal eins gleichzeitig; Abo und Like maximal 2, Kommentar 2, alle übrigen Kategorien 1, Hyperliquid/Referral zusammen 1.
- Positionen sind ausschließlich `bottom_left` und `top_right`, Safe Margin 80 px bei 2560×1440; `bottom_right` bleibt verboten.
- Die zehn CTA-WebMs aus `dimensionwithin-overlays-webm.zip` werden nach Package-/Stream-/Geometrieprüfung über Crop plus `alphamerge` in hashadressierten ProRes-4444-Cache konvertiert. `lowerthird.webm`, `card.webm`, Intro und Stinger werden nicht automatisch geplant.
- Sichtbarkeit und Sound bleiben unabhängig konfigurierbar. Alle Defaults sind stumm; `glocke.webm`-Audio braucht vorheriges Audit. Notification-Sound wird auf −24 LUFS short-term vorbereitet, Defaultgain −12 dB; optionales Ducking maximal 2,5 dB, Attack 30 ms, Release 250 ms.
- Review bleibt schema-validiertes JSON plus escaped, statisches HTML ohne Server oder externe Ressourcen.

## 12. Renderstrategie und Startoffsets

### 12.1 Normalisierung von Source-Audio und -Video

Die Source-Timeline hat Frame 0 am ersten decodierten Video-Frame, nicht am Container-`format.start_time`. `ffprobe` liefert Video- und Audio-Start-PTS mit jeweiliger Stream-Timebase. Der Planner rechnet beide rational, niemals über gerundete Dezimalsekunden.

- Video: erster decodierter Video-PTS wird subtrahiert; danach wird CFR 60/1 verifiziert.
- Audio beginnt relativ zum Videoepoch. Beginnt Audio früher, werden Samples vor Videoepoch entfernt. Beginnt Audio später, wird exakt die rationale Differenz als Stille vorangestellt. Danach wird auf 48 kHz resampled und mit `asetpts=N/SR/TB` bei null neu gesetzt.
- Der normalisierte Audiostrom wird am Videoende gepadded oder getrimmt; zulässige Korrektur höchstens ein Frame. Größere Start-/Endabweichung ist `E_INPUT_AV_OFFSET_UNSUPPORTED` und sperrt Autoediting.

Damit werden Audio und Video vor jeder Frameauswahl auf dieselbe Source-Timeline gebracht. Remuxbedingte Container-Startoffsets werden nicht mit Producer-QPC verwechselt.

### 12.2 800 Samples pro Frame: Phase-5-Hypothese

Bei 48 kHz und 60 FPS entsprechen rechnerisch 800 Samples einem Video-Frame. Die geplante FFmpeg-Kette `asetnsamples=800` plus framegleiche `aselect`-/`asetpts`-Auswahl ist jedoch eine Implementierungshypothese. Phase 5 muss beweisen, dass FFmpeg bei Source-Offsets, Pause, mehreren unmittelbar folgenden Cuts und langen Aufnahmen exakt die erwartete Sample- und Framezahl liefert.

Bis dieser Golden-Test bestanden ist, darf die Strategie nicht als Synchronitätsgarantie in echten Auto-Cuts verwendet werden. Scheitert der Test, bleibt zeitliches Rendering blockiert und benötigt eine versionierte Architekturkorrektur; es gibt keinen stillen Fallback.

### 12.3 Audio- und Videoausgabe

Nach erfolgreichem Phase-5-Gate bleibt die v0.1-Chain: framegleiche Auswahl, fünf Millisekunden Boundary-Fades ohne Daueränderung, nur erlaubte lokale Reparaturen, sanfter Kompressor bei nachgewiesenem Bedarf, Notification-Mix, verlustfreies `edit-mix.wav`, Loudness-Messpass, linearer zweiter `loudnorm`-Pass auf −14 LUFS/LRA 11/−1,5 dBTP, `alimiter`, `mastered-audio.wav`, einmaliges AAC-Encoding mit 320 kbit/s.

Video wird einmal final mit `libx264`, CRF 18, preset slow, High Profile, `yuv420p`, Source-Auflösung, CFR 60/1 und `+faststart` encodiert. Overlays verwenden ausschließlich Outputframes. Verifier: vollständiger Decode, erwartete Framezahl, Dauer innerhalb eines Frames, A/V-Sync höchstens ein Frame, −14 ±0,5 LUFS und höchstens −1,5 dBTP. Veröffentlichung erfolgt erst danach atomar aus `.partial.mp4`.

## 13. Konfiguration, Fehler, Logging und Verzeichnisse

Konfigurationspriorität bleibt: eingebettete Defaults < `%APPDATA%\DimensionWithin\MatrixAutoCutter\config.toml` < Projekt-Overrides. Unabschaltbare Invarianten sind Originalschutz, Sidecar-Finalisierung/Hash, Clock-Gates, Hard-Protection, Bottom-right-Verbot, Source-to-Output-Mapping, Split-Sperre, Policyunion und atomare Veröffentlichung.

Der Preflight übernimmt die v0.1-Gates: Windows x64, FFmpeg/ffprobe gleiche Major-Version ≥ 7, benötigte Filter/Encoder, CFR 60/1, decodierbare Audio-/Videospuren, Stream-Startoffsete, freier Speicher `max(20 GB, 2 × geschätzte Ausgabe + Cachebedarf)`, lokales `large-v3`-Modell samt Hash, CUDA-Testinferenz mit explizitem CPU-Fallback, Asset-/Cachehashes und Schreibtests nur in erlaubten Arbeitszielen. Für den Producer kommen OBS 32.x, Plugin-/Finalizerversion, gültige Source-/Transition-UUID-Konfiguration und Journalverzeichnis hinzu.

Erweiterte Fehlercodes:

- `E_PRODUCER_UNSUPPORTED_OBS`, `E_PRODUCER_CONFIG`, `E_PRODUCER_SPLIT_UNSUPPORTED`;
- `E_JOURNAL_INCOMPLETE`, `E_JOURNAL_SEQUENCE`, `E_JOURNAL_OUTPUT_FAILURE`;
- `E_SIDECAR_NOT_FINALIZED`, `E_SIDECAR_VERSION`, `E_SIDECAR_IDENTITY`, `E_SIDECAR_CLOCK_UNRELIABLE`, `E_SIDECAR_PAUSE_SEQUENCE`, `E_SIDECAR_POLICY`, `E_SIDECAR_COLLISION`;
- `E_FILE_NOT_CLOSED`, `E_HASH_CANCELLED`, `E_SOURCE_CHANGED_DURING_HASH`;
- `E_REMUX_TIMEOUT`, `E_REMUX_TARGET_MISMATCH`, `E_REBIND_IDENTITY`;
- `E_INPUT_AV_OFFSET_UNSUPPORTED` sowie die v0.1-Gruppen `E_INPUT_*`, `E_ASSET_*`, `E_MODEL_*`, `E_ANALYSIS_*`, `E_EDL_*`, `E_RENDER_*`, `E_VERIFY_*`, `E_IO_*`.

Alle Fehler tragen Code, deutschen Nutzertext, technischen Kontext, Artefakt-ID und `retryable`. Nur definierte Safe-Modes sind zulässig. Producerlog und Journal enthalten keine Secrets. Auto-Cutter-Logs bleiben NDJSON; FFmpeg-Ausgabe erhält eigene Dateien, zehn mal 20 MB Rotation pro Job.

```text
%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\
├─ producer\
│  ├─ journals\<session-id>.recording-journal.ndjson
│  ├─ finalization-receipts\<session-id>.json
│  ├─ recovery-index.json
│  └─ producer.log
├─ hash-cache-v1.json
├─ cache\assets\<cache-key>\{overlay.mov,probe.json,validation.json}
└─ models\faster-whisper-large-v3\...

F:\MatrixMarketAutoEdit\.matrix-auto-cutter\projects\<project-id>\
├─ project.json
├─ job.json
├─ artifacts\
│  ├─ media-probe.json
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
└─ tmp\
   ├─ analysis-audio.wav
   ├─ edit-mix.wav
   ├─ mastered-audio.wav
   └─ <export>.partial.mp4
```

Temp-Cleanup bleibt auf den aufgelösten konkreten Projekt-`tmp` beschränkt, folgt keinen Reparse-Points und bewahrt Fehlerdaten sieben Tage. Originalmedien, Assets, Journale und fremde Sidecars werden nicht bereinigt.

## 14. Aktualisierter Phasenplan

1. **Projekt-, Modell- und Umgebungsfundament plus Sidecar-/Protection-Vertrag.** Python 3.12, Pydantic-Modelle, UUIDv4, Zeit-/Intervallkern, Sidecar 1.1, Journalmodelle, Policyresolver, atomare JSON-IO, Fehler und Tests. Exit: erster Coding-Auftrag aus Abschnitt 17 vollständig grün; kein Medien- oder Anwendungscode.
2. **Medien-Probe und sichere Projektverzeichnisse — Teil A des zweiten Coding-Auftrags.** `ffprobe`, Stream-PTS, SourceIdentity, Win32-Close-Gate, Hashing, Pfadguards, Preflight. Exit: Probe-/Offsetfixtures, abbrechbares Hashing und Source-Immutabilität bestehen.
3. **OBS-Producer, Rohjournal und Finalisierung — Teil B desselben zweiten Coding-Auftrags.** Natives Plugin, Signalbindungen, Hotkeys, Journal, Clock-Samples, Pause, Pfad-/Split-Erkennung, Direct-MP4/Remux-Finalizer und Sidecar-Publikation. Exit: zweiter Coding-Auftrag sowie echte OBS-Tests aus Abschnitt 15.2 bestehen. Vor diesem Gate sind reale Auto-Cut-Tests verboten.
4. **EDL und Source-to-Output-Timeline-Mapping.** Policyaware Validator, Keep-Komplement, Vor-/Rückabbildung und Mapping aller Overlay-Sperren. Exit: Property-Tests; keine Time-/Overlay-/Audio-Policyverletzung.
5. **Konservative Stille-Erkennung und Test-Rendering.** Adaptive RMS-Kandidaten, Offsetnormalisierung, 800-Sample-Hypothese, Frameauswahl und synthetisches Rendering. Exit: Hypothese für kurze und mindestens 90-minütige Goldenfixtures bewiesen; A/V-Sync ≤ 1 Frame; andernfalls Architekturrevision.
6. **Lokale Transkription und Wortzeitstempel.** Modell-Preflight, CUDA/CPU, Mischsprache, Wortguards. Exit: monotone DE/EN-Wortframes und reproduzierbare Metadaten.
7. **CTA-Erkennung.** Intent, Ausschlüsse, getrennte Kategorien, Source-Events. Exit: positive und negative Goldens einschließlich Kanalmitglied/Community.
8. **Track-Matte-Overlay-Pipeline.** Registry, ZIP-Sicherheit, Alpha-Cache, Output-Scheduler und gemappte Overlay-Sperren. Exit: zehn CTA-Assets validiert, keine Schutz-/Positions-/Kollisionsverletzung.
9. **Notification-Sound-Mixing.** Unabhängige Soundmodi, Blocking-Policy, Gain, Ducking, Limiter. Exit: Glocke default stumm; Schutz-, Peak-, Loudness- und Sync-Tests bestehen.
10. **HTML-/JSON-Review.** Producer-/Clock-/Policyinformationen, statisches escaped HTML. Exit: Schema-/Snapshottests und Offline-Öffnung.
11. **Füllwörter, Selbstkorrekturen und Audioartefakte.** Strikte Kandidaten und policyaware lokale Reparatur. Exit: keine Zahlen-/Wiederholungsregression, kein Filter in blockierten Bereichen.
12. **Klickbare lokale Oberfläche.** PySide6-Workflow, Sidecarstatus, QProcess, Fortschritt, Abbruch, Rebind/Manual-Remux-Zuweisung und Ergebnisöffnung. Exit: kompletter lokaler Happy Path und definierte Fehler-/Abbruchszenarien.

## 15. Teststrategie und technische Akzeptanz

### 15.1 Vertrags- und Property-Tests

- Journal 1.0 kann nicht als Sidecar gelesen werden; Sidecar 1.0/1.1-unfinalized aktiviert Safe-Mode.
- Nur `finalized` plus passende vollständige Identität kann Auto-Cuts freischalten.
- Pause/Resume alterniert; Pausen-QPC-Zeit verschwindet aus Sourcezeit; Events während Pause werden nicht gemappt.
- Split-Konfiguration, Split-Request und Pfadwechsel erzeugen `E_PRODUCER_SPLIT_UNSUPPORTED` und kein Sidecar.
- Clockcounter, Drift, Samplegap, Residual und 250-ms-Gate werden jeweils an Grenzwert und knapp darüber getestet.
- Jede Protection-Ausdehnung enthält Unsicherheit plus zwei Frames; Rundung geht für Schutz nach außen.
- Policyunion verwendet OR für Blocks, AND für Allows, Hard-over-Soft; automatische Defaults sind nicht abschwächbar.
- Ein blockierter lokaler Audiorepair wird vollständig verworfen; globales Mastering bleibt erlaubt.
- Mapping von Overlay-Sperren über null, einen oder mehrere Keep-Fragmente ist monoton und exakt.
- Hashcancel veröffentlicht nichts; Größen-/mtime-/File-ID-Änderung invalidiert Cache und Finalisierung.
- Remux-Frame-/Dauermismatch, falsches Ziel und Rename ohne Rebind werden abgewiesen.
- JSON-Beispiele und aus Pydantic exportierte Schemas sind valide.

100 % Branch Coverage bleibt Pflicht für Zeitbasis, Journal/Sidecar, Protection, EDL und Mapping; mindestens 90 % für den übrigen Kern ohne UI. Property-Tests verwenden Hypothesis.

### 15.2 Echte OBS-Abnahme vor Auto-Cuts

Der Phase-3-Gate-Test verwendet OBS Studio 32.x Windows x64 und ein Testprofil mit eingebranntem visuellen Timecode sowie Test-Flash/-Beep für Ereignisvergleich:

1. Mindestens 30 Minuten Aufnahme in MKV mit aktiviertem OBS Auto-Remux zu MP4.
2. Mindestens fünf Szenenwechsel, ein vollständiges Intro, zwei Stinger, ein Outro und mindestens ein manueller Schutzbereich.
3. Eine Pause von mindestens 30 Sekunden; ein weiterer Szenenwechsel nach Resume.
4. Nach Stop existiert zunächst Journal `stopped_unfinalized`; erst nach geschlossener/remuxter MP4, Probe und vollständigem Hash erscheint `<stem>.obs-events.json`.
5. Alle schutzrelevanten gemappten Ereignisse liegen gegenüber Flash/Beep innerhalb ihrer ausgewiesenen, maximal 250 ms großen Unsicherheit; Protection enthält diese Unsicherheit plus zwei Frames.
6. MP4-Framezahl und normalisierte Dauer stimmen mit MKV innerhalb eines Frames überein; Sidecar bindet ausschließlich die MP4.
7. Separater Direct-MP4-Kurztest besteht denselben Close-/Hash-/Publish-Lifecycle.
8. Separater Split-Test aktiviert Splitting beziehungsweise fordert Split an: kein Sidecar, erwarteter Fehlercode.
9. Separater Crashtest beendet OBS während Recording: Journal bleibt unfinalisiert/aborted, kein Sidecar.
10. Separater Hash-Abbruchtest: keine temporäre Veröffentlichung und erfolgreicher vollständiger Retry.

Erst nach Bestehen dieses Gates darf Phase 5 einen realen OBS-Clip automatisch schneiden. Ein synthetischer Clip allein reicht nicht.

## 16. Risiken und Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| Callback entspricht nicht exakt sichtbarem Frame | Keine Exaktheitsbehauptung; Framecounter+QPC, Kalibrierung, Unsicherheitsgate 250 ms, zusätzlicher Schutz. |
| Producer blockiert OBS | Callback schreibt nur kurze geflushte Journalrecords; Probe/Hash im separaten Finalizer. |
| OBS-Crash/kaputtes Journal | Kein Stopanker, keine Finalisierung, Safe-Mode. |
| Pause verfälscht Wallclock | Outputcounter primär; QPC-Pausenintervalle explizit abziehen und validieren. |
| Split erzeugt mehrere Zeitbasen | v1 erkennt und verweigert Sidecar statt Dateien falsch zu verbinden. |
| Remux ändert PTS | Gegen finale MP4 neu proben/kalibrieren; Framezahl-/Dauergate; Startoffsetnormalisierung. |
| Datei wird während Hash verändert | Win32-Handle sperrt Write/Delete; Identität vor/nach Hash vergleichen. |
| Strenge Policy geht bei Union verloren | Elementare Intervallpartition und OR/AND/Hard-Priorität. |
| Globale Normalisierung verändert OBS-Audio | Produkt erlaubt globales Mastering; lokale Reparatur bleibt blockiert; Loudness/Peak werden reviewbar. |
| 800-Sample-Strategie verhält sich anders als angenommen | Phase-5-Golden-Gate; keine echte Auto-Cut-Freigabe und kein stiller Fallback bei Scheitern. |
| Source-Startoffsete driften A/V | Rationale PTS-Normalisierung vor Auswahl; >1-Frame-Korrektur sperrt Autoediting. |
| Alte Sidecars werden versehentlich akzeptiert | Consumer erlaubt ausschließlich finalisiertes Schema 1.1. |

## 17. Die ersten zwei Coding-Aufträge

### 17.1 Erster, weiterhin eng begrenzter Coding-Auftrag

**Implementiere ausschließlich den Python-3.12-Zeitbasis-/Journal-/Sidecar-1.1-/Protection-Kern. Kein UI, kein FFmpeg, keine Transkription, keine EDL, kein Rendering, kein OBS-Plugin.**

Laufzeitabhängigkeit:

- Pydantic 2.

Entwicklungsabhängigkeiten:

- pytest;
- pytest-cov;
- Hypothesis;
- mypy;
- Ruff.

Lieferumfang:

1. Minimales `src`-Layout nur für Kernbibliothek und Tests; Python-IDs über `uuid.uuid4()`.
2. Strikte Pydantic-Modelle für Journalrecords 1.0, finalisiertes Sidecar 1.1, SourceIdentity, ClockCalibration, PauseInterval, ProtectionPolicy und materialisierte FrameRange.
3. `expected_sidecar_path(mp4_path)` liefert ausschließlich `<stem>.obs-events.json`.
4. Lifecycle-Validator: Journal/unfinalized/Sidecar 1.0 können niemals `validated_sidecar_1_1` ergeben.
5. Reine Kalibrierungsfunktionen für Counter-/QPC-Eingabewerte ohne Medienzugriff, inklusive Gates, Pauseabzug, Unsicherheit und Rundung.
6. Protection Resolver mit Pflichtdefaults, manuellen sicheren Overrides, elementarer Union und getrennten Flags.
7. Consumer-Validator gegen eine bereitgestellte SourceIdentity; kein eigenes Datei-Hashing.
8. Atomarer Export `protection-ranges.json` und strukturierte `E_SIDECAR_*`-/`E_JOURNAL_*`-Fehler.

Pflichttests umfassen alle Vertrags-/Property-Fälle aus Abschnitt 15.1, insbesondere Schema-/Lifecycle-Trennung, Pause, Drift/Residual/250-ms-Gates, Unsicherheitspuffer, vier Policyflags, Union/Priorität, unvollständige Paare, Hashidentitätsvergleich als Wertmodell und Pfade mit Leerzeichen/Umlauten.

Definition of Done:

- `pytest` vollständig grün;
- `pytest --cov` weist 100 % Branch Coverage für den gesamten gelieferten Kern aus;
- Hypothesis-Properties sind deterministisch reproduzierbar;
- `mypy --strict` grün;
- `ruff check` grün;
- nur temporäre Testpfade werden beschrieben;
- keine OBS-, Medien-, App- oder Renderkomponente existiert.

### 17.2 Unmittelbar folgender Coding-Auftrag

**Implementiere danach den minimalen OBS-Producer-Prototyp und Finalizer ausschließlich für Journal, Kalibrierungsdaten, Close-Gate, Probe, Hash und Sidecar-Publikation. Noch keine Schnittanalyse oder UI.**

Dieser zweite Auftrag folgt unmittelbar auf den ersten und besitzt zwei interne Meilensteine: Phase 2 liefert Probe/Close/Hash, Phase 3 bindet darauf den Producer. Dazwischen liegt kein anderer Coding-Auftrag.

Lieferumfang:

- natives C++20-Plugin für OBS Studio 32.x Windows x64 auf Basis des offiziellen OBS-Plugin-Templates;
- Output-/Frontend-/Source-/Transition-Signalbindungen und die zwei Protection-Hotkeys;
- append-only Journal mit Flush, QPC, Framecounter, Pfad- und Splitstatus;
- Python-Finalizer unter Wiederverwendung des ersten Kernpakets;
- Direct-MP4, OBS-Auto-Remux und explizite Manual-Remux-Zuweisung;
- Win32-Close-Gate, `ffprobe`, abbrechbares vollständiges SHA-256, atomare Sidecar-1.1-Publikation;
- automatisierte Unit-/Integrationstests und die echte OBS-Abnahme aus Abschnitt 15.2.

Exit: Kein Auto-Cut-Code; der 30-Minuten-Test, Pause/Resume, Remux, Split-Ablehnung, Crash und Hash-Abbruch bestehen. Erst danach beginnt EDL-/Mapping-Arbeit.

## 18. Architekturkonformität

Änderungen an OBS-vs-Postproduktionsgrenze, no-sidecar-Schnittsperre oder Produktinvarianten benötigen eine neue Planning-Brief-Version. Änderungen an Producerwahl, Journal-/Sidecar-Schema, Clock-Gates, Split-/Remux-Support, Protection Policy, EDL/Mapping oder Renderhypothese benötigen mindestens eine neue Architekturversion und passende Schema-/Fixturemigration.

Dieser Plan implementiert nichts. Er definiert die Verträge so, dass der erste Coding-Agent ohne erneute Grundsatzdiskussion den Kern und anschließend den Producer bauen kann.
