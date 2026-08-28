# Orchestrator-Uebergabe — Shorts-Produktionslinie, 28. August 2026
Stand: HEAD `4eba561` auf `master`, gepusht. Arbeitsbaum: nur die bekannte
Datei `-`.

**Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-27-abend.md` NICHT ab —
es setzt sie fort.** Alles, was dort steht, gilt weiter, wo hier nichts anderes
steht; und jene setzt ihrerseits die Fassung vom 27.8. mittags fort, diese die
Abendfassung vom 25.8., diese die Nachmittagsfassung vom 25.8. Es sind fuenf
Dokumente in einer Kette, nicht fuenf Fassungen desselben. Das Nachschlagewerk
zu diesem Dokument ist `BESTAND-2026-08-28.md`; dort stehen Zeilennummern,
Signaturen und Schemata.

**Ein Unterschied zu allen Vorgaengerinnen:** Abschnitt 2 beschreibt nicht nur,
was sich geaendert hat, sondern den **vollstaendigen Ablauf**, wie ein Nutzer
die Linie heute faehrt — von der Aufnahme bis zum fertigen Short im Zielordner.
Der Grund: mit dem Wecker und dem Aufraeumer ist die Linie bis auf das
Hochladen fertig. Wer diesen Abschnitt liest, versteht die ganze Linie, ohne
vier Vorgaengerinnen aufzuschlagen.

Was hier steht, stammt aus den beiden Berichten
`artefakte\repeat\wecker\BERICHT-2026-08-27.md` und
`artefakte\repeat\aufraeumen\BERICHT-2026-08-28.md`, aus den in diesem Auftrag
gelesenen Quelldateien (`scripts\START-SHORTS-KETTE.ps1`,
`src\matrix_auto_cutter\shorts\aufraeumen.py`,
`src\matrix_auto_cutter\shorts\urteilslauf.py`), aus `docs\repeat\WECKER.md`
und aus den Angaben des Auftraggebers. Jede Zahl traegt ihre Herkunft. Was
nicht belegt ist, ist als **nicht belegt** gekennzeichnet. Wo zwei Quellen
einander widersprechen, stehen **beide** Fassungen da, ausdruecklich als
Widerspruch bezeichnet.

Frag beim ersten Kontakt nach `git log --oneline -6` und `git status --porcelain`.

---

## 1 — Rolle und Arbeitsweise

**Die Abschnitte 1.1 bis 1.4 der Nachmittagsfassung vom 25.8., 1.1 und 1.2 der
Abendfassung vom 25.8., 1.1 und 1.2 der Fassung vom 27.8. mittags und 1.1 und
1.2 der Abendfassung vom 27.8. gelten unveraendert** — Aufbau eines Auftrags,
Freigaben, Sperrliste, Modellwahl, „berichtigen statt befolgen", und die Regel,
dass `urteilslauf` ohne Fahnen kein Lesebefehl ist. Zwei Dinge sind
dazugekommen.

### 1.1 Der 28.8. — drei Faelle, in denen Vorgabe und Material auseinanderliefen

Die Abendfassung vom 27.8. haelt in 1.1 fest: die haeufigste Fehlerquelle ist
der Orchestrator, nicht die Ausfuehrung, und das ausfuehrende Fenster
berichtigt, fuehrt aus **und nennt beide Fassungen**. Der 28.8. hat drei
weitere Faelle geliefert. Sie sind kleiner als die drei vom 27.8., aber sie
folgen demselben Muster.

**Fall 1 — `WECKER.md` gibt einen Befehl an, der dort nicht laeuft, wo das
Dokument ihn auszufuehren heisst.** Abschnitt 2 von `WECKER.md` nennt einen
`schtasks`-Aufruf und weist an, ihn „in einer PowerShell **als
Administrator**" auszufuehren. Genau dort scheitert er: PowerShell zerlegt die
maskierten Anfuehrungszeichen in `/TR "… \"P:\…\" …"` anders als die
Eingabeaufforderung, und `schtasks` meldet

```
FEHLER: Ungueltige Syntax. Erforderliche Option sc fehlt.
```

Der Befehl ist nicht falsch — er ist fuer `cmd.exe` geschrieben und traegt eine
Anweisung, ihn in einer anderen Schale auszufuehren. **Das ist der einzige
Fall des 28.8., der in dieser Uebergabe eine Aenderung an einer bestehenden
Datei ausgeloest hat** (TEIL 4 des Auftrags, siehe Abschnitt 5.1).
Herkunft: Angaben des Auftraggebers unter GEGEBEN; `WECKER.md` Zeilen 65–70 in
diesem Auftrag nachgelesen.

**Fall 2 — der Aufraeumauftrag verlangte genau den Aufruf, den die
Schutzschwelle des Werkzeugs verhindern sollte.** `aufraeumen` traegt ein
Mindestalter von 48 Stunden (`MINDESTALTER_STUNDEN_VORGABE = 48`). Der Auftrag
verlangte den Lauf gegen `2026-08-25 15-14-00` — eine Aufnahme, deren juengste
`short.mp4` beim Lauf rund **24 Stunden** alt war. Der Aufruf lief trotzdem
durch, weil `--aufnahme` das Mindestalter umgeht; es regelt die **automatische
Auswahl**, nicht den benannten Aufruf. Das ausfuehrende Fenster hat das
ausgefuehrt und als Nachtrag gemeldet, statt es stillschweigend zu tun.
Herkunft: `aufraeumen\BERICHT-2026-08-28.md`, „Zwei Nachtraege"; im Code
nachgesehen (`aufraeumen.py:293-318`).

**Fall 3 — eine Zeilenzahl, die nicht stimmt.** `wecker\BERICHT-2026-08-27.md`
TEIL 2 nennt `scripts/START-SHORTS-KETTE.ps1` mit **130 Zeilen**. Die Datei hat
**134**; `git show --stat 48858f0` weist `134 +++` aus, und das Nachzaehlen im
Arbeitsbaum ergibt dasselbe. **Beide Fassungen stehen hier**; welche Zahl der
Bericht meinte, ist **nicht belegt** — moeglich ist ein Zaehlstand vor der
letzten Berichtigung. Folgen hat es keine, aber wer die Datei am Umfang
wiedererkennen will, nimmt die 134.

**Was der 28.8. NICHT geliefert hat:** einen Widerspruch, der eine Ausfuehrung
verhindert haette. `wecker\BERICHT-2026-08-27.md` schliesst mit
„Widersprueche zum Auftrag: Keiner"; `aufraeumen\BERICHT-2026-08-28.md` nennt
ausser den zwei Nachtraegen keinen. Eine **Praezisierung** ist erwaehnenswert:
der Weckerauftrag hielt es fuer moeglich, dass sich „nichts zu tun" nicht von
einem echten Fehlschlag unterscheiden laesst. Es laesst sich — Code 2 ist in
`kette.py` allein diesem Fall vorbehalten, und `kette.py` musste dafuer nicht
angefasst werden.

### 1.2 Ein loeschendes Werkzeug bekommt feste Namen, kein Muster

**Neu, und die tragende Entscheidung des Aufraeumers.** `ZWISCHENSTUFEN` ist
ein Tupel dreier Literale — `("ausschnitt.mp4", "leinwand.mp4",
"mit-avatar.mp4")` —, ausdruecklich **kein `glob("*.mp4")`, keine
Endungsregel, kein Muster**. Am 28.8. waere ein `glob("*.mp4")` gleichwertig
gewesen und haette dieselben 132 Dateien getroffen. Der Unterschied liegt in
der Zukunft: eine Musterregel loescht eines Tages etwas, das noch nicht
erfunden war — eine vierte Baustufe, eine `short-final.mp4`, eine
`short.mp4.partial`, ein Handmuster, das jemand danebenlegt.

Kommt eine Stufe hinzu, wird sie von Hand eingetragen — **oder sie bleibt
liegen. Das ist der harmlose der beiden Fehler.**

Dazu gehoert die zweite Haelfte derselben Regel: **in der Voreinstellung
loescht das Werkzeug nichts.** Ohne `--wirklich-loeschen` wird nur der Plan
gezeigt. Begruendung woertlich aus dem Modul-Docstring: „eine Fahne, die man
vergessen kann, darf nicht die Fahne sein, die vor dem Loeschen schuetzt."

Beides zusammen ist die Vorlage fuer jedes kuenftige Werkzeug, das etwas
entfernt — und die Uploadstufe wird eines sein, das etwas **veroeffentlicht**,
also ebenso wenig zurueckzunehmen.

---

## 2 — Der Ablauf, wie er heute ist

Die Linie ist **fertig bis auf das Hochladen**. Dieser Abschnitt beschreibt sie
vollstaendig, in der Reihenfolge, in der ein Nutzer sie faehrt. Alle Befehle
laufen aus der Repositoriumswurzel `P:\DimensionWithin-MatrixMarketAutoEditor`.

### 2.0 Ueberblick in einer Zeile

Aufnahme (morgens) → **Kette, sieben Stufen** (nachts von selbst) →
**Urteilslauf, sieben Schritte** (morgens, mit dem Menschen am Tor) → **Bau**
(Schritt 7 des Urteilslaufs) → **Aufraeumer** (spaeter, gibt rund 70 % des
Platzes frei) → **Hochladen** (noch nicht gebaut, Abschnitt 6).

### 2.1 Die Kette — sieben Stufen bis zu den Kandidaten

```powershell
python -m matrix_auto_cutter.shorts.kette [--aufnahme NAME] [--modell NAME]
       [--modell-buendelung NAME] [--lauf N] [--neu] [--neu-ab STUFE]
       [--bis STUFE] [--trocken] [--wurzel PFAD]
