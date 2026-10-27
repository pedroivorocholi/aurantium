"""Icons land on the pixel grid.

Reicon draws on a 24-unit grid with 1.5-unit strokes centred on whole grid
lines. Rendered straight at 16px on a 150% display (24 device px — the
maker's own screen) every one of those strokes straddled two pixels at half
intensity: the plus, the window's minimize bar and the close X all read as
blurred. ``icons.pixmap`` now snaps a stroked glyph's line width to whole
device pixels and shifts it so the lines sit on pixel centres.
"""

import pytest
from PySide6.QtGui import QColor

from aurantium import icons

WHITE = "#ffffff"


def _alpha_rows(kind, dpr, size=icons.SIZE):
    img = icons.pixmap(kind, WHITE, dpr, size).toImage()
    w, h = img.width(), img.height()
    return img, [[QColor.fromRgba(img.pixel(x, y)).alpha() for x in range(w)] for y in range(h)]


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_the_minimize_bar_has_no_half_tone_rows(qapp, dpr):
    img, rows = _alpha_rows("minimize", dpr)
    mid = img.width() // 2
    column = [rows[y][mid] for y in range(img.height())]
    lit = [a for a in column if a > 0]
    # every lit pixel in the bar's middle column is fully on: no half-tone
    # row above or below it
    assert lit and all(a >= 250 for a in lit), column


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_the_plus_has_a_solid_vertical_stem(qapp, dpr):
    img, rows = _alpha_rows("plus", dpr)
    y = img.height() // 4  # above the crossbar: only the stem is here
    lit = [a for a in rows[y] if a > 0]
    assert lit and all(a >= 250 for a in lit), rows[y]


def test_a_glyph_is_never_resampled_by_qt(qapp):
    """The pixmap's logical size is exactly what was asked for, so Qt draws
    it 1:1 instead of scaling it into the button."""
    for dpr in (1.0, 1.25, 1.5, 2.0):
        px = icons.pixmap("close", WHITE, dpr)
        assert px.deviceIndependentSize().width() == pytest.approx(icons.SIZE)


def test_dock_chrome_shows_icons_at_the_size_they_are_painted(qapp):
    """The tab close and area buttons were drawn at 11px and 12px from a
    16px pixmap — Qt shrank them, which blurs even a crisp glyph."""
    from aurantium import theme

    sheet = theme.ADS_STYLESHEET
    assert "iconSize: 11px" not in sheet
    assert "iconSize: 12px" not in sheet
    assert f"qproperty-iconSize: {icons.SIZE}px {icons.SIZE}px" in sheet


def test_chip_row_buttons_use_the_family_size(qapp):
    """14px (21 device px at 150%) has no stroke offset that lands on the
    grid; the + and export buttons use the family size like every other."""
    import inspect

    from aurantium.panels import chart

    src = inspect.getsource(chart)
    assert 'set_icon(self._add_btn, "plus", CHROME_TEXT_DIM, disabled_color=FG_MUTED)' in src


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
@pytest.mark.parametrize("kind", ["close", "plus", "minimize", "menu"])
def test_symmetric_glyphs_render_symmetric(qapp, kind, dpr):
    """The X drew lopsided on a 100% monitor: a 1px stroke has no centre
    pixel to sit on in a 16px icon. Strokes are an even width now, centred
    exactly, at every scale."""
    img, rows = _alpha_rows(kind, dpr)
    n = img.width()
    # within the rasterizer's own rounding (1/255), which no eye can see
    assert all(abs(rows[y][x] - rows[y][n - 1 - x]) <= 2 for y in range(n) for x in range(n))


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_strokes_are_two_solid_pixels(qapp, dpr):
    """Never a faint 1px line, at 100% included."""
    img, rows = _alpha_rows("minimize", dpr)
    mid = img.width() // 2
    solid = [y for y in range(img.height()) if rows[y][mid] >= 250]
    assert len(solid) == 2


@pytest.mark.parametrize("make", ["icon", "two_tone", "set_icon"])
def test_an_icon_built_before_its_window_is_shown_paints_for_its_screen(qapp, make):
    """A window is built before it is shown, while it still reports the
    primary screen's ratio. On a 100% main monitor with the window on a 150%
    laptop panel, every icon used to be a 100% glyph stretched by Qt: the
    lopsided, soft X. Icons now render for the scale they are painted at."""
    from PySide6.QtCore import QSize
    from PySide6.QtWidgets import QToolButton

    if make == "icon":
        ic = icons.icon("close", WHITE, 1.0)
    elif make == "two_tone":
        ic = icons.two_tone_icon("close", WHITE, "#ff0000", 1.0)
    else:
        b = QToolButton()
        icons.set_icon(b, "close", WHITE)
        ic = b.icon()
    for scale in (1.0, 1.5, 2.0):
        got = ic.pixmap(QSize(icons.SIZE, icons.SIZE), scale)
        assert got.toImage() == icons.pixmap("close", WHITE, scale).toImage()


def test_hover_and_disabled_tints_survive(qapp):
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QToolButton

    b = QToolButton()
    icons.set_icon(b, "plus", WHITE, hover_color="#ff0000", disabled_color="#00ff00")
    size = QSize(icons.SIZE, icons.SIZE)
    for mode, color in (
        (QIcon.Mode.Normal, WHITE),
        (QIcon.Mode.Active, "#ff0000"),
        (QIcon.Mode.Disabled, "#00ff00"),
    ):
        got = b.icon().pixmap(size, 1.5, mode)
        assert got.toImage() == icons.pixmap("plus", color, 1.5).toImage()


def _stroke_px(kind, dpr):
    """The thickest run of solid pixels down the glyph's columns."""
    img, rows = _alpha_rows(kind, dpr)
    best = 0
    for x in range(img.width()):
        run = 0
        for y in range(img.height()):
            run = run + 1 if rows[y][x] >= 200 else 0
            best = max(best, run) if run < img.height() // 3 else best
    return best


@pytest.mark.parametrize("dpr", [1.0, 1.5])
@pytest.mark.parametrize("kind", ["expand", "link", "search", "export-image", "help"])
def test_the_panel_chrome_glyphs_share_one_line_weight(qapp, kind, dpr):
    """The panel-maximize arrows were a filled outline drawn at 1.5 units,
    visibly thinner beside the stroked icons once those went to 2px. Every
    chrome glyph is stroked (or, for help, thickened) to the same weight."""
    assert _stroke_px(kind, dpr) >= _stroke_px("minimize", dpr)


def test_chrome_glyphs_are_stroked_where_reicon_has_a_stroked_version():
    from aurantium.reicon_icons import SVG

    for kind in ("expand", "link", "search", "export-image"):
        assert 'stroke="currentColor"' in SVG[kind], kind
