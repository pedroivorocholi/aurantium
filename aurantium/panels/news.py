"""News panel — headlines for the linked symbol.

Single-click a headline for a quick preview snippet (and it dims as read);
double-click to open it in the browser. The filter box narrows the list live.
"""

from __future__ import annotations

from typing import Any

from ..panel import register_panel
from ._news_common import NewsPanelBase


@register_panel(id="news", title="News", category="News")
class NewsPanel(NewsPanelBase):
    def build(self) -> None:
        self._build_news_ui()

    def on_symbol(self, symbol: str) -> None:
        self.set_status("loading…")
        self.set_news_empty_text(f"No recent headlines for {symbol}")
        self.unsubscribe_all()
        self.subscribe(f"news:{symbol}", self._on_news)

    def _on_news(self, data: Any) -> None:
        count = self._render_news(data)
        suffix = f"{count} headlines" if count else "no news"
        self.set_status(suffix)

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {"read": self._read_state()}

    def restore(self, settings: dict) -> None:
        if isinstance(settings, dict):
            self._restore_read(settings.get("read"))
