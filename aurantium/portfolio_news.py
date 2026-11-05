"""Pure helpers for the News panel's Portfolio mode.

Qt-free and stdlib-only: the news provider calls these on a worker thread and
the panel uses :func:`build_book` / :func:`encode_book` to subscribe. The book
is the user's open positions as weights; it feeds three layers of headlines:
holdings (each held ticker), exposures (sectors the book is heavy in) and
macro (themes that move everything).
"""

from __future__ import annotations

import json
import math
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

#: Weight a macro theme scores with when its boosting sector is not in the book
#: (or weighs less): macro news still ranks, just below anything the book owns.
MACRO_DEFAULT_WEIGHT = 0.1


def macro_weight(theme: str, sweights: dict[str, float]) -> float:
    """Score weight for a macro theme: the weight of the sector that makes it
    matter (``MACRO_BOOST``) when the book holds it, never below the default."""
    return max(MACRO_DEFAULT_WEIGHT, sweights.get(MACRO_BOOST.get(theme, ""), 0.0))


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


def _pos_num(v) -> Optional[float]:
    """A finite number > 0, else None (NaN, inf, zero, negative = absent)."""
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return float(v) if ok and v > 0 else None


def build_book(
    open_positions: list[dict], prices: dict, sectors: dict, *, to_pref=None
) -> list[Holding]:
    """Open positions -> weights. Value = |qty| x (price or cost). A position
    with no usable price or cost is valued at the mean of the valued ones; if
    none is valued, all weigh the same. Zero/non-finite qty is dropped.

    ``to_pref(symbol, amount_local, day)`` converts a listing-currency amount
    into the preferred currency (``day`` is always None here). A position it
    cannot value yet (rate loading, currency unknown) is left out of the book
    until it can; ``None`` keeps the raw, unconverted behaviour."""
    values: dict[str, Optional[float]] = {}
    for p in open_positions:
        sym = p.get("symbol")
        qty = p.get("qty")
        if not sym or not isinstance(qty, (int, float)) or isinstance(qty, bool):
            continue
        if not math.isfinite(qty) or qty == 0:
            continue
        unit = _pos_num(prices.get(sym)) or _pos_num(p.get("cost"))
        if unit is None:
            values.setdefault(sym, None)
        else:
            amount = abs(qty) * unit
            if to_pref is not None:
                amount = to_pref(sym, amount, None)
                if amount is None:
                    continue
            values[sym] = (values.get(sym) or 0.0) + amount
    if not values:
        return []
    valued = [v for v in values.values() if v is not None]
    fill = sum(valued) / len(valued) if valued else 1.0
    full = {s: (fill if v is None else v) for s, v in values.items()}
    total = sum(full.values())
    book: list[Holding] = [
        {"symbol": s, "weight": round(v / total, 2), "sector": sectors.get(s)}
        for s, v in full.items()
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


SECTOR_ROW_CAP = 8
MACRO_ROW_CAP = 20


def cap_per_reason(rows: list[dict], book: list[Holding]) -> list[dict]:
    """Best-scoring rows within each reason, so one heavy holding cannot fill
    the feed: a holding keeps max(3, round(25 x weight)), a sector 8, macro 20
    in total. Returns the survivors, best score first."""
    weights = {h["symbol"]: h["weight"] for h in book}
    kept: dict[str, int] = {}
    out: list[dict] = []
    for row in sorted(rows, key=lambda r: -r["score"]):
        sym = row.get("symbol")
        if sym:
            key, cap = "h:" + sym, max(3, round(25 * weights.get(sym, 0.0)))
        elif row["reason"] == "Macro":
            key, cap = "macro", MACRO_ROW_CAP
        else:
            key, cap = "s:" + row["reason"], SECTOR_ROW_CAP
        if kept.get(key, 0) < cap:
            kept[key] = kept.get(key, 0) + 1
            out.append(row)
    return out
