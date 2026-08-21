# Orchestrator-Übergabe — Shorts-Produktionslinie
**Stand: 21. August 2026, Abend. Verfasst zum Abschluss des Orchestrator-Chats vom 14.–21.8.**

Diese Datei ersetzt `ORCHESTRATOR-UEBERGABE-2026-08-14.md` als Einstieg. Die alte
bleibt gültig für alles, was den Langform-Cutter betrifft; für die Shorts-Linie
gilt ab hier diese.

Lies zusätzlich, in dieser Reihenfolge:
1. `artefakte\repeat\uebergabe\BESTAND-2026-08-21.md` — der vollständige Bestand
   mit allen Fundstellen, Konstanten und Modulsignaturen. **Das ist dein
   Nachschlagewerk.** Diese Übergabe erklärt das Warum, der Bestand das Was.
2. `labels\repeat\shorts-kriterien.yaml` — Fassung 0.7.
3. `docs\repeat\SHORTS-KONTEXT-2026-08-09.md` — Abschnitt 5 (Komposition),
   6 (Designsystem), 7 (Avatardatei). An mehreren Stellen überholt, die
   Korrekturen stehen unten.

---

## 0. DAS DRINGENDSTE ZUERST

**Seit dem 14. August ist nichts committet.** HEAD ist `9399fd6`, gepusht.
Der Arbeitsbaum trägt 10 geänderte und 29 untracked Dateien — darunter
**zehn von zwölf Produktionsmodulen** der Stufen 3a bis 5d, `build.py` mit
2078 Zeilen, und zwölf Testdateien.

Ein `git clean -fd` würde sieben Tage Arbeit vernichten. Ein
`git checkout -- .` die Kriteriendatei, die Vokabeldatei und vier weitere
Module.

**Das gehört gesichert, bevor irgendetwas anderes passiert.** Der Nutzer
committet selbst; er wurde darauf hingewiesen. Frag beim ersten Kontakt nach,
ob es geschehen ist.

---

## 1. Deine Rolle

Du baust nicht selbst. Du liest Berichte, rechnest Zahlen nach, triffst
Entscheidungen und schreibst Aufträge für Claude-Code-Fenster. Der Nutzer
schickt sie ab und bringt die Berichte zurück.

**Bei jedem Auftrag nennst du Modell, Denktiefe, Berechtigungsgrenzen und die
Begründung dafür.**

### 1.1 Modellwahl

- **Sonnet 5**, wenn alles feststeht und nur gebaut wird. Zahlen vorgegeben,
  Verfahren bekannt, Prüfsteine benannt.
- **Opus**, wenn der Auftrag ein Verfahren offen lässt oder eine Zahl gefunden
  statt gesetzt werden muss. Auch für die Zerlegung (Stufe 2), weil dort
  geurteilt wird.
- **Denktiefe mittel** reicht fast immer. Hoch nur, wenn wirklich entworfen wird.

Der Nutzer hat ausdrücklich gesagt: Opus nicht aus Gewohnheit. Wo Sonnet
reicht, Sonnet.

### 1.2 Freigaben — kurz halten

Nenne dem Nutzer nur die Ordnerfreigabe, in dieser Form:

