"""Pure geometry for per-panel divider drags (no Qt).

The window is a set of dock-area rectangles separated by ``gap`` pixels. Dragging
a divider moves only the panels pressed against the grabbed edge: the *movable
segment* starts at the two areas under the press point and grows along the line
through every area whose edge overlaps it, stopping at crossings. Keys of
:data:`Rects` are opaque hashables (dock areas in the Qt layer, strings in tests).

Edges within :data:`ALIGN_TOL_PX` count as collinear, so integer rounding in
nested splitters never turns an aligned crossing into a chaining case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Literal

ALIGN_TOL_PX = 2  # edges this close are treated as collinear
SNAP_PX = 6       # a dragged segment snaps to parallel edges this close

Axis = Literal["v", "h"]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


Rects = dict[Hashable, Rect]


@dataclass(frozen=True)
class Segment:
    """A straight run of divider. ``axis="v"`` is the line x = coord (``before``
    is left of it); ``"h"`` is y = coord (``before`` is above). ``coord`` is the
    ``before`` side's far edge; ``start``/``end`` span the run along the line."""

    axis: Axis
    coord: int
    start: int
    end: int
    before: frozenset
    after: frozenset


def _near_far(r: Rect, axis: Axis) -> tuple[int, int]:
    """(near edge, far edge) of ``r`` across the line's normal."""
    return (r.x, r.right) if axis == "v" else (r.y, r.bottom)


def _span(r: Rect, axis: Axis) -> tuple[int, int]:
    """(start, end) of ``r`` along the line."""
    return (r.y, r.bottom) if axis == "v" else (r.x, r.right)


def movable_segment(rects: Rects, axis: Axis, coord: int, along: int, gap: int,
                    whole_line: bool = False) -> Segment | None:
    """The segment a press at ``along`` on the line at ``coord`` would move.

    ``coord`` is the ``before`` side's far edge. With ``whole_line`` the segment
    also crosses crossings, covering the full contiguous straight line.
    """
    before = {k for k, r in rects.items()
              if abs(_near_far(r, axis)[1] - coord) <= ALIGN_TOL_PX}
    after = {k for k, r in rects.items()
             if abs(_near_far(r, axis)[0] - (coord + gap)) <= ALIGN_TOL_PX}

    def seed(side: set) -> Hashable | None:
        for k in side:
            s, e = _span(rects[k], axis)
            if s <= along <= e:
                return k
        return None

    sb, sa = seed(before), seed(after)
    if sb is None or sa is None:
        return None
    start = min(_span(rects[sb], axis)[0], _span(rects[sa], axis)[0])
    end = max(_span(rects[sb], axis)[1], _span(rects[sa], axis)[1])
    # Whole-line runs also join areas merely touching across the gap.
    slack = gap + ALIGN_TOL_PX if whole_line else 0
    picked_b, picked_a = {sb}, {sa}
    grew = True
    while grew:
        grew = False
        for side, picked in ((before, picked_b), (after, picked_a)):
            for k in side - picked:
                s, e = _span(rects[k], axis)
                if s < end + slack and e > start - slack:
                    picked.add(k)
                    start, end = min(start, s), max(end, e)
                    grew = True
    return Segment(axis, coord, start, end, frozenset(picked_b), frozenset(picked_a))


def move_segment(rects: Rects, seg: Segment, delta: int, gap: int,
                 mins: dict[Hashable, tuple[int, int]]) -> tuple[Rects, int]:
    """Shift ``seg`` by ``delta``: ``before`` areas grow, ``after`` areas shrink.

    ``mins`` maps each key to (min_w, min_h). Returns the new rects and the delta
    actually applied after clamping to those minimums.
    """
    v = seg.axis == "v"
    size = (lambda r: r.w) if v else (lambda r: r.h)
    mn = (lambda k: mins[k][0]) if v else (lambda k: mins[k][1])
    lo = -min((size(rects[k]) - mn(k) for k in seg.before), default=0)
    hi = min((size(rects[k]) - mn(k) for k in seg.after), default=0)
    d = max(lo, min(hi, delta))
    out = dict(rects)
    for k in seg.before:
        r = rects[k]
        out[k] = Rect(r.x, r.y, r.w + d, r.h) if v else Rect(r.x, r.y, r.w, r.h + d)
    for k in seg.after:
        r = rects[k]
        out[k] = (Rect(r.x + d, r.y, r.w - d, r.h) if v
                  else Rect(r.x, r.y + d, r.w, r.h - d))
    return out, d


def snap_delta(rects: Rects, seg: Segment, delta: int, gap: int,
               threshold: int = SNAP_PX) -> int:
    """``delta`` adjusted so the segment lands level with the nearest parallel
    edge of a panel outside the segment, if one is within ``threshold``."""
    moving = seg.before | seg.after
    targets: list[int] = []
    for k, r in rects.items():
        if k in moving:
            continue
        near, far = _near_far(r, seg.axis)
        targets += [far, near - gap]  # both as a `before`-side far-edge coord
    here = seg.coord + delta
    best = min(targets, key=lambda t: abs(t - here), default=None)
    if best is not None and abs(best - here) <= threshold:
        return best - seg.coord
    return delta
