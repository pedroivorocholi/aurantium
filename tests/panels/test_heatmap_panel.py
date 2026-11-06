"""The Heatmap panel: six universes drawn as a size-weighted treemap.

Data never comes from the network here: payloads are published straight onto a
fresh hub (the ``svc`` fixture), exactly as the providers would publish them.
"""

import importlib

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget
from shiboken6 import isValid

from aurantium.datahub import DataHub
from aurantium.heatmap_model import Tile
from aurantium.heatmap_universes import INDEXES, SECTORS
from aurantium.panel import UNLINKED
from aurantium.panels.watchlist import WatchlistPanel
from aurantium.portfolio_store import PortfolioStore
from aurantium.providers.screen import symbols_topic
from aurantium.symbol_context import SymbolContext

#: the exact empty-state wording the spec fixes for a group with no Watchlist
NO_WATCHLIST = "No Watchlist in group {g} — open one, or link this panel to a group that has one"


def _mod():
    # looked up per test: the ramp tests reload this module under each theme
    return importlib.import_module("aurantium.panels.sector_heatmap")


@pytest.fixture
def make(svc, qapp):
    WatchlistPanel._instances = []  # earlier tests may leave panels behind
    PortfolioStore.reset_instance(PortfolioStore(QSettings()))
    made = []

    def _make(cls=None, group="A", view=None, size=(640, 420)):
        p = (cls or _mod().HeatmapPanel)()
        p.build()
        p.set_link_group(group)
        if view is not None:
            p.restore({"view": view})
        p.resize(*size)
        p.show()
        qapp.processEvents()
        made.append(p)
        return p

    yield _make
    for p in made:
        if isValid(p):
            p.close()
            p.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    PortfolioStore.reset_instance(None)


def _flush():
    """Let the panel's coalesced redraw (a 0 ms single-shot) run."""
    QTest.qWait(0)
    QCoreApplication.processEvents()


def _publish(topic, payload):
    DataHub.instance().publish(topic, payload)
    _flush()


def _publish_error(topic, message):
    DataHub.instance().publish_error(topic, message)
    _flush()


def _quote(pct, price=100.0, **kw):
    return {"price": price, "change_pct": pct, **kw}


def _row(sym, cap, pct=0.5):
    return {"symbol": sym, "name": sym, "market_cap": cap, "price": 10.0,
            "change_pct": pct, "currency": "USD"}


def _tiles(p):
    _flush()
    return p.map.tiles()


def _syms(p):
    return [t.symbol for t in _tiles(p)]


def _empty(p):
    _flush()
    return p.empty_text()


def _centre(p, symbol):
    _flush()
    return p.map.tile_rect(symbol).center()


# -- the default ------------------------------------------------------------


def test_default_view_is_sectors(make):
    p = make()
    assert p.view == "sectors"
    assert sorted(_syms(p)) == sorted(etf for _k, _l, etf in SECTORS)
    for _k, _l, etf in SECTORS:
        assert f"quote:{etf}" in p._topics
    assert "sectorcap:all" in p._topics
    # skeletons until a reading lands, then coloured
    assert all(t.loading for t in _tiles(p))
    _publish("quote:XLK", _quote(1.5))
    xlk = next(t for t in _tiles(p) if t.symbol == "XLK")
    assert xlk.change_pct == 1.5 and not xlk.loading
    assert p.crumb.text() == "Sectors"
    assert not p.back_btn.isVisibleTo(p)


def test_sector_caps_size_the_tiles(make):
    p = make()
    caps = {key: float(i + 1) * 1e12 for i, (key, _l, _e) in enumerate(SECTORS)}
    _publish("sectorcap:all", caps)
    biggest = max(caps, key=caps.get)
    etf = next(e for k, _l, e in SECTORS if k == biggest)
    assert _syms(p)[0] == etf  # largest first, so it takes the top-left
    r = p.map.tile_rect(etf)
    assert r.width() * r.height() == max(
        p.map.tile_rect(e).width() * p.map.tile_rect(e).height() for _k, _l, e in SECTORS
    )


# -- click and drill --------------------------------------------------------


