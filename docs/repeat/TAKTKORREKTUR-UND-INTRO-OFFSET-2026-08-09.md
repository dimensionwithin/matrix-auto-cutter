# Taktkorrektur und neuer Intro-Offset — Baubericht

Stand: 9. August 2026, abends
Grundlage: `SCHRITT-1-TAKTKORREKTUR-VORBEREITUNG.md` und
`INTRO-CUT-BEFUND-2026-08-09.md`.

**Am echten Lauf abgenommen.**

| Lauf | Lag | Einstieg |
|---|---:|---|
| 18-51-29 | 17 | Anfang der Chart-Animation — richtig |
| 18-54-14 | 16 | gleiches Bild, Unterschied rund 1 Frame |

Beide entsprechen dem abgenommenen Zielpunkt. Vorher lagen genau diese Läufe
mit kleinem Lag bei rund 70 % der Linie — die Korrektur wirkt an dem Zustand, an
dem sie vorher falsch war.

> **Offen: die Gegenprobe bei großem Lag.** Beide Abnahmeläufe hatten Lag 16
> bzw. 17. Für Lag 63 ist die 47-Frame-Korrektur bisher nur **rechnerisch**
> belegt — `16-50-21` wird framegleich reproduziert (Abschnitt 1) —, aber nicht
> am Bild. Beim nächsten Lauf mit `monotonic_ns` um 1 049 999 958 nachholen und
> das Ergebnis hier eintragen.

---

## 1. Das Ergebnis vorweg

Die drei Läufe vom 9.8. abends, gerechnet mit dem gebauten Stand:

| Lauf | Marke | Lag | **neu: Schnitt** | alt: Marke+148 | ab sichtbarem Szenenanfang |
|---|---:|---:|---:|---:|---:|
| 16-50-21 | 517 | 63 | **665** | 665 | **85** |
| 17-45-21 | 252 | 16 | **353** | 400 | **85** |
| 17-52-38 | 565 | 16 | **666** | 713 | **85** |

Der Lauf, den du als richtig abgenommen hast, wird **framegleich reproduziert**:
665 vorher, 665 nachher. Die beiden zu späten Läufe wandern um genau 47 Frames
nach vorn. Und alle drei landen jetzt auf demselben Abstand von 85 Frames hinter
dem sichtbaren Szenenanfang — die Invariante, die vorher nicht herstellbar war.

Gerechnet gegen die echten Journale in `F:\MatrixMarketAutoEdit\`, nicht gegen
Fixtures.

---

## 2. Erster Schritt: vollständige Verbraucherliste

Wie festgelegt, `grep` über alle Verbraucher von `mapped_source_frame`, nicht
die Liste aus 5.3 übernommen. 31 Fundstellen, in drei Gruppen:

**Semantische Verbraucher — bekommen die Korrektur (3 Module):**

| Stelle | Rolle |
|---|---|
| `intro.py:186` | Sortierschlüssel des ersten Treffers |
| `intro.py:232` | Marke des Intro-Schnitts |
| `outro.py:260`, `outro.py:268` | Bereichsprüfung und Beginn des Schutzblocks |
| `protection.py:68`, `72`, `98`, `105` | Anfang und Ende jedes Schutzfensters |

**Produzent und Validator — dürfen sie *nicht* bekommen:**

| Stelle | Warum |
|---|---|
| `phase2/finalizer/sidecar_builder.py:444` | schreibt das Feld; die Abbildung selbst bleibt unangetastet |
| `sidecar.py:418` | rechnet die Abbildung beim Einlesen nach → jedes vorhandene Sidecar würde ungültig |
| `sidecar.py:452`, `454` | verlangen Frame 0 für `recording_started`, `video_frame_count` für `recording_stopped` |
| `sidecar.py:310`, `457`, `460-465`, `506-507` | Struktur- und Bereichsprüfungen |
| `models.py:679-688` | Pausenintervall-Modell |

Die Liste aus 5.3 war vollständig. Das war vorher nicht belegt, jetzt ist es.

---

## 3. Wo die Korrektur sitzt

Neues Modul `src\matrix_auto_cutter\event_lag.py`:

```python
FRONTEND_SAMPLED_EVENT_TYPES = frozenset({"scene_changed", "manual_protection"})

