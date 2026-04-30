"""Prompt templates for the three Claude calls.

Kept separate so you can tune wording without touching pipeline code.

Design notes:
  - Every call uses Anthropic tool-use with a strict JSON schema. The tool
    is never actually executed — we just use it to force structured output.
  - The user's category table, brand-boost list, kill rules, and condition
    multipliers are baked into the system prompts so the LLM's reasoning
    aligns with the deterministic rules.py decision layer.
"""
from __future__ import annotations
from typing import Any
from models import Confidence  # noqa: F401  (re-export-friendly)
import config


def _category_summary() -> str:
    lines = []
    for k, v in config.CATEGORY_TABLE.items():
        rg = " (REALLY-GOOD)" if v["really_good"] else ""
        lines.append(f"  - {k}{rg}: {v['reason']}")
    return "\n".join(lines)


def _brand_summary() -> str:
    items = sorted(config.BRAND_BOOST.items(),
                   key=lambda kv: -kv[1])[:25]
    return ", ".join(f"{b} ×{m}" for b, m in items)


# ---------- Stage 1: triage ----------

TRIAGE_SYSTEM = f"""You are an electronics-salvage appraiser helping a UBC \
engineering student in Vancouver decide whether to drive across the city to \
look at a Craigslist listing for $0–$30. You evaluate listings for parts-\
out value: GPUs, motors, batteries, lab gear, etc.

The student's buy rules (he applies these later — you just produce a coarse \
estimate):
  - Free → buy if reasonably close
  - Paid ≤ $20 → buy if salvage ≥ 2× ask
  - Paid $21–$30 → buy ONLY if salvage ≥ 3× ask AND item is "really-good"
  - Paid > $30 → never buy

Categories the student cares about (with reasons):
{_category_summary()}

EXCLUDED categories (always set worth_deep_appraisal=false): \
{', '.join(config.EXCLUDED_CATEGORIES)}.

Buyer/trader posts ('looking for', 'wanted', 'WTB', 'ISO', 'for trade') have \
ZERO salvage value to the student. Accessory-only listings (e.g. "ink \
cartridge", "filament", "laptop bag", "drone props", "ebike battery only") \
also count as zero — the student wants the unit, not the consumable.

Be conservative. Vague posts get low confidence and zero value. Triage is a \
fast, coarse pass — within ~50% is fine; we'll do a deep appraisal on \
items that pass."""


TRIAGE_TOOL = {
    "name": "record_triage",
    "description": "Record the coarse parts-out estimate for one listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "item_kind": {
                "type": "string",
                "description": "Short noun phrase, e.g. 'gaming desktop', "
                               "'lot of routers', 'nothing of interest'.",
            },
            "coarse_salvage_low": {"type": "integer", "minimum": 0},
            "coarse_salvage_high": {"type": "integer", "minimum": 0},
            "confidence": {"type": "string",
                           "enum": ["low", "medium", "high"]},
            "reasoning": {"type": "string", "maxLength": 400},
            "worth_deep_appraisal": {
                "type": "boolean",
                "description": "True if this is real electronics in the "
                               "student's category list. False for buyer "
                               "posts, accessory-only listings, excluded "
                               "categories, or hopelessly vague posts.",
            },
            "red_flags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Short tags: buyer_post, accessory_only, "
                               "excluded_category, vague, scammy, etc.",
            },
        },
        "required": ["item_kind", "coarse_salvage_low",
                     "coarse_salvage_high", "confidence", "reasoning",
                     "worth_deep_appraisal", "red_flags"],
    },
}


def triage_user_message(listing) -> str:
    return f"""Listing to triage:

Title: {listing.title}
Asking price: {"FREE" if listing.section == "free" else f"${listing.ask_price}"}
Neighborhood: {listing.neighborhood or "unknown"}
Body:
\"\"\"
{(listing.body or "")[:3000]}
\"\"\"

Call record_triage with your assessment."""


# ---------- Stage 2a: structured extraction ----------

EXTRACT_SYSTEM = f"""You extract a parts list from a used-electronics \
listing. The user buys $0–$30 items in Vancouver and salvages components \
to either resell on eBay or use in robotics projects.

Identify EVERY salvageable component and produce an eBay search query for \
each. Components must be drawn from this fixed taxonomy:
{', '.join(config.COMPONENT_CATEGORIES)}

Category-aware hints — when a listing matches one of these categories, the \
typical salvageable components are:
{_category_summary()}

Premium brands within a category command much higher prices on eBay. The \
student's known-premium brand list (with rough multipliers): {_brand_summary()}

Rules:
  1. Only list components that are plausibly inside this item OR explicitly \
mentioned. Don't invent ('gaming PCs usually have a GPU' is not enough — \
only list a GPU if the listing supports it).
  2. salvage_query must be a string a human would actually type into eBay's \
sold-listings search. Include brand + model when known. If condition is \
parts-only, include 'for parts'. Avoid noise words.
  3. Set quantity > 1 only if the listing explicitly says so (lot of, x4). \
Cap at 5.
  4. Use 'other' sparingly — only when nothing in the taxonomy fits.
  5. Confidence: 'high' = listing names the part directly. 'medium' = \
strongly implied by item type. 'low' = guess based on item class only."""


