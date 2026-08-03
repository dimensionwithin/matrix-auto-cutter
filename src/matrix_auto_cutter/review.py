"""Escaped static local review for one immutable proposal generation."""

from __future__ import annotations

import html
import json
import os
from contextlib import suppress
from pathlib import Path

from matrix_auto_cutter.approval import ApprovalGateResult, inspect_approval_state
from matrix_auto_cutter.cut_proposal import CutProposal, ProposalFailed, load_proposal

REVIEW_FILE_NAME = "review.html"


def review_path_for(proposal_path: Path) -> Path:
    """Derive the static review path from the generation directory."""
    if proposal_path.name != "cut-proposal.json":
        raise ValueError("proposal path must end in cut-proposal.json")
    return proposal_path.with_name(REVIEW_FILE_NAME)


def _format_duration(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def render_review_html(proposal: CutProposal, gate: ApprovalGateResult) -> bytes:
    """Render a standalone escaped document with local video seek controls."""
    expected_remaining = max(0, proposal.source_duration_ms - proposal.total_proposed_savings_ms)
    source_uri = Path(proposal.source_path).as_uri()
    cuts = (
        "".join(
            f"""
        <article class="cut" id="{_e(item.candidate_id)}">
          <h3>Schnitt {index}: {_e(item.start_timecode)} bis {_e(item.end_timecode)}</h3>
          <p><strong>Dauer:</strong> {_e(_format_duration(item.duration_ms))}</p>
          <p><strong>Grund:</strong> Konservativer zusammenhängender Silence-/Dead-Air-Bereich</p>
          <p><strong>Audio-Evidence:</strong> FFmpeg silencedetect,
             {_e(item.audio_evidence.threshold_db)} dB,
             Rohstille {_e(_format_duration(item.audio_evidence.raw_silence_start_ms))} bis
             {_e(_format_duration(item.audio_evidence.raw_silence_end_ms))}
             ({_e(_format_duration(item.audio_evidence.raw_silence_duration_ms))})</p>
          <p><strong>Handles:</strong> {_e(item.applied_handles.before_ms)} ms vorher,
             {_e(item.applied_handles.after_ms)} ms nachher ·
             <strong>Protection:</strong> keine blockierende Überlappung</p>
          <button type="button" data-seek="{item.audio_evidence.raw_silence_start_ms / 1000:.3f}">
            Im Video prüfen
          </button>
        </article>
        """
            for index, item in enumerate(proposal.proposed_cuts, start=1)
        )
        or '<p class="empty">Keine zeitentfernenden Schnitte vorgeschlagen.</p>'
    )
    rejections = (
        "".join(
            f"<li>{_e(item.reason)}: {_e(item.count)}</li>" for item in proposal.rejection_counts
        )
        or "<li>Keine verworfenen Kandidaten.</li>"
    )
    seek_values = json.dumps(
        [item.audio_evidence.raw_silence_start_ms / 1000 for item in proposal.proposed_cuts]
    ).replace("<", "\\u003c")
    decision_label = {
        "pending": "Noch keine Entscheidung",
        "approved": "Ausdrücklich freigegeben",
        "rejected": "Ausdrücklich abgelehnt",
    }[gate.decision]
    document = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; media-src file:;
                 style-src 'unsafe-inline'; script-src 'unsafe-inline'">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Matrix Auto Cutter Review - {_e(proposal.source_identity.file_name)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0 auto; max-width: 1100px; padding: 24px;
            background: #101316; color: #eef2f5; }}
    .safety {{ border: 2px solid #51d88a; background: #10281c; padding: 14px; border-radius: 8px; }}
    .warning {{ border-left: 5px solid #f5b942; padding: 10px 14px; background: #292311; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(210px,1fr)); gap: 10px; }}
    .metric,.cut {{ background: #1b2026; padding: 14px; border-radius: 8px; }}
    video {{ width: 100%; max-height: 620px; background: #000; }}
    code {{ overflow-wrap: anywhere; }}
    button {{ padding: 9px 13px; cursor: pointer; }}
    .muted {{ color: #aab4bf; }}
  </style>
</head>
<body>
  <h1>Schnittvorschlag prüfen</h1>
  <div class="safety"><strong>Die Rohaufnahme bleibt unverändert.</strong><br>
    Es wurde noch nicht gerendert und keine neue Videodatei erzeugt.
    Dies ist nur ein Vorschlag.</div>
  <p class="warning"><strong>Approval:</strong> {_e(decision_label)}. {_e(gate.reason)}<br>
    Freigabe oder Ablehnung erfolgt ausschließlich über die lokale Matrix-Review-Anwendung,
    nicht durch Schließen dieser Seite.</p>
  <h2>Aufnahme</h2>
  <p><strong>Datei:</strong> {_e(proposal.source_identity.file_name)}<br>
     <strong>Pfad:</strong> <code>{_e(proposal.source_path)}</code></p>
  <div class="grid">
    <div class="metric"><strong>Status</strong><br>{_e(proposal.status)}</div>
    <div class="metric"><strong>Dauer</strong><br>
      {_e(_format_duration(proposal.source_duration_ms))}</div>
    <div class="metric"><strong>Schnitte</strong><br>{proposal.total_proposed_cuts}</div>
    <div class="metric"><strong>Kürzung</strong><br>
      {_e(_format_duration(proposal.total_proposed_savings_ms))}</div>
    <div class="metric"><strong>Erwartete Restdauer</strong><br>
      {_e(_format_duration(expected_remaining))}</div>
  </div>
  <h2>Lokale Videoprüfung</h2>
  <video id="source-video" controls preload="metadata" src="{_e(source_uri)}"></video>
  <p class="muted">Die Sprungschaltflächen positionieren nur diese lokale Rohaufnahme;
    sie erzeugen keine Clips.</p>
  <h2>Vorgeschlagene Intervalle</h2>
  {cuts}
  <h2>Verworfene Kandidaten / Schutzkonflikte</h2>
  <ul>{rejections}</ul>
  <h2>Identifikation</h2>
  <p><strong>Proposal-ID:</strong> <code>{_e(proposal.proposal_id)}</code><br>
     <strong>Proposal-Digest:</strong> <code>{_e(proposal.proposal_digest[:16])}…</code><br>
     <strong>SourceIdentity-Digest:</strong> <code>{_e(proposal.source_identity_digest)}</code><br>
     <strong>Sidecar-SHA-256:</strong> <code>{_e(proposal.sidecar_sha256)}</code></p>
  <script>
    "use strict";
    const candidateStarts = {seek_values};
    const video = document.getElementById("source-video");
    document.querySelectorAll("button[data-seek]").forEach((button) => {{
      button.addEventListener("click", () => {{
        const target = Number(button.dataset.seek);
        if (Number.isFinite(target) && candidateStarts.includes(target)) {{
          video.currentTime = Math.max(0, target - 0.5);
          video.play().catch(() => {{}});
          video.scrollIntoView({{behavior: "smooth", block: "center"}});
        }}
      }});
    }});
  </script>
</body>
</html>
"""
    return document.encode("utf-8")


def write_review(proposal_path: Path) -> Path:
    """Revalidate proposal and atomically create/update its non-authoritative review."""
    loaded = load_proposal(proposal_path)
    if isinstance(loaded, ProposalFailed):
        raise ValueError(loaded.message_de)
    target = review_path_for(proposal_path)
    data = render_review_html(loaded.proposal, inspect_approval_state(proposal_path))
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return target
