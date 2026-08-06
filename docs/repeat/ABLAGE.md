# Ablage — repeat/Diagnose-Artefakte

## Struktur

- **`docs\repeat\`** -- versioniert: Uebergaben, Umgebung, Inventare.
- **`labels\repeat\`** -- versioniert: Urteile, Vokabeldatei. Klein, teuer.
  NICHT nach `data\` verschieben, das ist ignoriert.
- **`artefakte\repeat\`** -- ignoriert: Transkripte, whisper-Rohausgaben,
  Berichte, Prototypen, Probelaeufe.

## Teuer vs. billig

Teuer sind die elf `audio.wav.json`, zusammen rund 13 MB. `audio.wav` ist
gross und in Sekunden neu erzeugbar und wird nicht aufbewahrt.

## WARNUNG

**In diesem Repository niemals `git clean -xfd` ausfuehren.** Der Befehl
loescht `/artefakte/` mit, darunter die teuren whisper-Rohausgaben.

## Regel fuer kommende Laeufe

Ausgaben von Laeufen gehen ab sofort nach `artefakte\repeat\<auftragsname>\`,
nie auf den Desktop.
