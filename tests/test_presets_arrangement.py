"""Shipped presets must restore the arrangement they were generated with.

The JSON-level checks in ``test_presets_shipped.py`` only prove an ``ads_state``
string is present — not that it still applies. That gap is real: ``apply_layout``
swallows ``restoreState`` failures with a bare ``except Exception: pass``, so a
stale or corrupt blob degrades silently to QtAds' default tiling and every
JSON-level test keeps passing.

These tests restore each preset into a real window and measure the result. They
are what would have caught the sizing defect where a desk's news panel took half
the window while its chart was squeezed into a quarter.
"""

import json

import pytest

from aurantium.presets import available_presets

pytestmark = pytest.mark.usefixtures("qapp")

# Each desk's hero — the panel the layout is built around, and the one that must
# be the widest thing on screen. It is NOT the price chart everywhere: on a macro
# desk the rates panel is the point, and on a commodities desk it is the curve.
# See the sizing rule in tools/generate_presets.py.
CENTREPIECE = {
    "Macro Desk": "macro#1",
    "Commodities Desk": "futures_curve#1",
    "Equity Research": "chart#1",
}


@pytest.fixture(scope="module")
def window(qapp):
    """A real MainWindow, shown so Qt actually lays the docks out.

    An unrealized window reports zero widths for every dock area, which reads
    as a plausible-looking 50/50 split and hides exactly the defect these tests
    exist to catch.

    Providers are deliberately NOT registered: this test measures geometry, not
    data, and registering them starts background refresh threads that outlive
    the fixture and perturb timing-sensitive tests elsewhere in the session
    (it made the symbol-search debounce tests emit twice).
    """
    from aurantium.panel import discover_panels

    discover_panels([], packages=("aurantium.panels",))

    from aurantium.app import MainWindow

    win = MainWindow()
    win.resize(1600, 900)
    win.show()
    qapp.processEvents()
    yield win
    # Tear down fully — a live window keeps panel timers running for the rest
    # of the session.
    win.apply_layout({"version": 1, "panels": [], "ads_state": "", "symbols": {}})
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _areas(win) -> dict[int, tuple[object, list[str]]]:
    """Group the open docks by the dock area they share — the tabbing relation."""
    areas: dict[int, tuple[object, list[str]]] = {}
    for instance_id, dock in win._docks.items():
        area = dock.dockAreaWidget()
        areas.setdefault(id(area), (area, []))[1].append(instance_id)
    return areas


def _restore(win, qapp, preset):
    win.apply_layout(json.loads(json.dumps(preset.doc)))
    qapp.processEvents()
    return _areas(win)


@pytest.fixture
def preset(request, window):
    """Resolve a preset by name.

    Parametrization is by NAME, not by Preset object: ``available_presets()``
    validates against the panel registry, which is empty at collection time —
    panels are only discovered by the ``window`` fixture. Depending on it here
    is what makes the lookup succeed.
    """
    name = request.param
    found = next((p for p in available_presets() if p.name == name), None)
    assert found is not None, (
        f"shipped preset {name!r} not found — run tools/generate_presets.py"
    )
    return found


@pytest.mark.parametrize("preset", sorted(CENTREPIECE), indirect=True)
def test_every_panel_restores(window, qapp, preset):
    """Every panel in the document actually opens — no silent drops."""
    areas = _restore(window, qapp, preset)
    opened = {inst for _, insts in areas.values() for inst in insts}
    expected = {spec["instance"] for spec in preset.doc["panels"]}
    assert opened == expected


@pytest.mark.parametrize("preset", sorted(CENTREPIECE), indirect=True)
def test_centrepiece_is_the_widest_panel(window, qapp, preset):
    """The desk is built around one panel; it must not be the narrowest thing
    on screen. This is the assertion the sizing defect failed."""
    areas = _restore(window, qapp, preset)
    centrepiece = CENTREPIECE[preset.name]

    widths = {}
    for area, insts in areas.values():
        for inst in insts:
            widths[inst] = area.width()

    assert centrepiece in widths, f"{preset.name}: {centrepiece} did not open"
    widest = max(widths.values())
    assert widths[centrepiece] == widest, (
        f"{preset.name}: {centrepiece} is {widths[centrepiece]}px but the widest "
        f"panel is {widest}px — {widths}"
    )


def test_equity_research_tabs_fundamentals_with_analyst(window, qapp):
    """A tabbed pair is the one arrangement detail that cannot survive a failed
    restoreState, so it doubles as proof the ads_state really applied rather
    than degrading to default tiling."""
    found = next(
        (p for p in available_presets() if p.name == "Equity Research"), None
    )
    assert found is not None, "Equity Research preset missing"
    areas = _restore(window, qapp, found)

    shared = [
        set(insts)
        for _, insts in areas.values()
        if {"fundamentals#1", "analyst#1"} <= set(insts)
    ]
    assert shared, (
        "fundamentals#1 and analyst#1 are not tabbed together — the layout "
        f"restored as {[insts for _, insts in areas.values()]}"
    )


@pytest.mark.parametrize("preset", sorted(CENTREPIECE), indirect=True)
def test_seeds_a_symbol_for_link_group_a(window, qapp, preset):
    """A preset with no seeded symbol opens every symbol-driven panel blank on
    a fresh install, where the symbol context starts empty."""
    symbol = preset.doc.get("symbols", {}).get("A")
    assert symbol, f"{preset.name} seeds no symbol for link group A"
