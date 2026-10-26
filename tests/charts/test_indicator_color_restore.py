"""Restoring indicator colours across a theme switch.

Saved chart state carries two kinds of colour and, until now, treated them the
same: pass the string straight back to ``_add_indicator``.

**Theme defaults were not remapped.** The ``colors`` block (up/down/line/grid/
bg) has had theme-default detection since the palette became theme-aware, so a
black canvas becomes white when you switch. ``indicators`` never got it, so an
indicator drawn in the dark palette's ``#0c699a`` stayed ``#0c699a`` on the
light theme — a slot value that was never validated against white.

**Custom colours were never re-checked.** The picker rejects an illegible
colour against the canvas *at the moment of picking*, so anything chosen on
black is unexamined forever after. This was live, not hypothetical: the saved
layout on the development machine held SMA 200 at ``#f8e71c`` — 1.28:1 on white,
the exact value and ratio ``chart.py``'s own comment documents as rejected — and
volume at ``#18d90a`` at 1.91:1. Two of four indicator lines were invisible the
moment the light theme was selected.

The rule these tests pin: remap what the palette owns, replace what cannot be
seen, and leave everything else alone.
"""

import importlib

import pytest

from aurantium.color import contrast

#: The mark-contrast floor the rest of the colour system uses.
MARK_MIN = 3.0

#: The real saved layout that exposed this, verbatim.
SAVED = [
    ("sma", "#e910c1"),   # magenta — legible on both
    ("sma", "#f8e71c"),   # yellow  — 1.28:1 on white
    ("sma", "#0769e9"),   # blue    — legible on both
    ("volume", "#18d90a"),  # green — 1.91:1 on white
]


@pytest.fixture(params=["dark", "light"])
def chart(request, monkeypatch, qapp):
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw:
            request.param if key == "ui/theme" else
            (False if key == "ui/colorblind" else default)
    )
    theme = importlib.reload(importlib.import_module("aurantium.theme"))
    module = importlib.reload(importlib.import_module("aurantium.panels.chart"))
    module._test_theme = theme
    yield module
    monkeypatch.undo()
    importlib.reload(importlib.import_module("aurantium.theme"))
    importlib.reload(importlib.import_module("aurantium.panels.chart"))


def test_no_restored_indicator_is_invisible(chart):
    """The whole point. Whatever a layout holds, nothing is drawn below the
    mark-contrast floor on the canvas it is actually drawn on."""
    bg = chart._test_theme.BG
    for i, (_kind, saved) in enumerate(SAVED):
        out, _ = chart._restored_indicator_color(saved, i, bg)
        assert contrast(out, bg) >= MARK_MIN, (
            f"{saved} restored as {out}, {contrast(out, bg):.2f}:1 on {bg}"
        )


def test_a_legible_custom_colour_is_left_alone(chart):
    """Minimal intervention. A colour the user picked that still works is their
    colour, on either theme."""
    bg = chart._test_theme.BG
    for saved in ("#e910c1", "#0769e9"):
        out, swapped = chart._restored_indicator_color(saved, 0, bg)
        assert out == saved
        assert swapped is False


def test_only_the_light_theme_needs_substitutions(chart):
    """These four were all chosen on black, so on the dark theme nothing should
    move. A fix that repainted a working chart would be its own bug."""
    bg = chart._test_theme.BG
    swaps = [
        chart._restored_indicator_color(c, i, bg)[1]
        for i, (_k, c) in enumerate(SAVED)
    ]
    if chart._test_theme.current_theme() == "dark":
        assert not any(swaps), "dark theme should not substitute anything"
    else:
        assert sum(swaps) == 2, "the two sub-3:1 colours should be substituted"


def test_a_palette_colour_follows_its_slot_across_themes(chart):
    """Slot-indexed, not positional: SMA 50 keeps its identity through a theme
    switch instead of being handed whatever hex happens to be next."""
    dark_pal = chart._INDICATOR_PALETTES["dark"]
    light_pal = chart._INDICATOR_PALETTES["light"]
    active = chart.indicator_palette()
    for slot, dark_hex in enumerate(dark_pal):
        out, swapped = chart._restored_indicator_color(dark_hex, 0, chart._test_theme.BG)
        assert out == active[slot], f"dark slot {slot} did not remap to slot {slot}"
        assert swapped is False, "a theme remap is not a substitution"
    # ...and the same from the other direction.
    for slot, light_hex in enumerate(light_pal):
        out, _ = chart._restored_indicator_color(light_hex, 0, chart._test_theme.BG)
        assert out == active[slot]


def test_the_two_palettes_do_not_share_a_hex_at_different_slots(chart):
    """Precondition for the remap above: if one hex appeared at slot 1 in dark
    and slot 3 in light, ``_SLOT_OF_INDICATOR_COLOR`` would silently pick one."""
    seen: dict[str, int] = {}
    for name in ("dark", "light"):
        for slot, hex_ in enumerate(chart._INDICATOR_PALETTES[name]):
            key = hex_.lower()
            assert seen.get(key, slot) == slot, f"{hex_} occupies two slots"
            seen[key] = slot


def test_garbage_is_rejected_rather_than_drawn(chart):
    """A corrupt or hand-edited layout should not reach the plot."""
    for junk in (None, "", "not-a-colour", 42, {}):
        out, swapped = chart._restored_indicator_color(junk, 0, chart._test_theme.BG)
        assert out is None
        assert swapped is False
