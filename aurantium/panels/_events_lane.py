"""The chart's EVENTS lane: a thin strip under the price marking days that
have something to explain, and the click target that opens the Day Brief.

Why a separate pane rather than marks on the price plot: the price plot
auto-ranges, so anything pinned inside it has to be repositioned on every zoom,
fights the auto-range it sits in, and collides with the candles. A dedicated
x-linked strip has none of those problems, is a bigger and more obvious click
target, and reads like a terminal.

Why the marks are the click target: an action that opens or re-centres a panel
must not fire by accident, and a plot is a surface people click while reading.
These glyphs exist *only* on days that have something to say, so aiming at one
is deliberate by construction -- and seeing them is how the user learns the
feature exists at all.

The underscore prefix keeps ``discover_panels`` from trying to register this
module as a panel (panel.py:171-186).
"""

from __future__ import annotations

from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QPainterPath

from ..theme import ACCENT, DOWN, FG_DIM, FG_MUTED, FONT_XS, MONO_FONT, UP

#: z-score at which a daily return counts as worth flagging. Deliberately
#: strict: a lane that marks every other day teaches the user to ignore it.
SIGMA_THRESHOLD = 2.5

#: Mark size in pixels, and the lane's height. Tuned down from 13/30 after the
#: first build read as far too heavy: next to 11px chart text these are among
#: the loudest things on screen, and there can be a dozen of them across a
#: year. The lane is a footnote to the price, not a second chart -- it should
#: be findable when you look for it and invisible when you aren't.
MARK_SIZE = 8
HOVER_SIZE = 11
LANE_HEIGHT = 20

#: Closest two marks may sit, centre to centre, before they count as touching.
MIN_GAP_PX = MARK_SIZE + 2
#: A combined mark is a count. One step up from a single mark: glyphs are
#: scaled to fill their box, so a digit at the same size already reads larger
#: than an E or a dot, and +3 made the counts the loudest thing in the lane.
GROUP_SIZE = MARK_SIZE + 1
#: Lines a combined mark's tooltip lists before summarising the rest.
GROUP_TIP_LINES = 8


def cluster_marks(xs_px: list[float], min_gap: float = MIN_GAP_PX) -> list[list[int]]:
    """Group marks that would touch at this zoom: lists of indices into
    ``xs_px`` (which must be sorted), left to right.

    Marks are drawn at their bar's x, so an earnings day that was also an
    unusual move printed as one unreadable glyph, and a zoomed-out year of
    events smeared into a solid bar. Stacking them into two rows only moved
    the pile-up once three or more met; touching marks now merge into one
    mark that says how many it holds. A cluster grows while each next mark is
    within ``min_gap`` of the one before, so a dense run joins as one.
    """
    groups: list[list[int]] = []
    for i, x in enumerate(xs_px):
        if groups and x - xs_px[groups[-1][-1]] < min_gap:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups

#: kind -> (glyph, colour). Shape carries the meaning and colour only
#: reinforces it, per PRODUCT.md's colour-blindness rule.
MARK_STYLE: dict[str, tuple[str, str]] = {
    "earnings": ("E", ACCENT),
    "dividend": ("D", FG_DIM),
    "split": ("S", FG_DIM),
    "rating_up": ("▲", UP),
    "rating_down": ("▼", DOWN),
    "move_up": ("●", UP),
    "move_down": ("●", DOWN),
}

#: pyqtgraph's own built-in symbols for the geometric marks.
#: Letters go through ``glyph_path`` because every monospace face has A-Z, but
#: the arrows and dot must not: QPainterPath.addText does **no font
#: substitution**, so a face without U+25B2 would silently draw nothing. Qt's
#: text rendering elsewhere (the panel's event list) does substitute, which is
#: why the same characters are safe there.
PG_SYMBOL = {
    "rating_up": "t1",    # triangle up
    "rating_down": "t",   # triangle down
    "move_up": "o",
    "move_down": "o",
}

_LABELS = {
    "earnings": "Earnings",
    "dividend": "Ex-dividend",
    "split": "Split",
    "rating_up": "Upgrade",
    "rating_down": "Downgrade",
    "move_up": "Unusual move up",
    "move_down": "Unusual move down",
}

