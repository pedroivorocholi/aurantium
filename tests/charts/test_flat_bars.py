"""Flat bars (open = high = low = close) draw as a thin mark, not a wall.

Yield indices (^TYX, ^IRX) print a run of flat hourly bars on a bond-market
holiday. Their wick is a zero-length line, which a cosmetic pen in a QPicture
under the plot's scale paints as a column the full height of the chart: the
"broken candles" — a solid block where Columbus Day should be.
"""

import pytest
from PySide6.QtGui import QImage, QPainter, QTransform

from aurantium.panels.chart import CandlestickItem, OHLCBarItem

W, H = 400, 300


def _painted_rows(item) -> int:
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    # roughly the chart's scale: ~3px per bar, ~660px per yield point
    p.setTransform(QTransform(2.7, 0, 0, -660, -150, 3800))
    item.paint(p)
    p.end()
    return sum(
        1 for y in range(H) if any(img.pixelColor(x, y).alpha() for x in range(W))
    )


@pytest.mark.parametrize("cls", [CandlestickItem, OHLCBarItem])
def test_a_flat_bar_is_not_painted_as_a_column(qapp, cls):
    item = cls()
    t = [68.0, 69.0, 70.0, 71.0, 72.0]
    flat = 5.598
    item.set_ohlc(t, [flat] * 5, [flat] * 5, [flat] * 5, [flat] * 5)
    assert _painted_rows(item) <= 3


def test_a_normal_candle_still_has_its_wick(qapp):
    item = CandlestickItem()
    item.set_ohlc([69.0, 70.0, 71.0], [5.59, 5.59, 5.59], [5.62, 5.62, 5.62],
                  [5.56, 5.56, 5.56], [5.6, 5.6, 5.6])
    # 0.06 of a point at 660px/point: the wick spans ~40 rows
    assert 30 <= _painted_rows(item) <= 50

