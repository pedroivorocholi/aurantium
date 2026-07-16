"""Holders panel — insider/institution ownership header strip and a top
holders table (shares, % held, value)."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT

HEADERS = ["Holder", "Shares", "% Held", "Value"]


def _fmt_compact(value: Any) -> str:
    """Human-format a large number: T/B/M/K suffixes, plain otherwise."""
    if value is None:
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "-" if v < 0 else ""
    av = abs(v)
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if av >= div:
            return f"{sign}{av / div:.1f}{suffix}"
    return f"{sign}{av:,.0f}"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="holders", title="Holders", category="Research")
class HoldersPanel(Panel):
    def build(self) -> None:
        header_row = QHBoxLayout()
        self.insiders_lbl = QLabel("Insiders: -", self)
        self.insiders_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.institutions_lbl = QLabel("Institutions: -", self)
        self.institutions_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        header_row.addWidget(self.insiders_lbl)
        header_row.addSpacing(24)
        header_row.addWidget(self.institutions_lbl)
        header_row.addStretch(1)
        self.content_layout.addLayout(header_row)

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
        self.insiders_lbl.setText("Insiders: -")
        self.institutions_lbl.setText("Institutions: -")
        self.table.setRowCount(0)
        self.unsubscribe_all()
        self.subscribe(f"holders:{symbol}", self._on_holders)

    def _on_holders(self, data: Any) -> None:
        data = data if isinstance(data, dict) else {}

        self.insiders_lbl.setText(f"Insiders: {_fmt_pct(data.get('insiders_pct'))}")
        self.institutions_lbl.setText(f"Institutions: {_fmt_pct(data.get('institutions_pct'))}")

        top = data.get("top")
        top = top if isinstance(top, list) else []
        self.table.setRowCount(0)
        for entry in top:
            if not isinstance(entry, (list, tuple)) or len(entry) < 4:
                continue
            holder, shares, pct_held, value = entry[0], entry[1], entry[2], entry[3]
            r = self.table.rowCount()
            self.table.insertRow(r)

            holder_item = QTableWidgetItem(str(holder) if holder is not None else "-")
            holder_item.setFlags(holder_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, 0, holder_item)

            self._set_ro_item(r, 1, _fmt_compact(shares), align_right=True)
            self._set_ro_item(r, 2, _fmt_pct(pct_held), align_right=True)
            self._set_ro_item(r, 3, _fmt_compact(value), align_right=True)

        sym = self.current_symbol or "—"
        self.set_status(f"{sym} · {len(top)} holders")

    def _set_ro_item(self, row: int, col: int, text: str, align_right: bool = False) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if align_right:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, col, item)
