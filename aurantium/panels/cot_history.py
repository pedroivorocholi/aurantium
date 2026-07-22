"""Speculator Positioning panel — a weekly time series of CFTC Commitments
of Traders net speculative positioning for one commodity, from the same
keyless ``cftc:`` topic the Macro/Rates panel uses (the provider ships ~2.3
years of history in the payload).

Covers every commodity in ``commodities_meta`` that has a COT market, picked
from the same grouped dropdown as the Futures Curve panel. Picking a market
drives the panel's link group; a linked symbol that maps to a covered
commodity switches the chart.
"""

from __future__ import annotations

from typing import Any

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel

from ..commodities_meta import COMMODITIES, CommodityMeta, by_symbol
from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, FG_DIM, tick_color
from .futures_curve import make_commodity_combo

#: only commodities with a wired COT market appear in this panel
_COVERED = tuple(m for m in COMMODITIES if m.cftc_market)


@register_panel(
    id="cot_history", title="Speculator Positioning (CFTC)", category="Analytics"
)
class CotHistoryPanel(Panel):
    def build(self) -> None:
        self._meta: CommodityMeta = _COVERED[0]
        self._syncing = False  # True while the combo is being set from code

        # -- market selector: grouped dropdown ---------------------------------
        sel_row = QHBoxLayout()
        self.selector = make_commodity_combo(self, _COVERED)
        self.selector.currentIndexChanged.connect(self._on_select)
        sel_row.addWidget(self.selector)
        sel_row.addStretch(1)
        self.content_layout.addLayout(sel_row)

        # -- history plot ------------------------------------------------------
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground(BG)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLabel("left", "Net contracts")
        self.plot_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        self.plot_widget.addItem(
            pg.InfiniteLine(
                pos=0, angle=0, pen=pg.mkPen(FG_DIM, style=Qt.PenStyle.DashLine)
            )
        )
        self.series = pg.PlotDataItem(pen=pg.mkPen(ACCENT, width=2))
        self.plot_widget.addItem(self.series)
        self.content_layout.addWidget(self.plot_widget, 1)

        # -- latest reading + plain-English caption ----------------------------
        self.latest_lbl = QLabel("", self)
        self.content_layout.addWidget(self.latest_lbl)

        caption = QLabel(
            "Each week the CFTC reports what large speculative traders (money "
            "managers, hedge funds) hold in this market's futures. Above the "
            "dashed zero line they are net long — positioned for higher prices. "
            "Below it, net short — positioned for lower prices.",
            self,
        )
        caption.setWordWrap(True)
        caption.setStyleSheet(f"color: {FG_DIM};")
        self.content_layout.addWidget(caption)

        self._set_market(self._meta)

    # -- market switching -----------------------------------------------------

    def _on_select(self, _index: int) -> None:
        if self._syncing:
            return
        root = self.selector.currentData()
        meta = next((m for m in _COVERED if m.root == root), None)
        if meta is None or meta.root == self._meta.root:
            return
        self._set_market(meta)
        # drive linked panels to this commodity's continuous symbol
        self.set_symbol(meta.symbol)

    def _set_market(self, meta: CommodityMeta) -> None:
        self._meta = meta
        self._syncing = True
        try:
            pos = self.selector.findData(meta.root)
            if pos >= 0:
                self.selector.setCurrentIndex(pos)
        finally:
            self._syncing = False

        self.unsubscribe_all()
        self.series.setData([], [])
        self.latest_lbl.setText("")
        self.plot_widget.setTitle(meta.label, color=FG_DIM, size="9pt")
        self.set_status("loading…")
        self.subscribe(f"cftc:{meta.cftc_market}", self._on_cftc)

    # -- data ------------------------------------------------------------------

    def _on_cftc(self, data: Any) -> None:
        if not isinstance(data, dict) or data.get("market") != self._meta.cftc_market:
            return
        history = data.get("history") or []
        dates: list[str] = []
        nets: list[float] = []
        for row in history:
            if not isinstance(row, (list, tuple)) or len(row) < 2 or row[1] is None:
                continue
            dates.append(str(row[0])[:10])
            nets.append(float(row[1]))
        self.series.setData(list(range(len(nets))), nets)

        # a date label roughly every quarter, so the axis stays readable
        axis = self.plot_widget.getAxis("bottom")
        step = max(1, len(dates) // 8)
        axis.setTicks([[(i, dates[i][:7]) for i in range(0, len(dates), step)]])

        net = data.get("noncommercial_net")
        if net is not None:
            direction = "net long" if net >= 0 else "net short"
            report_date = str(data.get("report_date") or "")[:10]
            self.latest_lbl.setText(
                f"Latest: {net:+,.0f} contracts ({direction}) · report dated {report_date}"
            )
            self.latest_lbl.setStyleSheet(
                f"color: {tick_color(net)}; font-weight: bold;"
            )
        self.set_status(f"{len(nets)} weekly reports")

    # -- linked-symbol behavior -------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        # a linked symbol only switches the chart when it maps to a covered
        # commodity; anything else is ignored
        meta = by_symbol(symbol)
        if (
            meta is not None
            and meta.cftc_market
            and meta.cftc_market != self._meta.cftc_market
        ):
            self._set_market(meta)

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"market": self._meta.cftc_market}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        market = settings.get("market")
        for meta in _COVERED:
            if meta.cftc_market == market:
                self._set_market(meta)
                return
