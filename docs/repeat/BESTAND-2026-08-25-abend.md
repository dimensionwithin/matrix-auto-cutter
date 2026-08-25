# Bestand -- Shorts-Linie, Stand 2026-08-25 abends

Fortschreibung von `BESTAND-2026-08-25.md`. Auftrag: `uebergabe-25-08-abend`,
rein lesend ausser den beiden neuen Dokumenten unter `docs\repeat\`.
Repository `P:\DimensionWithin-MatrixMarketAutoEditor`.

**Wie dieses Dokument zu lesen ist:** Der Bestand vom 25.8. nachmittags gilt
weiter, wo hier nichts anderes steht. Dieses Dokument nennt, was seit
`a47921e` neu ist, und sagt bei jedem uebernommenen Abschnitt ausdruecklich,
dass er unveraendert gilt. Wer eine Modulsignatur, eine Konstante oder ein
Dateiformat der Stufen 0 bis 5d, `wortliste.py` oder `auswahl.py` sucht,
schlaegt dort nach.

Alle Zeilennummern sind in diesem Auftrag an den Dateien geprueft.

---

## 1. Stand

**HEAD:** `8e6ab9a` auf `master`, gepusht.

**`git status --porcelain` woertlich:**

```
 M labels/repeat/trefferquote.json
