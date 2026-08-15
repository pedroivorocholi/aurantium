"""Reading-language preferences and the language gate applied to news.

The user picks a **main language** plus any others they read (first run, and
anytime from ``Settings ▸ News Languages…``). Everything downstream keys off
:func:`spoken_languages`:

* ``providers/news.py`` asks each source for those languages only — per-language
  RSS feeds, NewsAPI ``language=``, gnews ``language=``/``country=``.
* Whatever still slips through is dropped by :func:`accepts_text`, a two-stage
  gate: an absolute Unicode-script check, then a *conservative* statistical
  check via ``langdetect``.

Why conservative: headlines are short, and langdetect is correspondingly shaky
on them ("Nvidia shares jump on strong AI demand" scores en 0.57 / id 0.29). A
gate that rejected on a weak top guess would silently hide real stories, which
is a far worse failure than letting one stray headline through. So a headline is
rejected only when the detector is *confident* (:data:`_REJECT_CONFIDENCE`) and
no accepted language appears anywhere in its candidate list.
"""

from __future__ import annotations

import threading
from typing import Iterable, Optional

from PySide6.QtCore import QLocale, QSettings

#: QSettings keys
MAIN_KEY = "news/mainLanguage"
OTHERS_KEY = "news/languages"
#: set once the user has been through the picker (first-run gate)
CHOSEN_KEY = "news/languagesChosen"

#: Reject a headline only when the detector is at least this sure of a language
#: we don't accept. Deliberately high — see the module docstring.
_REJECT_CONFIDENCE = 0.90
#: ...and only when no accepted language even appears as a candidate this far up.
_ACCEPT_FLOOR = 0.05
#: Below this many characters, langdetect is noise; script check only.
_MIN_DETECT_CHARS = 20
#: Share of a headline that may sit in a script the user doesn't read before it
#: is rejected. Tuned against live feeds — see :func:`script_accepted`.
_FOREIGN_SCRIPT_RATIO = 0.15


# -- the offered languages -------------------------------------------------
#
# Restricted to languages at least one news source actually delivers: every
# entry below has a market-news RSS feed in ``providers/news.py`` and is
# supported by gnews. (code, endonym, English name, script)
LANGUAGES: list[tuple[str, str, str, str]] = [
    ("en", "English", "English", "latin"),
    ("es", "Español", "Spanish", "latin"),
    ("pt", "Português", "Portuguese", "latin"),
    ("fr", "Français", "French", "latin"),
    ("de", "Deutsch", "German", "latin"),
    ("it", "Italiano", "Italian", "latin"),
    ("nl", "Nederlands", "Dutch", "latin"),
    ("sv", "Svenska", "Swedish", "latin"),
    ("pl", "Polski", "Polish", "latin"),
    ("tr", "Türkçe", "Turkish", "latin"),
    ("ru", "Русский", "Russian", "cyrillic"),
    ("el", "Ελληνικά", "Greek", "greek"),
    ("ar", "العربية", "Arabic", "arabic"),
    ("he", "עברית", "Hebrew", "hebrew"),
    ("hi", "हिन्दी", "Hindi", "devanagari"),
    ("ja", "日本語", "Japanese", "japanese"),
    ("ko", "한국어", "Korean", "hangul"),
    ("zh", "中文", "Chinese", "han"),
]

#: code -> (endonym, English name, script)
_BY_CODE = {code: (endo, eng, script) for code, endo, eng, script in LANGUAGES}

DEFAULT_LANGUAGE = "en"

#: Scripts a language can legitimately appear in. Japanese mixes kana with Han,
#: so Han text alone is ambiguous between zh and ja — both accept it.
_LANG_SCRIPTS: dict[str, set[str]] = {
    "ja": {"japanese", "han", "latin"},
    "zh": {"han", "latin"},
    "ko": {"hangul", "han", "latin"},
}


def scripts_for(code: str) -> set[str]:
    """Unicode scripts headlines in ``code`` may legitimately use. Latin is
    always allowed — every language quotes tickers, company names and numbers
    in Latin characters."""
    if code in _LANG_SCRIPTS:
        return _LANG_SCRIPTS[code]
    entry = _BY_CODE.get(code)
    return {entry[2], "latin"} if entry else {"latin"}


def endonym(code: str) -> str:
    """The language's name in itself, e.g. ``pt`` -> ``Português``."""
    entry = _BY_CODE.get(code)
    return entry[0] if entry else code


def english_name(code: str) -> str:
    entry = _BY_CODE.get(code)
    return entry[1] if entry else code


