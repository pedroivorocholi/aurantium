"""Every clickable surface answers the pointer.

The 1.8.0 pass gave buttons a full state set and stopped there. Audited
afterwards, most of the *other* clickable things in the app had one or two
states and no more: an inner tab bar had no pressed state, a sortable column
header had no pressed state, a dock tab had none, a tool button had no disabled
state, and the single most-clicked surface in the whole product — a table row,
where a click re-centers every linked panel — had no hover state at all.

This holds the line. It is a coverage matrix, not a colour check: what each
state *looks* like is decided in ``theme.py`` and verified on screen, but that a
state exists at all is mechanical and belongs in a test.

Exemptions are listed explicitly with a reason rather than left out quietly, so
a future reader can tell "deliberately absent" from "nobody got round to it".
"""

import re

import pytest

from aurantium import theme

HOVER, PRESSED, ON, FOCUS, OFF = "hover", "pressed", "on", "focus", "disabled"

#: selector -> states it must define.
MATRIX = {
    "QPushButton": {HOVER, PRESSED, ON, FOCUS, OFF},
    "QPushButton#chartChip": {HOVER, PRESSED, ON, FOCUS},
    "QToolButton": {HOVER, PRESSED, ON, FOCUS, OFF},
    "QTabBar::tab": {HOVER, PRESSED, ON, FOCUS, OFF},
    "QHeaderView::section": {HOVER, PRESSED, FOCUS, OFF},
    "QMenuBar::item": {PRESSED, ON, OFF},
    "QMenu::item": {ON, OFF},
    "QComboBox": {HOVER, PRESSED, FOCUS, OFF},
    "QCheckBox::indicator": {HOVER, PRESSED, ON, OFF},
    "QRadioButton::indicator": {HOVER, PRESSED, ON, OFF},
    "QScrollBar::handle:vertical": {HOVER, PRESSED},
    "QScrollBar::handle:horizontal": {HOVER, PRESSED},
    "QTableView::item": {HOVER, ON},
    "QListWidget::item": {HOVER, ON},
    "QListView#suggestPopup::item": {HOVER, ON},
    "ads--CDockWidgetTab": {HOVER, PRESSED, ON, FOCUS},
    "ads--CTitleBarButton": {HOVER, PRESSED, FOCUS, OFF},
    "ads--CDockSplitter::handle": {HOVER, PRESSED},
}

#: What Qt actually calls each state in a selector. ``on`` and ``focus`` have
#: more than one spelling because Qt does not use one word for them: a menu
#: entry under the pointer is ``:selected``, a dock tab that is current carries
#: the ``activeTab`` property, and keyboard focus is aurantium's own ``kbFocus``
#: property (focus.py) rather than ``:focus``, which would fire on mouse clicks.
SPELLINGS = {
    HOVER: ("hover",),
    PRESSED: ("pressed",),
    ON: ("checked", "selected", "activeTab", "on"),
    FOCUS: ("kbFocus", "focus"),
    OFF: ("disabled",),
}

#: Deliberately absent, with the reason. Anything not listed here and not in
#: MATRIX is simply not asserted either way.
EXEMPT = {
    ("QMenuBar::item", HOVER): "Qt uses :selected for a menu entry under the pointer",
    ("QMenu::item", HOVER): "same — :selected is the hover spelling for menus",
    ("QTableView::item", PRESSED): "a row is selected, not actuated; selection is the feedback",
    ("QListWidget::item", PRESSED): "as above",
    ("QPushButton#chartChip", OFF): "inherits QPushButton:disabled",
}

SHEET = theme.STYLESHEET + "\n" + theme.ADS_STYLESHEET


def _defines(selector: str, state: str) -> bool:
    """Whether the sheet has a rule for ``selector`` in ``state``.

    Matches both the pseudo-class form (``QPushButton:hover``) and the dynamic
    property form (``QPushButton[kbFocus="true"]``), and tolerates the property
    sitting before a sub-control — ``QCheckBox[kbFocus="true"]::indicator`` is
    how the focus ring is put on an indicator rather than on its label, and a
    naive scan reads that as missing.
    """
    base = selector.split("::")[0]
    sub = selector.split("::")[1] if "::" in selector else None
    for word in SPELLINGS[state]:
        patterns = [
            re.escape(selector) + r"[^,{\n]*:" + re.escape(word),
            re.escape(selector) + r"\[" + re.escape(word),
        ]
        if sub:
            patterns.append(
                re.escape(base) + r"\[" + re.escape(word) + r'[^\]]*\]::' + re.escape(sub)
            )
        if any(re.search(p, SHEET) for p in patterns):
            return True
    return False


