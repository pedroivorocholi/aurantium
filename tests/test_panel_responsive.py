"""Panels reorganize their content to fit the size they are given.

A dock panel is not one size — the same Watchlist can be a 300px sidebar strip
or a maximized full-screen table, and the user changes that by dragging a
splitter. Content that only suits one of those is broken at the others: at
320px the Watchlist elided prices to "4,432.…" (losing the actual number), and
at 900px it stretched five columns so far apart that a value floated 200px from
its own header.

Adapting is reorganizing for the new context, not scaling: drop what matters
least when space is short, rather than shrinking everything until nothing is
readable.
"""

import pytest

from aurantium.panel import COMPACT, REGULAR, WIDE, size_class_for


# -- the size classes themselves -------------------------------------------

def test_the_classes_are_ordered_by_width():
    assert size_class_for(320) == COMPACT
    assert size_class_for(600) == REGULAR
    assert size_class_for(1200) == WIDE


def test_the_compact_boundary_clears_the_widest_control_row(qapp):
    """The breakpoint is not a round number — it sits just above the widest
    control row any panel builds (the chart's range row), so a panel that has
    left COMPACT can always lay that row out without clipping."""
    from aurantium.panels.chart import ChartPanel

    chart = ChartPanel()
    chart.build()
    widest = max(
        chart._range_row.minimumSize().width(),
        chart._interval_row.minimumSize().width(),
    )
    assert size_class_for(widest + 1) != COMPACT


# -- panels are told about it ----------------------------------------------

@pytest.fixture
def panel(qapp):
    from aurantium.panel import Panel
    from aurantium.symbol_context import SymbolContext

    SymbolContext._inst = None

    class _P(Panel):
        panel_id = "resize_probe"
        panel_title = "Probe"

        def __init__(self):
            super().__init__()
            self.seen = []

        def build(self):
            pass

        def on_size_class(self, size_class):
            self.seen.append(size_class)

    p = _P()
    p.build()
    p.show()  # Qt sends no resize event to a hidden widget
    yield p
    SymbolContext._inst = None


def test_a_panel_knows_its_size_class(panel):
    panel.resize(320, 400)
    assert panel.size_class == COMPACT
    panel.resize(1200, 400)
    assert panel.size_class == WIDE


def test_the_hook_fires_when_the_class_changes(panel):
    panel.resize(320, 400)
    panel.seen.clear()
    panel.resize(1200, 400)
    assert panel.seen == [WIDE]


def test_the_hook_does_not_fire_on_every_pixel(panel):
    """A splitter drag emits a resize per pixel. Rebuilding a layout on each
    one would make dragging a panel edge stutter."""
    panel.resize(600, 400)
    panel.seen.clear()
    for w in range(600, 640):
        panel.resize(w, 400)
    assert panel.seen == []


def test_a_panel_that_ignores_the_hook_still_works(panel):
    """on_size_class is optional — user panels in user_panels/ must not have to
    implement it."""
    from aurantium.panel import Panel

    class _Bare(Panel):
        panel_id = "bare"
        panel_title = "Bare"

        def build(self):
            pass

    b = _Bare()
    b.build()
    b.show()
    b.resize(320, 400)  # must not raise
    assert b.size_class == COMPACT
