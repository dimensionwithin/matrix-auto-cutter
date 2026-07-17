# Matrix Auto Cutter — Producer Queue Overflow Contract 1.0

Status: normatives Paket-2G-Addendum; ausschließlich Queue-Overflow-, Callback- und
Writer-Terminalsemantik; keine Paket-2H-Implementierungsfreigabe
Stand: 2026-07-17

## 1. Geltung, Rang und normative Sprache

Dieses Addendum schließt ausschließlich die bislang offene Paket-2G-Semantik für eine volle
Producerqueue. Es implementiert nichts und gibt weder Paket 2G noch Paket 2H frei.

Bei Widerspruch gilt:

1. `matrix-auto-cutter-planning-brief-v0.5.md`;
2. `matrix-auto-cutter-architecture-plan-v0.2.md`;
3. `matrix-auto-cutter-asset-manifest-v0.5.json`;
4. Phase-1-Baseline `87fbfd19a50879abefec21af75d37405f6349da5`;
5. `matrix-auto-cutter-architecture-plan-v0.3.md`;
6. dieses Addendum ausschließlich für die zuvor offene Paket-2G-Queue-Overflow-Semantik.

Der Stream-Selection-Vertrag 1.0 bleibt für seinen eigenen Scope separat normativ. **MUSS**,
**DARF NICHT**, **SOLL** und **DARF** sind normativ.

Dieses Addendum DARF den Paket-2F-Consumervertrag nicht verändern, keine Journalfelder und keine
Journalrecordtypen ergänzen, keinen gültigen Producerfehler-Stop erfinden und nach Overflow kein
Bundle erlauben. Journal 1.0, seine Lifecycle- und Stopbedingungen sowie Session-, Integrity- und
Bundlevertrag bleiben unverändert.

## 2. Begriffe und Queueklasse

Die spätere Producerqueue MUSS:

- bounded und thread-sicher sein;
- Callbackproducer und genau einen Journalwriter unterstützen;
- nach erfolgreicher Enqueue-Linearisation geordnet übergeben;
- unbounded dynamisches Wachstum und stillen Recordverlust ausschließen.

Die konkrete Kapazität wird erst im allgemeinen Producer-Conformance-Vertrag gebunden. Dieses
Addendum entscheidet ausschließlich die Semantik des Queueausgangs `full`.

Ein *angenommenes Event* ist ein immutable Event-Snapshot, dessen `try_push` erfolgreich mit
`accepted` linearisiert wurde. Ein abgewiesenes Event ist niemals angenommen.

## 3. Callback-Enqueue

Callbacks verwenden ausschließlich:

```text
try_push(event_snapshot)
```

`try_push` MUSS genau einen eindeutigen linearen Ausgang liefern:

- `accepted`;
- `full`;
- `terminal`;
- `internal_error`.

`try_push` DARF NICHT warten, schlafen, spinnen, einen Retry ausführen, eine Dateisystem- oder
Netzwerkoperation ausführen oder unbounded Diagnose erzeugen.

## 4. Overflow-Linearisation

Der erste `full`-Ausgang während `recording_active` MUSS im selben Callback mittels atomarem
Compare-and-Swap irreversibel überführen:

```text
recording_active -> producer_failed_queue_overflow
```

Der erfolgreiche Compare-and-Swap ist der einzige Overflow-Linearisationpunkt.

Hat ein anderer Thread bereits erfolgreich in einen terminalen oder die Eventannahme schließenden
Zustand gewechselt, bleibt dieser frühere Zustand maßgeblich. Es entsteht kein zweiter
Fehlerübergang und das Event wird nicht angenommen.

Das wegen `full` abgewiesene Event:

- wird nicht enqueued;
- wird nicht später rekonstruiert;
- wird nicht durch ein synthetisches Journalrecord ersetzt;
- darf nicht still verworfen werden, während der Recordinglauf trotzdem als erfolgreich gilt.

## 5. Terminalität

`producer_failed_queue_overflow` ist irreversibel, terminal für den Recordinglauf, nicht
rücksetzbar und innerhalb desselben Laufs nicht retrybar. Der Zustand ist weder Pause noch
normaler Stop und kann kein gültiges Bundle begründen.

Nach diesem Zustand MÜSSEN alle weiteren Eventcallbacks mit `terminal` abgewiesen werden. Es darf
kein neuer Journalrecord mehr angenommen werden. Bereits vor der Overflow-Linearisation
angenommene Elemente bleiben ausschließlich Eigentum der Queue beziehungsweise des Writers und
unterliegen Abschnitt 8.

## 6. Callbacklatenz und bounded Arbeit

### 6.1 Wartebudget

Das producerseitige synchrone Wartebudget im Callback beträgt exakt:

```text
0 ms
```

Damit sind Warten auf Queueplatz, Writer, Flush oder Disk sowie Locks mit möglichem Schlafen,
Retry und Spin-until-success verboten. `0 ms` bezeichnet exakt null bewusstes Warten und keine
unrealistische Garantie gegen OS-Preemption.

