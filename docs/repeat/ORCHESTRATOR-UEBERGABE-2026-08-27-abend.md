# Orchestrator-Uebergabe — Shorts-Produktionslinie, 27. August 2026, Abend
Stand: HEAD `a3b9717` auf `master`, gepusht. Arbeitsbaum: nur die bekannte
Datei `-`.

**Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-27.md` NICHT ab — es
setzt sie fort.** Alles, was dort steht, gilt weiter, wo hier nichts anderes
steht; und jene setzt ihrerseits die Abendfassung vom 25.8. fort, diese die
Nachmittagsfassung vom 25.8. Es sind vier Dokumente in einer Kette, nicht vier
Fassungen desselben. Das Nachschlagewerk zu diesem Dokument ist
`BESTAND-2026-08-27-abend.md`; dort stehen Zeilennummern, Signaturen und
Schemata.

Was hier steht, stammt aus den beiden Berichten
`artefakte\repeat\vorauswahl-verfall\BERICHT-2026-08-27.md` und
`artefakte\repeat\teilbau\BERICHT-2026-08-27.md`, aus den in diesem Auftrag
gelesenen Quelldateien, aus `labels\repeat\trefferquote.json`, aus der
ergaenzten Fassung von `docs\repeat\BUENDELUNG-AUFTRAGSTEXT.md` und aus den
Angaben des Auftraggebers. Jede Zahl traegt ihre Herkunft. Was nicht belegt
ist, ist als **nicht belegt** gekennzeichnet. Wo zwei Quellen einander
widersprechen, stehen **beide** Fassungen da, ausdruecklich als Widerspruch
bezeichnet.

Frag beim ersten Kontakt nach `git log --oneline -6` und `git status --porcelain`.

---

## 1 — Rolle und Arbeitsweise

**Die Abschnitte 1.1 bis 1.4 der Nachmittagsfassung vom 25.8., 1.1 und 1.2 der
Abendfassung vom 25.8. und 1.1 und 1.2 der Fassung vom 27.8. mittags gelten
unveraendert** — Aufbau eines Auftrags, Freigaben, Sperrliste, Modellwahl, und
vor allem „berichtigen statt befolgen". Zwei Dinge sind dazugekommen.

### 1.1 Der Orchestrator hat am 27.8. dreimal gegen das Material geschrieben

Abschnitt 1.2 der Fassung vom 27.8. mittags sagt: die haeufigste Fehlerquelle
ist der Orchestrator, nicht die Ausfuehrung. Der 27.8. hat das dreimal
bestaetigt. In allen drei Faellen hat das ausfuehrende Fenster berichtigt,
ausgefuehrt **und beide Fassungen genannt**. Das ist die erwartete
Arbeitsweise, nicht die Ausnahme.

**Fall 1 — 17 statt 19 offene Kandidaten.** Der Auftrag `teilbau` gab „17
offene, 42 insgesamt" als Pruefstein vor. Richtig waren **19 offene und 44
insgesamt**. Die Vorgabe unterstellte, die 25 bereits gebauten Shorts seien
eine Teilmenge der 42 Baulisteneintraege. Sie sind es nicht: `kandidat-02` und
`kandidat-15` stehen nicht mehr in der heutigen Bauliste, dafuer stehen `1` und
`12` darin, ohne gebaut gewesen zu sein. Schnittmenge 23, offen 42 − 23 = 19,
danach im Ziel 25 + 19 = 44.
Quelle: `teilbau\BERICHT-2026-08-27.md`, „Zwei verwaiste Shorts".

**Fall 2 — „keine bestehende Testfunktion aendern" gegen eine geaenderte
Schlusszeile.** Derselbe Auftrag verlangte in einem Teil, dass die Schlusszeile
von Schritt 7 kuenftig **beide** Zahlen nennt (Zuwachs und Bestand), und
verbot in einem anderen, bestehende Tests anzufassen. Genau diese Zeile pruefen
zwei bestehende Tests woertlich. Der Widerspruch ist **unaufloesbar**; das
Fenster hat die zwei Zusicherungen angepasst, die Aussage der Tests unveraendert
gelassen und beides gemeldet.
Quelle: `teilbau\BERICHT-2026-08-27.md`, TEIL 4 und „Angehalten / berichtigt".

**Fall 3 — „ZWISCHEN Gruppen wird NICHT gerangt" gegen die verlangte
Vorauswahl.** Der Auftrag `vorauswahl-verfall-wurzelfelder` verlangte einen
`gruppen_rang` ueber alle Gruppen — waehrend
`docs\repeat\BUENDELUNG-AUFTRAGSTEXT.md` genau das ausdruecklich untersagte.
Das Fenster hat den Satz **bewusst ersetzt** statt ihn zu uebersehen: er lautet
jetzt „ZWISCHEN Gruppen wird gerangt — aber nur nach der Staerke des
Ausschnitts, nie nach der Wichtigkeit des Themas", mit Verweis auf den neuen
Abschnitt „Die Vorauswahl".
Quelle: `vorauswahl-verfall\BERICHT-2026-08-27.md`, „Angehalten — eine Vorgabe,
die dem Code widersprach"; im ergaenzten Auftragstext nachgelesen.

Die Fassung vom 27.8. mittags hatte diese Ersetzung in Abschnitt 4.5 und 6(1)
ausdruecklich **vorher** verlangt. Sie ist eingetreten. Das ist der einzige der
drei Faelle, den eine Uebergabe im Voraus verhindert hat — die beiden anderen
kosteten je einen Umlauf.

### 1.2 `urteilslauf` ohne Fahnen ist KEIN Lesebefehl

**Neu, und teuer bezahlt.** Beim Beleg fuer einen Pruefstein wurde
`urteilslauf` ohne Pfad und ohne Fahnen aufgerufen, nur um zu sehen, welche
Aufnahme gewaehlt wuerde. Der Lauf hat nicht gesucht, sondern seinen **ganzen
Weg genommen**: die Aufnahme war zu dem Zeitpunkt erst 47,3 Stunden alt, also
nicht verfallen, und wurde regulaer verarbeitet. Drei Folgen — ein dritter
Trefferquote-Eintrag (wiederhergestellt), zwei neue Sicherungsdateien unter
`labels\repeat\`, und eine neu erzeugte `bauliste.json` der echten Aufnahme
(20914 statt 22034 Byte, 42 statt 25 angenommene Kandidaten).
Quelle: `vorauswahl-verfall\BERICHT-2026-08-27.md`, „Was schiefging".

**Die Regel:** wer nur wissen will, welche Aufnahme gewaehlt wuerde, ruft
`urteilslauf.finde_aufnahme` auf oder `urteilslauf … --nur-offene-zeigen`.
Beide schreiben nichts. Der nackte `urteilslauf` baut, sichert und schreibt in
die Trefferquote.

---

## 2 — Stand der Linie

Der Stand der Fassung vom 27.8. mittags (Abschnitt 2: zwei Befehle, sieben
Stufen, Urteilstor, Gruppenanzeige) **gilt fort**. Geaendert haben sich Schritt
1 und 7 des `urteilslauf` und die Anzeige der Urteilsseite; die sieben Stufen
der Kette sind unveraendert.

### Die Commits seit `357f12f`

| Commit | Botschaft | Was er aendert |
|---|---|---|
| `897e762` | Shorts: Vorauswahl der besten Gruppen, Verfall nach 48 Stunden | `auswahl.py`, `judge.py`, `kette.py`, `urteilslauf.py`, `BUENDELUNG-AUFTRAGSTEXT.md`, zwei Testdateien |
| `175c1d4` | Shorts: Urteile und Kandidaten des Doppellaufs vom 25.8. | zwei Belegdateien unter `labels\repeat\` |
| `5379982` | Shorts: Trefferquote des Doppellaufs vom 25.8. | `labels\repeat\trefferquote.json` |
| `a3b9717` | Shorts: Teilbau baut nur die offenen, Rueckfall auf modelle | `auswahl.py`, `urteilslauf.py`, `tests\test_shorts_urteilslauf.py` |

**Widerspruch, ausdruecklich vermerkt.** Der Auftragstext dieser Uebergabe sagt
unter GEGEBEN „Fuenf Commits seit `357f12f`", nennt vier und laesst den fuenften
aus `git log` ermitteln. **`git log` kennt nach `357f12f` genau vier Commits.**
Der fuenfte kann nur `357f12f` selbst sein — der Commit der vorigen Uebergabe
(„Doku: Uebergabe und Bestand vom 27.8."), also der Anfangspunkt und nicht ein
weiterer Schritt. Beide Lesarten stehen hier; welche der Auftraggeber meinte,
ist **nicht belegt**. `git log --oneline -6` ist im Zweifel die Wahrheit.

### Befehl 1 — `kette`, unveraendert sieben Stufen

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

Rueckgabecodes unveraendert: **0** Erfolg, **2** keine Aufnahme, **5** Stufe
gescheitert oder unbekannt, **9** Urteile wuerden umgedeutet, **10**
`buendel.json` weicht ab. Die **6** gibt es nicht mehr.

Eine Aenderung durch `897e762`: **`bestimme_aufnahme` kennt jetzt den Verfall.**
Eine von selbst gewaehlte Aufnahme, die aelter als 48 Stunden ist, fuehrt zu
`KetteFehlschlag("nur_verfallen", …)` mit Code 2; eine ausdruecklich per
`--aufnahme` genannte laeuft weiter und wird nur gewarnt. Dieselbe Regel wie in
`urteilslauf.finde_aufnahme`, an derselben Stelle.

### Befehl 2 — `urteilslauf`, sieben Schritte, Schritt 1 und 7 geaendert

**Schritt 1** uebergeht verfallene Aufnahmen und **nennt jede in einer Zeile**
(`uebergangen: <NAME> (verfallen, N h alt)`). Gibt es nur verfallene, endet der
Lauf mit `ANGEHALTEN [nur_verfallen]` und Code 2 — **vor jedem Schreibvorgang**.
`--auch-verfallen` hebt das auf; ein ausdruecklich uebergebener Auftragsordner
verfaellt nicht, sondern warnt nur.

**Schritt 7 ist Teilbau.** `_CODE_ZIEL_BELEGT = 7` ist ersatzlos entfallen. Ein
belegter Zielordner haelt den Lauf nicht mehr an, sondern schraenkt ihn auf die
Kandidaten ohne `short.mp4` ein. Die Schlusszeile nennt Zuwachs **und** Bestand:

```
Fertig: <Ziel> - 19 von 19 offenen Shorts neu gebaut in 855.0 s, 44 insgesamt im Zielordner
```

Code 8 vergleicht seit `a3b9717` den **Zuwachs** gegen die Zahl der Eintraege
der Teilbauliste, nicht mehr den Bestand gegen die volle Bauliste — Begruendung
in Abschnitt 5.3.

Rueckgabecodes von `urteilslauf`: **0** Erfolg, **2** keine Aufnahme / nur
verfallene / Auftrag unlesbar, **5** Urteilsabweichung, **6** Sicherung
fehlgeschlagen, **8** Bau unvollstaendig. Die **7** gibt es nicht mehr.

### Die Urteilsseite zeigt jetzt eine Vorauswahl

Traegt die `buendel.json` bei **allen** Gruppen einen `gruppen_rang`, sortiert
die Seite nach Staerke statt nach Gruppennummer, zeigt die 15 vorausgewaehlten
Gruppen offen und die uebrigen hinter einer Zeile „N weitere Gruppen anzeigen".
Der Zaehler nennt die Vorauswahl **und daneben** die Gesamtzahl. Die gefuehrte
Sitzung (`buildInitialQueue`) fuehrt nur ueber die vorausgewaehlten offenen
Gruppen.

**Fehlt das Feld auch nur bei einer Gruppe, bleibt alles wie vorher** — alle
Gruppen, Nummernfolge, kein Aufklapper. Dasselbe Rueckfallmuster wie bei der
`buendel.json` selbst: eine halbe Vorauswahl waere schlechter als keine, weil
die Seite dann eine Reihenfolge zeigte, die nur fuer einen Teil des Bestandes
gilt.

### Zusammengefasst, mit Hash

| | |
|---|---|
| HEAD | `a3b9717` auf `master`, gepusht |
| `git status --porcelain` | nur `?? -` |
| `labels\repeat\trefferquote.json` | SHA-256 `9b2c60d1545297e0a6aafb37e0f4d4306c5d4af0779579a1b816f87e12250223`, drei Eintraege |
| `.py`-Dateien unter `src\matrix_auto_cutter\shorts\` | **28** |

Der Hash der Trefferquote stammt aus Pruefstein 5 des Teilbau-Berichts und ist
in diesem Auftrag nachgerechnet — er ist derselbe. Die Datei ist seit `5379982`
unveraendert. **Achtung:** es ist der Hash des **Arbeitsbaums** (CRLF), nicht
der der Repositoriumsfassung (LF) — siehe Abschnitt 7.5.

---

## 3 — Die Laeufe vom 27.8.

Beide an der Aufnahme `2026-08-25 15-14-00`.

### 3.1 Buendelung mit Vorauswahl, zweiter Lauf

Gefahren **an einer Kopie** unter `artefakte\repeat\vorauswahl-probe\`; der
echte Aufnahmeordner wurde nicht angefasst.

| Groesse | Wert | Herkunft |
|---|---|---|
| Laufzeit | **19 min 28 s** | Auftragstext dieser Uebergabe (GEGEBEN); deckungsgleich mit `vorauswahl-verfall\BERICHT-2026-08-27.md`, TEIL 6 |
| Kandidaten | 69 | ebenda |
| Gruppen | **46** | ebenda |
| Vorausgewaehlt | **15** | ebenda |
| `vorauswahl_groesse` | 15 | ebenda |
| `pruefe_buendel` | **null Abweichungen** | ebenda |
| Modell | `opus` | ebenda |

**46 Gruppen, nicht 47.** Der erste Buendellauf vom 26.8. ergab 47. Das ist kein
Fehler: ein neuer Lauf mit einem ergaenzten Auftragstext darf anders
gruppieren, und dieser hat zwei Gruppen zusammengelegt und eine geteilt. Die
alte `buendel.json` der echten Aufnahme wurde nicht angefasst.
Herkunft: `vorauswahl-verfall\BERICHT-2026-08-27.md`, TEIL 6.

Die 15 vorausgewaehlten Gruppen und die vollstaendige Auswertung aller 46
stehen in `artefakte\repeat\vorauswahl-probe\auswertung.json` — **`artefakte\`
ist `.gitignore`-ausgeschlossen**, die Datei liegt also nur lokal.

### 3.2 Der Verfall im echten Lauf

Zum ersten Anlauf um 14:27 war die Aufnahme erst 47,3 Stunden alt und damit
**nicht** verfallen. Um 15:14 ueberschritt sie die Grenze. Der Lauf um 15:14:53,
unveraenderter Befehl, ohne Pfadangabe, endete mit
`ANGEHALTEN [nur_verfallen]` und `EXITCODE=2` — in Schritt 1, vor jedem
Schreibvorgang: keine Bauliste, keine Sicherung, kein Trefferquote-Eintrag,
keine Urteilsdatei angefasst. Fuenf Aufnahmen wurden namentlich uebergangen
(483 h, 222 h, 189 h, 148 h, 48 h alt).
Herkunft: `vorauswahl-verfall\BERICHT-2026-08-27.md`, „Pruefstein 4".

### 3.3 Urteilsstand vom 27.8.

**69 von 69 beurteilt — 42 ja, 27 nein, Quote 61 %.**
Herkunft: Auftragstext dieser Uebergabe (GEGEBEN); in
`labels\repeat\trefferquote.json` als dritter Eintrag nachgelesen
(`kandidaten_gesamt` 69, `angenommen` 42, `abgelehnt` 27, `ohne_urteil` 0,
`quote` 0.609).

**Der Stand hat sich seit dem 26.8. verschoben, und beide Zahlen stehen hier.**
Die Fassung vom 27.8. mittags, Abschnitt 3.4, hielt woertlich fest:
`69 von 69 beurteilt - 44 ja, 25 nein, 0 offen - Quote 64 %`. Am 27.8. ist eine
dritte Urteilsdatei dazugekommen (`urteile-2026-08-27-095238.json`, genannt im
Teilbau-Bericht), die zwei Annahmen zuruecknahm. **Kein Widerspruch, sondern
zwei Zeitpunkte** — aber wer die 64 % irgendwo zitiert findet, weiss jetzt,
woher sie stammt.

### 3.4 Der Teilbau

| Groesse | Wert | Herkunft |
|---|---|---|
| Rueckgabecode | 0 | `teilbau\BERICHT-2026-08-27.md`, TEIL 5b |
| Laufzeit des Baus | **855,0 s** (14 min 15 s) | GEGEBEN; ebenda |
| neue `short.mp4` | **19** | GEGEBEN; ebenda |
| insgesamt im Zielordner danach | **44** | GEGEBEN; ebenda |
| Gesamtgroesse vorher | **263 111 453 B** (0,245 GiB) | GEGEBEN; ebenda |
| Gesamtgroesse nachher | **426 402 130 B** (0,397 GiB) | GEGEBEN; ebenda |
| der 25 vorhandenen mit neuer Aenderungszeit | **0** | GEGEBEN; ebenda, Pruefstein 3 |

Offene Indizes vor dem Bau (19): 1, 12, 43, 45, 48, 50, 51, 52, 54, 56, 57, 58,
59, 60, 63, 64, 66, 67, 68. Neu gebaut in Reihenfolge der Fertigstellung: 01,
43, 45, 12, 48, 50, 51, 52, 54, 56, 57, 58, 59, 64, 60, 63, 66, 67, 68 — von
2026-08-27 16:25:38 bis 16:38:38.

**Der erste echte Teilbau brach ab** mit
`ANGEHALTEN [candidates_unreadable]: Kandidat 67: 'enthaelt' verweist auf
unbekannte Indizes [36]`. 0 gebaut, Rueckgabecode 8, die 25 vorhandenen Shorts
unveraendert — der neue Zaehler hat damit gleich seinen ersten Dienst getan.
Ursache und Behebung in Abschnitt 5.2.

**Rund 163 MB Zuwachs fuer 19 Shorts**, also grob 8,6 MB je Short einschliesslich
Zwischenstufen. Das ist die Groessenordnung, an der der Aufraeumer fuer `F:`
(Abschnitt 6) haengt; **welcher Anteil davon Zwischenstufen sind, ist fuer
diesen Lauf nicht gemessen und damit nicht belegt.**

### 3.5 Die Trefferquote traegt jetzt drei Eintraege

| # | Aufnahme | Lauf / Kennung | Modell | Kandidaten | Quote |
|---|---|---|---|---|---|
| 1 | `2026-08-21 10-46-08` | `lauf` 1 | `sonnet` | 31 | **0,871** |
| 2 | `2026-08-25 15-14-00` | `lauf` 1 | `sonnet` | 39 | **0,641** |
| 3 | `2026-08-25 15-14-00` | `laeufe` [1, 2] | **`unbekannt`** | 69 | **0,609** |

Alle Werte in diesem Auftrag aus `labels\repeat\trefferquote.json` gelesen. Der
dritte Eintrag ist der, den die Fassung vom 27.8. mittags in Abschnitt 7.3 noch
als **fehlend** beschrieb — `laeufe` als Kennung hat ihn moeglich gemacht. Zu
seinem `unbekannt` siehe Abschnitt 7.2.

---

## 4 — Entscheidungen samt Begruendung

Die Entscheidungen 4.1 bis 4.4 der Nachmittagsfassung, 4.1 bis 4.3 der
Abendfassung und 4.1 bis 4.6 der Fassung vom 27.8. mittags **gelten weiter**.
Die folgenden vier bestaetigt der Auftraggeber am 27.8. abends ausdruecklich.
**Herkunft aller vier: der Auftragstext dieser Uebergabe unter GEGEBEN.**

### 4.1 Die 48-Stunden-Frist laeuft AB AUFNAHMEZEIT

Bestaetigt 4.4 der Fassung vom 27.8. mittags — und ist jetzt **im Code belegt**:
`urteilslauf.VERFALL_STUNDEN = 48`, gemessen ueber
`inventory.parse_name_timestamp` am **Ordnernamen**, nicht an der
Aenderungszeit einer Datei. Der Docstring nennt den Grund: „eine zweite
Zerlegung macht eine alte Aufnahme nicht wieder jung."

Damit ist auch die offene Frage aus Abschnitt 6(1) der Fassung vom 27.8.
mittags — „woher kommt die Aufnahmezeit?" — **beantwortet: aus dem
Ordnernamen.** Traegt ein Ordner keine lesbare Zeit, gilt er als nicht
verfallen; lieber eine Aufnahme zu viel anbieten als eine verschweigen.

### 4.2 Der Rhythmus des Tages

Aufnahme morgens → Schnitt → Hauptvideo hoch → **danach** die Shorts, in
derselben Frist. Bestaetigt 4.6 der Fassung vom 27.8. mittags unveraendert.
Daraus folgt weiterhin: die Shorts-Linie darf den Schnitt des Hauptvideos nicht
blockieren und muss mit dem gerenderten Ergebnis auskommen.

### 4.3 Ziel 4 bis 10 veroeffentlichte Shorts je Aufnahme

Ueber die auf die Aufnahme folgenden 24 Stunden verteilt eingeplant. Bestaetigt
4.3 der Fassung vom 27.8. mittags. **Das Wort ist `veroeffentlicht`, nicht
`gebaut`** — der Teilbau vom 27.8. hat 19 gebaut und 44 im Ordner liegen. Der
Abstand zwischen 44 und 4 bis 10 ist die eigentliche offene Strecke der Linie.

### 4.4 Die Vorauswahl umfasst 15 Gruppen

Belegt im Code: `auswahl.VORAUSWAHL_GROESSE = 15`, und im Auftragstext der
Buendelung als Wurzelfeld `vorauswahl_groesse` mit dem festen Wert `15`.
Gepruefte Grenze ist `min(15, Gruppenzahl)` — eine Aufnahme mit weniger als 15
Gruppen hat sie alle in der Vorauswahl.

**Begruendung, woertlich aus dem Kommentar an der Konstante:** „Der Nutzer
veroeffentlicht 4 bis 10 Shorts je Aufnahme und hat dafuer 48 Stunden — 15
Gruppen sind genug Auswahl dafuer und wenig genug, um sie in dem Fenster
wirklich durchzusehen."

---

## 5 — Betriebsfallen

Alle Fallen der drei Vorgaengerinnen **gelten weiter**. Vier sind dazugekommen.

### 5.1 Code 7 ist ersatzlos entfallen

Wer eine Anleitung, einen Test oder ein Skript findet, das auf
`_CODE_ZIEL_BELEGT = 7` oder auf „ANGEHALTEN [ziel_belegt]" wartet, hat eine
veraltete Quelle vor sich. **Der Teilbau baut nur, was noch keine `short.mp4`
hat** — `ist_gebaut` fragt nach der Datei, nicht nach dem Ordner. Ein
abgebrochener Bau, der `kandidat-NN` mit Zwischenstufen aber ohne Ergebnis
zurueckliess, gilt weiterhin als **offen** und wird neu gebaut; geloescht wird
der Ordner nicht, `build` schreibt in ihn hinein.

### 5.2 Eine gefilterte Bauliste macht `enthaelt`-Verweise ungueltig

`candidates.parse_candidates` weist einen `enthaelt`-Verweis auf einen Index
zurueck, den die Liste nicht enthaelt. In der **vollen** Bauliste faellt das
nie auf; **der Fehler entsteht erst durch das Filtern.** Am 27.8. verwies
Kandidat 67 auf 36, 36 war bereits gebaut und stand deshalb nicht in der
Teilbauliste — der erste echte Teilbau brach sofort ab.

`baue_teilbauliste` kuerzt `enthaelt` deshalb auf die Indizes, die in der
Teilliste stehen. Dieselbe Regel wie in `auswahl._bilde_verweise_ab`: **ein
fehlender Verweis behauptet nichts, ein falscher behauptet etwas**, und
`enthaelt` ist eine Zusatzangabe fuer die Gruppierung auf der Urteilsseite,
kein Pflichtfeld des Baus. Die Bauliste selbst bleibt unberuehrt — gekuerzt
wird nur die Kopie.

**Die Lehre ueber diesen Fall hinaus:** ohne den Lauf am echten Material waere
das nicht aufgefallen. Wer kuenftig irgendeine Kandidatenliste filtert —
Vorauswahl, Sortierung, Nacharbeit —, prueft zuerst, welche Felder auf andere
Indizes derselben Liste zeigen.

### 5.3 Der Zielordner und die Bauliste sind zwei verschiedene Dinge

Ein Zielordner kann `short.mp4` enthalten, die die heutige Bauliste nicht mehr
nennt. Bei der Aufnahme `2026-08-25 15-14-00` sind das `kandidat-02` und
`kandidat-15`: gebaut am 25./26.8. gegen eine aeltere Bauliste
(`shorts-bau-bericht.json` weist `candidate_count: 25` aus), seither hat ein
drittes Urteil die Auswahl verschoben.

Daraus folgen zwei Dinge:

1. **Der Bau zaehlt den ZUWACHS, nicht den Bestand.** Gegen den Bestand
   geprueft schluege er fehl, obwohl er alles Offene gebaut hat.
2. **Beim Hochladen zaehlt die BAULISTE, nicht der Ordnerinhalt.** Wer einmal
   ueber den Zielordner listet und alles hochlaedt, was er findet,
   veroeffentlicht zwei Shorts, die der Nutzer nicht ausgewaehlt hat. Das
   gehoert in die Uploadstufe hinein, bevor sie gebaut wird.

Die zwei verwaisten Shorts bleiben liegen: sie zu loeschen ist verboten und
waere auch sachlich falsch — gebautes Material, das nur die heutige Auswahl
nicht mehr nennt.

### 5.4 `modell` fehlt in aelteren zusammengefuehrten Kandidatendateien

`auswahl.fuehre_zusammen` entfernte das Wurzelfeld `modell` bis `897e762`
bewusst und ersetzte es durch `modelle` (`{"1": "sonnet", "2": "opus"}`).
`sicherungsnamen` und `lies_kandidaten_rohdaten` lasen aber `modell` — daher
`unbekannt` in Dateinamen und im Trefferquote-Eintrag.

Seit `897e762` schreibt `fuehre_zusammen` **beides**: `modelle` und ein daraus
gebildetes `modell` (`sonnet+opus`). Seit `a3b9717` gibt es zusaetzlich
`auswahl.modell_kennung` als Rueckfall — fehlt `modell`, wird es aus `modelle`
gebildet, in numerischer Laufreihenfolge mit `+` verbunden; fehlt auch das,
bleibt es bei `unbekannt`. Geraten wird nicht.

**Der Rueckfall berichtigt keine bestehenden Eintraege.** Wer eine
`kandidaten.json` vor sich hat, die vor `897e762` zusammengefuehrt wurde, findet
dort weiterhin kein `modell` — der Rueckfall greift beim **Lesen**, nicht in der
Datei. Und der dritte Trefferquote-Eintrag traegt weiterhin `unbekannt`
(Abschnitt 7.2).

---

## 6 — Der Weg weiter

Das Vorhaben (1) aus Abschnitt 6 der Fassung vom 27.8. mittags — Vorauswahl und
Verfall — ist **erledigt** und im Betrieb gefahren. Was bleibt, sind vier
Vorhaben in dieser Reihenfolge.

### (1) Der Wecker

Windows-Aufgabenplanung ruft `claude -p --permission-mode acceptEdits`. Die
Fallen stehen vollstaendig in Abschnitt 6, Schritt 4 der Nachmittagsfassung vom
25.8.: Arbeitsverzeichnis setzen, `TREFFERQUOTE_PFAD` ist relativ, `.claude\`
ist leer, kein Zeitplaner in Claude Code 2.1.220.

**Beim Aufsetzen erneut pruefen, ob eine neuere Claude-Code-Fassung einen
eingebauten Zeitplaner mitbringt.** Fuer neuere Fassungen **nicht belegt** — in
diesem Auftrag nicht geprueft, kein Netzzugriff.

Mit dem Verfall hat der Wecker einen zweiten Zweck: er stoesst nicht nur an,
sondern muss auch mit der Frist rechnen. Der Code haelt von selbst an (Code 2),
wenn nur noch Verfallenes daliegt — der Wecker darf das **nicht als Fehler
melden**, sondern als „nichts zu tun".

### (2) Die Uploadstufe — erst nach der Audit-Klaerung

**Kontingent und Audit sind zwei verschiedene Dinge. Das Kontingent steht, das
Audit nicht.**

- **Bestaetigt am Projekt des Nutzers:** „Video Uploads per day" 100, Nutzung 0.
- **Der OAuth-Zustimmungsbildschirm steht auf „In Produktion".**
- **Ob der Scope `youtube.upload` freigegeben und das Compliance-Audit
  bestanden ist, ist WEITERHIN NICHT GEKLAERT.**

Herkunft aller drei Angaben: der Auftragstext dieser Uebergabe. **Im Repository
nicht belegt, in diesem Auftrag nicht nachgeprueft** — kein Netzzugriff, kein
Zugriff auf das Google-Projekt.

Ein „In Produktion" stehender Zustimmungsbildschirm ist kein Nachweis eines
bestandenen Audits; ein zugeteiltes Uploadkontingent erst recht nicht. Wer die
beiden verwechselt, baut die Stufe und stellt Wochen spaeter fest, dass sie
nicht senden darf.

**Der Test, der die Frage beantwortet, ohne etwas zu veroeffentlichen:** ein
Video per API mit `privacyStatus: private` hochladen und in YouTube Studio
nachsehen, ob dort ein Hinweis auf unbestaetigte API-Nutzung steht. Das kostet
einen Uploadaufruf von hundert und entscheidet die Frage.

Bei 4 bis 10 Shorts je Aufnahme ist das Kontingent kein Engpass — es traegt
zehn Aufnahmen am Tag. **Der Engpass ist die Freigabe, nicht die Zuteilung.**

Weiterhin gilt: Shorts brauchen **9:16** und **`#Shorts` im Titel**; und die
Bauliste bestimmt, was hochgeht, nicht der Ordnerinhalt (Abschnitt 5.3).

