# Screenshots — capture instructions

The README references screenshots that should live in this folder. Both
dashboards run locally on the user's laptop; capture them with whatever
screenshot tool you like (Windows: Win+Shift+S → save).

## What to capture

### `01-cl_watcher-dashboard.png`
- URL: <http://localhost:8765/>
- Caption: cl_watcher live-scan dashboard during a scrape cycle.
- Look for: phase indicator ("Initial backfill" or "Idle"), tier
  breakdown chips, the "Top matches by score" table.
- Width: ~1200 px (full dashboard width). Crop to the wrapper.

### `02-appraiser-dashboard.png`
- URL: <http://localhost:8766/>
- Caption: appraiser dashboard with focus mode — distance is the visual
  anchor; BUY/MAYBE picks sorted by distance band then ratio.
- Look for: status banner ("BUY 16 · MAYBE 7 · SKIP 518"), the bold
  color-coded distance column, top picks table with full per-line
  reasoning.

### `03-pipeline-flow.png` *(optional)*
- An ASCII / mermaid diagram or hand-drawn export showing
  cl_watcher → state.db → prepare → subagents → results → aggregate →
  appraisal.db → dashboard. Already documented as text in the main
  README; only add this if you want a visual.

## Preferred format

PNG, dark mode (the dashboards adapt to system theme — use whichever
looks better on the README background). Trim browser chrome so only
the page content is visible. ~150–250 KB after compression.
