# Auftragstext: Shorts-Buendelung

## Rolle und Ergebnis

Du buendelst die Short-Kandidaten einer Aufnahme thematisch. Du liest die
Kandidatendatei, das Transkript und die geltende Kriteriendatei, ordnest
JEDEN Kandidaten genau einer Gruppe zu, bestimmst je Gruppe eine Empfehlung
und schreibst das Ergebnis als EINE JSON-Datei. Du schreibst keinen Code,
aenderst keine bestehende Datei und schneidest kein Video -- die eine Datei,
die du anlegst, ist das ganze Ergebnis.

Wozu das gut ist: der Nutzer beurteilt sonst jeden Kandidaten einzeln. Bei
69 Kandidaten ist das zu viel, und Doppelungen sind dabei nicht erkennbar --
am 26. August 2026 hat er drei Paare angenommen, die dasselbe Material
zeigen. Nach dieser Buendelung faellt je Gruppe EINE Entscheidung statt je
Kandidat.

## Platzhalter

`<AUFNAHME>` ist der Name der Aufnahme, zum Beispiel `2026-08-25 15-14-00`.
Er wird dir beim Start genannt. Alle Pfade unten sind relativ zur
Repo-Wurzel.

## Eingaben

- `labels\repeat\shorts-kriterien.yaml` -- die inhaltliche Vorgabe. **Lies
  sie vollstaendig.** Dieser Auftragstext gibt ihren Inhalt NICHT wieder,
  sondern verweist nur auf sie: stuenden die Kriterien auch hier, veralteten
  zwei Fassungen nebeneinander, und du wuesstest nicht, welche gilt. Was
  inhaltlich gilt, steht ausschliesslich in der YAML-Datei.
- `artefakte\repeat\shorts\<AUFNAHME>\kandidaten.json` -- die Kandidaten,
  die du buendelst. Sie ist deine Leitliste: jeder Index daraus bekommt
  einen Eintrag, kein anderer.
- `artefakte\repeat\shorts\<AUFNAHME>\transkript-rendered.json` -- das
  Transkript auf der gerenderten Achse. Daraus erkennst du, wovon ein
  Kandidat handelt, wenn Titel und Begruendung nicht reichen.
- `artefakte\repeat\shorts\<AUFNAHME>\wortliste.json` -- Wortliste mit
  Zeitmarken und Interpunktion. Hilft beim Ranken: wo sitzen die Grenzen
  wirklich.
- `artefakte\repeat\shorts\<AUFNAHME>\shorts-job.json` -- daraus
  `rendered_video.duration_ms`.

## Ausgabe: die Buendeldatei

Ziel: `artefakte\repeat\shorts\<AUFNAHME>\buendel.json`.

**Schreibe NICHT `kandidaten.json`, und aendere sie auch nicht.** An deren
Indizes haengen die Urteile des Nutzers: eine Urteilsdatei nennt einen Index
und erwartet dahinter genau den Kandidaten, den der Nutzer gesehen hat. Wird
`kandidaten.json` umgeschrieben, zeigen die vorhandenen Urteile auf anderes
Material als gemeint -- und das faellt niemandem auf. Die Buendelung
BESCHREIBT nur; sie legt eine zweite Datei daneben und laesst die erste in
Ruhe.

Wurzelobjekt mit dem Feld `buendel` (eine Liste) und zusaetzlich:

- `artifact_type` -- immer `"matrix_auto_cutter_shorts_buendel"`.
- `schema_version` -- immer `"1.0"`.
- `video_name` -- `<AUFNAHME>`.
- `kandidaten_gesamt` -- Ganzzahl, die Zahl der Eintraege in `buendel`.
  Sie muss der Zahl der Kandidaten in `kandidaten.json` gleichen.
- `gruppen_gesamt` -- Ganzzahl, die Zahl verschiedener `gruppe`-Werte.
- `vorauswahl_groesse` -- Ganzzahl, immer `15`. So viele Gruppen traegt die
  Vorauswahl. Hat die Aufnahme weniger als 15 Gruppen, steht hier trotzdem
  `15`, und alle Gruppen sind vorausgewaehlt.