### (3) Der Aufraeumer fuer `F:`

Unveraendert offen seit dem 25.8. nachmittags. Je Kandidat stehen neben
`short.mp4` die Zwischenstufen `ausschnitt.mp4`, `leinwand.mp4` und
`mit-avatar.mp4` samt Seitendateien; sie machen rund 70 % des Zielordners aus
(Messung 21.8.). Der Zielordner der Aufnahme `2026-08-25 15-14-00` ist am 27.8.
von 263 MB auf 426 MB gewachsen (Abschnitt 3.4). Die heutige **Gesamtgroesse**
von `F:` ist **nicht belegt** — `F:` war in diesem Auftrag gesperrt.

### (4) Eine Oberflaeche fuer die Shorts-Linie

Unveraendert: **noch nicht erkundet**, braucht einen eigenen, rein lesenden
Erkundungsauftrag ueber das bestehende Interface des Cutters, bevor irgendetwas
entworfen wird. Was es kann und was sich uebernehmen laesst, ist **nicht
belegt**.

---

## 7 — Offene Punkte

Alle offenen Punkte der drei Vorgaengerinnen **bleiben offen**, soweit hier
nichts anderes steht. Erledigt sind daraus: der fehlende Trefferquote-Eintrag
des Doppellaufs (7.3, erster Teil — `laeufe` ist jetzt die Kennung) und die
Ursache der `unbekannt`-Sicherungsnamen (7.1). Neu oder veraendert sind die
folgenden.

