r"""Stufe 2, Teil 3: die Urteilsseite, ausgeliefert über einen lokalen Server.

Muster übernommen von ``review_app`` (gelesen, nicht kopiert): Standard-
bibliothek-``http.server``, Bindung an ``127.0.0.1``, freier Port, eine
Einzelinstanz-Sperrdatei (eigener Name, siehe
:mod:`matrix_auto_cutter.shorts.judge_server`). Kein Framework, kein CDN,
keine externe Schriftart. Der Domänenkern ist ein anderer als bei
``repeat.review`` - dort Wiederholungspaare, hier einzelne Zeitspannen mit
Sicherheitsstufe und Verschachtelung (``enthaelt``).

**Entscheidung Video (Auftrag 20 Abschnitt 3.2 → Auftrag 21 Punkt 1 →
Auftrag 22):** Auftrag 21 prüfte ein `<video src="file://...">` und scheiterte
zweifach reproduziert an Chromes ``MEDIA_ELEMENT_ERROR: URL safety check`` -
eine ``file://``-Seite darf keine andere ``file://``-Datei laden, auch nicht
auf demselben Laufwerk. Über ``http://127.0.0.1`` gibt es diese Prüfung
nicht (dasselbe Prinzip, das ``review_app`` für seine HTML-Review-Brücke
schon nutzt). Der Ton-Ausschnitt entfällt damit - er war der Rückfall dafür,
den es nicht mehr braucht; die Videofunktionen dazu liegen in
:mod:`matrix_auto_cutter.shorts.judge_server`.

**Gruppen statt flacher Liste (Auftrag urteilsseite-gruppiert).** Liegt
``buendel.json`` im Auftragsordner und besteht sie ``auswahl.pruefe_buendel``,
zeigt die Seite die Kandidaten in Themengruppen: je Gruppe der empfohlene
Kandidat vorn, die uebrigen Fassungen eingeklappt dahinter. Der Anlass ist
belegt - am 26.8. hat der Nutzer in der flachen Liste drei Paare angenommen,
die dasselbe Material zeigen (64/10, 67/18, 66/16). Fehlt die Datei oder
meldet die Pruefung auch nur eine Abweichung, faellt die Seite auf die flache
Liste zurueck und sagt das im Kopf; aeltere Aufnahmen haben keine Buendelung,
und das ist kein Fehlschlag.

**Die Urteilsdatei bleibt kandidatenbezogen.** Eine Gruppenentscheidung
erzeugt mehrere Urteile, kein Gruppenurteil: je Kandidat eines, mit denselben
sieben Feldern wie bisher (``index``, ``titel``, ``start_ms``, ``end_ms``,
``ist_kind``, ``urteil``, ``notiz``). ``buendel.json`` ist ANZEIGE, nicht
Wahrheit - geht sie verloren oder wird sie neu gerechnet, sind die Urteile
vollstaendig und gueltig, denn sie haengen am ``index`` des Kandidaten und an
sonst nichts. Ein Gruppenurteil haenge dagegen an einer Gruppennummer, die
eine zweite Buendelung anders vergeben darf; damit waere Urteilszeit
vernichtet - das einzige Artefakt dieser Kette, das sich nicht neu erzeugen
laesst.
"""

from __future__ import annotations

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from matrix_auto_cutter.shorts.candidates import Candidate

# ---------------------------------------------------------------------------
# Transkripttext je Kandidat
#
# Auftrag 21, Punkt 2: der bisherige segmentweise Zuschnitt zeigte den Text
# des ganzen ueberlappenden Segments, auch wenn der Kandidat mitten darin
# endet - das las sich wie ein Themenwechsel, den es nicht gab (Beleg: #3
# endete im Ausdruck mit dem Anfang von #4). Wortgenauer Zuschnitt behebt
# das, gestuetzt auf die Wortzeitstempel aus dem rohen whisper-cli-Export
# (``-ojf``, ``transkript-rendered.wav.json``). Fehlt diese Datei, faellt
# der Bau auf die alte Segmentanzeige zurueck, aber sichtbar als ungenau
# markiert - "wenigstens am Segment abschneiden und den Rest sichtbar
# abtrennen" (Auftrag 21, Abschnitt 3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """Ein zu einem ganzen Wort zusammengefuegtes Token samt Zeitspanne."""

    text: str
    start_ms: int
    end_ms: int


def _merge_tokens_to_words(tokens: Sequence[Mapping[str, object]]) -> list[TranscriptWord]:
    """Fuege BPE-Teiltoken zu ganzen Woertern zusammen.

    whisper-cli's ``-ojf``-Ausgabe liefert Teiltoken, nicht Woerter - ein
    Token mit fuehrendem Leerzeichen beginnt ein neues Wort, alle folgenden
    Token ohne fuehrendes Leerzeichen gehoeren noch dazu (z. B. ``" L"`` +
    ``"ass"`` fuer "Lass"). Ohne diese Zusammenfuehrung reisst ein
    wortgenauer Zuschnitt mitten im Wort ab, statt sauber davor zu enden.
    """
    words: list[TranscriptWord] = []
    current_text = ""
    current_start: int | None = None
    current_end = 0
    for token in tokens:
        text = str(token["text"])
        if text.startswith("[_") or text == "":
            continue
        offsets = token["offsets"]
        start_ms = int(str(offsets["from"]))  # type: ignore[index]
        end_ms = int(str(offsets["to"]))  # type: ignore[index]
        starts_new_word = current_start is None or text.startswith(" ")
        if starts_new_word and current_start is not None:
            words.append(TranscriptWord(current_text, current_start, current_end))
            current_text = ""
            current_start = None
        if current_start is None:
            current_start = start_ms
        current_end = end_ms
        current_text += text
    if current_start is not None:
        words.append(TranscriptWord(current_text, current_start, current_end))
    return words


def load_transcript_words(path: Path) -> list[TranscriptWord]:
    """Lies Wortzeitstempel aus dem rohen whisper-cli-Export; fehlt er, leere Liste."""
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    tokens: list[Mapping[str, object]] = []
    for segment in payload.get("transcription", []):
        tokens.extend(segment.get("tokens", []))
    return _merge_tokens_to_words(tokens)