def test_click_sends_symbol(make):
    p = make()
    _publish("quote:XLE", _quote(-0.7))
    QTest.mouseClick(p.map, Qt.MouseButton.LeftButton, pos=_centre(p, "XLE"))
    assert SymbolContext.instance().symbol("A") == "XLE"
    assert p.current_symbol == "XLE"


def test_double_click_drills_and_back_returns(make):
    p = make()
    QTest.mouseDClick(p.map, Qt.MouseButton.LeftButton, pos=_centre(p, "XLK"))
    assert p.view == "sector:Technology"
    assert p.crumb.text().startswith("Sectors ›")
    assert "Technology" in p.crumb.text()
    assert p.back_btn.isVisibleTo(p)
    assert "screen:sector:Technology:30" in p._topics
    assert "quote:XLK" not in p._topics  # the sectors topics were let go
    rows = [_row(f"T{i}", 1e9 * (40 - i)) for i in range(35)]
    _publish("screen:sector:Technology:30", {"rows": rows, "as_of": "x"})
    assert len(_tiles(p)) == 30
    QTest.mouseClick(p.back_btn, Qt.MouseButton.LeftButton)
    assert p.view == "sectors"
    assert not p.back_btn.isVisibleTo(p)
    assert "screen:sector:Technology:30" not in p._topics
    assert "quote:XLK" in p._topics


def test_double_click_outside_sectors_does_not_drill(make):
    p = make(view="index:dow")
    members = INDEXES["dow"][2]
    _publish(symbols_topic(members), {"rows": [_row(s, 1e9) for s in members], "as_of": "x"})
    QTest.mouseDClick(p.map, Qt.MouseButton.LeftButton, pos=_centre(p, members[0]))
    assert p.view == "index:dow"


def test_backspace_goes_back(make, qapp):
    """Backspace reaches the map that was clicked, not whatever had focus
    before: a Watchlist's table deletes its selected rows on Backspace."""
    host = QWidget()
    row = QHBoxLayout(host)
    w = _watchlist("A", ["AAPL", "MSFT", "SPY"])
    p = make()
    row.addWidget(w)
    row.addWidget(p)
    host.resize(1100, 460)
    host.show()
    host.activateWindow()
    qapp.processEvents()
    w.table.setFocus()
    w.table.selectRow(0)
    qapp.processEvents()
    assert QApplication.focusWidget() is w.table

    centre = _centre(p, "XLK")
    QTest.mouseClick(p.map, Qt.MouseButton.LeftButton, pos=centre)
    QTest.mouseDClick(p.map, Qt.MouseButton.LeftButton, pos=centre)
    assert p.view == "sector:Technology"
    assert QApplication.focusWidget() is p.map

    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Backspace)
    assert p.view == "sectors"
    assert sorted(w.symbols()) == ["AAPL", "MSFT", "SPY"]  # the Watchlist kept its rows
    # nowhere further back to go: a second Backspace is a no-op, not an error
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Backspace)
    assert p.view == "sectors"
    assert sorted(w.symbols()) == ["AAPL", "MSFT", "SPY"]
    row.removeWidget(w)
    w.setParent(None)
    w.close()
    w.deleteLater()
    host.deleteLater()


def test_the_map_takes_focus_on_click(make):
    # so a key the map ignores bubbles to this panel, never to another one
    p = make()
    assert p.map.focusPolicy() == Qt.FocusPolicy.ClickFocus


# -- index views ------------------------------------------------------------


def test_index_view_shows_the_largest_n(make):
    p = make(view="index:sp500")
    label, n, members = INDEXES["sp500"]
    topic = symbols_topic(members)
    assert topic in p._topics
    rows = [_row(s, 1e9 * (600 - i)) for i, s in enumerate(members[:120])]
    _publish(topic, {"rows": rows, "as_of": "x"})
    assert len(_tiles(p)) == n == 40
    assert p.crumb.text() == f"{label} · largest {n}"


def test_error_keeps_the_last_good_tiles(make):
    p = make(view="index:dow")
    members = INDEXES["dow"][2]
    topic = symbols_topic(members)
    _publish(topic, {"rows": [_row(s, 1e9) for s in members], "as_of": "x"})
    before = _syms(p)
    _publish_error(topic, "Yahoo rate limit — retrying shortly")
    assert _syms(p) == before  # never blank the map
    assert "rate limit" in p.note.text()
    _publish(topic, {"rows": [_row(s, 1e9) for s in members], "as_of": "y"})
    assert p.note.text() == ""  # good data clears the message


