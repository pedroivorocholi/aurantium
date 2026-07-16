"""Fundamentals data provider: financial statements, earnings, dividends,
holders, options chains, and market movers, all via yfinance.

Handles six topic families in one provider, one ``run_async`` job per topic
(unlike ``MarketProvider``'s ``quote:*`` batching, these fetches don't share
an underlying API call, so batching wouldn't help).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

import yfinance as yf

from ..datahub import DataHub, Provider


def _as_float(x: Any) -> Optional[float]:
    """Coerce numpy/py numeric (or None/NaN) to a plain float, else None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _as_int(x: Any) -> Optional[int]:
    f = _as_float(x)
    return int(f) if f is not None else None


def _fi_get(fast_info: Any, *names: str) -> Any:
    """Read the first present field from a yfinance FastInfo object, trying
    both dict-style and attribute-style access (key naming has varied across
    yfinance versions)."""
    for name in names:
        try:
            val = fast_info[name]
            if val is not None:
                return val
        except Exception:
            pass
        val = getattr(fast_info, name, None)
        if val is not None:
            return val
    return None


def _date_to_iso(val: Any) -> str:
    try:
        return val.isoformat()
    except Exception:
        return str(val)


def _col_to_iso(col: Any) -> str:
    """DataFrame column (statement period) -> "YYYY-MM-DD" string."""
    try:
        return col.date().isoformat()
    except Exception:
        try:
            return col.strftime("%Y-%m-%d")
        except Exception:
            return str(col)


