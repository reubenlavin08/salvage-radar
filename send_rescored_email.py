"""One-shot: send an email digest of current notify-flagged matches.
Use after rescoring to deliver an updated picture without waiting for a
new listing to surface.
"""
import os
import sqlite3
import sys
from dotenv import load_dotenv

import config
import email_sender


def main():
    if not config.ENV_PATH.exists():
        print(f"No .env at {config.ENV_PATH}")
        sys.exit(1)
    load_dotenv(config.ENV_PATH)
    user = os.environ.get("GMAIL_USER", config.EMAIL_FROM)
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not pw:
        print("GMAIL_APP_PASSWORD missing in .env")
        sys.exit(1)

    conn = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT title, link, posted_at, ask_price, price_uncertain,
               salvage_estimate, score, tier, distance_km, neighborhood, body
        FROM seen_listings
        WHERE notified = 1 AND tier IN ('A','B','C','D','needs_review')
        ORDER BY score DESC
    """).fetchall()
    conn.close()

    if not rows:
        print("No matches to send.")
        return

    listings = []
    for r in rows:
        listings.append({
            "title": r["title"],
            "link": r["link"],
            "posted_at": r["posted_at"],
            "ask_price": r["ask_price"] or 0,
            "price_uncertain": bool(r["price_uncertain"]),
            "salvage_estimate": r["salvage_estimate"] or 0,
            "score": r["score"] or 0,
            "tier": r["tier"],
            "neighborhood": r["neighborhood"] or "",
            "body": r["body"] or "",
        })

    total_value = sum(L["salvage_estimate"] for L in listings)
    top_title = max(listings, key=lambda x: x["score"])["title"]
    subject = (f"CL parts: rescored — {len(listings)} clean matches "
               f"(~${total_value} salvage). Top: {top_title[:50]}")
    html = email_sender.build_digest_html(
        listings,
        header=(f"Rescored — {len(listings)} clean matches "
                f"after title+body+attributes tuning"))
    text = "\n".join(
        f"- {L['title']} (${L['ask_price']}, "
        f"est ${L['salvage_estimate']}, tier {L['tier']}, "
        f"{L['neighborhood']}): {L['link']}"
        for L in listings)

    ok = email_sender.send_email(subject, html, text, user, pw)
    print(f"Sent: {ok}, listings: {len(listings)}")


if __name__ == "__main__":
    main()
