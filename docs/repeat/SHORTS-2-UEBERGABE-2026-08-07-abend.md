# Matrix Auto Cutter — Übergabe an den nächsten Arbeitschat

Stand: 7. August 2026, 23:30 Uhr
Vorgänger-Chat: SHORTS-2 (Diagnose und Reparatur der Stop-Kette, Uhrenmetrik,
Startautomatik, Review-Fenster, Stinger)

**Diese Datei ersetzt die Übergabe vom 7.8. nachmittags vollständig.** Jene
Datei beschreibt in ihren wichtigsten Abschnitten zwei Fehler, die es so nicht
gibt — siehe Abschnitt 2. Sie liegt außerdem unter dem falschen Dateinamen,
siehe Abschnitt 7.

**Weiterhin gültig aus der SHORTS-1-Übergabe:** Abschnitt 4 (Shorts-Entwurf),
6 (Ablage), 8 (Arbeitsweise). Diese Datei lag dem letzten Chat nicht vor und
sollte dem nächsten mitgegeben werden, sobald der Shorts-Block beginnt.

---

## 1. Der Stand in einem Absatz

Eine Aufnahme läuft wieder ohne Handgriff durch. Bewiesen am Abend des 7.8.:
Aufnahme gestartet, gestoppt, Journal mit `stop`, Sidecar, Proposal, Review —
Fenster ging von selbst auf, `code: approval_pending`. Das hatte seit dem
3. August nicht mehr funktioniert. Fünf Commits, alle auf `origin/master`
gepusht. Testsuite von 1688 auf 1711 gewachsen.

---

## 2. Was an der alten Übergabe falsch war

Beide dort als höchste Priorität geführten Fehler existieren nicht in der
beschriebenen Form. Das ist wichtig, weil die alte Datei sonst in künftigen
Analysen als Prämisse mitgeschleppt wird.

### 2.1 „OBS-Plugin schreibt kein `stop` bei zwei Ausgängen" — widerlegt

Der Zwei-Ausgänge-Verdacht ist tot. `Source Record` ist ein Filter-Plugin auf
der Webcam-Quelle und dem OBS-Frontend unbekannt;
`obs_frontend_get_recording_output()` liefert ausschließlich
`simple_file_output`. Das Stop-Signal des zweiten Ausgangs erreicht den Adapter
nie, der Pfadabgleich wird gar nicht erst befragt.

Beleg: Am 3.8. lief **kein** zweiter Ausgang, und der Lauf `51afb549` ist
trotzdem am selben Block gescheitert.

Ebenfalls falsch: „17 Journale, 15 enden sauber". Stand 7.8. abends: 17
Journale, davon enden 12 mit `stop`, drei mit `pause`, zwei mit
`calibration_sample`.

