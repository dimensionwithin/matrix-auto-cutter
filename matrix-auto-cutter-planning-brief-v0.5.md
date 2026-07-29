# Matrix Auto Cutter — Planning Brief v0.5

Status: kanonische, implementierungsreife Produktspezifikation / Scope Freeze  
Stand: 2026-07-12  
Produktverantwortung: Joshua / DimensionWithin  
Technische Ausführung: `matrix-auto-cutter-architecture-plan-v0.1.md`

## 1. Geltung und Entscheidungsrang

Dieses Dokument ersetzt `matrix-auto-cutter-fable-planning-brief-v0.4.md` als kanonische Produktspezifikation. Das v0.4-Dokument bleibt unverändert als Historie erhalten. Bei Widersprüchen gilt v0.5. Technische Implementierungsentscheidungen stehen verbindlich im Architekturplan v0.1; sie sind keine offenen Produktentscheidungen.

Die wichtigste Änderung ist die klare Systemgrenze: OBS produziert Intro, Outro, Szenenwechsel und Transition-Stinger bereits live. Matrix Auto Cutter bearbeitet anschließend die fertige OBS-Aufnahme, schützt die live produzierten Abschnitte und fügt diese Elemente in Version 1 weder ein noch ordnet es sie um.

## 2. Nachweisbarer Projekt- und Assetbestand

Der geprüfte Projekt-Root ist `D:\workspace\matrix-auto-cutter`. Dort liegen:

- das bisherige Briefing `matrix-auto-cutter-fable-planning-brief-v0.4.md`;
- das bisherige Manifest `matrix-auto-cutter-asset-manifest-v0.4.json`;
- das Overlay-Paket `dimensionwithin-overlays-webm.zip` mit zwölf WebMs und `README.txt`;
- das OBS-Intro `intro-sting-sovereign-1440p.webm`;
- der OBS-Stinger `stinger-sovereign-desk-2200ms-trackmatte-1440p.webm`;
- die Windows-Ordneransichtsdatei `desktop.ini`.

Eine separate Audit-Datei ist im Projekt-Root nicht vorhanden. Die technischen Bestandsdaten des vorhandenen Manifests wurden gegen die Dateien beziehungsweise ZIP-Mitglieder geprüft und in `matrix-auto-cutter-asset-manifest-v0.5.json` übernommen. Das in v0.4 erwähnte `matrix-auto-cutter-overlay-manifest-v0.3.json` ist nicht vorhanden und wird nicht als Eingabe vorausgesetzt.

Das ZIP-README `dimensionwithin-overlays-webm.zip#README.txt` bestätigt die Track-Matte-Struktur: Fill links, Schwarz-Weiß-Maske rechts. `glocke.webm` besitzt als einziges CTA-Overlay eine Opus-Audiospur. Die beiden losen OBS-WebMs bleiben Original- und Referenzassets; Einzelheiten und Prüfsummen stehen im Manifest v0.5.

## 3. Produktdefinition

Matrix Auto Cutter ist eine spezialisierte lokale Windows-Anwendung für die automatische, konservative Postproduktion von Joshuas OBS-aufgezeichneten YouTube-Longform-Videos zu Krypto-, Chart- und Marktanalysen. Typische Aufnahmen sind 45 bis 90 Minuten lang, 2560×1440 bei 60 FPS, enthalten einen Chart- oder Desktop-Feed und einen kleinen Pepe-Avatar unten rechts. Die angestrebte Endlänge liegt meist bei 10 bis 30 Minuten, ist aber kein Schnittzwang.

Das Produkt ist kein allgemeiner Videoeditor. Qualität und Schutz der Aussage haben Vorrang vor Laufzeit. Der Prioritätsrahmen aus v0.4 bleibt gültig: hoher Automatisierungsgrad, maximale Schnittqualität, anschließend gute lokale Bedienbarkeit und erst danach Pipeline-Geschwindigkeit.

## 4. Produktionsgrenze: OBS und Matrix Auto Cutter

### 4.1 Verantwortung von OBS

OBS erzeugt während der Aufnahme:

- Intro und Outro;
- Szenenwechsel;
- Transition-Stinger;
- bewusst inszenierte Live-Abschnitte;
- die zeitliche Reihenfolge von Hook, Intro, Hauptinhalt, CTA-Passagen und Outro, soweit diese Struktur in der Aufnahme vorkommt.

