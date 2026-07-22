"""Example: Macro Snapshot — TEMPLATE FOR CUSTOM PANELS.

The leading underscore in this filename keeps it OUT of the Panels menu
(panel discovery skips ``_*.py`` files) — it's a tutorial, not a panel you'd
use. The ``category="Examples"`` below is a second guard: that category is
never registered, so even an underscore-less copy stays out of the menu. To
try it, copy it to a name without the underscore (e.g. ``my_panel.py``),
change ``id=``/``title=`` AND set a real ``category=`` (e.g. "Analytics"),
restart aurantium, and it appears under Panels ▸ Add Panel.

Read this file top to bottom, then copy it to write your own panel. A panel
is one Python file in ``user_panels/`` (this directory) with exactly one
``Panel`` subclass decorated with ``@register_panel``. Drop the file here,
restart aurantium, and it shows up in the Panels ▸ Add Panel menu — nothing
else to register.

The three things every panel needs:
  1. ``build()``       — construct widgets, add them to ``self.content_layout``.
  2. ``on_symbol(sym)`` — react when the linked symbol changes (optional —
                          skip it entirely if your panel isn't symbol-driven).
  3. ``self.subscribe(topic, callback)`` — the ONLY way to get data. Panels
     never call an API or make a network request directly; they ask
     DataHub for a topic and DataHub (via a Provider running elsewhere)
     publishes values to every subscriber whenever fresh data shows up.
     The callback may fire immediately with a cached value, and again
     whenever the topic refreshes — write it to be idempotent.

Note the import style below: this file lives OUTSIDE the ``aurantium``
package (in ``user_panels/``, loaded by file path at startup), so imports
are absolute (``from aurantium...``) rather than relative (``from ..``) like
the built-in panels under ``aurantium/panels/`` use.

To make your own panel: copy this file, rename it, change the ``id=`` /
``title=`` in ``@register_panel`` below to something unique, then replace
the body of ``build()`` and ``on_symbol()`` with your own widgets and
topics. Everything else (header, status text, link-group badge, settings
persistence) is handled by the ``Panel`` base class for free.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from aurantium.panel import Panel, register_panel
from aurantium.theme import ACCENT, DOWN, FG_DIM, UP

# Commodities shown in the mini-table below (World Bank Commodities topics).
COMMODITY_CODES = ["GOLD", "WTI", "COPPER"]


def _fmt(value: Any, decimals: int = 2) -> str:
    """Small defensive formatter — every field from a topic can be None."""
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


@register_panel(id="example_macro", title="Example: Macro Snapshot", category="Examples")
class ExampleMacroPanel(Panel):
    def build(self) -> None:
        # --- 1) CFTC positioning: two independent, non-symbol topics ------
        # These aren't tied to the linked symbol at all — we just subscribe
        # once in build() and let the callbacks update labels forever.
        cftc_grid = QGridLayout()
        cftc_grid.addWidget(QLabel("CFTC Positioning", self), 0, 0, 1, 3)

        self.gold_bias_lbl = QLabel("Gold: —", self)
        self.gold_net_lbl = QLabel("", self)
        cftc_grid.addWidget(self.gold_bias_lbl, 1, 0)
        cftc_grid.addWidget(self.gold_net_lbl, 1, 1)

        self.sp500_bias_lbl = QLabel("S&P 500: —", self)
        self.sp500_net_lbl = QLabel("", self)
        cftc_grid.addWidget(self.sp500_bias_lbl, 2, 0)
        cftc_grid.addWidget(self.sp500_net_lbl, 2, 1)

        self.content_layout.addLayout(cftc_grid)

        # subscribe() registers the callback AND triggers a refresh; the
        # callback fires immediately if a cached value already exists.
        self.subscribe("cftc:gold", self._on_cftc_gold)
        self.subscribe("cftc:sp500", self._on_cftc_sp500)

        # --- 2) Commodities mini-table: another set of static topics -------
        self.content_layout.addWidget(QLabel("Commodities", self))
        self.commodity_table = QTableWidget(len(COMMODITY_CODES), 3, self)
        self.commodity_table.setHorizontalHeaderLabels(["Code", "Price", "Chg%"])
        self.commodity_table.verticalHeader().setVisible(False)
        self.commodity_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._commodity_rows: dict[str, int] = {}
        for row, code in enumerate(COMMODITY_CODES):
            self.commodity_table.setItem(row, 0, QTableWidgetItem(code))
            self.commodity_table.setItem(row, 1, QTableWidgetItem("-"))
            self.commodity_table.setItem(row, 2, QTableWidgetItem("-"))
            self._commodity_rows[code] = row
            # one subscription per commodity; the "code" default argument
            # trick avoids the classic late-binding-closure-in-a-loop bug
            self.subscribe(f"wbc:{code}", lambda data, c=code: self._on_commodity(c, data))
        self.content_layout.addWidget(self.commodity_table)

        # --- 3) Linked-symbol demo -------------------------------------------
        # This is the piece that reacts to on_symbol() below — click any
        # symbol in a linked panel (e.g. Watchlist) and this label updates.
        self.linked_lbl = QLabel("Linked symbol: —", self)
        self.linked_lbl.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.content_layout.addWidget(self.linked_lbl)

        self.content_layout.addStretch(1)

    # -- on_symbol(): called whenever this panel's link group's active -------
    # symbol changes (e.g. someone clicked a row in the Watchlist panel).
    # Implementing this is entirely optional — a panel with only "static"
    # macro topics (like the CFTC/commodities data above) doesn't need it.
    def on_symbol(self, symbol: str) -> None:
        self.linked_lbl.setText(f"Linked symbol: {symbol}")

    # -- topic callbacks -------------------------------------------------------
    # Every callback MUST tolerate None / missing keys / wrong types: the
    # provider behind a topic may not have data yet, or the field may be
    # legitimately unavailable (e.g. no analyst coverage for a ticker).

    def _on_cftc_gold(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        bias = data.get("bias") or "—"
        net = data.get("noncommercial_net")
        self.gold_bias_lbl.setText(f"Gold: {bias}")
        self.gold_bias_lbl.setStyleSheet(f"color: {_bias_color(bias)};")
        self.gold_net_lbl.setText(f"net {_fmt(net, 0)}" if net is not None else "")

    def _on_cftc_sp500(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        bias = data.get("bias") or "—"
        net = data.get("noncommercial_net")
        self.sp500_bias_lbl.setText(f"S&P 500: {bias}")
        self.sp500_bias_lbl.setStyleSheet(f"color: {_bias_color(bias)};")
        self.sp500_net_lbl.setText(f"net {_fmt(net, 0)}" if net is not None else "")

    def _on_commodity(self, code: str, data: Any) -> None:
        row = self._commodity_rows.get(code)
        if row is None or not isinstance(data, dict):
            return
        price_item = self.commodity_table.item(row, 1)
        chg_item = self.commodity_table.item(row, 2)
        if price_item is None or chg_item is None:
            return
        price_item.setText(_fmt(data.get("price")))
        change_pct = data.get("change_pct")
        chg_item.setText(f"{_fmt(change_pct)}%" if change_pct is not None else "-")
        if change_pct is not None:
            chg_item.setForeground(QColor(UP if change_pct >= 0 else DOWN))


def _bias_color(bias: str) -> str:
    b = (bias or "").strip().lower()
    if b in ("bullish", "long", "net long"):
        return UP
    if b in ("bearish", "short", "net short"):
        return DOWN
    return FG_DIM
