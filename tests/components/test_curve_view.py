"""The Macro panel's yield-curve view: one country's whole curve, comparable.

The view draws the current curve on a square-root maturity axis labelled with
tenors, faint lines for the 1W / 1M / 1Y-ago comparisons the user toggles with
chips, and a twist strip of bp changes per tenor underneath. It knows nothing
about topics: the panel feeds ``set_curve`` / ``set_history`` and decides the
state. Setters never emit; only the user's clicks do.
"""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from aurantium import theme
from aurantium.components.curve_view import (
    MSG_NO_HISTORY,
    MSG_NO_HISTORY_THAT_FAR,
    MSG_POLICY_ONLY,
    CurveView,
)
from aurantium.macro_model import axis_x, bp_changes, tenor_label

US_NOW = [
    [0.0833, 4.52], [0.25, 4.48], [0.5, 4.40], [1.0, 4.28], [2.0, 4.31],
    [3.0, 4.18], [5.0, 4.09], [7.0, 4.12], [10.0, 4.22], [20.0, 4.55], [30.0, 4.49],
]


def shifted(points, bp):
    return [[y, round(r + bp / 100.0, 4)] for y, r in points]


US_1W = shifted(US_NOW, -3)
US_1M = shifted(US_NOW, -12)
US_1Y = shifted(US_NOW, +35)
US_PREV = shifted(US_NOW, -1)

US_DAYS = [
    ["2025-11-03", US_1Y],
    ["2026-10-05", US_1M],
    ["2026-10-29", US_1W],
    ["2026-11-04", US_PREV],
    ["2026-11-05", US_NOW],
]


@pytest.fixture
def view(qapp):
    v = CurveView()
    v.set_countries([("US", "United States"), ("JP", "Japan"), ("CN", "China")], "US")
    v.resize(900, 440)
    v.show()
    QApplication.processEvents()
    yield v
    v.close()
    v.deleteLater()


def load_us(v, comparisons=("1M",)):
    v.set_comparisons(list(comparisons))
    v.set_curve(US_NOW, True, "2026-11-05", "U.S. Treasury")
    v.set_history(US_DAYS)
    QApplication.processEvents()


def ticks_of(v):
    """(x, text) for every tenor tick on the shared maturity axis."""
    return v.tick_labels()


# -- axis --------------------------------------------------------------------


def test_tenor_ticks(view):
    load_us(view)
    xs, ys = view.curve_item.getData()
    assert list(xs) == pytest.approx([axis_x(y) for y, _ in US_NOW])
    assert list(ys) == pytest.approx([r for _, r in US_NOW])
    ticks = ticks_of(view)
    assert [x for x, _ in ticks] == pytest.approx([axis_x(y) for y, _ in US_NOW])
    # wide enough: every observed tenor carries its label
    assert [t for _, t in ticks] == [tenor_label(y) for y, _ in US_NOW]


def test_tenor_ticks_merge_comparison_tenors(view):
    view.set_comparisons(["1M"])
    now = [[2.0, 1.0], [10.0, 1.5]]
    then = [[1.0, 0.9], [2.0, 0.95], [10.0, 1.4]]
    view.set_curve(now, True, "2026-11-05", "Japan MOF")
    view.set_history([["2026-10-01", then], ["2026-11-05", now]])
    labels = [t for _, t in ticks_of(view)]
    assert labels == ["1Y", "2Y", "10Y"]


def test_narrow_width_thins_labels_but_keeps_key_tenors(view):
    load_us(view)
    view.resize(420, 360)
    QApplication.processEvents()
    labelled = [t for _, t in ticks_of(view) if t]
    assert "2Y" in labelled and "10Y" in labelled and "30Y" in labelled
    assert len(labelled) < len(US_NOW)
    # every tenor still has its tick (grid line), labelled or not
    assert len(ticks_of(view)) == len(US_NOW)


# -- comparisons ---------------------------------------------------------------


