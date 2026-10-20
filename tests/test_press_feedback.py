"""Press feedback: the scrim fires on mouse-down, clears, and honours the
reduced-motion setting.

The defect these tests pin is a colour one, not a crash: ``:pressed`` used to
set ``HEADER_BLUE`` (#1a2129) against a resting fill of ``BG_ELEV`` (#1b2530) —
one level, four and seven, which on screen is the same colour, so a pressed
button looked unpressed. ``test_pressed_is_not_the_stylesheet_state`` and the
sheet assertions below are what keep a future edit from quietly reintroducing a
snap to a near-identical colour instead of motion.
"""

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QWidget,
)

from aurantium import motion, press, theme


@pytest.fixture
def filt(qapp):
    """The app-wide filter, removed again afterwards so one test's presses
    cannot leak into the next one's widgets."""
    flt = press.install(qapp)
    yield flt
    qapp.removeEventFilter(flt)


def _click(qapp, widget, kind, button=Qt.MouseButton.LeftButton):
    event = QMouseEvent(
        kind,
        QPointF(4, 4),
        QPointF(4, 4),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(widget, event)


def _settle(qapp, anim):
    """Run the animation to its end without sleeping for its duration."""
    anim.setCurrentTime(anim.duration())
    qapp.processEvents()


# -- eligibility ------------------------------------------------------------


@pytest.mark.parametrize("factory", [QPushButton, QToolButton])
def test_buttons_take_press_feedback(qapp, factory):
    assert press.corner_radius(factory()) == theme.RADIUS_SM


@pytest.mark.parametrize("factory", [QCheckBox, QLineEdit, QLabel])
def test_non_buttons_do_not(qapp, factory):
    """A checkbox's press target is a 12px indicator and a line edit's is a
    text caret; a shadow over the whole widget would point at the wrong thing."""
    assert press.corner_radius(factory()) is None
    assert press.attach(factory()) is None


def test_the_scrim_is_created_once_per_widget(qapp):
    button = QPushButton("Refresh")
    assert press.attach(button) is press.attach(button)


# -- the press cycle --------------------------------------------------------


def test_a_mouse_press_sinks_the_control(qapp, filt):
    button = QPushButton("Refresh")
    button.resize(90, 26)
    assert press.press_level(button) == 0.0

    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    assert scrim is not None
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(1.0)


def test_releasing_clears_it(qapp, filt):
    button = QPushButton("Refresh")
    button.resize(90, 26)
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)

    _click(qapp, button, QEvent.Type.MouseButtonRelease)
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(0.0)


def test_a_release_anywhere_clears_a_stuck_press(qapp, filt):
    """A tab dragged out of its dock, or a button whose menu opens under the
    cursor, never delivers the release to the widget that got the press. The
    filter tracks the active scrim globally so those cannot stick."""
    button = QPushButton("Refresh")
    button.resize(90, 26)
    elsewhere = QPushButton("Other")
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(1.0)

    _click(qapp, elsewhere, QEvent.Type.MouseButtonRelease)
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(0.0)


def test_the_right_button_does_not_press(qapp, filt):
    """Right-click opens a context menu in several panels; it is not a press."""
    button = QPushButton("Refresh")
    button.resize(90, 26)
    _click(qapp, button, QEvent.Type.MouseButtonPress, Qt.MouseButton.RightButton)
    assert press.press_level(button) == 0.0


def test_a_disabled_control_does_not_press(qapp, filt):
    button = QPushButton("Refresh")
    button.setEnabled(False)
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    assert press.press_level(button) == 0.0


# -- the motion budget ------------------------------------------------------


def test_the_press_is_inside_the_budget():
    """``motion.py``'s ceiling for interface motion, and the brief's own
    "instant, under 100ms" for the moment the user is actually watching."""
    assert motion.PRESS_IN_MS <= 120
    assert motion.PRESS_OUT_MS <= motion.FLASH_MS + 60


