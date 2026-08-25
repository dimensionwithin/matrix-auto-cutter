# Auftragstext: Shorts-Zerlegung

## Rolle und Ergebnis

Du zerlegst eine gerenderte Aufnahme in Short-Kandidaten. Du liest das
Transkript der Aufnahme und die geltende Kriteriendatei, schlaegst daraus
Ausschnitte vor, schreibst sie als JSON-Datei und begruendest jede
Entscheidung in einem Bericht, der die Aufnahme lueckenlos abdeckt. Du
schreibst keinen Code, aenderst keine bestehende Datei und schneidest kein
Video -- die beiden Dateien, die du anlegst, sind das ganze Ergebnis.

## Platzhalter

`<AUFNAHME>` ist der Name der Aufnahme, zum Beispiel `2026-08-21 10-46-08`.
Er wird dir beim Start genannt. Alle Pfade unten sind relativ zur
Repo-Wurzel.

`<N>` ist die Nummer dieses Laufs. Vorgabe: 1.

## Eingaben

- `labels\repeat\shorts-kriterien.yaml` -- die inhaltliche Vorgabe. **Lies
  sie vollstaendig.** Dieser Auftragstext gibt ihren Inhalt NICHT wieder,
  sondern verweist nur auf sie: stuenden die Kriterien auch hier, veralteten
  zwei Fassungen nebeneinander, und du wuesstest nicht, welche gilt. Was
  inhaltlich gilt, steht ausschliesslich in der YAML-Datei.
- `artefakte\repeat\shorts\<AUFNAHME>\transkript-rendered.json` -- das
  Transkript auf der gerenderten Achse.
- `artefakte\repeat\shorts\<AUFNAHME>\wortliste.json` -- Wortliste mit
  Zeitmarken und Interpunktion. **Fehlt diese Datei**, faellst du auf
  `artefakte\repeat\shorts\<AUFNAHME>\transkript-rendered.wav.json` zurueck
  UND vermerkst diesen Rueckfall ausdruecklich im Bericht, im Kopf unter den
  Eingaben.
- `artefakte\repeat\shorts\<AUFNAHME>\shorts-job.json` -- daraus
  `rendered_video.duration_ms`. Die lueckenlose Karte muss bis zu diesem
  Wert reichen, nicht nur bis zum letzten Wort des Transkripts.

## Erste Handlung: Fassungspruefung

Lies die Kopfzeile von `labels\repeat\shorts-kriterien.yaml` und nenne die
dort stehende Fassung woertlich im Bericht. **Ist sie aelter als 0.8, halte
an und zerlege nicht.** Melde stattdessen die vorgefundene Fassung.

## Ausgabe: die Kandidatendatei

Ziel: `artefakte\repeat\shorts\<AUFNAHME>\kandidaten-lauf<N>.json`.

Schreibe NICHT `kandidaten.json`. Diese Datei entsteht spaeter aus der
Zusammenfuehrung mehrerer Laeufe.

Wurzelobjekt mit dem Feld `kandidaten` (eine Liste) und zusaetzlich:

- `kriterien_fassung` -- die Kopfzeile der Kriteriendatei WOERTLICH, also
  z. B. `shorts-kriterien.yaml -- Fassung 0.8 (24. August 2026)`, nicht die
  Nummer allein. Grund: die Trefferquote gruppiert nach dieser
  Zeichenkette. Schreibt ein Lauf `"0.8"` und der naechste
  `"Fassung 0.8 (24. August 2026)"`, stehen zwei Gruppen fuer dieselbe
  Fassung nebeneinander, und der Vergleich zweier Laeufe wird wertlos.
- `achse` -- immer `"gerendert"`.
- `video_name` -- `<AUFNAHME>`.
- `video_dauer_ms` -- `rendered_video.duration_ms` aus `shorts-job.json`.
- `lauf` -- die Nummer `<N>`.
- `modell` -- das Modell, mit dem dieser Lauf gefahren wurde. Ohne dieses
  Feld ist eine spaetere Trefferquote je Modell wertlos: man kann dann nicht
  mehr sagen, welches Modell welche Kandidaten vorgeschlagen hat.

Je Kandidat in `kandidaten`:

- `index` -- Ganzzahl, je Datei nur einmal vergeben.
- `start_ms`, `end_ms` -- Ganzzahlen, `end_ms` groesser als `start_ms`.
- `titel` -- nicht leer.
- `begruendung` -- nicht leer.
- `sicherheit` -- genau einer der Werte `hoch`, `mittel`, `niedrig`.
- `enthaelt` -- Liste von Indizes anderer Kandidaten dieser Datei; leere
  Liste, wenn nichts enthalten ist.

Zusaetzlich je Kandidat, wo zutreffend. Diese Felder werden vom
Bau ignoriert (`parse_candidates` liest nur die Pflichtfelder) und
koennen nichts kaputt machen; sie sind fuer den Menschen und fuer die
spaetere Auswertung da:

- `polarisierend` -- Wahrheitswert. Die Kriteriendatei verlangt ihn
  ausdruecklich unter `polarisierung.was_daraus_folgt`: die Zahl ist
  als Beobachtung nuetzlich, taugt aber nicht als Filter. Verlass dich
  nicht darauf, dass sie einen Kandidaten rettet oder verwirft.
