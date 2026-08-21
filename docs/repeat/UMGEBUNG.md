# Umgebung — whisper.cpp fuer repeat/Diagnose

## Werkzeuge

- **whisper.cpp-Binary**: `P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe`
  485.888 Bytes, Stand 28.04.2026. Gesamtgroesse des Baums `P:\AI\whisper.cpp`: 176 MB.
- **Modell (Vorgabe im Code)**: `P:\AI\whisper-data\models\ggml-large-v3-turbo.bin`
  1.624.555.275 Bytes (1,51 GB), Stand 12.08.2026 13:57. Voreinstellung
  `DEFAULT_WHISPER_MODEL` in `src\matrix_auto_cutter\shorts\transcript.py:69`.
  `ggml-small.bin` liegt weiterhin im Modellordner (487.601.967 Bytes,
  Stand 28.04.2026) und wird nicht mehr als Vorgabe verwendet. Gesamtgroesse
  von `P:\AI\whisper-data\models\`: 3.645.920.301 Bytes (3,40 GB;
  `Get-ChildItem ... | Measure-Object -Property Length -Sum`, 21.8.2026),
  darin `ggml-small.bin`, `ggml-medium.bin` und `ggml-large-v3-turbo.bin`.

Modell und Binary liegen bewusst **ausserhalb** des Repositorys und werden
nicht versioniert. `whisper.cpp/` steht bereits in der `.gitignore`
(Abschnitt "vendored SDKs / toolchains").

## Beispielaufruf (heute: ueber das Modul)

Die Transkription laeuft heute nicht mehr durch einen Direktaufruf des
Binaries, sondern durch `src\matrix_auto_cutter\shorts\transcript.py`, das
Modell, Binary und Vokabeldatei aus dem Repository zieht (siehe unten) und
die vorhandene `asr.py`-Kette aufruft:

```
uv run python -m matrix_auto_cutter.shorts.transcript ^
  <pfad-zur-shorts-job.json>
```

Ohne `--whisper-binary`/`--whisper-model`/`--prompt-file` verwendet der
Aufruf die Voreinstellungen aus `transcript.py` (Binary, Modell,
`labels\repeat\whisper-vokabular.txt`). Beleg:
`artefakte\repeat\shorts-vokabular\BERICHT-2026-08-19.md`, Abschnitt
"Pruefstein".

### Nachtrag: Direktaufruf des Binaries (nur fuer Diagnosefaelle)

```
P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe ^
  -m P:\AI\whisper-data\models\ggml-large-v3-turbo.bin ^
  -f <pfad-zur-wav-datei> ^
  -l de ^
  -t 4 ^
  -ojf ^
  -ml 60
