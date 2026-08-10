# Übergabe an den nächsten Orchestrator

Stand: 9. August 2026, abends. Gilt ab dem 10.8. früh.

---

## Was du bist und wie hier gearbeitet wird

Du orchestrierst. Du hast **keinen** Zugriff auf das Repositorium
`P:\DimensionWithin-MatrixMarketAutoEditor`, auf `F:\`, auf OBS oder auf die
laufenden Prozesse. Alles, was dort passiert, läuft über Claude Code, und du
schreibst dafür die Aufträge. Der Nutzer führt sie aus und bringt dir die
Berichte zurück.

Vier Regeln, die sich am 9.8. mehrfach bezahlt gemacht haben:

**Rein lesende Vorprüfung vor jedem Eingriff.** Kein Bauauftrag ohne Messung
davor. Am 9.8. hat das dreimal verhindert, dass eine plausible Erklärung als
Tatsache in den Code wandert.

**Berichte gegeneinander rechnen.** Zahlen aus zwei Dokumenten vergleichen,
Arithmetik nachrechnen, Widersprüche benennen. Mehrere echte Fehler des Tages
kamen so ans Licht — darunter zwei in Vorgaben, die der Orchestrator selbst
geschrieben hatte.

**Aufträge dürfen ihre eigene Prämisse widerlegen.** Schreib in jeden
Analyseauftrag ausdrücklich hinein, dass „Verdacht widerlegt" ein gültiges
Ergebnis ist und nicht plausibel gefüllt werden darf. Das hat am 9.8. die
Browserquellen-Hypothese gekippt, auf die sich Nutzer und Orchestrator schon
festgelegt hatten.

**Ein bestandener Lauf widerlegt keinen Fehler — er hat ihn womöglich nicht
getroffen.** Dreimal am 9.8. wahr gewesen. Wenn ein Fehler von einer
Restklasse, einem Zustand oder einer Zufallsgröße abhängt, muss die
Gegenprobe alle Zustände abdecken.

Der Nutzer arbeitet direkt, wird bei Reibung auch mal deutlich, und will
Klartext statt Beschwichtigung. Wenn du falsch lagst, sag es und nenn die
Zahl, die dich widerlegt. Wenn eine Anweisung von dir nicht funktioniert hat,
korrigier sie ohne Umschweife — PowerShell-Befehle bitte vorher zweimal
durchdenken, mehrere sind am 9.8. an Kleinigkeiten gescheitert und haben Zeit
gekostet.

---

## Was mitkommt

Vier Dateien gehören in die Project Knowledge des neuen Chats:

| Datei | Inhalt |
|---|---|
| `UMGEBUNG.md` | Pfade, Werkzeuge, Betriebsfallen |
| `ABLAGE.md` | Ablagekonventionen |
| `SHORTS-3-UEBERGABE-2026-08-09.md` | Cutter-Stand, am 9.8. abends ergänzt |
| `SHORTS-KONTEXT-2026-08-09.md` | Shorts-Entscheidungen — **ersetzt Abschnitt 4.2** der SHORTS-3-Übergabe |

`SHORTS-KONTEXT-2026-08-09.md` gehört außerdem nach `docs\repeat\` und ist
dort noch nicht abgelegt. Das ist eine der ersten Handlungen.

Nicht vorliegend, aber im Repositorium: `SHORTS-1-UEBERGABE-2026-08-07.md` in
`docs\repeat\`. Sie regelt Shorts-Entwurf, Ablage und Arbeitsweise und lag
bisher keinem Chat vor. **Vor dem ersten Shorts-Bauauftrag beschaffen.**

---

## Cutter-Stand

`HEAD = 2430568`, origin synchron, 1845 Tests grün, ruff sauber, mypy 20
vorbestehende Fehler in `repeat\*`.

Am 9.8. sind vier Commits entstanden, alle abgenommen:

| Commit | Inhalt |
|---|---|
| `a87b2a1` | `loudnorm` — Renderkette auf −14 LUFS, Kompressor, Limiter, zwei Durchgänge |
| `5f70713` | Längenfehler der Lautheitskette — `loudnorm` füllte auf volle 100 ms auf, jeder dritte Render wäre an der Dauerprüfung gescheitert |
| `787eb72` | `PermissionError WinError 5` beim atomaren Schreiben — vier Abstürze an einem Tag |
| `2430568` | Takt-Korrektur der Frameabbildung, `INTRO_CUT_OFFSET_FRAMES = 85`, Proposal 1.2 |

### Was das im Betrieb heißt

Die Kette läuft durch: Aufnahme → Stopp → Journal → Sidecar → Proposal →
Review-Fenster → Render → verifizierte MP4 in `F:\MatrixMarketAutoEdit\Rendered\`.
Der Ton kommt bei rund −14 LUFS heraus statt bei −31 bis −36. Der Intro-Cut
sitzt am Anfang der Chart-Animation, unabhängig vom Takt-Lag.

Der Intro-Cut rechnet jetzt `Szenenmarke + Takt-Lag + 85`. Der Takt-Lag steht
je Lauf im Sidecar unter `recording_started.clock_sample.monotonic_ns` und
nimmt über alle vermessenen Läufe **nur zwei Werte** an: rund 267–283 ms
(Lag 16/17) oder rund 1033–1050 ms (Lag 62/63). Warum es zwei diskrete
Zustände sind und keinen Zwischenwert gibt, ist unerklärt.

---

## Betriebsfallen — das Teuerste vom 9.8.

**Der Runner lädt neuen Code nur nach einem echten Neustart.**
`START-MATRIX-AUTO-CUTTER.cmd` meldet „läuft bereits" und tut nichts, wenn
noch ein Runner lebt. Die Zeile „Product Runner wird im Hintergrund
gestartet" belegt **keinen** Neustart. Einziger Beleg ist
`runner_starting` → `Runner startet.` im Log. Am 9.8. lief der Runner
dadurch von 12:07 bis 15:02 auf altem Code, quer über einen Commit hinweg —
und hat jede Messung dieses Zeitraums verfälscht. Bei jedem unerklärlichen
Verhalten ist das die erste Frage.

**Das Log liegt im Unterordner `logs\`:**
`%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\product-runner\logs\runner.log`

Nützlicher Filter:

```powershell
$log = (Get-ChildItem "$env:LOCALAPPDATA\DimensionWithin\MatrixAutoCutter" -Recurse -Filter runner.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
Get-Content $log -Tail 400 | Select-String -Pattern "Runner startet|läuft bereits|Lautheit: I|render_verifying|render_succeeded|render_failed|WinError" | Select-Object -Last 12
```

**`E_RENDER_VERIFY` benennt nicht, welche der drei Teilbedingungen gefallen
ist** (Medienprofil, Dauer, Decode) — dasselbe Muster wie `sidecar.clock_gate`.
Faustregel aus Messungen: Verifikation unter einer Sekunde heißt Profil oder
Dauer, mehrere Sekunden heißen echter Decode.

**Änderungen an den OBS-Quellen sehen wie Cutter-Fehler aus.** Am 9.8. dreimal:
umgestellte Tonspuren, ein geänderter Szenenübergang, gesetzte Haken an
Webquellen. Der Render läuft durch, erst die Verifikation oder die Zahlen
fallen. Bei jeder unerklärten Verschiebung ist „was wurde in OBS geändert" eine
Standardfrage.

**Änderungen an OBS gehören mit Uhrzeit notiert.** Die Szenensammlung führt
keine Historie, und die `.bak` wird bei jedem Speichern überschrieben. Am 9.8.
ist dadurch ein Regimewechsel von rund 70 Frames dauerhaft unerklärbar
geblieben.

**Die OBS-Logs rotieren schnell weg** — zehn Sitzungen, am 9.8. verschwand
während der Arbeit das älteste. Bei einem Befund, der ältere Läufe betrifft,
sofort sichern.

**`uv run python -m pytest`, niemals `uv run pytest`.**

---

## Offene Punkte am Cutter, nach Wert

**Gegenprobe bei Lag 63.** Die Takt-Korrektur ist an Läufen mit Lag 16/17 am
Bild abgenommen. Der große Lag ist nur rechnerisch belegt. Beim nächsten Lauf
mit `monotonic_ns` um 1 049 999 958 das erste Bild ansehen: Es muss genauso
aussehen wie bei kleinem Lag. Eintragen im Baubericht und in Abschnitt 3.3 der
Übergabe. Kostet nichts, kommt von selbst.

**True Peak +0,82 dBTP.** Im Lauf 17:30 lag der True Peak über null, bei allen
anderen zwischen −0,97 und −1,41. Die neue Lautheitswarnung hat ihn gefangen —
sie ist an diesem Tag zum ersten Mal produktiv und richtig gefeuert. Ursache
unbekannt, gehört nachgesehen, bevor ein Video damit veröffentlicht wird.

**Regime-Bruch vom 9.8. vormittags, unerklärt.** Zwischen 6:36 und 7:27 sprang
der Inhaltsanteil des Intro-Stings um rund 70 Frames, und es entstand eine
Schwarzlücke von 21–28 Frames, die es vorher nicht gab. Ausgeschlossen sind:
die HTML-Seite (letzte Änderung 11.06.2026), das Repositorium (die Lücke steckt
im Quellvideo), die Haken an der Browserquelle. Offen bleibt die
Szenensammlung, die keine Historie führt. **Springt die Sting-Phase zurück,
wandert `INTRO_CUT_OFFSET_FRAMES` mit, der Lag nicht.**

**Kompressor wirkt schwach auf Material ohne Ausreißer.** Lauf 08-43-22 gab
0,84 dB Crestgewinn für 2,97 dB Pegelverlust, Lauf 07-28-18 dagegen 4,93 für
1,9. Verdacht: 5 ms Attack gegen 120 ms Release bei Schwelle −24 dB. Wartet auf
eine Messreihe über mehrere Läufe.

**Die 1,5-dB-Schranke hat nur 0,2 dB Luft.** Über fünf Läufe streut das
Ergebnis um 1,91 dB (−13,39 bis −15,30). Beim ersten Fehlalarm gegen diese
Reihe rechnen, nicht die Schranke reflexhaft aufmachen.

**`protection.py` ist richtig verdrahtet, wirkt aber auf heutigen Daten
nicht** — die betroffenen Ereignistypen werden nie abgesetzt. Kein Fehler, nur
etwas, das man nicht für wirksam halten sollte.

**Frameverlust-Term** bleibt unkorrigiert, per Test dokumentiert. Selten (ein
Lauf von 17), dann aber 45 Frames groß. Nicht „klein".

**Getrennte Mikrofonspur in OBS.** Der Source-Record-Filter schneidet die
fertige Mischung mit, nicht das Mikrofon. Solange das so ist, ist keine Frage
der Art „Mikrofon oder Systemton" nachträglich entscheidbar. Klein, aber es
macht künftige Läufe diagnostizierbar — und der Shorts-Block erbt die
Einschränkung.

**Aufnahmepegel.** 17 bis 21 dB ungenutzte Aussteuerung, aber der Bus stand
beim langen Lauf schon bei −0,5 dBTP. Anheben lässt sich nur einzelne Quellen,
und welche, weiß man erst mit getrennten Spuren. Reihenfolge: erst
Spurentrennung, dann Aufnahmepegel.

**Stinger neu rendern.** Kleiner Umfang. Wichtige Richtigstellung vom 9.8.: Es
gibt **keine** Kopplung an `INTRO_CUT_OFFSET_FRAMES` — die alte Herleitung
„35 + 113 Stinger" war falsch, der Intro-Sting verschiebt die Konstante, der
Stinger nicht. Abnahme: letzter Videoframe gleich Containerlänge, Matte
vollständig zurückgezogen, konstante 60 fps.

**Kleinkram.** Die Datei namens `-` im Wurzelverzeichnis entsteht bei jedem
Testlauf und ist ungefährlich.

### OBS-Änderungen vom 9.8., für spätere Rätsel

- `Intro with Cam` stand bis etwa 16:30 auf Übergang **Schnitt**, seitdem auf
  Übergang 100 ms. Alle anderen Szenen standen schon auf 100 ms.
- Der TruthPill Rotator lief früher auf 45 fps, wurde am 9.8. auf 60 gestellt —
  **Zeitpunkt nicht mehr rekonstruierbar**, Kandidat für den Regime-Bruch, aber
  rechnerisch kein glatter Treffer (33 % gegen 56 %).
- Haken an den Webquellen um etwa 11:30 und erneut um 16:28/16:29 geändert.
- Der Haken „Deaktivieren, wenn Quelle nicht sichtbar ist" an der EndCard war
  kurzzeitig gesetzt und ist wieder entfernt — die Netzquelle hatte damit nicht
  mehr abgespielt.

---

## Shorts

Der vollständige Stand steht in `SHORTS-KONTEXT-2026-08-09.md`. Nichts davon
ist gebaut. Die Datei ersetzt Abschnitt 4.2 der SHORTS-3-Übergabe und gehört
nach `docs\repeat\`.

Drei Beiträge aus der Cutter-Seite, die dort noch fehlen:

**Shorts erben `loudnorm`.** Abschnitt 10 lässt das offen — es ist geklärt:
`loudnorm` sitzt im Audiozweig hinter `concat`, alles in `Rendered\` trägt
bereits −14 LUFS. Aber ein 30–60-Sekunden-Ausschnitt misst anders als die
Gesamtdatei; am 9.8. gemessen −14,34 gegen −15,30 im selben Video. Für Shorts
heißt das: messen, nicht annehmen. Und wenn ein Short eine Passage trifft, die
die Kompression stark angefasst hat, kann das Ergebnis abweichen.

**Der Tonabgleich für die Avatardatei funktioniert.** Abschnitt 7 will die
Synchronität „einmal über den Ton gegenprüfen". Das geht gut, weil der
Source-Record-Filter die **fertige Mischung** mitschneidet, nicht das
Mikrofon — beide Dateien tragen dasselbe Signal.

**Die Schnittliste liegt im Proposal, aber die Achse hat sich geändert.**
Proposal ist seit `2430568` auf Version **1.2** mit eigener Digest-Domäne und
dem Takt-Lag als Pflichtfeld. Wer die Schnittliste aus `cut-proposal.json`
auf die Avatardatei anwendet, muss wissen, welche Version er vor sich hat.

---

## Richtungsentscheidungen vom 9.8. abends

Nichts davon ist gebaut, nichts davon ist ein Auftrag. Es sind Festlegungen
zur Reihenfolge und zwei Gedanken, die die Bauweise beeinflussen.

### Formatreihenfolge

**Erst das Chart-Format vollständig durchautomatisieren — einschließlich
Shorts. Danach Livestreams. Danach weitere Formate, jedes für sich mit
seinen Shorts.**

Begründung des Nutzers: Ein Format bis zum fertigen Short durchzuziehen
zwingt dazu, den ganzen Weg einmal wirklich zu gehen. Erst dabei zeigt sich,
was eine spätere Verallgemeinerung überhaupt enthalten muss. Drei Formate
parallel anzudenken erzeugt eine Abstraktion für Fälle, die noch niemand
gebaut hat.

### Livestream → Video, nicht Livestream → Shorts

**Entschieden:** Wenn Livestreams drankommen, wird eine Strecke gebaut, die
aus einem Stream ein Video in `Rendered\` macht — mit derselben Datenlage wie
eine Cutter-Aufnahme. Die Shorts-Pipeline bekommt es dann geschenkt, ohne
eine Zeile über Streams zu wissen.

Dasselbe Argument, mit dem die Shorts an `Rendered\` hängen: Was in der
Renderkette gebaut wird, kommt geschenkt dazu. Eine zweite Strecke
Livestream → Shorts wären zwei Wege zum selben Ziel, die getrennt gepflegt
werden und getrennt kaputtgehen.

Damit wird die offene Frage klein und benennbar: nicht „wie baue ich Shorts
aus Streams", sondern nur „was fehlt einem Stream, um dieselbe Form zu haben
wie eine Cutter-Aufnahme" — Journal, Cursorprotokoll, Sidecar. Ob der
Producer beim Streamen mitschreibt, ist ungeklärt und entscheidet, ob das
eine kleine Sache ist oder ein eigenes Vorhaben.

Bleibt offen und gehört dorthin, nicht in die Shorts: Ein Stream ist lang und
hat keine Szenenstruktur wie ein geplantes Video. Ob die Stille-Erkennung
darauf sinnvoll arbeitet, weiß niemand.

### Szenenabhängige Regeln — angedacht, nicht beschlossen

Der Nutzer will weitere Formate über OBS-Szenen unterscheiden und dem Cutter
pro Szene ein anderes Verhalten geben. Drei Fälle wurden benannt:

| Szene | gewünschtes Verhalten |
|---|---|
| Chart | wie heute |
| Reaktion | **keine Schnitte** — es läuft fremder Ton, Schnitte in dessen Pausen zerschneiden das Material, auf das reagiert wird |
| Decode | mehr Luft **nach** dem gesprochenen Wort; Stille ist dort Lesezeit, kein Loch |

Die Maschinerie dafür steht großenteils schon: Jedes Ereignis im Sidecar
trägt `protection_level`, `buffer_before_ms`, `buffer_after_ms`,
`allows_global_mastering`, `blocks_local_audio_repair`, `blocks_overlays`,
`blocks_time_edits` — heute überall gleich befüllt. Und `intro.py` und
`outro.py` sind bei Licht besehen zwei fest verdrahtete Szenenregeln. Was
fehlt, ist eine versionierte Regeldatei statt zweier Sonderfälle im Code.

**Dieselbe Zuordnung steuert die Shorts-Komposition.** Reaktion heißt fremder
Bildschirm oben, Avatar unten. Decode heißt, der Mauszeiger wird zur
Hauptsache und die Verfolgung aus Shorts-Stufe 3 trägt das Bild statt es nur
zu verbessern. Chart heißt das Layout aus `SHORTS-KONTEXT-2026-08-09.md`.
Cutter und Shorts lesen dieselbe Tabelle von zwei Seiten. Das spricht dafür,
die Regeldatei **vor** Shorts-Stufe 3 und 5 zu bauen — sonst entstehen zwei
Kompositionen fest verdrahtet und müssen später auseinandergenommen werden.

**Drei Vorbehalte, die vor dem Bau geklärt gehören:**

Schlüssel ist die **Szenen-UUID, nicht der Name**. Das Label fehlt in 16 von
21 Journalen, und Szenen werden umbenannt — am 9.8. belegt, `Stinger - Outro`
wurde zu `Stinger - normal`. Eine Regel am Namen greift bei drei von vier
Aufnahmen nicht, und zwar still.

**Die Szenen gibt es noch nicht.** In den 29 vorhandenen Sidecars kommen nur
Intro with Cam, Charts und Outro vor. Bevor Regeln für Reaktion und Decode
entstehen, müssen echte Aufnahmen in diesen Szenen vorliegen — sonst werden
Zahlen festgelegt, an denen sich nichts prüfen lässt. Genau das ist mit den
148 des Intro-Cuts passiert.

**Zeitpunkt.** Eine Regeldatei fasst `intro.py`, `outro.py`, `protection.py`
und `cut_proposal.py` an — dieselben Dateien, die am 9.8. abends frisch
abgesichert wurden, und dieselben, die Shorts-Stufe 1 liest. Nicht
gleichzeitig mit Shorts-Stufe 0 und 1 bauen.

Auch beim reinen Chart-Format gilt schon jetzt: Szenenwerte gehören an eine
Stelle beisammen, nicht verstreut im Code. Nicht als fertiges Regelsystem —
nur so, dass später eine Zeile pro Szene reicht.

---

## Erster Auftrag im neuen Chat

Bevor irgendetwas gebaut wird, fehlt Repositoriums-Kontext, den keiner der
bisherigen Chats hatte. Schreib als Erstes einen rein lesenden Auftrag für
Claude Code, der ihn beschafft. Er sollte mindestens abdecken:

- `SHORTS-1-UEBERGABE-2026-08-07.md` aus `docs\repeat\` — Inhalt
  zusammenfassen, besonders Shorts-Entwurf, Ablage und Arbeitsweise.
- Welche Shorts-bezogenen Bausteine im Repositorium schon existieren:
  Transkriptionslauf, `--prompt`-Vokabulardatei in `labels\repeat\`,
  Cursorprotokoll-Format, Designsystem (`tokens.css`, `design-system.md`).
- Wo `cut-proposal.json` liegt, wie die Schnittliste darin aufgebaut ist und
  was sich mit Proposal 1.2 geändert hat.
- Ob es bereits ein zweites Programm neben dem Product Runner gibt, an das
  sich das Shorts-Werkzeug anlehnen könnte.
- Format und Ablage der Cursorprotokolle und der Webcamdateien in
  `F:\ShortsQuellen\Avatar\`.
- Ob Newsreader und JetBrains Mono inzwischen installiert sind.

Der Auftrag ist rein lesend, ohne Codeeingriff und ohne Commit. Erst mit
seinem Bericht steht fest, welche der sechs Stufen aus Abschnitt 8 überhaupt
Bauarbeit brauchen und welche schon halb dastehen.

Danach in dieser Reihenfolge: Stufe 0 (Werkzeug mit Dateiliste), Stufe 1
(Avatardatei nachschneiden — der erste Stolperstein). Jede Stufe für sich
ansehbar, kein großer Wurf. Das ist die Regel, die am 9.8. den Intro-Cut in
vier Anläufen gerettet hat.
