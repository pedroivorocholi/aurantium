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
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from .. import languages
from ..datahub import DataHub, Provider

MAX_ITEMS = 25

# Curated market-focused RSS feeds — free, no key. The English set is a subset
# of Fincept Terminal's defaults (fincept-qt/src/services/news/
# NewsService_Feeds.cpp — upstream repo and commit are recorded in
# research/fincept-terminal.md); the rest give each offered language at
# least one
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


#: NewsAPI's free tier indexes roughly the last month. Asking it for 2019
#: doesn't error, it just returns nothing -- so the guard is ours to apply.
NEWSAPI_LOOKBACK_DAYS = 28


def _date_tuple(iso: Optional[str]) -> Optional[tuple]:
    """'YYYY-MM-DD' -> (y, m, d), the shape gnews wants for start_date/end_date
    (gnews.py:159-186 also accepts a datetime, but the tuple is unambiguous)."""
    if not iso:
        return None
    try:
        d = date.fromisoformat(str(iso).strip())
    except (TypeError, ValueError):
        return None
    return (d.year, d.month, d.day)


def _within_newsapi_window(start: Optional[str]) -> bool:
    """True when a window starts recently enough for NewsAPI's free tier to
    have anything at all. Skipping the call is better than a wasted round trip
    against a rate-limited key."""
    d = _date_tuple(start)
    if d is None:
        return False
    return (date.today() - date(*d)).days <= NEWSAPI_LOOKBACK_DAYS


def _published_within(published: Any, start: str, end: str) -> bool:
    """Is a feed item's publish date inside [start, end] (inclusive)?

    Used to post-filter the RSS tier, which cannot be asked for a range.
    Items with an unparseable date are **excluded**: in a historical window an
    undated headline is far more likely to be today's than the day in
    question, and showing it would answer the user's question wrongly."""
    dt = _parse_feed_datetime(published if isinstance(published, str) else "")
    if dt is None:
        return False
    return start <= dt.date().isoformat() <= end


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


# -- ticker relevance -------------------------------------------------------
#
# Every non-RSS source searches loosely: NewsAPI matches the ticker anywhere
# in an article body (NRG -> Houston Texans at NRG Stadium), and yfinance's
# ticker feed mixes in peers' stories. A headline must name the company.

#: Enough on-topic items that the waterfall stops asking further sources.
RELEVANT_ENOUGH = 10

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "company", "co", "ltd",
    "limited", "plc", "llc", "lp", "l.p", "nv", "n.v", "sa", "s.a", "ag",
    "se", "spa", "s.p.a", "ab", "asa", "oyj", "kgaa", "bhd", "tbk",
}
_SHARE_CLASS = re.compile(r"\s+class\s+[a-z]\b.*$", re.IGNORECASE)
# Futures names end in their contract month: "Gold Dec 26", "Crude Oil Nov 26".
_CONTRACT_MONTH = re.compile(
    r"\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{2,4}$",
    re.IGNORECASE,
)
#: A fund's name is fund-family vocabulary ("State Street SPDR ... Trust"):
#: its single words identify nothing, so only the ticker or full name count.
_FUND_WORDS = {"etf", "fund", "trust", "index", "portfolio", "etn"}

#: Name words too common to identify a company on their own.
_GENERIC_NAME_WORDS = {
    "energy", "power", "electric", "technologies", "technology", "tech",
    "group", "financial", "financials", "international", "systems",
    "semiconductor", "semiconductors", "manufacturing", "computing",
    "holdings", "holding", "bank", "bancorp", "pharmaceuticals", "pharma",
    "communications", "industries", "global", "capital", "partners",
    "resources", "solutions", "services", "therapeutics", "brands",
    "networks", "software", "entertainment", "motors", "motor", "general",
    "american", "america", "national", "united", "first", "trust", "fund",
    "index", "health", "healthcare", "medical", "sciences", "biosciences",
    "labs", "laboratories", "markets", "investments", "realty",
    "properties", "airlines", "platforms", "digital", "data", "enterprises",
    "products", "materials", "chemical", "chemicals", "petroleum", "mining",
    "metals", "steel", "foods", "retail", "stores", "auto", "automotive",
    "insurance", "equity", "income", "growth", "shares", "etf", "spdr",
    "ishares", "vanguard", "invesco", "select", "sector", "walt", "the",
    "and", "of", "new", "north", "south", "west", "east", "world", "street",
    "state",
}


