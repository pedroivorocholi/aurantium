"""SymbolContext layout-restore robustness.

``from_json`` is fed ``doc.get("symbols", {})`` from a layout file on disk.
``.get`` returns the raw value whenever the key exists, so a hand-edited or
corrupted layout can hand it any shape at all. It runs under ``apply_layout``
during startup restore — raising there aborts the restore and drops the user
into an empty workspace, losing their saved layout.
"""

import pytest

from aurantium.symbol_context import SymbolContext


@pytest.fixture
def ctx():
    return SymbolContext()


@pytest.mark.parametrize(
    "junk",
    [
        # falsy — handled before this fix too
        None, {}, [], "", 0,
        # truthy non-dicts — these raised AttributeError before the guard
        ["A", "AAPL"], "AAPL", 42, (1, 2), {"A"}, b"AAPL", object(),
        # dict shells with unusable contents
        {"A": None}, {"A": 42}, {"A": ""}, {"A": ["AAPL"]}, {"A": {"x": 1}},
        {"A": b"AAPL"},
    ],
)
def test_from_json_never_raises(ctx, junk):
    ctx.from_json(junk)


def test_from_json_still_restores_a_valid_document(ctx):
    ctx.from_json({"A": "AAPL", "B": "MSFT"})
    assert ctx.symbol("A") == "AAPL"
    assert ctx.symbol("B") == "MSFT"


def test_from_json_keeps_good_entries_alongside_bad_ones(ctx):
    ctx.from_json({"A": "AAPL", "B": None, "C": 42})
    assert ctx.symbol("A") == "AAPL"
    assert ctx.symbol("B") == ""
    assert ctx.symbol("C") == ""


def test_non_dict_leaves_existing_state_untouched(ctx):
    ctx.set_symbol("A", "AAPL")
    ctx.from_json("garbage")
    assert ctx.symbol("A") == "AAPL"
