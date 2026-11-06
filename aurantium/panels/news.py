"""News panel — headlines from one of three sources, chosen per instance
from the link badge:

* **Topic** (unlinked, the default) watches its own free-text query
  (``newsq:QUERY``) — what the retired Topic News panel did, whose id now
  aliases here;
* **Linked** (any link group) follows the group's symbol (``news:SYM``);
* **Portfolio** (the badge menu's last entry) reads the app-wide book
  (``PortfolioStore``) and shows the headlines that can move it
  (``newsp:<book>``), each tagged with why. It keeps the panel's group, which
  only receives the tickers clicked in it.

Single-click a headline for a quick preview snippet (and it dims as read);
double-click to open it in the browser. The filter box narrows the list live.
"""

from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLineEdit

from ..components.responsive_row import ResponsiveRow
from ..fx import FxService
from ..guide import PanelGuide
from ..panel import EMPTY_NO_SYMBOL, Panel, PanelRegistry, register_panel
from ..symbol_context import DEFAULT_GROUP, UNLINKED
from ._news_common import NewsPanelBase, _set_news_columns, news_symbol_at

SOURCES = ("linked", "topic", "portfolio")
DEFAULT_QUERY = "markets"
#: store edits arrive in bursts (add, then a cost fill; undo of several rows)
_PORTFOLIO_DEBOUNCE_MS = 750


def _status(prefix: str, count: int) -> str:
    suffix = f"{count} headlines" if count else "no news"
    return f"{prefix} · {suffix}" if prefix else suffix


