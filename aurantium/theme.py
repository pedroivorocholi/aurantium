"""Terminal theme — Bloomberg-authentic dark (default), plus a light variant.

The default look is modeled on a Bloomberg Launchpad screen: true-black data
surfaces, a desaturated steel-blue title bar on every panel, amber as a primary
text color (not just an accent), blue column-header bands, true green/red for
ticks, and dense, monospaced tabular figures.

A light variant is available (Settings ▸ Theme). Colors are exposed as module-level
constants (``BG``, ``ACCENT``, …) that the rest of the app imports at load time,
and the active palette is chosen ONCE at import from the saved preference. The
theme therefore applies fully — charts and all — on the next launch, which is
why switching prompts a restart (see app.py). ``ON_ACCENT`` is the text color to
use on top of an ``ACCENT`` fill (black on amber in dark, white on amber in
light), so accent chips/selections stay readable in both themes.
"""

import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

#: QSettings key holding "dark" | "light"
THEME_SETTINGS_KEY = "ui/theme"
THEMES = ("dark", "light")
DEFAULT_THEME = "dark"

# Fonts: chrome font + figures font (theme-independent). Neither Windows font
# exists on macOS — Qt would silently fall back to some default, breaking the
# tabular-figure alignment the whole density-driven layout depends on — so
# pick the platform's own equivalents instead. Menlo is macOS's traditional
# terminal/dev monospace (bundled since 10.6, the same role Consolas plays on
# Windows); Helvetica Neue is the pre-San-Francisco system sans, still bundled
# and a closer visual match to Segoe UI's weight than trying to reference the
# San Francisco family by name (not reliably resolvable via plain QSS
# font-family strings across macOS versions).
if sys.platform == "darwin":
    UI_FONT = "Helvetica Neue"
    MONO_FONT = "Menlo"
else:
    UI_FONT = "Segoe UI"
    MONO_FONT = "Consolas"


# -- type, spacing and radius scale ----------------------------------------
#
# Before this there were roughly fifteen rendered text sizes between 9px and
# 20px, across two unit systems that did not line up: the QSS said
# ``font-size: 11px`` while ``app.setFont`` said 9 *point* (~12px at 96 DPI), so
# anything QSS did not reach — painted widgets, item delegates, native dialogs —
# rendered a size nobody had chosen. Five of the steps sat within a pixel of
# each other, which is below the threshold at which a size difference means
# anything, so they multiplied maintenance without communicating.
#
# Six steps, in pixels, one unit. The core three (SM/MD/LG) were already the
# intentional part of the old set; the rest replace one-off inline values.
FONT_XS = 9        # link badge — the smallest text in the app
FONT_SM = 10       # eyebrows, status, table headers, chart chips
FONT_MD = 11       # body and tables — the default
FONT_LG = 13       # command bar, section titles
FONT_TITLE = 16    # a panel's headline value (recommendation, total, company)
FONT_DISPLAY = 20  # the heatmap tile figure

# Weight has three values and three mechanisms — QSS ``bold``, QSS numeric, and
# ``QFont.setBold``. Numeric QSS is canonical: ``bold`` is 700, so a "600"
# semibold and a "bold" heading were two different weights doing the same job.
WEIGHT_MEDIUM = 600
WEIGHT_BOLD = 700

# Spacing: a 4/8 grid with a 6px dense step. 6 is not a compromise — it was
# already the single most common value in the app, because terminal density
# wants a tighter rhythm than 8 between related controls.
SPACE_XS = 2
SPACE_SM = 4
SPACE_MD = 6
SPACE_LG = 8
SPACE_XL = 12

# Two radii. There were five (2, 3, 7 in the sheet; 4 and 8 invented inline).
RADIUS_SM = 2      # controls: buttons, inputs, chips
RADIUS_MD = 3      # surfaces: menus, tooltips, tiles
RADIUS_ROUND = 7   # the radio indicator, which is a circle — not a fourth size


