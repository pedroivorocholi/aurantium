"""Dividends panel — yield/rate/ex-date/payout stats strip, dividend
history table, and a splits section appended below a separator row."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, FG_DIM

HEADERS = ["Date", "Amount"]
STAT_KEYS = ["Yield%", "Rate", "Ex-Date", "Payout Ratio"]


def _fmt_money(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="dividends", title="Dividends", category="Research")
class DividendsPanel(Panel):
    def build(self) -> None:
        # -- stats strip: 2x2 grid of label pairs ---------------------------
        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(16)
        self.stats_grid.setVerticalSpacing(4)
        self._stat_labels: dict[str, QLabel] = {}
        for i, key in enumerate(STAT_KEYS):
            row, col = divmod(i, 2)
            k_lbl = QLabel(key, self)
            k_lbl.setStyleSheet(f"color: {FG_DIM};")
            v_lbl = QLabel("-", self)
            v_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
            self.stats_grid.addWidget(k_lbl, row, col * 2)
            self.stats_grid.addWidget(v_lbl, row, col * 2 + 1)
            self._stat_labels[key] = v_lbl
        self.content_layout.addLayout(self.stats_grid)

        # -- history / splits table -------------------------------------------
        self.table = QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.content_layout.addWidget(self.table, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        for lbl in self._stat_labels.values():
            lbl.setText("-")
        self.table.setRowCount(0)
        self.unsubscribe_all()
        self.subscribe(f"dividends:{symbol}", self._on_dividends)

    def _on_dividends(self, data: Any) -> None:
        data = data if isinstance(data, dict) else {}

        self._stat_labels["Yield%"].setText(_fmt_pct(data.get("yield_pct")))
        self._stat_labels["Rate"].setText(_fmt_money(data.get("rate")))
        self._stat_labels["Ex-Date"].setText(data.get("ex_date") or "-")
        self._stat_labels["Payout Ratio"].setText(_fmt_pct(data.get("payout_ratio")))

        history = data.get("history")
        history = history if isinstance(history, list) else []
        splits = data.get("splits")
        splits = splits if isinstance(splits, list) else []

        self.table.setRowCount(0)
        for entry in history:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            date, amount = entry[0], entry[1]
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._set_ro_item(r, 0, str(date) if date is not None else "-")
            self._set_ro_item(r, 1, _fmt_money(amount), align_right=True)

        if splits:
            r = self.table.rowCount()
            self.table.insertRow(r)
            sep_item = QTableWidgetItem("— Splits —")
            sep_item.setFlags(sep_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            sep_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            sep_item.setForeground(QColor(FG_DIM))
            self.table.setItem(r, 0, sep_item)
            self.table.setSpan(r, 0, 1, len(HEADERS))

            for entry in splits:
                if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                    continue
                date, ratio = entry[0], entry[1]
                r = self.table.rowCount()
                self.table.insertRow(r)
                self._set_ro_item(r, 0, str(date) if date is not None else "-")
                self._set_ro_item(r, 1, str(ratio) if ratio is not None else "-", align_right=True)

        sym = self.current_symbol or "—"
        self.set_status(f"{sym} · {len(history)} dividends · {len(splits)} splits")

    def _set_ro_item(self, row: int, col: int, text: str, align_right: bool = False) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, col, item)
