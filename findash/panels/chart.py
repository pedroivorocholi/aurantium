"""Chart panel — price history with selectable chart styles (candles, OHLC
bars, line, area), a registry-driven indicator system (SMA/EMA/Bollinger/
VWAP/MACD/RSI/Volume — add/remove/recolor any number), decoupled range +
candle interval with yfinance-constraint validation, and per-instance
persistence (Bloomberg G-chart style)."""

from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QDate, QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QPainter, QPicture
from PySide6.QtWidgets import (
    QColorDialog,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from ..panel import Panel, register_panel
from ..theme import ACCENT, BG, BG_ELEV, DOWN, FG, FG_DIM, MONO_FONT, UP

# -- range / interval model -------------------------------------------------
#
# Range (how much history to show) and candle interval are independent.
# A range is either a preset label or a custom (start, end) date pair.

RANGE_PRESETS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]

# Calendar span (days) of each preset — frames the visible window after we
# fetch extra history so long moving averages have enough lookback.
PERIOD_SPAN_DAYS = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186,
    "1y": 372, "2y": 744, "5y": 1860, "max": 100000,
}

INTERVALS = ["1m", "5m", "15m", "30m", "1h", "1d", "1wk", "1mo"]

# yfinance's real interval limits (how far back each intraday interval can
# reach, in calendar days). 1d and coarser are uncapped.
INTERVAL_MAX_BACK_DAYS = {
    "1m": 30, "2m": 60, "5m": 60, "15m": 60, "30m": 60, "90m": 60,
    "1h": 730, "60m": 730,
}
# Approximate calendar days one bar covers — used to block ranges too short
# to hold at least a couple of bars (e.g. a 1mo interval on a 5d range).
INTERVAL_APPROX_DAYS = {
    "1m": 0.003, "5m": 0.013, "15m": 0.04, "30m": 0.08, "1h": 0.16,
    "1d": 1.0, "1wk": 7.0, "1mo": 30.4,
}

# Daily-interval fetch ladder: (period, approx trading days it yields). We
# pick the smallest that covers the visible window PLUS the longest active
# indicator lookback, so e.g. SMA200 is populated even at a 6mo view.
_DAILY_FETCH_LADDER = [
    ("6mo", 126), ("1y", 252), ("2y", 504), ("5y", 1260), ("max", 100000),
]

RSI_WINDOW = 14

# Colors handed to newly added indicators, in order.
INDICATOR_PALETTE = [
    "#e91e63", "#4a90d9", "#f8e71c", "#9c27b0", "#00bcd4",
    "#ff7043", "#8bc34a", "#f06292",
]

DEFAULT_COLORS = {
    "up": UP,
    "down": DOWN,
    "line": ACCENT,
    "grid": FG_DIM,
    "bg": BG,
}

CHART_TYPES = [
    ("candles", "Candlesticks"),
    ("bars", "OHLC bars"),
    ("line", "Line"),
    ("area", "Area / mountain"),
]


# -- indicator math ---------------------------------------------------------

def _sma(values: list, window: int) -> list:
    """Simple moving average; caller guarantees len(values) >= window."""
    arr = np.asarray(values, dtype=float)
    kernel = np.ones(window) / window
    return np.convolve(arr, kernel, mode="valid").tolist()


def _ema(values: list, window: int) -> list:
    """EMA seeded with the SMA of the first ``window`` bars; aligned to
    values[window - 1:]."""
    arr = np.asarray(values, dtype=float)
    alpha = 2.0 / (window + 1)
    out = [float(arr[:window].mean())]
    for v in arr[window:]:
        out.append(out[-1] + alpha * (float(v) - out[-1]))
    return out


def _rolling_std(values: list, window: int) -> list:
    arr = np.asarray(values, dtype=float)
    win = np.lib.stride_tricks.sliding_window_view(arr, window)
    return win.std(axis=1).tolist()


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