@pytest.mark.parametrize(
    "selector,state",
    [(sel, st) for sel, states in MATRIX.items() for st in sorted(states)],
)
def test_clickable_surfaces_define_their_states(selector, state):
    assert _defines(selector, state), (
        f"{selector} has no {state} state — every clickable surface answers "
        f"the pointer, or is listed in EXEMPT with a reason"
    )


@pytest.mark.parametrize("key,reason", sorted(EXEMPT.items()))
def test_exemptions_are_still_exemptions(key, reason):
    """An exemption that quietly became true is a stale comment; drop it from
    EXEMPT and move the state into MATRIX."""
    selector, state = key
    assert not _defines(selector, state), (
        f"{selector} now defines {state} — remove the exemption ({reason})"
    )


def test_the_row_hover_does_not_borrow_the_accent():
    """Amber means "this is data" in every table in this app. A row hover in
    the accent would read as a value changing, not as a pointer moving."""
    start = SHEET.index("QTableView::item:hover")
    rule = SHEET[start : SHEET.index("}", start)]
    assert theme.ACCENT not in rule
    assert theme.CHROME_LOW in rule


def test_sub_controls_are_the_only_snapping_presses():
    """A QSS sub-control (a tab, a header section, a scrollbar handle) cannot
    carry the animated scrim — there is no child widget to attach one to — so
    its press snaps. That limitation is Qt's, and it must stay confined to
    sub-controls: any *widget* that presses should go through press.py.
    """
    from aurantium import press
    from PySide6.QtWidgets import QPushButton, QToolButton

    # The widget-level controls the user actually hits most.
    assert press.corner_radius(QPushButton()) is not None
    assert press.corner_radius(QToolButton()) is not None
    # And the note explaining the split has to stay next to the rules.
    assert "sub-control" in SHEET or "sub-controls" in SHEET


def test_a_table_row_actually_paints_its_hover(qapp):
    """Presence in the sheet is not the same as reaching the screen.

    Item views only hover at all if Qt turns on ``WA_Hover`` and mouse tracking
    for the viewport, which it does lazily *because* a hover rule exists — so
    this asserts the whole chain, not the rule. It renders through the real
    style with ``State_MouseOver`` set, the way the item delegate does.

    Note the style call takes the **view**, not the viewport: pass the viewport
    and ``QTableView::item`` cannot resolve, the primitive paints nothing, and
    the result looks exactly like a missing rule.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtWidgets import (
        QStyle,
        QStyleOptionViewItem,
        QTableWidget,
        QTableWidgetItem,
    )

    theme.apply_theme(qapp)
    table = QTableWidget(2, 1)
    table.setItem(0, 0, QTableWidgetItem("TSLA"))
    table.resize(200, 90)
    table.ensurePolished()
    qapp.processEvents()

    assert table.viewport().testAttribute(Qt.WidgetAttribute.WA_Hover), (
        "Qt never enabled hover tracking — the ::item:hover rule cannot fire"
    )

    def row_fill(hover: bool) -> str:
        option = QStyleOptionViewItem()
        option.initFrom(table)
        option.rect = table.visualItemRect(table.item(0, 0))
        option.state |= QStyle.StateFlag.State_Enabled
        if hover:
            option.state |= QStyle.StateFlag.State_MouseOver
        pixmap = QPixmap(option.rect.size())
        pixmap.fill(Qt.GlobalColor.black)
        painter = QPainter(pixmap)
        painter.translate(-option.rect.x(), -option.rect.y())
        table.style().drawPrimitive(
            QStyle.PrimitiveElement.PE_PanelItemViewItem, option, painter, table
        )
        painter.end()
        return pixmap.toImage().pixelColor(4, option.rect.height() // 2).name()

    assert row_fill(True) == theme.CHROME_LOW.lower()
    assert row_fill(True) != row_fill(False)
