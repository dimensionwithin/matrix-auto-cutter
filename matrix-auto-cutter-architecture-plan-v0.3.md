# Matrix Auto Cutter — Technischer Architekturplan v0.3

Status: nach Audit reparierter Phase-2-Plan; keine Implementierungsfreigabe  
Stand: 2026-07-13  
Produktvertrag: `matrix-auto-cutter-planning-brief-v0.5.md`  
Phase-1-Vertrag: `matrix-auto-cutter-architecture-plan-v0.2.md`  
Assetvertrag: `matrix-auto-cutter-asset-manifest-v0.5.json`  
Eingefrorene Baseline: `87fbfd19a50879abefec21af75d37405f6349da5`

## 1. Geltung, Rangfolge und normative Sprache

Dieser Plan repariert ausschließlich die Planung der zweiten Ausbaustufe. Er implementiert nichts und gibt kein Paket frei. **MUSS**, **DARF NICHT**, **SOLL** und **DARF** sind normativ.

Bei Widerspruch gilt ohne Ausnahme:

1. Planning Brief v0.5;
2. Architekturplan v0.2 für Phase 1;
3. Asset Manifest v0.5;
4. Baseline-Commit `87fbfd19a50879abefec21af75d37405f6349da5`;
5. dieser Plan.

Phase 1 bleibt unverändert. Insbesondere bleiben Journal 1.0, Sidecar 1.1, `SourceIdentity`, kanonische JSON-/Decimal-/UUIDv4-Regeln, Clock-, Pause-, Pairing-, Protection- und Safe-Mode-Verträge unverändert. Neue Phase-2-Artefakte besitzen eigene Versionen und sind weder neue Journalrecords noch neue Sidecarfelder. Ohne gültiges finalisiertes quellidentisches Sidecar 1.1 bleiben zeitentfernende Auto-Cuts verboten. OBS-produzierte Intro-, Outro-, Szenenwechsel- und Stingerbereiche werden weder eingefügt noch ersetzt, verschoben oder neu geordnet.

## 2. Reparaturentscheidungen F-01 bis F-12

| Finding | Verbindliche Entscheidung | Primär betroffene Kapitel/Pakete |
|---|---|---|
| F-01 | Journal 1.0 bleibt allein finalisierbar; ein optionales, separat versioniertes Bundle ergänzt es ohne Migration. | 5, 17; 2F/2G |
| F-02 | Jede persistente Datei steht in der normativen Artefaktmatrix und besitzt Version, Validator, Writer-, Trust-, Atomik-, Recovery- und Bindungsvertrag. | 4; 2A–2G |
| F-03 | 2B ist lease-freier Probe Core; 2C erzeugt `CloseGateLease`; 2E integriert beide. | 7–9, 18; 2B/2C/2E |
| F-04 | Unveränderliche Finalartefakte werden atomar create-if-absent publiziert; niemals Replace. | 14; 2F |
| F-05 | Strategie B ist gewählt: Sidecar 1.1 ist alleinige fachliche Source of Truth; Receipts/State sind rekonstruierbare Evidence. | 14–15; 2F |
| F-06 | Mehrdeutige Hauptstreams sind ein strukturierter Fehler; nur eindeutige Auswahl oder validierte persistierte Benutzerzuweisung ist zulässig. | 7; 2B/2E |
| F-07 | Nicht ersetzbares Ownershipobjekt und separate Diagnosemetadaten sind strikt getrennt. | 6; 2A/2F |
| F-08 | `ValidatedFfprobeBinary` bindet Pfad, File-ID, Version und SHA-256; geprüfte Versionsmatrix statt unbegründetem Major-7-Gate. | 7; 2B |
| F-09 | `SourceState` ist exhaustiv definiert; alle Übergänge verwenden dieselben Namen. | 11; 2E |
| F-10 | Byteidentischer Retry gilt nur innerhalb einer persistierten `FinalizationIntent`; semantischer Determinismus gilt allgemein. | 13; 2F |
| F-11 | Operationsergebnisse sind exhaustiv auf stabile Fehler, State, Retry, Aktion, Artefakte, Log-Level und Safe-Mode abgebildet. | 16; 2A–2F |
| F-12 | Native Coverageziele, Toolchain, Teilmessungen und eng begrenzte Adapterausnahmen werden spätestens im 2G-Audit freigegeben. | 19; 2G/2H |

## 3. Systemgrenzen und Datenfluss

```text
untrusted source path
  -> Paket 2A: Path/Workspace/Snapshot-Grundtypen und Path-Ownership
  -> Paket 2C: restriktiver Handle + CloseGateLease + File-ID-Ownership
  -> Paket 2E: Probe Core(2B) unter Lease + Snapshot-Rechecks
  -> Paket 2D: Vollhash über denselben Lease-Handle
  -> Paket 2E: SourceIdentity + Evidence; ConfirmedSource nur Laufzeit

Journal 1.0 oder Journal Bundle 1.0
  -> Paket 2F: FinalizationIntent -> Sidecar 1.1 create-if-absent
  -> Sidecar 1.1 ist Commit und fachliche Source of Truth
  -> Receipt/State werden nach Commit erzeugt oder beim Recovery rekonstruiert

Paket 2G: Producer-Conformance, Golden Files, OBS-ABI, native Coveragefreigabe
  -> Paket 2H: natives Plugin, erst nach separater Freigabe
```

Kein Schritt erzeugt Transkription, EDL, Auto-Cuts, Rendering oder UI. Der Abhängigkeitsgraph ist `2A -> {2B,2C}; 2C -> 2D; {2B,2C,2D} -> 2E; 2E -> 2F -> 2G -> 2H` und ist azyklisch. 2B kennt keine Lease und behauptet keine Dateifinalität.

## 4. Normative Artefaktverträge

### 4.1 Gemeinsame Regeln

Jede persistente JSON-Datei MUSS ein Top-Level-`artifact_type` und eine eigene `schema_version` besitzen, strikt mit unbekannten Feldern abgewiesen und vor jeder Verwendung größenbegrenzt gelesen werden. Unbekannte Versionen werden ohne Migration abgewiesen. Kanonische JSON-Artefakte verwenden UTF-8 ohne BOM, sortierte Schlüssel nach Phase-1-`CanonicalModel`, exakte Zahlenlexeme und abschließendes LF. NDJSON folgt dem unveränderten Journal-1.0-Vertrag. Textdiagnostik ist ausdrücklich nicht kanonisch und niemals Trustquelle.

`I` bedeutet immutable und create-if-absent; `R` bedeutet fachlich ersetzbar mit explizitem Same-Directory-Temp/Flush/`ReplaceFileW`-Vertrag. Für `I` ist `MOVEFILE_REPLACE_EXISTING` verboten. Die für 2A vollständigen I-/R-Grundverträge stehen in 6.7. Maximalgrößen gelten vor vollständiger Materialisierung. Projekt-ID-Bindung steht in allen Projektartefakten, Run-ID-Bindung in laufbezogenen Artefakten; `—` bedeutet ausdrücklich keine Bindung. Cleanup folgt keinem Reparse Point und betrifft nur die jeweils genannte eigene Temp-/uncommitted-Datei. Quarantäne kopiert Evidence create-if-absent und verändert das Original nie.

### 4.2 Zentrale Artefaktmatrix

| Artefakt / Pfad | `artifact_type`; Version; Persistenz/Kanonik | Wahrheit/Trust beim Lesen | Erzeuger; alleiniger Writer; Leser/Validator | Max.; Publish/Overwrite | Bindungen (Projekt; Run; Source/FileSnapshot) | Reuse/Recovery/Migration/Cleanup |
|---|---|---|---|---|---|---|
| `project.json` | `matrix_project`; 1.0; persistent/kanonisch | nach vollständiger Validierung fachliche Source of Truth für Projekt-ID, Projektschema und Workspace-Bindung; niemals Source-of-Truth für Medien | Workspace; Workspace-Writer; alle Projektleser | 1 MiB; erstmalig create-if-absent, danach R via `ReplaceFileW`; Revision-CAS nach 6.7 | ja; letzter Run optional; Source nur als Artefakt-ID | nur gleiche Projekt-ID/Root; unbekannt abweisen; eigene Temp cleanup; nie aus unbekannten Dateien ableiten |
| `inputs/source-reference.json` | `source_reference`; 1.0; persistent/kanonisch | abgeleitete vorläufige Referenz, nie `SourceIdentity`; untrusted | Source-Registrierung; Workspace-Writer; Path Validator | 1 MiB; I create-if-absent | ja; Erzeugungsrun; erwarteter Pfad/Snapshot-ID | nur exakt validiert; bei Konflikt neues Projekt; keine Migration; quarantänisierbar |
| `probe/<probe-id>/media-probe.json` | `media_probe`; 1.0; persistent/kanonisch | abgeleitete Probe-Evidence; untrusted bis Binary-, Parser-, Snapshot- und Assignmentprüfung | Probe-Integration 2E; Probe-Writer; Builder | 4 MiB; I create-if-absent | ja; Probe-/Run-ID; Binary-ID, FileSnapshot vor/nach | nur bei identischen Bindungen; sonst neu proben; keine Migration; Generation quarantänisierbar |
| `probe/<probe-id>/stream-assignment.json` | `stream_assignment`; 1.0; persistent/kanonisch, nur bei expliziter Auswahl | Benutzerentscheidung, untrusted bis Source-/Probe-/Merkmalsbindung geprüft | explizite Assignment-Operation; Assignment-Writer; 2E-Integrator | 1 MiB; I create-if-absent | ja; Assignment-Run; Snapshot/SourceIdentity, Probe-Digest/-Version, Streammerkmale | nur bei vollständig gleichen Bindungen; veraltete Zuordnung abweisen; keine Migration; quarantänisierbar |
| `identity/<id>/source-identity-evidence.json` | `source_identity_evidence`; 1.0; persistent/kanonisch | abgeleitete Evidence, nicht alleinige Source of Truth; untrusted bis Kreuzprüfung | Builder; Identity-Writer; Finalizer/Recovery | 4 MiB; I | ja; Run; SourceIdentity-Digest, S0–S5, Probe/Hash | nur vollständig kreuzvalidiert; rekonstruierbar durch neuen Lauf; keine Migration; quarantänisierbar |
| `ConfirmedSource` | kein `artifact_type`; keine Version; **nur Laufzeit, nicht persistent** | zusammengesetzter Capability-Typ, nie aus Datei vertraut | Builder; kein Diskwriter; Finalizer | Laufzeitgrenze; kein Publish | Laufzeit-Projekt/Run; SourceIdentity+Evidence | pro Lauf aus validierten Artefakten rekonstruiert; entfällt bei Recovery/Upgrade |
| `FileSnapshot` / `FileSnapshotSequence` | `file_snapshot_sequence`; 1.0; persistent nur als Teil von Evidence, sonst Laufzeit; kanonisch | Mess-Evidence, nicht Finalitätsbeweis allein; untrusted bis Handle-/File-ID-Prüfung | Snapshotter; jeweiliger Evidence-Writer; Gate/Builder | 2 MiB; I als Evidence | ja; Run; Volume/File-ID und Snapshotkey | nur innerhalb gebundener Validierungsepoche; neues Gate bei Recovery; keine Migration |
| `identity/<id>/hash-receipt.json` | `source_hash_receipt`; 1.0; persistent/kanonisch | abgeleitete Vollhash-Evidence; untrusted bis Größe/EOF/Snapshots geprüft | Hasher 2D; Hash-Writer; Builder/Recovery | 1 MiB; I | ja; Hash-Run; Lease/S0/S4, SHA-256 | nur bei vollständig identischer Evidence; fehlend => neu hashen; keine Migration; quarantänisierbar |
| `journal-session.json` | `recording_journal_session`; 1.0; persistent/kanonisch, Bundle optional | zusätzliche Producerprovenienz; kein Besitz-/Authentizitätsbeweis; untrusted | Plugin 2H; Session-Writer; Bundle Validator | 1 MiB; I | Projekt optional erst Import; Plugin-Run; Recording-ID/Journalheader | im Legacyprofil nicht erforderlich; im deklarierten Bundle Pflicht und beschädigt => Bundle ablehnen; keine Migration |
| `journal-integrity.json` | `recording_journal_integrity`; 1.0; persistent/kanonisch, Bundle optional | Digest-Evidence, keine Signatur; untrusted bis Hash/IDs stimmen | Plugin 2H; Integrity-Writer; Bundle Validator | 1 MiB; I nach Stop | Projekt optional; Plugin-Run; Journal-SHA/Recording-ID | im Legacyprofil nicht erforderlich; im Bundle Pflicht; fehlend/beschädigt => Bundle ablehnen, Journal separat neu als Legacy bewertbar nur bei expliziter Profilwahl |
| `journal-bundle.json` | `recording_journal_bundle`; **bundle_schema_version 1.0**; persistent/kanonisch | Bundlemanifest, nicht Journal; untrusted | Plugin/Importer; Bundle-Writer; Bundle Validator | 1 MiB; I | Projekt beim Snapshot; Plugin-Run; Digests aller Teile/Recording-ID | nur komplett und digestgleich; unbekannte Bundleversion abweisen; keine implizite Migration; quarantänisierbar |
| Rohjournal `*.recording-journal.ndjson` | `recording_event_journal`; **journal_schema_version 1.0**; persistent/kanonisches NDJSON | Source of Truth für Aufnahmeevents; untrusted bis unveränderter Phase-1-Validator | Plugin; append-only Journalwriter; Legacy-/Bundle-Loader | 256 MiB, 64 KiB/Zeile, 1 Mio. Records; append+Flush je Record | Projekt erst Snapshot; Plugin-Run nur extern optional; Recording-ID im Header | Legacy eigenständig finalisierbar; nie reparieren/migrieren; Original nie cleanup; beschädigt quarantänisieren |
| `runs/<run>/finalization-intent.json` | `finalization_intent`; 1.0; persistent/kanonisch | Source of Truth für Retry-Provenienz, nicht für Finalisierung | Finalizer; Intent-Writer; Builder/Recovery | 2 MiB; I | ja; finalizer_run_id; Journaldigest, SourceIdentity, Algorithmen, Zielgeneration | gleiche Intention wiederverwenden; Konflikt => RecoveryConflict; keine Migration; nie automatisch löschen solange Sidecar/Run referenziert |
| `<stem>.obs-events.json` | unverändert `obs_event_sidecar`; **schema 1.1**; persistent/kanonisch | **alleinige fachliche Source of Truth und Commitpunkt** | Finalizer; SidecarPublisher allein; Phase-1-Consumer/Recovery | 256 MiB; I create-if-absent | Projekt nicht im Schema; Run im bestehenden Lifecycle; vollständige SourceIdentity/Recording-ID | Wiederverwendung allein nach vollständiger semantischer Validierung; fehlende Receipts rekonstruierbar; Sidecar nie migrieren/cleanup/quarantäneverschieben |
| `.<stem>.obs-events.json.tmp.<run-id>` | Sidecar-1.1-Bytes, aber Tempname; persistent bis Cleanup/nicht kanonisch als Artefaktstatus | niemals gültig oder sichtbar für Consumer | SidecarPublisher; derselbe; Recovery nur Diagnose | 256 MiB; `CREATE_NEW`, Flush; kein Replace | Projekt indirekt; Run im Namen; Ziel/Source in Bytes | nie reuse/publizieren durch Recovery; nach Ownership-/Pfadprüfung löschen oder Quarantäne; unbekannte Datei unangetastet |
| `sidecar/receipts/<session>.json` | `finalization_receipt`; 1.0; persistent/kanonisch | rekonstruierbare Evidence, **nicht Commit** | Finalizer/Recovery; Receipt-Writer; Diagnose/Recovery | 2 MiB; I | ja; Intent-Run; Sidecar-SHA, Recording-ID, SourceIdentity | validiert reuse; fehlend nach Sidecarvalidierung deterministisch rekonstruieren; Konflikt quarantänisieren; keine Migration |
| `runs/<run>/state/finalizer-state.json` | `finalizer_state`; 1.0; persistent/kanonisch | Diagnose/Resume-Hinweis, nicht Commit | State Machine; State-Writer; Recovery/UI | 2 MiB; R mit Revision-CAS | ja; ja; Artefakt-IDs | nur nach Kreuzprüfung; aus Intent/Sidecar rekonstruierbar; unbekannt abweisen; alte eigene Revision cleanup |
| Lockdiagnostik `locks/diagnostics/<lock-key>/<run>.json` | `lock_diagnostic`; 1.0; persistent/kanonisch | reine Diagnose, niemals Besitznachweis; kann stale sein | Lockmanager; Diagnose-Writer; Operator | 1 MiB; I pro Run oder R für expliziten Status | Projekt je Lock; ja; Path-/File-ID-Key | nie zur Lockübernahme; stale markieren/ignorieren; unbekannt abweisen; nach sicher fehlendem Ownershiphandle altersbasiert cleanup |
| `quarantine/<case>/report.json` | `quarantine_report`; 1.0; persistent/kanonisch | Diagnoseindex, Belege bleiben untrusted | Quarantine Service; alleiniger Quarantine-Writer; Operator | 4 MiB plus einzeln begrenzte Evidence; I | ja; Run; Digests/IDs statt Vertrauen | nie als Eingabe reuse; kein Auto-Recovery; keine Migration; niemals automatisch löschen |

