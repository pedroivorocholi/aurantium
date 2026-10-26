"""No panel scrolls sideways: tables shrink their columns to fit, tab rows
elide, Key Metrics' group chips wrap and show "&" literally."""

import pytest
from PySide6.QtWidgets import QAbstractScrollArea, QTableWidgetItem


@pytest.fixture(scope="module")
def registry(qapp):
    from aurantium.panel import PanelRegistry, discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    return PanelRegistry


def _fill(panel):
    from PySide6.QtWidgets import QTableWidget

    for t in panel.findChildren(QTableWidget):
        t.setRowCount(max(t.rowCount(), 4))
        for r in range(t.rowCount()):
            for c in range(t.columnCount()):
                if t.item(r, c) is None:
                    t.setItem(r, c, QTableWidgetItem("Long Company Name Inc" if c == 0 else "-1,234,567.89%"))


@pytest.mark.parametrize("width", [200, 320, 460])
def test_no_panel_scrolls_sideways(qapp, registry, width):
    overflow = []
    for meta in registry.all():
        panel = meta.cls()
        panel.build()
        panel.on_symbol = lambda s: None
        panel.resize(width, 500)
        panel.show()
        qapp.processEvents()
        _fill(panel)
        qapp.processEvents()
        panel.resize(width, 500)
        qapp.processEvents()
        for sa in panel.findChildren(QAbstractScrollArea):
            if sa.isVisibleTo(panel) and sa.horizontalScrollBar().maximum() > 0:
                overflow.append(f"{meta.id}: {type(sa).__name__}")
        panel.close()
        panel.deleteLater()
    assert not overflow, overflow


@pytest.mark.parametrize("width", [260, 340, 480])
def test_no_panel_outgrows_its_dock(qapp, registry, width):
    """What the user actually sees: QtAds wraps every docked panel in a
    scroll area sized to the dock. A panel whose minimum width is wider than
    the dock scrolled sideways there — Company held ~1000px through the
    Financials picker row. Panels must lay out inside any dock width."""
    from PySide6.QtWidgets import QScrollArea

    from aurantium.components.fit_width import relax_min_widths

    wide = []
    for meta in registry.all():
        panel = meta.cls()
        panel.build()
        relax_min_widths(panel)  # what MainWindow.add_panel does
        panel.on_symbol = lambda s: None
        dock = QScrollArea()
        dock.setWidgetResizable(True)
        dock.setWidget(panel)
        dock.resize(width, 600)
        dock.show()
        qapp.processEvents()
        _fill(panel)
        qapp.processEvents()
        if dock.horizontalScrollBar().maximum() > 0:
            wide.append(f"{meta.id}: needs {panel.minimumSizeHint().width()}px")
        dock.close()
        dock.deleteLater()
    assert not wide, wide


def test_responsive_row_wraps_and_fills(qapp):
    from PySide6.QtWidgets import QLineEdit, QPushButton, QWidget

    from aurantium.components.responsive_row import ResponsiveRow

    host = QWidget()
    row = ResponsiveRow(host)
    edit = QLineEdit(host)
    buttons = [QPushButton(f"Button number {i}", host) for i in range(5)]
    row.addWidget(edit, 1)
    for b in buttons:
        row.addWidget(b)
    row.addStretch(1)
    one_line = row.heightForWidth(5000)
    assert row.heightForWidth(150) > one_line            # wraps when narrow
    m = row.contentsMargins()  # a parented layout keeps the style's margins
    widest = max(w.minimumSizeHint().width() for w in [edit, *buttons])
    assert row.minimumSize().width() == widest + m.left() + m.right()   # one item, not the sum
    host.resize(5000, one_line)
    host.show()
    qapp.processEvents()
    assert edit.width() > edit.sizeHint().width()        # stretch takes the slack
    assert len({b.y() for b in buttons}) == 1            # one line when it fits
    narrow = row.minimumSize().width() + 10   # room for one control per line
    host.resize(narrow, row.heightForWidth(narrow))
    qapp.processEvents()
    assert len({b.y() for b in buttons}) == len(buttons)  # one per line when narrow
    assert all(b.geometry().right() <= narrow - m.right() for b in buttons)


def test_fitter_shrinks_then_restores_modes(qapp):
    from PySide6.QtWidgets import QHeaderView, QTableWidget

    from aurantium.components.fit_width import fit_columns

    t = QTableWidget(3, 4)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    for r in range(3):
        for c in range(4):
            t.setItem(r, c, QTableWidgetItem("x" * 30))
    fitter = fit_columns(t)
    assert fit_columns(t) is fitter
    t.resize(200, 200)
    t.show()
    qapp.processEvents()
    fitter.fit()
    header = t.horizontalHeader()
    assert t.horizontalScrollBar().maximum() == 0
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    t.resize(2000, 200)
    qapp.processEvents()
    fitter.fit()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    t.close()


