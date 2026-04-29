"""Salvage estimation, geo classification (haversine + string fallback),
notify decision, final score."""
import math
import re
import config


# ---------- salvage ----------

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _detect_quantity(text: str) -> int:
    """Best-effort numeric quantity from listing text. Capped at 5."""
    m = re.search(r"\b(?:lot of|x|qty|quantity)\s*(\d+)", text)
    if m:
        return min(int(m.group(1)), 5)
    m = re.search(r"\b(\d+)\s*(?:units?|pcs|pieces|of them|x)\b", text)
    if m:
        return min(int(m.group(1)), 5)
    return 1


def find_primary_product(title_norm: str):
    """Longest salvage-table keyword that appears in the (normalized) title.
    Returns the keyword or None."""
    for kw in sorted(config.SALVAGE_TABLE.keys(), key=len, reverse=True):
        if kw in title_norm:
            return kw
    return None


CONDITION_MULTIPLIER = {
    "new": 1.25,
    "like new": 1.20,
    "excellent": 1.15,
    "good": 1.0,
    "fair": 0.85,
    "salvage": 0.5,
    "parts only": 0.5,
    "for parts": 0.5,
}


def _condition_multiplier(attributes: dict | None):
    if not attributes:
        return None
    cond = (attributes.get("condition") or "").strip().lower()
    if not cond:
        return None
    # Look up exact match first, then substring fall-through
    if cond in CONDITION_MULTIPLIER:
        return CONDITION_MULTIPLIER[cond]
    for k, v in CONDITION_MULTIPLIER.items():
        if k in cond:
            return v
    return None


def estimate_salvage(title: str, body: str, attributes: dict = None) -> int:
    """Title-primary salvage scorer.

    1. If title looks like a buyer/trader post → 0.
    2. Find primary product class from TITLE only. No title match → 0.
    3. Per-class negative body filters can zero it out (accessory-only).
    4. Apply brand boost (capped) + condition (attributes preferred,
       body-modifier fallback) + quantity (capped).
    """
    title_n = _normalize(title)
    body_n = _normalize(body)
    combined = title_n + " " + body_n

    for p in config.BUYER_TITLE_PATTERNS:
        if p in title_n:
            return 0

    primary = find_primary_product(title_n)
    if primary is None:
        return 0

    # Kill if title carries an accessory-token for this product class
    # (matches plurals: "case" matches both "case" and "cases")
    for kill in config.NEGATIVE_TITLE_KILLERS.get(primary, []):
        if re.search(rf"\b{re.escape(kill)}s?\b", title_n):
            return 0

    for neg in config.NEGATIVE_BODY_FILTERS.get(primary, []):
        if neg in combined:
            return 0

    low, high = config.SALVAGE_TABLE[primary]
    base = (low + high) / 2.0

    multiplier = 1.0
    for boost_kw, mult in config.BRAND_BOOST.items():
        if boost_kw in combined:
            multiplier *= mult
    # Make/manufacturer in attributes can also trigger brand boost
    if attributes:
        for label in ("make", "manufacturer", "brand"):
            v = (attributes.get(label) or "").lower()
            for boost_kw, mult in config.BRAND_BOOST.items():
                if boost_kw in v:
                    multiplier *= mult
    multiplier = min(multiplier, 2.5)

    # Condition: prefer Craigslist's structured attribute when present
    cond_mult = _condition_multiplier(attributes)
    if cond_mult is not None:
        multiplier *= cond_mult
    else:
        if any(p in body_n for p in config.HEAVY_NEGATIVE_MODIFIERS):
            multiplier *= 0.5
        elif any(p in body_n for p in config.LIGHT_NEGATIVE_MODIFIERS):
            multiplier *= 0.7
        elif any(p in body_n for p in config.POSITIVE_MODIFIERS):
            multiplier *= 1.15

    qty = _detect_quantity(combined)
    multiplier *= min(qty, 3.0)

    return int(base * multiplier)


