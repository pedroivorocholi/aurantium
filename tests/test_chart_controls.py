"""The chart's control rows must fit inside a real panel.

Merging RANGE and INTERVAL onto one row saved a band of vertical chrome and
cost reachability: in a chart panel roughly half a 1080p screen wide, the
interval chips were pushed past the right edge with no way to scroll to them,
so the control was simply gone. Vertical space is worth less than a control the
user can reach.
"""

import pytest

#: A chart panel sharing a screen with one other column — the arrangement the
#: shipped workspaces use, and the width the merged row overflowed at.
REALISTIC_PANEL_WIDTH = 640


@pytest.fixture
def chart(qapp):
    from aurantium.panels.chart import ChartPanel

    p = ChartPanel()
    p.build()
    # The dock manager sets MinimumSizeHintFromContentMinimumSize on every
    # panel so a splitter can drag it small; without the same release here the
    # widget's own minimum (~609px) would clamp resize() and the compact path
    # would never be exercised.
    p.setMinimumSize(0, 0)
    return p


def _row_width(layout) -> int:
    """Minimum width the row needs before its controls start clipping."""
    return layout.minimumSize().width()


def test_the_range_row_fits_a_realistic_panel(chart):
    assert _row_width(chart._range_row) <= REALISTIC_PANEL_WIDTH


def test_the_interval_row_fits_a_realistic_panel(chart):
    assert _row_width(chart._interval_row) <= REALISTIC_PANEL_WIDTH


def test_range_and_interval_are_separate_rows(chart):
    """Structural guard on the same failure: one row cannot hold both without
    overflowing a normal panel."""
    assert chart._range_row is not chart._interval_row


def test_every_interval_chip_is_reachable(chart):
    """The clipped chips still existed as widgets — they were just off-screen.
    Membership in the interval row is what makes them reachable."""
    row = chart._interval_row
    widgets = {row.itemAt(i).widget() for i in range(row.count())}
    for label, btn in chart._interval_buttons.items():
        assert btn in widgets, f"interval chip {label} is not in the interval row"


# -- compact panels ---------------------------------------------------------

def test_compact_hides_the_eyebrow_labels(chart):
    """At 320px "RANGE" clipped to "RAN(" and "custom…" to "to". The eyebrows
    are the least informative thing in the row — the chips say what they are —
    so they go first."""
    from aurantium.panel import COMPACT

    chart.show()
    chart.resize(320, 400)
    assert chart.size_class == COMPACT
    assert not chart._range_eyebrow.isVisibleTo(chart)
    assert not chart._interval_eyebrow.isVisibleTo(chart)


def test_compact_keeps_the_common_ranges_reachable(chart):
    """Dropping chips is only acceptable if what remains covers the everyday
    cases — a day, a month, six months, a year, everything."""
    chart.show()
    chart.resize(320, 400)
    visible = {
        label for label, btn in chart._range_buttons.items()
        if btn.isVisibleTo(chart)
    }
    assert {"1d", "1mo", "6mo", "1y", "max"} <= visible


def test_compact_never_hides_the_active_selection(chart):
    """Hiding the chip that shows the current range would leave the user unable
    to see what they are looking at."""
    chart.show()
    chart._set_range_preset("2y")
    chart.resize(320, 400)
    assert chart._range_buttons["2y"].isVisibleTo(chart)


def test_widening_brings_every_chip_back(chart):
    chart.show()
    chart.resize(320, 400)
    chart.resize(1000, 400)
    for label, btn in chart._range_buttons.items():
        assert btn.isVisibleTo(chart), f"{label} did not come back"
    for label, btn in chart._interval_buttons.items():
        assert btn.isVisibleTo(chart), f"{label} did not come back"
    assert chart._range_eyebrow.isVisibleTo(chart)


def test_the_compact_range_row_fits_a_compact_panel(chart):
    chart.show()
    chart.resize(320, 400)
    chart._range_row.activate()
    chart._interval_row.activate()
    assert chart._range_row.minimumSize().width() <= 320
    assert chart._interval_row.minimumSize().width() <= 320


def test_compact_hides_the_indicators_eyebrow(chart):
    """Its row overflowed too — the add-indicator button was clipped to a
    sliver at 320px, which is the one control that row exists for."""
    chart.show()
    chart.resize(320, 400)
    assert not chart._indicators_eyebrow.isVisibleTo(chart)
    assert chart._add_btn.isVisibleTo(chart)
