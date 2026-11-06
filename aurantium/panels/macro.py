"""Macro / Rates panel — what the rate and macro backdrop is today, and
whether anything has shifted.

Top to bottom:

* a **gauge strip** (``GaugeStrip``): the policy rate, the 10Y, 2s10s, the
  dollar, VIX, MOVE, oil and gold by default, each with today's change and a
  one-month sparkline. The three rate gauges are *country-relative* (code
  ``*``): they follow the panel's country. Everything else is global;
* the **yield curve** (``CurveView``) for the panel's country, with 1W / 1M /
  1Y comparisons and a twist strip. Its country switch is the panel's;
* **spreads**: 2s10s and 3m10y for that country, with a one-year sparkline and
  a plain-language state (``macro_model.spread_state``);
* **positioning** (``PositioningBars``): large speculators' net CFTC position
  per market, placed in its own ~2-year range. Clicking a market opens the
  Speculator Positioning panel on it in this panel's link group.

There is no edit dialog: gauges and markets are added with **+**, replaced or
removed from the right-click menu and dragged to reorder, all in place, and
every edit (and a country switch) is one Ctrl+Z away.

The panel never fetches: ``rates:policy``, ``rates:curve:<CC>``,
``rates:curvehist:<CC>``, ``quote:*`` / ``history:*``, ``fred:*`` and
``cftc:*`` topics only, diffed on every change so a switch never re-delivers
what didn't change.
"""

from __future__ import annotations

import math
import os
from datetime import date
from typing import Any, Optional, Sequence

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import rates_meta
from ..commodities_meta import COMMODITIES, by_cftc_market
from .cot_history import market_symbol
from ..components import (
    FRED_ENTRIES,
    FX_ENTRIES,
    INDEX_ENTRIES,
    RATES_ENTRIES,
    TENOR_ENTRIES,  # noqa: F401  (kept public: the symbol completer reads it)
    CatalogEntry,
    CurveView,
    DEFAULT_GAUGES,
    Gauge,
    GaugeStrip,
    PositioningBars,
    open_add_picker,
)
from ..components.curve_view import CHIPS
from ..components.fmt import NULL_GLYPH, num, signed_pct
from ..components.symbol_catalog import commodity_entries
from ..guide import PanelGuide
from ..macro_model import (
    SPREAD_REASON_MISSING,
    SPREAD_STATE_RULES,
    comparison_date,
    percentile,
    rate_at,
    spread_bp,
    spread_reason,
    spread_state,
    tenor_label,
)
from ..panel import Panel, register_panel
from ..theme import (
    BG_ALT,
    BORDER_STRONG,
    DOWN,
    FG_DIM,
    FG_MUTED,
    FONT_LG,
    FONT_SM,
    LABEL,
    MONO_FONT,
    RADIUS_MD,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    UI_FONT,
    WEIGHT_MEDIUM,
)
from ..undo import UndoStack

DEFAULT_COUNTRY = "US"

# (label, CFTC market key) — the five Koji commodities by default
DEFAULT_CFTC = [
    ["Gold", "gold"],
    ["Silver", "silver"],
    ["Copper", "copper"],
    ["Brent Crude", "brent"],
    ["Henry Hub NatGas", "natgas"],
]

#: every market the CFTC provider serves: each commodity with a COT market,
#: then the financial ones (the Speculator Positioning panel covers them all)
_MARKET_CHOICES = [
    (c.cftc_market, c.label) for c in COMMODITIES if c.cftc_market
] + [
    ("sp500", "S&P 500 (E-mini)"),
    ("bitcoin", "Bitcoin"),
    ("euro_fx", "Euro FX"),
]
_MARKET_LABELS = dict(_MARKET_CHOICES)

#: picker entries for the positioning **+** (code = CFTC market key)
_MARKET_ENTRIES = [
    CatalogEntry(label, value, "quote", "CFTC") for value, label in _MARKET_CHOICES
]

#: legacy Yahoo yield indices → the curve gauge that replaces them
_LEGACY_CURVE = {"^TNX": "US:10", "^FVX": "US:5", "^TYX": "US:30", "^IRX": "US:0.25"}

#: the two spreads the Spreads row reads for the panel's country
_SPREADS = ((2.0, 10.0), (0.25, 10.0))

#: how a desk names a country's benchmark bonds ("Bund 10Y", "JGB 10Y")
_BOND_NAME = {"US": "US", "XM": "Bund", "JP": "JGB", "GB": "Gilt"}
#: and its central bank, for the policy-rate gauge
_BANK_NAME = {
    "US": "Fed", "XM": "ECB", "JP": "BoJ", "GB": "BoE", "CA": "BoC", "AU": "RBA",
    "CH": "SNB", "SE": "Riksbank", "NO": "Norges Bank", "NZ": "RBNZ", "KR": "BoK",
    "IN": "RBI", "BR": "BCB", "MX": "Banxico", "ZA": "SARB", "CN": "PBoC",
}

#: the country-relative gauges, offered in the picker so a removed default
#: can come back
_HERE_ENTRIES = [
    CatalogEntry("Policy rate (panel's country)", "*", "policy", "Rates",
                 "central bank policy rate fed ecb boj interest"),
    CatalogEntry("10Y yield (panel's country)", "*:10", "curve", "Rates",
                 "10 year government bond yield benchmark"),
    CatalogEntry("2s10s spread (panel's country)", "*:2-10", "curve", "Rates",
                 "curve slope inversion 2 10"),
    CatalogEntry("3m10y spread (panel's country)", "*:0.25-10", "curve", "Rates",
                 "curve slope inversion bill 3 month 10"),
]
#: the plain name a country-relative gauge keeps (the tile shows it as
#: "US 10Y", "JGB 2s10s", "Fed rate" — never the picker's description)
_HERE_LABELS = {"*": "Policy rate", "*:10": tenor_label(10), "*:2-10": "2s10s",
                "*:0.25-10": "3m10y"}

