"""Re-evaluate every row in the DB with the current scoring logic.

Pass 1: fetch and store body+attributes for any row missing either.
Pass 2: re-run salvage / geo / notify decisions against the stored data.

This is safe to run while the watcher is idle. Don't run it concurrently
with watcher.py.

Usage:
    .venv/Scripts/python.exe rescore.py
    .venv/Scripts/python.exe rescore.py --no-fetch    # skip body re-fetch
"""
import argparse
import json
import sqlite3
import sys
import time

import config
import fetcher
import scoring
import storage


def _section_from_link(link: str) -> str:
    """Best-effort: free-stuff URLs use /zip/ category code."""
    return "free" if "/zip/" in (link or "") else "paid"


def fetch_missing_bodies(conn, log_every=25):
    rows = conn.execute(
        "SELECT rss_id, link, title FROM seen_listings "
        "WHERE (body IS NULL OR body = '' OR attributes IS NULL) "
        "AND tier != 'fetch_failed'"
    ).fetchall()
    print(f"Pass 1: {len(rows)} rows need body+attributes fetch")
    if not rows:
        return 0
    fetched = 0
    failed = 0
    for i, (rss_id, link, title) in enumerate(rows, 1):
        sample = {"rss_id": rss_id, "link": link, "title": title or "",
                  "section": _section_from_link(link)}
        details = fetcher.enrich_listing(sample)
        if details is None:
            failed += 1
            continue
        body = details.get("body") or ""
        attrs = details.get("attributes") or {}
        attrs_json = json.dumps(attrs) if attrs else "{}"
        conn.execute(
            "UPDATE seen_listings SET body = ?, attributes = ?, "
            "neighborhood = COALESCE(NULLIF(?, ''), neighborhood), "
            "latitude = COALESCE(?, latitude), "
            "longitude = COALESCE(?, longitude) "
            "WHERE rss_id = ?",
            (body, attrs_json, details.get("neighborhood") or "",
             details.get("latitude"), details.get("longitude"), rss_id),
        )
        conn.commit()
        fetched += 1
        if i % log_every == 0:
            print(f"  fetched {i}/{len(rows)} (ok={fetched}, failed={failed})")
    print(f"Pass 1 done. fetched={fetched} failed={failed}")
    return fetched


def rescore_all(conn):
    rows = conn.execute(
        "SELECT rss_id, title, body, link, ask_price, price_uncertain, "
        "neighborhood, latitude, longitude, tier, section, attributes "
        "FROM seen_listings"
    ).fetchall()
    print(f"Pass 2: re-scoring {len(rows)} rows")

    flipped_to_excluded = 0
    re_scored = 0
    skipped = 0

    for r in rows:
        (rss_id, title, body, link, ask, p_unc, hood, lat, lon, old_tier,
         section, attrs_json) = r
        if old_tier == "fetch_failed":
            skipped += 1
            continue

        title = title or ""
        body = body or ""
        if section is None:
            section = _section_from_link(link)
        try:
            attrs = json.loads(attrs_json) if attrs_json else {}
        except Exception:
            attrs = {}

        tier, dist, geo_src = scoring.classify_geo(
            lat, lon, title, body, hood or "")

        if tier == "EXCLUDE":
            new_score = 0.0
            new_salvage = 0
            new_notify = 0
            if old_tier != "EXCLUDE":
                flipped_to_excluded += 1
        else:
            salvage = scoring.estimate_salvage(title, body, attrs)
            ask_val = ask if ask is not None else (0 if section == "free" else 20)
            is_free = (section == "free") or (ask_val == 0 and not p_unc)
            really_good = scoring.is_really_good(title, body)
            new_salvage = salvage
            new_score = scoring.compute_score(salvage, ask_val, tier)
            new_notify = 1 if scoring.should_notify(
                salvage, ask_val, tier, is_free, really_good, bool(p_unc)
            ) else 0

        conn.execute(
            "UPDATE seen_listings SET "
            "salvage_estimate = ?, score = ?, tier = ?, "
            "distance_km = ?, geo_source = ?, notified = ?, "
            "section = COALESCE(section, ?) "
            "WHERE rss_id = ?",
            (new_salvage, new_score, tier, dist, geo_src,
             new_notify, section, rss_id),
        )
        re_scored += 1

    conn.commit()
    print(f"Pass 2 done. rescored={re_scored} skipped={skipped} "
          f"newly_excluded={flipped_to_excluded}")


def summary(conn):
    rows = conn.execute(
        "SELECT tier, COUNT(*), SUM(notified) "
        "FROM seen_listings GROUP BY tier ORDER BY tier"
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*), SUM(notified) FROM seen_listings"
    ).fetchone()
    print()
    print("Post-rescore summary:")
    print(f"  total: {total[0]}, matches: {total[1] or 0}")
    for tier, count, n in rows:
        print(f"    {tier or '(null)'}: {count} listings, "
              f"{n or 0} notify-flagged")
    print()
    print("Top 10 by score:")
    top = conn.execute(
        "SELECT score, tier, distance_km, ask_price, salvage_estimate, "
        "neighborhood, title "
        "FROM seen_listings WHERE notified = 1 "
        "AND tier IN ('A','B','C','D','needs_review') "
        "ORDER BY score DESC LIMIT 10"
    ).fetchall()
    for s, t, d, p, sv, h, ti in top:
        d_s = f"{d:.1f}km" if d is not None else "  ?  "
        p_s = f"${p}" if p is not None else "  ?  "
        print(f"  {s:>5.0f}  {t:<13} {d_s:>6}  {p_s:>5}  "
              f"salv ${sv:<4}  {(h or '')[:18]:<18}  "
              f"{(ti or '')[:65]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip body re-fetch (just rescore)")
    args = ap.parse_args()

    conn = storage.connect(config.DB_PATH)
    if not args.no_fetch:
        fetch_missing_bodies(conn)
    rescore_all(conn)
    summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
