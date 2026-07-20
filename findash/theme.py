"""Terminal theme — Bloomberg-authentic dark (default), plus a light variant.

The default look is modeled on a Bloomberg Launchpad screen: true-black data
surfaces, a desaturated steel-blue title bar on every panel, amber as a primary
text color (not just an accent), blue column-header bands, true green/red for
ticks, and dense, monospaced tabular figures.

A light variant is available (View ▸ Theme). Colors are exposed as module-level
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
    """A copy of a theme's raw color map (defaults to the active theme). Lets
    other modules (e.g. the chart's per-panel colors) tell a theme-derived
    default from a genuine user customization across both themes."""
    return dict(_PALETTES.get(name or _read_theme_name(), _ACTIVE))


def set_theme(name: str) -> None:
    """Persist the theme choice. Takes effect on the next launch (the caller
    prompts for a restart), so the whole app — charts included — renders in one
    consistent theme rather than a half-restyled mix."""
    if name in _PALETTES:
        QSettings().setValue(THEME_SETTINGS_KEY, name)


# -- activate the saved palette: publish its colors as module constants -----
_ACTIVE = _PALETTES[_read_theme_name()]
globals().update(_ACTIVE)
# kept for import stability (referenced by name elsewhere / historically)
CHROME_HI = _ACTIVE["CHROME_HOVER"]
CHROME_LO = _ACTIVE["CHROME"]


def _build_stylesheet(p: dict) -> str:
    chrome_grad = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
        f" stop:0 {p['CHROME_HOVER']}, stop:0.5 {p['CHROME']}, stop:1 {p['CHROME']})"
    )
    return f"""
* {{ outline: 0; }}
QWidget {{
    background: {p['BG']}; color: {p['FG']};
    font-family: "{UI_FONT}"; font-size: 11px;
}}
QToolTip {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT']};
    border: 1px solid {p['CHROME_BORDER']}; padding: 4px 7px;
}}

/* -- panel header: a thin context strip under the title bar --------------- */
QWidget#panelHeader {{
    background: {p['BG']}; border-bottom: 1px solid {p['BORDER']};
}}
QLabel#panelEyebrow {{
    color: {p['FG_MUTED']}; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;
}}
QLabel#panelStatus {{
    color: {p['ACCENT_DEEP']}; font-size: 10px; font-family: "{MONO_FONT}";
}}

/* -- app top bar: steel-blue like the Launchpad title bar ---------------- */
QMenuBar {{
    background: {p['CHROME']}; color: {p['CHROME_TEXT']};
    border-bottom: 1px solid {p['CHROME_BORDER']}; padding: 0px 4px;
}}
QMenuBar::item {{ padding: 3px 9px; border-radius: 3px; color: {p['CHROME_TEXT_DIM']}; }}
QMenuBar::item:selected {{ background: {p['CHROME_HOVER']}; color: {p['CHROME_TEXT']}; }}

QWidget#commandBar {{ background: {p['BG']}; border-bottom: 1px solid {p['BORDER_STRONG']}; }}
QLabel#commandLabel {{
    color: {p['ACCENT']}; font-size: 11px; font-weight: 700; letter-spacing: 2px;
}}
QLineEdit#commandInput {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: 3px;
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
QHeaderView::section {{
    background: {p['HEADER_BLUE']}; color: {p['CHROME_TEXT_DIM']}; border: 0;
    border-right: 1px solid {p['CHROME_BORDER']}; border-bottom: 1px solid {p['CHROME_BORDER']};
    padding: 4px 6px; font-family: "{UI_FONT}"; font-size: 10px; font-weight: 700;
    letter-spacing: 0.4px;
}}
QHeaderView::section:last {{ border-right: 0; }}
QTableCornerButton::section {{ background: {p['HEADER_BLUE']}; border: 0; }}
QListWidget {{
    background: {p['BG']}; alternate-background-color: {p['BG_ALT']}; border: 0;
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QListWidget::item {{ padding: 2px 4px; }}
QListWidget::item:selected {{ background: {p['SELECT_BLUE']}; color: {p['CHROME_TEXT']}; }}

/* -- inputs -------------------------------------------------------------- */
QLineEdit {{
    background: {p['BG']}; border: 1px solid {p['BORDER_STRONG']}; border-radius: 3px;
    padding: 4px 8px; color: {p['FG']};
    selection-background-color: {p['ACCENT']}; selection-color: {p['ON_ACCENT']};
}}
QLineEdit:focus {{ border-color: {p['ACCENT']}; }}

/* -- buttons: flat tabs, amber when active ------------------------------- */
QPushButton {{
    background: {p['BG_ELEV']}; color: {p['CHROME_TEXT_DIM']};
    border: 1px solid {p['BORDER_STRONG']}; border-radius: 3px;
    padding: 4px 11px; font-size: 11px; font-weight: 600;
}}
QPushButton:hover {{ color: {p['CHROME_TEXT']}; border-color: {p['CHROME_HOVER']}; }}
QPushButton:pressed {{ background: {p['HEADER_BLUE']}; }}
QPushButton:checked {{
    background: {p['ACCENT']}; color: {p['ON_ACCENT']}; border-color: {p['ACCENT']}; font-weight: 700;
}}
QPushButton:disabled {{ color: {p['FG_MUTED']}; border-color: {p['BORDER']}; }}

QToolButton {{ background: transparent; border: 0; color: {p['CHROME_TEXT']}; padding: 2px; }}
QToolButton:hover {{ background: rgba(128,128,128,0.18); border-radius: 3px; }}

/* -- menus --------------------------------------------------------------- */
QMenu {{ background: {p['CHROME']}; border: 1px solid {p['CHROME_BORDER']}; padding: 3px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 3px; color: {p['CHROME_TEXT']}; }}
QMenu::item:selected {{ background: {p['ACCENT']}; color: {p['ON_ACCENT']}; }}
QMenu::separator {{ height: 1px; background: {p['CHROME_BORDER']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 13px; height: 13px; }}

/* -- scrollbars ---------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {p['BORDER_STRONG']}; border-radius: 4px; min-height: 28px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {p['CHROME_HOVER']}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p['BORDER_STRONG']}; border-radius: 4px; min-width: 28px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p['CHROME_HOVER']}; }}
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
ads--CDockContainerWidget {{ background: {p['CHROME_BORDER']}; }}
ads--CDockAreaWidget {{ background: {p['BG']}; border: 0; }}
ads--CDockAreaTitleBar {{
    background: {p['CHROME']}; border: 0; border-bottom: 1px solid {p['CHROME_BORDER']};
    padding: 0;
}}
ads--CDockWidgetTab {{
    background: {p['CHROME']}; border: 0; border-right: 1px solid {p['CHROME_BORDER']};
    padding: 2px 9px;
}}
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
/* splitters: wide, easy-to-grab, subtle until hovered (then amber) */
ads--CDockSplitter::handle {{ background: {p['CHROME_BORDER']}; }}
ads--CDockSplitter::handle:horizontal {{ width: 6px; }}
ads--CDockSplitter::handle:vertical {{ height: 6px; }}
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
