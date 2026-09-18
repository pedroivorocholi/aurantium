"""The chart's EVENTS lane and its Day Brief gestures.

The load-bearing property here is that nothing opens a panel by accident: a
plain click on the plot must stay inert, and every path that does publish a
date must name that date first.
"""

import datetime as dt

import pytest

from aurantium.date_context import DateContext


@pytest.fixture
def chart(qapp):
    from aurantium.panels.chart import ChartPanel

    c = ChartPanel()
    c.build()
    base = dt.datetime(2026, 3, 2)
    c._hist_t = [(base + dt.timedelta(days=i)).timestamp() for i in range(13)]
    c._hist_c = [100, 101, 100.5, 101.2, 100.8, 101.1, 100.9,
                 101.3, 101.0, 100.7, 101.4, 85.0, 86.0]
    yield c


def test_lane_exists_and_is_on_by_default(chart):
    assert chart._lane is not None
    assert chart._lane_on is True


def test_lane_is_x_linked_to_the_price_plot(chart):
    """An unlinked lane would drift out of alignment on any pan or zoom, and
    a mark would sit under the wrong candle."""
    assert chart._lane.pane.getViewBox().linkedView(0) is not None


def test_event_topics_merge_per_source(chart):
    chart._on_lane_earnings({"rows": [["2026-03-05", 1.5, 1.42, -5.3]]})
    chart._on_lane_ratings(
        {"upgrades": [{"date": "2026-03-05", "action": "down",
                       "from_grade": "Overweight", "to_grade": "Equal-Weight"}]}
    )
    chart._on_lane_cash({"history": [["2026-03-09", 0.24]], "splits": []})
    assert len(chart._lane_events) == 3
    # a second earnings publish replaces, never appends
    chart._on_lane_earnings({"rows": [["2026-03-06", 1.5, 1.42, -5.3]]})
    assert len(chart._lane_events) == 3


@pytest.mark.parametrize("junk", [None, "nonsense", 42, {}, {"rows": None}])
def test_event_handlers_tolerate_junk_payloads(chart, junk):
    chart._on_lane_earnings(junk)
    chart._on_lane_ratings(junk)
    chart._on_lane_cash(junk)


def test_date_under_cursor_uses_the_bar(chart):
    chart._cross_index = 5
    assert chart._date_under_cursor() == "2026-03-07"


def test_no_crosshair_means_no_date(chart):
    chart._cross_index = None
    assert chart._date_under_cursor() == ""


def test_d_with_no_crosshair_does_nothing_but_explain(chart):
    chart._cross_index = None
    chart._explain_cursor_day()
    assert "press D" in chart._status_lbl.text()


def test_context_menu_names_the_real_date(chart):
    chart._cross_index = 5
    chart._menu_bar_date = chart._date_under_cursor()
    first = chart._build_chart_menu().actions()[0].text()
    assert "What happened on" in first and "07 Mar 2026" in first


def test_context_menu_omits_the_entry_with_no_bar(chart):
    chart._menu_bar_date = ""
    first = chart._build_chart_menu().actions()[0].text()
    assert "What happened" not in first


def test_picking_a_date_publishes_to_the_link_group(chart):
    seen = []
    DateContext.instance().date_changed.connect(
        lambda g, d, w, s: seen.append((g, d))
    )
    chart.set_link_group("A")
    chart._pick_date("2026-03-07")
    assert ("A", "2026-03-07") in seen


def test_plain_clicks_on_the_plot_stay_inert(chart):
    """The whole gesture design rests on this: with no drawing tool armed a
    click on the price surface must do nothing at all."""
    calls = []
    chart._pick_date = lambda iso: calls.append(iso)
    assert chart._draw_tool is None
    chart._on_scene_clicked(_FakeClick())
    assert calls == []


def test_toggling_the_lane_off_clears_it(chart):
    chart._toggle_lane(False)
    assert chart._lane_on is False
    assert chart._lane.pane.isVisible() is False


def test_lane_state_survives_a_layout_round_trip(chart):
    chart._toggle_lane(False)
    saved = chart.settings()
    assert saved["events_lane"] is False
    chart.restore(saved)
    assert chart._lane_on is False


class _FakeClick:
    """Minimal stand-in for a pyqtgraph scene click event."""

    def button(self):
        from PySide6.QtCore import Qt

        return Qt.MouseButton.LeftButton

    def double(self):
        return False

    def scenePos(self):
        from PySide6.QtCore import QPointF

        return QPointF(0, 0)

    def accept(self):
        pass


