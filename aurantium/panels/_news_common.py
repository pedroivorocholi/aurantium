"""Shared news rendering: robust timestamp parsing + a clean Time/Headline
table (Time/Why/Headline for Portfolio news) used by the News panel in every
source mode.

Underscore-prefixed so ``discover_panels`` skips it (it registers no panel).
"""

from __future__ import annotations

import html as _html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import requests
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
)

from ..components.empty_state import EmptyState
from ..components.fit_width import fit_columns
from ..languages import main_language
from ..panel import EMPTY_NO_SYMBOL, Panel
from ..theme import FG_DIM, LABEL


def parse_published(value: Any) -> Optional[datetime]:
    """Parse the many shapes a feed 'published' field arrives in: epoch number,
    ISO-8601, or RFC-2822 (what gnews/RSS emit, e.g. 'Wed, 05 Feb 2026 08:00:00
    GMT') — the last of which the old ISO-only parser silently dropped."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        try:
            return parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return None


def format_when(dt: Optional[datetime]) -> str:
    """HH:MM for today's items (in local time), 'Mon DD' for older ones this
    year, 'Mon DD YY' before that — a bare 'Mar 17' read as this March."""
    if dt is None:
        return "—"
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    if dt.date() == now.date():
        return dt.strftime("%H:%M")
    if dt.year == now.year:
        return dt.strftime("%b %d")
    return dt.strftime("%b %d %y")


def headline_col(table: QTableWidget) -> int:
    """The headline is always the last column: 1 in Time | Headline, 2 when
    Portfolio news adds its Why tag between them."""
    return table.columnCount() - 1


def _set_news_columns(table: QTableWidget, show_reason: bool) -> None:
    labels = ["Time", "Why", "Headline"] if show_reason else ["Time", "Headline"]
    if table.columnCount() == len(labels):
        return
    table.setColumnCount(len(labels))
    table.setHorizontalHeaderLabels(labels)
    hh = table.horizontalHeader()
    for c in range(len(labels) - 1):
        hh.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
    hh.setSectionResizeMode(len(labels) - 1, QHeaderView.ResizeMode.Stretch)


def make_news_table(parent) -> QTableWidget:
    table = QTableWidget(0, 2, parent)
    fit_columns(table)
    table.setHorizontalHeaderLabels(["Time", "Headline"])
    vh = table.verticalHeader()
    vh.setVisible(False)
    vh.setDefaultSectionSize(20)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setTextElideMode(Qt.TextElideMode.ElideRight)
    table.setAlternatingRowColors(True)
    # per-pixel scrolling, same as MarketTable — headlines get scanned by drag
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    hh = table.horizontalHeader()
    hh.setHighlightSections(False)
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    return table


def _ranked_with_dates(items: list) -> list[tuple[dict, Optional[datetime]]]:
    """Parse each item's ``published`` exactly once, then rank the items.

    Ranking rule: language tier, then score, then recency. Language tier 0 — the user's
    main language, or an item whose source declared none (yfinance); tier 1 —
    their other languages. Untagged items rank with the main language rather
    than below it, since sinking unlabelled headlines would demote the entire
    yfinance fallback.

    There is deliberately no "mentions a linked ticker" tier any more: it
    matched the ticker case-sensitively, so a two-day-old "AAPL ..." headline
    outranked a fresh "Apple ..." one — and in Topic mode it pinned whatever
    named any link group's symbol. Symbol news is relevance-filtered by the
    provider; the table only orders it.

    Within each tier, items carrying a numeric ``score`` (Portfolio news, whose
    score already folds in position size, layer and recency) come first,
    highest score first; the rest — every Linked and Topic item — newest
    first, with items whose timestamp can't be parsed sinking to the bottom of
    their tier. Returns ``(entry, parsed_dt)`` pairs so callers
    reuse the parsed datetime instead of parsing it again for display. Non-dict
    entries are dropped (they carry no renderable fields).
    """
    main_lang = main_language()

    pairs: list[tuple[dict, Optional[datetime]]] = [
        (entry, parse_published(entry.get("published")))
        for entry in items
        if isinstance(entry, dict)
    ]

    def key(pair: tuple[dict, Optional[datetime]]) -> tuple:
        entry, dt = pair
        lang = str(entry.get("lang") or "").split("-")[0].lower()
        lang_tier = 0 if not lang or lang == main_lang else 1
        ts = dt.timestamp() if dt is not None else float("-inf")
        score = entry.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            return (lang_tier, 0, -float(score), -ts)
        return (lang_tier, 1, 0.0, -ts)

    pairs.sort(key=key)
    return pairs


def sort_news_items(items: list) -> list:
    """Ranked news entries (see ``_ranked_with_dates`` for the rule). Kept as a
    thin compatibility wrapper; ``populate_news_table`` uses the paired form so
    it never re-parses timestamps."""
    return [entry for entry, _dt in _ranked_with_dates(items)]


def populate_news_table(table: QTableWidget, data: Any, show_reason: bool = False) -> int:
    """Fill ``table`` from a list of news dicts, ranked per
    ``_ranked_with_dates``. ``show_reason`` adds the Why column (each item's
    ``reason`` tag: a ticker, a sector weight or "Macro"). Returns the row
    count."""
    table.setRowCount(0)
    _set_news_columns(table, show_reason)
    head_col = headline_col(table)
    pairs = _ranked_with_dates(data) if isinstance(data, list) else []
    count = 0
    for entry, published_dt in pairs:
        # feeds hand over HTML-escaped titles ("Marsh &amp; McLennan") and
        # stray double spaces; show the text a reader would see on the page
        title = " ".join(_html.unescape(entry.get("title") or "").split()) or "(untitled)"
        publisher = entry.get("publisher") or ""
        when = format_when(published_dt)  # reuse the datetime parsed for ranking

        row = table.rowCount()
        table.insertRow(row)

        time_item = QTableWidgetItem(when)
        time_item.setForeground(QColor(LABEL))
        time_item.setFlags(time_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        head_item = QTableWidgetItem(title)
        head_item.setToolTip(f"{title}\n— {publisher}" if publisher else title)
        head_item.setData(Qt.ItemDataRole.UserRole, entry.get("url"))
        head_item.setData(Qt.ItemDataRole.UserRole + 1, entry.get("summary") or "")
        # holdings-layer Portfolio rows name their ticker; a click sends it on
        head_item.setData(Qt.ItemDataRole.UserRole + 2, entry.get("symbol") or "")
        head_item.setFlags(head_item.flags() & ~Qt.ItemFlag.ItemIsEditable)

        table.setItem(row, 0, time_item)
        if show_reason:
            why_item = QTableWidgetItem(str(entry.get("reason") or ""))
            why_item.setForeground(QColor(LABEL))
            why_item.setFlags(why_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 1, why_item)
        table.setItem(row, head_col, head_item)
        count += 1
    return count


def news_url_at(table: QTableWidget, row: int) -> Optional[str]:
    item = table.item(row, headline_col(table))
    if item is None:
        return None
    url = item.data(Qt.ItemDataRole.UserRole)
    return str(url) if url else None


def news_summary_at(table: QTableWidget, row: int) -> str:
    item = table.item(row, headline_col(table))
    if item is None:
        return ""
    return str(item.data(Qt.ItemDataRole.UserRole + 1) or "")


def news_symbol_at(table: QTableWidget, row: int) -> str:
    """The ticker a holdings-layer Portfolio row is about, else ``""``."""
    item = table.item(row, headline_col(table))
    if item is None:
        return ""
    return str(item.data(Qt.ItemDataRole.UserRole + 2) or "")


def filter_news_table(table: QTableWidget, text: str) -> None:
    """Hide rows whose headline — or Why tag, when shown — doesn't contain
    ``text`` (case-insensitive)."""
    needle = (text or "").strip().casefold()
    cols = range(1, table.columnCount())  # every column but Time
    for r in range(table.rowCount()):
        hay = " ".join(
            item.text().casefold()
            for item in (table.item(r, c) for c in cols)
            if item is not None
        )
        table.setRowHidden(r, bool(needle) and needle not in hay)


_OG_RE = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
    re.I,
)
_OG_RE_REV = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:description["\']',
    re.I,
)
_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.I
)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


def parse_article_snippet(body: str) -> Optional[str]:
    """Pull a preview from page HTML: OpenGraph/meta description, else the first
    paragraph. Returns None if nothing usable is found."""
    for rx in (_OG_RE, _OG_RE_REV, _DESC_RE):
        m = rx.search(body)
        if m:
            return _html.unescape(m.group(1)).strip()[:400] or None
    m = _P_RE.search(body)
    if m:
        text = _html.unescape(_TAG_RE.sub("", m.group(1))).strip()
        return text[:400] or None
    return None


def fetch_article_snippet(url: str) -> Optional[str]:
    """Best-effort 1–2 sentence preview for a news URL. Returns None on any
    network/parse failure. **Runs on a worker thread — never call from the GUI
    thread.**"""
    try:
        resp = requests.get(
            url, timeout=5, headers={"User-Agent": "Mozilla/5.0 (aurantium)"}
        )
        resp.raise_for_status()
        body = resp.text[:200_000]
    except Exception:
        return None
    return parse_article_snippet(body)


class NewsPanelBase(Panel):
    """Shared news-panel behaviour: a live headline filter, read/unread dimming,
    and an on-click article preview fetched off the GUI thread.

    Subclasses build their own controls, then call :meth:`_build_news_ui` to add
    the filter box + table + preview line, and :meth:`_render_news` whenever new
    data arrives. Read URLs persist via :meth:`_read_state` / :meth:`_restore_read`.
    """

    # emitted from the fetch worker thread; delivered on the GUI thread
    snippet_ready = Signal(str, str)  # (url, snippet)

    def _build_news_ui(self) -> None:
        self._read_urls: set[str] = set()
        self._snippet_cache: dict[str, Optional[str]] = {}
        self._current_url: Optional[str] = None

        self._news_filter = QLineEdit(self)
        self._news_filter.setPlaceholderText("Filter headlines…")
        self._news_filter.setClearButtonEnabled(True)
        self._news_filter.textChanged.connect(self._apply_news_filter)
        self.content_layout.addWidget(self._news_filter)

        self.table = make_news_table(self)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.content_layout.addWidget(self.table, 1)
        # This is a plain QTableWidget rather than a MarketTable, so it needs
        # the empty state attached by hand — without it a news panel with no
        # symbol yet was an unexplained blank rectangle.
        self._news_empty_title = EMPTY_NO_SYMBOL
        self._news_empty_hint = (
            "Click a ticker in any linked panel, or type one in the search box"
        )
        self.news_empty = EmptyState.attach(
            self.table, self._news_empty_title, self._news_empty_hint
        )
        # set_loading() reaches the overlay, so a fetch reads "Loading…"
        # rather than "No recent headlines" while it is still in flight
        self.register_state_target(self.news_empty)

        self.preview_lbl = QLabel("", self)
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet(f"color: {FG_DIM}; padding: 3px 2px;")
        self.preview_lbl.setMaximumHeight(64)
        self.preview_lbl.setVisible(False)
        self.content_layout.addWidget(self.preview_lbl)

        self.snippet_ready.connect(self._on_snippet)

        # Hide the preview snippet as soon as focus leaves this panel — clicking
        # any other panel dismisses it (Qt auto-drops this connection when the
        # panel is destroyed, since the slot is a bound method of this QObject).
        from PySide6.QtWidgets import QApplication

        QApplication.instance().focusChanged.connect(self._on_focus_changed)

    # -- rendering ---------------------------------------------------------

    def _render_news(self, data: Any, show_reason: bool = False) -> int:
        count = populate_news_table(self.table, data, show_reason)
        self._apply_read_styling()
        filter_news_table(self.table, self._news_filter.text())
        return count

    def set_news_empty_text(self, title: str, hint: str = "") -> None:
        """What the headline table says when it has nothing to show. Subclasses
        call this from ``on_symbol`` (or when their query changes) so the
        message names the actual situation rather than a generic 'no data'."""
        self._news_empty_title = title
        self._news_empty_hint = hint
        self._apply_news_filter(self._news_filter.text())

    def _apply_news_filter(self, text: str) -> None:
        filter_news_table(self.table, text)
        # Hiding rows isn't a model change, so the overlay has to be told —
        # and a filter that matches nothing is its own case, not "no news".
        needle = (text or "").strip()
        if needle:
            self.news_empty.set_text(
                f"No headlines match “{needle}”",
                "Clear the filter to see everything",
            )
        else:
            self.news_empty.set_text(
                self._news_empty_title, self._news_empty_hint
            )
        self.news_empty.sync()

    def _apply_read_styling(self) -> None:
        dim = QColor(FG_DIM)
        for r in range(self.table.rowCount()):
            url = news_url_at(self.table, r)
            if url and url in self._read_urls:
                for c in range(self.table.columnCount()):
                    item = self.table.item(r, c)
                    if item is not None:
                        item.setForeground(dim)

    def _mark_read(self, url: Optional[str]) -> None:
        if url:
            self._read_urls.add(url)

    # -- interaction -------------------------------------------------------

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        url = news_url_at(self.table, row)
        if not url:
            return
        self._mark_read(url)
        self._apply_read_styling()
        title_item = self.table.item(row, headline_col(self.table))
        title = title_item.text() if title_item is not None else ""
        self._show_preview(url, news_summary_at(self.table, row), title)

    def _on_focus_changed(self, _old, now) -> None:
        """Dismiss the preview snippet when focus moves to a widget outside this
        news panel (i.e. the user clicked off it). Focus staying within the
        panel — table, filter box — keeps the preview up."""
        if now is None or now is self or self.isAncestorOf(now):
            return
        self._hide_preview()

    def _hide_preview(self) -> None:
        self._current_url = None
        self.preview_lbl.clear()
        self.preview_lbl.setVisible(False)
        self.table.clearSelection()

    def _open_row(self, row: int, _col: int) -> None:
        url = news_url_at(self.table, row)
        if not url:
            return
        self._mark_read(url)
        self._apply_read_styling()
        QDesktopServices.openUrl(QUrl(url))

    def _show_preview(self, url: str, summary: str = "", title: str = "") -> None:
        self._current_url = url
        # Prefer the feed's own summary — reliable and instant. Skip it only when
        # it's empty or just echoes the headline; then fall back to fetching the
        # article's OpenGraph description off-thread.
        s = (summary or "").strip()
        if s and s.casefold() != title.strip().casefold():
            self._snippet_cache[url] = s
            self._set_preview(s)
            return
        if url in self._snippet_cache:
            self._set_preview(self._snippet_cache[url])
            return
        self.preview_lbl.setText("Loading preview…")
        self.preview_lbl.setVisible(True)
        self._hub.run_async(lambda u=url: self._fetch_snippet(u))

    def _fetch_snippet(self, url: str) -> None:
        """Worker-thread body: fetch, then hop back to the GUI thread via the
        queued signal (guarded so a closed panel can't crash on emit)."""
        text = fetch_article_snippet(url)
        try:
            self.snippet_ready.emit(url, text or "")
        except RuntimeError:
            pass  # panel was destroyed mid-fetch

    def _on_snippet(self, url: str, text: str) -> None:
        self._snippet_cache[url] = text or None
        if url == self._current_url:
            self._set_preview(text or None)

    def _set_preview(self, text: Optional[str]) -> None:
        self.preview_lbl.setText(text if text else "(no preview available)")
        self.preview_lbl.setVisible(True)

    # -- read-state persistence (subclasses fold into settings/restore) ----

    def _read_state(self) -> list:
        # cap so the layout file can't grow without bound
        return list(self._read_urls)[:1000]

    def _restore_read(self, urls: Any) -> None:
        if isinstance(urls, list):
            self._read_urls = {str(u) for u in urls if u}