?? -
?? "labels/repeat/kandidaten-2026-08-25 15-14-00-lauf1-sonnet.json"
?? "labels/repeat/urteile-2026-08-25 15-14-00-lauf1-sonnet.json"
```

Die Datei `-` (9 Byte) ist unveraendert der bekannte Testrueckstand, Ursache
belegt in Abschnitt 12.1 des Bestands vom Nachmittag. Die drei
`labels\repeat\`-Zeilen sind die Belege des Betriebslaufs vom Abend
(Abschnitt 8); sie wurden von `urteilslauf` Schritt 6 geschrieben und sind
noch nicht committet.

**Die vier Commits seit `a47921e`:**

| Kuerzel | Betreff |
|---|---|
| `8e6ab9a` | Shorts: Modell der Zerlegung waehlbar, Nachschlagbefehl im Urteilslauf |
| `7c4195d` | Shorts: Kettenlaeufer, Bau im Urteilslauf, Zielwurzel berichtigt |
| `954c8a0` | Shorts: Stufe 0 kopflos, Platzhalter mit Zeitangabe |
| `d6dc3ee` | Shorts: Urteilslauf als ein Befehl, mit unterbrechbarem Warten |
| `a47921e` | Doku: Uebergabe und Bestand vom 25.8. |

---

## 2. Die Module der Shorts-Linie

Abschnitt 2 des Bestands vom Nachmittag **gilt unveraendert**. Neu
hinzugekommen ist **ein** Modul.

**`.py`-Dateien unter `src\matrix_auto_cutter\shorts\`: 28** (nachgezaehlt in
diesem Auftrag). Vorher 27; die Differenz ist `kette.py`.

---

## 3. `kette.py` (751 Zeilen) -- der Kettenlaeufer

Neu mit `7c4195d`, erweitert mit `8e6ab9a`. Sechs Stufen von der gerenderten
Aufnahme bis zu `kandidaten.json`, mit einer Zustandsdatei nach jeder Stufe.

**Zweck laut Modul-Docstring (Z.1-16):** die sechs Stufen waren einzeln
erprobte Werkzeuge und wurden bisher von Hand angestossen. Das Modul rechnet
nichts selbst aus -- es startet Prozesse, wartet auf sie, schreibt den Stand
fort und meldet Fortschritt. Kein einziges der aufgerufenen Werkzeuge meldet
von sich aus, wie weit es ist.

### 3.1 Konstanten

| Name | Wert | Zeile |
|---|---|---|
| `JOBS_ROOT` | `Path("artefakte") / "repeat" / "shorts"` | 65 |
| `ZUSTAND_FILE_NAME` | `"kette.json"` | 66 |
| `JOB_FILE_NAME` | `"shorts-job.json"` | 67 |
| `ARTIFACT_TYPE` | `"matrix_auto_cutter_shorts_kette"` | 68 |
| `SCHEMA_VERSION` | `"1.0"` | 69 |
| `CODE_ERFOLG` | `0` | 71 |
| `CODE_KEINE_AUFNAHME` | `2` | 72 |
| `CODE_STUFE_GESCHEITERT` | `5` | 73 |
| `CODE_ZUSAMMENFUEHRUNG_FEHLT` | `6` | 74 |
| `TRANSKRIPTION_FAKTOR` | `1.27` | 80 |
| `FORTSCHRITT_TAKT_SEKUNDEN` | `30.0` | 82 |
| `_POLL_TAKT_SEKUNDEN` | `0.25` | 83 |
| `CLAUDE_BEFEHL` | `"claude"` | 85 |
| `ZERLEGUNG_AUFTRAGSTEXT_PFAD` | `Path("docs") / "repeat" / "ZERLEGUNG-AUFTRAGSTEXT.md"` | 86 |
| `ZERLEGUNG_MODELL` | `"sonnet"` -- Vorgabewert von `--modell` | 87 |
| `ZERLEGUNG_LAUF` | `1` -- **fest, kein Zaehler**, s. Abschnitt 9.1 | 88 |
| `STATUS_OFFEN` / `_LAEUFT` / `_FERTIG` / `_GESCHEITERT` | `"offen"` / `"laeuft"` / `"fertig"` / `"gescheitert"` | 90-93 |
| `_KANDIDATEN_LAUF_GLOB` | `"kandidaten-lauf*.json"` | 114 |

### 3.2 Die sechs Stufen

`@dataclass(frozen=True) class Stufe` (Z.96-102): `name`, `ausgabe`,
`beschreibung`.

`STUFEN: tuple[Stufe, ...]` (Z.105-112):

| # | `name` | `ausgabe` | `beschreibung` | Prozess |
|---|---|---|---|---|
| 1 | `auftrag` | `shorts-job.json` | Auftragsdatei schreiben | `-m shorts.auftrag --aufnahme NAME --ausgabe PFAD [--force]` |
| 2 | `avatar_cut` | `avatar-cut.mp4` | Avatar nachschneiden | `-m shorts.avatar_cut JOB --output-root WURZEL` |
| 3 | `transcript` | `transkript-rendered.json` | Transkription | `-m shorts.transcript JOB [--force]` |
| 4 | `wortliste` | `wortliste.json` | Wortliste | `-m shorts.wortliste JOB [--force]` |
| 5 | `zerlegung` | `kandidaten-lauf1.json` | Zerlegung (Modell) | `claude -p TEXT --model MODELL --permission-mode acceptEdits` |
| 6 | `zusammenfuehrung` | `kandidaten.json` | Zusammenfuehrung | **kein Prozess** -- Kopie im Modul |

Die Ausgabe der Stufe 5 wird aus `ZERLEGUNG_LAUF` gebildet
(`f"kandidaten-lauf{ZERLEGUNG_LAUF}.json"`, Z.110) und ist damit heute fest
`kandidaten-lauf1.json`.

### 3.3 Schema von `kette.json`

Geschrieben von `schreibe_zustand` (Z.179) atomar ueber
`replace_atomically`, mit `indent=2`, `sort_keys=True`, `ensure_ascii=False`.
Nach **jeder** Stufe, nicht erst am Ende.

```json
{
  "artifact_type": "matrix_auto_cutter_shorts_kette",
  "schema_version": "1.0",
  "video_name": "2026-08-25 15-14-00",
  "begonnen_am": "2026-08-25T20:37:20.997898+00:00",
  "zuletzt_am":  "2026-08-25T21:51:38.364105+00:00",
  "stufen": {
    "<stufenname>": {
      "status":      "offen" | "laeuft" | "fertig" | "gescheitert",
      "begonnen_am": "<ISO 8601 mit Zeitzone>" | null,
      "beendet_am":  "<ISO 8601 mit Zeitzone>" | null,
      "dauer_s":     <float> | null,
      "ausgabe":     "<absoluter Pfad>" | null,
      "meldung":     "<Text>" | null,
      "modell":      "<Name>"        // NUR bei "zerlegung", nur wenn gelaufen
    }
  }
}
```

Zeitstempel in **UTC** (`_jetzt()`, Z.133, `datetime.now(UTC).isoformat()`).
Belegt am Istbestand von
`artefakte\repeat\shorts\2026-08-25 15-14-00\kette.json`, in diesem Auftrag
gelesen.

**Das Feld `modell`** entsteht nur im Anlauf-Zweig von `main` (Z.700-706),
nicht im Ueberspring-Zweig -- eine uebersprungene Zerlegung hat mit dem Modell
von heute nichts gefahren.

**Achtung, Abschnitt 9.2:** der Ueberspring-Zweig setzt nur `status`,
`ausgabe` und `meldung`. `dauer_s`, `begonnen_am` und `beendet_am` bleiben aus
dem vorigen Lauf stehen.

### 3.4 Funktionen

**Zustandsdatei:**
- `_jetzt() -> str` (Z.133)
- `_leerer_eintrag() -> dict[str, object]` (Z.138) -- die sechs Felder oben,
  alle `null` ausser `status: "offen"`
- `leerer_zustand(video_name: str) -> dict[str, object]` (Z.150)
- `lies_zustand(pfad: Path) -> dict[str, object] | None` (Z.163) -- unlesbar
  oder unerwartet heisst `None`; **ein kaputter Zustand ist kein Abbruchgrund**
- `schreibe_zustand(pfad: Path, zustand: dict[str, object]) -> None` (Z.179)
- `_eintrag(zustand, name) -> dict[str, object]` (Z.207) -- fehlt der Eintrag,
  entsteht er offen

**Aufnahme bestimmen:**
- `finde_laufende_kette(jobs_root: Path) -> tuple[Path, dict] | None` (Z.225)
  -- der Aufnahmeordner mit der juengsten `kette.json`. **Der Weg, auf dem der
  Bestand auf `F:` gar nicht erst angefasst wird.**
- `bestimme_aufnahme(jobs_root: Path, name: str | None) -> tuple[str, dict | None]`
  (Z.251)

**Dauer und Schaetzung:**
- `dauer_text(sekunden: float) -> str` (Z.282) -- `"12 min 22 s"`; **schneidet
  ab, rundet nicht**
- `audiodauer_s(job_path: Path) -> float | None` (Z.290)
- `erwartete_transkriptionsdauer_s(job_path: Path) -> float | None` (Z.314) --
  `duration_ms / 1000 * TRANSKRIPTION_FAKTOR`

**Prozesse:**
- `zerlegung_auftragstext(video_name, lauf=ZERLEGUNG_LAUF, modell=ZERLEGUNG_MODELL) -> str`
  (Z.325) -- der **Verweis** auf `ZERLEGUNG-AUFTRAGSTEXT.md`, nicht der
  Auftragstext selbst. Setzt `<AUFNAHME>`, `<N>` und den Wert des Wurzelfelds
  `modell`.
- `zerlegung_argv(video_name, lauf=ZERLEGUNG_LAUF, modell=ZERLEGUNG_MODELL) -> list[str]`
  (Z.348) -- `[CLAUDE_BEFEHL, "-p", TEXT, "--model", MODELL,
  "--permission-mode", "acceptEdits"]`
- `stufen_argv(stufe, *, job_path, jobs_root, video_name, erzwingen, modell=ZERLEGUNG_MODELL) -> list[str] | None`
  (Z.371) -- `None` heisst: kein Prozess, sondern Handarbeit (nur die
  Zusammenfuehrung)
- `fuehre_prozess(argv: Sequence[str], *, etikett: str) -> int` (Z.413) --
  **der einzige Ort, an dem das Modul einen Prozess startet.** Tests biegen
  genau diese Funktion um. Wartet in Takten von `_POLL_TAKT_SEKUNDEN` statt in
  einem `wait()` und meldet alle `FORTSCHRITT_TAKT_SEKUNDEN`:
  `  transcript laeuft seit 5 min 30 s ...` mit `flush=True`. Ein fehlendes
  Programm ist ein `KetteFehlschlag`, kein Absturz.

**Zusammenfuehrung:**
- `laufdateien(job_dir: Path) -> list[Path]` (Z.454) -- alle
  `kandidaten-lauf*.json`, nach Namen geordnet
- `fuehre_zusammen(job_dir: Path) -> Path` (Z.459) -- bei **einer** Laufdatei
  eine Kopie nach `kandidaten.json`; bei **mehreren** `KetteFehlschlag`
  `zusammenfuehrung_fehlt` mit `CODE_ZUSAMMENFUEHRUNG_FEHLT` (6), weil die
  Zusammenfuehrungslogik nicht gebaut ist; bei **keiner**
  `CODE_STUFE_GESCHEITERT` (5)

**Ablauf:**
- `stufen_index(bezeichnung: str) -> int` (Z.492) -- `"4"` und `"wortliste"`
  bezeichnen dieselbe Stufe; unbekannt → `stufe_unbekannt`, Code 5
- `wird_uebersprungen(stufe, job_dir, zustand) -> bool` (Z.515) -- Ausgabe muss
  daliegen; ein **fehlender** Eintrag und `offen` stehen dem Ueberspringen
  nicht entgegen, `laeuft` und `gescheitert` schon
- `_kopfzeile(nummer, stufe, job_path, modell=ZERLEGUNG_MODELL) -> str` (Z.533)
  -- `Stufe 3 von 6: Transkription, erwartet rund 12 min 22 s` bzw.
  `Stufe 5 von 6: Zerlegung (Modell), Modell opus`
- `_trockenlauf(job_dir, job_path, zustand, bis, modell=ZERLEGUNG_MODELL) -> int`
  (Z.559) -- **schreibt auch keine Zustandsdatei**
- `_fuehre_stufe_aus(stufe, *, job_dir, job_path, jobs_root, video_name, erzwingen, modell=ZERLEGUNG_MODELL) -> None`
  (Z.585) -- Rueckgabecode 0 allein genuegt nicht; die erwartete Ausgabe muss
  danach dasein
- `_parser() -> argparse.ArgumentParser` (Z.631)
- `main(argv: Sequence[str] | None = None) -> int` (Z.657)

`class KetteFehlschlag(Exception)` (Z.117): `code_name`, `text`,
`rueckgabecode`. Muster wie `auftrag.py`.

### 3.5 CLI

```powershell
python -m matrix_auto_cutter.shorts.kette [--aufnahme NAME] [--neu]
       [--neu-ab STUFE] [--bis STUFE] [--trocken] [--modell NAME]
       [--wurzel PFAD]
