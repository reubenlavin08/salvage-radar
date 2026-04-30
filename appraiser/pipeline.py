"""End-to-end orchestrator.

run_pipeline()
  1. Load N unappraised listings from cl_watcher (read-only).
  2. Triage each (Option 1, Haiku).        → triage table
  3. For passers, extract components       → extraction table
  4. For all components in passers, batch-fetch eBay comps (cached).
  5. Valuate each (Sonnet) using comps     → appraisal table
  6. Apply rules.decide() to overwrite recommendation with the user's
     deterministic rule, taking distance-tier into account.

Each stage is independently restartable. If you Ctrl+C in the middle,
just run again — the only re-work is the in-flight items.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from anthropic import Anthropic
from dotenv import load_dotenv

import comps as comps_mod
import config
import db as db_mod
import extractor
import rules
import triage as triage_mod
import valuator
from models import Appraisal, CompsResult, ExtractionResult, Listing


log = logging.getLogger(__name__)


def _load_env() -> None:
    if config.ENV_PATH.exists():
        load_dotenv(config.ENV_PATH)


def _client() -> Anthropic:
    _load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Add it to "
            f"{config.ENV_PATH} or export it."
        )
    return Anthropic()


# ---------- Listing lat/lon resolution ----------
# cl_watcher's seen_listings stores latitude/longitude; we pull them via
# a second read-only attach when computing distance tier.

def _attach_coords(listings: list[Listing]) -> dict[str, tuple]:
    """Map rss_id -> (lat, lon). Missing coords come back as (None, None).
    Returns an empty dict if there are no listings or the source DB
    doesn't exist (e.g. sample/dry-run mode)."""
    if not listings:
        return {}
    if not config.SOURCE_DB_PATH.exists():
        return {}
    src = db_mod.open_source(config.SOURCE_DB_PATH)
    try:
        rows = src.execute(
            "SELECT rss_id, latitude, longitude FROM seen_listings "
            "WHERE rss_id IN (%s)" % ",".join(["?"] * len(listings)),
            [ll.rss_id for ll in listings],
        ).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}
    finally:
        src.close()


# ---------- Stages ----------

def stage_triage(listings: list[Listing], client: Anthropic,
                 appr_db) -> list[tuple[Listing, "object"]]:
    """Returns the list of (listing, triage_result) for which triage
    succeeded.

    Before sending to the LLM, we semantic-prefilter to drop buyer posts,
    accessory-only listings, and excluded categories. Cheap and protects
    against the substring matcher missing 'WTB' / 'ISO' / 'e-scooter
    battery only' / 'leather office chair'."""
    prefiltered = []
    for ll in listings:
        if rules.is_buyer_post(ll.title, ll.body):
            log.debug("Drop %s (semantic buyer post): %r",
                      ll.rss_id, ll.title)
            continue
        if rules.is_excluded_category(ll.title):
            log.debug("Drop %s (semantic excluded category): %r",
                      ll.rss_id, ll.title)
            continue
        if rules.is_accessory_only(ll.title, ll.body):
            log.debug("Drop %s (semantic accessory-only): %r",
                      ll.rss_id, ll.title)
            continue
        prefiltered.append(ll)
    log.info("Semantic prefilter: %d → %d listings",
             len(listings), len(prefiltered))

    out = []
    for ll, res in triage_mod.triage_batch(prefiltered, client=client):
        if isinstance(res, Exception):
            continue
        db_mod.upsert_triage(appr_db, res)
        out.append((ll, res))
    return out


def stage_extract(passers: list[tuple[Listing, "object"]],
                  client: Anthropic, appr_db
                  ) -> list[tuple[Listing, ExtractionResult]]:
    out = []
    for ll, res in extractor.extract_batch(passers, client=client):
        if isinstance(res, Exception):
            continue
        db_mod.upsert_extraction(appr_db, res)
        out.append((ll, res))
    return out


def stage_comps(extractions: list[tuple[Listing, ExtractionResult]]
                ) -> dict[str, CompsResult]:
    queries = sorted({c.salvage_query for _, e in extractions
                      for c in e.components})
    log.info("Fetching %d unique comp queries (backend=%s)",
             len(queries), config.EBAY_BACKEND)
    return comps_mod.lookup_many(queries)


