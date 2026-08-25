# Orchestrator-Uebergabe — Matrix Auto Cutter, Shorts-Produktionslinie
Stand: 25. August 2026, HEAD `2855d5f` auf `master`, gepusht, Arbeitsbaum leer
bis auf die bekannte Datei `-`.

Dieses Dokument loest `ORCHESTRATOR-UEBERGABE-2026-08-24.md` ab. Das
Nachschlagewerk dazu ist `BESTAND-2026-08-25.md` — dort stehen Fundstellen,
Konstanten, Modulsignaturen und Dateiformate. Diese Uebergabe nennt keine
Zeilennummern; sie verweist auf den Bestand.

Was hier steht, stammt aus den Berichten dieser Sitzung unter
`artefakte\repeat\` und aus den in diesem Auftrag gelesenen Dateien. Jede Zahl
traegt ihre Quelle. Was nicht belegt ist, ist als **nicht belegt**
gekennzeichnet.

Frag beim ersten Kontakt nach dem aktuellen `git log --oneline -3` und
`git status --porcelain` — zwischen dem Schreiben dieses Dokuments und dem
ersten Auftrag kann Zeit vergehen.

---

## 1 — Rolle und Arbeitsweise

**Du baust nicht selbst.** Du liest Berichte, rechnest Zahlen nach, triffst
Entscheidungen und schreibst Auftraege fuer Claude-Code-Fenster. Der Nutzer
schickt sie ab und bringt die Berichte zurueck.

**Bei jedem Auftrag nennst du:** Modell, Denktiefe, Berechtigungsgrenzen und
die Begruendung dafuer.

### 1.1 Der Aufbau eines Auftrags

Diese Form hat sich ueber drei Wochen bewaehrt. Abweichungen kosten Umlaeufe.

```
AUFTRAG: <name>

ZWECK                     Ein Absatz. Wozu, nicht wie.
REPOSITORY-PRUEFUNG       `git rev-parse --show-toplevel` mit Erwartungswert.
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

**Der Auftrag nennt IMMER `git rev-parse --show-toplevel` mit
Erwartungswert.** Das ist neu und am 25.8. teuer gelernt worden: ein Fenster
stand im falschen Repository (dem YouTube-Projekt) und hat richtig angehalten.
Ohne diese Zeile haette es dort gearbeitet. Die Berichte dieser Sitzung
bestaetigen die Zeile als eingehaltene Praxis — `shorts-auswahl` vermerkt sie
"vor jedem Arbeitsschritt", ebenso `shorts-bau-probelauf` und
`arbeitskopie-seitendateien`.

**Pruefsteine tragen den erwarteten Istwert.** „Pruefe, ob alle Werte gerade
sind" ist schwach. „Erwartet: 0 Verstoesse bei 21747 geprueften Werten, nenne
beide Zahlen" ist stark — das Fenster merkt selbst, wenn etwas nicht stimmt.

**Ein Pruefstein, dessen Erwartung unerfuellbar ist, ist ein Fehler des
Auftraggebers, nicht des Fensters.** Zweimal passiert:

