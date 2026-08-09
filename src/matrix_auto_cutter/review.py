"""Escaped local review document with bounded, canonical browser previews."""

from __future__ import annotations

import html
import json
import os
from contextlib import suppress
from pathlib import Path

from matrix_auto_cutter.approval import ApprovalGateResult, inspect_approval_state
from matrix_auto_cutter.atomic import replace_atomically
from matrix_auto_cutter.cut_proposal import CutProposal, ProposalFailed, load_proposal
from matrix_auto_cutter.selection import SelectionReady, ensure_selection

REVIEW_FILE_NAME = "review.html"


def review_path_for(proposal_path: Path) -> Path:
    """Return the deterministic non-authoritative review location."""
    if proposal_path.name != "cut-proposal.json":
        raise ValueError("proposal path must end in cut-proposal.json")
    return proposal_path.with_name(REVIEW_FILE_NAME)


def _format_duration(milliseconds: int) -> str:
    hours, remainder = divmod(max(0, milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c")


_STYLE = """
:root {
  color-scheme: dark;
  font-family: system-ui, sans-serif;
}
body {
  max-width: 1120px;
  margin: auto;
  padding: 20px;
  background: #101316;
  color: #eef2f5;
}
button, input { font: inherit; }
button { padding: 8px 11px; margin: 3px; cursor: pointer; }
button:focus, input:focus { outline: 3px solid #ffe083; outline-offset: 2px; }
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 9px;
}
.card, .cut { background: #1b2026; border-radius: 8px; padding: 12px; }
.cut[aria-current="true"] { outline: 3px solid #70c7ff; }
.disabled { border-left: 6px solid #aaa; }
.enabled { border-left: 6px solid #51d88a; }
video { width: 100%; max-height: 570px; background: #000; }
.timeline {
  position: relative;
  height: 42px;
  margin: 12px 0;
  background: #303941;
  border-radius: 4px;
}
.mark, .position { position: absolute; top: 0; height: 100%; }
.cutzone {
  background: repeating-linear-gradient(
    45deg,
    #8b3a3a,
    #8b3a3a 7px,
    #6f2b2b 7px,
    #6f2b2b 14px
  );
}
.position { width: 3px; background: #ffe083; }
.legend { font-size: .92rem; }
.status { padding: 8px; background: #202a34; }
code { overflow-wrap: anywhere; }
.muted { color: #b6c0ca; }
"""


_SCRIPT = """
"use strict";
const cuts = __CUTS__;
const sourceDuration = __DURATION__;
const apiPrefix = __API_PREFIX__;
let selectionDigest = __SELECTION_DIGEST__;
let lastConfirmed = cuts.map((cut) => cut.enabled);
let index = 0;
let mode = "idle";
let endAt = 0;
let seekToken = 0;
let seekIssued = false;
const video = document.querySelector("#source-video");
const stateLabel = document.querySelector("#preview-state");
const formatTime = (seconds) => new Date(Math.max(0, seconds) * 1000)
  .toISOString().slice(11, 23);
const current = () => cuts[index];
const bounds = (cut) => ({
  start: Math.max(0, cut.start - 3),
  end: Math.min(sourceDuration, cut.end + 3),
});
function setState(next, description) {
  mode = next;
  stateLabel.textContent = next;
  document.querySelector("#preview-description").textContent = description;
}
function stop(completed = false) {
  video.pause();
  seekToken += 1;
  const nextState = completed ? "completed" : "idle";
  const description = completed
    ? "Vorschau automatisch beendet."
    : "Keine Vorschau aktiv.";
  setState(nextState, description);
}
function render() {
  if (!cuts.length) {
    document.querySelector("#counter").textContent = "Keine renderbaren Cuts";
    return;
  }
  const cut = current();
  const active = cuts.filter((item) => item.enabled);
  document.querySelector("#counter").textContent = (
    `${index + 1} von ${cuts.length}`
  );
  document.querySelector("#counts").textContent = (
    `${active.length} aktiviert · ${cuts.length - active.length} deaktiviert`
  );
  const selectedDuration = active.reduce((sum, item) => sum + item.duration, 0) / 1000;
  document.querySelector("#savings").textContent = formatTime(selectedDuration);
  document.querySelector("#remaining").textContent = formatTime(
    sourceDuration - selectedDuration,
  );
  document.querySelector("#cut-title").textContent = (
    `Schnitt ${index + 1} · ${cut.id}`
  );
  document.querySelector("#cut-detail").textContent = (
    `Start ${cut.startText} · Ende ${cut.endText} · `
    + `Dauer ${formatTime(cut.duration / 1000)} · `
    + `Status: ${cut.enabled ? "aktiviert" : "deaktiviert"}`
  );
  document.querySelector("#cut-confidence").textContent = cut.confidence;
  const box = document.querySelector("#cut");
  box.className = `cut ${cut.enabled ? "enabled" : "disabled"}`;
  box.setAttribute("aria-current", "true");
  document.querySelector("#toggle").textContent = cut.enabled
    ? "Cut deaktivieren"
    : "Cut aktivieren";
}
function adoptCanonical(body) {
  const canonical = new Map(
    body.candidates.map((item) => [item.candidate_id, item.enabled]),
  );
  cuts.forEach((cut) => {
    if (!canonical.has(cut.id)) {
      throw Error("Kanonische Auswahl ist unvollständig.");
    }
    cut.enabled = canonical.get(cut.id);
  });
  selectionDigest = body.selection_digest;
  lastConfirmed = cuts.map((cut) => cut.enabled);
  document.querySelector("#digest").textContent = (
    `${selectionDigest.slice(0, 16)}…`
  );
  render();
}
async function persist() {
  try {
    const enabled = Object.fromEntries(cuts.map((cut) => [cut.id, cut.enabled]));
    const response = await fetch(`${apiPrefix}/selection`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        enabled,
        expected_selection_digest: selectionDigest,
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      throw Error(body.message || "Speichern fehlgeschlagen");
    }
    adoptCanonical(body);
  } catch (error) {
    cuts.forEach((cut, position) => {
      cut.enabled = lastConfirmed[position];
    });
    render();
    setState("failed", `Auswahl konnte nicht gespeichert werden: ${error.message}`);
  }
}
async function hydrate() {
  if (!apiPrefix) return;
  try {
    const response = await fetch(`${apiPrefix}/selection`);
    const body = await response.json();
    if (!response.ok) {
      throw Error(body.message || "Auswahl konnte nicht geladen werden");
    }
    adoptCanonical(body);
  } catch (error) {
    setState("failed", `Auswahl konnte nicht geladen werden: ${error.message}`);
  }
}
function begin(kind) {
  if (!cuts.length) return;
  stop(false);
  const cut = current();
  const window = bounds(cut);
  const token = ++seekToken;
  seekIssued = false;
  endAt = window.end;
  const nextState = kind === "original"
    ? "original_pre_roll"
    : "cut_preview_pre_roll";
  const description = kind === "original"
    ? "Originalbereich: Vorlauf"
    : "Schnittergebnis: Vorlauf";
  setState(nextState, description);
  video.currentTime = window.start;
  video.addEventListener("seeked", function once() {
    if (token === seekToken) {
      video.play().catch(() => {
        setState("failed", "Video konnte nicht abgespielt werden.");
      });
    }
  }, { once: true });
}
video.addEventListener("timeupdate", () => {
  document.querySelector("#position").textContent = formatTime(video.currentTime);
  if (!cuts.length || ["idle", "completed", "failed"].includes(mode)) {
    return;
  }
  const cut = current();
  if (mode === "cut_preview_pre_roll" && video.currentTime >= cut.start && !seekIssued) {
    seekIssued = true;
    setState("cut_preview_seek", "Schnittergebnis: überspringe kanonischen Cut-Bereich");
    video.pause();
    video.currentTime = cut.end;
    video.addEventListener("seeked", () => {
      setState("cut_preview_post_roll", "Schnittergebnis: Nachlauf");
      video.play();
    }, { once: true });
  }
  if (video.currentTime >= endAt - .002) stop(true);
});
video.addEventListener("error", () => {
  setState("failed", "Quelle fehlt oder kann nicht gelesen werden.");
});
document.querySelector("#original").onclick = () => begin("original");
document.querySelector("#cut-preview").onclick = () => begin("cut");
document.querySelector("#previous").onclick = () => {
  stop();
  index = Math.max(0, index - 1);
  render();
};
document.querySelector("#next").onclick = () => {
  stop();
  index = Math.min(cuts.length - 1, index + 1);
  render();
};
document.querySelector("#toggle").onclick = () => {
  current().enabled = !current().enabled;
  persist();
  render();
};
document.querySelector("#all-on").onclick = () => {
  cuts.forEach((cut) => {
    cut.enabled = true;
  });
  persist();
  render();
};
document.querySelector("#all-off").onclick = () => {
  cuts.forEach((cut) => {
    cut.enabled = false;
  });
  persist();
  render();
};
render();
hydrate();
"""


def render_review_html(
    proposal: CutProposal,
    gate: ApprovalGateResult,
    selection: SelectionReady | None = None,
    *,
    media_url: str | None = None,
    api_prefix: str | None = None,
) -> bytes:
    """Render the local player; proposal frames remain the only cut authority."""
    enabled = (
        {item.candidate_id for item in selection.selection.candidates if item.enabled}
        if selection
        else {item.candidate_id for item in proposal.proposed_cuts}
    )
    savings = (
        selection.selection.selected_savings_ms if selection else proposal.total_proposed_savings_ms
    )
    cuts = [
        {
            "id": item.candidate_id,
            "start": item.start_frame / 60,
            "end": item.end_frame / 60,
            "startText": item.start_timecode,
            "endText": item.end_timecode,
            "duration": item.duration_ms,
            "enabled": item.candidate_id in enabled,
            "confidence": (
                "Konservativer zusammenhängender Silence-/Dead-Air-Bereich; "
                "Protection frei"
            ),
        }
        for item in proposal.proposed_cuts
    ]
    script = (
        _SCRIPT.replace("__CUTS__", _json(cuts))
        .replace("__DURATION__", str(proposal.source_duration_ms / 1000))
        .replace("__API_PREFIX__", _json(api_prefix))
        .replace(
            "__SELECTION_DIGEST__", _json(selection.selection.selection_digest if selection else "")
        )
    )
    digest = selection.selection.selection_digest if selection else ""
    source_url = media_url or Path(proposal.source_path).as_uri()
    source_name = Path(proposal.source_path).name
    document = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta name="referrer" content="no-referrer">
  <title>Matrix Review</title>
  <style>{_STYLE}</style>
</head>
<body>
  <h1>Schnittvorschlag prüfen</h1>
  <p class="status"><strong>Die Rohaufnahme bleibt unverändert.</strong>
    Es wurde noch nicht gerendert.</p>
  <div class="grid">
    <div class="card"><strong>Cut</strong><br><span id="counter">-</span></div>
    <div class="card"><strong>Aktive / deaktivierte</strong><br><span id="counts">-</span></div>
    <div class="card"><strong>Ausgewählte Kürzung</strong><br>
      <span id="savings">{_format_duration(savings)}</span></div>
    <div class="card"><strong>Erwartete Ausgabe</strong><br>
      <span id="remaining">{_format_duration(proposal.source_duration_ms - savings)}</span></div>
  </div>
  <p>Selection-Digest: <code id="digest">{_escape(digest[:16])}…</code></p>
  <p class="muted">Quelle: {_escape(source_name)}</p>
  <video id="source-video" controls preload="metadata" src="{_escape(source_url)}"></video>
  <p class="status">Zustand: <strong id="preview-state">idle</strong> ·
    Position: <strong id="position">00:00:00.000</strong> ·
    <span id="preview-description">Keine Vorschau aktiv.</span></p>
  <p>
    <button id="previous">← Vorheriger Schnitt</button>
    <button id="next">Nächster Schnitt →</button>
    <button id="original" title="Im Video prüfen">Originalbereich abspielen</button>
    <button id="cut-preview">Schnittergebnis abspielen</button>
    <button id="toggle">Cut deaktivieren</button>
    <button id="all-on">Alle aktivieren</button>
    <button id="all-off">Alle deaktivieren</button>
  </p>
  <article id="cut" class="cut">
    <h2 id="cut-title"></h2><p id="cut-detail"></p><p id="cut-confidence"></p>
  </article>
  <p>Proposal-ID: <code>{_escape(proposal.proposal_id)}</code></p>
  <script>{script}</script>
</body>
</html>"""
    return document.encode("utf-8")


def write_review(proposal_path: Path, *, api_prefix: str | None = None) -> Path:
    """Atomically write one review document from the canonical current selection."""
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        raise ValueError(loaded.message_de)
    selection = ensure_selection(proposal_path)
    data = render_review_html(
        loaded.proposal,
        inspect_approval_state(proposal_path),
        selection if isinstance(selection, SelectionReady) else None,
        api_prefix=api_prefix,
    )
    target = review_path_for(proposal_path)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # review.html haelt der Browser offen; ohne Wiederholung scheitert das
        # Neuschreiben, waehrend die Seite angezeigt wird.
        replace_atomically(temporary, target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return target
