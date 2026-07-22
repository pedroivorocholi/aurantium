"""Futures Curve panel — the term structure of a commodity's futures: price
of each upcoming delivery month, with a plain-English readout of the curve's
slope (contango vs. backwardation).

Covers every commodity in ``commodities_meta`` (metals, energy, agriculture),
picked from a grouped dropdown. Picking a commodity drives the panel's link
group, and a linked symbol that maps to a covered commodity (``GC=F``,
``NGU26.NYM``, …) re-centers the curve — the "everything links" behavior.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from ..commodities_meta import (
    CATEGORIES,
    COMMODITIES,
    CommodityMeta,
    by_symbol,
    contract_symbols,
)
from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, FG, FG_DIM

CONTRACT_COUNT = 8


def make_commodity_combo(parent, commodities) -> QComboBox:
    """A commodity dropdown grouped by category (disabled header items).
    Data role holds the commodity root. Shared by the curve and positioning
    panels so both selectors look identical."""
    combo = QComboBox(parent)
    for cat in CATEGORIES:
        in_cat = [m for m in commodities if m.category == cat]
        if not in_cat:
            continue
        combo.addItem(f"—— {cat} ——")
        combo.model().item(combo.count() - 1).setEnabled(False)
        for meta in in_cat:
            combo.addItem(meta.label, meta.root)
    return combo


@register_panel(id="futures_curve", title="Futures Curve", category="Markets")
class FuturesCurvePanel(Panel):
    def build(self) -> None:
        self._meta: CommodityMeta = COMMODITIES[0]
        # (yahoo symbol, "Mon YY" label) pairs for the active commodity
        self._contracts: list[tuple[str, str]] = []
        self._prices: dict[str, Optional[float]] = {}
        self._syncing = False  # True while the combo is being set from code

        # -- commodity selector: grouped dropdown ------------------------------
        sel_row = QHBoxLayout()
        self.selector = make_commodity_combo(self, COMMODITIES)
        self.selector.currentIndexChanged.connect(self._on_select)
        sel_row.addWidget(self.selector)
        sel_row.addStretch(1)
        self.content_layout.addLayout(sel_row)

        # -- curve plot -------------------------------------------------------
        self.curve_widget = pg.PlotWidget()
        self.curve_widget.setBackground(BG)
        self.curve_widget.showGrid(x=True, y=True, alpha=0.15)
        self.curve_widget.setLabel("left", "Price")
        self.curve_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.curve_widget.getAxis("left").setTextPen(FG_DIM)
        self.curve = pg.PlotDataItem(
            pen=pg.mkPen(ACCENT, width=2),
            symbol="o",
            symbolBrush=ACCENT,
            symbolPen=ACCENT,
            symbolSize=7,
        )
        self.curve_widget.addItem(self.curve)
        self.content_layout.addWidget(self.curve_widget, 1)

        # -- slope readout: descriptive, plain English ------------------------
        self.slope_lbl = QLabel("", self)
        self.slope_lbl.setWordWrap(True)
        self.slope_lbl.setStyleSheet(f"color: {FG};")
        self.content_layout.addWidget(self.slope_lbl)

        self._set_commodity(self._meta)

    # -- commodity switching ------------------------------------------------

    def _on_select(self, _index: int) -> None:
        if self._syncing:
            return
        root = self.selector.currentData()
        meta = next((m for m in COMMODITIES if m.root == root), None)
        if meta is None or meta.root == self._meta.root:
            return
        self._set_commodity(meta)
        # drive linked panels to this commodity's continuous symbol
        self.set_symbol(meta.symbol)

    def _set_commodity(self, meta: CommodityMeta) -> None:
        self._meta = meta
        self._syncing = True
        try:
            pos = self.selector.findData(meta.root)
            if pos >= 0:
                self.selector.setCurrentIndex(pos)
        finally:
            self._syncing = False

        self.unsubscribe_all()
        self._contracts = contract_symbols(meta, CONTRACT_COUNT)
        self._prices = {sym: None for sym, _label in self._contracts}
        axis = self.curve_widget.getAxis("bottom")
        axis.setTicks([[(i, label) for i, (_s, label) in enumerate(self._contracts)]])
        self.curve_widget.setTitle(meta.label, color=FG_DIM, size="9pt")
        self._redraw()
        for sym, _label in self._contracts:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    # -- data ------------------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        if symbol not in self._prices or not isinstance(data, dict):
            return
        self._prices[symbol] = data.get("price")
        self._redraw()

    def _redraw(self) -> None:
        xs: list[float] = []
        ys: list[float] = []
        for i, (sym, _label) in enumerate(self._contracts):
            p = self._prices.get(sym)
            if p is None:
                continue
            xs.append(float(i))
            ys.append(float(p))
        self.curve.setData(xs, ys)
        loaded = len(xs)
        self.set_status(f"contracts {loaded}/{len(self._contracts)}")
        self._update_slope(xs, ys)

    def _update_slope(self, xs: list[float], ys: list[float]) -> None:
        """Describe the curve's shape in plain words: front month vs. a
        contract ~3 delivery months out (or the farthest available)."""
        if len(ys) < 2:
            self.slope_lbl.setText("Waiting for contract prices…")
            self.slope_lbl.setStyleSheet(f"color: {FG_DIM};")
            return
        front_i, front_p = xs[0], ys[0]
        later_idx = next((k for k in range(len(xs)) if xs[k] >= front_i + 3), len(xs) - 1)
        later_i, later_p = xs[later_idx], ys[later_idx]
        front_label = self._contracts[int(front_i)][1]
        later_label = self._contracts[int(later_i)][1]
        pct = (later_p - front_p) / front_p * 100.0 if front_p else 0.0
        if later_p > front_p:
            shape = "Upward-sloping (contango): later deliveries cost more than nearer ones"
        elif later_p < front_p:
            shape = "Downward-sloping (backwardation): nearer deliveries cost more than later ones"
        else:
            shape = "Flat: nearer and later deliveries cost about the same"
        self.slope_lbl.setStyleSheet(f"color: {FG};")
        self.slope_lbl.setText(f"{shape} — {front_label} vs {later_label}: {pct:+.1f}%")

    # -- linked-symbol behavior -------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        # a linked symbol only re-centers the curve when it maps to a covered
        # commodity; anything else (AAPL, EURUSD=X) is ignored
        meta = by_symbol(symbol)
        if meta is not None and meta.root != self._meta.root:
            self._set_commodity(meta)

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"root": self._meta.root}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        root = settings.get("root")
        for meta in COMMODITIES:
            if meta.root == root:
                self._set_commodity(meta)
                return
