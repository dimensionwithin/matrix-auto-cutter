# Orchestrator-Uebergabe — Shorts-Produktionslinie, 27. August 2026
Stand: HEAD `d8c6c61` auf `master`, gepusht. Arbeitsbaum: die bekannte Datei `-`
sowie zwei unversionierte Belege unter `labels\repeat\` mit dem Namensbestandteil
`unbekannt` (Abschnitt 7.1).

**Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-25-abend.md` NICHT ab — es
setzt sie fort.** Alles, was dort steht, gilt weiter, wo hier nichts anderes
steht; und die Abendfassung setzt ihrerseits die Nachmittagsfassung
`ORCHESTRATOR-UEBERGABE-2026-08-25.md` fort. Es sind also drei Dokumente in einer
Kette, nicht drei Fassungen desselben. Das Nachschlagewerk zu diesem Dokument ist
`BESTAND-2026-08-27.md`; dort stehen Zeilennummern, Signaturen und Schemata.

Was hier steht, stammt aus den vier Berichten unter `artefakte\repeat\`
(`nachschlag`, `zusammenfuehrung-schont-urteile`, `buendelung`,
`urteilsseite-gruppiert`), aus den in diesem Auftrag gelesenen Quelldateien, aus
den Artefakten der beiden Betriebslaeufe und aus den Angaben des Auftraggebers.
Jede Zahl traegt ihre Herkunft. Was nicht belegt ist, ist als **nicht belegt**
gekennzeichnet. Wo zwei Quellen einander widersprechen, stehen **beide** Fassungen
da, ausdruecklich als Widerspruch bezeichnet.

Frag beim ersten Kontakt nach `git log --oneline -6` und `git status --porcelain`.

---

## 1 — Rolle und Arbeitsweise

**Die Abschnitte 1.1 bis 1.4 der Nachmittagsfassung vom 25.8. und die Abschnitte
1.1 und 1.2 der Abendfassung gelten unveraendert** — Aufbau eines Auftrags,
Freigaben, Sperrliste, Umgang mit dem Nutzer, Modellwahl, und vor allem die Regel
„berichtigen statt befolgen". Zwei Dinge sind seit dem 26.8. dazugekommen.

### 1.1 Die Berichtigungsregel hat sich dreimal bewaehrt

Abschnitt 1.2 der Abendfassung sagt: ein Auftrag, dessen Vorgabe der Wirklichkeit
widerspricht, wird berichtigt, ausgefuehrt und die Abweichung ausdruecklich
gemeldet. Am 26. und 27.8. ist das dreimal geschehen. Alle drei Faelle belegen,
dass die Regel richtig ist — und alle drei gehen auf **Vorgaben des
Orchestrators** zurueck, nicht auf Maengel des Codes.

**Fall 1 — die Pruefstein-Zahl 63 statt 69.** Der Auftrag
`zusammenfuehrung-schont-urteile` verlangte in TEIL 5 die Nachweiszeile
`39 von 63 beurteilt`. Derselbe Auftrag verlangte in TEIL 3 aber 39 + 24 + 6 = 69
Kandidaten und nannte die neuen Indizes 64 bis 69. Die 63 war der Istwert des
**kaputten** Bestands, in dem die sechs laengeren Fassungen noch in die Indizes
10, 14, 16, 18, 33 und 34 hineingeschrieben waren, statt angehaengt zu werden. Mit
der berichtigten Regel kann sie nicht mehr herauskommen. Das Fenster hat 69
geschrieben und den Widerspruch als eigenen Abschnitt gemeldet.
Quelle: `zusammenfuehrung-schont-urteile\BERICHT-2026-08-26.md`, Abschnitt
„ANGEHALTEN — eine Vorgabe berichtigt".

**Fall 2 — die Schreibweise von `kriterien_fassung`.** Der Auftrag `nachschlag`
liess in `ZERLEGUNG-AUFTRAGSTEXT.md` festschreiben, das Feld trage kuenftig die
Kopfzeile der Kriteriendatei woertlich. Das Fenster hat das ausgefuehrt **und im
selben Zug gemeldet, dass die Aenderung das Problem nicht loest, sondern eine
dritte Schreibweise hinzufuegt** (Abschnitt 7.2). Eine ausgefuehrte Vorgabe mit
begruendetem Vorbehalt ist mehr wert als eine ausgefuehrte Vorgabe ohne.
Quelle: `nachschlag\BERICHT-2026-08-25.md`, „Was mir aufgefallen ist", Punkt 4.

**Fall 3 — das Verbot eigener `ffprobe`-Aufrufe gegen einen Pruefstein, der eine
Messung verlangt.** Ein Auftrag untersagte eigene `ffprobe`-Aufrufe und verlangte
zugleich einen Pruefstein, der ohne Messung nicht zu erfuellen war. **Dieser
dritte Fall ist in den vier gelesenen Berichten NICHT belegt** — er stammt allein
aus dem Auftragstext dieser Uebergabe. Die beiden Faelle oben sind belegt, dieser
ist es nicht.

### 1.2 Die haeufigste Fehlerquelle ist der Orchestrator, nicht die Ausfuehrung

**Der wichtigste Satz dieses Abschnitts.** In dieser Sitzung hat der Orchestrator
mehrfach Auftragsteile geschrieben, die einander widersprachen:

- TEIL 3 gegen TEIL 5 im Auftrag `zusammenfuehrung-schont-urteile` (63 gegen 69).
- TEIL 3 gegen TEIL 5 im Auftrag `modellfahne-und-nachschlagzeile` vom 25.8.
  („keine bestehende Testfunktion aendern" gegen eine geforderte
  Ausgabeaenderung, die zwei Zeilenvergleiche trifft).
- TEIL 4b/4d gegen TEIL 5 im Auftrag `nachschlag` — vier bestehende
  Testfunktionen pruefen genau das Verhalten, das der Auftrag abschaffen laesst.
- TEIL 1 gegen TEIL 4 im Auftrag `zusammenfuehrung-schont-urteile` — die
  Regelaenderung macht eine bestehende Testfunktion notwendigerweise falsch.

In **keinem** dieser Faelle lag der Fehler beim ausfuehrenden Fenster. In jedem
lag er in zwei Auftragsteilen, die derselbe Orchestrator kurz nacheinander
geschrieben hat, ohne sie gegeneinander zu halten.

**Vier Fragen vor dem Absenden eines Auftrags:**

1. Nennt der Auftrag eine Zahl, die aus dem **heutigen** Bestand stammt, waehrend
   ein anderer Teil den Bestand aendert? Dann ist die Zahl von gestern.
2. Verlangt ein Teil eine Ausgabeaenderung, waehrend ein anderer Teil verbietet,
   Tests anzufassen? Zeilenvergleiche in Tests treffen Ausgabezeilen.
3. Verbietet ein Teil ein Werkzeug, das ein Pruefstein braucht?
4. Verlangt ein Teil das Entfernen eines Verhaltens, das ein Test festhaelt?

Ist eine dieser Fragen mit Ja zu beantworten, gehoert der Widerspruch in den
Auftrag hinein — mit der Ansage, welche Seite gewinnt. Sonst kostet er das
ausfuehrende Fenster einen Umlauf, und der Bericht traegt einen
ANGEHALTEN-Abschnitt, den niemand gebraucht haette.

---

## 2 — Stand der Linie

Der Stand der Abendfassung vom 25.8. (Abschnitt 2: zwei Befehle, Urteilstor)
**gilt fort, mit einer Erweiterung: die Kette hat jetzt SIEBEN Stufen.** Alles
uebrige — nicht verdrahtete Endcard, fehlendes Kandidatensuchmodul, das
strukturell unumgehbare Urteilstor — gilt unveraendert.

### Die vier Commits seit `4b9e289`

| Commit | Botschaft |
|---|---|
| `5690272` | Shorts: Nachschlaglauf und Zusammenfuehrung zweier Laeufe |
| `0f007cf` | Shorts: Zusammenfuehrung laesst beurteilte Kandidaten unangetastet |
| `970a151` | Shorts: Buendelung der Kandidaten nach Thema |
| `d8c6c61` | Shorts: Urteilsseite zeigt Gruppen, Verweise beim Zusammenfuehren |

Quelle: der Auftragstext dieser Uebergabe unter GEGEBEN, gegen `git log` dieses
Auftrags gehalten. Dazwischen liegt ausserdem `3f3da76` („Shorts: Urteile,
Kandidaten und Trefferquote des Laufs vom 25.8.") — ein reiner Artefakt-Commit
aus demselben Auftrag wie `5690272`.

### Befehl 1 — `kette`, sieben Stufen

```powershell
python -m matrix_auto_cutter.shorts.kette [--aufnahme NAME] [--modell NAME]
       [--modell-buendelung NAME] [--lauf N] [--neu] [--neu-ab STUFE]
       [--bis STUFE] [--trocken] [--wurzel PFAD]
