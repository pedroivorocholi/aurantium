"""Comparing the chart's symbol with another one (sector ETF, index, peer).

The overlay is rebased: level with the price at the first bar in view, then
moving by its own percentage, so where the lines part is where one beat the
other.
"""

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


DAY = 86400.0
T0 = 1_750_000_000.0


def _series(closes, t0=T0, step=DAY):
    t = [t0 + i * step for i in range(len(closes))]
    return {"t": t, "o": closes, "h": [c + 1 for c in closes],
            "l": [c - 1 for c in closes], "c": closes, "v": [1000] * len(closes)}


def test_align_takes_the_latest_close_at_or_before_each_bar():
    from aurantium.panels.chart import align_closes

    bars = [10.0, 20.0, 30.0, 40.0, 500.0]
    other_t = [9.0, 25.0, 40.0]
    other_c = [1.0, 2.0, 3.0]
    out = align_closes(bars, other_t, other_c, bar_seconds=10.0)
    assert out == [1.0, 1.0, 2.0, 3.0, None]  # long after its last bar: no data
    assert align_closes([5.0], other_t, other_c, 10.0) == [None]  # before its first


def test_the_overlay_starts_level_and_moves_by_its_own_percent(chart):
    chart._range = {"preset": "max"}
    chart._on_history(_series([100.0 + i for i in range(20)]))
    cmp = chart._add_compare("XLK", subscribe=False)
    chart._on_compare_history(cmp, _series([50.0 * (1.01 ** i) for i in range(20)]))
    chart.plot_widget.getViewBox().setXRange(0, 19, padding=0)
    ys = chart._compare_values(cmp)
    assert ys[0] == pytest.approx(100.0)  # level with the price at the left edge
    assert ys[10] == pytest.approx(100.0 * 1.01 ** 10)
    assert chart._compare_pct(cmp, 10) == pytest.approx((1.01 ** 10 - 1) * 100)


def test_panning_re_anchors_on_the_first_bar_in_view(chart):
    chart._range = {"preset": "max"}
    chart._on_history(_series([100.0 + i for i in range(40)]))
    cmp = chart._add_compare("SPY", subscribe=False)
    chart._on_compare_history(cmp, _series([10.0 + i for i in range(40)]))
    chart.plot_widget.getViewBox().setXRange(20, 39, padding=0)
    ys = chart._compare_values(cmp)
    assert ys[20] == pytest.approx(chart._hist_c[20])


def test_the_chip_reports_the_move_over_the_bars_in_view(chart):
    chart._range = {"preset": "max"}
    chart._on_history(_series([100.0] * 11))
    cmp = chart._add_compare("XLK", subscribe=False)
    chart._on_compare_history(cmp, _series([50.0] * 10 + [55.0]))
    chart.plot_widget.getViewBox().setXRange(0, 10, padding=0)
    assert cmp.chip.text() == "vs XLK +10.0%"
    assert "-10.00 pts" in cmp.chip.toolTip()


def test_comparisons_survive_save_and_restore(chart):
    chart._add_compare("XLK", color="#3366cc", subscribe=False)
    chart._add_compare("^GSPC", on=False, subscribe=False)
    saved = chart.settings()["compare"]
    assert saved == [{"symbol": "XLK", "color": "#3366cc", "on": True},
                     {"symbol": "^GSPC", "color": saved[1]["color"], "on": False}]
    chart._clear_compares()
    chart.restore({"compare": saved})
    assert [c.symbol for c in chart._compares] == ["XLK", "^GSPC"]
    assert chart._compares[1].on is False


def test_no_duplicates_and_a_cap(chart):
    from aurantium.panels.chart import MAX_COMPARES

    assert chart._add_compare("XLK", subscribe=False) is not None
    assert chart._add_compare("xlk", subscribe=False) is None
    for sym in ("A1", "A2", "A3", "A4"):
        chart._add_compare(sym, subscribe=False)
    assert len(chart._compares) == MAX_COMPARES


def test_removing_a_comparison_takes_its_line_off_the_chart(chart):
    cmp = chart._add_compare("XLK", subscribe=False)
    curve = cmp.curve
    chart._remove_compare(cmp)
    assert curve not in chart.plot_widget.getPlotItem().items
    assert chart._compares == []


def test_the_compare_menu_offers_sector_index_and_other(chart):
    chart._current_symbol = "AAPL"
    texts = [a.text() for a in chart._build_compare_menu(chart).actions()]
    assert texts[0].startswith("Sector ETF")
    assert texts[1] == "Index · ^GSPC"
    assert texts[2] == "Other symbol…"
