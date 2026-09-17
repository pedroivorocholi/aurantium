"""The painted icon family: one grid, one size, and no unicode affordances
left beside it.

``icons.py`` exists because affordances drawn as characters render in whatever
font the system happens to have for the codepoint — a different stroke weight,
different metrics and a different optical size from the painted panel chrome
sitting next to them. These tests hold the family closed: the size and DPR
contract, and a scan of the source for the two glyphs that were converted.
"""

import re
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QIcon

from aurantium import icons, theme

SOURCE = Path(icons.__file__).parent


# -- the grid ---------------------------------------------------------------


@pytest.mark.parametrize("kind", icons.KINDS)
def test_every_glyph_paints_at_the_family_size(qapp, kind):
    pixmap = icons.pixmap(kind, theme.CHROME_TEXT_DIM)
    assert pixmap.size().width() == icons.SIZE
    assert pixmap.size().height() == icons.SIZE


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_a_glyph_is_painted_for_the_device_pixel_ratio(qapp, dpr):
    """The bitmap grows with the ratio and is tagged with it, so the icon is
    the same logical size everywhere and stays hairline-sharp on HiDPI rather
    than being an upscaled 16px bitmap."""
    pixmap = icons.pixmap("close", theme.CHROME_TEXT_DIM, dpr)
    assert pixmap.width() == round(icons.SIZE * dpr)
    assert pixmap.devicePixelRatio() == pytest.approx(dpr)


@pytest.mark.parametrize("kind", icons.KINDS)
def test_every_glyph_actually_draws_something(qapp, kind):
    """A typo in the kind string is otherwise silent — ``_paint`` falls through
    every branch and returns a transparent square."""
    image = icons.pixmap(kind, "#ffffff", 2.0).toImage()
    inked = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 0
    )
    assert inked > 8, f"{kind} painted almost nothing"


def test_the_glyph_takes_the_colour_it_is_given(qapp):
    image = icons.pixmap("maximize", theme.ACCENT, 1.0).toImage()
    accent = QColor(theme.ACCENT)
    hues = {
        image.pixelColor(x, y).hue()
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).alpha() > 200
    }
    assert hues, "nothing opaque was painted"
    assert all(abs(h - accent.hue()) <= 2 for h in hues)


def test_the_two_tone_icon_carries_a_hover_colour(qapp):
    """QSS can recolour text on :hover but not an icon. The hover colour has to
    live in the icon's own Active mode or it is simply lost."""
    icon = icons.two_tone_icon("close", theme.FG_DIM, theme.DOWN)
    normal = icon.pixmap(icons.SIZE, icons.SIZE, QIcon.Mode.Normal).toImage()
    active = icon.pixmap(icons.SIZE, icons.SIZE, QIcon.Mode.Active).toImage()
    assert normal != active


def test_a_dot_carries_more_diameter_than_a_line_carries_thickness():
    """Matching the two numbers would make the grip read as a sixth of the
    family's weight — which is how the first attempt looked."""
    assert icons.DOT > icons.STROKE


# -- the family is closed ---------------------------------------------------


def _source_files():
    return [
        path
        for path in SOURCE.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


def test_no_affordance_is_still_drawn_as_a_character():
    """``✕`` and ``⠿`` were the two affordances set as button/label *text*.

    Prose may still mention them — the onboarding guide describes the Windows
    title-bar close button, which the OS draws and aurantium does not — so this
    looks for them being *assigned to a widget*, which is what puts a
    foreign-font glyph on screen next to a painted one.
    """
    assignment = re.compile(r"""set(Text|Pixmap)\(\s*["'][^"']*[✕⠿]""")
    offenders = [
        f"{path.relative_to(SOURCE)}:{n}: {line.strip()}"
        for path in _source_files()
        for n, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        )
        if assignment.search(line)
    ]
    assert not offenders, "affordance glyphs still set as widget text:\n" + "\n".join(
        offenders
    )


def test_the_colour_blind_direction_marks_are_left_alone():
    """``▲``/``▼`` are deliberately NOT in the painted family.

    They are prefixed to a signed number inside a table cell by
    ``theme.tick_glyph``: they scale with the cell's font, sit on its baseline,
    and survive being copied out with the text. An icon does none of that, and
    this is the colour-blind path, not decoration.
    """
    assert theme.tick_glyph is not None
    assert "▲" not in icons.KINDS and "▼" not in icons.KINDS
    source = (SOURCE / "icons.py").read_text(encoding="utf-8")
    assert "tick_glyph" in source, "the exclusion must stay documented"


# -- the extraction preserved the chrome set --------------------------------


def test_the_panel_chrome_kinds_all_survived():
    """The six glyphs that shipped on MainWindow before the family moved out."""
    for kind in ("close", "expand", "maximize", "restore", "menu", "pin"):
        assert kind in icons.KINDS


def test_main_window_still_hands_out_chrome_icons(qapp):
    """``_chrome_icon`` keeps its signature and its per-kind default colour —
    ``restore`` stays amber, because that tint is the "panel is maximized"
    state cue, not styling."""
    from aurantium.app import MainWindow

    assert callable(MainWindow._chrome_icon)
    text = (SOURCE / "app.py").read_text(encoding="utf-8")
    assert 'ACCENT if kind == "restore"' in text
