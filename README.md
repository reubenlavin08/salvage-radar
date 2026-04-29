# cl-watcher

Local agent that watches **Craigslist Vancouver** for free / cheap salvageable electronics and robotics parts near a target neighborhood (Dunbar / UBC by default), scores listings against estimated parts-salvage value, filters by precise map-pin geography, and emails a digest only when something worth picking up appears.

Originally written for me to find free printers, e-bikes, hoverboards, robot vacuums, treadmill motors, etc. close to UBC. Easy to retarget to any Craigslist region and any item set.

## What it does

- **36 default search terms** (free section + paid ≤ $30) across motors, microcontrollers, mobility devices (e-bike / hoverboard / e-scooter / VESC), robot vacuums, drones, RC, lab gear, and more
- **Per-listing enrichment**: fetches each listing page, extracts title, body text, asking price, neighborhood, **lat/long from the map pin**, and Craigslist's structured `condition` / `make` / `model` attributes
- **Precise haversine geo classification**: tiers listings A → D by km from the target point, with hard-exclude rules for boundaries that distance alone misses (e.g. east of Cambie, north of Lions Gate, south of Fraser)
- **Title-primary salvage scoring** with brand boosts, condition modifiers, accessory-only kill words, buyer / trader filtering, quantity detection
- **Local web dashboard** (Python stdlib only, no Node) at `http://localhost:8765` showing live progress, top matches, and full body / attributes per listing
- **Email digests** via Gmail SMTP — only fires when there are matches, max one email per 15-min run
- **Re-scoring**: tune the rules, run `rescore.py --no-fetch`, dashboard updates instantly. Periodic `rescore.py` (with network) re-fetches bodies/attrs for any listings missing them.

## Quick architecture

```
config.py        Search terms, salvage table, geo tiers, decision rules, killers
fetcher.py       HTML search page scraper + per-listing detail enrichment
scoring.py       Salvage estimator, geo classifier (haversine + string), notify decision
storage.py       SQLite persistence + idempotent migrations
watcher.py       Main entrypoint: fetch → dedup → enrich → score → notify
email_sender.py  Gmail SMTP digest
dashboard.py     Local web server with phase, progress bars, match tables
live_view.py     Terminal dashboard (alternative to web)
rescore.py       Re-evaluate every row (optional body re-fetch)
send_rescored_email.py  One-shot manual digest send
register_task.ps1  Windows Task Scheduler installer (15-min cadence)
```

State (DB, logs, secrets) lives at `%LOCALAPPDATA%\cl_watcher\` — outside any cloud-sync folder so SQLite WAL doesn't conflict with sync.

## Quickstart (Windows)

### 1. Clone and install

```powershell
git clone https://github.com/<your-username>/cl-watcher.git
cd cl-watcher
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If activation is blocked once: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`.

### 2. Generate a Gmail app password

1. Enable 2-Step Verification: <https://myaccount.google.com/security>
2. Create an app password: <https://myaccount.google.com/apppasswords>
3. Copy the 16-character password.

### 3. Create the .env

Put this in `%LOCALAPPDATA%\cl_watcher\.env`:

```
GMAIL_USER=youraddress@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
# Optional overrides (default to GMAIL_USER):
# CL_EMAIL_TO=alerts@example.com
# CL_EMAIL_FROM=youraddress@gmail.com
```

The folder is auto-created on first run.

### 4. Retarget for your area (optional)

Edit `config.py`:

- `CL_BASE = "https://vancouver.craigslist.org"` → your Craigslist subdomain
- `DUNBAR_LAT`, `DUNBAR_LON` → coordinates of your search center
- `TIER_A_KM ... TIER_D_KM` → radius bands you care about
- `EAST_BOUNDARY_LON`, the lat/lon hard-exclude rules in `scoring.classify_geo_by_coords` → adjust to your local geography
- `SEARCH_TERMS` → things you actually want
- `SALVAGE_TABLE`, `NEGATIVE_TITLE_KILLERS` → calibrate to your local market

### 5. First run

```powershell
.\.venv\Scripts\python.exe watcher.py
```

The first run does a full backfill (typically 1500–2000 listings, ~35 min wall clock at 1.5 s/request). It'll send a confirmation email when done — even if no matches surface.

### 6. Schedule it

```powershell
.\register_task.ps1
```

Runs every 15 minutes whenever the user is logged in (Interactive logon — no admin needed). For 24/7 operation, edit the script to use `-LogonType S4U -RunLevel Highest` and run as Administrator.

### 7. Watch live

```powershell
# Local web dashboard
.\.venv\Scripts\python.exe dashboard.py
# → open http://localhost:8765

# Or terminal dashboard
.\.venv\Scripts\python.exe live_view.py

# Or tail the watcher log
Get-Content "$env:LOCALAPPDATA\cl_watcher\log\watcher.log" -Wait -Tail 50
```

## Data captured per listing

Stored in SQLite at `%LOCALAPPDATA%\cl_watcher\state.db`:

| Column | Source |
|---|---|
| `rss_id` | listing URL (PK, dedup) |
| `title`, `body` | listing page (full text) |
| `attributes` | JSON of Craigslist `.attrgroup` (condition / make / model / dimensions) |
| `link`, `posted_at`, `section` (free vs paid) | URL + listing page |
| `ask_price`, `price_uncertain` ("make me an offer" / OBO detection) | listing page |
| `neighborhood`, `latitude`, `longitude` | listing page header + map div |
| `distance_km`, `tier`, `geo_source` | derived (haversine + string fallback) |
| `salvage_estimate`, `score`, `notified` | derived (re-runnable via `rescore.py --no-fetch`) |

Raw fields are persistent; derived fields are recomputable, so you can tune rules and rescore without touching the network.

## Tuning loop

```powershell
# Edit config.py — adjust salvage values, killers, brand boosts, etc.
.\.venv\Scripts\python.exe rescore.py --no-fetch    # ~2 sec, no network
# Refresh dashboard, see new top matches.

# To populate body / attributes for any rows that haven't been enriched yet:
.\.venv\Scripts\python.exe rescore.py    # network-bound, slow
```

## Useful commands

```powershell
# Force a run now
Start-ScheduledTask -TaskName ClWatcher

# Pause / resume / remove
Disable-ScheduledTask -TaskName ClWatcher
Enable-ScheduledTask  -TaskName ClWatcher
Unregister-ScheduledTask -TaskName ClWatcher -Confirm:$false

# Send a one-shot digest of current matches without waiting for new listings
.\.venv\Scripts\python.exe send_rescored_email.py
```

## Caveats

- Craigslist has no official API. The HTML scraper relies on the `.cl-static-search-result` block in their no-JS fallback page; if Craigslist changes their HTML, the fetcher needs updating.
- Salvage estimates are heuristics, not market prices — they need a tuning pass against your local market.
- The default 1.5 s request delay is polite throttling. Don't lower below ~0.7 s or you risk an IP-level block.
- Free listings get scooped in minutes. The 15-min cadence catches most but not all — going faster comes with rate-limit risk.

## License

[MIT](LICENSE)
