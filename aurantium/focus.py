"""Keyboard-focus visibility — the ``:focus-visible`` Qt does not have.

aurantium shipped with ``* { outline: 0; }`` as the first rule in its
stylesheet and exactly two ``:focus`` rules in the whole sheet, both on text
inputs. That combination removes Qt's own focus indication from every widget in
the application and replaces it almost nowhere: tabbing through a dialog, the
ring appears on the QLineEdits and then simply vanishes on the checkbox, the
buttons, the tab strip and the list. For a terminal whose entire premise is
keyboard-driven operation — and which already invested in a colour-blind mode —
that is the one accessibility axis that was never addressed.

The naive fix is worse than the bug. Adding plain ``:focus`` rules makes the
ring appear on *mouse clicks* too, so every button press leaves a highlight
behind and the interface looks permanently confused about where it is. The web
solved this with ``:focus-visible``; Qt has no such selector.

It does, however, carry the information the selector is derived from. A
``QEvent.FocusIn`` knows *why* focus moved — Tab, a shortcut, a menu, a mouse
press, a popup closing — via ``QFocusEvent.reason()``. This filter reads that
reason, stamps a ``kbFocus`` dynamic property on the widget, and lets the
stylesheet select ``[kbFocus="true"]``. Keyboard navigation gets a ring; mouse
clicks get nothing; the styling stays declarative in ``theme.py`` where the
rest of the design system lives.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt

#: The focus reasons that mean "a person is navigating with the keyboard".
#:
#: ``Popup`` and ``ActiveWindow`` are deliberately absent: focus returning after
#: a popup closes, or after the window is re-activated, is not navigation and
#: should not light anything up. ``Other`` is absent for the same reason — Qt
#: uses it for programmatic ``setFocus()`` calls, which happen on panel build.
KEYBOARD_REASONS = frozenset({
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
    Qt.FocusReason.MenuBarFocusReason,
})

#: The dynamic property the stylesheet selects on.
PROPERTY = "kbFocus"


def is_keyboard_focus(widget) -> bool:
    """Whether ``widget`` currently wears the keyboard-focus ring."""
    return bool(widget.property(PROPERTY))


class FocusVisibleFilter(QObject):
    """Application-wide event filter that maintains the ``kbFocus`` property.

    Install once, on the ``QApplication``. Every focus change costs one property
    write and — only when the value actually changes — one style repolish, which
    is what makes the new value take effect.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # A repolish can itself move focus in pathological cases (a widget that
        # grabs focus from a style event). Re-entering here would recurse.
        self._settling = False

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        if kind == QEvent.Type.FocusIn:
            self._set(obj, event.reason() in KEYBOARD_REASONS)
        elif kind == QEvent.Type.FocusOut:
            self._set(obj, False)
        return False  # never consume: this observes, it does not intercept

    def _set(self, widget, value: bool) -> None:
        if self._settling:
            return
        try:
            if bool(widget.property(PROPERTY)) == value:
                return  # no change, so no repolish — this is the common path
            widget.setProperty(PROPERTY, value)
            style = widget.style()
            if style is None:
                return
            self._settling = True
            try:
                # A dynamic property does not re-evaluate the stylesheet on its
                # own; unpolish/polish is what re-runs the selectors.
                style.unpolish(widget)
                style.polish(widget)
            finally:
                self._settling = False
        except (AttributeError, RuntimeError):
            # Not a QWidget, or destroyed underneath us mid-event.
            pass


def install(app) -> FocusVisibleFilter:
    """Install the filter on ``app`` and return it.

    The returned object must be kept alive — an event filter that gets garbage
    collected stops filtering, silently, and the rings simply never appear.
    """
    flt = FocusVisibleFilter(app)
    app.installEventFilter(flt)
    return flt
