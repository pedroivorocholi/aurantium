"""Every quote table answers "what just moved?" the same way.

The tick flash (``MarketTable.flash_cell``) had exactly one caller — the
watchlist's Last cell. Chg and Chg% beside it, and the quote cells of World
Indices, FX, Commodities, Macro and Portfolio, recoloured in silence. These
tests feed each panel two quotes and check the cells that moved flashed, the
ones that did not stayed still, and nothing flashes under reduced motion.
"""

import pytest

from aurantium import motion


def _flashing(table):
    return {id(entry[0]) for entry in table._flashes}


@pytest.fixture
def animate(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


def _quote(price, change, pct):
    return {"price": price, "change": change, "change_pct": pct}


# -- the shared helper ---------------------------------------------------------

def test_flash_on_change_flashes_only_a_real_move(qapp, animate):
    from PySide6.QtWidgets import QTableWidgetItem

    from aurantium.components.market_table import MarketTable

    t = MarketTable(1, 1)
    item = QTableWidgetItem("1")
    t.setItem(0, 0, item)
    t.flash_on_change(item, 100.0)  # first sight: nothing to compare with
    assert not t._flashes
    t.flash_on_change(item, 100.0)  # a refresh that changed nothing
    assert not t._flashes
    t.flash_on_change(item, 101.0)
    assert _flashing(t) == {id(item)}


def test_flash_on_change_is_still_under_reduced_motion(qapp, monkeypatch):
    from PySide6.QtWidgets import QTableWidgetItem

    from aurantium.components.market_table import MarketTable

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    t = MarketTable(1, 1)
    item = QTableWidgetItem("1")
    t.setItem(0, 0, item)
    t.flash_on_change(item, 100.0)
    t.flash_on_change(item, 101.0)
    assert not t._flashes
    assert not t._flash_timer.isActive()


# -- the panels ------------------------------------------------------------------

def _first_symbol(panel):
    rows = getattr(panel, "_row_of_symbol", None)
    if rows:
        return next(iter(rows))
    return next(iter(panel._cells))


def _cells(panel, symbol, cols):
    row = panel._row_of_symbol[symbol]
    return [panel.table.item(row, c) for c in cols]


@pytest.mark.parametrize(
    "module, cls_name",
    [
        ("world_indices", "WorldIndicesPanel"),
        ("fx_monitor", "FXMonitorPanel"),
        ("commodities", "CommoditiesPanel"),
    ],
)
def test_quote_panels_flash_last_chg_and_pct(qapp, animate, module, cls_name):
    import importlib

    mod = importlib.import_module(f"aurantium.panels.{module}")
    p = getattr(mod, cls_name)()
    p.build()
    sym = _first_symbol(p)
    cols = (mod.COL_LAST, mod.COL_CHG, mod.COL_CHGPCT)
    p._on_quote(sym, _quote(100.0, 1.0, 1.0))
    assert not p.table._flashes
    p._on_quote(sym, _quote(101.0, 2.0, 2.0))
    assert _flashing(p.table) == {id(i) for i in _cells(p, sym, cols)}


def test_watchlist_flashes_chg_and_pct_beside_last(qapp, animate):
    from aurantium.panels.watchlist import WatchlistPanel

    p = WatchlistPanel()
    p.build()
    sym = _first_symbol(p)
    p._on_quote(sym, _quote(100.0, 1.0, 1.0))
    p._on_quote(sym, _quote(101.0, 2.0, 2.0))
    cells = p._cells[sym]
    assert _flashing(p.table) == {id(cells["last"]), id(cells["chg"]), id(cells["pct"])}


def test_an_unchanged_refresh_flashes_nothing(qapp, animate):
    from aurantium.panels.watchlist import WatchlistPanel

    p = WatchlistPanel()
    p.build()
    sym = _first_symbol(p)
    p._on_quote(sym, _quote(100.0, 1.0, 1.0))
    p._on_quote(sym, _quote(100.0, 1.0, 1.0))
    assert not p.table._flashes


def test_macro_instrument_rows_flash(qapp, animate):
    from aurantium.panels.macro import MacroPanel

    p = MacroPanel()
    p.build()
    target = next(
        t for t in p._inst_row_of_target if p._inst_items(t) is not None
    )
    p._on_inst_quote(target, _quote(100.0, 1.0, 1.0))
    assert not p.inst_table._flashes
    p._on_inst_quote(target, _quote(101.0, 2.0, 2.0))
    assert _flashing(p.inst_table) == {id(i) for i in p._inst_items(target)}
