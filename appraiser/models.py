"""Typed records used throughout the appraiser pipeline.

Pydantic gives us free JSON validation when Claude returns structured
output, plus auto-generated JSON Schema for the tool-use definitions.
"""
from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ---------- Source listing (read from cl_watcher's DB) ----------

class Listing(BaseModel):
    rss_id: str
    title: str
    body: str = ""
    link: str = ""
    posted_at: Optional[str] = None
    ask_price: Optional[int] = None
    price_uncertain: bool = False
    neighborhood: Optional[str] = None
    section: Optional[str] = None  # 'free' | 'paid' | None
    attributes_json: Optional[str] = None


# ---------- Stage 1 output: triage (Option 1) ----------

Confidence = Literal["low", "medium", "high"]


class TriageResult(BaseModel):
    rss_id: str
    item_kind: str = Field(
        description="Short noun phrase, e.g. 'gaming desktop', 'lot of routers'."
    )
    coarse_salvage_low: int = Field(
        ge=0, description="Lower-bound parts-out value in CAD."
    )
    coarse_salvage_high: int = Field(
        ge=0, description="Upper-bound parts-out value in CAD."
    )
    confidence: Confidence
    reasoning: str = Field(description="One-to-two sentence rationale.")
    worth_deep_appraisal: bool = Field(
        description="True if this item passes the cheap filter and "
                    "should go through Option 2."
    )
    red_flags: list[str] = Field(
        default_factory=list,
        description="Buyer-post, scam patterns, missing details, etc."
    )


# ---------- Stage 2a output: structured extraction ----------

class Component(BaseModel):
    category: str = Field(
        description="One of config.COMPONENT_CATEGORIES."
    )
    label: str = Field(
        description="Short human label, e.g. 'RTX 3060 12GB' or 'unknown 24V "
                    "stepper, NEMA17-class'."
    )
    brand: Optional[str] = None
    model_number: Optional[str] = None
    quantity: int = Field(default=1, ge=1)
    condition: Optional[str] = Field(
        default=None,
        description="working | unknown | parts-only | broken | new"
    )
    confidence: Confidence
    salvage_query: str = Field(
        description="The eBay search string we should use for sold-comps. "
                    "Include 'used' or 'for parts' if condition warrants."
    )
    notes: str = ""


class ExtractionResult(BaseModel):
    rss_id: str
    item_kind: str
    overall_condition: str = "unknown"
    components: list[Component] = Field(default_factory=list)
    notes: str = ""
    extraction_confidence: Confidence


# ---------- Stage 2b: comps lookup ----------

class Comp(BaseModel):
    title: str
    sold_price_cad: float
    sold_at: Optional[str] = None
    url: Optional[str] = None
    condition: Optional[str] = None
    source: str = "ebay"


class CompsResult(BaseModel):
    query: str
    median_price: Optional[float]
    p25_price: Optional[float]
    p75_price: Optional[float]
    n_comps: int
    fetched_at: str
    sample: list[Comp] = Field(default_factory=list)
    cache_hit: bool = False


# ---------- Stage 2c: final valuation ----------

class ComponentValuation(BaseModel):
    category: str
    label: str
    quantity: int
    unit_low: float
    unit_high: float
    line_low: float
    line_high: float
    comps_used: int
    confidence: Confidence
    rationale: str = ""


class Appraisal(BaseModel):
    rss_id: str
    ask_price: int
    salvage_low: float
    salvage_high: float
    salvage_realized: float = Field(
        description="salvage midpoint × realization factor — what you "
                    "should actually expect to recover after shipping/effort."
    )
    ratio: float = Field(description="salvage_realized / max(ask_price, 1)")
    recommendation: Literal["BUY", "MAYBE", "SKIP"]
    confidence: Confidence
    line_items: list[ComponentValuation] = Field(default_factory=list)
    summary: str = ""
