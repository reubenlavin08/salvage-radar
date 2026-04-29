"""Craigslist Vancouver fetcher: HTML search + per-listing enrichment.

Search RSS endpoint is dead (403). The static HTML search page works for
discovery (title, link, search-card price/location), and each listing's
individual page provides authoritative price, neighborhood, body text, and
map lat/long for precise geo classification.
"""
import time
import re
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

import config

_session = requests.Session()
_session.headers.update({
    "User-Agent": config.USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "DNT": "1",
})


def build_urls():
    for term in config.SEARCH_TERMS:
        yield (term, "free",
               f"{config.CL_FREE_SEARCH}?{urlencode({'query': term})}")
        yield (term, "paid",
               f"{config.CL_GENERAL_SEARCH}?{urlencode({'query': term, 'max_price': config.MAX_PAID_PRICE})}")


def _get(url: str) -> str:
    try:
        time.sleep(config.REQUEST_DELAY_SEC)
        r = _session.get(url, timeout=20)
        if r.status_code != 200:
            return ""
        return r.text
    except Exception:
        return ""


def parse_search_page(html: str, term: str, section: str) -> list:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for li in soup.select("li.cl-static-search-result"):
        a = li.find("a")
        if not a or not a.get("href"):
            continue
        link = a["href"]
        title_el = li.select_one(".title")
        title = (title_el.get_text(strip=True)
                 if title_el else (li.get("title") or ""))
        out.append({
            "rss_id": link,
            "title": title,
            "link": link,
            "section": section,
            "term": term,
        })
    return out


def collect_search_results() -> list:
    """Return deduped list of {rss_id, title, link, section, term} from
    all search pages."""
    seen = {}
    for term, section, url in build_urls():
        for L in parse_search_page(_get(url), term, section):
            if L["rss_id"] not in seen:
                seen[L["rss_id"]] = L
    return list(seen.values())


def _parse_price_from_listing(soup: BeautifulSoup, body: str, title: str) -> dict:
    """Return {ask_price: int|None, price_unknown: bool}."""
    price = None
    el = soup.select_one(".price")
    if el:
        m = re.search(r"\d+", el.get_text())
        if m:
            price = int(m.group(0))

    text = (title + " " + body).lower()
    unknown_signal = any(p in text for p in config.PRICE_UNKNOWN_PATTERNS)
    # $0 or $1 placeholder + an offer/negotiable signal => price is unknown
    placeholder = price is not None and price <= 1
    price_unknown = placeholder and unknown_signal
    return {"ask_price": price, "price_unknown": price_unknown}


def _parse_attrgroups(soup: BeautifulSoup) -> dict:
    """Extract Craigslist's structured attributes (.attrgroup .attr).

    Returns dict {label_normalized: value}. Empty if the listing has none.
    Common labels: condition, make / manufacturer, model, dimensions, size.
    """
    attrs = {}
    for grp in soup.select(".attrgroup"):
        for attr in grp.select(".attr"):
            label_el = attr.select_one(".labl")
            value_el = attr.select_one(".valu")
            if not label_el or not value_el:
                continue
            label = label_el.get_text(strip=True).rstrip(":").lower().strip()
            value = value_el.get_text(" ", strip=True).lower()
            if label and value:
                attrs[label] = value
    return attrs


def enrich_listing(listing: dict) -> dict | None:
    """Fetch the listing page and extract authoritative fields.

    Returns dict with ask_price, price_unknown, neighborhood, latitude,
    longitude, body, posted_at, attributes (dict) — or None on fetch failure.
    """
    html = _get(listing["link"])
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # Lat/Lon from map div
    lat = lon = None
    map_el = soup.select_one("#map")
    if map_el:
        try:
            if map_el.get("data-latitude"):
                lat = float(map_el["data-latitude"])
            if map_el.get("data-longitude"):
                lon = float(map_el["data-longitude"])
        except (ValueError, TypeError):
            lat = lon = None

    # Neighborhood string (in title header parens)
    hood = ""
    title_text = soup.select_one(".postingtitletext")
    if title_text:
        # The bit after #titletextonly is "(neighborhood)"
        for span in title_text.find_all("span"):
            txt = span.get_text(strip=True)
            if txt.startswith("(") and txt.endswith(")"):
                hood = txt.strip("()").strip()
                break

    # Body
    body = ""
    body_el = soup.select_one("#postingbody")
    if body_el:
        body = body_el.get_text(" ", strip=True)
        body = re.sub(r"QR Code Link to This Post\s*", "", body)

    # Price
    price_info = _parse_price_from_listing(soup, body, listing["title"])

    # Posted time
    posted = ""
    time_el = soup.select_one("time.date.timeago")
    if time_el and time_el.get("datetime"):
        posted = time_el["datetime"]

    attributes = _parse_attrgroups(soup)

    return {
        "ask_price": price_info["ask_price"],
        "price_unknown": price_info["price_unknown"],
        "neighborhood": hood,
        "latitude": lat,
        "longitude": lon,
        "body": body,
        "posted_at": posted,
        "attributes": attributes,
    }
