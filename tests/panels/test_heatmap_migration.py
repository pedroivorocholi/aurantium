"""The Heatmap's Custom list: in-place editing, and the Sector Heatmap layouts
it inherits.

Old Sector Heatmap layouts saved ``{"tiles": [[label, symbol], ...]}``. The
eleven default tiles become the Sectors view; anything the user had edited
becomes the Custom view, so nobody's list is lost. Dialogs are never run here:
the picker and the list editor are monkeypatched where the panel imports them,
and context menus are built, not exec'd.
"""

import importlib

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from aurantium.components.symbol_catalog import CatalogEntry
from aurantium.panels.watchlist import WatchlistPanel
from aurantium.portfolio_store import PortfolioStore
from aurantium.undo import UndoStack

#: the old panel's DEFAULT_TILES, verbatim (git show 2b2e1f1)
OLD_DEFAULT_TILES = [
    ["Technology", "XLK"],
    ["Financials", "XLF"],
    ["Health Care", "XLV"],
    ["Cons Discretionary", "XLY"],
    ["Cons Staples", "XLP"],
    ["Energy", "XLE"],
    ["Industrials", "XLI"],
    ["Materials", "XLB"],
    ["Real Estate", "XLRE"],
    ["Utilities", "XLU"],
    ["Comm Services", "XLC"],
]

SIX = [["Apple", "AAPL"], ["Microsoft", "MSFT"], ["Nvidia", "NVDA"],
       ["Gold", "GC=F"], ["Euro", "EURUSD=X"], ["Technology", "XLK"]]


def _mod():
    return importlib.import_module("aurantium.panels.sector_heatmap")


@pytest.fixture
def make(svc, qapp):
    WatchlistPanel._instances = []
    PortfolioStore.reset_instance(PortfolioStore(QSettings()))
    UndoStack._instance = None
    made = []

    def _make(settings=None, size=(640, 420)):
        p = _mod().HeatmapPanel()
        p.build()
        p.set_link_group("A")
        if settings is not None:
            p.restore(settings)
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
    UndoStack._instance = None


def _flush():
    QTest.qWait(0)
    QCoreApplication.processEvents()


def _custom(p):
    return p.settings()["custom"]


def _centre(p, symbol):
    _flush()
    return p.map.tile_rect(symbol).center()


def _menu(p, pos=None):
    _flush()
    return p._context_menu(pos if pos is not None else QPoint(-5, -5))


def _texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def _act(menu, text):
    for a in menu.actions():
        if a.text().startswith(text):
            return a
    raise AssertionError(f"no {text!r} in {_texts(menu)}")


# -- migration ----------------------------------------------------------------


def test_default_tiles_become_sectors(make):
    shuffled = list(reversed(OLD_DEFAULT_TILES))  # order never mattered
    p = make({"tiles": shuffled})
    assert p.view == "sectors"
    assert p.settings() == {"view": "sectors", "custom": []}


def test_edited_tiles_become_custom(make):
    p = make({"tiles": [["Tech", "xlk"], ["Apple", "AAPL"], ["Gold", "GC=F"]]})
    assert p.view == "custom"
    assert p.settings() == {
        "view": "custom",
        "custom": [["Tech", "XLK"], ["Apple", "AAPL"], ["Gold", "GC=F"]],
    }
    assert "quote:AAPL" in p._topics and "quote:GC=F" in p._topics


def test_a_default_list_missing_one_sector_is_still_custom(make):
    p = make({"tiles": OLD_DEFAULT_TILES[:-1]})
    assert p.view == "custom"
    assert len(_custom(p)) == 10


def test_custom_labels_are_drawn(make):
    p = make({"view": "custom", "custom": [["Technology", "XLK"], ["AAPL", "AAPL"]]})
    _flush()
    labels = {t.symbol: t.label for t in p.map.tiles()}
    assert labels == {"XLK": "Technology", "AAPL": "AAPL"}


