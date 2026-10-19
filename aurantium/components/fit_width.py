"""Tables that always fit their panel's width — no horizontal scrolling.

A panel is a dock that the user sizes; a table inside it that scrolls sideways
hides columns behind a scrollbar nobody reaches for, and in a narrow sidebar
it hides the number the row exists to show. Aurantium's rule: every table
fits whatever width it is given.

``fit_columns(view)`` installs that rule on any ``QTableView``/``QTreeView``:

* the horizontal scrollbar is switched off;
* while the visible columns' natural widths fit, the view's own resize modes
  are left alone (content-sized numbers, a stretching name column);
* once they don't, every visible column is shrunk in proportion to its
  natural width and its text elides — so a wide column gives up more than a
  narrow one, and nothing is pushed out of view. The view's own resize modes
  come back as soon as the panel is wide enough again.

``MarketTable`` drops its least important columns first (see
``set_column_priority``) and calls this for whatever is left; plain tables get
the proportional fit alone.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QHeaderView

_ATTR = "_aurantium_fitter"


def _scale(widths: dict[int, int], avail: int, minimum: int) -> dict[int, int]:
    """Scale ``widths`` to sum to exactly ``avail``, in proportion, none
    below ``minimum``. Columns that would fall under the floor are pinned to
    it and the rest share what is left, so the floor can't push the row past
    the edge."""
    pinned: dict[int, int] = {}
    free = dict(widths)
    while free:
        room = avail - sum(pinned.values())
        total = sum(free.values()) or 1
        low = {c: w for c, w in free.items() if w * room // total < minimum}
        if not low:
            out = {c: w * room // total for c, w in free.items()}
            # rounding remainder to the widest, so the sum is exact
            widest = max(out, key=out.get)
            out[widest] += room - sum(out.values())
            return {**pinned, **out}
        for c in low:
            pinned[c] = minimum
            del free[c]
    return pinned


class ColumnFitter(QObject):
    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        self._saved: dict[int, QHeaderView.ResizeMode] | None = None
        self._busy = False
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.viewport().installEventFilter(self)
        # content changes alter natural widths; coalesce a burst of cell
        # writes into one refit on the next event-loop turn
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.fit)
        model = view.model()
        for signal in (model.dataChanged, model.rowsInserted, model.rowsRemoved,
                       model.modelReset, model.layoutChanged, model.columnsInserted):
            # a lambda, not the bound method: PySide can't resolve a variadic
            # Python method as a slot and logs "Slot not found" on teardown
            signal.connect(lambda *_a: self.schedule())
        header = self._header()
        if header is not None:
            header.sectionCountChanged.connect(lambda *_a: self.schedule())

    def _header(self) -> QHeaderView | None:
        h = getattr(self._view, "horizontalHeader", None) or getattr(self._view, "header", None)
        return h() if callable(h) else None

    def _alive(self) -> bool:
        # a view tearing down still delivers viewport events and model
        # signals after its own C++ object is gone
        return shiboken6.isValid(self) and shiboken6.isValid(self._view)

    def schedule(self) -> None:
        if self._alive() and not self._timer.isActive():
            self._timer.start()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if event.type() == QEvent.Type.Resize and self._alive() and obj is self._view.viewport():
            self.fit()
        return False

    #: narrowest the text column gets before the numeric columns start to give
    TEXT_FLOOR = 90

    def _flex_column(self, header: QHeaderView, cols: list[int]) -> int:
        """The column that absorbs slack: the view's Stretch column (from the
        modes saved before fitting), else the first visible one."""
        modes = self._saved or {c: header.sectionResizeMode(c) for c in cols}
        stretch = [c for c in cols if modes.get(c) == QHeaderView.ResizeMode.Stretch]
        return stretch[0] if len(stretch) == 1 else cols[0]

    def _natural(self, header: QHeaderView, col: int) -> int:
        return max(self._view.sizeHintForColumn(col), header.sectionSizeHint(col), 1)

    #: rows inspected per column to learn its data alignment
    _ALIGN_SCAN_ROWS = 12

    def align_headers(self) -> None:
        """Align each column's header label the way its cells are aligned.

        Qt centres header labels whatever the data does, so a right-aligned
        number sat under a centred "Chg%" and a left-aligned name column on a
        wide panel had its "Symbol" floating hundreds of pixels from the first
        letter of any symbol. Reading the alignment off the column's own cells
        keeps every table right without each panel declaring it twice.
        Group-header rows (spans) are skipped.
        """
        view = self._view
        item_at = getattr(view, "item", None)
        header_item = getattr(view, "horizontalHeaderItem", None)
        if item_at is None or header_item is None:
            return
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        centre = Qt.AlignmentFlag.AlignCenter
        rows = min(view.rowCount(), self._ALIGN_SCAN_ROWS)
        for c in range(view.columnCount()):
            hdr = header_item(c)
            if hdr is None:
                continue
            want = None
            for r in range(rows):
                if view.columnSpan(r, 0) > 1:
                    continue
                cell = item_at(r, c)
                if cell is None or not cell.text():
                    continue
                align = Qt.AlignmentFlag(int(cell.textAlignment()))
                if align & Qt.AlignmentFlag.AlignRight:
                    want = right
                elif align & Qt.AlignmentFlag.AlignHCenter:
                    want = centre
                else:
                    want = left
                break
            if want is None:
                continue  # no data yet: leave the label where it is
            if Qt.AlignmentFlag(int(hdr.textAlignment())) != want:
                hdr.setTextAlignment(want)

    def fit(self) -> None:
        if self._busy or not self._alive():
            return
        header = self._header()
        if header is None:
            return
        self._busy = True
        try:
            self.align_headers()
            cols = [c for c in range(header.count()) if not header.isSectionHidden(c)]
            avail = self._view.viewport().width()
            if not cols or avail <= 0:
                return
            natural = {c: self._natural(header, c) for c in cols}
            total = sum(natural.values())
            if total <= avail:
                self._restore(header)
                return
            if self._saved is None:
                self._saved = {c: header.sectionResizeMode(c) for c in range(header.count())}
            if header.stretchLastSection():
                header.setStretchLastSection(False)
            # many columns in a sliver: let sections go narrower than Qt's
            # default floor rather than overflow
            minimum = min(header.minimumSectionSize(), max(avail // len(cols), 8))
            if minimum < header.minimumSectionSize():
                header.setMinimumSectionSize(minimum)
            widths = dict(natural)
            excess = total - avail
            # the text column (the one that stretches — a name or line item)
            # gives up width first: a truncated name still reads, a truncated
            # number or date does not
            flex = self._flex_column(header, cols)
            floor = min(natural[flex], self.TEXT_FLOOR)
            give = min(excess, natural[flex] - floor)
            widths[flex] -= give
            excess -= give
            if excess > 0:
                widths = _scale(widths, avail, minimum)
            else:
                # the flex column absorbed it all; end exactly at the edge
                widths[flex] += avail - sum(widths.values())
            for c in cols:
                if header.sectionResizeMode(c) != QHeaderView.ResizeMode.Interactive:
                    header.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
                if header.sectionSize(c) != widths[c]:
                    header.resizeSection(c, widths[c])
        finally:
            self._busy = False

    def _restore(self, header: QHeaderView) -> None:
        if self._saved is None:
            return
        for c, mode in self._saved.items():
            if c < header.count() and header.sectionResizeMode(c) != mode:
                header.setSectionResizeMode(c, mode)
        self._saved = None

    def forget_saved_modes(self) -> None:
        """The owner just re-asserted its resize modes; treat them as normal."""
        self._saved = None


#: smallest a relaxed control may get; below this it is clipped
_CONTROL_MIN = 56


def relax_min_widths(root) -> None:
    """Let one-line labels and input controls under ``root`` shrink below
    their natural width.

    A ``QLabel`` (without word wrap), ``QComboBox`` or spin/date box reports
    its full text width as its *minimum*, so one long title or a combo of
    long names holds the whole panel wide. Setting an explicit small minimum
    keeps the size hint — they still get their natural width whenever there
    is room — but lets the layout squeeze them in a narrow dock. Widgets with
    an explicit width already (``setFixedWidth`` / ``setMinimumWidth``) are
    left as their panel set them.
    """
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QLabel

    for w in root.findChildren(QLabel) + root.findChildren(QComboBox) + root.findChildren(QAbstractSpinBox):
        if w.minimumWidth() > 0 or w.maximumWidth() < 16777215:
            continue  # the panel sized it on purpose
        if isinstance(w, QLabel):
            if w.wordWrap() or w.pixmap() is not None and not w.pixmap().isNull():
                continue
            w.setMinimumWidth(1)
        else:
            w.setMinimumWidth(min(_CONTROL_MIN, w.sizeHint().width()))


def fit_tabs(tabs) -> None:
    """A ``QTabWidget``/``QTabBar`` whose tabs shrink and elide to fit rather
    than growing scroll arrows."""
    tabs.setUsesScrollButtons(False)
    tabs.setElideMode(Qt.TextElideMode.ElideRight)


def fit_columns(view: QAbstractItemView) -> ColumnFitter:
    """Make ``view`` always fit its width. Idempotent."""
    fitter = getattr(view, _ATTR, None)
    if fitter is None:
        fitter = ColumnFitter(view)
        setattr(view, _ATTR, fitter)
    return fitter
