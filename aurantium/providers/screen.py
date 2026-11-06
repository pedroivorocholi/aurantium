"""Yahoo-screener provider: the universes behind the Heatmap panel.

Topics
------
``screen:sector:<Sector>:<n>``
    The ``n`` largest US equities in a screener sector (``Technology``,
    ``Consumer Cyclical``, ...), by intraday market cap, descending.
``screen:symbols:<SYM,SYM,...>``
    Exactly those symbols (sorted, upper-cased, de-duplicated, so equal sets
    share one topic -- build it with :func:`symbols_topic`).
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
  locally; a requested symbol outside that set (small caps, non-US) is absent.
* ``yf.Sector(key)`` wants the lower-case hyphenated Yahoo key
  (``technology``, ``consumer-cyclical``); the screener value ``Technology``
  fails. :func:`yahoo_sector_key` maps one to the other.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

import yfinance as yf

from ..datahub import DataHub, Provider
from ..heatmap_universes import SECTOR_KEYS as SECTORS  # the 11 screener sector values
from ._yf import (
    RATE_LIMIT_GATE,
    RATE_LIMIT_MESSAGE,
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


def yahoo_sector_key(sector: str) -> str:
    """Screener value (``Consumer Cyclical``) to ``yf.Sector`` key
    (``consumer-cyclical``)."""
    return sector.strip().lower().replace(" ", "-")


def symbols_topic(symbols) -> str:
    """Canonical ``screen:symbols:`` topic: equal symbol sets share one topic."""
    uniq = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    return "screen:symbols:" + ",".join(uniq)


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
                hub.publish(topic, self._symbols(parts[2]))
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

    def _symbols(self, csv: str) -> dict:
        want = {s for s in csv.split(",") if s}
        quotes = self._universe()
        rows = [_row(x) for x in quotes if x.get("symbol") in want]
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
