"""Bars before 1970 (``max`` on an old index or a long-listed stock).

On Windows ``datetime.fromtimestamp`` rejects negative timestamps with
OSError 22. The crosshair readout raised on every mouse move over such a bar;
the time axis caught it but labelled every pre-1970 bar 1970-01-01; the
events lane and the day lookup dropped those days silently.
"""

from datetime import datetime

import pytest

from aurantium import motion

DAY = 86400.0
T0 = -252_331_200.0  # 1962-01-02 00:00 UTC


def _series(n, t0=T0, step=DAY):
    closes = [10.0 + (i % 9) for i in range(n)]
    t = [t0 + i * step for i in range(n)]
    return {"t": t, "o": closes, "h": [c + 1 for c in closes],
            "l": [c - 1 for c in closes], "c": closes, "v": [1000] * n}


@pytest.fixture
def chart(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    p.resize(900, 600)
    p._current_symbol = "KO"
    p._range = {"preset": "max"}
    p._on_history(_series(400))
    return p


def test_bar_datetime_handles_times_before_1970():
    from aurantium.date_context import bar_datetime

    assert bar_datetime(T0).year == 1962
    assert bar_datetime(1_750_000_000.0) == datetime.fromtimestamp(1_750_000_000.0)


def test_the_crosshair_readout_works_on_a_1962_bar(chart):
    chart._update_readout(0)  # raised OSError 22 on Windows
    assert "1962" in chart._cross_text.textItem.toPlainText()


def test_the_time_axis_dates_pre_1970_bars_correctly(chart):
    dts = chart._clock.datetimes()
    assert dts[0].year == 1962
    assert dts[0] != dts[1]


def test_the_day_under_the_crosshair_is_found_before_1970(chart):
    chart._cross_index = 5
    assert chart._date_under_cursor().startswith("1962")



def test_the_events_lane_keeps_pre_1970_days():
    from aurantium.date_context import bar_datetime
    from aurantium.panels._events_lane import MARK_STYLE, build_marks

    data = _series(30)
    day = bar_datetime(data["t"][3]).date().isoformat()
    kind = next(iter(MARK_STYLE))
    marks = build_marks(data["t"], data["c"], [{"date": day, "kind": kind}])
    assert any(m["date"] == day and m["kind"] == kind for m in marks)
