"""CurveView — one country's whole yield curve, comparable against its past.

The heart of Macro / Rates. Top to bottom:

* a header row: the country switch (all 16 countries, each marked with what it
  honestly has), the source's citation with *its own* as-of date (never a
  global one), a ``partial — N points`` caveat for sparse curves, and the
  **1W / 1M / 1Y** comparison chips (any combination);
* the curve: maturities on a square-root axis so the short end is readable,
  labelled with tenors; the current curve in the data ink, each comparison as
  a fainter ghost of it labelled at its long end; the 2Y→10Y segment in the
  DOWN colour while the curve is inverted there;
* the twist strip: one bar per tenor, the bp change versus the first selected
  comparison (or the prior day when none is selected), its x axis locked to
  the curve's.

Hover anywhere on either plot snaps to the nearest observed tenor and reads
out the yield and the change versus every comparison on screen.

The view knows nothing about topics. The panel feeds ``set_curve`` /
``set_history``, sets the state, and listens to ``country_changed`` and
``comparisons_changed`` — which fire on the user's clicks only, never from a
setter. Points are only ever *observed*: nothing here interpolates, and a
comparison or bar exists only where both curves carry that tenor.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional, Sequence

import pyqtgraph as pg
from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from .. import motion, rates_meta, transitions
from ..macro_model import axis_x, bp_changes, comparison_date, point_on_or_before, rate_at, tenor_label
from ..theme import (
    BG,
    BORDER_STRONG,
    DOWN,
    FG_DIM,
    FG_MUTED,
    FONT_SM,
    FONT_XS,
    LABEL,
    MONO_FONT,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    UP,
)
from .empty_state import EmptyState
from .fmt import NULL_GLYPH, signed
from .responsive_row import ResponsiveRow

#: the comparison chips, in display (and list) order
CHIPS = ("1W", "1M", "1Y")
#: builder's pick per the spec ("none by default… or 1M"): one ghost is what a
#: reader of a curve almost always wants, and it makes the strip meaningful
DEFAULT_COMPARISONS = ["1M"]
STATES = ("curve", "partial", "none", "loading", "error")

MSG_POLICY_ONLY = "Policy rate only — no free yield curve for this country"
MSG_NO_HISTORY = "This source publishes only the latest curve"
MSG_NO_HISTORY_THAT_FAR = "No history that far back for this source"
MSG_NO_POINTS = "No curve points published"

SUFFIX_POLICY_ONLY = " · policy rate only"
SUFFIX_PARTIAL = " · partial"

#: ghost strength per comparison: the further back, the fainter
_GHOST_ALPHA = {"1W": 0.62, "1M": 0.44, "1Y": 0.28}
#: which tenor labels survive when the axis is too narrow for all of them
_LABEL_PRIORITY = (10.0, 2.0, 30.0, 5.0, 0.25, 1.0, 20.0, 7.0, 0.0833, 3.0, 0.5, 40.0, 15.0)
#: px a value axis is given, on both plots, so their x transforms line up
_LEFT_AXIS_PX = 46
#: px of breathing room each side of the outermost tenors
_EDGE_PX = 14
#: extra px on the right for the ghosts' "1M" end labels
_GHOST_LABEL_PX = 24
#: px between two tenor labels on the axis
_TICK_GAP_PX = 8
#: share of the stack the twist strip takes (curve : strip = 3 : 1)
_CURVE_STRETCH, _STRIP_STRETCH = 3, 1
#: px per bar needed before a bar carries its bp figure
_BAR_FIGURE_PX = 30


def _tick_font() -> QFont:
    f = QFont(MONO_FONT)
    f.setPixelSize(FONT_SM)
    return f


def _iso_or_empty(text: str) -> str:
    """``text`` as an ISO date, or "" when it isn't one."""
    try:
        return date.fromisoformat(text.strip()[:10]).isoformat()
    except (ValueError, AttributeError):
        return ""


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(a)
    return c