- `kette-bestandsaufnahme` sollte am Ende genau einen `git status`-Eintrag
  zeigen, naemlich den eigenen Bericht. Ein Bericht unter `artefakte\` kann
  dort nie erscheinen: `.gitignore:88` (`/artefakte/`) schliesst den Ordner
  aus. Das Fenster hat es richtig gemeldet, statt etwas zu erfinden.
- `shorts-bau-probelauf` und `arbeitskopie-seitendateien` sollten die Dauer
  der gebauten Shorts auf 50 ms gegen den **rohen** `end_ms - start_ms` der
  Bauliste pruefen. Gemessen wurden +377 bis +440 ms — und das voellig zu
  Recht, weil Stillevorlauf, Tonblende und Wortrandkollision die Grenzen
  absichtlich verschieben. Erst `shorts-bau-vollstaendig` hat gegen den
  **angepassten** Plan (`build_end_ms - build_start_ms` aus dem Baubericht)
  gemessen und kam auf hoechstens 13 ms. Die Toleranz gehoert gegen den
  angepassten Plan, nie gegen die rohen Kandidatengrenzen.

**Anhaltebedingungen muessen erfuellbar sein.** Zwei Bedingungen, die einander
ausschliessen, fuehren zu einem berechtigten Halt und einem verlorenen Umlauf.

**Der Commit des Vorauftrags wird zu TEIL 1 des Folgeauftrags**, statt einen
eigenen Umlauf zu kosten. So gefahren am 25.8.: `shorts-bau-probelauf` hat als
TEIL 1 den Commit `f5f7502` des Vorauftrags `shorts-auswahl` gesetzt und
gepusht, `shorts-bau-vollstaendig` als TEIL 1 den Commit `2855d5f` des
Vorauftrags `arbeitskopie-seitendateien`. Beide Male genau die vom Auftrag
genannten Pfade, `git show --stat HEAD` als Pruefstein.

**Bei parallel geschickten Auftraegen nennen sie einander** in der
Harmlos-Liste, mit den Dateien, die dort angefasst werden. Sonst haelt ein
Fenster an, weil sich der Boden unter ihm bewegt — voellig zu Recht. Am 24.8.
liefen `shorts-wortliste` und `zerlegung-auftragstext` so parallel; beide
haben den jeweils anderen Eintrag in `git status` als angekuendigt erkannt und
nicht angehalten.

### 1.2 Freigaben

- **Repo** lesend und schreibend, sofern der Auftrag etwas aendert.
- **`F:\MatrixMarketAutoEdit\` lesend ist Pflicht, sobald `build.py` laeuft.**
  Seit dem 25.8. auch **schreibend**, weil die Shorts dorthin gehen (Abschnitt
  4). Ein Auftrag, der einen Bau verlangt und `F:` sperrt, widerspricht sich
  und haelt an.
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
Auftrags pruefen. Sperre Produktdateien, nicht pauschal ihre Tests. Der
umgekehrte Fall ist am 24.8. aufgetreten: die Ursache der Datei `-` liegt in
`tests\repeat\test_cutcli.py`, das unter `tests\repeat\` und damit im
gesperrten Gebiet liegt — gefunden, benannt, nicht behoben.

### 1.4 Umgang mit dem Nutzer

- **Hoechstens zwei Fragen, ganz oben.** Er beantwortet sie sofort; lange
  Vorreden kosten ihn Zeit.
- **Er beurteilt das Bild und den Ton, du die Zahlen.** Wenn eine Entscheidung
  an Geschmack haengt, bau zwei Fassungen und lass ihn hoeren.
- **Kurz fassen.** Er hat mehrfach gesagt, dass ihm die Texte zu lang sind.
- **Behaupte nichts ueber Code, den du nicht gesehen hast.** Formuliere
  „pruefe, ob X gilt", nicht „X gilt".

---

## 2 — Stand der Linie

**Gebaut und verdrahtet:** Stufen 0, 1, 2, 3a, 3b, 4, 5a, 5b, 5c.
**Bewusst nicht verdrahtet:** Stufe 5d (Endcard) — eine Endcard zerstoert die
Wiederholschleife, auf die die Linie zielt. Modul existiert vollstaendig mit
eigenem CLI.
**Kein Modul:** die Kandidatensuche. Das ist Absicht — dort wird geurteilt.
Sie hat seit dem 24.8. aber einen verbindlichen Auftragstext, siehe unten.

**Die sechs Commits seit `f955792`:**

| Commit | Inhalt | Datei(en) |
|---|---|---|
| `f3c87e3` | Doku: Orchestrator-Uebergabe und Bestand vom 24.8. | `docs\repeat\` |
| `8581b3e` | Shorts: Wortliste mit Interpunktion kopflos erzeugbar | `shorts\wortliste.py` (142 Z.), `tests\test_shorts_wortliste.py` (112 Z.) |
| `fd740cd` | Shorts: Auftragstext der Zerlegung als eigene Vorgabe | `docs\repeat\ZERLEGUNG-AUFTRAGSTEXT.md` (169 Z.) |
| `a83a497` | Shorts: Urteile und Kandidaten des Sonnet-Laufs vom 21.8. gesichert | `labels\repeat\kandidaten-2026-08-21-lauf1-sonnet.json`, `labels\repeat\urteile-2026-08-21-lauf1-sonnet.json` |
| `f5f7502` | Shorts: Auswahl aus Urteilen samt Trefferquote | `shorts\auswahl.py` (460 Z.), `tests\test_shorts_auswahl.py` (252 Z.), `labels\repeat\trefferquote.json` |
| `2855d5f` | Shorts: Arbeitskopie nimmt Seitendateien mit | `shorts\build.py` (+9 Z.), `tests\test_shorts_build.py` (+94 Z.) |

Zeilen- und Dateiangaben aus `git show --stat` der jeweiligen Commits.

**Was damit neu in der Linie steht:**

- **`shorts\wortliste.py`** — Stufe 2 Teil 1b. Erzeugt
  `wortliste.json` je Aufnahmeordner aus der whisper-Rohausgabe, kopflos, mit
  Wiederanlauf (`--force`). Das ist das erste der beiden Einmalskripte vom
  21.8., das jetzt Produktcode ist. Probelauf: 1569 Woerter, 132.974 Byte,
  513 ms (`shorts-wortliste\BERICHT-2026-08-24.md`, TEIL 5).
- **`docs\repeat\ZERLEGUNG-AUFTRAGSTEXT.md`** — der Auftragstext der
  Zerlegung, 169 Zeilen, zwoelf Abschnitte. Er verweist auf die
  Kriteriendatei, statt sie wiederzugeben, verlangt eine Fassungspruefung
  (Halt unterhalb 0.8), schreibt nach `kandidaten-lauf<N>.json` statt
  `kandidaten.json` und verlangt die lueckenlose Karte samt vier
  Selbstauskuenften zur Punktdichte.
- **`shorts\auswahl.py`** — das fehlende Glied zwischen Urteil und Bau.
  `build.py` liest ausschliesslich `kandidaten.json` und kennt keine Urteile;
  ohne dieses Modul wuerde ein Bau alle Kandidaten bauen, auch die
  verworfenen. Es erzeugt `bauliste.json` (dasselbe Schema wie
  `kandidaten.json`, unveraendert an `build.py` uebergebbar) und schreibt den
  Trefferquote-Eintrag nach `labels\repeat\trefferquote.json` fort.
- **`build.py`, `ARBEITSKOPIE_BEGLEITER_SUFFIXE`** — die Arbeitskopie nimmt
  jetzt die Seitendatei `<stamm>.json` neben jeder kopierten Videodatei mit.
  Ohne diese neun Zeilen scheitert jeder Bau, dessen `--output-dir` auf einem
  anderen Laufwerk liegt als die Quellen.

**Der Auftragstext fuer die Zusammenfuehrung zweier Zerlegungslaeufe fehlt
weiterhin** (`zerlegung-auftragstext\BERICHT-2026-08-24.md`, Abschnitt 5;
`zerlegung-auftragstext-nachtrag\BERICHT-2026-08-24.md`, Abschnitt 5).

---

## 3 — Der erste vollstaendige Durchlauf vom 25.8.

Aufnahme `2026-08-21 10-46-08`, 584.900 ms Material. Der erste Durchlauf, bei
dem Zerlegung, Auswahl und Bau als benannte Schritte mit eigenen Berichten
gefahren wurden.

### 3.1 Zerlegung

Quelle: `zerlegung-probelauf-1\BERICHT-2026-08-25.md`, Abschnitte „Eingaben"
und „Selbstauskuenfte".

| Groesse | Wert |
|---|---|
| Modell | `sonnet`, Lauf 1 |
| Kriterienfassung | „Fassung 0.8 (24. August 2026)", vor dem Lauf geprueft |
| Eingaben | `transkript-rendered.json` (167 Segmente), `wortliste.json` (1569 Woerter, **kein** Rueckfall auf die `.wav.json` noetig), `shorts-job.json` (`duration_ms` = 584900) |
| Kandidaten | **31**, geschrieben nach `kandidaten-lauf1.json` |
| Karte | lueckenlos 0–584.900 ms, jeder Abschnitt mit Kandidat oder Grund |
| Punktdichte gesamt | 34 von 1569 = 2,17 % (`.`/`!`/`?`); 422 von 1569 = 26,90 % (alle Satzzeichen) |
| Erste Haelfte (< 292.450 ms, 760 Woerter) | 23 = 3,03 % Satzende; 202 = 26,58 % alle |
| Zweite Haelfte (>= 292.450 ms, 809 Woerter) | 11 = 1,36 % Satzende; 220 = 27,19 % alle |
| Kandidaten in der zweiten Haelfte | 16 von 31 — die Dichte faellt nicht ab |

**Laufzeit der Zerlegung: nicht belegt.** Der Bericht nennt keine.

**Widerspruch zwischen zwei Berichten, nicht geglaettet:**
`shorts-wortliste\BERICHT-2026-08-24.md` (Pruefstein 4 und „Was mir
aufgefallen ist") zaehlt auf derselben `wortliste.json` mit denselben 1569
Woertern **29 Woerter mit Punkt (1,8 %)** und **424 mit Satzzeichen**
(`.,!?;:`). `zerlegung-probelauf-1\BERICHT-2026-08-25.md` zaehlt **34
Satzende-Woerter (2,17 %)** und **422 mit Satzzeichen**. Beide Berichte
beanspruchen exakte Messung per Mustersuche. Die Differenz 29/34 laesst sich
durch die unterschiedliche Frage erklaeren (nur `.` gegen `.`/`!`/`?`), die
Differenz 424/422 nicht ohne Weiteres (die eine Zaehlung schliesst `;` und `:`
ein, die andere nennt nur „alle Satzzeichen einschliesslich Komma"). **Wer
diese Zahlen als Grundlage braucht, muss sie einmal selbst nachzaehlen.**
Beide Fassungen stehen hier bewusst nebeneinander.

### 3.2 Urteil und Auswahl

Quelle: `shorts-auswahl\BERICHT-2026-08-25.md`, TEIL 6 und Pruefsteine; die
Zahlen decken sich mit `labels\repeat\trefferquote.json`, das in diesem Auftrag
gelesen wurde.

Erfolgszeile woertlich:
`27 von 31 angenommen, 4 abgelehnt, 0 ohne Urteil -> artefakte\repeat\zerlegung-probelauf-1\urteil\bauliste.json`

| Groesse | Wert |
|---|---|
| Kandidaten gesamt | 31 |
| Angenommen | 27 |
| Abgelehnt | 4 (Indizes 2, 6, 24, 28) |
| Ohne Urteil | 0 |
| **Quote** | **0,871** |
| Sicherheit `hoch` | ja 14 / nein 1 |
| Sicherheit `mittel` | ja 12 / nein 2 |
| Sicherheit `niedrig` | ja 1 / nein 1 |
| Im Zielbereich (8.000–15.000 ms) | ja 20 / nein 7 |
| Polarisierend unter den Angenommenen | wahr 12 / falsch 15 |
| Abweichungen aus `pruefe_uebereinstimmung` | 0 |

Die Bauliste traegt 27 Eintraege, kleinster Index 1, groesster 31, ohne
Neunummerierung.

### 3.3 Bau

Zwei Anlaeufe, dann der volle Lauf.

**Anlauf 1** (`shorts-bau-probelauf\BERICHT-2026-08-25.md`, TEIL 3): drei
Kandidaten (1, 15, 31), Rueckgabecode **0**, aber **0 von 3 gebaut** — alle
drei scheiterten an `avatar_canvas_avatar_coverage_sidecar_missing`, weil die
Arbeitskopie `avatar-cut.json` nicht mitnahm. Zielordner danach 12 MB, nur
Zwischenstufen. Das Fenster hat auftragsgemaess angehalten, statt `build.py`
zu aendern.

**Anlauf 2** (`arbeitskopie-seitendateien\BERICHT-2026-08-25.md`, TEIL 4):
dieselben drei Kandidaten nach der Neun-Zeilen-Aenderung, **3/3 gebaut in
101,4 s** (Wanduhr 113,2 s), Rueckgabecode 0. Der alte Zielordner wurde nach
`...-fehlversuch\` umbenannt statt geloescht (11.939.229 Byte, unveraendert).

**Der volle Lauf** (`shorts-bau-vollstaendig\BERICHT-2026-08-25.md`, TEIL 3):

| Groesse | Wert |
|---|---|
| Bauliste | `bauliste-rest.json`, 24 Kandidaten (die 27 angenommenen ohne 1, 15, 31) |
| Kommandozeile | `uv run python -m matrix_auto_cutter.shorts.build "artefakte/repeat/shorts/2026-08-21 10-46-08/shorts-job.json" "artefakte/repeat/shorts-bau-vollstaendig/bauliste-rest.json" --output-dir "F:/MatrixMarketAutoEdit/Shorts-Rendered/2026-08-21 10-46-08"` |
| Arbeitsverzeichnis | `P:\DimensionWithin-MatrixMarketAutoEditor` |
| Voreinstellungen | `--parallel 4`, Mausverfolgung/Arbeitskopie/Framezahl-Cache/Stillevorlauf AN |
| Ergebnis | **24/24 gebaut**, Rueckgabecode 0 |
| Laufzeit | **701,7 s** gemeldet (Wanduhr 703 s), davon 0,1 s Vorlauf |
| Fehlschlaege | keiner; `excluded_by_scene_filter_count` 0, `excluded_by_loop_point_count` 0 |
| Groesste Abweichung gegen den angepassten Plan | **13 ms** (Kandidat 14); Spanne −13 bis +12 ms |
| Zielordner gesamt | **251.890.096 Byte** (~252 MB, ~240 MiB) |
| Ordner/Shorts insgesamt | 27 Ordner `kandidat-NN`, 27 `short.mp4` |
| Neue `*.partial.mp4` | keine |

Bei drei Kandidaten (21, 22, 30) griff zusaetzlich der Stillevorlauf
(Startmarke 560–910 ms nach vorn geschoben) — normales, gewolltes Verhalten,
bereits in der Plan-Dauer enthalten.

---

## 4 — Entscheidungen samt Begruendung

Diese Werte sind gemessen oder vom Nutzer abgenommen. Sie ohne Anlass zu
drehen ist teuer. Die Entscheidungen der Uebergabe vom 24.8. zu Stufe 3b
(Mausverfolgung), zur Tonseite der Kandidatengrenzen und zu den Kriterien 0.8
gelten unveraendert weiter und stehen dort; hier stehen nur die vom 25.8.

### 4.1 Die Shorts gehen nach `F:\MatrixMarketAutoEdit\Shorts-Rendered\<Aufnahme>\`

Ein Ordner je Video. Belegt durch die Kommandozeilen beider Bauauftraege vom
25.8. Begruendung: das Repo ist nicht der Ort fuer 252 MB je Aufnahme, und
`F:` hatte am 25.8. 4970,28 GB von 5589,01 GB frei
(`shorts-bau-probelauf\BERICHT-2026-08-25.md`, TEIL 2a).

Diese Entscheidung hat die Luecke in der Arbeitskopie ueberhaupt erst
freigelegt: solange auf demselben Laufwerk wie die Quellen gebaut wurde, gab
es keinen Laufwerkswechsel und damit keine Arbeitskopie.

### 4.2 Die Zerlegung laeuft im Regelfall EINMAL

Ein zweiter Lauf ist ein **Nachschlag von Hand bei magerer Ausbeute**, nicht
der Normalfall.

Begruendung: bei 87 % Annahmequote (Abschnitt 3.2) ist der Nachschlag der
Sonderfall. Der Nutzer bewertet 80–90 % der Shorts mit „Eins" und haelt
schwaechere fuer hinnehmbar.

**Das ist eine Aenderung gegenueber der Uebergabe vom 24.8.**, die die
doppelte Zerlegung als „die wichtigste Verfahrensentscheidung" fuehrte. Die
Begruendung von damals (zwei Laeufe fanden je acht Kandidaten, nur drei
dieselben; einzeln 6 bzw. 5 brauchbare, zusammen 10) bleibt richtig fuer den
Fall einer mageren Ausbeute — sie rechtfertigt aber keinen zweiten Lauf, wenn
der erste 27 von 31 durchbringt. `ZERLEGUNG-AUFTRAGSTEXT.md` traegt den
Nachschlaglauf deshalb weiterhin als Abschnitt, mit dem Verbot, den ersten
Lauf zu kennen.

### 4.3 Sonnet genuegt fuer die Zerlegung

Der Lauf vom 25.8. wurde mit `sonnet` gefahren und brachte 31 Kandidaten mit
87 % Annahmequote. Die Uebergabe vom 21.8. verlangte fuer die Zerlegung Opus;
**das ist mit diesem Lauf ueberholt.**

Damit die Aussage ueberhaupt pruefbar bleibt, traegt jede Kandidatendatei das
Wurzelfeld `modell` (`ZERLEGUNG-AUFTRAGSTEXT.md`), und `auswahl.py` schreibt
es in jeden Trefferquote-Eintrag fort. Ohne dieses Feld waere eine
Trefferquote je Modell wertlos.

### 4.4 Unbeaufsichtigt laeuft `claude -p --permission-mode acceptEdits`

Ohne `--permission-mode` scheitert der Lauf am Freigabedialog, **nachdem er
die Arbeit bereits geleistet hat** — der teuerste denkbare Zeitpunkt.

Die Optionen selbst sind erhoben, nicht behauptet:
`kette-bestandsaufnahme\BERICHT-2026-08-24.md`, TEIL 5b, gibt `claude --help`
woertlich wieder, einschliesslich `-p/--print`, `--permission-mode` (Auswahl
`acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`),
`--output-format`, `--json-schema`, `--max-budget-usd`, `--add-dir`,
`--model`, `--session-id`. Fassung: `2.1.220 (Claude Code)`.

---

## 5 — Betriebsfallen

Jede einzelne hat schon Zeit gekostet. Die Fallen der Uebergabe vom 24.8.
gelten weiter — hier zuerst sie, dann die neuen.

### 5.1 Uebernommen vom 24.8.

**Product Runner nach jeder Codeaenderung neu starten.** Er laedt den Code
beim Start. Im `runner.log` zaehlt nur `Runner startet.` mit Statuscode
`runner_starting`; `laeuft bereits` heisst, der alte Code laeuft weiter.

**Tests nur ueber PowerShell**, immer `uv run python -m pytest`, **nie**
`uv run pytest`, **nie** ueber Git-Bash. Am 25.8. erneut bestaetigt:
`uv run pytest` ohne `python -m` bricht mit `ModuleNotFoundError: No module
named 'tests'` in `tests/repeat/` und `tests/phase2/` ab, 20 Sammelfehler
(`shorts-auswahl\BERICHT-2026-08-25.md`, „Was mir aufgefallen ist", Punkt 4).

**Nach einem Fehlschlag nie neu transkribieren.** Die `.wav.json` steht noch
und kann direkt nachkonvertiert werden. Ein turbo-Lauf kostet die 1,27-fache
Echtzeit.

**Nie `git clean -xfd`.** Der Befehl loescht `artefakte\` mit, darunter die
whisper-Rohausgaben.

**Urteilsdateien nie anfassen.** Urteilszeit ist das einzige Artefakt, das
sich nicht neu erzeugen laesst.

**Gescheiterte Renderversuche lassen ihre Partialdatei liegen.** Fuenf solche
Dateien liegen unter `F:\MatrixMarketAutoEdit\Rendered\`, alle vom 9.8.,
namentlich aufgefuehrt in `shorts-bau-vollstaendig\BERICHT-2026-08-25.md`,
TEIL 3f. Im Zweifel ist das das fertige Video — vor dem Aufraeumen
hineinsehen.

**PowerShell 5.1 liest UTF-8 ohne `-Encoding UTF8` falsch** und schreibt per
Vorgabe **mit** BOM. Deshalb liest `load_offsets` mit `utf-8-sig`.

**Doppelte Namen, Verwechslungsgefahr:** `MIN_PAUSE_MS` existiert zweimal
(`loop_point` 250, `level_cut` 100) — bei Auftraegen immer das Modul nennen.
`ausschnitt.json` und `short.json` sind keine festen Formate, sondern Sidecars
zum jeweiligen Ausgabedateinamen. `grenze_der_regel` steht zweimal in der
Kriteriendatei.

**Zeilennummern in `level_cut.py` veralten schnell.**

### 5.2 Neu am 25.8. gefunden

**`build.py` liefert Rueckgabecode 0 auch dann, wenn KEIN Kandidat gebaut
wurde.** Der wichtigste neue Befund. Am 25.8. meldete der Probelauf Code 0 bei
0 von 3 gebauten Kandidaten — jeder Kandidat wird unabhaengig behandelt, ein
Kandidatenfehlschlag bricht den Gesamtlauf nicht ab
(`shorts-bau-probelauf\BERICHT-2026-08-25.md`, TEIL 3c). **Erfolgsnachweis ist
die Zahl der `short.mp4`, nicht der Rueckgabecode.** Hilfsweise
`summary.built_count` gegen `summary.candidate_count` im
`shorts-bau-bericht.json` — der aber nur im Erfolgsfall geschrieben wird.

**`build.py` ueberspringt vorhandene Ausgaben nicht.** Kein Wiederanlauf, kein
Ueberspringen: `mkdir(parents=True, exist_ok=True)` je Kandidatenordner, dann
wird hineingeschrieben. Ein vorher vorhandener `kandidat-NN` bleibt mit halb
ueberschriebenem Inhalt stehen, wenn der Lauf abbricht — der Aufraeumer
erfasst nur die in diesem Lauf **neu angelegten** Ordner
(`kette-bestandsaufnahme\BERICHT-2026-08-24.md`, TEIL 3, `build.py`-Abschnitt).

**Die Dauer eines Shorts weicht ABSICHTLICH von `end_ms - start_ms` ab.**
Stillevorlauf, Tonblende und Wortrandkollision verschieben Anfang und Ende.
Gemessen: +377 bis +440 ms gegen den rohen Wert
(`arbeitskopie-seitendateien\BERICHT-2026-08-25.md`, Pruefstein 3), aber
hoechstens 13 ms gegen den angepassten Plan
(`shorts-bau-vollstaendig\BERICHT-2026-08-25.md`, TEIL 3e). **Gemessen wird
gegen `build_end_ms - build_start_ms` aus dem Baubericht.**

**Der Vorlauf kostet bei kaltem Framezahl-Cache 230,1 s statt 0,1 s.**
Vergleich der Laeufe vom 21.8. und 25.8., `shorts-bau-vollstaendig`, „Was mir
aufgefallen ist". Wer eine Bauzeit veranschlagt, muss wissen, ob Cache und
Arbeitskopie warm sind.

**`TREFFERQUOTE_PFAD` in `auswahl.py` ist ein RELATIVER Pfad**
(`labels/repeat/trefferquote.json`) und haengt damit am Arbeitsverzeichnis des
Aufrufers. Im Probelauf war das die Repo-Wurzel, wie bei allen anderen
Shorts-Werkzeugen. **Fuer einen geplanten, unbeaufsichtigten Lauf ist das eine
Falle** — die Windows-Aufgabenplanung setzt das Arbeitsverzeichnis nicht von
selbst auf die Repo-Wurzel.

**`judge_server` uebernimmt beim Start den Inhalt der juengsten
`urteile*.json` im Auftragsordner** in eine neue Sitzungsdatei — noch vor dem
ersten Urteil. Treffen neue Kandidaten auf alte Urteile, zeigen die Urteile
auf fremde Kandidaten. `auswahl.py` faengt genau das ab
(`pruefe_uebereinstimmung` gegen `start_ms`, `end_ms`, `titel`; Abweichung →
Code 5, nichts wird geschrieben), aber nur, wenn `auswahl.py` ueberhaupt
dazwischensteht.

**Es gibt Tests, die in die Repo-Wurzel schreiben.** Die Datei `-`, 9 Byte,
Inhalt `mp4-bytes`, entsteht bei jedem Lauf von
`test_out_probe_failure_after_encode_warns_but_still_succeeds`: dem
Doppelgaenger `_FailOnSecondProbe` in `tests\repeat\test_cutcli.py` fehlt der
`silencedetect`-Zweig, den `_FakeRunner` in derselben Datei hat; der
ffmpeg-Aufruf zur Stilleerkennung endet auf `-`, und `Path("-")` ist ein
relativer Pfad ins Arbeitsverzeichnis. Vollstaendige Kette mit Fundstellen in
`zerlegung-auftragstext-nachtrag\BERICHT-2026-08-24.md`, TEIL 2b. **Nicht
behoben** — die Datei liegt unter `tests\repeat\` und damit im gesperrten
Gebiet. Fuenf weitere Doppelgaenger mit demselben `Path(argv[-1])`-Muster sind
dort gelistet, aber nicht geprueft.

---

## 6 — Der Weg zur Automatisierung

**Der wichtigste Abschnitt fuer den naechsten Chat.**

Zielbild: die Linie besteht aus **zwei automatischen Haelften mit einem
menschlichen Tor dazwischen**.

```
Aufnahme  ->  [auto]  ->  Schnittvorschlag
                             |  REVIEW-FENSTER DES NUTZERS
                          gerendertes Video
          ->  [auto]  ->  Transkript -> Wortliste -> Kandidaten
                             |  URTEILE DES NUTZERS
          ->  [auto]  ->  Auswahl -> fertige Shorts
                             |  Veroeffentlichen