# -- the gesture must actually produce a panel ----------------------------


@pytest.fixture
def win(qapp):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    w = MainWindow()
    yield w


def _day_briefs(w):
    return [
        d for d in w._docks.values()
        if getattr(d.widget(), "panel_id", "") == "day_brief"
    ]


def test_picking_a_date_opens_a_day_brief_when_none_is_docked(win):
    """The regression that made the feature look broken: the chart gestures
    published a date to a bus nobody was listening on. On any workspace saved
    before this panel existed there is no Day Brief docked, so the click had
    no visible effect at all."""
    chart_dock = win.add_panel("chart")
    chart = chart_dock.widget()
    assert _day_briefs(win) == []

    chart._pick_date("2022-02-03")

    assert len(_day_briefs(win)) == 1


def test_a_second_pick_reuses_the_open_panel(win):
    chart = win.add_panel("chart").widget()
    chart._pick_date("2022-02-03")
    chart._pick_date("2022-02-04")
    assert len(_day_briefs(win)) == 1


def test_the_opened_panel_joins_the_chart_link_group(win):
    """A Day Brief opened from a group-B chart must follow that chart, not
    group A."""
    chart = win.add_panel("chart").widget()
    chart.set_link_group("B")
    chart._pick_date("2022-02-03")
    brief = _day_briefs(win)[0].widget()
    assert brief.link_group == "B"
    assert brief._anchor == "2022-02-03"


def test_the_d_shortcut_opens_it_too(win):
    chart = win.add_panel("chart").widget()
    import datetime as dt

    base = dt.datetime(2026, 3, 2)
    chart._hist_t = [(base + dt.timedelta(days=i)).timestamp() for i in range(6)]
    chart._cross_index = 3
    chart._explain_cursor_day()
    assert len(_day_briefs(win)) == 1


# -- it opens as a window, not by squeezing the layout --------------------


def _is_floating(dock):
    return bool(dock.isFloating() or dock.isInFloatingContainer())


def test_a_new_day_brief_opens_floating(win):
    """Docking it into the side of a tuned workspace steals width from every
    panel the user actually keeps open, and forces sideways scrolling."""
    chart = win.add_panel("chart").widget()
    chart._pick_date("2022-02-03")
    assert _is_floating(_day_briefs(win)[0])


def test_the_day_brief_is_the_only_floatable_panel(win):
    """The never-float rule still holds for everything else."""
    import PySide6QtAds as QtAds

    for pid in ("chart", "news", "watchlist", "analyst"):
        dock = win.add_panel(pid)
        assert not dock.features() & QtAds.CDockWidget.DockWidgetFloatable, pid
        assert not _is_floating(dock), pid


def test_a_day_brief_already_docked_is_not_torn_out(win):
    """If the user has deliberately docked one, a later click must re-centre
    it where it is, not yank it into a window."""
    docked = win.add_panel("day_brief")  # no floating= -> docks normally
    assert not _is_floating(docked)

    chart = win.add_panel("chart").widget()
    chart._pick_date("2022-02-03")

    briefs = _day_briefs(win)
    assert len(briefs) == 1
    assert not _is_floating(briefs[0])
    assert briefs[0].widget()._anchor == "2022-02-03"


def test_slash_day_also_floats(win):
    win._cmd.setText("/day 2022-02-03")
    win._on_command()
    assert _is_floating(_day_briefs(win)[0])


def test_floating_geometry_stays_on_screen(win):
    from PySide6.QtWidgets import QApplication

    chart = win.add_panel("chart").widget()
    chart._pick_date("2022-02-03")
    container = _day_briefs(win)[0].floatingDockContainer()
    assert container is not None
    avail = (win.screen() or QApplication.primaryScreen()).availableGeometry()
    geo = container.geometry()
    assert geo.width() <= avail.width() and geo.height() <= avail.height()
    assert geo.left() >= avail.left() and geo.top() >= avail.top()


def test_a_floatable_panel_can_still_be_docked_by_the_user(win):
    """Floating is the opening position, not a cage — the dock feature stays
    on so it can be dragged into the workspace and kept."""
    import PySide6QtAds as QtAds

    chart = win.add_panel("chart").widget()
    chart._pick_date("2022-02-03")
    dock = _day_briefs(win)[0]
    assert dock.features() & QtAds.CDockWidget.DockWidgetMovable
    assert dock.features() & QtAds.CDockWidget.DockWidgetFloatable
