# Wecker: der naechtliche Kettenlauf

Bis heute laeuft die Shorts-Kette nur, wenn du sie startest. Dieses
Schriftstueck richtet einen Wecker ein, damit sie nachts von selbst
laeuft und die Kandidaten morgens bereitliegen.

Der Wecker selbst ist die **Windows-Aufgabenplanung**. Claude Code hat
in Fassung 2.1.220 keinen eigenen Zeitplaner -- geprueft an
`claude --help` und den Hilfen aller Unterbefehle (`agents`, `auth`,
`auto-mode`, `doctor`, `gateway`, `install`, `mcp`, `plugin`,
`project`, `setup-token`, `ultrareview`, `update`). Falls das eine
spaetere Fassung nachruestet, waere dieses Schriftstueck neu zu pruefen.

Aufgerufen wird `scripts\START-SHORTS-KETTE.ps1`.

---

## 1. Die Uhrzeit: 03:00 Uhr

**Empfehlung: taeglich um 03:00 Uhr.** Nicht geraten, sondern aus zwei
Dingen hergeleitet.

**Die Laufzeit.** Gemessen fuer knapp 18 Minuten Material:

| Stufe | Dauer |
| --- | --- |
| 1 Auftragsdatei | 3,5 s |
| 2 Avatarschnitt | 532 s (rund 9 min) |
| 3 Transkription | 1310 s (rund 22 min) |
| 4 Wortliste | 0,5 s |
| 5 Zerlegung (Modell) | rund 26 min |
| 6 Zusammenfuehrung | Sekunden |
| 7 Buendelung (Modell) | rund 19 min |
| **zusammen** | **rund 85 Minuten** |

Laengeres Material dehnt vor allem Stufe 3; die Transkription rechnet
mit einem Faktor auf die Aufnahmedauer. Rechne fuer eine ungewoehnlich
lange Aufnahme mit dem Doppelten, also rund drei Stunden.

**Dein Rhythmus.** Du nimmst morgens auf, danach folgen Schnitt und
Hauptvideo. Daraus ergeben sich zwei Zwaenge:

- Der Lauf muss fertig sein, **bevor du morgens anfaengst**. Bei 03:00
  Uhr ist er gegen 04:30 Uhr durch, im schlechten Fall gegen 06:00 Uhr.
  Das haelt auch dann, wenn eine Aufnahme aus dem Rahmen faellt.
- Der Lauf darf **nicht laufen, waehrend du am Rechner sitzt**. Stufe 2
  und 3 fressen CPU und GPU; parallel geschnitten wird das zaeh fuer
  beide Seiten. 03:00 Uhr liegt sicher ausserhalb.

Ein frueherer Zeitpunkt, etwa 01:00 Uhr, verschafft mehr Puffer, faellt
aber eher in eine Nacht, in der du noch wach bist. Ein spaeterer, etwa
05:00 Uhr, ist knapp: eine dreistuendige Aufnahme waere erst um 08:00
Uhr durch.

Ein zweiter Grund fuer die Nacht: die Aufnahme des Vortags ist dann
etwa 12 bis 18 Stunden alt und damit klar innerhalb der 48 Stunden, ab
denen `kette.py` eine Aufnahme als verfallen ansieht
(`VERFALL_STUNDEN = 48`, gemessen ab Aufnahmezeit). Selbst wenn ein
Lauf einmal ausfaellt, faengt ihn die naechste Nacht noch auf.

---

## 2. Einrichten

Diesen Befehl fuehrst **du** aus, als **Administrator** (wegen
`/RL HIGHEST`). Es gibt ihn in zwei Fassungen -- **welche du brauchst,
haengt an der Schale**, in der du ihn eintippst.

**Fassung fuer die Eingabeaufforderung (`cmd.exe`):**

```bash
schtasks /Create /TN "Shorts-Kette naechtlich" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1\"" /SC DAILY /ST 03:00 /RL HIGHEST /F
```

**In einer PowerShell scheitert genau dieser Befehl** mit

```
FEHLER: Ungueltige Syntax. Erforderliche Option sc fehlt.
```

Die Ursache: PowerShell zerlegt die maskierten Anfuehrungszeichen
(`\"`) im Wert von `/TR` anders als die Eingabeaufforderung, und
`schtasks` bekommt am Ende nicht die Argumente, die dastehen. Der
Befehl ist nicht falsch -- er ist fuer `cmd.exe` geschrieben.

**Fassung fuer PowerShell.** Die Argumente werden als Liste uebergeben;
dann setzt PowerShell sie selbst richtig zusammen, und es braucht keine
Maskierung:

```powershell
$aufruf = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1"'
schtasks.exe @('/Create','/TN','Shorts-Kette naechtlich','/TR',$aufruf,'/SC','DAILY','/ST','03:00','/RL','HIGHEST','/F')
```

Gleichwertig ist, denselben Befehl der ersten Fassung ueber `cmd /c` zu
schicken -- dann liest ihn die Schale, fuer die er geschrieben ist.

