"""Qt bridge for per-panel divider drags: read a dock container's splitter tree
as :mod:`aurantium.tiling` rectangles/nodes and apply a tree back to it.

Keys are ``CDockAreaWidget`` objects. ``restoreState`` recreates dock areas, so
nothing here caches them: every call reads the live widgets afresh. Only
visible areas count; hidden ones (closed panels) stay parked where they are.
Re-nesting in :func:`apply_tree` assumes the container has no hidden areas
(it would misplace them), so :class:`FreeResizeController` never drags then.
"""

from __future__ import annotations

from collections.abc import Callable

import PySide6QtAds as QtAds
from shiboken6 import isValid
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QSplitterHandle, QWidget

from .tiling import (Leaf, Node, Rect, Rects, Segment, Split, build_tree,
                     move_segment, movable_segment, shape, snap_delta)

_ORIENT = {"h": Qt.Horizontal, "v": Qt.Vertical}


def read_rects(container: QtAds.CDockContainerWidget) -> Rects:
    """Each visible dock area's geometry in ``rootSplitter()`` coordinates."""
    root = container.rootSplitter()
    out: Rects = {}
    for area in root.findChildren(QtAds.CDockAreaWidget):
        if area.isVisible():
            p = area.mapTo(root, area.rect().topLeft())
            out[area] = Rect(p.x(), p.y(), area.width(), area.height())
    return out


def handle_gap(container: QtAds.CDockContainerWidget) -> int:
    return container.rootSplitter().handleWidth()


def _shown(splitter: QtAds.CDockSplitter) -> list[QWidget]:
    """``splitter``'s visible children that hold a visible dock area."""
    out = []
    for i in range(splitter.count()):
        w = splitter.widget(i)
        if not w.isVisible():
            continue
        if isinstance(w, QtAds.CDockAreaWidget):
            out.append(w)
        elif isinstance(w, QtAds.CDockSplitter) and _shown(w):
            out.append(w)
    return out


def _resolve(w: QWidget) -> QWidget:
    """Skip splitters with a single visible child: they add no structure."""
    while isinstance(w, QtAds.CDockSplitter):
        kids = _shown(w)
        if len(kids) != 1:
            break
        w = kids[0]
    return w


def _orient(splitter: QtAds.CDockSplitter) -> str:
    return "h" if splitter.orientation() == Qt.Horizontal else "v"


def _read(w: QWidget) -> Node:
    w = _resolve(w)
    if isinstance(w, QtAds.CDockAreaWidget):
        return Leaf(w)
    kids = _shown(w)
    horiz = w.orientation() == Qt.Horizontal
    return Split(_orient(w), tuple(_read(k) for k in kids),
                 tuple(k.width() if horiz else k.height() for k in kids))


def read_tree(container: QtAds.CDockContainerWidget) -> Node | None:
    """The live splitter tree over visible areas, or ``None`` if there are none."""
    root = container.rootSplitter()
    if not _shown(root):
        return None
    return _read(root)


def _set_sizes(splitter: QtAds.CDockSplitter, kids: list[QWidget],
               sizes: tuple[int, ...]) -> None:
    """Size the visible ``kids``. Hidden siblings report 0 from ``sizes()`` and
    get it back, so ``setSizes`` resets them to their minimum."""
    want = dict(zip((id(k) for k in kids), sizes))
    current = splitter.sizes()
    splitter.setSizes([want.get(id(splitter.widget(i)), current[i])
                       for i in range(splitter.count())])


def _resize(w: QWidget, node: Node) -> None:
    """Same-shape apply: ``setSizes`` down the tree, no reparenting."""
    if isinstance(node, Leaf):
        return
    w = _resolve(w)
    kids = _shown(w)
    _set_sizes(w, kids, node.sizes)
    for k, child in zip(kids, node.children):
        _resize(k, child)


def _new_splitter(orient: str) -> QtAds.CDockSplitter:
    s = QtAds.CDockSplitter(_ORIENT[orient])
    s.setOpaqueResize(True)
    s.setChildrenCollapsible(False)
    return s


