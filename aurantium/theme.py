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

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

#: QSettings key holding "dark" | "light"
THEME_SETTINGS_KEY = "ui/theme"
THEMES = ("dark", "light")
DEFAULT_THEME = "dark"

# Fonts: Segoe UI for chrome, Consolas for figures (theme-independent).
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

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
}

_PALETTES = {"dark": _DARK, "light": _LIGHT}


def _read_theme_name() -> str:
    """The saved theme name, defaulting to dark. Safe to call before a
    QApplication exists — QSettings just returns the default then."""
    try:
        name = QSettings().value(THEME_SETTINGS_KEY, DEFAULT_THEME, type=str)
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
        QSettings().setValue(THEME_SETTINGS_KEY, name)


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
        return bool(QSettings().value(COLORBLIND_SETTINGS_KEY, False, type=bool))
    except Exception:
        return False


def colorblind_enabled() -> bool:
    """Whether the color-blind (deuteranopia-safe) mode is active."""
    return _read_colorblind()


def set_colorblind(on: bool) -> None:
    """Persist the color-blind choice. Applied on the next launch (the caller
    prompts for a restart), the same way a theme change is, so every panel and
    chart renders one consistent palette."""
    QSettings().setValue(COLORBLIND_SETTINGS_KEY, bool(on))


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
* {{ outline: 0; }}
QWidget {{
    background: {p['BG']}; color: {p['FG']};
    font-family: "{UI_FONT}"; font-size: 11px;
}}
QToolTip {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT']};
    border: 1px solid {p['CHROME_BORDER']}; border-radius: 3px; padding: 5px 9px;
}}

/* -- panel header: a thin context strip under the title bar --------------- */
QWidget#panelHeader {{
    background: {p['BG']}; border-bottom: 1px solid {p['BORDER']};
}}
QLabel#panelEyebrow {{
    color: {p['FG_MUTED']}; font-size: 10px; font-weight: 700; letter-spacing: 1.5px;
}}
QLabel#panelStatus {{
    color: {p['ACCENT_DEEP']}; font-size: 10px; font-family: "{MONO_FONT}";
}}

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
QMenuBar::item {{ padding: 5px 10px; border-radius: 2px; color: {p['CHROME_TEXT_DIM']}; }}
QMenuBar::item:selected {{ background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; }}
/* The blanket QWidget rule above paints every plain widget BG (true black),
   which is visibly darker than CHROME — override it so the logo's transparent
   PNG shows the same chrome grey as the rest of the row, not a black box. */
QLabel#menuBarLogo {{ background: transparent; padding: 0px 0px 0px 14px; }}

QWidget#commandBar {{ background: {p['BG']}; border-bottom: 1px solid {p['BORDER']}; }}
QLabel#commandLabel {{
    color: {p['ACCENT']}; font-size: 11px; font-weight: 700; letter-spacing: 2px;
}}
QLineEdit#commandInput {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: 2px;
    padding: 4px 10px; color: {p['ACCENT']}; font-family: "{MONO_FONT}"; font-size: 13px;
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
}}
QLineEdit#commandInput:focus {{ border-color: {p['ACCENT']}; }}

