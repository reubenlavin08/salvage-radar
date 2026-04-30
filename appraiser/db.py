"""DB layer.

Two databases:
  - source DB    : cl_watcher's state.db. We open it READ-ONLY (URI mode)
                   so an accidental write is impossible.
  - appraisal DB : our own. Holds triage results, extractions, and final
                   appraisals, keyed by rss_id.

The comps cache is a third small SQLite file, see comps.py.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from models import Appraisal, ExtractionResult, Listing, TriageResult


APPRAISAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS triage (
    rss_id TEXT PRIMARY KEY,
    run_at TEXT NOT NULL,
    item_kind TEXT,
    salvage_low INTEGER,
    salvage_high INTEGER,
    confidence TEXT,
    worth_deep INTEGER,
    red_flags TEXT,
    reasoning TEXT,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS extraction (
    rss_id TEXT PRIMARY KEY,
    run_at TEXT NOT NULL,
    item_kind TEXT,
    overall_condition TEXT,
    components_json TEXT,
    extraction_confidence TEXT,
    notes TEXT,
    raw_json TEXT
);
CREATE TABLE IF NOT EXISTS appraisal (
    rss_id TEXT PRIMARY KEY,
    run_at TEXT NOT NULL,
    ask_price INTEGER,
    salvage_low REAL,
    salvage_high REAL,
    salvage_realized REAL,
    ratio REAL,
    recommendation TEXT,
    confidence TEXT,
    summary TEXT,
    line_items_json TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_appraisal_ratio ON appraisal(ratio DESC);
CREATE INDEX IF NOT EXISTS idx_appraisal_reco ON appraisal(recommendation);
"""


# ---------- Source DB (read-only) ----------

def open_source(db_path: Path) -> sqlite3.Connection:
    """Open the cl_watcher DB in read-only mode via URI."""
    if not db_path.exists():
        raise FileNotFoundError(
            f"cl_watcher source DB not found at {db_path}. "
            "Run cl_watcher at least once to populate it, or point "
            "appraiser.config.SOURCE_DB_PATH elsewhere."
        )
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def fetch_listings(
    conn: sqlite3.Connection,
    limit: Optional[int] = None,
    only_unappraised: bool = False,
    appraisal_db: Optional[Path] = None,
) -> list[Listing]:
    """Pull listings from cl_watcher's seen_listings table.

    only_unappraised=True skips rss_ids that already have a row in our
    appraisal DB. We resolve that by attaching the appraisal DB read-only
    so we can left-anti-join in pure SQL.
    """
    sql = (
        "SELECT rss_id, title, body, link, posted_at, ask_price, "
        "price_uncertain, neighborhood, section, attributes "
        "FROM seen_listings "
    )
    if (only_unappraised and appraisal_db is not None
            and Path(appraisal_db).exists()):
        conn.execute(f"ATTACH DATABASE 'file:{appraisal_db}?mode=ro' "
                     "AS appraisal_db")
        sql += ("WHERE rss_id NOT IN "
                "(SELECT rss_id FROM appraisal_db.appraisal) ")
    sql += "ORDER BY first_seen_at DESC "
    if limit:
        sql += f"LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()
    out: list[Listing] = []
    for r in rows:
        (rss_id, title, body, link, posted_at, ask_price,
         price_uncertain, neighborhood, section, attributes) = r
        out.append(Listing(
            rss_id=rss_id,
            title=title or "",
            body=body or "",
            link=link or "",
            posted_at=posted_at,
            ask_price=ask_price,
            price_uncertain=bool(price_uncertain),
            neighborhood=neighborhood,
            section=section,
            attributes_json=attributes,
        ))
    return out


# ---------- Appraisal DB (read-write) ----------

