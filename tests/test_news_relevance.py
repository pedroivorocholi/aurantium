"""Ticker news must be about the ticker.

NewsAPI was the second tier of the waterfall and won whenever it returned
anything — and its full-text search matches the bare ticker anywhere in an
article. For NRG that was Houston Texans coverage (they play at NRG Stadium),
a Tom Cruise piece and an AI-datacenter story, while Google News had 25
on-topic NRG Energy headlines that were never shown. yfinance mixes in
related-company stories the same way.
"""

import pytest

from aurantium.providers import news
from aurantium.providers.news import NewsProvider, is_relevant, name_core


def _item(title, summary="", publisher="X"):
    return {"title": title, "publisher": publisher, "url": f"u:{title}", "published": "", "summary": summary}


# -- name core --------------------------------------------------------------

@pytest.mark.parametrize(
    "name, core",
    [
        ("NRG Energy, Inc.", "NRG Energy"),
        ("Netflix, Inc.", "Netflix"),
        ("Apple Inc.", "Apple"),
        ("The Walt Disney Company", "Walt Disney"),
        ("Alphabet Inc. Class A", "Alphabet"),
        ("Rigetti Computing, Inc.", "Rigetti Computing"),
        ("Taiwan Semiconductor Manufacturing Company Limited", "Taiwan Semiconductor Manufacturing"),
        ("", ""),
    ],
)
def test_legal_suffixes_are_stripped(name, core):
    assert name_core(name) == core


# -- relevance --------------------------------------------------------------

def test_off_topic_headlines_are_rejected():
    for title in (
        "Tom Cruise: Greatest stunt choreo incoming",
        "Texans fans already waiting for next season?",
        "Houston Texans NFL Power Rankings: Week 2",
    ):
        assert not is_relevant(_item(title), "NRG", "NRG Energy, Inc.")


def test_the_ticker_in_the_headline_counts():
    assert is_relevant(_item("$NRG stock is down 5% today"), "NRG", "NRG Energy, Inc.")
    assert is_relevant(_item("NRG Energy Q2 Earnings Lag Estimates"), "NRG", "")


def test_the_company_name_in_the_headline_counts():
    assert is_relevant(
        _item("Netflix Stock Downgraded To Sell As Viewer Engagement Wanes"),
        "NFLX",
        "Netflix, Inc.",
    )


def test_ticker_match_is_a_whole_word_and_case_sensitive():
    """'nrg' inside 'energy' or a lowercase word must not count."""
    assert not is_relevant(_item("Synergy stocks rally"), "NRG", "")
    assert not is_relevant(_item("the nrg drink craze"), "NRG", "")


def test_a_summary_stadium_mention_does_not_rescue_an_off_topic_headline():
    item = _item("Nico Collins injury update", summary="...practice at NRG Stadium on Friday")
    assert not is_relevant(item, "NRG", "NRG Energy, Inc.")


def test_an_exchange_style_mention_in_the_summary_counts():
    item = _item("Utilities lead the market higher", summary="Shares of NRG Energy (NYSE: NRG) rose 3%")
    assert is_relevant(item, "NRG", "NRG Energy, Inc.")


def test_suffixed_symbols_match_on_their_base():
    assert is_relevant(_item("PETR4 sobe com petróleo"), "PETR4.SA", "")
    assert is_relevant(_item("BRK.B hits a record"), "BRK-B", "")


def test_a_distinctive_word_of_the_name_counts():
    """Headlines say "Disney", not "Walt Disney"; "Rigetti", not "Rigetti
    Computing"."""
    assert is_relevant(_item("Is Disney Stock A Bargain?"), "DIS", "The Walt Disney Company")
    assert is_relevant(_item("Rigetti soars on new chip"), "RGTI", "Rigetti Computing, Inc.")