```

| # | Stufe | Ausgabe | Wer arbeitet |
|---|---|---|---|
| 1 | `auftrag` | `shorts-job.json` | eigenes Modul |
| 2 | `avatar_cut` | `avatar-cut.mp4` | eigenes Modul |
| 3 | `transcript` | `transkript-rendered.json` | eigenes Modul |
| 4 | `wortliste` | `wortliste.json` | eigenes Modul |
| 5 | `zerlegung` | `kandidaten-lauf<N>.json` | **Modell** ueber `claude -p` |
| 6 | `zusammenfuehrung` | `kandidaten.json` | Rechnung in `auswahl.py` |
| 7 | `buendelung` | `buendel.json` | **Modell** ueber `claude -p` |

Drei Aenderungen gegenueber dem 25.8. abends:

- **Stufe 5 haengt jetzt an `--lauf N`.** Der Nachschlag ueberschreibt den ersten
  Lauf nicht mehr; er schreibt `kandidaten-lauf2.json` daneben. `ZERLEGUNG_LAUF`
  ist nur noch der Vorgabewert. Der Stufen-*Name* bleibt `zerlegung`, damit
  `--neu-ab zerlegung` unabhaengig von der Nummer trifft.
- **Stufe 6 fuehrt wirklich zusammen.** `CODE_ZUSAMMENFUEHRUNG_FEHLT = 6` ist
  ersatzlos verschwunden. Zwei Laufdateien ergeben jetzt einen vereinigten
  Kandidatensatz — nach einer Regel, die keinen beurteilten Kandidaten anfasst
  (Abschnitt 5.1).
- **Stufe 7 ist neu.** Sie buendelt die Kandidaten thematisch, damit der Nutzer je
  Gruppe *eine* Entscheidung faellt statt je Kandidat. Eigene Fahne
  `--modell-buendelung` (Vorgabe `opus`), eigener Auftragstext
  `docs\repeat\BUENDELUNG-AUFTRAGSTEXT.md`, eigener Rueckgabecode 10.

Rueckgabecodes von `kette`: **0** Erfolg, **2** keine Aufnahme, **5** Stufe
gescheitert oder unbekannt, **9** Urteile wuerden umgedeutet, **10**
`buendel.json` weicht von `kandidaten.json` ab. Die **6** gibt es nicht mehr.

### Befehl 2 — `urteilslauf`, unveraendert sieben Schritte

Die Befehlszeile und die sieben Schritte stehen unveraendert (Abschnitt 2 der
Abendfassung, Abschnitt 4 des Bestands vom 25.8. abends). Geaendert hat sich nur
der Text der Nachschlagzeile in Schritt 4: sie nennt jetzt `--lauf 2`, und die
alte Warnung ueber Code 6 und das Ueberschreiben ist ersatzlos gestrichen. An
ihrer Stelle steht die Zusage, dass vorhandene Urteile gueltig bleiben.

### Die Urteilsseite zeigt Gruppen

Liegt eine `buendel.json` da und besteht sie die Pruefung, zeigt die Urteilsseite
**Gruppen statt einer flachen Liste**: je Gruppe der empfohlene Kandidat als volle
Karte, die uebrigen eingeklappt hinter „N weitere Fassungen" — mit sichtbaren
Titeln und Dauern, damit der Nutzer sieht, *was* er uebergeht, ohne aufzuklappen.
Je Kandidat einer Mehrfachgruppe gibt es den Knopf **„Diese Fassung nehmen"**: er
setzt fuer diesen `ja` und fuer die uebrigen der Gruppe `nein`, je Kandidat ein
eigener Schreibvorgang, jedes Urteil danach einzeln aenderbar.

**Die Urteilsdatei bleibt dabei kandidatenbezogen.** Eine Gruppenentscheidung
erzeugt mehrere Urteile, kein Gruppenurteil. Begruendung: `buendel.json` ist
Anzeige, nicht Wahrheit. Geht sie verloren, sind die Urteile vollstaendig und
gueltig, denn sie haengen am `index` des Kandidaten und an sonst nichts. Ein
Gruppenurteil haenge dagegen an einer Gruppennummer, die eine zweite Buendelung
anders vergeben darf.

**Bei jedem Zweifel faellt die Seite auf die flache Liste zurueck** und sagt im
Kopf, warum — fehlende, unlesbare oder nicht passende `buendel.json`. Nicht
teilweise gruppiert: eine Buendelung, die einen Index auslaesst, versteckte genau
den Kandidaten, ueber den nie entschieden wird, und zwar unsichtbar.

### Was zwischen den beiden Befehlen noch von Hand geschieht

Unveraendert: nichts ausser dem Urteilen selbst und dem Anstossen der beiden
Befehle.

---

## 3 — Die beiden Betriebslaeufe vom 26./27.8.

Beide an der Aufnahme `2026-08-25 15-14-00`, 1.073.716 ms Material (rund
17,9 min). Die Laufzeiten stammen aus
`artefakte\repeat\shorts\2026-08-25 15-14-00\kette.json`, in diesem Auftrag
gelesen; die Datei liegt unter `artefakte\` und ist `.gitignore`-ausgeschlossen —
die Zahlen stehen sonst nirgends im Repository.

### 3.1 Der Nachschlaglauf mit Opus

| Groesse | Wert | Herkunft |
|---|---|---|
| Laufzeit Stufe 5, Lauf 2 | **1595,8 s = 26 min 35,8 s** | `kette.json`, `stufen.zerlegung.laeufe["2"].dauer_s` |
| Begonnen / beendet | 2026-08-26 11:54:18 / 12:20:54 UTC | ebenda |
| Modell | `opus` | ebenda, Feld `modell` |
| Kandidaten | **65** | in diesem Auftrag aus `kandidaten-lauf2.json` nachgezaehlt |
| Median der Dauer | **10,68 s** | ebenda, nachgerechnet |
| Im Zielbereich 8–15 s | **54 von 65 = 83,1 %** | ebenda, nachgerechnet |

Zum Vergleich der Lauf 1 mit Sonnet an derselben Aufnahme (Zahlen in diesem
Auftrag aus `kandidaten-lauf1.json` nachgerechnet, deckungsgleich mit
Abschnitt 3.4 der Abendfassung):

| Groesse | Lauf 1 (sonnet) | Lauf 2 (opus) |
|---|---|---|
| Kandidaten | 39 | **65** |
| Median | 12,82 s | **10,68 s** |
| Im Zielbereich | 29 = **74,4 %** | 54 = **83,1 %** |

**Opus findet mehr und trifft die Laengenvorgabe besser.** Das ist der Befund,
auf den sich Entscheidung 4.2 stuetzt.

**41 der 65 Opus-Kandidaten galten als bereits bekannt.** Die Zahl steht in keiner
Datei; sie folgt aus der Zusammenfuehrung: 65 minus 24 neu angehaengte = 41
Kandidaten, die `gleicher_kandidat` einem Eintrag des ersten Laufs zuordnete. Von
diesen 41 waren 6 laenger als ihr Gegenstueck und wurden als eigene Kandidaten
64–69 angehaengt; die uebrigen 35 fielen weg. In diesem Auftrag an
`kandidaten.json` nachgeprueft: 39 aus Lauf 1, 30 aus Lauf 2, Summe 69.

**Nur rund ein Drittel dessen, was Opus sah, war neu.** Das ist genau die Aussage,
die `zerlegung_laeuft_zweimal` in der Kriteriendatei erwartet: der Gewinn des
zweiten Laufs liegt darin, dass er etwas *anderes* sieht.

### 3.2 Der Buendelungslauf mit Opus

| Groesse | Wert | Herkunft |
|---|---|---|
| Laufzeit Stufe 7 | **1036,3 s = 17 min 16,3 s** | `kette.json`, `stufen.buendelung.dauer_s` |
| Begonnen / beendet | 2026-08-26 23:26:30 / 23:43:47 UTC | ebenda |
| Modell | `opus` | ebenda; und `buendel.json`, Wurzelfeld `modell` |
| Rueckgabecode | **0** | `buendelung\BERICHT-2026-08-26.md`, Abschnitt 3 |
| `pruefe_buendel` | **null Abweichungen** | ebenda |
| Eintraege | **69**, Indizes 1–69 lueckenlos | `buendel.json`, in diesem Auftrag gelesen |
| Gruppen | **47** | ebenda, Wurzelfeld `gruppen_gesamt` |
| Empfehlungen | 47 — genau eine je Gruppe | `buendelung\BERICHT-2026-08-26.md` |
| Gruppen mit mehr als einem Kandidaten | **18** (40 der 69 Kandidaten) | ebenda |
| Groesste Gruppe | **3** Kandidaten; vier Gruppen dieser Groesse | ebenda |

Gruppengroessen: 29 Einzelgruppen, 14 Zweier-, 4 Dreiergruppen.

Verteilung ueber `projekt` (Quelle: `buendelung\BERICHT-2026-08-26.md`,
Abschnitt 3):

| Projekt | Kandidaten | Gruppen |
|---|---|---|
| Bitcoin | 26 | 18 |
| Hyperliquid | 19 | 13 |
| Marktpsychologie | 11 | 7 |
| XRP | 7 | 3 |
| Kanal | 6 | 6 |

**Der eigentliche Nachweis:** die drei Doppelpaare, die der Nutzer am 26.8.
unwissentlich zweimal angenommen hat — 64/10, 67/18, 66/16 — stehen je zusammen
in einer Gruppe (7, 23, 42), und in allen dreien ist die laengere Fassung die
empfohlene. Haette er die Buendelung vor sich gehabt, waeren aus drei
Doppelentscheidungen drei einzelne geworden.

Zusaetzlich unabhaengig nachgerechnet: **kein einziges zeitlich ueberlappendes
Kandidatenpaar steht in verschiedenen Gruppen** — 0 von 2346 geprueften Paaren.
Die schaerfste Regel des Auftragstextes haelt ohne Ausnahme.

### 3.3 Was die Buendelung bringt und was nicht

Sie sortiert nicht nach Projekt, sondern nach Aussage: 26 Bitcoin-Kandidaten
stehen in 18 Gruppen, nicht in einer. Die 29 Einzelgruppen sind kein Versagen,
sondern der erwartete Normalfall — die meisten Kandidaten sagen etwas Eigenes.

**Rechnerisch faellt die Zahl der Entscheidungen von 69 auf 47 — ein Drittel
weniger.** Gemessen am Ziel von 4 bis 10 veroeffentlichten Shorts je Aufnahme
(Abschnitt 4.3) ist das noch immer eine Groessenordnung zu viel. Daraus folgt das
naechste Vorhaben, die Vorauswahl (Abschnitt 6).

### 3.4 Der Urteilsstand der Aufnahme

Aus dem Nachweislauf in `urteilsseite-gruppiert\BERICHT-2026-08-27.md`,
Abschnitt 6, woertlich:

    69 von 69 beurteilt - 44 ja, 25 nein, 0 offen - Quote 64 %

Schritt 2 desselben Laufs meldet **0 Abweichungen** — jedes der 69 Urteile zeigt
auf einen Kandidaten mit demselben `start_ms`, `end_ms` und `titel`, den es beim
Faellen meinte. Das ist der Beleg, dass die Zusammenfuehrung vom 26.8. keine
Urteilszeit vernichtet hat.

**Achtung, dieser Urteilsstand steht NICHT in `trefferquote.json`** — siehe
Abschnitt 7.3.

---

## 4 — Entscheidungen samt Begruendung

Die Entscheidungen 4.1 bis 4.4 der Nachmittagsfassung und 4.1 bis 4.3 der
Abendfassung gelten weiter. Neu sind sechs Entscheidungen des Auftraggebers vom
26. und 27.8. **Herkunft aller sechs: der Auftragstext dieser Uebergabe unter
GEGEBEN. Im Repository sind sie nur mittelbar belegt** — dort, wo der Code sie
bereits umsetzt; das ist bei 4.1 und 4.2 der Fall, bei 4.3 bis 4.6 nicht.

### 4.1 Bauauftraege laufen grundsaetzlich mit Opus mittlerer Denktiefe

Sonnet bleibt nur noch fuer reine Commit-Auftraege. Das verschaerft Abschnitt 1.1
der Abendfassung, der Sonnet noch fuer Commit- **und** Doku-Auftraege offenliess.

**Begruendung:** das Gebaute soll lange halten. Ein Modul, das ein halbes Jahr im
Betrieb steht, rechtfertigt die teurere Denktiefe. Hinzugekommen ist seit dem
25.8. ein Erfahrungsgrund: die vier Auftraege des 26./27.8. sind mit Opus
gefahren und haben in jedem Fall einen Widerspruch in der eigenen Vorgabe gefunden
und benannt (Abschnitt 1.2), statt ihn auszufuehren.

### 4.2 Zerlegung und Buendelung fahren beide mit Opus als Vorgabe

Belegt im Code: `ZERLEGUNG_MODELL = "opus"` (bis zum 26.8. `"sonnet"`) und
`BUENDELUNG_MODELL = "opus"`. Beide Fahnen bleiben ueberschreibbar
(`--modell`, `--modell-buendelung`).

**Begruendung:** der Nachschlaglauf mit Opus brachte 65 statt 39 Kandidaten bei
besserem Zielbereichsanteil (Abschnitt 3.1) — er brachte das Material, um das es
geht. Diese Entscheidung ist damit die einzige der sechs, die auf einer Messung
ruht und nicht allein auf einer Setzung.

### 4.3 Ziel sind 4 bis 10 veroeffentlichte Shorts je Aufnahme

Ueber die auf die Aufnahme folgenden 24 Stunden verteilt eingeplant.

**Begruendung:** das ist die Zahl, die ein Kanal taeglich traegt, ohne den eigenen
Hauptinhalt zu verdraengen. Sie ist zugleich der Massstab, an dem alle
Zwischenstufen zu messen sind — 39 Kandidaten, 69 Kandidaten und 47 Gruppen sind
alle *zu viele Entscheidungen fuer zu wenige Ergebnisse*. Erst diese Zahl macht
die Vorauswahl (Abschnitt 6) zum naechsten Schritt und nicht zur Verzierung.

### 4.4 Shorts sind nach 48 Stunden ab AUFNAHMEZEIT wertlos

Nicht ab Schnitt, nicht ab Veroeffentlichung des Hauptvideos — **ab
Aufnahmezeit**.

**Begruendung:** der Inhalt ist Marktkommentar. Ein Kursziel, das vor drei Tagen
genannt wurde, ist heute entweder eingetroffen oder widerlegt; in beiden Faellen
traegt der Ausschnitt nicht mehr. Die Frist laeuft deshalb ab dem Zeitpunkt, an
dem der Sprecher etwas gesagt hat, und nicht ab dem Zeitpunkt, an dem die
Maschine damit fertig wurde.

**Das ist die folgenreichste der sechs Entscheidungen**, weil sie zum ersten Mal
eine harte Frist ueber die ganze Linie legt. Die Maschinenzeit bis zu den
Kandidaten betrug am 25.8. rund 57 Minuten (Abschnitt 3.1 der Abendfassung), der
Nachschlag kostet weitere 27, die Buendelung weitere 17 — zusammen mit Bau und
Urteilszeit ist ein erheblicher Teil der Frist verbraucht, bevor der erste Short
hochgeht. Wieviel genau, ist **nicht gemessen**: ein vollstaendiger Durchlauf
unter der neuen Frist ist noch nicht gefahren worden.

### 4.5 Die Buendelung soll kuenftig die besten 15 Gruppen vorauswaehlen

**Begruendung:** siehe 4.3. Bei 4 bis 10 Zielshorts sind 47 Gruppenentscheidungen
zu viel. Fuenfzehn ist die Zahl, aus der sich zehn auswaehlen lassen, ohne dass
die Auswahl selbst wieder Arbeit wird.

**Achtung, das steht im Widerspruch zum heutigen Auftragstext der Buendelung.**
`BUENDELUNG-AUFTRAGSTEXT.md` sagt ausdruecklich: „**ZWISCHEN Gruppen wird NICHT
gerangt.** Du sagst nicht, welches Thema wichtiger ist als ein anderes. Welches
Thema es wert ist, veroeffentlicht zu werden, entscheidet der Nutzer." Eine
Vorauswahl der besten 15 ist genau ein Rang zwischen Gruppen. **Beide Fassungen
stehen hier.** Der Auftrag, der die Vorauswahl baut, muss diesen Satz im
Auftragstext bewusst ersetzen und die Ersetzung begruenden — er darf ihn nicht
uebersehen.

### 4.6 Der Rhythmus des Tages

Aufnahme morgens → Schnitt → Hauptvideo hoch → danach die Shorts, in derselben
48-Stunden-Frist. Die Shorts sind also der **zweite** Durchgang durch dasselbe
Material, nicht der erste. Daraus folgt: die Shorts-Linie darf den Schnitt des
Hauptvideos nicht blockieren und muss mit dem gerenderten Ergebnis auskommen —
was sie tut, denn sie arbeitet auf der gerenderten Achse.

---

## 5 — Betriebsfallen

Alle Fallen der Nachmittagsfassung (5.1, 5.2) und der Abendfassung (5.1 bis 5.5)
**gelten weiter** — mit einer Praezisierung: 5.1 der Abendfassung („die
Schlusszeile von `kette` meldet Vollendung auch bei `--bis N`") ist unveraendert
offen, aber die Stufenzahl ist jetzt sieben. Fuenf Fallen sind dazugekommen.

### 5.1 Ein Index ist ein Versprechen — die Zusammenfuehrung darf ihn nie umdeuten

**Die wichtigste Falle dieser Uebergabe, und die einzige, die schon Schaden
angerichtet hat.**

Bis zum 26.8. schrieb `auswahl.fuehre_zusammen` eine laengere Fassung aus einem
spaeteren Lauf **in den vorhandenen Eintrag hinein**: `start_ms`, `end_ms`,
`titel`, `begruendung` und `dauer_ms` wurden ersetzt, der Index blieb. Das machte
am 26.8. **sechs beurteilte Indizes ungueltig** — 10, 14, 16, 18, 33 und 34
meinten danach einen anderen Ausschnitt als das Urteil auf ihnen.

Die Begruendung, warum das nie wieder geschehen darf, steht woertlich im
Docstring von `auswahl.fuehre_zusammen`: Urteile haengen am `index` und an sonst
nichts. Wer neu nummeriert oder Inhalt austauscht, laesst jedes vorhandene Urteil
auf einen fremden Kandidaten zeigen — **und zwar lautlos, denn eine Zahl passt
immer auf eine Zahl.** Urteilszeit ist das einzige Artefakt dieser Kette, das sich
nicht neu erzeugen laesst: Aufnahme, Transkript, Wortliste, Zerlegung und Bau
laufen jederzeit wieder, ein gefaelltes Urteil nicht.

Die berichtigte Regel: der Satz mit der kleinsten Laufnummer ist der Grundsatz und
bleibt unangetastet; eine laengere Fassung wird **hinten angehaengt** mit eigenem
Index und dem Feld `laengere_fassung_von`; beide stehen nebeneinander, der Nutzer
entscheidet.

**Die Pruefung hat gegriffen, nichts ging verloren.** `kette.fuehre_zusammen` ruft
vor dem Schreiben `_pruefe_urteile_bleiben_gueltig`; die Funktion vergleicht die
alte Datei je Index gegen die neu gerechnete und haelt mit Code 9 an, sobald ein
Index umgedeutet wuerde. Am echten Bestand liefert `veraenderte_indizes(alt, neu)`
genau `[10, 14, 16, 18, 33, 34]` — exakt die sechs Indizes, an denen der Schaden
entstand.

### 5.2 `currentCardIndex` las die Schleifenzahl statt der Position

In der flachen Liste fielen beide zusammen: die dritte Karte im Dokument war der
dritte Eintrag in `ENTRIES`. **Im Gruppenmodus nicht mehr** — dort bestimmen die
Gruppen die Reihenfolge, und eingeklappte Karten stehen dazwischen. Eine
Tastatureingabe haette das Urteil auf einen **fremden** Kandidaten gesetzt.

Behoben ueber `card.dataset.pos`: jede Karte traegt ihre Position, und die
Funktion liest sie von dort statt aus der Schleifenzahl.

**Die Lehre, die ueber diesen einen Fehler hinausgeht:** eine Anzeige, die die
Reihenfolge aendert, bricht jede Stelle, die Position und Identitaet
gleichsetzt. Wer die Urteilsseite weiter umbaut — Vorauswahl, Filter, Sortierung
— sucht zuerst nach genau diesem Muster.

### 5.3 `fuehre_zusammen` schrieb `enthaelt` unveraendert aus der Laufdatei fort

Ein Kandidat aus Lauf 2 traegt in `enthaelt` die Indizes **seines** Laufs. Die
Zusammenfuehrung gibt ihm einen neuen Index — seine Verweise zeigten danach auf
beliebige Kandidaten des Grundsatzes.

Am echten Bestand betrifft das genau einen Kandidaten:

| Kandidat | heute | richtig waere |
|---|---|---|
| **67** (602.500–616.420 ms) | `enthaelt: [36]` → 966.380–981.560 ms, **sechs Minuten entfernt** | `enthaelt: [18]` → 607.930–617.490 ms |

Das `laengere_fassung_von` desselben Kandidaten zeigt richtig auf 18. Kandidat 67
ist zugleich der einzige mit nicht-leerem `enthaelt` im ganzen Satz — in diesem
Auftrag an `kandidaten.json` nachgeprueft.

Behoben ueber `_bilde_verweise_ab`: die Verweise werden je Lauf auf die neue
Nummerierung abgebildet, die Abbildung entsteht waehrend des ganzen Laufs und
wird erst danach angewandt (ein `enthaelt` darf auf einen Kandidaten zeigen, der
weiter hinten in derselben Laufdatei steht). Was kein Ziel hat, faellt **weg**
statt falsch stehenzubleiben, und die Zahl steht als Wurzelfeld
`verworfene_verweise` in der Ausgabe.

**Die bestehende `kandidaten.json` wurde bewusst NICHT neu erzeugt** — an ihren
Indizes haengen 69 Urteile. Der falsche Verweis ist heute folgenlos
(`parse_candidates` laeuft durch, weil 36 ein existierender Index ist) und trifft
erst den naechsten Bestand, bei dem ein Lauf-2-Verweis ueber die Zahl der
Kandidaten hinausgeht. **Die Datei im Auftragsordner traegt den Fehler also
weiterhin**, und sie traegt aus demselben Grund auch kein Wurzelfeld
`verworfene_verweise` — in diesem Auftrag nachgeprueft.

### 5.4 Der Ueberspring-Zweig laesst `dauer_s` stehen — und mischt jetzt zwei Laeufe

Die Falle 5.2 der Abendfassung gilt unveraendert: eine uebersprungene Stufe
behaelt `dauer_s`, `begonnen_am` und `beendet_am` aus einem frueheren Anlauf und
traegt zugleich `"meldung": "uebersprungen, Ausgabe lag bereits vor"`.

**Seit `--lauf N` hat das eine zweite Wirkung.** Im heutigen `kette.json` steht
unter `stufen.zerlegung`:

```json
{ "ausgabe": "...\\kandidaten-lauf1.json",
  "lauf": 2, "modell": "opus", "dauer_s": 1595.8,
  "meldung": "uebersprungen, Ausgabe lag bereits vor", "status": "fertig" }
