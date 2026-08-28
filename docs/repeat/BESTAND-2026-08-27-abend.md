# Bestand — Shorts-Linie, Stand 2026-08-27, Abend

Fortschreibung von `BESTAND-2026-08-27.md`. Auftrag: `uebergabe-27-08-abend`,
rein lesend ausser den beiden neuen Dokumenten unter `docs\repeat\`.
Repository `P:\DimensionWithin-MatrixMarketAutoEditor`.

**Wie dieses Dokument zu lesen ist:** Der Bestand vom 27.8. mittags gilt weiter,
wo hier nichts anderes steht — und er setzt seinerseits die Bestaende vom 25.8.
abends und nachmittags fort. Dieses Dokument nennt, was seit `d8c6c61` neu ist.
Wer eine Modulsignatur, eine Konstante oder ein Dateiformat sucht, das hier
nicht steht, schlaegt dort nach.

Alle Zeilennummern sind in diesem Auftrag an den Dateien geprueft, Stand
`a3b9717`.

---

## 1. Stand

**HEAD:** `a3b9717` auf `master`, gepusht.

**`git status --porcelain` woertlich (vor dem Commit dieses Auftrags):**

```
?? -
```

Die Datei `-` (9 Byte) ist unveraendert der bekannte Testrueckstand, Ursache
belegt in Abschnitt 12.1 des Bestands vom 25.8. nachmittags. **Nicht anfassen,
nicht committen.**

**Die beiden `…-unbekannt.json` unter `labels\repeat\`, die der Bestand vom
27.8. mittags in Abschnitt 1 als unversioniert fuehrte, liegen nicht mehr da.**
Der Nutzer hat sie geloescht; die richtig benannten Nachfolger sind mit
`175c1d4` committet (Uebergabe vom 27.8. abends, Abschnitt 7.3).

**Die vier Commits seit `357f12f`:**

| Kuerzel | Betreff |
|---|---|
| `897e762` | Shorts: Vorauswahl der besten Gruppen, Verfall nach 48 Stunden |
| `175c1d4` | Shorts: Urteile und Kandidaten des Doppellaufs vom 25.8. |
| `5379982` | Shorts: Trefferquote des Doppellaufs vom 25.8. |
| `a3b9717` | Shorts: Teilbau baut nur die offenen, Rueckfall auf modelle |

Der Auftragstext dieses Auftrags spricht von fuenf Commits. `git log` kennt
nach `357f12f` vier; der fuenfte ist `357f12f` selbst. Vermerkt in Abschnitt 2
der Uebergabe vom 27.8. abends.

**Dateigroessen der vier geaenderten Module:**

| Datei | Zeilen (27.8. mittags) | Zeilen (jetzt) |
|---|---|---|
| `src\matrix_auto_cutter\shorts\auswahl.py` | 957 | **1216** |
| `src\matrix_auto_cutter\shorts\urteilslauf.py` | — | **878** |
| `src\matrix_auto_cutter\shorts\kette.py` | 1040 | **1065** |
| `src\matrix_auto_cutter\shorts\judge.py` | 1459 | **1544** |

**`.py`-Dateien unter `src\matrix_auto_cutter\shorts\`: 28.** In diesem Auftrag
nachgezaehlt. Die vollstaendige Liste: `__init__.py`, `app.py`, `auftrag.py`,
`auswahl.py`, `avatar_axis.py`, `avatar_canvas.py`, `avatar_cut.py`,
`avatar_lag.py`, `build.py`, `candidates.py`, `canvas.py`, `chart_crop.py`,
`cursor_track.py`, `endcard.py`, `frame_map.py`, `inventory.py`, `job.py`,
`judge.py`, `judge_server.py`, `kette.py`, `level_cut.py`, `loop_point.py`,
`scene_windows.py`, `subtitle_burn.py`, `subtitle_lines.py`, `transcript.py`,
`urteilslauf.py`, `wortliste.py`.

---

## 2. Die Module der Shorts-Linie

**Abschnitt 2 des Bestands vom 27.8. mittags gilt unveraendert.** Es ist kein
Modul dazugekommen und keines weggefallen — die 28 Dateien sind dieselben wie
dort. Geaendert haben sich vier davon.

---

## 3. `auswahl.py` (1216 Zeilen) — Vorauswahl, Kennungen, Rueckfall

**Die Abschnitte 4.2 (Zusammenfuehrung) und 4.4 (CLI) des Bestands vom 27.8.
mittags gelten fort**, mit den unten genannten Ergaenzungen.

### 3.1 Neue Konstante

| Zeile | Name | Wert |
|---|---|---|
| `auswahl.py:49` | `VORAUSWAHL_GROESSE` | `15` |

Der Kommentar darueber (Zeilen 44–48) traegt die Begruendung: „Der Nutzer
veroeffentlicht 4 bis 10 Shorts je Aufnahme und hat dafuer 48 Stunden — 15
Gruppen sind genug Auswahl dafuer und wenig genug, um sie in dem Fenster
wirklich durchzusehen."

Die uebrigen Konstanten (`BAULISTE_FILE_NAME`, `BUENDEL_FILE_NAME`,
`BUENDEL_ARTIFACT_TYPE`, `BUENDEL_SCHEMA_VERSION`, `LAUFDATEI_GLOB`,
`TREFFERQUOTE_PFAD`, `TREFFERQUOTE_SCHEMA_VERSION`, `_ZIELBEREICH_MIN_MS`,
`_ZIELBEREICH_MAX_MS`) stehen unveraendert.

### 3.2 Die Pruefungen der Vorauswahl

```python
# auswahl.py:489
def _pruefe_vorauswahl(
    gruppen: dict[int, list[tuple[int, dict[str, object]]]],
) -> list[str]: ...

