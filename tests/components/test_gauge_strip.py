"""The Macro panel's gauge strip: compact tiles edited in place.

The strip is one painted widget (like the Heatmap's map): tiles wrap onto more
rows as it narrows, a trailing **+** tile asks for a new gauge, a right-click
replaces or removes one, and a drag reorders them. Every *user* edit emits
``gauges_changed`` so the panel can push an undo step; ``set_gauges`` (the
panel restoring or undoing) never does.
"""

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from aurantium import motion, theme
from aurantium.components.gauge_strip import DEFAULT_GAUGES, Gauge, GaugeStrip
from aurantium.components.symbol_catalog import RATES_ENTRIES, search_catalog

GOLD = Gauge("quote", "GC=F", "Gold")
SILVER = Gauge("quote", "SI=F", "Silver")


@pytest.fixture
def strip(qapp):
    s = GaugeStrip()
    s.set_gauges(list(DEFAULT_GAUGES))
    s.resize(1100, s.heightForWidth(1100))
    s.show()
    QApplication.processEvents()
    yield s
    s.close()
    s.deleteLater()


def centre(strip: GaugeStrip, index: int) -> QPoint:
    return strip.tile_rect(index).center()


def codes(gauges) -> list[str]:
    return [g.code for g in gauges]


# -- defaults and the catalog ------------------------------------------------


def test_default_gauges_follow_the_spec_order():
    assert DEFAULT_GAUGES[0] == Gauge("policy", "*", "Policy rate")
    assert DEFAULT_GAUGES[1] == Gauge("curve", "*:10", "10Y")
    assert DEFAULT_GAUGES[2] == Gauge("curve", "*:2-10", "2s10s")
    assert codes(DEFAULT_GAUGES[3:]) == ["DX-Y.NYB", "^VIX", "^MOVE", "CL=F", "GC=F"]


def test_rates_entries_are_plain_english():
    assert RATES_ENTRIES
    for e in RATES_ENTRIES:
        assert e.kind == "curve" and e.category == "Rates"
        for ch in "^=:":
            assert ch not in e.label, e.label


def test_rates_entries_cover_the_brief():
    by_code = {e.code: e.label for e in RATES_ENTRIES}
    for code in ("US:2", "US:5", "US:10", "US:30", "US:2-10", "US:0.25-10",
                 "US:5-30", "XM:10", "JP:10", "CA:10", "BR:10"):
        assert code in by_code, code
    assert by_code["XM:10"] == "Bund 10Y"
    assert by_code["JP:10"] == "JGB 10Y"
    assert by_code["US:5-30"] == "5s30s spread"
    assert by_code["US:2"] == "US 2Y"


@pytest.mark.parametrize("word", ["treasury", "bund", "gilt", "jgb", "yield", "spread"])
def test_rates_entries_are_findable(word):
    assert search_catalog(RATES_ENTRIES, word), word


# -- layout --------------------------------------------------------------------


def test_wraps_when_narrow(strip):
    wide = strip.heightForWidth(1100)
    narrow = strip.heightForWidth(420)
    assert strip.hasHeightForWidth()
    assert strip.row_count(1100) == 1
    assert strip.row_count(420) > 1
    assert narrow > wide

    strip.resize(420, narrow)
    QApplication.processEvents()
    rects = [strip.tile_rect(i) for i in range(len(DEFAULT_GAUGES))]
    for r in rects:
        assert r.width() >= theme.GAUGE_TILE_MIN_PX
        assert r.left() >= 0 and r.right() < 420
        assert r.bottom() < narrow
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            assert not a.intersects(b)
    plus = strip.plus_rect()
    assert plus.isValid() and plus.right() < 420
    assert not any(plus.intersects(r) for r in rects)


def test_size_hint_matches_height_for_width(strip):
    hint = strip.sizeHint()
    assert hint.height() == strip.heightForWidth(hint.width())
    assert strip.minimumSizeHint().width() <= theme.GAUGE_TILE_MIN_PX * 2


# -- user edits ------------------------------------------------------------------


def test_set_gauges_never_emits(strip):
    spy = QSignalSpy(strip.gauges_changed)
    strip.set_gauges([GOLD, SILVER])
    assert spy.count() == 0
    assert strip.gauges() == [GOLD, SILVER]


