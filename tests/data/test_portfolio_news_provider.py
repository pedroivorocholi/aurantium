"""The newsp: topic: a book's headlines in three layers, scored and gated."""

from datetime import datetime, timedelta, timezone

import pytest

from aurantium.portfolio_news import MACRO_QUERIES, encode_book
from aurantium.providers import news
from aurantium.providers.news import NewsProvider

NOW = datetime.now(timezone.utc)


def _item(title, hours_ago=1.0, lang="en", url=None):
    return {
        "title": title, "publisher": "X", "url": url or f"u:{title}",
        "published": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "summary": "", "lang": lang,
    }


def H(sym, w, sector=None):
    return {"symbol": sym, "weight": w, "sector": sector}


@pytest.fixture
def prov(monkeypatch):
    monkeypatch.setattr(news.languages, "spoken_languages", lambda: ["en"])
    monkeypatch.setattr(news, "company_name", lambda sym: "")
    monkeypatch.setattr(news, "_macro_cache", {})
    p = NewsProvider()
    out = {}

    class _Hub:
        def publish(self, topic, value):
            out[topic] = value

        def publish_error(self, topic, err):
            out[topic] = err

    monkeypatch.setattr(news.DataHub, "instance", staticmethod(lambda: _Hub()))
    for src in ("_from_rss_symbol", "_from_finnhub_company", "_from_gnews",
                "_from_yfinance", "_from_newsapi", "_from_rss_query",
                "_from_gnews_query", "_from_finnhub_general"):
        monkeypatch.setattr(p, src, lambda *a, **k: None)
    return p, out


def _run(p, out, book):
    topic = "newsp:" + encode_book(book)
    p._fetch_portfolio(topic, encode_book(book))
    return out[topic]


def test_empty_book_publishes_empty(prov):
    p, out = prov
    assert _run(p, out, []) == []


def test_newsapi_never_called(prov, monkeypatch):
    p, out = prov

    def boom(*a, **k):
        raise AssertionError("NewsAPI called")

    monkeypatch.setattr(p, "_from_newsapi", boom)
    monkeypatch.setattr(p, "_from_gnews", lambda *a, **k: [])
    monkeypatch.setattr(p, "_from_rss_query", lambda *a, **k: [_item("Fed holds rates")])
    rows = _run(p, out, [H("NVDA", 1.0, "Technology")])
    assert isinstance(rows, list) and rows


def test_holdings_capped_at_ten(prov, monkeypatch):
    p, out = prov
    seen = []
    monkeypatch.setattr(p, "_symbol_items", lambda s, l, newsapi=True: seen.append((s, newsapi)) or [])
    book = [H(f"S{i}", 0.01 * (i + 1)) for i in range(12)]
    _run(p, out, book)
    assert sorted(s for s, _ in seen) == sorted(f"S{i}" for i in range(2, 12))
    assert all(na is False for _, na in seen)


def test_duplicate_headline_keeps_best_layer(prov, monkeypatch):
    p, out = prov
    dup = _item("Chips rally on Fed", 1, url="u:dup")
    monkeypatch.setattr(p, "_symbol_items", lambda s, l, newsapi=True: [dict(dup)])
    monkeypatch.setattr(p, "_from_rss_query", lambda q, l: [dict(dup)])
    rows = _run(p, out, [H("NVDA", 0.5, "Technology")])
    hits = [r for r in rows if r["url"] == "u:dup"]
    assert len(hits) == 1
    assert hits[0]["reason"] == "NVDA" and hits[0]["symbol"] == "NVDA"


def test_items_carry_reason_and_score(prov, monkeypatch):
    p, out = prov
    monkeypatch.setattr(p, "_symbol_items", lambda s, l, newsapi=True: [_item("NVDA up")])
    monkeypatch.setattr(p, "_from_rss_query", lambda q, l: [_item(f"story {q}")])
    rows = _run(p, out, [H("NVDA", 0.5, "Technology")])
    by = {r["reason"]: r for r in rows}
    assert "NVDA" in by and "Tech 50%" in by and "Macro" in by
    assert all(isinstance(r["score"], float) for r in rows)
    assert by["NVDA"]["symbol"] == "NVDA"
    assert "symbol" not in by["Macro"] and "symbol" not in by["Tech 50%"]
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_language_gate_applies(prov, monkeypatch):
    p, out = prov
    monkeypatch.setattr(p, "_symbol_items", lambda s, l, newsapi=True: [
        _item("NVDA up"), _item("NVDA sobe forte", lang="pt")])
    rows = _run(p, out, [H("NVDA", 1.0)])
    assert [r["title"] for r in rows] == ["NVDA up"]


def test_light_and_unknown_sectors_get_no_exposure_query(prov, monkeypatch):
    p, out = prov
    queries = []
    monkeypatch.setattr(p, "_from_rss_query", lambda q, l: queries.append(q) or [])
    monkeypatch.setattr(p, "_symbol_items", lambda s, l, newsapi=True: [_item(f"{s} news")])
    book = [H("AAA", 0.80, "Technology"), H("BBB", 0.05, "Energy"),
            H("CCC", 0.08, None), H("DDD", 0.07, "Spaceflight")]
    rows = _run(p, out, book)
    assert "tech stocks" in queries
    assert "energy stocks" not in queries          # below SECTOR_MIN_WEIGHT
    assert not any(q for q in queries if "Spaceflight" in q)
    # holdings with no usable sector still get holdings-layer news
    assert {"BBB", "CCC", "DDD"} <= {r["reason"] for r in rows}


def test_macro_boost_ranks_oil_above_generic(prov, monkeypatch):
    p, out = prov
    oil_q = dict((t, q) for t, q in MACRO_QUERIES)["oil"]
    jobs_q = dict((t, q) for t, q in MACRO_QUERIES)["dollar"]
    feeds = {oil_q: [_item("Oil jumps", 3)], jobs_q: [_item("Dollar slips", 3)]}
    monkeypatch.setattr(p, "_from_rss_query", lambda q, l: feeds.get(q))
    rows = _run(p, out, [H("XOM", 1.0, "Energy")])
    titles = [r["title"] for r in rows]
    assert titles.index("Oil jumps") < titles.index("Dollar slips")


def test_macro_cached_across_books(prov, monkeypatch):
    p, out = prov
    calls = []
    monkeypatch.setattr(p, "_from_rss_query", lambda q, l: calls.append(q) or [])
    _run(p, out, [H("A", 1.0)])
    n = len(calls)
    _run(p, out, [H("B", 1.0)])
    assert n == len(MACRO_QUERIES) and len(calls) == n