### 7.1 Die Vorauswahl ist UNBELEGT — und der einzige Hinweis faellt negativ aus

**Der wichtigste offene Punkt dieser Uebergabe.** Gegen die Urteile vom 26.8.
gemessen:

| Menge | Gruppen | davon mit einem `ja` | Anteil |
|---|---|---|---|
| **Vorauswahl** | 15 | 10 | **66,7 %** |
| Rest | 31 | 22 | **71,0 %** |
| Alle | 46 | 32 | **69,6 %** |

Herkunft: `vorauswahl-verfall\BERICHT-2026-08-27.md`. **Die Vorauswahl liegt
leicht unter dem Durchschnitt** — sie trifft nicht besser als der Rest.

**Zwei Gruende, warum das die Rangfolge trotzdem nicht widerlegt.**

1. **Das Mass ist zu stumpf.** Der Nutzer nahm am 26.8. 44 von 69 Kandidaten
   an, also **64 %**. Bei einer so hohen Annahmequote enthaelt fast jede Gruppe
   zwangslaeufig ein `ja`; der Unterschied zwischen 66,7 % und 71,0 % sind bei
   15 Gruppen weniger als eine ganze Gruppe.
2. **Es misst die falsche Frage.** `ja` hiess „diesen Ausschnitt bauen",
   gefaellt beim Einzelurteil ueber 69 Kandidaten. Die Vorauswahl beantwortet
   „welche 15 Themen zuerst ansehen". Ein Kandidat kann brauchbar und trotzdem
   nicht unter den besten 15 sein — genau darum geht es bei 4 bis 10
   Veroeffentlichungen.