def label(code: str) -> str:
    """Picker label: endonym plus the English name when they differ."""
    endo, eng = endonym(code), english_name(code)
    return endo if endo == eng else f"{endo} · {eng}"


# -- stored preference -----------------------------------------------------


def system_language() -> str:
    """The OS UI language if we offer it, else English. Used as the default
    before the user has been through the picker, so a fresh install behaves
    sensibly on its very first news fetch."""
    try:
        code = QLocale.system().name().split("_")[0].lower()
    except Exception:
        return DEFAULT_LANGUAGE
    return code if code in _BY_CODE else DEFAULT_LANGUAGE


def main_language() -> str:
    stored = str(QSettings().value(MAIN_KEY, "", type=str) or "")
    return stored if stored in _BY_CODE else system_language()


def spoken_languages() -> list[str]:
    """Every language the user reads, **main first**. Never empty — an empty
    stored list falls back to the main language alone, so a user who unticks
    everything still gets news rather than a blank panel."""
    main = main_language()
    raw = QSettings().value(OTHERS_KEY, [], type=list) or []
    out = [main]
    for code in raw:
        code = str(code)
        if code in _BY_CODE and code not in out:
            out.append(code)
    return out


def has_chosen() -> bool:
    """True once the user has been shown the picker (first-run gate)."""
    return bool(QSettings().value(CHOSEN_KEY, False, type=bool))


def set_languages(main: str, others: Iterable[str]) -> None:
    """Persist the choice. ``others`` may include ``main``; it's de-duplicated."""
    main = main if main in _BY_CODE else DEFAULT_LANGUAGE
    extra = [c for c in dict.fromkeys(str(o) for o in others) if c in _BY_CODE and c != main]
    settings = QSettings()
    settings.setValue(MAIN_KEY, main)
    settings.setValue(OTHERS_KEY, extra)
    settings.setValue(CHOSEN_KEY, True)


# -- stage 1: Unicode script ------------------------------------------------

#: (script name, inclusive codepoint ranges)
_SCRIPT_RANGES: list[tuple[str, tuple[tuple[int, int], ...]]] = [
    ("latin", ((0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F))),
    ("greek", ((0x370, 0x3FF), (0x1F00, 0x1FFF))),
    ("cyrillic", ((0x400, 0x4FF), (0x500, 0x52F))),
    ("hebrew", ((0x590, 0x5FF),)),
    ("arabic", ((0x600, 0x6FF), (0x750, 0x77F), (0xFB50, 0xFDFF))),
    ("devanagari", ((0x900, 0x97F),)),
    ("thai", ((0xE00, 0xE7F),)),
    ("japanese", ((0x3040, 0x309F), (0x30A0, 0x30FF))),  # kana: uniquely Japanese
    ("hangul", ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))),
    ("han", ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF))),
]


def _script_of(ch: str) -> Optional[str]:
    cp = ord(ch)
    for name, ranges in _SCRIPT_RANGES:
        for lo, hi in ranges:
            if lo <= cp <= hi:
                return name
    return None  # digits, punctuation, symbols — carry no language signal


def script_counts(text: str) -> dict[str, int]:
    """How many characters of each script ``text`` contains, ignoring digits,
    punctuation and symbols (which carry no language signal)."""
    counts: dict[str, int] = {}
    for ch in text:
        script = _script_of(ch)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    return counts


def scripts_in(text: str) -> set[str]:
    """Scripts present in ``text``."""
    return set(script_counts(text))


#: Scripts that identify a language outright wherever they appear. Kana is used
#: in Japanese and nowhere else; Hangul likewise for Korean. Han is deliberately
#: absent — it is shared by Chinese, Japanese and Korean and proves nothing.
_DECISIVE = {"japanese", "hangul"}


