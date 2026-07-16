"""Chart panel — candlestick price history with a period selector, moving
averages, and an RSI sub-panel (Bloomberg G-chart style)."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter, QPicture
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QPushButton

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, BG_ELEV, DOWN, FG, FG_DIM, MONO_FONT, UP

# label -> (period, interval) passed straight into the history:SYM:PERIOD:INTERVAL topic
PERIODS = [
    ("1d", "1d", "5m"),
    ("5d", "5d", "30m"),
    ("1mo", "1mo", "1d"),
    ("3mo", "3mo", "1d"),
    ("6mo", "6mo", "1d"),
    ("1y", "1y", "1d"),
    ("5y", "5y", "1wk"),
    ("max", "max", "1mo"),
]
INTERVAL_OF = {label: interval for label, _, interval in PERIODS}

# Calendar span (days) of each display period — frames the visible window after
# we fetch extra history so long moving averages have enough lookback.
PERIOD_SPAN_DAYS = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186,
    "1y": 372, "5y": 1860, "max": 100000,
}
# Approx trading days each daily-interval display period actually shows.
_TRADING_DAYS = {"1mo": 21, "3mo": 63, "6mo": 126, "1y": 252}
# Daily-interval fetch ladder: (period, approx trading days it yields). We pick
# the smallest that covers the visible window PLUS the longest active SMA, so a
# 200-day average is populated even when the user is looking at 6 months.
_DAILY_FETCH_LADDER = [
    ("6mo", 126), ("1y", 252), ("2y", 504), ("5y", 1260), ("max", 100000),
]

SMA_WINDOWS = (50, 100, 200)
SMA_COLORS = {50: "#e91e63", 100: "#4a90d9", 200: "#f8e71c"}
RSI_WINDOW = 14


def _sma(values: list, window: int) -> list:
    """Simple moving average; caller guarantees len(values) >= window."""
    arr = np.asarray(values, dtype=float)
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="valid").tolist()


def _wilder_rsi(values: list, window: int = RSI_WINDOW) -> Optional[list]:
    """Wilder's RSI(window). Returns None if not enough bars, else a list
    aligned to values[window:]."""
    n = len(values)
    if n < window + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, n)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window

    def _rsi_of(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    out = [_rsi_of(avg_gain, avg_loss)]
    for i in range(window, len(gains)):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        out.append(_rsi_of(avg_gain, avg_loss))
    return out


class CandlestickItem(pg.GraphicsObject):
    """Minimal OHLC candlestick item — standard pyqtgraph QPicture pattern.

    Body is a filled rect from open to close, wick is a line from low to
    high; both colored UP/DOWN by whether the candle closed above its open.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[float, float, float, float, float]] = []
        self._picture = QPicture()

    def set_ohlc(self, t: list, o: list, h: list, l: list, c: list) -> None:
        rows = []
        n = min(len(t), len(o), len(h), len(l), len(c))
        for i in range(n):
            ti, oi, hi, li, ci = t[i], o[i], h[i], l[i], c[i]
            if None in (ti, oi, hi, li, ci):
                continue
            rows.append((float(ti), float(oi), float(hi), float(li), float(ci)))
        self._rows = rows
        self._generate()

    def _generate(self) -> None:
        self.prepareGeometryChange()
        self._picture = QPicture()
        painter = QPainter(self._picture)
        if len(self._rows) >= 2:
            diffs = sorted(
                self._rows[i + 1][0] - self._rows[i][0] for i in range(len(self._rows) - 1)
            )
            diffs = [d for d in diffs if d > 0]
            spacing = diffs[len(diffs) // 2] if diffs else 86400.0
        else:
            spacing = 86400.0
        half_width = spacing * 0.35
        up_brush = pg.mkBrush(UP)
        down_brush = pg.mkBrush(DOWN)
        for t, o, h, l, c in self._rows:
            is_up = c >= o
            painter.setPen(pg.mkPen(UP if is_up else DOWN))
            painter.drawLine(QPointF(t, l), QPointF(t, h))
            painter.setBrush(up_brush if is_up else down_brush)
            body_h = (c - o) or (max(abs(o), 1.0) * 1e-4)
            painter.drawRect(QRectF(t - half_width, o, half_width * 2, body_h))
        painter.end()
        self.update()

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return QRectF(self._picture.boundingRect())


@register_panel(id="chart", title="Chart", category="Markets")
class ChartPanel(Panel):
    def build(self) -> None:
        self._period = "6mo"
        self._last_quote: dict = {}
        self._period_buttons: dict[str, QPushButton] = {}
        self._hist_t: list = []
        self._hist_c: list = []
        self._hist_hi: list = []
        self._hist_lo: list = []

        # defaults matching the user's Bloomberg G-chart setup: SMA50 + SMA200
        # + RSI(14) on, SMA100 off to reduce clutter.
        self._sma50_on = True
        self._sma100_on = False
        self._sma200_on = True
        self._rsi_on = True

        # view options (also settable from the right-click menu)
        self._chart_type = "candles"  # "candles" | "line"
        self._grid_on = True
        self._log_on = False

        # -- title row: ticker · price · change ------------------------------
        title_row = QHBoxLayout()
        title_row.setContentsMargins(2, 2, 2, 2)
        title_row.setSpacing(10)
        self.title_lbl = QLabel("—", self)
        tf = QFont()
        tf.setPointSize(15)
        tf.setBold(True)
        self.title_lbl.setFont(tf)
        self.title_lbl.setStyleSheet(f"color: {ACCENT};")
        self.price_lbl = QLabel("", self)
        pf = QFont(MONO_FONT)
        pf.setPointSize(14)
        self.price_lbl.setFont(pf)
        self.price_lbl.setStyleSheet(f"color: {FG};")
        self.chg_lbl = QLabel("", self)
        cf = QFont(MONO_FONT)
        cf.setPointSize(12)
        cf.setBold(True)
        self.chg_lbl.setFont(cf)
        title_row.addWidget(self.title_lbl)
        title_row.addWidget(self.price_lbl)
        title_row.addWidget(self.chg_lbl)
        title_row.addStretch(1)
        self.content_layout.addLayout(title_row)

        # -- period selector ---------------------------------------------------
        period_row = QHBoxLayout()
        period_row.setSpacing(6)
        range_lbl = QLabel("RANGE", self)
        range_lbl.setStyleSheet(
            "color: #565d67; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
        )
        period_row.addWidget(range_lbl)
        for label, _period, _interval in PERIODS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setFixedWidth(50)
            btn.clicked.connect(lambda _=False, p=label: self._set_period(p))
            period_row.addWidget(btn)
            self._period_buttons[label] = btn
        period_row.addStretch(1)
        self.content_layout.addLayout(period_row)
        self._update_period_buttons()

        # -- overlay toggles: each SMA button wears its own line color when
        # active, so it doubles as the legend — no separate legend row needed.
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(6)
        overlays_lbl = QLabel("OVERLAYS", self)
        overlays_lbl.setStyleSheet(
            "color: #565d67; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
        )
        toggle_row.addWidget(overlays_lbl)
        self._sma_buttons: dict[int, QPushButton] = {}
        for w in SMA_WINDOWS:
            btn = QPushButton(f"SMA {w}", self)
            btn.setCheckable(True)
            toggle_row.addWidget(btn)
            self._sma_buttons[w] = btn
        self._rsi_button = QPushButton("RSI", self)
        self._rsi_button.setCheckable(True)
        toggle_row.addWidget(self._rsi_button)
        toggle_row.addStretch(1)
        self.content_layout.addLayout(toggle_row)

        # -- price plot --------------------------------------------------------
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.plot_widget.setBackground(BG)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        self.plot_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.candle_item = CandlestickItem()
        self.plot_widget.addItem(self.candle_item)
        # line/area alternative to candlesticks (toggled from the right-click menu)
        self.line_curve = pg.PlotDataItem(
            pen=pg.mkPen(ACCENT, width=1.5), antialias=True
        )
        self.line_curve.setZValue(5)
        self.plot_widget.addItem(self.line_curve)
        self.line_curve.setVisible(False)

        self.sma_curves: dict[int, pg.PlotDataItem] = {}
        for w in SMA_WINDOWS:
            curve = pg.PlotDataItem(pen=pg.mkPen(SMA_COLORS[w], width=1), antialias=True)
            curve.setZValue(10)
            self.plot_widget.addItem(curve)
            self.sma_curves[w] = curve
        self.content_layout.addWidget(self.plot_widget, 3)

        # right-click settings menu on the chart
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.plot_widget.customContextMenuRequested.connect(self._show_chart_menu)

        # -- RSI sub-panel -------------------------------------------------------
        self.rsi_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.rsi_widget.setBackground(BG)
        self.rsi_widget.showGrid(x=True, y=True, alpha=0.15)
        self.rsi_widget.getAxis("left").setTextPen(FG_DIM)
        self.rsi_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.rsi_widget.setYRange(0, 100)
        self.rsi_widget.setXLink(self.plot_widget)
        dash_pen = pg.mkPen(FG_DIM, width=1, style=Qt.PenStyle.DashLine)
        self.rsi_widget.addItem(pg.InfiniteLine(pos=30, angle=0, pen=dash_pen))
        self.rsi_widget.addItem(pg.InfiniteLine(pos=70, angle=0, pen=dash_pen))
        self.rsi_curve = pg.PlotDataItem(pen=pg.mkPen(ACCENT, width=1), antialias=True)
        self.rsi_widget.addItem(self.rsi_curve)
        self.content_layout.addWidget(self.rsi_widget, 1)

        # wire toggle buttons now that the plot items they drive exist
        for w, btn in self._sma_buttons.items():
            btn.setChecked(getattr(self, f"_sma{w}_on"))
            btn.toggled.connect(lambda checked, w=w: self._on_sma_toggled(w, checked))
        self._rsi_button.setChecked(self._rsi_on)
        self._rsi_button.toggled.connect(self._on_rsi_toggled)
        self.rsi_widget.setVisible(self._rsi_on)
        self._update_legend()

    # -- period selector -----------------------------------------------------

    def _update_period_buttons(self) -> None:
        for label, btn in self._period_buttons.items():
            btn.setChecked(label == self._period)

    def _set_period(self, period: str) -> None:
        if period == self._period:
            return
        self._period = period
        self._update_period_buttons()
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    def _interval_for(self, period: str) -> str:
        return INTERVAL_OF.get(period, "1d")

    # -- MA / RSI toggles ------------------------------------------------------

    def _on_sma_toggled(self, window: int, checked: bool) -> None:
        setattr(self, f"_sma{window}_on", checked)
        self._update_legend()
        # Turning on a long average may need more history than the current
        # window fetched — re-request so e.g. SMA200 is populated at 6mo.
        if checked and self.current_symbol:
            self._resubscribe(self.current_symbol)
        else:
            self._update_overlays()

    def _on_rsi_toggled(self, checked: bool) -> None:
        self._rsi_on = checked
        self.rsi_widget.setVisible(checked)
        self._update_overlays()
        self._update_legend()

    # -- right-click settings menu --------------------------------------------

    def _show_chart_menu(self, pos) -> None:
        self._build_chart_menu().exec(self.plot_widget.mapToGlobal(pos))

    def _build_chart_menu(self) -> QMenu:
        menu = QMenu(self.plot_widget)

        type_menu = menu.addMenu("Chart type")
        grp = QActionGroup(type_menu)
        grp.setExclusive(True)
        for key, label in (("candles", "Candlesticks"), ("line", "Line / area")):
            act = QAction(label, type_menu, checkable=True)
            act.setChecked(self._chart_type == key)
            act.triggered.connect(lambda _=False, k=key: self._set_chart_type(k))
            grp.addAction(act)
            type_menu.addAction(act)

        menu.addSeparator()
        for w in SMA_WINDOWS:
            act = QAction(f"SMA {w}", menu, checkable=True)
            act.setChecked(getattr(self, f"_sma{w}_on"))
            act.triggered.connect(
                lambda checked, ww=w: self._sma_buttons[ww].setChecked(checked)
            )
            menu.addAction(act)
        rsi_act = QAction("RSI (14)", menu, checkable=True)
        rsi_act.setChecked(self._rsi_on)
        rsi_act.triggered.connect(self._rsi_button.setChecked)
        menu.addAction(rsi_act)

        menu.addSeparator()
        grid_act = QAction("Grid", menu, checkable=True)
        grid_act.setChecked(self._grid_on)
        grid_act.triggered.connect(self._toggle_grid)
        menu.addAction(grid_act)
        log_act = QAction("Log scale (Y)", menu, checkable=True)
        log_act.setChecked(self._log_on)
        log_act.triggered.connect(self._toggle_log)
        menu.addAction(log_act)

        menu.addSeparator()
        reset_act = QAction("Reset zoom", menu)
        reset_act.triggered.connect(
            lambda: self._frame_window(self._hist_t, self._hist_hi, self._hist_lo)
        )
        menu.addAction(reset_act)

        return menu

    def _set_chart_type(self, key: str) -> None:
        self._chart_type = key
        self._apply_chart_type()

    def _apply_chart_type(self) -> None:
        is_candles = self._chart_type == "candles"
        self.candle_item.setVisible(is_candles)
        self.line_curve.setVisible(not is_candles)
        if not is_candles and self._hist_t and self._hist_c:
            self.line_curve.setData(self._hist_t, self._hist_c)
        else:
            self.line_curve.setData([])

    def _toggle_grid(self, checked: bool) -> None:
        self._grid_on = checked
        self.plot_widget.showGrid(x=checked, y=checked, alpha=0.15)

    def _toggle_log(self, checked: bool) -> None:
        self._log_on = checked
        self.plot_widget.setLogMode(y=checked)

    def _update_legend(self) -> None:
        """Paint each active SMA toggle in its own line color so the button
        row doubles as the chart legend."""
        for w in SMA_WINDOWS:
            btn = self._sma_buttons.get(w)
            if btn is None:
                continue
            if getattr(self, f"_sma{w}_on"):
                color = SMA_COLORS[w]
                btn.setStyleSheet(
                    f"QPushButton {{ background: {BG_ELEV}; color: {color};"
                    f" border: 1px solid {color}; border-radius: 4px;"
                    " padding: 4px 11px; font-size: 11px; font-weight: 600; }"
                )
            else:
                btn.setStyleSheet("")

    def _update_overlays(self) -> None:
        t = self._hist_t
        c = self._hist_c
        for w in SMA_WINDOWS:
            curve = self.sma_curves[w]
            if not getattr(self, f"_sma{w}_on") or not c or len(c) < w:
                curve.setData([])
                continue
            sma_vals = _sma(c, w)
            t_aligned = t[w - 1:]
            curve.setData(t_aligned, sma_vals)

        if self._rsi_on and c and len(c) >= RSI_WINDOW + 1:
            rsi_vals = _wilder_rsi(c, RSI_WINDOW)
            if rsi_vals:
                self.rsi_curve.setData(t[RSI_WINDOW:], rsi_vals)
            else:
                self.rsi_curve.setData([])
        else:
            self.rsi_curve.setData([])

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self._last_quote = {}
        self._hist_t, self._hist_c = [], []
        self._hist_hi, self._hist_lo = [], []
        self._update_overlays()
        self._apply_chart_type()
        self._resubscribe(symbol)
        self._update_title()

    def _fetch_period(self) -> tuple[str, str]:
        """Period/interval to actually request. For the daily-interval band we
        widen the fetch so the longest active moving average has enough lookback
        — the visible window is reframed to ``self._period`` afterward."""
        disp = self._period
        interval = self._interval_for(disp)
        if interval != "1d":
            return disp, interval
        active = [w for w in SMA_WINDOWS if getattr(self, f"_sma{w}_on")]
        need = _TRADING_DAYS.get(disp, 0) + (max(active) if active else 0) + 10
        for cand, days in _DAILY_FETCH_LADDER:
            if days >= need:
                return cand, "1d"
        return "max", "1d"

    def _resubscribe(self, symbol: str) -> None:
        self.unsubscribe_all()
        fetch_period, interval = self._fetch_period()
        topic = f"history:{symbol}:{fetch_period}:{interval}"
        self.subscribe(topic, self._on_history)
        self.subscribe(f"quote:{symbol}", self._on_quote)

    def _frame_window(self, t: list, highs: list, lows: list) -> None:
        """Zoom the plot to the selected display period even though more bars
        may have been fetched, and fit Y to just the visible candles."""
        if not t:
            return
        end = t[-1]
        span = PERIOD_SPAN_DAYS.get(self._period, 100000) * 86400
        start = max(end - span, t[0])
        self.plot_widget.setXRange(start, end, padding=0.02)
        vis = [(lows[i], highs[i]) for i in range(len(t)) if t[i] >= start]
        if vis:
            lo = min(v[0] for v in vis)
            hi = max(v[1] for v in vis)
            pad = (hi - lo) * 0.06 or max(abs(hi), 1.0) * 1e-3
            self.plot_widget.setYRange(lo - pad, hi + pad, padding=0)

    # -- data callbacks ------------------------------------------------------

    def _on_history(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        t = data.get("t") or []
        o = data.get("o") or []
        h = data.get("h") or []
        l = data.get("l") or []
        c = data.get("c") or []
        if not t:
            self.set_status(f"{self.current_symbol} · no data")
            self._hist_t, self._hist_c = [], []
            self._update_overlays()
            return
        self.candle_item.set_ohlc(t, o, h, l, c)

        n = min(len(t), len(o), len(h), len(l), len(c))
        valid_t: list = []
        valid_c: list = []
        valid_h: list = []
        valid_l: list = []
        for i in range(n):
            ti, oi, hi, li, ci = t[i], o[i], h[i], l[i], c[i]
            if None in (ti, oi, hi, li, ci):
                continue
            valid_t.append(float(ti))
            valid_c.append(float(ci))
            valid_h.append(float(hi))
            valid_l.append(float(li))
        self._hist_t, self._hist_c = valid_t, valid_c
        self._hist_hi, self._hist_lo = valid_h, valid_l
        self._update_overlays()
        self._apply_chart_type()
        self._frame_window(valid_t, valid_h, valid_l)

        self.set_status(f"{self.current_symbol} · {self._period}")

    def _on_quote(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._last_quote = data
        self._update_title()

    def _update_title(self) -> None:
        sym = self.current_symbol or "—"
        self.title_lbl.setText(sym)
        price = self._last_quote.get("price")
        change_pct = self._last_quote.get("change_pct")
        self.price_lbl.setText(f"{price:,.2f}" if price is not None else "")
        if change_pct is None:
            self.chg_lbl.setText("")
        else:
            color = UP if change_pct >= 0 else DOWN
            sign = "+" if change_pct >= 0 else ""
            self.chg_lbl.setText(f"{sign}{change_pct:.2f}%")
            self.chg_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "period": self._period,
            "sma50": self._sma50_on,
            "sma100": self._sma100_on,
            "sma200": self._sma200_on,
            "rsi": self._rsi_on,
            "chart_type": self._chart_type,
            "grid": self._grid_on,
            "log": self._log_on,
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        period = settings.get("period")
        if period in INTERVAL_OF:
            self._period = period
            self._update_period_buttons()

        self._sma50_on = bool(settings.get("sma50", self._sma50_on))
        self._sma100_on = bool(settings.get("sma100", self._sma100_on))
        self._sma200_on = bool(settings.get("sma200", self._sma200_on))
        self._rsi_on = bool(settings.get("rsi", self._rsi_on))

        for w, btn in self._sma_buttons.items():
            btn.setChecked(getattr(self, f"_sma{w}_on"))
        self._rsi_button.setChecked(self._rsi_on)
        self.rsi_widget.setVisible(self._rsi_on)
        self._update_legend()
        self._update_overlays()

        ctype = settings.get("chart_type")
        if ctype in ("candles", "line"):
            self._chart_type = ctype
        self._grid_on = bool(settings.get("grid", self._grid_on))
        self._log_on = bool(settings.get("log", self._log_on))
        self.plot_widget.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.15)
        self.plot_widget.setLogMode(y=self._log_on)
        self._apply_chart_type()

        if self.current_symbol:
            self._resubscribe(self.current_symbol)
