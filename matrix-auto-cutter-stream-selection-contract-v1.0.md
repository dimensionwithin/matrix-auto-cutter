# Matrix Auto Cutter — Stream Selection Contract 1.0

Vorgeschlagener Dateiname: `matrix-auto-cutter-stream-selection-contract-v1.0.md`

## 1. Status

Status: normativer F-07-Vertragsentwurf; keine Implementierungsfreigabe

Dieses Dokument entscheidet ausschließlich die offenen F-07-Regeln für Streamauswahl und kanonische Stream-Evidence.

Die Feststellung der Implementierungsreife erfolgt ausschließlich durch das abschließende Reauditurteil außerhalb dieses Dokumententwurfs. Dieses Dokument autorisiert keine Änderung an Produktivcode, Tests, Goldens, Konfiguration, persistenten Artefakten oder bestehenden Dokumenten.

Eine Implementierung bedarf eines gesonderten F-07-Reparaturauftrags.

F-09 bleibt blockiert. Paket 2C und spätere Pakete werden nicht begonnen.

## 2. Geltungsbereich und Rang

Dieses Addendum gilt ausschließlich für:

- Streamkandidaten und automatische Streamauswahl;
- die Policy `stream_selection/1.0`;
- ffprobe-Streamfelder und deren Normalisierung;
- kanonische Stream-Evidence;
- Dispositionen, Side-Data und Streamtags;
- strukturierte Auswahlgründe;
- Streamauswahl-Evidence-Digest und Auswahlidentität;
- semantische Revalidierung;
- nicht automatisch auswählbare post-parse Probe-Core-Ausgänge.

Die Dokumentpräzedenz bleibt:

1. `matrix-auto-cutter-planning-brief-v0.5.md`
2. `matrix-auto-cutter-architecture-plan-v0.2.md`
3. `matrix-auto-cutter-asset-manifest-v0.5.json`
4. Phase-1-Baseline `87fbfd19a50879abefec21af75d37405f6349da5`
5. `matrix-auto-cutter-architecture-plan-v0.3.md`

Dieses Addendum darf die ersten vier Quellen nicht verengen oder verändern. Für bisher offene F-07-Details konkretisiert es Architektur v0.3.

## 3. Normative Sprache

Die Schlüsselwörter MUSS, DARF NICHT, SOLL, SOLL NICHT und DARF sind normativ.

„Fail closed“ bedeutet, dass keine automatische Auswahl und kein autoritativ verwendbarer `FinalizedStreamSelection`-Wert entsteht.

Diagnosetext ist niemals Auswahlgrund, Identität oder Autoritätsnachweis.

## 4. Exakte Policy und einzige Selektionsfunktion

Die exakte Policy-ID lautet:

`stream_selection/1.0`

Die Policy-ID ist eine interne Konstante. Sie ist kein Callerparameter und darf nicht durch Request, Konfiguration, Deserialisierung oder einen konstruierten Datenwert ersetzt werden.

Die Runtime MUSS die exakte Policy-ID prüfen. Ein bei autoritativer Verwendung vorliegender `FinalizedStreamSelection`-Wert mit einer anderen Policy-ID wird mit `E_PROBE_STREAM_INTEGRITY` abgewiesen.

Es existiert genau eine produktive Selektionsfunktion für `stream_selection/1.0`.

Die initiale Auswahl und jede semantische Revalidierung rufen dieselbe Funktion auf. Es darf keinen zweiten, angenäherten oder teilweise duplizierten Auswahlalgorithmus geben.

Eine Änderung an Kandidatenklassifikation, Defaultsemantik, Support, Rangfolge, Grundcodes, Identitätspayload oder selektionsrelevanter Normalisierung erfordert eine neue Policyrevision.

## 5. Boundedness und Trennung von 2B und 2E

Alle Laufzeitdaten dieses Vertrags unterliegen ausschließlich den bestehenden und bestandenen F-06-Grenzen für:

- ffprobe-Ausgabe und JSON-Parsing;
- Zahlen und Zahlenlexeme;
- Strings;
- Objekte;
- Arrays;
- Rekursion und Strukturtiefe;
- Streams, Tags und Side-Data.

Dieses Addendum führt keine neue numerische Grenzpolitik ein.

Die in Paket 2B gehaltene Probe-Evidence ist bounded Laufzeit-Evidence.

Das spätere persistente Artefakt `probe/<probe-id>/media-probe.json` gehört zu Paket 2E und behält die dort festgelegte 4-MiB-Artefaktgrenze. Dieses Addendum definiert weder dessen Persistenzschema noch dessen Schreib-, Publish-, Migrations- oder Recoveryalgorithmus.

## 6. Verbindliche Prüfpräzedenz

Der einzige Selektionsalgorithmus MUSS Fehler und Entscheidungen in dieser Reihenfolge bestimmen:

