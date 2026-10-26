"""Chart panel — price history with selectable chart styles (candles, OHLC
bars, line, area), a registry-driven indicator system (SMA/EMA/Bollinger/
VWAP/MACD/RSI/Volume — add/remove/recolor any number), decoupled range +
candle interval with yfinance-constraint validation, and per-instance
persistence (Bloomberg G-chart style)."""

from __future__ import annotations

import bisect
import itertools
import math
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QDate, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QPainter,
    QPen,
    QPicture,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import motion
from ..guide import GuideSection, PanelGuide
from ..components.empty_state import EmptyState
from ..date_context import DateContext, bar_datetime
from ..panel import EMPTY_NO_SYMBOL, EMPTY_NO_SYMBOL_HINT, NULL_GLYPH, COMPACT, WIDE, Panel, register_panel
from ..color import contrast
from ..symbol_context import DEFAULT_GROUP, UNLINKED
from ..undo import UndoStack
from ..theme import (
    ACCENT,
    BG,
    CHROME_TEXT_DIM,
    DOWN,
    FG,
    FG_DIM,
    FG_MUTED,
    THEMES,
    UP,
    current_theme,
    palette_colors,
    series_palette,
    series_slot_of,
)
from ._events_lane import EventsLane, build_marks
from ..components.responsive_row import ResponsiveRow

# -- range / interval model -------------------------------------------------
#
# Range (how much history to show) and candle interval are independent.
# A range is a preset label, a custom (start, end) date pair, or a custom
# lookback — the last N minutes, hours or days up to the latest bar.

RANGE_PRESETS = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]

# Calendar span (days) of each preset — frames the visible window after we
# fetch extra history so long moving averages have enough lookback.
PERIOD_SPAN_DAYS = {
    "1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186,
    "1y": 372, "2y": 744, "5y": 1860, "max": 100000,
}

# Sub-day lookbacks aren't yfinance periods. They fetch 5d — enough to reach
# the last session over a weekend, and to warm up indicators — then frame the
# final hour(s) of bars.
SUB_DAY_FETCH_PERIOD = "5d"

# Custom lookback ranges: {"lookback": "4h"} — a count and a unit.
LOOKBACK_UNIT_DAYS = {"m": 1 / 1440, "h": 1 / 24, "d": 1.0}
LOOKBACK_UNIT_NAMES = {"m": "minute", "h": "hour", "d": "day"}
#: The shortest lookback that holds two 1m bars (see _combo_problem).
MIN_LOOKBACK_DAYS = 10 / 1440
#: 1h and 4h used to be chips on the range row; a layout saved with one of
#: them opens as the same lookback.
LEGACY_LOOKBACK_PRESETS = ("1h", "4h")

# The interval a range switches to when the current one can't serve it
# (e.g. 1d bars on a 1d range). Kept only when it is itself valid.
PREFERRED_INTERVAL = {
    "1d": "5m", "5d": "15m", "1mo": "1h",
    "3mo": "1d", "6mo": "1d", "1y": "1d", "2y": "1d", "5y": "1wk", "max": "1mo",
}



def lookback_days(token: Any) -> Optional[float]:
    """Calendar days a lookback token ("90m", "4h", "3d") spans, or None if
    it isn't one."""
    m = re.fullmatch(r"(\d+)([mhd])", token) if isinstance(token, str) else None
    if not m or int(m.group(1)) <= 0:
        return None
    return int(m.group(1)) * LOOKBACK_UNIT_DAYS[m.group(2)]


def lookback_label(token: str) -> str:
    """'4h' -> 'Last 4 hours'."""
    n, unit = int(token[:-1]), LOOKBACK_UNIT_NAMES[token[-1]]
    return f"Last {n} {unit}{'' if n == 1 else 's'}"


def range_span_days(rng: dict) -> Optional[float]:
    """Calendar span of a preset or lookback range; None for a date pair."""
    if "preset" in rng:
        return PERIOD_SPAN_DAYS.get(rng["preset"], 100000)
    if "lookback" in rng:
        return lookback_days(rng["lookback"]) or 100000
    return None


def preferred_interval(rng: dict) -> Optional[str]:
    """The interval a preset or lookback range defaults to. Sub-day
    lookbacks keep what the old 1h / 4h chips used: 1m up to an hour, 5m
    beyond; longer ones borrow the nearest preset's."""
    if "preset" in rng:
        return PREFERRED_INTERVAL.get(rng["preset"])
    days = lookback_days(rng.get("lookback"))
    if days is None:
        return None
    if days <= 1 / 24:
        return "1m"
    if days < 1:
        return "5m"
    for preset in RANGE_PRESETS:
        if PERIOD_SPAN_DAYS[preset] >= days:
            return PREFERRED_INTERVAL[preset]
    return PREFERRED_INTERVAL["max"]


#: Width of the price axis, and of every pane stacked under it (indicator
#: panes and the events lane). The panes share the price plot's time range, so
#: they must also share its left edge: with each axis sized to its own labels
#: ("350" vs "80") and the events lane showing none, the same date landed a few
#: — or, for the lane, thirty — pixels apart, and an event mark sat beside the
#: candle it explains instead of under it.
LEFT_AXIS_PX = 54

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

# Mouse-wheel zoom per notch. pyqtgraph's default (-1/8) jumps ~26% a notch,
# too coarse to settle on a window; this is ~15%.
WHEEL_SCALE_FACTOR = -1 / 15
# Zooming in stops here, rather than at a single pixel-wide sliver of a bar.
MIN_VISIBLE_BARS = 10

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
# Every value is computed, not picked: see tests/charts/test_chart_palette.py, which
# re-runs the lightness, chroma, color-vision, contrast and reserved-color
# checks on each release. Do not hand-edit these without re-running it.
#: Kept as a name because tests and older code reference it. The values now
#: live in ``theme._SERIES_PALETTES`` — one categorical palette for the whole
#: app rather than one per panel.
_INDICATOR_PALETTES = {name: series_palette(name) for name in THEMES}


def indicator_palette(theme: str | None = None) -> tuple[str, ...]:
    """The indicator colors for a theme (defaults to the active one).

    A thin alias for :func:`theme.series_palette`. Chart indicators were the
    first consumer of a validated categorical palette and for a while the only
    one; the colours are now shared with every other series in the app.
    """
    return series_palette(theme)


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
#: Defaults of earlier light palettes. A layout saved under one stores these
#: hexes; without them here they would pass for the user's own picks and pin
#: the chart to a palette that no longer exists.
_RETIRED_CHART_DEFAULTS = {
    "line": {"#b45309", "#a34e00"},
    "grid": {"#5b636d", "#4b535e"},
    "up": {"#0a8f3c"},
    "down": {"#d32f2f"},
}
for _key, _vals in _RETIRED_CHART_DEFAULTS.items():
    _THEME_DEFAULT_VALUES.setdefault(_key, set()).update(_vals)


def _mark_too_low_contrast(color: "QColor") -> bool:
    """True if a mark/indicator color would be illegible on the active canvas:
    too light on the light theme's white, too dark on the dark theme's black.

    A lightness heuristic against the *theme's* default canvas. Kept as-is for
    the colour picker, where it runs interactively and the user sees the
    rejection message; :func:`_illegible_on` is the measured version used when
    restoring a saved layout, where nobody is watching.
    """
    if current_theme() == "light":
        return color.lightness() > 210
    return color.lightness() < 60


def _illegible_on(color_hex: str, bg_hex: str) -> bool:
    """Whether a mark would fail the 3:1 mark-contrast floor on ``bg_hex``.

    Measured WCAG contrast against the canvas actually in use, rather than a
    lightness guess against the theme's default one — the canvas is
    user-settable, so the two can differ.
    """
    try:
        return contrast(color_hex, bg_hex) < 3.0
    except Exception:
        return False


def _restored_indicator_color(saved: Any, position: int, bg_hex: str) -> tuple[str | None, bool]:
    """The colour to actually use for a restored indicator, and whether it was
    substituted.

    Two failure modes, both of which shipped.

    **A theme-default colour stays put.** ``colors`` (up/down/line/grid/bg) has
    remapped across themes since the palette was made theme-aware; ``indicators``
    never did. An indicator drawn in the dark palette's ``#0c699a`` stayed
    ``#0c699a`` on the light theme, where that slot is ``#2745f6`` and where the
    dark value was never validated. Recognised slots are remapped.

    **A custom colour is never re-checked.** The picker only rejects a colour
    against the canvas *at the time of picking*, so a colour chosen on black
    sails through onto white years later. Measured on a real saved layout: SMA
    200 at ``#f8e71c`` — 1.28:1 on white, the exact value this module's own
    comment documents as rejected — and volume at ``#18d90a``, 1.91:1. Two of
    four indicators were invisible. A custom colour that fails the floor on the
    active canvas falls back to its slot rather than being drawn where it cannot
    be seen; an invisible line is a worse betrayal of the user's choice than a
    changed one, and the substitution is announced.
    """
    if not (isinstance(saved, str) and QColor(saved).isValid()):
        return None, False
    slot = series_slot_of(saved)
    if slot is not None:
        return indicator_palette()[slot], False
    if _illegible_on(saved, bg_hex):
        palette = indicator_palette()
        return palette[position % len(palette)], True
    return saved, False


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
        return NULL_GLYPH
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

#: logical size of the legend swatch drawn before an indicator chip's label
_SWATCH_SIZE = (12, 8)


def _chip_swatch(color: str, widget, dashed: bool = False) -> QIcon:
    """A short rounded line in ``color``: the indicator's legend mark, painted
    at the widget's device-pixel ratio so it stays crisp at any scaling."""
    from .. import icons

    dpr = icons.device_pixel_ratio(widget)
    w, h = _SWATCH_SIZE
    pix = QPixmap(round(w * dpr), round(h * dpr))
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 2.5 * dpr)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    if dashed:  # a comparison: dashed on the chart, so dashed in its legend
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setDashPattern([1.6, 1.2])
    p.setPen(pen)
    y = h * dpr / 2
    p.drawLine(QPointF(1.5 * dpr, y), QPointF((w - 1.5) * dpr, y))
    p.end()
    pix.setDevicePixelRatio(dpr)
    return QIcon(pix)


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
        #: the draw-in reveal, created on first use (ChartPanel._draw_in)
        self.anim: Optional[QVariantAnimation] = None
        #: (items, [(curve, xs, ys)], [(bars, heights)]) while drawing in
        self._reveal: tuple[list, list, list] = ([], [], [])

    @property
    def spec(self) -> _IndicatorSpec:
        return INDICATOR_SPECS[self.kind]

    def label(self) -> str:
        w = self.params.get("window")
        return f"{self.spec.label} {w}" if w else self.spec.label


#: How many symbols one chart compares against at once. Past four the lines
#: stop being readable against each other, which is the whole point.
MAX_COMPARES = 4


class _Compare:
    """One symbol overlaid on the price for comparison (a sector ETF, the
    index, a peer).

    Drawn rebased: at the first bar in view it sits exactly on the price, and
    from there it moves by its own percentage. Where the two lines part is
    where one out- or under-performed the other, read straight off the price
    axis with no second scale. Re-anchored on every pan and zoom, so the
    comparison always starts at the left edge of what is on screen."""

    def __init__(self, symbol: str, color: str, on: bool = True) -> None:
        self.symbol = symbol
        self.color = color
        self.on = on
        self.t: list[float] = []  # its own bars, as fetched
        self.c: list[float] = []
        self.aligned: list[Optional[float]] = []  # its close at each chart bar
        self.curve: Optional[pg.PlotDataItem] = None
        self.chip: Optional[QPushButton] = None


