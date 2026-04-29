"""Live-view dashboard for the watcher's database. Refreshes every 3 seconds.

Usage (from project dir):
    .venv/Scripts/python.exe live_view.py
"""
import sqlite3
import time
import sys
import os
from datetime import datetime

import config

REFRESH_SEC = 3
TOP_LIMIT = 20
RECENT_LIMIT = 8
TARGET_BACKFILL = 1376  # rough; for progress %


def truncate(s, n):
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def progress_bar(pct, width=30):
    filled = int(pct * width / 100)
    return "[" + "#" * filled + "-" * (width - filled) + f"] {pct:>3.0f}%"


def query_top(conn, limit):
    return conn.execute("""
        SELECT score, tier, distance_km, ask_price, price_uncertain,
               salvage_estimate, neighborhood, title, link
        FROM seen_listings
        WHERE tier IN ('A','B','C','D','needs_review')
          AND notified = 1
        ORDER BY score DESC
        LIMIT ?
    """, (limit,)).fetchall()


def query_recent(conn, limit):
    return conn.execute("""
        SELECT first_seen_at, tier, distance_km, ask_price, salvage_estimate,
               score, neighborhood, title
        FROM seen_listings
        ORDER BY first_seen_at DESC
        LIMIT ?
    """, (limit,)).fetchall()


def query_summary(conn):
    breakdown = {}
    for tier, count in conn.execute(
        "SELECT tier, COUNT(*) FROM seen_listings GROUP BY tier"
    ).fetchall():
        breakdown[tier] = breakdown.get(tier, 0) + count
    matches = {}
    for tier, count in conn.execute(
        "SELECT tier, COUNT(*) FROM seen_listings "
        "WHERE notified = 1 AND tier IN ('A','B','C','D','needs_review') "
        "GROUP BY tier"
    ).fetchall():
        matches[tier] = count
    total = sum(breakdown.values())
    total_matches = sum(matches.values())
    return breakdown, matches, total, total_matches


def render(conn):
    breakdown, matches, total, total_matches = query_summary(conn)
    top = query_top(conn, TOP_LIMIT)
    recent = query_recent(conn, RECENT_LIMIT)
    now = datetime.now().strftime("%H:%M:%S")

    lines = []
    lines.append("=" * 110)
    pct = min(100, 100 * total / TARGET_BACKFILL) if TARGET_BACKFILL else 0
    lines.append(f" cl_watcher live  [{now}]   "
                 f"{progress_bar(pct)}   "
                 f"{total} processed / {total_matches} matches")
    lines.append("=" * 110)

    lines.append("")
    tiers = ["A", "B", "C", "D", "needs_review", "unknown",
             "EXCLUDE", "fetch_failed"]
    parts = []
    for t in tiers:
        c = breakdown.get(t, 0)
        if c == 0:
            continue
        m = matches.get(t, 0)
        if t in ("A", "B", "C", "D", "needs_review"):
            parts.append(f"{t}: {c} ({m} match)")
        else:
            parts.append(f"{t}: {c}")
    lines.append(" By tier   " + "    ".join(parts))

    lines.append("")
    lines.append("-" * 110)
    lines.append(f" TOP {TOP_LIMIT} MATCHES BY SCORE")
    lines.append("-" * 110)
    lines.append(f" {'Score':>5}  {'Tier':<13} {'Dist':>5}  "
                 f"{'Price':>6}  {'Salv':>5}  {'Neighborhood':<18}  Title")
    if not top:
        lines.append("   (none yet)")
    for score, tier, dist, ask, p_unc, salv, hood, title, link in top:
        dist_s = f"{dist:.1f}k" if dist is not None else " ?  "
        price_s = f"${ask}{'?' if p_unc else ''}"
        lines.append(
            f" {score:>5.0f}  {tier:<13} {dist_s:>5}  "
            f"{price_s:>6}  ${salv:>4}  "
            f"{truncate(hood, 18):<18}  {truncate(title, 55)}"
        )

    lines.append("")
    lines.append("-" * 110)
    lines.append(f" RECENTLY PROCESSED (last {RECENT_LIMIT})")
    lines.append("-" * 110)
    lines.append(f" {'Time':<8}  {'Tier':<13} {'Dist':>5}  "
                 f"{'Price':>5}  {'Salv':>5}  {'Score':>5}  "
                 f"{'Neighborhood':<18}  Title")
    for ts, tier, dist, ask, salv, score, hood, title in recent:
        try:
            tstr = datetime.fromisoformat(ts).strftime("%H:%M:%S")
        except Exception:
            tstr = (ts or "")[:8]
        dist_s = f"{dist:.1f}k" if dist is not None else " ?  "
        ask_s = f"${ask}" if ask is not None else " ?  "
        lines.append(
            f" {tstr:<8}  {tier:<13} {dist_s:>5}  "
            f"{ask_s:>5}  ${(salv or 0):>4}  {(score or 0):>5.0f}  "
            f"{truncate(hood, 18):<18}  {truncate(title, 55)}"
        )

    lines.append("")
    lines.append(f"Refreshing every {REFRESH_SEC}s. Ctrl+C to exit.")
    return "\n".join(lines)


def main():
    db = config.DB_PATH
    if not db.exists():
        print(f"DB not found at {db}. Run watcher.py first.")
        sys.exit(1)
    while True:
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            output = render(conn)
            conn.close()
        except sqlite3.Error as e:
            output = f"DB read error: {e}"
        os.system("cls" if os.name == "nt" else "clear")
        print(output)
        time.sleep(REFRESH_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
