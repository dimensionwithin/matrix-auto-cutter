"""Tests für Shorts-Stufe 2, Teil 3: die Urteilsseite (Video über den Server, Auftrag 22)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from matrix_auto_cutter.shorts import candidates as cd
from matrix_auto_cutter.shorts import judge


def _candidate(index: int, start_ms: int, end_ms: int, **overrides: object) -> cd.Candidate:
    base: dict[str, object] = {
        "titel": f"Titel {index}",
        "begruendung": f"Begruendung {index}",
        "sicherheit": "mittel",
        "enthaelt": (),
    }
    base.update(overrides)
    return cd.Candidate(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        titel=str(base["titel"]),
        begruendung=str(base["begruendung"]),
        sicherheit=base["sicherheit"],  # type: ignore[arg-type]
        enthaelt=tuple(base["enthaelt"]),  # type: ignore[arg-type]
    )


def _token(text: str, start_ms: int, end_ms: int) -> dict[str, object]:
    return {"text": text, "offsets": {"from": start_ms, "to": end_ms}}


# --- Reihenfolge/Verschachtelung, Auftrag 21 Punkt 3 (beste Sicherheit der Gruppe) --


def test_order_candidates_sorts_by_sicherheit_then_start() -> None:
    low = _candidate(0, 10_000, 20_000, sicherheit="niedrig")
    high = _candidate(1, 20_000, 30_000, sicherheit="hoch")
    mid = _candidate(2, 0, 10_000, sicherheit="mittel")
    ordered = judge.order_candidates([low, high, mid])
    assert [c.index for c, _ in ordered] == [1, 2, 0]
    assert [is_child for _, is_child in ordered] == [False, False, False]


def test_order_candidates_keeps_contained_candidates_next_to_their_container() -> None:
    container = _candidate(0, 0, 60_000, sicherheit="hoch", enthaelt=[1, 2])
    child_a = _candidate(1, 0, 20_000, sicherheit="niedrig")
    child_b = _candidate(2, 30_000, 50_000, sicherheit="niedrig")
    unrelated = _candidate(3, 100_000, 110_000, sicherheit="hoch")

    ordered = judge.order_candidates([child_a, unrelated, container, child_b])

    assert [c.index for c, _ in ordered] == [0, 1, 2, 3]
    assert [is_child for _, is_child in ordered] == [False, True, True, False]


def test_order_candidates_single_candidate_has_no_children() -> None:
    only = _candidate(0, 0, 1_000)
    ordered = judge.order_candidates([only])
    assert ordered == [(only, False)]


def test_order_candidates_group_sorts_by_best_member_not_the_outer_one() -> None:
    """Auftrag 21, Punkt 3: #11 (MITTEL) enthaelt #10 (HOCH) -> Gruppe gehoert zu HOCH.

    Das war der gemeldete Fehler: die Gruppe wanderte bisher zu MITTEL, weil
    nur die Sicherheit der aeusseren Fassung zaehlte.
    """
    outer_mittel = _candidate(11, 0, 53_000, sicherheit="mittel", enthaelt=[10])
    inner_hoch = _candidate(10, 0, 34_000, sicherheit="hoch")
    lone_mittel = _candidate(20, 100_000, 110_000, sicherheit="mittel")

    ordered = judge.order_candidates([outer_mittel, inner_hoch, lone_mittel])

    # die Gruppe #11/#10 steht komplett vor dem einsamen MITTEL-Kandidaten,
    # weil sie durch #10 (HOCH) besser eingestuft ist als ihre eigene
    # Sicherheit nahelegt
    assert [c.index for c, _ in ordered] == [11, 10, 20]


def test_order_candidates_group_with_two_children_sorts_by_best_child() -> None:
    """Wie das echte Beispiel #18(MITTEL)/#16+#17(HOCH) aus dem Auftrag."""
    outer = _candidate(18, 0, 75_000, sicherheit="mittel", enthaelt=[16, 17])
    child_hoch_1 = _candidate(16, 0, 28_000, sicherheit="hoch")
    child_hoch_2 = _candidate(17, 30_000, 47_000, sicherheit="hoch")
    other_hoch = _candidate(30, 200_000, 210_000, sicherheit="hoch")

    ordered = judge.order_candidates([outer, child_hoch_1, child_hoch_2, other_hoch])
    indices = [c.index for c, _ in ordered]

    # Gruppe #18/#16/#17 steht zusammen im HOCH-Rang, nicht bei MITTEL
    assert indices == [18, 16, 17, 30]