```

**Zweck:** aus einer gerenderten Aufnahme die Kandidatenausschnitte gewinnen und
sie zu Gruppen buendeln. Die Kette **urteilt nicht und baut nicht** — sie endet
bei den Kandidaten.

| # | Stufe | Ausgabe | Wer arbeitet | Dauer (18 min Material) |
|---|---|---|---|---|
| 1 | `auftrag` | `shorts-job.json` | eigenes Modul | 3,5 s |
| 2 | `avatar_cut` | `avatar-cut.mp4` | eigenes Modul | 532 s |
| 3 | `transcript` | `transkript-rendered.json` | eigenes Modul | 1310 s |
| 4 | `wortliste` | `wortliste.json` | eigenes Modul | 0,5 s |
| 5 | `zerlegung` | `kandidaten-lauf<N>.json` | **Modell** ueber `claude -p` | rund 26 min |
| 6 | `zusammenfuehrung` | `kandidaten.json` | Rechnung in `auswahl.py` | Sekunden |
| 7 | `buendelung` | `buendel.json` | **Modell** ueber `claude -p` | rund 19 min |

Zusammen **rund 85 Minuten**. Laengeres Material dehnt vor allem Stufe 3.
Herkunft der Zeiten: `WECKER.md` Abschnitt 1.

**Rueckgabecodes:** 0 Erfolg, 2 keine Aufnahme oder nur verfallene, 5 Stufe
gescheitert oder unbekannt, 9 Urteile wuerden umgedeutet, 10 `buendel.json`
weicht ab. Die 6 gibt es nicht.

**Ohne `--aufnahme` waehlt die Kette selbst**, in dieser Reihenfolge: erst der
Name aus einer vorgefundenen `kette.json` (eine schon laufende Kette wird
fortgesetzt), sonst die juengste Aufnahme aus dem Bestand — und die muss
**unverfallen** sein. Aelter als `VERFALL_STUNDEN = 48`, gemessen ab der
Aufnahmezeit im **Ordnernamen**, heisst: `KetteFehlschlag("nur_verfallen", …)`
mit Code 2. Eine ausdruecklich per `--aufnahme` genannte Aufnahme laeuft
weiter und wird nur gewarnt.

### 2.2 Der Urteilslauf — sieben Schritte, das menschliche Tor

```powershell
uv run python -m matrix_auto_cutter.shorts.urteilslauf [AUFTRAG]
       [--kein-server] [--keine-sicherung] [--keine-auswahl] [--kein-bau]
       [--nur-offene-zeigen] [--auch-verfallen] [--wurzel PFAD]
       [--platzhalter-server [SEKUNDEN]]
