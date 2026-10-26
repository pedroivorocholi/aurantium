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

from datetime import datetime
from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton

from ..guide import PanelGuide
from ..commodities_meta import COMMODITIES, CommodityMeta, by_symbol
from ..components import attach_hover, clamp_view
from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, FG_DIM, FONT_SM, tick_color
from .futures_curve import make_commodity_combo
from ..components.responsive_row import ResponsiveRow

#: range-button choices: (label, days back; None = everything)
_RANGES = [("6M", 183), ("1Y", 365), ("All", None)]

#: only commodities with a wired COT market appear in this panel
_COVERED = tuple(m for m in COMMODITIES if m.cftc_market)


def _segments_cross(xs: list, ys: list, box) -> bool:
    """Does the polyline through (xs, ys) enter ``box`` (a QRectF in data
    coordinates)? Segment by segment, so a steep move between two readings
    counts even when neither reading lies inside the box."""
    lo_y, hi_y = sorted((box.top(), box.bottom()))
    for x0, y0, x1, y1 in zip(xs, ys, xs[1:], ys[1:]):
        a, b = max(min(x0, x1), box.left()), min(max(x0, x1), box.right())
        if a > b:
            continue  # this segment is entirely left or right of the box
        if x1 == x0:
            ya, yb = y0, y1
        else:
            slope = (y1 - y0) / (x1 - x0)
            ya, yb = y0 + slope * (a - x0), y0 + slope * (b - x0)
        if max(ya, yb) >= lo_y and min(ya, yb) <= hi_y:
            return True
    return False