Interne Typen `ValidatedPath`, `CloseGateLease`, `ValidatedFfprobeBinary`, `ProbeCoreResult`, `ConfirmedSource`, Cancellation- und Progressobjekte sind ausdrücklich reine Laufzeittypen. Soweit ein Snapshot, eine Probe oder ein Resultat persistiert wird, geschieht dies ausschließlich in dem oben versionierten Artefakt, nicht durch Serialisierung des Laufzeitobjekts ohne Schema.

## 5. Journal-1.0- und Bundle-Kompatibilität

### 5.1 Zwei explizite Eingangsprofile

`legacy_journal_1_0` akzeptiert ein nach Phase 1 gültiges Journal 1.0 mit genau einem erfolgreichen `stopped_unfinalized`-Stop. Weder Session-, Integrity- noch Bundle-Receipt wird vorausgesetzt. Der unveränderte Phase-1-Validator entscheidet Syntax, Sequenz, Lifecycle und Records. Fehlende Phase-2-Provenienz wird als `not_available` ausgewiesen, niemals erfunden und niemals als höheres Vertrauen dargestellt. Das Journal bleibt finalisierbar, wenn Quelle, Clock, Close-Gate, Probe, Hash, Pairing und Sidecarvertrag anderweitig vollständig bestehen.

Ein Journal-only-Eingang erzeugt **nicht pauschal** Safe-Mode. Zulässig bleiben Sourcezuordnung, Close-Gate, Probe, Hash, Kalibrierung, Protection, Sidecar-1.1-Erzeugung und Finalisierung. Konservative Einschränkung: Aussagen über Plugin-Prozess, Producerkonfigurationsdigest, QPC-Frequenz-Receipt oder Bundleintegrität bleiben `not_available`; Funktionen, die genau diese zusätzliche Provenienz benötigen, bleiben deaktiviert. Safe-Mode entsteht nur aus einem konkreten bestehenden Fehler wie unvollständigem Journal, Source-/Clock-Mismatch oder ungültigem Sidecar, nicht aus dem bloßen Fehlen von Phase-2-Receipts.

`phase2_journal_bundle_1_0` ist ein separater Vertrag mit Manifestname `journal-bundle.json`, `artifact_type="recording_journal_bundle"` und `bundle_schema_version="1.0"`. Bundle- und Journalversion sind unabhängig. Bestandteile:

- Pflicht: unverändertes Journal 1.0, Session Receipt 1.0, Integrity Receipt 1.0 und Bundlemanifest 1.0;
- optional: rein diagnostische Producerlogs, die niemals Validierung oder Finalisierung freischalten;
- Bindung: Manifest nennt SHA-256 und Größe jedes Pflichtteils, `recording_session_id`, `plugin_run_id`, Producer-/OBS-Version; Session- und Integrity-Receipt müssen diese IDs sowie Journalheader/-digest kreuzkonsistent binden.

Unbekannte Bundle- oder Receiptversionen, fehlende Pflichtteile, Digestabweichung, beschädigte Receipts oder ID-Widerspruch weisen **das Bundleprofil** strukturiert ab. Kein Receipt wird als Journal-1.0-Record ausgegeben; es gibt keine implizite Migration. Der Nutzer/Caller darf dasselbe unveränderte Journal anschließend nur durch eine neue explizite Anfrage als `legacy_journal_1_0` prüfen lassen; der Finalizer fällt nicht still vom beschädigten Bundle auf Legacy zurück. Der Mindestvertrag ist mit diesem Kapitel vor 2F entschieden. 2G ergänzt nur Producer-Conformance und Goldens.

## 6. Workspace, Projekt-ID, Pfade und Locks

### 6.1 Workspace und Projekt-ID

Standardroot ist `D:\workspace\.matrix-auto-cutter`. Root und alle vorhandenen Vorfahren müssen lokale NTFS-Verzeichnisse, regulär erreichbar, ohne Reparse Points, UNC, ADS, Devicepfade, Cloud-Platzhalter oder Wechselmedium sein. Komponenten sind feste ASCII-Namen oder kanonische UUIDv4; Benutzertext ist nur JSON-Metadatum.

Projektanlage verwendet `CreateDirectoryW` create-if-absent für `projects/<uuid4>`. `ERROR_ALREADY_EXISTS` übernimmt das Verzeichnis niemals. Es wird eine neue UUID erzeugt, höchstens 16 Versuche. Nach 16 Kollisionen folgt `E_PROJECT_ID_COLLISION`; andere Existenz-/ACL-/I/O-Zustände werden nicht als Kollision umklassifiziert. Ein bestehendes Projekt wird ausschließlich durch explizites Open und vollständig validiertes `project.json` mit passender Projekt-ID, Rootbindung und Schema 1.0 geöffnet.

Der von 2A erzeugte geschlossene Workspaceumfang besteht ausschließlich aus dem validierten Workspace-Root, `projects/`, `projects/<project-id>/` und `projects/<project-id>/project.json`; hinzu kommen nur kurzlebige, eindeutig 2A-eigene Same-Directory-Temps nach 6.7. `<project-id>` ist die kleingeschriebene kanonische Textform einer neu kryptographisch zufällig erzeugten UUIDv4. Verzeichnisse wie `inputs/`, `probe/`, `identity/`, `runs/`, `sidecar/` oder `quarantine/` gehören nicht zum von 2A erzeugten Projektlayout; ihre Nennung in der Artefaktmatrix reserviert lediglich spätere Verträge und autorisiert 2A nicht, sie anzulegen.

`project.json` MUSS mindestens `artifact_type="matrix_project"`, `schema_version="1.0"`, die kanonische `project_id`, eine kanonische Workspace-Rootbindung und eine monotone nichtnegative `revision` enthalten. Die Rootbindung umfasst die validierte kanonische DOS-Rootreferenz sowie, sofern die Plattform sie liefert, Volume-Identität und Root-File-ID mit ausdrücklicher Verfügbarkeitsmarkierung. Erst nach Größen-, JSON-, Schema-, UUID-, Revisions-, Rootcontainment- und handlebasierter Rootbindungsprüfung ist dieses Artefakt die fachliche Source of Truth für Projekt-ID, Projektschema und Workspace-Zugehörigkeit. Verzeichnisname, Pfadkonvention, Lockdiagnostik, Tempdatei oder andere Datei dürfen diese Wahrheit nicht ersetzen.

Eine Anlage erzeugt immer eine neue UUIDv4 und reserviert `projects/<project-id>/` atomar beziehungsweise exklusiv create-if-absent. Ein vorhandenes Verzeichnis ist unabhängig von seinem Inhalt `ProjectAlreadyExists` für diesen Versuch, wird nie geöffnet, übernommen, ergänzt, geleert oder gelöscht und führt zu einem neuen UUID-Versuch. Ein nach Verzeichnisanlage, aber vor gültigem `project.json` abgebrochener Lauf hinterlässt eine Crashwaise; sie wird bei Open als `OrphanProjectDirectory` fail closed gemeldet und nie wiederverwendet. Beschädigte oder verwaiste Projekte bleiben unverändert am Ort. 2A verschiebt sie nicht automatisch; eine Verschiebung bedarf einer separat freigegebenen, versionsgebundenen Quarantäneoperation mit validierter Quelle, exklusivem Projektlock, create-if-absent-Ziel und Report. Bis zu einem solchen Vertrag ist ausschließlich Melden zulässig.

Ein bestehendes Projekt darf nur durch eine explizite Open-Operation geöffnet werden. Diese prüft zuerst die angeforderte ID, dann den sicheren Projektpfad und anschließend `project.json`; sie folgt keinem Reparse Point. Unbekannte Zusatzdateien oder -verzeichnisse werden weder in das Layout aufgenommen noch überschrieben, verschoben oder gelöscht. Auch ein ansonsten valides Projekt wird bei ID-, Schema- oder Root-Mismatch nicht geöffnet.

`ProjectMetadataMissing` ist der direkte Ausgang der expliziten Open-Operation für ein fehlendes `project.json`; `OrphanProjectDirectory` ist die Discovery-/Klassifikation desselben fail-closed Zustands eines gefundenen UUID-Verzeichnisses ohne publizierte Metadaten. Beide sind stabil unterscheidbar und autorisieren weder Wiederverwendung noch Mutation.