*Nachtrag (Stand 8.8.):* Unter `…\producer\journals\` liegen inzwischen **21**
Journale, davon 17 mit `stop`, 3 mit `pause`, 1 mit `calibration_sample`. Die
Differenz sind die Testläufe des 7.8. abends. Die Zählung von 7.8. war nicht
falsch, sondern ist überholt — beim Weiterverwenden immer den Stichtag
mitnennen.

### 2.2 „Frame-Einbruch am Szenenwechsel" als Gate-Ursache — widerlegt

Die dort genannten Werte `counter_span = 63` und `duration_span = 63020` sind
Artefakte der Handbearbeitung des Journals. Mit dem Stop-Counter 62233 ist die
Abweichung **null**.

Auch der Endcounter 62171 aus der alten Übergabe steht in **keiner** Fassung
der Journaldatei. Die produktive Datei wurde am 7.8. mindestens zweimal von
Hand verändert; belastbar ist ausschließlich
`F:\ShortsQuellen\ff2618be-journal-BACKUP.ndjson`.

*Korrektur (8.8.):* Der Stop-Counter lautet **62233**, nicht 62234 — 62234 war
ein Abschreibfehler. 62233 deckt sich mit der unabhängig gemessenen Framezahl
der MP4 (1037,22 s × 60 fps).

Wichtiger als die Ziffer ist, woher sie stammt. Das Backup
`ff2618be-journal-BACKUP.ndjson` enthält **keinen** `stop`-Record: 519 Zeilen,
letzter Eintrag ein `calibration_sample` mit `output_frame_count` 62149. Die
produktive Datei unterscheidet sich von ihm durch genau eine angehängte Zeile —
einen `stop` mit `output_frame_count` 62233. Genau diese Zeile ist die in
diesem Abschnitt beschriebene Handbearbeitung.

**Folge:** Die Aussage „das Gate war nie die bindende Bedingung" stützt sich
damit auf einen Wert aus einer handbearbeiteten Zeile, nicht auf die als allein
belastbar erklärte Backupdatei. Sie ist plausibel, aber **nicht belegt**, und
darf nicht als gesicherte Prämisse weitergereicht werden. Für die Reparatur
(Abschnitt 3.1) ist das folgenlos — die hängt nicht an dieser Zahl.

### 2.3 Die echte Ursache

Ein 22-Zeilen-Block, `obs_adapter.cpp:1340-1361`. Er „snappt" den QPC-Wert des
Stops auf einen counterabgeleiteten Wert und verwirft den Stop, wenn die
Differenz 8 Frames (133,33 ms) überschreitet. Die dahinterliegende Annahme —
Counter und QPC bleiben über die gesamte Aufnahme auf 8 Frames zusammen — war
undokumentiert und ungetestet. Eingeführt am 2.8. in Commit `a6cbf9d` als
Nebenwirkung einer Pause/Resume-Härtung.

Zwei Kanten, zwei reale Ausfälle:

- `ff2618be` (7.8. vormittags): Rückstand 46 Frames = 766 ms gegen 133 ms
  Toleranz → Stop verworfen.
- `51afb549` (3.8.): Toleranz bestanden, aber das Snapping setzte den
  Zeitstempel 16,67 ms **vor** den letzten `calibration_sample` → Writer
  verwarf ihn als QPC-Regression.

---

## 3. Was heute committet wurde

Alles auf `origin/master`, Working Tree sauber bis auf eine untrackte Datei
(Abschnitt 7).

| Commit | Inhalt |
|---|---|
| `dbbfc62` | (Vorlauf) Drift-Grenze 1000 ppm, `clock_bounds.py` als Einzelquelle |
| `1c496b9` | Reparatur: 8-Frame-Toleranz entfällt, Monotonie-Klemmung im Producer |
| `a88280e` | Diagnostik: Sammelmeldung benennt die gescheiterte Teilbedingung |
| `99e4671` | `START-ALLES.ps1` / `.cmd` |
| `5292107` | Uhrenmetrik ehrlich (siehe 3.2) |
| `95f7956` | Review-Fenster: Mindestgröße, Layout, Größe merken |

### 3.1 Die Reparatur (`1c496b9`, `a88280e`)

Die 8-Frame-Toleranz ist entfallen — ein großes L ist ein Befund über die
Aufnahme, kein Grund, den Stop wegzuwerfen. Die Verankerung selbst **bleibt**
(sie hält den Interleaver-Rückstand aus dem Drift-Gate heraus; ihr ersatzloser
Wegfall wäre ein Fehler gewesen und wurde vom Agenten in der Vorprüfung
abgefangen).

Die Monotonie-Klemmung sitzt im **Producer**, nicht im Adapter: Der Adapter
kennt nur den zuletzt *eingereichten* Record, der Writer prüft gegen den
zuletzt *geschriebenen*. Beide fallen auseinander, sobald der Writer etwas
ablehnt.

Der Test `obs_adapter_tests.cpp:1605-1621` aus `a6cbf9d` läuft unverändert
grün — der ursprüngliche Zweck der Verankerung ist erhalten.

### 3.2 Die Uhrenmetrik (`5292107`)

`drift_ppm` maß bisher keine Drift. Die Formel reduzierte sich auf
`L / span × 10⁶`, wobei L allein der Frame-Rückstand im Moment des Stops ist —
eine Punktmessung an einem Endpunkt, normiert auf die Lauflänge. Beleg: alle
zehn Läufe der Historie mit aktiver Verankerung maßen exakt 0,00 ppm.

Neu, in drei getrennten Größen:

1. **Drift** als Steigung über die Kalibrierreihe (Theil-Sen, Median aller
   paarweisen Steigungen). Robust gegen Sprünge, unverzerrt gegen synthetische
   Drift von 200/800/1500/3000 ppm. Die Stop-Probe ist ausgeschlossen — sie
   liegt per Konstruktion auf der Counterlinie und würde echte Drift verdecken.
2. **Frameverlust** absolut in Frames plus Zeitpunkt, als
   `finalization.warnings`-Eintrag. **Er warnt, er lässt den Lauf nicht
   scheitern.**
3. **Mindestbeweislage**: Gate erst ab 16,667 s aktiver Dauer, Warnung ab
   33,333 s — beides aus `clock_bounds.py` abgeleitet, nicht gesetzt. Darunter
   ist ein 1000-ppm-Gate physikalisch nicht messbar (bei 16 s entspricht ein
   einzelner Frame Quantisierung schon 1033 ppm).

Ergebnis: `89c344e6` von 1257,4 auf 0,0 ppm mit Vermerk „44 Frames bei 42,3 s",
`ff2618be` von 16,1 auf 0,0 mit „42 Frames bei 994,2 s". Kein Lauf, der vorher
durchging, fällt heraus.

**Korrektur vom 9.8.:** `89c344e6` galt damit als gerettet — war es aber nicht.
Die Aussage stimmt nur für die Drifthälfte des Gates. Der Lauf wurde weiterhin
abgelehnt, auf der **Residualhälfte**, mit 283,3 ms gegen 50 ms. Erst der
Gate-Fix vom 9.8. (Abschnitt 3a) lässt ihn tatsächlich durchlaufen. Wer die
Wirkung eines Gate-Eingriffs prüft, muss **beide** Teilbedingungen nachrechnen;
die Fehlermeldung `sidecar.clock_gate` nennt keine von beiden.

**Preis, bewusst bezahlt:** Der Verbraucher rechnet `drift_ppm` nicht mehr nach
(Weg B). Es ist jetzt eine deklarierte Kennzahl wie
`max_calibration_residual_ms`. Sieben Audit-Tests entfielen oder wurden
umgeschrieben, in der Commit-Message begründet. Der Alternativweg hätte die
Kalibrierreihe ins Sidecar aufnehmen müssen und damit den byte-getesteten
Schemablock berührt.

### 3.3 Startautomatik (`99e4671`)

`START-ALLES.cmd` im Repositoriumswurzelverzeichnis, per Doppelklick oder
Desktop-Verknüpfung. Reihenfolge, die zwingend ist:

1. Veadotube starten (Fenster muss **vor** OBS existieren)
2. OBS starten, 8 s warten, bis die Quellen gebunden sind
3. Veadotube kurz in den Fokus holen — **erst jetzt**, sonst wirkungslos
4. Fokus zurück auf OBS
5. Runner über `START-MATRIX-AUTO-CUTTER.cmd`

Getestet: Avatar erscheint ohne Handgriff, Runner startet mit neuer PID.

**Kein Stopp-Skript gebaut, mit Absicht.** Der Runner arbeitet **nach** dem Ende
der Aufnahme weiter (Finalizer, Proposal, Render — am 7.8. über sechs Minuten).
Ein Wächter, der ihn beim Schließen von OBS mitnimmt, würde Videos kosten.
Mehrfachstarts sind ungefährlich: Die `msvcrt`-Bytesperre lässt nur einen
Runner zu, ein zweiter meldet „läuft bereits" und beendet sich.

### 3.4 Review-Fenster (`95f7956`)

Mindestgröße **834 × 651**, Standardgröße **834 × 875** — am laufenden
Widgetbaum gemessen, nicht geschätzt. Die alten Werte waren `980x720` und
`minsize(760, 520)`; die Standardhöhe lag 155 px unter dem Bedarf, genau die
Höhe der beiden unteren Blöcke.

Eigentliche Ursache war die pack-Reihenfolge: Die Cut-Liste wurde vor den
Bedienelementen gepackt und nahm mit `expand=True` allen Platz. Jetzt liegen
Auswahl- und Buttonblöcke in einem Container mit `side="bottom"`, der zuerst
gepackt wird.

Fenstergröße und -position werden in
`…\product-runner\review-window.json` gemerkt und beim Öffnen geklemmt
wiederhergestellt. Unabhängig von der Schnittanzahl (gegen 0, 1, 3, 40 und 250
Schnitte geprüft).

---

## 3a. Nachtrag 9.8. — Clock-Gate und Proposal-Schema

### 3a.1 Die Stop-Probe ist kein Messpunkt mehr

Lauf `382a196c` vom 8.8. scheiterte mit `finalizer/E_JOURNAL_CORRUPT`,
`sidecar.clock_gate`, obwohl die Uhr einwandfrei war (Theil-Sen 155,9 ppm, unter
der Quantisierungsschwelle dieses Laufs).

Ursache: Der `stop`-Record kann seinen QPC-Tick mit der letzten
`calibration_sample` teilen — bei `382a196c` tragen seq 103 und seq 104
denselben `monotonic_ns` 199.933.325.336, der Counter steht beim Stop 29 Frames
weiter. `map_qpc_frame` findet per `bisect_left` die Kalibrierprobe und
interpoliert den Stop auf **deren** Counter. Gemessen wurde damit die
Stop-Latenz des A/V-Interleavers, nicht die Uhr: **483,3 ms** gegen die
50-ms-Schranke. `89c344e6` traf es mit 17 Frames und **283,3 ms**.

Es war kein Grenzfall, sondern ein Münzwurf: 17 der 19 finalisierbaren Journale
haben ihren Stop in einem eigenen Tick und messen 0,00 ms — auch bei
Stop-Latenzen von 3 bis 121 Frames. Nachgemessen: mit eigenem Tick akzeptierte
der alte Code sogar 3000 Frames Vorsprung mit 0,00 ms. Die Schranke griff also
nur bei Tickkollision.

Die Stop-Probe ist jetzt aus der **Residualschleife** heraus — mit derselben
Begründung, mit der `_samples_without_stop` sie schon aus der Driftschätzung
nahm: Der Adapter verankert ihren Zeitstempel auf dem Framecounter, ihr Residual
gegen dieselbe Counterlinie zu halten ist zirkulär. **Sie bleibt Stützstelle der
Interpolation**, sie ist nur kein Messpunkt. Die **50-ms-Schranke und
`MAX_DRIFT_PPM` sind unverändert**, es gibt keinen Sonderfall für kollidierende
Ticks und keine Toleranzerhöhung. Alle 19 Journale messen danach 0,00 ms.

Was dabei entfällt und nirgends ersetzt wird: eine Obergrenze für den
Counterzuwachs im letzten Intervall (siehe 6.6 b). Sie war nie eine Prüfung,
sondern eine Nebenwirkung der Tickkollision.

### 3a.2 Proposal-Schema 1.1 hat zwei Gestalten

`intro_resolution` wurde **in 1.1 hineingelegt**, ohne die Schemaversion zu
bumpen. Damit gilt: **Wer 1.1 liest, darf das Feld nicht voraussetzen.** Es gibt
1.1-Bytes mit und ohne `intro_resolution`, und beide sind gültig — sechs
Proposals auf der Platte tragen es nicht.

Der schärfere Validator („1.1 braucht Outro *und* Intro") hatte am 8.8. genau
das gebrochen: Das Review-Fenster wies sein eigenes Artefakt ab, weil ein noch
laufender Alt-Runner es ohne das Feld geschrieben hatte. Der 1.1-Zweig fordert
jetzt wieder nur die Outro-Resolution; frisch erzeugte Proposals tragen das
Intro-Feld ohnehin immer. Der saubere Weg wäre eine 1.2 mit eigener
Digest-Domain gewesen, wie damals beim Outro (siehe 6.6 c).

**Merksatz aus dem Fall:** Ein Feld nachträglich in eine bestehende
Schemaversion zu legen macht jedes bereits veröffentlichte Artefakt dieser
Version unlesbar, sobald der Validator es fordert.

---

## 4. Ergänzungen für `UMGEBUNG.md`

Jeder Punkt hat am 7.8. Zeit gekostet.

- **`python -m matrix_auto_cutter…` funktioniert NICHT.** Das `python` aus dem
  PATH ist eine andere Installation als die venv-Basis; `pydantic_core` bricht
  mit `ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'`.
  Der Interpreter muss aus `pyvenv.cfg` gelesen werden:
  ```powershell
  $base = ((Select-String -Path ".\.venv\pyvenv.cfg" -Pattern '^home = ').Line -replace '^home = ','').Trim()
  $env:PYTHONPATH = "$PWD\src;$PWD\.venv\Lib\site-packages"
  & "$base\python.exe" -m matrix_auto_cutter.product_runner --stop
  ```
  Genau daran ist am 7.8. ein `--stop` still gescheitert, worauf ein
  vermeintlich „nicht reagierender" Runner diagnostiziert wurde.
