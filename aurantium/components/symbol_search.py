"""Typeahead symbol suggestions shared by every symbol input.

Hybrid source: the curated catalog (commodities, indices, FX, rates, sector
ETFs) plus the user's watchlist answer instantly and offline; a debounced
Yahoo Finance search adds company matches by plain-English name ("nvidia" →
NVDA). Remote failures are silent — the popup simply stays local-only — and
rate limits go through the shared ``_yf`` gate like every other provider.

Two layers:

- ``SuggestionEngine`` — query in, ranked ``SymbolSuggestion`` list out, via
  the ``suggestions_ready(query, list)`` signal. Local results emit
  immediately; the Yahoo call fires after a debounce on a worker thread and
  a second emission merges it in. Per-session LRU cache keeps backspacing
  free. One shared instance (``shared_engine``) serves every input.
- ``SuggestField`` — attaches the popup to a ``QLineEdit``: an unfiltered
  ``QCompleter`` whose model the engine fills, Tab/Shift+Tab cycling, and an
  optional ``on_pick`` callback (the top bar navigates; panel fields just get
  the ticker filled in). ``attach_suggestions`` is the one-line helper for
  panels.

FRED series are deliberately excluded: every consumer here feeds ``quote:``
topics, where a FRED id would silently show nothing.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Iterable, NamedTuple, Sequence

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import QKeyEvent, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QApplication, QCompleter, QLineEdit

from .symbol_catalog import (
    FX_ENTRIES,
    INDEX_ENTRIES,
    SECTOR_ETF_ENTRIES,
    TENOR_ENTRIES,
    CatalogEntry,
    commodity_entries,
    search_catalog,
)

MAX_ROWS = 15          # popup rows after merging local + remote
_LOCAL_LIMIT = 10      # curated hits per query (remote fills the rest)
_REMOTE_MIN_CHARS = 2  # don't hit Yahoo for single letters
_DEBOUNCE_MS = 250
_CACHE_SIZE = 200
_SUGGESTION_ROLE = Qt.ItemDataRole.UserRole + 1
#: What gets inserted into the field on accept (the bare code). Deliberately
#: NOT EditRole: QStandardItem stores Display+Edit as one value, so using
#: EditRole would clobber the pretty "CODE — Name · Category" popup text.
_CODE_ROLE = Qt.ItemDataRole.UserRole + 2


class SymbolSuggestion(NamedTuple):
    code: str       # Yahoo symbol
    label: str      # plain-English display name
    category: str   # "Equity" | "Commodity" | "Index" | "FX" | … (popup tag)


def _display_text(s: SymbolSuggestion) -> str:
    if s.label and s.label != s.code:
        return f"{s.code} — {s.label} · {s.category}"
    return f"{s.code} · {s.category}"


# -- local source ----------------------------------------------------------

_catalog_cache: list[CatalogEntry] | None = None


def _catalog() -> list[CatalogEntry]:
    """Every curated non-FRED entry, built once (commodity list is derived)."""
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = (
            list(TENOR_ENTRIES)
            + list(INDEX_ENTRIES)
            + list(FX_ENTRIES)
            + list(SECTOR_ETF_ENTRIES)
            + commodity_entries()
        )
    return _catalog_cache


def local_suggestions(
    text: str, extra_symbols: Iterable[str] = ()
) -> list[SymbolSuggestion]:
    """Instant, offline candidates: ranked curated-catalog hits first, then
    watchlist symbols matching by prefix. Empty text suggests nothing (an
    empty field must not pop a list of arbitrary curated entries)."""
    needle = (text or "").strip().casefold()
    if not needle:
        return []
    out = [
        SymbolSuggestion(e.code, e.label, e.category)
        for e in search_catalog(_catalog(), text, limit=_LOCAL_LIMIT)
    ]
    for sym in extra_symbols:
        if str(sym).casefold().startswith(needle):
            out.append(SymbolSuggestion(str(sym), "", "Watchlist"))
    return out


def merge_suggestions(
    local: Sequence[SymbolSuggestion],
    remote: Sequence[SymbolSuggestion],
    limit: int = MAX_ROWS,
) -> list[SymbolSuggestion]:
    """Local first (curated names outrank Yahoo for e.g. "gold"), then remote,
    deduped by code case-insensitively, capped at ``limit``."""
    seen: set[str] = set()
    out: list[SymbolSuggestion] = []
    for s in list(local) + list(remote):
        key = s.code.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


# -- remote source (Yahoo search via yfinance) -----------------------------

_QUOTE_TYPE_LABEL = {
    "EQUITY": "Equity",
    "ETF": "ETF",
    "INDEX": "Index",
    "CURRENCY": "FX",
    "CRYPTOCURRENCY": "Crypto",
    "FUTURE": "Future",
    "MUTUALFUND": "Fund",
}


def quotes_to_suggestions(quotes: Iterable[dict]) -> list[SymbolSuggestion]:
    """Map Yahoo search-result dicts to suggestions; rows without a symbol or
    with an unsupported quote type (options) are dropped."""
    out: list[SymbolSuggestion] = []
    for q in quotes:
        code = q.get("symbol")
        if not code:
            continue
        qtype = str(q.get("quoteType") or "").upper()
        if qtype == "OPTION":
            continue
        label = q.get("shortname") or q.get("longname") or ""
        out.append(
            SymbolSuggestion(str(code), str(label), _QUOTE_TYPE_LABEL.get(qtype, "Quote"))
        )
    return out


def yahoo_search(query: str, limit: int = 8) -> list[SymbolSuggestion]:
    """One Yahoo search call. Quiet during the global rate-limit cooldown;
    a throttle that survives ``with_retry`` trips the shared gate and raises
    (the caller's worker swallows it — suggestions just stay local)."""
    from ..providers._yf import RATE_LIMIT_GATE, with_retry

    if RATE_LIMIT_GATE.blocked():
        return []

    def call() -> list[dict]:
        import yfinance as yf

        search = yf.Search(
            query,
            max_results=limit,
            news_count=0,
            lists_count=0,
            include_cb=False,
            timeout=8,
        )
        return list(search.quotes or [])

    return quotes_to_suggestions(with_retry(call))


class _TaskSignals(QObject):
    done = Signal(str, list)  # query, list[SymbolSuggestion]


class _RemoteTask(QRunnable):
    """Runs one remote search off the GUI thread; failures emit an empty list."""

    def __init__(self, query: str, fn: Callable[[str], list[SymbolSuggestion]]) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._query = query
        self._fn = fn
        self.signals = _TaskSignals()

    def run(self) -> None:  # noqa: D102 (QRunnable override)
        try:
            results = self._fn(self._query)
        except Exception:
            results = []
        self.signals.done.emit(self._query, results)


# -- engine ----------------------------------------------------------------


class SuggestionEngine(QObject):
    """Turns typed text into ranked suggestions, local-instant + remote-late."""

    suggestions_ready = Signal(str, list)  # query, list[SymbolSuggestion]

    def __init__(
        self,
        parent: QObject | None = None,
        remote: Callable[[str], list[SymbolSuggestion]] | None = yahoo_search,
        extra_symbols: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._remote = remote
        self._extra = extra_symbols or (lambda: ())
        self._cache: OrderedDict[str, list[SymbolSuggestion]] = OrderedDict()
        self._query = ""
        self._pending: set[str] = set()  # queries already in flight
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._fire_remote)

    def set_extra_symbols(self, provider: Callable[[], Iterable[str]]) -> None:
        """Late-bind the watchlist-symbols provider (the main window owns it)."""
        self._extra = provider

    def request(self, text: str) -> None:
        """Called on every keystroke. Emits local suggestions immediately;
        arms the debounce for a remote pass (skipped on cache hits)."""
        query = (text or "").strip()
        self._query = query
        if not query:
            self._timer.stop()
            self.suggestions_ready.emit(query, [])
            return
        local = local_suggestions(query, self._extra())
        cached = self._cache.get(query.casefold())
        if cached is not None:
            self._cache.move_to_end(query.casefold())
            self._timer.stop()
            self.suggestions_ready.emit(query, merge_suggestions(local, cached))
            return
        self.suggestions_ready.emit(query, merge_suggestions(local, []))
        if self._remote is not None and len(query) >= _REMOTE_MIN_CHARS:
            self._timer.start()

    def _fire_remote(self) -> None:
        query = self._query
        if not query or self._remote is None or query.casefold() in self._pending:
            return
        self._pending.add(query.casefold())
        task = _RemoteTask(query, self._remote)
        task.signals.done.connect(self._on_remote)
        QThreadPool.globalInstance().start(task)

    def _on_remote(self, query: str, results: list) -> None:
        key = query.casefold()
        self._pending.discard(key)
        self._cache[key] = list(results)
        while len(self._cache) > _CACHE_SIZE:
            self._cache.popitem(last=False)
        if query != self._query:  # user kept typing — stale, drop silently
            return
        local = local_suggestions(query, self._extra())
        self.suggestions_ready.emit(query, merge_suggestions(local, results))


_shared_engine: SuggestionEngine | None = None


def shared_engine() -> SuggestionEngine:
    """The process-wide engine every input shares (one cache, one debounce)."""
    global _shared_engine
    if _shared_engine is None:
        _shared_engine = SuggestionEngine()
    return _shared_engine


# -- field attachment ------------------------------------------------------


class SuggestField(QObject):
    """Wires a ``QLineEdit`` to an engine: unfiltered completer popup, Tab /
    Shift+Tab cycling, Enter/click accepts, Esc closes.

    - ``on_pick(suggestion)`` — called after a suggestion row is accepted
      (the completer has already put the code in the field). The top bar uses
      it to navigate immediately; panel fields leave it ``None``.
    - ``slash_completions`` — command-bar hook: when the text starts with
      "/", rows come from this callable (prefix-filtered) instead of the
      engine, preserving ``/add`` / ``/layout`` completion.
    - ``token_separator`` — for list fields ("SPY, QQQ"): suggestions match
      the last token and accepting replaces only that token.
    """

    def __init__(
        self,
        line_edit: QLineEdit,
        engine: SuggestionEngine | None = None,
        on_pick: Callable[[SymbolSuggestion], None] | None = None,
        slash_completions: Callable[[], list[str]] | None = None,
        token_separator: str | None = None,
    ) -> None:
        super().__init__(line_edit)
        self._edit = line_edit
        self._engine = engine or shared_engine()
        self._on_pick = on_pick
        self._slash = slash_completions
        self._sep = token_separator
        self._query = ""       # what we last asked the engine for
        self._text_before = ""  # full field text before completer insertion

        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion
        )
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        # insertion text (pathFromIndex) comes from this role, display from
        # DisplayRole — rows can show "CODE — Name" while accepting fills CODE
        self._completer.setCompletionRole(_CODE_ROLE)
        popup = self._completer.popup()
        popup.setObjectName("suggestPopup")
        popup.setUniformItemSizes(True)
        line_edit.setCompleter(self._completer)
        # installed after QCompleter's own filter, so ours runs first
        popup.installEventFilter(self)
        line_edit.installEventFilter(self)

        self._completer.activated[QModelIndex].connect(self._on_activated)
        line_edit.textEdited.connect(self._on_text_edited)
        self._engine.suggestions_ready.connect(self._on_suggestions)

    # -- typing → engine ---------------------------------------------------

    def _current_query(self, text: str) -> str:
        if self._sep is not None:
            text = text.split(self._sep)[-1]
        return text.strip()

    def _on_text_edited(self, text: str) -> None:
        self._text_before = text
        if self._slash is not None and text.lstrip().startswith("/"):
            self._query = ""
            self._show_slash_rows(text.strip())
            return
        self._query = self._current_query(text)
        self._engine.request(self._query)

    def _show_slash_rows(self, prefix: str) -> None:
        try:
            items = [str(x) for x in self._slash() if str(x).startswith(prefix)]
        except Exception:
            items = []
        self._model.clear()
        for text in items[:MAX_ROWS]:
            item = QStandardItem()
            item.setData(text, Qt.ItemDataRole.DisplayRole)
            item.setData(text, _CODE_ROLE)
            self._model.appendRow(item)
        self._refresh_popup()

    # -- engine → popup ----------------------------------------------------

    def _on_suggestions(self, query: str, suggestions: list) -> None:
        # the engine is shared: only react to answers for *our* current query
        # while *we* have focus
        if not self._edit.hasFocus() or query != self._query:
            return
        self._model.clear()
        for s in suggestions:
            item = QStandardItem()
            item.setData(_display_text(s), Qt.ItemDataRole.DisplayRole)
            item.setData(s.code, _CODE_ROLE)
            item.setData(s, _SUGGESTION_ROLE)
            self._model.appendRow(item)
        self._refresh_popup()

    def _refresh_popup(self) -> None:
        popup = self._completer.popup()
        if self._model.rowCount() == 0:
            popup.hide()
            return
        if self._edit.hasFocus():
            self._completer.complete()

    # -- accepting ---------------------------------------------------------

    def _on_activated(self, index: QModelIndex) -> None:
        # Qt has already replaced the field text with the code (EditRole)
        suggestion = index.data(_SUGGESTION_ROLE)
        if self._sep is not None and suggestion is not None:
            tokens = [t.strip() for t in self._text_before.split(self._sep)]
            rebuilt = [t for t in tokens[:-1] if t] + [suggestion.code]
            self._edit.setText((self._sep + " ").join(rebuilt))
        if suggestion is not None and self._on_pick is not None:
            self._on_pick(suggestion)

    # -- keys --------------------------------------------------------------

    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress:
            return False
        popup = self._completer.popup()
        key = event.key()

        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            if popup.isVisible() and self._model.rowCount() > 0:
                self._cycle(-1 if key == Qt.Key.Key_Backtab else 1)
                return True
            if obj is self._edit and self._model.rowCount() > 0 and self._edit.text():
                self._completer.complete()
                self._cycle(1)
                return True
            return False

        # Enter with nothing highlighted: close the popup and let the field's
        # own returnPressed flow run (send the raw text / add the symbol)
        if (
            key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and obj is popup
            and not popup.currentIndex().isValid()
        ):
            popup.hide()
            QApplication.sendEvent(
                self._edit,
                QKeyEvent(event.type(), key, event.modifiers(), event.text()),
            )
            return True
        return False

    def _cycle(self, step: int) -> None:
        # the popup view runs on the completer's *completion model* (a proxy
        # over ours, 1:1 in unfiltered mode) — index through it, not _model
        popup = self._completer.popup()
        model = popup.model()
        count = model.rowCount()
        if count == 0:
            return
        row = popup.currentIndex().row()  # -1 when nothing highlighted
        popup.setCurrentIndex(model.index((row + step) % count, 0))


def attach_suggestions(
    line_edit: QLineEdit,
    on_pick: Callable[[SymbolSuggestion], None] | None = None,
    token_separator: str | None = None,
) -> SuggestField:
    """One-liner for panels: shared engine, fill-the-field-on-pick behavior."""
    return SuggestField(
        line_edit,
        shared_engine(),
        on_pick=on_pick,
        token_separator=token_separator,
    )
