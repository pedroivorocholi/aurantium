"""Keyboard focus must be visible — and only for the keyboard.

aurantium shipped with ``* { outline: 0; }`` as the first rule in its
stylesheet and two ``:focus`` rules in the whole sheet, both on text inputs.
Qt's own focus indication was removed everywhere and replaced almost nowhere,
so tabbing through a dialog showed a ring on the QLineEdits and nothing at all
on the buttons, checkboxes, tab strip, lists or tables.

The naive repair is worse than the defect: plain ``:focus`` rules fire on mouse
clicks too, leaving a highlight behind after every press. The web's answer is
``:focus-visible``; Qt has no such selector, but ``QFocusEvent.reason()``
carries the information it is derived from. ``focus.FocusVisibleFilter`` reads
that and maintains a ``kbFocus`` dynamic property for the stylesheet.

These tests drive the filter with synthetic focus events, so they run headless
and assert the actual branch rather than a screenshot.
"""

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QCheckBox, QLineEdit, QPushButton

from aurantium import focus, theme


@pytest.fixture
def widget(qapp):
    w = QPushButton("Run")
    yield w
    w.deleteLater()


@pytest.fixture
def filt(qapp):
    return focus.FocusVisibleFilter()


def send(filt, widget, reason, *, out=False):
    kind = QEvent.Type.FocusOut if out else QEvent.Type.FocusIn
    filt.eventFilter(widget, QFocusEvent(kind, reason))


# -- the gate --------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
        Qt.FocusReason.MenuBarFocusReason,
    ],
)
def test_keyboard_navigation_shows_the_ring(filt, widget, reason):
    send(filt, widget, reason)
    assert focus.is_keyboard_focus(widget)


@pytest.mark.parametrize(
    "reason",
    [
        Qt.FocusReason.MouseFocusReason,
        Qt.FocusReason.PopupFocusReason,
        Qt.FocusReason.ActiveWindowFocusReason,
        Qt.FocusReason.OtherFocusReason,
    ],
)
def test_everything_else_stays_silent(filt, widget, reason):
    """The reason plain ``:focus`` rules were not an option.

    ``Popup`` and ``ActiveWindow`` matter specifically: focus returning after a
    menu closes or the window is re-activated is not navigation, and lighting up
    then would put a ring on screen every time the user alt-tabs back.
    ``Other`` covers programmatic ``setFocus()``, which panels do while building.
    """
    send(filt, widget, reason)
    assert not focus.is_keyboard_focus(widget)


def test_the_ring_clears_when_focus_leaves(filt, widget):
    send(filt, widget, Qt.FocusReason.TabFocusReason)
    assert focus.is_keyboard_focus(widget)
    send(filt, widget, Qt.FocusReason.TabFocusReason, out=True)
    assert not focus.is_keyboard_focus(widget)


def test_tabbing_then_clicking_drops_the_ring(filt, widget):
    """The sequence that exposes a stale property: reach a control by Tab, then
    click it. The ring must not survive the click."""
    send(filt, widget, Qt.FocusReason.TabFocusReason)
    send(filt, widget, Qt.FocusReason.TabFocusReason, out=True)
    send(filt, widget, Qt.FocusReason.MouseFocusReason)
    assert not focus.is_keyboard_focus(widget)


def test_the_filter_never_consumes_the_event(filt, widget):
    """It observes. Returning True here would swallow focus changes and break
    keyboard navigation outright — the opposite of the point."""
    for reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.MouseFocusReason):
        assert filt.eventFilter(
            widget, QFocusEvent(QEvent.Type.FocusIn, reason)
        ) is False


def test_non_widget_objects_are_survivable(filt, qapp):
    """The filter sits on the QApplication and sees every object's events, not
    just widgets'."""
    from PySide6.QtCore import QObject

    obj = QObject()
    filt.eventFilter(obj, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    # no exception is the assertion


def test_a_graphics_scene_is_survivable(filt, qapp):
    """QGraphicsScene (pyqtgraph's chart scene) has its own ``style()``, so it
    slipped past the AttributeError guard and ``unpolish(scene)`` raised a
    TypeError on every chart focus change."""
    from PySide6.QtWidgets import QGraphicsScene

    scene = QGraphicsScene()
    filt.eventFilter(scene, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason))
    filt.eventFilter(scene, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.TabFocusReason))


