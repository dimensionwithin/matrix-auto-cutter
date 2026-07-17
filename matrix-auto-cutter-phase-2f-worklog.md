# Matrix Auto Cutter — Paket 2F Implementierungs- und Auditworklog

Status: nicht normativ; ausschließlich Implementierungs- und Auditevidence für Paket 2F  
Baseline: `94a90bed3c07dc82516682d42d9933d8c42313e0`  
Präzedenz: Planning Brief v0.5, Architektur v0.2, Manifest v0.5, Phase-1-Baseline,
Architektur v0.3, Stream Selection Contract 1.0 und die Paketcommits 2B bis 2E.

## Entscheidungen vor Produktivänderungen

### 1. Tatsächliche Phase-1-Journal- und Sidecar-Schnittstellen

`matrix_auto_cutter.journal.validate_journal()` ist der einzige Journal-1.0-Validator. Er
validiert die geschlossenen Recordmodelle, Header, lückenlose Sequenz, genau einen letzten
`stopped_unfinalized`-Stop, Output-/Splitfehler, Event-ID-Eindeutigkeit, Pfadwechsel, monotone
Clock-/Counterwerte und Pause/Resume. Der 2F-Loader begrenzt und dekodiert NDJSON und übergibt
danach ausschließlich Mappings an diesen Validator; er implementiert keinen zweiten
Journalvalidator.

`matrix_auto_cutter.sidecar.ObsEventSidecar` ist das unveränderte Sidecar-1.1-Modell.
`validate_sidecar()` ist der vollständige Consumer-Validator gegen die unveränderte
`SourceIdentity`. Kanonische Bytes stammen ausschließlich aus `CanonicalModel.model_dump_json()`
plus LF. Clockabbildung verwendet ausschließlich die Phase-1-Funktionen aus `calibration.py`;
Pause-, Pairing- und Protectionsemantik werden durch die unveränderten Modelle,
`validate_sidecar()` und `materialize_protection()` geprüft. 2F konstruiert diese vorhandenen
Modelle, führt nach Konstruktion beide Phase-1-Validatoren aus und erzeugt weder neue
Sidecarfelder noch einen alternativen Serializer.

### 2. Tatsächliche `ConfirmedSource`-Autoritätsprüfung

2E bindet die exakte Objektidentität über eine private `WeakKeyDictionary`-Issuerregistry.
`ConfirmedSource.authorized`/`require_authorized()` prüfen Registrytoken, unveränderte
SourceIdentity/Evidence und die noch offene `CloseGateLease`. Die Lease besitzt bereits Project-,
Path- und Source/File-ID-Ownership und eine private `_run_lease_usage()`-Session; `close()` wartet
auf aktive Usages. Eine punktuelle `require_authorized()`-Prüfung verhindert jedoch noch nicht
eine Invalidierung zwischen Prüfung und Commit. Kleinste Ergänzung: eine private 2E-Usagefunktion,
die unter der bestehenden Issuerregistry eine private, handlefreie Lease-Usage hält. Kein
Rohhandle, keine neue Lease, keine Persistenz und keine Autorität nach Close/Invalidierung.

### 3. Tatsächliche 2A-Atomik-, Lock- und Pfadschnittstellen

2A stellt `ValidatedPath`, handlebasierte Reparse-/NTFS-Prüfung, bounded `secure_read_file()`,
Project-/Path-Ownership, `CancellationToken.begin_irreversible_commit()`, create-if-absent-I und
Revision-CAS-R bereit. Der native Port implementiert `CREATE_NEW`, partielle Write-Schleifen,
`FlushFileBuffers`, `MoveFileExW(..., MOVEFILE_WRITE_THROUGH)` ohne Replace sowie separat
`ReplaceFileW` für R-Artefakte. 2C hält zusätzlich den Source/File-ID-Lock in der Lease.

2A exponiert an der Baseline noch keinen reservierten Target-Lock, keine create-only externe
Zielrolle und kein generisches R-CAS für `finalizer_state`. Engste kompatible Ergänzung:

- `TargetLockLease`/`acquire_target_lock()` im bestehenden Ownershipsystem und separatem
  `ownership/targets`-Namespace;
- eine create-only externe Zielpfadrolle, ausschließlich sicher aus demselben validierten
  Sourceverzeichnis ableitbar;
- ein generisches kanonisches Revision-CAS für ausdrücklich R-markierte Projektartefakte;
- ein create-if-absent-Publishprimitiv für eine callergebundene Same-Directory-Tempdatei.