1. Raw-JSON- und Schemafehler prüfen, einschließlich doppelter kritischer Felder, falscher Raw-Typen, unbekannter Dispositionsflags und Feldnamenskollisionen.
2. Die vollständige technische Klassifizierbarkeit aller strukturell relevanten Streams prüfen.
3. Die belastbare Defaultzählbarkeit für die jeweilige Defaultmenge prüfen.
4. Defaultambiguität prüfen.
5. Bei genau einem Default dessen Eignung beziehungsweise Support prüfen.
6. Ohne Default vollständig auswertbare Nicht-Defaultstreams nach dem ausdrücklich normierten Support filtern.
7. Die eindeutige automatische Auswahl durchführen oder Missing, Unsupported beziehungsweise Ambiguous zurückgeben.
8. Digest, Auswahlidentität und semantische Revalidierung prüfen.

Daraus folgt:

- Fehlendes oder unbestimmbares `attached_pic` eines Videostreams ergibt `stream_selection.video_metadata`, nicht `stream_selection.video_missing`.
- Fehlendes oder unbestimmbares `default` eines Hauptvideostreams beziehungsweise Audiostreams ergibt den jeweiligen Metadatenausgang.
- Doppelte kritische Felder, unbekannte Dispositionsflags und kritische Feldnamenskollisionen sind Schemafehler.
- Mehrere belastbare Defaults sind ambig, auch wenn einzelne vollständig auswertbare Defaults später als nicht unterstützt klassifiziert würden.
- Ein unvollständiger strukturell relevanter Stream wird niemals durch einen vollständigen anderen Stream verdeckt.
- Schemafehler haben Vorrang vor Metadaten-, Default-, Support- und Auswahlentscheidungen.
- Unvollständige technische Klassifizierbarkeit hat Vorrang vor Defaultambiguität.

## 7. Kanonische Stream-Evidence

### 7.1 Evidenceumfang

Die kanonische Stream-Evidence umfasst alle normalisierten Streams und alle für sie erhaltenen Felder, insbesondere:

- ausgewählte und nicht ausgewählte Video- und Audiostreams;
- Attached Pictures;
- Subtitle-, Data- und Attachmentstreams;
- Streams mit unbekanntem `codec_type`;
- bekannte normalisierte Streamfelder;
- zulässige unbekannte allgemeine Streamfelder;
- vollständige Tags;
- vollständige Side-Data.

Kein Stream wird allein deshalb aus der Evidence entfernt, weil er nicht auswählbar oder unter Policy 1.0 nicht unterstützt ist.

### 7.2 Typgetreue Kanonisierung

Die Kanonisierung MUSS `null`, Boolean, Integer, exakte Dezimalzahl, String, Array und Objekt als verschiedene Wertarten erhalten.

Bekannte Felder verwenden ihre normierte interne Darstellung. Unbekannte Werte dürfen nicht stringifiziert, mit `repr()` abgebildet oder implizit in eine andere Wertart umgewandelt werden.

Objektschlüssel werden nach der bestehenden kanonischen Phase-1-/F-06-Schlüsselordnung sortiert.

Arrays behalten ihre ursprüngliche Reihenfolge, sofern für das konkrete Feld keine eigene Mengenkanonik festgelegt ist.

Die äußere ffprobe-`streams`-Reihenfolge ist die einzige ausdrücklich nicht gebundene Streamarrayreihenfolge.

## 8. Geschlossene Feldnamen und Kollisionen

### 8.1 ASCII-Kollisionsfunktion

Für kritische Feldnamenskollisionen gilt ausschließlich folgende Vergleichsfunktion:

- ASCII-Buchstaben `A` bis `Z` werden auf `a` bis `z` abgebildet.
- Alle anderen Zeichen und Unicode-Codepoints bleiben unverändert.
- Es findet keine Unicode-Normalisierung statt.

Ein unbekannter Feldname kollidiert genau dann mit einem bekannten Feldnamen, wenn beide nach dieser ASCII-Abbildung gleich sind, aber nicht exakt dieselbe Zeichenfolge besitzen.

Nicht verwendet werden:

- Unicode-Casefolding für Feldnamen;
- Unicode-Normalisierung;
- Edit Distance;
- Präfix- oder Suffixvergleich;
- Bindestrich-/Unterstrichumwandlung;
- Trimmen;
- phonetische oder lexikografische Ähnlichkeit;
- sonstige Heuristiken.

Ohne ausdrückliche Enumeration existiert kein Alias. Unter `stream_selection/1.0` ist die enumerierte Aliasmenge für Streamobjekte, Dispositionen, Side-Data und Auswahlidentität jeweils leer.

### 8.2 Streamobjekt

Die exakt bekannten Streamfeldnamen sind:

- `index`
- `codec_name`
- `profile`
- `pix_fmt`
- `codec_type`
- `disposition`
- `time_base`
- `r_frame_rate`
- `avg_frame_rate`
- `start_time`
- `duration`
- `nb_frames`
- `width`
- `height`
- `sample_rate`
- `channels`
- `channel_layout`
- `tags`
- `side_data_list`

Ein anderer Feldname, der ASCII-case-insensitive mit einem dieser Namen kollidiert, ergibt:

