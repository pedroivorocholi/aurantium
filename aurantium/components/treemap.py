"""Squarified treemap layout — tiles sized by weight, kept close to square.

``layout(weights, w, h)`` is the Bruls–Huizing–van Wijk algorithm: weights are
scaled so their areas sum to ``w * h``, then laid out in rows along the shorter
side of what is left, each row growing while it improves its worst aspect
ratio. Pure geometry, no Qt, so any panel (or test) can call it.

Callers pass weights sorted descending (the algorithm's quality depends on it)
and get rects back in the same order. A zero or negative weight gets a
zero-size rect.
"""

from __future__ import annotations

from collections.abc import Sequence

Rect = tuple[float, float, float, float]


def _worst(row: list[float], side: float) -> float:
    """Worst aspect ratio of ``row`` (areas) laid along a strip of length ``side``."""
    total = sum(row)
    return max(
        max(side * side * a / (total * total), total * total / (side * side * a))
        for a in row
    )


def layout(weights: Sequence[float], w: float, h: float) -> list[Rect]:
    """Rects ``(x, y, w, h)`` tiling ``w`` x ``h``, one per weight, in input order."""
    rects: list[Rect] = [(0.0, 0.0, 0.0, 0.0)] * len(weights)
    total = sum(x for x in weights if x > 0)
    if total <= 0 or w <= 0 or h <= 0:
        return rects
    scale = w * h / total
    items = [(i, x * scale) for i, x in enumerate(weights) if x > 0]

    x0, y0, rw, rh = 0.0, 0.0, float(w), float(h)
    pos = 0
    while pos < len(items):
        side = min(rw, rh)
        row = [items[pos][1]]
        end = pos + 1
        # Grow the row while adding the next tile doesn't worsen its shape.
        while end < len(items) and _worst(row + [items[end][1]], side) <= _worst(row, side):
            row.append(items[end][1])
            end += 1
        thick = sum(row) / side  # strip depth across the remaining rect
        last = end == len(items)
        offset = 0.0
        for k, area in enumerate(row):
            length = area / thick
            if k == len(row) - 1:  # absorb rounding so strips close exactly
                length = side - offset
            i = items[pos + k][0]
            if rw >= rh:  # strip is a column on the left
                rects[i] = (x0, y0 + offset, thick, length)
            else:  # strip is a row on top
                rects[i] = (x0 + offset, y0, length, thick)
            offset += length
        if rw >= rh:
            x0, rw = x0 + thick, rw - thick
        else:
            y0, rh = y0 + thick, rh - thick
        pos = end
        if last:
            break
    return rects
