"""DateContext mirrors SymbolContext's group semantics but carries an anchor
date plus a window token, and must tolerate junk from a hand-edited layout
file without raising."""

import pytest

from aurantium.date_context import (
    DEFAULT_WINDOW,
    WINDOW_SPAN_DAYS,
    DateContext,
    format_range,
    parse_iso,
    window_range,
)
from aurantium.symbol_context import UNLINKED


@pytest.fixture
def ctx():
    yield DateContext()


# -- pure helpers ---------------------------------------------------------


def test_parse_iso_accepts_padding_and_rejects_junk():
    assert parse_iso(" 2026-03-14 ").isoformat() == "2026-03-14"
    assert parse_iso("14/03/2026") is None
    assert parse_iso("") is None
    assert parse_iso(None) is None
    assert parse_iso(20260314) is None


def test_window_token_is_the_total_span_not_a_radius():
    """The number on the chip is what the user reads back in the resolved
    range underneath it, so "1W" has to be seven days, not fifteen."""
    assert window_range("2026-03-14", "1D") == ("2026-03-14", "2026-03-14")
    assert window_range("2026-03-14", "3D") == ("2026-03-13", "2026-03-15")
    assert window_range("2026-03-14", "1W") == ("2026-03-11", "2026-03-17")
    assert window_range("2026-03-14", "1M") == ("2026-02-27", "2026-03-29")


@pytest.mark.parametrize("token", ["1D", "3D", "1W", "1M"])
def test_resolved_span_matches_the_chip_label(token):
    start, end = window_range("2026-03-14", token)
    days = (parse_iso(end) - parse_iso(start)).days + 1
    assert days == WINDOW_SPAN_DAYS[token]


@pytest.mark.parametrize("token", ["1D", "3D", "1W", "1M"])
def test_the_anchor_sits_in_the_middle(token):
    start, end = window_range("2026-03-14", token)
    anchor = parse_iso("2026-03-14")
    assert (anchor - parse_iso(start)).days == (parse_iso(end) - anchor).days


def test_window_range_falls_back_to_default_on_unknown_token():
    assert window_range("2026-03-14", "5Y") == window_range(
        "2026-03-14", DEFAULT_WINDOW
    )


def test_window_range_rejects_a_bad_anchor():
    assert window_range("not-a-date", "1D") is None


def test_the_default_window_reaches_over_the_weekend():
    """A Monday gap is routinely explained by Saturday news, so the default is
    3D rather than the strict single session -- which stays available."""
    assert DEFAULT_WINDOW == "3D"
    start, end = window_range("2026-03-16", DEFAULT_WINDOW)  # a Monday
    assert start == "2026-03-15" and end == "2026-03-17"


def test_format_range_collapses_a_shared_month():
    assert format_range("2026-03-13", "2026-03-15") == "13 – 15 Mar"
    assert format_range("2026-02-26", "2026-03-04") == "26 Feb – 4 Mar"
    assert format_range("2026-03-14", "2026-03-14") == "14 Mar"
    assert format_range("junk", "2026-03-14") == ""


# -- the bus --------------------------------------------------------------


def test_set_and_read_back(ctx):
    ctx.set_date("A", "2026-03-14", "1W")
    assert ctx.date("A") == "2026-03-14"
    assert ctx.window("A") == "1W"
    assert ctx.date("B") == ""
    assert ctx.window("B") == DEFAULT_WINDOW


def test_invalid_date_is_rejected(ctx):
    ctx.set_date("A", "2026-13-45")
    assert ctx.date("A") == ""


def test_unknown_window_keeps_the_current_one(ctx):
    ctx.set_date("A", "2026-03-14", "1W")
    ctx.set_date("A", "2026-03-15", "nonsense")
    assert ctx.window("A") == "1W"


def test_unlinked_group_is_ignored(ctx):
    ctx.set_date(UNLINKED, "2026-03-14")
    assert ctx.date(UNLINKED) == ""


def test_signal_carries_group_date_window_and_source(ctx):
    seen = []
    ctx.date_changed.connect(lambda g, d, w, s: seen.append((g, d, w, s)))
    sentinel = object()
    ctx.set_date("A", "2026-03-14", "3D", source=sentinel)
    assert seen == [("A", "2026-03-14", "3D", sentinel)]


def test_same_value_does_not_republish(ctx):
    seen = []
    ctx.set_date("A", "2026-03-14", "1D")
    ctx.date_changed.connect(lambda *a: seen.append(a))
    ctx.set_date("A", "2026-03-14", "1D")
    assert seen == []
    ctx.set_date("A", "2026-03-14", "1W")  # window alone is a real change
    assert len(seen) == 1


def test_round_trips_through_json(ctx):
    ctx.set_date("A", "2026-03-14", "1W")
    ctx.set_date("C", "2020-01-02", "1M")
    other = DateContext()
    other.from_json(ctx.to_json())
    assert other.date("A") == "2026-03-14" and other.window("A") == "1W"
    assert other.date("C") == "2020-01-02" and other.window("C") == "1M"


@pytest.mark.parametrize(
    "junk",
    [
        None,
        "a string",
        ["a", "list"],
        42,
        {"A": "not a dict"},
        {"A": {"anchor": "nope"}},
        {"A": {"anchor": None}},
        {7: {"anchor": "2026-03-14"}},
        {"A": {"anchor": "2026-03-14", "window": "ZZZ"}},
    ],
)
def test_from_json_never_raises(ctx, junk):
    """A raise here would not crash the app -- it would silently cost the user
    their entire saved workspace. See rates_context.py:67-87."""
    ctx.from_json(junk)


def test_from_json_keeps_a_valid_entry_and_repairs_a_bad_window(ctx):
    ctx.from_json({"A": {"anchor": "2026-03-14", "window": "ZZZ"}})
    assert ctx.date("A") == "2026-03-14"
    assert ctx.window("A") == DEFAULT_WINDOW
