"""Day Brief provider: what a single trading day looked like, and what landed
on or around it.

Serves two topics:

``daystat:SYM:YYYY-MM-DD``
    The anchor day's OHLCV plus the context that explains it -- volume against
    its own 30-day average, the move of a broad benchmark and a sector proxy
    over the same session, and the EXCESS (stock minus benchmark) that answers
    "was it this company, or was it everything?".

``dayev:SYM:YYYY-MM-DD..YYYY-MM-DD``
    Dated corporate events inside a window: earnings with the EPS surprise,
    ex-dividends, splits, and analyst rating changes.

Both are keyed entirely by their topic string, so DataHub's SQLite cache
(cache.py) does the right thing for free. Long TTLs are unusually safe here:
**a past trading day is immutable.** Once a brief has been fetched, revisiting
that date is instant and works offline.

Fetching is direct yfinance, matching every other provider -- the Provider
contract (datahub.py:34) is publish-only, providers do not consume each
other's topics. Benchmark and sector histories are therefore memoised in
process (``_HIST_MEMO``), because the same index window is wanted by every
symbol the user inspects that session, and refetching it per lookup is the one
way this provider could plausibly trip a 429.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional

import yfinance as yf

from ..datahub import DataHub, Provider
from ..sector_meta import benchmark_for, sector_proxy_for
from ._yf import (
    RATE_LIMIT_GATE,
    RATE_LIMIT_MESSAGE,
    publish_fetch_error,
    with_retry,
)

#: extra history pulled behind the anchor so a 30-day average volume exists
LOOKBACK_DAYS = 75
#: how far past the anchor to fetch, so a snap has somewhere to land
SNAP_LIMIT_DAYS = 5
#: yfinance's terse Action codes, spelled out. A reiteration and an upgrade
#: must never look alike in a list a user is scanning for a cause.
ACTION_WORDS = {
    "up": "upgrade",
    "down": "downgrade",
    "main": "maintained",
    "init": "initiated",
    "reit": "reiterated",
}

#: in-process memo for benchmark/sector series: {(sym, start, end): (ts, rows)}
_HIST_MEMO: dict[tuple, tuple[float, list]] = {}
_HIST_MEMO_TTL = 1800.0
_memo_lock = threading.Lock()


def _as_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN


def _as_int(x: Any) -> Optional[int]:
    f = _as_float(x)
    return None if f is None else int(f)


def _iso(value: Any) -> str:
    """Anything date-ish to 'YYYY-MM-DD', or '' when it is not."""
    if isinstance(value, str):
        return value[:10]
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    fn = getattr(value, "date", None)  # pandas Timestamp
    if callable(fn):
        try:
            return fn().isoformat()
        except Exception:
            return ""
    return ""


def pct_change(prev_close: Optional[float], close: Optional[float]) -> Optional[float]:
    """Session return in percent. Pure; unit-tested without Qt."""
    if prev_close is None or close is None or prev_close == 0:
        return None
    return (close - prev_close) / prev_close * 100.0


def excess(stock_pct: Optional[float], bench_pct: Optional[float]) -> Optional[float]:
    """Stock move minus benchmark move -- the Day Brief's headline number.
    None when either side is missing, so the panel omits the line rather than
    printing a comparison it cannot stand behind."""
    if stock_pct is None or bench_pct is None:
        return None
    return stock_pct - bench_pct


def verdict(excess_pct: Optional[float], stock_pct: Optional[float]) -> str:
    """Plain-language reading of the EXCESS number -- the one line that says
    what actually happened. Thresholds are deliberately coarse: this is a
    sentence, not a statistic."""
    if excess_pct is None or stock_pct is None:
        return ""
    if abs(stock_pct) < 0.75:
        return "a quiet session"
    share = abs(excess_pct) / abs(stock_pct)
    if share >= 0.7:
        return "stock-specific, not the market"
    if share <= 0.3:
        return "moved with the market"
    return "partly the market, partly the stock"


def rows_from_frame(df: Any) -> list[dict]:
    """yfinance history frame to [{date, o, h, l, c, v}], oldest first.
    Isolated so the maths above can be exercised against plain dict fixtures
    with no network and no pandas ceremony in the tests."""
    out: list[dict] = []
    if df is None or getattr(df, "empty", True):
        return out
    for idx, row in df.iterrows():
        iso = _iso(idx)
        if not iso:
            continue
        out.append(
            {
                "date": iso,
                "o": _as_float(row.get("Open")),
                "h": _as_float(row.get("High")),
                "l": _as_float(row.get("Low")),
                "c": _as_float(row.get("Close")),
                "v": _as_int(row.get("Volume")),
            }
        )
    return out


def snap_to_session(rows: list[dict], anchor: str) -> tuple[Optional[int], str]:
    """Index of the anchor date in ``rows``, else the nearest earlier session.

    Returns ``(index, snapped_from)``, where ``snapped_from`` is the originally
    requested date when a snap happened and "" when it did not. Weekends,
    holidays and dates before the listing all land here, and the panel says so
    out loud rather than quietly showing a different day."""
    if not rows:
        return None, ""
    for i, r in enumerate(rows):
        if r["date"] == anchor:
            return i, ""
    earlier = [i for i, r in enumerate(rows) if r["date"] < anchor]
    if earlier:
        return earlier[-1], anchor
    return None, ""


def avg_volume(rows: list[dict], upto: int, window: int = 30) -> Optional[float]:
    """Mean volume over the ``window`` sessions ending the day *before*
    ``upto``. The anchor day is excluded on purpose -- including a 4x volume
    day in the average it is being measured against blunts the very signal the
    ratio exists to show."""
    lo = max(0, upto - window)
    vals = [r["v"] for r in rows[lo:upto] if r["v"]]
    return sum(vals) / len(vals) if vals else None


class DayInfoProvider(Provider):
    """Serves ``daystat:*`` and ``dayev:*``."""

    def topic_patterns(self) -> list[str]:
        return ["daystat:*", "dayev:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            parts = topic.split(":")
            if parts[0] == "daystat" and len(parts) == 3:
                hub.run_async(lambda t=topic: self._fetch_daystat(t))
            elif parts[0] == "dayev" and len(parts) == 3 and ".." in parts[2]:
                hub.run_async(lambda t=topic: self._fetch_dayev(t))
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

    # -- shared fetch ------------------------------------------------------

    def _history(self, symbol: str, start: str, end: str, *, memo: bool) -> list[dict]:
        """Daily bars for [start, end] inclusive. ``memo`` is on for benchmark
        and sector series, which repeat across every symbol opened in a
        session; it is off for the stock itself, which does not."""
        key = (symbol, start, end)
        if memo:
            with _memo_lock:
                hit = _HIST_MEMO.get(key)
                if hit and (time.monotonic() - hit[0]) < _HIST_MEMO_TTL:
                    return hit[1]
        end_excl = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        df = with_retry(
            lambda: yf.Ticker(symbol).history(start=start, end=end_excl, interval="1d")
        )
        rows = rows_from_frame(df)
        if memo:
            with _memo_lock:
                if len(_HIST_MEMO) > 64:
                    _HIST_MEMO.clear()
                _HIST_MEMO[key] = (time.monotonic(), rows)
        return rows

    @staticmethod
    def _session_move(rows: list[dict], session: str) -> Optional[float]:
        """A benchmark's own move on the same session, so the comparison is
        like-for-like with the stock."""
        idx, _ = snap_to_session(rows, session)
        if idx is None or idx == 0:
            return None
        return pct_change(rows[idx - 1]["c"], rows[idx]["c"])

    # -- daystat -----------------------------------------------------------

    def _fetch_daystat(self, topic: str) -> None:
        hub = DataHub.instance()
        _, symbol, anchor = topic.split(":", 2)
        if RATE_LIMIT_GATE.blocked():
            hub.publish_error(topic, RATE_LIMIT_MESSAGE)
            return
        try:
            anchor_d = date.fromisoformat(anchor)
        except ValueError:
            hub.publish_error(topic, f"not a date: {anchor}")
            return
        try:
            start = (anchor_d - timedelta(days=LOOKBACK_DAYS)).isoformat()
            end = (anchor_d + timedelta(days=SNAP_LIMIT_DAYS)).isoformat()
            rows = self._history(symbol, start, end, memo=False)
            idx, snapped_from = snap_to_session(rows, anchor)
            if idx is None:
                hub.publish(
                    topic,
                    {
                        "symbol": symbol,
                        "date": anchor,
                        "empty": True,
                        "reason": "no trading data on or before this date",
                    },
                )
                return

            day = rows[idx]
            prev_c = rows[idx - 1]["c"] if idx > 0 else None
            stock_pct = pct_change(prev_c, day["c"])
            avg_v = avg_volume(rows, idx)
            session = day["date"]

            value: dict = {
                "symbol": symbol,
                "date": session,
                "snapped_from": snapped_from,
                "o": day["o"],
                "h": day["h"],
                "l": day["l"],
                "c": day["c"],
                "v": day["v"],
                "prev_close": prev_c,
                "pct": stock_pct,
                "avg_vol_30": avg_v,
                "vol_ratio": (day["v"] / avg_v) if (avg_v and day["v"]) else None,
                "bench_sym": None,
                "bench_pct": None,
                "sector_sym": None,
                "sector_pct": None,
                "excess_pct": None,
                "verdict": "",
            }

            bench = benchmark_for(symbol)
            if bench:
                b_rows = self._history(bench, start, end, memo=True)
                b_pct = self._session_move(b_rows, session)
                value["bench_sym"] = bench
                value["bench_pct"] = b_pct
                value["excess_pct"] = excess(stock_pct, b_pct)
                value["verdict"] = verdict(value["excess_pct"], stock_pct)

            proxy = sector_proxy_for(symbol, self._sector_of(symbol))
            if proxy:
                s_rows = self._history(proxy, start, end, memo=True)
                value["sector_sym"] = proxy
                value["sector_pct"] = self._session_move(s_rows, session)

            hub.publish(topic, value)
        except Exception as exc:
            publish_fetch_error(hub, topic, "day stats fetch failed", exc)

    @staticmethod
    def _sector_of(symbol: str) -> Optional[str]:
        """The stock's sector: from the hub when the Profile panel already
        fetched it, else straight from yfinance. Never fatal -- a missing
        sector only drops the sector comparison line."""
        try:
            cached = DataHub.instance().peek(f"profile:{symbol}")
            if isinstance(cached, dict) and cached.get("sector"):
                return str(cached["sector"])
        except Exception:
            pass
        try:
            info = with_retry(lambda: yf.Ticker(symbol).info) or {}
            return info.get("sector")
        except Exception:
            return None

    # -- dayev -------------------------------------------------------------

    def _fetch_dayev(self, topic: str) -> None:
        hub = DataHub.instance()
        _, symbol, window = topic.split(":", 2)
        start, _, end = window.partition("..")
        if RATE_LIMIT_GATE.blocked():
            hub.publish_error(topic, RATE_LIMIT_MESSAGE)
            return
        try:
            tkr = yf.Ticker(symbol)
            events: list[dict] = []
            events += self._earnings_events(tkr, start, end)
            events += self._cash_events(tkr, start, end)
            events += self._rating_events(tkr, start, end)
            events.sort(key=lambda e: (e["date"], e["kind"]))
            hub.publish(topic, {"symbol": symbol, "events": events})
        except Exception as exc:
            publish_fetch_error(hub, topic, "day events fetch failed", exc)

    @staticmethod
    def _earnings_events(tkr: Any, start: str, end: str) -> list[dict]:
        out: list[dict] = []
        try:
            df = with_retry(lambda: tkr.earnings_dates)
        except Exception:
            return out
        if df is None or getattr(df, "empty", True):
            return out
        for idx, row in df.iterrows():
            iso = _iso(idx)
            if not iso or not (start <= iso <= end):
                continue
            est = _as_float(row.get("EPS Estimate"))
            act = _as_float(row.get("Reported EPS"))
            surprise = _as_float(row.get("Surprise(%)"))
            if act is None and est is None:
                detail = "scheduled"
            else:
                shown_a = f"{act:,.2f}" if act is not None else "--"
                shown_e = f"{est:,.2f}" if est is not None else "--"
                detail = f"EPS {shown_a} vs {shown_e}e"
                if surprise is not None:
                    detail += f"  ({surprise:+.1f}%)"
            out.append(
                {"date": iso, "kind": "earnings", "title": "Earnings", "detail": detail}
            )
        return out

    @staticmethod
    def _cash_events(tkr: Any, start: str, end: str) -> list[dict]:
        """Ex-dividends and splits -- both are ordinary explanations for a gap
        that has nothing whatever to do with news."""
        out: list[dict] = []
        try:
            divs = with_retry(lambda: tkr.dividends)
            if divs is not None and not divs.empty:
                for idx, amount in divs.items():
                    iso = _iso(idx)
                    amt = _as_float(amount)
                    if iso and start <= iso <= end and amt:
                        out.append(
                            {
                                "date": iso,
                                "kind": "dividend",
                                "title": "Ex-dividend",
                                "detail": f"{amt:g} per share",
                            }
                        )
        except Exception:
            pass
        try:
            splits = with_retry(lambda: tkr.splits)
            if splits is not None and not splits.empty:
                for idx, ratio in splits.items():
                    iso = _iso(idx)
                    r = _as_float(ratio)
                    if iso and start <= iso <= end and r:
                        out.append(
                            {
                                "date": iso,
                                "kind": "split",
                                "title": "Split",
                                "detail": f"{r:g}-for-1",
                            }
                        )
        except Exception:
            pass
        return out

    @staticmethod
    def _rating_events(tkr: Any, start: str, end: str) -> list[dict]:
        """Analyst actions. Reads the full upgrades_downgrades frame rather
        than the 15-row slice market.py:361 keeps for the Analyst panel -- a
        date two years back needs the whole history, and widening that slice
        would change what the Analyst panel shows."""
        out: list[dict] = []
        try:
            df = with_retry(lambda: tkr.upgrades_downgrades)
        except Exception:
            return out
        if df is None or getattr(df, "empty", True):
            return out
        for idx, row in df.iterrows():
            iso = _iso(idx)
            if not iso or not (start <= iso <= end):
                continue
            firm = str(row.get("Firm", "") or "").strip()
            frm = str(row.get("FromGrade", "") or "").strip()
            to = str(row.get("ToGrade", "") or "").strip()
            action = str(row.get("Action", "") or "").strip().lower()
            if frm and to and frm != to:
                detail = f"{frm} → {to}"
            elif to:
                # no grade change: say what the action actually was, so a
                # reiteration never reads as an upgrade
                word = ACTION_WORDS.get(action)
                detail = f"{to} ({word})" if word else to
            else:
                detail = ACTION_WORDS.get(action, action)
            out.append(
                {
                    "date": iso,
                    "kind": "rating",
                    "title": firm or "Rating change",
                    "detail": detail,
                    "action": action,
                }
            )
        return out