```
(`kette.py:631-654`)

| Fahne | Wirkung |
|---|---|
| `--aufnahme NAME` | Name der Aufnahme; ohne: die juengste (ueber `kette.json`, sonst ueber den Bestand) |
| `--neu` | alle Stufen erzwingen |
| `--neu-ab STUFE` | ab dieser Stufe erzwingen, Name oder 1-6 |
| `--bis STUFE` | nach dieser Stufe anhalten, Name oder 1-6 |
| `--trocken` | nur nennen, was geschaehe; fuehrt nichts aus und schreibt nichts |
| `--modell NAME` | Modell der Zerlegung, an `claude --model` und in den Auftragstext. **Vorgabe `sonnet`** |
| `--wurzel PFAD` | abweichende Repo-Wurzel |

Rueckgabecodes: 0 Erfolg, 2 keine Aufnahme, 5 Stufe gescheitert oder Stufe
unbekannt, 6 Zusammenfuehrung fehlt.

---

## 4. Die Aenderungen an `urteilslauf.py` (650 Zeilen)

Das Modul entstand mit `d6dc3ee` und wurde mit `954c8a0`, `7c4195d` und
`8e6ab9a` erweitert. Es steht im Bestand vom Nachmittag noch nicht.

### 4.1 Konstanten

| Name | Wert | Zeile |
|---|---|---|
| `SICHERUNG_DIR` | `Path("labels/repeat")` | 49 |
| `AUFNAHMEN_UNTERPFAD` | `Path("artefakte/repeat/shorts")` | 50 |
| `JOB_FILE_NAME` | `"shorts-job.json"` | 51 |
| **`RENDER_WURZEL`** | **`r"F:\MatrixMarketAutoEdit\Shorts Rendered"`** -- mit LEERZEICHEN, s. Abschnitt 7 | 52 |
| `NACHSCHLAG_MODELL` | `"opus"` | 53 |
| `_CODE_ERFOLG` | `0` | 55 |
| `_CODE_KEINE_AUFNAHME` / `_CODE_AUFTRAG_UNLESBAR` | `2` | 56-57 |
| `_CODE_URTEILE_ABWEICHUNG` | `5` | 58 |
| `_CODE_SICHERUNG_FEHLGESCHLAGEN` | `6` | 59 |
| **`_CODE_ZIEL_BELEGT`** | **`7`** | 60 |
| **`_CODE_BAU_UNVOLLSTAENDIG`** | **`8`** | 61 |
| `_BEENDE_FRIST_SEKUNDEN` | `5.0` | 63 |
| `_PLATZHALTER_SEKUNDEN` | `4.0` | 66 |
| `_SHORT_NAME` | `"short.mp4"` | 69 |

### 4.2 Die sieben Schritte in `main` (Z.462)

| Schritt | Zeile | Was |
|---|---|---|
| 1 Aufnahme bestimmen | 499 | `job_path` oder juengste Kandidatendatei unter `AUFNAHMEN_UNTERPFAD` |
| 2 Urteile pruefen | 533 | `pruefe_vor_start`; Abweichung → Code 5, **Urteilsseite wird nicht gestartet** |
| 3 Urteilsseite | 548 | `starte_urteilsseite`, endet durch Strg+C des Nutzers |
| 4 Quote | 561 | Quotenzeile **mit Prozent** plus Nachschlagzeile, s. 4.4 |
| 5 Bauliste | 582 | `auswahl.main([job_dir])` |
| 6 Sicherung | 594 | `sichere_urteile` nach `labels\repeat\` |
| 7 **Bau** | 607 | s. 4.3 |

`lies_kandidaten_wurzel` wird seit `8e6ab9a` **vor Schritt 4** gelesen (Z.563),
weil die Nachschlagzeile `video_name` schon dort braucht; Schritt 6 und 7
benutzen dieselbe Variable weiter.

### 4.3 Der Bau in Schritt 7

Neu mit `7c4195d`. Ablauf (Z.607-645):

1. `--kein-bau` → Bauzeile ausgeben, Code 0, Ende.
2. Bauliste fehlt → Code **8**.
3. Zielordner traegt schon `kandidat-NN`-Ordner → Code **7**, nichts wird
   angefasst. Begruendung im Code: `build` ueberspringt vorhandene Ausgaben
   nicht und baute Fertiges neu.
4. `ziel_dir.mkdir(parents=True, exist_ok=True)`; scheitert das → Code 8.
5. `fuehre_bau_aus`, dann `zaehle_shorts(ziel_dir)`.
6. Schlusszeile `Fertig: {ziel_dir} - {gebaut} von {erwartet} Shorts in {dauer} s`.
7. `gebaut != erwartet` → Code **8** mit dem Zusatz „der Rueckgabecode von
   build zaehlt dafuer nicht".

Zugehoerige Funktionen:
- `bauziel(video_name: str) -> Path` (Z.390) -- `Path(RENDER_WURZEL) / video_name`
- `baubefehl(job_path, bauliste_pfad, video_name) -> str` (Z.376) -- nur zum
  Hinschreiben. **Ohne abschliessenden Trennstrich im `--output-dir`**: unter
  Windows maskierte ein Backslash vor dem schliessenden Anfuehrungszeichen
  dieses, und die Zeile waere nicht kopierbar.
- `vorhandene_kandidatenordner(ziel_dir) -> list[str]` (Z.400)
- `zaehle_baulisteneintraege(bauliste_pfad) -> int` (Z.411)
- `zaehle_shorts(ziel_dir) -> int` (Z.423)
- `_bau_argv(job_path, bauliste_pfad, ziel_dir) -> list[str]` (Z.437) --
  dieselbe Zeile, die `baubefehl` hinschreibt
- `fuehre_bau_aus(job_path, bauliste_pfad, ziel_dir) -> int` (Z.450)

### 4.4 Quote und Nachschlagzeile

Neu mit `8e6ab9a`.

- `quote_prozent(ja: int, gesamt: int) -> int` (Z.347) -- ganze Prozent,
  `0` bei `gesamt <= 0`
- `nachschlagbefehl(video_name: str, modell: str = NACHSCHLAG_MODELL) -> str`
  (Z.360) -- nur zum Hinschreiben, **nie ausgefuehrt**

Ausgabe von Schritt 4, woertlich aus dem Lauf vom 25.8. abends:

```
Schritt 4: Urteile zaehlen
  31 von 31 beurteilt - 27 ja, 4 nein, 0 offen - Quote 87 %
  Nachschlag mit einem anderen Modell, falls die Ausbeute zu mager war:
  python -m matrix_auto_cutter.shorts.kette --aufnahme "2026-08-21 10-46-08" --neu-ab zerlegung --modell opus
  Achtung: die Zusammenfuehrung ist NICHT gebaut - liegen mehrere kandidaten-lauf*.json vor, haelt kette mit Code 6 an. Heute schreibt der Nachschlag ohnehin wieder kandidaten-lauf1.json und ueberschreibt den ersten Lauf.
