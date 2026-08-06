"""Curated starter workspaces shipped with the app.

A preset is an ordinary layout document — the same shape
``MainWindow.serialize_layout()`` produces — with two extra keys, ``name`` and
``description``, stored as JSON under ``layouts/presets/``.

Presets are *generated*, never hand-written and never hand-captured from a
running app: ``tools/generate_presets.py`` drives a real dock manager
headlessly to build each desk, so the bundled file carries a genuine
``ads_state`` and restores the intended arrangement instead of QtAds' default
tiling. To change a desk — its panels, their arrangement or its seed symbol —
edit the ``DESKS`` table in that script and re-run it; do not edit the JSON
under ``layouts/presets/`` by hand.

Validation is all-or-nothing per file: a preset naming a panel that no longer
exists is dropped entirely rather than loaded half-built. Nothing here may
raise: ``available_presets()`` runs during ``MainWindow.__init__``, so an
unreadable or corrupt file must be skipped, never propagated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .paths import BUNDLE_DIR

PRESETS_DIR = BUNDLE_DIR / "layouts" / "presets"


@dataclass(frozen=True)
class Preset:
    """One shipped workspace: a display name, a one-line blurb, and the
    layout document handed straight to ``MainWindow.apply_layout``."""

    name: str
    description: str
    doc: dict


def _registered_panel_ids() -> set[str]:
    from .panel import PanelRegistry

    return {meta.id for meta in PanelRegistry.all()}


def parse_preset(raw: str, known_panel_ids: set[str]) -> Preset | None:
    """Parse one preset file's text. Returns ``None`` if it is unusable."""
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, dict):
        return None

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    panels = doc.get("panels")
    if not isinstance(panels, list) or not panels:
        return None
    for spec in panels:
        if not isinstance(spec, dict):
            return None
        if spec.get("panel_id") not in known_panel_ids:
            return None

    description = doc.get("description")
    return Preset(
        name=name.strip(),
        description=description.strip() if isinstance(description, str) else "",
        doc=doc,
    )


def available_presets(
    directory: Path | None = None,
    known_panel_ids: set[str] | None = None,
) -> list[Preset]:
    """Every shipped preset that parses and references only registered panels,
    sorted by display name."""
    directory = PRESETS_DIR if directory is None else directory
    if known_panel_ids is None:
        known_panel_ids = _registered_panel_ids()

    presets: list[Preset] = []
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return presets

    for path in files:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A truncated download or a disk-corrupted byte must cost the user
            # one workspace, not the whole window: this runs inside
            # MainWindow.__init__, where an exception means no app at all.
            continue
        preset = parse_preset(raw, known_panel_ids)
        if preset is not None:
            presets.append(preset)

    presets.sort(key=lambda p: p.name.lower())
    return presets
