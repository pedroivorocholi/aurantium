"""Curated symbol catalog behind the editor "Add…" pickers.

Every configurable panel's picker searches a slice of this catalog, so users
pick instruments by plain-English name ("10 year treasury", "cattle",
"dollar") instead of recalling Yahoo/FRED codes. Entries are data, not
behavior — the editor turns a picked entry into a row.

``kind``:
  - ``"quote"`` — a Yahoo Finance symbol (feeds ``quote:`` topics)
  - ``"fred"``  — a FRED series id (feeds ``fred:`` topics; needs a key)
  - ``"curve"`` — a point or spread read off a country's yield curve
    (``rates:curve:<CC>``): ``"<CC>:<years>"`` for a tenor, ``"<CC>:<short>-<long>"``
    for a spread. ``*`` as the country means "the panel's current country".
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

from ..commodities_meta import COMMODITIES
from ..macro_model import tenor_label


class CatalogEntry(NamedTuple):
    label: str      # plain-English display name
    code: str       # Yahoo symbol or FRED series id
    kind: str       # "quote" | "fred" | "curve"
    category: str   # picker group tag ("Rates", "FX", "Metals", …)
    keywords: str = ""  # extra search terms, lowercase


def search_catalog(
    entries: Sequence[CatalogEntry], text: str, limit: int = 30
) -> list[CatalogEntry]:
    """Rank ``entries`` for ``text``: label-prefix beats code-prefix beats
    substring anywhere in label/code/keywords. Empty text keeps the curated
    order. Ties keep curated order (sort is stable)."""
    needle = (text or "").strip().casefold()
    if not needle:
        return list(entries[:limit])
    scored: list[tuple[int, CatalogEntry]] = []
    for e in entries:
        label = e.label.casefold()
        code = e.code.casefold()
        haystack = f"{label} {code} {e.keywords}"
        if label.startswith(needle):
            score = 0
        elif code.startswith(needle):
            score = 1
        elif any(word.startswith(needle) for word in haystack.split()):
            score = 2
        elif needle in haystack:
            score = 3
        else:
            continue
        scored.append((score, e))
    scored.sort(key=lambda pair: pair[0])
    return [e for _score, e in scored[:limit]]


#: US Treasury yield indices — the quoted price IS the yield in percent
TENOR_ENTRIES: list[CatalogEntry] = [
    CatalogEntry("3M Treasury Yield", "^IRX", "quote", "Rates", "13 week bill short"),
    CatalogEntry("5Y Treasury Yield", "^FVX", "quote", "Rates", "5 year note"),
    CatalogEntry("10Y Treasury Yield", "^TNX", "quote", "Rates", "10 year note benchmark"),
    CatalogEntry("30Y Treasury Yield", "^TYX", "quote", "Rates", "30 year long bond"),
]

def _us_tenor(years: float, words: str) -> CatalogEntry:
    return CatalogEntry(f"US {tenor_label(years)}", f"US:{years:g}", "curve", "Rates",
                        f"treasury yield us government bond {words}")


#: Points and spreads read off the yield curves (``kind="curve"``), named the
#: way a desk says them. The label never shows a code: "Bund 10Y", not XM:10.
RATES_ENTRIES: list[CatalogEntry] = [
    _us_tenor(2, "2 year note short end"),
    _us_tenor(5, "5 year note belly"),
    _us_tenor(10, "10 year note benchmark"),
    _us_tenor(30, "30 year long bond"),
    CatalogEntry("2s10s spread", "US:2-10", "curve", "Rates",
                 "treasury yield curve spread slope inversion recession 2 10"),
    CatalogEntry("3m10y spread", "US:0.25-10", "curve", "Rates",
                 "treasury yield curve spread bill inversion recession 3 month 10"),
    CatalogEntry("5s30s spread", "US:5-30", "curve", "Rates",
                 "treasury yield curve spread long end 5 30"),
    CatalogEntry("Bund 10Y", "XM:10", "curve", "Rates",
                 "germany euro area government bond yield sovereign gilt"),
    CatalogEntry("JGB 10Y", "JP:10", "curve", "Rates",
                 "japan government bond yield sovereign gilt"),
    CatalogEntry("Canada 10Y", "CA:10", "curve", "Rates",
                 "canada government bond yield sovereign gilt"),
    CatalogEntry("Brazil 10Y", "BR:10", "curve", "Rates",
                 "brazil tesouro government bond yield sovereign gilt"),
]

INDEX_ENTRIES: list[CatalogEntry] = [
    CatalogEntry("S&P 500", "^GSPC", "quote", "Indices", "spx us equities"),
    CatalogEntry("Nasdaq 100", "^NDX", "quote", "Indices", "tech"),
    CatalogEntry("Dow Jones Industrial", "^DJI", "quote", "Indices", "djia"),
    CatalogEntry("Russell 2000", "^RUT", "quote", "Indices", "small caps"),
    CatalogEntry("VIX", "^VIX", "quote", "Indices", "volatility fear"),
    CatalogEntry("FTSE 100", "^FTSE", "quote", "Indices", "uk london"),
    CatalogEntry("DAX", "^GDAXI", "quote", "Indices", "germany frankfurt"),
    CatalogEntry("CAC 40", "^FCHI", "quote", "Indices", "france paris"),
    CatalogEntry("Euro Stoxx 50", "^STOXX50E", "quote", "Indices", "europe"),
    CatalogEntry("Nikkei 225", "^N225", "quote", "Indices", "japan tokyo"),
    CatalogEntry("Hang Seng", "^HSI", "quote", "Indices", "hong kong"),
    CatalogEntry("Shanghai Composite", "000001.SS", "quote", "Indices", "china"),
    CatalogEntry("Bovespa", "^BVSP", "quote", "Indices", "brazil ibovespa"),
]

FX_ENTRIES: list[CatalogEntry] = [
    CatalogEntry("Dollar Index", "DX-Y.NYB", "quote", "FX", "dxy usd greenback"),
    CatalogEntry("EUR/USD", "EURUSD=X", "quote", "FX", "euro"),
    CatalogEntry("USD/JPY", "USDJPY=X", "quote", "FX", "yen japan"),
    CatalogEntry("GBP/USD", "GBPUSD=X", "quote", "FX", "pound sterling cable"),
    CatalogEntry("USD/CHF", "USDCHF=X", "quote", "FX", "swiss franc"),
    CatalogEntry("AUD/USD", "AUDUSD=X", "quote", "FX", "aussie"),
    CatalogEntry("USD/CAD", "USDCAD=X", "quote", "FX", "loonie canada"),
    CatalogEntry("USD/BRL", "USDBRL=X", "quote", "FX", "real brazil"),
    CatalogEntry("USD/MXN", "USDMXN=X", "quote", "FX", "peso mexico"),
    CatalogEntry("USD/CNY", "USDCNY=X", "quote", "FX", "yuan china renminbi"),
    CatalogEntry("Bitcoin", "BTC-USD", "quote", "Crypto", "btc crypto"),
    CatalogEntry("Ethereum", "ETH-USD", "quote", "Crypto", "eth crypto"),
]

#: common FRED series by plain-English name (needs a free FRED key)
FRED_ENTRIES: list[CatalogEntry] = [
    CatalogEntry("10Y Real Yield (TIPS)", "DFII10", "fred", "FRED", "real rate inflation adjusted"),
    CatalogEntry("10Y Inflation Breakeven", "T10YIE", "fred", "FRED", "expectations"),
    CatalogEntry("10s–2s Yield Spread", "T10Y2Y", "fred", "FRED", "curve inversion recession"),
    CatalogEntry("2Y Treasury Yield", "DGS2", "fred", "FRED", "2 year short end"),
    CatalogEntry("30Y Mortgage Rate", "MORTGAGE30US", "fred", "FRED", "housing"),
    CatalogEntry("Unemployment Rate", "UNRATE", "fred", "FRED", "jobs labor"),
    CatalogEntry("CPI (All Items)", "CPIAUCSL", "fred", "FRED", "inflation prices"),
    CatalogEntry("Fed Funds Rate", "FEDFUNDS", "fred", "FRED", "fomc policy"),
    CatalogEntry("High-Yield Spread", "BAMLH0A0HYM2", "fred", "FRED", "credit junk oas"),
]

SECTOR_ETF_ENTRIES: list[CatalogEntry] = [
    CatalogEntry("Technology", "XLK", "quote", "Sectors", "tech software"),
    CatalogEntry("Financials", "XLF", "quote", "Sectors", "banks"),
    CatalogEntry("Energy", "XLE", "quote", "Sectors", "oil gas"),
    CatalogEntry("Health Care", "XLV", "quote", "Sectors", "pharma biotech"),
    CatalogEntry("Industrials", "XLI", "quote", "Sectors", "manufacturing"),
    CatalogEntry("Consumer Discretionary", "XLY", "quote", "Sectors", "retail"),
    CatalogEntry("Consumer Staples", "XLP", "quote", "Sectors", "defensive"),
    CatalogEntry("Utilities", "XLU", "quote", "Sectors", "power defensive"),
    CatalogEntry("Materials", "XLB", "quote", "Sectors", "chemicals mining"),
    CatalogEntry("Real Estate", "XLRE", "quote", "Sectors", "reits property"),
    CatalogEntry("Communication Services", "XLC", "quote", "Sectors", "media telecom"),
]


def commodity_entries() -> list[CatalogEntry]:
    """Every curated commodity as a picker entry (continuous front-month
    symbol), grouped by its ``commodities_meta`` category."""
    return [
        CatalogEntry(meta.label, meta.symbol, "quote", meta.category, "commodity futures")
        for meta in COMMODITIES
    ]