```

Die drei Zeilen erscheinen **immer**, nicht nur bei niedriger Quote -- es gibt
keine Schwelle im Code. Der `video_name` kommt aus dem Wurzelfeld der
Kandidatendatei, nicht aus dem Ordnernamen; die beiden duerfen
auseinanderfallen.

Der Warnsatz ueber das Ueberschreiben ist eine Berichtigung der Auftragsvorgabe
-- Begruendung in `modellfahne\BERICHT-2026-08-25.md`, TEIL 2, und in der
Uebergabe vom Abend, Abschnitt 1.2.

### 4.5 Der Platzhalterserver

- `_server_argv(job_path, *, platzhalter: bool | float) -> list[str]` (Z.181)
  -- `platzhalter` ist `False` (echter Server), `True` (Vorgabedauer) oder eine
  Sekundenzahl
- `starte_urteilsseite(job_path, *, platzhalter: bool | float = False) -> int`
  (Z.193)
- `warte_auf_kind(...)` (Z.161) -- Warteschleife statt `wait()`, s. Abschnitt 9.4
- `_abbruch_merker()` (Z.125), `_beende_kind(process)` (Z.109)

```powershell
--platzhalter-server [SEKUNDEN]
```
`nargs="?"`, `type=float`, `const=_PLATZHALTER_SEKUNDEN` (4.0), `default=False`
(`urteilslauf.py:482-493`). Ohne Zahl 4,0 s. Der Platzhalter ist ein
schlafender Einzeiler `python -c "import time; time.sleep(N)"` -- damit laesst
sich Strg+C ueben, ohne den Urteilsserver auf einen echten Auftragsordner
loszulassen.

### 4.6 CLI

```powershell
python -m matrix_auto_cutter.shorts.urteilslauf [JOB_PATH] [--kein-server]
       [--keine-auswahl] [--keine-sicherung] [--kein-bau]
       [--platzhalter-server [SEKUNDEN]] [--wurzel PFAD]