def test_reduced_motion_still_delivers_the_state(qapp, filt, monkeypatch):
    """Reduced motion is gentler, not absent. The control still reads as
    pressed; the press simply does not travel — and, critically, it must not
    leave a control stuck in shadow, which is the failure ``motion.py``'s
    docstring records from the link flash."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    button = QPushButton("Refresh")
    button.resize(90, 26)

    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    assert scrim._anim.duration() == 0
    assert press.press_level(button) == pytest.approx(1.0)

    _click(qapp, button, QEvent.Type.MouseButtonRelease)
    assert press.press_level(button) == pytest.approx(0.0)


def test_the_house_curve_is_used(qapp):
    button = QPushButton("Refresh")
    scrim = press.attach(button)
    assert scrim._anim.easingCurve().type() == motion.EASE_OUT


# -- the stylesheet side of the contract ------------------------------------


def test_pressed_is_not_the_stylesheet_state(qapp):
    """``:pressed`` holds the hover fill rather than swapping to another
    colour. A swap would land on the first frame and hide the eased sink
    underneath it — and the colour it used to swap to was invisible anyway."""
    sheet = theme.STYLESHEET
    assert f"QPushButton:pressed {{\n    background: {theme.CHROME_HOVER};" in sheet
    # The exact rule that shipped. HEADER_BLUE is still a legitimate colour
    # elsewhere (it is the table column-header band), so this pins the rule
    # rather than the value.
    assert f"QPushButton:pressed {{ background: {theme.HEADER_BLUE}; }}" not in sheet


def test_pressed_is_never_lighter_than_hover_on_a_tool_button():
    """The old rule raised a pressed QToolButton to rgba(…,0.30) against a
    hover of 0.18 — i.e. a press read as *more* hovered, the opposite of a
    control being pushed in."""
    sheet = theme.STYLESHEET
    assert "QToolButton:pressed {{ background: rgba(128,128,128,0.30); }}" not in sheet
    assert "QToolButton:pressed { background: rgba(128,128,128,0.18); }" in sheet


@pytest.mark.parametrize(
    "selector",
    [
        "QPushButton {",
        "QPushButton:hover {",
        "QPushButton:pressed {",
        "QPushButton:checked {",
        'QPushButton[kbFocus="true"] {',
        "QPushButton:disabled {",
    ],
)
def test_every_resting_state_of_a_button_is_defined(selector):
    """Six states, each with a rule of its own. ``:pressed`` is in the list
    because the rule still has to exist — it is what a keyboard activation and
    a reduced-motion press fall back to — but what it *says* is pinned by
    ``test_pressed_is_not_the_stylesheet_state`` above."""
    assert selector in theme.STYLESHEET


def test_the_press_alpha_is_tuned_per_theme():
    """The same alpha is not the same perceptual step over black and over
    white: on light the resting and hover fills are only ~14 levels apart."""
    assert set(theme._PRESS_ALPHA) == set(theme.THEMES)
    assert theme._PRESS_ALPHA["dark"] > theme._PRESS_ALPHA["light"]
    assert 0.0 < theme.PRESS_ALPHA < 1.0


def test_a_dock_tab_takes_press_feedback(qapp):
    """Dock tabs are the one eligible control that is not a QAbstractButton.
    They are matched by class name rather than by ``isinstance`` so ``press``
    never has to import a docking library to decide whether a QPushButton is a
    button — and they take radius 0, because this theme draws them square."""
    QtAds = pytest.importorskip("PySide6QtAds")
    # (manager, title) rather than (title): the single-argument constructor is
    # deprecated, and app.py already uses this form.
    manager = QtAds.CDockManager()
    tab = QtAds.CDockWidgetTab(QtAds.CDockWidget(manager, "Watchlist"))
    assert tab.metaObject().className().endswith("CDockWidgetTab")
    assert press.corner_radius(tab) == 0


def test_the_filter_is_installed_at_startup():
    """``install`` is called from ``__main__.main()``, next to the focus
    filter, and the result is held on the app object.

    A filter that is installed but not referenced is garbage collected and
    stops filtering *silently* — every control simply goes back to
    acknowledging nothing, with no error anywhere. This is the same trap
    ``focus.install`` documents, so the same belt goes on it.
    """
    from pathlib import Path

    import aurantium

    source = (Path(aurantium.__file__).parent / "__main__.py").read_text(
        encoding="utf-8"
    )
    assert "from . import press" in source
    assert "app._aurantium_press_filter = press.install(app)" in source


def test_press_is_in_the_frozen_build():
    """It is imported lazily from inside ``main()``, so PyInstaller's static
    analysis never sees it — the same shape that put ``focus`` and
    ``onboarding_dialog`` in the spec's hiddenimports."""
    from pathlib import Path

    import aurantium

    spec = (Path(aurantium.__file__).parent.parent / "aurantium.spec").read_text(
        encoding="utf-8"
    )
    assert '"aurantium.press"' in spec
    assert '"aurantium.icons"' in spec


