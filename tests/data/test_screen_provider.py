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


def _intl(sym, cap, cur, price=10.0, pct=-1.0):
    return {"symbol": sym, "shortName": f"{sym.upper()}   PN   N2", "longName": f"{sym} SA",
            "marketCap": cap, "regularMarketPrice": price,
            "regularMarketChangePercent": pct, "currency": cur}


def test_symbols_topic_prices_non_us_symbols(hub, monkeypatch):
    screens, batches = [], []

    def fake_screen(q, **kw):
        screens.append(kw)
        return {"quotes": [_q("AAPL", 3e12), _q("MSFT", 2e12)]}

    def fake_quotes(symbols):
        batches.append(list(symbols))
        return [_intl(s, 6e11, "BRL") for s in symbols if s != "NOPE.SA"]

    monkeypatch.setattr(screen.yf, "screen", fake_screen)
    monkeypatch.setattr(screen, "_yahoo_quotes", fake_quotes)
    p = ScreenProvider()
    topic = screen.symbols_topic(["PETR4.SA", "AAPL", "NOPE.SA"])
    p._fetch(topic)
    rows = {r["symbol"]: r for r in hub.published[topic]["rows"]}
    assert set(rows) == {"AAPL", "PETR4.SA"}  # Yahoo does not know NOPE.SA: no row
    assert rows["PETR4.SA"] == {
        "symbol": "PETR4.SA", "name": "PETR4.SA SA", "market_cap": 6e11,
        "price": 10.0, "change_pct": -1.0, "currency": "BRL",
    }
    assert len(screens) == 1  # the US universe still prices AAPL
    assert batches == [["NOPE.SA", "PETR4.SA"]]  # one batch for everything else


def test_symbols_topic_without_us_symbols_skips_the_universe(hub, monkeypatch):
    def no_screen(q, **kw):
        raise AssertionError("the US universe is not needed")

    monkeypatch.setattr(screen.yf, "screen", no_screen)
    monkeypatch.setattr(screen, "_yahoo_quotes",
                        lambda syms: [_intl(s, 1e9, "JPY") for s in syms])
    monkeypatch.setattr(screen, "QUOTE_BATCH", 2)
    topic = screen.symbols_topic(["7203.T", "6758.T", "9984.T"])
    ScreenProvider()._fetch(topic)
    assert len(hub.published[topic]["rows"]) == 3


def test_us_symbols_outside_the_universe_are_quoted(hub, monkeypatch):
    monkeypatch.setattr(screen.yf, "screen", lambda q, **kw: {"quotes": [_q("AAPL", 3e12)]})
    seen = []
    monkeypatch.setattr(screen, "_yahoo_quotes",
                        lambda syms: seen.extend(syms) or [_q(s, 1e9) for s in syms])
    topic = screen.symbols_topic(["AAPL", "SMALL"])
    ScreenProvider()._fetch(topic)
    assert seen == ["SMALL"]
    assert {r["symbol"] for r in hub.published[topic]["rows"]} == {"AAPL", "SMALL"}


def test_quote_batch_failure_publishes_error_not_partial_rows(hub, monkeypatch):
    monkeypatch.setattr(screen.yf, "screen", lambda q, **kw: {"quotes": [_q("AAPL", 3e12)]})

    def boom(syms):
        raise RuntimeError("quote endpoint down")

    monkeypatch.setattr(screen, "_yahoo_quotes", boom)
    topic = screen.symbols_topic(["AAPL", "PETR4.SA"])
    ScreenProvider()._fetch(topic)
    assert topic not in hub.published
    assert "quote endpoint down" in hub.errors[topic]


@pytest.fixture
def shares(monkeypatch):
    """A clean share-count cache, and the lookups made, per symbol."""
    monkeypatch.setattr(screen, "_SHARES", {})
    monkeypatch.setattr(screen, "_share_locks", {})
    return []


def _capless(*syms, cur="GBp", price=3352.0, kind="EQUITY"):
    return [{**_intl(s, None, cur, price=price), "quoteType": kind} for s in syms]


def test_an_equity_without_a_cap_is_sized_from_its_shares(hub, monkeypatch, shares):
    monkeypatch.setattr(screen, "_share_count", lambda s: shares.append(s) or 1e9)
    rows = _capless("HLMA.L") + _capless("ISF.L", price=800.0, kind="ETF")
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda syms: [dict(r) for r in rows])
    topic = screen.symbols_topic(["HLMA.L", "ISF.L"])
    p = ScreenProvider()
    p._fetch(topic)
    p._fetch(topic)
    got = {r["symbol"]: r for r in hub.published[topic]["rows"]}
    assert got["HLMA.L"]["market_cap"] == pytest.approx(33.52 * 1e9)  # pence -> pounds
    assert not got["HLMA.L"].get("cap_pending")
    assert got["ISF.L"]["market_cap"] is None and not got["ISF.L"].get("cap_pending")
    assert shares == ["HLMA.L"]  # the share count is kept, not re-fetched


def test_a_rate_limited_share_lookup_fails_the_topic_and_caches_nothing(hub, monkeypatch, shares):
    def throttled(sym):
        shares.append(sym)
        raise _yf.YFRateLimitError()

    monkeypatch.setattr(screen, "_share_count", throttled)
    monkeypatch.setattr(_yf.time, "sleep", lambda s: None)  # with_retry's backoff
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda syms: _capless(*syms))
    topic = screen.symbols_topic(["ALV.DE", "CON.DE"])
    ScreenProvider()._fetch(topic)
    assert topic not in hub.published
    assert hub.errors[topic] == _yf.RATE_LIMIT_MESSAGE
    assert screen._SHARES == {}  # not poisoned: the next refresh asks again
    assert set(shares) == {"ALV.DE"}  # the throttle stopped the loop at once