def test_cluster_map_groups_container_and_children() -> None:
    outer = _candidate(0, 0, 10_000, enthaelt=[1])
    inner = _candidate(1, 0, 5_000)
    lone = _candidate(2, 20_000, 25_000)

    mapping = judge.cluster_map([outer, inner, lone])

    assert {c.index for c in mapping[0]} == {0, 1}
    assert mapping[0] == mapping[1]
    assert mapping[2] == (lone,)


# --- Transkripttext: Auftrag 21 Punkt 2 (wortgenauer Zuschnitt) ---------------------


def test_candidate_transcript_text_from_segments_joins_overlapping_segments() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 1_000, "text": "vorher"},
        {"start_ms": 1_000, "end_ms": 3_000, "text": "innen eins"},
        {"start_ms": 3_000, "end_ms": 5_000, "text": "innen zwei"},
        {"start_ms": 5_000, "end_ms": 6_000, "text": "nachher"},
    ]
    text = judge.candidate_transcript_text_from_segments(segments, start_ms=1_000, end_ms=5_000)
    assert text == "innen eins innen zwei"


def test_candidate_transcript_text_from_segments_skips_blank_segments() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 1_000, "text": "   "},
        {"start_ms": 1_000, "end_ms": 2_000, "text": "Text"},
    ]
    assert judge.candidate_transcript_text_from_segments(segments, 0, 2_000) == "Text"


def test_load_transcript_segments_missing_file_returns_empty(tmp_path: Path) -> None:
    assert judge.load_transcript_segments(tmp_path / "fehlt.json") == []


def test_load_transcript_segments_reads_segments(tmp_path: Path) -> None:
    path = tmp_path / "transkript-rendered.json"
    path.write_text(
        json.dumps({"segments": [{"start_ms": 0, "end_ms": 1_000, "text": "hallo"}]}),
        encoding="utf-8",
    )
    segments = judge.load_transcript_segments(path)
    assert segments == [{"start_ms": 0, "end_ms": 1_000, "text": "hallo"}]


def test_merge_tokens_to_words_joins_subword_pieces() -> None:
    """whisper-cli's -ojf liefert Teiltoken (" L" + "ass") statt ganzer Woerter."""
    tokens = [
        _token(" Wir", 0, 300),
        _token(" L", 300, 350),
        _token("ass", 350, 500),
        _token(" uns", 500, 700),
    ]
    words = judge._merge_tokens_to_words(tokens)
    assert [w.text for w in words] == [" Wir", " Lass", " uns"]
    assert words[1].start_ms == 300
    assert words[1].end_ms == 500


def test_merge_tokens_to_words_skips_special_and_empty_tokens() -> None:
    tokens = [
        _token("[_BEG_]", 0, 0),
        _token("", 0, 0),
        _token(" Hallo", 0, 300),
    ]
    words = judge._merge_tokens_to_words(tokens)
    assert [w.text for w in words] == [" Hallo"]


def test_candidate_transcript_text_from_words_drops_words_crossing_the_boundary() -> None:
    """Das gemeldete Leck: #3 endete mit dem angeschnittenen Wortanfang von #4."""
    words = [
        judge.TranscriptWord(" Danach", 0, 500),
        judge.TranscriptWord(" kommt", 500, 1_000),
        judge.TranscriptWord(" das", 1_000, 1_200),
        judge.TranscriptWord(" Ende.", 1_200, 1_500),
        judge.TranscriptWord(" Lass", 1_450, 1_900),  # ragt ueber die Grenze hinaus
        judge.TranscriptWord(" uns", 1_900, 2_100),
    ]
    text = judge.candidate_transcript_text_from_words(words, start_ms=0, end_ms=1_500)
    assert text == "Danach kommt das Ende."
    assert "Lass" not in text


