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


def test_a_ticker_glued_to_another_symbol_is_not_a_match():
    """`"F" stock` used to return Yahoo quote pages for KC=F and 639.F."""
    for title in (
        "Coffee Dec 26 (KC=F) Stock Price, News, Quote & History",
        "Spotify Technology S.A. (639.F) Stock Price, News, Quote & History",
        "Barings Europe Select J EUR Inc (0P0001FAV9.F) stock price",
    ):
        assert not is_relevant(_item(title), "F", "Ford Motor Company")
    assert is_relevant(_item("Ford Motor Company (F) is attracting attention"), "F", "Ford Motor Company")


def test_a_dash_separated_alias_counts_as_a_name():
    """Yahoo names Petrobras "Petróleo Brasileiro S.A. - Petrobras"."""
    name = "Petróleo Brasileiro S.A. - Petrobras"
    assert news.name_aliases(name) == ["Petróleo Brasileiro", "Petrobras"]
    assert is_relevant(_item("Petrobras makes third oil discovery"), "PETR4.SA", name)
    assert is_relevant(_item("Petróleo Brasileiro output rises"), "PETR4.SA", name)
