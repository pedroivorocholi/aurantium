"""Analyst Recs panel — recommendation summary, price targets, upgrade log."""

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
    QVBoxLayout,
)

from ..panel import Panel, register_panel
from ..theme import DOWN, FG_DIM, UP

UPGRADE_HEADERS = ["Date", "Firm", "Action", "From", "To"]

_BUYISH = {"buy", "strong_buy", "strongbuy", "outperform", "overweight"}
_SELLISH = {"sell", "strong_sell", "strongsell", "underperform", "underweight"}


def _rec_color(key: str) -> str:
    k = (key or "").strip().lower()
    if k in _BUYISH:
        return UP
    if k in _SELLISH:
        return DOWN
    return FG_DIM


def _fmt_price(v: Any) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="analyst", title="Analyst Recs", category="Research")
class AnalystPanel(Panel):
    def build(self) -> None:
        # -- summary strip ------------------------------------------------
        summary = QVBoxLayout()

        rec_row = QHBoxLayout()
        self.rec_lbl = QLabel("—", self)
        self.rec_lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
        self.mean_lbl = QLabel("", self)
        self.count_lbl = QLabel("", self)
        rec_row.addWidget(self.rec_lbl)
        rec_row.addWidget(self.mean_lbl)
        rec_row.addWidget(self.count_lbl)
        rec_row.addStretch(1)
        summary.addLayout(rec_row)

        target_row = QHBoxLayout()
        self.target_low_lbl = QLabel("Low: -", self)
        self.target_mean_lbl = QLabel("Mean: -", self)
        self.target_high_lbl = QLabel("High: -", self)
        for lbl in (self.target_low_lbl, self.target_mean_lbl, self.target_high_lbl):
            lbl.setStyleSheet(f"color: {FG_DIM};")
        target_row.addWidget(self.target_low_lbl)
        target_row.addWidget(self.target_mean_lbl)
        target_row.addWidget(self.target_high_lbl)
        target_row.addStretch(1)
        summary.addLayout(target_row)

        self.content_layout.addLayout(summary)

        # -- upgrades table -------------------------------------------------
        self.table = QTableWidget(0, len(UPGRADE_HEADERS), self)
        self.table.setHorizontalHeaderLabels(UPGRADE_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.content_layout.addWidget(self.table, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self.unsubscribe_all()
        self.subscribe(f"analyst:{symbol}", self._on_analyst)

    def _on_analyst(self, data: Any) -> None:
        if not isinstance(data, dict):
            return

        rec_key = data.get("recommendation_key")
        rec_mean = data.get("recommendation_mean")
        count = data.get("analyst_count")

        self.rec_lbl.setText((rec_key or "—").replace("_", " ").upper())
        self.rec_lbl.setStyleSheet(f"font-weight: bold; font-size: 14px; color: {_rec_color(rec_key)};")
        self.mean_lbl.setText(f"avg {rec_mean:.2f}" if isinstance(rec_mean, (int, float)) else "")
        self.count_lbl.setText(f"({count} analysts)" if count is not None else "")

        self.target_low_lbl.setText(f"Low: {_fmt_price(data.get('target_low'))}")
        self.target_mean_lbl.setText(f"Mean: {_fmt_price(data.get('target_mean'))}")
        self.target_high_lbl.setText(f"High: {_fmt_price(data.get('target_high'))}")

        upgrades = data.get("upgrades") if isinstance(data.get("upgrades"), list) else []
        self.table.setRowCount(0)
        for entry in upgrades:
            if not isinstance(entry, dict):
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [
                entry.get("date") or "-",
                entry.get("firm") or "-",
                entry.get("action") or "-",
                entry.get("from_grade") or "-",
                entry.get("to_grade") or "-",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, item)

        self.set_status(f"{self.current_symbol} · {len(upgrades)} upgrades/downgrades")