def test_a_fund_name_word_does_not_count():
    """"State Street SPDR S&P 500 ETF Trust" let "State Penguins" through."""
    name = "State Street SPDR S&P 500 ETF Trust"
    assert not is_relevant(_item("Youngstown State Penguins @ South Dakota State"), "SPY", name)
    assert is_relevant(_item("SPY is down 0.5% today"), "SPY", name)


def test_futures_match_on_the_commodity_not_the_code():
    name = "Gold Dec 26"
    assert name_core(name) == "Gold"
    assert is_relevant(_item("Gold touches $5,400 as demand jumps"), "GC=F", name)
    assert not is_relevant(_item("Screening GC variants in tumor proteins"), "GC=F", name)


def test_generic_name_words_do_not_count():
    assert not is_relevant(_item("Energy stocks slide as oil falls"), "NRG", "NRG Energy, Inc.")


# -- the waterfall ----------------------------------------------------------

@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(news.languages, "spoken_languages", lambda: ["en"])
    monkeypatch.setattr(news, "company_name", lambda sym: {"NRG": "NRG Energy, Inc."}.get(sym, ""))
    p = NewsProvider()
    published = {}

    class _Hub:
        def publish(self, topic, value):
            published[topic] = value

        def publish_error(self, topic, err):
            published[topic] = err

    monkeypatch.setattr(news.DataHub, "instance", staticmethod(lambda: _Hub()))
    return p, published


def test_junk_from_an_early_source_no_longer_masks_a_good_later_one(provider, monkeypatch):
    p, published = provider
    junk = [_item(t) for t in ("Tom Cruise stunt", "Texans fans wait", "Week 3 Ducks", "Creatorverse", "NFL Week 1")]
    good = [_item(f"NRG Energy headline {i}") for i in range(8)]
    monkeypatch.setattr(p, "_from_rss_symbol", lambda s, l=None: None)
    monkeypatch.setattr(p, "_from_newsapi", lambda s, l=None, **k: junk)
    monkeypatch.setattr(p, "_from_gnews", lambda s, l=None: good)
    monkeypatch.setattr(p, "_from_yfinance", lambda s: [])
    p._fetch("news:NRG", "NRG")
    titles = [i["title"] for i in published["news:NRG"]]
    assert titles == [i["title"] for i in good]


def test_sources_are_merged_until_there_is_enough(provider, monkeypatch):
    p, published = provider
    a = [_item("NRG Energy one"), _item("Tom Cruise")]
    b = [_item("NRG Energy one"), _item("NRG Energy two"), _item("NRG Energy three")]
    c = [_item("NRG Energy four"), _item("NRG Energy five"), _item("NRG Energy six")]
    monkeypatch.setattr(p, "_from_rss_symbol", lambda s, l=None: None)
    monkeypatch.setattr(p, "_from_newsapi", lambda s, l=None, **k: a)
    monkeypatch.setattr(p, "_from_gnews", lambda s, l=None: b)
    monkeypatch.setattr(p, "_from_yfinance", lambda s: c)
    p._fetch("news:NRG", "NRG")
    titles = [i["title"] for i in published["news:NRG"]]
    assert titles[:3] == ["NRG Energy one", "NRG Energy two", "NRG Energy three"]
    assert "Tom Cruise" not in titles
    assert titles.count("NRG Energy one") == 1  # de-duplicated


def test_a_source_that_is_already_enough_stops_the_waterfall(provider, monkeypatch):
    p, published = provider
    many = [_item(f"NRG Energy {i}") for i in range(12)]
    called = []
    monkeypatch.setattr(p, "_from_rss_symbol", lambda s, l=None: None)
    monkeypatch.setattr(p, "_from_newsapi", lambda s, l=None, **k: many)
    monkeypatch.setattr(p, "_from_gnews", lambda s, l=None: called.append("gnews") or [])
    monkeypatch.setattr(p, "_from_yfinance", lambda s: called.append("yf") or [])
    p._fetch("news:NRG", "NRG")
    assert called == []
    assert len(published["news:NRG"]) == 12
