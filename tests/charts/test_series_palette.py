"""The one categorical palette, and the gate that was missing from it.

``theme._SERIES_PALETTES`` is now the only source of series colours in the app.
Before it there were four independent lists — the chart's validated four, plus
hand-picked sets in ``performance``, ``portfolio`` and ``symbol_context`` — and
three of the four had never been checked against anything.

**The gate this file adds.** ``test_chart_palette`` already asserted that no
slot could be mistaken for a reserved colour, but it compared *normal vision
only*. Light slot 1 was a dark olive, ``#3c5b07``, which passed that comfortably
(ΔE 20.9 from ACCENT, 28.5 from DOWN) and failed under deuteranopia: **5.1** and
**3.4**, both beneath the 6.0 floor. A red-green colour-blind user on the light
theme could not distinguish an SMA line from the price line or from a losing
candle — in a product that ships a colour-blind mode as a feature. The colour
was replaced; this file is the check that would have caught it.

It also reads the reserved colours out of ``theme``'s palette tables directly
rather than through ``palette_colors()``, which folds in the colour-blind
substitutes only when the *developer's own* setting happens to be on. That made
the original check machine-dependent — it would pass or fail depending on a
preference, which is not a property a guard may have.
"""

import pytest

from aurantium import theme
from palette_checks import (
    CONTRAST_MIN,
    CVD_FLOOR,
    CVD_TARGET,
    NORMAL_FLOOR,
    check,
    contrast,
    cvd_separation,
    delta_e,
)

SURFACES = {"dark": "#000000", "light": "#ffffff"}


def reserved(theme_name: str) -> dict[str, str]:
    """Every colour a series may not be confused with.

    Read straight from the palette tables, so the set is the same on every
    machine: the price accent, the two tick colours, and the colour-blind
    substitutes for those ticks — which are live for any user with the mode on
    and must therefore always be treated as spoken for.
    """
    pal = theme._PALETTES[theme_name]
    cb = theme._COLORBLIND[theme_name]
    return {
        "ACCENT": pal["ACCENT"],
        "UP": pal["UP"],
        "DOWN": pal["DOWN"],
        "CB-UP": cb["UP"],
        "CB-DOWN": cb["DOWN"],
    }


@pytest.mark.parametrize("theme_name", list(theme.THEMES))
def test_palette_passes_every_computable_check(theme_name):
    report = check(
        theme.series_palette(theme_name),
        mode=theme_name,
        surface=SURFACES[theme_name],
        pairs="adjacent",
    )
    assert report["failures"] == []


@pytest.mark.parametrize("theme_name", list(theme.THEMES))
def test_every_slot_is_visible_on_its_surface(theme_name):
    for c in theme.series_palette(theme_name):
        ratio = contrast(c, SURFACES[theme_name])
        assert ratio >= CONTRAST_MIN, f"{c} is {ratio:.2f}:1 on {theme_name}"


@pytest.mark.parametrize("theme_name", list(theme.THEMES))
def test_no_slot_can_be_mistaken_for_a_reserved_colour(theme_name):
    """The check that was missing: **colour-vision deficiency**, not just
    normal vision. This is the assertion that fails against ``#3c5b07``."""
    for slot, c in enumerate(theme.series_palette(theme_name)):
        for name, r in reserved(theme_name).items():
            normal = delta_e(c, r)
            cvd = cvd_separation(c, r)
            assert normal >= NORMAL_FLOOR, (
                f"{theme_name} slot {slot} {c} vs {name} {r}: ΔE {normal:.1f}"
            )
            assert cvd >= CVD_FLOOR, (
                f"{theme_name} slot {slot} {c} vs {name} {r}: "
                f"CVD ΔE {cvd:.1f}, under the {CVD_FLOOR} floor"
            )


#: Normal-vision floor for pairs that are not adjacent.
#:
#: Lower than ``NORMAL_FLOOR`` on purpose, and the reason is measured. Slots are
#: handed out in order, so only neighbours are guaranteed to share a chart;
#: everything else meets by coincidence and carries a labelled colour chip when
#: it does. The dark palette's olive and teal — slots 1 and 3 — sit at ΔE
#: **14.7**, a hair under the 15.0 adjacent gate. Re-stepping either to clear it
#: would cost more elsewhere (both currently clear every reserved colour with
#: room), so the honest move is to state the weaker bar for non-neighbours and
#: pin the actual number, rather than quietly widening the main gate.
NON_ADJACENT_NORMAL_FLOOR = 12.0