# -- palettes --------------------------------------------------------------
# Dark is the original, byte-for-byte. Light is a clean paper variant that
# keeps the amber identity (a deeper, readable burnt-amber on white).
_DARK = {
    "ACCENT": "#ffab2e",       # amber — accent AND the default data-label color
    "ACCENT_DEEP": "#c8842a",  # dimmed amber
    "ON_ACCENT": "#000000",    # text on an amber fill — black on the dark theme
    "BG": "#000000",           # true black — all data surfaces
    "BG_ALT": "#0b0c0e",       # near-black zebra stripe
    "BG_ELEV": "#1b2530",      # raised controls (buttons), bluish-dark
    "CHROME": "#161b21",       # dark chrome — all title bars / top / bottom
    "CHROME_HOVER": "#242c35", # hover / active tab (a subtle lift)
    "BG_HEADER": "#222d39",    # section rows inside panels
    "CHROME_BORDER": "#0c1015",# thin outline between chrome and black
    "CHROME_TEXT": "#d7dde3",  # light text on chrome
    "CHROME_TEXT_DIM": "#828c97",
    "HEADER_BLUE": "#1a2129",  # table column-header band
    "SELECT_BLUE": "#1d3143",  # selected row
    "BORDER": "#141820",       # hairline dividers on black
    "BORDER_STRONG": "#28303a",
    "FG": "#cdd2d6",           # primary text (cool off-white)
    "FG_DIM": "#7c858e",       # secondary text, axis labels
    "FG_MUTED": "#535b64",     # eyebrows, disabled
    "UP": "#33c46a",           # gains — true green
    "DOWN": "#ff4d4d",         # losses — red
    # Keyboard-focus ring. A bright neutral rather than ACCENT: amber already
    # means "selected / active / this is the price" everywhere in this app, and
    # a focus ring in the same colour would be ambiguous against a checked
    # button or a chart chip. Measured 11.4:1 or better on every surface it can
    # land on. It does NOT work on an amber fill (1.62:1) — checked controls
    # take ON_ACCENT for their ring instead.
    "FOCUS": "#e8eef4",
}

_LIGHT = {
    "ACCENT": "#b45309",       # burnt amber — readable as text on white and as a fill
    "ACCENT_DEEP": "#92400e",  # deeper amber
    "ON_ACCENT": "#ffffff",    # text on an amber fill — white on the light theme
    "BG": "#ffffff",           # white — all data surfaces
    "BG_ALT": "#f4f5f7",       # light zebra stripe
    "BG_ELEV": "#eceef1",      # raised controls (buttons)
    "CHROME": "#e8eaed",       # light chrome — title bars / top / bottom
    "CHROME_HOVER": "#dcdfe3", # hover / active tab
    "BG_HEADER": "#dbe1ea",    # section rows inside panels
    "CHROME_BORDER": "#c9ced4",# outline between chrome and surface
    "CHROME_TEXT": "#1b2028",  # dark text on light chrome
    "CHROME_TEXT_DIM": "#4c545e",  # darker so small chrome text stays readable
    "HEADER_BLUE": "#d4dde9",  # table column-header band (light) — a clear blue-gray band
    "SELECT_BLUE": "#cfe0f5",  # selected row (light blue)
    "BORDER": "#dfe3e8",       # hairline dividers
    "BORDER_STRONG": "#c3c9d0",
    "FG": "#1a1f26",           # primary text (near-black)
    "FG_DIM": "#5b636d",       # secondary text, axis labels
    "FG_MUTED": "#8a929b",     # eyebrows, disabled
    "UP": "#0a8f3c",           # gains — green (darker for a white bg)
    "DOWN": "#d32f2f",         # losses — red
    "FOCUS": "#1a1f26",        # keyboard-focus ring — 12:1 or better on every surface
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}


#: The application's QSettings scope, named explicitly rather than inherited
#: from QCoreApplication.
#:
#: This module resolves the whole palette at *import* time (see the bottom of
#: the file), and a bare ``QSettings()`` keys off the names set by
#: ``QCoreApplication.setOrganizationName``/``setApplicationName``. Those are
#: set inside ``__main__.main()`` — but the splash screen imports this module
#: first (``__main__.py`` builds its status line with ``MONO_FONT``), and the
#: findash→aurantium migration briefly points the names at ``findash`` on the
#: way through. Either way a bare ``QSettings()`` read at import lands in the
#: wrong scope, silently returns the "dark" default, and freezes STYLESHEET and
#: every colour constant on the dark palette for the life of the process.
#:
#: That is exactly what shipped: selecting the light theme restarted the app
#: and changed nothing but the chart canvas, whose colours are stored in the
#: layout and remapped later, once the names are correct. Naming the scope here
#: makes the read independent of import order and of whatever the application
#: names happen to be at the moment — reads and writes now always agree.
_ORG = "aurantium"
_APP = "aurantium"


def _settings() -> QSettings:
    """This module's settings handle, in an explicitly-named scope."""
    return QSettings(_ORG, _APP)


def _read_theme_name() -> str:
    """The saved theme name, defaulting to dark. Safe to call before a
    QApplication exists — the scope is named, so it does not depend on one."""
    try:
        name = _settings().value(THEME_SETTINGS_KEY, DEFAULT_THEME, type=str)
    except Exception:
        name = DEFAULT_THEME
    return name if name in _PALETTES else DEFAULT_THEME


def current_theme() -> str:
    """Currently-selected theme name ("dark" | "light")."""
    return _read_theme_name()


def palette_colors(name: str | None = None) -> dict:
    """A copy of a theme's color map (defaults to the active theme), with the
    color-blind up/down override folded in when that mode is on. Lets other
    modules (e.g. the chart's per-panel colors) tell a theme-derived default
    from a genuine user customization across both themes."""
    name = name or _read_theme_name()
    pal = dict(_PALETTES.get(name, _ACTIVE))
    if _read_colorblind() and name in _COLORBLIND:
        pal.update(_COLORBLIND[name])
    return pal


