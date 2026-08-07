"""Generate the shipped preset workspaces under ``layouts/presets/``.

Presets are ordinary layout documents (the same shape
``MainWindow.serialize_layout()`` produces) plus ``name`` and ``description``.
The ``ads_state`` blob inside is an opaque QtAds binary hex string — there is
no sane way to hand-write one. Rather than capture it once by mouse in the
running app (fragile, unreviewable in a diff, and impossible to redo when a
panel is renamed), this script drives the *real* dock manager headlessly:
it builds an actual ``MainWindow``, adds the desk's panels with
``add_panel(..., area=..., target_instance=...)`` to build the precise
arrangement, calls ``serialize_layout()`` to capture a genuine ``ads_state``,
and writes the result as pretty-printed JSON.

Each desk also seeds link group A with a symbol before its panels are built,
so a fresh install — where the symbol context starts empty — opens on real
data rather than a screen of blank symbol-driven panels.

Run it whenever a desk's arrangement changes, or after a panel referenced by
a desk is renamed/removed (then update the desk definitions below and re-run).
It is deterministic: two consecutive runs produce byte-identical files.

Each desk is built in its **own subprocess**. Building all three in one process
crashed roughly one run in four, always partway, leaving some desks rewritten
and the rest stale — a silent partial generation. The crash lives in state
shared between desks, so nothing is shared. A crashed child fails the whole run
loudly instead.

Usage (from ``app/``):

    .venv/Scripts/python.exe tools/generate_presets.py

Pass a single desk's filename to build just that one (this is how the parent
invokes each child):

    .venv/Scripts/python.exe tools/generate_presets.py macro_desk.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Must happen before any PySide6 widget import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

PRESETS_DIR = APP_ROOT / "layouts" / "presets"

# Each step: (panel_id, instance_id, area_attr, target_instance)
# area_attr is a QtAds.DockWidgetArea attribute name; target_instance is
# None for a global placement.
#
# "panels" decides the TOPOLOGY — which panel sits beside or beneath which:
#   * target_instance=None places against the whole container.
#   * target_instance="foo#1" joins the splitter that already holds foo#1.
# QtAds then divides every splitter evenly, which is where "splits" comes in.
#
# "splits" decides the SIZES. An even division ignores what a panel is FOR: a
# financial-statement table is unreadable at a quarter width, a news headline
# list wants height and almost no width, and a rates curve needs horizontal
# room to show a shape. Each entry names the dock instances on either side of
# one splitter plus the weights to give them; the generator finds that splitter
# by its contents and calls setSizes(). QtAds stores the result in ads_state
# and rescales proportionally on restore, so the ratios hold at any size.
#
# Weights are relative to their own splitter, so nested ones compound — see the
# note on the commodities monitor below.
#
# Measured by restoring each preset into a shown 1600x900 window (QtAds
# stores ratios, so these hold at any size):
#   Macro Desk        macro 80%w x 55%h spanning the top (hero)
#                     cot_history 40%w | chart 40%w beneath
#                     news 20%w full height
#   Commodities Desk  futures_curve 50%w x 52%h (hero)
#                     cot_history 50%w x 43%h beneath it
#                     commodities 20%w full height | chart 30%w full height
#   Equity Research   chart 63%w x 55%h (hero)
#                     fundamentals+analyst 63%w x 40%h tabbed beneath
#                     watchlist 15%w full height | news 22%w full height
#
# Re-measure after changing this table, and read each panel's rectangle rather
# than inferring columns — grouping dock areas by x-origin and summing widths
# double-counts a column that holds two stacked panels, which reports a hero at
# 40% when it is really at 50%. Note also that the window must be shown() first:
# an unrealized one reports zero width for every dock area, which reads as a
# plausible even split and hides the very thing you are measuring.
#
# "symbol" seeds link group A before the panels are built, so the desk opens
# on real data instead of blank panels on a fresh install (SymbolContext is
# empty there, and an empty "symbols" map restores to nothing). It also gets
# baked into the panels' own saved settings — CL=F, for instance, switches
# futures_curve to Crude and cot_history to the crude_oil CFTC market.
DESKS = [
    {
        # Hero is Macro / Rates — on a macro desk the rates panel IS the point,
        # not a single ticker's price chart. Macro and COT are both wide
        # time-series charts, so neither is stacked in a narrow column.
        "file": "macro_desk.json",
        "name": "Macro Desk",
        "description": "Rates, inflation and positioning — the macro starting point.",
        "symbol": "^TNX",  # US 10-year yield
        "panels": [
            ("macro", "macro#1", "CenterDockWidgetArea", None),
            ("cot_history", "cot_history#1", "BottomDockWidgetArea", "macro#1"),
            ("chart", "chart#1", "RightDockWidgetArea", "cot_history#1"),
            ("news", "news#1", "RightDockWidgetArea", None),
        ],
        "splits": [
            # outer: everything | news
            ([["macro#1", "cot_history#1", "chart#1"], ["news#1"]], [80, 20]),
            # left region: rates on top, the pair beneath
            ([["macro#1"], ["cot_history#1", "chart#1"]], [58, 42]),
            # bottom row: positioning | chart
            ([["cot_history#1"], ["chart#1"]], [50, 50]),
        ],
    },
    {
        # Hero is the Futures Curve — the signature panel of a commodities
        # desk. The commodities monitor is a price list, so it takes a narrow
        # full-height column.
        "file": "commodities_desk.json",
        "name": "Commodities Desk",
        "description": "Curves, prices and COT positioning for commodities.",
        "symbol": "CL=F",  # WTI crude — a futures root the curve/COT panels cover
        "panels": [
            ("commodities", "commodities#1", "CenterDockWidgetArea", None),
            ("futures_curve", "futures_curve#1", "RightDockWidgetArea", "commodities#1"),
            ("cot_history", "cot_history#1", "BottomDockWidgetArea", "futures_curve#1"),
            ("chart", "chart#1", "RightDockWidgetArea", None),
        ],
        "splits": [
            # outer: monitor + centre | chart
            (
                [
                    ["commodities#1", "futures_curve#1", "cot_history#1"],
                    ["chart#1"],
                ],
                [70, 30],
            ),
            # monitor | centre column. Weights are relative to the PARENT
            # split, so these compound: 29% of the 70% region lands the monitor
            # at ~20% of the window, which is what long names like
            # "Natural Gas" need.
            (
                [["commodities#1"], ["futures_curve#1", "cot_history#1"]],
                [29, 71],
            ),
            # centre: curve over positioning
            ([["futures_curve#1"], ["cot_history#1"]], [55, 45]),
        ],
    },
    {
        # Classic terminal shape: narrow nav column left, chart hero, and the
        # wide tables (Financials / Analyst Recs, tabbed) across the full
        # centre width beneath it rather than squeezed into a sliver.
        "file": "equity_research.json",
        "name": "Equity Research",
        "description": "Watchlist, chart and fundamentals for single-name work.",
        "symbol": "AAPL",
        "panels": [
            ("watchlist", "watchlist#1", "CenterDockWidgetArea", None),
            ("chart", "chart#1", "RightDockWidgetArea", "watchlist#1"),
            ("fundamentals", "fundamentals#1", "BottomDockWidgetArea", "chart#1"),
            ("analyst", "analyst#1", "CenterDockWidgetArea", "fundamentals#1"),
            ("news", "news#1", "RightDockWidgetArea", None),
        ],
        "splits": [
            # outer: watchlist + centre | news
            (
                [
                    ["watchlist#1", "chart#1", "fundamentals#1", "analyst#1"],
                    ["news#1"],
                ],
                [78, 22],
            ),
            # watchlist | centre column (weights are relative to the parent
            # split: 19% of the 78% region lands it at ~15% of the window)
            (
                [["watchlist#1"], ["chart#1", "fundamentals#1", "analyst#1"]],
                [19, 81],
            ),
            # centre: chart over the tabbed tables
            ([["chart#1"], ["fundamentals#1", "analyst#1"]], [58, 42]),
        ],
    },
]


def _clear_symbols() -> None:
    """Empty the symbol context before a desk seeds its own.

    ``SymbolContext`` is a process-wide singleton and ``from_json`` merges
    rather than replaces, so without this one desk's seed symbol would leak
    into the next desk's ``symbols`` map. Each desk gets a fresh window, but
    not a fresh process — this is the one piece of state that outlives it.
    """
    from aurantium.symbol_context import SymbolContext

    SymbolContext.instance()._symbols.clear()


def _dock_names(widget, QtAds) -> set[str]:
    """Every dock instance id living under ``widget``."""
    areas = (
        [widget]
        if isinstance(widget, QtAds.CDockAreaWidget)
        else widget.findChildren(QtAds.CDockAreaWidget)
    )
    return {
        area.dockWidget(i).objectName()
        for area in areas
        for i in range(area.dockWidgetsCount())
    }


def _apply_splits(win, QtAds, app, desk: dict) -> None:
    """Size each splitter by what its panels are for, not QtAds' even split.

    A splitter is identified by the set of dock instances on each side, so the
    spec stays readable and breaks loudly if a desk's panel list changes
    without its splits being updated.
    """
    from PySide6.QtWidgets import QSplitter

    # Let Qt lay the freshly-added docks out first: until it has, every
    # splitter reports zero extent and setSizes() would be a no-op.
    app.processEvents()

    for sides, weights in desk.get("splits", []):
        wanted = [set(side) for side in sides]
        target = None
        for splitter in win.dock_manager.findChildren(QSplitter):
            if splitter.count() != len(wanted):
                continue
            actual = [
                _dock_names(splitter.widget(i), QtAds)
                for i in range(splitter.count())
            ]
            if actual == wanted:
                target = splitter
                break
        if target is None:
            raise RuntimeError(
                f"{desk['name']}: no splitter matches {sides!r} — the panel "
                f"list and the splits spec have diverged"
            )
        extent = sum(target.sizes())
        if not extent:
            raise RuntimeError(
                f"{desk['name']}: splitter for {sides!r} has zero extent — the "
                f"window must be shown() before sizing"
            )
        scale = extent / sum(weights)
        target.setSizes([round(w * scale) for w in weights])
        app.processEvents()


def build_desk(win, QtAds, app, desk: dict) -> dict:
    from aurantium.symbol_context import DEFAULT_GROUP, SymbolContext

    # Seed before the panels exist: add_panel syncs each new panel to the
    # group's current symbol as it is created, so the arrangement, the
    # per-panel settings and the serialized "symbols" map all agree.
    symbol = desk.get("symbol")
    if symbol:
        SymbolContext.instance().set_symbol(DEFAULT_GROUP, symbol)

    for panel_id, instance_id, area_name, target_instance in desk["panels"]:
        area = getattr(QtAds.DockWidgetArea, area_name)
        dock = win.add_panel(
            panel_id,
            instance_id=instance_id,
            area=area,
            target_instance=target_instance,
        )
        if dock is None:
            raise RuntimeError(
                f"add_panel failed for panel_id={panel_id!r} instance_id="
                f"{instance_id!r} while building desk {desk['name']!r}"
            )

    _apply_splits(win, QtAds, app, desk)

    doc = win.serialize_layout()
    doc["name"] = desk["name"]
    doc["description"] = desk["description"]
    return doc


def _generate_one(desk: dict) -> None:
    """Build one desk in this process and write its file."""
    from PySide6.QtWidgets import QApplication
    import PySide6QtAds as QtAds

    from aurantium.panel import discover_panels
    from aurantium.providers import register_all_providers

    app = QApplication.instance() or QApplication([])

    register_all_providers()
    errors = discover_panels([], packages=("aurantium.panels",))
    for err in errors:
        print(f"[generate_presets] panel failed to load:\n{err}", file=sys.stderr)

    from aurantium.app import MainWindow

    PRESETS_DIR.mkdir(parents=True, exist_ok=True)

    win = MainWindow()
    # Sizing needs a laid-out window: an unrealized one reports zero extent for
    # every splitter, so setSizes() would silently do nothing. The size is only
    # a reference frame — QtAds stores proportions and rescales on restore.
    win.resize(1600, 900)
    win.show()
    app.processEvents()

    _clear_symbols()
    doc = build_desk(win, QtAds, app, desk)

    for spec in doc["panels"]:
        assert spec["link_group"] == "A", (
            f"{desk['name']}: {spec['instance']} has link_group "
            f"{spec['link_group']!r}, expected 'A'"
        )
    want = desk["symbol"].upper()
    assert doc["symbols"] == {"A": want}, (
        f"{desk['name']}: expected symbols {{'A': {want!r}}}, got "
        f"{doc['symbols']!r} — a desk must seed exactly its own symbol"
    )

    out_path = PRESETS_DIR / desk["file"]
    out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path.relative_to(APP_ROOT)}  ({desk['name']!r})")
    win.hide()


def main() -> None:
    """Generate every desk, each in its own subprocess.

    Building all three in one process crashed intermittently — roughly one run
    in four, and always partway, leaving some desks written and the rest stale.
    The crash lives in state shared between desks (QtAds tears docks down
    asynchronously, and a MainWindow is not built to be created or emptied
    repeatedly), so the fix is to share nothing: one desk, one process.

    A crashed child is a loud failure here rather than a silent partial
    generation, which is the property that actually matters.
    """
    if len(sys.argv) > 1:
        target = sys.argv[1]
        desk = next((d for d in DESKS if d["file"] == target), None)
        if desk is None:
            raise SystemExit(f"unknown desk file: {target}")
        _generate_one(desk)
        return

    import subprocess

    script = str(Path(__file__).resolve())
    failures = []
    for desk in DESKS:
        result = subprocess.run([sys.executable, script, desk["file"]])
        if result.returncode != 0:
            failures.append((desk["name"], result.returncode))
            print(
                f"[generate_presets] FAILED {desk['name']!r} "
                f"(exit {result.returncode})",
                file=sys.stderr,
            )
    if failures:
        raise SystemExit(
            "generation failed for: "
            + ", ".join(f"{n} (exit {c})" for n, c in failures)
        )


if __name__ == "__main__":
    main()
