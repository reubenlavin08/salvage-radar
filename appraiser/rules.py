"""Deterministic rule layer that mirrors the user's hand-tuned criteria.

The LLM is good at extraction and at sanity-reasoning over comp prices,
but the BUY/MAYBE/SKIP decision must be predictable. We compute it here
so the user can audit the rule applied to any listing.

Category and exclusion matching are done semantically (see semantic.py)
so vocabulary drift like "RPi 4" → "raspberry pi 4" doesn't lose the
listing. We still keep substring matching as a fast first pass — it's
free and catches the obvious cases.

Functions:
  classify_distance(lat, lon) -> ("A"|"B"|"C"|"D"|"EXCLUDE", km)
  category_of(item_kind, components) -> (key, really_good, similarity)
  decide(ask_price, salvage_realized, tier, really_good)
       -> ("BUY"|"MAYBE"|"SKIP", reason)
"""
from __future__ import annotations
import logging
import math
from typing import Optional

import config


log = logging.getLogger(__name__)

# Track whether we've already warned about each semantic-backend failure
# so we don't spam the log with the same message thousands of times.
_warned: set[str] = set()


def _warn_once(key: str, msg: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    log.warning(msg)


# ---------- distance ----------

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def classify_distance(lat: Optional[float], lon: Optional[float]
                      ) -> tuple[str, Optional[float]]:
    if lat is None or lon is None:
        return ("UNKNOWN", None)
    d = haversine_km(lat, lon, config.DUNBAR_LAT, config.DUNBAR_LON)
    if (lon > config.EAST_BOUNDARY_LON
            and d > config.EAST_BOUNDARY_TOLERANCE_KM):
        return ("EXCLUDE", d)
    if lat > config.LIONS_GATE_LAT and lon < config.LIONS_GATE_LON_MAX:
        return ("EXCLUDE", d)
    if lat < config.FRASER_NORTH_ARM_LAT:
        return ("EXCLUDE", d)
    if d <= config.TIER_A_KM:
        return ("A", d)
    if d <= config.TIER_B_KM:
        return ("B", d)
    if d <= config.TIER_C_KM:
        return ("C", d)
    if d <= config.TIER_D_KM:
        return ("D", d)
    return ("EXCLUDE", d)


# ---------- category match ----------

def category_of(item_kind: str, components: list[str]
                ) -> tuple[Optional[str], bool, float]:
    """Match item_kind against CATEGORY_TABLE.
    Returns (matched_key, really_good_flag, similarity).

    Strategy:
      1. Substring match (fast, free, catches obvious cases).
      2. Component-cue fallback (VESC/Kinect/hub-motor → strong category
         signals even if the item_kind is generic).
      3. Semantic embedding match (catches "RPi 4", "lab DC source",
         "e-scooter", "FDM printer", etc.).
    """
    text = (item_kind or "").lower()

    # 1. Substring (longest first)
    for key in sorted(config.CATEGORY_TABLE.keys(), key=len, reverse=True):
        if key in text:
            return key, config.CATEGORY_TABLE[key]["really_good"], 1.0

    # 2. Component-cue fallback
    cs = ",".join(components)
    if "vesc" in cs:
        return "vesc", True, 1.0
    if "kinect_sensor" in cs:
        return "kinect", True, 1.0
    if "hub_motor" in cs:
        return "electric scooter", True, 1.0

    # 3. Semantic match — lazy-imported so callers that don't need it
    # don't pay the embedding-model startup cost.
    try:
        import semantic
        key, sim = semantic.category_of_semantic(text)
    except Exception as e:
        _warn_once("category", f"Semantic category match unavailable: {e}")
        return None, False, 0.0
    if key is None:
        return None, False, sim
    really_good = config.CATEGORY_TABLE.get(key, {}).get("really_good", False)
    return key, really_good, sim


# ---------- buy decision ----------

def decide(ask_price: int, salvage_realized: float, tier: str,
           really_good: bool) -> tuple[str, str]:
    """Return ('BUY'|'MAYBE'|'SKIP', short reason).

    Strict version of the user's rule:
      - free      → BUY if tier in {A,B,C} or (D and really_good)
      - ≤ $20     → BUY if salvage ≥ 2× ask
      - $21–$30   → BUY if salvage ≥ 3× ask AND really_good
      - > $30     → never SKIP
      - tier==EXCLUDE → SKIP regardless

    MAYBE is reserved for one band: paid ≤ $20 with 1.5–2.0× salvage,
    so the user can sanity-check borderline calls instead of dropping
    them entirely.
    """
    if tier == "EXCLUDE":
        return ("SKIP", "outside Vancouver buy-zone")
    if ask_price > config.PAID_HIGH_CEILING:
        return ("SKIP", f"ask > ${config.PAID_HIGH_CEILING} ceiling")

    if ask_price <= 0:
        if tier == "D" and not really_good:
            return ("SKIP", "free but Tier D and not really-good category")
        if tier == "UNKNOWN":
            return ("MAYBE", "free but tier unknown")
        return ("BUY", f"free, Tier {tier}")

    if ask_price <= config.PAID_LOW_CEILING:
        ratio = salvage_realized / max(ask_price, 1)
        if ratio >= config.PAID_LOW_RATIO:
            return ("BUY", f"≤ ${config.PAID_LOW_CEILING} and "
                           f"{ratio:.1f}× salvage")
        if ratio >= 1.5:
            return ("MAYBE", f"borderline {ratio:.1f}× — review")
        return ("SKIP", f"only {ratio:.1f}× salvage")

    # $21–$30 band
    ratio = salvage_realized / max(ask_price, 1)
    if not really_good:
        return ("SKIP",
                f"${ask_price} but not really-good category")
    if ratio >= config.PAID_HIGH_RATIO:
        return ("BUY", f"${ask_price} really-good item, "
                       f"{ratio:.1f}× salvage")
    return ("SKIP",
            f"really-good but only {ratio:.1f}× salvage on ${ask_price}")


# ---------- distance prefilter ----------

def is_too_far(latitude, longitude) -> bool:
    """True if a listing's coords are farther than
    config.MAX_PREFILTER_DISTANCE_KM from Dunbar. Listings without
    coordinates are kept (return False) — no signal to filter on."""
    if latitude is None or longitude is None:
        return False
    d = haversine_km(latitude, longitude,
                     config.DUNBAR_LAT, config.DUNBAR_LON)
    return d > config.MAX_PREFILTER_DISTANCE_KM


# ---------- coarse non-electronics drop ----------

def is_non_electronics(title: str, body: str = "") -> bool:
    """Coarse pre-filter: drop listings clearly not electronics-related.

    Matches `config.NON_ELECTRONICS_KEYWORDS` against the title (and the
    first 500 chars of body as a tiebreaker for the broader phrases).
    Returns False if any `config.KEEP_OVERRIDE_KEYWORDS` phrase is present
    anywhere in title+body — that's the mechanical-salvage escape hatch
    (scrapped CNC frame, gear set, linear rails, premium brands).

    Substring-only; cheap. Run before the LLM ever sees the listing.
    """
    title_n = (title or "").lower()
    body_n = (body or "").lower()[:500]
    combined = title_n + " " + body_n

    for keep in config.KEEP_OVERRIDE_KEYWORDS:
        if keep in combined:
            return False

    title_padded = " " + title_n + " "
    for kill in config.NON_ELECTRONICS_KEYWORDS:
        if kill in title_padded:
            return True
    return False


# ---------- excluded-category + accessory + buyer-post guards ----------

def is_excluded_category(item_kind: str) -> bool:
    """Substring + semantic. Returns True if listing is in an excluded
    category (bicycle, office chair, CRT, loose battery)."""
    text = (item_kind or "").lower()
    if any(ex in text for ex in config.EXCLUDED_CATEGORIES):
        return True
    try:
        import semantic
        key, _ = semantic.is_excluded_semantic(text)
        return key is not None
    except Exception as e:
        _warn_once("exclusion", f"Semantic exclusion match unavailable: {e}")
        return False


def is_buyer_post(title: str, body: str = "") -> bool:
    """Substring + semantic. WTB / ISO / 'looking for' / 'will pay cash'.

    We check the title with high priority and the first 200 chars of body
    as a secondary signal — buyer-post intent is almost always in the title."""
    title_n = (title or "").lower()
    for p in config.BUYER_TITLE_PATTERNS:
        if p in title_n:
            return True
    try:
        import semantic
        is_buyer, _ = semantic.is_buyer_post_semantic(title_n)
        if is_buyer:
            return True
        if body:
            is_buyer2, _ = semantic.is_buyer_post_semantic(
                (body or "").lower()[:200])
            if is_buyer2:
                return True
    except Exception as e:
        _warn_once("buyer", f"Semantic buyer-post match unavailable: {e}")
    return False


def is_accessory_only(title: str, body: str = "",
                      category_key: Optional[str] = None) -> bool:
    """Substring (per-category killers) + semantic ('accessory-only')."""
    title_n = (title or "").lower()
    if category_key:
        for kill in config.ACCESSORY_TITLE_KILLERS.get(category_key, []):
            if kill in title_n:
                return True
    try:
        import semantic
        is_acc, _ = semantic.is_accessory_only_semantic(title_n)
        return is_acc
    except Exception as e:
        _warn_once("accessory", f"Semantic accessory match unavailable: {e}")
        return False