def set_theme(name: str) -> None:
    """Persist the theme choice. Takes effect on the next launch (the caller
    prompts for a restart), so the whole app — charts included — renders in one
    consistent theme rather than a half-restyled mix."""
    if name in _PALETTES:
        _settings().setValue(THEME_SETTINGS_KEY, name)


# -- categorical series palette ---------------------------------------------
#
# The colours for telling *series* apart — chart indicators, comparison lines,
# anything where hue carries identity rather than magnitude or direction. One
# definition, so a series means the same thing in every panel; before this
# there were four independent lists, three of them hand-picked and unvalidated,
# and two of them failing the project's own published checks.
#
# **Four slots, and four is the ceiling — measured, not chosen.** Five colours
# are already spoken for and cannot be reused for identity: ACCENT (the price),
# UP and DOWN (direction), and the colour-blind mode's substitutes for those
# last two. Add the lightness band that keeps every series at comparable visual
# weight, the chroma floor, and 3:1 against the surface, and the light theme has
# almost nothing left: a gamut search over every in-band, in-gamut colour finds
# candidates only in violet, magenta and pink, all of which crowd slots 0 and 2.
# A fifth light slot exists on paper but only at the contrast floor and with
# adjacent separation below target. Dark could carry more; the two themes are
# required to stay the same length, so light binds. Charts that need more than
# four series cycle, and the labelled colour chip beside each one is the
# secondary encoding that legitimises reuse.
#
# **Slot order is an index promise, not a hue promise.** The two themes have
# never agreed on hue per slot (dark runs blue / yellow / violet / teal, light
# violet / … / magenta / blue) because a hue that clears the gates on black
# rarely clears them on white. What slot N guarantees is that the same *series*
# keeps the same slot across a theme switch — see ``_restored_indicator_color``
# in ``panels/chart.py``.
#
# Every value is computed against ``aurantium.color`` and re-checked by
# ``tests/test_chart_palette.py`` and ``tests/test_series_palette.py``.
# Do not hand-edit without re-running them.
_SERIES_PALETTES = {
    # blue · olive · violet · teal
    "dark": ("#0c699a", "#6e6b03", "#7035f5", "#15957b"),
    # blue-violet · violet · magenta · teal
    "light": ("#2745f6", "#8b81fe", "#791c8a", "#0fa4b0"),
}

#: Slots retired by a palette change, mapped to the slot they used to hold.
#:
#: Saved layouts store an indicator's *hex*, so a slot whose value changes would
#: orphan every chart that used it. Keeping the old value here lets the restore
#: path recognise it and hand back the slot's current colour instead.
#:
#: ``#3c5b07`` (light slot 1) was a dark olive. It cleared every check the guard
#: actually ran, and failed the one it did not: measured against the reserved
#: colours under deuteranopia it sat ΔE **5.1** from ACCENT and **3.4** from
#: DOWN, both under the 6.0 floor. On the light theme a red-green colour-blind
#: user could not tell an SMA line from the price line, or from a losing candle.
#: The guard only compared normal vision, where the same pairs measure 20.9 and
#: 28.5 and look fine.
_RETIRED_SERIES_COLORS = {
    "#3c5b07": 1,
}


def series_palette(theme: str | None = None) -> tuple[str, ...]:
    """The categorical series colours for a theme (defaults to the active one)."""
    name = theme or _read_theme_name()
    return _SERIES_PALETTES.get(name, _SERIES_PALETTES[DEFAULT_THEME])


def series_color(index: int, theme: str | None = None) -> str:
    """The colour for series ``index``, assigned in fixed slot order.

    Wraps past the last slot rather than generating a new hue: a generated
    colour is indistinguishable from an existing one under colour-vision
    deficiency and passes none of the checks. Callers that can show more series
    than there are slots must label them.
    """
    palette = series_palette(theme)
    return palette[index % len(palette)]


def series_slot_of(color: str) -> int | None:
    """The slot a colour occupies in *either* theme, including retired values,
    or None if it is not a palette colour at all (i.e. a user's own pick)."""
    key = (color or "").strip().lower()
    for name in THEMES:
        for slot, value in enumerate(_SERIES_PALETTES[name]):
            if value.lower() == key:
                return slot
    return _RETIRED_SERIES_COLORS.get(key)


# -- color-blind mode -------------------------------------------------------
#: QSettings key: when true, up/down use a deuteranopia/protanopia-safe palette
#: (blue up, vermillion down) instead of green/red, AND panels prefix ▲/▼ so
#: direction reads without relying on color at all.
COLORBLIND_SETTINGS_KEY = "ui/colorblind"

