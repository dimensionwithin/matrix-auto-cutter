# PermissionError WinError 5 beim atomaren Schreiben — Behebung

Stand: 9. August 2026, abends
Anlass: `runner.log`, 9.8. 17:02:11, `render.py:775` → `render.py:715` →
`os.replace(temporary, path)` → `PermissionError: [WinError 5]` beim Tausch
`.render-status.json.<uuid>.tmp` → `render-status.json`.
Vier Vorkommen am 9.8.: 06:44:22 (`E_RENDER_EXCEPTION`), nachmittags,
16:29:45 (`status.json`, `E_RUNNER_STARTUP`), 17:02:11 (`render-status.json`).

**Am echten Lauf abgenommen.** Neun Dateien geändert, eine Testdatei neu.

---

## 1. Gegenprobe und Abnahme

Proposal `aad895d33cb41ba191f1574f62e2e31b`, unveränderter Code:

| Lauf | Review-Fenster | Ergebnis |
|---|---|---|
| 17:02:11 | offen | `PermissionError` WinError 5 |
| 17:30:16 | direkt nach „Final rendern" geschlossen | `render_succeeded` |

Damit war die lesende Seite als Ursache belegt.

Abnahme mit diesem Stand:

| Lauf | Review-Fenster | Ergebnis |
|---|---|---|
| 17:45:21 | **offen** | `render_succeeded`, Verifikation bestanden |

Genau der Fall, der um 17:02:11 gestorben ist, läuft jetzt durch.

---

## 2. Was der Fehler mechanisch ist

Vor dem Bau habe ich die Kombinationen an dieser Anlage gemessen — nicht
angenommen:

| Leser hält Zieldatei offen | Ersetzen | Ergebnis |
|---|---|---|
| normal (`open(..., "rb")`) | `os.replace` | **WinError 5** — der gemeldete Fehler |
| mit `FILE_SHARE_DELETE` | `os.replace` | **WinError 5** — unverändert |
| normal (`open(..., "rb")`) | POSIX-Rename | **WinError 32** |
| mit `FILE_SHARE_DELETE` | POSIX-Rename | **gelingt** |

`os.replace` benutzt unter Windows `MoveFileExW`. Das kann eine Zieldatei nicht
überschreiben, solange irgendein Griff darauf offen ist — auch dann nicht, wenn
dieser Griff das Löschen ausdrücklich erlaubt. Nur
`SetFileInformationByHandle` mit `FileRenameInfoEx` und
`FILE_RENAME_POSIX_SEMANTICS` kann es: die alte Datei wird verdrängt, der Leser
liest seinen Griff zu Ende, und der Name zeigt sofort auf die neue.

**Beide Hälften werden gebraucht.** Die naheliegende Lösung — nur die Leser mit
`FILE_SHARE_DELETE` öffnen — hätte am Fehler nichts geändert. Das steht hier so
deutlich, weil genau das der erste Versuch war und die Messung ihn widerlegt
hat.

---

## 3. Punkt 1 — Wiederholung in einer gemeinsamen Hilfsfunktion

Neu in `src\matrix_auto_cutter\atomic.py`:

```python
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_SECONDS = 0.05
_RETRYABLE_WINERRORS = frozenset({5, 32})   # ACCESS_DENIED, SHARING_VIOLATION

def replace_atomically(temporary, target, *, create_only=False,
                       attempts=REPLACE_ATTEMPTS,
                       retry_seconds=REPLACE_RETRY_SECONDS,
                       sleep=time.sleep) -> None
```

Ablauf je Versuch:

1. `os.rename` (bei `create_only`) beziehungsweise `os.replace`.
2. Fehler, der **kein** `winerror` 5 oder 32 ist: sofort durchreichen. Kein
   pauschales `except`. `FileExistsError` (183) erreicht den Aufrufer im
   `create_only`-Fall damit unverändert.
3. Sperrkonflikt und Windows: einmal mit POSIX-Semantik versuchen. Gelingt es,
   fertig — ohne Wartezeit.
4. Sonst 50 ms warten und wiederholen, bis zum fünften Versuch. Danach fliegt
   der ursprüngliche Fehler.

