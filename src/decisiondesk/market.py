"""Local Market Intelligence (Milestone 5.3): where an owner's menu prices sit among comparable
nearby restaurants, from a small, manually verified, source-attributed public dataset.

Data: ``data/market/austin_menu_prices.csv``. These are PUBLIC menu prices read from each
restaurant's own website on the stated date. They are kept apart from the business's private
records and are never mixed into forecasts, demand or profit calculations. Restaurants whose
prices could not be verified are logged in ``austin_research_log.csv`` and left out.

Comparability rules (a single taco is not a plate of three):
  * each row says what one order contains (``units_per_order``, ``includes_sides``,
    ``with_protein``) and which of our products it may be compared with;
  * ``match_level``: exact (same serving), constructed (e.g. 3 x a single taco = a plate of
    three, no sides), similar (same dish, portion or tier may differ), unmatched (different
    serving, e.g. 2 tacos with rice and beans);
  * peer statistics use one representative row per restaurant and, by default, only exact and
    constructed matches; "similar" can be included on request; unmatched rows are shown but never
    counted;
  * chains are excluded from primary peers by default.

What this does NOT do: a competitor's price says nothing about how OUR customers react to a
price change (price elasticity stays an assumption in the Scenario Lab), and nothing here is
sales, demand, cost or margin data about any competitor. The module never recommends a price
because competitors charge it; it can only open the Scenario Lab to explore one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

MARKET_DIR = Path(__file__).resolve().parents[2] / "data" / "market"
PRICE_FILE = "austin_menu_prices.csv"
LOG_FILE = "austin_research_log.csv"
STALE_DAYS = 45
MIN_PEERS = 3
COUNTED_LEVELS = ("exact", "constructed")
REQUIRED = ("restaurant", "concept", "ownership", "item_category", "item_name", "price",
            "units_per_order", "matches_product", "match_level", "representative",
            "source_url", "source_type", "verified_date")
MARKET_NAME = "Austin, TX"

# What one unit of each of our products is, for matching.
OUR_SERVING: dict[str, dict] = {
    "tacos": {"desc": "plate of three street tacos, no sides", "units": 3},
    "burritos": {"desc": "burrito with rice and beans", "units": 1},
    "quesadillas": {"desc": "quesadilla with protein and salsa", "units": 1},
    "nachos": {"desc": "loaded nachos with protein", "units": 1},
    "drinks": {"desc": "agua fresca or soda", "units": 1},
}
NOT_A_RECOMMENDATION = (
    "Competitor menu prices show where your price sits locally. They do not show how your "
    "customers would react to a change, and they are not a reason on their own to change a price. "
    "Use the Decision Lab to test a change under stated price-sensitivity assumptions.")


@dataclass
class MarketData:
    prices: pd.DataFrame
    log: pd.DataFrame
    source_dir: Path


def load_market(directory: str | Path | None = None) -> MarketData:
    d = Path(directory or MARKET_DIR)
    prices = pd.read_csv(d / PRICE_FILE, dtype={"zip": str})
    missing = [c for c in REQUIRED if c not in prices.columns]
    if missing:
        raise ValueError(f"market price file is missing columns {missing}")
    if prices["price"].isna().any() or (prices["price"] <= 0).any():
        raise ValueError("every market price must be a positive number")
    for col in ("source_url", "verified_date"):
        if prices[col].isna().any() or (prices[col].astype(str).str.strip() == "").any():
            raise ValueError(f"every market price needs a {col}")
    prices["verified_date"] = pd.to_datetime(prices["verified_date"]).dt.date
    bad = set(prices["match_level"]) - {"exact", "constructed", "similar", "unmatched"}
    if bad:
        raise ValueError(f"unknown match levels {bad}")
    prices["matches_product"] = prices["matches_product"].fillna("")
    prices["representative"] = prices["representative"].astype(str).str.strip().str.lower().isin(["yes", "true"])
    log = pd.read_csv(d / LOG_FILE) if (d / LOG_FILE).exists() else pd.DataFrame()
    return MarketData(prices, log, d)


def _is_chain(ownership: str) -> bool:
    return "chain" in str(ownership).lower()


def comparable_price(row: pd.Series, product_id: str) -> float | None:
    """Price for OUR serving of ``product_id`` (e.g. 3 x a single taco); None if not comparable."""
    if row["matches_product"] != product_id or row["match_level"] == "unmatched":
        return None
    units = row["units_per_order"]
    if pd.isna(units) or float(units) <= 0:
        return None
    return round(float(row["price"]) * OUR_SERVING[product_id]["units"] / float(units), 2)


def compare(product_id: str, our_price: float, *, market: MarketData | None = None,
            concepts: list[str] | None = None, include_chains: bool = False,
            include_similar: bool = False, as_of: date | None = None,
            stale_days: int = STALE_DAYS) -> dict:
    """Where ``our_price`` sits among verified comparable local prices for one product."""
    if product_id not in OUR_SERVING:
        raise ValueError(f"unknown product {product_id!r}")
    m = market or load_market()
    as_of = as_of or date.today()
    levels = (*COUNTED_LEVELS, "similar") if include_similar else COUNTED_LEVELS
    rows, excluded = [], []
    for _, r in m.prices.iterrows():
        if r["matches_product"] != product_id and not (
                r["match_level"] == "unmatched" and str(r["item_category"]) in _categories(product_id)):
            continue
        age = (as_of - r["verified_date"]).days
        rec = {"restaurant": r["restaurant"], "concept": r["concept"], "ownership": r["ownership"],
               "item": r["item_name"], "listed_price": float(r["price"]),
               "serving": r.get("unit_desc", ""), "match_level": r["match_level"],
               "comparable_price": comparable_price(r, product_id), "source_url": r["source_url"],
               "source_type": r["source_type"], "verified_date": str(r["verified_date"]),
               "age_days": age, "stale": age > stale_days, "notes": r.get("notes", "") if
               isinstance(r.get("notes", ""), str) else ""}
        reason = None
        if r["match_level"] == "unmatched":
            reason = "different serving: " + (rec["notes"] or "not comparable")
        elif r["match_level"] not in levels:
            reason = "similar but not identical serving (excluded unless 'include similar' is on)"
        elif not r["representative"]:
            reason = "another item from the same restaurant is used for this product"
        elif _is_chain(r["ownership"]) and not include_chains:
            reason = "chain (excluded from primary peers)"
        elif concepts and r["concept"] not in concepts:
            reason = "concept filtered out"
        (excluded if reason else rows).append(rec | ({"excluded_because": reason} if reason else {}))
    prices = np.array([x["comparable_price"] for x in rows], float)
    n = len(rows)
    out = {"product_id": product_id, "market": MARKET_NAME, "our_serving": OUR_SERVING[product_id]["desc"],
           "our_price": round(float(our_price), 2), "as_of": str(as_of),
           "comparables": sorted(rows, key=lambda x: x["comparable_price"]), "excluded": excluded,
           "peer_count": n, "match_levels_counted": list(levels),
           "data_origin": "PUBLIC menu prices from official restaurant websites (manually verified)",
           "not_a_recommendation": NOT_A_RECOMMENDATION, "warnings": []}
    if n:
        lo, med, hi = float(prices.min()), float(np.median(prices)), float(prices.max())
        cheaper = int((prices < our_price - 1e-9).sum())
        dearer = int((prices > our_price + 1e-9).sum())
        out.update({"peer_min": round(lo, 2), "peer_median": round(med, 2), "peer_max": round(hi, 2),
                    "difference_vs_median": round(our_price - med, 2),
                    "difference_vs_median_pct": round((our_price - med) / med * 100, 1),
                    "peers_cheaper": cheaper, "peers_more_expensive": dearer,
                    "position": ("below the comparable range" if our_price < lo - 1e-9 else
                                 "above the comparable range" if our_price > hi + 1e-9 else
                                 "within the comparable range")})
    if n < MIN_PEERS:
        out["warnings"].append(f"Only {n} verified comparable(s): not enough for a local benchmark "
                               f"(at least {MIN_PEERS} needed).")
    elif n < 5:
        out["warnings"].append(f"Small sample: {n} verified comparables. Treat the range as indicative.")
    stale = [x["restaurant"] for x in rows if x["stale"]]
    if stale:
        out["warnings"].append(f"Prices older than {stale_days} days: {', '.join(stale)}. Re-verify before relying on them.")
    if len({x["concept"] for x in rows}) > 1:
        out["warnings"].append("Comparables mix restaurant types (" + ", ".join(sorted({x["concept"] for x in rows}))
                               + "); filter by type for a like-for-like view.")
    out["sufficient"] = n >= MIN_PEERS
    return out


def _categories(product_id: str) -> set[str]:
    return {"tacos": {"taco"}, "burritos": {"burrito"}, "quesadillas": {"quesadilla"},
            "nachos": {"nachos"}, "drinks": {"drink"}}[product_id]


def overview(our_prices: dict[str, float], *, market: MarketData | None = None,
             as_of: date | None = None, **kw) -> pd.DataFrame:
    """One row per product: our price vs the verified comparable range."""
    m = market or load_market()
    rows = []
    for pid, price in our_prices.items():
        c = compare(pid, price, market=m, as_of=as_of, **kw)
        rows.append({"product_id": pid, "our_price": c["our_price"], "peers": c["peer_count"],
                     "peer_min": c.get("peer_min"), "peer_median": c.get("peer_median"),
                     "peer_max": c.get("peer_max"), "vs_median_pct": c.get("difference_vs_median_pct"),
                     "position": c.get("position", "not enough comparables") if c["sufficient"]
                     else "not enough comparables"})
    return pd.DataFrame(rows)


def freshness(market: MarketData | None = None, as_of: date | None = None) -> dict:
    m = market or load_market()
    as_of = as_of or date.today()
    oldest = min(m.prices["verified_date"])
    newest = max(m.prices["verified_date"])
    return {"restaurants_with_verified_prices": int(m.prices["restaurant"].nunique()),
            "restaurants_researched": len(m.log) if len(m.log) else None,
            "oldest_verification": str(oldest), "newest_verification": str(newest),
            "oldest_age_days": (as_of - oldest).days, "stale": (as_of - oldest).days > STALE_DAYS,
            "next_reverification_due": str(oldest + pd.Timedelta(days=STALE_DAYS).to_pytimedelta()),
            "method": "Manual check of each restaurant's official online menu; no scraping of "
                      "ordering platforms. Re-verify every 45 days or before acting on a price."}
