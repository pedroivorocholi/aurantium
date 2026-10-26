"""Day Brief maths and metadata.

Everything here is Qt-free: the sums that produce the EXCESS line, the session
snapping, the sigma flagging behind the chart's EVENTS lane, and the two lookup
tables that decide what a stock is measured against.
"""

import pytest

from aurantium.app import _as_of_spec
from aurantium.panels._events_lane import (
    MARK_STYLE,
    build_marks,
    unusual_move_indices,
)
from aurantium.providers.dayinfo import (
    avg_volume,
    excess,
    pct_change,
    snap_to_session,
    verdict,
)
from aurantium.providers.news import (
    _date_tuple,
    _published_within,
    _within_newsapi_window,
)
from aurantium.sector_meta import (
    SECTOR_ETF,
    benchmark_for,
    sector_proxy_for,
    suffix_of,
)


# -- session maths --------------------------------------------------------


def test_pct_change():
    assert pct_change(100.0, 110.0) == pytest.approx(10.0)
    assert pct_change(100.0, 91.58) == pytest.approx(-8.42)


@pytest.mark.parametrize("prev,cur", [(None, 10.0), (10.0, None), (0.0, 10.0)])
def test_pct_change_refuses_impossible_inputs(prev, cur):
    assert pct_change(prev, cur) is None


def test_excess_is_stock_minus_benchmark():
    assert excess(-8.42, -0.31) == pytest.approx(-8.11)


def test_excess_is_none_when_either_side_is_missing():
    """A missing benchmark must omit the line, never silently read as zero --
    that would turn 'we don't know' into 'the market did nothing'."""
    assert excess(-8.42, None) is None
    assert excess(None, -0.31) is None


def test_verdict_reads_the_excess():
    assert verdict(-8.11, -8.42) == "stock-specific, not the market"
    assert verdict(-0.40, -8.42) == "moved with the market"
    assert verdict(-4.00, -8.42) == "partly the market, partly the stock"


def test_verdict_stays_quiet_on_a_flat_day():
    assert verdict(0.05, 0.20) == "a quiet session"
    assert verdict(None, -8.42) == ""


# -- session snapping -----------------------------------------------------

ROWS = [
    {"date": "2026-03-11", "c": 10.0, "v": 100},
    {"date": "2026-03-12", "c": 11.0, "v": 200},
    {"date": "2026-03-13", "c": 9.0, "v": 300},
]


def test_exact_session_does_not_report_a_snap():
    assert snap_to_session(ROWS, "2026-03-12") == (1, "")


def test_weekend_snaps_back_and_reports_it():
    """The panel prints snapped_from, so the user is told which day they are
    actually looking at rather than quietly shown a different one."""
    assert snap_to_session(ROWS, "2026-03-14") == (2, "2026-03-14")


def test_date_before_any_session_yields_nothing():
    assert snap_to_session(ROWS, "2019-01-01") == (None, "")


def test_empty_history_yields_nothing():
    assert snap_to_session([], "2026-03-12") == (None, "")


def test_avg_volume_excludes_the_anchor_day():
    """Including a 4x volume day in the average it is measured against would
    blunt the very signal the ratio exists to show."""
    assert avg_volume(ROWS, 2) == pytest.approx(150.0)
    assert avg_volume(ROWS, 0) is None


# -- unusual moves --------------------------------------------------------

QUIET = [100, 101, 100.5, 101.2, 100.8, 101.1, 100.9, 101.3, 101.0, 100.7, 101.4]


def test_a_crash_day_is_flagged():
    flagged = unusual_move_indices(QUIET + [85.0])
    assert [i for i, _ in flagged] == [11]
    assert flagged[0][1] < 0


def test_a_quiet_series_flags_nothing():
    assert unusual_move_indices(QUIET) == []


def test_a_flat_series_has_no_spread_to_measure():
    assert unusual_move_indices([100.0] * 12) == []


def test_too_short_a_window_is_not_judged():
    """Two points have a standard deviation but no meaning."""
    assert unusual_move_indices([100, 130]) == []


# -- lane marks -----------------------------------------------------------


def _times(n, start="2026-03-02"):
    from datetime import datetime, timedelta

    base = datetime.fromisoformat(start)
    return [(base + timedelta(days=i)).timestamp() for i in range(n)]


def test_events_and_moves_merge_onto_bars():
    times = _times(12)
    marks = build_marks(
        times,
        QUIET + [85.0],
        [{"date": "2026-03-05", "kind": "earnings", "detail": "EPS 1.42"}],
    )
    kinds = {m["kind"] for m in marks}
    assert "earnings" in kinds
    assert "move_down" in kinds


def test_a_rating_event_splits_by_direction():
    times = _times(12)
    down = build_marks(
        times, QUIET + [101.5], [{"date": "2026-03-05", "kind": "rating", "action": "down"}]
    )
    up = build_marks(
        times, QUIET + [101.5], [{"date": "2026-03-05", "kind": "rating", "action": "up"}]
    )
    assert down[0]["kind"] == "rating_down"
    assert up[0]["kind"] == "rating_up"


