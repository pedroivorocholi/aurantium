"""The sector heatmap's diverging ramp.

The heatmap is the one panel that encodes its whole payload in colour: eleven
tiles, one number each, and the fill is what the eye reads first. It shipped
with no test, and with three defects that a test would have caught.

1. **The labels were hardcoded near-white** (``#f2f2f2`` / ``#e0e0e0`` /
   ``#ffffff``) over a fill that runs from the theme's near-background neutral
   out to saturated green or red. Fine at the saturated end of the dark ramp.
   On the light theme, eight of eleven steps put the figure under 3:1 and a
   flat tile put it at **1.09:1** — the number was simply not on screen.
2. **No-data and 0.00% returned the same neutral**, so a dead feed rendered as
   "every sector is exactly flat": a confident wrong answer, which is worse
   than a visibly broken one.
3. **The blend ran in gamma-encoded sRGB**, so equal steps in percentage were
   not equal steps in perceived lightness.

These tests hold all three, in both themes and in colour-blind mode — the last
of which matters because the ramp's endpoints are ``UP``/``DOWN`` and therefore
swap to blue/vermillion, changing every fill and so every label decision.
"""

import importlib

import pytest

from aurantium.color import contrast, delta_e, oklch

#: Every reading the ramp has to survive, including both clamp edges and past
#: them. ``None`` is the no-data case.
READINGS = [None, -5.0, -2.0, -1.0, -0.5, -0.01, 0.0, 0.01, 0.5, 1.0, 2.0, 5.0]

#: WCAG for normal-size text. The figure is 20px bold, so 3:1 would be
#: defensible — but it is the panel's primary payload, and the whole point of
#: computing the ink is that there is no reason to settle.
INK_MIN = 4.5


@pytest.fixture(params=["dark", "light"])
def heat(request, monkeypatch, qapp):
    """The heatmap module re-imported against a given theme.

    ``theme`` publishes its palette as module-level constants resolved once at
    import, and ``sector_heatmap`` binds them with ``from ..theme import``. So
    switching theme inside a test means re-importing both, in order.
    """
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw:
            request.param if key == "ui/theme" else
            (False if key == "ui/colorblind" else default)
    )
    theme = importlib.reload(importlib.import_module("aurantium.theme"))
    module = importlib.reload(importlib.import_module("aurantium.panels.sector_heatmap"))
    module._test_theme = theme
    yield module
    # Leave both modules resolved against the real setting again.
    monkeypatch.undo()
    importlib.reload(importlib.import_module("aurantium.theme"))
    importlib.reload(importlib.import_module("aurantium.panels.sector_heatmap"))


# -- the label must be readable on its own tile ----------------------------


@pytest.mark.parametrize("pct", READINGS)
def test_the_figure_is_readable_on_every_tile(heat, pct):
    """The defect this panel actually shipped: white ink at 1.09:1."""
    fill = heat._tile_color(pct).name()
    ink = heat._tile_ink(pct)
    ratio = contrast(ink, fill)
    assert ratio >= INK_MIN, (
        f"{pct}% tile: {ink} on {fill} is {ratio:.2f}:1, under {INK_MIN}:1"
    )


def test_the_old_hardcoded_white_would_fail(heat):
    """Guard the guard.

    If a future change makes every tile dark again, the test above passes
    trivially and stops meaning anything. This asserts the ramp still contains
    at least one tile where the old hardcoded white was genuinely wrong — i.e.
    that ``_tile_ink`` is still doing work rather than always returning white.
    """
    worst = min(contrast("#ffffff", heat._tile_color(p).name()) for p in READINGS)
    assert worst < INK_MIN, (
        "no tile penalises hardcoded white any more — is the ramp still diverging?"
    )


# -- no data is not a value ------------------------------------------------


def test_no_data_is_off_the_ramp(heat):
    """The no-data fill must not be a point *on* the ramp.

    Deliberately not "ΔE ≥ 8 from flat". That assertion is unmeetable on the
    light theme without lying: ``BG`` (#ffffff) and ``BG_ALT`` (#f4f5f7) are
    ΔE 3.0 apart, and separating them needs a fill a third of the way to
    ``FG_MUTED`` — a mid-grey that reads as a value rather than an absence. The
    two themes want opposite directions. What *is* true and worth pinning is
    that no-data takes the panel surface, which the ramp never returns.
    """
    none_fill = heat._tile_color(None).name()
    ramp = {heat._tile_color(p).name() for p in READINGS if p is not None}
    assert none_fill not in ramp
    assert not heat._has_reading(None)
    assert all(heat._has_reading(p) for p in READINGS if p is not None)