def test_error_before_any_data_shows_in_the_map(make):
    p = make(view="sector:Energy")
    _publish_error("screen:sector:Energy:30", "Yahoo rate limit")
    assert "Yahoo rate limit" in _empty(p)
    assert p.note.text() == ""  # said once, not twice


# -- watchlist --------------------------------------------------------------


def _watchlist(group, symbols):
    w = WatchlistPanel()
    w.build()
    w.set_link_group(group)
    w.restore({"symbols": symbols})
    return w


def test_watchlist_view_follows_group(make):
    w = _watchlist("A", ["AAPL", "SPY"])
    p = make(view="watchlist")
    assert sorted(_syms(p)) == ["AAPL", "SPY"]
    assert symbols_topic(["AAPL", "SPY"]) in p._topics
    assert "quote:SPY" in p._topics

    w.restore({"symbols": ["MSFT"]})  # the list changed: follow it
    assert _syms(p) == ["MSFT"]
    assert "quote:AAPL" not in p._topics

    p.set_link_group("B")  # nothing in B
    assert _empty(p) == NO_WATCHLIST.format(g="B")
    assert not p._topics

    other = _watchlist("B", ["NVDA"])  # opened later: picked up on the recheck
    p._watch_timer.timeout.emit()
    assert _syms(p) == ["NVDA"]

    w.set_link_group("B")  # the first-opened Watchlist in the group wins
    p._watch_timer.timeout.emit()
    assert _syms(p) == ["MSFT"]
    other.restore({"symbols": ["AMD"]})  # the old one no longer drives the map
    assert _syms(p) == ["MSFT"]
    for x in (w, other):
        x.close()
        x.deleteLater()


def test_mixed_tiles_keep_their_size_while_a_new_list_loads(make):
    """Adding a symbol changes the screen topic; until it publishes, the
    tiles already on the map keep their market-cap weight instead of all
    collapsing to the median."""
    w = _watchlist("A", ["AAPL", "MSFT"])
    p = make(view="watchlist")
    _publish(symbols_topic(["AAPL", "MSFT"]),
             {"rows": [_row("AAPL", 3e12), _row("MSFT", 1e12)], "as_of": "x"})
    before = {t.symbol: t.weight for t in _tiles(p)}
    assert before == {"AAPL": 3e12, "MSFT": 1e12}
    w.restore({"symbols": ["AAPL", "MSFT", "NVDA"]})
    after = {t.symbol: t.weight for t in _tiles(p)}
    assert after["AAPL"] == 3e12 and after["MSFT"] == 1e12
    assert symbols_topic(["AAPL", "MSFT"]) not in p._screens  # pruned with its subscription
    w.close()
    w.deleteLater()


def test_watchlist_empty_state_text(make):
    p = make(view="watchlist")
    assert _tiles(p) == []
    assert _empty(p) == NO_WATCHLIST.format(g="A")
    p.set_link_group(UNLINKED)
    assert _empty(p) == "This panel is unlinked — link it to a group that has a Watchlist"


def test_watchlist_symbol_outside_the_screener_is_quote_coloured(make):
    w = _watchlist("A", ["AAPL", "BTC-USD"])
    p = make(view="watchlist")
    _publish(symbols_topic(["AAPL", "BTC-USD"]), {"rows": [_row("AAPL", 3e12)], "as_of": "x"})
    _publish("quote:BTC-USD", _quote(2.4))
    btc = next(t for t in _tiles(p) if t.symbol == "BTC-USD")
    assert btc.change_pct == 2.4 and not btc.loading
    w.close()
    w.deleteLater()


# -- portfolio --------------------------------------------------------------


