"""First-run onboarding / help modal.

A tabbed dialog with a keyboard & mouse cheat sheet and a short dashboard
guide. Shown automatically on first launch (gated by a ``QSettings`` flag) and
on demand from ``Settings ▸ Keyboard Shortcuts & Guide…`` or the F1 key.

The cheat sheet is hand-audited against the codebase — every shortcut listed is
one the app actually binds (see ``app.py`` and ``panels/watchlist.py``) plus the
PyQtGraph default chart controls; nothing aspirational.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, BG_ELEV, CHROME_TEXT, FG, FG_DIM

#: bump the suffix if the content changes enough to warrant re-showing everyone
_SETTINGS_KEY = "has_seen_onboarding_v1"


def _page_css() -> str:
    return f"""
    <style>
      body {{ color: {FG}; font-size: 13px; }}
      h2 {{ color: {ACCENT}; font-size: 15px; margin: 14px 0 4px 0; }}
      p  {{ color: {FG_DIM}; margin: 2px 0 10px 0; }}
      table {{ border-collapse: collapse; margin: 2px 0 12px 0; width: 100%; }}
      td {{ padding: 3px 10px 3px 0; vertical-align: top; }}
      td.k {{ white-space: nowrap; width: 34%; }}
      kbd {{
        background: {BG_ELEV}; color: {CHROME_TEXT};
        border-radius: 3px; padding: 1px 6px; font-family: monospace;
      }}
      td.d {{ color: {FG}; }}
    </style>
    """


def _kbd(*keys: str) -> str:
    return " ".join(f"<kbd>{k}</kbd>" for k in keys)


def _row(keys_html: str, desc: str) -> str:
    return f'<tr><td class="k">{keys_html}</td><td class="d">{desc}</td></tr>'


_SHORTCUTS_HTML = _page_css() + f"""
<body>
<h2>Global</h2>
<table>
  {_row(_kbd("Ctrl", "F"), "Jump to the SYMBOL box and select its text")}
  {_row(_kbd("Enter"), "In the SYMBOL box: send the ticker (or run a /command)")}
  {_row(_kbd("↑") + " / " + _kbd("↓"), "In the SYMBOL box: recall previous entries")}
  {_row(_kbd("Tab"), "In the SYMBOL box: autocomplete a symbol or /command")}
  {_row(_kbd("F5"), "Refresh all live feeds (quotes, news, charts, financials)")}
  {_row(_kbd("F11"), "Maximize the focused panel — press again to restore")}
  {_row(_kbd("Esc"), "Restore a maximized panel")}
  {_row(_kbd("Shift", "F11"), "Toggle borderless full screen (Aurantium opens in full screen)")}
  {_row(_kbd("Ctrl", "W"), "Close the focused panel")}
  {_row(_kbd("Ctrl", "Shift", "T"), "Reopen the last panel you closed")}
  {_row(_kbd("F1"), "Open this Keyboard Shortcuts &amp; Guide window")}
  {_row(_kbd("Ctrl", "Z"), "Undo the last edit — drawing, watchlist / portfolio / monitor change")}
  {_row(_kbd("Ctrl", "S"), "Save the current layout under a name")}
  {_row(_kbd("Ctrl", "Q"), "Quit Aurantium")}
</table>

<h2>Panels &amp; symbols</h2>
<table>
  {_row("Click a row", "Set that symbol for the panel's link group — linked panels follow")}
  {_row("Link badge (top-right)", "Change a panel's link group, or unlink it")}
  {_row(_kbd("Ctrl", "1") + "–" + _kbd("Ctrl", "4"), "Set the focused panel's link group (A/B/C/D)")}
  {_row("Drag a panel tab", "Move &amp; snap it into a new position (panels never float free)")}
  {_row("Middle-click a tab", "Close that panel")}
</table>

<h2>Charts</h2>
<table>
  {_row("Move the mouse", "Crosshair snaps to the nearest bar with a date + O/H/L/C readout")}
  {_row("Left-drag", "Pan the view")}
  {_row("Mouse wheel", "Zoom in / out")}
  {_row("Right-drag", "Scale the axes independently")}
  {_row("Right-click", "Chart menu — type, colors, drawing tools, export image")}
  {_row("Right-click &#9656; Drawing", "Trendline, horizontal line, or channels — points snap to candles; saved with the layout")}
  {_row("“A” button (bottom-left)", "Auto-range: reset the view to fit the data")}
</table>

<h2>Tables</h2>
<table>
  {_row("Filter box (above a table)", "Type to show only matching rows — live substring match")}
  {_row("Click a header", "Sort by that column; click again to reverse (sortable tables)")}
  {_row("Right-click", "Export to CSV, or show/hide individual columns")}
  {_row(_kbd("↑"), "/ " + _kbd("↓") + " — move the row selection")}
