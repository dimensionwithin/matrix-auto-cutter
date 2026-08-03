# Matrix Auto Cutter

Bereitet fertige OBS-Bildschirmaufnahmen für den automatischen Schnitt vor: prüft die
Aufnahmedatei auf Identität und Unversehrtheit, rechnet die Ereignisse der Aufnahme
frame-genau in die Zeitbasis des Videos um und markiert die Abschnitte, die nicht
geschnitten werden dürfen.

**Status: in aktiver Entwicklung.**

Fertig und getestet ist der Unterbau — Zeitbasis, Journal- und Sidecar-Verträge,
Schutzbereiche, Quellenprüfung und das atomare Schreiben des Projektstands.
Der eigentliche Schnitt ist noch nicht gebaut: Transkription, Erkennung entfernbarer
Passagen, CTA-Planung, EDL-Ausgabe und Rendern fehlen. Wer das Repo öffnet, findet
die Ingest- und Sicherungsschicht, nicht das fertige Werkzeug.

## Was es löst

Eine Marktanalyse-Aufnahme läuft 45 bis 90 Minuten und soll auf 10 bis 30 Minuten
gekürzt werden. Automatische Stille-Schneider sind dafür ungeeignet: Sie entfernen
genau die Stellen, die bewusst produziert wurden — Intro, Stinger, Szenenwechsel,
Denk- und Chartlesepausen.

Bevor überhaupt ein Schnitt geplant werden darf, muss dreierlei feststehen: dass die
Datei auf der Platte wirklich die aufgezeichnete ist, dass Ereigniszeitstempel exakt
auf Frames abgebildet sind, und welche Bereiche gesperrt sind. Genau diese
Vorbedingungen stellt dieses Projekt her — nachweisbar und ohne stille Annahmen.

## Wie es funktioniert

Die Arbeitsteilung ist bewusst eng gezogen: OBS erzeugt Intro, Outro, Szenenwechsel
und Stinger bereits live während der Aufnahme. Matrix Auto Cutter bearbeitet die
fertige MP4 nach und lässt die live produzierten Abschnitte unangetastet. Die
Originaldateien werden nie verändert.

**Kern**

- `timebase` — ausschließlich CFR 60/1, halboffene Frameintervalle `[start, end)`,
  Rechnung über `Fraction`, keine Gleitkommazahlen.
- `journal` — strikte Validierung des Recording-Journals, das der OBS-Producer
  während der Aufnahme schreibt: lückenlose Sequenz, monotone Uhr- und Counterwerte,
  Pause/Resume-Paarung, eindeutige Event-IDs.
- `calibration` — bildet QPC-Zeitstempel (Nanosekunden) rational auf Quellframes ab;
  rechnet Pausen heraus, bestimmt Taktdrift in ppm und die Unsicherheit je Ereignis.
- `protection` — paart Ereignisse zu Schutzbereichen, erweitert sie um die
  Messunsicherheit und vereinigt die Policies. Ein Bereich kann Zeitschnitte,
  Overlays und lokale Audioreparatur einzeln sperren.
- `sidecar` — Sidecar-1.1-Vertrag mit einem Validator, der keine Exceptions wirft,
  sondern strukturierte Fehler zurückgibt.
- `atomic` — atomares Schreiben, damit ein Abbruch keinen halben Zustand hinterlässt.

**Phase 2 — Vertrauensgrenze zur Datei**

- `close_gate` — wartet über Win32-Handles darauf, dass der Recorder die Datei
  wirklich freigegeben hat, abgesichert über Leases und Besitzprüfung.
- `probe` — gekapselter `ffprobe`-Aufruf: Identität und Version der Binary werden
  gegen eine Policy geprüft, bevor ihrer Ausgabe geglaubt wird; danach
  Stream-Auswahl und Medienprofil.
- `source_confirmation` — belegt, dass die Datei am Pfad dieselbe ist, die das
  Journal beschreibt; erneute Pfadprüfung gegen Umbenennen und Austausch.
