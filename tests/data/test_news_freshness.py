"""News must be current, on topic, and newest first.

Diagnosed 2026-11-02 against live sources: NewsAPI's free tier serves articles
24 hours late and matches words anywhere in the body, yet it ranked ahead of
Google News — so a topic like "Brazil" showed day-old football coverage. No
tier had a freshness window either (PETR4.SA showed nine-year-old headlines).
Every source is now merged, filtered for relevance and age, and sorted by
publish time; NewsAPI only tops up a thin result.
"""

from datetime import datetime, timedelta, timezone

import pytest

from aurantium.providers import news
from aurantium.providers.news import NewsProvider

NOW = datetime.now(timezone.utc)


def _item(title, hours_ago=1.0, publisher="X", lang="en"):
    return {
        "title": title,
        "publisher": publisher,
        "url": f"u:{title}",
        "published": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "summary": "",
        "lang": lang,
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(news.languages, "spoken_languages", lambda: ["en"])
    monkeypatch.setattr(
        news, "company_name",
        lambda sym: {"NRG": "NRG Energy, Inc.", "F": "Ford Motor Company"}.get(sym, ""),
    )
    p = NewsProvider()
    published = {}

    class _Hub:
        def publish(self, topic, value):
            published[topic] = value

        def publish_error(self, topic, err):
            published[topic] = err

    monkeypatch.setattr(news.DataHub, "instance", staticmethod(lambda: _Hub()))
    for src in ("_from_rss_symbol", "_from_finnhub_company", "_from_gnews",
                "_from_yfinance", "_from_newsapi", "_from_rss_query",
                "_from_gnews_query", "_from_finnhub_general"):
        monkeypatch.setattr(p, src, lambda *a, **k: None)
    return p, published


def _titles(published, topic):
    return [i["title"] for i in published[topic]]


# -- symbol news -------------------------------------------------------------

def test_symbol_sources_are_merged_newest_first(provider, monkeypatch):
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [_item("NRG Energy B", 5), _item("NRG Energy D", 30)])
    monkeypatch.setattr(p, "_from_finnhub_company", lambda *a, **k: [_item("NRG Energy A", 0.2)])
    monkeypatch.setattr(p, "_from_rss_symbol", lambda *a, **k: [_item("NRG Energy C", 12)])
    p._fetch("news:NRG", "NRG")
    assert _titles(published, "news:NRG") == ["NRG Energy A", "NRG Energy B", "NRG Energy C", "NRG Energy D"]


def test_off_topic_items_are_dropped_and_duplicates_merged(provider, monkeypatch):
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [_item("NRG Energy one", 1), _item("Tom Cruise stunt", 0.5)])
    monkeypatch.setattr(p, "_from_finnhub_company", lambda *a, **k: [_item("NRG Energy One", 2)])
    p._fetch("news:NRG", "NRG")
    assert [t.lower() for t in _titles(published, "news:NRG")] == ["nrg energy one"]


def test_stale_items_give_way_to_fresh_ones(provider, monkeypatch):
    p, published = provider
    fresh = [_item(f"NRG Energy fresh {i}", i + 1) for i in range(news.MIN_FRESH)]
    stale = [_item("NRG Energy two weeks old", 24 * 14)]
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: fresh + stale)
    p._fetch("news:NRG", "NRG")
    assert "NRG Energy two weeks old" not in _titles(published, "news:NRG")


def test_a_quiet_name_still_shows_its_last_month(provider, monkeypatch):
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [_item("NRG Energy two weeks old", 24 * 14)])
    p._fetch("news:NRG", "NRG")
    assert _titles(published, "news:NRG") == ["NRG Energy two weeks old"]


def test_ancient_headlines_are_never_shown(provider, monkeypatch):
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [_item("NRG Energy in 2017", 24 * 3000)])
    p._fetch("news:NRG", "NRG")
    assert published["news:NRG"] == []


def test_newsapi_only_tops_up_a_thin_result(provider, monkeypatch):
    p, published = provider
    called = []
    plenty = [_item(f"NRG Energy {i}", i + 1) for i in range(news.MIN_FRESH)]
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: plenty)
    monkeypatch.setattr(p, "_from_newsapi", lambda *a, **k: called.append(1) or [])
    p._fetch("news:NRG", "NRG")
    assert called == []


