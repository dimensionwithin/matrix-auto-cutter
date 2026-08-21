# Orchestrator-Übergabe — Matrix Auto Cutter, Shorts-Linie

Stand: 14. August 2026, abends
Vorgänger: `ORCHESTRATOR-UEBERGABE-2026-08-10.md` (derselbe Ordner)
HEAD: **`9399fd6`**, gepusht, Arbeitsbaum sauber

**Was diese Datei ist:** Die Übergabe an den nächsten Orchestrator-Chat. Sie
beschreibt Rolle und Arbeitsweise, den Stand der Shorts-Linie, die getroffenen
Entscheidungen samt Begründung, die Betriebsfallen und die offenen Punkte.

**Was sie nicht ist:** Ein Bauauftrag. Und keine Zusammenfassung der Berichte —
die liegen vollständig unter `artefakte\repeat\` und werden hier nur benannt.

---

## 1. Rolle und Arbeitsweise

Der Orchestrator-Chat baut nicht selbst. Er liest Berichte, rechnet Zahlen
nach, trifft Entscheidungen und **schreibt Aufträge für Claude-Code-Fenster**.
Der Nutzer schickt sie ab und bringt die Berichte zurück.

### 1.1 Jeder Auftrag nennt vier Dinge

Aus `SHORTS-1-UEBERGABE-2026-08-07.md` Abschnitt 3:

```
Modell:          Claude Sonnet 5 oder Claude Opus
Denktiefe:       niedrig / mittel / hoch
Berechtigungen:  was geaendert werden darf, was nicht
Begruendung:     warum diese Wahl
```

**Faustregel für die Modellwahl, in dieser Woche gefunden:**

- **Sonnet** für alles, wo die Antwort schon existiert — nachschlagen, messen,
  nach Vorgabe bauen, committen.
- **Opus** für alles, wo geurteilt werden muss — Muster finden, Widersprüche
  auflösen, offene Fragen entscheiden.

Die Markenanalyse und die Blindzerlegung liefen mit Opus und hoher Denktiefe;
beide haben Vermutungen des Orchestrators widerlegt statt sie zu bestätigen.
Das war den Aufpreis wert.

### 1.2 Jeder Auftrag trägt eine namentliche Verbotsliste

Nicht „die bestehende Pipeline nicht anfassen", sondern Dateinamen. Eine
namentliche Liste lässt sich nicht großzügig auslegen:

```
cut_proposal.py  intro.py  outro.py  protection.py  render.py  loudness.py
event_lag.py     product_runner.py   review_app.py  review.py  approval.py
src\matrix_auto_cutter\repeat\*
docs\matrix-auto-cutter-architecture-plan-v0.2.md
```

Das Architekturdokument wird ab Zeile 287 byte-genau gegen das exportierte
Pydantic-Schema getestet.

Der erlaubte Bereich für die Shorts-Linie:

```
src\matrix_auto_cutter\shorts\*
tests\test_shorts_*.py
pyproject.toml      nur einzelne Zeilen, ausdruecklich benannt
```

Geschützte Dateien werden **gelesen und importiert, nie kopiert**. Als
Eingriff 1 der Stufe-0-Nachbesserung die Freigabeprüfung brauchte, wurde sie
aus `approval.py` importiert — eine zweite Kopie derselben Sicherheitslogik
wäre still auseinandergedriftet.

### 1.3 Anhalten schlägt Raten

Die wichtigste Regel dieser Woche. Fünfmal hat ein Fenster angehalten statt zu
raten, und jedes Mal war es richtig:

- Suchpfad der Proposals war zu eng gefasst
- Datum des Regime-Bruchs im Auftrag falsch (8.8. statt 9.8.)
- Orchestrator-Übergabe lag untracked im Baum
- `ggml-medium.bin` fehlte, kein Download angestoßen
- Kriteriendatei trug noch Fassung 0.2

Jeder Auftrag bekommt deshalb einen Abschnitt **„Angehalten"** im Bericht, und
„nicht gefunden" ist ausdrücklich ein gültiges Ergebnis.

### 1.4 Aufträge tragen Prüfsteine, keine Vermutungen

Wo ein Ergebnis vorhersagbar ist, steht es im Auftrag. Beispiele, die
funktioniert haben:

- „Die drei Lagwerte müssen −116,625 / −8800,0 / −7466,625 ms reproduzieren"
- „Die Ausgabeframezahlen 2077, 2873, 5712 dürfen sich **nicht** ändern"
- „Alle 18 Zeilen müssen dieselben Zuordnungen zeigen wie vorher"

Der letzte ist der schärfste Testtyp: eine Änderung, die nichts ändern darf.

### 1.5 Frische Fenster

Jeder Auftrag geht an ein neues Fenster. Ein Fenster, das seine eigene
Schlussfolgerung nachprüft, prüft sie nicht. Die Aufträge sind deshalb so
geschrieben, dass sie ohne Vorwissen funktionieren — Pfade ausgeschrieben,
Erwartungswerte drin, Vorberichte namentlich genannt.

### 1.6 Was Fenster nicht prüfen können

Auftrag 23 meldete „zwei volle Durchläufe bestätigt". Beim Nutzer lief die
Schleife nicht. Die ferngesteuerte Browserumgebung verhält sich anders als
sein Rechner.

**Regel daraus:** Verhalten im Browser prüft der Nutzer. Fenster prüfen Code,
Zahlen und Dateien. Wenn ein Auftrag doch eine Browserprüfung braucht, gehört
die Auflage dazu, ausdrücklich zu benennen, was sich dort **nicht** zeigen
ließ.

---

## 2. Wo die Shorts-Linie steht

Der Stufenplan steht in `SHORTS-KONTEXT-2026-08-09.md` Abschnitt 8.

| # | Stufe | Stand |
|---|---|---|
| 0 | Werkzeug mit Dateiliste | **fertig**, `7857888` |
| 1 | Avatardatei nachschneiden | **fertig**, `853568f` |
| 2 | Kandidaten finden | **fertig**, `9399fd6` |
| 3 | Ausschnitt mit Mausverfolgung | offen |
| 4 | Untertitel | offen |
| 5 | Komposition und Endcard | offen |

### Stufe 0 — `matrix-auto-shorts`

Tk-Fenster, listet die 18 gerenderten Videos aus `F:\MatrixMarketAutoEdit\Rendered\`,
ordnet je Video Rohaufnahme, Sidecar, Proposal, Avatardatei und
Cursorprotokoll zu und schreibt auf Knopfdruck eine `shorts-job.json`.

Die Proposal-Auswahl ist **freigabegebunden** und prüft die Digestbindung über
`approval.py`. Ohne freigegebenen Kandidaten bleibt die Zeile `ungeklaert`
statt zu raten. Geratene Zuordnungen sind in der Anzeige als solche
gekennzeichnet.

### Stufe 1 — `avatar_cut.py`

Legt die freigegebene Schnittliste auf die separate Webcamaufnahme. Der
Versatz wird je Lauf per Kreuzkorrelation gemessen (`avatar_lag.py`, numpy);
er schwankt zwischen 6 und 528 Frames und ist **keine Konstante**. Schlägt die
Messung fehl, bricht der Lauf ab statt zu schätzen.

`frame_map.py` bildet Quellframes auf gerenderte ab — als **reine Funktion**,
weil dieselbe Rechnung später das Cursorprotokoll trägt.

### Stufe 2 — Kandidatensuche und Urteilsseite

Drei Teile, zwei davon gebaut:

1. **Transkript** der gerenderten Fassung mit `ggml-medium` (`transcript.py`)
2. **Zerlegung** in Kandidaten — läuft **vorerst in einem Claude-Code-Fenster**
3. **Urteilsseite** über lokalen Server (`judge.py`, `judge_server.py`)

Teil 2 ist bewusst nicht gebaut. Festgelegt ist das **Dateiformat**
dazwischen: `kandidaten.json` mit `start_ms`, `end_ms`, `titel`,
`begruendung`, `sicherheit`, `enthaelt`. Solange die Zerlegung ein Fenster
ist, schreibt es die Datei von Hand; später schreibt sie ein Programm. Teil 3
merkt den Unterschied nicht.

**Die Urteilssitzung** führt durch: erster offener Kandidat spielt an, läuft in
Endlosschleife wie ein Short, Tasten `1`/`2`/`3` urteilen und springen weiter,
Leertaste hält an. „Später" wandert einmal ans Ende. Urteile werden **nach
jedem Tastendruck** geschrieben, nicht am Schluss. Am Ende steht die Meldung
unten im Bild, mit dem Pfad der Datei.

Start:

```
uv run python -m matrix_auto_cutter.shorts.judge_server "<pfad>\shorts-job.json"
```

---

## 3. Entscheidungen, die nicht neu verhandelt werden

Alle mit Beleg. Wer sie ändern will, braucht einen neuen Beleg, nicht ein
neues Argument.

| Entscheidung | Grund |
|---|---|
| **`ggml-medium`**, nicht `small` oder `large-v3-turbo` | 0 entartete Zeitmarken, volle Abdeckung, Fachbegriffe korrekt. `turbo` fehlten 13,6 s am Ende und halluzinierte. `MODELLVERGLEICH-2026-08-14.md` |
| **Gerenderte Fassung** transkribieren, nicht die Rohaufnahme | Keine Musik, keine Stille, keine Halluzination, Ende auf 0,02 s genau — und keine Umrechnung mehr nötig. `TRANSKRIPTQUELLE-2026-08-14.md` |
| **Lokaler Server** statt Datei | Chrome verweigert `file://`-Seiten den Zugriff auf `file://`-Videos (`MEDIA_ELEMENT_ERROR: URL safety check`), zweifach belegt. Chrome mit abgeschalteter Prüfung wurde verworfen. |
| **Zerlegung im Fenster**, nicht als API-Aufruf | Solange der Nutzer jeden Vorschlag ohnehin sichtet, ist ein Fenster kein Umweg. Die Frage wird erst scharf, wenn nachts fertige Vorschläge dastehen sollen. |
| **numpy** ja, **scipy** nein | Kreuzkorrelation über FFT reicht. Der ffmpeg-Filter `axcorrelate` wurde geprüft und verworfen — seine Blocksemantik müsste aus dem Quelltext rückentwickelt werden. |
| **Mikrofonspur zurückgestellt** | Der Grund dafür war Halluzination über Musik — die ist durch die gerenderte Fassung gelöst. Und `render.py` würde eine zweite Tonspur **ablehnen** (siehe 4.3). |

