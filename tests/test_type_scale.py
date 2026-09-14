"""The type, weight, spacing and radius scale.

Before this there were roughly fifteen rendered text sizes between 9px and 20px
across two unit systems that did not agree — the stylesheet said
``font-size: 11px`` while ``app.setFont`` said 9 *point* (~12px at 96 DPI), so
anything QSS did not reach rendered a size nobody had chosen. Five of those
steps sat within a pixel of each other, below the threshold at which a size
difference communicates anything, so they cost maintenance and bought nothing.

Weight had three values expressed three ways (QSS ``bold``, QSS numeric,
``QFont.setBold``), which meant a "600" semibold and a "bold" heading were two
different weights doing the same job. Radius had five values for what is two
decisions. And two idioms — a secondary label and a stat value — were
hand-written at roughly thirty call sites, each one a per-widget stylesheet
bypassing the design system.
"""

import re

import pytest

from aurantium import theme

SIZES = [theme.FONT_XS, theme.FONT_SM, theme.FONT_MD,
         theme.FONT_LG, theme.FONT_TITLE, theme.FONT_DISPLAY]


def rules_only(sheet: str) -> str:
    """The sheet with its comments removed.

    The comments document the values and idioms that were *retired*, so a naive
    scan finds "font-weight: bold" in the note explaining why nothing uses
    ``bold`` any more. Documentation doing its job is not a regression.
    """
    return re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)


# -- the scale itself ------------------------------------------------------


def test_the_scale_is_ordered_and_has_no_redundant_steps():
    """Every step must be a step the eye can actually resolve.

    The old set had sizes within ~4% of each other — 10.67px (8pt) beside 11px
    beside 11.33px (8.5pt) — which is below the threshold at which a size
    difference carries meaning, so they multiplied maintenance without
    communicating anything.
    """
    assert SIZES == sorted(SIZES)
    # A ratio, not a pixel count. At 10px a single pixel is a 10% step and reads
    # as one; at 16px it does not. The old set's problem was five pairs inside
    # ~4% of each other, which is what a fixed 2px rule was reaching for and
    # would also have wrongly condemned the deliberate 10/11 pair this terminal
    # is built on (eyebrows against body text).
    for a, b in zip(SIZES, SIZES[1:]):
        assert b / a >= 1.09, f"{a}px and {b}px are within {b / a:.0%} — one step"


def test_the_scale_is_small():
    """Six steps for a terminal. More than that and the hierarchy stops being
    readable as a hierarchy."""
    assert len(set(SIZES)) == 6


def test_the_stylesheet_only_uses_scale_sizes():
    used = {int(n) for n in re.findall(r"font-size: (\d+)px", rules_only(theme.STYLESHEET))}
    assert used <= set(SIZES), f"off-scale sizes in the sheet: {used - set(SIZES)}"


def test_the_stylesheet_only_uses_scale_radii():
    allowed = {theme.RADIUS_SM, theme.RADIUS_MD, theme.RADIUS_ROUND}
    used = {int(n) for n in re.findall(r"border-radius: (\d+)px", rules_only(theme.STYLESHEET))}
    assert used <= allowed, f"off-scale radii: {used - allowed}"


def test_weight_has_one_mechanism():
    """``bold`` is 700, so mixing the keyword with numerics meant the same
    intent rendered at two different weights depending on which file you were
    in."""
    used = set(re.findall(r"font-weight: (\w+)", rules_only(theme.STYLESHEET)))
    assert used <= {str(theme.WEIGHT_MEDIUM), str(theme.WEIGHT_BOLD)}, used
    assert "bold" not in used


def test_the_base_font_matches_the_stylesheet(qapp):
    """The two-unit-system bug. ``QFont(UI_FONT, 9)`` is ~12px at 96 DPI while
    the sheet said 11px, so painted widgets and native dialogs disagreed with
    every styled one."""
    theme.apply_theme(qapp)
    font = qapp.font()
    assert font.pixelSize() == theme.FONT_MD
    assert font.pointSize() == -1, "the base font must be set in pixels, not points"