_symbol_cache: dict[str, QPainterPath] = {}


def glyph_path(ch: str) -> QPainterPath:
    """A pyqtgraph scatter symbol drawn from a character.

    pyqtgraph accepts a QPainterPath as a symbol and scales it by ``size``, so
    the path is normalised into a unit box centred on the origin -- otherwise
    an 'E' and a triangle would render at wildly different visual weights."""
    cached = _symbol_cache.get(ch)
    if cached is not None:
        return cached
    font = QFont(MONO_FONT)
    font.setPointSizeF(10.0)
    font.setBold(True)
    path = QPainterPath()
    path.addText(0, 0, font, ch)
    box: QRectF = path.boundingRect()
    scale = 1.0 / max(box.width(), box.height(), 1e-6)
    tr = pg.QtGui.QTransform()
    tr.scale(scale, scale)
    tr.translate(-box.center().x(), -box.center().y())
    path = tr.map(path)
    _symbol_cache[ch] = path
    return path


def unusual_move_indices(
    closes: list, threshold: float = SIGMA_THRESHOLD
) -> list[tuple[int, float]]:
    """Indices into ``closes`` whose daily return is at least ``threshold``
    standard deviations from the mean, with the return itself.

    Pure and Qt-free so it can be unit-tested directly. Uses the population
    standard deviation of the loaded window -- this is a "look here" hint, not
    a statistic anyone will trade on, and a sample correction would only move
    the boundary case."""
    rets: list[tuple[int, float]] = []
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if not prev:
            continue
        rets.append((i, (cur - prev) / prev))
    if len(rets) < 8:  # too short a window for a meaningful spread
        return []
    values = [r for _, r in rets]
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    sd = var ** 0.5
    if sd <= 0:
        return []
    return [(i, r) for i, r in rets if abs((r - mean) / sd) >= threshold]


def build_marks(
    times: list,
    closes: list,
    events: list[dict],
    threshold: float = SIGMA_THRESHOLD,
) -> list[dict]:
    """Merge corporate events and unusual moves into one list of lane marks.

    ``events`` are dicts as ``dayev:`` publishes them; ``times`` are the
    chart's own epoch-second bars. Marks are keyed to the *bar* so a mark
    always sits under the candle it explains, and an event on a non-trading
    day quietly attaches to the nearest bar rather than floating in a gap."""
    if not times or not closes:
        return []
    from ..date_context import bar_datetime

    by_date: dict[str, int] = {}
    for i, t in enumerate(times):
        try:
            by_date.setdefault(bar_datetime(t).date().isoformat(), i)
        except (ValueError, OSError, OverflowError):
            continue

    marks: dict[tuple[str, str], dict] = {}

    for ev in events or []:
        iso = str(ev.get("date") or "")[:10]
        idx = by_date.get(iso)
        if idx is None:
            continue
        kind = str(ev.get("kind") or "")
        if kind == "rating":
            blob = f"{ev.get('action') or ''} {ev.get('detail') or ''}".lower()
            if "down" in blob:
                kind = "rating_down"
            elif "up" in blob or "→" in blob:
                kind = "rating_up"
            else:
                # a reiteration or a maintained rating is not a change; the
                # lane only marks days where something actually happened,
                # which is what makes a mark worth aiming at
                continue
        if kind not in MARK_STYLE:
            continue
        marks[(iso, kind)] = {
            "t": times[idx],
            "x": idx,
            "date": iso,
            "kind": kind,
            "label": _LABELS.get(kind, kind),
            "detail": str(ev.get("detail") or ""),
        }

    for idx, ret in unusual_move_indices(closes, threshold):
        if idx >= len(times):
            continue
        try:
            iso = bar_datetime(times[idx]).date().isoformat()
        except (ValueError, OSError, OverflowError):
            continue
        kind = "move_up" if ret >= 0 else "move_down"
        marks.setdefault(
            (iso, kind),
            {
                "t": times[idx],
                "x": idx,
                "date": iso,
                "kind": kind,
                "label": _LABELS[kind],
                "detail": f"{ret * 100:+.1f}% session",
            },
        )

    return sorted(marks.values(), key=lambda m: (m["t"], m["kind"]))


