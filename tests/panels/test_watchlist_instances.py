"""The Watchlist exposes its symbols and a lookup by link group, so a Heatmap
can follow the Watchlist in its own group and redraw when that list changes."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy

from shiboken6 import isValid

from aurantium.panel import UNLINKED
from aurantium.panels.watchlist import WatchlistPanel


@pytest.fixture
def make(qapp, isolated_settings):
    WatchlistPanel._instances = []  # earlier tests may leave panels behind
    made = []

    def _make(group="A"):
        p = WatchlistPanel()
        p.build()
        p.set_link_group(group)
        made.append(p)
        return p

    yield _make
    for p in made:
        if isValid(p):
            p.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_in_group_returns_first_opened(make):
    first = make("A")
    second = make("A")
    assert WatchlistPanel.in_group("A") is first
    assert second is not first


def test_none_when_group_empty_or_unlinked(make):
    make(UNLINKED)
    make("A")
    assert WatchlistPanel.in_group("B") is None
    assert WatchlistPanel.in_group(UNLINKED) is None


def test_group_is_resolved_at_call_time(make):
    p = make("A")
    p.set_link_group("B")
    assert WatchlistPanel.in_group("A") is None
    assert WatchlistPanel.in_group("B") is p


def test_destroyed_instance_is_dropped(make, qapp):
    gone = make("A")
    kept = make("A")
    gone.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert WatchlistPanel.in_group("A") is kept


def test_symbols_are_plain_strings_in_table_order(make):
    p = make()
    syms = p.symbols()
    assert syms and all(type(s) is str for s in syms)
    assert syms == [p.table.item(r, 0).text() for r in range(p.table.rowCount())]


def test_symbols_changed_fires_on_add_and_remove(make):
    p = make()
    spy = QSignalSpy(p.symbols_changed)
    p.symbol_edit.setText("ZZZZ")
    p._add_symbol()
    assert spy.count() == 1
    assert "ZZZZ" in p.symbols()
    p.table.selectRow(p.symbols().index("ZZZZ"))
    p._remove_selected()
    assert spy.count() == 2
    assert "ZZZZ" not in p.symbols()


def test_symbols_changed_fires_when_link_group_changes(make):
    p = make("A")
    spy = QSignalSpy(p.symbols_changed)
    p.set_link_group("B")
    assert spy.count() == 1