- Code: `E_PROBE_SCHEMA`
- Phase: `json_schema`

Ein anderer Feldname ohne solche Kollision ist ein allgemeines Streamzusatzfeld. Er wird innerhalb der bestehenden F-06-Grenzen vollständig, rekursiv und typgetreu als Evidence erhalten und besitzt keinen Auswahlrang.

### 8.3 Disposition

Die exakt bekannten Dispositionsnamen sind:

- `default`
- `dub`
- `original`
- `comment`
- `lyrics`
- `karaoke`
- `forced`
- `hearing_impaired`
- `visual_impaired`
- `clean_effects`
- `attached_pic`
- `timed_thumbnails`
- `non_diegetic`
- `captions`
- `descriptions`
- `metadata`
- `dependent`
- `still_image`
- `multilayer`

Jeder andere Dispositionsname ist unbekannte kritische Semantik und ergibt `E_PROBE_SCHEMA` in `json_schema`.

Eine ASCII-case-insensitive Kollision mit einem bekannten Dispositionsnamen wird genauso abgewiesen.

### 8.4 Side-Data-Eintrag

Die exakt bekannten technisch projizierten Side-Data-Feldnamen sind:

- `side_data_type`
- `rotation`
- `displaymatrix`

Eine ASCII-case-insensitive, aber nicht exakte Kollision mit einem dieser Feldnamen ergibt `E_PROBE_SCHEMA` in `json_schema`.

Alle anderen Side-Data-Zusatzfelder werden typgetreu und rekursiv als Evidence erhalten.

### 8.5 Auswahlidentität

Die Feldmenge der Auswahlidentität ist in Abschnitt 17 geschlossen. Jeder abweichende Feldname und jedes zusätzliche Feld ist unzulässig. Es gibt keine Identitätsfeld-Aliase.

## 9. Streamindex, Disposition und Codecidentifikation

### 9.1 Streamindex

`stream.index` MUSS im ffprobe-Roh-JSON ein echter nichtnegativer JSON-Integer sein.

Unzulässig sind:

- JSON-Boolean;
- numerischer String;
- Dezimalpunkt;
- Exponentenlexem;
- Float- oder Decimalrepräsentation;
- `null`;
- jede Coercion.

Die interne Darstellung ist ein nichtnegativer Integer innerhalb der bestehenden F-06-Zahlengrenze.

Streamindizes müssen eindeutig sein. Doppelte Indizes ergeben `E_PROBE_SCHEMA` in `json_schema`.

Die vollständige Streammenge wird kanonisch aufsteigend nach diesem Index sortiert.

### 9.2 Dispositionswerte

Eine vorhandene Disposition MUSS ein JSON-Objekt sein.

Jeder vorhandene Dispositionswert MUSS ein echter JSON-Integer mit exakt dem Wert `0` oder `1` sein.

Unzulässig sind insbesondere:

- JSON-Boolean;
- String `"0"` oder `"1"`;
- Dezimal- oder Exponentenlexem;
- Float-/Decimalrepräsentation;
- `null`;
- jede Coercion.

Die interne Darstellung lautet:

- `0` → Boolean `false`
- `1` → Boolean `true`

Ein fehlendes selektionskritisches Flag wird als `not_available` klassifiziert und niemals als `false` interpretiert.

Für jeden Videostream muss `attached_pic` belastbar klassifizierbar sein. Für jeden Hauptvideostream mit `attached_pic = false` muss zusätzlich `default` belastbar sein. Für jeden Audiostream muss `default` belastbar sein.

### 9.3 Codecidentifikation

Das Raw-JSON-Feld heißt exakt `codec_name`.

Ist `codec_name` vorhanden und nicht `null`, MUSS der Raw-Wert ein bounded JSON-String sein. Ein anderer vorhandener Raw-Typ ergibt `E_PROBE_SCHEMA` in `json_schema`.

Der Raw-String wird exakt erhalten. Er wird weder getrimmt noch case-normalisiert.

Für die technische Codecprojektion gelten die bereits vorhandenen Semantiken:

- fehlendes Feld oder JSON-`null` → `not_available`;
- leerer oder ausschließlich aus Whitespace bestehender String → `not_available`;
- ein String, dessen unveränderter Wert mittels der bestehenden `casefold()`-Operation exakt `unknown`, `none` oder `n/a` ergibt → bestehender Nichtverfügbarkeits-/Unbekanntzustand;
- jeder andere bounded String → vorhandene Codecidentifikation mit exakt diesem Stringwert.

Die bestehende Sentinelmenge ist damit geschlossen:

- `unknown`
- `none`
- `n/a`

Es wird keine neue Codec-Sentinelmenge und keine Codec-Allowlist eingeführt.

Ein automatisch auswählbarer Video- oder Audiostream MUSS eine vorhandene belastbare Codecidentifikation besitzen.

Fehlende, leere oder bestehend als nicht verfügbar beziehungsweise unbekannt normalisierte Codecidentifikation ergibt:

- für Hauptvideo: `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.video_metadata`;
- für Audio: `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata`.

Der konkrete Codecname bleibt vollständige Evidence, ist aber:

- kein Rang;
- kein Tie-Breaker;
- kein lexikografisches Auswahlkriterium;
- keine Decoderverfügbarkeits-Allowlist.

## 10. Tags und Rotationstags

### 10.1 Allgemeine Tag-Evidence

Ein vorhandenes `tags`-Feld MUSS ein JSON-Objekt aus Stringschlüsseln und Stringwerten sein.

Schlüssel und Werte werden exakt case-sensitive erhalten. Casevarianten werden nicht zusammengeführt oder überschrieben.

Damit sind insbesondere gleichzeitig zulässig:

- `language` und `LANGUAGE`;
- `title` und `TITLE`;
- `rotate` und `Rotate`.

Sprache und Titel sind keine Eignungsvoraussetzungen, Auswahlkriterien oder Tie-Breaker.

Für die optionale Komfortprojektion von `language` und `title` gilt ausschließlich ASCII-case-insensitive Gleichheit mit dem jeweiligen ASCII-Namen:

- kein passender Schlüssel → `not_available`;
- genau ein passender Schlüssel → exakter Wert;
- mehr als ein passender Schlüssel → `ambiguous`.

Mehrere Casevarianten von Sprache oder Titel sind kein Schemafehler.

### 10.2 Keine autoritative Tagrotation unter Policy 1.0

Unter `stream_selection/1.0` existiert keine autoritative technische Rotationstagsprojektion.

Das gilt für `rotate`, `Rotate`, `ROTATE` und jede andere Tag-Schreibweise gleichermaßen.

Rotationstags:

- bleiben vollständige case-sensitive Tag-Evidence;
- werden in `stream_selection_evidence_digest` gebunden;
- beeinflussen die automatische Auswahl nicht;
- beeinflussen die technischen Rotationsfelder nicht;
- erzeugen keinen Schemafehler allein wegen ihres Schlüsselnamens oder Stringwertes;
- werden nicht mit Side-Data- oder Display-Matrix-Rotation verglichen;
- können keinen Side-Data-Konflikt erzeugen.

Mehrere Rotationstag-Casevarianten sowie gleiche oder widersprüchliche Stringwerte bleiben reine Evidence.

Nur die bereits normierten Side-Data-/Display-Matrix-Projektionen dürfen unter Policy 1.0 technisch ausgewertet werden.

Ein zukünftiger autoritativer Rotationstagvertrag benötigt eine neue Policyrevision oder ein separates, ausdrücklich ranggeordnetes normatives Addendum.

## 11. Side-Data und Display Matrix

`side_data_list` ist vollständige bounded Stream-Evidence.

Die Reihenfolge ihrer Einträge wird erhalten. Jeder Eintrag wird vollständig, rekursiv und typgetreu kanonisiert.

Unbekannte Zusatzfelder sind zulässig, sofern ihr Name nicht nach Abschnitt 8.4 mit einem bekannten technisch projizierten Feld kollidiert.

Die bereits bestehenden normalisierten Side-Data-/Display-Matrix-Projektionen bleiben unverändert. Dieses Addendum erweitert weder ihre Einheit noch ihren Wertebereich oder ihre Zahlenverarbeitung.

Wenn mehrere bekannte Side-Data-/Display-Matrix-Projektionen desselben Streams widersprüchliche normalisierte technische Ergebnisse liefern, ergibt sich:

- Code: `E_PROBE_SCHEMA`
- Phase: `json_schema`

Rotationstags werden bei dieser Konfliktprüfung nicht berücksichtigt.

Side-Data ist kein Auswahlrang und kein Tie-Breaker.

## 12. Vollständige technische Klassifizierbarkeit

### 12.1 Video

Zunächst werden alle Streams mit normalisiertem `codec_type = video` erfasst.

Für jeden solchen Stream muss `attached_pic` belastbar klassifizierbar sein. Ist dies für mindestens einen Videostream nicht möglich, lautet der Ausgang `E_PROBE_UNSUPPORTED_MEDIA` in `stream_selection.video_metadata`.

Die Hauptvideomenge besteht anschließend aus exakt den Videostreams mit `attached_pic = false`.

Jeder Hauptvideostream muss hinsichtlich aller Auswahlmetadaten vollständig klassifizierbar sein:

- gültiger eindeutiger Streamindex;
- belastbares `codec_type = video`;
- belastbares `attached_pic = false`;
- belastbares `default`;
- belastbare Codecidentifikation;
- positive exakte `width`;
- positive exakte `height`;
- keine widersprüchliche bekannte Auswahl- oder Sicherheitssemantik.

Ist mindestens ein Hauptvideostream unvollständig, unbestimmt oder widersprüchlich, endet die gesamte Videoauswahl mit:

- Code: `E_PROBE_UNSUPPORTED_MEDIA`
- Phase: `stream_selection.video_metadata`

Ein vollständiger anderer Videostream darf ihn nicht verdecken.