# auswahl.py:567
def _pruefe_vorauswahl_grenze(
    gruppen: dict[int, list[tuple[int, dict[str, object]]]],
    rang_je_gruppe: dict[int, int],
) -> list[str]: ...
```

**Beide Felder sind FREIWILLIG.** Eine `buendel.json` aus der Zeit vor der
Vorauswahl traegt sie gar nicht, und das ist keine Abweichung. **Erst wenn EINE
Gruppe sie traegt, muessen alle sie tragen** — Begruendung im Docstring: „eine
halbe Vorauswahl waere schlimmer als keine, weil die Seite dann eine Reihenfolge
zeigte, die nur fuer einen Teil des Bestandes gilt."

`_pruefe_vorauswahl` meldet:

- `gruppen_rang` uneinheitlich innerhalb einer Gruppe;
- `gruppen_rang` nicht lueckenlos 1 bis Gruppenzahl, jeder Wert genau einmal;
- fehlender `gruppen_rang`, sobald mindestens eine Gruppe ihn traegt.

`_pruefe_vorauswahl_grenze` meldet zwei Befunde, der zweite ist der wichtigere:

- `vorauswahl` uneinheitlich innerhalb einer Gruppe;
- Zahl der vorausgewaehlten Gruppen ungleich `min(VORAUSWAHL_GROESSE, len(gruppen))`;
- **eine vorausgewaehlte Gruppe mit groesserem `gruppen_rang` als eine nicht
  vorausgewaehlte.** Docstring: das „ist kein Zaehlfehler, sondern widerspricht
  sich selbst — dann sagt die Datei zwei verschiedene Dinge darueber, was die
  besten Gruppen sind."

### 3.3 `pruefe_buendel` — jetzt sechs Befunde statt fuenf

```python
# auswahl.py:628
def pruefe_buendel(
    kandidaten: list[dict[str, object]], buendel: list[dict[str, object]]
) -> list[str]: ...
```

Signatur unveraendert; leere Liste heisst weiterhin „in Ordnung". Die
Befundreihenfolge laut Docstring: fehlende Indizes, ueberzaehlige Indizes,
Gruppen ohne genau eine Empfehlung, doppelte oder fehlende `rang`-Werte, **eine
unstimmige Vorauswahl (`_pruefe_vorauswahl`)**, auseinandergerissene Paare mit
`laengere_fassung_von`. Der fuenfte Punkt ist neu; der letzte bleibt der
wichtigste.

Beide Eingaben sind weiterhin **rohe** Wortlisten und keine `Candidate`-Objekte
— `laengere_fassung_von` ist ein Buchfuehrungsfeld und wuerde von
`parse_candidates` weggeschnitten.

### 3.4 Kennungen: `laeufe_kennung`, `modell_kennung`, `_laufschluessel`

```python
# auswahl.py:869
def laeufe_kennung(wurzel: dict[str, object]) -> list[object]: ...

# auswahl.py:886
def modell_kennung(wurzel: dict[str, object]) -> str: ...

