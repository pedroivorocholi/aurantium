"""The F1 guide: a navigable help window.

A section list on the left — Getting Started, Shortcuts, Linking & Workspaces,
Settings, then one entry per panel grouped by category (the Company panel's
tabs nested under it) — and a content pane on the right. Each page is short;
its detail sits in collapsed ``▸`` blocks that expand on click. The search box
filters the list and opens every block that mentions the query.

It used to be two ``QTextBrowser`` tabs of long HTML: a cheat sheet and a guide
that read top to bottom, which users found overwhelming and which drifted out of
step with the panels it described. Panel pages are now generated from each
panel's own ``GUIDE`` declaration (see ``guide.py``), so content lives in one
place and a panel without a guide fails ``tests/test_panel_guides.py``.

Shown automatically on first launch (gated by a ``QSettings`` flag), on F1 /
Settings ▸ Keyboard Shortcuts & Guide…, and from every panel's ``?`` button,
which passes its panel id so the window opens on that panel's page.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import guide, motion
from .theme import (
    ACCENT,
    LABEL,
    BG_ELEV,
    BORDER,
    CHROME_TEXT,
    FG,
    FG_DIM,
    FG_MUTED,
    FONT_LG,
    FONT_MD,
    FONT_SM,
    FONT_TITLE,
    MONO_FONT,
    SPACE_LG,
    SPACE_SM,
    SPACE_XL,
    WEIGHT_BOLD,
)

#: bump the suffix if the content changes enough to warrant re-showing everyone
_SETTINGS_KEY = "has_seen_onboarding_v1"

#: URL scheme the guide's inline affordance icons are registered under. Any
#: scheme QTextBrowser will not try to fetch from the network does; naming it
#: after the app keeps it obvious in the HTML that it resolves locally.
_GLYPH_SCHEME = "aurantium-glyph"

#: painted icons a guide may name inline as ``[[grip]]`` / ``[[close]]``
_GLYPHS = ("grip", "close")

#: nav item roles
_ROLE_ID = Qt.ItemDataRole.UserRole

#: width of the section list — fits the longest panel title at FONT_MD
_NAV_WIDTH = 212


def _glyph(kind: str) -> str:
    """One painted affordance icon, inline in the guide's prose.

    Sized to the surrounding text rather than to the family's 16px grid: here
    the icon is a word in a sentence, so it takes the sentence's optical size
    the way a character would have.
    """
    return (
        f'<img src="{_GLYPH_SCHEME}:{kind}" width="{FONT_LG}" height="{FONT_LG}"'
        ' style="vertical-align: middle;">'
    )


def _page_css() -> str:
    return f"""
    <style>
      body {{ color: {FG}; font-size: {FONT_LG}px; }}
      h2 {{ color: {ACCENT}; font-size: {FONT_TITLE}px; font-weight: {WEIGHT_BOLD};
            margin: 0 0 {SPACE_LG}px 0; }}
      h3 {{ color: {FG_MUTED}; font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD};
            letter-spacing: 1.5px; margin: {SPACE_XL}px 0 2px 0; }}
      p  {{ color: {FG}; margin: 0 0 {SPACE_LG}px 0; }}
      p.purpose {{ color: {CHROME_TEXT}; }}
      p.note {{ color: {FG_DIM}; font-size: {FONT_MD}px; margin-top: {SPACE_XL}px; }}
      p.crumb {{ color: {FG_MUTED}; font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD};
                 letter-spacing: 1.5px; margin: 0 0 2px 0; }}
      span.label {{ color: {FG_MUTED}; font-size: {FONT_SM}px; font-weight: {WEIGHT_BOLD};
                    letter-spacing: 1.5px; }}
      ul {{ margin: 0 0 {SPACE_LG}px 0; }}
      li {{ margin: 0 0 2px 0; }}
      a {{ color: {LABEL}; text-decoration: none; }}
      p.more {{ margin: {SPACE_LG}px 0 2px 0; font-weight: {WEIGHT_BOLD}; }}
      div.detail {{ margin: 0 0 0 14px; }}
      table {{ border-collapse: collapse; margin: 0 0 {SPACE_LG}px 0; }}
      td {{ padding: 3px {SPACE_XL}px 3px 0; vertical-align: middle; }}
      table.keys td.k {{ white-space: nowrap; }}
      table.fields td {{ vertical-align: top; }}
      td.d {{ color: {FG}; }}
      table.fields td.k {{ white-space: nowrap; color: {CHROME_TEXT}; }}
      kbd {{ background: {BG_ELEV}; color: {CHROME_TEXT}; font-family: "{MONO_FONT}";
             font-size: {FONT_MD}px; }}
      span.gesture {{ color: {CHROME_TEXT}; }}
      span.or {{ color: {FG_MUTED}; }}
      code {{ font-family: "{MONO_FONT}"; color: {CHROME_TEXT}; font-size: {FONT_MD}px;
              white-space: nowrap; }}
    </style>
    """


class OnboardingDialog(QDialog):
    """The F1 guide. ``section`` opens a page directly: a static page id
    (``"shortcuts"``), a panel id (``"watchlist"``), or a retired alias that
    resolves to its host's tab (``"key_metrics"`` → Company ▸ Key Metrics)."""

    def __init__(self, parent: QWidget | None = None, section: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aurantium — Keyboard Shortcuts & Guide")
        self.resize(900, 620)

        self._pages = {p.id: p for p in guide.build_pages()}
        self._order = list(self._pages)
        self._expanded: set[tuple[str, int]] = set()
        self._current = ""
        self._rendered = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, SPACE_XL, SPACE_XL, SPACE_XL)
        layout.setSpacing(SPACE_LG)

        self._search = QLineEdit(self)
        self._search.setObjectName("guideSearch")
        self._search.setPlaceholderText("Search the guide — a panel, a key, a term…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        body = QHBoxLayout()
        body.setSpacing(0)
        self._nav = QTreeWidget(self)
        self._nav.setObjectName("guideNav")
        self._nav.setHeaderHidden(True)
        self._nav.setRootIsDecorated(False)
        self._nav.setIndentation(14)
        self._nav.setFixedWidth(_NAV_WIDTH)
        self._nav.setUniformRowHeights(True)
        self._nav.currentItemChanged.connect(self._on_nav)
        body.addWidget(self._nav)

        self._browser = QTextBrowser(self)
        self._browser.setObjectName("guidePage")
        self._browser.setOpenLinks(False)
        self._browser.anchorClicked.connect(self._on_link)
        self._register_glyphs(self._browser)
        # bullets just inside the text column, not Qt's default 40px out
        self._browser.document().setIndentWidth(SPACE_XL + SPACE_SM)
        body.addWidget(self._browser, 1)
        layout.addLayout(body, 1)

        foot = QHBoxLayout()
        foot.setSpacing(SPACE_LG)
        self._dont_show = QCheckBox("Don't show this automatically on startup", self)
        # pre-checked: first-run shows it once, then it stays out of the way
        # unless the user opens it via F1, a panel's ?, or the Settings menu.
        self._dont_show.setChecked(True)
        foot.addWidget(self._dont_show)
        foot.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)  # Close is the only button
        foot.addWidget(buttons)
        layout.addLayout(foot)

        self._items: dict[str, QTreeWidgetItem] = {}
        self._headers: list[tuple[QTreeWidgetItem, list[str]]] = []
        self._build_nav()
        self.show_section(section or "getting-started")

    # -- public ----------------------------------------------------------------

    def section_ids(self) -> list[str]:
        """Every page id, in nav order."""
        return list(self._order)

    def current_section(self) -> str:
        return self._current

    def show_section(self, section: str) -> bool:
        """Open a page by id (aliases resolved). False if there is no such page."""
        page_id = guide.resolve_section(section)
        item = self._items.get(page_id or "")
        if item is None:
            return False
        if item.isHidden():
            self._search.clear()
        self._nav.setCurrentItem(item)
        self._render(page_id)
        return True

    # -- nav -------------------------------------------------------------------

    def _build_nav(self) -> None:
        nav = self._nav
        group_items: dict[str, list[str]] = {}
        header: QTreeWidgetItem | None = None
        last_group = None
        for page in self._pages.values():
            if page.parent:
                parent = self._items[page.parent]
                item = QTreeWidgetItem(parent, [page.title])
                parent.setExpanded(True)
            else:
                if page.group and page.group != last_group:
                    header = self._header_item(page.group.upper())
                    group_items[page.group] = []
                    self._headers.append((header, group_items[page.group]))
                    last_group = page.group
                item = QTreeWidgetItem(nav, [page.title])
            item.setData(0, _ROLE_ID, page.id)
            self._items[page.id] = item
            if page.group:
                group_items.setdefault(page.group, []).append(page.id)
        nav.expandAll()

    def _header_item(self, text: str) -> QTreeWidgetItem:
        item = QTreeWidgetItem(self._nav, [text])
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont(self._nav.font())
        font.setPixelSize(FONT_SM)
        font.setWeight(QFont.Weight(WEIGHT_BOLD))
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.5)
        item.setFont(0, font)
        item.setForeground(0, QColor(FG_MUTED))
        return item

    def _on_nav(self, item: QTreeWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        page_id = item.data(0, _ROLE_ID)
        if page_id and page_id != self._current:
            self._render(page_id)

    # -- search ------------------------------------------------------------------

    def _query(self) -> list[str]:
        return [w for w in self._search.text().lower().split() if w]

    def _matches(self, page: guide.Page, words: list[str]) -> bool:
        haystack = f"{page.title} {page.search} {page.lead} " + " ".join(
            f"{d.title} {d.body}" for d in page.details)
        haystack = html.unescape(haystack).lower()
        return all(w in haystack for w in words)

    def _on_search(self, _text: str) -> None:
        words = self._query()
        visible: list[str] = []
        for page_id, item in self._items.items():
            page = self._pages[page_id]
            hit = not words or self._matches(page, words)
            item.setHidden(not hit)
            if hit:
                visible.append(page_id)
        # a hosted tab that matches keeps its host visible
        for page_id in list(visible):
            parent = self._pages[page_id].parent
            if parent and self._items[parent].isHidden():
                self._items[parent].setHidden(False)
        for header, ids in self._headers:
            header.setHidden(all(self._items[i].isHidden() for i in ids))
        if self._current in visible:
            self._render(self._current)
        elif visible:
            self._nav.setCurrentItem(self._items[visible[0]])

    # -- page ------------------------------------------------------------------

    def _render(self, page_id: str) -> None:
        self._current = page_id
        page = self._pages[page_id]
        words = self._query()
        parts = [_page_css(), "<body>"]
        if page.parent:
            parts.append(f"<p class='crumb'>{html.escape(self._pages[page.parent].title.upper())}</p>")
        elif page.group:
            parts.append(f"<p class='crumb'>{html.escape(page.group.upper())}</p>")
        parts.append(f"<h2>{html.escape(page.title)}</h2>")
        parts.append(self._with_glyphs(page.lead))
        for i, detail in enumerate(page.details):
            hay = html.unescape(f"{detail.title} {detail.body}").lower()
            open_ = (page_id, i) in self._expanded or (
                words and all(w in hay for w in words))
            mark = "▾" if open_ else "▸"
            parts.append(
                f"<p class='more'><a href='more:{i}'>{mark}&nbsp;{html.escape(detail.title)}</a></p>")
            if open_:
                parts.append(f"<div class='detail'>{self._with_glyphs(detail.body)}</div>")
        parts.append("</body>")
        # expanding a block re-renders the same page: keep the reader's place
        bar = self._browser.verticalScrollBar()
        keep = bar.value() if page_id == self._rendered else 0
        self._browser.setHtml("".join(parts))
        self._rendered = page_id
        bar.setValue(keep)

    @staticmethod
    def _with_glyphs(body: str) -> str:
        for kind in _GLYPHS:
            body = body.replace(f"[[{kind}]]", _glyph(kind))
        return body

    def _on_link(self, url: QUrl) -> None:
        target = url.toString()
        if target.startswith("more:"):
            key = (self._current, int(target.split(":", 1)[1]))
            self._expanded ^= {key}
            self._render(self._current)
        elif target.startswith("guide:"):
            self.show_section(target.split(":", 1)[1])
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def _register_glyphs(self, browser: QTextBrowser) -> None:
        """Make the painted affordance icons available to the page as images.

        Guides describe two controls by drawing them inline — the list
        editor's row grip and row-delete mark. Those used to be the characters
        ``⠿`` and ``✕``, which stopped being right when the dialog they describe
        started painting them: the guide would have shown a braille-fallback
        glyph beside a sentence about a mark that no longer looks like that.

        A QTextDocument resolves ``<img src="…">`` through its own resource
        table, so the guide embeds the real icon rather than an approximation
        of it, and it stays correct automatically the next time the family is
        redrawn.
        """
        from . import icons

        dpr = icons.device_pixel_ratio(self)
        document = browser.document()
        for kind in _GLYPHS:
            document.addResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl(f"{_GLYPH_SCHEME}:{kind}"),
                icons.pixmap(kind, FG, dpr),
            )

    # -- lifecycle ---------------------------------------------------------------

    def done(self, result: int) -> None:  # noqa: N802 (Qt override)
        """Persist the auto-show preference on any close (Close / Esc / ✕)."""
        QSettings().setValue(_SETTINGS_KEY, self._dont_show.isChecked())
        super().done(result)

    @staticmethod
    def should_auto_show() -> bool:
        """True on first launch (flag unset) — i.e. show the modal once."""
        return not QSettings().value(_SETTINGS_KEY, False, type=bool)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # First-run tier: a beat of arrival is welcome here in a way it would
        # not be on a control the user hits all day.
        super().showEvent(event)
        motion.fade_in_dialog(self)
