"""ListEditorDialog — the one Edit… dialog behind every configurable panel.

Panels describe their editable lists declaratively (sections → typed columns →
rows) and get a consistent, themed editor. Rows render as a clean list —
drag-grip to reorder, inline editors per column, live validation status, a
hover ✕ to delete — instead of spreadsheet cells. New rows come from a
type-ahead picker over a curated :mod:`symbol_catalog` slice (plain-English
search, free-text fallback), and per-section preset chips add common setups
in one click. Each section explains what it feeds in one plain sentence.

Typical use::

    sections = [
        EditorSection(
            key="energy", title="Energy",
            columns=[EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
            rows=self._energy,
            description="Live quotes for each energy future in the table.",
            catalog=commodity_entries(),
            presets=[("Oil complex", [["WTI Crude", "CL=F"], ["Brent Crude", "BZ=F"]])],
        ),
        ...,
    ]
    result = open_list_editor(self, "Edit Commodities", sections)
    if result is not None:
        self._energy = result["energy"] or self._energy

The public contract is unchanged from the previous spreadsheet editor:
``open_list_editor`` returns the edited rows per section key (typed per
column), or ``None`` on cancel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..datahub import DataHub
from ..theme import ACCENT, CHROME_LO, DOWN, FG_DIM, MONO_FONT, UP
from .symbol_catalog import CatalogEntry, search_catalog

_ROW_MIME = "application/x-aurantium-editor-row"


@dataclass
class EditorColumn:
    """One column of an editable list.

    kind:
      - ``"text"``   — free text (labels)
      - ``"symbol"`` — free text, auto-uppercased (ticker symbols / codes)
      - ``"number"`` — QDoubleSpinBox editor
      - ``"choice"`` — QComboBox over ``choices`` ``(value, label)`` pairs;
        the label is displayed, the value is stored/returned
    """

    title: str
    kind: str = "text"
    choices: list[tuple[Any, str]] = field(default_factory=list)
    decimals: int = 2
    minimum: float = 0.0
    maximum: float = 1e9
    placeholder: str = ""

    def label_for(self, value: Any) -> str:
        for v, label in self.choices:
            if v == value:
                return label
        return str(value)


@dataclass
class EditorSection:
    """One editable list (rendered as a tab when the dialog has several).

    ``description`` is a one-line plain-English sentence shown under the
    section title (what this list feeds). ``catalog`` powers the "+ Add"
    type-ahead picker; without one, Add appends a blank row. ``presets``
    are (chip label, rows) one-click additions. ``row_factory`` maps a
    picked :class:`CatalogEntry` to a row when the default mapping (label →
    first text column, code → symbol column, kind → choice column) isn't
    right — e.g. yield-curve rows that also need a maturity."""

    key: str
    title: str
    columns: list[EditorColumn]
    rows: list  # list of row lists; choice cells hold the stored value
    hint: str = ""
    description: str = ""
    catalog: list[CatalogEntry] = field(default_factory=list)
    presets: list[tuple[str, list]] = field(default_factory=list)
    row_factory: Optional[Callable[[CatalogEntry], list]] = None


def _default_row_from_entry(section: EditorSection, entry: CatalogEntry) -> list:
    values: list = []
    label_used = False
    for col in section.columns:
        if col.kind == "text" and not label_used:
            values.append(entry.label)
            label_used = True
        elif col.kind == "symbol":
            values.append(entry.code)
        elif col.kind == "choice":
            stored = [v for v, _label in col.choices]
            values.append(entry.kind if entry.kind in stored else (stored[0] if stored else None))
        elif col.kind == "number":
            values.append(0.0)
        else:
            values.append("")
    return values


# --------------------------------------------------------------------------
# Add picker
# --------------------------------------------------------------------------

class _AddPickerDialog(QDialog):
    """Type-ahead picker over a catalog slice, with free-text fallback."""

    def __init__(
        self,
        catalog: list[CatalogEntry],
        allow_free_text: bool,
        title: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 360)
        self._catalog = catalog
        self._allow_free = allow_free_text
        self.chosen: Optional[CatalogEntry] = None

        layout = QVBoxLayout(self)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Search — plain name or code…")
        self.search.textChanged.connect(self._refresh)
        layout.addWidget(self.search)

        self.results = QListWidget(self)
        self.results.itemActivated.connect(self._accept_item)
        layout.addWidget(self.results, 1)

        hint = QLabel("Enter picks the highlighted match.", self)
        hint.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
        layout.addWidget(hint)

        self.search.returnPressed.connect(self._accept_current)
        self._refresh("")
        self.search.setFocus()

    def _refresh(self, text: str) -> None:
        self.results.clear()
        for entry in search_catalog(self._catalog, text, limit=40):
            item = QListWidgetItem(f"{entry.label}   ·   {entry.code}")
            item.setToolTip(f"{entry.category} — {entry.code}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(item)
        free = (text or "").strip()
        if self._allow_free and free:
            code = free.upper()
            entry = CatalogEntry(code, code, "quote", "Custom")
            item = QListWidgetItem(f'Add "{code}" as a custom symbol')
            item.setForeground(self.palette().text())
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _accept_item(self, item: QListWidgetItem) -> None:
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def _accept_current(self) -> None:
        item = self.results.currentItem()
        if item is not None:
            self._accept_item(item)


def open_add_picker(
    parent,
    catalog: list[CatalogEntry],
    *,
    allow_free_text: bool = True,
    title: str = "Add…",
) -> Optional[CatalogEntry]:
    """Run the type-ahead picker modally; the picked entry, or None."""
    dlg = _AddPickerDialog(catalog, allow_free_text, title, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.chosen
    return None


# --------------------------------------------------------------------------
# Row widgets
# --------------------------------------------------------------------------

class _Grip(QLabel):
    """Drag handle — starts an internal row drag; the list reorders on drop."""

    def __init__(self, row_widget: "_RowWidget", parent=None) -> None:
        super().__init__("⠿", parent)
        self._row_widget = row_widget
        self.setObjectName("secondary")
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag to reorder")

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        index = self._row_widget.current_index()
        if index < 0:
            return
        mime = QMimeData()
        mime.setData(_ROW_MIME, str(index).encode())
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)


class _RowWidget(QWidget):
    """One editable row: grip · per-column editors · status · delete."""

    changed = Signal()
    delete_requested = Signal(object)  # self

    def __init__(self, section: EditorSection, values: list, parent=None) -> None:
        super().__init__(parent)
        self._section = section
        self._editors: list[QWidget] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)
        layout.addWidget(_Grip(self, self))

        mono = QFont(MONO_FONT)
        for c, col in enumerate(section.columns):
            value = values[c] if c < len(values) else (
                col.choices[0][0] if col.kind == "choice" and col.choices else ""
            )
            editor: QWidget
            if col.kind == "choice":
                combo = QComboBox(self)
                for v, label in col.choices:
                    combo.addItem(label, v)
                pos = combo.findData(value)
                combo.setCurrentIndex(pos if pos >= 0 else 0)
                combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
                editor = combo
            elif col.kind == "number":
                spin = QDoubleSpinBox(self)
                spin.setDecimals(col.decimals)
                spin.setRange(col.minimum, col.maximum)
                try:
                    spin.setValue(float(value))
                except (TypeError, ValueError):
                    spin.setValue(0.0)
                spin.valueChanged.connect(lambda *_: self.changed.emit())
                spin.setFixedWidth(86)
                editor = spin
            else:
                edit = QLineEdit("" if value is None else str(value), self)
                edit.setPlaceholderText(col.placeholder or col.title)
                if col.kind == "symbol":
                    edit.setFont(mono)
                    edit.editingFinished.connect(
                        lambda e=edit: e.setText(e.text().strip().upper())
                    )
                edit.textChanged.connect(lambda *_: self.changed.emit())
                editor = edit
            editor.setToolTip(col.title)
            self._editors.append(editor)
            stretch = 2 if col.kind in ("text", "symbol") else 0
            layout.addWidget(editor, stretch)

        self.status = QLabel("", self)
        self.status.setFixedWidth(14)
        layout.addWidget(self.status)

        delete = QToolButton(self)
        delete.setText("✕")
        delete.setToolTip("Remove this row")
        delete.setAutoRaise(True)
        delete.setStyleSheet(
            f"QToolButton {{ color: {FG_DIM}; border: none; }}"
            f"QToolButton:hover {{ color: {DOWN}; }}"
        )
        delete.clicked.connect(lambda: self.delete_requested.emit(self))
        layout.addWidget(delete)

    # -- data -----------------------------------------------------------------

    def current_index(self) -> int:
        parent = self.parent()
        while parent is not None and not isinstance(parent, _RowList):
            parent = parent.parent()
        if isinstance(parent, _RowList):
            return parent.index_of_widget(self)
        return -1

    def values(self) -> list:
        out: list = []
        for col, editor in zip(self._section.columns, self._editors):
            if col.kind == "choice":
                out.append(editor.currentData())
            elif col.kind == "number":
                out.append(float(editor.value()))
            elif col.kind == "symbol":
                out.append(editor.text().strip().upper())
            else:
                out.append(editor.text().strip())
        return out

    def structural_error(self) -> Optional[str]:
        """A blocking problem with this row, or None. Blank leading labels
        are fine when a later cell can stand in for them (see results())."""
        values = self.values()
        cols = self._section.columns
        for col, value in zip(cols, values):
            if col.kind == "symbol" and not value:
                return f"{col.title} is required"
            if col.kind == "choice" and value is None:
                return f"pick a {col.title}"
        if cols and cols[0].kind == "text" and not values[0]:
            fallback = next((v for v in values[1:] if v not in (None, "")), None)
            if fallback is None:
                return f"{cols[0].title} is required"
        return None

    def set_status(self, glyph: str, color: str, tooltip: str = "") -> None:
        self.status.setText(glyph)
        self.status.setStyleSheet(f"color: {color};")
        self.status.setToolTip(tooltip)

    def symbol_to_validate(self) -> Optional[str]:
        """The quote symbol this row should be checked against, or None
        (no symbol column, blank, or the row is a FRED series)."""
        values = self.values()
        symbol: Optional[str] = None
        for col, value in zip(self._section.columns, values):
            if col.kind == "choice" and value == "fred":
                return None
            if col.kind == "symbol" and symbol is None:
                symbol = value or None
        return symbol


class _RowList(QListWidget):
    """The rows, with grip-initiated internal drag reordering."""

    reorder = Signal(int, int)  # from_index, to_index

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)

    def index_of_widget(self, widget: QWidget) -> int:
        for i in range(self.count()):
            if self.itemWidget(self.item(i)) is widget:
                return i
        return -1

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_ROW_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(_ROW_MIME):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not event.mimeData().hasFormat(_ROW_MIME):
            return
        source = int(bytes(event.mimeData().data(_ROW_MIME)).decode())
        item = self.itemAt(event.position().toPoint())
        target = self.row(item) if item is not None else self.count() - 1
        event.acceptProposedAction()
        if source != target:
            self.reorder.emit(source, target)


# --------------------------------------------------------------------------
# Section widget
# --------------------------------------------------------------------------

class _SectionWidget(QWidget):
    """One section: description, preset chips, row list, and Add."""

    validity_changed = Signal()

    def __init__(self, section: EditorSection, parent=None) -> None:
        super().__init__(parent)
        self._section = section
        self._rows: list[_RowWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)

        description = section.description or section.hint
        if description:
            desc = QLabel(description, self)
            desc.setWordWrap(True)
            desc.setObjectName("secondary")
            layout.addWidget(desc)

        if section.presets:
            chips = QHBoxLayout()
            chips.setSpacing(6)
            for i, (label, _rows) in enumerate(section.presets):
                chip = QPushButton(label, self)
                chip.setToolTip("Add this preset's rows")
                chip.setStyleSheet(
                    f"QPushButton {{ border: 1px solid {CHROME_LO}; border-radius: 8px;"
                    f" padding: 2px 10px; color: {FG_DIM}; background: transparent; }}"
                    f"QPushButton:hover {{ color: {ACCENT}; border-color: {ACCENT}; }}"
                )
                chip.clicked.connect(lambda _=False, idx=i: self.apply_preset(idx))
                chips.addWidget(chip)
            chips.addStretch(1)
            layout.addLayout(chips)

        self.list = _RowList(self)
        self.list.reorder.connect(self.move_row)
        layout.addWidget(self.list, 1)

        add = QPushButton("+ Add…", self)
        add.clicked.connect(self._on_add)
        layout.addWidget(add)

        self.error_lbl = QLabel("", self)
        self.error_lbl.setStyleSheet(f"color: {DOWN}; font-size: 10px;")
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        for row in section.rows:
            self._append_row(list(row))
        self._revalidate()

    # -- row management -------------------------------------------------------

    def _append_row(self, values: list) -> _RowWidget:
        widget = _RowWidget(self._section, values, self.list)
        widget.changed.connect(self._revalidate)
        widget.delete_requested.connect(self._on_delete)
        item = QListWidgetItem(self.list)
        item.setSizeHint(widget.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)
        self._rows.append(widget)
        self._start_symbol_check(widget)
        return widget

    def add_row(self, values: list) -> None:
        self._append_row(values)
        self._revalidate()

    def remove_row(self, index: int) -> None:
        if not (0 <= index < len(self._rows)):
            return
        widget = self._rows.pop(index)
        self.list.takeItem(index)
        widget.deleteLater()
        self._revalidate()

    def move_row(self, source: int, target: int) -> None:
        if source == target or not (
            0 <= source < len(self._rows) and 0 <= target < len(self._rows)
        ):
            return
        data = [w.values() for w in self._rows]
        data.insert(target, data.pop(source))
        self._rebuild(data)

    def _rebuild(self, data: list[list]) -> None:
        for widget in self._rows:
            widget.deleteLater()
        self._rows.clear()
        self.list.clear()
        for values in data:
            self._append_row(values)
        self._revalidate()

    def _on_delete(self, widget: _RowWidget) -> None:
        index = self._rows.index(widget) if widget in self._rows else -1
        if index >= 0:
            self.remove_row(index)

    def _on_add(self) -> None:
        if self._section.catalog:
            entry = open_add_picker(
                self,
                self._section.catalog,
                title=f"Add to {self._section.title}",
            )
            if entry is None:
                return
            factory = self._section.row_factory or (
                lambda e: _default_row_from_entry(self._section, e)
            )
            self.add_row(factory(entry))
        else:
            widget = self._append_row([])
            self._revalidate()
            if widget._editors:
                widget._editors[0].setFocus()

    def apply_preset(self, index: int) -> None:
        if not (0 <= index < len(self._section.presets)):
            return
        existing = [w.values() for w in self._rows]
        for row in self._section.presets[index][1]:
            if list(row) not in existing:
                self._append_row(list(row))
        self._revalidate()

    # -- async symbol validation ----------------------------------------------

    def _start_symbol_check(self, widget: _RowWidget) -> None:
        symbol = widget.symbol_to_validate()
        if not symbol:
            widget.set_status("", FG_DIM)
            return
        rows = self._rows  # liveness check against the current list

        def on_data(data: Any, w=widget) -> None:
            if w in rows and isinstance(data, dict) and data.get("price") is not None:
                w.set_status("✓", UP, "Symbol verified")

        def on_error(error: str, w=widget) -> None:
            if w in rows and not w.status.text():
                w.set_status("✗", DOWN, f"Could not verify: {error}")

        window = self.window()
        DataHub.instance().subscribe(window, f"quote:{symbol}", on_data, on_error)

    # -- results ---------------------------------------------------------------

    def first_error(self) -> Optional[str]:
        for i, widget in enumerate(self._rows, start=1):
            error = widget.structural_error()
            if error:
                return f"Row {i}: {error}"
        return None

    def _revalidate(self) -> None:
        error = self.first_error()
        self.error_lbl.setText(error or "")
        self.error_lbl.setVisible(bool(error))
        self.validity_changed.emit()

    def rows(self) -> list:
        """The edited rows, typed per column. A blank leading label borrows
        the first non-empty later cell (symbols read fine as their own
        label) — same contract as the previous editor."""
        out: list = []
        cols = self._section.columns
        for widget in self._rows:
            if widget.structural_error() is not None:
                continue  # OK is disabled while any of these exist
            values = widget.values()
            if cols and cols[0].kind == "text" and not values[0]:
                values[0] = str(
                    next((v for v in values[1:] if v not in (None, "")), "")
                )
            out.append(values)
        return out


# --------------------------------------------------------------------------
# Dialog
# --------------------------------------------------------------------------

class ListEditorDialog(QDialog):
    """Tabbed (or single-section) themed editor over typed row lists."""

    def __init__(self, title: str, sections: list[EditorSection], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 470)
        self._widgets: dict[str, _SectionWidget] = {}

        layout = QVBoxLayout(self)
        if len(sections) == 1:
            w = _SectionWidget(sections[0], self)
            self._widgets[sections[0].key] = w
            layout.addWidget(w, 1)
        else:
            tabs = QTabWidget(self)
            for section in sections:
                w = _SectionWidget(section, tabs)
                self._widgets[section.key] = w
                tabs.addTab(w, section.title)
            layout.addWidget(tabs, 1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        for w in self._widgets.values():
            w.validity_changed.connect(self._update_ok)
        self._update_ok()
        self.finished.connect(
            lambda *_: DataHub.instance().unsubscribe_all(self)
        )

    def _update_ok(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(all(w.first_error() is None for w in self._widgets.values()))

    def ok_enabled(self) -> bool:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        return bool(ok is not None and ok.isEnabled())

    def section_widget(self, key: str) -> _SectionWidget:
        return self._widgets[key]

    def results(self) -> dict[str, list]:
        return {key: w.rows() for key, w in self._widgets.items()}


def open_list_editor(
    parent, title: str, sections: list[EditorSection]
) -> Optional[dict[str, list]]:
    """Run the editor modally; the edited rows per section key, or None on
    cancel."""
    dlg = ListEditorDialog(title, sections, parent)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return dlg.results()
    return None