# auswahl.py:912
def _laufschluessel(schluessel: object) -> tuple[int, str]: ...
```

`laeufe_kennung` (neu mit `897e762`): `laeufe`, ersatzweise `[lauf]`,
ersatzweise `[]`. Docstring nennt den Anlass: „Genau daran fehlte der
Trefferquote-Eintrag vom 26. August 2026."

`modell_kennung` (neu mit `a3b9717`): `modell`, wenn es da und nicht leer ist;
sonst die Werte aus `modelle` in **numerischer** Laufreihenfolge mit `+`
verbunden; sonst `"unbekannt"`. **Geraten wird nicht.**

`_laufschluessel` ordnet die Schluessel von `modelle` numerisch — sie sind
Zeichenketten, und alphabetisch stuende `"10"` vor `"2"`.

### 3.5 `fuehre_zusammen` schreibt `modell` wieder mit

```python
# auswahl.py:187
def fuehre_zusammen(saetze: list[tuple[int, dict[str, object]]]) -> dict[str, object]: ...
```

Signatur und Regel unveraendert (Bestand vom 27.8. mittags, 4.2). Neu ist eine
Zeile in der Nutzlast, `auswahl.py:316`:

```python
payload["modell"] = "+".join(modelle[str(nummer)] for nummer, _ in geordnet)
```

Der Kommentar darueber sagt, warum das Feld trotz `modelle` bleibt: „alles, was
nach der Zusammenfuehrung kommt, sucht das Wurzelfeld und nicht die Abbildung."

Die Wurzelfelder einer zusammengefuehrten `kandidaten.json` sind damit:
`achse`, `kandidaten`, `kriterien_fassung`, `lauf`, `laeufe`, **`modell`**,
`modelle`, `verworfene_verweise`, `video_dauer_ms`, `video_name`,
`zusammengefuehrt_am`. **Dateien, die vor `897e762` zusammengefuehrt wurden,
tragen kein `modell`** — dort greift `modell_kennung` beim Lesen.

### 3.6 `lies_kandidaten_rohdaten`

```python
# auswahl.py:828
def lies_kandidaten_rohdaten(pfad: Path) -> tuple[list[dict[str, object]], dict[str, object]]: ...
```

Signatur unveraendert. Geaendert ist, wie die Wurzelfelder gefuellt werden
(`auswahl.py:856-866`): `kriterien_fassung` und `video_name` wie bisher als
Zeichenketten, **`modell` aber ueber `modell_kennung`** und zusaetzlich
`laeufe` ueber `laeufe_kennung`. Kommentar an der Stelle: „`modell` nicht wie
die uebrigen Felder: fehlt es, tritt `modelle` an seine Stelle."

### 3.7 `trefferquote_eintrag` — zwei neue Felder

```python
# auswahl.py:927
def trefferquote_eintrag(
    *,
    video_name: str,
    lauf: int | str | None,
    laeufe: list[object] | None = None,
    notiz: str = "",
    modell: str,
    kriterien_fassung: str,
    kandidaten: list[Candidate],
    angenommen: list[Candidate],
    abgelehnt: list[Candidate],
    ohne_urteil: list[Candidate],
    polarisierend_je_index: dict[int, bool] | None = None,
) -> dict[str, object]: ...
```

Neu sind `laeufe` und `notiz`. Im Ergebnis stehen **beide** Felder:

```python
"laeufe": list(laeufe) if laeufe is not None else ([] if lauf is None else [lauf]),
"notiz": notiz,
```

Kommentar im Code: „Beide Felder: `laeufe` ist die Kennung, `lauf` bleibt
stehen, damit die zwei Alteintraege und die neuen dasselbe Schema haben."

### 3.8 `_hat_bestehenden_eintrag` prueft `laeufe` statt `lauf`

```python
# auswahl.py:1016
def _hat_bestehenden_eintrag(
    pfad: Path, *, video_name: str, laeufe: list[object]
) -> bool: ...
```

**Ein Alteintrag ohne `laeufe` gilt als `[lauf]`** und blockiert damit weiterhin
genau seinen eigenen Fall. Umgeschrieben wird er nicht — Docstring: „die
Trefferquote ist eine Reihe von Messungen, und eine nachtraeglich ergaenzte
Messung waere keine mehr."

### 3.9 CLI: `--notiz TEXT`

`auswahl.py:1102`, an `main` durchgereicht bei `auswahl.py:1195`:

```
--notiz TEXT   Freitext am Trefferquote-Eintrag, etwa um einen Lauf als nicht
               repraesentativ zu vermerken. Wird nur durchgereicht, nicht
               ausgewertet.