def candidate_transcript_text_from_words(
    words: Sequence[TranscriptWord], start_ms: int, end_ms: int
) -> str:
    """Verkette nur die Woerter, die vollständig innerhalb des Kandidaten liegen.

    Volle Enthaltenheit (nicht bloße Überlappung) ist bewusst strenger als
    die alte Segmentanzeige: ein Wort, das über die Grenze hinausragt, wird
    weggelassen statt angeschnitten - lieber ein Wort zu wenig als der
    Anfang des nächsten Kandidaten (Auftrag 21, Punkt 2).
    """
    parts = [word.text for word in words if word.start_ms >= start_ms and word.end_ms <= end_ms]
    return "".join(parts).strip()


def candidate_transcript_text_from_segments(
    segments: Sequence[Mapping[str, object]], start_ms: int, end_ms: int
) -> str:
    """Verkette den Text aller Transkriptsegmente, die den Kandidaten überlappen.

    Rückfall, wenn keine Wortzeitstempel vorliegen - gröber als
    :func:`candidate_transcript_text_from_words`, weil ganze Segmente auch
    dann gezeigt werden, wenn sie über die Kandidatengrenze hinausragen.
    """
    parts: list[str] = []
    for segment in segments:
        seg_start = int(str(segment["start_ms"]))
        seg_end = int(str(segment["end_ms"]))
        if seg_end > start_ms and seg_start < end_ms:
            text = str(segment["text"]).strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def candidate_transcript(
    *,
    words: Sequence[TranscriptWord],
    segments: Sequence[Mapping[str, object]],
    start_ms: int,
    end_ms: int,
) -> tuple[str, bool]:
    """Baue den Transkriptausschnitt eines Kandidaten; liefert ``(text, wortgenau)``.

    Bevorzugt Wortzeitstempel (präzise, siehe oben). Ohne sie - oder wenn im
    Zeitraum kein einziges vollständig enthaltenes Wort liegt - fällt der
    Bau auf die Segmentanzeige zurück und markiert das Ergebnis als nicht
    wortgenau, damit die Seite es sichtbar von einem präzisen Zuschnitt
    abtrennt (Auftrag 21, Abschnitt 3: "den Rest sichtbar abtrennen").
    """
    if words:
        text = candidate_transcript_text_from_words(words, start_ms, end_ms)
        if text:
            return text, True
    return candidate_transcript_text_from_segments(segments, start_ms, end_ms), False


def load_transcript_segments(path: Path) -> list[dict[str, object]]:
    """Lies die Segmentliste eines ``transkript*.json``; fehlt sie, gib eine leere Liste."""
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = payload.get("segments", []) if isinstance(payload, dict) else []
    return [segment for segment in segments if isinstance(segment, dict)]


# ---------------------------------------------------------------------------
# Reihenfolge: nach der besten Sicherheit einer Gruppe (nicht der der
# äußeren Fassung), Container unmittelbar vor den Kandidaten, die er laut
# ``enthaelt`` vollständig umfasst - kurze und lange Fassung müssen
# nebeneinander sichtbar sein (Auftrag 20, Abschnitt 3.2; Auftrag 21,
# Punkt 3: eine Gruppe mit einer HOCH-Fassung darin gehört in den
# HOCH-Abschnitt, auch wenn die äußere Fassung nur MITTEL ist).
# ---------------------------------------------------------------------------


def _clusters(candidates: Sequence[Candidate]) -> list[list[Candidate]]:
    """Baue Cluster: ``[Wurzel, *enthaltene Kandidaten in Startzeit-Reihenfolge]``."""
    contained: set[int] = set()
    for candidate in candidates:
        contained.update(candidate.enthaelt)
    by_index = {candidate.index: candidate for candidate in candidates}
    roots = [candidate for candidate in candidates if candidate.index not in contained]
    clusters: list[list[Candidate]] = []
    for root in roots:
        children = sorted(
            (by_index[ref] for ref in root.enthaelt if ref in by_index),
            key=lambda candidate: candidate.start_ms,
        )
        clusters.append([root, *children])
    return clusters


def order_candidates(candidates: Sequence[Candidate]) -> list[tuple[Candidate, bool]]:
    """Sortiere Cluster nach ihrer besten (niedrigsten) Sicherheitsstufe, dann Startzeit.

    Gibt Paare ``(Kandidat, ist_kind)`` zurück. Ein Kandidat ohne Container
    ist selbst die Wurzel seines (einelementigen) Clusters. Ist ein Kandidat
    in mehreren ``enthaelt``-Listen genannt, erscheint er unter jedem
    Container erneut - das ist im vorliegenden Material nicht beobachtet,
    wird hier aber nicht als Fehler behandelt.
    """
    clusters = _clusters(candidates)
    clusters.sort(
        key=lambda cluster: (
            min(candidate.sicherheit_rank for candidate in cluster),
            cluster[0].start_ms,
        )
    )
    ordered: list[tuple[Candidate, bool]] = []
    for cluster in clusters:
        ordered.append((cluster[0], False))
        ordered.extend((candidate, True) for candidate in cluster[1:])
    return ordered


def cluster_map(candidates: Sequence[Candidate]) -> dict[int, tuple[Candidate, ...]]:
    """Bilde jeden Kandidatenindex auf seine vollständige Gruppe ab (Länge 1, wenn keine)."""
    mapping: dict[int, tuple[Candidate, ...]] = {}
    for cluster in _clusters(candidates):
        members = tuple(cluster)
        for candidate in cluster:
            mapping[candidate.index] = members
    return mapping


# ---------------------------------------------------------------------------
# HTML-Seite
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClusterMember:
    """Eine Fassung innerhalb einer Verschachtelungsgruppe, fuer den Direktvergleich."""

    index: int
    titel: str
    start_ms: int
    end_ms: int
    is_self: bool


@dataclass(frozen=True, slots=True)
class JudgeEntry:
    """Eine Karte der Urteilsseite: ein Kandidat samt Video, Text und Verschachtelungsstatus."""

    index: int
    titel: str
    begruendung: str
    sicherheit: str
    start_ms: int
    end_ms: int
    is_child: bool
    transcript_text: str
    transcript_precise: bool
    cluster: tuple[ClusterMember, ...]


def _cluster_members(cluster: Sequence[Candidate], self_index: int) -> tuple[ClusterMember, ...]:
    if len(cluster) < 2:
        return ()
    return tuple(
        ClusterMember(
            index=member.index,
            titel=member.titel,
            start_ms=member.start_ms,
            end_ms=member.end_ms,
            is_self=member.index == self_index,
        )
        for member in cluster
    )


