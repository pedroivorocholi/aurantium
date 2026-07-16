"""Terminal theme — Bloomberg-authentic: amber-on-black with steel-blue chrome.

The look is modeled directly on a Bloomberg Launchpad screen: true-black data
surfaces, a desaturated steel-blue title bar on every panel, amber as a primary
text color (not just an accent), blue column-header bands, true green/red for
ticks, and dense, monospaced tabular figures. Chrome is blue; data is amber and
white on black.
"""

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

# -- palette ---------------------------------------------------------------
ACCENT = "#ffab2e"        # amber — accent AND the default data-label color
ACCENT_DEEP = "#c8842a"   # dimmed amber

BG = "#000000"            # true black — all data surfaces
BG_ALT = "#0b0c0e"        # near-black zebra stripe
BG_ELEV = "#1b2530"       # raised controls (buttons), bluish-dark

# Chrome — dark, thin, understated. Kept close to the black canvas so title
# bars and the top/bottom bars recede instead of reading as heavy gray slabs;
# the amber active-tab underline and light text do the distinguishing.
CHROME = "#161b21"        # dark chrome — all title bars / top / bottom
CHROME_HOVER = "#242c35"  # hover / active tab (a subtle lift)
BG_HEADER = "#222d39"     # section rows inside panels (a touch bluer to mark them)
CHROME_HI = CHROME_HOVER  # kept for import stability
CHROME_LO = CHROME
CHROME_BORDER = "#0c1015" # thin outline between chrome and black
CHROME_TEXT = "#d7dde3"   # light text on chrome
CHROME_TEXT_DIM = "#828c97"

HEADER_BLUE = "#1a2129"   # table column-header band (subtle, not a gray slab)
SELECT_BLUE = "#1d3143"   # selected row

BORDER = "#141820"        # hairline dividers on black
BORDER_STRONG = "#28303a"

FG = "#cdd2d6"            # primary text (cool off-white)
FG_DIM = "#7c858e"        # secondary text, axis labels
FG_MUTED = "#535b64"      # eyebrows, disabled

UP = "#33c46a"            # gains — true green
DOWN = "#ff4d4d"          # losses — red

# Fonts: Segoe UI for chrome, Consolas for figures.
UI_FONT = "Segoe UI"
MONO_FONT = "Consolas"

# flat chrome — a hair of top sheen only, no heavy gradient
_CHROME_GRAD = (
    f"qlineargradient(x1:0, y1:0, x2:0, y2:1,"
    f" stop:0 {CHROME_HOVER}, stop:0.5 {CHROME}, stop:1 {CHROME})"
)

STYLESHEET = f"""
* {{ outline: 0; }}
QWidget {{
    background: {BG}; color: {FG};
    font-family: "{UI_FONT}"; font-size: 11px;
}}
QToolTip {{
    background: {CHROME_LO}; color: {CHROME_TEXT};
    border: 1px solid {CHROME_BORDER}; padding: 4px 7px;
}}

/* -- panel header: a thin black context strip under the blue title bar --- */
QWidget#panelHeader {{
    background: {BG}; border-bottom: 1px solid {BORDER};
}}
QLabel#panelEyebrow {{
    color: {FG_MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 1.5px;
}}
QLabel#panelStatus {{
    color: {ACCENT_DEEP}; font-size: 10px; font-family: "{MONO_FONT}";
}}

/* -- app top bar: steel-blue like the Launchpad title bar ---------------- */
QMenuBar {{
    background: {CHROME}; color: {CHROME_TEXT};
    border-bottom: 1px solid {CHROME_BORDER}; padding: 0px 4px;
}}
QMenuBar::item {{ padding: 3px 9px; border-radius: 3px; color: {CHROME_TEXT_DIM}; }}
QMenuBar::item:selected {{ background: {CHROME_HOVER}; color: {CHROME_TEXT}; }}

QWidget#commandBar {{ background: {BG}; border-bottom: 1px solid {BORDER_STRONG}; }}
QLabel#commandLabel {{
    color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 2px;
}}
QLineEdit#commandInput {{
    background: {BG}; border: 1px solid {BORDER_STRONG}; border-radius: 3px;
    padding: 4px 10px; color: {ACCENT}; font-family: "{MONO_FONT}"; font-size: 13px;
    selection-background-color: {ACCENT}; selection-color: {BG};
}}
QLineEdit#commandInput:focus {{ border-color: {ACCENT}; }}

/* -- tables: black data, blue header band, amber-ready cells ------------- */
QTableWidget, QTableView {{
    background: {BG}; alternate-background-color: {BG_ALT};
    gridline-color: {BORDER}; border: 0;
    selection-background-color: {SELECT_BLUE}; selection-color: {CHROME_TEXT};
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QTableView::item {{ padding: 1px 4px; }}
QHeaderView::section {{
    background: {HEADER_BLUE}; color: {CHROME_TEXT_DIM}; border: 0;
    border-right: 1px solid {CHROME_BORDER}; border-bottom: 1px solid {CHROME_BORDER};
    padding: 4px 6px; font-family: "{UI_FONT}"; font-size: 10px; font-weight: 700;
    letter-spacing: 0.4px;
}}
QHeaderView::section:last {{ border-right: 0; }}
QTableCornerButton::section {{ background: {HEADER_BLUE}; border: 0; }}
QListWidget {{
    background: {BG}; alternate-background-color: {BG_ALT}; border: 0;
    font-family: "{MONO_FONT}"; font-size: 11px;
}}
QListWidget::item {{ padding: 2px 4px; }}
QListWidget::item:selected {{ background: {SELECT_BLUE}; color: {CHROME_TEXT}; }}

/* -- inputs -------------------------------------------------------------- */
QLineEdit {{
    background: {BG}; border: 1px solid {BORDER_STRONG}; border-radius: 3px;
    padding: 4px 8px; color: {FG};
    selection-background-color: {ACCENT}; selection-color: {BG};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

/* -- buttons: flat tabs, amber when active ------------------------------- */
QPushButton {{
    background: {BG_ELEV}; color: {CHROME_TEXT_DIM};
    border: 1px solid {BORDER_STRONG}; border-radius: 3px;
    padding: 4px 11px; font-size: 11px; font-weight: 600;
}}
QPushButton:hover {{ color: {CHROME_TEXT}; border-color: {CHROME_HI}; }}
QPushButton:pressed {{ background: {HEADER_BLUE}; }}
QPushButton:checked {{
    background: {ACCENT}; color: {BG}; border-color: {ACCENT}; font-weight: 700;
}}
QPushButton:disabled {{ color: {FG_MUTED}; border-color: {BORDER}; }}

QToolButton {{ background: transparent; border: 0; color: {CHROME_TEXT}; padding: 2px; }}
QToolButton:hover {{ background: rgba(255,255,255,0.12); border-radius: 3px; }}

/* -- menus --------------------------------------------------------------- */
QMenu {{ background: {CHROME_LO}; border: 1px solid {CHROME_BORDER}; padding: 3px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 3px; color: {CHROME_TEXT}; }}
QMenu::item:selected {{ background: {ACCENT}; color: {BG}; }}
QMenu::separator {{ height: 1px; background: {CHROME_BORDER}; margin: 4px 8px; }}

/* -- scrollbars ---------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: 4px; min-height: 28px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {CHROME_HI}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {BORDER_STRONG}; border-radius: 4px; min-width: 28px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {CHROME_HI}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* -- status bar ---------------------------------------------------------- */
QStatusBar {{
    background: {CHROME}; color: {CHROME_TEXT_DIM};
    border-top: 1px solid {CHROME_BORDER}; font-size: 11px;
}}
QStatusBar::item {{ border: 0; }}
"""

