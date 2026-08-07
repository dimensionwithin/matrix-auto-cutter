# Matrix Auto Cutter — Übergabe an den Arbeitschat „Shorts"

Stand: 7. August 2026, nachts
Vorgänger-Chat: REPEAT-3 (Lautheitsmessung, Vokabeltest, Entwurf Shorts)
Vorgänger-Übergabe: `docs\repeat\REPEAT-3-HANDOFF-2026-08-06.md` — weiterhin
gültig für alles zum Repeat-Durchgang, **mit den Korrekturen in Abschnitt 3
dieser Datei**.

**Dieser Chat ist ein Arbeitschat, kein reiner Planungschat.** Der erste Teil
des Tages ist eine Videoaufnahme, die der Nutzer ohnehin macht. Sie liefert
nebenbei das Material für den Shorts-Block. Führe ihn Schritt für Schritt
durch Abschnitt 1, **bevor** irgendetwas anderes besprochen wird.

---

## 1. Zuerst: die Aufnahme heute früh

Der Nutzer nimmt heute ein echtes Video auf. Das ist kein Testlauf — das
Video wird veröffentlicht. Die Zusatzschritte hier kosten ihn zusammen keine
zwei Minuten und liefern Material, das sich **nachträglich nicht mehr
beschaffen lässt**.

Arbeite die Punkte in dieser Reihenfolge mit ihm ab und hake sie einzeln ab.

### 1.1 Cursor-Protokoll starten — VOR OBS

PowerShell öffnen, das hier hineinkopieren, laufen lassen. Erst danach OBS
starten.

```powershell
Add-Type -AssemblyName System.Windows.Forms
$out = "F:\ShortsQuellen\Cursor\cursor-$(Get-Date -Format 'yyyy-MM-dd HH-mm-ss').csv"
"zeit,x,y" | Set-Content $out
Write-Host "Schreibe nach: $out  -- mit Strg+C beenden"
while ($true) {
  $p = [System.Windows.Forms.Cursor]::Position
  "{0:o},{1},{2}" -f (Get-Date), $p.X, $p.Y | Add-Content $out
  Start-Sleep -Milliseconds 500
}
```

Zwei Zeilen pro Sekunde, reines Abfragen der Cursorposition, keine
Bildanalyse. Der Ressourcenverbrauch ist nicht messbar — das war eine
ausdrückliche Anforderung des Nutzers.

Nach der Aufnahme mit **Strg+C** beenden. Nicht vergessen, sonst läuft es
weiter und die Datei wächst sinnlos.

### 1.2 Der Anker — ohne ihn ist die CSV wertlos

Sobald OBS aufnimmt: Maus für **etwa drei Sekunden in die linke obere
Bildschirmecke**, dann wieder weg. Erst danach mit dem Reden anfangen.

Grund: Die CSV hat Wanduhrzeit, das Video hat Videozeit. Der Anker ist im
Video sichtbar und in der CSV als Werte nahe `0,0` auffindbar. Daraus rechnen
wir den Versatz exakt aus. Ohne Anker ist die Synchronisation Schätzung.

### 1.3 Zwei bis drei Short-Kandidaten notieren

Wenn ihm beim Reden ein Moment auffällt, von dem er denkt „das wäre ein gutes
Short": Videozeit oder Uhrzeit auf einen Zettel. Drei Stück reichen.

Das ist später das Prüfmaterial für den Kandidatensucher — findet er genau
diese Stellen? Ohne diese Notizen braucht es dafür eine eigene
Sichtungsrunde.

### 1.4 Was er ausdrücklich NICHT ändern soll

- **Aussteuerung nicht anfassen.** Das Material liegt bei −35,8 LUFS, also
  leise, aber die Spitzen sind bei −2,4 dBTP. Aufdrehen riskiert
  Übersteuerung. Das wird sauber im Rendern korrigiert, nicht in der
  Aufnahme.
