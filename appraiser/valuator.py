"""Final stage: combine extraction + comps into an Appraisal.

Two paths:
  - LLM path (default): Claude Sonnet receives the comp medians for every
    component query and reasons about per-line unit values. Useful when
    comps are noisy or when condition adjustments matter.
  - Deterministic fallback: if the LLM call fails, we synthesize a simple
    sum-of-medians valuation so the listing isn't dropped from the output.

The recommendation in the Appraisal is overwritten by rules.decide() in
pipeline.py — the LLM's recommendation is just a hint.
"""
from __future__ import annotations
import logging
from typing import Iterable

from anthropic import Anthropic
from tenacity import (retry, stop_after_attempt, wait_exponential,
                      retry_if_exception_type)

import config
import prompts
from models import (Appraisal, ComponentValuation, CompsResult,
                    ExtractionResult, Listing)


log = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_valuate(client: Anthropic, listing: Listing,
                  extraction: ExtractionResult,
                  comps: dict[str, CompsResult]) -> dict:
    comps_payload = {q: r.model_dump() for q, r in comps.items()}
    resp = client.messages.create(
        model=config.VALUATE_MODEL,
        max_tokens=config.VALUATE_MAX_TOKENS,
        system=prompts.VALUATE_SYSTEM,
        tools=[prompts.VALUATE_TOOL],
        tool_choice={"type": "tool", "name": "record_appraisal"},
        messages=[{"role": "user",
                   "content": prompts.valuate_user_message(
                       listing, extraction, comps_payload)}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_appraisal":
            return dict(block.input)
    raise RuntimeError(
        f"No record_appraisal tool use in response for {listing.rss_id}"
    )


def valuate(listing: Listing, extraction: ExtractionResult,
            comps: dict[str, CompsResult],
            client: Anthropic | None = None) -> Appraisal:
    """Produce an Appraisal. The recommendation here is the LLM's hint
    only — pipeline.py overrides it with the deterministic rule."""
    client = client or Anthropic()
    ask = listing.ask_price or 0
    if listing.section == "free":
        ask = 0
    try:
        data = _call_valuate(client, listing, extraction, comps)
    except Exception as e:
        log.warning("LLM valuation failed for %s, falling back: %s",
                    listing.rss_id, e)
        return _fallback_valuation(listing, extraction, comps, ask)

    line_items = [ComponentValuation(**li) for li in data["line_items"]]
    salvage_low = float(data["salvage_low"])
    salvage_high = float(data["salvage_high"])
    midpoint = (salvage_low + salvage_high) / 2.0
    realized = midpoint * config.SALVAGE_REALIZATION_FACTOR
    return Appraisal(
        rss_id=listing.rss_id,
        ask_price=ask,
        salvage_low=salvage_low,
        salvage_high=salvage_high,
        salvage_realized=realized,
        ratio=realized / max(ask, 1),
        recommendation=data["recommendation"],
        confidence=data["confidence"],
        line_items=line_items,
        summary=data["summary"],
    )


def _fallback_valuation(listing: Listing, extraction: ExtractionResult,
                        comps: dict[str, CompsResult],
                        ask: int) -> Appraisal:
    """Deterministic sum-of-medians backup. No brand boost, no condition
    adjustment — just a defensible floor so a failed LLM call doesn't
    drop the listing entirely."""
    line_items: list[ComponentValuation] = []
    total_low = 0.0
    total_high = 0.0
    for c in extraction.components:
        cr = comps.get(c.salvage_query)
        med = (cr.median_price if cr and cr.median_price else 0.0)
        p25 = (cr.p25_price if cr and cr.p25_price else med * 0.7)
        p75 = (cr.p75_price if cr and cr.p75_price else med * 1.3)
        line_low = p25 * c.quantity
        line_high = p75 * c.quantity
        total_low += line_low
        total_high += line_high
        line_items.append(ComponentValuation(
            category=c.category, label=c.label, quantity=c.quantity,
            unit_low=p25, unit_high=p75,
            line_low=line_low, line_high=line_high,
            comps_used=cr.n_comps if cr else 0,
            confidence="low",
            rationale="fallback (LLM call failed) — comp p25/p75 only",
        ))
    midpoint = (total_low + total_high) / 2.0
    realized = midpoint * config.SALVAGE_REALIZATION_FACTOR
    ratio = realized / max(ask, 1)
    if ratio >= config.RECOMMEND_RATIO:
        rec = "BUY"
    elif ratio >= 1.5:
        rec = "MAYBE"
    else:
        rec = "SKIP"
    return Appraisal(
        rss_id=listing.rss_id, ask_price=ask,
        salvage_low=total_low, salvage_high=total_high,
        salvage_realized=realized, ratio=ratio,
        recommendation=rec, confidence="low",
        line_items=line_items,
        summary=f"deterministic fallback: ${total_low:.0f}–${total_high:.0f}",
    )
