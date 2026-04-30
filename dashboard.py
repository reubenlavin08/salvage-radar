"""Local web dashboard for cl_watcher. Stdlib only.

Run:
    .venv/Scripts/python.exe dashboard.py
Open: http://localhost:8765
"""
import json
import os
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config

PORT = 8765
TARGET_BACKFILL_FALLBACK = 1376  # used only if meta.current_target absent
TOP_LIMIT = 30
RECENT_LIMIT = 12

# Appraiser DB sits next to cl_watcher's state.db, in a subdir.
APPRAISAL_DB_PATH = (Path(os.environ.get("LOCALAPPDATA",
                                         str(Path.home())))
                     / "cl_watcher" / "appraiser" / "appraisal.db")
APPRAISAL_TOP_LIMIT = 50

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>cl_watcher</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {
    --bg: #ffffff;
    --fg: #0a0a0a;
    --muted: #6b7280;
    --border: #e5e7eb;
    --row-hover: #f9fafb;
    --A: #2563eb;
    --B: #059669;
    --C: #475569;
    --D: #d97706;
    --R: #7c3aed;
    --X: #9ca3af;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0a0a0a;
      --fg: #f5f5f5;
      --muted: #9ca3af;
      --border: #1f2937;
      --row-hover: #111827;
    }
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 14px; line-height: 1.5;
  }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 32px 24px 64px; }
  header { display: flex; justify-content: space-between; align-items: baseline;
           margin-bottom: 8px; }
  h1 { font-size: 18px; font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  .subtitle { color: var(--muted); font-size: 12px; }
  .timestamp { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }

  .progress-block { margin: 24px 0; }
  .progress-meta { display: flex; justify-content: space-between;
                   color: var(--muted); font-size: 12px; margin-bottom: 6px;
                   font-variant-numeric: tabular-nums; }
  .bar-track { height: 6px; background: var(--border); border-radius: 3px;
               overflow: hidden; }
  .bar-fill { height: 100%; background: var(--fg); transition: width 400ms ease; }
  .bar-secondary { background: var(--A); opacity: 0.7; }
  .phase-block { display: flex; align-items: center; gap: 12px;
                 margin: 16px 0 8px; padding: 8px 12px;
                 background: var(--row-hover); border-radius: 6px;
                 border: 1px solid var(--border); }
  .phase-label { font-weight: 600; font-size: 13px; }
  .phase-label.active::before { content: "● "; color: var(--B); animation: pulse 1.4s ease infinite; }
  .phase-label.idle::before { content: "○ "; color: var(--muted); }
  .phase-detail { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  .muted { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
  details.body-detail { margin-top: 6px; }
  details.body-detail summary { cursor: pointer; color: var(--muted); font-size: 11px; }
  details.body-detail .body-text { margin-top: 4px; padding: 8px;
                                    background: var(--row-hover);
                                    border-left: 2px solid var(--border);
                                    font-size: 12px; color: var(--muted);
                                    white-space: pre-wrap;
                                    max-height: 240px; overflow: auto; }

  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 32px; }
  .chip { display: inline-flex; align-items: center; gap: 6px;
          padding: 4px 10px; border: 1px solid var(--border);
          border-radius: 999px; font-size: 12px;
          font-variant-numeric: tabular-nums; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-A { background: var(--A); }
  .dot-B { background: var(--B); }
  .dot-C { background: var(--C); }
  .dot-D { background: var(--D); }
  .dot-R { background: var(--R); }
  .dot-X { background: var(--X); }
  .chip .match { color: var(--muted); }

  h2 { font-size: 13px; font-weight: 600; margin: 32px 0 12px;
       text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }

  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th { text-align: left; font-weight: 500; color: var(--muted);
             font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
             padding: 8px 12px; border-bottom: 1px solid var(--border); }
  tbody td { padding: 10px 12px; border-bottom: 1px solid var(--border);
             vertical-align: top; }
  tbody tr:hover { background: var(--row-hover); }
  td.num { font-variant-numeric: tabular-nums; text-align: right;
           white-space: nowrap; }
  td.title a { color: var(--fg); text-decoration: none; }
  td.title a:hover { text-decoration: underline; }
  .tier-pill { display: inline-block; padding: 1px 7px; border-radius: 4px;
               font-size: 11px; font-weight: 500; color: white;
               font-variant-numeric: tabular-nums; }
  .tier-A { background: var(--A); }
  .tier-B { background: var(--B); }
  .tier-C { background: var(--C); }
  .tier-D { background: var(--D); }
  .tier-needs_review { background: var(--R); }
  .tier-EXCLUDE { background: var(--X); }
  .tier-fetch_failed, .tier-unknown { background: var(--X); opacity: 0.6; }
  .geo-mark { color: var(--muted); font-size: 11px; margin-left: 4px; }
  .price-uncertain { color: var(--D); }
  .empty { color: var(--muted); padding: 16px 12px; font-style: italic; }
  footer { margin-top: 48px; color: var(--muted); font-size: 11px; }

  /* Appraiser section */
  .appraisal-banner { display: flex; align-items: center; gap: 12px;
                      padding: 10px 14px; border: 1px solid var(--border);
                      border-radius: 6px; background: var(--row-hover);
                      margin: 16px 0 8px; font-size: 13px; flex-wrap: wrap; }
  .appraisal-banner .label { font-weight: 600; }
  .appraisal-banner .meta { color: var(--muted); font-variant-numeric: tabular-nums; }
  .reco-pill { display: inline-block; padding: 2px 8px; border-radius: 4px;
               font-size: 11px; font-weight: 600; color: white;
               letter-spacing: 0.04em; }
  .reco-BUY    { background: var(--B); }
  .reco-MAYBE  { background: var(--D); }
  .reco-SKIP   { background: var(--X); }
  .reco-REJECTED { background: var(--muted); }
  .conf-pill { display: inline-block; padding: 1px 6px; border-radius: 3px;
               font-size: 10px; color: var(--muted); border: 1px solid var(--border);
               text-transform: uppercase; letter-spacing: 0.05em; }
  .ratio-cell { font-weight: 600; font-variant-numeric: tabular-nums; }
  .ratio-good { color: var(--B); }
  .ratio-meh  { color: var(--D); }
  .ratio-bad  { color: var(--X); }
  .summary-cell { color: var(--muted); font-size: 12px; max-width: 460px;
                  white-space: pre-wrap; }
  /* Make distance the visual anchor of each row — large, bold, color-
     coded by tier band. */
  .dist-cell { font-weight: 700; font-size: 15px;
               font-variant-numeric: tabular-nums; white-space: nowrap; }
  .dist-A { color: var(--A); }      /* <= 2.5 km, very close */
  .dist-B { color: var(--B); }      /* <= 4.5 km */
  .dist-C { color: var(--C); }      /* <= 7 km */
  .dist-D { color: var(--D); }      /* <= 9 km */
  .dist-far { color: var(--X); }    /* > 9 km, faded */
  .dist-unknown { color: var(--muted); font-weight: 400; font-style: italic; }
  /* All anchors in the appraiser section use the foreground color so
     they're readable on dark backgrounds (default browser blue is too
     dark against #0a0a0a). Hover gets an underline. */
  #appraiser-section a,
  #appr-top-rows a,
  #appr-skip-rows a,
  #appr-live-rows a {
    color: var(--fg);
    text-decoration: none;
    border-bottom: 1px dotted var(--muted);
  }
  #appraiser-section a:hover,
  #appr-top-rows a:hover,
  #appr-skip-rows a:hover,
  #appr-live-rows a:hover { border-bottom-style: solid; }

  /* "Recent vs. archive" headings + collapsible archive blocks. */
  .window-label { font-size: 11px; font-weight: 400; color: var(--muted);
                  letter-spacing: 0.04em; margin-left: 6px;
                  text-transform: uppercase; }
  .window-count { font-size: 12px; font-weight: 400; color: var(--muted);
                  margin-left: 8px; font-variant-numeric: tabular-nums; }
  /* "Times" / "Posted" column cells — keep them compact and muted so
     the more actionable Rec/Distance columns stay visually dominant.
     The two-line stack (posted vs. appraised) uses .t-line + .t-label. */
  .posted-cell { color: var(--muted); font-size: 12px; white-space: nowrap;
                 font-variant-numeric: tabular-nums; }
  .t-line { line-height: 1.4; }
  .t-label { color: var(--muted); opacity: 0.7;
             text-transform: uppercase; font-size: 10px;
             letter-spacing: 0.05em; margin-right: 4px; }

  /* Scraper heartbeat — shown at the top of the Indexed tab. Status pill
     turns from green ("alive") to amber ("slow") to red ("stale") based
     on how long ago the last row landed. */
  .heartbeat { display: flex; align-items: center; gap: 14px;
               padding: 10px 14px; margin: 0 0 16px;
               border: 1px solid var(--border); border-radius: 6px;
               background: var(--row-hover); font-size: 13px;
               flex-wrap: wrap; }
  .heartbeat-line { font-variant-numeric: tabular-nums; }
  .heartbeat-line.muted { color: var(--muted); }
  .heartbeat-pill { display: inline-block; padding: 3px 10px;
                    border-radius: 4px; font-size: 11px; font-weight: 700;
                    color: white; letter-spacing: 0.05em;
                    text-transform: uppercase; }
  .hb-alive { background: var(--B); }
  .hb-slow  { background: var(--D); }
  .hb-stale { background: var(--X); }
  .hb-unknown { background: var(--muted); }

  /* Tabs — three views: indexed area (cl_watcher live scan), appraised
     (last 24 h BUY/MAYBE/SKIP), and archive (older). The active tab is
     decided by URL hash if present, else by port (8766 → appraised,
     anything else → indexed). */
  .tabs { display: flex; gap: 4px; margin: 8px 0 24px;
          border-bottom: 1px solid var(--border);
          padding-bottom: 0; }
  .tabs button {
    background: none; border: none; padding: 10px 16px;
    color: var(--muted); font: inherit; font-size: 13px; font-weight: 600;
    letter-spacing: 0.02em;
    cursor: pointer; border-bottom: 2px solid transparent;
    margin-bottom: -1px; transition: color 0.12s, border-color 0.12s;
  }
  .tabs button:hover { color: var(--fg); }
  .tabs button.active { color: var(--fg); border-bottom-color: var(--fg); }
  .tabs .tab-count { color: var(--muted); font-weight: 400; margin-left: 6px;
                     font-variant-numeric: tabular-nums; }
  .tabs button.active .tab-count { color: var(--muted); }
  .tab-pane { display: none; }
  .tab-pane.active { display: block; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <h1>cl_watcher</h1>
      <div class="subtitle">Craigslist Vancouver robotics-parts watcher</div>
    </div>
    <div class="timestamp" id="ts">—</div>
  </header>

  <nav class="tabs" id="tabs">
    <button data-tab="indexed">Indexed area</button>
    <button data-tab="appraised">Appraised
      <span class="tab-count" id="tab-count-appraised"></span>
    </button>
    <button data-tab="archive">Archive
      <span class="tab-count" id="tab-count-archive"></span>
    </button>
  </nav>

  <section class="tab-pane cl-section" data-tab="indexed">
    <!-- Scraper heartbeat — shows whether cl_watcher is up-to-date even
         when nothing matches the user's specs. -->
    <div class="heartbeat" id="heartbeat">
      <span class="heartbeat-pill" id="hb-pill">—</span>
      <span class="heartbeat-line">
        Last insert: <strong id="hb-last">—</strong>
      </span>
      <span class="heartbeat-line muted">
        <span id="hb-15m">—</span> in 15 min ·
        <span id="hb-1h">—</span> in 1 h ·
        <span id="hb-24h">—</span> in 24 h
      </span>
    </div>

    <div class="phase-block">
      <span class="phase-label" id="phase">—</span>
      <span class="phase-detail" id="phase-detail"></span>
    </div>

    <div class="progress-block">
      <div class="progress-meta">
        <span>Listings indexed:
              <strong><span id="processed">—</span></strong></span>
        <span><strong><span id="matches">—</span></strong> matches</span>
      </div>
      <div class="bar-track"><div class="bar-fill" id="bar" style="width:0%"></div></div>
    </div>

    <div class="progress-block" id="body-progress-block" hidden>
      <div class="progress-meta">
        <span>Bodies fetched:
              <strong><span id="bodies-stored">—</span></strong>
              / <span id="bodies-total">—</span>
              (<span id="bodies-missing">—</span> missing)</span>
        <span class="muted" id="bodies-pct">—</span>
      </div>
      <div class="bar-track">
        <div class="bar-fill bar-secondary" id="body-bar" style="width:0%"></div>
      </div>
    </div>

    <div class="chips" id="chips"></div>

    <h2>Top matches by score</h2>
    <table>
      <thead><tr>
        <th class="num" style="text-align:right">Score</th>
        <th>Tier</th>
        <th class="num" style="text-align:right">Dist</th>
        <th class="num" style="text-align:right">Price</th>
        <th class="num" style="text-align:right">Salv</th>
        <th>Neighborhood</th>
        <th>Title</th>
      </tr></thead>
      <tbody id="top-rows"></tbody>
    </table>

    <h2>Recently processed</h2>
    <table>
      <thead><tr>
        <th>Indexed</th>
        <th>Posted</th>
        <th>Tier</th>
        <th class="num" style="text-align:right">Dist</th>
        <th class="num" style="text-align:right">Price</th>
        <th class="num" style="text-align:right">Salv</th>
        <th class="num" style="text-align:right">Score</th>
        <th>Neighborhood</th>
        <th>Title</th>
      </tr></thead>
      <tbody id="recent-rows"></tbody>
    </table>
  </section>

  <section class="tab-pane" data-tab="appraised" id="appraiser-section">
    <h2 id="appraiser-heading" style="margin-top:0">Appraiser — AI salvage valuation</h2>

    <!-- Appraiser heartbeat — shows whether the cycle ran recently and
         what made the cut (BUY/MAYBE) vs. what didn't (SKIP/REJECTED). -->
    <div class="heartbeat" id="appr-heartbeat">
      <span class="heartbeat-pill" id="appr-hb-pill">—</span>
      <span class="heartbeat-line">
        Last cycle: <strong id="appr-hb-last">—</strong>
      </span>
      <span class="heartbeat-line muted">
        <span id="appr-hb-15m">—</span> in 15 min ·
        <span id="appr-hb-1h">—</span> in 1 h ·
        <span id="appr-hb-24h">—</span> in 24 h
      </span>
    </div>

    <div class="appraisal-banner" id="appraiser-banner">
      <span class="label">Status:</span>
      <span id="appr-status" class="meta">loading…</span>
    </div>

    <div class="appraisal-banner" id="appr-live-banner" hidden>
      <span class="label">Live:</span>
      <span id="appr-live-meta" class="meta">—</span>
    </div>

    <h2 style="margin-top:24px">
      Recently appraised — feed <span class="window-label">last 24 h, every result</span>
      <span class="window-count" id="appr-feed-count"></span>
    </h2>
    <table>
      <thead><tr>
        <th>Appraised</th>
        <th>Rec</th>
        <th>Posted</th>
        <th class="num" style="text-align:right">Ask</th>
        <th>Title / Reasoning</th>
      </tr></thead>
      <tbody id="appr-feed-rows"></tbody>
    </table>

    <h2 style="margin-top:24px" id="appr-live-heading" hidden>In-flight preview (not yet aggregated)</h2>
    <table id="appr-live-table" hidden>
      <thead><tr>
        <th>State</th>
        <th>Item kind</th>
        <th class="num" style="text-align:right">Ask</th>
        <th class="num" style="text-align:right">Salvage est.</th>
        <th>Title / Reasoning</th>
      </tr></thead>
      <tbody id="appr-live-rows"></tbody>
    </table>

    <h2 style="margin-top:24px">
      Recently appraised <span class="window-label">last 24 h</span>
      <span class="window-count" id="appr-top-recent-count"></span>
    </h2>
    <table>
      <thead><tr>
        <th>Rec</th>
        <th>Times</th>
        <th class="num" style="text-align:right">Distance</th>
        <th>Tier</th>
        <th class="num" style="text-align:right">Ratio</th>
        <th class="num" style="text-align:right">Ask</th>
        <th class="num" style="text-align:right">Salvage</th>
        <th>Conf</th>
        <th>Neighborhood</th>
        <th>Title / Reasoning</th>
      </tr></thead>
      <tbody id="appr-top-rows"></tbody>
    </table>

    <h2 style="margin-top:32px">
      Recently skipped <span class="window-label">last 24 h, sample of 30</span>
      <span class="window-count" id="appr-skip-recent-count"></span>
    </h2>
    <table>
      <thead><tr>
        <th>Rec</th>
        <th>Times</th>
        <th class="num" style="text-align:right">Distance</th>
        <th class="num" style="text-align:right">Ask</th>
        <th class="num" style="text-align:right">Salvage</th>
        <th>Title / Reason</th>
      </tr></thead>
      <tbody id="appr-skip-rows"></tbody>
    </table>
  </section>

  <section class="tab-pane" data-tab="archive">
    <h2 style="margin-top:0">
      Archive — BUY / MAYBE picks
      <span class="window-label">older than 24 h</span>
      <span class="window-count" id="appr-top-archive-count"></span>
    </h2>
    <table>
      <thead><tr>
        <th>Rec</th>
        <th>Times</th>
        <th class="num" style="text-align:right">Distance</th>
        <th>Tier</th>
        <th class="num" style="text-align:right">Ratio</th>
        <th class="num" style="text-align:right">Ask</th>
        <th class="num" style="text-align:right">Salvage</th>
        <th>Conf</th>
        <th>Neighborhood</th>
        <th>Title / Reasoning</th>
      </tr></thead>
      <tbody id="appr-top-archive-rows"></tbody>
    </table>

    <h2 style="margin-top:32px">
      Archive — skipped <span class="window-label">older than 24 h, sample of 30</span>
      <span class="window-count" id="appr-skip-archive-count"></span>
    </h2>
    <table>
      <thead><tr>
        <th>Rec</th>
        <th>Times</th>
        <th class="num" style="text-align:right">Distance</th>
        <th class="num" style="text-align:right">Ask</th>
        <th class="num" style="text-align:right">Salvage</th>
        <th>Title / Reason</th>
      </tr></thead>
      <tbody id="appr-skip-archive-rows"></tbody>
    </table>
  </section>

  <footer>
    Auto-refreshing every 3 s. DB: <code id="dbpath">—</code>
    · Appraisal DB: <code id="appr-dbpath">—</code>
  </footer>

</div>

<script>
  // Port 8766 = appraiser-focused view. Port 8765 (default) keeps the
  // full cl_watcher dashboard visible.
  if (location.port === '8766') document.body.classList.add('focus-appraiser');

  const TIER_ORDER = ["A","B","C","D","needs_review","unknown","EXCLUDE","fetch_failed"];
  const TIER_DOT = {A:"A",B:"B",C:"C",D:"D",needs_review:"R",unknown:"X",EXCLUDE:"X",fetch_failed:"X"};

  function fmtDist(d) { return d == null ? "—" : d.toFixed(1) + " km"; }
  function fmtPrice(p, unc) {
    if (p == null) return "—";
    return "$" + p + (unc ? '<span class="price-uncertain"> ?</span>' : '');
  }
  function fmtSalv(s) { return s == null ? "—" : "$" + s; }
  function fmtScore(s) { return s == null ? "—" : Math.round(s); }
  function timeShort(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString("en-CA", {hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false});
    } catch(e) { return iso.slice(0,8); }
  }

  function tierPill(t) {
    const safe = (t || "").replace(/[^A-Za-z_]/g,"");
    return `<span class="tier-pill tier-${safe}">${t || "?"}</span>`;
  }

  function geoMark(src) {
    if (src === "coords") return '<span class="geo-mark" title="from map coordinates">●</span>';
    if (src === "string") return '<span class="geo-mark" title="from neighborhood string">~</span>';
    return '';
  }

  function renderChips(breakdown, matches) {
    const c = document.getElementById("chips");
    c.innerHTML = "";
    for (const t of TIER_ORDER) {
      const count = breakdown[t] || 0;
      if (count === 0) continue;
      const m = matches[t] || 0;
      const matchTxt = ["A","B","C","D","needs_review"].includes(t)
        ? `<span class="match">${m} match</span>` : '';
      c.innerHTML += `
        <span class="chip">
          <span class="dot dot-${TIER_DOT[t]}"></span>
          <strong>${t}</strong> ${count} ${matchTxt}
        </span>`;
    }
  }

  function escHTML(s) {
    return (s || "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;",
      '"': "&quot;", "'": "&#39;"
    })[c]);
  }
  function bodyDetail(b) {
    if (!b) return '';
    const trimmed = b.length > 800 ? b.slice(0, 800) + "…" : b;
    return `<details class="body-detail">
      <summary>view body</summary>
      <div class="body-text">${escHTML(trimmed)}</div>
    </details>`;
  }

  function renderTop(rows) {
    const tb = document.getElementById("top-rows");
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="7" class="empty">No matches yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td class="num"><strong>${fmtScore(r.score)}</strong></td>
        <td>${tierPill(r.tier)} ${geoMark(r.geo_source)}</td>
        <td class="num">${fmtDist(r.distance_km)}</td>
        <td class="num">${fmtPrice(r.ask_price, r.price_uncertain)}</td>
        <td class="num">${fmtSalv(r.salvage_estimate)}</td>
        <td>${escHTML(r.neighborhood) || "—"}</td>
        <td class="title">
          <a href="${escHTML(r.link)}" target="_blank" rel="noopener">${escHTML(r.title)}</a>
          ${bodyDetail(r.body)}
        </td>
      </tr>`).join("");
  }

  function renderRecent(rows) {
    const tb = document.getElementById("recent-rows");
    if (!rows.length) {
      tb.innerHTML = '<tr><td colspan="9" class="empty">Nothing yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td class="num">${timeShort(r.first_seen_at)}</td>
        <td class="num posted-cell">${fmtPosted(r.posted_at)}</td>
        <td>${tierPill(r.tier)} ${geoMark(r.geo_source)}</td>
        <td class="num">${fmtDist(r.distance_km)}</td>
        <td class="num">${fmtPrice(r.ask_price, r.price_uncertain)}</td>
        <td class="num">${fmtSalv(r.salvage_estimate)}</td>
        <td class="num">${fmtScore(r.score)}</td>
        <td>${escHTML(r.neighborhood) || "—"}</td>
        <td class="title">
          ${escHTML(r.title)}
          ${bodyDetail(r.body)}
        </td>
      </tr>`).join("");
  }

  // ---------- Time formatting ----------
  // Renders an ISO timestamp like "2026-04-25T17:59:19-0700" as either
  // a relative age ("3 d ago") for recent values or an absolute date
  // ("Apr 25") for older ones. "—" when missing.
  function fmtAge2(s) {
    if (!s) return null;
    const d = new Date(s);
    if (isNaN(d)) return null;
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 0) return d.toLocaleString();
    if (diff < 3600)   return Math.round(diff / 60) + ' min ago';
    if (diff < 86400)  return Math.round(diff / 3600) + ' h ago';
    if (diff < 7 * 86400) return Math.round(diff / 86400) + ' d ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }
  function fmtPosted(s) {
    const v = fmtAge2(s);
    return v ? v : '<span class="muted">—</span>';
  }
  // For appraiser tables — shows BOTH "posted on Craigslist" (when the
  // listing went up) and "appraised by us" (when our pipeline scored it)
  // so you can tell whether stale listings are clogging the queue.
  function fmtTimes(posted_at, run_at) {
    const p = fmtAge2(posted_at);
    const r = fmtAge2(run_at);
    const out = [];
    if (p) out.push(`<div class="t-line"><span class="t-label">posted</span> ${p}</div>`);
    if (r) out.push(`<div class="t-line"><span class="t-label">appr.</span> ${r}</div>`);
    return out.length ? out.join('') : '<span class="muted">—</span>';
  }

  async function refresh() {
    try {
      const r = await fetch("/api/state");
      if (!r.ok) return;
      const d = await r.json();
      document.getElementById("ts").textContent = new Date().toLocaleTimeString();
      document.getElementById("processed").textContent = d.total;
      document.getElementById("matches").textContent = d.total_matches;

      // Phase indicator
      const phaseEl = document.getElementById("phase");
      phaseEl.textContent = d.phase;
      phaseEl.classList.toggle("active", d.phase !== "Idle");
      phaseEl.classList.toggle("idle", d.phase === "Idle");
      const phasePct = d.phase_target
        ? (100 * d.phase_progress / d.phase_target).toFixed(1)
        : 0;
      document.getElementById("phase-detail").textContent =
        d.phase === "Idle" ? "" :
        `${d.phase_progress.toLocaleString()} / ${d.phase_target.toLocaleString()} (${phasePct}%)`;

      // Main listings bar — uses phase target if active, else just total
      const mainPct = d.phase_target
        ? Math.min(100, 100 * d.phase_progress / d.phase_target) : 100;
      document.getElementById("bar").style.width = mainPct + "%";

      // Bodies-fetched bar (shown when there are any rows missing bodies)
      const bp = document.getElementById("body-progress-block");
      if (d.bodies_missing > 0 || d.bodies_stored < d.total) {
        bp.hidden = false;
        document.getElementById("bodies-stored").textContent = d.bodies_stored.toLocaleString();
        document.getElementById("bodies-total").textContent = d.total.toLocaleString();
        document.getElementById("bodies-missing").textContent = d.bodies_missing.toLocaleString();
        const bpct = d.total ? (100 * d.bodies_stored / d.total) : 0;
        document.getElementById("body-bar").style.width = bpct + "%";
        document.getElementById("bodies-pct").textContent = bpct.toFixed(1) + "%";
      } else {
        bp.hidden = true;
      }

      document.getElementById("dbpath").textContent = d.db_path;
      renderChips(d.breakdown, d.matches);
      renderTop(d.top);
      renderRecent(d.recent);
      renderHeartbeat(d);
    } catch(e) { /* ignore transient errors */ }
  }

  function fmtAge(secs) {
    if (secs == null) return '—';
    if (secs < 60)   return Math.round(secs) + ' s ago';
    if (secs < 3600) return Math.round(secs / 60) + ' min ago';
    if (secs < 86400) return (secs / 3600).toFixed(1) + ' h ago';
    return (secs / 86400).toFixed(1) + ' d ago';
  }
  function renderHeartbeat(d) {
    const secs = d.seconds_since_last_insert;
    const pill = document.getElementById('hb-pill');
    let label, cls;
    if (secs == null) {
      label = 'unknown'; cls = 'hb-unknown';
    } else if (secs < 30 * 60) {
      label = 'alive'; cls = 'hb-alive';
    } else if (secs < 2 * 3600) {
      label = 'slow'; cls = 'hb-slow';
    } else {
      label = 'stale'; cls = 'hb-stale';
    }
    pill.textContent = label;
    pill.className = 'heartbeat-pill ' + cls;
    document.getElementById('hb-last').textContent = fmtAge(secs);
    document.getElementById('hb-15m').textContent = (d.inserts_15m ?? '—').toLocaleString();
    document.getElementById('hb-1h').textContent = (d.inserts_1h ?? '—').toLocaleString();
    document.getElementById('hb-24h').textContent = (d.inserts_24h ?? '—').toLocaleString();
  }
  refresh();
  setInterval(refresh, 3000);

  // ---------- Appraiser section ----------
  function fmtMoney(v) {
    if (v == null) return '—';
    return '$' + (Math.round(v * 100) / 100).toFixed(0);
  }
  function ratioClass(r) {
    if (r == null) return 'ratio-bad';
    if (r >= 2.5) return 'ratio-good';
    if (r >= 1.5) return 'ratio-meh';
    return 'ratio-bad';
  }
  function recoPill(r) {
    return `<span class="reco-pill reco-${r}">${r}</span>`;
  }
  function confPill(c) {
    return `<span class="conf-pill">${c || '—'}</span>`;
  }
  function tierMini(t) {
    if (!t) return '—';
    const safe = (t || "").replace(/[^A-Za-z_]/g,"");
    return `<span class="tier-pill tier-${safe}">${t}</span>`;
  }
  function summaryCell(row) {
    const title = escHTML(row.title || row.rss_id || '—');
    const link = row.link
      ? `<a href="${escHTML(row.link)}" target="_blank" rel="noopener">${title}</a>`
      : title;
    const summary = escHTML((row.summary || '').slice(0, 800));
    const detail = row.body ? bodyDetail(row.body) : '';
    return `<div><strong>${link}</strong></div>
            <div class="summary-cell">${summary}</div>
            ${detail}`;
  }

  function distCell(km) {
    if (km == null) return '<td class="num dist-cell dist-unknown">—</td>';
    let cls = 'dist-far';
    if (km <= 2.5) cls = 'dist-A';
    else if (km <= 4.5) cls = 'dist-B';
    else if (km <= 7) cls = 'dist-C';
    else if (km <= 9) cls = 'dist-D';
    return `<td class="num dist-cell ${cls}">${km.toFixed(1)} km</td>`;
  }

  function distBand(km) {
    // Sort key: closer = lower number. Unknown = highest.
    if (km == null) return 99;
    if (km <= 2.5) return 0;
    if (km <= 4.5) return 1;
    if (km <= 7) return 2;
    if (km <= 9) return 3;
    return 4;
  }

  function renderApprTop(rows, tbodyId) {
    tbodyId = tbodyId || 'appr-top-rows';
    const tb = document.getElementById(tbodyId);
    if (!tb) return;
    if (!rows || !rows.length) {
      const msg = tbodyId === 'appr-top-archive-rows'
        ? 'No archive picks yet.'
        : 'No BUY/MAYBE picks in the last 24 h — wait for the next cycle.';
      tb.innerHTML = `<tr><td colspan="10" class="empty">${msg}</td></tr>`;
      return;
    }
    // Re-sort: distance band first, then ratio descending.
    rows = rows.slice().sort((a, b) => {
      const ba = distBand(a.distance_km), bb = distBand(b.distance_km);
      if (ba !== bb) return ba - bb;
      return (b.ratio || 0) - (a.ratio || 0);
    });
    tb.innerHTML = rows.map(r => `
      <tr>
        <td>${recoPill(r.recommendation)}</td>
        <td class="posted-cell">${fmtTimes(r.posted_at, r.run_at)}</td>
        ${distCell(r.distance_km)}
        <td>${tierMini(r.tier)}</td>
        <td class="num ratio-cell ${ratioClass(r.ratio)}">${r.ratio ? r.ratio.toFixed(2) + 'x' : '—'}</td>
        <td class="num">${fmtMoney(r.ask_price)}</td>
        <td class="num">${fmtMoney(r.salvage_realized)}<br><span class="muted">${fmtMoney(r.salvage_low)}–${fmtMoney(r.salvage_high)}</span></td>
        <td>${confPill(r.confidence)}</td>
        <td>${escHTML(r.neighborhood) || '—'}</td>
        <td>${summaryCell(r)}</td>
      </tr>
    `).join('');
  }

  function renderApprSkip(rows, tbodyId) {
    tbodyId = tbodyId || 'appr-skip-rows';
    const tb = document.getElementById(tbodyId);
    if (!tb) return;
    if (!rows || !rows.length) {
      const msg = tbodyId === 'appr-skip-archive-rows'
        ? 'No archive skips yet.'
        : 'No skipped items in the last 24 h.';
      tb.innerHTML = `<tr><td colspan="6" class="empty">${msg}</td></tr>`;
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td>${recoPill(r.recommendation)}</td>
        <td class="posted-cell">${fmtTimes(r.posted_at, r.run_at)}</td>
        ${distCell(r.distance_km)}
        <td class="num">${fmtMoney(r.ask_price)}</td>
        <td class="num">${fmtMoney(r.salvage_realized)}</td>
        <td>${summaryCell(r)}</td>
      </tr>
    `).join('');
  }

  // Chronological feed of every appraisal in the last 24 h. Shows the
  // appraiser is doing work even on cycles that produce no BUY/MAYBE.
  function renderApprFeed(rows) {
    const tb = document.getElementById('appr-feed-rows');
    if (!rows || !rows.length) {
      tb.innerHTML = '<tr><td colspan="5" class="empty">No appraisals in the last 24 h.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td class="posted-cell"><strong>${fmtPosted(r.run_at)}</strong></td>
        <td>${recoPill(r.recommendation)}</td>
        <td class="posted-cell">${fmtPosted(r.posted_at)}</td>
        <td class="num">${fmtMoney(r.ask_price)}</td>
        <td>${summaryCell(r)}</td>
      </tr>
    `).join('');
  }

  function setText(id, v) {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  }

  async function refreshAppr() {
    try {
      const r = await fetch('/api/appraisals');
      if (!r.ok) return;
      const d = await r.json();
      const status = document.getElementById('appr-status');
      const dbp = document.getElementById('appr-dbpath');
      dbp.textContent = d.db_path || '—';
      if (!d.available) {
        status.textContent = d.message || 'unavailable';
        renderApprTop([], 'appr-top-rows');
        renderApprTop([], 'appr-top-archive-rows');
        renderApprSkip([], 'appr-skip-rows');
        renderApprSkip([], 'appr-skip-archive-rows');
        return;
      }
      const recs = d.by_recommendation || {};
      const recsR = d.by_recommendation_recent || {};
      const buy = recs.BUY || 0, maybe = recs.MAYBE || 0,
            skip = recs.SKIP || 0, rej = recs.REJECTED || 0;
      const buyR = recsR.BUY || 0, maybeR = recsR.MAYBE || 0,
            skipR = recsR.SKIP || 0, rejR = recsR.REJECTED || 0;
      const lr = d.last_run ? new Date(d.last_run).toLocaleString() : '—';
      status.innerHTML = `
        <strong>${d.total_recent || 0}</strong> in last 24 h ·
        <span class="reco-pill reco-BUY">BUY ${buyR}</span>
        <span class="reco-pill reco-MAYBE">MAYBE ${maybeR}</span>
        <span class="reco-pill reco-SKIP">SKIP ${skipR}</span>
        <span class="reco-pill reco-REJECTED">REJECTED ${rejR}</span>
        · all-time:
        <span class="reco-pill reco-BUY">BUY ${buy}</span>
        <span class="reco-pill reco-MAYBE">MAYBE ${maybe}</span>
        <span class="reco-pill reco-SKIP">SKIP ${skip}</span>
        <span class="reco-pill reco-REJECTED">REJECTED ${rej}</span>
        · last run: <span class="meta">${lr}</span>
      `;
      renderApprHeartbeat(d);
      const topR = d.top_recent || [];
      const topA = d.top_archive || [];
      const skipRR = d.skipped_recent || [];
      const skipAA = d.skipped_archive || [];
      renderApprTop(topR, 'appr-top-rows');
      renderApprTop(topA, 'appr-top-archive-rows');
      renderApprSkip(skipRR, 'appr-skip-rows');
      renderApprSkip(skipAA, 'appr-skip-archive-rows');
      const feed = d.feed || [];
      renderApprFeed(feed);
      setText('appr-feed-count', feed.length ? `${feed.length} shown` : '0');
      setText('appr-top-recent-count', topR.length ? `${topR.length} shown` : '0');
      setText('appr-top-archive-count', topA.length ? `${topA.length} shown` : '0');
      setText('appr-skip-recent-count', skipRR.length ? `${skipRR.length} shown` : '0');
      setText('appr-skip-archive-count', skipAA.length ? `${skipAA.length} shown` : '0');
      // Tab labels — show count next to the tab name so the user can see
      // at a glance what each tab contains without having to switch.
      setText('tab-count-appraised', d.total_recent != null ? `${d.total_recent}` : '');
      const archiveCount = (d.total || 0) - (d.total_recent || 0);
      setText('tab-count-archive', archiveCount > 0 ? `${archiveCount}` : '');
      renderApprLive(d.live || {});
    } catch (e) { /* ignore */ }
  }

  function renderApprHeartbeat(d) {
    const secs = d.seconds_since_last_run;
    const pill = document.getElementById('appr-hb-pill');
    let label, cls;
    // The appraiser is on a 15-min cron; "stale" thresholds are looser
    // than the scraper since fewer cycles can produce zero-work runs.
    if (secs == null) {
      label = 'no runs'; cls = 'hb-unknown';
    } else if (secs < 60 * 60) {
      label = 'alive'; cls = 'hb-alive';
    } else if (secs < 6 * 3600) {
      label = 'slow'; cls = 'hb-slow';
    } else {
      label = 'stale'; cls = 'hb-stale';
    }
    pill.textContent = label;
    pill.className = 'heartbeat-pill ' + cls;
    document.getElementById('appr-hb-last').textContent = fmtAge(secs);
    document.getElementById('appr-hb-15m').textContent = (d.appraised_15m ?? '—').toLocaleString();
    document.getElementById('appr-hb-1h').textContent = (d.appraised_1h ?? '—').toLocaleString();
    document.getElementById('appr-hb-24h').textContent = (d.appraised_24h ?? '—').toLocaleString();
  }

  function renderApprLive(live) {
    const banner = document.getElementById('appr-live-banner');
    const meta = document.getElementById('appr-live-meta');
    const heading = document.getElementById('appr-live-heading');
    const table = document.getElementById('appr-live-table');
    const tb = document.getElementById('appr-live-rows');
    const queued = live.batches_queued || 0;
    const done = live.batches_done || 0;
    const dropped = live.dropped_count || 0;
    const previewAppr = live.preview_appraised || 0;
    const previewSkip = live.preview_skipped || 0;
    const unagg = live.live_unaggregated_count || 0;
    if (queued === 0 && done === 0 && dropped === 0) {
      banner.hidden = true;
      heading.hidden = true;
      table.hidden = true;
      return;
    }
    banner.hidden = false;
    const inFlight = queued - done;
    meta.innerHTML = `
      <strong>${queued}</strong> batch${queued===1?'':'es'} queued ·
      <strong>${done}</strong> with results so far ·
      <span class="reco-pill reco-BUY">${previewAppr} appraised</span>
      <span class="reco-pill reco-SKIP">${previewSkip} agent-skipped</span>
      · ${dropped} prefilter-dropped
      ${unagg > 0 ? `· <strong>${unagg}</strong> awaiting aggregation` : ''}
    `;
    const rows = live.live_unaggregated || [];
    if (rows.length === 0) {
      heading.hidden = true; table.hidden = true; return;
    }
    heading.hidden = false; table.hidden = false;
    tb.innerHTML = rows.map(r => {
      const state = r.skipped
        ? `<span class="reco-pill reco-SKIP">SKIP</span>`
        : `<span class="reco-pill reco-MAYBE">PREVIEW</span>`;
      const salv = r.skipped
        ? '—'
        : `${fmtMoney(r.salvage_low)}–${fmtMoney(r.salvage_high)}`;
      const title = escHTML(r.title || r.rss_id || '—');
      const link = r.link
        ? `<a href="${escHTML(r.link)}" target="_blank" rel="noopener">${title}</a>`
        : title;
      const summary = escHTML((r.summary || '').slice(0, 600));
      const reason = r.skip_reason ? `<em>(${escHTML(r.skip_reason)})</em> ` : '';
      return `<tr>
        <td>${state}</td>
        <td>${escHTML(r.item_kind || '—')}</td>
        <td class="num">${fmtMoney(r.ask_price)}</td>
        <td class="num">${salv}</td>
        <td>
          <div><strong>${link}</strong></div>
          <div class="summary-cell">${reason}${summary}</div>
        </td>
      </tr>`;
    }).join('');
  }
  refreshAppr();
  setInterval(refreshAppr, 5000);

  // ---------- Tab switching ----------
  function activateTab(name) {
    const panes = document.querySelectorAll('.tab-pane');
    const buttons = document.querySelectorAll('#tabs button');
    let matched = false;
    panes.forEach(p => {
      const m = p.dataset.tab === name;
      p.classList.toggle('active', m);
      if (m) matched = true;
    });
    buttons.forEach(b => {
      b.classList.toggle('active', b.dataset.tab === name);
    });
    if (matched && location.hash.slice(1) !== name) {
      // Update hash without scrolling.
      history.replaceState(null, '', '#' + name);
    }
  }

  // Default tab: URL hash if present, else port-based fallback (8766
  // boots straight into the appraiser view, anything else into the
  // indexed live-scan view).
  const _defaultTab = location.port === '8766' ? 'appraised' : 'indexed';
  const _initialTab = location.hash.slice(1) || _defaultTab;
  activateTab(_initialTab);

  document.getElementById('tabs').addEventListener('click', e => {
    const btn = e.target.closest('button[data-tab]');
    if (btn) activateTab(btn.dataset.tab);
  });

  // Allow back/forward / manual hash edits to switch tabs too.
  window.addEventListener('hashchange', () => {
    const h = location.hash.slice(1);
    if (h) activateTab(h);
  });
