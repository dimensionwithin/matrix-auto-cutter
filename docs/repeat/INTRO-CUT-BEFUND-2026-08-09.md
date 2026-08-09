# Intro-Cut — Befund zur Fehlplatzierung des Schnitts

> **Korrigiert am 9.8. abends.** Die Messungen dieses Berichts sind
> unverändert gültig, die Zuschreibung des Inhaltsanteils war falsch: nicht die
> Browserquelle baut das Intro auf, sondern die Medienquelle
> `intro-sting-sovereign-1440p.webm`. Betroffen sind 1, 2, 5.2, 5.3 und 10, jeweils
> an Ort und Stelle richtiggestellt. Herleitung in
> `SCHRITT-1-TAKTKORREKTUR-VORBEREITUNG.md`, Abschnitt 0.

Stand: 9. August 2026, nachmittags
Anlass: Der Intro-Schnitt landet mitten im Übergang statt am Intro-Anfang.
`INTRO_CUT_OFFSET_FRAMES = 148` wurde am 9.8. vormittags an einem Lauf
nachjustiert und trifft seither nicht mehr.

**Diese Datei ist eine reine Messung. Es wurde nichts geändert** — kein Code,
kein Commit, keine Einstellung in OBS, keine Aufnahme angefasst. Alles darin ist
aus Journalen, OBS-Logs, der Szenensammlung und dem dekodierten Bildmaterial
gelesen oder gerechnet.