**Der saubere Test ist die naechste Aufnahme: welche Shorts der Nutzer
tatsaechlich VEROEFFENTLICHT**, nicht welche er zum Bau freigibt. Bis dahin
gilt die Vorauswahl als unbelegt — sie ordnet die Arbeit, aber es ist nicht
gezeigt, dass sie sie richtig ordnet. Da sie nichts aussortiert und der Rest
einen Klick entfernt bleibt, ist der Schaden im Zweifel gering; der Nutzen ist
es vorerst aber auch.

Dieser Test setzt voraus, dass ueberhaupt festgehalten wird, **was
veroeffentlicht wurde**. Heute gibt es dafuer keine Datei und kein Feld — das
ist die stille Voraussetzung der Uploadstufe (Abschnitt 6(2)); ob es geplant
ist, ist **nicht belegt**.

### 7.2 Der dritte Trefferquote-Eintrag traegt `unbekannt` — bewusst

Er muesste `sonnet+opus` heissen. Er wurde **bewusst nicht berichtigt**:
`_hat_bestehenden_eintrag` schreibt einen vorhandenen Eintrag nie um, und der
Grund steht im Docstring — „die Trefferquote ist eine Reihe von Messungen, und
eine nachtraeglich ergaenzte Messung waere keine mehr."

