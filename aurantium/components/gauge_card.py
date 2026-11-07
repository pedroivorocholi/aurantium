"""GaugeCard — one Macro gauge opened up: its figures over a real chart.

A click on a gauge tile opens this card under the tile's row (the strip owns
where it sits and how it grows; see ``GaugeStrip``). Top to bottom:

* the gauge's name, its value and today's change — the tile's own figures,
  set from the tile so the two can never disagree — with **1M / 6M / 1Y /
  5Y** range chips on the right;
* a chart of the chosen range, with a hover readout snapped to the nearest
  day that has a value;
* the range's high and low, each with its date, and the source with its
  as-of ("U.S. Treasury · 5 Nov").

The card is dumb, like the strip: the panel fills it with a ``GaugeDetail``
(the series, how to print a value, the source, and why a chip can't draw).
A chip with nothing to draw is disabled and its tooltip says why. With no
series, a sentence stands in for the chart ("BIS publishes the latest rate
only") or, while the data is on its way, "Loading history…".

A click on the card away from its chips and chart, or Escape, asks the strip
to close it (``close_requested``).
"""

from __future__ import annotations

import bisect
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Mapping, Optional

import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..theme import (
    BG,
    BG_ALT,
    BORDER_STRONG,
    CHART_AXIS_PX,
    CHART_DOT_PX,
    CHART_LINE_PX,
    DOWN,
    FG_DIM,
    FONT_MD,
    FONT_SM,
    FONT_TITLE,
    LABEL,
    MONO_FONT,
    RADIUS_MD,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XS,
    UI_FONT,
    UP,
    WEIGHT_MEDIUM,
)
from .responsive_row import ResponsiveRow

#: the range chips, in display order; the default is the first
RANGES = ("1M", "6M", "1Y", "5Y")
DEFAULT_RANGE = RANGES[0]

MSG_LOADING = "Loading history…"
#: the app sheet paints every QWidget with BG; the card's parts sit on its
#: own surface instead
_CLEAR = "background: transparent;"
CLOSE_HINT = "Click here or press Esc to close"

#: the chart never gets shorter than this, however the header wraps
_CHART_MIN_PX = 6 * SPACE_LG + 2 * SPACE_XS
#: the floor a footer figure keeps before it elides
_FOOT_MIN_PX = 12 * SPACE_LG
#: the narrowest dock a panel is laid out for (tests/components/test_fit_width)
_NARROWEST_PX = 260


def _epoch(d: date) -> int:
    """A day as the chart's x: UTC midnight, read back on a UTC date axis."""
    return calendar.timegm(d.timetuple())


def _day(d: date, last: Optional[date]) -> str:
    """'5 Nov' in the series' last year, '5 Nov 2025' otherwise."""
    text = f"{d.day} {d.strftime('%b')}"
    return text if last is not None and d.year == last.year else f"{text} {d.year}"


def _full_day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')} {d.year}"


def _plain(value: float) -> str:
    return f"{value:,.2f}"


@dataclass(frozen=True)
class GaugeDetail:
    """What the panel hands the card for the open gauge.

    ``series`` is ``(day, value)`` oldest first, already cut to ``range``;
    ``fmt`` prints one value the way the tile does; ``reasons`` maps a chip
    that can't draw to the sentence its tooltip gives; ``message`` stands in
    for the chart when there is no series; ``loading`` says the series is on
    its way."""

    series: tuple = ()
    fmt: Callable[[float], str] = _plain
    source: str = ""
    range: str = DEFAULT_RANGE
    reasons: Mapping[str, str] = field(default_factory=dict)
    message: str = ""
    loading: bool = False
    #: the smallest value span the chart shows: a spread that moved 1bp in
    #: a month is a flat line, not a mountain range
    min_span: float = 0.0


#: room between two date labels, beyond the labels themselves
_TICK_GAP_PX = 2 * SPACE_LG
#: a span up to this many days is labelled by day ("3 Nov"); up to
#: _MONTHS_UP_TO by month ("Nov"); anything longer by year ("2024")
_DAYS_UP_TO = 62
_MONTHS_UP_TO = 2 * 366
#: tick steps per unit, finest first. Never a label on every day.
_DAY_STEPS = (7, 14)
_MONTH_STEPS = (1, 2, 3, 6)
_YEAR_STEPS = (1, 2, 5, 10, 25)
_DAY_S = 86400
_MONDAY = date(1970, 1, 5)  # day ticks count weeks from here