def test_portfolio_view_sizes_by_converted_value(make):
    store = PortfolioStore.instance()
    store.add("AAPL", 10, 100.0, "2026-01-02")
    store.add("VOD.L", 100, 1.0, "2026-01-02")  # no currency or rate yet
    p = make(view="portfolio")
    assert {"quote:AAPL", "quote:VOD.L"} <= p._topics
    _publish("quote:AAPL", _quote(1.0, price=200.0))
    _publish("quote:VOD.L", _quote(-0.5, price=80.0, currency="GBp"))
    tiles = {t.symbol: t for t in _tiles(p)}
    assert tiles["AAPL"].weight == 2000.0 and not tiles["AAPL"].loading
    assert tiles["VOD.L"].loading  # the GBP rate has not landed
    assert not p.map.tile_rect("VOD.L").isEmpty()  # still visible, as a skeleton
    store.add("MSFT", 1, 1.0, "2026-01-02")
    assert "quote:MSFT" in p._topics  # a new position joins the map


def test_portfolio_empty_state(make):
    p = make(view="portfolio")
    assert _empty(p).startswith("No open positions")


def test_custom_view_starts_empty(make):
    p = make(view="custom")
    assert _tiles(p) == []
    assert _empty(p).startswith("No tiles yet")


# -- painting ---------------------------------------------------------------


def test_tiny_tiles_have_no_label_but_tooltip(qapp):
    m = _mod()._Map()
    m.resize(300, 200)
    big = Tile("BIG", "BIG", 10_000.0, 1.2, "Big Co (BIG)\nPrice 1.00")
    tiny = Tile("TINY", "TINY", 300.0, -0.3, "Tiny Co (TINY)\nPrice 2.00")
    m.set_tiles([tiny, big])
    assert [t.symbol for t in m.tiles()] == ["BIG", "TINY"]  # sorted, largest first
    assert m.label_lines("TINY") == ()
    assert m.label_lines("BIG")[0] == "BIG"
    assert m.label_lines("BIG")[1] == "+1.20%"
    assert m.tooltip_at(m.tile_rect("TINY").center()) == tiny.tooltip
    assert m.tooltip_at(m.tile_rect("BIG").center()) == big.tooltip
    m.grab()  # paints without raising


def test_loading_tiles_keep_a_minimum_share(qapp):
    m = _mod()._Map()
    m.resize(400, 300)
    m.set_tiles([
        Tile("A", "A", 1000.0, 1.0, "a"),
        Tile("L", "L", 0.0, None, "l", loading=True),
    ])
    assert [t.symbol for t in m.tiles()] == ["A", "L"]  # loading tiles go last
    r = m.tile_rect("L")
    assert r.width() > 0 and r.height() > 0


def test_tile_press_sinks_only_that_tile(qapp):
    from aurantium import press

    m = _mod()._Map()
    m.resize(300, 200)
    m.set_tiles([Tile("A", "A", 3.0, 1.0, "a"), Tile("B", "B", 1.0, -1.0, "b")])
    host, rect, _radius = press.press_region(m, m.tile_rect("B").center())
    assert host is m and rect == m.tile_rect("B")


# -- persistence ------------------------------------------------------------


@pytest.mark.parametrize("view", ["sectors", "sector:Technology", "index:ndx", "portfolio",
                                  "watchlist", "custom"])
def test_view_persists_in_settings(make, view):
    p = make()
    p.set_view(view)
    assert p.settings() == {"view": view, "custom": []}
    q = make(view=p.settings()["view"])
    assert q.view == view
    assert q.back_btn.isVisibleTo(q) == view.startswith("sector:")


@pytest.mark.parametrize("bad", [
    {"view": "bogus"}, {"view": 5}, {"view": None}, {"view": "sector:Nope"},
    {"view": "index:zzz"}, {}, None, "sectors", {"tiles": "XLE"},
])
def test_unknown_view_falls_back_to_sectors(make, bad):
    p = make()
    p.set_view("index:dow")
    p.restore(bad)
    assert p.view == "sectors"


# -- teardown ---------------------------------------------------------------


def test_close_lets_go_of_the_app_wide_signals(make, qapp):
    w = _watchlist("A", ["AAPL"])
    p = make(view="watchlist")
    p.close()
    w.restore({"symbols": ["MSFT"]})  # would re-render a closed panel
    PortfolioStore.instance().add("AAPL", 1, 1.0, "2026-01-02")
    assert _syms(p) == ["AAPL"]
    assert not p._watch_timer.isActive()
    w.close()
    w.deleteLater()