# Okabe–Ito-derived safe up/down per theme. Sky-blue up + vermillion down stay
# mutually distinct under red-green color blindness and clear of the amber
# ACCENT. Tuned per theme so each reads on its own background.
_COLORBLIND = {
    "dark":  {"UP": "#56b4e9", "DOWN": "#e8703a"},
    "light": {"UP": "#0072b2", "DOWN": "#d55e00"},
}


def _read_colorblind() -> bool:
    """The saved color-blind flag. Safe before a QApplication exists."""
    try:
        return bool(_settings().value(COLORBLIND_SETTINGS_KEY, False, type=bool))
    except Exception:
        return False


def colorblind_enabled() -> bool:
    """Whether the color-blind (deuteranopia-safe) mode is active."""
    return _read_colorblind()


def set_colorblind(on: bool) -> None:
    """Persist the color-blind choice. Applied on the next launch (the caller
    prompts for a restart), the same way a theme change is, so every panel and
    chart renders one consistent palette."""
    _settings().setValue(COLORBLIND_SETTINGS_KEY, bool(on))


# -- activate the saved palette: publish its colors as module constants -----
_active_name = _read_theme_name()
_ACTIVE = dict(_PALETTES[_active_name])
if _read_colorblind():
    _ACTIVE.update(_COLORBLIND[_active_name])
globals().update(_ACTIVE)
# kept for import stability (referenced by name elsewhere / historically)
CHROME_HI = _ACTIVE["CHROME_HOVER"]
CHROME_LO = _ACTIVE["CHROME"]


