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

## Product Runner nach jeder Codeaenderung neu starten

Der Product Runner laeuft als Hintergrundprozess und laedt den Code beim Start.
Nach jeder Codeaenderung muss er **gestoppt und neu gestartet** werden, sonst
laeuft der alte Stand weiter. `START-ALLES` meldet in diesem Fall nur „Product
Runner laeuft bereits" und startet nichts neu.

Am 8.8.2026 hat das eine Fehlersuche ueber Stunden verursacht: Der Runner
erzeugte ein Proposal mit Altcode, das frisch gestartete Review-Fenster las es
mit neuem Code und wies es als ungueltig ab. Der Fehler sah wie ein
Schemaproblem aus und war ein Prozessproblem.

## Animierte OBS-Quellen brauchen zwei Haekchen

Browserquellen mit Animation -- „TruthPill Rotator", „EndCart" und
vergleichbare -- brauchen in ihren Eigenschaften beides:

- **Deaktivieren, wenn Quelle nicht sichtbar ist**
- **Browser bei Szenenaktivierung aktualisieren**

Ohne diese Haekchen laeuft die Animation im Hintergrund weiter, und der
Szenenwechsel trifft sie an zufaelliger Stelle. Das sah zweimal wie ein Fehler
im Cutter aus und war keiner.
