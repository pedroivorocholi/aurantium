"""Company Profile — the Company panel's first tab. A DES-style snapshot:
who the company is, what it trades at, and a door into each deeper view.

Top to bottom: name and live price; ticker · sector › industry · place, with
the website; a grid of stat tiles; the 52-week range drawn as a bar; the
business description (clamped, expandable); officers.

Tiles are the drill-down. Each one with a deeper view opens it — P/E and
market cap → Key Metrics ▸ Valuation, beta → Key Metrics ▸ Risk, EPS →
Earnings — and only those tiles light up on hover, so nothing pretends to be
clickable that isn't.
"""

from __future__ import annotations

from html import escape
from typing import Any, Callable, Optional

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..components.fmt import integer as _fmt_int
from ..components.fmt import num as _fmt_num
from ..panel import NULL_GLYPH, Panel, register_panel
from ..press import PRESS_RADIUS_PROPERTY
from ..theme import ACCENT, BORDER_STRONG, FG_DIM, MONO_FONT, RADIUS_SM, tick_color
from ..components.responsive_row import ResponsiveRow


def _fmt_big(value: Any) -> str:
    if value is None:
        return NULL_GLYPH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NULL_GLYPH
    for suffix, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(v) >= div:
            return f"{v / div:.2f}{suffix}"
    return f"{v:,.0f}"


def _fmt_pct_units(value: Any) -> str:
    """A value already in percent units (1.25 → "1.25%")."""
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return NULL_GLYPH