def _build_stylesheet(p: dict) -> str:
    return f"""
/* -- keyboard focus ------------------------------------------------------
   This used to be ``* {{ outline: 0; }}``, which removed Qt's focus indication
   from every widget in the app while replacing it in exactly two places (the
   QLineEdit family, below). Tabbing through a dialog showed a ring on the text
   inputs and nothing at all on the buttons, checkboxes, tabs, lists or tables.

   Scoped to item views, where the rule was actually earning its keep: Qt draws
   a dotted rectangle around the *focused item* there, which fights the row
   selection. Everything else gets a real ring, gated on keyboard navigation by
   focus.py so it never fires on a mouse click — Qt has no ``:focus-visible``,
   so the gate is a ``kbFocus`` dynamic property that filter maintains.

   Focusable controls reserve their ring as a transparent 1px border in their
   *base* rule. Adding a border on focus to a control that had none would grow
   its content box and shift the layout under the user mid-tab. */
QAbstractItemView {{ outline: 0; }}
QWidget {{
    background: {p['BG']}; color: {p['FG']};
    font-family: "{UI_FONT}"; font-size: {FONT_MD}px;
}}
QToolTip {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT']};
    border: 1px solid {p['CHROME_BORDER']}; border-radius: {RADIUS_MD}px; padding: 5px 9px;
}}

/* -- panel header: a thin context strip under the title bar ---------------
   The strip itself paints its own background and hairline (panel._HeaderStrip)
   so it can animate the link flash; only its children are styled here. Child
   backgrounds must be explicitly transparent — the blanket QWidget rule above
   would otherwise paint each label with a flat BG rectangle that the flash
   couldn't tint. */
QWidget#panelHeader > QLabel {{ background: transparent; }}
QLabel#panelSymbol {{
    background: transparent; color: {p['ACCENT']};
    font-family: "{MONO_FONT}"; font-size: {FONT_MD}px; font-weight: {WEIGHT_BOLD};
    letter-spacing: 0.5px;
}}
/* -- semantic label roles -------------------------------------------------
   Two idioms were hand-written at ~30 call sites: ``color: FG_DIM`` for a
   secondary label, and ``color: ACCENT; font-weight: bold`` for a stat value.
   Every one of them was a per-widget stylesheet — a full parse, a bypass of the
   design system, and a place for the two spellings to drift apart (some carried
   a font-size, some did not; some said ``bold``, some said 700).

   As object names they are one rule each, and a panel asks for a *role* rather
   than restating a colour. */
QLabel#secondary {{ background: transparent; color: {p['FG_DIM']}; }}
QLabel#statValue {{
    background: transparent; color: {p['ACCENT']};
    font-weight: {WEIGHT_BOLD};
}}
QLabel#statValueLarge {{
    background: transparent; color: {p['ACCENT']};
    font-size: {FONT_TITLE}px; font-weight: {WEIGHT_BOLD};
}}

QLabel#panelEyebrow {{
    color: {p['FG_MUTED']}; font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD}; letter-spacing: 1.5px;
}}
QLabel#panelStatus {{
    background: transparent; color: {p['ACCENT_DEEP']};
    font-size: {FONT_SM}px; font-family: "{MONO_FONT}";
}}
/* The state slot, beside the info slot. Separate label because one shared,
   un-prioritised slot meant a later write silently destroyed an earlier one —
   a success count could erase an error posted a moment before. Severity drives
   the colour; the default (loading) is deliberately the quietest thing in the
   header, because it is also the most frequent. */
QLabel#panelState {{
    background: transparent; color: {p['FG_MUTED']};
    font-size: {FONT_SM}px; font-family: "{MONO_FONT}";
}}
QLabel#panelState[severity="stale"] {{ color: {p['ACCENT']}; }}
QLabel#panelState[severity="error"] {{ color: {p['DOWN']}; }}

/* -- app top bar: steel-blue like the Launchpad title bar ---------------- */
/* Taller than a stock menu bar — it has to fit the wordmark logo, which sits
   in QWidget#menuBarRow beside the QMenuBar (a widget added as a QWidgetAction
   isn't reliably shown by QMenuBar, so the logo is a plain layout sibling
   instead). The removed bottom status bar (see MainWindow.__init__) gives
   back the height this costs. */
QWidget#menuBarRow {{
    background: {p['CHROME']}; border-bottom: 1px solid {p['CHROME_BORDER']};
}}
QMenuBar {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT']}; padding: 3px 8px 3px 2px;
}}
QMenuBar::item {{ padding: 5px 10px; border-radius: {RADIUS_SM}px; color: {p['CHROME_TEXT_DIM']}; }}
QMenuBar::item:selected {{ background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; }}
/* The blanket QWidget rule above paints every plain widget BG (true black),
   which is visibly darker than CHROME — override it so the logo's transparent
   PNG shows the same chrome grey as the rest of the row, not a black box. */
QLabel#menuBarLogo {{ background: transparent; padding: 0px 0px 0px 14px; }}

QWidget#commandBar {{ background: {p['BG']}; border-bottom: 1px solid {p['BORDER']}; }}
QLabel#commandLabel {{
    color: {p['ACCENT']}; font-size: {FONT_MD}px; font-weight: {WEIGHT_BOLD}; letter-spacing: 2px;
}}
QLineEdit#commandInput {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: {RADIUS_SM}px;
    padding: 4px 10px; color: {p['ACCENT']}; font-family: "{MONO_FONT}"; font-size: {FONT_LG}px;
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
}}
QLineEdit#commandInput:focus {{ border-color: {p['ACCENT']}; }}

/* -- tables: data surface, header band, accent-ready cells --------------- */
QTableWidget, QTableView {{
    background: {p['BG']}; alternate-background-color: {p['BG_ALT']};
    gridline-color: {p['BORDER']}; border: 1px solid transparent;
    selection-background-color: {p['SELECT_BLUE']}; selection-color: {p['CHROME_TEXT']};
    font-family: "{MONO_FONT}"; font-size: {FONT_MD}px;
}}
QTableView::item {{ padding: 1px 4px; }}
QHeaderView {{ background: {p['HEADER_BLUE']}; }}
QHeaderView::section {{
    background: {p['HEADER_BLUE']}; color: {p['CHROME_TEXT_DIM']}; border: 0;
    border-right: 1px solid {p['BORDER']}; border-bottom: 1px solid {p['BORDER']};
    padding: 4px 6px; font-family: "{UI_FONT}"; font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD};
    letter-spacing: 0.4px;
}}
QHeaderView::section:hover {{ background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; }}
QHeaderView::section[kbFocus="true"] {{ border: 1px solid {p['FOCUS']}; }}
QHeaderView::section:last {{ border-right: 0; }}
QTableCornerButton::section {{ background: {p['HEADER_BLUE']}; border: 0; }}
QListWidget {{
    background: {p['BG']}; alternate-background-color: {p['BG_ALT']};
    border: 1px solid transparent;
    font-family: "{MONO_FONT}"; font-size: {FONT_MD}px;
}}
QTableWidget[kbFocus="true"], QTableView[kbFocus="true"],
QListWidget[kbFocus="true"] {{ border-color: {p['FOCUS']}; }}
/* Selection must not grey out when the view loses focus — Fusion's inactive
   highlight is not palette-derived and reads as "this row stopped mattering". */
QTableView::item:selected:!active, QListWidget::item:selected:!active {{
    background: {p['SELECT_BLUE']}; color: {p['CHROME_TEXT']};
}}
QListWidget::item {{ padding: 2px 4px; }}
QListWidget::item:selected {{ background: {p['SELECT_BLUE']}; color: {p['CHROME_TEXT']}; }}

/* -- inner tab widgets (e.g. Portfolio) — amber underline like the docks -- */
QTabWidget::pane {{ border: 0; border-top: 1px solid {p['BORDER']}; }}
QTabBar::tab {{
    background: transparent; color: {p['CHROME_TEXT_DIM']};
    padding: 5px 12px; border: 0; border-bottom: 2px solid transparent;
    font-weight: {WEIGHT_MEDIUM};
}}
QTabBar::tab:hover {{ color: {p['CHROME_TEXT']}; }}
QTabBar::tab:selected {{ color: {p['CHROME_TEXT']}; border-bottom: 2px solid {p['ACCENT']}; }}
QTabBar::tab[kbFocus="true"] {{ color: {p['FOCUS']}; border-bottom-color: {p['FOCUS']}; }}

/* -- inputs -------------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: {RADIUS_SM}px;
    padding: 4px 8px; color: {p['FG']};
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {p['ACCENT']};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {p['CHROME_HOVER']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p['FG_MUTED']}; }}
QComboBox::drop-down {{ border: 0; width: 18px; }}
QComboBox QAbstractItemView {{
    background: {p['CHROME']}; border: 1px solid {p['CHROME_BORDER']};
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
    padding: 2px;
}}

/* -- symbol suggestion popup (components.symbol_search) ------------------ */
QListView#suggestPopup {{
    background: {p['CHROME']}; border: 1px solid {p['CHROME_BORDER']};
    color: {p['CHROME_TEXT']}; outline: 0; padding: 2px;
    font-family: "{MONO_FONT}"; font-size: {FONT_MD}px;
}}
QListView#suggestPopup::item {{ padding: 4px 8px; }}
QListView#suggestPopup::item:selected {{
    background: {p['ACCENT']}; color: {p['ON_ACCENT']};
}}

/* -- buttons: flat tabs, amber when active ------------------------------- */
QPushButton {{
    background: {p['BG_ELEV']}; color: {p['CHROME_TEXT_DIM']};
    border: 1px solid {p['BORDER_STRONG']}; border-radius: {RADIUS_SM}px;
    padding: 5px 13px; font-size: {FONT_MD}px; font-weight: {WEIGHT_MEDIUM};
}}
QPushButton[kbFocus="true"] {{ border-color: {p['FOCUS']}; }}
/* On an amber fill the neutral ring measures 1.62:1 and simply is not there;
   ON_ACCENT is the colour guaranteed readable on ACCENT in both themes. */
QPushButton:checked[kbFocus="true"] {{ border-color: {p['ON_ACCENT']}; }}
QPushButton:hover {{
    background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; border-color: {p['BORDER_STRONG']};
}}
QPushButton:pressed {{ background: {p['HEADER_BLUE']}; }}
QPushButton:checked {{
    background: {p['ACCENT']}; color: {p['ON_ACCENT']}; border-color: {p['ACCENT']}; font-weight: {WEIGHT_BOLD};
}}
QPushButton:checked:hover {{ background: {p['ACCENT_DEEP']}; border-color: {p['ACCENT_DEEP']}; }}
QPushButton:disabled {{ color: {p['FG_MUTED']}; border-color: {p['BORDER']}; background: {p['BG_ALT']}; }}

/* Chart range/interval chips: a compact terminal selector, not a form button.
   The checked state is an amber outline rather than an amber fill — with the
   panel-header symbol, the command bar and the indicator legend all already
   speaking amber, two solid amber blocks per chart were spending the accent on
   a setting rather than on data. */
QPushButton#chartChip {{
    padding: 1px 7px; font-size: {FONT_SM}px; font-weight: {WEIGHT_MEDIUM};
    min-height: 18px; max-height: 18px; border-radius: {RADIUS_SM}px;
}}
QPushButton#chartChip:checked {{
    background: {p['BG_ELEV']}; color: {p['ACCENT']};
    border-color: {p['ACCENT']}; font-weight: {WEIGHT_BOLD};
}}
QPushButton#chartChip:checked:hover {{
    background: {p['CHROME_HOVER']}; border-color: {p['ACCENT']};
}}
QPushButton#chartChip[kbFocus="true"] {{ border-color: {p['FOCUS']}; }}

QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: {RADIUS_SM}px;
    color: {p['CHROME_TEXT']}; padding: 2px;
}}
QToolButton:hover {{ background: rgba(128,128,128,0.18); }}
QToolButton:pressed {{ background: rgba(128,128,128,0.30); }}
/* A checkable QToolButton rendered identically on and off, which is why the
   panel link badge hand-writes its own stylesheet in Python (panel.py). */
QToolButton:checked {{
    background: {p['BG_ELEV']}; color: {p['ACCENT']}; border-color: {p['ACCENT']};
}}
QToolButton[kbFocus="true"] {{ border-color: {p['FOCUS']}; }}

/* -- checkboxes / radios ------------------------------------------------- */
/* The indicator needs explicit borders: unstyled, Qt draws a dark native box
   on our near-black surfaces, so an unticked option reads as plain text with
   nothing to click. Ticked is a filled amber square rather than a checkmark
   glyph — a QSS-styled indicator doesn't draw the native check, and a solid
   fill reads faster at 12px anyway. */
QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 12px; height: 12px;
    background: {p['BG_ELEV']};
    border: 1px solid {p['BORDER_STRONG']};
}}
QCheckBox::indicator {{ border-radius: {RADIUS_SM}px; }}
QRadioButton::indicator {{ border-radius: {RADIUS_ROUND}px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {p['ACCENT']};
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p['ACCENT']}; border-color: {p['ACCENT']};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {p['BORDER']};
}}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background: {p['ACCENT_DEEP']}; border-color: {p['ACCENT_DEEP']};
}}
/* The ring goes on the indicator, not the label: the label is just text and a
   border round it reads as a box, not as focus. */
QCheckBox[kbFocus="true"]::indicator, QRadioButton[kbFocus="true"]::indicator {{
    border-color: {p['FOCUS']};
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p['FG_MUTED']}; }}

/* -- menus --------------------------------------------------------------- */
QMenu {{
    background: {p['CHROME']}; border: 1px solid {p['CHROME_BORDER']};
    border-radius: {RADIUS_MD}px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: {RADIUS_SM}px; color: {p['CHROME_TEXT']}; }}
QMenu::item:selected {{ background: {p['ACCENT']}; color: {p['ON_ACCENT']}; }}
QMenu::item:disabled {{ color: {p['FG_MUTED']}; }}
QMenu::separator {{ height: 1px; background: {p['CHROME_BORDER']}; margin: 5px 10px; }}
/* Sized but never filled, so a checkable action (Full screen, Colour-blind
   mode, the Theme radio group) rendered its tick with the native style on a
   near-black menu. */
QMenu::indicator {{ width: 13px; height: 13px; margin-left: 4px; }}
QMenu::indicator:checked {{
    background: {p['ACCENT']}; border-radius: {RADIUS_SM}px;
}}
QMenu::indicator:non-exclusive:checked, QMenu::indicator:exclusive:checked {{
    background: {p['ACCENT']};
}}

/* -- scrollbars ---------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {p['BORDER']}; border-radius: {RADIUS_MD}px; min-height: 28px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['BORDER_STRONG']}; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p['BORDER']}; border-radius: {RADIUS_MD}px; min-width: 28px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['BORDER_STRONG']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- surfaces that were rendering Fusion-default --------------------------
   Each of these appears inside an otherwise heavily themed app and had no rule
   at all, so it fell back to the native style: a light-grey groupbox on a
   true-black dialog, a spinbox with unstyled steppers, a message box that looks
   like it belongs to a different program. QMessageBox in particular is the
   most prominent one — it is what the theme-switch prompt and the API-key
   upsell are built from. */
QDialog {{ background: {p['BG']}; }}
QGroupBox {{
    border: 1px solid {p['BORDER_STRONG']}; border-radius: {RADIUS_SM}px;
    margin-top: 8px; padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 8px;
    padding: 0 4px; color: {p['FG_DIM']};
    font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD}; letter-spacing: 1.5px;
}}
QTextBrowser {{
    background: {p['BG']}; color: {p['FG']};
    border: 1px solid transparent; selection-background-color: {p['SELECT_BLUE']};
}}
QTextBrowser[kbFocus="true"] {{ border-color: {p['FOCUS']}; }}
QScrollArea {{ background: {p['BG']}; border: 0; }}
QDialogButtonBox QPushButton {{ min-width: 76px; }}
QMessageBox {{ background: {p['CHROME']}; }}
QMessageBox QLabel {{ background: transparent; color: {p['CHROME_TEXT']}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: {p['BG_ELEV']}; border: 0; width: 14px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p['CHROME_HOVER']};
}}
QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {p['CHROME_HOVER']}; }}
QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {p['FG_MUTED']}; }}

/* -- status bar ---------------------------------------------------------- */
QStatusBar {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT_DIM']};
    border-top: 1px solid {p['CHROME_BORDER']}; font-size: {FONT_MD}px;
}}
QStatusBar::item {{ border: 0; }}
"""