def open_appraisal(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(APPRAISAL_SCHEMA)
    return conn


def upsert_triage(conn: sqlite3.Connection, t: TriageResult) -> None:
    conn.execute(
        """INSERT INTO triage (rss_id, run_at, item_kind, salvage_low,
                               salvage_high, confidence, worth_deep,
                               red_flags, reasoning, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(rss_id) DO UPDATE SET
              run_at=excluded.run_at, item_kind=excluded.item_kind,
              salvage_low=excluded.salvage_low,
              salvage_high=excluded.salvage_high,
              confidence=excluded.confidence,
              worth_deep=excluded.worth_deep,
              red_flags=excluded.red_flags,
              reasoning=excluded.reasoning,
              raw_json=excluded.raw_json""",
        (t.rss_id, datetime.utcnow().isoformat(), t.item_kind,
         t.coarse_salvage_low, t.coarse_salvage_high, t.confidence,
         int(t.worth_deep_appraisal), json.dumps(t.red_flags),
         t.reasoning, t.model_dump_json()),
    )
    conn.commit()


def upsert_extraction(conn: sqlite3.Connection, e: ExtractionResult) -> None:
    conn.execute(
        """INSERT INTO extraction (rss_id, run_at, item_kind,
                                   overall_condition, components_json,
                                   extraction_confidence, notes, raw_json)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(rss_id) DO UPDATE SET
              run_at=excluded.run_at, item_kind=excluded.item_kind,
              overall_condition=excluded.overall_condition,
              components_json=excluded.components_json,
              extraction_confidence=excluded.extraction_confidence,
              notes=excluded.notes, raw_json=excluded.raw_json""",
        (e.rss_id, datetime.utcnow().isoformat(), e.item_kind,
         e.overall_condition,
         json.dumps([c.model_dump() for c in e.components]),
         e.extraction_confidence, e.notes, e.model_dump_json()),
    )
    conn.commit()


def upsert_appraisal(conn: sqlite3.Connection, a: Appraisal) -> None:
    conn.execute(
        """INSERT INTO appraisal (rss_id, run_at, ask_price, salvage_low,
                                  salvage_high, salvage_realized, ratio,
                                  recommendation, confidence, summary,
                                  line_items_json, raw_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(rss_id) DO UPDATE SET
              run_at=excluded.run_at, ask_price=excluded.ask_price,
              salvage_low=excluded.salvage_low,
              salvage_high=excluded.salvage_high,
              salvage_realized=excluded.salvage_realized,
              ratio=excluded.ratio,
              recommendation=excluded.recommendation,
              confidence=excluded.confidence, summary=excluded.summary,
              line_items_json=excluded.line_items_json,
              raw_json=excluded.raw_json""",
        (a.rss_id, datetime.utcnow().isoformat(), a.ask_price,
         a.salvage_low, a.salvage_high, a.salvage_realized, a.ratio,
         a.recommendation, a.confidence, a.summary,
         json.dumps([li.model_dump() for li in a.line_items]),
         a.model_dump_json()),
    )
    conn.commit()


def upsert_rejection(
    conn: sqlite3.Connection,
    rss_id: str,
    ask_price: Optional[int],
    reason: str,
) -> None:
    """Write a REJECTED row for a listing the prefilter dropped (too far,
    too expensive, buyer post, accessory-only, etc.) so the dashboard can
    distinguish "deliberately filtered out" from "still pending".

    INSERT OR IGNORE: if the row already has any appraisal (LLM or earlier
    rejection), this is a no-op. Re-running prepare.py is therefore safe.
    """
    conn.execute(
        """INSERT OR IGNORE INTO appraisal (
            rss_id, run_at, ask_price, salvage_low, salvage_high,
            salvage_realized, ratio, recommendation, confidence,
            summary, line_items_json, raw_json
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rss_id, datetime.utcnow().isoformat(), ask_price,
         0.0, 0.0, 0.0, 0.0,
         "REJECTED", "prefilter", f"Prefilter: {reason}",
         "[]", "{}"),
    )


def fetch_triage_passers(conn: sqlite3.Connection) -> Iterable[str]:
    """rss_ids that triage marked worth_deep=1, missing from extraction table."""
    rows = conn.execute(
        """SELECT t.rss_id FROM triage t
           LEFT JOIN extraction e ON e.rss_id = t.rss_id
           WHERE t.worth_deep = 1 AND e.rss_id IS NULL"""
    ).fetchall()
    return [r[0] for r in rows]


def fetch_top(conn: sqlite3.Connection, n: int = 30) -> list[dict]:
    rows = conn.execute(
        """SELECT a.rss_id, a.ask_price, a.salvage_realized, a.ratio,
                  a.recommendation, a.confidence, a.summary
           FROM appraisal a
           WHERE a.recommendation IN ('BUY','MAYBE')
           ORDER BY a.ratio DESC LIMIT ?""", (n,)
    ).fetchall()
    cols = ["rss_id", "ask_price", "salvage_realized", "ratio",
            "recommendation", "confidence", "summary"]
    return [dict(zip(cols, r)) for r in rows]
