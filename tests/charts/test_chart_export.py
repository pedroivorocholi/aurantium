"""The chart image export (panels/_chart_export.py).

It used to be ``panel.grab()``: the whole panel, control rows included, at
screen resolution. Now it is the plot area only, at twice the on-screen
pixels, under a header that names what was plotted.
"""

import pytest
from PySide6.QtGui import QColor

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
    return {"t": t, "o": closes, "h": [c + 1 for c in closes],
            "l": [c - 1 for c in closes], "c": closes, "v": [1000] * len(closes)}


def _loaded(chart, qapp, n=260):
    chart._current_symbol = "AAPL"  # no provider fetch: the bars come below
    chart._range = {"preset": "1y"}
    chart._on_history(_series([100.0 + (i % 17) for i in range(n)]))
    qapp.processEvents()
    return chart


def test_export_is_twice_the_plot_size(chart, qapp):
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    image = chart.chart_image()
    assert image is not None
    plot_w, plot_h = _chart_export.plot_size(chart)
    assert plot_w > 0 and plot_h > 0
    assert image.width() == 2 * plot_w
    # the plot area is 2x; the header and footer frame it
    frame = image.height() - 2 * plot_h
    assert frame > 0 and frame % 2 == 0


def test_export_covers_every_plot_pane_and_no_controls(chart, qapp):
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    panes = _chart_export.plot_panes(chart)
    # the default working set: price, the events lane, and RSI's own pane
    assert chart.plot_widget in panes
    assert any(inst.pane in panes for inst in chart._indicators if inst.pane is not None)
    # the control rows are not plot panes
    assert chart._export_btn not in panes


def test_the_legend_names_every_visible_series(chart, qapp):
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    cmp = chart._add_compare("XLK", subscribe=False)
    chart._on_compare_history(cmp, _series([50.0 + i * 0.1 for i in range(260)]))
    labels = [e.label for e in _chart_export.legend(chart)]
    for inst in chart._indicators:
        assert inst.label() in labels
    assert any(l.startswith("vs XLK") for l in labels)
    off = chart._indicators[0]  # SMA 50
    off.on = False
    assert off.label() not in [e.label for e in _chart_export.legend(chart)]


def test_the_caption_says_interval_range_and_log(chart, qapp):
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    chart._log_on = True
    _sym, rest = _chart_export.caption(chart)
    assert chart._interval in rest and "1y" in rest and "log" in rest


def test_export_leaves_the_screen_alone(chart, qapp):
    _loaded(chart, qapp)
    before = chart.plot_widget.grab().toImage()
    size = chart.size()
    chart._cross_v.setVisible(True)
    chart.chart_image()
    assert chart._cross_v.isVisible()  # hidden for the render only
    chart._cross_v.setVisible(False)
    qapp.processEvents()
    assert chart.size() == size
    assert chart.plot_widget.grab().toImage() == before


def test_nothing_plotted_nothing_exported(chart):
    assert chart.chart_image() is None


def test_the_export_is_painted_on_the_chart_background(chart, qapp):
    _loaded(chart, qapp)
    image = chart.chart_image()
    assert image.pixelColor(1, 1) == QColor(chart._colors["bg"])


def test_copy_puts_the_image_on_the_clipboard(chart, qapp):
    _loaded(chart, qapp)
    chart._copy_chart_image()
    got = qapp.clipboard().image()
    assert not got.isNull()
    assert got.width() == chart.chart_image().width()


def test_the_toolbar_and_the_menu_both_reach_it(chart):
    from aurantium import icons

    assert chart._export_btn.toolTip()
    assert "export-image" in icons.KINDS
    texts = [a.text() for a in chart._build_export_menu(chart).actions()]
    assert texts == ["Save as PNG…", "Copy chart image"]


def test_an_indicator_with_nothing_drawn_is_left_out_of_the_legend(chart, qapp):
    from aurantium.panels import _chart_export

    chart._current_symbol = "AAPL"
    chart._range = {"preset": "max"}
    chart._on_history(_series([100.0 + (i % 7) for i in range(120)]))  # < 200 bars
    labels = [e.label for e in _chart_export.legend(chart)]
    assert "SMA 50" in labels
    assert "SMA 200" not in labels  # no line on the chart, so no entry