Die öffentlichen 2A-Ausgänge für Anlage und Open sind diskriminiert; jeder Fehler erhält den unveränderten internen OS-Code und seine Ursache, soweit vorhanden:

| Resultat | stabiler Code | Bedeutung |
|---|---|---|
| `ProjectCreated` / `ProjectOpened` | kein Fehler | `project.json` ist vollständig publiziert beziehungsweise vollständig validiert |
| `ProjectAlreadyExists` | `E_PROJECT_ALREADY_EXISTS` | der create-if-absent-Kandidat existiert; keine Übernahme |
| `ProjectIdCollision` | `E_PROJECT_ID_COLLISION` | 16 echte UUID-Verzeichniskollisionen; keine Umklassifizierung anderer Fehler |
| `InvalidProjectId` | `E_PROJECT_ID_INVALID` | keine kanonische UUIDv4 |
| `ProjectMetadataMissing` | `E_PROJECT_METADATA_MISSING` | Projektverzeichnis vorhanden, `project.json` fehlt |
| `ProjectMetadataInvalid` | `E_PROJECT_METADATA_INVALID` | beschädigtes JSON oder Schema-/Kanonikverstoß |
| `ProjectBindingMismatch` | `E_PROJECT_BINDING_MISMATCH` | ID-, Verzeichnis- oder Workspace-Rootbindung widerspricht der validierten Realität |
| `OrphanProjectDirectory` | `E_PROJECT_ORPHAN` | UUID-Projektverzeichnis ohne erfolgreich publizierte gültige Metadaten |
| `UnsupportedProjectVersion` | `E_PROJECT_VERSION_UNSUPPORTED` | syntaktisch lesbare, aber nicht unterstützte Projekt-/Schemaversion |
| `ProjectOpenFailed` | `E_PROJECT_OPEN_FAILED` | erhaltener sonstiger ACL-/I/O-/OS-Fehler; niemals still als Kollision oder Busy behandelt |

### 6.2 `%LOCALAPPDATA%`-Lockwurzel

Die Lockwurzel wird mit `SHGetKnownFolderPath(FOLDERID_LocalAppData)` für den aktuellen Benutzer ermittelt, absolut normalisiert und handlebasiert validiert. Für sie gilt dieselbe local-NTFS-only-, keine-UNC-, keine-Reparse- und keine-lokale-Umleitungs-Policy. Jeder existierende Vorfahr und Endordner wird mit `FILE_FLAG_OPEN_REPARSE_POINT`, Dateityp, Volume und ACL-Zugriff für den aktuellen Benutzer geprüft. Zugriffsverweigerung, unerwartete ACL-Vererbung, Reparse, Umleitung oder nicht unterstütztes Dateisystem endet fail closed; der Prozess ändert keine ACL und fordert keine Elevation an.

### 6.3 Ownershipobjekte und Diagnose

Ownershippfade:

```text
%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\locks\ownership\projects\<project-id>.lck
...\paths\<sha256-canonical-path>.lck
...\sources\<volume>-<file-id-128>.lck
...\targets\<sha256-canonical-target>.lck
```

Jedes Ownershipobjekt wird mit `CreateFileW(GENERIC_READ|GENERIC_WRITE, dwShareMode=0, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL)` geöffnet. Ausschließlich der **offene Handle** beweist Besitz. Dateiinhalt, PID, Existenz oder Diagnose beweisen nichts. Der Ownershippfad wird während des Besitzes weder ersetzt, umbenannt noch gelöscht; Diagnose wird nicht in-place benötigt. Nach Handle-Schließung oder Prozesscrash besteht kein Besitz mehr, auch wenn die Datei bleibt.

Diagnosepfad ist separat `locks/diagnostics/<lock-key>/<run-id>.json`, create-if-absent. Er enthält Run-/Projekt-ID, Prozess-ID/-Startzeit, Lockart, redigierten Schlüssel, Erwerbszeit und Status. Er darf stale sein, übernimmt oder löst keinen Lock und darf nach nachgewiesen freiem Ownershiphandle altersbasiert bereinigt werden. Access denied oder unbekannter Openfehler ist kein `busy`; nur `ERROR_SHARING_VIOLATION`/`ERROR_LOCK_VIOLATION` am Ownershipobjekt ergibt concurrent/busy. Nicht unterstütztes Filesystem ist `E_CLOSE_GATE_UNSUPPORTED` beziehungsweise `E_LOCK_UNSUPPORTED`.

Lockreihenfolge: Projekt -> kanonischer Pfad -> nach sicherer Handle-Öffnung und verfügbarer File-ID Source-Instanz -> Ziel. Ein File-ID-Lock wird niemals vor verfügbarer File-ID verlangt. Zwei Projekte derselben Source serialisieren am File-ID-Lock. Zwei Hardlinks derselben Datei sind wegen `(Volume ID, FILE_ID_128)` dieselbe Dateiinstanz; Pfadlocks allein genügen nicht. Erwerb ist fail-fast oder explizit zeitbegrenzt/cancellable, nie unbounded; bei Teilfehlern werden Handles in umgekehrter Reihenfolge geschlossen.

### 6.4 `ValidatedPath` und Windows-Pfadsicherheit

`ValidatedPath` ist ein unveränderlicher Laufzeitwert mit diskriminierter Rolle `workspace_internal` oder `external_source_read_only`; eine Rolle darf nie implizit in die andere konvertiert werden. Er enthält ursprüngliche Eingabe, validierte kanonische DOS-Darstellung, kontrolliert abgeleitete interne Win32-Long-Path-Darstellung, Rootbindung bei internen Pfaden und die durchgeführten Policyprüfungen. Er beweist keine fortdauernde Existenz und ersetzt keine sichere Handleprüfung.

