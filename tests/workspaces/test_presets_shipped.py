"""The presets we actually ship must load. This guards against a panel being
renamed or removed without its preset being updated — which would silently
drop a workspace from the menu."""

import pytest

from aurantium.presets import PRESETS_DIR, available_presets

pytestmark = pytest.mark.usefixtures("qapp")

EXPECTED = {"Commodities Desk", "Equity Research", "Macro Desk"}


def _registered_ids() -> set[str]:
    """Load the built-in panels exactly the way __main__ does at startup."""
    from aurantium.panel import PanelRegistry, discover_panels

    discover_panels([], packages=("aurantium.panels",))
    return {meta.id for meta in PanelRegistry.all()}


def test_presets_dir_exists():
    assert PRESETS_DIR.is_dir(), f"missing presets dir: {PRESETS_DIR}"


def test_every_shipped_preset_loads():
    names = {p.name for p in available_presets(known_panel_ids=_registered_ids())}
    assert EXPECTED <= names, f"presets failed validation: {EXPECTED - names}"


def test_every_shipped_preset_has_a_description():
    for preset in available_presets(known_panel_ids=_registered_ids()):
        assert preset.description, f"{preset.name} has no description"


def test_every_shipped_preset_carries_an_arrangement():
    """A preset without ads_state tiles by QtAds default, which defeats the
    purpose — these are captured from a real arrangement."""
    for preset in available_presets(known_panel_ids=_registered_ids()):
        assert preset.doc.get("ads_state"), f"{preset.name} has no ads_state"