> **Freigabe:** Repo · `F:\MatrixMarketAutoEdit\` lesend

Nicht mehr. Die feinen Regeln (was gelesen, was geschrieben wird) gehören in
den Auftragstext, nicht in die Frage an den Nutzer.

Die Ordner, die vorkommen:
```
P:\DimensionWithin-MatrixMarketAutoEditor\   das Repo, immer da
F:\MatrixMarketAutoEdit\                     gerenderte Videos, Rohaufnahmen
F:\ShortsQuellen\                            Avatar, Cursorprotokolle
P:\DimensionWithin\DW Logo\                  Bildmarke
%LOCALAPPDATA%\DimensionWithin\              Journale, Proposals, Bindungen
%APPDATA%\obs-studio\basic\scenes\           OBS-Szenensammlungen
```
**Niemals `F:` als Ganzes freigeben oder rekursiv durchsuchen** — dort liegen
Terabyte Videomaterial, der Lauf hängt.

**WICHTIG, dreimal passiert:** Wenn `build.py` im Spiel ist, braucht der
Auftrag `F:\MatrixMarketAutoEdit\` **lesend**. `shorts-job.json` verweist
dorthin. Ein Auftrag, der `build.py` verlangt und `F:` sperrt, widerspricht
sich und hält an.

### 1.3 Auftragsaufbau

Jeder Auftrag enthält:
- erwarteter Repo-Zustand, mit **BEKANNT UND HARMLOS**-Liste (siehe 3.1)
- Verbotsliste — was nicht angefasst werden darf
- was gebaut wird, mit belegten Zahlen
- **Prüfsteine** mit erwarteten Istwerten, nicht nur „bestanden"
- Gates
- Berichtspfad
- ein **„Angehalten"**-Abschnitt mit dem Satz: *Trifft eine Annahme nicht zu,
  ist Melden richtig und Weiterbauen falsch.*

### 1.4 Die Regel, die ich mir am 14.8. selbst gegeben habe

**Ein Auftrag darf einen Codefehler nicht behaupten, wenn ich den Code nicht
gelesen habe. Er darf ihn nur fragen.**

„Prüfe, ob X gilt, und ändere nur, wenn nicht" statt „X ist falsch, ändere es."

Ich habe diese Regel danach noch dreimal gebrochen und jedes Mal dafür bezahlt.
Halte dich daran.

---

## 2. Wo die Linie steht

### 2.1 Die Kette, wie sie heute läuft

```
OBS-Aufnahme  →  Cutter rendert  →  F:\MatrixMarketAutoEdit\Rendered\
                                     │
  1. Werkzeug (app.py)          →  shorts-job.json
  2. Avatar-Nachschnitt         →  avatar-cut.mp4
  3. Transkript                 →  transkript-rendered.json
  4. Zerlegung (Fenster!)       →  kandidaten.json
  5. Urteil (Server + Mensch)   →  urteile-<Zeitstempel>.json
  6. Bau (build.py)             →  33 × short.mp4
```

Die vollständigen Befehle stehen in Abschnitt 4 des Bestandsberichts.

### 2.2 Was gebaut ist

Stufen 0, 1, 2, 3a, 4, 5a, 5b, 5c — alle vorhanden und im Bau verdrahtet.
`build.py` macht aus Auftragsdatei und Kandidaten in **einem** Aufruf fertige
Shorts, mit allen Werten abgeleitet.

**5d (Endcard) existiert, wird aber bewusst nicht aufgerufen.** Eine Endcard
beendet ein Short sichtbar und zerstört die Wiederholschleife, auf die die
Linie zielt. Sie bleibt für spätere Langvideos liegen. Das ist im Code an vier
Stellen vermerkt.

### 2.3 Was NICHT existiert

**Stufe 3b, die Mausverfolgung.** Kein Modul, keine Glättung, kein Rückfall bei
negativem x. Was heute läuft, ist 3a: fester Ausschnitt, mittig bei Versatz 416.

Der Nutzer hat mehrfach gefragt, warum sich das Chart nicht bewegt. Antwort:
weil es diese Stufe nicht gibt. **Das ist der nächste große Baustein.**

**Und die Kandidatensuche hat kein Modul.** `kandidaten.json` entsteht in einem
Claude-Code-Fenster mit einem Auftragstext, nicht durch Code. Das ist Absicht —
dort wird geurteilt. Der Auftragstext steht in Abschnitt 6.3.

---

## 3. Betriebsfallen

### 3.1 Die BEKANNT-UND-HARMLOS-Liste

Gehört in **jeden** Auftrag, sonst hält das Fenster an:

```
BEKANNT UND HARMLOS, kein Grund anzuhalten:
- die untracked Datei `-` im Wurzelverzeichnis (0 Byte, Shell-Artefakt)
- docs\repeat\ORCHESTRATOR-UEBERGABE-2026-08-14.md und diese Uebergabe
- die Berichtsordner unter artefakte\repeat\
- die zehn untracked Module unter src\matrix_auto_cutter\shorts\ und die
  zwoelf untracked Testdateien unter tests\