Kontaktbögen zu den Messungen liegen in
`artefakte\repeat\intro-cut-befund\` (nicht versioniert).

---

## 1. Der Befund in einem Absatz

Die Differenz zwischen der Journalmarke und dem Punkt, an dem das Intro
wirklich steht, **streut über 121 Frames — von 139 bis 260, also gut zwei
Sekunden.** Eine feste Zahl kann das nicht treffen. Die Streuung hat aber zwei
Ursachen, und die vermutete ist nur die kleinere. Der größere Teil, 47 Frames,
ist ein Takt-Versatz: `mapped_source_frame` liegt in jedem Lauf um genau die
Aufnahme-Anlaufzeit zu früh, und die springt zwischen 267 ms und 1050 ms. Dieser
Betrag steht bereits im Sidecar und ist berechenbar. Der Rest ist der Vorlauf
der Medienquelle `intro-sting-sovereign-1440p.webm` in der Intro-Szene (siehe
Richtigstellung in 5.2; ursprünglich stand hier das Neuladen der
Browserquelle). Er ist real und groß, schwankt von Lauf zu Lauf aber nur um
±6 Frames — und hat sich am 9.8. vormittags einmalig um rund 70 Frames
verschoben und damit die 148 entwertet.

---

## 2. Was gemessen wurde

Für jeden Lauf mit einer `scene_changed`-Marke auf „Intro with Cam" wurde das
Quellvideo ab `mapped_source_frame - 30` dekodiert (160×90, Graustufen) und vier
Größen bestimmt, alle relativ zur Marke:

| Größe | Definition |
|---|---|
| erster Bildwechsel | erste messbare Abweichung vom Bild unmittelbar vor der Marke |
| Schwarzlücke | zusammenhängende Frames mit Luminanz ≈ 0 |
| Bild wieder da | erster Frame nach der Lücke mit normaler Szenenhelligkeit |
| **1. Karte steht** | erster Frame, ab dem die erste Karte 20 Frames am Stück vollständig steht |

**„1. Karte steht" ist der Referenzpunkt dieser Messreihe.** Er markiert den
Moment, in dem der Aufbau abgeschlossen ist. Die anderen drei Größen sind
Zwischenschritte, die zeigen, woraus sich der Weg dorthin zusammensetzt.

> **Zwei spätere Einschränkungen.** Erstens stammt der Aufbau aus der
> Sting-Datei, nicht aus der Browserseite (5.2). Zweitens ist die stehende
> Karte **nicht** der Zielpunkt für den Schnitt: abgenommen wurde der Anfang
> der Chart-Animation bei rund 5 % der Linie, also rund 100 Frames früher.
> Als gemeinsamer, eindeutig messbarer Bezugspunkt für den Vergleich der
> 17 Läufe bleibt die Karte trotzdem brauchbar.

Als Datenbasis dienten alle 29 Sidecars in `F:\MatrixMarketAutoEdit\`; davon
tragen 17 eine verwertbare Intro-Marke. Zusätzlich wurden alle 58 Szenenwechsel
dieser Läufe vermessen, nicht nur die Intro-Wechsel.

---

## 3. Die Messreihe

| Lauf | Marke | Takt-Lag | 1. Bildwechsel | Schwarz | Bild da | **1. Karte steht** | Karte − Lag |
|---|---:|---:|---:|---:|---:|---:|---:|
| 07-08 11-35-16 | 3079 | 17 | +8 | – | +8 | **+140** | 123 |
| 07-08 20-48-23 | 497 | 62 | +55 | – | +55 | **+183** | 121 |
| 08-08 07-28-18 | 1425 | 17 | +12 | – | +12 | **+139** | 122 |
| 08-08 20-28-17 | 536 | 16 | +14 | – | +14 | **+139** | 123 |
| 09-08 06-28-58 | 11530 | 16 | +15 | – | +15 | **+144** | 128 |
| 09-08 07-25-37 | 439 | 17 | +16 | 21 F | +44 | **+204** | 187 |
| 09-08 07-29-51 | 782 | 17 | +16 | 28 F | +52 | **+214** | 197 |
| 09-08 07-54-23 | 419 | 62 | +61 | 28 F | +96 | **+259** | 197 |
| 09-08 08-14-05 | 268 | 17 | +15 | 27 F | +50 | **+204** | 187 |
| 09-08 08-42-28 | 258 | 62 | +61 | 28 F | +96 | **+259** | 197 |
| 09-08 08-43-22 | 475 | 62 | +60 | 28 F | +96 | **+259** | 197 |
| 09-08 12-09-50 | 357 | 17 | +17 | 22 F | +46 | **+205** | 188 |
| 09-08 14-14-08 | 2164 | 62 | +61 | 28 F | +97 | **+260** | 198 |
| 09-08 14-15-47 | 329 | 62 | +61 | 28 F | +97 | **+260** | 198 |
| 09-08 14-31-51 | 288 | 63 | +61 | 28 F | +97 | **+259** | 196 |
| 09-08 15-10-19 | 284 | 17 | +16 | 28 F | +52 | **+214** | 197 |
| 09-08 15-15-43 | 448 | 16 | +15 | 28 F | +51 | **+214** | 198 |

**Spannen**

| | min | max | Spanne |
|---|---:|---:|---:|
| 1. Karte steht (absolut) | 139 | 260 | **121 Frames** |
| Takt-Lag | 16 | 63 | 47 Frames |
| Karte − Lag, bis 9.8. früh | 121 | 128 | 7 Frames |
| Karte − Lag, ab 9.8. 7:25 Uhr | 187 | 198 | 11 Frames |

Die Streuung ist also **nicht** ein diffuses Rauschen einer Ladezeit, sondern
die Summe aus einem sauber berechenbaren Sprung (47 Frames) und einem
einmaligen Regimewechsel im Seitenaufbau (rund 70 Frames), mit sehr wenig
Restrauschen darin.

---

## 4. Der Takt-Versatz

Die Spalte „Takt-Lag" ist **keine Bildmessung**, sondern eine Rechnung aus dem
Sidecar:

```
recording_started.clock_sample.monotonic_ns × 60 / 1e9
```

Der Wert nimmt über alle 17 Läufe genau zwei Größenordnungen an:

| `monotonic_ns` | = Frames | Anzahl Läufe | gemessener 1. Bildwechsel |
|---:|---:|---:|---|
| 266 666 656 / 283 333 322 | 16 / 17 | 10 | 8 … 17 |
| 1 033 333 292 / 1 049 999 958 | 62 / 63 | 7 | 55 … 61 |

Rechnung und Bildmessung decken sich Lauf für Lauf auf 0 bis 3 Frames. Die
Differenz 1033 ms − 283 ms = 750 ms entspricht exakt 45 Frames — genau dem
Sprung, den das Bild zeigt. Über 17 Läufe ist das keine Koinzidenz.

**Der Versatz ist nicht szenenabhängig.** Innerhalb eines Laufs gilt er für
jeden Szenenwechsel gleich:

| Lauf | Intro with Cam | Charts | Outro | weitere |
|---|---:|---:|---:|---|
| 09-08 07-54-23 (Lag 62) | +61 | +59 | +59 | |
| 09-08 14-31-51 (Lag 63) | +61 | +60 | +61 | |
| 09-08 12-09-50 (Lag 17) | +17 | +17 | +15 | |
| 09-08 15-15-43 (Lag 16) | +15 | +15 | +16 | Desktop +17, Gaming +17 |
| 08-08 07-28-18 (Lag 17) | +12 | +14 | +14 | |

Damit ist der große, streuende Anteil eindeutig **nicht** der Browserquelle
zuzuschreiben.

### 4.1 Deutung

Als Schluss gekennzeichnet, nicht als Messung: `mapped_source_frame` ist
`output_frame_count - 1`. Der Zähler, den der Adapter beim Frontend-Callback
abgreift, hinkt dem, was der Compositor gerade rendert, um die Pipeline-Tiefe
hinterher — und die Pipeline-Tiefe *ist* die Anlaufzeit, die `recording_started`
misst. Die Marke landet deshalb systematisch um diesen Betrag zu früh auf der
Frameachse.

`SHORTS-3-UEBERGABE-2026-08-09.md` nennt diesen Startversatz mit „rund 283 ms",
und `intro.py` im Modulkopf ebenso. **Beide behandeln ihn als Konstante. Er ist
keine** — er liegt zwischen 267 ms und 1050 ms.

### 4.2 Frameverlust als zweiter Versatz

`finalization.warnings` trägt Einträge wie `frame_loss: 45 frames at 4.0 s`.
Liegt ein Verlust vor der Marke, verschiebt er sie zusätzlich, und zwar mitten
im Lauf. Beleg: `2026-08-09 08-14-05` hat 45 verlorene Frames bei 4,0 s. Der
Intro-Wechsel (Marke 268) misst +15, die späteren Wechsel zu Charts (1667) und
Outro (6173) messen +61 und +59 — also +15 plus genau die 45 verlorenen Frames.
Hier lag der Verlust knapp hinter der Intro-Marke und blieb folgenlos; bei einem
etwas früheren Szenenwechsel wären es 45 Frames Fehler gewesen.

---

## 5. Die Browserquelle

### 5.1 Sie liegt in der Zielszene

Geladene Sammlung ist `Unbenannt.json` mit `current_scene: Intro with Cam`.
Deren Szene-UUID `df50e171-befb-4d89-b9e9-66a29dd0865e` ist identisch mit
`INTRO_SCENE_UUID` in `intro.py`. Inhalt der Szene:

```
Intro with Cam:
  Starting Intro      ffmpeg_source    D:/OBS/Musik/Starting/Smart Boys (Edit).mp3
  TruthPill Rotator   browser_source   <- hier
  Facecam Avatar      window_capture   (Chroma Key, Source Record)
  Intro - Sting       ffmpeg_source    intro-sting-sovereign-1440p.webm  (6,046 s)
  Stinger - normal    ffmpeg_source    Stinger_synchron - Outro.webm     (1,877 s)
  ABO                 ffmpeg_source    abo_alpha.webm  (restart_on_activate)
  Kommentar           ffmpeg_source    kommentar_alpha.webm
