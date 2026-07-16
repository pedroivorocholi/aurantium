"""Macro / Rates panel — US Treasury yield curve, CFTC positioning snapshot,
and a status line. Fully static: no linked-symbol behavior, no user config.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, DOWN, FG_DIM, UP

# (maturity years, quote ticker, curve tick label) — price field IS the yield
MATURITIES = [
    (0.25, "^IRX", "3M"),
    (5.0, "^FVX", "5Y"),
    (10.0, "^TNX", "10Y"),
    (30.0, "^TYX", "30Y"),
]

CFTC_MARKETS = [
    ("Gold", "gold"),
    ("S&P 500", "sp500"),
    ("Crude Oil", "crude_oil"),
    ("Bitcoin", "bitcoin"),
]

COL_MARKET, COL_NETSPEC, COL_BIAS = range(3)
CFTC_HEADERS = ["Market", "Net Spec", "Bias"]


def _fmt_num(value: Any, decimals: int = 0) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="macro", title="Macro / Rates", category="Analytics")
class MacroPanel(Panel):
    def build(self) -> None:
        self._yields: dict[str, Optional[float]] = {ticker: None for _, ticker, _ in MATURITIES}
        self._cftc_loaded: set[str] = set()

        # -- (a) US Treasury yield curve ------------------------------------------
        curve_title = QLabel("US Treasury Yield Curve", self)
        curve_title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.content_layout.addWidget(curve_title)

        self.curve_widget = pg.PlotWidget()
        self.curve_widget.setBackground(BG)
        self.curve_widget.showGrid(x=True, y=True, alpha=0.15)
        self.curve_widget.setLabel("left", "Yield (%)")
        axis_bottom = self.curve_widget.getAxis("bottom")
        axis_bottom.setTicks([[(m, label) for m, _t, label in MATURITIES]])
        axis_bottom.setTextPen(FG_DIM)
        self.curve_widget.getAxis("left").setTextPen(FG_DIM)
        self.yield_curve = pg.PlotDataItem(
            pen=pg.mkPen(ACCENT, width=2),
            symbol="o",
            symbolBrush=ACCENT,
            symbolPen=ACCENT,
            symbolSize=8,
        )
        self.curve_widget.addItem(self.yield_curve)
        self.content_layout.addWidget(self.curve_widget, 2)

        self.spread_lbl = QLabel("10Y–3M spread: —", self)
        self.spread_lbl.setStyleSheet(f"color: {FG_DIM};")
        self.content_layout.addWidget(self.spread_lbl)

        # -- (b) CFTC positioning ---------------------------------------------------
        cftc_title = QLabel("Positioning (CFTC)", self)
        cftc_title.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.content_layout.addWidget(cftc_title)

        self.cftc_table = QTableWidget(len(CFTC_MARKETS), len(CFTC_HEADERS), self)
        self.cftc_table.setHorizontalHeaderLabels(CFTC_HEADERS)
        self.cftc_table.verticalHeader().setVisible(False)
        self.cftc_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cftc_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.cftc_table.setAlternatingRowColors(True)
        header = self.cftc_table.horizontalHeader()
        header.setSectionResizeMode(COL_MARKET, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_NETSPEC, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_BIAS, QHeaderView.ResizeMode.Stretch)
        self._cftc_row: dict[str, int] = {}
        for row, (label, market) in enumerate(CFTC_MARKETS):
            name_item = QTableWidgetItem(label)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.cftc_table.setItem(row, COL_MARKET, name_item)
            for col in (COL_NETSPEC, COL_BIAS):
                item = QTableWidgetItem("-")
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.cftc_table.setItem(row, col, item)
            self._cftc_row[market] = row
        self.content_layout.addWidget(self.cftc_table, 1)

        # -- (c) status only ------------------------------------------------------------
        self.set_status("loading…")

        # -- static subscriptions (no on_symbol; this panel never follows links) ----------
        for _maturity, ticker, _label in MATURITIES:
            self.subscribe(f"quote:{ticker}", lambda data, t=ticker: self._on_yield_quote(t, data))
        for _label, market in CFTC_MARKETS:
            self.subscribe(f"cftc:{market}", lambda data, m=market: self._on_cftc(m, data))

    # -- yield curve -------------------------------------------------------------

    def _on_yield_quote(self, ticker: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._yields[ticker] = data.get("price")
        self._redraw_curve()
        self._update_status()

    def _redraw_curve(self) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for maturity, ticker, _label in MATURITIES:
            y = self._yields.get(ticker)
            if y is None:
                continue
            xs.append(maturity)
            ys.append(float(y))
        self.yield_curve.setData(xs, ys)

        irx = self._yields.get("^IRX")
        tnx = self._yields.get("^TNX")
        if irx is None or tnx is None:
            self.spread_lbl.setText("10Y–3M spread: —")
            self.spread_lbl.setStyleSheet(f"color: {FG_DIM};")
            return
        spread_bp = (float(tnx) - float(irx)) * 100.0
        color = DOWN if spread_bp < 0 else UP
        self.spread_lbl.setText(f"10Y–3M spread: {spread_bp:+.0f} bp")
        self.spread_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # -- CFTC positioning ----------------------------------------------------------

    def _on_cftc(self, market: str, data: Any) -> None:
        row = self._cftc_row.get(market)
        if row is None or not isinstance(data, dict):
            return
        self._cftc_loaded.add(market)
        net_item = self.cftc_table.item(row, COL_NETSPEC)
        bias_item = self.cftc_table.item(row, COL_BIAS)
        if not (net_item and bias_item):
            return
        net_spec = data.get("noncommercial_net")
        bias = data.get("bias")
        net_item.setText(_fmt_num(net_spec))
        bias_text = str(bias) if bias is not None else "-"
        bias_item.setText(bias_text)
        low = bias_text.lower()
        if "bull" in low:
            bias_item.setForeground(QColor(UP))
        elif "bear" in low:
            bias_item.setForeground(QColor(DOWN))
        else:
            bias_item.setForeground(QColor(FG_DIM))
        self._update_status()

    # -- status -----------------------------------------------------------------------

    def _update_status(self) -> None:
        yields_loaded = sum(1 for v in self._yields.values() if v is not None)
        cftc_loaded = len(self._cftc_loaded)
        if yields_loaded == len(MATURITIES) and cftc_loaded == len(CFTC_MARKETS):
            self.set_status("ready")
        else:
            self.set_status(
                f"yields {yields_loaded}/{len(MATURITIES)} · CFTC {cftc_loaded}/{len(CFTC_MARKETS)}"
            )
