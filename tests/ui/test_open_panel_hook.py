"""A panel can open another panel in its own link group.

``panel.open_panel_hook(panel_id, link_group, settings)`` is set by the main
window. Macro's positioning bars use it: click a market and the Speculator
Positioning panel in the same link group re-centres on it, or — if none is
open — one opens floating, the way panels open from the Panels menu.

``open_linked_panel``'s choices are tested against a fake window holding only
what it touches (``_docks``, ``_unbuilt``, ``add_panel``, ``focus_dock``); the
last tests drive the real main window, on isolated settings, through the hook.
"""

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from aurantium import panel as panel_module
from aurantium.app import open_linked_panel


class FakePanel:
    def __init__(self, panel_id: str, link_group: str) -> None:
        self.panel_id = panel_id
        self.link_group = link_group
        self.restored: list[dict] = []

    def restore(self, settings: dict) -> None:
        self.restored.append(dict(settings))


class FakeDock:
    def __init__(self, widget) -> None:
        self._widget = widget

    def widget(self):
        return self._widget


class FakeWindow:
    def __init__(self) -> None:
        self._docks: dict = {}
        self._unbuilt: dict = {}
        self.added: list[tuple] = []
        self.focused: list = []

    def add_panel(self, panel_id, link_group=None, settings=None, floating=False, **_kw):
        self.added.append((panel_id, link_group, settings, floating))
        dock = FakeDock(FakePanel(panel_id, link_group))
        self._docks[f"{panel_id}#{len(self.added)}"] = dock
        return dock

    def focus_dock(self, dock) -> None:
        self.focused.append(dock)


def test_hook_is_declared_on_the_panel_module():
    assert hasattr(panel_module, "open_panel_hook")


def test_hook_opens_floating_when_absent():
    win = FakeWindow()
    # a positioning panel in ANOTHER group does not count
    other = FakeDock(FakePanel("cot_history", "B"))
    win._docks["cot_history#1"] = other
    open_linked_panel(win, "cot_history", "A", {"market": "gold"})
    assert win.added == [("cot_history", "A", {"market": "gold"}, True)]
    assert other.widget().restored == []
    assert win.focused == []


def test_hook_focuses_existing_in_group():
    win = FakeWindow()
    win._docks["chart#1"] = FakeDock(FakePanel("chart", "A"))
    win._docks["cot_history#1"] = FakeDock(FakePanel("cot_history", "B"))
    target = FakeDock(FakePanel("cot_history", "A"))
    win._docks["cot_history#2"] = target
    open_linked_panel(win, "cot_history", "A", {"market": "crude_oil"})
    assert win.added == []
    assert win.focused == [target]
    assert target.widget().restored == [{"market": "crude_oil"}]
    assert win._docks["cot_history#1"].widget().restored == []


def test_hook_points_a_not_yet_built_panel_at_the_market():
    """A restored layout builds panels lazily: a placeholder dock in the group
    takes the settings for when it is built, and is focused."""

    class Meta:
        id = "cot_history"

    win = FakeWindow()
    placeholder = FakeDock(object())
    win._docks["cot_history#3"] = placeholder
    win._unbuilt["cot_history#3"] = (Meta, "A", {"market": "gold"})
    open_linked_panel(win, "cot_history", "A", {"market": "copper"})
    assert win.added == []
    assert win.focused == [placeholder]
    assert win._unbuilt["cot_history#3"][2] == {"market": "copper"}


def test_hook_applies_a_retired_ids_alias_settings(monkeypatch):
    from aurantium.panel import PanelRegistry

    monkeypatch.setitem(PanelRegistry._aliases, "old_cot", ("cot_history", {"range": "1Y"}))
    win = FakeWindow()
    open_linked_panel(win, "old_cot", "A", {"market": "gold"})
    assert win.added == [("cot_history", "A", {"range": "1Y", "market": "gold"}, True)]


# -- the real main window -------------------------------------------------------------


@pytest.fixture
def win(svc):
    """A real MainWindow on isolated settings and a fresh hub."""
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()
    w.deleteLater()
    QApplication.processEvents()


def _markets():
    from aurantium.panels.cot_history import _COVERED

    return _COVERED[1].cftc_market, _COVERED[2].cftc_market


def _cot_docks(win, group):
    return [d for d in win._docks.values()
            if getattr(d.widget(), "panel_id", "") == "cot_history"
            and d.widget().link_group == group]


def test_real_window_opens_floating_then_focuses_and_applies(win):
    first, second = _markets()
    assert panel_module.open_panel_hook is not None
    panel_module.open_panel_hook("cot_history", "C", {"market": first})
    (dock,) = _cot_docks(win, "C")
    assert dock.isFloating() or dock.isInFloatingContainer()
    assert dock.widget().settings()["market"] == first

    before = set(win._docks)
    panel_module.open_panel_hook("cot_history", "C", {"market": second})
    assert set(win._docks) == before          # nothing new opened
    assert _cot_docks(win, "C") == [dock]
    assert dock.widget().settings()["market"] == second


def _maximize(win, group):
    import PySide6QtAds as QtAds

    docks = []
    for g in (group, "D"):
        d = win.add_panel("cot_history", link_group=g)
        win.dock_manager.addDockWidget(QtAds.DockWidgetArea.RightDockWidgetArea, d)
        docks.append(d)
    QApplication.processEvents()
    win._set_maximized(docks[1].objectName(), True)
    return docks


def test_real_window_leaves_a_maximize_hiding_the_target(win):
    first, _ = _markets()
    target, other = _maximize(win, "C")
    assert win._maximized_instance == other.objectName()
    assert target.isClosed()
    panel_module.open_panel_hook("cot_history", "C", {"market": first})
    assert win._maximized_instance is None
    assert not target.isClosed()


def test_real_window_keeps_a_maximize_in_another_window(win):
    first, _ = _markets()
    target = win.add_panel("cot_history", link_group="C", floating=True)
    _, other = _maximize(win, "E")
    assert win._maximized_instance == other.objectName()
    panel_module.open_panel_hook("cot_history", "C", {"market": first})
    assert win._maximized_instance == other.objectName()
    assert target.widget().settings()["market"] == first


def test_hook_is_cleared_when_the_window_is_destroyed(svc):
    from aurantium.app import MainWindow

    w = MainWindow()
    assert panel_module.open_panel_hook is not None
    w.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()
    assert panel_module.open_panel_hook is None