### 6.2 Erlaubte Callbackarbeit

Ein Callback DARF ausschließlich:

1. Callbackdaten innerhalb ihrer Lebensdauer in ein bounded immutable Snapshotobjekt kopieren;
2. genau einen `try_push` ausführen;
3. gegebenenfalls genau einen atomaren terminalen Statusübergang versuchen;
4. den Writer über ein nicht blockierendes Signal informieren;
5. einen stabilen Callbackausgang zurückgeben.

Diese Arbeit MUSS bounded sein. Keine OBS-Objektpointer oder fremden Stringpointer dürfen den
Callback verlassen. Keine C++-Exception darf eine C-/OBS-Grenze überschreiten. Journal-
serialisierung und Dateischreibvorgänge im Callback sind verboten.

### 6.3 Bestehendes Unsicherheitsbudget

Dieses Addendum erweitert das bestehende Signal-/Callback-Unsicherheitsbudget von 100 ms nicht.
Vertragsgegenstand sind exakt 0 ms bewusstes Warten, bounded lokale Arbeit und das unveränderte
100-ms-Gesamtbudget für die Eventunsicherheit. Ein Event, dessen gemessene Unsicherheit dieses
bestehende Budget überschreitet, darf nicht als präzises Event ausgegeben werden.

## 7. Gemeinsamer Producerzustand und Stop-vs-Overflow

Der Producer MUSS einen atomaren gemeinsamen Zustand besitzen. Die spätere vollständige State
Machine darf weitere bereits vertragskonforme Zustände besitzen, muss aber mindestens
unterscheiden:

```text
not_started
recording_active
stop_requested
producer_failed_queue_overflow
producer_failed_io
stopped_unfinalized
closed
```

### 7.1 Stoplinearisation

Ein normaler Stop versucht atomar:

```text
recording_active -> stop_requested
```

Nur der erfolgreiche Übergang schließt die Eventannahme und erlaubt dem Writer die geordnete
normale Stopsequenz. Callbacks nach `stop_requested` werden mit `terminal` als nach Stop
abgewiesen und nicht als Queueoverflow klassifiziert.

### 7.2 Konkurrenzregel

Konkurrieren Stop und Overflow, gewinnt der zuerst erfolgreich linearisierte atomare Übergang.
Es gibt keinen gemischten Zustand. Kein Event darf gleichzeitig angenommen und als Overflow
abgewiesen werden.

Gewinnt Overflow zuerst,

```text
recording_active -> producer_failed_queue_overflow
```

scheitert der Stop kontrolliert. Ein erfolgreicher Stoprecord DARF NICHT entstehen.

Gewinnt Stop zuerst,

```text
recording_active -> stop_requested
```

darf ein späterer Callback nicht angenommen werden; er wird jedoch nicht als Queueoverflow
klassifiziert. Ein späterer `full`-Ausgang darf den Lauf nicht nachträglich ungültig machen.

## 8. Writerfolge nach Overflow

Nach linearisiertem Overflow gilt exakt diese Reihenfolge:

1. Der Writer wird nicht blockierend signalisiert.
2. Es werden keine neuen Queueelemente angenommen.
3. Ein bereits begonnener vollständiger Recordwrite darf kontrolliert beendet werden.
4. Alle vor der Overflow-Linearisation erfolgreich angenommenen Queueelemente dürfen in ihrer
   Queueordnung abgearbeitet werden.
5. Jede vollständig geschriebene Journalzeile MUSS einschließlich LF geflusht werden.
6. Partielle Zeilen werden nicht repariert oder promotet.
7. Es wird kein erfolgreicher Stoprecord erzeugt.
8. Es wird kein Integrity Receipt erzeugt.
9. Es wird kein Bundlemanifest erzeugt.
10. Das Journal wird nach Abschluss des kontrollierten Writerpfads geschlossen.
11. Ein bereits publiziertes Session Receipt bleibt unverändert bestehen.
12. Das unvollständige Journal darf unverändert als Diagnose- oder Legacy-Evidence bestehen,
    ist aber ohne erfolgreichen Stop nicht finalisierbar.
13. Diagnostik liegt außerhalb des Journals und ist keine Trustquelle.

Der Writer DARF nach Producerfailure keine erfundenen Records erzeugen. Insbesondere darf er
keinen synthetischen Stop, kein Recovery-, Output-error- oder Overflowrecord erzeugen. Zwar kennt
Journal 1.0 `recovery` und `output_error`, autorisiert aber keinen dieser Typen als Abbildung eines
Queueoverflows. Dieses Addendum ändert diese Semantik nicht.

## 9. Write- und Flushfehler während des Drains

Tritt während des kontrollierten Drains zusätzlich ein Write- oder Flushfehler auf, bleibt der
Recordinglauf terminal fehlgeschlagen. Der zeitlich zuerst linearisierte producerterminale Grund
bleibt primär; zusätzliche Writerfehler werden bounded und sekundär diagnostiziert.

