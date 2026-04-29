"""SQLite persistence: dedup, scoring history, run logs, first-run flag."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_listings (
    rss_id TEXT PRIMARY KEY,
    first_seen_at TEXT NOT NULL,
    title TEXT,
    body TEXT,
    link TEXT,
    posted_at TEXT,
    ask_price INTEGER,
    price_uncertain INTEGER DEFAULT 0,
    neighborhood TEXT,
    latitude REAL,
    longitude REAL,
    distance_km REAL,
    tier TEXT,
    geo_source TEXT,
    salvage_estimate INTEGER,
    score REAL,
    notified INTEGER DEFAULT 0,
    section TEXT,
    attributes TEXT
);
CREATE TABLE IF NOT EXISTS run_log (
    run_at TEXT PRIMARY KEY,
    fetched INTEGER, new INTEGER, scored INTEGER,
    notified INTEGER, errors INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY, value TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Idempotent ALTER TABLE for older DBs missing newer columns."""
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(seen_listings)").fetchall()}
    if "body" not in cols:
        conn.execute("ALTER TABLE seen_listings ADD COLUMN body TEXT")
    if "section" not in cols:
        conn.execute("ALTER TABLE seen_listings ADD COLUMN section TEXT")
    if "attributes" not in cols:
        conn.execute("ALTER TABLE seen_listings ADD COLUMN attributes TEXT")
    conn.commit()


def is_first_run(conn: sqlite3.Connection) -> bool:
    cur = conn.execute("SELECT value FROM meta WHERE key='initialized'")
    return cur.fetchone() is None


def mark_initialized(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('initialized', ?)",
        (datetime.utcnow().isoformat(),),
    )
    conn.commit()


def listing_exists(conn: sqlite3.Connection, rss_id: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen_listings WHERE rss_id=?", (rss_id,))
    return cur.fetchone() is not None


def insert_listing(conn, rss_id, title, body, link, posted_at, ask_price,
                   price_uncertain, neighborhood, latitude, longitude,
                   distance_km, tier, geo_source, salvage_estimate,
                   score, notified, section, attributes_json=None):
    conn.execute(
        """INSERT OR IGNORE INTO seen_listings
           (rss_id, first_seen_at, title, body, link, posted_at, ask_price,
            price_uncertain, neighborhood, latitude, longitude, distance_km,
            tier, geo_source, salvage_estimate, score, notified, section,
            attributes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (rss_id, datetime.utcnow().isoformat(), title, body, link, posted_at,
         ask_price, 1 if price_uncertain else 0, neighborhood,
         latitude, longitude, distance_km, tier, geo_source,
         salvage_estimate, score, notified, section, attributes_json),
    )
    conn.commit()


def gc_old(conn: sqlite3.Connection, days: int = 30) -> None:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn.execute("DELETE FROM seen_listings WHERE first_seen_at < ?", (cutoff,))
    conn.commit()


def log_run(conn, fetched, new, scored, notified, errors):
    conn.execute(
        """INSERT OR REPLACE INTO run_log
           (run_at, fetched, new, scored, notified, errors)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (datetime.utcnow().isoformat(), fetched, new, scored, notified, errors),
    )
    conn.commit()
