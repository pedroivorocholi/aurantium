"""A workspace is restored in place: the saved arrangement first, every panel
an outline, then the real panels built into their places.

It used to build each panel and dock it as a tab of the same area, applying
the saved arrangement only at the end — the window showed the panels piling
up one over the other. The stuck states to rule out: a placeholder left in a
dock, a dock left holding nothing, or a panel whose build failed leaving a
blank hole in the arrangement.
"""

import pytest
from PySide6.QtWidgets import QApplication

from aurantium import motion
from aurantium import panel as panel_mod
from aurantium.panel import LoadingPlaceholder, Panel


@pytest.fixture(params=[True, False], ids=["anim-on", "anim-off"])
def animate(request, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: request.param)
    monkeypatch.setattr(panel_mod, "_pulse", None)
    return request.param


@pytest.fixture
def win(qapp, animate):
    from aurantium.panel import discover_panels
    from aurantium.paths import EXT_DIR

    discover_panels([EXT_DIR / "user_panels"], packages=("aurantium.panels",))
    from aurantium.app import MainWindow

    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    yield w
    w.close()
    w.deleteLater()
    QApplication.processEvents()


def _two_columns(win):
    win.add_panel("watchlist", instance_id="watchlist#1")
    win.add_panel(
        "chart", instance_id="chart#1", target_instance="watchlist#1",
        area=__import__("PySide6QtAds").DockWidgetArea.RightDockWidgetArea,
    )
    QApplication.processEvents()
    return win.serialize_layout()


def _area_of(win, iid):
    return win._docks[iid].dockAreaWidget()


def test_the_arrangement_is_in_place_before_any_panel_is_built(win):
    doc = _two_columns(win)
    seen = {}

    def ready():
        seen["areas"] = {iid: _area_of(win, iid) for iid in win._docks}
        seen["widgets"] = {iid: type(d.widget()) for iid, d in win._docks.items()}

    win.layout_ready = ready
    assert win.apply_layout(doc)
    # side by side, as saved — not tabbed into one area
    assert seen["areas"]["watchlist#1"] is not seen["areas"]["chart#1"]
    assert set(seen["widgets"].values()) == {LoadingPlaceholder}


def test_every_placeholder_is_replaced_by_its_panel(win):
    doc = _two_columns(win)
    assert win.apply_layout(doc)
    assert set(win._docks) == {"watchlist#1", "chart#1"}
    for dock in win._docks.values():
        assert isinstance(dock.widget(), Panel)
    assert not win._unbuilt
    QApplication.processEvents()
    assert not win.findChildren(LoadingPlaceholder)
    assert _area_of(win, "watchlist#1") is not _area_of(win, "chart#1")


def test_the_restored_layout_serializes_like_the_saved_one(win):
    doc = _two_columns(win)
    assert win.apply_layout(doc)
    again = win.serialize_layout()
    assert [p["instance"] for p in again["panels"]] == [p["instance"] for p in doc["panels"]]


def test_a_panel_that_fails_to_build_closes_its_place(win, monkeypatch):
    doc = _two_columns(win)
    from aurantium.panels.chart import ChartPanel

    def broken(self):
        raise ValueError("boom")

    monkeypatch.setattr(ChartPanel, "build", broken)
    monkeypatch.setattr(win, "notify", lambda *a, **k: None)
    assert win.apply_layout(doc)
    assert set(win._docks) == {"watchlist#1"}
    assert isinstance(win._docks["watchlist#1"].widget(), Panel)


def test_plot_panels_load_behind_gridlines_and_tables_behind_rows(qapp, animate):
    from aurantium.panels.chart import ChartPanel
    from aurantium.panels.watchlist import WatchlistPanel

    assert ChartPanel.SKELETON == "plot"
    assert LoadingPlaceholder(ChartPanel)._skeleton._table is False
    assert LoadingPlaceholder(WatchlistPanel)._skeleton._table is True


def test_a_placeholder_stops_pulsing_at_the_cap(qapp, animate):
    from aurantium.panels.watchlist import WatchlistPanel

    holder = LoadingPlaceholder(WatchlistPanel)
    holder._stop()
    assert holder._skeleton is None
    assert not panel_mod._skeleton_pulse()._live


def test_startup_closes_the_splash_once_the_arrangement_stands():
    import inspect

    from aurantium import __main__ as entry

    src = inspect.getsource(entry.main)
    assert "win.layout_ready = lambda: _close_splash(splash, win)" in src
    assert "win.layout_ready = None" in src
