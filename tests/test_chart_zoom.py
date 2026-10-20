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

def test_sub_day_ranges_live_behind_custom_not_on_the_chip_row(chart):
    """1h / 4h moved off the row to make room for 5y; custom… reaches them."""
    assert "1h" not in chart._range_buttons
    assert "4h" not in chart._range_buttons


def test_a_range_is_never_greyed_out(chart):
    """On the default 1d interval, 1d used to be disabled — the user had to
    discover that the interval row gated the range row."""
    for label, btn in chart._range_buttons.items():
        assert btn.isEnabled(), label


def test_a_one_hour_lookback_switches_to_1m_bars(chart):
    assert chart._interval == "1d"
    chart._set_custom_range({"lookback": "1h"})
    assert chart._range == {"lookback": "1h"}
    assert chart._interval == "1m"
    assert chart._combo_problem(chart._range, chart._interval) is None


def test_a_four_hour_lookback_loads_5m_bars(chart):
    chart._set_custom_range({"lookback": "4h"})
    assert chart._interval == "5m"
    assert chart._custom_btn.isChecked()
    assert not any(b.isChecked() for b in chart._range_buttons.values())
    assert chart._custom_btn.toolTip() == "Last 4 hours"


def test_a_still_valid_interval_is_kept(chart):
    chart._set_interval("5m")
    chart._set_custom_range({"lookback": "4h"})
    assert chart._interval == "5m"


def test_a_long_range_after_intraday_falls_back_to_daily(chart):
    chart._set_custom_range({"lookback": "1h"})
    chart._set_range_preset("1y")
    assert chart._interval == "1d"


def test_lookback_default_intervals():
    from aurantium.panels.chart import preferred_interval

    assert preferred_interval({"lookback": "30m"}) == "1m"
    assert preferred_interval({"lookback": "1h"}) == "1m"
    assert preferred_interval({"lookback": "90m"}) == "5m"
    assert preferred_interval({"lookback": "4h"}) == "5m"
    assert preferred_interval({"lookback": "3d"}) == "15m"  # as the 5d preset


def test_lookback_tokens_parse():
    from aurantium.panels.chart import lookback_days, lookback_label

    assert lookback_days("4h") == pytest.approx(4 / 24)
    assert lookback_days("90m") == pytest.approx(90 / 1440)
    assert lookback_days("2d") == 2
    for bad in ("0h", "4x", "h", "", None, 4):
        assert lookback_days(bad) is None
    assert lookback_label("1h") == "Last 1 hour"
    assert lookback_label("15m") == "Last 15 minutes"


def test_sub_day_lookbacks_fetch_enough_to_cover_a_weekend(chart):
    """A 1h window on Saturday is the last hour of Friday's session; fetching
    period=1d alone would also leave indicators without warm-up bars."""
    chart._set_custom_range({"lookback": "1h"})
    period, interval = chart._fetch_spec()
    assert period == "5d"
    assert interval == "1m"


def test_a_day_lookback_fetches_back_from_today(chart):
    from datetime import date

    chart._set_custom_range({"lookback": "3d"})
    period, _ = chart._fetch_spec()
    start, _, end = period.partition("..")
    assert end == date.today().isoformat()
    assert (date.today() - date.fromisoformat(start)).days >= 3


@pytest.mark.parametrize("token, hours", [("1h", 1), ("4h", 4)])
def test_a_lookback_frames_just_that_window(chart, token, hours):
    """The window ends at the latest bar and starts N hours before it."""
    chart._set_custom_range({"lookback": token})
    data = _bars()
    chart._on_history(data)
    (x0, x1), _ = chart.plot_widget.getViewBox().viewRange()
    # x is the bar slot: an hour of 1-minute bars is 60 slots wide
    assert x1 - x0 == pytest.approx(60 * hours, rel=0.1)


@pytest.mark.parametrize("old", ["1h", "4h"])
def test_a_layout_saved_with_an_old_sub_day_preset_opens_as_a_lookback(chart, old):
    chart.restore({"range": {"preset": old}, "interval": "5m", "indicators": []})
    assert chart._range == {"lookback": old}
    assert chart._interval == "5m"
    chart._update_range_interval_buttons()
    assert chart._custom_btn.isChecked()
    assert chart.settings()["range"] == {"lookback": old}


def test_a_lookback_survives_a_save_and_restore(chart, qapp):
    from aurantium.panels.chart import ChartPanel

    chart._set_custom_range({"lookback": "90m"})
    other = ChartPanel()
    other.build()
    other.restore(chart.settings())
    assert other._range == {"lookback": "90m"}
    assert other._interval == chart._interval


def test_the_dialog_returns_a_lookback_from_a_quick_pick(chart):
    from datetime import date

    from aurantium.panels.chart import _RangeDialog

    dlg = _RangeDialog(chart, date(2026, 1, 1), date(2026, 6, 1))
    assert dlg.dates_radio.isChecked()  # opens on dates, as before
    dlg._quick_pick("4h")
    assert dlg.result() == dlg.DialogCode.Accepted
    assert dlg.chosen_range() == {"lookback": "4h"}


def test_the_dialog_keeps_the_date_pair_mode(chart):
    from datetime import date

    from aurantium.panels.chart import _RangeDialog

    dlg = _RangeDialog(chart, date(2026, 1, 1), date(2026, 6, 1))
    assert dlg.chosen_range() == {"start": "2026-01-01", "end": "2026-06-01"}


