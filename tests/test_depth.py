"""Depth: chrome sits above the data surface, in both themes.

The app was flat. The menu row, the command bar, every panel header strip and
every data surface sat on one plane, told apart only by fill — and two of them
were not even told apart by that: ``QWidget#commandBar`` painted ``BG`` and
``_HeaderStrip`` painted ``BG``, the same true black as the table beneath them.

The treatment is one treatment, applied everywhere chrome meets data: a raised
surface gets a lit top edge and a dark bottom edge. These tests hold the two
new palette entries in the right order relative to the surfaces they sit
between, in *both* themes — which is the part a single-theme eyeball check
misses, because "one step up from the background" inverts between them.
"""

import pytest
from PySide6.QtGui import QColor

from aurantium import theme

THEMES = list(theme.THEMES)


def _lum(value: str) -> float:
    """Perceived lightness, 0-1. Enough to order two greys; the real contrast
    maths lives in ``aurantium.color``."""
    c = QColor(value)
    return (0.2126 * c.redF() + 0.7152 * c.greenF() + 0.0722 * c.blueF())


# -- the palette entries ----------------------------------------------------


@pytest.mark.parametrize("name", THEMES)
def test_both_themes_define_the_depth_colours(name):
    palette = theme.palette_colors(name)
    assert "CHROME_LOW" in palette
    assert "CHROME_EDGE" in palette


@pytest.mark.parametrize("name", THEMES)
def test_the_panel_header_plane_sits_between_the_data_and_the_chrome(name):
    """CHROME_LOW is the panel header strip: chrome, one step down from the
    dock title bar above it. It has to be distinguishable from the data surface
    or the strip goes back to reading as the table's first row — and it must
    not overtake full CHROME, or the panel grows a second title bar."""
    p = theme.palette_colors(name)
    data, low, chrome = _lum(p["BG"]), _lum(p["CHROME_LOW"]), _lum(p["CHROME"])
    if data < 0.5:  # dark theme: chrome is lighter than the data surface
        assert data < low < chrome
    else:  # light theme: chrome is darker than the paper
        assert data > low > chrome
    assert abs(low - data) > 0.001, "CHROME_LOW is indistinguishable from BG"


@pytest.mark.parametrize("name", THEMES)
def test_the_lit_edge_is_lighter_than_the_surface_it_sits_on(name):
    """CHROME_EDGE is a highlight, in both themes. On the light theme that
    means it goes *up* to white while CHROME_BORDER goes down — the same
    physical model, not an inverted one."""
    p = theme.palette_colors(name)
    assert _lum(p["CHROME_EDGE"]) > _lum(p["CHROME"])
    assert _lum(p["CHROME_BORDER"]) < _lum(p["CHROME"])


# -- the surfaces that use them ---------------------------------------------


def test_the_command_bar_is_no_longer_on_the_data_plane():
    """It painted ``BG`` — the identical true black as the table under it — so
    the one control the user types into had no edge at all."""
    sheet = theme.STYLESHEET
    assert f"QWidget#commandBar {{\n    background: {theme.CHROME};" in sheet
    assert f"QWidget#commandBar {{ background: {theme.BG};" not in sheet


def test_the_command_bar_is_raised_on_both_edges():
    sheet = theme.STYLESHEET
    start = sheet.index("QWidget#commandBar {")
    rule = sheet[start : sheet.index("}", start)]
    assert f"border-top: 1px solid {theme.CHROME_EDGE}" in rule
    assert f"border-bottom: 1px solid {theme.CHROME_BORDER}" in rule


def test_the_command_label_clears_its_background():
    """The blanket ``QWidget`` rule paints every plain widget BG, which on a
    chrome bar is a black box around the label — the trap ``menuBarLogo``
    already documents."""
    sheet = theme.STYLESHEET
    start = sheet.index("QLabel#commandLabel {")
    rule = sheet[start : sheet.index("}", start)]
    assert "background: transparent" in rule


def test_the_dock_title_bar_is_raised_on_both_edges():
    sheet = theme.ADS_STYLESHEET
    start = sheet.index("ads--CDockAreaTitleBar {")
    rule = sheet[start : sheet.index("}", start)]
    assert f"border-top: 1px solid {theme.CHROME_EDGE}" in rule
    assert f"border-bottom: 1px solid {theme.CHROME_BORDER}" in rule


def test_the_panel_header_strip_paints_the_chrome_plane(qapp):
    """The strip paints itself so it can animate the link flash, which means
    its fill is in Python rather than in the sheet and no stylesheet assertion
    can reach it."""
    from aurantium.panel import _HeaderStrip

    strip = _HeaderStrip(None)
    strip.resize(240, 21)
    image = strip.grab().toImage()
    middle = image.pixelColor(120, 10)
    assert middle == QColor(theme.CHROME_LOW)
    assert middle != QColor(theme.BG), "back on the data plane"


def test_the_panel_header_strip_has_a_lit_top_edge(qapp):
    from aurantium.panel import _HeaderStrip

    strip = _HeaderStrip(None)
    strip.resize(240, 21)
    image = strip.grab().toImage()
    assert image.pixelColor(120, 0) == QColor(theme.CHROME_EDGE)
    assert image.pixelColor(120, 20) == QColor(theme.BORDER)


def test_the_flash_still_wins_over_the_edge(qapp):
    """A highlight drawn over a coloured link flash reads as a second,
    competing signal — and the flash is the product's differentiator, so it is
    the one that gets the strip."""
    from aurantium.panel import _HeaderStrip

    strip = _HeaderStrip(None)
    strip.resize(240, 21)
    strip.set_flash(1.0)
    image = strip.grab().toImage()
    assert image.pixelColor(120, 0) != QColor(theme.CHROME_EDGE)
    assert image.pixelColor(120, 10) != QColor(theme.CHROME_LOW)


def test_the_data_surface_itself_stays_flat():
    """Depth separates chrome from data. It does not decorate the data: a table
    with a bevel on it is a regression, not an improvement."""
    sheet = theme.STYLESHEET
    start = sheet.index("QTableWidget, QTableView {")
    rule = sheet[start : sheet.index("}", start)]
    assert theme.CHROME_EDGE not in rule
    assert f"background: {theme.BG}" in rule
