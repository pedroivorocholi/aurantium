"""Macro/econ data provider: FRED, World Bank, commodities, CFTC, EIA.

All API keys are read from environment variables only (never hardcoded);
see ``__main__.py`` which loads ``.env`` before providers are registered.
Keyless sources (World Bank, CFTC) work with no configuration.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

from ..datahub import DataHub, Provider


def _as_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


# Commodity code -> Yahoo Finance futures/spot symbol. World Bank's Pink
# Sheet commodity data has no clean REST endpoint (Fincept's original
# CME/LME scripts for this were broken placeholders); we substitute Yahoo
# Finance futures quotes as a practical stand-in.
_WBC_SYMBOLS = {
    "GOLD": "GC=F",
    "COPPER": "HG=F",
    "BRENT": "BZ=F",
    "WTI": "CL=F",
    "NATGAS": "NG=F",
    "SILVER": "SI=F",
    "ALUMINUM": "ALI=F",
}

# CFTC market key -> market_and_exchange_names filter (Socrata `like` pattern).
_CFTC_MARKETS = {
    "gold": "GOLD - COMMODITY EXCHANGE INC.",
    "crude_oil": "CRUDE OIL, LIGHT SWEET%",
    "sp500": "E-MINI S&P 500%",
    "bitcoin": "BITCOIN%",
    "euro_fx": "EURO FX%",
}

# EIA petroleum spot price route -> series id.
_EIA_SERIES = {
    "wti": "RWTC",
    "brent": "RBRTE",
    "gasoline": "EER_EPMRU_PF4_RGC_DPG",
    "diesel": "EER_EPD2DXL0_PF4_RGC_DPG",
}


class EconProvider(Provider):
    """Serves ``fred:*``, ``wb:*``, ``wbc:*``, ``cftc:*``, ``eia:*`` topics."""

    def topic_patterns(self) -> list[str]:
        return ["fred:*", "wb:*", "wbc:*", "cftc:*", "eia:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            parts = topic.split(":")
            kind = parts[0]
            if kind == "fred" and len(parts) >= 2:
                series_id = parts[1]
                hub.run_async(lambda t=topic, s=series_id: self._fetch_fred(t, s))
            elif kind == "wb" and len(parts) >= 3:
                country, indicator = parts[1], parts[2]
                hub.run_async(
                    lambda t=topic, c=country, i=indicator: self._fetch_wb(t, c, i)
                )
            elif kind == "wbc" and len(parts) >= 2:
                code = parts[1]
                hub.run_async(lambda t=topic, c=code: self._fetch_wbc(t, c))
            elif kind == "cftc" and len(parts) >= 2:
                market = parts[1]
                hub.run_async(lambda t=topic, m=market: self._fetch_cftc(t, m))
            elif kind == "eia" and len(parts) >= 3:
                route = parts[2]
                hub.run_async(lambda t=topic, r=route: self._fetch_eia(t, r))
            else:
                hub.publish_error(topic, f"unrecognized topic: {topic}")

    # -- FRED ------------------------------------------------------------

    def _fetch_fred(self, topic: str, series_id: str) -> None:
        hub = DataHub.instance()
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            hub.publish_error(
                topic, "FRED_API_KEY not set (free key: fred.stlouisfed.org)"
            )
            return
        try:
            obs_resp = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                },
                timeout=10,
            )
            obs_resp.raise_for_status()
            observations = obs_resp.json().get("observations", [])
            points: list[list] = []
            for obs in observations:
                raw = obs.get("value")
                val = None
                if raw not in (None, ".", ""):
                    val = _as_float(raw)
                points.append([obs.get("date"), val])

            title, units = series_id, ""
            try:
                meta_resp = requests.get(
                    "https://api.stlouisfed.org/fred/series",
                    params={
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                    },
                    timeout=10,
                )
                meta_resp.raise_for_status()
                series_list = meta_resp.json().get("seriess", [])
                if series_list:
                    title = series_list[0].get("title", series_id)
                    units = series_list[0].get("units", "")
            except Exception:
                pass  # metadata is nice-to-have; tolerate failure

            hub.publish(
                topic,
                {"id": series_id, "title": title, "units": units, "points": points},
            )
        except Exception as exc:
            hub.publish_error(topic, f"FRED fetch failed: {exc}")

    # -- World Bank indicators --------------------------------------------

    def _fetch_wb(self, topic: str, country: str, indicator: str) -> None:
        hub = DataHub.instance()
        try:
            resp = requests.get(
                f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}",
                params={"format": "json", "per_page": 100},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or len(data) < 2 or not data[1]:
                hub.publish_error(topic, f"no World Bank data for {country}/{indicator}")
                return
            rows = data[1]
            name = indicator
            points: list[list] = []
            for row in rows:
                ind = row.get("indicator") or {}
                if ind.get("value"):
                    name = ind.get("value")
                points.append([row.get("date"), _as_float(row.get("value"))])
            points.reverse()  # API returns newest-first; make it oldest -> newest
            hub.publish(
                topic,
                {"country": country, "indicator": indicator, "name": name, "points": points},
            )
        except Exception as exc:
            hub.publish_error(topic, f"World Bank fetch failed: {exc}")

    # -- commodities (Yahoo Finance stand-in) -------------------------------

    def _fetch_wbc(self, topic: str, code: str) -> None:
        hub = DataHub.instance()
        symbol = _WBC_SYMBOLS.get(code.upper())
        if symbol is None:
            hub.publish_error(topic, f"unknown commodity code: {code}")
            return
        try:
            import yfinance as yf

            tkr = yf.Ticker(symbol)
            hist = tkr.history(period="3mo", interval="1d")
            if hist is None or hist.empty:
                hub.publish_error(topic, f"no data for {code} ({symbol})")
                return
            closes = hist["Close"].tolist()
            price = _as_float(closes[-1])
            prev = _as_float(closes[-2]) if len(closes) > 1 else None
            change_pct = None
            if price is not None and prev:
                change_pct = (price - prev) / prev * 100.0
            points = [
                [ts.isoformat(), _as_float(v)]
                for ts, v in zip(hist.index.to_pydatetime(), closes)
            ]
            hub.publish(
                topic,
                {
                    "code": code.upper(),
                    "symbol": symbol,
                    "price": price,
                    "change_pct": change_pct,
                    "points": points,
                },
            )
        except Exception as exc:
            hub.publish_error(topic, f"commodity fetch failed: {exc}")

    # -- CFTC Commitment of Traders -----------------------------------------

    def _fetch_cftc(self, topic: str, market: str) -> None:
        hub = DataHub.instance()
        pattern = _CFTC_MARKETS.get(market)
        if pattern is None:
            hub.publish_error(topic, f"unknown CFTC market: {market}")
            return
        try:
            resp = requests.get(
                "https://publicreporting.cftc.gov/resource/jun7-fc8e.json",
                params={
                    "$limit": 8,
                    "$order": "report_date_as_yyyy_mm_dd DESC",
                    "$where": f"market_and_exchange_names like '{pattern}'",
                },
                timeout=10,
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                hub.publish_error(topic, f"no CFTC data for {market}")
                return
            row = rows[0]

            def _i(key: str) -> int:
                f = _as_float(row.get(key))
                return int(f) if f is not None else 0

            commercial_net = _i("comm_positions_long_all") - _i("comm_positions_short_all")
            noncommercial_net = _i("noncomm_positions_long_all") - _i(
                "noncomm_positions_short_all"
            )
            open_interest = _i("open_interest_all")
            bias = "bullish" if noncommercial_net > 0 else "bearish"

            hub.publish(
                topic,
                {
                    "market": market,
                    "report_date": row.get("report_date_as_yyyy_mm_dd", ""),
                    "commercial_net": commercial_net,
                    "noncommercial_net": noncommercial_net,
                    "open_interest": open_interest,
                    "bias": bias,
                },
            )
        except Exception as exc:
            hub.publish_error(topic, f"CFTC fetch failed: {exc}")

    # -- EIA petroleum spot prices -------------------------------------------

    def _fetch_eia(self, topic: str, route: str) -> None:
        hub = DataHub.instance()
        api_key = os.environ.get("EIA_API_KEY")
        if not api_key:
            hub.publish_error(topic, "EIA_API_KEY not set (free key: eia.gov/opendata)")
            return
        series = _EIA_SERIES.get(route)
        if series is None:
            hub.publish_error(topic, f"unknown EIA route: {route}")
            return
        try:
            resp = requests.get(
                "https://api.eia.gov/v2/petroleum/pri/spt/data/",
                params={
                    "api_key": api_key,
                    "frequency": "weekly",
                    "data[0]": "value",
                    "facets[series][]": series,
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "length": 52,
                },
                timeout=10,
            )
            resp.raise_for_status()
            rows = resp.json().get("response", {}).get("data", [])
            units = rows[0].get("units", "") if rows else ""
            points = [[row.get("period"), _as_float(row.get("value"))] for row in rows]
            points.reverse()  # oldest -> newest
            hub.publish(
                topic, {"route": route, "series": series, "units": units, "points": points}
            )
        except Exception as exc:
            hub.publish_error(topic, f"EIA fetch failed: {exc}")
