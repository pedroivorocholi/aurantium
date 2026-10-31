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
import pyqtgraph as pg
from PySide6.QtGui import QColor, QPainter
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
    thread. While that holds the thread the morph waits on its first frame,
    then plays once frames come on time again — never skipped to its end."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    anim = p._morph.anim
    # away for longer than the whole morph
    time.sleep(motion.CHART_MORPH_MS * 1.5 / 1000)
    anim._tick()
    assert anim.currentTime() == 0  # the thread just came back: hold
    anim._tick()  # the next frame on time
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


def _overlay_colors(p, t):
    from PySide6.QtGui import QColor, QImage, QPainter

    morph = p._morph
    morph._t = t
    morph.overlay_opacity = 1.0
    size = p.plot_widget.viewport().size()
    img = QImage(size, QImage.Format.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    morph.paint(painter)
    painter.end()
    seen = set()
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            px = QColor.fromRgba(img.pixel(x, y))
            if px.alpha() == 255:
                seen.add(px.name())
    return seen


@pytest.mark.parametrize("kind", ["candles", "bars"])
def test_the_morph_keeps_the_chart_type(qapp, monkeypatch, feeds, kind):
    """Candles morph as candles and bars as bars — never a line in between."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    p._chart_type = kind
    p._apply_chart_type()
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    assert morph.kind == kind
    colors = _overlay_colors(p, 0.5)
    up = QColor(p._colors["up"]).name()
    down = QColor(p._colors["down"]).name()
    assert colors & {up, down}
    assert QColor(p._colors["line"]).name() not in colors
    _settle(p)
    _assert_final(p)


def test_the_last_frame_is_the_new_chart_bar_for_bar(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    # the new prices re-lay out the price axis, shifting the plot inside the
    # viewport: the last frame must follow it
    QApplication.processEvents()
    end = p._morph.bars_at(1.0)
    _settle(p)
    real = p._morph._series_bars()  # the chart as it stands once landed
    assert len(end.x) == len(real.x)
    assert list(end.c) == pytest.approx(list(real.c), abs=0.5)
    assert list(end.h) == pytest.approx(list(real.h), abs=0.5)


def test_an_area_chart_morphs_as_an_area(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    p._chart_type = "area"
    p._apply_chart_type()
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert p._morph.kind == "area"
    assert _overlay_colors(p, 0.5)
    _settle(p)
    _assert_final(p)


def test_a_line_chart_still_morphs_as_a_line(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    p._chart_type = "line"
    p._apply_chart_type()
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    assert p._morph.kind == "line"
    assert _overlay_colors(p, 0.5)  # it paints
    _settle(p)
    _assert_final(p)


# -- frame cost -------------------------------------------------------------------
#
# The morph rescales the price axis every frame, so whatever a repaint costs,
# the morph pays it ~12 times in 200ms. Two costs made it stutter: the price
# grid drew dozens of lines no eye could see, and every frame painted twice.

def _grid_specs(p):
    axis = p.plot_widget.getAxis("left")
    _axis_spec, tick_specs, _text_specs = axis.generateDrawSpecs(QPainter())
    return tick_specs


def test_the_price_grid_skips_lines_too_faint_to_see(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=False, feeds={"AAA": _bars(100.0, n=1260)})
    p.on_symbol("AAA")
    specs = _grid_specs(p)
    alphas = [pen.color().alpha() for pen, _p1, _p2 in specs]
    assert alphas  # the grid is still there...
    assert min(alphas) >= 10  # ...minus pyqtgraph's ~2% finest level
    # the grid's two visible levels both survive
    assert len(set(alphas)) >= 2


def test_ticks_without_a_grid_are_untouched(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=False, feeds={"AAA": _bars(100.0, n=1260)})
    p._toggle_grid(False)
    p.on_symbol("AAA")
    stock = pg.AxisItem.generateDrawSpecs(p.plot_widget.getAxis("left"), QPainter())
    assert len(_grid_specs(p)) == len(stock[1])


def test_the_candles_fade_in_where_they_will_land(qapp, monkeypatch, feeds):
    """The real candles are drawn through the price axis: fading in while the
    axis was still rescaling, they showed as a second chart off to one side
    of the morphing one. The axis lands before the crossfade starts."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    anim = morph.anim
    for frac in (0.5, 0.6, 0.8):
        anim.setCurrentTime(int(anim.duration() * frac))
        y_now = tuple(p.plot_widget.getViewBox().viewRange()[1])
        assert y_now == pytest.approx(morph._y_to)
    _settle(p)
    _assert_final(p)


def test_the_first_frame_ignores_the_last_morphs_clock(qapp, monkeypatch, feeds):
    """Frame 0 is painted before the animation restarts; its clock still
    reads the previous morph's end, which must not show the real candles."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    _settle(p)
    morph = p._morph
    assert morph.anim.currentTime() == morph.anim.duration()  # stale clock
    morph._step(0.0)
    assert p.candle_item.opacity() == 0.0


def test_a_morph_frame_repaints_through_the_scene_only(qapp, monkeypatch, feeds):
    """The overlay is a child of the viewport, so the scene's repaint already
    repaints it. Asking the overlay separately cost a second full paint per
    frame once the real candles faded in — half the frame rate."""
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    direct = []
    monkeypatch.setattr(morph.overlay, "update", lambda *a: direct.append(a))
    morph._step(0.75)
    assert direct == []
    _settle(p)
    _assert_final(p)


def _windowed_feeds():
    # more history fetched than the 6M window shows, as in the app: the real
    # line runs on past the plot's left edge
    return {"AAA": _bars(100.0, n=400), "BBB": _bars(400.0, n=400, slope=-0.5)}


@pytest.mark.parametrize("kind", ["line", "area"])
def test_the_price_line_is_handed_over_not_crossfaded(qapp, monkeypatch, kind):
    p = _make_chart(monkeypatch, animate=True, feeds=_windowed_feeds())
    p._chart_type = kind
    p._apply_chart_type()
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    morph = p._morph
    anim = morph.anim
    anim.setCurrentTime(int(anim.duration() * 0.75))  # deep in the crossfade
    assert p.line_curve.opacity() == 0.0
    assert morph.overlay_opacity == 1.0
    _settle(p)
    _assert_final(p)


def _plot_pixels(p):
    vb = p.plot_widget.getViewBox()
    rect = p.plot_widget.viewportTransform().mapRect(vb.sceneBoundingRect()).toRect()
    return p.plot_widget.viewport().grab(rect).toImage()


@pytest.mark.parametrize("kind", ["line", "area"])
def test_the_handover_frame_matches_the_real_chart(qapp, monkeypatch, kind):
    """What makes the handover invisible: the overlay's last frame, edges and
    area floor included, is the real price line."""
    p = _make_chart(monkeypatch, animate=True, feeds=_windowed_feeds())
    p._chart_type = kind
    p._apply_chart_type()
    p._toggle_grid(False)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    _settle(p)
    QApplication.processEvents()
    for item in _faded_items_but_price(p):
        item.setVisible(False)  # compare the price line alone
    real = _plot_pixels(p)
    morph = p._morph
    morph._from = morph._series_bars()
    morph._t, morph.overlay_opacity = 1.0, 1.0
    p.line_curve.setOpacity(0.0)
    morph.overlay.setGeometry(p.plot_widget.viewport().rect())
    morph.overlay.show()
    morph.overlay.raise_()
    QApplication.processEvents()
    drawn = _plot_pixels(p)
    assert real.size() == drawn.size()
    # antialiasing may round the two a fraction of a pixel apart; the shape,
    # the edges and the area's floor must not differ (the line's very end,
    # where a sub-pixel shift moves the cap across a column, is skipped)
    for x in range(0, real.width(), 10):
        if not _ink(real, min(x + 3, real.width() - 1))[0]:
            continue
        a, b = _ink(real, x), _ink(drawn, x)
        assert b[0] == pytest.approx(a[0], rel=0.05, abs=20), f"column {x}: ink"
        if a[0]:
            assert b[1] == pytest.approx(a[1], abs=0.6), f"column {x}: centre"


def _ink(img, x):
    """(total brightness, its weighted mean y) down one column."""
    total = weighted = 0.0
    for y in range(img.height()):
        v = QColor.fromRgb(img.pixel(x, y)).red()
        if v > 12:
            total += v
            weighted += v * y
    return total, (weighted / total if total else 0.0)


def _faded_items_but_price(p):
    items = []
    for inst in p._indicators:
        items.extend(inst.items)
    return items


# -- today's data replacing yesterday's ------------------------------------------
#
# The first chart of the day warms from the disk cache (yesterday's bars) and
# morphs into them; the network's fresh bars land a few hundred ms later. That
# used to land the morph on the spot — a cut, then a jump to the new last bar.

def test_a_refresh_mid_morph_retargets_instead_of_cutting(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")  # yesterday's 80 bars, from the cache
    morph = p._morph
    anim = morph.anim
    anim.setCurrentTime(int(anim.duration() * 0.3))
    fresh = _bars(400.0, n=81, slope=-2.0)  # today adds a bar
    p._on_history(fresh)
    assert morph.running()  # still travelling...
    assert anim.currentTime() == int(anim.duration() * 0.3)  # ...from where it was
    assert len(morph.bars_at(0.5).x) == len(morph._series_bars().x)  # toward today's bars
    _settle(p)
    _assert_final(p)
    assert len(p._hist_c) == 81


def test_a_refresh_after_the_morph_is_not_a_morph(qapp, monkeypatch, feeds):
    p = _make_chart(monkeypatch, animate=True, feeds=feeds)
    _load_first(p, monkeypatch)
    p.on_symbol("BBB")
    _settle(p)
    p._on_history(_bars(400.0, n=81, slope=-2.0))
    assert not p._morph.running()
    _assert_final(p)
