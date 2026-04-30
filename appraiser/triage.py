"""Option 1: cheap LLM triage pass.

Runs Claude Haiku once per listing with a forced-tool schema and stores
the coarse salvage range + a worth_deep_appraisal flag.

Concurrency uses a thread pool — Anthropic's SDK is synchronous and
threads are simpler than asyncio for this scale. The bottleneck is the
API rate limit, not CPU.
"""
from __future__ import annotations
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from anthropic import Anthropic
from tenacity import (retry, stop_after_attempt, wait_exponential,
                      retry_if_exception_type)

import config
import prompts
from models import Listing, TriageResult


log = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_triage(client: Anthropic, listing: Listing) -> TriageResult:
    resp = client.messages.create(
        model=config.TRIAGE_MODEL,
        max_tokens=config.TRIAGE_MAX_TOKENS,
        system=prompts.TRIAGE_SYSTEM,
        tools=[prompts.TRIAGE_TOOL],
        tool_choice={"type": "tool", "name": "record_triage"},
        messages=[{"role": "user",
                   "content": prompts.triage_user_message(listing)}],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_triage":
            data = dict(block.input)
            data["rss_id"] = listing.rss_id
            return TriageResult(**data)
    raise RuntimeError(
        f"No record_triage tool use in response for {listing.rss_id}: "
        f"{resp.content!r}"
    )


def triage_batch(listings: Iterable[Listing],
                 client: Anthropic | None = None
                 ) -> Iterable[tuple[Listing, TriageResult | Exception]]:
    """Run triage concurrently. Yields (listing, result-or-exception)."""
    client = client or Anthropic()
    listings = list(listings)
    with ThreadPoolExecutor(max_workers=config.TRIAGE_CONCURRENCY) as pool:
        futures = {pool.submit(_call_triage, client, ll): ll
                   for ll in listings}
        for fut in as_completed(futures):
            ll = futures[fut]
            try:
                yield ll, fut.result()
            except Exception as e:
                log.warning("Triage failed for %s: %s", ll.rss_id, e)
                yield ll, e


def passes_filter(t: TriageResult, ask_price: int) -> bool:
    """Should this listing graduate to Option 2?

    Same logic as triage's own worth_deep_appraisal flag, plus a
    quantitative ratio gate so that confidently-low items get cut even if
    the model said 'sure, take a look'.
    """
    if not t.worth_deep_appraisal:
        return False
    confidence_rank = {"low": 0, "medium": 1, "high": 2}
    if (confidence_rank[t.confidence]
            < confidence_rank[config.TRIAGE_CONFIDENCE_FLOOR]):
        return False
    midpoint = (t.coarse_salvage_low + t.coarse_salvage_high) / 2.0
    denom = max(ask_price, 1)
    return midpoint / denom >= config.TRIAGE_RATIO_FLOOR
