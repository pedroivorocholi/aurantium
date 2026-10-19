"""The F1 guide's content model: per-panel quick guides and the static pages.

Every panel declares its own quick guide next to its code, as a ``GUIDE``
class attribute holding a :class:`PanelGuide`::

    @register_panel(id="my_panel", title="My Panel", category="Custom")
    class MyPanel(Panel):
        GUIDE = PanelGuide(
            purpose="One line: what this panel is for.",
            links="What drives it, and what clicking in it drives.",
            interactions=("Click a row to …", "Right-click for …"),
            fields=(("Last", "Latest traded price"),),
            shortcuts=(("Delete", "Remove the selected row"),),
        )

That declaration is the single source of truth. The F1 window builds each
panel's page from it, the panel's ``?`` button and context menu open that
page, and ``tests/test_panel_guides.py`` fails for any registered panel that
has no guide — an unlisted panel is effectively a hidden one.

The static pages (Getting Started, Shortcuts, Linking & Workspaces, Settings)
live here too. ``SHORTCUT_GROUPS`` is the one shortcut table; the test checks
it against the key sequences the app actually binds, in both directions.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Callable, Optional


# --------------------------------------------------------------------------
# Declarations
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class GuideSection:
    """A sub-view of a panel (a tab, a mode, a secondary table), explained in
    a sentence or three. Shown collapsed on the panel's page."""

    title: str
    text: str


@dataclass(frozen=True)
class PanelGuide:
    """A panel's quick guide. Plain text throughout; the renderer escapes it,
    except that ``**bold**`` is honoured so a control's name can stand out."""

    #: one line: what the panel is for
    purpose: str
    #: what drives this panel and what clicking in it drives
    links: str = ""
    #: the gestures that matter, one per entry
    interactions: tuple[str, ...] = ()
    #: (column or field, plain-language meaning)
    fields: tuple[tuple[str, str], ...] = ()
    #: (keys, what they do) — panel-specific only; global keys live on Shortcuts
    shortcuts: tuple[tuple[str, str], ...] = ()
    #: tabs and sub-views, each explained briefly
    sections: tuple[GuideSection, ...] = ()

    def is_empty(self) -> bool:
        return not (self.purpose or "").strip()

    def search_text(self) -> str:
        parts = [self.purpose, self.links, *self.interactions]
        parts += [f"{k} {v}" for k, v in self.fields]
        parts += [f"{k} {v}" for k, v in self.shortcuts]
        parts += [f"{s.title} {s.text}" for s in self.sections]
        return " ".join(parts)


#: The list editor behind every configurable panel's Edit… button, described
#: once and shared by those panels' guides. ``[[grip]]`` / ``[[close]]`` are
#: drawn as the editor's own painted icons.
LIST_EDITOR = GuideSection(
    "Editing the list",
    "**Edit…** opens the list editor: drag a row by its [[grip]] grip to "
    "reorder, [[close]] removes it, and **+ Add…** searches by name (“10 year”, "
    "“cattle”, “euro”) or takes any ticker, verified live. Preset chips add "
    "common sets in one click. Right-clicking a row offers the same edits "
    "without the dialog, and Ctrl+Z undoes any of them.",
)


# --------------------------------------------------------------------------
# Shortcuts — the one table
# --------------------------------------------------------------------------
#
# Keys are written exactly as ``QKeySequence.toString()`` spells them, joined
# with " / " for alternates, so the test can match them against the live
# bindings. Rows whose keys are not a bindable sequence (mouse gestures, keys
# handled inside one widget) are marked ``bound=False`` and are checked by
# hand against the code instead.

@dataclass(frozen=True)
class Shortcut:
    keys: str
    text: str
    #: True when the app binds this as a QAction/QShortcut sequence
    bound: bool = True


