"""One News panel, three sources: Linked follows the link group, Topic watches
its own query, Portfolio reads the app-wide book. Pins the retirement of
``topic_news`` (alias + layout migration) and Portfolio mode's rules: only
open positions, an empty book shows nothing, it works with no Portfolio panel
open, and ticker-tagged rows send their symbol to the link group."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from aurantium.portfolio_store import PortfolioStore

NOW = datetime.now(timezone.utc).isoformat()


class _FakeHub:
    """Records subscriptions; tests deliver data through the stored callback."""

    def __init__(self):
        self.subs: dict = {}
        self.cache: dict = {}

    def subscribe(self, owner, topic, callback, on_error=None):
        self.subs[topic] = callback

    def unsubscribe(self, owner, topic):
        self.subs.pop(topic, None)

    def unsubscribe_all(self, owner):
        self.subs.clear()

    def peek(self, topic):
        return self.cache.get(topic)

    def run_async(self, fn):
        pass

    def newsp(self):
        return [t for t in self.subs if t.startswith("newsp:")]


@pytest.fixture
def store(qapp, tmp_path):
    s = PortfolioStore(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))
    PortfolioStore.reset_instance(s)
    yield s
    PortfolioStore.reset_instance(None)


@pytest.fixture
def make_panel(qapp):
    made = []

    def _make():
        from aurantium.panels.news import NewsPanel

        p = NewsPanel()
        p._hub = _FakeHub()
        p.build()
        made.append(p)
        return p

    yield _make
    for p in made:
        p.close()
        p.deleteLater()


def _flush(p):
    """Run the 750 ms store-change debounce now."""
    p._portfolio_debounce.stop()
    p._rebuild_portfolio()


def _rows(p):
    return p.table.rowCount()


def _portfolio_items():
    return [
        {"title": "Nvidia beats estimates", "url": "u-nvda", "published": NOW,
         "summary": "s1", "reason": "NVDA", "score": 3.0, "symbol": "NVDA"},
        {"title": "Fed holds rates", "url": "u-fed", "published": NOW,
         "summary": "s2", "reason": "Macro", "score": 2.0},
        {"title": "Chipmakers rally", "url": "u-sec", "published": NOW,
         "summary": "s3", "reason": "Technology 60%", "score": 1.0},
    ]


def _row_of(p, url):
    from aurantium.panels._news_common import news_url_at

    for r in range(p.table.rowCount()):
        if news_url_at(p.table, r) == url:
            return r
    raise AssertionError(url)


# -- retiring topic_news ------------------------------------------------------


def test_topic_news_alias_resolves(qapp):
    from aurantium.panel import PanelRegistry
    import aurantium.panels.news  # noqa: F401

    assert PanelRegistry.resolve("topic_news") == ("news", {"source": "topic"})
    assert PanelRegistry.get("topic_news") is None


def test_migrate_news_layout(qapp):
    from aurantium.panels.company import migrate_layout
    from aurantium.panels.news import migrate_news_layout

    doc = {"panels": [
        {"instance": "topic_news#1", "panel_id": "topic_news", "link_group": "B",
         "settings": {"query": "Brazil", "read": ["u"]}},
        {"instance": "profile#1", "panel_id": "profile", "link_group": "A",
         "settings": {}},
    ]}
    snapshot = json.loads(json.dumps(doc))
    out = migrate_news_layout(migrate_layout(doc))
    assert doc == snapshot  # input untouched
    news = [s for s in out["panels"] if s["panel_id"] == "news"]
    assert news == [{
        "instance": "topic_news#1", "panel_id": "news", "link_group": "B",
        "settings": {"source": "topic", "query": "Brazil", "read": ["u"]},
    }]
    assert [s["panel_id"] for s in out["panels"]] == ["news", "company"]

    untouched = {"panels": [{"instance": "news#1", "panel_id": "news",
                             "link_group": "A", "settings": {}}]}
    assert migrate_news_layout(untouched) is untouched

    bare = migrate_news_layout({"panels": [
        {"instance": "topic_news#3", "panel_id": "topic_news", "settings": {}}]})
    assert bare["panels"][0]["settings"] == {
        "source": "topic", "query": "markets", "read": []}


def test_default_layout_has_no_topic_news():
    path = Path(__file__).resolve().parents[2] / "layouts" / "default.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    ids = [s["panel_id"] for s in doc["panels"]]
    assert "topic_news" not in ids
    topics = [s for s in doc["panels"]
              if s["panel_id"] == "news" and s["settings"].get("source") == "topic"]
    assert sorted(s["settings"]["query"] for s in topics) == ["Brazil", "energy commodities"]
    # the saved dock arrangement names these instance ids
    assert sorted(s["instance"] for s in topics) == ["topic_news#1", "topic_news#2"]


# -- settings / linking -------------------------------------------------------


def test_old_news_settings_restore_as_linked(make_panel):
    p = make_panel()
    p.restore({"read": ["a"]})
    assert p.settings() == {"source": "linked", "query": "markets", "read": ["a"]}
    p.on_symbol("AAPL")
    assert "news:AAPL" in p._hub.subs


def test_topic_settings_round_trip(make_panel):
    p = make_panel()
    p.restore({"source": "topic", "query": "Brazil", "read": []})
    assert "newsq:Brazil" in p._hub.subs
    assert p.settings()["source"] == "topic"
    assert p.settings()["query"] == "Brazil"


def test_on_symbol_ignored_outside_linked(make_panel):
    p = make_panel()
    p.set_source("topic")
    p.on_symbol("AAPL")
    assert not any(t.startswith("news:") for t in p._hub.subs)
    assert "newsq:markets" in p._hub.subs
    p.set_source("linked")
    assert "news:AAPL" in p._hub.subs
    assert not any(t.startswith("newsq:") for t in p._hub.subs)


def test_query_row_only_in_topic(make_panel):
    p = make_panel()
    assert p._query_box.isHidden()
    p.set_source("topic")
    assert not p._query_box.isHidden()
    p.set_source("portfolio")
    assert p._query_box.isHidden()


# -- portfolio mode -----------------------------------------------------------


def test_portfolio_empty_book_shows_empty_state(store, make_panel):
    p = make_panel()
    p.set_source("portfolio")
    assert p._hub.newsp() == []
    assert _rows(p) == 0
    assert p._news_empty_title == "Your portfolio is empty"


def test_portfolio_all_sold_clears(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    [topic] = p._hub.newsp()
    p._hub.subs[topic](_portfolio_items())
    assert _rows(p) == 3
    store.mark_sold([0], {"NVDA": 120.0}, "2026-11-03")
    _flush(p)
    assert _rows(p) == 0
    assert p._hub.newsp() == []
    assert p._news_empty_title == "Your portfolio is empty"


def test_portfolio_resubscribes_on_store_change(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    [first] = p._hub.newsp()
    assert "profile:NVDA" in p._hub.subs
    store.add("XOM", 50, 100.0, "2026-01-02")
    assert p._portfolio_debounce.isActive()
    _flush(p)
    [second] = p._hub.newsp()
    assert second != first
    assert "XOM" in second and "profile:XOM" in p._hub.subs
    # an unchanged book doesn't resubscribe
    _flush(p)
    assert p._hub.newsp() == [second]


def test_portfolio_uses_peeked_quotes_and_sectors(store, make_panel):
    store.add("NVDA", 1, 100.0, "2026-01-02")
    store.add("XOM", 1, 100.0, "2026-01-02")
    p = make_panel()
    p._hub.cache["quote:NVDA"] = {"price": 300.0}
    p.set_source("portfolio")
    [topic] = p._hub.newsp()
    book = json.loads(topic[len("newsp:"):])
    assert book[0]["symbol"] == "NVDA" and book[0]["weight"] == 0.75
    p._hub.subs["profile:XOM"]({"sector": "Energy"})
    _flush(p)
    [topic] = p._hub.newsp()
    assert {h["symbol"]: h["sector"] for h in json.loads(topic[6:])}["XOM"] == "Energy"


def test_portfolio_works_without_portfolio_panel(store, make_panel):
    from aurantium.panels.portfolio import PortfolioPanel

    assert not [w for w in __import__("PySide6.QtWidgets").QtWidgets.QApplication.allWidgets()
                if isinstance(w, PortfolioPanel)]
    p = make_panel()
    p.set_source("portfolio")
    assert p._hub.newsp() == []
    store.add("AAPL", 5, 150.0, "2026-01-02")
    _flush(p)
    assert len(p._hub.newsp()) == 1


def test_portfolio_shows_why_column_and_status(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    p._hub.subs[p._hub.newsp()[0]](_portfolio_items())
    assert p.table.columnCount() == 3
    assert p.table.horizontalHeaderItem(1).text() == "Why"
    assert p.table.item(0, 1).text() == "NVDA"  # highest score first
    assert p._status_lbl.text() == "portfolio · 3 headlines"
    p.set_source("topic")
    p._hub.subs["newsq:markets"]([{"title": "x", "url": "u", "published": NOW}])
    assert p.table.columnCount() == 2


def test_ticker_row_click_sends_symbol(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    p._hub.subs[p._hub.newsp()[0]](_portfolio_items())
    sent = []
    p.set_symbol = sent.append
    p._on_cell_clicked(_row_of(p, "u-nvda"), 2)
    assert sent == ["NVDA"]
    assert "u-nvda" in p._read_urls


def test_macro_row_click_sends_nothing(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    p._hub.subs[p._hub.newsp()[0]](_portfolio_items())
    sent = []
    p.set_symbol = sent.append
    p._on_cell_clicked(_row_of(p, "u-fed"), 2)
    p._on_cell_clicked(_row_of(p, "u-sec"), 2)
    assert sent == []
    assert {"u-fed", "u-sec"} <= p._read_urls


def test_filter_matches_reason(store, make_panel):
    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = make_panel()
    p.set_source("portfolio")
    p._hub.subs[p._hub.newsp()[0]](_portfolio_items())
    p._news_filter.setText("Macro")
    visible = [r for r in range(p.table.rowCount()) if not p.table.isRowHidden(r)]
    assert visible == [_row_of(p, "u-fed")]


# -- review fixes ---------------------------------------------------------------


class _ErrHub(_FakeHub):
    """Also records on_error so a test can fail a subscription."""

    def __init__(self):
        super().__init__()
        self.errs: dict = {}

    def subscribe(self, owner, topic, callback, on_error=None):
        super().subscribe(owner, topic, callback, on_error)
        self.errs[topic] = on_error


def test_profile_error_is_not_shown(store, qapp):
    from aurantium.panels.news import NewsPanel

    store.add("NVDA", 10, 100.0, "2026-01-02")
    p = NewsPanel()
    p._hub = _ErrHub()
    p.build()
    p.set_source("portfolio")
    on_error = p._hub.errs["profile:NVDA"]
    if on_error is None:
        on_error = p._show_error  # what Panel.subscribe would fall back to
    before = p._state_lbl.text()  # "Loading…": newsp: is still in flight
    on_error("profile fetch failed")
    assert p._state_lbl.text() == before
    assert "⚠" not in p._state_lbl.text() and "failed" not in p._state_lbl.text()
    p.close()
    p.deleteLater()


def test_header_symbol_blank_outside_linked(make_panel):
    p = make_panel()
    p._apply_symbol("AAPL")
    assert p._symbol_lbl.text() == "AAPL"
    p.set_source("portfolio")
    assert p._symbol_lbl.text() == ""
    p._apply_symbol("MSFT")  # remembered, not shown
    assert p._symbol_lbl.text() == ""
    p.set_source("topic")
    assert p._symbol_lbl.text() == ""
    p.set_source("linked")
    assert p._symbol_lbl.text() == "MSFT"
    assert "news:MSFT" in p._hub.subs
    p._apply_symbol("NVDA")  # while linked, the header follows at once
    assert p._symbol_lbl.text() == "NVDA"


def test_closed_or_switched_panel_ignores_store_changes(store, qapp, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent

    from aurantium.panels.news import NewsPanel

    store.add("NVDA", 10, 100.0, "2026-01-02")
    closed = NewsPanel()
    closed._hub = _FakeHub()
    closed.build()
    closed.set_source("portfolio")
    closed.close()
    closed.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    switched = NewsPanel()
    switched._hub = _FakeHub()
    switched.build()
    switched.set_source("portfolio")
    switched.set_source("linked")

    calls = []
    monkeypatch.setattr(NewsPanel, "_schedule_portfolio", lambda self: calls.append("s"))
    monkeypatch.setattr(NewsPanel, "_rebuild_portfolio", lambda self: calls.append("r"))
    store.add("XOM", 5, 100.0, "2026-01-02")
    qapp.processEvents()
    assert calls == []
    assert not switched._portfolio_debounce.isActive()
    assert switched._hub.newsp() == []
    switched.close()
    switched.deleteLater()