---

## 4. Betriebsfallen

### 4.1 Tests nur über PowerShell

```
uv run python -m pytest      niemals: uv run pytest
```

Unter Git-Bash scheitert derselbe Aufruf mit „No module named pytest", obwohl
`PYTHONPATH` identisch gesetzt ist. Steht in `UMGEBUNG.md`.

Erwartet: **2088 bestanden, 1 vorbestehender Skip.**
`mypy src`: **genau 20 vorbestehende Fehler** — `repeat\cut.py` (3),
`repeat\cutcli.py` (7), `repeat\cli.py` (10). Diese Aufzählung wurde in drei
Berichten falsch zusammengefasst; sie gehört so in jeden Auftrag.

### 4.2 Die Datei `-`

Entsteht bei jedem Testlauf im Wurzelverzeichnis, 0 Bytes, harmlos, wird nie
committet. Bekannter Bestandsfehler (`SHORTS-1` Abschnitt 7): ein Test unter
`tests\phase2` schreibt nach `-` statt nach stdout. **Kein Grund anzuhalten.**

### 4.3 `render.py` lehnt eine zweite Tonspur ab

```python
if len(videos) != 1 or len(audios) != 1:
    return None      # E_RENDER_STREAMS
```

Eine Aufnahme mit zwei Tonspuren wird **nicht falsch gerendert — sie wird gar
nicht gerendert.** Wer in OBS eine Mikrofonspur einrichtet, ohne vorher den
Code zu ändern, bekommt ab der nächsten Aufnahme kein Video mehr durch die
Kette. **Code zuerst, OBS danach.**