```

Die uebrigen Fahnen (`job_path`, `--kandidaten`, `--urteile`, `--output`,
`--keine-trefferquote`, `--zusammenfuehren`) stehen unveraendert.

---

## 4. `urteilslauf.py` (878 Zeilen) — Verfall und Teilbau

**Abschnitt 4 des Bestands vom 25.8. abends gilt fort**, ausser fuer die hier
genannten Stellen.

### 4.1 Neue Konstanten und der Wegfall von Code 7

| Zeile | Name | Wert |
|---|---|---|
| `urteilslauf.py:71` | `VERFALL_STUNDEN` | `48` |
| `urteilslauf.py:75` | `_CODE_NUR_VERFALLEN` | `2` |
| `urteilslauf.py:91` | `TEILBAULISTE_FILE_NAME` | `"bauliste-offen.json"` |

Der Kommentar vor `VERFALL_STUNDEN` (Zeilen 65–70) nennt die Messgrundlage:
„Gemessen wird ab der Aufnahmezeit im Ordnernamen
(`inventory.parse_name_timestamp`), nicht ab der Aenderungszeit einer Datei:
eine zweite Zerlegung macht eine alte Aufnahme nicht wieder jung."

**`_CODE_ZIEL_BELEGT = 7` kommt in der Datei nicht mehr vor** — in diesem
Auftrag nachgezaehlt: null Treffer. An seiner Stelle steht ein Kommentar,
`urteilslauf.py:76-78`:

```python
# Code 7 (ziel_belegt) ist ersatzlos entfallen: ein belegter Zielordner haelt
# den Lauf nicht mehr an, sondern schraenkt ihn auf die offenen Kandidaten ein
# (Teilbau, siehe :func:`offene_indizes`).
```

Die uebrigen Codes stehen unveraendert: `_CODE_ERFOLG` 0,
`_CODE_KEINE_AUFNAHME` 2, `_CODE_AUFTRAG_UNLESBAR` 2,
`_CODE_URTEILE_ABWEICHUNG` 5, `_CODE_SICHERUNG_FEHLGESCHLAGEN` 6,
`_CODE_BAU_UNVOLLSTAENDIG` 8 (`urteilslauf.py:79`).

### 4.2 Der Verfall

```python
# urteilslauf.py:94
def alter_stunden(name: str, jetzt: datetime | None = None) -> float | None: ...

# urteilslauf.py:109
def ist_verfallen(name: str, jetzt: datetime | None = None) -> bool: ...

# urteilslauf.py:115
def finde_aufnahme(
    wurzel: Path, *, auch_verfallen: bool = False, jetzt: datetime | None = None
) -> Path | None: ...
```

`alter_stunden` gibt `None` zurueck, wenn der Ordnername keine lesbare Zeit
traegt; ein solcher Ordner gilt als **nicht** verfallen — Docstring: „lieber
eine Aufnahme zu viel anbieten als eine, die es noch gibt, wegen eines
unerwarteten Namens verschweigen."

`finde_aufnahme` waehlt weiterhin nach der **Aenderungszeit der
`kandidaten.json`**, nicht nach Ordnernamen. Neu ist der Parameter
`auch_verfallen` und die Zeile bei `urteilslauf.py:140-143`: eine verfallene
Aufnahme wird uebergangen und dabei **genannt**:

```
  uebergangen: <NAME> (verfallen, N h alt)
```

Der Parameter `jetzt` ist die Testeinstiegsstelle; ohne ihn `datetime.now()`.

### 4.3 Schritt 1 in `main`

`main` beginnt bei `urteilslauf.py:632`. Der Verfallszweig steht bei
`urteilslauf.py:703-716`:

- ohne Pfadangabe: `finde_aufnahme(wurzel, auch_verfallen=args.auch_verfallen)`;
- findet das nichts, wird **noch einmal** mit `auch_verfallen=True` gesucht, um
  die beiden Faelle „gar keine Aufnahme" und „nur verfallene" zu trennen. Der
  Kommentar an der Stelle begruendet das: „Ein gemeinsamer Satz waere" ungenau.
- nur Verfallenes → `ANGEHALTEN [nur_verfallen]` und `_CODE_NUR_VERFALLEN` (2).

Bei **ausdruecklich uebergebenem Pfad** (`urteilslauf.py:736`) wird nicht
angehalten, sondern gewarnt: „ausdruecklich angegeben, deshalb wird
fortgefahren."

### 4.4 Der Teilbau

```python
# urteilslauf.py:501
def lies_bauliste(bauliste_pfad: Path) -> dict[str, object]: ...

# urteilslauf.py:516
def bauliste_indizes(bauliste: dict[str, object]) -> list[int]: ...

# urteilslauf.py:528
def ist_gebaut(ziel_dir: Path, index: int) -> bool: ...

# urteilslauf.py:538
def offene_indizes(bauliste: dict[str, object], ziel_dir: Path) -> list[int]: ...

# urteilslauf.py:547
def baue_teilbauliste(bauliste: dict[str, object], offene: Sequence[int]) -> dict[str, object]: ...

