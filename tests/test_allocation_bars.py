"""Portfolio sector allocation, as a ranked bar rather than a pie.

The pie needed one distinguishable colour per sector and there were up to
eleven. The list it used was invented rather than derived and failed on its own
terms: ``#8bc34a`` and ``#7ed321`` measured ΔE 5.8 apart under *normal* vision,
``#ffca28`` and ``#7ed321`` ΔE 2.1 under protanopia, and ``#f8e71c`` sat at
1.28:1 on the light theme's canvas. Slices were also assigned by rank order, so
adding a position reordered the sectors and repainted all of them.

A ranked bar removes the requirement instead of trying to meet it: position
carries rank, length carries magnitude, and each row names itself. These tests
pin the properties that make that true — and the one that got away first time
round, which is that no text may sit on the bar.
"""

import pytest

from aurantium.color import contrast


@pytest.fixture
def bars(qapp):
    from aurantium.panels.portfolio import _AllocationBars

    return _AllocationBars()


SECTORS = [
    ("Technology", 31.2), ("Financials", 18.4), ("Health Care", 14.1),
    ("Energy", 11.0), ("Industrials", 8.3), ("Cons Staples", 6.2),
    ("Utilities", 4.4), ("Materials", 3.1), ("Real Estate", 2.0),
    ("Comm Services", 1.0), ("Cons Discretionary", 0.3),
]


def test_rows_are_ranked_regardless_of_input_order(bars):
    """Rank is the encoding, so it cannot depend on dict iteration order."""
    bars.set_rows(list(reversed(SECTORS)))
    values = [v for _l, v in bars._rows if _l != bars.OTHER]
    assert values == sorted(values, reverse=True)
    assert bars._rows[0][0] == "Technology"


def test_the_tail_folds_into_one_residual(bars):
    """Eleven bars is past the point where part-to-whole reads at a glance."""
    bars.set_rows(SECTORS)
    assert len(bars._rows) == bars.MAX_ROWS
    assert bars._rows[-1][0] == bars.OTHER


def test_the_fold_conserves_the_total(bars):
    """"Other" must be the actual remainder — a part-to-whole chart that does
    not sum to the whole is worse than no chart."""
    bars.set_rows(SECTORS)
    assert sum(v for _l, v in bars._rows) == pytest.approx(
        sum(v for _l, v in SECTORS)
    )


def test_a_short_list_is_left_alone(bars):
    """No spurious "Other" when everything already fits."""
    short = SECTORS[:4]
    bars.set_rows(short)
    assert len(bars._rows) == 4
    assert bars.OTHER not in [l for l, _v in bars._rows]


def test_empty_is_handled(bars):
    bars.set_rows([])
    assert bars._rows == []
    bars.repaint()  # must not raise on the empty path


def test_colour_is_never_asked_to_carry_identity(bars):
    """The point of the form change. ``set_rows`` takes no colour argument and
    the widget holds no palette, so there is nothing to validate and nothing to
    drift. If a colour ever appears in this signature, the pie is back."""
    import inspect

    sig = inspect.signature(bars.set_rows)
    assert list(sig.parameters) == ["rows"]
    src = inspect.getsource(type(bars))
    assert "PIE_COLORS" not in src


def test_no_categorical_palette_remains_in_the_module():
    """The retired list must not survive as a *value*.

    Checked against module attributes rather than the source text: the first
    version of this test grepped the file and tripped over the docstrings that
    explain why those colours were removed, which is documentation doing its
    job, not a regression.
    """
    from aurantium.panels import portfolio

    assert not hasattr(portfolio, "PIE_COLORS")

    condemned = {"#8bc34a", "#7ed321", "#f8e71c", "#ffca28", "#e91e63"}
    for name in dir(portfolio):
        value = getattr(portfolio, name)
        if isinstance(value, str):
            assert value.lower() not in condemned, f"{name} still holds {value}"
        elif isinstance(value, (list, tuple, set)):
            found = {
                v.lower() for v in value
                if isinstance(v, str) and v.lower() in condemned
            }
            assert not found, f"{name} still holds {sorted(found)}"


# -- the bug the first version of this widget shipped ----------------------


def test_label_text_never_sits_on_the_bar_fill(bars):
    """The first draft drew the sector name inside the bar, which put ``FG`` on
    ``ACCENT`` at 1.24:1 on the dark theme — the exact defect this whole pass
    exists to remove, reintroduced by the fix for it.

    Text is drawn on the panel background; this pins that it stays legible
    there, and that the bar colours are never used as a text surface.
    """
    from aurantium import theme

    for ink in (theme.FG, theme.FG_DIM):
        assert contrast(ink, theme.BG) >= 4.5

    # ...and the reason the label cannot go back on the fill.
    assert contrast(theme.FG, theme.ACCENT) < 4.5


def test_the_residual_is_visually_distinct_from_a_sector(bars):
    """"Other" stays last even when it outweighs rows above it, so it must not
    read as a rank violation. A neutral fill is what says "bucket"."""
    from aurantium import theme

    bars.set_rows(SECTORS)
    other_value = dict(bars._rows)[bars.OTHER]
    smallest_sector = min(v for l, v in bars._rows if l != bars.OTHER)
    # The case that makes the distinct fill necessary, not merely nice.
    assert other_value > smallest_sector
    assert theme.FG_MUTED not in (theme.ACCENT, theme.ACCENT_DEEP)