def _fill(splitter: QtAds.CDockSplitter, node: Split,
          made: list[QtAds.CDockSplitter]) -> None:
    """Put ``node``'s children at the front of ``splitter`` in order."""
    splitter.setOrientation(_ORIENT[node.orient])
    for i, child in enumerate(node.children):
        if isinstance(child, Leaf):
            w = child.key
        else:
            w = _new_splitter(child.orient)
            made.append(w)
            _fill(w, child, made)
        splitter.insertWidget(i, w)
        w.show()


def _prune(splitter: QtAds.CDockSplitter) -> None:
    """Delete descendant splitters with no area left; hide the hidden-only ones."""
    for i in reversed(range(splitter.count())):
        w = splitter.widget(i)
        if not isinstance(w, QtAds.CDockSplitter):
            continue
        _prune(w)
        if not w.findChildren(QtAds.CDockAreaWidget):
            w.setParent(None)
            w.deleteLater()
        elif not w.hasVisibleContent():
            w.hide()


def _renest(container: QtAds.CDockContainerWidget, root: QtAds.CDockSplitter,
            node: Node) -> None:
    if isinstance(node, Leaf):
        root.insertWidget(0, node.key)
    else:
        made: list[QtAds.CDockSplitter] = []
        _fill(root, node, made)
        for s in made:
            container.updateSplitterHandles(s)
    _prune(root)
    container.updateSplitterHandles(root)


def apply_tree(container: QtAds.CDockContainerWidget, node: Node) -> None:
    """Make the container's splitters match ``node`` (keys: its visible areas).

    Cheap when only sizes differ: just ``setSizes``. A changed shape re-nests,
    reusing ``rootSplitter()`` and building fresh child splitters.
    """
    root = container.rootSplitter()
    current = read_tree(container)
    root.setUpdatesEnabled(False)
    try:
        if current is None or shape(current) != shape(node):
            _renest(container, root, node)
        _resize(root, node)
    finally:
        root.setUpdatesEnabled(True)


# a drag whose release may never arrive (focus or app switch, a modal) ends here
_DRAG_ENDERS = frozenset((QEvent.WindowDeactivate, QEvent.ApplicationDeactivate))
_MOUSE = frozenset((QEvent.MouseMove, QEvent.MouseButtonRelease,
                    QEvent.MouseButtonPress))


class _Drag:
    """One divider drag in progress: everything fixed at press time."""

    def __init__(self, container, seg: Segment, press: QPoint, rects: Rects,
                 mins, gap: int, tree: Node, grabber: QWidget,
                 handle: QSplitterHandle, splitter: QtAds.CDockSplitter):
        self.container = container
        self.grabber = grabber  # holds the mouse grab for the whole drag
        self.handle = handle    # shows :pressed until the drag ends or it's gone
        # handle's splitter, kept from press: calling handle.splitter() again
        # later invalidates the root splitter's Python wrapper (shiboken6)
        self.splitter = splitter
        self.seg = seg
        self.press = press  # global position, so the moving handle can't skew it
        self.rects = rects
        self.mins = mins
        self.gap = gap
        self.tree = tree    # the last applied tree, preferred when both cuts fit
        self.delta: int | None = None