Diese Elemente sind Bestandteil der importierten MP4 und gelten als redaktionell beabsichtigt.

### 4.2 Verantwortung von Matrix Auto Cutter v1

Matrix Auto Cutter:

- analysiert eine abgeschlossene lokale OBS-MP4;
- transkribiert gemischte deutsche und englische Sprache lokal mit Wortzeitstempeln;
- bildet OBS-Ereignisse in Schutzbereiche ab;
- reduziert eindeutig leere, ungeschützte Passagen konservativ;
- erkennt Fehlstarts, Selbstkorrekturen und sicher entfernbare Füllwörter;
- schützt Denk-, Chartlese- und Dramaturgiepausen;
- behandelt Mundklicks und Schmatzgeräusche nur, wenn die Maßnahme als sicher eingestuft wird;
- normalisiert und verbessert Audio zurückhaltend;
- erkennt CTA-Absichten im Transkript und plant passende Notification-Overlays;
- erzeugt EDL, Source-to-Output-Mapping sowie JSON- und HTML-Review;
- rendert sicher eine finale MP4;
- lässt alle Originaldateien unverändert.

Matrix Auto Cutter fügt in v1 kein Intro, Outro oder Stinger ein, ersetzt nichts davon, verschiebt diese Elemente nicht und verwendet sie nicht als automatische Übergänge. `intro-sting-sovereign-1440p.webm` und `stinger-sovereign-desk-2200ms-trackmatte-1440p.webm` dürfen lediglich als OBS-Assets, Referenzen und Kandidaten für eine spätere Fingerprint-Erweiterung registriert werden.

## 5. Verbindlicher Version-1-Scope

### 5.1 Import und lokale Verarbeitung

- Zielsystem ist Windows; die Anwendung ist lokal und anklickbar, nicht CLI-only.
- Eingabe ist primär eine abgeschlossene OBS-MP4. 2560×1440, 60 FPS und eine gemischte Audio-/Videodatei sind das Standardprofil; abweichende Quellen werden vor Analyse geprobt und entweder unterstützt oder mit klarer Begründung abgewiesen.
- Die in v0.4 genannten Workflow-Vorgaben `D:\media` für Quellen und `D:\workspace` für Exporte bleiben konfigurierbare Standardpfade. Der tatsächliche Code-/Planungs-Root ist der in Abschnitt 2 belegte Root.
- Keine Cloud-API und keine Internetpflicht für Analyse oder Rendering.
- Qualität hat Vorrang vor Geschwindigkeit. Bekannte Zielhardware aus v0.4: Ryzen 9 3900X, RTX 3060, 64 GB RAM; CUDA-Verfügbarkeit wird geprüft und nicht vorausgesetzt.

### 5.2 Schnittziel

Der Zielstil ist ruhig und sauber verdichtet, nicht hektisch. Automatisch entfernt werden nur Passagen mit ausreichend hoher Sicherheit. Nicht jede Stille ist Dead Air. Folgende Kontexte erhöhen oder erzwingen Schutz:

- Denkpause vor einer wichtigen Aussage;
- Zeit zum Lesen oder Zeigen eines Charts;
- dramaturgische Pause;
- OBS-produzierter Abschnitt oder Szenenwechsel;
- Selbstkorrektur, deren Sinn durch einen Teilcut verfälscht würde;
- Transkript- oder Analyseunsicherheit.

Bekannte Chartkontext-Phrasen aus v0.4, darunter „interessanter Bereich“ und „interessante Stelle“, sind Startpunkte einer konfigurierten Schutzbibliothek. Cursoraktivität ist eine spätere Erweiterung, kein v1-Kernsignal.

### 5.3 Sprache, Füllwörter und Korrekturen

Deutsch und Englisch dürfen innerhalb eines Satzes wechseln. Das Ergebnis enthält Wortanfang, Wortende und Confidence je Wort. Besonders zu prüfen sind „äh“, „ähm“, „mhm“, „ja“, „also“, „sozusagen“, „im Grunde“ und „genau“.

„Äh“ und „ähm“ dürfen nur dann automatisch entfernt werden, wenn ausreichende Audiohandles vorhanden sind, keine Schutzregel greift und der Satz nach dem Schnitt semantisch und akustisch intakt bleibt. Wörter wie „ja“, „also“ oder „genau“ sind nur als kontextabhängige Kandidaten zulässig. Selbstkorrekturen wie „Bitcoin ist bei 120k … äh nein, 112k“ müssen als zusammengehöriger Korrekturkontext erkannt werden. Stilistische Wiederholungen bleiben erhalten.

