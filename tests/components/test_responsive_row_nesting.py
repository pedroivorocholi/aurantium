"""A layout nested in a ResponsiveRow lives in a host widget, never as the
row's child layout.

On PySide 6.12, destroying a row that owned a child layout deleted the child
after the row's Python side was gone; Qt's ChildRemoved handling then called
the row's ``itemAt`` — a pure virtual with no Python method left behind it.
The NotImplementedError leaked into the next Python callback (the app-wide
transitions event filter), so closing a Company panel could raise in
unrelated code. The Profile tab's price column was the one nested layout.
"""

from PySide6.QtWidgets import QApplication, QLabel, QLayout, QVBoxLayout, QWidget

from aurantium import transitions
from aurantium.components.responsive_row import ResponsiveRow


def _row_with_column():
    host = QWidget()
    outer = QVBoxLayout(host)
    row = ResponsiveRow()
    row.addWidget(QLabel("name"), 1)
    col = QVBoxLayout()
    col.addWidget(QLabel("price"))
    col.addWidget(QLabel("change"))
    row.addLayout(col)
    outer.addLayout(row)
    return host, row, col


def test_a_nested_layout_is_hosted_by_a_widget(qapp):
    host, row, col = _row_with_column()
    assert not any(isinstance(c, QLayout) for c in row.children())
    assert all(row.itemAt(i).layout() is None for i in range(row.count()))
    assert isinstance(col.parentWidget(), QWidget)
    assert col.parentWidget().parentWidget() is host


def test_a_hosted_layout_still_fills_the_line_height(qapp):
    host, row, col = _row_with_column()
    host.resize(400, 120)
    host.show()
    QApplication.processEvents()
    line_h = max(row.itemAt(i).geometry().height() for i in range(row.count()))
    assert col.parentWidget().height() == line_h
    host.close()


def test_stretch_factor_finds_a_hosted_layout(qapp):
    _host, row, col = _row_with_column()
    assert row.setStretchFactor(col, 3)
    assert row.stretch(1) == 3


def test_destroying_a_company_panel_raises_nothing(qapp):
    from aurantium.panel import PanelRegistry, discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    flt = transitions.install(qapp)
    try:
        panel = PanelRegistry.get("company").cls()
        panel.build()
        panel.deleteLater()
        del panel
        QApplication.sendPostedEvents(None, 52)  # QEvent.DeferredDelete
        QApplication.processEvents()
    finally:
        qapp.removeEventFilter(flt)
