"""Shared metadata for the commodity markets aurantium treats as first-class:
the mapping between a commodity's continuous futures symbol (``GC=F``), its
CFTC Commitments-of-Traders market key (``gold``), and its futures-contract
root (``GC`` on ``CMX``) with the delivery-month cycle needed to spell
individual contract symbols (``GCZ25.CMX``).

Used by the Macro/Rates, Futures Curve, and Positioning History panels so a
symbol clicked in any panel resolves to the same market everywhere.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple, Optional

#: futures delivery month codes, January → December
MONTH_CODES = "FGHJKMNQUVXZ"


class CommodityMeta(NamedTuple):
    label: str        # plain-English name
    symbol: str       # continuous front-month symbol ("GC=F")
    cftc_market: Optional[str]  # key into the econ provider's CFTC market map
    root: str         # futures contract root ("GC")
    exchange: str     # Yahoo exchange suffix ("CMX" | "NYM" | "CBT" | "NYB")
    months: str       # active delivery-month codes, subset of MONTH_CODES
    category: str     # "Metals" | "Energy" | "Agriculture"


#: curated curve-capable commodities, grouped by category. Every root's
#: contract symbols and every COT market name were verified live against
#: Yahoo Finance / the CFTC Socrata endpoint before being listed here.
COMMODITIES: tuple[CommodityMeta, ...] = (
    # -- metals --
    CommodityMeta("Gold", "GC=F", "gold", "GC", "CMX", "GJMQVZ", "Metals"),
    CommodityMeta("Silver", "SI=F", "silver", "SI", "CMX", "FHKNUZ", "Metals"),
    CommodityMeta("Copper", "HG=F", "copper", "HG", "CMX", "HKNUZ", "Metals"),
    CommodityMeta("Platinum", "PL=F", "platinum", "PL", "NYM", "FJNV", "Metals"),
    CommodityMeta("Palladium", "PA=F", "palladium", "PA", "NYM", "HMUZ", "Metals"),
    # -- energy --
    CommodityMeta("Brent Crude", "BZ=F", "brent", "BZ", "NYM", MONTH_CODES, "Energy"),
    CommodityMeta("Henry Hub NatGas", "NG=F", "natgas", "NG", "NYM", MONTH_CODES, "Energy"),
    CommodityMeta("WTI Crude", "CL=F", "crude_oil", "CL", "NYM", MONTH_CODES, "Energy"),
    CommodityMeta("Gasoline (RBOB)", "RB=F", "gasoline", "RB", "NYM", MONTH_CODES, "Energy"),
    CommodityMeta("Heating Oil (ULSD)", "HO=F", "heating_oil", "HO", "NYM", MONTH_CODES, "Energy"),
    # -- agriculture --
    CommodityMeta("Wheat (SRW)", "ZW=F", "wheat", "ZW", "CBT", "HKNUZ", "Agriculture"),
    CommodityMeta("Corn", "ZC=F", "corn", "ZC", "CBT", "HKNUZ", "Agriculture"),
    CommodityMeta("Soybeans", "ZS=F", "soybeans", "ZS", "CBT", "FHKNQUX", "Agriculture"),
    CommodityMeta("Coffee", "KC=F", "coffee", "KC", "NYB", "HKNUZ", "Agriculture"),
    CommodityMeta("Sugar", "SB=F", "sugar", "SB", "NYB", "HKNV", "Agriculture"),
    CommodityMeta("Cocoa", "CC=F", "cocoa", "CC", "NYB", "HKNUZ", "Agriculture"),
    CommodityMeta("Cotton", "CT=F", "cotton", "CT", "NYB", "HKNVZ", "Agriculture"),
)

#: categories in display order (for grouped selectors)
CATEGORIES = ("Metals", "Energy", "Agriculture")

_BY_SYMBOL = {c.symbol: c for c in COMMODITIES}
_BY_MARKET = {c.cftc_market: c for c in COMMODITIES if c.cftc_market}
_BY_ROOT = {c.root: c for c in COMMODITIES}


def by_symbol(symbol: str) -> Optional[CommodityMeta]:
    """Resolve a symbol — continuous (``GC=F``) or an individual contract
    (``GCZ25.CMX``) — to its commodity, else None."""
    sym = (symbol or "").strip().upper()
    meta = _BY_SYMBOL.get(sym)
    if meta is not None:
        return meta
    # contract form: ROOT + month code + 2-digit year + "." + exchange
    body = sym.split(".", 1)[0]
    for root, meta in _BY_ROOT.items():
        rest = body[len(root):]
        if (
            body.startswith(root)
            and len(rest) == 3
            and rest[0] in MONTH_CODES
            and rest[1:].isdigit()
        ):
            return meta
    return None


def by_cftc_market(market: str) -> Optional[CommodityMeta]:
    return _BY_MARKET.get((market or "").strip().lower())


def contract_symbols(meta: CommodityMeta, count: int = 8, today: Optional[date] = None) -> list[tuple[str, str]]:
    """The next ``count`` contract symbols for a commodity, as
    ``(yahoo_symbol, "Mon YY" label)`` pairs, starting from next month (the
    expiring front month is skipped — it's mostly noise on a curve)."""
    today = today or date.today()
    out: list[tuple[str, str]] = []
    year, month = today.year, today.month
    while len(out) < count:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        code = MONTH_CODES[month - 1]
        if code not in meta.months:
            continue
        label = f"{date(year, month, 1):%b %y}"
        out.append((f"{meta.root}{code}{year % 100:02d}.{meta.exchange}", label))
    return out
