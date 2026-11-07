"""Yahoo-screener provider: the universes behind the Heatmap panel.

Topics
------
``screen:sector:<Sector>:<n>``
    The ``n`` largest US equities in a screener sector (``Technology``,
    ``Consumer Cyclical``, ...), by intraday market cap, descending.
``screen:index:<key>``
    The members of ``heatmap_universes.INDEXES[key]``. Like the symbols topic,
    except a US-shaped member missing from the 250-largest US universe is not
    quoted: it is smaller than all 250, so it can never make a top-40 map.
``screen:symbols:<SYM,SYM,...>``
    Exactly those symbols, any region (sorted, upper-cased, de-duplicated, so
    equal sets share one topic -- build it with :func:`symbols_topic`). A
    symbol Yahoo does not know has no row.
``sectorcap:all``
    ``{<Sector>: market_cap}`` for the 11 sectors, from ``yf.Sector(key).overview``.

``screen:*`` payload: ``{"rows": [{symbol, name, market_cap, price, change_pct,
currency}], "as_of": iso}``. ``change_pct`` is in PERCENT units (1.23 = +1.23%),
the same as quote payloads. Failures publish an error, never an empty ``rows``.

Live findings (yfinance 1.5.1, 2026-11-05)
------------------------------------------
* ``yf.screen(EquityQuery("and", [eq region us, eq sector <Sector>]),
  sortField="intradaymarketcap", sortAsc=False, size=N)`` works with the
  Title-case screener sector value. Result is ``{"quotes": [...], ...}``; each
  quote carries ``symbol``, ``shortName``/``longName``, ``marketCap``,
  ``regularMarketPrice``, ``regularMarketChangePercent`` (percent units) and
  ``currency``.
* ``EquityQuery("is-in", ["ticker", ...])`` does NOT work: yfinance raises
  ``Invalid field ... "ticker"`` and no symbol field exists. So the symbols
  topic fetches the 250 largest US caps (the screener's size cap) and filters
  locally, then prices everything else (non-US listings, small caps, ETFs) in
  one batch quote call per 250 symbols (see below).
* Yahoo's batch quote endpoint (``/v7/finance/quote?symbols=A,B,...``, through
  yfinance's crumb-carrying session) takes 250 symbols in one request and
  answers for every region, with ``marketCap`` in the listing's BASE unit:
  ``SHEL.L`` is quoted in pence (``GBp``) but its cap is in pounds. Unknown
  symbols are simply absent. ``longName`` reads better than ``shortName``
  abroad (``PETROBRAS   PN      N2`` vs ``Petróleo Brasileiro S.A.``).
* A few large listings come back with no ``marketCap`` at all (2026-11-06:
  ALV.DE, HLMA.L, FRES.L, PUB.PA...). Their cap is rebuilt from the price and
  the latest ``shares_out`` of Yahoo's fundamentals timeseries (the source of
  yfinance's ``fast_info.shares``, read directly so a failure raises instead
  of logging an ERROR and passing for "no data"). Counts are cached for a
  day, at most ``MAX_SHARE_LOOKUPS`` are fetched per refresh, and a 429 fails
  the topic. An equity still without a cap carries ``cap_pending``, so the
  map shows it waiting rather than dropping it.
* ``yf.Sector(key)`` wants the lower-case hyphenated Yahoo key
  (``technology``, ``consumer-cyclical``); the screener value ``Technology``
  fails. :func:`yahoo_sector_key` maps one to the other.
"""

from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import yfinance as yf

from ..datahub import DataHub, Provider
from ..fx import normalise as normalise_currency
from ..heatmap_universes import INDEXES
from ..heatmap_universes import SECTOR_KEYS as SECTORS  # the 11 screener sector values
from ._yf import (
    RATE_LIMIT_GATE,
    RATE_LIMIT_MESSAGE,
    YFRateLimitError,
    is_rate_limited,
    publish_fetch_error,
    with_retry,
)

#: the screener's hard page limit
MAX_SCREEN_SIZE = 250
#: seconds the 250-row US universe is reused across ``screen:symbols:*`` topics
UNIVERSE_TTL_S = 55.0
#: (monotonic ts, quotes); only ever set from a successful fetch
_UNIVERSE: Optional[tuple[float, list]] = None
_universe_lock = threading.Lock()

