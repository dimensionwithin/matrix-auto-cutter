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
- **ZWISCHEN Gruppen wird NICHT gerangt.** Du sagst nicht, welches Thema
  wichtiger ist als ein anderes. Welches Thema es wert ist, veroeffentlicht
  zu werden, entscheidet der Nutzer -- deine Arbeit ist, ihm je Thema den
  besten Ausschnitt hinzulegen, nicht die Themen zu sortieren.

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

## Bevor du schreibst: pruefe selbst

Gehe die Datei durch, bevor du sie ablegst. Diese fuenf Punkte werden nach
deinem Lauf maschinell geprueft, und jede Abweichung laesst die Stufe
scheitern:

1. Jeder Index aus `kandidaten.json` kommt in `buendel.json` vor.
2. Kein Index in `buendel.json`, den es in `kandidaten.json` nicht gibt.
3. Je Gruppe genau ein `empfohlen` auf wahr.
4. Je Gruppe die Raenge 1 bis n, jeder genau einmal.
5. Jedes Paar mit `laengere_fassung_von` in derselben Gruppe.

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