def script_accepted(text: str, langs: Iterable[str]) -> bool:
    """False when ``text`` is *written in* a script none of ``langs`` uses.

    This is the absolute half of the gate and the one that kills the original
    complaint: with Chinese unselected, a Han-script headline can never pass,
    whatever a source claims about it.

    The rule is proportion, and the threshold is low on purpose. Two obvious
    rules both fail on real market headlines:

    * *Presence* — reject on any foreign character — would drop
      "Alibaba (阿里巴巴) shares rise", an English headline quoting a company's
      name.
    * *Dominance* — reject only when the foreign script wins the character
      count — lets "Magnetar Financial出售CoreWeave约3,340万美元股票" through,
      because tickers, company names and figures leave it with more Latin
      characters than Han. Measured against the live feeds, dominance let four
      Chinese headlines reach an English-only reader.

    CJK is information-dense: twenty Han characters is an entire headline where
    twenty Latin characters is three words. So a script the user doesn't read
    only has to account for :data:`_FOREIGN_SCRIPT_RATIO` of the text to condemn
    it — enough headroom for a quoted name, not enough for a clause.

    Kana and Hangul are exempt from the ratio: each is used in exactly one
    language, so any amount settles the question outright.
    """
    allowed: set[str] = set()
    for code in langs:
        allowed |= scripts_for(code)
    counts = script_counts(text)
    if not counts:
        return True  # nothing but numbers/punctuation — no evidence either way
    for script in _DECISIVE:
        if counts.get(script) and script not in allowed:
            return False
    # Latin is excluded from the numerator, never the denominator: it is the
    # shared alphabet every language borrows for tickers and figures, so its
    # presence is not evidence of language — but its volume is the baseline the
    # foreign share is measured against.
    foreign = sum(n for s, n in counts.items() if s != "latin" and s not in allowed)
    return foreign / sum(counts.values()) <= _FOREIGN_SCRIPT_RATIO


# -- stage 2: statistical detection ----------------------------------------

_detect_lock = threading.Lock()
_detect_ready: Optional[bool] = None


def _detector():
    """``langdetect.detect_langs`` with a fixed seed, or None if unavailable.

    langdetect is non-deterministic by default — the same headline can land in
    different buckets across runs. Seeding makes the gate reproducible, which
    matters because an unseeded false reject is a story the user never sees and
    cannot reproduce to report.
    """
    global _detect_ready
    with _detect_lock:
        if _detect_ready is False:
            return None
        try:
            from langdetect import DetectorFactory, detect_langs

            if _detect_ready is None:
                DetectorFactory.seed = 0
                _detect_ready = True
            return detect_langs
        except Exception:
            _detect_ready = False
            return None


def _normalize(code: str) -> str:
    """langdetect emits regional tags (``zh-cn``, ``zh-tw``); we key off the
    base language."""
    return code.split("-")[0].lower()


def detect_accepted(text: str, langs: Iterable[str]) -> bool:
    """Conservative statistical check — see the module docstring.

    Returns True (accept) whenever there's any doubt: text too short, langdetect
    missing or erroring, a weak top guess, or an accepted language anywhere in
    the candidate list.
    """
    accepted = {_normalize(c) for c in langs}
    stripped = "".join(ch for ch in text if _script_of(ch) is not None)
    if len(stripped) < _MIN_DETECT_CHARS:
        return True
    detect_langs = _detector()
    if detect_langs is None:
        return True
    try:
        candidates = detect_langs(text)
    except Exception:
        return True
    if not candidates:
        return True
    for cand in candidates:
        if _normalize(cand.lang) in accepted and cand.prob >= _ACCEPT_FLOOR:
            return True  # plausibly one of ours — keep it
    return candidates[0].prob < _REJECT_CONFIDENCE


# -- the public gate --------------------------------------------------------


def accepts_text(text: str, langs: Optional[Iterable[str]] = None) -> bool:
    """True when ``text`` reads as one of the user's languages.

    Both stages must pass. ``langs`` defaults to the stored preference; pass it
    explicitly to keep a batch consistent (and to avoid re-reading QSettings
    once per headline).
    """
    if not text or not text.strip():
        return True
    codes = list(langs) if langs is not None else spoken_languages()
    if not codes:
        return True
    return script_accepted(text, codes) and detect_accepted(text, codes)


def accepts_item(item: dict, langs: Optional[Iterable[str]] = None) -> bool:
    """Gate one news dict.

    A source-declared ``lang`` settles the *statistical* question — the source
    was asked for that language, so second-guessing it with a detector on a
    short headline would only manufacture false rejects. The script check still
    runs regardless, because editions leak: a Google News ``en`` edition can and
    does return the occasional Han-script item, and that is exactly what must
    never reach the panel.
    """
    codes = list(langs) if langs is not None else spoken_languages()
    text = " ".join(x for x in (item.get("title"), item.get("summary")) if x)
    declared = item.get("lang")
    if declared:
        if _normalize(str(declared)) not in {_normalize(c) for c in codes}:
            return False
        return not text.strip() or script_accepted(text, codes)
    return accepts_text(text, codes)


def filter_items(items: list, langs: Optional[Iterable[str]] = None) -> list:
    """Drop every entry not in one of the user's languages, preserving order."""
    codes = list(langs) if langs is not None else spoken_languages()
    return [i for i in items if isinstance(i, dict) and accepts_item(i, codes)]
