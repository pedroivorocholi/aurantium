"""Menus, combo dropdowns and tooltips are translucent windows, so their
rounded borders don't sit inside square opaque corners."""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QComboBox, QMenu, QToolTip

from aurantium import popups
from aurantium.theme import apply_theme


def _corner_alpha(widget) -> int:
    return widget.grab().toImage().pixelColor(0, 0).alpha()


def test_popups_have_transparent_corners(qapp):
    apply_theme(qapp)
    flt = popups.install(qapp)
    try:
        menu = QMenu()
        menu.addAction("Alpha")
        menu.popup(QPoint(100, 100))
        qapp.processEvents()
        assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert _corner_alpha(menu) == 0
        menu.hide()

        combo = QComboBox()
        combo.addItems(["A", "B", "C"])
        combo.show()
        combo.showPopup()
        qapp.processEvents()
        assert _corner_alpha(combo.view().window()) == 0
        combo.hidePopup()

        QToolTip.showText(QPoint(100, 100), "tip")
        qapp.processEvents()
        tip = next(w for w in qapp.topLevelWidgets()
                   if w.metaObject().className() == "QTipLabel")
        assert _corner_alpha(tip) == 0
        QToolTip.hideText()
    finally:
        qapp.removeEventFilter(flt)