- **OBS-Ausgabepfade nicht umstellen.** Bewusst offen gelassen (siehe 1.5).
- **Sonst nichts anders machen als sonst.** Wir brauchen repräsentatives
  Material. Wer „für den Tracker" die Maus anders bewegt, testet ein
  Verhalten, das es im Alltag nicht gibt.

### 1.5 Nach der Aufnahme: wo sind die Dateien gelandet?

**Das ist der wichtigste Einzelbefund des Tages.** Der ganze Shorts-Entwurf
setzt voraus, dass OBS Bildschirm und Webcam als **zwei getrennte Dateien**
schreibt. Beim 12. März war das so. Ob die heutige Konfiguration das noch
tut, ist ungeprüft.

Nachsehen und melden:

- Wie viele Dateien mit dem heutigen Zeitstempel gibt es?
- In welchen Ordnern liegen sie?

Bekannt ist: Am 4. August lag `AvatarWebcam-2026-08-04 01-11-36.mp4` direkt
im Wurzelverzeichnis `F:\`, die Bildschirmaufnahme dagegen in
`F:\MatrixMarketAutoEdit\`. Die Ausgabepfade sind also uneinheitlich.

Ergebnis:

- **Zwei Dateien** → Entwurf trägt, weiter wie geplant.
- **Nur eine Datei** → der Avatar ist fest eingebrannt. Dann muss der
  Kompositions-Plan neu gedacht werden, und wir wären bei genau dem Problem,
  das OpusClip schlecht löst. Das ist kein Weltuntergang, aber es ändert den
  Zuschnitt des Blocks. In dem Fall: **nicht weiterplanen, erst besprechen.**

### 1.6 Aufräumen und sichern

- Webcam-Datei **von Hand** nach `F:\ShortsQuellen\Avatar\` verschieben.
  OBS wird bewusst noch nicht umkonfiguriert — erst wenn feststeht, wo es
  tatsächlich hinschreibt, und mit wachem Kopf.
- Die CSV liegt bereits in `F:\ShortsQuellen\Cursor\`.
- **Rohdateien behalten**, auch nach dem Rendern.
- Danach das Video normal durch den Cutter jagen, wie immer.

---

## 2. Was seit der letzten Übergabe erledigt wurde

Beide offenen Punkte aus Abschnitt 4 der Vorgänger-Übergabe sind geschlossen,
**beide ohne eine Zeile Produktcode**. Nicht wieder aufmachen.

### 2.1 Lautheit — Verdacht widerlegt

EBU R128 über drei Generationen derselben Aufnahme:

| Datei | LUFS (input_i) | True Peak | LRA |
|---|---:|---:|---:|
| Original | −35,76 | −2,37 | 11,90 |
| Render | −35,74 | −2,69 | 8,30 |
| Nachschnitt | −35,85 | −2,84 | 8,40 |

Differenzen: Original → Render **+0,02 LUFS**, Render → Nachschnitt
**−0,11 LUFS**. Beide weit unter der 1-LUFS-Schwelle. **Der Renderpfad macht
die Tonspur nicht leiser.** Die Codesuche nach Audiofiltern entfiel
planmäßig.

Der Rückgang der Loudness Range von 11,90 auf 8,30 LU ist erwartet und kein
Fehler: Der Renderer entfernt Pausen, damit fallen die leisesten Passagen weg.

**Aber:** −35,8 LUFS ist absolut gesehen sehr leise. YouTube zielt auf etwa
−14 LUFS und regelt nur herunter, nie herauf. Die Videos laufen beim
Zuschauer also deutlich leiser als alles daneben. Für Shorts auf
Handylautsprechern ist das relevant. **Als eigener kleiner Auftrag am Ende
der Shorts-Kette vorsehen** (einmalige `loudnorm`-Stufe), nicht im
bestehenden Renderpfad, nicht jetzt.

### 2.2 Vokabeltest — wirkt nicht

Testmaterial: 10-Minuten-Fenster (30:00–40:00) aus
`F:\OLD\2026-03-12 20-03-29.mp4`, dieselbe Datei zweimal transkribiert.

| Wort | ohne Vokabeln | mit Vokabeln |
|---|---:|---:|
| Beerenmarkt | 7 | 6 |
| Bärenmarkt | 13 | 13 |
| Bullenmarkt | 1 | 1 |
| Beermarkt (neu) | 0 | 1 |

Von sieben Fehlstellen blieben sechs identisch falsch, eine wurde zu
**„Beermarkt"** — einem dritten Fehlerbegriff, den es vorher nicht gab. Keine
einzige Korrektur zu „Bärenmarkt". **`--prompt` läuft im Shorts-Block nicht
mit.** Die Frage ist gemessen und erledigt.

### 2.3 Zwei Befunde, die niemand gesucht hat

**Whispers Fehlerquote hängt am Kontextfenster.** Dasselbe Audio ergab über
die ganze Datei 18× „Beerenmarkt" und praktisch kein „Bärenmarkt"; über den
10-Minuten-Ausschnitt 7× „Beerenmarkt" und 13× „Bärenmarkt". Summe je ~20,
also gleich viele Erwähnungen, völlig andere Verteilung — bei gleichem
Modell, gleichen Parametern, gleichen Sekunden. Whisper schleppt den Text der
Vorsegmente als Kontext mit; ein anderer Einstiegspunkt prägt anders. Der
eingebaute Kontext des Materials wirkt stärker als jede vorangestellte
Vokabelliste. **Folge: Transkript-Vergleiche über unterschiedliche Zuschnitte
sind nicht direkt vergleichbar.**

**Hypothese, unbestätigt:** In allen sieben geprüften Kontextstellen meinte
„Beerenmarkt" eindeutig den *Bären* („wir sind im Beerenmarkt", „letzten
Beerenmarkt", „historische Beerenmarkt-Levels"). „Bullenmarkt" wurde separat
und korrekt erkannt. Falls sich das über mehr Stellen bestätigt, ist der
Fehler **nicht mehrdeutig, sondern eine simple Ersetzung im Nachgang** —
sehr viel billiger als jeder ASR-Eingriff. Sieben Fälle sind keine Messung.
Prüfbar mit einem Grep über alle 28 Transkripte, wenn es im Shorts-Block
relevant wird. **Nicht vorher.**

---

## 3. Korrekturen an der Vorgänger-Übergabe

Vier Fehler standen darin. Sie sind hier korrigiert und gelten ab sofort in
dieser Fassung.

**Es sind 28 Transkripte, nicht 27.** `audio.wav.json` unter
`artefakte\repeat\nacht\`, `\old\`, `\recheck\`, `\2C\`. Weiterhin gilt: nie
neu transkribieren.

**`-l de` ist Pflichtparameter, genau wie `-ml 60`.** Ohne ihn fällt
whisper-cli auf Englisch zurück und transkribiert „bear market" statt
„Beerenmarkt". Das hat in diesem Block einen kompletten Lauf entwertet. Gehört
bei nächster Gelegenheit in `docs\repeat\UMGEBUNG.md`.

**`--initial-prompt-file` existiert in dieser whisper-cli-Version nicht.**
Es gibt nur `--prompt PROMPT` als String. Die Behauptung in der alten
Übergabe („`--initial-prompt-file` existiert in der CLI") war falsch.

**Modellwahl: vier Punkte, nicht einer.** Die alte Übergabe hatte den
Abschnitt auf Haiku/Sonnet/Opus eingedampft. Die Project Instructions
verlangen mehr. Verbindlich ist:

> Nenne für jeden Auftrag ausdrücklich: empfohlenes Modell (Haiku, Sonnet oder
> Opus); Thinking-Intensität; Berechtigungsgrenzen; kurze Begründung.
>
> Haiku für rein mechanische, eng spezifizierte Einzelschritte. Sonnet für
> Sammeln, Implementieren, Verifizieren, Stapelläufe. Opus nur, wenn der
> Produktpfad selbst berührt wird oder Verträge entworfen werden.
>
> Opus nicht reflexartig empfehlen. Die Modellwahl ist proportional zum
> tatsächlichen Risiko.
>
> Immer: keine übersprungenen Berechtigungen, kein Commit im selben Auftrag
> wie die Implementierung, Working Tree bleibt für separate Abnahme erhalten.

---

## 4. Der Shorts-Block — Entwurfsstand

Noch nichts implementiert. Das hier ist die Richtung, nicht die Spezifikation.

### 4.1 Die Idee in einem Satz

Ein lokales, zugeschnittenes OpusClip: langes Video rein, Shorts raus — auf
eigenem Material, ohne Upload, ohne Abo, und **ohne den Fehler, den OpusClip
macht**.

### 4.2 Was OpusClip falsch macht und wir nicht

OpusClip verkauft einen Score, der entscheidet, was ein guter Moment ist.
Genau das hat der Repeat-Block **nachweislich widerlegt**: aus 25 beurteilten
Stellen waren die beiden höchsten Scores bewusste Wiederholungen, ein echter
Versprecher lag bei 0,600. Kein Schwellwert trennte die Gruppen.

„Welche 60 Sekunden sind gut?" ist dasselbe Urteilsproblem. Die Maschine
schlägt Kandidaten vor, **der Nutzer wählt** — zehn Sekunden pro Clip in
einer Urteilsseite wie beim Repeat. Die Maschine macht Zuschneiden,
Untertiteln, Rendern. **Von Anfang an so bauen**, nicht erst auf einen
Automatismus hoffen. Das spart die Runde, die der Repeat-Block hinter sich
hat.

### 4.3 Der Kompositions-Ansatz — der eigentliche Vorteil

Der Avatar sitzt in der Aufnahme fest unten rechts. OpusClip müsste ihn aus
dem fertigen Bild herausschneiden und scheitert daran.

Wir haben die Quellen **getrennt** (vorbehaltlich 1.5). Das Short wird also
nicht aus einem Bild herausgeschnitten, sondern **neu komponiert**: Avatar
oben, relevanter Bildschirmausschnitt unten — die Bauform, die man aus vielen
Shorts kennt. Kein Upscaling, kein Kompromiss.

### 4.4 Cursor-Nachführung

Der Mauszeiger ist der beste verfügbare Hinweis darauf, wovon der Nutzer
gerade redet — besser als Bildanalyse, weil er die Aufmerksamkeit direkt
abbildet. Damit lässt sich der vertikale Ausschnitt dorthin führen, wo gerade
erklärt wird, statt starr die Bildmitte zu nehmen.

**Mitschreiben statt erkennen** ist entschieden (siehe 1.1). Zehn Zeilen
Skript, keine Erkennung, keine Fehlerquote. Der Bildweg wäre bei wechselnden
Chart-Hintergründen wackelig und teuer. Für die 28 Altaufnahmen gibt es kein
Protokoll — dort bliebe nur ein fester Ausschnitt oder der Bildweg.

Offen für später: Glättung. Der Zeiger springt, die Kamera darf das nicht.
Träges Nachziehen, Mindestverweildauer, harte Schnitte statt Zittern.

**Effizienz ist eine ausdrückliche Anforderung des Nutzers.** Eine Abfrage
alle 500 ms während der Aufnahme ist im Rauschen. Der teure Bildweg wird
dadurch gerade vermieden.

### 4.5 Die Anforderung an den bestehenden Renderer

Vom Nutzer selbst formuliert und der Kern der Sache: Wenn der Renderer
Passagen aus dem Bildschirmvideo entfernt, müssen **dieselben Schnitte** auf
Webcam-Datei und Cursor-Protokoll angewendet werden. Sonst laufen die Spuren
auseinander.

Der billigste Weg: Der Renderer **gibt seine Schnittliste heraus**, statt sie
nur anzuwenden. Er entscheidet weiterhin allein, es kommt nur eine Datei mehr
heraus. Ob er das heute schon tut, ist ungeprüft — siehe 5.1.

---

## 5. Nächste Schritte nach der Aufnahme

### 5.1 Bestandsaufnahme, rein lesend

Der erste Claude-Code-Auftrag im Shorts-Block. **Sonnet, Intensität mittel,
Berechtigungen eng (rein lesend, kein Schreibzugriff irgendwo),** weil es um
Verstehen geht, nicht um Ändern — aber im Produktpfad gelesen wird.

Zu klären:

1. Erzeugt der Renderer intern eine Schnittliste, und wird sie irgendwo
   ausgegeben oder verworfen? Fundstellen mit Zeilennummer.
2. Wie findet der Renderer seine Eingabedateien — nur oberste Ebene von
   `F:\MatrixMarketAutoEdit\` oder auch Unterordner? (Relevant, weil sonst
   irgendwann eine Webcam-Datei versehentlich als eigenes Video verarbeitet
   wird. `F:\ShortsQuellen\` liegt bewusst außerhalb.)
3. In welchem Format liegt die Schnittliste vor — Zeitstempel, Frames,
   Segmentgrenzen?

**Nichts ändern, nur berichten.** Das Ergebnis entscheidet, ob der
Shorts-Block den Produktpfad anfassen muss. Falls ja: erst dann ist Opus im
Gespräch.

### 5.2 Die offene Frage an den Nutzer

Steht noch aus, er wollte sie in Ruhe beantworten: **Was soll am Ende aus dem
Shorts-Schritt herausfallen?** Optionen von schmal nach breit:

- nur Kandidaten-Zeitfenster zur Auswahl
- Zeitfenster plus geschnittene 16:9-Clips
- fertige vertikale 9:16-Clips
- vertikal plus eingebrannte Untertitel

Er hat gesagt, er hat genaue Vorstellungen. **Fragen, nicht annehmen.** Die
Antwort ändert den Zuschnitt erheblich.

---

## 6. Ablage — gilt verbindlich

```
P:\DimensionWithin-MatrixMarketAutoEditor
├─ docs\repeat\        versioniert   Übergaben, UMGEBUNG.md, ABLAGE.md
├─ labels\repeat\      versioniert   urteile-2026-08-05.json, Vokabeldatei
└─ artefakte\repeat\   ignoriert     Transkripte, Rohausgaben, Probeläufe

