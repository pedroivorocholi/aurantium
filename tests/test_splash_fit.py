"""The splash card must exactly fill its window at every display scale.

QSplashScreen sizes its window to the bitmap's logical size rounded, and the
platform rounds that back to physical pixels. A 640px card at 150% is 426.67
logical px; the window rounds up to 427 and the sliver the bitmap doesn't cover
painted white down the right edge. At 225% it rounds *down* and crops the amber
border off instead. ``_splash_pixmap`` resizes the card to the exact window
extent by repeating or dropping interior columns, never by rescaling.
"""

import pytest

from aurantium.__main__ import (
    _splash_pixmap,
    splash_fit_px,
    splash_window_px,
)
from aurantium.paths import BUNDLE_DIR

SCALES = [1.0, 1.1, 1.15, 1.25, 1.33, 1.5, 1.66, 1.75, 1.9, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5]


@pytest.mark.parametrize("dpr", SCALES)
@pytest.mark.parametrize("px", [640, 360])
def test_fitted_extent_is_exactly_the_window(px, dpr):
    fit = splash_fit_px(px, dpr)
    assert splash_window_px(fit, dpr) == fit
    assert abs(fit - px) <= 2  # a column or two, not a resize


@pytest.mark.parametrize("dpr", SCALES)
def test_card_keeps_its_border_on_every_edge(qapp, dpr):
    from PySide6.QtGui import QPixmap

    base = QPixmap(str(BUNDLE_DIR / "aurantium_splash.png"))
    border = base.toImage().pixelColor(0, base.height() // 2)
    img = _splash_pixmap(base, dpr, "LOADING").toImage()
    w, h = img.width(), img.height()

    assert (w, h) == (splash_fit_px(base.width(), dpr), splash_fit_px(base.height(), dpr))
    for y in range(h):
        assert img.pixelColor(w - 1, y) == border, (dpr, "right", y)
        assert img.pixelColor(0, y) == border, (dpr, "left", y)
    for x in range(w):
        assert img.pixelColor(x, h - 1) == border, (dpr, "bottom", x)
        assert img.pixelColor(x, 0) == border, (dpr, "top", x)