#: symbols per batch quote request (the endpoint answers 250 at once)
QUOTE_BATCH = 250
_QUOTE_URL = "https://query1.finance.yahoo.com/v7/finance/quote"
_QUOTE_FIELDS = ",".join((
    "symbol", "shortName", "longName", "marketCap", "regularMarketPrice",
    "regularMarketChangePercent", "currency", "quoteType",
))
_SHARES_URL = (
    "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{sym}"
)
#: share counts one fetch may look up (for listings Yahoo gives no cap); the
#: rest wait, as ``cap_pending`` rows, for the next refresh
MAX_SHARE_LOOKUPS = 15
#: seconds a share count is reused; Yahoo having none is asked again sooner
SHARES_TTL_S = 24 * 3600.0
SHARES_RETRY_S = 3600.0
#: symbol -> (monotonic ts, shares, or None for "Yahoo has none")
_SHARES: dict[str, tuple[float, Optional[float]]] = {}
#: symbol -> the lock one lookup of it holds, so two topics wanting the same
#: symbol make one request (``_shares_lock`` guards both dicts)
_share_locks: dict[str, threading.Lock] = {}
_shares_lock = threading.Lock()


def yahoo_sector_key(sector: str) -> str:
    """Screener value (``Consumer Cyclical``) to ``yf.Sector`` key
    (``consumer-cyclical``)."""
    return sector.strip().lower().replace(" ", "-")


def symbols_topic(symbols) -> str:
    """Canonical ``screen:symbols:`` topic: equal symbol sets share one topic."""
    uniq = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    return "screen:symbols:" + ",".join(uniq)


def index_topic(key: str) -> str:
    """The ``screen:index:`` topic for an ``INDEXES`` key."""
    return f"screen:index:{key}"


