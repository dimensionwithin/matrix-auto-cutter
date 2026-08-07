# Matrix Auto Cutter — Übergabe an neuen Chat

Stand: 6. August 2026, abends
Vorgänger-Chat: REPEAT-2 (Aufräumen, REPEAT-2A bis 2E)
Alles abgeschlossen und gepusht.

---

## 1. Verifizierter Repositoryzustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        01457e0efa7cfff43101e73848bd2943dab34854
origin      synchron, Working Tree sauber
```

Commits dieses Arbeitsblocks, alle gepusht:

```
01457e0  fix: clamp snap window to file bounds, discard crossing snaps (2E)
3b43d81  feat: snap repeat cut points to measured silence   (2D)
2105722  feat: add post-render repeat cut with per-passage choice   (2C)
04628f8  feat: allow transcript reuse with source audio in repeat cli (2B)
6146dce  feat: add audio snippets and review html for repeat   (2A)
84b3a88  docs: consolidate repeat artefacts, environment and labels
a345955  ← Ausgangspunkt dieses Blocks
```

**1688 passed, 1 skipped.** Paket `repeat`: 100 % Coverage, 1400 Anweisungen.
Das Paket ist **dauerhaft isoliert** — kein bestehendes Modul importiert es,
es importiert nichts aus dem Produktpfad. Das ist eine Entwurfsentscheidung,
keine Übergangslösung.

**Bekannter Schönheitsfehler, nicht durch diese Arbeit verursacht:** Ein
Bestandstest unter `tests\phase2` schreibt nach `-` statt nach stdout und
legt dabei eine Datei namens `-` im Arbeitsverzeichnis an. Harmlos, aber
echter Fehler. Nicht ohne eigenen Auftrag reparieren.

---

## 2. Was jetzt funktioniert — die vollständige Kette

Der Repeat-Durchgang läuft **nach dem Rendern**, auf der fertigen Datei.
Das Schneideprogramm wird nicht angefasst.

```
1) uv run python -m matrix_auto_cutter.repeat.cli ^
     --source "<fertig-gerendert.mp4>" ^
     --whisper-binary "P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe" ^
     --whisper-model "P:\AI\whisper-data\models\ggml-small.bin" ^
     --threads 4 --work-dir <dir>\work ^
     --emit-transcript <dir>\transcript.json ^
     --out <dir>\diagnostics.json ^
     --snippet-dir <dir>\snippets --emit-review <dir>\review.html

   → Nutzer öffnet review.html, urteilt, lädt urteile.json herunter

2) uv run python -m matrix_auto_cutter.repeat.cutcli ^
     --source "<fertig-gerendert.mp4>" ^
     --urteile <dir>\urteile.json ^
     --out <dir>\final.mp4 --preset medium