def name_core(name: str) -> str:
    """"NRG Energy, Inc." -> "NRG Energy"; "The Walt Disney Company" ->
    "Walt Disney". Legal suffixes, share classes and a leading "The" go."""
    s = _SHARE_CLASS.sub("", (name or "").replace(",", " ").strip())
    s = _CONTRACT_MONTH.sub("", s)
    words = s.split()
    if words and words[0].lower() == "the":
        words = words[1:]
    while words and words[-1].lower().rstrip(".") in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _ticker_base(symbol: str) -> str:
    """"PETR4.SA" -> "PETR4", "^TNX" -> "TNX", "EURUSD=X" -> "EURUSD"."""
    base = symbol.lstrip("^").split("=")[0]
    return base.split(".")[0] if "." in base and not base.startswith(".") else base


def _ticker_pattern(symbol: str) -> re.Pattern:
    base = _ticker_base(symbol)
    # BRK-B is written BRK.B / BRK/B / BRK-B in headlines
    body = r"[.\-/]".join(re.escape(p) for p in base.split("-"))
    return re.compile(rf"(?<![A-Za-z0-9])\$?{body}(?![A-Za-z0-9])")


def _name_patterns(name: str) -> list[re.Pattern]:
    core = name_core(name)
    if not core:
        return []
    terms = [core]
    if _FUND_WORDS & {w.lower() for w in core.split()}:
        return [re.compile(rf"(?<!\w){re.escape(core)}(?!\w)", re.IGNORECASE)]
    terms += [
        w for w in core.split()
        if len(w) >= 4 and w.lower().strip(".&'") not in _GENERIC_NAME_WORDS
    ]
    return [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE) for t in terms]


def is_relevant(item: dict, symbol: str, name: str) -> bool:
    """Whether a headline is about ``symbol``: the ticker (case-sensitive,
    whole word) or a distinctive part of the company name in the title, or
    an exchange-style mention ("(NYSE: NRG)", "$NRG") in the summary. A
    plain ticker in the summary doesn't count — that is exactly the
    "at NRG Stadium" false positive."""
    title = item.get("title") or ""
    # "GC=F", "^TNX", "EURUSD=X" aren't written in headlines, and their bases
    # collide with ordinary text ("GC" = gastric cancer): match the name only.
    tradable_word = not any(c in symbol for c in "=^")
    if tradable_word and _ticker_pattern(symbol).search(title):
        return True
    if any(p.search(title) for p in _name_patterns(name)):
        return True
    if not tradable_word:
        return False
    base = re.escape(_ticker_base(symbol))
    summary = item.get("summary") or ""
    return bool(
        re.search(rf"(?:\b[A-Z]{{2,8}}\s*:\s*|\$|\(){base}\b", summary)
    )


_name_cache: dict[str, str] = {}


