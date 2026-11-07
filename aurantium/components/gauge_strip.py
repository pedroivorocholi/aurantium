"""GaugeStrip — the row of compact gauges across the top of Macro / Rates.

Each tile reads, top to bottom: the gauge's name (small, muted), its value
(large, tabular, in the data ink) with today's signed change beside it, a
one-month sparkline, and an optional note line ("as of Sep", "inverted",
"needs a FRED key"). Until its first reading a tile shows a skeleton.

One painted widget for the whole strip, like the Heatmap's map: a splitter drag
re-wrapping eight tiles is one layout pass and one paint, not eight widgets
being moved, and a reorder can glide every tile at once from one animation.

Editing happens in place, never in a dialog:

* a trailing **+** tile emits ``add_requested``; the panel opens its picker and
  calls ``add_gauge``;
* right-click → **Replace with…** (``replace_requested``, then the panel calls
  ``replace_gauge``) or **Remove**;
* drag a tile past the system drag distance to reorder — a thin accent bar
  marks where it will land, Escape puts it back.

A click on a tile (a press and release on it, never a drag) still emits
``gauge_clicked`` and also **opens the gauge into a detail card**
(``GaugeCard``): a full-width row of its own right under the tile's row, with
a chart, range chips, the range's high and low and the source. The rows below
slide down to make room — nothing is hidden. One card at a time; a click on
the same tile, on the card away from its controls, or Escape (with the strip
or card focused) closes it. The card is view state: not saved, not an undo
step, and it follows its gauge through a reorder. The panel fills it
(``set_detail``) after ``expansion_changed`` / ``detail_range_changed``.

``add_gauge`` / ``replace_gauge`` / ``remove_gauge`` and a drop are *user*
edits: each emits ``gauges_changed`` with the new list so the panel can push an
undo step. ``set_gauges`` is the panel talking (a restore, an undo) and never
emits. Tiles glide to their new slots when animations are on and jump when
they are off; the logical state is final the moment an edit returns either
way — only the painting travels.

The strip knows nothing about data. The panel resolves ``*`` (the current
country) in rate gauges, feeds ``set_reading``, and can rename a tile for
display with ``set_label`` ("10Y" shown as "UST 10Y" or "Bund 10Y").
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Optional, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QScrollArea, QSizePolicy, QToolTip, QWidget

from .. import motion
from ..macro_model import tenor_label
from .gauge_card import DEFAULT_RANGE, GaugeCard, GaugeDetail
from ..theme import (
    ACCENT,
    BG_ALT,
    BORDER_STRONG,
    CHROME_LOW,
    DOWN,
    FG_DIM,
    FG_MUTED,
    FONT_MD,
    FONT_SM,
    FONT_TITLE,
    FONT_XS,
    GAUGE_CARD_PX,
    GAUGE_TILE_MIN_PX,
    ICON_PX,
    LABEL,
    MONO_FONT,
    RADIUS_MD,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    UI_FONT,
    UP,
    WEIGHT_MEDIUM,
)

GAUGE_KINDS = ("quote", "fred", "curve", "policy")


@dataclass(frozen=True)
class Gauge:
    """What a tile measures. ``kind`` is one of ``GAUGE_KINDS``; ``code`` is a
    Yahoo symbol, a FRED series id, ``"<CC>:<years>"`` / ``"<CC>:<short>-<long>"``
    for a curve point or spread, or the country for a policy rate. ``*`` as
    the country means "whichever country the panel is on"."""

    kind: str
    code: str
    label: str

    @property
    def sends_symbol(self) -> bool:
        """Whether a click has a symbol to send to the link group. Only market
        quotes do; a policy rate, a curve point or a spread is not a ticker."""
        return self.kind == "quote"


#: The strip a new Macro panel opens with (Global order). The three rate
#: gauges follow the panel's country; the market gauges are global.
DEFAULT_GAUGES: tuple[Gauge, ...] = (
    Gauge("policy", "*", "Policy rate"),
    Gauge("curve", "*:10", tenor_label(10)),
    Gauge("curve", "*:2-10", "2s10s"),
    Gauge("quote", "DX-Y.NYB", "Dollar Index"),
    Gauge("quote", "^VIX", "VIX"),
    Gauge("quote", "^MOVE", "MOVE"),
    Gauge("quote", "CL=F", "WTI"),
    Gauge("quote", "GC=F", "Gold"),
)


@dataclass(frozen=True)
class Reading:
    value: str
    change: str
    change_sign: int
    spark: tuple[float, ...]
    note: str = ""
    note_tone: str = ""  # one of NOTE_TONES


#: How a note line is inked: plain, or "down" for a state worth a glance
#: ("inverted") — the loss colour, the one the change figure already uses.
NOTE_TONES = ("", "down")


class _Tile:
    """One gauge on screen. ``uid`` survives reorders and ``set_gauges`` so an
    animation can tell where each tile came from."""

    __slots__ = ("gauge", "reading", "label", "uid")
    _ids = itertools.count(1)

    def __init__(self, gauge: Gauge) -> None:
        self.gauge = gauge
        self.reading: Optional[Reading] = None
        self.label: str = ""  # display override; "" = the gauge's own label
        self.uid = next(self._ids)

    @property
    def shown_label(self) -> str:
        return self.label or self.gauge.label


_PLUS = 0  # the + tile's uid in animation snapshots (tile uids start at 1)
_CARD = -1  # the open card's key in animation snapshots

#: Tile geometry. Gaps match the dense step the rest of the terminal uses.
_GAP = SPACE_MD
_PAD_X = SPACE_LG
_PAD_Y = SPACE_MD
_SPARK_H = SPACE_XL + SPACE_SM
_PLUS_W = ICON_PX + 2 * SPACE_LG
#: A row stops sharing out width past this: two gauges on a wide panel stay
#: gauges, not banners.
_TILE_MAX_PX = GAUGE_TILE_MIN_PX * 2
_NO_SYMBOL_HINT = "Nothing to send to linked panels: a click only opens its detail."
_EDIT_HINT = "Click for detail · drag to reorder · right-click to replace or remove"
_OPEN_HINT = "Click to close its detail · drag to reorder · right-click to replace or remove"


def _font(family: str, px: int, weight: int = 0) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    if weight:
        f.setWeight(QFont.Weight(weight))
    return f


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(
        a.x() + (b.x() - a.x()) * t,
        a.y() + (b.y() - a.y()) * t,
        a.width() + (b.width() - a.width()) * t,
        a.height() + (b.height() - a.height()) * t,
    )


class GaugeStrip(QWidget):
    #: how long ``reveal`` keeps a tile outlined: long enough to find it,
    #: not motion (it does not move or fade), so the same with animations off
    REVEAL_HOLD_MS = 1200

    gauge_clicked = Signal(object)    # Gauge
    gauges_changed = Signal(list)     # list[Gauge], after every user edit
    add_requested = Signal()
    replace_requested = Signal(int)   # tile index
    expansion_changed = Signal(object)  # the Gauge opened into its card, or None
    detail_range_changed = Signal(str)  # a range chip picked on the open card

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        # focus on press, so Escape reaches a drag in progress
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        self._f_name = _font(UI_FONT, FONT_SM)
        self._f_value = _font(MONO_FONT, FONT_TITLE, WEIGHT_MEDIUM)
        self._f_change = _font(MONO_FONT, FONT_MD)
        self._f_note = _font(UI_FONT, FONT_XS)
        self._fm_name = QFontMetrics(self._f_name)
        self._fm_value = QFontMetrics(self._f_value)
        self._fm_change = QFontMetrics(self._f_change)
        self._fm_note = QFontMetrics(self._f_note)
        self._tile_h = (
            2 * _PAD_Y + self._fm_name.height() + SPACE_XS + self._fm_value.height()
            + SPACE_XS + _SPARK_H + SPACE_XS + self._fm_note.height()
        )

        self._tiles: list[_Tile] = []
        self._rects: list[QRect] = []   # layout targets, one per tile
        self._plus = QRect()
        self._laid_width = -1
        self._rows = 0

        self._hover = -1                # tile index; -2 = the + tile
        self._press = -1                # tile index pressed; -2 = the + tile
        self._press_pos = QPoint()
        self._drag = -1                 # tile index being dragged
        self._drag_offset = QPoint()
        self._drag_pos = QPoint()
        self._slot = -1

        # one animation drives every tile: 0 -> 1, each tile interpolating
        # from where it was painted to where it now belongs
        self._t = 1.0
        self._from: dict[int, QRectF] = {}
        self._fade_in: set[int] = set()
        self._ghosts: list[tuple[_Tile, QRectF]] = []
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve(motion.EASE_TRAVEL))
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._settle)

        self._revealed = -1
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._end_reveal)

        # the detail card: which gauge is open, and how far each opening
        # under a row is drawn (0..1 of the card's height, by tile uid)
        self._expanded_uid = -1          # the open gauge; -1 = none
        self._card_uid = -1              # what the card shows (stays while closing)
        self._open: dict[int, float] = {}
        self._open_from: dict[int, float] = {}
        self._open_to: dict[int, float] = {}
        self._open_anim = QVariantAnimation(self)
        self._open_anim.setStartValue(0.0)
        self._open_anim.setEndValue(1.0)
        self._open_anim.setEasingCurve(QEasingCurve(motion.EASE_TRAVEL))
        self._open_anim.valueChanged.connect(self._on_open_value)
        self._open_anim.finished.connect(self._settle_open)
        self._ranges: dict[tuple[str, str], str] = {}  # this session's chip per gauge
        self._card: Optional[GaugeCard] = None  # made on first open: a chart costs
        self._clip: Optional[QWidget] = None    # the card's window while it grows
        self._card_geom = QRect()        # the card's slot
        self._openings: dict[int, QRect] = {}  # every opening drawn, by uid
        #: a card closing under one row while it opens under another: a still
        #: of what it showed, drawn into the closing opening (no empty band)
        self._closing: Optional[tuple[int, QPixmap]] = None
        self._follow = False             # keep the opening card in view
        self._laid_height = -1

        # press feedback sinks just the pressed tile (see press.py)
        self.__dict__["press_region"] = self._press_region

    # -- content (the panel talking: never emits) -----------------------------

    def set_gauges(self, gauges: Sequence[Gauge]) -> None:
        """Replace the whole strip. A gauge already on the strip keeps its
        reading and display label, and glides to its new slot."""
        before = self._snapshot()
        pool = list(self._tiles)
        tiles: list[_Tile] = []
        for g in gauges:
            match = next((t for t in pool if (t.gauge.kind, t.gauge.code) == (g.kind, g.code)), None)
            if match is not None:
                pool.remove(match)
                match.gauge = g
                tiles.append(match)
            else:
                tiles.append(_Tile(g))
        animate = bool(self._tiles)
        self._tiles = tiles
        self._end_drag()
        self._after_edit(before, pool if animate else [], animate=animate)

    def gauges(self) -> list[Gauge]:
        return [t.gauge for t in self._tiles]

    def set_reading(self, index: int, value: str, change: str, change_sign: int,
                    spark: Sequence[float], note: str = "", note_tone: str = "") -> None:
        if not 0 <= index < len(self._tiles):
            return
        clean = []
        for v in spark or ():
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue  # a gap in the history is skipped, not fatal
            if math.isfinite(f):
                clean.append(f)
        sign = (change_sign > 0) - (change_sign < 0)
        tone = note_tone if note_tone in NOTE_TONES else ""
        self._tiles[index].reading = Reading(str(value), str(change or ""), sign, tuple(clean),
                                              str(note or ""), tone)
        self._sync_card_heading()
        self.update()

    def clear_reading(self, index: int) -> None:
        """Put tile ``index`` back to its skeleton: what it showed no longer
        stands (the panel switched country and the new data is on its way)."""
        if 0 <= index < len(self._tiles) and self._tiles[index].reading is not None:
            self._tiles[index].reading = None
            self._sync_card_heading()
            self.update()

    def reveal(self, index: int) -> None:
        """Outline tile ``index`` for a moment: "it's already here"."""
        if not 0 <= index < len(self._tiles):
            return
        self._revealed = index
        self._reveal_timer.start(self.REVEAL_HOLD_MS)
        self.update()

    def highlighted(self) -> int:
        """The tile ``reveal`` is outlining, else -1."""
        return self._revealed

    def _end_reveal(self) -> None:
        self._revealed = -1
        self.update()

    def set_label(self, index: int, text: str) -> None:
        """Show tile ``index`` under ``text`` without changing its gauge
        ("10Y" as "UST 10Y"). Empty text goes back to the gauge's own label."""
        if 0 <= index < len(self._tiles):
            self._tiles[index].label = str(text or "")
            self._sync_card_heading()
            self.update()

    # -- the detail card (view state: never an edit, never saved) --------------

    def expand(self, index: int) -> None:
        """Open tile ``index`` into its detail card, closing any other."""
        if not 0 <= index < len(self._tiles):
            return
        tile = self._tiles[index]
        if tile.uid == self._expanded_uid:
            return
        card = self._ensure_card()
        before = self._card_uid
        still = None
        if (before >= 0 and self._clip.isVisible() and motion.animations_enabled()
                and self._row_of(before) != self._row_of(tile.uid)):
            still = card.grab()  # before the card turns to the new gauge
        self._expanded_uid = self._card_uid = tile.uid
        self._follow = True
        card.set_loading(self.detail_range())
        self._sync_card_heading()
        self._animate_open(tile.uid, before, still)
        self.expansion_changed.emit(tile.gauge)

    def collapse(self) -> None:
        if self._expanded_uid < 0:
            return
        self._expanded_uid = -1
        self._hand_focus_back()
        self._follow = False
        self._animate_open(-1, self._card_uid)
        self.expansion_changed.emit(None)

    def toggle(self, index: int) -> None:
        if 0 <= index < len(self._tiles) and self._tiles[index].uid == self._expanded_uid:
            self.collapse()
        else:
            self.expand(index)

    def expanded_index(self) -> int:
        return next((i for i, t in enumerate(self._tiles) if t.uid == self._expanded_uid), -1)

    def expanded_gauge(self) -> Optional[Gauge]:
        i = self.expanded_index()
        return self._tiles[i].gauge if i >= 0 else None

    def detail_range(self) -> str:
        """The range chip the open gauge last had this session (1M at first)."""
        g = self.expanded_gauge()
        return self._ranges.get((g.kind, g.code), DEFAULT_RANGE) if g is not None else DEFAULT_RANGE

    def set_detail(self, detail: GaugeDetail) -> None:
        """The panel filling the open card."""
        if self._card is not None and self._expanded_uid >= 0:
            self._card.set_detail(detail)

    def clear_detail(self) -> None:
        """The open card back to loading: what it showed no longer stands."""
        if self._card is not None and self._expanded_uid >= 0:
            self._card.set_loading(self.detail_range())

    def card(self) -> GaugeCard:
        return self._ensure_card()

    def card_rect(self) -> QRect:
        """The card's slot: where it is (zero height when closed), as far
        open as it is so far."""
        self._ensure_layout()
        return QRect(self._card_geom)

    def painted_card_rect(self) -> QRect:
        """Where the card is drawn right now: its slot, unless it is still
        gliding there with its tile after a reorder."""
        self._ensure_layout()
        if self._card_geom.height() <= 0:
            return QRect()
        return self._painted(_CARD, self._card_geom).toAlignedRect()

    def closing_card_rect(self) -> QRect:
        """Where a card switched to another row is still closing (drawn from
        a still of what it showed), else a null rect."""
        if self._closing is None:
            return QRect()
        r = self._openings.get(self._closing[0])
        return QRect(r) if r is not None and r.height() > 0 else QRect()

    def _ensure_card(self) -> GaugeCard:
        if self._card is None:
            self._clip = QWidget(self)
            self._clip.hide()
            self._card = GaugeCard(self._clip)
            self._card.range_picked.connect(self._on_card_range)
            self._card.close_requested.connect(self.collapse)
        return self._card

    def _on_card_range(self, name: str) -> None:
        g = self.expanded_gauge()
        if g is None:
            return
        self._ranges[(g.kind, g.code)] = name
        self._card.set_loading(name)  # the panel answers with the range's data
        self.detail_range_changed.emit(name)

    def _sync_card_heading(self) -> None:
        if self._card is None or self._card_uid < 0:
            return
        tile = next((t for t in self._tiles if t.uid == self._card_uid), None)
        if tile is None:
            return
        r = tile.reading
        if r is None:
            self._card.set_heading(tile.shown_label, "…", "", 0)
        else:
            self._card.set_heading(tile.shown_label, r.value, r.change, r.change_sign,
                                   r.note, r.note_tone)

    def _hand_focus_back(self) -> None:
        """A closing card must not take the keyboard focus away with it."""
        focus = QApplication.focusWidget()
        if self._clip is not None and focus is not None and self._clip.isAncestorOf(focus):
            self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _row_of(self, uid: int) -> int:
        self._ensure_layout()
        i = next((k for k, t in enumerate(self._tiles) if t.uid == uid), -1)
        return self._rects[i].top() if 0 <= i < len(self._rects) else -1

    def _animate_open(self, uid: int, before: int, still: Optional[QPixmap] = None) -> None:
        """Open the card under ``uid``'s row (or close it, ``uid`` -1). A
        switch between tiles of one row keeps the opening where it is; to
        another row, the old opening closes over a still of the old card
        while the new one opens, so nothing is left blank."""
        if uid >= 0 and before >= 0 and before != uid and before in self._open:
            if self._row_of(before) == self._row_of(uid):
                self._open[uid] = self._open.pop(before)
        target = {uid: 1.0} if uid >= 0 else {}
        self._open_anim.stop()
        if not (motion.animations_enabled() and self.isVisible()):
            self._closing = None
            self._open = dict(target)
            self._settle_open()
            return
        self._closing = (before, still) if still is not None and before in self._open else None
        self._open_from = dict(self._open)
        self._open_to = target
        self._open_anim.setDuration(motion.CARD_GROW_MS)
        self._open_anim.start()

    def _on_open_value(self, value) -> None:
        t = float(value)
        keys = set(self._open_from) | set(self._open_to)
        self._open = {u: self._open_from.get(u, 0.0)
                      + (self._open_to.get(u, 0.0) - self._open_from.get(u, 0.0)) * t
                      for u in keys}
        self._relayout()
        self.update()
        if self._follow:
            self._follow_card()

    def _settle_open(self) -> None:
        if self._open_anim.state() == QVariantAnimation.State.Running:
            return
        if self._expanded_uid >= 0:
            self._open = {self._expanded_uid: 1.0}
        else:
            self._open = {}
            self._card_uid = -1
        self._open_from, self._open_to = {}, {}
        self._closing = None
        self._relayout()
        self.update()
        if self._follow:
            # once the panel's layout has taken the new height
            self._follow = False
            QTimer.singleShot(0, self, self._follow_card)

    def _scroll_area(self) -> Optional[QScrollArea]:
        w = self.parentWidget()
        while w is not None:
            if isinstance(w, QScrollArea) and w.widget() is not None and w.widget().isAncestorOf(self):
                return w
            w = w.parentWidget()
        return None

    def _follow_card(self) -> None:
        """Scroll the nearest scroll area so the open card, and the tile it
        belongs to, are in view. Called on every frame of the opening, so
        the view travels with the card; once at the end."""
        k = self.expanded_index()
        sa = self._scroll_area()
        if k < 0 or sa is None or self._card_geom.height() <= 0:
            return
        body = sa.widget()
        if body.layout() is not None:
            body.layout().activate()
        self._ensure_layout()
        top = self.mapTo(body, QPoint(0, self._rects[k].top())).y()
        bottom = self.mapTo(body, QPoint(0, self._card_geom.bottom())).y()
        bar = sa.verticalScrollBar()
        view = sa.viewport().height()
        v = bar.value()
        if bottom > v + view - 1:
            v = bottom - view + 1
        v = min(v, top)  # the tile itself never scrolls out of the top
        if v != bar.value():
            bar.setValue(v)

    def _drop_missing_expansion(self) -> None:
        """The open gauge left the strip (a remove, an undo): close at once."""
        uids = {t.uid for t in self._tiles}
        self._open = {u: f for u, f in self._open.items() if u in uids}
        self._open_from = {u: f for u, f in self._open_from.items() if u in uids}
        self._open_to = {u: f for u, f in self._open_to.items() if u in uids}
        if self._card_uid >= 0 and self._card_uid not in uids:
            self._card_uid = -1
        if self._closing is not None and self._closing[0] not in uids:
            self._closing = None
        if self._expanded_uid >= 0 and self._expanded_uid not in uids:
            self._expanded_uid = -1
            self._hand_focus_back()
            self._open_anim.stop()
            self._open = {}
            self._open_from, self._open_to = {}, {}
            self.expansion_changed.emit(None)

    # -- user edits (each emits gauges_changed) ------------------------------

    # Each edit snapshots first, so a tile lifted mid-drag glides from the
    # pointer, then ends the drag: its index would otherwise point at
    # whichever tile now sits in that slot.

    def add_gauge(self, gauge: Gauge) -> None:
        before = self._snapshot()
        self._end_drag()
        self._tiles.append(_Tile(gauge))
        self._after_edit(before, [])
        self._emit_changed()

    def replace_gauge(self, index: int, gauge: Gauge) -> None:
        if not 0 <= index < len(self._tiles):
            return
        before = self._snapshot()
        self._end_drag()
        old = self._tiles[index]
        self._tiles[index] = _Tile(gauge)
        self._after_edit(before, [old])
        self._emit_changed()

    def remove_gauge(self, index: int) -> None:
        if not 0 <= index < len(self._tiles):
            return
        before = self._snapshot()
        self._end_drag()
        old = self._tiles.pop(index)
        self._after_edit(before, [old])
        self._emit_changed()

    def move_gauge(self, index: int, slot: int) -> bool:
        """Move tile ``index`` to sit before slot ``slot`` (0..len). False, and
        no edit, when that leaves it where it was."""
        n = len(self._tiles)
        if not 0 <= index < n or not 0 <= slot <= n or slot in (index, index + 1):
            return False
        before = self._snapshot()
        self._end_drag()
        tile = self._tiles.pop(index)
        self._tiles.insert(slot - 1 if slot > index else slot, tile)
        self._after_edit(before, [])
        self._emit_changed()
        return True

    def _emit_changed(self) -> None:
        self.gauges_changed.emit(self.gauges())

    # -- queries (the panel and tests read the strip through these) ----------

    def reading(self, index: int) -> Optional[Reading]:
        return self._tiles[index].reading if 0 <= index < len(self._tiles) else None

    def note_ink(self, index: int) -> str:
        """The colour tile ``index``'s note is drawn in."""
        reading = self.reading(index)
        return DOWN if reading is not None and reading.note_tone == "down" else FG_DIM

    def is_loading(self, index: int) -> bool:
        return 0 <= index < len(self._tiles) and self._tiles[index].reading is None

    def display_label(self, index: int) -> str:
        return self._tiles[index].shown_label if 0 <= index < len(self._tiles) else ""

    def tile_rect(self, index: int) -> QRect:
        self._ensure_layout()
        return QRect(self._rects[index]) if 0 <= index < len(self._rects) else QRect()

    def plus_rect(self) -> QRect:
        self._ensure_layout()
        return QRect(self._plus)

    def painted_rect(self, index: int) -> QRect:
        """Where tile ``index`` is drawn right now (its slot, unless it is
        still gliding there)."""
        self._ensure_layout()
        if not 0 <= index < len(self._tiles):
            return QRect()
        return self._painted(self._tiles[index].uid, self._rects[index]).toAlignedRect()

    def index_at(self, pos) -> int:
        """Tile index under ``pos``; -2 for the + tile; -1 for neither."""
        self._ensure_layout()
        for i, r in enumerate(self._rects):
            if r.contains(pos):
                return i
        return -2 if self._plus.contains(pos) else -1

    def tooltip_at(self, pos) -> str:
        i = self.index_at(pos)
        if i == -2:
            return "Add a gauge"
        if i < 0:
            return ""
        tile = self._tiles[i]
        lines = [tile.shown_label]
        if tile.label and tile.gauge.label not in tile.label:
            lines[0] += f" ({tile.gauge.label})"
        if tile.reading is not None and tile.reading.note:
            lines.append(tile.reading.note)
        if not tile.gauge.sends_symbol:
            lines.append(_NO_SYMBOL_HINT)
        lines.append(_OPEN_HINT if tile.uid == self._expanded_uid else _EDIT_HINT)
        return "\n".join(lines)

    def is_dragging(self) -> bool:
        return self._drag >= 0

    def drop_slot(self) -> int:
        return self._slot if self._drag >= 0 else -1

    def drop_indicator_rect(self) -> QRect:
        """The accent bar marking where a dragged tile will land; null when
        no drag is under way or the drop would leave it where it is."""
        if self._drag < 0 or self._slot < 0 or self._slot in (self._drag, self._drag + 1):
            return QRect()
        self._ensure_layout()
        n = len(self._rects)
        if self._slot < n:
            anchor = self._rects[self._slot]
            x = anchor.left() - _GAP // 2
        else:
            anchor = self._rects[-1]
            x = anchor.right() + 1 + _GAP // 2
        x = max(0, min(self.width() - SPACE_XS, x - SPACE_XS // 2))
        return QRect(x, anchor.top(), SPACE_XS, anchor.height())

    def row_count(self, width: int | None = None) -> int:
        return self._grid(self.width() if width is None else width)[2]

    def is_animating(self) -> bool:
        running = QVariantAnimation.State.Running
        return self._anim.state() == running or self._open_anim.state() == running

    def finish_animation(self) -> None:
        if self._anim.state() != QVariantAnimation.State.Stopped:
            self._anim.stop()
        self._settle()
        if self._open_anim.state() != QVariantAnimation.State.Stopped:
            self._open_anim.stop()
            self._settle_open()

    # -- layout -----------------------------------------------------------------

    def _card_full_h(self, width: int) -> int:
        """The open card's height at ``width``: the token, or more when a
        narrow card wraps its chips and footer."""
        if self._card is None:
            return GAUGE_CARD_PX
        return max(GAUGE_CARD_PX, self._card.heightForWidth(width))

    def _grid(self, width: int) -> tuple[list[QRect], QRect, int, int, dict[int, QRect]]:
        """Tile rects, the + tile's rect, the row count, the total height and
        every opening's rect (by tile uid) at ``width``.

        Columns are as many minimum-width tiles as fit beside a reserved +
        column; the tiles of a row then share the leftover width (up to a
        cap), so the grid fills edge to edge and the columns line up across
        rows. The + tile sits right after the last gauge. Under a row with
        an open (or opening) gauge sits the card's own row, as tall as it is
        open so far; the rows below it move down by that much."""
        n = len(self._tiles)
        avail = max(GAUGE_TILE_MIN_PX, width - _PLUS_W - _GAP)
        cols = max(1, (avail + _GAP) // (GAUGE_TILE_MIN_PX + _GAP))
        cols = min(cols, max(1, n))
        tw = min(float(_TILE_MAX_PX), (avail - _GAP * (cols - 1)) / cols)
        th = self._tile_h
        rows = max(1, math.ceil(n / cols))
        full = self._card_full_h(width) if self._open else 0
        opening: dict[int, float] = {}
        for i, t in enumerate(self._tiles):
            f = self._open.get(t.uid, 0.0)
            if f > 0.0:
                r = i // cols
                opening[r] = max(opening.get(r, 0.0), f)
        tops: list[int] = []
        y = 0.0
        for r in range(rows):
            tops.append(round(y))
            y += th + _GAP + opening.get(r, 0.0) * (full + _GAP)
        height = max(th, round(y - _GAP))
        rects: list[QRect] = []
        for i in range(n):
            row, col = divmod(i, cols)
            x0 = round(col * (tw + _GAP))
            x1 = round(col * (tw + _GAP) + tw)
            rects.append(QRect(x0, tops[row], x1 - x0, th))
        if rects:
            last = rects[-1]
            plus = QRect(last.right() + 1 + _GAP, last.top(), _PLUS_W, th)
        else:
            plus = QRect(0, 0, _PLUS_W, th)
        openings: dict[int, QRect] = {}
        for i, t in enumerate(self._tiles):
            f = self._open.get(t.uid, 0.0)
            if f > 0.0:
                openings[t.uid] = QRect(0, tops[i // cols] + th + _GAP, width, round(f * full))
        return rects, plus, rows, height, openings

    def _ensure_layout(self) -> None:
        if self._laid_width != self.width() or len(self._rects) != len(self._tiles):
            self._relayout()

    def _relayout(self) -> None:
        self._rects, self._plus, rows, height, self._openings = self._grid(self.width())
        self._card_geom = QRect(self._openings.get(self._card_uid, QRect()))
        self._laid_width = self.width()
        self._rows = rows
        if height != self._laid_height:
            # the strip's height changed: ask the panel's layout for it
            self._laid_height = height
            self.updateGeometry()
        self._place_card()

    def _place_card(self) -> None:
        """The card sits at full size inside a window as tall as it is open
        so far, so it is uncovered like a drawer instead of being squeezed
        (its chart is laid out once, not on every frame)."""
        if self._clip is None:
            return
        if self._card_geom.height() <= 0 or self._card_uid < 0:
            self._clip.hide()
            return
        # after a reorder the card glides with its tile, like every tile
        geom = self._painted(_CARD, self._card_geom).toAlignedRect()
        self._clip.setGeometry(geom)
        full = max(geom.height(), self._card_full_h(geom.width()))
        if self._card.size() != QSize(geom.width(), full):
            self._card.setGeometry(0, 0, geom.width(), full)
        self._clip.show()
        self._clip.raise_()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return self._grid(width)[3]

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        n = max(1, len(self._tiles))
        w = n * (GAUGE_TILE_MIN_PX + _GAP) + _PLUS_W
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        w = GAUGE_TILE_MIN_PX + _GAP + _PLUS_W
        return QSize(w, self._tile_h)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # a new width mid-glide lands it: the targets just moved under it.
        # A new height alone is the card opening: the glide carries on.
        if event.oldSize().width() != event.size().width():
            if self._anim.state() != QVariantAnimation.State.Stopped:
                self._anim.stop()
            self._settle()
        self._relayout()

    # -- animation ----------------------------------------------------------------

    def _painted(self, uid: int, target: QRect) -> QRectF:
        start = self._from.get(uid)
        if start is None or self._t >= 1.0:
            return QRectF(target)
        return _lerp_rect(start, QRectF(target), self._t)

    def _snapshot(self) -> dict[int, QRectF]:
        """Where every tile (and the + tile) is drawn right now."""
        self._ensure_layout()
        shot = {t.uid: self._painted(t.uid, r) for t, r in zip(self._tiles, self._rects)}
        shot[_PLUS] = self._painted(_PLUS, self._plus)
        if self._card_geom.height() > 0:
            shot[_CARD] = self._painted(_CARD, self._card_geom)
        if self._drag >= 0:
            shot[self._tiles[self._drag].uid] = QRectF(self._lifted_rect())
        return shot

    def _after_edit(self, before: dict[int, QRectF], gone: list[_Tile],
                    animate: bool = True) -> None:
        self._revealed = -1  # the outlined index may now be another tile
        self._reveal_timer.stop()
        self._drop_missing_expansion()
        self._relayout()
        if self._hover >= len(self._tiles):
            self._hover = -1
        if not (animate and motion.animations_enabled() and self.isVisible()):
            self.finish_animation()
            self.update()
            return
        self._anim.stop()
        self._from = {uid: r for uid, r in before.items()}
        self._fade_in = {t.uid for t in self._tiles if t.uid not in before}
        self._ghosts = [(t, before[t.uid]) for t in gone if t.uid in before]
        self._t = 0.0
        self._anim.setDuration(motion.TILE_TRAVEL_MS)
        self._anim.start()
        self.update()

    def _on_anim_value(self, value) -> None:
        self._t = float(value)
        self._place_card()
        self.update()

    def _settle(self) -> None:
        self._t = 1.0
        self._from.clear()
        self._fade_in.clear()
        self._ghosts.clear()
        self._place_card()
        self.update()

    # -- dragging -------------------------------------------------------------------

    def _lifted_rect(self) -> QRect:
        r = QRect(self._rects[self._drag])
        r.moveTopLeft(self._drag_pos - self._drag_offset)
        return r

    def _slot_at(self, pos: QPoint) -> int:
        """The slot (0..len) a drop at ``pos`` lands in: before the tile whose
        left half it is over, after it on its right half; rows by height."""
        self._ensure_layout()
        if not self._rects:
            return 0
        tops = sorted({r.top() for r in self._rects})
        row_top = tops[0]
        for top in tops:
            if pos.y() >= top - _GAP:
                row_top = top
        row = [i for i, r in enumerate(self._rects) if r.top() == row_top]
        for i in row:
            if pos.x() < self._rects[i].center().x():
                return i
        return row[-1] + 1

    def _start_drag(self, pos: QPoint) -> None:
        self._drag = self._press
        self._drag_offset = self._press_pos - self._rects[self._drag].topLeft()
        self._drag_pos = pos
        self._slot = self._slot_at(pos)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        QToolTip.hideText()
        self.update()

    def _end_drag(self) -> None:
        self._drag = -1
        self._slot = -1
        self._press = -1
        self._hover = -1  # the next move works out what is under the pointer
        self.unsetCursor()

    def _cancel_drag(self) -> None:
        if self._drag < 0:
            return
        before = self._snapshot()
        self._end_drag()
        self._after_edit(before, [])

    # -- pointer and keys ---------------------------------------------------------

    def _press_region(self, pos):
        i = self.index_at(pos)
        if i == -2:
            return QRect(self._plus), RADIUS_MD
        return (QRect(self._rects[i]), RADIUS_MD) if i >= 0 else None

    def event(self, event) -> bool:  # noqa: A003 (Qt override)
        if (event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape
                and (self._drag >= 0 or self._expanded_uid >= 0)):
            event.accept()  # ours, before a window's Esc shortcut (un-maximise) takes it
            return True
        if event.type() == QEvent.Type.ToolTip:
            tip = "" if self._drag >= 0 else self.tooltip_at(event.pos())
            if tip:
                i = self.index_at(event.pos())
                area = self._plus if i == -2 else self._rects[i]
                QToolTip.showText(event.globalPos(), tip, self, area)
            else:
                QToolTip.hideText()
                event.ignore()
            return True
        return super().event(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            self._press = self.index_at(pos)
            self._press_pos = pos
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        pos = event.position().toPoint()
        if self._drag >= 0:
            self._drag_pos = pos
            self._slot = self._slot_at(pos)
            self.update()
            return
        if (self._press >= 0
                and (pos - self._press_pos).manhattanLength() >= QApplication.startDragDistance()):
            self._start_drag(pos)
            return
        i = self.index_at(pos)
        if i != self._hover:
            self._hover = i
            # every tile opens its detail on a click, so every tile is a button
            if i == -2 or i >= 0:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        pos = event.position().toPoint()
        if self._drag >= 0:
            # move_gauge ends the drag itself, after noting where the lifted
            # tile was, so it glides from the pointer into its slot
            if not self.move_gauge(self._drag, self._slot):
                self._cancel_drag()  # dropped where it was: glide back home
            return
        pressed, self._press = self._press, -1
        # a click is a press and release on the same tile, so sliding off a
        # tile to think again cancels it, the way a button does
        i = self.index_at(pos)
        if i == pressed == -2:
            self.add_requested.emit()
        elif i >= 0 and i == pressed:
            uid = self._tiles[i].uid
            self.gauge_clicked.emit(self._tiles[i].gauge)
            # the click may have changed the strip under us: find the tile again
            k = next((n for n, t in enumerate(self._tiles) if t.uid == uid), -1)
            self.toggle(k)
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # a drag nobody can finish (focus went elsewhere, an Alt+Tab) is put back
        self._cancel_drag()
        super().focusOutEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self._cancel_drag()
        super().changeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape and self._drag >= 0:
            self._cancel_drag()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self._expanded_uid >= 0:
            self.collapse()
            event.accept()
            return
        super().keyPressEvent(event)

    def _context_menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Replace with…").triggered.connect(
            lambda _=False, i=index: self.replace_requested.emit(i))
        menu.addAction("Remove").triggered.connect(
            lambda _=False, i=index: self.remove_gauge(i))
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        i = self.index_at(event.pos())
        if i < 0 or self._drag >= 0:
            event.ignore()
            return
        menu = self._context_menu(i)
        menu.exec(event.globalPos())
        menu.deleteLater()

    # -- painting ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        self._ensure_layout()
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            t = self._t
            closing = self.closing_card_rect()
            if closing.isValid():
                # the old card, closing like a drawer over its last frame
                pix = self._closing[1]
                p.drawPixmap(closing.topLeft(), pix,
                             QRect(0, 0, round(closing.width() * pix.devicePixelRatio()),
                                   round(closing.height() * pix.devicePixelRatio())))
            for tile, rect in self._ghosts:
                p.setOpacity(max(0.0, 1.0 - t))
                self._paint_tile(p, tile, rect, hover=False)
            for i, (tile, target) in enumerate(zip(self._tiles, self._rects)):
                if i == self._drag:
                    p.setOpacity(1.0)
                    self._paint_placeholder(p, QRectF(target))
                    continue
                p.setOpacity(t if tile.uid in self._fade_in else 1.0)
                opened = tile.uid == self._card_uid and self._card_geom.height() > 0
                self._paint_tile(p, tile, self._painted(tile.uid, target), hover=i == self._hover,
                                 lifted=i == self._revealed, opened=opened)
            p.setOpacity(1.0)
            self._paint_caret(p)
            self._paint_plus(p, self._painted(_PLUS, self._plus), hover=self._hover == -2)
            if self._drag >= 0:
                bar = self.drop_indicator_rect()
                if bar.isValid():
                    p.fillRect(bar, QColor(ACCENT))
                # opaque, so the tiles it passes over never show through it
                self._paint_tile(p, self._tiles[self._drag], QRectF(self._lifted_rect()),
                                 hover=False, lifted=True)
        finally:
            p.end()

    def _frame(self, p: QPainter, rect: QRectF, edge: str, fill: str | None,
               dashed: bool = False) -> None:
        body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        pen = QPen(QColor(edge))
        pen.setWidthF(1.0)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, RADIUS_MD, RADIUS_MD)

    def _paint_placeholder(self, p: QPainter, rect: QRectF) -> None:
        self._frame(p, rect, BORDER_STRONG, None, dashed=True)

    def _paint_plus(self, p: QPainter, rect: QRectF, hover: bool) -> None:
        self._frame(p, rect, FG_DIM if hover else BORDER_STRONG, None, dashed=not hover)
        c = rect.center()
        arm = ICON_PX / 2 - SPACE_XS
        pen = QPen(QColor(LABEL if hover else FG_DIM))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(c.x() - arm, c.y()), QPointF(c.x() + arm, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - arm), QPointF(c.x(), c.y() + arm))

    def _paint_caret(self, p: QPainter) -> None:
        """A small notch on the card's top edge, pointing up at its tile, so
        it reads as that tile opened rather than a new row from nowhere."""
        card = self.painted_card_rect()
        k = next((i for i, t in enumerate(self._tiles) if t.uid == self._card_uid), -1)
        if k < 0 or card.height() <= 0 or k == self._drag:
            return
        tile = self._painted(self._tiles[k].uid, self._rects[k])
        cx, base = tile.center().x(), float(card.top())
        half, tip = float(SPACE_MD), base - (_GAP - 1)
        path = QPainterPath(QPointF(cx - half, base))
        path.lineTo(cx, tip)
        path.lineTo(cx + half, base)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawPath(path)

    def _paint_tile(self, p: QPainter, tile: _Tile, rect: QRectF, hover: bool,
                    lifted: bool = False, opened: bool = False) -> None:
        edge = ACCENT if (lifted or opened) else (FG_DIM if hover else BORDER_STRONG)
        self._frame(p, rect, edge, CHROME_LOW if lifted else BG_ALT)
        inner = rect.adjusted(_PAD_X, _PAD_Y, -_PAD_X, -_PAD_Y)
        x, w = inner.left(), inner.width()
        y = inner.top()
        name_h = self._fm_name.height()
        value_h = self._fm_value.height()
        reading = tile.reading

        # the value row: the figure, and today's change right-aligned beside
        # it — or up on the name row when both will not fit side by side
        value = reading.value if reading is not None else "…"
        change = reading.change if reading is not None else ""
        value_w = self._fm_value.horizontalAdvance(value)
        change_w = self._fm_change.horizontalAdvance(change) if change else 0
        change_on_name_row = bool(change) and value_w + SPACE_MD + change_w > w

        name_w = w - (change_w + SPACE_MD if change_on_name_row else 0)
        p.setFont(self._f_name)
        p.setPen(QColor(FG_DIM))
        name = self._fm_name.elidedText(tile.shown_label, Qt.TextElideMode.ElideRight, int(name_w))
        p.drawText(QRectF(x, y, name_w, name_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        y_value = y + name_h + SPACE_XS
        p.setFont(self._f_value)
        p.setPen(QColor(LABEL if reading is not None else FG_MUTED))
        shown = self._fm_value.elidedText(value, Qt.TextElideMode.ElideRight, int(w))
        p.drawText(QRectF(x, y_value, w, value_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown)
        if change:
            sign = reading.change_sign if reading is not None else 0
            p.setFont(self._f_change)
            p.setPen(QColor(UP if sign > 0 else DOWN if sign < 0 else FG_DIM))
            if change_on_name_row:
                box = QRectF(x, y, w, name_h)
                align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            else:
                # share the value's baseline, so the two read as one line
                base = y_value + self._fm_value.ascent()
                box = QRectF(x, base - self._fm_change.ascent(), w, self._fm_change.height())
                align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
            p.drawText(box, align, change)

        spark = QRectF(x, y_value + value_h + SPACE_XS, w, _SPARK_H)
        if reading is None:
            # skeleton: the workspace skeleton's bar, where the line will be
            bar = QRectF(spark.left(), spark.center().y() - SPACE_XS, w * 0.7, 2 * SPACE_XS)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(CHROME_LOW))
            p.drawRoundedRect(bar, SPACE_XS, SPACE_XS)
        else:
            self._paint_spark(p, spark, reading.spark)

        if reading is not None and reading.note:
            p.setFont(self._f_note)
            p.setPen(QColor(DOWN if reading.note_tone == "down" else FG_DIM))
            note = self._fm_note.elidedText(reading.note, Qt.TextElideMode.ElideRight, int(w))
            p.drawText(QRectF(x, spark.bottom() + SPACE_XS, w, self._fm_note.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, note)

    def _paint_spark(self, p: QPainter, rect: QRectF, values: tuple[float, ...]) -> None:
        """A one-line history: neutral ink, the latest point marked in the
        data ink. Direction is already said by the coloured change; a red or
        green line beside it would argue with a day that went the other way."""
        if len(values) < 2:
            return
        lo, hi = min(values), max(values)
        span = hi - lo
        dot = SPACE_XS
        top, height = rect.top() + dot, rect.height() - 2 * dot
        left, width = rect.left(), rect.width() - dot
        step = width / (len(values) - 1)

        def y_of(v: float) -> float:
            return top + height / 2 if span <= 0 else top + (hi - v) / span * height

        path = QPainterPath(QPointF(left, y_of(values[0])))
        for k, v in enumerate(values[1:], 1):
            path.lineTo(left + k * step, y_of(v))
        pen = QPen(QColor(FG_DIM))
        pen.setWidthF(1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        end = QPointF(left + width, y_of(values[-1]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LABEL))
        p.drawEllipse(end, dot, dot)
