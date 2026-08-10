# Shorts — Kontext und Entscheidungen

Stand: 9. August 2026, abends
Quelle: Denkchat zur Shorts-Produktionslinie (parallel zur Cutter-Arbeit)

**Was diese Datei ist:** Der gebündelte Stand aller Shorts-Entscheidungen vom
9.8. Sie ersetzt den knappen Shorts-Abschnitt (4.2) in
`SHORTS-3-UEBERGABE-2026-08-09.md`, der vom Vormittag stammt und überholt ist.

**Was sie nicht ist:** Ein Bauauftrag. Nichts davon ist implementiert. Der
Cutter-Teil (Intro-Cut, Clock-Gate, loudnorm) läuft in einem eigenen Chat und
ist hier nur insoweit erwähnt, wie er die Shorts betrifft.

**Voraussetzung, bevor gebaut wird:** `SHORTS-1-UEBERGABE-2026-08-07.md` aus
`docs\repeat\` lesen — sie regelt Shorts-Entwurf, Ablage und Arbeitsweise und
lag bisher keinem der Chats vor.

---

## 1. Die Grundentscheidung: weg von Opus Clip

Shorts entstehen bisher über **Opus Clip**, ein kostenpflichtiger Dienst, der
Momente findet, umrahmt und Untertitel einbrennt.

**Entschieden: vollständig ersetzen.** Zwei Gründe, beide vom Nutzer:

- Laufende Kosten für etwas, das einmal richtig gebaut werden kann.
- Opus Clip kennt das Designsystem nicht und wird es nie kennen. Jedes Element
  der aktuellen Shorts verstößt gegen die eigenen Guardrails (siehe Abschnitt 6).

Die Haltung dazu wörtlich: *„Lieber bau ich unser System einmal perfekt, auch
wenn es dreißig Stunden mehr dauert."*

**Gegenposition, die trotzdem gilt:** „Rund" heißt nicht „alles auf einmal". Der
Intro-Cut ist am 9.8. in vier Anläufen entstanden, jeder für sich ansehbar. Ein
einziger großer Auftrag hätte am Ende drei gleichzeitig falsche Dinge ergeben,
ohne dass zuzuordnen wäre, welches woran liegt. Das Ziel ist das runde Werkzeug,
der Weg dorthin geht in Stufen.

---

## 2. Quelle und Auslösung

**Quelle:** `F:\MatrixMarketAutoEdit\Rendered\` — die fertig gerenderten Videos
aus dem Cutter, nicht das Rohmaterial.

Begründung: Alles, was in der Renderkette gebaut wird (Intro-Cut, Stille-Cuts,
`loudnorm`), kommt den Shorts damit geschenkt zu. Der Preis: Zeitmarken der
Shorts beziehen sich auf die gerenderte Achse, nicht auf die Rohzeit.

**Auslösung: manuell, ausdrücklich kein Ordnerbeobachter.** Beim Testen landen
regelmäßig Videos in `Rendered\`, aus denen niemand Shorts will — am 9.8. allein
über ein Dutzend.

**Konsequenz: ein eigenes Werkzeug.** Liste der gerenderten Videos, pro Video
ein Knopf. Das ist bewusst **nicht** der Product Runner, sondern ein zweites
Programm daneben. Die bestehende Cutter-Pipeline wird nicht angefasst.

**Historischer Hinweis:** Bisherige Shorts stammen aus Streams und
Inner-Circle-Meetings — Aufnahmen **ohne** Journal und **ohne** Cursorprotokoll.
Die neue Linie zielt auf die Cutter-Videos mit voller Datenlage. Altmaterial
bleibt Handarbeit oder fällt weg.

---

## 3. Kandidatenauswahl über Claude Code

Der Teil, den Opus Clip gut konnte und der nichts mit der Marke zu tun hat.

**Entschieden:** Kein eigenes Auswahlmodell. Claude Code liest Transkript und
Cursorprotokoll und schlägt Zeitfenster mit Begründung vor. Der Nutzer wählt aus.

Warum das trägt:

- Das Transkript entsteht ohnehin für die Untertitel — die Auswahl liest
  dieselbe Datei, kostet also fast nichts extra.
- Ein Sprachmodell beurteilt, ob eine Passage als eigenständiger Gedanke
  funktioniert. Genau dafür will man kein eigenes Modell trainieren.
- Die Intelligenz ist im Abo enthalten, es entstehen keine zusätzlichen Kosten.

**Datenlage:** Transkript sagt *was*, Cursorprotokoll sagt *wo* (Mausposition,
zwei Abfragen pro Sekunde), Journal sagt *in welcher Szene*. Opus Clip sah nur
Pixel und Ton. Videobilder muss niemand analysieren.

**Der entscheidende Punkt: eine versionierte Kriteriendatei.**
„Finde interessante Stellen" liefert Beliebiges. Brauchbar ist etwa:

> Passagen von 30–60 s, die ohne Vorwissen verständlich sind, eine These
> enthalten und mit einem vollständigen Satz enden.

Diese Kriterien gehören als Datei ins Repository, **nicht** in einen Prompt, der
jedes Mal neu getippt wird — nur so lassen sie sich über Wochen schärfen.

**Integration: die runde Variante.** Vorschläge landen im Werkzeug aus
Abschnitt 2 und werden dort per Klick ausgewählt — nicht von Hand aus einem
Claude-Code-Fenster abgetippt.

---

## 4. Transkription und Untertitel

**Eigener Transkriptionslauf für Shorts.** Die Long-Form-Videos sollen keine
Untertitel bekommen; die bestehende Pipeline bleibt unangetastet.

**Modell:** größer als `ggml-small.bin`, voraussichtlich `medium`. Ein Short ist
30–60 s, nicht 17 Minuten — der Geschwindigkeitsnachteil ist hier belanglos, die
Genauigkeit bei Wortzeitstempeln zählt.

**Offen, gehört gemessen:** Ob `medium` bei deutschen Wortzeitstempeln wirklich
besser ist. Größere Modelle erfinden bei langen Stillen eher Text. An einem
echten Ausschnitt gegenprüfen, nicht annehmen.

**Vokabular:** Dieselbe `--prompt`-Datei wie bisher (in `labels\repeat\`,
versioniert, klein und teuer). Das ist ein Aufrufparameter, kein
Modellmerkmal — beide Modelle können sie nutzen. Deutsch-englischer Mischtext
ist genau der Fall, für den sie gebaut wurde.

**Wortzeitstempel:** `whisper.cpp` liefert sie bereits über `-ojf` — dasselbe
Flag, das für die Absturzsicherheit ohnehin gesetzt wird. Kein neues Werkzeug
nötig.

**`-ml 60` gilt hier NICHT.** Das ist die Regel für die repeat-Diagnose. Für
Untertitel braucht es kurze Segmente.

### Darstellung

- **Schrift: JetBrains Mono.** Entschieden gegen Newsreader — der ist ein
  kontrastreicher Serif, dessen Haarstriche auf dem Telefon bei schneller
  Lesegeschwindigkeit wegbrechen. JetBrains Mono trägt im System das Register
  „Daten", ist aber lesbar; der Nutzer hat das ausdrücklich abgesegnet.
- **Wort-für-Wort-Hervorhebung: nur Farbe.** Ganze Zeile in `--bone-dim`, das
  aktive Wort wechselt auf `--bone` oder `--brass`. Kein Springen, kein
  Skalieren, keine Bewegung. Das hält den Effekt im Register und außerhalb des
  „bro trader"-Vokabulars.
- **Position:** Die aktuelle Untertitelzeile endet bei rund y=1440 (bei
  1080×1920), direkt über der Kanalzeile. Diese Linie ist richtig gesetzt und
  sollte als Fixpunkt erhalten bleiben.

---

## 5. Komposition

### Sicherheitszone — gemessen an echten Screenshots (1080×1920)

```
oben     200 px   Notch, Statusleiste, YouTube-Kopfzeile (Zurück, Lupe, Menü)
unten    480 px   Kanalzeile, "Mitglied werden", Titel, Kommentarleiste,
                  Fortschrittsbalken, Systemnavigation  — 25 % der Höhe
