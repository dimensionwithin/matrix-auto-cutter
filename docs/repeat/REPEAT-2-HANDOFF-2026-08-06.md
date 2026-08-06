# Matrix Auto Cutter — Übergabe an neuen Chat

Stand: 6. August 2026
Vorgänger-Chat: REPEAT-1A2 (ASR-Adapter), REPEAT-1A3 (Randdetektor) und zwei
Korrekturpakete. Alles abgeschlossen und gepusht.

---

## 1. Verifizierter Repositoryzustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        a3459558dd52bf83b0d2821ca3585ff6845d92bc
origin      a345955  — synchron, Working Tree sauber
```

Commit-Kette dieses Arbeitsblocks, alle gepusht:

```
a345955  fix: keep ordinals intact, merge duplicate candidates, add ASR prompt
82cad37  fix: make whisper conversion robust for full-length recordings
66a389f  feat: add boundary echo detector and diagnostics 1.1
d10edbf  feat: add whisper.cpp ASR adapter for repeat diagnostics
113a909  ← Ausgangspunkt dieses Blocks
```

192 Tests im Paket `repeat`, 100 % Coverage. Gesamtsuite 1563 passed, 1 skipped.
Das Paket ist weiterhin **vollständig isoliert** — kein bestehendes Modul
importiert es, der Produktpfad ist unberührt.

---

## 2. Was jetzt funktioniert

**Ein Befehl von der Rohaufnahme zur Diagnose.**

```
uv run python -m matrix_auto_cutter.repeat.cli ^
  --source "F:\OLD\2026-02-19 20-00-22.mp4" ^
  --whisper-binary "P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe" ^
  --whisper-model "P:\AI\whisper-data\models\ggml-small.bin" ^
  --threads 4 ^
  --work-dir <dir>\work ^
  --emit-transcript <dir>\transcript.json ^
  --out <dir>\diagnostics.json
```

ffprobe → ffmpeg → whisper.cpp → Konvertierung → Diagnose. Alles über einen
injizierbaren Prozess-Seam, Tests ausschließlich mit gefälschtem Runner.
Keine neue Abhängigkeit. Zeiten sind quellabsolut.

Gemessen: **4,06-fache Echtzeit** bei 4 Threads. Eine Stunde Aufnahme ≈ 15 min.
whisper läuft mit BELOW_NORMAL_PRIORITY_CLASS, der Rechner bleibt benutzbar.

**Zwei Detektoren**, beide diagnostisch, keiner entscheidet:

- `detect.py` — Äußerungsvergleich, findet lange Neuansätze
- `boundary.py` — gleitendes Wortfenster über die Äußerungsgrenze, findet
  kurze Echos. Fenster 3–8 Wörter, Schwelle 0,70

Diagnosevertrag `repeat_diagnostics/1.2`: `detector` ist eine Liste, meldet
dasselbe Paar von beiden Detektoren, entsteht ein Kandidat mit zwei Scores.

---

## 3. Der wichtigste Befund: 25 beurteilte Stellen aus echtem Material

Vier Aufnahmen, 3,57 Stunden Sprache, 15 774 Wörter. 28 Rohkandidaten,
nach Zusammenfassung 25 Stellen. Der Nutzer hat **jede einzelne angehört
und beurteilt** (`urteile.json`, siehe §7).

```
5  Versprecher      20 %
19 bewusst          76 %
1  Unsinn            4 %

utterance   10 Kandidaten,  3 Versprecher   30 %
boundary    18 Kandidaten,  2 Versprecher   11 %
```

**Der Score trennt nicht.**

```
boundary   Versprecher:  0,783   0,875
           bewusst:      0,714 … 0,875 … 1,000   1,000
utterance  Versprecher:  0,600   0,608   0,972
           bewusst:      0,560 … 0,705 … 1,000
