"""Pure helpers for the News panel's Portfolio mode.

Qt-free and stdlib-only: the news provider calls these on a worker thread and
the panel uses :func:`build_book` / :func:`encode_book` to subscribe. The book
is the user's open positions as weights; it feeds three layers of headlines:
holdings (each held ticker), exposures (sectors the book is heavy in) and
macro (themes that move everything).
"""

from __future__ import annotations

import json
from typing import Optional, TypedDict


class Holding(TypedDict):
    symbol: str
    weight: float
    sector: Optional[str]


HOLDINGS_CAP = 10
SECTOR_MIN_WEIGHT = 0.10   # sectors lighter than this get no exposure query

# Yahoo Finance sector name -> short news query. RSS title search needs every
# word in the headline, so these stay at 1-3 common words.
SECTOR_QUERIES: dict[str, str] = {
    "Technology": "tech stocks",
    "Financial Services": "bank stocks",
    "Healthcare": "healthcare stocks",
    "Energy": "energy stocks",
    "Consumer Cyclical": "retail stocks",
    "Consumer Defensive": "consumer staples",
    "Industrials": "industrial stocks",
    "Basic Materials": "mining stocks",
    "Communication Services": "media stocks",
    "Utilities": "utilities stocks",
    "Real Estate": "REIT",
}

# (theme, query) for what moves a whole book.
MACRO_QUERIES: tuple[tuple[str, str], ...] = (
    ("rates", "Fed"),
    ("inflation", "inflation"),
    ("jobs", "jobs report"),
    ("oil", "oil prices"),
    ("dollar", "dollar"),
    ("recession", "recession"),
    ("geopolitics", "sanctions"),
)

# Macro theme -> the sector that makes it matter more.
MACRO_BOOST: dict[str, str] = {
    "rates": "Financial Services",
    "oil": "Energy",
    "inflation": "Consumer Defensive",
    "recession": "Consumer Cyclical",
    "jobs": "Industrials",
}

_SECTOR_SHORT = {
    "Technology": "Tech",
    "Financial Services": "Financials",
    "Healthcare": "Health",
    "Energy": "Energy",
    "Consumer Cyclical": "Cons. Cyc.",
    "Consumer Defensive": "Staples",
    "Industrials": "Industrials",
    "Basic Materials": "Materials",
    "Communication Services": "Comm.",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}

_LAYER_BASE = {"holding": 1.0, "sector": 0.7, "macro": 0.5}
_HALF_LIFE_H = 24.0
_AGE_FLOOR = 1e-6      # known ages never decay below this ...
_AGE_UNKNOWN = 1e-9    # ... so an undated item always ranks last


def build_book(open_positions: list[dict], prices: dict, sectors: dict) -> list[Holding]:
    """Open positions -> weights. Value = qty x (price or cost); a position
    with neither takes an equal share of what the valued ones leave over."""
    values: dict[str, Optional[float]] = {}
    for p in open_positions:
        sym = p.get("symbol")
        if not sym:
            continue
        unit = prices.get(sym) or p.get("cost")
        qty = p.get("qty") or 0
        v = qty * unit if unit else None
        if v is None:
            values.setdefault(sym, None)
        else:
            values[sym] = (values.get(sym) or 0.0) + v
    if not values:
        return []
    known = sum(v for v in values.values() if v)
    missing = [s for s, v in values.items() if not v]
    if not known:
        weights = {s: 1.0 / len(values) for s in values}
    elif missing:
        # Unvalued positions take 1/n each; valued ones split the rest.
        weights = {s: v / known for s, v in values.items() if v}
        share = 1.0 / len(values)
        for s in weights:
            weights[s] *= 1.0 - share * len(missing)
        for s in missing:
            weights[s] = share
    else:
        weights = {s: v / known for s, v in values.items()}
    book: list[Holding] = [
        {"symbol": s, "weight": round(w, 2), "sector": sectors.get(s)}
        for s, w in weights.items()
    ]
    book.sort(key=lambda h: -h["weight"])
    return book


def encode_book(book: list[Holding]) -> str:
    return json.dumps(book, separators=(",", ":"))


def decode_book(text: str) -> list[Holding]:
    try:
        raw = json.loads(text)
        return [
            {"symbol": str(h["symbol"]), "weight": float(h["weight"]),
             "sector": h.get("sector") or None}
            for h in raw
        ] if isinstance(raw, list) else []
    except (ValueError, TypeError, KeyError, AttributeError):
        return []


def sector_weights(book: list[Holding]) -> dict[str, float]:
    out: dict[str, float] = {}
    for h in book:
        if h["sector"]:
            out[h["sector"]] = round(out.get(h["sector"], 0.0) + h["weight"], 2)
    return out


def score_item(layer: str, weight: float, age_hours: Optional[float]) -> float:
    """Rank key: layer base x (1 + 2 x weight) x recency, higher is better.

    Recency halves every 24h; an item with no timestamp ranks below any dated
    one in the same layer and weight."""
    base = _LAYER_BASE.get(layer, 0.5)
    if age_hours is None:
        decay = _AGE_UNKNOWN
    else:
        decay = max(0.5 ** (max(age_hours, 0.0) / _HALF_LIFE_H), _AGE_FLOOR)
    return base * (1.0 + 2.0 * weight) * decay


def reason_for(layer: str, key: str, weight: float) -> str:
    """Why an item is on the Portfolio feed: ticker, 'Tech 40%' or 'Macro'."""
    if layer == "holding":
        return key
    if layer == "sector":
        return f"{_SECTOR_SHORT.get(key, key)} {round(weight * 100)}%"
    return "Macro"