def test_remove_emits_changed(strip):
    spy = QSignalSpy(strip.gauges_changed)
    strip.remove_gauge(2)
    assert spy.count() == 1
    emitted = spy.at(0)[0]
    assert "*:2-10" not in codes(emitted)
    assert len(emitted) == len(DEFAULT_GAUGES) - 1
    assert strip.gauges() == emitted


def test_context_menu_remove_and_replace(strip):
    changed = QSignalSpy(strip.gauges_changed)
    replace = QSignalSpy(strip.replace_requested)
    menu = strip._context_menu(4)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Replace with…", "Remove"]
    menu.actions()[0].trigger()
    assert replace.count() == 1 and replace.at(0)[0] == 4
    assert changed.count() == 0
    strip.replace_gauge(4, SILVER)
    assert changed.count() == 1
    assert strip.gauges()[4] == SILVER
    strip._context_menu(4).actions()[-1].trigger()
    assert changed.count() == 2
    assert SILVER not in strip.gauges()


def test_plus_requests_add(strip):
    add = QSignalSpy(strip.add_requested)
    changed = QSignalSpy(strip.gauges_changed)
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     strip.plus_rect().center())
    assert add.count() == 1
    assert changed.count() == 0
    strip.add_gauge(SILVER)
    assert changed.count() == 1
    assert strip.gauges()[-1] == SILVER


def test_click_emits_gauge(strip):
    spy = QSignalSpy(strip.gauge_clicked)
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(strip, 3))
    assert spy.count() == 1
    assert spy.at(0)[0] == DEFAULT_GAUGES[3]
    # a tile with nothing to send still reports the click; the panel decides
    QTest.mouseClick(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(strip, 0))
    assert spy.count() == 2 and spy.at(1)[0].kind == "policy"


def test_press_and_release_on_different_tiles_is_not_a_click(strip):
    spy = QSignalSpy(strip.gauge_clicked)
    QTest.mousePress(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(strip, 3))
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       centre(strip, 4))
    assert spy.count() == 0


