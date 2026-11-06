"""A panel can open another panel in its own link group.

``panel.open_panel_hook(panel_id, link_group, settings)`` is set by the main
window. Macro's positioning bars use it: click a market and the Speculator
Positioning panel in the same link group re-centres on it, or — if none is
open — one opens floating, the way panels open from the Panels menu.

The main window is too heavy (and too stateful: it reads the installed app's
layouts) to build here, so ``open_linked_panel`` is tested against a fake that
has only what it touches: ``_docks``, ``_unbuilt``, ``add_panel`` and
``focus_dock``.
"""

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