`pix_fmt` und CFR gehören nicht zur Vollständigkeitsvoraussetzung.

### 12.2 Audio

Zunächst werden alle Streams mit normalisiertem `codec_type = audio` erfasst.

Jeder Audiostream muss vollständig klassifizierbar sein hinsichtlich:

- gültigem eindeutigem Streamindex;
- belastbarem `codec_type = audio`;
- belastbarem `default`;
- belastbarer Codecidentifikation;
- positiver exakter Samplerate;
- positiver exakter Kanalzahl;
- vorhandenem nicht leerem Kanallayout;
- positiver exakter Streamdauer;
- widerspruchsfreier bekannter Auswahl- und Sicherheitssemantik.

Ist mindestens ein Audiostream unvollständig, unbestimmt oder widersprüchlich, endet die gesamte Audioauswahl mit:

- Code: `E_PROBE_UNSUPPORTED_MEDIA`
- Phase: `stream_selection.audio_metadata`

Ein vollständig auswertbarer anderer Audiostream darf ihn nicht verdecken.

Eine vollständig auswertbare, aber unter Policy 1.0 nicht unterstützte Layout-/Kanalzahlkombination ist nicht „unvollständig“. Sie wird erst nach Defaultzählung durch die Supportregel behandelt.

## 13. Videoauswahl

### 13.1 Missing

Ist kein Videostream vorhanden, lautet der Ausgang:

- Code: `E_PROBE_UNSUPPORTED_MEDIA`
- Phase: `stream_selection.video_missing`

Sind Videostreams vorhanden, aber alle nach belastbarer Klassifikation Attached Pictures, gilt derselbe Missing-Ausgang.

Ein Videostream mit fehlendem oder unbestimmbarem `attached_pic` erzeugt dagegen `stream_selection.video_metadata`.

### 13.2 Defaultprüfung

Erst nachdem alle Hauptvideostreams vollständig klassifizierbar sind, wird ihre Defaultanzahl bestimmt.

- Mehr als ein Video-Default ergibt `E_PROBE_AMBIGUOUS_STREAMS` in `stream_selection.video_ambiguous`.
- Genau ein vollständig auswertbarer Video-Default wird ausgewählt und erhält `video_unique_default`.
- Kein Default führt zur Auswahl nach Eignung und Auflösung.

### 13.3 Eignung und Auflösungsordnung

Jeder vollständig klassifizierbare Hauptvideostream ist unter Policy 1.0 ein geeigneter Videokandidat. Es gibt keine Codec-Allowlist, keinen `pix_fmt`-Filter und keinen CFR-Filter.

Für jeden Kandidaten gelten:

- `long_edge = max(width, height)`
- `short_edge = min(width, height)`

A dominiert B genau dann, wenn:

- `A.long_edge >= B.long_edge`;
- `A.short_edge >= B.short_edge`;
- mindestens eine Ungleichung strikt ist.

`width * height` ist kein Rang.

Ohne Default gilt:

- Genau ein geeigneter Kandidat wird mit `video_single_eligible` ausgewählt.
- Bei mehreren Kandidaten darf nur ein einziger eindeutig maximaler Kandidat gewinnen; Grundcode `video_unique_resolution_maximum`.
- Mehrere maximale oder unvergleichbare maximale Kandidaten ergeben `E_PROBE_AMBIGUOUS_STREAMS` in `stream_selection.video_ambiguous`.

Damit gilt ausdrücklich:

- ein vollständiger Hauptvideostream plus ein unvollständiger Nicht-Default-Hauptvideostream → `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.video_metadata`, keine Auswahl.

Index, Listenposition, Codecname, Sprache, Titel, `pix_fmt`, CFR und lexikografische Ordnung dürfen keine Videoambiguität auflösen.

## 14. Audioauswahl

### 14.1 Missing

Ist kein Audiostream vorhanden, lautet der Ausgang:

- Code: `E_PROBE_UNSUPPORTED_MEDIA`
- Phase: `stream_selection.audio_missing`

### 14.2 Unterstützte Layouts

Unter Policy 1.0 werden ausschließlich unterstützt:

- `mono` mit exakt einem Kanal;
- `stereo` mit exakt zwei Kanälen.

Die Rangfolge lautet exakt:

`stereo > mono`

Jede andere vollständig auswertbare Layout-/Kanalzahlkombination ist unter Policy 1.0 nicht unterstützt.

Der geschlossene Detailcode für einen insgesamt nicht auswählbaren Ausgang aufgrund einer solchen Kombination lautet:

`audio_layout_unsupported`

Er gehört zu:

- Code: `E_PROBE_UNSUPPORTED_MEDIA`
- Phase: `stream_selection.audio_metadata`

Er ist kein neuer Top-Level-Errorcode.

### 14.3 Defaultprüfung

Erst nachdem alle Audiostreams technisch vollständig klassifizierbar sind, wird die Defaultanzahl über alle Audiostreams bestimmt.

