# Paket 2E – Arbeitsprotokoll vor Implementierung

Stand der Schnittstellenanalyse: Baseline `c16f97a117cb8b5ce2ca9de86497058f5534e848`.
Dieses Protokoll hält die vor dem ersten Produktcode festgelegten
Integrationsentscheidungen fest.

## Autoritative Probevalidierung

`run_probe()` bleibt die einzige Probeoperation. Ein `ProbeOk` wird nur verwendet,
wenn Probe-Vertrag, Binaryobjekt, Sourcepfad, erwarteter Snapshotkey, Vor-/Nachsnapshot,
vollständige Stream-Evidence und deren Digest zur unabhängig gebundenen aktuellen
Lease-Probe passen. Die enthaltene `FinalizedStreamSelection` wird unmittelbar vor
Verwendung mit `selection_semantically_matches()` gegen die unabhängig gebundenen
Streams revalidiert. Dadurch wird ausschließlich die produktive 2B-Selektion erneut
ausgeführt; 2E besitzt keinen zweiten Auswahlalgorithmus.

Ein `ProbeFailed` bleibt mit seinem ursprünglichen `ProbeError` primär. Nur die von 2B
gelieferte `ProbeDiagnosticProfile` für einen vollständig geparsten, aber nicht
automatisch auswählbaren Ausgang kann ein `media_probe` erzeugen. Sie bewahrt das
bounded normalisierte Profil, alle kanonischen Streams, den Evidence-Digest, Binary-,
Source- und Snapshotbindungen sowie Fehlercode, Phase und Detailcode. Ohne dieses
Profil werden keine erfundenen Probe-Evidence und kein Auswahl- oder Hashfortschritt
erzeugt.

## Lease, S3 und S5

Eine autorisierte Lease wird über die private 2C-Registry geprüft. Die
Integrationsnutzung validiert die weiterhin offenen Project-, Path-, File-ID- und
Sourcehandle-Ownerships und erhöht einen eigenen aktiven Nutzungszähler. `close()`
wartet auf dessen Freigabe. S3 und S5 werden innerhalb dieser Nutzung durch die
vorhandene authentische Recheckoperation über exakt den von 2C gehaltenen Sourcehandle
erhoben und jeweils mit S0 als `SameInstanceUnchanged` verglichen.

Vor Probe und vor Identity-Commit wird der ursprünglich gebundene Sourcepfad über die
2A-Pfad- und Snapshotgrenze erneut komponenten- und handlebasiert ohne Reparse-Folgen
geprüft. Volume-ID und FILE_ID_128 müssen exakt zur Lease passen; ein Hardlinkalias
wird weder als neuer Callerpfad angenommen noch in die Sourcebindung geschrieben.

## Hash, S4 und Receipt

2E ruft ausschließlich `hash_lease_source()` auf. Die private 2C-Integrationsnutzung
belegt nicht den exklusiven I/O-Slot, sodass der unveränderte 2D-Hasher ihn innerhalb
derselben offenen Lease erwerben kann. 2E akzeptiert nur ein durch
`receipt_from_completed()` authentifiziertes `HashCompleted`. Receipt, Lease-ID,
Epoche, Projekt/Run, S0, S4, Volume-/File-ID, Bytezahl, EOF-belegter 2D-Erfolg,
Digest und Snapshotvergleich werden vollständig gekreuzprüft. Das Receipt wird nur
über `publish_hash_receipt()` publiziert und anschließend als identische Receipt-1.0-
Evidence gebunden.

## Phase-1-SourceIdentity

Der unveränderte zehnfeldrige Phase-1-Typ wird direkt importiert. Größe und SHA-256
kommen aus S0 beziehungsweise authentischem `HashCompleted`; Dateiname und Binding
kommen aus der validierten Sourcebindung; Dauer, Framezahl, 60/1-FPS sowie Video- und
Audiostart werden aus den autoritativ gebundenen aktuellen Streams und dem aktuellen
Profil exakt und ohne Gleitkommarundung abgeleitet. Konstruktion erfolgt über den
bestehenden strikten Pydantic-Validator. Kanonische Bytes und domain-separierter
Digest werden deterministisch erzeugt und durch erneute Phase-1-Validierung
wertgleich verglichen. Vor S5 und letzter Pfadrevalidierung bleibt dieser Wert intern
und nicht autoritativ.

## Minimale fehlende Schnittstelle

2B benötigt keine Änderung: Erfolgs- und Diagnose-Evidence sowie die semantische
Revalidierung sind read-only verfügbar. 2D benötigt keine Änderung: Authentizitäts-
prüfung, S4, Receiptmodell und Publisher sind vollständig komponierbar.

2C benötigt genau eine private, backward-kompatible Integrationsnutzung. Der bisherige
`io_active`-Slot kann nicht die gesamte 2E-Operation halten, weil der 2D-Hasher diesen
Slot selbst exklusiv benötigt. Die Ergänzung führt daher nur `active_usages` und eine
handlelose private Session ein. Sie authentifiziert Lease und Ownerships, blockiert
`close()` über Probe, S3, Hash/S4, S5 und Identity-Commit und delegiert Rechecks an die
bestehende Leaseautorität. Sie verändert weder Sharematrix noch Gate-, Recheck- oder
Hashsemantik und gibt keinen Rohhandle frei.
