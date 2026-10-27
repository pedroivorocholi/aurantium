"""ResponsiveRow — a horizontal row that wraps instead of forcing its width.

A ``QHBoxLayout`` of controls can never be narrower than all of its controls
side by side, and that minimum propagates up: the panel reports it, the dock
wraps the panel in a scroll area, and a narrow dock scrolls sideways. The
Financials picker row alone held the Company panel at ~1000px.

``ResponsiveRow`` is a drop-in for ``QHBoxLayout`` (``addWidget(w, stretch)``,
``addStretch``, ``addSpacing``, ``addLayout``, ``insertWidget``…):

* when its items fit on one line it lays out like a box layout — each item at
  least its minimum, grown toward its size hint, then leftover width shared
  by stretch factors (and expanding spacers);
* when they don't, it breaks into as many lines as needed, each laid out the
  same way, with stretches and spacings dropped (they only mean something on
  a single line). Its minimum width is its widest single item.

It reports height-for-width, so the layout above gives a wrapped row the
height its extra lines need.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QSizePolicy, QSpacerItem, QWidget, QWidgetItem

#: Marks the plain widget ``insertLayout`` puts a nested layout in.
_HOSTS_LAYOUT = "responsiveRowHost"


class ResponsiveRow(QLayout):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list = []      # QLayoutItem
        self._stretch: list[int] = []
        self._spacing = -1
        if parent is None:
            self.setContentsMargins(0, 0, 0, 0)

    # -- QHBoxLayout-compatible API ----------------------------------------------

    def setSpacing(self, spacing: int) -> None:  # noqa: N802 (Qt API)
        self._spacing = spacing
        self.invalidate()

    def spacing(self) -> int:
        # QHBoxLayout's default gap under this app's style
        return self._spacing if self._spacing >= 0 else 6

    def _insert(self, index: int, item, stretch: int) -> None:
        if index < 0 or index > len(self._items):
            index = len(self._items)
        self._items.insert(index, item)
        self._stretch.insert(index, int(stretch))
        self.invalidate()

    def addItem(self, item) -> None:  # noqa: N802 (Qt override)
        self._insert(-1, item, 0)

    def addWidget(self, widget: QWidget, stretch: int = 0, alignment=Qt.AlignmentFlag(0)) -> None:  # noqa: N802
        self.insertWidget(-1, widget, stretch, alignment)

    def insertWidget(self, index: int, widget: QWidget, stretch: int = 0, alignment=Qt.AlignmentFlag(0)) -> None:  # noqa: N802
        self.addChildWidget(widget)
        item = QWidgetItem(widget)
        if alignment:
            item.setAlignment(alignment)
        self._insert(index, item, stretch)

    def addLayout(self, layout: QLayout, stretch: int = 0) -> None:  # noqa: N802
        self.insertLayout(-1, layout, stretch)

    def insertLayout(self, index: int, layout: QLayout, stretch: int = 0) -> None:  # noqa: N802
        # Hosted in a plain widget, never a child layout of this row: on
        # PySide 6.12, destroying the row deleted a child layout after the
        # row's Python side was gone, and Qt's ChildRemoved handling called
        # ``itemAt`` on it — a pure virtual with nothing behind it, whose
        # error leaked into the next Python callback.
        host = QWidget()
        host.setProperty(_HOSTS_LAYOUT, True)
        layout.setContentsMargins(0, 0, 0, 0)
        host.setLayout(layout)
        self.insertWidget(index, host, stretch)

    def addStretch(self, stretch: int = 0) -> None:  # noqa: N802
        self.insertStretch(-1, stretch)

    def insertStretch(self, index: int, stretch: int = 0) -> None:  # noqa: N802
        spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._insert(index, spacer, stretch)

    def addSpacing(self, size: int) -> None:  # noqa: N802
        self.insertSpacing(-1, size)

    def insertSpacing(self, index: int, size: int) -> None:  # noqa: N802
        spacer = QSpacerItem(size, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._insert(index, spacer, 0)

    def setStretch(self, index: int, stretch: int) -> None:  # noqa: N802
        if 0 <= index < len(self._stretch):
            self._stretch[index] = int(stretch)
            self.invalidate()

    def stretch(self, index: int) -> int:
        return self._stretch[index] if 0 <= index < len(self._stretch) else 0

    def setStretchFactor(self, target, stretch: int) -> bool:  # noqa: N802
        for i, item in enumerate(self._items):
            w = item.widget()
            if w is target or (w is not None and w.property(_HOSTS_LAYOUT) and w.layout() is target):
                self._stretch[i] = int(stretch)
                self.invalidate()
                return True
        return False

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 (Qt override)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 (Qt override)
        if not 0 <= index < len(self._items):
            return None
        self._stretch.pop(index)
        item = self._items.pop(index)
        self.invalidate()
        return item

    def expandingDirections(self):  # noqa: N802 (Qt override)
        dirs = Qt.Orientation.Horizontal
        if any(self._expands_v(i) for i in self._visible()):
            dirs |= Qt.Orientation.Vertical
        return dirs

    # -- geometry -------------------------------------------------------------------

    def _visible(self) -> list[int]:
        out = []
        for i, item in enumerate(self._items):
            w = item.widget()
            if w is not None and w.isHidden():
                continue
            if item.isEmpty() and item.spacerItem() is None:
                continue
            out.append(i)
        return out

    @staticmethod
    def _hosts_layout(item) -> bool:
        # a nested layout fills its line's height, as it did as a child layout
        w = item.widget()
        return w is not None and bool(w.property(_HOSTS_LAYOUT))

    @staticmethod
    def _min_w(item) -> int:
        return max(item.minimumSize().width(), 0)

    @staticmethod
    def _hint_w(item) -> int:
        return max(item.sizeHint().width(), item.minimumSize().width(), 0)

    def set_grow_priority(self, widget: QWidget, priority: int) -> None:
        """Order in which items grow from their minimum toward their natural
        width: lower first (default 0). An item whose compact form is a fine
        fallback — an overflow menu — waits until the rest are full size."""
        widget.setProperty("_rowGrowPriority", int(priority))
        self.invalidate()

    def _atomic(self, i: int) -> bool:
        w = self._items[i].widget()
        return bool(w is not None and getattr(w, "ROW_ALL_OR_NOTHING", False))

    def _priority(self, i: int) -> int:
        w = self._items[i].widget()
        value = w.property("_rowGrowPriority") if w is not None else None
        return int(value) if value is not None else 0

    def _grows(self, i: int) -> int:
        """Share of leftover width: its stretch, or 1 if it expands."""
        if self._stretch[i]:
            return self._stretch[i]
        if self._items[i].expandingDirections() & Qt.Orientation.Horizontal:
            return 1
        return 0

    def _lines(self, width: int) -> list[list[int]]:
        idx = self._visible()
        sp = self.spacing()
        total = sum(self._min_w(self._items[i]) for i in idx) + sp * max(len(idx) - 1, 0)
        if total <= width:
            return [idx]
        # wrap: stretches and spacings only mean something on one line
        idx = [i for i in idx if self._items[i].spacerItem() is None]
        lines: list[list[int]] = []
        line: list[int] = []
        used = 0
        for i in idx:
            w = self._min_w(self._items[i])
            if line and used + sp + w > width:
                lines.append(line)
                line, used = [], 0
            used += (sp if line else 0) + w
            line.append(i)
        if line:
            lines.append(line)
        return lines

    def _widths(self, line: list[int], width: int) -> dict[int, int]:
        sp = self.spacing()
        avail = max(width - sp * max(len(line) - 1, 0), 0)
        widths = {i: self._min_w(self._items[i]) for i in line}
        spare = avail - sum(widths.values())
        # grow toward size hints, highest priority first (lowest number);
        # within a priority, in proportion to what each lacks
        for prio in sorted({self._priority(i) for i in line}):
            if spare <= 0:
                break
            tier = [i for i in line if self._priority(i) == prio]
            want = {i: self._hint_w(self._items[i]) - widths[i] for i in tier}
            # all-or-nothing items (a bar that collapses to a dropdown or a
            # "⋯" when short of its full width) take their whole hint or
            # stay at their minimum — a partial share would sit unused
            for i in [i for i in tier if self._atomic(i) and want[i] > 0]:
                if want[i] <= spare:
                    widths[i] += want[i]
                    spare -= want[i]
                want[i] = 0
            need = sum(max(v, 0) for v in want.values())
            if need:
                give = min(spare, need)
                for i in tier:
                    if want[i] > 0:
                        widths[i] += want[i] * give // need
                spare = avail - sum(widths.values())
        # leftover to stretch factors / expanding items — but never past an
        # item's maximum, or the excess becomes a dead gap beside it instead
        # of going to the stretches that want it
        for _ in range(len(line)):
            if spare <= 0:
                break
            growers = {i: self._grows(i) for i in line
                       if widths[i] < self._items[i].maximumSize().width()}
            total = sum(growers.values())
            if not total:
                break
            for i, g in growers.items():
                cap = self._items[i].maximumSize().width()
                widths[i] = min(cap, widths[i] + spare * g // total)
            spare = avail - sum(widths.values())
        return widths

    def _line_height(self, line: list[int], widths: dict[int, int] | None = None) -> int:
        h = 0
        for i in line:
            item = self._items[i]
            if item.hasHeightForWidth() and widths is not None:
                h = max(h, item.heightForWidth(widths[i]))
            else:
                h = max(h, item.sizeHint().height())
        return h

    def _expands_v(self, i: int) -> bool:
        return bool(self._items[i].expandingDirections() & Qt.Orientation.Vertical)

    def _do_layout(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        width = max(area.width(), 0)
        vsp = self.spacing()
        lines = self._lines(width)
        widths = [self._widths(line, width) for line in lines]
        heights = [self._line_height(line, w) for line, w in zip(lines, widths)]
        if apply:
            # spare height goes to lines holding vertically-expanding items
            # (a table), so a row of tables still fills the panel
            spare = area.height() - sum(heights) - vsp * max(len(lines) - 1, 0)
            tall = [n for n, line in enumerate(lines) if any(self._expands_v(i) for i in line)]
            if spare > 0 and tall:
                for n in tall:
                    heights[n] += spare // len(tall)
        y = area.y()
        for n, line in enumerate(lines):
            lh = heights[n]
            if apply:
                x = area.x()
                for i in line:
                    item = self._items[i]
                    if self._hosts_layout(item) or self._expands_v(i):
                        ih = lh
                    else:
                        ih = max(min(item.sizeHint().height(), lh), min(item.minimumSize().height(), lh))
                    item.setGeometry(QRect(x, y + (lh - ih) // 2, widths[n][i], ih))
                    x += widths[n][i] + self.spacing()
            y += lh + (vsp if n < len(lines) - 1 else 0)
        return y - rect.y() + m.bottom()

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt override)
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt override)
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        idx = self._visible()
        m = self.contentsMargins()
        w = sum(self._hint_w(self._items[i]) for i in idx) + self.spacing() * max(len(idx) - 1, 0)
        return QSize(w + m.left() + m.right(), self._line_height(idx) + m.top() + m.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt override)
        idx = [i for i in self._visible() if self._items[i].spacerItem() is None]
        m = self.contentsMargins()
        w = max((self._min_w(self._items[i]) for i in idx), default=0)
        h = max((self._items[i].minimumSize().height() for i in idx), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())