```
(`urteilslauf.py:462-494`; `JOB_PATH` ist optional -- ohne ihn wird die
juengste Aufnahme gesucht)

Rueckgabecodes: 0, 2 (keine Aufnahme / Auftrag unlesbar), 5 (Urteile weichen
ab), 6 (Sicherung fehlgeschlagen), **7 (Ziel belegt)**, **8 (Bau
unvollstaendig)**.

---

## 5. Die Riegel-Fixtures in `tests\conftest.py` (283 Zeilen)

Neu mit `7c4195d`, ab Zeile 161. Der bestehende Inhalt (kanonische Fixtures
des Vertragskerns: `raw_sidecar`, `expected_source`, `parsed_sidecar` und ihre
Hilfsfunktionen) steht unveraendert davor.

### 5.1 Konstanten und Hilfen

| Name | Wert | Zeile |
|---|---|---|
| `MARKE_UNTERPROZESS` | `"echter_unterprozess"` | 175 |
| `_SHORTS_PRAEFIX` | `"test_shorts_"` | 176 |
| `_FREMDE_WURZEL` | `"f:"` | 177 |

- `class RiegelVerletzt(Exception)` (Z.180) -- **erbt bewusst NICHT von
  `OSError`**: `Path.exists()` faengt `OSError` und `ValueError` ab und meldete
  dann bloss „gibt es nicht"; der Test waere gruen geblieben und der Riegel
  unsichtbar.
- `_ist_shorts_test(request) -> bool` (Z.189) --
  `request.path.name.startswith("test_shorts_")`. **Die eine Zeile, an der die
  Reichweite des Riegels haengt.**
- `_zeigt_nach_f(wert) -> bool` (Z.193) -- erste zwei Zeichen `f:`,
  Gross-/Kleinschreibung egal, `\` und `/` egal, `bytes` und `os.PathLike`
  eingeschlossen, offene Dateideskriptoren (`int`) ausgenommen.

### 5.2 `kein_echter_unterprozess` (Z.206, `autouse`)

Biegt `subprocess.run` und `subprocess.Popen` auf einen Wehrhandler um, der
`RiegelVerletzt` mit dem versuchten Befehl in der Meldung wirft. Greift nicht,
wenn der Test `@pytest.mark.echter_unterprozess` traegt (Z.218).

Alle zwoelf Module unter `shorts\`, die Prozesse starten, machen
`import subprocess`; keines `from subprocess import run`. Das Umbiegen der
Modulattribute greift damit ueberall.

### 5.3 `kein_zugriff_auf_f` (Z.252, `autouse`)

Umwickelt elf `os`-Funktionen und `builtins.open` (`_OS_TUEREN`, Z.237):
`stat`, `lstat`, `mkdir`, `rmdir`, `remove`, `unlink`, `rename`, `replace`,
`listdir`, `scandir`, `open`. Ein Pfad, der nach `F:` zeigt, wirft
`RiegelVerletzt`.

**Verriegelt wird die `os`-Ebene, nicht `pathlib`:** `Path.exists()`,
`Path.mkdir()`, `Path.open()`, `shutil.copy()` und `open()` laufen alle dort
zusammen. Der blosse **Bau** eines `Path("F:/...")` bleibt erlaubt -- erst das
Anfassen zaehlt.

### 5.4 Die Marke

Registriert in `pyproject.toml:36-38` (noetig wegen `--strict-markers`):

```toml
markers = [
    "echter_unterprozess: dieser Test darf wirklich einen Unterprozess starten",
]
```

**Sieben Tests tragen sie**, jeder mit einer Zeile Begruendung darueber:

| Datei | Tests |
|---|---|
| `tests\test_shorts_urteilslauf.py` | die vier Platzhalter-Tests |
| `tests\test_shorts_build.py` | die drei Prozesswache-Tests (`build._ProzessWache`) |

Bei beiden Gruppen ist der Unterprozess der Gegenstand des Tests.

`tests\test_shorts_riegel.py` (6 Tests) prueft den Riegel selbst -- die Datei
heisst `test_shorts_*`, damit die Fixtures dort genauso greifen.

---

## 6. Die Stufen

Abschnitt 6 des Bestands vom Nachmittag **gilt unveraendert**. Ergaenzung: die
dort einzeln aufgefuehrten Stufen werden seit `7c4195d` von `kette.py`
angestossen; die Zuordnung Stufe → Prozess steht in Abschnitt 3.2.

---

## 7. Die Dateiformate

Abschnitt 7 des Bestands vom Nachmittag **gilt unveraendert** fuer
`shorts-job.json`, `kandidaten.json`, `urteile-*.json`, `avatar-cut.json`,
`ausschnitt.json`, `shorts-bau-bericht.json`, `short.json`,
`transkript-rendered.json`, `transkript-rendered.wav.json`, `wortliste.json`,
`bauliste.json` und `trefferquote.json`.

**Neu hinzugekommen: `kette.json`** (Abschnitt 3.3).

**Berichtigt: die Zielwurzel des Baus.**

    F:\MatrixMarketAutoEdit\Shorts Rendered\<Aufnahme>\

**Mit LEERZEICHEN.** Der Code fuehrte bis `7c4195d` `Shorts-Rendered` mit
Bindestrich; der Ordner auf `F:` traegt ein Leerzeichen. Der Wert steht in
genau einer Konstante, `urteilslauf.py:52`.

**Die Dokumente vom 25.8. nachmittags tragen den falschen Namen** in
`BESTAND-2026-08-25.md` Z.442 und Z.514 sowie
`ORCHESTRATOR-UEBERGABE-2026-08-25.md` Z.276 und Z.300. Sie bleiben als
Zeitstand stehen; die Berichtigung steht hier und in der Uebergabe vom Abend,
Abschnitt 4.2.

---

## 8. `labels\repeat\trefferquote.json` -- zwei Eintraege

Gelesen in diesem Auftrag. Schema `1.0`, Wurzel `eintraege`.

| Feld | Eintrag 1 | Eintrag 2 |
|---|---|---|
| `video_name` | `2026-08-21 10-46-08` | `2026-08-25 15-14-00` |
| `modell` | `sonnet` | `sonnet` |
| `lauf` | 1 | 1 |
| **`quote`** | **0,871** | **0,641** |
| `kandidaten_gesamt` | 31 | 39 |
| `angenommen` | 27 | 25 |
| `abgelehnt` | 4 | 14 |
| `ohne_urteil` | 0 | 0 |
| `im_zielbereich_ja` / `_nein` | 20 / 7 | 18 / 7 |
| `polarisierend` wahr / falsch | 12 / 15 | 9 / 16 |
| `sicherheit.hoch` ja / nein | 14 / 1 | 10 / 7 |
| `sicherheit.mittel` ja / nein | 12 / 2 | 14 / 6 |
| `sicherheit.niedrig` ja / nein | 1 / 1 | 1 / 1 |
| **`kriterien_fassung`** | **`"Fassung 0.8 (24. August 2026)"`** | **`"0.8"`** |

**Die beiden Schreibweisen von `kriterien_fassung` ergeben zwei Gruppen fuer
dieselbe Fassung** -- Abschnitt 9.3.

Die zugehoerigen Belege des zweiten Laufs liegen als
`labels\repeat\kandidaten-2026-08-25 15-14-00-lauf1-sonnet.json` und
`labels\repeat\urteile-2026-08-25 15-14-00-lauf1-sonnet.json` daneben,
geschrieben von `urteilslauf` Schritt 6. **Noch nicht committet.**

Wurzelfelder der Kandidatendatei des zweiten Laufs, in diesem Auftrag gelesen:

```json
{
  "kriterien_fassung": "0.8",
  "achse": "gerendert",
  "video_name": "2026-08-25 15-14-00",
  "video_dauer_ms": 1073716,
  "lauf": 1,
  "modell": "sonnet"
}
```

---

## 9. Flecken und Fallen

Abschnitte 12.1 bis 12.6 des Bestands vom Nachmittag **gelten unveraendert**.
Vier sind dazugekommen.

### 9.1 `ZERLEGUNG_LAUF` ist fest 1 -- der Nachschlag ueberschreibt

`kette.py:88`. Die Stufenausgabe wird daraus gebildet (Z.110), und
`zerlegung_auftragstext` sagt dem Modell `<N> ist {lauf}`. Es gibt keine
Fahne, die den Lauf hochzaehlt.

Ein Nachschlag mit `--neu-ab zerlegung` schreibt darum wieder
`kandidaten-lauf1.json` und **ueberschreibt den ersten Lauf**. Er erzeugt
keine zweite Datei, und `fuehre_zusammen` haelt folglich auch nicht mit
Code 6 an.

`urteilslauf` warnt seit `8e6ab9a` in der Nachschlagzeile davor. **Nicht
behoben** -- siehe Uebergabe vom Abend, Abschnitt 6 (1).

### 9.2 Uebersprungene Stufen behalten `dauer_s` des vorigen Laufs

Der Ueberspring-Zweig in `main` (`kette.py:690-698`) setzt `status`, `ausgabe`
und `meldung`; `dauer_s`, `begonnen_am` und `beendet_am` bleiben stehen. Eine
Stufe kann darum gleichzeitig `"meldung": "uebersprungen, Ausgabe lag bereits
vor"` und ein `dauer_s` aus einem frueheren Anlauf tragen.

