"""Phase 1: prepare batches for parallel subagent processing.

Reads cl_watcher's state.db read-only, runs the semantic prefilter to
drop buyer posts / accessories / excluded categories, then writes one
JSON file per batch into appraiser/batches/.

Each batch is a list of listing dicts the subagent can read directly.

Usage:
  python appraiser/prepare.py                # default batch size (30)
  python appraiser/prepare.py --batch-size 50
  python appraiser/prepare.py --limit 200    # only first N (for testing)
  python appraiser/prepare.py --all          # re-batch everything, even
                                              # already-appraised listings
"""
from __future__ import annotations
import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

import config
import db as db_mod
import rules
from models import Listing


log = logging.getLogger(__name__)

BATCH_DIR = config.CODE_DIR / "batches"
RESULTS_DIR = config.CODE_DIR / "results"


def _semantic_prefilter(
    listings: list[Listing],
    coords: dict[str, tuple],
) -> tuple[list[Listing], list[dict]]:
    """Returns (kept, dropped). dropped is a list of {rss_id, reason}
    that we record in case the user wants to audit what got cut.

    `coords` maps rss_id -> (lat, lon) so the distance prefilter can
    drop listings beyond config.MAX_PREFILTER_DISTANCE_KM."""
    kept: list[Listing] = []
    dropped: list[dict] = []
    for ll in listings:
        # Above-ceiling price drop. Anything > $30 is auto-SKIP per the
        # student's rule, so don't waste agent tokens on it. Keeps
        # section=='free' (price 0) and missing-price unknowns.
        if (ll.section != "free" and ll.ask_price is not None
                and ll.ask_price > config.PAID_HIGH_CEILING):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": f"above_${config.PAID_HIGH_CEILING}_ceiling"})
            continue
        lat, lon = coords.get(ll.rss_id, (None, None))
        if rules.is_too_far(lat, lon):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": "too_far"})
            continue
        if rules.is_buyer_post(ll.title, ll.body):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": "buyer_post"})
            continue
        if rules.is_excluded_category(ll.title):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": "excluded_category"})
            continue
        if rules.is_non_electronics(ll.title, ll.body):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": "non_electronics"})
            continue
        if rules.is_accessory_only(ll.title, ll.body):
            dropped.append({"rss_id": ll.rss_id, "title": ll.title,
                            "reason": "accessory_only"})
            continue
        kept.append(ll)
    return kept, dropped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size", type=int, default=100,
                   help="Listings per agent (default 100). Larger = fewer "
                        "system-prompt repetitions across the run.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--all", action="store_true",
                   help="Re-batch all listings (default skips appraised).")
    p.add_argument("--clear", action="store_true",
                   help="Delete existing batches/ and results/ first.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    if args.clear:
        if BATCH_DIR.exists():
            shutil.rmtree(BATCH_DIR)
        if RESULTS_DIR.exists():
            shutil.rmtree(RESULTS_DIR)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not config.SOURCE_DB_PATH.exists():
        print(f"cl_watcher state.db not found at {config.SOURCE_DB_PATH}.",
              file=sys.stderr)
        print("Has cl_watcher run yet?", file=sys.stderr)
        sys.exit(1)

    src = db_mod.open_source(config.SOURCE_DB_PATH)
    try:
        listings = db_mod.fetch_listings(
            src, limit=args.limit,
            only_unappraised=not args.all,
            appraisal_db=config.APPRAISAL_DB_PATH,
        )
    finally:
        src.close()

    log.info("Loaded %d listings from cl_watcher (only_unappraised=%s)",
             len(listings), not args.all)

    if not listings:
        print(json.dumps({"loaded": 0, "kept": 0, "batches": 0}))
        return

    # Coords from cl_watcher (needed for the distance prefilter AND
    # downstream by aggregate.py for tier classification).
    coords = _fetch_coords([ll.rss_id for ll in listings])

    kept, dropped = _semantic_prefilter(listings, coords)
    log.info("Semantic prefilter: %d kept, %d dropped",
             len(kept), len(dropped))

    # Write the dropped log so the user can audit
    drop_path = BATCH_DIR / "_dropped.json"
    drop_path.write_text(json.dumps(dropped, indent=2), encoding="utf-8")

    # Chunk into batches
    n = args.batch_size
    batches = [kept[i:i + n] for i in range(0, len(kept), n)]
    for idx, batch in enumerate(batches, start=1):
        path = BATCH_DIR / f"batch_{idx:03d}.json"
        payload = []
        for ll in batch:
            d = ll.model_dump()
            lat, lon = coords.get(ll.rss_id, (None, None))
            d["latitude"] = lat
            d["longitude"] = lon
            payload.append(d)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Manifest used by /appraise to know what to dispatch
    manifest = {
        "batch_size": n,
        "n_listings_kept": len(kept),
        "n_listings_dropped": len(dropped),
        "n_batches": len(batches),
        "batches": [str((BATCH_DIR / f"batch_{i:03d}.json").as_posix())
                    for i in range(1, len(batches) + 1)],
        "results_dir": str(RESULTS_DIR.as_posix()),
        "expected_results": [
            str((RESULTS_DIR / f"batch_{i:03d}.json").as_posix())
            for i in range(1, len(batches) + 1)
        ],
    }
    (BATCH_DIR / "_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")

    print(json.dumps({
        "loaded": len(listings),
        "kept": len(kept),
        "dropped": len(dropped),
        "batches": len(batches),
        "batch_dir": str(BATCH_DIR.as_posix()),
        "results_dir": str(RESULTS_DIR.as_posix()),
    }, indent=2))


def _fetch_coords(rss_ids: list[str]) -> dict[str, tuple]:
    if not rss_ids:
        return {}
    src = db_mod.open_source(config.SOURCE_DB_PATH)
    try:
        # Chunk to avoid hitting SQLite's parameter limit
        out: dict[str, tuple] = {}
        for i in range(0, len(rss_ids), 500):
            chunk = rss_ids[i:i + 500]
            rows = src.execute(
                "SELECT rss_id, latitude, longitude FROM seen_listings "
                "WHERE rss_id IN (%s)" % ",".join(["?"] * len(chunk)),
                chunk,
            ).fetchall()
            for r in rows:
                out[r[0]] = (r[1], r[2])
        return out
    finally:
        src.close()


if __name__ == "__main__":
    main()
