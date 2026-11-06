"""Pure maths for the Macro / Rates panel: no Qt, no I/O.

Curve points are ``[[years, rate], ...]`` with rates in percent (4.21 = 4.21%),
so 1 bp = 0.01. Points are only ever *observed*: nothing here interpolates.
"""

from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Optional

TOLERANCE_Y = 0.05  # a point counts as "this tenor" within this many years…
TOLERANCE_REL = 0.10  # …or this share of the tenor, whichever is wider

# The relative band is for bond-by-bond curves (Tesouro), whose points sit at
# each bond's own maturity — 10.16Y, not 10Y. The nearest bond is still an
# observed rate, never an interpolated one, and across days it is the same bond
# (it ages ~0.08Y a month), so changes compare like with like.

SPREAD_REASON_MISSING = "This curve has no {tenor} point"

# Spread-state rules, worded once for the panel GUIDE.
SPREAD_STATE_RULES = (
    "Inverted: below zero. Steepening: up 10 bp or more over 20 business days. "
    "Flattening: down 10 bp or more. Otherwise normal."
)

_TREND_BP = 10.0
_TREND_DAYS = 20


def axis_x(years: float) -> float:
    """Curve x position: square-root spacing so the short end is not crushed."""
    return math.sqrt(years)


def tenor_label(years: float) -> str:
    """0.25 -> '3M', 0.5 -> '6M', 1 -> '1Y', 10 -> '10Y'."""
    if years < 1:
        return f"{round(years * 12)}M"
    return f"{round(years, 1):g}Y"  # a bond's 1.1554Y reads as 1.2Y


def rate_at(points, years: float) -> Optional[float]:
    """The observed rate nearest ``years`` within tolerance, else None."""
    tol = max(TOLERANCE_Y, TOLERANCE_REL * years)
    best = None
    for p in points or []:
        d = abs(p[0] - years)
        if d < tol and (best is None or d < best[0]):  # strict: 9Y is not 10Y
            best = (d, p[1])
    return None if best is None else float(best[1])


def bp_changes(now, then) -> list[tuple[float, float]]:
    """``(years, bp)`` for tenors observed in both curves, sorted by years."""
    out = []
    for p in now or []:
        before = rate_at(then, p[0])
        if before is not None:
            out.append((p[0], (p[1] - before) * 100.0))
    return sorted(out)


def spread_bp(points, short_y: float, long_y: float) -> Optional[float]:
    """``(long - short)`` in bp, or None if either leg is not observed."""
    s, lo = rate_at(points, short_y), rate_at(points, long_y)
    if s is None or lo is None:
        return None
    return (lo - s) * 100.0


def spread_reason(points, short_y: float, long_y: float) -> Optional[str]:
    """Why :func:`spread_bp` is None (names the first missing leg), else None."""
    for y in (short_y, long_y):
        if rate_at(points, y) is None:
            return SPREAD_REASON_MISSING.format(tenor=tenor_label(y))
    return None


def spread_state(series) -> str:
    """'inverted' | 'steepening' | 'flattening' | 'normal' for a bp series (oldest first)."""
    if not series:
        return "normal"
    last = series[-1]
    if last < 0:
        return "inverted"
    if len(series) <= _TREND_DAYS:
        return "normal"
    move = last - series[-1 - _TREND_DAYS]
    if move >= _TREND_BP:
        return "steepening"
    if move <= -_TREND_BP:
        return "flattening"
    return "normal"


def percentile(history, value: float, min_len: int = 26) -> Optional[float]:
    """Share of history values <= value, 0-100, 1 dp; None if history is short."""
    if len(history) < min_len:
        return None
    return round(100.0 * sum(1 for h in history if h <= value) / len(history), 1)


def days_since(iso: str, today: date) -> int:
    return (today - date.fromisoformat(iso)).days


def point_on_or_before(days, iso: str):
    """Points of the last history day on or before ``iso`` (ISO dates sort), else None."""
    best = None
    for d, pts in days or []:
        if d <= iso and (best is None or d > best[0]):
            best = (d, pts)
    return None if best is None else best[1]


def comparison_date(as_of_iso: str, chip: str) -> str:
    """Calendar date a '1W' / '1M' / '1Y' comparison chip looks back to."""
    d = date.fromisoformat(as_of_iso)
    if chip == "1W":
        return (d - timedelta(days=7)).isoformat()
    months = {"1M": 1, "1Y": 12}.get(chip)
    if months is None:
        raise ValueError(f"unknown comparison chip: {chip!r}")
    idx = d.year * 12 + (d.month - 1) - months
    y, mo = divmod(idx, 12)
    mo += 1
    return date(y, mo, min(d.day, calendar.monthrange(y, mo)[1])).isoformat()