def _median_spacing(t: list) -> float:
    if len(t) < 2:
        return 86400.0
    diffs = sorted(t[i + 1] - t[i] for i in range(len(t) - 1))
    diffs = [d for d in diffs if d > 0]
    return diffs[len(diffs) // 2] if diffs else 86400.0


# -- indicator registry -----------------------------------------------------
#
# Adding a new indicator = one _IndicatorSpec entry + a render function.
# ``pane`` is "price" (overlay on the price plot) or "osc" (own sub-panel).

class _IndicatorSpec:
    def __init__(
        self,
        kind: str,
        label: str,
        pane: str,
        default_window: Optional[int],
        lookback: Callable[[dict], int],
    ) -> None:
        self.kind = kind
        self.label = label
        self.pane = pane
        self.default_window = default_window  # None = no window parameter
        self.lookback = lookback  # bars of history the math needs to warm up


INDICATOR_SPECS: dict[str, _IndicatorSpec] = {
    "sma": _IndicatorSpec("sma", "SMA", "price", 50, lambda p: p.get("window", 50)),
    "ema": _IndicatorSpec("ema", "EMA", "price", 21, lambda p: p.get("window", 21) * 3),
    "bb": _IndicatorSpec("bb", "BB", "price", 20, lambda p: p.get("window", 20)),
    "vwap": _IndicatorSpec("vwap", "VWAP", "price", None, lambda p: 0),
    "volume": _IndicatorSpec("volume", "VOL", "osc", None, lambda p: 0),
    "rsi": _IndicatorSpec("rsi", "RSI", "osc", 14, lambda p: p.get("window", 14) + 1),
    "macd": _IndicatorSpec("macd", "MACD", "osc", None, lambda p: 26 + 9),
}


class _IndicatorInstance:
    """One active indicator: config + the pg items it owns."""

    _uid_counter = itertools.count(1)

    def __init__(self, kind: str, params: dict, color: str, on: bool = True) -> None:
        self.uid = next(self._uid_counter)
        self.kind = kind
        self.params = dict(params)
        self.color = color
        self.on = on
        self.chip: Optional[QPushButton] = None
        self.pane: Optional[pg.PlotWidget] = None  # osc indicators only
        self.items: list = []       # pg items on the price plot
        self.pane_items: list = []  # pg items inside self.pane

    @property
    def spec(self) -> _IndicatorSpec:
        return INDICATOR_SPECS[self.kind]

    def label(self) -> str:
        w = self.params.get("window")
        return f"{self.spec.label} {w}" if w else self.spec.label


# -- chart items ------------------------------------------------------------

class CandlestickItem(pg.GraphicsObject):
    """Minimal OHLC candlestick item — standard pyqtgraph QPicture pattern.

    Body is a filled rect from open to close, wick is a line from low to
    high; both colored up/down by whether the candle closed above its open.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[float, float, float, float, float]] = []
        self._up = UP
        self._down = DOWN
        self._picture = QPicture()

    def set_colors(self, up: str, down: str) -> None:
        self._up, self._down = up, down
        self._generate()

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
        spacing = _median_spacing([r[0] for r in self._rows])
        half_width = spacing * 0.35
        up_brush = pg.mkBrush(self._up)
        down_brush = pg.mkBrush(self._down)
        for t, o, h, l, c in self._rows:
            is_up = c >= o
            painter.setPen(pg.mkPen(self._up if is_up else self._down))
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


class OHLCBarItem(pg.GraphicsObject):
    """Classic OHLC bar: vertical low-high line, open tick left, close tick
    right; colored up/down like the candles."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[float, float, float, float, float]] = []
        self._up = UP
        self._down = DOWN
        self._picture = QPicture()

    def set_colors(self, up: str, down: str) -> None:
        self._up, self._down = up, down
        self._generate()

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
        spacing = _median_spacing([r[0] for r in self._rows])
        tick = spacing * 0.35
        for t, o, h, l, c in self._rows:
            painter.setPen(pg.mkPen(self._up if c >= o else self._down, width=1))
            painter.drawLine(QPointF(t, l), QPointF(t, h))
            painter.drawLine(QPointF(t - tick, o), QPointF(t, o))
            painter.drawLine(QPointF(t, c), QPointF(t + tick, c))
        painter.end()
        self.update()

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return QRectF(self._picture.boundingRect())


class _CompactAxis(pg.AxisItem):
    """Y axis for big magnitudes (volume): 150M / 2.5B instead of 1.5e+08."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing) -> list[str]:
        out = []
        for v in values:
            av = abs(v)
            for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
                if av >= div:
                    s = f"{v / div:.1f}"
                    if s.endswith(".0"):
                        s = s[:-2]
                    out.append(s + suffix)
                    break
            else:
                out.append(f"{v:g}")
        return out


# -- custom range dialog ----------------------------------------------------

class _RangeDialog(QDialog):
    """Two date pickers, themed by the app-global stylesheet."""

    def __init__(self, parent, start: date, end: date) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom range")
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.start_edit = QDateEdit(QDate(start.year, start.month, start.day), self)
        self.end_edit = QDateEdit(QDate(end.year, end.month, end.day), self)
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMaximumDate(QDate.currentDate())
        row.addWidget(QLabel("From", self))
        row.addWidget(self.start_edit)
        row.addWidget(QLabel("to", self))
        row.addWidget(self.end_edit)
        lay.addLayout(row)
        self._hint = QLabel("", self)
        self._hint.setStyleSheet(f"color: {DOWN}; font-size: 10px;")
        lay.addWidget(self._hint)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        btns.accepted.connect(self._try_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _try_accept(self) -> None:
        if self.start_edit.date() >= self.end_edit.date():
            self._hint.setText("Start must be before end.")
            return
        self.accept()

    def dates(self) -> tuple[date, date]:
        s, e = self.start_edit.date(), self.end_edit.date()
        return date(s.year(), s.month(), s.day()), date(e.year(), e.month(), e.day())


# -- panel ------------------------------------------------------------------

@register_panel(id="chart", title="Chart", category="Markets")
class ChartPanel(Panel):
    def build(self) -> None:
        # range is either {"preset": "6mo"} or {"start": iso, "end": iso}
        self._range: dict = {"preset": "6mo"}
        self._interval = "1d"
        self._last_quote: dict = {}
        self._hist_t: list = []
        self._hist_o: list = []
        self._hist_c: list = []
        self._hist_hi: list = []
        self._hist_lo: list = []
        self._hist_v: list = []

        self._chart_type = "candles"  # candles | bars | line | area
        self._grid_on = True
        self._log_on = False
        self._colors = dict(DEFAULT_COLORS)
        self._indicators: list[_IndicatorInstance] = []
        self._palette_iter = 0

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

        # -- range selector ---------------------------------------------------
        range_row = QHBoxLayout()
        range_row.setSpacing(6)
        range_row.addWidget(self._eyebrow("RANGE"))
        self._range_buttons: dict[str, QPushButton] = {}
        for label in RANGE_PRESETS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            self._size_selector_btn(btn)
            btn.clicked.connect(lambda _=False, p=label: self._set_range_preset(p))
            range_row.addWidget(btn)
            self._range_buttons[label] = btn
        self._custom_btn = QPushButton("custom…", self)
        self._custom_btn.setCheckable(True)
        self._custom_btn.clicked.connect(self._pick_custom_range)
        range_row.addWidget(self._custom_btn)
        range_row.addStretch(1)
        self.content_layout.addLayout(range_row)

        # -- interval selector ------------------------------------------------
        int_row = QHBoxLayout()
        int_row.setSpacing(6)
        int_row.addWidget(self._eyebrow("INTERVAL"))
        self._interval_buttons: dict[str, QPushButton] = {}
        for label in INTERVALS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            self._size_selector_btn(btn)
            btn.clicked.connect(lambda _=False, i=label: self._set_interval(i))
            int_row.addWidget(btn)
            self._interval_buttons[label] = btn
        int_row.addStretch(1)
        self.content_layout.addLayout(int_row)

        # -- indicator chips: one toggle per active indicator; the chip wears
        # its line color when on, doubling as the legend. Right-click a chip
        # to recolor / edit / remove; "+" adds a new indicator.
        chips_row = QHBoxLayout()
        chips_row.setSpacing(6)
        chips_row.addWidget(self._eyebrow("INDICATORS"))
        self._chips_row = chips_row
        self._add_btn = QPushButton("+", self)
        self._add_btn.setFixedWidth(28)
        self._add_btn.clicked.connect(self._show_add_menu)
        chips_row.addWidget(self._add_btn)
        chips_row.addStretch(1)
        self.content_layout.addLayout(chips_row)

        # -- price plot --------------------------------------------------------
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}
        )
        self.plot_widget.setBackground(self._colors["bg"])
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        self.plot_widget.getAxis("bottom").setTextPen(FG_DIM)
        self.candle_item = CandlestickItem()
        self.plot_widget.addItem(self.candle_item)
        self.bar_item = OHLCBarItem()
        self.plot_widget.addItem(self.bar_item)
        self.bar_item.setVisible(False)
        self.line_curve = pg.PlotDataItem(
            pen=pg.mkPen(self._colors["line"], width=1.5), antialias=True
        )
        self.line_curve.setZValue(5)
        self.plot_widget.addItem(self.line_curve)
        self.line_curve.setVisible(False)
        self.content_layout.addWidget(self.plot_widget, 3)

        # right-click settings menu on the chart
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_widget.customContextMenuRequested.connect(self._show_chart_menu)

        # default working set mirrors the old fixed layout: SMA50 + SMA200 + RSI
        self._add_indicator("sma", {"window": 50}, color="#e91e63", rebuild=False)
        self._add_indicator("sma", {"window": 200}, color="#f8e71c", rebuild=False)
        self._add_indicator("rsi", {"window": 14}, color=ACCENT, rebuild=False)

        self._update_range_interval_buttons()

    def _size_selector_btn(self, btn: QPushButton) -> None:
        """Fixed width sized to the label (checked state renders bold, which
        is wider than the metrics of the normal font — hence the margin), so
        short tags stay compact without clipping '3mo'/'15m'-style labels."""
        text_w = btn.fontMetrics().horizontalAdvance(btn.text())
        btn.setFixedWidth(max(40, text_w + 28))

    def _eyebrow(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setStyleSheet(
            "color: #565d67; font-size: 10px; font-weight: 600; letter-spacing: 1px;"
        )
        return lbl

    # -- range / interval selection ------------------------------------------

    def _range_span_days(self) -> float:
        if "preset" in self._range:
            return PERIOD_SPAN_DAYS.get(self._range["preset"], 100000)
        s = date.fromisoformat(self._range["start"])
        e = date.fromisoformat(self._range["end"])
        return max((e - s).days, 1)

    def _range_back_days(self) -> float:
        """Calendar days from the earliest requested bar to today — what the
        yfinance intraday caps actually constrain."""
        if "preset" in self._range:
            return PERIOD_SPAN_DAYS.get(self._range["preset"], 100000)
        s = date.fromisoformat(self._range["start"])
        return max((date.today() - s).days, 1)

    def _combo_problem(self, rng: dict, interval: str) -> Optional[str]:
        """Reason the (range, interval) pair is invalid, or None if fine.
        Encodes yfinance's real limits (see INTERVAL_MAX_BACK_DAYS)."""
        if "preset" in rng:
            span = back = PERIOD_SPAN_DAYS.get(rng["preset"], 100000)
        else:
            s = date.fromisoformat(rng["start"])
            e = date.fromisoformat(rng["end"])
            span = max((e - s).days, 1)
            back = max((date.today() - s).days, 1)
        cap = INTERVAL_MAX_BACK_DAYS.get(interval)
        if cap is not None and back > cap:
            return f"{interval} bars: yfinance only serves the last ~{cap} days"
        if INTERVAL_APPROX_DAYS.get(interval, 1.0) * 2 > span:
            return f"range too short for {interval} bars (needs ≥2 bars)"
        return None

    def _update_range_interval_buttons(self) -> None:
        """Check the active buttons, and grey out any range/interval choice
        invalid against the other axis's current value (with a tooltip
        explaining why)."""
        active_preset = self._range.get("preset")
        for label, btn in self._range_buttons.items():
            problem = self._combo_problem({"preset": label}, self._interval)
            btn.setEnabled(problem is None)
            btn.setToolTip(problem or "")
            btn.setChecked(label == active_preset)
        self._custom_btn.setChecked(active_preset is None)
        if active_preset is None:
            self._custom_btn.setToolTip(
                f"{self._range['start']} → {self._range['end']}"
            )
        else:
            self._custom_btn.setToolTip("Pick an explicit start/end date")
        for label, btn in self._interval_buttons.items():
            problem = self._combo_problem(self._range, label)
            btn.setEnabled(problem is None)
            btn.setToolTip(problem or "")
            btn.setChecked(label == self._interval)

    def _set_range_preset(self, preset: str) -> None:
        if self._range.get("preset") == preset:
            self._update_range_interval_buttons()
            return
        self._range = {"preset": preset}
        self._range_or_interval_changed()

    def _pick_custom_range(self) -> None:
        if "preset" in self._range:
            end = date.today()
            start = end - timedelta(days=180)
        else:
            start = date.fromisoformat(self._range["start"])
            end = date.fromisoformat(self._range["end"])
        dlg = _RangeDialog(self, start, end)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._update_range_interval_buttons()
            return
        start, end = dlg.dates()
        self._range = {"start": start.isoformat(), "end": end.isoformat()}
        # a custom range can invalidate the current interval — fall back to
        # the finest still-valid one rather than fetching a combo that errors
        if self._combo_problem(self._range, self._interval):
            for cand in INTERVALS:
                if not self._combo_problem(self._range, cand):
                    self.set_status(
                        f"interval {self._interval} invalid for range — using {cand}"
                    )
                    self._interval = cand
                    break
        self._range_or_interval_changed()

    def _set_interval(self, interval: str) -> None:
        if interval == self._interval:
            self._update_range_interval_buttons()
            return
        self._interval = interval
        self._range_or_interval_changed()

    def _range_or_interval_changed(self) -> None:
        self._update_range_interval_buttons()
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    # -- indicators -----------------------------------------------------------

    def _next_color(self) -> str:
        color = INDICATOR_PALETTE[self._palette_iter % len(INDICATOR_PALETTE)]
        self._palette_iter += 1
        return color

    def _pick_new_indicator_color(self, label: str) -> Optional[str]:
        """Prompt for a color when adding a new indicator, seeded with the
        next palette color. Loops on too-dark picks (same rule as
        recoloring) until a valid color is chosen or the user cancels."""
        seed = self._next_color()
        while True:
            picked = QColorDialog.getColor(QColor(seed), self, f"{label} color")
            if not picked.isValid():
                return None
            if picked.lightness() < 60:
                self.set_status("⚠ too dark for the black canvas — pick a lighter color")
                seed = picked.name()
                continue
            return picked.name()

    def _show_add_menu(self) -> None:
        menu = QMenu(self)
        for kind, spec in INDICATOR_SPECS.items():
            text = f"Add {spec.label}…" if spec.default_window else f"Add {spec.label}"
            act = QAction(text, menu)
            act.triggered.connect(lambda _=False, k=kind: self._add_indicator_ui(k))
            menu.addAction(act)
        menu.exec(self._add_btn.mapToGlobal(self._add_btn.rect().bottomLeft()))

    def _add_indicator_ui(self, kind: str) -> None:
        spec = INDICATOR_SPECS[kind]
        params: dict = {}
        if spec.default_window is not None:
            window, ok = QInputDialog.getInt(
                self, f"{spec.label} period", "Period (bars):",
                spec.default_window, 2, 500,
            )
            if not ok:
                return
            params["window"] = window
        color = self._pick_new_indicator_color(spec.label)
        if color is None:
            return
        self._add_indicator(kind, params, color=color)

    def _add_indicator(
        self, kind: str, params: dict,
        color: Optional[str] = None, on: bool = True, rebuild: bool = True,
    ) -> None:
        inst = _IndicatorInstance(kind, params, color or self._next_color(), on)
        self._indicators.append(inst)
        self._build_chip(inst)
        self._build_indicator_items(inst)
        if rebuild:
            self._refresh_indicator(inst)
            self._maybe_widen_fetch()

    def _build_chip(self, inst: _IndicatorInstance) -> None:
        chip = QPushButton(inst.label(), self)
        chip.setCheckable(True)
        chip.setChecked(inst.on)
        chip.toggled.connect(lambda checked, i=inst: self._on_chip_toggled(i, checked))
        chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        chip.customContextMenuRequested.connect(
            lambda pos, i=inst: self._show_chip_menu(i, pos)
        )
        chip.setToolTip("Click: toggle · right-click: color / edit / remove")
        # insert before the "+" button
        idx = self._chips_row.indexOf(self._add_btn)
        self._chips_row.insertWidget(idx, chip)
        inst.chip = chip
        self._style_chip(inst)

    def _style_chip(self, inst: _IndicatorInstance) -> None:
        if inst.chip is None:
            return
        if inst.on:
            inst.chip.setStyleSheet(
                f"QPushButton {{ background: {BG_ELEV}; color: {inst.color};"
                f" border: 1px solid {inst.color}; border-radius: 4px;"
                " padding: 4px 11px; font-size: 11px; font-weight: 600; }"
            )
        else:
            inst.chip.setStyleSheet("")

    def _show_chip_menu(self, inst: _IndicatorInstance, pos) -> None:
        menu = QMenu(self)
        color_act = QAction("Color…", menu)
        color_act.triggered.connect(lambda: self._recolor_indicator(inst))
        menu.addAction(color_act)
        if inst.spec.default_window is not None:
            edit_act = QAction("Edit period…", menu)
            edit_act.triggered.connect(lambda: self._edit_indicator(inst))
            menu.addAction(edit_act)
        menu.addSeparator()
        rm_act = QAction("Remove", menu)
        rm_act.triggered.connect(lambda: self._remove_indicator(inst))
        menu.addAction(rm_act)
        menu.exec(inst.chip.mapToGlobal(pos))

    def _on_chip_toggled(self, inst: _IndicatorInstance, checked: bool) -> None:
        inst.on = checked
        self._style_chip(inst)
        self._refresh_indicator(inst)
        if checked:
            self._maybe_widen_fetch()

    def _recolor_indicator(self, inst: _IndicatorInstance) -> None:
        picked = QColorDialog.getColor(QColor(inst.color), self, "Indicator color")
        if not picked.isValid():
            return
        if picked.lightness() < 60:
            self.set_status("⚠ too dark for the black canvas — keeping old color")
            return
        inst.color = picked.name()
        self._style_chip(inst)
        self._build_indicator_items(inst)
        self._refresh_indicator(inst)

    def _edit_indicator(self, inst: _IndicatorInstance) -> None:
        window, ok = QInputDialog.getInt(
            self, f"{inst.spec.label} period", "Period (bars):",
            inst.params.get("window", inst.spec.default_window or 14), 2, 500,
        )
        if not ok:
            return
        inst.params["window"] = window
        if inst.chip is not None:
            inst.chip.setText(inst.label())
        self._refresh_indicator(inst)
        self._maybe_widen_fetch()

    def _remove_indicator(self, inst: _IndicatorInstance) -> None:
        self._teardown_indicator_items(inst)
        if inst.chip is not None:
            self._chips_row.removeWidget(inst.chip)
            inst.chip.deleteLater()
        self._indicators.remove(inst)

    def _maybe_widen_fetch(self) -> None:
        """A new/longer indicator may need more lookback than what's fetched."""
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    # -- indicator plumbing: build pg items / panes, compute + set data -------

    def _make_pane(self, compact_y: bool = False) -> pg.PlotWidget:
        axes: dict = {"bottom": pg.DateAxisItem(orientation="bottom")}
        if compact_y:
            axes["left"] = _CompactAxis(orientation="left")
        pane = pg.PlotWidget(axisItems=axes)
        pane.setBackground(self._colors["bg"])
        pane.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.15)
        pane.getAxis("left").setTextPen(FG_DIM)
        pane.getAxis("bottom").setTextPen(FG_DIM)
        pane.setXLink(self.plot_widget)
        self.content_layout.addWidget(pane, 1)
        return pane

    def _teardown_indicator_items(self, inst: _IndicatorInstance) -> None:
        for item in inst.items:
            self.plot_widget.removeItem(item)
        inst.items = []
        if inst.pane is not None:
            self.content_layout.removeWidget(inst.pane)
            inst.pane.deleteLater()
            inst.pane = None
        inst.pane_items = []

    def _build_indicator_items(self, inst: _IndicatorInstance) -> None:
        """(Re)create the pg items an instance renders into. Data is applied
        separately by _refresh_indicator."""
        self._teardown_indicator_items(inst)
        pen = pg.mkPen(inst.color, width=1)
        if inst.kind in ("sma", "ema", "vwap"):
            curve = pg.PlotDataItem(pen=pen, antialias=True)
            curve.setZValue(10)
            self.plot_widget.addItem(curve)
            inst.items = [curve]
        elif inst.kind == "bb":
            dash = pg.mkPen(inst.color, width=1, style=Qt.PenStyle.DashLine)
            mid = pg.PlotDataItem(pen=pen, antialias=True)
            hi = pg.PlotDataItem(pen=dash, antialias=True)
            lo = pg.PlotDataItem(pen=dash, antialias=True)
            fill_color = QColor(inst.color)
            fill_color.setAlpha(28)
            fill = pg.FillBetweenItem(hi, lo, brush=pg.mkBrush(fill_color))
            for item in (fill, mid, hi, lo):
                item.setZValue(9)
                self.plot_widget.addItem(item)
            inst.items = [mid, hi, lo, fill]
        elif inst.kind == "rsi":
            pane = self._make_pane()
            pane.setYRange(0, 100)
            dash_pen = pg.mkPen(FG_DIM, width=1, style=Qt.PenStyle.DashLine)
            pane.addItem(pg.InfiniteLine(pos=30, angle=0, pen=dash_pen))
            pane.addItem(pg.InfiniteLine(pos=70, angle=0, pen=dash_pen))
            curve = pg.PlotDataItem(pen=pen, antialias=True)
            pane.addItem(curve)
            inst.pane = pane
            inst.pane_items = [curve]
        elif inst.kind == "macd":
            pane = self._make_pane()
            hist = pg.BarGraphItem(x=[], height=[], width=1)
            macd_curve = pg.PlotDataItem(pen=pen, antialias=True)
            sig_curve = pg.PlotDataItem(pen=pg.mkPen(FG_DIM, width=1), antialias=True)
            pane.addItem(hist)
            pane.addItem(macd_curve)
            pane.addItem(sig_curve)
            inst.pane = pane
            inst.pane_items = [hist, macd_curve, sig_curve]
        elif inst.kind == "volume":
            pane = self._make_pane(compact_y=True)
            bars = pg.BarGraphItem(x=[], height=[], width=1)
            pane.addItem(bars)
            inst.pane = pane
            inst.pane_items = [bars]
        self._apply_indicator_visibility(inst)

    def _apply_indicator_visibility(self, inst: _IndicatorInstance) -> None:
        for item in inst.items:
            item.setVisible(inst.on)
        if inst.pane is not None:
            inst.pane.setVisible(inst.on)

    def _refresh_indicator(self, inst: _IndicatorInstance) -> None:
        """Recompute and apply one indicator's data from stored history."""
        self._apply_indicator_visibility(inst)
        t, c = self._hist_t, self._hist_c
        if not inst.on:
            return

        def _clear() -> None:
            for item in inst.items + inst.pane_items:
                if isinstance(item, pg.BarGraphItem):
                    item.setOpts(x=[], height=[], width=1)
                elif isinstance(item, pg.PlotDataItem):
                    item.setData([])

        if not t:
            _clear()
            return
        w = inst.params.get("window", 0)
        if inst.kind in ("sma", "ema"):
            if len(c) < w:
                _clear()
                return
            vals = _sma(c, w) if inst.kind == "sma" else _ema(c, w)
            inst.items[0].setData(t[w - 1:], vals)
        elif inst.kind == "bb":
            if len(c) < w:
                _clear()
                return
            mid = _sma(c, w)
            std = _rolling_std(c, w)
            ta = t[w - 1:]
            upper = [m + 2 * s for m, s in zip(mid, std)]
            lower = [m - 2 * s for m, s in zip(mid, std)]
            inst.items[0].setData(ta, mid)
            inst.items[1].setData(ta, upper)
            inst.items[2].setData(ta, lower)
        elif inst.kind == "vwap":
            v = self._hist_v
            if not v or len(v) != len(t):
                _clear()
                return
            tp = [(h + l + cc) / 3.0 for h, l, cc in
                  zip(self._hist_hi, self._hist_lo, c)]
            cum_pv = np.cumsum(np.asarray(tp) * np.asarray(v, dtype=float))
            cum_v = np.cumsum(np.asarray(v, dtype=float))
            with np.errstate(divide="ignore", invalid="ignore"):
                vwap = np.where(cum_v > 0, cum_pv / cum_v, np.nan)
            inst.items[0].setData(t, vwap.tolist())
        elif inst.kind == "rsi":
            vals = _wilder_rsi(c, w or RSI_WINDOW)
            if vals:
                inst.pane_items[0].setData(t[(w or RSI_WINDOW):], vals)
            else:
                _clear()
        elif inst.kind == "macd":
            if len(c) < 26 + 9:
                _clear()
                return
            ema12 = _ema(c, 12)
            ema26 = _ema(c, 26)
            macd = [a - b for a, b in zip(ema12[26 - 12:], ema26)]
            signal = _ema(macd, 9)
            # macd is aligned to t[25:]; the 9-EMA signal trims 8 more bars
            macd_a = macd[8:]
            ta = t[25 + 8:]
            hist = [m - s for m, s in zip(macd_a, signal)]
            spacing = _median_spacing(ta)
            up_c, dn_c = QColor(self._colors["up"]), QColor(self._colors["down"])
            up_c.setAlpha(140)
            dn_c.setAlpha(140)
            brushes = [pg.mkBrush(up_c if h >= 0 else dn_c) for h in hist]
            inst.pane_items[0].setOpts(
                x=ta, height=hist, width=spacing * 0.6, brushes=brushes, pen=None
            )
            inst.pane_items[1].setData(ta, macd_a)
            inst.pane_items[2].setData(ta, signal)
        elif inst.kind == "volume":
            v = self._hist_v
            if not v or len(v) != len(t):
                _clear()
                return
            spacing = _median_spacing(t)
            up_c, dn_c = QColor(self._colors["up"]), QColor(self._colors["down"])
            up_c.setAlpha(170)
            dn_c.setAlpha(170)
            brushes = [
                pg.mkBrush(up_c if cc >= oo else dn_c)
                for oo, cc in zip(self._hist_o, c)
            ]
            inst.pane_items[0].setOpts(
                x=t, height=v, width=spacing * 0.7, brushes=brushes, pen=None
            )

    def _refresh_all_indicators(self) -> None:
        for inst in self._indicators:
            self._refresh_indicator(inst)

    # -- right-click settings menu --------------------------------------------

    def _show_chart_menu(self, pos) -> None:
        self._build_chart_menu().exec(self.plot_widget.mapToGlobal(pos))

    def _build_chart_menu(self) -> QMenu:
        menu = QMenu(self.plot_widget)

        type_menu = menu.addMenu("Chart type")
        grp = QActionGroup(type_menu)
        grp.setExclusive(True)
        for key, label in CHART_TYPES:
            act = QAction(label, type_menu, checkable=True)
            act.setChecked(self._chart_type == key)
            act.triggered.connect(lambda _=False, k=key: self._set_chart_type(k))
            grp.addAction(act)
            type_menu.addAction(act)

        color_menu = menu.addMenu("Colors")
        for key, label in (
            ("up", "Up candles…"), ("down", "Down candles…"),
            ("line", "Line / area…"), ("grid", "Grid / axes…"),
            ("bg", "Background…"),
        ):
            act = QAction(label, color_menu)
            act.triggered.connect(lambda _=False, k=key: self._pick_color(k))
            color_menu.addAction(act)

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
        png_act = QAction("Export PNG…", menu)
        png_act.triggered.connect(self._export_png)
        menu.addAction(png_act)
        defaults_act = QAction("Reset chart to defaults", menu)
        defaults_act.triggered.connect(self._reset_defaults)
        menu.addAction(defaults_act)

        return menu

    def _set_chart_type(self, key: str) -> None:
        self._chart_type = key
        self._apply_chart_type()

    def _apply_chart_type(self) -> None:
        kind = self._chart_type
        self.candle_item.setVisible(kind == "candles")
        self.bar_item.setVisible(kind == "bars")
        self.line_curve.setVisible(kind in ("line", "area"))
        if kind in ("line", "area") and self._hist_t and self._hist_c:
            if kind == "area":
                fill_color = QColor(self._colors["line"])
                fill_color.setAlpha(60)
                base = min(self._hist_lo) if self._hist_lo else 0
                self.line_curve.setFillLevel(base)
                self.line_curve.setFillBrush(pg.mkBrush(fill_color))
            else:
                self.line_curve.setFillLevel(None)
            self.line_curve.setData(self._hist_t, self._hist_c)
        else:
            self.line_curve.setData([])

    # -- colors ----------------------------------------------------------------

    def _pick_color(self, key: str) -> None:
        picked = QColorDialog.getColor(
            QColor(self._colors[key]), self, "Chart color"
        )
        if not picked.isValid():
            return
        # keep the terminal dark: backgrounds must stay near-black, marks must
        # stay visible on it — refuse picks that break the theme
        if key == "bg" and picked.lightness() > 40:
            self.set_status("⚠ background must stay dark — keeping old color")
            return
        if key != "bg" and picked.lightness() < 60:
            self.set_status("⚠ too dark for the black canvas — keeping old color")
            return
        self._colors[key] = picked.name()
        self._apply_colors()

    def _apply_colors(self) -> None:
        self.candle_item.set_colors(self._colors["up"], self._colors["down"])
        self.bar_item.set_colors(self._colors["up"], self._colors["down"])
        self.line_curve.setPen(pg.mkPen(self._colors["line"], width=1.5))
        self.plot_widget.setBackground(self._colors["bg"])
        for axis in ("left", "bottom"):
            self.plot_widget.getAxis(axis).setTextPen(self._colors["grid"])
            self.plot_widget.getAxis(axis).setPen(pg.mkPen(self._colors["grid"]))
        for inst in self._indicators:
            if inst.pane is not None:
                inst.pane.setBackground(self._colors["bg"])
                for axis in ("left", "bottom"):
                    inst.pane.getAxis(axis).setTextPen(self._colors["grid"])
                    inst.pane.getAxis(axis).setPen(pg.mkPen(self._colors["grid"]))
        # volume/MACD histograms derive bar colors from up/down
        self._apply_chart_type()
        self._refresh_all_indicators()

    def _toggle_grid(self, checked: bool) -> None:
        self._grid_on = checked
        self.plot_widget.showGrid(x=checked, y=checked, alpha=0.15)
        for inst in self._indicators:
            if inst.pane is not None:
                inst.pane.showGrid(x=checked, y=checked, alpha=0.15)

    def _toggle_log(self, checked: bool) -> None:
        self._log_on = checked
        self.plot_widget.setLogMode(y=checked)

    # -- extras ----------------------------------------------------------------

    def _export_png(self) -> None:
        sym = self.current_symbol or "chart"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export chart as PNG", f"{sym}_chart.png", "PNG image (*.png)"
        )
        if not path:
            return
        if self.grab().save(path, "PNG"):
            self.set_status(f"saved {path}")
        else:
            self.set_status("⚠ PNG export failed")

    def _reset_defaults(self) -> None:
        for inst in list(self._indicators):
            self._remove_indicator(inst)
        self._palette_iter = 0
        self._colors = dict(DEFAULT_COLORS)
        self._chart_type = "candles"
        self._grid_on = True
        self._log_on = False
        self._range = {"preset": "6mo"}
        self._interval = "1d"
        self._add_indicator("sma", {"window": 50}, color="#e91e63", rebuild=False)
        self._add_indicator("sma", {"window": 200}, color="#f8e71c", rebuild=False)
        self._add_indicator("rsi", {"window": 14}, color=ACCENT, rebuild=False)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLogMode(y=False)
        self._apply_colors()
        self._update_range_interval_buttons()
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self._last_quote = {}
        self._hist_t, self._hist_o, self._hist_c = [], [], []
        self._hist_hi, self._hist_lo, self._hist_v = [], [], []
        self._refresh_all_indicators()
        self._apply_chart_type()
        self._resubscribe(symbol)
        self._update_title()

    def _max_lookback_bars(self) -> int:
        return max(
            (inst.spec.lookback(inst.params) for inst in self._indicators if inst.on),
            default=0,
        )

    def _fetch_spec(self) -> tuple[str, str]:
        """Topic period token + interval to actually request. For daily preset
        ranges we widen the fetch (ladder) so the longest active indicator has
        enough lookback; for custom ranges we pull the start date back. The
        visible window is reframed to the selected range afterward."""
        interval = self._interval
        lookback = self._max_lookback_bars()
        if "preset" in self._range:
            preset = self._range["preset"]
            if interval != "1d":
                return preset, interval
            vis_days = int(PERIOD_SPAN_DAYS.get(preset, 100000) * 0.69)  # ~trading days
            need = vis_days + lookback + 10
            for cand, days in _DAILY_FETCH_LADDER:
                if days >= need:
                    return cand, "1d"
            return "max", "1d"
        # custom range: widen the fetched start so indicators warm up before
        # the visible window, clamped to what the interval can legally reach
        start = date.fromisoformat(self._range["start"])
        end = date.fromisoformat(self._range["end"])
        widen = int(lookback * INTERVAL_APPROX_DAYS.get(interval, 1.0) * 1.7) + 3
        fetch_start = start - timedelta(days=widen)
        cap = INTERVAL_MAX_BACK_DAYS.get(interval)
        if cap is not None:
            earliest = date.today() - timedelta(days=cap)
            fetch_start = max(fetch_start, earliest)
        return f"{fetch_start.isoformat()}..{end.isoformat()}", interval

    def _resubscribe(self, symbol: str) -> None:
        self.unsubscribe_all()
        period_token, interval = self._fetch_spec()
        topic = f"history:{symbol}:{period_token}:{interval}"
        self.subscribe(topic, self._on_history)
        self.subscribe(f"quote:{symbol}", self._on_quote)

    def _frame_window(self, t: list, highs: list, lows: list) -> None:
        """Zoom the plot to the selected range even though more bars may have
        been fetched, and fit Y to just the visible candles."""
        if not t:
            return
        if "preset" in self._range:
            end = t[-1]
            span = PERIOD_SPAN_DAYS.get(self._range["preset"], 100000) * 86400
            start = max(end - span, t[0])
        else:
            start = datetime.fromisoformat(self._range["start"]).timestamp()
            end = (
                datetime.fromisoformat(self._range["end"]) + timedelta(days=1)
            ).timestamp()
        self.plot_widget.setXRange(start, end, padding=0.02)
        vis = [(lows[i], highs[i]) for i in range(len(t)) if start <= t[i] <= end]
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
        v = data.get("v") or []
        if not t:
            self.set_status(f"{self.current_symbol} · no data")
            self._hist_t, self._hist_o, self._hist_c = [], [], []
            self._hist_hi, self._hist_lo, self._hist_v = [], [], []
            self._refresh_all_indicators()
            return
        self.candle_item.set_ohlc(t, o, h, l, c)
        self.bar_item.set_ohlc(t, o, h, l, c)

        n = min(len(t), len(o), len(h), len(l), len(c))
        valid_t: list = []
        valid_o: list = []
        valid_c: list = []
        valid_h: list = []
        valid_l: list = []
        valid_v: list = []
        for i in range(n):
            ti, oi, hi, li, ci = t[i], o[i], h[i], l[i], c[i]
            if None in (ti, oi, hi, li, ci):
                continue
            valid_t.append(float(ti))
            valid_o.append(float(oi))
            valid_c.append(float(ci))
            valid_h.append(float(hi))
            valid_l.append(float(li))
            vi = v[i] if i < len(v) else None
            valid_v.append(float(vi) if vi is not None else 0.0)
        self._hist_t, self._hist_o, self._hist_c = valid_t, valid_o, valid_c
        self._hist_hi, self._hist_lo, self._hist_v = valid_h, valid_l, valid_v
        self._refresh_all_indicators()
        self._apply_chart_type()
        self._frame_window(valid_t, valid_h, valid_l)

        rng = self._range.get("preset") or (
            f"{self._range.get('start')}→{self._range.get('end')}"
        )
        self.set_status(f"{self.current_symbol} · {rng} · {self._interval}")

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
            color = self._colors["up"] if change_pct >= 0 else self._colors["down"]
            sign = "+" if change_pct >= 0 else ""
            self.chg_lbl.setText(f"{sign}{change_pct:.2f}%")
            self.chg_lbl.setStyleSheet(f"color: {color}; font-weight: bold;")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "range": dict(self._range),
            "interval": self._interval,
            "chart_type": self._chart_type,
            "grid": self._grid_on,
            "log": self._log_on,
            "colors": dict(self._colors),
            "indicators": [
                {
                    "kind": inst.kind,
                    "params": dict(inst.params),
                    "color": inst.color,
                    "on": inst.on,
                }
                for inst in self._indicators
            ],
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return

        # legacy layouts (pre chart-customization): period + fixed sma50/100/
        # 200 + rsi flags — map onto the new model
        if "indicators" not in settings and (
            "period" in settings or "sma50" in settings
        ):
            settings = self._migrate_legacy(settings)

        rng = settings.get("range")
        if isinstance(rng, dict) and (
            rng.get("preset") in PERIOD_SPAN_DAYS
            or ("start" in rng and "end" in rng)
        ):
            self._range = dict(rng)
        interval = settings.get("interval")
        if interval in INTERVALS and not self._combo_problem(self._range, interval):
            self._interval = interval

        ctype = settings.get("chart_type")
        if ctype in dict(CHART_TYPES):
            self._chart_type = ctype
        self._grid_on = bool(settings.get("grid", self._grid_on))
        self._log_on = bool(settings.get("log", self._log_on))

        colors = settings.get("colors")
        if isinstance(colors, dict):
            for key in self._colors:
                val = colors.get(key)
                if isinstance(val, str) and QColor(val).isValid():
                    self._colors[key] = val

        saved = settings.get("indicators")
        if isinstance(saved, list):
            for inst in list(self._indicators):
                self._remove_indicator(inst)
            for entry in saved:
                if not isinstance(entry, dict) or entry.get("kind") not in INDICATOR_SPECS:
                    continue
                params = entry.get("params")
                self._add_indicator(
                    entry["kind"],
                    params if isinstance(params, dict) else {},
                    color=entry.get("color"),
                    on=bool(entry.get("on", True)),
                    rebuild=False,
                )

        self.plot_widget.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.15)
        self.plot_widget.setLogMode(y=self._log_on)
        self._apply_colors()
        self._update_range_interval_buttons()
        self._refresh_all_indicators()

        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    def _migrate_legacy(self, old: dict) -> dict:
        indicators = []
        for w, color in ((50, "#e91e63"), (100, "#4a90d9"), (200, "#f8e71c")):
            if old.get(f"sma{w}", w != 100):  # old defaults: 50/200 on, 100 off
                indicators.append(
                    {"kind": "sma", "params": {"window": w}, "color": color, "on": True}
                )
        if old.get("rsi", True):
            indicators.append(
                {"kind": "rsi", "params": {"window": 14}, "color": ACCENT, "on": True}
            )
        period = old.get("period")
        return {
            "range": {"preset": period if period in PERIOD_SPAN_DAYS else "6mo"},
            "interval": "1d",
            "chart_type": old.get("chart_type", "candles"),
            "grid": old.get("grid", True),
            "log": old.get("log", False),
            "indicators": indicators,
        }