Bestehende 2A-Semantik und APIs bleiben unverändert; die Ergänzungen werden durch 2F und enge
2A-Regressionen belegt.

### 4. Exakter Sidecarzielpfadvertrag

Für die authentisch bestätigte MP4 liefert ausschließlich Phase 1
`expected_sidecar_path(source_path)` den Namen `<stem>.obs-events.json`. Ziel und Temp liegen im
selben validierten Sourceverzeichnis. Temp ist exakt
`.<stem>.obs-events.json.tmp.<finalizer-run-id>`. Beide werden als create-only externe Ziele aus
der bestätigten Source abgeleitet; freie Callerpfade, UNC, Devicepfade, ADS, Reparse Points und
andere Volumes sind ausgeschlossen. Ziel-File-ID darf nie der Source-File-ID entsprechen;
vorhandene Ziele benötigen regulären NTFS-Typ und genau einen Link.

### 5. Journalprofil-Erkennung

Es gibt keine Erkennung und kein Raten. Die öffentliche Request-Enum akzeptiert exakt
`legacy_journal_1_0` oder `phase2_journal_bundle_1_0`. Legacy lädt nur das unveränderte Journal.
Bundle lädt zusätzlich die drei explizit angegebenen Pflichtartefakte und validiert Versionen,
IDs, Größen und Digests. Jeder Bundlefehler beendet genau diese Operation; ein Legacylauf ist nur
eine neue explizite Operation.

### 6. `FinalizationIntent`-Feld- und Digestentscheidung

Intent 1.0 ist ein striktes `CanonicalModel`, kanonisch plus LF, immutable und create-if-absent.
Es bindet alle geforderten Projekt-, Journal-/Bundle-, SourceIdentity-/Evidence-, Probe-/Hash-/
Assignment-, Versions-, Clock-/Pairing-/Protection-/Serialisierungs-, Ziel- und Zeitwerte. Der
Digest `finalization_key` ist SHA-256 über
`matrix-auto-cutter/finalization-intent/1.0`, NUL und die kanonische Payload ohne das
`finalization_key`-Feld. Die einmaligen UUIDv4-Werte für Zielgeneration und synthetisches
Stop-Event sowie `finalized_at` werden vor dem Intent gewählt und darin gebunden. `intent_id` ist
der `finalization_key`; der separate Intentdigest ist SHA-256 über die vollständigen kanonischen
Intentbytes.

Für das Bundlemanifest wird ein selbstreferenzfreier Manifestdigest analog über die kanonische
Manifestpayload ohne das Feld `bundle_manifest_digest` geprüft. Dadurch sind Manifestdigest und
alle enthaltenen Komponentendigestbindungen gleichzeitig überprüfbar, ohne einen zyklischen
Digestvertrag zu behaupten.

### 7. Finalizer-State-Persistenz

Der Laufzeitautomat enthält exakt die normativen Zustände und eine geschlossene Übergangsmatrix.
`finalizer_state` 1.0 ist Diagnose, maximal 2 MiB, kanonisch, strikt und R mit monotoner Revision.
Writes erfolgen unter dem bereits in der authentischen Source-Usage gehaltenen Project Lock über
2A-First-Publish beziehungsweise vollständiges Revision-CAS. Ungültiger/konfligierender State
wird nicht vertraut und kann niemals den Sidecarstatus ändern. Vor Intent verfügbare Bindungen
werden ausdrücklich als `not_available` markiert, nicht erfunden.

### 8. Sidecar-Commitgrenze

Einziger fachlicher Commit ist der erfolgreiche atomare No-Replace-Rename der vollständig
geschriebenen und geflushten Sidecartemp. Unmittelbar davor werden Target Lock, authentische
Source-Usage, Lease-Recheck, Intent und Zielbindung erneut geprüft und Cancellation gegen den
Commit linearisiert. Nach jedem Renameausgang wird das Ziel bounded geöffnet und vollständig mit
Phase 1 sowie Intent/Recording-ID/SourceIdentity/kanonischen Sollbytes validiert. Intent, Temp,
Receipt und State sind niemals Commit.

### 9. Receipt-/State-Rekonstruktion

