# Matrix Auto Cutter — Übergabe an neuen Chat

Stand: 5. August 2026
Vorgänger-Chat: Audit 01 und REPEAT-1A (Erkennung), abgeschlossen und gepusht.

---

## 1. Verifizierter Repositoryzustand

```
Repository  P:\DimensionWithin-MatrixMarketAutoEditor
Remote      https://github.com/dimensionwithin/matrix-auto-cutter.git
Branch      master
HEAD        113a9099d14044a7cd235dc82ad22433598d6e52
origin      113a909  — synchron, nichts ausstehend
Working Tree leer
```

Commit-Kette dieses Arbeitsblocks, alle gepusht:

```
113a909  fix: split utterances on sentence punctuation and drop prefix weight
c83d344  fix: widen repeat pairing window and strip correction markers
0127a11  feat: add repeat detection diagnostics package
824819e  feat: add verified outro tail cutting   ← Ausgangspunkt (OUTRO-1)
```

---

## 2. Was gebaut wurde: `src/matrix_auto_cutter/repeat/`

Ein **vollständig isoliertes** Diagnosepaket. Kein bestehendes Modul importiert es — nachgewiesen durch Volltextsuche. Der Produktpfad ist unberührt.

Acht Module: `transcript.py` (Vertrag `repeat_transcript/1.0`), `utterances.py`, `similarity.py`, `detect.py`, `diagnostics.py` (Vertrag `repeat_diagnostics/1.0`), `cli.py`, `errors.py`, `__init__.py`.
74 Tests unter `tests/repeat/`, **100 % Coverage** (352 Statements, 80 Branches).
Einzige Ergänzung außerhalb: `[project.optional-dependencies] repeat = ["rapidfuzz>=3,<4"]` plus Lockfile.

**Funktionsweise.** Aus einem Transkript mit Wortzeiten werden Äußerungen gebildet — getrennt an Satzinterpunktion (primär), an Wortpausen über 700 ms und an einem Notbremsen-Deckel von 20 s. Jede Äußerung wird gegen alle folgenden innerhalb von 2 s Lücke verglichen. Der Score ist `0,5·ratio + 0,25·ngram + 0,25·subsequence + Korrekturmarker-Bonus`, Schwellwert 0,55. Ergebnis ist eine **Diagnose**, niemals ein Schnitt.

Aufruf: `uv run python -m matrix_auto_cutter.repeat.cli --transcript X.json --out Y.json`

---

## 3. Proof of Value — an echtem Material erbracht

Drei Minuten aus einer realen Aufnahme (`F:\MatrixMarketAutoEdit\2026-08-04 01-11-36.mp4`, Fenster 2:00–5:00), transkribiert mit whisper.cpp.

```
24 Äußerungen, 22 geprüfte Paare, 1 Kandidat
Kandidat: Äußerung 0 → 1, Score 0,633
  „…die erstmalig herdenken, dass wir im Beerenmarkt sind seit Februar."
  „Ja, die Grunde erstmalig denken, dass wir im Beerenmarkt sind, seitdem…"
Kein Fehlalarm. Rang 3 abwärts liegt bei ≤ 0,37.
```

Der Fund ist korrekt und wurde ohne Vorwissen über die Fundstelle erzielt.

**Offen geblieben:** Rang 2, „Wir haben unseren Selling Climax." / „S elling Climax.", liegt bei 0,525 knapp unter dem Schwellwert. Sehr kurze Passagen fallen beim n-Gramm-Anteil ab. Nicht angefasst, weil eine einzelne Probe keine Grundlage für Parametertuning ist.

---

## 4. Technische Fakten für den Nachfolger

