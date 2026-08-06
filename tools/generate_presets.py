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

Run it whenever a desk's arrangement changes, or after a panel referenced by
a desk is renamed/removed (then update the desk definitions below and
re-run). It is deterministic and safe to re-run: it always rebuilds each
desk from an empty workspace and overwrites its file.

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
DESKS = [
    {
        "file": "macro_desk.json",
        "name": "Macro Desk",
        "description": "Rates, inflation and positioning — the macro starting point.",
        "panels": [
            ("macro", "macro#1", "CenterDockWidgetArea", None),
            ("chart", "chart#1", "RightDockWidgetArea", "macro#1"),
            ("cot_history", "cot_history#1", "BottomDockWidgetArea", "macro#1"),
            ("news", "news#1", "RightDockWidgetArea", None),
        ],
    },
    {
        "file": "commodities_desk.json",
        "name": "Commodities Desk",
        "description": "Curves, prices and COT positioning for commodities.",
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
        "panels": [
            ("watchlist", "watchlist#1", "CenterDockWidgetArea", None),
            ("chart", "chart#1", "RightDockWidgetArea", "watchlist#1"),
            ("fundamentals", "fundamentals#1", "BottomDockWidgetArea", "chart#1"),
            ("analyst", "analyst#1", "CenterDockWidgetArea", "fundamentals#1"),
            ("news", "news#1", "RightDockWidgetArea", None),
        ],
    },
]


def _reset_to_empty(win) -> None:
    """Close every dock so the next desk starts from a blank workspace.

    Mirrors what ``MainWindow.apply_layout`` does before rebuilding: mass
    -close via ``closeDockWidget`` under the loading-layout guard, then clear
    the instance bookkeeping dicts.
    """
    win._loading_layout = True
    try:
        for dock in list(win._docks.values()):
            dock.closeDockWidget()
    finally:
        win._loading_layout = False
    win._docks.clear()
    win._maximize_actions.clear()


def build_desk(win, QtAds, desk: dict) -> dict:
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
        out_path = PRESETS_DIR / desk["file"]
        out_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {out_path.relative_to(APP_ROOT)}  ({desk['name']!r})")

    _reset_to_empty(win)
    win.close()


if __name__ == "__main__":
    main()