</table>

<h2>Watchlist</h2>
<table>
  {_row(_kbd("Delete"), "/ " + _kbd("Backspace") + " — remove the selected watchlist row")}
</table>
</body>
"""


_GUIDE_HTML = _page_css() + """
<body>
<h2>Getting started</h2>
<p>Type a ticker (e.g. <b>AAPL</b>, <b>MSFT</b>, <b>ES=F</b>) in the SYMBOL box
at the top and press Enter. Every panel in the same <i>link group</i> updates to
that symbol at once. A fresh install opens to an empty workspace — add panels
from the <b>Panels</b> menu.</p>

<h2>The command bar</h2>
<p>The SYMBOL box up top is also a command line. Type a ticker and press Enter to
drive the linked panels, or type a <b>/command</b>: <code>/add &lt;panel&gt;</code>
opens a panel, <code>/layout &lt;name&gt;</code> loads a saved layout,
<code>/save &lt;name&gt;</code> saves the current one, and <code>/refresh</code>
reloads every feed. Press <b>Tab</b> to autocomplete a symbol or command, and
<b>↑ / ↓</b> to recall what you typed before (remembered across sessions).</p>

<h2>Arranging your workspace</h2>
<p>Drag a panel by its tab to move and snap it beside, above, or below another
panel; drop it onto a tab strip to stack them as tabs. Panels relocate and snap
but never tear off into free-floating windows. Pin a panel to a window edge to
turn it into a slide-out tab. Press <b>F11</b> to focus on one panel full-window,
Esc or F11 to bring the rest back.</p>

<h2>Settings</h2>
<p>Everything except <b>Panels</b> now lives under the <b>Settings</b> menu —
theme, color-blind mode, layouts, API keys, the guide, updates, and quit.</p>

<h2>Theme &amp; accessibility</h2>
<p>Switch between the <b>dark</b> (default) and <b>light</b> theme under
<b>Settings &#9656; Theme</b>. Aurantium restarts to apply it, so the whole
interface — charts included — matches the theme you pick. <b>Settings &#9656;
Color-blind mode</b> swaps the green/red up-down colors for a deuteranopia-safe
blue/orange pair and adds <b>▲ / ▼</b> direction marks to change values, so
direction reads without relying on color; it also restarts to apply.</p>

<h2>Symbol search &amp; link groups</h2>
<p>Each panel carries a colored <b>link badge</b> in its top-right corner. Panels
in the same group share a symbol, so clicking a name in one (a mover, a holding,
a commodity) drives the others. Use the badge to move a panel to another group,
or Unlink it so it navigates on its own.</p>