SHORTCUT_GROUPS: tuple[tuple[str, tuple[Shortcut, ...]], ...] = (
    ("Global", (
        Shortcut("Ctrl+F", "Jump to the SYMBOL box with its text selected"),
        Shortcut("F5 / Ctrl+R", "Refresh every live feed"),
        Shortcut("Ctrl+Z", "Undo the last edit (drawing, list or position change)"),
        Shortcut("Ctrl+S", "Save the current layout under a name"),
        Shortcut("Shift+F11 / Ctrl+Shift+F", "Toggle borderless full screen for the window you are in"),
        Shortcut("F1 / Ctrl+Shift+H / Help", "Open this guide (Help is the key some keyboards carry)"),
        Shortcut("Shift+F1 / Ctrl+Shift+G", "Open the guide on the focused panel's page"),
        Shortcut("Ctrl+Q", "Quit aurantium"),
    )),
    ("Panels", (
        Shortcut("F11 / Ctrl+Shift+M", "Maximize the focused panel; press again to restore"),
        Shortcut("Esc", "Restore a maximized panel"),
        Shortcut("Ctrl+W", "Close the focused panel"),
        Shortcut("Ctrl+Shift+T", "Reopen the last panel you closed"),
        Shortcut("Ctrl+1 / Ctrl+2 / Ctrl+3 / Ctrl+4", "Put the focused panel in link group A, B, C or D"),
        Shortcut("Middle-click a tab", "Close that panel", bound=False),
    )),
    ("SYMBOL box", (
        Shortcut("Enter", "Send the ticker, or run a /command", bound=False),
        Shortcut("Up / Down", "Recall earlier entries (kept across sessions)", bound=False),
        Shortcut("Tab / Shift+Tab", "Cycle through the suggestions", bound=False),
        Shortcut("Esc", "Dismiss the suggestions", bound=False),
    )),
    ("Charts", (
        Shortcut("D", "Explain the day under the crosshair in the Day Brief"),
        Shortcut("Wheel", "Zoom time around the cursor", bound=False),
        Shortcut("Drag", "Scroll through time", bound=False),
        Shortcut("Double-click", "Reset the view to the selected range", bound=False),
    )),
    ("Tables", (
        Shortcut("Up / Down", "Move the row selection", bound=False),
        Shortcut("Delete / Backspace", "Watchlist: remove the selected symbol", bound=False),
    )),
)

#: F-row note shown under the table
FKEY_NOTE = (
    "On many laptops the F-row controls volume and brightness unless you hold "
    "Fn. Every F-key above has a Ctrl alternate, or press Fn+Esc on most "
    "Lenovos to lock the F-keys on."
)


def bound_shortcuts() -> set[str]:
    """Every key sequence the Shortcuts page claims the app binds."""
    out: set[str] = set()
    for _group, rows in SHORTCUT_GROUPS:
        for row in rows:
            if row.bound:
                out.update(k.strip() for k in row.keys.split(" / "))
    return out


# --------------------------------------------------------------------------
# Static pages
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Detail:
    """A collapsed block on a page: a title the user clicks to expand."""

    title: str
    body: str  # trusted HTML


@dataclass
class Page:
    id: str
    title: str
    #: nav grouping: "" for the top block, else the panel category
    group: str
    lead: str  # trusted HTML, always shown
    details: list[Detail] = field(default_factory=list)
    #: hosted child pages (the Company panel's tabs)
    parent: str = ""
    search: str = ""


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _ul(items) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


GETTING_STARTED = Page(
    id="getting-started",
    title="Getting Started",
    group="",
    lead=(
        _p("aurantium is a terminal of <b>linked panels</b>. Type a ticker or a "
           "name (<i>AAPL</i>, <i>nvidia</i>, <i>gold</i>) in the <b>SYMBOL</b> box "
           "and press Enter: every panel in the same link group re-centers on it. "
           "Clicking a symbol in any panel does the same.")
        + _p("Add panels from the <b>Panels</b> menu. A new panel opens as its own "
             "window; drag its tab into the workspace and it snaps into place. "
             "Drag a panel off the window, onto a second monitor say, and it "
             "becomes a window of its own.")
        + _p("<b>F11</b> maximizes the focused panel and <b>Esc</b> brings the rest "
             "back. The SYMBOL box is also a command line: <code>/add</code>, "
             "<code>/layout</code>, <code>/save</code>, <code>/refresh</code>, "
             "<code>/day</code>.")
        + _p("Every panel has a <b>?</b> in its header that opens its own page here. "
             "Ready-made desks are under <b>Settings ▸ Workspaces</b>.")
    ),
)