Jeder Schalter, der Reihe nach:

| Schalter | Bedeutung |
| --- | --- |
| `/Create` | Aufgabe anlegen. |
| `/TN "Shorts-Kette naechtlich"` | Der Name, unter dem sie in der Aufgabenplanung steht. Unter diesem Namen loeschst und startest du sie spaeter auch. |
| `/TR "..."` | Was ausgefuehrt wird. Der ganze Befehl steht in Anfuehrungszeichen; der Pfad darin nochmals in maskierten (`\"`), weil er ein Leerzeichen enthalten koennte und `schtasks` sonst am ersten Leerzeichen abschneidet. |
| `-NoProfile` | PowerShell laedt dein Benutzerprofil nicht. Schneller, und vor allem: der Lauf haengt nicht davon ab, was in deinem Profil steht. |
| `-ExecutionPolicy Bypass` | Umgeht die Skript-Signaturpruefung fuer genau diesen Aufruf. Ohne das weigert sich PowerShell auf den meisten Rechnern, ein unsigniertes `.ps1` zu starten. Aendert nichts an den Einstellungen des Systems. |
| `-File "...ps1"` | Das Startskript. Es wechselt selbst ins Repo (siehe Abschnitt 6). |
| `/SC DAILY` | Taeglich. |
| `/ST 03:00` | Startzeit, siehe Abschnitt 1. |
| `/RL HIGHEST` | Hoechste verfuegbare Rechte. Noetig, weil die Kette auf `F:` und `P:` schreibt und ffmpeg startet. |
| `/F` | Ueberschreibt eine gleichnamige Aufgabe ohne Rueckfrage. Praktisch beim Neueinrichten. |

Wenn der Rechner nachts schlafen soll, willst du zusaetzlich, dass die
Aufgabe ihn weckt. Das kann `schtasks` nicht; setze es hinterher in der
grafischen Aufgabenplanung (`taskschd.msc`), Eigenschaften der Aufgabe,
Reiter *Bedingungen*, Haken bei *Computer zum Ausfuehren dieser Aufgabe
reaktivieren*.

### Die Anmeldeart: es bleibt bei „Nur interaktiv"

Im Reiter *Allgemein* steht die Wahl zwischen *Nur ausfuehren, wenn der
Benutzer angemeldet ist* und *Unabhaengig von der Benutzeranmeldung
ausfuehren*. Die zweite Einstellung verlangt beim Speichern das
**Windows-Passwort**.

**Bei einer Anmeldung ueber ein Microsoft-Konto ist das nicht die PIN.**
Der Versuch am 28.8. schlug daran fehl.

**Entschieden: es bleibt bei „Nur interaktiv".** Das genuegt fuer diesen
Zweck -- der Rechner darf **gesperrt** sein, nur nicht **abgemeldet**.
Wer nachts abmeldet, den Benutzer wechselt oder herunterfaehrt, bekommt
keinen Lauf; erkennbar daran, dass unter
`artefakte\repeat\kette-protokoll\` fuer die Nacht gar keine Datei
liegt (Abschnitt 4).

### Die Energieverwaltung muss aus dem Weg

Die Aufgabenplanung unterbindet einen Lauf sonst stillschweigend. Vier
Einstellungen, am 28.8. per PowerShell gesetzt:

| Einstellung | Wert | Wirkung |
| --- | --- | --- |
| `DisallowStartIfOnBatteries` | `false` | startet auch im Akkubetrieb |
| `StopIfGoingOnBatteries` | `false` | bricht nicht ab, wenn der Strom waehrend des Laufs ausfaellt |
| `StartWhenAvailable` | `true` | holt einen versaeumten Lauf nach |
| `WakeToRun` | `true` | weckt den Rechner (dasselbe wie der Haken oben) |

Die beiden Batteriehaken sind die **Vorgabe** der Aufgabenplanung. Wer
sie stehen laesst, bekommt auf einem Geraet im Akkubetrieb keinen Lauf
und keine Fehlermeldung.

---

## 3. Was der Lauf tut -- und was nicht

Er faehrt die sieben Stufen bis zu den **Kandidaten** und haelt dort an.

Er urteilt **nicht**, buendelt **nicht** zur Auswahl, baut **nicht**
und laedt **nichts** hoch. Das menschliche Tor bleibt: morgens startest
du selbst

```bash
uv run python -m matrix_auto_cutter.shorts.urteilslauf
```

und entscheidest danach, was gebaut wird.

Ohne `--aufnahme` waehlt die Kette selbst -- erst eine schon laufende
Kette aus einer vorgefundenen `kette.json`, sonst die juengste
unverfallene Aufnahme aus dem Bestand. Genau deshalb ruft die
Aufgabenplanung das Skript ohne Parameter auf.

**Tag ohne Aufnahme.** Findet die Kette nichts Unverfallenes, ist das
kein Fehler. Das Skript schreibt eine Zeile ins Protokoll und endet mit
0, damit die Aufgabenplanung keinen Fehlschlag meldet. Erkennbar an der
Protokollzeile:

```
Nichts zu tun: keine unverfallene Aufnahme vorhanden. Das ist der Normalfall an einem Tag ohne Aufnahme, kein Fehlschlag.
```

Jeder andere von null verschiedene Code wird unveraendert
weitergereicht und erscheint in der Aufgabenplanung als Fehlschlag.

---

## 4. Wenn es nicht laeuft

**Das Protokoll liegt unter**

```
P:\DimensionWithin-MatrixMarketAutoEditor\artefakte\repeat\kette-protokoll\
```

Eine Datei je Lauf, benannt
`<jahr>-<monat>-<tag>-<stunde><minute><sekunde>.log`. Jede Zeile traegt
einen Zeitstempel, die letzte ist eine Zusammenfassung mit Start, Ende,
Dauer, Rueckgabecode und gewaehlter Aufnahme. Fang immer dort an: die
Zusammenfassung sagt dir in einer Zeile, ob und woran es scheiterte.

Gibt es **gar keine Protokolldatei** fuer die Nacht, ist das Skript nie
angelaufen -- dann liegt es an der Aufgabe, nicht an der Kette. Sieh in
`taskschd.msc` unter *Verlauf* nach.

**Von Hand ausloesen**, ohne auf die Nacht zu warten:

```bash
schtasks /Run /TN "Shorts-Kette naechtlich"
```

**Das Skript direkt starten**, um die Ausgabe live zu sehen:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1"
```