```

Das Urteilstor ist strukturell unumgehbar: `judge_server` haelt mit
`webbrowser.open` und `serve_forever` und endet nur durch Strg+C
(`kette-bestandsaufnahme\BERICHT-2026-08-24.md`, „Was mir aufgefallen ist",
Punkt 5). Ein Kettenlaeufer, der das ignoriert, haengt.

Vier Schritte, in dieser Reihenfolge:

### Schritt 1 — Startskript Urteilsseite

**Was es tun muss:** aus einem Aufnahmenamen den `judge_server` mit den
richtigen Pfaden starten, den Browser oeffnen und nach dem Strg+C sauber
enden. Ein Skript, kein Kettenglied — es steht am menschlichen Tor.

**Welche Fallen es umgehen muss:**
- Es muss die **richtige** Kandidatendatei uebergeben. Nach der Umstellung auf
  `kandidaten-lauf<N>.json` liegt im Aufnahmeordner nicht mehr zwingend eine
  `kandidaten.json`, die `judge_server` aber erwartet.
- Es muss den Nutzer sehen lassen, **welche** Urteilsdatei uebernommen wurde.
  `judge_server` kopiert stillschweigend die juengste `urteile*.json` in die
  neue Sitzungsdatei (Abschnitt 5.2).
- Es darf keine Urteilsdatei loeschen oder ueberschreiben.

### Schritt 2 — Stufe 0 kopflos

**Was es tun muss:** `shorts-job.json` ohne Tk-Fenster erzeugen.
`build_inventory` (`inventory.py`) liefert bereits **jedes** Feld des Payloads
ausser `created_at` und importiert kein `tkinter`. Vier Dinge kommen heute nur
ueber `app.py` zustande und muessen ersetzt werden
(`kette-bestandsaufnahme\BERICHT-2026-08-24.md`, TEIL 2b):
1. `created_at` — heute `datetime.now(UTC).isoformat()` im Tk-Rueckruf, es
   gibt keinen zweiten Erzeuger im Repo.
2. Der Zielpfad ueber `job_output_path(jobs_root, row.name)`.
3. `sessions_dir` und `artifacts_dir` — in `build_inventory` keyword-only
   **ohne Vorgabewert**; ein kopfloser Aufrufer muss sie selbst besetzen. Sie
   kommen heute aus `default_state_directory()` und damit aus
   `%LOCALAPPDATA%`.
4. Die Ueberschreib-Entscheidung — heute `messagebox.askyesno`. Kopflos
   braucht sie eine Fahne.

**Welche Fallen es umgehen muss:** `build_inventory` zeigt per Vorgabe auf
`F:\MatrixMarketAutoEdit` und `F:\ShortsQuellen`; ein Auftrag, der Stufe 0
kopflos bauen und `F:` sperren will, widerspricht sich. Das Einmalskript vom
21.8. (`write_shorts_job.py`) ist **nicht auffindbar** — sein einziger
genannter Ablageort liegt unter `%LOCALAPPDATA%\Temp` und damit im gesperrten
Gebiet. Es gibt also keine Vorlage; der Schritt wird neu gebaut.

### Schritt 3 — Der Kettenlaeufer

**Was er tun muss:** die erste automatische Haelfte am Stueck fahren —
Stufe 0 (kopflos) → Stufe 1 (Avatarschnitt) → Stufe 2.1 (Transkription) →
Wortliste → Zerlegung — und danach die zweite Haelfte, Auswahl → Bau. Er
endet vor dem Urteilstor und faengt danach wieder an.

**Welche Fallen er umgehen muss — jede einzelne ist oben belegt:**

- **Rueckgabecode 0 ist kein Erfolgsnachweis** (Abschnitt 5.2). Nach dem Bau
  zaehlt er `short.mp4`, nicht den Code.
- **`build.py` ueberspringt nichts.** Wiederanlauf heisst hier: vorher pruefen,
  ob der Zielordner schon Kandidaten traegt.
- **Der stille Wiederanlauf der Transkription ist gefaehrlicher als ein lauter
  Abbruch.** `transcript.py` verwendet eine vorhandene `*.wav.json` ohne jede
  Pruefung wieder — auch eine abgebrochene, halb geschriebene. Der
  Kettenlaeufer muss sie vor der Wiederverwendung wenigstens als JSON parsen.
  `wortliste.py` macht genau das bereits fuer seinen eigenen Fall (eigener
  Rueckgabecode 3 bei kaputtem JSON, 4 bei leerer Wortliste, keine leere Datei
  auf der Platte).
- **Ein abgebrochener Bau hinterlaesst keine Spur.** `write_build_report`
  steht hinter der `BuildFailed`-Rueckgabe — die Fehlerursache ist genau so
  lange bekannt, wie das Fenster offen bleibt. Ein unbeaufsichtigter Lauf muss
  stdout selbst mitschreiben.
- **`TREFFERQUOTE_PFAD` ist relativ.** Der Laeufer setzt das
  Arbeitsverzeichnis ausdruecklich auf die Repo-Wurzel.
- **Der Bau braucht ein `--output-dir`**, Pflichtparameter ohne Vorgabe. Die
  Konvention aus Abschnitt 4.1 steht nirgends im Code — der Laeufer bringt sie
  mit.
- **Es gibt keine Fortschrittsanzeige, die auf der Uhr steht.**
  `transcript.py` gibt zwischen Start und Ende **kein einziges Zeichen** aus;
  die whisper-Ausgabe landet in einer Pipe. Dabei liegt alles Noetige schon
  vor: `ProcessResult.duration_ms` wird gemessen und weggeworfen, die
  Audiodauer ist zweifach bekannt (aus `shorts-job.json` und aus einer eigenen
  ffprobe-Messung), und der Faktor 1,27 ist gemessen. Ein Fenster hat die
  Transkriptionsdauer aus gezaehlten Wartezyklen hochgerechnet und lag um
  Faktor 17 daneben (3,5 h statt 12 min 22 s). **Ein Schritt, der
  unbeaufsichtigt laeuft, muss sagen koennen, wo er steht.**
- **Bereitschaft einer Aufnahme ist heute nur an Dateien ablesbar.** Es gibt
  keine Zustandsdatei, die den Fortschritt durch die Kette festhaelt — gesucht
  und nicht gefunden. Belastbar ist allein `transkript-rendered.json`, weil
  sie atomar getauscht wird; `transkript-rendered.wav.json` ist es **nicht**.
  Eine praktikable Zusatzpruefung waere `segment_count` gegen 0. **Eine
  Zustandsdatei je Aufnahme ist das erste, was der Kettenlaeufer selbst
  mitbringen sollte.**
- **Namenskonventionen doppelt gepflegt:** `AVATAR_CUT_FILE_NAME` steht in
  `avatar_cut.py` **und** in `build.py`, ebenso `AUSSCHNITT_FILE_NAME` in
  `chart_crop.py` und `build.py`. Wer einen Namen aendert, muss beide Stellen
  treffen.

### Schritt 4 — Die geplante Aufgabe

**Was sie tun muss:** den Kettenlaeufer wiederholt anstossen.

**Welche Fallen sie umgehen muss:**
- **Claude Code 2.1.220 hat keinen Zeitplaner.** Kein `cron`, kein `schedule`,
  kein `timer`, kein `task` unter den Unterbefehlen; `claude agents` verwaltet
  laufende Hintergrundsitzungen, plant aber keine. Die Wiederholung muss aus
  der **Windows-Aufgabenplanung** kommen. Dort gibt es bisher nichts:
  `Get-ScheduledTask`, gefiltert auf `matrix|claude|shorts|cutter|cursor`,
  liefert keine einzige Aufgabe. **Beim Aufsetzen erneut pruefen, ob eine
  neuere Fassung einen eingebauten Weg bietet.**
- **Ohne `--permission-mode` scheitert der Lauf am Freigabedialog**
  (Abschnitt 4.4).
- **Das Arbeitsverzeichnis muss gesetzt werden** — alle Eingaben sind relativ
  zur Repo-Wurzel, und `TREFFERQUOTE_PFAD` ebenfalls.
- **`.claude\` im Repo ist ein leeres Verzeichnis.** Fuer eine geplante
  Aufgabe waere das der natuerliche Ort fuer Auftragstext, erlaubte Werkzeuge
  und Verzeichnisfreigaben — heute ungenutzt.
- **Ob eine per `claude -p` gestartete Sitzung Zugriff auf ein
  `scheduled-tasks`-Werkzeug haette, ist nicht belegbar.** Es steht in keiner
  erreichbaren Konfigurationsdatei; `claude mcp list` kennt nur
  `claude-design`. Das Werkzeug wurde nicht aufgerufen.

---

## 7 — Offene Punkte

Alle folgenden Punkte sind **unerledigt**.

**Zwischenstufen bleiben liegen; ein Aufraeumer fehlt.** Je Kandidat stehen
neben `short.mp4` die Zwischenstufen `ausschnitt.mp4`, `leinwand.mp4` und
`mit-avatar.mp4` samt Seitendateien; `build.py` raeumt sie nicht auf, egal ob
der Kandidat durchlaeuft oder scheitert. Groessenordnung aus den drei
Kandidaten des zweiten Probelaufs: `short.mp4` 3.134.333 B gegen 2.146.984 +
2.130.423 + 3.140.226 B an Zwischenstufen bei kandidat-01 — die
Zwischenstufen machen rund 70 % des Zielordners aus. Bei ~252 MB je Aufnahme
ist das der Loewenanteil. **Unersetzlich sind nur die Urteilsdateien und die
whisper-Rohausgaben** — alles andere laesst sich neu bauen.

**`bauliste-rest.json` entstand von Hand; eine Regel fuer Teilbauten fehlt.**
Sie wurde aus `bauliste.json` abgeleitet, indem die drei bereits gebauten
Indizes aus dem `kandidaten`-Array entfernt wurden. Es gibt kein Werkzeug
dafuer und keine Konvention, wo eine solche Teilliste liegt.

**Das Feld `angenommen` traegt in Teillisten weiterhin die Zahl der
Gesamtauswahl.** In `bauliste-rest.json` steht `angenommen: 27` bei 24
Eintraegen — bewusst so belassen, derselben Konvention folgend wie
`bauliste-probe.json`. Der Bau liest das Feld nicht; ein Auswertungsskript
wuerde darauf hereinfallen.

**Die Zusammenfuehrung zweier Zerlegungslaeufe ist nicht gebaut.** Die Regel
steht in `zerlegung_laeuft_zweimal` in der Kriteriendatei (mehr als die
Haelfte der kuerzeren Dauer Ueberlappung, dann die laengere Fassung), es gibt
aber weder Auftragstext noch Code. Seit dem 24.8. unveraendert offen.
Ausserdem: wohin die zusammengefuehrte Datei geschrieben wird und wer sie zu
`kandidaten.json` macht, ist nicht festgelegt.

**`die_sicherheitsstufe_traegt` gilt fuer Sonnet nicht.** Gemessen am Lauf vom
25.8.: `hoch` 14 von 15 angenommen, `mittel` 12 von 14. Die Stufe `hoch`
traegt also kaum besser als `mittel`. **Erster Gegenbefund; vier Ablehnungen
sind keine Reihe.** Nicht als widerlegt behandeln, sondern beim naechsten Lauf
mitzaehlen — genau dafuer schreibt `auswahl.py` die Verteilung je
Sicherheitsstufe in `trefferquote.json` fort.

**Claude Code 2.1.220 hat keinen Zeitplaner.** Die Wiederholung muss aus der
Windows-Aufgabenplanung kommen. Beim Aufsetzen erneut pruefen, ob eine neuere
Fassung einen eingebauten Weg bietet.

**Die Punktdichte-Zahlen widersprechen einander** (Abschnitt 3.1). Vor der
naechsten Verwendung einmal selbst nachzaehlen.

**Aus der Uebergabe vom 24.8. weiterhin offen:** das Veroeffentlichen (nur
Shorts, Longform und Thumbnails bleiben draussen; fuer Shorts ist nichts
gebaut); der Stillevorlauf-Rueckfall bei kandidat-08/-09 der Aufnahme vom
21.8. (Verschiebungen −910 und −540 ms analysiert, nie gebaut, nie gehoert);
die verschwundene +1-Frameabweichung (Ursache nicht belegt, Pruefung
unveraendert im Code); der fehlende turbo-Durchsatzwert in `UMGEBUNG.md`
(741,7 s fuer 584,9 s Audio, Faktor 1,27, 4 Threads, `-ml 120`, gemessen
21.8. — steht bis heute nur im Bericht); die lueckenhafte Fassungsgeschichte
der Kriteriendatei (0.3 → 0.7 ohne Zwischenstaende, 0.6 nur als `.bak`); die
vier Ordner mit zusammengeschobenen Pfadnamen, deren Ursache nicht auffindbar
war; und `laengere_fassung` als Feld — es hat seit dem Nachtrag vom 24.8.
einen Platz im Schema der Kandidatendatei, ist aber noch nie von einem Lauf
befuellt worden.
