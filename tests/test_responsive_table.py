"""Tables drop their least important columns rather than crushing all of them.

At 320px the Watchlist rendered "4,432.…" — the price, the one number the row
exists for, elided to fit a Volume column nobody reads at that width. Dropping
Volume and showing the price whole is the right trade; Qt's default is the
wrong one.
"""

import pytest
from PySide6.QtWidgets import QTableWidgetItem


@pytest.fixture
def table(qapp):
    from aurantium.components.market_table import MarketTable

    t = MarketTable(0, 5)
    t.setHorizontalHeaderLabels(["Symbol", "Last", "Chg", "Chg%", "Volume"])
    for row in range(3):
        t.insertRow(row)
        for col, text in enumerate(
            ["BTC USD", "62,952.63", "203.10", "0.32%", "20.0B"]
        ):
            t.setItem(row, col, QTableWidgetItem(text))
    t.show()
    return t


def _visible(t):
    return [c for c in range(t.columnCount()) if not t.isColumnHidden(c)]


def test_all_columns_show_when_there_is_room(table):
    # keep = Symbol and Last; the rest are droppable, least important first
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(1000, 200)
    assert _visible(table) == [0, 1, 2, 3, 4]


def test_the_least_important_column_goes_first(table):
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(300, 200)
    assert 4 not in _visible(table), "Volume should be the first to go"


def test_the_essential_columns_are_never_dropped(table):
    """However narrow it gets, the row still has to say which symbol and what
    price — that is the whole content of the row."""
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(120, 200)
    assert 0 in _visible(table)
    assert 1 in _visible(table)


def test_columns_come_back_when_the_panel_is_widened(table):
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(300, 200)
    narrow = _visible(table)
    table.resize(1000, 200)
    assert len(_visible(table)) > len(narrow)
    assert _visible(table) == [0, 1, 2, 3, 4]


def test_a_user_hidden_column_stays_hidden_when_widened(table):
    """Responsive hiding must not fight the right-click Columns menu — a column
    the user turned off stays off at every width."""
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(1000, 200)
    table._toggle_column(3, False)  # user hides Chg%
    table.resize(300, 200)
    table.resize(1000, 200)
    assert 3 not in _visible(table)


def test_a_table_without_priorities_is_untouched(table):
    """Opt-in: panels that never call set_column_priority keep Qt's behaviour."""
    table.resize(200, 200)
    assert _visible(table) == [0, 1, 2, 3, 4]


# -- the wide end ----------------------------------------------------------

def test_the_table_fills_a_wide_panel(table):
    """Packing every column to its content left a wide panel mostly black, with
    the zebra striping and the selected-row highlight stopping short of the
    edge — which reads as a broken table, not a tidy one. The name column
    absorbs the slack instead, the way terminal tables have always done it."""
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(900, 200)
    used = sum(
        table.columnWidth(c)
        for c in range(table.columnCount())
        if not table.isColumnHidden(c)
    )
    assert used >= table.viewport().width() - 4, (
        f"table uses {used}px of {table.viewport().width()}px"
    )


def test_the_name_column_is_the_one_that_grows(table):
    """Slack goes to the text column; the numeric columns stay tight so each
    value sits under its own header."""
    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(900, 200)
    assert table.columnWidth(0) > table.columnWidth(1)


def test_columns_do_not_sprawl_when_the_panel_is_wide(table):
    """Stretching every column meant a value floated ~200px from its own
    header on a wide panel — the eye can no longer connect the two, and a
    density-first terminal should not spend space that way. Columns size to
    their content; the slack collects at the end of the row."""
    from PySide6.QtWidgets import QHeaderView

    # a panel that stretches every column, the way they all did
    header = table.horizontalHeader()
    for col in range(1, table.columnCount()):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    table.resize(1400, 200)
    content = table.sizeHintForColumn(1)
    assert table.columnWidth(1) <= content * 2 + 40, (
        f"Last column is {table.columnWidth(1)}px for {content}px of content"
    )


def test_a_panel_setting_stretch_afterwards_does_not_defeat_the_priorities(table):
    """Ordering trap: several panels call setSectionResizeMode(Stretch) after
    declaring priorities, which silently restored the sprawl. The table
    re-asserts content sizing on resize so the order of the two calls in a
    panel's build() stops mattering."""
    from PySide6.QtWidgets import QHeaderView

    table.set_column_priority(keep=[0, 1], droppable=[4, 2, 3])
    header = table.horizontalHeader()
    for col in range(table.columnCount()):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

    table.resize(1400, 200)

    content = table.sizeHintForColumn(1)
    assert table.columnWidth(1) <= content * 2 + 40