LINKING = Page(
    id="linking",
    title="Linking & Workspaces",
    group="",
    lead=(
        _p("Each panel carries a colored <b>link badge</b> at the right of its "
           "header. Panels in the same group (A–D) share one symbol: click a name "
           "in one and the others follow. The header prints the symbol the panel "
           "is on and <b>flashes the group color</b> when a symbol arrives from "
           "another panel. Choose <b>Unlinked</b> on the badge to let a panel "
           "navigate on its own; <b>Ctrl+1</b>–<b>Ctrl+4</b> regroup the focused "
           "panel.")
    ),
    details=[
        Detail("Arranging panels", _p(
            "Drag a panel by its tab to dock it beside, above or below another; "
            "drop it on a tab strip to stack them as tabs. The pin button on a "
            "title bar tucks a panel into a slide-out tab on the window edge. "
            "<b>F11</b> maximizes the focused panel; <b>Ctrl+W</b> closes it and "
            "<b>Ctrl+Shift+T</b> brings it back. The focused panel's title bar "
            "carries an amber underline.")),
        Detail("Multiple monitors", _p(
            "Drag a panel by its tab, or a whole stack by the empty strip beside "
            "its tabs, past the edge of the window and let go: it becomes a "
            "window of its own that you can move to another monitor. Drop more "
            "panels into it and they dock and snap just as in the main window. "
            "A drag released inside a window but not on a drop target goes back "
            "where it came from. Panels in every window share the link groups, "
            "<b>Shift+F11</b> puts the window you are in into full screen, and "
            "<b>F11</b> maximizes a panel within its own window. Each window's "
            "position, monitor and full-screen state are saved with the layout; "
            "a window whose monitor is gone reopens on this one.")),
        Detail("Panel sizes", _p(
            "Panels reorganize for the size you give them. Squeeze one narrow and "
            "its table drops its least important columns first — the symbol and "
            "its price always survive — while button rows fold into a "
            "<b>⋯</b> menu. Columns you hide yourself (right-click a header ▸ "
            "Columns) stay hidden at every size.")),
        Detail("Workspaces", _p(
            "Curated starter desks — Macro Desk, Commodities Desk and Equity "
            "Research — are under <b>Settings ▸ Workspaces</b>, and a fresh "
            "install offers them at startup. Loading one replaces the current "
            "arrangement, so save first if you want to keep it.")),
        Detail("Layouts", _p(
            "<b>Settings ▸ Layout</b> saves the arrangement under a name "
            "(<b>Ctrl+S</b>), loads, exports to a shareable "
            "<code>.aurantiumlayout</code> file, imports, deletes, or resets to the "
            "default. The last arrangement is saved automatically and restored on "
            "the next launch.")),
        Detail("Commands", _ul([
            "<code>/add &lt;panel id&gt;</code> — open a panel",
            "<code>/layout &lt;name&gt;</code> — load a saved layout",
            "<code>/save [name]</code> — save the current layout",
            "<code>/refresh</code> — reload every feed",
            "<code>/day 2026-03-14</code> or <code>/day START..END</code> — point "
            "the Day Brief at a date or span",
            "<code>AAPL 2026-03-14</code> — a symbol and a date in one entry",
        ])),
        Detail("Symbol search", _p(
            "As you type, the SYMBOL box suggests instruments by <b>name</b> as "
            "well as ticker — “10 year treasury”, “cattle” and “euro” all work — "
            "from a curated catalog plus a live lookup. The same suggestions "
            "appear in the symbol boxes of the Watchlist, Price Alerts, Portfolio "
            "and Performance panels.")),
    ],
)

