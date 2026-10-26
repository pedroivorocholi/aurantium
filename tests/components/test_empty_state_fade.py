"""The empty-state message crossfades instead of cutting.

MarketTable's own empty message has faded since it was written; the shared
``EmptyState`` (news, Day Brief, the chart's empty plot, Performance) simply
appeared and vanished. Same overlay, same answer now. The stuck states to
rule out: a message left half-faded over real rows, or one that never comes
back after a quick hide-then-show.
"""

import pytest
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem, QWidget

from aurantium import motion
from aurantium.components.empty_state import EmptyState


@pytest.fixture(params=[True, False], ids=["anim-on", "anim-off"])
def animate(request, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: request.param)
    return request.param


def _settle(widget):
    anim = getattr(widget, "_aurantium_fade", None)
    if anim is not None and anim.state() == anim.State.Running:
        anim.setCurrentTime(anim.duration())
    motion.settle_pending_detach()
    QApplication.processEvents()


@pytest.fixture
def host(qapp):
    w = QWidget()
    w.resize(300, 200)
    w.show()
    yield w
    w.close()
    w.deleteLater()


def test_showing_fades_in_and_lands_opaque(host, animate):
    state = EmptyState(host, "No data")
    state.set_shown(True)
    assert not state.isHidden()
    if animate:
        assert motion.fade_opacity(state) < 1.0  # it travels
    _settle(state)
    assert not state.isHidden()
    assert motion.fade_opacity(state) == pytest.approx(1.0)


def test_hiding_is_immediate(host, animate):
    """Content arriving takes the message away at once: never "empty" over
    real rows, even for a frame."""
    state = EmptyState(host, "No data")
    state.set_shown(True)
    _settle(state)
    state.set_shown(False)
    assert state.isHidden()
    state.set_shown(True)
    if animate:
        assert motion.fade_opacity(state) < 1.0  # back in, from transparent
    _settle(state)
    assert motion.fade_opacity(state) == pytest.approx(1.0)


def test_hiding_mid_fade_in_is_immediate(host, animate):
    state = EmptyState(host, "No data")
    state.set_shown(True)
    state.set_shown(False)
    assert state.isHidden()
    _settle(state)
    assert state.isHidden()


def test_hide_then_show_mid_fade_lands_shown(host, animate):
    state = EmptyState(host, "No data")
    state.set_shown(True)
    _settle(state)
    state.set_shown(False)
    state.set_shown(True)  # rows came and went again before the fade ended
    _settle(state)
    assert not state.isHidden()
    assert motion.fade_opacity(state) == pytest.approx(1.0)


def test_attached_view_crossfades_with_its_rows(qapp, animate):
    table = QTableWidget(0, 1)
    table.resize(300, 200)
    table.show()
    state = EmptyState.attach(table, "No headlines")
    _settle(state)
    assert not state.isHidden()
    table.insertRow(0)
    table.setItem(0, 0, QTableWidgetItem("x"))
    _settle(state)
    assert state.isHidden()
    table.setRowCount(0)
    _settle(state)
    assert not state.isHidden()
    table.close()
    table.deleteLater()


def test_the_chart_empty_plot_uses_the_fade(qapp, monkeypatch):
    calls = []
    monkeypatch.setattr(EmptyState, "set_shown", lambda self, on: calls.append(on))
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    p._sync_empty()
    assert calls  # routed through set_shown, not show()/hide()