Wurde ein Writerfehler vor dem Queueoverflow terminal linearisiert, bleibt `producer_failed_io`
der primäre Zustand. In jedem Fall sind Integrity Receipt, Bundlemanifest, Reparatur und Retry im
selben Recordinglauf verboten.

## 10. Shutdown

### 10.1 Idempotenz und Reihenfolge

Shutdown MUSS thread-sicher, idempotent und nicht rücksetzbar sein. Er muss:

1. die geschlossene Eventannahme herstellen oder bestätigen;
2. den Writer signalisieren;
3. auf Writer-Acknowledgement und Join warten;
4. eigene Handles erst nach Writerende schließen;
5. Queue- und Snapshotownership erst nach Writerende freigeben.

### 10.2 Deadline

Die Produktionsdeadline für den kontrollierten Writer-Shutdown beträgt:

```text
5 Sekunden
```

Die Deadline MUSS eine monotone Clock verwenden. Tests dürfen Clock und Wait injizieren.

### 10.3 Timeout

Bei Ablauf der Deadline lautet der stabile Ausgang:

```text
producer_shutdown_timeout
```

Der Timeout ist terminal und fail closed. Er erlaubt weder Integrity Receipt noch
Bundlemanifest oder eine erfolgreiche Producerbeendigung. Shared State und Writerressourcen
dürfen nicht zerstört werden, solange der Writer noch aktiv sein kann. Paket 2H darf Plugin-Code
nicht entladen, solange ein Writerthread noch darin ausführt.

Die Diagnose bleibt bounded. Ein zweiter unbounded Wait und Exceptionescape über die C-/OBS-
Grenze sind verboten. Der spätere Paket-2H-Adapter MUSS dieses Verhalten gegen die tatsächlichen
OBS-Unloadgrenzen kreuzvalidieren. Diese Anforderung gibt Paket 2H nicht frei.

## 11. Sichtbare Artefaktfolgen und 2F-Kompatibilität

Nach Queueoverflow sind ausschließlich zulässig:

- ein bereits publiziertes Session Receipt;
- bis zur Fehlergrenze vollständig geschriebene und geflushte Journalzeilen;
- bei zusätzlichem Crash- oder I/O-Fehler eine partielle letzte Journalzeile;
- bounded, nicht autoritative Diagnose.

Nicht zulässig sind:

- ein erfolgreicher `stopped_unfinalized`-Stop;
- Integrity Receipt;
- Bundlemanifest;
- eine Behauptung gültiger Bundlevollständigkeit;
- stiller Erfolg, automatisches Journalrepair oder synthetische Provenienz.

Der unveränderte Phase-1- und Paket-2F-Consumervertrag bleibt maßgeblich. Ein solches Journal ist
ohne erfolgreichen letzten Stop nicht finalisierbar. Paket 2F muss und darf für dieses Addendum
nicht geändert werden.

## 12. Stabile producerinterne Ergebnisse

Der spätere Producer-/ABI-Vertrag MUSS mindestens diese geschlossenen Gründe abbilden:

```text
producer_ok
producer_rejected_after_stop
producer_failed_queue_overflow
producer_failed_io
producer_shutdown_timeout
producer_internal_error
```

Diese Werte sind ausschließlich Producer-/ABI-Ausgänge. Sie sind keine neuen Journalrecordtypen,
keine neuen 2F-Fehlercodes und keine Sidecarfelder. Unbekannte interne Fehler dürfen nicht als
Queueoverflow maskiert werden.

## 13. Verpflichtende spätere Paket-2G-Tests

Die wiederaufgenommene Paket-2G-Implementierung MUSS mindestens prüfen:

- Queue exakt voll, erstes `full` und gewinnender Overflow-CAS;
- kein zweiter CAS-Gewinner;
- Stop gewinnt vor Overflow und Overflow gewinnt vor Stop;
- Callback nach Stop und nach Overflow;
- kein Retry, Spin, Callbackwait oder Disk-I/O im Callback;
- geordneter Drain aller zuvor angenommenen Events;
- kein Stoprecord, Integrity Receipt oder Bundlemanifest nach Overflow;
- unverändert bestehendes Session Receipt;
- Write- und Flushfehler während Drain;
- erfolgreicher und wiederholter Shutdown;
- Shutdown-Timeout und keine Ressourcenfreigabe bei aktivem Writer;
- kein C++-Exceptionescape;
- 100 Prozent Statements, Branches und Functions im Queue-/State-Core.

## 14. Nicht-Scope und Schluss-Gate

Dieses Addendum legt weder konkrete Queuekapazität noch Queue-Datenstruktur fest und
implementiert keine OBS-Callbacks, OBS-Entry-Points, Plugin-DLL, Journalserializer,
Cross-Language-Komponente, C-ABI-Header, Coverage-Toolchain, OBS-SDK-Version oder Paket 2H.

Diese Punkte dürfen erst nach dem separaten Commit dieses Vertrags im wiederaufgenommenen
Paket-2G-Auftrag bearbeitet werden. Paket 2H bleibt bis zu seiner eigenen Freigabe gesperrt.