**Die Folge muss man kennen:** jede Auswertung, die ueber `modell` gruppiert,
sieht drei Werte (`sonnet`, `sonnet`, `unbekannt`) und kann den ergiebigsten
Lauf der Linie keinem Modell zuordnen. Kuenftige Eintraege bekommen die richtige
Kennung (`modell_kennung`); dieser eine nicht. Wer die Reihe auswertet, schlaegt
hier nach.

### 7.3 Zwei Sicherungen mit `-unbekannt` im Namen — vom Nutzer geloescht

Der Ablauf, damit die Berichte nicht in die Irre fuehren:

1. Der versehentliche `urteilslauf` vom 27.8. (Abschnitt 1.2) erzeugte
   `kandidaten-…-lauf1+2-unbekannt.json` und `urteile-…-lauf1+2-unbekannt.json`.
2. **`vorauswahl-verfall\BERICHT-2026-08-27.md` nennt sie „inhaltlich Kopien der
   schon gesicherten Dateien" und liess sie stehen** — Loeschen war dem Auftrag
   untersagt.
3. **Diese Bezeichnung war falsch.** Sie trugen **69 Urteile**, die vorhandene
   Sicherung nur **39**. Herkunft dieser Zahlen: der Auftragstext dieser
   Uebergabe.
4. Der Nutzer hat sie geloescht; danach wurden sie richtig neu erzeugt und
   umbenannt. Im Repository liegen heute
   `kandidaten-2026-08-25 15-14-00-lauf1+2-sonnet+opus.json` und
   `urteile-2026-08-25 15-14-00-lauf1+2-sonnet+opus.json`, committet mit
   `175c1d4` — in diesem Auftrag im Arbeitsbaum nachgesehen.

