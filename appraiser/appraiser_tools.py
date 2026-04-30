"""Helper CLI for subagents.

Subagents have Bash but they shouldn't have to import Python modules
directly to do simple things like an eBay comp lookup or a distance
classification. This single script wraps everything an agent needs into
JSON-out commands.

Usage from inside a subagent:
  python appraiser/appraiser_tools.py comps "raspberry pi 4 8gb"
  python appraiser/appraiser_tools.py distance 49.245 -123.185
  python appraiser/appraiser_tools.py category "rpi 4 4gb"
  python appraiser/appraiser_tools.py decide 20 65 A true
"""
from __future__ import annotations
import argparse
import json
import sys

import comps as comps_mod
import config
import rules


def cmd_comps(args):
    r = comps_mod.lookup(args.query)
    print(r.model_dump_json())


def cmd_distance(args):
    tier, dist = rules.classify_distance(args.lat, args.lon)
    print(json.dumps({"tier": tier, "distance_km": dist,
                      "tier_multiplier": config.TIER_WEIGHTS.get(tier)}))


def cmd_category(args):
    key, really_good, sim = rules.category_of(args.text, [])
    print(json.dumps({
        "category_key": key,
        "really_good": really_good,
        "similarity": sim,
        "reason": (config.CATEGORY_TABLE.get(key, {}).get("reason")
                   if key else None),
    }))


def cmd_decide(args):
    tier = args.tier.upper()
    decision, reason = rules.decide(
        args.ask_price, args.salvage_realized, tier, args.really_good)
    print(json.dumps({"recommendation": decision, "reason": reason}))


def cmd_criteria(args):
    """Dump the user's criteria in a compact JSON form. Lets the agent
    read the full ruleset by piping this into context if needed."""
    out = {
        "category_table": config.CATEGORY_TABLE,
        "excluded_categories": config.EXCLUDED_CATEGORIES,
        "brand_boost": config.BRAND_BOOST,
        "condition_multipliers": config.CONDITION_MULTIPLIERS,
        "tier_weights": config.TIER_WEIGHTS,
        "decision": {
            "free": "BUY in-zone (D requires really_good)",
            "<= $20": f"BUY if salvage >= {config.PAID_LOW_RATIO}x ask",
            "$21-$30": f"BUY if salvage >= {config.PAID_HIGH_RATIO}x "
                       "AND really_good",
            "> $30": "SKIP",
        },
        "realization_factor": config.SALVAGE_REALIZATION_FACTOR,
    }
    print(json.dumps(out, indent=2))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("comps")
    s.add_argument("query")
    s.set_defaults(func=cmd_comps)

    s = sub.add_parser("distance")
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.set_defaults(func=cmd_distance)

    s = sub.add_parser("category")
    s.add_argument("text")
    s.set_defaults(func=cmd_category)

    s = sub.add_parser("decide")
    s.add_argument("ask_price", type=int)
    s.add_argument("salvage_realized", type=float)
    s.add_argument("tier", choices=["A", "B", "C", "D",
                                    "EXCLUDE", "UNKNOWN",
                                    "a", "b", "c", "d"])
    s.add_argument("really_good", type=lambda s: s.lower() == "true")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("criteria")
    s.set_defaults(func=cmd_criteria)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
