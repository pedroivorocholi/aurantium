"""Panels opened from the menu arrive as their own window, so opening one
never reshuffles a tuned layout. Dragged into the layout, a panel snaps in.
Dragged out past every aurantium window, it becomes a window of its own (a
second monitor's docking area); a drag released inside a window but off any
drop target goes back where it came from.
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
    _menu_action(win, "Company").trigger()
    (new_id,) = set(win._docks) - before
    dock = win._docks[new_id]
    assert _floating(dock)


def test_slash_add_opens_floating(win):
    before = set(win._docks)
    win._run_slash_command("/add key_metrics")
    (new_id,) = set(win._docks) - before
    assert _floating(win._docks[new_id])


def test_a_docked_panel_can_still_become_a_window(win, qapp):
    """Multi-screen: a docked panel can be dragged out onto another monitor."""
    import PySide6QtAds as QtAds

    dock = win.add_panel("company", floating=True)
    win.dock_manager.addDockWidget(QtAds.DockWidgetArea.RightDockWidgetArea, dock)
    qapp.processEvents()
    assert not _floating(dock)
    assert _floatable(dock)


def test_programmatic_panels_still_dock(win):
    """Layouts and presets place panels exactly — no surprise windows."""
    dock = win.add_panel("chart")
    assert not _floating(dock)


class _Cursor:
    def __init__(self, point):
        self._p = point

    def pos(self):
        return self._p


def _tear_off(win, qapp, monkeypatch, dock, point):
    """What a drag released at ``point`` does: QtAds floats the dock, then the
    window decides whether that was a new window or a missed drop."""
    import aurantium.app as app_mod

    monkeypatch.setattr(app_mod, "QCursor", _Cursor(point))
    win.dock_manager.addDockWidgetFloating(dock)
    for _ in range(5):
        qapp.processEvents()


def test_a_drag_out_of_every_window_makes_a_new_window(win, qapp, monkeypatch):
    from PySide6.QtCore import QPoint

    win.show()
    win.add_panel("chart")
    dock = win.add_panel("company")
    qapp.processEvents()
    far = win.frameGeometry().bottomRight() + QPoint(4000, 4000)
    _tear_off(win, qapp, monkeypatch, dock, far)
    assert _floating(dock)


def test_a_drag_released_inside_the_window_goes_back_home(win, qapp, monkeypatch):
    win.show()
    chart = win.add_panel("chart")
    dock = win.add_panel("company")
    qapp.processEvents()
    home = dock.dockAreaWidget()
    _tear_off(win, qapp, monkeypatch, dock, win.frameGeometry().center())
    assert not _floating(dock), "a missed drop must not leave a stray window"
    assert dock.dockAreaWidget() is home or dock.dockAreaWidget() is chart.dockAreaWidget()


def test_panel_windows_get_the_app_shortcuts(win):
    dock = win.add_panel("company", floating=True)
    container = dock.dockContainer().floatingWidget()
    keys = {a.shortcut().toString() for a in container.actions()}
    assert "F5" in keys and "F11" in keys


def test_a_new_panel_window_keeps_the_title_bar(win, qapp, monkeypatch):
    """Regression: collecting shortcuts called QMainWindow.menuBar(), which
    builds an empty QMenuBar and installs it in place of the title bar —
    deleting it. The bar vanished on the first drag to another monitor and
    the next window-state change crashed on its dead buttons."""
    import shiboken6
    from PySide6.QtCore import QPoint

    win.show()
    win.add_panel("chart")
    first = win.add_panel("company")
    second = win.add_panel("watchlist")
    qapp.processEvents()
    far = win.frameGeometry().bottomRight() + QPoint(4000, 4000)
    _tear_off(win, qapp, monkeypatch, first, far)
    _tear_off(win, qapp, monkeypatch, second, far)
    qapp.processEvents()
    assert shiboken6.isValid(win._title_bar)
    assert win.menuWidget() is win._title_bar
    assert win._title_bar.isVisible()
    win._title_bar.sync_window_state()  # its buttons are still alive


def test_panel_window_shortcuts_include_the_menus(win):
    dock = win.add_panel("company", floating=True)
    container = dock.dockContainer().floatingWidget()
    menu_keys = {
        a.shortcut().toString()
        for m in (win._m_panels, win._m_settings)
        for a in m.actions()
        if a.menu() is None and a.shortcuts()
    }
    keys = {a.shortcut().toString() for a in container.actions()}
    assert menu_keys <= keys


def test_f11_on_a_lone_panel_maximizes_its_window(win, qapp):
    """A panel alone in its window has no siblings to hide, so F11 used to do
    nothing there; it maximizes that window on its monitor instead."""
    win.show()
    win.add_panel("chart")
    dock = win.add_panel("company", floating=True)
    fw = dock.dockContainer().floatingWidget()
    fw.show()
    qapp.processEvents()
    win._set_maximized(dock.objectName(), True)
    qapp.processEvents()
    assert fw.isMaximized()
    assert win._maximized_instance is None, "no panel-maximize state to undo"
    win._set_maximized(dock.objectName(), True)  # F11 again restores
    qapp.processEvents()
    assert not fw.isMaximized()


def test_maximize_stays_inside_its_own_window(win, qapp):
    main = win.add_panel("chart")
    win.add_panel("watchlist")
    other = win.add_panel("company", floating=True)
    qapp.processEvents()
    win._set_maximized(main.objectName(), True)
    assert not other.isClosed(), "a panel on another monitor stays put"
    win._exit_maximize()


def test_an_off_screen_window_is_pulled_back(win, qapp):
    from PySide6.QtWidgets import QApplication

    dock = win.add_panel("company", floating=True)
    container = dock.dockContainer().floatingWidget()
    container.move(-60000, -60000)
    win._keep_on_screen(container)
    assert QApplication.screenAt(container.frameGeometry().center()) is not None


def test_link_groups_reach_panels_in_other_windows(win, qapp):
    from aurantium.symbol_context import DEFAULT_GROUP, SymbolContext

    dock = win.add_panel("company", floating=True)
    panel = dock.widget()
    SymbolContext.instance().set_symbol(panel.link_group or DEFAULT_GROUP, "MSFT")
    qapp.processEvents()
    assert panel.current_symbol == "MSFT"


def test_a_floating_panel_survives_save_and_restore(win):
    win.add_panel("chart")
    win.add_panel("company", floating=True)
    doc = win.serialize_layout()
    spec = next(p for p in doc["panels"] if p["panel_id"] == "company")
    assert spec["floating"] is True
    assert win.apply_layout(doc)
    dock = next(d for d in win._docks.values() if d.widget().panel_id == "company")
    assert _floating(dock)
