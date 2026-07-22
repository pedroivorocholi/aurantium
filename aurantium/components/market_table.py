"""MarketTable — the shared table widget used by aurantium's data panels.

A thin ``QTableWidget`` subclass that bakes in the terminal's table conventions
(read-only, row selection, no vertical header, zebra striping, no grid) and adds
two things every data panel wants:

* a **loading overlay** — ``set_loading(True)`` dims the table and shows a
  "Loading…" indicator while a fetch is in flight;
* a right-click **"Export Table to CSV…"** action.

Panels construct it like a normal table (``MarketTable(rows, cols, self)``), set
their own header labels, and populate cells as usual — they just drop the
repeated configuration boilerplate.

Note on the overlay: an item view paints its rows on ``viewport()``, not on the
widget itself, so overriding ``MarketTable.paintEvent`` would only cover the
frame. The overlay is therefore a small transparent child of the viewport with
its own ``paintEvent`` — the correct, flicker-free way to draw over the rows.
"""

from __future__ import annotations

import csv
from contextlib import contextmanager

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..theme import ACCENT


_NUM_SUFFIX = {"T": 1e12, "B": 1e9, "M": 1e6, "K": 1e3}


def parse_numeric(text: str) -> float | None:
    """Best-effort parse of a *displayed* cell value to a float, for sorting.

    Understands the formats aurantium panels actually render: thousands
    separators, ``$``/``%``/``+`` decoration, parenthesised negatives, and
    ``T``/``B``/``M``/``K`` magnitude suffixes (so ``"1.2M"`` > ``"900K"``).
    Returns ``None`` when the text isn't a single number (blanks, ``"-"``,
    ranges like ``"1.2 – 3.4"``), letting callers fall back to string order.
    """
    s = (text or "").strip()
    if not s or s in {"-", "—", "N/A", "n/a"}:
        return None
    # strip the color-blind direction glyphs first — a cell may read "▲ +1.2%"
    s = s.replace("▲", "").replace("▼", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    for ch in (",", "$", "%", "+", " "):
        s = s.replace(ch, "")
    if not s:
        return None
    mult = 1.0
    if s[-1].upper() in _NUM_SUFFIX:
        mult = _NUM_SUFFIX[s[-1].upper()]
        s = s[:-1]
    try:
        value = float(s) * mult
    except ValueError:
        return None
    return -value if neg else value


class NumericTableWidgetItem(QTableWidgetItem):
    """A table item that sorts by the numeric value of its displayed text.

    Falls back to case-insensitive string comparison when a cell isn't
    numeric; non-numeric cells (``"-"``) sort below numbers in ascending
    order. Use this for any column a panel wants sorted as numbers rather
    than as strings (prices, %, volumes).
    """

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D105 (Qt override)
        a = parse_numeric(self.text())
        b = parse_numeric(other.text())
        if a is not None and b is not None:
            return a < b
        if a is not None:
            return False  # numbers rank above blanks/dashes
        if b is not None:
            return True
        return self.text().casefold() < other.text().casefold()


def make_filter_edit(table: "MarketTable", placeholder: str = "Filter…") -> QLineEdit:
    """A small QLineEdit wired to live-filter ``table``. The caller adds it to
    a panel layout (typically just above the table)."""
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    edit.textChanged.connect(table.apply_filter)
    return edit


class _LoadingOverlay(QWidget):
    """Semi-transparent veil + centered 'Loading…' label, sized to the viewport."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        # let clicks/scroll pass through to the table underneath
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140))  # dim the rows beneath
        f = self.font()
        f.setPointSizeF(max(f.pointSizeF() + 1.0, 10.0))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(ACCENT))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Loading…")
        p.end()


class MarketTable(QTableWidget):
    """QTableWidget with aurantium defaults, a loading overlay, and CSV export."""

    def __init__(
        self, rows: int = 0, cols: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(rows, cols, parent)
        self._loading = False
        self._column_menu = False

        # -- shared table conventions (previously copied into every panel) --
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setHighlightSections(False)

        # -- right-click CSV export -----------------------------------------
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # -- loading overlay (child of the viewport, see module docstring) --
        self._overlay = _LoadingOverlay(self.viewport())
        # track viewport resizes too — a scrollbar appearing/disappearing
        # resizes the viewport without resizing the table widget itself.
        self.viewport().installEventFilter(self)

    # -- loading state ------------------------------------------------------

    def set_loading(self, loading: bool) -> None:
        """Show/hide the 'Loading…' overlay. Idempotent."""
        loading = bool(loading)
        if loading == self._loading:
            return
        self._loading = loading
        if loading:
            self._overlay.setGeometry(self.viewport().rect())
            self._overlay.show()
            self._overlay.raise_()
        else:
            self._overlay.hide()

    @property
    def is_loading(self) -> bool:
        return self._loading

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        if self._loading:
            self._overlay.setGeometry(self.viewport().rect())

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if (
            self._loading
            and obj is self.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._overlay.setGeometry(self.viewport().rect())
        return super().eventFilter(obj, event)

    # -- sorting ------------------------------------------------------------

    def enable_sorting(
        self,
        default_column: int | None = None,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        """Turn on click-to-sort headers. Panels should populate numeric
        columns with :class:`NumericTableWidgetItem` so those sort as numbers,
        and wrap bulk (re)population in :meth:`bulk_update` so rows aren't
        re-sorted on every insert."""
        self.setSortingEnabled(True)
        self.horizontalHeader().setSortIndicatorShown(True)
        if default_column is not None:
            self.sortByColumn(default_column, order)

    @contextmanager
    def bulk_update(self):
        """Context manager that suspends sorting while a panel rebuilds its
        rows, then restores it (Qt re-applies the active sort indicator on the
        rebuilt data). A no-op when sorting was never enabled."""
        was = self.isSortingEnabled()
        self.setSortingEnabled(False)
        try:
            yield
        finally:
            self.setSortingEnabled(was)

    # -- live substring filter ---------------------------------------------

    def apply_filter(self, text: str) -> None:
        """Hide rows whose visible cell text doesn't contain ``text`` (case-
        insensitive substring). Group-header rows — a single cell spanning
        every column — are shown only when at least one data row beneath them
        (up to the next header) survives the filter, so filtering a grouped
        monitor table hides now-empty section headers too."""
        needle = (text or "").strip().casefold()
        rows = self.rowCount()
        cols = self.columnCount()

        def is_header(r: int) -> bool:
            return cols > 1 and self.columnSpan(r, 0) >= cols

        def row_text(r: int) -> str:
            parts = [
                self.item(r, c).text()
                for c in range(cols)
                if self.item(r, c) is not None
            ]
            return " ".join(parts).casefold()

        data_visible: dict[int, bool] = {}
        for r in range(rows):
            if is_header(r):
                continue
            visible = (not needle) or (needle in row_text(r))
            data_visible[r] = visible
            self.setRowHidden(r, not visible)

        for r in range(rows):
            if not is_header(r):
                continue
            keep = False
            for rr in range(r + 1, rows):
                if is_header(rr):
                    break
                if data_visible.get(rr, False):
                    keep = True
                    break
            self.setRowHidden(r, not keep)

    # -- column show/hide ---------------------------------------------------

    def enable_column_menu(self) -> None:
        """Add a "Columns" submenu to the right-click menu, letting the user
        toggle individual columns. Persist via :meth:`hidden_columns` /
        :meth:`set_hidden_columns` in the panel's ``settings``/``restore``."""
        self._column_menu = True

    def hidden_columns(self) -> list[int]:
        return [c for c in range(self.columnCount()) if self.isColumnHidden(c)]

    def set_hidden_columns(self, cols) -> None:
        wanted = {int(c) for c in cols} if isinstance(cols, (list, tuple, set)) else set()
        for c in range(self.columnCount()):
            self.setColumnHidden(c, c in wanted)

    # -- panel-provided row actions -----------------------------------------

    def set_row_actions(self, provider) -> None:
        """Let the owning panel prepend quick actions to the right-click
        menu. ``provider(row)`` (row is -1 outside any row) returns a list
        of ``(text, callable)`` pairs shown above the built-in entries —
        the hook behind "Remove …" / "Add…" / "Edit panel…" on
        configurable panels."""
        self._row_actions = provider

    # -- CSV export ---------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)

        quick_actions: dict = {}
        provider = getattr(self, "_row_actions", None)
        if provider is not None:
            index = self.indexAt(pos)
            row = index.row() if index.isValid() else -1
            for text, slot in provider(row) or []:
                quick_actions[menu.addAction(text)] = slot
            if quick_actions:
                menu.addSeparator()

        export_act = menu.addAction("Export Table to CSV…")

        col_actions: dict = {}
        if self._column_menu and self.columnCount() > 1:
            cols_menu = menu.addMenu("Columns")
            for c in range(self.columnCount()):
                hdr = self.horizontalHeaderItem(c)
                label = hdr.text() if hdr is not None else f"Column {c + 1}"
                act = cols_menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(not self.isColumnHidden(c))
                col_actions[act] = c

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen in quick_actions:
            quick_actions[chosen]()
        elif chosen is export_act:
            self._export_csv()
        elif chosen in col_actions:
            self._toggle_column(col_actions[chosen], chosen.isChecked())

    def _toggle_column(self, col: int, want_visible: bool) -> None:
        if not want_visible:
            visible = [
                c for c in range(self.columnCount()) if not self.isColumnHidden(c)
            ]
            if len(visible) <= 1:
                return  # never hide the last visible column
        self.setColumnHidden(col, not want_visible)

    def _export_csv(self) -> None:
        rows, cols = self.rowCount(), self.columnCount()
        if rows == 0 or cols == 0:
            QMessageBox.information(
                self, "Export Table to CSV", "The table is empty — nothing to export."
            )
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Table to CSV",
            "table.csv",
            "CSV files (*.csv);;All files (*)",
        )
        if not path:
            return

        headers = []
        for c in range(cols):
            item = self.horizontalHeaderItem(c)
            headers.append(item.text() if item is not None else f"Column {c + 1}")

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow(headers)
                for r in range(rows):
                    cells = [self.item(r, c) for c in range(cols)]
                    writer.writerow(
                        [cell.text() if cell is not None else "" for cell in cells]
                    )
        except OSError as exc:
            QMessageBox.warning(
                self, "Export Table to CSV", f"Couldn't write the file:\n{exc}"
            )
            return