def company_name(symbol: str) -> str:
    """Best-effort display name for ``symbol`` ('' when unknown). Prefers the
    quote topic most panels already hold; otherwise one Yahoo search call,
    cached for the session."""
    if symbol in _name_cache:
        return _name_cache[symbol]
    name = ""
    try:
        quote = DataHub.instance().peek(f"quote:{symbol}")
        if isinstance(quote, dict) and quote.get("name") and quote["name"] != symbol:
            name = str(quote["name"])
    except Exception:
        pass
    if not name:
        try:
            from ._yf import RATE_LIMIT_GATE

            if not RATE_LIMIT_GATE.blocked():
                import yfinance as yf

                for q in yf.Search(symbol, max_results=5, news_count=0, timeout=6).quotes or []:
                    if str(q.get("symbol", "")).upper() == symbol.upper():
                        name = q.get("longname") or q.get("shortname") or ""
                        break
        except Exception:
            name = ""
    if name:  # a throttled/failed lookup must not stick for the whole session
        _name_cache[symbol] = name
    return name


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
        return ["news:*", "newsq:*", "newsr:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            if topic.startswith("newsq:"):
                query = topic.split(":", 1)[1]
                hub.run_async(lambda t=topic, q=query: self._fetch_query(t, q))
                continue
            if topic.startswith("newsr:"):
                parts = topic.split(":")
                if len(parts) != 3 or ".." not in parts[2]:
                    hub.publish_error(topic, f"malformed ranged news topic: {topic}")
                    continue
                start, _, end = parts[2].partition("..")
                hub.run_async(
                    lambda t=topic, s=parts[1], a=start, b=end: self._fetch_ranged(
                        t, s, a, b
                    )
                )
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
            hub.publish(topic, self._gate(self._symbol_items(symbol, langs), langs))
        except Exception as exc:
            hub.publish_error(topic, f"news fetch failed: {exc}")

    def _symbol_items(self, symbol: str, langs: list[str]) -> list[dict]:
        """Walk the sources in order, keeping only headlines about ``symbol``
        and topping up from the next source until there are enough.

        The old rule — first source to return *anything* wins — let one
        loose NewsAPI match set (NRG -> NRG Stadium -> Texans coverage) mask
        a Google News result that was entirely on topic. If nothing at all
        passes the relevance check (an ETF or index whose headlines rarely
        name it), the first non-empty source is shown as before rather than
        an empty panel.
        """
        name = company_name(symbol)
        core = name_core(name)
        # Finance terms keep "Netflix" from meaning its shows' premieres.
        subject = f'("{core}" OR {_ticker_base(symbol)})' if core else symbol
        newsapi_q = f"{subject} AND (stock OR shares OR earnings OR analyst OR investors)"
        sources = (
            lambda: self._from_rss_symbol(symbol, langs),
            lambda: self._from_newsapi(newsapi_q, langs),
            lambda: self._from_gnews(symbol, langs),
            lambda: self._from_yfinance(symbol),
        )
        kept: list[dict] = []
        seen: set[str] = set()
        fallback: Optional[list[dict]] = None
        for fetch in sources:
            items = fetch()
            if not items:
                continue
            if fallback is None:
                fallback = items
            for item in items:
                key = re.sub(r"\W+", " ", (item.get("title") or "").lower()).strip()
                if key in seen or not is_relevant(item, symbol, name):
                    continue
                seen.add(key)
                kept.append(item)
            if len(kept) >= RELEVANT_ENOUGH:
                break
        if not kept:
            return fallback or []
        return _sort_by_published(kept)

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

    def _fetch_ranged(self, topic: str, symbol: str, start: str, end: str) -> None:
        """Historical waterfall for ``newsr:SYM:START..END``.

        The order is deliberately **not** the live one. gnews goes first
        because it is the only tier with real archive depth -- Google News
        honours ``before:``/``after:`` operators years back, verified to 2019.
        NewsAPI is second but only inside its free-tier lookback. RSS is a
        recency top-up, post-filtered on the parsed publish date, and useless
        for anything older than a few days. yfinance is absent entirely:
        ``Ticker.news`` has no date control at all, so including it would
        silently return today's headlines for a 2019 query -- the single worst
        failure this feature could have.
        """
        hub = DataHub.instance()
        try:
            langs = languages.spoken_languages()
            items = self._from_gnews_ranged(symbol, start, end, langs)
            if items is None and _within_newsapi_window(start):
                items = self._from_newsapi(symbol, langs, start=start, end=end)
            if items is None:
                items = self._rss_in_range(symbol, start, end, langs)
            if items is None:
                items = []
            kept = self._gate(items, langs)
            hub.publish(
                topic,
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "items": kept,
                    # so the panel can say "12 hidden by your reading languages"
                    # instead of showing a bare, baffling empty state
                    "found": len(items),
                    "hidden": max(0, len(items) - len(kept)),
                },
            )
        except Exception as exc:
            hub.publish_error(topic, f"ranged news fetch failed: {exc}")

    def _from_gnews_ranged(
        self, symbol: str, start: str, end: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        return self._gnews_search(f'"{symbol}" stock', langs, start=start, end=end)

    def _rss_in_range(
        self, symbol: str, start: str, end: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Whatever the live feeds happen to still be carrying, filtered to the
        window. Only ever useful when the window touches the last few days."""
        items = self._from_rss_symbol(symbol, langs)
        if not items:
            return None
        kept = [i for i in items if _published_within(i.get("published"), start, end)]
        return kept or None

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
        self,
        symbol: str,
        langs: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
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
            params = {
                "q": symbol,
                "language": code,
                "sortBy": "publishedAt",
                "pageSize": MAX_ITEMS,
            }
            if start and end:
                # /v2/everything takes ISO-8601; the free tier only indexes
                # roughly the last month, which _within_newsapi_window guards
                params["from"], params["to"] = start, end
            try:
                resp = requests.get(
                    "https://newsapi.org/v2/everything",
                    params=params,
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
        query: str,
        langs: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> Optional[list[dict]]:
        """One gnews edition per language, merged newest-first. GNews defaults
        to the US/English edition, so the language/country pair is what makes
        the other languages show up at all.

        ``start``/``end`` (ISO dates) turn this into a historical search:
        gnews translates them into Google News ``after:``/``before:``
        operators (gnews.py:93-109), which reach back years."""
        try:
            from gnews import GNews
        except Exception:
            return None
        codes = langs if langs is not None else languages.spoken_languages()
        window = _date_tuple(start), _date_tuple(end)
        items: list[dict] = []
        for code in codes[:MAX_LANGS_PER_FETCH]:
            try:
                gn = GNews(
                    language=code,
                    country=GNEWS_COUNTRY.get(code, "US"),
                    max_results=MAX_ITEMS,
                )
                if window[0] and window[1]:
                    gn.start_date, gn.end_date = window
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