# QtAds installs its OWN stylesheet directly on the dock manager, which outranks
# the app-global sheet above. So the docking chrome must be applied to the dock
# manager itself (see app.py) via this dedicated sheet.
ADS_STYLESHEET = f"""
ads--CDockContainerWidget {{ background: {CHROME_BORDER}; }}
ads--CDockAreaWidget {{ background: {BG}; border: 0; }}
ads--CDockAreaTitleBar {{
    background: {CHROME}; border: 0; border-bottom: 1px solid {CHROME_BORDER};
    padding: 0;
}}
ads--CDockWidgetTab {{
    background: {CHROME}; border: 0; border-right: 1px solid {CHROME_BORDER};
    padding: 2px 9px;
}}
ads--CDockWidgetTab[activeTab="true"] {{
    background: {CHROME_HOVER}; border-bottom: 2px solid {ACCENT};
}}
ads--CDockWidgetTab QLabel {{
    background: transparent; color: {CHROME_TEXT_DIM};
    font-size: 11px; font-weight: 600;
}}
ads--CDockWidgetTab[activeTab="true"] QLabel {{ color: {CHROME_TEXT}; }}
ads--CDockWidgetTab:hover QLabel {{ color: {CHROME_TEXT}; }}
/* small, borderless close button inside each tab (was a big boxed X) */
ads--CDockWidgetTab QPushButton, ads--CDockWidgetTab QToolButton {{
    background: transparent; border: 0; padding: 0; margin-left: 5px;
    qproperty-iconSize: 11px 11px;
}}
ads--CDockWidgetTab QPushButton:hover, ads--CDockWidgetTab QToolButton:hover {{
    background: rgba(255,255,255,0.14); border-radius: 2px;
}}
/* the area's ▾ / float / ✕ buttons — small and quiet */
ads--CTitleBarButton {{
    background: transparent; border: 0; padding: 1px;
    color: {CHROME_TEXT_DIM}; qproperty-iconSize: 12px 12px;
}}
ads--CTitleBarButton:hover {{ background: rgba(255,255,255,0.14); border-radius: 2px; }}
/* splitters: a wide, easy-to-grab handle that stays visually dark/subtle,
   and lights up amber on hover so it's clearly draggable */
ads--CDockSplitter::handle {{ background: {CHROME_BORDER}; }}
ads--CDockSplitter::handle:horizontal {{ width: 6px; }}
ads--CDockSplitter::handle:vertical {{ height: 6px; }}
ads--CDockSplitter::handle:hover {{ background: {ACCENT}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont(UI_FONT, 9))
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(FG))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_ALT))
    pal.setColor(QPalette.ColorRole.Text, QColor(FG))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG_ELEV))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(CHROME_TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(CHROME_LO))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(CHROME_TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(SELECT_BLUE))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(CHROME_TEXT))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(FG_MUTED))
    pal.setColor(QPalette.ColorRole.Link, QColor(ACCENT))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
