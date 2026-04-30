"""Option 2 stage 1: structured component extraction with Claude.

Reads a listing + its triage result, returns an ExtractionResult with a
parts list and per-part eBay search queries.
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
from models import ExtractionResult, Listing, TriageResult


log = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def _call_extract(client: Anthropic, listing: Listing,
                  triage: TriageResult) -> ExtractionResult:
    resp = client.messages.create(
        model=config.EXTRACT_MODEL,
        max_tokens=config.EXTRACT_MAX_TOKENS,
        system=prompts.EXTRACT_SYSTEM,
        tools=[prompts.EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "record_extraction"},
        messages=[{
            "role": "user",
            "content": prompts.extract_user_message(listing, triage),
        }],
    )
    for block in resp.content:
        if block.type == "tool_use" and block.name == "record_extraction":
            data = dict(block.input)
            data["rss_id"] = listing.rss_id
            return ExtractionResult(**data)
    raise RuntimeError(
        f"No record_extraction tool use in response for {listing.rss_id}"
    )


def extract_batch(items: Iterable[tuple[Listing, TriageResult]],
                  client: Anthropic | None = None
                  ) -> Iterable[tuple[Listing, ExtractionResult | Exception]]:
    client = client or Anthropic()
    items = list(items)
    with ThreadPoolExecutor(max_workers=config.EXTRACT_CONCURRENCY) as pool:
        futures = {pool.submit(_call_extract, client, ll, tr): ll
                   for ll, tr in items}
        for fut in as_completed(futures):
            ll = futures[fut]
            try:
                yield ll, fut.result()
            except Exception as e:
                log.warning("Extraction failed for %s: %s", ll.rss_id, e)
                yield ll, e
