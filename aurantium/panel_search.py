"""Search box at the top of the Panels menu.

Typing replaces the category submenus with a ranked list of panels; Enter
opens the highlighted one exactly as clicking it in the menu would. The search
is meant to land on the right panel when the user doesn't know its name, so a
panel is matched on, strongest first:

1. its name, including a partial word ("comm" → Commodities);
2. its guide's ``keywords`` — other names for the same thing ("graph" → Chart)
   and words that belong to it ("debt" → Company › Financials);
3. a typo of either ("comodity", "wachlist");
4. any word of its quick guide, as a weak fallback.

The tabs of a host panel (Company's Profile, Key Metrics, Financials and
Earnings) are entries of their own, opening the host on that tab — so the old
panel names still find what they became.

The ranking (:func:`build_entries`, :func:`search`, :func:`closest`) is plain
Python and runs without a window; :class:`PanelMenuSearch` is the widget.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QCoreApplication, QEvent, QModelIndex, QObject, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QActionEvent, QFont, QFontMetrics, QPainter, QColor
from PySide6.QtWidgets import (
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .guide import PanelGuide
from .theme import (
    ACCENT,
    CHROME_TEXT,
    CHROME_TEXT_DIM,
    FONT_MD,
    FONT_SM,
    FG_MUTED,
    ON_ACCENT,
    RADIUS_SM,
    SPACE_LG,
    SPACE_SM,
    SPACE_XL,
    UI_FONT,
    WEIGHT_MEDIUM,
)

#: most rows the menu shows for one query
MAX_RESULTS = 8

#: words that say nothing about which panel is wanted
_STOPWORDS = frozenset(
    "a an the of for and or to in on at by with is are my me i we our it its "
    "show see view open find want get what which how where panel panels".split()
)

# how much a match in each part of an entry counts
_W_NAME = 1.0
_W_KEYWORD = 0.8
_W_CATEGORY = 0.4
_W_GUIDE = 0.3

#: below this a row isn't worth showing
_MIN_SCORE = 0.2
#: a typo is accepted at this similarity or better
_FUZZY = 0.8
#: the "closest" guess offered when nothing matches needs at least this
_CLOSEST = 0.55


# --------------------------------------------------------------------------
# Ranking
# --------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, accents and punctuation dropped. Punctuation *inside* a word
    joins it ("P/E" → "pe", "S&P" → "sp") so it matches however it's typed."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"(?<=\w)[/&.'’-](?=\w)", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _stem(token: str) -> str:
    """A plural and its singular are the same word ("bonds", "bond")."""
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _tokens(text: str, *, drop_stopwords: bool = True) -> list[str]:
    out = []
    for tok in _normalize(text).split():
        if drop_stopwords and tok in _STOPWORDS:
            continue
        out.append(_stem(tok))
    return out


def _phrase(text: str) -> str:
    return " ".join(_tokens(text, drop_stopwords=False))


@dataclass(frozen=True)
class PanelEntry:
    """One searchable row: a panel, or a tab of a host panel."""

    #: the id handed to ``add_panel`` — a retired alias for a host's tab
    open_id: str
    #: shown in the row: "Commodities", "Company › Financials"
    title: str
    #: shown under the title: the guide's one-line purpose
    purpose: str
    names: tuple[str, ...]
    keywords: tuple[str, ...] = ()
    category: str = ""
    guide_text: str = ""

    def __post_init__(self) -> None:
        # tokenised once, here, not on every keystroke
        object.__setattr__(self, "_name_toks", {t for n in self.names for t in _tokens(n)})
        object.__setattr__(self, "_kw_toks", {t for k in self.keywords for t in _tokens(k)})
        object.__setattr__(self, "_cat_toks", set(_tokens(self.category)))
        object.__setattr__(self, "_guide_toks", set(_tokens(self.guide_text)))
        object.__setattr__(
            self, "_phrases",
            tuple(p for p in (_phrase(x) for x in (*self.names, *self.keywords)) if p),
        )


def _plain(text: str) -> str:
    """Guide text without its ``**bold**`` markers, on one line."""
    return " ".join(text.replace("**", "").split())


def _first_sentence(text: str) -> str:
    text = _plain(text)
    m = re.search(r"(?<=[.!?])\s", text)
    return text[: m.start()] if m else text


def _guide(cls) -> Optional[PanelGuide]:
    g = getattr(cls, "GUIDE", None)
    return g if isinstance(g, PanelGuide) else None


def build_entries(metas: Iterable, resolve: Callable[[str], tuple[str, dict]]) -> list[PanelEntry]:
    """Entries for every listed panel and every hosted tab. ``resolve`` is
    ``PanelRegistry.resolve``: a tab opens through the retired id that maps
    to it, or through its host when no such alias exists."""
    entries: list[PanelEntry] = []
    for meta in metas:
        g = _guide(meta.cls)
        entries.append(PanelEntry(
            open_id=meta.id,
            title=meta.title,
            purpose=_first_sentence(g.purpose) if g else "",
            names=(meta.title, meta.id.replace("_", " ")),
            keywords=g.keywords if g else (),
            category=meta.category,
            guide_text=g.search_text() if g else "",
        ))
        for key, label, child in getattr(meta.cls, "HOSTED_TABS", ()):
            cg = _guide(child)
            opens_tab = resolve(key) == (meta.id, {"tab": key})
            entries.append(PanelEntry(
                open_id=key if opens_tab else meta.id,
                title=f"{meta.title} › {label}",
                purpose=_first_sentence(cg.purpose) if cg else "",
                # the retired panel's own name ("Company Profile") counts too
                names=(label, getattr(child, "panel_title", label)),
                keywords=cg.keywords if cg else (),
                category=meta.category,
                guide_text=cg.search_text() if cg else "",
            ))
    return entries


def _quality(q: str, t: str, *, prefix_min: int = 2, fuzzy: bool = True) -> float:
    """How well query word ``q`` matches entry word ``t``: 1 for the same
    word, a little less for the start of it, less again for a typo."""
    if q == t:
        return 1.0
    if len(q) >= prefix_min and t.startswith(q):
        return 0.7 + 0.3 * len(q) / len(t)
    if fuzzy and len(q) >= 4 and len(t) >= 4:
        r = SequenceMatcher(None, q, t).ratio()
        if len(t) > len(q):
            # a typo in a word still being typed: "comod" against "commo…"
            r = max(r, SequenceMatcher(None, q, t[: len(q)]).ratio())
        if r >= _FUZZY:
            return 0.75 * r
    return 0.0


def _best(q: str, entry: PanelEntry) -> float:
    best = 0.0
    for toks, weight, kw in (
        (entry._name_toks, _W_NAME, {"prefix_min": 1}),
        (entry._kw_toks, _W_KEYWORD, {}),
        (entry._cat_toks, _W_CATEGORY, {}),
        # the guide is long: exact words and long prefixes only, no typos
        (entry._guide_toks, _W_GUIDE, {"prefix_min": 4, "fuzzy": False}),
    ):
        if weight <= best:
            break  # nothing in a lighter field can beat what's found
        for t in toks:
            s = weight * _quality(q, t, **kw)
            if s > best:
                best = s
                if s >= weight:
                    break
    return best


def score(query: str, entry: PanelEntry) -> float:
    """0 for no match; higher is better. Every query word counts, and a word
    that matches nothing pulls the score down rather than ruling the panel
    out — "top losers" still finds Market Movers on "losers"."""
    words = _tokens(query)
    if not words:
        return 0.0
    bests = [_best(w, entry) for w in words]
    matched = sum(1 for b in bests if b > 0)
    if not matched:
        return 0.0
    total = sum(bests) / len(words) * (matched / len(words))
    phrase = " ".join(words)
    if any(p == phrase for p in entry._phrases):
        total += 0.5
    elif len(phrase) >= 2 and any(p.startswith(phrase) for p in entry._phrases):
        total += 0.3
    return total


def search(query: str, entries: Iterable[PanelEntry], limit: int = MAX_RESULTS) -> list[PanelEntry]:
    """The best-matching entries, best first; empty for an empty query."""
    scored = [(score(query, e), e) for e in entries]
    hits = [(s, e) for s, e in scored if s >= _MIN_SCORE]
    hits.sort(key=lambda se: (-se[0], se[1].title))
    return [e for _s, e in hits[:limit]]


def closest(query: str, entries: Iterable[PanelEntry]) -> Optional[PanelEntry]:
    """When :func:`search` finds nothing: the entry whose name or keyword the
    query most resembles, if any comes reasonably close."""
    q = _phrase(query)
    if len(q) < 3:
        return None
    best, best_r = None, _CLOSEST
    for e in entries:
        for p in e._phrases:
            r = SequenceMatcher(None, q, p).ratio()
            if r > best_r:
                best, best_r = e, r
    return best


# --------------------------------------------------------------------------
# Menu widget
# --------------------------------------------------------------------------

#: item data roles
_ROLE_ENTRY = Qt.ItemDataRole.UserRole
_ROLE_NOTE = Qt.ItemDataRole.UserRole + 1

#: the box and its rows are this wide; the menu grows to fit
_WIDTH = 340


class _ResultDelegate(QStyledItemDelegate):
    """A result row as the menu draws its items: name over a dim one-line
    purpose, the highlighted row filled amber like a selected menu item."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._title_font = QFont(UI_FONT)
        self._title_font.setPixelSize(FONT_MD)
        self._title_font.setWeight(QFont.Weight(WEIGHT_MEDIUM))
        self._sub_font = QFont(UI_FONT)
        self._sub_font.setPixelSize(FONT_SM)

    def _row_height(self) -> int:
        return (QFontMetrics(self._title_font).height()
                + QFontMetrics(self._sub_font).height() + 2 * SPACE_LG)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        if index.data(_ROLE_NOTE):
            return QSize(_WIDTH, QFontMetrics(self._sub_font).height() + 2 * SPACE_LG)
        return QSize(_WIDTH, self._row_height())

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        text_rect = rect.adjusted(SPACE_XL, SPACE_LG, -SPACE_XL, -SPACE_LG)
        note = index.data(_ROLE_NOTE)
        if note:
            p.setFont(self._sub_font)
            p.setPen(QColor(FG_MUTED))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       QFontMetrics(self._sub_font).elidedText(
                           note, Qt.TextElideMode.ElideRight, text_rect.width()))
            p.restore()
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(rect, RADIUS_SM, RADIUS_SM)
        entry: PanelEntry = index.data(_ROLE_ENTRY)
        title_h = QFontMetrics(self._title_font).height()
        p.setFont(self._title_font)
        p.setPen(QColor(ON_ACCENT if selected else CHROME_TEXT))
        p.drawText(QRect(text_rect.left(), text_rect.top(), text_rect.width(), title_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   QFontMetrics(self._title_font).elidedText(
                       index.data(Qt.ItemDataRole.DisplayRole) or "",
                       Qt.TextElideMode.ElideRight, text_rect.width()))
        if entry is not None and entry.purpose:
            p.setFont(self._sub_font)
            p.setPen(QColor(ON_ACCENT if selected else CHROME_TEXT_DIM))
            sub = QRect(text_rect.left(), text_rect.top() + title_h,
                        text_rect.width(), text_rect.height() - title_h)
            p.drawText(sub, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(self._sub_font).elidedText(
                           entry.purpose, Qt.TextElideMode.ElideRight, sub.width()))
        p.restore()


class PanelMenuSearch(QObject):
    """Puts a search box at the top of ``menu``. While it holds text every
    other action in the menu is hidden and a ranked result list shows in their
    place; clearing it brings the categories back.

    ``entries`` is called each time the menu opens, so panels registered
    after start-up are searchable too. ``open_panel(open_id)`` opens a pick.
    """

    def __init__(self, menu: QMenu, entries: Callable[[], list[PanelEntry]],
                 open_panel: Callable[[str], None]) -> None:
        super().__init__(menu)
        self._menu = menu
        self._entries_fn = entries
        self._entries: list[PanelEntry] = []
        self._open_panel = open_panel

        box = QWidget(menu)
        box.setObjectName("panelSearchBox")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(SPACE_SM, SPACE_SM, SPACE_SM, SPACE_SM)
        lay.setSpacing(SPACE_SM)

        self.edit = QLineEdit(box)
        self.edit.setObjectName("panelSearch")
        self.edit.setPlaceholderText("Search panels…")
        self.edit.setClearButtonEnabled(True)
        self.edit.setMinimumWidth(_WIDTH)
        self.edit.textChanged.connect(self._on_text)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)

        self.results = QListWidget(box)
        self.results.setObjectName("panelSearchResults")
        self.results.setItemDelegate(_ResultDelegate(self.results))
        self.results.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # typing stays in the box
        self.results.setMouseTracking(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setFrameShape(QListWidget.Shape.NoFrame)
        self.results.entered.connect(self._on_hover)
        self.results.itemClicked.connect(self._open_item)
        self.results.hide()
        lay.addWidget(self.results)

        self.action = QWidgetAction(menu)
        self.action.setDefaultWidget(box)
        menu.aboutToShow.connect(self._on_about_to_show)

    # -- menu lifecycle -------------------------------------------------------

    def _on_about_to_show(self) -> None:
        self._entries = self._entries_fn()
        if self.edit.text():
            self.edit.clear()  # each opening starts from the categories
        # the menu takes focus as it pops up; take it back once it has
        QTimer.singleShot(0, self._focus_box)

    def _focus_box(self) -> None:
        if self._menu.isVisible():
            self._menu.setActiveAction(self.action)
            self.edit.setFocus(Qt.FocusReason.PopupFocusReason)

    def _set_searching(self, on: bool) -> None:
        for act in self._menu.actions():
            if act is not self.action:
                act.setVisible(not on)
        self.results.setVisible(on)

    # -- searching ------------------------------------------------------------

    def _on_text(self, text: str) -> None:
        query = text.strip()
        self.results.clear()
        if not query:
            self._set_searching(False)
            self._refit()
            return
        if not self._entries:
            self._entries = self._entries_fn()
        hits = search(query, self._entries)
        if hits:
            for e in hits:
                self._add_entry(e)
        else:
            note = QListWidgetItem(self.results)
            note.setData(_ROLE_NOTE, f"No panels match “{query}”")
            note.setFlags(Qt.ItemFlag.NoItemFlags)
            guess = closest(query, self._entries)
            if guess is not None:
                self._add_entry(guess, prefix="Closest: ")
        first = next((i for i in range(self.results.count())
                      if self.results.item(i).data(_ROLE_ENTRY) is not None), -1)
        self.results.setCurrentRow(first)
        self._set_searching(True)
        self._refit()

    def _add_entry(self, entry: PanelEntry, prefix: str = "") -> None:
        item = QListWidgetItem(prefix + entry.title, self.results)
        item.setData(_ROLE_ENTRY, entry)
        item.setToolTip(entry.purpose)

    def _refit(self) -> None:
        """Size the list to its rows and the menu to the list."""
        rows = self.results.count()
        h = sum(self.results.sizeHintForRow(i) for i in range(rows))
        self.results.setFixedHeight(h + 2 * self.results.frameWidth())
        self.action.defaultWidget().adjustSize()
        # QMenu caches each action's size and only re-measures one it is told
        # changed; adjustSize alone kept the old height and clipped the list
        QCoreApplication.sendEvent(
            self._menu, QActionEvent(QEvent.Type.ActionChanged, self.action))
        self._menu.adjustSize()

    # -- picking --------------------------------------------------------------

    def _on_hover(self, index: QModelIndex) -> None:
        if index.data(_ROLE_ENTRY) is not None:
            self.results.setCurrentIndex(index)

    def _move(self, step: int) -> None:
        n = self.results.count()
        if not n:
            return
        row = self.results.currentRow()
        for _ in range(n):
            row = (row + step) % n
            if self.results.item(row).data(_ROLE_ENTRY) is not None:
                self.results.setCurrentRow(row)
                return

    def _open_item(self, item: Optional[QListWidgetItem]) -> None:
        entry = item.data(_ROLE_ENTRY) if item is not None else None
        if entry is None:
            return
        self._menu.close()
        self._open_panel(entry.open_id)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            searching = bool(self.edit.text().strip())
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if searching:
                    self._move(1 if key == Qt.Key.Key_Down else -1)
                    return True
                # no query: arrows walk the categories as in any menu
                acts = [a for a in self._menu.actions()
                        if a is not self.action and a.isVisible() and not a.isSeparator()]
                if acts:
                    self._menu.setActiveAction(acts[0] if key == Qt.Key.Key_Down else acts[-1])
                    self._menu.setFocus()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and searching:
                self._open_item(self.results.currentItem())
                return True
            if key == Qt.Key.Key_Escape and self.edit.text():
                self.edit.clear()
                return True
        return super().eventFilter(obj, event)