```

## Durchsatz

Die 4,06-fache Echtzeit war an `ggml-small` gemessen und gilt fuer
`ggml-large-v3-turbo` nicht. Fuer `turbo` liegen zwei Messungen an
unterschiedlichem Material vor, beide nicht mit der alten Zahl vergleichbar
(andere Threadzahl/Laenge):

- `artefakte\repeat\shorts-modellvergleich\MODELLVERGLEICH-2026-08-14.md`,
  Abschnitt 2.2: 1037,19 s Audio, `-t 8`, Laufzeit 13 min 56 s (836 s).
- `artefakte\repeat\shorts-stufe-4\BERICHT-2026-08-17.md`, Abschnitt "Teil A":
  74,6 s Audio, `-t 8`, **ohne** `-ml`, Laufzeit 49,710 s (0,666-fache
  Echtzeit laut Bericht, d. h. schneller als Echtzeit).

Ein eigener, sauber vergleichbarer Durchsatzlauf fuer `turbo` unter den
Bedingungen der alten Messung (4 Threads, `-ml 60`) ist nicht belegt.

## `-ml`: 60 im repeat-Diagnosepfad, 120 im Shorts-Pfad

`-ml` (max_len) ist ohne Wert Pflicht -- ohne die Option liefert whisper
Segmente von zehn Sekunden und mehr.

Zwei Vorgaben stehen nebeneinander im Code:

- **Repeat-Diagnose** (`src\matrix_auto_cutter\repeat\asr.py:18`,
  `DEFAULT_MAX_SEGMENT_LEN = 60`): Segmentgrenzen tragen die
  Aeusserungsbildung nachgelagert im repeat-Paket -- ein zu grosses Segment
  zerstoert diese Grundlage. Dieser Pfad ist unveraendert.
- **Shorts-Stufe-2-Transkription** (`src\matrix_auto_cutter\shorts\transcript.py:62`,
  `DEFAULT_MAX_SEGMENT_LEN = 120`): fuer die Kandidatensuche soll ein
  zusammenhaengender Gedanke nicht an einer festen Segmentgrenze zerreissen.
  Der Code-Kommentar an derselben Stelle nennt das selbst "ein erster, nicht
  gemessener Kompromiss" und verweist auf "Auftrag 15, Abschnitt 2.1"
  (`artefakte\repeat\shorts-stufe-2-vorbereitung\VORBEREITUNG-2026-08-11.md`).
  Diese Datei traegt keine Unterabschnitte 2.1/2.2 -- ihr Abschnitt "## 2 —
  Urteilsseite aus dem Repeat-Block" behandelt die Urteilsseite, nicht die
  Segmentlaenge. Eine gemessene Begruendung fuer den Wechsel von 60 auf 120
  wurde in `artefakte\repeat\` nicht gefunden.

**Ergebnis:** Wert 120 gilt im Shorts-Pfad laut Code
(`transcript.py:62`), Wert 60 gilt weiterhin im repeat-Diagnosepfad laut
Code (`asr.py:18`). Keiner der beiden Werte ist falsch -- sie gelten fuer
verschiedene Pfade. Die Begruendung fuer den Shorts-Wert 120 ist im Code
selbst als ungemessen vermerkt und in den durchsuchten Berichten nicht
belegt.

## Vokabeldatei (`--prompt`)

- **Ort**: `labels\repeat\whisper-vokabular.txt`.
- **Fundstelle**: `DEFAULT_VOCAB_PATH` in
  `src\matrix_auto_cutter\shorts\transcript.py:70`; gezogen ueber
  `resolve_prompt()` (`transcript.py:96-106`), sofern `--prompt-file` nicht
  gesetzt ist und die Datei existiert. Der Inhalt wird strikt als UTF-8
  gelesen (`load_prompt_text()`, `transcript.py:87-93`) und unveraendert als
  `--prompt` an whisper-cli durchgereicht.
- **Wofuer der Wert steht**: Fachbegriffe, die whisper sonst falsch erkennt
  (Beleg: "wrong-footed" vs. "gerund wurde",
  `artefakte\repeat\shorts-vokabular\BERICHT-2026-08-19.md`).
- Die Datei muss UTF-8 OHNE BOM sein. Schreiben per PowerShell mit
  `UTF8Encoding($false)`, pruefen mit `Get-Content -Encoding UTF8` -- ohne
  den Schalter zeigt PowerShell 5.1 Buchstabensalat, obwohl die Datei
  stimmt.
- Die Vorgabe wirkt nur im ERSTEN Verarbeitungsfenster von whisper.cpp.
  Fachbegriffe im hinteren Teil einer Aufnahme sind unzuverlaessig
  (gemessen 18.8.: "wrong-footed" im Ausschnitt korrekt, im Volllauf
  "gerong-footed").

## `-ojf` und Absturzsicherheit

`-ojf` schreibt die Rohausgabe als `<wav>.json` neben die WAV-Datei, bevor
die Konvertierung ins repeat-Transkript laeuft. Diese Rohausgabe ueberlebt
jeden nachgelagerten Absturz. Nach einem Fehlschlag in der Konvertierung
oder Diagnose **nie neu transkribieren** -- die vorhandene `<wav>.json`
steht noch und kann direkt nachkonvertiert werden.

## Vor jedem Stapellauf pruefen

Audiobitrate und `volumedetect` vor jedem Stapellauf pruefen:

- 2.274 bit/s bedeutet eine stumme Tonspur.
- 195.000 bit/s ist brauchbar.

## Tests

Immer `uv run python -m pytest`, niemals `uv run pytest`.

Und nur ueber PowerShell, nicht ueber Git-Bash. Unter Git-Bash scheitert
derselbe Aufruf mit „No module named pytest", obwohl das Paket im
site-packages liegt und `PYTHONPATH` identisch gesetzt ist. Beide Shells
reichen den `;`-getrennten Pfad unterschiedlich an den Windows-Python-Prozess
weiter. Gefunden am 10.8.2026 beim Bau von Shorts-Stufe 0.

## Product Runner nach jeder Codeaenderung neu starten

Der Product Runner laeuft als Hintergrundprozess und laedt den Code beim Start.
Nach jeder Codeaenderung muss er **gestoppt und neu gestartet** werden, sonst
laeuft der alte Stand weiter. `START-ALLES` meldet in diesem Fall nur „Product
Runner laeuft bereits" und startet nichts neu.

Am 8.8.2026 hat das eine Fehlersuche ueber Stunden verursacht: Der Runner
erzeugte ein Proposal mit Altcode, das frisch gestartete Review-Fenster las es
mit neuem Code und wies es als ungueltig ab. Der Fehler sah wie ein
Schemaproblem aus und war ein Prozessproblem.

**`START-MATRIX-AUTO-CUTTER.cmd` startet nichts neu, solange ein Runner lebt.**
Es meldet dann nur „laeuft bereits" und tut nichts. Im `runner.log` stehen zwei
verschiedene Meldungen, und nur die zweite zaehlt:

- `Product Runner wird im Hintergrund gestartet.` kommt aus dem Starter
  (`product_startup.py:102`) und wird geschrieben, **bevor** irgendetwas
  geprueft ist. Sie belegt **keinen** Neustart. Folgt darauf `Matrix Auto Cutter
  Product Runner laeuft bereits.` (`product_runner.py:2027`), hat der neue
  Prozess den laufenden vorgefunden und sich sofort beendet -- der alte Code
  laeuft weiter.
- `Runner startet.` mit Statuscode `runner_starting` (`product_runner.py:938`)
  ist der einzige Beleg fuer einen tatsaechlichen Start.

Am 9.8.2026 lief der Runner dadurch von **12:07:20 bis 15:02:39** auf altem
Code, quer ueber den Commit `a87b2a1` (12:41:54) hinweg. Um 14:11:54 und 14:27:56
stand jeweils die Starterzeile im Log, eine Sekunde spaeter `laeuft bereits`.
Nachweisbar wurde es daran, dass der Runner um 14:18 eine Warnung
protokollierte, deren Zeichenkette der Commit entfernt hatte.

### Wo das Log liegt

```
%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\product-runner\logs\runner.log
```

**Nicht** direkt im Zustandsverzeichnis -- der Unterordner `logs\` gehoert dazu.

Nuetzlicher Filter zum Nachsehen:

```powershell
$log = "$env:LOCALAPPDATA\DimensionWithin\MatrixAutoCutter\product-runner\logs\runner.log"
Get-Content $log -Tail 400 | Select-String -Pattern "Runner startet|laeuft bereits|Lautheit: I|render_verifying|render_succeeded|render_failed"
```

## `E_RENDER_VERIFY` benennt nicht, welche Bedingung gefallen ist

Die Renderverifikation prueft drei Dinge -- Medienprofil, Dauer gegen die
erwartete Laenge (Toleranz 50 ms) und einen vollstaendigen Decode --, meldet
aber nur einen gemeinsamen Fehlercode und einen Sammeltext. Wie
`sidecar.clock_gate`: **bei einem Fehlschlag immer alle drei nachrechnen**,
nicht raten.

Praktisch: `ffprobe` auf die Partialdatei (Breite, Hoehe, `avg_frame_rate`,
`sample_rate`, `format=duration` gegen `expected_output_duration_ms` aus
`render-plan.json`) und dann der Decode von Hand.

**Faustregel aus dem 9.8.2026, abgelesen an der Dauer der Verifikationsphase im
`runner.log`:** unter 1 Sekunde heisst Medienprofil oder Dauer -- der Decode
steht hinter der Profilpruefung und wird dann uebersprungen. Mehrere Sekunden
heissen, der Decode ist wirklich gelaufen. Gemessene Faelle: 1 s und weniger bei
den beiden Fehlschlaegen um 14:18 und 14:20 (Dauerbedingung), 6 s beim
bestandenen Lauf um 12:12, 3 s beim bestandenen Lauf um 15:11.

## Gescheiterte Renderversuche lassen ihre Partialdatei liegen

Das ist Absicht und soll so bleiben. Der Renderer loescht nur, was er als sein
Eigentum beweisen kann -- die fertige Ausgabe nach dem Verlinken und die eigene
NVENC-Testdatei. Nach einem Fehlschlag in Verifikation oder Veroeffentlichung
bleibt die `*.partial.mp4` in `Rendered\` stehen.

Das ist im Zweifel das fertige Video: Am 9.8.2026 sind zwei Partialdateien zu je
24,4 MB liegen geblieben, deren Ton fehlerfrei dekodierte und mit -14,20 LUFS
genau im Ziel lag -- sie waren nur 83 ms zu lang. Vor dem Aufraeumen also immer
erst hineinsehen. Die Dateien blockieren nichts: Jeder Versuch traegt seine
eigene `attempt_id` im Namen.

## Animierte OBS-Quellen brauchen zwei Haekchen

Browserquellen mit Animation -- „TruthPill Rotator", „EndCart" und
vergleichbare -- brauchen in ihren Eigenschaften beides:

- **Deaktivieren, wenn Quelle nicht sichtbar ist**
- **Browser bei Szenenaktivierung aktualisieren**

Ohne diese Haekchen laeuft die Animation im Hintergrund weiter, und der
Szenenwechsel trifft sie an zufaelliger Stelle. Das sah zweimal wie ein Fehler
im Cutter aus und war keiner.

## Cursor-Waechter

Skript: `scripts\START-CURSOR-WAECHTER.ps1`, protokolliert Mauszeiger-Position
waehrend einer OBS-Aufnahme nach `F:\ShortsQuellen\Cursor\` (Vorgabe
`-TargetDir`). Laeuft als Teil von `START-ALLES.ps1` (ruft das Skript
gegen Ende auf) mit. Er muss **vor** der Aufnahme laufen -- er verbindet
sich per obs-websocket und schreibt erst ab `RecordStateChanged`.
