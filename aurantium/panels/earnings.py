"""Earnings panel — next report date and an EPS estimate-vs-actual history
table with beat/miss coloring."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidgetItem,
)

from ..guide import PanelGuide
from ..components import MarketTable, NumericTableWidgetItem, make_filter_edit
from ..components import fmt
from ..panel import EMPTY_NO_SYMBOL, EMPTY_NO_SYMBOL_HINT, NULL_GLYPH, Panel, register_panel
from ..theme import ACCENT, FG_DIM, UP, apply_tick

HEADERS = ["Date", "EPS Est", "EPS Actual", "Surprise%"]


def _fmt_eps(value: Any) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return NULL_GLYPH


def _fmt_pct(value: Any) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


@register_panel(id="earnings", title="Earnings", category="Research", listed=False)
class EarningsPanel(Panel):
    GUIDE = PanelGuide(
        purpose="When the company reports next, and its earnings per share against estimates.",
        links="Follows the Company panel's symbol.",
        interactions=(
            "Beats print green and misses red; the filter box narrows by date.",
        ),
        fields=(
            ("EPS Est", "Analysts' consensus estimate of earnings per share"),
            ("EPS Actual", "What the company reported"),
            ("Surprise%", "How far the result beat (+) or missed (−) the estimate"),
        ),
    )

    def build(self) -> None:
        self.next_lbl = QLabel(f"Next earnings: {NULL_GLYPH}", self)
        self.next_lbl.setObjectName("statValue")
        self.content_layout.addWidget(self.next_lbl)

        self.table = MarketTable(0, len(HEADERS), self)
        self.register_state_target(self.table)
        self.table.setHorizontalHeaderLabels(HEADERS)
        # Narrow panels give up columns instead of squeezing every one
        self.table.set_column_priority(keep=[0, 2], droppable=[1, 3])
        self.table.set_empty_text(
            EMPTY_NO_SYMBOL,
            EMPTY_NO_SYMBOL_HINT,
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.enable_sorting()
        self.table.enable_column_menu()

        self._filter = make_filter_edit(self.table, "Filter by date…")
        self.content_layout.addWidget(self._filter)
        self.content_layout.addWidget(self.table, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        self.table.set_empty_text(f"No earnings history for {symbol}", "")
        self.next_lbl.setText(f"Next earnings: {NULL_GLYPH}")
        self.table.setRowCount(0)
        self.unsubscribe_all()
        self.subscribe(f"earnings:{symbol}", self._on_earnings)

    def _on_earnings(self, data: Any) -> None:
        # The fetch resolved — clear the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        data = data if isinstance(data, dict) else {}

        next_date = data.get("next_date")
        self.next_lbl.setText(f"Next earnings: {fmt.date(next_date)}" if next_date else f"Next earnings: {NULL_GLYPH}")

        rows = data.get("rows")
        rows = rows if isinstance(rows, list) else []
        with self.table.bulk_update():
            self.table.setRowCount(0)
            for row_data in rows:
                if not isinstance(row_data, (list, tuple)) or len(row_data) < 4:
                    continue
                date, est, actual, surprise = row_data[0], row_data[1], row_data[2], row_data[3]
                r = self.table.rowCount()
                self.table.insertRow(r)

                date_item = QTableWidgetItem(fmt.date(date))
                date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                date_item.setForeground(QColor(FG_DIM))
                self.table.setItem(r, 0, date_item)

                est_item = NumericTableWidgetItem(_fmt_eps(est))
                est_item.setFlags(est_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                est_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(r, 1, est_item)

                beat = False
                try:
                    if est is not None and actual is not None:
                        beat = float(actual) > float(est)
                except (TypeError, ValueError):
                    beat = False

                actual_item = NumericTableWidgetItem(_fmt_eps(actual))
                actual_item.setFlags(actual_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                actual_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if actual is not None and beat:
                    actual_item.setForeground(QColor(UP))
                self.table.setItem(r, 2, actual_item)

                surprise_item = NumericTableWidgetItem(_fmt_pct(surprise))
                surprise_item.setFlags(surprise_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                surprise_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                try:
                    if surprise is not None:
                        apply_tick(surprise_item, float(surprise))
                except (TypeError, ValueError):
                    pass
                self.table.setItem(r, 3, surprise_item)
        self.table.apply_filter(self._filter.text())

        sym = self.current_symbol or "—"
        self.set_status(f"{len(rows)} reports")

    # -- persistence ------------------------------------------------------------

    def settings(self) -> dict:
        return {"hidden_cols": self.table.hidden_columns()}

    def restore(self, settings: dict) -> None:
        if isinstance(settings, dict):
            self.table.set_hidden_columns(settings.get("hidden_cols", []))