def build_judge_entries(
    candidates: Sequence[Candidate],
    *,
    transcript_segments: Sequence[Mapping[str, object]],
    transcript_words: Sequence[TranscriptWord],
) -> list[JudgeEntry]:
    """Baue die geordnete Kartenliste: ein Transkriptausschnitt je Kandidat, kein ffmpeg-Lauf mehr.

    Das Video wird nicht mehr je Kandidat geschnitten - der Server liefert
    die eine gerenderte Datei mit Bereichsanfragen aus, die Seite springt per
    ``#t=start,ende`` an die Kandidatengrenze (:mod:`judge_server`).
    """
    clusters = cluster_map(candidates)
    entries: list[JudgeEntry] = []
    for candidate, is_child in order_candidates(candidates):
        transcript_text, transcript_precise = candidate_transcript(
            words=transcript_words,
            segments=transcript_segments,
            start_ms=candidate.start_ms,
            end_ms=candidate.end_ms,
        )
        entries.append(
            JudgeEntry(
                index=candidate.index,
                titel=candidate.titel,
                begruendung=candidate.begruendung,
                sicherheit=candidate.sicherheit,
                start_ms=candidate.start_ms,
                end_ms=candidate.end_ms,
                is_child=is_child,
                transcript_text=transcript_text,
                transcript_precise=transcript_precise,
                cluster=_cluster_members(clusters[candidate.index], candidate.index),
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Buendelung: die Gruppen aus ``buendel.json`` fuer die Anzeige
#
# Warum die Urteilsdatei trotzdem kandidatenbezogen bleibt (Auftrag
# urteilsseite-gruppiert, Teil 4): ``buendel.json`` ist ANZEIGE, nicht
# Wahrheit. Eine Gruppenentscheidung erzeugt mehrere Urteile, kein
# Gruppenurteil - je Kandidat eines, mit denselben sieben Feldern wie bisher.
# Geht die Buendelung verloren oder wird sie neu gerechnet, sind die Urteile
# vollstaendig und gueltig: sie haengen am ``index`` des Kandidaten und an
# sonst nichts. Ein Gruppenurteil waere dagegen an eine Gruppennummer
# gebunden, die eine zweite Buendelung anders vergeben darf - und damit
# waere Urteilszeit vernichtet, das einzige Artefakt dieser Kette, das sich
# nicht neu erzeugen laesst.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuendelGruppe:
    """Eine Themengruppe der Urteilsseite: ein Empfohlener, die uebrigen dahinter."""

    nummer: int
    projekt: str
    thema: str
    empfohlen: int
    begruendung: str
    indizes: tuple[int, ...]

    @property
    def weitere(self) -> tuple[int, ...]:
        """Die Indizes hinter dem Empfohlenen - leer bei einer Einzelgruppe."""
        return tuple(index for index in self.indizes if index != self.empfohlen)


def _ganzzahl(wert: object) -> int | None:
    """Der Wert als Ganzzahl - oder ``None``, wenn er keine ist (``True`` ist keine)."""
    return wert if isinstance(wert, int) and not isinstance(wert, bool) else None


def baue_gruppen(buendel: Sequence[Mapping[str, object]]) -> list[BuendelGruppe]:
    """Fasse die Eintraege aus ``buendel.json`` zu Gruppen in Nummernfolge zusammen.

    Rein rechnend, ohne Datei und ohne Pruefung: geprueft hat vorher
    ``auswahl.pruefe_buendel``, und nur eine Buendelung ohne Meldung kommt
    ueberhaupt bis hierher (:func:`judge_server.lade_buendel_gruppen`).
    Innerhalb der Gruppe wird nach ``rang`` sortiert, die Gruppen selbst nach
    ``gruppe`` - der Lauf vergibt die Nummern bereits nach ``projekt``
    sortiert und darin chronologisch, hier wird nichts umsortiert.

    ``projekt``, ``thema`` und ``begruendung`` der Ueberschrift stammen vom
    EMPFOHLENEN Eintrag: er ist der, den der Nutzer zuerst sieht, und seine
    ``begruendung`` sagt, warum gerade er vorgeschlagen wird.
    """
    nach_gruppe: dict[int, list[Mapping[str, object]]] = {}
    for eintrag in buendel:
        nummer = _ganzzahl(eintrag.get("gruppe"))
        index = _ganzzahl(eintrag.get("index"))
        if nummer is None or index is None:
            continue
        nach_gruppe.setdefault(nummer, []).append(eintrag)
    gruppen: list[BuendelGruppe] = []
    for nummer in sorted(nach_gruppe):
        eintraege = sorted(nach_gruppe[nummer], key=lambda e: (_ganzzahl(e.get("rang")) or 0))
        empfohlen_eintrag = next(
            (e for e in eintraege if e.get("empfohlen") is True), eintraege[0]
        )
        empfohlen = _ganzzahl(empfohlen_eintrag.get("index"))
        assert empfohlen is not None
        indizes = [empfohlen]
        indizes.extend(
            index
            for e in eintraege
            if (index := _ganzzahl(e.get("index"))) is not None and index != empfohlen
        )
        gruppen.append(
            BuendelGruppe(
                nummer=nummer,
                projekt=str(empfohlen_eintrag.get("projekt", "")),
                thema=str(empfohlen_eintrag.get("thema", "")),
                empfohlen=empfohlen,
                begruendung=str(empfohlen_eintrag.get("begruendung", "")),
                indizes=tuple(indizes),
            )
        )
    return gruppen


def _gruppe_to_js_dict(gruppe: BuendelGruppe) -> dict[str, object]:
    return {
        "nummer": gruppe.nummer,
        "projekt": html.escape(gruppe.projekt),
        "thema": html.escape(gruppe.thema),
        "empfohlen": gruppe.empfohlen,
        "begruendung": html.escape(gruppe.begruendung),
        "indizes": list(gruppe.indizes),
    }


def _entry_to_js_dict(entry: JudgeEntry) -> dict[str, object]:
    return {
        "index": entry.index,
        "titel": html.escape(entry.titel),
        "begruendung": html.escape(entry.begruendung),
        "sicherheit": entry.sicherheit,
        "start_ms": entry.start_ms,
        "end_ms": entry.end_ms,
        "is_child": entry.is_child,
        "transcript_text": html.escape(entry.transcript_text),
        "transcript_precise": entry.transcript_precise,
        "cluster": [
            {
                "index": member.index,
                "titel": html.escape(member.titel),
                "start_ms": member.start_ms,
                "end_ms": member.end_ms,
                "is_self": member.is_self,
            }
            for member in entry.cluster
        ],
    }


_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Shorts-Urteilsseite</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff;
    --fg: #1a1a1a;
    --muted: #666;
    --card-bg: #f6f6f8;
    --child-bg: #eceef2;
    --border: #d8d8dc;
    --accent: #2b6cb0;
    --hoch: #2f7d3c;
    --mittel: #b5851a;
    --niedrig: #b5541a;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16161a;
      --fg: #eaeaea;
      --muted: #9a9aa2;
      --card-bg: #202024;
      --child-bg: #26262c;
      --border: #34343a;
      --accent: #6ea8e0;
      --hoch: #6cc37c;
      --mittel: #d9ab4d;
      --niedrig: #d97a4d;
    }
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    margin: 0;
    padding: 0 0 4rem 0;
  }
  header {
    position: sticky;
    top: 0;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    padding: 0.75rem 1rem;
    z-index: 10;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  #progress { font-weight: 600; }
  main {
    max-width: 860px;
    margin: 1rem auto;
    padding: 0 1rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  details#kriterien {
    max-width: 860px;
    margin: 1rem auto;
    padding: 0 1rem;
  }
  details#kriterien pre {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1rem;
    overflow-x: auto;
    font-size: 0.8rem;
    white-space: pre-wrap;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.25rem;
  }
  .card.child {
    background: var(--child-bg);
    margin-left: 2rem;
    border-style: dashed;
  }
  .card.judged { border-color: var(--accent); border-style: solid; }
  .card.active-session { box-shadow: 0 0 0 3px var(--accent); }
  .card-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.25rem 1rem;
    margin-bottom: 0.4rem;
  }
  .card-head h3 { margin: 0; font-size: 1.05rem; }
  .badge {
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 0.1rem 0.5rem;
    border-radius: 999px;
    border: 1px solid currentColor;
  }
  .badge.hoch { color: var(--hoch); }
  .badge.mittel { color: var(--mittel); }
  .badge.niedrig { color: var(--niedrig); }
  .meta { font-size: 0.85rem; color: var(--muted); margin-bottom: 0.5rem; }
  .child-tag {
    font-size: 0.75rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }
  video { width: 100%; margin: 0.5rem 0; border-radius: 6px; background: #000; }
  .begruendung { margin: 0.4rem 0; }
  .transcript {
    margin: 0.5rem 0;
    font-style: italic;
    color: var(--muted);
  }
  .transcript .label {
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    font-style: normal;
  }
  .buttons { display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }
  button {
    border: 1px solid var(--border);
    background: var(--bg);
    color: var(--fg);
    border-radius: 6px;
    padding: 0.4rem 0.9rem;
    cursor: pointer;
    font-size: 0.9rem;
  }
  button:hover { border-color: var(--accent); }
  button.active {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }
  input[type=text] {
    width: 100%;
    margin-top: 0.5rem;
    padding: 0.4rem 0.6rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg);
    color: var(--fg);
  }
  #download-bar {
    max-width: 860px;
    margin: 1rem auto 0 auto;
    padding: 0 1rem;
  }
  #download-btn { padding: 0.6rem 1.2rem; font-weight: 600; }
  .hint { font-size: 0.8rem; color: var(--muted); }
  #sicherheit-legende {
    max-width: 860px;
    margin: 1rem auto 0 auto;
    padding: 0 1rem;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .transcript-warning {
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--niedrig);
    margin-left: 0.5rem;
  }
  .vergleich {
    margin: 0.5rem 0 0.75rem 0;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
  }
  .vergleich table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.85rem;
  }
  .vergleich th, .vergleich td {
    text-align: left;
    padding: 0.35rem 0.6rem;
    white-space: nowrap;
  }
  .vergleich thead tr { border-bottom: 1px solid var(--border); }
  .vergleich tr.self { background: var(--child-bg); font-weight: 600; }
  #autoplay-hint {
    position: sticky;
    top: 3rem;
    z-index: 9;
    background: var(--accent);
    color: white;
    text-align: center;
    font-weight: 600;
    padding: 0.5rem 1rem;
  }
  /* Fest im Bild, nicht am Seitenanfang: nach dem letzten Urteil steht der
     Blick unten bei der zuletzt beurteilten Karte - eine Meldung im
     Seitenfluss stand dort gemessen 17.361 px ueber dem Sichtfeld
     (Auftrag 25, Fehler B). "fixed" haelt sie im Bild und fuegt oben keine
     Hoehe ein, die den Rest der Seite verschiebt. */
  #session-status {
    position: fixed;
    left: 50%;
    bottom: 1.25rem;
    transform: translateX(-50%);
    z-index: 20;
    max-width: min(860px, calc(100vw - 2rem));
    padding: 0.75rem 1.25rem;
    border: 1px solid var(--accent);
    border-radius: 8px;
    background: var(--card-bg);
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.35);
    font-weight: 600;
    text-align: center;
  }
  #session-status-pfad {
    margin-top: 0.35rem;
    font-weight: 400;
    font-size: 0.8rem;
    color: var(--muted);
    word-break: break-all;
  }
  #session-status-close { margin-top: 0.6rem; }
  /* Gruppen (Auftrag urteilsseite-gruppiert) */
  #rueckfall-hinweis {
    max-width: 860px;
    margin: 1rem auto 0 auto;
    padding: 0.6rem 1rem;
    font-size: 0.85rem;
    color: var(--niedrig);
    border: 1px dashed var(--niedrig);
    border-radius: 8px;
  }
  .gruppe {
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.75rem 0.75rem 1rem 0.75rem;
    background: transparent;
  }
  .gruppe.entschieden { border-color: var(--accent); }
  .gruppe-kopf { margin: 0 0.5rem 0.5rem 0.5rem; }
  .gruppe-kopf h2 {
    margin: 0;
    font-size: 1rem;
    letter-spacing: 0.01em;
  }
  .gruppe-nummer {
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
  }
  .gruppe-thema { font-size: 0.9rem; color: var(--muted); margin-top: 0.15rem; }
  .empfehlung {
    margin: 0.5rem 0 0.25rem 0;
    padding: 0.5rem 0.75rem;
    border-left: 3px solid var(--accent);
    background: var(--child-bg);
    border-radius: 0 6px 6px 0;
    font-size: 0.9rem;
  }
  .empfehlung .label {
    display: block;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin-bottom: 0.2rem;
  }
  details.weitere { margin-top: 0.75rem; }
  details.weitere > summary {
    cursor: pointer;
    font-size: 0.85rem;
    color: var(--muted);
    padding: 0.35rem 0.5rem;
  }
  details.weitere > summary:hover { color: var(--accent); }
  details.weitere .weitere-liste {
    margin: 0.1rem 0 0.6rem 1.4rem;
    font-size: 0.8rem;
    color: var(--muted);
  }
  details.weitere .kandidaten {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  button.fassung-nehmen {
    border-color: var(--accent);
    color: var(--accent);
    font-weight: 600;
  }
  button.fassung-nehmen:hover { background: var(--accent); color: white; }
</style>
</head>
<body>
<header>
  <div id="progress">0 von 0 beurteilt</div>
  <div class="hint">Tasten: 1 Ja &middot; 2 Nein &middot; 3 Sp&auml;ter &middot;
  Leertaste Pause/Weiter</div>
</header>
<div id="autoplay-hint" hidden>Zum Starten Leertaste dr&uuml;cken.</div>
<div id="session-status" hidden>
  <div id="session-status-text"></div>
  __URTEILE_PFAD_HTML__
  <button id="session-status-close">Schlie&szlig;en</button>
</div>
__RUECKFALL_HTML__
<p id="sicherheit-legende">
  <strong>Sicherheit (hoch/mittel/niedrig):</strong> die Selbsteinschätzung
  des zerlegenden Sprachmodells aus <code>kandidaten.json</code> - kein
  gemessener Wert, keine Prüfung durch diese Seite.
</p>
<details id="kriterien">__KRITERIEN_HTML__</details>
<main id="cards"></main>
<div id="download-bar">
  <button id="download-btn">Urteile herunterladen</button>
</div>

<script>
const ENTRIES = __ENTRIES_JSON__;
// Leer heisst: keine gueltige buendel.json - die Seite bleibt flach.
const GRUPPEN = __GRUPPEN_JSON__;

const state = ENTRIES.map(() => ({ urteil: null, notiz: "" }));
const saveTimers = {};

// Kandidatenindex -> Position in ENTRIES. Karten, Zustand und Video-Element
// haengen an der POSITION, buendel.json redet dagegen vom INDEX; ohne diese
// Bruecke zeigte jede Gruppe auf die falsche Karte.
const posByIndex = {};
ENTRIES.forEach((entry, i) => { posByIndex[entry.index] = i; });

function gruppenModus() { return GRUPPEN.length > 0; }

// Gefuehrte Sitzung (Auftrag 24): eine Warteschlange offener Kandidaten in
// Anzeige-Reihenfolge. "Spaeter" reiht einmal ans Ende ein - deferCount haelt
// das je Kandidat fest, nur fuer die Dauer dieser Sitzung (kein Serverfeld).
let sessionQueue = [];
let sessionActive = null;
const deferCount = ENTRIES.map(() => 0);
let autoplayBlocked = false;

function buildInitialQueue() {
  if (gruppenModus()) {
    // Im Gruppenmodus fuehrt die Sitzung nur ueber die EMPFOHLENEN
    // Kandidaten offener Gruppen, in Gruppenreihenfolge. Die uebrigen
    // Fassungen stehen eingeklappt: sie in die Warteschlange zu nehmen
    // hiesse, zu einer unsichtbaren Karte zu scrollen und ein Video
    // abzuspielen, das niemand sieht. Von Hand bedienbar bleiben sie
    // vollstaendig - aufgeklappt wie jede andere Karte.
    return GRUPPEN
      .filter((g) => !gruppeEntschieden(g))
      .map((g) => posByIndex[g.empfohlen])
      .filter((i) => i !== undefined);
  }
  return ENTRIES
    .map((_entry, i) => i)
    .filter((i) => state[i].urteil === null || state[i].urteil === "spaeter");
}

function gruppeEntschieden(gruppe) {
  return gruppe.indizes.some((index) => {
    const pos = posByIndex[index];
    return pos !== undefined && state[pos].urteil !== null;
  });
}

function showAutoplayHint() {
  autoplayBlocked = true;
  const hint = document.getElementById("autoplay-hint");
  if (hint) hint.hidden = false;
}

function hideAutoplayHint() {
  autoplayBlocked = false;
  const hint = document.getElementById("autoplay-hint");
  if (hint) hint.hidden = true;
}

function playActive() {
  if (sessionActive === null) return;
  const video = document.getElementById("video-" + sessionActive);
  if (!video) return;
  const playPromise = video.play();
  if (playPromise && typeof playPromise.catch === "function") {
    playPromise.catch(() => showAutoplayHint());
  }
}

function showSessionEndMessage() {
  const decided = state.filter((s) => s.urteil === "ja" || s.urteil === "nein").length;
  const total = ENTRIES.length;
  const open = total - decided;
  const el = document.getElementById("session-status");
  if (!el) return;
  el.hidden = false;
  document.getElementById("session-status-text").textContent = open === 0
    ? "Alle " + total + " beurteilt."
    : decided + " entschieden, " + open + " offen geblieben.";
}

function advanceSession() {
  if (sessionActive !== null) {
    const prevVideo = document.getElementById("video-" + sessionActive);
    if (prevVideo) prevVideo.pause();
    const prevCard = document.getElementById("card-" + sessionActive);
    if (prevCard) prevCard.classList.remove("active-session");
  }
  if (sessionQueue.length === 0) {
    sessionActive = null;
    showSessionEndMessage();
    return;
  }
  sessionActive = sessionQueue.shift();
  const card = document.getElementById("card-" + sessionActive);
  if (card) {
    card.classList.add("active-session");
    card.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  playActive();
}

function startSession() {
  sessionQueue = buildInitialQueue();
  advanceSession();
}

function applyUrteil(index, urteil) {
  state[index].urteil = urteil;
  renderCardButtons(index);
  updateProgress();
  saveUrteil(index);
}

function handleSessionUrteil(urteil) {
  if (sessionActive === null) return;
  const index = sessionActive;
  if (urteil === "spaeter" && deferCount[index] === 0) {
    deferCount[index] = 1;
    applyUrteil(index, "spaeter");
    sessionQueue.push(index);
  } else {
    applyUrteil(index, urteil);
  }
  advanceSession();
}

function fmtHms(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return pad(h) + ":" + pad(m) + ":" + pad(s);
}

function updateProgress() {
  const judged = state.filter((s) => s.urteil !== null).length;
  if (gruppenModus()) {
    // Im Gruppenmodus zaehlt die Gruppe, nicht der Kandidat: der Nutzer
    // faellt je Gruppe EINE Entscheidung, und "47 von 69 beurteilt" saehe
    // nach Rueckstand aus, wo keiner ist.
    const entschieden = GRUPPEN.filter(gruppeEntschieden).length;
    const offen = GRUPPEN.length - entschieden;
    document.getElementById("progress").textContent =
      entschieden + " von " + GRUPPEN.length + " Gruppen entschieden, " + offen + " offen"
      + " (" + judged + " von " + ENTRIES.length + " Kandidaten beurteilt)";
    GRUPPEN.forEach((gruppe) => {
      const el = document.getElementById("gruppe-" + gruppe.nummer);
      if (el) el.classList.toggle("entschieden", gruppeEntschieden(gruppe));
    });
    return;
  }
  document.getElementById("progress").textContent =
    judged + " von " + ENTRIES.length + " beurteilt";
}

async function saveUrteil(index) {
  const entry = ENTRIES[index];
  try {
    await fetch("/urteile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        index: entry.index,
        urteil: state[index].urteil,
        notiz: state[index].notiz,
      }),
    });
  } catch (e) {
    // Speichern auf dem Server fehlgeschlagen - der Stand bleibt lokal
    // erhalten und der Download-Knopf greift weiterhin als zweiter Weg.
  }
}

