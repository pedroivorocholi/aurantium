"""The workspace skeleton: each restored panel's outline, standing in for it
until its data arrives (motion.py, "Loading pulse").

The failure that matters is the stuck one — a placeholder left over a panel
that has long since loaded, or a pulse still ticking with nothing on screen
(idle CPU, all day). So every path out is tested: data, error, empty, a plain
clear, the hard cap, and the panel closing underneath it.
"""

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QTableWidget

from aurantium import motion
from aurantium import panel as panel_mod


@pytest.fixture(params=[True, False], ids=["anim-on", "anim-off"])
def animate(request, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: request.param)
    # a pulse of this test's own: windows other tests left open may still
    # hold skeletons (their 8s cap never elapses inside a test run)
    monkeypatch.setattr(panel_mod, "_pulse", None)
    return request.param


@pytest.fixture
def panel(qapp, animate):
    class _P(panel_mod.Panel):
        def build(self):
            self.table = QTableWidget(5, 3, self)
            self.content_layout.addWidget(self.table)

    p = _P()
    p.build()
    p.resize(400, 300)
    p.show()
    QApplication.processEvents()
    yield p
    p.close()
    p.deleteLater()
    _flush_deletes()


def _flush_deletes():
    """deleteLater needs the event loop; processEvents alone never runs it."""
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _settle():
    # fades are 160ms; drive them to the end rather than sleeping
    for anim in list(panel_mod._skeleton_anims_for_tests()):
        anim.setCurrentTime(anim.duration())
    motion.settle_pending_detach()
    QApplication.processEvents()


def _gone(p):
    sk = p._skeleton
    return sk is None


def test_no_skeleton_on_a_panel_that_is_not_loading(panel):
    panel.show_skeleton()
    assert _gone(panel)


def test_a_loading_panel_gets_a_skeleton_over_its_body(panel):
    panel.set_loading(True)
    panel.show_skeleton()
    sk = panel._skeleton
    assert sk is not None and sk.isVisible()
    assert sk.geometry() == sk.parentWidget().rect()


@pytest.mark.parametrize(
    "ready",
    [
        lambda p: p.set_loading(False),
        lambda p: p.set_error("boom"),
        lambda p: p.set_empty("Nothing here"),
        lambda p: p.clear_state(),
    ],
    ids=["data", "error", "empty", "clear"],
)
def test_every_way_out_retires_the_skeleton(panel, ready):
    panel.set_loading(True)
    panel.show_skeleton()
    ready(panel)
    assert _gone(panel)
    _settle()
    assert not any(
        isinstance(w, panel_mod._Skeleton) and w.isVisible()
        for w in panel.findChildren(panel_mod._Skeleton)
    )


def test_the_cap_retires_a_skeleton_whose_data_never_comes(panel):
    panel.set_loading(True)
    panel.show_skeleton()
    panel._skeleton._cap.timeout.emit()  # SKELETON_MAX_MS elapsed
    assert _gone(panel)


def test_the_pulse_runs_only_while_a_skeleton_is_up(panel, animate):
    pulse = panel_mod._skeleton_pulse()
    panel.set_loading(True)
    panel.show_skeleton()
    assert pulse.running() is animate  # static under reduced motion
    panel.set_loading(False)
    assert not pulse.running()


def test_reduced_motion_retires_at_once(panel, animate):
    if animate:
        pytest.skip("the instant path only")
    panel.set_loading(True)
    panel.show_skeleton()
    sk = panel._skeleton
    panel.set_loading(False)
    assert not sk.isVisible()


def test_closing_the_panel_with_a_live_skeleton_is_safe(qapp, animate):
    class _P(panel_mod.Panel):
        def build(self):
            pass

    p = _P()
    p.build()
    p.show()
    p.set_loading(True)
    p.show_skeleton()
    p.close()
    p.deleteLater()
    _flush_deletes()
    assert not panel_mod._skeleton_pulse().running()


# -- the restore path --------------------------------------------------------

@pytest.fixture
def win(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    w = MainWindow()
    yield w
    w.close()
    w.deleteLater()
    QApplication.processEvents()


def test_restoring_a_layout_never_fades_panels_in(win, monkeypatch):
    """motion.fade_in_panel says so; apply_layout used to reset its guard
    before the panels were added, so every restored panel faded."""
    win.add_panel("chart")
    doc = win.serialize_layout()
    calls = []
    monkeypatch.setattr(motion, "fade_in_panel", lambda p: calls.append(p))
    assert win.apply_layout(doc)
    assert calls == []


def test_a_panel_the_user_adds_still_fades_in(win, monkeypatch):
    calls = []
    monkeypatch.setattr(motion, "fade_in_panel", lambda p: calls.append(p))
    win.add_panel("chart")
    assert len(calls) == 1


def test_restoring_a_layout_skeletons_the_panels_still_loading(win, monkeypatch):
    shown = []
    monkeypatch.setattr(
        panel_mod.Panel, "show_skeleton", lambda self: shown.append(self.panel_id)
    )
    win.add_panel("chart")
    doc = win.serialize_layout()
    assert win.apply_layout(doc)
    assert "chart" in shown
