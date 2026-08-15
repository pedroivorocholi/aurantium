"""Chart panel — price history with selectable chart styles (candles, OHLC
bars, line, area), a registry-driven indicator system (SMA/EMA/Bollinger/
VWAP/MACD/RSI/Volume — add/remove/recolor any number), decoupled range +
candle interval with yfinance-constraint validation, and per-instance
persistence (Bloomberg G-chart style)."""

from __future__ import annotations

import bisect
import itertools
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QDate, QEvent, QPointF, QRectF, Qt
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

from ..components.empty_state import EmptyState
from ..panel import COMPACT, Panel, register_panel
from ..undo import UndoStack
from ..theme import (
    ACCENT,
    BG,
    BG_ELEV,
    DOWN,
    FG,
    FG_DIM,
    FG_MUTED,
    MONO_FONT,
    THEMES,
    UP,
    current_theme,
    palette_colors,
)

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

# -- indicator colors ------------------------------------------------------
#
# Per theme, like UP/DOWN, because one set of hues cannot clear 3:1 against
# both true black and white: a color light enough to read on #000000 is too
# pale on #ffffff. The palette that used to be here was a single theme-blind
# list, and it failed on both surfaces — #8bc34a and #ff7043 sat ΔE 3.2 apart
# under deuteranopia (indistinguishable), and #f8e71c rendered at 1.28:1 on
# the light theme, effectively invisible. Shipping a color-blind mode for
# up/down ticks while the chart itself collapsed was a half-kept promise.
#
# Four slots, not eight. Green, red and amber are all spoken for — gain, loss,
# and "this is the price" — so an indicator must stay clear of them as well as
# of the color-blind mode's blue/vermillion substitutes. What's left of the
# wheel holds four well-separated hues, not eight; more series would mean
# colors too close to tell apart, which is worse than reusing one. Charts with
# more than four indicators cycle, and the labeled color chip above the plot
# is what disambiguates them.
#
# Every value is computed, not picked: see tests/test_chart_palette.py, which
# re-runs the lightness, chroma, color-vision, contrast and reserved-color
# checks on each release. Do not hand-edit these without re-running it.
_INDICATOR_PALETTES = {
    # blue · olive · violet · teal
    "dark": ("#0c699a", "#6e6b03", "#7035f5", "#15957b"),
    "light": ("#2745f6", "#3c5b07", "#791c8a", "#0fa4b0"),
}


def indicator_palette(theme: str | None = None) -> tuple[str, ...]:
    """The indicator colors for a theme (defaults to the active one)."""
    name = theme or current_theme()
    return _INDICATOR_PALETTES.get(name, _INDICATOR_PALETTES["dark"])


def default_indicator_colors(theme: str | None = None) -> tuple[str, ...]:
    """Colors for the three indicators every chart opens with — SMA 50, SMA
    200, RSI. They take the first slots so the set the user always sees is the
    best-separated one, rather than whatever the cycle happened to land on."""
    return indicator_palette(theme)[:3]

DEFAULT_COLORS = {
    "up": UP,
    "down": DOWN,
    "line": ACCENT,
    "grid": FG_DIM,
    "bg": BG,
}

# Drawings (trendlines / channels) share one user-settable color, default blue.
# Kept out of DEFAULT_COLORS so it isn't remapped by the theme-default machinery.
DEFAULT_DRAWING_COLOR = "#2196f3"

# Drawing tools: internal id -> (menu label, number of clicks to place it).
DRAW_TOOLS = [
    ("trendline", "Trendline", 2),
    ("hline", "Horizontal line", 1),
    ("parallel", "Parallel channel", 3),
    ("flatbottom", "Flat-bottom channel", 3),
    ("disjoint", "Disjoint channel", 4),
]
DRAW_TOOL_POINTS = {tid: n for tid, _label, n in DRAW_TOOLS}
DRAW_TOOL_HINTS = {
    "trendline": "click start and end points",
    "hline": "click a price level",
    "parallel": "click 2 points for the base line, then a 3rd to set the width",
    "flatbottom": "click 2 points for the sloped top, then a 3rd for the flat bottom",
    "disjoint": "click 2 points for the first line, then 2 for the second",
}
# Point indices that move freely (no candle snapping) — the channel width/height
# controls, so the user can size a channel to any height.
DRAW_TOOL_FREE_POINTS = {"parallel": {2}, "flatbottom": {2}}


def _chart_defaults_for(theme_name: str) -> dict:
    """The default chart colors for a given theme (bg/grid follow it, up/down/
    line are that theme's accents)."""
    p = palette_colors(theme_name)
    return {
        "up": p["UP"],
        "down": p["DOWN"],
        "line": p["ACCENT"],
        "grid": p["FG_DIM"],
        "bg": p["BG"],
    }


