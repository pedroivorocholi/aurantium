"""Pure tile model for the Heatmap panel: data payloads in, sized tiles out.

No Qt widgets and no I/O. The panel feeds it ``screen:*`` rows, ``quote:*``
payloads and portfolio positions, and paints the :class:`Tile` list as a
treemap. ``weight`` is the tile's area share (market cap, position value, or a
stand-in); ``change_pct`` is in percent units and decides the colour.

Caps are compared in one currency. A view whose equities all list in one
currency is sized as reported; a mixed one (a Custom list of AAPL and
PETR4.SA, or the FTSE's few dollar listings) converts every cap into the
view's most common currency through the caller's ``convert`` -- the fewest
rates to wait on -- and a tile whose rate has not landed yet waits as a
``loading`` tile rather than being sized in the wrong unit.
"""

from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable, Optional

from .components import fmt
from .fx import listing_currency_guess, normalise
from .heatmap_universes import SECTORS, SHARE_CLASSES, SHORT_LABELS


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


def fold_share_classes(symbols) -> dict[str, str]:
    """``{tile symbol: symbol as requested}``, in order. An alias share class
    folds into its kept one only when that one is also requested: a lone
    PETR3 (ordinary shares, its own price) stays PETR3 rather than being shown
    and linked as PETR4."""
    asked = set(symbols)
    out: dict[str, str] = {}
    for s in symbols:
        kept = SHARE_CLASSES.get(s)
        out.setdefault(kept if kept in asked else s, s)
    return out


def _merge_share_classes(rows: list[dict], kept_wanted=()) -> dict[str, dict]:
    """``{symbol: row}`` with share-class aliases folded into the kept symbol:
    the larger cap used (Yahoo reports the WHOLE company's cap on every share
    class, so summing would double-count), the kept symbol's change and price
    used. An alias folds only when its kept symbol is among the rows or in
    ``kept_wanted``; otherwise it is its own tile."""
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
            if kept not in kept_wanted:
                continue
            by[kept] = {**a, "symbol": kept}
        else:
            caps = [c for c in (_num(k.get("market_cap")), _num(a.get("market_cap")))
                    if c is not None]
            k["market_cap"] = max(caps) if caps else None
        del by[alias]
    return by


#: ``convert(src, dst, cap)``: ``cap`` (in base-unit ``src``) in ``dst``, or
#: ``None`` while the rate is loading
CapConverter = Callable[[str, str, float], Optional[float]]


def _cap_currency(r: dict) -> Optional[str]:
    """The ISO code a row's cap is in. Yahoo quotes some listings in a minor
    unit (``GBp``) but reports their cap in the base one (pounds)."""
    return normalise(listing_currency_guess(str(r.get("symbol") or ""), r.get("currency")))[0]


def _comparable_caps(
    merged: dict[str, dict], syms, convert: Optional[CapConverter]
) -> tuple[dict[str, float], set[str]]:
    """``({symbol: cap in one currency}, symbols waiting)`` for the rows among
    ``syms`` with a positive cap. One currency, or no ``convert``: caps as
    reported. Waiting: an equity whose cap the provider is still looking up
    (``cap_pending``), one whose rate has not landed, and, in a mixed view,
    one of unknown currency -- shown, never sized in a guessed unit."""
    raw: dict[str, tuple[float, Optional[str]]] = {}
    waiting: set[str] = set()
    for s in syms:
        r = merged.get(s)
        cap = _num(r.get("market_cap")) if r else None
        if cap is not None and cap > 0:
            raw[s] = (cap, _cap_currency(r))
        elif r and r.get("cap_pending"):
            waiting.add(s)
    counts = Counter(cur for _cap, cur in raw.values() if cur)
    if len(counts) <= 1 or convert is None:
        if counts and convert is not None:  # one known currency, some unknown
            waiting |= {s for s, (_cap, cur) in raw.items() if cur is None}
        return {s: cap for s, (cap, cur) in raw.items() if s not in waiting}, waiting
    target = counts.most_common(1)[0][0]
    caps: dict[str, float] = {}
    for s, (cap, cur) in raw.items():
        if cur is None:
            waiting.add(s)
            continue
        v = cap if cur == target else convert(cur, target, cap)
        if v is None or v <= 0:
            waiting.add(s)
        else:
            caps[s] = v
    return caps, waiting


