"""Yahoo-screener provider: stubbed ``yf.screen`` / ``yf.Sector``, no network."""

import pytest

from aurantium.providers import _yf, screen
from aurantium.providers.screen import ScreenProvider


def _q(sym, cap, price=10.0, pct=1.5, name=None):
    return {
        "symbol": sym,
        "shortName": name or f"{sym} Inc",
        "marketCap": cap,
        "regularMarketPrice": price,
        "regularMarketChangePercent": pct,
        "currency": "USD",
    }


class _Hub:
    def __init__(self):
        self.published = {}
        self.errors = {}

    def publish(self, topic, value):
        self.published[topic] = value

    def publish_error(self, topic, msg):
        self.errors[topic] = msg


@pytest.fixture
def hub(monkeypatch):
    h = _Hub()
    monkeypatch.setattr(screen.DataHub, "instance", staticmethod(lambda: h))
    monkeypatch.setattr(_yf, "RATE_LIMIT_GATE", _yf._RateLimitGate())
    monkeypatch.setattr(screen, "RATE_LIMIT_GATE", _yf.RATE_LIMIT_GATE)
    monkeypatch.setattr(screen, "_UNIVERSE", None)
    return h


def test_sector_topic_maps_screener_rows(hub, monkeypatch):
    calls = []

    def fake_screen(query, **kw):
        calls.append(kw)
        return {"quotes": [_q("AAPL", 3e12, 200.0, 1.23), _q("MSFT", 2e12)]}

    monkeypatch.setattr(screen.yf, "screen", fake_screen)
    topic = "screen:sector:Consumer Cyclical:2"
    ScreenProvider()._fetch(topic)
    val = hub.published[topic]
    assert [r["symbol"] for r in val["rows"]] == ["AAPL", "MSFT"]
    r = val["rows"][0]
    assert r == {
        "symbol": "AAPL", "name": "AAPL Inc", "market_cap": 3e12,
        "price": 200.0, "change_pct": 1.23, "currency": "USD",
    }
    assert "as_of" in val
    assert calls[0]["sortField"] == "intradaymarketcap"
    assert calls[0]["sortAsc"] is False
    assert calls[0]["size"] == 2


def test_symbols_topic_is_order_independent(hub, monkeypatch):
    monkeypatch.setattr(
        screen.yf, "screen",
        lambda q, **kw: {"quotes": [_q("AAPL", 3e12), _q("MSFT", 2e12), _q("ZZZ", 1e9)]},
    )
    p = ScreenProvider()
    assert screen.symbols_topic(["msft", "AAPL"]) == screen.symbols_topic(["AAPL", "MSFT", "AAPL"])
    assert screen.symbols_topic(["MSFT", "AAPL"]) == "screen:symbols:AAPL,MSFT"
    p._fetch("screen:symbols:AAPL,MSFT")
    rows = hub.published["screen:symbols:AAPL,MSFT"]["rows"]
    assert sorted(r["symbol"] for r in rows) == ["AAPL", "MSFT"]  # ZZZ filtered out


def test_sectorcap_reads_overview(hub, monkeypatch):
    seen = []

    class FakeSector:
        def __init__(self, key):
            seen.append(key)
            self.overview = {"market_cap": float(len(key))}

    monkeypatch.setattr(screen.yf, "Sector", FakeSector)
    ScreenProvider()._fetch("sectorcap:all")
    val = hub.published["sectorcap:all"]
    assert len(val) == 11
    assert val["Consumer Cyclical"] == float(len("consumer-cyclical"))
    assert "technology" in seen and "financial-services" in seen


def test_rate_limited_publishes_error_not_empty(hub, monkeypatch):
    monkeypatch.setattr(screen.yf, "screen", lambda *a, **k: {"quotes": []})
    _yf.RATE_LIMIT_GATE.trip()
    p = ScreenProvider()
    p._fetch("screen:sector:Technology:5")
    p._fetch("sectorcap:all")
    assert hub.errors["screen:sector:Technology:5"] == _yf.RATE_LIMIT_MESSAGE
    assert hub.errors["sectorcap:all"] == _yf.RATE_LIMIT_MESSAGE
    assert hub.published == {}


def test_fetch_exception_publishes_error(hub, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(screen.yf, "screen", boom)
    ScreenProvider()._fetch("screen:sector:Technology:5")
    assert "down" in hub.errors["screen:sector:Technology:5"]
    assert hub.published == {}


def test_universe_is_cached_across_symbol_topics(hub, monkeypatch):
    calls = []

    def fake_screen(q, **kw):
        calls.append(kw)
        return {"quotes": [_q("AAPL", 3e12), _q("MSFT", 2e12), _q("XOM", 1e12)]}

    monkeypatch.setattr(screen.yf, "screen", fake_screen)
    p = ScreenProvider()
    p._fetch("screen:symbols:AAPL")
    p._fetch("screen:symbols:MSFT,XOM")
    assert len(calls) == 1
    assert [r["symbol"] for r in hub.published["screen:symbols:MSFT,XOM"]["rows"]] == ["MSFT", "XOM"]


def test_universe_failure_is_not_cached(hub, monkeypatch):
    state = {"n": 0}

    def flaky(q, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("down")
        return {"quotes": [_q("AAPL", 3e12)]}

    monkeypatch.setattr(screen.yf, "screen", flaky)
    p = ScreenProvider()
    p._fetch("screen:symbols:AAPL")
    assert "screen:symbols:AAPL" in hub.errors
    p._fetch("screen:symbols:AAPL")
    assert hub.published["screen:symbols:AAPL"]["rows"][0]["symbol"] == "AAPL"


def test_sectorcap_partial_failure_publishes_error(hub, monkeypatch):
    class FlakySector:
        def __init__(self, key):
            if key == "energy":
                raise RuntimeError("boom")
            self.overview = {"market_cap": 1.0}

    monkeypatch.setattr(screen.yf, "Sector", FlakySector)
    ScreenProvider()._fetch("sectorcap:all")
    assert "sectorcap:all" not in hub.published
    assert "boom" in hub.errors["sectorcap:all"]


@pytest.mark.parametrize("bad", [None, [], {"quotes": None}, {"nope": 1}])
def test_malformed_screen_result_publishes_error(hub, monkeypatch, bad):
    monkeypatch.setattr(screen.yf, "screen", lambda *a, **k: bad)
    ScreenProvider()._fetch("screen:sector:Technology:5")
    assert "screen:sector:Technology:5" in hub.errors
    assert hub.published == {}