### 5.4 Audio

Die Anwendung darf De-Click, vorsichtige Rauschbearbeitung, sanfte Dynamikbearbeitung und Lautheitsnormalisierung einsetzen. Verarbeitung muss reproduzierbar, im Review ausgewiesen und auf Kopien beziehungsweise Render-Zwischendaten beschränkt sein. Sie darf Sprachverständlichkeit und Natürlichkeit nicht sichtbar beziehungsweise hörbar verschlechtern. Zweifelhafte Klick-/Schmatzerkennung wird nur markiert, nicht automatisch repariert.

## 6. OBS-Ereignis-Sidecar und Schutzbereiche

Die primäre Schutzquelle ist ein OBS-Ereignis-Sidecar. Für `name.mp4` lautet der einzige automatisch zugeordnete Dateiname `name.obs-events.json`; es liegt neben der MP4. Die darin gespeicherte Quelldatei-Identität muss mit Dateiname, Größe und SHA-256 der MP4 übereinstimmen. Das verbindliche Schema und ein vollständiges Beispiel stehen im Architekturplan.

Unterstützte Ereignisse sind mindestens:

- Aufnahmebeginn und Aufnahmeende;
- Szenenwechsel;
- Intro-Start und Intro-Ende;
- Outro-Start und Outro-Ende;
- Stinger-Start und Stinger-Ende;
- manuelle Schutzmarker oder Schutzintervalle;
- harte und weiche Schutzklasse;
- Schutzpuffer vor und nach einem Ereignis;
- Kennzeichnung, ob ein Schutzbereich auch Overlays blockiert.

Harter Schutz ist absolut: Ein Autoschnitt darf ihn nicht schneiden, verkürzen oder verschieben. Weicher Schutz kann einen Kandidaten nur dann zulassen, wenn die im Architekturplan definierte hohe Sicherheit erreicht wird; der geschützte Teil selbst bleibt erhalten.

Fehlt das Sidecar, stimmt seine Quelldatei-Identität nicht oder kann es nicht sicher interpretiert werden, sind alle zeitentfernenden Autoschnitte deaktiviert. Die Anwendung darf dann weiterhin analysieren, Review-Artefakte erzeugen, Audio verarbeiten und ausschließlich sicher platzierbare Overlays planen/rendern. Dieser Modus ist im UI und Review deutlich als `no_sidecar_safe_mode` zu kennzeichnen.

Unvollständige Ereignispaare werden konservativ geschlossen: Ein Start ohne Ende schützt bis zum Aufnahmeende; ein Ende ohne Start schützt ab Aufnahmebeginn. Ungültige Zeitwerte, widersprüchliche Aufnahmegrenzen oder eine falsche Quelldatei-Identität machen das Sidecar für Autoschnitte unbrauchbar. Asset-Fingerprinting über Bild oder Audio ist nur als spätere Fallback-Erweiterung vorgesehen.

## 7. EDL und Timeline-Mapping

Transkription, Audioanalyse, Schutzbereiche, Schnittkandidaten und CTA-Erkennung entstehen zunächst auf der Source-Timeline. Erst eine validierte EDL definiert die erhaltenen Quellsegmente. Daraus wird ein explizites Source-to-Output-Mapping aus lückenlosen, halb offenen Intervallen erzeugt.

Jedes erhaltene Segment speichert Quellstart, Quellende, Ausgabestart und Ausgabeende. Ein Quellzeitpunkt innerhalb eines entfernten Intervalls hat keine Ausgabeabbildung. Alle renderbaren Ereignisse, insbesondere CTA-Overlays, müssen über dieses Mapping auf Ausgabe-Frames abgebildet werden. Das Rendern mit ursprünglichen Source-Zeitstempeln ist verboten.

Verbindliche Randfälle:

- Vollständig entfernte CTA-Passage: kein Overlay.
- Teilweise gekürzte CTA-Passage: Overlay nur, wenn Handlungsabsicht und Objekt in den erhaltenen Wörtern weiterhin eindeutig sind; sonst kein Overlay.
- CTA in einem geschützten Bereich: darf erhalten bleiben, aber Overlay-Blockierung des Bereichs ist zu respektieren.
- Overlay an einer Schnittgrenze: in ein ausreichend langes, ununterbrochenes Ausgabesegment verschieben; falls das nicht möglich ist, unterdrücken.
- Direkt aufeinanderfolgende Schnitte: vor Mapping-Erzeugung normalisieren; keine Null- oder Mikrosequenzen erzeugen.
- Audio und Video verwenden dieselben rationalen Segmentgrenzen.
- Bei 60 FPS sind Schnitt- und Eventpositionen ganzzahlige Frames. Schutz wird nach außen, Schnitt wird nach innen gerundet. Die maximal zulässige A/V-Abweichung beträgt einen Frame.

## 8. CTA-Kategorien und Aussageabsicht

Die Kategorien sind getrennt und werden nicht allein durch ein Stichwort ausgelöst:

| Kategorie | Asset | Positive Absicht | Wichtige Ausschlüsse |
|---|---|---|---|
| Like | `like.webm` | Zuschauer soll liken/Daumen geben | bloße Verwendung von „like“ als englisches Füllwort |
| Abo | `abo.webm` | Kanal abonnieren | Abo eines fremden Dienstes |
| Glocke | `glocke.webm` | Benachrichtigungsglocke aktivieren | reale Glocke/Marktglocke |
| Kommentar | `kommentar.webm` | Zuschauer soll kommentieren | Kommentar über fremde Kommentare ohne Aufforderung |
| Kanalmitglied | `kanalmitglied.webm` | bezahlte/ausdrückliche Kanalmitgliedschaft | neutrale „Mitglieder des Marktes“, allgemeine Community-Mitglieder |
| Community | `community.webm` | TruthPill-Community oder Discord beitreten | Kanalmitgliedschaft, neutrale Gruppenbeschreibung |
| Hyperliquid | `hyperliquid.webm` | Hyperliquid nutzen/aufrufen | rein analytische Erwähnung ohne Handlungsabsicht |
| Referral | `referral.webm` | Referral-Link oder Code nutzen | bloße Erwähnung eines Links ohne Empfehlung |
| Website | `website.webm` | Website besuchen | Quellenangabe ohne Zuschaueraufforderung |
| Numerologie | `numerologie.webm` | Numerologie-Angebot ansehen/nutzen | inhaltliche Erwähnung ohne CTA |

„Werde Kanalmitglied“, „unterstütze den Kanal als Mitglied“ und „klick auf Mitglied werden“ gehören ausschließlich zu Kanalmitglied. „Komm in die TruthPill Community“, „den Discord findest du unten“ und „tritt unserer Community bei“ gehören zu Community. „Viele Mitglieder des Marktes erwarten …“ löst nichts aus.

Die Erkennung kombiniert normalisierte Phrase, Handlungsverb, Adressierung des Zuschauers, Kategorieobjekt, Nahkontext, Transkript-Confidence und Negativregeln. Nur hohe Gesamt-Confidence erzeugt automatisch ein Overlay. Mittlere Confidence erscheint als Review-Hinweis ohne Overlay. Bei Kategorie-Konflikten gewinnt die spezifischere Handlungsabsicht: Referral vor Hyperliquid bei Link/Code-Nutzung und Kanalmitglied vor Community bei „Mitglied werden“.

Frequenzlimits und Cooldowns sind zentral konfiguriert, nicht in Einzelregeln verteilt. Der Architekturplan setzt verbindliche v1-Defaults. Die Planung verhindert zeitliche Overlay-Überschneidungen und respektiert globale sowie kategorieweise Limits.

## 9. Notification-Overlays und Sounds

Die zehn CTA-WebMs in `dimensionwithin-overlays-webm.zip` sind Track-Matte-Assets. Originale werden weder entpackt überschrieben noch konvertiert ersetzt. Die Pipeline validiert die ZIP-Mitglieder, trennt Fill und Maske, führt sie über Alpha zusammen und schreibt ausschließlich einen hashadressierten Cache. `lowerthird.webm` und `card.webm` bleiben registrierte, aber nicht automatisch geplante Nicht-CTA-Assets.

Overlay-Positionen sind konfigurierbar. In v1 zulässig sind unten links und oben rechts. Unten rechts ist wegen des Pepe-Avatars eine verbotene Zone. Die konkrete Position wird gegen Framegröße, Safe Margins, Schutzbereiche und andere Overlays geprüft.