Dazu: Spur 2 ist vom `Source Record`-Filter der Facecam belegt. Eine
Mikrofonspur müsste auf Spur 3+. Alle Details in `TRANSKRIPTQUELLE-2026-08-14.md`
Abschnitt C.

### 4.4 Product Runner

Läuft als `pythonw.exe`. Eine Suche nach dem Prozessnamen `matrix-auto-runner`
findet **strukturell nie etwas** — das wurde einmal als „Runner unverändert"
berichtet und war keine Aussage. Richtig:

```powershell
$s = "$env:LOCALAPPDATA\DimensionWithin\MatrixAutoCutter\product-runner\status.json"
$j = Get-Content $s -Raw | ConvertFrom-Json
Get-Process -Id $j.runner_pid -ErrorAction SilentlyContinue
```

Nach jeder Änderung an Code, den der Runner lädt, muss er **neu gestartet**
werden. Die Shorts-Module lädt er nicht.

### 4.5 whisper.cpp rechnet auf der CPU

`whisper_backend_init_gpu: no GPU found` — reiner CPU-Bau vom 28.04. Die
RTX 3060 liegt brach. `-t 8` von 12 Kernen, nicht mehr, der Nutzer arbeitet
weiter. `medium` braucht rund 10 Minuten für ein 17-Minuten-Video.

