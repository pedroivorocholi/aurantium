"""The Workspaces submenu lists every valid preset and loads one on click.

Both methods are called unbound against a duck-typed host rather than a real
MainWindow: constructing MainWindow needs the whole app (dock manager, panel
registry, providers). ``_rebuild_workspaces_menu`` parents its QActions to
``self``, so the host must be a real QWidget — an uninitialised
``MainWindow.__new__`` would have no underlying C++ object and crash.
"""

import pytest
from PySide6.QtWidgets import QMenu, QWidget

pytestmark = pytest.mark.usefixtures("qapp")


class _Host(QWidget):
    """Minimal stand-in exposing only what the two methods touch."""

    def __init__(self):
        super().__init__()
        self._m_workspaces = QMenu(self)
        self.loaded = []
        self.applied = []
        self.messages = []

    def _load_workspace(self, preset):
        self.loaded.append(preset)

    def apply_layout(self, doc):
        self.applied.append(doc)
        return True

    def statusBar(self):  # noqa: N802 (Qt naming)
        host = self

        class _Bar:
            def showMessage(self, text, timeout=0):  # noqa: N802
                host.messages.append(text)

        return _Bar()


def _presets():
    from aurantium.presets import Preset

    return [
        Preset(name="Alpha Desk", description="First.", doc={"panels": []}),
        Preset(name="Beta Desk", description="Second.", doc={"panels": []}),
    ]


def test_rebuild_lists_every_preset(monkeypatch):
    from aurantium import app as app_mod

    presets = _presets()
    monkeypatch.setattr(app_mod, "available_presets", lambda: presets)

    host = _Host()
    app_mod.MainWindow._rebuild_workspaces_menu(host)

    actions = host._m_workspaces.actions()
    assert [a.text() for a in actions] == ["Alpha Desk", "Beta Desk"]
    assert actions[0].toolTip() == "First."
    # Setting the tooltip is not enough: QMenu suppresses action tooltips
    # unless it opts in, so this is the property the user actually sees.
    assert host._m_workspaces.toolTipsVisible()


def test_clicking_an_entry_loads_that_preset(monkeypatch):
    from aurantium import app as app_mod

    presets = _presets()
    monkeypatch.setattr(app_mod, "available_presets", lambda: presets)

    host = _Host()
    app_mod.MainWindow._rebuild_workspaces_menu(host)
    host._m_workspaces.actions()[1].trigger()

    assert host.loaded == [presets[1]]


def test_rebuild_is_idempotent(monkeypatch):
    """Called once today, but it clears before it fills — so if it ever gains
    a second call site (an entitlement gate re-filtering the list) it must not
    accumulate duplicates."""
    from aurantium import app as app_mod

    monkeypatch.setattr(app_mod, "available_presets", _presets)

    host = _Host()
    app_mod.MainWindow._rebuild_workspaces_menu(host)
    app_mod.MainWindow._rebuild_workspaces_menu(host)

    assert len(host._m_workspaces.actions()) == 2


def test_rebuild_shows_disabled_placeholder_when_none(monkeypatch):
    from aurantium import app as app_mod

    monkeypatch.setattr(app_mod, "available_presets", lambda: [])

    host = _Host()
    app_mod.MainWindow._rebuild_workspaces_menu(host)

    actions = host._m_workspaces.actions()
    assert len(actions) == 1
    assert not actions[0].isEnabled()


def test_load_workspace_applies_the_layout():
    from aurantium import app as app_mod
    from aurantium.presets import Preset

    host = _Host()
    preset = Preset(
        name="Alpha Desk", description="", doc={"panels": [{"panel_id": "chart"}]}
    )
    app_mod.MainWindow._load_workspace(host, preset)

    assert host.applied == [{"panels": [{"panel_id": "chart"}]}]
    assert "Alpha Desk" in host.messages[0]