def _build_ads_stylesheet(p: dict) -> str:
    return f"""
ads--CDockContainerWidget {{ background: {p['BORDER']}; }}
ads--CDockAreaWidget {{ background: {p['BG']}; border: 0; }}
ads--CDockAreaTitleBar {{
    background: {p['CHROME']}; border: 0; border-bottom: 1px solid {p['CHROME_BORDER']};
    padding: 0 2px;
}}
ads--CDockWidgetTab {{
    background: transparent; border: 0; border-right: 1px solid {p['CHROME_BORDER']};
    border-bottom: 2px solid transparent; padding: 4px 12px;
}}
ads--CDockWidgetTab:hover {{ background: {p['CHROME_HOVER']}; }}
ads--CDockWidgetTab[activeTab="true"] {{
    background: {p['CHROME_HOVER']}; border-bottom: 2px solid {p['ACCENT']};
}}
ads--CDockWidgetTab QLabel {{
    background: transparent; color: {p['CHROME_TEXT_DIM']};
    font-size: {FONT_MD}px; font-weight: {WEIGHT_MEDIUM};
}}
ads--CDockWidgetTab[activeTab="true"] QLabel {{ color: {p['CHROME_TEXT']}; }}
ads--CDockWidgetTab:hover QLabel {{ color: {p['CHROME_TEXT']}; }}
/* small, borderless close button inside each tab */
ads--CDockWidgetTab QPushButton, ads--CDockWidgetTab QToolButton {{
    background: transparent; border: 0; padding: 0; margin-left: 5px;
    qproperty-iconSize: 11px 11px;
}}
ads--CDockWidgetTab QPushButton:hover, ads--CDockWidgetTab QToolButton:hover {{
    background: rgba(128,128,128,0.22); border-radius: {RADIUS_SM}px;
}}
/* the area's controls — small and quiet */
ads--CTitleBarButton {{
    background: transparent; border: 0; padding: 1px;
    color: {p['CHROME_TEXT_DIM']}; qproperty-iconSize: 12px 12px;
}}
ads--CTitleBarButton:hover {{ background: rgba(128,128,128,0.22); border-radius: {RADIUS_SM}px; }}
/* The focused dock area. F11 maximizes "the focused panel" and Ctrl+W closes
   it, but nothing on screen said which one that was. QtAds already sets the
   ``focused`` property on the active tab and title bar — CDockManager
   FocusHighlighting is enabled in app.py — so this is only a matter of
   selecting on it. Deliberately quiet: a persistent amber underline on the
   active tab already exists, so this adds a title-bar rule rather than a
   second competing highlight. */
ads--CDockAreaTitleBar[focused="true"] {{
    border-bottom: 1px solid {p['ACCENT']};
}}
ads--CDockWidgetTab[focused="true"] QLabel {{ color: {p['CHROME_TEXT']}; }}

/* splitters: thin hairline dividers, subtle until hovered (then amber) */
ads--CDockSplitter::handle {{ background: {p['BORDER']}; }}
ads--CDockSplitter::handle:horizontal {{ width: 2px; }}
ads--CDockSplitter::handle:vertical {{ height: 2px; }}
ads--CDockSplitter::handle:hover {{ background: {p['ACCENT']}; }}
"""


