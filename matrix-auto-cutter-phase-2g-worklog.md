# Matrix Auto Cutter — Paket 2G Vorab-, Implementierungs- und Auditevidence

Status: nicht normativ; Queue-Overflow-Lücke vertraglich repariert, Paket 2G nicht implementiert
Baseline: `dc30097a50b33e0ba1e860b26ce77fd2abfee6a7`
Parent: `94a90bed3c07dc82516682d42d9933d8c42313e0`
Scope: ausschließlich Paket-2G-Entscheidungs-, Werkzeug- und Auditevidence

Eingangsevidence dieses separaten Vertragsauftrags: ursprüngliche Dateigröße 12.251 Bytes,
ursprünglicher SHA-256
`602047DABB4BC51432CFD1BBD5C7E2A6658951202DA47F46A51E2174D20E9453`. Der ursprüngliche
Blockerbefund, Abbruchzeitpunkt und damalige Nichtimplementierung bleiben in Abschnitt 15
vollständig als historische Auditevidence erhalten.

Dieses Dokument ersetzt weder Planning Brief, Architektur, Assetmanifest noch einen festen
Vertrag. Es führt keine neue Producer- oder 2F-Pflicht ein. Wegen der in Abschnitt 15
festgehaltenen Vertragslücke wurden kein Produktivcode, keine Goldens, keine native
Builddefinition, kein ABI-Header und keine Coveragekonfiguration angelegt.

## 1. Vom 2F-Consumer akzeptierte Journalbytes

Der feste Loader akzeptiert ausschließlich das vom Caller explizit gewählte Profil
`legacy_journal_1_0` oder `phase2_journal_bundle_1_0`; es gibt keine Profilerkennung und keinen
stillen Bundle-zu-Legacy-Fallback.

Das Rohjournal ist eine Bytefolge aus mindestens einem Header und genau einem letzten Stoprecord.
Jeder Record ist ein einzelnes kanonisches JSON-Objekt, unmittelbar gefolgt von `LF` (`0x0a`).
Die Datei endet mit diesem `LF`. Verboten sind BOM, CRLF, leere Zeilen, ungültiges UTF-8,
doppelte JSON-Schlüssel, nicht kanonische Schlüssel-/Wertbytes und eine partielle letzte Zeile.
Die Kanonik ist die bestehende Phase-1-Kanonik: UTF-8 ohne BOM, Unicode nicht ASCII-escaped,
Objektschlüssel lexikografisch sortiert, keine überflüssigen Whitespaces, exakte endliche
Zahlenlexeme, JSON-Boolean und `null` typgetreu. Jede gelesene Zeile muss bytegleich zur erneuten
Serialisierung durch `_json_mapping_payload()` sein.

Grenzen: höchstens 256 MiB Journalbytes, höchstens 64 KiB je Zeile ohne LF und höchstens
1.000.000 Records. Das Original wird nur gelesen und weder repariert, migriert noch verändert.

Geschlossene Recordtypen und Felder des unveränderten Journal-1.0-Modells:

- `header`: gemeinsame Artefakt-/Versionsfelder, `record_type`, `sequence=0`,
  `recording_session_id`, `lifecycle_status="recording"`, `producer`, `clock`, `capabilities`,
  `initial_output_path`;
- `event`: gemeinsame Felder, `record_type`, `sequence`, `event_id`, `event_type`,
  `monotonic_ns`, optionaler `output_frame_count`, `recording_paused` sowie optional
  `source_uuid`, `pair_id`, `label`;
- `calibration_sample`: gemeinsame Felder, `record_type`, `sequence`, `monotonic_ns`,
  `output_frame_count`, `recording_paused=false`;
- `pause` und `resume`: gemeinsame Felder, `record_type`, `sequence`, `event_id`,
  `monotonic_ns`, `output_frame_count` und der jeweils feste Pausenstatus;
- `path_snapshot`: gemeinsame Felder, `record_type`, `sequence`, `monotonic_ns`,
  `output_frame_count`, `recording_paused`, `output_path`;
- `split_status`: gemeinsame Felder, `record_type`, `sequence`, `monotonic_ns`,
  `output_frame_count`, `recording_paused`, `split_requested`, `file_splitting_detected`;
- `output_error`: gemeinsame Felder, `record_type`, `sequence`, `monotonic_ns`,
  `output_frame_count`, `recording_paused`, `output_result="failure"`, `diagnostic`;