/* -- tables: data surface, header band, accent-ready cells --------------- */
QTableWidget, QTableView {{
    background: {p['BG']}; alternate-background-color: {p['BG_ALT']};
    gridline-color: {p['BORDER']}; border: 0;
    selection-background-color: {p['SELECT_BLUE']}; selection-color: {p['CHROME_TEXT']};
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QTableView::item {{ padding: 1px 4px; }}
QHeaderView {{ background: {p['HEADER_BLUE']}; }}
QHeaderView::section {{
    background: {p['HEADER_BLUE']}; color: {p['CHROME_TEXT_DIM']}; border: 0;
    border-right: 1px solid {p['BORDER']}; border-bottom: 1px solid {p['BORDER']};
    padding: 4px 6px; font-family: "{UI_FONT}"; font-size: 10px; font-weight: 700;
    letter-spacing: 0.4px;
}}
QHeaderView::section:hover {{ background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; }}
QHeaderView::section:last {{ border-right: 0; }}
QTableCornerButton::section {{ background: {p['HEADER_BLUE']}; border: 0; }}
QListWidget {{
    background: {p['BG']}; alternate-background-color: {p['BG_ALT']}; border: 0;
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QListWidget::item {{ padding: 2px 4px; }}
QListWidget::item:selected {{ background: {p['SELECT_BLUE']}; color: {p['CHROME_TEXT']}; }}

/* -- inner tab widgets (e.g. Portfolio) — amber underline like the docks -- */
QTabWidget::pane {{ border: 0; border-top: 1px solid {p['BORDER']}; }}
QTabBar::tab {{
    background: transparent; color: {p['CHROME_TEXT_DIM']};
    padding: 5px 12px; border: 0; border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:hover {{ color: {p['CHROME_TEXT']}; }}
QTabBar::tab:selected {{ color: {p['CHROME_TEXT']}; border-bottom: 2px solid {p['ACCENT']}; }}

/* -- inputs -------------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: 2px;
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
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QListView#suggestPopup::item {{ padding: 4px 8px; }}
QListView#suggestPopup::item:selected {{
    background: {p['ACCENT']}; color: {p['ON_ACCENT']};
}}

/* -- buttons: flat tabs, amber when active ------------------------------- */
QPushButton {{
    background: {p['BG_ELEV']}; color: {p['CHROME_TEXT_DIM']};
    border: 1px solid {p['BORDER_STRONG']}; border-radius: 2px;
    padding: 5px 13px; font-size: 11px; font-weight: 600;
}}
QPushButton:hover {{
    background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; border-color: {p['BORDER_STRONG']};
}}
QPushButton:pressed {{ background: {p['HEADER_BLUE']}; }}
QPushButton:checked {{
    background: {p['ACCENT']}; color: {p['ON_ACCENT']}; border-color: {p['ACCENT']}; font-weight: 700;
}}
QPushButton:checked:hover {{ background: {p['ACCENT_DEEP']}; border-color: {p['ACCENT_DEEP']}; }}
QPushButton:disabled {{ color: {p['FG_MUTED']}; border-color: {p['BORDER']}; background: {p['BG_ALT']}; }}

QToolButton {{
    background: transparent; border: 0; border-radius: 2px;
    color: {p['CHROME_TEXT']}; padding: 3px;
}}
QToolButton:hover {{ background: rgba(128,128,128,0.18); }}
QToolButton:pressed {{ background: rgba(128,128,128,0.30); }}

/* -- checkboxes / radios ------------------------------------------------- */
QCheckBox, QRadioButton {{ spacing: 6px; }}

/* -- menus --------------------------------------------------------------- */
QMenu {{
    background: {p['CHROME']}; border: 1px solid {p['CHROME_BORDER']};
    border-radius: 3px; padding: 4px;
}}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 2px; color: {p['CHROME_TEXT']}; }}
QMenu::item:selected {{ background: {p['ACCENT']}; color: {p['ON_ACCENT']}; }}
QMenu::item:disabled {{ color: {p['FG_MUTED']}; }}
QMenu::separator {{ height: 1px; background: {p['CHROME_BORDER']}; margin: 5px 10px; }}
QMenu::indicator {{ width: 13px; height: 13px; }}

/* -- scrollbars ---------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {p['BORDER']}; border-radius: 3px; min-height: 28px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['BORDER_STRONG']}; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p['BORDER']}; border-radius: 3px; min-width: 28px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['BORDER_STRONG']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- status bar ---------------------------------------------------------- */
QStatusBar {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT_DIM']};
    border-top: 1px solid {p['CHROME_BORDER']}; font-size: 11px;
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
    font-size: 11px; font-weight: 600;
}}
ads--CDockWidgetTab[activeTab="true"] QLabel {{ color: {p['CHROME_TEXT']}; }}
ads--CDockWidgetTab:hover QLabel {{ color: {p['CHROME_TEXT']}; }}
/* small, borderless close button inside each tab */
ads--CDockWidgetTab QPushButton, ads--CDockWidgetTab QToolButton {{
    background: transparent; border: 0; padding: 0; margin-left: 5px;
    qproperty-iconSize: 11px 11px;
}}
ads--CDockWidgetTab QPushButton:hover, ads--CDockWidgetTab QToolButton:hover {{
    background: rgba(128,128,128,0.22); border-radius: 2px;
}}
/* the area's controls — small and quiet */
ads--CTitleBarButton {{
    background: transparent; border: 0; padding: 1px;
    color: {p['CHROME_TEXT_DIM']}; qproperty-iconSize: 12px 12px;
}}
ads--CTitleBarButton:hover {{ background: rgba(128,128,128,0.22); border-radius: 2px; }}
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
    app.setFont(QFont(UI_FONT, 9))
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
