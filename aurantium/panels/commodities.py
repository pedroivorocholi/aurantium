"""Commodities panel — Bloomberg GLCO clone: a grouped monitor table for
energy and metals futures, with bold group-header rows. Row click drives
linked panels; group headers are not selectable.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidgetItem,
)

from ..components import (
    EditorColumn,
    EditorSection,
    MarketTable,
    commodity_entries,
    make_filter_edit,
    open_add_picker,
    open_list_editor,
)
from ..panel import Panel, register_panel
from ..undo import UndoStack
from ..theme import ACCENT, BG_HEADER, FG_DIM, apply_tick

DEFAULT_ENERGY = [
    ["WTI", "CL=F"],
    ["Brent", "BZ=F"],
    ["Gasoline", "RB=F"],
    ["Heating Oil", "HO=F"],
    ["NatGas", "NG=F"],
]
DEFAULT_METALS = [
    ["Gold", "GC=F"],
    ["Silver", "SI=F"],
    ["Copper", "HG=F"],
    ["Platinum", "PL=F"],
    ["Aluminum", "ALI=F"],
]

COL_NAME, COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE = range(5)
HEADERS = ["Commodity", "Last", "Chg", "Chg%", "Range (1D)"]

ROW_KIND_HEADER = "header"
ROW_KIND_DATA = "data"


def _fmt_num(value: Any, decimals: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_range(low: Any, high: Any) -> str:
    if low is None and high is None:
        return "-"
    return f"{_fmt_num(low)} – {_fmt_num(high)}"


@register_panel(id="commodities", title="Commodities", category="Markets")
class CommoditiesPanel(Panel):
    def build(self) -> None:
        self._energy: list = [list(row) for row in DEFAULT_ENERGY]
        self._metals: list = [list(row) for row in DEFAULT_METALS]
        # row -> ("header", None) | ("data", symbol)
        self._row_kind: dict[int, tuple[str, str | None]] = {}
        self._row_of_symbol: dict[str, int] = {}

        self.table = MarketTable(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.ResizeToContents)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.enable_column_menu()
        self.table.set_row_actions(self._row_actions)

        self._filter = make_filter_edit(self.table, "Filter commodities…")
        self.content_layout.addWidget(self._filter)
        self.content_layout.addWidget(self.table, 1)

        edit_row = QHBoxLayout()
        edit_row.addStretch(1)
        edit_btn = QPushButton("Edit…", self)
        edit_btn.clicked.connect(self._open_edit_dialog)
        edit_row.addWidget(edit_btn)
        self.content_layout.addLayout(edit_row)

        self._rebuild_table()

    # -- table (re)construction ----------------------------------------------

    def _rebuild_table(self) -> None:
        """Rebuild all rows (group headers + data rows) and resubscribe all
        quote topics — mirrors watchlist.py's rebuild-on-change pattern."""
        self.unsubscribe_all()
        self.table.setRowCount(0)
        self._row_kind.clear()
        self._row_of_symbol.clear()

        self._append_group_header("Energy")
        for label, sym in self._energy:
            self._append_data_row(label, sym)

        self._append_group_header("Metals")
        for label, sym in self._metals:
            self._append_data_row(label, sym)

        if hasattr(self, "_filter"):
            self.table.apply_filter(self._filter.text())

        for _label, sym in self._energy + self._metals:
            self.subscribe(f"quote:{sym}", lambda data, s=sym: self._on_quote(s, data))

    def _append_group_header(self, text: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # not selectable
        item.setForeground(QColor(ACCENT))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setBackground(QColor(BG_HEADER))
        self.table.setItem(row, 0, item)
        self.table.setSpan(row, 0, 1, len(HEADERS))
        self._row_kind[row] = (ROW_KIND_HEADER, None)

    def _append_data_row(self, label: str, symbol: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        name_item = QTableWidgetItem(f"  {label}")
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, COL_NAME, name_item)
        for col in (COL_LAST, COL_CHG, COL_CHGPCT, COL_RANGE):
            item = QTableWidgetItem("-")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)
        self._row_kind[row] = (ROW_KIND_DATA, symbol)
        self._row_of_symbol[symbol] = row

    # -- data callbacks ----------------------------------------------------------

    def _on_quote(self, symbol: str, data: Any) -> None:
        row = self._row_of_symbol.get(symbol)
        if row is None or not isinstance(data, dict):
            return
        price = data.get("price")
        change = data.get("change")
        change_pct = data.get("change_pct")
        day_low = data.get("day_low")
        day_high = data.get("day_high")

        last_item = self.table.item(row, COL_LAST)
        chg_item = self.table.item(row, COL_CHG)
        pct_item = self.table.item(row, COL_CHGPCT)
        range_item = self.table.item(row, COL_RANGE)
        if not (last_item and chg_item and pct_item and range_item):
            return

        last_item.setText(_fmt_num(price))
        chg_item.setText(_fmt_num(change))
        pct_item.setText(f"{_fmt_num(change_pct)}%" if change_pct is not None else "-")
        range_item.setText(_fmt_range(day_low, day_high))

        if change is not None:
            apply_tick(chg_item, change, glyph=False)
            apply_tick(pct_item, change)
        else:
            dim = QColor(FG_DIM)
            chg_item.setForeground(dim)
            pct_item.setForeground(dim)

    # -- selection -> navigation (skip group headers) ---------------------------

    def _on_row_selected(self) -> None:
        model = self.table.selectionModel()
        rows = model.selectedRows() if model else []
        if not rows:
            return
        row = rows[0].row()
        kind, symbol = self._row_kind.get(row, (None, None))
        if kind != ROW_KIND_DATA or not symbol:
            return
        self.set_symbol(symbol)

    # -- edit dialog ---------------------------------------------------------

    def _apply_edit(self, energy=None, metals=None) -> None:
        """Apply a config change (None = keep) behind one undo snapshot —
        shared by the Edit dialog and the right-click quick actions."""
        snap_e = [list(r) for r in self._energy]
        snap_m = [list(r) for r in self._metals]

        def _undo() -> None:
            self._energy = [list(r) for r in snap_e]
            self._metals = [list(r) for r in snap_m]
            self._rebuild_table()
            self.set_status("undo · edit commodities")

        UndoStack.instance().push("edit commodities", _undo)
        if energy is not None:
            self._energy = energy
        if metals is not None:
            self._metals = metals
        self._rebuild_table()

    def _open_edit_dialog(self) -> None:
        columns = [EditorColumn("Label"), EditorColumn("Symbol", kind="symbol")]
        catalog = commodity_entries()
        result = open_list_editor(
            self,
            "Edit Commodities",
            [
                EditorSection(
                    "energy",
                    "Energy",
                    columns,
                    self._energy,
                    description="Live futures quotes in the Energy group — any "
                    "Yahoo Finance symbol works (CL=F, BZ=F…).",
                    catalog=catalog,
                    presets=[
                        ("Oil complex", [["WTI", "CL=F"], ["Brent", "BZ=F"]]),
                    ],
                ),
                EditorSection(
                    "metals",
                    "Metals",
                    columns,
                    self._metals,
                    description="Live futures quotes in the Metals group — any "
                    "Yahoo Finance symbol works (GC=F, SI=F…).",
                    catalog=catalog,
                    presets=[
                        ("Precious", [["Gold", "GC=F"], ["Silver", "SI=F"]]),
                    ],
                ),
            ],
        )
        if result is None:
            return
        energy, metals = result["energy"], result["metals"]
        if energy or metals:
            self._apply_edit(energy or None, metals or None)

    def _row_actions(self, row: int) -> list:
        actions = []
        kind, symbol = self._row_kind.get(row, (None, None))
        if kind == ROW_KIND_DATA and symbol:
            in_energy = any(s == symbol for _l, s in self._energy)
            group = self._energy if in_energy else self._metals
            label = next((l for l, s in group if s == symbol), symbol)

            def _remove() -> None:
                rows = [list(r) for r in group if r[1] != symbol]
                if in_energy:
                    self._apply_edit(energy=rows)
                else:
                    self._apply_edit(metals=rows)

            actions.append((f'Remove "{label}"', _remove))

        def _add(to_energy: bool) -> None:
            entry = open_add_picker(
                self,
                commodity_entries(),
                title="Add to Energy" if to_energy else "Add to Metals",
            )
            if entry is None:
                return
            new_row = [entry.label, entry.code]
            if to_energy:
                self._apply_edit(energy=[list(r) for r in self._energy] + [new_row])
            else:
                self._apply_edit(metals=[list(r) for r in self._metals] + [new_row])

        actions.append(("Add to Energy…", lambda: _add(True)))
        actions.append(("Add to Metals…", lambda: _add(False)))
        actions.append(("Edit panel…", self._open_edit_dialog))
        return actions

    # -- persistence -------------------------------------------------------------

    def settings(self) -> dict:
        return {
            "energy": [list(r) for r in self._energy],
            "metals": [list(r) for r in self._metals],
            "hidden_cols": self.table.hidden_columns(),
        }

    def restore(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            return
        energy = settings.get("energy")
        metals = settings.get("metals")
        changed = False
        if isinstance(energy, list) and energy:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in energy if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._energy = cleaned
                changed = True
        if isinstance(metals, list) and metals:
            cleaned = [[str(r[0]), str(r[1]).upper()] for r in metals if isinstance(r, list) and len(r) == 2]
            if cleaned:
                self._metals = cleaned
                changed = True
        if changed:
            self._rebuild_table()
        self.table.set_hidden_columns(settings.get("hidden_cols", []))
