"""PositioningBars — the CFTC positioning list at the foot of Macro / Rates.

One row per market, left to right: the market's name; a diverging bar of large
speculators' net position (right of the zero line in the gain colour when they
are net long, left of it in the loss colour when net short, every row on one
shared scale); the net figure; the week-over-week change as a small signed
delta; and a marker for where this week's reading sits in the market's own
~2-year range (left end = the most short it has been, right end = the most
long). A market with too little history for that range shows no marker; its
bar is still drawn. Until its first reading a row shows a skeleton.

Bullish and bearish are said by direction and colour, never by words.

Like the gauge strip this is one painted widget, and editing happens in place:

* a trailing **+** row emits ``add_requested``; the panel opens its picker and
  calls ``add_row``;
* right-click → **Remove**;
* drag a row past the system drag distance to reorder — a thin accent bar
  marks where it will land, Escape puts it back.

``add_row`` / ``remove_row`` / a drop are *user* edits: each emits
``markets_changed`` with the new market list so the panel can save it and push
an undo step. ``set_rows`` is the panel talking (fresh data, a restore, an
undo) and never emits. Rows glide to their new places when animations are on
and jump when they are off; the logical order is final the moment an edit
returns either way.

A click on a row emits ``market_clicked(market)``; the panel opens the
Speculator Positioning panel on it through ``panel.open_panel_hook``.
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
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QApplication, QMenu, QSizePolicy, QToolTip, QWidget

from .. import motion
from .fmt import compact
from ..theme import (
    ACCENT,
    BORDER_STRONG,
    CHROME_LOW,
    DOWN,
    FG,
    FG_DIM,
    FG_MUTED,
    FONT_MD,
    FONT_SM,
    ICON_PX,
    LABEL,
    MONO_FONT,
    RADIUS_SM,
    SPACE_LG,
    SPACE_MD,
    SPACE_SM,
    SPACE_XL,
    SPACE_XS,
    UI_FONT,
    UP,
)


@dataclass(frozen=True)
class PositionRow:
    """One market's reading. ``net`` and ``wow`` are contracts (``None`` until
    the first report arrives); ``pct`` is 0–100, or ``None`` when the market
    has too little history to place the reading in its range."""

    market: str
    label: str
    net: Optional[float] = None
    wow: Optional[float] = None
    pct: Optional[float] = None


class _Row:
    """One market on screen. ``uid`` survives reorders and ``set_rows`` so an
    animation can tell where each row came from."""

    __slots__ = ("data", "uid")
    _ids = itertools.count(1)

    def __init__(self, data: PositionRow) -> None:
        self.data = data
        self.uid = next(self._ids)


_PLUS = 0  # the + row's uid in animation snapshots (row uids start at 1)

#: Row geometry: the dense step the rest of the terminal uses.
_GAP = SPACE_XS
_PAD_X = SPACE_LG
_COL_GAP = SPACE_LG
_PCT_W = 4 * SPACE_XL           # the 2-year range track
_BAR_MIN_W = 6 * SPACE_XL       # below this a bar says nothing
_LABEL_MIN_W = 4 * SPACE_XL
_WIDEST_FIGURE = "-888.8K"      # sizes the net and W/W columns
_MARKER_W = 2 * SPACE_XS
_EDIT_HINT = "Click for its weekly chart · drag to reorder · right-click to remove"


def _font(family: str, px: int) -> QFont:
    f = QFont(family)
    f.setPixelSize(px)
    return f


def _f(value) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _signed(value: Optional[float]) -> str:
    if value is None:
        return ""
    text = compact(value)
    return f"+{text}" if value > 0 else text


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _lerp_rect(a: QRectF, b: QRectF, t: float) -> QRectF:
    return QRectF(
        a.x() + (b.x() - a.x()) * t,
        a.y() + (b.y() - a.y()) * t,
        a.width() + (b.width() - a.width()) * t,
        a.height() + (b.height() - a.height()) * t,
    )


class PositioningBars(QWidget):
    market_clicked = Signal(str)
    markets_changed = Signal(list)   # list[str] of markets, after every user edit
    add_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        # focus on press, so Escape reaches a drag in progress
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._f_label = _font(UI_FONT, FONT_MD)
        self._f_net = _font(MONO_FONT, FONT_MD)
        self._f_wow = _font(MONO_FONT, FONT_SM)
        self._fm_label = QFontMetrics(self._f_label)
        self._fm_net = QFontMetrics(self._f_net)
        self._fm_wow = QFontMetrics(self._f_wow)
        self._row_h = max(self._fm_label.height(), self._fm_net.height()) + 2 * SPACE_SM
        self._bar_h = max(2 * SPACE_XS, self._row_h - 2 * SPACE_MD)
        self._net_w = self._fm_net.horizontalAdvance(_WIDEST_FIGURE)
        self._wow_w = self._fm_wow.horizontalAdvance(_WIDEST_FIGURE)

        self._rows: list[_Row] = []
        self._hover = -1                # row index; -2 = the + row
        self._press = -1                # row index pressed; -2 = the + row
        self._press_pos = QPoint()
        self._drag = -1                 # row index being dragged
        self._drag_offset = 0
        self._drag_y = 0
        self._slot = -1

        # one animation drives every row: 0 -> 1, each row interpolating from
        # where it was painted to where it now belongs
        self._t = 1.0
        self._from: dict[int, QRectF] = {}
        self._fade_in: set[int] = set()
        self._ghosts: list[tuple[_Row, QRectF]] = []
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve(motion.EASE_TRAVEL))
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._settle)

        # press feedback sinks just the pressed row (see press.py)
        self.__dict__["press_region"] = self._press_region

    # -- content (the panel talking: never emits) -----------------------------

    def set_rows(self, rows: Sequence) -> None:
        """Replace every row with ``(market, label, net, wow, pct)`` tuples
        (or ``PositionRow``s). A market already listed keeps its place in the
        animation and glides to its new slot."""
        before = self._snapshot()
        pool = list(self._rows)
        fresh: list[_Row] = []
        for raw in rows:
            data = raw if isinstance(raw, PositionRow) else self._coerce(raw)
            if data is None:
                continue
            match = next((r for r in pool if r.data.market == data.market), None)
            if match is not None:
                pool.remove(match)
                match.data = data
                fresh.append(match)
            else:
                fresh.append(_Row(data))
        animate = bool(self._rows)
        self._rows = fresh
        self._end_drag()
        self._after_edit(before, pool if animate else [], animate=animate)

    @staticmethod
    def _coerce(raw) -> Optional[PositionRow]:
        try:
            market, label, net, wow, pct = raw
        except (TypeError, ValueError):
            return None
        pct = _f(pct)
        if pct is not None:
            pct = max(0.0, min(100.0, pct))
        return PositionRow(str(market), str(label), _f(net), _f(wow), pct)

    def markets(self) -> list[str]:
        return [r.data.market for r in self._rows]

    # -- user edits (each emits markets_changed) -----------------------------

    def add_row(self, market: str, label: str) -> None:
        """Append ``market`` with no reading yet (it shows a skeleton until the
        panel's next ``set_rows``)."""
        before = self._snapshot()
        self._end_drag()
        self._rows.append(_Row(PositionRow(str(market), str(label))))
        self._after_edit(before, [])
        self._emit_changed()

    def remove_row(self, index: int) -> None:
        if not 0 <= index < len(self._rows):
            return
        before = self._snapshot()
        self._end_drag()
        old = self._rows.pop(index)
        self._after_edit(before, [old])
        self._emit_changed()

    def move_row(self, index: int, slot: int) -> bool:
        """Move row ``index`` to sit before slot ``slot`` (0..len). False, and
        no edit, when that leaves it where it was."""
        n = len(self._rows)
        if not 0 <= index < n or not 0 <= slot <= n or slot in (index, index + 1):
            return False
        before = self._snapshot()
        self._end_drag()
        row = self._rows.pop(index)
        self._rows.insert(slot - 1 if slot > index else slot, row)
        self._after_edit(before, [])
        self._emit_changed()
        return True

    def _emit_changed(self) -> None:
        self.markets_changed.emit(self.markets())

    # -- queries (the panel and tests read the bars through these) -----------

    def is_loading(self, index: int) -> bool:
        return 0 <= index < len(self._rows) and self._rows[index].data.net is None

    def row_rect(self, index: int) -> QRect:
        if not 0 <= index < len(self._rows):
            return QRect()
        return QRect(0, index * (self._row_h + _GAP), self.width(), self._row_h)

    def plus_rect(self) -> QRect:
        return QRect(0, len(self._rows) * (self._row_h + _GAP), self.width(), self._row_h)

    def painted_rect(self, index: int) -> QRect:
        """Where row ``index`` is drawn right now (its slot, unless it is
        still gliding there)."""
        if not 0 <= index < len(self._rows):
            return QRect()
        return self._painted(self._rows[index].uid, self.row_rect(index)).toAlignedRect()

    def index_at(self, pos) -> int:
        """Row index under ``pos``; -2 for the + row; -1 for neither."""
        for i in range(len(self._rows)):
            if self.row_rect(i).contains(pos):
                return i
        return -2 if self.plus_rect().contains(pos) else -1

    def zero_x(self) -> float:
        """The x of the zero line every bar grows from."""
        cols = self._columns(self.width())
        return cols["bar"][0] + cols["bar"][1] / 2

    def bar_track(self, index: int) -> QRectF:
        """The full extent a bar in row ``index`` may span."""
        return self._track(self.row_rect(index), "bar")

    def percentile_track(self, index: int) -> QRectF:
        """The 2-year range track (left end 0, right end 100) of row ``index``."""
        return self._track(self.row_rect(index), "pct")

    def bar_rect(self, index: int) -> QRectF:
        """Row ``index``'s bar; null while it has no reading."""
        if not 0 <= index < len(self._rows):
            return QRectF()
        return self._bar_in(self.row_rect(index), self._rows[index].data)

    def bar_color(self, index: int) -> str:
        net = self._rows[index].data.net if 0 <= index < len(self._rows) else None
        return UP if net is not None and net >= 0 else DOWN

    def marker_rect(self, index: int) -> QRectF:
        """Row ``index``'s range marker; null without enough history."""
        if not 0 <= index < len(self._rows):
            return QRectF()
        return self._marker_in(self.row_rect(index), self._rows[index].data)

    def net_text(self, index: int) -> str:
        return _signed(self._rows[index].data.net) if 0 <= index < len(self._rows) else ""

    def wow_text(self, index: int) -> str:
        return _signed(self._rows[index].data.wow) if 0 <= index < len(self._rows) else ""

    def wow_color(self, index: int) -> str:
        wow = self._rows[index].data.wow if 0 <= index < len(self._rows) else None
        if not wow:
            return FG_DIM
        return UP if wow > 0 else DOWN

    def tooltip_at(self, pos) -> str:
        i = self.index_at(pos)
        if i == -2:
            return "Add a market"
        if i < 0:
            return ""
        d = self._rows[i].data
        lines = [d.label]
        if d.net is None:
            lines.append("Waiting for the latest CFTC report")
        else:
            side = "long" if d.net >= 0 else "short"
            lines.append(f"Large speculators net {side} {abs(d.net):,.0f} contracts")
            if d.wow is not None:
                lines.append(f"Week over week: {d.wow:+,.0f}")
            if d.pct is None:
                lines.append("Not enough history yet to place it in its 2-year range")
            else:
                lines.append(f"{_ordinal(int(round(d.pct)))} percentile of its ~2-year range "
                             "(0 = most short, 100 = most long)")
        lines.append(_EDIT_HINT)
        return "\n".join(lines)

    def is_dragging(self) -> bool:
        return self._drag >= 0

    def drop_slot(self) -> int:
        return self._slot if self._drag >= 0 else -1

    def drop_indicator_rect(self) -> QRect:
        """The accent bar marking where a dragged row will land; null when no
        drag is under way or the drop would leave it where it is."""
        if self._drag < 0 or self._slot < 0 or self._slot in (self._drag, self._drag + 1):
            return QRect()
        if self._slot < len(self._rows):
            y = self.row_rect(self._slot).top() - _GAP // 2
        else:
            y = self.row_rect(len(self._rows) - 1).bottom() + 1 + _GAP // 2
        y = max(0, min(self.height() - SPACE_XS, y - SPACE_XS // 2))
        return QRect(0, y, self.width(), SPACE_XS)

    def is_animating(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def finish_animation(self) -> None:
        if self._anim.state() != QVariantAnimation.State.Stopped:
            self._anim.stop()
        self._settle()

    # -- layout -----------------------------------------------------------------

    def _widest_label(self) -> int:
        # + a little slack: at a fractional screen scale the painted advance
        # runs a pixel or two past the integer metric, and a name that fits
        # must never be elided
        return max((self._fm_label.horizontalAdvance(r.data.label) + SPACE_SM
                    for r in self._rows), default=_LABEL_MIN_W)

    def _fixed_w(self) -> int:
        """Everything but the label and the bar: padding, figures, range."""
        return 2 * _PAD_X + self._net_w + self._wow_w + _PCT_W + 4 * _COL_GAP

    def _columns(self, width: int) -> dict[str, tuple[int, int]]:
        """``(left, width)`` of each column at ``width``. The name column takes
        its widest label up to a third of the row; the bar takes the rest."""
        label_w = max(_LABEL_MIN_W, min(self._widest_label(), width // 3))
        bar_w = max(_BAR_MIN_W, width - self._fixed_w() - label_w)
        x = _PAD_X
        cols = {}
        for name, w in (("label", label_w), ("bar", bar_w), ("net", self._net_w),
                        ("wow", self._wow_w), ("pct", _PCT_W)):
            cols[name] = (x, w)
            x += w + _COL_GAP
        return cols

    def _track(self, row: QRect | QRectF, column: str) -> QRectF:
        if row.isNull():
            return QRectF()
        left, w = self._columns(self.width())[column]
        cy = QRectF(row).center().y()
        return QRectF(row.left() + left, cy - self._bar_h / 2, w, self._bar_h)

    def _scale(self) -> float:
        return max((abs(r.data.net) for r in self._rows if r.data.net is not None), default=0.0)

    def _bar_in(self, row: QRect | QRectF, data: PositionRow) -> QRectF:
        if data.net is None:
            return QRectF()
        track = self._track(row, "bar")
        zero = track.center().x()
        biggest = self._scale()
        half = track.width() / 2
        length = 0.0 if biggest <= 0 else half * abs(data.net) / biggest
        if data.net != 0:
            length = max(1.0, length)  # a tiny position is still a position
        left = zero if data.net >= 0 else zero - length
        return QRectF(left, track.top(), length, track.height())

    def _marker_in(self, row: QRect | QRectF, data: PositionRow) -> QRectF:
        if data.pct is None or data.net is None:
            return QRectF()
        track = self._track(row, "pct")
        x = track.left() + track.width() * data.pct / 100.0
        return QRectF(x - _MARKER_W / 2, track.top(), _MARKER_W, track.height())

    def _height_for(self, rows: int) -> int:
        return (rows + 1) * self._row_h + rows * _GAP  # + the + row

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        w = self._fixed_w() + self._widest_label() + 2 * _BAR_MIN_W
        return QSize(w, self._height_for(len(self._rows)))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        w = self._fixed_w() + _LABEL_MIN_W + _BAR_MIN_W
        return QSize(w, self._height_for(len(self._rows)))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        # a resize mid-glide lands it: the targets just moved under it
        self.finish_animation()

    # -- animation ----------------------------------------------------------------

    def _painted(self, uid: int, target: QRect) -> QRectF:
        start = self._from.get(uid)
        if start is None or self._t >= 1.0:
            return QRectF(target)
        return _lerp_rect(start, QRectF(target), self._t)

    def _snapshot(self) -> dict[int, QRectF]:
        """Where every row (and the + row) is drawn right now."""
        shot = {r.uid: self._painted(r.uid, self.row_rect(i)) for i, r in enumerate(self._rows)}
        shot[_PLUS] = self._painted(_PLUS, self.plus_rect())
        if self._drag >= 0:
            shot[self._rows[self._drag].uid] = QRectF(self._lifted_rect())
        return shot

    def _after_edit(self, before: dict[int, QRectF], gone: list[_Row],
                    animate: bool = True) -> None:
        if self._hover >= len(self._rows):
            self._hover = -1
        if self.height() != self._height_for(len(self._rows)):
            self.updateGeometry()  # one row more or fewer: ask the layout
        if not (animate and motion.animations_enabled() and self.isVisible()):
            self.finish_animation()
            self.update()
            return
        self._anim.stop()
        self._from = dict(before)
        self._fade_in = {r.uid for r in self._rows if r.uid not in before}
        self._ghosts = [(r, before[r.uid]) for r in gone if r.uid in before]
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
        r = self.row_rect(self._drag)
        r.moveTop(self._drag_y - self._drag_offset)
        return r

    def _slot_at(self, pos: QPoint) -> int:
        """The slot (0..len) a drop at ``pos`` lands in: before the row whose
        top half it is over, after it on its bottom half."""
        for i in range(len(self._rows)):
            if pos.y() < self.row_rect(i).center().y():
                return i
        return len(self._rows)

    def _start_drag(self, pos: QPoint) -> None:
        self._drag = self._press
        self._drag_offset = self._press_pos.y() - self.row_rect(self._drag).top()
        self._drag_y = pos.y()
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
            return self.plus_rect(), RADIUS_SM
        return (self.row_rect(i), RADIUS_SM) if i >= 0 else None

    def event(self, event) -> bool:  # noqa: A003 (Qt override)
        if event.type() == QEvent.Type.ToolTip:
            tip = "" if self._drag >= 0 else self.tooltip_at(event.pos())
            if tip:
                i = self.index_at(event.pos())
                area = self.plus_rect() if i == -2 else self.row_rect(i)
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
            self._drag_y = pos.y()
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
            if i != -1:
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
            # move_row ends the drag itself, after noting where the lifted row
            # was, so it glides from the pointer into its slot
            if not self.move_row(self._drag, self._slot):
                self._cancel_drag()  # dropped where it was: glide back home
            return
        pressed, self._press = self._press, -1
        # a click is a press and release on the same row
        i = self.index_at(pos)
        if i == pressed == -2:
            self.add_requested.emit()
        elif i >= 0 and i == pressed:
            self.market_clicked.emit(self._rows[i].data.market)
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
        super().keyPressEvent(event)

    def _context_menu(self, index: int) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Remove").triggered.connect(
            lambda _=False, i=index: self.remove_row(i))
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
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            t = self._t
            for row, rect in self._ghosts:
                p.setOpacity(max(0.0, 1.0 - t))
                self._paint_row(p, row.data, rect, hover=False)
            for i, row in enumerate(self._rows):
                target = self.row_rect(i)
                if i == self._drag:
                    p.setOpacity(1.0)
                    self._frame(p, QRectF(target), BORDER_STRONG, None, dashed=True)
                    continue
                p.setOpacity(t if row.uid in self._fade_in else 1.0)
                self._paint_row(p, row.data, self._painted(row.uid, target), hover=i == self._hover)
            p.setOpacity(1.0)
            self._paint_plus(p, self._painted(_PLUS, self.plus_rect()), hover=self._hover == -2)
            if self._drag >= 0:
                bar = self.drop_indicator_rect()
                if bar.isValid():
                    p.fillRect(bar, QColor(ACCENT))
                # opaque, so the rows it passes over never show through it
                self._paint_row(p, self._rows[self._drag].data, QRectF(self._lifted_rect()),
                                hover=False, lifted=True)
        finally:
            p.end()

    def _frame(self, p: QPainter, rect: QRectF, edge: str | None, fill: str | None,
               dashed: bool = False) -> None:
        body = rect.adjusted(0.5, 0.5, -0.5, -0.5)
        if edge:
            pen = QPen(QColor(edge))
            pen.setWidthF(1.0)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
        else:
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body, RADIUS_SM, RADIUS_SM)

    def _text(self, p: QPainter, font: QFont, color: str, rect: QRectF, text: str,
              align: Qt.AlignmentFlag) -> None:
        p.setFont(font)
        p.setPen(QColor(color))
        p.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)

    def _paint_row(self, p: QPainter, data: PositionRow, rect: QRectF, hover: bool,
                   lifted: bool = False) -> None:
        if lifted:
            self._frame(p, rect, ACCENT, CHROME_LOW)
        elif hover:
            self._frame(p, rect, None, CHROME_LOW)
        cols = self._columns(self.width())
        top, h = rect.top(), rect.height()

        def cell(name: str) -> QRectF:
            left, w = cols[name]
            return QRectF(rect.left() + left, top, w, h)

        name_box = cell("label")
        name = self._fm_label.elidedText(data.label, Qt.TextElideMode.ElideRight,
                                         int(name_box.width()))
        self._text(p, self._f_label, FG, name_box, name, Qt.AlignmentFlag.AlignLeft)

        track = self._track(rect, "bar")
        zero = track.center().x()
        p.setPen(QPen(QColor(BORDER_STRONG), 1.0))
        p.drawLine(QPointF(zero, top + SPACE_XS), QPointF(zero, top + h - SPACE_XS))

        if data.net is None:
            # skeleton: the workspace skeleton's bar, where the reading will be
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(CHROME_LOW))
            skel = QRectF(zero - track.width() / 4, track.top(), track.width() / 2, track.height())
            p.drawRoundedRect(skel, SPACE_XS, SPACE_XS)
            self._text(p, self._f_net, FG_MUTED, cell("net"), "…", Qt.AlignmentFlag.AlignRight)
            return

        bar = self._bar_in(rect, data)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(UP if data.net >= 0 else DOWN))
        p.drawRect(bar)

        self._text(p, self._f_net, LABEL, cell("net"), _signed(data.net),
                   Qt.AlignmentFlag.AlignRight)
        if data.wow is not None:
            ink = FG_DIM if not data.wow else (UP if data.wow > 0 else DOWN)
            self._text(p, self._f_wow, ink, cell("wow"), _signed(data.wow),
                       Qt.AlignmentFlag.AlignRight)

        pct_box = self._track(rect, "pct")
        if data.pct is None:
            # no range to place it in: a quiet dash, no marker
            self._text(p, self._f_wow, FG_MUTED, pct_box, "—", Qt.AlignmentFlag.AlignHCenter)
            return
        cy = pct_box.center().y()
        p.setPen(QPen(QColor(BORDER_STRONG), 1.0))
        p.drawLine(QPointF(pct_box.left(), cy), QPointF(pct_box.right(), cy))
        for x in (pct_box.left(), pct_box.right()):
            p.drawLine(QPointF(x, cy - SPACE_XS), QPointF(x, cy + SPACE_XS))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(LABEL))
        p.drawRect(self._marker_in(rect, data))

    def _paint_plus(self, p: QPainter, rect: QRectF, hover: bool) -> None:
        if hover:
            self._frame(p, rect, None, CHROME_LOW)
        ink = LABEL if hover else FG_DIM
        left = rect.left() + _PAD_X
        cy = rect.center().y()
        arm = ICON_PX / 2 - SPACE_SM
        pen = QPen(QColor(ink))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(pen)
        cx = left + arm
        p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
        p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))
        text_box = QRectF(cx + arm + SPACE_MD, rect.top(), rect.width(), rect.height())
        self._text(p, self._f_label, ink, text_box, "Add market", Qt.AlignmentFlag.AlignLeft)
