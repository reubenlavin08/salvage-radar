"""Local web dashboard for cl_watcher. Stdlib only.

Run:
    .venv/Scripts/python.exe dashboard.py
Open: http://localhost:8765
"""
import json
import os
import re
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import config

# Port: 8765 = full dashboard (default), 8766 = appraiser-focused view.
# Allow override via DASHBOARD_PORT env var or `--port N` so two instances
# can run side-by-side without editing this file.
def _resolve_port() -> int:
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            try:
                return int(sys.argv[i + 1])
            except ValueError:
                pass
        if a.startswith("--port="):
            try:
                return int(a.split("=", 1)[1])
            except ValueError:
                pass
    return int(os.environ.get("DASHBOARD_PORT", "8765"))

PORT = _resolve_port()
TARGET_BACKFILL_FALLBACK = 1376  # used only if meta.current_target absent
TOP_LIMIT = 30
RECENT_LIMIT = 60  # Indexed-tab chronological feed depth

# Appraiser DB sits next to cl_watcher's state.db. Use the same
# resolution chain as cl_watcher/config.py and appraiser/config.py so
# that setting SALVAGE_RADAR_STATE_DIR moves both DBs in lockstep.
def _resolve_appraisal_db_path() -> Path:
    override = os.environ.get("SALVAGE_RADAR_STATE_DIR")
    if override:
        return Path(override) / "appraiser" / "appraisal.db"
    base = os.environ.get("LOCALAPPDATA", str(Path.home()))
    return Path(base) / "cl_watcher" / "appraiser" / "appraisal.db"

