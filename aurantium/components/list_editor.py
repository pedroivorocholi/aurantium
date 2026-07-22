"""ListEditorDialog — the one Edit… dialog behind every configurable panel.

Panels describe their editable lists declaratively (sections → typed columns →
rows) and get a consistent, themed editor: a real table with inline cell
editing, add/remove/reorder buttons, dropdowns for constrained values, and a
spin box for numbers — no raw text syntax anywhere. Multiple sections render
as tabs (same amber-underline style as the Portfolio panel's inner tabs).

Typical use::

    sections = [
        EditorSection(
            key="energy", title="Energy",
            columns=[EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")],
            rows=self._energy,
            hint="Any Yahoo Finance symbol works — CL=F, BZ=F, NG=F…",
        ),
        ...,
    ]
    result = open_list_editor(self, "Edit Commodities", sections)
    if result is not None:
        self._energy = result["energy"] or self._energy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..theme import FG_DIM

#: item data role holding a choice column's stored value (display shows label)
_VALUE_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class EditorColumn:
    """One column of an editable list.

    kind:
      - ``"text"``   — free text (labels)
      - ``"symbol"`` — free text, auto-uppercased (ticker symbols)
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

    def label_for(self, value: Any) -> str:
        for v, label in self.choices:
            if v == value:
                return label
        return str(value)


@dataclass
class EditorSection:
    """One editable list (rendered as a tab when the dialog has several)."""

    key: str
    title: str
    columns: list[EditorColumn]
    rows: list  # list of row lists; choice cells hold the stored value
    hint: str = ""


class _CellDelegate(QStyledItemDelegate):
    """Per-table delegate dispatching editors by the column's kind."""

    def __init__(self, columns: list[EditorColumn], parent=None) -> None:
        super().__init__(parent)
        self._columns = columns

    def createEditor(self, parent, option, index):  # noqa: N802 (Qt override)
        col = self._columns[index.column()]
        if col.kind == "choice":
            combo = QComboBox(parent)
            for value, label in col.choices:
                combo.addItem(label, value)
            return combo
        if col.kind == "number":
            spin = QDoubleSpinBox(parent)
            spin.setDecimals(col.decimals)
            spin.setRange(col.minimum, col.maximum)
            return spin
        return super().createEditor(parent, option, index)

    def setEditorData(self, editor, index):  # noqa: N802 (Qt override)
        col = self._columns[index.column()]
        if col.kind == "choice" and isinstance(editor, QComboBox):
            value = index.data(_VALUE_ROLE)
            pos = editor.findData(value)
            editor.setCurrentIndex(pos if pos >= 0 else 0)
            return
        if col.kind == "number" and isinstance(editor, QDoubleSpinBox):
            try:
                editor.setValue(float(index.data() or 0))
            except (TypeError, ValueError):
                editor.setValue(0)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):  # noqa: N802 (Qt override)
        col = self._columns[index.column()]
        if col.kind == "choice" and isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
            model.setData(index, editor.currentData(), _VALUE_ROLE)
            return
        if col.kind == "number" and isinstance(editor, QDoubleSpinBox):
            model.setData(index, f"{editor.value():g}")
            return
        if col.kind == "symbol" and isinstance(editor, QLineEdit):
            model.setData(index, editor.text().strip().upper())
            return
        super().setModelData(editor, model, index)


class _SectionWidget(QWidget):
    """One section: its table plus the add/remove/reorder toolbar and hint."""

    def __init__(self, section: EditorSection, parent=None) -> None:
        super().__init__(parent)
        self._section = section

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(6)

        self.table = QTableWidget(0, len(section.columns), self)
        self.table.setHorizontalHeaderLabels([c.title for c in section.columns])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self.table.horizontalHeader()
        for c in range(len(section.columns)):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.Stretch)
        self.table.setItemDelegate(_CellDelegate(section.columns, self.table))
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, tip, slot in (
            ("+ Add", "Add a row", self._add_row),
            ("− Remove", "Remove the selected row", self._remove_row),
            ("▲", "Move the selected row up", lambda: self._move_row(-1)),
            ("▼", "Move the selected row down", lambda: self._move_row(+1)),
        ):
            btn = QPushButton(text, self)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        if section.hint:
            hint = QLabel(section.hint, self)
            hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {FG_DIM}; font-size: 10px;")
            layout.addWidget(hint)

        for row in section.rows:
            self._append_row(list(row))

    # -- rows -----------------------------------------------------------------

    def _make_item(self, col: EditorColumn, value: Any) -> QTableWidgetItem:
        if col.kind == "choice":
            item = QTableWidgetItem(col.label_for(value))
            item.setData(_VALUE_ROLE, value)
        elif col.kind == "number":
            try:
                item = QTableWidgetItem(f"{float(value):g}")
            except (TypeError, ValueError):
                item = QTableWidgetItem("")
        else:
            item = QTableWidgetItem("" if value is None else str(value))
        return item

    def _append_row(self, values: list) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for c, col in enumerate(self._section.columns):
            value = values[c] if c < len(values) else (
                col.choices[0][0] if col.kind == "choice" and col.choices else ""
            )
            self.table.setItem(row, c, self._make_item(col, value))

    def _add_row(self) -> None:
        self._append_row([])
        row = self.table.rowCount() - 1
        self.table.selectRow(row)
        self.table.editItem(self.table.item(row, 0))

    def _remove_row(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _move_row(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.table.rowCount()):
            return
        cols = range(self.table.columnCount())
        for c in cols:
            a, b = self.table.takeItem(row, c), self.table.takeItem(target, c)
            self.table.setItem(row, c, b)
            self.table.setItem(target, c, a)
        self.table.selectRow(target)

    # -- results ---------------------------------------------------------------

    def rows(self) -> list:
        """The edited rows, typed per column. Rows with any empty non-label
        cell are dropped; an empty leading label falls back to the value of
        the first non-empty cell after it."""
        out: list = []
        cols = self._section.columns
        for r in range(self.table.rowCount()):
            values: list = []
            valid = True
            for c, col in enumerate(cols):
                item = self.table.item(r, c)
                text = (item.text() if item is not None else "").strip()
                if col.kind == "choice":
                    value = item.data(_VALUE_ROLE) if item is not None else None
                    if value is None:
                        valid = False
                    values.append(value)
                elif col.kind == "number":
                    try:
                        values.append(float(text))
                    except (TypeError, ValueError):
                        valid = False
                        values.append(None)
                elif col.kind == "symbol":
                    if not text:
                        valid = False
                    values.append(text.upper())
                else:
                    values.append(text)
            if not valid:
                continue
            # blank label → borrow the first non-empty later cell (symbols read
            # fine as their own label)
            if cols and cols[0].kind == "text" and not values[0]:
                fallback = next((str(v) for v in values[1:] if v not in (None, "")), "")
                if not fallback:
                    continue
                values[0] = fallback
            out.append(values)
        return out


class ListEditorDialog(QDialog):
    """Tabbed (or single-section) themed editor over typed row lists."""

    def __init__(self, title: str, sections: list[EditorSection], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(520, 430)
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