# ---------- geo ----------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def classify_geo_by_coords(lat: float, lon: float) -> tuple:
    """Return (tier, distance_km) given lat/lon. Tier in {A,B,C,D,EXCLUDE}."""
    dist = haversine_km(lat, lon, config.DUNBAR_LAT, config.DUNBAR_LON)
    # Hard east-side cut: anything east of the Cambie meridian and not
    # very close to Dunbar is excluded.
    if lon > config.EAST_BOUNDARY_LON and dist > config.EAST_BOUNDARY_TOLERANCE_KM:
        return ("EXCLUDE", dist)
    # Hard exclude north of First Narrows / Lions Gate (North Van/West Van).
    # Lions Gate south anchor ~49.318°. West End / Coal Harbour top out
    # around 49.305°, so use 49.32° to be safe.
    if lat > 49.32 and lon < -123.05:
        return ("EXCLUDE", dist)
    # South of Fraser (Richmond/Delta) — Fraser north arm ~49.20°N
    if lat < 49.197:
        return ("EXCLUDE", dist)
    if dist <= config.TIER_A_KM:
        return ("A", dist)
    if dist <= config.TIER_B_KM:
        return ("B", dist)
    if dist <= config.TIER_C_KM:
        return ("C", dist)
    if dist <= config.TIER_D_KM:
        return ("D", dist)
    return ("EXCLUDE", dist)


def classify_geo_by_string(title: str, body: str, hood_field: str) -> str:
    text = (title + " " + body + " " + hood_field).lower()
    for kw in sorted(config.HARD_EXCLUDE, key=len, reverse=True):
        if kw in text:
            return "EXCLUDE"
    for kw in config.TIER_A:
        if kw in text:
            return "A"
    for kw in config.TIER_B:
        if kw in text:
            return "B"
    for kw in config.TIER_C:
        if kw in text:
            return "C"
    for kw in config.TIER_D:
        if kw in text:
            return "D"
    return "needs_review"


def classify_geo(latitude, longitude, title, body, hood_field):
    """Prefer coords; fall back to string. Returns (tier, distance_km, source).

    source ∈ {"coords", "string", "fallback"}.
    tier ∈ {A, B, C, D, EXCLUDE, needs_review}.
    """
    if latitude is not None and longitude is not None:
        tier, dist = classify_geo_by_coords(latitude, longitude)
        return (tier, dist, "coords")
    tier = classify_geo_by_string(title, body, hood_field)
    return (tier, None, "string")


# ---------- price ----------

def reconcile_price(ask_price, price_unknown: bool, section: str) -> tuple:
    """Return (effective_ask_price, is_free, treat_as_uncertain).

    If price_unknown=True and section=free => treat as $0 free.
    If price_unknown=True and section=paid => treat as $20 (worst case our
        ceiling), flagged uncertain so notify rule is stricter.
    """
    if section == "free":
        return (0, True, False)
    if ask_price is None:
        # Couldn't parse a number at all on a paid listing — treat as
        # uncertain at the $20 ceiling
        return (20, False, True)
    if price_unknown:
        return (max(20, ask_price), False, True)
    return (ask_price, ask_price == 0, False)


# ---------- decision ----------

def is_really_good(title: str, body: str) -> bool:
    text = (title + " " + body).lower()
    return any(kw in text for kw in config.REALLY_GOOD_KEYWORDS)


def should_notify(salvage: int, ask_price: int, tier: str, is_free: bool,
                  really_good: bool, price_uncertain: bool) -> bool:
    if tier == "EXCLUDE":
        return False
    # needs_review: only if free + clearly valuable
    if tier == "needs_review":
        return is_free and salvage >= 100

    if is_free:
        if tier == "D":
            return salvage >= 40
        return True

    # Uncertain price on paid listing — require stronger salvage signal
    if price_uncertain:
        if tier in ("A", "B", "C"):
            return salvage >= 60
        if tier == "D":
            return salvage >= 100
        return False

    if ask_price <= 20:
        threshold = 2.0 if tier in ("A", "B", "C") else 3.0
        return salvage >= threshold * ask_price
    if ask_price <= 30:
        if not really_good:
            return False
        threshold = 3.0 if tier in ("A", "B", "C") else 4.5
        return salvage >= threshold * ask_price
    return False


def compute_score(salvage: int, ask_price: int, tier: str) -> float:
    weight = config.TIER_WEIGHTS.get(tier, 0.95)
    return (salvage - ask_price) * weight
