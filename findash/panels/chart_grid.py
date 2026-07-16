"""Chart Grid panel — Bloomberg "chart grid" clone: an N-column grid of
small line charts, one per symbol, each with a title label showing last
price and change%, colored by sign. Click a cell to drive linked panels.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, BORDER_STRONG, DOWN, FG_DIM, UP

DEFAULT_SYMBOLS = [
    "^NDX", "^GSPC", "^DJI", "^BVSP", "^FCHI",
    "GC=F", "CL=F", "NG=F", "BTC-USD",
]

COLUMNS = 3
HISTORY_PERIOD = "6mo"
HISTORY_INTERVAL = "1d"
MA_MUTED = "#c9a24a"  # muted gold moving-average line on the mini charts


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _sma(values: list, window: int) -> Optional[list]:
    """Simple moving average aligned to ``values[window-1:]`` (pure Python —
    the mini charts don't otherwise need numpy)."""
    if window <= 0 or len(values) < window:
        return None
    total = sum(values[:window])
    out = [total / window]
    for i in range(window, len(values)):
        total += values[i] - values[i - window]
        out.append(total / window)
    return out


class _ChartCell(QWidget):
    """One grid cell: title label + mini line chart. Clicking anywhere on
    the cell publishes its symbol via ``on_click``."""

    def __init__(self, symbol: str, on_click, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.symbol = symbol
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(120)
        self.setObjectName("chartCell")
        self.setStyleSheet(
            f"QWidget#chartCell {{ background: {BG}; border: 1px solid {BORDER_STRONG}; }}"
            f"QWidget#chartCell:hover {{ border-color: {ACCENT}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 6)
        layout.setSpacing(3)

        self.title_lbl = QLabel(symbol, self)
        self.title_lbl.setStyleSheet(f"font-weight: bold; color: {ACCENT};")
        layout.addWidget(self.title_lbl)

        self.plot_widget = pg.PlotWidget(self)
        self.plot_widget.setBackground(BG)
        self.plot_widget.setMinimumHeight(90)
        self.plot_widget.showGrid(x=False, y=False)
        self.plot_widget.getPlotItem().hideAxis("bottom")
        left_axis = self.plot_widget.getAxis("left")
        left_axis.setTextPen(FG_DIM)
        left_axis.setWidth(34)
        self.plot_widget.getPlotItem().setMenuEnabled(False)
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideButtons()
        layout.addWidget(self.plot_widget, 1)

        self._curve: Optional[pg.PlotDataItem] = None

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click(self.symbol)
        super().mousePressEvent(event)

    def set_history(self, t: list, c: list) -> None:
        pairs = [(ti, ci) for ti, ci in zip(t, c) if ti is not None and ci is not None]
        if not pairs:
            return
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        color = UP if ys[-1] >= ys[0] else DOWN
        self.plot_widget.clear()
        pen = pg.mkPen(color, width=1.5)
        fill_color = QColor(color)
        fill_color.setAlpha(48)
        brush = pg.mkBrush(fill_color)  # translucent fill
        self._curve = self.plot_widget.plot(
            xs, ys, pen=pen, fillLevel=min(ys), brush=brush
        )
        # muted moving-average line for structure (50-bar, or 20 if data is short)
        window = 50 if len(ys) >= 50 else (20 if len(ys) >= 20 else 0)
        ma = _sma(ys, window) if window else None
        if ma is not None:
            self.plot_widget.plot(
                xs[window - 1:], ma, pen=pg.mkPen(MA_MUTED, width=1)
            )
        self.plot_widget.enableAutoRange()

    def set_quote(self, data: dict) -> None:
        price = data.get("price")
        change_pct = data.get("change_pct")
        last = _fmt_num(price)
        if change_pct is None:
            chg_txt = "-"
            color = FG_DIM
        else:
            sign = "+" if change_pct >= 0 else ""
            chg_txt = f"{sign}{change_pct:.2f}%"
            color = UP if change_pct >= 0 else DOWN
        self.title_lbl.setText(f"{self.symbol}  {last}  {chg_txt}")
        self.title_lbl.setStyleSheet(f"font-weight: bold; color: {color};")


@register_panel(id="chart_grid", title="Chart Grid", category="Markets")
class ChartGridPanel(Panel):
    def build(self) -> None:
        self._symbols: list[str] = list(DEFAULT_SYMBOLS)
        self._cells: dict[str, _ChartCell] = {}

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._grid_host = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setSpacing(6)
        self._scroll.setWidget(self._grid_host)
        self.content_layout.addWidget(self._scroll, 1)

        config_row = QHBoxLayout()
        self.symbols_edit = QLineEdit(self)
        self.symbols_edit.setText(", ".join(self._symbols))
        self.symbols_edit.setPlaceholderText("Comma-separated symbols…")
        apply_btn = QPushButton("Apply", self)
        apply_btn.clicked.connect(self._apply_symbols)
        self.symbols_edit.returnPressed.connect(self._apply_symbols)
        config_row.addWidget(self.symbols_edit, 1)
        config_row.addWidget(apply_btn)
        self.content_layout.addLayout(config_row)

        self._rebuild_grid()

    # -- grid (re)construction -------------------------------------------------

    def _rebuild_grid(self) -> None:
        """Tear down all cells/subscriptions and rebuild from
        ``self._symbols`` — mirrors watchlist.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cells.clear()

        for i, sym in enumerate(self._symbols):
            cell = _ChartCell(sym, self._on_cell_click, self._grid_host)
            row, col = divmod(i, COLUMNS)
            self._grid.addWidget(cell, row, col)
            self._cells[sym] = cell

        for sym in self._symbols:
            self.subscribe(
                f"history:{sym}:{HISTORY_PERIOD}:{HISTORY_INTERVAL}",
                lambda data, s=sym: self._on_history(s, data),
            )
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    # -- data callbacks ----------------------------------------------------------

    def _on_history(self, symbol: str, data: Any) -> None:
        cell = self._cells.get(symbol)
        if cell is None or not isinstance(data, dict):
            return
        t = data.get("t") or []
        c = data.get("c") or []
        cell.set_history(t, c)

    def _on_quote(self, symbol: str, data: Any) -> None:
        cell = self._cells.get(symbol)
        if cell is None or not isinstance(data, dict):
            return
        cell.set_quote(data)

    # -- interaction -----------------------------------------------------------

    def _on_cell_click(self, symbol: str) -> None:
        self.set_symbol(symbol)

    def _apply_symbols(self) -> None:
        text = self.symbols_edit.text()
        symbols = [s.strip().upper() for s in text.split(",") if s.strip()]
        if not symbols:
            return
        self._symbols = symbols
        self._rebuild_grid()

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"symbols": list(self._symbols)}

    def restore(self, settings: dict) -> None:
        symbols = settings.get("symbols") if isinstance(settings, dict) else None
        if isinstance(symbols, list) and symbols:
            cleaned = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if cleaned:
                self._symbols = cleaned
                self.symbols_edit.setText(", ".join(self._symbols))
                self._rebuild_grid()
