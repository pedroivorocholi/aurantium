"""The chart indicator palette must be legible and colour-blind-safe.

aurantium ships a colour-blind mode for up/down ticks, so a chart palette that
collapses under deuteranopia is a broken promise, not a nitpick. These are the
computable checks — run, never eyeballed — against each theme's real surface.

The palette is per theme for the same reason ``UP``/``DOWN`` are: one set of
hues cannot clear 3:1 against both true black and white. A colour light enough
to read on ``#000000`` is too pale on ``#ffffff``.
"""

import pytest

from palette_checks import (
    CVD_FLOOR,
    NORMAL_FLOOR,
    check,
    contrast,
    cvd_separation,
    delta_e,
    oklch,
)

#: The surface each theme's chart actually draws on (theme.BG).
SURFACES = {"dark": "#000000", "light": "#ffffff"}


def _palette(theme: str):
    from aurantium.panels.chart import indicator_palette

    return indicator_palette(theme)


# -- the port is trustworthy ------------------------------------------------

def test_the_port_reproduces_the_reference_validator():
    """Cross-check against a number produced by the reference implementation:
    the palette aurantium used to ship collapsed at ΔE 3.2 under deuteranopia
    for #8bc34a↔#ff7043. If the port drifts, this catches it before the port
    starts certifying bad palettes as good."""
    assert cvd_separation("#8bc34a", "#ff7043") == pytest.approx(3.2, abs=0.1)
    assert delta_e("#8bc34a", "#ff7043") == pytest.approx(25.5, abs=0.1)
    assert contrast("#f8e71c", "#ffffff") == pytest.approx(1.28, abs=0.01)


# -- the shipped palettes ---------------------------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_palette_passes_every_computable_check(theme):
    report = check(
        _palette(theme), mode=theme, surface=SURFACES[theme], pairs="adjacent"
    )
    assert not report["failures"], (
        f"{theme} indicator palette: " + "; ".join(report["failures"])
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_palette_marks_are_visible_on_their_surface(theme):
    """A line the user cannot see is not a chart series. The yellow that used
    to ship sat at 1.28:1 on the light theme's white canvas."""
    for color in _palette(theme):
        assert contrast(color, SURFACES[theme]) >= 3.0, (
            f"{color} is {contrast(color, SURFACES[theme]):.2f}:1 on {theme}"
        )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_no_indicator_can_be_mistaken_for_a_reserved_color(theme):
    """UP, DOWN and ACCENT carry fixed meanings — gain, loss, and "this is the
    price". An indicator line close enough to be confused with one of them
    reads as a signal it isn't. The validator can't catch this; it doesn't know
    which colours are spoken for."""
    from aurantium import theme as theme_mod

    reserved = {
        "UP": theme_mod.palette_colors(theme)["UP"],
        "DOWN": theme_mod.palette_colors(theme)["DOWN"],
        "ACCENT": theme_mod.palette_colors(theme)["ACCENT"],
    }
    for color in _palette(theme):
        for name, other in reserved.items():
            assert delta_e(color, other) >= NORMAL_FLOOR, (
                f"{theme}: indicator {color} is ΔE "
                f"{delta_e(color, other):.1f} from {name} ({other})"
            )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_pair_is_at_least_at_the_cvd_floor(theme):
    """Indicator lines overlay one another and cross, so any two can end up
    side by side — not just neighbours in the assignment order. Adjacent pairs
    must clear the target; every other pair must at minimum clear the floor,
    which the labelled colour chip above the chart legitimises as secondary
    encoding."""
    palette = _palette(theme)
    for i, a in enumerate(palette):
        for b in palette[i + 1:]:
            assert cvd_separation(a, b) >= CVD_FLOOR, (
                f"{theme}: {a}↔{b} ΔE {cvd_separation(a, b):.1f}"
            )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_palette_is_capped_at_what_stays_distinguishable(theme):
    """Hue alone cannot separate an unbounded number of series under CVD. The
    palette is deliberately short; a chart with more indicators than this
    reuses colours and leans on the labelled chips."""
    assert 4 <= len(_palette(theme)) <= 6


def test_the_two_themes_stay_in_step():
    """Slot N means the same series in both themes, so a user switching theme
    sees their SMA 50 keep its identity rather than swap with the SMA 200."""
    assert len(_palette("dark")) == len(_palette("light"))


# -- the defaults a fresh chart opens with ----------------------------------

@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_default_indicators_take_the_first_slots(theme):
    """SMA 50 / SMA 200 / RSI are what every chart opens with, so they must be
    the best-separated three, not whatever the cycle happened to land on."""
    from aurantium.panels.chart import default_indicator_colors

    defaults = default_indicator_colors(theme)
    assert defaults == tuple(_palette(theme)[:3])
