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


@pytest.mark.parametrize("dpr", [1.5, 2.0])
@pytest.mark.parametrize("kind", ["close", "plus"])
def test_symmetric_glyphs_render_symmetric(qapp, kind, dpr):
    """The X once drew with one diagonal heavier than the other: a 1px stroke
    nudged half a pixel off the glyph's centre. (At 100% the stroke is 1px on
    a 16px canvas, which has no centre pixel: crisp there means half a pixel
    off centre, so symmetry is only asserted for the even widths.)"""
    img, rows = _alpha_rows(kind, dpr)
    n = img.width()
    assert all(rows[y][x] == rows[y][n - 1 - x] for y in range(n) for x in range(n))


def test_strokes_are_never_thinner_than_designed_at_150(qapp):
    """1.5-unit strokes at 24 device px: 2px, not a faint 1px."""
    img, rows = _alpha_rows("minimize", 1.5)
    mid = img.width() // 2
    solid = [y for y in range(img.height()) if rows[y][mid] >= 250]
    assert len(solid) == 2