#: market gauges the shared catalog lacks
_EXTRA_QUOTES = [
    CatalogEntry("MOVE (bond volatility)", "^MOVE", "quote", "Rates",
                 "move index treasury options implied volatility"),
]

_SPARK_DAYS = "1M"  # gauge sparklines: one month


# -- small helpers --------------------------------------------------------------


def _years(text: str) -> Optional[float]:
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v >= 0 else None


def _parse_curve_code(code: str):
    """``"CC:10"`` → (CC, 10.0, None); ``"CC:2-10"`` → (CC, 2.0, 10.0); else None."""
    cc, sep, rest = str(code or "").partition(":")
    if not sep or not cc:
        return None
    if "-" in rest:
        short, _, long_ = rest.partition("-")
        s, lo = _years(short), _years(long_)
        if s is None or lo is None:
            return None
        return cc.upper() if cc != "*" else "*", s, lo
    y = _years(rest)
    return None if y is None else (cc.upper() if cc != "*" else "*", y, None)


def spread_name(short_y: float, long_y: float) -> str:
    """Desk names: 2s10s, 5s30s, 3m10y."""
    if short_y < 1:
        return f"{round(short_y * 12)}m{long_y:g}y"
    return f"{short_y:g}s{long_y:g}s"


def bond_name(cc: str) -> str:
    if cc in _BOND_NAME:
        return _BOND_NAME[cc]
    meta = rates_meta.by_code(cc)
    return meta.label if meta is not None else cc


def bank_name(cc: str) -> str:
    return _BANK_NAME.get(cc, bond_name(cc))


def _period(text: str) -> tuple[Optional[date], bool]:
    """'2026-11-05' → (date, True); BIS's monthly '2026-10' → (1 Oct, False)."""
    t = str(text or "").strip()[:10]
    try:
        if len(t) == 7:
            return date(int(t[:4]), int(t[5:7]), 1), False
        return date.fromisoformat(t), True
    except (TypeError, ValueError):
        return None, False


def _short_date(iso: str, today: Optional[date] = None) -> str:
    """'2026-11-05' → '5 Nov' this year, '5 Nov 2025' otherwise; a monthly
    period reads 'Oct' / 'Oct 2025'."""
    d, has_day = _period(iso)
    if d is None:
        return str(iso or "")
    today = today or date.today()
    text = f"{d.day} {d.strftime('%b')}" if has_day else d.strftime("%b")
    return text if d.year == today.year else f"{text} {d.year}"


def _month_year(iso: str) -> str:
    d, _ = _period(iso)
    return d.strftime("%b %Y") if d is not None else str(iso or "")


def _month_after(period: str) -> str:
    """The month after a BIS period: its last month at the old rate is
    ``last_change``, so the move landed in the next one."""
    d, _ = _period(period)
    if d is None:
        return str(period or "")
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return date(y, m, 1).strftime("%b %Y")


def _bp(value: float) -> str:
    return f"{value:+.0f}bp"


def _f(value) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# -- the Spreads row ---------------------------------------------------------------