# -- the scrim has to actually paint ----------------------------------------
#
# These are the tests that were missing. Everything above asserts the animated
# *property*, and the property was always correct — it animated 0 -> 1 exactly
# as designed while the widget carrying it was hidden, so a press painted
# nothing and the whole feature was invisible in the running app. Asserting a
# property is not asserting a pixel.


def test_the_scrim_is_visible_while_the_press_is_in_flight(qapp, filt):
    """``QPropertyAnimation.start()`` writes its start value synchronously, and
    for a press-in that value is 0.0. Any teardown keyed on "value is zero"
    therefore fires one line after the scrim is shown."""
    button = QPushButton("Refresh")
    button.resize(90, 26)
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    assert not scrim.isHidden(), "the scrim hid itself as the animation started"


def test_a_press_darkens_the_control_on_screen(qapp, filt):
    """The end-to-end assertion: press the button, then look at the pixels the
    button actually painted — not at the property driving them."""
    # The palette has to be live: without it the button renders Fusion's default
    # grey and "darker than CHROME_HOVER" compares against a colour that is not
    # on screen.
    theme.apply_theme(qapp)
    button = QPushButton("Refresh")
    button.resize(96, 27)
    button.ensurePolished()
    qapp.processEvents()

    def fill():
        # The button's own grab, which includes its children — and the scrim is
        # a child. Grabbing a parent instead samples whatever the parent painted
        # and can pass while the scrim is hidden.
        image = button.grab().toImage()
        return image.pixelColor(8, button.height() // 2)

    resting = fill()
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)
    pressed = fill()

    assert pressed != resting, "the press painted nothing"
    assert sum(pressed.getRgb()[:3]) < sum(resting.getRgb()[:3]), (
        f"pressed {pressed.name()} is not darker than resting {resting.name()}"
    )
    # and darker than the hover fill it sits on top of, which is the whole
    # point: hover lifts, pressing sinks.
    hover = QColor(theme.CHROME_HOVER)
    assert sum(pressed.getRgb()[:3]) < sum(hover.getRgb()[:3]), (
        f"pressed {pressed.name()} is not darker than hover {hover.name()}"
    )


def test_releasing_hides_the_scrim_again(qapp, filt):
    """It must not stay installed over the control once the press is over —
    a widget that paints nothing still costs a composite on every repaint."""
    button = QPushButton("Refresh")
    button.resize(90, 26)
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)
    _click(qapp, button, QEvent.Type.MouseButtonRelease)
    _settle(qapp, scrim._anim)
    qapp.processEvents()
    assert scrim.isHidden()


def test_an_unrelated_widget_hiding_does_not_cancel_the_press(qapp, filt):
    """The filter is application-wide, so it sees ``Hide`` for every object in
    the app — not just the one being pressed.

    This is what made dock tabs look broken. Clicking an *inactive* tab hides
    the panel you are switching away from; that unrelated ``Hide`` arrived one
    event after the press started and tore the scrim down before a single frame
    painted. Clicking the already-active tab hides nothing, so it worked — and
    "works on the tab that is already selected" is a very good disguise.
    """
    button = QPushButton("Refresh")
    button.resize(90, 26)
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(1.0)

    # Something else in the application goes away. This is the real shape:
    # clicking an inactive dock tab makes QtAds hide the *outgoing* tab's close
    # button, and that is a QPushButton child of a visible parent. A top-level
    # widget that was never really shown emits no Hide at all, which is why the
    # first version of this test passed against the bug.
    host = QWidget()
    unrelated = QPushButton("x", host)
    host.show()
    qapp.processEvents()
    unrelated.hide()
    qapp.processEvents()

    # Settle first. A cancelled press does not drop to zero instantly — it
    # starts a 160ms decay — so checking the level straight after the hide
    # reads ~1.0 either way and passes against the bug.
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(1.0), (
        "an unrelated widget hiding cancelled the press"
    )
    assert not scrim.isHidden()


def test_the_pressed_widget_hiding_does_cancel_it(qapp, filt):
    """The narrow case the Hide handling was actually for: whatever the click
    did removed the control itself, so there is nothing left to decay over."""
    container = QWidget()
    button = QPushButton("Refresh", container)
    button.resize(90, 26)
    container.show()
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    _settle(qapp, scrim._anim)

    container.hide()
    qapp.processEvents()
    _settle(qapp, scrim._anim)
    assert press.press_level(button) == pytest.approx(0.0)