```

**Zweck:** einmal aufrufen, urteilen, Fenster schliessen — der Rest laeuft von
selbst. Die sieben Schritte:

| # | Schritt | Was geschieht |
|---|---|---|
| 1 | Aufnahme bestimmen | juengste `kandidaten.json`; verfallene werden **namentlich uebergangen** (`uebergangen: <NAME> (verfallen, N h alt)`); nur Verfallenes → `ANGEHALTEN [nur_verfallen]`, Code 2, **vor jedem Schreibvorgang** |
| 2 | vorhandene Urteile gegen die Kandidaten pruefen | Abweichung → Code 5 |
| 3 | Urteilsseite starten | Browser; **Strg+C beendet sie**, der Lauf geht danach weiter |
| 4 | Urteile zaehlen | Trefferquote, dazu die Nachschlagzeile |
| 5 | Bauliste erzeugen | ueber `auswahl`; `--keine-auswahl` uebergeht ihn |
| 6 | Urteile und Kandidaten sichern | nach `labels\repeat\`; Fehlschlag → Code 6 |
| 7 | Shorts bauen | **Teilbau**, siehe unten; unvollstaendig → Code 8 |

**Rueckgabecodes:** 0 Erfolg, 2 keine Aufnahme / nur verfallene / Auftrag
unlesbar, 5 Urteilsabweichung, 6 Sicherung fehlgeschlagen, 8 Bau
unvollstaendig. Die 7 (`ziel_belegt`) ist ersatzlos entfallen.

**WARNUNG, unveraendert aus der Abendfassung vom 27.8., Abschnitt 1.2:** ein
nackter `urteilslauf` ohne Pfad und ohne Fahnen ist **kein Lesebefehl**. Er
nimmt seinen ganzen Weg: baut, sichert und schreibt in die Trefferquote. Wer
nur wissen will, welche Aufnahme gewaehlt wuerde, ruft
`urteilslauf.finde_aufnahme` auf oder haengt `--nur-offene-zeigen` an.

**Die Urteilsseite zeigt eine Vorauswahl**, sobald die `buendel.json` bei
**allen** Gruppen einen `gruppen_rang` traegt: sortiert nach Staerke, die 15
vorausgewaehlten offen, die uebrigen hinter „N weitere Gruppen anzeigen".
Fehlt das Feld auch nur bei einer Gruppe, bleibt alles wie vorher.

### 2.3 Der Teilbau — Schritt 7 im Einzelnen

**Zweck:** nur bauen, was noch keine `short.mp4` hat. Ein belegter Zielordner
haelt den Lauf **nicht** mehr an.

```powershell
# nur sehen, was offen ist - baut nichts, legt keine Teilbauliste an, Code 0
uv run python -m matrix_auto_cutter.shorts.urteilslauf --nur-offene-zeigen
```

Der Ablauf: `bauliste.json` lesen → offene Indizes bestimmen (`ist_gebaut`
fragt nach `<Ziel>\kandidat-NN\short.mp4`, **nicht** nach dem Ordner) →
`bauliste-offen.json` in den Aufnahmeordner schreiben → bauen → **den ZUWACHS**
gegen die Zahl der Teilbaulisteneintraege pruefen. Schlusszeile:

```
Fertig: <Ziel> - 19 von 19 offenen Shorts neu gebaut in 855.0 s, 44 insgesamt im Zielordner
```

Zwei Fallen, beide teuer bezahlt und beide in Abschnitt 5 der Abendfassung vom
27.8. begruendet: die Teilbauliste **kuerzt `enthaelt`** auf die Indizes, die in
ihr stehen (sonst bricht der Bau an einem Verweis auf einen bereits gebauten
Kandidaten ab), und geprueft wird der **Zuwachs**, nicht der Bestand (im
Zielordner koennen `short.mp4` liegen, die die heutige Bauliste nicht nennt).

### 2.4 Der Nachschlag — ein zweiter Zerlegungslauf

**Zweck:** mehr Kandidaten aus derselben Aufnahme, mit einem anderen Modell.
Die vollstaendige Zeile schreibt `urteilslauf` in Schritt 4 selbst hin;
`nachschlagbefehl` erzeugt sie:

```powershell
python -m matrix_auto_cutter.shorts.kette --aufnahme "2026-08-25 15-14-00" `
       --neu-ab zerlegung --lauf 2 --modell opus
```

**`--lauf 2` ist der Kern der Zeile.** Ohne sie schriebe der Nachschlag wieder
`kandidaten-lauf1.json` und ueberschriebe den ersten Lauf. Mit ihr entsteht
`kandidaten-lauf2.json` daneben, und Stufe 6 vereinigt beide.

Der Aufnahmename kommt aus dem Wurzelfeld `video_name` der Kandidatendatei,
nicht aus dem Ordnernamen — die beiden duerfen auseinanderfallen.

