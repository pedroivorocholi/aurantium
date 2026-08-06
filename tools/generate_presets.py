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
a desk is renamed/removed (then update the desk definitions below and
re-run). It is deterministic and safe to re-run: it always rebuilds each
desk from an empty workspace (docks *and* symbol context) and overwrites its
file. Two consecutive runs, and a run with the desks in any order, produce
byte-identical files.

Usage (from ``app/``):

    .venv/Scripts/python.exe tools/generate_presets.py
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
# Sizing rule — the two placements behave very differently, and getting this
# wrong is invisible until you measure the restored layout:
#
#   * target_instance=None (global) splits the *entire container* 50/50, so
#     the new panel takes half the window and everything already placed
#     shares the other half.
#   * target_instance="foo#1" joins the splitter that already holds foo#1,
#     and the columns in that splitter end up evenly divided.
#
# So each desk makes exactly ONE global placement, and it is the desk's
# analytical centrepiece, placed LAST — that is what makes it the widest
# panel. Every supporting panel is placed against an existing instance and
# lands narrower. Placing a supporting panel globally (as `news` once was)
# hands it half the window and squeezes the centrepiece into a quarter.
#
# Measured by restoring each preset into a shown window, at both 1600x900 and
# 2560x1440 (identical at both, so structural rather than pixel accidents).
# Each desk restores into THREE columns:
#   Macro Desk        chart 50% · macro+cot_history 25% · news 25%
#   Commodities Desk  chart 50% · futures_curve+cot_history 25% · commodities 25%
#   Equity Research   chart 50% · watchlist+fundamentals+analyst 25% · news 25%
#
# Re-measure after changing this table. Two traps, both hit for real while
# writing this:
#   * The window must be shown() first. An unrealized window reports zero width
#     for every dock area, which reads as a plausible 50/50 and hides the very
#     defect you are measuring for.
#   * Group by COLUMN (a dock area's x-origin), not by dock area. Two stacked
#     panels are two dock areas sharing one column; summing area widths counts
#     that column twice and understates the centrepiece (it reports 40/20 here
#     instead of the true 50/25).
#
# "symbol" seeds link group A before the panels are built, so the desk opens
# on real data instead of blank panels on a fresh install (SymbolContext is
# empty there, and an empty "symbols" map restores to nothing). It also gets
# baked into the panels' own saved settings — CL=F, for instance, switches
# futures_curve to Crude and cot_history to the crude_oil CFTC market.
DESKS = [
    {
        "file": "macro_desk.json",
        "name": "Macro Desk",
        "description": "Rates, inflation and positioning — the macro starting point.",
        "symbol": "^TNX",  # US 10-year yield
        "panels": [
            ("macro", "macro#1", "CenterDockWidgetArea", None),
            ("news", "news#1", "RightDockWidgetArea", "macro#1"),
            ("cot_history", "cot_history#1", "BottomDockWidgetArea", "macro#1"),
            ("chart", "chart#1", "RightDockWidgetArea", None),
        ],
    },
    {
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
    },
    {
        "file": "equity_research.json",
        "name": "Equity Research",
        "description": "Watchlist, chart and fundamentals for single-name work.",
        "symbol": "AAPL",
        "panels": [
            ("watchlist", "watchlist#1", "CenterDockWidgetArea", None),
            ("news", "news#1", "RightDockWidgetArea", "watchlist#1"),
            ("fundamentals", "fundamentals#1", "BottomDockWidgetArea", "watchlist#1"),
            ("analyst", "analyst#1", "CenterDockWidgetArea", "fundamentals#1"),
            ("chart", "chart#1", "RightDockWidgetArea", None),
        ],
    },
]


def _reset_to_empty(win) -> None:
    """Close every dock so the next desk starts from a blank workspace.

    Mirrors what ``MainWindow.apply_layout`` does before rebuilding: mass
    -close via ``closeDockWidget`` under the loading-layout guard, then clear
    the instance bookkeeping dicts.

    Also empties the symbol context. ``SymbolContext`` is a process-wide
    singleton and ``from_json`` merges rather than replaces, so without this
    one desk's seed symbol would leak into the next desk's ``symbols`` map.
    """
    from aurantium.symbol_context import SymbolContext

    win._loading_layout = True
    try:
        for dock in list(win._docks.values()):
            dock.closeDockWidget()
    finally:
        win._loading_layout = False
    win._docks.clear()
    win._maximize_actions.clear()
    SymbolContext.instance()._symbols.clear()


def build_desk(win, QtAds, desk: dict) -> dict:
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

    doc = win.serialize_layout()
    doc["name"] = desk["name"]
    doc["description"] = desk["description"]
    return doc


def main() -> None:
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

    win = MainWindow()

    PRESETS_DIR.mkdir(parents=True, exist_ok=True)

    for desk in DESKS:
        _reset_to_empty(win)
        doc = build_desk(win, QtAds, desk)
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

    _reset_to_empty(win)
    win.close()


if __name__ == "__main__":
    main()
