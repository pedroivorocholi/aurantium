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
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QMenu, QSizePolicy, QToolTip, QWidget

from .. import motion
from ..macro_model import tenor_label
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

#: Tile geometry. Gaps match the dense step the rest of the terminal uses.
_GAP = SPACE_MD
_PAD_X = SPACE_LG
_PAD_Y = SPACE_MD
_SPARK_H = SPACE_XL + SPACE_SM
_PLUS_W = ICON_PX + 2 * SPACE_LG
#: A row stops sharing out width past this: two gauges on a wide panel stay
#: gauges, not banners.
_TILE_MAX_PX = GAUGE_TILE_MIN_PX * 2
_NO_SYMBOL_HINT = "No symbol to send to linked panels: clicking does nothing."
_EDIT_HINT = "Drag to reorder · right-click to replace or remove"


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
    gauge_clicked = Signal(object)    # Gauge
    gauges_changed = Signal(list)     # list[Gauge], after every user edit
    add_requested = Signal()
    replace_requested = Signal(int)   # tile index

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
                    spark: Sequence[float], note: str = "") -> None:
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
        self._tiles[index].reading = Reading(str(value), str(change or ""), sign, tuple(clean),
                                              str(note or ""))
        self.update()

    def set_label(self, index: int, text: str) -> None:
        """Show tile ``index`` under ``text`` without changing its gauge
        ("10Y" as "UST 10Y"). Empty text goes back to the gauge's own label."""
        if 0 <= index < len(self._tiles):
            self._tiles[index].label = str(text or "")
            self.update()

    # -- user edits (each emits gauges_changed) ------------------------------

    def add_gauge(self, gauge: Gauge) -> None:
        before = self._snapshot()
        self._tiles.append(_Tile(gauge))
        self._after_edit(before, [])
        self._emit_changed()

    def replace_gauge(self, index: int, gauge: Gauge) -> None:
        if not 0 <= index < len(self._tiles):
            return
        before = self._snapshot()
        old = self._tiles[index]
        self._tiles[index] = _Tile(gauge)
        self._after_edit(before, [old])
        self._emit_changed()

    def remove_gauge(self, index: int) -> None:
        if not 0 <= index < len(self._tiles):
            return
        before = self._snapshot()
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
        lines.append(_EDIT_HINT)
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
        return self._anim.state() == QVariantAnimation.State.Running

    def finish_animation(self) -> None:
        if self._anim.state() != QVariantAnimation.State.Stopped:
            self._anim.stop()
        self._settle()

    # -- layout -----------------------------------------------------------------

    def _grid(self, width: int) -> tuple[list[QRect], QRect, int]:
        """Tile rects, the + tile's rect and the row count at ``width``.

        Columns are as many minimum-width tiles as fit beside a reserved +
        column; the tiles of a row then share the leftover width (up to a
        cap), so the grid fills edge to edge and the columns line up across
        rows. The + tile sits right after the last gauge."""
        n = len(self._tiles)
        avail = max(GAUGE_TILE_MIN_PX, width - _PLUS_W - _GAP)
        cols = max(1, (avail + _GAP) // (GAUGE_TILE_MIN_PX + _GAP))
        cols = min(cols, max(1, n))
        tw = min(float(_TILE_MAX_PX), (avail - _GAP * (cols - 1)) / cols)
        th = self._tile_h
        rects: list[QRect] = []
        for i in range(n):
            row, col = divmod(i, cols)
            x0 = round(col * (tw + _GAP))
            x1 = round(col * (tw + _GAP) + tw)
            rects.append(QRect(x0, row * (th + _GAP), x1 - x0, th))
        if rects:
            last = rects[-1]
            plus = QRect(last.right() + 1 + _GAP, last.top(), _PLUS_W, th)
        else:
            plus = QRect(0, 0, _PLUS_W, th)
        rows = max(1, math.ceil(n / cols))
        return rects, plus, rows

    def _ensure_layout(self) -> None:
        if self._laid_width != self.width() or len(self._rects) != len(self._tiles):
            self._relayout()

    def _relayout(self) -> None:
        self._rects, self._plus, rows = self._grid(self.width())
        self._laid_width = self.width()
        if rows != self._rows:
            # the strip's height changed: ask the panel's layout for it
            self._rows = rows
            self.updateGeometry()

    def _height_for_rows(self, rows: int) -> int:
        return rows * self._tile_h + (rows - 1) * _GAP

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return self._height_for_rows(self._grid(width)[2])

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        n = max(1, len(self._tiles))
        w = n * (GAUGE_TILE_MIN_PX + _GAP) + _PLUS_W
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        w = GAUGE_TILE_MIN_PX + _GAP + _PLUS_W
        return QSize(w, self._tile_h)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # a resize mid-glide lands it: the targets just moved under it
        self.finish_animation()
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
        if self._drag >= 0:
            shot[self._tiles[self._drag].uid] = QRectF(self._lifted_rect())
        return shot

    def _after_edit(self, before: dict[int, QRectF], gone: list[_Tile],
                    animate: bool = True) -> None:
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
        self.update()

    def _settle(self) -> None:
        self._t = 1.0
        self._from.clear()
        self._fade_in.clear()
        self._ghosts.clear()
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
            sendable = i == -2 or (i >= 0 and self._tiles[i].gauge.sends_symbol)
            if sendable:
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
            index, slot = self._drag, self._slot
            before = self._snapshot()
            self._end_drag()
            n = len(self._tiles)
            if 0 <= slot <= n and slot not in (index, index + 1):
                tile = self._tiles.pop(index)
                self._tiles.insert(slot - 1 if slot > index else slot, tile)
                self._after_edit(before, [])
                self._emit_changed()
            else:
                self._after_edit(before, [])  # glide back home
            return
        pressed, self._press = self._press, -1
        # a click is a press and release on the same tile, so sliding off a
        # tile to think again cancels it, the way a button does
        i = self.index_at(pos)
        if i == pressed == -2:
            self.add_requested.emit()
        elif i >= 0 and i == pressed:
            self.gauge_clicked.emit(self._tiles[i].gauge)
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape and self._drag >= 0:
            self._cancel_drag()
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
            for tile, rect in self._ghosts:
                p.setOpacity(max(0.0, 1.0 - t))
                self._paint_tile(p, tile, rect, hover=False)
            for i, (tile, target) in enumerate(zip(self._tiles, self._rects)):
                if i == self._drag:
                    p.setOpacity(1.0)
                    self._paint_placeholder(p, QRectF(target))
                    continue
                p.setOpacity(t if tile.uid in self._fade_in else 1.0)
                self._paint_tile(p, tile, self._painted(tile.uid, target), hover=i == self._hover)
            p.setOpacity(1.0)
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

    def _paint_tile(self, p: QPainter, tile: _Tile, rect: QRectF, hover: bool,
                    lifted: bool = False) -> None:
        edge = ACCENT if lifted else (FG_DIM if hover else BORDER_STRONG)
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
            p.setPen(QColor(FG_DIM))
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
