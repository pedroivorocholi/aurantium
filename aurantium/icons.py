"""The painted icon family — one stroke weight, one optical size, one grid.

aurantium already had a good icon set: six 16x16 glyphs painted into a pixmap
for the panel title bars, antialiased, on a 1.4px cosmetic round-cap stroke and
device-pixel-ratio aware so they stay hairline-sharp on a HiDPI display. It
lived as a private method on ``MainWindow``.

The problem was everything *outside* it. Affordances elsewhere in the app were
unicode characters set as button text — ``✕`` for remove, ``⠿`` for a drag
grip — which means the system picks whichever installed font happens to carry
the codepoint. That font is almost never the UI font: on a stock Windows 11
install ``⠿`` comes from a braille fallback and ``✕`` from Segoe UI Symbol,
each with its own stroke weight, its own vertical metrics and its own idea of
how big a 10px glyph should be. Beside a painted 1.4px icon they visibly
disagree, and there is no stylesheet property that can make them agree.

So the affordances are painted too, on the same grid and with the same pen.

What is deliberately NOT here
-----------------------------

``▲`` and ``▼`` stay as text. They are the colour-blind direction marks that
``theme.tick_glyph`` prefixes to a signed number *inside a table cell*: they
have to scale with the cell's font, sit on its baseline, and survive being
copied out with the text. An icon cannot do any of those, and this is an
accessibility path, not decoration.

``▸`` stays as text. It is a menu-path separator in prose ("Settings ▸ Theme"),
not something the user can click.

The grid
--------

16x16 logical units, painted into a ``round(16 * dpr)`` pixmap tagged with that
ratio. Strokes are cosmetic, so the 1.4px weight is 1.4 *device* pixels at any
scale factor — which is what keeps a hairline a hairline rather than letting it
bloat to 2.8px on a 200% display. Round caps and round joins throughout.

Marks are drawn on integer or half-integer coordinates depending on whether the
stroke needs to straddle a pixel boundary or sit inside one: a 1.4px stroke
centred on x=4.0 covers 3.3 to 4.7, which antialiases across two columns, while
``drawRect(4, 4, 8, 8)`` lands its edges where the existing ``maximize`` glyph
has always put them. Values here match what shipped; do not "tidy" them without
looking at the result at 100% and 200%.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

#: Logical size of every glyph in the family. One optical size, so an icon
#: never has to be scaled to sit beside another one.
SIZE = 16

#: The one stroke weight. Cosmetic, so it means 1.4 device pixels at any
#: device-pixel-ratio rather than 1.4 logical units scaled up with everything
#: else.
STROKE = 1.4

#: Dot diameter, for the one glyph made of dots rather than lines.
#:
#: Not ``STROKE``, and the difference is the point. A dot's *diameter* and a
#: line's *thickness* are different quantities: a 1.4px dot carries about a
#: sixth of the ink of a 6px line at 1.4px, so matching the two numbers makes
#: the dots read as a sixth of the family's weight — which is exactly how the
#: first attempt looked, rendered side by side with the other six glyphs at
#: 1x, 2x and 4x. 2.4px is where a dot's mass matches a stroke's. Going on to
#: 2.8 starts to merge the columns at 100%.
DOT = 2.4

#: Every kind this module can paint. Used by the tests to hold the family
#: closed — a seventh glyph added as unicode text somewhere else in the app is
#: exactly the regression this module exists to prevent.
KINDS = (
    "close",
    "expand",
    "maximize",
    "restore",
    "menu",
    "pin",
    "grip",
)


def _paint(painter: QPainter, kind: str, color: QColor) -> None:
    """Draw one glyph into ``painter``, which is already set up on the 16x16
    logical grid with antialiasing on."""
    pen = QPen(color)
    pen.setWidthF(STROKE)
    pen.setCosmetic(True)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "close":
        painter.drawLine(QPointF(5, 5), QPointF(11, 11))
        painter.drawLine(QPointF(11, 5), QPointF(5, 11))
    elif kind == "expand":  # diagonal arrows to opposite corners → fill window
        painter.drawLine(QPointF(7, 7), QPointF(4, 4))
        painter.drawLine(QPointF(4, 4), QPointF(4, 7))
        painter.drawLine(QPointF(4, 4), QPointF(7, 4))
        painter.drawLine(QPointF(9, 9), QPointF(12, 12))
        painter.drawLine(QPointF(12, 12), QPointF(12, 9))
        painter.drawLine(QPointF(12, 12), QPointF(9, 12))
    elif kind == "maximize":
        painter.drawRect(4, 4, 8, 8)
    elif kind == "restore":
        painter.drawRect(QRectF(5.5, 3.5, 6, 6))  # back square (upper-right)
        painter.drawRect(QRectF(3.5, 5.5, 6, 6))  # front square (lower-left)
    elif kind == "menu":
        for y in (5, 8, 11):
            painter.drawLine(QPointF(4, y), QPointF(12, y))
    elif kind == "pin":
        painter.drawEllipse(QPointF(8, 6), 2.6, 2.6)
        painter.drawLine(QPointF(8, 8.6), QPointF(8, 12.5))
    elif kind == "grip":
        # Two columns of three dots, the same arrangement as the ⠿ it replaces.
        #
        # Painted as round-capped points rather than as filled ellipses so the
        # dots stay *cosmetic*, like every stroke in the family: a filled
        # ellipse is in logical units and would grow with the device pixel
        # ratio while the lines beside it did not, which is precisely the
        # optical-size disagreement this module exists to remove.
        #
        # 4px between columns and 3.5px between rows: tight enough to read as
        # one object at 16px, open enough that the dots do not merge at 100%.
        pen.setWidthF(DOT)
        painter.setPen(pen)
        for x in (6.0, 10.0):
            for y in (4.5, 8.0, 11.5):
                painter.drawPoint(QPointF(x, y))


def icon(kind: str, color: str, dpr: float = 1.0) -> QIcon:
    """One glyph, in one colour, at one device-pixel-ratio."""
    return QIcon(pixmap(kind, color, dpr))


def pixmap(kind: str, color: str, dpr: float = 1.0) -> QPixmap:
    """The glyph as a pixmap — for a QLabel, which takes no QIcon."""
    dpr = dpr or 1.0
    px = QPixmap(round(SIZE * dpr), round(SIZE * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _paint(painter, kind, QColor(color))
    painter.end()
    return px


def two_tone_icon(
    kind: str, color: str, hover_color: str, dpr: float = 1.0
) -> QIcon:
    """A glyph that changes colour under the pointer.

    QSS can recolour text on ``:hover``; it cannot recolour an icon, which is
    the one thing lost by moving an affordance from a character to a painted
    mark. ``QIcon`` carries the state itself: Qt asks for the ``Active`` mode
    pixmap while the mouse is over the control and ``Normal`` otherwise, so the
    hover colour survives the move.

    Used by the list editor's row-delete button, whose ``✕`` went from grey to
    the loss red on hover and would otherwise have gone flat.
    """
    result = QIcon()
    result.addPixmap(pixmap(kind, color, dpr), QIcon.Mode.Normal)
    result.addPixmap(pixmap(kind, hover_color, dpr), QIcon.Mode.Active)
    result.addPixmap(pixmap(kind, hover_color, dpr), QIcon.Mode.Selected)
    return result


def device_pixel_ratio(widget=None) -> float:
    """The ratio to paint for: the widget's own screen when there is one, else
    the primary screen, else 1.0.

    Asking the widget matters on a multi-monitor desk with mixed scaling, which
    is the normal case for the kind of user who runs a terminal — the icon has
    to be painted for the display the window is actually on.
    """
    if widget is not None:
        try:
            ratio = widget.devicePixelRatioF()
            if ratio:
                return ratio
        except (AttributeError, RuntimeError):
            pass
    from PySide6.QtWidgets import QApplication

    screen = QApplication.primaryScreen()
    return screen.devicePixelRatio() if screen is not None else 1.0
