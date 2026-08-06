"""``default_startup`` branching: restore an existing session without ever
showing the chooser; on a fresh install, show the chooser and route its
result to ``_load_workspace``; dismissing it leaves the window untouched;
and, once presented, never offer it again.

Called unbound against a duck-typed host rather than a real MainWindow, same
pattern as test_workspaces_menu.py: constructing MainWindow needs the whole
app (dock manager, panel registry, providers), and none of that machinery is
exercised by this branch of logic. ``pick_workspace`` is monkeypatched so no
GUI is involved.
"""

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


class _FakeSettings:
    """Dict-backed stand-in for QSettings so tests neither read nor write the
    developer's real registry, and each test starts from a fresh install."""

    def __init__(self, store):
        self._store = store

    def value(self, key, default=None, type=None):  # noqa: A002 (Qt signature)
        val = self._store.get(key, default)
        return bool(val) if type is bool else val

    def setValue(self, key, val):  # noqa: N802 (Qt naming)
        self._store[key] = val


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    from aurantium import app as app_mod

    store: dict = {}
    monkeypatch.setattr(app_mod, "QSettings", lambda: _FakeSettings(store))
    return store


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


def test_chooser_is_offered_only_once(monkeypatch, settings):
    """A user who starts empty and quits without adding a panel auto-saves
    ``"panels": []`` — indistinguishable from a fresh install. Without the
    flag the chooser would reappear at every launch, a modal nag with no way
    to dismiss it for good."""
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    shown = []
    monkeypatch.setattr(
        wc_mod, "pick_workspace", lambda presets, parent=None: shown.append(1)
    )

    host = _Host(None)
    app_mod.MainWindow.default_startup(host)
    assert shown == [1]
    assert settings[app_mod.WORKSPACE_CHOOSER_SHOWN_KEY] is True

    # second launch: still nothing saved, because the user never opened a panel
    host = _Host({"version": 1, "panels": [], "ads_state": "", "symbols": {}})
    app_mod.MainWindow.default_startup(host)
    assert shown == [1]
    assert host.applied == []

    # third launch, this time with a stale empty dict — same outcome
    host = _Host(None)
    app_mod.MainWindow.default_startup(host)
    assert shown == [1]


def test_before_prompt_does_not_fire_when_the_chooser_is_suppressed(
    monkeypatch, settings
):
    """The splash must not be torn down for a chooser that will not appear."""
    from aurantium import app as app_mod
    import aurantium.workspace_chooser as wc_mod

    monkeypatch.setattr(wc_mod, "pick_workspace", lambda presets, parent=None: None)
    settings[app_mod.WORKSPACE_CHOOSER_SHOWN_KEY] = True

    calls = []
    host = _Host(None)
    app_mod.MainWindow.default_startup(host, before_prompt=lambda: calls.append(1))

    assert calls == []


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