def test_the_cooldown_gate_is_checked_before_each_share_lookup(hub, monkeypatch, shares):
    def first_trips_the_gate(sym):
        shares.append(sym)
        _yf.RATE_LIMIT_GATE.trip()  # another provider is throttled meanwhile
        return 1e9

    monkeypatch.setattr(screen, "_share_count", first_trips_the_gate)
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda syms: _capless(*syms))
    topic = screen.symbols_topic(["ALV.DE", "CON.DE", "FRE.DE"])
    ScreenProvider()._fetch(topic)
    assert len(shares) == 1
    assert hub.errors[topic] == _yf.RATE_LIMIT_MESSAGE and topic not in hub.published


def test_share_lookups_are_capped_per_fetch(hub, monkeypatch, shares):
    monkeypatch.setattr(screen, "MAX_SHARE_LOOKUPS", 2)
    monkeypatch.setattr(screen, "_share_count", lambda s: shares.append(s) or 1e9)
    syms = ["A.DE", "B.DE", "C.DE", "D.DE"]
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda q: _capless(*q, cur="EUR", price=10.0))
    topic = screen.symbols_topic(syms)
    p = ScreenProvider()
    p._fetch(topic)
    rows = hub.published[topic]["rows"]
    assert len(shares) == 2 and len(rows) == 4  # every row kept
    waiting = [r["symbol"] for r in rows if r.get("cap_pending")]
    assert len(waiting) == 2 and all(r["market_cap"] is None for r in rows
                                     if r["symbol"] in waiting)
    p._fetch(topic)  # the next refresh fills the rest from where it stopped
    assert len(shares) == 4
    assert not any(r.get("cap_pending") for r in hub.published[topic]["rows"])


def test_no_share_data_is_cached_but_a_failed_request_is_not(hub, monkeypatch, shares):
    def lookup(sym):
        shares.append(sym)
        if sym == "BROKEN.L":
            raise ConnectionError("reset")
        return None  # Yahoo answered: it has no share count for this one

    monkeypatch.setattr(screen, "_share_count", lookup)
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda q: _capless(*q))
    topic = screen.symbols_topic(["BROKEN.L", "NODATA.L"])
    p = ScreenProvider()
    p._fetch(topic)
    rows = {r["symbol"]: r for r in hub.published[topic]["rows"]}
    assert rows["BROKEN.L"]["cap_pending"] and rows["NODATA.L"]["cap_pending"]
    assert screen._SHARES["NODATA.L"][1] is None and "BROKEN.L" not in screen._SHARES
    p._fetch(topic)
    assert shares.count("BROKEN.L") == 2 and shares.count("NODATA.L") == 1


def test_one_share_lookup_per_symbol_at_a_time(monkeypatch, shares):
    import threading

    started, release = threading.Event(), threading.Event()

    def slow(sym):
        shares.append(sym)
        started.set()
        release.wait(5)
        return 1e9

    monkeypatch.setattr(screen, "_share_count", slow)
    out = []
    workers = [threading.Thread(target=lambda: out.append(
        screen._shares("ALV.DE", screen._LookupBudget(5)))) for _ in range(2)]
    workers[0].start()
    started.wait(5)
    workers[1].start()  # a second panel's topic wants the same symbol
    release.set()
    for w in workers:
        w.join(5)
    assert shares == ["ALV.DE"] and out == [1e9, 1e9]


def test_share_count_reads_the_latest_timeseries_value(monkeypatch):
    calls = []

    class FakeData:
        def get_raw_json(self, url, params=None, timeout=30):
            calls.append((url, params))
            if params["symbol"] == "SPY":
                return {"timeseries": {"result": [{"meta": {}}]}}
            return {"timeseries": {"result": [
                {"timestamp": [3, 1, 2], "shares_out": [300.0, 100.0, 200.0]}]}}

    import yfinance.data
    monkeypatch.setattr(yfinance.data, "YfData", FakeData)
    assert screen._share_count("ALV.DE") == 300.0
    assert screen._share_count("SPY") is None
    assert "ALV.DE" in calls[0][0]


def test_index_topic_skips_quoting_us_names_outside_the_universe(hub, monkeypatch):
    monkeypatch.setattr(screen.yf, "screen", lambda q, **kw: {"quotes": [_q("AAPL", 3e12)]})
    asked = []
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda syms: asked.extend(syms) or [])
    monkeypatch.setitem(screen.INDEXES, "test", ("Test", 40, ("AAPL", "SMALL")))
    topic = screen.index_topic("test")
    assert topic == "screen:index:test"
    ScreenProvider()._fetch(topic)
    assert asked == []  # SMALL is smaller than all 250: it can never make a top-40 map
    assert [r["symbol"] for r in hub.published[topic]["rows"]] == ["AAPL"]


def test_index_topic_quotes_listings_abroad(hub, monkeypatch):
    def no_screen(q, **kw):
        raise AssertionError("no US names here")

    monkeypatch.setattr(screen.yf, "screen", no_screen)
    monkeypatch.setattr(screen, "_yahoo_quotes", lambda syms: [_intl(s, 1e9, "BRL") for s in syms])
    topic = screen.index_topic("ibov")
    ScreenProvider()._fetch(topic)
    assert len(hub.published[topic]["rows"]) == len(screen.INDEXES["ibov"][2])
    ScreenProvider()._fetch("screen:index:nope")
    assert "screen:index:nope" in hub.errors


def test_a_missing_yfinance_internal_fails_only_the_topic(hub, monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "yfinance.data", None)  # a future yfinance moved it
    topic = screen.symbols_topic(["PETR4.SA"])
    ScreenProvider()._fetch(topic)
    assert topic in hub.errors and topic not in hub.published
