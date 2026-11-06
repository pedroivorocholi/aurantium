"""Pure tile model for the Heatmap panel: data payloads in, sized tiles out.

No Qt widgets and no I/O. The panel feeds it ``screen:*`` rows, ``quote:*``
payloads and portfolio positions, and paints the :class:`Tile` list as a
treemap. ``weight`` is the tile's area share (market cap, position value, or a
stand-in); ``change_pct`` is in percent units and decides the colour.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Callable, Optional

from .components import fmt
from .heatmap_universes import SECTORS, SHARE_CLASSES


@dataclass(frozen=True)
class Tile:
    symbol: str
    label: str
    weight: float
    change_pct: Optional[float]
    tooltip: str
    loading: bool = False
    sector_key: Optional[str] = None


def _num(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return None if x != x else float(x)


def _tooltip(name, symbol, price, change_pct, extra_label, extra_value) -> str:
    head = f"{name} ({symbol})" if name and name != symbol else str(symbol)
    return "\n".join(
        (head, f"Price {fmt.price(price)}", f"Change {fmt.signed_pct(change_pct)}",
         f"{extra_label} {extra_value}")
    )


def _merge_share_classes(rows: list[dict]) -> dict[str, dict]:
    """``{symbol: row}`` with share-class aliases folded into the kept symbol:
    the larger cap used (Yahoo reports the WHOLE company's cap on every share
    class, so summing would double-count), the kept symbol's change and price
    used. An alias whose kept symbol is absent is shown under the kept symbol."""
    by: dict[str, dict] = {}
    for r in rows:
        sym = r.get("symbol")
        if sym:
            by[sym] = dict(r)
    for alias, kept in SHARE_CLASSES.items():
        a = by.get(alias)
        if a is None:
            continue
        k = by.get(kept)
        if k is None:
            by[kept] = {**a, "symbol": kept}
        else:
            caps = [c for c in (_num(k.get("market_cap")), _num(a.get("market_cap")))
                    if c is not None]
            k["market_cap"] = max(caps) if caps else None
        del by[alias]
    return by


def _equity_tile(r: dict, weight: float) -> Tile:
    sym = r["symbol"]
    pct = _num(r.get("change_pct"))
    cap = _num(r.get("market_cap"))
    return Tile(
        sym, sym, weight, pct,
        _tooltip(r.get("name"), sym, r.get("price"), pct, "Market cap", fmt.compact(cap)),
    )


def tiles_from_screen(rows: list[dict], limit: int) -> list[Tile]:
    """The ``limit`` largest tiles of an index/sector screen. Rows without a
    market cap are dropped; share classes are merged."""
    merged = _merge_share_classes(rows)
    tiles = [
        _equity_tile(r, cap)
        for r in merged.values()
        if (cap := _num(r.get("market_cap"))) is not None and cap > 0
    ]
    tiles.sort(key=lambda t: t.weight, reverse=True)
    return tiles[: max(0, limit)]


def tiles_mixed(symbols: list[str], screen_rows: list[dict], quotes: dict[str, dict]) -> list[Tile]:
    """One tile per requested symbol, in order. Equities the screener returned
    are sized by market cap; everything else (ETFs, FX, anything the screener
    missed) is coloured from its quote and sized at the median equity cap, or
    1.0 each when there are no equities. A symbol with no data yet is kept as
    a ``loading`` tile rather than dropped."""
    merged = _merge_share_classes(screen_rows)
    want: dict[str, str] = {}  # kept symbol -> symbol as requested
    for s in symbols:
        want.setdefault(SHARE_CLASSES.get(s, s), s)
    caps = {
        s: c for s in want
        if s in merged and (c := _num(merged[s].get("market_cap"))) is not None and c > 0
    }
    median = statistics.median(caps.values()) if caps else 1.0
    out: list[Tile] = []
    for sym in want:
        if sym in caps:
            out.append(_equity_tile(merged[sym], caps[sym]))
            continue
        q = quotes.get(sym) or quotes.get(want[sym])
        row = merged.get(sym) or {}
        if q is None and not row:
            out.append(Tile(sym, sym, median, None, f"{sym}\nLoading", loading=True))
            continue
        src = q or row
        pct = _num(src.get("change_pct"))
        out.append(Tile(
            sym, sym, median, pct,
            _tooltip(src.get("name"), sym, src.get("price"), pct, "Size", "median cap"),
            loading=pct is None,
        ))
    return out


def tiles_sectors(quotes: dict[str, dict], caps: dict[str, float]) -> list[Tile]:
    """One tile per sector, the sector ETF's quote giving the colour. Equal
    weights until all 11 sector caps have arrived."""
    sized = bool(caps) and all((_num(caps.get(k)) or 0) > 0 for k, _l, _e in SECTORS)
    out: list[Tile] = []
    for key, label, etf in SECTORS:
        q = quotes.get(etf)
        pct = _num(q.get("change_pct")) if q else None
        cap = caps.get(key) if sized else None
        out.append(Tile(
            etf, label, float(cap) if sized else 1.0, pct,
            _tooltip(label, etf, q.get("price") if q else None, pct,
                     "Sector cap", fmt.compact(cap)),
            loading=pct is None, sector_key=key,
        ))
    return out


def tiles_portfolio(
    positions: list[dict],
    quotes: dict[str, dict],
    value: Callable[[str, float], Optional[float]],
) -> list[Tile]:
    """One tile per held symbol (open positions only, lots merged), sized by
    ``value(symbol, abs(qty) * price)`` -- the caller converts to the display
    currency. No quote or no conversion yet: a ``loading`` tile of weight 0."""
    totals: dict[str, Optional[float]] = {}
    for p in positions:
        sym = str(p.get("symbol") or "").strip().upper()
        qty = _num(p.get("qty"))
        if not sym or qty is None or p.get("sell_date"):
            continue
        q = quotes.get(sym)
        price = _num(q.get("price")) if q else None
        v = value(sym, abs(qty) * price) if price is not None else None
        if sym not in totals:
            totals[sym] = 0.0
        if v is None or totals[sym] is None:
            totals[sym] = None
        else:
            totals[sym] += v
    grand = sum(v for v in totals.values() if v) or 0.0
    out: list[Tile] = []
    for sym, v in totals.items():
        q = quotes.get(sym) or {}
        pct = _num(q.get("change_pct"))
        share = f"{v / grand * 100:.1f}%" if v is not None and grand else fmt.NULL_GLYPH
        out.append(Tile(
            sym, sym, v or 0.0, pct,
            _tooltip(q.get("name"), sym, q.get("price"), pct, "Weight", share),
            loading=v is None,
        ))
    return out
