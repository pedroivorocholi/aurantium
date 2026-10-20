"""ActionBar — a panel's action buttons, folding into one "⋯" menu when
there isn't room for them.

The ``ChipBar`` rule applied to actions: a control line never wraps. When the
panel is wide (say, maximized) every action is its own button, one click
away; when it is narrow they collapse into a single "⋯" menu holding the
same actions, so the table below keeps its height.

The ``QAction`` is the source of truth — text, tooltip, enabled state and the
slot — and both the buttons and the menu entries follow it, so disabling an
action disables it in whichever form is showing.
"""

from __future__ import annotations

from typing import Callable

import shiboken6
from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QPushButton, QSizePolicy, QToolButton, QWidget

#: gap between buttons
_GAP = 6


class ActionBar(QWidget):
    #: in a ResponsiveRow, take the full natural width or just the minimum:
    #: with less than full width this collapses, and the rest would sit unused
    ROW_ALL_OR_NOTHING = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: (button, its QAction or None for a submenu, its QMenu or None)
        self._entries: list[tuple[QPushButton, QAction | None, QMenu | None]] = []
        self._collapsed = False
        self.more_btn = QToolButton(self)
        self.more_btn.setObjectName("moreButton")
        from .. import icons
        from ..theme import CHROME_TEXT

        icons.set_icon(self.more_btn, "more", CHROME_TEXT)
        self.more_btn.setAccessibleName("More")
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self.more_btn)
        self.more_btn.setMenu(self._menu)
        self.more_btn.hide()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    # -- building ---------------------------------------------------------------

    def add_action(self, text: str, slot: Callable, tooltip: str = "", menu_text: str = "") -> QAction:
        """A button labelled ``text``; in the "⋯" menu it reads ``menu_text``
        (default: ``text``)."""
        act = QAction(menu_text or text, self)
        act.setToolTip(tooltip)
        act.triggered.connect(slot)
        btn = QPushButton(text, self)
        btn.setToolTip(tooltip)
        btn.clicked.connect(act.trigger)
        act.changed.connect(lambda b=btn, a=act: b.setEnabled(a.isEnabled()) if shiboken6.isValid(b) else None)
        self._menu.addAction(act)
        self._entries.append((btn, act, None))
        self._changed()
        return act

    def add_menu(self, text: str, tooltip: str = "") -> QMenu:
        """A button that opens a menu; in the "⋯" menu it is a submenu.
        Returns the menu to add actions to."""
        sub = QMenu(text, self)
        btn = QPushButton(text, self)
        btn.setToolTip(tooltip)
        btn.setMenu(sub)
        self._menu.addMenu(sub)
        self._entries.append((btn, None, sub))
        self._changed()
        return sub

    def add_separator(self) -> None:
        """A divider in the "⋯" menu (buttons simply sit side by side)."""
        self._menu.addSeparator()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_menu_enabled(self, menu: QMenu, enabled: bool) -> None:
        for btn, _act, sub in self._entries:
            if sub is menu:
                btn.setEnabled(enabled)
        menu.setEnabled(enabled)

    # -- layout -------------------------------------------------------------------

    def _changed(self) -> None:
        if not shiboken6.isValid(self):
            return
        self.updateGeometry()
        self._place()

    def _line_width(self) -> int:
        widths = [b.sizeHint().width() for b, _a, _m in self._entries]
        return sum(widths) + _GAP * max(len(widths) - 1, 0)

    def _height(self) -> int:
        h = max((b.sizeHint().height() for b, _a, _m in self._entries), default=0)
        return max(h, self.more_btn.sizeHint().height())

    def _place(self) -> None:
        width = self.width()
        collapse = bool(self._entries) and self._line_width() > width
        if collapse != self._collapsed:
            self._collapsed = collapse
            self.updateGeometry()
        h = self.height()
        if collapse:
            for b, _a, _m in self._entries:
                b.hide()
            hint = self.more_btn.sizeHint()
            # right-aligned: the "⋯" sits where the last button was
            self.more_btn.setGeometry(width - hint.width(), (h - hint.height()) // 2,
                                      hint.width(), hint.height())
            self.more_btn.show()
            return
        self.more_btn.hide()
        x = width - self._line_width()  # right-aligned, like the menu
        for b, _a, _m in self._entries:
            hint = b.sizeHint()
            b.setGeometry(x, (h - hint.height()) // 2, hint.width(), hint.height())
            b.show()
            x += hint.width() + _GAP

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._place()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        QTimer.singleShot(0, self._changed)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._line_width(), self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self.more_btn.sizeHint().width(), self._height())
