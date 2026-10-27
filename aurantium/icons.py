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

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
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
    # Always an even number of device pixels, and at least 2. Even, because
    # an even-width line centres exactly on the glyph's midpoint at every
    # size: a 1px line has no centre pixel to sit on in a 16px icon, so at
    # 100% the X landed half a pixel off and read as lopsided. At least 2,
    # because 1px read as faint: 2px at 100%, 150% and 200%.
    width_px = max(2, 2 * round(_STROKE * scale / 2))
    body = _STROKE_WIDTH.sub(f'stroke-width="{width_px / scale:.4f}"', body)
    # even width: the lines' edges, not their centres, go on pixel edges
    shift_px = (-(VIEWBOX / 2 * scale)) % 1.0
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


class _GlyphEngine(QIconEngine):
    """Paints a glyph for the screen it is being drawn on, when it is drawn.

    Icons used to be rendered once, at construction, for the ratio of the
    screen the widget reported then. A window is built before it is shown, so
    that was the *primary* screen's — and on a desk with a 100% main monitor
    and a 150% laptop panel, every icon in a window on the laptop was a 100%
    glyph (1px strokes, half a pixel off centre) stretched by Qt to 150%: a
    lopsided, soft X. Rendered on demand at the scale Qt asks for, an icon is
    right on whichever screen it is on, and stays right when the window is
    dragged to the other one.
    """

    def __init__(self, kind: str, colors: dict, size: int = 0) -> None:
        super().__init__()
        self._kind = kind
        #: QIcon.Mode -> colour; Normal is always present
        self._colors = colors
        self._size = size

    def _color(self, mode) -> str:
        return self._colors.get(mode) or self._colors[QIcon.Mode.Normal]

    def scaledPixmap(self, size, mode, state, scale):  # noqa: N802 (Qt override)
        logical = max(1, min(size.width(), size.height()))
        return _cached_pixmap(self._kind, self._color(mode), float(scale or 1.0), logical)

    def pixmap(self, size, mode, state):  # noqa: D102 (Qt override)
        return self.scaledPixmap(size, mode, state, 1.0)

    def paint(self, painter, rect, mode, state) -> None:  # noqa: D102 (Qt override)
        side = min(rect.width(), rect.height())
        scale = painter.device().devicePixelRatioF() if painter.device() else 1.0
        px = self.scaledPixmap(QSize(side, side), mode, state, scale)
        target = QRect(0, 0, side, side)
        target.moveCenter(rect.center())
        painter.drawPixmap(target.topLeft(), px)

    def actualSize(self, size, mode, state):  # noqa: N802 (Qt override)
        side = min(size.width(), size.height())
        if self._size:
            side = min(side, self._size)
        return QSize(side, side)

    def availableSizes(self, mode=QIcon.Mode.Normal, state=QIcon.State.Off):  # noqa: N802
        return [QSize(self._size or SIZE, self._size or SIZE)]

    def clone(self) -> "_GlyphEngine":
        return _GlyphEngine(self._kind, dict(self._colors), self._size)

    def key(self) -> str:
        return "aurantium-glyph"


@lru_cache(maxsize=1024)
def _cached_pixmap(kind: str, color: str, dpr: float, size: int) -> QPixmap:
    # icons repaint with their buttons; the glyph is rendered once per
    # (kind, colour, scale, size) rather than on every paint
    return pixmap(kind, color, dpr, size)


def _glyph_icon(kind: str, colors: dict, size: int = 0) -> QIcon:
    return QIcon(_GlyphEngine(kind, {m: str(c) for m, c in colors.items() if c}, size))


def icon(kind: str, color: str, dpr: float = 1.0) -> QIcon:
    """One glyph, in one colour, painted for whatever screen shows it.

    ``dpr`` is accepted for existing callers and no longer needed: the icon
    renders at paint time for the screen it is on (see ``_GlyphEngine``)."""
    return _glyph_icon(kind, {QIcon.Mode.Normal: color})


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
    return _glyph_icon(
        kind,
        {
            QIcon.Mode.Normal: color,
            QIcon.Mode.Active: hover_color,
            QIcon.Mode.Selected: hover_color,
        },
    )


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
    colors = {QIcon.Mode.Normal: color}
    if hover_color:
        colors[QIcon.Mode.Active] = colors[QIcon.Mode.Selected] = hover_color
    if disabled_color:
        colors[QIcon.Mode.Disabled] = disabled_color
    button.setText("")
    button.setIcon(_glyph_icon(kind, colors, size))
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
