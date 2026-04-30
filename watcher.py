"""Main entrypoint: fetch -> dedup -> enrich -> score -> notify."""
import json
import os
import sys
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from dotenv import load_dotenv

import config
import storage
import fetcher
import scoring
import email_sender


def setup_logging() -> logging.Logger:
    log_file = config.LOG_DIR / "watcher.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = TimedRotatingFileHandler(log_file, when="D", backupCount=7,
                                  encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers = [fh, sh]
    return logging.getLogger("watcher")


def load_secrets(log: logging.Logger):
    if not config.ENV_PATH.exists():
        log.error(f"No .env file at {config.ENV_PATH}. "
                  "Create it with GMAIL_USER and GMAIL_APP_PASSWORD.")
        sys.exit(1)
    load_dotenv(config.ENV_PATH)
    user = os.environ.get("GMAIL_USER", config.EMAIL_FROM)
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        log.error("GMAIL_APP_PASSWORD missing in .env")
        sys.exit(1)
    return user, pw


def main():
    log = setup_logging()
    user, pw = load_secrets(log)

    conn = storage.connect(config.DB_PATH)
    first_run = storage.is_first_run(conn)
    log.info(f"Run start. first_run={first_run}")

    listings = fetcher.collect_search_results()
    log.info(f"Fetched {len(listings)} unique listings from "
             f"{len(config.SEARCH_TERMS)*2} search pages")

    new_listings = [L for L in listings
                    if not storage.listing_exists(conn, L["rss_id"])]
    log.info(f"{len(new_listings)} are new (not in DB)")

    # Publish run target to meta so the dashboard shows accurate progress
    target_total = storage.connect(config.DB_PATH).execute(
        "SELECT COUNT(*) FROM seen_listings").fetchone()[0] + len(new_listings)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('current_target', ?)",
                 (str(target_total),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('current_phase', ?)",
                 ("Initial backfill" if first_run else "Incremental scan",))
    # Heartbeat — the dashboard surfaces this as "last check ran" so the
    # user can tell the watcher is actually polling Craigslist even on
    # cycles where every listing is a duplicate (no new rows inserted).
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) "
        "VALUES('last_check_started_at', ?)",
        (datetime.utcnow().isoformat() + "Z",))
    conn.commit()

    notify_batch = []
    errors = 0
    scored = 0
    enriched = 0

    for i, L in enumerate(new_listings, 1):
        if i % 50 == 0:
            log.info(f"  ... enriched {i}/{len(new_listings)}")
        try:
            details = fetcher.enrich_listing(L)
            if details is None:
                storage.insert_listing(conn, L["rss_id"], L["title"], "",
                                       L["link"], "", None, False, "",
                                       None, None, None, "fetch_failed",
                                       "none", 0, 0.0, 0, L["section"], None)
                errors += 1
                continue
            enriched += 1

            attrs = details.get("attributes") or {}
            attrs_json = json.dumps(attrs) if attrs else None

            tier, dist, geo_source = scoring.classify_geo(
                details["latitude"], details["longitude"],
                L["title"], details["body"], details["neighborhood"])

            # Hard scrape-time radius: anything geocoded beyond
            # MAX_SCRAPE_DISTANCE_KM is force-excluded so we don't waste
            # body-fetch / scoring / DB-write effort on it. Listings with
            # no usable geo (dist is None) fall through to the regular
            # classify_geo decision.
            if (dist is not None
                    and dist > config.MAX_SCRAPE_DISTANCE_KM):
                tier = "EXCLUDE"

            ask, is_free, price_uncertain = scoring.reconcile_price(
                details["ask_price"], details["price_unknown"], L["section"])

            if tier == "EXCLUDE":
                storage.insert_listing(
                    conn, L["rss_id"], L["title"], details["body"],
                    L["link"], details["posted_at"], ask, price_uncertain,
                    details["neighborhood"], details["latitude"],
                    details["longitude"], dist, "EXCLUDE", geo_source,
                    0, 0.0, 0, L["section"], attrs_json)
                continue

            salvage = scoring.estimate_salvage(L["title"], details["body"], attrs)
            really_good = scoring.is_really_good(L["title"], details["body"])
            score = scoring.compute_score(salvage, ask, tier)
            notify = scoring.should_notify(salvage, ask, tier, is_free,
                                           really_good, price_uncertain)
            scored += 1

            storage.insert_listing(
                conn, L["rss_id"], L["title"], details["body"],
                L["link"], details["posted_at"], ask, price_uncertain,
                details["neighborhood"], details["latitude"],
                details["longitude"], dist, tier, geo_source,
                salvage, score, 1 if notify else 0, L["section"], attrs_json)

            if notify:
                notify_batch.append({
                    "title": L["title"],
                    "link": L["link"],
                    "posted_at": details["posted_at"],
                    "ask_price": ask,
                    "price_uncertain": price_uncertain,
                    "salvage_estimate": salvage,
                    "score": score,
                    "tier": tier,
                    "distance_km": dist,
                    "geo_source": geo_source,
                    "neighborhood": details["neighborhood"],
                    "body": details["body"],
                })
        except Exception as e:
            log.exception(f"Error scoring {L.get('rss_id')}: {e}")
            errors += 1

    log.info(f"Enrichment done: {enriched}/{len(new_listings)} succeeded")

    storage.gc_old(conn)
    storage.log_run(conn, len(listings), len(new_listings), scored,
                    len(notify_batch), errors)

    if first_run:
        if notify_batch:
            total_value = sum(L["salvage_estimate"] for L in notify_batch)
            top_title = max(notify_batch, key=lambda x: x["score"])["title"]
            subject = (f"CL watcher live — {len(notify_batch)} initial matches "
                       f"(~${total_value} salvage). Top: {top_title[:50]}")
            html = email_sender.build_digest_html(
                notify_batch,
                header=(f"Craigslist watcher is live — "
                        f"{len(notify_batch)} initial matches"))
            text = "\n".join(
                f"- {L['title']} (${L['ask_price']}, "
                f"est ${L['salvage_estimate']}, tier {L['tier']}, "
                f"{L['neighborhood']}): {L['link']}"
                for L in notify_batch)
        else:
            subject = "CL watcher live — setup confirmation (0 initial matches)"
            html = email_sender.build_digest_html(
                [],
                header=("Craigslist watcher is live. Setup confirmed. "
                        "No initial matches; future runs will email you "
                        "as new listings appear."))
            text = ("Craigslist watcher setup confirmed. "
                    f"Backfilled {len(listings)} listings into the database. "
                    "No initial matches surfaced. "
                    "Future runs every 15 minutes will email you when "
                    "new listings match your criteria.")

        ok = email_sender.send_email(subject, html, text, user, pw)
        if ok:
            storage.mark_initialized(conn)
            log.info("First-run email sent; DB marked initialized")
        else:
            log.error("First-run email send failed; DB NOT marked initialized "
                      "(next run will retry)")
    else:
        if notify_batch:
            total_value = sum(L["salvage_estimate"] for L in notify_batch)
            top_title = max(notify_batch, key=lambda x: x["score"])["title"]
            subject = (f"CL parts: {len(notify_batch)} new "
                       f"(~${total_value} salvage). Top: {top_title[:50]}")
            html = email_sender.build_digest_html(notify_batch)
            text = "\n".join(
                f"- {L['title']} (${L['ask_price']}, "
                f"est ${L['salvage_estimate']}, tier {L['tier']}, "
                f"{L['neighborhood']}): {L['link']}"
                for L in notify_batch)
            email_sender.send_email(subject, html, text, user, pw)
            log.info(f"Sent digest with {len(notify_batch)} listings")
        else:
            log.info("No matches this run; no email sent")

    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('current_phase', 'Idle')")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) "
        "VALUES('last_check_finished_at', ?)",
        (datetime.utcnow().isoformat() + "Z",))
    conn.commit()
    log.info(f"Run done. fetched={len(listings)} new={len(new_listings)} "
             f"scored={scored} notified={len(notify_batch)} errors={errors}")


if __name__ == "__main__":
    main()