function setUrteil(index, urteil) {
  state[index].urteil = state[index].urteil === urteil ? null : urteil;
  renderCardButtons(index);
  updateProgress();
  saveUrteil(index);
}

function renderCardButtons(index) {
  const card = document.getElementById("card-" + index);
  if (!card) return;
  card.classList.toggle("judged", state[index].urteil !== null);
  ["ja", "nein", "spaeter"].forEach((key) => {
    const btn = card.querySelector('button[data-urteil="' + key + '"]');
    if (btn) btn.classList.toggle("active", state[index].urteil === key);
  });
}

function buildVergleich(entry) {
  const wrap = document.createElement("div");
  wrap.className = "vergleich";
  const table = document.createElement("table");
  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["#", "Zeitraum", "Dauer"].forEach((text) => {
    const th = document.createElement("th");
    th.textContent = text;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  entry.cluster.forEach((member) => {
    const row = document.createElement("tr");
    if (member.is_self) row.className = "self";
    const dauerS = Math.round((member.end_ms - member.start_ms) / 1000);
    const cells = [
      "#" + member.index,
      fmtHms(member.start_ms) + " - " + fmtHms(member.end_ms),
      dauerS + " s",
    ];
    cells.forEach((text) => {
      const td = document.createElement("td");
      td.textContent = text;
      row.appendChild(td);
    });
    tbody.appendChild(row);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  return wrap;
}

function nimmFassung(gruppe, gewaehlt) {
  // Ein Angebot, keine Automatik: erst dieser Klick setzt ueberhaupt etwas.
  // Er setzt fuer den gewaehlten Kandidaten "ja" und fuer die uebrigen der
  // Gruppe "nein" - je Kandidat ein eigenes Urteil, kein Gruppenurteil.
  // Jedes davon bleibt danach einzeln aenderbar.
  gruppe.indizes.forEach((index) => {
    const pos = posByIndex[index];
    if (pos === undefined) return;
    state[pos].urteil = index === gewaehlt ? "ja" : "nein";
    renderCardButtons(pos);
    saveUrteil(pos);
  });
  updateProgress();
  if (sessionActive !== null && gruppe.indizes.indexOf(ENTRIES[sessionActive].index) !== -1) {
    advanceSession();
  }
}

function buildCard(entry, index, gruppe) {
  const card = document.createElement("div");
  card.className = "card" + (entry.is_child ? " child" : "");
  card.id = "card-" + index;
  // Die Position, nicht der Kandidatenindex: im Gruppenmodus stimmt die
  // DOM-Reihenfolge nicht mehr mit ENTRIES ueberein, und currentCardIndex
  // liest den Zustand ueber die Position.
  card.dataset.pos = String(index);

  const head = document.createElement("div");
  head.className = "card-head";
  const title = document.createElement("h3");
  title.textContent = "#" + entry.index + " " + entry.titel;
  head.appendChild(title);
  const badge = document.createElement("span");
  badge.className = "badge " + entry.sicherheit;
  badge.textContent = entry.sicherheit;
  head.appendChild(badge);
  if (entry.is_child) {
    const tag = document.createElement("span");
    tag.className = "child-tag";
    tag.textContent = "enthalten in der Fassung darüber";
    head.appendChild(tag);
  }
  card.appendChild(head);

  const dauerS = Math.round((entry.end_ms - entry.start_ms) / 1000);
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = fmtHms(entry.start_ms) + " - " + fmtHms(entry.end_ms) +
    " (" + dauerS + " s)";
  card.appendChild(meta);

  if (!entry.is_child && entry.cluster.length > 1) {
    card.appendChild(buildVergleich(entry));
  }

  const video = document.createElement("video");
  video.controls = true;
  video.preload = "none";
  video.id = "video-" + index;
  const startS = entry.start_ms / 1000;
  const endS = entry.end_ms / 1000;
  // Nur der Startpunkt als Zeitfragment, kein ",end" mehr (Auftrag 25,
  // Fehler A): mit "#t=start,end" haelt Chrome beim ersten Durchlauf genau
  // am Ende von sich aus an - Ereignis "pause", kein "ended". Der
  // Ruecksprung unten greift dann zwar noch, aber die Wiedergabe steht
  // still, und die Schleife endete nach einem Durchlauf. Das Ende begrenzt
  // jetzt allein der timeupdate-Horcher.
  video.src = "/video#t=" + startS;
  video.addEventListener("play", () => {
    document.querySelectorAll("video").forEach((other) => {
      if (other !== video && !other.paused) other.pause();
    });
  });
  // Manuelles Spulen von natuerlichem Ablauf trennen: nicht ueber den
  // Zeitabstand zweier timeupdate-Takte (das trug nur einen Takt weit -
  // beim naechsten Takt war der Abstand wieder klein und die Schleife riss
  // zurueck), sondern ueber das "seeking"-Ereignis. Wer von Hand hinter das
  // Ausschnittsende spult, darf dort weiterschauen; sobald er wieder im
  // Ausschnitt landet, greift die Schleife erneut.
  let liefZuvor = false;
  let eigenerSprung = false;
  let manuellHinterEnde = false;
  video.addEventListener("seeking", () => {
    if (eigenerSprung) {
      eigenerSprung = false;
      return;
    }
    manuellHinterEnde = video.currentTime >= endS;
  });
  video.addEventListener("timeupdate", () => {
    if (video.currentTime < endS) manuellHinterEnde = false;
    if (video.currentTime >= endS && !manuellHinterEnde) {
      eigenerSprung = true;
      video.currentTime = startS;
      // Hat der Browser am Ausschnittsende von sich aus angehalten, obwohl
      // der Takt davor noch lief, hier weiterspielen - sonst bliebe es
      // nach genau einem Durchlauf stehen.
      if (video.paused && liefZuvor) video.play();
    }
    liefZuvor = !video.paused;
  });
  card.appendChild(video);

  const begruendung = document.createElement("div");
  begruendung.className = "begruendung";
  begruendung.textContent = entry.begruendung;
  card.appendChild(begruendung);

  if (entry.transcript_text) {
    const transcript = document.createElement("div");
    transcript.className = "transcript";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "Transkript";
    transcript.appendChild(label);
    if (!entry.transcript_precise) {
      const warning = document.createElement("span");
      warning.className = "transcript-warning";
      warning.textContent = "ungenau - ganzes Segment, keine Wortzeitstempel";
      label.appendChild(warning);
    }
    transcript.appendChild(document.createTextNode(entry.transcript_text));
    card.appendChild(transcript);
  }

  const buttons = document.createElement("div");
  buttons.className = "buttons";
  const defs = [
    ["ja", "Ja"],
    ["nein", "Nein"],
    ["spaeter", "Später"],
  ];
  defs.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.dataset.urteil = key;
    btn.addEventListener("click", () => {
      if (index === sessionActive) handleSessionUrteil(key);
      else setUrteil(index, key);
    });
    buttons.appendChild(btn);
  });
  if (gruppe && gruppe.indizes.length > 1) {
    const nehmen = document.createElement("button");
    nehmen.className = "fassung-nehmen";
    nehmen.textContent = "Diese Fassung nehmen";
    nehmen.addEventListener("click", () => nimmFassung(gruppe, entry.index));
    buttons.appendChild(nehmen);
  }
  card.appendChild(buttons);

  const note = document.createElement("input");
  note.type = "text";
  note.placeholder = "Notiz (optional)";
  note.value = state[index].notiz;
  note.addEventListener("input", () => {
    state[index].notiz = note.value;
    // Notizen entstehen tippend - nicht bei jedem Tastendruck speichern,
    // sondern eine kurze Ruhepause abwarten, bevor der Server geschrieben wird.
    clearTimeout(saveTimers[index]);
    saveTimers[index] = setTimeout(() => saveUrteil(index), 600);
  });
  card.appendChild(note);

  return card;
}

async function loadRestoredUrteile() {
  try {
    const res = await fetch("/urteile");
    if (!res.ok) return {};
    return await res.json();
  } catch (e) {
    return {};
  }
}

function applyRestored(restored) {
  ENTRIES.forEach((entry, index) => {
    const saved = restored[String(entry.index)];
    if (!saved) return;
    if (saved.urteil === "ja" || saved.urteil === "nein" || saved.urteil === "spaeter") {
      state[index].urteil = saved.urteil;
    }
    if (typeof saved.notiz === "string") {
      state[index].notiz = saved.notiz;
    }
  });
}

function haengeKarteAn(container, pos, gruppe) {
  if (pos === undefined) return;
  container.appendChild(buildCard(ENTRIES[pos], pos, gruppe));
  if (state[pos].urteil !== null) renderCardButtons(pos);
}

function buildGruppe(gruppe) {
  const box = document.createElement("section");
  box.className = "gruppe";
  box.id = "gruppe-" + gruppe.nummer;

  const kopf = document.createElement("div");
  kopf.className = "gruppe-kopf";
  const nummer = document.createElement("div");
  nummer.className = "gruppe-nummer";
  nummer.textContent = "Gruppe " + gruppe.nummer + " von " + GRUPPEN.length;
  kopf.appendChild(nummer);
  const titel = document.createElement("h2");
  titel.textContent = gruppe.projekt;
  kopf.appendChild(titel);
  const thema = document.createElement("div");
  thema.className = "gruppe-thema";
  thema.textContent = gruppe.thema;
  kopf.appendChild(thema);
  box.appendChild(kopf);

  if (gruppe.begruendung) {
    const warum = document.createElement("div");
    warum.className = "empfehlung";
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = "Warum diese Fassung vorgeschlagen wird";
    warum.appendChild(label);
    warum.appendChild(document.createTextNode(gruppe.begruendung));
    box.appendChild(warum);
  }

  haengeKarteAn(box, posByIndex[gruppe.empfohlen], gruppe);

  const weitere = gruppe.indizes.filter((index) => index !== gruppe.empfohlen);
  if (weitere.length > 0) {
    const details = document.createElement("details");
    details.className = "weitere";
    const summary = document.createElement("summary");
    summary.textContent = weitere.length === 1
      ? "1 weitere Fassung"
      : weitere.length + " weitere Fassungen";
    details.appendChild(summary);
    // Titel und Dauer stehen schon im zugeklappten Zustand da: der Nutzer
    // soll sehen koennen, WAS er hier uebergeht, ohne aufzuklappen.
    const liste = document.createElement("ul");
    liste.className = "weitere-liste";
    weitere.forEach((index) => {
      const pos = posByIndex[index];
      if (pos === undefined) return;
      const entry = ENTRIES[pos];
      const dauerS = Math.round((entry.end_ms - entry.start_ms) / 1000);
      const zeile = document.createElement("li");
      zeile.textContent = "#" + entry.index + " " + entry.titel + " (" + dauerS + " s)";
      liste.appendChild(zeile);
    });
    details.appendChild(liste);
    const kandidaten = document.createElement("div");
    kandidaten.className = "kandidaten";
    weitere.forEach((index) => haengeKarteAn(kandidaten, posByIndex[index], gruppe));
    details.appendChild(kandidaten);
    box.appendChild(details);
  }
  return box;
}

async function render() {
  const restored = await loadRestoredUrteile();
  applyRestored(restored);
  const container = document.getElementById("cards");
  if (gruppenModus()) {
    GRUPPEN.forEach((gruppe) => container.appendChild(buildGruppe(gruppe)));
  } else {
    ENTRIES.forEach((entry, index) => {
      container.appendChild(buildCard(entry, index));
      if (state[index].urteil !== null) renderCardButtons(index);
    });
  }
  updateProgress();
  startSession();
}

function currentCardIndex() {
  const cards = Array.from(document.querySelectorAll(".card"));
  const scrollMid = window.scrollY + window.innerHeight / 2;
  let closest = 0;
  let closestDist = Infinity;
  cards.forEach((card) => {
    const rect = card.getBoundingClientRect();
    const top = rect.top + window.scrollY;
    const mid = top + rect.height / 2;
    const dist = Math.abs(mid - scrollMid);
    // Die Position aus dem Datenfeld, nicht die Schleifenzahl: im
    // Gruppenmodus steht die dritte Karte im Dokument nicht mehr an der
    // dritten Stelle von ENTRIES, und eine Taste setzte sonst das Urteil
    // auf einen fremden Kandidaten.
    const pos = Number(card.dataset.pos);
    if (dist < closestDist && Number.isInteger(pos)) {
      closestDist = dist;
      closest = pos;
    }
  });
  return closest;
}

document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
  if (autoplayBlocked) {
    // Browser verweigerte automatisches Abspielen bis zur ersten Nutzereingabe -
    // der erste Tastendruck egal welcher Taste hebt die Sperre auf und startet
    // die Wiedergabe; er zaehlt nicht zusaetzlich als Urteil oder Pause-Umschalter.
    hideAutoplayHint();
    playActive();
    e.preventDefault();
    return;
  }
  if (e.code === "Space") {
    e.preventDefault();
    const index = sessionActive !== null ? sessionActive : currentCardIndex();
    const video = document.getElementById("video-" + index);
    if (video) {
      if (video.paused) video.play(); else video.pause();
    }
    return;
  }
  if (e.key === "1") {
    e.preventDefault();
    if (sessionActive !== null) handleSessionUrteil("ja");
    else setUrteil(currentCardIndex(), "ja");
  } else if (e.key === "2") {
    e.preventDefault();
    if (sessionActive !== null) handleSessionUrteil("nein");
    else setUrteil(currentCardIndex(), "nein");
  } else if (e.key === "3") {
    e.preventDefault();
    if (sessionActive !== null) handleSessionUrteil("spaeter");
    else setUrteil(currentCardIndex(), "spaeter");
  }
});

