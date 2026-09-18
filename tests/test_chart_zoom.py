"""Sub-day ranges and chart navigation.

Two complaints: there was no way to look at less than a day (and on the
default 1d interval even the ``1d`` range was greyed out), and zooming was
pyqtgraph's default — the wheel scaled price and time together around the
cursor, drag panned both axes off into empty space, and the price axis never
refit to what was on screen.
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
    p.resize(900, 600)
    return p


def _bars(n=600, step=60.0, t0=1_750_000_000.0):
    """A synthetic 1-minute series climbing linearly, so any window's price
    extent is known exactly."""
    t = [t0 + i * step for i in range(n)]
    c = [100.0 + i for i in range(n)]
    return {
        "t": t,
        "o": c,
        "h": [x + 0.5 for x in c],
        "l": [x - 0.5 for x in c],
        "c": c,
        "v": [1000] * n,
    }


# -- sub-day ranges ------------------------------------------------------------

def test_there_are_ranges_shorter_than_a_day(chart):
    assert "1h" in chart._range_buttons
    assert "4h" in chart._range_buttons


def test_a_range_is_never_greyed_out(chart):
    """On the default 1d interval, 1d / 4h / 1h all used to be disabled —
    the user had to discover that the interval row gated the range row."""
    for label, btn in chart._range_buttons.items():
        assert btn.isEnabled(), label


def test_picking_a_short_range_switches_to_an_intraday_interval(chart):
    assert chart._interval == "1d"
    chart._set_range_preset("1h")
    assert chart._range == {"preset": "1h"}
    assert chart._interval == "1m"
    assert chart._combo_problem(chart._range, chart._interval) is None


def test_a_still_valid_interval_is_kept(chart):
    chart._set_interval("5m")
    chart._set_range_preset("4h")
    assert chart._interval == "5m"


def test_a_long_range_after_intraday_falls_back_to_daily(chart):
    chart._set_range_preset("1h")
    chart._set_range_preset("1y")
    assert chart._interval == "1d"


def test_sub_day_ranges_fetch_enough_to_cover_a_weekend(chart):
    """A 1h window on Saturday is the last hour of Friday's session; fetching
    period=1d alone would also leave indicators without warm-up bars."""
    chart._set_range_preset("1h")
    period, interval = chart._fetch_spec()
    assert period == "5d"
    assert interval == "1m"


def test_a_sub_day_range_frames_just_that_window(chart):
    chart._set_range_preset("1h")
    data = _bars()
    chart._on_history(data)
    (x0, x1), _ = chart.plot_widget.getViewBox().viewRange()
    assert x1 - x0 == pytest.approx(3600, rel=0.1)


def test_intraday_history_refreshes_quickly(qapp):
    from aurantium.datahub import DataHub
    from aurantium.providers import register_all_providers

    hub = DataHub.instance()
    register_all_providers()
    assert hub._resolve_policy("history:AAPL:5d:1m").ttl_s <= 120
    assert hub._resolve_policy("history:AAPL:1d:15m").ttl_s <= 120
    assert hub._resolve_policy("history:AAPL:1y:1d").ttl_s >= 1800
    assert hub._resolve_policy("history:AAPL:2y:1mo").ttl_s >= 1800


# -- navigation ----------------------------------------------------------------

def test_the_mouse_moves_time_not_price(chart):
    vb = chart.plot_widget.getViewBox()
    assert vb.state["mouseEnabled"] == [True, False]


def test_indicator_panes_follow_the_same_rule(chart):
    panes = [inst.pane for inst in chart._indicators if inst.pane is not None]
    assert panes, "default RSI pane expected"
    for pane in panes:
        assert pane.getViewBox().state["mouseEnabled"] == [True, False]


def test_price_refits_to_the_visible_bars_after_a_zoom(chart):
    chart._set_range_preset("1d")
    data = _bars()
    chart._on_history(data)
    vb = chart.plot_widget.getViewBox()
    # zoom into bars 100..200: closes 200..300, so lows 199.5 / highs 300.5
    vb.setXRange(data["t"][100], data["t"][200], padding=0)
    _, (y0, y1) = vb.viewRange()
    assert y0 < 199.5 and y1 > 300.5
    assert y0 > 190 and y1 < 310, "y should hug the visible bars, not all 600"


def test_price_refit_respects_log_scale(chart):
    chart._set_range_preset("1d")
    data = _bars()
    chart._toggle_log(True)
    chart._on_history(data)
    vb = chart.plot_widget.getViewBox()
    vb.setXRange(data["t"][100], data["t"][200], padding=0)
    _, (y0, y1) = vb.viewRange()
    assert y0 < math.log10(199.5) and y1 > math.log10(300.5)
    assert y1 < 3.0  # log units, not raw prices


def test_you_cannot_pan_off_into_empty_space(chart):
    chart._set_range_preset("1d")
    data = _bars()
    chart._on_history(data)
    limits = chart.plot_widget.getViewBox().state["limits"]
    x_min, x_max = limits["xLimits"]
    assert x_min is not None and x_max is not None
    assert x_min >= data["t"][0] - 3600
    assert x_max <= data["t"][-1] + 3600 * 3
    assert limits["xRange"][0] is not None, "zooming in must stop at a few bars"


def test_double_click_resets_the_view(chart):
    chart._set_range_preset("1h")
    data = _bars()
    chart._on_history(data)
    vb = chart.plot_widget.getViewBox()
    before = vb.viewRange()[0]
    vb.setXRange(data["t"][10], data["t"][30], padding=0)
    chart._reset_view()
    after = vb.viewRange()[0]
    assert after == pytest.approx(before, rel=1e-6)