```

Einstellungen der Quelle:

```json
{"is_local_file": true,
 "local_file": "P:/tpc rotator/Rotator Discord n8n/truth_pill_rotator_master.html",
 "width": 2560, "height": 1440, "fps": 60, "fps_custom": true,
 "shutdown": true, "restart_when_active": true}
```

`shutdown` ist „Deaktivieren, wenn Quelle nicht sichtbar ist",
`restart_when_active` ist „Browser bei Szenenaktivierung aktualisieren". Beide
gesetzt, wie vermutet.

Dieselbe Quelle steckt außerdem in Intro, Charts (im Group „Rotator"),
Desktop / Browser Chilling und Gaming. Outro hat stattdessen „EndCart" mit
denselben zwei Haken. Die Vorszene ist in allen protokollierten Läufen „Intro"
(OBS-Logs, letzter Szenenwechsel vor `==== Recording Start ====`) — also
Intro → Intro with Cam, beide mit derselben Browserquelle, die beim Umschalten
trotzdem neu lädt.

*Randnotiz:* `global.ini` nennt als aktive Sammlung „CleanValo 2", zu der es
keine `.json` mehr gibt, nur noch `.bak` und `.v1`. Geladen und laufend
geschrieben wird `Unbenannt.json`, deren Szenenbaum sich Zeile für Zeile mit dem
OBS-Log von 15:17 Uhr deckt. Der `global.ini`-Eintrag ist veraltet und für die
Frage ohne Belang.

### 5.2 Was sie anrichtet — und was nicht

> **Richtigstellung.** Der Aufbau nach der Schwarzlücke — eintippende Kopfzeile
> „DIMENSION WITHIN — MARKET CYCLE PROTOCOL", sich zeichnende Chartlinie, dann
> die Karte — stammt **nicht** von der neu geladenen Browserseite, sondern aus
> der Medienquelle `intro-sting-sovereign-1440p.webm` in derselben Szene. Die
> Datei ist 364 Frames lang und trägt die Karte ab ihrem Frame 187, exakt dort,
> wo der unten gemessene Inhaltsanteil liegt. Drei Belege: die Übereinstimmung
> auf 0 bis 11 Frames; die Karte „Zyklen statt News." ist in jedem Lauf
> dieselbe, während die Rotator-Karten der Vorszene wechseln; und die Quelle
> startet per OBS-Standard mit der Szenenaktivierung neu. Die beiden Haken an
> der Browserquelle wurden erst gegen 11:30 Uhr gesetzt und änderten am
> Inhaltsanteil einen Frame.

Die Zeit vom ersten Bildwechsel bis „Karte steht" beträgt

- 121 … 128 Frames in den Läufen bis 9.8. früh,
- 187 … 198 Frames ab dem 9.8., 7:25 Uhr.

Innerhalb eines Regimes schwankt sie also um 7 beziehungsweise 11 Frames. Für
diesen Anteil ist eine feste Zahl angemessen — sie ist nur seit dem 9.8. früh
die falsche feste Zahl.

### 5.3 Der Bruch am 9.8. vormittags

Bis einschließlich `06-28-58` (6:36 Uhr) gibt es beim Intro-Wechsel **gar keine
Schwarzlücke.** Die Seite lud auch damals neu — der Chart zeichnet sich auch
dort von null —, aber das Bild blieb dabei dunkelgrau statt schwarz. Ab
`07-25-37` (7:27 Uhr) ist es 21 bis 28 Frames vollständig schwarz, und der
Aufbau dauert 65 bis 70 Frames länger.

Zwischen 6:36 und 7:27 Uhr am 9.8. hat sich also etwas geändert.

> **Richtigstellung.** Die hier ursprünglich vermutete Ursache — der Haken
> „Deaktivieren, wenn Quelle nicht sichtbar ist" neu gesetzt — ist widerlegt:
> beide Haken waren um 11:22 Uhr noch leer und wurden erst gegen 11:30 Uhr
> gesetzt, lange nach dem Bruch. Was sich messen lässt, ist die Phase des
> Stings gegenüber dem sichtbaren Szenenanfang: sie sprang von −65 auf 0 bis
> +11 Frames.

**Die Ursache ist unerklärt.** Ausgeschlossen sind Rechnerneustart (letzter
Systemstart 4.8., 21:52 Uhr), Treiber- und GPU-Wechsel, OBS 32.1.2,
obs-browser 2.26.8, das Journal-Plugin, die Encodereinstellungen und die
Sting-Datei selbst (11.6.2026). Übrig bleibt allein die Szenensammlung, und
genau für die gibt es keine Historie: die OBS-Logs reichen nur bis 7:53 Uhr
zurück, `Unbenannt.json` wird laufend überschrieben. Ebenfalls unerklärt
bleiben die 21 bis 28 Frames reines Schwarz — keine der vier Quellen der Szene
kann sie liefern.

---

## 6. Kein späteres Signal im Journal

**Es gibt keins.**

Der Adapter reicht genau zwei Ereignistypen an den Producer weiter:
`EventType::recording_started` (`obs_adapter.cpp:1033`) und
`EventType::scene_changed` (`obs_adapter.cpp:1177`), dazu Pause-, Resume- und
Stop-Records. Das Enum in `journal_producer.hpp:77` kennt zwar zusätzlich
`intro_started`, `intro_ended`, `stinger_started`, `stinger_ended`,
`outro_started`, `outro_ended` und `manual_protection`, und `event_name()` kann
sie benennen — **abgesetzt wird keines davon.** Über alle 29 Sidecars kommen nur
`recording_started`, `scene_changed`, `recording_paused`, `recording_resumed`
und `recording_stopped` vor.

OBS selbst kennt auf Frontend-Ebene ebenfalls nichts, was „Browserseite fertig
gemalt" markieren würde; der Adapter hängt nur an
`OBS_FRONTEND_EVENT_SCENE_CHANGED` und den Output-Signalen
(`obs_plugin.cpp:418`).

Für den *größeren* der beiden Anteile steht die Information aber schon im
Journal: `recording_started.clock_sample.monotonic_ns`. Kein späteres Ereignis,
aber eine vorhandene, laufindividuelle Größe.

---

## 7. Was 148 damals getroffen hat

Im alten Regime stand die Karte bei +139 bis +144. **148 lag knapp dahinter —
genau am Intro-Anfang.** Die Zahl war richtig kalibriert.

Ihre Herleitung im Kommentar ab `intro.py:45` stimmt allerdings nicht: „35
Frames Rest der Vorszene plus 113 Frames Stingerlänge". Einen 1,877-s-Wisch gibt
es beim Intro-Wechsel nicht. Der Szenenübergang der Sammlung ist „Schnitt" mit
100 ms (`current_transition`); `Stinger_synchron.webm` läuft hier nur als
Medienquelle *innerhalb* der Szenen und als Quell-Übergang in Charts, Desktop
und Gaming. Die Zahl trifft, die Begründung nicht — und deshalb führt auch die
Warnung „ein neu gerenderter Stinger verschiebt diese Zahl" in die Irre.

### 7.1 Wo die Renders tatsächlich anfangen

Jeder gerenderte Lauf wurde gegen seine Quelle gematcht (erster Renderframe
gegen alle Quellframes, Pixelabstand, Minimum):

| Render | Marke | Renderstart | = Marke + | Restabweichung |
|---|---:|---:|---:|---:|
| 07-25-37 | 439 | 474 | **+35** | 0,00 |
| 07-29-51 | 782 | — | **+17 … +44** | mehrdeutig |
| 07-54-23 | 419 | 567 | **+148** | 0,05 |
| 08-14-05 | 268 | 416 | **+148** | 0,06 |
| 08-43-22 | 475 | 623 | **+148** | 0,05 |
| 12-09-50 | 357 | 505 | **+148** | 0,05 |
| 14-31-51 | 288 | 436 | **+148** | 0,05 |
| 15-10-19 | 284 | 432 | **+148** | 0,06 |
| 15-15-43 | 448 | 596 | **+148** | 0,06 |

`07-29-51` lässt sich nicht eindeutig zuordnen: Frame 0 bis 5 des Renders haben
Luminanz exakt 0,0, der Start liegt in der Schwarzlücke, und dort sind alle
Frames identisch. Vermutlich +35 wie beim Nachbarlauf. Die Renders von
`11-35-16` und `07-28-18` beginnen bei Quellframe 0 — dort lief der Intro-Cut
noch nicht.

Sichtbar heißt das: `14-31-51` (Lag 63, Karte bei +259) beginnt mit einem fast
leeren Bild, in dem der Chart erst anfängt, sich zu zeichnen. `15-15-43` und
`12-09-50` (Lag 16/17, Karte bei +205 bis +214) beginnen mit einem schon zu zwei
Dritteln gezeichneten Chart. Beides ist mitten im Aufbau, das erste deutlich
schlimmer. Siehe `render-vergleich-0754-0814-1209.png` und
`render-14-31-51-erste-120-frames.png`.

### 7.2 Korrektur zur Ausgangsannahme

Der Vormittagslauf `07-54-23` hatte bereits Lag 62 und startet im Render exakt
so fehlplatziert wie `14-31-51`. **Der Fehler trat am 9.8. vormittags also schon
auf** — nur nicht bei den Läufen mit kleinem Lag (`08-14-05`, später
`12-09-50`), die dadurch akzeptabel aussahen. Nachmittags hatten alle drei Läufe
(14-14, 14-15, 14-31) den großen Lag, und damit war er durchgängig sichtbar.

---

## 8. Ansatzpunkt im Code

Die einzige Stelle ist `intro.py:237`:

```python
marker = event.mapped_source_frame
start = marker + INTRO_CUT_OFFSET_FRAMES
```

Hier addiert eine feste Konstante auf eine Marke, die zwei verschiedene Fehler
enthält. Sinnvoll ist eine Trennung in drei Terme.

**Term 1 — Takt-Korrektur, aus dem Sidecar berechenbar.** Alles Nötige ist da:
`sidecar.events` enthält das `recording_started` mit
`clock_sample.monotonic_ns`, und `sidecar.source.fps_num` / `fps_den` stehen in
`models.py:531`. Die Korrektur ist
`round(monotonic_ns × fps_num / (fps_den × 1e9))` und deckt 47 der 121 Frames
Streuung ab.

> Diese Korrektur gehört genau genommen **nicht** nach `intro.py`, sondern in
> die Frameabbildung selbst. Sie betrifft jede Marke, auch die des Outros und
> jede Schutzzone. Als lokale Korrektur im Intro ist sie eine Reparatur an der
> falschen Stelle — sie wirkt dort aber sofort, und der Outro-Schnitt liegt am
> Ende einer langen Szene, wo 45 Frames weniger auffallen.

**Term 2 — Nachladen der Szene.** Bleibt eine gemessene Konstante wie heute, nur
eine andere: 187 bis 198 statt 121 bis 128. Aus dem Journal ist sie nicht
ableitbar (Abschnitt 6), und mit ±6 Frames Restschwankung ist eine Konstante
hier auch angemessen. Sie muss aber neu gemessen werden, sobald sich an der
Szene oder an der Seite etwas ändert — was am 9.8. vormittags passiert ist, ohne
dass es aufgefallen wäre. Diese Abhängigkeit gehört in den Kommentar, an die
Stelle der Stinger-Herleitung.

**Term 3 — Frameverlust vor der Marke.** `finalization.warnings` trägt die
Verluste (Abschnitt 4.2). Ob dieser Term tragbar ist, hängt daran, ob
`warnings` im validierten Modell erreichbar ist — das wurde nicht zu Ende
verfolgt.

Der Kommentarblock ab `intro.py:45` muss in jedem Fall mit: die Herleitung
„35 + 113 = 148" beschreibt einen Übergang, den es an dieser Stelle nicht gibt.

---

## 9. Belege

Kontaktbögen in `artefakte\repeat\intro-cut-befund\`:

| Datei | Inhalt |
|---|---|
| `quelle-12-09-50-ab-marke.png` | Quelllauf ab Marke, alle 20 Frames — Vorszene, Schwarz, Seitenaufbau, erste Karte |
| `quelle-08-08-07-28-18-ab-marke.png` | Kalibrierlauf des alten Regimes, alle 10 Frames — Neuladen ohne Schwarzlücke |
| `quelle-07-08-20-48-23-ab-marke.png` | alter Lauf mit großem Takt-Lag (62) |
| `quelle-06-28-58-ab-marke-aufgehellt.png` | letzter Lauf vor dem Bruch, aufgehellt — Seite blank, aber nicht schwarz |
| `render-14-31-51-erste-120-frames.png` | Render mit Lag 63 — beginnt am Anfang des Chart-Aufbaus |
| `render-15-15-43-erste-120-frames.png` | Render mit Lag 16 — beginnt beim zu zwei Dritteln gezeichneten Chart |
| `render-vergleich-0754-0814-1209.png` | drei Renders im Vergleich, Frames 0/30/60/90 |

Datenquellen: 29 Sidecars in `F:\MatrixMarketAutoEdit\`, Renders in
`F:\MatrixMarketAutoEdit\Rendered\`, OBS-Logs in
`%APPDATA%\obs-studio\logs\` (reichen bis 9.8. 7:53 Uhr zurück),
Szenensammlung `%APPDATA%\obs-studio\basic\scenes\Unbenannt.json`.

---

## 10. Fazit

Der ursprüngliche Verdacht trifft nicht. Die Browserquelle liegt zwar in der
Zielszene und lädt bei jedem Umschalten neu, aber den Intro-Aufbau liefert sie
nicht — das tut die Medienquelle `intro-sting-sovereign-1440p.webm` mit ihren
364 Frames, Karte ab Frame 187. Dieser Anteil ist seit dem 9.8. vormittags um
rund 70 Frames nach hinten gerutscht und deshalb wurde 148 zu klein; innerhalb
eines Regimes schwankt er aber nur um ±6 Frames.

Die eigentliche Streuung von 47 Frames kommt aus dem Takt:
`mapped_source_frame` liegt in jedem Lauf um genau die Aufnahme-Anlaufzeit zu
früh, und die springt zwischen 267 ms und 1050 ms.

Eine feste Größe scheitert also aus zwei Gründen gleichzeitig — und nur einer
davon lässt sich aus dem Journal reparieren.