**Ob er sich lohnt, ist offen:** der Nachschlag vom 26.8. brachte mehr
Kandidaten bei schlechterer Quote (Abschnitt 7.4 der Abendfassung vom 27.8.).

### 2.5 Der Aufraeumer — Platz zurueckholen

```powershell
# Plan zeigen, nichts loeschen (Voreinstellung)
uv run python -m matrix_auto_cutter.shorts.aufraeumen

# eine benannte Aufnahme wirklich raeumen
uv run python -m matrix_auto_cutter.shorts.aufraeumen `
       --aufnahme "2026-08-25 15-14-00" --wirklich-loeschen
```

**Zweck:** die drei Zwischenstufen aus den Bauordnern entfernen. Sie machen
rund 70 % des Zielordners aus. Vollstaendig in Abschnitt 4.

### 2.6 Der Wecker — die Kette laeuft nachts von selbst

Windows-Aufgabenplanung, taeglich 03:00 Uhr, ruft
`scripts\START-SHORTS-KETTE.ps1`. Der Lauf faehrt **nur die Kette** — er
urteilt nicht, buendelt nicht zur Auswahl, baut nicht und laedt nichts hoch.
Vollstaendig in Abschnitt 3.

### 2.7 Wo was liegt

| Was | Wo |
|---|---|
| Aufnahmeordner (Auftrag, Transkript, Kandidaten, Buendel) | `artefakte\repeat\shorts\<AUFNAHME>\` |
| Zielordner der gebauten Shorts | `F:\MatrixMarketAutoEdit\Shorts Rendered\<AUFNAHME>\kandidat-NN\` |
| Sicherungen der Urteile und Kandidaten | `labels\repeat\` |
| Trefferquote | `labels\repeat\trefferquote.json` (**relativer** Pfad!) |
| Protokolle des Weckers | `artefakte\repeat\kette-protokoll\<datum>-<uhrzeit>.log` |
| Protokolle des Aufraeumers | `artefakte\repeat\aufraeumen\<datum>-<uhrzeit>.json` |

`artefakte\` steht in `.gitignore` — was dort liegt, liegt nur lokal.

---

## 3 — Der Wecker

**Neu seit `48858f0`.** Die Anleitung steht vollstaendig in
`docs\repeat\WECKER.md`. Dieser Abschnitt haelt fest, was beim Einrichten am
28.8. dazugelernt wurde und in der Anleitung fehlte.

### 3.1 Die Aufgabe ist eingerichtet und erprobt

| Groesse | Wert |
|---|---|
| Name | `Shorts-Kette naechtlich` |
| Zeitplan | taeglich, 03:00 Uhr |
| Status | **Bereit** |
| Rechte | `/RL HIGHEST` |
| Anmeldeart | **Nur interaktiv** (siehe 3.3) |

Herkunft: Angaben des Auftraggebers unter GEGEBEN. **In diesem Auftrag nicht
nachgeprueft** — das Anlegen, Aendern und Loeschen geplanter Aufgaben war
verboten.

### 3.2 Die PowerShell-Falle beim `schtasks`-Aufruf

**Der Befehl aus `WECKER.md` Abschnitt 2 scheitert in PowerShell** mit

```
FEHLER: Ungueltige Syntax. Erforderliche Option sc fehlt.
```

Ursache: PowerShell zerlegt die maskierten Anfuehrungszeichen (`\"`) im Wert
von `/TR` anders als die Eingabeaufforderung. Der Aufruf ist fuer `cmd.exe`
geschrieben.

**Zwei Wege, die funktionieren.** Erstens: die Argumente als **Liste**
uebergeben, dann setzt PowerShell sie selbst richtig zusammen.

```powershell
$aufruf = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1"'
schtasks.exe @('/Create','/TN','Shorts-Kette naechtlich','/TR',$aufruf,'/SC','DAILY','/ST','03:00','/RL','HIGHEST','/F')
```

Zweitens: denselben Befehl ueber `cmd /c` schicken, dann liest ihn die Schale,
fuer die er geschrieben ist.

Beides ist in `WECKER.md` Abschnitt 2 nachgetragen (Abschnitt 5.1).

### 3.3 „Unabhaengig von der Benutzeranmeldung" scheitert am Passwort

Die Aufgabenplanung verlangt fuer diese Einstellung das **Windows-Passwort**.
Bei Anmeldung ueber ein Microsoft-Konto ist das **nicht die PIN**; der Versuch
schlug fehl.

**Entschieden: es bleibt bei „Nur interaktiv".** Das genuegt fuer den Zweck —
der Rechner darf **gesperrt** sein, nur nicht **abgemeldet**. Wer nachts den
Rechner herunterfaehrt oder sich abmeldet, bekommt keinen Lauf; die naechste
Nacht faengt ihn auf, solange die Aufnahme unter 48 Stunden bleibt.

### 3.4 Die Energieverwaltungsschranken sind entfernt

Per PowerShell gesetzt:

| Einstellung | Wert |
|---|---|
| `DisallowStartIfOnBatteries` | `false` |
| `StopIfGoingOnBatteries` | `false` |
| `StartWhenAvailable` | `true` |
| `WakeToRun` | `true` |

`StartWhenAvailable` holt einen versaeumten Lauf nach; `WakeToRun` weckt den
Rechner. Die zwei Batteriehaken sind die Vorgabe der Aufgabenplanung und haetten
den Lauf auf einem Geraet im Akkubetrieb stumm unterbunden.

Herkunft: Angaben des Auftraggebers unter GEGEBEN. **In diesem Auftrag nicht
nachgeprueft.**

### 3.5 Die Feuerprobe am 28.8., 16:17

Von Hand ausgeloest. Ergebnis:

- Protokolldatei angelegt;
- erste Zeile `Startskript in P:\DimensionWithin-MatrixMarketAutoEditor` —
  **der Weg ins Repo greift aus `C:\Windows\system32` heraus**, und genau
  dafuer steht der feste Pfad im Skript;
- alle sieben Stufen uebersprungen (die Aufnahme war bereits durchgelaufen);
- `gemeldet 0`.

Damit ist die Kette der Zustaendigkeiten einmal durchgemessen:
Aufgabenplanung → PowerShell → Skript → Verzeichniswechsel → `uv` → `kette`
→ Protokoll → Rueckgabecode. **Was noch fehlt, ist ein Lauf an einer frischen
Aufnahme** (Abschnitt 7.2).

### 3.6 Was das Skript sonst noch tut

- **Protokoll:** eine Datei je Lauf,
  `artefakte\repeat\kette-protokoll\<jahr>-<monat>-<tag>-<stunde><minute><sekunde>.log`.
  Jede Zeile mit Zeitstempel, die letzte eine `ZUSAMMENFASSUNG` mit Start,
  Ende, Dauer, Rueckgabecode, gemeldetem Code und gewaehlter Aufnahme.
- **Code 2 wird auf 0 gesenkt.** „Keine unverfallene Aufnahme" ist an einem Tag
  ohne Aufnahme der Normalfall und kein Grund fuer die Aufgabenplanung, einen
  Fehlschlag zu melden. **Jeder andere von null verschiedene Code wird
  unveraendert weitergereicht.**
- **Die gewaehlte Aufnahme wird aus der Kettenausgabe mitgelesen**, nicht ein
  zweites Mal eigenstaendig bestimmt: zwei Bestimmungswege koennten
  auseinanderlaufen.
- **Handschalter** `-Aufnahme`, `-Trocken`, `-Modell`. Die Aufgabenplanung
  benutzt keinen davon.

---

## 4 — Der Aufraeumer

**Neu seit `4eba561`.** `src\matrix_auto_cutter\shorts\aufraeumen.py`, 407
Zeilen; `tests\test_shorts_aufraeumen.py`, 287 Zeilen, 19 Tests.

### 4.1 Was der Lauf vom 28.8. bewirkt hat

Gegen die Aufnahme `2026-08-25 15-14-00`, mit `--wirklich-loeschen`,
Rueckgabecode 0:

| | vorher | nachher |
|---|---:|---:|
| Ordnergroesse | **426.402.130 Byte** | **125.300.086 Byte** |
| Dateien | **353** | **221** |
| `short.mp4` | 44 | **44** |
| `*.json` | 177 | **177** |
| Kandidatenordner | 44 | **44** |

**132 Dateien geloescht, 301.102.044 Byte frei — exakt die Differenz.** Keine
`short.mp4` fehlt, keine ist leer, keine `*.json` wurde angefasst, kein Ordner
verschwand. Verblieben sind genau sechs Dateinamen je Ordner:
`ausschnitt.json`, `leinwand.json`, `mit-avatar.json`, `short.json`,
`short.mp4`, `shorts-bau-bericht.json`.

Protokoll: `artefakte\repeat\aufraeumen\2026-08-28-165133.json`, 19.826 Byte,
atomar geschrieben. Es traegt jede der 132 Dateien mit vollem Pfad und Groesse.

Herkunft: Angaben des Auftraggebers unter GEGEBEN, deckungsgleich mit
`aufraeumen\BERICHT-2026-08-28.md`. **Die Zahlen sind in diesem Auftrag nicht
nachgemessen** — `F:` war gesperrt.

Zur Einordnung: der Zielordner war am 27.8. durch den Teilbau von 263 MB auf
426 MB gewachsen. Der Aufraeumer bringt ihn unter den Stand **vor** dem
Teilbau. **Die Gesamtgroesse von `F:` bleibt nicht belegt.**

### 4.2 Was er NICHT loescht — und warum

Der wichtigste Abschnitt zu diesem Werkzeug. Ein Werkzeug, das loescht, wird an
dem gemessen, was es stehen laesst.

| Was | Warum |
|---|---|
| **`short.mp4` — nie, in keinem Modus** | Sie ist das Ergebnis der ganzen Linie und das einzige, woran ein spaeterer Teilbau erkennt, dass dieser Kandidat fertig ist. Sie steht nicht in `ZWISCHENSTUFEN`, und ihr Vorhandensein ist die **Voraussetzung** dafuer, dass im Ordner ueberhaupt etwas geloescht wird. |
| **Jede `*.json`** | Sie tragen die Bauentscheidungen: Versatz, Zuschnitt, Achsenabweichung. Zusammen 103.536 Byte — 0,02 % des Ordners. Der Platzgewinn waere nicht messbar, der Verlust an Nachvollziehbarkeit vollstaendig. |
| **Jede `*.partial.mp4` und jede fremde Datei** | Eine `.partial` ist im Zweifel ein fertiges Video unter einem Zwischennamen. Ein `notizen.txt` gehoert jemandem. |
| **Der Kandidatenordner selbst — auch leer** | `vorhandene_kandidatenordner` liest Ordner**namen**. Ein verschwundener Ordner aendert, was ein spaeterer Lauf fuer gebaut haelt. Es gibt kein `rmdir` und kein `rmtree` im Modul. |
| **Alles ausserhalb der Renderwurzel** | `--aufnahme` nimmt einen NAMEN, keinen Pfad. `..`, Pfadtrenner und absolute Pfade werden abgewiesen (`_ist_schlichter_name`). |
| **Alles, was kein exakter Name aus `ZWISCHENSTUFEN` ist** | Siehe Abschnitt 1.2. |
| **Ohne `--wirklich-loeschen`: gar nichts** | Die Voreinstellung ist das Anzeigen. |