def _drag(strip, start: QPoint, end: QPoint, release: bool = True) -> None:
    QTest.mousePress(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    step = QApplication.startDragDistance() + 2
    QTest.mouseMove(strip, start + QPoint(step, 0))
    mid = (start + end) / 2
    QTest.mouseMove(strip, QPoint(int(mid.x()), int(mid.y())))
    QTest.mouseMove(strip, end)
    if release:
        QTest.mouseRelease(strip, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier, end)


def test_drag_reorders(strip):
    changed = QSignalSpy(strip.gauges_changed)
    clicked = QSignalSpy(strip.gauge_clicked)
    # drop VIX (4) onto the right half of Gold (7): it lands last
    target = strip.tile_rect(7)
    end = QPoint(target.right() - 4, target.center().y())
    _drag(strip, centre(strip, 4), end, release=False)
    assert strip.is_dragging()
    assert strip.drop_slot() == 8
    assert strip.drop_indicator_rect().isValid()
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert not strip.is_dragging()
    assert changed.count() == 1
    assert codes(strip.gauges())[-1] == "^VIX"
    assert codes(strip.gauges())[3:7] == ["DX-Y.NYB", "^MOVE", "CL=F", "GC=F"]
    assert clicked.count() == 0  # a drag is never a click


def test_drag_to_the_front(strip):
    changed = QSignalSpy(strip.gauges_changed)
    first = strip.tile_rect(0)
    _drag(strip, centre(strip, 6), QPoint(first.left() + 4, first.center().y()))
    assert changed.count() == 1
    assert codes(strip.gauges())[0] == "CL=F"


def test_drop_in_place_is_not_an_edit(strip):
    changed = QSignalSpy(strip.gauges_changed)
    r = strip.tile_rect(3)
    _drag(strip, centre(strip, 3), QPoint(r.left() + 6, r.center().y()))
    assert changed.count() == 0
    assert strip.gauges() == list(DEFAULT_GAUGES)


def test_escape_cancels_drag(strip):
    changed = QSignalSpy(strip.gauges_changed)
    end = centre(strip, 7)
    _drag(strip, centre(strip, 4), end, release=False)
    assert strip.is_dragging()
    QTest.keyClick(strip, Qt.Key.Key_Escape)
    assert not strip.is_dragging()
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert changed.count() == 0
    assert strip.gauges() == list(DEFAULT_GAUGES)


# -- readings ----------------------------------------------------------------------


def test_skeleton_until_first_reading(strip):
    assert strip.is_loading(3)
    strip.set_reading(3, "99.84", "+0.21", 1, [99.0, 99.5, 99.84], note="")
    assert not strip.is_loading(3)
    assert strip.reading(3).value == "99.84"


def test_readings_and_labels_survive_set_gauges(strip):
    strip.set_reading(7, "4,012.3", "+12.4", 1, [1.0, 2.0])
    strip.set_label(1, "UST 10Y")
    strip.set_gauges(list(reversed(DEFAULT_GAUGES)))
    assert strip.reading(0).value == "4,012.3"
    assert strip.display_label(6) == "UST 10Y"


def test_set_label_overrides_the_display_name(strip):
    strip.set_label(1, "Bund 10Y")
    assert strip.display_label(1) == "Bund 10Y"
    assert strip.gauges()[1].label == "10Y"  # the stored gauge keeps its own


def test_tooltip_explains_tiles_with_nothing_to_send(strip):
    strip.set_reading(2, "-0.41", "-2bp", -1, [0.1, -0.4], note="inverted")
    tip = strip.tooltip_at(centre(strip, 2))
    assert "2s10s" in tip and "inverted" in tip
    assert "nothing" in tip.lower()
    vix = strip.tooltip_at(centre(strip, 4))
    assert "VIX" in vix and "nothing" not in vix.lower()


def test_works_with_animations_off(strip, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    strip.remove_gauge(0)
    assert not strip.is_animating()
    strip.add_gauge(SILVER)
    assert not strip.is_animating()
    first = strip.tile_rect(0)
    _drag(strip, centre(strip, 5), QPoint(first.left() + 4, first.center().y()))
    assert not strip.is_animating()
    for i in range(len(strip.gauges())):
        assert strip.painted_rect(i) == strip.tile_rect(i)
    for i, g in enumerate(strip.gauges()):
        strip.set_reading(i, "1.00", "+0.01", 1, [1.0, 1.1, 0.9, 1.2])
    assert not strip.grab().isNull()


def test_edits_animate_when_animations_on(strip, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    strip.remove_gauge(0)
    assert strip.is_animating()
    # the logical state is already final; only the painting travels
    assert len(strip.gauges()) == len(DEFAULT_GAUGES) - 1
    strip.finish_animation()
    assert not strip.is_animating()
    for i in range(len(strip.gauges())):
        assert strip.painted_rect(i) == strip.tile_rect(i)


def test_gaps_in_the_history_are_skipped(strip):
    strip.set_reading(4, "17.6", "-0.8", -1, [17.0, None, "n/a", float("nan"), 17.6])
    assert strip.reading(4).spark == (17.0, 17.6)
    assert not strip.grab().isNull()


# -- review fixes ------------------------------------------------------------


def test_programmatic_edit_mid_drag_ends_the_drag(strip):
    changed = QSignalSpy(strip.gauges_changed)
    end = centre(strip, 7)
    _drag(strip, centre(strip, 4), end, release=False)
    assert strip.is_dragging()
    strip.remove_gauge(1)
    assert not strip.is_dragging()
    assert changed.count() == 1
    after = strip.gauges()
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert changed.count() == 1  # the stale drop does nothing
    assert strip.gauges() == after


@pytest.mark.parametrize("edit", ["add", "replace"])
def test_add_or_replace_mid_drag_ends_the_drag(strip, edit):
    _drag(strip, centre(strip, 4), centre(strip, 7), release=False)
    if edit == "add":
        strip.add_gauge(SILVER)
    else:
        strip.replace_gauge(0, SILVER)
    assert not strip.is_dragging()


def test_focus_loss_cancels_a_drag(strip):
    changed = QSignalSpy(strip.gauges_changed)
    end = centre(strip, 7)
    _drag(strip, centre(strip, 4), end, release=False)
    assert strip.is_dragging()
    QApplication.sendEvent(strip, QFocusEvent(QEvent.Type.FocusOut,
                                              Qt.FocusReason.ActiveWindowFocusReason))
    assert not strip.is_dragging()
    QTest.mouseRelease(strip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert changed.count() == 0


def test_window_deactivation_cancels_a_drag(strip, monkeypatch):
    _drag(strip, centre(strip, 4), centre(strip, 7), release=False)
    # offscreen there is no other window to switch to, so stand in for one
    monkeypatch.setattr(strip, "isActiveWindow", lambda: False)
    QApplication.sendEvent(strip, QEvent(QEvent.Type.ActivationChange))
    assert not strip.is_dragging()


def test_drop_resets_hover(strip):
    QTest.mouseMove(strip, centre(strip, 4))
    assert strip._hover == 4
    target = strip.tile_rect(7)
    _drag(strip, centre(strip, 4), QPoint(target.right() - 4, target.center().y()))
    assert strip._hover == -1


def test_strip_gets_taller_in_a_layout_when_narrow(qapp):
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    s = GaugeStrip()
    s.set_gauges(list(DEFAULT_GAUGES))
    lay.addWidget(s)
    lay.addStretch(1)
    host.resize(1100, 600)
    host.show()
    QApplication.processEvents()
    wide = s.height()
    assert wide == s.heightForWidth(s.width())
    host.resize(420, 600)
    QApplication.processEvents()
    assert s.height() > wide
    assert s.height() == s.heightForWidth(s.width())
    host.close()
    host.deleteLater()


def test_deleting_mid_animation_is_safe(qapp, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    s = GaugeStrip()
    s.set_gauges(list(DEFAULT_GAUGES))
    s.resize(1100, s.heightForWidth(1100))
    s.show()
    QApplication.processEvents()
    s.remove_gauge(0)
    assert s.is_animating()
    s.close()
    s.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QTest.qWait(motion.TILE_TRAVEL_MS + 60)  # the animation's ticks must not reach a dead widget


def test_note_tone_colours_state_notes(strip):
    strip.set_reading(2, "-0.41", "-2bp", -1, [0.1, -0.4], note="inverted", note_tone="down")
    assert strip.reading(2).note_tone == "down"
    assert strip.note_ink(2) == theme.DOWN
    strip.set_reading(1, "4.2", "+1bp", 1, [4.1, 4.2], note="as of Sep")
    assert strip.reading(1).note_tone == ""
    assert strip.note_ink(1) == theme.FG_DIM
    strip.set_reading(3, "99", "+0.1", 1, [1, 2], note="x", note_tone="loud")
    assert strip.reading(3).note_tone == ""  # unknown tones fall back to plain


# -- clearing and finding a tile ---------------------------------------------------


def test_clear_reading_puts_a_tile_back_to_its_skeleton(strip):
    strip.set_reading(1, "4.22%", "+1bp", 1, [4.1, 4.22])
    assert not strip.is_loading(1)
    strip.clear_reading(1)
    assert strip.is_loading(1)
    assert strip.reading(1) is None
    strip.clear_reading(99)  # out of range: ignored


def test_reveal_highlights_a_tile_then_lets_go(strip):
    assert strip.highlighted() == -1
    strip.reveal(2)
    assert strip.highlighted() == 2
    QTest.qWait(strip.REVEAL_HOLD_MS + 100)
    assert strip.highlighted() == -1


# -- click to expand ---------------------------------------------------------------


def _click(widget, pos: QPoint) -> None:
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def _settle(strip: GaugeStrip) -> None:
    strip.finish_animation()
    QApplication.processEvents()


def test_click_expands_and_click_again_collapses(strip):
    clicked = QSignalSpy(strip.gauge_clicked)
    changed = QSignalSpy(strip.expansion_changed)
    _click(strip, centre(strip, 3))
    _settle(strip)
    assert strip.expanded_index() == 3
    assert strip.expanded_gauge() == DEFAULT_GAUGES[3]
    assert clicked.count() == 1  # linking is kept: the click still goes out
    assert changed.count() == 1 and changed.at(0)[0] == DEFAULT_GAUGES[3]
    card = strip.card_rect()
    tile = strip.tile_rect(3)
    # its own full-width row, right below the tile's row
    assert card.left() == 0 and card.width() == strip.width()
    assert tile.bottom() < card.top() <= tile.bottom() + theme.SPACE_LG
    assert card.height() >= theme.GAUGE_CARD_PX
    assert strip.heightForWidth(strip.width()) >= card.bottom() + 1
    assert strip.card().isVisibleTo(strip)
    # the same tile again shrinks it back
    _click(strip, centre(strip, 3))
    _settle(strip)
    assert strip.expanded_index() == -1
    assert changed.count() == 2 and changed.at(1)[0] is None
    assert strip.card_rect().height() == 0
    assert strip.heightForWidth(strip.width()) == strip.tile_rect(0).height()
    # and a click on the card itself, away from its controls, does too
    _click(strip, centre(strip, 3))
    _settle(strip)
    card = strip.card()
    _click(card, QPoint(theme.SPACE_SM, theme.SPACE_SM))
    _settle(strip)
    assert strip.expanded_index() == -1


def test_card_opens_under_its_row_and_later_rows_move_down(strip):
    strip.resize(420, strip.heightForWidth(420))
    _settle(strip)
    assert strip.row_count() > 1
    first_row_bottom = strip.tile_rect(0).bottom()
    below = next(i for i in range(len(DEFAULT_GAUGES)) if strip.tile_rect(i).top() > first_row_bottom)
    before = strip.tile_rect(below).top()
    _click(strip, centre(strip, 0))
    _settle(strip)
    card = strip.card_rect()
    assert card.top() > first_row_bottom
    # nothing is hidden: the next row now sits under the card, one gap below
    top = strip.tile_rect(below).top()
    assert top > card.bottom()
    assert top - (card.bottom() + 1) == before - (first_row_bottom + 1)
    assert len(strip.gauges()) == len(DEFAULT_GAUGES)


def test_only_one_expanded(strip):
    _click(strip, centre(strip, 3))
    _click(strip, centre(strip, 5))
    _settle(strip)
    assert strip.expanded_index() == 5
    assert len([w for w in strip.findChildren(type(strip.card())) if w.isVisibleTo(strip)]) == 1


def test_esc_collapses_only_with_focus(strip):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QLineEdit

    # nothing open: the strip lets Escape through for whoever wants it
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    override.ignore()
    QApplication.sendEvent(strip, override)
    assert not override.isAccepted()

    _click(strip, centre(strip, 4))
    _settle(strip)
    # open: Escape is claimed before a window shortcut (un-maximise) takes it
    override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    override.ignore()
    QApplication.sendEvent(strip, override)
    assert override.isAccepted()
    # Escape somewhere else leaves it open
    other = QLineEdit()
    other.show()
    QTest.keyClick(other, Qt.Key.Key_Escape)
    assert strip.expanded_index() == 4
    other.close()
    other.deleteLater()
    # on the strip it closes the card
    QTest.keyClick(strip, Qt.Key.Key_Escape)
    _settle(strip)
    assert strip.expanded_index() == -1
    # and from inside the card (a range chip has focus after a click)
    _click(strip, centre(strip, 4))
    _settle(strip)
    QTest.keyClick(strip.card().chip("1Y"), Qt.Key.Key_Escape)
    _settle(strip)
    assert strip.expanded_index() == -1


def test_drag_does_not_expand(strip):
    changed = QSignalSpy(strip.expansion_changed)
    target = strip.tile_rect(7)
    _drag(strip, centre(strip, 4), QPoint(target.right() - 4, target.center().y()))
    _settle(strip)
    assert strip.expanded_index() == -1
    assert changed.count() == 0
    # nor does a drop in place (the press-release pair is not a click)
    r = strip.tile_rect(3)
    _drag(strip, centre(strip, 3), QPoint(r.left() + 6, r.center().y()))
    _settle(strip)
    assert strip.expanded_index() == -1


def test_remove_expanded_collapses(strip):
    changed = QSignalSpy(strip.expansion_changed)
    _click(strip, centre(strip, 3))
    _settle(strip)
    strip.remove_gauge(3)
    _settle(strip)
    assert strip.expanded_index() == -1
    assert changed.count() == 2 and changed.at(1)[0] is None
    assert not strip.card().isVisibleTo(strip)
    assert strip.heightForWidth(strip.width()) == strip.tile_rect(0).height()
    # removing another tile keeps the open one open, under its new index
    _click(strip, centre(strip, 5))
    _settle(strip)
    gauge = strip.expanded_gauge()
    strip.remove_gauge(0)
    _settle(strip)
    assert strip.expanded_gauge() == gauge and strip.expanded_index() == 4
    # an undo (the panel restoring a list without it) closes it too
    strip.set_gauges([g for g in strip.gauges() if g != gauge])
    _settle(strip)
    assert strip.expanded_index() == -1


def test_reorder_keeps_expansion(strip):
    vix = DEFAULT_GAUGES[4]
    _click(strip, centre(strip, 4))
    _settle(strip)
    target = strip.tile_rect(7)
    _drag(strip, centre(strip, 4), QPoint(target.right() - 4, target.center().y()))
    _settle(strip)
    assert codes(strip.gauges())[-1] == "^VIX"
    assert strip.expanded_gauge() == vix and strip.expanded_index() == 7
    # the context menu's Remove still works with a card open
    menu = strip._context_menu(0)
    next(a for a in menu.actions() if a.text() == "Remove").trigger()
    assert strip.expanded_gauge() == vix


def test_range_choice_is_remembered_per_gauge(strip):
    picked = QSignalSpy(strip.detail_range_changed)
    _click(strip, centre(strip, 4))
    assert strip.detail_range() == "1M"  # the default
    strip.card().chip("1Y").click()
    assert picked.count() == 1 and picked.at(0)[0] == "1Y"
    assert strip.detail_range() == "1Y"
    _click(strip, centre(strip, 5))
    assert strip.detail_range() == "1M"
    _click(strip, centre(strip, 4))
    assert strip.detail_range() == "1Y"


def test_card_heading_follows_the_tile(strip):
    _click(strip, centre(strip, 4))
    card = strip.card()
    assert card.is_loading()
    strip.set_reading(4, "17.40", "+2.96%", 1, [16.0, 17.4])
    assert card.value_text() == "17.40" and card.change_text() == "+2.96%"
    strip.set_label(4, "VIX index")
    assert card.label_text() == "VIX index"
    strip.clear_reading(4)
    assert card.value_text() == "…"


def test_card_shows_high_low_and_snaps_its_hover(strip):
    from datetime import date

    from aurantium.components.gauge_card import GaugeDetail

    _click(strip, centre(strip, 4))
    _settle(strip)
    series = ((date(2026, 10, 6), 18.0), (date(2026, 10, 20), 21.5),
              (date(2026, 11, 3), 15.25), (date(2026, 11, 5), 17.4))
    strip.set_detail(GaugeDetail(series=series, fmt=lambda v: f"{v:.2f}",
                                 source="Yahoo · 5 Nov", range="1M"))
    card = strip.card()
    assert not card.is_loading()
    assert card.high_text() == "High 21.50 · 20 Oct"
    assert card.low_text() == "Low 15.25 · 3 Nov"
    assert card.source_text() == "Yahoo · 5 Nov"
    assert card.chip("1M").isChecked()
    # the readout snaps to the nearest day that has a value
    assert card.hover_text_near(date(2026, 10, 19)) == "20 Oct 2026  21.50"
    # a chip with nothing to show says why and can't be picked
    strip.set_detail(GaugeDetail(series=series, fmt=str, range="1M",
                                 reasons={"5Y": "History goes back about a year"}))
    assert not card.chip("5Y").isEnabled()
    assert "about a year" in card.chip("5Y").toolTip()
    # no series: a message stands in for the chart
    strip.set_detail(GaugeDetail(message="BIS publishes the latest rate only", range="1M"))
    assert card.message() == "BIS publishes the latest rate only"
    assert card.high_text() == "" and card.low_text() == ""


def test_expansion_motion_follows_the_animation_setting(strip, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    _click(strip, centre(strip, 2))
    assert not strip.is_animating()
    assert strip.card_rect().height() >= theme.GAUGE_CARD_PX
    _click(strip, centre(strip, 2))
    assert not strip.is_animating() and strip.card_rect().height() == 0
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    _click(strip, centre(strip, 2))
    assert strip.is_animating()
    assert strip.expanded_index() == 2  # the state is final at once; only the drawing travels
    QTest.qWait(motion.CARD_GROW_MS + 150)
    assert not strip.is_animating()
    assert strip.card_rect().height() >= theme.GAUGE_CARD_PX


def _second_row_tile(strip) -> int:
    return next(i for i in range(len(strip.gauges())) if strip.tile_rect(i).top() > strip.tile_rect(0).bottom())


def test_switching_rows_closes_the_old_card_over_its_last_frame(strip, monkeypatch):
    strip.resize(420, strip.heightForWidth(420))
    _settle(strip)
    lower = _second_row_tile(strip)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    strip.expand(0)
    _settle(strip)
    old = strip.card_rect()
    strip.expand(lower)
    assert strip.is_animating()
    # the old row's opening is never an empty band: it closes over a still
    # of the card it held, from where that card was
    closing = strip.closing_card_rect()
    assert closing.isValid() and closing.top() == old.top()
    strip.finish_animation()
    assert not strip.closing_card_rect().isValid()
    assert strip.card_rect().top() > strip.tile_rect(lower).bottom()
    # with animations off the switch is instant, with nothing left closing
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    strip.expand(0)
    assert not strip.is_animating() and not strip.closing_card_rect().isValid()
    assert strip.card_rect().top() > strip.tile_rect(0).bottom()


def test_a_reorder_glides_the_open_card_with_its_tile(strip, monkeypatch):
    strip.resize(420, strip.heightForWidth(420))
    _settle(strip)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    strip.expand(0)
    _settle(strip)
    was = strip.painted_card_rect()
    strip.move_gauge(0, len(strip.gauges()))  # to the last row
    assert strip.expanded_index() == len(strip.gauges()) - 1
    target = strip.card_rect()
    assert target.top() != was.top()
    # the card starts where it was drawn, not at its new slot …
    assert strip.painted_card_rect().top() == was.top()
    strip.finish_animation()
    # … and lands there with the tiles
    assert strip.painted_card_rect() == strip.card_rect()
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    strip.move_gauge(len(strip.gauges()) - 1, 0)
    assert strip.painted_card_rect() == strip.card_rect()
    assert strip.card_rect().top() > strip.tile_rect(0).bottom()


@pytest.mark.parametrize("animated", [False, True])
def test_opening_scrolls_the_card_into_view(qapp, monkeypatch, animated):
    from PySide6.QtWidgets import QScrollArea

    monkeypatch.setattr(motion, "animations_enabled", lambda: animated)
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    s = GaugeStrip(body)
    s.set_gauges(list(DEFAULT_GAUGES))
    lay.addWidget(s)
    filler = QWidget(body)
    filler.setMinimumHeight(600)
    lay.addWidget(filler)
    area.setWidget(body)
    area.resize(420, 360)  # room for a tile and its card, not the whole strip
    area.show()
    QApplication.processEvents()
    last = len(DEFAULT_GAUGES) - 1  # on the bottom row
    s.expand(last)
    QTest.qWait(motion.CARD_GROW_MS + 150 if animated else 30)
    QApplication.processEvents()
    view = area.viewport()
    card = s.card_rect()
    top = s.mapTo(view, s.tile_rect(last).topLeft()).y()
    bottom = s.mapTo(view, card.bottomLeft()).y()
    assert card.height() >= theme.GAUGE_CARD_PX
    assert area.verticalScrollBar().value() > 0  # it had to scroll for it
    assert 0 <= top and bottom < view.height()
    area.close()
    area.deleteLater()


def test_close_hint_is_not_offered_over_the_chart(strip):
    from datetime import date

    from aurantium.components.gauge_card import CLOSE_HINT, GaugeDetail

    strip.expand(4)
    _settle(strip)
    card = strip.card()
    card.set_detail(GaugeDetail(series=((date(2026, 10, 1), 1.0), (date(2026, 11, 1), 2.0))))
    QApplication.processEvents()
    assert card.tooltip_at(QPoint(theme.SPACE_SM, theme.SPACE_SM)) == CLOSE_HINT
    on_chart = card.plot.geometry().center()
    assert card.tooltip_at(card.plot.parentWidget().mapTo(card, on_chart)) == ""


# -- the card's date axis -------------------------------------------------------------


def _daily(days: int, step: int = 1):
    from datetime import date, timedelta

    end = date(2026, 11, 5)
    return tuple((end - timedelta(days=k), 100.0 + k % 9) for k in range(days, -1, -step))


@pytest.mark.parametrize("width", [260, 900])
@pytest.mark.parametrize("span, step, pattern", [
    ("1M", 1, r"^\d{1,2} [A-Z][a-z]{2}$"),   # "3 Nov"
    ("5Y", 7, r"^\d{4}$"),                    # "2024"
])
def test_date_ticks_never_overlap_and_suit_the_range(qapp, width, span, step, pattern):
    import re

    from aurantium.components.gauge_card import GaugeCard, GaugeDetail

    card = GaugeCard()
    days = 31 if span == "1M" else 5 * 366
    card.set_detail(GaugeDetail(series=_daily(days, step), range=span))
    card.resize(width, theme.GAUGE_CARD_PX)
    card.show()
    for _ in range(3):
        QApplication.processEvents()
    axis = card.plot.getAxis("bottom")
    labels = axis.tick_labels()
    assert len(labels) >= 2, labels
    for _x, text in labels:
        assert re.match(pattern, text), text
    # each label's box, centred on its tick, clear of the next one
    boxes = sorted((x - axis.label_width(t) / 2, x + axis.label_width(t) / 2) for x, t in labels)
    for (_a0, a1), (b0, _b1) in zip(boxes, boxes[1:]):
        assert a1 + theme.SPACE_SM <= b0, labels
    # and none overhangs either end of the axis
    axis_w = axis.geometry().width()
    assert boxes[0][0] >= 0 and boxes[-1][1] <= axis_w, (labels, axis_w)
    # never a label on every day
    if span == "1M":
        assert len(labels) <= 5
    card.close()
    card.deleteLater()


def test_a_year_reads_by_month_and_marks_january_with_its_year(qapp):
    from aurantium.components.gauge_card import GaugeCard, GaugeDetail

    card = GaugeCard()
    card.set_detail(GaugeDetail(series=_daily(366), range="1Y"))
    card.resize(900, theme.GAUGE_CARD_PX)
    card.show()
    for _ in range(3):
        QApplication.processEvents()
    texts = [t for _x, t in card.plot.getAxis("bottom").tick_labels()]
    assert "2026" in texts and any(t in texts for t in ("Feb", "Mar", "Apr", "Jul"))
    card.close()
    card.deleteLater()


def test_a_single_reading_still_names_its_day(qapp):
    from datetime import date

    from aurantium.components.gauge_card import GaugeCard, GaugeDetail

    card = GaugeCard()
    card.set_detail(GaugeDetail(series=((date(2026, 11, 5), 4.22),), range="1M"))
    card.resize(260, theme.GAUGE_CARD_PX)
    card.show()
    for _ in range(3):
        QApplication.processEvents()
    assert [t for _x, t in card.plot.getAxis("bottom").tick_labels()] == ["5 Nov"]
    card.close()
    card.deleteLater()


def test_removing_the_new_gauge_mid_switch_lets_the_old_still_go(strip, monkeypatch):
    strip.resize(420, strip.heightForWidth(420))
    _settle(strip)
    lower = _second_row_tile(strip)
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    strip.expand(0)
    _settle(strip)
    strip.expand(lower)
    assert strip.closing_card_rect().isValid()
    strip.remove_gauge(lower)
    assert strip.expanded_index() == -1
    assert strip._closing is None  # no full-size still kept around
    assert not strip.closing_card_rect().isValid()


def test_a_user_scroll_during_the_open_is_not_pulled_back(qapp, monkeypatch):
    from PySide6.QtWidgets import QScrollArea

    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    area = QScrollArea()
    area.setWidgetResizable(True)
    body = QWidget()
    lay = QVBoxLayout(body)
    lay.setContentsMargins(0, 0, 0, 0)
    s = GaugeStrip(body)
    s.set_gauges(list(DEFAULT_GAUGES))
    lay.addWidget(s)
    filler = QWidget(body)
    filler.setMinimumHeight(600)
    lay.addWidget(filler)
    area.setWidget(body)
    area.resize(420, 360)
    area.show()
    QApplication.processEvents()
    s.expand(len(DEFAULT_GAUGES) - 1)
    QTest.qWait(motion.CARD_GROW_MS // 3)
    assert s.is_animating()
    bar = area.verticalScrollBar()
    bar.setValue(0)  # the user wheels back to the top mid-open
    QTest.qWait(motion.CARD_GROW_MS + 150)
    QApplication.processEvents()
    assert not s.is_animating()
    assert bar.value() == 0
    # the next open follows again
    s.collapse()
    s.finish_animation()
    s.expand(len(DEFAULT_GAUGES) - 2)
    QTest.qWait(motion.CARD_GROW_MS + 150)
    QApplication.processEvents()
    assert bar.value() > 0
    area.close()
    area.deleteLater()


def test_card_chart_lines_are_antialiased(qapp):
    # without it pyqtgraph draws stair-stepped lines (pyqtgraph's own default is off)
    from aurantium.components.gauge_card import GaugeCard

    card = GaugeCard()
    assert card._line.opts["antialias"]
    assert card._last.opts["antialias"] and card._dot.opts["antialias"]