document.getElementById("session-status-close").addEventListener("click", () => {
  document.getElementById("session-status").hidden = true;
});

document.getElementById("download-btn").addEventListener("click", () => {
  const payload = ENTRIES.map((entry, index) => ({
    index: entry.index,
    titel: entry.titel,
    start_ms: entry.start_ms,
    end_ms: entry.end_ms,
    ist_kind: entry.is_child,
    urteil: state[index].urteil,
    notiz: state[index].notiz,
  }));
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "urteile.json";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

render();
</script>
</body>
</html>
"""

_KRITERIEN_MISSING_HTML = (
    "<summary>Kriterien (shorts-kriterien.yaml nicht gefunden)</summary>"
    "<p>Die Kriteriendatei lag beim Bauen dieser Seite nicht vor.</p>"
)


def _kriterien_html(kriterien_text: str | None) -> str:
    if kriterien_text is None:
        return _KRITERIEN_MISSING_HTML
    return "<summary>Kriterien (labels/repeat/shorts-kriterien.yaml)</summary><pre>" + html.escape(
        kriterien_text
    ) + "</pre>"


def _urteile_pfad_html(urteile_path: Path | None) -> str:
    """Die Zeile in der Abschlussmeldung, die den Speicherort nennt.

    Der Nutzer sieht das Speichern nach jedem Tastendruck sonst nicht
    (Auftrag 25, Abschnitt 3) - nur eine Auskunft, kein Dialog. Ohne
    bekannten Pfad bleibt die Zeile weg.
    """
    if urteile_path is None:
        return ""
    return (
        '<div id="session-status-pfad">Gespeichert in <code>'
        + html.escape(str(urteile_path))
        + "</code></div>"
    )


def _rueckfall_html(grund: str | None) -> str:
    """Die Zeile im Kopf, die den Rueckfall auf die flache Liste begruendet.

    Sie steht nur, wenn es einen Grund gibt. Eine Seite, die stillschweigend
    flach bleibt, sieht aus wie eine Seite ohne Buendelung - und der Nutzer
    beurteilt dann wieder 69 Kandidaten einzeln, ohne zu wissen, warum.
    """
    if grund is None:
        return ""
    return (
        '<p id="rueckfall-hinweis"><strong>Ungruppierte Ansicht:</strong> '
        + html.escape(grund)
        + "</p>"
    )


def build_judge_html(
    entries: Sequence[JudgeEntry],
    *,
    kriterien_text: str | None,
    urteile_path: Path | None = None,
    gruppen: Sequence[BuendelGruppe] = (),
    rueckfall_grund: str | None = None,
) -> str:
    """Render die eigenständige Urteilsseite für ``entries``.

    ``kriterien_text`` ist der rohe Inhalt von ``shorts-kriterien.yaml`` -
    gelesen und angezeigt, nicht ausgewertet (Auftrag 20, Abschnitt 3.4).
    Fehlt die Datei, läuft die Seite ohne diesen Abschnitt.

    ``urteile_path`` nennt in der Abschlussmeldung, wohin der Server die
    Urteile schreibt. Ohne Angabe bleibt die Meldung ohne Pfadzeile.

    ``gruppen`` schaltet die gruppierte Ansicht ein. Leer heißt: flache
    Liste wie bisher - entweder weil es keine ``buendel.json`` gibt oder
    weil sie die Prüfung nicht besteht. ``rueckfall_grund`` sagt dann im
    Kopf, welcher der beiden Fälle vorliegt.
    """
    entries_json = json.dumps([_entry_to_js_dict(entry) for entry in entries])
    gruppen_json = json.dumps([_gruppe_to_js_dict(gruppe) for gruppe in gruppen])
    return (
        _TEMPLATE.replace("__ENTRIES_JSON__", entries_json)
        .replace("__GRUPPEN_JSON__", gruppen_json)
        .replace("__KRITERIEN_HTML__", _kriterien_html(kriterien_text))
        .replace("__URTEILE_PFAD_HTML__", _urteile_pfad_html(urteile_path))
        .replace("__RUECKFALL_HTML__", _rueckfall_html(rueckfall_grund))
    )


DEFAULT_KRITERIEN_PATH = Path("labels") / "repeat" / "shorts-kriterien.yaml"
