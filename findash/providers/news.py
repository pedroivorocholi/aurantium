"""News provider: ``news:SYM`` via a source waterfall (adapted from Fincept).

Order: NewsAPI.org (if NEWSAPI_KEY is set) -> gnews package -> yfinance
Ticker.news as a last resort. Each source is best-effort; failures fall
through to the next source rather than failing the topic.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from ..datahub import DataHub, Provider

MAX_ITEMS = 25


class NewsProvider(Provider):
    """Serves ``news:*`` topics."""

    def topic_patterns(self) -> list[str]:
        return ["news:*", "newsq:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            if topic.startswith("newsq:"):
                query = topic.split(":", 1)[1]
                hub.run_async(lambda t=topic, q=query: self._fetch_query(t, q))
                continue
            parts = topic.split(":")
            if len(parts) != 2:
                hub.publish_error(topic, f"malformed news topic: {topic}")
                continue
            symbol = parts[1]
            hub.run_async(lambda t=topic, s=symbol: self._fetch(t, s))

    def _fetch(self, topic: str, symbol: str) -> None:
        hub = DataHub.instance()
        try:
            items = self._from_newsapi(symbol)
            if items is None:
                items = self._from_gnews(symbol)
            if items is None:
                items = self._from_yfinance(symbol)
            if items is None:
                items = []
            hub.publish(topic, items[:MAX_ITEMS])
        except Exception as exc:
            hub.publish_error(topic, f"news fetch failed: {exc}")

    def _fetch_query(self, topic: str, query: str) -> None:
        """Free-text query waterfall: NewsAPI -> gnews, no yfinance fallback
        (yfinance's ``Ticker.news`` is symbol-only, not a text search)."""
        hub = DataHub.instance()
        try:
            items = self._from_newsapi(query)
            if items is None:
                items = self._from_gnews_query(query)
            if items is None:
                items = []
            hub.publish(topic, items[:MAX_ITEMS])
        except Exception as exc:
            hub.publish_error(topic, f"news query fetch failed: {exc}")

    # -- sources -------------------------------------------------------

    def _from_newsapi(self, symbol: str) -> Optional[list[dict]]:
        api_key = os.environ.get("NEWSAPI_KEY")
        if not api_key:
            return None
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": symbol,
                    "sortBy": "publishedAt",
                    "pageSize": MAX_ITEMS,
                },
                headers={"X-Api-Key": api_key},
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
            items = []
            for a in articles[:MAX_ITEMS]:
                source = a.get("source") or {}
                items.append(
                    {
                        "title": a.get("title") or "",
                        "publisher": source.get("name") or "",
                        "url": a.get("url") or "",
                        "published": a.get("publishedAt") or "",
                    }
                )
            return items
        except Exception:
            return None  # fall through to gnews

    def _from_gnews(self, symbol: str) -> Optional[list[dict]]:
        try:
            from gnews import GNews
        except Exception:
            return None
        try:
            gn = GNews(max_results=MAX_ITEMS)
            results = gn.get_news(f'"{symbol}" stock') or []
            items = []
            for r in results[:MAX_ITEMS]:
                publisher = r.get("publisher")
                if isinstance(publisher, dict):
                    publisher = publisher.get("title", "")
                items.append(
                    {
                        "title": r.get("title") or "",
                        "publisher": publisher or "",
                        "url": r.get("url") or "",
                        "published": r.get("published date") or "",
                    }
                )
            return items
        except Exception:
            return None  # fall through to yfinance

    def _from_gnews_query(self, query: str) -> Optional[list[dict]]:
        """Like ``_from_gnews`` but for free-text queries: no stock-ticker
        decoration around the search string."""
        try:
            from gnews import GNews
        except Exception:
            return None
        try:
            gn = GNews(max_results=MAX_ITEMS)
            results = gn.get_news(query) or []
            items = []
            for r in results[:MAX_ITEMS]:
                publisher = r.get("publisher")
                if isinstance(publisher, dict):
                    publisher = publisher.get("title", "")
                items.append(
                    {
                        "title": r.get("title") or "",
                        "publisher": publisher or "",
                        "url": r.get("url") or "",
                        "published": r.get("published date") or "",
                    }
                )
            return items
        except Exception:
            return None

    def _from_yfinance(self, symbol: str) -> Optional[list[dict]]:
        try:
            import yfinance as yf
        except Exception:
            return None
        try:
            tkr = yf.Ticker(symbol)
            raw = tkr.news or []
            items = []
            for item in raw[:MAX_ITEMS]:
                items.append(self._parse_yf_news_item(item))
            return items
        except Exception:
            return None

    @staticmethod
    def _parse_yf_news_item(item: dict) -> dict:
        """yfinance's news dict shape has varied across versions: newer
        releases nest fields under item["content"], older ones are flat."""
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, dict):
            title = content.get("title") or ""
            provider = content.get("provider") or {}
            publisher = provider.get("displayName", "") if isinstance(provider, dict) else ""
            url = ""
            canonical = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
            if isinstance(canonical, dict):
                url = canonical.get("url", "") or ""
            published = content.get("pubDate") or content.get("displayTime") or ""
            return {"title": title, "publisher": publisher, "url": url, "published": published}

        title = item.get("title") or ""
        publisher = item.get("publisher") or ""
        url = item.get("link") or ""
        published = ""
        ts = item.get("providerPublishTime")
        if ts:
            try:
                published = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except Exception:
                published = ""
        return {"title": title, "publisher": publisher, "url": url, "published": published}
