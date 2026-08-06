"""Build a single self-contained ``review.html`` for human judging of candidate pairs.

No server, no CDN, no framework. Audio is embedded as a base64 data URI.
State lives only in the page's memory; judgments are exported via a
client-side download of ``urteile.json`` matching the bindende schema in
``labels/repeat/urteile-2026-08-05.json``.
"""

from __future__ import annotations

import base64
import html
import json
from dataclasses import dataclass

_SIZE_WARNING_BYTES = 150 * 1024 * 1024
_AUDIO_MIME = "audio/mp4"


@dataclass(frozen=True)
class ReviewEntry:
    """One card's worth of data: both passages, scores, detectors, and its clip audio."""

    stem: str
    nr: int
    source: str
    first_text: str
    first_start_ms: int
    first_end_ms: int
    second_text: str
    second_start_ms: int
    second_end_ms: int
    detectors: tuple[str, ...]
    utterance_score: float | None
    boundary_score: float | None
    window_words: int | None
    first_window_text: str | None
    second_window_text: str | None
    audio_bytes: bytes | None
    audio_error: str | None


def _entry_to_js_dict(entry: ReviewEntry) -> dict[str, object]:
    audio_data_uri: str | None = None
    if entry.audio_bytes is not None:
        encoded = base64.b64encode(entry.audio_bytes).decode("ascii")
        audio_data_uri = f"data:{_AUDIO_MIME};base64,{encoded}"
    return {
        "stem": html.escape(entry.stem),
        "nr": entry.nr,
        "source": html.escape(entry.source),
        "first": {
            "text": html.escape(entry.first_text),
            "start_ms": entry.first_start_ms,
            "end_ms": entry.first_end_ms,
        },
        "second": {
            "text": html.escape(entry.second_text),
            "start_ms": entry.second_start_ms,
            "end_ms": entry.second_end_ms,
        },
        "detectors": [html.escape(d) for d in entry.detectors],
        "utterance_score": entry.utterance_score,
        "boundary_score": entry.boundary_score,
        "window_words": entry.window_words,
        "first_window_text": html.escape(entry.first_window_text)
        if entry.first_window_text is not None
        else None,
        "second_window_text": html.escape(entry.second_window_text)
        if entry.second_window_text is not None
        else None,
        "audio_data_uri": audio_data_uri,
        "audio_error": html.escape(entry.audio_error) if entry.audio_error is not None else None,
    }


_TEMPLATE = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>Repeat-Review</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #ffffff;
    --fg: #1a1a1a;
    --muted: #666;
    --card-bg: #f6f6f8;
    --border: #d8d8dc;
    --accent: #2b6cb0;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #16161a;
      --fg: #eaeaea;
      --muted: #9a9aa2;
      --card-bg: #202024;
      --border: #34343a;
      --accent: #6ea8e0;
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
  .schnitt-row { display: none; margin-top: 0.4rem; }
  .schnitt-row button.active {
    background: var(--accent);
    color: white;
    border-color: var(--accent);
  }
  #size-warning {
    font-size: 0.85rem;
    color: #b5541a;
    padding: 0.5rem 1rem;
    display: none;
  }
  main {
    max-width: 900px;
    margin: 1rem auto;
    padding: 0 1rem;
    display: flex;
    flex-direction: column;
    gap: 1rem;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 1rem 1.25rem;
  }
  .card.judged { border-color: var(--accent); }
  .card-head {
    display: flex;
    flex-wrap: wrap;
    gap: 0.25rem 1rem;
    font-size: 0.9rem;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }
  .card-head strong { color: var(--fg); }
  audio { width: 100%; margin: 0.5rem 0; }
  .passage { margin: 0.5rem 0; }
  .passage .label {
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.02em;
    color: var(--muted);
  }
  .passage .text { margin-top: 0.15rem; }
  .detectors { font-size: 0.85rem; color: var(--muted); margin: 0.5rem 0; }
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
  .audio-missing {
    font-size: 0.85rem;
    color: #b5541a;
    margin: 0.5rem 0;
  }
  #download-bar {
    max-width: 900px;
    margin: 1rem auto 0 auto;
    padding: 0 1rem;
  }
  #download-btn {
    padding: 0.6rem 1.2rem;
    font-weight: 600;
  }
  .hint { font-size: 0.8rem; color: var(--muted); }
</style>
</head>
<body>
<header>
  <div id="progress">0 von 0 beurteilt</div>
  <div class="hint">Tasten: 1 Versprecher &middot; 2 Bewusst &middot; 3 Unsinn &middot;
  4 Erste raus &middot; 5 Zweite raus &middot; 6 Beide raus &middot;
  Leertaste Wiedergabe</div>
</header>
<div id="size-warning"></div>
<main id="cards"></main>
<div id="download-bar">
  <button id="download-btn">Urteile herunterladen</button>
</div>

<script>
const ENTRIES = __ENTRIES_JSON__;
const SIZE_WARNING_BYTES = __SIZE_WARNING_BYTES__;