rechts   150 px   Herz, Kommentar, Teilen, Remix (unteres Drittel)
```

Nutzbares Feld: rund **1080 × 1240**, von y=200 bis y=1440.

Als Token ins Designsystem aufnehmen, damit Claude Design jeden Entwurf
automatisch dagegen prüft:

```css
--shorts-safe-top:    200px;
--shorts-safe-bottom: 480px;
--shorts-safe-right:  150px;
```

### Aufteilung

Die ursprüngliche Idee „obere Hälfte Chart, untere Hälfte Avatar" kollidiert mit
der Realität: Die untere Hälfte sind 960 px, davon liegt die Hälfte unter
YouTubes Bedienelementen. Für Avatar **und** Untertitel bleiben real ~340 px.

Vorschlag:

```
y  200 – 1100   Chart (1080 × 900)
y 1100 – 1440   Untertitelzeile + Avatarkopf
y 1440 –        Ausblutung: Schultern und Körper des Avatars dürfen hinter
                die Kommentarleiste laufen
```

Der Trick ist die Ausblutung: Nur Gesicht und Text müssen über der Grenze
bleiben. So wirkt der Avatar groß, ohne Platz zu kosten.

**Der Avatar gehört nach links** — rechts sitzt die Buttonleiste. Untertitel
über ihm oder rechts daneben, aber links von x=930.

### Chart-Ausschnitt mit Mausverfolgung

Bei 1080×900 Zielfläche braucht es aus der 2560×1440-Quelle einen Ausschnitt im
Verhältnis 1,2 — also etwa **1730×1440**: volle Höhe, rund 830 px horizontaler
Spielraum. Genug, dass Verfolgung etwas bringt, nicht so eng, dass jedes Zucken
das Bild reißt.

Zwei Anforderungen an die Verfolgung:

- **Glätten.** Der Ausschnitt darf nicht hinterherhecheln, sondern zieht träge
  und gedämpft nach.
- **Rückfall bei negativem x.** Jede CSV-Zeile mit negativem x ist ein Moment
  auf dem nicht aufgenommenen Monitor (DISPLAY2 liegt bei `-2560,0`). Dann:
  Position halten, nicht springen.

---

## 6. Designsystem

Alle Elemente entstehen mit dem Designsystem („DimensionWithin — Sovereign
Desk", v1.0). Quelle ist `tokens.css` plus `design-system.md` — **nicht** das
5,4-MB-Standalone-HTML, das eine Showcase-Seite ist.

**Empfehlung zur Umsetzung:** Überlagerungen als HTML/CSS mit denselben Tokens
bauen, dann rendern und mit ffmpeg zusammensetzen. Ein Designsystem, eine
Quelle. Vorbild ist `TruthPill Rotator` — läuft bereits so in OBS. Sonst werden
Farben und Schriften an zwei Orten gepflegt und driften auseinander.

### Verstöße der aktuellen Shorts (alle im Screenshot sichtbar)

| Element | Verstoß | Regel |
|---|---|---|
| Untertitel, Hooktext | fette Groteske mit Schlagschatten und Kontur | `--shadow: none`, keine doppelte Kontur, kein Inter für die Stimme |
| Endcard | roter YouTube-Play-Button | Rot in dieser Sättigung ist nicht in der Palette; Oxblood `#8a2a2a` bedeutet Zusammenbruch |
| Endcard | mehrere CTAs | End Screen: eine Messinglinie, Monogramm, **genau ein CTA** |
| Mascot | groß und mittig | System sagt rechte Spalte ~30 %, „never the hero" |