def test_settings_round_trip(make):
    p = make({"view": "custom", "custom": SIX})
    assert p.settings() == {"view": "custom", "custom": SIX}
    q = make(p.settings())
    assert q.settings() == p.settings()
    # the key is there even when the list is empty
    assert make().settings() == {"view": "sectors", "custom": []}
    # another view keeps the Custom list it is not showing
    r = make({"view": "index:dow", "custom": SIX})
    assert r.settings() == {"view": "index:dow", "custom": SIX}


@pytest.mark.parametrize("garbage", [
    None, [], "sectors", 7, {}, {"view": 5}, {"view": "nonsense"},
    {"tiles": "XLK"}, {"tiles": []}, {"tiles": [1, None, ["only"], [None, None]]},
    {"view": "custom", "custom": "AAPL"}, {"view": "custom", "custom": {"a": 1}},
    {"view": None, "custom": None}, {"tiles": None, "view": None},
])
def test_restore_tolerates_garbage(make, garbage):
    p = make(garbage)
    assert p.view in ("sectors", "custom")
    s = p.settings()
    assert s["custom"] == []
    assert s["view"] == ("custom" if isinstance(garbage, dict) and garbage.get("view") == "custom"
                         else "sectors")


def test_restore_cleans_custom_rows(make):
    p = make({"view": "custom", "custom": [
        [None, "msft"], ["", " NVDA "], ["Label", 3], "row", ["Only"], 5,
        ["Apple", "AAPL"], ["Dup", "AAPL"], ["Blank", "  "],
    ]})
    assert _custom(p) == [["MSFT", "MSFT"], ["NVDA", "NVDA"], ["Apple", "AAPL"]]


# -- editing ------------------------------------------------------------------


def test_custom_edit_is_undoable(make, monkeypatch):
    mod = _mod()
    p = make({"view": "custom", "custom": SIX[:3]})
    stack = UndoStack.instance()

    # Remove, from the tile under the pointer
    menu = _menu(p, _centre(p, "MSFT"))
    _act(menu, "Remove").trigger()
    assert [s for _l, s in _custom(p)] == ["AAPL", "NVDA"]
    _flush()
    assert "MSFT" not in [t.symbol for t in p.map.tiles()]
    assert "quote:MSFT" not in p._topics
    assert stack._stack[-1][0] == "Remove MSFT from heatmap"
    assert stack.undo() == "Remove MSFT from heatmap"
    assert [s for _l, s in _custom(p)] == ["AAPL", "MSFT", "NVDA"]
    assert "quote:MSFT" in p._topics

    # Add tile…, appended at the end; the picker offers the broad catalog
    offered = {}

    def picker(_parent, catalog, **_k):
        offered["codes"] = {e.code for e in catalog}
        return CatalogEntry("Gold", "GC=F", "quote", "Metals")

    monkeypatch.setattr(mod, "open_add_picker", picker)
    _act(_menu(p), "Add tile").trigger()
    assert {"GC=F", "XLK", "^GSPC"} <= offered["codes"]
    assert _custom(p)[-1] == ["Gold", "GC=F"]
    assert stack.undo() == "Add GC=F to heatmap"
    assert "GC=F" not in [s for _l, s in _custom(p)]

    # a symbol already on the map is a quiet no-op: no change, no undo entry
    depth = len(stack._stack)
    monkeypatch.setattr(mod, "open_add_picker",
                        lambda *a, **k: CatalogEntry("apple", "aapl", "quote", ""))
    _act(_menu(p), "Add tile").trigger()
    assert [s for _l, s in _custom(p)] == ["AAPL", "MSFT", "NVDA"]
    assert len(stack._stack) == depth

    # a cancelled picker changes nothing
    monkeypatch.setattr(mod, "open_add_picker", lambda *a, **k: None)
    _act(_menu(p), "Add tile").trigger()
    assert len(stack._stack) == depth

    # Edit panel…: the editor's order is the tile order
    seen = {}

    def fake_editor(parent, title, sections):
        seen["rows"] = [list(r) for r in sections[0].rows]
        return {sections[0].key: [["N", "nvda"], ["Apple", "AAPL"]]}

    monkeypatch.setattr(mod, "open_list_editor", fake_editor)
    _act(_menu(p), "Edit panel").trigger()
    assert seen["rows"] == [["Apple", "AAPL"], ["Microsoft", "MSFT"], ["Nvidia", "NVDA"]]
    assert _custom(p) == [["N", "NVDA"], ["Apple", "AAPL"]]
    assert stack.undo() == "Edit heatmap"
    assert _custom(p) == SIX[:3]

    # a cancelled editor changes nothing
    monkeypatch.setattr(mod, "open_list_editor", lambda *a, **k: None)
    _act(_menu(p), "Edit panel").trigger()
    assert _custom(p) == SIX[:3]
    assert len(stack._stack) == depth