# default chart colors per theme, and — per color key — the set of every theme's
# default value for it. A saved color that matches any theme default is treated
# as theme-derived (so it follows a theme switch); anything else is a genuine
# user customization and is preserved.
_CHART_DEFAULTS_BY_THEME = {t: _chart_defaults_for(t) for t in THEMES}
_THEME_DEFAULT_VALUES = {
    key: {defaults[key].lower() for defaults in _CHART_DEFAULTS_BY_THEME.values()}
    for key in DEFAULT_COLORS
}


def _mark_too_low_contrast(color: "QColor") -> bool:
    """True if a mark/indicator color would be illegible on the active canvas:
    too light on the light theme's white, too dark on the dark theme's black."""
    if current_theme() == "light":
        return color.lightness() > 210
    return color.lightness() < 60


def _mark_reject_msg() -> str:
    return (
        "⚠ too light for the white canvas"
        if current_theme() == "light"
        else "⚠ too dark for the black canvas"
    )

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


def _fmt_compact_num(value: Any) -> str:
    """Human-format a volume: T/B/M/K suffixes, plain otherwise."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    for suffix, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.1f}{suffix}"
    return f"{v:,.0f}"


def _line_y_at(xa, ya, xb, yb, x):
    """y of the line through (xa,ya)-(xb,yb) at x (flat if the line is vertical)."""
    if xb == xa:
        return ya
    return ya + (yb - ya) / (xb - xa) * (x - xa)


def _annotation_geometry(
    atype: str, pts: list, x_lo: float, x_hi: float
) -> tuple[list, Optional[tuple]]:
    """Turn a drawing's placed points into ``(lines, fill)``.

    ``lines`` is a list of ``(xs, ys)`` boundary strokes; ``fill`` is ``None`` or
    ``(xs_a, ys_a, xs_b, ys_b)`` — two curves over the same x samples whose
    interior is shaded (channel body). Renders whatever the points so far
    determine, so the same function drives both the finished drawing and the live
    rubber-band preview (placed points + current cursor). ``x_lo``/``x_hi`` bound
    horizontal lines to the data range.
    """
    lines: list[tuple[list, list]] = []
    fill: Optional[tuple] = None

    def seg(a, b) -> None:
        lines.append(([a[0], b[0]], [a[1], b[1]]))

    if atype == "hline":
        if pts:
            lines.append(([x_lo, x_hi], [pts[0][1], pts[0][1]]))
    elif atype == "trendline":
        if len(pts) >= 2:
            seg(pts[0], pts[1])
    elif atype == "parallel":
        if len(pts) >= 2:
            seg(pts[0], pts[1])
        if len(pts) >= 3:
            (x1, y1), (x2, y2), (x3, y3) = pts[0], pts[1], pts[2]
            if x2 != x1:
                m = (y2 - y1) / (x2 - x1)
                off = y3 - (y1 + m * (x3 - x1))
                lines.append(([x1, x2], [y1 + off, y2 + off]))
                fill = ([x1, x2], [y1, y2], [x1, x2], [y1 + off, y2 + off])
    elif atype == "flatbottom":
        if len(pts) >= 2:
            seg(pts[0], pts[1])
        if len(pts) >= 3:
            x1, x2 = pts[0][0], pts[1][0]
            yb = pts[2][1]
            lines.append(([x1, x2], [yb, yb]))
            fill = ([x1, x2], [pts[0][1], pts[1][1]], [x1, x2], [yb, yb])
    elif atype == "disjoint":
        if len(pts) >= 2:
            seg(pts[0], pts[1])
        if len(pts) >= 4:
            seg(pts[2], pts[3])
            (x1, y1), (x2, y2) = pts[0], pts[1]
            (x3, y3), (x4, y4) = pts[2], pts[3]
            xu0, xu1 = min(x1, x2, x3, x4), max(x1, x2, x3, x4)
            fill = (
                [xu0, xu1],
                [_line_y_at(x1, y1, x2, y2, xu0), _line_y_at(x1, y1, x2, y2, xu1)],
                [xu0, xu1],
                [_line_y_at(x3, y3, x4, y4, xu0), _line_y_at(x3, y3, x4, y4, xu1)],
            )
    return lines, fill


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
        description: str,
    ) -> None:
        self.kind = kind
        self.label = label
        self.pane = pane
        self.default_window = default_window  # None = no window parameter
        self.lookback = lookback  # bars of history the math needs to warm up
        self.description = description  # shown as a tooltip in the add menu


INDICATOR_SPECS: dict[str, _IndicatorSpec] = {
    "sma": _IndicatorSpec(
        "sma", "SMA", "price", 50, lambda p: p.get("window", 50),
        "Simple Moving Average — average closing price over the last N bars.",
    ),
    "ema": _IndicatorSpec(
        "ema", "EMA", "price", 21, lambda p: p.get("window", 21) * 3,
        "Exponential Moving Average — like SMA, but weights recent bars more heavily.",
    ),
    "bb": _IndicatorSpec(
        "bb", "BB", "price", 20, lambda p: p.get("window", 20),
        "Bollinger Bands — a moving average with upper/lower bands at N standard"
        " deviations, showing volatility.",
    ),
    "vwap": _IndicatorSpec(
        "vwap", "VWAP", "price", None, lambda p: 0,
        "Volume Weighted Average Price — average price weighted by traded volume.",
    ),
    "volume": _IndicatorSpec(
        "volume", "VOL", "osc", None, lambda p: 0,
        "Volume — number of shares traded per bar.",
    ),
    "rsi": _IndicatorSpec(
        "rsi", "RSI", "osc", 14, lambda p: p.get("window", 14) + 1,
        "Relative Strength Index — momentum oscillator (0–100) showing"
        " overbought/oversold conditions.",
    ),
    "macd": _IndicatorSpec(
        "macd", "MACD", "osc", None, lambda p: 26 + 9,
        "Moving Average Convergence Divergence — trend-following momentum"
        " indicator from the difference of two EMAs.",
    ),
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

        # crosshair readout + drawings (#23). Each annotation is
        # {"type": <tool>, "points": [[x, y], ...]}; all share one drawing color.
        # Annotations are tied to the symbol they were drawn on so they don't
        # float over a different symbol's data after the linked symbol changes.
        self._annotations: list[dict] = []
        self._annotation_items: list = []
        self._annotations_symbol = ""
        self._drawing_color = DEFAULT_DRAWING_COLOR
        self._draw_tool: Optional[str] = None
        self._draw_points: list = []
        self._preview_items: list = []

        # -- title row: ticker · price · change ------------------------------
        title_row = QHBoxLayout()
        title_row.setContentsMargins(2, 2, 2, 2)
        title_row.setSpacing(10)
        self.title_lbl = QLabel("", self)
        self.title_lbl.setVisible(False)  # shown once a symbol arrives
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

        # -- range + interval selectors ---------------------------------------
        # Two rows. They were briefly merged into one to save a band of chrome,
        # but ten range chips plus eight interval chips need ~890px of minimum
        # width, and a chart panel sharing the screen with another column is
        # narrower than that — the interval chips were pushed past the right
        # edge with no way to scroll to them, so the control was gone. A row of
        # vertical space is worth less than a control the user can reach.
        #
        # The density win came from the compact chip style, not the merge:
        # #chartChip took each row from ~50px to ~24px, so two rows still cost
        # a third of what three loose rows did.
        range_row = QHBoxLayout()
        self._range_row = range_row
        range_row.setSpacing(3)
        self._range_eyebrow = self._eyebrow("RANGE")
        range_row.addWidget(self._range_eyebrow)
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
        self._custom_btn.setObjectName("chartChip")
        self._custom_btn.clicked.connect(self._pick_custom_range)
        range_row.addWidget(self._custom_btn)
        range_row.addStretch(1)
        self.content_layout.addLayout(range_row)

        int_row = QHBoxLayout()
        self._interval_row = int_row
        int_row.setSpacing(3)
        self._interval_eyebrow = self._eyebrow("INTERVAL")
        int_row.addWidget(self._interval_eyebrow)
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
        self._indicators_eyebrow = self._eyebrow("INDICATORS")
        chips_row.addWidget(self._indicators_eyebrow)
        self._chips_row = chips_row
        # The chip style is load-bearing here, not cosmetic: the default
        # QPushButton padding is 13px a side, so inside a 28px fixed width Qt
        # had no room left for the glyph and elided it away — the only control
        # for adding an indicator rendered as an empty box.
        self._add_btn = QPushButton("+", self)
        self._add_btn.setObjectName("chartChip")
        self._add_btn.setFixedWidth(26)
        self._add_btn.setToolTip("Add an indicator")
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

        # -- empty state -------------------------------------------------------
        # A chart with no series still draws a full axis pair, so an unset (or
        # unavailable) symbol rendered as a grid labelled with meaningless
        # values — "59.600 / 21:00:00 / 0.2 / -0.2" — which reads as a broken
        # app rather than as "pick a name". Say so instead, and hide the axes
        # while there is nothing for them to measure.
        # Parented to the viewport, not the PlotWidget: a QGraphicsView is a
        # scroll area, and a child of the widget itself would be drawn behind
        # the viewport and never seen.
        self._empty = EmptyState(self.plot_widget.viewport())
        self.plot_widget.viewport().installEventFilter(self)

        # right-click settings menu on the chart
        self.plot_widget.setMenuEnabled(False)
        self.plot_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plot_widget.customContextMenuRequested.connect(self._show_chart_menu)

        self._setup_crosshair()

        # default working set mirrors the old fixed layout: SMA50 + SMA200 + RSI.
        # RSI used to draw in ACCENT — the amber that means "this is the price"
        # everywhere else in the app, so the oscillator wore the one color
        # already spoken for. All three now come from the checked palette.
        _sma50, _sma200, _rsi = default_indicator_colors()
        self._add_indicator("sma", {"window": 50}, color=_sma50, rebuild=False)
        self._add_indicator("sma", {"window": 200}, color=_sma200, rebuild=False)
        self._add_indicator("rsi", {"window": 14}, color=_rsi, rebuild=False)

        self._update_range_interval_buttons()
        # last in build(), so the default indicators above already exist and
        # their sub-panes hide along with the empty price plot
        self._sync_empty()

    # -- responsive controls -------------------------------------------------

    #: What a narrow chart keeps: a day, a month, half a year, a year, and
    #: everything. Enough to cover the everyday moves without a horizontal row
    #: that clips off the panel edge.
    COMPACT_RANGES = ("1d", "1mo", "6mo", "1y", "max")
    COMPACT_INTERVALS = ("1m", "1h", "1d", "1wk")

    def on_size_class(self, size_class: str) -> None:
        """Reorganize the control rows for the width the panel now has.

        At 320px the full rows clipped: "RANGE" rendered as "RAN(", "custom…"
        as "to", and the add-indicator button was sliced in half — controls
        that are present but unreachable. Compact drops the eyebrows (the chips
        already say what they are) and the less-used presets, keeping the
        common set plus whatever is currently selected, so the row always fits.
        """
        compact = size_class == COMPACT
        self._range_eyebrow.setVisible(not compact)
        self._interval_eyebrow.setVisible(not compact)
        self._indicators_eyebrow.setVisible(not compact)
        self._custom_btn.setVisible(not compact)

        for label, btn in self._range_buttons.items():
            btn.setVisible(
                not compact
                or label in self.COMPACT_RANGES
                or btn.isChecked()  # never hide what is currently selected
            )
        for label, btn in self._interval_buttons.items():
            btn.setVisible(
                not compact or label in self.COMPACT_INTERVALS or btn.isChecked()
            )

    # -- empty state ---------------------------------------------------------

    def _sync_empty(self) -> None:
        """Show the "nothing to plot" message when there are no bars, and hide
        the axes with it — an axis labelled with placeholder numbers is worse
        than no axis at all. Indicator sub-panes hide entirely: an empty RSI
        strip with its 30/70 guides drawn is the same lie in miniature."""
        empty = not self._hist_t
        for inst in self._indicators:
            if inst.pane is not None:
                inst.pane.setVisible(not empty)
        self.plot_widget.getAxis("left").setVisible(not empty)
        self.plot_widget.getAxis("bottom").setVisible(not empty)
        if empty:
            if self.current_symbol:
                self._empty.set_text(
                    f"No price history for {self.current_symbol}",
                    "Try a different range or interval, or press F5 to refresh",
                )
            else:
                self._empty.set_text(
                    "No symbol selected",
                    "Click a ticker in any linked panel, or type one in the SYMBOL bar",
                )
            self._empty.setGeometry(self.plot_widget.viewport().rect())
            self._empty.show()
            self._empty.raise_()
        else:
            self._empty.hide()

    def _size_selector_btn(self, btn: QPushButton) -> None:
        """Size a range/interval chip to its label.

        These are terminal chips, not form buttons: they take the compact
        ``#chartChip`` style (see theme.py) rather than the app's default
        QPushButton padding, which spent roughly a third of a short chart
        panel's height on three rows of selectors. Width still tracks the
        label — the checked state renders bold, which is wider than the normal
        font's metrics, hence the margin — so '3mo'/'15m' never clip.
        """
        btn.setObjectName("chartChip")
        text_w = btn.fontMetrics().horizontalAdvance(btn.text())
        btn.setFixedWidth(max(30, text_w + 18))

    def _eyebrow(self, text: str) -> QLabel:
        # FG_MUTED, not a hardcoded grey: the literal that used to be here was
        # picked for the dark theme and stayed put on the light one.
        lbl = QLabel(text, self)
        lbl.setStyleSheet(
            f"color: {FG_MUTED}; font-size: 10px; font-weight: 600;"
            " letter-spacing: 1px;"
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
        palette = indicator_palette()
        color = palette[self._palette_iter % len(palette)]
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
            if _mark_too_low_contrast(picked):
                self.set_status(f"{_mark_reject_msg()} — pick another color")
                seed = picked.name()
                continue
            return picked.name()

    def _show_add_menu(self) -> None:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for kind, spec in INDICATOR_SPECS.items():
            text = f"Add {spec.label}…" if spec.default_window else f"Add {spec.label}"
            act = QAction(text, menu)
            act.setToolTip(spec.description)
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
        if _mark_too_low_contrast(picked):
            self.set_status(f"{_mark_reject_msg()} — keeping old color")
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
            # release the pg item explicitly — removeItem only detaches it from
            # the plot; without this, repeatedly adding/removing indicators
            # accumulates orphaned GraphicsObjects until the next GC pass.
            if hasattr(item, "deleteLater"):
                item.deleteLater()
        inst.items = []
        if inst.pane is not None:
            self.content_layout.removeWidget(inst.pane)
            inst.pane.deleteLater()  # destroys the pane's scene and its pane_items
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
        draw_menu = menu.addMenu("Drawing")
        for tid, label, _n in DRAW_TOOLS:
            act = QAction(label, draw_menu)
            act.triggered.connect(lambda _=False, t=tid: self._start_tool(t))
            draw_menu.addAction(act)
        draw_menu.addSeparator()
        color_act = QAction("Drawing color…", draw_menu)
        color_act.triggered.connect(self._pick_drawing_color)
        draw_menu.addAction(color_act)
        undo_act = QAction("Remove last drawing", draw_menu)
        undo_act.setEnabled(bool(self._annotations))
        undo_act.triggered.connect(self._remove_last_drawing)
        draw_menu.addAction(undo_act)
        clear_act = QAction("Clear drawings", draw_menu)
        clear_act.setEnabled(bool(self._annotations))
        clear_act.triggered.connect(self._clear_drawings_action)
        draw_menu.addAction(clear_act)

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
        # keep marks legible on the canvas — the constraint flips with the theme:
        # a dark theme needs a near-black bg and light marks; a light theme needs
        # a near-white bg and dark marks.
        light = current_theme() == "light"
        if key == "bg":
            if light and picked.lightness() < 200:
                self.set_status("⚠ background must stay light — keeping old color")
                return
            if not light and picked.lightness() > 40:
                self.set_status("⚠ background must stay dark — keeping old color")
                return
        else:
            if light and picked.lightness() > 210:
                self.set_status("⚠ too light for the white canvas — keeping old color")
                return
            if not light and picked.lightness() < 60:
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
        # crosshair follows the grid/axis color; text keeps FG via setHtml
        if hasattr(self, "_cross_v"):
            cross_pen = pg.mkPen(
                self._colors["grid"], width=1, style=Qt.PenStyle.DashLine
            )
            self._cross_v.setPen(cross_pen)
            self._cross_h.setPen(cross_pen)
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
        self._drawing_color = DEFAULT_DRAWING_COLOR
        self._clear_drawings()
        self._palette_iter = 0
        self._colors = dict(DEFAULT_COLORS)
        self._chart_type = "candles"
        self._grid_on = True
        self._log_on = False
        self._range = {"preset": "6mo"}
        self._interval = "1d"
        _sma50, _sma200, _rsi = default_indicator_colors()
        self._add_indicator("sma", {"window": 50}, color=_sma50, rebuild=False)
        self._add_indicator("sma", {"window": 200}, color=_sma200, rebuild=False)
        self._add_indicator("rsi", {"window": 14}, color=_rsi, rebuild=False)
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLogMode(y=False)
        self._apply_colors()
        self._update_range_interval_buttons()
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    # -- crosshair + OHLC readout (#23) ----------------------------------------

    def _setup_crosshair(self) -> None:
        """A vertical+horizontal crosshair that snaps to the nearest bar, plus a
        pinned OHLC readout. Lines/text are added with ignoreBounds so they never
        affect auto-range."""
        pen = pg.mkPen(self._colors["grid"], width=1, style=Qt.PenStyle.DashLine)
        self._cross_v = pg.InfiniteLine(angle=90, movable=False, pen=pen)
        self._cross_h = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        for ln in (self._cross_v, self._cross_h):
            ln.setZValue(20)
            ln.setVisible(False)
            self.plot_widget.addItem(ln, ignoreBounds=True)
        self._cross_text = pg.TextItem(anchor=(0, 0), color=self._colors["grid"])
        self._cross_text.setZValue(21)
        self._cross_text.setVisible(False)
        self.plot_widget.addItem(self._cross_text, ignoreBounds=True)

        scene = self.plot_widget.scene()
        scene.sigMouseMoved.connect(self._on_mouse_moved)
        scene.sigMouseClicked.connect(self._on_scene_clicked)
        # hide the crosshair when the pointer leaves the plot
        self.plot_widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        """One filter for both plot-widget concerns — the crosshair and the
        empty-state overlay. They must share a method: a second ``eventFilter``
        defined later in the class body would silently replace this one."""
        if obj is self.plot_widget and event.type() == QEvent.Type.Leave:
            self._hide_crosshair()
        elif (
            obj is self.plot_widget.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            # Unconditional, not gated on isVisible(): the resizes that matter
            # happen while the window is still being laid out, when isVisible()
            # is still False for every widget — gating there leaves the overlay
            # stuck at its initial geometry and the message off-center.
            self._empty.setGeometry(self.plot_widget.viewport().rect())
        return super().eventFilter(obj, event)

    def _hide_crosshair(self) -> None:
        for item in (self._cross_v, self._cross_h, self._cross_text):
            item.setVisible(False)

    def _nearest_index(self, x: float) -> Optional[int]:
        t = self._hist_t
        if not t:
            return None
        j = bisect.bisect_left(t, x)
        if j <= 0:
            return 0
        if j >= len(t):
            return len(t) - 1
        return j if (t[j] - x) < (x - t[j - 1]) else j - 1

    def _on_mouse_moved(self, pos) -> None:
        if not self._hist_t:
            self._hide_crosshair()
            return
        if not self.plot_widget.sceneBoundingRect().contains(pos):
            self._hide_crosshair()
            return
        vb = self.plot_widget.getViewBox()
        mp = vb.mapSceneToView(pos)
        i = self._nearest_index(mp.x())
        if i is None:
            self._hide_crosshair()
            return
        self._cross_v.setPos(self._hist_t[i])
        self._cross_h.setPos(mp.y())
        self._cross_v.setVisible(True)
        self._cross_h.setVisible(True)
        self._update_readout(i)
        # while a drawing tool is active, rubber-band the in-progress shape to
        # the cursor so it follows the crosshair until the next click
        if self._draw_tool is not None:
            self._update_preview(mp)

    def _update_readout(self, i: int) -> None:
        o, h, l, c = self._hist_o[i], self._hist_hi[i], self._hist_lo[i], self._hist_c[i]
        v = self._hist_v[i] if i < len(self._hist_v) else None
        when = datetime.fromtimestamp(self._hist_t[i])
        fmt = "%Y-%m-%d" if self._interval in ("1d", "1wk", "1mo") else "%Y-%m-%d %H:%M"
        col = self._colors["up"] if c >= o else self._colors["down"]
        vol = f"  V {_fmt_compact_num(v)}" if v else ""
        self._cross_text.setHtml(
            f'<div style="font-family:monospace;font-size:11px;color:{FG};">'
            f'{when.strftime(fmt)}<br>'
            f'O {o:,.2f}&nbsp; H {h:,.2f}&nbsp; L {l:,.2f}&nbsp; '
            f'<span style="color:{col};">C {c:,.2f}</span>{vol}</div>'
        )
        # pin the readout to the top-left corner of the current view
        (xmin, _xmax), (_ymin, ymax) = self.plot_widget.getViewBox().viewRange()
        self._cross_text.setPos(xmin, ymax)
        self._cross_text.setVisible(True)

    # -- drawing tools: trendlines & channels (#23) ----------------------------

    def _data_x_range(self) -> tuple[float, float]:
        """X span for horizontal drawings — the loaded bars, or the current
        view when there's no data yet."""
        if self._hist_t:
            return self._hist_t[0], self._hist_t[-1]
        (xlo, xhi), _ = self.plot_widget.getViewBox().viewRange()
        return xlo, xhi

    def _snap_point(self, mp) -> tuple:
        """Snap a view-coordinate point to the nearest bar's time and to that
        bar's nearest O/H/L/C price, so drawings anchor to candles."""
        i = self._nearest_index(mp.x())
        if i is None:
            return (mp.x(), mp.y())
        ohlc = (self._hist_o[i], self._hist_hi[i], self._hist_lo[i], self._hist_c[i])
        y = min(ohlc, key=lambda v: abs(v - mp.y()))
        return (self._hist_t[i], y)

    def _place_point(self, mp, index: int) -> tuple:
        """The coordinate for the point being placed. Most points snap to a
        candle; the channel width/height controls move freely so the user can
        size a channel to any height."""
        free = index in DRAW_TOOL_FREE_POINTS.get(self._draw_tool, ())
        return (mp.x(), mp.y()) if free else self._snap_point(mp)

    def _snapshot_drawings(self) -> tuple:
        return (
            [{"type": a["type"], "points": [list(p) for p in a["points"]]}
             for a in self._annotations],
            self._drawing_color,
        )

    def _push_drawing_undo(self, label: str) -> None:
        """Record the current drawings + color so Ctrl+Z can restore them after
        the caller mutates them (add/remove/clear/recolor)."""
        anns, color = self._snapshot_drawings()

        def _undo() -> None:
            self._annotations = [
                {"type": a["type"], "points": [list(p) for p in a["points"]]}
                for a in anns
            ]
            self._drawing_color = color
            self._render_annotations()
            self.set_status(f"undo · {label}")

        UndoStack.instance().push(label, _undo)

    def _start_tool(self, tool: str) -> None:
        self._draw_tool = tool
        self._draw_points = []
        self._clear_preview()
        hint = DRAW_TOOL_HINTS.get(tool, "")
        self.set_status(f"drawing — {hint} (right-click to cancel)")

    def _cancel_tool(self) -> None:
        self._draw_tool = None
        self._draw_points = []
        self._clear_preview()
        self.set_status("drawing cancelled")

    def _on_scene_clicked(self, ev) -> None:
        if self._draw_tool is None:
            return
        if ev.button() != Qt.MouseButton.LeftButton:
            self._cancel_tool()
            ev.accept()
            return
        mp = self.plot_widget.getViewBox().mapSceneToView(ev.scenePos())
        self._draw_points.append(list(self._place_point(mp, len(self._draw_points))))
        ev.accept()
        needed = DRAW_TOOL_POINTS[self._draw_tool]
        if len(self._draw_points) >= needed:
            tool = self._draw_tool
            self._push_drawing_undo(f"add {tool}")
            self._annotations.append(
                {"type": tool, "points": [list(p) for p in self._draw_points[:needed]]}
            )
            self._annotations_symbol = self.current_symbol
            self._draw_tool = None
            self._draw_points = []
            self._clear_preview()
            self._render_annotations()
            self.set_status(f"{tool} added ({len(self._annotations)} drawing(s))")
        else:
            self.set_status(
                f"drawing — {len(self._draw_points)}/{needed} points placed"
            )

    def _polyline_item(self, xs: list, ys: list, *, preview: bool) -> pg.PlotDataItem:
        style = Qt.PenStyle.DashLine if preview else Qt.PenStyle.SolidLine
        width = 1.2 if preview else 1.5
        item = pg.PlotDataItem(
            xs, ys, pen=pg.mkPen(self._drawing_color, width=width, style=style)
        )
        item.setZValue(7 if preview else 6)
        self.plot_widget.addItem(item, ignoreBounds=True)
        return item

    def _fill_item(self, fill: tuple, *, preview: bool) -> pg.FillBetweenItem:
        """A shaded channel interior between two boundary curves."""
        xs_a, ys_a, xs_b, ys_b = fill
        curve_a = pg.PlotCurveItem(xs_a, ys_a)
        curve_b = pg.PlotCurveItem(xs_b, ys_b)
        brush_color = QColor(self._drawing_color)
        brush_color.setAlpha(28 if preview else 45)
        fb = pg.FillBetweenItem(curve_a, curve_b, brush=pg.mkBrush(brush_color))
        fb.setZValue(4)  # above candles, below the price line/indicators
        self.plot_widget.addItem(fb, ignoreBounds=True)
        return fb

    def _add_geometry_items(self, atype, pts, *, preview: bool) -> None:
        x_lo, x_hi = self._data_x_range()
        lines, fill = _annotation_geometry(atype, pts, x_lo, x_hi)
        target = self._preview_items if preview else self._annotation_items
        if fill is not None:
            target.append(self._fill_item(fill, preview=preview))
        for xs, ys in lines:
            target.append(self._polyline_item(xs, ys, preview=preview))

    def _render_annotations(self) -> None:
        for item in self._annotation_items:
            self.plot_widget.removeItem(item)
        self._annotation_items = []
        for ann in self._annotations:
            self._add_geometry_items(ann["type"], ann["points"], preview=False)

    def _clear_preview(self) -> None:
        for item in self._preview_items:
            self.plot_widget.removeItem(item)
        self._preview_items = []

    def _update_preview(self, mp) -> None:
        self._clear_preview()
        if self._draw_tool is None:
            return
        cursor = self._place_point(mp, len(self._draw_points))
        pts = self._draw_points + [list(cursor)]
        self._add_geometry_items(self._draw_tool, pts, preview=True)

    def _pick_drawing_color(self) -> None:
        picked = QColorDialog.getColor(
            QColor(self._drawing_color), self, "Drawing color"
        )
        if not picked.isValid():
            return
        self._push_drawing_undo("drawing color")
        self._drawing_color = picked.name()
        self._render_annotations()
        self.set_status("drawing color updated")

    def _remove_last_drawing(self) -> None:
        if not self._annotations:
            return
        self._push_drawing_undo("remove drawing")
        self._annotations.pop()
        self._render_annotations()
        self.set_status(f"removed drawing ({len(self._annotations)} left)")

    def _clear_drawings_action(self) -> None:
        """User-invoked 'Clear drawings' (undoable). Distinct from the internal
        clear used on symbol change / reset, which must not create undo steps."""
        if not self._annotations:
            return
        self._push_drawing_undo("clear drawings")
        self._clear_drawings()
        self.set_status("cleared drawings")

    def _clear_drawings(self) -> None:
        self._annotations = []
        self._clear_preview()
        self._render_annotations()

    # -- linked-symbol lifecycle ------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        # trendlines are drawn against a specific symbol's bars — drop them when
        # the linked symbol actually changes (restore() re-seeds the tag so
        # persisted drawings survive a layout reload of the same symbol).
        if symbol != self._annotations_symbol:
            self._clear_drawings()
            self._annotations_symbol = symbol
        self.set_status("loading…")
        self._hide_crosshair()
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
            self.set_status("no data")
            self._hist_t, self._hist_o, self._hist_c = [], [], []
            self._hist_hi, self._hist_lo, self._hist_v = [], [], []
            self._refresh_all_indicators()
            self._sync_empty()
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
        self._render_annotations()  # keep horizontal drawings spanning the bars

        rng = self._range.get("preset") or (
            f"{self._range.get('start')}→{self._range.get('end')}"
        )
        self.set_status(f"{rng} · {self._interval}")
        self._sync_empty()

    def _on_quote(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._last_quote = data
        self._update_title()

    def _update_title(self) -> None:
        # With no symbol the row held a lone em dash above an empty canvas —
        # a placeholder for a placeholder. The empty state on the plot says it
        # properly, so the title row simply steps aside until there's a name.
        sym = self.current_symbol
        self.title_lbl.setVisible(bool(sym))
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
            "annotations": [
                {"type": a["type"], "points": [list(p) for p in a["points"]]}
                for a in self._annotations
            ],
            "annotations_symbol": self._annotations_symbol,
            "drawing_color": self._drawing_color,
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
            active_defaults = _CHART_DEFAULTS_BY_THEME[current_theme()]
            for key in self._colors:
                val = colors.get(key)
                if not (isinstance(val, str) and QColor(val).isValid()):
                    continue
                if val.lower() in _THEME_DEFAULT_VALUES[key]:
                    # theme-derived default (from whichever theme it was saved
                    # in) → adopt the ACTIVE theme's default so a chart saved in
                    # dark doesn't stay black after switching to light, and vice
                    # versa. Genuine custom picks fall through and are kept.
                    self._colors[key] = active_defaults[key]
                else:
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

        # drawings (validated) + the symbol they belong to, so on_symbol() keeps
        # them when the same symbol is re-applied post-restore
        dc = settings.get("drawing_color")
        if isinstance(dc, str) and QColor(dc).isValid():
            self._drawing_color = dc
        anns = settings.get("annotations")
        if isinstance(anns, list):
            clean: list[dict] = []
            for a in anns:
                if not isinstance(a, dict):
                    continue
                # migrate the original single-line format {x1,y1,x2,y2}
                if "type" not in a and all(k in a for k in ("x1", "y1", "x2", "y2")):
                    try:
                        clean.append({
                            "type": "trendline",
                            "points": [[float(a["x1"]), float(a["y1"])],
                                       [float(a["x2"]), float(a["y2"])]],
                        })
                    except (TypeError, ValueError):
                        pass
                    continue
                atype = a.get("type")
                pts = a.get("points")
                if atype not in DRAW_TOOL_POINTS or not isinstance(pts, list):
                    continue
                try:
                    cpts = [[float(p[0]), float(p[1])] for p in pts]
                except (TypeError, ValueError, IndexError):
                    continue
                if len(cpts) >= DRAW_TOOL_POINTS[atype]:
                    clean.append({"type": atype, "points": cpts[:DRAW_TOOL_POINTS[atype]]})
            self._annotations = clean
            self._render_annotations()
        asym = settings.get("annotations_symbol")
        if isinstance(asym, str):
            self._annotations_symbol = asym

        self.plot_widget.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.15)
        self.plot_widget.setLogMode(y=self._log_on)
        self._apply_colors()
        self._update_range_interval_buttons()
        self._refresh_all_indicators()

        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    def _migrate_legacy(self, old: dict) -> dict:
        indicators = []
        # a layout saved before the palette was theme-aware carries no colors
        # of its own for these; give them the current checked slots
        _legacy = indicator_palette()
        for w, color in ((50, _legacy[0]), (100, _legacy[3]), (200, _legacy[1])):
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
