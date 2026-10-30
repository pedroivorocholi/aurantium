"""Qt bridge for per-panel divider drags (aurantium.free_resize)."""

import zlib

import pytest
import PySide6QtAds as QtAds
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QLabel, QMainWindow, QSplitterHandle,
                               QWidget)

from shiboken6 import delete, getCppPointer, isValid

from aurantium import free_resize
from aurantium.free_resize import (FreeResizeController, apply_tree, handle_gap,
                                   read_rects, read_tree)
from aurantium.tiling import SNAP_PX, Leaf, Rect, Split, build_tree, shape

NAMES = ("a", "b", "c", "d")


@pytest.fixture
def grid_window(qapp):
    """Columns-first 2x2: root | [a / c] [b / d], 1000x700, shown offscreen."""
    win = QMainWindow()
    win.resize(1000, 700)
    mgr = QtAds.CDockManager(win)

    def dock(name):
        d = QtAds.CDockWidget(mgr, name)
        d.setObjectName(name)
        d.setWidget(QLabel(name))
        return d

    A = QtAds.DockWidgetArea
    a = mgr.addDockWidget(A.LeftDockWidgetArea, dock("a"))
    b = mgr.addDockWidget(A.RightDockWidgetArea, dock("b"))
    mgr.addDockWidget(A.BottomDockWidgetArea, dock("c"), a)
    mgr.addDockWidget(A.BottomDockWidgetArea, dock("d"), b)
    win.show()
    qapp.processEvents()
    yield mgr
    win.close()
    win.deleteLater()
    qapp.processEvents()


def areas(mgr, names=NAMES):
    """name -> its dock area, read fresh (restoreState recreates areas)."""
    return {n: mgr.findDockWidget(n).dockAreaWidget() for n in names}


def named_rects(mgr, names=NAMES, container=None):
    rects = read_rects(container or mgr)
    return {n: rects[a] for n, a in areas(mgr, names).items()}


def close(r1: Rect, r2: Rect, tol=1):
    return all(abs(p - q) <= tol for p, q in
               zip((r1.x, r1.y, r1.w, r1.h), (r2.x, r2.y, r2.w, r2.h)))


def splitters(mgr):
    return {id(s) for s in mgr.rootSplitter().findChildren(QtAds.CDockSplitter)}


def xml(state) -> str:
    raw = bytes(state)
    for offset in range(16):
        try:
            return zlib.decompress(raw[offset:]).decode()
        except zlib.error:
            pass
    raise AssertionError("undecodable state")


def stepped(mgr):
    """The grid with only the top row's divider moved 100px right."""
    r = named_rects(mgr)
    ar = areas(mgr)
    out = {ar[n]: rect for n, rect in r.items()}
    a, b = r["a"], r["b"]
    out[ar["a"]] = Rect(a.x, a.y, a.w + 100, a.h)
    out[ar["b"]] = Rect(b.x + 100, b.y, b.w - 100, b.h)
    return out


def test_read_rects_matches_area_geometry(grid_window):
    mgr = grid_window
    root = mgr.rootSplitter()
    rects = read_rects(mgr)
    assert set(rects) == set(areas(mgr).values())
    for area, r in rects.items():
        top_left = area.mapTo(root, area.rect().topLeft())
        assert r == Rect(top_left.x(), top_left.y(), area.width(), area.height())
    g = handle_gap(mgr)
    assert g == root.handleWidth()
    r = named_rects(mgr)
    assert r["a"].x == r["c"].x == 0 and r["b"].x == r["a"].right + g
    assert r["c"].y == r["a"].bottom + g


def test_read_tree_is_columns_first(grid_window):
    mgr = grid_window
    ar = areas(mgr)
    assert shape(read_tree(mgr)) == ("h", (("v", (ar["a"], ar["c"])),
                                           ("v", (ar["b"], ar["d"]))))
    assert shape(read_tree(mgr)) == shape(build_tree(read_rects(mgr), handle_gap(mgr)))


