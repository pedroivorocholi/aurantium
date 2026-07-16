"""Earnings panel — next report date and an EPS estimate-vs-actual history
table with beat/miss coloring."""

from __future__ import annotations

from typing import Any

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
from ..theme import ACCENT, DOWN, FG_DIM, UP

HEADERS = ["Date", "EPS Est", "EPS Actual", "Surprise%"]


def _fmt_eps(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


@register_panel(id="earnings", title="Earnings", category="Research")
class EarningsPanel(Panel):
    def build(self) -> None:
        self.next_lbl = QLabel("Next earnings: -", self)
        self.next_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: bold; font-size: 13px;")
        self.content_layout.addWidget(self.next_lbl)

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
        self.next_lbl.setText("Next earnings: -")
        self.table.setRowCount(0)
        self.unsubscribe_all()
        self.subscribe(f"earnings:{symbol}", self._on_earnings)

    def _on_earnings(self, data: Any) -> None:
        data = data if isinstance(data, dict) else {}

        next_date = data.get("next_date")
        self.next_lbl.setText(f"Next earnings: {next_date}" if next_date else "Next earnings: -")

        rows = data.get("rows")
        rows = rows if isinstance(rows, list) else []
        self.table.setRowCount(0)
        for row_data in rows:
            if not isinstance(row_data, (list, tuple)) or len(row_data) < 4:
                continue
            date, est, actual, surprise = row_data[0], row_data[1], row_data[2], row_data[3]
            r = self.table.rowCount()
            self.table.insertRow(r)

            date_item = QTableWidgetItem(str(date) if date is not None else "-")
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            date_item.setForeground(QColor(FG_DIM))
            self.table.setItem(r, 0, date_item)

            est_item = QTableWidgetItem(_fmt_eps(est))
            est_item.setFlags(est_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            est_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(r, 1, est_item)

            beat = False
            try:
                if est is not None and actual is not None:
                    beat = float(actual) > float(est)
            except (TypeError, ValueError):
                beat = False

            actual_item = QTableWidgetItem(_fmt_eps(actual))
            actual_item.setFlags(actual_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            actual_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if actual is not None and beat:
                actual_item.setForeground(QColor(UP))
            self.table.setItem(r, 2, actual_item)

            surprise_item = QTableWidgetItem(_fmt_pct(surprise))
            surprise_item.setFlags(surprise_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            surprise_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            try:
                if surprise is not None:
                    surprise_item.setForeground(QColor(UP if float(surprise) >= 0 else DOWN))
            except (TypeError, ValueError):
                pass
            self.table.setItem(r, 3, surprise_item)

        sym = self.current_symbol or "—"
        self.set_status(f"{sym} · {len(rows)} reports")
