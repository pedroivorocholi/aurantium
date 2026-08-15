"""Topic News panel — free-text news query, independent of the linked
symbol. Layouts can preconfigure instances with different queries (e.g.
"Brazil", "energy commodities") via ``settings()``/``restore()``.

Shares the symbol News panel's filter box, read/unread dimming, and on-click
preview (see ``NewsPanelBase``); adds its own query field on top.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

from ..panel import register_panel
from ._news_common import NewsPanelBase

DEFAULT_QUERY = "markets"


@register_panel(id="topic_news", title="Topic News", category="News")
class TopicNewsPanel(NewsPanelBase):
    def build(self) -> None:
        self._query = DEFAULT_QUERY

        query_row = QHBoxLayout()
        self.query_edit = QLineEdit(self)
        self.query_edit.setText(self._query)
        self.query_edit.setPlaceholderText("Search query…")
        self.query_edit.returnPressed.connect(self._apply_query)
        set_btn = QPushButton("Set", self)
        set_btn.clicked.connect(self._apply_query)
        query_row.addWidget(self.query_edit, 1)
        query_row.addWidget(set_btn)
        self.content_layout.addLayout(query_row)

        self._build_news_ui()

        self._apply_query()

    # -- query handling ---------------------------------------------------

    def _apply_query(self) -> None:
        query = self.query_edit.text().strip() or DEFAULT_QUERY
        self.query_edit.setText(query)
        self._query = query
        self.set_status(query)
        self.set_news_empty_text(
            f"No headlines for “{query}”", "Try a broader search above"
        )
        self.unsubscribe_all()
        self.subscribe(f"newsq:{query}", self._on_news)

    def _on_news(self, data: Any) -> None:
        count = self._render_news(data)
        suffix = f"{count} headlines" if count else "no news"
        self.set_status(f"{self._query} · {suffix}")

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"query": self._query, "read": self._read_state()}

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        self._restore_read(settings.get("read"))
        query = settings.get("query")
        if isinstance(query, str) and query.strip():
            self.query_edit.setText(query.strip())
            self._apply_query()