- Mehr als ein Default ergibt `E_PROBE_AMBIGUOUS_STREAMS` in `stream_selection.audio_ambiguous`, unabhängig vom späteren Layoutsupport.
- Genau ein Default wird nur ausgewählt, wenn seine Layout-/Kanalzahlkombination unter Policy 1.0 unterstützt ist.
- Ein einzelner Default mit nicht unterstütztem Layout blockiert die Auswahl mit `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata` und `audio_layout_unsupported`.
- Ein unterstützter Nicht-Default darf einen nicht unterstützten Default nicht ersetzen.
- Ein ausgewählter Default erhält `audio_unique_default`.

### 14.4 Auswahl ohne Default

Wenn kein Default vorhanden ist, werden erst jetzt vollständig auswertbare Nicht-Defaultstreams nach Layoutsupport gefiltert.

Ein vollständig auswertbarer, aber nicht unterstützter Nicht-Default darf aus der Kandidatenmenge ausgeschlossen werden. Er bleibt vollständig in der Evidence gebunden.

Danach gilt:

- Kein unterstützter Kandidat → `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata`, `audio_layout_unsupported`.
- Genau ein unterstützter Kandidat → Auswahl mit `audio_single_eligible`.
- Mehrere unterstützte Kandidaten → exakt `stereo > mono`.
- Genau ein Kandidat mit eindeutig höchstem Layout → `audio_unique_highest_supported_layout`.
- Mehrere Kandidaten mit demselben höchsten Layout → `E_PROBE_AMBIGUOUS_STREAMS`, `stream_selection.audio_ambiguous`.

Damit gelten ausdrücklich:

- ein vollständiger Audiostream plus ein unvollständiger Nicht-Default-Audiostream → `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata`;
- ein Stereo-Nicht-Default plus ein vollständig auswertbarer 5.1-Nicht-Default → 5.1 wird nach vollständiger Klassifikation ausgeschlossen; Stereo wird mit `audio_single_eligible` ausgewählt;
- ein Stereo-Nicht-Default plus ein unvollständiger Audiostream → `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata`;
- ein Stereo-Nicht-Default plus ein nicht unterstützter 5.1-Default → `E_PROBE_UNSUPPORTED_MEDIA`, `stream_selection.audio_metadata`, `audio_layout_unsupported`;
- ein unterstützter und ein nicht unterstützter Default → `E_PROBE_AMBIGUOUS_STREAMS`, `stream_selection.audio_ambiguous`;
- mehrere Defaults, von denen nur einer unterstützt ist → `E_PROBE_AMBIGUOUS_STREAMS`, `stream_selection.audio_ambiguous`.

Index, Listenposition, Sprache, Titel, Codecname und lexikografische Ordnung dürfen keinen Audiogleichstand auflösen.

## 15. CFR und `pix_fmt`

### 15.1 CFR

Die von `-show_streams` gelieferten Zusammenfassungswerte beweisen keine konstante Folge tatsächlicher Frameintervalle.

Insbesondere sind allein nicht autoritativ:

- `avg_frame_rate == r_frame_rate`;
- `nb_frames / duration == frame_rate`;
- Kombinationen dieser Werte.

Der CFR-Status unter `stream_selection/1.0` lautet:

`not_established`

CFR darf:

- keinen Kandidaten ausschließen;
- keinen Rang erzeugen;
- keinen Gleichstand brechen;
- keinen Auswahlgrundcode erzeugen.

Dieses Addendum definiert keinen `-show_frames`- oder `-show_packets`-Produktpfad.

Ein `ProbeOk` unter Paket 2B beweist keine CFR-60/1-Finalisierungseignung.

### 15.2 `pix_fmt`

`pix_fmt` ist optionale normalisierte und digestgebundene Evidence.

Es ist:

- keine Eignungspflicht;
- kein Supportfilter;
- kein Rang;
- kein Tie-Breaker;
- kein Auswahlgrundcode.

## 16. Geschlossene Auswahlgrundcodes

Ein erfolgreicher `FinalizedStreamSelection`-Wert enthält genau einen Video- und genau einen Audio-Grundcode.

Die geschlossene Enum unter Policy 1.0 besteht exakt aus diesen sechs Codes:

Video:

- `video_single_eligible`
- `video_unique_default`
- `video_unique_resolution_maximum`

Audio:

- `audio_single_eligible`
- `audio_unique_default`
- `audio_unique_highest_supported_layout`

Andere Werte sind unzulässig.

Callertext, Diagnosetext, zusätzliche Codes, Listen freier Begründungen und unbekannte Enumwerte dürfen nicht als Grundcode akzeptiert werden.

Das Feld `video_reason_code` akzeptiert ausschließlich die drei Video-Codes. Das Feld `audio_reason_code` akzeptiert ausschließlich die drei Audio-Codes.

Policy 1.0 besitzt keinen CFR-Grundcode.

## 17. Digest und vollständig geschlossene Auswahlidentität

### 17.1 `stream_selection_evidence_digest`

Der separate Streamauswahl-Evidence-Digest heißt exakt:

`stream_selection_evidence_digest`