Die POSIX-Semantik ist bewusst **Eskalation, nicht Ablösung**. Ohne Konflikt
läuft der billige Normalweg: kein `ctypes`, kein zusätzlicher Dateigriff. Das
zählt, weil dieser Pfad bei jedem Fortschrittstakt eines Renders durchlaufen
wird. Fehlt die Unterstützung (ältere Windows, kein NTFS), meldet der Versuch
schlicht Misserfolg und die normale Wiederholung übernimmt.

### 3.1 Bleibt die create-only-Garantie in der Eskalation erhalten?

Ja, über zwei voneinander unabhängige Barrieren.

**Strukturell.** `_windows_posix_replace` setzt
`FILE_RENAME_REPLACE_IF_EXISTS` und würde ein vorhandenes Ziel verdrängen. Der
Aufruf steht deshalb hinter `not create_only` — für eine create-only-Schreibung
ist der Zweig nicht erreichbar. `ReplaceIfExists` wird also nicht auf `false`
gesetzt; der Pfad mit dem Flag läuft schlicht nie.

**Unabhängig davon durch Windows selbst.** Gemessen an dieser Anlage meldet
`os.rename` ein vorhandenes Ziel als `FileExistsError` mit `winerror` 183 —
**gleich, ob es jemand offen hält oder nicht**:

| Ziel | Ergebnis |
|---|---|
| vorhanden, frei | `FileExistsError` winerror 183 |
| vorhanden, gesperrt | `FileExistsError` winerror 183 |

183 steht nicht in `_RETRYABLE_WINERRORS`. Es wird also weder gewartet noch
eskaliert, der Fehler erreicht den Aufrufer unverändert, und das Ziel bleibt
Byte für Byte stehen. Festgehalten in
`test_create_only_never_overwrites_a_locked_existing_target` und
`test_create_only_never_reaches_the_posix_escalation`.

**Zum finalen MP4:** das hängt an dieser Stelle gar nicht. Es wird in
`render.py:2504` über `os.link(partial, target)` veröffentlicht, gefolgt von
`partial.unlink()` — ein harter Link, kein Rename. `os.link` ist von dieser
Änderung nicht berührt und behält seine eigene create-only-Garantie über
`FileExistsError`.

Der eingehängte `sleep` existiert für die Tests: er erlaubt, den Konflikt echt
herzustellen und trotzdem ohne Wanduhr zu prüfen.

---

## 4. Punkt 2 — alle Stellen, eine Funktion

`grep -rn "os\.replace\|os\.rename" src/` findet jetzt nur noch die
Hilfsfunktion selbst. Umgestellte Aufrufer:

| Datei:Zeile | Artefakt | Warum es hier auftritt |
|---|---|---|
| `render.py:717` | `render-status.json` u. a. | **der gemeldete Traceback**; Review-Fenster pollt alle 750 ms |
| `render.py:720` | Erstanlage (`create_only`) | dieselbe Funktion, `FileExistsError` bleibt erhalten |
| `product_runner.py:766` | `status.json`, Session-State, `stop.request` | **`E_RUNNER_STARTUP` 16:29:45** |
| `product_runner.py:762` | Session-Claim (`create_only`) | — |
| `product_runner.py:412/415` | **Logrotation `runner.log`** | derselbe Fehler, sobald das Protokollfenster offen ist — war noch nicht gemeldet |
| `approval.py:170/174` | `approval.json` | Runner und Review-Fenster lesen beide |
| `selection.py:229/232` | `selection.json` | dito |
| `cut_proposal.py:411` | `cut-proposal.json` | Erstanlage |
| `review.py:405` | `review.html` | **hält der Browser offen**, solange die Seite angezeigt wird |
| `review_app.py:155` | Fenstergeometrie | Vollständigkeit |
| `repeat/diagnostics.py:293` | `diagnostics.json` | Vollständigkeit |
| `atomic.py` | `protection-ranges.json` | Vollständigkeit |

Die Logrotation und `review.html` waren nicht im Auftrag genannt und haben
denselben Fehlermodus. Beide sind jetzt mit abgedeckt.

---

## 5. Punkt 3 — die lesende Seite

Neu in `atomic.py`: `open_shared(path)` und `read_bytes_shared(path)`. Unter
Windows über `CreateFileW` mit
`FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`, sonst ein gewöhnliches
`open`. Fehler kommen als `OSError` mit gesetztem `winerror`, die bestehenden
`except OSError`-Zweige der Aufrufer greifen unverändert.

Eingesetzt an den drei Stellen, die im Sekundentakt gelesen werden, während ein
anderer Prozess schreibt:

| Datei:Zeile | Leser |
|---|---|
| `render.py:733`, `render.py:755` | `load_render_status` — Review-Fenster, alle 750 ms |
| `product_runner.py:444` | `load_runner_status` — dito |
| `product_runner.py:508` | `tail_runner_log` — Protokollfenster, alle 1000 ms |

Die zweite Hälfte der Forderung — Lesefehler tolerieren und beim nächsten
Durchlauf erneut versuchen — war bereits erfüllt: alle drei fangen `OSError` und
liefern `None` beziehungsweise einen Hinweistext; der Poll versucht es 750 bis
1000 ms später erneut. Das bleibt so.

Nicht umgestellt: `load_window_geometry` in `review_app.py`. Die Datei hat genau
einen Schreiber, nämlich das Review-Fenster selbst, und wird nicht gepollt.

---

## 6. Punkt 5 — Status bricht keinen Render mehr ab

`write_render_status` gibt jetzt `bool` zurück, fängt `OSError`, warnt mit
`RuntimeWarning` und läuft weiter. Der nächste Fortschrittstakt schreibt
ohnehin neu. `_atomic_write` selbst wirft weiterhin — `render-result.json` und
`render-plan.json` sind Ergebnis und müssen laut scheitern. Nur der Status ist
Anzeige.

Dasselbe für die Runnerseite: neu `ProductRunner._write_status_file`, benutzt
von `_publish_status` und `heartbeat`. Damit fällt auch der
`E_RUNNER_STARTUP` von 16:29:45 weg — der war kein Startfehler, sondern ein
gescheitertes Statusschreiben, das im breiten `except Exception` des Startpfads
landete.

Ein Detail, das leicht durchrutscht: `_publish_status` entprellt über
`_last_status_by_subject` und trug den Schlüssel **vor** dem Schreiben ein.
Schluckt man den Fehler ohne weiteres, bliebe die Datei bis zum nächsten
*anderen* Status veraltet stehen. Der Eintrag wird jetzt bei Misserfolg wieder
entfernt.

---

## 7. Punkt 4 — Regressionstests

`tests\test_atomic_sharing.py`, dreizehn Tests. **Kein Test hängt an einer
Wanduhr**: der Sperrkonflikt ist echt, aber die Freigabe passiert im
eingehängten `sleep`, nicht in einem Timer. Die Windows-spezifischen sind mit
`skipif(os.name != "nt")` markiert.

| Test | prüft |
|---|---|
| `test_an_open_target_is_replaced_after_the_reader_releases_it` | **die geforderte Regression**: Ziel offen, erster Versuch scheitert echt, nach Wiederholung gelingt es; genau eine Wartezeit |
| `test_a_target_that_never_releases_passes_the_error_through` | genau vier Wartezeiten, dann `winerror in {5, 32}`, Ziel unverändert |
| `test_a_shared_reader_never_blocks_the_writer` | geteilt geöffneter Leser löst **gar keine** Wiederholung aus |
| `test_read_bytes_shared_matches_a_plain_read` | geteiltes Lesen liefert denselben Inhalt |
| `test_a_missing_file_still_raises_oserror` | Fehlerverhalten der Leseseite bleibt austauschbar |
| `test_only_the_two_sharing_errors_are_retried` | `FileExistsError` wird nicht wiederholt |
| `test_create_only_never_overwrites_a_locked_existing_target` | **create-only bei vorhandenem UND gesperrtem Ziel**: 183, keine Wartezeit, Ziel unverändert |
| `test_create_only_never_reaches_the_posix_escalation` | der Zweig mit `ReplaceIfExists` läuft bei `create_only` nie |
| `test_at_least_one_attempt_is_required` | `attempts=0` ist ein Programmierfehler |
| `test_the_render_status_is_written_while_a_shared_reader_polls` | Regression zu `render.py:715` mit gehärtetem Leser |
| `test_the_render_status_survives_a_reader_that_releases_late` | derselbe Traceback mit einem Leser, den die Härtung **nicht** erreicht (Virenscanner, Explorer-Vorschau) |
| `test_a_locked_status_file_only_warns_and_never_raises` | Punkt 5 für `render-status.json` |
| `test_a_locked_runner_status_only_warns_and_never_raises` | Punkt 5 für `status.json` |

---

## 8. Prüfung