- `modell` -- das Modell, mit dem dieser Lauf gefahren wurde. Es wird dir
  beim Start genannt. Ohne dieses Feld laesst sich spaeter nicht mehr sagen,
  welches Modell welche Buendelung vorgeschlagen hat.
- `gebuendelt_am` -- Zeitpunkt in ISO-Form mit Zeitzone.

Je Kandidat GENAU EIN Eintrag in `buendel`, ueber `index` zugeordnet:

- `index` -- Ganzzahl. Muss in `kandidaten.json` vorkommen. Jeder Index von
  dort kommt genau einmal vor, keiner doppelt, keiner zusaetzlich.
- `projekt` -- Zeichenkette: das Handelsobjekt oder das Thema, um das es
  geht. Zum Beispiel `Bitcoin`, `XRP`, `Hyperliquid`, `Marktbreite`,
  `Meta`. Es gibt KEINE feste Liste -- du waehlst die Bezeichnungen selbst.
  Aber du haeltst die Schreibweise ueber alle Eintraege gleich: `Bitcoin`
  und `BTC` nebeneinander machen aus einem Projekt zwei.
- `thema` -- kurze Zeichenkette: die konkrete Aussage, nicht das Projekt.
  `Bitcoin` ist ein Projekt, `Ruecklauf auf 92k vor dem naechsten Bein`
  ist ein Thema.
- `gruppe` -- Ganzzahl ab 1.
- `rang` -- Ganzzahl ab 1, INNERHALB der Gruppe. Eine Gruppe mit drei
  Kandidaten traegt die Raenge 1, 2, 3 -- jeden genau einmal.
- `empfohlen` -- Wahrheitswert. Je Gruppe steht er bei GENAU EINEM Eintrag
  auf wahr, bei allen anderen auf falsch. Der empfohlene Eintrag ist der mit
  `rang` 1.
- `gruppen_rang` -- Ganzzahl ab 1, EINDEUTIG ueber ALLE Gruppen hinweg.
  Nicht zu verwechseln mit `rang`: `rang` ordnet die Kandidaten INNERHALB
  einer Gruppe, `gruppen_rang` ordnet die GRUPPEN untereinander. Alle
  Kandidaten derselben Gruppe tragen denselben `gruppen_rang`. Bei 47
  Gruppen werden die Werte 1 bis 47 vergeben, jeder genau einmal.
- `vorauswahl` -- Wahrheitswert. Wahr bei allen Kandidaten der Gruppen mit
  `gruppen_rang` 1 bis 15, falsch bei allen uebrigen. Hat die Aufnahme
  weniger als 15 Gruppen, ist er ueberall wahr.
- `begruendung` -- nicht leer. Beim empfohlenen Kandidaten: warum er der
  beste seiner Gruppe ist. Bei den uebrigen: warum er zuruecksteht.

## Regeln fuer die Gruppenbildung

- **In eine Gruppe gehoert, was dieselbe Aussage oder denselben Moment
  trifft -- nicht, was dasselbe Projekt betrifft.** Zehn
  Bitcoin-Kandidaten koennen zehn Gruppen sein. Wenn der Sprecher fuenfmal
  ueber Bitcoin spricht und dabei fuenf verschiedene Dinge sagt, sind das
  fuenf Entscheidungen, nicht eine.
- **Kandidaten, die sich zeitlich ueberlappen, gehoeren FAST IMMER in
  dieselbe Gruppe.** Zwei Ausschnitte, die dasselbe Stueck Ton benutzen,
  zeigen fast immer dasselbe Material. Willst du sie trennen, musst du in
  der `begruendung` beider ausdruecklich sagen, warum die Ueberlappung hier
  ausnahmsweise nichts bedeutet.
- **Ein Kandidat mit `laengere_fassung_von` gehoert IMMER in dieselbe Gruppe
  wie sein Gegenstueck.** Das Feld sagt woertlich, dass es sich um dasselbe
  Material in laengerer Fassung handelt. Keine Ausnahme.
