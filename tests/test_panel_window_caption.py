"""Panel windows (a panel dragged onto a second monitor) wear the main
window's title bar — logo, Panels, Settings and the caption buttons — minus
the symbol search, instead of the grey native Windows caption."""

import sys

import pytest
from PySide6.QtCore import Qt

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows frame only")


@pytest.fixture
def win(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    yield MainWindow()


def _bar(fw):
    from aurantium.title_bar import TitleBar

    return fw.findChild(TitleBar)


def test_a_panel_window_gets_the_title_bar_without_search(win):
    dock = win.add_panel("company", floating=True)
    fw = dock.dockContainer().floatingWidget()
    bar = _bar(fw)
    assert bar is not None
    assert fw.layout().itemAt(0).widget() is bar, "the bar sits on top"
    assert fw.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert bar.panels_button.menu() is win._m_panels
    assert bar.settings_button.menu() is win._m_settings
    assert bar.close_button is not None and bar.max_button is not None
    assert bar.findChild(type(win._cmd)) is None, "no symbol search"


def test_the_panel_window_bar_is_its_drag_caption(win, qapp):
    from aurantium import title_bar

    dock = win.add_panel("company", floating=True)
    fw = dock.dockContainer().floatingWidget()
    fw.resize(700, 500)
    fw.show()
    qapp.processEvents()
    frame = title_bar._panel_frames.frames[int(fw.winId())]
    bar = _bar(fw)
    empty = bar.mapTo(fw, bar.rect().center())  # the stretch in the middle
    assert frame.hit_at(empty) == title_bar.HTCAPTION
    max_btn = bar.max_button
    assert frame.hit_at(max_btn.mapTo(fw, max_btn.rect().center())) == title_bar.HTMAXBUTTON
    assert frame.hit_at(fw.rect().center()) == title_bar.HTCLIENT


def test_closing_a_panel_window_unregisters_its_frame(win, qapp):
    from aurantium import title_bar

    dock = win.add_panel("company", floating=True)
    fw = dock.dockContainer().floatingWidget()
    fw.show()
    qapp.processEvents()
    hwnd = int(fw.winId())
    assert hwnd in title_bar._panel_frames.frames
    fw.deleteLater()
    qapp.processEvents()
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert hwnd not in title_bar._panel_frames.frames