# urteilslauf.py:583
def schreibe_teilbauliste(pfad: Path, teil: dict[str, object]) -> Path: ...
```

`lies_bauliste` liest **roh** und nicht ueber `load_candidates`, weil die
Teilbauliste die WURZEL der Bauliste unveraendert weiterreichen soll
(`stammt_aus`, `urteile_aus`, die Zaehlungen). Fehlt oder faellt die Datei aus,
kommt ein leeres Woerterbuch zurueck.

`ist_gebaut` prueft `ziel_dir / f"kandidat-{index:02d}" / "short.mp4"`. **Der
Ordner allein zaehlt nicht** — ein abgebrochener Bau laesst `kandidat-NN` mit
Zwischenstufen zurueck, aber ohne Ergebnis.

`baue_teilbauliste` haelt die `index`-Werte **unveraendert** (`build` bildet
daraus die Ordnernamen) und **kuerzt `enthaelt`** auf die Indizes der
Teilliste. Der Docstring nennt den Anlass woertlich: „der erste echte Teilbau
vom 27.8. brach genau daran ab — Kandidat 67 verweist auf 36, und 36 war
bereits gebaut."

`schreibe_teilbauliste` schreibt nach `<AUFNAHME>\bauliste-offen.json`, JSON mit
`ensure_ascii=False, indent=2, sort_keys=True` und Schluss-Zeilenumbruch —
dasselbe Format wie `auswahl`.

Unveraendert daneben: `vorhandene_kandidatenordner` (`urteilslauf.py:478`),
`zaehle_baulisteneintraege` (`:489`), `zaehle_shorts` (`:593`).

### 4.5 Schritt 7 in `main`

`urteilslauf.py:814-870`. Der Ablauf:

1. `bauliste = lies_bauliste(...)`, `alle = bauliste_indizes(...)`,
   `offene = offene_indizes(...)`, `vorher = zaehle_shorts(ziel_dir)`.
2. Bestandsmeldung: `N Eintrag/Eintraege in der Bauliste, davon M offene`, und
   bei vorhandenen Shorts `K short.mp4 liegen bereits im Zielordner - sie
   bleiben unberuehrt`.
3. `--nur-offene-zeigen` → offene Indizes ausgeben, **Code 0, nichts gebaut,
   keine Teilbauliste**.
4. Nichts offen → `Fertig: jeder Kandidat der Bauliste ist bereits gebaut -
   nichts zu tun`, Code 0.
5. Sonst Teilbauliste schreiben, `erwartet = zaehle_baulisteneintraege(teilbauliste_pfad)`,
   `fuehre_bau_aus(job_path, teilbauliste_pfad, ziel_dir)`.
6. **`neu_gebaut = gesamt_nachher - vorher`** — der ZUWACHS. Der Kommentar
   begruendet es: „im Zielordner koennen `short.mp4` liegen, die diese Bauliste
   gar nicht nennt."
7. Schlusszeile: `Fertig: <Ziel> - <neu> von <erwartet> offenen Shorts neu
   gebaut in <s> s, <gesamt> insgesamt im Zielordner`.
8. `neu_gebaut != erwartet` → `ANGEHALTEN [bau_unvollstaendig]: N neue short.mp4
   statt M - der Rueckgabecode von build zaehlt dafuer nicht`, Code 8.

### 4.6 Namen der Sicherungen

```python
# urteilslauf.py:324
def _namensteil(wurzel: dict[str, object], feld: str) -> str: ...

# urteilslauf.py:335
def _laufteil(kandidaten_wurzel: dict[str, object]) -> str: ...

# urteilslauf.py:349
def sicherungsnamen(kandidaten_wurzel: dict[str, object]) -> tuple[str, str]: ...
```

`_laufteil` (neu): `1` bei einem Lauf, `1+2` bei zweien, gebildet aus
`auswahl.laeufe_kennung`, ersatzweise aus `lauf`.

`sicherungsnamen` bildet den Modellteil jetzt ueber `auswahl.modell_kennung`
statt ueber `_namensteil(wurzel, "modell")`. Ergebnis:
`(urteile-<kern>.json, kandidaten-<kern>.json)` mit
`kern = f"{video_name}-lauf{lauf}-{modell}"`, also etwa
`kandidaten-2026-08-25 15-14-00-lauf1+2-sonnet+opus.json`.

### 4.7 CLI

Zwei neue Fahnen:

| Zeile | Fahne | Wirkung |
|---|---|---|
| `urteilslauf.py:652` | `--nur-offene-zeigen` | nennt die offenen Indizes, baut nichts, legt keine Teilbauliste an, Code 0 |
| `urteilslauf.py:661` | `--auch-verfallen` | bietet auch Aufnahmen an, die aelter als 48 Stunden sind |

Die uebrigen Fahnen (`job_path`, `--kein-server`, `--keine-sicherung`,
`--kein-bau`, …) stehen unveraendert.

---

## 5. `kette.py` (1065 Zeilen) — der Verfall in `bestimme_aufnahme`

**Die Abschnitte 3.1 bis 3.5 des Bestands vom 27.8. mittags gelten unveraendert**
— sieben Stufen, Konstanten, Schema von `kette.json`, Rueckgabecodes, CLI.
Geaendert ist genau eine Funktion.

Neuer Import, `kette.py:76-80`:

```python
from matrix_auto_cutter.shorts.urteilslauf import (
    VERFALL_STUNDEN,
    alter_stunden,
    ist_verfallen,
)
```

```python
# kette.py:336
def bestimme_aufnahme(jobs_root: Path, name: str | None) -> tuple[str, dict[str, object] | None]: ...
```

Signatur unveraendert. Zwei Zweige sind dazugekommen:

- **ausdruecklich genannter Name** (`kette.py:347-353`): ist er verfallen, wird
  gewarnt und fortgefahren — `WARNUNG: <NAME> ist N h alt und damit aelter als
  48 Stunden - ausdruecklich angegeben, deshalb wird fortgefahren.`
- **von selbst gewaehlte Aufnahme** (`kette.py:366-378`): ist sie verfallen,
  `raise KetteFehlschlag("nur_verfallen", …, CODE_KEINE_AUFNAHME)`. Die Meldung
  nennt den Weg zurueck: `Fuer Nacharbeit: --aufnahme <NAME> ausdruecklich
  angeben.` Der Kommentar dort verweist ausdruecklich auf
  `urteilslauf.finde_aufnahme` als dieselbe Pruefung an derselben Stelle.

**Es wird nichts geloescht und nichts in eine Urteilsdatei geschrieben.** Der
Verfall ist NICHT-ANBIETEN, nicht Schreiben; der Test
`test_verfall_schreibt_in_keine_urteilsdatei` haelt das fest.

---

## 6. `judge.py` (1544 Zeilen) — die Vorauswahl auf der Urteilsseite

**Abschnitt 6 des Bestands vom 27.8. mittags (Gruppenmodus) gilt fort.**
Ergaenzt sind zwei Felder und vier JS-Funktionen.

### 6.1 `BuendelGruppe` traegt zwei neue Felder

```python
# judge.py:349
@dataclass(frozen=True)
class BuendelGruppe:
    ...
    gruppen_rang: int | None = None   # judge.py:358
    vorauswahl: bool = True           # judge.py:359