APPRAISAL_DB_PATH = _resolve_appraisal_db_path()
APPRAISAL_TOP_LIMIT = 50

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Salvage Radar</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&display=swap">
<style>
  /* Editorial / heritage palette inspired by kalso.vaangroup.com.
     Warm cream background, charcoal-brown ink, no pure black, no
     gradients, no glow. Borders and dividers carry weight; type
     does the visual heavy lifting. */
  :root {
    --bg: #faf8f5;             /* warm cream */
    --paper: #ffffff;          /* slightly brighter cards */
    --fg: #1f1d1e;             /* charcoal-brown ink (kalso #1f1d1e x46) */
    --muted: #8a8580;           /* warm gray for secondary text */
    --muted-soft: #b5b1ac;     /* even quieter for backgrounded labels */
    --border: #ddd9d3;         /* subtle warm divider */
    --border-strong: #1f1d1e;  /* strong rules under headings */
    --row-hover: #f1ede7;      /* warm hover shade */
    /* Functional accents — kept for distance bands and recommendation
       pills, but pulled toward the earthy palette. */
    --A: #2a3f5f;              /* close: deep ink-blue */
    --B: #3d6043;              /* worth-buying: forest green */
    --C: #6b6358;              /* mid: olive-stone */
    --D: #a16022;              /* further: burnt sienna */
    --R: #5e3b6e;              /* rejected accent (used sparingly) */
    --X: #b5a89d;              /* deprioritized: warm sand */
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--fg);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "Helvetica Neue", Arial, sans-serif;
    font-size: 15px; line-height: 1.55;
    font-feature-settings: "ss01", "cv11";
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  .wrap { max-width: 1240px; margin: 0 auto; padding: 56px 32px 96px; }
  header { display: flex; justify-content: space-between; align-items: baseline;
           padding-bottom: 28px; margin-bottom: 0;
           border-bottom: 1px solid var(--border-strong); }
  h1 { font-family: 'Fraunces', Georgia, serif;
       font-optical-sizing: auto;
       font-weight: 600; font-size: 42px; line-height: 1.05;
       margin: 0; letter-spacing: -0.02em;
       font-variation-settings: "opsz" 144, "SOFT" 30; }
  h2 { font-family: 'Fraunces', Georgia, serif;
       font-weight: 600; font-size: 24px; line-height: 1.2;
       margin: 40px 0 16px; letter-spacing: -0.01em;
       font-variation-settings: "opsz" 48; }
  h3 { font-family: 'Fraunces', Georgia, serif;
       font-weight: 600; font-size: 18px; margin: 24px 0 8px; }
  .subtitle { color: var(--muted); font-size: 13px;
              font-style: italic; font-family: 'Fraunces', Georgia, serif; }
  .timestamp { color: var(--muted); font-size: 12px;
               font-variant-numeric: tabular-nums;
               text-transform: uppercase; letter-spacing: 0.12em; }

  .progress-block { margin: 24px 0; }
  .progress-meta { display: flex; justify-content: space-between;
                   color: var(--muted); font-size: 12px; margin-bottom: 6px;
                   font-variant-numeric: tabular-nums; }
  .bar-track { height: 6px; background: var(--border); border-radius: 3px;
               overflow: hidden; }
  .bar-fill { height: 100%; background: var(--fg); transition: width 400ms ease; }
  .bar-secondary { background: var(--A); opacity: 0.7; }
  .phase-block { display: flex; align-items: center; gap: 16px;
                 margin: 24px 0 12px; padding: 12px 0;
                 border-top: 1px solid var(--border);
                 border-bottom: 1px solid var(--border); }
  .phase-label { font-weight: 600; font-size: 13px;
                 text-transform: uppercase; letter-spacing: 0.12em; }
  .phase-label.active::before { content: "● "; color: var(--B); animation: pulse 1.4s ease infinite; }
  .phase-label.idle::before { content: "○ "; color: var(--muted); }
  .phase-detail { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  /* Subtle motion language — kept restrained so the dashboard reads as
     editorial rather than playful. Each animation is sub-second, eased,
     and runs once unless it represents a live state. */
  @keyframes fadeRise {
    from { opacity: 0; transform: translateY(6px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  @keyframes shimmer {
    0% { opacity: 0.55; }
    50% { opacity: 1; }
    100% { opacity: 0.55; }
  }
  /* Whole tab pane fades in on activation. */
  .tab-pane.active { animation: fadeRise 360ms ease-out both; }
  /* h2 gets a tiny entrance lift on tab activation. Scoped under
     .tab-pane.active so the animation runs once when the tab opens,
     not every time the panel re-renders.

     NOTE: per-row (tbody tr) animation was removed deliberately. The
     dashboard rebuilds tables every 3-5 s, and an :animation on tr
     would re-fire on every poll, producing a constant flicker. The
     tab-pane fade above is enough entry texture. */
  .tab-pane.active h2 { animation: fadeRise 320ms ease-out both; }
  /* The live "alive" heartbeat pill breathes slowly so the user can
     tell at a glance the data is current. The "stale" pill stays still. */
  .heartbeat-pill.hb-alive { animation: shimmer 2.4s ease-in-out infinite; }
  /* Buttons (tabs, body-detail summaries) lift slightly on hover and
     ease out — gives a tactile cue without the blocky feel of squared
     corners. */
  .tabs button, details.body-detail summary, table tr {
    transition: transform 180ms ease, color 180ms ease,
                background 180ms ease, border-color 180ms ease;
  }
  .tabs button:hover { transform: translateY(-1px); }
  details.body-detail summary:hover { transform: translateX(2px); }
  /* Reco / tier / dist pills inflate by 1px on row hover so the row
     feels selectable without distracting halo effects. */
  tbody tr:hover .reco-pill,
  tbody tr:hover .tier-pill,
  tbody tr:hover .conf-pill { transform: scale(1.04); }
  .reco-pill, .tier-pill, .conf-pill {
    transition: transform 180ms cubic-bezier(0.34, 1.32, 0.64, 1);
  }
  /* Honour reduced-motion preference. */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
    }
  }
  .muted { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; }
  details.body-detail { margin-top: 8px; }
  details.body-detail summary { cursor: pointer; color: var(--muted);
                                font-size: 11px; text-transform: uppercase;
                                letter-spacing: 0.1em; }
  details.body-detail summary:hover { color: var(--fg); }
  details.body-detail .body-text { margin-top: 8px; padding: 12px 16px;
                                    background: var(--paper);
                                    border-left: 3px solid var(--fg);
                                    font-size: 13px; color: var(--muted);
                                    white-space: pre-wrap;
                                    max-height: 260px; overflow: auto;
                                    font-family: 'Fraunces', Georgia, serif;
                                    line-height: 1.6;
                                    font-variation-settings: "opsz" 14; }

  .chips { display: flex; flex-wrap: wrap; gap: 10px; margin: 20px 0 36px; }
  .chip { display: inline-flex; align-items: center; gap: 8px;
          padding: 6px 12px; border: 1px solid var(--border);
          border-radius: 4px; font-size: 12px;
          font-variant-numeric: tabular-nums;
          background: var(--paper);
          text-transform: uppercase; letter-spacing: 0.08em; }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .dot-A { background: var(--A); }
  .dot-B { background: var(--B); }
  .dot-C { background: var(--C); }
  .dot-D { background: var(--D); }
  .dot-R { background: var(--R); }
  .dot-X { background: var(--X); }
  .chip .match { color: var(--muted); }

  table { width: 100%; border-collapse: collapse; font-size: 14px;
          border-top: 1px solid var(--border-strong); }
  thead th { text-align: left; font-weight: 600; color: var(--fg);
             font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em;
             padding: 14px 14px;
             border-bottom: 1px solid var(--border-strong); }
  tbody td { padding: 14px 14px; border-bottom: 1px solid var(--border);
             vertical-align: top; }
  tbody tr:hover { background: var(--row-hover); }
  td.num { font-variant-numeric: tabular-nums; text-align: right;
           white-space: nowrap; }
  td.title a, td a { color: var(--fg); text-decoration: none;
                     border-bottom: 1px solid var(--border-strong);
                     padding-bottom: 1px; }
  td.title a:hover, td a:hover { background: var(--fg); color: var(--bg); }
  .tier-pill { display: inline-block; padding: 3px 8px; border-radius: 4px;
               font-size: 10px; font-weight: 600; color: var(--bg);
               font-variant-numeric: tabular-nums;
               text-transform: uppercase; letter-spacing: 0.08em; }
  .tier-A { background: var(--A); }
  .tier-B { background: var(--B); }
  .tier-C { background: var(--C); }
  .tier-D { background: var(--D); }
  .tier-needs_review { background: var(--R); }
  .tier-EXCLUDE { background: var(--X); }
  .tier-fetch_failed, .tier-unknown { background: var(--X); opacity: 0.6; }
  .geo-mark { color: var(--muted); font-size: 11px; margin-left: 4px; }
  .price-uncertain { color: var(--D); }
  .empty { color: var(--muted); padding: 24px 14px; font-style: italic;
           font-family: 'Fraunces', Georgia, serif; }
  footer { margin-top: 64px; padding-top: 24px;
           border-top: 1px solid var(--border);
           color: var(--muted); font-size: 11px;
           text-transform: uppercase; letter-spacing: 0.12em; }

  /* Appraiser section */
  .appraisal-banner { display: flex; align-items: center; gap: 16px;
                      padding: 16px 0; border-top: 1px solid var(--border);
                      border-bottom: 1px solid var(--border);
                      margin: 16px 0 8px; font-size: 14px; flex-wrap: wrap; }
  .appraisal-banner .label { font-weight: 600;
                              text-transform: uppercase;
                              letter-spacing: 0.12em; font-size: 11px; }
  .appraisal-banner .meta { color: var(--fg); font-variant-numeric: tabular-nums; }
  .reco-pill { display: inline-block; padding: 3px 9px; border-radius: 4px;
               font-size: 10px; font-weight: 600; color: var(--bg);
               text-transform: uppercase; letter-spacing: 0.1em; }
  .reco-BUY    { background: var(--B); }
  .reco-MAYBE  { background: var(--D); }
  .reco-SKIP   { background: var(--C); }
  .reco-REJECTED { background: var(--X); }
  .conf-pill { display: inline-block; padding: 2px 7px; border-radius: 4px;
               font-size: 10px; color: var(--muted);
               border: 1px solid var(--border);
               text-transform: uppercase; letter-spacing: 0.1em;
               background: var(--paper); }
  .ratio-cell { font-weight: 700; font-variant-numeric: tabular-nums;
                font-family: 'Fraunces', Georgia, serif;
                font-variation-settings: "opsz" 24; }
  .ratio-good { color: var(--B); }
  .ratio-meh  { color: var(--D); }
  .ratio-bad  { color: var(--muted); }
  .summary-cell { color: var(--muted); font-size: 13px; max-width: 480px;
                  white-space: pre-wrap; line-height: 1.5; }
  /* Make distance the visual anchor of each row — set in display serif
     so it reads like an editorial pull-stat. */
  .dist-cell { font-weight: 700; font-size: 18px;
               font-family: 'Fraunces', Georgia, serif;
               font-variation-settings: "opsz" 48;
               font-variant-numeric: tabular-nums; white-space: nowrap; }
  .dist-A { color: var(--A); }      /* <= 2.5 km, very close */
  .dist-B { color: var(--B); }      /* <= 4.5 km */
  .dist-C { color: var(--C); }      /* <= 7 km */
  .dist-D { color: var(--D); }      /* <= 9 km */
  .dist-far { color: var(--X); }    /* > 9 km, faded */
  .dist-unknown { color: var(--muted); font-weight: 400; font-style: italic; }
  /* Anchors inside the appraiser section inherit the editorial styling
     of td a above (ink-rule underline, invert on hover). */

  /* "Recent vs. archive" headings + collapsible archive blocks. */
  .window-label { font-size: 11px; font-weight: 400; color: var(--muted);
                  letter-spacing: 0.04em; margin-left: 6px;
                  text-transform: uppercase; }
  .window-count { font-size: 12px; font-weight: 400; color: var(--muted);
                  margin-left: 8px; font-variant-numeric: tabular-nums; }
  /* "Times" / "Posted" column cells — compact, muted, monospaced
     so they read as data rather than competing with the title. The
     two-line stack uses .t-line + .t-label. */
  .posted-cell { color: var(--muted); font-size: 12px; white-space: nowrap;
                 font-variant-numeric: tabular-nums; }
  .t-line { line-height: 1.5; }
  .t-label { color: var(--muted-soft);
             text-transform: uppercase; font-size: 9px;
             letter-spacing: 0.14em; margin-right: 6px; font-weight: 600; }

  /* Heartbeats — flat editorial banners, no rounded corners or fills.
     Status pill becomes an inline marker. */
  .heartbeat { display: flex; align-items: center; gap: 20px;
               padding: 16px 0; margin: 0 0 28px;
               border-top: 1px solid var(--border-strong);
               border-bottom: 1px solid var(--border-strong);
               font-size: 14px; flex-wrap: wrap; }
  .heartbeat-line { font-variant-numeric: tabular-nums; color: var(--fg); }
  .heartbeat-line.muted { color: var(--muted); }
  .heartbeat-pill { display: inline-block; padding: 4px 12px;
                    border-radius: 4px; font-size: 10px; font-weight: 700;
                    color: var(--bg); letter-spacing: 0.12em;
                    text-transform: uppercase; }
  .hb-alive { background: var(--B); }
  .hb-slow  { background: var(--D); }
  .hb-stale { background: var(--R); }
  .hb-unknown { background: var(--muted); }

  /* Tabs — editorial nav with uppercase labels, generous letter-spacing,
     and a bold ink underline for the active tab. Default tab is decided
     by URL hash if present, else by port (8766 → appraised, anything
     else → indexed). */
  .tabs { display: flex; gap: 0; margin: 32px 0 40px;
          border-bottom: 1px solid var(--border-strong);
          padding-bottom: 0; }
  .tabs button {
    background: none; border: none;
    padding: 16px 24px 16px 0;
    margin-right: 32px;
    color: var(--muted); font: inherit; font-size: 12px; font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    cursor: pointer; border-bottom: 3px solid transparent;
    margin-bottom: -1px; transition: color 0.15s, border-color 0.15s;
  }
  .tabs button:hover { color: var(--fg); }
  .tabs button.active { color: var(--fg); border-bottom-color: var(--fg); }
  .tabs .tab-count { color: var(--muted); font-weight: 400; margin-left: 8px;
                     font-variant-numeric: tabular-nums;
                     letter-spacing: 0.04em; }
  .tabs button.active .tab-count { color: var(--muted); }
  .tab-pane { display: none; }
  .tab-pane.active { display: block; }

  /* Debug-tab specific styling. Kept minimal — log tails dominate the
     visual weight and they have their own monospace pre block. */
  .dbg-card {
    border: 1px solid var(--rule); border-radius: 4px;
    padding: 12px 14px; background: var(--bg-soft);
    font-size: 13px; line-height: 1.55;
  }
  .dbg-card.ok    { border-left: 3px solid #2f7d2f; }
  .dbg-card.fail  { border-left: 3px solid #b03030; }
  .dbg-card.unknown { border-left: 3px solid var(--muted); }
  .dbg-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 18px;
  }
  .dbg-h3 {
    font-size: 13px; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.04em;
    margin: 0 0 8px 0;
  }
  table.dbg-mini { font-size: 13px; }
  table.dbg-mini td { padding: 4px 8px; border-bottom: 1px dotted var(--rule); }
  .dbg-logs {
    display: grid; grid-template-columns: 1fr; gap: 16px;
  }
  .dbg-pre {
    background: #0d0d0d; color: #e6e6e6;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11.5px; line-height: 1.45;
    padding: 10px 12px; border-radius: 4px;
    max-height: 320px; overflow-y: auto; overflow-x: auto;
    white-space: pre; margin: 0;
  }
  .dbg-pre .ln-err  { color: #ff8a7a; }
  .dbg-pre .ln-warn { color: #ffd070; }
  .dbg-pre .ln-info { color: #b8d4ff; }
  .age-fresh { color: #2f7d2f; }
  .age-stale { color: #b03030; font-weight: 600; }
  .age-warn  { color: #c47b00; }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div>
      <h1>Salvage Radar</h1>
      <div class="subtitle">Vancouver craigslist · indexed and appraised</div>
    </div>
    <div class="timestamp" id="ts">—</div>
  </header>

  <nav class="tabs" id="tabs">
    <button data-tab="indexed">Indexed area</button>
    <button data-tab="appraised">Appraised
      <span class="tab-count" id="tab-count-appraised"></span>
    </button>
    <button data-tab="recent">Recently appraised
      <span class="tab-count" id="tab-count-recent"></span>
    </button>
    <button data-tab="archive">Archive
      <span class="tab-count" id="tab-count-archive"></span>
    </button>
    <button data-tab="debug">Debug
      <span class="tab-count" id="tab-count-debug"></span>
    </button>
  </nav>

  <section class="tab-pane cl-section" data-tab="indexed">
    <!-- Scraper heartbeat — distinguishes "last check ran" (the watcher
         polled Craigslist) from "last insert" (a new row landed). When
         the scraper polls but everything is a duplicate, the last-check
         time still updates while last-insert stays put. -->
    <div class="heartbeat" id="heartbeat">
      <span class="heartbeat-pill" id="hb-pill">—</span>
      <span class="heartbeat-line">
        Last check: <strong id="hb-check">—</strong>
      </span>
      <span class="heartbeat-line muted">
        Last insert: <span id="hb-last">—</span>
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

    <h2>Indexed listings <span class="window-label">newest first</span></h2>
    <p class="muted" style="margin-top:-4px;font-size:13px;font-style:italic;
                            font-family:'Fraunces',Georgia,serif">
      Just the raw scrape feed in chronological order — the appraiser
      handles ranking by estimated value.
    </p>
    <table>
      <thead><tr>
        <th>Indexed</th>
        <th>Posted</th>
        <th>Tier</th>
        <th class="num" style="text-align:right">Dist</th>
        <th class="num" style="text-align:right">Price</th>
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

  <section class="tab-pane" data-tab="recent">
    <h2 style="margin-top:0">
      Recently appraised
      <span class="window-label">chronological feed, last 24 h, every result</span>
      <span class="window-count" id="appr-feed-count"></span>
    </h2>
    <p class="muted" style="margin-top:-4px;font-size:13px">
      Every appraisal in chronological order — BUY, MAYBE, SKIP, REJECTED.
      Use this to verify the appraiser is doing work even on cycles
      that produce no buys.
    </p>
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

  <section class="tab-pane" data-tab="debug">
    <h2 style="margin-top:0">Pipeline debugger</h2>
    <p class="muted" style="margin-top:-8px">
      Live view of every pipeline stage. Refreshes every 3 s.
      <span id="debug-queried-at" class="muted"></span>
    </p>

    <h2 style="margin-top:24px">Scheduled tasks</h2>
    <table>
      <thead><tr>
        <th>Name</th><th>State</th><th>Last run</th>
        <th>Last result</th><th>Next run</th>
      </tr></thead>
      <tbody id="debug-task-rows"></tbody>
    </table>

    <h2 style="margin-top:24px">Last LLM (claude) invocation</h2>
    <div id="debug-claude" class="dbg-card">—</div>

    <h2 style="margin-top:24px">Batch / result file freshness</h2>
    <div class="dbg-grid">
      <div>
        <h3 class="dbg-h3">batches/ (inputs to LLM)</h3>
        <table class="dbg-mini"><tbody id="debug-batches-rows"></tbody></table>
      </div>
      <div>
        <h3 class="dbg-h3">results/ (LLM outputs)</h3>
        <table class="dbg-mini"><tbody id="debug-results-rows"></tbody></table>
      </div>
    </div>

    <h2 style="margin-top:24px">Most recent indexer inserts</h2>
    <table>
      <thead><tr>
        <th>When</th><th>Tier</th><th class="num">Dist</th>
        <th class="num">Ask</th><th>Section</th><th>Neighborhood</th>
        <th>Title</th>
      </tr></thead>
      <tbody id="debug-recent-inserts"></tbody>
    </table>

    <h2 style="margin-top:24px">Most recent appraisal-DB writes</h2>
    <table>
      <thead><tr>
        <th>When</th><th>Rec</th><th class="num">Ask</th>
        <th class="num">Salvage</th><th>Summary</th>
      </tr></thead>
      <tbody id="debug-recent-appraisals"></tbody>
    </table>

    <h2 style="margin-top:24px">Live log tails</h2>
    <div class="dbg-logs">
      <div>
        <h3 class="dbg-h3">watcher.log <span class="muted">(scraper)</span></h3>
        <pre class="dbg-pre" id="debug-log-watcher"></pre>
      </div>
      <div>
        <h3 class="dbg-h3">wrapper.log <span class="muted">(chain glue)</span></h3>
        <pre class="dbg-pre" id="debug-log-wrapper"></pre>
      </div>
      <div>
        <h3 class="dbg-h3">cycle.log <span class="muted">(appraiser)</span></h3>
        <pre class="dbg-pre" id="debug-log-cycle"></pre>
      </div>
    </div>
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
      tb.innerHTML = '<tr><td colspan="7" class="empty">Nothing yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(r => `
      <tr>
        <td class="num posted-cell">${fmtPosted(r.first_seen_at)}</td>
        <td class="num posted-cell">${fmtPosted(r.posted_at)}</td>
        <td>${tierPill(r.tier)} ${geoMark(r.geo_source)}</td>
        <td class="num">${fmtDist(r.distance_km)}</td>
        <td class="num">${fmtPrice(r.ask_price, r.price_uncertain)}</td>
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
      // The Indexed tab is now a pure chronological feed — appraiser
      // handles value ranking. We still ask the API for d.top so the
      // chips stay populated, but we don't render it as its own table.
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
    // The pill reflects scraper LIVENESS, not insert freshness — the
    // scraper can be alive while nothing new is showing up. Use the
    // last-check timestamp as the primary signal, with the schedule
    // expectation of one check every 15 min.
    const checkSecs = d.seconds_since_last_check;
    const insSecs = d.seconds_since_last_insert;
    const pill = document.getElementById('hb-pill');
    let label, cls;
    if (checkSecs == null) {
      label = 'unknown'; cls = 'hb-unknown';
    } else if (checkSecs < 20 * 60) {
      label = 'alive'; cls = 'hb-alive';   // ran within last 20 min
    } else if (checkSecs < 60 * 60) {
      label = 'slow'; cls = 'hb-slow';     // missed a cycle
    } else {
      label = 'stale'; cls = 'hb-stale';   // missed multiple cycles
    }
    pill.textContent = label;
    pill.className = 'heartbeat-pill ' + cls;
    document.getElementById('hb-check').textContent = fmtAge(checkSecs);
    document.getElementById('hb-last').textContent = fmtAge(insSecs);
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
      // "Appraised" tab counts BUY/MAYBE picks (the deals view), the
      // "Recently appraised" tab counts the chronological feed.
      const recentDealsCount = (topR.length || 0);
      setText('tab-count-appraised', recentDealsCount > 0 ? `${recentDealsCount}` : '');
      const feedShown = (d.feed || []).length;
      setText('tab-count-recent', feedShown > 0 ? `${feedShown}` : '');
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
    // Show "real + rejected" split so the prefilter's output doesn't
    // drown out the agent's actual work in the cycle counter.
    const fmtSplit = (real, rej) => {
      const r = (real ?? 0).toLocaleString();
      const j = (rej ?? 0).toLocaleString();
      return real == null && rej == null
        ? '—'
        : `${r} appraised + ${j} prefiltered`;
    };
    document.getElementById('appr-hb-15m').textContent =
      fmtSplit(d.appraised_15m_real, d.appraised_15m_rejected);
    document.getElementById('appr-hb-1h').textContent =
      fmtSplit(d.appraised_1h_real, d.appraised_1h_rejected);
    document.getElementById('appr-hb-24h').textContent =
      fmtSplit(d.appraised_24h_real, d.appraised_24h_rejected);
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

  // ---------- Debug tab ----------
  // Polls /api/debug every 3 s and renders log tails, file freshness,
  // task status, recent inserts/appraisals, and last LLM exit. Only
  // polls when the tab is active so the dashboard isn't constantly
  // running PowerShell + reading log files in the background.
  let _debugPollHandle = null;

  function ageClass(min) {
    if (min == null) return '';
    if (min < 10) return 'age-fresh';
    if (min < 60) return 'age-warn';
    return 'age-stale';
  }
  function fmtAgeMin(min) {
    if (min == null) return '—';
    if (min < 1) return Math.round(min * 60) + 's ago';
    if (min < 60) return min.toFixed(1) + 'm ago';
    if (min < 60 * 24) return (min / 60).toFixed(1) + 'h ago';
    return (min / (60 * 24)).toFixed(1) + 'd ago';
  }
  function logLineClass(ln) {
    const s = ln.toLowerCase();
    if (s.includes('error') || s.includes('exit=1')
        || s.includes('failed') || s.includes('exception')
        || s.includes('hit your limit')) return 'ln-err';
    if (s.includes('warn')) return 'ln-warn';
    if (s.includes('info') || s.includes('cycle start')
        || s.includes('cycle end') || s.includes('triggering')) return 'ln-info';
    return '';
  }
  function renderLogTail(elId, lines) {
    const el = document.getElementById(elId);
    if (!el) return;
    if (!lines || !lines.length) {
      el.textContent = '(empty)';
      return;
    }
    el.innerHTML = lines.map(ln => {
      const cls = logLineClass(ln);
      const safe = ln.replace(/&/g, '&amp;').replace(/</g, '&lt;')
                     .replace(/>/g, '&gt;');
      return cls ? `<span class="${cls}">${safe}</span>` : safe;
    }).join('\n');
    // Keep view pinned to the latest line.
    el.scrollTop = el.scrollHeight;
  }

  async function refreshDebug() {
    try {
      const r = await fetch('/api/debug');
      if (!r.ok) return;
      const d = await r.json();

      document.getElementById('debug-queried-at').textContent =
        '· queried ' + (d.queried_at || '');

      // Tasks
      const taskRows = (d.tasks || []).map(t => {
        const stateClass = t.State === 'Running' ? 'ok'
          : t.State === 'Disabled' ? 'fail' : '';
        return `<tr>
          <td><strong>${t.Name}</strong></td>
          <td class="${stateClass}">${t.State}</td>
          <td>${t.LastRun || '—'}</td>
          <td>${t.LastResult || '—'}</td>
          <td>${t.NextRun || '—'}</td>
        </tr>`;
      }).join('');
      document.getElementById('debug-task-rows').innerHTML =
        taskRows || '<tr><td colspan="5" class="muted">no scheduled tasks</td></tr>';

      // Last claude invocation
      const lc = d.last_claude_invocation || {};
      const claudeEl = document.getElementById('debug-claude');
      let cls = 'unknown';
      if (lc.exit === 0) cls = 'ok';
      else if (lc.exit !== null && lc.exit !== undefined) cls = 'fail';
      claudeEl.className = 'dbg-card ' + cls;
      const exitTxt = (lc.exit === null || lc.exit === undefined)
        ? 'unknown' : 'exit=' + lc.exit;
      claudeEl.innerHTML = `
        <div><strong>${exitTxt}</strong> · ${lc.at || 'no record'}</div>
        ${lc.message ? '<div style="margin-top:6px;color:var(--muted)">'
          + lc.message.replace(/&/g, '&amp;').replace(/</g, '&lt;')
          + '</div>' : ''}
      `;

      // Files
      const fileRows = (files) => (files || []).map(f =>
        `<tr>
          <td><code>${f.name}</code></td>
          <td class="num">${f.size.toLocaleString()} B</td>
          <td class="${ageClass(f.age_minutes)}">${fmtAgeMin(f.age_minutes)}</td>
        </tr>`
      ).join('') || '<tr><td colspan="3" class="muted">(none)</td></tr>';
      document.getElementById('debug-batches-rows').innerHTML =
        fileRows(d.batches && d.batches.files);
      document.getElementById('debug-results-rows').innerHTML =
        fileRows(d.results && d.results.files);

      // Recent inserts
      document.getElementById('debug-recent-inserts').innerHTML =
        (d.recent_inserts || []).map(r => {
          const ts = r.first_seen_at ? r.first_seen_at.slice(11, 19) : '—';
          const ask = r.ask_price == null ? '—'
            : ('$' + r.ask_price + (r.price_uncertain ? '?' : ''));
          const dist = r.distance_km == null ? '—'
            : r.distance_km.toFixed(1) + ' km';
          return `<tr>
            <td>${ts}</td>
            <td><span class="tier-pill tier-${r.tier || 'unknown'}">${r.tier || '?'}</span></td>
            <td class="num">${dist}</td>
            <td class="num">${ask}</td>
            <td>${r.section || ''}</td>
            <td>${(r.neighborhood || '').slice(0, 24)}</td>
            <td>${(r.title || '').slice(0, 60)}</td>
          </tr>`;
        }).join('');

      // Recent appraisals
      document.getElementById('debug-recent-appraisals').innerHTML =
        (d.recent_appraisals || []).map(r => {
          const ts = r.run_at ? r.run_at.replace('T', ' ').slice(0, 19) : '—';
          const recCls = r.recommendation === 'BUY' ? 'ok'
            : r.recommendation === 'REJECTED' ? 'fail' : '';
          return `<tr>
            <td>${ts}</td>
            <td class="${recCls}"><strong>${r.recommendation || '?'}</strong></td>
            <td class="num">$${r.ask_price || 0}</td>
            <td class="num">$${(r.salvage_realized || 0).toFixed(0)}</td>
            <td>${(r.summary || '').slice(0, 110)}</td>
          </tr>`;
        }).join('');

      // Log tails
      renderLogTail('debug-log-watcher', d.logs && d.logs.watcher);
      renderLogTail('debug-log-wrapper', d.logs && d.logs.wrapper);
      renderLogTail('debug-log-cycle', d.logs && d.logs.appraiser_cycle);
    } catch (e) {
      console.error('debug refresh failed', e);
    }
  }

  function startDebugPolling() {
    if (_debugPollHandle) return;
    refreshDebug();
    _debugPollHandle = setInterval(refreshDebug, 3000);
  }
  function stopDebugPolling() {
    if (_debugPollHandle) {
      clearInterval(_debugPollHandle);
      _debugPollHandle = null;
    }
  }

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
    // Start the debug poller only when its tab is showing — otherwise
    // we'd be running PowerShell + reading three log files every 3 s
    // for nothing. The other tabs use lightweight polls already.
    if (name === 'debug') startDebugPolling();
    else stopDebugPolling();
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


_TZ_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")


def _utc_iso(s):
    """Normalize an ISO timestamp string for JS consumption.

    The watcher and appraiser write naive UTC timestamps (e.g.
    `2026-04-30T04:37:56.123`). JS `new Date()` treats anything without
    a timezone marker as LOCAL time, so a UTC value gets displayed
    7 hours off in PDT. We append `Z` so the browser converts it
    correctly. No-op if the string already carries `+HH:MM` / `Z`.
    """
    if not s or not isinstance(s, str):
        return s
    if _TZ_RE.search(s):
        return s
    return s + "Z"


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
        meta_check_start = conn.execute(
            "SELECT value FROM meta WHERE key='last_check_started_at'"
        ).fetchone()
        meta_check_end = conn.execute(
            "SELECT value FROM meta WHERE key='last_check_finished_at'"
        ).fetchone()
        target_from_meta = int(meta_target[0]) if meta_target else None
        phase_from_meta = meta_phase[0] if meta_phase else None
        last_check_started_at = meta_check_start[0] if meta_check_start else None
        last_check_finished_at = meta_check_end[0] if meta_check_end else None
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

        # Compute "seconds since last check" — preferring the start
        # timestamp so that a run currently in progress still counts.
        try:
            from datetime import datetime as _dt2, timezone as _tz2
            check_anchor = last_check_started_at or last_check_finished_at
            if check_anchor:
                # Strip Z, parse, treat as UTC-naive
                _s = check_anchor.rstrip("Z")
                _t = _dt2.fromisoformat(_s)
                secs_since_check = (_dt2.utcnow() - _t).total_seconds()
            else:
                secs_since_check = None
        except Exception:
            secs_since_check = None

        # Heartbeat counts — how many rows landed in each rolling window.
        # Used by the Indexed tab to show the user that scraping is still
        # alive even when nothing matches their specs.
        #
        # CRITICAL: do NOT use SQLite's datetime('now','-N minutes') here.
        # That function returns a SPACE-separated string ('2026-04-30 05:38:00')
        # while our row timestamps are T-separated ISO ('2026-04-30T05:38:00').
        # SQLite's >= becomes a string compare and 'T' (0x54) > ' ' (0x20),
        # so every same-day row would falsely pass the threshold — turning
        # "in last 15 min" into "today total". Compute the threshold in
        # Python in the matching ISO format and pass it as a parameter.
        from datetime import datetime as _dt3, timedelta as _td3
        _now_utc = _dt3.utcnow()
        _t15 = (_now_utc - _td3(minutes=15)).isoformat()
        _t60 = (_now_utc - _td3(hours=1)).isoformat()
        _t24h = (_now_utc - _td3(days=1)).isoformat()
        inserts_15m = conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE first_seen_at >= ?",
            (_t15,)
        ).fetchone()[0]
        inserts_1h = conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE first_seen_at >= ?",
            (_t60,)
        ).fetchone()[0]
        inserts_24h = conn.execute(
            "SELECT COUNT(*) FROM seen_listings WHERE first_seen_at >= ?",
            (_t24h,)
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

    # Normalize all timestamp strings so the browser interprets them as
    # UTC instead of local time.
    for r in recent:
        r["first_seen_at"] = _utc_iso(r.get("first_seen_at"))
        r["posted_at"] = _utc_iso(r.get("posted_at"))

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
        "last_insert_at": _utc_iso(last_insert),
        "seconds_since_last_insert": recent_insert_secs,
        "last_check_started_at": _utc_iso(last_check_started_at),
        "last_check_finished_at": _utc_iso(last_check_finished_at),
        "seconds_since_last_check": secs_since_check,
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

        # Threshold timestamps in matching ISO format (T-separated). See
        # the long comment above query_state's heartbeat block for why we
        # can't use SQLite's datetime('now',...) here — string-compare
        # against our T-separated row format would silently match every
        # same-day row.
        from datetime import datetime as _dt, timedelta as _td
        _now_utc = _dt.utcnow()
        _t15m = (_now_utc - _td(minutes=15)).isoformat()
        _t1h = (_now_utc - _td(hours=1)).isoformat()
        _t24h = (_now_utc - _td(days=1)).isoformat()

        # Counts (last 24 h) so the banner can split "recent vs. archive"
        recs_recent = {row["recommendation"]: row["c"]
                       for row in conn.execute(
                           "SELECT recommendation, COUNT(*) c "
                           "FROM appraisal WHERE run_at >= ? "
                           "GROUP BY recommendation", (_t24h,))}
        total_recent = sum(recs_recent.values())

        last_run = conn.execute(
            "SELECT MAX(run_at) FROM appraisal").fetchone()[0]

        # Appraiser heartbeat — same shape as the scraper heartbeat in
        # query_state. Lets the dashboard show "alive / slow / stale"
        # for the appraisal pipeline, broken down by what made the cut
        # (BUY/MAYBE) vs. what didn't (SKIP/REJECTED).
        try:
            # Python 3.10's fromisoformat can't parse the 'Z' suffix
            # (3.11+ can). Strip it so this works on either runtime.
            _lr = last_run.rstrip("Z") if last_run else None
            last_run_dt = _dt.fromisoformat(_lr) if _lr else None
            secs_since_appr = (_now_utc - last_run_dt).total_seconds() \
                if last_run_dt else None
        except Exception:
            secs_since_appr = None
        # Heartbeat counts split by "real appraisal" (LLM did work) vs
        # "prefilter rejection" (prepare.py dropped before the agent saw it).
        # Lumping them inflates the visible cycle volume — the user wants
        # to know how many things the AGENT actually evaluated.
        appr_15m_real = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation != 'REJECTED'", (_t15m,)).fetchone()[0]
        appr_15m_rej = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation = 'REJECTED'", (_t15m,)).fetchone()[0]
        appr_1h_real = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation != 'REJECTED'", (_t1h,)).fetchone()[0]
        appr_1h_rej = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation = 'REJECTED'", (_t1h,)).fetchone()[0]
        appr_24h_real = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation != 'REJECTED'", (_t24h,)).fetchone()[0]
        appr_24h_rej = conn.execute(
            "SELECT COUNT(*) FROM appraisal WHERE run_at >= ? "
            "AND recommendation = 'REJECTED'", (_t24h,)).fetchone()[0]
        appr_15m = appr_15m_real + appr_15m_rej
        appr_1h = appr_1h_real + appr_1h_rej
        appr_24h = appr_24h_real + appr_24h_rej

        # Top by ratio (BUY/MAYBE), split into "last 24h" and "archive"
        # so the dashboard surfaces what was just appraised before older
        # results. Each window is independently capped so the user always
        # sees both, even when a single cycle dominates.
        top_recent_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_low, salvage_high, "
            "salvage_realized, ratio, recommendation, confidence, summary "
            "FROM appraisal "
            "WHERE recommendation IN ('BUY','MAYBE') AND run_at >= ? "
            "ORDER BY ratio DESC LIMIT ?",
            (_t24h, APPRAISAL_TOP_LIMIT)))
        top_archive_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_low, salvage_high, "
            "salvage_realized, ratio, recommendation, confidence, summary "
            "FROM appraisal "
            "WHERE recommendation IN ('BUY','MAYBE') AND run_at < ? "
            "ORDER BY ratio DESC LIMIT ?",
            (_t24h, APPRAISAL_TOP_LIMIT)))

        # Chronological feed of every appraisal in the last 24 h —
        # regardless of recommendation. Lets the user see "is the
        # appraiser actually doing anything right now" at a glance.
        feed_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal WHERE run_at >= ? "
            "ORDER BY run_at DESC LIMIT 50", (_t24h,)))

        # Skip samples — same recent/archive split.
        skip_recent_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal WHERE recommendation = 'SKIP' AND run_at >= ? "
            "ORDER BY run_at DESC LIMIT 30", (_t24h,)))
        skip_archive_rows = list(conn.execute(
            "SELECT rss_id, run_at, ask_price, salvage_realized, ratio, "
            "       recommendation, confidence, summary "
            "FROM appraisal WHERE recommendation = 'SKIP' AND run_at < ? "
            "ORDER BY run_at DESC LIMIT 30", (_t24h,)))

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
                "posted_at": _utc_iso(m.get("posted_at")),
            })
            # run_at is selected directly from the appraisal table.
            d["run_at"] = _utc_iso(d.get("run_at"))
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
            "last_run": _utc_iso(last_run),
            "seconds_since_last_run": secs_since_appr,
            "appraised_15m": appr_15m,
            "appraised_1h": appr_1h,
            "appraised_24h": appr_24h,
            # Split: "real" = LLM agent ran (BUY/MAYBE/SKIP);
            # "rejected" = prefilter dropped it (REJECTED). The user
            # cares about the first number — the second tells them
            # how much the prefilter is doing on their behalf.
            "appraised_15m_real": appr_15m_real,
            "appraised_15m_rejected": appr_15m_rej,
            "appraised_1h_real": appr_1h_real,
            "appraised_1h_rejected": appr_1h_rej,
            "appraised_24h_real": appr_24h_real,
            "appraised_24h_rejected": appr_24h_rej,
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


