"""Portfolio panel — user-entered positions marked to live quotes.

Driver panel: clicking a row publishes ``set_symbol`` like watchlist.py, but
this panel never *follows* the linked symbol (no ``on_symbol`` override) so
navigating elsewhere never disturbs the position list.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG_HEADER, DOWN, UP

COL_SYMBOL, COL_QTY, COL_COST, COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT = range(7)
HEADERS = ["Symbol", "Qty", "Cost", "Last", "Mkt Value", "P&L", "P&L%"]


def _fmt(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="portfolio", title="Portfolio", category="Analytics")
class PortfolioPanel(Panel):
    def build(self) -> None:
        self._positions: list[dict] = []  # [{"symbol", "qty", "cost"}, ...]
        self._last_price: dict[str, Optional[float]] = {}
        self._data_row_count = 0

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_QTY, COL_COST, COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.content_layout.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.symbol_edit = QLineEdit(self)
        self.symbol_edit.setPlaceholderText("Symbol…")
        self.qty_spin = QDoubleSpinBox(self)
        self.qty_spin.setRange(0.0001, 1e9)
        self.qty_spin.setDecimals(4)
        self.qty_spin.setValue(1.0)
        self.cost_spin = QDoubleSpinBox(self)
        self.cost_spin.setRange(0.0, 1e7)
        self.cost_spin.setDecimals(4)
        add_btn = QPushButton("Add", self)
        add_btn.clicked.connect(self._add_position)
        remove_btn = QPushButton("Remove", self)
        remove_btn.clicked.connect(self._remove_selected)
        add_row.addWidget(self.symbol_edit, 1)
        add_row.addWidget(self.qty_spin)
        add_row.addWidget(self.cost_spin)
        add_row.addWidget(add_btn)
        add_row.addWidget(remove_btn)
        self.content_layout.addLayout(add_row)

        self._rebuild_table()

    # -- table (re)construction ----------------------------------------------

    def _rebuild_table(self) -> None:
        """Rebuild all rows (positions + bold totals row) and resubscribe all
        quote topics — mirrors watchlist.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._last_price.clear()
        self._data_row_count = len(self._positions)

        for pos in self._positions:
            self._append_position_row(pos)
        self._append_totals_row()

        symbols = {pos["symbol"] for pos in self._positions}
        for sym in symbols:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))
        self._recompute_totals()

    def _append_position_row(self, pos: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        sym_item = QTableWidgetItem(pos["symbol"])
        sym_item.setFlags(sym_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_SYMBOL, sym_item)

        qty_item = QTableWidgetItem(_fmt(pos["qty"], 4))
        cost_item = QTableWidgetItem(_fmt(pos["cost"], 4))
        for item in (qty_item, cost_item):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, COL_QTY, qty_item)
        self.table.setItem(row, COL_COST, cost_item)

        for col in (COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            item = QTableWidgetItem("-")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)

    def _append_totals_row(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        label_item = QTableWidgetItem("TOTAL")
        label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        font = label_item.font()
        font.setBold(True)
        label_item.setFont(font)
        label_item.setForeground(QColor(ACCENT))
        label_item.setBackground(QColor(BG_HEADER))
        self.table.setItem(row, COL_SYMBOL, label_item)
        for col in (COL_QTY, COL_COST, COL_LAST, COL_MKTVAL, COL_PNL, COL_PNLPCT):
            item = QTableWidgetItem("-")
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            bold_font = item.font()
            bold_font.setBold(True)
            item.setFont(bold_font)
            item.setBackground(QColor(BG_HEADER))
            self.table.setItem(row, col, item)

    # -- data callbacks --------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._last_price[symbol] = data.get("price")
        for row, pos in enumerate(self._positions):
            if pos["symbol"] == symbol:
                self._update_position_row(row, pos)
        self._recompute_totals()

    def _update_position_row(self, row: int, pos: dict) -> None:
        last = self._last_price.get(pos["symbol"])
        qty = pos["qty"]
        cost = pos["cost"]
        mkt_val = last * qty if last is not None else None
        pnl = (last - cost) * qty if last is not None else None
        pnl_pct = ((last - cost) / cost * 100.0) if (last is not None and cost) else None

        last_item = self.table.item(row, COL_LAST)
        mktval_item = self.table.item(row, COL_MKTVAL)
        pnl_item = self.table.item(row, COL_PNL)
        pnlpct_item = self.table.item(row, COL_PNLPCT)
        if not (last_item and mktval_item and pnl_item and pnlpct_item):
            return
        last_item.setText(_fmt(last))
        mktval_item.setText(_fmt(mkt_val))
        pnl_item.setText(_fmt(pnl))
        pnlpct_item.setText(f"{_fmt(pnl_pct)}%" if pnl_pct is not None else "-")
        if pnl is not None:
            color = QColor(UP) if pnl >= 0 else QColor(DOWN)
            pnl_item.setForeground(color)
            pnlpct_item.setForeground(color)

    def _recompute_totals(self) -> None:
        total_row = self.table.rowCount() - 1
        if total_row < 0 or total_row != self._data_row_count:
            return  # table mid-rebuild
        total_mkt = 0.0
        total_pnl = 0.0
        total_cost = 0.0
        any_data = False
        for pos in self._positions:
            last = self._last_price.get(pos["symbol"])
            if last is None:
                continue
            any_data = True
            total_mkt += last * pos["qty"]
            total_pnl += (last - pos["cost"]) * pos["qty"]
            total_cost += pos["cost"] * pos["qty"]

        mktval_item = self.table.item(total_row, COL_MKTVAL)
        pnl_item = self.table.item(total_row, COL_PNL)
        pnlpct_item = self.table.item(total_row, COL_PNLPCT)
        if not (mktval_item and pnl_item and pnlpct_item):
            return
        if not any_data:
            mktval_item.setText("-")
            pnl_item.setText("-")
            pnlpct_item.setText("-")
            return
        mktval_item.setText(_fmt(total_mkt))
        pnl_item.setText(_fmt(total_pnl))
        pnl_pct = (total_pnl / total_cost * 100.0) if total_cost else None
        pnlpct_item.setText(f"{_fmt(pnl_pct)}%" if pnl_pct is not None else "-")
        color = QColor(UP) if total_pnl >= 0 else QColor(DOWN)
        pnl_item.setForeground(color)
        pnlpct_item.setForeground(color)

    # -- add / remove ------------------------------------------------------------

    def _add_position(self) -> None:
        symbol = self.symbol_edit.text().strip().upper()
        if not symbol:
            return
        qty = self.qty_spin.value()
        cost = self.cost_spin.value()
        self._positions.append({"symbol": symbol, "qty": qty, "cost": cost})
        self.symbol_edit.clear()
        self._rebuild_table()

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        removed = False
        for row in rows:
            if row >= self._data_row_count:
                continue  # totals row
            del self._positions[row]
            removed = True
        if removed:
            self._rebuild_table()

    # -- selection -> navigation --------------------------------------------------

    def _on_row_selected(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        if row >= self._data_row_count:
            return  # totals row
        item = self.table.item(row, COL_SYMBOL)
        if item is None:
            return
        self.set_symbol(item.text())

    # -- persistence ---------------------------------------------------------------

    def settings(self) -> dict:
        return {"positions": [[p["symbol"], p["qty"], p["cost"]] for p in self._positions]}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        positions = settings.get("positions")
        if not isinstance(positions, list):
            return
        cleaned = []
        for entry in positions:
            if not (isinstance(entry, list) and len(entry) == 3):
                continue
            symbol, qty, cost = entry
            try:
                qty = float(qty)
                cost = float(cost)
            except (TypeError, ValueError):
                continue
            symbol = str(symbol).strip().upper()
            if not symbol:
                continue
            cleaned.append({"symbol": symbol, "qty": qty, "cost": cost})
        self._positions = cleaned
        self._rebuild_table()