class _SpreadReadout(QWidget):
    """One spread: its name, the value in bp, its state in words, and a
    one-year sparkline with the zero line marked when the year crossed it.
    ``None`` shows "—" and says why on hover."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = ""
        self._value: Optional[float] = None
        self._state = ""
        self._series: tuple[float, ...] = ()
        self._reason = ""
        self._loading = True
        self._f_name = QFont(UI_FONT)
        self._f_name.setPixelSize(FONT_SM)
        self._f_value = QFont(MONO_FONT)
        self._f_value.setPixelSize(FONT_LG)
        self._f_value.setWeight(QFont.Weight(WEIGHT_MEDIUM))
        self._f_state = QFont(UI_FONT)
        self._f_state.setPixelSize(FONT_SM)
        self._fm_name = QFontMetrics(self._f_name)
        self._fm_value = QFontMetrics(self._f_value)
        self._fm_state = QFontMetrics(self._f_state)
        self._h = 2 * SPACE_MD + self._fm_name.height() + SPACE_XS + self._fm_value.height()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._h)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(16 * SPACE_XL, self._h)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(6 * SPACE_XL, self._h)

    # -- the panel talking ------------------------------------------------

    def set_loading(self, name: str) -> None:
        self._name, self._loading = name, True
        self._value, self._state, self._series, self._reason = None, "", (), ""
        self._sync_tip()
        self.update()

    def set_spread(self, name: str, value: Optional[float], state: str,
                   series: Sequence[float], reason: str = "") -> None:
        self._name, self._loading = name, False
        self._value = value
        self._state = state if value is not None else ""
        self._series = tuple(v for v in (series or ()) if _f(v) is not None)
        self._reason = reason if value is None else ""
        self._sync_tip()
        self.update()

    # -- reading back (tests) -----------------------------------------------

    def name(self) -> str:
        return self._name

    def value_text(self) -> str:
        if self._loading:
            return "…"
        return NULL_GLYPH if self._value is None else f"{self._value:+.0f} bp"

    def state(self) -> str:
        return self._state

    def series(self) -> tuple[float, ...]:
        return self._series

    def _sync_tip(self) -> None:
        if self._loading:
            tip = f"{self._name}: waiting for the curve"
        elif self._value is None:
            tip = f"{self._name}: {self._reason or 'not available'}"
        else:
            tip = (f"{self._name}: {self._value:+.1f} bp, {self._state}\n"
                   f"Sparkline: the last year\n{SPREAD_STATE_RULES}")
        self.setToolTip(tip)

    # -- painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor(BORDER_STRONG)))
            p.setBrush(QColor(BG_ALT))
            p.drawRoundedRect(rect, RADIUS_MD, RADIUS_MD)
            inner = rect.adjusted(SPACE_LG, SPACE_MD, -SPACE_LG, -SPACE_MD)
            text_w = max(self._fm_value.horizontalAdvance("+888 bp"),
                         self._fm_name.horizontalAdvance(self._name)) + SPACE_LG
            text_w = min(text_w, inner.width() * 0.55)
            p.setFont(self._f_name)
            p.setPen(QColor(FG_DIM))
            name_h = self._fm_name.height()
            p.drawText(QRectF(inner.left(), inner.top(), text_w, name_h),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._fm_name.elidedText(self._name, Qt.TextElideMode.ElideRight, int(text_w)))
            y_val = inner.top() + name_h + SPACE_XS
            p.setFont(self._f_value)
            p.setPen(QColor(LABEL if self._value is not None else FG_MUTED))
            p.drawText(QRectF(inner.left(), y_val, text_w, self._fm_value.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.value_text())
            # the state rides the name row's right edge, the spark under it
            right = QRectF(inner.left() + text_w, inner.top(), inner.width() - text_w, inner.height())
            if self._state:
                p.setFont(self._f_state)
                p.setPen(QColor(DOWN if self._state == "inverted" else FG_DIM))
                p.drawText(QRectF(right.left(), right.top(), right.width(), name_h),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._state)
            spark = QRectF(right.left(), y_val, right.width(), self._fm_value.height())
            self._paint_spark(p, spark)
        finally:
            p.end()

    def _paint_spark(self, p: QPainter, rect: QRectF) -> None:
        values = self._series
        if len(values) < 2 or rect.width() < SPACE_XL:
            return
        lo, hi = min(values), max(values)
        span = hi - lo
        top, height = rect.top() + SPACE_XS, rect.height() - 2 * SPACE_XS
        step = (rect.width() - SPACE_XS) / (len(values) - 1)

        def y_of(v: float) -> float:
            return top + height / 2 if span <= 0 else top + (hi - v) / span * height

        if lo < 0 < hi:  # the year crossed zero: show where inversion starts
            zero = QPen(QColor(FG_MUTED))
            zero.setStyle(Qt.PenStyle.DotLine)
            p.setPen(zero)
            p.drawLine(QPointF(rect.left(), y_of(0.0)), QPointF(rect.right(), y_of(0.0)))
        path = QPainterPath(QPointF(rect.left(), y_of(values[0])))
        for k, v in enumerate(values[1:], 1):
            path.lineTo(rect.left() + k * step, y_of(v))
        pen = QPen(QColor(FG_DIM))
        pen.setWidthF(1.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(DOWN if values[-1] < 0 else LABEL))
        p.drawEllipse(QPointF(rect.left() + (len(values) - 1) * step, y_of(values[-1])),
                      SPACE_XS, SPACE_XS)


class _CurveHost(QWidget):
    """Holds the curve and answers for its height.

    The curve's own layout asks for pyqtgraph's tall default plot height, and
    inside the panel's scroll area that pushed the spreads and positioning
    below the fold even on a tall panel. A layout-less host is asked through
    its own ``heightForWidth`` instead: the curve's header (which wraps when
    narrow) plus a fixed share for the plots, then any height left over."""

    _PLOTS_PX = 34 * SPACE_LG

    def __init__(self, curve: CurveView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.curve = curve
        curve.setParent(self)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(30 * SPACE_LG)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        header = self.curve.layout().itemAt(0) if self.curve.layout() is not None else None
        if header is None:
            head = 0
        elif header.hasHeightForWidth():
            head = header.heightForWidth(width)
        else:
            head = header.sizeHint().height()
        return head + SPACE_SM + self._PLOTS_PX

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(60 * SPACE_LG, self.heightForWidth(60 * SPACE_LG))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        # never the reason a narrow panel scrolls sideways: below the curve's
        # own minimum its header clips at the right instead
        return QSize(10 * SPACE_XL, 30 * SPACE_LG)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.curve.setGeometry(self.rect())


# -- the panel ---------------------------------------------------------------------


@register_panel(id="macro", title="Macro / Rates", category="Analytics")
class MacroPanel(Panel):
    GUIDE = PanelGuide(
        purpose="The rate and macro backdrop at a glance: key gauges, a country's "
        "whole government yield curve (the US Treasury curve by default) against "
        "its past, the curve's spreads and CFTC speculator positioning.",
        links="Click a market gauge (dollar, VIX, oil, gold…) to send its symbol to "
        "this link group. Click a positioning market to send its symbol too (gold's "
        "futures, the S&P 500 E-mini, Bitcoin, EUR/USD) and open Speculator "
        "Positioning on it in the same group — floating if the group has none.",
        interactions=(
            "**Country** (on the curve) sets the panel's country: the curve, the "
            "policy-rate, 10Y and 2s10s gauges and the spreads follow it; market "
            "gauges stay put. Countries without a free curve say so.",
            "**1W**, **1M** and **1Y** on the curve overlay the curve that long "
            "ago; the bars under it give each maturity's change in bp versus the "
            "first one selected (versus yesterday with none). Hover for any "
            "tenor's yield and changes.",
            "Gauges: **+** adds one by plain name (“dollar”, “Bund 10Y”, FRED "
            "series when a FRED key is set), right-click → **Replace with…** or "
            "**Remove**, drag to reorder.",
            "Positioning: **+** adds a market, right-click → **Remove**, drag to "
            "reorder. Every edit and country switch undoes with Ctrl+Z.",
            "Adding a gauge that is already on the strip outlines the one there "
            "instead of adding a twin.",
            "The header gives each source's own latest date (BIS, the curve's "
            "publisher, CFTC, FRED) — they publish on different days — and cites "
            "BIS for the policy rates.",
        ),
        fields=(
            ("Policy rate", "The central bank's rate (BIS), with its last move"),
            ("FRED gauge", "The latest release, the period it covers and its change "
             "versus the previous release — not a daily move; the line is the last 12 "
             "releases"),
            ("2s10s / 3m10y", "10-year yield minus the 2-year / 3-month, in basis points"),
            ("Spread state", SPREAD_STATE_RULES),
            ("MOVE", "Expected Treasury volatility from options — the bond market's VIX"),
            ("Positioning bar", "Large speculators' net futures position: right and "
             "green when net long, left and red when net short"),
            ("Range marker", "Where this week's position sits in its own ~2-year "
             "range: left end = most short, right end = most long"),
            ("W/W", "Change in that position since the previous weekly report"),
        ),
        keywords=(
            "bonds", "treasuries", "treasury", "yields", "yield curve", "interest rates",
            "rates", "Fed", "FOMC", "inflation", "CPI", "economy", "economic data",
            "GDP", "2s10s", "inversion", "CFTC", "positioning", "VIX", "dollar",
            "MOVE", "bond volatility", "policy rate", "central bank", "gilts",
            "Bunds", "JGB",
        ),
    )

    def build(self) -> None:
        self._country = DEFAULT_COUNTRY
        self._comparisons: list[str] = []
        self._gauges: list[Gauge] = list(DEFAULT_GAUGES)
        self._cftc: list[list[str]] = [list(r) for r in DEFAULT_CFTC]

        # data, as the topics deliver it
        self._policy: Optional[dict] = None
        self._policy_error = ""
        self._curves: dict[str, dict] = {}
        self._curve_errors: dict[str, str] = {}
        self._hists: dict[str, list] = {}
        self._hist_sources: dict[str, list] = {}
        self._quotes: dict[str, dict] = {}
        self._quote_errors: dict[str, str] = {}
        self._closes: dict[str, list[float]] = {}
        self._fred: dict[str, dict] = {}
        self._fred_errors: dict[str, str] = {}
        self._cot: dict[str, dict] = {}
        #: topics wait for restore() or the first show, so a layout restored
        #: to another country never fetches the default one first
        self._live = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # vertical only: the gauges wrap and the bars reflow to the width
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_LG)
        self._scroll = scroll

        self.gauge_strip = GaugeStrip(body)
        self.gauge_strip.set_gauges(self._gauges)
        self.gauge_strip.gauges_changed.connect(self._on_gauges_edited)
        self.gauge_strip.add_requested.connect(self._on_add_gauge)
        self.gauge_strip.replace_requested.connect(self._on_replace_gauge)
        self.gauge_strip.gauge_clicked.connect(self._on_gauge_clicked)
        col.addWidget(self.gauge_strip)

        self.curve_view = CurveView()
        self.curve_view.set_countries([(c.code, c.label) for c in rates_meta.COUNTRIES],
                                      self._country)
        self.curve_view.set_comparisons(self.curve_view.comparisons())
        self._comparisons = self.curve_view.comparisons()
        self.curve_view.country_changed.connect(self._on_country_picked)
        self.curve_view.comparisons_changed.connect(self._on_comparisons)
        self._curve_host = _CurveHost(self.curve_view, body)
        col.addWidget(self._curve_host, 1)

        spreads_title = QLabel("SPREADS", body)
        spreads_title.setObjectName("panelEyebrow")
        spreads_title.setToolTip(SPREAD_STATE_RULES)
        spreads_title.setMinimumWidth(1)  # clips, never widens a narrow panel
        col.addWidget(spreads_title)
        row = QHBoxLayout()
        row.setSpacing(SPACE_MD)
        self.spreads = [_SpreadReadout(body) for _ in _SPREADS]
        for w in self.spreads:
            row.addWidget(w, 1)
        col.addLayout(row)

        cot_title = QLabel("POSITIONING (CFTC)", body)
        cot_title.setObjectName("panelEyebrow")
        cot_title.setToolTip(
            "Large speculators' net futures position — the weekly CFTC\n"
            "Commitments of Traders report. Click a market for its history."
        )
        cot_title.setMinimumWidth(1)
        col.addWidget(cot_title)
        self.positioning = PositioningBars(body)
        self.positioning.market_clicked.connect(self._on_market_clicked)
        self.positioning.markets_changed.connect(self._on_markets_edited)
        self.positioning.add_requested.connect(self._on_add_market)
        col.addWidget(self.positioning)

        scroll.setWidget(body)
        self.content_layout.addWidget(scroll, 1)

        self.set_loading(True)
        self._apply_all()

    # -- the whole state → screen ------------------------------------------------

    def _apply_all(self) -> None:
        """Push every setting to the widgets and resync the topics. Used by
        build, restore and undo; edits made *in* a widget only resync."""
        self.gauge_strip.set_gauges(self._gauges)
        self.curve_view.set_countries([(c.code, c.label) for c in rates_meta.COUNTRIES],
                                      self._country)
        self.curve_view.set_comparisons(self._comparisons)
        if self._live:
            self._sync_topics()
        self._render()

    def _go_live(self) -> None:
        if not self._live:
            self._live = True
            self._sync_topics()
            self._render()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._go_live()

    def _render(self) -> None:
        self._render_gauges()
        self._render_curve()
        self._render_spreads()
        self._render_positioning()
        self._update_status()

    # -- subscriptions --------------------------------------------------------------

    def _gauge_countries(self) -> set[str]:
        out = set()
        for g in self._gauges:
            if g.kind == "curve":
                parsed = _parse_curve_code(g.code)
                if parsed is not None:
                    out.add(self._country if parsed[0] == "*" else parsed[0])
        return out

    def _wanted_topics(self) -> dict:
        want: dict = {"rates:policy": (self._on_policy, self._on_policy_error)}
        for cc in {self._country} | self._gauge_countries():
            if rates_meta.by_code(cc) is None:
                continue
            want[f"rates:curve:{cc}"] = (
                lambda d, c=cc: self._on_curve(c, d), lambda e, c=cc: self._on_curve_error(c, e))
            want[f"rates:curvehist:{cc}"] = (
                lambda d, c=cc: self._on_hist(c, d), lambda e, c=cc: self._on_hist_error(c, e))
        for g in self._gauges:
            if g.kind == "quote" and g.code:
                s = g.code
                want[f"quote:{s}"] = (lambda d, s=s: self._on_quote(s, d),
                                      lambda e, s=s: self._on_quote_error(s, e))
                want[f"history:{s}:1mo:1d"] = (lambda d, s=s: self._on_closes(s, d),
                                               lambda e: None)
            elif g.kind == "fred" and g.code:
                sid = g.code
                want[f"fred:{sid}"] = (lambda d, i=sid: self._on_fred(i, d),
                                       lambda e, i=sid: self._on_fred_error(i, e))
        for _label, market in self._cftc:
            want[f"cftc:{market}"] = (lambda d, m=market: self._on_cot(m, d), lambda e: None)
        return want

    def _sync_topics(self) -> None:
        """Subscribe what's newly needed, drop what isn't; leave the rest, so
        an edit never re-delivers (and re-flashes) unchanged data."""
        want = self._wanted_topics()
        for topic in list(self._topics):
            if topic not in want:
                self.unsubscribe(topic)
        for topic, (cb, err) in want.items():
            if topic not in self._topics:
                self.subscribe(topic, cb, on_error=err)

    # -- topic callbacks (store, then redraw what depends on it) ---------------------

    def _on_policy(self, data: Any) -> None:
        if isinstance(data, dict):
            self._policy, self._policy_error = data, ""
            self._render_gauges()
            self._update_status()

    def _on_policy_error(self, error: str) -> None:
        self._policy_error = str(error)
        self._render_gauges()

    def _on_curve(self, cc: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._curves[cc] = data
        self._curve_errors.pop(cc, None)
        if cc == self._country:
            self.set_loading(False)
        self._render()

    def _on_curve_error(self, cc: str, error: str) -> None:
        self._curve_errors[cc] = str(error)
        if cc == self._country:
            self.set_loading(False)
        self._render()

    def _on_hist(self, cc: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._hists[cc] = [d for d in (data.get("days") or [])
                           if isinstance(d, (list, tuple)) and len(d) == 2]
        self._hist_sources[cc] = list(data.get("sources") or [])
        if cc == self._country:
            self.curve_view.set_history(self._hists[cc])
        self._render_gauges()
        self._render_spreads()

    def _on_hist_error(self, cc: str, _error: str) -> None:
        # no history is not an error worth a banner: the chips say why
        self._hists.setdefault(cc, [])
        if cc == self._country:
            self.curve_view.set_history(self._hists[cc])

    def _on_quote(self, sym: str, data: Any) -> None:
        if isinstance(data, dict):
            self._quotes[sym] = data
            self._quote_errors.pop(sym, None)
            self._render_gauges()

    def _on_quote_error(self, sym: str, error: str) -> None:
        self._quote_errors[sym] = str(error)
        self._render_gauges()

    def _on_closes(self, sym: str, data: Any) -> None:
        if isinstance(data, dict):
            self._closes[sym] = [v for v in (data.get("c") or []) if _f(v) is not None]
            self._render_gauges()

    def _on_fred(self, sid: str, data: Any) -> None:
        if isinstance(data, dict):
            self._fred[sid] = data
            self._fred_errors.pop(sid, None)
            self._render_gauges()
            self._update_status()

    def _on_fred_error(self, sid: str, error: str) -> None:
        self._fred_errors[sid] = str(error)
        self._render_gauges()

    def _on_cot(self, market: str, data: Any) -> None:
        if isinstance(data, dict):
            self._cot[market] = data
            self._render_positioning()
            self._update_status()

    # -- gauges ---------------------------------------------------------------------

    def _resolve_cc(self, cc: str) -> str:
        return self._country if cc == "*" else cc

    def _display_label(self, g: Gauge) -> str:
        """The name a tile shows: a country-relative gauge is named for the
        panel's country ("US 10Y", "Bund 2s10s", "ECB rate"); "" keeps the
        gauge's own label."""
        if g.kind == "policy" and g.code == "*":
            return f"{bank_name(self._country)} rate"
        if g.kind == "curve":
            parsed = _parse_curve_code(g.code)
            if parsed is not None and parsed[0] == "*":
                _cc, a, b = parsed
                tail = tenor_label(a) if b is None else spread_name(a, b)
                return f"{bond_name(self._country)} {tail}"
        return ""

    def _render_gauges(self) -> None:
        strip = self.gauge_strip
        for i, g in enumerate(self._gauges):
            strip.set_label(i, self._display_label(g))
            reading = self._reading(g)
            if reading is None:
                # nothing for it yet: never leave a previous reading standing
                # (another country's 10Y under this country's name)
                strip.clear_reading(i)
            else:
                strip.set_reading(i, *reading)

    def _reading(self, g: Gauge):
        """(value, change, sign, spark, note, tone) for a gauge, or None while
        it has nothing yet (the tile keeps its skeleton)."""
        try:
            if g.kind == "quote":
                return self._quote_reading(g.code)
            if g.kind == "fred":
                return self._fred_reading(g.code)
            if g.kind == "policy":
                return self._policy_reading(self._resolve_cc(g.code or "*"))
            if g.kind == "curve":
                return self._curve_reading(g.code)
        except Exception:  # a malformed payload must not take the strip down
            return (NULL_GLYPH, "", 0, (), "unavailable", "")
        return None

    def _quote_reading(self, sym: str):
        q = self._quotes.get(sym)
        if q is None:
            if sym in self._quote_errors:
                return (NULL_GLYPH, "", 0, (), "unavailable", "")
            return None
        change_pct = _f(q.get("change_pct"))
        change = signed_pct(change_pct) if change_pct is not None else ""
        sign = 0 if change_pct is None else (1 if change_pct > 0 else -1 if change_pct < 0 else 0)
        return (num(q.get("price")), change, sign, tuple(self._closes.get(sym, ())), "", "")

    def _fred_reading(self, sid: str):
        data = self._fred.get(sid)
        if data is None:
            err = self._fred_errors.get(sid)
            if err is None:
                return None
            note = "needs a FRED key" if "API_KEY" in err else "unavailable"
            return (NULL_GLYPH, "", 0, (), note, "")
        obs = [(d, _f(v)) for d, v in (data.get("points") or []) if _f(v) is not None]
        if not obs:
            return (NULL_GLYPH, "", 0, (), "no releases yet", "")
        last_d, last = obs[-1]
        change, sign = "", 0
        if len(obs) > 1:
            delta = last - obs[-2][1]
            change = f"{delta:+,.2f}"
            sign = (delta > 0) - (delta < 0)
        # a release, not a daily move: say which period it covers
        return (num(last), change, sign, tuple(v for _d, v in obs[-12:]),
                f"{_month_year(last_d)} release", "")

    def _policy_reading(self, cc: str):
        if self._policy is None:
            return (NULL_GLYPH, "", 0, (), "unavailable", "") if self._policy_error else None
        row = next((r for r in self._policy.get("countries") or [] if r.get("code") == cc), None)
        rate = _f(row.get("policy")) if row else None
        if rate is None:
            return (NULL_GLYPH, "", 0, (), "no BIS policy rate", "")
        note = "BIS"
        prev, when = _f(row.get("prev")), row.get("last_change")
        if prev is not None and when and round(abs(rate - prev) * 100) >= 1:
            verb = "cut" if rate < prev else "hiked"
            note = f"{verb} {abs(rate - prev) * 100:.0f}bp in {_month_after(when)}"
        return (f"{rate:.2f}%", "", 0, (), note, "")

    def _curve_series(self, cc: str, fn) -> list[tuple[str, float]]:
        """(iso, fn(points)) over the history, oldest first, with today's
        curve appended when it is newer than the last history day."""
        out = []
        for iso, pts in self._hists.get(cc) or []:
            v = fn(pts)
            if v is not None:
                out.append((str(iso), v))
        cur = self._curves.get(cc) or {}
        as_of = str(cur.get("as_of") or "")
        v = fn(cur.get("points") or [])
        if v is not None and as_of and (not out or as_of > out[-1][0]):
            out.append((as_of, v))
        return out

    def _curve_reading(self, code: str):
        parsed = _parse_curve_code(code)
        if parsed is None:
            return (NULL_GLYPH, "", 0, (), "unknown curve point", "")
        cc, a, b = parsed
        cc = self._resolve_cc(cc)
        meta = rates_meta.by_code(cc)
        if meta is None:
            return (NULL_GLYPH, "", 0, (), "unknown country", "")
        if meta.curve_source == "none":
            return (NULL_GLYPH, "", 0, (), "no free yield curve", "")
        cur = self._curves.get(cc)
        if cur is None:
            return (NULL_GLYPH, "", 0, (), "curve unavailable", "") if cc in self._curve_errors else None
        points = cur.get("points") or []
        if b is None:
            fn = lambda pts: rate_at(pts, a)  # noqa: E731
            now = fn(points)
            if now is None:
                return (NULL_GLYPH, "", 0, (), SPREAD_REASON_MISSING.format(tenor=tenor_label(a)), "")
            value, scale = f"{now:.2f}%", 100.0
        else:
            fn = lambda pts: spread_bp(pts, a, b)  # noqa: E731
            now = fn(points)
            if now is None:
                return (NULL_GLYPH, "", 0, (), spread_reason(points, a, b) or "", "")
            value, scale = f"{now:+.0f} bp", 1.0
        series = self._curve_series(cc, fn)
        change, sign = "", 0
        if len(series) > 1:
            delta = (series[-1][1] - series[-2][1]) * scale
            sign = (delta >= 0.5) - (delta <= -0.5)
            change = _bp(delta) if sign else "0bp"  # never "+0bp" or "-0bp"
        as_of = series[-1][0] if series else ""
        spark: tuple = ()
        if as_of:
            try:
                since = comparison_date(as_of, _SPARK_DAYS)
                spark = tuple(v for d, v in series if d >= since)
            except ValueError:
                spark = ()
        note, tone = "", ""
        if b is not None:
            state = spread_state([v for _d, v in series])
            if state != "normal":
                note, tone = state, ("down" if state == "inverted" else "")
        return (value, change, sign, spark, note, tone)

    def _on_gauge_clicked(self, g: Gauge) -> None:
        if g.sends_symbol:
            self.set_symbol(g.code)

    def _picker_catalog(self) -> list:
        entries = list(RATES_ENTRIES) + _HERE_ENTRIES + _EXTRA_QUOTES
        entries += FX_ENTRIES + INDEX_ENTRIES + commodity_entries()
        if os.environ.get("FRED_API_KEY"):
            entries += FRED_ENTRIES
        return entries

    def _gauge_from_entry(self, entry: CatalogEntry) -> Optional[Gauge]:
        if entry is None:
            return None
        kind = entry.kind if entry.kind in ("quote", "fred", "curve", "policy") else "quote"
        return Gauge(kind, entry.code, _HERE_LABELS.get(entry.code, entry.label))

    def _resolved(self, g: Gauge) -> tuple[str, str]:
        """What a gauge reads at the panel's current country: "*:10" on a US
        panel and "US:10" are the same tile."""
        if g.kind == "policy":
            return g.kind, self._resolve_cc(g.code or "*")
        if g.kind == "curve" and g.code.startswith("*:"):
            return g.kind, f"{self._country}:{g.code[2:]}"
        return g.kind, g.code

    def _same_as(self, g: Gauge, skip: int = -1) -> int:
        """Index of a tile already reading what ``g`` would, else -1."""
        key = self._resolved(g)
        return next((i for i, x in enumerate(self._gauges)
                     if i != skip and self._resolved(x) == key), -1)

    def _reveal(self, index: int) -> None:
        """Point at a tile that is already there instead of adding a twin."""
        self.gauge_strip.reveal(index)
        r = self.gauge_strip.tile_rect(index)
        body = self._scroll.widget()
        c = self.gauge_strip.mapTo(body, r.center())
        self._scroll.ensureVisible(c.x(), c.y(), 0, r.height() // 2 + SPACE_LG)

    def _pick_gauge(self, title: str) -> Optional[Gauge]:
        return self._gauge_from_entry(
            open_add_picker(self, self._picker_catalog(), title=title, show_codes=False))

    def _on_add_gauge(self) -> None:
        g = self._pick_gauge("Add Gauge")
        if g is None:
            return
        same = self._same_as(g)
        if same >= 0:
            self._reveal(same)
            return
        self.gauge_strip.add_gauge(g)

    def _on_replace_gauge(self, index: int) -> None:
        if not 0 <= index < len(self._gauges):
            return
        g = self._pick_gauge("Replace Gauge")
        if g is None:
            return
        same = self._same_as(g, skip=index)
        if same >= 0:
            self._reveal(same)
            return
        self.gauge_strip.replace_gauge(index, g)

    def _on_gauges_edited(self, gauges: list) -> None:
        """The strip changed under the user's hand: record the undo step,
        then fetch what the new strip needs."""
        before = list(self._gauges)
        self._push_undo("edit gauges", gauges=before)
        self._gauges = list(gauges)
        self._sync_topics()
        self._render_gauges()
        self._update_status()

    # -- country and curve -------------------------------------------------------

    def _on_country_picked(self, code: str) -> None:
        if code == self._country or rates_meta.by_code(code) is None:
            return
        self._push_undo("switch country", country=self._country)
        self._country = code
        self._sync_topics()
        self._render()

    def _on_comparisons(self, chips: list) -> None:
        self._comparisons = list(chips)

    def _render_curve(self) -> None:
        cc = self._country
        view = self.curve_view
        meta = rates_meta.by_code(cc)
        if meta is not None and meta.curve_source == "none":
            view.set_state("none")
            return
        cur = self._curves.get(cc)
        if cur is None:
            self._feed_history()
            if cc in self._curve_errors:
                view.set_state("error", "The source could not be reached; it retries on its own")
            else:
                view.set_state("loading")
            return
        points = [p for p in (cur.get("points") or []) if isinstance(p, (list, tuple)) and len(p) == 2]
        if not points:
            view.set_state("error", str(cur.get("note") or "No curve points published"))
            return
        # the view adds the as-of itself; the curve's own source, not BIS
        # (BIS is cited where its policy rates appear: the header)
        citation = " · ".join(str(s) for s in (cur.get("sources") or []) if s != rates_meta.BIS)
        self._feed_history()
        view.set_curve(points, bool(cur.get("complete")), str(cur.get("as_of") or ""),
                       citation or (meta.citation if meta else ""))

    def _feed_history(self) -> None:
        """The view gets this country's history, or waits for it with the
        chips unlit: never another country's."""
        if self._country in self._hists:
            self.curve_view.set_history(self._hists[self._country])
        else:
            self.curve_view.clear_history()

    # -- spreads --------------------------------------------------------------------

    def _render_spreads(self) -> None:
        cc = self._country
        meta = rates_meta.by_code(cc)
        cur = self._curves.get(cc)
        for w, (a, b) in zip(self.spreads, _SPREADS):
            name = f"{bond_name(cc)} {spread_name(a, b)}"
            if meta is not None and meta.curve_source == "none":
                w.set_spread(name, None, "", (), "No free yield curve for this country")
                continue
            if cur is None:
                if cc in self._curve_errors:
                    w.set_spread(name, None, "", (), "The curve could not be loaded")
                else:
                    w.set_loading(name)
                continue
            points = cur.get("points") or []
            now = spread_bp(points, a, b)
            if now is None:
                w.set_spread(name, None, "", (), spread_reason(points, a, b) or "")
                continue
            series = [v for _d, v in self._curve_series(cc, lambda pts: spread_bp(pts, a, b))]
            w.set_spread(name, now, spread_state(series), series)

    # -- positioning ----------------------------------------------------------------

    def _render_positioning(self) -> None:
        rows = []
        for label, market in self._cftc:
            d = self._cot.get(market)
            net = wow = pct = None
            if d is not None:
                net = _f(d.get("noncommercial_net"))
                prev = _f(d.get("noncommercial_net_prev"))
                if net is not None and prev is not None:
                    wow = net - prev
                hist = [_f(h[1]) for h in (d.get("history") or [])
                        if isinstance(h, (list, tuple)) and len(h) > 1]
                hist = [h for h in hist if h is not None]
                if net is not None:
                    pct = percentile(hist, net)
            rows.append((market, label, net, wow, pct))
        self.positioning.set_rows(rows)

    def _on_market_clicked(self, market: str) -> None:
        """Open (or re-centre) Speculator Positioning on this market in our
        link group. The key goes through the commodity table so a market is
        always named the way cot_history matches it."""
        meta = by_cftc_market(market)
        key = meta.cftc_market if meta is not None else market
        if meta is None and key not in _MARKET_LABELS:
            return  # nothing the positioning panel could show
        # every market has a symbol the rest of the app knows: send it, so a
        # chart in the group follows the click like any other
        symbol = meta.symbol if meta is not None else market_symbol(key)
        if symbol:
            self.set_symbol(symbol)
        from .. import panel as panel_module

        hook = panel_module.open_panel_hook
        if hook is not None:
            hook("cot_history", self.link_group, {"market": key})

    def _on_add_market(self) -> None:
        taken = {m for _l, m in self._cftc}
        entries = [e for e in _MARKET_ENTRIES if e.code not in taken]
        entry = open_add_picker(self, entries, allow_free_text=False, title="Add Market",
                                show_codes=False)
        if entry is None or entry.code in taken:
            return
        self.positioning.add_row(entry.code, entry.label)

    def _on_markets_edited(self, markets: list) -> None:
        before = [list(r) for r in self._cftc]
        labels = {m: lbl for lbl, m in self._cftc}
        self._push_undo("edit positioning", cftc=before)
        self._cftc = [[labels.get(m) or _MARKET_LABELS.get(m, m), m] for m in markets]
        self._sync_topics()
        self._render_positioning()
        self._update_status()

    # -- undo -----------------------------------------------------------------------

    def _push_undo(self, label: str, *, gauges=None, cftc=None, country=None) -> None:
        def _undo() -> None:
            if gauges is not None:
                self._gauges = list(gauges)
            if cftc is not None:
                self._cftc = [list(r) for r in cftc]
            if country is not None:
                self._country = country
            self._apply_all()
            self.set_status(f"undo · {label}")

        UndoStack.instance().push(label, _undo)

    # -- status: each source's own as-of, and who to thank -------------------------

    def _source_dates(self) -> list[tuple[str, str]]:
        out: dict[str, str] = {}

        def note(name: str, iso: str) -> None:
            if name and iso and iso > out.get(name, ""):
                out[name] = iso

        if self._policy is not None and any(g.kind == "policy" for g in self._gauges):
            note(rates_meta.BIS, str(self._policy.get("as_of") or ""))
        for cc in {self._country} | self._gauge_countries():
            cur = self._curves.get(cc)
            if cur is None:
                continue
            for s in cur.get("sources") or []:
                if s != rates_meta.BIS:
                    note(str(s), str(cur.get("as_of") or ""))
        for sid, d in self._fred.items():
            if any(g.kind == "fred" and g.code == sid for g in self._gauges):
                pts = [p for p in (d.get("points") or []) if _f(p[1]) is not None]
                if pts:
                    note(rates_meta.FRED, str(pts[-1][0]))
        for _l, m in self._cftc:
            d = self._cot.get(m)
            if d is not None:
                note("CFTC", str(d.get("report_date") or "")[:10])
        return list(out.items())

    def status_text(self) -> str:
        return " · ".join(f"{name} {_short_date(iso)}" for name, iso in self._source_dates())

    def _update_status(self) -> None:
        self.set_status(self.status_text())

    # -- linked-symbol behaviour ----------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        """The backdrop doesn't change with the linked symbol."""

    def header_symbol(self) -> str:
        # nothing here is about the group's symbol, so the header claims none
        return ""

    # -- persistence ----------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "country": self._country,
            "comparisons": list(self._comparisons),
            "gauges": [[g.kind, g.code, g.label] for g in self._gauges],
            "cftc": [list(r) for r in self._cftc],
        }

    def restore(self, settings: dict) -> None:
        """Accept this panel's settings and the pre-redesign ones. Never raises:
        anything unreadable keeps its default."""
        if not isinstance(settings, dict):
            return
        try:
            self._restore(settings)
        except Exception:
            import traceback

            traceback.print_exc()
        self._live = True  # the country is known now: fetch exactly its topics
        self._apply_all()

    def _restore(self, s: dict) -> None:
        country = s.get("country")
        if isinstance(country, str) and rates_meta.by_code(country) is not None:
            self._country = rates_meta.by_code(country).code
        comparisons = s.get("comparisons")
        if isinstance(comparisons, list):
            self._comparisons = [c for c in CHIPS if c in comparisons]
        gauges = s.get("gauges")
        if isinstance(gauges, list):
            cleaned = []
            for r in gauges:
                if (isinstance(r, (list, tuple)) and len(r) == 3
                        and all(isinstance(x, str) for x in r)
                        and r[0] in ("quote", "fred", "curve", "policy")):
                    g = Gauge(r[0], r[1], r[2])
                    if not any((x.kind, x.code) == (g.kind, g.code) for x in cleaned):
                        cleaned.append(g)
            # an empty list is a choice; a list of nothing readable is not
            self._gauges = cleaned if (cleaned or not gauges) else list(DEFAULT_GAUGES)
        elif isinstance(s.get("instruments"), list):
            self._gauges = self._migrate_instruments(s["instruments"])
        # legacy "tenors" is ignored on purpose: the curve comes from its source
        cftc = s.get("cftc")
        if isinstance(cftc, list):
            cleaned = []
            for r in cftc:
                if isinstance(r, (list, tuple)) and len(r) == 2:
                    market = str(r[1]).strip().lower()
                    if market and market not in {m for _l, m in cleaned}:
                        cleaned.append([str(r[0]), market])
            if cleaned or not cftc:
                self._cftc = cleaned

    def _migrate_instruments(self, instruments: list) -> list[Gauge]:
        """Old monitor rows → gauges: the defaults first, then whatever the
        user had added that the defaults don't already show."""
        out = list(DEFAULT_GAUGES)

        def present(g: Gauge) -> bool:
            for x in out:
                if x.kind != g.kind:
                    continue
                if x.code == g.code:
                    return True
                if x.kind == "curve" and x.code.startswith("*:"):
                    # "*:10" on a US panel already is "US:10"
                    if f"{self._country}:{x.code[2:]}" == g.code:
                        return True
            return False

        for r in instruments:
            if not (isinstance(r, (list, tuple)) and len(r) == 2):
                continue
            label, target = str(r[0]), str(r[1]).strip()
            if not target:
                continue
            if target.lower().startswith("fred:"):
                g = Gauge("fred", target.split(":", 1)[1], label)
            elif target.upper() in _LEGACY_CURVE:
                code = _LEGACY_CURVE[target.upper()]
                cc, _, years = code.partition(":")
                g = Gauge("curve", code, f"{bond_name(cc)} {tenor_label(float(years))}")
            else:
                g = Gauge("quote", target, label)
            if not present(g):
                out.append(g)
        return out
