"""Phase 3: collect agent results into the appraisal DB.

Reads every appraiser/results/batch_NNN.json, joins back to the listings
in cl_watcher (read-only, just for lat/lon/ask_price reconciliation),
applies rules.decide() with distance tier, and writes Appraisal rows.

Idempotent — re-running just upserts.
"""
from __future__ import annotations
import argparse
import glob
import json
import logging
import sys
from pathlib import Path

import config
import db as db_mod
import rules
from models import Appraisal, ComponentValuation


log = logging.getLogger(__name__)

RESULTS_DIR = config.CODE_DIR / "results"
BATCH_DIR = config.CODE_DIR / "batches"


def _load_batches() -> dict[str, dict]:
    """rss_id -> input listing dict (with lat/lon)."""
    out: dict[str, dict] = {}
    for f in sorted(BATCH_DIR.glob("batch_*.json")):
        for ll in json.loads(f.read_text(encoding="utf-8")):
            out[ll["rss_id"]] = ll
    return out


def _load_results() -> list[dict]:
    out: list[dict] = []
    files = sorted(RESULTS_DIR.glob("batch_*.json"))
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("Skipping malformed result file %s: %s", f, e)
            continue
        if isinstance(data, dict) and "results" in data:
            data = data["results"]
        if not isinstance(data, list):
            log.warning("Result file %s isn't a JSON array; skipping", f)
            continue
        for r in data:
            r["_source_file"] = str(f.name)
            out.append(r)
    return out


def _to_appraisal(record: dict, listing: dict) -> Appraisal:
    rss_id = record["rss_id"]
    ask = listing.get("ask_price") or 0
    if listing.get("section") == "free":
        ask = 0

    if record.get("skipped"):
        return Appraisal(
            rss_id=rss_id, ask_price=ask,
            salvage_low=0.0, salvage_high=0.0,
            salvage_realized=0.0, ratio=0.0,
            recommendation="SKIP",
            confidence="high",
            line_items=[],
            summary=f"prefilter: {record.get('skip_reason', 'unknown')}",
        )

    components = record.get("components", []) or []
    line_items = [
        ComponentValuation(
            category=c["category"],
            label=c["label"],
            quantity=c.get("quantity", 1),
            unit_low=float(c.get("unit_low_cad", 0) or 0),
            unit_high=float(c.get("unit_high_cad", 0) or 0),
            line_low=float(c.get("line_low_cad", 0) or 0),
            line_high=float(c.get("line_high_cad", 0) or 0),
            comps_used=int(c.get("comp_n", 0) or 0),
            confidence=c.get("confidence", "low"),
            rationale=c.get("rationale", ""),
        ) for c in components
    ]
    salvage_low = float(record.get("salvage_low_cad", 0) or 0)
    salvage_high = float(record.get("salvage_high_cad", 0) or 0)
    midpoint = (salvage_low + salvage_high) / 2.0
    realized = midpoint * config.SALVAGE_REALIZATION_FACTOR

    # Distance tier from cl_watcher coords
    lat = listing.get("latitude")
    lon = listing.get("longitude")
    tier, dist = rules.classify_distance(lat, lon)
    tier_mult = config.TIER_WEIGHTS.get(tier, 1.0)
    realized_for_rule = realized * tier_mult

    item_kind = record.get("item_kind", "")
    cat_key, really_good, sim = rules.category_of(
        item_kind, [c["category"] for c in components])

    if rules.is_excluded_category(item_kind):
        decision, reason = "SKIP", "excluded category"
    else:
        decision, reason = rules.decide(
            ask, realized_for_rule, tier, really_good)

    match_note = (f"semantic match {sim:.2f}" if 0 < sim < 1.0
                  else "exact match" if sim == 1.0 else "no category")
    summary = (
        f"{record.get('summary', '')}\n"
        f"[rule] tier={tier} "
        f"({f'{dist:.1f} km' if dist is not None else 'no coords'}), "
        f"category={cat_key} "
        f"({'really-good' if really_good else 'standard'}, {match_note}), "
        f"realized×tier=${realized_for_rule:.0f} → {decision}: {reason}"
    )

    return Appraisal(
        rss_id=rss_id, ask_price=ask,
        salvage_low=salvage_low, salvage_high=salvage_high,
        salvage_realized=realized,
        ratio=realized / max(ask, 1),
        recommendation=decision,
        confidence=record.get("extraction_confidence", "low"),
        line_items=line_items,
        summary=summary,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", default=str(RESULTS_DIR))
    p.add_argument("--top", type=int, default=20,
                   help="How many top picks to print at the end.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s: %(message)s")

    listings_by_id = _load_batches()
    if not listings_by_id:
        print("No batches found. Run prepare.py first.", file=sys.stderr)
        sys.exit(1)
    log.info("Loaded %d listings from batches/", len(listings_by_id))

    records = _load_results()
    if not records:
        print("No agent result files found in results/. "
              "Did the agents run?", file=sys.stderr)
        sys.exit(1)
    log.info("Loaded %d records from results/", len(records))

    conn = db_mod.open_appraisal(config.APPRAISAL_DB_PATH)
    try:
        n_written = 0
        n_skipped = 0
        for rec in records:
            rss_id = rec.get("rss_id")
            if not rss_id or rss_id not in listings_by_id:
                log.warning("Result for unknown rss_id %r; skipping", rss_id)
                continue
            try:
                appr = _to_appraisal(rec, listings_by_id[rss_id])
            except Exception as e:
                log.warning("Bad record for %s: %s", rss_id, e)
                continue
            db_mod.upsert_appraisal(conn, appr)
            n_written += 1
            if appr.recommendation == "SKIP":
                n_skipped += 1

        # Summary
        recs = dict(conn.execute(
            "SELECT recommendation, COUNT(*) FROM appraisal "
            "GROUP BY recommendation").fetchall())
        top_rows = db_mod.fetch_top(conn, n=args.top)
    finally:
        conn.close()

    print(json.dumps({
        "result_records": len(records),
        "written": n_written,
        "skipped": n_skipped,
        "recommendations_total": recs,
    }, indent=2))

    if top_rows:
        print(f"\nTop {len(top_rows)} BUY/MAYBE picks:\n")
        fmt = "{:<22} {:>5} {:>9} {:>6} {:>5}  {:<5}  {}"
        print(fmt.format("rss_id", "ask", "salvage", "ratio",
                         "conf", "rec", "summary"))
        print("-" * 110)
        for r in top_rows:
            s = (r["summary"] or "").splitlines()[0][:60]
            print(fmt.format(
                r["rss_id"][:22], f"${r['ask_price']}",
                f"${r['salvage_realized']:.0f}",
                f"{r['ratio']:.2f}x", r["confidence"][:4],
                r["recommendation"], s))


if __name__ == "__main__":
    main()