def _mark_x(m: dict) -> float:
    """Plot x of a mark: its bar's slot (the chart plots bars by index, so
    closed hours take no room), or its time for a caller that plots by time."""
    return m.get("x", m["t"])


class EventsLane:
    """Owns the lane pane and its scatter item. Created by ChartPanel; the
    chart keeps all its own state and just feeds ``set_marks``."""

    HEIGHT = LANE_HEIGHT

    def __init__(
        self,
        pane: pg.PlotWidget,
        on_pick: Callable[[str], None],
        background: str,
    ) -> None:
        self.pane = pane
        self._on_pick = on_pick
        self._marks: list[dict] = []

        pane.setFixedHeight(self.HEIGHT)
        pane.setBackground(background)
        pane.showGrid(x=False, y=False)
        pane.setMenuEnabled(False)
        pane.setMouseEnabled(x=False, y=False)
        pane.hideAxis("bottom")
        # The left axis stays, as a gutter as wide as the price axis above
        # (the chart sets the width), so the lane's dates line up with the
        # candles; its one "tick" is the EVENTS caption, which therefore sits
        # in the label column beside the strip instead of over its marks.
        axis = pane.getAxis("left")
        axis.setPen(pg.mkPen(None))
        axis.setTickPen(pg.mkPen(0, 0, 0, 0))  # a zero-length tick still paints a dot; mkPen(None) is ignored here
        axis.setTextPen(FG_MUTED)
        axis.setTicks([[(0.0, "EVENTS")], []])
        caption_font = QFont(MONO_FONT)
        caption_font.setPixelSize(FONT_XS)
        caption_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1)
        axis.setStyle(tickFont=caption_font, tickLength=0)
        pane.setYRange(-1.0, 1.0, padding=0)
        pane.setToolTip("Days with something to explain — click a mark")


        self._scatter = pg.ScatterPlotItem(
            pxMode=True,
            size=MARK_SIZE,
            hoverable=True,
            hoverSize=HOVER_SIZE,
            tip=self._tip,
        )
        self._scatter.setZValue(10)
        self._scatter.sigClicked.connect(self._clicked)
        # press feedback (press.py) sinks just the mark under the pointer
        pane.__dict__["press_region"] = self._press_region
        self._scatter.sigHovered.connect(self._hovered)
        pane.addItem(self._scatter)
        pane.getViewBox().sigXRangeChanged.connect(self._reposition_label)

    # -- data --------------------------------------------------------------

    def set_marks(self, marks: list[dict]) -> None:
        self._marks = marks or []
        self._groups: list[list[int]] = []
        self._place()

    def _place(self) -> None:
        """(Re)draw the marks, merging any that would touch at this zoom."""
        if not self._marks:
            self._groups = []
            self._scatter.clear()
            return
        groups = self._groups_for_view()
        pts = self._scatter.points()
        if groups == getattr(self, "_groups", None) and pts is not None \
                and len(pts) == len(groups):
            return  # same arrangement as on screen: nothing to redraw
        self._groups = groups
        spots = []
        for idxs in groups:
            members = [self._marks[i] for i in idxs]
            if len(members) == 1:
                m = members[0]
                glyph, color = MARK_STYLE.get(m["kind"], ("●", FG_DIM))
                symbol = PG_SYMBOL.get(m["kind"]) or glyph_path(glyph)
                spots.append({"pos": (_mark_x(m), 0.0), "symbol": symbol,
                              "brush": pg.mkBrush(color), "pen": None, "data": m})
                continue
            xs = [_mark_x(m) for m in members]
            kinds = {m["kind"] for m in members}
            # one kind keeps its colour; a mix is neutral (the secondary-text
            # grey, not full ink, so a count never outshouts the marks)
            color = MARK_STYLE.get(next(iter(kinds)), ("", FG_DIM))[1] if len(kinds) == 1 else FG_DIM
            count = str(len(members)) if len(members) < 10 else "9+"
            spots.append({
                "pos": ((min(xs) + max(xs)) / 2.0, 0.0),
                "symbol": glyph_path(count),
                "size": GROUP_SIZE,
                "brush": pg.mkBrush(color),
                "pen": None,
                "data": {"group": members, "x0": min(xs), "x1": max(xs)},
            })
        self._scatter.setData(spots)

    def _groups_for_view(self) -> list[list[int]]:
        singles = [[i] for i in range(len(self._marks))]
        try:
            vb = self.pane.getViewBox()
            (x0, x1), _ = vb.viewRange()
            width = vb.width()
        except Exception:
            return singles
        if x1 <= x0 or width <= 0:
            return singles
        scale = width / (x1 - x0)
        return cluster_marks([(_mark_x(m) - x0) * scale for m in self._marks])

    def clear(self) -> None:
        self.set_marks([])

    # -- interaction -------------------------------------------------------

    @staticmethod
    def _tip(x, y, data) -> str:
        """Hover text. The user always learns the date and what the mark is
        *before* committing to a click."""
        if not isinstance(data, dict):
            return ""
        from datetime import date as _date

        def pretty(iso: str, fmt: str) -> str:
            try:
                return _date.fromisoformat(iso).strftime(fmt)
            except ValueError:
                return iso

        group = data.get("group")
        if group:
            lines = [
                f"{pretty(m.get('date', ''), '%d %b %Y')} · {m.get('label', '')}"
                + (f" ({m['detail']})" if m.get("detail") else "")
                for m in group[:GROUP_TIP_LINES]
            ]
            if len(group) > GROUP_TIP_LINES:
                lines.append(f"… and {len(group) - GROUP_TIP_LINES} more")
            dates = {m.get("date") for m in group}
            hint = "click for the Day Brief" if len(dates) == 1 else "click to zoom in"
            return f"{len(group)} events · {hint}\n" + "\n".join(lines)
        iso = data.get("date", "")
        detail = data.get("detail") or ""
        line = f"{pretty(iso, '%a %d %b %Y')} · {data.get('label', '')}"
        return f"{line}\n{detail}" if detail else line

    def _clicked(self, _item, points, _ev=None) -> None:
        if not len(points):
            return
        data = points[0].data()
        if not isinstance(data, dict):
            return
        group = data.get("group")
        if group:
            dates = {m.get("date") for m in group}
            if len(dates) == 1:
                self._on_pick(str(next(iter(dates))))
            else:
                self.zoom_to(data["x0"], data["x1"])
            return
        if data.get("date"):
            self._on_pick(str(data["date"]))

    def _press_region(self, pos):
        """The pressed mark's square in pane coordinates, for press.py."""
        from PySide6.QtCore import QPoint, QRect

        scene_pos = self.pane.mapToScene(pos)
        pts = self._scatter.pointsAt(self._scatter.mapFromScene(scene_pos))
        if pts is None or not len(pts):
            return None
        centre = self.pane.mapFromScene(self._scatter.mapToScene(pts[0].pos()))
        half = HOVER_SIZE // 2 + 2
        rect = QRect(centre - QPoint(half, half), centre + QPoint(half, half))
        return rect, half

    def zoom_to(self, x0: float, x1: float) -> None:
        """Open a combined mark: zoom until its events stand apart. The lane is
        x-linked to the price plot, so the whole chart follows."""
        span = max(x1 - x0, 1.0)
        pad = max(span * 0.6, 3.0)
        self.pane.getViewBox().setXRange(x0 - pad, x1 + pad, padding=0)

    def _hovered(self, _item, points, _ev=None) -> None:
        shape = (
            Qt.CursorShape.PointingHandCursor
            if len(points)
            else Qt.CursorShape.ArrowCursor
        )
        self.pane.setCursor(shape)

    def _reposition_label(self, *_args) -> None:
        """A zoom changes how far apart the marks sit on screen: re-group.
        (The EVENTS caption lives in the axis gutter now and never moves.)"""
        if getattr(self, "_marks", None):
            self._place()

    def set_background(self, color: str) -> None:
        self.pane.setBackground(color)