SETTINGS = Page(
    id="settings",
    title="Settings",
    group="",
    lead=_p("Everything except <b>Panels</b> lives under the <b>Settings</b> menu."),
    details=[
        Detail("Theme", _p(
            "Dark (default) or light. aurantium restarts to apply it and restores "
            "your workspace, so charts and chrome always match.")),
        Detail("Color-blind mode", _p(
            "Swaps the green/red up-down colors for a deuteranopia-safe "
            "blue/orange pair and prefixes change values with <b>▲</b>/<b>▼</b>, "
            "so direction never depends on color alone. Restarts to apply.")),
        Detail("Close to tray", _p(
            "When on, the window's ✕ hides aurantium to the system tray instead of "
            "quitting, so price alerts keep firing. The tray icon's menu has "
            "Show, Hide, the same toggle, and Quit.")),
        Detail("Full Screen", _p(
            "Borderless full screen that covers the taskbar (<b>Shift+F11</b> / "
            "<b>Ctrl+Shift+F</b>). aurantium opens this way; turning it off "
            "leaves a maximized window.")),
        Detail("Workspaces and Layout", _p(
            "Starter desks, and your own saved arrangements — see "
            "<a href=\"guide:linking\">Linking &amp; Workspaces</a>.")),
        Detail("News Languages…", _p(
            "Pick a main language plus any others you read. Headlines appear "
            "only in those languages, main language first, and each language "
            "adds its own market feeds. Asked once on first launch.")),
        Detail("API Keys…", _p(
            "aurantium runs on free keyless sources (Yahoo Finance, Google News — "
            "delayed). Optional free keys from Finnhub, Twelve Data, FRED, EIA "
            "and NewsAPI unlock faster quotes and more data; each row links to "
            "its sign-up page. Keys stay in a local file on this machine.")),
        Detail("Help, updates and quit", _p(
            "<b>Keyboard Shortcuts &amp; Guide…</b> (<b>F1</b>) opens this window, "
            "and <b>Guide to the Focused Panel…</b> (<b>Shift+F1</b>) opens it on "
            "the page of the panel you are working in. <b>Check for "
            "Updates…</b> asks for a new version now (aurantium also checks once a "
            "day). <b>About</b> shows the version. <b>Quit</b> (<b>Ctrl+Q</b>) "
            "exits even when Close to tray is on.")),
        Detail("Data and caching", _p(
            "Recently seen data is cached locally, so panels fill in instantly on "
            "the next launch — even offline — before a fresh refresh runs. "
            "<b>F5</b> forces every feed to refresh now. A header that says "
            "<i>stale</i> is showing cached data.")),
        Detail("Keyboard focus", _p(
            "Moving through the interface with <b>Tab</b> / <b>Shift+Tab</b> draws "
            "a bright outline on the control that will take the next keystroke. "
            "A mouse click never leaves one behind.")),
    ],
)


def shortcuts_page() -> Page:
    blocks = []
    for group, rows in SHORTCUT_GROUPS:
        body = "".join(_key_row(r.keys, r.text) for r in rows)
        blocks.append(f"<h3>{html.escape(group)}</h3><table class='keys'>{body}</table>")
    lead = "".join(blocks) + f"<p class='note'>{html.escape(FKEY_NOTE)}</p>"
    search = " ".join(f"{r.keys} {r.text}" for _g, rows in SHORTCUT_GROUPS for r in rows)
    return Page(id="shortcuts", title="Shortcuts", group="", lead=lead, search=search)


def _kbd(keys: str) -> str:
    """``"F5 / Ctrl+R"`` → kbd chips. A gesture ("Middle-click a tab") stays
    plain text: a key chip around a sentence reads as a key named that."""
    alts = []
    for alt in keys.split(" / "):
        alt = alt.strip()
        if " " in alt or alt in ("Wheel", "Drag", "Double-click"):
            alts.append(f"<span class='gesture'>{html.escape(alt)}</span>")
            continue
        alts.append("<kbd>" + "</kbd>+<kbd>".join(
            html.escape(k) for k in _split_keys(alt)) + "</kbd>")
    return " <span class='or'>/</span> ".join(alts)


