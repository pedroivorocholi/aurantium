"""The Macro panel's gauge strip: compact tiles edited in place.

The strip is one painted widget (like the Heatmap's map): tiles wrap onto more
rows as it narrows, a trailing **+** tile asks for a new gauge, a right-click
replaces or removes one, and a drag reorders them. Every *user* edit emits
``gauges_changed`` so the panel can push an undo step; ``set_gauges`` (the
panel restoring or undoing) never does.
"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

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