const state = ENTRIES.map(() => ({ urteil: null, notiz: "", schnitt: null }));

function fmtHms(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return pad(h) + ":" + pad(m) + ":" + pad(s);
}

function fmtScore(v) {
  return v === null || v === undefined ? "\\u2013" : v.toFixed(4);
}

function detectorLabel(entry) {
  const both = entry.detectors.includes("utterance") && entry.detectors.includes("boundary");
  if (both) return "beide (utterance + boundary)";
  return entry.detectors.join(", ");
}

function updateProgress() {
  const judged = state.filter((s) => s.urteil !== null).length;
  document.getElementById("progress").textContent =
    judged + " von " + ENTRIES.length + " beurteilt";
}

function setUrteil(index, urteil) {
  state[index].urteil = state[index].urteil === urteil ? null : urteil;
  if (state[index].urteil === "versprecher") {
    // Vorbelegung "erste": bei einer echten Wiederholung bricht die erste
    // Passage ab, die zweite traegt den Gedanken weiter -- die erste ist
    // damit im Regelfall die richtige zum Herausschneiden.
    if (state[index].schnitt === null) state[index].schnitt = "erste";
  } else {
    state[index].schnitt = null;
  }
  renderCardButtons(index);
  updateProgress();
}

function setSchnitt(index, schnitt) {
  if (state[index].urteil !== "versprecher") return;
  state[index].schnitt = schnitt;
  renderCardButtons(index);
}

function renderCardButtons(index) {
  const card = document.getElementById("card-" + index);
  if (!card) return;
  card.classList.toggle("judged", state[index].urteil !== null);
  ["versprecher", "bewusst", "unsinn"].forEach((key) => {
    const btn = card.querySelector('button[data-urteil="' + key + '"]');
    if (btn) btn.classList.toggle("active", state[index].urteil === key);
  });
  const schnittRow = card.querySelector(".schnitt-row");
  if (schnittRow) {
    const show = state[index].urteil === "versprecher";
    schnittRow.style.display = show ? "flex" : "none";
    ["erste", "zweite", "beide"].forEach((key) => {
      const btn = schnittRow.querySelector('button[data-schnitt="' + key + '"]');
      if (btn) btn.classList.toggle("active", state[index].schnitt === key);
    });
  }
}

function buildCard(entry, index) {
  const card = document.createElement("div");
  card.className = "card";
  card.id = "card-" + index;

  const head = document.createElement("div");
  head.className = "card-head";
  const scoreParts = [];
  if (entry.utterance_score !== null) {
    scoreParts.push("utterance=" + fmtScore(entry.utterance_score));
  }
  if (entry.boundary_score !== null) {
    scoreParts.push("boundary=" + fmtScore(entry.boundary_score));
  }

  const fileSpan = document.createElement("span");
  const fileStrong = document.createElement("strong");
  fileStrong.textContent = "Datei:";
  fileSpan.appendChild(fileStrong);
  fileSpan.appendChild(document.createTextNode(" " + entry.stem + " #" + entry.nr));
  head.appendChild(fileSpan);

  const timeSpan = document.createElement("span");
  const timeStrong = document.createElement("strong");
  timeStrong.textContent = "Zeit:";
  timeSpan.appendChild(timeStrong);
  timeSpan.appendChild(document.createTextNode(" " + fmtHms(entry.first.start_ms)));
  head.appendChild(timeSpan);

  const scoreSpan = document.createElement("span");
  const scoreStrong = document.createElement("strong");
  scoreStrong.textContent = "Score(s):";
  scoreSpan.appendChild(scoreStrong);
  scoreSpan.appendChild(document.createTextNode(" " + scoreParts.join(", ")));
  head.appendChild(scoreSpan);

  const detSpan = document.createElement("span");
  const detStrong = document.createElement("strong");
  detStrong.textContent = "Detektor:";
  detSpan.appendChild(detStrong);
  detSpan.appendChild(document.createTextNode(" " + detectorLabel(entry)));
  head.appendChild(detSpan);

  card.appendChild(head);

  if (entry.audio_data_uri) {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    audio.id = "audio-" + index;
    const source = document.createElement("source");
    source.src = entry.audio_data_uri;
    source.type = "audio/mp4";
    audio.appendChild(source);
    card.appendChild(audio);
  } else {
    const missing = document.createElement("div");
    missing.className = "audio-missing";
    missing.textContent = "Kein Audio verfuegbar: " + (entry.audio_error || "unbekannter Fehler");
    card.appendChild(missing);
  }

  const first = document.createElement("div");
  first.className = "passage";
  const firstLabel = document.createElement("div");
  firstLabel.className = "label";
  firstLabel.textContent = "Erste Passage";
  first.appendChild(firstLabel);
  const firstText = document.createElement("div");
  firstText.className = "text";
  firstText.textContent = entry.first.text;
  first.appendChild(firstText);
  card.appendChild(first);

  const second = document.createElement("div");
  second.className = "passage";
  const secondLabel = document.createElement("div");
  secondLabel.className = "label";
  secondLabel.textContent = "Zweite Passage";
  second.appendChild(secondLabel);
  const secondText = document.createElement("div");
  secondText.className = "text";
  secondText.textContent = entry.second.text;
  second.appendChild(secondText);
  card.appendChild(second);

  const detectors = document.createElement("div");
  detectors.className = "detectors";
  detectors.textContent = "Detektoren: " + entry.detectors.join(", ");
  card.appendChild(detectors);

  const buttons = document.createElement("div");
  buttons.className = "buttons";
  const defs = [
    ["versprecher", "Versprecher"],
    ["bewusst", "Bewusst"],
    ["unsinn", "Unsinn"],
  ];
  defs.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.dataset.urteil = key;
    btn.addEventListener("click", () => setUrteil(index, key));
    buttons.appendChild(btn);
  });
  card.appendChild(buttons);

  const schnittRow = document.createElement("div");
  schnittRow.className = "schnitt-row buttons";
  const schnittDefs = [
    ["erste", "Erste raus"],
    ["zweite", "Zweite raus"],
    ["beide", "Beide raus"],
  ];
  schnittDefs.forEach(([key, label]) => {
    const btn = document.createElement("button");
    btn.textContent = label;
    btn.dataset.schnitt = key;
    btn.addEventListener("click", () => setSchnitt(index, key));
    schnittRow.appendChild(btn);
  });
  card.appendChild(schnittRow);

  const note = document.createElement("input");
  note.type = "text";
  note.placeholder = "Notiz (optional)";
  note.addEventListener("input", () => {
    state[index].notiz = note.value;
  });
  card.appendChild(note);

  return card;
}