class FreeResizeController(QObject):
    """App-wide event filter that turns a press on a dock splitter handle into
    a per-panel drag: only the panels pressed against the grabbed edge move.

    Shift at press moves the whole straight line (the old behaviour); Alt
    during the drag skips snap-to-align. Falls through to the native QtAds
    drag when ``is_active()`` is false, when the container has a hidden area,
    or when no movable segment is found.
    """

    def __init__(self, is_active: Callable[[], bool], parent=None):
        super().__init__(parent)
        self._is_active = is_active
        self._drag: _Drag | None = None

    def eventFilter(self, obj, event):  # noqa: N802 (Qt override)
        t = event.type()
        if self._drag is None:
            if t == QEvent.MouseButtonPress and isinstance(obj, QSplitterHandle):
                return self._press(obj, event)
            return False
        if t in _MOUSE and not isinstance(obj, QWidget):
            # The window's own copy, before Qt turns it into a widget event:
            # let it through, or Qt never clears its implicit grab on release
            # and later hovers keep going to the old handle (stuck cursor).
            return False
        # Mid-drag the root splitter holds the mouse grab (a re-nest deletes
        # the pressed handle), but act on mouse events whoever receives them.
        if t == QEvent.MouseMove:
            if not event.buttons() & Qt.LeftButton:
                self._end()  # the release went missing
                return False
            self._move(event)
            return True
        if t == QEvent.MouseButtonRelease:
            self._end()
            return True
        if t == QEvent.MouseButtonPress:
            self._end()
            return self.eventFilter(obj, event)
        if t in _DRAG_ENDERS:
            self._end()
        return False

    def _end(self) -> None:
        d, self._drag = self._drag, None
        if d is None:
            return
        if isValid(d.grabber):
            d.grabber.releaseMouse()
        if isValid(d.handle) and isValid(d.splitter):
            # The real release went to the grabber; this one clears :pressed.
            # Opaque for the moment, or the handle would move its splitter to
            # the release point (QtAds splitters default to non-opaque).
            opaque = d.splitter.opaqueResize()
            d.splitter.setOpaqueResize(True)
            up = QMouseEvent(QEvent.MouseButtonRelease, QPointF(), QPointF(),
                             Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
            QApplication.sendEvent(d.handle, up)
            d.splitter.setOpaqueResize(opaque)

    def _press(self, handle: QSplitterHandle, event) -> bool:
        self._drag = None
        if event.button() != Qt.LeftButton:
            return False
        splitter = handle.splitter()
        if not isinstance(splitter, QtAds.CDockSplitter) or not self._is_active():
            return False
        area = splitter.findChild(QtAds.CDockAreaWidget)
        container = area.dockContainer() if area is not None else None
        if container is None:
            return False
        root = container.rootSplitter()
        all_areas = root.findChildren(QtAds.CDockAreaWidget)
        if any(not a.isVisible() for a in all_areas):
            return False  # re-nesting would misplace hidden areas
        tree = read_tree(container)
        if tree is None:
            return False
        rects, gap = read_rects(container), handle_gap(container)
        top_left = handle.mapTo(root, QPoint(0, 0))
        at = handle.mapTo(root, event.position().toPoint())
        if splitter.orientation() == Qt.Horizontal:  # a vertical divider line
            axis, coord, along = "v", top_left.x(), at.y()
        else:
            axis, coord, along = "h", top_left.y(), at.x()
        whole = bool(event.modifiers() & Qt.ShiftModifier)
        seg = movable_segment(rects, axis, coord, along, gap, whole_line=whole)
        if seg is None:
            return False
        mins = {}
        for a in rects:
            m = a.minimumSizeHint().expandedTo(a.minimumSize())
            mins[a] = (m.width(), m.height())
        self._drag = _Drag(container, seg, event.globalPosition().toPoint(),
                           rects, mins, gap, tree, root, handle, splitter)
        # apply_tree reuses the root splitter, so the grab survives a re-nest
        root.grabMouse(handle.cursor())
        # The press still reaches the handle, so it paints its held (:pressed)
        # look; QSplitterHandle only moves the splitter on mouse moves, and
        # every move goes through this filter until the drag ends.
        return False

    def _move(self, event) -> None:
        d = self._drag
        if (not self._is_active() or not isValid(d.container)
                or any(not isValid(a) or not a.isVisible() for a in d.rects)):
            self._end()  # F11, a workspace load or a closed panel mid-drag
            return
        moved = event.globalPosition().toPoint() - d.press
        delta = moved.x() if d.seg.axis == "v" else moved.y()
        if not event.modifiers() & Qt.AltModifier:
            delta = snap_delta(d.rects, d.seg, delta, d.gap)
        rects, delta = move_segment(d.rects, d.seg, delta, d.gap, d.mins)
        if delta == d.delta:
            return
        tree = build_tree(rects, d.gap, prefer=d.tree)
        if tree is None:
            return  # a pinwheel: hold the last valid position
        apply_tree(d.container, tree)
        d.tree, d.delta = tree, delta