def test_repeated_events_do_not_thrash(filt, widget, monkeypatch):
    """Each change costs a style repolish, so an unchanged value must not
    trigger one. Panels re-focus often."""
    calls = []
    real = widget.style()
    monkeypatch.setattr(
        type(real), "polish", lambda self, w, _c=calls: _c.append(w), raising=False
    )
    for _ in range(5):
        send(filt, widget, Qt.FocusReason.TabFocusReason)
    assert len(calls) == 1


# -- the stylesheet has somewhere to put it --------------------------------


def test_the_global_outline_kill_is_gone():
    """``* { outline: 0 }`` is what made every other rule here necessary."""
    sheet = theme.STYLESHEET
    # The comment explaining the removal quotes the rule, so check for it as an
    # actual selector rather than as a substring.
    assert "\n* {" not in sheet
    assert "QAbstractItemView { outline: 0; }" in sheet


@pytest.mark.parametrize(
    "selector",
    [
        'QPushButton[kbFocus="true"]',
        'QToolButton[kbFocus="true"]',
        'QTabBar::tab[kbFocus="true"]',
        'QCheckBox[kbFocus="true"]::indicator',
        'QHeaderView::section[kbFocus="true"]',
        'QTextBrowser[kbFocus="true"]',
    ],
)
def test_every_interactive_family_has_a_ring(selector):
    assert selector in theme.STYLESHEET


def test_text_inputs_keep_their_existing_focus_rule():
    """They already had one and it works on mouse focus too, which is correct
    for a text field: the caret is there either way."""
    assert "QLineEdit:focus" in theme.STYLESHEET


def test_the_ring_is_readable_on_every_surface_it_can_land_on():
    """A ring nobody can see is the bug, restated."""
    from aurantium.color import contrast

    for name in theme.THEMES:
        pal = theme._PALETTES[name]
        for surface in ("BG", "CHROME", "BG_ELEV", "BG_HEADER", "SELECT_BLUE"):
            ratio = contrast(pal["FOCUS"], pal[surface])
            assert ratio >= 3.0, f"{name}: FOCUS on {surface} is {ratio:.2f}:1"


def test_checked_controls_use_a_ring_that_works_on_amber():
    """The neutral ring measures 1.62:1 on the dark theme's amber fill, so a
    checked button would lose it entirely. ON_ACCENT is readable on ACCENT by
    construction, which is what it exists for."""
    from aurantium.color import contrast

    assert 'QPushButton:checked[kbFocus="true"]' in theme.STYLESHEET
    for name in theme.THEMES:
        pal = theme._PALETTES[name]
        assert contrast(pal["ON_ACCENT"], pal["ACCENT"]) >= 3.0
    # ...and the reason the normal ring cannot be reused there.
    assert contrast(theme._PALETTES["dark"]["FOCUS"],
                    theme._PALETTES["dark"]["ACCENT"]) < 3.0


def test_the_focused_dock_area_is_marked():
    """F11 maximizes "the focused panel"; something has to say which."""
    assert 'ads--CDockAreaTitleBar[focused="true"]' in theme.ADS_STYLESHEET


def test_selection_survives_losing_focus():
    """Fusion's inactive highlight is not palette-derived and reads as "this row
    stopped mattering"."""
    assert "::item:selected:!active" in theme.STYLESHEET


# -- the widgets that had no rules at all ----------------------------------


@pytest.mark.parametrize(
    "selector",
    ["QGroupBox", "QTextBrowser", "QMessageBox", "QDialog",
     "QSpinBox::up-button", "QDialogButtonBox QPushButton"],
)
def test_previously_unstyled_surfaces_are_styled(selector):
    """Each of these rendered Fusion-native inside a heavily themed app."""
    assert selector in theme.STYLESHEET


def test_the_checkable_menu_indicator_is_filled():
    """It was sized but never given a fill, so Full Screen / Colour-blind mode /
    the Theme radio group drew their ticks with the native style."""
    assert "QMenu::indicator:checked" in theme.STYLESHEET
