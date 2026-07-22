"""Shared pyqtgraph helpers: bounded views and hover readouts.

pyqtgraph's default ViewBox lets the user zoom and pan infinitely past the
data. :func:`clamp_view` pins a plot's view limits to its data extent (plus
a small margin) every time the data changes, and :func:`attach_hover` adds
the crosshair-with-readout every analytics chart should have.
"""

from __future__ import annotations

from typing import Callable, Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt

from ..theme import ACCENT, FG_DIM


def view_limits(
    xs: list[float], ys: list[float], pad: float = 0.08
) -> Optional[dict]:
    """ViewBox ``setLimits`` kwargs covering the data extent plus ``pad``
    of the span on every side; degenerate spans get ±1. None when empty."""
    if not xs or not ys:
        return None
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    x_pad = (x1 - x0) * pad or 1.0
    y_pad = (y1 - y0) * pad or 1.0
    return {
        "xMin": x0 - x_pad,
        "xMax": x1 + x_pad,
        "yMin": y0 - y_pad,
        "yMax": y1 + y_pad,
    }


def clamp_view(
    plot_widget: pg.PlotWidget,
    xs: list[float],
    ys: list[float],
    pad: float = 0.08,
    lock: bool = False,
) -> None:
    """Clamp ``plot_widget`` so the user can't zoom/pan past the data.
    ``lock=True`` additionally disables mouse interaction and keeps the
    plot auto-fitted (right for small fixed-point charts like a curve)."""
    vb = plot_widget.getViewBox()
    limits = view_limits(xs, ys, pad)
    if limits is None:
        return
    vb.setLimits(**limits)
    if lock:
        vb.setMouseEnabled(x=False, y=False)
        plot_widget.hideButtons()
        vb.enableAutoRange()


def attach_hover(
    plot_widget: pg.PlotWidget,
    formatter: Callable[[float, float], Optional[str]],
) -> None:
    """Crosshair + readout following the mouse. ``formatter(x, y)`` maps the
    mouse position (data coords) to the readout text — return None to hide
    (e.g. no nearby data point). The formatter is where snapping to the
    nearest point happens; this helper only owns the widgets."""
    vline = pg.InfiniteLine(
        angle=90, movable=False, pen=pg.mkPen(FG_DIM, style=Qt.PenStyle.DotLine)
    )
    vline.setVisible(False)
    plot_widget.addItem(vline, ignoreBounds=True)
    label = pg.TextItem(color=ACCENT, anchor=(0, 1))
    label.setVisible(False)
    plot_widget.addItem(label, ignoreBounds=True)

    vb = plot_widget.getViewBox()

    def on_move(pos) -> None:
        if not plot_widget.sceneBoundingRect().contains(pos):
            vline.setVisible(False)
            label.setVisible(False)
            return
        point = vb.mapSceneToView(pos)
        text = formatter(point.x(), point.y())
        if not text:
            vline.setVisible(False)
            label.setVisible(False)
            return
        vline.setPos(point.x())
        label.setText(text)
        label.setPos(point.x(), point.y())
        vline.setVisible(True)
        label.setVisible(True)

    # keep a reference on the widget so the proxy isn't garbage-collected
    plot_widget._hover_proxy = pg.SignalProxy(  # noqa: SLF001 (deliberate attach)
        plot_widget.scene().sigMouseMoved, rateLimit=45, slot=lambda ev: on_move(ev[0])
    )
