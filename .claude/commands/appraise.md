---
description: Appraise unappraised cl_watcher listings with parallel Claude Code subagents (no API key)
argument-hint: [max-parallel] [batch-size] [limit]
---

You are orchestrating the salvage-appraiser. Run the pipeline using parallel
Claude Code subagents — no Anthropic API key needed; all reasoning is on the
user's Claude subscription.

## Defaults
- `max_parallel` = first arg, default **5**
- `batch_size`   = second arg, default **100** (larger batches = fewer
                   system-prompt repetitions; the brief is small enough
                   that 100 listings × ~500 tokens fits in the agent's
                   context comfortably)
- `limit`        = third arg, optional. If set, only this many unappraised
                   listings are processed this run.

Already-appraised listings (rows in `appraisal.db`) are always skipped by
default, so running `/appraise` repeatedly walks through new listings only.

## Step 1 — Prepare batches

Run from the project root (`C:\Users\User\OneDrive\Desktop\Claude Project`):

```bash
python appraiser/prepare.py --batch-size <batch_size> [--limit <limit>]
```

Pass `--clear` only on the very first run or if the user asks to redo
everything. Otherwise leave existing batches/results alone — `prepare.py`
overwrites batch files in place and `aggregate.py` upserts on `rss_id`,
so re-running is safe.

This:
- Reads `cl_watcher`'s `state.db` read-only
- Runs the semantic prefilter (drops buyer posts / accessory-only / excluded
  categories)
- Writes one batch JSON per chunk to `appraiser/batches/batch_NNN.json`
- Writes `appraiser/batches/_manifest.json` with the full file list
- Prints a JSON summary

If it reports `"loaded": 0`, the cl_watcher DB hasn't been populated yet —
tell the user and stop.

## Step 2 — Read the agent brief and manifest

- Read `appraiser/agent_brief.md` — this is the per-agent prompt template.
- Read `appraiser/batches/_manifest.json` — gives you `batches[]` (input
  paths) and `expected_results[]` (output paths).

## Step 3 — Spawn subagents in parallel waves

For each batch, build the agent prompt:

1. Take the contents of `agent_brief.md`.
2. Substitute `<BATCH_PATH>` with the batch input path.
3. Substitute `<OUTPUT_PATH>` with the corresponding expected_results path.
4. Append the literal contents of the batch JSON file at the end of the
   prompt (so the agent has the listings inline and doesn't need to re-read
   them — though it can if it wants).

Spawn agents using the `Agent` tool with `subagent_type: "general-purpose"`.

**Run them in waves of `max_parallel`** — issue `max_parallel` Agent tool
calls in a single message (they execute concurrently), wait for that wave
to complete, then issue the next wave. Continue until all batches are
done.

For each wave:
- Use a short `description` like "Appraise batch 003"
- Pass the constructed prompt as `prompt`

If an agent reports failure (e.g. couldn't write its output file), note it
and move on — `aggregate.py` ignores missing batches, and you can re-run
just the failed ones later by re-spawning agents for those specific
batch files.

## Step 4 — Aggregate

Once every wave is done, run:

```bash
python appraiser/aggregate.py --top 30
```

This consolidates all `appraiser/results/batch_*.json` files into the
appraisal SQLite DB, applies the user's deterministic BUY/MAYBE/SKIP
rule with distance-tier multipliers, and prints the top picks.

## Step 5 — Report

After aggregation finishes, summarize for the user in 3 short bullets:
- How many listings were appraised
- Counts of BUY / MAYBE / SKIP
- Top 3 rss_ids by ratio (use the table aggregate.py prints)

That's it. Do not summarize what each agent did — the aggregator is the
ground truth.

## Failure modes to watch for

- **No `_manifest.json` after Step 1**: prepare.py crashed. Re-read its
  stderr.
- **An agent times out or returns junk**: just leave its result file
  missing; aggregate.py will skip it. Tell the user which batches need a
  re-run and offer to re-dispatch them.
- **eBay scraping fails inside an agent**: the agent should fall back to
  prior knowledge with `confidence: low`. That's by design — don't
  intervene.