def test_apply_same_shape_only_resizes(grid_window, qapp):
    mgr = grid_window
    before = splitters(mgr)
    tree = read_tree(mgr)
    left, right = tree.children
    moved = Split("h", (left, right), (tree.sizes[0] - 120, tree.sizes[1] + 120))
    apply_tree(mgr, moved)
    qapp.processEvents()
    assert splitters(mgr) == before
    r = named_rects(mgr)
    assert r["a"].w == r["c"].w == tree.sizes[0] - 120
    assert r["b"].w == r["d"].w == tree.sizes[1] + 120


def test_apply_renests_to_step(grid_window, qapp):
    mgr = grid_window
    old = named_rects(mgr)
    target = stepped(mgr)
    tree = build_tree(target, handle_gap(mgr))
    assert tree.orient == "v"  # the step broke the full-height cut
    apply_tree(mgr, tree)
    qapp.processEvents()
    assert shape(read_tree(mgr)) == shape(tree)
    assert mgr.rootSplitter().orientation() == Qt.Vertical
    new = named_rects(mgr)
    assert close(new["c"], old["c"]) and close(new["d"], old["d"])
    ar = areas(mgr)
    assert close(new["a"], target[ar["a"]]) and close(new["b"], target[ar["b"]])
    # no empty splitters left behind
    qapp.processEvents()
    for s in mgr.rootSplitter().findChildren(QtAds.CDockSplitter):
        assert s.findChildren(QtAds.CDockAreaWidget)


def test_renest_roundtrips_state(grid_window, qapp):
    mgr = grid_window
    apply_tree(mgr, build_tree(stepped(mgr), handle_gap(mgr)))
    qapp.processEvents()
    before = named_rects(mgr)
    state = mgr.saveState()
    assert mgr.restoreState(state)
    qapp.processEvents()
    after = named_rects(mgr)
    assert all(close(before[n], after[n]) for n in NAMES)
    text = xml(state)
    rows = text.index('<Splitter Orientation="-"')
    assert text.index('<Splitter Orientation="|"') > rows
    assert text.count('<Splitter Orientation="|"') == 2


def test_apply_leaf_tree_is_noop_shape(qapp):
    """A single visible area reads as a leaf and applies without error."""
    win = QMainWindow()
    win.resize(600, 400)
    mgr = QtAds.CDockManager(win)
    d = QtAds.CDockWidget(mgr, "solo")
    d.setWidget(QLabel("solo"))
    area = mgr.addDockWidget(QtAds.DockWidgetArea.LeftDockWidgetArea, d)
    win.show()
    qapp.processEvents()
    try:
        assert read_tree(mgr) == Leaf(area)
        apply_tree(mgr, Leaf(area))
        assert read_tree(mgr) == Leaf(area)
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


# --- the drag controller --------------------------------------------------------

PEDRO = ("topic", "news", "chart", "analyst", "company", "watch", "comm")


@pytest.fixture
def controller(qapp):
    """An always-active controller filtering the whole app, removed afterwards."""
    ctrl = FreeResizeController(lambda: True)
    qapp.installEventFilter(ctrl)
    yield ctrl
    qapp.removeEventFilter(ctrl)


def _dock(mgr, name):
    d = QtAds.CDockWidget(mgr, name)
    d.setObjectName(name)
    d.setWidget(QLabel(name))
    return d


@pytest.fixture
def pedro_window(qapp, controller):
    """[topic/news] | [chart/[analyst|company]] | [watch/comm], 1200x800, with
    news's top (y~300) above chart's bottom (y~450) so news borders chart."""
    win = QMainWindow()
    win.resize(1200, 800)
    mgr = QtAds.CDockManager(win)
    A = QtAds.DockWidgetArea
    topic = mgr.addDockWidget(A.LeftDockWidgetArea, _dock(mgr, "topic"))
    chart = mgr.addDockWidget(A.RightDockWidgetArea, _dock(mgr, "chart"))
    watch = mgr.addDockWidget(A.RightDockWidgetArea, _dock(mgr, "watch"))
    mgr.addDockWidget(A.BottomDockWidgetArea, _dock(mgr, "news"), topic)
    analyst = mgr.addDockWidget(A.BottomDockWidgetArea, _dock(mgr, "analyst"), chart)
    mgr.addDockWidget(A.RightDockWidgetArea, _dock(mgr, "company"), analyst)
    mgr.addDockWidget(A.BottomDockWidgetArea, _dock(mgr, "comm"), watch)
    win.show()
    qapp.processEvents()
    mgr.rootSplitter().setSizes([360, 480, 360])
    for area, top in ((topic, 300), (chart, 450), (watch, 400)):
        col = area.parentWidget()
        col.setSizes([top, col.height() - top - col.handleWidth()])
    qapp.processEvents()
    yield mgr, controller
    win.close()
    win.deleteLater()
    qapp.processEvents()


