"""Selections travel instead of cutting (transitions.py).

The underline under the company tabs, the dock tabs' active box, a chip's
checked fill and a menu arriving all used to change in a single frame. These
hold the moves to the house rules: the indicator ends exactly where the static
one used to be drawn, a new click retargets from where it is (never queues,
never snaps back), and reduced motion lands on the final state at once.
"""

import pytest
from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QButtonGroup, QMenu, QPushButton, QWidget

from aurantium import motion, theme, transitions


@pytest.fixture
def filt(qapp):
    flt = transitions.install(qapp)
    yield flt
    qapp.removeEventFilter(flt)


@pytest.fixture
def animated(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)


@pytest.fixture
def reduced(monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)


def _settle(ind):
    for _ in range(100):
        if not ind.animating():
            return
        QTest.qWait(10)
    raise AssertionError("indicator never settled")


def _partway(ind):
    """Stand the running slide a third of the way along: the animation clock,
    not a wall-clock wait, so a loaded machine cannot leave it unstarted."""
    assert ind.animating()
    ind._anim.setCurrentTime(ind._anim.duration() // 3)


def _underline(bar, i):
    r = bar.tabRect(i)
    return QRect(r.left(), r.bottom() - 1, r.width(), 2)


def _company(qapp):
    from aurantium.panels.company import CompanyPanel

    panel = CompanyPanel()
    panel.build()
    panel.resize(640, 400)
    panel.show()
    qapp.processEvents()
    return panel


# -- company tabs: the underline ------------------------------------------------


def test_company_tabs_get_a_sliding_underline(qapp, filt, animated):
    panel = _company(qapp)
    bar = panel.tabs.tabBar()
    ind = transitions.indicator_of(bar)
    assert ind is not None and ind.isVisible()
    assert ind.geometry() == _underline(bar, 0)
    assert bar.property(transitions.PROPERTY) is True


def test_the_underline_slides_in_x_and_width(qapp, filt, animated):
    panel = _company(qapp)
    bar = panel.tabs.tabBar()
    ind = transitions.indicator_of(bar)
    start = ind.geometry()
    panel.tabs.setCurrentIndex(2)
    assert ind.animating()
    _partway(ind)
    mid = ind.geometry()
    assert start.left() < mid.left() < _underline(bar, 2).left()
    _settle(ind)
    assert ind.geometry() == _underline(bar, 2)
    assert ind.geometry().width() != start.width() or bar.tabRect(2).width() == bar.tabRect(0).width()


def test_a_second_click_retargets_from_where_it_is(qapp, filt, animated):
    panel = _company(qapp)
    bar = panel.tabs.tabBar()
    ind = transitions.indicator_of(bar)
    panel.tabs.setCurrentIndex(3)
    _partway(ind)
    here = ind.geometry()
    panel.tabs.setCurrentIndex(1)
    # the new slide starts at the indicator's current position, not at tab 0
    # or tab 3
    assert ind._anim.startValue() == here
    assert ind._anim.endValue() == _underline(bar, 1)
    _settle(ind)
    assert ind.geometry() == _underline(bar, 1)


def test_reduced_motion_lands_the_underline_at_once(qapp, filt, reduced):
    panel = _company(qapp)
    bar = panel.tabs.tabBar()
    ind = transitions.indicator_of(bar)
    panel.tabs.setCurrentIndex(2)
    assert not ind.animating()
    assert ind.geometry() == _underline(bar, 2)


def test_the_static_underline_goes_clear_only_where_an_indicator_draws_it():
    sheet = theme.STYLESHEET
    assert 'QTabBar[slidingIndicator="true"]::tab:selected {{ border-bottom-color: transparent; }}'.replace(
        "{{", "{"
    ).replace("}}", "}") in sheet
    # the 2px border itself stays, so no tab changes height
    assert f"QTabBar::tab:selected {{ color: {theme.CHROME_TEXT}; border-bottom: 2px solid {theme.ACCENT}; }}" in sheet


def test_the_underline_follows_a_resize_without_sliding(qapp, filt, animated):
    panel = _company(qapp)
    bar = panel.tabs.tabBar()
    ind = transitions.indicator_of(bar)
    panel.tabs.setCurrentIndex(1)
    _settle(ind)
    panel.resize(300, 400)  # fit_tabs elides: the tab gets narrower
    qapp.processEvents()
    QTest.qWait(20)
    assert not ind.animating()
    assert ind.geometry() == _underline(bar, 1)


def test_the_incoming_page_fades_up(qapp, filt, animated):
    panel = _company(qapp)
    panel.tabs.setCurrentIndex(1)
    page = panel.tabs.widget(1)
    assert motion.fade_opacity(page) < 1.0
    for _ in range(60):
        QTest.qWait(10)
        if motion.fade_opacity(page) >= 0.999:
            break
    assert motion.fade_opacity(page) == pytest.approx(1.0, abs=1e-3)


# -- dock tabs: the active box ---------------------------------------------------


def _dock_area(qapp):
    import PySide6QtAds as QtAds
    from PySide6.QtWidgets import QLabel, QMainWindow

    win = QMainWindow()
    mgr = QtAds.CDockManager(win)
    mgr.setStyleSheet(theme.ADS_STYLESHEET)
    docks = []
    area = None
    for title in ("News", "Market Movers", "Watchlist"):
        d = QtAds.CDockWidget(mgr, title)
        d.setWidget(QLabel(title))
        if area is None:
            area = mgr.addDockWidget(QtAds.LeftDockWidgetArea, d)
        else:
            mgr.addDockWidgetTabToArea(d, area)
        docks.append(d)
    win.resize(700, 400)
    win.show()
    qapp.processEvents()
    QTest.qWait(20)
    return win, area.titleBar().tabBar()


def test_a_dock_area_gets_a_sliding_active_box(qapp, filt, animated):
    win, bar = _dock_area(qapp)
    ind = transitions.indicator_of(bar)
    assert ind is not None and ind.isVisible()
    cur = bar.tab(bar.currentIndex())
    assert ind.geometry() == cur.geometry()
    assert ind.parentWidget() is cur.parentWidget()  # scrolls and drags with the tabs
    # beneath the tabs, so their labels paint over it
    siblings = ind.parentWidget().children()
    assert siblings.index(ind) < siblings.index(cur)


def test_the_dock_box_stretches_to_a_wider_tab(qapp, filt, animated):
    win, bar = _dock_area(qapp)
    ind = transitions.indicator_of(bar)
    bar.setCurrentIndex(0)
    _settle(ind)
    narrow = ind.geometry()
    bar.setCurrentIndex(1)  # "Market Movers": wider than "News"
    assert ind.animating()
    _partway(ind)
    mid = ind.geometry()
    assert narrow.left() < mid.left() and mid.width() > narrow.width()
    _settle(ind)
    QTest.qWait(20)
    assert ind.geometry() == bar.tab(1).geometry()


def test_the_dock_box_retargets_mid_slide(qapp, filt, animated):
    win, bar = _dock_area(qapp)
    ind = transitions.indicator_of(bar)
    bar.setCurrentIndex(0)
    _settle(ind)
    bar.setCurrentIndex(2)
    _partway(ind)
    here = ind.geometry()
    bar.setCurrentIndex(1)
    assert ind._anim.startValue() == here
    _settle(ind)
    QTest.qWait(20)
    assert ind.geometry() == bar.tab(1).geometry()


def test_reduced_motion_lands_the_dock_box_at_once(qapp, filt, reduced):
    win, bar = _dock_area(qapp)
    ind = transitions.indicator_of(bar)
    bar.setCurrentIndex(1)
    assert not ind.animating()
    qapp.processEvents()
    assert ind.geometry() == bar.tab(1).geometry()


def test_the_dock_tab_goes_clear_only_over_an_indicator():
    sheet = theme.ADS_STYLESHEET
    start = sheet.index('ads--CDockAreaTabBar[slidingIndicator="true"] ads--CDockWidgetTab[activeTab="true"] {')
    rule = sheet[start : sheet.index("}", start)]
    assert "background: transparent" in rule
    assert "border-bottom-color: transparent" in rule


def test_the_indicators_never_take_a_click(qapp, filt, animated):
    panel = _company(qapp)
    ind = transitions.indicator_of(panel.tabs.tabBar())
    assert ind.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


# -- chips: the checked fill crossfades ---------------------------------------------


def _chips(qapp):
    host = QWidget()
    group = QButtonGroup(host)
    chips = []
    for i, label in enumerate(("1d", "5d", "1mo")):
        b = QPushButton(label, host)
        b.setObjectName("chartChip")
        b.setCheckable(True)
        b.setGeometry(i * 50, 0, 46, 20)
        group.addButton(b)
        chips.append(b)
    chips[0].setChecked(True)
    host.resize(200, 30)
    host.show()
    qapp.processEvents()
    return host, chips


def _mouse(qapp, w, kind):
    pos = QPointF(w.rect().center())
    qapp.sendEvent(
        w,
        QMouseEvent(
            kind, pos, QPointF(w.mapToGlobal(pos)), Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def _fades(w):
    return w.findChildren(transitions._Snapshot)


def test_clicking_a_chip_crossfades_both_fills(qapp, filt, animated):
    host, chips = _chips(qapp)
    _mouse(qapp, chips[2], QEvent.Type.MouseButtonPress)
    _mouse(qapp, chips[2], QEvent.Type.MouseButtonRelease)
    qapp.processEvents()
    assert chips[2].isChecked() and not chips[0].isChecked()
    assert _fades(chips[2]) and _fades(chips[0])  # the new fill in, the old out
    assert not _fades(chips[1])  # untouched
    QTest.qWait(motion.CROSSFADE_MS + 80)
    assert not _fades(chips[2]) and not _fades(chips[0])


def test_a_programmatic_check_stays_instant(qapp, filt, animated):
    host, chips = _chips(qapp)
    chips[1].setChecked(True)
    qapp.processEvents()
    assert not any(_fades(c) for c in chips)


def test_reduced_motion_chips_change_at_once(qapp, filt, reduced):
    host, chips = _chips(qapp)
    _mouse(qapp, chips[1], QEvent.Type.MouseButtonPress)
    _mouse(qapp, chips[1], QEvent.Type.MouseButtonRelease)
    qapp.processEvents()
    assert chips[1].isChecked()
    assert not any(_fades(c) for c in chips)


# -- popups ----------------------------------------------------------------------------


def test_a_menu_fades_in(qapp, filt, animated):
    menu = QMenu()
    menu.addAction("Save layout")
    menu.popup(menu.mapToGlobal(menu.pos()))
    qapp.processEvents()
    assert menu.windowOpacity() < 1.0
    QTest.qWait(motion.POPUP_IN_MS + 80)
    assert menu.windowOpacity() == pytest.approx(1.0)
    menu.close()


def test_reduced_motion_menus_open_opaque(qapp, filt, reduced):
    menu = QMenu()
    menu.addAction("Save layout")
    menu.popup(menu.mapToGlobal(menu.pos()))
    qapp.processEvents()
    assert menu.windowOpacity() == pytest.approx(1.0)
    menu.close()



def test_the_trackers_survive_garbage_collection(qapp, filt, animated):
    # QtAds builds its tab bars in C++, so nothing on the Python side held the
    # tracker: a collection before anyone looked it up stripped its attributes
    # while Qt kept calling its event filter and the indicator's paintEvent
    import gc

    import PySide6QtAds as QtAds
    from PySide6.QtWidgets import QLabel, QMainWindow

    def build():  # never touches the tab bar, so no wrapper anchors it
        win = QMainWindow()
        mgr = QtAds.CDockManager(win)
        mgr.setStyleSheet(theme.ADS_STYLESHEET)
        area = None
        for title in ("News", "Market Movers"):
            d = QtAds.CDockWidget(mgr, title)
            d.setWidget(QLabel(title))
            if area is None:
                area = mgr.addDockWidget(QtAds.LeftDockWidgetArea, d)
            else:
                mgr.addDockWidgetTabToArea(d, area)
        win.resize(500, 300)
        win.show()
        QTest.qWait(30)
        return win

    win = build()
    gc.collect()
    QTest.qWait(10)
    bar = win.findChild(QtAds.ads.CDockAreaTabBar)
    tracker = bar.findChild(transitions._DockTabTracker, "", transitions._DIRECT)
    assert tracker is not None and hasattr(tracker, "_watched")
    ind = transitions.indicator_of(bar)
    assert ind is not None and hasattr(ind, "_paint")
    bar.setCurrentIndex(1)
    ind.repaint()


# -- teardown: the underline must never take the app down ------------------------

_TORN_DOWN_PAINT = r"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget
import shiboken6
app = QApplication([])
from aurantium import transitions
tabs = QTabWidget()
for i in range(3):
    tabs.addTab(QLabel(str(i)), f"Tab {i}")
tabs.resize(300, 120)
tabs.show()
app.processEvents()
ind = transitions.slide_tab_bar(tabs.tabBar())
# The state Qt leaves while it destroys a tab bar: the bar's wrapper is dead,
# its underline child is still alive and still gets painted.
shiboken6.invalidate(tabs.tabBar())
ind.repaint()
app.processEvents()
print("survived")
"""


def test_underline_paint_survives_its_tab_bar_being_torn_down():
    """An error escaping ``paintEvent`` with a QPainter open aborts the whole
    process (0xc0000409 in the frozen build) — the 1.12.0 crash on closing a
    window with tabs. Run in a subprocess: the failure mode is an abort."""
    import subprocess
    import sys
    from pathlib import Path

    app_dir = Path(transitions.__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", _TORN_DOWN_PAINT],
        cwd=app_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0 and "survived" in result.stdout, (
        f"exit {result.returncode}\n{result.stdout}\n{result.stderr[-2000:]}"
    )