class _PercentAxis(pg.AxisItem):
    """Yield axis: one tick level, every label with the same decimals and its
    ``%`` ("0.5%  1.0%  1.5%", never "1%" beside "1.5%")."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # pyqtgraph's minor levels labelled every 0.1% down a tall plot
        self.setStyle(maxTickLevel=0)
        self.setTickDensity(1.8)

    def tickStrings(self, values, scale, spacing) -> list[str]:  # noqa: N802
        decimals = max(0, -math.floor(math.log10(spacing * scale))) if spacing > 0 else 2
        return [f"{v * scale:.{decimals}f}%" for v in values]


class _CountryCombo(QComboBox):
    """The country switch. The list says what each country honestly has
    ("China · policy rate only"); the closed combo shows just the name, so the
    header stays compact. Painted through the style, so the theme's combo look
    (and its arrow) apply unchanged."""

    NAME_ROLE = Qt.ItemDataRole.UserRole + 1

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        name = self.currentData(self.NAME_ROLE)
        if name:
            opt.currentText = name
        p.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        p.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # wide enough for the longest *name*, not the longest list entry
        hint = super().sizeHint()
        names = [self.itemData(i, self.NAME_ROLE) or "" for i in range(self.count())]
        if not names:
            return hint
        fm = self.fontMetrics()
        longest = max(fm.horizontalAdvance(n) for n in names)
        widest = max(fm.horizontalAdvance(self.itemText(i)) for i in range(self.count()))
        return QSize(max(hint.width() - (widest - longest), 60), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return self.sizeHint()


def _make_plot(left_axis: Optional[pg.AxisItem] = None) -> pg.PlotWidget:
    """A plot in the app's chart style: data surface background, faint grid,
    dim tabular tick labels, no zoom/pan (a curve is a fixed-point chart)."""
    axes = {"bottom": pg.AxisItem("bottom")}
    if left_axis is not None:
        axes["left"] = left_axis
    w = pg.PlotWidget(axisItems=axes)
    w.setMenuEnabled(False)  # the app's menu, not pyqtgraph's
    w.setBackground(BG)
    w.showGrid(x=True, y=True, alpha=0.15)
    w.hideButtons()
    vb = w.getViewBox()
    vb.setMouseEnabled(x=False, y=False)
    vb.disableAutoRange()
    font = _tick_font()
    for name in ("left", "bottom"):
        ax = w.getAxis(name)
        ax.setTextPen(FG_DIM)
        ax.setPen(pg.mkPen(BORDER_STRONG))
        ax.setTickFont(font)
    w.getAxis("left").setWidth(_LEFT_AXIS_PX)
    w.getAxis("left").enableAutoSIPrefix(False)
    w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    w.setMinimumHeight(1)
    return w


class CurveView(QWidget):
    """One yield curve with 1W / 1M / 1Y comparisons and a twist strip."""

    country_changed = Signal(str)
    comparisons_changed = Signal(list)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[list[float]] = []
        self._complete = True
        self._as_of = ""
        self._as_of_text = ""
        self._citation = ""
        self._ticks: list[tuple[float, str]] = []
        self._days: list = []
        self._comparisons: list[str] = list(DEFAULT_COMPARISONS)
        self._state = "loading"
        self._message = ""
        self._country = ""
        self._countries: list[tuple[str, str]] = []
        self._drawn_country: Optional[str] = None
        self._shown: dict[str, list] = {}       # chip -> points drawn for it
        self._twist: list[tuple[float, float]] = []
        self._tenors: list[float] = []          # every tenor on the axis
        self._hover_years: Optional[float] = None
        self._fades: dict[str, list[QPropertyAnimation]] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_SM)

        # -- header ----------------------------------------------------------
        header = ResponsiveRow()
        header.setSpacing(SPACE_LG)
        self.country_combo = _CountryCombo(self)
        self.country_combo.setToolTip("Country: the curve, policy rate and spreads follow it")
        self.country_combo.activated.connect(self._on_country_activated)
        header.addWidget(self.country_combo)
        self.source_label = QLabel("", self)
        self.source_label.setStyleSheet(f"color: {FG_DIM}; font-size: {FONT_SM}px;")
        self.source_label.setToolTip("Source and the date it last published")
        header.addWidget(self.source_label)
        self.partial_label = QLabel("", self)
        self.partial_label.setStyleSheet(f"color: {FG_DIM}; font-size: {FONT_SM}px;")
        self.partial_label.setToolTip(
            "This source publishes only these maturities; nothing between them is drawn in"
        )
        self.partial_label.hide()
        header.addWidget(self.partial_label)
        header.addStretch(1)
        self._chip_row = QWidget(self)
        chips = QHBoxLayout(self._chip_row)
        chips.setContentsMargins(0, 0, 0, 0)
        chips.setSpacing(SPACE_SM)
        self._chips: dict[str, QPushButton] = {}
        for chip in CHIPS:
            b = QPushButton(chip, self._chip_row)
            b.setObjectName("chartChip")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(chip in self._comparisons)
            b.clicked.connect(self._on_chip_clicked)
            chips.addWidget(b)
            self._chips[chip] = b
        header.addWidget(self._chip_row)
        root.addLayout(header)

        # -- body: the two plots, or a message ---------------------------------
        self._body = QStackedWidget(self)
        self._plots = QWidget(self._body)
        stack = QVBoxLayout(self._plots)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        self.curve_plot = _make_plot(_PercentAxis("left"))
        self.strip_plot = _make_plot()
        # a short strip: zero and one step either side is all it can label
        self.strip_plot.getAxis("left").setStyle(maxTickLevel=0)
        self.strip_plot.getAxis("left").setTickDensity(0.6)
        self.strip_plot.setXLink(self.curve_plot)
        stack.addWidget(self.curve_plot, _CURVE_STRETCH)
        stack.addWidget(self.strip_plot, _STRIP_STRETCH)
        self._body.addWidget(self._plots)

        self._message_page = QWidget(self._body)
        self.empty_state = self._empty = EmptyState(self._message_page)
        self._message_page.installEventFilter(self)
        self._body.addWidget(self._message_page)
        root.addWidget(self._body, 1)

        self._build_items()
        self._apply_state()

    # -- plot items ------------------------------------------------------------

    def _build_items(self) -> None:
        cp, sp = self.curve_plot, self.strip_plot
        self.curve_item = pg.PlotDataItem()
        self.curve_item.setZValue(10)
        cp.addItem(self.curve_item)
        self.inversion_item = pg.PlotDataItem(
            pen=pg.mkPen(DOWN, width=2),
            symbol="o",
            symbolSize=5,
            symbolBrush=DOWN,
            symbolPen=pg.mkPen(DOWN),
        )
        self.inversion_item.setZValue(11)
        self.inversion_item.setVisible(False)
        cp.addItem(self.inversion_item)
        self.comparison_items: dict[str, pg.PlotDataItem] = {}
        self._ghost_labels: dict[str, pg.TextItem] = {}
        self._point_labels: list[pg.TextItem] = []

        self.twist_bars = pg.BarGraphItem(x=[], height=[], width=0.1, brush=pg.mkBrush(FG_MUTED), pen=pg.mkPen(None))
        sp.addItem(self.twist_bars)
        self._zero = pg.InfiniteLine(pos=0, angle=0, movable=False, pen=pg.mkPen(FG_MUTED))
        sp.addItem(self._zero, ignoreBounds=True)
        self._bar_figures: list[pg.TextItem] = []
        # the strip's caption sits in the view's own pixel space, top-left
        self._twist_label = pg.TextItem("", color=FG_DIM, anchor=(0, 0))
        self._twist_label.setFont(_tick_font())
        self._twist_label.setParentItem(sp.getViewBox())
        self._twist_label.setPos(SPACE_MD, 0)

        # hover: a snapped guide on both plots, highlighted points, a readout
        guide_pen = pg.mkPen(FG_DIM, style=Qt.PenStyle.DotLine)
        self._guides = []
        for plot in (cp, sp):
            line = pg.InfiniteLine(angle=90, movable=False, pen=guide_pen)
            line.setZValue(20)
            line.setVisible(False)
            plot.addItem(line, ignoreBounds=True)
            self._guides.append(line)
        self._hover_dots = pg.ScatterPlotItem(size=7, pen=pg.mkPen(BG, width=1))
        self._hover_dots.setZValue(21)
        cp.addItem(self._hover_dots, ignoreBounds=True)
        self._readout = pg.TextItem(
            "", anchor=(0, 1), fill=pg.mkBrush(BG), border=pg.mkPen(BORDER_STRONG)
        )
        self._readout.setFont(_tick_font())
        self._readout.setZValue(22)
        self._readout.setVisible(False)
        cp.addItem(self._readout, ignoreBounds=True)
        # the view's own resize, not the widget's: the widget's arrives before
        # the plot has laid out its new width, and the ticks would thin for
        # the old one
        cp.getViewBox().sigResized.connect(self._apply_ranges)
        self._hover_proxies = []
        for plot in (cp, sp):
            plot.installEventFilter(self)
            self._hover_proxies.append(
                pg.SignalProxy(
                    plot.scene().sigMouseMoved,
                    rateLimit=60,
                    slot=lambda ev, p=plot: self._on_mouse(p, ev[0]),
                )
            )

    # -- public API: the panel talking (never emits) --------------------------------

    def set_countries(self, countries: Sequence[tuple[str, str]], current: str) -> None:
        """Fill the switch with ``(code, name)`` pairs and select ``current``.
        Each entry is marked with what its source has (policy rate only,
        partial)."""
        self._countries = [(str(c), str(n)) for c, n in countries]
        combo = self.country_combo
        combo.blockSignals(True)
        combo.clear()
        for code, name in self._countries:
            combo.addItem(name + self._country_suffix(code), code)
            combo.setItemData(combo.count() - 1, name, _CountryCombo.NAME_ROLE)
        combo.blockSignals(False)
        self._select_country(current)
        combo.updateGeometry()

    def set_curve(self, points, complete: bool, as_of: str, citation: str) -> None:
        """The current curve: ``[[years, rate%], ...]``, the source's
        ``complete`` flag, its own as-of date and its citation. Sets the state
        to ``curve`` (or ``partial`` for a sparse source)."""
        self._points = sorted(
            ([float(y), float(r)] for y, r in (points or []) if r is not None),
            key=lambda p: p[0],
        )
        self._complete = bool(complete)
        # comparisons look back from an ISO date; anything else still shows in
        # the header but draws no comparison rather than raising in a slot
        self._as_of_text = str(as_of or "")
        self._as_of = _iso_or_empty(self._as_of_text)
        self._citation = str(citation or "")
        if not self._points:
            self.set_state("error", MSG_NO_POINTS)
            return
        new_country = self._drawn_country is not None and self._drawn_country != self._country
        self._drawn_country = self._country
        self.set_state("curve" if self._complete else "partial")
        if new_country:
            transitions.fade_in_content(self._plots)

    def set_history(self, days) -> None:
        """``[[iso, points], ...]`` oldest → newest, from ``rates:curvehist``."""
        self._days = [[str(d), pts] for d, pts in (days or []) if pts]
        self._sync_chips()
        self._redraw()

    def set_comparisons(self, chips: Sequence[str]) -> None:
        """Select comparison chips (a restore or an undo); never emits."""
        self._comparisons = [c for c in CHIPS if c in set(chips or [])]
        self._sync_chips()
        self._redraw()

    def set_state(self, kind: str, message: str = "") -> None:
        """``curve`` | ``partial`` | ``none`` | ``loading`` | ``error``. The
        first two draw; the rest replace the plots with a message."""
        if kind not in STATES:
            raise ValueError(f"unknown curve state: {kind!r}")
        self._state = kind
        if kind == "none":
            self._message = MSG_POLICY_ONLY
        elif kind == "error":
            self._message = message or "Curve unavailable"
        else:
            self._message = ""
        self._apply_state()

    # -- public API: reading back ---------------------------------------------------

    def comparisons(self) -> list[str]:
        return list(self._comparisons)

    def country(self) -> str:
        return self._country

    def state(self) -> str:
        return self._state

    def message(self) -> str:
        return self._message

    def plots_visible(self) -> bool:
        return self._body.currentWidget() is self._plots

    def chip(self, name: str) -> QPushButton:
        return self._chips[name]

    def source_text(self) -> str:
        return self.source_label.text()

    def partial_text(self) -> str:
        return self.partial_label.text()

    def twist_label_text(self) -> str:
        return self._twist_label.toPlainText()

    def country_menu_texts(self) -> list[str]:
        return [self.country_combo.itemText(i) for i in range(self.country_combo.count())]

    def country_button_text(self) -> str:
        return self.country_combo.currentData(_CountryCombo.NAME_ROLE) or ""

    def tick_labels(self) -> list[tuple[float, str]]:
        """(x, label) for every tenor tick on the maturity axis, in x order;
        a tenor whose label didn't fit has ``""``."""
        return list(self._ticks)

    def point_labels(self) -> list[pg.TextItem]:
        """The printed readings of a partial curve (empty for a full one)."""
        return list(self._point_labels)

    def bar_figures(self) -> list[pg.TextItem]:
        """The bp figures on the twist bars (empty when the bars are too tight)."""
        return list(self._bar_figures)

    def pick_country(self, code: str) -> None:
        """Act as the user picking ``code`` in the switch (emits)."""
        i = self.country_combo.findData(code)
        if i >= 0:
            self.country_combo.setCurrentIndex(i)
            self._on_country_activated(i)

    # -- hover --------------------------------------------------------------------

    def _hover_parts(self, years: float):
        """(tenor years, rate, [(chip, bp | None)]) at the observed tenor
        nearest ``years`` on the maturity axis, else None."""
        if not self._points or self._state not in ("curve", "partial"):
            return None
        x = axis_x(max(years, 0.0))
        y, rate = min(self._points, key=lambda p: abs(axis_x(p[0]) - x))
        deltas = []
        for chip in CHIPS:
            pts = self._shown.get(chip)
            if pts is None:
                continue
            then = rate_at(pts, y)
            deltas.append((chip, None if then is None else (rate - then) * 100.0))
        return y, rate, deltas

    def hover_text(self, years: float) -> str:
        """'10Y  4.218%  Δ1M +12.3bp' for the tenor nearest ``years``."""
        parts = self._hover_parts(years)
        if parts is None:
            return ""
        y, rate, deltas = parts
        out = [tenor_label(y), f"{rate:.3f}%"]
        for chip, bp in deltas:
            out.append(f"Δ{chip} {NULL_GLYPH if bp is None else signed(bp, 1) + 'bp'}")
        return "  ".join(out)

    def _hover_html(self, parts) -> str:
        y, rate, deltas = parts
        bits = [
            f'<span style="color:{FG_DIM}">{tenor_label(y)}</span>',
            f'<span style="color:{LABEL}">{rate:.3f}%</span>',
        ]
        for chip, bp in deltas:
            if bp is None:
                val, color = NULL_GLYPH, FG_MUTED
            else:
                val, color = signed(bp, 1) + "bp", UP if bp > 0 else DOWN if bp < 0 else FG_DIM
            bits.append(
                f'<span style="color:{FG_MUTED}">Δ{chip}</span>&nbsp;<span style="color:{color}">{val}</span>'
            )
        return "&nbsp;&nbsp;".join(bits)

    def _on_mouse(self, plot: pg.PlotWidget, pos) -> None:
        if not plot.sceneBoundingRect().contains(pos) or not self.plots_visible():
            self._hide_hover()
            return
        x = plot.getViewBox().mapSceneToView(pos).x()
        parts = self._hover_parts(max(x, 0.0) ** 2)
        if parts is None:
            self._hide_hover()
            return
        self._show_hover(parts)

    def _show_hover(self, parts) -> None:
        y, rate, _deltas = parts
        self._hover_years = y
        x = axis_x(y)
        for line in self._guides:
            line.setPos(x)
            line.setVisible(True)
        spots = [{"pos": (x, rate), "brush": pg.mkBrush(LABEL)}]
        for chip, pts in self._shown.items():
            then = rate_at(pts, y)
            if then is not None:
                spots.append({"pos": (x, then), "brush": pg.mkBrush(_alpha(LABEL, _GHOST_ALPHA[chip] + 0.2))})
        self._hover_dots.setData(spots)
        self._readout.setHtml(self._hover_html(parts))
        # sit beside the point, on whichever side has the room
        (x0, x1), (y0, y1) = self.curve_plot.getViewBox().viewRange()
        right = x > (x0 + x1) / 2
        high = rate > (y0 + y1) / 2
        self._readout.setAnchor((1.0 if right else 0.0, 0.0 if high else 1.0))
        self._readout.setPos(x, rate)
        self._readout.setVisible(True)

    def _hide_hover(self) -> None:
        self._hover_years = None
        for line in self._guides:
            line.setVisible(False)
        self._hover_dots.setData([])
        self._readout.setVisible(False)

    # -- user actions (emit) -------------------------------------------------------

    def _on_chip_clicked(self) -> None:
        before = set(self._shown)
        # a disabled chip can't be clicked, so it keeps the user's choice: when
        # richer history arrives it comes back on by itself
        self._comparisons = [
            c for c in CHIPS
            if (self._chips[c].isChecked() if self._chips[c].isEnabled() else c in self._comparisons)
        ]
        self._redraw()
        for chip in set(self._shown) - before:
            self._fade_in_ghost(chip)
        self.comparisons_changed.emit(list(self._comparisons))

    def _on_country_activated(self, index: int) -> None:
        code = self.country_combo.itemData(index)
        if not code or code == self._country:
            return
        self._country = code
        self.country_combo.update()
        self.country_changed.emit(code)

    # -- internals -------------------------------------------------------------------

    def _country_suffix(self, code: str) -> str:
        meta = rates_meta.by_code(code)
        if meta is None:
            return ""
        if meta.curve_source == "none":
            return SUFFIX_POLICY_ONLY
        if meta.curve_source == "fred":
            return SUFFIX_PARTIAL  # two FRED points, never a whole curve
        return ""

    def _select_country(self, code: str) -> None:
        i = self.country_combo.findData(code)
        if i < 0 and self.country_combo.count():
            i = 0  # an unknown code: the switch and the view agree on the first
        self._country = self.country_combo.itemData(i) if i >= 0 else ""
        if i >= 0:
            self.country_combo.blockSignals(True)
            self.country_combo.setCurrentIndex(i)
            self.country_combo.blockSignals(False)

    def _apply_state(self) -> None:
        drawing = self._state in ("curve", "partial")
        self._hide_hover()
        if drawing:
            self._empty.set_loading(False)
            self._empty.set_shown(False)
            self._body.setCurrentWidget(self._plots)
        else:
            if self._state == "none":
                title, _, hint = MSG_POLICY_ONLY.partition(" — ")
                self._empty.set_text(title, hint[:1].upper() + hint[1:])
            elif self._state == "error":
                self._empty.set_text("Curve unavailable", self._message)
            self._empty.set_loading(self._state == "loading")
            self._body.setCurrentWidget(self._message_page)
            self._empty.setGeometry(self._message_page.rect())
            self._empty.set_shown(True)
        self._chip_row.setVisible(self._state != "none")
        self._sync_header()
        self._sync_chips()
        self._redraw()

    def _sync_header(self) -> None:
        drawing = self._state in ("curve", "partial")
        if drawing and self._citation:
            self.source_label.setText(
                f"{self._citation} · {self._as_of_text}" if self._as_of_text else self._citation
            )
        elif self._state == "none":
            meta = rates_meta.by_code(self._country)
            self.source_label.setText(meta.citation if meta is not None else "")
        else:
            self.source_label.setText("")
        self.source_label.setVisible(bool(self.source_label.text()))
        partial = drawing and self._state == "partial"
        n = len(self._points)
        self.partial_label.setText(f"partial — {n} point{'s' if n != 1 else ''}" if partial else "")
        self.partial_label.setVisible(partial)

    def _chip_reason(self, chip: str) -> Optional[str]:
        """Why ``chip`` can't be drawn for this source, else None."""
        if len(self._days) == 1:
            return MSG_NO_HISTORY
        if self._days and self._as_of:
            if comparison_date(self._as_of, chip) < self._days[0][0]:
                return MSG_NO_HISTORY_THAT_FAR
        return None

    def _sync_chips(self) -> None:
        for chip, b in self._chips.items():
            reason = self._chip_reason(chip)
            b.setEnabled(reason is None)
            # checked only while it draws: a lit chip with no line is a lie
            b.setChecked(reason is None and chip in self._comparisons)
            b.setToolTip(reason or f"Compare with the curve {chip[:-1]} {self._chip_unit(chip)} ago")

    @staticmethod
    def _chip_unit(chip: str) -> str:
        return {"W": "week", "M": "month", "Y": "year"}[chip[-1]]

    def _comparison_points(self, chip: str):
        if self._chip_reason(chip) is not None or not self._days or not self._as_of:
            return None
        return point_on_or_before(self._days, comparison_date(self._as_of, chip))

    def _prior_day_points(self):
        if not self._as_of:
            return None
        before = (date.fromisoformat(self._as_of) - timedelta(days=1)).isoformat()
        return point_on_or_before(self._days, before)

    def _redraw(self) -> None:
        drawing = self._state in ("curve", "partial") and bool(self._points)
        cp = self.curve_plot

        # current curve
        xs = [axis_x(y) for y, _ in self._points] if drawing else []
        ys = [r for _, r in self._points] if drawing else []
        if self._state == "partial":
            pen = pg.mkPen(LABEL, width=2, style=Qt.PenStyle.DashLine)
            size = 7
        else:
            pen = pg.mkPen(LABEL, width=2)
            size = 5
        self.curve_item.setData(
            xs, ys, pen=pen, symbol="o", symbolSize=size, symbolBrush=LABEL, symbolPen=pg.mkPen(LABEL)
        )
        self._draw_inversion(drawing)
        self._draw_point_labels(drawing and self._state == "partial")

        # comparisons
        self._shown = {}
        if drawing:
            for chip in self._comparisons:
                pts = self._comparison_points(chip)
                if pts:
                    self._shown[chip] = sorted(pts, key=lambda p: p[0])
        for chip in list(self.comparison_items):
            if chip not in self._shown:
                cp.removeItem(self.comparison_items.pop(chip))
                cp.removeItem(self._ghost_labels.pop(chip))
                for anim in self._fades.pop(chip, []):
                    anim.stop()
        for chip, pts in self._shown.items():
            color = _alpha(LABEL, _GHOST_ALPHA[chip])
            item = self.comparison_items.get(chip)
            if item is None:
                item = pg.PlotDataItem()
                item.setZValue(5 - CHIPS.index(chip))
                cp.addItem(item)
                self.comparison_items[chip] = item
                label = pg.TextItem(chip, color=color, anchor=(0, 0.5))
                label.setFont(_tick_font())
                cp.addItem(label, ignoreBounds=True)
                self._ghost_labels[chip] = label
            item.setData(
                [axis_x(y) for y, _ in pts], [r for _, r in pts], pen=pg.mkPen(color, width=1.25)
            )
            last_y, last_r = pts[-1]
            self._ghost_labels[chip].setPos(axis_x(last_y), last_r)

        # twist strip
        base_chip = next((c for c in self._comparisons if c in self._shown), None)
        if not drawing:
            base, caption = None, ""
        elif base_chip is not None:
            base, caption = self._shown[base_chip], f"Δ vs {base_chip} (bp)"
        else:
            base, caption = self._prior_day_points(), "Δ vs prior day (bp)"
        self._twist = bp_changes(self._points, base) if base else []
        self._twist_label.setText(caption if self._twist else "")
        self._draw_twist()

        # axes
        tenors = {round(y, 4) for y, _ in self._points} if drawing else set()
        for pts in self._shown.values():
            tenors.update(round(y, 4) for y, _ in pts)
        self._tenors = sorted(tenors)
        has_strip = bool(self._twist)
        self.strip_plot.setVisible(has_strip)
        # the tenor labels go under whichever plot is at the bottom
        self.curve_plot.getAxis("bottom").setStyle(showValues=not has_strip)
        self._apply_ranges()
        if self._hover_years is not None:
            parts = self._hover_parts(self._hover_years)
            if parts:
                self._show_hover(parts)
            else:
                self._hide_hover()

    def _draw_point_labels(self, on: bool) -> None:
        """A sparse curve is a few readings, not a shape: print each one."""
        for t in self._point_labels:
            self.curve_plot.removeItem(t)
        self._point_labels = []
        if not on:
            return
        last = len(self._points) - 1
        for i, (y, r) in enumerate(self._points):
            # the outer readings align inward, so neither is cut by the edge
            ax = 0.0 if i == 0 else 1.0 if i == last else 0.5
            t = pg.TextItem(f"{r:.2f}%", color=FG_DIM, anchor=(ax, 1.4))
            t.setFont(_tick_font())
            t.setPos(axis_x(y), r)
            self.curve_plot.addItem(t, ignoreBounds=True)
            self._point_labels.append(t)

    def _draw_inversion(self, drawing: bool) -> None:
        two = rate_at(self._points, 2.0) if drawing else None
        ten = rate_at(self._points, 10.0) if drawing else None
        if two is None or ten is None or two <= ten:
            self.inversion_item.setData([], [])
            self.inversion_item.setVisible(False)
            return
        seg = [p for p in self._points if 2.0 - 0.05 <= p[0] <= 10.0 + 0.05]
        self.inversion_item.setData([axis_x(y) for y, _ in seg], [r for _, r in seg])
        self.inversion_item.setVisible(True)

    def _draw_twist(self) -> None:
        xs = [axis_x(y) for y, _ in self._twist]
        hs = [bp for _, bp in self._twist]
        brushes = [pg.mkBrush(UP if h > 0 else DOWN if h < 0 else FG_MUTED) for h in hs]
        self.twist_bars.setOpts(x=xs, height=hs, width=self._bar_width(xs), brushes=brushes)
        for t in self._bar_figures:
            self.strip_plot.removeItem(t)
        self._bar_figures = []
        if not hs:
            return
        # zero sits where the data needs it: an all-positive move gets the
        # height above the line, with headroom for the figures and caption
        top, bot = max(max(hs), 0.0), min(min(hs), 0.0)
        span = max(top - bot, 5.0)
        above = span * (0.75 if top > 0 else 0.45)
        below = span * (0.35 if bot < 0 else 0.12)
        self.strip_plot.getViewBox().setYRange(bot - below, top + above, padding=0)

    @staticmethod
    def _bar_width(xs: list[float]) -> float:
        if len(xs) < 2:
            return 0.2
        gap = min(b - a for a, b in zip(xs, xs[1:]))
        return max(min(gap * 0.6, 0.35), 0.02)

    def _plot_px(self) -> float:
        return max(self.curve_plot.getViewBox().width(), 1.0)

    def _apply_ranges(self) -> None:
        if not self._tenors:
            self._ticks = []
            self.strip_plot.getAxis("bottom").setTicks([[]])
            self.curve_plot.getAxis("bottom").setTicks([[]])
            return
        x0, x1 = axis_x(self._tenors[0]), axis_x(self._tenors[-1])
        span = (x1 - x0) or 1.0
        right_px = _EDGE_PX + (_GHOST_LABEL_PX if self._shown else 0)
        avail = max(self._plot_px() - _EDGE_PX - right_px, 40.0)
        upp = span / avail  # x units per pixel
        self.curve_plot.getViewBox().setXRange(x0 - _EDGE_PX * upp, x1 + right_px * upp, padding=0)
        for chip, label in self._ghost_labels.items():
            pts = self._shown[chip]
            label.setPos(axis_x(pts[-1][0]) + 4 * upp, pts[-1][1])

        ys = [r for _, r in self._points] + [r for pts in self._shown.values() for _, r in pts]
        lo, hi = min(ys), max(ys)
        pad = max((hi - lo) * 0.14, 0.08)
        self.curve_plot.getViewBox().setYRange(lo - pad, hi + pad, padding=0)

        ticks = self._thin_ticks(1.0 / upp)
        self._ticks = ticks
        self.curve_plot.getAxis("bottom").setTicks([ticks])
        self.strip_plot.getAxis("bottom").setTicks([ticks])
        self._place_bar_figures(1.0 / upp)

    def _thin_ticks(self, px_per_unit: float) -> list[tuple[float, str]]:
        """A tick at every tenor; labels by priority while they don't collide."""
        fm = QFontMetrics(_tick_font())
        rank = {y: i for i, y in enumerate(_LABEL_PRIORITY)}
        order = sorted(self._tenors, key=lambda y: (rank.get(round(y, 2), len(rank)), y))
        taken: list[tuple[float, float]] = []
        keep: set[float] = set()
        for y in order:
            c = axis_x(y) * px_per_unit
            half = (fm.horizontalAdvance(tenor_label(y)) + _TICK_GAP_PX) / 2
            if all(c + half <= a or c - half >= b for a, b in taken):
                taken.append((c - half, c + half))
                keep.add(y)
        return [(axis_x(y), tenor_label(y) if y in keep else "") for y in self._tenors]

    def _place_bar_figures(self, px_per_unit: float) -> None:
        for t in self._bar_figures:
            self.strip_plot.removeItem(t)
        self._bar_figures = []
        xs = [axis_x(y) for y, _ in self._twist]
        if len(xs) < 1:
            return
        min_gap_px = min((b - a for a, b in zip(xs, xs[1:])), default=1.0) * px_per_unit
        if min_gap_px < _BAR_FIGURE_PX:
            return
        font = QFont(MONO_FONT)
        font.setPixelSize(FONT_XS)
        for x, (_y, bp) in zip(xs, self._twist):
            t = pg.TextItem(f"{bp:+.0f}", color=FG_DIM, anchor=(0.5, 1.0 if bp >= 0 else 0.0))
            t.setFont(font)
            t.setPos(x, bp)
            self.strip_plot.addItem(t, ignoreBounds=True)
            self._bar_figures.append(t)

    def _fade_in_ghost(self, chip: str) -> None:
        """A comparison the user just switched on arrives; it doesn't pop."""
        items = [self.comparison_items.get(chip), self._ghost_labels.get(chip)]
        if not motion.animations_enabled():
            return
        self._fades[chip] = []
        for item in items:
            if item is None:
                continue
            anim = QPropertyAnimation(item, b"opacity", item)
            anim.setDuration(motion.duration(motion.OVERLAY_MS))
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve(motion.EASE_OUT))
            item.setOpacity(0.0)
            anim.start()
            self._fades[chip].append(anim)

    # -- Qt -------------------------------------------------------------------------

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        et = event.type()
        if obj is self._message_page and et == QEvent.Type.Resize:
            self._empty.setGeometry(self._message_page.rect())
        elif obj in (self.curve_plot, self.strip_plot):
            if et == QEvent.Type.Leave:
                self._hide_hover()
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._apply_ranges()
