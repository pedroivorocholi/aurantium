"""Options Chain panel — Bloomberg OMON-lite: expiry picker + spot label
above two side-by-side read-only tables (calls / puts). The ATM strike row
(closest to spot) is highlighted; ITM rows get a subtle tint.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..panel import Panel, register_panel
from ..theme import BG_HEADER

# calls: Vol | OI | IV% | Bid | Ask | Last | Strike
CALL_HEADERS = ["Vol", "OI", "IV%", "Bid", "Ask", "Last", "Strike"]
CALL_COL_VOL, CALL_COL_OI, CALL_COL_IV, CALL_COL_BID, CALL_COL_ASK, CALL_COL_LAST, CALL_COL_STRIKE = range(7)

# puts: Strike | Last | Bid | Ask | IV% | OI | Vol
PUT_HEADERS = ["Strike", "Last", "Bid", "Ask", "IV%", "OI", "Vol"]
PUT_COL_STRIKE, PUT_COL_LAST, PUT_COL_BID, PUT_COL_ASK, PUT_COL_IV, PUT_COL_OI, PUT_COL_VOL = range(7)

# payload row layout: [strike, last, bid, ask, volume, open_interest, iv_pct]
ROW_STRIKE, ROW_LAST, ROW_BID, ROW_ASK, ROW_VOLUME, ROW_OI, ROW_IV = range(7)

_ITM_TINT_CALL = "#1c2b22"   # very subtle green tint
_ITM_TINT_PUT = "#2b1c1c"    # very subtle red tint


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_int(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _row_field(row: Any, idx: int) -> Any:
    if not isinstance(row, (list, tuple)) or idx >= len(row):
        return None
    return row[idx]


def _closest_strike_index(rows: list, spot: Any) -> int | None:
    if spot is None or not rows:
        return None
    try:
        spot_f = float(spot)
    except (TypeError, ValueError):
        return None
    best_idx = None
    best_dist = None
    for i, row in enumerate(rows):
        strike = _row_field(row, ROW_STRIKE)
        if strike is None:
            continue
        try:
            dist = abs(float(strike) - spot_f)
        except (TypeError, ValueError):
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _make_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


@register_panel(id="options", title="Options Chain", category="Research")
class OptionsChainPanel(Panel):
    def build(self) -> None:
        self._expiries: list[str] = []
        self._current_expiry: str = ""
        self._spot: Any = None

        # -- header row: expiry combo + spot label ---------------------------
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Expiry:", self))
        self.expiry_combo = QComboBox(self)
        self.expiry_combo.currentTextChanged.connect(self._on_expiry_changed)
        header_row.addWidget(self.expiry_combo)
        header_row.addStretch(1)
        self.spot_lbl = QLabel("Spot: -", self)
        self.spot_lbl.setStyleSheet("font-weight: bold;")
        header_row.addWidget(self.spot_lbl)
        self.content_layout.addLayout(header_row)

        # -- calls / puts tables side by side ---------------------------------
        tables_row = QHBoxLayout()

        self.calls_table = QTableWidget(0, len(CALL_HEADERS), self)
        self.calls_table.setHorizontalHeaderLabels(CALL_HEADERS)
        self._configure_table(self.calls_table)
        tables_row.addWidget(self.calls_table, 1)

        self.puts_table = QTableWidget(0, len(PUT_HEADERS), self)
        self.puts_table.setHorizontalHeaderLabels(PUT_HEADERS)
        self._configure_table(self.puts_table)
        tables_row.addWidget(self.puts_table, 1)

        self.content_layout.addLayout(tables_row, 1)

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    # -- symbol / subscription lifecycle -------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self._expiries = []
        self._current_expiry = ""
        self._spot = None
        self.expiry_combo.blockSignals(True)
        self.expiry_combo.clear()
        self.expiry_combo.blockSignals(False)
        self.calls_table.setRowCount(0)
        self.puts_table.setRowCount(0)
        self.spot_lbl.setText("Spot: -")
        self.unsubscribe_all()
        self.subscribe(f"options:{symbol}", self._on_options)

    def _on_expiry_changed(self, expiry: str) -> None:
        if not expiry or expiry == self._current_expiry or not self.current_symbol:
            return
        self._current_expiry = expiry
        self.unsubscribe_all()
        self.subscribe(f"options:{self.current_symbol}:{expiry}", self._on_options)

    # -- data callback --------------------------------------------------------

    def _on_options(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        # Drop stale callbacks from a previous symbol. Switching tickers quickly
        # can leave a just-queued payload for the old symbol in flight; if it
        # names a different symbol than the one we're showing now, ignore it so
        # the expiry combo and tables never fill with the wrong ticker's data.
        payload_sym = data.get("symbol")
        if payload_sym and payload_sym != self.current_symbol:
            return

        spot = data.get("spot")
        self._spot = spot
        self.spot_lbl.setText(f"Spot: {_fmt_num(spot)}" if spot is not None else "Spot: -")

        expiry = data.get("expiry")
        expiries = data.get("expiries")
        if isinstance(expiries, list) and expiries:
            self._populate_expiry_combo(expiries, expiry)

        calls = data.get("calls") if isinstance(data.get("calls"), list) else []
        puts = data.get("puts") if isinstance(data.get("puts"), list) else []

        self._populate_calls(calls, spot)
        self._populate_puts(puts, spot)

        sym = data.get("symbol") or self.current_symbol
        self.set_status(f"{sym} · {expiry or '-'} · {len(calls)}C / {len(puts)}P")

    def _populate_expiry_combo(self, expiries: list, current: Any) -> None:
        # Guard against feedback loops while repopulating.
        self.expiry_combo.blockSignals(True)
        try:
            existing = [self.expiry_combo.itemText(i) for i in range(self.expiry_combo.count())]
            new_items = [str(e) for e in expiries]
            if existing != new_items:
                self.expiry_combo.clear()
                self.expiry_combo.addItems(new_items)
                self._expiries = new_items

            target = str(current) if current is not None else self._current_expiry
            if target and target in new_items:
                idx = self.expiry_combo.findText(target)
                if idx >= 0:
                    self.expiry_combo.setCurrentIndex(idx)
                self._current_expiry = target
            elif new_items and not self._current_expiry:
                self.expiry_combo.setCurrentIndex(0)
                self._current_expiry = new_items[0]
        finally:
            self.expiry_combo.blockSignals(False)

    def _populate_calls(self, rows: list, spot: Any) -> None:
        atm_idx = _closest_strike_index(rows, spot)
        self.calls_table.setRowCount(0)
        for i, row in enumerate(rows):
            strike = _row_field(row, ROW_STRIKE)
            last = _row_field(row, ROW_LAST)
            bid = _row_field(row, ROW_BID)
            ask = _row_field(row, ROW_ASK)
            volume = _row_field(row, ROW_VOLUME)
            oi = _row_field(row, ROW_OI)
            iv = _row_field(row, ROW_IV)

            r = self.calls_table.rowCount()
            self.calls_table.insertRow(r)
            values = {
                CALL_COL_VOL: _fmt_int(volume),
                CALL_COL_OI: _fmt_int(oi),
                CALL_COL_IV: _fmt_num(iv, 1),
                CALL_COL_BID: _fmt_num(bid),
                CALL_COL_ASK: _fmt_num(ask),
                CALL_COL_LAST: _fmt_num(last),
                CALL_COL_STRIKE: _fmt_num(strike),
            }
            is_itm = _is_itm(strike, spot, is_call=True)
            is_atm = i == atm_idx
            for col, text in values.items():
                item = _make_item(text)
                self._style_row_item(item, is_itm, is_atm, is_call=True)
                self.calls_table.setItem(r, col, item)

    def _populate_puts(self, rows: list, spot: Any) -> None:
        atm_idx = _closest_strike_index(rows, spot)
        self.puts_table.setRowCount(0)
        for i, row in enumerate(rows):
            strike = _row_field(row, ROW_STRIKE)
            last = _row_field(row, ROW_LAST)
            bid = _row_field(row, ROW_BID)
            ask = _row_field(row, ROW_ASK)
            volume = _row_field(row, ROW_VOLUME)
            oi = _row_field(row, ROW_OI)
            iv = _row_field(row, ROW_IV)

            r = self.puts_table.rowCount()
            self.puts_table.insertRow(r)
            values = {
                PUT_COL_STRIKE: _fmt_num(strike),
                PUT_COL_LAST: _fmt_num(last),
                PUT_COL_BID: _fmt_num(bid),
                PUT_COL_ASK: _fmt_num(ask),
                PUT_COL_IV: _fmt_num(iv, 1),
                PUT_COL_OI: _fmt_int(oi),
                PUT_COL_VOL: _fmt_int(volume),
            }
            is_itm = _is_itm(strike, spot, is_call=False)
            is_atm = i == atm_idx
            for col, text in values.items():
                item = _make_item(text)
                self._style_row_item(item, is_itm, is_atm, is_call=False)
                self.puts_table.setItem(r, col, item)

    @staticmethod
    def _style_row_item(item: QTableWidgetItem, is_itm: bool, is_atm: bool, is_call: bool) -> None:
        if is_atm:
            item.setBackground(QColor(BG_HEADER))
        elif is_itm:
            item.setBackground(QColor(_ITM_TINT_CALL if is_call else _ITM_TINT_PUT))


def _is_itm(strike: Any, spot: Any, is_call: bool) -> bool:
    if strike is None or spot is None:
        return False
    try:
        strike_f = float(strike)
        spot_f = float(spot)
    except (TypeError, ValueError):
        return False
    return strike_f < spot_f if is_call else strike_f > spot_f
