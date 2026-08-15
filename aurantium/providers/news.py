"""News provider: ``news:SYM`` via a source waterfall (adapted from Fincept).

Order: publisher RSS feeds (Fincept's default-feed approach, free and
keyless) -> NewsAPI.org (if NEWSAPI_KEY is set) -> gnews package ->
yfinance Ticker.news as a last resort. RSS results are only accepted when
enough headlines match the symbol/query; otherwise the fetch falls through
to the next source, so thin RSS matches never mask the older sources.

**Language handling.** Every source is scoped to the languages the user reads
(``languages.spoken_languages``): RSS fetches only the feeds published in those
languages, NewsAPI is queried once per language, and gnews gets a matching
``language``/``country`` pair. Items carry the ``lang`` they came from. Whatever
still slips through — notably yfinance, which offers no language control — is
dropped by ``languages.filter_items`` immediately before publishing, so a panel
can never render a headline in a language the user didn't pick.
"""

from __future__ import annotations

import html as _html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from .. import languages
from ..datahub import DataHub, Provider

MAX_ITEMS = 25

# Curated market-focused RSS feeds — free, no key. The English set is a subset
# of Fincept Terminal's defaults (research/fincept-terminal
# .../NewsService_Feeds.cpp); the rest give each offered language at least one
# live market feed, so picking a language actually *adds* coverage rather than
# only filtering. Every URL here was probed for a parseable feed with items.
RSS_FEEDS: list[tuple[str, str, str, str]] = [
    # (publisher, url, category, language)
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", "markets", "en"),
    ("WSJ", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml", "markets", "en"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/", "markets", "en"),
    (
        "CNBC",
        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
        "markets",
        "en",
    ),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml", "markets", "en"),
    ("BBC", "http://feeds.bbci.co.uk/news/business/rss.xml", "markets", "en"),
    ("Investing.com", "https://www.investing.com/rss/news.rss", "markets", "en"),
    ("Benzinga", "https://www.benzinga.com/feed", "markets", "en"),
    ("OilPrice", "https://oilprice.com/rss/main", "energy", "en"),
    ("FXStreet", "https://www.fxstreet.com/rss/news", "forex", "en"),
    # -- other languages ---------------------------------------------------
    ("Investing.com ES", "https://es.investing.com/rss/news.rss", "markets", "es"),
    ("Expansión", "https://e00-expansion.uecdn.es/rss/portada.xml", "markets", "es"),
    ("Investing.com BR", "https://br.investing.com/rss/news.rss", "markets", "pt"),
    ("Investing.com FR", "https://fr.investing.com/rss/news.rss", "markets", "fr"),
    ("Le Monde Économie", "https://www.lemonde.fr/economie/rss_full.xml", "markets", "fr"),
    ("Investing.com DE", "https://de.investing.com/rss/news.rss", "markets", "de"),
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/finanzen", "markets", "de"),
    ("Investing.com IT", "https://it.investing.com/rss/news.rss", "markets", "it"),
    ("Il Sole 24 Ore", "https://www.ilsole24ore.com/rss/finanza.xml", "markets", "it"),
    ("Investing.com NL", "https://nl.investing.com/rss/news.rss", "markets", "nl"),
    ("Investing.com SE", "https://se.investing.com/rss/news.rss", "markets", "sv"),
    ("Investing.com PL", "https://pl.investing.com/rss/news.rss", "markets", "pl"),
    ("Investing.com TR", "https://tr.investing.com/rss/news.rss", "markets", "tr"),
    ("Investing.com RU", "https://ru.investing.com/rss/news.rss", "markets", "ru"),
    ("Investing.com GR", "https://gr.investing.com/rss/news.rss", "markets", "el"),
    ("Investing.com AR", "https://sa.investing.com/rss/news.rss", "markets", "ar"),
    ("Investing.com IL", "https://il.investing.com/rss/news.rss", "markets", "he"),
    ("Investing.com IN", "https://hi.investing.com/rss/news.rss", "markets", "hi"),
    ("Investing.com JP", "https://jp.investing.com/rss/news.rss", "markets", "ja"),
    ("Investing.com KR", "https://kr.investing.com/rss/news.rss", "markets", "ko"),
    ("Investing.com CN", "https://cn.investing.com/rss/news.rss", "markets", "zh"),
]

