# Orchestrator-Uebergabe — Matrix Auto Cutter, Shorts-Produktionslinie
Stand: 24. August 2026, HEAD `f955792` auf `master`, gepusht, Arbeitsbaum leer.

Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-21.md` ab. Das
Nachschlagewerk dazu ist `BESTAND-2026-08-24.md` — dort stehen alle
Fundstellen, Konstanten, Modulsignaturen und Dateiformate. Diese Uebergabe
nennt keine Zeilennummern; sie verweist auf den Bestand.

---

## 0 — Nichts ist dringend

Anders als bei der letzten Uebergabe liegt nichts ungesichert herum. Sechs
Commits seit `9ba1f63`, alle gepusht, `git status --porcelain` leer. Der
Punkt, zu dem zurueckgekehrt werden kann, ist ein funktionierender.

Frag beim ersten Kontakt trotzdem nach dem aktuellen `git log --oneline -3`
und `git status --porcelain` — zwischen dem Schreiben dieses Dokuments und
dem ersten Auftrag kann Zeit vergehen.

---

## 1 — Rolle und Arbeitsweise

**Du baust nicht selbst.** Du liest Berichte, rechnest Zahlen nach, triffst
Entscheidungen und schreibst Auftraege fuer Claude-Code-Fenster. Der Nutzer
schickt sie ab und bringt die Berichte zurueck.

**Bei jedem Auftrag nennst du:** Modell, Denktiefe, Berechtigungsgrenzen und
die Begruendung dafuer.

### 1.1 Der Aufbau eines Auftrags

Diese Form hat sich ueber zwei Wochen bewaehrt. Abweichungen kosten
Umlaeufe.

```
AUFTRAG: <name>

ZWECK                     Ein Absatz. Wozu, nicht wie.
ERWARTETER REPO-ZUSTAND   HEAD, Branch, was im Arbeitsbaum liegt.
                          "Weicht es ab, HALTE AN."
BEKANNT UND HARMLOS       Alles, was auffallen wird und kein Grund zum
                          Anhalten ist. Anhalten nur bei Abweichungen,
                          die HIER NICHT genannt sind.
VERBOTEN                  Sperrliste plus das, was diesmal besonders gilt.
GEGEBEN                   Was nicht nachzupruefen ist, mit Herkunft.
TEIL 1..n                 Die Arbeit, in Schritten.
PRUEFSTEINE               Mit ERWARTETEM ISTWERT, nicht nur "pruefe X".
GATES                     pytest / ruff / mypy mit Sollwerten.
BERICHT                   Pfad unter artefakte\repeat\<auftragsname>\.
ANGEHALTEN                "Trifft eine Annahme nicht zu, ist Melden
                          richtig und Weiterbauen falsch."
