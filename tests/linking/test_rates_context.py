"""RatesContext mirrors SymbolContext's group semantics but validates its
payload against the country table, and must tolerate junk from a hand-edited
layout file without raising."""

import pytest

from aurantium.rates_context import RatesContext
from aurantium.symbol_context import DEFAULT_GROUP, UNLINKED


@pytest.fixture
def ctx():
    c = RatesContext()
    yield c


def test_set_and_read_back(ctx):
    ctx.set_country("A", "JP")
    assert ctx.country("A") == "JP"
    assert ctx.country("B") == ""


def test_codes_are_normalized(ctx):
    ctx.set_country("A", " jp ")
    assert ctx.country("A") == "JP"


def test_unknown_code_is_rejected(ctx):
    ctx.set_country("A", "ZZ")
    assert ctx.country("A") == ""


def test_unlinked_group_is_ignored(ctx):
    ctx.set_country(UNLINKED, "US")
    assert ctx.country(UNLINKED) == ""


def test_signal_carries_group_code_and_source(ctx):
    seen = []
    ctx.country_changed.connect(lambda g, c, s: seen.append((g, c, s)))
    sentinel = object()
    ctx.set_country("A", "US", source=sentinel)
    assert seen == [("A", "US", sentinel)]


def test_same_value_is_suppressed(ctx):
    seen = []
    ctx.set_country("A", "US")
    ctx.country_changed.connect(lambda g, c, s: seen.append(c))
    ctx.set_country("A", "US")
    assert seen == []


def test_groups_are_independent(ctx):
    ctx.set_country("A", "US")
    ctx.set_country("B", "JP")
    assert (ctx.country("A"), ctx.country("B")) == ("US", "JP")


def test_json_round_trip(ctx):
    ctx.set_country("A", "US")
    ctx.set_country("C", "XM")
    restored = RatesContext()
    restored.from_json(ctx.to_json())
    assert restored.country("A") == "US"
    assert restored.country("C") == "XM"


@pytest.mark.parametrize(
    "junk",
    [
        None, {}, {"A": None}, {"A": 42}, {"A": "ZZ"}, {"A": ""}, {7: "US"},
        # truthy non-dicts: doc.get("rates", {}) returns the raw value when the
        # key exists, so a hand-edited layout can hand us any of these
        [], ["A", "US"], "US", 42, (1, 2), {"A"}, b"US", object(),
        {"A": {"nested": 1}}, {"A": ["US"]}, {"A": b"US"}, {"A": " us "},
    ],
)
def test_from_json_tolerates_junk(ctx, junk):
    ctx.from_json(junk)          # must not raise — a raise costs the saved layout
    assert ctx.country("A") in ("", "US")


def test_default_group_matches_symbol_context():
    assert DEFAULT_GROUP == "A"
