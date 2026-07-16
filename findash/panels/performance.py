"""Performance panel — relative cumulative % return of the linked symbol
against a user-editable comparison set (Bloomberg PERF-style relative chart).

Every series is normalized to 0% at the start of the visible window; the
linked symbol is always series #1, comparison symbols follow in the order
typed into the compare box.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, FG_DIM

# label -> (period, interval) passed straight into history:SYM:PERIOD:INTERVAL
PERIODS = [
    ("1mo", "1mo", "1d"),
    ("6mo", "6mo", "1d"),
    ("1y", "1y", "1d"),
    ("5y", "5y", "1wk"),
]
INTERVAL_OF = {label: interval for label, _, interval in PERIODS}

DEFAULT_COMPARE = ["SPY", "QQQ"]
COLORS = [ACCENT, "#4a90d9", "#7ed321", "#e91e63", "#9b59b6", "#1abc9c"]


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        s = str(s).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


@register_panel(id="performance", title="Performance", category="Analytics")
class PerformancePanel(Panel):
    def build(self) -> None:
        self._period = "6mo"
        self._compare: list[str] = list(DEFAULT_COMPARE)
        self._symbols: list[str] = []          # series[0] is always the linked symbol
        self._history: dict[str, Any] = {}     # symbol -> raw history dict
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._legend_labels: dict[str, QLabel] = {}
        self._period_buttons: dict[str, QPushButton] = {}

        # -- period selector ---------------------------------------------------
        period_row = QHBoxLayout()
        for label, _period, _interval in PERIODS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setFixedWidth(48)
            btn.clicked.connect(lambda _=False, p=label: self._set_period(p))
            period_row.addWidget(btn)
            self._period_buttons[label] = btn
        period_row.addStretch(1)
        self.content_layout.addLayout(period_row)
        self._update_period_buttons()

        # -- comparison list editor --------------------------------------------
        compare_row = QHBoxLayout()
        compare_row.addWidget(QLabel("Compare:", self))
        self.compare_edit = QLineEdit(self)
        self.compare_edit.setText(", ".join(self._compare))
        self.compare_edit.returnPressed.connect(self._apply_compare)
        apply_btn = QPushButton("Apply", self)
        apply_btn.clicked.connect(self._apply_compare)
        compare_row.addWidget(self.compare_edit, 1)
        compare_row.addWidget(apply_btn)
        self.content_layout.addLayout(compare_row)

        # -- legend row (colored symbol + cumulative % labels), above the plot --
        self.legend_row = QHBoxLayout()
        self.content_layout.addLayout(self.legend_row)

        # -- plot -----------------------------------------------------------------
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.plot_widget.setBackground(BG)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        self.plot_widget.getAxis("bottom").setTextPen(FG_DIM)
        zero_pen = pg.mkPen(FG_DIM, width=1, style=Qt.PenStyle.DashLine)
        self.plot_widget.addItem(pg.InfiniteLine(pos=0, angle=0, pen=zero_pen))
        self.content_layout.addWidget(self.plot_widget, 1)

    # -- period selector -----------------------------------------------------

    def _update_period_buttons(self) -> None:
        for label, btn in self._period_buttons.items():
            btn.setChecked(label == self._period)

    def _interval_for(self, period: str) -> str:
        return INTERVAL_OF.get(period, "1d")

    def _set_period(self, period: str) -> None:
        if period == self._period or period not in INTERVAL_OF:
            return
        self._period = period
        self._update_period_buttons()
        self._rebuild_series()

    # -- comparison list editor -----------------------------------------------

    def _apply_compare(self) -> None:
        self._compare = _dedupe(self.compare_edit.text().split(","))
        self.compare_edit.setText(", ".join(self._compare))
        self._rebuild_series()

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self._rebuild_series()

    # -- series (re)construction ------------------------------------------------

    def _recompute_symbols(self) -> list[str]:
        base = [self.current_symbol] if self.current_symbol else []
        return _dedupe(base + self._compare)

    def _rebuild_series(self) -> None:
        self._symbols = self._recompute_symbols()
        self.unsubscribe_all()
        self._history.clear()
        self._clear_plot_items()
        self._clear_legend()

        for i, sym in enumerate(self._symbols):
            color = COLORS[i % len(COLORS)]
            curve = pg.PlotDataItem(pen=pg.mkPen(color, width=2), antialias=True)
            self.plot_widget.addItem(curve)
            self._curves[sym] = curve

            lbl = QLabel(f"{sym}  —", self)
            lbl.setStyleSheet(f"color: {color}; font-weight: bold;")
            self.legend_row.addWidget(lbl)
            self._legend_labels[sym] = lbl
        self.legend_row.addStretch(1)

        if not self._symbols:
            self.set_status("no symbols")
            return
        self.set_status(f"{self._period}")
        for sym in self._symbols:
            topic = f"history:{sym}:{self._period}:{self._interval_for(self._period)}"
            self.subscribe(topic, lambda data, s=sym: self._on_history(s, data))

    def _clear_plot_items(self) -> None:
        for curve in self._curves.values():
            self.plot_widget.removeItem(curve)
        self._curves.clear()

    def _clear_legend(self) -> None:
        while self.legend_row.count():
            item = self.legend_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._legend_labels.clear()

    # -- data callbacks --------------------------------------------------------

    def _on_history(self, symbol: str, data: Any) -> None:
        self._history[symbol] = data
        self._redraw_symbol(symbol)
        self.plot_widget.enableAutoRange()

    def _redraw_symbol(self, symbol: str) -> None:
        curve = self._curves.get(symbol)
        if curve is None:
            return  # comparison set changed since the subscribe fired
        data = self._history.get(symbol)
        xs: list[float] = []
        ys: list[float] = []
        if isinstance(data, dict):
            t = data.get("t") or []
            c = data.get("c") or []
            n = min(len(t), len(c))
            base: Optional[float] = None
            for i in range(n):
                ti, ci = t[i], c[i]
                if ti is None or ci is None:
                    continue
                ci = float(ci)
                if base is None:
                    if ci == 0:
                        continue
                    base = ci
                xs.append(float(ti))
                ys.append((ci / base - 1.0) * 100.0)
        curve.setData(xs, ys)

        lbl = self._legend_labels.get(symbol)
        if lbl is None:
            return
        if ys:
            last_pct = ys[-1]
            sign = "+" if last_pct >= 0 else ""
            lbl.setText(f"{symbol}  {sign}{last_pct:.2f}%")
        else:
            lbl.setText(f"{symbol}  —")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"compare": list(self._compare), "period": self._period}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        compare = settings.get("compare")
        if isinstance(compare, list):
            self._compare = _dedupe(compare)
            self.compare_edit.setText(", ".join(self._compare))

        period = settings.get("period")
        if period in INTERVAL_OF:
            self._period = period
            self._update_period_buttons()

        if self.current_symbol:
            self._rebuild_series()