def test_an_event_outside_the_loaded_bars_is_dropped():
    times = _times(12)
    marks = build_marks(times, QUIET + [101.5], [{"date": "1999-01-01", "kind": "split"}])
    assert marks == []


def test_every_mark_kind_has_a_style():
    """A kind with no style would silently render as a generic dot, losing the
    shape that carries the meaning for colour-blind readers."""
    times = _times(12)
    events = [
        {"date": "2026-03-05", "kind": k}
        for k in ("earnings", "dividend", "split", "rating")
    ]
    for mark in build_marks(times, QUIET + [85.0], events):
        assert mark["kind"] in MARK_STYLE


def test_build_marks_tolerates_empty_input():
    assert build_marks([], [], []) == []
    assert build_marks(_times(3), [1, 2, 3], None) == []


# -- benchmarks and sectors ----------------------------------------------


def test_us_listing_measures_against_the_sp500():
    assert benchmark_for("AAPL") == "^GSPC"


def test_suffixed_listings_get_their_local_index():
    assert benchmark_for("PETR4.SA") == "^BVSP"
    assert benchmark_for("SAP.DE") == "^GDAXI"
    assert benchmark_for("7203.T") == "^N225"


def test_unknown_suffix_has_no_benchmark():
    """Better to omit the comparison than to measure a Warsaw listing against
    the S&P 500 and print a number that looks meaningful."""
    assert benchmark_for("FOO.XX") is None


@pytest.mark.parametrize("sym", ["^GSPC", "EURUSD=X", "BTC-USD", "", "   "])
def test_things_that_are_not_stocks_have_no_benchmark(sym):
    assert benchmark_for(sym) is None


def test_suffix_of():
    assert suffix_of("PETR4.SA") == "SA"
    assert suffix_of("AAPL") == ""
    assert suffix_of("^GSPC") == ""


def test_sector_proxy_only_for_us_listings():
    assert sector_proxy_for("AAPL", "Technology") == "XLK"
    assert sector_proxy_for("SAP.DE", "Technology") is None
    assert sector_proxy_for("AAPL", "Nonsense Sector") is None
    assert sector_proxy_for("AAPL", None) is None


def test_all_eleven_gics_sectors_are_mapped():
    assert len(SECTOR_ETF) == 11
    assert len(set(SECTOR_ETF.values())) == 11


# -- news date plumbing ---------------------------------------------------


def test_date_tuple_is_what_gnews_wants():
    assert _date_tuple("2026-03-14") == (2026, 3, 14)
    assert _date_tuple("junk") is None
    assert _date_tuple(None) is None


def test_newsapi_window_guard_skips_old_dates():
    from datetime import date, timedelta

    recent = (date.today() - timedelta(days=3)).isoformat()
    assert _within_newsapi_window(recent) is True
    assert _within_newsapi_window("2019-01-02") is False
    assert _within_newsapi_window(None) is False


def test_rss_post_filter_keeps_only_the_window():
    stamp = "Thu, 03 Feb 2022 08:00:00 GMT"
    assert _published_within(stamp, "2022-02-02", "2022-02-05") is True
    assert _published_within(stamp, "2026-01-01", "2026-01-05") is False


def test_undated_items_are_excluded_from_a_historical_window():
    """An undated headline in a 2019 window is far more likely to be today's
    than the day in question; including it would answer wrongly."""
    assert _published_within("", "2022-02-02", "2022-02-05") is False
    assert _published_within(None, "2022-02-02", "2022-02-05") is False


# -- /day argument parsing ------------------------------------------------


def test_single_date_becomes_the_tightest_window():
    assert _as_of_spec("2026-03-14") == ("2026-03-14", "1D")


def test_a_range_centres_and_picks_a_covering_window():
    """Seven requested days must select the seven-day chip, not a wider one."""
    anchor, window = _as_of_spec("2026-03-09..2026-03-15")
    assert anchor == "2026-03-12"
    assert window == "1W"


def test_a_three_day_range_picks_the_three_day_chip():
    assert _as_of_spec("2026-03-13..2026-03-15") == ("2026-03-14", "3D")


def test_a_reversed_range_is_accepted():
    assert _as_of_spec("2026-03-15..2026-03-09") == _as_of_spec(
        "2026-03-09..2026-03-15"
    )


def test_a_very_wide_range_clamps_to_the_largest_window():
    _, window = _as_of_spec("2020-01-01..2026-01-01")
    assert window == "1M"


@pytest.mark.parametrize("junk", ["", "junk", "2026-03-09..junk", "..", None])
def test_unparseable_day_arguments_are_refused(junk):
    assert _as_of_spec(junk) == (None, "")
