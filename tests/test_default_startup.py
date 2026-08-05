"""``default_startup`` branching: restore an existing session without ever
showing the chooser; on a fresh install, show the chooser and route its
result to ``_load_workspace``; dismissing it leaves the window untouched.

Called unbound against a duck-typed host rather than a real MainWindow, same
pattern as test_workspaces_menu.py: constructing MainWindow needs the whole
app (dock manager, panel registry, providers), and none of that machinery is
exercised by this branch of logic. ``pick_workspace`` is monkeypatched so no
GUI is involved.
"""

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


class _Host:
    """Minimal stand-in exposing only what default_startup touches."""

    def __init__(self, last):
        self.layout_store = _Store(last)
        self.applied = []
        self.loaded = []

    def apply_layout(self, doc):
        self.applied.append(doc)
        return True

    def _load_workspace(self, preset):
        self.loaded.append(preset)


class _Store:
    def __init__(self, last):
        self._last = last

    def get_last(self):
        return self._last


def _preset():
    from aurantium.presets import Preset

    return Preset(name="Alpha Desk", description="First.", doc={"panels": [{"panel_id": "chart"}]})


def test_restores_saved_session_without_showing_chooser(monkeypatch):
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    called = []
    monkeypatch.setattr(
        wc_mod, "pick_workspace", lambda *a, **k: called.append(1) or None
    )

    saved = {"panels": [{"panel_id": "watchlist"}]}
    host = _Host(saved)
    app_mod.MainWindow.default_startup(host)

    assert host.applied == [saved]
    assert host.loaded == []
    assert called == []


def test_no_saved_session_shows_chooser_and_loads_chosen_preset(monkeypatch):
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    preset = _preset()
    monkeypatch.setattr(wc_mod, "pick_workspace", lambda presets, parent=None: preset)

    host = _Host(None)
    app_mod.MainWindow.default_startup(host)

    assert host.loaded == [preset]
    assert host.applied == []


def test_dismissed_chooser_leaves_window_empty(monkeypatch):
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    monkeypatch.setattr(wc_mod, "pick_workspace", lambda presets, parent=None: None)

    host = _Host(None)
    app_mod.MainWindow.default_startup(host)

    assert host.loaded == []
    assert host.applied == []


def test_before_prompt_fires_only_on_no_session_branch(monkeypatch):
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    monkeypatch.setattr(wc_mod, "pick_workspace", lambda presets, parent=None: None)

    calls = []
    host = _Host(None)
    app_mod.MainWindow.default_startup(host, before_prompt=lambda: calls.append(1))
    assert calls == [1]

    calls.clear()
    host = _Host({"panels": [{"panel_id": "watchlist"}]})
    app_mod.MainWindow.default_startup(host, before_prompt=lambda: calls.append(1))
    assert calls == []