F:\ShortsQuellen\Avatar    Webcam-Rohaufnahmen (neu, 7.8.)
F:\ShortsQuellen\Cursor    Cursor-Protokolle als CSV (neu, 7.8.)
```

Beide `ShortsQuellen`-Ordner liegen bewusst **außerhalb** von
`F:\MatrixMarketAutoEdit\`, damit der funktionierende Schneider sie nicht
als Eingabe aufgreift.

- Ausgaben von Läufen nach `artefakte\<auftragsname>\`. Nie auf den Desktop,
  nie nach `C:`.
- **Niemals `git clean -xfd` in diesem Repository** — löscht `/artefakte/`
  samt der teuren whisper-Rohausgaben.
- `labels\` nicht nach `data\` verschieben (steht in der `.gitignore`).

**Werkzeug, außerhalb des Repositorys:**
`P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe`,
Modell `P:\AI\whisper-data\models\ggml-small.bin`.
Durchsatz 3,3–4,1× Echtzeit bei 4 Threads.
**Pflichtparameter: `-l de` und `-ml 60`.** `-ojf` schreibt die Rohausgabe
neben die WAV; sie überlebt jeden Absturz — nach einem Fehlschlag nie neu
transkribieren, sondern nachkonvertieren.

---

## 7. Repositoryzustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        01457e0efa7cfff43101e73848bd2943dab34854
origin      synchron
```