STYLESHEET = _build_stylesheet(_ACTIVE)
# QtAds installs its OWN stylesheet on the dock manager, which outranks the
# app-global sheet; the docking chrome is applied to the dock manager directly
# (see app.py) via this dedicated sheet.
ADS_STYLESHEET = _build_ads_stylesheet(_ACTIVE)


def apply_theme(app: QApplication) -> None:
    p = _ACTIVE
    app.setStyle("Fusion")
    # Pixels, matching the stylesheet. This was ``QFont(UI_FONT, 9)`` — 9
    # *point*, about 12px at 96 DPI — while the sheet said 11px, so painted
    # widgets, item delegates and native dialogs rendered a size that appeared
    # nowhere in the design. Same unit, same number, one scale.
    base_font = QFont(UI_FONT)
    base_font.setPixelSize(FONT_MD)
    app.setFont(base_font)
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(p["BG"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(p["FG"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(p["BG"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p["BG_ALT"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(p["FG"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(p["BG_ELEV"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(p["CHROME_TEXT"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p["CHROME"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p["CHROME_TEXT"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p["SELECT_BLUE"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p["CHROME_TEXT"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p["FG_MUTED"]))
    pal.setColor(QPalette.ColorRole.Link, QColor(p["ACCENT"]))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)


# -- up/down tick helpers ---------------------------------------------------
# Shared by every panel that colors a signed change value, so the color-blind
# palette swap and the ▲/▼ direction glyph are decided in exactly one place.

def tick_color(value) -> str:
    """The up/down color for a signed number, from the active palette (already
    color-blind-safe when that mode is on). ``None``/negative reads as down."""
    return UP if (value is not None and value >= 0) else DOWN


def tick_glyph(value) -> str:
    """A ``"▲ "`` / ``"▼ "`` prefix for a signed number when color-blind mode is
    on, else ``""`` — so direction survives without color."""
    if not colorblind_enabled():
        return ""
    return "▲ " if (value is not None and value >= 0) else "▼ "


def apply_tick(item, value, *, text: str | None = None, glyph: bool = True) -> None:
    """Color a table cell by the sign of ``value`` and, in color-blind mode,
    prefix its text with ▲/▼. Pass ``text`` to set the cell text here, or call
    after the item's text is already set to prepend the glyph to it.

    Set ``glyph=False`` for a secondary cell colored from the same sign (e.g. a
    change *and* a %-change column in one row) so only one ▲/▼ shows per row.

    Replaces the repeated ``QColor(UP) if v >= 0 else QColor(DOWN)`` +
    ``setForeground`` pattern across the data panels.
    """
    if text is not None:
        item.setText(text)
    item.setForeground(QColor(tick_color(value)))
    if glyph:
        g = tick_glyph(value)
        if g:
            item.setText(g + item.text())
