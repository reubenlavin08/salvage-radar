"""eBay sold-listings lookup with three pluggable backends.

Backends
--------
'scrape' : fetch eBay's sold-listings HTML and parse. No account needed.
           This works today but is fragile — eBay can change their markup
           without notice. Use it as the default until you set up the API.

'api'    : eBay Browse API. Free dev account at developer.ebay.com,
           ~15 minutes to sign up:
             1. Register → Application Keys → request a Production keyset
             2. Put EBAY_APP_ID and EBAY_CERT_ID in your .env file
             3. Set APPRAISER_EBAY_BACKEND=api
           Note: Browse API does NOT expose sold/completed listings — only
           active listings. For genuine sold prices you'd need either the
           Marketplace Insights API (which requires a manual approval
           step) or the legacy Finding API (deprecated). The 'api' backend
           in this file currently uses Browse for active-listing medians,
           which is a *worse* signal than scrape's sold prices but more
           stable. Tradeoff to revisit.

'mock'   : canned data for unit tests / dry-run.

Cache
-----
Every query is keyed by a normalized hash and stored in a small SQLite
file with a TTL. Two reasons:
  - 2,000 listings × ~5 components = up to 10,000 eBay queries. Cache
    must be aggressive.
  - eBay does not love being scraped. Every cache hit is one less HTTP
    request.
"""
from __future__ import annotations
import hashlib
import json
import logging
import re
import sqlite3
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

import config
from models import Comp, CompsResult


log = logging.getLogger(__name__)

CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS comp_cache (
    query_hash TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    backend TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def _normalize_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.strip().lower())


def _query_hash(q: str, backend: str) -> str:
    return hashlib.sha1(
        f"{backend}|{_normalize_query(q)}".encode()
    ).hexdigest()


# ---------- Cache ----------

def _open_cache(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(CACHE_SCHEMA)
    return conn


def _cache_get(conn: sqlite3.Connection, q: str, backend: str
               ) -> Optional[CompsResult]:
    h = _query_hash(q, backend)
    row = conn.execute(
        "SELECT fetched_at, payload_json FROM comp_cache "
        "WHERE query_hash=?", (h,)
    ).fetchone()
    if not row:
        return None
    fetched_at, payload = row
    age = datetime.utcnow() - datetime.fromisoformat(fetched_at)
    if age > timedelta(days=config.EBAY_CACHE_TTL_DAYS):
        return None
    cr = CompsResult(**json.loads(payload))
    cr.cache_hit = True
    return cr


def _cache_put(conn: sqlite3.Connection, q: str, backend: str,
               result: CompsResult) -> None:
    h = _query_hash(q, backend)
    conn.execute(
        """INSERT INTO comp_cache (query_hash, query, backend, fetched_at,
                                   payload_json)
           VALUES (?,?,?,?,?)
           ON CONFLICT(query_hash) DO UPDATE SET
              query=excluded.query, backend=excluded.backend,
              fetched_at=excluded.fetched_at,
              payload_json=excluded.payload_json""",
        (h, q, backend, result.fetched_at, result.model_dump_json()),
    )
    conn.commit()


# ---------- Backend: scrape ----------

# Use ebay.ca: prices come back in CAD natively (no FX) and Canadian
# eBay tends to bot-block less aggressively than .com.
EBAY_HOMEPAGE = "https://www.ebay.ca/"
EBAY_SOLD_URL = (
    "https://www.ebay.ca/sch/i.html?"
    "_nkw={q}&_sacat=0&LH_Sold=1&LH_Complete=1&_ipg=60"
)

USD_TO_CAD = 1.37  # only used for non-CAD listings on ebay.ca

# Rotate among modern UAs to dilute fingerprinting.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


def _make_browser_headers(referer: str | None = None) -> dict:
    import random
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }
    if referer:
        h["Referer"] = referer
    return h


# Cached httpx client across calls so we keep cookies set by the homepage
# warm-up. None means "not initialized yet for this process".
_client_cache: httpx.Client | None = None
_warmed = False


def _get_client() -> httpx.Client:
    global _client_cache
    if _client_cache is None:
        _client_cache = httpx.Client(
            timeout=config.EBAY_TIMEOUT_S,
            follow_redirects=True,
            http2=False,
        )
    return _client_cache


def _warm_session(client: httpx.Client) -> None:
    """Visit the eBay.ca homepage once to pick up session cookies (the
    'dp1', 'nonsession', 'ebay' cookies). Without these, search pages
    are far more likely to come back as 403."""
    global _warmed
    if _warmed:
        return
    try:
        r = client.get(EBAY_HOMEPAGE, headers=_make_browser_headers())
        if r.status_code == 200:
            _warmed = True
            log.info("eBay session warmed (got %d cookies)",
                     len(client.cookies))
        else:
            log.warning("eBay warm-up returned %d", r.status_code)
    except Exception as e:
        log.warning("eBay warm-up failed: %s", e)