# -- the semantic roles ----------------------------------------------------


@pytest.fixture
def rendered(qapp):
    """Render a label of each role on the panel surface and read its ink.

    Rendered rather than asserted against the stylesheet text: a rule that is
    present but does not match (wrong widget class, wrong specificity) would
    pass a substring check and fail the user. These labels are parented and the
    host paints a known background — an unparented ``render()`` reads
    uninitialised pixels.
    """
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    theme.apply_theme(qapp)
    host = QWidget()
    host.setObjectName("h")
    host.setStyleSheet("QWidget#h { background: %s; }" % theme.BG)
    lay = QVBoxLayout(host)
    names = ["", "secondary", "statValue", "statValueLarge"]
    labels = {}
    for n in names:
        lbl = QLabel("88888888", host)
        if n:
            lbl.setObjectName(n)
        lay.addWidget(lbl)
        labels[n] = lbl
    host.resize(200, 140)
    host.show()
    qapp.processEvents()
    pix = QPixmap(host.size())
    host.render(pix)
    img = pix.toImage()

    def ink(name):
        g = labels[name].geometry()
        best, best_lum = None, -1.0
        for y in range(g.top(), g.bottom() + 1):
            for x in range(g.left(), g.right() + 1):
                c = img.pixelColor(x, y)
                lum = c.red() * 0.2126 + c.green() * 0.7152 + c.blue() * 0.0722
                if lum > best_lum:
                    best_lum, best = lum, c.name()
        return best

    yield ink, labels
    host.deleteLater()


def test_the_default_label_is_the_foreground(rendered):
    ink, _ = rendered
    assert ink("") == theme.FG.lower()


def test_secondary_resolves_to_the_dim_foreground(rendered):
    """~17 call sites wrote ``color: FG_DIM`` by hand."""
    ink, _ = rendered
    assert ink("secondary") == theme.FG_DIM.lower()


def test_stat_value_resolves_to_the_accent(rendered):
    """~9 call sites wrote ``color: ACCENT; font-weight: bold``."""
    ink, labels = rendered
    assert ink("statValue") == theme.ACCENT.lower()
    assert labels["statValue"].font().weight() == theme.WEIGHT_BOLD


def test_the_large_stat_value_takes_the_title_step(rendered):
    ink, labels = rendered
    assert ink("statValueLarge") == theme.ACCENT.lower()
    assert labels["statValueLarge"].font().pixelSize() == theme.FONT_TITLE


# -- the exported workbook -------------------------------------------------


def test_the_excel_export_is_one_coherent_palette():
    """It used to freeze the *dark* theme's chrome and pair it with a *light*
    zebra stripe — neither theme, following nothing."""
    from aurantium.panels import fundamentals

    light = theme.palette_colors("light")
    for key in ("ACCENT", "ON_ACCENT", "BG_HEADER", "FG", "BG_ALT"):
        assert fundamentals._DOC[key] == light[key]


def test_the_exported_workbook_is_legible_on_paper():
    """A document, not a screenshot of the app. Every band has to hold text at
    a ratio that survives printing."""
    from aurantium.color import contrast
    from aurantium.panels import fundamentals

    doc = fundamentals._DOC
    assert contrast(doc["ON_ACCENT"], doc["ACCENT"]) >= 4.5   # title band
    assert contrast(doc["FG"], doc["BG_HEADER"]) >= 4.5       # column headers
    assert contrast(doc["FG"], doc["BG_ALT"]) >= 4.5          # zebra rows


def test_the_export_does_not_follow_the_active_theme():
    """Deliberate: a black-background spreadsheet is unreadable printed and
    startling in someone else's inbox."""
    from aurantium.panels import fundamentals

    assert fundamentals._DOC["BG_ALT"] == theme.palette_colors("light")["BG_ALT"]
    assert fundamentals._xl("#b45309") == "B45309"