def test_comparison_line_drawn_per_chip(view):
    load_us(view, comparisons=())
    assert view.comparison_items == {}
    spy = QSignalSpy(view.comparisons_changed)
    view.chip("1M").click()
    view.chip("1Y").click()
    assert spy.count() == 2
    assert spy.at(1)[0] == ["1M", "1Y"]
    assert set(view.comparison_items) == {"1M", "1Y"}
    _, ys = view.comparison_items["1Y"].getData()
    assert list(ys) == pytest.approx([r for _, r in US_1Y])
    view.chip("1M").click()
    assert set(view.comparison_items) == {"1Y"}


def test_setters_never_emit(view):
    spy_c = QSignalSpy(view.comparisons_changed)
    spy_k = QSignalSpy(view.country_changed)
    load_us(view, comparisons=("1W", "1Y"))
    view.set_countries([("US", "United States"), ("JP", "Japan")], "JP")
    assert spy_c.count() == 0 and spy_k.count() == 0
    assert view.comparisons() == ["1W", "1Y"]


def test_default_comparison_is_one_month(qapp):
    v = CurveView()
    try:
        assert v.comparisons() == ["1M"]
    finally:
        v.deleteLater()


def test_country_pick_emits(view):
    spy = QSignalSpy(view.country_changed)
    view.pick_country("JP")
    assert spy.count() == 1 and spy.at(0)[0] == "JP"
    assert view.country() == "JP"


def test_country_list_marks_policy_only_and_partial(qapp):
    v = CurveView()
    try:
        v.set_countries([("US", "United States"), ("GB", "United Kingdom"), ("CN", "China")], "US")
        assert v.country_menu_texts() == [
            "United States",
            "United Kingdom · partial",
            "China · policy rate only",
        ]
        assert v.country_button_text() == "United States"
    finally:
        v.deleteLater()


# -- twist strip ---------------------------------------------------------------


def test_twist_bars_match_bp_changes(view):
    load_us(view, comparisons=("1M", "1Y"))
    expected = bp_changes(US_NOW, US_1M)  # the first selected comparison
    opts = view.twist_bars.opts
    assert list(opts["x"]) == pytest.approx([axis_x(y) for y, _ in expected])
    assert list(opts["height"]) == pytest.approx([bp for _, bp in expected])
    assert view.twist_label_text() == "Δ vs 1M (bp)"
    brushes = opts["brushes"]
    assert all(b.color().name() == theme.UP for b in brushes)  # all +12 bp


def test_twist_falls_back_to_previous_day(view):
    load_us(view, comparisons=())
    expected = bp_changes(US_NOW, US_PREV)
    assert list(view.twist_bars.opts["height"]) == pytest.approx([bp for _, bp in expected])
    assert view.twist_label_text() == "Δ vs prior day (bp)"


def test_twist_down_bars_use_down_token(view):
    load_us(view, comparisons=("1Y",))
    brushes = view.twist_bars.opts["brushes"]
    assert all(b.color().name() == theme.DOWN for b in brushes)  # all -35 bp


# -- inversion, sparse, none ---------------------------------------------------------


def test_inverted_segment_drawn_in_down(view):
    load_us(view)  # 2Y 4.31 > 10Y 4.22
    assert view.inversion_item.isVisible()
    xs, _ = view.inversion_item.getData()
    assert xs[0] == pytest.approx(axis_x(2.0)) and xs[-1] == pytest.approx(axis_x(10.0))
    assert view.inversion_item.opts["pen"].color().name() == theme.DOWN


def test_no_inversion_segment_when_normal(view):
    normal = [[2.0, 3.5], [5.0, 3.8], [10.0, 4.1]]
    view.set_curve(normal, True, "2026-11-05", "U.S. Treasury")
    assert not view.inversion_item.isVisible()


def test_sparse_curve_is_dashed_and_labelled(view):
    uk = [[0.25, 4.71], [10.0, 4.45]]
    view.set_curve(uk, False, "2026-10-01", "FRED")
    QApplication.processEvents()
    assert view.state() == "partial"
    assert view.curve_item.opts["pen"].style() == Qt.PenStyle.DashLine
    assert view.curve_item.opts["symbol"] is not None
    assert view.partial_text() == "partial — 2 points"
    assert view.partial_label.isVisible()
    # a full curve is solid and unlabelled
    view.set_curve(US_NOW, True, "2026-11-05", "U.S. Treasury")
    assert view.curve_item.opts["pen"].style() == Qt.PenStyle.SolidLine
    assert not view.partial_label.isVisible()