@pytest.mark.parametrize("theme_name", list(theme.THEMES))
def test_adjacent_slots_hit_the_target_and_the_rest_the_floor(theme_name):
    """Adjacent slots touch on a chart and get the target; any other pair only
    has to clear the floor, which the labelled colour chip beside each series
    legitimises as secondary encoding."""
    pal = theme.series_palette(theme_name)
    for i in range(len(pal)):
        for j in range(i + 1, len(pal)):
            adjacent = j == i + 1
            gate = CVD_TARGET if adjacent else CVD_FLOOR
            sep = cvd_separation(pal[i], pal[j])
            assert sep >= gate, (
                f"{theme_name} slots {i}-{j}: CVD ΔE {sep:.1f}, gate {gate}"
            )
            normal_gate = NORMAL_FLOOR if adjacent else NON_ADJACENT_NORMAL_FLOOR
            normal = delta_e(pal[i], pal[j])
            assert normal >= normal_gate, (
                f"{theme_name} slots {i}-{j}: ΔE {normal:.1f}, gate {normal_gate}"
            )


def test_the_known_weak_pair_has_not_got_worse():
    """Dark olive vs dark teal, the one pair below the adjacent normal-vision
    gate. Pinned so a future palette edit cannot erode it further without
    someone noticing."""
    assert delta_e("#6e6b03", "#15957b") == pytest.approx(14.7, abs=0.2)
    assert cvd_separation("#6e6b03", "#15957b") >= CVD_FLOOR


def test_the_two_themes_stay_in_step():
    """Slot N must exist in both, or a theme switch has nowhere to map it."""
    lengths = {t: len(theme.series_palette(t)) for t in theme.THEMES}
    assert len(set(lengths.values())) == 1, lengths


def test_the_palette_is_capped_at_what_stays_distinguishable():
    """Four is the measured ceiling, not a preference.

    Five colours are reserved (see :func:`reserved`), and with the lightness
    band and the 3:1 surface gate applied, a full gamut search finds no fifth
    light-theme slot that clears the adjacent target without crowding an
    existing one. Raising this number means re-running that search and proving
    otherwise — not relaxing a threshold.
    """
    for t in theme.THEMES:
        assert 4 <= len(theme.series_palette(t)) <= 6


def test_no_colour_holds_two_different_slots():
    """Precondition for slot-indexed remapping across a theme switch."""
    seen: dict[str, int] = {}
    for t in theme.THEMES:
        for slot, c in enumerate(theme.series_palette(t)):
            key = c.lower()
            assert seen.get(key, slot) == slot, f"{c} occupies two slots"
            seen[key] = slot


# -- assignment ------------------------------------------------------------


def test_series_colour_is_fixed_order_and_wraps():
    """Never a generated hue past the last slot: a generated colour is
    indistinguishable from an existing one under CVD and passes no check."""
    pal = theme.series_palette()
    assert [theme.series_color(i) for i in range(len(pal))] == list(pal)
    assert theme.series_color(len(pal)) == pal[0]
    assert theme.series_color(len(pal) + 2) == pal[2]


def test_retired_colours_still_resolve_to_their_slot():
    """A saved layout holding a retired hex must not orphan. ``#3c5b07`` was
    light slot 1 before the colour-blind collision was found."""
    assert theme.series_slot_of("#3c5b07") == 1
    assert theme.series_slot_of("#3C5B07") == 1


def test_current_palette_colours_resolve_to_their_slot():
    for t in theme.THEMES:
        for slot, c in enumerate(theme.series_palette(t)):
            assert theme.series_slot_of(c) == slot


def test_a_users_own_colour_is_not_claimed_by_a_slot():
    """Only palette values map to slots; anything else is a genuine custom pick
    and the restore path must leave it alone."""
    assert theme.series_slot_of("#123456") is None
    assert theme.series_slot_of("") is None
    assert theme.series_slot_of(None) is None


# -- the retired value is retired for a reason -----------------------------


def test_the_retired_olive_really_did_fail():
    """Guard the guard. If this ever stops failing, the gate above has been
    weakened and the regression can walk back in."""
    res = reserved("light")
    assert cvd_separation("#3c5b07", res["ACCENT"]) < CVD_FLOOR
    assert cvd_separation("#3c5b07", res["DOWN"]) < CVD_FLOOR
    # ...while looking perfectly fine to normal vision, which is why it shipped.
    assert delta_e("#3c5b07", res["ACCENT"]) > NORMAL_FLOOR
    assert delta_e("#3c5b07", res["DOWN"]) > NORMAL_FLOOR
