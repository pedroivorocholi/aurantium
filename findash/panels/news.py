"""News panel — headlines for the linked symbol, double-click to open."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from ..panel import Panel, register_panel
from ._news_common import make_news_table, news_url_at, populate_news_table


@register_panel(id="news", title="News", category="News")
class NewsPanel(Panel):
    def build(self) -> None:
        self.table = make_news_table(self)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.content_layout.addWidget(self.table, 1)

    def on_symbol(self, symbol: str) -> None:
        self.set_status(f"{symbol} loading…")
        self.unsubscribe_all()
        self.subscribe(f"news:{symbol}", self._on_news)

    def _on_news(self, data: Any) -> None:
        count = populate_news_table(self.table, data)
        suffix = f"{count} headlines" if count else "no news"
        self.set_status(f"{self.current_symbol} · {suffix}")

    def _open_row(self, row: int, _column: int) -> None:
        url = news_url_at(self.table, row)
        if url:
            QDesktopServices.openUrl(QUrl(url))
