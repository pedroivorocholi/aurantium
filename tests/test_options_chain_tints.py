"""The options chain's in-the-money row tints.

These shipped as two literals — ``#1c2b22`` and ``#2b1c1c`` — chosen against
the dark theme and applied unconditionally. On the light theme they painted
near-black blocks behind near-black text: the panel's own foreground measured
**1.12:1** on the call tint and **1.01:1** on the put tint, which is not "low
contrast", it is text that is not on the screen. Every in-the-money row in the
chain was affected, which on a liquid name is half the table.

They are now derived from the surface and the tick colours, and the property
worth pinning is not the hex — it is that the tint sits a *fixed perceptual
distance* from whatever surface it lands on. A fixed blend fraction cannot do
that: OKLab lightness is compressed near black, so the same fraction moves much
further from white than from black.
"""

import importlib

import pytest

from aurantium.color import contrast, delta_e

#: Body text on a tinted row. These rows carry the live figures, so the tint is
#: a background and has no business eating into their legibility.
TEXT_MIN = 4.5


@pytest.fixture(params=["dark", "light"])
def chain(request, monkeypatch, qapp):
    """``options_chain`` and ``theme`` re-imported against a given theme."""
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw:
            request.param if key == "ui/theme" else
            (False if key == "ui/colorblind" else default)
    )
    theme = importlib.reload(importlib.import_module("aurantium.theme"))
    module = importlib.reload(
        importlib.import_module("aurantium.panels.options_chain")
    )
    module._test_theme = theme
    yield module
    monkeypatch.undo()
    importlib.reload(importlib.import_module("aurantium.theme"))
    importlib.reload(importlib.import_module("aurantium.panels.options_chain"))


def test_row_text_is_readable_on_both_tints(chain):
    """The regression that shipped. Was 1.12:1 and 1.01:1 on the light theme."""
    fg = chain._test_theme.FG
    for name, tint in (("call", chain._ITM_TINT_CALL), ("put", chain._ITM_TINT_PUT)):
        ratio = contrast(fg, tint)
        assert ratio >= TEXT_MIN, f"{name} tint {tint}: text at {ratio:.2f}:1"


def test_the_old_hardcoded_tints_would_fail_here(chain):
    """Guard the guard: on the light theme the shipped values must still be
    demonstrably unreadable, or this test file has stopped testing anything."""
    if chain._test_theme.current_theme() != "light":
        pytest.skip("the shipped values were chosen for the dark theme")
    fg = chain._test_theme.FG
    assert contrast(fg, "#1c2b22") < 2.0
    assert contrast(fg, "#2b1c1c") < 2.0


def test_the_tint_is_a_tint_not_a_fill(chain):
    """It has to read as a band behind the numbers, not as a block. Both ends
    matter: too close and the row is not marked, too far and it competes."""
    bg = chain._test_theme.BG
    for tint in (chain._ITM_TINT_CALL, chain._ITM_TINT_PUT):
        d = delta_e(tint, bg)
        assert d >= 8.0, f"{tint} is ΔE {d:.1f} from the surface — invisible"
        assert d <= 24.0, f"{tint} is ΔE {d:.1f} from the surface — a fill"


def test_both_themes_tint_by_the_same_perceptual_amount(chain):
    """The point of solving for ΔE instead of picking a blend fraction: an
    in-the-money row means the same thing, and looks equally emphatic, whichever
    theme the user runs."""
    bg = chain._test_theme.BG
    for tint in (chain._ITM_TINT_CALL, chain._ITM_TINT_PUT):
        assert delta_e(tint, bg) == pytest.approx(chain._ITM_TINT_DE, abs=0.5)


def test_calls_and_puts_are_tinted_apart(chain):
    """They sit in separate tables so they are never adjacent, but the side
    should still be legible from the colour alone."""
    assert delta_e(chain._ITM_TINT_CALL, chain._ITM_TINT_PUT) >= 3.0


def test_the_tints_follow_colour_blind_mode(monkeypatch, qapp):
    """Derived from UP/DOWN, so the deuteranopia-safe substitutes must come
    through without anything else changing. The hardcoded values never did."""
    from PySide6.QtCore import QSettings

    monkeypatch.setattr(
        QSettings, "value", lambda self, key, default=None, **kw:
            "dark" if key == "ui/theme" else
            (True if key == "ui/colorblind" else default)
    )
    theme = importlib.reload(importlib.import_module("aurantium.theme"))
    chain = importlib.reload(
        importlib.import_module("aurantium.panels.options_chain")
    )
    try:
        # Not "is the tint nearer UP than DOWN" — at ΔE 16 off a near-black
        # surface both tints are far from either saturated endpoint, and that
        # comparison is decided by lightness rather than hue. What the swap
        # actually has to do is *change the tints* and keep the two sides apart.
        assert chain._ITM_TINT_CALL == chain._tint(theme.UP)
        assert chain._ITM_TINT_PUT == chain._tint(theme.DOWN)
        assert chain._ITM_TINT_CALL != "#011105"  # the green-derived dark tint
        assert delta_e(chain._ITM_TINT_CALL, chain._ITM_TINT_PUT) >= 3.0
        assert contrast(theme.FG, chain._ITM_TINT_CALL) >= TEXT_MIN
        assert contrast(theme.FG, chain._ITM_TINT_PUT) >= TEXT_MIN
    finally:
        monkeypatch.undo()
        importlib.reload(importlib.import_module("aurantium.theme"))
        importlib.reload(importlib.import_module("aurantium.panels.options_chain"))
