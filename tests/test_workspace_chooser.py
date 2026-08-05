"""First-run chooser: picking an entry returns that preset; dismissing
returns None so the user lands on the empty workspace as before."""

import pytest

pytestmark = pytest.mark.usefixtures("qapp")


def _presets():
    from aurantium.presets import Preset

    return [
        Preset(name="Alpha Desk", description="First.", doc={"panels": [{"panel_id": "chart"}]}),
        Preset(name="Beta Desk", description="Second.", doc={"panels": [{"panel_id": "news"}]}),
    ]


def test_lists_every_preset():
    from aurantium.workspace_chooser import WorkspaceChooser

    dlg = WorkspaceChooser(_presets())
    labels = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    assert labels == ["Alpha Desk", "Beta Desk"]


def test_chosen_returns_the_selected_preset():
    from aurantium.workspace_chooser import WorkspaceChooser

    presets = _presets()
    dlg = WorkspaceChooser(presets)
    dlg._list.setCurrentRow(1)
    assert dlg.chosen() is presets[1]


def test_chosen_is_none_when_nothing_selected():
    from aurantium.workspace_chooser import WorkspaceChooser

    dlg = WorkspaceChooser(_presets())
    dlg._list.setCurrentRow(-1)
    assert dlg.chosen() is None


def test_first_row_preselected_so_accepting_always_yields_a_preset():
    from aurantium.workspace_chooser import WorkspaceChooser

    presets = _presets()
    dlg = WorkspaceChooser(presets)
    assert dlg.chosen() is presets[0]