- **Zwei Kandidaten, die dasselbe Szenario aus verschiedenen Blickwinkeln
  zeigen, gehoeren zusammen.** Der Sprecher stellt oft zwei oder drei
  moegliche Verlaeufe nebeneinander -- "geht es hoch, dann ...", "faellt es
  dagegen, dann ...". Das ist EIN Gedanke mit mehreren Aesten, nicht mehrere
  Gedanken.
- **Eine Gruppe darf aus einem einzigen Kandidaten bestehen.** Ein
  Kandidat, der allein steht, wird nicht kuenstlich angelagert. Dann traegt
  er `rang` 1 und `empfohlen` wahr.
- **Der Rang innerhalb der Gruppe folgt der Kriteriendatei**: welcher
  Ausschnitt steht am besten fuer sich, welche Grenzen sitzen sauber, wie
  liegt die Laenge zur Vorgabe. Die YAML-Datei sagt, worauf es dabei
  ankommt -- lies sie und wende sie an.
- **ZWISCHEN Gruppen wird gerangt -- aber nur nach der Staerke des
  Ausschnitts, nie nach der Wichtigkeit des Themas.** Siehe den naechsten
  Abschnitt. Welches Thema es wert ist, veroeffentlicht zu werden,
  entscheidet weiterhin der Nutzer; du sagst ihm nur, in welcher Reihenfolge
  er hinsehen soll.

## Die Gruppennummern

Vergib die Nummern zuletzt, wenn alle Gruppen stehen. Ordne die Gruppen

1. nach `projekt`, alphabetisch aufsteigend, und
2. innerhalb desselben Projekts chronologisch nach dem fruehesten
   `start_ms` ihrer Kandidaten,

und nummeriere sie in dieser Reihenfolge von 1 an lueckenlos durch. So
liegen im Ergebnis die Gruppen eines Projekts beieinander und darin in der
Reihenfolge, in der die Aufnahme sie bringt.

Alle Kandidaten einer Gruppe tragen dasselbe `projekt`. Waehle das Projekt
also so, dass es fuer die ganze Gruppe stimmt.

## Die Vorauswahl: `gruppen_rang` und `vorauswahl`

Wozu das gut ist -- woertlich: **Der Nutzer veroeffentlicht 4 bis 10 Shorts
je Aufnahme und hat dafuer 48 Stunden Zeit.** Danach ist die Aufnahme fuer
Shorts wertlos. 47 Gruppenentscheidungen sind in diesem Fenster zu viel. Die
Vorauswahl nennt ihm die 15 Gruppen, bei denen es sich zuerst lohnt
hinzusehen.

Die Vorauswahl ist eine **Lesereihenfolge, keine Aussortierung.** Es faellt
nichts weg: jeder Kandidat bleibt in `kandidaten.json`, jede Gruppe bleibt in
`buendel.json`, und die Urteilsseite haelt die uebrigen Gruppen einen Klick
entfernt bereit. Du entscheidest nicht, was der Nutzer sehen darf, sondern
womit er anfaengt.

**Wonach du rangst.** Nach der Staerke des BESTEN Kandidaten der Gruppe --
also des empfohlenen, des mit `rang` 1 --, gemessen an der Kriteriendatei.
Drei Fragen, dieselben, nach denen du auch innerhalb der Gruppe rangst:

1. Steht der Ausschnitt fuer sich? Versteht ihn jemand, der die Aufnahme
   nicht kennt und mitten hineinfaellt?
2. Sitzen die Grenzen sauber? Faengt er an einem Satzanfang an und hoert an
   einem Satzende auf, ohne angeschnittenes Wort und ohne toten Vorlauf?
3. Traegt er eine eigene Aussage? Sagt er etwas, das jemand behalten oder
   widersprechen kann -- oder plaetschert er nur mit?

**Wonach du ausdruecklich NICHT rangst.** Diese drei Dinge fliessen NICHT
ein, auch nicht als Nebengewicht:

- **Wie oft ein Thema vorkommt.** Zehn Bitcoin-Gruppen machen keine davon
  besser und keine schlechter. Ein Projekt, das nur einmal vorkommt, wird
  nicht zurueckgesetzt, damit die Vorauswahl bunter aussieht -- und ein
  Projekt, das oft vorkommt, wird nicht bevorzugt, weil es wohl wichtig sein
  muesse.
- **Wie lang der Ausschnitt ist.** Die Laenge gehoert in den Rang INNERHALB
  der Gruppe, wo sie gegen die Vorgabe der Kriteriendatei zaehlt. Zwischen
  Gruppen sagt sie nichts: ein starker Ausschnitt von 9 Sekunden steht ueber
  einem schwachen von 13.
- **An welcher Stelle der Aufnahme er liegt.** Nicht der Anfang ist besser,
  nicht das Ende. `start_ms` ordnet die Gruppennummern (siehe oben), den
  `gruppen_rang` ordnet es nicht.

**Wie du die Werte vergibst.** Bilde eine Reihenfolge ueber ALLE Gruppen und
vergib `gruppen_rang` 1 fuer die staerkste, 2 fuer die zweitstaerkste, und so
weiter bis zur letzten -- lueckenlos, jeden Wert genau einmal. Trage den Wert
bei JEDEM Kandidaten der Gruppe ein, nicht nur beim empfohlenen. Setze dann
`vorauswahl` bei den Kandidaten der Gruppen mit `gruppen_rang` 1 bis 15 auf
wahr und bei allen uebrigen auf falsch. Hat die Aufnahme weniger als 15
Gruppen, tragen alle `vorauswahl` wahr.

Der `gruppen_rang` ist von der Gruppennummer unabhaengig: `gruppe` folgt
Projekt und Zeit, `gruppen_rang` folgt der Staerke. Gruppe 1 kann
`gruppen_rang` 30 tragen, Gruppe 44 den `gruppen_rang` 1.

## Bevor du schreibst: pruefe selbst

Gehe die Datei durch, bevor du sie ablegst. Diese sieben Punkte werden nach
deinem Lauf maschinell geprueft, und jede Abweichung laesst die Stufe
scheitern:

1. Jeder Index aus `kandidaten.json` kommt in `buendel.json` vor.
2. Kein Index in `buendel.json`, den es in `kandidaten.json` nicht gibt.
3. Je Gruppe genau ein `empfohlen` auf wahr.
4. Je Gruppe die Raenge 1 bis n, jeder genau einmal.
5. Jedes Paar mit `laengere_fassung_von` in derselben Gruppe.
6. `gruppen_rang` lueckenlos von 1 bis zur Gruppenzahl, jeder Wert genau
   einmal, und alle Kandidaten einer Gruppe mit demselben Wert.
7. Genau `min(15, Gruppenzahl)` Gruppen mit `vorauswahl` wahr, und zwar
   genau die mit den kleinsten `gruppen_rang`-Werten -- keine
   vorausgewaehlte Gruppe darf einen groesseren `gruppen_rang` haben als
   eine nicht vorausgewaehlte.

## Was dieser Lauf NICHT tun darf

- Code oder Tests aendern.
- `git commit`, `git push` oder sonst den Arbeitsbaum umschreiben.
- Das Laufwerk `F:` beruehren. Die Buendelung liest nur Text und braucht es
  nicht.
- Urteilsdateien anfassen -- weder lesen noch schreiben noch verschieben.
  Deine Buendelung soll nicht davon abhaengen, was der Nutzer schon
  entschieden hat.
- `kandidaten.json` oder eine `kandidaten-lauf<N>.json` aendern.
- Eine vorhandene `buendel.json` ueberschreiben. Gibt es sie schon, halte an
  und melde das.

## Wenn du nicht weiterkommst

Halte an und melde. Ein Lauf, der eine Eingabe nicht findet oder eine
vorhandene `buendel.json` antrifft, endet mit einer Meldung und ohne Datei.
Ein halbes Ergebnis ist schlechter als keines: es sieht von aussen fertig
aus, und der Nutzer trifft seine Entscheidungen dann auf einer Buendelung,
die nur die Haelfte der Kandidaten kennt.
