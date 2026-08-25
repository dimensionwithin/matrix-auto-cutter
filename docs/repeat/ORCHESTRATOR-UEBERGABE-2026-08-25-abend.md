# Orchestrator-Uebergabe — Shorts-Produktionslinie, 25. August 2026, Abend
Stand: HEAD `8e6ab9a` auf `master`, gepusht. Arbeitsbaum: die bekannte Datei `-`
sowie die drei Belege des Betriebslaufs unter `labels\repeat\` (Abschnitt 3).

**Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-25.md` NICHT ab — es
setzt sie fort.** Alles, was dort steht, gilt weiter, wo hier nichts anderes
steht. Die Nachmittagsfassung wird verwiesen, nicht abgeschrieben. Das
Nachschlagewerk zu diesem Dokument ist `BESTAND-2026-08-25-abend.md`; dort
stehen Zeilennummern, Signaturen und Schemata.

Was hier steht, stammt aus den Berichten unter `artefakte\repeat\`
(`kettenlaeufer`, `render-wurzel-und-riegel`, `modellfahne`), aus den in diesem
Auftrag gelesenen Quelldateien und aus den Artefakten des Betriebslaufs. Jede
Zahl traegt ihre Herkunft. Was nicht belegt ist, ist als **nicht belegt**
gekennzeichnet.

Frag beim ersten Kontakt nach `git log --oneline -6` und
`git status --porcelain`.

---

## 1 — Rolle und Arbeitsweise

**Die Abschnitte 1.1 bis 1.4 der Nachmittagsfassung gelten unveraendert** —
Aufbau eines Auftrags, Freigaben, Sperrliste, Umgang mit dem Nutzer. Zwei
Dinge sind heute Abend dazugekommen.

### 1.1 Bauauftraege laufen mit Opus, mittlere Denktiefe

**Entscheidung des Auftraggebers vom 25.8. abends.** Begruendung: das Gebaute
soll lange halten. Sonnet bleibt fuer reine Commit- und Doku-Auftraege, bei
denen nichts entworfen wird.

Das ist eine Aenderung gegenueber der bisherigen Praxis, in der die Modellwahl
je Auftrag neu abgewogen wurde. Der Grund fuer die Festlegung ist nicht die
gemessene Qualitaet eines einzelnen Laufs, sondern die Lebensdauer des
Ergebnisses: ein Modul, das ein halbes Jahr im Betrieb steht, rechtfertigt die
teurere Denktiefe. Die Zerlegung ist davon **nicht** betroffen — dort gilt
weiterhin Abschnitt 4.3 der Nachmittagsfassung (Sonnet genuegt), und seit
heute Abend ist das Modell ohnehin je Lauf waehlbar (Abschnitt 2).

### 1.2 Ein Auftrag, dessen Vorgabe der Wirklichkeit widerspricht, wird berichtigt — nicht befolgt

Bisher galt: das ausfuehrende Fenster haelt an und meldet. Das bleibt richtig,
wenn die Arbeit dadurch unmoeglich wird. **Wenn sie es nicht wird, berichtigt
das Fenster die Vorgabe, fuehrt den Auftrag zu Ende und meldet die Abweichung
ausdruecklich im Bericht.** Ein verlorener Umlauf kostet mehr als eine
begruendete Korrektur.

Der Anlass, an dem das gelernt wurde, ist der Nachschlaghinweis aus dem
Auftrag `modellfahne-und-nachschlagzeile`. Der Orchestrator hatte diktiert,
`urteilslauf` solle hinschreiben: „ein zweiter Zerlegungslauf erzeugt
`kandidaten-lauf2.json`, und die Zusammenfuehrung ist NICHT gebaut — `kette`
haelt dann mit Code 6 an."

**Der erste Halbsatz stimmt nicht.** `ZERLEGUNG_LAUF` ist eine Konstante mit
dem Wert 1 (`kette.py:88`), die Stufenausgabe heisst fest
`kandidaten-lauf1.json` (`kette.py:110`), und der Auftragstext sagt dem Modell
`<N> ist 1`. Ein Nachschlag mit `--neu-ab zerlegung` erzeugt keine zweite
Datei — er **ueberschreibt den ersten Lauf**. Der Hinweis haette den Nutzer
genau den Kandidatensatz gekostet, den er schuetzen wollte.

Das Fenster hat den Hinweis so geschrieben, dass er beide Tatsachen nennt, und
die Abweichung im Bericht (`modellfahne\BERICHT-2026-08-25.md`, TEIL 2) als
eigenen Abschnitt begruendet. Das ist die Form, die kuenftig erwartet wird:
berichtigen, ausfuehren, ausdruecklich melden.

**Zweiter Fall aus demselben Auftrag:** TEIL 3 verlangte, keine bestehende
Testfunktion zu aendern; TEIL 2a verlangte eine Prozentangabe am Ende der
Quotenzeile. Zwei Zusicherungen vergleichen genau diese Zeile auf Gleichheit —
die Vorgaben schliessen einander aus. Das Fenster hat die minimale Aenderung
vorgenommen (die erwartete Zeichenkette um die neue Angabe erweitert, der
Vergleich bleibt Gleichheit) und es gemeldet. **Beim Schreiben eines Auftrags
also pruefen, ob eine Ausgabeaenderung bestehende Zeilenvergleiche trifft.**

---

## 2 — Stand der Linie

Der Stand der Nachmittagsfassung (Abschnitt 2: gebaute Stufen, nicht
verdrahtete Endcard, fehlendes Kandidatensuchmodul) **gilt unveraendert**.
Neu ist, dass die Linie jetzt aus **zwei Befehlen** besteht.

### Die vier Commits seit der Nachmittagsuebergabe (`a47921e`)

| Commit | Botschaft |
|---|---|
| `d6dc3ee` | Shorts: Urteilslauf als ein Befehl, mit unterbrechbarem Warten |
| `954c8a0` | Shorts: Stufe 0 kopflos, Platzhalter mit Zeitangabe |
| `7c4195d` | Shorts: Kettenlaeufer, Bau im Urteilslauf, Zielwurzel berichtigt |
| `8e6ab9a` | Shorts: Modell der Zerlegung waehlbar, Nachschlagbefehl im Urteilslauf |

Quelle: `git log --oneline -6` in diesem Auftrag.

### Befehl 1 — `kette`, bis vor das Urteilstor

```powershell
python -m matrix_auto_cutter.shorts.kette [--aufnahme NAME] [--modell NAME]
       [--neu] [--neu-ab STUFE] [--bis STUFE] [--trocken] [--wurzel PFAD]