```

**Pruefsteine tragen den erwarteten Istwert.** „Pruefe, ob alle Werte gerade
sind" ist schwach. „Erwartet: 0 Verstoesse bei 21747 geprueften Werten, nenne
beide Zahlen" ist stark — das Fenster merkt selbst, wenn etwas nicht stimmt.

**Anhaltebedingungen muessen erfuellbar sein.** Zwei Bedingungen, die
einander ausschliessen, fuehren zu einem berechtigten Halt und einem
verlorenen Umlauf. Das ist mir zweimal passiert (siehe Abschnitt 5).

**Bei parallel geschickten Auftraegen nennen sie einander** in der
Harmlos-Liste, mit den Dateien, die dort angefasst werden. Sonst haelt ein
Fenster an, weil sich der Boden unter ihm bewegt — voellig zu Recht.

### 1.2 Freigaben

- **Repo** lesend und schreibend, sofern der Auftrag etwas aendert.
- **`F:\MatrixMarketAutoEdit\` lesend ist Pflicht, sobald `build.py` laeuft.**
  Ein Auftrag, der einen Bau verlangt und `F:` sperrt, widerspricht sich und
  haelt an. Das ist dreimal passiert.
- **`F:\ShortsQuellen\` lesend** fuer Avatardatei und Cursorprotokoll.
- **`%LOCALAPPDATA%\DimensionWithin\` lesend** fuer Proposal und Journal.
- **`P:\AI\whisper-data\` lesend** nur bei Transkriptionsfragen.
- **Nie `F:` als Ganzes** — dort liegen Terabyte Videomaterial.
- **`%APPDATA%\obs-studio\` steht auf der Sperrliste.** Wo Lesen noetig ist,
  ausdruecklich und nur lesend freigeben.

### 1.3 Sperrliste

`cut_proposal.py`, `intro.py`, `outro.py`, `protection.py`, `render.py`,
`loudness.py`, `event_lag.py`, `product_runner.py`, `review_app.py`,
`review.py`, `approval.py`, `src\matrix_auto_cutter\repeat\*`, `native\**`,
`START-*.cmd`, `START-ALLES.ps1`, `%APPDATA%\obs-studio\**`

**Achtung bei Testdateien:** Ein Test kann in einer Datei liegen, die wegen
einer anderen Baustelle gesperrt ist, und trotzdem das Thema des aktuellen
Auftrags pruefen. Das fuehrt zu einem Zielkonflikt zwischen „keine
Ausfaelle" und der Sperre. Sperre Produktdateien, nicht pauschal ihre Tests.

### 1.4 Umgang mit dem Nutzer

- **Hoechstens zwei Fragen, ganz oben.** Er beantwortet sie sofort; lange
  Vorreden kosten ihn Zeit.
- **Er beurteilt das Bild und den Ton, du die Zahlen.** Wenn eine
  Entscheidung an Geschmack haengt, bau zwei Fassungen und lass ihn hoeren.
  Das hat in dieser Sitzung viermal besser funktioniert als jede Messung.
- **Kurz fassen.** Er hat mehrfach gesagt, dass ihm die Texte zu lang sind.
- **Behaupte nichts ueber Code, den du nicht gesehen hast.** Formuliere
  „pruefe, ob X gilt", nicht „X gilt".

---

## 2 — Stand

**Gebaut und verdrahtet:** Stufen 0, 1, 2, 3a, 3b, 4, 5a, 5b, 5c.
**Bewusst nicht verdrahtet:** Stufe 5d (Endcard) — eine Endcard zerstoert die
Wiederholschleife, auf die die Linie zielt. Modul existiert vollstaendig mit
eigenem CLI.
**Kein Modul:** die Kandidatensuche. Das ist Absicht — dort wird geurteilt.

**Die sechs Commits seit `9ba1f63`:**

| Commit | Inhalt |
|---|---|
| `d7823e1` | Stufe 3b: Ortsbezug berichtigt und an 3a/Bau verdrahtet |
| `d433f42` | Doku: Ablagekonvention und Umgebungsstand nachgezogen |
| `2b37fd5` | Stufe 3b: Trittzone, Reserve und Fahrtdauer auf den abgenommenen Stand |
| `fc2c12d` | Stufe 3b: linken Anschlag freigegeben, Trittzone auf 460 |
| `927bdd9` | Tonseite der Kandidatengrenzen abgeschlossen |
| `ef014cf` | Kriterien auf Fassung 0.8 |
| `f955792` | verwaiste .bak-Staende als eigene Fassungen gesichert |

**Der erste vollstaendige Durchlauf ist gelaufen.** Aufnahme
`2026-08-21 10-46-08`, 584,9 s Material, niemand hatte sie vorher gesehen.
Ergebnis: elf Shorts, davon neun vom Nutzer als einwandfrei abgenommen.

---

## 3 — Getroffene Entscheidungen samt Begruendung

Diese Werte sind gemessen oder vom Nutzer abgenommen. Sie ohne Anlass zu
drehen ist teuer.

### 3.1 Stufe 3b, Mausverfolgung

| Wert | Begruendung |
|---|---|
| `X_OFFSET_MIN_3B = 0` | Stand bei 482, weil in den Quellspalten 0–481 die OBS-Quelle `AVATAR` liegt. Der Nutzer hat die Sperre am 22.8. ausdruecklich aufgehoben: ein doppelter Avatar stoert ihn nicht, die Chartinhalte sind wichtiger. Wirkung: Zeiger im Bild bei kandidat-02 von 0 auf 601 von 601 Frames. |
| `TRITTZONE_RAND_PX = 460` | Kleinster gemessener Wert, bei dem die Rueckfahrt nach links wieder ausloest. **Groesserer Rand = schmalere Trittzone = empfindlicher.** Bei 700 waeren es 11 Linksfahrten und 16 Flatterfahrten. |
| `RESERVE_PX = 250` | Vorlauf. Zusammen mit dem Rand steht der Zeiger nach einer Fahrt 710 px von der Austrittskante. |
| `FAHRT_MIN_MS = 250`, `FAHRT_MAX_MS = 450` | Untergrenze ist nicht Geschmack, sondern die Bildrate: bei 832 px Spanne sind das 46 px je Frame. |
| `AUSTRITT_VERZOEGERUNG_MS = 300`, `MINDESTVERWEILDAUER_MS = 1000` | Gegen kurze Wischer bzw. Zappeln. |
| Anker | **`csv_first_row_at`**, nicht `recording_started_at` — die beiden liegen 158 ms auseinander. `AUFNAHMESTART_VERSATZ_MS = 0`, gemessen. |
| Ortsabbildung | Das Cursorprotokoll traegt **Desktop-Koordinaten**, die OBS-Leinwand zeigt ein skaliertes Fensterabbild. Ohne Umrechnung liegt jede Fahrt bis zu 214 px daneben. Konstanten stehen im Modul mit dokumentierter Herkunft. |

**Cursor nicht im Bild** (zweiter Monitor liegt links) → Ziel ist der linke
Anschlag, dort halten. Weisung des Nutzers: nicht zur Mitte zurueckspringen,
nicht schnappen; die Maus kommt immer von links wieder herein.

**Keine Umkehrsperre.** War entworfen, vom Nutzer abgelehnt: sie macht die
Kamera fuer Richtungswechsel taub. Nicht wieder vorschlagen ohne neuen Anlass.

### 3.2 Die Tonseite der Kandidatengrenzen

Entstanden in fuenf aufeinanderfolgenden Auftraegen. Die Kette steht
zusammenhaengend in Abschnitt 6 des Bestands.

- **Wortraender werden am Ton gemessen, nicht an den Whisper-Marken.** Diese
  liegen systematisch 35–65 ms zu frueh; bei `pause_after_ms = 0` meldet
  whisper keine Pause, wo hoerbar 120–160 ms Abstand sind.
- **Die Endgrenze wird am Anfang des naechsten Wortes gedeckelt.** Ohne diese
  Schranke lief die Suche zwei Woerter zu weit.
- **`MIN_NACHKLANG_MS = 60`** — ein Wort, das im selben Augenblick endet wie
  das Video, wirkt beim Schleifensprung abgeschnitten, auch wenn es
  vollstaendig ist.
- **`TON_EINBLENDE_MS`/`TON_AUSBLENDE_MS = 40`**, per `afade` direkt nach dem
  `atrim` in `chart_crop.py`. Drei Anlaeufe an der Punktsuche haben gezeigt,
  dass es zwischen zwei Woertern fluessiger Rede keinen sauberen Punkt gibt.
  Die Blende liegt hinter der Lautheitsnormalisierung — gemessene
  LUFS-Abweichung 0,00 bis 0,02.

**Was nicht mehr versucht werden sollte:** Weitere Schwellenwerte zu drehen.
Bei kandidat-06 wurde die Luecke zwischen zwei Woertern mit **null
Millisekunden** gemessen. Dort existiert kein richtiger Schnittpunkt; jeder
Schnitt kappt entweder die letzte Silbe oder nimmt das naechste Wort mit. Die
Antwort darauf steht jetzt in den Kriterien, nicht im Code.

### 3.3 Kriterien, Fassung 0.8

Fuenf Aenderungen, alle aus den Urteilen des Nutzers abgeleitet:

1. **Der Nachbarsatz darf mit, wenn er die Aussage staerker macht.** Alle drei
   Ablehnungen des Nutzers waren Schnittfehler dieser Art, keine
   Inhaltsfehler. Haeufigster Fall: die eingeloeste eigene Ansage.
2. **Ein eingeloester Rueckbezug ist erlaubt**, ein nicht eingeloester bleibt
   verboten.
3. **Zahlen- und Levelketten tragen.** Ausgeschlossen sind Rechnungen, nicht
   genannte Kursmarken. Ein Zerlegungslauf hatte deshalb die halbe Aufnahme
   uebersprungen; der Nutzer nahm alle vier Kandidaten aus dieser Zone an.
4. **Neu: eine Grenze darf nicht dort liegen, wo zwei Woerter einander
   beruehren.** Ausschluss bei null Millisekunden, nicht Forderung nach einer
   Sprechpause — das ist ein Unterschied, an dem eine erste Fassung
   gescheitert ist.
5. **Die Zerlegung laeuft zweimal und wird zusammengefuehrt.**

### 3.4 Die doppelte Zerlegung — die wichtigste Verfahrensentscheidung

Zwei unabhaengige Laeufe auf demselben Transkript fanden je acht Kandidaten,
davon **nur drei dieselben**. Der Nutzer nahm aus Lauf A fuenf von fuenf
eigenen an, aus Lauf B vier von fuenf, und von den drei gemeinsamen nur einen
von drei.

**Ein Lauf allein: 6 bzw. 5 brauchbare Kandidaten. Beide zusammen: 10.**

Und: Einigkeit beider Laeufe sagt nichts ueber Qualitaet. Der zweite Lauf darf
den ersten nicht kennen — er kostet nichts als einen Modellaufruf auf einem
Transkript, das ohnehin dasteht.

---

## 4 — Betriebsfallen

Jede einzelne hat schon Zeit gekostet.

**Product Runner nach jeder Codeaenderung neu starten.** Er laedt den Code
beim Start. Im `runner.log` zaehlt nur `Runner startet.` mit Statuscode
`runner_starting` — die Zeile `Product Runner wird im Hintergrund gestartet.`
beweist nichts, und `laeuft bereits` heisst, der alte Code laeuft weiter. Am
9.8. lief er dadurch drei Stunden auf altem Stand.

**Tests nur ueber PowerShell**, immer `uv run python -m pytest`, **nie**
`uv run pytest`, **nie** ueber Git-Bash.

**Nach einem Fehlschlag nie neu transkribieren.** Die `.wav.json` steht noch
und kann direkt nachkonvertiert werden. Ein turbo-Lauf kostet die 1,27-fache
Echtzeit — eine Stunde Aufnahme sind gut 76 Minuten.

**Nie `git clean -xfd`.** Der Befehl loescht `artefakte\` mit, darunter die
whisper-Rohausgaben.

**Urteilsdateien nie anfassen.** Urteilszeit ist das einzige Artefakt, das
sich nicht neu erzeugen laesst.

**Gescheiterte Renderversuche lassen ihre Partialdatei liegen.** Das ist
Absicht. Im Zweifel ist das das fertige Video — vor dem Aufraeumen
hineinsehen.

**PowerShell 5.1 liest UTF-8 ohne `-Encoding UTF8` falsch** und schreibt per
Vorgabe **mit** BOM. Deshalb liest `load_offsets` inzwischen mit
`utf-8-sig` — `ausschnitt.json` ist die Datei, die von Hand geschrieben wird,
wenn eine Kurve danebenliegt.

**Doppelte Namen, Verwechslungsgefahr:**
- `MIN_PAUSE_MS` existiert zweimal: `loop_point` (250) und `level_cut` (100).
  Bei Auftraegen immer das Modul nennen.
- `ausschnitt.json` und `short.json` sind keine festen Formate, sondern
  Sidecars zum jeweiligen Ausgabedateinamen. Je Verzeichnisebene anderer
  Inhalt.
- `grenze_der_regel` steht zweimal in der Kriteriendatei.

**Zeilennummern in `level_cut.py` veralten schnell** — die Datei waechst
haeufig. Zeilenangaben aus aelteren Dokumenten bei jedem Bestand neu pruefen.

---

## 5 — Meine Fehler in dieser Sitzung

Ausdruecklich aufgeschrieben, damit sie sich nicht wiederholen. Alle vier
haben je einen Umlauf gekostet.

**Die Trittzone falsch herum gedreht.** Ein groesserer Rand macht die
Trittzone **schmaler** und die Kamera empfindlicher. Ich habe kleinere Werte
messen lassen und damit genau das Gegenteil geprueft. Der Sweep ging von 0 bis
300, waehrend die Antwort bei 460 lag.

**Eine eigene Regel im Nachtrag gestrichen.** Im ersten Auftrag stand „ist die
Pause null, bleibt die Grenze stehen". Mein Nachtrag ersetzte den Abschnitt
durch eine Tonmessung und loeschte die Regel dabei mit. Zwei Auftraege spaeter
musste sie wiederhergestellt werden.

**Aus einer Messreihe das Maximum genommen.** Beim Wortrandabstand lag der
Median bei 40 ms, das Maximum bei 390. Ich habe 390 gesetzt — mit dem
Ergebnis, dass die Konstante bei **allen 22 Grenzen** kollidierte und nie zum
Zug kam. Ein Wert, der immer in die Ausweichregel laeuft, ist kein Wert.

**Die Kriteriendatei aus dem Gedaechtnis diktiert.** Zwei erfundene Schluessel
und eine erfundene Ebene. Das Fenster fand drei Widersprueche und hielt an.
**Lehre: Vor jeder Textvorgabe die Datei lesen lassen und die Struktur
zurueckmelden lassen.**

---

## 6 — Offene Punkte

**Die Verkettung** — der naechste Brocken, siehe Abschnitt 7.

**Das Veroeffentlichen** — eigenes Vorhaben, danach. **Nur Shorts.**
Longform-Upload und Thumbnails bleiben ausdruecklich draussen; der Nutzer hat
das als spaetere Spielerei eingeordnet. Fuer Shorts ist nichts gebaut; ob aus
der Longform-Kette des Product Runners etwas wiederverwendbar ist, ist
ungeprueft.

**Stillevorlauf-Rueckfall bei kandidat-08 und -09.** Beide haben eine lange
Stille vor dem ersten Wort; die Wortrandsuche findet dort keinen
Pausengrund-Bereich und faellt auf die aeltere Messung zurueck.
Verschiebungen von −910 und −540 ms sind analysiert, aber **nie gebaut und nie
gehoert**. Steht seit drei Berichten.

**Die +1-Frameabweichung ist verschwunden.** Sie tritt seit dem 21.8. in
keinem Baubericht mehr auf — durchgehend null. Ob das an einer Behebung liegt
oder an guenstigeren Framezahlen, ist nicht belegt. Die Pruefung existiert
unveraendert im Code.

**`UMGEBUNG.md` fehlt der gemessene turbo-Durchsatz:** 741,7 s Laufzeit fuer
584,9 s Audio, Faktor 1,27, CPU, 4 Threads, `-ml 120`, Vokabeldatei gezogen,
gemessen am 21.8. Das Dokument sagt an dieser Stelle noch, es gebe keine
vergleichbare Messung.

**Die Fassungsgeschichte der Kriteriendatei ist lueckenhaft.** Der Git-Verlauf
springt von 0.3 auf 0.7; die 0.6 existiert nur als `.bak`, seit `f955792`
versioniert. Kein Handlungsbedarf, aber niemand sollte vergeblich nach einem
Commit dafuer suchen.

**Vier Ordner mit zusammengeschobenen Pfadnamen** lagen im Wurzelverzeichnis
von `C:` (`UsersschanAppDataLocalTempmatrix-auto-cutter-repair-pytest` und
Varianten, je 0 Byte, geloescht). Die vermutete Stelle in den
`protection.py`-Tests liess sich **nicht** finden — alle Temp-Pfade dort
werden sauber ueber `tmp_path` gebildet. Herkunft ungeklaert, moeglicherweise
ausserhalb dieses Repos.

---

## 7 — Was als Naechstes: die Verkettung

Die Linie besteht aus **zwei automatischen Haelften mit menschlichen Toren
dazwischen**:

```
Aufnahme  ->  [auto]  ->  Schnittvorschlag
                             |  REVIEW-FENSTER DES NUTZERS
                          gerendertes Video
          ->  [auto]  ->  Transkript -> Kandidaten
                             |  URTEILE DES NUTZERS
          ->  [auto]  ->  fertige Shorts
                             |  Veroeffentlichen