def query_debug():
    """Return live debug payload — log tails, file freshness, recent
    pipeline events. Powers the Debug tab so the user can see what's
    happening end-to-end without tailing logs in three terminals.

    Everything here is best-effort: missing files / paths must NEVER
    raise (debug panel must keep working even if part of the system is
    broken — that's the whole point).
    """
    from datetime import datetime
    out: dict = {"available": True, "errors": []}

    def _safe_tail(path: str, n: int = 40) -> list:
        try:
            p = Path(path)
            if not p.exists():
                return [f"(no file at {path})"]
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            return [ln.rstrip("\n") for ln in lines[-n:]]
        except Exception as e:
            return [f"(error reading {path}: {e})"]

    # 1. Log tails — three streams the user actually wants to see live.
    log_root_local = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    out["logs"] = {
        "watcher": _safe_tail(str(config.LOG_DIR / "watcher.log"), 40),
        "wrapper": _safe_tail(
            str(log_root_local / "cl_watcher" / "log" / "wrapper.log"), 40),
        "appraiser_cycle": _safe_tail(
            str(log_root_local / "cl_watcher" / "appraiser"
                / "log" / "cycle.log"), 60),
    }

    # 2. Batch / result file freshness. Stale results/ + fresh batches/
    # is the diagnostic signature of "LLM call is failing silently."
    appraiser_root = Path("C:/Users/User/OneDrive/Desktop/Claude Project"
                          "/appraiser")
    def _summarize_dir(d: Path) -> dict:
        if not d.exists():
            return {"path": str(d), "exists": False, "files": []}
        files = []
        for f in sorted(d.glob("batch_*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True):
            mt = datetime.utcfromtimestamp(f.stat().st_mtime)
            age_min = (datetime.utcnow() - mt).total_seconds() / 60
            files.append({"name": f.name, "size": f.stat().st_size,
                          "mtime": mt.isoformat() + "Z",
                          "age_minutes": round(age_min, 1)})
        return {"path": str(d), "exists": True, "files": files[:10]}
    out["batches"] = _summarize_dir(appraiser_root / "batches")
    out["results"] = _summarize_dir(appraiser_root / "results")

    # 3. Last claude invocation outcome — parse cycle.log for the most
    # recent `claude exit=` line and the line right after it. That tells
    # the user "did the LLM actually run, or did it fail with quota /
    # permissions / network?"
    last_claude = {"exit": None, "message": None, "at": None}
    try:
        cycle_lines = out["logs"]["appraiser_cycle"]
        for i in range(len(cycle_lines) - 1, -1, -1):
            ln = cycle_lines[i]
            if "claude exit=" in ln or "claude:" in ln:
                last_claude["at"] = ln.split(" claude")[0] \
                    if " claude" in ln else None
                if "claude exit=" in ln:
                    try:
                        last_claude["exit"] = int(
                            ln.split("claude exit=")[1].split(";")[0])
                    except Exception:
                        pass
                # Capture the next non-empty line as the message
                for j in range(i + 1, min(i + 4, len(cycle_lines))):
                    nxt = cycle_lines[j].strip()
                    if nxt and not nxt.startswith("==="):
                        last_claude["message"] = nxt[:300]
                        break
                break
    except Exception as e:
        out["errors"].append(f"claude exit parse: {e}")
    out["last_claude_invocation"] = last_claude

    # 4. Recent indexer inserts — last 10 rows with tier / distance / ask
    # so the user can see the scrape-time decisions live.
    try:
        src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro",
                              uri=True, timeout=5)
        src.row_factory = sqlite3.Row
        rows = src.execute(
            "SELECT first_seen_at, title, tier, distance_km, ask_price, "
            "       price_uncertain, neighborhood, section "
            "FROM seen_listings ORDER BY first_seen_at DESC LIMIT 12"
        ).fetchall()
        out["recent_inserts"] = [dict(r) for r in rows]
        src.close()
    except Exception as e:
        out["recent_inserts"] = []
        out["errors"].append(f"recent_inserts: {e}")

    # 5. Recent appraisal-DB writes — last 10 with run_at / recommendation
    try:
        appr = sqlite3.connect(f"file:{APPRAISAL_DB_PATH}?mode=ro",
                               uri=True, timeout=5)
        appr.row_factory = sqlite3.Row
        rows = appr.execute(
            "SELECT run_at, rss_id, recommendation, ask_price, "
            "       salvage_realized, summary "
            "FROM appraisal ORDER BY run_at DESC LIMIT 12"
        ).fetchall()
        out["recent_appraisals"] = []
        for r in rows:
            d = dict(r)
            # Trim summary to one line for compact display
            s = (d.get("summary") or "").splitlines()[0][:140]
            d["summary"] = s
            out["recent_appraisals"].append(d)
        appr.close()
    except Exception as e:
        out["recent_appraisals"] = []
        out["errors"].append(f"recent_appraisals: {e}")

    # 6. Scheduled-task status (via PowerShell). Fire-and-forget; if it
    # fails we silently degrade. Useful so the user can see Disabled vs
    # Ready vs Running without leaving the dashboard.
    out["tasks"] = []
    try:
        import subprocess
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask -TaskName 'ClWatcher','ClAppraiser' "
             "-ErrorAction SilentlyContinue | ForEach-Object { "
             "$i = Get-ScheduledTaskInfo $_; "
             "[PSCustomObject]@{Name=$_.TaskName; State=$_.State.ToString(); "
             "LastRun=$i.LastRunTime.ToString('o'); "
             "LastResult=('0x{0:X}' -f $i.LastTaskResult); "
             "NextRun=$i.NextRunTime.ToString('o') } } | "
             "ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=5)
        if ps.returncode == 0 and ps.stdout.strip():
            data = json.loads(ps.stdout)
            if isinstance(data, dict):
                data = [data]
            out["tasks"] = data
    except Exception as e:
        out["errors"].append(f"task status: {e}")

    out["queried_at"] = datetime.utcnow().isoformat() + "Z"
    return out


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
        if path == "/api/debug":
            body = json.dumps(query_debug()).encode("utf-8")
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
    print(f"Salvage Radar dashboard: http://localhost:{PORT}")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        server.shutdown()


if __name__ == "__main__":
    main()