**In diesem Block wurde kein Code geändert und nichts committet.** Der einzige
untracked-Eintrag ist `docs/repeat/REPEAT-3-HANDOFF-2026-08-06.md` — die
Vorgänger-Übergabe, die noch eingecheckt werden muss. **Diese Datei gehört
ebenfalls nach `docs\repeat\`** und fährt beim nächsten Commit mit.

Erzeugte Artefakte (ignoriert, können bleiben):
`artefakte\repeat\lautheit\` (drei loudnorm-Rohausgaben),
`artefakte\repeat\vokabeltest\` (`ohne.json`, `mit.json`,
`ohne-EN-fehllauf.json`).

**Bekannter Schönheitsfehler, nicht durch diese Arbeit verursacht:** Ein
Bestandstest unter `tests\phase2` schreibt nach `-` statt nach stdout und legt
eine Datei namens `-` an. Harmlos, echter Fehler, nicht ohne eigenen Auftrag
reparieren.

---

## 8. Arbeitsweise — bitte beibehalten

**Rollenverteilung.** Der Chat orchestriert und auditiert, Claude Code
implementiert. Der Chat schreibt keinen Produktionscode und committet nicht.

**Jeder Auftrag nennt:** erlaubten Änderungsbereich, verbotene Operationen,
Qualitätsgates mit exakten Befehlen, Berichtsanforderungen. Kein Commit im
selben Auftrag wie die Implementierung. Commit und Push sind eigene, minimale
Aufträge mit Scope-Prüfung als erstem Schritt und Gate **vor** dem Commit.

**Modellwahl:** vier Punkte, siehe Abschnitt 3.

**Berichte prüfen, nicht glauben.** Zahlen gegeneinander rechnen. In diesem
Block hielt die Arithmetik durchgehend — **alle drei Fehler standen in meinen
eigenen Auftragstexten**: fehlender Pfad zur Binärdatei, fehlendes `-l de`,
falsche Anzahl Transkripte. Claude Code hat jedes Mal geprüft statt
angenommen, den Unterschied nachgewiesen (Vergleich der `params`-Blöcke) und
angehalten, statt eigenmächtig zu korrigieren. **Das ist das gewünschte
Verhalten und darf nicht wegoptimiert werden.** Es kostet eine Runde und
spart ein falsches Ergebnis.

**Achtung bei Suchbefehlen:** Ein `find /` über alle Laufwerke läuft auch über
`F:` mit Terabytes an Videomaterial und hängt endlos. In Aufträgen immer den
Suchpfad eingrenzen.

**Zum Nutzer.** Er liest die Claude-Code-Berichte nicht — das ist Aufgabe des
Chats. Er braucht: einen fertigen Prompt zum Kopieren, Modell und Intensität,
und in zwei Sätzen was dabei herauskommt. Er will zügig zum fertigen Produkt
und schätzt Direktheit über Ausführlichkeit. Er ist zu Recht vorsichtig, dass
am funktionierenden Schnittprogramm nichts kaputtgeht. **Wenn er sagt, etwas
klinge falsch, stimmt das** — beim Nachschnitt hat sein Gehör einen echten
Entwurfsfehler gefunden, den keine Kennzahl gezeigt hatte.

Er hat außerdem gute Einfälle im müden Zustand — der gesamte Abschnitt 4
entstand spätabends kurz vor dem Schlafengehen. Mitschreiben statt
diskutieren.

**Bei Nachtläufen:** Frühwarnung nach *jeder* Datei, Aufträge über zehn
Minuten als Hintergrundlauf, vor jedem Start auf verwaiste `whisper-cli.exe`
und `ffmpeg.exe` prüfen, nach jeder Datei `audio.wav` löschen und
`audio.wav.json` behalten.

---

## 9. Was in den neuen Chat gehört

Diese Datei. Alles andere liegt im Repository unter `docs\repeat\` und
`labels\repeat\` oder auf der Platte des Nutzers und kann bei Bedarf
angefordert werden.