```

**Warum auf der gerenderten Datei und nicht auf der Rohaufnahme:**
Der Renderer entfernt Passagen. Zeitstempel aus der Rohaufnahme stimmen
danach nicht mehr und müssten durch die Schnittliste des Renderers
umgerechnet werden. Auf dem Render sind die Zeitstempel bereits im
Koordinatensystem genau der Datei, die geschnitten wird. Keine Umrechnung,
Vertrauenskette unberührt, Durchgang jederzeit überspringbar.

**Module im Paket `repeat`:** `cli.py`, `cutcli.py` (zwei eigene
Einstiegspunkte), `cut.py` (Schnittplan), `snap.py` (Heranrücken),
`snippets.py`, `review.py`, `detect.py`, `boundary.py`, `asr.py`,
`audio.py`, `diagnostics.py`, `similarity.py`, `transcript.py`,
`utterances.py`, `whisper_json.py`, `process.py`, `errors.py`.

### Neu in diesem Block

- **`--transcript` und `--source` gemeinsam erlaubt** (2B). Transkript wird
  wiederverwendet, whisper läuft nicht, `--source` liefert nur das Audio für
  die Schnipsel. Das ist der Normalfall — whisper ist der teure Schritt.
- **Urteilsschema um `schnitt` erweitert** (2C): `"erste" | "zweite" |
  "beide" | null`. Fehlt das Feld, gilt `"erste"`. Die 25 alten Urteile
  bleiben gültig. Bei `bewusst`/`unsinn` immer `null`.
- **`review.html` mit zweiter Knopfreihe**: Taste 1 (Versprecher) blendet
  „erste raus / zweite raus / beide raus" ein, Tasten 4/5/6. Vorbelegt
  „erste", weil die zweite Passage den Gedanken weiterträgt.
- **Nachschnitt** (2C): ein ffmpeg-Lauf, `filter_complex trim/atrim/concat`,
  bildgenau, einmal kodiert. Encoder-Vorgaben aus `render.py` gelesen
  (nicht importiert): `libx264 -preset slow -crf 18 -profile:v high
  -pix_fmt yuv420p -c:a aac -ar 48000`.
- **Heranrücken an gemessene Stille** (2D): `silencedetect`, Fenster ±750 ms,
  Schwelle −35 dB, Mindestdauer 80 ms. Reihenfolge zwingend:
  **erst heranrücken, dann zusammenführen** — Verschieben kann getrennte
  Bereiche zur Überlappung bringen. `--no-snap` verhält sich byteidentisch
  zum Verhalten davor.
- **Zwei Randfälle geschlossen** (2E): Das Suchfenster wird auf
  `[0, duration_ms]` geklemmt, damit ein Schnitt nahe Dateianfang oder -ende
  nicht ins Leere sucht. Und würden sich die beiden Grenzen eines
  Entfernbereichs durch das Verschieben überkreuzen, wird das Heranrücken
  für **beide** Grenzen verworfen und der Bereich behält seine
  Originalwerte — kein halbes Heranrücken, keine negative Dauer. Die
  Überkreuzungsprüfung sitzt in `snap_urteile()` (`cutcli.py`), weil erst
  dort Start und Ende zusammen vorliegen; `snap_point()` bleibt eine reine
  Punktfunktion. Nachgewiesen: alle acht Verschiebungen und die Zieldauer
  851.555 ms blieben gegenüber 2D unverändert, `verworfen: 0`.

**Schutzmechanismen, alle nachgewiesen:** `--out` gleich `--source` → Exit 2.
Vorhandenes `--out` → Abbruch ohne Überschreiben. Quelldatei bleibt
nachweislich unverändert (Größe und mtime vor/nach verglichen).

---

## 3. Die Befunde, auf denen alles ruht

### Der Score trennt nicht — Schwellwert-Tuning ist erledigt

Aus 25 beurteilten Stellen: die beiden höchsten Scores überhaupt waren
**bewusste** Wiederholungen, ein echter Versprecher lag bei 0,600. Kein
Schwellwert trennt die Gruppen. Wer es erneut versucht, wiederholt einen
bereits gemessenen Fehler.

### Der Mensch im Kreis ist der Entwurf, kein Zwischenschritt

| Klasse | Beispiel | schneiden? |
|---|---|---|
| Neuansatz | „…seit Februar." / „Ja, die im Grunde erstmalig…" | ja |
| Versehentliches Echo | „in dem Sinne?" / „In dem Sinne, …" | ja |
| **Stilmittel** | „500, 700 Prozent… 500, 700 Prozent" | **nein** |

Klasse 2 und 3 sind **im Text prinzipiell nicht unterscheidbar** — der
Unterschied liegt in der Absicht. Ein perfektes Transkript ändert daran
nichts. Bestätigt am 6.8.: von zwei Kandidaten im echten Render waren
**beide** „bewusst".

Daraus folgt: nichts wird vorausgewählt, nichts ohne den Nutzer geschnitten.
Der dritte Detektor („Wiederholung innerhalb einer Äußerung") und das
Sammeln weiterer Etiketten sind **bewusst gestrichen**, nicht vergessen.

### Whispers Wortzeitstempel taugen nicht als Schnittpunkte

Median-Pause zwischen Wörtern: 0 ms. Das Ende eines Wortes ist rechnerisch
der Anfang des nächsten. Ein Schnitt darauf landet systematisch mitten im
Wort — genau das war beim ersten echten Nachschnitt zu hören. Nach dem
Heranrücken an gemessene Stille (Verschiebungen 54 bis 266 ms) urteilte der
Nutzer: **„keine Audioglitches, sauber getrennt."**

Die Zeitstempel sind gut genug für *„hier ist eine Wiederholung"*, nicht für
*„hier schneiden"*.

### Kandidaten je 1000 Wörter erkennt kaputte Transkripte

Aus dem Stapellauf über 23 Aufnahmen (15,03 Sprachstunden, 719 Kandidaten):

| Datei | Kand./1000 Wörter | Wörter/h |
|---|---:|---:|
| 2026-01-12 17-05-10 | 517 | 392 |
| 2026-04-30 21-21-15 | 117 | 1773 |
| 2025-11-11 20-02-33 | 34 | 2831 |
| *nächstbeste* | 7,8 | 10239 |
| übrige 19 | 0,24–6,2 | 4595–11702 |

Drei Whisper-Wiederholschleifen, saubere Trennlücke zwischen 34 und 7,8.
**Verbindliche Gesundheitsprüfung in jedem Stapelauftrag:**
Wörter/Stunde unter 4000 ist verdächtig, Kandidaten je 1000 Wörter über 10
bedeutet mit hoher Sicherheit eine Schleife. Die alte Schwelle von
1000 Wörtern/Stunde fing nur eine von drei.

Reale Ausbeute nach Abzug der drei: **150 Kandidaten aus 11,7 Stunden**,
knapp 13 je Stunde.

---

## 4. Offene Kleinigkeiten — beide ohne Produktcode-Änderung

### Lautheitsmessung

Verdacht des Nutzers: der Renderpfad macht die Tonspur bei jedem Durchlauf
leiser. Gemessen wurde bisher nur `volumedetect` auf dem Render:
`max_volume −2,9 dB`, `mean_volume −39,0 dB`. Der Mittelwert wird von
Sprechpausen nach unten gezogen und beweist nichts.

Zu messen ist **EBU R128 (LUFS)** über drei Generationen: Original →
Render → Nachschnitt. Alle drei Dateien existieren:
`F:\MatrixMarketAutoEdit\2026-08-04 01-11-36.mp4` (vermutet),
`F:\MatrixMarketAutoEdit\Rendered\2026-08-04 01-11-36.matrix-cut.mp4`,
`artefakte\repeat\2D\geschnitten-snap.mp4`.
Deutungsregel: unter 1 LUFS Differenz ist Messrauschen, darüber echter
Verlust. Verliert Original → Render mehr als 1 LUFS, dann rein lesend in
`src\matrix_auto_cutter\` nach Audiofiltern suchen (`volume`, `loudnorm`,
`dynaudnorm`, `acompressor`, `alimiter`, `-af`, Audioanteil in
`-filter_complex`) und Fundstellen mit Zeilennummer melden — **nicht
reparieren.**

### Vokabeltest

Whisper macht aus **Bärenmarkt** und **Bullenmarkt** beides
**„Beerenmarkt"**. Zwei entgegengesetzte Begriffe kollabieren auf denselben
Text — für Transkripte sinnverkehrt, für den Detektor eine Quelle von
Phantom-Wiederholungen.

`labels\repeat\whisper-vokabular.txt` enthält beide Wörter,
`--initial-prompt-file` existiert in der CLI. **Nie benutzt.**
Test: dieselbe Datei zweimal transkribieren, mit und ohne Vokabeldatei,
beide Transkripte auf „Beerenmarkt", „Bärenmarkt", „Bullenmarkt" vergleichen.
Fünf Minuten Rechenzeit. Prompt-Limit ca. 224 Token.

---

## 5. Ablage — gilt verbindlich

```
P:\DimensionWithin-MatrixMarketAutoEditor
├─ docs\repeat\        versioniert   Übergaben, UMGEBUNG.md, ABLAGE.md,
│                                    Inventare, Research-Report
├─ labels\repeat\      versioniert   urteile-2026-08-05.json (25 Urteile),
│                                    whisper-vokabular.txt
└─ artefakte\repeat\   ignoriert     Transkripte, whisper-Rohausgaben,
                                     Prototypen, Probeläufe, nacht\, 2A–2E\