Für `workspace_internal` sind ausschließlich (a) rootrelative Komponentenfolgen ohne Laufwerk/Rootpräfix oder (b) vollständig qualifizierte lokale DOS-Pfade `X:\...` zulässig, die lexikalisch im bereits validierten Workspace-Root liegen. Für `external_source_read_only` ist ausschließlich ein vollständig qualifizierter lokaler DOS-Pfad zulässig; relative Eingaben sind verboten, und der resultierende Wert autorisiert nur lesende Opens. Caller-Eingaben mit UNC-Präfix, `\\?\`, `\\.\`, `\??\`, Volume-GUID-, GLOBALROOT- oder anderem Device-/Namespace-Präfix sind verboten. Nur der Adapter darf nach erfolgreicher DOS-Validierung intern eine `\\?\X:\...`-Darstellung erzeugen; diese verlässt den Vertrauensbereich nicht und wird nie erneut als Caller-Eingabe interpretiert.

Jede Komponente wird vor OS-Normalisierung einzeln geprüft. Verboten sind leere Komponenten außerhalb des einzigen Rootseparators, `.` und `..`, Rootescape, Slash-/Backslash-Einschleusung in Komponenten, NUL/Steuerzeichen, Wildcards, Doppelpunkte und damit ADS, abschließende Punkte oder Leerzeichen sowie Windows-Gerätenamen case-insensitiv und nach Entfernung einer optionalen Erweiterung (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, einschließlich Erweiterungs- und Win32-Superscriptvarianten wie `con.txt`, `COM¹`–`COM³` und `LPT¹`–`LPT³`). Pfad- und Layoutvergleiche erfolgen ordinal case-insensitiv; zwei Namen, die so kollidieren, sind `CaseCollision` und werden nicht als zwei Artefakte akzeptiert.

Unicode muss vom Eingabestring über die verwendeten Wide-Character-Win32-Aufrufe und zurück verlustfrei dieselbe Codepointfolge ergeben. 2A nimmt keine NFC-/NFD- oder andere fachliche Unicode-Kanonisierung vor; ein abweichender Roundtrip wird abgewiesen. Die kanonische DOS-Darstellung normalisiert nur Laufwerksbuchstaben und Separatoren nach diesem Vertrag, niemals verbotene Komponenten weg.

Containment wird für interne Pfade zuerst lexikalisch gegen die validierte Rootbindung und nach jedem sicheren Open erneut handlebasiert mit `GetFinalPathNameByHandleW`, Volume-Identität und, soweit verfügbar, File-ID geprüft. Externe Quellreferenzen werden entsprechend vor dem Open an ihre validierte absolute lokale DOS-Referenz und danach an die daraus sicher aufgelöste lokale Handle-Referenz gebunden, ohne dadurch Workspacezugehörigkeit zu behaupten. Jeder vorhandene Vorfahr und das Ziel werden ohne Folgen von Reparse Points geöffnet und untersucht. Reparse Point, Junction, Symlink, Mount-/Namespace-Umleitung, Root-/Volumewechsel oder nicht hinreichend belegbare finale Bindung endet fail closed. Wird eine reguläre Datei verlangt, sind Verzeichnis, Device, Pipe, Socket, Reparseobjekt und jeder andere nicht reguläre Typ abzulehnen. Der Vertrag darf hierfür einen testbaren Win32-Adapter verwenden; er schreibt keine einzelne Funktionsimplementierung vor.

Pfadoperationen liefern `PathValidated` oder einen stabilen Ablehnungsgrund. Mindestens öffentlich sind `E_PATH_INPUT_FORM`, `E_PATH_COMPONENT_EMPTY`, `E_PATH_DOT_COMPONENT`, `E_PATH_ROOT_ESCAPE`, `E_PATH_ADS`, `E_PATH_UNC`, `E_PATH_DEVICE_NAMESPACE`, `E_PATH_RESERVED_NAME`, `E_PATH_TRAILING_DOT_SPACE`, `E_PATH_CASE_COLLISION`, `E_PATH_UNICODE_ROUNDTRIP`, `E_PATH_REPARSE`, `E_PATH_ROOT_MISMATCH`, `E_PATH_NOT_REGULAR`, `E_PATH_EVIDENCE_INSUFFICIENT`, `E_PATH_ACCESS_DENIED` und `E_PATH_OS_ERROR`. Der letzte Ausgang erhält unbekannte OS-Ursachen unverändert; Access denied, Reparse und unbekannte I/O-Fehler sind niemals `busy`.

### 6.5 Öffentlicher `FileSnapshot`-Vertrag

`FileSnapshot` ist ein unveränderlicher Evidencewert einer einzelnen erfolgreichen Messung. Seine vollständige öffentliche Feldmenge ist:

| Feld | Pflicht / Verfügbarkeit | Bedeutung |
|---|---|---|
| `path_ref` | Pflicht | rollenbehafteter `ValidatedPath` beziehungsweise dessen stabile kanonische Referenz |
| `file_type` | Pflicht, Wert in 2A `regular_file` | beobachteter Typ; andere Typen erzeugen kein erfolgreiches Snapshotobjekt |
| `size_bytes` | Pflicht | nichtnegative Dateigröße vom sicheren Handle |
| `last_write_time` | Pflicht | präzisionsbewahrter OS-Wert mit benannter Einheit/Epoche |
| `creation_time` | optional, `not_available` zulässig | präzisionsbewahrter OS-Erstellzeitwert |
| `change_time` | optional, `not_available` zulässig | Dateisystem-Änderungszeit, falls zuverlässig verfügbar |
| `attributes` | Pflicht | sicherheitsrelevante Dateiattribute einschließlich Reparse-/Offlineindikatoren |
| `volume_id` | optional, `not_available` zulässig | stabile Volume-Identität, soweit der Adapter sie liefert |
| `file_id` | optional, `not_available` zulässig | File-ID (`FILE_ID_128` bevorzugt) samt ID-Schema |
| `snapshot_key` | Pflicht | SHA-256 über domänenseparierte kanonische Kodierung von Evidenceversion, Dateityp, Größe, Zeiten, Attributen, Volume-/File-ID und deren Verfügbarkeitsmarkern; `path_ref` ist bewusst nicht Bestandteil |
| `evidence_version` | Pflicht | Version von Feld-/Kodierungs-/Vergleichsvertrag, hier `file_snapshot/1.0` |

Zeitwerte werden nicht auf gröbere Anwendungseinheiten gerundet. `snapshot_key` ist Vergleichsevidence, kein Inhaltsdigest. Ein Snapshot mit nicht verfügbarer Volume-/File-ID darf als Pfad-/Metadatenevidence existieren; eine Operation, die Dateiinstanzgleichheit verlangt, muss daraus `SnapshotEvidenceInsufficient` statt Gleichheit ableiten.

Snapshotbildung ist ein diskriminierter Resultattyp: `SnapshotOk(snapshot)`, `SnapshotFileMissing` (`E_SNAPSHOT_NOT_FOUND`), `SnapshotNotRegular(observed_type)` (`E_FILE_NOT_REGULAR`), `SnapshotAccessDenied` (`E_SNAPSHOT_ACCESS_DENIED`), `SnapshotUnsafePath` (`E_PATH_UNSAFE` plus konkretem Pfadgrund), `SnapshotEvidenceInsufficient` (`E_SNAPSHOT_EVIDENCE_INSUFFICIENT`) und `SnapshotOsError` (`E_SNAPSHOT_OS_ERROR`, mit unverändertem OS-Code/Ursache). Fehlende Datei wird nur nach sicherer Pfadprüfung als solche klassifiziert; unbekannte Open-/Queryfehler werden nicht als fehlend, busy oder unzureichend umgedeutet.

Der öffentliche Vergleich zweier Snapshots liefert genau eine diskriminierte Klasse: `SameInstanceUnchanged` bei gleicher verfügbarer `(volume_id,file_id)` und identischer Snapshot-Evidence; `SameInstanceChanged` bei gleicher Instanz, aber abweichender Größe, Zeit, Attributen oder anderem Snapshotkey; `DifferentInstance` bei belegbar verschiedener Instanz; `NotComparable` bei nicht ausreichender gemeinsamer Instanzevidence; `ComparisonFailed` bei Versions-, Kodierungs- oder erhaltenem OS-/Invariantenfehler. Pfadgleichheit allein beweist nie Instanzgleichheit. `FileSnapshot` und sein Schlüssel bleiben Evidence und sind niemals Inhaltsidentität, Vollhash oder der unveränderte Phase-1-`SourceIdentity`-Wert.

### 6.6 Cancellation, Progress und öffentliche 2A-Fehlerbasis

Cancellation ist ein thread-sicherer, monotoner, idempotenter und nicht rücksetzbarer Laufzeitvertrag. Nach beobachtetem Abbruch darf dieselbe Operation keinen Erfolg mehr veröffentlichen. Prüfstellen liegen mindestens vor teuren oder blockierenden Schritten, nach externen beziehungsweise OS-Aufrufen und unmittelbar vor jeder irreversiblen Veröffentlichung. Cancellation und irreversible Veröffentlichung besitzen eine synchronisierte lineare Commitgrenze: linearisiert der Abbruch zuerst, findet kein Publish statt und kein Erfolg wird ausgegeben; linearisiert der Commit zuerst, wird die bereits begonnene nicht sicher abbrechbare OS-Operation vollständig ausgewertet, und ein erst danach eintreffender Abbruch ändert ihr Resultat nicht rückwirkend. `Cancelled`/`E_CANCELLED` bleibt ein eigener Ausgang und wird nicht als I/O-Fehler maskiert.

Progressereignisse sind immutable beziehungsweise als unveränderliche Werte zu behandeln und enthalten mindestens `operation_id`, eine pro Operation streng monoton steigende `sequence`, Ereignisart und bounded strukturierte Nutzdaten. Progress ist weder Log noch Diagnose und trägt keine Exception als Steuerfluss. 2A verwendet synchrone Listenerzustellung mit Backpressure und ohne unbounded Queue; dadurch kann ein langsamer Listener die Operation verzögern, aber keine Ressourcenakkumulation erzeugen. Listener werden über einen stabilen Snapshot der Registrierungen aufgerufen. Listenerfehler werden bounded als getrennte Diagnose erhalten, ersetzen weder Resultat noch Primärfehler der Operation und verhindern die Zustellung an weitere Listener; sie dürfen nur durch eine explizite separate Diagnoseabfrage sichtbar werden.

Die stabile öffentliche 2A-Fehlerbasis umfasst zusätzlich zu den spezifischen Codes aus 6.1, 6.4 und 6.5 mindestens die Kategorien `E_WORKSPACE_INVALID`, `E_PROJECT_ID_COLLISION`, `E_PATH_UNSAFE`, `E_FILE_NOT_REGULAR`, `E_PATH_REPARSE`, `E_PATH_UNC`, `E_PATH_ADS`, `E_PATH_DEVICE_NAMESPACE`, `E_PROJECT_LOCK_BUSY`, `E_PATH_LOCK_BUSY`, `E_LOCK_ACCESS_DENIED`, `E_LOCK_IO`, `E_SNAPSHOT_FAILED`, `E_SNAPSHOT_EVIDENCE_INSUFFICIENT`, `E_ATOMIC_PUBLISH_FAILED`, `E_CAS_CONFLICT` und `E_CANCELLED`. Nur nachgewiesene Sharing-/Lockverletzung am richtigen Ownershipobjekt ist Busy. Unbekannte interne und OS-Ursachen behalten Typ, Code und Verkettung und dürfen insbesondere nicht als Busy, Collision oder CAS-Konflikt umklassifiziert werden.

### 6.7 Atomare 2A-Projektmetadaten

Alle drei Publisharten verwenden eine Tempdatei im Zielverzeichnis. Ihr Name liegt im reservierten 2A-Namensraum `.~matrix-2a-<artifact>-<operation-uuid>.tmp`, wobei `<operation-uuid>` eine neue kanonische UUIDv4 ist. Die Temp wird mit `CREATE_NEW` exklusiv erzeugt, über einen nicht-Reparse-Handle vollständig geschrieben, auf erwartete Bytezahl geprüft, in den Dateidaten geflusht und vor der Veröffentlichung geschlossen, soweit die konkrete Win32-Publishoperation dies fordert. Zielverzeichnis, Temp und Ziel werden unmittelbar vor Publish erneut nach 6.4 validiert.

Für die erstmalige Veröffentlichung eines noch nicht vorhandenen ersetzbaren Artefakts, insbesondere des initialen `project.json`, wird die geflushte Temp ohne Replace atomar zum Ziel verschoben. Ein zwischenzeitlich erschienenes Ziel erzeugt `E_PROJECT_ALREADY_EXISTS` beziehungsweise den artefaktspezifischen Already-exists-Ausgang; es wird nie überschrieben. Erst nach erfolgreichem Publish darf `ProjectCreated` erscheinen.

Für ein vorhandenes, ausdrücklich `R`-markiertes Artefakt MUSS jeder vertragskonforme Writer vor dem ersten Lesen, Validieren oder Ersetzen den zuständigen exklusiven Project Lock besitzen und ihn bis zum Abschluss der Nachvalidierung weiter halten. Revision-CAS ist ausschließlich ein **kooperativer logischer CAS-Vertrag zwischen vertragskonformen Matrix-Auto-Cutter-Writern**. Der Project Lock serialisiert nur diese Writer; `ReplaceFileW` ist zwar eine atomare Replace-Operation, besitzt aber kein atomisches Compare-and-Swap-Prädikat für zuvor gelesene Revision, File-ID oder Digest.

Nach Lockerwerb öffnet und validiert der Writer das vorhandene Ziel vollständig und sicher. Er erfasst erwartete Projektbindung, Artefakttyp, Schema und Revision sowie, soweit verfügbar, File-ID, Volume-ID, Größe, relevante Zeiten und Digest der kanonischen Zielbytes als Zielinstanz-Evidence. Eine bereits beim ersten Lesen festgestellte valide, aber nicht erwartete Revision oder Zielinstanz ist `CasConflict`/`E_CAS_CONFLICT`; ein ungültiges, fremd gebundenes oder nicht vollständig validierbares Ziel ist dagegen ein Integritätsfehler und kein normaler CAS-Konflikt. Unmittelbar vor dem Replace MUSS der Writer Zielpfad, vollständige Zielbytes, Bindungen, Schema, Revision und verfügbare Instanz-Evidence erneut sicher validieren. Jede Abweichung verhindert den Replace: eine weiterhin valide kooperative Erwartungsabweichung ergibt `E_CAS_CONFLICT`; Evidence für fremde oder unerwartete Workspacemutation behält diese Klassifikation und Ursache als strukturierten Integritätsfehler und darf nicht zu CAS-Konflikt oder Lock-Busy herabgestuft werden.

`ReplaceFileW` darf nur nach erfolgreicher Revalidierung, bei identischer erwarteter Revision und unter weiter gehaltenem Project Lock auf kanonische Bytes mit `revision + 1` erfolgen. Danach MUSS der Writer das Ergebnis über den Zielpfad neu öffnen und vollständig gegen die exakt beabsichtigten kanonischen Bytes, Projektbindung, Artefakttyp, Schema und neue Revision validieren. Erst diese erfolgreiche Nachvalidierung erlaubt ein Erfolgsresultat. Ein OS-Fehler beim Replace oder Reopen ist `E_ATOMIC_PUBLISH_FAILED`; ein unerwartetes, abweichendes oder nicht vollständig validierbares Ergebnis ist `E_ATOMIC_PUBLISH_INTEGRITY`. OS-, Evidence- und Integritätsursachen bleiben sichtbar. Bei `E_ATOMIC_PUBLISH_INTEGRITY` oder jedem OS-Fehler, nach dem der Zielzustand nicht vollständig bewiesen werden kann, bleibt das Projekt für weitere vertrauensabhängige Operationen fail closed.

Nicht kooperierende Fremdprozesse oder manuelle Workspacemutation liegen außerhalb des kooperativen Lockvertrags. 2A erkennt sie vor und nach `ReplaceFileW` soweit technisch möglich, garantiert aber ausdrücklich **nicht**, dass ein fremder Writer im getrennten Prüf-/Replace-Fenster niemals überschrieben werden kann. Ein solcher Vorgang darf weder als erfolgreicher Publish noch als normaler CAS-Konflikt oder Lock-Busy verschluckt werden. Backupnamen werden nicht als Wahrheit oder automatischer Recoveryeingang verwendet und müssen, falls die Adapteroperation einen verlangt, im gleichen eigenen Namensraum liegen.

Diese Präzisierung betrifft ausschließlich Updates vorhandener `R`-Artefakte. Erstmaliges create-if-absent nach dem vorstehenden Absatz und immutable `I`-Publishes nach dem folgenden Absatz bleiben unverändert.

Ein `I`-markiertes Artefakt wird ausschließlich create-if-absent ohne Replace veröffentlicht. Ein vorhandenes Ziel bleibt unverändert und wird nur nach seinem eigenen Artefaktvertrag auf idempotente Gleichheit geprüft; sonst ist es ein Konflikt. `ReplaceFileW`, `MOVEFILE_REPLACE_EXISTING` und Last-Writer-Wins sind für `I` verboten.

Cleanup betrifft ausschließlich eine durch denselben Lauf exklusiv erzeugte und anhand Namensraum plus Operations-ID belegte Temp- oder Backupdatei. Es folgt keinem Reparse Point, löscht keine unbekannte Datei und verändert niemals das Ziel. Cleanupfehler werden als sekundäre Diagnose erhalten und ersetzen die primäre Operationsursache nicht. Recovery darf eine Temp-/Backupdatei niemals als Source of Truth lesen, übernehmen oder promoten; unbekannte Dateien bleiben unangetastet.

Wiederverwendet werden ausschließlich unveränderte Phase-1-Prinzipien: kanonische JSON-Bytes, strikte Schema-/Unbekanntfeldprüfung, UUIDv4-Kanonik, monotone Revisionen sowie Erhalt der primären Fehlerursache. Phase 1 und seine Dateien werden nicht geändert. Separat und neu für Phase 2/2A sind der Windows-spezifische sichere Pfad-/Handlevertrag, exklusive Same-Directory-Temps, konkurrierendes First-Publish, kooperatives logisches Revision-CAS/Replace, Reparse-sicheres Cleanup und die hier definierten strukturierten Ergebnisse; daraus wird keine Änderung an Journal 1.0, Sidecar 1.1 oder `SourceIdentity` abgeleitet.

## 7. Paket 2B: Probe Core und Binary-Vertrauen

### 7.1 `ValidatedFfprobeBinary`

Der Probe Core akzeptiert ausschließlich einen Laufzeitwert `ValidatedFfprobeBinary` mit:

- absolutem normalisiertem, handlekanonischem Pfad;
- Nachweis reguläre lokale Datei auf unterstütztem NTFS;
- keine Reparse Points im Endobjekt oder in Vorfahren, keine UNC/ADS/Device-/Cloud-/Wechselmedienpfade;
- Volume ID, `FILE_ID_128`, Größe, Last-Write-Time und Change-Time;
- vollständigem SHA-256 der Binary;
- vollständigem `ffprobe -version`-Versionstring, semantisch geparster Version und vollständiger Build-/Configuration-Zeile;
- Validierungszeitpunkt und Validatorvertragsversion.

Entscheidung: Binary-SHA-256 ist verpflichtend pro Validierungsepoche; es gibt bewusst keine globale kryptografische Allowlist und keine Signaturpflicht, weil Distribution/Publisher noch nicht festgelegt sind und ein Hash ohne autorisierte Vergleichsliste keine Herkunft beweist. Der Hash bindet aber exakt die vom Nutzer lokal konfigurierte und geprüfte Binary gegen Austausch. UI/Config muss diesen lokalen Vertrauensentscheid mit Pfad, Version und Hash sichtbar bestätigen. Eine spätere signierte Distribution benötigt einen neuen Distributionsvertrag.

Unmittelbar vor Prozessstart wird der Binarypfad mit restriktivem Lesehandle erneut geöffnet und Volume/File-ID, Größe, Last-/Change-Time und SHA-256 gegen den validierten Wert geprüft; der Handle bleibt bis erfolgreichem `CreateProcessW`-Start offen. Austausch ergibt `E_PROBE_BINARY_CHANGED`. Der Kindprozess erhält ausschließlich den kanonischen Pfad.

Keine unbegründete Mindestversion 7.x gilt mehr. Vor 2B wird eine testgestützte Supported-Version-Matrix 1.0 als Teil des Probe-Core-Schemas festgeschrieben. Initial zulässig ist exakt die in 2B-Goldens geprüfte Major/Minor/Patch-Version; Erweiterungen erfordern Parser-/Feld-/Prozessgoldens und eine neue Matrixrevision, nicht zwingend ein neues Artefaktschema. Benötigte Fähigkeiten sind `-print_format json`, `-show_error`, `-show_format`, `-show_streams`, `-show_programs`, Stream-Disposition, rationale Timebases/Framerates, Start-/Dauerfelder, `nb_frames`, Rotation/Display-Matrix und JSON-UTF-8-Verhalten. Unbekannte oder nicht gelistete Versionen ergeben `E_PROBE_UNSUPPORTED_VERSION`.

### 7.2 Probe Core

2B enthält Binaryvalidierung, argumentlistenbasierten `CreateProcessW`-Runner ohne Shell/URL, Windows Job Object, Timeout 120 s (1–600 s), Cancellation, stdout 16 MiB, stderr 4 MiB, UTF-8/JSON-Parser, normalisiertes `MediaProfile`, Prozess- und Parserfehler sowie Streamauswahl. Tests verwenden kontrollierte Pfade und Prozessadapter.

2B erhält einen validierten Pfad und erwarteten Snapshotkey, aber **keine** `CloseGateLease`. Es darf eine Datei proben und Evidence erzeugen, behauptet jedoch weder geschlossene/stabile Datei noch Bindung an eine Validierungsepoche oder Finalisierungssicherheit. Ein 2B-Profil wird in 2E unter Lease neu erzeugt; bloße Wiederverwendung eines früheren Pfad-Probes ist für `SourceIdentity` verboten.

### 7.3 Streamauswahl

Automatische Auswahl ist nur bei genau einem eindeutigen Kandidaten erlaubt. Kein kleinster Index löst Gleichstand auf. `attached_pic` ist nie Hauptvideo; Data-/Subtitle-/Attachmentstreams sind nie Audio-/Videohauptstream. Regeln:

- Video: valide decodierbare Metadaten, nicht attached picture; genau eine Default-Disposition gewinnt nur, wenn dadurch genau ein Kandidat verbleibt; dann eindeutige höchste unterstützte Auflösung und eindeutige belastbare CFR-Framerate. Mehrere Defaults, gleiche Auflösung/Framerate oder gleicher Rang sind `E_PROBE_AMBIGUOUS_STREAMS`.
- Audio: valide Samplerate, Kanalzahl/-layout und Dauer; genau ein Default gewinnt; danach nur ein eindeutig ranghöchstes unterstütztes Layout. Gleiche Layouts/Kanalzahl, mehrere Defaults, stumme oder unvollständige Kandidaten sind ambig beziehungsweise unsupported, nicht per Index gelöst.
- Attached pictures bleiben sichtbar im Profil, aber ausgeschlossen. Data streams bleiben sichtbar und führen bei unerwarteter finalisierungsrelevanter Rolle zu unsupported. Stumme Videoquellen oder unvollständige Audio-/Videometadaten dürfen geprobt, aber nicht finalisiert werden.

Alternative ist `StreamAssignment` als persistentes kanonisches Artefakt `stream_assignment`, Schema 1.0, create-if-absent. Es bindet Projekt-ID, SourceSnapshotkey/SourceIdentity soweit vorhanden, `media-probe`-Artefaktdigest und -version, Video-/Audioindex sowie relevante Merkmale (Codec, Disposition, Auflösung/Framerate beziehungsweise Samplerate/Kanäle/Layout/Start/Dauer). 2E validiert jede Bindung gegen die neue Lease-Probe. Eine Zuordnung anderer Quelle, anderer Probeversion, veränderter Merkmale oder fehlenden Streams ist ungültig. Ohne eindeutige oder explizit validierte Auswahl entsteht keine `SourceIdentity`.

## 8. Paket 2C: Close-Gate, Lease und Win32-Exhaustivität

2C öffnet die Source mit `CreateFileW(GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING)` ohne `FILE_SHARE_WRITE` und ohne `FILE_SHARE_DELETE`, nach separater Reparseprüfung. Drei identische Handle-Snapshots S0/S1/S2 im Abstand je 1 s bilden das zweisekündige Stabilitätsfenster. Bei Erfolg entsteht `CloseGateLease` mit offenem Handle, Volume/File-ID, S0–S2 und erlaubter Recheck-Operation. `closed` behauptet nur restriktiven Open plus Stabilität, nicht Probe, Hash oder physische Flush-Garantie.

Win32-Klassifikation:

| Win32-Ausgang | CloseGateResult / Fehler | Regel |
|---|---|---|
| `ERROR_SHARING_VIOLATION`, `ERROR_LOCK_VIOLATION` | `CloseGateBusy` / `E_CLOSE_GATE_BUSY` | erwartbar, neues Gate retrybar |
| `ERROR_ACCESS_DENIED` | `CloseGateInaccessible` / `E_CLOSE_GATE_INACCESSIBLE` | niemals automatisch busy; Rechte/ACL prüfen |
| `ERROR_FILE_NOT_FOUND`, `ERROR_PATH_NOT_FOUND` | `CloseGateDisappeared` / `E_CLOSE_GATE_DISAPPEARED` | neuer Locate/Open-Versuch nötig |
| `ERROR_DELETE_PENDING`, soweit API/NTSTATUS-Mapping erkennbar | `CloseGateDeletePending` / `E_CLOSE_GATE_DELETE_PENDING` | kein Probe-/Hashstart; kein Retry im selben Gate; neuer Open-Versuch |
| Reparse, falscher Dateityp, UNC, Nicht-NTFS | `CloseGateUnsupported` / `E_CLOSE_GATE_UNSUPPORTED` | fail closed |
| `ERROR_INVALID_HANDLE` | `CloseGateUnknownWin32Error` / `E_CLOSE_GATE_WIN32_UNKNOWN` | interner Fehler, Primärcode erhalten |
| Snapshotänderung S0–S2 | `CloseGateUnstable` / `E_CLOSE_GATE_UNSTABLE` | Lease wird nicht ausgegeben |
| sonstiger Fehler | `CloseGateUnknownWin32Error` / `E_CLOSE_GATE_WIN32_UNKNOWN` | Primär-/NTSTATUScode, Phase und Ursache erhalten; nie busy |

Delete-Pending wird über den Openfehler und, soweit verfügbar, `FileStandardInfo.DeletePending`/native Statusabbildung erkannt. Nach Erkennung werden Handle/Locks kontrolliert geschlossen. Probe oder Hash startet nicht; derselbe unveränderte Gateversuch darf nicht loopen. Cancellation liefert einen eigenen kontrollierten Close-Gate-Cancel-Ausgang und keinen `closed`-Wert.

## 9. Paket 2D/2E: Hash, Integration und Identität

2D hasht ausschließlich über den gehaltenen Lease-Handle von Offset 0 bis exakt S0-Ende, Defaultblock 8 MiB, Cancellation vor und zwischen Reads. Nur `bytes_read == size`, unmittelbares EOF und unverändertes S4 erzeugen `HashCompleted`; Cancel, I/O, vorzeitiges EOF, Extra-Bytes oder Snapshotänderung erzeugen keinen Digest/Receipt.

2E ist die einzige Integrationsschicht für pfadbasiertes ffprobe unter Lease:

1. Lease und S0–S2 aus 2C sowie Path-/File-ID-Locks prüfen;
2. unmittelbar vor Probe Pfad erneut auf dieselbe Volume/File-ID auflösen;
3. 2B-Probe Core mit Sourcepfad ausführen, während Lease offen bleibt;
4. S3 über Lease lesen und Gleichheit prüfen;
5. Streamauswahl beziehungsweise gebundene Benutzerzuweisung validieren;
6. 2D-Vollhash und S4 prüfen;
7. unmittelbar vor Identitätsfreigabe S5 und Pfad/File-ID erneut prüfen.

`SourceIdentityEvidence` bindet Projekt-/Run-ID, Lease-Epoche, S0–S5, Volume/File-ID, Probe-Digest/-Version, `ValidatedFfprobeBinary`-Digest, Streamassignment, Hashreceipt und Bindingmodus. Der bestehende Phase-1-`SourceIdentity` bleibt unverändert.

Entscheidung zu `ConfirmedSource`: **Option A, nur Laufzeittyp**. Er ist nicht persistent, besitzt kein Schema, wird pro Lauf aus vollständig validiertem `SourceIdentity` plus `SourceIdentityEvidence` rekonstruiert und darf nie als Trustquelle aus einer Datei geladen werden.

## 10. Sichere Pfade, Hardlinks und Source-Alias

Quelle und Workspace müssen lokale reguläre NTFS-Objekte ohne Reparse Points sein. Pfadprüfung erfolgt lexikalisch und handlebasiert vor jedem Open/Publish. `GetFinalPathNameByHandleW`, Volume ID und `FILE_ID_128` sind maßgeblich. Hardlinks derselben File-ID sind dieselbe Sourceinstanz; ein Pfadlock allein schützt nicht. Der File-ID-Lock folgt erst auf den sicheren Prüfhandle. Das Sidecarziel darf nicht dieselbe File-ID wie die Source besitzen; unerwartete Hardlinkzahl am Ziel oder fremdes Ziel ist Konflikt und wird nie überschrieben.

## 11. Exhaustiver `SourceState`-Automat

Nur die folgenden Namen sind zulässig:

| Zustand | Bedeutung / Eingang | Übergänge | Persistenz/Artefakte | Fehler, Cancellation, Recovery |
|---|---|---|---|---|
| `unknown` | noch keine Quelle | `located`, `disappeared`, `unsupported`, `failed`, `cancelled` | Laufzeit; keine | neu lokalisieren; Cancel terminal |
| `located` | zulässiger Pfad, noch kein Gate | `awaiting_close`, `disappeared`, `unsupported`, `failed`, `cancelled` | Referenz/Snapshot optional | neuer Open-Versuch |
| `awaiting_close` | Gate läuft/ist busy | `closed`, `located`, `disappeared`, `unsupported`, `failed`, `cancelled` | Gate-Diagnose | busy/unstable => neues Gate; DeletePending => `located` erst nach neuem Open |
| `closed` | Lease + S0–S2 | `probing`, `invalidated`, `disappeared`, `failed`, `cancelled` | Lease Laufzeit; Snapshots Evidence | Neustart immer ab `located`/neuem Gate |
| `probing` | Probe Core unter Lease | `probed`, `invalidated`, `unsupported`, `failed`, `cancelled` | Probe-run uncommitted | Probe-Cancel terminal für Run; Retry neues Gate |
| `probed` | eindeutiges/zugewiesenes Profil + S3 | `hashing`, `invalidated`, `unsupported`, `failed`, `cancelled` | media-probe 1.0 | Retry neues Gate |
| `hashing` | Vollhash über Lease | `hash_completed`, `invalidated`, `failed`, `cancelled` | Fortschritt, noch kein Receipt | kein Partialresume; Retry neues Gate |
| `hash_completed` | Vollhash + S4 | `confirming_identity`, `invalidated`, `failed`, `cancelled` | Hashreceipt 1.0 | Kreuzprüfung oder neues Gate |
| `confirming_identity` | S5/Bindungen werden geprüft | `confirmed`, `invalidated`, `disappeared`, `failed`, `cancelled` | Evidence uncommitted | keine Teilidentität |
| `confirmed` | SourceIdentity + validierte Evidence | `invalidated`, `disappeared` | persistente Evidence, ConfirmedSource Laufzeit | pro Run rekonstruieren |
| `invalidated` | Epoche widersprüchlich/verändert | `located`, `disappeared`, `failed`, `cancelled` | Fehler/Evidence | niemals zurück auf confirmed ohne neues Gate |
| `disappeared` | Source/Pfad nicht auffindbar | `located`, `cancelled` | Diagnose | explizit neu lokalisieren |
| `unsupported` | Policy/Medium/Filesystem nicht unterstützt | terminal | Report | neue Eingabe/Version erforderlich |
| `cancelled` | kontrollierter Abbruch | terminal | Cancel-State | neuer Run |
| `failed` | interner oder nicht als Fachzustand modellierter Fehler | terminal | Fehlerreport | Ursache erhalten; neuer Run nach Policy |

Zustände selbst sind Laufzeitwerte; nur `finalizer_state` 1.0 kann den letzten sicheren Zustand diagnostisch persistieren. Recovery vertraut ihm nicht ohne Artefaktkreuzprüfung.

## 12. Finalizerzustände

Finalizerzustände sind `discovered -> validating_input -> resolving_source -> awaiting_close -> probing -> hashing -> confirming_identity -> preparing_intent -> constructing_sidecar -> committing_sidecar -> finalized`. Terminal sind `cancelled`, `failed`, `quarantined`. Jeder Übergang wird in `finalizer-state` 1.0 gespeichert; ein fehlender State verhindert Recovery nicht.

`validating_input` akzeptiert explizit entweder Legacy-Journal 1.0 oder Bundle 1.0 nach Kapitel 5. `preparing_intent` muss vor Sidecarkonstruktion erfolgreich sein. Cancellation vor Commit ergibt `cancelled`; ab Beginn des nicht unterbrechbaren create-if-absent-Commits wird danach stets Zielvalidierung ausgeführt und entweder `finalized` oder `RecoveryConflict` gemeldet.

## 13. Determinismus und `FinalizationIntent`

Allgemein garantiert der Plan **semantischen Determinismus**: gleiche bestätigte Eingaben und Vertrags-/Algorithmusversionen ergeben gleiche Ranges, Drift-, Pause-, Pairing-, Protection- und `SourceIdentity`-Werte.

Byteidentischer Retry-Determinismus gilt genau für dieselbe persistierte `FinalizationIntent`. Sie wird vor Sidecarkonstruktion create-if-absent geschrieben und enthält:

- `finalizer_run_id` und einmalig gewähltes `finalized_at` mit Zeitzone;
- Projekt-ID, Eingangsprofil und Recording-ID;
- SHA-256/Größe des kanonischen Journal-1.0-Snapshots beziehungsweise sichere Bundlebindung;
- vollständigen SourceIdentity-Digest und Evidence-/Probe-/Hash-Artefakt-IDs;
- Versionen von Sidecar-, Journal-, Bundle-, Finalizer-, Clock-, Pairing-, Protection- und Serialisierungsvertrag;
- Zielpfad-Digest und eindeutige Zielgeneration UUIDv4;
- `finalization_key` über alle vorgenannten semantischen Werte.

Retries derselben Intent verwenden exakt deren `finalizer_run_id` und `finalized_at`; sie erzeugen byteidentische Sidecarbytes. Ohne passende Intent wird keine Bytegleichheitsbehauptung gemacht und eine neue Intent darf nicht als Retry derselben Generation ausgegeben werden. Ein Intentkonflikt wird quarantänisiert; Provenienzwerte werden weder neu erfunden noch aus einer fremden Intent übernommen.

## 14. Sidecar-Publish: create-if-absent ohne fremdes Überschreiben

Unveränderliche Finalartefakte sind Sidecar 1.1, Finalization Receipt und generation-gebundene Evidence/Intent. Sie werden create-if-absent publiziert. Fachlich ersetzbar sind nur ausdrücklich als `R` markierte Projektmetadaten wie `project.json` und `finalizer-state.json`; ihr Replace-Vertrag besitzt Revision-CAS und darf nie auf Finalartefakte angewandt werden.

Konkrete Win32-Strategie für das erstmalige Sidecar:

1. Temp im Sourceverzeichnis mit `CreateFileW(..., CREATE_NEW)` erzeugen, vollständig schreiben, `FlushFileBuffers`, schließen.
2. Ziel-/Sourcepfad, Ziel-Lock, Lease, S5 und File-ID unmittelbar erneut prüfen.
3. Temp mit `MoveFileExW(temp, target, MOVEFILE_WRITE_THROUGH)` **ohne** `MOVEFILE_REPLACE_EXISTING` umbenennen. Windows-Rename ohne Replace ist die atomare create-if-absent-Operation auf demselben lokalen NTFS-Volume.
4. `ERROR_FILE_EXISTS`/`ERROR_ALREADY_EXISTS` ist `TargetAlreadyExists`, kein Anlass zum Replace. Das bestehende Ziel wird bounded vollständig gelesen, Sidecar-1.1-schema- und semantikvalidiert, gegen SourceIdentity, Recording-ID und Intentwerte geprüft. Nur bestätigte semantische Identität gilt als idempotenter Erfolg; es wird nicht neu geschrieben.
5. Fremdes, beschädigtes, unbekanntes oder nicht zuordenbares Ziel ist `E_TARGET_ALREADY_EXISTS`/`RecoveryConflict`, bleibt unverändert und wird nur im Projekt reportiert. Nicht kooperierende Zielerzeugung endet fail closed.

Eine vorherige Existenzprüfung ist lediglich Diagnose und nie der Atomizitätsbeweis. `MOVEFILE_REPLACE_EXISTING` ist für das Sidecar und alle unveränderlichen Finalartefakte verboten.

## 15. Commit-, Crash- und Recoveryvertrag

Gewählt ist **Strategie B: Sidecar als alleinige Source of Truth**. Der atomare erfolgreiche create-if-absent-Rename des vollständig geflushten Sidecar 1.1 ist der einzige fachliche Commitpunkt. Sidecar 1.1 enthält bereits Recording-ID, vollständige SourceIdentity, Lifecycle mit `finalizer_run_id`/`finalized_at`, Producer/Finalizer-Version, Clock, Pause, Events und FinalizationEvidence. Die `FinalizationIntent` bindet zusätzlich Journaldigest und Algorithmen. Receipt und State sind rekonstruierbare Evidence, kein Commitbestandteil.

Es gibt keine Behauptung, mehrere Renames seien gemeinsam atomar. Eine „Generation“ besteht logisch aus Intent, Sidecar, Evidence, Receipt und State mit derselben Zielgeneration; **nur das Sidecar** committed sie. Leser erkennen Finalisierung ausschließlich am erwarteten Sidecarpfad und dessen vollständiger Phase-1-Schema-/Sourcevalidierung. Uncommitted Intent/Evidence/Temp/State bleiben für Consumer unsichtbar.

| Crashpunkt | Sichtbar / Source of Truth | Wiederanlauf, Quarantäne, Cleanup und Idempotenz |
|---|---|---|
| vor Temp-Schreiben | höchstens Intent/Evidence; kein Sidecar | kein Commit; neue Temp derselben Intent; widersprüchliche Evidence quarantänisieren |
| während Temp-Schreiben | partielle eindeutige Temp | Temp niemals lesen/publizieren; nach Ownershipprüfung löschen oder quarantänisieren; gleicher Intent-Retry |
| nach Temp-Flush | vollständige Temp, aber kein Sidecar | weiterhin uncommitted; Temp nicht „promoten“; neu konstruieren/byteprüfen und normaler create-if-absent-Versuch |
| vor Final-Commit | Intent+Temp | Ziel erneut prüfen; normaler Commit; fremdes Ziel nie ersetzen |
| unmittelbar nach Final-Commit | gültiges Sidecar, Receipt/State evtl. fehlend | Sidecar ist Source of Truth und Lauf gilt finalisiert; vollständige Sidecar-/Source-/Intentprüfung, dann Receipt/State deterministisch create-if-absent rekonstruieren |
| vor State-Update | Sidecar und evtl. Receipt | finalisiert; fehlenden State aus Sidecar/Intent/Receipt rekonstruieren; Konflikt quarantänisieren |
| nach State-Update | vollständige oder teilweise abgeleitete Evidence | Sidecar entscheidet; State/Receipt kreuzprüfen; kein Rewrite |

Fehlt Intent nach sichtbar gültigem Sidecar (etwa Verlust eines Projektartefakts), verhindert dies nicht die Anerkennung des Sidecars als finalisiert. Recovery kann Receipt/State aus Sidecar, erneut validiertem Journal und bestätigter Quelle rekonstruieren; kann die Journalbindung nicht mehr belegt werden, bleibt das Sidecar als Phase-1-Source-of-Truth gültig, während zusätzliche Phase-2-Provenienz ausdrücklich `not_reconstructable` bleibt. Es wird weder gelöscht noch überschrieben. Verwaiste uncommitted Generationen werden reportiert; nur eigene eindeutig benannte Temps dürfen gelöscht, andere Dateien nur unangetastet reportiert werden.

## 16. Vollständige Resultat-/Fehlermatrix

`Safe` bedeutet no-sidecar Safe-Mode für nachgelagerte Auto-Cuts. Persistente Artefakte sind auf die genannten Diagnose-/Evidenceobjekte begrenzt; niemals entsteht bei Fehler ein neues finales Sidecar. Unerwartete `TypeError`, `RuntimeError`, Invariantenverletzungen und unbekannte Exceptions werden nicht als erwartbare Eingabefehler verschluckt; Ursache, Trace/Win32-Code und `__cause__` bleiben im internen Fehler erhalten.

| Operation Result | stabiler ErrorCode | Art | Finalizerzustand | Retry | Nutzeraktion | erlaubte Artefakte | Log | Safe |
|---|---|---|---|---|---|---|---|---|
| WorkspaceOrProjectInvalid | spezifischer `E_WORKSPACE_*`/`E_PROJECT_*` aus 6.1 | Eingabe/Policy | noch kein Finalizerlauf | nach Korrektur | Root/Projekt prüfen | bounded Report, keine Übernahme | warn/error | ja |
| ProjectCollision | `E_PROJECT_ALREADY_EXISTS`/`E_PROJECT_ID_COLLISION` | erwartete Kollision | noch kein Finalizerlauf | neue UUID bis Budget | keine oder Root prüfen | keine fremden Artefakte | info/error | ja |
| UnsafePath | spezifischer `E_PATH_*` aus 6.4 | Sicherheitsinput | noch kein Finalizerlauf/failed | nur korrigierte Eingabe | sicheren lokalen Pfad wählen | redigierter Report | warn | ja |
| ProjectOrPathLockBusy | `E_PROJECT_LOCK_BUSY`/`E_PATH_LOCK_BUSY` | erwartet, nur belegte Lockverletzung | noch kein Finalizerlauf/failed | später/zeitbegrenzt | Besitzer beenden/warten | Lockdiagnose | info/warn | ja |
| LockAccessOrIoFailed | `E_LOCK_ACCESS_DENIED`/`E_LOCK_IO` | OS/I/O | noch kein Finalizerlauf/failed | ursachenabhängig | Rechte/Medium prüfen | redigierter Report | error | ja |
| SnapshotFailedOrInsufficient | spezifischer `E_SNAPSHOT_*` aus 6.5 | Eingabe/OS/Evidence | noch kein Finalizerlauf/failed | ursachenabhängig | Quelle/Plattform prüfen | Snapshot-/Fehlerreport | warn/error | ja |
| AtomicMetadataPublishFailed | `E_ATOMIC_PUBLISH_FAILED`/`E_ATOMIC_PUBLISH_INTEGRITY` | OS/I/O oder unerwartete Workspacemutation | noch kein Finalizerlauf/failed | nur nach vollständiger neuer Projektvalidierung | Datenträger/Rechte/Workspaceintegrität prüfen | eigene Temp, Report; Zielzustand nicht verschleiern | error | ja |
| MetadataCasConflict | `E_CAS_CONFLICT` | erwarteter Konkurrenzkonflikt | noch kein Finalizerlauf/failed | neu lesen/neue Operation | Projekt neu laden | Report; Ziel unverändert | warn | ja |
| OperationCancelled | `E_CANCELLED` | erwartet Cancel | cancelled/noch kein Finalizerlauf | neue Operation | erneut starten | bounded Cancel-State | info | ja |
| CloseGateBusy | `E_CLOSE_GATE_BUSY` | erwartet | `awaiting_close`/failed nach Budget | ja, neues Gate | Writer schließen/warten | Diagnose, Snapshots | info/warn | ja |
| CloseGateUnstable | `E_CLOSE_GATE_UNSTABLE` | erwartet | failed | ja, neues Gate | Aufnahme/Remux beenden | Evidence | warn | ja |
| CloseGateInaccessible | `E_CLOSE_GATE_INACCESSIBLE` | erwartet OS | failed | bedingt | Rechte/ACL prüfen | Report | error | ja |
| CloseGateDeletePending | `E_CLOSE_GATE_DELETE_PENDING` | erwartet OS | failed | nur neuer Open | Löschung/Rename abwarten | Report | warn | ja |
| CloseGateDisappeared | `E_CLOSE_GATE_DISAPPEARED` | erwartet | failed | nach Locate | Quelle wiederherstellen | Report | warn | ja |
| CloseGateUnsupported | `E_CLOSE_GATE_UNSUPPORTED` | erwartet Policy | failed | nein gleiche Eingabe | lokale NTFS-Datei wählen | Report | warn | ja |
| CloseGateUnknownWin32Error | `E_CLOSE_GATE_WIN32_UNKNOWN` | intern/unbekannt | failed | nein automatisch | Diagnose melden | redigierter Report | error | ja |
| ProbeCancelled | `E_PROBE_CANCELLED` | erwartet Cancel | cancelled | neuer Run | erneut starten | Cancel-State, bounded Logs | info | ja |
| ProbeTimeout | `E_PROBE_TIMEOUT` | erwartet | failed | ja neues Gate | Binary/Medium prüfen | Prozessreport | warn | ja |
| ProbeStartFailed | `E_PROBE_START_FAILED` | Umgebung | failed | nach Korrektur | Binary konfigurieren | Report | error | ja |
| ProbeOutputLimitExceeded | `E_PROBE_OUTPUT_LIMIT` | Sicherheitsinput | quarantined | nein automatisch | Binary/Medium prüfen | bounded Raw/Report | error | ja |
| ProbeInvalidUtf8 | `E_PROBE_INVALID_UTF8` | Eingabe/Subprozess | failed | nach Korrektur | Binary prüfen | bounded Raw | error | ja |
| ProbeInvalidJson | `E_PROBE_INVALID_JSON` | Eingabe/Subprozess | failed | nach Korrektur | Binary/Medium prüfen | bounded Raw | error | ja |
| ProbeUnsupportedVersion | `E_PROBE_UNSUPPORTED_VERSION` | Umgebung | failed | nach Matrixupdate | unterstützte Binary wählen | Binaryreport | warn | ja |
| ProbeAmbiguousStreams | `E_PROBE_AMBIGUOUS_STREAMS` | erwartete Ambiguität | quarantined/await assignment | ja mit Assignment | Streams explizit zuordnen | Probeprofil/Report | warn | ja |
| HashCancelled | `E_HASH_CANCELLED` | erwartet Cancel | cancelled | neuer Run/Gate | erneut starten | State, kein Receipt | info | ja |
| HashIoError | `E_HASH_IO` | OS/I/O | failed | OS-codeabhängig neues Gate | Datenträger prüfen | Report | error | ja |
| HashUnexpectedEof | `E_HASH_UNEXPECTED_EOF` | Sicherheitsinput | quarantined | neues Gate | Source prüfen | Snapshots/Report | error | ja |
| SourceChanged | `E_SOURCE_CHANGED` | Sicherheitsinput | quarantined | neues Gate | stabile Source liefern | Evidence, kein Identityreceipt | error | ja |
| JournalIncomplete | bestehend `E_JOURNAL_INCOMPLETE` | Eingabe | failed | nein unverändert | vollständiges Journal wählen | Recoveryreport | warn | ja |
| JournalCorrupt | `E_JOURNAL_CORRUPT` | Sicherheitsinput | quarantined | nein unverändert | Original sichern/Producer prüfen | unveränderte Belege/Report | error | ja |
| JournalSourceMismatch | `E_JOURNAL_SOURCE_MISMATCH` | Sicherheitsinput | quarantined | mit richtiger Zuordnung | richtige Source wählen | Report | error | ja |
| ConcurrentFinalizer | `E_FINALIZER_CONCURRENT` | erwartet | failed | später | anderen Lauf beenden | Lockdiagnose | warn | ja |
| TargetAlreadyExists | `E_TARGET_ALREADY_EXISTS` | Kollision | finalized nur bei identischer Vollvalidierung, sonst quarantined | nur identisch | Ziel prüfen | Report; Ziel unverändert | warn/error | ja bei Konflikt |
| AtomicPublishFailed | `E_ATOMIC_PUBLISH_FAILED` | OS/I/O | failed/recovery | nach Zielprüfung | Datenträger/Rechte prüfen | Temp/Report | error | ja |
| RecoveryConflict | `E_RECOVERY_CONFLICT` | Sicherheitskonflikt | quarantined | nein automatisch | manuell auditieren | Quarantänebericht | error | ja |
| UnexpectedInternalError | `E_FINALIZER_INTERNAL` | intern | failed | nein automatisch | Entwicklerdiagnose | redigierter Report | critical | ja |

## 17. Journalproducer und Receipts

Journal 1.0 bleibt append-only UTF-8-NDJSON mit unveränderten Phase-1-Records, Limits und erfolgreichem `stopped_unfinalized`-Stop. Das Plugin darf keine Receiptfelder in Records ergänzen. Für neue Pluginläufe erzeugt 2H standardmäßig Bundle 1.0: Session Receipt vor Journalstart, Integrity Receipt nach erfolgreichem Stop und Manifest nach allen Pflichtteilen. Receipts binden Recording-ID und Plugin-Run-ID; Integrity bindet Journaldigest und Session-Receipt-Digest. Das ist ein Integritätsnachweis, keine Signatur gegen privilegierte lokale Angreifer. Paket 2F implementiert beide Eingangsprofile bereits vollständig; 2G verändert keine 2F-Sicherheitsvoraussetzung nachträglich.

## 18. Implementierungspakete 2A bis 2H

Jedes Paket besitzt eine separate Commit-Grenze und benötigt explizite Freigabe. „Commit-Grenze“ beschreibt nur späteren Scope; dieser Plan staged oder committed nichts.

### 18.1 Paket 2A — Workspace- und Vertragsfundament

- **Scope:** Workspace, UUIDv4-create-if-absent mit 16 Retries, sichere Pfade/`%LOCALAPPDATA%`-Root, normative Artefaktgrundtypen, `FileSnapshot`, Project-/Path-Ownership und getrennte Diagnose, Cancellation-/Progress-Grundtypen, Fehlergrundlage, atomare I/R-Primitiven.
- **Nicht-Scope:** ffprobe, Close-Gate-Entscheidung, File-ID-Lock vor verfügbarer File-ID, Hash, Identity, Finalizer, OBS.
- **Eingänge/Ausgaben/Typbesitz:** Phase 1/stdlib/Win32 -> `ProjectLayout`, `ValidatedPath`, Snapshot-/Artefaktmodelle; 2A besitzt Grundtypen.
- **Abhängigkeiten:** nur Phase 1/stdlib/Win32; keine neue Paketabhängigkeit.
- **Qualitätsgates und risikoreiche Testbereiche:** neue UUID-/Crashwaisen-/Open-Validierung, Komponenten- und Unicode-Pfadproperties, Root/ACL/Reparse/UNC/ADS/Device/Long-Path/NTFS, Casekollisionen, Snapshot-Instanzvergleiche mit fehlender Evidence, Hardlinkgrundlagen, Ownership vs Diagnose, Cancellation-/Progresskonkurrenz sowie First-Publish/I/R/CAS-/Cleanup-Fehler. Dies ist keine exhaustive Testmatrix.
- **Audit-Gate:** Rootescape vor und nach Handle-Open unmöglich, Source unverändert, geschlossenes 2A-Layout, stabile diskriminierte Ergebnisse, keine unversionierte JSON-Datei, Ownership und Atomik auf realem Windows technisch getestet, 100/100 Python-Kern, Phase 1 grün. Fachlogik (Validierung, Zustands-/Resultatentscheidungen, kanonische Evidence) und injizierbare Win32-Adapter (Open/Query/Flush/Move/Replace/Lock) besitzen eine testbare Verantwortungsgrenze; die endgültige Modulstruktur und öffentliche Funktionssignaturen bestimmt erst der separate 2A-Implementierungsauftrag.
- **Commit-Grenze:** ausschließlich 2A-Code/Tests/Doku; kein 2B.

### 18.2 Paket 2B — Probe Core

- **Scope:** `ValidatedFfprobeBinary`, Versionsmatrix, Runner/Job Object, Limits/Timeout/Cancel, Parser/Profile, eindeutige Streamauswahl und Ambiguitätsfehler.
- **Nicht-Scope:** Lease, Dateifinalität, stabile Sourcebehauptung, Hash, Identity, Finalizer, Binaryinstallation.
- **Eingänge/Ausgaben/Typbesitz:** validierter Pfad/Snapshot/Binary -> `ProbeCoreResult`; 2B besitzt Probe-/Assignmentschemas.
- **Abhängigkeiten:** 2A.
- **Tests:** Binaryaustausch, Versionen, UTF-8/JSON/Limits/Kill, alle Streamties/attached/data/stumm/unvollständig.
- **Audit-Gate:** keine Finalitätsbehauptung; Binaryvertrauen und Ambiguität fail closed; Prozess/Parser sicher.
- **Commit-Grenze:** Probe Core, kein 2C.

### 18.3 Paket 2C — Close-Gate und Lease

- **Scope:** restriktiver Handle, S0–S2, Stabilitätsfenster, `CloseGateLease`, File-ID-Ermittlung/-Lock, Rechecks, Delete-Pending und Win32-Exhaustivität.
- **Nicht-Scope:** Probeparser, Hashinhalt, Identity, Journal, Finalizer.
- **Eingänge/Ausgaben/Typbesitz:** 2A-Pfad/Snapshot/Cancellation -> Lease/strukturierte Ergebnisse; 2C besitzt Gate-/Lease-Typen.
- **Abhängigkeiten:** 2A, nicht 2B.
- **Tests:** echte Sharematrix, Delete-Pending soweit erzeugbar/Adaptermapping, AccessDenied, unknown code, Hardlinks, Wachstum/Rename/Reparse.
- **Audit-Gate:** kein unbekannter Zustand wird closed; File-ID-Lock erst nach ID; Lease hält denselben Handle.
- **Commit-Grenze:** Gate, kein 2D.

### 18.4 Paket 2D — Vollhash über Lease

- **Scope:** vollständiger SHA-256, Blocklimits, Cancellation, EOF, S0/S4, Hash Receipt 1.0.
- **Nicht-Scope:** Probe, Cachevertrauen, Identity, Sidecar.
- **Eingänge/Ausgaben/Typbesitz:** 2C-Lease -> HashResult; 2D besitzt Hashschema.
- **Abhängigkeiten:** 2C.
- **Tests:** Vektoren, Truncate/Grow/I/O/Cancel, kein Digest bei Fehler.
- **Audit-Gate:** Vollständigkeit, O(Block), TOCTOU und Primärursache bewiesen.
- **Commit-Grenze:** Hasher, kein 2E.

### 18.5 Paket 2E — Lease-Probe-Integration und Identität

- **Scope:** 2B-Probe unter 2C-Lease, S3–S5, File-ID-Rechecks, Streamassignment, Phase-1-SourceIdentity, Evidence; `ConfirmedSource` nur Laufzeit.
- **Nicht-Scope:** Journalladen, Sidecarbau, Auto-Cut.
- **Eingänge/Ausgaben/Typbesitz:** 2B+2C+2D -> SourceIdentity/Evidence/ConfirmedSource runtime; 2E besitzt Integrations-/Evidence-Schema.
- **Abhängigkeiten:** 2B, 2C, 2D.
- **Tests:** Probe/Lease-Races, Streamassignmentalterung, Move/Rename/Copy/Hardlink, S3–S5-Mutation.
- **Audit-Gate:** keine Identity ohne eindeutigen Stream und zusammenhängende Lease-Epoche; Phase-1-Typ unverändert.
- **Commit-Grenze:** Identity, kein 2F.

### 18.6 Paket 2F — Finalizer und Recovery

- **Scope:** Legacy-Journal und Bundle 1.0, Loader/Bindung, `FinalizationIntent`, State Machine, Phase-1-Kalibrierung/Pause/Pairing/Protection, Locks, Sidecar-create-if-absent, Receiptrekonstruktion, Strategie-B-Recovery/Idempotenz.
- **Nicht-Scope:** Producer/OBS, Auto-Cut, EDL, UI, Rendering.
- **Eingänge/Ausgaben/Typbesitz:** beide Kapitel-5-Profile + 2E -> Sidecar 1.1 oder strukturiertes Terminalresultat; 2F besitzt Intent/State/Receipt/Bundle-Consumer.
- **Abhängigkeiten:** 2A–2E und unveränderte Phase 1.
- **Tests:** Journal-only ohne Receipts, kaputtes Bundle ohne stillen Fallback, alle Crashpunkte, Zielrace, identisches/fremdes Sidecar, zwei Finalizer, Receipt-/Stateverlust.
- **Audit-Gate:** Journal 1.0 eigenständig finalisierbar; Commitpunkt eindeutig; kein Replace/fremdes Überschreiben; Crash nach Commit sicher erkannt.
- **Commit-Grenze:** Finalizer vollständig, kein 2G/2H.

### 18.7 Paket 2G — Producer-Conformance, ABI und native Qualität

- **Scope:** bytegenaue Journal-/Bundle-/Receipt-Goldens für den bereits festen Mindestvertrag, Callback-/Queue-/Serializerregeln, exakter OBS-SDK-/Patchstand, C-ABI, Windows-C++-Coveragefreigabe.
- **Nicht-Scope:** Änderung der 2F-Pflichten, kompilierbarer Plugincode oder OBS-Anbindung.
- **Eingänge/Ausgaben/Typbesitz:** auditierter 2F-Loader -> Cross-Language-Conformance-Suite und ABI-/Coveragefreigabe.
- **Abhängigkeiten:** 2F.
- **Tests:** Goldens, partielle Zeile, Queueüberlauf, Crash, UUID/Counter/Flush, Schema-Cross-Language.
- **Audit-Gate:** Mindestvertrag unverändert producerseitig erfüllbar; OBS-Version und Coveragewerte freigegeben; 2H weiterhin separat gesperrt.
- **Commit-Grenze:** Vertrag/Goldens, kein Plugin.

### 18.8 Paket 2H — Natives OBS-Plugin

- **Scope:** nach separater Freigabe C++20-Plugin über offizielle C-ABI, Signale/Hotkeys, bounded Queue, Journal-/Bundlewriter, Finalizerübergabe, OBS-E2E.
- **Nicht-Scope:** Probe/Hash/Sidecar im Plugin, Netzwerk, Medienänderung, EDL/Auto-Cut/UI.
- **Eingänge/Ausgaben/Typbesitz:** 2G-Goldens/ABI -> Journal 1.0 plus Bundle 1.0; Finalizer bleibt alleiniger Sidecarwriter.
- **Abhängigkeiten:** 2G plus explizite Freigabe.
- **Tests:** Lifecycle/Reentrancy/Pause/Pairing/Queue/Disk/Crash/Split, 30-Minuten-Remux und Direct-MP4.
- **Audit-Gate:** native Coveragepolitik erfüllt, echte OBS-Matrix bestanden, kein Sidecar bei Producerfehler.
- **Commit-Grenze:** isolierter Plugincode, kein Auto-Cut.

## 19. Native Test- und Coveragepolitik

Vor 2H, spätestens im 2G-Audit, wird Windows-native Coverage mit Visual Studio Enterprise Dynamic Native Code Coverage (`Microsoft.CodeCoverage.Console`, `/PROFILE`, branchfähige native Instrumentierung in der freigegebenen VS-Toolchain) erhoben und als Cobertura plus HTML archiviert. Falls die im freigegebenen VS-Patchstand erzeugten Branchdaten nach einem dokumentierten Golden-Selbsttest nicht belastbar sind, ist OpenCppCoverage mit exakt fixierter Version als zweiter Messlauf zulässig; 2H bleibt bis zur Toolfreigabe blockiert.

Getrennte Messmodule und Ziele:

| Modul | Line/Statement | Branch | Function | Ausschlussregel |
|---|---:|---:|---:|---|
| pure Journal-Core-Logik | 100 % | 100 % | 100 % | keine fachliche Zeile |
| bounded Queue | 100 % | 100 % | 100 % | keine |
| Serializer/Flush-State | 100 % | 100 % | 100 % | keine |
| UUID-/Counterlogik | 100 % | 100 % | 100 % | keine |
| Plattformadapter Win32 | >=95 % | >=90 % | 100 % | nur vom Tool nicht instrumentierbare OS-Thunks |
| OBS-ABI-Adapter | >=90 % | >=85 % | 100 % registrierter Adapterfunktionen | nur nachweislich nicht synthetisierbare OBS-interne Fehlerkante |

Der vollständig testbare Kern hat damit 100 % relevante Line/Statement- und 100 % relevante Branch-Coverage. Ausschlüsse sind einzeln mit Datei, Funktion, Zeile, Toolgrund und manueller Auditentscheidung in einer Allowlist zu dokumentieren; generierte OBS-/SDK-Header, Compiler-Thunks und fremder Bibliothekscode dürfen ausgeschlossen werden, eigene Adapterlogik nicht pauschal. Jeder reale OBS-ABI-Ausschluss benötigt Integrationstest, E2E-Testmatrix (Start/Stop, Pause/Resume, Szenen/Intro/Outro/Stinger, Hotkeys, Queuevoll, Diskfehler, Crash, Split, Remux/Direct-MP4) und manuelles Review. Keine pauschale Coverage-Ausnahme ist zulässig. 2H bleibt gesperrt, bis der 2G-Audit diese Messung, Toolversionen, Ziele und Ausnahmen ausdrücklich freigibt.

## 20. Szenarien A bis J

| Szenario | Normativer Ablauf und Ergebnis | Bewertung |
|---|---|---|
| A — normale OBS-MKV | gültiges Legacy-Journal 1.0 **oder** validiertes Bundle; eindeutiges Remux-MP4; Locks -> CloseGateLease -> Lease-Probe/eindeutige Streams -> Vollhash -> SourceIdentity/Evidence -> Intent -> Sidecar create-if-absent -> Zielvalidierung -> Receipt/State; Recovery erkennt Sidecar als Commit | eindeutig und konservativ |
| B — Direct-MP4 noch offen | Sharing violation/busy oder unstable; kein Lease-Probe/Hash/Intent/Sidecar; neues Gate nach Stop | eindeutig und konservativ |
| C — Legacy-Journal ohne Receipts | Phase-1-valides `stopped_unfinalized` wird finalisiert; zusätzliche Provenienz `not_available`, keine Vertrauensaufwertung | eindeutig und konservativ |
| D — beschädigtes Bundle | Bundle abgewiesen/quarantänisiert; kein stiller Fallback; explizit neuer Legacy-Request darf unverändertes Journal separat prüfen | eindeutig und konservativ |
| E — mehrere plausible Streams | `E_PROBE_AMBIGUOUS_STREAMS`; keine SourceIdentity; nur korrekt gebundene persistierte Benutzerzuweisung erlaubt Fortsetzung | eindeutig und konservativ |
| F — Delete-Pending/Sourcewechsel | eigenes Ergebnis, kein Probe-/Hashstart beziehungsweise Invalidierung S3–S5; neuer Open/Gate erforderlich | eindeutig und konservativ |
| G — Crash nach Final-Commit | Sidecar 1.1 ist bereits Source of Truth; Recovery validiert Sidecar/Source/Intent, erkennt finalisiert und rekonstruiert fehlendes Receipt/State; fremdes Ziel nie überschrieben | eindeutig und konservativ |
| H — zwei Finalizer | feste Lockreihenfolge; Ownershiphandle allein besitzt Lock, getrennte Diagnose; zweiter Prozess fail-fast; selbst bei Zielrace gewinnt create-if-absent, Verlierer validiert oder meldet Konflikt; kein Deadlock/Last-Writer-Wins | eindeutig und konservativ |
| I — Hardlink/zweites Projekt | getrennte Pathlocks, danach gleiche File-ID und ein Source-Instanzlock; serialisiert; Sidecarziel darf kein Sourcealias sein | eindeutig und konservativ |
| J — fremdes/beschädigtes Sidecar | Ziel bleibt unverändert; vollständige Validation scheitert; `TargetAlreadyExists`/RecoveryConflict, Quarantänebericht im Projekt, Safe-Mode | eindeutig und konservativ |

## 21. Audit-Gates und Qualitätsnachweise

Jedes Paketaudit bestätigt Scope/Nicht-Scope, nur paketbezogene Dateien, grüne unveränderte Phase-1-Suite, keine unfreigegebene Abhängigkeit/Netzwerk/Sourcemutation, Error-/Cancellation-/Atomik-Invarianten, reale Windows-Tests, keine Folgepaketimplementierung und separate nächste Freigabe. Für neuen Python-Kern gelten 100 % Statement und Branch, `mypy --strict`, Ruff und Ruff-Format. Native Ziele stehen in Kapitel 19.

Zusätzliche harte Gates:

- 2A: Projektidentität/Layout, ValidatedPath, FileSnapshot, Lockroot, Ownership/Diagnose, Cancellation/Progress, Fehlerbasis und atomare I/R/CAS-Grundtypen implementierungsreif; risikoreiche Windows-/Konkurrenzpfade nach 18.1 nachgewiesen; keine offene Frage blockiert.
- 2B: Binarymatrix/-austausch und Streamambiguität fail closed; keine Finalitätsbehauptung.
- 2C: Lease/Win32/Delete-Pending exhaustiv; File-ID-Lock erst nach ID.
- 2D: kein Partialdigest/Receipt.
- 2E: Probe unter Lease, S3–S5, keine SourceIdentity ohne eindeutigen Stream.
- 2F: beide Journalprofile, Intent, create-if-absent und Crash-Recovery vollständig.
- 2G: Producer-Conformance, OBS-ABI/Patchstand und native Coverage freigegeben; keine neue 2F-Pflicht.
- 2H: echte OBS-/Coverage-/E2E-Abnahme; Auto-Cut bleibt weiterhin gesperrt.

## 22. Offene Fragen und späteste Entscheidungspunkte

| Punkt | Status / Default | spätester Punkt | Blockiert |
|---|---|---|---|
| ffprobe-Distribution | offen für Distribution; verbindlicher Default ist explizit konfigurierte externe lokale Binary, kein Download/Bundling | vor Distribution | nicht 2A/2B-Kern; Binaryvertrauen ist bereits entschieden |
| konkrete ffprobe-Testversion | muss als Versionsmatrixrevision im 2B-Gate anhand Goldens festgeschrieben werden | vor Beginn 2B | 2B, nicht 2A |
| OBS-SDK-/Patchstand | offen; unbekannte Version fail closed | spätestens 2G vor 2H | 2H |
| Receipt-Mindestvertrag | in Kapitel 4/5/17 vollständig entschieden | vor 2F erfüllt | nichts offen |
| Persistente 2A-Schemas | Matrix und gemeinsame Regeln entschieden; konkrete JSON-Schemas werden in 2A umgesetzt | vor/innerhalb 2A nach diesem Vertrag | keine Architekturfrage vor 2A |
| Probe-/Lease-Integration | 2B Core, 2C Lease, 2E Integration verbindlich | entschieden | nichts |
| Streamambiguität | fail closed oder gebundene Assignment 1.0 | entschieden vor 2B | nichts |
| Commit-/Recoverystrategie | Strategie B, Sidecar als Commit/Source of Truth | entschieden vor 2F | nichts |
| native Coverage/Toolpatch | Ziele/Toolfamilie entschieden; exakte Toolpatchfreigabe in 2G | spätestens 2G | 2H |

Keine blockierende offene Frage vor Paket 2A.

## 23. Normative Konsistenzregeln

1. Journal 1.0 ist ohne Receipt eigenständig finalisierbar; Bundle 1.0 ist ein separater optionaler Eingang.
2. Keine persistente JSON-Datei ist unversioniert. Interne Laufzeittypen sind nicht persistent.
3. Probe Core besitzt keine Lease und erklärt keine Finalität; nur 2E verbindet 2B und 2C.
4. Mehrdeutige Streams werden nie nach kleinstem Index oder anderer stiller Heuristik gewählt.
5. `probing` ist in Source- und Finalizerautomaten definiert.
6. Byteidentische Retries setzen dieselbe persistierte `FinalizationIntent` voraus.
7. Das Ownershipobjekt wird während Share-0-Besitz nie ersetzt; Diagnose ist separat.
8. Ein unveränderliches Finalartefakt verwendet niemals `MOVEFILE_REPLACE_EXISTING`.
9. Mehrere Datei-Renames sind keine atomare Gruppe. Sidecar 1.1 allein ist Commitpunkt und fachliche Source of Truth.
10. Receipt/State dürfen nach Commit fehlen und werden nach vollständiger Prüfung rekonstruiert.
11. Unbekannte Win32-/Schema-/Bundle-/Binaryversionen enden fail closed und behalten ihren Primärcode.
12. Paket 2G darf keine für 2F bereits erforderliche Sicherheitspflicht nachträglich einführen.

## 24. Expliziter Nicht-Scope und Schluss-Gate

Dieser Plan implementiert weder Produktiv- noch Testcode, installiert keine Abhängigkeit und beginnt kein Paket. Nicht geplant oder freigegeben sind Transkription, Analyse, EDL, Timeline-Mapping, Rendering, Overlays/Sound, UI, Cloud/Netzwerk, Medienänderung oder Plugincode vor 2H. Phase-1-Dateien, v0.2, Planning Brief, Manifest, `pyproject.toml` und `uv.lock` bleiben unverändert.

Der Plan ist erst nach erneutem Read-only-Audit freigabefähig. Paket 2A darf erst nach separater ausdrücklicher Freigabe beginnen. Die Architektur erklärt v0.3 nach der vorliegenden Reparatur lediglich als bereit für diesen Reaudit, nicht als implementierungsfreigegeben.
