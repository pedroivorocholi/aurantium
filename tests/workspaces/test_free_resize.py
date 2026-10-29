"""Qt bridge for per-panel divider drags (aurantium.free_resize)."""

import zlib

import pytest
import PySide6QtAds as QtAds
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow

from aurantium.free_resize import apply_tree, handle_gap, read_rects, read_tree
from aurantium.tiling import Leaf, Rect, Split, build_tree, shape

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


def areas(mgr):
    """name -> its dock area, read fresh (restoreState recreates areas)."""
    return {n: mgr.findDockWidget(n).dockAreaWidget() for n in NAMES}


def named_rects(mgr):
    rects = read_rects(mgr)
    return {n: rects[a] for n, a in areas(mgr).items()}


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
