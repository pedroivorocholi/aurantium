"""Watchlist panel — the headline driver panel.

Shows live quotes for a user-editable symbol list. Clicking a row publishes
that symbol via ``self.set_symbol()``, which drives every other panel in the
same link group. This panel itself does not *follow* the linked symbol (it's
a source, not a sink) but it does highlight the matching row so the user can
see which symbol is currently active elsewhere.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, DOWN, UP

DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "TSLA",
    "SPY", "QQQ", "ES=F", "GC=F", "CL=F", "BTC-USD",
]

COL_SYMBOL, COL_LAST, COL_CHG, COL_CHGPCT, COL_VOLUME = range(5)
HEADERS = ["Symbol", "Last", "Chg", "Chg%", "Volume"]


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_volume(value: Any) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    for suffix, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.1f}{suffix}"
    return f"{v:,.0f}"


@register_panel(id="watchlist", title="Watchlist", category="Markets")
class WatchlistPanel(Panel):
    def build(self) -> None:
        self._symbols: list[str] = list(DEFAULT_SYMBOLS)
        self._row_of: dict[str, int] = {}
        self._suppress_select = False

        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_VOLUME):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.installEventFilter(self)
        self.content_layout.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.symbol_edit = QLineEdit(self)
        self.symbol_edit.setPlaceholderText("Add symbol…")
        self.symbol_edit.returnPressed.connect(self._add_symbol)
        add_btn = QPushButton("Add", self)
        add_btn.clicked.connect(self._add_symbol)
        remove_btn = QPushButton("Remove", self)
        remove_btn.clicked.connect(self._remove_selected)
        add_row.addWidget(self.symbol_edit, 1)
        add_row.addWidget(add_btn)
        add_row.addWidget(remove_btn)
        self.content_layout.addLayout(add_row)

        self._rebuild_table()

    # -- Qt event filter: Del/Backspace on the table removes the row --------

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._remove_selected()
                return True
        return super().eventFilter(obj, event)

    # -- table (re)construction ----------------------------------------------

    def _rebuild_table(self) -> None:
        """Rebuild all rows and resubscribe all quote topics from
        ``self._symbols`` — simplest correct way to keep subscriptions in
        sync with an editable symbol list."""
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._row_of.clear()
        for sym in self._symbols:
            self._append_row(sym)
        for sym in self._symbols:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    def _append_row(self, symbol: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        sym_item = QTableWidgetItem(symbol)
        sym_item.setFlags(sym_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        sym_item.setForeground(QColor(ACCENT))
        self.table.setItem(row, COL_SYMBOL, sym_item)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_VOLUME):
            item = QTableWidgetItem("-")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)
        self._row_of[symbol] = row

    def _on_quote(self, symbol: str, data: Any) -> None:
        row = self._row_of.get(symbol)
        if row is None or not isinstance(data, dict):
            return
        price = data.get("price")
        change = data.get("change")
        change_pct = data.get("change_pct")
        volume = data.get("volume")

        last_item = self.table.item(row, COL_LAST)
        chg_item = self.table.item(row, COL_CHG)
        pct_item = self.table.item(row, COL_CHGPCT)
        vol_item = self.table.item(row, COL_VOLUME)
        if not (last_item and chg_item and pct_item and vol_item):
            return

        last_item.setText(_fmt_num(price))
        chg_item.setText(_fmt_num(change))
        pct_item.setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")
        vol_item.setText(_fmt_volume(volume))

        if change is not None:
            color = QColor(UP) if change >= 0 else QColor(DOWN)
            chg_item.setForeground(color)
            pct_item.setForeground(color)

    # -- add / remove symbols -------------------------------------------------

    def _add_symbol(self) -> None:
        text = self.symbol_edit.text().strip().upper()
        self.symbol_edit.clear()
        if not text or text in self._symbols:
            return
        self._symbols.append(text)
        self._rebuild_table()

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for row in rows:
            item = self.table.item(row, COL_SYMBOL)
            if item is None:
                continue
            sym = item.text()
            if sym in self._symbols:
                self._symbols.remove(sym)
        self._rebuild_table()

    # -- selection -> navigation (user click only) -----------------------------

    def _on_row_selected(self) -> None:
        if self._suppress_select:
            return
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        item = self.table.item(rows[0].row(), COL_SYMBOL)
        if item is None:
            return
        self.set_symbol(item.text())

    # -- linked-symbol highlight (no re-publish, no loop) ----------------------

    def on_symbol(self, symbol: str) -> None:
        row = self._row_of.get(symbol)
        if row is None:
            return
        self._suppress_select = True
        try:
            self.table.selectRow(row)
        finally:
            self._suppress_select = False

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"symbols": list(self._symbols)}

    def restore(self, settings: dict) -> None:
        symbols = settings.get("symbols") if isinstance(settings, dict) else None
        if isinstance(symbols, list) and symbols:
            cleaned = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if cleaned:
                self._symbols = cleaned
                self._rebuild_table()