def _equity_tile(r: dict, weight: float) -> Tile:
    sym = r["symbol"]
    pct = _num(r.get("change_pct"))
    cap = _num(r.get("market_cap"))
    cur = _cap_currency(r)
    shown = fmt.compact(cap)
    if cap is not None and cur and cur != "USD":
        shown = f"{shown} {cur}"
    return Tile(
        sym, sym, weight, pct,
        _tooltip(r.get("name"), sym, r.get("price"), pct, "Market cap", shown),
    )


def _rate_tile(sym: str, weight: float) -> Tile:
    """An equity whose size is not known yet (a rate, a share count): drawn
    as loading."""
    return Tile(sym, sym, weight, None, f"{sym}\nLoading", loading=True)


def _short_labels(tiles: list[Tile]) -> list[Tile]:
    """Labels without the view's main exchange suffix (``PETR4``, ``0700``),
    which only eats a small tile's width. A symbol keeps its suffix when it
    is not the main one (DAX's ``AIR.PA``) or when the bare name would be
    ambiguous on this map (Euro Stoxx's ``SAN.PA`` Sanofi vs ``SAN.MC``
    Santander). The tile's symbol, tooltip and link stay the full one."""
    def split(sym: str) -> tuple[str, str]:
        stem, dot, suffix = sym.rpartition(".")
        return (stem, suffix) if dot and stem else (sym, "")

    parts = {t.symbol: split(t.symbol) for t in tiles}
    suffixes = Counter(sfx for _stem, sfx in parts.values() if sfx)
    if not suffixes:
        return tiles
    main = suffixes.most_common(1)[0][0]
    stems = Counter(stem for stem, _sfx in parts.values())
    out = []
    for t in tiles:
        stem, sfx = parts[t.symbol]
        if sfx == main and stems[stem] == 1 and t.label == t.symbol:
            t = replace(t, label=stem)
        out.append(t)
    return out


def tiles_from_screen(
    rows: list[dict],
    limit: int,
    convert: Optional[CapConverter] = None,
    short_labels: bool = False,
) -> list[Tile]:
    """The ``limit`` largest tiles of an index/sector screen. Rows without a
    market cap are dropped, except an equity whose cap is still being looked
    up (``cap_pending``), which waits; share classes are merged; caps in
    another currency than most are converted (see the module docstring).
    ``short_labels`` (index views) drops the main exchange suffix."""
    merged = _merge_share_classes(rows)
    caps, waiting = _comparable_caps(merged, merged, convert)
    tiles = [_equity_tile(merged[s], cap) for s, cap in caps.items()]
    if waiting:
        median = statistics.median(caps.values()) if caps else 1.0
        tiles += [_rate_tile(s, median) for s in merged if s in waiting]
    tiles.sort(key=lambda t: t.weight, reverse=True)
    tiles = tiles[: max(0, limit)]
    return _short_labels(tiles) if short_labels else tiles


def tiles_mixed(
    symbols: list[str],
    screen_rows: list[dict],
    quotes: dict[str, dict],
    convert: Optional[CapConverter] = None,
) -> list[Tile]:
    """One tile per requested symbol, in order. Equities the screener returned
    are sized by market cap (converted to one currency, see the module
    docstring); everything else (ETFs, FX, anything the screener missed) is
    coloured from its quote and sized at the median equity cap, or 1.0 each
    when there are no equities. A symbol with no data yet, or whose cap waits
    on a rate, is kept as a ``loading`` tile rather than dropped."""
    want = fold_share_classes(symbols)  # tile symbol -> symbol as requested
    merged = _merge_share_classes(screen_rows, kept_wanted=set(want))
    caps, waiting = _comparable_caps(merged, want, convert)
    median = statistics.median(caps.values()) if caps else 1.0
    out: list[Tile] = []
    for sym in want:
        if sym in caps:
            out.append(_equity_tile(merged[sym], caps[sym]))
            continue
        if sym in waiting:
            out.append(_rate_tile(sym, median))
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
            etf, SHORT_LABELS.get(key, label), float(cap) if sized else 1.0, pct,
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