def _utc_day(x: float) -> date:
    return date(1970, 1, 1) + timedelta(days=int(x // _DAY_S))


class _DateAxis(pg.AxisItem):
    """The card's time axis: one tick level, calendar-aligned, with labels
    that suit the span and never touch.

    A month reads by day ("3 Nov", on Mondays), six months or a year by month ("Nov";
    January carries its year, "2026", so a year turning is marked), five
    years by year ("2024"). The step is the finest one whose labels each
    get their own width plus a gap at the axis's current width; when even
    the coarsest day or month step is too tight, the next unit up is used.
    Values are UTC midnights (``_epoch``)."""

    def __init__(self) -> None:
        super().__init__("bottom")
        self._date_fm = QFontMetrics(QFont())

    def setTickFont(self, font) -> None:  # noqa: N802 (pyqtgraph API)
        super().setTickFont(font)
        self._date_fm = QFontMetrics(font)

    # -- choosing ticks -------------------------------------------------------

    def _logical(self, size: float) -> float:
        """pyqtgraph measures the axis in device pixels; labels are measured
        in logical ones."""
        view = self.getViewWidget()
        ratio = view.devicePixelRatioF() if view is not None else 1.0
        return size / (ratio or 1.0)

    def _candidates(self, span_days: float):
        days = [("day", n) for n in _DAY_STEPS]
        months = [("month", n) for n in _MONTH_STEPS]
        years = [("year", n) for n in _YEAR_STEPS]
        if span_days <= _DAYS_UP_TO:
            return days + months + years
        if span_days <= _MONTHS_UP_TO:
            return months + years
        return years

    def _label_w(self, unit: str) -> int:
        samples = {"day": ("30 Sep", "28 May"), "month": ("May", "Sep", "2026"),
                   "year": ("2026",)}[unit]
        return max(self._date_fm.horizontalAdvance(t) for t in samples)

    @staticmethod
    def _period_days(unit: str, n: int) -> float:
        return {"day": 1.0, "month": 30.44, "year": 365.25}[unit] * n

    @staticmethod
    def _values(unit: str, n: int, lo: date, hi: date) -> list[int]:
        out = []
        if unit == "day":
            # Mondays (every other one for 14): evenly spaced, unlike dates
            # of the month, whose last step to the 1st is a short one
            d = lo
            while d <= hi:
                weeks, weekday = divmod((d - _MONDAY).days, 7)
                if weekday == 0 and weeks % (n // 7) == 0:
                    out.append(d)
                d += timedelta(days=1)
        elif unit == "month":
            y, m = lo.year, lo.month
            while date(y, m, 1) <= hi:
                if (m - 1) % n == 0 and date(y, m, 1) >= lo:
                    out.append(date(y, m, 1))
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        else:
            for y in range(lo.year, hi.year + 1):
                if y % n == 0 and date(y, 1, 1) >= lo:
                    out.append(date(y, 1, 1))
        return [_epoch(d) for d in out]

    def ticks(self, lo: float, hi: float, width: float) -> tuple[str, list[int]]:
        """(unit, tick values) for the span ``lo``..``hi`` drawn ``width``
        logical px wide."""
        if hi <= lo or width <= 0:
            return "day", []
        span_days = (hi - lo) / _DAY_S
        px_per_day = width / span_days
        first, last = _utc_day(lo), _utc_day(hi)
        if _epoch(first) < lo:
            first += timedelta(days=1)
        choice = None
        for unit, n in self._candidates(span_days):
            if self._period_days(unit, n) * px_per_day >= self._label_w(unit) + _TICK_GAP_PX:
                choice = (unit, n)
                break
        if choice is None:
            choice = ("year", _YEAR_STEPS[-1])
        return choice[0], self._values(*choice, first, last)

    def tickValues(self, minVal, maxVal, size):  # noqa: N802 (pyqtgraph API)
        unit, values = self.ticks(minVal, maxVal, self._logical(size))
        self._date_unit = unit
        return [(self._period_days(unit, 1) * _DAY_S, values)]

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph API)
        return [self.date_text(v, getattr(self, "_date_unit", "day")) for v in values]

    @staticmethod
    def date_text(value: float, unit: str) -> str:
        d = _utc_day(value)
        if unit == "day":
            return f"{d.day} {d.strftime('%b')}"
        if unit == "month":
            return str(d.year) if d.month == 1 else d.strftime("%b")
        return str(d.year)

    def tick_labels(self) -> list[tuple[float, str]]:
        """(centre x in logical px along the axis, text) of every label at
        the axis's current range and width (tests, and nothing else)."""
        lo, hi = self.range
        width = self.geometry().width()
        unit, values = self.ticks(lo, hi, width)
        if hi <= lo:
            return []
        return [((v - lo) / (hi - lo) * width, self.date_text(v, unit))
                for v in values if lo <= v <= hi]

    def label_width(self, text: str) -> int:
        return self._date_fm.horizontalAdvance(text)


class _FitLabel(QLabel):
    """A footer figure ("High 4.52% · 5 Nov 2025", the source). Its minimum
    is its whole text, up to a floor, so a narrow footer puts it on a line
    of its own rather than cutting it; only a line too narrow for even that
    shortens it, with an ellipsis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full = ""

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = str(text or "")
        self._fit()
        self.updateGeometry()

    def full_text(self) -> str:
        return self._full

    def _natural(self) -> int:
        m = self.contentsMargins()
        return self.fontMetrics().horizontalAdvance(self._full) + m.left() + m.right()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._natural(), super().sizeHint().height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(min(self._natural(), _FOOT_MIN_PX), super().minimumSizeHint().height())

    def _fit(self) -> None:
        shown = self._full
        if self.width() < self._natural():
            shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight,
                                                  max(1, self.width()))
        super().setText(shown)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._fit()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._fit()


def _label(parent: QWidget, px: int, color: str, mono: bool = False, weight: int = 0,
           fit: bool = False) -> QLabel:
    lbl = _FitLabel(parent) if fit else QLabel("", parent)
    family = MONO_FONT if mono else UI_FONT
    weight_css = f" font-weight: {weight};" if weight else ""
    lbl.setStyleSheet(f'{_CLEAR} color: {color}; font-family: "{family}"; font-size: {px}px;{weight_css}')
    if not fit:
        lbl.setMinimumWidth(1)  # clips, never widens a narrow panel
    # clicks fall through to the card, which closes on them
    lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    return lbl


class GaugeCard(QWidget):
    range_picked = Signal(str)
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._series: tuple[tuple[date, float], ...] = ()
        self._xs: list[int] = []
        self._fmt: Callable[[float], str] = _plain
        self._range = DEFAULT_RANGE
        self._message = MSG_LOADING
        self._loading = True
        self._min_span = 0.0
        self._press: Optional[QPoint] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(SPACE_LG, SPACE_MD, SPACE_LG, SPACE_MD)
        root.setSpacing(SPACE_SM)

        # -- heading: name, value and change; range chips on the right -----------
        head = ResponsiveRow()
        head.setSpacing(SPACE_LG)
        figures = QWidget(self)
        figures.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        figures.setStyleSheet(_CLEAR)
        col = QVBoxLayout(figures)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._name = _label(figures, FONT_SM, FG_DIM)
        col.addWidget(self._name)
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(SPACE_MD)
        self._value = _label(figures, FONT_TITLE, LABEL, mono=True, weight=WEIGHT_MEDIUM)
        self._change = _label(figures, FONT_MD, FG_DIM, mono=True)
        line.addWidget(self._value)
        line.addWidget(self._change, 0, Qt.AlignmentFlag.AlignBaseline)
        line.addStretch(1)
        col.addLayout(line)
        # the tile's note ("cut 25bp in Oct 2026", "inverted", "as of 1 Sep")
        self._note_line = _label(figures, FONT_SM, FG_DIM)
        self._note_line.hide()
        col.addWidget(self._note_line)
        head.addWidget(figures, 1)

        self._chip_row = QWidget(self)
        self._chip_row.setObjectName("gaugeCardChips")
        self._chip_row.setStyleSheet(f"QWidget#gaugeCardChips {{ {_CLEAR} }}")
        chips = ResponsiveRow(self._chip_row)  # wraps when even they don't fit
        # never wider than the chips (they stay right-aligned); vertically
        # Expanding: the heading's row then gives it the height
        # its wrapped lines need, not one line's
        self._chip_row.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Expanding)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(SPACE_SM)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._chips: dict[str, QPushButton] = {}
        for name in RANGES:
            b = QPushButton(name, self._chip_row)
            b.setObjectName("chartChip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, r=name: self._on_chip(r))
            self._group.addButton(b)
            chips.addWidget(b)
            self._chips[name] = b
        head.addWidget(self._chip_row)
        root.addLayout(head)

        # -- the chart, or a sentence in its place ---------------------------------
        self._body = QStackedWidget(self)
        self._body.setMinimumHeight(_CHART_MIN_PX)
        self._body.setStyleSheet(f"QStackedWidget {{ {_CLEAR} }}")
        self.plot = self._make_plot()
        self._body.addWidget(self.plot)
        self._note = QLabel("", self._body)
        self._note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._note.setWordWrap(True)
        self._note.setStyleSheet(f"{_CLEAR} color: {FG_DIM}; font-size: {FONT_SM}px;")
        self._note.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._body.addWidget(self._note)
        root.addWidget(self._body, 1)

        # -- footer: high, low, source -------------------------------------------
        foot = ResponsiveRow()
        foot.setSpacing(SPACE_LG)
        # kept whole: a narrow card wraps them onto lines of their own
        self._high = _label(self, FONT_SM, FG_DIM, mono=True, fit=True)
        self._low = _label(self, FONT_SM, FG_DIM, mono=True, fit=True)
        self._source = _label(self, FONT_SM, FG_DIM, fit=True)
        foot.addWidget(self._high)
        foot.addWidget(self._low)
        foot.addStretch(1)
        foot.addWidget(self._source)
        root.addLayout(foot)

        self._apply()

    # -- the chart -------------------------------------------------------------------

    def _make_plot(self) -> pg.PlotWidget:
        """The app's small-chart style (see curve_view._make_plot): the card's
        own surface, faint grid, dim tabular ticks, no zoom or pan."""
        w = pg.PlotWidget(axisItems={"bottom": _DateAxis()})
        w.setMenuEnabled(False)
        w.setBackground(BG_ALT)
        w.showGrid(x=True, y=True, alpha=0.15)
        w.hideButtons()
        w.setMinimumHeight(1)
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        vb = w.getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        vb.disableAutoRange()
        font = QFont(MONO_FONT)
        font.setPixelSize(FONT_SM)
        for name in ("left", "bottom"):
            ax = w.getAxis(name)
            ax.setTextPen(FG_DIM)
            ax.setPen(pg.mkPen(BORDER_STRONG))
            ax.setTickFont(font)
        left = w.getAxis("left")
        left.setWidth(CHART_AXIS_PX)
        left.enableAutoSIPrefix(False)
        left.setStyle(maxTickLevel=0)
        left.setTickDensity(0.8)

        self._line = pg.PlotDataItem(pen=pg.mkPen(LABEL, width=CHART_LINE_PX))
        self._line.setZValue(10)
        w.addItem(self._line)
        self._last = pg.ScatterPlotItem(size=CHART_DOT_PX, brush=pg.mkBrush(LABEL),
                                        pen=pg.mkPen(BG_ALT, width=1))
        self._last.setZValue(11)
        w.addItem(self._last)
        self._guide = pg.InfiniteLine(angle=90, movable=False,
                                      pen=pg.mkPen(FG_DIM, style=Qt.PenStyle.DotLine))
        self._guide.setZValue(20)
        self._guide.setVisible(False)
        w.addItem(self._guide, ignoreBounds=True)
        self._dot = pg.ScatterPlotItem(size=CHART_DOT_PX, brush=pg.mkBrush(LABEL),
                                       pen=pg.mkPen(BG, width=1))
        self._dot.setZValue(21)
        w.addItem(self._dot, ignoreBounds=True)
        self._readout = pg.TextItem("", color=LABEL, anchor=(0, 1), fill=pg.mkBrush(BG),
                                    border=pg.mkPen(BORDER_STRONG))
        self._readout.setFont(font)
        self._readout.setZValue(22)
        self._readout.setVisible(False)
        w.addItem(self._readout, ignoreBounds=True)
        w.installEventFilter(self)
        self._hover_proxy = pg.SignalProxy(w.scene().sigMouseMoved, rateLimit=60,
                                           slot=lambda ev: self._on_mouse(ev[0]))
        return w

    # -- the strip / panel talking (never emits) -------------------------------------

    def set_heading(self, label: str, value: str, change: str, sign: int,
                    note: str = "", note_tone: str = "") -> None:
        self._name.setText(label)
        self._value.setText(value)
        self._change.setText(change)
        color = UP if sign > 0 else DOWN if sign < 0 else FG_DIM
        self._change.setStyleSheet(f'{_CLEAR} color: {color}; font-family: "{MONO_FONT}"; '
                                   f'font-size: {FONT_MD}px;')
        self._note_line.setText(note)
        self._note_line.setStyleSheet(f"{_CLEAR} color: {DOWN if note_tone == 'down' else FG_DIM}; "
                                      f"font-size: {FONT_SM}px;")
        self._note_line.setVisible(bool(note))

    def set_loading(self, range_: Optional[str] = None) -> None:
        """Nothing to show yet (another gauge just opened, the country
        changed): the chart says it is loading; the chips stay usable."""
        self.set_detail(GaugeDetail(range=range_ or self._range, loading=True))

    def set_detail(self, detail: GaugeDetail) -> None:
        self._fmt = detail.fmt or _plain
        self._range = detail.range if detail.range in RANGES else DEFAULT_RANGE
        series = []
        for d, v in detail.series or ():
            if isinstance(d, date):
                try:
                    series.append((d, float(v)))
                except (TypeError, ValueError):
                    continue
        self._series = tuple(series)
        self._loading = bool(detail.loading) and not self._series
        self._message = "" if self._series else (MSG_LOADING if self._loading else detail.message)
        self._min_span = max(0.0, float(detail.min_span or 0.0))
        reasons = dict(detail.reasons or {})
        # an exclusive group won't let its checked chip go: with every chip
        # disabled (a source with no history) none may stay lit
        self._group.setExclusive(False)
        for name, b in self._chips.items():
            why = reasons.get(name)
            b.setEnabled(why is None)
            b.setToolTip(why or f"Show the last {self._span_words(name)}")
            b.setChecked(name == self._range and why is None)
        self._group.setExclusive(True)
        self._source.setText(detail.source)
        self._apply()

    # -- reading back (the strip, tests) -------------------------------------------

    def chip(self, name: str) -> QPushButton:
        return self._chips[name]

    def range(self) -> str:  # noqa: A003 (reads as the chip's name)
        return self._range

    def series(self) -> tuple:
        return self._series

    def is_loading(self) -> bool:
        return self._loading

    def message(self) -> str:
        return self._message

    def label_text(self) -> str:
        return self._name.text()

    def value_text(self) -> str:
        return self._value.text()

    def change_text(self) -> str:
        return self._change.text()

    def high_text(self) -> str:
        return self._high.full_text()

    def low_text(self) -> str:
        return self._low.full_text()

    def source_text(self) -> str:
        return self._source.full_text()

    def hover_text_near(self, day: date) -> str:
        """The readout the chart shows with the pointer over ``day``."""
        i = self._nearest(_epoch(day))
        return "" if i is None else self._readout_text(i)

    # -- size ------------------------------------------------------------------------

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        """The height the header, the footer and the shortest chart need at
        ``width`` (the strip opens the card at least this tall)."""
        lay = self.layout()
        m = lay.contentsMargins()
        inner = max(1, width - m.left() - m.right())
        total = m.top() + m.bottom()
        for i in range(lay.count()):
            item = lay.itemAt(i)
            if item.widget() is self._body:
                total += _CHART_MIN_PX
            elif item.hasHeightForWidth():
                total += item.heightForWidth(inner)
            else:
                total += item.sizeHint().height()
        return total + lay.spacing() * (lay.count() - 1)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(1, self.heightForWidth(_NARROWEST_PX))

    # -- user input --------------------------------------------------------------------

    def _on_chip(self, name: str) -> None:
        if name == self._range:
            self._chips[name].setChecked(True)  # one is always on
            return
        self.range_picked.emit(name)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pos = event.position().toPoint()
        self._press = None if self._on_chart(pos) else pos
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pressed, self._press = self._press, None
        if (event.button() == Qt.MouseButton.LeftButton and pressed is not None
                and self.rect().contains(event.position().toPoint())):
            self.close_requested.emit()
        event.accept()

    def event(self, event) -> bool:  # noqa: A003 (Qt override)
        if (event.type() == QEvent.Type.ShortcutOverride
                and event.key() == Qt.Key.Key_Escape and self.isVisible()):
            event.accept()  # ours before a window's Esc shortcut takes it
            return True
        if event.type() == QEvent.Type.ToolTip:
            # chips answer for themselves; the rest of the card says how to
            # close it, except over the chart, where a click does not
            tip = self.tooltip_at(event.pos())
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def tooltip_at(self, pos) -> str:
        """The card's own tooltip at ``pos`` (card coordinates)."""
        if self._on_chart(pos):
            return ""
        return CLOSE_HINT

    def _on_chart(self, pos) -> bool:
        return self._body.currentWidget() is self.plot and self._body.geometry().contains(pos)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape:
            self.close_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.plot and event.type() == QEvent.Type.Leave:
            self._hide_hover()
        return super().eventFilter(obj, event)

    # -- drawing -----------------------------------------------------------------------

    @staticmethod
    def _span_words(name: str) -> str:
        n, unit = name[:-1], {"M": "month", "Y": "year"}[name[-1]]
        return unit if n == "1" else f"{n} {unit}s"

    def _apply(self) -> None:
        self._hide_hover()
        series = self._series
        self._xs = [_epoch(d) for d, _v in series]
        if not series:
            self._line.setData([], [])
            self._last.setData([])
            self._note.setText(self._message)
            self._body.setCurrentWidget(self._note)
            self._high.setText("")
            self._low.setText("")
            self._high.setVisible(False)
            self._low.setVisible(False)
            return
        ys = [v for _d, v in series]
        self._line.setData(self._xs, ys)
        self._last.setData([self._xs[-1]], [ys[-1]])
        self._body.setCurrentWidget(self.plot)
        last = series[-1][0]
        hi = max(range(len(ys)), key=lambda i: (ys[i], i))   # the latest of equal highs
        lo = min(range(len(ys)), key=lambda i: (ys[i], -i))
        self._high.setText(f"High {self._fmt(ys[hi])} · {_day(series[hi][0], last)}")
        self._low.setText(f"Low {self._fmt(ys[lo])} · {_day(series[lo][0], last)}")
        self._high.setVisible(True)
        self._low.setVisible(True)
        x0, x1 = self._xs[0], self._xs[-1]
        if x1 <= x0:
            x0, x1 = x0 - 86400, x1 + 86400
        y0, y1 = min(ys), max(ys)
        span = max(self._min_span, (abs(y0) * 0.01 or 1.0) if y1 <= y0 else 0.0)
        if y1 - y0 < span:
            mid = (y0 + y1) / 2
            y0, y1 = mid - span / 2, mid + span / 2
        vb = self.plot.getViewBox()
        vb.setXRange(x0, x1, padding=0.02)
        vb.setYRange(y0, y1, padding=0.1)

    def _nearest(self, x: float) -> Optional[int]:
        xs = self._xs
        if not xs:
            return None
        k = bisect.bisect_left(xs, x)
        if k <= 0:
            return 0
        if k >= len(xs):
            return len(xs) - 1
        return k if xs[k] - x < x - xs[k - 1] else k - 1

    def _readout_text(self, i: int) -> str:
        d, v = self._series[i]
        return f"{_full_day(d)}  {self._fmt(v)}"

    def _on_mouse(self, pos) -> None:
        plot = self.plot
        if (not self._series or self._body.currentWidget() is not plot
                or not plot.sceneBoundingRect().contains(pos)):
            self._hide_hover()
            return
        x = plot.getViewBox().mapSceneToView(pos).x()
        i = self._nearest(x)
        if i is None:
            self._hide_hover()
            return
        px, py = self._xs[i], self._series[i][1]
        self._guide.setPos(px)
        self._guide.setVisible(True)
        self._dot.setData([px], [py])
        self._readout.setText(self._readout_text(i))
        # beside the point, on whichever side has the room
        (x0, x1), (y0, y1) = plot.getViewBox().viewRange()
        self._readout.setAnchor((1.0 if px > (x0 + x1) / 2 else 0.0,
                                 0.0 if py > (y0 + y1) / 2 else 1.0))
        self._readout.setPos(px, py)
        self._readout.setVisible(True)

    def _hide_hover(self) -> None:
        self._guide.setVisible(False)
        self._dot.setData([])
        self._readout.setVisible(False)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            pen = QPen(QColor(BORDER_STRONG))
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.setBrush(QColor(BG_ALT))
            p.drawRoundedRect(body, RADIUS_MD, RADIUS_MD)
        finally:
            p.end()
