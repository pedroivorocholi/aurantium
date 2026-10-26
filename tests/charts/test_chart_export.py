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
    actions = chart._build_export_menu(chart).actions()
    assert [a.text() for a in actions] == ["Save as PNG", "Copy chart image"]
    assert [a.text() for a in actions[0].menu().actions()] == ["Dark…", "Light…"]
    assert [a.text() for a in actions[1].menu().actions()] == ["Dark", "Light"]


def _other_theme():
    from aurantium import theme

    return "light" if theme.active_theme() == "dark" else "dark"


def test_the_export_can_be_in_the_other_theme(chart, qapp):
    from aurantium import theme

    _loaded(chart, qapp)
    other = _other_theme()
    image = chart.chart_image(other)
    assert image.pixelColor(1, 1) == QColor(theme.palette_colors(other)["BG"])
    # same frame either way: only the colours change
    assert image.size() == chart.chart_image().size()


def test_the_other_theme_leaves_the_screen_alone(chart, qapp):
    _loaded(chart, qapp)
    pens = [inst.items[0].opts["pen"].color().name()
            for inst in chart._indicators if inst.items]
    axis = chart.plot_widget.getAxis("left").textPen().color().name()
    before = chart.plot_widget.grab().toImage()
    chart.chart_image(_other_theme())
    qapp.processEvents()
    assert [inst.items[0].opts["pen"].color().name()
            for inst in chart._indicators if inst.items] == pens
    assert chart.plot_widget.getAxis("left").textPen().color().name() == axis
    assert chart.candle_item._up == chart._colors["up"]
    assert chart.plot_widget.grab().toImage() == before


def test_the_other_theme_maps_roles_and_slots(chart, qapp):
    from aurantium import theme
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    here, other = theme.active_theme(), _other_theme()
    src, dst = theme.palette_colors(here), theme.palette_colors(other)
    cmap = _chart_export.color_map(chart, other)
    for role in ("BG", "ACCENT", "UP", "DOWN", "FG_DIM"):
        assert cmap[src[role].lower()] == dst[role]
    assert cmap[theme.series_color(0, here).lower()] == theme.series_color(0, other)
    assert _chart_export.color_map(chart, here) == {}


def test_a_picked_color_is_kept_only_where_it_reads(chart, qapp):
    from aurantium import theme
    from aurantium.panels import _chart_export

    _loaded(chart, qapp)
    other = _other_theme()
    unreadable = "#fafafa" if other == "light" else "#050505"
    readable = "#1565c0"  # mid blue: 3:1 on black and on white
    chart._indicators[0].color = unreadable
    chart._indicators[1].color = readable
    cmap = _chart_export.color_map(chart, other)
    assert cmap[unreadable] == theme.series_color(0, other)
    assert readable not in cmap


def test_an_indicator_with_nothing_drawn_is_left_out_of_the_legend(chart, qapp):
    from aurantium.panels import _chart_export

    chart._current_symbol = "AAPL"
    chart._range = {"preset": "max"}
    chart._on_history(_series([100.0 + (i % 7) for i in range(120)]))  # < 200 bars
    labels = [e.label for e in _chart_export.legend(chart)]
    assert "SMA 50" in labels
    assert "SMA 200" not in labels  # no line on the chart, so no entry


def test_the_header_carries_the_logo_mark_top_right_in_either_theme(chart, qapp):
    from aurantium.panels import _chart_export as ex

    _loaded(chart, qapp)
    for name in ("dark", "light"):
        mark = ex.logo_mark(name, ex.MARK_H * 2)
        assert mark is not None and mark.height() == ex.MARK_H * 2
        assert mark.width() < 2 * mark.height()  # the mark alone, not the wordmark
        image = chart.chart_image(name)
        x = image.width() - ex.PAD * 2 - mark.width() // 2
        band = range(ex.PAD * 2, (ex.PAD + ex.MARK_H) * 2)
        bg = image.pixelColor(1, 1)
        assert any(image.pixelColor(x, y) != bg for y in band)