def test_candidate_transcript_prefers_word_level_when_available() -> None:
    words = [judge.TranscriptWord(" Genau.", 0, 500)]
    segments = [{"start_ms": 0, "end_ms": 2_000, "text": "Genau. Und noch mehr."}]
    text, precise = judge.candidate_transcript(
        words=words, segments=segments, start_ms=0, end_ms=500
    )
    assert text == "Genau."
    assert precise is True


def test_candidate_transcript_falls_back_to_segments_without_words() -> None:
    segments = [{"start_ms": 0, "end_ms": 2_000, "text": "Ganzes Segment"}]
    text, precise = judge.candidate_transcript(
        words=[], segments=segments, start_ms=0, end_ms=500
    )
    assert text == "Ganzes Segment"
    assert precise is False


def test_candidate_transcript_falls_back_when_no_word_is_fully_contained() -> None:
    words = [judge.TranscriptWord(" Wort", 0, 2_000)]  # ragt komplett ueber [0,500) hinaus
    segments = [{"start_ms": 0, "end_ms": 2_000, "text": "Ganzes Segment"}]
    text, precise = judge.candidate_transcript(
        words=words, segments=segments, start_ms=0, end_ms=500
    )
    assert text == "Ganzes Segment"
    assert precise is False


def test_load_transcript_words_missing_file_returns_empty(tmp_path: Path) -> None:
    assert judge.load_transcript_words(tmp_path / "fehlt.wav.json") == []


def test_load_transcript_words_reads_and_merges_tokens(tmp_path: Path) -> None:
    path = tmp_path / "transkript-rendered.wav.json"
    path.write_text(
        json.dumps(
            {
                "transcription": [
                    {"tokens": [_token("[_BEG_]", 0, 0), _token(" Hallo", 0, 400)]},
                    {"tokens": [_token(" Welt", 400, 800)]},
                ]
            }
        ),
        encoding="utf-8",
    )
    words = judge.load_transcript_words(path)
    assert [w.text for w in words] == [" Hallo", " Welt"]


# --- Kartenaufbau --------------------------------------------------------------------