@register_panel(id="news", title="News", category="News")
class NewsPanel(NewsPanelBase):
    GUIDE = PanelGuide(
        purpose=(
            "Headlines from one of three sources. **Topic** (the default): a free-text "
            "query — a theme, a country, a sector — newest first. **Linked**: the linked "
            "symbol, newest first — the past week, merged from publisher feeds, Google "
            "News and Finnhub (up to a month for quiet names). **Portfolio**: news that "
            "can move your open "
            "positions — about the holdings themselves, the sectors the book leans on, and "
            "macro events — ranked by relevance: larger positions and fresher news first."
        ),
        links=(
            "The link badge picks the source: **Unlinked** is Topic, a **group** makes it "
            "Linked news for that group's symbol, and **Portfolio news** at the bottom of "
            "the badge menu switches to your book. Portfolio ignores the group's symbol, "
            "but clicking a headline tagged with a ticker sends that ticker to the group."
        ),
        interactions=(
            "Each panel remembers its source, saved with the layout.",
            "In Topic, type a topic in the left box and press Enter. *markets*, *forex*, "
            "*crypto* and *mergers* also pull Finnhub's market wire.",
            "In Portfolio, the **Why** column tags each headline: a ticker you hold, a "
            "sector and its weight in the book, or *Macro*. An empty portfolio shows "
            "nothing — add positions in the Portfolio panel.",
            "Single-click a headline for a preview below the list; it dims once read.",
            "Double-click to open the article in your browser.",
            "The filter box (right) narrows the headlines (and Why tags) as you type; read state "
            "is saved with the layout.",
            "Only your reading languages appear (Settings ▸ News Languages…).",
        ),
        keywords=(
            "headlines", "articles", "stories", "press", "media", "newsfeed", "RSS",
            "sentiment", "topic", "breaking", "feed",
        ),
    )

    def build(self) -> None:
        # panels open unlinked, so a new News panel is Topic news; linking it
        # to a group makes it Linked
        self._source = "topic"
        self._query = DEFAULT_QUERY
        self._linked_symbol = ""
        # Portfolio mode
        self._store = None
        self._fx = None
        self._news_topic: Optional[str] = None
        self._profile_syms: set[str] = set()
        self._sectors: dict[str, Optional[str]] = {}
        self._portfolio_debounce = QTimer(self)
        self._portfolio_debounce.setSingleShot(True)
        self._portfolio_debounce.setInterval(_PORTFOLIO_DEBOUNCE_MS)
        self._portfolio_debounce.timeout.connect(self._rebuild_portfolio)

        self._build_news_ui()

        # -- one slim row: Topic's query (hidden in the other modes) beside
        # the filter, so the headlines start right under the header
        self.query_edit = QLineEdit(self)
        self.query_edit.setText(self._query)
        self.query_edit.setPlaceholderText("Topic — press Enter")
        self.query_edit.setToolTip("The topic this panel follows; press Enter to apply")
        self.query_edit.returnPressed.connect(self._apply_query)
        self.content_layout.removeWidget(self._news_filter)
        row = ResponsiveRow()
        row.addWidget(self.query_edit, 1)
        row.addWidget(self._news_filter, 1)
        self.content_layout.insertLayout(0, row)

        # -- Portfolio sits in the link badge's menu, under the groups
        menu = self._badge.menu()
        for act in menu.actions():
            if act.text() == "Unlinked":
                act.setText("Unlinked — topic news")
        menu.addSeparator()
        self._portfolio_act = QAction("Portfolio news", menu)
        self._portfolio_act.setCheckable(True)
        self._portfolio_act.toggled.connect(self._on_portfolio_toggled)
        menu.addAction(self._portfolio_act)

        self._show_source("topic")

    # -- source ------------------------------------------------------------

    def set_link_group(self, group: str) -> None:
        # the badge picks the source: a group is Linked, Unlinked is Topic —
        # and choosing either leaves Portfolio
        Panel.set_link_group(self, group)
        if hasattr(self, "_portfolio_act"):  # built
            self._show_source("topic" if group == UNLINKED else "linked")

    def set_source(self, source: str) -> None:
        """Switch what this panel shows, moving the link group to match:
        Topic unlinks, Linked joins a group if it had none, Portfolio keeps
        the group (it only receives clicked tickers)."""
        if source not in SOURCES:
            source = "topic"
        if source == "topic" and self._link_group != UNLINKED:
            Panel.set_link_group(self, UNLINKED)
        elif source == "linked" and self._link_group == UNLINKED:
            Panel.set_link_group(self, DEFAULT_GROUP)
        self._show_source(source)

    def _on_portfolio_toggled(self, on: bool) -> None:
        if on:
            self._show_source("portfolio")
        else:
            self._show_source("topic" if self._link_group == UNLINKED else "linked")

    def _show_source(self, source: str) -> None:
        """Show ``source``; drops the old source's subscriptions."""
        self._source = source
        self._portfolio_act.blockSignals(True)
        self._portfolio_act.setChecked(source == "portfolio")
        self._portfolio_act.blockSignals(False)
        self._update_badge()
        if source == "portfolio":
            self._badge.setToolTip(
                "Portfolio news — clicked tickers go to the link group"
                if self._link_group != UNLINKED
                else "Portfolio news — link a group to send clicked tickers to it"
            )
        self.query_edit.setVisible(source == "topic")
        self.refresh_header_symbol()
        self._portfolio_debounce.stop()
        self._watch_store(source == "portfolio")
        self.unsubscribe_all()
        self._news_topic = None
        self._profile_syms.clear()
        self.table.setRowCount(0)
        _set_news_columns(self.table, show_reason=source == "portfolio")
        self.set_loading(False)
        self.set_status("")
        if source == "linked":
            if self._linked_symbol:
                self._subscribe_linked(self._linked_symbol)
            else:
                self.set_news_empty_text(
                    EMPTY_NO_SYMBOL,
                    "Click a ticker in any linked panel, or type one in the search box",
                )
        elif source == "topic":
            self._apply_query()
        else:
            self._rebuild_portfolio()

    # -- Linked ------------------------------------------------------------

    def on_symbol(self, symbol: str) -> None:
        # remembered in every mode, so switching back to Linked picks it up
        self._linked_symbol = symbol
        self.refresh_header_symbol()
        if self._source == "linked":
            self._subscribe_linked(symbol)

    def header_symbol(self) -> str:
        # Topic and Portfolio ignore the link group, so the header names no symbol
        if getattr(self, "_source", "linked") != "linked":
            return ""
        return getattr(self, "_linked_symbol", "") or self._current_symbol

    def _subscribe_linked(self, symbol: str) -> None:
        self.set_loading(True)
        self.set_news_empty_text(f"No recent headlines for {symbol}")
        self.unsubscribe_all()
        self.subscribe(f"news:{symbol}", self._on_linked_news)

    def _on_linked_news(self, data: Any) -> None:
        # The fetch resolved — lower the veil before deciding whether
        # there is anything to show.
        self.set_loading(False)
        self.set_status(_status("", self._render_news(data)))

    # -- Topic -------------------------------------------------------------

    def _apply_query(self) -> None:
        query = self.query_edit.text().strip() or DEFAULT_QUERY
        self.query_edit.setText(query)
        self._query = query
        if self._source != "topic":
            return
        self.set_status(query)
        self.set_news_empty_text(
            f"No headlines for “{query}”", "Try a broader topic"
        )
        self.unsubscribe_all()
        self.subscribe(f"newsq:{query}", self._on_topic_news)

    def _on_topic_news(self, data: Any) -> None:
        self.set_status(_status(self._query, self._render_news(data)))

    # -- Portfolio ---------------------------------------------------------

    def _watch_store(self, on: bool) -> None:
        """Follow the app-wide book only while in Portfolio mode — the store
        isn't touched at all by Linked/Topic panels."""
        if on and self._store is None:
            from ..portfolio_store import PortfolioStore

            self._store = PortfolioStore.instance()
            self._store.changed.connect(self._schedule_portfolio)
            # a rate landing or a new preferred currency reweighs the book
            self._fx = FxService.instance()
            self._fx.changed.connect(self._schedule_portfolio)
        elif not on and self._store is not None:
            try:
                self._store.changed.disconnect(self._schedule_portfolio)
            except (RuntimeError, TypeError):
                pass
            if self._fx is not None:
                try:
                    self._fx.changed.disconnect(self._schedule_portfolio)
                except (RuntimeError, TypeError):
                    pass
            self._store = None
            self._fx = None

    def _schedule_portfolio(self) -> None:
        self._portfolio_debounce.start()

    def _unsubscribe(self, topic: str) -> None:
        self._hub.unsubscribe(self, topic)
        self._topics.discard(topic)

    def _rebuild_portfolio(self) -> None:
        if self._source != "portfolio" or self._store is None:
            return
        from ..portfolio_news import _pos_num, build_book, encode_book

        fx = FxService.instance()

        positions = self._store.open_positions()
        symbols = list(dict.fromkeys(p["symbol"] for p in positions))
        # sectors feed the exposure layer; a profile that arrives later
        # re-debounces, so the book catches up without blocking on it
        for sym in self._profile_syms - set(symbols):
            self._unsubscribe(f"profile:{sym}")
        self._profile_syms &= set(symbols)
        for sym in symbols:
            if sym not in self._profile_syms:
                self._profile_syms.add(sym)
                # the sector layer is optional: a failed profile fetch
                # mustn't post an error over headlines that are loading
                self.subscribe(
                    f"profile:{sym}",
                    lambda d, s=sym: self._on_profile(s, d),
                    on_error=lambda _err: None,
                )

        # Weights come from cost, so the topic (the cache key) is the same at
        # every launch whether or not a quote has landed yet; a live price
        # only stands in for a position with no usable cost.
        costed = {p["symbol"] for p in positions if _pos_num(p.get("cost"))}
        prices = {}
        for sym in symbols:
            if sym in costed:
                continue
            quote = self._hub.peek(f"quote:{sym}")
            if isinstance(quote, dict) and quote.get("price") is not None:
                prices[sym] = quote["price"]
        for sym in symbols:  # a quote also reveals the listing currency
            quote = self._hub.peek(f"quote:{sym}")
            if isinstance(quote, dict):
                fx.note_quote(sym, quote)
        book = build_book(positions, prices, self._sectors, to_pref=fx.value)
        if positions and not book:
            # every position is still waiting on its rate: hold the current
            # list rather than flash "empty"; FxService.changed re-runs this
            if self.table.rowCount() == 0:
                self.set_loading(True)
            return
        if not book:
            # an empty book shows nothing at all — not even macro news
            self.unsubscribe_all()
            self._news_topic = None
            self._profile_syms.clear()
            self.table.setRowCount(0)
            self.set_loading(False)
            self.set_status("")
            self.set_news_empty_text(
                "Your portfolio is empty", "Add positions in the Portfolio panel"
            )
            return
        topic = "newsp:" + encode_book(book)
        if topic == self._news_topic:
            return
        if self._news_topic is not None:
            self._unsubscribe(self._news_topic)
        self._news_topic = topic
        self.set_news_empty_text("No headlines for your portfolio yet")
        if self.table.rowCount() == 0:
            self.set_loading(True)
        self.subscribe(topic, self._on_portfolio_news)

    def _on_profile(self, symbol: str, data: Any) -> None:
        if not isinstance(data, dict):
            return
        sector = data.get("sector") or None
        if self._sectors.get(symbol) != sector:
            self._sectors[symbol] = sector
            self._schedule_portfolio()

    def _on_portfolio_news(self, data: Any) -> None:
        self.set_loading(False)
        count = self._render_news(data, show_reason=True)
        if not count:  # resolved empty: "yet" would read as still loading
            self.set_news_empty_text("No recent headlines for your portfolio")
        self.set_status(_status("portfolio", count))

    def _on_cell_clicked(self, row: int, col: int) -> None:
        super()._on_cell_clicked(row, col)
        if self._source == "portfolio":
            symbol = news_symbol_at(self.table, row)
            if symbol:
                self.set_symbol(symbol)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._portfolio_debounce.stop()
        self._watch_store(False)
        super().closeEvent(event)

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"source": self._source, "query": self._query, "read": self._read_state()}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        self._restore_read(settings.get("read"))
        query = settings.get("query")
        if isinstance(query, str) and query.strip():
            self._query = query.strip()
            self.query_edit.setText(self._query)
        # The link group, restored first, already chose Linked or Topic; only
        # Portfolio is stored on its own. A Topic dock that carried a group it
        # ignored (Topic News, and this branch's first layouts) unlinks.
        source = settings.get("source")
        if source == "portfolio" or (source == "topic" and self._source != "topic"):
            self.set_source(source)
        elif self._source == "topic":
            self._apply_query()  # the restored query, not the default


