"""The Panels menu's search finds a panel when the user doesn't know its name.

Ranking is checked against the live registry, so a panel whose keywords drift
or go missing fails here: other names for the same thing ("graph" → Chart),
words that belong to a panel ("debt" → Company › Financials), typos, and the
names of the retired panels that became Company's tabs.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu

from aurantium import panel_search
from aurantium.guide import PanelGuide

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture(scope="module")
def entries():
    from aurantium.panel import PanelRegistry, discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    return panel_search.build_entries(PanelRegistry.all(), PanelRegistry.resolve)


def _top(query, entries):
    hits = panel_search.search(query, entries)
    return hits[0].open_id if hits else None


# -- every panel can be found by more than its name -----------------------------

def test_every_listed_panel_and_tab_declares_keywords():
    from aurantium.panel import PanelRegistry, discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    missing = []
    for meta in PanelRegistry.all():
        g = getattr(meta.cls, "GUIDE", None)
        if not (isinstance(g, PanelGuide) and g.keywords):
            missing.append(meta.id)
        for key, _label, child in getattr(meta.cls, "HOSTED_TABS", ()):
            cg = getattr(child, "GUIDE", None)
            if not (isinstance(cg, PanelGuide) and cg.keywords):
                missing.append(f"{meta.id}:{key}")
    assert not missing, f"panels without search keywords: {missing}"


# -- ranking --------------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    # the name, whole or begun
    ("commodities", "commodities"),
    ("comm", "commodities"),
    ("watchlist", "watchlist"),
    # other names for the same thing
    ("graph", "chart"),
    ("candlestick", "chart"),
    ("forex", "fx"),
    ("currency", "fx"),
    ("bonds", "macro"),
    ("yields", "macro"),
    ("treasury", "macro"),
    ("gainers", "movers"),
    ("top losers", "movers"),
    # words that belong to a panel
    ("oil", "commodities"),
    ("gold", "commodities"),
    ("debt", "fundamentals"),
    ("balance sheet", "fundamentals"),
    ("who owns", "holders"),
    ("13F", "holders"),
    ("calls puts", "options"),
    ("contango", "futures_curve"),
    ("P/E", "key_metrics"),
    ("pe", "key_metrics"),
    # typos
    ("comodity", "commodities"),
    ("wachlist", "watchlist"),
    ("optoins", "options"),
    ("dividnds", "dividends"),
    # the retired panels open Company on their tab
    ("earnings", "earnings"),
    ("financials", "fundamentals"),
    ("key metrics", "key_metrics"),
    ("company profile", "profile"),
])
def test_finds_the_right_panel(entries, query, expected):
    assert _top(query, entries) == expected, panel_search.search(query, entries)[:3]


def test_company_itself_still_comes_first_for_its_name(entries):
    assert _top("company", entries) == "company"


def test_case_and_accents_do_not_matter(entries):
    assert _top("GRÁPH", entries) == "chart"


def test_empty_or_filler_queries_find_nothing(entries):
    assert panel_search.search("", entries) == []
    assert panel_search.search("   ", entries) == []
    assert panel_search.search("show me the", entries) == []


def test_gibberish_finds_nothing_and_has_no_closest(entries):
    assert panel_search.search("xqzvw", entries) == []
    assert panel_search.closest("xqzvw", entries) is None


def test_results_are_capped(entries):
    assert len(panel_search.search("s", entries)) <= panel_search.MAX_RESULTS


def test_tab_entries_name_their_host(entries):
    tab = next(e for e in entries if e.open_id == "earnings")
    assert tab.title == "Company › Earnings"
    assert tab.purpose and "**" not in tab.purpose


# -- the menu -------------------------------------------------------------------

def _menu(entries):
    opened = []
    menu = QMenu()
    search = panel_search.PanelMenuSearch(menu, lambda: entries, opened.append)
    menu.addAction(search.action)
    cats = [menu.addMenu("Markets").menuAction(), menu.addMenu("Research").menuAction()]
    return menu, search, cats, opened


def test_typing_swaps_categories_for_results(entries):
    menu, search, cats, _opened = _menu(entries)
    search._on_about_to_show()
    search.edit.setText("oil")
    assert all(not a.isVisible() for a in cats)
    assert not search.results.isHidden()
    assert search.results.currentItem().data(panel_search._ROLE_ENTRY).open_id == "commodities"
    search.edit.clear()
    assert all(a.isVisible() for a in cats)
    assert search.results.isHidden()
    menu.deleteLater()


def test_enter_opens_the_highlighted_row_and_arrows_move_it(entries):
    menu, search, _cats, opened = _menu(entries)
    search._on_about_to_show()
    search.edit.setText("chart")
    first = search.results.currentItem().data(panel_search._ROLE_ENTRY).open_id
    QTest.keyClick(search.edit, Qt.Key.Key_Down)
    second = search.results.currentItem().data(panel_search._ROLE_ENTRY).open_id
    assert second != first
    QTest.keyClick(search.edit, Qt.Key.Key_Return)
    assert opened == [second]
    menu.deleteLater()


def test_no_match_says_so_and_offers_the_closest(entries):
    menu, search, _cats, opened = _menu(entries)
    search._on_about_to_show()
    search.edit.setText("xqzvw")
    assert search.results.count() == 1
    assert "No panels match" in search.results.item(0).data(panel_search._ROLE_NOTE)
    QTest.keyClick(search.edit, Qt.Key.Key_Return)
    assert opened == []  # nothing to open
    menu.deleteLater()


def test_escape_clears_before_it_closes(entries):
    menu, search, cats, _opened = _menu(entries)
    search._on_about_to_show()
    search.edit.setText("gold")
    QTest.keyClick(search.edit, Qt.Key.Key_Escape)
    assert search.edit.text() == ""
    assert all(a.isVisible() for a in cats)
    menu.deleteLater()


def test_reopening_starts_from_the_categories(entries):
    menu, search, cats, _opened = _menu(entries)
    search.edit.setText("gold")
    search._on_about_to_show()
    assert search.edit.text() == ""
    assert all(a.isVisible() for a in cats)
    menu.deleteLater()
