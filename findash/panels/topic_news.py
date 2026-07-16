"""Topic News panel — free-text news query, independent of the linked
symbol. Layouts can preconfigure instances with different queries (e.g.
"Brazil", "energy commodities") via ``settings()``/``restore()``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton

from ..panel import Panel, register_panel
from ._news_common import make_news_table, news_url_at, populate_news_table

DEFAULT_QUERY = "markets"


@register_panel(id="topic_news", title="Topic News", category="News")
class TopicNewsPanel(Panel):
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

        self.table = make_news_table(self)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.content_layout.addWidget(self.table, 1)

        self._apply_query()

    # -- query handling ---------------------------------------------------

    def _apply_query(self) -> None:
        query = self.query_edit.text().strip() or DEFAULT_QUERY
        self.query_edit.setText(query)
        self._query = query
        self.set_status(query)
        self.unsubscribe_all()
        self.subscribe(f"newsq:{query}", self._on_news)

    def _on_news(self, data: Any) -> None:
        count = populate_news_table(self.table, data)
        suffix = f"{count} headlines" if count else "no news"
        self.set_status(f"{self._query} · {suffix}")

    def _open_row(self, row: int, _column: int) -> None:
        url = news_url_at(self.table, row)
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"query": self._query}

    def restore(self, settings: dict) -> None:
        query = settings.get("query") if isinstance(settings, dict) else None
        if isinstance(query, str) and query.strip():
            self.query_edit.setText(query.strip())
            self._apply_query()