Er ist SHA-256 über:

1. die ASCII-Domainseparation `matrix-stream-selection-evidence/1.0`;
2. genau ein Nullbyte `0x00`;
3. die kanonischen Bytes der vollständigen, nach Streamindex sortierten Streammenge.

Er wird als kleingeschriebener 64-stelliger Hexstring dargestellt.

Er umfasst insbesondere:

- ausgewählte und nicht ausgewählte Streams;
- zulässige unbekannte allgemeine Streamfelder;
- Tags einschließlich Rotationstags;
- Side-Data;
- Attached Pictures;
- nicht auswählbare Streamtypen.

Eine Permutation des äußeren `streams`-Arrays verändert den Digest nicht.

Die Reihenfolge von Arrays innerhalb eines gebundenen Streamfeldes bleibt Evidence, sofern keine eigene Mengenkanonik besteht.

Der Digest ergänzt bestehende breitere MediaProfile-, Probe-Evidence-, Binary- und Probeversionsbindungen. Er ersetzt oder verengt keinen davon.

### 17.2 Geschlossene Identitätspayload

Für `matrix-stream-selection-identity/1.0` besitzt die Payload exakt diese sechs case-sensitive Felder:

- `policy_id`
- `stream_selection_evidence_digest`
- `video_index`
- `audio_index`
- `video_reason_code`
- `audio_reason_code`

Kein siebtes Feld ist zulässig. Kein Feld darf fehlen. Unbekannte Felder werden abgewiesen.

Die Feldtypen und Wertebereiche sind exakt:

- `policy_id`: JSON-String mit exakt `stream_selection/1.0`;
- `stream_selection_evidence_digest`: JSON-String, exakt der kleingeschriebene 64-stellige SHA-256-Hexwert der gebundenen Stream-Evidence;
- `video_index`: nichtnegativer JSON-Integer gemäß Abschnitt 9.1;
- `audio_index`: nichtnegativer JSON-Integer gemäß Abschnitt 9.1;
- `video_reason_code`: JSON-String aus exakt der geschlossenen Video-Enum in Abschnitt 16;
- `audio_reason_code`: JSON-String aus exakt der geschlossenen Audio-Enum in Abschnitt 16.

### 17.3 Exakte kanonische Payloadbytes

Die Payload wird als JSON-Objekt in exakt dieser Schlüsselreihenfolge serialisiert:

1. `audio_index`
2. `audio_reason_code`
3. `policy_id`
4. `stream_selection_evidence_digest`
5. `video_index`
6. `video_reason_code`

Es gelten:

- UTF-8 ohne BOM;
- keine Leerzeichen oder Zeilenumbrüche;
- `,` unmittelbar zwischen Feldern;
- `:` unmittelbar zwischen Schlüssel und Wert;
- keine abschließende LF-Sequenz;
- Stringescaping nach der bestehenden kanonischen JSON-Semantik;
- Integer als kanonische dezimale JSON-Integer ohne Pluszeichen und ohne führende Nullen.

Die Byteform entspricht damit:

```text
{"audio_index":<audio-index>,"audio_reason_code":"<audio-reason>","policy_id":"stream_selection/1.0","stream_selection_evidence_digest":"<digest>","video_index":<video-index>,"video_reason_code":"<video-reason>"}
```

Die Platzhalter werden durch die kanonisch serialisierten tatsächlichen Werte ersetzt und sind nicht Teil der Bytes.

### 17.4 Identitätsdigest

Die Auswahlidentität ist SHA-256 über:

1. die ASCII-Domainseparation `matrix-stream-selection-identity/1.0`;
2. genau ein Nullbyte `0x00`;
3. die exakten kanonischen Payloadbytes aus Abschnitt 17.3.

Das Ergebnis wird als kleingeschriebener 64-stelliger Hexstring dargestellt.

Die Domainseparation, sechs Felder, Feldnamen, Feldtypen, Feldsemantik, Feldreihenfolge und kanonischen Payloadbytes sind Bestandteil von Policy 1.0.

Jede Payloaderweiterung, jedes zusätzliche Feld oder jede Feldsemantikänderung erfordert eine neue Auswahlidentitätsrevision und eine neue Streamauswahl-Policyrevision.

## 18. Semantische Autoritätsprüfung

`FinalizedStreamSelection` ist ein Datenwert und allein niemals Autorität.

Keine Autorität entsteht durch:

- direkte Konstruktion;
- `dataclasses.replace()`;
- flache oder tiefe Kopie;
- Pickle;
- Deserialisierung;
- Umgehung einer Frozen-Grenze;
- einen lediglich formell passenden Digest;
- einen selbstkonsistent neu berechneten falschen Digest.

Es gibt keinen zusätzlichen Issuertoken.

Unmittelbar vor jeder autoritativen Verwendung MUSS:

1. die unabhängig gebundene kanonische Stream-Evidence aus dem umgebenden Probe-/Profilevidence-Vertrag bezogen werden;
2. `stream_selection_evidence_digest` neu berechnet werden;
3. die einzige produktive Selektionsfunktion mit exakt `stream_selection/1.0` erneut ausgeführt werden;
4. der neu berechnete Erwartungswert feldgenau verglichen werden.

