# Ablage — repeat/Diagnose-Artefakte

## Struktur

- **`docs\repeat\`** -- versioniert: Uebergaben, Umgebung, Inventare.
- **`labels\repeat\`** -- versioniert: Urteile, Vokabeldatei. Klein, teuer.
  NICHT nach `data\` verschieben, das ist ignoriert.
- **`artefakte\repeat\`** -- ignoriert: Transkripte, whisper-Rohausgaben,
  Berichte, Prototypen, Probelaeufe.

## Teuer vs. billig

Teuer sind die whisper-Rohausgaben (`*.wav.json`) unter `artefakte\repeat\`.
Stand 21.8.2026 (`Get-ChildItem -Recurse -Filter *.wav.json | Measure-Object
-Property Length -Sum`): **42 Dateien, 55.786.980 Bytes (53,2 MB)**. Die
Dateien heissen heute unterschiedlich je Auftragsordner -- ueberwiegend
`audio.wav.json` in den aelteren `nacht\`/`old\`/`batch-musik\`-Ordnern,
daneben `transkript.wav.json`, `transkript-rendered.wav.json` (Shorts-Linie,
je Aufnahmeordner) und `probe-medium.wav.json`/`probe-turbo.wav.json`
(Modellvergleiche). `audio.wav`/`transkript.wav` selbst sind gross und in
Sekunden neu erzeugbar und werden nicht aufbewahrt.

## Ordnerkonvention Shorts-Linie

Ausgaben der Shorts-Linie liegen unter
`artefakte\repeat\shorts\<aufnahmename>\`. Belegt am Ordner
`artefakte\repeat\shorts\2026-08-19 17-26-15\`:

- `shorts-job.json` -- Auftragsdatei
- `avatar-cut.mp4`, `avatar-cut.json`, `avatar-cut.mp4.framecount.json` --
  Avatarschnitt
- `transkript-rendered.wav.json`, `transkript-rendered.json` -- Transkript
  (Rohausgabe und aufbereitete Fassung)
- `kandidaten.json` -- Kandidaten
- `urteile-2026-08-21-105656.json`, `urteile-2026-08-21-114055.json` --
  Urteile

## WARNUNG

**In diesem Repository niemals `git clean -xfd` ausfuehren.** Der Befehl
loescht `/artefakte/` mit, darunter die teuren whisper-Rohausgaben.

**Urteilsdateien nie anfassen.** `urteile-*.json`, auch die unter
`urteile-verworfen\`, werden nie geloescht, ueberschrieben oder verschoben.
Urteilszeit ist das einzige Artefakt in diesem Repository, das sich nicht
neu erzeugen laesst -- anders als Transkripte oder Renderausgaben, die aus
Quellmaterial neu gebaut werden koennen.

## Regel fuer kommende Laeufe

Ausgaben von Laeufen gehen ab sofort nach `artefakte\repeat\<auftragsname>\`,
nie auf den Desktop.