```

Die erste Haelfte laeuft ueber den Product Runner. Die zweite ist gebaut, wird
aber von Hand aneinandergereiht.

**Drei Dinge fehlen**, protokolliert im Aufnahmebericht vom 21.8.:

1. **Die GUI fuer Stufe 0.** Sie liess sich nicht bedienen; `shorts-job.json`
   entstand per Einmalskript. Braucht einen kopflosen Weg.
2. **Eine Fortschrittsanzeige, die auf der Uhr steht.** Ein Fenster hat die
   Transkriptionsdauer aus gezaehlten Wartezyklen hochgerechnet und lag um
   Faktor 17 daneben. Ein Schritt, der unbeaufsichtigt laeuft, muss sagen
   koennen, wo er steht.
3. **Die Zerlegung als Modellschritt** — zweimal laufend, zusammengefuehrt.
   Das ist der einzige Schritt der Kette, der nicht deterministisch ist.

**Drei Eigenschaften braucht die Kette, die heute keine Stufe hat:**
wiederanlauffaehig, jeder Schritt idempotent, Fehlschlaege laut statt still.
Jede Betriebsfalle aus Abschnitt 4 ist ein Fall davon — heute befolgt der
Nutzer diese Regeln, weil er danebensitzt.

**Vorgehen:** Der Aufnahmebericht
`artefakte\repeat\shorts-lauf-21-08\BERICHT-2026-08-21.md` hat die Handgriffe
bereits protokolliert. Er ist die Spezifikation. Der erste Bauauftrag kann
daraus geschrieben werden, ohne dass noch etwas erhoben werden muss.

---

## 8 — Die Urteile als Messdaten

Der Nutzer will weiter selbst urteilen. Die Automatisierung der
Kandidatenauswahl ist ein spaeteres Ziel, kein aktuelles.

**Damit die Urteile spaeter etwas wert sind, muss ab jetzt bei jedem Lauf
mitgezaehlt werden, wie oft die Kriteriendatei seine Entscheidung getroffen
haette.** Das kostet nichts, wenn es von Anfang an mitlaeuft, und ist teuer
nachzuruesten. Bisher ist es nicht eingebaut.

Stand der Quote: Aufnahme vom 19.8. 29 von 33 angenommen; Aufnahme vom 21.8.
10 von 13. Die drei Ablehnungen der zweiten waren Schnittfehler, keine
Inhaltsfehler — daraus ist die 0.8 entstanden.