`finalization_receipt` 1.0 wird ausschließlich nach sichtbar vollständig validiertem Sidecar als
immutable create-if-absent publiziert. Seine Bytes sind deterministisch aus Sidecar, Intent und
SourceIdentity ableitbar. Recovery erkennt Commit allein am Sidecar. Bei gültigem Sidecar werden
fehlendes Receipt und State rekonstruiert; Konflikte bleiben unverändert und werden getrennt als
Evidence-/Recoverykonflikt gemeldet. Ein gültiges Sidecar ohne Intent bleibt Phase-1-gültig;
zusätzliche Phase-2-Provenienz lautet dann `not_reconstructable`.

### 10. Crashpunktmatrix

Vor Intent: kein Commit, neuer Lauf möglich. Nach Intent/vor Temp: gleiche Intent wiederverwenden.
Während Temp und nach Flush: eigene Temp nie lesen oder promoten; sicher löschen/reportieren und
Bytes neu konstruieren. Unmittelbar vor Commit: normale Zielrevalidierung und No-Replace-Publish.
Unmittelbar nach Commit sowie vor Receipt/State: Sidecar entscheidet; Receipt/State werden
rekonstruiert. Nach State: alle Artefakte werden kreuzgeprüft, Sidecar bleibt entscheidend.

### 11. Cancellation-/Commitlinearisation

Alle geforderten Prüfstellen verwenden den monotonen 2A-Token. Die private ConfirmedSource-Usage
hält Lease und die ersten drei Ownershipstufen. Direkt vor `MoveFileExW` linearisiert genau ein
Commitpermit innerhalb dieser Usage gegen Cancel und Lease-Close. Gewinnt Cancel, wird nicht
publiziert. Beginnt Rename zuerst, wird sein Ausgang und anschließend stets das Ziel vollständig
ausgewertet. Später Cancel widerruft kein Sidecar; Receipt/State dürfen dann fehlen.

### 12. Minimal erforderliche Baselineschnittstellenergänzungen

Phase 1: keine Dateiänderung. 2B/2C/2D: keine fachliche Änderung. 2A erhält nur Target-Lock,
create-only Zielpfad und generische I/R-Publishexposition auf bestehenden Portoperationen. 2E
erhält nur die private authentifizierte Usage-Session. Jede Ergänzung bleibt backward-kompatibel,
ändert keine Confirmationsemantik und wird mit den bestehenden Paketregressionen getestet.

## Implementierungs- und Auditevidence nach Abschluss

Implementiert sind die zwei expliziten Profile, bounded Loader, strikte Bundlemodelle,
Issuer-/Lease-/Port-gebundene `ConfirmedSource`-Usage, Target-Lock, Intent, State-Machine,
Sidecarkonstruktion über die unveränderten Phase-1-Modelle und -Funktionen, No-Replace-Publish,
Receipt, Revision-CAS-State sowie explizite Recovery. Persistierte Intents binden auch Probe-,
Hash- und Assignmentartefakte an den aktuellen 2E-Nachweis. Bundle-Referenzen sind sichere
Blattnamen und Manifestkomponententypen sind positionsgebunden. SourceIdentity-Digests in Intent
und Receipt sind selbstvalidierend.

Crash-Recovery verwirft ausschließlich die exakt durch Run-ID gebundene eigene Tempdatei und
konstruiert die Sidecarbytes neu; fremde Temps werden nicht gelesen, verschoben oder gelöscht.
Cancellation wird vor Temp-Erzeugung, zwischen partiellen Writes, nach vollständigem Write, nach
Flush und unmittelbar an der Lease-gebundenen Commitlinearisation ausgewertet. Der Atomikpfad
verwendet ausschließlich `MoveFileExW` ohne Replace; der vorhandene kanonische Serializer wird
für Sidecarbytes wiederverwendet.

Der Abschlusslauf ergab: 2F-Kern 115 und die reale 2F-Windows-Integration drei Tests bestanden;
Phase 1 196, 2A 125, 2B 494, 2C 101 mit einem engen plattformbedingten
Delete-Pending-Skip, 2D 80 und 2E 156 bestanden. Die Gesamtsuite ergab 1271 bestanden, einen
engen Skip und 100,00 Prozent Statements/Branches über 9266 Statements und 2806 Branches. Der
neue 2F-Kern erreichte separat 1253/1253 Statements und 356/356 Branches. `mypy --strict`, Ruff
Check und Ruff Format Check sind grün. Der vollständige Testlauf hinterließ keine laufenden
Python-/ffmpeg-/ffprobe-Prozesse und die auftragseigenen Test-/Coverageartefakte wurden entfernt.
