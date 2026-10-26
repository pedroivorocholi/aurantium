"""The icon family: one set, one optical size, tinted from the palette.

Every icon in aurantium comes from Reicon (https://reicon.dev, MIT licensed),
vendored into ``reicon_icons.py`` by ``tools/codegen/vendor_reicon.py``. That replaced
a hand-painted 16px set (1.4px strokes drawn with QPainter) that never read as
one consistent family next to the unicode glyphs (``?``, ``+``, ``⋯``, ``◀``)
still set as button text elsewhere. Those are icons from this set now too.

Each glyph is a 24x24 SVG painted in ``currentColor``; it is rendered with
QtSvg at the family's logical size, for the screen's device pixel ratio, in
whatever palette colour the caller passes, so a theme switch never needs new
assets.

What is deliberately NOT here
-----------------------------

``▲`` and ``▼`` stay as text. They are the colour-blind direction marks that
``theme.tick_glyph`` prefixes to a signed number *inside a table cell*: they
have to scale with the cell's font, sit on its baseline, and survive being
copied out with the text. An icon cannot do any of those, and this is an
accessibility path, not decoration.

``▸`` stays as text. It is a menu-path separator in prose ("Settings ▸ Theme"),
not something the user can click.
"""

from __future__ import annotations

import re
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .reicon_icons import SVG, VIEWBOX
from .theme import ICON_PX

#: Logical size of every glyph in the family. One optical size, so an icon
#: never has to be scaled to sit beside another one.
SIZE = ICON_PX

#: Every kind this module can paint. Used by the tests to hold the family
#: closed — an affordance added as unicode text somewhere else in the app is
#: exactly the regression this module exists to prevent.
KINDS = tuple(SVG)


#: Reicon's stroke weight, in grid units.
_STROKE = 1.5
_STROKE_WIDTH = re.compile(r'stroke-width="[\d.]+"')


def _hint(kind: str, device_px: int) -> tuple[str, float]:
    """The glyph's markup and grid offset for a ``device_px``-wide render.

    Reicon strokes are 1.5 units wide and centred on whole grid lines. Drawn
    as-is at 24 device pixels (16px at 150%) each one straddles two pixels at
    half intensity — the blur. So a stroked glyph gets a line width of a whole
    number of device pixels, and is shifted so the lines its strokes are
    centred on (the grid's centre line, and the rest of the grid with it at
    the family's sizes) land on pixel centres for an odd width, or pixel edges
    for an even one. Filled glyphs are drawn as authored: their outlines are
    curves, which antialias whatever the grid.
    """
    body = SVG[kind]
    if 'stroke="currentColor"' not in body:
        return body, 0.0
    scale = device_px / VIEWBOX
    # just under the true weight, rounded: 1px at 16px/150%, 2px at 200%
    width_px = max(1, round(_STROKE * scale - 0.25))
    body = _STROKE_WIDTH.sub(f'stroke-width="{width_px / scale:.4f}"', body)
    target = 0.5 if width_px % 2 else 0.0
    shift_px = (target - (VIEWBOX / 2 * scale) % 1.0) % 1.0
    return body, shift_px / scale


@lru_cache(maxsize=512)
def _renderer(kind: str, color: str, device_px: int = 0) -> QSvgRenderer:
    body, shift = _hint(kind, device_px) if device_px else (SVG[kind], 0.0)
    body = body.replace("currentColor", color)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{-shift:.4f} {-shift:.4f} {VIEWBOX} {VIEWBOX}" '
        f'fill="none">{body}</svg>'
    )
    return QSvgRenderer(QByteArray(svg.encode("utf-8")))


def icon(kind: str, color: str, dpr: float = 1.0) -> QIcon:
    """One glyph, in one colour, at one device-pixel-ratio."""
    return QIcon(pixmap(kind, color, dpr))


def pixmap(kind: str, color: str, dpr: float = 1.0, size: int = SIZE) -> QPixmap:
    """The glyph as a pixmap — for a QLabel, which takes no QIcon."""
    dpr = dpr or 1.0
    device_px = round(size * dpr)
    px = QPixmap(device_px, device_px)
    # the exact ratio this pixmap has, so Qt draws it 1:1 at ``size`` logical
    # pixels rather than resampling it (which blurs even a crisp glyph)
    px.setDevicePixelRatio(device_px / size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _renderer(kind, str(color), device_px).render(painter, QRectF(0, 0, size, size))
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

    Used by the list editor's row-delete button (grey to the loss red) and the
    title bar's close button (white on the red hover fill).
    """
    result = QIcon()
    result.addPixmap(pixmap(kind, color, dpr), QIcon.Mode.Normal)
    result.addPixmap(pixmap(kind, hover_color, dpr), QIcon.Mode.Active)
    result.addPixmap(pixmap(kind, hover_color, dpr), QIcon.Mode.Selected)
    return result


def set_icon(
    button,
    kind: str,
    color: str,
    hover_color: str | None = None,
    disabled_color: str | None = None,
    size: int = SIZE,
) -> None:
    """Put glyph ``kind`` on a QToolButton/QPushButton in place of its text,
    painted for the button's screen, with optional hover (``Active``) and
    disabled tints — Qt's own disabled tint of an icon is a grey wash that
    matches nothing in the palette."""
    from PySide6.QtCore import QSize

    dpr = device_pixel_ratio(button)
    result = QIcon()
    result.addPixmap(pixmap(kind, color, dpr, size), QIcon.Mode.Normal)
    if hover_color:
        hover = pixmap(kind, hover_color, dpr, size)
        result.addPixmap(hover, QIcon.Mode.Active)
        result.addPixmap(hover, QIcon.Mode.Selected)
    if disabled_color:
        result.addPixmap(pixmap(kind, disabled_color, dpr, size), QIcon.Mode.Disabled)
    button.setText("")
    button.setIcon(result)
    button.setIconSize(QSize(size, size))


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
