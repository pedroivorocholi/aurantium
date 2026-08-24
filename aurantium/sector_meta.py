"""Benchmark and sector-proxy tickers, for the Day Brief's EXCESS line.

The Day Brief answers "was it this company, or was it everything?" by
subtracting a benchmark's move from the stock's. That answer is only worth
printing if the benchmark is the right one, so both maps below fail closed:
an unrecognised sector or an unrecognised listing suffix yields ``None`` and
the comparison line is omitted rather than computed against the wrong index.
A silently wrong EXCESS number is worse than a missing one.

Sector strings are yfinance's own ``info["sector"]`` values (the eleven GICS
sectors as Yahoo spells them), surfaced by ``profile:SYM``
(providers/market.py). Sector proxies are the SPDR Select Sector ETFs, which
only make sense for US listings -- ``sector_proxy_for`` therefore returns None
for suffixed tickers even when the sector is known.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "SECTOR_ETF",
    "INDEX_BY_SUFFIX",
    "DEFAULT_INDEX",
    "benchmark_for",
    "sector_proxy_for",
    "suffix_of",
]

#: yfinance sector string -> SPDR Select Sector ETF (US listings only)
SECTOR_ETF: dict[str, str] = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}

#: Yahoo exchange suffix -> the local broad index to compare against.
#: Deliberately short: every entry here is one somebody can sanity-check.
INDEX_BY_SUFFIX: dict[str, str] = {
    "SA": "^BVSP",     # B3, Sao Paulo
    "L": "^FTSE",      # London
    "DE": "^GDAXI",    # Xetra
    "PA": "^FCHI",     # Euronext Paris
    "AS": "^AEX",      # Euronext Amsterdam
    "MI": "FTSEMIB.MI",  # Borsa Italiana
    "MC": "^IBEX",     # BME Madrid
    "SW": "^SSMI",     # SIX Swiss
    "ST": "^OMX",      # Nasdaq Stockholm
    "TO": "^GSPTSE",   # Toronto
    "HK": "^HSI",      # Hong Kong
    "T": "^N225",      # Tokyo
    "KS": "^KS11",     # Korea
    "AX": "^AXJO",     # ASX
    "NS": "^NSEI",     # NSE India
    "BO": "^BSESN",    # BSE India
    "MX": "^MXX",      # Mexico
    "JO": "^J203.JO",  # Johannesburg
}

#: what an unsuffixed (US-listed) ticker is measured against
DEFAULT_INDEX = "^GSPC"


def suffix_of(symbol: str) -> str:
    """``"PETR4.SA"`` -> ``"SA"``; ``"AAPL"`` -> ``""``.

    Index tickers (``^GSPC``) and currency pairs (``EURUSD=X``) have no
    meaningful listing suffix and return "" so callers can reject them."""
    if not isinstance(symbol, str):
        return ""
    sym = symbol.strip().upper()
    if not sym or sym.startswith("^") or "=" in sym:
        return ""
    _, _, tail = sym.rpartition(".")
    return tail if tail != sym else ""


def benchmark_for(symbol: str) -> Optional[str]:
    """The broad index this symbol should be measured against, or None when we
    have no honest answer. None means "omit the VS line", not "use SPY"."""
    if not isinstance(symbol, str) or not symbol.strip():
        return None
    sym = symbol.strip().upper()
    if sym.startswith("^") or "=" in sym or "-" in sym:
        return None  # an index, an FX pair or a crypto pair benchmarks nothing
    suffix = suffix_of(sym)
    if not suffix:
        return DEFAULT_INDEX
    return INDEX_BY_SUFFIX.get(suffix)


def sector_proxy_for(symbol: str, sector: Optional[str]) -> Optional[str]:
    """The sector ETF for a US listing with a known sector, else None.

    The SPDR sector ETFs track US large caps; pairing them with a Frankfurt or
    Tokyo listing would produce a number that looks meaningful and isn't."""
    if not sector or suffix_of(symbol):
        return None
    return SECTOR_ETF.get(sector.strip())
