"""Heatmap panel — any set of stocks as a market map.

One universe at a time (the 11 sectors, a sector's largest companies, an
index's largest members, the portfolio, the group's Watchlist, or a custom
list) drawn as a squarified treemap: each tile sized by market cap or position
value, filled by today's move. The fill runs from DOWN (red, <= -2%) through
neutral BG_ALT (0%) to UP (green, >= +2%). Click a tile to send its symbol to
the link group; double-click a sector to open its largest companies.

The module keeps its old name (and the panel its ``sectors`` id) so saved
layouts open it unchanged.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolTip,
    QWidget,
)
from shiboken6 import isValid

from .. import motion
from ..color import mix_oklab, readable_on
from ..components import treemap
from ..components.empty_state import EmptyState
from ..components.responsive_row import ResponsiveRow
from ..fx import FxService
from ..guide import PanelGuide
from ..heatmap_model import (
    Tile,
    tiles_from_screen,
    tiles_mixed,
    tiles_portfolio,
    tiles_sectors,
)
from ..heatmap_universes import INDEXES, SECTOR_KEYS, SECTORS, view_label
from ..panel import NULL_GLYPH, UNLINKED, WARN_GLYPH, Panel, register_panel
from ..portfolio_store import PortfolioStore
from ..providers.screen import symbols_topic
from ..theme import (
    ACCENT,
    BG,
    BG_ALT,
    BORDER_STRONG,
    CHROME_LOW,
    DOWN,
    FG,
    FG_DIM,
    FG_MUTED,
    FONT_DISPLAY,
    FONT_LG,
    FONT_MD,
    FONT_SM,
    FONT_TITLE,
    FONT_XS,
    MONO_FONT,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XS,
    TILE_LABEL_MIN_PX,
    UI_FONT,
    UP,
    WEIGHT_BOLD,
)
from .watchlist import WatchlistPanel

PCT_CLAMP = 2.0  # +/- 2% maps to full saturation


def _pct_of(change_pct: Any) -> float | None:
    """``change_pct`` as a float, or None when there is no reading at all."""
    if change_pct is None:
        return None
    try:
        return float(change_pct)
    except (TypeError, ValueError):
        return None


def _tile_color(change_pct: Any) -> QColor:
    """The tile fill for a reading, or the *no-data* fill for a missing one.

    Two changes from the original.

    **No data is not neutral.** Both a missing reading and a dead-flat 0.00%
    used to return ``BG_ALT``, so a total provider outage rendered as "every
    sector is exactly flat" — a wrong answer stated confidently, which is worse
    than an obviously broken one. The fill is the pre-attentive channel here;
    the figure underneath differs ("—" vs "+0.00%") but nobody reads eleven
    figures to discover the feed is down. No-data now takes the panel surface
    ``BG``, which is off the ramp by construction (the ramp starts at
    ``BG_ALT``). On the light theme that fill alone is not enough separation —
    see :func:`_has_reading` for why, and for the channels that carry it
    instead.

    **The blend is perceptual.** ``mix_oklab`` rather than averaging sRGB bytes.
    A modest gain on this ramp — see that function — but a ramp whose whole job
    is encoding magnitude should not have its evenness depend on which two
    colours it happens to run between.
    """
    pct = _pct_of(change_pct)
    if pct is None:
        return QColor(BG)
    end = UP if pct >= 0 else DOWN
    return QColor(mix_oklab(BG_ALT, end, abs(pct) / PCT_CLAMP))


def _tile_ink(change_pct: Any) -> str:
    """The label colour for a tile, chosen against that tile's own fill.

    The three labels were hardcoded ``#f2f2f2`` / ``#e0e0e0`` / ``#ffffff``.
    That is defensible at the saturated end of the dark ramp and indefensible
    everywhere else: on the light theme eight of eleven steps put white text at
    under 3:1, bottoming out at **1.09:1** on a flat tile — the figure was
    simply not there. Picking per tile takes the worst case to 4.67:1.
    """
    return readable_on(_tile_color(change_pct).name())


def _has_reading(change_pct: Any) -> bool:
    """Whether this tile has a value at all.

    The distinction cannot be carried by fill. On the dark theme "no data"
    (``BG``, near-black) sits ΔE 15.4 from a flat 0.00% (``BG_ALT``) and reads
    clearly. On the light theme the same two are ΔE **3.0** — and pushing them
    apart needs a fill about a third of the way to ``FG_MUTED``, which is a
    mid-grey that reads as *a value on the ramp*, not as an absence. The two
    themes want opposite directions and neither answer is honest.

    So absence is deliberately encoded off the colour channel: a dashed outline,
    the null glyph instead of a percentage, and muted ink. That is the same rule
    the up/down ticks already follow with ▲/▼ — when colour cannot carry a
    distinction on its own, it does not have to.
    """
    return _pct_of(change_pct) is not None


def _fmt_pct(value: Any) -> str:
    pct = _pct_of(value)
    return NULL_GLYPH if pct is None else f"{pct:+.2f}%"



# -- the map ---------------------------------------------------------------

#: The share of the sized total each loading (zero-weight) tile is laid out at,
#: so a position still waiting on its rate stands as a visible skeleton rather
#: than vanishing until it lands.
LOADING_SHARE = 0.02

#: Label steps, largest first. The name line takes the step, the figure the
#: next one down, so the symbol leads and the figure stays readable under it.
_LABEL_STEPS = (FONT_DISPLAY, FONT_TITLE, FONT_LG, FONT_MD, FONT_SM)


def _font(family: str, px: int, bold: bool = False) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    if bold:
        f.setWeight(QFont.Weight(WEIGHT_BOLD))
    return f


def _ordered(tiles: list[Tile]) -> list[Tile]:
    """Largest first (the layout's quality depends on it); zero-weight loading
    tiles after every sized one, in the order they came."""
    sized = sorted((t for t in tiles if t.weight > 0), key=lambda t: t.weight, reverse=True)
    return sized + [t for t in tiles if t.weight <= 0]


def _layout_weights(tiles: list[Tile]) -> list[float]:
    total = sum(t.weight for t in tiles if t.weight > 0)
    if total <= 0:
        return [1.0] * len(tiles)  # nothing sized yet: an even grid of skeletons
    floor = total * LOADING_SHARE
    return [t.weight if t.weight > 0 else floor for t in tiles]


def _label_for(tile: Tile, rect: QRect) -> tuple[int, tuple[str, ...]]:
    """``(name px, lines)`` for the largest label that fits ``rect``; no lines
    below ``TILE_LABEL_MIN_PX`` or when nothing fits. The sector name is tried
    before its ETF, two lines (name and figure) before the name alone."""
    if min(rect.width(), rect.height()) < TILE_LABEL_MIN_PX:
        return 0, ()
    avail_w = rect.width() - 2 * SPACE_SM
    # the label never fills the tile: a block that touches both edges reads as
    # crammed, and the colour is the primary channel, not the text
    avail_h = (rect.height() - 2 * SPACE_SM) * 0.8
    names = [tile.label] if tile.label == tile.symbol else [tile.label, tile.symbol]
    figure = None if tile.loading else _fmt_pct(tile.change_pct)
    if figure is not None:
        for name in names:
            for i, px in enumerate(_LABEL_STEPS):
                small = _LABEL_STEPS[min(i + 1, len(_LABEL_STEPS) - 1)]
                top = QFontMetrics(_font(UI_FONT, px, bold=True))
                low = QFontMetrics(_font(MONO_FONT, small))
                if (
                    max(top.horizontalAdvance(name), low.horizontalAdvance(figure)) <= avail_w
                    and top.height() + low.height() <= avail_h
                ):
                    return px, (name, figure)
    for name in names:
        for px in _LABEL_STEPS:
            fm = QFontMetrics(_font(UI_FONT, px, bold=True))
            if fm.horizontalAdvance(name) <= avail_w and fm.height() <= avail_h:
                return px, (name,)
    return 0, ()


class _Map(QWidget):
    """The treemap itself: lays the tiles out on resize and on new data, and
    paints them. One widget for the whole map rather than one per tile — a
    40-tile index re-sized by a splitter drag is one layout pass and one paint,
    not forty widgets being moved."""

    tile_clicked = Signal(str)  # symbol
    tile_activated = Signal(object)  # Tile, on double-click

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(1, 1)
        self._input: list[Tile] = []
        self._tiles: list[Tile] = []  # in layout order
        self._rects: list[QRect] = []
        self._labels: list[tuple[int, tuple[str, ...]]] = []
        self._hover = -1
        self._pressed = -1
        self._current = ""
        self.empty = EmptyState(self)
        # press feedback sinks just the tile under the pointer (see press.py)
        self.__dict__["press_region"] = self._press_region

    # -- content --------------------------------------------------------------

    def set_tiles(self, tiles: list[Tile]) -> None:
        self._input = list(tiles)
        self._relayout()

    def set_current(self, symbol: str) -> None:
        if symbol != self._current:
            self._current = symbol
            self.update()

    def set_empty(self, title: str, hint: str = "", loading: bool = False) -> None:
        self.empty.set_text(title, hint)
        self.empty.set_loading(loading)
        self.empty.setGeometry(self.rect())
        self.empty.set_shown(True)
        self.empty.update()

    def clear_empty(self) -> None:
        self.empty.set_loading(False)
        self.empty.set_shown(False)

    # -- queries (the panel and tests read the map through these) -------------

    def tiles(self) -> list[Tile]:
        return list(self._tiles)

    def _index_of(self, symbol: str) -> int:
        return next((i for i, t in enumerate(self._tiles) if t.symbol == symbol), -1)

    def tile_rect(self, symbol: str) -> QRect:
        i = self._index_of(symbol)
        return QRect(self._rects[i]) if i >= 0 else QRect()

    def label_lines(self, symbol: str) -> tuple[str, ...]:
        i = self._index_of(symbol)
        return self._labels[i][1] if i >= 0 else ()

    def index_at(self, pos) -> int:
        for i, r in enumerate(self._rects):
            if r.contains(pos):
                return i
        return -1

    def tooltip_at(self, pos) -> str:
        i = self.index_at(pos)
        return self._tiles[i].tooltip if i >= 0 else ""

    def _press_region(self, pos):
        i = self.index_at(pos)
        return (QRect(self._rects[i]), 0) if i >= 0 else None

    # -- layout ---------------------------------------------------------------

    def _relayout(self) -> None:
        self._tiles = _ordered(self._input)
        w, h = self.width(), self.height()
        rects: list[QRect] = []
        for x, y, rw, rh in treemap.layout(_layout_weights(self._tiles), w, h):
            # snap both edges, not origin and size, so neighbours share an
            # edge exactly: no seams, no overlaps
            x0, y0, x1, y1 = round(x), round(y), round(x + rw), round(y + rh)
            rects.append(QRect(x0, y0, x1 - x0, y1 - y0))
        self._rects = rects
        self._labels = [_label_for(t, r) for t, r in zip(self._tiles, rects)]
        if self._hover >= len(self._tiles):
            self._hover = -1
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self.empty.setGeometry(self.rect())
        self._relayout()

    # -- painting -------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(BG))
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            for i, (tile, rect) in enumerate(zip(self._tiles, self._rects)):
                if rect.width() < 1 or rect.height() < 1:
                    continue
                self._paint_tile(p, tile, rect, self._labels[i], i)
        finally:
            p.end()

    def _paint_tile(self, p: QPainter, tile: Tile, rect: QRect, label, i: int) -> None:
        # The 1px BG gutter on the right and bottom is the tile border: true
        # black between fills, the same hairline the terminal draws everywhere.
        body = rect.adjusted(0, 0, -1, -1)
        if tile.loading:
            p.fillRect(body, QColor(CHROME_LOW))  # the workspace skeleton's bar fill
            ink = FG_MUTED
        else:
            p.fillRect(body, _tile_color(tile.change_pct))
            if _has_reading(tile.change_pct):
                ink = _tile_ink(tile.change_pct)
            else:
                # no data: dashed edge and muted ink, as _has_reading explains
                pen = QPen(QColor(BORDER_STRONG))
                pen.setStyle(Qt.PenStyle.DashLine)
                pen.setWidth(1)
                p.setPen(pen)
                p.drawRect(body.adjusted(0, 0, -1, -1))
                ink = FG_MUTED
        # the linked symbol and the hovered tile get an inset outline: amber
        # for "this is the one the group is on", the foreground for the pointer
        edge = ACCENT if tile.symbol == self._current else (FG if i == self._hover else None)
        if edge is not None and body.width() > 2 and body.height() > 2:
            pen = QPen(QColor(edge))
            pen.setWidth(1)
            p.setPen(pen)
            p.drawRect(body.adjusted(0, 0, -1, -1))
        px, lines = label
        if not lines:
            return
        top = _font(UI_FONT, px, bold=True)
        idx = _LABEL_STEPS.index(px)
        low = _font(MONO_FONT, _LABEL_STEPS[min(idx + 1, len(_LABEL_STEPS) - 1)])
        fm_top, fm_low = QFontMetrics(top), QFontMetrics(low)
        block = fm_top.height() + (fm_low.height() if len(lines) > 1 else 0)
        y = body.top() + (body.height() - block) // 2
        p.setPen(QColor(ink))
        p.setFont(top)
        p.drawText(QRect(body.left(), y, body.width(), fm_top.height()),
                   Qt.AlignmentFlag.AlignCenter, lines[0])
        if len(lines) > 1:
            p.setFont(low)
            p.drawText(QRect(body.left(), y + fm_top.height(), body.width(), fm_low.height()),
                       Qt.AlignmentFlag.AlignCenter, lines[1])

    # -- pointer --------------------------------------------------------------

    def event(self, event) -> bool:  # noqa: A003 (Qt override)
        if event.type() == QEvent.Type.ToolTip:
            i = self.index_at(event.pos())
            if i >= 0:
                QToolTip.showText(event.globalPos(), self._tiles[i].tooltip, self, self._rects[i])
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        i = self.index_at(event.position().toPoint())
        if i != self._hover:
            self._hover = i
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = self.index_at(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # a click is a press and release on the same tile, so dragging off a
        # tile to think again cancels it, the way a button does
        if event.button() == Qt.MouseButton.LeftButton:
            i = self.index_at(event.position().toPoint())
            if i >= 0 and i == self._pressed:
                self.tile_clicked.emit(self._tiles[i].symbol)
            self._pressed = -1
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            i = self.index_at(event.position().toPoint())
            if i >= 0:
                self.tile_activated.emit(self._tiles[i])
                return
        super().mouseDoubleClickEvent(event)


class _Legend(QWidget):
    """The ramp's key: five cells, −2% to +2%, under the map's right edge.
    Painted from the same ``_tile_color`` / ``_tile_ink`` as the tiles, so it
    can never disagree with them."""

    STOPS = (-2.0, -1.0, 0.0, 1.0, 2.0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font = _font(MONO_FONT, FONT_XS)
        fm = QFontMetrics(self._font)
        self._cell_w = fm.horizontalAdvance("+2%") + 2 * SPACE_LG
        self.setFixedHeight(fm.height() + 2 * SPACE_XS)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    @staticmethod
    def _text(pct: float) -> str:
        return "0%" if pct == 0 else f"{pct:+.0f}%"

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._cell_w * len(self.STOPS), self.height())

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor(BG))
            p.setFont(self._font)
            n = len(self.STOPS)
            cell = min(self._cell_w, self.width() // n)
            x = self.width() - cell * n
            for pct in self.STOPS:
                r = QRect(x, 0, cell, self.height())
                p.fillRect(r.adjusted(0, 0, -1, 0), _tile_color(pct))
                p.setPen(QColor(_tile_ink(pct)))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._text(pct))
                x += cell
        finally:
            p.end()


# -- views -----------------------------------------------------------------

#: companies shown for a drilled sector
SECTOR_N = 30
#: how often the Watchlist view looks again for a Watchlist in its group
WATCH_RECHECK_MS = 1000

_SIMPLE_VIEWS = ("sectors", "portfolio", "watchlist", "custom")
_SECTOR_LABELS = {key: label for key, label, _etf in SECTORS}


def clean_view(view: Any) -> Optional[str]:
    """The canonical spelling of a view key, or None for anything unknown."""
    if not isinstance(view, str):
        return None
    v = view.strip()
    if v.lower() in _SIMPLE_VIEWS:
        return v.lower()
    kind, _, key = v.partition(":")
    if kind.lower() == "index" and key.lower() in INDEXES:
        return f"index:{key.lower()}"
    if kind.lower() == "sector":
        for k in SECTOR_KEYS:
            if k.lower() == key.strip().lower():
                return f"sector:{k}"
    return None


@register_panel(id="sectors", title="Heatmap", category="Markets")
class HeatmapPanel(Panel):
    SKELETON = "plot"

    GUIDE = PanelGuide(
        purpose="Any set of stocks as a market map: tiles sized by value, coloured by today's move.",
        links=(
            "Click a tile to send its symbol to every panel in this link group; "
            "the map itself follows no symbol. In the **Watchlist** view it draws "
            "the Watchlist in its own link group, and redraws as that list changes."
        ),
        interactions=(
            "**View** picks the map: the 11 Sectors, one sector's largest "
            "companies, the S&P 500 or Nasdaq 100 (largest 40) or the Dow 30, "
            "your Portfolio, the group's Watchlist, or a Custom list.",
            "Click a tile to send its symbol to the link group; hover for name, "
            "price, change and size.",
            "Double-click a sector to open its 30 largest companies; **◂ Back** "
            "or Backspace returns to the sectors.",
            "A dark tile is still loading; a dashed one has no reading.",
        ),
        fields=(
            ("Size", "Market cap — sector cap for Sectors, position value in "
             "your currency for Portfolio."),
            ("Colour", "Today's % change: red at −2% or worse, through neutral, "
             "to green at +2% or better."),
        ),
        keywords=(
            "heat map", "heatmap", "market map", "treemap", "sectors", "S&P 500",
            "Nasdaq", "Dow", "portfolio map", "finviz",
        ),
    )

    def build(self) -> None:
        self._view = "sectors"
        #: (label, symbol) tiles of the Custom view; Task 6 brings the editing
        self._custom: list[tuple[str, str]] = []
        self._quotes: dict[str, dict] = {}
        self._screens: dict[str, list] = {}  # screen topic -> its last good rows
        self._caps: dict[str, float] = {}
        self._errors: dict[str, str] = {}  # topic -> the provider's message
        self._watchlist: Optional[WatchlistPanel] = None
        self._syncing = False
        self._waiting = False

        row = ResponsiveRow()
        row.setSpacing(SPACE_MD)
        self.back_btn = QPushButton("◂ Back", self)
        self.back_btn.setToolTip("Back to the sectors (Backspace)")
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.hide()
        row.addWidget(self.back_btn)
        self.crumb = QLabel("", self)
        self.crumb.setStyleSheet(f"color: {FG}; font-size: {FONT_MD}px;")
        row.addWidget(self.crumb)
        # The provider's message on a failed refresh. Quiet and beside the
        # breadcrumb: the map keeps its last good tiles, so this is a footnote
        # on what is shown, not a replacement for it. Never hidden, only
        # emptied: it is also the stretch that holds View at the right edge.
        self.note = QLabel("", self)
        self.note.setStyleSheet(f"color: {FG_DIM}; font-size: {FONT_SM}px;")
        self.note.setMinimumWidth(1)
        self.note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.note, 1)
        self.view_btn = QPushButton("View", self)
        self.view_btn.setToolTip("Choose what the map shows")
        self._view_menu = self._build_view_menu()
        self.view_btn.setMenu(self._view_menu)
        row.addWidget(self.view_btn)
        self.content_layout.addLayout(row)

        self.map = _Map(self)
        self.map.tile_clicked.connect(self.set_symbol)
        self.map.tile_activated.connect(self._on_tile_activated)
        self.content_layout.addWidget(self.map, 1)
        self.legend = _Legend(self)
        self.content_layout.addWidget(self.legend)

        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(WATCH_RECHECK_MS)
        self._watch_timer.timeout.connect(self._resolve_watchlist)
        PortfolioStore.instance().changed.connect(self._on_store_changed)
        FxService.instance().changed.connect(self._on_fx_changed)

        self._apply_view(animate=False)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # a closed panel awaiting deleteLater must not redraw or resubscribe
        self._watch_timer.stop()
        self._set_watchlist(None)
        try:
            PortfolioStore.instance().changed.disconnect(self._on_store_changed)
        except (RuntimeError, TypeError):
            pass  # already disconnected / never connected
        try:
            FxService.instance().changed.disconnect(self._on_fx_changed)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    # -- the view -------------------------------------------------------------

    @property
    def view(self) -> str:
        return self._view

    def set_view(self, view: str) -> None:
        clean = clean_view(view) or "sectors"
        if clean == self._view:
            return
        self._view = clean
        self._apply_view(animate=True)

    def go_back(self) -> None:
        if self._view.startswith("sector:"):
            self.set_view("sectors")

    def _build_view_menu(self) -> QMenu:
        menu = QMenu(self)
        group = QActionGroup(menu)
        self._view_actions: dict[str, Any] = {}

        def add(target: QMenu, text: str, view: str) -> None:
            act = target.addAction(text)
            act.setCheckable(True)
            group.addAction(act)
            act.triggered.connect(lambda _=False, v=view: self.set_view(v))
            self._view_actions[view] = act

        add(menu, "Sectors", "sectors")
        sector_menu = menu.addMenu("Sector")
        for key, label, _etf in SECTORS:
            add(sector_menu, label, f"sector:{key}")
        index_menu = menu.addMenu("Index")
        for key, (label, _n, _members) in INDEXES.items():
            add(index_menu, label, f"index:{key}")
        menu.addSeparator()
        add(menu, "Portfolio", "portfolio")
        add(menu, "Watchlist", "watchlist")
        add(menu, "Custom", "custom")
        menu.aboutToShow.connect(self._check_current_view)
        return menu

    def _check_current_view(self) -> None:
        for view, act in self._view_actions.items():
            act.setChecked(view == self._view)

    def _crumb_text(self) -> str:
        if self._view.startswith("sector:"):
            return f"Sectors › {_SECTOR_LABELS[self._view[7:]]}"
        if self._view.startswith("index:"):
            return view_label(self._view[6:])
        return view_label(self._view)

    def _apply_view(self, animate: bool) -> None:
        drilled = self._view.startswith("sector:")
        self.back_btn.setVisible(drilled)
        self.crumb.setText(self._crumb_text())
        self._errors.clear()
        if self._view == "watchlist":
            self._watch_timer.start()
            self._resolve_watchlist(force=True)
        else:
            self._watch_timer.stop()
            self._set_watchlist(None)
            self._refresh()
        if animate:
            self._fade_in_map()

    def _fade_in_map(self) -> None:
        """The new map rises from ``PAGE_IN_FROM``, as a tab's page does.
        Opacity only: the tiles are laid out at their final size at once."""
        if not motion.animations_enabled() or not self.map.isVisible():
            return
        motion.fade_animation(self.map)  # make sure the effect exists to seed it
        self.map._aurantium_fade_effect.setOpacity(
            min(motion.fade_opacity(self.map), motion.PAGE_IN_FROM)
        )
        motion.fade(self.map, 1.0, ms=motion.OVERLAY_MS)

    def _on_tile_activated(self, tile: Tile) -> None:
        if self._view == "sectors" and tile.sector_key:
            self.set_view(f"sector:{tile.sector_key}")

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Backspace and self._view.startswith("sector:"):
            self.go_back()
            event.accept()
            return
        super().keyPressEvent(event)

    def on_symbol(self, symbol: str) -> None:
        self.map.set_current(symbol)

    # -- the group's watchlist ------------------------------------------------

    def set_link_group(self, group: str) -> None:
        super().set_link_group(group)
        if getattr(self, "_view", None) == "watchlist":
            self._resolve_watchlist(force=True)

    def _resolve_watchlist(self, force: bool = False) -> None:
        """Point the Watchlist view at the first-opened Watchlist in this
        panel's group. Run on a group change, on that Watchlist's
        ``symbols_changed`` (which a group change of its own also fires), and
        once a second while the view is up — a Watchlist opened later, or
        closed, announces itself to nobody."""
        if self._view != "watchlist":
            return
        found = WatchlistPanel.in_group(self.link_group)
        changed = found is not self._watchlist
        if changed:
            self._set_watchlist(found)
        if changed or force:
            self._refresh()

    def _set_watchlist(self, w: Optional[WatchlistPanel]) -> None:
        old, self._watchlist = self._watchlist, w
        if old is not None and old is not w and isValid(old):
            try:
                old.symbols_changed.disconnect(self._on_watchlist_changed)
            except (RuntimeError, TypeError):
                pass
        if w is not None and w is not old:
            w.symbols_changed.connect(self._on_watchlist_changed)

    def _on_watchlist_changed(self) -> None:
        self._resolve_watchlist(force=True)

    def _mixed_symbols(self) -> list[str]:
        if self._view == "custom":
            syms = [s for _label, s in self._custom]
        else:
            w = self._watchlist
            syms = w.symbols() if w is not None and isValid(w) else []
        return list(dict.fromkeys(s.strip().upper() for s in syms if s.strip()))

    # -- subscriptions --------------------------------------------------------

    def _open_symbols(self) -> list[str]:
        positions = PortfolioStore.instance().open_positions()
        return list(dict.fromkeys(
            str(p.get("symbol") or "").strip().upper() for p in positions if p.get("symbol")
        ))

    def _wanted_topics(self) -> list[str]:
        v = self._view
        if v == "sectors":
            return [f"quote:{etf}" for _k, _l, etf in SECTORS] + ["sectorcap:all"]
        if v.startswith("sector:"):
            return [f"screen:sector:{v[7:]}:{SECTOR_N}"]
        if v.startswith("index:"):
            return [symbols_topic(INDEXES[v[6:]][2])]
        if v == "portfolio":
            return [f"quote:{s}" for s in self._open_symbols()]
        syms = self._mixed_symbols()
        # every symbol's quote too: the screener only knows the 250 largest US
        # caps, and an ETF, a future or a small cap must still be coloured
        return ([symbols_topic(syms)] + [f"quote:{s}" for s in syms]) if syms else []

    def _refresh(self) -> None:
        """Bring the subscriptions in line with the view, then redraw once.

        Diffed rather than dropped and re-made: a Watchlist edit that adds one
        symbol subscribes one topic, and the other tiles keep their colour."""
        wanted = list(dict.fromkeys(self._wanted_topics()))
        self._syncing = True  # cached values arrive inside subscribe(): draw once, after
        try:
            for topic in [t for t in self._topics if t not in wanted]:
                self._hub.unsubscribe(self, topic)
                self._topics.discard(topic)
                self._errors.pop(topic, None)
            for topic in wanted:
                if topic not in self._topics:
                    self.subscribe(
                        topic,
                        lambda data, t=topic: self._on_data(t, data),
                        lambda err, t=topic: self._on_error(t, err),
                    )
        finally:
            self._syncing = False
        self._render()

    def _on_data(self, topic: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        if topic.startswith("quote:"):
            sym = topic[6:]
            self._quotes[sym] = data
            FxService.instance().note_quote(sym, data)
        elif topic == "sectorcap:all":
            self._caps = {k: v for k, v in data.items() if isinstance(v, (int, float))}
        elif topic.startswith("screen:"):
            rows = data.get("rows")
            if not isinstance(rows, list):
                return
            self._screens[topic] = rows
        self._errors.pop(topic, None)
        if not self._syncing:
            self._render()

    def _on_error(self, topic: str, message: str) -> None:
        """Keep the last good tiles; say what went wrong beside the breadcrumb."""
        self._errors[topic] = str(message or "refresh failed")
        if not self._syncing:
            self._render()

    def _show_note(self, has_tiles: bool) -> None:
        # with no tiles to annotate, the map's own empty state says it instead
        msg = next(reversed(self._errors.values())) if self._errors and has_tiles else ""
        self.note.setText(f"{WARN_GLYPH} {msg}" if msg else "")
        self.note.setToolTip(msg)

    def _on_store_changed(self) -> None:
        if self._view == "portfolio":
            self._refresh()

    def _on_fx_changed(self) -> None:
        if self._view == "portfolio":
            self._render()

    # -- tiles ----------------------------------------------------------------

    def _tiles(self) -> list[Tile]:
        v = self._view
        if v == "sectors":
            return tiles_sectors(self._quotes, self._caps)
        if v.startswith("sector:"):
            rows = self._screens.get(f"screen:sector:{v[7:]}:{SECTOR_N}")
            return tiles_from_screen(rows, SECTOR_N) if rows else []
        if v.startswith("index:"):
            _label, n, members = INDEXES[v[6:]]
            rows = self._screens.get(symbols_topic(members))
            return tiles_from_screen(rows, n) if rows else []
        if v == "portfolio":
            fx = FxService.instance()
            return tiles_portfolio(
                PortfolioStore.instance().open_positions(), self._quotes,
                lambda s, a: fx.value(s, a),
            )
        syms = self._mixed_symbols()
        if not syms:
            return []
        rows = self._screens.get(symbols_topic(syms)) or []
        return tiles_mixed(syms, rows, self._quotes)

    def _empty_message(self) -> tuple[str, str, bool]:
        """``(title, hint, loading)`` for a map with no tiles."""
        v = self._view
        if v == "watchlist":
            w = self._watchlist
            if self.link_group == UNLINKED:
                return ("No Watchlist — this panel is unlinked",
                        "link it to a group that has a Watchlist", False)
            if w is None or not isValid(w):
                return (f"No Watchlist in group {self.link_group}",
                        "open one, or link this panel to a group that has one", False)
            return ("Watchlist is empty",
                    f"add symbols to the Watchlist in group {self.link_group}", False)
        if v == "custom":
            return "No tiles yet", "right-click to add", False
        if v == "portfolio":
            return "No open positions", "add one in the Portfolio panel", False
        if self._errors:
            return f"{WARN_GLYPH} {next(reversed(self._errors.values()))}", "", False
        return "", "", True

    def _render(self) -> None:
        tiles = self._tiles()
        self._show_note(bool(tiles))
        self.map.set_tiles(tiles)
        if tiles:
            self.map.clear_empty()
        else:
            title, hint, loading = self._empty_message()
            self.map.set_empty(title, hint, loading)
        # the header's loading state (and, on a workspace restore, the panel
        # skeleton) while the view has nothing real to show yet
        waiting = bool(self._topics) and not self._errors and (
            not tiles or all(t.loading for t in tiles)
        )
        if waiting != self._waiting:
            self._waiting = waiting
            self.set_loading(waiting)

    def empty_text(self) -> str:
        """What the map's empty state says, title and hint; "" when it has tiles."""
        if self.map.empty.isHidden():
            return ""
        if self.map.empty._loading:
            return "Loading…"
        e = self.map.empty
        return f"{e.title} — {e.hint}" if e.hint else e.title

    # -- persistence ----------------------------------------------------------

    def settings(self) -> dict:
        return {"view": self._view}

    def restore(self, settings: dict) -> None:
        view = settings.get("view") if isinstance(settings, dict) else None
        self.set_view(clean_view(view) or "sectors")