Sichtbarkeit und Sound sind unabhängige Entscheidungen. Jedes Asset kann laut Manifest und Konfiguration ohne Sound, mit geprüftem internem Sound oder mit einem explizit registrierten externen Sound laufen. Da kein externes Soundasset im Bestand existiert, wird keines erfunden. Der interne Ton von `glocke.webm` ist ungeprüft und standardmäßig deaktiviert. Vor Freigabe sind Lautheit, True Peak, Länge und Störanteile zu prüfen.

Sounds werden mit eigener Lautstärke, Peak-Schutz und optional leichtem Ducking der Programmaudiospur gemischt. Die Defaultwerte stehen im Architekturplan und sind im Review sichtbar.

## 10. Review, UI und Export

Version 1 rendert automatisiert eine reale finale MP4, sofern Preflight, EDL und Renderprüfung erfolgreich sind. Riskante Entscheidungen bleiben reviewbar. Ein professioneller Timeline-Editor ist nicht erforderlich.

Die lokale anklickbare Oberfläche muss mindestens Import, Sidecarstatus, Preflight, Jobstart/-abbruch, Fortschritt, Warnungen, Ergebnislinks und ein erneutes Rendern mit geänderter Konfiguration ermöglichen. JSON- und statische HTML-Artefakte bilden den ersten Review-Layer und enthalten Schnitte, verworfene Kandidaten, Schutzbereiche, CTA-Entscheidungen, Overlay-/Soundplanung, Timeline-Mapping, Audioverarbeitung, Warnungen und Prüfergebnisse.

Standardexport ist MP4 für YouTube, im Regelfall mit erhaltener Quellauflösung 2560×1440 und 60 FPS. Das Dateinamensmuster lautet `YYYY-MM-DD_title_final.mp4`; Kollisionen werden durch eine neue, deterministische Suffixversion aufgelöst, niemals durch Überschreiben. Originalvideo und -audio bleiben unverändert.

## 11. Nicht Bestandteil von Version 1

- automatische Intro- oder Outro-Einfügung;
- automatische Stinger-Platzierung oder nachträgliche Szenenregie;
- Verschieben, Ersetzen oder Neuordnen OBS-produzierter Bereiche;
- automatische Hook-Erfindung;
- Neuordnung kompletter Inhaltsabschnitte;
- professioneller Timeline-Editor;
- Longform-zu-Shorts und automatische Highlight-/Hook-Extraktion;
- Cloud-APIs;
- allgemeiner Mehrzweck-Videoeditor;
- automatische Kapitel als Kernfunktion;
- Fingerprint-Erkennung als Kernschutz;
- Cursor-/Mausanalyse als Kernschnittsignal.

Longform-zu-Shorts, Fingerprinting, Cursoraktivität, Projektverlauf und fortgeschrittene UI bleiben mögliche spätere Richtungen, ohne v1 zu präjudizieren.

## 12. Verbindlich ersetzte v0.4-Anforderungen

Folgende v0.4-Aussagen sind ausdrücklich verworfen:

- „Intro should be inserted automatically“ wird ersetzt durch: Intro ist bereits in der OBS-MP4 und darf nicht automatisch eingefügt oder verschoben werden.
- „Outro should be inserted automatically“ wird entsprechend ersetzt.
- automatische Stinger-Nutzung zwischen Hook, Intro und Hauptteil wird ausgeschlossen.
- „insert hook → intro → main section → CTA → outro structure“ wird ersetzt durch Erhaltung der von OBS aufgenommenen Reihenfolge; keine Hook-Erfindung oder Abschnittsneuordnung.
- Intro-/Outro-Einfügung als automatische v1-Entscheidung entfällt.
- das Fehlen eines separaten Outro-Assets braucht keinen Postproduktions-Fallback, weil v1 kein Outro einfügt.
- Kanalmitglied und Community sind nicht austauschbar, sondern zwei getrennte CTA-Kategorien und Assets.
- der in v0.4 genannte Repo-Pfad `D:\legacy\matrix-auto-cutter` wird durch den tatsächlich geprüften Root `D:\workspace\matrix-auto-cutter` ersetzt.
- die in v0.4 genannten externen Intro-, Outro- und Design-Assetordner sind keine v1-Laufzeitabhängigkeit. Kanonischer Postproduktionsbestand ist ausschließlich das relative, hashgeprüfte Manifest v0.5; OBS darf seine Produktionsassets unabhängig verwalten.

