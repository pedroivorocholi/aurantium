"""A new indicator draws in; a removed one fades out.

The draw-in rewrites a curve's data frame by frame (a left-to-right reveal),
so the stuck state to fear is a curve left truncated — half an SMA, read as
real. Every test checks the end state: the full series, at full opacity, or
nothing at all.
"""

import pytest
from PySide6.QtWidgets import QApplication

from aurantium import motion


def _bars(n=200):
    t = [1_700_000_000.0 + i * 86400 for i in range(n)]
    c = [100.0 + (i % 17) for i in range(n)]
    return {"t": t, "o": c, "h": [x + 1 for x in c], "l": [x - 1 for x in c],
            "c": c, "v": [1000] * n}


def _chart(monkeypatch, animate):
    monkeypatch.setattr(motion, "animations_enabled", lambda: animate)
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    p.resize(900, 600)
    p.show()
    p._on_history(_bars())
    QApplication.processEvents()
    return p


def _points(item):
    xs, _ys = item.getData()
    return 0 if xs is None else len(xs)


def _full_points(p, inst):
    """What _refresh_indicator lands on, for comparison."""
    p._refresh_indicator(inst)
    return [_points(i) for i in inst.items if hasattr(i, "getData")]


def _in_plot(p, item):
    return item in p.plot_widget.getPlotItem().items


def _settle(p):
    for anim in p._indicator_anims_for_tests():
        anim.setCurrentTime(anim.duration())
    QApplication.processEvents()


def _add_sma(p):
    p._add_indicator("sma", {"window": 20}, color="#3399ff")
    inst = p._indicators[-1]
    p._draw_in(inst)
    return inst


# -- draw in -------------------------------------------------------------------

def test_reduced_motion_adds_the_full_series_at_once(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=False)
    inst = _add_sma(p)
    assert inst.anim is None or not inst.anim.state() == inst.anim.State.Running
    curve = inst.items[0]
    assert curve.opacity() == pytest.approx(1.0)
    assert _points(curve) == 200 - 20 + 1


def test_a_new_indicator_draws_in_left_to_right(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=True)
    inst = _add_sma(p)
    curve = inst.items[0]
    full = 200 - 20 + 1
    inst.anim.setCurrentTime(inst.anim.duration() // 3)
    assert 2 <= _points(curve) < full
    assert curve.opacity() < 1.0
    _settle(p)
    assert _points(curve) == full
    assert curve.opacity() == pytest.approx(1.0)


def test_a_refresh_mid_draw_in_lands_on_the_full_series(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=True)
    inst = _add_sma(p)
    inst.anim.setCurrentTime(inst.anim.duration() // 3)
    p._on_history(_bars())  # live data arrives mid-reveal
    curve = inst.items[0]
    assert inst.anim.state() != inst.anim.State.Running
    assert _points(curve) == 200 - 20 + 1
    assert curve.opacity() == pytest.approx(1.0)


def test_the_add_menu_path_draws_in(qapp, monkeypatch):
    from aurantium.panels import chart as chart_mod

    p = _chart(monkeypatch, animate=True)
    monkeypatch.setattr(chart_mod.QInputDialog, "getInt", lambda *a, **k: (20, True))
    monkeypatch.setattr(p, "_pick_new_indicator_color", lambda label: "#3399ff")
    p._add_indicator_ui("sma")
    inst = p._indicators[-1]
    assert inst.anim is not None and inst.anim.state() == inst.anim.State.Running
    _settle(p)


def test_a_restored_layout_never_animates_its_indicators(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=True)
    p._add_indicator("ema", {"window": 10}, color="#3399ff")
    settings = p.settings()
    q = _chart(monkeypatch, animate=True)
    q.restore(settings)
    assert not list(q._indicator_anims_for_tests())


# -- fade out --------------------------------------------------------------------

def test_reduced_motion_removes_at_once(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=False)
    inst = _add_sma(p)
    curve = inst.items[0]
    p._remove_indicator(inst, animate=True)
    assert not _in_plot(p, curve)
    assert inst not in p._indicators


@pytest.mark.parametrize("kind, params", [("sma", {"window": 20}), ("bb", {"window": 20})])
def test_a_removed_overlay_fades_then_goes(qapp, monkeypatch, kind, params):
    p = _chart(monkeypatch, animate=True)
    p._add_indicator(kind, params, color="#3399ff")
    inst = p._indicators[-1]
    items = list(inst.items)
    p._remove_indicator(inst, animate=True)  # the chip menu's Remove
    assert inst not in p._indicators
    assert inst.chip is None or not inst.chip.isVisible()
    assert all(_in_plot(p, i) for i in items)  # still fading
    _settle(p)
    assert not any(_in_plot(p, i) for i in items)


def test_a_sub_pane_is_removed_at_once(qapp, monkeypatch):
    """A pane holds layout space; fading it then collapsing it is two
    changes for one action."""
    p = _chart(monkeypatch, animate=True)
    p._add_indicator("rsi", {"window": 14}, color="#3399ff")
    inst = p._indicators[-1]
    pane = inst.pane
    p._remove_indicator(inst, animate=True)
    assert inst.pane is None
    assert not pane.isVisible()


def test_removing_mid_draw_in_leaves_nothing_behind(qapp, monkeypatch):
    p = _chart(monkeypatch, animate=True)
    inst = _add_sma(p)
    curve = inst.items[0]
    inst.anim.setCurrentTime(inst.anim.duration() // 3)
    p._remove_indicator(inst, animate=True)
    _settle(p)
    assert not _in_plot(p, curve)
    assert not list(p._indicator_anims_for_tests())