```

Die `ausgabe` stammt aus einem spaeteren Lauf, der Lauf 1 uebersprang; `lauf`,
`modell` und `dauer_s` stammen aus Lauf 2. **Der Zusammenfassungseintrag ist also
in sich widerspruechlich.** Wer wissen will, was ein *bestimmter* Lauf gekostet
hat, liest ausschliesslich `stufen.zerlegung.laeufe["<N>"]`.

Und auch dort ist Vorsicht geboten: `laeufe["1"]` traegt `dauer_s: null`,
`begonnen_am: null`, `beendet_am: null` — **die Laufzeit des ersten
Zerlegungslaufs ist verloren.** Sie steht nur noch in Abschnitt 3.1 der
Abendfassung (1553,5 s), weil sie dort festgehalten wurde, bevor der Nachschlag
den Eintrag ueberschrieb. Genau dafuer sind diese Uebergaben da.

### 5.5 Ein Container und sein Kind aus DEMSELBEN Lauf gelten als derselbe Kandidat

`gleicher_kandidat` fragt nur nach Ueberlappung — mehr als die Haelfte der
kuerzeren Dauer. Ein verschachtelter Kandidat liegt vollstaendig in seinem
Container, ueberlappt ihn also zu 100 % der eigenen Dauer und gilt als derselbe.

Das ist keine Schwaeche der Regel: sie ist fuer den Vergleich **verschiedener**
Laeufe geschrieben, und `fuehre_zusammen` wendet sie auch nur so an — der
Grundsatz wird nie gegen sich selbst geprueft. Aber wer Testfaelle baut oder ueber
die Regel nachdenkt, stolpert darueber: beim Schreiben der Tests zu 5.3 wurden
zwei Szenarien unbrauchbar, weil das gewaehlte Verweisziel mit seinem Container
verschmolz und der Verweis zur Selbstreferenz geworden waere.

---

## 6 — Der Weg weiter

Die Vorhaben (1) und (2) aus Abschnitt 6 der Abendfassung sind **erledigt**:
`--lauf N` und die Zusammenfuehrung stehen und sind im Betrieb gefahren worden.
Was bleibt, sind fuenf Vorhaben in dieser Reihenfolge.

### (1) Vorauswahl und Verfall — das dringendste

**Zwei Dinge, die zusammengehoeren, weil beide dieselbe Frage beantworten: was
soll der Nutzer ueberhaupt zu sehen bekommen?**

**Die Vorauswahl.** Die Buendelung rankt die Gruppen und markiert die besten 15.
Die Urteilsseite zeigt nur diese; der Rest ist aufklappbar, nicht verschwunden —
dasselbe Muster wie bei den „N weiteren Fassungen" innerhalb einer Gruppe, und
aus demselben Grund: was verborgen ist, muss sichtbar bleiben.

**Der Verfall.** Aufnahmen aelter als 48 Stunden **ab Aufnahmezeit** werden nicht
mehr zum Urteilen angeboten; offene Urteile gelten als `verfallen`.

**Begruendung:** bei 4 bis 10 Zielshorts (4.3) sind 47 Gruppenentscheidungen zu
viel — die Buendelung hat 69 auf 47 gedrueckt, das reicht nicht. Und was liegen
bleibt, ist nach zwei Tagen wertlos (4.4); eine Aufnahme, die noch zum Urteilen
angeboten wird, obwohl ihre Frist abgelaufen ist, kostet Urteilszeit fuer nichts.

**Vor dem Bau zu klaeren:**

- Der Satz „ZWISCHEN Gruppen wird NICHT gerangt" in
  `BUENDELUNG-AUFTRAGSTEXT.md` muss bewusst ersetzt werden — siehe 4.5.
- Woher kommt die Aufnahmezeit? Aus dem Ordnernamen (`2026-08-25 15-14-00`) oder
  aus einem Feld in `shorts-job.json`? **In diesem Auftrag nicht geprueft, nicht
  belegt.** Der Ordnername traegt die Zeit im Klartext und ist die naheliegende
  Quelle, aber ob er verlaesslich der Aufnahmezeit entspricht, ist offen.
- `verfallen` waere ein neuer Urteilswert neben `ja`, `nein`, `spaeter`. Das
  Schema der Urteilsdatei zu erweitern ist der einzige Weg, der bisher **nie**
  gegangen wurde — Abschnitt 4 des Berichts `urteilsseite-gruppiert` haelt
  ausdruecklich fest, dass die sieben Felder unveraendert blieben. Hier gehoert
  eine bewusste Entscheidung hin, keine beilaeufige.

### (2) Die Uploadstufe — erst nach einer Klaerung, die kein Code beschleunigt

**Baue das nicht, bevor geklaert ist, ob Scope und Audit stehen.** Ein
Google-Audit dauert Wochen und laesst sich durch keine Zeile Code abkuerzen. Wer
zuerst die Stufe baut und dann feststellt, dass `youtube.upload` nicht
freigegeben ist, hat die Reihenfolge verkehrt herum gewaehlt.

**Recherchiert am 27.8.2026, Herkunft: der Auftragstext dieser Uebergabe. Im
Repository nicht belegt, in diesem Auftrag nicht nachgeprueft** — kein
Netzzugriff, kein Zugriff auf das Google-Projekt des Nutzers:

- **Standardzuteilung:** 100 `videos.insert`-Aufrufe pro Tag in einem **eigenen**
  Kontingent, 100 `search.list`, 10.000 Einheiten fuer alles uebrige. Am Projekt
  des Nutzers bestaetigt: „Video Uploads per day" steht auf 100, Nutzung 0.
- **Aeltere Anleitungen nennen 1600 Einheiten je Upload aus dem gemeinsamen
  Topf.** Das war frueher so und ist es nicht mehr. Wer auf eine Quelle stoesst,
  die diese Zahl nennt, hat eine veraltete Quelle vor sich — nicht einen
  Widerspruch zur obigen Angabe.
- Der OAuth-Zustimmungsbildschirm des Nutzerprojekts steht auf **„In
  Produktion"**. Ob der Scope `youtube.upload` freigegeben und das Audit
  bestanden ist, ist **NICHT geklaert** und die eigentliche offene Frage.
- Shorts brauchen **9:16** und **`#Shorts` im Titel**.