Alle übrigen v0.4-Ziele gelten nur, soweit sie nicht dieser v0.5-Spezifikation widersprechen.

## 13. Überprüfbare Produkt-Akzeptanzkriterien

1. Ein Projektimport verändert SHA-256, Größe und Änderungszeit der Quell-MP4 sowie aller Originalassets nicht.
2. Für `aufnahme.mp4` wird ausschließlich `aufnahme.obs-events.json` automatisch zugeordnet; eine Identitätsabweichung aktiviert sichtbar `no_sidecar_safe_mode`.
3. Ohne gültiges Sidecar enthält die finale EDL keine zeitentfernenden Auto-Edits.
4. Kein akzeptierter Schnitt überschneidet einen gepufferten harten Schutzbereich; weiche Schutzkonflikte werden nachweisbar verworfen oder auf ungeschützte Frames gekürzt.
5. Intro-, Outro-, Szenenwechsel- und Stinger-Ereignisse werden in Testfixtures vollständig geschützt und weder verschoben noch eingefügt.
6. Jede EDL erzeugt ein lückenloses, monotones Source-to-Output-Mapping; entfernte Quellframes besitzen keine Ausgabeabbildung.
7. Alle Overlay-Startzeiten sind Output-Frames aus dem Mapping. Ein Test mit zwei aufeinanderfolgenden Schnitten beweist, dass keine Source-Zeit direkt gerendert wird.
8. Eine vollständig entfernte CTA erzeugt kein Overlay; eine teilweise entfernte CTA nur dann, wenn erhaltene Wörter weiterhin Handlungsverb und Zielobjekt tragen.
9. Die Beispielsätze für Kanalmitglied und Community werden der richtigen getrennten Kategorie zugeordnet; „Viele Mitglieder des Marktes erwarten …“ erzeugt kein CTA.
10. Die zehn CTA-Assets werden aus `dimensionwithin-overlays-webm.zip` validiert und in einen von den Originalen getrennten Alpha-Cache überführt; `lowerthird.webm`, `card.webm`, Intro und Stinger werden in v1 nicht automatisch geplant.
11. Kein Overlay liegt in der konfigurierten Bottom-right-Schutzzone, überlappt ein anderes Overlay oder überschreitet zentrale Frequenzlimits.
12. `glocke.webm` wird im Defaultprofil ohne interne Audiospur gerendert. Jeder aktivierte Notification-Sound hält den konfigurierten True-Peak-Grenzwert ein.
13. Ein Standardtestexport ist 2560×1440 bei 60 FPS; Audio-/Videosynchronität weicht an Anfang, Schnittgrenzen und Ende höchstens einen Frame ab.
14. Die Audioausgabe erreicht im Standardprofil −14 LUFS integrated mit ±0,5 LU Toleranz und maximal −1,5 dBTP, sofern das Eingangsmaterial eine valide Audiospur besitzt.
15. Jeder erfolgreiche Job erzeugt finale MP4, EDL, Mapping, Review-JSON und eigenständiges Review-HTML; ein abgebrochener/fehlgeschlagener Job veröffentlicht keine partielle Datei als final.
16. Transkriptfixtures mit Deutsch-/Englisch-Mischsprache enthalten monotone Wortzeitstempel und Confidence; unsichere Wörter können keinen riskanten Autoschnitt allein begründen.
17. Stilistische Wiederholungen und geschützte Denk-/Chartpausen bleiben in den festgelegten Regressionstests erhalten.

## 14. Produktfragen und technische Entscheidungen

Für den Scope Freeze bestehen keine blockierenden offenen Produktfragen. Neue kreative Wünsche, zusätzliche CTA-Kategorien oder geänderte Automatisierungsgrenzen sind spätere Produktänderungen und benötigen eine neue Spezifikationsversion.

Programmiersprache, UI-Stack, Transkriptionsengine, Schwellenwerte, Datenmodelle, Cacheformat, FFmpeg-Filtergraph, Fehlerstrategie, Tests und Phasenabfolge sind im `matrix-auto-cutter-architecture-plan-v0.1.md` entschieden und werden nicht als Produktfragen an Joshua zurückgegeben.
