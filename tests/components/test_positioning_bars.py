"""The Macro panel's positioning bars: one row per CFTC market.

Each row is a diverging bar of large speculators' net position (right and in
the gain colour when net long, left and in the loss colour when net short),
the week-over-week change, and a marker for where the reading sits in its own
~2-year range — hidden when the market has too little history for one.
Editing mirrors the gauge strip: a trailing **+** row asks for a market, a
right-click removes one, a drag reorders them. User edits emit
``markets_changed``; ``set_rows`` (the panel restoring or undoing) never does.
"""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from aurantium import motion, theme
from aurantium.components.positioning_bars import PositioningBars

ROWS = [
    ("gold", "Gold", 201_579.0, 12_340.0, 87.0),
    ("crude_oil", "WTI Crude", -45_200.0, -3_100.0, 12.0),
    ("sp500", "S&P 500 (E-mini)", 98_000.0, 0.0, 55.0),
    ("bitcoin", "Bitcoin", -1_250.0, 410.0, None),   # too little history
    ("copper", "Copper", 15_400.0, -2_050.0, 100.0),
]


@pytest.fixture
def bars(qapp):
    b = PositioningBars()
    b.set_rows(ROWS)
    b.resize(560, b.sizeHint().height())
    b.show()
    QApplication.processEvents()
    yield b
    b.close()
    b.deleteLater()


def centre(bars: PositioningBars, index: int) -> QPoint:
    return bars.row_rect(index).center()


def _click(bars, pos: QPoint) -> None:
    QTest.mouseClick(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)


