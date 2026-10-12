"""ChipBar — one-of-many chips that never take more than one line.

Two earlier shapes failed in turn. A ``QTabBar`` with more tabs than width
grew scroll arrows and hid most of the choices (Key Metrics has thirteen
groups). Chips that *wrapped* fixed that but stacked four lines of chrome
above the table in a narrow panel, leaving the data a sliver to scroll in.

So: chips on one line when they all fit; when they don't, the same choices
collapse into a dropdown showing the current one. Either way the bar is one
line tall, and every choice is one or two clicks away.

The API mirrors the slice of ``QTabBar`` the panels use — ``addTab``,
``setTabData``/``tabData``, ``currentIndex``/``setCurrentIndex``,
``currentChanged`` — so swapping one for the other is a one-line change.
"""

from __future__ import annotations

from typing import Any

import shiboken6
from PySide6.QtCore import QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QComboBox, QPushButton, QSizePolicy, QWidget

class _ChevronCombo(QComboBox):
    """The collapsed bar's dropdown, with a visible chevron. The app theme
    styles ``QComboBox::drop-down`` but draws no arrow in it, so a collapsed
    bar read as a plain text box — nothing said it opens."""

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        from ..theme import FG_DIM

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(FG_DIM), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 11, self.height() / 2
        p.drawPolyline([QPointF(cx - 4, cy - 2), QPointF(cx, cy + 2), QPointF(cx + 4, cy - 2)])
        p.end()


#: gap between chips
_GAP = 4
#: narrowest the collapsed dropdown gets
_COMBO_MIN = 80


class ChipBar(QWidget):
    """One-of-many chips on one line, collapsing to a dropdown when they
    don't fit. ``currentChanged(index)`` fires when the user (or
    ``setCurrentIndex``) picks a different one."""

    currentChanged = Signal(int)

    #: in a ResponsiveRow, take the full natural width or just the minimum:
    #: with less than full width this collapses, and the rest would sit unused
    ROW_ALL_OR_NOTHING = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_clicked)
        self._chips: list[QPushButton] = []
        self._data: list[Any] = []
        self._current = -1
        self._collapsed = False
        self.combo = _ChevronCombo(self)
        self.combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.combo.setMinimumWidth(1)
        self.combo.hide()
        self.combo.activated.connect(self._on_clicked)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # -- QTabBar-shaped API ---------------------------------------------------

    def count(self) -> int:
        return len(self._chips)

    def addTab(self, text: str) -> int:  # noqa: N802 (QTabBar API)
        chip = QPushButton(text.replace("&", "&&"), self)  # "&" is a mnemonic
        chip.setObjectName("chartChip")
        chip.setCheckable(True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        index = len(self._chips)
        self._group.addButton(chip, index)
        self._chips.append(chip)
        self._data.append(None)
        self.combo.blockSignals(True)
        self.combo.addItem(text)
        self.combo.blockSignals(False)
        if self._current < 0:
            self._set_current(0, emit=not self.signalsBlocked())
        self._changed()
        return index

    def removeTab(self, index: int) -> None:  # noqa: N802 (QTabBar API)
        if not 0 <= index < len(self._chips):
            return
        chip = self._chips.pop(index)
        self._data.pop(index)
        self._group.removeButton(chip)
        # hide now: deleteLater runs on a later event-loop turn, and until
        # then the old chip paints at its old spot, under the new ones
        chip.hide()
        chip.deleteLater()
        self.combo.blockSignals(True)
        self.combo.removeItem(index)
        self.combo.blockSignals(False)
        for i, c in enumerate(self._chips):
            self._group.setId(c, i)
        if index < self._current or self._current >= len(self._chips):
            self._current -= 1
        self._changed()

    def tabText(self, index: int) -> str:  # noqa: N802 (QTabBar API)
        return self.combo.itemText(index)

    def setTabData(self, index: int, data: Any) -> None:  # noqa: N802 (QTabBar API)
        self._data[index] = data

    def tabData(self, index: int) -> Any:  # noqa: N802 (QTabBar API)
        return self._data[index] if 0 <= index < len(self._data) else None

    def setTabToolTip(self, index: int, tip: str) -> None:  # noqa: N802 (QTabBar API)
        self._chips[index].setToolTip(tip)
        self.combo.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)

    def currentIndex(self) -> int:  # noqa: N802 (QTabBar API)
        return self._current

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (QTabBar API)
        if 0 <= index < len(self._chips):
            self._set_current(index, emit=index != self._current)

    @property
    def collapsed(self) -> bool:
        """True while the choices are shown as a dropdown."""
        return self._collapsed

    # -- internals --------------------------------------------------------------

    def _set_current(self, index: int, emit: bool) -> None:
        self._current = index
        self._chips[index].setChecked(True)
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        # the checked chip is bold, so wider: re-measure once the style has
        # applied, or it overlaps its neighbour
        QTimer.singleShot(0, self._changed)
        if emit and not self.signalsBlocked():
            self.currentChanged.emit(index)

    def _on_clicked(self, index: int) -> None:
        if index != self._current:
            self._set_current(index, emit=True)

    def _changed(self) -> None:
        if not shiboken6.isValid(self):
            return
        self.updateGeometry()
        self._place()

    def _line_width(self) -> int:
        widths = [c.sizeHint().width() for c in self._chips]
        return sum(widths) + _GAP * max(len(widths) - 1, 0)

    def _height(self) -> int:
        # the same height in both modes, so the table below never jumps
        # when the bar switches between chips and dropdown
        chip_h = max((c.sizeHint().height() for c in self._chips), default=0)
        return max(chip_h, self.combo.sizeHint().height())

    def _place(self) -> None:
        width = self.width()
        collapse = bool(self._chips) and self._line_width() > width
        if collapse != self._collapsed:
            self._collapsed = collapse
            self.updateGeometry()
        if collapse:
            for c in self._chips:
                c.hide()
            h = self.combo.sizeHint().height()
            w = min(width, max(self.combo.sizeHint().width(), _COMBO_MIN))
            self.combo.setGeometry(0, (self.height() - h) // 2, w, h)
            self.combo.show()
            return
        self.combo.hide()
        x = 0
        for c in self._chips:
            hint = c.sizeHint()
            c.setGeometry(x, (self.height() - hint.height()) // 2, hint.width(), hint.height())
            c.show()
            x += hint.width() + _GAP

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._place()

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._line_width(), self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(_COMBO_MIN, self._height())
