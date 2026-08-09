# Matrix Auto Cutter — Übergabe an den nächsten Arbeitschat

Stand: 9. August 2026, vormittags
Vorgänger-Chat: SHORTS-2 (Doku-Bereinigung, Intro-Cut, Clock-Gate, Proposal-1.1)

**Diese Datei ergänzt `SHORTS-2-UEBERGABE-2026-08-07-abend.md`, ersetzt sie
aber nicht.** Jene Datei ist am 9.8. nachgezogen worden und weiterhin gültig;
sie enthält den ausführlichen Stand zu Stop-Kette, Uhrenmetrik, Startautomatik,
Review-Fenster und Stinger. Diese hier beschreibt, was seither passiert ist,
und woran als Nächstes gearbeitet wird.

**Für den Shorts-Block zusätzlich beschaffen:** `SHORTS-1-UEBERGABE-2026-08-07.md`
aus `docs\repeat\`. Sie regelt Shorts-Entwurf, Ablage und Arbeitsweise und lag
den letzten beiden Chats nicht vor.

---

## 1. Der Stand in einem Absatz

Eine Aufnahme läuft ohne Handgriff durch, und seit dem 9.8. schneidet der
Cutter auch das Intro selbst. Am Vormittag mehrfach in echt bewiesen: Aufnahme,
Stopp, Journal, Sidecar, Proposal, Review-Fenster von selbst, Render — erster
Frame des fertigen Videos ist der Introbeginn, der Einstieg bis zum ersten Wort
bleibt ungeschnitten. Drei Commits, alle auf `origin/master`. Testsuite von
1711 auf 1739.

---

## 2. Was am 8./9.8. committet wurde

| Commit | Inhalt |
|---|---|
| `3a1abb9` | Doku: falsch benannte Übergabenotiz umbenannt, Abenddatei korrigiert |
| `6aa6a59` | Doku: Abschnitt 6.4 nachgezogen (Stinger in OBS eingesetzt) |
| `840fcf3` | Intro-Cut, Clock-Gate, Proposal-1.1, Review-Zeile, UMGEBUNG.md |

### 2.1 Intro-Cut (`840fcf3`)

Gebaut als Spiegelbild des Outro-Mechanismus, in `cut_proposal.py` unmittelbar
beim Outro-Block. Neu: `src\matrix_auto_cutter\intro.py`.

Zwei Konstanten, beide in `intro.py`, beide am Ergebnis nachjustiert:

- **`INTRO_CUT_OFFSET_FRAMES = 148`** — 35 Frames Rest der Vorszene (gemessen)
  plus 113 Frames Stingerlänge (`Stinger_synchron.webm`, gemessen 1,877 s).
  Ergibt 2,467 s. **Ein neu gerenderter Stinger verschiebt diese Zahl.**
  → **Überholt. Seit dem 9.8. abends 85, und die Herleitung war falsch;
  siehe Abschnitt 3.3.**
- **`INTRO_FLOW_PROTECTED_FRAMES = 450`** — 7,5 s feste Einstiegszone hinter dem
  Schnitt, halboffen `[intro_start, intro_start+450)`. Stille-Kandidaten, die
  darin *beginnen*, werden **ganz verworfen**, nicht gekürzt. Der Nutzer
  gestaltet den Einstieg bewusst.

Zeitachse ist die **Frameachse** über `mapped_source_frame`, nicht `monotonic_ns`
— die beiden Uhren liegen rund 283 ms auseinander (Startversatz zwischen
Ausgangssignal und erstem Videoframe). → **Der Startversatz ist keine
Konstante: gemessen 267 ms bis 1050 ms. Siehe Abschnitt 3.3.**

Bindung über das Label „Intro with Cam", Rückfallebene `scene_uuid`
`df50e171-befb-4d89-b9e9-66a29dd0865e`. Erstes Vorkommen gewinnt. Fehlt das
Label, passiert nichts und der Lauf läuft normal weiter — das ist der häufigere
Fall (16 von 21 Journalen). Marke auf Frame 0: kein Versatz, es gibt dort keine
Vorszene, aus der der Stinger wischen könnte.

**Warum die feste Zone die abgeleitete ersetzt hat:** Der erste Entwurf leitete
das Zonenende aus den `silencedetect`-Intervallen ab. Das ist in echt
gescheitert, weil hinter dem Intro-Cut die Intromusik läuft — keine Stille, Zone
leer, und die Pause vor dem ersten Wort war ungeschützt und wurde geschnitten.
`silencedetect` trennt laut von leise, nicht Sprache von Musik; ein „erstes
Wort" ist daraus nicht ableitbar.

### 2.2 Clock-Gate: Stop-Probe aus der Residualschleife (`840fcf3`)

Der `stop`-Record kann seinen QPC-Tick mit der letzten `calibration_sample`
teilen. `map_qpc_frame` findet dann per `bisect_left` die Kalibrierprobe,
interpoliert den Stop auf deren Counter und verbucht die Stop-Latenz des
A/V-Interleavers als Uhrenresidual. Real aufgetreten: `382a196c` 483,3 ms,
`89c344e6` 283,3 ms — gegen 50 ms Schranke.

Ein Münzwurf, kein Grenzfall: 17 von 19 Journalen hatten ihren Stop in einem
eigenen Tick und maßen 0,00 ms. Die Stop-Latenz selbst ist mit 3 bis 121 Frames
über die ganze Historie normal.

Fix: Die Stop-Probe ist aus der Residualschleife heraus — mit derselben
Begründung, mit der `_samples_without_stop` sie längst aus der Driftschätzung
nimmt (counterverankert, also zirkulär). Sie bleibt **Stützstelle der
Interpolation**, ist nur kein Messpunkt mehr. 50-ms-Schranke, `MAX_DRIFT_PPM`
und `DRIFT_WARNING_PPM` unverändert. Alle 19 Journale messen danach 0,00 ms.

**Nachtrag 9.8. abends:** Diese Datei nannte den Bezeichner zuerst
`_drift_samples`. Den gibt es nicht. Er heißt `_samples_without_stop` und steht
in `src\matrix_auto_cutter\phase2\finalizer\sidecar_builder.py:193`; die
SHORTS-2-Übergabe hatte ihn in §3a.1 richtig. Oben korrigiert.

**Korrektur zu einer alten Erfolgsmeldung:** `89c344e6` galt seit `5292107` als
gerettet, wurde aber weiterhin abgelehnt — nur auf der Residualhälfte statt der
Drifthälfte. Erst seit diesem Fix läuft der Lauf durch. Merksatz:
`sidecar.clock_gate` benennt **nicht**, welche der beiden Teilbedingungen
gefeuert hat.

### 2.3 Proposal-1.1 hat zwei Gestalten

Die 1.1-Regel verlangte kurzzeitig Outro **und** Intro-Resolution, ohne
Schemabump. Damit wurde jedes bereits geschriebene 1.1-Proposal unladbar. Die
Regel fordert wieder nur die Outro-Resolution; frisch erzeugte Proposals tragen
`intro_resolution` immer, alte bleiben lesbar.

**Merksatz: Wer Proposal-1.1 liest, darf `intro_resolution` nicht
voraussetzen.** Der sauberere Weg wäre ein Bump auf 1.2 mit eigener
Digest-Domain, siehe offene Punkte. → **Am 9.8. abends gebaut, siehe
Abschnitt 3.3.**

---

## 3. Der nächste Schritt: `loudnorm`

Höchster Wert, steht seit drei Übergaben, betrifft jeden einzelnen Zuschauer.

**Befund:** Die gerenderten Videos liegen bei **−35,8 LUFS**. YouTube zielt auf
−14 LUFS und regelt nur herunter, nie herauf. Das sind rund **20 dB zu leise**.

**Nachtrag 9.8. abends — die −35,8 sind ein Einzelwert.** Sie stammen aus der
Messreihe vom 6.8. (`artefakte\repeat\lautheit\`, drei Dateien einer Kette,
−35,76 / −35,74 / −35,85). Über die Läufe hinweg streut der Wert erheblich:

| Lauf | integriert | True Peak |
|---|---|---|
| 2026-08-08 07-28-18 | −30,97 LUFS | −0,50 dBTP |
| 2026-08-09 08-43-22 | −31,78 LUFS | −11,85 dBTP |
| 2026-08-09 08-14-05 | −35,14 LUFS | −11,02 dBTP |
| Messreihe 06.08. | −35,76 LUFS | −2,37 dBTP |

Also **17 bis 21 dB** zu leise, nicht durchgehend 20. Wer eine feste Anhebung
plant, plant an der Streuung vorbei — und am True Peak, der zwischen −0,50 und
−11,85 dBTP liegt. Genau deshalb misst die Kette pro Lauf, statt zu rechnen.

**Ziel:** Ein eigener `loudnorm`-Schritt am Ende der Renderkette.

**Vorprüfung, die zuerst geklärt werden muss — rein lesend:**

Wo sitzt die letzte ffmpeg-Stufe der Renderkette, und wird dort neu kodiert oder
mit `-c copy` zusammengesetzt? Das entscheidet über den Aufwand:

- Wird ohnehin neu kodiert → `loudnorm` ist ein Filter mehr, praktisch gratis.
- Wird mit `-c copy` zusammengesetzt → eine zusätzliche Kodierstufe für die
  Tonspur. Dann ist die Frage, ob nur der Ton neu kodiert wird (Video weiter
  `copy`, günstig) oder das ganze Video mitläuft (teuer bei 17-Minuten-Videos).

Weiter zu klären: Ein- oder Zweidurchgang. `loudnorm` in einem Durchgang ist
schnell, aber ungenauer; zwei Durchgänge messen erst und korrigieren dann. Bei
konstantem Aufnahmesetup ist ein Durchgang wahrscheinlich ausreichend — das
sollte gemessen, nicht angenommen werden.

Abnahme: Das gerenderte Video misst nach dem Schritt rund −14 LUFS
(`ffmpeg -af loudnorm=print_format=json` oder `ffmpeg -af ebur128`), und der
Ton klingt nicht gepumpt.

### 3.1 Nachtrag 9.8. abends — gebaut und abgenommen

Die Vorprüfung ergab: Die letzte Stufe kodiert ohnehin neu
(`render.py` `_render_arguments`, `-c:a aac -ar 48000`, kein `-c copy`), es gibt
genau eine Tonspur, kein `amix`. Die Angleichung ist damit ein Filter mehr im
bestehenden Audiozweig. Gebaut als zwei Durchgänge: ein eigener, nur-Ton
Messdurchgang **durch dieselben Schnitte** wie der Render — der Schnitt
verschiebt den integrierten Wert (+0,36 dB am Lauf 08-43-22), eine Messung an
der ungeschnittenen Quelle misst die falsche Datei.

Neu: `src\matrix_auto_cutter\loudness.py`, alle Konstanten dort mit ihrer
Herkunft im Kommentar.

**Abnahmemessung, 60-s-Fenster aus Lauf `2026-08-09 12-09-50`:**

| | gemessen |
|---|---|
| I | **−14,34 LUFS** |
| TP | **−1,19 dBTP** |
| LRA | **6,60 LU** |

Ton am echten Lauf abgenommen, kein Pumpen. **Der LRA-Vergleich ist grob:** Die
Referenz von 6,20 LU stammt aus einem **4-Minuten**-Fenster eines **anderen**
Laufs (07-28-18, Fenster 08:00–12:00). Ein 60-s-Fenster erfasst weniger Umfang
als ein 4-Minuten-Fenster; die beiden Zahlen sind nicht direkt vergleichbar und
ihre Nähe ist kein Beleg für Umfangstreue.

**Was NICHT geprüft wird:** `normalization_type`. Der Modus fällt bei jedem
realistischen Lauf auf `dynamic` zurück — ein Sprachsignal mit 17 bis 21 dB
Anhebungsbedarf erreicht den für `linear` nötigen Crest von 13 dB nicht. Der
Wert steht im Protokoll, nicht in einer Warnung. Gewarnt wird stattdessen am
fertig kodierten Ergebnis: I mehr als 1,5 dB vom Ziel, TP über −0,5 dBTP oder
LRA unter 4 LU.

Zur TP-Schranke, damit sie niemand auf den Limiterwert zieht: `alimiter`
begrenzt den Abtastspitzenwert bei 48 kHz, `loudnorm` misst den True Peak mit
Überabtastung. Der TP liegt darum **systematisch über** dem Limiterwert —
gemessen −1,19 / −1,21 / −1,00 dBTP gegen einen Limiter auf −1,5. Eine Schranke
auf −1,5 wäre derselbe Fehler wie `normalization_type`.

### 3.2 Nachtrag 9.8. spätnachmittags — Längenfehler und zweite Abnahme

Die Abnahme in 3.1 war ein Zufallstreffer. `loudnorm` dehnt die **Zeitachse** auf
das nächste volle 100-ms-Vielfache — die Samplezahl bleibt richtig, die
Zeitstempel laufen ihr voraus. 100 ms sind 6 Videoframes, also ging die
Dauerprüfung (Toleranz 50 ms) nur auf, wenn `output_frame_count` durch 6 teilbar
war. Lauf 12-09-50 hatte 5712 Frames, Restklasse 0. Lauf 14-15-47 hatte 5209,
Restklasse 1 — der ungünstigste Fall mit 83 ms Überhang — und fiel zweimal mit
`E_RENDER_VERIFY`.

Behoben durch eine Klemmung am Ende der Audiokette:
`… , apad, atrim=end_pts=<output_frame_count × 800>`. **`end_pts`, nicht
`end_sample`:** `end_sample` zählt Samples und hätte nie ausgelöst, weil die
Samplezahl bereits stimmte. Die Klemmung muss hinter `aresample=48000` stehen —
`loudnorm` gibt mit 192 kHz aus, davor würde sie auf ein Viertel schneiden.

**Zweite Abnahme, Lauf `2026-08-09 15-10-19`, Renderende 15:11:55:**

| | gemessen |
|---|---|
| I | **−13,39 LUFS** |
| TP | **−1,41 dBTP** |
| LRA | **18,80 LU** |

Keine Warnung, Verifikation 3 s (Decode ist also wirklich gelaufen), Dauer
39.466 gegen 39.467 ms erwartet. 2368 Frames, **Restklasse 4**.

Direkt danach ein zweiter Lauf, `2026-08-09 15-15-43`, 15:16:54: I −14,53 LUFS,
TP −0,97 dBTP, LRA 6,50 LU, 1519 Frames — **Restklasse 1**, genau die Klasse, die
um 14:18 gefallen war. Dauer 25.316 gegen 25.317 ms erwartet.

Zwei Dinge zum Merken:

- **Die Streuung ist größer als gedacht.** I liegt über fünf abgenommene Läufe
  zwischen −13,39 und −15,30 LUFS, der LRA zwischen 6,50 und 18,80 LU. Die
  1,5-dB-Schranke hat nach unten nur noch 0,2 dB Luft. Wer sie enger zieht, muss
  gegen diese Reihe rechnen.
- **Der Runner lief von 12:07:20 bis 15:02:39 auf altem Code**, quer über den
  Commit `a87b2a1` hinweg, weil `START-MATRIX-AUTO-CUTTER.cmd` bei laufendem
  Runner nur „läuft bereits" meldet. Die Startzeile im Log belegt keinen
  Neustart — nur `runner_starting` „Runner startet." tut das. Steht jetzt in
  `UMGEBUNG.md`, zusammen mit dem Logpfad und einem Filter zum Nachsehen.

---

### 3.3 Nachtrag 9.8. abends — Taktkorrektur, Offset 85, Proposal 1.2

Der Intro-Schnitt saß wieder falsch. Die Analyse (`INTRO-CUT-BEFUND-2026-08-09.md`,
17 Läufe) zeigte eine Streuung von 121 Frames, die in zwei Teile zerfällt.

**Der Takt-Versatz — jetzt gerechnet statt eingebacken.** `mapped_source_frame`
ist `output_frame_counter - 1`; der Zähler, den der Adapter beim
Frontend-Callback abgreift, hinkt dem Compositor um die Tiefe der Ausgabepipeline
hinterher — und die *ist* die Anlaufzeit, die `recording_started` misst. Jede
Szenenmarke liegt deshalb um diesen Betrag zu früh. Er schwankt laufabhängig
zwischen **16 und 63 Frames** (267 ms bis 1050 ms) und steht im Sidecar.

Neu: `src\matrix_auto_cutter\event_lag.py` mit `pipeline_lag_frames(sidecar)`.
Angewandt in `intro.py`, `outro.py` und `protection.py`. **Nicht** in der
Sidecar-Abbildung — die ist an beiden Enden verankert und wird beim Einlesen
nachgerechnet; eine geänderte Formel machte jedes vorhandene Sidecar ungültig.

**Nur Frontend-Marken wandern.** `scene_changed` und `manual_protection` ja;
`recording_started`, `recording_stopped`, Pause und Resume nie — sie sind an der
Ausgabe verankert und exakt. Der Schutzblock am Aufnahmeanfang muss auf Frame 0
stehen bleiben, sonst gäbe er die erste Sekunde frei.

**`INTRO_CUT_OFFSET_FRAMES = 85` statt 148**, und die Bezugsgröße ist neu:
*Marke + Lag + Konstante* statt *Marke + Konstante*. Die 85 sind der Vorlauf von
`intro-sting-sovereign-1440p.webm` bis zum **Anfang der Chart-Animation, rund
5 % der Linie** — ausdrücklich nicht bis zur stehenden Karte, die liegt bei
Stingframe 187 und wäre viel zu spät.

Gerechnet gegen die echten Journale:

| Lauf | Marke | Lag | neu | alt (Marke+148) | ab sichtbarem Szenenanfang |
|---|---:|---:|---:|---:|---:|
| 16-50-21 | 517 | 63 | **665** | 665 | 85 |
| 17-45-21 | 252 | 16 | **353** | 400 | 85 |
| 17-52-38 | 565 | 16 | **666** | 713 | 85 |

Der als richtig abgenommene Lauf wird framegleich reproduziert, die zu späten
wandern um 47 Frames vor, und alle landen auf demselben Abstand.

**Proposal 1.2** mit eigener Digest-Domäne
(`matrix-auto-cutter/cut-proposal/1.2`). `pipeline_lag_frames` ist ab 1.2 in
jeder vorhandenen Resolution Pflicht und in 1.0/1.1 verboten, damit ältere Bytes
kanonisch bleiben. Grund für das Feld: eine nicht nachrechenbare Zahl im
Proposal war der Grund, dass die falsche 148 so lange unentdeckt blieb.

**Abgenommen am echten Lauf:** 18-51-29 (Lag 17) und 18-54-14 (Lag 16), beide
mit dem Einstieg am Anfang der Chart-Animation. Vorher lagen genau diese Läufe
bei rund 70 % der Linie.

> **OFFEN — Gegenprobe bei Lag 63.** Beide Abnahmeläufe hatten kleinen Lag. Für
> den großen Lag ist die 47-Frame-Korrektur nur **rechnerisch** belegt, nicht am
> Bild. **Beim nächsten Lauf mit `monotonic_ns` um 1 049 999 958 nachholen** und
> das Ergebnis in `TAKTKORREKTUR-UND-INTRO-OFFSET-2026-08-09.md` eintragen.

Zwei weitere Punkte bleiben offen: `protection.py` ist richtig verdrahtet, wirkt
aber auf heutigen Daten nicht (die betroffenen Ereignistypen setzt der Producer
nie ab), und ein Frameverlust vor der Marke wird nicht korrigiert — selten
(1 von 17 Läufen), dann aber 45 Frames. Beides per Test dokumentiert.

**Ebenfalls unerklärt geblieben:** der Regime-Bruch am 9.8. zwischen 6:36 und
7:27 Uhr, der die Sting-Phase um rund 70 Frames verschob. Rechnerneustart,
Treiber, OBS-, Browser- und Pluginstand, Encoder und die Sting-Datei sind
ausgeschlossen; übrig bleibt die Szenensammlung, für die es keine Historie gibt.
Springt die Phase zurück, wandert `INTRO_CUT_OFFSET_FRAMES` mit — der Lag nicht,
der ist jetzt gerechnet.

Details: `TAKTKORREKTUR-UND-INTRO-OFFSET-2026-08-09.md`.

---

## 4. Offene Punkte — nach Wert geordnet

### 4.1 `loudnorm` — erledigt
Gebaut und abgenommen, siehe 3.1, Längenfehler behoben und zweimal nachgemessen,
siehe 3.2. Offen bleibt nur die Beobachtung am
Kompressor: Er arbeitet auf Material ohne Ausreißer schwach (Lauf 08-43-22:
0,84 dB Crestgewinn für 2,97 dB Pegelverlust; Lauf 07-28-18 mit einem
Einzelereignis über der Schwelle: 4,93 dB für 1,90 dB). Verdacht: 5 ms Attack
gegen 120 ms Release bei Schwelle −24 dB. Steht als Kommentar an den
`COMP_*`-Konstanten und wartet auf eine Messreihe über mehrere Läufe.

### 4.2 Shorts-Block

**Vorher `SHORTS-1-UEBERGABE-2026-08-07.md` aus `docs\repeat\` beschaffen.**

Material gesichert und unangetastet:
- Cursor-Protokoll: `F:\ShortsQuellen\Cursor\cursor-2026-08-07 11-28-59.csv`,
  2867 Zeilen, 11:29:08–11:52, zwei Abfragen pro Sekunde
- Webcam: `F:\ShortsQuellen\Avatar\AvatarWebcam-2026-08-07 11-35-16.mp4`, 1036,12 s
- Bildschirm: `F:\MatrixMarketAutoEdit\2026-08-07 11-35-16.mp4`, 1037,22 s
- Anker: Maus drei Sekunden in der linken oberen Ecke, in allen drei Spuren

Bildschirmgeometrie: DISPLAY1 primär `0,0`–`2559,1439` (aufgenommen), DISPLAY2
links bei `-2560,0`. **Jede CSV-Zeile mit negativem x ist ein Moment auf dem
nicht aufgenommenen Monitor.**

**Weiterhin unbeantwortete Frage, fragen statt annehmen:** Was soll aus dem
Shorts-Schritt herausfallen? Nur Zeitfenster / plus 16:9-Clips / fertige
9:16-Clips / vertikal plus Untertitel?

**Wichtig seit dem 9.8.:** Der Intro-Cut verschiebt die gerenderte Zeitachse.
Short-Kandidaten deshalb weiterhin aus dem **Rohvideo** notieren.

### 4.3 Stinger neu rendern

Der Wisch endet unvollendet, also mit hartem Schnitt. Das behebt nur ein
Neurendern. Abnahmebedingung: letzter Videoframe = Containerlänge, Matte am Ende
vollständig zurückgezogen, konstante 60 fps.

**Diese Kopplung gibt es nicht.** Sie stand hier, weil die Herleitung der 148
falsch war: beim Intro-Wechsel schaltet die Sammlung mit „Schnitt" (100 ms),
einen Stingerwisch gibt es dort nicht. `INTRO_CUT_OFFSET_FRAMES` hängt an
`intro-sting-sovereign-1440p.webm`, nicht an `Stinger_synchron.webm`. Ein neu
gerenderter **Stinger** lässt die Konstante unberührt; ein neu gerenderter
**Intro-Sting** verschiebt sie. Siehe Abschnitt 3.3.

### 4.4 Nachlese zum Clock-Gate

Vier Punkte, alle aus der Analyse vom 8./9.8., keiner blockierend:

- **Mindestbeweislage rechnet gegen die falsche Basis.** `minimum_measurable_ns`
  leitet 16,667 s aus der *Gesamtdauer* ab, Theil-Sen misst aber über Paarbasen
  von rund einem Drittel davon. Die Quantisierungsschwelle liegt darum erst ab
  ca. 50 s unter 1000 ppm und ab ca. 100 s unter 500 ppm. Zwischen etwa 17 s und
  50 s ist das Gate scharf, obwohl ein einzelner Frame Zappeln es reißen kann.
  Beleg: `172c0fa3` entkam mit 2754,8 ppm nur, weil es 0,14 s zu kurz war.
- **Keine Obergrenze für den Counterzuwachs im letzten Intervall.** Die alte,
  scheinbare Schranke griff nur bei Tickkollision und ließ bei eigenem Tick auch
  3000 Frames durch. Wer eine echte Grenze will, braucht eine eigene, explizite
  Prüfung, die für alle Läufe gilt. `sample_gaps_valid` bindet weiterhin Abstand
  (≤ 5 s) und Monotonie.
- **Proposal-1.2 statt gelockerter 1.1-Regel.** Eigene Digest-Domain, analog zur
  Outro-Einführung. Berührt `_DIGEST_DOMAIN_*`, das `Literal["1.0","1.1"]`,
  `review_app` und `docs\`.
- **`minimum_keep_island` ist im Normalbetrieb nicht mehr erreichbar.** Die
  450-Frame-Zone überdeckt die 30-Frame-Inselgrenze vollständig. Die Regel ist
  jetzt Rückversicherung für Sonderfälle, kein Alltagsmechanismus.

### 4.5 Kleinkram

- **Kodierungspanne:** „vollstÃ¤ndig" statt „vollständig" in `status.json` und
  `runner.log`. Nur Anzeigetexte.
- **Datei `-`** im Wurzelverzeichnis entsteht bei **jedem** pytest-Lauf. Ein
  Test behandelt `-` unter Windows als Dateinamen statt als stdout-Konvention.
- **Native Adaptersuite ist flaky**, vorbestehend: feste 2-s-Fristen im
  Test-Harness unter Maschinenlast.
- **20 mypy-Fehler in `repeat\`**, vorbestehend.
- **`ruff format`** meldet vorbestehende Abweichungen (drei
  `rejected = (*rejected,`-Blöcke in `cut_proposal.py`, eine Zeile in
  `review_app.py`), im Repo nicht erzwungen.
- **Logitech G Plugin** in OBS aktiv — vermutlich ungenutzt. Nicht
  deinstallieren, im Zweifel nur das Häkchen entfernen (reversibel).

---

## 5. Zwei Fallen, die am 8./9.8. Stunden gekostet haben

Beide stehen jetzt auch in `UMGEBUNG.md`. Sie hier zu wiederholen ist Absicht:
Sie haben zweimal wie ein Fehler im Cutter ausgesehen und waren keiner.

**1. Der Product Runner überlebt jede Codeänderung.** Er ist ein
Hintergrundprozess und lädt neuen Code nicht nach. Nach *jeder* Änderung:
stoppen und neu starten, sonst testet man den alten Stand. Das hat am 8.8. eine
Fehlersuche über Stunden ausgelöst — ein alter Runner schrieb ein Proposal ohne
das neue Feld, das frische Review-Fenster wies es ab.

Sauber stoppen (`Stop-Process` ist verboten, `python` aus dem PATH funktioniert
nicht):

```powershell
cd P:\DimensionWithin-MatrixMarketAutoEditor
$base = ((Select-String -Path ".\.venv\pyvenv.cfg" -Pattern '^home = ').Line -replace '^home = ','').Trim()
$env:PYTHONPATH = "$PWD\src;$PWD\.venv\Lib\site-packages"
& "$base\python.exe" -m matrix_auto_cutter.product_runner --stop
```

Dann `START-MATRIX-AUTO-CUTTER.cmd`, neue PID prüfen.

**2. Animierte OBS-Quellen laufen im Hintergrund weiter.** Browserquellen
(`TruthPill Rotator`, `EndCart`) brauchen **„Deaktivieren, wenn Quelle nicht
sichtbar ist"** und **„Browser bei Szenenaktivierung aktualisieren"**. Ohne
diese Häkchen ist die Animation beim Szenenwechsel schon mittendrin, und zwar
jedes Mal an anderer Stelle. Das sah zweimal wie ein wandernder Schnitt aus und
war ein OBS-Problem: Der Intro-Cut saß beide Male exakt 148 Frames hinter der
Marke, nur stand die Chartanimation einmal am Anfang und einmal fast am Ende.

Medienquellen (`Intro - Sting`) brauchen stattdessen **„Wiedergabe bei
Quellenaktivierung erneut starten"** — bei `Intro - Sting` bereits gesetzt.

---

## 6. Repositoriumszustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        840fcf3
origin      synchron
Tests       1739 passed, 1 skipped
```

Working Tree sauber bis auf die Datei `-` (entsteht bei jedem pytest-Lauf,
gehört nicht ins Repository, `rm ./-` entfernt sie).

**Nachtrag 9.8. abends:** Der Block oben beschreibt den Stand am Vormittag. Der
`loudnorm`-Commit aus 3.1 setzt HEAD weiter und bringt die Suite auf
1788 passed, 1 skipped. Danach `787eb72` (atomares Ersetzen, siehe
`PERMISSIONERROR-ATOMARES-SCHREIBEN-2026-08-09.md`) auf 1818 und die
Taktkorrektur aus 3.3 auf **1845 passed, 1 skipped**.

Zur Datei `-`: sie liegt weiterhin untracked im Wurzelverzeichnis und ist bei
keinem dieser Commits mitgefahren.

---

## 7. Befund über die Dokumentation

Unverändert gültig: **`docs\` ist gleichzeitig veraltet und vertragsbindend.**

*Veraltet:* „Product Runner" und `review_app` kommen in keinem
Architekturdokument vor; beschrieben wird ein statisches `review.html`.
`no_sidecar_safe_mode` ist dokumentiert, aber nicht implementiert.

*Vertragsbindend:* Der kanonische Schemablock in
`docs\matrix-auto-cutter-architecture-plan-v0.2.md` ab Zeile 287 wird byte-genau
gegen das exportierte Pydantic-Schema getestet
(`test_exported_schema_recursively_matches_canonical_sidecar_contract`). Die
Datei hat 64.899 B. **Nicht anfassen, ohne den Test zu kennen.**

**Für die Project Knowledge:** `UMGEBUNG.md`, `ABLAGE.md`, die
SHORTS-2-Übergabe und diese Datei. Die vier Architekturdokumente **nicht** — bei
Bedarf gezielt über Claude Code lesen lassen, mit dem Hinweis, dass sie teils
überholt sind.

---

## 8. Arbeitsweise — was sich bewährt hat

**Rein lesende Analyseaufträge vor jedem Eingriff.** Der Ertrag war am 8./9.8.
erneut außergewöhnlich: Die Gate-Analyse hat die Ursache gefunden *und* eine
falsche Erfolgsmeldung aus einer früheren Übergabe widerlegt (`89c344e6`).

**„Nichts ausführen, was Zustand ändert" ist die richtige Formulierung.**
`grep`, `git log` und Rechnen sind erwünscht.

**Aufträge enden ohne Commit, wenn die Änderung sichtbares Verhalten ändert.**
Erst am echten Lauf ansehen, dann committen. Hat sich beim Intro-Cut viermal
ausgezahlt.

**Der Agent hat mehrfach von sich aus angehalten oder eine Vorgabe widerlegt**,
statt sie umzusetzen — bei der Zonendefinition, bei der Schemafrage, bei der
verlorenen Prüfung. Jedes Mal war das richtig. **Nicht wegoptimieren.**

**Berichte gegeneinander rechnen bleibt Pflicht.** Am 9.8. mussten erneut
mehrere Zahlen aus früheren Berichten korrigiert werden.

**Der Nutzer hat zweimal eine falsche Diagnose des Assistenten gekippt** — beim
zeitlichen Ablauf des Stingertauschs und bei der Frage, ob das Intro eine
Browserquelle ist. Widerspruch ernst nehmen, nicht wegargumentieren.

**Prioritätskonflikte:** Erst das Video herausbekommen, dann sauber reparieren.

---

## 9. Was in den neuen Chat gehört

Diese Datei und die SHORTS-2-Übergabe. Diese Datei gehört außerdem nach
`docs\repeat\` und fährt beim nächsten Commit mit.

Erste zwei Handgriffe:

1. Diese Übergabe nach `docs\repeat\` legen (fährt beim `loudnorm`-Commit mit).
2. `loudnorm` als erster Auftrag — beginnend mit der rein lesenden Vorprüfung
   aus Abschnitt 3.