def handle_at(container, x, y):
    """The visible splitter handle under root-splitter point (x, y)."""
    root = container.rootSplitter()
    for h in root.findChildren(QSplitterHandle):
        if not h.isVisible():
            continue
        tl = h.mapTo(root, QPoint(0, 0))
        if tl.x() <= x < tl.x() + h.width() and tl.y() <= y < tl.y() + h.height():
            return h
    raise AssertionError(f"no handle at {(x, y)}")


def drag(container, x, y, dx, dy, press_mods=Qt.NoModifier,
         move_mods=Qt.NoModifier):
    """Press the handle at root point (x, y), move by (dx, dy) in two steps,
    release. Local points are recomputed from global ones as the handle moves."""
    h = handle_at(container, x, y)
    start = container.rootSplitter().mapToGlobal(QPoint(x, y))
    QTest.mousePress(h, Qt.LeftButton, press_mods, h.mapFromGlobal(start))
    for f in (0.5, 1.0):
        to = start + QPoint(int(dx * f), int(dy * f))
        QTest.mouseEvent(QTest.MouseAction.MouseMove, h, Qt.NoButton, move_mods,
                         h.mapFromGlobal(to))
    end = start + QPoint(dx, dy)
    QTest.mouseRelease(h, Qt.LeftButton, Qt.NoModifier, h.mapFromGlobal(end))
    QApplication.processEvents()


def test_drag_news_edge_leaves_company_untouched(pedro_window):
    mgr, _ = pedro_window
    old = named_rects(mgr, PEDRO)
    drag(mgr, old["news"].right + 1, old["news"].y + 100, 50, 0)
    new = named_rects(mgr, PEDRO)
    for n in ("topic", "news"):
        assert new[n].w == old[n].w + 50
    for n in ("chart", "analyst"):
        assert new[n].w == old[n].w - 50 and new[n].x == old[n].x + 50
    for n in ("company", "watch", "comm"):
        assert new[n] == old[n]


def test_plain_drag_moves_only_its_column(grid_window, controller):
    mgr = grid_window
    old = named_rects(mgr)
    drag(mgr, old["a"].x + 50, old["a"].bottom + 1, 0, 60)
    new = named_rects(mgr)
    assert new["a"].h == old["a"].h + 60 and new["c"].h == old["c"].h - 60
    assert new["b"] == old["b"] and new["d"] == old["d"]


def test_shift_drag_moves_whole_line(grid_window, controller):
    mgr = grid_window
    old = named_rects(mgr)
    drag(mgr, old["a"].x + 50, old["a"].bottom + 1, 0, 60,
         press_mods=Qt.ShiftModifier)
    new = named_rects(mgr)
    for n in ("a", "b"):
        assert new[n].h == old[n].h + 60
    for n in ("c", "d"):
        assert new[n].h == old[n].h - 60 and new[n].y == old[n].y + 60


def _chart_bottom_drag(mgr, move_mods):
    """Drag chart's bottom to SNAP_PX // 2 below topic's bottom edge."""
    old = named_rects(mgr, PEDRO)
    target = old["topic"].bottom + SNAP_PX // 2
    drag(mgr, old["chart"].x + 50, old["chart"].bottom + 1, 0,
         target - old["chart"].bottom, move_mods=move_mods)
    return old, named_rects(mgr, PEDRO), target


def test_drag_snaps_level_with_parallel_edge(pedro_window):
    mgr, _ = pedro_window
    old, new, _ = _chart_bottom_drag(mgr, Qt.NoModifier)
    assert new["chart"].bottom == old["topic"].bottom
    assert new["analyst"].y == new["company"].y == new["news"].y


