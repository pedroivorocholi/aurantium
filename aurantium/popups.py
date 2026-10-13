"""Make popup windows (menus, combo dropdowns, tooltips) truly rounded.

The stylesheet gives QMenu / the combo list / QToolTip a ``border-radius``,
but each of those is its own top-level window, and an opaque window is
always a rectangle: the rounded border is painted *inside* a square box,
so the square corners show around it (plus Windows' square drop shadow).

The fix is per window: no frame, no native shadow, translucent background.
It must land before the native window is created, so this hooks the Polish
event (sent ahead of the first show) on the application.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QMenu, QWidget

# Qt's private popup classes, matched by class name
_POPUP_CLASSES = ("QComboBoxPrivateContainer", "QTipLabel")


def is_rounded_popup(widget: QObject) -> bool:
    if not isinstance(widget, QWidget):
        return False
    if isinstance(widget, QMenu):
        return True
    return widget.metaObject().className() in _POPUP_CLASSES


def make_translucent(widget: QWidget) -> None:
    if widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
        return
    widget.setWindowFlags(
        widget.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class RoundedPopupFilter(QObject):
    """App-wide filter: makes every menu/dropdown/tooltip window translucent."""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Polish and is_rounded_popup(obj):
            try:
                make_translucent(obj)
            except RuntimeError:
                pass  # destroyed mid-event
        return False


def install(app) -> RoundedPopupFilter:
    """Install on ``app``; keep the returned object alive."""
    flt = RoundedPopupFilter(app)
    app.installEventFilter(flt)
    return flt
