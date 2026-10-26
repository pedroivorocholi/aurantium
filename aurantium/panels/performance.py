"""Performance panel — relative cumulative % return of the linked symbol
against a user-editable comparison set (Bloomberg PERF-style relative chart).

Every series is normalized to 0% at the start of the visible window; the
linked symbol is always series #1, comparison symbols follow in the order
typed into the compare box.
"""

from __future__ import annotations

from typing import Any, Optional

import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton

from ..guide import PanelGuide
from ..components import attach_suggestions
from ..panel import EMPTY_NO_SYMBOL, EMPTY_NO_SYMBOL_HINT, NULL_GLYPH, Panel, register_panel
from ..theme import ACCENT, BG, FG, FG_DIM, series_color
from ..components.responsive_row import ResponsiveRow

# label -> (period, interval) passed straight into history:SYM:PERIOD:INTERVAL
PERIODS = [
    ("1mo", "1mo", "1d"),
    ("6mo", "6mo", "1d"),
    ("1y", "1y", "1d"),
    ("5y", "5y", "1wk"),
]
INTERVAL_OF = {label: interval for label, _, interval in PERIODS}

DEFAULT_COMPARE = ["SPY", "QQQ"]


def _series_color(index: int) -> str:
    """The pen colour for series ``index``.

    Series 0 is the linked symbol — the subject the panel exists to show — so
    it wears ACCENT, the same amber that means "this is the instrument"
    everywhere else. The comparisons are genuine categorical identity and take
    the shared series palette.

    This replaced a hand-picked list, ``[ACCENT, "#4a90d9", "#7ed321",
    "#e91e63", "#9b59b6", "#1abc9c"]``, which was theme-blind and failed the
    project's own checks in both themes — ``#7ed321`` measured 1.87:1 against
    the light theme's white canvas, which is a line you cannot see.
    """
    return ACCENT if index == 0 else series_color(index - 1)


def _legend_html(symbol: str, color: str, last_pct: float | None) -> str:
    """One legend entry: a colour swatch, then the symbol and its return.

    The swatch carries identity; the text stays in the ordinary foreground. The
    symbol name used to be painted in the series colour itself, which spends the
    only channel that distinguishes the series on text that already says which
    series it is — and drags legibility down to whatever the line colour happens
    to measure against the panel background.
    """
    value = NULL_GLYPH if last_pct is None else f"{last_pct:+.2f}%"
    return (
        f'<span style="color:{color}">■</span> '
        f'<span style="color:{FG}"><b>{symbol}</b>&nbsp;&nbsp;{value}</span>'
    )


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
    SKELETON = "plot"
    GUIDE = PanelGuide(
        purpose="The linked symbol's return against a comparison set, from the start of the window.",
        links="Follows the link group's symbol, which is always the first line.",
        interactions=(
            "**1mo / 6mo / 1y / 5y** set the window; every line starts at 0%.",
            "Type symbols in **Compare**, separated by commas, and press **Apply** or Enter.",
            "The legend above the chart prints each line's total return.",
        ),
    )

    def build(self) -> None:
        self._period = "6mo"
        self._compare: list[str] = list(DEFAULT_COMPARE)
        self._symbols: list[str] = []          # series[0] is always the linked symbol
        self._history: dict[str, Any] = {}     # symbol -> raw history dict
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._legend_labels: dict[str, QLabel] = {}
        self._legend_colors: dict[str, str] = {}
        self._period_buttons: dict[str, QPushButton] = {}

        # -- period selector ---------------------------------------------------
        period_row = ResponsiveRow()
        for label, _period, _interval in PERIODS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            # the app's one selector style (chart range, Day Brief window):
            # an outline chip, never a filled block — and sized by its label,
            # since a fixed width clipped "6M" to look like "3M"
            btn.setObjectName("chartChip")
            btn.clicked.connect(lambda _=False, p=label: self._set_period(p))
            period_row.addWidget(btn)
            self._period_buttons[label] = btn
        period_row.addStretch(1)
        self.content_layout.addLayout(period_row)
        self._update_period_buttons()

        # -- comparison list editor --------------------------------------------
        compare_row = ResponsiveRow()
        compare_row.addWidget(QLabel("Compare:", self))
        self.compare_edit = QLineEdit(self)
        self.compare_edit.setText(", ".join(self._compare))
        self.compare_edit.returnPressed.connect(self._apply_compare)
        attach_suggestions(self.compare_edit, token_separator=",")
        apply_btn = QPushButton("Apply", self)
        apply_btn.clicked.connect(self._apply_compare)
        compare_row.addWidget(self.compare_edit, 1)
        compare_row.addWidget(apply_btn)
        self.content_layout.addLayout(compare_row)

        # -- legend row (colored symbol + cumulative % labels), above the plot --
        self.legend_row = ResponsiveRow()
        self.content_layout.addLayout(self.legend_row)

        # -- plot -----------------------------------------------------------------
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.plot_widget.setMenuEnabled(False)  # app menu (with Quick guide), not pyqtgraph's own
        self.plot_widget.setBackground(BG)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        # the figures are % returns; say so, as Macro's curve says "Yield (%)"
        self.plot_widget.setLabel("left", "Return (%)")
        self.plot_widget.getAxis("bottom").setTextPen(FG_DIM)
        zero_pen = pg.mkPen(FG_DIM, width=1, style=Qt.PenStyle.DashLine)
        self._zero_line = pg.InfiniteLine(pos=0, angle=0, pen=zero_pen)
        self.plot_widget.addItem(self._zero_line)
        self.content_layout.addWidget(self.plot_widget, 1)

        # Until a symbol arrives the plot had nothing to say but showed it
        # anyway: bare axes from -400 to 400. Say what is missing instead,
        # with the same overlay the Chart uses.
        from ..components.empty_state import EmptyState

        self._empty = EmptyState(
            self.plot_widget.viewport(), EMPTY_NO_SYMBOL, EMPTY_NO_SYMBOL_HINT
        )
        self.plot_widget.viewport().installEventFilter(self)
        self._set_plot_empty(True)

    def _set_plot_empty(self, empty: bool) -> None:
        """With nothing to plot, hide the axes and the zero line too, as the
        Chart does: otherwise they measure nothing (-400..400) and the zero
        line strikes through the message."""
        item = self.plot_widget.getPlotItem()
        for side in ("left", "bottom"):
            item.showAxis(side, not empty)
        self._zero_line.setVisible(not empty)
        self._empty.set_shown(empty)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.plot_widget.viewport() and event.type() == QEvent.Type.Resize:
            self._empty.setGeometry(obj.rect())
        return super().eventFilter(obj, event)

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
        self._set_plot_empty(False)
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
            color = _series_color(i)
            curve = pg.PlotDataItem(pen=pg.mkPen(color, width=2), antialias=True)
            self.plot_widget.addItem(curve)
            self._curves[sym] = curve

            lbl = QLabel(self)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            self._legend_colors[sym] = color
            lbl.setText(_legend_html(sym, color, None))
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
        self._legend_colors.clear()

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
        lbl.setText(
            _legend_html(
                symbol,
                self._legend_colors.get(symbol, ACCENT),
                ys[-1] if ys else None,
            )
        )

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