### 4.3 Die Vorfrage, die das Loeschen ueberhaupt erlaubt

**Liest jemand die Zwischenstufen?** Gesucht, nicht angenommen. Die drei Namen
kommen in `src/` und `tests/` an genau einer Stelle vor: `build.py`, in
`_build_one_candidate`, jeweils **wenige Zeilen, nachdem dieselbe Funktion die
Datei geschrieben hat**. Es gibt kein „ueberspringen, wenn schon da". Kein
`glob("*.mp4")` fasst sie an. Der Teilbau entscheidet allein an `short.mp4` und
an den Ordnernamen.

**Ein Loeschen kann keinen spaeteren Lauf brechen.** Das ist die Grundlage.

### 4.4 Rueckgabecodes

| Code | Name | Bedeutung |
|---:|---|---|
| 0 | — | Erfolg, auch wenn nichts zu tun war |
| 2 | `wurzel_fehlt` | Renderwurzel nicht erreichbar |
| 3 | `keine_aufnahme` | keine Aufnahme erfuellt das Mindestalter / benannte nicht gefunden |
| 4 | `ordner_ohne_short` | gemeldet, **nicht angefasst**; die uebrigen werden trotzdem aufgeraeumt |

Meldeformat `ANGEHALTEN [<code_name>]: <text>`.

### 4.5 Das Protokoll wird auch bei einem Abbruch geschrieben

Die Loeschschleife steht in einem `try`, das Schreiben in dessen `finally`.
Begruendung im Kommentar: geloeschte Dateien **ohne Nachweis** waeren der
schlimmere Ausgang.

---

## 5 — Betriebsfallen

**Alle Fallen der vier Vorgaengerinnen gelten weiter** — insbesondere 5.1 bis
5.4 der Abendfassung vom 27.8. (Code 7 entfallen; eine gefilterte Bauliste
macht `enthaelt`-Verweise ungueltig; Zielordner und Bauliste sind zwei
verschiedene Dinge; `modell` fehlt in aelteren Kandidatendateien). Drei sind
dazugekommen.

### 5.1 Der `schtasks`-Befehl in `WECKER.md` geht in PowerShell nicht durch

Vollstaendig in Abschnitt 3.2. **In `WECKER.md` Abschnitt 2 berichtigt** — der
bisherige Befehl bleibt stehen, ausdruecklich gekennzeichnet als Fassung fuer
die **Eingabeaufforderung**, und daneben steht die PowerShell-taugliche
Fassung samt Ursache. Das ist die einzige Aenderung, die dieser Auftrag an
einem bestehenden Dokument vorgenommen hat.

**Die Lehre darueber hinaus:** ein Befehl in einer Anleitung traegt eine
stillschweigende Angabe darueber, **welche Schale** ihn liest. Wer sie nicht
hinschreibt, hat sie trotzdem gemacht.

### 5.2 „Nur interaktiv" heisst: gesperrt ja, abgemeldet nein

Die Aufgabe laeuft **nur, wenn der Benutzer angemeldet ist**. Der Bildschirm
darf gesperrt sein — das reicht. Abgemeldet, Benutzerwechsel oder
heruntergefahren heisst: kein Lauf, und dann liegt unter
`artefakte\repeat\kette-protokoll\` keine Datei fuer die Nacht. **Gibt es gar
keine Protokolldatei, ist das Skript nie angelaufen** — dann liegt es an der
Aufgabe, nicht an der Kette; nachzusehen in `taskschd.msc` unter *Verlauf*.

Verwandt und weiterhin gueltig: liegt das Repo auf einem Netzlaufwerk, endet
das Skript mit `ANGEHALTEN: Repo-Wurzel ... nicht erreichbar`, wenn `P:` zum
Zeitpunkt des Laufs nicht eingebunden war.

### 5.3 Das Mindestalter des Aufraeumers greift nicht bei `--aufnahme`

`MINDESTALTER_STUNDEN_VORGABE = 48` regelt die **automatische Auswahl** — also
den Aufruf **ohne** `--aufnahme`. Wer eine Aufnahme namentlich nennt, umgeht
die Schwelle vollstaendig; der Lauf vom 28.8. lief gegen eine Aufnahme, deren
juengste `short.mp4` rund 24 Stunden alt war.

Das ist kein Fehler, sondern dieselbe Regel wie beim Verfall in `kette` und
`urteilslauf`: **eine ausdrueckliche Nennung sticht die automatische
Schutzschwelle.** Wer die Schwelle wirken lassen will, laesst `--aufnahme` weg
— oder setzt `--mindestalter-stunden` ausdruecklich.

---

## 6 — Der Weg weiter

Die Vorhaben (1) Wecker und (3) Aufraeumer aus Abschnitt 6 der Abendfassung
vom 27.8. sind **erledigt**. Drei bleiben, in dieser Reihenfolge — und die
erste ist **kein Bauauftrag**.

### (1) Die Linie einige Videos im Betrieb fahren

**Das ist der naechste Schritt.** Entscheidung des Auftraggebers vom 28.8.: die
Uploadstufe wird **nicht jetzt** gebaut. Erst soll die Linie einige Videos im
Betrieb laufen.

**Der Grund ist die fehlende Zahl.** Die Vorauswahl der 15 besten Gruppen ist
unbelegt (Abschnitt 7.1), und der einzige saubere Test dafuer ist: **welche
Shorts der Nutzer tatsaechlich VEROEFFENTLICHT** — nicht, welche er zum Bau
freigibt. Am 27.8. wurden 44 gebaut; veroeffentlicht werden sollen 4 bis 10.
Der Abstand zwischen 44 und 4 bis 10 ist die eigentliche offene Strecke, und
niemand hat ihn bisher gemessen.

Was der Betrieb nebenbei liefert: die erste Nacht mit einer **frischen**
Aufnahme (Abschnitt 7.2), weitere Trefferquote-Eintraege mit richtiger
Modellkennung, und Erfahrungswerte zur Laufzeit an echtem statt an bereits
gerechnetem Material.

### (2) Die Uploadstufe — der Audit-Test zuerst

**Der Audit-Test steht vor dem Bau.** Er ist in Abschnitt 7.4 beschrieben und
kostet einen Uploadaufruf von hundert.

Beim Bau zu beachten, alles bereits teuer gelernt:

1. **Es zaehlt die BAULISTE, nicht der Ordnerinhalt.** Im Zielordner koennen
   `short.mp4` liegen, die der Nutzer inzwischen abgelehnt hat — bei
   `2026-08-25 15-14-00` sind das `kandidat-02` und `kandidat-15`. Wer ueber
   den Zielordner listet und alles hochlaedt, was er findet, veroeffentlicht
   zwei Shorts, die der Nutzer nicht ausgewaehlt hat.
2. **Shorts brauchen 9:16 und `#Shorts` im Titel.**
3. **Die 4 bis 10 Veroeffentlichungen werden ueber 24 Stunden verteilt
   geplant**, gerechnet ab der Aufnahme.