def test_short_tickers_search_by_company_name():
    assert news.symbol_search_query("F", "Ford Motor Company") == '"Ford Motor" stock'
    assert news.symbol_search_query("NVDA", "NVIDIA Corporation") == '("NVIDIA" OR "NVDA") stock'
    assert news.symbol_search_query("GC=F", "Gold Dec 26") == '"Gold" (price OR futures OR market)'
    assert news.symbol_search_query(
        "PETR4.SA", "Petróleo Brasileiro S.A. - Petrobras"
    ) == '("Petróleo Brasileiro" OR "Petrobras" OR "PETR4") stock'
    assert news.symbol_search_query("XYZQ", "") == '"XYZQ" stock'


def test_a_busy_day_fills_the_panel(provider, monkeypatch):
    """25 headlines covered only the last 24 minutes of "markets"."""
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews_query", lambda *a, **k: [_item(f"Story {i}", i * 0.1) for i in range(100)])
    p._fetch_query("newsq:markets", "markets")
    assert len(published["newsq:markets"]) == 75


# -- topic news --------------------------------------------------------------

def test_topic_sources_are_merged_newest_first(provider, monkeypatch):
    p, published = provider
    rss = [_item(f"Brazil rss {i}", 3 + i) for i in range(6)]
    monkeypatch.setattr(p, "_from_rss_query", lambda *a, **k: rss)
    monkeypatch.setattr(p, "_from_gnews_query", lambda *a, **k: [_item("Brazil gnews", 0.1)])
    p._fetch_query("newsq:Brazil", "Brazil")
    titles = _titles(published, "newsq:Brazil")
    assert titles[0] == "Brazil gnews"
    assert len(titles) == 7


def test_topic_newsapi_is_not_consulted_when_there_is_enough(provider, monkeypatch):
    p, published = provider
    called = []
    monkeypatch.setattr(p, "_from_gnews_query", lambda *a, **k: [_item(f"AI {i}", i) for i in range(10)])
    monkeypatch.setattr(p, "_from_newsapi", lambda *a, **k: called.append(1) or [_item("old junk", 25)])
    p._fetch_query("newsq:AI", "AI")
    assert called == []
    assert "old junk" not in _titles(published, "newsq:AI")


def test_rss_topic_match_is_whole_word(monkeypatch):
    pool = [
        {"title": "Fed official said rates stay", "category": "markets", "published": "", "lang": "en"},
        {"title": "AI chip demand lifts Nvidia", "category": "markets", "published": "", "lang": "en"},
        {"title": "Oil prices tumble on Iran talks", "category": "energy", "published": "", "lang": "en"},
    ]
    monkeypatch.setattr(news, "_rss_items", lambda langs=None: pool)
    p = NewsProvider()
    assert [i["title"] for i in p._from_rss_query("ai", ["en"])] == ["AI chip demand lifts Nvidia"]
    assert [i["title"] for i in p._from_rss_query("oil price", ["en"])] == ["Oil prices tumble on Iran talks"]
    assert len(p._from_rss_query("markets", ["en"])) == 2  # a category name pulls its feeds


def test_quote_pages_are_not_news(provider, monkeypatch):
    """Google News indexes Yahoo's quote and option-chain pages."""
    p, published = provider
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [
        _item("NRG Nov 2026 120.000 call (NRG261106C00120000) Stock Price, News, Quote & History", 1),
        _item("NRG Jan 2029 120.000 call (NRG290119C00120000) Interactive Stock Chart", 2),
        _item("NRG Energy, Inc. (NRG) Stock Historical Prices & Data", 3),
        _item("NRG Energy beats on earnings", 4),
    ])
    p._fetch("news:NRG", "NRG")
    assert _titles(published, "news:NRG") == ["NRG Energy beats on earnings"]


def test_live_google_search_asks_for_today_and_the_week(monkeypatch):
    """`when:7d` alone ranks by relevance across the week, leaving gaps of
    days at the top; `when:1d` alone is empty for quiet topics."""
    asked = []
    monkeypatch.setattr(news, "_google_news_rss", lambda q, code, period: asked.append(period) or [])
    NewsProvider()._from_gnews_query("Brazil", ["en"])
    assert sorted(asked) == ["1d", "7d"]


def test_gnews_titles_lose_their_publisher_suffix():
    assert news._strip_publisher("Nvidia rebounds - Barron's", "Barron's") == "Nvidia rebounds"
    assert news._strip_publisher("A - B test", "Reuters") == "A - B test"