def _split_keys(seq: str) -> list[str]:
    # "Ctrl++" style sequences don't occur here; a plain split is exact
    return [k for k in seq.split("+") if k]


#: width of the key column, so every group's descriptions start on one line
KEY_COL_PX = 230


#: a panel page's own (short) key table needs less room
PANEL_KEY_COL_PX = 150


def _key_row(keys: str, text: str, width: int = KEY_COL_PX) -> str:
    return (f"<tr><td class='k' width='{width}'>{_kbd(keys)}</td>"
            f"<td class='d'>{html.escape(text)}</td></tr>")


# --------------------------------------------------------------------------
# Panel pages
# --------------------------------------------------------------------------

def _rich(text: str) -> str:
    """Escape, then honour ``**bold**``."""
    out = html.escape(text)
    parts = out.split("**")
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))


def panel_page(page_id: str, title: str, group: str, guide: PanelGuide,
               parent: str = "") -> Page:
    lead = f"<p class='purpose'>{_rich(guide.purpose)}</p>"
    if guide.links:
        lead += f"<p><span class='label'>LINKING</span> {_rich(guide.links)}</p>"
    if guide.interactions:
        lead += _ul(_rich(i) for i in guide.interactions)
    if guide.shortcuts:
        lead += "<h3>KEYS</h3><table class='keys'>" + "".join(
            _key_row(k, v, PANEL_KEY_COL_PX) for k, v in guide.shortcuts) + "</table>"
    details: list[Detail] = []
    for s in guide.sections:
        details.append(Detail(s.title, _p(_rich(s.text))))
    if guide.fields:
        rows = "".join(
            f"<tr><td class='k'><b>{_rich(k)}</b></td><td class='d'>{_rich(v)}</td></tr>"
            for k, v in guide.fields)
        details.append(Detail("Columns & fields", f"<table class='fields'>{rows}</table>"))
    return Page(id=page_id, title=title, group=group, lead=lead, details=details,
                parent=parent, search=f"{title} {guide.search_text()}")


def build_pages(panel_metas=None) -> list[Page]:
    """Every F1 page, in nav order: the static block, then one page per
    registered panel grouped by category, then hosted tab pages under their
    host. ``panel_metas`` defaults to the live registry."""
    from .panel import PanelRegistry

    pages = [GETTING_STARTED, shortcuts_page(), LINKING, SETTINGS]
    metas = PanelRegistry.all() if panel_metas is None else panel_metas
    for meta in metas:
        guide = getattr(meta.cls, "GUIDE", None)
        if not isinstance(guide, PanelGuide):
            continue
        pages.append(panel_page(meta.id, meta.title, meta.category, guide))
        for key, label, child_cls in getattr(meta.cls, "HOSTED_TABS", ()):
            child_guide = getattr(child_cls, "GUIDE", None)
            if isinstance(child_guide, PanelGuide):
                pages.append(panel_page(f"{meta.id}:{key}", label, meta.category,
                                        child_guide, parent=meta.id))
    return pages


STATIC_IDS = ("getting-started", "shortcuts", "linking", "settings")


def resolve_section(section: Optional[str]) -> Optional[str]:
    """Map a panel id — including a retired alias like ``key_metrics`` — to its
    page id (``company:key_metrics``). Static ids and unknown ids pass through."""
    if not section or section in STATIC_IDS or ":" in section:
        return section
    from .panel import PanelRegistry

    new_id, extra = PanelRegistry.resolve(section)
    tab = extra.get("tab") if isinstance(extra, dict) else None
    return f"{new_id}:{tab}" if tab else new_id


#: callable the panel header uses to open the guide; installed by the main
#: window so a panel never needs to know who hosts it
open_guide_hook: Optional[Callable[[str], None]] = None