- `laengere_fassung` -- Ganzzahl oder Objekt mit `start_ms`/`end_ms`.
  Verlangt von `kuerzestes_was_noch_fuer_sich_steht.folge`: bei
  verschachtelten Moeglichkeiten sind die kurzen Fassungen die
  Vorschlaege, die lange kommt hier mit.
- `dauer_ms` -- Ganzzahl, `end_ms` minus `start_ms`.
- `erstes_wort`, `letztes_wort` -- die Woerter an den Grenzen, woertlich
  aus der Wortliste. Sie machen im Bericht nachvollziehbar, wo die
  Grenze wirklich liegt.

**`enthaelt` nimmt AUSSCHLIESSLICH Ganzzahlen. Objekte darin lassen den Bau
mit `candidates_unreadable` abbrechen.** Kein Objekt, kein Text, keine
verschachtelte Liste -- nur Zahlen. Ein Eintrag darf nicht auf den eigenen
`index` zeigen und auf keinen Index, den es in dieser Datei nicht gibt.

## Ausgabe: der Begleitbericht

Ziel: `artefakte\repeat\<auftragsname>\BERICHT-<datum>.md`.

Er traegt eine **lueckenlose Karte** der Aufnahme: eine Tabelle aller
Abschnitte von 0 ms bis `video_dauer_ms`, ohne Loch und ohne Ueberlappung.
Das Ende eines Abschnitts ist der Anfang des naechsten. Jeder Abschnitt
traegt entweder einen Kandidaten oder einen **Grund**, warum er verworfen
wurde. Der Nutzer sichtet das Video nicht selbst -- er liest die Karte und
sieht gezielt in die Luecken. Ein Abschnitt ohne Grund ist ein Fehler.

## Selbstauskuenfte am Ende des Berichts

- Laufzeit der Aufnahme in ms.
- Zahl der Segmente der Quelle.
- Zahl der Woerter der Quelle.
- Der Satz: `Kein Code geaendert, kein Commit, kein Zugriff auf F:.`
- Die Punktdichte der Aufnahme: Zahl der Woerter, deren Text auf `.`,
  `!` oder `?` endet, und ihr Anteil an allen Woertern. Dazu dieselbe
  Zahl fuer alle Satzzeichen einschliesslich Komma.
- Dieselben beiden Anteile getrennt fuer die erste und die zweite
  Haelfte der Aufnahme.

Diese vier Zahlen kosten dich nichts -- die Wortliste liegt vor -- und
sind spaeter nicht mehr nachholbar. Ohne sie laesst sich ein magerer
Lauf nicht von einer Aufnahme unterscheiden, auf der das staerkste
Kriterium schlicht kein Material hatte.

## Der Nachschlaglauf

Ein zweiter Lauf wird bei Bedarf von Hand gefahren. **Er darf den ersten
nicht kennen.** Lies keine vorhandene `kandidaten-lauf1.json` -- auch nicht
"nur zum Abgleich", auch nicht, um Doppelungen zu vermeiden. Zusammengefuehrt
wird spaeter und von anderer Hand.

Begruendung, aus `zerlegung_laeuft_zweimal` in der Kriteriendatei: Zwei
unabhaengige Zerlegungen derselben Aufnahme fanden je acht Kandidaten, davon
nur drei dieselben -- und ausgerechnet die gemeinsamen wurden vom Nutzer
ueberwiegend abgelehnt. Uebereinstimmung beider Laeufe sagt also nichts ueber
Qualitaet. Der Gewinn liegt allein darin, dass der zweite Lauf etwas anderes
sieht. Kennt er den ersten, faellt genau das weg.

## Die zweite Haelfte der Aufnahme

Die Kriteriendatei vermerkt unter `bekannte_grenzen`, dass die Punktdichte
ueber die Laufzeit wegbricht: gemessen 5,4 % im ersten Viertel gegen 0,4 % im
letzten, so dass `interpunktion_traegt_die_grenzen` im hinteren Teil einer
Aufnahme praktisch nicht zur Verfuegung steht. Dieselbe Ursache trifft die
Vokabelvorgabe, die nur im ersten Verarbeitungsfenster wirkt.

**Weisung: Behandle die zweite Haelfte der Aufnahme deswegen NICHT duenner.**
Der Wegfall betrifft das Werkzeug, nicht das Material. Wo die Interpunktion
fehlt, traegt allein der Textsinn -- setze die Grenzen dort nach dem Sinn und
melde die Unsicherheit ueber `sicherheit`, statt weniger Kandidaten
vorzuschlagen. Eine im hinteren Teil sichtbar abfallende Kandidatendichte ist
ein Befund, den du im Bericht begruenden musst, kein zulaessiges Ergebnis
aus Bequemlichkeit.

## Was dieser Lauf NICHT tun darf

- Code oder Tests aendern.
- `git commit`, `git push` oder sonst den Arbeitsbaum umschreiben.
- Das Laufwerk `F:` beruehren. Die Zerlegung liest nur Text und braucht es
  nicht.
- Urteilsdateien anfassen.
- Eine vorhandene `kandidaten-lauf<N>.json` ueberschreiben. Gibt es sie
  schon, halte an und melde das.

## Wenn du nicht weiterkommst

Halte an und melde. Ein Lauf, der eine Eingabe nicht findet, eine
Fassung aelter als 0.8 vorfindet oder eine vorhandene
`kandidaten-lauf<N>.json` antrifft, endet mit einer Meldung und ohne
Datei. Ein halbes Ergebnis ist schlechter als keines: es sieht von
aussen fertig aus.