**whisper.cpp läuft als Subprozess.** Gefunden auf dem Rechner: `whisper-cli.exe` (CPU-Build) mit `ggml-small.bin` (487 MB) unter `P:\AI\whisper-data\models\`. RTX 3060 vorhanden, aber der Build ist ohne CUDA. Drei Minuten Audio in 60 s auf CPU. Deutsch, Wortzeitstempel über `-ojf`, kein VAD.

**Konsequenz:** `faster-whisper`, `ctranslate2`, `onnxruntime`, `torch` werden **nicht** gebraucht. Ein Subprozess-Adapter passt exakt zur Projektdoktrin, die auch ffmpeg und ffprobe nur über Argumentvektoren anspricht. Das ursprüngliche Audit hatte einen schweren Dependency-Eingriff erwartet — der entfällt.

**whisper.cpp-Eigenheiten**, im Probelauf gemessen:
- Wortzeitstempel sind lückenlos, Median-Pause 0 ms. Pausenbasierte Segmentierung funktioniert damit **nicht**. Nur die Interpunktion trägt.
- Gelegentlich mehrere Tokens auf derselben Millisekunde. Braucht eine durchlaufende monotone Uhr beim Konvertieren.
- BPE-Subwort-Tokens: ein neues Wort beginnt, wenn der Tokentext mit Leerzeichen anfängt.

**Vorbestehende rote Gates im Repository** — nicht durch diese Arbeit verursacht, nicht reparieren ohne eigenen Auftrag:
- `ruff format --check src tests` meldet 14 Altdateien
- projektweites `fail_under = 100` liegt real bei 91,78 %
- `uv run pytest` schlägt fehl (`ModuleNotFoundError: tests`), weil das Konsolenskript die Repowurzel nicht in `sys.path` legt. **Immer `uv run python -m pytest` verwenden.**

**Korrektur am Audit 01:** dort stand, das 100-%-Coverage-Gate sei bindend. Es ist konfiguriert, aber auf `master` nicht erfüllt. Das neue Paket erfüllt es; das Repository insgesamt nicht.

---

## 5. Was noch fehlt bis zum fertigen Feature

| Paket | Inhalt | Vertrauensgrenze |
|---|---|---|
| **REPEAT-1A2** | ASR-Adapter: Audio extrahieren, whisper.cpp als Subprozess, Konvertierung, CLI-Pfad `--source`. Ressourcenkontrolle. | keine — bleibt im isolierten Paket |
| **REPEAT-1B** | Human Gate: beide Passagen anzeigen, vorhören, entscheiden. Braucht Media-Serving im Review, das heute fehlt. | Review-/UI-Schicht |
| **REPEAT-1C** | Proposal 1.2 und Selection 1.1 mit exklusiven Paaren („erste **oder** zweite Passage, niemals beide"). Heute kennt `SelectedCandidate` nur `enabled: bool`. | Verträge, Approval, Render |

Erst nach C entsteht aus einer Diagnose ein Schnitt.

---

## 6. Der nächste Auftrag steht bereit

REPEAT-1A2 ist im Vorgängerchat vollständig ausformuliert worden. Der neue Chat sollte ihn **nicht neu erfinden**, sondern beim Nutzer erfragen oder aus dessen Chatverlauf übernehmen. Kern in einem Satz: neues Modul `asr.py`, `whisper_json.py`, `audio.py` plus CLI-Erweiterung `--source`, alles über einen injizierbaren Prozess-Seam nach dem Vorbild von `render.NativeProcessRunner`, Tests ausschließlich mit gefälschtem Prozess-Runner, keine neue Abhängigkeit.

---

## 7. Arbeitsweise — bitte beibehalten

**Rollenverteilung.** Der Chat orchestriert und auditiert, Claude Code implementiert. Der Chat schreibt keinen Produktionscode und committet nicht.

**Jeder Auftrag nennt:** erlaubten Änderungsbereich, verbotene Operationen, Qualitätsgates mit exakten Befehlen, Berichtsanforderungen. Kein Commit im selben Auftrag wie die Implementierung. Commit und Push sind eigene, minimale Aufträge mit Scope-Prüfung als erstem Schritt.

**Modellwahl.** Haiku für Mechanik (Commit, Push). Sonnet für Sammeln, Implementieren, Verifizieren. Opus erst für REPEAT-1C, wo die Vertrauenskette berührt wird. Nicht reflexartig Opus.

**Berichte prüfen, nicht glauben.** Zahlen gegeneinander abgleichen: Testanzahl im Paket gegen Gesamtsuite, Statements gegen Coverage. Beide Deltas müssen zusammenpassen.

**Lehre aus dem Vorgängerchat.** REPEAT-1A brauchte vier Runden, weil Parameter festgelegt wurden, bevor Daten vorlagen — und danach korrigiert werden mussten. Konsequenz: Entscheidungen vorziehen, größere Pakete schnüren, und bei Tuning-Zahlen bei „gut genug" aufhören. 0,633 reicht; daraus 0,71 zu machen ist kein Fortschritt.

**Zum Nutzer.** Er liest die Claude-Code-Berichte nicht — das ist die Aufgabe des Chats. Er braucht: einen fertigen Prompt zum Kopieren, die Modellempfehlung, und in zwei Sätzen was dabei herauskommt. Er will zügig zum fertigen Produkt und schätzt Direktheit über Ausführlichkeit.

---

## 8. Was in den neuen Chat gehört

Diese Datei. Dazu, falls die Erkennung weiter untersucht wird, der Regressionsfall `transcript.json` aus dem Probelauf (echtes Whisper-Transkript, 43 Segmente, 315 Wörter, enthält den bestätigten Kandidaten). Er liegt unter `C:\Users\schan\Desktop\repeat-probe\`.

Der Ordner `repeat-probe` auf dem Desktop ist ansonsten Wegwerf-Material und kann gelöscht werden — mit Ausnahme von `transcript.json` und `raw-transcript.json`.
