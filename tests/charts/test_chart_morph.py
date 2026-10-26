"""The chart's symbol-change morph (motion.py, "Chart data transitions").

A linked symbol change used to be a cut: the old candles sat there through the
fetch, then the new ones replaced them and the axes jumped in one frame. Now
the old line morphs into the new one. Everything that can go wrong with that
is a stuck in-between state — candles left half-transparent, an overlay left
painted over the real chart, a Y range frozen mid-way — so every test below
checks the *end* state, with animations on and off.
"""

import time

import pytest
from PySide6.QtWidgets import QApplication

from aurantium import motion


def _bars(base, n=120, step=86400.0, t0=1_700_000_000.0, slope=1.0):
    t = [t0 + i * step for i in range(n)]
    c = [base + slope * i for i in range(n)]
    return {
        "t": t,
        "o": c,
        "h": [x + 0.5 for x in c],
        "l": [x - 0.5 for x in c],
        "c": c,
        "v": [1000] * n,
    }


def _make_chart(monkeypatch, animate: bool, feeds: dict):
    monkeypatch.setattr(motion, "animations_enabled", lambda: animate)
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    p.setMinimumSize(0, 0)
    p.resize(900, 600)
    p.show()
    QApplication.processEvents()

    def fake_resubscribe(symbol):
        # a cache hit: DataHub calls back synchronously, inside on_symbol
        data = feeds.get(symbol)
        if data is not None:
            p._on_history(data)

    monkeypatch.setattr(p, "_resubscribe", fake_resubscribe)
    return p


def _price_items(p):
    return [p.candle_item, p.bar_item, p.line_curve]


def _fitted_y(p):
    """What _fit_y_to_visible lands on, computed fresh."""
    p._fit_y_to_visible()
    return tuple(p.plot_widget.getViewBox().viewRange()[1])


def _assert_final(p):
    morph = p._morph
    assert not morph.running()
    assert not morph.overlay.isVisible()
    for item in _price_items(p):
        assert item.opacity() == pytest.approx(1.0)
    y_now = tuple(p.plot_widget.getViewBox().viewRange()[1])
    assert y_now == pytest.approx(_fitted_y(p))


def _settle(p):
    anim = p._morph.anim
    if anim.state() != anim.State.Stopped:
        anim.setCurrentTime(anim.duration())
    QApplication.processEvents()


@pytest.fixture
def feeds():
    return {"AAA": _bars(100.0), "BBB": _bars(400.0, n=80, slope=-2.0)}


def _load_first(p, monkeypatch):
    p.on_symbol("AAA")
    assert p._hist_t  # loaded
    # a later change must not count as "too fast after the previous one"
    monkeypatch.setattr(
        p._morph, "_last_change", time.monotonic() - 10.0, raising=True
    )


# -- animations off: the old behaviour, exactly ------------------------------

def test_reduced_motion_takes_no_snapshot_and_lands_on_the_final_chart(
    qapp, monkeypatch, feeds
):
    p = _make_chart(monkeypatch, animate=False, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert p._morph._snapshot is None
    _assert_final(p)
    assert p._hist_c[0] == pytest.approx(400.0)


# -- animations on --------------------------------------------------------------

def test_a_symbol_change_morphs_and_settles_on_the_final_chart(
    qapp, monkeypatch, feeds
):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    assert morph.running()
    assert morph.overlay.isVisible()
    # at the start the real candles are hidden under the morphing line
    assert p.candle_item.opacity() < 0.5
    _settle(p)
    _assert_final(p)


def test_the_morph_interpolates_between_the_two_shapes(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    start = morph.points_at(0.0)
    end = morph.points_at(1.0)
    mid = morph.points_at(0.5)
    assert len(start) == len(end) == len(mid)
    # old series climbs (y pixels fall left→right), new one falls
    assert start[0].y() > start[-1].y()
    assert end[0].y() < end[-1].y()
    for a, m, b in zip(start, mid, end):
        assert m.y() == pytest.approx((a.y() + b.y()) / 2, abs=0.01)
    _settle(p)


def test_first_load_does_not_morph(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    p.on_symbol("AAA")
    assert not p._morph.running()
    _assert_final(p)


def test_a_change_inside_the_morph_window_snaps(qapp, monkeypatch, feeds):
    """Stepping through a watchlist must never animate."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert p._morph.running()
    p.on_symbol("AAA")  # immediately again
    assert not p._morph.running()
    _assert_final(p)
    assert p._hist_c[0] == pytest.approx(100.0)


def test_a_slow_fetch_still_morphs_from_the_old_series(qapp, monkeypatch, feeds):
    """Not cached: the history lands later, as its own callback."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    monkeypatch.setattr(p, "_resubscribe", lambda symbol: None)
    p.on_symbol("BBB")
    assert not p._morph.running()
    p._on_history(feeds["BBB"])
    assert p._morph.running()
    _settle(p)
    _assert_final(p)


def test_a_live_refresh_of_the_same_symbol_never_morphs(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p._on_history(feeds["AAA"])
    assert not p._morph.running()
    _assert_final(p)


def test_a_range_change_never_morphs(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p._set_range_preset("1y")
    p._on_history(feeds["AAA"])
    assert not p._morph.running()
    _assert_final(p)


def test_an_empty_result_cancels_the_morph(qapp, monkeypatch, feeds):
    feeds["BBB"] = {"t": [], "o": [], "h": [], "l": [], "c": []}
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert not p._morph.running()
    assert not p._morph.overlay.isVisible()
    for item in _price_items(p):
        assert item.opacity() == pytest.approx(1.0)


def test_a_user_pan_mid_morph_lands_on_the_final_state(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert p._morph.running()
    p.plot_widget.getViewBox().sigRangeChangedManually.emit([True, False])
    assert not p._morph.running()
    _assert_final(p)


def test_closing_the_panel_mid_morph_is_safe(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    p.close()
    p.deleteLater()
    QApplication.processEvents()


def test_a_stalled_ui_thread_delays_the_morph_instead_of_skipping_it(
    qapp, monkeypatch, feeds
):
    """A linked symbol change re-renders every panel in the group on the UI
    thread. However long that holds the thread, the morph's clock advances at
    most two frames per tick, so its frames still reach the screen."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    anim = p._morph.anim
    time.sleep(0.5)  # the thread is away for longer than the whole morph
    anim._tick()
    assert 0 < anim.currentTime() <= motion.PacedAnimation.MAX_STEP_MS
    assert p._morph.running()
    _settle(p)
    _assert_final(p)


def test_a_paced_morph_runs_to_the_end_on_its_own(qapp, monkeypatch, feeds):
    from PySide6.QtTest import QTest

    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    QTest.qWait(motion.CHART_MORPH_MS * 4)
    _assert_final(p)


def test_volume_rises_with_the_morph(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    p._add_indicator("volume", {}, color="#3399ff")
    vol = p._indicators[-1]
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert vol.anim is not None and vol.anim.state() != vol.anim.State.Stopped
    for anim in [vol.anim, p._morph.anim]:
        anim.setCurrentTime(anim.duration())
    QApplication.processEvents()
    assert list(vol.pane_items[0].opts["height"]) == pytest.approx([1000] * 80)
    assert vol.pane_items[0].opacity() == pytest.approx(1.0)
    _assert_final(p)


def test_volume_does_not_animate_with_reduced_motion(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=False, feeds=feeds)
    p._add_indicator("volume", {}, color="#3399ff")
    vol = p._indicators[-1]
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert vol.anim is None or vol.anim.state() == vol.anim.State.Stopped
    assert list(vol.pane_items[0].opts["height"]) == pytest.approx([1000] * 80)
