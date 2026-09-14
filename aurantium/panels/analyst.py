"""Analyst Recs panel — recommendation summary, price targets, upgrade log."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..components.fmt import num as _fmt_price
from ..components import MarketTable, make_filter_edit
from ..panel import EMPTY_NO_SYMBOL, EMPTY_NO_SYMBOL_HINT, NULL_GLYPH, Panel, register_panel
from ..theme import DOWN, FG_DIM, FONT_TITLE, UP

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


@register_panel(id="analyst", title="Analyst Recs", category="Research")
class AnalystPanel(Panel):
    def build(self) -> None:
        # -- summary strip ------------------------------------------------
        summary = QVBoxLayout()

        rec_row = QHBoxLayout()
        self.rec_lbl = QLabel("—", self)
        self.rec_lbl.setStyleSheet(f"font-weight: 700; font-size: {FONT_TITLE}px;")
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
            lbl.setObjectName("secondary")
        target_row.addWidget(self.target_low_lbl)
        target_row.addWidget(self.target_mean_lbl)
        target_row.addWidget(self.target_high_lbl)
        target_row.addStretch(1)
        summary.addLayout(target_row)

        self.content_layout.addLayout(summary)

        # -- upgrades table -------------------------------------------------
        self.table = MarketTable(0, len(UPGRADE_HEADERS), self)
        self.register_state_target(self.table)
        self.table.setHorizontalHeaderLabels(UPGRADE_HEADERS)
        # Narrow panels give up columns instead of squeezing every one
        self.table.set_column_priority(keep=[0, 2], droppable=[3, 4, 1])
        self.table.set_empty_text(
            EMPTY_NO_SYMBOL,
            EMPTY_NO_SYMBOL_HINT,
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.enable_sorting()
        self.table.enable_column_menu()

        self._filter = make_filter_edit(self.table, "Filter firms/actions…")
        self.content_layout.addWidget(self._filter)
        self.content_layout.addWidget(self.table, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        self._clear()
        self.table.set_empty_text(f"No analyst actions for {symbol}", "")
        self.unsubscribe_all()
        self.subscribe(f"analyst:{symbol}", self._on_analyst)

    def _clear(self) -> None:
        """Drop the previous symbol's figures before the next fetch lands.

        Without this the summary strip kept the *old* company's recommendation
        and price targets under the *new* company's ticker for as long as the
        request took. Stale-but-labelled-wrong is the one presentation a market
        panel must never offer: an empty field says "not yet", a wrong field
        says nothing at all.
        """
        self.rec_lbl.setText(NULL_GLYPH)
        self.rec_lbl.setStyleSheet(f"font-weight: 700; font-size: {FONT_TITLE}px;")
        self.mean_lbl.setText("")
        self.count_lbl.setText("")
        self.target_low_lbl.setText(f"Low: {NULL_GLYPH}")
        self.target_mean_lbl.setText(f"Mean: {NULL_GLYPH}")
        self.target_high_lbl.setText(f"High: {NULL_GLYPH}")
        self.table.setRowCount(0)

    def _on_analyst(self, data: Any) -> None:
        # The fetch resolved — clear the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        if not isinstance(data, dict):
            return

        rec_key = data.get("recommendation_key")
        rec_mean = data.get("recommendation_mean")
        count = data.get("analyst_count")

        self.rec_lbl.setText((rec_key or "—").replace("_", " ").upper())
        self.rec_lbl.setStyleSheet(f"font-weight: 700; font-size: {FONT_TITLE}px; color: {_rec_color(rec_key)};")
        self.mean_lbl.setText(f"avg {rec_mean:.2f}" if isinstance(rec_mean, (int, float)) else "")
        self.count_lbl.setText(f"({count} analysts)" if count is not None else "")

        self.target_low_lbl.setText(f"Low: {_fmt_price(data.get('target_low'))}")
        self.target_mean_lbl.setText(f"Mean: {_fmt_price(data.get('target_mean'))}")
        self.target_high_lbl.setText(f"High: {_fmt_price(data.get('target_high'))}")

        upgrades = data.get("upgrades") if isinstance(data.get("upgrades"), list) else []
        with self.table.bulk_update():
            self.table.setRowCount(0)
            for entry in upgrades:
                if not isinstance(entry, dict):
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                values = [
                    entry.get("date") or NULL_GLYPH,
                    entry.get("firm") or NULL_GLYPH,
                    entry.get("action") or NULL_GLYPH,
                    entry.get("from_grade") or NULL_GLYPH,
                    entry.get("to_grade") or NULL_GLYPH,
                ]
                for col, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(row, col, item)
        self.table.apply_filter(self._filter.text())

        self.set_status(f"{len(upgrades)} upgrades/downgrades")

    # -- persistence ------------------------------------------------------------

    def settings(self) -> dict:
        return {"hidden_cols": self.table.hidden_columns()}

    def restore(self, settings: dict) -> None:
        if isinstance(settings, dict):
            self.table.set_hidden_columns(settings.get("hidden_cols", []))