Genau so steht es in `kette.json` des Betriebslaufs vom Abend. **Wer die
Zeiten eines bestimmten Laufs braucht, muss `meldung` mitlesen.**

### 9.3 `kriterien_fassung` in zwei Schreibweisen

`"Fassung 0.8 (24. August 2026)"` gegen `"0.8"`, Abschnitt 8. Jede Auswertung,
die nach diesem Feld gruppiert, zerfaellt. Die Ursache ist **nicht belegt**;
`ZERLEGUNG-AUFTRAGSTEXT.md` verlangt eine Fassungspruefung, gibt aber offenbar
keine Schreibweise vor.

### 9.4 Blockierendes `wait()` stellt unter Windows kein Strg+C zu

Gemessen 6,06 s gegen 1,02 s
(`urteilslauf-strg-c\BERICHT-2026-08-25.md`, Z.171-172). Deshalb warten
`urteilslauf.warte_auf_kind` (Z.161) und `kette.fuehre_prozess` (Z.413) beide
in Takten von 0,25 s. **Wer in dieser Linie auf einen Kindprozess wartet,
uebernimmt dieses Muster.**

### 9.5 Die Schlusszeile von `kette` meldet Vollendung auch bei `--bis N`

`kette.py:745-746` gibt `Kette fertig: ...` und `Weiter mit: ... urteilslauf`
unbedingt nach der Stufenschleife aus, auch wenn `--bis 3` den Lauf planmaessig
frueher beendet hat. Der Zustand in `kette.json` ist korrekt; nur der Text
luegt. **Nicht behoben.**