- **`pythonw.exe` schluckt Fehlermeldungen wortlos.** Bei jeder Fehlersuche
  `python.exe` verwenden.
- **`review_app` braucht `--proposal PFAD`.** Ohne Runner von Hand aufrufen:
  ```powershell
  $p = Get-ChildItem "$env:LOCALAPPDATA\DimensionWithin\MatrixAutoCutter\product-runner\artifacts" -Recurse -Filter "cut-proposal.json" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  & "$base\pythonw.exe" -m matrix_auto_cutter.review_app --proposal $p.FullName
  ```
- **Das OBS-Plugin liegt unter**
  `C:\ProgramData\obs-studio\plugins\matrix-auto-cutter-obs\bin\64bit\matrix-auto-cutter-obs.dll`
  — echte Kopie, kein Symlink. Neubau liegt unter
  `build\obs-nmake-cmake440\`. Zum Tausch: OBS schließen, PowerShell **als
  Administrator**, alte Datei vorher sichern. Ladevorgang danach im OBS-Log
  prüfen (`Select-String -Pattern "matrix"`); der Pfad steht dort **nicht**,
  nur der Dateiname.
- **`status.json` schreibt UTC, `runner.log` schreibt Ortszeit (+02:00).**
  Zwei Formate im selben Produkt — beim Zeitvergleich beachten.
- **Webcam-Ausgabepfad** steht nicht in den OBS-Einstellungen, sondern im
  Filter `Source Record` auf der Webcam-Quelle. Steht jetzt auf
  `F:\ShortsQuellen\Avatar`. Niemals auf `F:\MatrixMarketAutoEdit` — dort sucht
  der Cutter seine Eingangsvideos.
- Aus der alten Übergabe unverändert gültig: `ffprobe`/`ffmpeg` 8.1.1 im PATH;
  `product_runner` ist fensterlos; Review-Fenster erst bei fertigem Proposal;
  Zustandsverzeichnis unter `%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\`;
  `-l de` und `-ml 60` Pflicht für whisper-cli; 28 Transkripte, nie neu
  transkribieren.

---

## 5. Der Stinger — diagnostiziert, teilweise behoben

Vier Dateien (`Stinger_Dektop`, `Stinger_Gaming`, `Stinger_utro`,
`stinger-sovereign-desk-2200ms-trackmatte-1440p`) sind **byte-identisch**. Es
gibt einen Stinger unter vier Namen; ein Fix wirkt überall.

Gemessen:

```
Containerlänge      2,391 s
Letzter Videoframe  1,876 s
Tonspur bis         2,391 s
```

**515 ms ohne ein einziges neues Bild.** Die Tonspur hält den Container offen,
OBS zeigt in dieser Zeit das letzte Standbild. Das ist das vom Nutzer seit
Beginn beobachtete Hängen.

Verschärfend: Der letzte Frame liegt **mitten in der Animation** — die Matte ist
noch zu rund 40 % weiß, der Wisch also unvollendet. Genau dieser halbe Zustand
friert ein. Zusätzlich hat die Datei variable Bildrate (~47 fps im Schnitt,
unregelmäßige Zeitstempel), und der Dateiname verspricht 2200 ms statt 2391 ms.

**Behoben (Zwischenlösung):** Zwei Dateien wurden erzeugt und vom Nutzer
getestet — `Stinger_synchron.webm` (Ton auf 1,845 s gekürzt mit 150 ms
Ausblendung, Container 1,877 s) und `Stinger_ohne_Ton.webm`. Das Standbild ist
damit weg. **Am 7.8. vom Nutzer von Hand in OBS eingesetzt — erledigt.**

**Offen:** Der Wisch endet weiterhin unvollendet, also mit hartem Schnitt. Das
behebt nur ein Neurendern. Abnahmebedingung dafür: letzter Videoframe =
Containerlänge, Matte am Ende vollständig zurückgezogen, konstante 60 fps.

**Frameverlust-Befund dazu:** Die Verlusterkennung findet in allen 19 Journalen
(Stand 7.8.) genau zwei Ereignisse (7.8. vormittags bei 994 s, abends bei
42,3 s). Im
Abschlusstest mit sechs Szenenwechseln blieb der Rückstand über alle Wechsel
konstant — **kein** Verlust. Die These „alle Stinger kosten Frames" ist damit
widerlegt; es sind Einzelereignisse unter Last.

---

## 6. Offene Punkte — nach Wert geordnet

### 6.1 Intro-Cut bei „Intro with Cam" — **gebaut am 9.8.**

**Erledigt.** `intro.py` ist neu, `cut_proposal.py` bindet ihn ein. Umgesetzt
wie unten festgelegt: auf der **Frameachse** über `mapped_source_frame`, mit
Bindung an das Label und der `scene_uuid` als Rückfallebene, und mit dem
Zweig `no_matching_scene_event` als dem häufigeren Fall.

Zwei Zahlen kamen aus den Läufen vom 8. und 9.8. dazu, beide als Konstanten in
`intro.py` und beide dort mit ihrer Herkunft kommentiert:

- **`INTRO_CUT_OFFSET_FRAMES = 148`** — der Schnitt liegt *hinter* der
  Szenenmarke, nicht auf ihr. OBS schreibt `scene_changed` im Moment des
  Umschaltens, der Stinger wischt danach noch. 35 Frames waren der gemessene
  Rest der Vorszene (30 gemessen + 5 Sicherheit); damit begann das Video aber
  auf dem Blackscreen am Anfang des Übergangs. Plus die gemessene Stingerlänge
  von 1,877 s = 113 Frames ergibt 148 Frames = 2,467 s. Damit fällt der ganze
  Übergang weg und der erste sichtbare Frame ist das Intro.
  **Ein neu gerenderter Stinger verschiebt diese 148** (siehe 6.4) — die Zahl
  wandert mit seiner Länge.
- **`INTRO_FLOW_PROTECTED_FRAMES = 450`** — feste Einstiegszone von 7,5 s hinter
  dem Schnitt, halboffen `[intro_start, intro_start + 450)`. Jeder
  Stille-Kandidat, der **darin beginnt**, wird **ganz verworfen**
  (`intro_flow_protected`), nicht gekürzt: ein bei 450 angesetzter Restschnitt
  fiele wieder in den bewusst gestalteten Einstieg. Ab Frame 450 arbeitet der
  Cutter unverändert.

Die Zone war zuerst aus den `silencedetect`-Daten *abgeleitet* (Ende der ersten
Pause hinter dem Schnitt). Der Lauf vom 9.8. hat das widerlegt: Hinter dem
Schnitt läuft das Intro **mit Musik**, es gibt dort keine erkannte Stille, und
die eigentliche Pause vor dem ersten Wort lag außerhalb jeder Ableitung — der
Cutter nahm alles bis zum ersten Wort weg. silencedetect trennt laut von leise,
nicht Intro von Gespräch. Eine feste Länge trifft den gestalteten Einstieg
zuverlässiger als jede Ableitung daraus. Beide Zahlen werden am Ergebnis
nachjustiert, nicht hergeleitet.

Der Nebeneffekt unten gilt unverändert: Der Intro-Cut verschiebt die gerenderte
Zeitachse, Short-Kandidaten weiterhin aus dem Rohvideo notieren.

---

*Die ursprüngliche Spezifikation, zur Nachvollziehbarkeit:*

Der Wert steht im
Journal als `scene_changed` mit Label „Intro with Cam". Am 7.8. vormittags lag
er bei **51,633 s Rohzeit**.

**Wichtig für den Bau:** Der Schnitt gehört **in die Schnittliste vor dem
Rendern**, nicht ans Ende. Gegenprobe, die den Ansatz bestätigt: Der Nutzer hat
am 7.8. von Hand **34 Sekunden** aus dem *gerenderten* Video geschnitten. 51,6 s
roh minus ~17,6 s vom Cutter entfernte Totzeit ergibt genau diese 34 s. Rohe und
gerenderte Zeitachse zeigen auf denselben Punkt.

Nebeneffekt für später: Der Intro-Cut verschiebt die gerenderte Zeitachse.
Short-Kandidaten deshalb weiterhin aus dem **Rohvideo** notieren.

**Befund vom 8.8. — die Szenenmarke steht auf zwei Uhren.** Journaluhr:
`monotonic_ns` 51.633.331.268 = 51,633 s, Nullpunkt ist
`producer_monotonic_at_output_start_signal`. Frameachse: derselbe Record trägt
`output_frame_count` 3080, das sind bei 60 fps 51,33 s. Die Differenz von rund
283 ms ist der Startversatz zwischen Ausgangssignal und erstem Videoframe
(`recording_started` bei 283.333.322 ns, Frame 1). Die 34-Sekunden-Gegenprobe
oben ist zu grob, um zwischen beiden zu entscheiden.

**Festlegung für den Bau:** auf der **Frameachse** über `mapped_source_frame`
arbeiten, wie es `outro.py:249` bereits produktiv tut. Einbauort ist
`cut_proposal.py` um Zeile 866, unmittelbar beim Outro-Mechanismus —
spiegelbildlich am Anfang statt am Ende. Zwei Zweige sind Pflicht, analog
`no_matching_scene_event` und `ambiguous_scene_events`: das Label fehlt in 16
von 21 Journalen, „kein Label" ist der häufigere Fall. Bindung über das Label
„Intro with Cam", mit der `scene_uuid`
`df50e171-befb-4d89-b9e9-66a29dd0865e` als Rückfallebene.

### 6.2 `loudnorm`

−35,8 LUFS, YouTube zielt auf −14 und regelt nur herunter. Rund 20 dB zu leise.
Eigener `loudnorm`-Schritt am Ende der Renderkette. Größter sichtbarer
Qualitätsgewinn, betrifft jeden Zuschauer, steht seit zwei Übergaben.

### 6.3 Shorts-Block

**Vorher die SHORTS-1-Übergabe beschaffen** (`docs\repeat\`), sie regelt
Abschnitt 4, 6 und 8 und lag dem letzten Chat nicht vor.

Material gesichert und unangetastet:
- Cursor-Protokoll: `F:\ShortsQuellen\Cursor\cursor-2026-08-07 11-28-59.csv`,
  2867 Zeilen, 11:29:08–11:52, zwei Abfragen pro Sekunde
- Webcam: `F:\ShortsQuellen\Avatar\AvatarWebcam-2026-08-07 11-35-16.mp4`,
  1036,12 s
- Bildschirm: `F:\MatrixMarketAutoEdit\2026-08-07 11-35-16.mp4`, 1037,22 s
- Anker: Maus drei Sekunden in der linken oberen Ecke, in allen drei Spuren

Bildschirmgeometrie: DISPLAY1 primär `0,0`–`2559,1439` (aufgenommen), DISPLAY2
links bei `-2560,0`. **Jede CSV-Zeile mit negativem x ist ein Moment auf dem
nicht aufgenommenen Monitor.**

**Weiterhin unbeantwortete Frage, fragen statt annehmen:** Was soll aus dem
Shorts-Schritt herausfallen? Nur Zeitfenster / plus 16:9-Clips / fertige
9:16-Clips / vertikal plus Untertitel?

### 6.4 Stinger neu rendern

Siehe Abschnitt 5. Die getesteten Zwischendateien sind am 7.8. in OBS
eingesetzt; der Druck ist damit raus. Offen bleibt hier nur noch das
Neurendern. Abnahmebedingung unverändert: letzter Videoframe =
Containerlänge, Matte am Ende vollständig zurückgezogen, konstante 60 fps.

### 6.5 Kleinkram

- **Kodierungspanne:** „vollstÃ¤ndig" statt „vollständig" in `status.json` und
  `runner.log`. Nur Anzeigetexte.
- **Datei `-`** im Wurzelverzeichnis entsteht bei **jedem** pytest-Lauf. Ein
  Test behandelt `-` unter Windows als Dateinamen statt als stdout-Konvention.
- **Native Adaptersuite ist flaky**, vorbestehend: feste 2-s-Fristen im
  Test-Harness unter Maschinenlast. Belegt mit Dreiweg-Vergleich, 4/125 im
  Ausgangsstand gegen 3/125 danach — kein Unterschied.
- **20 mypy-Fehler in `repeat\`**, vorbestehend.
- **`ruff format`** meldet eine vorbestehende Abweichung in
  `review_app.py:484`, im Repo nicht erzwungen.
- **Logitech G Plugin** in OBS aktiv — vermutlich ungenutzt. Nicht
  deinstallieren, im Zweifel nur das Häkchen entfernen (reversibel).

### 6.6 Uhrenmetrik und Proposal-Schema — vier offene Punkte vom 9.8.

Keiner davon ist dringend, alle vier sind belegt und keiner ist ein Bug im
laufenden Betrieb.

**(a) Die Mindestbeweislage rechnet gegen die falsche Basis.**
`minimum_measurable_ns` leitet 16,667 s daraus ab, dass ein einzelner Frame über
die **Gesamtdauer** unter 1000 ppm bleibt. Theil-Sen nimmt seinen Median aber
über die **Paarbasen**, und deren Median liegt bei rund einem Drittel der
Laufzeit. Die Quantisierungsschwelle sinkt deshalb erst ab ca. **50 s** unter
1000 ppm und erst ab ca. **100 s** unter 500 ppm — nicht ab 16,7 bzw. 33,3 s.
Zwischen ca. 17 s und 50 s ist das Gate also schärfer als beabsichtigt.
Belege aus der Historie: `172c0fa3` (16,53 s) misst 2754,8 ppm, exakt ein Frame
über seine mediane Paarbasis von 6,05 s — und entkam nur, weil es 0,14 s zu kurz
war. `66f24f53` (30,75 s) war gate-aktiv bei einer Quantisierungsschwelle von
1652,9 ppm und landete rein zufällig bei 0,0. Die Warnung bei `78ac253f`
(516,5 ppm) ist etwa ein halber Frame. Fix wäre, die Schwelle an die mediane
Paarbasis zu binden statt an die Gesamtdauer.

**(b) Keine Obergrenze für den Counterzuwachs im letzten Intervall.**
Seit 3a.1 bindet den Stop nur noch `sample_gaps_valid`: aktiver Abstand ≤ 5 s und
nicht fallender Counter. Wie weit der Stop-Counter der letzten Kalibrierprobe
vorauslaufen darf, prüft nichts mehr. Vorher tat das auch nichts — die alte
Prüfung griff nur bei Tickkollision, also bei 2 von 19 Läufen nach Zufall. Wer
die Grenze will, baut sie als **eigene explizite Prüfung gegen die letzte
Probe**, die dann für **alle** Läufe gilt.

**(c) Proposal-1.2 statt der gelockerten 1.1-Regel.**
Sauber wäre ein Bump auf 1.2 mit eigener Digest-Domain, analog zur
Outro-Einführung, die damals 1.0 → 1.1 ging und die Altbytes im 1.0-Zweig
gültig ließ. Berührt `_DIGEST_DOMAIN_*`, das `Literal["1.0","1.1"]`,
`review_app` und `docs\`. Danach dürfte 1.2 `intro_resolution` wieder fordern.

**(d) `minimum_keep_island` ist im Normalbetrieb nicht mehr erreichbar.**
Die 450-Frame-Zone deckt die 30-Frame-Inselgrenze (500 ms) vollständig ab: Was
die Inselregel hinter dem Lead-in verwerfen würde, ist längst
`intro_flow_protected`. Die Regel bleibt als Rückversicherung stehen und greift
erst, wenn `minimum_keep_island_ms` über 7,5 s gesetzt wird — der zugehörige
Test tut genau das, um sie überhaupt noch prüfen zu können. Beim Nachjustieren
der 450 im Blick behalten.

---

## 7. Repositoriumszustand und eine Falle

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        95f7956
origin      synchron
```