def test_custom_menu_offers_remove_only_on_a_tile(make):
    p = make({"view": "custom", "custom": SIX[:3]})
    on_tile = _texts(_menu(p, _centre(p, "AAPL")))
    assert on_tile[0] == 'Remove "Apple"'
    assert "Add tile…" in on_tile and "Edit panel…" in on_tile
    off = _texts(_menu(p))
    assert not any(t.startswith("Remove") for t in off)
    assert "Add tile…" in off and "Edit panel…" in off
    assert "Show in Custom" not in off


def test_empty_custom_offers_add(make, monkeypatch):
    p = make({"view": "custom", "custom": []})
    _flush()
    assert "right-click" in p.empty_text()
    monkeypatch.setattr(_mod(), "open_add_picker",
                        lambda *a, **k: CatalogEntry("Apple", "AAPL", "quote", ""))
    _act(_menu(p, p.map.rect().center()), "Add tile").trigger()
    assert _custom(p) == [["Apple", "AAPL"]]
    _flush()
    assert [t.symbol for t in p.map.tiles()] == ["AAPL"]
    assert p.empty_text() == ""


def test_other_views_offer_only_show_in_custom(make):
    p = make()
    _flush()
    texts = _texts(_menu(p, _centre(p, "XLK")))
    assert "Show in Custom" in texts
    assert not any(t.startswith(("Remove", "Add tile", "Edit panel")) for t in texts)


def test_show_in_custom_copies_the_map_and_is_undoable(make):
    p = make({"view": "sectors", "custom": [["Apple", "AAPL"]]})
    _flush()
    shown = [[t.label, t.symbol] for t in p.map.tiles()]
    assert len(shown) == 11
    _act(_menu(p), "Show in Custom").trigger()
    assert p.view == "custom"
    assert _custom(p) == shown  # in display order, replacing the old list
    _flush()
    assert [t.symbol for t in p.map.tiles()] == [s for _l, s in shown]
    assert UndoStack.instance().undo() == "Show in Custom"
    assert p.view == "sectors"
    assert _custom(p) == [["Apple", "AAPL"]]


def test_the_guide_covers_custom_editing():
    guide = _mod().HeatmapPanel.GUIDE
    text = guide.search_text()
    for phrase in ("Remove", "Add tile…", "Edit panel…", "Show in Custom", "Ctrl+Z"):
        assert phrase in text


# -- focus after a View menu choice ---------------------------------------------


def test_view_menu_choice_focuses_the_map_in_the_active_window(make, qapp):
    p = make()
    p.activateWindow()
    QTest.qWaitForWindowActive(p)
    other = QWidget(p)
    other.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    other.setFocus()
    qapp.processEvents()
    p._view_actions["sector:Technology"].trigger()  # drilling, via the menu
    qapp.processEvents()
    assert p.view == "sector:Technology"
    assert QApplication.focusWidget() is p.map
    QTest.keyClick(p.map, Qt.Key.Key_Backspace)
    assert p.view == "sectors"


def test_view_menu_choice_never_steals_focus_from_another_window(make, qapp):
    p = make()
    p.window().isActiveWindow = lambda: False  # another window has the focus
    calls = []
    p.map.setFocus = lambda *a: calls.append(a)
    p._view_actions["index:dow"].trigger()
    assert p.view == "index:dow"
    assert calls == []