- artefakte\repeat\shorts-bau-parallel\abbruch\ -- unvollstaendige
  Kandidatenordner aus einem harten Abbruch, liegen lassen
- der Test tests\test_cut_proposal_approval.py::
  test_review_selection_bridge_... ist flatterhaft. Faellt nur er, im
  Alleinlauf nachpruefen und vermerken, nicht anhalten.
Anhalten nur bei Abweichungen, die HIER NICHT genannt sind.
```

### 3.2 Gates

Immer über PowerShell, **nie** über Git-Bash:
```
uv run python -m pytest        (niemals: uv run pytest)
uv run ruff check .            -> "All checks passed!"
mypy src                       -> genau 20 vorbestehende Fehler
                                  repeat\cut.py (3), cutcli.py (7), cli.py (10)
```
Stand 21.8.: **2429 Tests bestanden, 1 Skip.** Andere Verteilung bei mypy heißt,
dass Produktcode berührt wurde.

`ruff` wurde eine Zeit lang vergessen — es ist ein etabliertes Gate und gehört
in jeden Auftrag.

### 3.3 Sperrliste

Nie ändern, außer der Auftrag gibt es ausdrücklich frei:
```
cut_proposal.py  intro.py  outro.py  protection.py  render.py  loudness.py
event_lag.py  product_runner.py  review_app.py  review.py  approval.py
src\matrix_auto_cutter\repeat\*
native\**  und die installierte matrix-auto-cutter-obs.dll
START-MATRIX-AUTO-CUTTER.cmd  START-ALLES.cmd
%APPDATA%\obs-studio\**
docs\matrix-auto-cutter-architecture-plan-v0.2.md
```
`START-ALLES.ps1` ist inzwischen freigegeben worden (Cursor-Wächter), aber nur
mit ausdrücklicher Nennung.

Die drei Urteilsdateien unter `artefakte\repeat\shorts\2026-08-07 11-35-16\`
und `urteile-verworfen\` werden **nie** angefasst. Urteilszeit ist das einzige
Artefakt, das sich nicht neu erzeugen lässt.

### 3.4 Der Cutter läuft täglich

Der Nutzer schneidet damit seine Langvideos. Jede Änderung an `intro.py`,
`outro.py`, `cut_proposal.py` braucht einen eigenen Prüfstein und wird nicht
nebenbei gemacht.

---

## 4. Was heute entschieden wurde, und warum

### 4.1 Die Aufnahmeseite ist fertig

Vier Dinge wurden am 17.–19.8. behoben, alle rückwirkend nicht nachholbar:

- **Cursor-Wächter.** Läuft automatisch über `START-ALLES.ps1`, hört auf
  obs-websocket `RecordStateChanged`, tastet mit 8 Hz ab. Vorher wurden achtzehn
  Aufnahmen ohne Protokoll gemacht. Einmalsperre über benannten Mutex — **nicht**
  über WMI, `Win32_Process` existiert auf diesem Rechner nicht.
- **Webcam auf 1920×1080.** Vorher 630×422, der Avatar musste hochgerechnet
  werden. Umgestellt wird das in **Veadotube**, nicht in OBS.
- **Chroma Key entfernt.** Die Veadotube-Quelle läuft jetzt als **Spielaufnahme**
  mit „Transparenz erlauben", nicht als Fensteraufnahme. Fensteraufnahme kann
  keine Transparenz — daher das graue Rechteck und der Farbstich, den der Chroma
  Key erzeugte.
- **Outro-Bindung repariert.** `outro-scene-binding.json` zeigte auf die
  Szenensammlung `Unbenannt`, die am 19.8. in `Experimental` umbenannt wurde.
  Dadurch fiel `resolve_outro()` auf `scene_collection_missing`, die 900-Frame-
  Schutzzone griff nie, und im Outro wurden Stille-Schnitte gesetzt. Behoben,
  belegt: `outro_resolution.status: "resolved"`, genau ein `outro_excess_tail`.

**Wenn der Nutzer die Szenensammlung erneut umbenennt, bricht das wieder.**
Der Digest der Bindungsdatei geht über den Namen.

### 4.2 Transkript: turbo mit Vokabeldatei

`ggml-large-v3-turbo`, gemessen gegen `ggml-medium`: turbo lässt keine Wörter
aus, setzt siebenmal so viele Satzzeichen, halb so viele unbrauchbare
Zeitstempel.

**Die Vokabeldatei wirkt** — und zwar deutlich: 717 Kommas und 673
Großbuchstaben statt null, 5009 Tokens statt 3963. Sie steht in
`labels\repeat\whisper-vokabular.txt` und wird von `transcript.py` automatisch
gezogen.

**Aber sie wirkt nur im ersten Verarbeitungsfenster.** Derselbe Satz wurde in
einem 12-Sekunden-Ausschnitt korrekt als „wrong-footed" erkannt, im Volllauf
über 20 Minuten als „gerong-footed". Fachbegriffe im hinteren Teil einer
Aufnahme sind unzuverlässig. Dieselbe Ursache dürfte hinter dem Einbruch der
Punktdichte stehen: 5,4 % im ersten Viertel, 0,4 % im letzten.

**Kodierung:** Die Datei muss UTF-8 ohne BOM sein. Sie war es zweimal nicht.
Schreib sie per PowerShell mit `UTF8Encoding($false)`, und prüfe mit
`Get-Content -Encoding UTF8` — ohne den Schalter zeigt PowerShell 5.1
Buchstabensalat, obwohl die Datei stimmt.

### 4.3 Die Schnittkanten

Drei Verfahren übereinander, in dieser Reihenfolge:

1. **Rasten** (`loop_point.py`): auf Wortgrenzen, **nur zusammenziehend**, nie
   erweiternd. Ausnahme: liegt eine Marke höchstens 150 ms hinter einem
   Wortanfang, gilt das Wort als gewollt.
2. **Stillevorlauf** (`level_cut.py`): Liegt vor dem ersten Ton mehr als 500 ms
   Stille, wird die Marke nach vorn geschoben. Toleranz gegen kurze
   Unterbrechungen: 120 ms.
3. **Pegelschnitt** (`level_cut.py`): sucht die leiseste Stelle.
   **Startgrenze nur rückwärts, 150 ms.** Endgrenze beidseitig, 250 ms, und
   nimmt die **Mitte des leisesten Bereichs** statt des tiefsten Punktes.

**Warum asymmetrisch:** Der Nutzer hat vier Shorts angehört. Die Enden waren
tadellos, die Anfänge nicht. Ein Schnitt, der zu spät beginnt, schneidet ein
Wort an; ein zu früher ist harmlos.

**Warum das Medianverfahren nur am Ende greift:** In 150 ms passt kein 100-ms-
Bereich. Gemessen: 94 % Rückfall an der Startgrenze, der Fehlerfall trat in
null von 4984 Fensterlagen auf. Am Ende dagegen wandert die Marke 699-mal aus
einem Wort heraus und nur 256-mal hinein.

**Ungelöst und bewusst so gelassen:** Kandidat 5 der Aufnahme vom 19.8. Dort
liegen eine 300-ms-Aufhellung und ein 400-ms-Halteton so nah beieinander, dass
keine Toleranzschwelle sie trennt. Zwei von 33 Kandidaten — der Nutzer sortiert
sie beim Urteilen aus.

**Was kein Verfahren kann:** eine Pause finden, die im Ton nicht existiert. Bei
80 % der Wortübergänge ist die Lücke im Transkript exakt 0 ms. Wo der Sprecher
zwei Wörter zusammenzieht, gibt es akustisch keine Grenze.

### 4.4 Der Bau ist achtmal schneller geworden

```
20.8. früh    515 s je Short
20.8. abends   63 s je Short
```

Drei Schritte, in der Reihenfolge ihrer Wirkung:

- **Framezahl-Cache in der Seitendatei** (58,3 %). `ffprobe -count_frames`
  dekodiert eine 433-MB-Datei Bild für Bild. Das Ergebnis ändert sich nie und
  wird jetzt neben der Datei abgelegt. Für `F:` in einem Ausweichverzeichnis,
  weil dorthin nicht geschrieben wird.
- **Werte einmal je Lauf messen statt je Kandidat** (47 %). Elf ffprobe-Aufrufe
  je Kandidat auf acht reduziert.
- **Parallelisierung** (16,6 %). `--parallel 4`, gemessen bester Wert. Bei 8
  wird es wieder langsamer — ab 4 liegt die CPU in der Bauphase bei 96–99 %.

**Die Arbeitskopie war ein Fehlschlag** (3,3 % langsamer) und ist trotzdem drin.
Sie half gegen wiederholtes *Lesen*, der Engpass war wiederholtes *Dekodieren*.
Schadet nicht.

**Offen:** Der Strg+C-Beleg fehlt. Drei Versuche scheiterten an der
Hintergrundverwaltung von Claude Code. Der Nutzer sollte einmal von Hand
abbrechen und `Get-Process ffmpeg` prüfen — verwaiste ffmpeg-Prozesse sind
unangenehm.

### 4.5 Die Kriteriendatei, Fassung 0.7

Vier Fassungen an zwei Tagen. Was heute gilt:

**Länge: 8 bis 15 Sekunden, Obergrenze 20.** Gestützt allein auf Zhao et al.
2024 (KDD, Kuaishou/WeChat-Logs): Wiederholungsrate fällt monoton mit der
Länge, der kürzeste Bereich bis 14 s ist deutlich abgesetzt.
Die frühere Begründung über eine „Lücke oberhalb 20 s" steht unter `widerlegt` —
sie war ein Artefakt strengerer Kriterien.

**Interpunktion trägt die Grenzen** — das stärkste Kriterium, das je in der
Datei stand. An zwei Aufnahmen unabhängig: Faktor 13,3 und 14,8 über Zufall.
Am 19.8. lagen **alle 33 Kandidatenenden** auf einem Satzzeichen.

**`grenze_liegt_auf_einer_sprechpause` ist bei dieser Länge tot** — 7 % gegen
8,1 % Zufall. 33 Schnittfugen auf 20 Minuten sind eine alle 37 Sekunden; ein
Zwölfsekünder enthält im Mittel keine.

**`polarisierung` trennt nicht mehr** — 44 von 45, dann 26 von 33. Bleibt als
Beobachtung drin. Es hat seine Arbeit getan: In Lauf 2 fielen ohne dieses
Kriterium genau die stärksten Aussagen weg.

**`schleifenfaehiger_uebergang` ist neu und unbelegt.** Weisung des Nutzers:
Ein Short, dessen letztes Wort zum ersten passt, lässt die Wiederholung nicht
auffallen. Zwei Bauformen — Beginn mit Bindewort, oder offenes Ende. **Nicht
als Wortliste umsetzen**, das ist maschinell zu leicht zu erfüllen.

**Widerlegt und nicht wieder aufnehmen:** „offener Schluss erzeugt
Wiederholung" — die Zeigarnik-Metaanalyse 2025 findet ein Verhältnis von 0,99,
also keinen Effekt.

### 4.6 Der Urteilsdurchgang vom 21.8.

**29 von 33 angenommen, 87,9 %.** Der einzige frühere Durchgang lag bei 14 von
21, also 67 %.

```
hoch      9 ja / 1 nein     90 %
mittel   18 ja / 2 nein     90 %
niedrig   2 ja / 1 nein     67 %
```

**Die Sicherheitsstufe trägt nicht mehr.** Am 14.8. war sie 13 von 13 bei
„hoch" und 1 von 4 bei „mittel". Jetzt sind hoch und mittel ununterscheidbar —
`im_zweifel_mitgeben` hat sie verwässert. Das war das Kriterium, auf das die
spätere Automatik setzen sollte. **Das ist ein offener Punkt.**

Länge trennt auch nicht: angenommen Median 10,55 s, abgelehnt 11,26 s.

**Wichtig für die Auswertung:** Die Urteilsseite zeigt das **Rohvideo** und
springt per Zeitspanne. Sie zeigt keine gebauten Shorts, keinen Avatar, keine
Untertitel und keine Schnittkorrekturen. Die vier Ablehnungen sind inhaltliche
Urteile über die Marken aus `kandidaten.json`, nicht über die fertigen Shorts.
Der Nutzer hat die fertigen Fassungen anschließend geprüft und für gut befunden.

Urteilsdatei: `artefakte\repeat\shorts\2026-08-19 17-26-15\urteile-2026-08-21-105656.json`

---

## 5. Meine Fehler, damit du sie nicht wiederholst

**Behaupten statt fragen.** Ich habe dreimal einen Codefehler in einen Auftrag
geschrieben, den ich aus einem Bericht erschlossen, aber nie im Code gesehen
hatte. Jedes Mal war er schon behoben oder existierte nicht.

**F: sperren und gleichzeitig `build.py` verlangen.** Dreimal. Der Bau liest
das gerenderte Video von dort.

**„Durchgehend still" wörtlich verlangen.** Ein einziger 10-ms-Block über der
Schwelle setzte die Messung zurück. In echtem Ton gibt es das nicht — Nachhall
und Musikakzente durchbrechen jede Schwelle.

**Byteexakte Prüfung nach verlustbehafteter Kodierung verlangen.** Unmöglich,
nicht schwer. H.264 mit `crf 18` verändert jedes Pixel.

**Aus einer Zahl auf einen Zusammenhang schließen.** Die „Lücke oberhalb 20
Sekunden" war ein Artefakt der Kriterien, kein Befund am Material.

**Zu lange an einem Fall drehen.** Drei Runden für zwei von 33 Kandidaten. Wenn
ein Feinschliff die dritte Runde erreicht, ist er es meist nicht wert.

**Und die häufigste Rückmeldung des Nutzers:** zu viel Text, Fragen zu
beiläufig. Fragen gehören kurz und nach oben.

---

## 6. Offene Punkte, nach Dringlichkeit

### 6.1 Committen

Siehe Abschnitt 0. Sieben Tage ungesichert.

### 6.2 Stufe 3b — die Mausverfolgung

**Der nächste große Baustein.** Das Material ist da: Der Wächter zeichnet seit
dem 17.8. mit 8 Hz auf, es gibt Protokolle vom 19. und 21.

**Die Regel, die der Nutzer will — Totband, nicht Nachführen:**

> Solange der Cursor im sichtbaren Bereich ist, steht das Bild. Erst wenn er
> nach links oder rechts hinausläuft, fährt der Ausschnitt weich nach. Nicht
> ruckartig, aber reaktiv.

Das ist besser als permanentes Zentrieren — es entspricht dem, was ein
Kameramann täte.

**Die Zahlen dafür stehen fest:** Ausschnitt 1728 px in einer 2560er Quelle,
also 832 px Spielraum. Bei mittiger Position (Versatz 416) sieht der Short die
Spalten 416 bis 2144. Der Cursor darf sich darin frei bewegen.

**Weitere Vorgaben aus früheren Entwürfen:**
- Median über 3 Messwerte (300 ms bei 8 Hz) gegen Ausreißer
- Totzone rund 100 px in der Quelle
- Mindestverweildauer 1 Sekunde nach einer Fahrt
- Fahrtdauer nach Weglänge, 350 bis 700 ms, weich an beiden Enden
- **Negatives x hält die Position** — DISPLAY2 liegt bei −2560,0. Gemessen:
  31 % bis 58 % der Zeilen tragen negatives x. Kein Randfall.
- **Jedes Keep-Segment für sich glätten.** Kein Filterfenster über eine
  Schnittkante hinweg — dort springt der Cursor scheinbar, weil Stille
  herausgeschnitten wurde. Dafür ist `frame_map.py` da.

Die Werte sind Startwerte. Der Nutzer stellt sie am ersten fertigen Short nach
Gefühl ein.

### 6.3 Die Kandidatensuche automatisieren

`kandidaten.json` entsteht heute in einem Claude-Code-Fenster. Der Auftragstext
ist stabil geworden und steht in
`artefakte\repeat\shorts-zerlegung-1726\BERICHT-2026-08-19.md` als Vorlage.

**Zwei Dinge, die dort zwingend hineingehören:**
- `enthaelt` nimmt **ausschließlich** eine Liste von Ganzzahlen. Objekte darin
  lassen den Bau mit `candidates_unreadable` abbrechen. Passiert einmal.
- Die lückenlose Karte: alle Abschnitte des Videos, ohne Loch, mit Grund bei
  jedem verworfenen. Der Nutzer sichtet das Video nicht selbst — er liest die
  Karte und sieht gezielt in die Lücken.

Der Nutzer will das langfristig als **geplante Aufgabe in Claude Code auf
seinem Rechner**, nicht über die API. Begründung: Die Zerlegung braucht
Kriteriendatei, Transkript und Schnittliste — alles lokal.

### 6.4 Die Verkettung

Fünf der sechs Schritte sind reine Verkettung — alle Werte stehen in
`shorts-job.json`. Ein Modul könnte sie aus einem Videonamen heraus
nacheinander anstoßen: Auftragsdatei, Transkript, Avatar-Nachschnitt, Bau.

Nur die Zerlegung braucht ein Fenster, und das Urteil den Menschen.

### 6.5 Upload und Staffelung

Der Nutzer hat einen fertigen Plan, aber nichts davon ist gebaut.

**Was schon da ist:** `kandidaten.json` trägt je Kandidat `titel`,
`begruendung` und `traegt_welche_aussage`. Der Titel ist unmittelbar brauchbar,
die Beschreibung lässt sich daraus bilden.

**Die Staffelung** soll deterministisch sein: n angenommene Shorts über
24 Stunden verteilen, Startzeitpunkt mittags. Aufnahmen entstehen zwischen
9 und 13 Uhr.

**Die Wochenstruktur des Nutzers:**
```
Mo, Di, Mi, Sa   normale Videos
Do abends        Member Meeting (TruthPill Community)
Fr morgens       Freischaltung des Meetings für Supporter
So abends        Livestream, offen
```
An Tagen, an denen am nächsten Tag kein Content kommt, auf 48 Stunden staffeln.

**Was ungeklärt ist und recherchiert werden muss:**
- YouTube-API: Tageskontingent für Uploads, Grenzen für geplante
  Veröffentlichungen. Der Nutzer sagt, dort habe sich etwas geändert. Ob ein
  Batch-Upload Grenzen umgeht, ist offen.
- **Veröffentlichungsdichte.** Ob sechs bis achtzehn Shorts täglich sich
  gegenseitig im Feed verdrängen, weiß niemand. Der Kuaishou-Befund sagt dazu
  nichts.

### 6.6 Die Rückmeldung von draußen

Der blinde Fleck der ganzen Linie. Welche Shorts liefen, welche nicht. Ohne
diese Zahlen bleibt jede Kriterienänderung eine Vermutung — auch die
Schleifenregel.

Zehn Zeilen aus YouTube Studio wären der Anfang: Länge und Aufrufe der
veröffentlichten Shorts. Der Nutzer hat es zweimal angeboten, es ist nie
geschehen.

### 6.7 Kleinere Punkte

- **Die Sicherheitsstufe trägt nicht mehr** (4.6). Sie war der geplante Weg zur
  Automatik. Braucht ein Ersatzkriterium oder eine schärfere Setzung.
- **`endcard.py` hängt an `canvas.BACKGROUND_COLOR_HEX`** und hat dadurch die
  Shorts-Entscheidung „Schwarz statt Ink" mitbekommen. Für Langvideos gilt Ink.
  Gehört getrennt.
- **Ein Frame Differenz** zwischen Proposal-Arithmetik (`avatar_axis.py`,
  46543) und tatsächlichem Renderergebnis (46542). Die Achsenprüfung ist
  deshalb eine Warnung mit Grenze 5. Ursache ungeklärt.
- **Zahlwörter** werden gelegentlich falsch zerlegt: „54-zig" statt
  „vierundfünfzig". Rastet das Werkzeug darauf, schneidet es mitten ins Wort.
  Die Vokabeldatei nimmt die häufigsten auf, aber siehe 4.2.
- **`avatar_axis.py` hat keine eigene Testdatei.**
- **Der Stinger ruckelt gelegentlich.** Notlösung: Übergangsüberschreibung je
  Szene, 250 ms Überblende. Advanced Scene Switcher wäre das richtige Werkzeug,
  ist notiert, nicht installiert.
- **Die Datei `-`** im Wurzelverzeichnis entsteht bei Testläufen. Harmlos, nicht
  löschen — sie ist ein Prüfstein dafür, dass kein Fenster eigenmächtig
  aufräumt.

---

## 7. Bestand am 21.8.2026

```
gerenderte Videos       27  (F:\MatrixMarketAutoEdit\Rendered\)
Rohaufnahmen            34
Avatardateien           44  (F:\ShortsQuellen\Avatar\)
Cursorprotokolle        23  (11 Paare + 1 einzelne csv vom 7.8.)
Tests                 2429  bestanden, 1 Skip
```

**Die Aufnahme `2026-08-21 10-46-08` ist noch nicht durch die Kette gelaufen.**
Sie ist von heute Vormittag, 584,9 s gerendert, Avatar 1920×1080, Cursorpaar
vorhanden mit 5159 Zeilen. **Die erste Aufnahme unter Fassung 0.7 und mit
vollständig reparierter Aufnahmeseite.**

Fertig gebaut liegen:
- `artefakte\repeat\shorts-urteil\` — 33 Shorts der Aufnahme vom 19.8., der
  Stand, den der Nutzer beurteilt hat
- `artefakte\repeat\shorts-final\` — dieselben, vor der Stillevorlauf-Regel

---

## 8. Wie der Nutzer arbeitet

Er schätzt **Direktheit über Ausführlichkeit** und hat mehrfach darum gebeten,
Fragen kurz und ganz oben zu stellen statt sie in Prosa zu vergraben.

Er hört sehr genau hin. Mehrere der wichtigsten Befunde dieser Woche kamen von
ihm, nicht aus einer Messung: das gedehnte „mhh" als Stilmittel, dass Zeigewörter
durch das mitlaufende Chart gedeckt sind, dass eine Endcard die Schleife
zerstört, dass polarisierende Aussagen fehlten, dass der Bau zu langsam ist.

**Wenn er sagt, etwas klinge falsch, dann klingt es falsch** — auch wenn die
Zahlen zunächst etwas anderes sagen. Zweimal hat sich seine Beschreibung als
präziser erwiesen als meine Diagnose.

Er arbeitet schnell und überliest Fragen, wenn sie zu weit unten stehen. Stell
höchstens zwei, und stell sie zuerst.