def test_build_judge_entries_attaches_transcript() -> None:
    candidates = [_candidate(0, 1_000, 3_000, sicherheit="hoch")]
    segments = [{"start_ms": 0, "end_ms": 5_000, "text": "gesprochener Text"}]
    entries = judge.build_judge_entries(
        candidates, transcript_segments=segments, transcript_words=[]
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry.transcript_text == "gesprochener Text"
    assert entry.transcript_precise is False
    assert entry.is_child is False
    assert entry.cluster == ()


def test_build_judge_entries_uses_word_level_transcript_when_available() -> None:
    candidates = [_candidate(0, 0, 500)]
    words = [judge.TranscriptWord(" Genau.", 0, 500), judge.TranscriptWord(" Weiter", 500, 900)]
    entries = judge.build_judge_entries(
        candidates, transcript_segments=[], transcript_words=words
    )
    assert entries[0].transcript_text == "Genau."
    assert entries[0].transcript_precise is True


def test_build_judge_entries_fills_cluster_for_grouped_candidates() -> None:
    outer = _candidate(11, 0, 53_000, sicherheit="mittel", enthaelt=[10])
    inner = _candidate(10, 0, 34_000, sicherheit="hoch")
    entries = judge.build_judge_entries([outer, inner], transcript_segments=[], transcript_words=[])
    by_index = {e.index: e for e in entries}
    assert {m.index for m in by_index[11].cluster} == {10, 11}
    assert {m.index for m in by_index[10].cluster} == {10, 11}
    self_member = next(m for m in by_index[11].cluster if m.is_self)
    assert self_member.index == 11


# --- HTML-Seite ------------------------------------------------------------------


def _entry(
    index: int = 0,
    is_child: bool = False,
    transcript_precise: bool = True,
    cluster: tuple[judge.ClusterMember, ...] = (),
    start_ms: int = 0,
    end_ms: int = 1_000,
) -> judge.JudgeEntry:
    return judge.JudgeEntry(
        index=index,
        titel="Titel",
        begruendung="Begruendung",
        sicherheit="hoch",
        start_ms=start_ms,
        end_ms=end_ms,
        is_child=is_child,
        transcript_text="Text",
        transcript_precise=transcript_precise,
        cluster=cluster,
    )


def test_build_judge_html_points_video_at_server_route_with_time_fragment() -> None:
    html_text = judge.build_judge_html(
        [_entry(start_ms=45_000, end_ms=86_000)], kriterien_text=None
    )
    assert "/video#t=" in html_text
    assert "file://" not in html_text
    assert "data:audio" not in html_text


def test_build_judge_html_loops_video_at_candidate_boundaries() -> None:
    html_text = judge.build_judge_html(
        [_entry(start_ms=45_317, end_ms=86_817)], kriterien_text=None
    )
    assert "entry.start_ms / 1000" in html_text
    assert "entry.end_ms / 1000" in html_text
    assert "video.currentTime = startS" in html_text
    assert '"timeupdate"' in html_text


def test_build_judge_html_time_fragment_has_no_end_so_the_loop_keeps_running() -> None:
    """Auftrag 25, Fehler A.

    Mit ``#t=start,end`` haelt Chrome beim ersten Durchlauf am Ende von
    selbst an (Ereignis ``pause``, kein ``ended``). Der Ruecksprung setzt
    dann zwar ``currentTime`` zurueck, die Wiedergabe steht aber - die
    Schleife lief genau einmal. Ohne Endmarke im Fragment begrenzt allein
    der ``timeupdate``-Horcher den Ausschnitt.
    """
    html_text = judge.build_judge_html(
        [_entry(start_ms=576_850, end_ms=591_733)], kriterien_text=None
    )
    assert '"/video#t=" + startS;' in html_text
    assert '"/video#t=" + startS + "," + endS' not in html_text


def test_build_judge_html_resumes_playback_after_the_loop_jump() -> None:
    """Haelt der Browser am Ausschnittsende an, laeuft es danach weiter."""
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "if (video.paused && liefZuvor) video.play();" in html_text
    assert "liefZuvor = !video.paused;" in html_text


def test_build_judge_html_keeps_manual_seek_apart_from_natural_playback() -> None:
    """Von Hand hinter das Ende spulen darf nicht zurueckgerissen werden.

    Der frueher benutzte Zeitabstand zweier ``timeupdate``-Takte trug nur
    einen Takt weit: danach war der Abstand wieder klein und die Schleife
    riss zurueck (im Browser gemessen, Auftrag 25). Das ``seeking``-
    Ereignis haelt dagegen bis zur Rueckkehr in den Ausschnitt.
    """
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert '"seeking"' in html_text
    assert "manuellHinterEnde = video.currentTime >= endS;" in html_text
    assert "if (video.currentTime < endS) manuellHinterEnde = false;" in html_text
    assert "video.currentTime >= endS && !manuellHinterEnde" in html_text
    # Der eigene Ruecksprung darf nicht als Handarbeit zaehlen.
    assert "eigenerSprung = true;" in html_text
    assert "lastTime" not in html_text


def test_build_judge_html_wires_guided_session_flow() -> None:
    html_text = judge.build_judge_html(
        [_entry(index=0), _entry(index=1)], kriterien_text=None
    )
    # Warteschlange nur aus offenen Kandidaten (unbeurteilt oder "spaeter").
    assert 'state[i].urteil === null || state[i].urteil === "spaeter"' in html_text
    # Sitzung startet automatisch nach dem Aufbau der Karten.
    assert "startSession();\n}" in html_text
    # Autoplay-Versuch mit Abweisungs-Behandlung statt Stummschalten.
    assert "video.play()" in html_text
    assert "showAutoplayHint" in html_text
    assert "autoplay-hint" in html_text
    assert "video.muted" not in html_text
    assert ".muted = true" not in html_text
    # Tasten 1/2/3 und Leertaste zielen auf den Sitzungs-Kandidaten.
    assert "handleSessionUrteil(" in html_text
    assert "sessionActive" in html_text


def test_build_judge_html_defers_spaeter_only_once_per_session() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert 'urteil === "spaeter" && deferCount[index] === 0' in html_text
    assert "sessionQueue.push(index)" in html_text


def test_build_judge_html_shows_session_end_message_with_counts() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert '"Alle " + total + " beurteilt."' in html_text
    assert '" entschieden, " + open + " offen geblieben."' in html_text


def test_build_judge_html_pins_session_end_message_into_view() -> None:
    """Auftrag 25, Fehler B.

    Im Seitenfluss stand die Meldung am Seitenanfang - nach dem letzten
    Urteil gemessen 17.361 px ueber dem Sichtfeld. ``position: fixed``
    haelt sie im Bild und fuegt oben keine Hoehe ein, die den Rest der
    Seite verschiebt.
    """
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    marker = html_text.index("#session-status {")
    block = html_text[marker : marker + 400]
    assert "position: fixed;" in block
    assert "bottom:" in block
    assert 'id="session-status-text"' in html_text
    assert 'id="session-status-close"' in html_text


def test_build_judge_html_names_the_urteile_file_in_the_end_message(tmp_path: Path) -> None:
    """Der Nutzer sieht das Speichern sonst nicht - nur eine Auskunft."""
    urteile_path = tmp_path / "urteile.json"
    html_text = judge.build_judge_html(
        [_entry()], kriterien_text=None, urteile_path=urteile_path
    )
    assert "Gespeichert in" in html_text
    assert html.escape(str(urteile_path)) in html_text


def test_build_judge_html_omits_the_path_line_without_a_urteile_path() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "Gespeichert in" not in html_text
    assert "__URTEILE_PFAD_HTML__" not in html_text


def test_build_judge_html_escapes_the_urteile_path(tmp_path: Path) -> None:
    urteile_path = tmp_path / "<urteile>.json"
    html_text = judge.build_judge_html(
        [_entry()], kriterien_text=None, urteile_path=urteile_path
    )
    assert "<urteile>.json" not in html_text
    assert "&lt;urteile&gt;.json" in html_text


def test_build_judge_html_is_self_contained_and_embeds_entries() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "<script src=" not in html_text
    assert "cdn." not in html_text
    assert '"titel": "Titel"' in html_text
    assert "urteile.json" in html_text
    # Ohne Buendelung bleibt die Seite flach - der Rueckfall ist der
    # Normalfall fuer jede Aufnahme vor dem 27.8. (Auftrag
    # urteilsseite-gruppiert). Ohne Grund steht auch kein Hinweis im Kopf.
    assert "const GRUPPEN = [];" in html_text
    assert 'id="rueckfall-hinweis"' not in html_text


def test_build_judge_html_has_no_external_references() -> None:
    """Offline-Anforderung (Auftrag 20/21): kein Netzzugriff noetig."""
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "http://" not in html_text
    assert "https://" not in html_text
    assert "<link " not in html_text


def test_build_judge_html_shows_missing_kriterien_notice() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "nicht gefunden" in html_text


def test_build_judge_html_embeds_kriterien_text_when_given() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text="kriterien:\n  - name: x\n")
    assert "kriterien:" in html_text
    assert "name: x" in html_text


def test_build_judge_html_escapes_kriterien_text() -> None:
    html_text = judge.build_judge_html([_entry()], kriterien_text="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_build_judge_html_explains_sicherheit_once() -> None:
    """Auftrag 21, Punkt 4: die Seite erklaert, woher 'sicherheit' stammt."""
    html_text = judge.build_judge_html([_entry()], kriterien_text=None)
    assert "Selbsteinschätzung" in html_text
    assert "kandidaten.json" in html_text


def test_build_judge_html_embeds_cluster_for_js_comparison() -> None:
    member = judge.ClusterMember(index=10, titel="Kurz", start_ms=0, end_ms=34_000, is_self=True)
    html_text = judge.build_judge_html([_entry(cluster=(member,))], kriterien_text=None)
    assert '"cluster"' in html_text
    assert '"is_self": true' in html_text


def test_build_judge_html_marks_imprecise_transcript() -> None:
    html_text = judge.build_judge_html(
        [_entry(transcript_precise=False)], kriterien_text=None
    )
    assert '"transcript_precise": false' in html_text
