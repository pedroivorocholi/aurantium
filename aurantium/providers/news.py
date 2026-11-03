"""News provider: ``news:SYM`` and ``newsq:QUERY`` from merged sources.

Live news asks every fast source at once — publisher RSS feeds (Fincept's
default-feed approach, free and keyless), Finnhub (if FINNHUB_API_KEY is set),
Google News via the gnews package, and yfinance for symbols — then merges the
results: off-topic items dropped, duplicates collapsed, anything older than
``FRESH_DAYS`` dropped while enough fresher items exist, newest first.

NewsAPI.org only tops up a thin result. Its free tier serves articles 24 hours
late and matches words anywhere in an article body, so when it ranked ahead of
Google News (as it once did) a topic like "Brazil" showed day-old football
coverage and every panel's top story was a day or two old.

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
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from xml.etree import ElementTree

import requests

from .. import languages
from .. import portfolio_news as pn
from ..datahub import DataHub, Provider

#: Headlines per panel, and per source request. 25 covered only the last
#: 24 minutes of "markets" on a busy day; 75 shows hours. NewsAPI's pageSize
#: caps at 100, as does a Google News feed.
MAX_ITEMS = 75

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

#: Live headlines older than this give way to fresher ones...
FRESH_DAYS = 7
#: ...as long as at least this many fresh ones exist; below it (a quiet name)
#: the window widens to MAX_AGE_DAYS, and NewsAPI is asked to top up.
MIN_FRESH = 8
#: Never shown as live news, however quiet the name: Google News will happily
#: return a 2017 quote page for a thin query.
MAX_AGE_DAYS = 30

#: Topic queries that name a Finnhub market-news category.
FINNHUB_CATEGORIES = {
    "markets": "general", "market": "general", "general": "general",
    "forex": "forex", "fx": "forex", "crypto": "crypto",
    "mergers": "merger", "m&a": "merger",
}
#: Finnhub's free company-news endpoint covers North American listings only;
#: suffixed symbols (PETR4.SA) get a 403, so they aren't even asked.
_FINNHUB_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,6}(?:-[A-Z])?$")
_RSS_HEADERS = {"User-Agent": "Mozilla/5.0 (aurantium RSS reader)"}

# Cache keyed by the active language set, so changing languages in Settings
# invalidates it implicitly — no cross-wiring from the dialog needed.
_rss_lock = threading.Lock()
_rss_cache: dict[tuple[str, ...], dict[str, Any]] = {}
#: Portfolio macro headlines are the same for every book: one fetch per
#: language set, kept for MACRO_CACHE_TTL (the newsp: policy TTL).
MACRO_CACHE_TTL = 900.0
_macro_lock = threading.Lock()
_macro_cache: dict[tuple[str, ...], dict[str, Any]] = {}

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
    # Not glued to another symbol: for F, "(KC=F)" and "(639.F)" are other
    # instruments' quote pages, and "U.S." is not ticker S.
    return re.compile(
        rf"(?<![A-Za-z0-9.=^])\$?{body}(?![A-Za-z0-9=]|\.[A-Za-z0-9])"
    )


def name_aliases(name: str) -> list[str]:
    """Every name a company goes by in ``name``: Yahoo writes some as
    "Petróleo Brasileiro S.A. - Petrobras", and headlines use either half."""
    out: list[str] = []
    for part in (name or "").split(" - "):
        core = name_core(part)
        if core and core.lower() not in {o.lower() for o in out}:
            out.append(core)
    return out


def _name_patterns(name: str) -> list[re.Pattern]:
    patterns: list[re.Pattern] = []
    for core in name_aliases(name):
        terms = [core]
        if not _FUND_WORDS & {w.lower() for w in core.split()}:
            terms += [
                w for w in core.split()
                if len(w) >= 4 and w.lower().strip(".&'") not in _GENERIC_NAME_WORDS
            ]
        patterns += [
            re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE) for t in terms
        ]
    return patterns


def symbol_search_query(symbol: str, name: str) -> str:
    """The Google News query for ``symbol``'s headlines.

    Search by name: a one- or two-letter ticker is noise to a search engine
    (``"F" stock`` returned quote pages for KC=F and 639.F), so it only joins
    the query from three letters up. Futures and indices ("GC=F", "^TNX") are
    searched by name alone, without "stock"."""
    tradable = not any(c in symbol for c in "=^")
    base = _ticker_base(symbol)
    terms = [f'"{a}"' for a in name_aliases(name)]
    if not terms or (tradable and len(base) >= 3):
        terms.append(f'"{base}"')
    body = terms[0] if len(terms) == 1 else f"({' OR '.join(terms)})"
    # bare "Gold" found Gold Glove awards
    return f"{body} stock" if tradable else f"{body} (price OR futures OR market)"


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


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
#: A 100-item search feed (~140 KB) sometimes takes 8 s to arrive; the 6 s
#: publisher-feed timeout silently dropped it, leaving RSS's handful of items.
GOOGLE_NEWS_TIMEOUT = 15.0


def _google_news_rss(query: str, code: str, period: Optional[str]) -> list[dict]:
    """One Google News search feed, read directly: a single request.

    The gnews package fetches the same feed, then spends one more HTTP request
    per article trying to resolve Google's redirect link — which, without
    playwright installed, hands the same news.google.com link back. 25
    pointless round trips took ~20 s per language. Links stay Google redirects;
    the browser follows them, and the preview resolves one on click."""
    country = GNEWS_COUNTRY.get(code, "US")
    q = f"{query} when:{period}" if period else query
    try:
        resp = requests.get(
            GOOGLE_NEWS_RSS,
            params={"q": q, "hl": code, "gl": country, "ceid": f"{country}:{code}"},
            headers=_RSS_HEADERS,
            timeout=GOOGLE_NEWS_TIMEOUT,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)
    except Exception:
        return []
    items: list[dict] = []
    for node in root.iter("item"):
        publisher = (node.findtext("source") or "").strip()
        title = _strip_publisher((node.findtext("title") or "").strip(), publisher)
        if not title:
            continue
        # the description is only "<a>headline</a> publisher" again
        summary = _clean_summary(node.findtext("description"))
        if summary.casefold().startswith(title.casefold()):
            summary = ""
        items.append(
            {
                "title": title,
                "publisher": publisher,
                "url": (node.findtext("link") or "").strip(),
                "published": (node.findtext("pubDate") or "").strip(),
                "summary": summary,
                "lang": code,
            }
        )
        if len(items) >= MAX_ITEMS:
            break
    return items


def _strip_publisher(title: str, publisher: str) -> str:
    """Google News appends " - Publisher" to every headline; the panel shows
    the publisher separately, and the suffix defeats de-duplication."""
    suffix = f" - {publisher}" if publisher else ""
    if suffix and title.endswith(suffix):
        return title[: -len(suffix)].rstrip()
    return title


def _dedupe_key(item: dict) -> str:
    title = _strip_publisher(item.get("title") or "", item.get("publisher") or "")
    return re.sub(r"\W+", " ", title.lower()).strip()


def _age_days(item: dict, now: float) -> Optional[float]:
    dt = _parse_feed_datetime(item.get("published") or "")
    return (now - dt.timestamp()) / 86400 if dt else None


def _fresh_count(items: list[dict]) -> int:
    now = time.time()
    return sum(
        1 for i in items
        if (age := _age_days(i, now)) is not None and age <= FRESH_DAYS
    )


def _freshest(items: list[dict]) -> list[dict]:
    """Newest first, trimmed by age: only the last ``FRESH_DAYS`` when at
    least ``MIN_FRESH`` such items exist, else the last ``MAX_AGE_DAYS``
    (undated items kept, at the bottom). Nothing older is ever live news."""
    now = time.time()
    fresh, recent = [], []
    for item in items:
        age = _age_days(item, now)
        if age is not None and age <= FRESH_DAYS:
            fresh.append(item)
        if age is None or age <= MAX_AGE_DAYS:
            recent.append(item)
    return _sort_by_published(fresh if len(fresh) >= MIN_FRESH else recent)


def _gather(sources) -> list[list[dict]]:
    """Run every source concurrently; a source that raises or returns None
    contributes nothing. Results follow the order of ``sources``."""
    def run(fetch):
        try:
            return fetch() or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        return list(pool.map(run, sources))


#: Google News indexes Yahoo/Moomoo quote and option-chain pages as if they
#: were articles ("NRG Nov 2026 120.000 call (...) Stock Price, News, Quote &
#: History"). They name the symbol, so relevance alone lets them through.
_QUOTE_PAGE = re.compile(
    r"stock price,? news,? quote|quote & history|interactive stock chart"
    r"|options chain|historical prices|seasonal returns chart"
    r"|stock forecast & analyst ratings|analyst ratings, stock forecast",
    re.IGNORECASE,
)


def _merge(batches, keep=lambda item: True, seen: Optional[set] = None) -> list[dict]:
    """Flatten ``batches``, dropping quote pages, items ``keep`` rejects and
    repeats of a headline already seen (earlier batches win)."""
    seen = set() if seen is None else seen
    out: list[dict] = []
    for batch in batches:
        for item in batch:
            key = _dedupe_key(item)
            if (
                not key or key in seen
                or _QUOTE_PAGE.search(item.get("title") or "")
                or not keep(item)
            ):
                continue
            seen.add(key)
            out.append(item)
    return out


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
        return ["news:*", "newsq:*", "newsr:*", "newsp:*"]

    def refresh(self, topics: list[str]) -> None:
        hub = DataHub.instance()
        for topic in topics:
            if topic.startswith("newsq:"):
                query = topic.split(":", 1)[1]
                hub.run_async(lambda t=topic, q=query: self._fetch_query(t, q))
                continue
            if topic.startswith("newsp:"):
                book = topic.split(":", 1)[1]
                hub.run_async(lambda t=topic, b=book: self._fetch_portfolio(t, b))
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

    def _symbol_items(
        self, symbol: str, langs: list[str], newsapi: bool = True
    ) -> list[dict]:
        """Every fast source at once, merged: only headlines about ``symbol``,
        duplicates collapsed, newest first, trimmed by age (``_freshest``).
        NewsAPI tops up only when that leaves fewer than ``MIN_FRESH`` (and
        ``newsapi`` is true).

        If nothing at all passes the relevance check (an ETF or index whose
        headlines rarely name it), the first non-empty source is shown rather
        than an empty panel — still trimmed by age.
        """
        name = company_name(symbol)
        batches = _gather((
            lambda: self._from_finnhub_company(symbol, langs),
            lambda: self._from_gnews(symbol, langs, symbol_search_query(symbol, name)),
            lambda: self._from_rss_symbol(symbol, langs, name),
            lambda: self._from_yfinance(symbol),
        ))

        def relevant(item: dict) -> bool:
            return is_relevant(item, symbol, name)

        seen: set[str] = set()
        kept = _merge(batches, relevant, seen)
        if newsapi and _fresh_count(kept) < MIN_FRESH:
            aliases = name_aliases(name)
            # Finance terms keep "Netflix" from meaning its shows' premieres.
            subject = (
                f'("{aliases[0]}" OR {_ticker_base(symbol)})' if aliases else symbol
            )
            q = f"{subject} AND (stock OR shares OR earnings OR analyst OR investors)"
            kept += _merge([self._from_newsapi(q, langs) or []], relevant, seen)
        if not kept:
            kept = next((b for b in batches if b), [])
        return _freshest(kept)

    def _query_items(self, query: str, langs: list[str]) -> list[dict]:
        """Fast free-text sources only (RSS + Google News); never NewsAPI."""
        batches = _gather((
            lambda: self._from_rss_query(query, langs),
            lambda: self._from_gnews_query(query, langs),
        ))
        return _merge(batches)

    def _macro_items(self, langs: list[str]) -> dict[str, list[dict]]:
        """theme -> items, shared by every book, cached per language set."""
        key = tuple(sorted(langs))
        with _macro_lock:
            hit = _macro_cache.get(key)
            if hit and time.monotonic() - hit["ts"] < MACRO_CACHE_TTL:
                return hit["items"]
        res = _gather([lambda q=q: self._query_items(q, langs) for _, q in pn.MACRO_QUERIES])
        items = {theme: r for (theme, _), r in zip(pn.MACRO_QUERIES, res)}
        with _macro_lock:
            _macro_cache.clear()  # only the active language set is worth keeping
            _macro_cache[key] = {"ts": time.monotonic(), "items": items}
        return items

    def _fetch_portfolio(self, topic: str, book_text: str) -> None:
        """``newsp:<book>``: headlines for the held tickers, the sectors the
        book is heavy in, and macro themes, scored and sorted best first.
        A headline in several layers keeps its highest-scoring one."""
        hub = DataHub.instance()
        try:
            book = pn.decode_book(book_text)
            if not book:
                hub.publish(topic, [])
                return
            langs = languages.spoken_languages()
            held = sorted(book, key=lambda h: -h["weight"])[: pn.HOLDINGS_CAP]
            sweights = pn.sector_weights(book)
            sectors = {
                s: w for s, w in sweights.items()
                if w >= pn.SECTOR_MIN_WEIGHT and s in pn.SECTOR_QUERIES
            }
            hold_res = _gather([
                lambda s=h["symbol"]: self._symbol_items(s, langs, newsapi=False)
                for h in held
            ])
            sec_res = _gather([
                lambda s=s: self._query_items(pn.SECTOR_QUERIES[s], langs) for s in sectors
            ]) if sectors else []
            macro = self._macro_items(langs)

            now = time.time()
            cands: list[dict] = []

            def add(layer, key, weight, items, symbol=None):
                for it in items:
                    age = _age_days(it, now)
                    row = dict(
                        it,
                        reason=pn.reason_for(layer, key, weight),
                        score=pn.score_item(layer, weight, None if age is None else age * 24),
                    )
                    if symbol:
                        row["symbol"] = symbol
                    cands.append(row)

            for h, items in zip(held, hold_res):
                add("holding", h["symbol"], h["weight"], items, h["symbol"])
            for (s, w), items in zip(sectors.items(), sec_res):
                add("sector", s, w, items)
            for theme, items in macro.items():
                add("macro", theme, pn.macro_weight(theme, sweights), items)

            cands.sort(key=lambda r: -r["score"])
            kept = _merge([cands])  # shared seen set: first (best) copy wins
            # score order *before* the gate, so its cap keeps the best 75
            fresh = sorted(_freshest(kept), key=lambda r: -r["score"])
            rows = self._gate(fresh, langs)
            hub.publish(topic, rows)
        except Exception as exc:
            hub.publish_error(topic, f"portfolio news fetch failed: {exc}")

    def _fetch_query(self, topic: str, query: str) -> None:
        """Free-text query: RSS, Finnhub (for a market category) and Google
        News at once, merged newest first; NewsAPI only tops up. No yfinance
        — ``Ticker.news`` is symbol-only, not a text search."""
        hub = DataHub.instance()
        try:
            langs = languages.spoken_languages()
            batches = _gather((
                lambda: self._from_rss_query(query, langs),
                lambda: self._from_finnhub_general(query, langs),
                lambda: self._from_gnews_query(query, langs),
            ))
            seen: set[str] = set()
            items = _merge(batches, seen=seen)
            if _fresh_count(items) < MIN_FRESH:
                items += _merge([self._from_newsapi(query, langs) or []], seen=seen)
            hub.publish(topic, self._gate(_freshest(items), langs))
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
        self, symbol: str, langs: Optional[list[str]] = None, name: str = ""
    ) -> Optional[list[dict]]:
        """Feed headlines about ``symbol`` (see ``is_relevant``); None when
        there are none."""
        try:
            matches = [i for i in _rss_items(langs) if is_relevant(i, symbol, name)]
            return self._strip_category(matches) or None
        except Exception:
            return None

    def _from_rss_query(
        self, query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Headlines where every query word appears as a word in the title
        (plurals allowed: "price" finds "prices", but "ai" no longer finds
        "said"), or where the query names a feed category (the Topic News
        default 'markets' pulls the whole markets feed set)."""
        try:
            tokens = [t for t in query.lower().split() if t]
            if not tokens:
                return None
            patterns = [
                re.compile(rf"(?<!\w){re.escape(t)}(?:s|es)?(?!\w)", re.IGNORECASE)
                for t in tokens
            ]
            matches = [
                item for item in _rss_items(langs)
                if query.lower() == item["category"]
                or all(p.search(item["title"]) for p in patterns)
            ]
            return self._strip_category(matches) or None
        except Exception:
            return None

    def _from_finnhub_company(
        self, symbol: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Finnhub ``/company-news`` for the last ``FRESH_DAYS``: near real
        time, but it tags peers' stories with the symbol too, so the merge's
        relevance check still applies. English only."""
        api_key = os.environ.get("FINNHUB_API_KEY")
        codes = langs if langs is not None else languages.spoken_languages()
        if not api_key or "en" not in codes or not _FINNHUB_SYMBOL.match(symbol):
            return None
        today = date.today()
        return self._finnhub(
            "company-news",
            {
                "symbol": symbol.replace("-", "."),
                "from": (today - timedelta(days=FRESH_DAYS)).isoformat(),
                "to": today.isoformat(),
            },
            api_key,
        )

    def _from_finnhub_general(
        self, query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Finnhub ``/news`` when the topic names one of its categories
        ("markets", "forex", "crypto", "mergers"). English only."""
        api_key = os.environ.get("FINNHUB_API_KEY")
        codes = langs if langs is not None else languages.spoken_languages()
        category = FINNHUB_CATEGORIES.get(query.strip().lower())
        if not api_key or "en" not in codes or not category:
            return None
        return self._finnhub("news", {"category": category}, api_key)

    @staticmethod
    def _finnhub(path: str, params: dict, api_key: str) -> Optional[list[dict]]:
        try:
            resp = requests.get(
                f"https://finnhub.io/api/v1/{path}",
                params=params,
                headers={"X-Finnhub-Token": api_key},
                timeout=8,
            )
            resp.raise_for_status()
            articles = resp.json()
        except Exception:
            return None
        items = []
        for a in articles if isinstance(articles, list) else []:
            try:
                published = datetime.fromtimestamp(
                    int(a.get("datetime")), tz=timezone.utc
                ).isoformat()
            except (TypeError, ValueError, OSError):
                published = ""
            items.append(
                {
                    "title": a.get("headline") or "",
                    "publisher": a.get("source") or "",
                    "url": a.get("url") or "",
                    "published": published,
                    "summary": _clean_summary(a.get("summary")),
                    "lang": "en",
                }
            )
        return items or None

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
        self, symbol: str, langs: Optional[list[str]] = None, query: str = ""
    ) -> Optional[list[dict]]:
        q = query or symbol_search_query(symbol, company_name(symbol))
        return self._gnews_search(q, langs, period=f"{FRESH_DAYS}d")

    def _from_gnews_query(
        self, query: str, langs: Optional[list[str]] = None
    ) -> Optional[list[dict]]:
        """Like ``_from_gnews`` but for free-text queries: no stock-ticker
        decoration around the search string."""
        return self._gnews_search(query, langs, period=f"{FRESH_DAYS}d")

    @staticmethod
    def _gnews_search(
        query: str,
        langs: Optional[list[str]] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        period: Optional[str] = None,
    ) -> Optional[list[dict]]:
        """One gnews edition per language, merged newest-first. GNews defaults
        to the US/English edition, so the language/country pair is what makes
        the other languages show up at all.

        ``start``/``end`` (ISO dates) turn this into a historical search:
        gnews translates them into Google News ``after:``/``before:``
        operators (gnews.py:93-109), which reach back years. ``period``
        ("7d") is Google's ``when:`` window, for live searches — without it a
        thin query returns pages years old."""
        codes = langs if langs is not None else languages.spoken_languages()
        window = _date_tuple(start), _date_tuple(end)
        if not (window[0] and window[1]):
            # A week's window alone is ranked by relevance and leaves gaps of
            # days at the top; today's alone is empty for quiet topics. Ask
            # for both — each is a single cheap request.
            periods = ["1d", period] if period and period != "1d" else [period]
            jobs = [(code, p) for code in codes[:MAX_LANGS_PER_FETCH] for p in periods]
            batches = _gather([
                lambda c=code, p=p: _google_news_rss(query, c, p) for code, p in jobs
            ])
            items = _merge(batches)
            return _sort_by_published(items) if items else None
        try:
            from gnews import GNews
        except Exception:
            return None
        items: list[dict] = []
        for code in codes[:MAX_LANGS_PER_FETCH]:
            try:
                gn = GNews(
                    language=code,
                    country=GNEWS_COUNTRY.get(code, "US"),
                    max_results=MAX_ITEMS,
                )
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
                        "title": _strip_publisher(r.get("title") or "", publisher or ""),
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