4. Die Regel aus Abschnitt 1.2 gilt sinngemaess: ein Werkzeug, das
   veroeffentlicht, ist so wenig zurueckzunehmen wie eines, das loescht. Feste
   Namen statt Muster, und in der Voreinstellung geschieht nichts.
5. **Es gibt bis heute keine Stelle, an der festgehalten wird, was
   veroeffentlicht wurde.** Das ist die stille Voraussetzung des Tests aus (1)
   und gehoert in diese Stufe hinein.

### (3) Eine Oberflaeche fuer die Shorts-Linie

Unveraendert: **bisher nicht erkundet.** Braucht einen eigenen, rein lesenden
Erkundungsauftrag ueber das bestehende Interface des Cutters, bevor irgendetwas
entworfen wird. Was es kann und was sich uebernehmen laesst, ist **nicht
belegt**.

---

## 7 — Offene Punkte

**Alle offenen Punkte der vier Vorgaengerinnen bleiben offen**, soweit hier
nichts anderes steht. Erledigt sind daraus: der Wecker (6(1) der Abendfassung
vom 27.8.) und der Aufraeumer (6(3) dort). Die folgenden sind die, die heute
zaehlen.

### 7.1 Die Vorauswahl bleibt UNBELEGT

Unveraendert gegenueber Abschnitt 7.1 der Abendfassung vom 27.8. Gegen die
Urteile vom 26.8. gemessen traf die Vorauswahl **nicht besser** als der Rest
(66,7 % gegen 71,0 %). Beide Gruende, warum das die Rangfolge trotzdem nicht
widerlegt, gelten weiter: das Mass ist zu stumpf (bei 64 % Annahmequote
enthaelt fast jede Gruppe ein `ja`), und es misst die falsche Frage
(„bauen?" statt „welche 15 zuerst ansehen?").

**Der saubere Test ist der Betrieb aus Abschnitt 6(1).** Bis dahin: die
Vorauswahl ordnet die Arbeit, aber es ist nicht gezeigt, dass sie sie richtig
ordnet. Sie sortiert nichts aus, der Rest bleibt einen Klick entfernt — der
Schaden ist im Zweifel gering, der Nutzen vorerst aber auch.

### 7.2 Der Wecker ist noch nie an einer frischen Aufnahme gelaufen

Beide Nachweise — der Trockenlauf vom 28.8. um 14:58 und die Feuerprobe um
16:17 — liefen gegen `2026-08-25 15-14-00`, eine Aufnahme, deren sieben Stufen
alle bereits vorlagen. **Beide Male wurden sieben von sieben Stufen
uebersprungen.**

Was damit **nicht** geprueft ist: ein Lauf, der wirklich rechnet — 85 Minuten
Avatarschnitt, Transkription und zwei Modellaufrufe, nachts, ohne dass jemand
zusieht, mit `--permission-mode acceptEdits` fuer `claude -p`. Insbesondere
offen:

- ob die Modellaufrufe in einer nicht-interaktiven Sitzung ohne Rueckfrage
  durchlaufen (`WECKER.md` Abschnitt 5 beschreibt genau das Haengen, das sonst
  eintritt);
- ob ein Lauf von 85 bis 180 Minuten die Aufgabenplanung ueberdauert;
- ob der Rechner in der Praxis angemeldet bleibt (Abschnitt 5.2).