Ein CUDA-Neubau brächte Faktor 5 bis 10 — siehe offene Punkte.

### 4.6 Niemals `git clean -xfd`

Löscht `/artefakte/` mit, darunter die teuren whisper-Rohausgaben
(`ABLAGE.md`).

---

## 5. Was gemessen wurde und trägt

Zahlen, die als Grundlage taugen. Alle Berichte unter `artefakte\repeat\`.

**Tonversatz Bildschirm ↔ Avatar:** 18 Paare gemessen, 6 bis 528 Frames, kein
Drift innerhalb eines Laufs, exakt auf dem 60-Hz-Frameraster. Negativer Lag
heißt: **die Avataraufnahme hat später begonnen.** (Die Beschriftung in
`NACHMESSUNG-2026-08-10.md` ist an dieser Stelle invertiert; die Messungen
stimmen.)

**Pipelinetiefe springt innerhalb einer Aufnahme.** Bei zwei von vier
geprüften Läufen springt sie nach einem `scene_changed` um 40 bis 47 Frames
und bleibt oben. `pipeline_lag_frames` wird nur einmal beim Start gemessen und
kann das nicht erfassen. **Das betrifft den Cutter, nicht die Shorts** — der
Befund gehört in den Cutter-Chat, siehe offene Punkte.

**Grenzen liegen auf Sprechpausen.** 59 % der Nutzergrenzen innerhalb 1,5 s
einer Schnittkante gegen 26 % an Zufallspunkten; außerhalb von Erzählblöcken
83 %. Unabhängig bestätigt durch die Blindzerlegung, die die Schnittliste nie
gesehen hatte.

**Ein Sprachmodell findet die Kandidaten.** Blind zerlegt, ohne die
Nutzermarken zu kennen: 17 von 17 Marken getroffen. Im pausenlosen
Erzählblock, wo die Pausenmethode versagt, sechs Stücke statt einem.

**Die Kriterien stehen in `labels\repeat\shorts-kriterien.yaml`**, Fassung
0.3, abgeleitet aus 17 Marken und 21 echten Urteilen. Fünf belegte Kriterien,
vier widerlegte. Die widerlegten stehen mit drin, damit sie nicht
zurückkehren.

---

## 6. Offene Punkte

Nach Dringlichkeit, nicht nach Größe.

### 6.1 Der Cursor-Logger läuft nicht mit — blockiert Stufe 3

**Von 18 Videos hat genau eines ein Cursorprotokoll.** Der Start ist Handarbeit
vor OBS (`SHORTS-1` Abschnitt 1) und wird deshalb vergessen. Jede Aufnahme
ohne Protokoll ist für Stufe 3 dauerhaft verloren.

Stufe 3 lässt sich derzeit an genau einem Video bauen und an keinem anderen
anwenden. **Das gehört gelöst, bevor Stufe 3 anfängt** — entweder durch
Automatisierung des Starts oder durch eine Gewohnheit, die trägt.

Dazu die Ankergeste: Maus drei Sekunden in die linke obere Ecke direkt nach
Aufnahmestart. Sie macht die Wanduhr-Brücke pro Lauf geschenkt, statt sie
forensisch zu rekonstruieren.

### 6.2 Ein zweites Video durch die Kandidatensuche

Alles in der Kriteriendatei hängt an **einer** Aufnahme. 15 von 21 angenommen
ist eine hohe Quote — das kann heißen, dass die Zerlegung gut ist, oder dass
dieses Video besonders dicht ist.

Kosten: eine Zerlegung im Fenster, zwanzig Minuten Urteilen. Ergebnis: die
Kriterien tragen oder nicht.

### 6.3 Der Pipelinebefund gehört in den Cutter-Chat

`PIPELINETIEFE-2026-08-10.md` hat dafür einen eigenen Schlussabschnitt, der
ohne Shorts-Kontext lesbar ist. Zwei Ergänzungen gehören dazu:

- Die Stoppdifferenz geht über **alle 18 Läufe** auf, wenn man sie gegen die
  Tiefe beim **Stopp** rechnet statt beim Start.
- Die Lagged- und Skipped-Frame-Zähler von OBS trennen die beiden Deutungen:
  tiefere Pipeline oder verlorene Frames. Die Reparatur wäre je nachdem eine
  andere.

Das ist der erste harte Hinweis auf den Regime-Bruch vom 9.8., den seither
niemand erklären konnte.

### 6.4 `UMGEBUNG.md` in der Project Knowledge ersetzen

Die Datei in `docs\repeat\` trägt seit dem 10.8. den PowerShell-Absatz. Die
Kopie in der Project Knowledge ist ein eigener Upload und noch alt. Der
nächste frische Chat liest sonst den falschen Stand.

### 6.5 whisper.cpp mit CUDA neu bauen

Faktor 5 bis 10. Mehr Gewinn als jeder Modellwechsel, und die Modellfrage
wird dadurch billig. Braucht das CUDA-Toolkit und einen sauberen
Build-Durchlauf. **Der vorhandene Bau bleibt stehen**, bis der neue
nachweislich läuft und dieselben Wörter liefert — neuer Pfad daneben, nicht
darüber.

### 6.6 Untertitel für die Langvideos

`SHORTS-KONTEXT-2026-08-09.md` Abschnitt 4 sagt derzeit ausdrücklich, dass die
Long-Form-Videos **keine** bekommen. **Der Nutzer hat das am 14.8. revidiert:**
Er will sie später haben, Begründung Verständlichkeit. Technisch fällt es fast
ab, weil Transkript und Wortzeitstempel ohnehin entstehen.

### 6.7 Kleinigkeiten

- `find_proposal` sortiert `generated_at` **lexikalisch**. Geht gut, solange
  alle Zeitstempel dasselbe Format tragen. Mischt sich ein `Z` darunter,
  sortiert es still falsch.
- Der Cursor-Vorlauf in `shorts-job.json` heißt `lead_seconds` und misst gegen
  die **erste CSV-Datenzeile** (367,9 s), nicht gegen den Dateinamen (377 s).
  Wer die 9,06 s Loggeranlauf verwechselt, liegt in Stufe 3 daneben.
- Altlasten unter `artefakte\repeat\shorts\2026-08-07 11-35-16\`:
  `urteilsseite.html` (9 MB) und `urteil-clips\` werden nicht mehr benutzt.
- Bei „Später" auf dem **letzten** offenen Kandidaten kommt er sofort wieder,
  weil die Warteschlange sonst leer ist. Streng genommen richtig, sieht aber
  aus wie ein Fehlklick.

---

## 7. Was als Nächstes ansteht

Vorschlag, keine Festlegung:

1. **6.1 lösen** — Cursor-Logger, sonst ist Stufe 3 an ein Video gefesselt.
2. **6.2 machen** — zweites Video, prüft die Kriterien.
3. **Stufe 3 bauen** — Bildausschnitt mit Mausverfolgung. `frame_map.py` trägt
   das Cursorprotokoll bereits; die Glättung und der Rückfall bei negativem x
   (DISPLAY2 liegt bei `-2560,0`) stehen in `SHORTS-KONTEXT` Abschnitt 5.

6.3 und 6.4 sind Handgriffe des Nutzers und hängen an nichts davon.

---

## 8. Wo was liegt

```
docs\repeat\                      Uebergaben, Umgebung, Inventare (versioniert)
labels\repeat\                    Urteile, Vokabeldatei, Kriterien (versioniert)
artefakte\repeat\                 Berichte, Transkripte, Pruefskripte (ignoriert)

