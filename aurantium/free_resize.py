"""Qt bridge for per-panel divider drags: read a dock container's splitter tree
as :mod:`aurantium.tiling` rectangles/nodes and apply a tree back to it.

Keys are ``CDockAreaWidget`` objects. ``restoreState`` recreates dock areas, so
nothing here caches them: every call reads the live widgets afresh. Only
visible areas count; hidden ones (closed panels) stay parked where they are.
"""

from __future__ import annotations

import PySide6QtAds as QtAds
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from .tiling import Leaf, Node, Rect, Rects, Split, shape

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
    """Size the visible ``kids``; hidden siblings keep their stored size."""
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