- `recovery`: gemeinsame Felder, `record_type`, `sequence`,
  `lifecycle_status` aus `aborted|finalization_failed`, `diagnostic`;
- `stop`: gemeinsame Felder, `record_type`, `sequence`,
  `lifecycle_status="stopped_unfinalized"`, `monotonic_ns`, `output_frame_count`,
  `recording_paused`, `last_recording_path`, `output_result`,
  `file_splitting_detected`.

`event_type` ist geschlossen auf `recording_started`, `scene_changed`, `intro_started`,
`intro_ended`, `outro_started`, `outro_ended`, `stinger_started`, `stinger_ended` und
`manual_protection`. Unbekannte Felder, Typen oder Versionen werden abgewiesen.

## 2. Exakte Session-, Integrity- und Bundlefelder

Alle drei JSON-Artefakte sind strikt, kanonisch, UTF-8 ohne BOM, besitzen genau ein finales LF,
weisen unbekannte Felder ab und sind vor Materialisierung auf 1 MiB begrenzt.

`journal-session.json`, `recording_journal_session`, Schema 1.0:

- `artifact_type`, `schema_version`, `recording_session_id`, `plugin_run_id`;
- `producer_name="matrix-auto-cutter-obs-producer"`, `producer_version`, `obs_version`;
- `journal_schema_version="1.0"`.

`journal-integrity.json`, `recording_journal_integrity`, Schema 1.0:

- `artifact_type`, `schema_version`, `recording_session_id`, `plugin_run_id`;
- `journal_reference` als sicherer Blattname;
- `journal_size_bytes`, `journal_sha256`, `session_receipt_digest`;
- `journal_schema_version="1.0"`.

`journal-bundle.json`, `recording_journal_bundle`, Bundleschema 1.0:

- `artifact_type`, `bundle_schema_version`, `recording_session_id`, `plugin_run_id`;
- `producer_version`, `obs_version`;
- positionsgebundene Komponenten `journal`, `session_receipt`, `integrity_receipt` mit genau
  `artifact_type`, `schema_version="1.0"`, sicherem Blattnamen `safe_reference`, `size_bytes`,
  `sha256`;
- `bundle_manifest_digest` als SHA-256 über
  `matrix-journal-bundle/1.0`, ein NUL-Byte und die kanonische Manifestpayload ohne dieses
  Digestfeld.

Der Loader kreuzbindet Recording-ID, Plugin-Run-ID, Producer-/OBS-Version, Journalheader,
Komponentennamen, Größen und Digests. Integrity bindet Journaldigest/-größe und Sessiondigest.

## 3. Kanonische Serialisierungsregeln

Kanonische JSON-Artefakte verwenden den bestehenden `CanonicalModel`-Serializer und
`canonical_bytes(model)`: sortierte Schlüssel, kompaktes JSON, exakte endliche Zahlen,
kleingeschriebene kanonische UUIDv4-Texte, UTF-8 ohne BOM und genau ein finales LF. Journalzeilen
verwenden dieselbe kompakte JSON-Kanonik und jeweils genau ein LF. Plattform-Newlines, Locale,
Zeitzone und lokale Codepage dürfen die Bytes nicht beeinflussen. Goldens dürfen nur feste
Testwerte enthalten.

## 4. Pflichtflushpunkte

Die höherrangige Architektur verlangt für jeden vollständig publizierten Journalrecord das
Leeren der C-Laufzeit- und Windows-Dateipuffer. Session Receipt wird create-if-absent vor dem
Journalstart publiziert. Integrity Receipt darf erst nach erfolgreichem Stop und vollständigem
Journaldigest create-if-absent publiziert werden. Das Bundlemanifest darf erst nach allen
vollständigen und kreuzkonsistenten Pflichtteilen create-if-absent publiziert werden. Der
2F-Consumer akzeptiert nur vollständig sichtbare kanonische Dateien; Tempdateien oder
Diagnoselogs sind keine Trustquelle.

## 5. Crash- und Partial-Write-Semantik

Eine Journaldatei ohne finales LF, mit partieller Zeile oder ohne genau einen erfolgreichen
letzten Stop ist kein gültiger Legacyeingang. Ein Crash vor Integrity Receipt lässt höchstens das
Journal separat im expliziten Legacyprofil prüfen. Ein Crash nach Integrity Receipt, aber vor
Bundlemanifest ergibt kein gültiges Bundleprofil. Partielle oder nicht kanonische Receipts und
Manifeste werden abgewiesen. Es gibt keine Reparatur, Temp-Promotion oder erfundene Provenienz.
Ein Bundlemanifest darf nach Producerfehler oder fehlender Vollständigkeit nicht entstehen.