F:\MatrixMarketAutoEdit\          Rohaufnahmen und Sidecars
F:\MatrixMarketAutoEdit\Rendered\ die 18 gerenderten Videos
F:\ShortsQuellen\Avatar\          Webcamaufnahmen
F:\ShortsQuellen\Cursor\          Cursorprotokolle (genau eines)

%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\product-runner\
    artifacts\<recording_id>\proposals\<proposal_id>\cut-proposal.json
    sessions\*.json
    status.json
```

Die Berichte dieser Woche, in der Reihenfolge ihrer Entstehung:

```
shorts-kontext-inventar\SHORTS-KONTEXT-INVENTAR-2026-08-10.md
shorts-kontext-inventar\NACHTRAG-INVENTAR-2026-08-10.md
shorts-tonabgleich\TONABGLEICH-UND-ZEITBRUECKE-2026-08-10.md
shorts-tonabgleich\nachmessung\NACHMESSUNG-2026-08-10.md
shorts-lag-und-proposal\LAG-UND-PROPOSAL-2026-08-10.md
pipelinetiefe-lauf-2-und-7\PIPELINETIEFE-2026-08-10.md
shorts-stufe-0\BAUBERICHT-STUFE-0-2026-08-10.md
shorts-stufe-1\BAUBERICHT-STUFE-1-2026-08-11.md
shorts-markenanalyse\MARKENANALYSE-2026-08-11.md
shorts-blindzerlegung\BLINDZERLEGUNG-2026-08-11.md
shorts-modellvergleich\MODELLVERGLEICH-2026-08-14.md
shorts-transkriptquelle\TRANSKRIPTQUELLE-2026-08-14.md
shorts-stufe-2\BAUBERICHT-STUFE-2-2026-08-14.md
shorts-stufe-2\URTEILSSERVER-2026-08-14.md
shorts-stufe-2\SITZUNGSFEHLER-2026-08-14.md
```

**Nicht auf Vorrat lesen.** Gezielt nachschlagen, wenn eine Frage danach
verlangt — und dann sagen, warum.
