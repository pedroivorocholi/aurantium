"""Financials panel — Bloomberg FA style. Income / Balance / Cash Flow
statements, annual or quarterly, in a flipped table (line items as rows,
periods as columns)."""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import DOWN

STATEMENTS = [("income", "Income"), ("balance", "Balance"), ("cashflow", "Cash Flow")]
PERIODS = [("annual", "Annual"), ("quarterly", "Quarterly")]


def _fmt_compact(value: Any) -> str:
    """Human-format a financial-statement value: T/B/M suffixes, plain for
    small magnitudes, negatives keep their sign."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if v < 0 else ""
    av = abs(v)
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if av >= div:
            return f"{sign}{av / div:.1f}{suffix}"
    if av >= 1e3:
        return f"{sign}{av:,.0f}"
    return f"{sign}{av:,.2f}"


@register_panel(id="fundamentals", title="Financials", category="Research")
class FundamentalsPanel(Panel):
    def build(self) -> None:
        self._statement = "income"
        self._period = "annual"
        self._data: dict = {}

        # -- statement picker + period toggle -------------------------------
        picker_row = QHBoxLayout()
        self._statement_buttons: dict[str, QPushButton] = {}
        stmt_group = QButtonGroup(self)
        stmt_group.setExclusive(True)
        for key, label in STATEMENTS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=key: self._set_statement(k))
            stmt_group.addButton(btn)
            picker_row.addWidget(btn)
            self._statement_buttons[key] = btn
        picker_row.addSpacing(16)

        self._period_buttons: dict[str, QPushButton] = {}
        period_group = QButtonGroup(self)
        period_group.setExclusive(True)
        for key, label in PERIODS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=key: self._set_period(k))
            period_group.addButton(btn)
            picker_row.addWidget(btn)
            self._period_buttons[key] = btn
        picker_row.addStretch(1)
        self.content_layout.addLayout(picker_row)
        self._update_buttons()

        # -- table -----------------------------------------------------------
        self.table = QTableWidget(0, 1, self)
        self.table.setHorizontalHeaderLabels(["Line Item"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.content_layout.addWidget(self.table, 1)

    # -- statement / period toggles -------------------------------------------

    def _update_buttons(self) -> None:
        for key, btn in self._statement_buttons.items():
            btn.setChecked(key == self._statement)
        for key, btn in self._period_buttons.items():
            btn.setChecked(key == self._period)

    def _set_statement(self, key: str) -> None:
        if key == self._statement:
            return
        self._statement = key
        self._update_buttons()
        self._render()

    def _set_period(self, key: str) -> None:
        if key == self._period:
            return
        self._period = key
        self._update_buttons()
        self._render()

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self._data = {}
        self.unsubscribe_all()
        self.subscribe(f"financials:{symbol}", self._on_financials)

    def _on_financials(self, data: Any) -> None:
        self._data = data if isinstance(data, dict) else {}
        self._render()

    # -- rendering -------------------------------------------------------------

    def _current_block(self) -> Optional[dict]:
        stmt = self._data.get(self._statement)
        if not isinstance(stmt, dict):
            return None
        block = stmt.get(self._period)
        return block if isinstance(block, dict) else None

    def _render(self) -> None:
        block = self._current_block()
        columns = block.get("columns") if block else None
        rows = block.get("rows") if block else None
        columns = columns if isinstance(columns, list) else []
        rows = rows if isinstance(rows, list) else []

        headers = ["Line Item"] + [str(c) for c in columns]
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(0)

        for row_data in rows:
            if not isinstance(row_data, (list, tuple)) or not row_data:
                continue
            label = row_data[0]
            values = list(row_data[1:])
            r = self.table.rowCount()
            self.table.insertRow(r)
            label_item = QTableWidgetItem(str(label) if label is not None else "-")
            label_item.setFlags(label_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 0, label_item)
            for col in range(len(columns)):
                value = values[col] if col < len(values) else None
                item = QTableWidgetItem(_fmt_compact(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                try:
                    if value is not None and float(value) < 0:
                        item.setForeground(QColor(DOWN))
                except (TypeError, ValueError):
                    pass
                self.table.setItem(r, col + 1, item)

        sym = self.current_symbol or "—"
        stmt_label = dict(STATEMENTS).get(self._statement, self._statement)
        period_label = dict(PERIODS).get(self._period, self._period)
        self.set_status(f"{sym} · {stmt_label} · {period_label} · {len(rows)} lines")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"statement": self._statement, "period": self._period}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        stmt = settings.get("statement")
        if stmt in self._statement_buttons:
            self._statement = stmt
        period = settings.get("period")
        if period in self._period_buttons:
            self._period = period
        self._update_buttons()
        self._render()
