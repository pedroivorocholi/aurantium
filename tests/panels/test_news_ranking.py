"""The headline table is newest first.

It used to rank any headline naming a linked ticker (case-sensitive) above
everything else, so a two-day-old "AAPL ..." story sat above a ten-minute-old
"Apple ..." one — in Topic News too, for every link group's symbol. The
provider already filters symbol news for relevance; the table only orders it.
"""

from datetime import datetime, timedelta, timezone

from aurantium.panels import _news_common
from aurantium.panels._news_common import format_when, sort_news_items
from aurantium.symbol_context import SymbolContext

NOW = datetime.now(timezone.utc)


def _item(title, hours_ago, lang="en"):
    return {"title": title, "published": (NOW - timedelta(hours=hours_ago)).isoformat(), "lang": lang}


def test_a_linked_ticker_does_not_pin_an_old_headline(monkeypatch):
    monkeypatch.setattr(_news_common, "main_language", lambda: "en")

    class _Linked:  # every link group points at AAPL
        def symbol(self, _group):
            return "AAPL"

    monkeypatch.setattr(SymbolContext, "instance", staticmethod(lambda: _Linked()))
    old = _item("AAPL slips ahead of earnings", 48)
    new = _item("Apple beats on iPhone sales", 0.2)
    assert sort_news_items([old, new]) == [new, old]


def test_main_language_still_leads(monkeypatch):
    monkeypatch.setattr(_news_common, "main_language", lambda: "en")
    pt = _item("Petrobras sobe", 0.1, lang="pt")
    en = _item("Petrobras rises", 3)
    assert sort_news_items([pt, en]) == [en, pt]


def test_last_years_date_shows_its_year():
    dt = datetime(NOW.year - 1, 3, 17, 12, tzinfo=timezone.utc)
    assert format_when(dt).endswith(str(NOW.year - 1)[2:])


def test_scored_items_order_by_score_within_a_tier(monkeypatch):
    # Portfolio rows carry a score (position size, layer, recency already
    # folded in); the table orders them by it, still main language first.
    monkeypatch.setattr(_news_common, "main_language", lambda: "en")
    big = dict(_item("Nvidia guidance", 10), score=5.0)
    fed = dict(_item("Fed holds", 0.1), score=2.0)
    pt = dict(_item("Petrobras sobe", 0.1, lang="pt"), score=9.0)
    assert sort_news_items([fed, pt, big]) == [big, fed, pt]