EXTRACT_TOOL = {
    "name": "record_extraction",
    "description": "Record the structured parts list for a listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "item_kind": {"type": "string"},
            "overall_condition": {
                "type": "string",
                "enum": ["working", "unknown", "parts-only",
                         "broken", "new", "mixed"],
            },
            "components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": config.COMPONENT_CATEGORIES,
                        },
                        "label": {"type": "string"},
                        "brand": {"type": ["string", "null"]},
                        "model_number": {"type": ["string", "null"]},
                        "quantity": {"type": "integer",
                                     "minimum": 1, "maximum": 20},
                        "condition": {
                            "type": ["string", "null"],
                            "enum": ["working", "unknown", "parts-only",
                                     "broken", "new", None],
                        },
                        "confidence": {"type": "string",
                                       "enum": ["low", "medium", "high"]},
                        "salvage_query": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["category", "label", "quantity",
                                 "confidence", "salvage_query"],
                },
            },
            "notes": {"type": "string"},
            "extraction_confidence": {"type": "string",
                                      "enum": ["low", "medium", "high"]},
        },
        "required": ["item_kind", "overall_condition", "components",
                     "extraction_confidence"],
    },
}


def extract_user_message(listing, triage) -> str:
    return f"""Triage flagged this as worth a deep appraisal.

Triage said: {triage.item_kind} — coarse value ${triage.coarse_salvage_low}-\
${triage.coarse_salvage_high}.

Title: {listing.title}
Asking: {"FREE" if listing.section == "free" else f"${listing.ask_price}"}
Body:
\"\"\"
{(listing.body or "")[:6000]}
\"\"\"

Call record_extraction with the parts list."""


# ---------- Stage 2c: final valuation ----------

VALUATE_SYSTEM = f"""You produce the final salvage valuation. You receive \
(a) the structured component list and (b) eBay sold-comp medians per query.

Rules:
  - Use the comp medians as the centerline for each component's unit value.
  - If comps are noisy or sparse (n<3), widen the range and lower confidence.
  - If condition is parts-only / broken, multiply the line by 0.5 (mirrors \
the user's condition table).
  - If condition is like-new / new, multiply by 1.20 / 1.25.
  - For premium brands within a category, you may apply up to ×2.0 on top \
of comp price IF comps don't already reflect the brand. Cap brand boost at \
×2.0 total.
  - Don't include the realization factor — that's applied downstream.

The user applies a realization factor of {config.SALVAGE_REALIZATION_FACTOR} \
externally and a tier-distance multiplier on top. Your output is the raw \
sum of unfiltered component values.

Recommendation column is a hint only (the deterministic rule layer is \
authoritative downstream). Use:
  BUY   = midpoint > {config.RECOMMEND_RATIO}× ask AND confidence ≥ medium
  MAYBE = midpoint > 1.5× ask
  SKIP  = otherwise

Premium brand reference (rough multipliers, only useful if comps don't \
reflect the brand): {_brand_summary()}"""


VALUATE_TOOL = {
    "name": "record_appraisal",
    "description": "Record the final per-component appraisal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "salvage_low": {"type": "number", "minimum": 0},
            "salvage_high": {"type": "number", "minimum": 0},
            "confidence": {"type": "string",
                           "enum": ["low", "medium", "high"]},
            "recommendation": {"type": "string",
                               "enum": ["BUY", "MAYBE", "SKIP"]},
            "summary": {"type": "string", "maxLength": 600},
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "label": {"type": "string"},
                        "quantity": {"type": "integer", "minimum": 1},
                        "unit_low": {"type": "number"},
                        "unit_high": {"type": "number"},
                        "line_low": {"type": "number"},
                        "line_high": {"type": "number"},
                        "comps_used": {"type": "integer", "minimum": 0},
                        "confidence": {"type": "string",
                                       "enum": ["low", "medium", "high"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["category", "label", "quantity",
                                 "unit_low", "unit_high",
                                 "line_low", "line_high",
                                 "comps_used", "confidence"],
                },
            },
        },
        "required": ["salvage_low", "salvage_high", "confidence",
                     "recommendation", "summary", "line_items"],
    },
}


def valuate_user_message(listing, extraction, comps_by_query: dict[str, Any]
                         ) -> str:
    """`comps_by_query` maps a salvage_query string to a CompsResult dict."""
    lines = [
        f"Listing: {listing.title}",
        f"Ask: {'FREE' if listing.section == 'free' else f'${listing.ask_price}'}",
        f"Item kind: {extraction.item_kind}",
        f"Overall condition: {extraction.overall_condition}",
        "",
        "Components and eBay sold-comps:",
    ]
    for c in extraction.components:
        comps = comps_by_query.get(c.salvage_query, {})
        n = comps.get("n_comps", 0)
        med = comps.get("median_price")
        p25 = comps.get("p25_price")
        p75 = comps.get("p75_price")
        comp_str = (f"  comps n={n}, median=${med}, p25=${p25}, p75=${p75}"
                    if n else "  comps: none found")
        lines.append(
            f"- [{c.category}] {c.label} (qty={c.quantity}, "
            f"cond={c.condition or 'unknown'}, conf={c.confidence})\n"
            f"  query: \"{c.salvage_query}\"\n{comp_str}"
        )
    lines.append("")
    lines.append("Call record_appraisal with the final valuation.")
    return "\n".join(lines)