## 6. UUID-, Recording-ID-, Plugin-Run-ID- und Counterregeln

Persistierte IDs sind kanonische kleingeschriebene UUIDv4. Feste UUIDs sind nur für Tests und
Goldens zulässig; der spätere Produktpfad muss kryptographisch geeignete, nicht deterministische
UUIDv4 erzeugen. `recording_session_id` bindet Header, beide Receipts und Manifest.
`plugin_run_id` bindet Session, Integrity und Manifest und ist im Legacyprofil ausdrücklich
`not_available`.

`sequence` beginnt beim Header mit 0 und entspricht lückenlos dem Recordindex; nach Pause/Resume
gibt es keinen Reset. Event-IDs aus Event/Pause/Resume sind eindeutig. QPC-Werte dürfen nicht
rückwärts laufen. Counter dürfen außerhalb einer Pause nicht rückwärts laufen; während einer
Pause sind höchstens zwei Frames Bewegung erlaubt. Counterüberlauf darf später nicht wrappen,
ist jedoch noch als Producerpfad zu spezifizieren und fail closed zu behandeln.

## 7. Geplante positive und negative Goldens

Geplant waren sämtliche im Paket-2G-Auftrag aufgezählten positiven und negativen Fälle,
einschließlich Legacy-Minimum, vollständigem Eventjournal, Pause/Resume, allen geschlossenen
Szenen-/Intro-/Outro-/Stingertypen, Unicode, drei Receipts/Manifest, vollständigem Bundle,
ignorierten Diagnoselogs sowie Encoding-, Kanonik-, Sequenz-, Lifecycle-, Digest-, Größen-,
ID-, Versions-, Queue-, Disk- und Crashfehlern. Jeder Fall sollte feste Bytes, Größe, SHA-256,
Profil und erwarteten stabilen 2F-Ausgang erhalten. Wegen Abschnitt 15 wurde kein Golden erzeugt.

## 8. Geplante Cross-Language-Grenze

Die beabsichtigte Grenze war ein OBS-unabhängiges C++20-EXE-Harness mit festen semantischen
Testwerten und einer kleinen C-ABI. Es sollte Journalzeilen und die drei Bundleartefakte erzeugen,
seine Bytes und Digests gegen feste Repositorygoldens vergleichen und dieselben Dateien über
einen Python-Test durch den bestehenden 2F-Loader prüfen. Keine OBS-Header, OBS-Symbole,
Netzwerknutzung oder Drittanbieter-JSON-Bibliothek waren vorgesehen.

## 9. Geplanter C-ABI-Vertrag

Vorgesehen waren ausschließlich feste C-Integerbreiten, Windows-x64, feste Calling Convention,
UTF-8-Bytebereiche mit Länge, `struct_size`/ABI-Version, nullte Reserved-Felder, modulgebundene
Ownership und exception-freie Resultatcodes für Initialisierung, Shutdown, immutable Snapshot,
Status, Diagnose, Stop/Cancellation und spätere Finalizerübergabe. OBS-Typen sollten vollständig
hinter dem späteren 2H-Adapter bleiben. Ein exaktes ABI wurde wegen Abschnitt 15 bewusst nicht
erfunden oder festgeschrieben.

## 10. Visual Studio, MSVC und Windows SDK

Nicht qualifiziert. Die verbindliche Vertragsprüfung in Abschnitt 15 blockierte vor
Toolchain-Pinning und vor jeder nativen Buildänderung. Es wurde keine Toolinstallation,
Systemänderung oder Elevation vorgenommen.

## 11. `Microsoft.CodeCoverage.Console`

Nicht qualifiziert und nicht ausgeführt. Kein Coverageartefakt wurde erzeugt.

## 12. Geplanter Branch-Coverage-Selbsttest

Geplant war ein separates natives Goldenmodul mit exakt bekannten Statements, Branches und
Functions, je einem absichtlich genommenen und nicht genommenen Zweig, eigenem Modulfilter sowie
parsebarem Cobertura und HTML. Es wurde wegen Abschnitt 15 nicht implementiert oder ausgeführt.

## 13. OpenCppCoverage

Nicht qualifiziert und nicht ausgeführt. Es wurde nichts installiert.

## 14. OBS-SDK-/Patchstand