def test_alt_drag_skips_snap(pedro_window):
    mgr, _ = pedro_window
    old, new, target = _chart_bottom_drag(mgr, Qt.AltModifier)
    assert new["chart"].bottom == target
    assert new["topic"] == old["topic"] and new["news"] == old["news"]


def test_drag_stops_at_last_valid(pedro_window):
    mgr, _ = pedro_window
    old = named_rects(mgr, PEDRO)
    drag(mgr, old["news"].right + 1, old["news"].y + 100, 2000, 0)
    new = named_rects(mgr, PEDRO)
    ar = areas(mgr, PEDRO)
    for n, r in new.items():
        hint = ar[n].minimumSizeHint()
        assert r.w >= hint.width() and r.h >= hint.height()
    assert new["news"].w > old["news"].w
    for n in ("company", "watch", "comm"):
        assert new[n] == old[n]


def test_inactive_with_hidden_areas(pedro_window):
    mgr, ctrl = pedro_window
    mgr.findDockWidget("company").toggleView(False)
    QApplication.processEvents()
    names = tuple(n for n in PEDRO if n != "company")
    tree = shape(read_tree(mgr))
    old = named_rects(mgr, names)
    drag(mgr, old["news"].right + 1, old["news"].y + 100, 50, 0)
    new = named_rects(mgr, names)
    assert shape(read_tree(mgr)) == tree
    assert new["news"].w == old["news"].w + 50  # the native drag still ran
    assert ctrl._drag is None


def test_inactive_when_is_active_false(pedro_window):
    mgr, ctrl = pedro_window
    ctrl._is_active = lambda: False
    old = named_rects(mgr, PEDRO)
    drag(mgr, old["news"].right + 1, old["news"].y + 100, 50, 0)
    new = named_rects(mgr, PEDRO)
    assert new["news"].w == old["news"].w + 50  # native drag
    # natively the whole centre column shrinks, so company changes with it
    assert new["company"] != old["company"]
    assert ctrl._drag is None


def test_drag_in_floating_container(qapp, controller):
    win = QMainWindow()
    win.resize(800, 600)
    mgr = QtAds.CDockManager(win)
    A = QtAds.DockWidgetArea
    mgr.addDockWidget(A.LeftDockWidgetArea, _dock(mgr, "main1"))
    mgr.addDockWidget(A.RightDockWidgetArea, _dock(mgr, "main2"))
    fw = mgr.addDockWidgetFloating(_dock(mgr, "a"))
    fa = mgr.findDockWidget("a").dockAreaWidget()
    fb = mgr.addDockWidget(A.RightDockWidgetArea, _dock(mgr, "b"), fa)
    mgr.addDockWidget(A.BottomDockWidgetArea, _dock(mgr, "c"), fa)
    mgr.addDockWidget(A.BottomDockWidgetArea, _dock(mgr, "d"), fb)
    win.show()
    fw.resize(800, 600)
    fw.show()
    qapp.processEvents()
    try:
        container = fw.dockContainer()
        assert container.rootSplitter() is not mgr.rootSplitter()
        main_before = read_rects(mgr)
        old = named_rects(mgr, container=container)
        drag(container, old["a"].x + 50, old["a"].bottom + 1, 0, 40)
        new = named_rects(mgr, container=container)
        assert new["a"].h == old["a"].h + 40 and new["c"].h == old["c"].h - 40
        assert new["b"] == old["b"] and new["d"] == old["d"]
        assert read_rects(mgr) == main_before
    finally:
        fw.close()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reshaping_drag_through_window_keeps_following(grid_window, controller):
    """A move that re-nests deletes the grabbed handle; the drag must keep
    following the pointer and end cleanly. Events go through the window, as
    real OS events do, so Qt's grab decides who receives them."""
    mgr = grid_window
    win = mgr.parentWidget()
    root = mgr.rootSplitter()
    old = named_rects(mgr)
    assert abs(old["a"].bottom - old["b"].bottom) <= 2  # a "+" crossing
    x, y = old["a"].right + 1, old["a"].y + 100
    handle = handle_at(mgr, x, y)
    wh = win.windowHandle()

    def at(dx):
        return root.mapTo(win, QPoint(x + dx, y))

    QTest.mousePress(wh, Qt.LeftButton, Qt.NoModifier, at(0))
    for dx in (20, 60, 100, 140):
        QTest.mouseMove(wh, at(dx))
        QApplication.processEvents()
    QTest.mouseRelease(wh, Qt.LeftButton, Qt.NoModifier, at(140))
    QApplication.processEvents()
    new = named_rects(mgr)
    assert root.orientation() == Qt.Vertical  # it did re-nest (rows-first)
    assert new["a"].right == old["a"].right + 140
    assert new["c"] == old["c"] and new["d"] == old["d"]
    assert controller._drag is None
    assert QWidget.mouseGrabber() is None
    del handle


