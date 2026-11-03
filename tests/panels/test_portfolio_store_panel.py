"""The Portfolio panel reads and writes the app-wide PortfolioStore, so the
book outlives any one panel."""

import pytest
from PySide6.QtCore import QSettings

from aurantium.portfolio_store import PortfolioStore
from aurantium.undo import UndoStack


@pytest.fixture
def store(qapp, tmp_path):
    s = PortfolioStore(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))
    PortfolioStore.reset_instance(s)
    UndoStack.instance().clear()
    yield s
    PortfolioStore.reset_instance(None)


def _panel():
    from aurantium.panels.portfolio import PortfolioPanel

    p = PortfolioPanel()
    p.build()
    return p


def _add(p, sym="AAPL"):
    p.symbol_edit.setText(sym)
    p._add_position()


def _symbols(p):
    return [p.table.item(r, 0).text() for r in range(p._data_row_count)]


def test_two_panels_share_the_book(store):
    a, b = _panel(), _panel()
    _add(a)
    assert b._data_row_count == 1
    assert _symbols(b) == ["AAPL"]


def test_closing_panel_keeps_positions(store):
    a = _panel()
    _add(a)
    a.close()
    a.deleteLater()
    assert _symbols(_panel()) == ["AAPL"]


def test_undo_after_panel_closed(store):
    a = _panel()
    _add(a)
    a.close()
    a.deleteLater()
    UndoStack.instance().undo()
    assert store.is_empty()


def test_settings_carries_no_positions(store):
    a = _panel()
    _add(a)
    assert a.settings() == {}
