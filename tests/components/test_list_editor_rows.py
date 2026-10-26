"""Round-trip and validation tests for the rewritten list editor.

Everything runs offscreen; no dialog is ever exec()'d — rows are read back
through the same code path OK uses.
"""

import pytest

from aurantium.components.list_editor import (
    EditorColumn,
    EditorSection,
    ListEditorDialog,
)

pytestmark = pytest.mark.usefixtures("qapp")


def _dialog(sections):
    return ListEditorDialog("Test", sections)


def test_typed_round_trip_mixed_columns():
    section = EditorSection(
        "tenors",
        "Yield Curve",
        [
            EditorColumn("Maturity (yrs)", kind="number", maximum=100.0),
            EditorColumn("Label"),
            EditorColumn("Symbol", kind="symbol"),
        ],
        [[0.25, "3M", "^IRX"], [10.0, "10Y", "^TNX"]],
    )
    dlg = _dialog([section])
    assert dlg.results() == {"tenors": [[0.25, "3M", "^IRX"], [10.0, "10Y", "^TNX"]]}


def test_choice_round_trip_keeps_stored_value():
    section = EditorSection(
        "inst",
        "Instruments",
        [
            EditorColumn("Label"),
            EditorColumn("Source", kind="choice", choices=[("quote", "Quote"), ("fred", "FRED")]),
            EditorColumn("Code", kind="symbol"),
        ],
        [["Dollar Index", "quote", "DX-Y.NYB"], ["Real Yield", "fred", "DFII10"]],
    )
    dlg = _dialog([section])
    assert dlg.results()["inst"] == [
        ["Dollar Index", "quote", "DX-Y.NYB"],
        ["Real Yield", "fred", "DFII10"],
    ]


def test_symbols_uppercased():
    section = EditorSection(
        "s", "Symbols", [EditorColumn("Symbol", kind="symbol")], [["gc=f"]]
    )
    dlg = _dialog([section])
    assert dlg.results()["s"] == [["GC=F"]]


def test_blank_label_falls_back_to_symbol():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["", "CL=F"]],
    )
    dlg = _dialog([section])
    assert dlg.results()["rows"] == [["CL=F", "CL=F"]]


def test_empty_symbol_is_structural_error_and_blocks_ok():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["Gold", ""]],
    )
    dlg = _dialog([section])
    assert not dlg.ok_enabled()


def test_valid_rows_enable_ok():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["Gold", "GC=F"]],
    )
    dlg = _dialog([section])
    assert dlg.ok_enabled()


def test_add_and_remove_rows_programmatically():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["Gold", "GC=F"]],
    )
    dlg = _dialog([section])
    widget = dlg.section_widget("rows")
    widget.add_row(["Silver", "SI=F"])
    assert dlg.results()["rows"] == [["Gold", "GC=F"], ["Silver", "SI=F"]]
    widget.remove_row(0)
    assert dlg.results()["rows"] == [["Silver", "SI=F"]]


def test_move_row():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["A", "GC=F"], ["B", "SI=F"], ["C", "HG=F"]],
    )
    dlg = _dialog([section])
    dlg.section_widget("rows").move_row(0, 2)
    assert [r[0] for r in dlg.results()["rows"]] == ["B", "C", "A"]


def test_preset_adds_rows_without_duplicates():
    section = EditorSection(
        "rows",
        "Rows",
        [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
        [["Gold", "GC=F"]],
        presets=[("Metals", [["Gold", "GC=F"], ["Silver", "SI=F"]])],
    )
    dlg = _dialog([section])
    dlg.section_widget("rows").apply_preset(0)
    assert dlg.results()["rows"] == [["Gold", "GC=F"], ["Silver", "SI=F"]]
