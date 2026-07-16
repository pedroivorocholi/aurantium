"""Market data provider: quotes, OHLCV history, and analyst ratings via yfinance.

Handles three topic families in one provider so a single ``yf.Tickers`` batch
call can serve many ``quote:*`` topics at once. ``history:*`` and
``analyst:*`` topics are fetched one job per topic (their parameters vary
per-topic, so batching wouldn't help).
"""

from __future__ import annotations

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


class MarketProvider(Provider):
    """Serves ``quote:*``, ``history:*``, ``analyst:*`` via yfinance."""

    def topic_patterns(self) -> list[str]:
        return ["quote:*", "history:*", "analyst:*", "profile:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        quote_syms: list[str] = []
        history_topics: list[str] = []
        analyst_syms: list[str] = []
        profile_syms: list[str] = []

        for topic in topics:
            parts = topic.split(":")
            kind = parts[0]
            if kind == "quote" and len(parts) == 2:
                quote_syms.append(parts[1])
            elif kind == "history" and len(parts) == 4:
                history_topics.append(topic)
            elif kind == "analyst" and len(parts) == 2:
                analyst_syms.append(parts[1])
            elif kind == "profile" and len(parts) == 2:
                profile_syms.append(parts[1])
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

        if quote_syms:
            hub.run_async(lambda syms=list(quote_syms): self._fetch_quotes(syms))
        for ht in history_topics:
            hub.run_async(lambda t=ht: self._fetch_history(t))
        for sym in analyst_syms:
            hub.run_async(lambda s=sym: self._fetch_analyst(s))
        for sym in profile_syms:
            hub.run_async(lambda s=sym: self._fetch_profile(s))

    # -- quotes --------------------------------------------------------

    def _fetch_quotes(self, symbols: list[str]) -> None:
        """One job, one yf.Tickers batch call, per-symbol try/except so a
        single bad symbol doesn't fail the whole batch."""
        hub = DataHub.instance()
        try:
            batch = yf.Tickers(" ".join(symbols))
        except Exception as exc:
            for sym in symbols:
                hub.publish_error(f"quote:{sym}", f"quote fetch failed: {exc}")
            return

        for sym in symbols:
            topic = f"quote:{sym}"
            try:
                tkr = batch.tickers.get(sym) or yf.Ticker(sym)
                hub.publish(topic, self._build_quote(sym, tkr))
            except Exception as exc:
                hub.publish_error(topic, f"quote fetch failed: {exc}")

    def _build_quote(self, symbol: str, tkr: Any) -> dict:
        price = prev_close = day_high = day_low = volume = currency = None

        try:
            fi = tkr.fast_info
            price = _fi_get(fi, "last_price", "lastPrice")
            prev_close = _fi_get(
                fi, "previous_close", "previousClose", "regular_market_previous_close"
            )
            day_high = _fi_get(fi, "day_high", "dayHigh")
            day_low = _fi_get(fi, "day_low", "dayLow")
            volume = _fi_get(fi, "last_volume", "lastVolume")
            currency = _fi_get(fi, "currency")
        except Exception:
            pass

        name: Any = symbol
        try:
            info = tkr.info or {}
            name = info.get("longName") or info.get("shortName") or symbol
            if price is None:
                price = info.get("currentPrice") or info.get("regularMarketPrice")
            if prev_close is None:
                prev_close = info.get("previousClose") or info.get(
                    "regularMarketPreviousClose"
                )
            if currency is None:
                currency = info.get("currency")
            if day_high is None:
                day_high = info.get("dayHigh") or info.get("regularMarketDayHigh")
            if day_low is None:
                day_low = info.get("dayLow") or info.get("regularMarketDayLow")
            if volume is None:
                volume = info.get("volume") or info.get("regularMarketVolume")
        except Exception:
            pass

        price_f = _as_float(price)
        prev_close_f = _as_float(prev_close)
        change = change_pct = None
        if price_f is not None and prev_close_f:
            change = price_f - prev_close_f
            change_pct = (change / prev_close_f) * 100.0

        return {
            "symbol": symbol,
            "name": str(name) if name is not None else symbol,
            "price": price_f,
            "change": change,
            "change_pct": change_pct,
            "prev_close": prev_close_f,
            "volume": _as_int(volume),
            "currency": str(currency) if currency is not None else None,
            "day_high": _as_float(day_high),
            "day_low": _as_float(day_low),
        }

    # -- history ---------------------------------------------------------

    def _fetch_history(self, topic: str) -> None:
        hub = DataHub.instance()
        parts = topic.split(":")
        if len(parts) != 4:
            hub.publish_error(topic, f"malformed history topic: {topic}")
            return
        _, symbol, period, interval = parts
        try:
            tkr = yf.Ticker(symbol)
            df = tkr.history(period=period, interval=interval)
            if df is None or df.empty:
                hub.publish_error(topic, f"no history data for {symbol}")
                return
            t = [int(ts.timestamp()) for ts in df.index.to_pydatetime()]
            value = {
                "symbol": symbol,
                "period": period,
                "interval": interval,
                "t": t,
                "o": [_as_float(v) for v in df["Open"].tolist()],
                "h": [_as_float(v) for v in df["High"].tolist()],
                "l": [_as_float(v) for v in df["Low"].tolist()],
                "c": [_as_float(v) for v in df["Close"].tolist()],
                "v": [_as_int(v) for v in df["Volume"].tolist()],
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"history fetch failed: {exc}")

    # -- analyst -----------------------------------------------------------

    def _fetch_analyst(self, symbol: str) -> None:
        hub = DataHub.instance()
        topic = f"analyst:{symbol}"
        try:
            tkr = yf.Ticker(symbol)
            info = tkr.info or {}
            value: dict = {
                "symbol": symbol,
                "target_high": _as_float(info.get("targetHighPrice")),
                "target_low": _as_float(info.get("targetLowPrice")),
                "target_mean": _as_float(info.get("targetMeanPrice")),
                "recommendation_mean": _as_float(info.get("recommendationMean")),
                "recommendation_key": info.get("recommendationKey"),
                "analyst_count": _as_int(info.get("numberOfAnalystOpinions")),
                "upgrades": [],
            }

            try:
                df = tkr.upgrades_downgrades
                if df is not None and not df.empty:
                    d = df.reset_index()
                    date_col = d.columns[0]
                    d = d.sort_values(date_col, ascending=False).head(15)
                    upgrades = []
                    for _, row in d.iterrows():
                        upgrades.append(
                            {
                                "date": _date_to_iso(row[date_col]),
                                "firm": str(row.get("Firm", "") or ""),
                                "action": str(row.get("Action", "") or ""),
                                "from_grade": str(row.get("FromGrade", "") or ""),
                                "to_grade": str(row.get("ToGrade", "") or ""),
                            }
                        )
                    value["upgrades"] = upgrades
            except Exception:
                pass  # upgrades_downgrades is optional/flaky; keep the rest

            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"analyst fetch failed: {exc}")

    # -- profile -------------------------------------------------------------

    def _fetch_profile(self, symbol: str) -> None:
        hub = DataHub.instance()
        topic = f"profile:{symbol}"
        try:
            tkr = yf.Ticker(symbol)
            info = tkr.info or {}

            officers: list[dict] = []
            raw_officers = info.get("companyOfficers")
            if isinstance(raw_officers, list):
                for o in raw_officers[:5]:
                    if not isinstance(o, dict):
                        continue
                    officers.append(
                        {
                            "name": o.get("name"),
                            "title": o.get("title"),
                        }
                    )

            value = {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName"),
                "description": info.get("longBusinessSummary"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "website": info.get("website"),
                "country": info.get("country"),
                "city": info.get("city"),
                "employees": _as_int(info.get("fullTimeEmployees")),
                "market_cap": _as_float(info.get("marketCap")),
                "pe_trailing": _as_float(info.get("trailingPE")),
                "pe_forward": _as_float(info.get("forwardPE")),
                "eps_trailing": _as_float(info.get("trailingEps")),
                "dividend_yield": _as_float(info.get("dividendYield")),
                "beta": _as_float(info.get("beta")),
                "week52_high": _as_float(info.get("fiftyTwoWeekHigh")),
                "week52_low": _as_float(info.get("fiftyTwoWeekLow")),
                "shares_outstanding": _as_int(info.get("sharesOutstanding")),
                "officers": officers,
            }
            hub.publish(topic, value)
        except Exception as exc:
            hub.publish_error(topic, f"profile fetch failed: {exc}")