```

Sechs Stufen am Stueck: Auftragsdatei → Avatarschnitt → Transkription →
Wortliste → Zerlegung → Zusammenfuehrung. Nach **jeder** Stufe schreibt er
`kette.json` in den Aufnahmeordner; ein abgebrochener Lauf laesst sich damit
fortsetzen, statt von vorn zu beginnen. Waehrend einer laufenden Stufe meldet
er alle 30 s, wie lange sie schon laeuft — vorher gab kein Werkzeug der Kette
ein einziges Zeichen von sich (`kettenlaeufer\BERICHT-2026-08-25.md`).

Eine Stufe gilt als erledigt, wenn ihre Ausgabe daliegt und `kette.json` sie
nicht als `laeuft` oder `gescheitert` fuehrt. Ein **fehlender** Eintrag zaehlt
als „keine Auskunft" und steht dem Ueberspringen nicht entgegen — sonst baute
der erste Lauf ueber einem von Hand durchgefahrenen Bestand alles neu,
einschliesslich Transkription und Zerlegung.

Neu seit `8e6ab9a`: **`--modell NAME`** (Vorgabe `sonnet`) wird an
`claude --model` durchgereicht **und** als Wurzelfeld `modell` in den
Auftragstext geschrieben. Beides, nicht eines: die Fahne bestimmt, womit
gefahren wird, das Wurzelfeld haelt fest, womit gefahren wurde — daraus liest
die Trefferquote. Der Wert steht auch in `kette.json` beim Stufeneintrag
`zerlegung`, aber nur, wenn die Stufe wirklich angelaufen ist.

### Befehl 2 — `urteilslauf`, vom Urteilstor bis zu den fertigen Shorts

```powershell
python -m matrix_auto_cutter.shorts.urteilslauf [JOB_PATH] [--kein-server]
       [--keine-auswahl] [--keine-sicherung] [--kein-bau]
       [--platzhalter-server [SEKUNDEN]] [--wurzel PFAD]
