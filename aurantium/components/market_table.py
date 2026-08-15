"""MarketTable — the shared table widget used by aurantium's data panels.

A thin ``QTableWidget`` subclass that bakes in the terminal's table conventions
(read-only, row selection, no vertical header, zebra striping, no grid) and adds
the things every data panel wants:

* a **loading overlay** — ``set_loading(True)`` dims the table and shows a
  "Loading…" indicator while a fetch is in flight;
* an **empty state** — when there are no rows (or a filter hid them all) the
  table says what's missing instead of showing a blank rectangle. Panels name
  their own case via ``set_empty_text``; it is kept in sync automatically from
  the model's own row signals, so panels never have to remember to call it;
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

from PySide6.QtCore import QEasingCurve, QEvent, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .. import motion
from ..theme import ACCENT, tick_color
from .empty_state import EmptyState


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


#: Tick-flash decay, ms. Short: it must read as "that one just moved" and be
#: gone before the eye tracks it as motion.
FLASH_MS = 180
#: Frame interval for the shared decay timer (~60fps).
_FLASH_TICK_MS = 16
#: Peak alpha of the tint. Low enough to sit under a price all day without
#: fighting the zebra striping or the selected-row highlight.
_FLASH_ALPHA = 90
#: The app's ease-out, applied to the decay so the flash matches every other
#: animation's curve instead of falling off linearly.
_DECAY = QEasingCurve(motion.EASE_OUT)


class MarketTable(QTableWidget):
    """QTableWidget with aurantium defaults, loading + empty overlays, a
    tick flash, and CSV export."""

    def __init__(
        self, rows: int = 0, cols: int = 0, parent: QWidget | None = None
    ) -> None:
        super().__init__(rows, cols, parent)
        self._loading = False
        self._column_menu = False
        self._empty_title = "No data"
        self._empty_hint = ""
        self._filter_text = ""
        # responsive columns (opt in via set_column_priority)
        self._keep_columns: list[int] = []
        self._droppable_columns: list[int] = []
        self._user_hidden: set[int] = set()
        self._stretch_column: int | None = None

        # -- tick flash: list of [item, intensity 0..1, QColor] --------------
        # One shared timer decays every flashing cell rather than one animation
        # per cell — a busy watchlist can tick a dozen cells at once, and a
        # dozen QPropertyAnimations to tint a dozen table cells is the wrong
        # order of cost for the effect.
        #
        # Each entry is [item, intensity, color, progress]. A list, not a dict
        # keyed by item: QTableWidgetItem is unhashable.
        self._flashes: list = []
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(_FLASH_TICK_MS)
        self._flash_timer.timeout.connect(self._step_flashes)

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
        # Scroll by pixel, not by row. Qt's default snaps a whole row per wheel
        # notch, which in a dense table reads as the view lurching rather than
        # moving. Costs nothing and needs no motion budget — there is nothing
        # to time and nothing to disable under reduced motion.
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # -- right-click CSV export -----------------------------------------
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # -- overlays (children of the viewport, see module docstring) ------
        self._overlay = _LoadingOverlay(self.viewport())
        self._empty = EmptyState(self.viewport())
        # track viewport resizes too — a scrollbar appearing/disappearing
        # resizes the viewport without resizing the table widget itself.
        self.viewport().installEventFilter(self)

        # Re-evaluate the empty state whenever the rows change, wherever the
        # change came from (setRowCount, insertRow, clearContents, a sort, a
        # model reset) — panels shouldn't have to remember to call anything.
        model = self.model()
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.modelReset,
            model.layoutChanged,
        ):
            signal.connect(self._sync_empty)
        self._sync_empty()  # a table built empty says so from the first paint

    # -- loading state ------------------------------------------------------

    def set_loading(self, loading: bool) -> None:
        """Show/hide the 'Loading…' overlay. Idempotent."""
        loading = bool(loading)
        if loading == self._loading:
            return
        self._loading = loading
        if loading:
            # Instant in: this is the app confirming the fetch started, and any
            # delay on that reads as lag.
            self._overlay.setGeometry(self.viewport().rect())
            motion.fade(self._overlay, 1.0, ms=0)
            self._overlay.raise_()
        else:
            # Fade out: the veil lifting off the data it was covering. A hard
            # cut here makes rows appear to snap into place.
            motion.fade(self._overlay, 0.0, hide_when_done=True)
        self._sync_empty()  # "Loading…" and "No data" must never both show

    @property
    def is_loading(self) -> bool:
        return self._loading

    # -- tick flash ---------------------------------------------------------

    def flash_cell(self, item, direction) -> None:
        """Briefly tint ``item``'s background up- or down-colored, then decay.

        This is the terminal convention, and it carries information rather than
        decoration: in a dense table a price that silently mutates gives no clue
        that it moved at all, let alone which of twenty rows moved. The flash
        answers "what just changed?" — the one question the table can't
        otherwise answer.

        It stays legitimate by staying a *tint*: nothing moves, nothing
        resizes, and the number itself is never obscured, so a value being read
        is never disturbed. The color comes from the theme's up/down pair, so
        the color-blind palette applies here automatically.

        ``direction`` is a signed number (or None to skip).
        """
        if item is None or direction is None:
            return
        if not motion.animations_enabled():
            return  # the state is already in the text; the tint is the extra
        color = QColor(tick_color(direction))
        for entry in self._flashes:
            if entry[0] is item:  # already flashing — retarget, don't stack
                entry[1], entry[2], entry[3] = 1.0, color, 0.0
                break
        else:
            self._flashes.append([item, 1.0, color, 0.0])
        if not self._flash_timer.isActive():
            self._flash_timer.start()

    def _step_flashes(self) -> None:
        """Advance every live flash one frame.

        Progress runs linearly; the *intensity* comes off the house ease-out
        curve, so the tint is full on the frame the eye lands on and drops away
        fast. A linear intensity ramp — what this used to do — falls off at a
        constant rate, which reads as the flash lingering half-lit and then
        cutting out.
        """
        step = _FLASH_TICK_MS / FLASH_MS
        alive = []
        for entry in self._flashes:
            item, color = entry[0], entry[2]
            entry[3] = progress = entry[3] + step
            intensity = 0.0 if progress >= 1.0 else 1.0 - _DECAY.valueForProgress(progress)
            entry[1] = intensity
            try:
                if intensity <= 0:
                    # clear to the default brush so the row's zebra stripe and
                    # selection highlight come back
                    item.setBackground(QBrush())
                else:
                    tint = QColor(color)
                    tint.setAlpha(int(_FLASH_ALPHA * intensity))
                    item.setBackground(QBrush(tint))
                    alive.append(entry)
            except RuntimeError:
                pass  # the row was rebuilt out from under us; drop the entry
        self._flashes = alive
        if not self._flashes:
            self._flash_timer.stop()

    # -- empty state --------------------------------------------------------

    def set_empty_text(self, title: str, hint: str = "") -> None:
        """What this table says when it has no rows.

        Panels should name the actual situation — ``"No symbol selected"`` /
        ``"Click a ticker in any linked panel"`` beats a generic "No data",
        which is the fallback.
        """
        self._empty_title = title or "No data"
        self._empty_hint = hint
        self._sync_empty()

    def _visible_row_count(self) -> int:
        return sum(
            1 for r in range(self.rowCount()) if not self.isRowHidden(r)
        )

    def _sync_empty(self, *args) -> None:
        """Show the empty message only when there is genuinely nothing to look
        at — not while a fetch is in flight (the loading veil owns that moment),
        and with a filter-specific message when the rows exist but are all
        filtered out."""
        if self._loading or self._visible_row_count():
            # isHidden(), not isVisible(): isVisible() is False whenever any
            # ancestor is hidden, so during layout restore — every panel built
            # before the window is shown — the fade-out would never run and the
            # message would still be sitting there when the window appeared.
            if not self._empty.isHidden():
                # Rows arriving is the moment the message stops being true;
                # crossfading it out reads as the data replacing it rather than
                # as the panel blinking.
                motion.fade(self._empty, 0.0, hide_when_done=True)
            return
        if self._filter_text and self.rowCount():
            self._empty.set_text(
                f"No rows match “{self._filter_text}”",
                "Clear the filter to see everything",
            )
        else:
            self._empty.set_text(self._empty_title, self._empty_hint)
        self._empty.setGeometry(self.viewport().rect())
        self._empty.raise_()
        self._empty.update()
        motion.fade(self._empty, 1.0)

    def _resize_overlays(self) -> None:
        # Both unconditionally: the resizes that matter happen while the window
        # is still being laid out, when isVisible() is still False everywhere —
        # gating on it leaves an overlay stuck at its initial geometry.
        rect = self.viewport().rect()
        self._overlay.setGeometry(rect)
        self._empty.setGeometry(rect)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._resize_overlays()
        self._sync_columns()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        if obj is self.viewport() and event.type() == QEvent.Type.Resize:
            self._resize_overlays()
            self._sync_columns()
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
        self._filter_text = (text or "").strip()
        needle = self._filter_text.casefold()
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

        self._sync_empty()  # filtering everything away is its own empty state

    # -- responsive columns -------------------------------------------------

    def set_column_priority(self, keep, droppable, stretch=None) -> None:
        """Declare which columns survive a narrow panel and in what order the
        rest are given up.

        ``keep`` is the row's reason to exist — the symbol and its price — and
        is never hidden however narrow the panel gets. ``droppable`` is ordered
        least-important-first: those columns are dropped one at a time until
        the remaining ones fit, and restored as the panel widens.

        ``stretch`` is the column that absorbs leftover width on a wide panel
        — the text one, so numbers stay tight under their headers. Defaults to
        the first ``keep`` column, which is the name or symbol in every panel
        here.

        Opt-in. A table that never calls this keeps Qt's default behaviour of
        squeezing every column, which is right for panels whose columns are all
        equally load-bearing.
        """
        self._keep_columns = list(keep)
        self._droppable_columns = list(droppable)
        self._stretch_column = (
            stretch if stretch is not None
            else (self._keep_columns[0] if self._keep_columns else None)
        )

        # Size columns to their content rather than stretching them to fill.
        # Stretching every column meant that on a wide panel a value floated a
        # couple of hundred pixels from its own header — the eye can no longer
        # pair them, and a density-first terminal shouldn't spend space that
        # way. Columns now pack left and any slack is left blank at the right,
        # which is what real terminal tables do. Narrow panels are handled by
        # dropping columns below, not by squeezing these.
        self._enforce_content_sizing()
        self._sync_columns()

    def _enforce_content_sizing(self) -> None:
        """Numeric columns sized to their content; the name column takes the
        slack.

        Two failure modes to avoid, and they pull in opposite directions.
        Stretching *every* column pushed a value a couple of hundred pixels
        from its own header. Sizing every column to its content fixed that but
        left a wide panel mostly empty, with the zebra striping and the
        selected-row highlight stopping short of the edge — which reads as a
        broken table. Growing only the text column does both: the numbers stay
        tight and aligned under their headers, and the row still spans the
        panel. It is also what terminal tables have always looked like.

        Re-asserted on every resize rather than set once, because several
        panels call ``setSectionResizeMode(Stretch)`` *after* declaring their
        priorities and silently restored the sprawl. Making the table
        authoritative means the order of those two calls in a panel's
        ``build()`` stops being a trap. Each mode is only written when it
        differs, so this doesn't churn the header on every resize event.
        """
        header = self.horizontalHeader()
        if header.stretchLastSection():
            header.setStretchLastSection(False)
        grow = self._stretch_column
        for col in range(self.columnCount()):
            wanted = (
                QHeaderView.ResizeMode.Stretch
                if col == grow
                else QHeaderView.ResizeMode.ResizeToContents
            )
            if header.sectionResizeMode(col) != wanted:
                header.setSectionResizeMode(col, wanted)

    def _natural_width(self, columns) -> int:
        """Width these columns need before anything has to elide."""
        header = self.horizontalHeader()
        return sum(max(self.sizeHintForColumn(c), header.sectionSizeHint(c))
                   for c in columns)

    def _sync_columns(self) -> None:
        """Show as many columns as fit, dropping from the least important end.

        Skips any column the user turned off via the right-click Columns menu —
        responsive layout and an explicit user choice must not fight, and the
        user's choice wins.
        """
        if not self._droppable_columns:
            return
        self._enforce_content_sizing()
        available = self.viewport().width()
        candidates = [
            c for c in self._droppable_columns if c not in self._user_hidden
        ]
        shown = list(self._keep_columns) + candidates
        # give up the least important column until the rest fit
        while len(candidates) and self._natural_width(shown) > available:
            dropped = candidates.pop(0)
            shown.remove(dropped)
        for col in self._droppable_columns:
            want = col in shown and col not in self._user_hidden
            if self.isColumnHidden(col) == want:
                self.setColumnHidden(col, not want)

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
        # Remember it as a deliberate choice so widening the panel doesn't
        # bring back a column the user turned off.
        if want_visible:
            self._user_hidden.discard(col)
        else:
            self._user_hidden.add(col)
        self.setColumnHidden(col, not want_visible)
        self._sync_columns()

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