### Beschlossene Abweichung vom Designsystem

**Der Mascot darf in Shorts der Held sein.** Der Nutzer wörtlich: *„Das
Designsystem ist schon relativ alt. Lass dir davon nix verbieten. Mein Wort
gilt."*

Begründung: In Shorts spricht der Mascot, er ist die Präsenz in der unteren
Hälfte. Die 30-%-Rechtsspalte funktioniert in 9:16 ohnehin nicht — dort sitzen
die YouTube-Buttons.

**Aufgabe daraus:** Das Designsystem bekommt einen dokumentierten
Shorts-Abschnitt, der das ausdrücklich erlaubt, statt dass die Regel stillgebrochen
wird.

### Endcard — Warnung

Auf einen echten YouTube-Button zu zeigen ist heikel: Die Positionen
unterscheiden sich zwischen iOS und Android, zwischen Telefongrößen, und YouTube
verschiebt sie. Der Avatar zeigt dann ins Leere.

Robuster: einen **eigenen** Button im Designsystem an eine kontrollierte Stelle
zeichnen und darauf zeigen. Sieht außerdem nach der Marke aus, nicht nach
YouTube.

---

## 7. Die Avatardatei

### Gemessen am 9.8. (`AvatarWebcam-2026-08-09_08-43-03.mp4`)

```
630 × 422, H.264, yuv420p, 60 fps — KEIN Alphakanal
Hintergrund   (0, 0, 0)      reines Schwarz
Mantel        (21, 19, 17)
Kapuze        (38, 36, 32)
Gesicht       (255, 255, 255)
Ton           AAC, 48 kHz, stereo
```