---

## 10. Gates und Tests

Abschnitt 10 des Bestands vom Nachmittag gilt der Form nach weiter; die Zahlen
sind gewachsen. Letzter belegter Gate-Lauf
(`modellfahne\BERICHT-2026-08-25.md`, Abschnitt „Gates"):

| Gate | Ergebnis |
|---|---|
| `uv run python -m pytest` | **2671 passed, 1 skipped, 0 Fehler** in 146,7 s |
| `uv run ruff check .` | **All checks passed!** |
| `uv run mypy src` | **genau 20 Fehler in 3 Dateien** -- `repeat\cut.py` (3), `repeat\cutcli.py` (7), `repeat\cli.py` (10) |

Wachstum der Suite seit dem Nachmittag: 2605 (`2855d5f`) → 2652 (vor dem
Riegel) → 2657 (`7c4195d`, +6 Riegel-Tests bei gleichzeitiger Reparatur von
22 Tests) → 2671 (`8e6ab9a`, +14 Tests fuer `--modell` und die
Nachschlagzeile).

**Die drei vorbestehenden `PytestUnhandledThreadExceptionWarning` sind seit
`7c4195d` verschwunden** -- sie kamen aus den Prozesswache-Tests. Der Lauf
meldet jetzt keine Warnungen mehr. (Die Auftraege fuehren sie weiterhin unter
„bekannt und harmlos"; das ist ueberholt.)

Neue Testdateien: `tests\test_shorts_kette.py` (25 Tests),
`tests\test_shorts_riegel.py` (6 Tests).

---

## 11. Was nicht gebaut ist

Abschnitt 11 des Bestands vom Nachmittag **gilt unveraendert**, mit drei
Streichungen und einer Ergaenzung.

**Gestrichen, weil gebaut:** das Startskript der Urteilsseite (in
`urteilslauf` aufgegangen), Stufe 0 kopflos (`954c8a0`), der Kettenlaeufer
(`7c4195d`).

**Weiterhin nicht gebaut:** die Zusammenfuehrung zweier Zerlegungslaeufe samt
Auftragstext; `--lauf N` in `kette`; der Aufraeumer fuer die Zwischenstufen im
Bauordner; die geplante Aufgabe (Wecker); eine Oberflaeche fuer die
Shorts-Linie; das Veroeffentlichen.

**Neu als nicht gebaut:** eine Normalisierung von `kriterien_fassung`
(Abschnitt 9.3).

---

## Pruefsteine dieses Auftrags

| Groesse | Wert |
|---|---|
| `.py`-Dateien unter `src\matrix_auto_cutter\shorts\` | **28** |
| Eintraege in `labels\repeat\trefferquote.json` | **2** (Abschnitt 8) |
| Tests mit `@pytest.mark.echter_unterprozess` | **7** (Abschnitt 5.4) |
| Zeilen `kette.py` / `urteilslauf.py` / `tests\conftest.py` | **751 / 650 / 283** |