def test_none_state_shows_policy_rate_message(view):
    view.set_state("none")
    QApplication.processEvents()
    assert view.state() == "none"
    assert view.message() == MSG_POLICY_ONLY
    assert MSG_POLICY_ONLY == "Policy rate only — no free yield curve for this country"
    assert not view.plots_visible()


def test_error_state_shows_message(view):
    view.set_state("error", "Treasury did not answer")
    assert view.message() == "Treasury did not answer"
    assert not view.plots_visible()


def test_header_cites_source_and_its_own_date(view):
    load_us(view)
    assert view.source_text() == "U.S. Treasury · 2026-11-05"


# -- chips without history -------------------------------------------------------------


def test_chips_disabled_without_history(view):
    view.set_comparisons(["1M"])
    view.set_curve(US_NOW, True, "2026-11-05", "Japan MOF")
    view.set_history([["2026-11-05", US_NOW]])
    for chip in ("1W", "1M", "1Y"):
        assert not view.chip(chip).isEnabled()
        assert view.chip(chip).toolTip() == MSG_NO_HISTORY
    assert view.comparison_items == {}


def test_chip_disabled_when_history_too_short(view):
    view.set_comparisons(["1M", "1Y"])
    view.set_curve(US_NOW, True, "2026-11-05", "ECB")
    view.set_history([["2026-09-01", US_1M], ["2026-11-05", US_NOW]])
    assert view.chip("1M").isEnabled()
    assert not view.chip("1Y").isEnabled()
    assert view.chip("1Y").toolTip() == MSG_NO_HISTORY_THAT_FAR
    assert not view.chip("1Y").isChecked()  # not lit while it draws nothing
    assert set(view.comparison_items) == {"1M"}
    # clicking another chip doesn't drop the unavailable one from the choice
    view.chip("1W").click()
    assert view.comparisons() == ["1W", "1M", "1Y"]
    # the preference survives: richer history later brings 1Y back
    view.set_history(US_DAYS)
    assert view.chip("1Y").isEnabled() and view.chip("1Y").isChecked()
    assert set(view.comparison_items) == {"1W", "1M", "1Y"}


# -- hover -----------------------------------------------------------------------


def test_hover_text(view):
    load_us(view, comparisons=("1M", "1Y"))
    assert view.hover_text(10.0) == "10Y  4.220%  Δ1M +12.0bp  Δ1Y -35.0bp"
    # snaps to the nearest observed tenor on the maturity axis
    assert view.hover_text(9.1).startswith("10Y  4.220%")
    assert view.hover_text(0.3).startswith("3M  4.480%")
    view.chip("1M").click()
    view.chip("1Y").click()
    assert view.hover_text(2.0) == "2Y  4.310%"


def test_hover_text_empty_without_curve(qapp):
    v = CurveView()
    try:
        assert v.hover_text(10.0) == ""
    finally:
        v.deleteLater()


def test_new_comparison_lands_fully_drawn(view):
    """A chip switched on fades its line in (animations on) or shows it at
    once (off); either way it ends fully opaque."""
    from PySide6.QtTest import QTest

    from aurantium import motion

    load_us(view, comparisons=())
    view.chip("1Y").click()
    QTest.qWait(motion.OVERLAY_MS + 120)
    assert view.comparison_items["1Y"].opacity() == pytest.approx(1.0)


def test_switching_country_redraws_whole(view):
    load_us(view)
    view.pick_country("JP")
    jp = [[1.0, 0.62], [2.0, 0.74], [10.0, 1.42], [40.0, 2.86]]
    view.set_curve(jp, True, "2026-11-05", "Japan MOF")
    view.set_history([["2026-11-05", jp]])
    from PySide6.QtTest import QTest

    from aurantium import motion

    QTest.qWait(motion.OVERLAY_MS + 120)
    xs, _ = view.curve_item.getData()
    assert list(xs) == pytest.approx([axis_x(y) for y, _ in jp])
    assert view.comparison_items == {}
    assert view.source_text() == "Japan MOF · 2026-11-05"