def _scrape_sold(q: str) -> CompsResult:
    url = EBAY_SOLD_URL.format(q=quote_plus(q))
    client = _get_client()
    _warm_session(client)
    headers = _make_browser_headers(referer=EBAY_HOMEPAGE)
    log.debug("eBay scrape: %s", url)

    html = None
    last_status = None
    for attempt in range(3):
        try:
            r = client.get(url, headers=headers)
            last_status = r.status_code
            text = r.text
            # eBay's bot block returns 200 with a "Pardon Our Interruption"
            # body — detect and treat as a soft block.
            if "Pardon Our Interruption" in text[:500]:
                last_status = "soft-block"
                log.warning("eBay soft-blocked query %r (attempt %d/3)",
                            q, attempt + 1)
                # Drop cookies + back off, the next attempt gets a fresh
                # warm-up.
                global _warmed
                client.cookies.clear()
                _warmed = False
                time.sleep(8.0 + attempt * 4)
                _warm_session(client)
                headers = _make_browser_headers(referer=EBAY_HOMEPAGE)
                continue
            r.raise_for_status()
            html = text
            break
        except httpx.HTTPStatusError as e:
            log.warning("eBay HTTP %d for %r (attempt %d/3)",
                        e.response.status_code, q, attempt + 1)
            time.sleep(5.0 + attempt * 3)
            headers = _make_browser_headers(referer=EBAY_HOMEPAGE)

    if html is None:
        log.warning("eBay gave up on %r (last_status=%s)", q, last_status)
        return CompsResult(
            query=q, median_price=None, p25_price=None, p75_price=None,
            n_comps=0, fetched_at=datetime.utcnow().isoformat(),
        )

    soup = BeautifulSoup(html, "lxml")
    # New eBay layout (2025+): <li class="s-card s-card--horizontal">
    # holding a div.su-card-container with title/price/subtitle inside.
    # Old layout (li.s-item) kept as fallback in case eBay rolls back.
    items = soup.select("li.s-card") or soup.select("li.s-item")
    comps: list[Comp] = []
    for li in items:
        title_el = (li.select_one(".s-card__title")
                    or li.select_one(".s-item__title"))
        price_el = (li.select_one(".s-card__price")
                    or li.select_one(".s-item__price"))
        link_el = li.select_one("a.s-card__link") or li.select_one("a")
        cond_el = (li.select_one(".s-card__subtitle")
                   or li.select_one(".SECONDARY_INFO"))
        if not title_el or not price_el:
            continue
        title = title_el.get_text(strip=True)
        if title.lower().startswith("shop on ebay"):
            continue
        price = _parse_price(price_el.get_text(strip=True))
        if price is None:
            continue
        comps.append(Comp(
            title=title,
            sold_price_cad=price,
            url=link_el["href"] if link_el and link_el.has_attr("href")
                else None,
            condition=cond_el.get_text(strip=True) if cond_el else None,
            source="ebay_scrape",
        ))
        if len(comps) >= config.EBAY_MAX_COMPS_PER_QUERY:
            break

    prices = [c.sold_price_cad for c in comps]
    return CompsResult(
        query=q,
        median_price=statistics.median(prices) if prices else None,
        p25_price=_quantile(prices, 0.25) if prices else None,
        p75_price=_quantile(prices, 0.75) if prices else None,
        n_comps=len(prices),
        fetched_at=datetime.utcnow().isoformat(),
        sample=comps[:5],
    )


def _parse_price(s: str) -> Optional[float]:
    """Parse strings like 'C $24.99', 'US $30.00', 'EUR 12.00 to EUR 15.00'.
    On ebay.ca, the dominant format is 'C $24.99' which we treat as CAD.
    Returns CAD float, or None if unparseable. Picks the low end of a range."""
    # ebay.ca usually emits 'C $24.99'; also handle 'CDN $...', 'CA$...'
    m = re.search(r"\bC[A-Z]{0,2}\s*\$\s*(\d+(?:[.,]\d+)?)", s)
    if m:
        return float(m.group(1).replace(",", ""))
    # 'US $24.99' explicit USD
    m = re.search(r"\bUS\s*\$\s*(\d+(?:[.,]\d+)?)", s)
    if m:
        return float(m.group(1).replace(",", "")) * USD_TO_CAD
    # Bare $24.99 — on ebay.ca this is CAD by default.
    m = re.search(r"\$\s*(\d+(?:[.,]\d+)?)", s)
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[hi] * frac