def _float(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


#: (key, eyebrow, payload field, formatter, (tab, Key Metrics group) or None, tooltip)
TILES: list[tuple[str, str, str, Callable[[Any], str], Optional[tuple[str, str]], str]] = [
    ("mktcap", "MKT CAP", "market_cap", _fmt_big, ("key_metrics", "Valuation"),
     "Market capitalization. Click for valuation metrics and their history."),
    ("pe", "P/E TTM", "pe_trailing", _fmt_num, ("key_metrics", "Valuation"),
     "Price ÷ trailing-twelve-month EPS. Click for valuation metrics."),
    ("pe_fwd", "P/E FWD", "pe_forward", _fmt_num, ("key_metrics", "Valuation"),
     "Price ÷ next-year consensus EPS. Click for valuation metrics."),
    ("eps", "EPS TTM", "eps_trailing", _fmt_num, ("earnings", ""),
     "Trailing-twelve-month earnings per share. Click for the earnings history."),
    ("div", "DIV YIELD", "dividend_yield_pct", _fmt_pct_units, ("key_metrics", "Dividends & Buybacks"),
     "Indicated annual dividend ÷ price. Click for dividend metrics."),
    ("beta", "BETA", "beta", _fmt_num, ("key_metrics", "Risk"),
     "Sensitivity to the S&P 500. Click for risk metrics, including alpha."),
    ("shares", "SHARES OUT", "shares_outstanding", _fmt_big, None, "Shares outstanding."),
    ("employees", "EMPLOYEES", "employees", _fmt_int, None, "Full-time employees."),
]

#: description lines shown before "Show more"
_DESC_LINES = 4


class StatTile(QFrame):
    """One labelled figure. Clickable only when it has somewhere to go."""

    clicked = Signal()

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statTile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 6)
        lay.setSpacing(1)
        self.label = QLabel(label, self)
        self.label.setObjectName("tileLabel")
        self.value = QLabel(NULL_GLYPH, self)
        self.value.setObjectName("tileValue")
        lay.addWidget(self.label)
        lay.addWidget(self.value)
        # never let a long figure force the grid wider than the panel
        for lbl in (self.label, self.value):
            lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            lbl.setMinimumWidth(0)
        self.setMinimumWidth(0)
        self.set_clickable(False)

    def set_clickable(self, on: bool) -> None:
        self.setProperty("clickable", "true" if on else "false")
        # sink on press like every button — only while it is a door
        self.setProperty(PRESS_RADIUS_PROPERTY, RADIUS_SM if on else None)
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)
        # a dynamic property doesn't re-run the stylesheet on its own
        for w in (self, self.label, self.value):
            w.style().unpolish(w)
            w.style().polish(w)

    @property
    def clickable(self) -> bool:
        return self.property("clickable") == "true"

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if (
            self.clickable
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def _round_pen(color: str, width: float = 3) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


class RangeBar(QWidget):
    """The 52-week range as a track, filled up to today's price."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.low: Optional[float] = None
        self.high: Optional[float] = None
        self.price: Optional[float] = None

    def set_values(self, low: Any, high: Any, price: Any) -> None:
        self.low, self.high, self.price = _float(low), _float(high), _float(price)
        pos = self.position()
        self.setToolTip(
            "" if pos is None
            else f"Price is {pos * 100:.0f}% of the way from the 52-week low to the high."
        )
        self.update()

    def position(self) -> Optional[float]:
        if self.low is None or self.high is None or self.price is None or self.high <= self.low:
            return None
        return min(max((self.price - self.low) / (self.high - self.low), 0.0), 1.0)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = p.font()
        font.setFamily(MONO_FONT)
        p.setFont(font)
        fm = QFontMetrics(font)
        lo = _fmt_num(self.low) if self.low is not None else NULL_GLYPH
        hi = _fmt_num(self.high) if self.high is not None else NULL_GLYPH
        lo_w, hi_w = fm.horizontalAdvance(lo), fm.horizontalAdvance(hi)
        mid = 10
        x0, x1 = lo_w + 12, self.width() - hi_w - 12
        p.setPen(QColor(FG_DIM))
        p.drawText(QRectF(0, 0, lo_w + 2, 2 * mid), Qt.AlignmentFlag.AlignVCenter, lo)
        p.drawText(QRectF(x1 + 10, 0, hi_w + 2, 2 * mid), Qt.AlignmentFlag.AlignVCenter, hi)
        if x1 <= x0:
            p.end()
            return
        p.setPen(_round_pen(BORDER_STRONG))
        p.drawLine(x0, mid, x1, mid)
        pos = self.position()
        if pos is not None:
            x = x0 + (x1 - x0) * pos
            p.setPen(_round_pen(ACCENT))
            p.drawLine(x0, mid, round(x), mid)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(QRectF(x - 4.5, mid - 4.5, 9, 9))
            label = f"{pos * 100:.0f}%"
            w = fm.horizontalAdvance(label)
            lx = min(max(x - w / 2, x0), x1 - w)
            p.setPen(QColor(FG_DIM))
            p.drawText(QRectF(lx, mid + 6, w + 2, 16), Qt.AlignmentFlag.AlignCenter, label)
        p.end()


@register_panel(id="profile", title="Company Profile", category="Research", listed=False)
class ProfilePanel(Panel):
    def build(self) -> None:
        self._info: dict = {}
        self._price: Optional[float] = None
        self._desc_open = False

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        # vertical only: everything below reflows to the width it is given
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll
        body = QWidget(scroll)
        outer = QVBoxLayout(body)
        # no inset of its own: the Company tab supplies it, as for every tab
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # -- identity: name left, live price right -----------------------------
        head = ResponsiveRow()
        head.setSpacing(12)
        self.name_lbl = QLabel(NULL_GLYPH, body)
        self.name_lbl.setObjectName("statValueLarge")
        self.name_lbl.setWordWrap(True)
        head.addWidget(self.name_lbl, 1)
        price_col = QVBoxLayout()
        price_col.setSpacing(0)
        self.price_lbl = QLabel("", body)
        self.price_lbl.setObjectName("profilePrice")
        self.price_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.change_lbl = QLabel("", body)
        self.change_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        price_col.addWidget(self.price_lbl)
        price_col.addWidget(self.change_lbl)
        head.addLayout(price_col)
        outer.addLayout(head)

        self.sector_lbl = QLabel("", body)
        self.sector_lbl.setObjectName("secondary")
        self.sector_lbl.setWordWrap(True)
        outer.addWidget(self.sector_lbl)
        # its own line: a long domain must never set the panel's width
        self.web_lbl = QLabel("", body)
        self.web_lbl.setTextFormat(Qt.TextFormat.RichText)
        self.web_lbl.setOpenExternalLinks(True)
        self.web_lbl.setToolTip("Open the company website in your browser")
        self.web_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        outer.addWidget(self.web_lbl)

        # -- stat tiles ------------------------------------------------------
        self.tiles_grid = QGridLayout()
        self.tiles_grid.setHorizontalSpacing(6)
        self.tiles_grid.setVerticalSpacing(6)
        self.tiles: dict[str, StatTile] = {}
        for key, label, _field, _fmt, _target, tip in TILES:
            tile = StatTile(label, body)
            tile.setToolTip(tip)
            tile.clicked.connect(lambda k=key: self.open_tile(k))
            self.tiles[key] = tile
        self._tile_cols = 0
        self._layout_tiles(4)
        outer.addLayout(self.tiles_grid)

        # -- 52-week range -----------------------------------------------------
        outer.addWidget(self._eyebrow("52-WEEK RANGE", body))
        self.range_bar = RangeBar(body)
        outer.addWidget(self.range_bar)

        # -- business description ----------------------------------------------
        outer.addWidget(self._eyebrow("ABOUT", body))
        self.desc_lbl = QLabel("", body)
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.desc_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.desc_lbl)
        # a text link, not a button: it's a reveal, and a raised control
        # under a paragraph reads as an action on the company
        self.more_btn = QLabel("", body)
        self.more_btn.setTextFormat(Qt.TextFormat.RichText)
        self.more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_btn.linkActivated.connect(lambda _href: self._toggle_desc())
        self.more_btn.setVisible(False)
        more_row = ResponsiveRow()
        more_row.addWidget(self.more_btn)
        more_row.addStretch(1)
        outer.addLayout(more_row)

        # -- officers ---------------------------------------------------------
        outer.addWidget(self._eyebrow("OFFICERS", body))
        self.officers_grid = QGridLayout()
        self.officers_grid.setVerticalSpacing(1)
        self.officers_grid.setColumnStretch(0, 1)
        outer.addLayout(self.officers_grid)

        outer.addStretch(1)
        scroll.setWidget(body)
        self.content_layout.addWidget(scroll, 1)
        self._sync_clickable()

    @staticmethod
    def _eyebrow(text: str, parent: QWidget) -> QLabel:
        lbl = QLabel(text, parent)
        lbl.setObjectName("panelEyebrow")
        lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        return lbl

    # -- size classes ----------------------------------------------------------

    #: narrowest a tile may get before the grid drops a column
    TILE_MIN = 96

    def _reflow_tiles(self) -> None:
        # the panel's own width: the scroll viewport hasn't been resized yet
        # when this panel's resize event arrives
        m = self.content_layout.contentsMargins()
        width = self.width() - m.left() - m.right()
        cols = max(1, min(len(TILES), (width + 6) // (self.TILE_MIN + 6)))
        # prefer an even split of the eight tiles: 8, 4, 2 (then 1)
        for even in (8, 4, 2, 1):
            if cols >= even:
                cols = even
                break
        self._layout_tiles(cols)

    def _layout_tiles(self, cols: int) -> None:
        if cols == self._tile_cols:
            return
        self._tile_cols = cols
        for tile in self.tiles.values():
            self.tiles_grid.removeWidget(tile)
        for i, tile in enumerate(self.tiles.values()):
            r, c = divmod(i, cols)
            self.tiles_grid.addWidget(tile, r, c)
        for c in range(len(TILES)):
            self.tiles_grid.setColumnStretch(c, 1 if c < cols else 0)

    # -- drill-down ------------------------------------------------------------

    def _hosted(self) -> bool:
        return self._host is not None and hasattr(self._host, "show_tab")

    def _sync_clickable(self) -> None:
        """A tile is a door only when this profile sits in the Company panel
        (where the deeper tabs are) and it has a figure to explain."""
        hosted = self._hosted()
        for key, _label, field, _fmt, target, _tip in TILES:
            has_value = self._info.get(field) is not None
            self.tiles[key].set_clickable(bool(target) and hosted and has_value)

    def open_tile(self, key: str) -> None:
        target = next((t[4] for t in TILES if t[0] == key), None)
        if not target or not self._hosted():
            return
        tab, group = target
        child = self._host.show_tab(tab)
        if group and child is not None and hasattr(child, "show_group"):
            child.show_group(group)

    # -- description -------------------------------------------------------------

    def _toggle_desc(self) -> None:
        self._desc_open = not self._desc_open
        self._clamp_desc()

    def _clamp_desc(self) -> None:
        if not hasattr(self, "desc_lbl"):
            return  # resize before build()
        text = self.desc_lbl.text()
        fm = QFontMetrics(self.desc_lbl.font())
        # measure against the width the label is about to get, not its
        # current one — on a resize the label hasn't been laid out yet
        m = self.content_layout.contentsMargins()
        scrollbar = self._scroll.verticalScrollBar()
        sb = scrollbar.sizeHint().width() if scrollbar.isVisible() else 0
        width = max(self.width() - m.left() - m.right() - sb, 120)
        full = fm.boundingRect(0, 0, width, 100000, Qt.TextFlag.TextWordWrap, text).height()
        clamp = fm.lineSpacing() * _DESC_LINES
        overflow = full > clamp + 2
        self.more_btn.setVisible(overflow)
        label = "Show less" if self._desc_open else "Show more"
        self.more_btn.setText(f'<a href="#" style="color:{ACCENT}; text-decoration:none;">{label}</a>')
        self.desc_lbl.setMaximumHeight(16777215 if (self._desc_open or not overflow) else clamp)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if hasattr(self, "tiles"):
            self._reflow_tiles()
        self._clamp_desc()

    # -- data ------------------------------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        self.set_loading(True)
        self._clear()
        self.unsubscribe_all()
        self.subscribe(f"profile:{symbol}", self._on_profile)
        # the price is garnish here: a quote failure must not flag the profile
        self.subscribe(f"quote:{symbol}", self._on_quote, on_error=lambda _e: None)

    def _clear(self) -> None:
        """Empty the panel before the next company arrives — a stale profile
        under a new ticker is a confident, complete, wrong answer."""
        self._info = {}
        self._price = None
        self._desc_open = False
        self.name_lbl.setText(NULL_GLYPH)
        self.sector_lbl.setText("")
        self.web_lbl.setText("")
        self.price_lbl.setText("")
        self.change_lbl.setText("")
        self.desc_lbl.setText("")
        for tile in self.tiles.values():
            tile.value.setText(NULL_GLYPH)
        self.range_bar.set_values(None, None, None)
        self._clear_officers()
        self._sync_clickable()
        self._clamp_desc()

    def _clear_officers(self) -> None:
        while self.officers_grid.count():
            item = self.officers_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_quote(self, data: Any) -> None:
        q = data if isinstance(data, dict) else {}
        price = _float(q.get("price"))
        if price is None:
            return
        self._price = price
        self.price_lbl.setText(f"{price:,.2f}")
        chg, pct = _float(q.get("change")), _float(q.get("change_pct"))
        if chg is not None and pct is not None:
            sign = "+" if chg >= 0 else ""
            self.change_lbl.setText(f"{sign}{chg:,.2f}  {sign}{pct:.2f}%")
            self.change_lbl.setStyleSheet(
                f'background: transparent; color: {tick_color(chg)}; font-family: "{MONO_FONT}";'
            )
        self.range_bar.set_values(self._info.get("week52_low"), self._info.get("week52_high"), price)

    def _on_profile(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        info = data if isinstance(data, dict) else {}
        self._info = info

        self.name_lbl.setText(str(info.get("name") or self.current_symbol or NULL_GLYPH))

        bits = [self.current_symbol] if self.current_symbol else []
        industry = " › ".join(p for p in (info.get("sector"), info.get("industry")) if p)
        if industry:
            bits.append(industry)
        place = ", ".join(p for p in (info.get("city"), info.get("country")) if p)
        if place:
            bits.append(place)
        self.sector_lbl.setText("  ·  ".join(bits) or NULL_GLYPH)

        site = str(info.get("website") or "")
        if site.startswith(("http://", "https://")):
            shown = site.split("//", 1)[1].rstrip("/")
            shown = shown[4:] if shown.startswith("www.") else shown
            self.web_lbl.setText(
                f'<a href="{escape(site)}" style="color:{ACCENT}; text-decoration:none;">{escape(shown)} ↗</a>'
            )
        else:
            self.web_lbl.setText("")

        for key, _label, field, fmt, _target, _tip in TILES:
            value = info.get(field)
            self.tiles[key].value.setText(fmt(value) if value is not None else NULL_GLYPH)

        self.range_bar.set_values(info.get("week52_low"), info.get("week52_high"), self._price)

        self._desc_open = False
        self.desc_lbl.setText(info.get("description") or "No description available.")
        self._clamp_desc()

        self._clear_officers()
        officers = info.get("officers")
        officers = [o for o in officers if isinstance(o, dict)] if isinstance(officers, list) else []
        if not officers:
            none = QLabel(NULL_GLYPH, self)
            none.setObjectName("secondary")
            self.officers_grid.addWidget(none, 0, 0)
        for r, o in enumerate(officers):
            name = QLabel(o.get("name") or NULL_GLYPH, self)
            name.setWordWrap(True)
            name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            title = QLabel(o.get("title") or "", self)
            title.setObjectName("secondary")
            title.setWordWrap(True)
            # title under the name, not beside it: two columns of names and
            # titles can't share a sidebar-width panel without one wrapping
            # into a ragged sliver
            self.officers_grid.addWidget(name, 2 * r, 0)
            self.officers_grid.addWidget(title, 2 * r + 1, 0)
            if r:  # a little air between officers, none within one
                name.setContentsMargins(0, 5, 0, 0)

        self._sync_clickable()
        self.set_status(info.get("sector") or "")