Bei 4 bis 10 Shorts je Aufnahme ist das Kontingent von 100 Uploads pro Tag kein
Engpass — es traegt zehn Aufnahmen am Tag. **Der Engpass ist die Freigabe, nicht
die Zuteilung.**

### (3) Der Wecker

Windows-Aufgabenplanung ruft `claude -p --permission-mode acceptEdits`. Die Fallen
stehen vollstaendig in Abschnitt 6, Schritt 4 der Nachmittagsfassung
(Arbeitsverzeichnis setzen, `TREFFERQUOTE_PFAD` ist relativ, `.claude\` ist leer,
kein Zeitplaner in Claude Code 2.1.220). Beim Aufsetzen erneut pruefen, ob eine
neuere Fassung einen eingebauten Zeitplaner mitbringt — fuer neuere Fassungen
**nicht belegt**, in diesem Auftrag nicht geprueft.

Mit Entscheidung 4.4 hat der Wecker einen zweiten Zweck bekommen: er muss auch
die **Frist** ueberwachen, nicht nur den Start anstossen.

### (4) Der Aufraeumer fuer `F:`

Unveraendert offen seit dem 25.8. nachmittags. Je Kandidat stehen neben
`short.mp4` die Zwischenstufen `ausschnitt.mp4`, `leinwand.mp4` und
`mit-avatar.mp4` samt Seitendateien; sie machen rund 70 % des Zielordners aus, bei
einer Groessenordnung von 252 MB je Aufnahme (Messung 21.8.). Mit jedem
Betriebslauf kommt ein Zielordner dazu; die heutige Gesamtgroesse ist **nicht
belegt** — `F:` war in diesem Auftrag gesperrt.

### (5) Eine Oberflaeche fuer die Shorts-Linie

Unveraendert: **noch nicht erkundet**, braucht einen eigenen, rein lesenden
Erkundungsauftrag ueber das bestehende Interface des Cutters, bevor irgendetwas
entworfen wird. Was es kann und was sich uebernehmen laesst, ist **nicht belegt**.

---

## 7 — Offene Punkte

Alle offenen Punkte der Nachmittags- und der Abendfassung **bleiben offen**,
soweit hier nichts anderes steht. Erledigt sind daraus: `--lauf N` und der
fehlende Auftragstext der Zusammenfuehrung (die Regel liegt jetzt in
`auswahl.fuehre_zusammen`; ein eigener Auftragstext wird nicht mehr gebraucht,
weil kein Modell die Zusammenfuehrung faehrt). Neu oder veraendert sind die
folgenden.

### 7.1 Zwei unversionierte Dateien mit `unbekannt` im Namen

```
?? "labels/repeat/kandidaten-2026-08-25 15-14-00-lauf1-unbekannt.json"
?? "labels/repeat/urteile-2026-08-25 15-14-00-lauf1-unbekannt.json"
```

Sie stammen aus einem Lauf des Nutzers am 26.8. (Aenderungszeiten 15:05 und
21:57) und **nicht** aus einem der vier Bauauftraege — beide Berichte, die sie
antrafen, halten das ausdruecklich fest.

**Die Ursache ist in diesem Auftrag geklaert, und der Auftragstext dieser
Uebergabe beschreibt sie ungenau.** Er sagt, „ein `auswahl`-Lauf" habe „die
Wurzelfelder nicht gefunden". Belegt ist:

- Geschrieben hat sie **`urteilslauf` Schritt 6** (`sichere_urteile`), nicht
  `auswahl`.
- Es fehlte **genau ein** Wurzelfeld, nicht „die Wurzelfelder":
  `sicherungsnamen` liest `video_name`, `lauf` und `modell` aus der Wurzel von
  `kandidaten.json`; die ersten beiden stehen da (daher der richtige Aufnahmename
  und `lauf1`), **`modell` fehlt.**
- Es fehlt, weil `auswahl.fuehre_zusammen` das Wurzelfeld `modell` beim
  Zusammenfuehren bewusst **entfernt** und durch `modelle` ersetzt
  (`{"1": "sonnet", "2": "opus"}`) — ein zusammengefuehrter Satz stammt aus
  mehreren Modellen und kann kein einzelnes nennen. In diesem Auftrag an
  `kandidaten.json` nachgeprueft: die Wurzelfelder sind `achse`,
  `kriterien_fassung`, `laeufe`, `lauf`, `modelle`, `video_dauer_ms`,
  `video_name`, `zusammengefuehrt_am` — kein `modell`.

**Das ist also kein Fehlbedienen, sondern eine Luecke zwischen zwei Aenderungen:**
seit der Zusammenfuehrung gibt es kein einzelnes `modell` mehr, und
`sicherungsnamen` weiss davon nichts. Der Namensteil wird von jetzt an bei jeder
zusammengefuehrten Aufnahme `unbekannt` lauten.

**Zu tun:** `sicherungsnamen` auf `modelle` ausweiten (etwa `sonnet+opus`) oder
den Namensteil bei mehreren Laeufen bewusst anders bilden. Die beiden vorhandenen
Dateien **nicht anfassen** — sie sind Belege eines Nutzerlaufs. Ob sie committet
werden sollen, ist eine Entscheidung des Auftraggebers und **nicht getroffen**.

### 7.2 `kriterien_fassung` — jetzt drei Schreibweisen, nicht zwei

Der Auftragstext dieser Uebergabe nennt zwei. **Belegt sind drei.**

| Ort | Wert |
|---|---|
| `trefferquote.json`, Eintrag 1 (21.8.) | `"Fassung 0.8 (24. August 2026)"` |
| `trefferquote.json`, Eintrag 2 (25.8.) | `"0.8"` |
| `kandidaten-lauf1.json` (25.8., sonnet) | `"0.8"` |
| `kandidaten-lauf2.json` (26.8., opus) | `"Fassung 0.8 (24. August 2026)"` |
| Kopfzeile der Kriteriendatei | `# shorts-kriterien.yaml — Fassung 0.8 (24. August 2026)` |
| `ZERLEGUNG-AUFTRAGSTEXT.md` verlangt seit `5690272` | die Kopfzeile **woertlich** |

Alle Werte in diesem Auftrag aus den Dateien gelesen.

**Zwei Befunde stecken darin.** Erstens: `trefferquote.json` traegt zwei
Schreibweisen fuer dieselbe Fassung — jede Auswertung, die danach gruppiert,
zerfaellt. Zweitens: **der Nachschlaglauf vom 26.8. hat die neue Vorgabe nicht
getroffen.** Er schrieb `"Fassung 0.8 (24. August 2026)"`, verlangt war die
Kopfzeile woertlich, also mit dem Dateinamen davor. Ob die Vorgabe zum Zeitpunkt
des Laufs schon galt, ist in diesem Auftrag **nicht geprueft** — Commit `5690272`
und Lauf liegen am selben Tag.

Die Dokumentaenderung stoppt das Auseinanderlaufen also bestenfalls kuenftig; sie
repariert nichts. Es fehlt weiterhin eine **Normalisierung beim Lesen der
Trefferquote** oder eine einmalige Berichtigung der bestehenden Eintraege.

### 7.3 Der Urteilsstand vom 26.8. steht in keiner Trefferquote

**Der Auftragstext dieser Uebergabe sagt, dieser Eintrag der Trefferquote sei
nicht repraesentativ und solle so gekennzeichnet werden. Den Eintrag gibt es
nicht.**

Belegt: `trefferquote.json` traegt **zwei** Eintraege (21.8. und 25.8.), beide mit
`lauf: 1` und `modell: "sonnet"`. Der Lauf vom 26.8. — 69 Kandidaten, 44 ja,
25 nein, Quote 64 % — ist nicht darunter. Der Grund steht woertlich in beiden
Nachweislaeufen:

    Trefferquote-Eintrag fuer video_name='2026-08-25 15-14-00' lauf=1
    existiert bereits in labels\repeat\trefferquote.json - nichts angehaengt

`_hat_bestehenden_eintrag` prueft auf `video_name` **und** `lauf`. Der
zusammengefuehrte Satz traegt `lauf: 1` (die kleinste Laufnummer), faellt damit
mit dem Sonnet-Eintrag vom 25.8. zusammen und wird verworfen.

**Damit sind zwei Dinge offen, nicht eines:**

1. **Der Auswertungsverlust.** Der ergiebigste Lauf der ganzen Linie — 69
   Kandidaten aus zwei Modellen, vollstaendig beurteilt — hinterlaesst in der
   Trefferquote **keine Spur**. Solange `lauf` der Schluessel ist, wird das bei
   jeder zusammengefuehrten Aufnahme wieder so sein. Der Schluessel muss um die
   Zusammenfuehrung wissen (etwa `laeufe` statt `lauf`), sonst ist die
   Trefferquote fuer den Regelbetrieb blind.
2. **Die Repraesentativitaet.** Der Nutzer hat beim Urteilen am 26.8. eigenen
   Angaben nach einen Teil der Kandidaten **aus Ermuedung** abgelehnt. Ein
   Trefferquoteneintrag dieses Laufs waere also ohnehin nicht als Modellbefund zu
   lesen. **Herkunft dieser Angabe: der Auftragstext dieser Uebergabe. Im
   Repository nicht belegt.** Wird der Eintrag nachgetragen, gehoert ein Feld
   dazu, das ihn als nicht repraesentativ kennzeichnet — sonst verschlechtert er
   jede spaetere Modellauswertung, statt sie zu verbessern.

### 7.4 Punktdichte und Ausbeute — weiterhin zwei Messpunkte, keine Reihe

| Aufnahme | Material | Quote | Interpunktion |
|---|---|---|---|
| `2026-08-21 10-46-08` | 584.900 ms | **87,1 %** | normal interpunktiert |
| `2026-08-25 15-14-00` | 1.073.716 ms | **64,1 %** | zehn Minuten ohne jedes Satzzeichen |

Quoten aus `trefferquote.json`, in diesem Auftrag gelesen.

**Unveraendert: das sind ZWEI Messpunkte, keine Reihe.** Der Zusammenhang ist eine
Vermutung. Es koennte ebenso an der Materiallaenge liegen, am Thema, an der
Tagesform des Modells oder an der Urteilsstrenge des Nutzers — und seit 7.3 ist
klar, dass die Urteilsstrenge tatsaechlich schwankt.

Der Nachschlaglauf liefert dazu ein **Gegenargument**, das vorher nicht da war:
Opus fand an derselben Aufnahme, mit derselben fehlenden Interpunktion, **65
statt 39** Kandidaten bei besserem Zielbereichsanteil. Haette die Punktdichte das
Material erschoepft, waere das nicht moeglich gewesen. **Das entkraeftet die
Vermutung nicht, aber es verschiebt sie:** die fehlende Interpunktion scheint
eher das schwaechere Modell zu treffen als das Material.

**Was zu tun ist, unveraendert:** mitzaehlen. Der Auftragstext der Zerlegung
verlangt bereits vier Selbstauskuenfte zur Punktdichte — sie landen aber nicht in
`trefferquote.json`. Drei bis fuenf weitere Laeufe, dann laesst sich die Frage
beantworten. Vorher nicht.

Der Widerspruch in den Punktdichte-Zahlen des 21.8. (29/34 und 424/422) ist
**unveraendert offen**.

### 7.5 Die Faustregel der Kriteriendatei ueber die Interpunktion ist so nicht haltbar

`labels\repeat\shorts-kriterien.yaml` sagt unter `bekannte_grenzen` (Zeile 28–30)
und in Zeile 128–130, die Punktdichte breche **„ueber die Laufzeit weg"**; im
letzten Viertel einer Aufnahme stehe `interpunktion_traegt_die_grenzen` praktisch
nicht zur Verfuegung. `ZERLEGUNG-AUFTRAGSTEXT.md` gibt dieselbe Regel weiter
(5,4 % im ersten Viertel gegen 0,4 % im letzten).

**An der Aufnahme vom 25.8. stimmt das nicht.** In diesem Auftrag aus
`wortliste.json` nachgezaehlt (3023 Woerter, 64 davon auf `.`, `!` oder `?`
endend):

| Abschnitt | Woerter mit Satzende | Anteil |
|---|---|---|
| Erste Haelfte (Wort 1–1511) | 8 | **0,53 %** |
| Zweite Haelfte (Wort 1512–3023) | 56 | **3,70 %** |
| Gesamt | 64 | 2,12 % |

**Die Dichte ist in der zweiten Haelfte siebenmal so hoch wie in der ersten — das
Gegenteil der Faustregel.** Die Interpunktion brach hier in der **Mitte** weg,
nicht ueber die Laufzeit: zwischen dem Satzendwort bei **58,66 s** und dem
naechsten bei **673,36 s** liegen 614,7 s ohne ein einziges Satzzeichen. Davor
5,00 % (8 von 160 Woertern), danach 5,06 % (56 von 1107).

**Widerspruch zum Auftragstext dieser Uebergabe, ausdruecklich vermerkt:** dort
stehen „Sekunde 60 bis 671", „davor 5,03 %", „danach 5,05 %". Meine Nachzaehlung
ergibt 58,66 s / 673,36 s und 5,00 % / 5,06 %. Die Abweichungen sind klein und
erklaeren sich aus der Wahl der Grenzwoerter; **an der Aussage aendern sie
nichts, und die Aussage ist die eigentliche Sache.** Beide Fassungen stehen hier,
weil ich nicht sagen kann, welche Grenzziehung der Auftragstext benutzt hat.

**Was daraus folgt.** Die Regel in der Kriteriendatei ist eine Verallgemeinerung
aus **einer** Aufnahme. Eine zweite widerlegt sie. Die Weisung, die daraus
abgeleitet wurde — „behandle die zweite Haelfte deswegen nicht duenner" — bleibt
trotzdem richtig; sie ist nur aus dem falschen Grund richtig. **Die Faustregel
gehoert bei der naechsten Fassung der Kriteriendatei berichtigt**: die
Interpunktion kann an beliebiger Stelle wegbrechen, und wo sie es tut, traegt
allein der Textsinn. Wo genau, sagt die Wortliste — sie liegt jedem
Zerlegungslauf ohnehin vor.

### 7.6 Die Zusammenfuehrung ist nicht byte-idempotent

`zusammengefuehrt_am` traegt einen Zeitstempel. Zwei Aufrufe von Stufe 6 ueber
demselben Bestand ergeben zwei verschiedene Dateien, obwohl sich inhaltlich
nichts geaendert hat. Fuer die Urteile harmlos (die Indizes bleiben), fuer ein
spaeteres „hat sich etwas geaendert?" ueber einen Hash irrefuehrend.

### 7.7 Stufe 6 und `auswahl --zusammenfuehren` verhalten sich verschieden

Beide melden Code 9 mit derselben Meldung, aber aus verschiedenem Anlass: die
Befehlszeile sperrt **pauschal**, sobald `kandidaten.json` und Urteile
zusammentreffen, weil sie den kuenftigen Inhalt nicht kennt. Stufe 6 prueft
**nach Befund**, weil ihr die neue Fassung fertig gerechnet vorliegt. Das ist so
gewollt und begruendet — aber es ist eine Stelle, an der spaeter jemand
stolpert.

### 7.8 `aus_laeufen` ist ersatzlos verschwunden — und mit ihm eine Zahl

Bis zum 26.8. trug ein ersetzter Kandidat `aus_laeufen`. Mit der berichtigten
Regel (5.1) stammt kein Eintrag mehr aus zwei Laeufen, also wurde das Feld
gestrichen. **Damit ist aber auch nicht mehr ablesbar, wie oft zwei unabhaengige
Laeufe denselben Ausschnitt gefunden haben** — und genau danach fragt
`zerlegung_laeuft_zweimal` in der Kriteriendatei. Heute laesst sich die Zahl 41
(Abschnitt 3.1) nur noch aus der Differenz erschliessen, nicht aus der Datei
lesen. Die 35 weggefallenen Kandidaten hinterlassen gar keine Spur.

### 7.9 Weiterhin offen aus den Vorgaengerinnen

Ohne Aenderung: die Schlusszeile von `kette` meldet Vollendung auch bei `--bis N`;
`angenommen` traegt in Teillisten die Zahl der Gesamtauswahl; die Zwischenstufen
im Bauordner bleiben liegen; `die_sicherheitsstufe_traegt` gilt fuer Sonnet nicht;
das Veroeffentlichen; der Stillevorlauf-Rueckfall bei kandidat-08/-09 vom 21.8.;
die verschwundene +1-Frameabweichung; der fehlende turbo-Durchsatzwert in
`UMGEBUNG.md`; die lueckenhafte Fassungsgeschichte der Kriteriendatei; die vier
Ordner mit zusammengeschobenen Pfadnamen; `laengere_fassung` als nie befuelltes
Feld; und die Datei `-` aus `tests\repeat\test_cutcli.py`.

Ausserdem gilt weiter, was die Auftraege unter „bekannt und harmlos" fuehren: 20
vorbestehende mypy-Fehler in drei Dateien, drei Pytest-Warnungen, sieben Tests mit
`@pytest.mark.echter_unterprozess`. Dazu neu:
`test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state`
gilt als **flatterhaft** — zwei der vier Auftraege sahen ihn einmal scheitern und
im naechsten Lauf bestehen. **Nachgesehen wurde er nie**; ob er einen echten
Fehler verdeckt, ist **nicht belegt**.