<h2>Charts</h2>
<p>Hover the price chart to read any bar: a <b>crosshair</b> snaps to the nearest
candle and shows its date and open/high/low/close in the corner. Right-click the
chart for type (candles, bars, line, area), colors, indicators, and
<b>Drawing</b> tools: a <i>trendline</i>, a <i>horizontal line</i>, or a
<i>channel</i> (parallel, flat-bottom, disjoint). Pick a tool, then click to place
its points — each click <b>snaps to the nearest candle</b>, and the shape follows
your cursor between clicks. Drawings are <b>blue</b> by default (change it under
Drawing &#9656; Drawing color…) and are saved with the layout for that symbol.</p>

<h2>News</h2>
<p>In a News or Topic News panel, <b>single-click</b> a headline for a quick
preview snippet below the list — the headline dims once you've read it.
<b>Double-click</b> to open the full article in your browser. The filter box
narrows the headlines live, and read/unread state is remembered with the
layout.</p>

<h2>Price alerts &amp; the tray</h2>
<p>Add a <b>Price Alerts</b> panel to set threshold rules (e.g. <i>AAPL price
above 200</i>, or <i>TSLA change% below −5</i>). When a rule triggers, Aurantium
shows a tray notification — rules keep running in the background as long as the
app is open, and each fires once until the condition resets. Right-click the tray
icon to Show/Hide/Quit; turn on <b>Close to tray</b> — there or under
<b>Settings</b> — if you'd like the window's ✕ to minimize to the tray instead of
quitting (off by default, so ✕ quits as usual).</p>

<h2>Macro, commodities &amp; positioning</h2>
<p>The <b>Macro / Rates</b> panel is built for a weekly macro check: a US
Treasury <b>yield curve</b>, a <b>Macro Monitor</b> (Dollar Index by default),
and weekly <b>CFTC positioning</b> for gold, silver, copper, Brent, and natural
gas. All three lists are configurable via <b>Edit…</b> — including FRED data
series like the 10-year real yield (add a row as
<code>Label,fred:DFII10</code>; needs a free FRED key under Settings &#9656; API
Keys…). Click an instrument or positioning row to drive the linked panels.</p>
<p>The <b>Futures Curve</b> panel plots the price of each upcoming delivery
month for a commodity and says in plain words whether the curve slopes
<i>upward</i> (contango: later deliveries cost more) or <i>downward</i>
(backwardation: nearer deliveries cost more). The <b>Positioning History
(CFTC)</b> panel charts about two years of the weekly net position of large
speculative traders. In both, pick a commodity with the buttons — linked
panels follow — or click a commodity anywhere else (Commodities, Macro/Rates)
and these panels re-center on it.</p>

<h2>Portfolio</h2>
<p>In the <b>Positions</b> tab add a holding: symbol, <b>Qty</b>, <b>Price</b>,
and a <b>Buy date</b>. Leave Price on <i>market</i> to use the current quote as
your cost basis. Positions mark to live quotes with running P&amp;L; select one
and <b>Mark sold</b> to close it as of today. The analytics tabs read the book:
<b>Allocation</b> shows a sector pie of open positions, <b>Performance</b> charts
the portfolio value <i>from your earliest buy date</i> (counting each position
only while held) rebased to 100 against SPY, and <b>Risk</b> reports weighted
beta, largest-position concentration, and max drawdown. Those tabs start pulling
data only when first opened.</p>

<h2>Options &amp; Greeks</h2>
<p>The Options Chain panel shows calls and puts around the spot, with the ATM
strike highlighted. Greek columns — <b>Δ</b> delta, <b>Γ</b> gamma, <b>Θ</b>
theta (per day) and <b>Vega</b> (per 1% vol) — are computed with Black–Scholes
and hidden by default; right-click a table header and use <b>Columns</b> to show
them (the choice is saved with the layout).</p>

<h2>Tables: sort, filter &amp; columns</h2>
<p>Flat tables (Watchlist, Movers, Holders, Earnings, Analyst) <b>sort</b> when
you click a column header — click again to reverse. Numbers sort as numbers, so
<b>1.2M</b> ranks above <b>900K</b>. Grouped monitors (Commodities, FX, World
Indices) and the Financials statement keep their natural order and aren't
sortable, but <i>every</i> table has a <b>filter box</b> above it: start typing
to show only the rows that match. Right-click any table to
<b>Export Table to CSV…</b> or to <b>show/hide columns</b> (your choice is
remembered with the layout). The Financials panel additionally offers styled
Excel export and clipboard copy from its toolbar.</p>

<h2>Layouts</h2>
<p>Use <b>Settings &#9656; Layout</b> to save the current arrangement under a name
(<b>Ctrl+S</b>), reload a saved one, or reset to the default. Layouts can be
exported to a shareable <code>.aurantiumlayout</code> file and imported on another
machine. Your last arrangement is auto-saved and restored on the next launch.</p>

<h2>Data sources</h2>
<p>Aurantium runs out of the box on free, keyless sources (Yahoo Finance, Google
News — delayed). Connect optional free API keys from <b>Settings &#9656; API
Keys…</b> (Finnhub, Twelve Data, FRED, EIA, NewsAPI) for richer, faster data. Press
<b>F5</b> anytime to force-refresh every live feed. Recently-seen data is cached
locally, so panels fill in instantly on the next launch — even offline — before
a fresh refresh runs.</p>
</body>
"""


class OnboardingDialog(QDialog):
    """Tabbed shortcuts + guide modal with a 'don't auto-show' preference."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aurantium — Keyboard Shortcuts & Guide")
        self.resize(660, 580)

        layout = QVBoxLayout(self)
        tabs = QTabWidget(self)
        tabs.addTab(self._browser(_SHORTCUTS_HTML), "Shortcuts")
        tabs.addTab(self._browser(_GUIDE_HTML), "Dashboard Guide")
        layout.addWidget(tabs, 1)

        self._dont_show = QCheckBox(
            "Don't show this automatically on startup", self
        )
        # pre-checked: first-run shows it once, then it stays out of the way
        # unless the user opens it via F1 / the Help menu.
        self._dont_show.setChecked(True)
        layout.addWidget(self._dont_show)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)  # Close is the only button
        layout.addWidget(buttons)

    def _browser(self, html: str) -> QTextBrowser:
        browser = QTextBrowser(self)
        browser.setOpenExternalLinks(True)
        browser.setHtml(html)
        return browser

    def done(self, result: int) -> None:  # noqa: N802 (Qt override)
        """Persist the auto-show preference on any close (OK / Close / Esc)."""
        QSettings().setValue(_SETTINGS_KEY, self._dont_show.isChecked())
        super().done(result)

    @staticmethod
    def should_auto_show() -> bool:
        """True on first launch (flag unset) — i.e. show the modal once."""
        return not QSettings().value(_SETTINGS_KEY, False, type=bool)