def align_closes(
    bar_t: list, other_t: list, other_c: list, bar_seconds: float
) -> list[Optional[float]]:
    """``other``'s close at each of the chart's bars: the latest close at or
    before the bar, so a series that trades different hours (an index, a
    foreign listing) still lines up. None before its first bar or long after
    its last one, rather than a flat line pretending to be data."""
    out: list[Optional[float]] = []
    if not other_t:
        return [None] * len(bar_t)
    stale = other_t[-1] + 2 * bar_seconds
    for t in bar_t:
        j = bisect.bisect_right(other_t, t) - 1
        out.append(other_c[j] if 0 <= j and t <= stale else None)
    return out


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
        self._log_y = False
        self._picture = QPicture()

    def set_colors(self, up: str, down: str) -> None:
        self._up, self._down = up, down
        self._generate()

    def setLogMode(self, x_mode: bool, y_mode: bool) -> None:  # noqa: N802 (pyqtgraph API)
        """Called by the PlotItem when log scale changes. pyqtgraph transforms
        its own curves; a custom picture item has to draw in log10 itself."""
        if bool(y_mode) != self._log_y:
            self._log_y = bool(y_mode)
            self._generate()

    def _view_rows(self) -> list:
        """The rows in view units: prices, or their log10 on a log axis."""
        if not self._log_y:
            return self._rows
        return [
            (t, math.log10(o), math.log10(h), math.log10(l), math.log10(c))
            for t, o, h, l, c in self._rows
            if min(o, h, l, c) > 0
        ]

    def _data_bounds(self, rows: list, half_width: float) -> QRectF:
        """Exact extent of the marks. QPicture.boundingRect() is an integer
        rect: harmless at prices, ten times too tall in log10 units."""
        if not rows:
            return QRectF()
        x0 = min(r[0] for r in rows) - half_width
        x1 = max(r[0] for r in rows) + half_width
        y0 = min(r[3] for r in rows)
        y1 = max(r[2] for r in rows)
        return QRectF(x0, y0, x1 - x0, (y1 - y0) or 1e-6)

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
        rows = self._view_rows()
        self._bounds = self._data_bounds(rows, half_width)
        for t, o, h, l, c in rows:
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
        return QRectF(getattr(self, "_bounds", QRectF()))


class OHLCBarItem(pg.GraphicsObject):
    """Classic OHLC bar: vertical low-high line, open tick left, close tick
    right; colored up/down like the candles."""

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[tuple[float, float, float, float, float]] = []
        self._up = UP
        self._down = DOWN
        self._log_y = False
        self._picture = QPicture()

    def set_colors(self, up: str, down: str) -> None:
        self._up, self._down = up, down
        self._generate()

    def setLogMode(self, x_mode: bool, y_mode: bool) -> None:  # noqa: N802 (pyqtgraph API)
        """Called by the PlotItem when log scale changes. pyqtgraph transforms
        its own curves; a custom picture item has to draw in log10 itself."""
        if bool(y_mode) != self._log_y:
            self._log_y = bool(y_mode)
            self._generate()

    def _view_rows(self) -> list:
        """The rows in view units: prices, or their log10 on a log axis."""
        if not self._log_y:
            return self._rows
        return [
            (t, math.log10(o), math.log10(h), math.log10(l), math.log10(c))
            for t, o, h, l, c in self._rows
            if min(o, h, l, c) > 0
        ]

    def _data_bounds(self, rows: list, half_width: float) -> QRectF:
        """Exact extent of the marks. QPicture.boundingRect() is an integer
        rect: harmless at prices, ten times too tall in log10 units."""
        if not rows:
            return QRectF()
        x0 = min(r[0] for r in rows) - half_width
        x1 = max(r[0] for r in rows) + half_width
        y0 = min(r[3] for r in rows)
        y1 = max(r[2] for r in rows)
        return QRectF(x0, y0, x1 - x0, (y1 - y0) or 1e-6)

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
        rows = self._view_rows()
        self._bounds = self._data_bounds(rows, tick)
        for t, o, h, l, c in rows:
            painter.setPen(pg.mkPen(self._up if c >= o else self._down, width=1))
            painter.drawLine(QPointF(t, l), QPointF(t, h))
            painter.drawLine(QPointF(t - tick, o), QPointF(t, o))
            painter.drawLine(QPointF(t, c), QPointF(t + tick, c))
        painter.end()
        self.update()

    def paint(self, painter: QPainter, *args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QRectF:
        return QRectF(getattr(self, "_bounds", QRectF()))


def _next_nice(step: float) -> float:
    """The next round step above ``step`` (1 → 1.5 → 2 → 2.5 → 3 → 4 → 5 → 10 ...).

    Finer than pyqtgraph's 1-2-5 so a short pane can land on two labels
    (e.g. 300M · 600M) instead of jumping from three crowded ones to one."""
    exp = 10.0 ** np.floor(np.log10(step))
    m = step / exp
    for n in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 10.0):
        if n > m * (1 + 1e-9):
            return n * exp
    return 20.0 * exp


class _PriceAxis(pg.AxisItem):
    """The price axis. Linear, it is pyqtgraph's own. Log, it labels prices.

    pyqtgraph's log axis only labels decades and their 2..9 multiples (100,
    200, 300 ...), so a chart between 240 and 348 got one label or none. A
    price chart lives inside a decade almost always, so here the ticks are
    chosen as round *prices* across the visible range and placed at their
    log10 — the same labels as linear, spaced logarithmically."""

    def logTickValues(self, minVal, maxVal, size, stdTicks):  # noqa: N802 (pyqtgraph API)
        lo, hi = 10.0 ** minVal, 10.0 ** maxVal
        levels = []
        seen: list[float] = []
        for spacing, offset in self.tickSpacing(lo, hi, size):
            start = math.ceil((lo - offset) / spacing) * spacing + offset
            values = []
            price = start
            while price <= hi:
                if price > 0 and not any(abs(price - p) < spacing * 0.01 for p in seen):
                    values.append(math.log10(price))
                    seen.append(price)
                price += spacing
            # the level carries its *price* spacing: logTickStrings formats with it
            levels.append((spacing, values))
        return levels

    def logTickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph API)
        places = max(0, math.ceil(-math.log10(spacing))) if spacing and spacing > 0 else 2
        return [f"{10.0 ** v:.{places}f}" for v in values]


class _SpacedAxis(pg.AxisItem):
    """Y axis whose labels never collide in a short pane.

    pyqtgraph always labels the major tick level no matter how cramped, so a
    squeezed indicator pane stacks its labels on top of each other. Here the
    major step is the smallest round one whose labels clear each other — but never
    past the point where no label fits inside the pane, so there is always at
    least one. Ticks too close to an edge to show their whole label (pyqtgraph
    silently drops those) are left out when picking the step."""

    def tickValues(self, minVal: float, maxVal: float, size: float):
        levels = super().tickValues(minVal, maxVal, size)
        lo_v, hi_v = sorted((minVal, maxVal))
        dif = hi_v - lo_v
        if not levels or dif == 0 or size <= 0 or self._tickSpacing is not None:
            return levels
        text_h = QFontMetrics(self.style["tickFont"] or self.font()).height()
        min_px = 1.0 * text_h  # pyqtgraph draws labels 0.8 text_h tall
        px_per_unit = size / dif
        margin = 0.4 * text_h / px_per_unit  # half a drawn label, in data units
        lo, hi = lo_v + margin, hi_v - margin

        def inside(step: float) -> list[float]:
            first = np.ceil(lo / step) * step
            # "+ 0.0" folds -0.0 into 0.0: ceil() of a hair-negative bound is
            # -0.0, which pyqtgraph printed as a "-0" tick under RSI
            return [float(v) + 0.0 for v in np.arange(first, hi + step * 1e-9, step)]

        # aim for ~3 text-heights between labels, or pyqtgraph's own spacing
        # if that's tighter; a short pane settles for anything >= min_px
        want_px = max(min_px, min(levels[0][0] * px_per_unit, 3 * text_h))
        step = 10.0 ** np.floor(np.log10(min_px / px_per_unit))
        while step * px_per_unit < want_px and inside(_next_nice(step)):
            step = _next_nice(step)
        ticks = inside(step)
        if step * px_per_unit < min_px:
            ticks = ticks[-1:]  # even the widest step crowds — keep just the top
        out = [(step, ticks)]
        # finer levels only where they subdivide the step evenly and their
        # labels clear the same gap
        for sp, _vals in levels[1:]:
            ratio = step / sp
            if (sp < step and abs(ratio - round(ratio)) < 1e-6
                    and sp * px_per_unit >= min_px):
                out.append((sp, [v for v in inside(sp) if v not in ticks]))
        return out


class _CompactAxis(_SpacedAxis):
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


# -- bar-slot time axis -------------------------------------------------------
#
# The chart places bar i at x = i, not at its timestamp. On clock time, every
# night, weekend and holiday was a stretch of empty plot: a 5-day 15-minute
# chart spent two thirds of its width on hours the market was shut. One slot
# per bar closes those gaps, and the bottom axis maps slots back to dates.

