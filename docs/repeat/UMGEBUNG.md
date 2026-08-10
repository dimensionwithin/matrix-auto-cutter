# Umgebung — whisper.cpp fuer repeat/Diagnose

## Werkzeuge

- **whisper.cpp-Binary**: `P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe`
  485.888 Bytes, Stand 28.04.2026. Gesamtgroesse des Baums `P:\AI\whisper.cpp`: 176 MB.
- **Modell**: `P:\AI\whisper-data\models\ggml-small.bin`
  487.601.967 Bytes (465 MB), Stand 28.04.2026.

Beide liegen bewusst **ausserhalb** des Repositorys und werden nicht versioniert.
`whisper.cpp/` steht bereits in der `.gitignore` (Abschnitt "vendored SDKs / toolchains").

## Beispielaufruf

```
P:\AI\whisper.cpp\build\bin\Release\whisper-cli.exe ^
  -m P:\AI\whisper-data\models\ggml-small.bin ^
  -f <pfad-zur-wav-datei> ^
  -l de ^
  -t 4 ^
  -ojf ^
  -ml 60
```

## Durchsatz

Gemessen: 4,06-fache Echtzeit bei 4 Threads. Eine Stunde Aufnahme entspricht
rund 15 Minuten Transkriptionszeit.

## `-ml 60` ist Pflicht

Ohne `-ml` (max_len) liefert whisper Segmente von zehn Sekunden und mehr.
Segmentgrenzen tragen die Aeusserungsbildung nachgelagert im repeat-Paket --
ein zu grosses Segment zerstoert diese Grundlage.

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