RSS_CACHE_TTL = 180.0  # seconds; keeps feed polling polite across panels
RSS_FETCH_TIMEOUT = 6.0
RSS_MIN_MATCHES = 5  # fewer matches than this -> fall through to next source
_RSS_HEADERS = {"User-Agent": "Mozilla/5.0 (aurantium RSS reader)"}

# Cache keyed by the active language set, so changing languages in Settings
# invalidates it implicitly — no cross-wiring from the dialog needed.
_rss_lock = threading.Lock()
_rss_cache: dict[tuple[str, ...], dict[str, Any]] = {}

# gnews wants a country alongside the language; without a sensible pairing it
# defaults to the US edition and returns English for every language.
GNEWS_COUNTRY = {
    "en": "US", "es": "ES", "pt": "BR", "fr": "FR", "de": "DE", "it": "IT",
    "nl": "NL", "sv": "SE", "pl": "PL", "tr": "TR", "ru": "RU", "el": "GR",
    "ar": "AE", "he": "IL", "hi": "IN", "ja": "JP", "ko": "KR", "zh": "CN",
}

# NewsAPI /v2/everything only indexes these. Querying an unsupported code
# returns an error, so unsupported languages simply skip this source.
NEWSAPI_LANGS = {
    "ar", "de", "en", "es", "fr", "he", "it", "nl", "no", "pt", "ru", "sv", "zh",
}
#: Cap on how many languages a keyed/searched source is queried for in one
#: fetch. NewsAPI and gnews both cost one HTTP round trip per language, so a
#: polyglot user picking eight languages would otherwise make the News panel
#: crawl. The RSS tier — the primary source — has no such cap and always covers
#: every selected language.
MAX_LANGS_PER_FETCH = 4


def _parse_feed_datetime(value: str) -> Optional[datetime]:
    """RSS pubDate is RFC-2822, Atom updated is ISO-8601; try both."""
    text = (value or "").strip()
    if not text:
        return None
    dt: Optional[datetime] = None
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt is not None and dt.tzinfo is None:  # keep sort keys comparable
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_summary(text: Any, limit: int = 320) -> str:
    """Strip HTML tags/entities from a feed summary and collapse whitespace.
    Google-News-style descriptions that are just the headline again are dropped
    by the panel (it compares against the title), so returning them is harmless."""
    if not text:
        return ""
    plain = _html.unescape(re.sub(r"<[^>]+>", " ", str(text)))
    return " ".join(plain.split())[:limit]