**Erst sehen, was geschaehe**, ohne etwas auszufuehren:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1" -Trocken
```

**Eine bestimmte Aufnahme nacharbeiten**, auch eine verfallene:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "P:\DimensionWithin-MatrixMarketAutoEditor\scripts\START-SHORTS-KETTE.ps1" -Aufnahme "2026-08-25 15-14-00"
```

`-Modell <name>` reicht `--modell` durch. Alle drei Schalter sind fuer
den Handbetrieb; die Aufgabenplanung benutzt keinen davon.

**Zustand der Aufgabe ansehen:**

```bash
schtasks /Query /TN "Shorts-Kette naechtlich" /V /FO LIST
```

**Wieder entfernen:**

```bash
schtasks /Delete /TN "Shorts-Kette naechtlich" /F
```

**Wenn das Skript sofort mit `ANGEHALTEN: Repo-Wurzel ... nicht
erreichbar` endet**, war `P:` zum Zeitpunkt des Laufs nicht eingebunden.
Bei einem Netzlaufwerk passiert das, wenn niemand angemeldet ist.

---

## 5. Der Modellaufruf braucht `--permission-mode acceptEdits`

Stufe 5 (Zerlegung) und Stufe 7 (Buendelung) rufen `claude` auf. **Ohne
`--permission-mode acceptEdits` wartet dieser Aufruf auf eine
Bestaetigung, die nachts niemand gibt.** Er haengt dann, bis eine
Zeitgrenze greift.

`kette.py` setzt den Schalter selbst; du musst nichts tun. Diese
Warnung steht hier fuer den Fall, dass jemand den Aufruf aendert.

**Woran du erkennst, dass es daran lag:** im Protokoll steht die
Kopfzeile der Stufe

```
Stufe 5 von 7: Zerlegung (Modell), Modell opus
```

und danach ueber Minuten hinweg nur die Fortschrittsmeldungen
`Zerlegung laeuft seit ...` -- ohne dass je eine `fertig in ...`-Zeile
folgt. Die Zusammenfassung nennt dann einen von null verschiedenen
Rueckgabecode, und die erwartete Ausgabedatei (`kandidaten-lauf1.json`
bzw. `buendel.json`) fehlt im Aufnahmeordner. Ein echter Modellfehler
sieht anders aus: er endet schnell und mit einer Meldung.

---

## 6. Warum der feste Pfad im Skript steht

Das Skript wechselt mit einem hart eingetragenen Pfad ins Repo, nicht
relativ zu seinem eigenen Ort. Zwei Gruende, beide handfest:

1. Die Aufgabenplanung startet eine Aufgabe mit **unbestimmtem
   Arbeitsverzeichnis** -- meist `C:\Windows\system32`, je nach
   Anmeldeart aber auch anders.
2. `TREFFERQUOTE_PFAD` in `src\matrix_auto_cutter\shorts\auswahl.py` ist
   **relativ** (`labels/repeat/trefferquote.json`). Aus dem falschen
   Verzeichnis heraus legte die Kette diese Datei an der falschen Stelle
   an, statt zu scheitern -- ein stiller Fehler, den man erst Tage
   spaeter bemerkt.

Ziehst du das Repo je um, ist `$RepoWurzel` im Kopf von
`scripts\START-SHORTS-KETTE.ps1` die eine Stelle, die du aenderst. Der
Test `test_traegt_den_festen_repo_pfad` in `tests\test_shorts_wecker.py`
haelt beides zusammen.
