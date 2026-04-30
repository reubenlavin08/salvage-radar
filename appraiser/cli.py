"""Command-line entry point.

Examples:
  python cli.py dry-run                # use samples/sample_listings.json
  python cli.py run --limit 50         # appraise 50 fresh listings
  python cli.py run                    # appraise all unappraised
  python cli.py report --top 30        # print top BUY/MAYBE rows
  python cli.py inspect <rss_id>       # full triage+extraction+appraisal
"""
from __future__ import annotations
import argparse
import json
import logging
import sys
from pathlib import Path

import config
import db as db_mod
from pipeline import run_pipeline


def _setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    log_path = config.LOG_DIR / "appraiser.log"
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def cmd_run(args):
    summary = run_pipeline(
        limit=args.limit,
        only_unappraised=not args.all,
        sample_path=None,
    )
    print(json.dumps(summary, indent=2))


def cmd_dry_run(args):
    sample = config.CODE_DIR / "samples" / "sample_listings.json"
    if not sample.exists():
        print(f"Sample fixture not found at {sample}", file=sys.stderr)
        sys.exit(1)
    summary = run_pipeline(
        limit=args.limit,
        only_unappraised=False,
        sample_path=sample,
    )
    print(json.dumps(summary, indent=2))


def cmd_report(args):
    conn = db_mod.open_appraisal(config.APPRAISAL_DB_PATH)
    try:
        rows = db_mod.fetch_top(conn, n=args.top)
    finally:
        conn.close()
    if not rows:
        print("No appraisals yet. Run `python cli.py run` first.")
        return
    fmt = "{:<24} {:>5} {:>9} {:>6} {:>5}  {:<5}  {}"
    print(fmt.format("rss_id", "ask", "salvage", "ratio",
                     "conf", "rec", "summary"))
    print("-" * 110)
    for r in rows:
        s = (r["summary"] or "").splitlines()[0][:60]
        print(fmt.format(
            r["rss_id"][:22], f"${r['ask_price']}",
            f"${r['salvage_realized']:.0f}", f"{r['ratio']:.2f}x",
            r["confidence"][:4], r["recommendation"], s))


def cmd_inspect(args):
    conn = db_mod.open_appraisal(config.APPRAISAL_DB_PATH)
    try:
        for table in ("triage", "extraction", "appraisal"):
            row = conn.execute(
                f"SELECT raw_json FROM {table} WHERE rss_id=?",
                (args.rss_id,)
            ).fetchone()
            print(f"\n=== {table} ===")
            if not row:
                print("(no row)")
                continue
            print(json.dumps(json.loads(row[0]), indent=2))
    finally:
        conn.close()


def cmd_status(args):
    conn = db_mod.open_appraisal(config.APPRAISAL_DB_PATH)
    try:
        c1 = conn.execute("SELECT COUNT(*) FROM triage").fetchone()[0]
        c2 = conn.execute("SELECT COUNT(*) FROM extraction").fetchone()[0]
        c3 = conn.execute("SELECT COUNT(*) FROM appraisal").fetchone()[0]
        recs = dict(conn.execute(
            "SELECT recommendation, COUNT(*) FROM appraisal "
            "GROUP BY recommendation").fetchall())
    finally:
        conn.close()
    print(json.dumps({
        "triage_rows": c1, "extraction_rows": c2,
        "appraisal_rows": c3, "recommendations": recs,
        "ebay_backend": config.EBAY_BACKEND,
        "source_db": str(config.SOURCE_DB_PATH),
        "appraisal_db": str(config.APPRAISAL_DB_PATH),
    }, indent=2))


def main():
    p = argparse.ArgumentParser(prog="appraiser")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="Appraise listings from cl_watcher DB.")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--all", action="store_true",
                    help="Re-appraise everything, not just unappraised.")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("dry-run",
                        help="Run on samples/sample_listings.json.")
    sp.add_argument("--limit", type=int, default=None)
    sp.set_defaults(func=cmd_dry_run)

    sp = sub.add_parser("report", help="Top BUY/MAYBE picks.")
    sp.add_argument("--top", type=int, default=30)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("inspect", help="Full record for one rss_id.")
    sp.add_argument("rss_id")
    sp.set_defaults(func=cmd_inspect)

    sp = sub.add_parser("status", help="Row counts + paths.")
    sp.set_defaults(func=cmd_status)

    args = p.parse_args()
    _setup_logging(args.verbose)
    args.func(args)


if __name__ == "__main__":
    main()