```

Beide mit Vorgabewerten, damit eine aeltere `buendel.json` unveraendert traegt.

### 6.2 `baue_gruppen` sortiert nach Staerke, wenn sie kann

```python
# judge.py:372
def baue_gruppen(buendel: Sequence[Mapping[str, object]]) -> list[BuendelGruppe]: ...
```

Gelesen wird vom **empfohlenen** Eintrag der Gruppe (`judge.py:424-425`):

```python
gruppen_rang=_ganzzahl(empfohlen_eintrag.get("gruppen_rang")),
vorauswahl=empfohlen_eintrag.get("vorauswahl") is not False,
```

Beachte `is not False`: ein fehlendes Feld heisst **wahr**, nicht falsch.

Sortiert wird nur, wenn **alle** Gruppen einen `gruppen_rang` tragen
(`judge.py:428-429`):

```python
if all(gruppe.gruppen_rang is not None for gruppe in gruppen):
    gruppen.sort(key=lambda gruppe: (gruppe.gruppen_rang or 0, gruppe.nummer))
```

**Die Gruppennummer bleibt unveraendert an der Gruppe haengen und wird weiter
angezeigt**, damit ein Verweis auf „Gruppe 12" dieselbe Gruppe meint wie in
`buendel.json`.

`_gruppe_to_js_dict` (`judge.py:433`) reicht beide Felder als `gruppen_rang` und
`vorauswahl` ins JS-Literal `GRUPPEN` durch (`judge.py:441-442`).

### 6.3 Die vier JS-Funktionen

| Zeile | Funktion | Aufgabe |
|---|---|---|
| `judge.py:834` | `vorauswahlAktiv()` | wahr, sobald **eine** Gruppe einen `gruppen_rang` traegt |
| `judge.py:840` | `vorauswahlGruppen()` | die vorausgewaehlten — ohne Vorauswahl: alle |
| `judge.py:845` | `uebrigeGruppen()` | die uebrigen — ohne Vorauswahl: keine |
| `judge.py:1336` | `buildUebrige(uebrige)` | der `<details>`-Aufklapper |

`vorauswahlGruppen()` gibt ohne aktive Vorauswahl **alle** Gruppen zurueck — so
muss keine der drei aufrufenden Stellen zwei Faelle kennen.

**`buildUebrige` baut die Karten ERST BEIM AUFKLAPPEN** (`toggle`-Ereignis,
einmalig ueber ein `gebaut`-Merkzeichen). Begruendung im Kommentar: „32 Gruppen
mit je einem `<video>` im Vorrat kosten Ladezeit fuer etwas, das der Nutzer in
den meisten Sitzungen gar nicht ansieht." Die Beschriftung lautet „1 weitere
Gruppe anzeigen" bzw. „N weitere Gruppen anzeigen".

Drei angepasste Stellen:

- `buildInitialQueue` (`judge.py:869`) fuehrt nur ueber `vorauswahlGruppen()`.
- `updateProgress` (`judge.py:985-991`) zaehlt die Vorauswahl und haengt bei
  aktiver Vorauswahl `" (Vorauswahl; N Gruppen insgesamt)"` an. Kommentar:
  „‚3 von 47' saehe nach Rueckstand aus, wo der Nutzer in Wahrheit schon ein
  Fuenftel seines Arbeitsvorrats erledigt hat."
- Der Seitenaufbau (`judge.py:1363`) zeichnet `vorauswahlGruppen()` offen und
  haengt `buildUebrige(uebrigeGruppen())` darunter, sofern etwas uebrig ist.

CSS fuer den Aufklapper: `judge.py:714-725`, Klasse `.uebrige-gruppen`,
gestrichelter Rand — sichtbar als eigene Zeile, nicht versteckt.

### 6.4 `judge_server.py` — unveraendert

**Abschnitt 7 des Bestands vom 27.8. mittags gilt unveraendert.** Der Rueckfall
auf die flache Liste bei fehlender, unlesbarer oder nicht passender
`buendel.json` ist derselbe; die Vorauswahl ist eine Schicht darueber und
aendert am Rueckfall nichts.

---

## 7. Das Schema von `buendel.json` — zwei Kandidatenfelder, ein Wurzelfeld

**Abschnitt 5 des Bestands vom 27.8. mittags gilt fort.** Dazugekommen sind
drei Felder; die Beschreibungen stehen woertlich in
`docs\repeat\BUENDELUNG-AUFTRAGSTEXT.md` (ergaenzt mit `897e762`).

**Wurzelfeld:**

| Feld | Typ | Bedeutung |
|---|---|---|
| `vorauswahl_groesse` | Ganzzahl, immer `15` | So viele Gruppen traegt die Vorauswahl. Auch bei weniger als 15 Gruppen steht hier `15`, und alle Gruppen sind vorausgewaehlt. |

**Kandidatenfelder:**

| Feld | Typ | Bedeutung |
|---|---|---|
| `gruppen_rang` | Ganzzahl ab 1, **eindeutig ueber alle Gruppen** | ordnet die GRUPPEN untereinander. Alle Kandidaten derselben Gruppe tragen denselben Wert. Bei 46 Gruppen die Werte 1 bis 46, jeder genau einmal. |
| `vorauswahl` | Wahrheitswert | wahr bei allen Kandidaten der Gruppen mit `gruppen_rang` 1 bis 15, falsch bei allen uebrigen. |

**Nicht zu verwechseln:** `rang` ordnet die Kandidaten INNERHALB einer Gruppe,
`gruppen_rang` ordnet die GRUPPEN. Und `gruppe` folgt Projekt und Zeit,
`gruppen_rang` folgt der Staerke — Gruppe 1 kann `gruppen_rang` 30 tragen.

Wonach gerangt wird, steht im Auftragstext unter „Die Vorauswahl": nach der
Staerke des **besten** Kandidaten der Gruppe, nach denselben drei Fragen wie
innerhalb der Gruppe (steht er fuer sich, sitzen die Grenzen sauber, traegt er
eine eigene Aussage). Ausdruecklich **nicht** nach Haeufigkeit des Themas, nicht
nach Laenge, nicht nach Stelle in der Aufnahme.

---

## 8. `labels\repeat\trefferquote.json` — jetzt drei Eintraege

Der Bestand vom 27.8. mittags fuehrt in Abschnitt 10 **zwei** Eintraege. Es sind
jetzt **drei**; der dritte ist mit `5379982` dazugekommen.

| # | `video_name` | `lauf` | `laeufe` | `modell` | `kandidaten_gesamt` | `quote` |
|---|---|---|---|---|---|---|
| 1 | `2026-08-21 10-46-08` | 1 | fehlt | `sonnet` | 31 | 0.871 |
| 2 | `2026-08-25 15-14-00` | 1 | fehlt | `sonnet` | 39 | 0.641 |
| 3 | `2026-08-25 15-14-00` | 1 | `[1, 2]` | `unbekannt` | 69 | 0.609 |

`schema_version` unveraendert `"1.0"`. Die Eintraege 1 und 2 tragen weiterhin
**13** Wurzelfelder; Eintrag 3 traegt **15** — dazu `laeufe` und `notiz`
(letzteres leer). Ein Alteintrag ohne `laeufe` gilt beim Vergleich als `[lauf]`.

SHA-256 der Arbeitsbaumfassung:
`9b2c60d1545297e0a6aafb37e0f4d4306c5d4af0779579a1b816f87e12250223`.
**Achtung:** die Dateien unter `labels\repeat\` liegen im Repository mit LF, im
Arbeitsbaum mit CRLF — ein Hashvergleich zwischen beiden Seiten schlaegt fehl,
obwohl der Inhalt derselbe ist. Bei JSON ist der Unterschied folgenlos.

---

## 9. Auftragstexte

`docs\repeat\BUENDELUNG-AUFTRAGSTEXT.md` (238 Zeilen) ist mit `897e762`
ergaenzt worden:

- Der Aufzaehlungspunkt **„ZWISCHEN Gruppen wird NICHT gerangt"** ist ersetzt
  durch **„ZWISCHEN Gruppen wird gerangt — aber nur nach der Staerke des
  Ausschnitts, nie nach der Wichtigkeit des Themas."** Der Sinn des alten Satzes
  bleibt erhalten: welches Thema es wert ist, veroeffentlicht zu werden,
  entscheidet weiterhin der Nutzer.
- Neuer Abschnitt „Die Vorauswahl: `gruppen_rang` und `vorauswahl`".
- Die Selbstpruefpunkte sind von **fuenf auf sieben** gewachsen. Punkt 6:
  `gruppen_rang` lueckenlos, jeder Wert genau einmal, alle Kandidaten einer
  Gruppe mit demselben Wert. Punkt 7: genau `min(15, Gruppenzahl)` Gruppen mit
  `vorauswahl` wahr, und zwar genau die mit den kleinsten Werten.

`docs\repeat\ZERLEGUNG-AUFTRAGSTEXT.md` ist **unveraendert** seit `357f12f`.

---

## 10. Gates und Tests

Beide Auftraege des 27.8. haben ihre Gates gefahren. Werte aus den Berichten
uebernommen — **in diesem Auftrag NICHT nachgefahren**, pytest, ruff und mypy
sind hier gesperrt.

| Gate | `vorauswahl-verfall` | `teilbau` (Endstand) |
|---|---|---|
| `uv run python -m pytest` | 2792 bestanden, 1 uebersprungen, 0 Fehler | **2800 bestanden**, 1 uebersprungen, 3 Warnungen, 0 Fehler |
| `uv run ruff check .` | All checks passed! | All checks passed! |
| `uv run mypy src` | genau 20 Fehler in 3 Dateien | 20 Fehler in 3 Dateien — unveraendert |

**Neue Testdatei:** `tests\test_shorts_vorauswahl_verfall.py`, **597 Zeilen**,
36 Tests.

**`tests\test_shorts_urteilslauf.py`** ist auf **968 Zeilen** gewachsen; acht
Tests sind fuer den Teilbau dazugekommen, keiner schreibt auf `F:`
(`bauziel_umgebogen` biegt `RENDER_WURZEL` auf `tmp_path`). Der Testhelfer
`_baue_aufnahme` nimmt jetzt `indizes=[…]` fuer nicht lueckenlose
Kandidatennummern.

**Vier bestehende Testfunktionen wurden geaendert oder ersetzt** — alle in den
Berichten genannt:

1. `test_finde_aufnahme_nimmt_juengste_kandidatendatei` — ruft `finde_aufnahme`
   jetzt mit `auch_verfallen=True`; ihre Aufnahmeordner heissen `2026-08-01`
   und `2026-07-01` und waeren sonst verfallen. Die gepruefte Regel ist
   unveraendert.
2. `test_vorhandener_kandidatenordner_haelt_mit_code_7_an` — **gleichwertig
   ersetzt** durch `test_vorhandener_kandidatenordner_wird_nicht_neu_gebaut`.
3. `test_weniger_shorts_als_baulisteneintraege_ist_code_8` und
4. `test_vollstaendiger_bau_endet_mit_code_0` — je zwei Zusicherungen an den
   neuen Wortlaut der Schlusszeile angepasst; die Aussage der Tests ist
   unveraendert.

Weiterhin gilt: 20 vorbestehende mypy-Fehler, drei Pytest-Warnungen, sieben
Tests mit `@pytest.mark.echter_unterprozess`, und
`test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state`
als flatterhaft.

---

## 11. Was nicht gebaut ist

**Abschnitt 14 des Bestands vom 27.8. mittags gilt unveraendert**, mit einer
Streichung: **Vorauswahl und Verfall sind gebaut.** Offen bleiben der Wecker,
die Uploadstufe, der Aufraeumer fuer `F:` und die Oberflaeche — siehe Abschnitt
6 der Uebergabe vom 27.8. abends.

Nicht gebaut, aber neu sichtbar geworden: **es gibt keine Stelle, an der
festgehalten wird, welche Shorts tatsaechlich veroeffentlicht wurden.** Ohne die
laesst sich die Vorauswahl nicht gegen das eigentliche Mass pruefen (Abschnitt
7.1 der Uebergabe). Ob das geplant ist, ist **nicht belegt**.

---

## Pruefsteine dieses Auftrags

Sie stehen in der Antwort des ausfuehrenden Fensters, nicht hier. Dieses
Dokument und `ORCHESTRATOR-UEBERGABE-2026-08-27-abend.md` sind die beiden
einzigen Dateien, die der Auftrag `uebergabe-27-08-abend` geschrieben hat.