</script>
</body>
</html>
"""


def _has_column(conn, table, col):
    return any(r[1] == col for r in conn.execute(
        f"PRAGMA table_info({table})").fetchall())


def query_state():
    db = config.DB_PATH
    if not db.exists():
        return {"error": f"DB not found at {db}"}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        breakdown = {}
        for row in conn.execute(
            "SELECT tier, COUNT(*) c FROM seen_listings GROUP BY tier"):
            breakdown[row["tier"] or "unknown"] = row["c"]
        matches = {}
        for row in conn.execute(
            "SELECT tier, COUNT(*) c FROM seen_listings "
            "WHERE notified=1 AND tier IN ('A','B','C','D','needs_review') "
            "GROUP BY tier"):
            matches[row["tier"]] = row["c"]
        total = sum(breakdown.values())
        total_matches = sum(matches.values())

        # New columns may not exist on freshly-migrated DBs yet
        has_body = _has_column(conn, "seen_listings", "body")
        bodies_stored = 0
        if has_body:
            bodies_stored = conn.execute(
                "SELECT COUNT(*) FROM seen_listings "
                "WHERE body IS NOT NULL AND body != ''"
            ).fetchone()[0]
        bodies_missing = max(0, total - bodies_stored)

        # Authoritative target from running scan (written by watcher.py /
        # rescore.py); fall back to legacy hardcoded value if absent.
        meta_target = conn.execute(
            "SELECT value FROM meta WHERE key='current_target'"
        ).fetchone()
        meta_phase = conn.execute(
            "SELECT value FROM meta WHERE key='current_phase'"
        ).fetchone()
        target_from_meta = int(meta_target[0]) if meta_target else None
        phase_from_meta = meta_phase[0] if meta_phase else None
        target_backfill = target_from_meta or TARGET_BACKFILL_FALLBACK

        # "Recent activity": is there an insert in the last 90s?
        last_insert = conn.execute(
            "SELECT MAX(first_seen_at) FROM seen_listings"
        ).fetchone()[0]
        try:
            from datetime import datetime, timezone
            last_dt = datetime.fromisoformat(last_insert) \
                if last_insert else None
            now = datetime.utcnow()
            recent_insert_secs = (now - last_dt).total_seconds() \
                if last_dt else None
        except Exception:
            recent_insert_secs = None

        # Heartbeat counts — how many rows landed in each rolling window.
        # Used by the Indexed tab to show the user that scraping is still
        # alive even when nothing matches their specs.
        inserts_15m = conn.execute(
            "SELECT COUNT(*) FROM seen_listings "
            "WHERE first_seen_at >= datetime('now','-15 minutes')"
        ).fetchone()[0]
        inserts_1h = conn.execute(
            "SELECT COUNT(*) FROM seen_listings "
            "WHERE first_seen_at >= datetime('now','-1 hour')"
        ).fetchone()[0]
        inserts_24h = conn.execute(
            "SELECT COUNT(*) FROM seen_listings "
            "WHERE first_seen_at >= datetime('now','-1 day')"
        ).fetchone()[0]

        # Phase detection — prefer explicit meta override
        if phase_from_meta and recent_insert_secs is not None and recent_insert_secs < 90:
            phase = phase_from_meta
            phase_progress = total
            phase_target = target_backfill
        elif bodies_missing > 0:
            phase = "Re-fetching bodies"
            phase_progress = bodies_stored
            phase_target = total
        elif total < target_backfill:
            phase = "Initial backfill"
            phase_progress = total
            phase_target = target_backfill
        else:
            phase = "Idle"
            phase_progress = total
            phase_target = total

        cols = "score, tier, distance_km, ask_price, price_uncertain, " \
               "salvage_estimate, neighborhood, title, link, geo_source"
        if has_body:
            cols += ", body"
        top = [dict(r) for r in conn.execute(
            f"SELECT {cols} FROM seen_listings "
            "WHERE notified=1 AND tier IN ('A','B','C','D','needs_review') "
            "ORDER BY score DESC LIMIT ?", (TOP_LIMIT,))]
        recent_cols = "first_seen_at, posted_at, score, tier, distance_km, " \
                      "ask_price, price_uncertain, salvage_estimate, " \
                      "neighborhood, title, geo_source"
        if has_body:
            recent_cols += ", body"
        recent = [dict(r) for r in conn.execute(
            f"SELECT {recent_cols} FROM seen_listings "
            "ORDER BY first_seen_at DESC LIMIT ?", (RECENT_LIMIT,))]
    finally:
        conn.close()
    return {
        "total": total,
        "target": target_backfill,
        "total_matches": total_matches,
        "bodies_stored": bodies_stored,
        "bodies_missing": bodies_missing,
        "phase": phase,
        "phase_progress": phase_progress,
        "phase_target": phase_target,
        "breakdown": breakdown,
        "matches": matches,
        "top": top,
        "recent": recent,
        "db_path": str(db),
        "last_insert_at": last_insert,
        "seconds_since_last_insert": recent_insert_secs,
        "inserts_15m": inserts_15m,
        "inserts_1h": inserts_1h,
        "inserts_24h": inserts_24h,
    }


APPRAISER_DIR = Path(__file__).parent.parent / "appraiser"
APPRAISER_BATCHES_DIR = APPRAISER_DIR / "batches"
APPRAISER_RESULTS_DIR = APPRAISER_DIR / "results"


def _scan_live_results() -> dict:
    """Walk appraiser/results/*.json + appraiser/batches/*.json to surface
    in-flight progress: how many batches are queued vs. finished, plus a
    live preview of records the agents have produced but the aggregator
    hasn't yet written to the DB.

    Returns {batches_queued, batches_done, records_preview, dropped_count}.
    Empty if the appraiser directories don't exist."""
    out = {"batches_queued": 0, "batches_done": 0, "records_preview": [],
           "dropped_count": 0, "preview_skipped": 0,
           "preview_appraised": 0}
    if APPRAISER_BATCHES_DIR.exists():
        out["batches_queued"] = len(list(
            APPRAISER_BATCHES_DIR.glob("batch_*.json")))
        dropped_path = APPRAISER_BATCHES_DIR / "_dropped.json"
        if dropped_path.exists():
            try:
                out["dropped_count"] = len(
                    json.loads(dropped_path.read_text(encoding="utf-8")))
            except Exception:
                pass
    if APPRAISER_RESULTS_DIR.exists():
        result_files = sorted(APPRAISER_RESULTS_DIR.glob("batch_*.json"))
        # A batch is only "in progress / done" if its file has at least
        # one record. Empty-array initial writes don't count.
        for f in result_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, list) or not data:
                continue
            out["batches_done"] += 1
            for r in data:
                if r.get("skipped"):
                    out["preview_skipped"] += 1
                else:
                    out["preview_appraised"] += 1
                out["records_preview"].append(r)
    return out


def query_appraisals():
    """Read the appraiser's SQLite DB read-only and return a JSON-friendly
    summary: counts + top picks + recent appraisals + per-listing detail
    joined to the original cl_watcher row (title, link, neighborhood).

    Also includes an in-flight 'live' section that reads
    `appraiser/results/*.json` directly so users see appraisals appear
    as agents finish each batch — before aggregate.py writes them to DB."""
    live = _scan_live_results()

    if not APPRAISAL_DB_PATH.exists():
        # Dashboard has live data even before the DB exists.
        return {"available": False,
                "db_path": str(APPRAISAL_DB_PATH),
                "message": "No aggregated appraisals yet.",
                "live": live}

    conn = sqlite3.connect(f"file:{APPRAISAL_DB_PATH}?mode=ro",
                           uri=True, timeout=5)
    src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro",
                          uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        src.row_factory = sqlite3.Row

        # Counts (all-time)
        recs = {row["recommendation"]: row["c"]
                for row in conn.execute(
                    "SELECT recommendation, COUNT(*) c "
                    "FROM appraisal GROUP BY recommendation")}
        total = sum(recs.values())

        # Counts (last 24 h) so the banner can split "recent vs. archive"
        recs_recent = {row["recommendation"]: row["c"]
                       for row in conn.execute(
                           "SELECT recommendation, COUNT(*) c "
                           "FROM appraisal "
                           "WHERE run_at >= datetime('now','-1 day') "
                           "GROUP BY recommendation")}
        total_recent = sum(recs_recent.values())

        last_run = conn.execute(
            "SELECT MAX(run_at) FROM appraisal").fetchone()[0]

        # Appraiser heartbeat — same shape as the scraper heartbeat in
        # query_state. Lets the dashboard show "alive / slow / stale"
        # for the appraisal pipeline, broken down by what made the cut
        # (BUY/MAYBE) vs. what didn't (SKIP/REJECTED).
        try:
            from datetime import datetime as _dt
            last_run_dt = _dt.fromisoformat(last_run) if last_run else None
            secs_since_appr = (_dt.utcnow() - last_run_dt).total_seconds() \
                if last_run_dt else None
        except Exception:
            secs_since_appr = None
        appr_15m = conn.execute(
            "SELECT COUNT(*) FROM appraisal "
            "WHERE run_at >= datetime('now','-15 minutes')").fetchone()[0]
        appr_1h = conn.execute(
            "SELECT COUNT(*) FROM appraisal "
            "WHERE run_at >= datetime('now','-1 hour')").fetchone()[0]
        appr_24h = conn.execute(
            "SELECT COUNT(*) FROM appraisal "
            "WHERE run_at >= datetime('now','-1 day')").fetchone()[0]

        # Top by ratio (BUY/MAYBE), split into "last 24h" and "archive"
        # so the dashboard surfaces what was just appraised before older
        # results. Each window is independently capped so the user always
        # sees both, even when a single cycle dominates.
        top_recent_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_low, salvage_high, "
            "salvage_realized, ratio, recommendation, confidence, summary "
            "FROM appraisal "
            "WHERE recommendation IN ('BUY','MAYBE') "
            "  AND run_at >= datetime('now','-1 day') "
            "ORDER BY ratio DESC LIMIT ?", (APPRAISAL_TOP_LIMIT,)))
        top_archive_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_low, salvage_high, "
            "salvage_realized, ratio, recommendation, confidence, summary "
            "FROM appraisal "
            "WHERE recommendation IN ('BUY','MAYBE') "
            "  AND run_at < datetime('now','-1 day') "
            "ORDER BY ratio DESC LIMIT ?", (APPRAISAL_TOP_LIMIT,)))

        # Chronological feed of every appraisal in the last 24 h —
        # regardless of recommendation. Lets the user see "is the
        # appraiser actually doing anything right now" at a glance.
        feed_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal "
            "WHERE run_at >= datetime('now','-1 day') "
            "ORDER BY run_at DESC LIMIT 50"))

        # Skip samples — same recent/archive split.
        skip_recent_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal WHERE recommendation = 'SKIP' "
            "  AND run_at >= datetime('now','-1 day') "
            "ORDER BY run_at DESC LIMIT 30"))
        skip_archive_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal WHERE recommendation = 'SKIP' "
            "  AND run_at < datetime('now','-1 day') "
            "ORDER BY run_at DESC LIMIT 30"))

        # Backwards-compat aliases for any external consumers / tests
        # that grab `.top` and `.skipped_sample`.
        top_rows = top_recent_rows + top_archive_rows
        skip_rows = skip_recent_rows + skip_archive_rows

        # Pull the cl_watcher fields for these rss_ids in a single query
        all_rss = list({r["rss_id"]
                        for r in (top_rows + skip_rows + feed_rows)})
        meta = {}
        if all_rss:
            qmarks = ",".join(["?"] * len(all_rss))
            for r in src.execute(
                f"SELECT rss_id, title, body, link, neighborhood, "
                f"       ask_price, section, distance_km, tier, posted_at "
                f"FROM seen_listings WHERE rss_id IN ({qmarks})",
                all_rss
            ):
                meta[r["rss_id"]] = dict(r)

        def enrich(row):
            d = dict(row)
            m = meta.get(d["rss_id"], {})
            d.update({
                "title": m.get("title"),
                "body": m.get("body"),
                "link": m.get("link"),
                "neighborhood": m.get("neighborhood"),
                "tier": m.get("tier"),
                "distance_km": m.get("distance_km"),
                "section": m.get("section"),
                "posted_at": m.get("posted_at"),
            })
            return d

        # Enrich live-preview rows with cl_watcher metadata (title/link/etc.)
        # so the dashboard can show them with proper titles before
        # aggregate.py runs.
        live_rss = [r.get("rss_id") for r in live["records_preview"]
                    if r.get("rss_id")]
        if live_rss:
            qmarks = ",".join(["?"] * len(live_rss))
            for r in src.execute(
                f"SELECT rss_id, title, link, neighborhood, ask_price, "
                f"       section, distance_km, tier, posted_at "
                f"FROM seen_listings WHERE rss_id IN ({qmarks})", live_rss
            ):
                meta[r["rss_id"]] = dict(r)

        # Surface only records not yet in the DB (= truly "in-flight").
        in_db = {r["rss_id"] for r in conn.execute(
            "SELECT rss_id FROM appraisal")}
        live_unaggregated = []
        for r in live["records_preview"]:
            if r.get("rss_id") in in_db:
                continue
            m = meta.get(r.get("rss_id"), {})
            live_unaggregated.append({
                "rss_id": r.get("rss_id"),
                "skipped": r.get("skipped", False),
                "skip_reason": r.get("skip_reason"),
                "item_kind": r.get("item_kind"),
                "salvage_low": r.get("salvage_low_cad", 0),
                "salvage_high": r.get("salvage_high_cad", 0),
                "summary": r.get("summary", ""),
                "extraction_confidence": r.get("extraction_confidence"),
                "title": m.get("title"),
                "link": m.get("link"),
                "neighborhood": m.get("neighborhood"),
                "ask_price": m.get("ask_price"),
                "section": m.get("section"),
                "tier": m.get("tier"),
                "posted_at": m.get("posted_at"),
            })

        return {
            "available": True,
            "db_path": str(APPRAISAL_DB_PATH),
            "last_run": last_run,
            "seconds_since_last_run": secs_since_appr,
            "appraised_15m": appr_15m,
            "appraised_1h": appr_1h,
            "appraised_24h": appr_24h,
            "total": total,
            "total_recent": total_recent,
            "by_recommendation": recs,
            "by_recommendation_recent": recs_recent,
            # New split keys — the frontend uses these.
            "top_recent": [enrich(r) for r in top_recent_rows],
            "top_archive": [enrich(r) for r in top_archive_rows],
            "skipped_recent": [enrich(r) for r in skip_recent_rows],
            "skipped_archive": [enrich(r) for r in skip_archive_rows],
            # Chronological feed — every appraisal in the last 24h.
            "feed": [enrich(r) for r in feed_rows],
            # Legacy keys retained for backwards compatibility.
            "top": [enrich(r) for r in top_rows],
            "skipped_sample": [enrich(r) for r in skip_rows],
            "live": {**live,
                     "live_unaggregated_count": len(live_unaggregated),
                     "live_unaggregated": live_unaggregated[:30]},
        }
    finally:
        conn.close()
        src.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence default access log

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/state":
            body = json.dumps(query_state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/appraisals":
            body = json.dumps(query_appraisals()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"cl_watcher dashboard: http://localhost:{PORT}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()


if __name__ == "__main__":
    main()
