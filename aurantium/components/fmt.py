"""Number formatting for table cells and stat labels.

There were 28 private ``_fmt_*`` functions across the panels, and nine of them
— every ``_fmt_num`` — were byte-for-byte identical. They existed because each
panel needed the same two decisions and there was nowhere shared to put them:
how to render a number, and what to show when there isn't one.

The second decision is the one that mattered. Those copies returned ``"-"``, an
ASCII hyphen, in 113 places. In a right-aligned monospaced numeric column —
which is most of this app — a hyphen sits exactly where a minus sign would, at
the same width, in the same ink. ``-`` and ``-1.2`` begin identically, so a
reader scanning a column of losses has to stop and work out whether a row is
negative or absent. The em dash cannot be mistaken for an operator, which is
precisely why typographers use it for elision.

Every function here returns :data:`aurantium.panel.NULL_GLYPH` for a missing,
unparseable or NaN value, so "no data" looks the same everywhere and never looks
like a number.
"""

from __future__ import annotations

from typing import Any

from ..panel import NULL_GLYPH

__all__ = [
    "NULL_GLYPH",
    "num",
    "pct",
    "signed_pct",
    "integer",
    "compact",
    "price",
]


def _f(value: Any) -> float | None:
    """``value`` as a float, or None when there is no usable number.

    NaN is folded in with None on purpose: pandas turns a missing cell into NaN,
    so a statement with a gap would otherwise render the literal text "nan" in
    the middle of a column of figures.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def num(value: Any, decimals: int = 2) -> str:
    """A plain number with thousands separators."""
    v = _f(value)
    return NULL_GLYPH if v is None else f"{v:,.{decimals}f}"


def pct(value: Any, decimals: int = 2) -> str:
    """A percentage, unsigned."""
    v = _f(value)
    return NULL_GLYPH if v is None else f"{v:.{decimals}f}%"


def signed_pct(value: Any, decimals: int = 2) -> str:
    """A percentage that always carries its sign.

    For anything the reader compares against zero — a surprise, a day move, a
    return. An unsigned ``0.42%`` beside a signed ``-1.10%`` reads as a column
    with inconsistent formatting rather than as a positive number.
    """
    v = _f(value)
    if v is None:
        return NULL_GLYPH
    return f"{v:+.{decimals}f}%"


def integer(value: Any) -> str:
    """A whole number with thousands separators (volume, open interest)."""
    v = _f(value)
    return NULL_GLYPH if v is None else f"{int(v):,}"


def compact(value: Any, decimals: int = 1) -> str:
    """A large number with a T/B/M/K suffix, keeping its sign.

    Market caps, revenues and volumes span too many orders of magnitude for a
    fixed format: rendered in full they blow out the column, and truncated they
    lose the magnitude that is the whole point.
    """
    v = _f(value)
    if v is None:
        return NULL_GLYPH
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{v / div:,.{decimals}f}{suffix}"
    return f"{v:,.0f}"


def price(value: Any, decimals: int = 2) -> str:
    """A price. Small values get more decimals.

    An FX pair quoted as a fraction of a dollar carries its information in the
    fourth and fifth decimal place; rounding it to two shows 0.00 for a rate
    that moved.
    """
    v = _f(value)
    if v is None:
        return NULL_GLYPH
    if abs(v) < 1:
        return f"{v:,.5f}".rstrip("0").rstrip(".") or "0"
    if abs(v) < 10:
        return f"{v:,.4f}".rstrip("0").rstrip(".") or "0"
    return f"{v:,.{decimals}f}"