- `source_hash` — lease-gebundenes Hashen mit Quittung.
- `finalizer` — Zustandsautomat mit Wiederaufnahme nach Abbruch; veröffentlicht das
  Projektdokument atomar per Compare-and-Swap.
- Querschnitt: Workspace- und Pfadvalidierung, Sperr-Leases, Fortschrittsmeldungen,
  Cancellation, Dateisnapshots.

Jede Stufe meldet Fehler als typisierte Werte statt als Ausnahmen, damit der Aufrufer
jeden Fall behandeln muss.

## Technik

- Python 3.12, Paketierung mit Hatchling, Abhängigkeiten über `uv`
- Pydantic v2 für alle Datenverträge
- Windows-spezifisch, wo es sein muss: direkte Win32-Aufrufe über `ctypes` für
  Handle- und Dateiidentität, jeweils hinter einem Port mit austauschbarer
  Implementierung für die Tests
- Externe Abhängigkeit zur Laufzeit: `ffprobe`. Kein Netzwerkzugriff, keine
  Cloud-API, keine Telemetrie
- `mypy --strict` — fehlerfrei über 70 Quelldateien
- `ruff` — fehlerfrei, inklusive Docstring- und Annotationsregeln
- 1273 Tests mit `pytest`, davon Property-based-Tests mit `Hypothesis` sowie
  Integrationstests gegen echte `ffprobe`-Binaries und echte Win32-Handles
- 100 % Branch Coverage, erzwungen über `fail_under = 100` (9274 Statements,
  2808 Branches)
- Testcode ist umfangreicher als Produktivcode (ca. 25 700 zu 22 100 Zeilen)

Bekannt: ein Windows-Integrationstest schlägt gelegentlich fehl, wenn die gesamte
Suite läuft, und ist einzeln grün — eine Race Condition im Test, nicht im
Produktivcode. Noch nicht behoben.

## Automatischer Post-Stop-Produktpfad

Der eindeutige Startpunkt unter Windows ist:

```text
START-MATRIX-AUTO-CUTTER.cmd
```

Er prüft die normale Installation
`C:\Program Files\obs-studio\bin\64bit\obs64.exe` auf Version 32.1.2, installiert
bei geschlossenem OBS die gebaute Plugin-DLL in die von OBS empfohlene Pluginablage
`C:\ProgramData\obs-studio\plugins` und startet
den sichtbaren Product Runner. Danach startet er das normale OBS oder akzeptiert die
bereits laufende normale Instanz. Portable OBS wird dabei nicht verwendet; Profile,
Szenen und OBS-Einstellungen werden weder kopiert noch geändert.

Nach einem normalen erfolgreichen Stop beobachtet der Runner die normative Ablage
`%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\producer\journals`. Er übernimmt nur
ein vollständig validiertes Journal mit erfolgreichem Stop, liest den exakten MP4-Pfad
aus dessen validiertem Schlussrecord und akzeptiert im Produktbetrieb ausschließlich
Direct MP4 unter `F:\MatrixMarketAutoEdit`. Anschließend verwendet er direkt den
bestehenden Finalizer und dessen erneute Sidecar-1.1-Validierung. Die MP4 wird nur
gelesen.

Der aktuelle maschinenlesbare Zustand liegt unter
`%LOCALAPPDATA%\DimensionWithin\MatrixAutoCutter\product-runner\status.json`.
Dauerhafte atomare Session-Claims liegen im Unterverzeichnis `sessions`; dadurch
bleiben erfolgreiche Läufe nach einem Runnerneustart erledigt und unterbrochene
Finalisierungen verwenden wieder dasselbe Projekt. Das sichtbare Runnerfenster zeigt
dieselben Zustände als deutsche Meldungen. Ein Fehler einer Aufnahme beendet den
Runner nicht und blockiert spätere Aufnahmen nicht.

Ein frisch gebautes Plugin kann separat und reproduzierbar installiert werden:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_obs_plugin.ps1
```

Das Skript verweigert den Austausch bei laufendem OBS, prüft OBS 32.1.2 sowie den
SHA-256 der Kopie und berührt keine anderen Plugins.

Quellcode und Dokumentation sind auf Deutsch.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