def test_the_dialog_reopens_on_the_current_lookback(chart):
    from datetime import date

    from aurantium.panels.chart import _RangeDialog

    dlg = _RangeDialog(chart, date(2026, 1, 1), date(2026, 6, 1), "90m")
    assert dlg.last_radio.isChecked()
    assert dlg.chosen_range() == {"lookback": "90m"}


def test_the_dialog_refuses_a_lookback_too_short_for_two_bars(chart):
    from datetime import date

    from aurantium.panels.chart import _RangeDialog

    dlg = _RangeDialog(chart, date(2026, 1, 1), date(2026, 6, 1), "5m")
    dlg._try_accept()
    assert dlg.result() != dlg.DialogCode.Accepted
    assert dlg._hint.text()


def test_intraday_history_refreshes_quickly(qapp):
    from aurantium.datahub import DataHub
    from aurantium.providers import register_all_providers

    hub = DataHub.instance()
    # Restore afterwards: real providers left on the shared hub make every
    # later test that subscribes fire live yfinance fetches, which pin the
    # global QThreadPool and starve unrelated tests (symbol search) on CI.
    saved = (list(hub._providers), list(hub._policies), dict(hub._policy_prefix))
    try:
        register_all_providers()
        assert hub._resolve_policy("history:AAPL:5d:1m").ttl_s <= 120
        assert hub._resolve_policy("history:AAPL:1d:15m").ttl_s <= 120
        assert hub._resolve_policy("history:AAPL:1y:1d").ttl_s >= 1800
        assert hub._resolve_policy("history:AAPL:2y:1mo").ttl_s >= 1800
    finally:
        hub._providers[:] = saved[0]
        hub._policies[:] = saved[1]
        hub._policy_prefix.clear()
        hub._policy_prefix.update(saved[2])


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
    vb.setXRange(100, 200, padding=0)
    _, (y0, y1) = vb.viewRange()
    assert y0 < 199.5 and y1 > 300.5
    assert y0 > 190 and y1 < 310, "y should hug the visible bars, not all 600"


def test_price_refit_respects_log_scale(chart):
    chart._set_range_preset("1d")
    data = _bars()
    chart._toggle_log(True)
    chart._on_history(data)
    vb = chart.plot_widget.getViewBox()
    vb.setXRange(100, 200, padding=0)
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
    n = len(data["t"])
    assert x_min >= -3
    assert x_max <= (n - 1) + 10
    assert limits["xRange"][0] is not None, "zooming in must stop at a few bars"


def test_double_click_resets_the_view(chart):
    chart._set_range_preset("1h")
    data = _bars()
    chart._on_history(data)
    vb = chart.plot_widget.getViewBox()
    before = vb.viewRange()[0]
    vb.setXRange(10, 30, padding=0)
    chart._reset_view()
    after = vb.viewRange()[0]
    assert after == pytest.approx(before, rel=1e-6)


# -- closed-market gaps ----------------------------------------------------------

def _two_sessions():
    """Two 6.5-hour sessions of 15-minute bars with a 17.5-hour night between."""
    t0 = 1_750_000_000.0
    day1 = [t0 + i * 900 for i in range(26)]
    day2 = [day1[-1] + 17.5 * 3600 + i * 900 for i in range(26)]
    t = day1 + day2
    c = [100.0 + i for i in range(len(t))]
    return {"t": t, "o": c, "h": [x + 0.5 for x in c],
            "l": [x - 0.5 for x in c], "c": c, "v": [1000] * len(t)}


def test_closed_hours_take_no_room(chart):
    """Bars sit one slot apart, so the overnight close is not a blank strip."""
    chart._set_range_preset("5d")
    chart._set_interval("15m")
    chart._on_history(_two_sessions())
    rows = chart.candle_item._rows
    xs = [r[0] for r in rows]
    assert xs == list(range(52))


def test_the_time_axis_labels_slots_with_real_dates(chart):
    from datetime import datetime

    data = _two_sessions()
    chart._set_range_preset("5d")
    chart._set_interval("15m")
    chart._on_history(data)
    axis = chart.plot_widget.getAxis("bottom")
    levels = axis.tickValues(0, 51, 800)
    ticks = levels[0][1]
    assert 26.0 in ticks, "the second session's open gets a tick"
    labels = axis.tickStrings(ticks, 1, 1)
    second = labels[ticks.index(26.0)]
    assert second == datetime.fromtimestamp(data["t"][26]).strftime("%d %b")


def test_time_and_slot_convert_both_ways(chart):
    data = _two_sessions()
    chart._on_history(data)
    for i in (0, 10, 25, 26, 51):
        assert chart._x_of_time(data["t"][i]) == pytest.approx(i)
        assert chart._time_of_x(i) == pytest.approx(data["t"][i])
    # past the last bar extends at the bar spacing
    assert chart._x_of_time(data["t"][-1] + 1800) == pytest.approx(53)
    assert chart._time_of_x(53.0) == pytest.approx(data["t"][-1] + 1800)


def test_drawings_are_kept_as_times_and_drawn_on_slots(chart):
    data = _two_sessions()
    chart._on_history(data)
    chart._annotations = [{"type": "trendline",
                           "points": [[data["t"][20], 120.0], [data["t"][30], 130.0]]}]
    chart._render_annotations()
    xs, _ys = chart._annotation_items[0].getData()
    assert list(xs) == [20.0, 30.0]
