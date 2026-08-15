"""The panel header strip — where cross-panel symbol linking becomes visible.

Every panel prints the symbol it is currently showing, and flashes its header
in the link group's color when that symbol arrived from *another* panel. With
several panels open, those two things are what tell the user which name each
panel is on and that a click over there landed over here.
"""

import pytest

from aurantium.symbol_context import DEFAULT_GROUP, GROUP_COLORS, UNLINKED


@pytest.fixture
def panel(qapp):
    from aurantium.panel import Panel
    from aurantium.symbol_context import SymbolContext

    # a fresh context per test: the singleton is shared process-wide and would
    # otherwise leak one test's symbol into the next
    SymbolContext._inst = None

    class _P(Panel):
        panel_id = "test_panel"
        panel_title = "Test"

        def build(self) -> None:
            pass

    p = _P()
    p.build()
    yield p
    SymbolContext._inst = None


def test_header_starts_with_no_symbol(panel):
    assert panel._symbol_lbl.text() == ""


def test_header_prints_the_active_symbol(panel):
    panel.set_symbol("aapl")
    assert panel._symbol_lbl.text() == "AAPL"


def test_a_symbol_from_the_link_group_flashes_the_header(panel):
    from aurantium.symbol_context import SymbolContext

    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "MSFT", source=None)
    assert panel._symbol_lbl.text() == "MSFT"
    assert panel._header.flash > 0.0


def test_the_panel_does_not_flash_at_its_own_click(panel):
    """The user is already looking at the click they just made; flashing there
    would spend the signal on the one panel that doesn't need it."""
    panel.set_symbol("MSFT")
    assert panel._header.flash == 0.0


def test_an_unlinked_panel_ignores_the_group(panel):
    from aurantium.symbol_context import SymbolContext

    panel.set_link_group(UNLINKED)
    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "TSLA", source=None)
    assert panel._symbol_lbl.text() == ""
    assert panel._header.flash == 0.0


def test_the_flash_uses_the_link_groups_own_color(panel):
    from PySide6.QtGui import QColor

    from aurantium.symbol_context import SymbolContext

    panel.set_link_group("B")
    SymbolContext.instance().set_symbol("B", "NVDA", source=None)
    assert panel._header._tint == QColor(GROUP_COLORS["B"])


def test_the_badge_is_outlined_not_filled(panel):
    """The link badge is the least important thing on screen; a solid amber
    chip made it the loudest, competing with the data for the accent color."""
    sheet = panel._badge.styleSheet()
    assert "background: transparent" in sheet
    assert GROUP_COLORS[DEFAULT_GROUP] in sheet


def test_the_badge_has_no_hardcoded_colors_when_unlinked(panel):
    """The unlinked state used to hardcode dark-theme greys, which were wrong
    on the light theme."""
    from aurantium.theme import BORDER_STRONG, FG_MUTED

    panel.set_link_group(UNLINKED)
    sheet = panel._badge.styleSheet()
    assert FG_MUTED in sheet
    assert BORDER_STRONG in sheet


def test_a_second_flash_survives_the_first_animation_being_collected(panel):
    """Same DeleteWhenStopped defect as the notifier: pulsing twice called
    .stop() on an animation Qt had already destroyed, so the second and every
    later propagated symbol raised instead of flashing."""
    from PySide6.QtCore import QCoreApplication, QEvent

    panel._header.pulse("#f5a623")
    # DeleteWhenStopped defers the delete; the event loop is what destroys it
    panel._header._anim.stop()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    panel._header.pulse("#4a90d9")  # must not raise

    assert panel._header.flash > 0.0


def test_reduced_motion_leaves_no_lingering_tint(panel, monkeypatch):
    """The reduced-motion branch used to set the tint to full and leave it
    there — nothing ever cleared it, so the header stayed permanently coloured
    after the first propagated symbol. A persistent artifact is worse than no
    animation: the user asked for less motion and got a stuck visual state."""
    from aurantium import motion

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    panel._header.pulse("#f5a623")
    assert panel._header.flash == 0.0


def test_reduced_motion_still_shows_which_symbol_arrived(panel, monkeypatch):
    """Dropping the tint loses no information: the header prints the symbol,
    which is the thing the flash was pointing at."""
    from aurantium import motion
    from aurantium.symbol_context import SymbolContext

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    SymbolContext.instance().set_symbol(DEFAULT_GROUP, "MSFT", source=None)
    assert panel._symbol_lbl.text() == "MSFT"