```

Die beiden höchsten Werte überhaupt sind bewusste Wiederholungen. Ein echter
Versprecher liegt bei 0,600. **Kein Schwellwert trennt die Gruppen.**
Schwellwert-Tuning ist damit erledigt — es bringt nichts. Wer es trotzdem
versucht, wiederholt einen Fehler, der hier bereits gemessen wurde.

### Die dreiteilige Fehlertaxonomie

| Klasse | Beispiel | schneiden? |
|---|---|---|
| Neuansatz | „…seit Februar." / „Ja, die im Grunde erstmalig denken…" | ja |
| Versehentliches Echo | „in dem Sinne?" / „In dem Sinne, …" | ja |
| **Stilmittel** | „500, 700 Prozent… 500, 700 Prozent", „definitiv, definitiv" | **nein** |

Klasse 3 ist der Normalfall, nicht die Ausnahme: der Nutzer setzt Wiederholung
bewusst als rhetorisches Mittel ein. Klasse 2 und 3 sind **im Text nicht
unterscheidbar** — der Unterschied liegt in der Absicht, teils im Bild.
Daraus folgt zwingend: **nichts wird vorausgewählt, nichts ohne den Nutzer
geschnitten.** Die frühere Idee „Neuansätze vorausgewählt, Echos abgewählt"
ist durch die Daten widerlegt.

### Vorgabe des Nutzers

> „Wir sollten lieber in die Richtung korrigieren, dass wir zu wenig
> wegnehmen anstelle von zu viel."

Und: bei einer echten Wiederholung ist **die zweite Passage die relevante** —
die erste bricht ab, die zweite trägt den Gedanken weiter. Default beim
Schneiden also: erste Passage raus, zweite bleibt.

---

## 4. Offene Lücke, bewusst nicht geschlossen

**Wiederholungen innerhalb einer Äußerung sieht heute niemand.** Beide
Detektoren vergleichen nur *zwischen* Äußerungen.

- „Beerenmarkt, Beerenmarkt, Beerenmarkt, Beerenmarkt" — nie gemeldet
- Der bestätigte Versprecher vom 08.01. („Januar, sondern am 20." /
  „Januar, ähm, am 20.") ist durch die Ordnungszahl-Korrektur in eine
  einzige Äußerung gewandert und wird seither **nicht mehr gefunden**

Das ist der Preis einer sonst richtigen Korrektur (im Deutschen beendet ein
Punkt nach einer Ziffer keinen Satz). Ein dritter Detektor „Wiederholung
innerhalb einer Äußerung" wäre die Antwort — aber erst mit mehr Etiketten.

---

## 5. Der nächste Auftrag: das Wegwerf-Werkzeug wird fest (Option B)

Entschieden gegen REPEAT-1B im Produkt, **für** den schnellen Weg. Begründung:
der Nutzer schneidet damit diese Woche, und jedes Meeting liefert nebenbei
neue Etiketten — die 25 Urteile waren mehr wert als alles Tuning davor.

Der Prototyp existiert und hat funktioniert: `review.html`, eine einzelne
Datei, Kartenstapel, `<audio>` je Stelle, drei Knöpfe, Tasten 1/2/3,
Download als JSON. Der Nutzer hat 25 Stellen in etwa einer Viertelstunde
beurteilt. **Das ist die Vorlage.**

Zu bauen, alles im isolierten Paket:

| Teil | Inhalt |
|---|---|
| **Schnipsel** | Aus `diagnostics.json` je Stelle ein Audioschnipsel schneiden, der beide Passagen plus 2 s Vor- und Nachlauf enthält |
| **Review-HTML** | Erzeugung aus Diagnose + Schnipseln, eine Datei, kein Server, kein CDN, kein Framework, Zustand im Speicher, Download als `urteile.json` |
| **Schnittliste** | Aus `urteile.json` eine ffmpeg-Schnittliste erzeugen: für jedes Urteil „Versprecher" die **erste** Passage entfernen |
| **CLI** | Ein Befehl von der Aufnahme bis zur fertigen `review.html` |

Wichtig: Der Schnitt selbst berührt die Vertrauenskette. Ob er über den
bestehenden Renderpfad läuft oder zunächst als eigenständige ffmpeg-Liste
außerhalb, ist die erste zu treffende Entscheidung — **mit dem Nutzer klären,
nicht annehmen.**

---

## 6. Technische Fakten für den Nachfolger

**Vier Fehlerursachen, alle dieselbe Wurzel:** Annahmen, die im
Dreiminutenfenster tragen und über eine Stunde brechen.

1. `source_duration_ms` aus ffprobe ist gerundet, whispers letztes Segment
   liegt Millisekunden darüber → behoben (Maximum bilden)
2. Die monotone Uhr lief nur innerhalb eines Segments → behoben (durchlaufend)
3. Segmentgrenzen konnten vor das Ende des Vorgängers rutschen → behoben
4. „[Musik]" rutschte durch den Sondertoken-Filter, weil whisper es in drei
   BPE-Tokens zerlegt → behoben (nach dem Zusammenbau filtern)

**Wer Vollduchläufe anfasst, prüfe zuerst, ob eine fünfte dieser Art wartet.**

**whisper.cpp:**
- `-ml 60` ist Pflicht. Ohne max_len liefert whisper Segmente von zehn
  Sekunden und mehr; der validierte Probelauf hatte es nur zufällig über
  die Nebenwirkung von `-owts` gesetzt
- `-ojf` schreibt die Rohausgabe als `<wav>.json` **neben** die WAV, bevor
  unsere Konvertierung läuft. **Diese Datei überlebt jeden Absturz** — nach
  einem Fehlschlag nie neu transkribieren, sondern nachkonvertieren
- `--prompt` existiert jetzt als `--initial-prompt` / `--initial-prompt-file`.
  Ungenutzt, Vokabeldatei liegt bereit (§7). Prompt-Limit ca. 224 Token
- Wortzeitstempel lückenlos, Median-Pause 0 ms → Pausensegmentierung trägt
  nicht, nur Interpunktion

**Materialauswahl — teuer gelernt.** Ein erster Stapellauf über vier
Aufnahmen aus `F:\VIDEO ROHABLAGE` lieferte nach zwei Stunden Rechenzeit
ausschließlich „[Musik]": Gaming-Aufnahmen mit stummer Tonspur, erkennbar an
**2 274 bit/s Audiobitrate** gegenüber 195 000 bei brauchbarem Material.
Vor jedem Stapellauf Bitrate und `volumedetect` prüfen.

**Brauchbares Material** liegt unter `F:\OLD` — 1211 Dateien, davon 38 mit
Bitrate > 50 000 und max_volume > −30 dB, zusammen 28,3 h. Inner-Circle-
Meetings, dienstags und donnerstags gegen 20 Uhr. `AvatarWebcam-`-Dateien
sind Parallelaufnahmen desselben Termins und überflüssig.
Inventar: `C:\Users\schan\Desktop\old-inventar.md`

**Vorbestehende rote Gates** — nicht durch diese Arbeit verursacht, nicht
ohne eigenen Auftrag reparieren: `ruff format --check` meldet Altdateien,
projektweites `fail_under = 100` liegt real darunter, und `uv run pytest`
schlägt fehl. **Immer `uv run python -m pytest` verwenden.**

---

## 7. Dateien außerhalb des Repositorys

| Pfad | Inhalt |
|---|---|
| `C:\Users\schan\Desktop\repeat-old\<STEM>\work\audio.wav.json` | vier whisper-Rohausgaben, 3,57 h Sprache — **nie neu transkribieren** |
| `C:\Users\schan\Desktop\repeat-old\<STEM>\transcript.json` | konvertierte Transkripte |
| `C:\Users\schan\Desktop\repeat-recheck\<STEM>\` | dieselben, nach der Ordnungszahl-Korrektur |
| `C:\Users\schan\Desktop\repeat-review\review.html` | **Prototyp des Human Gate — Vorlage für den nächsten Auftrag** |
| `C:\Users\schan\Desktop\repeat-review\urteile.json` | **die 25 Urteile — die wertvollste Datei im Projekt** |
| `C:\Users\schan\Desktop\old-inventar.md` | Materialinventar mit Pegelmessung |
| `whisper-vokabular.txt` | Vokabeldatei für `--initial-prompt-file`, noch ungenutzt |

---

## 8. Arbeitsweise — bitte beibehalten

**Rollenverteilung.** Der Chat orchestriert und auditiert, Claude Code
implementiert. Der Chat schreibt keinen Produktionscode und committet nicht.

**Jeder Auftrag nennt:** erlaubten Änderungsbereich, verbotene Operationen,
Qualitätsgates mit exakten Befehlen, Berichtsanforderungen. Kein Commit im
selben Auftrag wie die Implementierung. Commit und Push sind eigene, minimale
Aufträge mit Scope-Prüfung als erstem Schritt.

**Modellwahl.** Haiku für Mechanik (Commit, Push). Sonnet für Sammeln,
Implementieren, Verifizieren, Stapelläufe. Opus erst, wenn die
Vertrauenskette berührt wird — also beim Schneiden.

**Berichte prüfen, nicht glauben.** Zahlen gegeneinander abgleichen. In
diesem Block enthielt ein Bericht eine falsch summierte Gesamtdauer und eine
unsortierte „Top 5"; ein anderer meldete 750 Wörter für zwei Stunden, was den
Materialfehler entlarvte. Die Spalte, in die niemand schaut, enthält oft die
Antwort.

**Lehren aus diesem Block:**

- **Frühwarnung in jeden Stapelauftrag.** Nach *jeder* Datei prüfen, ob das
  Ergebnis plausibel ist (Wortzahl, Exitcode), sonst stoppen. Das hat drei
  vergebliche Läufe verhindert.
- **Aufträge über zehn Minuten Laufzeit als Hintergrundlauf formulieren.**
  Claude Codes Vordergrundbefehle brechen nach zehn Minuten ab — und lassen
  dabei eine verwaiste `whisper-cli.exe` zurück, die weiterrechnet. Vor
  jedem Start auf Waisen prüfen.
- **Bei Fehlschlägen zuerst fragen, was überlebt hat.** Die teure
  Transkription liegt fast immer schon auf der Platte.
- **Messen statt vermuten.** Zwei Hypothesen zur Materialursache waren
  falsch; die richtige stand als Zahl im Bericht.

**Zum Nutzer.** Er liest die Claude-Code-Berichte nicht — das ist die Aufgabe
des Chats. Er braucht: einen fertigen Prompt zum Kopieren, die
Modellempfehlung, und in zwei Sätzen was dabei herauskommt. Er will zügig zum
fertigen Produkt und schätzt Direktheit über Ausführlichkeit. Er ist zu Recht
vorsichtig, dass am funktionierenden Schnittprogramm nichts kaputtgeht —
Scope-Disziplin ist keine Formalie, sondern das, was Vertrauen trägt.

---

## 9. Was in den neuen Chat gehört

Diese Datei, `urteile.json`, `review.html` und `whisper-vokabular.txt`.
Alles andere liegt auf der Platte des Nutzers und kann bei Bedarf angefordert
werden.