def _drag(bars, start: QPoint, end: QPoint, release: bool = True) -> None:
    QTest.mousePress(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    step = QApplication.startDragDistance() + 2
    QTest.mouseMove(bars, start + QPoint(0, step))
    mid = (start + end) / 2
    QTest.mouseMove(bars, QPoint(int(mid.x()), int(mid.y())))
    QTest.mouseMove(bars, end)
    if release:
        QTest.mouseRelease(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)


# -- bars ----------------------------------------------------------------------


def test_bar_direction_follows_sign(bars):
    zero = bars.zero_x()
    long_bar, short_bar = bars.bar_rect(0), bars.bar_rect(1)
    # net long grows right of the zero line, in the gain colour
    assert long_bar.left() == pytest.approx(zero, abs=1)
    assert long_bar.right() > zero + 10
    assert bars.bar_color(0) == theme.UP
    # net short grows left of it, in the loss colour
    assert short_bar.right() == pytest.approx(zero, abs=1)
    assert short_bar.left() < zero - 10
    assert bars.bar_color(1) == theme.DOWN
    # every bar shares one scale: the largest position gets the longest bar
    assert long_bar.width() > bars.bar_rect(2).width() > bars.bar_rect(4).width()
    assert long_bar.width() > short_bar.width()
    track = bars.bar_track(0)
    assert long_bar.right() <= track.right() + 1
    assert zero == pytest.approx(track.center().x(), abs=1)


def test_percentile_marker_hidden_without_history(bars):
    assert bars.marker_rect(3).isNull()          # no percentile: no marker
    assert not bars.bar_rect(3).isNull()          # but the bar is still drawn
    track = bars.percentile_track(0)
    lo, mid, hi = bars.marker_rect(1), bars.marker_rect(2), bars.marker_rect(4)
    assert not lo.isNull() and not mid.isNull() and not hi.isNull()
    # 0 = most short (left end), 100 = most long (right end)
    assert lo.center().x() < mid.center().x() < hi.center().x()
    assert hi.center().x() == pytest.approx(track.right(), abs=2)
    assert "history" in bars.tooltip_at(centre(bars, 3)).lower()
    assert "87th" in bars.tooltip_at(centre(bars, 0))


def test_week_over_week_delta_is_signed(bars):
    assert bars.wow_text(0) == "+12.3K"
    assert bars.wow_text(1) == "-3.1K"
    assert bars.wow_color(0) == theme.UP
    assert bars.wow_color(1) == theme.DOWN
    assert bars.wow_color(2) == theme.FG_DIM
    assert bars.net_text(0) == "+201.6K"
    assert bars.net_text(1) == "-45.2K"


def test_a_row_without_a_reading_shows_a_skeleton(bars):
    bars.set_rows([("gold", "Gold", None, None, None)])
    assert bars.is_loading(0)
    assert bars.bar_rect(0).isNull()
    assert not bars.grab().isNull()


# -- clicks and edits -------------------------------------------------------------


def test_click_emits_market(bars):
    spy = QSignalSpy(bars.market_clicked)
    _click(bars, centre(bars, 1))
    assert spy.count() == 1
    assert spy.at(0)[0] == "crude_oil"


def test_press_and_release_on_different_rows_is_not_a_click(bars):
    spy = QSignalSpy(bars.market_clicked)
    QTest.mousePress(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     centre(bars, 0))
    QTest.mouseRelease(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       centre(bars, 1))
    assert spy.count() == 0


def test_set_rows_never_emits(bars):
    spy = QSignalSpy(bars.markets_changed)
    bars.set_rows(list(reversed(ROWS)))
    assert spy.count() == 0
    assert bars.markets()[0] == "copper"


def test_plus_requests_add(bars):
    add = QSignalSpy(bars.add_requested)
    changed = QSignalSpy(bars.markets_changed)
    clicked = QSignalSpy(bars.market_clicked)
    _click(bars, bars.plus_rect().center())
    assert add.count() == 1
    assert changed.count() == 0 and clicked.count() == 0
    bars.add_row("silver", "Silver")
    assert changed.count() == 1
    assert changed.at(0)[0][-1] == "silver"
    assert bars.is_loading(len(ROWS))  # no reading yet


def test_context_menu_remove(bars):
    changed = QSignalSpy(bars.markets_changed)
    menu = bars._context_menu(2)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Remove"]
    menu.actions()[0].trigger()
    assert changed.count() == 1
    assert "sp500" not in changed.at(0)[0]
    assert bars.markets() == changed.at(0)[0]


def test_drag_reorders(bars):
    changed = QSignalSpy(bars.markets_changed)
    clicked = QSignalSpy(bars.market_clicked)
    target = bars.row_rect(4)
    end = QPoint(target.center().x(), target.bottom() - 2)
    _drag(bars, centre(bars, 0), end, release=False)
    assert bars.is_dragging()
    assert bars.drop_slot() == 5
    assert bars.drop_indicator_rect().isValid()
    QTest.mouseRelease(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert not bars.is_dragging()
    assert changed.count() == 1
    assert bars.markets() == ["crude_oil", "sp500", "bitcoin", "copper", "gold"]
    assert clicked.count() == 0


def test_drop_in_place_is_not_an_edit(bars):
    changed = QSignalSpy(bars.markets_changed)
    r = bars.row_rect(2)
    _drag(bars, centre(bars, 2), QPoint(r.center().x(), r.top() + 3))
    assert changed.count() == 0


def test_escape_cancels_drag(bars):
    changed = QSignalSpy(bars.markets_changed)
    end = centre(bars, 4)
    _drag(bars, centre(bars, 0), end, release=False)
    assert bars.is_dragging()
    QTest.keyClick(bars, Qt.Key.Key_Escape)
    assert not bars.is_dragging()
    QTest.mouseRelease(bars, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    assert changed.count() == 0
    assert bars.markets()[0] == "gold"


# -- size and motion -----------------------------------------------------------------


def test_height_follows_row_count(bars):
    tall = bars.sizeHint().height()
    bars.set_rows(ROWS[:2])
    assert bars.sizeHint().height() < tall
    assert bars.plus_rect().bottom() < bars.sizeHint().height()


def test_works_with_animations_off(bars, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    bars.remove_row(0)
    assert not bars.is_animating()
    bars.add_row("silver", "Silver")
    assert not bars.is_animating()
    _drag(bars, centre(bars, 4), QPoint(centre(bars, 0).x(), bars.row_rect(0).top() + 2))
    assert not bars.is_animating()
    assert bars.markets()[0] == "silver"
    for i in range(len(bars.markets())):
        assert bars.painted_rect(i) == bars.row_rect(i)
    assert not bars.grab().isNull()


def test_edits_animate_when_animations_on(bars, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    bars.remove_row(0)
    assert bars.is_animating()
    assert bars.markets()[0] == "crude_oil"  # the state is final at once
    bars.finish_animation()
    assert not bars.is_animating()
    for i in range(len(bars.markets())):
        assert bars.painted_rect(i) == bars.row_rect(i)