def _num(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def _row(q: dict) -> dict:
    return {
        "symbol": q.get("symbol"),
        "name": q.get("shortName") or q.get("longName") or q.get("symbol"),
        "market_cap": _num(q.get("marketCap")),
        "price": _num(q.get("regularMarketPrice")),
        "change_pct": _num(q.get("regularMarketChangePercent")),
        "currency": q.get("currency"),
    }


def _quote_row(q: dict) -> dict:
    """A batch-quote result as a ``screen:*`` row: the long name, tidied."""
    row = _row(q)
    name = q.get("longName") or q.get("shortName")
    if name:
        row["name"] = re.sub(r"\s+", " ", str(name)).strip()
    return row


def _is_us_shaped(sym: str) -> bool:
    """A plain US ticker (``AAPL``, ``BRK-B``): no exchange suffix, not an
    index, FX pair or future. Only these can be in the US universe."""
    return not any(c in sym for c in ".=^")


def _yahoo_quotes(symbols: list[str]) -> list[dict]:
    """One batch quote request: the raw quote dicts Yahoo returned."""
    # imported here: a yfinance internal, so if a release moves it only this
    # topic fails (with a note), not the import of every provider
    from yfinance.data import YfData

    res = YfData().get_raw_json(
        _QUOTE_URL, params={"symbols": ",".join(symbols), "fields": _QUOTE_FIELDS}
    )
    body = res.get("quoteResponse") if isinstance(res, dict) else None
    quotes = body.get("result") if isinstance(body, dict) else None
    if not isinstance(quotes, list):
        raise ValueError("quote endpoint returned no result list")
    return [q for q in quotes if isinstance(q, dict) and q.get("symbol")]


def _share_count(sym: str) -> Optional[float]:
    """The latest shares outstanding Yahoo has for ``sym``; ``None`` when it
    has none (funds, some listings). A failed request raises."""
    from yfinance.data import YfData

    now = int(time.time())
    res = YfData().get_raw_json(
        _SHARES_URL.format(sym=sym),
        params={"symbol": sym, "period1": now - 548 * 86400, "period2": now},
    )
    result = res["timeseries"]["result"]  # malformed raises: a failure, not "none"
    series = result[0] if result else {}
    points = [
        (t, n)
        for t, v in zip(series.get("timestamp") or [], series.get("shares_out") or [])
        if (n := _num(v)) and n > 0
    ]
    return max(points)[1] if points else None


class _LookupBudget:
    """How many share-count requests one fetch may still make."""

    def __init__(self, n: int) -> None:
        self.left = n


def _shares(sym: str, budget: _LookupBudget) -> Optional[float]:
    """:func:`_share_count`, cached. ``None`` when unknown: Yahoo has none,
    the request failed (not cached: retried next refresh), or this fetch's
    budget is spent. A rate limit raises, so the topic fails calmly."""
    with _shares_lock:
        lock = _share_locks.setdefault(sym, threading.Lock())
    with lock:  # one lookup per symbol at a time; a second caller reads the cache
        with _shares_lock:
            hit = _SHARES.get(sym)
        if hit and (time.monotonic() - hit[0]) < (SHARES_TTL_S if hit[1] else SHARES_RETRY_S):
            return hit[1]
        if budget.left <= 0:
            return None
        if RATE_LIMIT_GATE.blocked():
            raise YFRateLimitError()
        budget.left -= 1
        try:
            n = with_retry(lambda: _share_count(sym))
        except Exception as exc:
            if is_rate_limited(exc):
                raise
            return None
        with _shares_lock:
            _SHARES[sym] = (time.monotonic(), n)
        return n


def _fill_cap(row: dict, quote_type: Any, budget: _LookupBudget) -> dict:
    """An equity Yahoo gave no cap: price x shares, in the base unit. One
    still without a cap is marked ``cap_pending``: it waits on the map."""
    if row.get("market_cap") or quote_type != "EQUITY":
        return row
    shares = _shares(row["symbol"], budget) if row.get("price") else None
    if shares:
        _base, factor = normalise_currency(row.get("currency"))
        row["market_cap"] = row["price"] * factor * shares
    else:
        row["cap_pending"] = True
    return row


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ScreenProvider(Provider):
    """Serves ``screen:*`` and ``sectorcap:*``."""

    def topic_patterns(self) -> list[str]:
        return ["screen:*", "sectorcap:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            hub.run_async(lambda t=topic: self._fetch(t))

    def _fetch(self, topic: str) -> None:
        hub = DataHub.instance()
        if RATE_LIMIT_GATE.blocked():
            hub.publish_error(topic, RATE_LIMIT_MESSAGE)
            return
        parts = topic.split(":")
        try:
            if parts[0] == "screen" and len(parts) == 4 and parts[1] == "sector":
                hub.publish(topic, self._sector(parts[2], int(parts[3])))
            elif parts[0] == "screen" and len(parts) == 3 and parts[1] == "symbols":
                hub.publish(topic, self._symbols(parts[2].split(",")))
            elif parts[0] == "screen" and len(parts) == 3 and parts[1] == "index":
                if parts[2] not in INDEXES:
                    raise ValueError(f"unknown index: {parts[2]}")
                members = INDEXES[parts[2]][2]
                hub.publish(topic, self._symbols(members, quote_us_misses=False))
            elif topic == "sectorcap:all":
                hub.publish(topic, self._sectorcap())
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")
        except Exception as exc:
            publish_fetch_error(hub, topic, "screener fetch failed", exc)

    @staticmethod
    def _screen(query: Any, size: int) -> list[dict]:
        res = with_retry(
            lambda: yf.screen(
                query, sortField="intradaymarketcap", sortAsc=False, size=size
            )
        )
        quotes = res.get("quotes") if isinstance(res, dict) else None
        if not isinstance(quotes, list):
            raise ValueError("screener returned no quotes list")
        return quotes

    def _sector(self, sector: str, n: int) -> dict:
        n = max(1, min(n, MAX_SCREEN_SIZE))
        q = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("eq", ["sector", sector]),
            ],
        )
        quotes = self._screen(q, n)
        return {"rows": [_row(x) for x in quotes if x.get("symbol")], "as_of": _now_iso()}

    def _symbols(self, symbols, quote_us_misses: bool = True) -> dict:
        """US tickers from the shared US universe; everything else (and, with
        ``quote_us_misses``, any US ticker it lacks) from batch quotes. Any
        failed request fails the whole topic: a partial set would draw a
        wrong map."""
        want = sorted({s for s in symbols if s})
        rows: list[dict] = []
        if any(_is_us_shaped(s) for s in want):
            wanted = set(want)
            rows = [_row(x) for x in self._universe() if x.get("symbol") in wanted]
        found = {r["symbol"] for r in rows}
        rest = [s for s in want if s not in found
                and (quote_us_misses or not _is_us_shaped(s))]
        asked = set(rest)
        budget = _LookupBudget(MAX_SHARE_LOOKUPS)
        for i in range(0, len(rest), QUOTE_BATCH):
            chunk = rest[i:i + QUOTE_BATCH]
            quotes = with_retry(lambda c=chunk: _yahoo_quotes(c))
            rows += [_fill_cap(_quote_row(q), q.get("quoteType"), budget)
                     for q in quotes if q["symbol"] in asked]
        return {"rows": rows, "as_of": _now_iso()}

    def _universe(self) -> list[dict]:
        """The largest US caps, shared by every symbols topic for a minute.
        The lock is held across the fetch so concurrent topics make one call;
        a failure raises before anything is stored."""
        global _UNIVERSE
        with _universe_lock:
            hit = _UNIVERSE
            if hit and (time.monotonic() - hit[0]) < UNIVERSE_TTL_S:
                return hit[1]
            quotes = self._screen(
                yf.EquityQuery("eq", ["region", "us"]), MAX_SCREEN_SIZE
            )
            _UNIVERSE = (time.monotonic(), quotes)
            return quotes

    def _sectorcap(self) -> dict:
        """All 11 sectors or nothing: a partial dict would be cached for a
        day and skew the panel, so any miss raises and the hub retries."""
        out: dict[str, float] = {}
        for sector in SECTORS:
            ov = with_retry(lambda s=sector: yf.Sector(yahoo_sector_key(s)).overview)
            cap = _num((ov or {}).get("market_cap"))
            if cap is None:
                raise ValueError(f"no market cap for sector {sector}")
            out[sector] = cap
        return out