**Kein Keying nötig.** Der Mantel liegt bei (21,19,17), `--ink` bei (23,22,20) —
derselbe Ton. Der Hintergrund ist reines Schwarz und damit dunkler als jede
Fläche des Systems. Ein `blend=lighten` gegen die Ink-Fläche genügt: Schwarz
verschwindet, der Mantel verschmilzt (2 Stufen Unterschied, unsichtbar), Gesicht
und Kapuzenkante bleiben. Keine Matte-Ränder, keine Keying-Artefakte, keine
Maske.

Das ist genau das, was das Designsystem mit „fade the inner edge with a soft
mask" meint — nur geschenkt, weil der Avatar bereits nach Spezifikation auf Ink
gebaut ist.

**Auflösungsgrenze:** 630 px nativ. Im Short höchstens **480–560 px breit**
halten, dann ist es sauberer Downscale. Für mehr muss die Webcamquelle in OBS
höher aufgenommen werden — kostet nichts und wäre jetzt der richtige Moment.

### OFFEN und wichtig: die Schnitte fehlen auf der Avatardatei

**Das ist der erste Stolperstein und bisher nicht bedacht.**

Der Cutter schneidet ausschließlich die Bildschirmaufnahme. Die separate
Webcamdatei liegt unangetastet in `F:\ShortsQuellen\Avatar\` und weiß von den
Schnitten nichts. Im gerenderten Video ist der Avatar klein über das Chart
gelegt und daraus nicht herauslösbar — für die untere Hälfte des Shorts braucht
es die freistehende Datei.

**Lösbar, Daten stehen.** Vom 7.8.:

```
2026-08-07 11-35-16.mp4               1037,22 s   Bildschirm
AvatarWebcam-2026-08-07 11-35-16.mp4  1036,12 s   Avatar
```

Identischer Zeitstempel im Dateinamen — beide Aufnahmen starten im selben
Moment, der Versatz von rund einer Sekunde liegt am Ende. Einmal über den Ton
gegenprüfen, dann dieselbe Schnittliste aus `cut-proposal.json` auf die
Avatardatei anwenden.

**Das ist ein eigener Schritt der Shorts-Pipeline, kein Eingriff in den
Cutter.**

---

## 8. Die Pipeline in Stufen

Jede Stufe für sich ansehbar — dieselbe Regel, die beim Intro-Cut viermal
gerettet hat.

| # | Stufe | Anmerkung |
|---|---|---|
| 0 | Werkzeug mit Dateiliste | Auslösung, kein Ordnerbeobachter |
| 1 | Avatardatei nachschneiden | erster Stolperstein, siehe 7 |
| 2 | Kandidaten finden | Kriteriendatei + Claude Code, wenig Bauarbeit |
| 3 | Ausschnitt mit Mausverfolgung | Glätten, Rückfall bei negativem x |
| 4 | Untertitel | eigene Transkription, Wortzeitstempel |
| 5 | Komposition und Endcard | HTML/CSS aus dem Designsystem |

---

## 9. Offene Punkte

1. **Schnitte auf der Avatardatei** — siehe 7. Blockiert die Komposition.
2. **Schriften nicht installiert.** Weder Newsreader noch JetBrains Mono liegen
   unter `C:\Windows\Fonts` oder im Nutzerprofil (am 9.8. geprüft). Beide von
   Google Fonts holen, **statische TTFs**, nicht Variable Fonts — ffmpeg und
   Chromium kommen damit zuverlässiger klar. Newsreader in 400/500/600/700/800
   plus Italic, JetBrains Mono in 400/500.
3. **`medium` gegen `small`** bei deutschen Wortzeitstempeln — messen.
4. **Shorts-Abschnitt im Designsystem** dokumentieren (Mascot als Held,
   Sicherheitszone, Untertitelschrift).
5. **Webcam-Aufnahmeauflösung** in OBS erhöhen, falls der Avatar größer als
   ~560 px werden soll.
6. **Ein fertiges Opus-Clip-Short als Datei** wäre nützlich, um zu sehen, ob aus
   dessen Ausgabe etwas maschinell weiterverwendbar ist — vor der Abschaltung
   sichern.

---

## 10. Was noch nicht entschieden ist

- **Zielverhältnis der Chartfläche** — 1080×900 ist ein Vorschlag, keine
  Festlegung.
- **Wie viele Shorts pro Video** und ob mehrere gleichzeitig gerendert werden.
- **Ob Shorts eine eigene Lautheitsbehandlung brauchen** oder `loudnorm` aus der
  Renderkette erben. Hängt davon ab, wo `loudnorm` in der Kette landet — der
  Cutter-Chat klärt das gerade.
- **Ausgabeort und Benennung** der fertigen Shorts.