def stage_valuate(extractions: list[tuple[Listing, ExtractionResult]],
                  comp_index: dict[str, CompsResult],
                  client: Anthropic, appr_db) -> list[Appraisal]:
    appraisals: list[Appraisal] = []
    coords = _attach_coords([ll for ll, _ in extractions])
    for ll, ext in extractions:
        # Subset of comp_index for just this listing's queries
        per_listing = {c.salvage_query: comp_index[c.salvage_query]
                       for c in ext.components
                       if c.salvage_query in comp_index}
        try:
            appr = valuator.valuate(ll, ext, per_listing, client=client)
        except Exception as e:
            log.warning("Valuator failed for %s: %s", ll.rss_id, e)
            continue

        # Override recommendation with the user's deterministic rule.
        lat, lon = coords.get(ll.rss_id, (None, None))
        tier, dist = rules.classify_distance(lat, lon)
        # Tier multiplier shapes effective realized value for the rule.
        tier_mult = config.TIER_WEIGHTS.get(tier, 1.0)
        realized_for_rule = appr.salvage_realized * tier_mult
        category_key, really_good, sim = rules.category_of(
            ext.item_kind, [c.category for c in ext.components])
        if rules.is_excluded_category(ext.item_kind):
            decision, reason = "SKIP", "excluded category"
        else:
            decision, reason = rules.decide(
                appr.ask_price, realized_for_rule, tier, really_good)
        appr.recommendation = decision
        match_note = (f"semantic match {sim:.2f}" if 0 < sim < 1.0
                      else "exact match" if sim == 1.0 else "no match")
        appr.summary = (
            f"{appr.summary}\n[rule] tier={tier} "
            f"({f'{dist:.1f} km' if dist is not None else 'no coords'}), "
            f"category={category_key} "
            f"({'really-good' if really_good else 'standard'}, "
            f"{match_note}), "
            f"realized×tier=${realized_for_rule:.0f} → {decision}: {reason}"
        )
        db_mod.upsert_appraisal(appr_db, appr)
        appraisals.append(appr)
    return appraisals


# ---------- Top-level entry ----------

def run_pipeline(limit: Optional[int] = None,
                 only_unappraised: bool = True,
                 sample_path: Optional[Path] = None) -> dict:
    """Run all stages. If sample_path is set, load listings from JSON
    instead of cl_watcher's DB (lets you dry-run with no source data)."""
    client = _client()
    appr_db = db_mod.open_appraisal(config.APPRAISAL_DB_PATH)
    try:
        if sample_path is not None:
            listings = _load_sample(sample_path, limit=limit)
        else:
            src = db_mod.open_source(config.SOURCE_DB_PATH)
            try:
                listings = db_mod.fetch_listings(
                    src, limit=limit,
                    only_unappraised=only_unappraised,
                    appraisal_db=config.APPRAISAL_DB_PATH,
                )
            finally:
                src.close()

        log.info("Loaded %d listings", len(listings))
        if not listings:
            return {"loaded": 0}

        triaged = stage_triage(listings, client, appr_db)
        log.info("Triaged %d listings", len(triaged))

        passers = [(ll, t) for ll, t in triaged
                   if triage_mod.passes_filter(t, ll.ask_price or 0)]
        log.info("Passers for deep appraisal: %d", len(passers))
        if not passers:
            return {"loaded": len(listings), "triaged": len(triaged),
                    "passers": 0}

        extractions = stage_extract(passers, client, appr_db)
        log.info("Extracted %d listings", len(extractions))

        comp_index = stage_comps(extractions)

        appraisals = stage_valuate(extractions, comp_index,
                                   client, appr_db)
        log.info("Appraised %d listings", len(appraisals))

        recs = {"BUY": 0, "MAYBE": 0, "SKIP": 0}
        for a in appraisals:
            recs[a.recommendation] += 1
        return {
            "loaded": len(listings),
            "triaged": len(triaged),
            "passers": len(passers),
            "extracted": len(extractions),
            "appraised": len(appraisals),
            "recommendations": recs,
        }
    finally:
        appr_db.close()


def _load_sample(path: Path, limit: Optional[int] = None) -> list[Listing]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = [Listing(**row) for row in data]
    return out[:limit] if limit else out
