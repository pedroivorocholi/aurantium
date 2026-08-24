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

from ..theme import ACCENT, DOWN, FG_DIM, FG_MUTED, MONO_FONT, UP

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
    from datetime import datetime

    by_date: dict[str, int] = {}
    for i, t in enumerate(times):
        try:
            by_date.setdefault(datetime.fromtimestamp(t).date().isoformat(), i)
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
            "date": iso,
            "kind": kind,
            "label": _LABELS.get(kind, kind),
            "detail": str(ev.get("detail") or ""),
        }

    for idx, ret in unusual_move_indices(closes, threshold):
        if idx >= len(times):
            continue
        try:
            iso = datetime.fromtimestamp(times[idx]).date().isoformat()
        except (ValueError, OSError, OverflowError):
            continue
        kind = "move_up" if ret >= 0 else "move_down"
        marks.setdefault(
            (iso, kind),
            {
                "t": times[idx],
                "date": iso,
                "kind": kind,
                "label": _LABELS[kind],
                "detail": f"{ret * 100:+.1f}% session",
            },
        )

    return sorted(marks.values(), key=lambda m: (m["t"], m["kind"]))


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
        pane.hideAxis("left")
        pane.hideAxis("bottom")
        pane.setYRange(-1.0, 1.0, padding=0)
        pane.setToolTip("Days with something to explain — click a mark")

        self._label = pg.TextItem(
            html=f'<span style="font-family:{MONO_FONT};font-size:7pt;'
            f'letter-spacing:1px;color:{FG_MUTED};">EVENTS</span>',
            anchor=(0, 0.5),
        )
        self._label.setZValue(5)
        pane.addItem(self._label, ignoreBounds=True)

        self._scatter = pg.ScatterPlotItem(
            pxMode=True,
            size=MARK_SIZE,
            hoverable=True,
            hoverSize=HOVER_SIZE,
            tip=self._tip,
        )
        self._scatter.setZValue(10)
        self._scatter.sigClicked.connect(self._clicked)
        self._scatter.sigHovered.connect(self._hovered)
        pane.addItem(self._scatter)
        pane.getViewBox().sigXRangeChanged.connect(self._reposition_label)

    # -- data --------------------------------------------------------------

    def set_marks(self, marks: list[dict]) -> None:
        self._marks = marks or []
        if not self._marks:
            self._scatter.clear()
            return
        spots = []
        for m in self._marks:
            glyph, color = MARK_STYLE.get(m["kind"], ("●", FG_DIM))
            symbol = PG_SYMBOL.get(m["kind"]) or glyph_path(glyph)
            spots.append(
                {
                    "pos": (m["t"], 0.0),
                    "symbol": symbol,
                    "brush": pg.mkBrush(color),
                    "pen": None,
                    "data": m,
                }
            )
        self._scatter.setData(spots)
        self._reposition_label()

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

        iso = data.get("date", "")
        try:
            pretty = _date.fromisoformat(iso).strftime("%a %d %b %Y")
        except ValueError:
            pretty = iso
        detail = data.get("detail") or ""
        line = f"{pretty} · {data.get('label', '')}"
        return f"{line}\n{detail}" if detail else line

    def _clicked(self, _item, points, _ev=None) -> None:
        if not len(points):
            return
        data = points[0].data()
        if isinstance(data, dict) and data.get("date"):
            self._on_pick(str(data["date"]))

    def _hovered(self, _item, points, _ev=None) -> None:
        shape = (
            Qt.CursorShape.PointingHandCursor
            if len(points)
            else Qt.CursorShape.ArrowCursor
        )
        self.pane.setCursor(shape)

    def _reposition_label(self, *_args) -> None:
        """Pin the EVENTS caption to the left edge of the visible range, so it
        names the strip without scrolling away with the data."""
        try:
            (x0, _x1), _ = self.pane.getViewBox().viewRange()
        except Exception:
            return
        self._label.setPos(x0, 0.62)

    def set_background(self, color: str) -> None:
        self.pane.setBackground(color)