# the retired Topic News panel: menus, commands and presets naming it open
# this panel in Topic mode
PanelRegistry.add_alias("topic_news", "news", {"source": "topic"})


def migrate_news_layout(doc: dict) -> dict:
    """Rewrite a layout's retired ``topic_news`` docks as News panels in Topic
    mode with their query and read state. Each keeps its instance id and link
    group, so the saved dock arrangement still puts it where it was. Returns
    ``doc`` itself when there is nothing to migrate, else a new dict; ``doc``
    is never modified."""
    panels = doc.get("panels") if isinstance(doc, dict) else None
    if not isinstance(panels, list) or not any(
        isinstance(s, dict) and s.get("panel_id") == "topic_news" for s in panels
    ):
        return doc
    out: list = []
    for spec in panels:
        if not isinstance(spec, dict) or spec.get("panel_id") != "topic_news":
            out.append(spec)
            continue
        old = spec.get("settings") if isinstance(spec.get("settings"), dict) else {}
        read = old.get("read")
        new_spec = dict(spec)
        new_spec["panel_id"] = "news"
        new_spec["link_group"] = UNLINKED  # unlinked is what makes it Topic
        new_spec["settings"] = {
            "source": "topic",
            "query": old.get("query") or DEFAULT_QUERY,
            "read": list(read) if isinstance(read, list) else [],
        }
        out.append(new_spec)
    new = dict(doc)
    new["panels"] = out
    return new