function render() {
  const container = document.getElementById("cards");
  ENTRIES.forEach((entry, index) => {
    container.appendChild(buildCard(entry, index));
  });
  updateProgress();

  const totalBytes = ENTRIES.reduce((sum, e) => {
    if (!e.audio_data_uri) return sum;
    return sum + e.audio_data_uri.length;
  }, 0);
  if (totalBytes > SIZE_WARNING_BYTES) {
    const warning = document.getElementById("size-warning");
    warning.textContent = "Warnung: eingebettetes Audio ist groesser als 150 MB (" +
      (totalBytes / (1024 * 1024)).toFixed(1) + " MB). Das Laden dieser Seite kann langsam sein.";
    warning.style.display = "block";
  }
}

function currentCardIndex() {
  const cards = Array.from(document.querySelectorAll(".card"));
  const scrollMid = window.scrollY + window.innerHeight / 2;
  let closest = 0;
  let closestDist = Infinity;
  cards.forEach((card, i) => {
    const rect = card.getBoundingClientRect();
    const top = rect.top + window.scrollY;
    const mid = top + rect.height / 2;
    const dist = Math.abs(mid - scrollMid);
    if (dist < closestDist) {
      closestDist = dist;
      closest = i;
    }
  });
  return closest;
}

document.addEventListener("keydown", (e) => {
  if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
  const index = currentCardIndex();
  if (e.key === "1") { setUrteil(index, "versprecher"); e.preventDefault(); }
  else if (e.key === "2") { setUrteil(index, "bewusst"); e.preventDefault(); }
  else if (e.key === "3") { setUrteil(index, "unsinn"); e.preventDefault(); }
  else if (e.key === "4") { setSchnitt(index, "erste"); e.preventDefault(); }
  else if (e.key === "5") { setSchnitt(index, "zweite"); e.preventDefault(); }
  else if (e.key === "6") { setSchnitt(index, "beide"); e.preventDefault(); }
  else if (e.code === "Space") {
    const audio = document.getElementById("audio-" + index);
    if (audio) {
      if (audio.paused) audio.play(); else audio.pause();
    }
    e.preventDefault();
  }
});

document.getElementById("download-btn").addEventListener("click", () => {
  const payload = ENTRIES.map((entry, index) => ({
    datei: entry.stem,
    eintragsnummer: entry.nr,
    erste_passage: { start_ms: entry.first.start_ms, end_ms: entry.first.end_ms },
    zweite_passage: { start_ms: entry.second.start_ms, end_ms: entry.second.end_ms },
    scores: { utterance: entry.utterance_score, boundary: entry.boundary_score },
    detektoren: entry.detectors,
    urteil: state[index].urteil,
    schnitt: state[index].schnitt,
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


def build_review_html(entries: list[ReviewEntry]) -> str:
    """Render the single self-contained review HTML page for ``entries``."""
    entries_json = json.dumps([_entry_to_js_dict(e) for e in entries])
    return _TEMPLATE.replace("__ENTRIES_JSON__", entries_json).replace(
        "__SIZE_WARNING_BYTES__", str(_SIZE_WARNING_BYTES)
    )