@register_panel(
    id="cot_history", title="Speculator Positioning (CFTC)", category="Analytics"
)
class CotHistoryPanel(Panel):
    SKELETON = "plot"
    GUIDE = PanelGuide(
        purpose="How large speculators are positioned in one futures market, week by week (CFTC Commitments of Traders).",
        links="Pick a market in the dropdown and linked panels follow; click a "
        "commodity elsewhere and the chart switches to it.",
        interactions=(
            "**6M**, **1Y** and **All** set the range; hover for any week's reading.",
            "Above the dashed zero line speculators are net long — positioned for "
            "higher prices; below it, net short. The line under the chart gives the "
            "latest reading and its report date.",
        ),
        fields=(
            ("Net contracts", "Long minus short contracts held by large speculators; the axis abbreviates (300K = 300,000)"),
        ),
    )

    def build(self) -> None:
        self._meta: CommodityMeta = _COVERED[0]
        self._syncing = False  # True while the combo is being set from code

        #: loaded weekly series: parallel (epoch seconds, net contracts, date)
        self._times: list[float] = []
        self._nets: list[float] = []
        self._dates: list[str] = []

        # -- market selector + range buttons -----------------------------------
        sel_row = ResponsiveRow()
        self.selector = make_commodity_combo(self, _COVERED)
        self.selector.currentIndexChanged.connect(self._on_select)
        sel_row.addWidget(self.selector)
        sel_row.addStretch(1)
        self._range_group = QButtonGroup(self)
        self._range_days: Optional[int] = None  # None = All
        for i, (label, days) in enumerate(_RANGES):
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setChecked(days is None)
            # the app's one selector style (chart range, Day Brief window):
            # an outline chip, never a filled block — and sized by its label,
            # since a fixed width clipped "6M" to look like "3M"
            btn.setObjectName("chartChip")
            btn.setToolTip(f"Show the last {label}" if days else "Show every report")
            self._range_group.addButton(btn, i)
            sel_row.addWidget(btn)
        self._range_group.idClicked.connect(self._on_range)
        self.content_layout.addLayout(sel_row)

        # -- history plot (real weekly date axis) ------------------------------
        from ..components.plot_utils import CompactAxis

        # 300K, not 300000: the axis matches the +201,579 readout's magnitude
        # without printing six bare digits per tick
        self.plot_widget = pg.PlotWidget(
            axisItems={
                "bottom": pg.DateAxisItem(orientation="bottom"),
                "left": CompactAxis(orientation="left"),
            }
        )
        self.plot_widget.setBackground(BG)
        self.plot_widget.setMenuEnabled(False)  # app menu (with Quick guide), not pyqtgraph's own
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
        self.latest_marker = pg.ScatterPlotItem(
            size=7, brush=pg.mkBrush(ACCENT), pen=pg.mkPen(ACCENT)
        )
        self.plot_widget.addItem(self.latest_marker)
        self.latest_txt = pg.TextItem(color=ACCENT, anchor=(1, 1))
        self.plot_widget.addItem(self.latest_txt, ignoreBounds=True)
        attach_hover(self.plot_widget, self._hover_text)
        self.plot_widget.getViewBox().sigRangeChanged.connect(self._place_tag)
        self.plot_widget.getViewBox().sigResized.connect(self._place_tag)
        self.content_layout.addWidget(self.plot_widget, 1)

        # -- latest reading + plain-English caption ----------------------------
        self.latest_lbl = QLabel("", self)
        self.latest_lbl.setWordWrap(True)  # wraps in a narrow dock, never clips
        self.content_layout.addWidget(self.latest_lbl)

        caption = QLabel(
            "Each week the CFTC reports what large speculative traders (money "
            "managers, hedge funds) hold in this market's futures. Above the "
            "dashed zero line they are net long — positioned for higher prices. "
            "Below it, net short — positioned for lower prices.",
            self,
        )
        caption.setWordWrap(True)
        caption.setObjectName("secondary")
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
        self.latest_marker.setData([], [])
        self.latest_txt.setText("")
        self._times, self._nets, self._dates = [], [], []
        self.latest_lbl.setText("")
        self.plot_widget.setTitle(meta.label, color=FG_DIM, size=f"{FONT_SM}px")
        self.set_loading(True)
        self.subscribe(f"cftc:{meta.cftc_market}", self._on_cftc)

    # -- data ------------------------------------------------------------------

    def _on_cftc(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        if not isinstance(data, dict) or data.get("market") != self._meta.cftc_market:
            return
        history = data.get("history") or []
        times: list[float] = []
        nets: list[float] = []
        dates: list[str] = []
        for row in history:
            if not isinstance(row, (list, tuple)) or len(row) < 2 or row[1] is None:
                continue
            date_str = str(row[0])[:10]
            try:
                ts = datetime.strptime(date_str, "%Y-%m-%d").timestamp()
            except ValueError:
                continue
            times.append(ts)
            dates.append(date_str)
            nets.append(float(row[1]))
        self._times, self._nets, self._dates = times, nets, dates
        self.series.setData(times, nets)
        if times:
            self.latest_marker.setData([times[-1]], [nets[-1]])
            self.latest_txt.setText(f"{nets[-1]:+,.0f}")
            self.latest_txt.setPos(times[-1], nets[-1])
        clamp_view(self.plot_widget, times, nets + [0.0])
        self._apply_range()
        self._place_tag()

        net = data.get("noncommercial_net")
        if net is not None:
            direction = "net long" if net >= 0 else "net short"
            report_date = str(data.get("report_date") or "")[:10]
            self.latest_lbl.setText(
                f"Latest: {net:+,.0f} contracts ({direction}) · report dated {report_date}"
            )
            self.latest_lbl.setStyleSheet(
                f"color: {tick_color(net)}; font-weight: 700;"
            )
        self.set_status(f"{len(nets)} weekly reports")

    # -- view range & hover ----------------------------------------------------

    def _on_range(self, button_id: int) -> None:
        self._range_days = _RANGES[button_id][1]
        self._apply_range()

    def _apply_range(self) -> None:
        if not self._times:
            return
        vb = self.plot_widget.getViewBox()
        if self._range_days is None:
            vb.autoRange()
            return
        cutoff = self._times[-1] - self._range_days * 86400.0
        start = max(cutoff, self._times[0])
        vb.setAutoVisible(y=True)
        vb.enableAutoRange(axis=vb.YAxis)
        self.plot_widget.setXRange(start, self._times[-1], padding=0.02)

    def _hover_text(self, x: float, _y: float) -> Optional[str]:
        """Snap to the nearest weekly report — 'week of 2026-03-17 · +187,450
        contracts'."""
        if not self._times:
            return None
        nearest = min(range(len(self._times)), key=lambda i: abs(self._times[i] - x))
        # ignore hovers far outside the data (more than ~2 weeks off)
        if abs(self._times[nearest] - x) > 14 * 86400.0:
            return None
        return f"week of {self._dates[nearest]} · {self._nets[nearest]:+,.0f} contracts"

    # -- linked-symbol behavior -------------------------------------------------

    #: where the end-value tag may sit, tried in order: below-left of the
    #: last point, then above-left
    _TAG_ANCHORS = ((1, -0.15), (1, 1.15))

    def _place_tag(self, *_args) -> None:
        """Put the end-value tag where the series doesn't run.

        The tag sat up-left of the last point, which is exactly where the
        line arrives from when it falls into that point, and in a small dock
        any fixed side collided with the line. Each candidate box is tested
        against the plotted points in screen space; when both sides collide
        the tag is hidden, since the "Latest: …" line under the chart prints
        the same figure. Re-run on every zoom and resize, because the box is
        a fixed pixel size and the data under it changes with the scale.
        """
        txt = self.latest_txt
        times, nets = getattr(self, "_times", []), getattr(self, "_nets", [])
        if not times or not txt.toPlainText():
            txt.setVisible(False)
            return
        # The tag's box in data units, from its pixel size and the view's
        # scale. (Mapping the TextItem's own rect reads a transform pyqtgraph
        # only updates at paint time, so it lagged a resize.)
        px, py = self.plot_widget.getViewBox().viewPixelSize()
        rect = txt.textItem.boundingRect()
        w, h = rect.width() * px, rect.height() * py
        x, y = times[-1], nets[-1]
        for anchor in self._TAG_ANCHORS:
            # anchor (1, a) puts the point a text-heights below the text's top
            # edge, so in data units (y grows upward) the top edge is at
            # y + a*h: a < 0 hangs the tag below the point, a > 1 lifts it above
            top = y + anchor[1] * h
            box = QRectF(x - w, top - h, w, h)
            txt.setAnchor(anchor)
            txt.setPos(x, y)
            if not _segments_cross(times, nets, box):
                txt.setVisible(True)
                return
        txt.setVisible(False)

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