def _epoch_to_iso(x: Any) -> Optional[str]:
    f = _as_float(x)
    if f is None:
        return None
    try:
        return datetime.fromtimestamp(f, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _match_col(columns: Any, *needles: str) -> Optional[Any]:
    """Fuzzy column lookup: first column whose normalized name contains any
    of ``needles`` (also normalized), in priority order."""
    norm_cols = [(c, _norm(c)) for c in columns]
    for needle in needles:
        n_needle = _norm(needle)
        for c, nc in norm_cols:
            if n_needle in nc:
                return c
    return None


def _parse_pct(v: Any) -> Optional[float]:
    """Best-effort parse of a holders percentage cell: handles "5.32%"
    strings, fractions (0.0532), and already-percent floats (5.32)."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().rstrip("%")
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        f = _as_float(v)
        if f is None:
            return None
    if f < 1:
        f *= 100.0
    return f


def _find_holder_pct(df: Any, keyword: str) -> Optional[float]:
    """Locate a row mentioning ``keyword`` (e.g. "insider"/"institution") in
    a ``major_holders`` DataFrame. Shape varies wildly across yfinance
    versions, so this tries both the newer index-labeled shape and the
    older two-unlabeled-column shape; returns None if unparseable."""
    if df is None:
        return None
    try:
        if df.empty:
            return None
    except Exception:
        return None

    try:
        for idx in df.index:
            if keyword in str(idx).lower():
                try:
                    if "Value" in df.columns:
                        return _parse_pct(df.loc[idx, "Value"])
                except Exception:
                    pass
                try:
                    return _parse_pct(df.loc[idx].iloc[0])
                except Exception:
                    pass
    except Exception:
        pass

    try:
        for _, row in df.iterrows():
            vals = list(row.values)
            for i, v in enumerate(vals):
                if keyword in str(v).lower():
                    others = vals[:i] + vals[i + 1:]
                    if others:
                        return _parse_pct(others[0])
    except Exception:
        pass
    return None


def _split_ratio_str(ratio: float) -> Optional[str]:
    if ratio is None or ratio <= 0:
        return None

    def fmt(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else f"{x:g}"

    if ratio >= 1:
        return f"{fmt(ratio)}:1"
    return f"1:{fmt(1.0 / ratio)}"


def _is_future(ts: Any, now: datetime) -> bool:
    try:
        return ts > now
    except TypeError:
        try:
            return ts.replace(tzinfo=None) > now.replace(tzinfo=None)
        except Exception:
            return False


class FundamentalsProvider(Provider):
    """Serves ``financials:*``, ``earnings:*``, ``dividends:*``,
    ``holders:*``, ``options:*``, ``movers:*`` via yfinance."""

    def topic_patterns(self) -> list[str]:
        return [
            "financials:*",
            "earnings:*",
            "dividends:*",
            "holders:*",
            "options:*",
            "movers:*",
        ]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            parts = topic.split(":")
            kind = parts[0]
            if kind == "financials" and len(parts) == 2:
                hub.run_async(lambda t=topic, s=parts[1]: self._fetch_financials(t, s))
            elif kind == "earnings" and len(parts) == 2:
                hub.run_async(lambda t=topic, s=parts[1]: self._fetch_earnings(t, s))
            elif kind == "dividends" and len(parts) == 2:
                hub.run_async(lambda t=topic, s=parts[1]: self._fetch_dividends(t, s))
            elif kind == "holders" and len(parts) == 2:
                hub.run_async(lambda t=topic, s=parts[1]: self._fetch_holders(t, s))
            elif kind == "options" and len(parts) in (2, 3):
                symbol = parts[1]
                expiry = parts[2] if len(parts) == 3 else None
                hub.run_async(lambda t=topic, s=symbol, e=expiry: self._fetch_options(t, s, e))
            elif kind == "movers" and len(parts) == 2:
                hub.run_async(lambda t=topic, k=parts[1]: self._fetch_movers(t, k))
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

    # -- financials ----------------------------------------------------

    @staticmethod
    def _safe_df(getter: Any) -> Any:
        try:
            return getter()
        except Exception:
            return None

    @staticmethod
    def _stmt_dict(df: Any) -> dict:
        if df is None or df.empty:
            return {"columns": [], "rows": []}
        try:
            cols = sorted(df.columns, reverse=True)[:4]
        except Exception:
            cols = list(df.columns)[:4]
        columns = [_col_to_iso(c) for c in cols]
        rows = []
        for label in df.index:
            row_vals = [_as_float(df.loc[label, c]) for c in cols]
            rows.append([str(label)] + row_vals)
        return {"columns": columns, "rows": rows}

    def _fetch_financials(self, topic: str, symbol: str) -> None:
        hub = DataHub.instance()
        try:
            tkr = yf.Ticker(symbol)
            value = {
                "symbol": symbol,
                "income": {
                    "annual": self._stmt_dict(self._safe_df(lambda: tkr.income_stmt)),
                    "quarterly": self._stmt_dict(
                        self._safe_df(lambda: tkr.quarterly_income_stmt)
                    ),
                },
                "balance": {
                    "annual": self._stmt_dict(self._safe_df(lambda: tkr.balance_sheet)),
                    "quarterly": self._stmt_dict(
                        self._safe_df(lambda: tkr.quarterly_balance_sheet)
                    ),
                },
                "cashflow": {
                    "annual": self._stmt_dict(self._safe_df(lambda: tkr.cashflow)),
                    "quarterly": self._stmt_dict(
                        self._safe_df(lambda: tkr.quarterly_cashflow)
                    ),
                },
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"financials fetch failed: {exc}")

    # -- earnings --------------------------------------------------------

    def _fetch_earnings(self, topic: str, symbol: str) -> None:
        hub = DataHub.instance()
        try:
            tkr = yf.Ticker(symbol)
            df = tkr.earnings_dates
            rows: list[list] = []
            next_date = None

            if df is not None and not df.empty:
                est_col = _match_col(df.columns, "epsestimate")
                rep_col = _match_col(df.columns, "reportedeps")
                sur_col = _match_col(df.columns, "surprise")
                now = datetime.now(timezone.utc)

                entries = []
                for idx, row in df.iterrows():
                    eps_est = _as_float(row.get(est_col)) if est_col is not None else None
                    eps_rep = _as_float(row.get(rep_col)) if rep_col is not None else None
                    surprise = _as_float(row.get(sur_col)) if sur_col is not None else None
                    entries.append((idx, [_date_to_iso(idx), eps_est, eps_rep, surprise]))

                future = [idx for idx, _ in entries if _is_future(idx, now)]
                if future:
                    try:
                        next_date = _date_to_iso(min(future))
                    except Exception:
                        next_date = None

                entries.sort(key=lambda e: e[0], reverse=True)
                rows = [e[1] for e in entries[:12]]

            value = {"symbol": symbol, "next_date": next_date, "rows": rows}
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"earnings fetch failed: {exc}")

    # -- dividends -------------------------------------------------------

    def _fetch_dividends(self, topic: str, symbol: str) -> None:
        hub = DataHub.instance()
        try:
            tkr = yf.Ticker(symbol)
            try:
                info = tkr.info or {}
            except Exception:
                info = {}

            rate = _as_float(info.get("dividendRate"))
            # yfinance's dividendYield has flip-flopped between fraction and
            # percent across versions; deriving from rate/price is unambiguous.
            price = _as_float(
                info.get("regularMarketPrice") or info.get("currentPrice")
            )
            if rate is not None and price:
                yield_pct = rate / price * 100.0
            else:
                dy = _as_float(info.get("dividendYield"))
                # sane equity yields are < 25%; treat smaller-than-plausible
                # values as fractions, implausibly large ones as junk
                if dy is None:
                    yield_pct = None
                elif dy < 0.25:
                    yield_pct = dy * 100.0
                elif dy <= 25:
                    yield_pct = dy
                else:
                    yield_pct = None
            ex_date = _epoch_to_iso(info.get("exDividendDate"))
            payout_ratio = _as_float(info.get("payoutRatio"))

            history: list[list] = []
            try:
                div_series = tkr.dividends
                if div_series is not None and not div_series.empty:
                    for idx, amt in div_series.tail(20).items():
                        history.append([_date_to_iso(idx), _as_float(amt)])
            except Exception:
                pass

            splits: list[list] = []
            try:
                split_series = tkr.splits
                if split_series is not None and not split_series.empty:
                    for idx, ratio in split_series.tail(10).items():
                        r = _as_float(ratio)
                        splits.append(
                            [_date_to_iso(idx), _split_ratio_str(r) if r is not None else None]
                        )
            except Exception:
                pass

            value = {
                "symbol": symbol,
                "yield_pct": yield_pct,
                "rate": rate,
                "ex_date": ex_date,
                "payout_ratio": payout_ratio,
                "history": history,
                "splits": splits,
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"dividends fetch failed: {exc}")

    # -- holders -----------------------------------------------------------

    def _fetch_holders(self, topic: str, symbol: str) -> None:
        hub = DataHub.instance()
        try:
            tkr = yf.Ticker(symbol)

            insiders_pct = institutions_pct = None
            try:
                mh = tkr.major_holders
                insiders_pct = _find_holder_pct(mh, "insider")
                institutions_pct = _find_holder_pct(mh, "institution")
            except Exception:
                pass

            top: list[list] = []
            try:
                ih = tkr.institutional_holders
                if ih is not None and not ih.empty:
                    holder_col = _match_col(ih.columns, "holder")
                    shares_col = _match_col(ih.columns, "shares")
                    pct_col = _match_col(ih.columns, "pctheld", "pctout", "held")
                    value_col = _match_col(ih.columns, "value")
                    for _, row in ih.head(15).iterrows():
                        name = row.get(holder_col) if holder_col is not None else None
                        shares = _as_int(row.get(shares_col)) if shares_col is not None else None
                        pct = _as_float(row.get(pct_col)) if pct_col is not None else None
                        if pct is not None and pct < 1:
                            pct *= 100.0
                        val = _as_float(row.get(value_col)) if value_col is not None else None
                        top.append(
                            [str(name) if name is not None else None, shares, pct, val]
                        )
            except Exception:
                pass

            value = {
                "symbol": symbol,
                "insiders_pct": insiders_pct,
                "institutions_pct": institutions_pct,
                "top": top,
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"holders fetch failed: {exc}")

    # -- options -------------------------------------------------------------

    @staticmethod
    def _nearest_rows(df: Any, spot: Optional[float]) -> list:
        if df is None or df.empty:
            return []
        try:
            records = df.to_dict("records")
        except Exception:
            return []

        def strike_of(r: dict) -> Optional[float]:
            return _as_float(r.get("strike"))

        valid = [r for r in records if strike_of(r) is not None]
        if spot is None:
            selected = sorted(valid, key=strike_of)[:16]
        else:
            below = sorted(
                [r for r in valid if strike_of(r) <= spot], key=lambda r: -strike_of(r)
            )[:8]
            above = sorted(
                [r for r in valid if strike_of(r) > spot], key=lambda r: strike_of(r)
            )[:8]
            selected = below + above
        selected.sort(key=strike_of)

        rows = []
        for r in selected:
            iv = _as_float(r.get("impliedVolatility"))
            rows.append(
                [
                    strike_of(r),
                    _as_float(r.get("lastPrice")),
                    _as_float(r.get("bid")),
                    _as_float(r.get("ask")),
                    _as_int(r.get("volume")),
                    _as_int(r.get("openInterest")),
                    iv * 100.0 if iv is not None else None,
                ]
            )
        return rows

    def _fetch_options(self, topic: str, symbol: str, expiry: Optional[str]) -> None:
        hub = DataHub.instance()
        try:
            tkr = yf.Ticker(symbol)
            expiries = list(tkr.options or ())
            if not expiries:
                hub.publish_error(topic, f"no options for {symbol}")
                return
            use_expiry = expiry if expiry in expiries else expiries[0]

            spot = None
            try:
                fi = tkr.fast_info
                spot = _as_float(_fi_get(fi, "last_price", "lastPrice"))
            except Exception:
                spot = None
            if spot is None:
                try:
                    info = tkr.info or {}
                    spot = _as_float(info.get("regularMarketPrice"))
                except Exception:
                    spot = None

            chain = tkr.option_chain(use_expiry)
            calls = self._nearest_rows(chain.calls, spot)
            puts = self._nearest_rows(chain.puts, spot)

            value = {
                "symbol": symbol,
                "spot": spot,
                "expiry": use_expiry,
                "expiries": expiries,
                "calls": calls,
                "puts": puts,
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"options fetch failed: {exc}")

    # -- movers --------------------------------------------------------------

    _MOVERS_SCREENS = {
        "gainers": "day_gainers",
        "losers": "day_losers",
        "actives": "most_actives",
    }

    def _fetch_movers(self, topic: str, kind: str) -> None:
        hub = DataHub.instance()
        try:
            screen_key = self._MOVERS_SCREENS.get(kind)
            if screen_key is None:
                raise ValueError(f"unknown movers kind: {kind}")
            result = yf.screen(screen_key)
            quotes = (result or {}).get("quotes") or []
            rows = []
            for q in quotes[:25]:
                symbol = q.get("symbol")
                name = q.get("shortName") or q.get("longName") or symbol
                rows.append(
                    [
                        symbol,
                        name,
                        _as_float(q.get("regularMarketPrice")),
                        _as_float(q.get("regularMarketChangePercent")),
                        _as_int(q.get("regularMarketVolume")),
                    ]
                )
            hub.publish(topic, rows)
        except Exception as exc:
            hub.publish_error(topic, f"movers unavailable: {exc}")
