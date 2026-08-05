"""Preset workspaces: only well-formed presets that reference real panels
ever reach the Workspaces menu. A preset naming a panel that no longer
exists is rejected whole — a half-built desk is worse than no entry."""

import json

from aurantium.presets import Preset, available_presets, parse_preset

KNOWN = {"chart", "macro", "news"}


def _doc(**over):
    doc = {
        "version": 1,
        "name": "Macro Desk",
        "description": "Rates, inflation and positioning.",
        "panels": [
            {
                "instance": "macro#1",
                "panel_id": "macro",
                "link_group": "A",
                "settings": {},
            }
        ],
        "ads_state": "00",
    }
    doc.update(over)
    return doc


def test_parses_a_valid_preset():
    preset = parse_preset(json.dumps(_doc()), KNOWN)
    assert isinstance(preset, Preset)
    assert preset.name == "Macro Desk"
    assert preset.description == "Rates, inflation and positioning."
    assert preset.doc["panels"][0]["panel_id"] == "macro"


def test_rejects_invalid_json():
    assert parse_preset("{not json", KNOWN) is None


def test_rejects_non_dict_top_level():
    assert parse_preset("[1, 2, 3]", KNOWN) is None


def test_rejects_unknown_panel_id():
    doc = _doc(panels=[{"instance": "x#1", "panel_id": "no_such_panel"}])
    assert parse_preset(json.dumps(doc), KNOWN) is None


def test_rejects_missing_or_blank_name():
    assert parse_preset(json.dumps(_doc(name="")), KNOWN) is None
    assert parse_preset(json.dumps(_doc(name="   ")), KNOWN) is None
    no_name = _doc()
    del no_name["name"]
    assert parse_preset(json.dumps(no_name), KNOWN) is None


def test_rejects_empty_panel_list():
    assert parse_preset(json.dumps(_doc(panels=[])), KNOWN) is None


def test_description_defaults_to_empty_string():
    no_desc = _doc()
    del no_desc["description"]
    preset = parse_preset(json.dumps(no_desc), KNOWN)
    assert preset is not None
    assert preset.description == ""


def test_available_presets_skips_bad_files_and_sorts_by_name(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps(_doc(name="Zulu Desk")), encoding="utf-8")
    (tmp_path / "a.json").write_text(json.dumps(_doc(name="Alpha Desk")), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("not a preset", encoding="utf-8")

    presets = available_presets(directory=tmp_path, known_panel_ids=KNOWN)

    assert [p.name for p in presets] == ["Alpha Desk", "Zulu Desk"]


def test_available_presets_returns_empty_when_dir_missing(tmp_path):
    missing = tmp_path / "nope"
    assert available_presets(directory=missing, known_panel_ids=KNOWN) == []
