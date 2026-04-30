# Appraiser

Two-stage AI valuator for the Craigslist listings collected by `cl_watcher`.
Reads from cl_watcher's SQLite DB **read-only** and writes its own
appraisal DB — your watcher's data is never modified.

## Two ways to run it

### Subagent path (default — no API key)

The `/appraise` slash command spawns parallel Claude Code subagents on
your subscription. No Anthropic API billing.

```text
[ /appraise ]                              ← you type this in Claude Code
        │
        ▼
   prepare.py        semantic prefilter, write appraiser/batches/batch_NNN.json
        │
        ▼
   N subagents       run in parallel waves; each handles ~30 listings,
        │           uses appraiser_tools.py for eBay comps, writes
        ▼           appraiser/results/batch_NNN.json
   aggregate.py      collect JSON → SQLite, apply rules.decide(), print top
```

### Legacy API path (optional)

If you ever do get an Anthropic API key, `python cli.py run` uses
`triage.py` / `extractor.py` / `valuator.py` over the SDK. Same logic,
faster wall-clock. Keep `anthropic` uncommented in `requirements.txt`.

## Why semantic search

The cl_watcher prefilter loses listings to vocabulary drift:

| Listing title             | Substring match misses it because… |
|---------------------------|-----------------------------------|
| `RPi 4 4GB working`       | doesn't contain "raspberry pi" |
| `lab DC source 0–30V`     | doesn't contain "bench psu" |
| `Intel NUC i5`            | doesn't contain "single board computer" |
| `WTB old ThinkPad`        | doesn't contain "wanted to buy" |
| `e-scooter for parts`     | doesn't contain "electric scooter" |

`semantic.py` embeds the listing title and the user's category / buyer /
exclusion phrases into a vector space and matches by cosine similarity.
"RPi 4 4GB" lives next to "raspberry pi 4" and we keep the listing.

Two backends:
- **`local`** — `sentence-transformers` running offline. Free, no account.
  ~90 MB model download on first run. **Default.**
- **`voyage`** — Voyage AI (Anthropic's recommended embeddings provider).
  Lightweight install, ~$0.02 per 1M tokens, well under 1¢ for 2,000 listings.

Set with `APPRAISER_EMBED_BACKEND=local|voyage`.

## Files

| File                    | Purpose |
|-------------------------|---------|
| `prepare.py`            | Phase 1: semantic prefilter, split into batch JSONs |
| `agent_brief.md`        | Per-subagent prompt — full criteria, schema, instructions |
| `aggregate.py`          | Phase 3: collect agent results → SQLite + apply rules |
| `appraiser_tools.py`    | CLI helper subagents call: `comps`, `distance`, `decide`, `category`, `criteria` |
| `comps.py`              | eBay sold-comps lookup (scrape / api / mock) with cache |
| `rules.py`              | Deterministic BUY/MAYBE/SKIP, distance tier, category match |
| `semantic.py`           | Embedding-based category / buyer / accessory / excluded matching |
| `config.py`             | Thresholds, category table, brand boosts, kill rules |
| `db.py`                 | Read-only access to cl_watcher; read-write to `appraisal.db` |
| `models.py`             | Pydantic typed records |
| `cli.py` `pipeline.py` `triage.py` `extractor.py` `valuator.py` `prompts.py` | Legacy API path (optional) |
| `samples/sample_listings.json` | 8 hand-written fixtures |
| `../.claude/commands/appraise.md` | The `/appraise` slash command |

## Setup

```powershell
cd "C:\Users\User\OneDrive\Desktop\Claude Project\appraiser"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Once cl_watcher has populated `state.db`, run `/appraise` in Claude Code
from the project root.

## Subagent path: how it actually runs

When you type `/appraise` in Claude Code:

1. The orchestrator (your foreground Claude Code session) reads the slash
   command at `.claude/commands/appraise.md`.
2. It runs `python appraiser/prepare.py` to prefilter and chunk listings
   into `appraiser/batches/batch_001.json`, `batch_002.json`, …
3. It spawns subagents in parallel waves, **5 at a time by default**.
   Each subagent gets the contents of `agent_brief.md` (the full prompt
   with the user's criteria) plus its assigned batch.
4. Each subagent reads its batch, reasons about each listing, runs eBay
   comp lookups via `python appraiser/appraiser_tools.py comps "<query>"`,
   and writes its results to `appraiser/results/batch_NNN.json`.
5. Once every wave finishes, the orchestrator runs
   `python appraiser/aggregate.py --top 30` which:
   - Joins agent records back to listings (for lat/lon/ask_price)
   - Applies `rules.decide()` with distance-tier multipliers
   - Writes Appraisal rows
   - Prints the top BUY/MAYBE picks

To run:
```text
/appraise               # 5 parallel × 30/batch, all unappraised
/appraise 8             # 8 parallel × 30/batch
/appraise 8 50          # 8 parallel × 50/batch
/appraise 4 25 100      # only 100 listings this run (~10–15 min)
/appraise 5 30 500      # only 500 listings this run (~25–30 min)
```

Already-appraised listings are always skipped on subsequent runs, so
you can chip through the 2,000-row backlog in 100-listing chunks
without ever re-processing what's already done. State persists in
`appraisal.db` across runs.

## Runtime

Rough estimate for ~2,000 listings:

| Stage | Wall time |
|---|---|
| Semantic prefilter (offline) | ~1 min |
| Subagent processing (5 parallel × ~7 min each, ~14 waves) | ~90 min |
| eBay comp scraping (cached, dedup'd across agents) | runs concurrently with above |
| Aggregation | < 30 sec |
| **Total** | **~90–120 min** |

8 parallel agents → ~60–80 min. 10 → ~50–70 min.

You can also run `/appraise` on a small slice for testing — `prepare.py
--limit 50` gives you ~2 batches.

## eBay comps

Default backend is `scrape` — works today, no account.

When you have an eBay developer account: set `EBAY_APP_ID` and
`EBAY_CERT_ID` in `%LOCALAPPDATA%\cl_watcher\.env`, then
`APPRAISER_EBAY_BACKEND=api`. Note: Browse API gives *active* listings,
not sold prices. Real sold-price data needs the Marketplace Insights API
(manual approval).

## Tuning

- `config.RECOMMEND_RATIO` — final ratio for BUY (default 2.5).
- `config.SALVAGE_REALIZATION_FACTOR` — fraction of comp-median you
  expect to recover (default 0.70).
- `semantic.SEMANTIC_MATCH_THRESHOLD` — cosine similarity floor
  (default 0.55). Lower = more permissive.
- `config.TRIAGE_RATIO_FLOOR` — coarse-stage cutoff for the legacy
  API path (default 1.5).

## Out of scope

- Updating `cl_watcher`'s `SEARCH_TERMS` list. Adding a semantic prefilter
  there would broaden capture upstream — separate change.
- A learning loop that calibrates `SALVAGE_REALIZATION_FACTOR` from your
  actual buy/resale outcomes.
- Image-based appraisal (Craigslist photo URL → Claude vision).