```

- **Ausgaben von Läufen gehen nach `artefakte\repeat\<auftragsname>\`.**
  Nie auf den Desktop, nie nach `C:`.
- **`labels\` nicht nach `data\` verschieben** — `data/` steht in der
  `.gitignore` und würde die Etiketten unsichtbar ignorieren.
- **Niemals `git clean -xfd` in diesem Repository.** Der Befehl löscht
  `/artefakte/` mit, darunter die teuren whisper-Rohausgaben.
- `.gitignore` enthält seit diesem Block `/artefakte/` und `*.m4a`.

**Werkzeug, bewusst außerhalb des Repositorys:**
`P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe` (485.888 Bytes),
`P:\AI\whisper-data\models\ggml-small.bin` (487.601.967 Bytes).
Durchsatz 3,3–4,1× Echtzeit bei 4 Threads. `-ml 60` ist Pflicht.
`-ojf` schreibt die Rohausgabe neben die WAV, **sie überlebt jeden Absturz** —
nach einem Fehlschlag nie neu transkribieren, sondern nachkonvertieren.
Details in `docs\repeat\UMGEBUNG.md`.

**Nie neu transkribieren:** 27 vorhandene `audio.wav.json` unter
`artefakte\repeat\nacht\`, `\old\`, `\recheck\`, `\2C\`.

---

## 6. Arbeitsweise — bitte beibehalten

**Rollenverteilung.** Der Chat orchestriert und auditiert, Claude Code
implementiert. Der Chat schreibt keinen Produktionscode und committet nicht.

**Jeder Auftrag nennt:** erlaubten Änderungsbereich, verbotene Operationen,
Qualitätsgates mit exakten Befehlen, Berichtsanforderungen. Kein Commit im
selben Auftrag wie die Implementierung. Commit und Push sind eigene,
minimale Aufträge mit Scope-Prüfung als erstem Schritt und **Gate vor dem
Commit**, nicht danach.

**Modellwahl.** Haiku für Mechanik (Commit, Push, Listen). Sonnet für
Implementieren, Verifizieren, Stapelläufe. Opus nur, wenn der Produktpfad
selbst berührt wird — beim Repeat-Durchgang war das nie der Fall.

**Berichte prüfen, nicht glauben.** Zahlen gegeneinander rechnen. In diesem
Block hielt die Arithmetik durchgehend; die Fehler lagen in der *Deutung*
(eine kaputte Datei gemeldet statt drei) und in **meinen eigenen
Auftragstexten** (falscher Quellpfad, falsch benannter Überlappungsfall,
verwechselte Zeitstempel). Claude Code hat jedes Mal geprüft statt
angenommen und die Abweichung gemeldet — das ist das gewünschte Verhalten
und darf nicht wegoptimiert werden.

**Zum Nutzer.** Er liest die Claude-Code-Berichte nicht — das ist Aufgabe
des Chats. Er braucht: einen fertigen Prompt zum Kopieren, die
Modellempfehlung, und in zwei Sätzen was dabei herauskommt. Er will zügig
zum fertigen Produkt und schätzt Direktheit über Ausführlichkeit. Er ist zu
Recht vorsichtig, dass am funktionierenden Schnittprogramm nichts
kaputtgeht. Wenn er sagt, etwas klinge falsch, **stimmt das** — beim
Nachschnitt hat sein Gehör einen echten Entwurfsfehler gefunden, den keine
Kennzahl gezeigt hatte.

**Bei Nachtläufen:** Frühwarnung nach *jeder* Datei, Aufträge über zehn
Minuten als Hintergrundlauf, vor jedem Start auf verwaiste `whisper-cli.exe`
und `ffmpeg.exe` prüfen, nach jeder Datei `audio.wav` löschen und
`audio.wav.json` behalten.

---

## 7. Der nächste Block: Livestream → Video → Shorts

REPEAT ist abgeschlossen und wird geparkt. Der nächste Schritt bringt mehr
ins Produkt: aus Livestream-Aufnahmen Videos machen, aus Videos Shorts.

**Eine Lehre gilt dort unverändert:** Shorts-Auswahl ist wieder ein
Urteilsproblem. „Welche 60 Sekunden sind gut?" entscheidet keine Maschine —
sie schlägt Kandidaten vor, der Nutzer wählt. Wird das von Anfang an so
gebaut statt auf einen Automatismus zu hoffen, spart es die Runde, die
dieser Block gerade hinter sich hat.

Der Repeat-Durchgang ist dafür eine brauchbare Bauform: eigenständiger
Einstiegspunkt, arbeitet auf jeder fertigen Videodatei, Produktpfad
unberührt, jederzeit überspringbar.

---

## 8. Was in den neuen Chat gehört

Diese Datei. Alles andere liegt im Repository unter `docs\repeat\` und
`labels\repeat\` oder auf der Platte des Nutzers und kann bei Bedarf
angefordert werden.
