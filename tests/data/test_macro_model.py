"""Pure maths behind the Macro / Rates panel: axis, lookups, spreads, percentile."""

from datetime import date

import pytest

from aurantium import macro_model as m

CURVE = [[0.25, 4.30], [2, 4.00], [5, 4.10], [10, 4.50], [30, 4.80]]


def test_axis_spreads_short_end():
    assert m.axis_x(4) == 2.0
    assert m.axis_x(0.25) == 0.5
    # linear spacing would give 1.75/20 = 0.09; sqrt gives the short end far more room
    assert (m.axis_x(2) - m.axis_x(0.25)) / (m.axis_x(30) - m.axis_x(10)) > 0.3


def test_rate_at_never_interpolates():
    assert m.rate_at(CURVE, 10) == 4.50
    assert m.rate_at(CURVE, 10.04) == 4.50
    assert m.rate_at(CURVE, 7) is None  # between 5y and 10y: no point, no guess
    assert m.rate_at([], 2) is None


def test_bp_changes():
    then = [[0.25, 4.40], [2, 3.90], [10, 4.50], [20, 4.7]]
    now = [[0.25, 4.30], [2, 4.00], [5, 4.10], [10, 4.50]]
    out = m.bp_changes(now, then)
    assert [y for y, _ in out] == [0.25, 2, 10]  # only shared tenors, sorted
    assert out[0][1] == pytest.approx(-10)
    assert out[1][1] == pytest.approx(10)
    assert out[2][1] == pytest.approx(0)
    assert m.bp_changes(now, []) == []


def test_spread_missing_leg_is_none_with_reason():
    assert m.spread_bp(CURVE, 2, 10) == pytest.approx(50)
    assert m.spread_reason(CURVE, 2, 10) is None
    assert m.spread_bp(CURVE, 3, 10) is None
    reason = m.spread_reason(CURVE, 3, 10)
    assert reason == m.SPREAD_REASON_MISSING.format(tenor="3Y")
    assert m.spread_reason(CURVE, 2, 20) == "This curve has no 20Y point"
    # first missing leg is the short one when both are absent
    assert m.spread_reason(CURVE, 3, 20) == "This curve has no 3Y point"


def test_tenor_label():
    assert [m.tenor_label(y) for y in (0.25, 2, 10, 0.5, 1)] == ["3M", "2Y", "10Y", "6M", "1Y"]


def test_spread_states():
    flat = [50.0] * 30
    assert m.spread_state(flat) == "normal"
    assert m.spread_state(flat[:-1] + [-1.0]) == "inverted"
    series = [10.0] * 10 + [20.0] * 20 + [30.0]
    assert series[-21] == 20.0 and m.spread_state(series) == "steepening"
    series = [60.0] * 10 + [50.0] * 20 + [40.0]
    assert m.spread_state(series) == "flattening"
    series = [60.0] * 10 + [50.0] * 20 + [45.0]
    assert m.spread_state(series) == "normal"
    # exactly 10 bp counts
    assert m.spread_state([0.0] * 20 + [10.0]) == "steepening"
    # short history: no trend
    assert m.spread_state([5.0, 40.0]) == "normal"
    assert m.spread_state([5.0, -40.0]) == "inverted"
    assert m.spread_state([0.0] * 20) == "normal"  # 20 entries: still short
    assert m.spread_state([]) == "normal"


def test_percentile_needs_enough_history():
    hist = list(range(1, 27))  # 26 values
    assert m.percentile(hist, 26) == 100.0
    assert m.percentile(hist, 0) == 0.0
    assert m.percentile(hist, 13) == 50.0
    assert m.percentile(hist, 1) == pytest.approx(3.8)
    assert m.percentile(hist[:25], 10) is None
    assert m.percentile(hist[:5], 3, min_len=5) == 60.0


def test_days_since():
    assert m.days_since("2026-11-01", date(2026, 11, 6)) == 5
    assert m.days_since("2026-11-06", date(2026, 11, 6)) == 0


def test_point_on_or_before():
    days = [["2026-10-28", [[2, 1.0]]], ["2026-10-30", [[2, 2.0]]], ["2026-11-02", [[2, 3.0]]]]
    assert m.point_on_or_before(days, "2026-10-30") == [[2, 2.0]]
    assert m.point_on_or_before(days, "2026-11-01") == [[2, 2.0]]  # weekend rolls back
    assert m.point_on_or_before(days, "2026-12-01") == [[2, 3.0]]
    assert m.point_on_or_before(days, "2026-10-01") is None
    assert m.point_on_or_before([], "2026-10-01") is None


def test_comparison_date():
    assert m.comparison_date("2026-11-06", "1W") == "2026-10-30"
    assert m.comparison_date("2026-11-06", "1M") == "2026-10-06"
    assert m.comparison_date("2026-11-06", "1Y") == "2025-11-06"
    assert m.comparison_date("2026-03-31", "1M") == "2026-02-28"  # clamped
    assert m.comparison_date("2024-02-29", "1Y") == "2023-02-28"
    assert m.comparison_date("2026-01-15", "1M") == "2025-12-15"
    with pytest.raises(ValueError):
        m.comparison_date("2026-11-06", "5Y")


def test_spread_rules_text():
    assert m.SPREAD_STATE_RULES == (
        "Inverted: below zero. Steepening: up 10 bp or more over 20 business days. "
        "Flattening: down 10 bp or more. Otherwise normal."
    )


def test_bond_maturities_label_to_one_decimal():
    assert [m.tenor_label(y) for y in (1.1554, 10.1574, 0.16, 2.0)] == ["1.2Y", "10.2Y", "2M", "2Y"]


def test_bond_by_bond_curve_reads_its_nearest_bond_never_interpolates():
    br = [[0.16, 13.58], [1.16, 14.36], [2.16, 14.94], [10.16, 15.21]]
    assert m.rate_at(br, 10) == 15.21 and m.rate_at(br, 2) == 14.94
    assert m.rate_at(br, 0.25) is None  # 2M is not 3M
    assert m.rate_at(br, 5) is None     # nothing near 5Y: no made-up point
    month_ago = [[0.24, 13.22], [1.24, 12.66], [2.24, 12.74], [10.24, 13.0]]
    assert [round(bp) for _, bp in m.bp_changes(br, month_ago)] == [170, 220, 221]  # 2M vs 3M stays apart


def test_standard_tenors_still_need_their_own_point():
    us = [[1 / 12, 4.0], [0.25, 4.1], [2, 4.2], [7, 4.4], [10, 4.5]]
    assert m.rate_at(us, 1 / 6) is None  # 1M and 3M do not stand in for 2M
    assert m.rate_at(us, 8) is None      # 7Y and 10Y do not stand in for 8Y