def test_chips_collapse_to_one_line(qapp):
    """Chips when they fit; a dropdown when they don't — never more lines.
    Wrapped chips stacked four lines above the Key Metrics table."""
    from aurantium.components.chip_bar import ChipBar

    bar = ChipBar()
    picked = []
    bar.currentChanged.connect(picked.append)
    for label in ("All", "Income & Cash Flow", "Dividends & Buybacks", "Risk", "Custom"):
        i = bar.addTab(label)
        bar.setTabData(i, label)
    assert bar.tabText(1) == "Income & Cash Flow"
    assert bar._chips[1].text() == "Income && Cash Flow"   # no mnemonic underline
    bar.resize(2000, bar.sizeHint().height())
    bar.show()
    qapp.processEvents()
    assert not bar.collapsed and not bar.combo.isVisible()
    one_line = bar.height()
    bar.resize(150, one_line)
    qapp.processEvents()
    assert bar.collapsed and bar.combo.isVisible()
    assert not any(c.isVisible() for c in bar._chips)
    assert bar.sizeHint().height() == one_line             # same height both ways
    bar.combo.activated.emit(3)                            # pick from the dropdown
    assert bar.currentIndex() == 3 and picked[-1] == 3
    bar.setCurrentIndex(1)
    assert bar.combo.currentIndex() == 1 and bar._chips[1].isChecked()
    bar.removeTab(0)
    assert bar.currentIndex() == 0 and bar.tabData(0) == "Income & Cash Flow"
    assert bar.combo.count() == 4


def test_panels_fill_the_dock_not_their_preferred_height(qapp, registry):
    """QtAds' scroll area sized height-for-width content to its *preferred*
    height: with wrapping rows inside, the chart grew taller than its dock and
    the plot was cut off. A docked panel takes the dock's height."""
    from PySide6.QtWidgets import QScrollArea

    for pid in ("chart", "company", "movers"):
        panel = registry.get(pid).cls()
        panel.build()
        panel.on_symbol = lambda s: None
        dock = QScrollArea()
        dock.setWidgetResizable(True)
        dock.setWidget(panel)
        dock.resize(420, 520)
        dock.show()
        qapp.processEvents()
        assert panel.height() <= dock.viewport().height(), pid
        dock.close()
        dock.deleteLater()


def test_removed_chips_stop_painting_at_once(qapp):
    """Removed chips used to stay visible until deleteLater ran, painting
    under the new chips — the group chips looked fused together."""
    from aurantium.components.chip_bar import ChipBar

    bar = ChipBar()
    bar.addTab("All")
    bar.addTab("Custom")
    bar.show()
    qapp.processEvents()
    old = list(bar._chips)
    while bar.count():
        bar.removeTab(0)
    assert all(c.isHidden() for c in old)


def test_financials_period_headers_compact_when_narrow(qapp):
    from aurantium.panels.fundamentals import FundamentalsPanel

    p = FundamentalsPanel()
    p.build()
    stmt = {"columns": ["2025-09-30", "2024-09-30", "2023-09-30", "2022-09-30"],
            "rows": [["Total Revenue", 1.0, 2.0, 3.0, 4.0]]}
    p._data = {"income": {"annual": stmt}}
    p.resize(900, 400)
    p.show()
    qapp.processEvents()
    p._render()
    assert p.table.horizontalHeaderItem(1).text() == "2025-09-30"
    p.resize(300, 400)
    qapp.processEvents()
    item = p.table.horizontalHeaderItem(1)
    assert item.text() == "Sep '25" and item.toolTip() == "2025-09-30"
    p.close()



@pytest.mark.parametrize("width", [320, 480, 900])
def test_company_tables_keep_their_height(qapp, width):
    """Key Metrics and Financials keep their controls to one line above the
    table at any width, so the table — not the chrome — gets the height."""
    from PySide6.QtWidgets import QScrollArea

    from aurantium import valuation as V
    from aurantium.panels.company import CompanyPanel

    p = CompanyPanel()
    p.build()
    for c in p.children_by_key.values():
        c.on_symbol = lambda s: None
    p.child("key_metrics")._on_metrics(V.build_metrics({"symbol": "T", "info": {"trailingPE": 20}}))
    dock = QScrollArea()
    dock.setWidgetResizable(True)
    dock.setWidget(p)
    dock.resize(width, 520)
    dock.show()
    for key in ("key_metrics", "fundamentals"):
        p.show_tab(key)
        qapp.processEvents()
        child = p.child(key)
        controls_bottom = child.table.mapTo(child, child.table.rect().topLeft()).y()
        # one control line (+ inset/spacing), whatever the width
        assert controls_bottom <= 60, (key, width, controls_bottom)
    dock.close()

def test_financials_actions_are_buttons_when_wide_menu_when_narrow(qapp):
    """Browser / Download / Copy are their own buttons in a wide (maximized)
    panel and fold into one "⋯" menu only when the panel is narrow; the
    pickers get their full width before the actions unfold."""
    from PySide6.QtWidgets import QScrollArea

    from aurantium.panels.fundamentals import FundamentalsPanel

    p = FundamentalsPanel()
    p.build()
    stmt = {"columns": ["2025-09-30"], "rows": [["Total Revenue", 1.0]]}
    p._data = {"income": {"annual": stmt}}
    p._render()
    p._update_actions()
    dock = QScrollArea()
    dock.setWidgetResizable(True)
    dock.setWidget(p)
    dock.show()
    seen = {}
    for width in (320, 700, 1400):
        dock.resize(width, 300)
        for _ in range(3):
            qapp.processEvents()
        seen[width] = (p._actions.collapsed, p._stmt_bar.collapsed)
    assert seen[1400] == (False, False)          # every action a button
    assert seen[700] == (True, False)            # pickers first, actions fold
    assert seen[320][0] is True
    # the menu and the buttons drive the same actions
    p._copy_act.setEnabled(False)
    copy_btn = next(b for b, a, _m in p._actions._entries if a is p._copy_act)
    assert not copy_btn.isEnabled()
    dock.close()
