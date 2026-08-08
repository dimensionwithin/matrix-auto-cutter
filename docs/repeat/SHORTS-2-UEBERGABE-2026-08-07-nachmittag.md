> **Überholt.** Diese Datei wird vollständig ersetzt durch
> `SHORTS-2-UEBERGABE-2026-08-07-abend.md`. Ihre Abschnitte 3.1
> („OBS-Plugin schreibt kein stop bei zwei Ausgängen") und 3.2
> („Frame-Einbruch am Szenenwechsel als Gate-Ursache") beschreiben zwei
> Fehler, die es so nicht gibt; die Belege dafür stehen in Abschnitt 2
> der Abenddatei. Sie lag außerdem unter dem Dateinamen des kanonischen
> Architekturplans und wurde am 8.8. umbenannt. Aufbewahrt, weil ihr
> Inhalt sonst nirgends existiert — nicht als Quelle für Prämissen
> benutzen.

# Matrix Auto Cutter — Übergabe an den nächsten Arbeitschat

Stand: 7. August 2026, nachmittags
Vorgänger-Chat: SHORTS-1 (Aufnahme, Cursor-Protokoll, Notfallreparatur des Cutters)
Vorgänger-Übergabe: `docs\repeat\SHORTS-1-UEBERGABE-2026-08-07.md` — **gilt weiter
für Abschnitt 4 (Shorts-Entwurf), 6 (Ablage) und 8 (Arbeitsweise).** Die
Abschnitte 1 und 5 sind abgearbeitet, Abschnitt 7 ist überholt (neuer HEAD).

**Der Shorts-Block wurde an diesem Tag nicht begonnen.** Der gesamte Tag ging
für eine Notfallreparatur des Cutters drauf. Das Video ist veröffentlicht. Der
Fehler, der dazu geführt hat, ist **nicht behoben** — er wird bei der nächsten
Aufnahme mit Webcam wieder auftreten. Siehe Abschnitt 3.1.

---

## 1. Was am Morgen gesichert wurde (Shorts-Material)

Alles unangetastet, wartet auf den Shorts-Block.

- **Cursor-Protokoll:** `F:\ShortsQuellen\Cursor\cursor-2026-08-07 11-28-59.csv`,
  2867 Zeilen, lückenlos von 11:29:08 bis 11:52. Zwei Abfragen pro Sekunde.
- **Webcam-Spur:** `F:\ShortsQuellen\Avatar\AvatarWebcam-2026-08-07 11-35-16.mp4`,
  1036,12 s. **Von Hand dorthin verschoben** — OBS schreibt weiterhin nach `F:\`.
- **Bildschirmspur:** `F:\MatrixMarketAutoEdit\2026-08-07 11-35-16.mp4`, 1037,22 s.
- **Anker:** Maus drei Sekunden in der linken oberen Ecke, vor dem Reden.
  Steht in allen drei Spuren.

**Befund zu Abschnitt 1.5 der Vorgänger-Übergabe: zwei getrennte Dateien.**
Der Kompositions-Ansatz aus 4.3 trägt, kein Umplanen nötig.

**Bildschirmgeometrie:** DISPLAY1 primär, `0,0`–`2559,1439`, wurde aufgenommen.
DISPLAY2 links davon bei `-2560,0`. **Jede CSV-Zeile mit negativem x ist ein
Moment auf dem nicht aufgenommenen Monitor** — dort zeigt der Zeiger nicht auf
das, was im Bild ist. Für die Nachführung (4.4) verwertbar.

**Der Anker hat eine dritte, ungeplante Aufgabe:** Bildschirm- und Webcam-Datei
unterscheiden sich um 1,1 s Dauer. Ob der Versatz am Anfang oder Ende sitzt,
sagt nur der Anker, weil er in beiden Videos sichtbar ist.

**Nicht notiert:** Short-Kandidaten (Abschnitt 1.3). Der Nutzer wählt sie beim
Sichten. Zeitmarken dann aus dem **Rohvideo** nehmen, nicht aus dem
geschnittenen.

**Offene Frage aus 5.2, weiterhin unbeantwortet:** Was soll aus dem
Shorts-Schritt herausfallen? Nur Zeitfenster / plus 16:9-Clips / fertige
9:16-Clips / vertikal plus Untertitel. **Fragen, nicht annehmen.**

---

## 2. Was am Cutter passiert ist

### 2.1 Der Verlauf in Kürze

Nach der Aufnahme öffnete sich kein Review-Fenster. Ursachensuche über mehrere
Stunden, Reparatur, Video am Ende veröffentlicht.

Kette der Befunde:

1. **Kein Fenster ist normal.** `product_runner` ist ein fensterloser
   Hintergrundprozess. `review_app` erscheint erst bei fertigem Proposal.
2. Das Journal der Aufnahme hatte **keinen `stop`-Datensatz**. Ohne den kein
   Sidecar, ohne Sidecar kein Proposal, ohne Proposal kein Fenster.
3. Stop-Zeile von Hand angehängt → Finalizer scheiterte an einer Drift-Grenze.
4. Grenze angehoben → scheiterte an der **zweiten** Grenze (Pydantic-Feld).
5. Zweite und **dritte** angehoben (`validate_sidecar`) → scheiterte am
   **Framezahl-Abgleich**, der eigentlichen Ursache.
6. Endcounter korrigiert → Proposal, Review, Render, Veröffentlichung.

**Lehre für den nächsten Chat:** Die Drift-Anhebung war rückblickend gar nicht
nötig. Mit korrektem Endcounter lag der Drift bei **253 ppm**, also unter der
alten Grenze von 500. Der 743-ppm-Wert war Folge des zu niedrigen Counters,
nicht dessen Ursache. Wir haben zwei Runden lang das Symptom bearbeitet.

### 2.2 Was committet wurde

Commit `dbbfc62`, gepusht, `origin/master` synchron.

- Drift-Grenze 500 → **1000 ppm**, Warnschwelle bei 500 ppm mit Logeintrag.
- Neue Einzelquelle `src\matrix_auto_cutter\clock_bounds.py`
  (`DRIFT_WARNING_PPM`, `MAX_DRIFT_PPM`). Ersetzt **vier** verstreute Grenzen:
  `sidecar_builder`, Pydantic-Feld in `models.py`, JSON-Schema-Annotation,
  Neuberechnung in `validate_sidecar`.
- **Fünfte Stelle in der Doku:** `matrix-auto-cutter-architecture-plan-v0.2.md:345`
  im kanonischen Schemablock, der byte-genau gegen das exportierte
  Pydantic-Schema getestet wird.
- Ablehnungsgründe werden jetzt im Phase-1-Validierungsfehler **durchgereicht**
  (Feld, Wert, Kontext) statt verworfen. Unabhängig vom Anlass, soll bleiben.
- Sechs Tests auf die Konstanten umgestellt, Warnschwelle getestet.

**Volle Suite: 1688 passed, 0 failed.**

### 2.3 Der Eingriff am Journal — dokumentiert, weil unsichtbar

Am Journal `ff2618be-a9c1-4260-81ea-c0e08b630ff4` wurde **von Hand eine
Stop-Zeile angehängt**, die das OBS-Plugin nicht geschrieben hat:

```
"monotonic_ns":1037466666656,"output_frame_count":62233,"sequence":519
```

`62233` ist die per ffprobe gemessene echte Framezahl der MP4 und damit ehrlich.
**Die Verteilung über die Zeit ist rekonstruiert**, nicht gemessen: Die letzte
echte Kalibrierprobe stand bei Frame 62149 zu 1036,85 s. Im Journal ist das
nicht als Rekonstruktion erkennbar — deshalb steht es hier.

Sicherungen: `F:\ShortsQuellen\ff2618be-journal-BACKUP.ndjson` (Originalzustand,
519 Zeilen) und `F:\ShortsQuellen\session-backup\` (zwei fehlgeschlagene
Sessiondateien).

**Wichtig für künftige Fälle:** Eine Session im Zustand `finalizer_failed` ist
**terminal**. Der Runner versucht sie nie wieder. Zum erneuten Versuch muss die
Sessiondatei aus `…\product-runner\sessions\` weggeschoben werden, und der
Runner braucht einen Neustart, wenn Code geändert wurde.

---

## 3. Offene Fehler — nach Dringlichkeit

### 3.1 OBS-Plugin schreibt kein `stop` bei zwei Ausgängen — HÖCHSTE PRIORITÄT

**Das trifft jede künftige Aufnahme mit Webcam, also alle.** Ohne Fix
wiederholt sich der 7. August bei jedem Video.

Gemessener Vergleich, selber Tag, selbe OBS-Instanz (32.1.2), Producer
0.1.0-experimental:

| Journal | Dauer | Datensätze | Ende |
|---|---:|---:|---|
| `6f375521` (Abbruch-Take) | 49 s | 27 | `stop`, sauber |
| `ff2618be` (echter Take) | 1037 s | 519 | `calibration_sample`, **kein `stop`** |

Historie: 17 Journale, 15 enden sauber. Nur zwei enden ohne `stop` —
`51afb549` (03.08.) und `ff2618be`. Der Fehler ist selten, nicht chronisch.

**Der auffällige neue Faktor:** Erstmals liefen zwei OBS-Ausgänge gleichzeitig.
Der Adapter kennt aber nur **einen** Output — `acquire_recording_output()`
holt einmalig `obs_frontend_get_recording_output()` und hält ihn in einem
einzelnen `std::atomic<obs_output_t*>` (`obs_plugin.cpp:263-275`, `:534`). Keine
Unterscheidung nach Name oder UUID.

Wahrscheinlichste Kandidaten aus der Analyse:

1. Stiller Drop in der Command-Queue (Kapazität 4) bei `on_output(stopped)`,
   `obs_adapter.cpp:786-803` → `force_cleanup()`, schreibt nie ins Journal.
2. Validierungs-Gate in `process_stop()` verwirft das Signal:
   Pfadabgleich oder 8-Frame-QPC-Toleranz, `obs_adapter.cpp:1332-1371`. Der
   zweite Output ist ein plausibler Störfaktor für genau diese Prüfung.
3. Stiller Abbruch im Writer-Thread, `journal_producer.cpp:621-640`.

**Alle drei sind laut Overflow-Vertrag bewusst stille Pfade** — das System
schreibt bei Terminal-Failure lieber nichts als etwas Erfundenes
(`producer-queue-overflow-contract-v1.0.md:201-204`). Das ist Absicht, macht
die Diagnose aber teuer.

Ein echter Queue-Overflow ist ausgeschlossen: 514 Datensätze gegen 8192
Kapazität, und kein Fehlerdatensatz im Journal.

**C++ im nativen Teil, Opus, Neubau des Plugins nötig.**

### 3.2 Frame-Einbruch am Szenenwechsel — ZWEITHÖCHSTE PRIORITÄT

**Auch mit korrektem `stop` wäre die Aufnahme gescheitert.** Zweiter,
unabhängiger Defekt.

Bei t ≈ 995 s verschwanden **42 Frames in einem einzigen 2-Sekunden-Intervall**,
exakt am dritten `scene_changed` („Outro", 994,82 s). Davor lief der Zähler
16 Minuten mit exakt 60,000 fps. Punktuell, nicht schleichend.

Folge: Endcounter 62171 gegen echte Framezahl 62233 der MP4 — **63 Frames
Abstand bei 6 Frames Toleranz**. Das Gate:

| Prüfung | Fundstelle | Toleranz | Ist |
|---|---|---:|---:|
| `counter_span` | `sidecar.py:339` | ≤ 6 | **63** |
| `duration_span` | `sidecar.py:341` | ≤ 6000 ms | **63020** |

**Dieses Gate ist die eigentlich bindende Bedingung, nicht der Drift.** In der
ersten Analyse wurde es übersehen, weil es in `validate_sidecar` sitzt und nicht
im Kalibrierpfad des Finalizers. Claude Code hat das später von sich aus
korrigiert.

Der Zusammenhang mit dem Szenenwechsel ist zu auffällig für Zufall. Vermutlich
derselbe Ort wie 3.1.

### 3.3 Intro-Cut — Feature, vom Nutzer gewünscht

Der Schnitt soll dort gesetzt werden, wo der Szenenwechsel zu
**„Intro with Cam"** liegt. Alles davor weg.

**Der Wert steht bereits im Journal:** `scene_changed`, Label „Intro with Cam",
`monotonic_ns` 51.633.331.268 → **51,633 s**. Nichts muss erkannt oder geraten
werden, die Marke wird nur nicht benutzt.

Am 7.8. wurden stattdessen 34 Sekunden von Hand mit ffmpeg abgeschnitten
(zwei Durchgänge, 24 s + 10 s, `-c copy`).

**Erst angehen, wenn 3.1 und 3.2 stehen** — es setzt auf denselben Journaldaten
auf, die heute unzuverlässig waren.

### 3.4 Kleinkram

- **Lautheit:** −35,8 LUFS, YouTube zielt auf −14 und regelt nur herunter.
  Eigener `loudnorm`-Schritt am Ende der Kette. Steht seit der
  Vorgänger-Übergabe.
- **Kodierungspanne:** `status.json` und `runner.log` enthalten „vollstÃ¤ndig"
  statt „vollständig". Nur Anzeigetexte.
- **`tests\repeat\test_cutcli.py`** legt bei jedem vollen Suitenlauf eine Datei
  namens `-` im Wurzelverzeichnis an. Bekannt, harmlos, eigener Auftrag.
- **20 mypy-Fehler in `repeat\`**, vorbestehend. Formatierungsbefunde in
  `sidecar_builder.py`, `sidecar.py` und sieben Testdateien, alle vorbestehend.
- **Drift-Grenze 1000 ppm:** Sollte sie bleiben? Mit korrektem Endcounter waren
  es 253 ppm. Argument fürs Behalten: Warnschwelle und verbesserte
  Fehlermeldung sind echte Gewinne, und solange 3.2 ungeklärt ist, ist eine
  Warnung bei 600 ppm besser als ein toter Lauf. Entscheidung offen.

---

## 4. Was in `UMGEBUNG.md` fehlt

Jeder dieser Punkte hat am 7.8. Zeit gekostet.

- **`ffprobe`/`ffmpeg` 8.1.1 liegen im PATH.** Voller Pfad:
  `C:\Users\schan\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_…\ffmpeg-8.1.1-full_build\bin\`.
  Der Aufruf `ffprobe …` genügt.
- **`product_runner` ist ein fensterloser Hintergrundprozess** (`pythonw.exe`,
  kein `MainWindowTitle`). Ein zweiter Start meldet „läuft bereits", Exitcode 2,
  den `product_startup` als Erfolg wertet → Exitcode 0, keine Ausgabe, kein
  Fenster. **Das ist normal und kein Fehler.**
- **Das Review-Fenster (`review_app`) erscheint erst bei fertigem Proposal.**
- **Zustandsverzeichnis:**
  `%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\product-runner\`
  (`status.json`, `sessions\`, `logs\runner.log`), Journale unter
  `…\MatrixAutoCutter\producer\journals\`.
- **Runner sauber beenden:** `python -m matrix_auto_cutter.product_runner --stop`.
  **Nie `Stop-Process`.** Die Sperre ist eine `msvcrt`-Bytesperre, die Windows
  beim Prozessende freigibt — verwaiste Sperren gibt es also nicht, aber der
  Zustand bleibt unsauber.
- **`-l de` und `-ml 60`** bleiben Pflichtparameter für whisper-cli.
- **28 Transkripte**, nie neu transkribieren.

---

## 5. Befund über die Dokumentation selbst

**`docs\` ist gleichzeitig veraltet und vertragsbindend.** Diese Mischung ist
gefährlich und muss jeder wissen, der dort liest.

*Veraltet:* Die Begriffe „Product Runner" und `review_app` kommen in keinem
Dokument vor. Beschrieben wird ein statisches `review.html`, das der Nutzer
selbst öffnet — das Produkt hat ein automatisch erscheinendes Fenster.
`no_sidecar_safe_mode` ist als Planungsbegriff dokumentiert, aber **nicht
implementiert**: Es ist nur ein Rückgabewert des Validators, den jeder Konsument
als Fehler behandelt. Eine Startreihenfolge OBS/Runner ist nirgends festgelegt.

*Vertragsbindend:* Der kanonische Schemablock in `v0.2` wird byte-genau gegen
das exportierte Pydantic-Schema getestet. Eine Änderung dort bricht die Suite.

**Empfehlung für die Project Knowledge:** `UMGEBUNG.md`, `ABLAGE.md` und die
jeweils neueste Übergabe hinein. Die vier Architekturdokumente **nicht** — bei
Bedarf gezielt über Claude Code lesen lassen, dann mit dem Hinweis, dass sie
teils überholt sind.

---

## 6. Repositoryzustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        dbbfc62  „Drift-Gate auf 1000 ppm anheben, Grenzwertquelle vereinheitlichen"
origin      synchron, Working Tree sauber
```

Der Commit enthält zehn Dateien, darunter die beiden vorher untracked
Übergaben aus `docs\repeat\`.

Ablage, Werkzeugpfade und die Warnungen zu `git clean -xfd` und `find /`:
unverändert gültig, siehe Abschnitt 6 der Vorgänger-Übergabe.

---

## 7. Arbeitsweise — was sich am 7.8. bewährt hat

Abschnitt 8 der Vorgänger-Übergabe gilt unverändert. Ergänzungen aus diesem Tag:

**Rein lesende Analyseaufträge vor jedem Eingriff.** Vier davon an diesem Tag,
jeder hat etwas gefunden, das der vorherige übersehen hatte. Der teuerste
Fehler war, nach dem ersten Bericht sofort zu handeln.

**„Nichts ausführen, nur bewerten" gehört in jeden Analyseauftrag.** Einmal
hätte ein voreiliger `matrix-auto-finalize`-Aufruf die Session terminal
gemacht.

**Claude Code hat zweimal von sich aus angehalten**, statt den Auftragsrahmen zu
überschreiten — beim Test außerhalb des Änderungsbereichs und bei der
Schemastelle in `docs\`. Beide Male war das richtig und hat einen sauberen
Folgeauftrag ermöglicht. **Nicht wegoptimieren.**

**Eine Selbstkorrektur ist im Bericht aufgetaucht:** Die Aussage aus Runde 1,
ein Framezahl-Abgleich existiere nicht, war falsch. Der Agent hat das später
von sich aus richtiggestellt. Berichte gegeneinander rechnen bleibt Pflicht.

**Der Nutzer stand unter Zeitdruck** (Charts veralten). Bei
Prioritätskonflikten: Erst das Video herausbekommen, dann sauber reparieren.
Das hat funktioniert — der Commit kam nach der Veröffentlichung.

---

## 8. Vorgeschlagene Reihenfolge

1. **OBS-Plugin, fehlendes `stop` bei zwei Ausgängen** (3.1) — Opus, C++,
   Plugin-Neubau. Ohne das wiederholt sich der 7.8. beim nächsten Video.
2. **Frame-Einbruch am Szenenwechsel** (3.2) — vermutlich derselbe Ort.
   Zusammen mit 1 sind das die Bedingungen dafür, dass eine Aufnahme wieder
   ohne Handgriffe durchläuft. Möglicherweise ein Auftrag statt zwei.
3. **Intro-Cut bei „Intro with Cam"** (3.3) — sauber spezifiziert, wartet auf
   stabile Journaldaten.
4. **Shorts-Block** — Bestandsaufnahme des Renderers (Schnittliste heraus?),
   dann die offene Frage aus 5.2 der Vorgänger-Übergabe klären.
5. `loudnorm`, Kleinkram aus 3.4.

---

## 9. Was in den neuen Chat gehört

Diese Datei. Sie gehört außerdem nach `docs\repeat\` und fährt beim nächsten
Commit mit.