```
uv run python -m pytest        1818 passed, 1 skipped
uv run ruff check src tests    All checks passed
uv run mypy src                20 Fehler, alle vorbestehend
```

Die mypy-Fehler liegen in `repeat\cli.py`, `repeat\cut.py`, `repeat\cutcli.py` —
keine davon geändert, keine in den geänderten Dateien.

`ruff format`: `render.py`, `selection.py` und `repeat\diagnostics.py` waren auf
HEAD formatiert und sind es wieder. `approval.py`, `cut_proposal.py`,
`product_runner.py`, `review.py`, `review_app.py` weichen auch auf HEAD schon
ab; die habe ich **nicht** durchformatiert, das wäre ein fremder Diff gewesen.

### Vier Alttests, die zwischendurch gebrochen sind

`test_write_diagnostics_replace_failure_removes_temporary_file`,
`test_atomic_os_errors_preserve_existing_target_and_remove_temp[replace]`,
`test_old_approval_does_not_apply_to_new_generation_and_decision_is_atomic`,
`test_atomic_replace_failure_removes_temporary_file`.

Alle vier monkeypatchen `os.replace`, um einen Fehler zu simulieren. Ein erster
Entwurf hatte den POSIX-Rename als *primären* Weg — damit lag der Seam nicht
mehr auf dem Pfad und die Simulation lief ins Leere. Das war der Anlass, die
Struktur auf „`os.replace` zuerst, POSIX nur bei Konflikt" umzustellen. Jetzt
laufen alle vier unverändert. **Kein committeter Test musste angepasst werden.**

### Eine Beobachtung, die ich nicht wegerkläre

`test_review_selection_bridge_rejects_untrusted_requests_and_persists_canonical_state`
ist in **einem von drei** vollen Läufen gefallen, isoliert 10 von 10 grün und in
den beiden anderen vollen Läufen grün. Der Test startet einen echten
HTTP-Bridge-Server und bindet einen Port; die Suite mischt die Reihenfolge über
`pytest-randomly`. Ich habe den Traceback nicht eingefangen und behaupte
deshalb **nicht**, dass er vorbestehend ist. Kommt er wieder, gehört die
Ausgabe gesichert.

---

## 9. Umfang

```
 src/matrix_auto_cutter/approval.py             5 +-
 src/matrix_auto_cutter/atomic.py             232 ++++++++++++++-
 src/matrix_auto_cutter/cut_proposal.py         3 +-
 src/matrix_auto_cutter/product_runner.py      46 +++++-
 src/matrix_auto_cutter/render.py              36 ++++-
 src/matrix_auto_cutter/repeat/diagnostics.py   3 +-
 src/matrix_auto_cutter/review.py               5 +-
 src/matrix_auto_cutter/review_app.py           3 +-
 src/matrix_auto_cutter/selection.py            5 +-
 9 files changed, 311 insertions(+), 27 deletions(-)
 tests/test_atomic_sharing.py                 neu
```

---

## 10. Was offen bleibt

- **Die Wiederholung ist die Rückfallebene, nicht die Lösung.** Für Leser, die
  wir kontrollieren, greift jetzt die geteilte Öffnung plus POSIX-Rename und es
  gibt gar keinen Konflikt mehr. Für fremde Leser — Virenscanner,
  Explorer-Vorschau, ein Editor auf `review.html` — bleiben 200 ms Budget. Reicht
  das einmal nicht, ist der Status kurz veraltet und der Render läuft weiter;
  bei einem Ergebnisartefakt scheitert er weiterhin laut. Das ist beabsichtigt.
- **`os.link` hat keine Wiederholung.** Die Veröffentlichung des finalen MP4
  in `render.py:2504` ist von diesem Auftrag nicht berührt — `os.link` ist
  weder `os.replace` noch `os.rename`. Denkbar bleibt, dass ein Virenscanner
  die frisch geschriebene Partial-Datei hält und der Link mit 32 scheitert.
  Beobachtet wurde das nicht; falls es auftritt, gehört dieselbe Behandlung
  dorthin.
- **`E_RENDER_EXCEPTION` von 06:44:22** habe ich nicht gegengelesen. Der
  Zeitpunkt passt zum abgebrochenen Render
  `2026-08-09 06-28-58.matrix-cut.render-attempt-…partial.mp4`; ob dort
  derselbe Traceback steht, ist ungeprüft.
