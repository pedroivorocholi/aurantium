"""Panels opened from the menu arrive as their own window, so opening one
never reshuffles a tuned layout. Dragged into the layout, a panel snaps in and
is docked for good: docked panels move and snap but never tear off again.
"""

import pytest


@pytest.fixture
def win(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    w = MainWindow()
    yield w


def _floating(dock):
    return bool(dock.isFloating() or dock.isInFloatingContainer())


def _floatable(dock):
    import PySide6QtAds as QtAds

    return bool(dock.features() & QtAds.CDockWidget.DockWidgetFloatable)


def _menu_action(win, title):
    for sub in win._m_panels.actions():
        menu = sub.menu()
        if menu is None:
            continue
        for act in menu.actions():
            if act.text() == title:
                return act
    raise KeyError(title)


def test_menu_opens_a_floating_window(win):
    before = set(win._docks)
    _menu_action(win, "Key Metrics").trigger()
    (new_id,) = set(win._docks) - before
    dock = win._docks[new_id]
    assert _floating(dock)


def test_slash_add_opens_floating(win):
    before = set(win._docks)
    win._run_slash_command("/add key_metrics")
    (new_id,) = set(win._docks) - before
    assert _floating(win._docks[new_id])


def test_docking_it_ends_floating_for_good(win, qapp):
    import PySide6QtAds as QtAds

    dock = win.add_panel("key_metrics", floating=True)
    assert _floatable(dock)
    # what a drop onto the layout does: move it into a dock area
    win.dock_manager.addDockWidget(QtAds.DockWidgetArea.RightDockWidgetArea, dock)
    qapp.processEvents()
    assert not _floating(dock)
    assert not _floatable(dock)


def test_programmatic_panels_still_dock(win):
    """Layouts and presets place panels exactly — no surprise windows."""
    dock = win.add_panel("chart")
    assert not _floating(dock)
    assert not _floatable(dock)


def test_a_floating_panel_survives_save_and_restore(win):
    win.add_panel("chart")
    win.add_panel("key_metrics", floating=True)
    doc = win.serialize_layout()
    spec = next(p for p in doc["panels"] if p["panel_id"] == "key_metrics")
    assert spec["floating"] is True
    assert win.apply_layout(doc)
    dock = next(d for d in win._docks.values() if d.widget().panel_id == "key_metrics")
    assert _floating(dock)