**Die erste Nacht mit einer frischen Aufnahme ist der eigentliche Nachweis.**
Wer sie faehrt, sieht zuerst in `artefakte\repeat\kette-protokoll\` nach und
liest die `ZUSAMMENFASSUNG`.

### 7.3 Der dritte Trefferquote-Eintrag traegt weiterhin `unbekannt`

Unveraendert. Er muesste `sonnet+opus` heissen und wurde **bewusst nicht
berichtigt**: `_hat_bestehenden_eintrag` schreibt einen vorhandenen Eintrag nie
um — „die Trefferquote ist eine Reihe von Messungen, und eine nachtraeglich
ergaenzte Messung waere keine mehr."

**Die Folge muss man kennen:** jede Auswertung, die ueber `modell` gruppiert,
sieht `sonnet`, `sonnet`, `unbekannt` und kann den ergiebigsten Lauf der Linie
keinem Modell zuordnen. Kuenftige Eintraege bekommen die richtige Kennung.

### 7.4 Das Google-Audit — der Stand vom 28.8.

**Kontingent und Audit sind zwei verschiedene Dinge. Das Kontingent steht.**

| Frage | Stand am 28.8. |
|---|---|
| `Video Uploads per day` | **100**, Nutzung 0 — bestaetigt |
| OAuth-Zustimmungsbildschirm | **„In Produktion"** |
| Die drei Scope-Listen unter „Datenzugriff" | **LEER** |
| Compliance-Audit | **OFFEN** |

**Die leeren Scope-Listen sind kein Hindernis, und das ist neu.** Der Nutzer
aendert ueber dasselbe Projekt seit Monaten Thumbnails. `thumbnails.set`
verlangt denselben Scope wie der Upload (`youtube.upload`) — der Scope
funktioniert also, trotz leerer Liste. **Die Liste ist die Deklaration fuer
fremde Nutzer, nicht die technische Freigabe fuer den Inhaber.**

**Offen bleibt allein das Compliance-Audit.** Der Test, der die Frage
beantwortet, ohne etwas zu veroeffentlichen: einen fertigen Short per API mit
`privacyStatus: private` hochladen und in YouTube Studio nachsehen, ob dort ein
Hinweis auf unbestaetigte API-Nutzung steht. **Noch nicht durchgefuehrt.**

Bei 4 bis 10 Shorts je Aufnahme ist das Kontingent kein Engpass — es traegt
zehn Aufnahmen am Tag. Der Engpass ist die Freigabe, nicht die Zuteilung.

Herkunft aller Angaben dieses Abschnitts: die Angaben des Auftraggebers unter
GEGEBEN. **Im Repository nicht belegt, in diesem Auftrag nicht nachgeprueft** —
kein Netzzugriff, kein Zugriff auf das Google-Projekt.

### 7.5 Weiterhin offen aus den Vorgaengerinnen

Ohne Aenderung: `angenommen` traegt in Teillisten die Zahl der Gesamtauswahl;
die Zusammenfuehrung mehrerer Laeufe in der Trefferquote ist ueber `laeufe`
unterscheidbar, aber nicht auswertbar; die Schlusszeile von `kette` meldet
Vollendung auch bei `--bis N`; `kriterien_fassung` in drei Schreibweisen; die
Faustregel der Kriteriendatei ueber die Interpunktion; die nicht
byte-idempotente Zusammenfuehrung; der Unterschied zwischen Stufe 6 und
`auswahl --zusammenfuehren`; das verschwundene `aus_laeufen`; der falsche
`enthaelt`-Verweis in der bestehenden `kandidaten.json`; die zwei verwaisten
Shorts `kandidat-02` und `kandidat-15`; sowie die vollstaendige Liste in
Abschnitt 7.9 der Fassung vom 27.8. mittags.

**Gestrichen aus dieser Liste:** „die Zwischenstufen im Bauordner bleiben
liegen" — dafuer gibt es jetzt den Aufraeumer.

Ausserdem gilt weiter, was die Auftraege unter „bekannt und harmlos" fuehren:
20 vorbestehende mypy-Fehler in drei Dateien, drei Pytest-Warnungen, sieben
Tests mit `@pytest.mark.echter_unterprozess`, die Datei `-`, und
`test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state`
als flatterhaft. **Nachgesehen wurde er weiterhin nie**; ob er einen echten
Fehler verdeckt, ist **nicht belegt**. Dateien unter `labels\repeat\` liegen im
Repository mit LF, im Arbeitsbaum mit CRLF — wer einen Hash notiert, schreibt
dazu, von welcher Seite er ihn genommen hat.

---

## 8 — Das naechste Vorhaben: eine Oberflaeche

Der Auftraggeber hat am 28.8. entschieden, als Naechstes eine
Oberflaeche zu bauen, nicht die Uploadstufe. Begruendung in seinen
Worten: ohne eine Ansicht, in der er die Kette ueberwachen und
Fehler einsehen kann, hat er keinen Ueberblick ueber den Prozess --
und ohne den kann er die Linie auch nicht sinnvoll im Betrieb
einfahren.

Was sie zeigen soll:

- Welche Aufnahme gerade verarbeitet wurde, und von welchem TAG sie
  stammt. Dazu das heutige Datum, damit sich beides zuordnen laesst.
- Welcher Schritt gerade laeuft. Ausdruecklich als Hilfe beim Testen
  genannt.
- Ein Fenster fuer Fehler: was schiefgelaufen ist, sichtbar an einer
  Stelle, statt in den Protokollen gesucht werden zu muessen. Von
  dort aus soll der Weg in die Protokolle kurz sein.

Was sie koennen soll:

- Die Kette von Hand anstossen. Die Kette laeuft nachts von selbst;
  fuer einen Lauf ausser der Reihe braucht es einen Weg ueber die
  Oberflaeche.
- Ein Symbol auf dem Bildschirm, ueber das sich die Oberflaeche
  starten laesst.

Was der naechste Orchestrator zuerst tun sollte: einen rein lesenden
Erkundungsauftrag. Der bisherige Orchestrator hat das bestehende
Interface des Cutters NIE GESEHEN und kennt es nur aus Dokumenten.
Zu klaeren ist, wie es gebaut ist, was sich uebernehmen laesst, und
wo eine Shorts-Ansicht andocken kann, ohne `product_runner.py` und
`review_app.py` zu beruehren -- beide stehen auf der Sperrliste.
Erst danach ein Bauauftrag.

Die Datenlage ist guenstig: `kette.json` traegt je Aufnahme den
Stand jeder Stufe mit Zeitstempel und Dauer, die Protokolle unter
`artefakte\repeat\kette-protokoll\` je Lauf eine Zeile mit
Zeitstempel und am Ende eine Zusammenfassung, `buendel.json` die
Gruppen, `trefferquote.json` die Reihe der Messungen. Eine
Oberflaeche muesste nichts davon neu erzeugen, sondern nur lesen.
