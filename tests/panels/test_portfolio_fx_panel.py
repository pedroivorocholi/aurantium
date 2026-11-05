"""The Portfolio panel shows values, P&L and analytics in the preferred currency."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QSignalSpy, QTest

from aurantium.datahub import DataHub
from aurantium.fx import FxService
from aurantium.panels.portfolio import (
    COL_LAST, COL_MKTVAL, COL_PNL, PortfolioPanel,
)
from aurantium.portfolio_store import PortfolioStore


@pytest.fixture
def store(svc, tmp_path):
    s = PortfolioStore(QSettings(str(tmp_path / "p.ini"), QSettings.Format.IniFormat))
    PortfolioStore.reset_instance(s)
    yield s
    PortfolioStore.reset_instance(None)


@pytest.fixture
def panel(store):
    p = PortfolioPanel()
    p.build()
    yield p
    p.close()
    p.deleteLater()


def _publish(topic, data):
    DataHub.instance().publish(topic, data)


def _settle():
    QTest.qWait(350)  # FxService coalesces ``changed`` over 150 ms


def _totals(p, col=COL_MKTVAL):
    return p.table.item(p._data_row_count, col).text()


def test_headers_name_the_currency(panel):
    assert panel.table.horizontalHeaderItem(COL_MKTVAL).text() == "Mkt Val (USD)"
    FxService.instance().set_preferred("BRL")
    _settle()
    assert panel.table.horizontalHeaderItem(COL_MKTVAL).text() == "Mkt Val (BRL)"
    assert panel.table.horizontalHeaderItem(COL_PNL).text() == "P&L (BRL)"


def test_totals_wait_for_rates(store, panel):
    store.add("PETR4.SA", 100, 30.0, "")
    _publish("quote:PETR4.SA", {"price": 38.0, "currency": "BRL"})
    _settle()
    assert _totals(panel) == "…"
    assert panel.table.item(0, COL_MKTVAL).text() == "…"
    assert panel.table.item(0, COL_LAST).text() == "38.00"  # listing currency
    _publish("quote:BRLUSD=X", {"price": 0.18})
    _settle()
    assert panel.table.item(0, COL_MKTVAL).text() == "684.00"
    assert _totals(panel) == "684.00"
    assert _totals(panel, COL_PNL) == "+144.00"


def test_switch_currency_blanks_then_fills(store, panel):
    store.add("AAPL", 10, 150.0, "")
    store.add("PETR4.SA", 100, 30.0, "")
    _publish("quote:AAPL", {"price": 200.0, "currency": "USD"})
    _publish("quote:PETR4.SA", {"price": 38.0, "currency": "BRL"})
    _publish("quote:BRLUSD=X", {"price": 0.18})
    _settle()
    assert _totals(panel) == "2,684.00"
    FxService.instance().set_preferred("EUR")
    _settle()
    assert _totals(panel) == "…"
    _publish("quote:USDEUR=X", {"price": 0.9})
    _publish("quote:BRLEUR=X", {"price": 0.17})
    _settle()
    assert _totals(panel) == "2,446.00"


def test_unknown_currency_is_dashed_and_left_out(store, panel):
    store.add("AAPL", 10, 150.0, "")
    store.add("FOO.XX", 5, 10.0, "")
    _publish("quote:AAPL", {"price": 200.0, "currency": "USD"})
    _publish("quote:FOO.XX", {"price": 12.0})
    _settle()
    cell = panel.table.item(1, COL_MKTVAL)
    assert cell.text() == "—"
    assert cell.toolTip() == "Currency unknown — left out of totals"
    assert _totals(panel) == "2,000.00"
    assert "1 position left out: currency unknown" in _totals_tip(panel)


def _totals_tip(p):
    return p.table.item(p._data_row_count, COL_MKTVAL).toolTip()


def test_left_out_note_on_allocation_tab(store, panel):
    store.add("FOO.XX", 5, 10.0, "")
    store.add("BAR.XX", 5, 10.0, "")
    _publish("quote:FOO.XX", {"price": 12.0})
    _publish("quote:BAR.XX", {"price": 12.0})
    panel.tabs.setCurrentIndex(1)
    _settle()
    assert panel._alloc_note.text() == "2 positions left out: currency unknown"
    assert not panel._alloc_note.isHidden()



def test_performance_names_currency_and_converts_benchmark(store, panel):
    store.add("AAPL", 10, 150.0, "")
    FxService.instance().set_preferred("EUR")
    panel.tabs.setCurrentIndex(2)
    _publish("quote:AAPL", {"price": 200.0, "currency": "USD"})
    _publish("history:SPY:1y:1d", {"t": [1_700_000_000, 1_700_086_400], "c": [100.0, 110.0]})
    _publish("history:AAPL:1y:1d", {"t": [1_700_000_000, 1_700_086_400], "c": [100.0, 110.0]})
    _publish("history:USDEUR=X:2y:1d", {"t": [1_690_000_000], "c": [0.5]})
    _settle()
    assert panel._perf_bench.name() == "SPY (EUR)"
    assert panel._perf_port.name() == "Portfolio (EUR)"
    _x, y = panel._perf_bench.getData()
    assert list(y) == pytest.approx([100.0, 110.0])  # rebased, constant rate