# ---------- Backend: api (eBay Browse — active listings) ----------

def _api_active(q: str) -> CompsResult:
    """eBay Browse API. Active listings only (not sold).

    Requires EBAY_APP_ID + EBAY_CERT_ID in env. We do client-credentials
    OAuth on the fly. This is more stable than scraping but the signal
    is weaker (asking prices, not sold prices). For real sold-price data
    you need the Marketplace Insights API which has manual approval.
    """
    import os, base64
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    if not app_id or not cert_id:
        raise RuntimeError(
            "EBAY_APP_ID and EBAY_CERT_ID must be set in env to use the "
            "'api' backend. Add them to %LOCALAPPDATA%\\cl_watcher\\.env."
        )
    token_b64 = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    with httpx.Client(timeout=config.EBAY_TIMEOUT_S) as client:
        tr = client.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {token_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        tr.raise_for_status()
        token = tr.json()["access_token"]
        sr = client.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA",
            },
            params={"q": q,
                    "limit": config.EBAY_MAX_COMPS_PER_QUERY,
                    "filter": "buyingOptions:{FIXED_PRICE}"},
        )
        sr.raise_for_status()
        items = sr.json().get("itemSummaries", [])
    comps: list[Comp] = []
    for it in items:
        price = it.get("price") or {}
        val = price.get("value")
        cur = price.get("currency", "USD")
        if val is None:
            continue
        cad = float(val) if cur == "CAD" else float(val) * USD_TO_CAD
        comps.append(Comp(
            title=it.get("title", ""),
            sold_price_cad=cad,
            url=it.get("itemWebUrl"),
            condition=it.get("condition"),
            source="ebay_api_active",
        ))
    prices = [c.sold_price_cad for c in comps]
    return CompsResult(
        query=q,
        median_price=statistics.median(prices) if prices else None,
        p25_price=_quantile(prices, 0.25) if prices else None,
        p75_price=_quantile(prices, 0.75) if prices else None,
        n_comps=len(prices),
        fetched_at=datetime.utcnow().isoformat(),
        sample=comps[:5],
    )


# ---------- Backend: mock ----------

_MOCK_DATA: dict[str, float] = {
    "rtx 3060": 220.0, "rtx 3070": 320.0, "rtx 4060": 280.0,
    "ryzen 5 5600x": 110.0, "intel i7-9700k": 90.0,
    "ddr4 16gb": 28.0, "psu 650w": 35.0, "ssd 500gb": 25.0,
    "nema 17 stepper": 8.0, "raspberry pi 4": 65.0,
}


def _mock(q: str) -> CompsResult:
    qn = _normalize_query(q)
    found = []
    for k, v in _MOCK_DATA.items():
        if k in qn:
            found.append(v)
    if not found:
        return CompsResult(
            query=q, median_price=None, p25_price=None, p75_price=None,
            n_comps=0, fetched_at=datetime.utcnow().isoformat(),
        )
    med = statistics.median(found)
    return CompsResult(
        query=q,
        median_price=med, p25_price=med * 0.8, p75_price=med * 1.2,
        n_comps=len(found) * 5,
        fetched_at=datetime.utcnow().isoformat(),
        sample=[Comp(title=f"mock {q}", sold_price_cad=med, source="mock")],
    )


# ---------- Public API ----------

def lookup(query: str) -> CompsResult:
    """Look up sold-comps for a query, using cache + configured backend."""
    backend = config.EBAY_BACKEND
    cache = _open_cache(config.COMPS_CACHE_PATH)
    try:
        hit = _cache_get(cache, query, backend)
        if hit is not None:
            return hit
        if backend == "scrape":
            time.sleep(config.EBAY_REQUEST_DELAY_S)
            result = _scrape_sold(query)
        elif backend == "api":
            result = _api_active(query)
        elif backend == "mock":
            result = _mock(query)
        else:
            raise ValueError(f"Unknown EBAY_BACKEND: {backend}")
        _cache_put(cache, query, backend, result)
        return result
    finally:
        cache.close()


def lookup_many(queries: list[str]) -> dict[str, CompsResult]:
    """Sequential by default — eBay throttles fast scrapers, so polite is
    safer than parallel. If you switch to the API backend, you can bump
    config.COMPS_CONCURRENCY and rewrite this with a pool."""
    out: dict[str, CompsResult] = {}
    for q in queries:
        try:
            out[q] = lookup(q)
        except Exception as e:
            log.warning("comps lookup failed for %r: %s", q, e)
            out[q] = CompsResult(
                query=q, median_price=None, p25_price=None, p75_price=None,
                n_comps=0, fetched_at=datetime.utcnow().isoformat(),
            )
    return out
