"""The splash card's progress bar.

A determinate hairline under the LOADING line, filled by counted startup
steps (providers, panel discovery, window, then one step per restored panel).
The Qt event loop is not running during startup, so the bar steps at each
repaint rather than gliding — the same with animations on or off. What can go
wrong is geometry: a bar off-centre, under the status text, or outside the
card's amber border at some display scale.
"""

import pytest
from PySide6.QtGui import QColor, QPixmap

from aurantium.__main__ import (
    _SPLASH_BAR_W,
    _SPLASH_BAR_Y,
    _splash_pixmap,
)
from aurantium.paths import ASSETS_DIR
from aurantium.theme import palette_colors

DARK = palette_colors("dark")


@pytest.fixture
def base(qapp):
    pix = QPixmap(str(ASSETS_DIR / "images" / "aurantium_splash.png"))
    assert not pix.isNull()
    return pix


def _px(pix, x, y) -> str:
    return QColor(pix.toImage().pixel(x, y)).name()


def _bar_x0(pix) -> int:
    return (pix.width() - _SPLASH_BAR_W) // 2


@pytest.mark.parametrize("progress", [0.0, 0.5, 1.0])
def test_the_bar_fills_to_the_step_reached(base, progress):
    pix = _splash_pixmap(base, 1.0, "LOADING", progress)
    x0 = _bar_x0(pix)
    filled = int(round(_SPLASH_BAR_W * progress))
    for dx in (0, _SPLASH_BAR_W // 4, _SPLASH_BAR_W // 2, _SPLASH_BAR_W - 1):
        want = DARK["ACCENT_DEEP"] if dx < filled else DARK["CHROME_EDGE"]
        assert _px(pix, x0 + dx, _SPLASH_BAR_Y) == want, dx


def test_no_progress_means_no_bar(base):
    """The first paint (before any step is counted) is the baked card."""
    pix = _splash_pixmap(base, 1.0, "LOADING")
    x0 = _bar_x0(pix)
    assert _px(pix, x0 + 1, _SPLASH_BAR_Y) == DARK["BG"]


def test_progress_is_clamped(base):
    over = _splash_pixmap(base, 1.0, "LOADING", 1.7)
    full = _splash_pixmap(base, 1.0, "LOADING", 1.0)
    assert over.toImage() == full.toImage()


def test_the_bar_sits_below_the_status_line_and_inside_the_border(base):
    from aurantium.__main__ import _SPLASH_TEXT_Y

    assert _SPLASH_BAR_Y > _SPLASH_TEXT_Y + 24  # clear of the text rect
    assert _SPLASH_BAR_Y < base.height() - 2  # above the bottom border


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.25])
def test_the_bar_survives_every_display_scale(base, dpr):
    pix = _splash_pixmap(base, dpr, "LOADING", 1.0)
    x_mid = pix.width() // 2
    assert _px(pix, x_mid, _SPLASH_BAR_Y) == DARK["ACCENT_DEEP"]


def test_layout_restore_reports_one_step_per_panel(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    win = MainWindow()
    try:
        win.add_panel("chart")
        win.add_panel("company")
        doc = win.serialize_layout()
        steps = []
        win.layout_progress = lambda i, n: steps.append((i, n))
        assert win.apply_layout(doc)
        assert steps == [(1, 2), (2, 2)]
    finally:
        win.layout_progress = None
        win.close()
        win.deleteLater()