def test_drag_ends_when_button_no_longer_held(grid_window, controller):
    """A lost release (grab lost, modal popped) must not leave a drag stuck."""
    mgr = grid_window
    old = named_rects(mgr)
    h = handle_at(mgr, old["a"].x + 50, old["a"].bottom + 1)
    QTest.mousePress(h, Qt.LeftButton, Qt.NoModifier, QPoint(5, 1))
    assert controller._drag is not None
    mv = QMouseEvent(QEvent.MouseMove, QPointF(5, 20), h.mapToGlobal(QPointF(5, 20)),
                     Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(h, mv)
    assert controller._drag is None
    assert QWidget.mouseGrabber() is None


def _window_press(mgr, x, y):
    """Press root point (x, y) through the window, as a real OS press arrives.
    Returns ``at(dx, dy)``: window coordinates of the press point plus (dx, dy)."""
    win = mgr.parentWidget()
    root = mgr.rootSplitter()

    def at(dx=0, dy=0):
        return root.mapTo(win, QPoint(x + dx, y + dy))

    QTest.mousePress(win.windowHandle(), Qt.LeftButton, Qt.NoModifier, at())
    return at


def test_window_drag_then_hover_reaches_panel(grid_window, controller, qapp):
    """After a window-level drag that keeps the shape, Qt's implicit grab must
    be cleared: a later hover over another panel reaches that panel, not the
    old handle (a stuck resize cursor and dead tooltips otherwise)."""
    mgr = grid_window
    root, win = mgr.rootSplitter(), mgr.parentWidget()  # kept across the drag
    wh = win.windowHandle()
    old = named_rects(mgr)
    at = _window_press(mgr, old["a"].x + 50, old["a"].bottom + 1)
    for dy in (20, 40, 60):
        QTest.mouseMove(wh, at(0, dy))
        qapp.processEvents()
    QTest.mouseRelease(wh, Qt.LeftButton, Qt.NoModifier, at(0, 60))
    qapp.processEvents()
    new = named_rects(mgr)
    assert new["a"].h == old["a"].h + 60 and new["b"] == old["b"]

    label = mgr.findDockWidget("d").widget()
    label.setMouseTracking(True)
    hits = []

    class Spy(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.MouseMove and isinstance(obj, QWidget):
                hits.append(obj)
            return False

    spy = Spy()
    qapp.installEventFilter(spy)
    try:
        assert isValid(root)  # its Python wrapper survived the drag
        for k in (100, 120):
            QTest.mouseMove(wh, root.mapTo(win, QPoint(new["d"].x + k, new["d"].y + k)))
            qapp.processEvents()
    finally:
        qapp.removeEventFilter(spy)
    assert hits and hits[0] is label  # parents see it too as it propagates
    assert not any(isinstance(w, QSplitterHandle) for w in hits)
    assert wh.cursor().shape() not in (Qt.SplitHCursor, Qt.SplitVCursor)


def test_divider_looks_held_while_dragging(grid_window, controller, qapp):
    """The pressed divider keeps its held (ACCENT_DEEP) fill for the drag."""
    from aurantium import theme
    mgr = grid_window
    mgr.setStyleSheet(theme.ADS_STYLESHEET)
    qapp.processEvents()
    win, wh = mgr.parentWidget(), mgr.parentWidget().windowHandle()
    old = named_rects(mgr)
    x, y = old["a"].x + 50, old["a"].bottom + 1
    h = handle_at(mgr, x, y)

    def fill():
        p = h.mapTo(win, QPoint(h.width() // 2, h.height() // 2))
        return win.grab().toImage().pixelColor(p).name()

    at = _window_press(mgr, x, y)
    QTest.mouseMove(wh, at(0, 30))
    qapp.processEvents()
    assert controller._drag is not None
    assert fill() == theme.ACCENT_DEEP.lower()
    QTest.mouseRelease(wh, Qt.LeftButton, Qt.NoModifier, at(0, 30))
    qapp.processEvents()
    assert controller._drag is None
    assert fill() != theme.ACCENT_DEEP.lower()


@pytest.mark.parametrize("change", ["inactive", "hidden"])
def test_drag_ends_when_layout_changes_under_it(grid_window, qapp, change):
    """F11 (controller goes inactive) or a panel hidden mid-drag ends the drag
    without touching the layout."""
    active = [True]
    ctrl = FreeResizeController(lambda: active[0])
    qapp.installEventFilter(ctrl)
    try:
        mgr = grid_window
        old = named_rects(mgr)
        h = handle_at(mgr, old["a"].x + 50, old["a"].bottom + 1)
        QTest.mousePress(h, Qt.LeftButton, Qt.NoModifier, QPoint(5, 1))
        assert ctrl._drag is not None
        if change == "inactive":
            active[0] = False
        else:
            mgr.findDockWidget("d").toggleView(False)
            qapp.processEvents()
        before = named_rects(mgr, ("a", "c"))
        QTest.mouseEvent(QTest.MouseAction.MouseMove, h, Qt.NoButton,
                         Qt.NoModifier, QPoint(5, 40))
        assert ctrl._drag is None
        assert QWidget.mouseGrabber() is None
        if change == "inactive":
            assert named_rects(mgr, ("a", "c")) == before
        QTest.mouseRelease(h, Qt.LeftButton, Qt.NoModifier, QPoint(5, 40))
        qapp.processEvents()
    finally:
        qapp.removeEventFilter(ctrl)


@pytest.fixture
def shared(qapp, monkeypatch):
    """A fresh shared controller for this test, removed afterwards."""
    monkeypatch.setattr(free_resize, "_shared", None)
    yield
    ctrl = free_resize._shared
    if ctrl is not None and isValid(ctrl):
        qapp.removeEventFilter(ctrl)
        delete(ctrl)


def _drag_a_b_line(mgr):
    """Drag the a|b divider at a's height; return (old, new) rects."""
    old = named_rects(mgr)
    drag(mgr, old["a"].right + 1, old["a"].y + 50, 40, 0)
    return old, named_rects(mgr)


def test_install_shares_one_filter(grid_window, shared, qapp):
    """Every window registers with one app-wide filter, not one each: leftover
    windows would otherwise pile filters up that see every event."""
    other = QMainWindow()
    try:
        first = free_resize.install(grid_window, lambda: True)
        assert free_resize.install(QtAds.CDockManager(other), lambda: True) is first
    finally:
        delete(other)


def test_shared_filter_gates_each_manager(grid_window, shared, qapp):
    gate = [False]
    ctrl = free_resize.install(grid_window, lambda: gate[0])
    a = named_rects(grid_window)["a"]
    h = handle_at(grid_window, a.right + 1, a.y + 50)
    for is_open in (False, True):
        gate[0] = is_open
        QTest.mousePress(h, Qt.LeftButton, Qt.NoModifier, QPoint(1, 5))
        assert (ctrl._drag is not None) == is_open
        QTest.mouseRelease(h, Qt.LeftButton, Qt.NoModifier, QPoint(1, 5))
        qapp.processEvents()
        assert ctrl._drag is None


def test_shared_filter_leaves_unregistered_managers_alone(grid_window, shared):
    other = QMainWindow()
    try:
        free_resize.install(QtAds.CDockManager(other), lambda: True)
        old, new = _drag_a_b_line(grid_window)
        assert new["c"] != old["c"]  # native: not ours to re-nest
        assert free_resize._shared._drag is None
    finally:
        delete(other)


def test_gate_dropped_when_manager_dies(shared, qapp):
    other = QMainWindow()
    mgr = QtAds.CDockManager(other)
    key = getCppPointer(mgr)[0]
    ctrl = free_resize.install(mgr, lambda: True)
    assert key in ctrl._gates
    delete(other)
    assert key not in ctrl._gates