def _parse_feed_xml(
    xml_text: str, publisher: str, category: str, lang: str = "en"
) -> list[dict]:
    """Extract items from RSS 2.0 or Atom XML; malformed feeds yield []."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    items: list[dict] = []

    for item in root.iter("item"):  # RSS 2.0
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append(
            {
                "title": title,
                "publisher": publisher,
                "url": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
                "summary": _clean_summary(item.findtext("description")),
                "category": category,
                "lang": lang,
            }
        )

    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):  # Atom
        title = (entry.findtext(f"{atom}title") or "").strip()
        if not title:
            continue
        url = ""
        link = entry.find(f"{atom}link")
        if link is not None:
            url = (link.get("href") or "").strip()
        published = (
            entry.findtext(f"{atom}published") or entry.findtext(f"{atom}updated") or ""
        ).strip()
        summary = (
            entry.findtext(f"{atom}summary")
            or entry.findtext(f"{atom}content")
            or ""
        )
        items.append(
            {
                "title": title,
                "publisher": publisher,
                "url": url,
                "published": published,
                "summary": _clean_summary(summary),
                "category": category,
                "lang": lang,
            }
        )
    return items


def _fetch_one_feed(feed: tuple[str, str, str, str]) -> list[dict]:
    publisher, url, category, lang = feed
    try:
        resp = requests.get(url, headers=_RSS_HEADERS, timeout=RSS_FETCH_TIMEOUT)
        resp.raise_for_status()
        return _parse_feed_xml(resp.text, publisher, category, lang)
    except Exception:
        return []  # dead/slow feeds must not break the rest


def _sort_by_published(items: list[dict]) -> list[dict]:
    """Newest first; unparseable timestamps sink to the bottom. Used to merge
    the per-language result sets into one coherent list."""
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    return sorted(
        items,
        key=lambda i: _parse_feed_datetime(i.get("published", "")) or epoch,
        reverse=True,
    )


def feeds_for(langs: list[str]) -> list[tuple[str, str, str, str]]:
    """The feeds published in ``langs``. Falls back to the full table if a
    language set somehow matches nothing, so the panel degrades to "some news"
    rather than to an empty table."""
    selected = [f for f in RSS_FEEDS if f[3] in set(langs)]
    return selected or RSS_FEEDS


def _rss_items(langs: Optional[list[str]] = None) -> list[dict]:
    """Items from every feed in the user's languages, newest first, cached for
    RSS_CACHE_TTL per language set."""
    codes = langs if langs is not None else languages.spoken_languages()
    key = tuple(sorted(codes))
    with _rss_lock:
        entry = _rss_cache.get(key)
        if entry and time.monotonic() - entry["ts"] < RSS_CACHE_TTL:
            return entry["items"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_fetch_one_feed, feeds_for(codes)))
    items = [item for feed_items in results for item in feed_items]
    epoch = datetime.fromtimestamp(0, tz=timezone.utc)
    items.sort(
        key=lambda i: _parse_feed_datetime(i["published"]) or epoch, reverse=True
    )
    with _rss_lock:
        # Only the active set is worth keeping; languages change rarely, and an
        # unbounded dict would pin every set the user ever tried in memory.
        _rss_cache.clear()
        _rss_cache[key] = {"ts": time.monotonic(), "items": items}
    return items


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
            langs = languages.spoken_languages()
            items = self._from_rss_symbol(symbol, langs)
            if items is None:
                items = self._from_newsapi(symbol, langs)
            if items is None:
                items = self._from_gnews(symbol, langs)
            if items is None:
                items = self._from_yfinance(symbol)
            if items is None:
                items = []
            hub.publish(topic, self._gate(items, langs))
        except Exception as exc:
            hub.publish_error(topic, f"news fetch failed: {exc}")

    def _fetch_query(self, topic: str, query: str) -> None:
        """Free-text query waterfall: NewsAPI -> gnews, no yfinance fallback
        (yfinance's ``Ticker.news`` is symbol-only, not a text search)."""
        hub = DataHub.instance()
        try:
            langs = languages.spoken_languages()
            items = self._from_rss_query(query, langs)
            if items is None:
                items = self._from_newsapi(query, langs)
            if items is None:
                items = self._from_gnews_query(query, langs)
            if items is None:
                items = []
            hub.publish(topic, self._gate(items, langs))
        except Exception as exc:
            hub.publish_error(topic, f"news query fetch failed: {exc}")

    @staticmethod
    def _gate(items: list[dict], langs: list[str]) -> list[dict]:
        """Last line of defence before anything reaches a panel: drop entries
        that aren't in one of the user's languages, then cap the list.

        Trimming happens *after* filtering — capping first would let rejected
        headlines eat slots and leave a short list of survivors.
        """
        return languages.filter_items(items, langs)[:MAX_ITEMS]

    # -- sources -------------------------------------------------------

    @staticmethod
    def _strip_category(items: list[dict]) -> list[dict]:
        """Drop the internal 'category' key so published payloads keep the
        same shape as the other sources (title/publisher/url/published)."""
        return [{k: v for k, v in i.items() if k != "category"} for i in items]

    def _from_rss_symbol(
        self, symbol: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Headlines mentioning the ticker as a standalone word (case-
        sensitive: 'AAPL' matches, 'aapl' inside a word doesn't). Recall is
        deliberately conservative — most symbols fall through to the
        broader sources below."""
        try:
            pattern = re.compile(rf"\b{re.escape(symbol)}\b")
            matches = [i for i in _rss_items(langs) if pattern.search(i["title"])]
            if len(matches) < RSS_MIN_MATCHES:
                return None
            return self._strip_category(matches)
        except Exception:
            return None

    def _from_rss_query(
        self, query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Headlines where every query word appears in the title, or where
        the query names a feed category (e.g. the Topic News default
        'markets' pulls the whole markets feed set)."""
        try:
            tokens = [t for t in query.lower().split() if t]
            if not tokens:
                return None
            matches = []
            for item in _rss_items(langs):
                title = item["title"].lower()
                if all(t in title for t in tokens) or query.lower() == item["category"]:
                    matches.append(item)
            if len(matches) < RSS_MIN_MATCHES:
                return None
            return self._strip_category(matches)
        except Exception:
            return None

    def _from_newsapi(
        self, symbol: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """NewsAPI ``/v2/everything``, once per language.

        The endpoint takes a single ``language`` per call and, when the
        parameter is omitted, returns **every** language it indexes — which is
        how Mandarin headlines used to reach an English-only reader. Querying
        per language is the fix; results are merged newest-first.
        """
        api_key = os.environ.get("NEWSAPI_KEY")
        if not api_key:
            return None
        codes = langs if langs is not None else languages.spoken_languages()
        supported = [c for c in codes if c in NEWSAPI_LANGS][:MAX_LANGS_PER_FETCH]
        if not supported:
            return None  # nothing this source can serve; fall through to gnews
        items: list[dict] = []
        for code in supported:
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": symbol,
                        "language": code,
                        "sortBy": "publishedAt",
                        "pageSize": MAX_ITEMS,
                    },
                    headers={"X-Api-Key": api_key},
                    timeout=10,
                )
                resp.raise_for_status()
                articles = resp.json().get("articles", [])
            except Exception:
                continue  # one bad language must not sink the others
            for a in articles[:MAX_ITEMS]:
                source = a.get("source") or {}
                items.append(
                    {
                        "title": a.get("title") or "",
                        "publisher": source.get("name") or "",
                        "url": a.get("url") or "",
                        "published": a.get("publishedAt") or "",
                        "summary": _clean_summary(a.get("description")),
                        "lang": code,
                    }
                )
        if not items:
            return None  # fall through to gnews
        return _sort_by_published(items)

    def _from_gnews(
        self, symbol: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        return self._gnews_search(f'"{symbol}" stock', langs)

    def _from_gnews_query(
        self, query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Like ``_from_gnews`` but for free-text queries: no stock-ticker
        decoration around the search string."""
        return self._gnews_search(query, langs)

    @staticmethod
    def _gnews_search(
        query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """One gnews edition per language, merged newest-first. GNews defaults
        to the US/English edition, so the language/country pair is what makes
        the other languages show up at all."""
        try:
            from gnews import GNews
        except Exception:
            return None
        codes = langs if langs is not None else languages.spoken_languages()
        items: list[dict] = []
        for code in codes[:MAX_LANGS_PER_FETCH]:
            try:
                gn = GNews(
                    language=code,
                    country=GNEWS_COUNTRY.get(code, "US"),
                    max_results=MAX_ITEMS,
                )
                results = gn.get_news(query) or []
            except Exception:
                continue
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
                        "summary": _clean_summary(r.get("description")),
                        "lang": code,
                    }
                )
        if not items:
            return None
        return _sort_by_published(items)

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
            summary = content.get("summary") or content.get("description") or ""
            return {
                "title": title, "publisher": publisher, "url": url,
                "published": published, "summary": _clean_summary(summary),
            }

        title = item.get("title") or ""
        publisher = item.get("publisher") or ""
        url = item.get("link") or ""
        summary = item.get("summary") or ""
        published = ""
        ts = item.get("providerPublishTime")
        if ts:
            try:
                published = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            except Exception:
                published = ""
        return {
            "title": title, "publisher": publisher, "url": url,
            "published": published, "summary": _clean_summary(summary),
        }
