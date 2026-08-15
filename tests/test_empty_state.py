"""The empty state: a table with nothing in it must say what's missing.

Covers both wirings — MarketTable's built-in one (which also has to cooperate
with the loading veil and the row filter) and EmptyState.attach() for plain
item views such as the news table.
"""

import pytest

from aurantium import motion


def _finish_fades(widget):
    """Run any in-flight fade to its end. The empty state now crossfades, so
    "is it gone" is only answerable once the animation has landed."""
    anim = getattr(widget, "_aurantium_fade", None)
    if anim is not None and anim.duration():
        anim.setCurrentTime(anim.duration())


@pytest.fixture
def table(qapp):
    from aurantium.components.market_table import MarketTable

    t = MarketTable(0, 2)
    t.setHorizontalHeaderLabels(["A", "B"])
    t.resize(400, 300)
    return t


def _add_row(t, a, b):
    from PySide6.QtWidgets import QTableWidgetItem

    r = t.rowCount()
    t.insertRow(r)
    t.setItem(r, 0, QTableWidgetItem(a))
    t.setItem(r, 1, QTableWidgetItem(b))


def test_empty_table_shows_its_message(table):
    table.set_empty_text("No symbol selected", "Click a ticker")
    assert table._empty.isVisibleTo(table)
    assert table._empty.title == "No symbol selected"
    assert table._empty.hint == "Click a ticker"


def test_message_clears_once_rows_arrive(table):
    _add_row(table, "AAPL", "1")
    _finish_fades(table._empty)
    assert not table._empty.isVisibleTo(table)


def test_message_returns_when_rows_are_cleared(table):
    _add_row(table, "AAPL", "1")
    table.setRowCount(0)
    assert table._empty.isVisibleTo(table)


def test_loading_veil_wins_over_the_empty_message(table):
    """Both at once would say "nothing here" about a fetch still in flight."""
    table.set_loading(True)
    _finish_fades(table._empty)
    assert not table._empty.isVisibleTo(table)
    table.set_loading(False)
    assert table._empty.isVisibleTo(table)


def test_filtering_everything_away_says_so(table):
    _add_row(table, "AAPL", "1")
    table.set_empty_text("No symbol selected")
    table.apply_filter("zzz")
    assert table._empty.isVisibleTo(table)
    assert "zzz" in table._empty.title
    assert table._empty.title != "No symbol selected"


def test_clearing_the_filter_restores_the_panel_message(table):
    _add_row(table, "AAPL", "1")
    table.set_empty_text("No symbol selected")
    table.apply_filter("zzz")
    table.apply_filter("")
    _finish_fades(table._empty)
    assert not table._empty.isVisibleTo(table)


def test_attach_gives_a_plain_view_an_empty_state(qapp):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from aurantium.components.empty_state import EmptyState

    view = QTableWidget(0, 1)
    state = EmptyState.attach(view, "No headlines", "Pick a symbol")
    assert state.isVisibleTo(view)

    view.insertRow(0)
    view.setItem(0, 0, QTableWidgetItem("headline"))
    assert not state.isVisibleTo(view)

    view.setRowHidden(0, True)
    state.sync()  # hiding rows is not a model change; callers resync
    assert state.isVisibleTo(view)