#: Calendar units the time axis can label by, finest first: (name, key).
#: A tick goes on the first bar of each new key; the finest unit whose ticks
#: still clear each other wins.
_TICK_UNITS: tuple[tuple[str, Callable[[datetime], Any]], ...] = (
    ("1min", lambda d: (d.date(), d.hour, d.minute)),
    ("5min", lambda d: (d.date(), d.hour, d.minute // 5)),
    ("15min", lambda d: (d.date(), d.hour, d.minute // 15)),
    ("30min", lambda d: (d.date(), d.hour, d.minute // 30)),
    ("1h", lambda d: (d.date(), d.hour)),
    ("2h", lambda d: (d.date(), d.hour // 2)),
    ("4h", lambda d: (d.date(), d.hour // 4)),
    ("day", lambda d: d.date()),
    ("week", lambda d: d.isocalendar()[:2]),
    ("month", lambda d: (d.year, d.month)),
    ("quarter", lambda d: (d.year, (d.month - 1) // 3)),
    ("year", lambda d: d.year),
    ("5y", lambda d: d.year // 5),
)
#: Minimum gap between two time labels, in pixels (a "14 Oct" label is ~40).
_TICK_MIN_PX = 80


class _BarClock:
    """Bar timestamps plus the per-unit tick positions derived from them,
    shared by every bottom axis on the chart. Recomputed only when the bars
    change (``times`` is replaced, never mutated in place)."""

    def __init__(self, times_of: Callable[[], list]) -> None:
        self._times_of = times_of
        self._key: tuple = ()
        self._dts: list[datetime] = []
        self._bounds: dict[str, list[int]] = {}

    def _sync(self) -> list[datetime]:
        t = self._times_of()
        key = (id(t), len(t))
        if key != self._key:
            self._key = key
            dts = []
            for v in t:
                try:
                    dts.append(bar_datetime(v))
                except (ValueError, OSError, OverflowError, TypeError):
                    dts.append(dts[-1] if dts else datetime(1970, 1, 1))
            self._dts = dts
            self._bounds = {}
        return self._dts

    def datetimes(self) -> list[datetime]:
        return self._sync()

    def boundaries(self, unit: str, keyfn: Callable[[datetime], Any]) -> list[int]:
        """Indices of the bars that open a new ``unit`` (bar 0 included)."""
        dts = self._sync()
        if unit not in self._bounds:
            out: list[int] = []
            prev = object()
            for i, d in enumerate(dts):
                k = keyfn(d)
                if k != prev:
                    out.append(i)
                    prev = k
            self._bounds[unit] = out
        return self._bounds[unit]


class _BarTimeAxis(pg.AxisItem):
    """Bottom axis for a bar-slot x: ticks on calendar boundaries (session
    open, day, month, year), labelled with the bar's real date or time."""

    def __init__(self, clock: _BarClock, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("orientation", "bottom")
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._unit = "day"

    def tickValues(self, minVal: float, maxVal: float, size: float):  # noqa: N802
        dts = self._clock.datetimes()
        n = len(dts)
        if not n or size <= 0 or maxVal <= minVal:
            return []
        i0 = max(0, math.ceil(minVal))
        i1 = min(n - 1, math.floor(maxVal))
        if i1 < i0:
            return []
        px_per_bar = size / (maxVal - minVal)
        budget = max(1, int(size // _TICK_MIN_PX))
        chosen: list[int] = []
        for unit, keyfn in _TICK_UNITS:
            b = self._clock.boundaries(unit, keyfn)
            lo, hi = bisect.bisect_left(b, i0), bisect.bisect_right(b, i1)
            chosen, self._unit = b[lo:hi], unit
            if hi - lo <= budget:
                break
        # boundaries are uneven (a short session, a holiday week), so a unit
        # that fits on count can still crowd locally — drop any label that
        # would land on its neighbour
        ticks: list[float] = []
        last = None
        for i in chosen:
            if last is None or (i - last) * px_per_bar >= _TICK_MIN_PX:
                ticks.append(float(i))
                last = i
        return [(1.0, ticks)]

    def tickStrings(self, values, scale, spacing) -> list[str]:  # noqa: N802
        dts = self._clock.datetimes()
        out: list[str] = []
        for v in values:
            i = int(round(v))
            if not 0 <= i < len(dts):
                out.append("")
                continue
            d = dts[i]
            before = dts[i - 1] if i > 0 else None
            unit = self._unit
            if unit in ("year", "5y"):
                s = d.strftime("%Y")
            elif unit in ("month", "quarter"):
                # the year where it turns over, so a long chart stays dated
                new_year = before is None or before.year != d.year
                s = d.strftime("%Y") if new_year else d.strftime("%b")
            elif unit in ("day", "week"):
                s = d.strftime("%d %b")
            else:
                # intraday: the first bar of each session carries the date
                new_day = before is None or before.date() != d.date()
                s = d.strftime("%d %b") if new_day else d.strftime("%H:%M")
            out.append(s)
        return out


# -- custom range dialog ----------------------------------------------------

#: Points both series are resampled to for the morph. Enough that a line
#: travelling between two shapes reads as a line, not a polygon; the real
#: candles crossfade in over it, so finer detail would never be seen.
MORPH_POINTS = 128


class _MorphOverlay(QWidget):
    """The line the morph paints, laid over the plot's viewport. Out of the
    scene on purpose: it travels in pixel space, so the two series need not
    share a bar count, an axis or a scale."""

    def __init__(self, viewport: QWidget, morph: "_SeriesMorph") -> None:
        super().__init__(viewport)
        self._morph = morph
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            pts = self._morph.points_now()
            if len(pts) >= 2:
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.setOpacity(self._morph.overlay_opacity)
                p.setPen(QPen(QColor(self._morph.color), 1.5))
                p.drawPolyline(pts)
        except (AttributeError, RuntimeError):
            pass  # panel torn down underneath us
        finally:
            p.end()


class _SeriesMorph:
    """The old symbol's line morphing into the new one's (motion.py, "Chart
    data transitions" — a deliberate, narrow exception to the budget).

    The chart's final state is always applied first, exactly as without
    motion. The morph then only borrows three things for ``CHART_MORPH_MS``:
    an overlay line travelling from the old shape to the new, the plot's Y
    range travelling from the old fit to the new one (so the axis rescales),
    and the opacity of the real series, which crossfades in over the second
    half. ``finish`` hands all three back, so any interruption — a pan, the
    next symbol, a range change, an empty result — can only land on the
    final chart.
    """

    def __init__(self, panel: "ChartPanel") -> None:
        self._panel = panel
        #: The old series in viewport pixels, plus the Y range it was drawn
        #: in, waiting for the new symbol's history. None when no morph is due.
        self._snapshot: Optional[tuple[list, tuple]] = None
        self._from: list[QPointF] = []
        self._to: list[QPointF] = []
        self._y_from = (0.0, 1.0)
        self._y_to = (0.0, 1.0)
        self._t = 1.0
        self._last_change = float("-inf")
        self.color = panel._colors["line"]
        self.overlay_opacity = 1.0
        self.overlay = _MorphOverlay(panel.plot_widget.viewport(), self)
        # One persistent animation per chart (see motion.fade_animation for
        # why throwaway DeleteWhenStopped animations are not used here).
        self.anim = motion.PacedAnimation(panel)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.setEasingCurve(motion.EASE_TRAVEL)
        self.anim.valueChanged.connect(self._step)
        self.anim.finished.connect(self.finish)
        # a drag or wheel on the plot is the user taking over: stop at once
        panel.plot_widget.getViewBox().sigRangeChangedManually.connect(
            lambda *_: self.finish()
        )

    def running(self) -> bool:
        """Under way (a PacedAnimation runs while its Qt state is Paused)."""
        return self.anim.state() != QVariantAnimation.State.Stopped

    # -- the three moments ---------------------------------------------------

    def symbol_changing(self) -> None:
        """Before the old series is cleared: snapshot it, unless this change
        follows the last one too quickly to be watched."""
        now = time.monotonic()
        was_running = self.running()
        self.finish()
        self._snapshot = None
        too_soon = was_running or (now - self._last_change) * 1000.0 < motion.CHART_MORPH_MS
        self._last_change = now
        if too_soon or not motion.animations_enabled():
            return
        pts = self._series_points()
        if pts:
            y_range = tuple(self._panel.plot_widget.getViewBox().viewRange()[1])
            self._snapshot = (pts, y_range)

    def history_arriving(self) -> None:
        """A history callback is about to apply. Anything that is not the
        answer to a pending symbol change (a live refresh, a widened fetch)
        lands any running morph first."""
        if self._snapshot is None:
            self.finish()

    def history_landed(self) -> None:
        """The new symbol's final chart is in place: morph into it."""
        snap, self._snapshot = self._snapshot, None
        if snap is None:
            return
        ms = motion.duration(motion.CHART_MORPH_MS)
        to = self._series_points()
        if ms == 0 or not to:
            return
        panel = self._panel
        self._from = _resample(snap[0])
        self._to = _resample(to)
        self._y_from = snap[1]
        self._y_to = tuple(panel.plot_widget.getViewBox().viewRange()[1])
        self.color = panel._colors["line"]
        self.overlay.setGeometry(panel.plot_widget.viewport().rect())
        self.overlay.show()
        self.overlay.raise_()
        self.anim.stop()
        self.anim.setDuration(ms)
        self._step(0.0)
        self.anim.start()
        # the volume pane answers the same change: its bars rise from the
        # baseline alongside the price line
        for inst in panel._indicators:
            if inst.kind == "volume":
                panel._draw_in(inst)

    def cancel(self) -> None:
        """Not a symbol change after all (a range or interval change, an empty
        result): drop the snapshot and land any running morph."""
        self._snapshot = None
        self.finish()

    def finish(self) -> None:
        """Land on the final chart now."""
        try:
            if self.running():
                self.anim.stop()
            active = self.overlay.isVisible()
            self.overlay.hide()
            self._t = 1.0
            for item in self._faded_items():
                item.setOpacity(1.0)
            if active:
                self._panel._fit_y_to_visible()
        except RuntimeError:
            pass  # panel destroyed mid-morph

    # -- frames --------------------------------------------------------------

    def points_at(self, t: float) -> list[QPointF]:
        return [
            QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
            for a, b in zip(self._from, self._to)
        ]

    def points_now(self) -> list[QPointF]:
        return self.points_at(self._t)

    def _step(self, value) -> None:
        try:
            self._t = float(value)
            panel = self._panel
            (a0, a1), (b0, b1) = self._y_from, self._y_to
            t = self._t
            panel._fitting_y = True  # keep _fit_y_to_visible out of the way
            try:
                panel.plot_widget.getViewBox().setYRange(
                    a0 + (b0 - a0) * t, a1 + (b1 - a1) * t, padding=0
                )
            finally:
                panel._fitting_y = False
            # The crossfade runs on linear time, not the eased value: the line
            # does its travelling early (EASE_OUT), and the real series takes
            # over across the second half, once the shape has nearly landed.
            duration = self.anim.duration()
            linear = self.anim.currentTime() / duration if duration else 1.0
            fade = min(1.0, max(0.0, (linear - 0.5) / 0.5))
            for item in self._faded_items():
                item.setOpacity(fade)
            self.overlay_opacity = 1.0 - fade
            self.overlay.update()
        except RuntimeError:
            pass  # panel destroyed mid-morph

    def _faded_items(self) -> list:
        panel = self._panel
        items = [panel.candle_item, panel.bar_item, panel.line_curve]
        for inst in panel._indicators:
            items.extend(inst.items)
        items.extend(cmp.curve for cmp in panel._compares if cmp.curve is not None)
        return items

    def _series_points(self) -> list[QPointF]:
        """The visible closes in viewport pixels. ViewBox mapping is affine,
        so three mapped points give the whole transform."""
        panel = self._panel
        closes = panel._hist_c
        vb = panel.plot_widget.getViewBox()
        if not closes or vb.width() <= 0 or vb.height() <= 0:
            return []
        (x0, x1), _ = vb.viewRange()
        i0 = max(0, math.ceil(x0))
        i1 = min(len(closes), math.floor(x1) + 1)
        if i1 - i0 < 2:
            return []
        ys = np.asarray(closes[i0:i1], dtype=float)
        if panel._log_on:
            if ys.min() <= 0:
                return []
            ys = np.log10(ys)
        view = panel.plot_widget.viewportTransform()

        def px(x: float, y: float) -> QPointF:
            return view.map(vb.mapViewToScene(QPointF(x, y)))

        origin, unit_x, unit_y = px(0.0, 0.0), px(1.0, 0.0), px(0.0, 1.0)
        xs = origin.x() + (unit_x.x() - origin.x()) * np.arange(i0, i1, dtype=float)
        ys_px = origin.y() + (unit_y.y() - origin.y()) * ys
        return [QPointF(float(x), float(y)) for x, y in zip(xs, ys_px)]


def _resample(points: list[QPointF], n: int = MORPH_POINTS) -> list[QPointF]:
    """``n`` points evenly spaced in x along the polyline ``points``, so two
    series of any bar count can be paired off point for point."""
    xs = np.array([p.x() for p in points])
    ys = np.array([p.y() for p in points])
    grid = np.linspace(xs[0], xs[-1], n)
    return [QPointF(float(x), float(y)) for x, y in zip(grid, np.interp(grid, xs, ys))]


class _RangeDialog(QDialog):
    """A trailing lookback (last N minutes / hours / days, with 1h and 4h as
    quick picks) or two date pickers, themed by the app-global stylesheet."""

    UNITS = (("minutes", "m"), ("hours", "h"), ("days", "d"))

    def __init__(
        self, parent, start: date, end: date, lookback: Optional[str] = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom range")
        lay = QVBoxLayout(self)
        self.last_radio = QRadioButton("Last", self)
        self.dates_radio = QRadioButton("From", self)
        group = QButtonGroup(self)
        group.addButton(self.last_radio)
        group.addButton(self.dates_radio)

        last_row = ResponsiveRow()
        self.count_spin = QSpinBox(self)
        self.count_spin.setRange(1, 9999)
        self.unit_combo = QComboBox(self)
        for name, _ in self.UNITS:
            self.unit_combo.addItem(name)
        last_row.addWidget(self.last_radio)
        last_row.addWidget(self.count_spin)
        last_row.addWidget(self.unit_combo)
        for token in LEGACY_LOOKBACK_PRESETS:
            quick = QPushButton(token, self)
            quick.setObjectName("chartChip")
            quick.clicked.connect(lambda _=False, t=token: self._quick_pick(t))
            last_row.addWidget(quick)
        last_row.addStretch(1)
        lay.addLayout(last_row)

        row = ResponsiveRow()
        self.start_edit = QDateEdit(QDate(start.year, start.month, start.day), self)
        self.end_edit = QDateEdit(QDate(end.year, end.month, end.day), self)
        for edit in (self.start_edit, self.end_edit):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setMaximumDate(QDate.currentDate())
        row.addWidget(self.dates_radio)
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

        self._set_lookback(lookback if lookback_days(lookback) else "4h")
        (self.last_radio if lookback else self.dates_radio).setChecked(True)
        # touching either side's inputs selects that mode
        self.count_spin.valueChanged.connect(lambda _: self.last_radio.setChecked(True))
        self.unit_combo.currentIndexChanged.connect(
            lambda _: self.last_radio.setChecked(True)
        )
        for edit in (self.start_edit, self.end_edit):
            edit.dateChanged.connect(lambda _: self.dates_radio.setChecked(True))

    def _set_lookback(self, token: str) -> None:
        self.count_spin.setValue(int(token[:-1]))
        units = [u for _, u in self.UNITS]
        self.unit_combo.setCurrentIndex(units.index(token[-1]))

    def _quick_pick(self, token: str) -> None:
        self._set_lookback(token)
        self.last_radio.setChecked(True)
        self._try_accept()

    def lookback(self) -> str:
        return f"{self.count_spin.value()}{self.UNITS[self.unit_combo.currentIndex()][1]}"

    def _try_accept(self) -> None:
        if self.last_radio.isChecked():
            if lookback_days(self.lookback()) < MIN_LOOKBACK_DAYS:
                self._hint.setText("Look back at least 10 minutes.")
                return
        elif self.start_edit.date() >= self.end_edit.date():
            self._hint.setText("Start must be before end.")
            return
        self.accept()

    def dates(self) -> tuple[date, date]:
        s, e = self.start_edit.date(), self.end_edit.date()
        return date(s.year(), s.month(), s.day()), date(e.year(), e.month(), e.day())

    def chosen_range(self) -> dict:
        """{"lookback": "4h"} or {"start": iso, "end": iso}."""
        if self.last_radio.isChecked():
            return {"lookback": self.lookback()}
        start, end = self.dates()
        return {"start": start.isoformat(), "end": end.isoformat()}


# -- panel ------------------------------------------------------------------

@register_panel(id="chart", title="Chart", category="Markets")
class ChartPanel(Panel):
    GUIDE = PanelGuide(
        purpose="Price chart for the linked symbol, with indicators, drawings and an events lane.",
        links="Follows the link group's symbol: the old line morphs into the new "
        "symbol's, the price axis rescales to it and volume rises from its "
        "baseline (instant when Windows "
        "animations are off, or when you step through symbols quickly). A click "
        "on a mark in the EVENTS lane opens the Day Brief on that date.",
        interactions=(
            "**RANGE** sets the window, 1d to max. **custom…** picks a trailing "
            "lookback (the last N minutes, hours or days, with 1h and 4h one click away) "
            "or explicit from/to dates. **INTERVAL** sets the bar size. A range the interval can't serve switches the interval for you. "
            "Bars sit side by side, so nights, weekends and holidays leave no gaps.",
            "Hover for a crosshair with the bar's date and open/high/low/close. Drag to "
            "scroll through time, wheel to zoom, double-click to reset.",
            "**INDICATORS**: **+** adds SMA, EMA, Bollinger Bands, VWAP, Volume, RSI or "
            "MACD; a new one draws in from the left (volume and MACD bars rise), and "
            "a removed one fades out. "
            "Click a chip to switch it off and on; right-click it to recolor, "
            "change its period or remove it.",
            "Right-click the chart for chart type, colors, grid, log scale, the events "
            "lane, drawing tools, export and reset options.",
            "The image button at the end of the indicator row exports the chart as it "
            "stands: **Save as PNG** or **Copy chart image** to paste elsewhere, each "
            "in **Dark** or **Light**, whatever theme the app is in. Also on the "
            "right-click menu.",
        ),
        fields=(
            ("SMA / EMA", "Simple and exponential moving averages of the close over N bars"),
            ("BB", "Bollinger Bands: a moving average with bands N standard deviations away"),
            ("VWAP", "Average price weighted by traded volume"),
            ("RSI", "Momentum oscillator from 0 to 100, with dashed lines at 70 and 30"),
            ("MACD", "Trend momentum from the difference of two EMAs"),
        ),
        shortcuts=(("D", "Explain the day under the crosshair in the Day Brief"),),
        sections=(
            GuideSection("Export", "The export reproduces the chart on screen, whatever "
                "range, interval, indicators, comparisons and log scale are on: the "
                "price pane, the events lane and every indicator pane with their axes, at "
                "twice the on-screen pixels. A header names the symbol, interval and range, "
                "with a legend entry in its plotted color for each series. The controls "
                "above the chart are left out. Pick **Dark** or **Light** for the image: "
                "the other theme recolors the chart by role (price line, up/down, axes, "
                "indicator slots); a color you picked is kept if it still reads on that "
                "background."),
            GuideSection("Drawing", "Right-click ▸ **Drawing** offers a trendline, a "
                "horizontal line, and parallel, flat-bottom or disjoint channels. Click "
                "to place each point; points snap to the nearest candle. Drawings are "
                "saved with the layout for that symbol. **Drawing color…**, **Remove last "
                "drawing** and **Clear drawings** sit in the same menu, and Ctrl+Z undoes."),
            GuideSection("Compare", "**vs** in the indicator row (or right-click ▸ "
                "**Compare**) overlays another symbol as a dashed line: the stock's **sector ETF**, its "
                "**index**, or any **other symbol**. Each line starts level with the "
                "price at the left edge of the view and moves by its own percentage, so "
                "where the lines part is where one beat the other; panning or zooming "
                "re-levels them. Its chip shows its move across the bars in view; hover "
                "for the gap to the chart's symbol, click to hide it, right-click to "
                "recolor or remove. Up to four at once, saved with the layout."),
            GuideSection("Events lane", "The strip under the price marks days worth "
                "explaining: **E** earnings, **D** ex-dividend, **S** split, ▲/▼ an "
                "analyst upgrade or downgrade, and ● a session whose move was unusually "
                "large for this stock. Hover a mark for its date; click it to open the "
                "Day Brief. Marks too close to tell apart merge into one showing how many "
                "it holds: hover it for the list, click it to zoom in until they separate. "
                "Right-click ▸ **Events lane** shows or hides the strip."),
            GuideSection("Asking the Day Brief", "Hover a bar and press **D**, or "
                "right-click a bar and choose **What happened on …?**, to explain that "
                "session in the Day Brief, which opens if it isn't already up."),
            GuideSection("Narrow panels", "Squeezed narrow, the range and interval rows "
                "keep the common choices; widen the panel and the rest come back."),
        ),
    )

    def build(self) -> None:
        # range is {"preset": "6mo"}, {"lookback": "4h"} or
        # {"start": iso, "end": iso}
        self._range: dict = {"preset": "6mo"}
        self._interval = "1d"
        self._last_quote: dict = {}
        self._hist_t: list = []
        self._hist_o: list = []
        self._hist_c: list = []
        self._hist_hi: list = []
        self._hist_lo: list = []
        self._hist_v: list = []
        # x is the bar's slot, not its timestamp (see _BarClock); every bottom
        # axis reads the real times through this one clock
        self._clock = _BarClock(lambda: self._hist_t)

        self._chart_type = "candles"  # candles | bars | line | area
        self._grid_on = True
        self._log_on = False
        self._colors = dict(DEFAULT_COLORS)
        self._indicators: list[_IndicatorInstance] = []
        self._fading_out: list[QVariantAnimation] = []  # removed overlays, mid-fade
        self._palette_iter = 0
        self._compares: list[_Compare] = []
        self._sector_wanted = False  # "compare with sector ETF" awaits the profile

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

        # -- events lane (#day-brief): marks under the price on days that have
        # something to explain, and the click target that opens a Day Brief.
        # Event payloads arrive on their own topics and are merged with the
        # unusual-move flags computed from the loaded closes.
        self._lane_on = True
        self._lane: Optional[EventsLane] = None
        self._lane_events: list[dict] = []
        self._menu_bar_date = ""  # bar under the cursor when the menu opened
        self._cross_index: Optional[int] = None

        # -- title row: ticker · price · change ------------------------------
        title_row = ResponsiveRow()
        title_row.setContentsMargins(2, 2, 2, 2)
        title_row.setSpacing(10)
        # Styled by role (theme.py). These were setFont() point sizes, which the
        # app sheet's blanket QWidget font rule overrode — the "headline" came
        # out at body size, and the price in the proportional face.
        self.title_lbl = QLabel("", self)
        self.title_lbl.setObjectName("chartTicker")
        self.title_lbl.setVisible(False)  # shown once a symbol arrives
        self.price_lbl = QLabel("", self)
        self.price_lbl.setObjectName("chartPrice")
        self.chg_lbl = QLabel("", self)
        self.chg_lbl.setObjectName("chartChange")
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
        range_row = ResponsiveRow()
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

        int_row = ResponsiveRow()
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
        chips_row = ResponsiveRow()
        chips_row.setSpacing(6)
        self._indicators_eyebrow = self._eyebrow("INDICATORS")
        chips_row.addWidget(self._indicators_eyebrow)
        self._chips_row = chips_row
        # The chip style is load-bearing here, not cosmetic: the default
        # QPushButton padding is 13px a side, so inside a 28px fixed width Qt
        # had no room left for the glyph and elided it away — the only control
        # for adding an indicator rendered as an empty box.
        self._add_btn = QPushButton(self)
        self._add_btn.setObjectName("chartChip")
        from .. import icons

        icons.set_icon(self._add_btn, "plus", CHROME_TEXT_DIM, disabled_color=FG_MUTED)
        self._add_btn.setAccessibleName("Add an indicator")
        self._add_btn.setFixedWidth(26)
        self._add_btn.setToolTip("Add an indicator")
        self._add_btn.clicked.connect(self._show_add_menu)
        chips_row.addWidget(self._add_btn)
        self._cmp_btn = QPushButton("vs", self)
        self._cmp_btn.setObjectName("chartChip")
        self._cmp_btn.setToolTip("Compare with another symbol: its sector ETF, the index, a peer")
        self._cmp_btn.clicked.connect(
            lambda: self._build_compare_menu(self).exec(
                self._cmp_btn.mapToGlobal(self._cmp_btn.rect().bottomLeft())
            )
        )
        chips_row.addWidget(self._cmp_btn)
        chips_row.addStretch(1)
        # export: the chart as it stands, saved or copied (_chart_export.py)
        self._export_btn = QPushButton(self)
        self._export_btn.setObjectName("chartChip")
        icons.set_icon(self._export_btn, "export-image", CHROME_TEXT_DIM, disabled_color=FG_MUTED)
        self._export_btn.setAccessibleName("Export chart image")
        self._export_btn.setFixedWidth(26)
        self._export_btn.setToolTip(
            "Export chart image: save as PNG or copy, dark or light, at 2× resolution"
        )
        self._export_btn.clicked.connect(
            lambda: self._build_export_menu(self).exec(
                self._export_btn.mapToGlobal(self._export_btn.rect().bottomLeft())
            )
        )
        chips_row.addWidget(self._export_btn)
        self.content_layout.addLayout(chips_row)

        # -- price plot --------------------------------------------------------
        self.plot_widget = pg.PlotWidget(
            axisItems={"bottom": _BarTimeAxis(self._clock), "left": _PriceAxis(orientation="left")}
        )
        self.plot_widget.setBackground(self._colors["bg"])
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.getAxis("left").setTextPen(FG_DIM)
        self.plot_widget.getAxis("left").setWidth(LEFT_AXIS_PX)
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
        self._setup_navigation()
        self._morph = _SeriesMorph(self)

        # -- events lane -------------------------------------------------------
        # Deliberately its own x-linked strip rather than marks inside the price
        # plot: nothing to reposition on zoom, no fight with auto-range, and a
        # far bigger click target. See _events_lane.py for the full argument.
        lane_pane = pg.PlotWidget(axisItems={"bottom": _BarTimeAxis(self._clock)})
        lane_pane.setXLink(self.plot_widget)
        lane_pane.getAxis("left").setWidth(LEFT_AXIS_PX)  # gutter under the price axis
        self._lane = EventsLane(lane_pane, self._pick_date, self._colors["bg"])
        self.content_layout.addWidget(lane_pane)

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
        self._install_day_shortcut()

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

    #: What a narrow chart keeps: a day, a week, a month, half a year, a year,
    #: and everything. Enough to cover the everyday moves without a horizontal
    #: row that clips off the panel edge.
    COMPACT_RANGES = ("1d", "5d", "1mo", "6mo", "1y", "max")
    COMPACT_INTERVALS = ("1m", "1h", "1d", "1wk")
    #: Hidden below WIDE so a regular-width range row stays inside the panel
    #: edge; 1y and 5y already bracket it.
    WIDE_ONLY_RANGES = ("2y",)

    def on_size_class(self, size_class: str) -> None:
        """Reorganize the control rows for the width the panel now has.

        At 320px the full rows clipped: "RANGE" rendered as "RAN(", "custom…"
        as "to", and the add-indicator button was sliced in half — controls
        that are present but unreachable. Compact drops the eyebrows (the chips
        already say what they are) and the less-used presets, keeping the
        common set plus whatever is currently selected, so the row always fits.
        """
        compact = size_class == COMPACT
        wide = size_class == WIDE
        self._range_eyebrow.setVisible(not compact)
        self._interval_eyebrow.setVisible(not compact)
        self._indicators_eyebrow.setVisible(not compact)
        self._custom_btn.setVisible(not compact)

        for label, btn in self._range_buttons.items():
            if compact:
                keep = label in self.COMPACT_RANGES
            else:
                keep = wide or label not in self.WIDE_ONLY_RANGES
            # never hide what is currently selected
            btn.setVisible(keep or btn.isChecked())
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
        # the lane's gutter carries only its EVENTS caption: with nothing
        # plotted it would label an empty strip, so it goes with the axes
        # (its width stays, keeping the lane in the price plot's time column)
        self._lane.pane.getAxis("left").setStyle(showValues=not empty)
        if empty:
            if self.current_symbol:
                self._empty.set_text(
                    f"No price history for {self.current_symbol}",
                    "Try a different range or interval, or press F5 to refresh",
                )
            else:
                self._empty.set_text(
                    EMPTY_NO_SYMBOL,
                    "Click a ticker in any linked panel, or type one in the search box",
                )
            self._empty.setGeometry(self.plot_widget.viewport().rect())
            self._empty.set_shown(True)
        else:
            self._empty.set_shown(False)

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
        """A control-row label ("RANGE", "INTERVAL", "INDICATORS").

        Defers to ``QLabel#panelEyebrow`` in theme.py. This used to restate the
        same element inline at a *different* weight and tracking — 600/1px here
        against the theme's 700/1.5px — so the two eyebrows in the app looked
        subtly unlike each other for no reason anyone had chosen.
        """
        lbl = QLabel(text, self)
        lbl.setObjectName("panelEyebrow")
        return lbl

    # -- range / interval selection ------------------------------------------

    def _range_span_days(self) -> float:
        span = range_span_days(self._range)
        if span is not None:
            return span
        s = date.fromisoformat(self._range["start"])
        e = date.fromisoformat(self._range["end"])
        return max((e - s).days, 1)

    def _range_back_days(self) -> float:
        """Calendar days from the earliest requested bar to today — what the
        yfinance intraday caps actually constrain."""
        span = range_span_days(self._range)
        if span is not None:
            return span
        s = date.fromisoformat(self._range["start"])
        return max((date.today() - s).days, 1)

    def _combo_problem(self, rng: dict, interval: str) -> Optional[str]:
        """Reason the (range, interval) pair is invalid, or None if fine.
        Encodes yfinance's real limits (see INTERVAL_MAX_BACK_DAYS)."""
        span = back = range_span_days(rng)
        if span is None:
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
            # Never disabled: picking a range the interval can't serve moves
            # the interval instead (see _set_range_preset). Greying ranges out
            # made "1d" look unavailable on the default daily interval.
            fit = self._interval_for_range({"preset": label})
            btn.setToolTip(
                "" if fit == self._interval else f"switches interval to {fit}"
            )
            btn.setChecked(label == active_preset)
        self._custom_btn.setChecked(active_preset is None)
        if "lookback" in self._range:
            self._custom_btn.setToolTip(lookback_label(self._range["lookback"]))
        elif active_preset is None:
            self._custom_btn.setToolTip(
                f"{self._range['start']} → {self._range['end']}"
            )
        else:
            self._custom_btn.setToolTip("Pick a lookback or explicit start/end dates")
        for label, btn in self._interval_buttons.items():
            problem = self._combo_problem(self._range, label)
            btn.setEnabled(problem is None)
            btn.setToolTip(problem or "")
            btn.setChecked(label == self._interval)
        # Chip visibility depends on the checked chip (a selected 2y stays
        # shown), and the base class never fires on_size_class for the size
        # class a panel starts in — so apply it here, on every change.
        self.on_size_class(self.size_class)

    def _set_range_preset(self, preset: str) -> None:
        if self._range.get("preset") == preset:
            self._update_range_interval_buttons()
            return
        self._range = {"preset": preset}
        fit = self._interval_for_range(self._range)
        if fit != self._interval:
            self.set_status(f"{preset} range — using {fit} bars")
            self._interval = fit
        self._range_or_interval_changed()

    def _interval_for_range(self, rng: dict) -> str:
        """The interval to use with ``rng``: the current one if it's valid,
        else the range's preferred interval, else the finest valid one."""
        if not self._combo_problem(rng, self._interval):
            return self._interval
        preferred = preferred_interval(rng)
        if preferred and not self._combo_problem(rng, preferred):
            return preferred
        for cand in INTERVALS:
            if not self._combo_problem(rng, cand):
                return cand
        return self._interval

    def _pick_custom_range(self) -> None:
        if "start" in self._range:
            start = date.fromisoformat(self._range["start"])
            end = date.fromisoformat(self._range["end"])
        else:
            end = date.today()
            start = end - timedelta(days=180)
        dlg = _RangeDialog(self, start, end, self._range.get("lookback"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            self._update_range_interval_buttons()
            return
        self._set_custom_range(dlg.chosen_range())

    def _set_custom_range(self, rng: dict) -> None:
        if "lookback" in rng:
            # a lookback behaves like a preset: it has its own default interval
            self._range = rng
            fit = self._interval_for_range(rng)
            if fit != self._interval:
                self.set_status(f"{lookback_label(rng['lookback'])} — using {fit} bars")
                self._interval = fit
            self._range_or_interval_changed()
            return
        self._range = rng
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
        self._morph.cancel()  # a new window on the same symbol never morphs
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
        # the user's own add — a restored layout's indicators are not events
        self._draw_in(self._indicators[-1])

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
        chip.setObjectName("indicatorChip")
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
        """The chip is the indicator's legend: a short line in the series
        colour before the label, the way a chart legend shows it.

        Two earlier versions put the colour on the chip itself — as label ink
        (olive and blue text was near illegible at chip size) and then as a
        coloured frame with a thick left edge, which read as an uneven border
        and made these chips taller than the range and interval chips above.
        Now they share those chips' metrics and neutral edge (QSS role
        ``indicatorChip``), and only the swatch carries the colour; an off
        indicator mutes both swatch and label."""
        if inst.chip is None:
            return
        inst.chip.setIcon(_chip_swatch(inst.color if inst.on else FG_MUTED, inst.chip))
        inst.chip.setIconSize(QSize(*_SWATCH_SIZE))

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
        rm_act.triggered.connect(lambda: self._remove_indicator(inst, animate=True))
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

    def _remove_indicator(self, inst: _IndicatorInstance, animate: bool = False) -> None:
        """``animate`` only for the user removing one from its chip menu: a
        restore or a reset to defaults swaps indicators in bulk, which is not
        an event to watch."""
        self._land_reveal(inst)
        # overlays on the price fade out on their own; the chip and any pane
        # go now (they hold layout space, and layout never animates)
        if animate and self._fade_out_items(inst.items):
            inst.items = []
        self._teardown_indicator_items(inst)
        if inst.chip is not None:
            self._chips_row.removeWidget(inst.chip)
            # hidden now, deleted later: until the deferred delete runs, a chip
            # out of its layout still paints at its last position — over the
            # readout, when a restored layout replaced the default indicators
            inst.chip.hide()
            inst.chip.deleteLater()
        self._indicators.remove(inst)

    # -- indicator motion ------------------------------------------------------

    def _draw_in(self, inst: _IndicatorInstance) -> None:
        """Reveal a just-added indicator left to right as it fades up.

        Curves are re-sliced frame by frame from their own full data; bars
        (volume, the MACD histogram) rise from their baseline, their pane's
        scale held at the final one so the growth is not autoranged away. Any refresh of the indicator mid-reveal lands it
        (``_refresh_indicator``), and so does the end of the reveal, so the
        last word on its data is always the real computation.
        """
        ms = motion.duration(motion.INDICATOR_IN_MS)
        items = inst.items + inst.pane_items
        if ms == 0 or not inst.on or not items:
            return
        curves, bars = [], []
        for item in items:
            if isinstance(item, pg.PlotDataItem):
                xs, ys = item.xData, item.yData
                if xs is not None and len(xs) > 1:
                    curves.append((item, xs, ys))
            elif isinstance(item, pg.BarGraphItem):
                heights = item.opts.get("height")
                if heights is not None and len(heights):
                    bars.append((item, np.asarray(heights, dtype=float)))
        if bars and inst.pane is not None:
            vb = inst.pane.getViewBox()
            vb.updateAutoRange()
            vb.disableAutoRange(axis=pg.ViewBox.YAxis)
        inst._reveal = (items, curves, bars)
        if inst.anim is None:
            inst.anim = motion.PacedAnimation(self)
            inst.anim.setStartValue(0.0)
            inst.anim.setEndValue(1.0)
            inst.anim.setEasingCurve(motion.EASE_TRAVEL)
            inst.anim.valueChanged.connect(lambda v, i=inst: self._reveal_step(i, v))
            inst.anim.finished.connect(lambda i=inst: self._refresh_indicator(i))
        inst.anim.stop()
        inst.anim.setDuration(ms)
        self._reveal_step(inst, 0.0)
        inst.anim.start()

    def _reveal_step(self, inst: _IndicatorInstance, value) -> None:
        t = float(value)
        items, curves, bars = inst._reveal
        try:
            for item in items:
                item.setOpacity(t)
            for item, xs, ys in curves:
                k = max(2, int(round(len(xs) * t)))
                item.setData(xs[:k], ys[:k])
            for item, heights in bars:
                item.setOpts(height=heights * t)
        except RuntimeError:
            pass  # torn down mid-reveal

    def _land_reveal(self, inst: _IndicatorInstance) -> None:
        """Stop a running reveal and put the indicator at full opacity. Its
        data is restored by whoever called (``_refresh_indicator``)."""
        if inst.anim is not None and inst.anim.state() != QVariantAnimation.State.Stopped:
            inst.anim.stop()
        if inst._reveal[2] and inst.pane is not None:
            try:
                inst.pane.getViewBox().enableAutoRange(axis=pg.ViewBox.YAxis)
            except RuntimeError:
                pass
        inst._reveal = ([], [], [])
        for item in inst.items + inst.pane_items:
            try:
                item.setOpacity(1.0)
            except RuntimeError:
                pass

    def _fade_out_items(self, items: list) -> bool:
        """Fade price-plot items out, then remove them. True when they were
        taken over (the caller must not tear them down itself)."""
        ms = motion.duration(motion.INDICATOR_OUT_MS)
        if ms == 0 or not items or not any(item.isVisible() for item in items):
            return False
        starts = [item.opacity() for item in items]
        anim = QVariantAnimation(self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(motion.EASE_OUT)
        anim.setDuration(ms)

        def step(value) -> None:
            try:
                for item, start in zip(items, starts):
                    item.setOpacity(start * float(value))
            except RuntimeError:
                pass

        def done() -> None:
            for item in items:
                try:
                    self.plot_widget.removeItem(item)
                    if hasattr(item, "deleteLater"):
                        item.deleteLater()
                except RuntimeError:
                    pass
            if anim in self._fading_out:
                self._fading_out.remove(anim)
            anim.deleteLater()

        anim.valueChanged.connect(step)
        anim.finished.connect(done)
        self._fading_out.append(anim)  # held until done, then released
        anim.start()
        return True

    def _indicator_anims_for_tests(self) -> list:
        stopped = QVariantAnimation.State.Stopped
        anims = [i.anim for i in self._indicators if i.anim is not None and i.anim.state() != stopped]
        return anims + [a for a in self._fading_out if a.state() != stopped]

    def _maybe_widen_fetch(self) -> None:
        """A new/longer indicator may need more lookback than what's fetched."""
        if self.current_symbol:
            self._resubscribe(self.current_symbol)

    # -- indicator plumbing: build pg items / panes, compute + set data -------

    def _make_pane(self, compact_y: bool = False) -> pg.PlotWidget:
        axes: dict = {
            "bottom": _BarTimeAxis(self._clock),
            "left": (_CompactAxis if compact_y else _SpacedAxis)(orientation="left"),
        }
        pane = pg.PlotWidget(axisItems=axes)
        pane.getAxis("left").setWidth(LEFT_AXIS_PX)  # one time column: see LEFT_AXIS_PX
        pane.setBackground(self._colors["bg"])
        pane.showGrid(x=self._grid_on, y=self._grid_on, alpha=0.15)
        pane.getAxis("left").setTextPen(FG_DIM)
        pane.getAxis("bottom").setTextPen(FG_DIM)
        pane.setXLink(self.plot_widget)
        # Same rule as the price plot: the mouse moves time, and Y fits what
        # is visible (a fixed-scale pane like RSI's 0–100 overrides this).
        pane.setMouseEnabled(x=True, y=False)
        pane.hideButtons()
        # indicator panes share the chart's own menu (type, colors, drawing…)
        pane.setMenuEnabled(False)
        pane.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        pane.customContextMenuRequested.connect(
            lambda pos, w=pane: self._show_chart_menu(
                self.plot_widget.mapFromGlobal(w.mapToGlobal(pos))
            )
        )
        pane.getViewBox().setAutoVisible(y=True)
        pane.getViewBox().state["wheelScaleFactor"] = WHEEL_SCALE_FACTOR
        pane.scene().sigMouseClicked.connect(self._on_pane_clicked)
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
            inst.pane.hide()  # see _remove_indicator: gone from view at once
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
        self._land_reveal(inst)  # a draw-in never outlives real data
        self._apply_indicator_visibility(inst)
        t, c = self._hist_x, self._hist_c
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
        # capture the bar before the menu grabs the mouse and the crosshair
        # stops tracking, so the entry can name the date the user pointed at
        self._menu_bar_date = self._date_under_cursor()
        menu = self._build_chart_menu()
        self.add_guide_action(menu)
        menu.exec(self.plot_widget.mapToGlobal(pos))

    def _build_chart_menu(self) -> QMenu:
        menu = QMenu(self.plot_widget)

        # -- day brief: first, and labelled with the real date. An action that
        # opens a panel should never leave the user guessing which day it will
        # act on, so the date is in the menu item itself.
        iso = self._menu_bar_date
        if iso:
            try:
                pretty = date.fromisoformat(iso).strftime("%a %d %b %Y")
            except ValueError:
                pretty = iso
            day_act = QAction(f"What happened on {pretty}?", menu)
            day_act.triggered.connect(lambda _=False, d=iso: self._pick_date(d))
            menu.addAction(day_act)
            menu.addSeparator()

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
        lane_act = QAction("Events lane", menu, checkable=True)
        lane_act.setChecked(self._lane_on)
        lane_act.setToolTip("Marks on days with earnings, dividends, rating changes or an unusual move")
        lane_act.triggered.connect(self._toggle_lane)
        menu.addAction(lane_act)
        grid_act = QAction("Grid", menu, checkable=True)
        grid_act.setChecked(self._grid_on)
        grid_act.triggered.connect(self._toggle_grid)
        menu.addAction(grid_act)
        log_act = QAction("Log scale (Y)", menu, checkable=True)
        log_act.setChecked(self._log_on)
        log_act.triggered.connect(self._toggle_log)
        menu.addAction(log_act)

        menu.addSeparator()
        menu.addMenu(self._build_compare_menu(menu))
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
        for act in self._build_export_menu(menu).actions():
            menu.addAction(act)
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
            self.line_curve.setData(self._hist_x, self._hist_c)
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
        self._morph.finish()  # its pixels were mapped on the old scale
        self._log_on = checked
        vb = self.plot_widget.getViewBox()
        window = vb.viewRange()[0]
        self.plot_widget.setLogMode(y=checked)
        # setLogMode re-enables auto-range on both axes: the chart jumped back
        # to every loaded bar, and y fitted the candles only (a comparison,
        # added with ignoreBounds, ran off the bottom). The chart frames
        # itself, so put its window back and fit y the way it always does.
        vb.disableAutoRange()
        vb.setXRange(*window, padding=0)
        self._render_annotations()  # channel fills are mapped when built
        self._render_compares()  # re-anchored to the window's first bar
        self._fit_y_to_visible()

    # -- extras ----------------------------------------------------------------

    def _build_export_menu(self, parent) -> QMenu:
        """Save or copy, each in either theme — the image need not match the
        app (a light chart for a document, a dark one for a deck)."""
        menu = QMenu("Export", parent)
        for title, handler, ellipsis in (
            ("Save as PNG", self._export_png, "…"),
            ("Copy chart image", self._copy_chart_image, ""),
        ):
            sub = menu.addMenu(title)
            for name in THEMES:
                act = QAction(f"{name.capitalize()}{ellipsis}", sub)
                act.triggered.connect(lambda _=False, h=handler, n=name: h(n))
                sub.addAction(act)
        return menu

    def chart_image(self, theme_name: str | None = None):
        """The chart as it stands, at twice its on-screen pixels, with a
        header and legend, in ``theme_name`` (default: the app's); None when
        nothing is plotted. See _chart_export."""
        from . import _chart_export

        return _chart_export.render(self, theme_name=theme_name)

    def _export_png(self, theme_name: str | None = None) -> None:
        image = self.chart_image(theme_name)
        if image is None:
            self.set_status("nothing to export — no bars plotted")
            return
        sym = self.current_symbol or "chart"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export chart as PNG", f"{sym}_chart.png", "PNG image (*.png)"
        )
        if not path:
            return
        if image.save(path, "PNG"):
            self.set_status(f"saved {path}")
        else:
            self.set_status("⚠ PNG export failed")

    def _copy_chart_image(self, theme_name: str | None = None) -> None:
        image = self.chart_image(theme_name)
        if image is None:
            self.set_status("nothing to copy — no bars plotted")
            return
        QApplication.clipboard().setImage(image)
        self.set_status("chart image copied")

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
        self._cross_index = None
        for item in (self._cross_v, self._cross_h, self._cross_text):
            item.setVisible(False)

    @property
    def _hist_x(self) -> list[int]:
        """Plot x of each loaded bar: its slot, so closed hours take no room."""
        return list(range(len(self._hist_t)))

    def _x_of_time(self, ts: float) -> float:
        """Epoch seconds -> plot x. Exact on a bar, linear between two bars,
        and extended at the bar spacing past either end (a drawing's free
        point can sit beyond the data)."""
        t = self._hist_t
        n = len(t)
        if not n:
            return float(ts)
        bar = self._bar_seconds()
        if ts <= t[0]:
            return (ts - t[0]) / bar
        if ts >= t[-1]:
            return (n - 1) + (ts - t[-1]) / bar
        j = bisect.bisect_right(t, ts)
        a, b = t[j - 1], t[j]
        return (j - 1) + ((ts - a) / (b - a) if b > a else 0.0)

    def _time_of_x(self, x: float) -> float:
        """Plot x -> epoch seconds; the inverse of :meth:`_x_of_time`."""
        t = self._hist_t
        n = len(t)
        if not n:
            return float(x)
        bar = self._bar_seconds()
        if x <= 0:
            return t[0] + x * bar
        if x >= n - 1:
            return t[-1] + (x - (n - 1)) * bar
        i = int(math.floor(x))
        return t[i] + (x - i) * (t[i + 1] - t[i])

    def _nearest_index(self, x: float) -> Optional[int]:
        n = len(self._hist_t)
        if not n:
            return None
        return min(max(int(round(x)), 0), n - 1)

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
        self._cross_index = i  # the `D` shortcut reads the bar from here
        self._cross_v.setPos(i)
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
        when = bar_datetime(self._hist_t[i])
        fmt = "%Y-%m-%d" if self._interval in ("1d", "1wk", "1mo") else "%Y-%m-%d %H:%M"
        col = self._colors["up"] if c >= o else self._colors["down"]
        vol = f"  V {_fmt_compact_num(v)}" if v else ""
        cmp_lines = ""
        for cmp in self._compares:
            if not cmp.on or i >= len(cmp.aligned) or cmp.aligned[i] is None:
                continue
            pct = self._compare_pct(cmp, i)
            pct_txt = f" ({pct:+.2f}%)" if pct is not None else ""
            cmp_lines += (f'<br><span style="color:{cmp.color};">{cmp.symbol}</span> '
                          f'{cmp.aligned[i]:,.2f}{pct_txt}')
        self._cross_text.setHtml(
            f'<div style="font-family:monospace;font-size:11px;color:{FG};">'
            f'{when.strftime(fmt)}<br>'
            f'O {o:,.2f}&nbsp; H {h:,.2f}&nbsp; L {l:,.2f}&nbsp; '
            f'<span style="color:{col};">C {c:,.2f}</span>{vol}{cmp_lines}</div>'
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
            return 0.0, float(len(self._hist_t) - 1)
        (xlo, xhi), _ = self.plot_widget.getViewBox().viewRange()
        return xlo, xhi

    def _snap_point(self, mp) -> tuple:
        """Snap a view-coordinate point to the nearest bar's time and to that
        bar's nearest O/H/L/C price, so drawings anchor to candles."""
        i = self._nearest_index(mp.x())
        price = self._price_of_view_y(mp.y())
        if i is None:
            return (mp.x(), price)
        ohlc = (self._hist_o[i], self._hist_hi[i], self._hist_lo[i], self._hist_c[i])
        y = min(ohlc, key=lambda v: abs(v - price))
        return (self._hist_t[i], y)

    def _price_of_view_y(self, y: float) -> float:
        """A plot y as a price: on a log axis the view is in log10 units."""
        return 10.0 ** y if self._log_on else y

    def _place_point(self, mp, index: int) -> tuple:
        """The coordinate for the point being placed. Most points snap to a
        candle; the channel width/height controls move freely so the user can
        size a channel to any height."""
        free = index in DRAW_TOOL_FREE_POINTS.get(self._draw_tool, ())
        # stored as (time, price), so a drawing survives a reload that brings
        # back a different number of bars
        if free:
            return (self._time_of_x(mp.x()), self._price_of_view_y(mp.y()))
        return self._snap_point(mp)

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
            self._on_pane_clicked(ev)  # double-click resets the view
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
        if self._log_on:
            # PlotCurveItem has no log mode (PlotDataItem does): map it here
            ys_a = [math.log10(y) if y > 0 else np.nan for y in ys_a]
            ys_b = [math.log10(y) if y > 0 else np.nan for y in ys_b]
        curve_a = pg.PlotCurveItem(xs_a, ys_a)
        curve_b = pg.PlotCurveItem(xs_b, ys_b)
        brush_color = QColor(self._drawing_color)
        brush_color.setAlpha(28 if preview else 45)
        fb = pg.FillBetweenItem(curve_a, curve_b, brush=pg.mkBrush(brush_color))
        fb.setZValue(4)  # above candles, below the price line/indicators
        self.plot_widget.addItem(fb, ignoreBounds=True)
        return fb

    def _add_geometry_items(self, atype, pts, *, preview: bool) -> None:
        if not self._hist_t:
            return  # points are times: nothing to place them against yet
        x_lo, x_hi = self._data_x_range()
        pts = [(self._x_of_time(p[0]), p[1]) for p in pts]
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
        # first, while the old series is still loaded: the morph's snapshot
        self._morph.symbol_changing()
        if symbol != self._annotations_symbol:
            self._clear_drawings()
            self._annotations_symbol = symbol
        self.set_loading(True)
        self._hide_crosshair()
        self._last_quote = {}
        self._hist_t, self._hist_o, self._hist_c = [], [], []
        self._hist_hi, self._hist_lo, self._hist_v = [], [], []
        self._refresh_all_indicators()
        self._apply_chart_type()
        self._sector_wanted = False
        self._realign_compares()
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
        if "lookback" in self._range:
            days = lookback_days(self._range["lookback"]) or 1
            if days <= 1:
                return SUB_DAY_FETCH_PERIOD, interval
            # N days back from today, plus a weekend's slack so the window
            # ending at the latest bar is full on a Monday morning
            start = date.today() - timedelta(days=math.ceil(days) + 4)
            end = date.today()
        else:
            start = date.fromisoformat(self._range["start"])
            end = date.fromisoformat(self._range["end"])
        # custom range: widen the fetched start so indicators warm up before
        # the visible window, clamped to what the interval can legally reach
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
        for cmp in self._compares:
            self.subscribe(
                f"history:{cmp.symbol}:{period_token}:{interval}",
                lambda data, c=cmp: self._on_compare_history(c, data),
            )
        if self._sector_wanted:
            self.subscribe(f"profile:{symbol}", self._on_profile_for_sector)
        if self._lane_on:
            # All three already exist with long TTLs, so these are usually
            # cache hits rather than new network work.
            self._lane_events = []
            self.subscribe(f"earnings:{symbol}", self._on_lane_earnings)
            self.subscribe(f"dividends:{symbol}", self._on_lane_cash)
            self.subscribe(f"analyst:{symbol}", self._on_lane_ratings)

    # -- events lane ---------------------------------------------------------

    def _merge_lane_events(self, kind: str, rows: list[dict]) -> None:
        """Replace this source's contribution and redraw. Each source arrives
        on its own topic and at its own time, so they are kept separable
        rather than appended blindly."""
        self._lane_events = [
            e for e in self._lane_events if e.get("_src") != kind
        ] + [dict(r, _src=kind) for r in rows]
        self._render_lane()

    def _on_lane_earnings(self, data: Any) -> None:
        rows = []
        if isinstance(data, dict):
            for row in data.get("rows") or []:
                if isinstance(row, (list, tuple)) and row and row[0]:
                    rows.append({"date": str(row[0])[:10], "kind": "earnings"})
        self._merge_lane_events("earnings", rows)

    def _on_lane_cash(self, data: Any) -> None:
        rows = []
        if isinstance(data, dict):
            for entry in data.get("history") or []:
                if isinstance(entry, (list, tuple)) and entry and entry[0]:
                    rows.append({"date": str(entry[0])[:10], "kind": "dividend"})
            for entry in data.get("splits") or []:
                if isinstance(entry, (list, tuple)) and entry and entry[0]:
                    rows.append({"date": str(entry[0])[:10], "kind": "split"})
        self._merge_lane_events("cash", rows)

    def _on_lane_ratings(self, data: Any) -> None:
        rows = []
        if isinstance(data, dict):
            for up in data.get("upgrades") or []:
                if isinstance(up, dict) and up.get("date"):
                    rows.append(
                        {
                            "date": str(up["date"])[:10],
                            "kind": "rating",
                            "action": up.get("action", ""),
                            "detail": f"{up.get('from_grade','')} → {up.get('to_grade','')}",
                        }
                    )
        self._merge_lane_events("ratings", rows)

    def _render_lane(self) -> None:
        if self._lane is None:
            return
        if not self._lane_on:
            self._lane.clear()
            return
        self._lane.set_marks(
            build_marks(self._hist_t, self._hist_c, self._lane_events)
        )

    def _toggle_lane(self, on: bool) -> None:
        self._lane_on = bool(on)
        if self._lane is not None:
            self._lane.pane.setVisible(self._lane_on)
        if self._lane_on and self.current_symbol:
            self._resubscribe(self.current_symbol)
        else:
            self._render_lane()

    def _pick_date(self, iso: str) -> None:
        """Open a Day Brief on ``iso`` and point this panel's link group at it.

        Routed through ``MainWindow.open_day_brief`` rather than publishing to
        DateContext directly: publishing alone leaves the click with no visible
        effect whenever no Day Brief happens to be docked, which is the default
        state of every workspace saved before that panel existed.
        """
        if not iso:
            return
        group = self.link_group
        win = self.window()
        opener = getattr(win, "open_day_brief", None)
        if callable(opener):
            opener(iso, group=group if group != UNLINKED else DEFAULT_GROUP, source=self)
        elif group != UNLINKED:  # detached panel (tests, previews)
            DateContext.instance().set_date(group, iso, source=self)
        try:
            pretty = date.fromisoformat(iso).strftime("%a %d %b %Y")
        except ValueError:
            pretty = iso
        notify = getattr(win, "notify", None)
        if callable(notify):
            notify(f"Day Brief · {pretty}", 2500)
        else:
            self.set_status(pretty)

    def _date_under_cursor(self) -> str:
        """ISO date of the bar the crosshair is currently on, or ''.

        Derived from the bar itself, exactly as the crosshair readout does
        (chart.py's _update_readout) -- never from a separate local-midnight
        conversion, which would drift by a day near the boundary."""
        idx = self._cross_index
        if idx is None or not (0 <= idx < len(self._hist_t)):
            return ""
        try:
            return bar_datetime(self._hist_t[idx]).date().isoformat()
        except (ValueError, OSError, OverflowError):
            return ""

    def _install_day_shortcut(self) -> None:
        """`D` explains the day under the crosshair.

        Scoped to this widget and its children (not the window) so it only
        fires when the chart has focus, and so it never steals a keystroke
        from the command bar or any other panel's text entry."""
        sc = QShortcut(QKeySequence("D"), self)
        sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        sc.activated.connect(self._explain_cursor_day)
        self._day_shortcut = sc

    def _explain_cursor_day(self) -> None:
        """The `D` shortcut. Nothing under the crosshair means nothing
        happens, rather than a guess at which day was meant."""
        iso = self._date_under_cursor()
        if iso:
            self._pick_date(iso)
        else:
            self.set_status("hover a bar, then press D")

    # -- compare (overlay another symbol) --------------------------------------

    def _build_compare_menu(self, parent) -> QMenu:
        from ..sector_meta import benchmark_for, sector_proxy_for

        menu = QMenu("Compare", parent)
        menu.setToolTipsVisible(True)
        sym = self.current_symbol
        full = len(self._compares) >= MAX_COMPARES

        sector = self._known_sector()
        etf = sector_proxy_for(sym, sector) if sym and sector else None
        if etf:
            text = f"Sector ETF · {etf} ({sector})"
        elif sym and not sector:
            text = "Sector ETF"
        else:
            text = "Sector ETF (none for this listing)"
        act = QAction(text, menu)
        act.setToolTip("The SPDR sector fund for this stock's sector (US listings)")
        act.setEnabled(bool(sym) and not full and (etf is not None or not sector))
        act.triggered.connect(self._compare_with_sector)
        menu.addAction(act)

        bench = benchmark_for(sym) if sym else None
        act = QAction(f"Index · {bench}" if bench else "Index", menu)
        act.setToolTip("The broad index this listing trades against")
        act.setEnabled(bench is not None and not full)
        act.triggered.connect(lambda _=False, b=bench: self._add_compare(b))
        menu.addAction(act)

        act = QAction("Other symbol…", menu)
        act.setEnabled(not full)
        act.triggered.connect(self._compare_with_other)
        menu.addAction(act)

        if self._compares:
            menu.addSeparator()
            clear = QAction("Clear comparisons", menu)
            clear.triggered.connect(self._clear_compares)
            menu.addAction(clear)
        if full:
            menu.addSeparator()
            note = QAction(f"Up to {MAX_COMPARES} at a time", menu)
            note.setEnabled(False)
            menu.addAction(note)
        return menu

    def _known_sector(self) -> Optional[str]:
        sym = self.current_symbol
        if not sym:
            return None
        try:
            from ..datahub import DataHub

            prof = DataHub.instance().peek(f"profile:{sym}")
        except Exception:
            return None
        if isinstance(prof, dict) and prof.get("sector"):
            return str(prof["sector"])
        return None

    def _compare_with_sector(self) -> None:
        from ..sector_meta import sector_proxy_for

        sym = self.current_symbol
        if not sym:
            return
        sector = self._known_sector()
        if sector:
            etf = sector_proxy_for(sym, sector)
            if etf:
                self._add_compare(etf)
            else:
                self.set_status(f"no sector ETF for {sym}")
            return
        # not fetched yet: ask for the profile and finish when it lands
        self._sector_wanted = True
        self.set_status(f"looking up {sym}'s sector…")
        self.subscribe(f"profile:{sym}", self._on_profile_for_sector)

    def _on_profile_for_sector(self, data: Any) -> None:
        if not self._sector_wanted or not isinstance(data, dict):
            return
        from ..sector_meta import sector_proxy_for

        self._sector_wanted = False
        etf = sector_proxy_for(self.current_symbol, data.get("sector"))
        if etf:
            self._add_compare(etf)
        else:
            self.set_status(f"no sector ETF for {self.current_symbol}")

    def _compare_with_other(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Compare", "Symbol to compare with (e.g. SPY, MSFT, ^IXIC):"
        )
        if ok and text.strip():
            self._add_compare(text.strip().upper())

    def _add_compare(
        self, symbol: Optional[str], color: Optional[str] = None,
        on: bool = True, subscribe: bool = True,
    ) -> Optional[_Compare]:
        if not symbol:
            return None
        symbol = symbol.strip().upper()
        if any(c.symbol == symbol for c in self._compares):
            self.set_status(f"already comparing with {symbol}")
            return None
        if len(self._compares) >= MAX_COMPARES:
            self.set_status(f"up to {MAX_COMPARES} comparisons at a time")
            return None
        if symbol == (self.current_symbol or "").upper():
            self.set_status("that is the symbol on the chart")
            return None
        cmp = _Compare(symbol, color or self._least_used_color(), on)
        cmp.curve = pg.PlotDataItem(
            pen=self._compare_pen(cmp.color), antialias=True, connect="finite"
        )
        cmp.curve.setZValue(6)
        self.plot_widget.addItem(cmp.curve)
        chip = QPushButton("", self)
        chip.setObjectName("indicatorChip")
        chip.setCheckable(True)
        chip.setChecked(on)
        chip.toggled.connect(lambda checked, c=cmp: self._on_compare_toggled(c, checked))
        chip.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        chip.customContextMenuRequested.connect(
            lambda pos, c=cmp: self._show_compare_chip_menu(c, pos)
        )
        self._chips_row.insertWidget(self._chips_row.indexOf(self._add_btn), chip)
        cmp.chip = chip
        self._compares.append(cmp)
        self._style_compare_chip(cmp)
        if subscribe and self.current_symbol:
            period_token, interval = self._fetch_spec()
            self.subscribe(
                f"history:{symbol}:{period_token}:{interval}",
                lambda data, c=cmp: self._on_compare_history(c, data),
            )
        return cmp

    @staticmethod
    def _compare_pen(color: str):
        """Dashed: the series palette has four colours and a chart can carry
        three indicators and four comparisons, so colour alone can't keep a
        comparison apart from an SMA. The dash says "another symbol"."""
        return pg.mkPen(color, width=1.5, style=Qt.PenStyle.DashLine)

    def _least_used_color(self) -> str:
        """The palette colour fewest lines on this chart already wear."""
        palette = indicator_palette()
        used = [i.color.lower() for i in self._indicators if i.on]
        used += [c.color.lower() for c in self._compares]
        return min(palette, key=lambda col: (used.count(col.lower()), palette.index(col)))

    def _on_compare_history(self, cmp: _Compare, data: Any) -> None:
        if cmp not in self._compares or not isinstance(data, dict):
            return
        t, c = data.get("t") or [], data.get("c") or []
        pairs = [(float(a), float(b)) for a, b in zip(t, c) if a is not None and b is not None]
        cmp.t = [a for a, _ in pairs]
        cmp.c = [b for _, b in pairs]
        self._realign_compares()
        if not cmp.t:
            self.set_status(f"no data for {cmp.symbol}")

    def _realign_compares(self) -> None:
        bar = self._bar_seconds()
        for cmp in self._compares:
            cmp.aligned = align_closes(self._hist_t, cmp.t, cmp.c, bar)
        self._render_compares()
        self._fit_y_to_visible()

    def _anchor_index(self) -> Optional[int]:
        """The first chart bar in view: where every comparison starts level."""
        n = len(self._hist_t)
        if not n:
            return None
        (x0, _x1), _ = self.plot_widget.getViewBox().viewRange()
        return min(max(0, math.ceil(x0)), n - 1)

    def _compare_base(self, cmp: _Compare) -> Optional[tuple[int, float]]:
        """(bar, its close) the overlay is rebased on: the anchor bar, or the
        first bar after it where the compared symbol has a close."""
        a = self._anchor_index()
        if a is None:
            return None
        for i in range(a, len(cmp.aligned)):
            if cmp.aligned[i]:
                return i, cmp.aligned[i]
        return None

    def _compare_values(self, cmp: _Compare) -> list[Optional[float]]:
        """The overlay's y per chart bar, in the chart's price units."""
        base = self._compare_base(cmp) if cmp.on else None
        if base is None:
            return []
        i, base_c = base
        price = self._hist_c[i]
        return [v * price / base_c if v is not None else None for v in cmp.aligned]

    def _compare_pct(self, cmp: _Compare, i: int) -> Optional[float]:
        base = self._compare_base(cmp)
        if base is None or i >= len(cmp.aligned) or cmp.aligned[i] is None:
            return None
        return (cmp.aligned[i] / base[1] - 1.0) * 100.0

    def _visible_last_index(self) -> Optional[int]:
        n = len(self._hist_t)
        if not n:
            return None
        (_x0, x1), _ = self.plot_widget.getViewBox().viewRange()
        return min(max(0, math.floor(x1)), n - 1)

    def _render_compares(self) -> None:
        last = self._visible_last_index()
        for cmp in self._compares:
            if cmp.curve is None:
                continue
            ys = self._compare_values(cmp)
            if not ys:
                cmp.curve.setData([])
            else:
                cmp.curve.setData(
                    self._hist_x, [np.nan if y is None else y for y in ys]
                )
            cmp.curve.setVisible(cmp.on)
            self._style_compare_chip(cmp, last)

    def _style_compare_chip(self, cmp: _Compare, last: Optional[int] = None) -> None:
        """The chip is the overlay's legend and its scoreboard: how far it
        moved across the bars in view, beside how far the chart's symbol did."""
        if cmp.chip is None:
            return
        cmp.chip.setIcon(_chip_swatch(cmp.color if cmp.on else FG_MUTED, cmp.chip, dashed=True))
        cmp.chip.setIconSize(QSize(*_SWATCH_SIZE))
        if last is None:
            last = self._visible_last_index()
        pct = self._compare_pct(cmp, last) if (last is not None and cmp.on) else None
        cmp.chip.setText(f"vs {cmp.symbol}" + (f" {pct:+.1f}%" if pct is not None else ""))
        tip = "Click: show / hide · right-click: color / remove"
        base = self._compare_base(cmp) if cmp.on else None
        if pct is not None and base is not None and self._hist_c and last is not None:
            own = (self._hist_c[last] / self._hist_c[base[0]] - 1.0) * 100.0
            tip = (f"Across the bars in view: {self.current_symbol} {own:+.2f}%, "
                   f"{cmp.symbol} {pct:+.2f}% ({own - pct:+.2f} pts)\n{tip}")
        cmp.chip.setToolTip(tip)

    def _on_compare_toggled(self, cmp: _Compare, checked: bool) -> None:
        cmp.on = checked
        self._render_compares()
        self._fit_y_to_visible()

    def _show_compare_chip_menu(self, cmp: _Compare, pos) -> None:
        menu = QMenu(self)
        color_act = QAction("Color…", menu)
        color_act.triggered.connect(lambda: self._recolor_compare(cmp))
        menu.addAction(color_act)
        menu.addSeparator()
        rm_act = QAction("Remove", menu)
        rm_act.triggered.connect(lambda: self._remove_compare(cmp))
        menu.addAction(rm_act)
        menu.exec(cmp.chip.mapToGlobal(pos))

    def _recolor_compare(self, cmp: _Compare) -> None:
        picked = QColorDialog.getColor(QColor(cmp.color), self, f"{cmp.symbol} color")
        if not picked.isValid():
            return
        if _mark_too_low_contrast(picked):
            self.set_status(f"{_mark_reject_msg()} — keeping old color")
            return
        cmp.color = picked.name()
        if cmp.curve is not None:
            cmp.curve.setPen(self._compare_pen(cmp.color))
        self._style_compare_chip(cmp)

    def _remove_compare(self, cmp: _Compare) -> None:
        if cmp.curve is not None:
            self.plot_widget.removeItem(cmp.curve)
        if cmp.chip is not None:
            self._chips_row.removeWidget(cmp.chip)
            cmp.chip.hide()  # see _remove_indicator: hidden now, deleted later
            cmp.chip.deleteLater()
        self._compares.remove(cmp)
        self._fit_y_to_visible()

    def _clear_compares(self) -> None:
        for cmp in list(self._compares):
            self._remove_compare(cmp)

    # -- navigation ------------------------------------------------------------
    #
    # pyqtgraph's default ViewBox scaled price and time together around the
    # cursor, let a drag pan both axes off into blank space, and never refit
    # price to what was on screen — so every zoom left the candles squashed
    # into a strip or cut off. Trading charts navigate time, and price follows.

    def _setup_navigation(self) -> None:
        vb = self.plot_widget.getViewBox()
        self.plot_widget.setMouseEnabled(x=True, y=False)
        self.plot_widget.hideButtons()  # the stray "A" auto-range button
        vb.state["wheelScaleFactor"] = WHEEL_SCALE_FACTOR
        self._fitting_y = False
        vb.sigXRangeChanged.connect(lambda *_: self._on_view_x_changed())

    def _bar_seconds(self) -> float:
        t = self._hist_t
        if len(t) < 2:
            return 86400.0
        gaps = sorted(b - a for a, b in zip(t, t[1:]) if b > a)
        return gaps[len(gaps) // 2] if gaps else 86400.0

    def _apply_x_limits(self) -> None:
        """Keep the view on the data: a little air on the left, room to see
        the last bar clear of the edge on the right, and zoom stops at a
        handful of bars rather than at one pixel-wide sliver."""
        vb = self.plot_widget.getViewBox()
        t = self._hist_t
        if not t:
            vb.setLimits(xMin=None, xMax=None, minXRange=None, maxXRange=None)
            return
        x_min = -2.0
        x_max = (len(t) - 1) + 8.0
        vb.setLimits(
            xMin=x_min,
            xMax=x_max,
            minXRange=min(float(MIN_VISIBLE_BARS), x_max - x_min),
            maxXRange=x_max - x_min,
        )

    def _on_view_x_changed(self) -> None:
        self._render_compares()  # re-anchor before y fits to what is drawn
        self._fit_y_to_visible()

    def _fit_y_to_visible(self) -> None:
        """Fit price to the bars currently in view (log-aware)."""
        if self._fitting_y or not self._hist_t:
            return
        vb = self.plot_widget.getViewBox()
        (x0, x1), _ = vb.viewRange()
        i0 = max(0, math.ceil(x0))
        i1 = min(len(self._hist_t), math.floor(x1) + 1)
        if i1 <= i0:
            return
        lo = min(self._hist_lo[i0:i1])
        hi = max(self._hist_hi[i0:i1])
        for cmp in self._compares:
            ys = [y for y in self._compare_values(cmp)[i0:i1] if y is not None]
            if ys:
                lo, hi = min(lo, min(ys)), max(hi, max(ys))
        if self._log_on:
            if lo <= 0:
                return
            lo, hi = math.log10(lo), math.log10(hi)
        pad = (hi - lo) * 0.06 or max(abs(hi), 1.0) * 1e-3
        self._fitting_y = True
        try:
            vb.setYRange(lo - pad, hi + pad, padding=0)
        finally:
            self._fitting_y = False

    def _reset_view(self) -> None:
        self._morph.finish()
        self._frame_window(self._hist_t, self._hist_hi, self._hist_lo)

    def _on_pane_clicked(self, ev) -> None:
        if ev.double() and ev.button() == Qt.MouseButton.LeftButton:
            self._reset_view()
            ev.accept()

    def _frame_window(self, t: list, highs: list, lows: list) -> None:
        """Zoom the plot to the selected range even though more bars may have
        been fetched, and fit Y to just the visible candles."""
        if not t:
            return
        span_days = range_span_days(self._range)
        if span_days is not None:
            end = t[-1]
            span = span_days * 86400
            start = max(end - span, t[0])
        else:
            start = datetime.fromisoformat(self._range["start"]).timestamp()
            end = (
                datetime.fromisoformat(self._range["end"]) + timedelta(days=1)
            ).timestamp()
        self.plot_widget.setXRange(
            self._x_of_time(start), self._x_of_time(end), padding=0.02
        )
        # explicit: an unchanged X range emits no sigXRangeChanged
        self._fit_y_to_visible()

    # -- data callbacks ------------------------------------------------------

    def _on_history(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        if not isinstance(data, dict):
            return
        self._morph.history_arriving()
        t = data.get("t") or []
        o = data.get("o") or []
        h = data.get("h") or []
        l = data.get("l") or []
        c = data.get("c") or []
        v = data.get("v") or []
        if not t:
            self._morph.cancel()
            self.set_status("no data")
            self._hist_t, self._hist_o, self._hist_c = [], [], []
            self._hist_hi, self._hist_lo, self._hist_v = [], [], []
            self._refresh_all_indicators()
            self._apply_x_limits()
            self._render_lane()
            self._realign_compares()
            self._sync_empty()
            return
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
        xs = self._hist_x
        self.candle_item.set_ohlc(xs, valid_o, valid_h, valid_l, valid_c)
        self.bar_item.set_ohlc(xs, valid_o, valid_h, valid_l, valid_c)
        self._refresh_all_indicators()
        self._apply_chart_type()
        self._apply_x_limits()
        self._frame_window(valid_t, valid_h, valid_l)
        self._render_annotations()  # keep horizontal drawings spanning the bars
        self._render_lane()  # unusual-move flags depend on the loaded closes
        self._realign_compares()

        rng = (
            self._range.get("preset")
            or self._range.get("lookback")
            or f"{self._range.get('start')}→{self._range.get('end')}"
        )
        self.set_status(f"{rng} · {self._interval}")
        self._sync_empty()
        # last: the morph starts from the finished chart, never builds it
        self._morph.history_landed()

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
            self.chg_lbl.setStyleSheet(f"color: {color};")  # weight and face: the role

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
            "events_lane": self._lane_on,
            "compare": [
                {"symbol": c.symbol, "color": c.color, "on": c.on}
                for c in self._compares
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
        if isinstance(rng, dict) and rng.get("preset") in LEGACY_LOOKBACK_PRESETS:
            # 1h / 4h left the chip row for custom…; open them as lookbacks
            rng = {"lookback": rng["preset"]}
        if isinstance(rng, dict) and (
            rng.get("preset") in PERIOD_SPAN_DAYS
            or lookback_days(rng.get("lookback")) is not None
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
            substituted = 0
            for position, entry in enumerate(saved):
                if not isinstance(entry, dict) or entry.get("kind") not in INDICATOR_SPECS:
                    continue
                params = entry.get("params")
                color, swapped = _restored_indicator_color(
                    entry.get("color"), position, self._colors["bg"]
                )
                substituted += swapped
                self._add_indicator(
                    entry["kind"],
                    params if isinstance(params, dict) else {},
                    color=color,
                    on=bool(entry.get("on", True)),
                    rebuild=False,
                )
            if substituted:
                # Say so. Quietly repainting a colour the user chose is the kind
                # of thing that reads as a bug when they notice it later.
                self.set_status(
                    f"⚠ {substituted} indicator colour(s) were invisible on this "
                    "theme — reset to the palette"
                )

        compares = settings.get("compare")
        if isinstance(compares, list):
            self._clear_compares()
            for entry in compares[:MAX_COMPARES]:
                if not isinstance(entry, dict) or not isinstance(entry.get("symbol"), str):
                    continue
                color = entry.get("color")
                if not (isinstance(color, str) and QColor(color).isValid()):
                    color = None
                self._add_compare(entry["symbol"], color=color,
                                  on=bool(entry.get("on", True)), subscribe=False)

        lane = settings.get("events_lane")
        if isinstance(lane, bool):
            self._lane_on = lane
            if self._lane is not None:
                self._lane.pane.setVisible(lane)

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