**Verloren ging nichts**, weil der Aufnahmeordner die Urteile traegt; die
Sicherung unter `labels\repeat\` ist eine Kopie, nicht das Original.

**Beide Fassungen stehen hier**, weil `teilbau\BERICHT-2026-08-27.md`, TEIL 3
noch schreibt, die `-unbekannt.json` „bleiben ebenfalls stehen". Zum Zeitpunkt
jenes Berichts stimmte das; heute liegen sie nicht mehr da. Der Bericht ist
nicht falsch, sondern ueberholt.

**Die Lehre:** „inhaltlich eine Kopie" ist eine Behauptung ueber einen Inhalt,
den man gezaehlt haben muss. 69 gegen 39 ist keine Kopie.

### 7.4 Der Nachschlag mit Opus hat die Quote nicht gehoben

| Satz | Kandidaten | Quote |
|---|---|---|
| Lauf 1 allein, sonnet | 39 | **0,641** |
| Lauf 1+2 zusammengefuehrt | 69 | **0,609** |

Aus `trefferquote.json` gelesen. **Mehr Kandidaten, schlechtere Quote.** Dazu
passt die Buendelungsauswertung: 69 Kandidaten ergeben 46 Gruppen, also nur 46
verschiedene Aussagen — der Zuwachs an Kandidaten ist zu einem guten Teil ein
Zuwachs an Fassungen desselben.

**Zwei unabhaengige Hinweise in dieselbe Richtung, aber keine Reihe.** Und ein
Gegenargument, das genannt gehoert: die 0,609 stammen aus einem Urteilslauf, bei
dem der Nutzer nach eigenen Angaben einen Teil **aus Ermuedung** abgelehnt hat
(Fassung vom 27.8. mittags, 7.3 — dort schon als **nicht belegt** markiert). Ein
Modellbefund ist das also nicht. Der Eintrag traegt seit `897e762` ein Feld
`notiz` (heute leer) und `auswahl` eine Fahne `--notiz TEXT` — **genau dafuer**.
Dass sie hier nicht benutzt wurde, ist eine verpasste Gelegenheit.

Zu einer Aussage ueber Sonnet gegen Opus fehlen weiterhin drei bis fuenf Laeufe.

### 7.5 Weiterhin offen aus den Vorgaengerinnen

Ohne Aenderung: die Zwischenstufen im Bauordner bleiben liegen; `angenommen`
traegt in Teillisten die Zahl der Gesamtauswahl (und die Teilbauliste erbt das,
weil sie die Wurzel der Bauliste unveraendert weiterreicht); die
Zusammenfuehrung mehrerer Laeufe in der Trefferquote ist ueber `laeufe`
unterscheidbar, aber nicht auswertbar; die Schlusszeile von `kette` meldet
Vollendung auch bei `--bis N`; `kriterien_fassung` in drei Schreibweisen; die
Faustregel der Kriteriendatei ueber die Interpunktion; die nicht
byte-idempotente Zusammenfuehrung; der Unterschied zwischen Stufe 6 und
`auswahl --zusammenfuehren`; das verschwundene `aus_laeufen`; der falsche
`enthaelt`-Verweis in der bestehenden `kandidaten.json`; sowie die vollstaendige
Liste in Abschnitt 7.9 der Fassung vom 27.8. mittags.

Ausserdem gilt weiter, was die Auftraege unter „bekannt und harmlos" fuehren:
20 vorbestehende mypy-Fehler in drei Dateien, drei Pytest-Warnungen, sieben
Tests mit `@pytest.mark.echter_unterprozess`, die Datei `-`, und
`test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state`
als flatterhaft. **Nachgesehen wurde er weiterhin nie**; ob er einen echten
Fehler verdeckt, ist **nicht belegt**.

**Neu in dieser Liste:** Dateien unter `labels\repeat\` liegen im Repository mit
LF, im Arbeitsbaum mit CRLF. Bei JSON folgenlos — aber **ein Hashvergleich
zwischen Repositoriumsfassung und Arbeitsbaumfassung schlaegt fehl.** Wer einen
Hash notiert, schreibt dazu, von welcher Seite er ihn genommen hat; der in
Abschnitt 2 ist der des Arbeitsbaums.
