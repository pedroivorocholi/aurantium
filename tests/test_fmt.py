"""Shared number formatting, and one glyph for "no value".

There were 28 private ``_fmt_*`` functions across the panels; nine of them —
every ``_fmt_num`` — were byte-for-byte identical. They existed because each
panel needed the same two decisions with nowhere shared to put them: how to
render a number, and what to show when there isn't one.

The second decision is the one that mattered, and it was wrong in 113 places.
An ASCII hyphen in a right-aligned monospaced numeric column sits exactly where
a minus sign would, at the same width, in the same ink — so ``-`` and ``-1.2``
begin identically and a reader scanning for losses has to stop and work out
whether a row is negative or absent.
"""

import math
from pathlib import Path

import pytest

from aurantium.components import fmt
from aurantium.panel import NULL_GLYPH

PANELS = Path(__file__).resolve().parent.parent / "aurantium" / "panels"

#: Everything that means "there is no number here".
MISSING = [None, "", "n/a", "abc", object(), float("nan")]


@pytest.mark.parametrize("fn", [fmt.num, fmt.pct, fmt.signed_pct,
                                fmt.integer, fmt.compact, fmt.price])
@pytest.mark.parametrize("value", MISSING)
def test_every_formatter_renders_the_same_null(fn, value):
    """One glyph, from every entry point. A panel that invents its own is how
    seventeen phrasings of "empty" happened elsewhere."""
    assert fn(value) == NULL_GLYPH


def test_nan_is_not_rendered_as_a_number():
    """pandas turns a missing statement cell into NaN, so without folding it in
    with None the table prints the literal text "nan" mid-column."""
    assert fmt.num(float("nan")) == NULL_GLYPH
    assert fmt.compact(math.nan) == NULL_GLYPH


def test_the_null_glyph_is_not_a_hyphen():
    """The whole point. If this ever reverts, the column becomes ambiguous
    again and nothing else in the suite would notice."""
    assert NULL_GLYPH == "—"
    assert NULL_GLYPH != "-"


def test_the_null_glyph_still_sorts_as_empty():
    """MarketTable parses cell text to sort numerically. A null that parses as a
    number — or fails to be recognised as null — would scatter empty rows
    through the ordering."""
    from aurantium.components.market_table import parse_numeric

    assert parse_numeric(NULL_GLYPH) is None
    assert parse_numeric("-") is None          # older panels, still tolerated
    assert parse_numeric("1,234.50") == pytest.approx(1234.5)


# -- formatting ------------------------------------------------------------


def test_num_groups_thousands():
    assert fmt.num(1234.5) == "1,234.50"
    assert fmt.num(1234.5, decimals=0) == "1,234"


def test_signed_pct_always_carries_a_sign():
    """An unsigned 0.42% beside a signed -1.10% reads as inconsistent
    formatting rather than as a positive number."""
    assert fmt.signed_pct(1.5) == "+1.50%"
    assert fmt.signed_pct(-1.5) == "-1.50%"
    assert fmt.signed_pct(0) == "+0.00%"


def test_compact_keeps_magnitude_and_sign():
    assert fmt.compact(2.4e12) == "2.4T"
    assert fmt.compact(-3.1e9) == "-3.1B"
    assert fmt.compact(950) == "950"


def test_price_gives_small_values_more_decimals():
    """An FX pair quoted as a fraction of a dollar carries its information in
    the fourth and fifth decimal; rounding to two shows 0.00 for a rate that
    moved."""
    assert fmt.price(0.00123) == "0.00123"
    assert fmt.price(1.2345) == "1.2345"
    assert fmt.price(228.1) == "228.10"


# -- the duplication is gone -----------------------------------------------


def test_no_panel_redefines_the_identical_number_formatter():
    """Nine byte-identical copies of ``_fmt_num`` existed. A tenth would drift."""
    offenders = []
    for src in PANELS.glob("*.py"):
        text = src.read_text(encoding="utf-8")
        if "def _fmt_num(" in text:
            offenders.append(src.name)
    assert not offenders, f"redefining the shared formatter: {offenders}"


def test_no_panel_returns_a_hyphen_as_a_null():
    """The migration's completeness check. Parsing code that *splits* on a
    hyphen, and a ``sign = "-"`` for an actual minus, are legitimate — only a
    bare ``return "-"`` is the old null."""
    offenders = []
    for src in PANELS.glob("*.py"):
        for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped in ('return "-"', 'return "-";'):
                offenders.append(f"{src.name}:{i}")
    assert not offenders, f"still returning a hyphen as null: {offenders}"
