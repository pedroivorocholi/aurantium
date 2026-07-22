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
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
)

from ..components import (
    MarketTable,
    NumericTableWidgetItem,
    attach_suggestions,
    make_filter_edit,
)
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import ACCENT, apply_tick

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
        # symbol -> its column-0 item (find current row via table.row(item)) and
        # symbol -> the live-updated value cells. Keyed by item reference, not
        # row index, so quote updates and highlight survive user re-sorting.
        self._sym_item: dict[str, QTableWidgetItem] = {}
        self._cells: dict[str, dict] = {}
        self._suppress_select = False

        self.table = MarketTable(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_SYMBOL, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_VOLUME):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.installEventFilter(self)
        self.table.enable_sorting()
        self.table.enable_column_menu()

        self._filter = make_filter_edit(self.table, "Filter symbols…")
        self.content_layout.addWidget(self._filter)
        self.content_layout.addWidget(self.table, 1)

        add_row = QHBoxLayout()
        self.symbol_edit = QLineEdit(self)
        self.symbol_edit.setPlaceholderText("Add symbol or name…")
        self.symbol_edit.returnPressed.connect(self._add_symbol)
        attach_suggestions(self.symbol_edit)
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
        self._sym_item.clear()
        self._cells.clear()
        with self.table.bulk_update():
            self.table.setRowCount(0)
            for sym in self._symbols:
                self._append_row(sym)
        self.table.apply_filter(self._filter.text())
        for sym in self._symbols:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    def _append_row(self, symbol: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        sym_item = QTableWidgetItem(symbol)
        sym_item.setFlags(sym_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        sym_item.setForeground(QColor(ACCENT))
        self.table.setItem(row, COL_SYMBOL, sym_item)
        cells: dict = {}
        for key, col in (
            ("last", COL_LAST),
            ("chg", COL_CHG),
            ("pct", COL_CHGPCT),
            ("vol", COL_VOLUME),
        ):
            item = NumericTableWidgetItem("-")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)
            cells[key] = item
        self._sym_item[symbol] = sym_item
        self._cells[symbol] = cells

    def _on_quote(self, symbol: str, data: Any) -> None:
        cells = self._cells.get(symbol)
        if cells is None or not isinstance(data, dict):
            return
        price = data.get("price")
        change = data.get("change")
        change_pct = data.get("change_pct")
        volume = data.get("volume")

        # update via cached item references — correct even after the user has
        # sorted the table (visual row indices no longer track symbols). The
        # bulk_update coalesces the four edits into a single re-sort per tick.
        with self.table.bulk_update():
            cells["last"].setText(_fmt_num(price))
            cells["chg"].setText(_fmt_num(change))
            cells["pct"].setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")
            cells["vol"].setText(_fmt_volume(volume))
            if change is not None:
                apply_tick(cells["chg"], change, glyph=False)
                apply_tick(cells["pct"], change)
        # a re-sort can drop row-hidden flags, so re-assert an active filter
        if self._filter.text().strip():
            self.table.apply_filter(self._filter.text())

    # -- add / remove symbols -------------------------------------------------

    def _push_symbols_undo(self, label: str) -> None:
        snap = list(self._symbols)

        def _undo() -> None:
            self._symbols = list(snap)
            self._rebuild_table()
            self.set_status(f"undo · {label}")

        UndoStack.instance().push(label, _undo)

    def _add_symbol(self) -> None:
        text = self.symbol_edit.text().strip().upper()
        self.symbol_edit.clear()
        if not text or text in self._symbols:
            return
        self._push_symbols_undo("add symbol")
        self._symbols.append(text)
        self._rebuild_table()

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self._push_symbols_undo("remove symbol")
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
        item = self._sym_item.get(symbol)
        if item is None:
            return
        row = self.table.row(item)
        if row < 0:
            return
        self._suppress_select = True
        try:
            self.table.selectRow(row)
        finally:
            self._suppress_select = False

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "symbols": list(self._symbols),
            "hidden_cols": self.table.hidden_columns(),
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        symbols = settings.get("symbols")
        if isinstance(symbols, list) and symbols:
            cleaned = [str(s).strip().upper() for s in symbols if str(s).strip()]
            if cleaned:
                self._symbols = cleaned
                self._rebuild_table()
        self.table.set_hidden_columns(settings.get("hidden_cols", []))