```

Sieben Schritte: Aufnahme bestimmen → Urteile pruefen → Urteilsseite starten →
Quote → Bauliste → Sicherung → **Bau**. Der Nutzer setzt einen Befehl ab,
urteilt im Browser, schliesst das Fenster mit Strg+C — und der Rest laeuft
durch.

Der Bau in Schritt 7 ist seit `7c4195d` verdrahtet. Er zaehlt zum Nachweis
`short.mp4`, nicht den Rueckgabecode von `build` (Abschnitt 5.2 der
Nachmittagsfassung), haelt bei belegtem Ziel mit Code 7 an und bei
unvollstaendigem Bau mit Code 8.

### Was zwischen den beiden Befehlen noch von Hand geschieht

Nichts ausser dem Urteilen selbst — und dem Anstossen der beiden Befehle. Das
Urteilstor bleibt strukturell unumgehbar (Abschnitt 6 der Nachmittagsfassung).

---

## 3 — Der erste vollstaendige Betriebslauf vom 25.8. abends

Aufnahme `2026-08-25 15-14-00`, **1.073.716 ms** Material (rund 17,9 min).
Quelle der Materialdauer: `shorts-job.json` der Aufnahme, Feld
`rendered_video.duration_ms`, in diesem Auftrag gelesen.

Der erste Lauf, bei dem die Linie **als Linie** gefahren wurde — nicht mehr
als Folge einzeln angestossener Werkzeuge. Der Durchlauf vom Nachmittag
(Aufnahme `2026-08-21 10-46-08`, Abschnitt 3 der Nachmittagsfassung) bleibt
zum Vergleich stehen.

### 3.1 Die Laufzeiten je Stufe

Quelle: `artefakte\repeat\shorts\2026-08-25 15-14-00\kette.json`, Feld
`dauer_s` je Stufe. **Diese Datei liegt unter `artefakte\` und ist damit
`.gitignore`-ausgeschlossen — die Zahlen stehen sonst nirgends im
Repository.** Sie hier festzuhalten war der Zweck dieses Auftrags.

| Stufe | Dauer | Anmerkung |
|---|---|---|
| 1 Auftragsdatei | **3,5 s** | Stufe 0 kopflos, seit `954c8a0` |
| 2 Avatarschnitt | **532,1 s** | 8 min 52 s |
| 3 Transkription | **1310,2 s** | 21 min 50 s |
| 4 Wortliste | **0,5 s** | |
| 5 Zerlegung | **1553,5 s** | 25 min 53 s, Sonnet ueber `claude -p` |
| 6 Zusammenfuehrung | **0,0 s** | reine Kopie |
| **Summe bis zu den Kandidaten** | **3399,8 s** | **56,7 min** |

**Die Schaetzung der Transkription war um 4 % zu hoch.** Angezeigt wurden
`22 min 43 s` (1073,716 s × `TRANSKRIPTION_FAKTOR` 1,27 = 1363,6 s), gemessen
1310,2 s. 1363,6 / 1310,2 = 1,041. Der Faktor 1,27 stammt aus der Messung vom
21.8. und traegt damit auch ueber die doppelte Materiallaenge.

### 3.2 Der Bau

| Groesse | Wert |
|---|---|
| Kandidaten | 25 |
| Laufzeit | **1656,9 s** (27,6 min) |
| davon Vorlauf | **415,7 s** bei **kaltem** Framezahl-Cache |
| Ergebnis | **25 von 25 gebaut** |

**Herkunft: die Werkzeugausgabe des Laufs, wie sie der Auftrag als GEGEBEN
nennt. Im Repository nicht belegt** — der Baubericht liegt unter
`F:\MatrixMarketAutoEdit\Shorts Rendered\2026-08-25 15-14-00\`, und `F:` war
in diesem Auftrag gesperrt. Wer die Zahlen nachrechnen will, liest dort
`shorts-bau-bericht.json`.

Der Vorlauf bestaetigt die Falle aus Abschnitt 5.2 der Nachmittagsfassung in
der Groessenordnung: 415,7 s bei kaltem Cache gegen 0,1 s bei warmem. Der
dort genannte Vergleichswert war 230,1 s bei 584.900 ms Material — bei
1.073.716 ms, also knapp der doppelten Laenge, sind 415,7 s stimmig.

### 3.3 Die Gesamtrechnung

    bis zu den Kandidaten   3399,8 s   =  56,7 min
    Bau                     1656,9 s   =  27,6 min
    ------------------------------------------------
    Maschinenzeit           5056,7 s   =  84,3 min

**Rund 84 Minuten Maschinenzeit fuer knapp 18 Minuten Material** — das
4,7-fache der Echtzeit. Nicht enthalten: die Urteilszeit des Nutzers und die
Wartezeit zwischen den beiden Befehlen.

*(Der Auftrag nannte „rund 85 min". Die Summe der belegten Einzelwerte ergibt
84,3 min. Die Differenz ist Rundung, kein Widerspruch; hier steht der
gerechnete Wert.)*

### 3.4 Das Ergebnis

Alle folgenden Zahlen sind in diesem Auftrag aus den Artefakten
**nachgerechnet**, nicht uebernommen.

| Groesse | Wert | Quelle |
|---|---|---|
| Transkriptsegmente | **438** | `transkript-rendered.json` |
| Woerter | **3023** | `wortliste.json` |
| Kandidaten | **39** | `kandidaten-lauf1.json` |
| Angenommen | **25** (Quote **64,1 %**) | `labels\repeat\trefferquote.json`, Eintrag 2 |
| Abgelehnt | 14 | ebenda |
| Ohne Urteil | 0 | ebenda |
| Gebaut | 25 von 25 | Werkzeugausgabe, s. 3.2 |

**Struktur der 39 Kandidaten** (nachgerechnet aus
`labels\repeat\kandidaten-2026-08-25 15-14-00-lauf1-sonnet.json`):

| Groesse | Wert |
|---|---|
| Indizes | 1–39, **lueckenlos** |
| Ueberlappungen | **keine** |
| Median der Dauer | **12,82 s** |
| Im Zielbereich 8–15 s | **29 von 39 = 74,4 %** |
| Kuerzester / laengster | 8,22 s / 17,82 s — **keiner unter 8 s, keiner ueber 20 s** |
| Materialabdeckung | **46,7 %** |
| Sicherheit | **17 hoch / 20 mittel / 2 niedrig** |

Die Struktur ist tadellos: das Modell haelt die Kriterien 0.8 ein, auch bei
doppelter Materiallaenge. **Die Ausbeute ist es nicht** — 64 % gegen 87 % beim
Lauf vom Nachmittag. Zur Vermutung ueber die Ursache siehe Abschnitt 7.

### 3.5 Der Lauf fand in zwei Anlaeufen statt

Aus den Zeitstempeln in `kette.json` abgelesen: die Stufen 1 bis 4 liefen von
`20:37:20` bis `21:08:07` UTC. Die Zerlegung begann erst `21:25:44` UTC — rund
17,6 Minuten spaeter. Ihre `meldung` ist `null`, waehrend die vier Stufen davor
`"uebersprungen, Ausgabe lag bereits vor"` tragen.

Der Lauf wurde also nach der Wortliste unterbrochen und mit einem zweiten
Aufruf fortgesetzt, der die vier fertigen Stufen uebersprang. **Genau der Fall,
fuer den `kette.json` gebaut wurde — er hat beim ersten scharfen Einsatz
funktioniert.** Der Grund der Unterbrechung ist **nicht belegt**.

Beachte dabei die Eigenheit aus Abschnitt 5: die uebersprungenen Stufen tragen
weiterhin `dauer_s` aus dem ersten Anlauf. Nur deshalb sind die Laufzeiten
ueberhaupt noch da.

---

## 4 — Entscheidungen samt Begruendung

Die Entscheidungen 4.1 bis 4.4 der Nachmittagsfassung gelten weiter, **mit
einer Berichtigung an 4.1** (siehe 4.2 hier). Neu sind drei.

### 4.1 Der Bau laeuft im `urteilslauf` mit, abschaltbar ueber `--kein-bau`

Seit `7c4195d`. Begruendung: **der Nutzer soll einen Befehl absetzen und
danach nur noch urteilen.** Ein Bau, der als achter Befehl von Hand
nachgeschoben werden muss, macht aus einem Ablauf wieder eine Bedienung.

`--kein-bau` gibt die Bauzeile stattdessen nur aus — der Weg fuer Aufnahmen,
deren Shorts schon gebaut sind, und der Weg fuer jede Erprobung.

Der Bau uebernimmt dabei die Regeln, die am Nachmittag als Fallen notiert
waren: Erfolgsnachweis ist die Zahl der `short.mp4` (Code 8, wenn sie nicht
zur Bauliste passt), und ein belegtes Ziel haelt den Lauf an (Code 7), statt
Fertiges zu ueberbauen.

### 4.2 Die Zielwurzel heisst `F:\MatrixMarketAutoEdit\Shorts Rendered\` — mit LEERZEICHEN

**Das ist eine Berichtigung.** Der Code fuehrte bis `7c4195d` den Namen mit
Bindestrich (`Shorts-Rendered`); der tatsaechliche Ordner auf `F:` traegt ein
Leerzeichen. Seit Schritt 7 nicht mehr nur ausgibt, sondern ausfuehrt, haette
ein scharfer Bau ins Leere gezielt. Berichtigt in
`urteilslauf.py:52` (`RENDER_WURZEL`), der Wert steht in genau einer Konstante.

**Die Dokumente vom 25.8. nachmittags tragen den falschen Namen an vier
Stellen.** Sie bleiben unangetastet — sie sind Zeitstand. Wer dort abschreibt,
legt wieder einen Bindestrich-Ordner an:

| Datei | Zeile | Stelle |
|---|---|---|
| `docs\repeat\BESTAND-2026-08-25.md` | 442 | Bauaufruf in Abschnitt 9 |
| `docs\repeat\BESTAND-2026-08-25.md` | 514 | „Neuer Ausgabeort der Shorts" |
| `docs\repeat\ORCHESTRATOR-UEBERGABE-2026-08-25.md` | 276 | Kommandozeile in Abschnitt 3.3 |
| `docs\repeat\ORCHESTRATOR-UEBERGABE-2026-08-25.md` | 300 | Ueberschrift 4.1 |

Die Zeilen 514 und 300 sind die gefaehrlicheren: sie stehen als Aussage ueber
den Bestand da, nicht als Protokoll eines Laufs. Fundstellen in diesem Auftrag
mit `grep` verifiziert.

Ausserdem tragen 28 Zeilen in sechs Berichten unter `artefakte\repeat\` den
falschen Namen. Die sind Protokoll und bleiben.

### 4.3 Tests starten keine echten Unterprozesse und fassen `F:` nicht an

Seit `7c4195d`. Zwei `autouse`-Fixtures in `tests\conftest.py` greifen fuer
jeden Test der `test_shorts_*`-Dateien:

- `subprocess.run` und `subprocess.Popen` werfen `RiegelVerletzt`, statt zu
  starten.
- Jeder Pfad, dessen erste zwei Zeichen `f:` sind, laesst den Test scheitern.

Ausnahme je Test ueber `@pytest.mark.echter_unterprozess` — nie global.
Derzeit sieben Tests: vier Platzhalter-Tests des Urteilslaufs und drei Tests
der Prozesswache in `build.py`. Bei beiden Gruppen **ist** der Unterprozess der
Gegenstand des Tests.

Der Anlass: sechs Tests hatten `build` echt gestartet und dabei Ordner auf `F:`
angelegt. Was der Riegel dann tatsaechlich fand, war groesser — Abschnitt 5.

---

## 5 — Betriebsfallen

Alle Fallen der Nachmittagsfassung (Abschnitte 5.1 und 5.2) **gelten weiter**.
Vier sind heute Abend dazugekommen.

### 5.1 Die Schlusszeile von `kette` meldet Vollendung auch bei `--bis N`

`kette.py:745` gibt nach der Stufenschleife unbedingt
`Kette fertig: ...\kandidaten.json` und `Weiter mit: ... urteilslauf` aus — auch
dann, wenn `--bis 3` den Lauf planmaessig nach der Transkription beendet hat
und weder `kandidaten.json` noch sonst etwas Weiterfuehrendes existiert.

**Der Zustand in `kette.json` ist korrekt; nur der Text luegt.** Wer den
Erfolg eines Laufs feststellen will, liest die Zustandsdatei, nicht die
Schlusszeile. Nicht behoben.

### 5.2 `kette.json` kann fuer dieselbe Stufe eine Dauer und „uebersprungen" tragen

Der Ueberspring-Zweig setzt `status`, `ausgabe` und `meldung`, laesst aber
`dauer_s`, `begonnen_am` und `beendet_am` des vorigen Laufs stehen. Eine Stufe
kann darum gleichzeitig `"meldung": "uebersprungen, Ausgabe lag bereits vor"`
und `"dauer_s": 1310.2` tragen — die 1310,2 s stammen dann aus einem frueheren
Anlauf, nicht aus diesem.

Genau so steht es in `kette.json` des Betriebslaufs (Abschnitt 3.5). **Das ist
ein Glueck, kein Entwurf:** ohne diese Eigenheit waeren die vier Laufzeiten aus
Abschnitt 3.1 verloren gewesen. Wer die Zeiten eines **bestimmten** Laufs
braucht, muss `meldung` mitlesen.

### 5.3 `kriterien_fassung` wird in zwei Schreibweisen geschrieben

| Lauf | Wert |
|---|---|
| 21.8., Aufnahme `2026-08-21 10-46-08` | `"Fassung 0.8 (24. August 2026)"` |
| 25.8., Aufnahme `2026-08-25 15-14-00` | `"0.8"` |

Belegt in `labels\repeat\trefferquote.json` (beide Eintraege) und in den beiden
Kandidatendateien unter `labels\repeat\`. **In der Trefferquote ergibt das zwei
Gruppen fuer dieselbe Kriterienfassung** — jede Auswertung, die nach
`kriterien_fassung` gruppiert, zerfaellt.

Warum der zweite Lauf die kurze Form schrieb, ist **nicht belegt**; der
Auftragstext `ZERLEGUNG-AUFTRAGSTEXT.md` verlangt eine Fassungspruefung, gibt
aber offenbar keine Schreibweise vor. Vor der ersten Auswertung je Fassung ist
das zu vereinheitlichen — entweder im Auftragstext oder durch Normalisieren in
`auswahl.py`.

### 5.4 13 Build-Tests starteten bei jedem Suitenlauf echtes ffmpeg

Gefunden durch den Riegel aus Abschnitt 4.3
(`render-wurzel-und-riegel\BERICHT-2026-08-25.md`, TEIL 3a). Die Tests
uebergeben `ffmpeg_path=Path("ffmpeg.exe")` — im Testcode sieht das wie ein
Platzhalter aus, trifft aber ueber den `PATH` die echte Binaerdatei (auf dieser
Maschine ffmpeg 8.1.1 aus dem WinGet-Paket). Die Wortrand- und Pegelsuche in
`build._apply_level_correction` lief damit bei jedem Suitenlauf gegen leere
`tmp_path`-Dateien.

**Ein Test war gruen aus dem falschen Grund:**
`test_probe_duration_ms_no_ffprobe_available` prueft dem Namen nach „kein
ffprobe verfuegbar" und lieferte `None` — aber weil das **echte** ffprobe an
einer nicht existierenden Datei scheiterte, nicht weil keines gefunden wurde.
Beides repariert durch Mocken, nicht durch die Markierung.

**Die Lehre fuer Auftraege:** ein Pfad ohne Verzeichnis ist im Test kein
Platzhalter, sondern eine `PATH`-Suche. Wo ein Test ein Werkzeug nicht rufen
soll, gehoert ein Pfad hin, den es nicht gibt — oder ein Mock.

### 5.5 Ein blockierendes `process.wait()` stellt unter Windows kein Strg+C zu

Gemessen (`urteilslauf-strg-c\BERICHT-2026-08-25.md`, Zeilen 171–172):

| Fassung | Ergebnis |
|---|---|
| blockierendes `process.wait()`, Kind schlaeft 6 s | `KeyboardInterrupt` erst nach **6,06 s** — also erst, als das Kind ohnehin endete |
| Warteschleife plus Signalhandler | Rueckkehr nach **1,02 s**, ueber den Merker; kein `KeyboardInterrupt` ausgeloest |

Das blockierende `wait()` liess den Interpreter nie zum Signalpruefpunkt
kommen. Deshalb wartet sowohl `urteilslauf.warte_auf_kind` als auch
`kette.fuehre_prozess` in Takten von 0,25 s statt in einem einzigen `wait()`.
Der Fortschrittstakt alle 30 s faellt als Nebengewinn desselben Musters ab.

**Wer irgendwo in dieser Linie auf einen Kindprozess wartet, uebernimmt dieses
Muster** — sonst ist der Prozess unter Windows nicht abbrechbar.

---

## 6 — Der Weg weiter

Der Weg zur Automatisierung aus Abschnitt 6 der Nachmittagsfassung ist in den
Schritten 1 bis 3 **erledigt**: Startskript, Stufe 0 kopflos und der
Kettenlaeufer stehen und sind im Betrieb gefahren worden. Was bleibt, sind
drei Vorhaben in dieser Reihenfolge.

### (1) `--lauf N` und die Zusammenfuehrung — dringend

**Das dringendste Vorhaben.** Bei 64 % Quote (Abschnitt 3.4) ist der
Nachschlag kein Sonderfall mehr; die Entscheidung 4.2 der Nachmittagsfassung
(„die Zerlegung laeuft im Regelfall einmal") stuetzte sich auf 87 %.

Heute ist der Nachschlag **nicht fahrbar**: `--neu-ab zerlegung` schreibt
wieder `kandidaten-lauf1.json` und ueberschreibt den ersten Lauf (Abschnitt
1.2). `urteilslauf` schreibt den Nachschlagbefehl seit `8e6ab9a` samt dieser
Warnung hin — die Warnung ersetzt die Loesung nicht.

Zwei Dinge gehoeren zusammen:

- **`--lauf N`** in `kette` (oder ein Hochzaehlen aus den vorhandenen
  `kandidaten-lauf*.json`). Der kleinere Teil: `ZERLEGUNG_LAUF` ist bereits
  ueberall als Parameter durchgereicht, nur die Fahne fehlt.
- **Die Zusammenfuehrung.** Der groessere Teil. Die Regel steht in
  `zerlegung_laeuft_zweimal` in der Kriteriendatei (mehr als die Haelfte der
  kuerzeren Dauer Ueberlappung → die laengere Fassung), aber es gibt weder
  Auftragstext noch Code. `kette.fuehre_zusammen` haelt heute bei mehr als
  einer Laufdatei bewusst mit Code 6 an, statt zu raten. Offen seit dem 24.8.

### (2) Der Wecker

Windows-Aufgabenplanung ruft `claude -p --permission-mode acceptEdits`. Die
Fallen stehen vollstaendig in Abschnitt 6, Schritt 4 der Nachmittagsfassung
(Arbeitsverzeichnis setzen, `TREFFERQUOTE_PFAD` ist relativ, `.claude\` ist
leer, kein Zeitplaner in Claude Code 2.1.220).

**Beim Aufsetzen erneut pruefen, ob eine neuere Claude-Code-Fassung einen
eingebauten Zeitplaner mitbringt.** Fuer 2.1.220 ist das Fehlen belegt; fuer
neuere Fassungen **nicht belegt** — in diesem Auftrag wurde nicht geprueft.

### (3) Eine Oberflaeche fuer die Shorts-Linie

Angelehnt an das bestehende Interface des Cutters. **Noch nicht erkundet** —
der Orchestrator hat das bestehende Interface nie gesehen, und in diesem
Auftrag wurde `review_app.py` / `app.py` nicht gelesen (Sperrliste bzw. kein
Anlass). Was es kann, wie es aufgebaut ist und was sich uebernehmen laesst, ist
**nicht belegt**.

**Das braucht einen eigenen Erkundungsauftrag**, bevor irgendetwas entworfen
wird: rein lesend, mit Bericht, der das bestehende Interface beschreibt —
Rahmenwerk, Aufbau, Zustandshaltung, Startweg — und benennt, was fuer die
Shorts-Linie taugt.

---

## 7 — Offene Punkte

Alle offenen Punkte der Nachmittagsfassung (Abschnitt 7) **bleiben offen**,
soweit hier nichts anderes steht. Erledigt sind daraus: nichts. Neu oder
veraendert sind die folgenden.

### Punktdichte und Ausbeute — eine Vermutung, keine Reihe

| Aufnahme | Material | Kandidaten | Quote | Interpunktion |
|---|---|---|---|---|
| `2026-08-21 10-46-08` | 584.900 ms | 31 | **87,1 %** | normal interpunktiert |
| `2026-08-25 15-14-00` | 1.073.716 ms | 39 | **64,1 %** | zehn Minuten ohne jedes Satzzeichen |

Quoten aus `labels\repeat\trefferquote.json`, beide Eintraege, in diesem
Auftrag gelesen. Die Angabe zur Interpunktion der zweiten Aufnahme stammt aus
dem Auftrag und ist hier **nicht nachgezaehlt**.

**Das sind ZWEI Messpunkte, keine Reihe.** Der Zusammenhang ist eine
**Vermutung** und ausdruecklich nicht belegt: es koennte ebenso an der
Materiallaenge liegen (die doppelte Laenge verduennt die guten Stellen), am
Thema, an der Tagesform des Modells oder an der Urteilsstrenge des Nutzers.

Was dafuer spraeche: die Kandidatenstruktur ist bei beiden Laeufen tadellos
(Abschnitt 3.4) — das Modell haelt die formalen Kriterien ein und trifft
trotzdem schlechter. Das passt zu der Annahme, dass ihm die Satzgrenzen
fehlten, an denen ein Ausschnitt anfangen und aufhoeren sollte.

**Was zu tun ist:** mitzaehlen. `auswahl.py` schreibt die Quote je Lauf
ohnehin fort; die Punktdichte gehoert daneben. Der Auftragstext der Zerlegung
verlangt bereits vier Selbstauskuenfte zur Punktdichte — sie landen aber nicht
in `trefferquote.json`. Drei bis fuenf weitere Laeufe, dann laesst sich die
Frage beantworten. Vorher nicht.

Der Widerspruch in den Punktdichte-Zahlen des 21.8. (Abschnitt 3.1 der
Nachmittagsfassung, 29/34 und 424/422) ist **unveraendert offen** und beim
Nachzaehlen mitzuerledigen.

### Die Zwischenstufen im Bauordner bleiben liegen

Unveraendert offen seit dem 25.8. nachmittags. Je Kandidat stehen neben
`short.mp4` die Zwischenstufen `ausschnitt.mp4`, `leinwand.mp4` und
`mit-avatar.mp4` samt Seitendateien; sie machen rund 70 % des Zielordners aus.
**Ein Aufraeumer fehlt weiterhin.**

Mit dem Betriebslauf ist ein zweiter Zielordner dazugekommen. Bei
Groessenordnung 252 MB je Aufnahme (Messung 21.8.) waechst der Posten mit jeder
Aufnahme. Die Groesse des neuen Ordners ist **nicht belegt** — `F:` war in
diesem Auftrag gesperrt.

### `angenommen` traegt in Teillisten die Zahl der Gesamtauswahl

Unveraendert offen. In `bauliste-rest.json` steht `angenommen: 27` bei 24
Eintraegen. Der Bau liest das Feld nicht; ein Auswertungsskript wuerde darauf
hereinfallen.

### Weiterhin offen aus der Nachmittagsfassung

Ohne Aenderung: der fehlende Auftragstext der Zusammenfuehrung (siehe
Abschnitt 6 (1)); `die_sicherheitsstufe_traegt` gilt fuer Sonnet nicht — der
Betriebslauf liefert dazu einen **zweiten Gegenbefund**, `hoch` 10 von 17 gegen
`mittel` 14 von 20 (`trefferquote.json`, Eintrag 2), die Stufe `hoch` traegt
also erneut nicht besser; das Veroeffentlichen; der Stillevorlauf-Rueckfall
bei kandidat-08/-09 vom 21.8.; die verschwundene +1-Frameabweichung; der
fehlende turbo-Durchsatzwert in `UMGEBUNG.md`; die lueckenhafte
Fassungsgeschichte der Kriteriendatei; die vier Ordner mit zusammengeschobenen
Pfadnamen; `laengere_fassung` als nie befuelltes Feld; und die Datei `-` aus
`tests\repeat\test_cutcli.py`.