def pipeline_lag_frames(sidecar) -> int
def is_frontend_sampled(event) -> bool
def corrected_source_frame(event, lag_frames, total_frames) -> int
def corrected_end_source_frame(event, lag_frames, total_frames) -> int | None
```

`pipeline_lag_frames` rechnet
`recording_started.clock_sample.monotonic_ns × fps_num / (fps_den × 1e9)` mit
`Fraction`, halbe Frames nach oben wie `_round_half_up` in `calibration.py`.
Fehlt der Anker oder gibt es mehr als einen: 0, kein Raten.

Nicht in `affine_counter_frame` und nicht in die Sidecar-Abbildung — begründet
in 5.1 der Vorbereitung und im Modulkopf festgehalten.

### 3.1 Eine Präzisierung an der Festlegung zu `protection.py`

Die Vorgabe lautet: „Schutzfenster wandern mit. Eine Zone um den rohen Frame
schützt sonst die falsche Stelle." Das stimmt — **für Marken**. Für die an der
Ausgabe verankerten Ereignisse wäre es aktiv schädlich.

`recording_started` ist per Konstruktion am ersten Videoframe verankert
(`output_frame_count = 1` → Frame 0) und trägt `buffer_after_ms: 1000`; es
schützt die erste Sekunde der Aufnahme. Um 62 Frames verschoben **gäbe dieser
Block den Anfang der Aufnahme frei**. Dasselbe gilt spiegelbildlich für
`recording_stopped` und für Pause/Resume, die alle aus denselben
Ausgangssignalen stammen und deshalb exakt sind.

Verschoben wird deshalb nur, was aus einem Frontend-Callback stammt:
`scene_changed` und `manual_protection`. Festgehalten in
`test_the_protection_block_at_the_recording_start_stays_on_frame_zero` und
`test_the_output_anchored_events_never_move`.

**Praktische Folge, die man kennen muss:** in `materialize_protection` werden
`scene_changed`-Ereignisse ohnehin übersprungen (`protection.py:221`, sie
bekommen Schutz erst über die Outro-Bindung), und `manual_protection` sowie die
Paarfamilien `intro_*`/`outro_*`/`stinger_*` werden vom Producer nie abgesetzt.
**Auf heutigen Daten ändert `protection.py` damit nichts.** Der Mechanismus ist
richtig verdrahtet und getestet, wirkt aber erst, wenn solche Ereignisse
kommen. Der Schutzblock, der heute tatsächlich wandert, ist der 900-Frame-Block
des Outros — und der läuft über `outro.py`.

---

## 4. Schritt 2: der neue Offset

`INTRO_CUT_OFFSET_FRAMES = 85`, und die Bezugsgröße hat sich geändert: nicht
mehr Marke plus Konstante, sondern **Marke plus Lag plus Konstante**.

Der Kommentarblock ab `intro.py:45` ist ersetzt. Die alte Herleitung
„35 Rest der Vorszene + 113 Frames Stinger" beschrieb einen Übergang, den es an
dieser Stelle nicht gibt: die Sammlung schaltet mit „Schnitt" (100 ms), ein
Stingerwisch kommt beim Intro-Wechsel nicht vor. Der neue Text nennt, was
wirklich übersprungen wird — den Vorlauf von
`intro-sting-sovereign-1440p.webm` — trägt die drei Messläufe mit ihrem Lag ein
und hält fest, dass der Zielpunkt **der Anfang der Chart-Animation bei rund 5 %
der Linie** ist, ausdrücklich nicht die stehende Karte.

Diese Zielpunkt-Korrektur ist wichtig: der Analysebericht hatte „Karte steht"
als Referenz verwendet. Das war zu spät. Die Karte steht bei Stingframe 187, der
Schnitt liegt jetzt bei 85.

`test_intro.py:241` ist mitgezogen (`assert INTRO_CUT_OFFSET_FRAMES == 85`),
zusammen mit allen abgeleiteten Zahlen.

---

## 5. Proposal 1.2

- `PROPOSAL_SCHEMA_VERSION = "1.2"`
- `_DIGEST_DOMAIN_V12 = b"matrix-auto-cutter/cut-proposal/1.2\0"`, ausgewählt
  über `_DIGEST_DOMAINS` statt über eine Zweifach-Verzweigung
- `pipeline_lag_frames` als eigenes Feld in `IntroResolutionEvidence` und
  `OutroResolutionEvidence`

Die Versionsregeln:

| Version | Lagfeld |
|---|---|
| 1.0 | verboten |
| 1.1 | verboten — sonst wären ältere Bytes nicht mehr kanonisch |
| 1.2 | in **jeder** vorhandenen Resolution Pflicht |

Der Lag steht auch in den nicht aufgelösten Rückgaben. Er hängt allein am
Journal und ist auch dann bestimmt, wenn die Outro-Bindung fehlt — das war der
einzige echte Fehler im ersten Bauversuch, gefunden durch
`test_valid_source_and_sidecar_publish_concrete_deterministic_proposal`.

**Nicht ergänzt:** `IntroCandidateEvidence` und `OutroCandidateEvidence`. Die
Festlegung nennt die beiden Resolution-Modelle; die Candidate-Modelle würden bei
einer Pflichtangabe alle vorhandenen 1.1-Proposals unlesbar machen. Der Betrag
steht im selben Proposal, damit bleibt der Schnittpunkt nachrechenbar.

---

## 6. Frameverlust

Wie festgelegt offen gelassen und nur dokumentiert:
`test_frame_loss_before_the_marker_is_not_corrected`. Der Docstring nennt den
Beleg (`2026-08-09 08-14-05`, 45 Frames bei 4,0 s, Intro-Wechsel davor +15, die
späteren Wechsel +61 und +59) und sagt, was zu tun ist, wenn der Term später
gebaut wird.

---

## 7. Tests

**Neu: `tests\test_event_lag.py`, 21 Tests.**

| Gruppe | Inhalt |
|---|---|
| Ableitung | 0 ns → 0 Frames (sichert ab, dass die Altsuite nicht zufällig grün ist); die vier real gemessenen Anlaufzeiten mit ihrem Lauf als Herkunft; Halbframe-Rundung; fehlender oder doppelter Anker → 0 |
| Reichweite | nur Frontend-Marken wandern; Anker bleiben stehen; **der Schutzblock auf Frame 0 bleibt auf Frame 0**; ein Frontend-Marker verschiebt sein Fenster um genau den Lag |
| Intro | Schnitt hinter dem sichtbaren Anfang; gleiche Marke plus verschiedene Anlaufzeit ergibt 45 Frames Unterschied; Marke 0 entfernt weiterhin nichts; erst der Lag trägt die Marke über das Quellende |
| Offen | Frameverlust dokumentiert |

**Neu in `tests\test_outro.py`, 3 Tests:** Schutzblock und Tail folgen dem
sichtbaren Szenenanfang; die Randlage aus der Gegenprobe, in der der Lag die
letzten 60 Frames Luft aufbraucht und der Tail sauber entfällt statt falsch zu
schneiden; der Lag steht auch ohne Bindung in der Rückgabe.

**Neu in `tests\test_intro.py`, 3 Tests:** 1.2 trägt den Lag in den Bytes;
eigene Digest-Domäne (dieselben Inhaltsbytes hashen unter 1.1 und 1.2
verschieden); ältere Versionen dürfen ihn nicht tragen; 1.2 verweigert eine
Resolution ohne ihn.

### Angepasste Alttests

Alle in `test_intro.py`, alle als Folge der beiden beabsichtigten Änderungen.
Zahlen, die dem neuen Offset folgen: `removed_ms` 53 800 → 52 750,
`intro_start_frame` 1048 → 985 und 1648 → 1585, Timecode `00:00:53.800` →
`00:00:52.750`, `KeepSegment` 3148 → 3085, `FLOW_END` 1198 → 1135.

Drei Stellen brauchten mehr als eine neue Zahl:

- **`test_the_zone_boundary_is_half_open`** prüft die halboffene Grenze auf den
  Frame genau. Die Stilleeingaben mussten von 19,60/19,61 s auf 18,54/18,56 s
  wandern, damit sie wieder einen Frame vor und genau auf der neuen Grenze
  liegen. Die Absicht des Tests bleibt unangetastet.
- **`test_the_pause_after_a_musical_intro_is_protected`** hatte eine Stille bei
  12,0 s, die vor dem alten Schnitt (Frame 748) lag und hinter dem neuen
  (Frame 685) gelegen hätte — aus „vom Lead-in verdrängt" wäre „von der Zone
  geschützt" geworden und der Test hätte etwas anderes geprüft als gedacht. Die
  Stille ist auf 10,0 s verschoben, damit sie ihre Rolle behält.
- **`_without_intro_resolution`** baut die Bytes nach, die ein 1.1-Stand
  veröffentlicht hätte. Ein solcher Stand kannte weder `intro_resolution` noch
  die Taktkorrektur; beides muss jetzt aus den Bytes verschwinden, sonst
  entstünde ein Artefakt, das es nie gegeben hat.

Keine Assertion wurde abgeschwächt, keine gelöscht.

---

## 8. Prüfung

```
uv run python -m pytest        1845 passed, 1 skipped
uv run ruff check src tests    All checks passed
uv run mypy src                20 Fehler, alle vorbestehend
```

Von 1818 auf 1845 Tests. Die mypy-Fehler liegen in `repeat\cli.py`,
`repeat\cut.py`, `repeat\cutcli.py` — nicht angefasst, keine in den geänderten
Dateien.

`ruff format`: `protection.py`, `test_intro.py` und die beiden neuen Dateien
sind formatiert. `cut_proposal.py` und `test_outro.py` weichen auch auf HEAD
schon ab und wurden deshalb nicht durchformatiert — das wäre ein fremder Diff.

---

## 9. Umfang

```
 src/matrix_auto_cutter/cut_proposal.py    29 ++++--
 src/matrix_auto_cutter/intro.py           59 +++++-----
 src/matrix_auto_cutter/outro.py           36 ++++---
 src/matrix_auto_cutter/protection.py      36 ++++---
 tests/test_intro.py                      130 ++++++++++++++-----
 tests/test_outro.py                       81 +++++++++++-
 src/matrix_auto_cutter/event_lag.py      neu
 tests/test_event_lag.py                  neu
```

Nicht committet.

---

## 10. Was offen bleibt

- **Die Gegenprobe bei Lag 63 steht aus.** Abgenommen ist der Fall mit kleinem
  Lag (18-51-29, 18-54-14). Dass die 47-Frame-Korrektur auch am Bild stimmt,
  ist für den großen Lag noch nicht gesehen — nur gerechnet.
- **`protection.py` wirkt heute nicht** (Abschnitt 3.1). Sobald
  `manual_protection` oder die Paarfamilien abgesetzt werden, greift es.
- **Frameverlust bleibt unkorrigiert** (Abschnitt 6).
- **Der Regime-Bruch vom 9.8. früh ist weiter unerklärt.** Springt die Phase des
  Stings zurück, wandert `INTRO_CUT_OFFSET_FRAMES` mit — der Lag nicht, der ist
  jetzt gerechnet. Das ist der Gewinn: von zwei verschränkten Unbekannten ist
  eine gelöst und die andere isoliert.
- **Alte Proposals werden nicht migriert.** Vorhandene 1.1-Artefakte bleiben
  lesbar; ein neuer Lauf über dieselbe Aufnahme erzeugt eine neue
  Proposal-Generation mit anderer ID.