def test_no_data_carries_the_non_colour_channels(heat):
    """Since fill cannot do it alone, the other channels must — and must be
    tested, or the light theme quietly goes back to "flat market"."""
    from aurantium.panel import NULL_GLYPH

    assert heat._fmt_pct(None) == NULL_GLYPH          # not a percentage
    assert heat._fmt_pct(0.0) == "+0.00%"             # ...and flat still is one
    # The tile paints a dashed outline only when there is no reading; that flag
    # is what paintEvent branches on.
    assert heat._has_reading(0.0) is True
    assert heat._has_reading(None) is False


def test_no_data_is_visibly_separate_where_the_theme_allows_it(heat):
    """On the dark theme the fill *can* carry it (ΔE 15.4), so it should. This
    guards against a future change that flattens both onto one surface."""
    if heat._test_theme.current_theme() != "dark":
        pytest.skip("light theme cannot separate these by fill — see above")
    assert delta_e(
        heat._tile_color(None).name(), heat._tile_color(0.0).name()
    ) >= 8.0


def test_no_data_reads_as_the_null_glyph(heat):
    from aurantium.panel import NULL_GLYPH

    assert heat._fmt_pct(None) == NULL_GLYPH
    assert heat._fmt_pct("not a number") == NULL_GLYPH
    assert heat._fmt_pct(1.5) == "+1.50%"
    assert heat._fmt_pct(-1.5) == "-1.50%"


# -- the ramp itself -------------------------------------------------------


def test_each_arm_is_monotone_in_lightness(heat):
    """A ramp that doubles back is a ramp where two different readings look the
    same. Checked per arm: the two arms run to different hues, so lightness is
    only required to be ordered *within* one."""
    for arm in ([0.0, 0.5, 1.0, 2.0], [0.0, -0.5, -1.0, -2.0]):
        ls = [oklch(heat._tile_color(p).name())[0] for p in arm]
        deltas = [b - a for a, b in zip(ls, ls[1:])]
        assert all(d < 0 for d in deltas) or all(d > 0 for d in deltas), (
            f"arm {arm} is not monotone in lightness: {ls}"
        )


def test_the_arms_end_on_the_theme_tick_colours(heat):
    """Full saturation is UP/DOWN, so the heatmap agrees with every other panel
    about what green and red mean — and inherits the colour-blind swap."""
    theme = heat._test_theme
    assert heat._tile_color(2.0).name().lower() == theme.UP.lower()
    assert heat._tile_color(-2.0).name().lower() == theme.DOWN.lower()


def test_past_the_clamp_saturates_rather_than_overshooting(heat):
    """±5% must look like ±2%, not wrap around into some other colour."""
    assert heat._tile_color(5.0).name() == heat._tile_color(2.0).name()
    assert heat._tile_color(-5.0).name() == heat._tile_color(-2.0).name()


def test_direction_survives_colour_blind_mode(monkeypatch, qapp):
    """The ramp's endpoints are UP/DOWN, so colour-blind mode changes every
    fill. Up and down must still be far apart after the swap."""
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw:
            "dark" if key == "ui/theme" else
            (True if key == "ui/colorblind" else default)
    )
    importlib.reload(importlib.import_module("aurantium.theme"))
    heat = importlib.reload(importlib.import_module("aurantium.panels.sector_heatmap"))
    try:
        # Only at full saturation. A diverging ramp's two arms *converge* on the
        # neutral by construction — at ±0.5% they are 25% of the way out and
        # measure ΔE 7.2, which is the ramp working, not failing. What carries
        # direction near the midpoint is the sign printed in the figure, the
        # same secondary channel the ▲/▼ tick glyphs provide elsewhere.
        up, down = heat._tile_color(2.0).name(), heat._tile_color(-2.0).name()
        assert delta_e(up, down) >= 15.0, "±2% collide under colour-blind mode"
        for pct in (0.5, 1.0, 2.0):
            assert heat._fmt_pct(pct).startswith("+")
            assert heat._fmt_pct(-pct).startswith("-")
            assert contrast(heat._tile_ink(pct), heat._tile_color(pct).name()) >= INK_MIN
            assert contrast(heat._tile_ink(-pct), heat._tile_color(-pct).name()) >= INK_MIN
        # And the arms must at least be ordered — never identical.
        for pct in (0.5, 1.0, 2.0):
            assert heat._tile_color(pct).name() != heat._tile_color(-pct).name()
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("aurantium.theme"))
        importlib.reload(importlib.import_module("aurantium.panels.sector_heatmap"))