**Untracked und gefährlich:** `docs\repeat\matrix-auto-cutter-architecture-plan-v0.2.md`
ist **nicht** der Architekturplan, sondern die Übergabenotiz vom 7.8.
nachmittags unter dessen Dateinamen (14.977 B gegen 64.899 B, am 8.8. gemessen;
die früher hier genannten 63.418 B stehen in keiner Fassung — keine inhaltliche
Überschneidung). Wer sie für eine Kopie hält, überschreibt damit den kanonischen
Plan — und dessen Schemablock wird byte-genau getestet.

**Erste Handlung im nächsten Chat:** Diese Datei umbenennen — nicht löschen, der
Inhalt existiert nirgendwo sonst (in keinem Commit, keinem Stash, keinem
Reflog) — und die vorliegende Übergabe nach `docs\repeat\` legen.

*Erledigt am 8.8.:* umbenannt in
`docs\repeat\SHORTS-2-UEBERGABE-2026-08-07-nachmittag.md`, mit Kopfvermerk als
überholt gekennzeichnet; beide Dateien sind seither versioniert.

---

## 8. Befund über die Dokumentation

Unverändert gültig: **`docs\` ist gleichzeitig veraltet und vertragsbindend.**

*Veraltet:* „Product Runner" und `review_app` kommen in keinem Dokument vor;
beschrieben wird ein statisches `review.html`. `no_sidecar_safe_mode` ist
dokumentiert, aber nicht implementiert. Eine Startreihenfolge OBS/Runner ist
nirgends festgelegt (sie existiert erst seit heute als Skript).

*Vertragsbindend:* Der kanonische Schemablock in `v0.2` ab Zeile 287 wird
byte-genau gegen das exportierte Pydantic-Schema getestet
(`test_exported_schema_recursively_matches_canonical_sidecar_contract`). Zeile
160 und 289 sind freie Prosa und wurden heute nachgezogen.

**Für die Project Knowledge:** `UMGEBUNG.md`, `ABLAGE.md` und diese Übergabe.
Die vier Architekturdokumente **nicht** — bei Bedarf gezielt über Claude Code
lesen lassen, mit dem Hinweis, dass sie teils überholt sind.

---

## 9. Arbeitsweise — was sich am 7.8. bewährt hat

**Rein lesende Analyseaufträge vor jedem Eingriff.** Der Ertrag war heute
außergewöhnlich: Zwei Analyseaufträge haben je eine Prämisse des Auftrags selbst
widerlegt — den Zwei-Ausgänge-Verdacht und die Behauptung, das Snapping sei
ersatzlos entfernbar. Ohne diese Vorprüfungen wären zehn von dreizehn Läufen der
Historie gescheitert.

**„Nichts ausführen, was Zustand ändert" ist die richtige Formulierung.** Das
frühere „führe nichts aus" war zu grob und hat einen Agenten in eine unnötige
Grenzverletzung getrieben; `grep`, `git log` und Rechnen sind erwünscht.

**Der Agent hat dreimal von sich aus angehalten**, statt den Auftragsrahmen zu
überschreiten. Jedes Mal war das richtig. **Nicht wegoptimieren.**

**Berichte gegeneinander rechnen bleibt Pflicht.** Mehrere Zahlen aus früheren
Berichten und aus meinen eigenen Zusammenfassungen mussten korrigiert werden.

**Prioritätskonflikte:** Erst das Video herausbekommen, dann sauber reparieren.
Hat am Vormittag funktioniert.

---

## 10. Was in den neuen Chat gehört

Diese Datei. Und beim ersten Commit nach `docs\repeat\`, zusammen mit der
Bereinigung aus Abschnitt 7.

Erste drei Handgriffe morgen:

1. ~~Die falsch benannte Datei in `docs\repeat\` aufräumen.~~ **Erledigt am
   8.8.:** umbenennen — nicht löschen, der Inhalt existiert nirgendwo sonst.
2. ~~Die getesteten Stinger-Dateien in OBS einsetzen.~~ **Erledigt:** am 7.8.
   vom Nutzer von Hand eingesetzt. Offen bleibt nur das Neurendern (6.4).
3. Intro-Cut (6.1) als erster Auftrag — sauber spezifiziert, und die
   Journaldaten sind seit heute verlässlich. Bauvorgabe siehe Nachtrag vom 8.8.
   in 6.1 (Frameachse, nicht Journaluhr).