Nicht freigegeben. Architektur v0.2 nennt OBS Studio 32.x und verwendet 32.2.0 nur in
repräsentativen Daten. Architektur v0.3 erklärt den exakten OBS-SDK-/Patchstand ausdrücklich bis
2G als offen und unbekannte Versionen als fail closed. Vor dem Blocker wurde kein lokaler,
offiziell belegbarer SDK-/Header-/Commitstand qualifiziert. Paket 2H bleibt gesperrt.

## 15. Blockierende Producerentscheidung

Die priorisierten Quellen verlangen für 2G/2H eine bounded Queue und testen `Queue voll`, legen
aber weder Kapazität noch den exakten Overflow-Zustandsübergang fest. Der 2F-Consumer beweist nur
die Auswirkungen auf Artefakte: Ein `output_error`- oder Recoveryrecord, ein nicht erfolgreicher
Stop, eine unvollständige Sequenz oder ein fehlender erfolgreicher letzter Stop ist ungültig; ein
vollständiges Bundle darf bei Producerfehler nicht entstehen. Er kann und darf nicht entscheiden,
ob der spätere Producer bei Queuevoll:

1. den aktuellen Recordinglauf irreversibel als `producer_failed` markiert und den Writer
   kontrolliert auslaufen lässt; oder
2. im Callback einen synchronen fail-closed Übergang linearisiert.

Beide Implementierungen können dieselben vom 2F-Consumer sichtbaren ungültigen Artefakte erzeugen,
haben aber unterschiedliche Callback-, Threading-, Backpressure-, Shutdown- und ABI-Semantik.
Eine Auswahl wäre daher eine neue Producerpflicht und keine Ableitung aus dem festen
2F-Mindestvertrag.

Nach der ausdrücklichen Paket-2G-Regel ist dies eine blockierende Vertragslücke. Erforderlich ist
eine höherrangig autorisierte, versionierte Entscheidung für genau einen der beiden Übergänge
einschließlich Linearisation, zulässiger Callbacklatenz, terminalem Zustand und Shutdownfolge.
Bis dahin: keine Producerimplementierung, keine ABI-Freigabe, keine Coveragefreigabe, kein Commit
und Paket 2H gesperrt.

## 16. Autorisierte normative Reparatur

Die zuvor offene Entscheidung ist durch
`matrix-auto-cutter-producer-queue-overflow-contract-v1.0.md` ausschließlich für
Queue-Overflow-, Callback- und Writer-Terminalsemantik autorisiert und geschlossen.

Die Entscheidung lautet:

- Callback-Enqueue verwendet genau einen nicht wartenden `try_push`; bewusstes Wartebudget ist
  exakt 0 ms.
- Der erste `full`-Ausgang während `recording_active` versucht im selben Callback genau den
  atomaren Compare-and-Swap
  `recording_active -> producer_failed_queue_overflow`.
- Der erfolgreiche Compare-and-Swap ist der einzige Overflow-Linearisationpunkt und der Zustand
  ist für den Recordinglauf irreversibel terminal.
- Stop und Overflow konkurrieren über denselben atomaren Zustand; der zuerst erfolgreich
  linearisierte Übergang gewinnt.
- Nach Overflow werden nur zuvor angenommene Elemente geordnet kontrolliert abgearbeitet. Es
  entsteht kein erfolgreicher Stoprecord, kein Integrity Receipt und kein Bundlemanifest.
- Ein bereits publiziertes Session Receipt bleibt unverändert. Diagnostik liegt außerhalb des
  Journals und bleibt nicht autoritativ.
- Shutdown ist thread-sicher und idempotent; seine monotone Produktionsdeadline beträgt exakt
  fünf Sekunden. Timeout ist `producer_shutdown_timeout`, terminal und fail closed, ohne
  Ressourcenfreigabe bei möglicherweise aktivem Writer.

Diese Reparatur ergänzt keine Journalfelder oder Recordtypen und ändert weder Phase 1 noch den
Paket-2F-Loader, Bundlevalidator oder dessen Sicherheitsvoraussetzungen. Der ursprüngliche
Paket-2G-Abbruch erfolgte vor jeder Implementierung und bleibt korrekt dokumentiert. Paket 2G
darf erst nach dem separaten Commit dieses Addendums in einem neuen Implementierungsauftrag
wiederaufgenommen werden. Dieses Worklog behauptet weder eine Paket-2G-Implementierung noch eine
ABI-, Coverage-, OBS-SDK- oder Paket-2H-Freigabe.