Exakt zu vergleichen sind:

- Policy-ID;
- `stream_selection_evidence_digest`;
- Videoindex;
- Audioindex;
- Video-Grundcode;
- Audio-Grundcode;
- Auswahlidentität.

Ergibt die erneute Auswahl keinen Erfolg oder weicht eines dieser Felder ab, gilt:

- Code: `E_PROBE_STREAM_INTEGRITY`
- Phase: `stream_finalization_integrity`

Die allein im vorliegenden `FinalizedStreamSelection`-Wert enthaltene Evidence ist ohne ihre unabhängige übergeordnete Bindung keine Autoritätsquelle.

Im normalen `run_probe()`-Pfad findet die vollständige semantische Revalidierung genau einmal unmittelbar vor dem einzigen finalen `ProbeOk`-Commitpunkt statt.

Die Revalidierung ist ein weiterer Aufruf derselben Selektionsfunktion, kein zweiter Algorithmus.

## 19. Fehlercodes und Phasen

Es gelten die bestehenden Top-Level-Codes:

- `E_PROBE_SCHEMA`
- `E_PROBE_UNSUPPORTED_MEDIA`
- `E_PROBE_AMBIGUOUS_STREAMS`
- `E_PROBE_STREAM_INTEGRITY`

Es gelten die bestehenden Streamauswahlphasen:

- `stream_selection.video_missing`
- `stream_selection.audio_missing`
- `stream_selection.video_metadata`
- `stream_selection.audio_metadata`
- `stream_selection.video_ambiguous`
- `stream_selection.audio_ambiguous`

Für Streamschemafehler gilt `json_schema`.

Für semantische Integritätsfehler gilt `stream_finalization_integrity`.

Als geschlossener Detailcode unter `E_PROBE_UNSUPPORTED_MEDIA` und `stream_selection.audio_metadata` gilt:

`audio_layout_unsupported`

Er ist kein Top-Level-Errorcode.

## 20. Nicht automatisch auswählbare Probe-Core-Ausgänge

Wenn ffprobe-JSON erfolgreich bounded geparst und normalisiert wurde, die automatische Auswahl aber wegen Missing, Unsupported oder Ambiguität endet, behält der Probe-Core-Ausgang:

- das bounded normalisierte Profil;
- die vollständige kanonische Stream-Evidence;
- `stream_selection_evidence_digest`;
- Fehlercode;
- bestehende Phase;
- einen gegebenenfalls definierten Detailcode.

Ein solcher Ausgang enthält keinen autoritativ verwendbaren `FinalizedStreamSelection`-Wert und keine erfolgreiche Auswahlidentität.

Die Evidence dient ausschließlich Diagnose, Reportbindung und späterer gebundener Assignmentprüfung.

Eine spätere Benutzerzuweisung bleibt ausschließlich Sache von `stream_assignment/1.0` und Paket 2E.

## 21. Explizite Nicht-Scope-Grenze

Dieses Addendum gestaltet nicht erneut:

- F-01 — Capability-Issuer- und Constructibility-Grenze;
- F-02 — Versionsparser, Supportpolicy und Reportbindung;
- F-03 — suspended Prozessstart und Jobzuweisung;
- F-04 — Dateiumleitung, Jobterminalität und Größen-Gate;
- F-05 — kausale Fehlerlinearisation und finaler Commitpunkt;
- F-06 — bounded numerische und rationale Verarbeitung;
- F-08 — Handleownership und Cleanup-/BaseException-Semantik.

Nicht umfasst sind:

- Prozessstart, Job Objects, Outputdateien oder Cleanup;
- Close-Gate, Lease, Hash, SourceIdentity oder Finalizer;
- persistente `media-probe`- oder `stream_assignment`-Algorithmen;
- Änderungen an Phase 1;
- F-09;
- Paket 2C bis 2H.

Die bestehende Binary-/Versionspolicy bleibt eine unveränderte Abhängigkeit.

F-09 bleibt blockiert.

## 22. Auswirkungen auf einen späteren F-07-Reparaturauftrag

Ein gesondert freigegebener F-07-Reparaturauftrag MUSS ausschließlich folgende Vertragsdeltas umsetzen:

- belastbare Codecprojektion mit bestehender Sentinelsemantik;
- vollständige Klassifikation aller relevanten Streams vor Defaultzählung;
- geschlossene ASCII-Feldnamenskollision ohne Aliasheuristik;
- Entfernung autoritativer Rotationstagprojektion aus Policy 1.0;
- technische Rotation ausschließlich aus bestehender Side-Data-/Display-Matrix-Projektion;
- exakt geschlossene Identitätspayload mit sechs Feldern;
- alle bereits geschlossenen A–H-Regeln dieses Vertrags.

Der Reparaturauftrag darf weder F-09 noch Paket 2C oder spätere Pakete beginnen.