def test_the_scrim_follows_a_control_that_resizes_mid_press(qapp, filt):
    """A control can change size *because* it was clicked.

    A QtAds dock tab is the case that found this: activating a tab reveals its
    close button, so the tab jumps from 88px to 108px the instant the press
    lands. Geometry set once at mouse-down leaves the shadow 20px short of the
    control for the whole press, which reads as a rendering fault rather than
    as feedback.
    """
    # Shown, because a never-shown widget defers its QResizeEvent until it is
    # polished — so a hidden widget would silently never deliver the resize
    # this is about.
    button = QPushButton("Refresh")
    button.resize(90, 26)
    button.show()
    qapp.processEvents()
    _click(qapp, button, QEvent.Type.MouseButtonPress)
    scrim = press.scrim_of(button)
    assert scrim.size() == button.size()

    button.resize(140, 30)
    qapp.processEvents()
    assert scrim.size() == button.size(), (
        f"scrim {scrim.size()} did not follow the control to {button.size()}"
    )


# -- opt-in surfaces (Company ▸ Profile stat tiles) ----------------------------


def test_clickable_profile_tiles_sink_like_buttons(qapp, filt):
    """The stat tiles are frames, not buttons, so the filter skipped them and
    a click on one acknowledged nothing. A clickable tile opts in through
    ``pressRadius``; an inert one (Employees) stays inert."""
    from aurantium.panels.profile import StatTile

    tile = StatTile("P/E TTM")
    tile.resize(120, 48)
    tile.show()
    qapp.processEvents()
    assert press.corner_radius(tile) is None          # inert: no feedback
    tile.set_clickable(True)
    assert press.corner_radius(tile) == theme.RADIUS_SM
    # users click the figure, i.e. the label child: the press propagates up
    # to the tile, which is what sinks
    qapp.sendEvent(tile.value, QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(4, 4), QPointF(4, 4),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    scrim = press.scrim_of(tile)
    assert scrim is not None
    _settle(qapp, scrim._anim)
    assert press.press_level(tile) > 0.5
    _click(qapp, tile, QEvent.Type.MouseButtonRelease)
    _settle(qapp, scrim._anim)
    assert press.press_level(tile) == pytest.approx(0.0)
    tile.set_clickable(False)
    assert press.corner_radius(tile) is None


# -- rows, sections, tabs, tiles, marks ---------------------------------------
#
# Item views, header sections, tab bars and custom-painted surfaces sink only
# the pressed region, with the same scrim, curve, durations and alpha as a
# button.


def _press_at(qapp, widget, pos, kind=QEvent.Type.MouseButtonPress):
    event = QMouseEvent(
        kind,
        QPointF(pos),
        QPointF(widget.mapToGlobal(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(widget, event)


def _row_centre(view, row, col=0):
    return view.visualRect(view.model().index(row, col)).center()


def _settled_region(qapp, host):
    scrim = getattr(host, press._REGION_ATTR)
    _settle(qapp, scrim._anim)
    return scrim


def test_pressing_a_watchlist_row_sinks_that_row(qapp, filt):
    from aurantium.panels.watchlist import WatchlistPanel

    panel = WatchlistPanel()
    panel.build()
    panel.resize(600, 400)
    panel.show()
    qapp.processEvents()
    table = panel.table
    assert table.rowCount() >= 2
    vp = table.viewport()

    _press_at(qapp, vp, _row_centre(table, 1))
    scrim = _settled_region(qapp, vp)
    assert press.press_level(table) > 0.0
    row_rect = table.visualRect(table.model().index(1, 0))
    assert scrim.geometry().top() == row_rect.top()
    assert scrim.geometry().height() == row_rect.height()
    assert scrim.geometry().width() == vp.width()  # the row, not the cell

    _press_at(qapp, vp, _row_centre(table, 1), QEvent.Type.MouseButtonRelease)
    _settle(qapp, scrim._anim)
    assert press.press_level(table) == pytest.approx(0.0)


def test_pressing_a_news_item_sinks_it(qapp, filt):
    from aurantium.panels.news import NewsPanel
    from aurantium.panels._news_common import populate_news_table

    panel = NewsPanel()
    panel.build()
    panel.resize(600, 400)
    panel.show()
    populate_news_table(panel.table, [
        {"title": "Fed holds rates", "publisher": "Wire", "url": "https://example.com/a"},
        {"title": "Oil climbs", "publisher": "Wire", "url": "https://example.com/b"},
    ])
    qapp.processEvents()
    vp = panel.table.viewport()
    _press_at(qapp, vp, _row_centre(panel.table, 0, 1))
    _settled_region(qapp, vp)
    assert press.press_level(panel.table) > 0.0


def test_one_scrim_serves_every_row(qapp, filt):
    """No per-row widget or effect: a big table must cost one scrim."""
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    table = QTableWidget(200, 3)
    for r in range(200):
        table.setItem(r, 0, QTableWidgetItem(str(r)))
    table.resize(400, 300)
    table.show()
    qapp.processEvents()
    vp = table.viewport()
    for r in (0, 3, 5):
        _press_at(qapp, vp, _row_centre(table, r))
        _press_at(qapp, vp, _row_centre(table, r), QEvent.Type.MouseButtonRelease)
    scrims = [c for c in vp.children() if isinstance(c, press._PressScrim)]
    assert len(scrims) == 1


def test_a_cell_selecting_table_sinks_the_cell(qapp, filt):
    from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

    table = QTableWidget(3, 3)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
    for r in range(3):
        for c in range(3):
            table.setItem(r, c, QTableWidgetItem("x"))
    table.resize(400, 200)
    table.show()
    qapp.processEvents()
    vp = table.viewport()
    _press_at(qapp, vp, _row_centre(table, 1, 1))
    scrim = _settled_region(qapp, vp)
    assert scrim.geometry() == table.visualRect(table.model().index(1, 1))
    _press_at(qapp, vp, _row_centre(table, 1, 1), QEvent.Type.MouseButtonRelease)


def test_a_read_only_view_does_not_pretend_to_be_a_button(qapp, filt):
    from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

    table = QTableWidget(2, 2)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setItem(0, 0, QTableWidgetItem("x"))
    table.resize(300, 200)
    table.show()
    qapp.processEvents()
    _press_at(qapp, table.viewport(), _row_centre(table, 0))
    assert press.press_level(table) == 0.0


def test_a_sortable_header_section_sinks(qapp, filt):
    from PySide6.QtWidgets import QTableWidget

    table = QTableWidget(2, 3)
    table.setSortingEnabled(True)
    table.resize(400, 200)
    table.show()
    qapp.processEvents()
    header = table.horizontalHeader()
    vp = header.viewport()
    x = header.sectionViewportPosition(1) + 5
    _press_at(qapp, vp, QPointF(x, 5).toPoint())
    scrim = _settled_region(qapp, vp)
    assert scrim.geometry().left() == header.sectionViewportPosition(1)
    assert scrim.geometry().width() == header.sectionSize(1)


def test_a_tab_sinks(qapp, filt):
    from PySide6.QtWidgets import QTabWidget

    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Profile")
    tabs.addTab(QWidget(), "Key Metrics")
    tabs.resize(400, 200)
    tabs.show()
    qapp.processEvents()
    bar = tabs.tabBar()
    _press_at(qapp, bar, bar.tabRect(1).center())
    scrim = _settled_region(qapp, bar)
    assert scrim.geometry() == bar.tabRect(1)
    assert press.press_level(bar) > 0.0


def test_a_heatmap_tile_sinks(qapp, filt):
    from aurantium.panels.sector_heatmap import _Tile

    tile = _Tile("Energy", "XLE")
    tile.resize(120, 70)
    assert press.corner_radius(tile) == _Tile.RADIUS
    _click(qapp, tile, QEvent.Type.MouseButtonPress)
    _settle(qapp, press.scrim_of(tile)._anim)
    assert press.press_level(tile) > 0.0


def test_an_editable_combo_is_a_text_field(qapp):
    from PySide6.QtWidgets import QComboBox

    plain, editable = QComboBox(), QComboBox()
    editable.setEditable(True)
    assert press.corner_radius(plain) == theme.RADIUS_SM
    assert press.corner_radius(editable) is None


def test_a_row_lands_without_travel_under_reduced_motion(qapp, filt, monkeypatch):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    table = QTableWidget(2, 2)
    table.setItem(0, 0, QTableWidgetItem("x"))
    table.resize(300, 200)
    table.show()
    qapp.processEvents()
    vp = table.viewport()
    _press_at(qapp, vp, _row_centre(table, 0))
    assert press.press_level(table) == pytest.approx(1.0)  # no frames needed
    _press_at(qapp, vp, _row_centre(table, 0), QEvent.Type.MouseButtonRelease)
    assert press.press_level(table) == pytest.approx(0.0)
