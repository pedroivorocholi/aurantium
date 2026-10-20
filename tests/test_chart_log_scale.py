"""Log scale (Y) on the price chart.

pyqtgraph's log mode only transforms items that implement ``setLogMode``:
its own curves did, the candles and OHLC bars (custom QPicture items) did
not, so switching log on fitted the view to log10(price) ~ 2.5 while the
candles stayed drawn at 340 — off screen. The stock axis in log mode labels
only decades (10, 100, 1000), so a 240–340 chart had no price labels at all.
"""

import math

import pytest

from aurantium import motion


@pytest.fixture
def chart(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    p.setMinimumSize(0, 0)
    p.resize(900, 640)
    p.show()
    qapp.processEvents()
    return p


DAY = 86400.0
T0 = 1_750_000_000.0


def _series(closes, t0=T0, step=DAY):
    t = [t0 + i * step for i in range(len(closes))]
    return {"t": t, "o": closes, "h": [c + 2 for c in closes],
            "l": [c - 2 for c in closes], "c": closes, "v": [1000] * len(closes)}


def _loaded(chart, qapp):
    chart._current_symbol = "AAPL"
    chart._range = {"preset": "1y"}
    chart._on_history(_series([240.0 + (i * 0.4) for i in range(260)]))
    qapp.processEvents()


def _y_range(chart):
    return chart.plot_widget.getViewBox().viewRange()[1]


@pytest.mark.parametrize("kind,item", [("candles", "candle_item"), ("bars", "bar_item")])
def test_price_marks_stay_in_view_in_log_scale(chart, qapp, kind, item):
    _loaded(chart, qapp)
    chart._set_chart_type(kind)
    chart._toggle_log(True)
    qapp.processEvents()
    lo, hi = _y_range(chart)
    rect = getattr(chart, item).boundingRect()
    # drawn in the same (log10) units as the view, and inside it
    assert lo - 0.05 <= rect.top() and rect.bottom() <= hi + 0.05
    assert rect.bottom() == pytest.approx(math.log10(max(chart._hist_hi)), abs=0.01)


def test_switching_log_off_puts_the_candles_back_in_price_units(chart, qapp):
    _loaded(chart, qapp)
    chart._toggle_log(True)
    chart._toggle_log(False)
    qapp.processEvents()
    rect = chart.candle_item.boundingRect()
    assert rect.bottom() == pytest.approx(max(chart._hist_hi), abs=1.0)


def test_the_log_axis_labels_prices_inside_a_decade(chart, qapp):
    _loaded(chart, qapp)
    chart._toggle_log(True)
    qapp.processEvents()
    axis = chart.plot_widget.getAxis("left")
    lo, hi = _y_range(chart)
    levels = axis.tickValues(lo, hi, 400)
    values = [v for _spacing, vs in levels for v in vs if lo <= v <= hi]
    assert len(values) >= 3, values
    labels = axis.tickStrings(values, 1.0, levels[0][0])
    prices = [float(s.replace(",", "")) for s in labels]
    for v, price in zip(values, prices):
        assert price == pytest.approx(10 ** v, rel=1e-6)


def test_points_placed_in_log_scale_are_stored_as_prices(chart, qapp):
    from PySide6.QtCore import QPointF

    _loaded(chart, qapp)
    chart._toggle_log(True)
    i = 100
    # the pointer over the bar's high: snaps to the high, not to the low
    t, price = chart._snap_point(QPointF(i, math.log10(chart._hist_hi[i])))
    assert price == chart._hist_hi[i]
    # a free (unsnapped) point is a price, not log10 of one
    chart._draw_tool = "channel"
    from aurantium.panels.chart import DRAW_TOOL_FREE_POINTS

    free = next((j for tool, js in DRAW_TOOL_FREE_POINTS.items() for j in js
                 if (chart.__setattr__("_draw_tool", tool) or True)), None)
    assert free is not None
    _t, p2 = chart._place_point(QPointF(i, math.log10(321.0)), free)
    assert p2 == pytest.approx(321.0)


def test_a_channel_fill_follows_log_scale(chart, qapp):
    _loaded(chart, qapp)
    chart._toggle_log(True)
    fb = chart._fill_item(([0, 10], [300.0, 310.0], [0, 10], [280.0, 290.0]), preview=False)
    ys = fb.curves[0].yData if hasattr(fb, "curves") else None
    if ys is None:
        ys = fb.curve1.yData if hasattr(fb, "curve1") else None
    assert ys is not None
    assert max(ys) == pytest.approx(math.log10(310.0), abs=1e-6)


def test_toggling_log_keeps_the_window_and_fits_everything_in_it(chart, qapp):
    """pyqtgraph's setLogMode re-enables auto-range on both axes: the chart
    jumped back to all loaded bars, and y fitted only the candles, so a
    comparison rebased to the new left edge ran off the bottom."""
    _loaded(chart, qapp)
    cmp = chart._add_compare("XLK", subscribe=False)
    chart._on_compare_history(cmp, _series([50.0 + i * 0.3 for i in range(260)]))
    vb = chart.plot_widget.getViewBox()
    vb.setXRange(150, 230, padding=0)
    qapp.processEvents()
    before = vb.viewRange()[0]
    chart._toggle_log(True)
    qapp.processEvents()
    x0, x1 = vb.viewRange()[0]
    assert x0 == pytest.approx(before[0], abs=0.5) and x1 == pytest.approx(before[1], abs=0.5)
    assert vb.autoRangeEnabled() == [False, False]
    lo, hi = vb.viewRange()[1]
    ys = [y for y in chart._compare_values(cmp)[151:230] if y is not None]
    assert lo <= math.log10(min(ys)) and math.log10(max(ys)) <= hi
